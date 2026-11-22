"""Real-car backend: PCA9685 motors + servo, TB6612 direction GPIOs, ultrasonic/light/line sensors.

Lock order: the motor lock (outer) is held across a whole motor operation; the I2C bus lock (inner, inside
PCA9685 / sensor reads) is taken per transaction. Never take the motor lock while holding the bus lock.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Callable, Optional

from .base import DUTY_MAX, STEER_MAX_DEG, HardwareFault, OnSample
from .pca9685 import MOTOR_LEFT_CH, MOTOR_RIGHT_CH, SERVO_CH, PCA9685, servo_ticks
from .sensors import DEFAULT_LIGHT_CHANNELS, SensorHub, SystemClock, Ultrasonic

log = logging.getLogger("picar.hal")

DIR_RIGHT_GPIO, DIR_LEFT_GPIO = 27, 17     # direction pins; HIGH = forward on both wheels
SAMPLE_PERIOD_S = 0.04                     # ~25 Hz
STOP_LOCK_TIMEOUT_S = 0.2                  # stop_motors gives up on the motor lock after this
SWITCH_INTERVAL_S = 0.0005                 # GIL switch interval while the sensor thread times echoes

__all__ = ["RealBackend", "HardwareFault"]

# (name, PWM channel, direction pin)
_LEFT = ("left", MOTOR_LEFT_CH, DIR_LEFT_GPIO)
_RIGHT = ("right", MOTOR_RIGHT_CH, DIR_RIGHT_GPIO)


class _HardwareBase:
    """Shared by RealBackend and DryRunBackend: bus/GPIO setup, locks, inhibit, sensor thread.

    Subclasses provide `_write_motors(left, right)` (called under the motor lock) and `_zero_channels()`
    (returns True when both channels were zeroed).
    """

    name = "base"

    def __init__(self, calibration: Optional[dict], bus=None, gpio=None, clock=None, us_timing: str = "poll",
                 open_edges=None):
        """us_timing: "poll", "edges" or "auto" (decision D9); the server passes its --us-timing, tests default
        to polling on their fake GPIO. open_edges replaces the kernel line for tests."""
        self.cal = calibration if calibration is not None else {}
        self.clock = clock or SystemClock()
        self.lock = threading.Lock()                   # I2C bus lock (inner)
        self._motor_lock = threading.Lock()            # whole motor operations (outer)
        self._motion = (0, 0)                          # last applied signed duty (left, right)
        self._inhibit = False                          # forward inhibit latch
        self._stop_latched = False                     # full stop latch: every write becomes zero
        self._on_sample: Optional[OnSample] = None
        self._deadline_probe: Optional[Callable[[], tuple[bool, bool]]] = None
        self._latch_cb: Optional[Callable[[str], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._closed = False
        self._old_switch = None
        if bus is None:
            import smbus2                              # not present in the test container
            bus = smbus2.SMBus(1)
        if gpio is None:
            import RPi.GPIO as gpio                    # rpi-lgpio on the Pi
            gpio.setwarnings(False)
            gpio.setmode(gpio.BCM)
        self.bus, self.gpio = bus, gpio
        self.pca = PCA9685(bus, self.lock, sleep=self.clock.sleep)
        def make_poll() -> Ultrasonic:
            return Ultrasonic(gpio, self.clock)

        if us_timing == "poll":
            us = make_poll()
        else:
            from .ultrasonic_edges import open_edge_ultrasonic, select_ultrasonic   # Linux-only path
            us = select_ultrasonic(us_timing, self.clock, make_poll, stop=self._stop_evt,
                                   open_edges=open_edges or open_edge_ultrasonic)
        self.hub = SensorHub(bus, gpio, self.lock, self.clock, self.motion,
                             lambda: bool(self.cal.get("line_enabled", False)), motor_lock=self._motor_lock,
                             light_channels=lambda: self.cal.get("light_channels", DEFAULT_LIGHT_CHANNELS),
                             ultrasonic=us, fallback=make_poll if us.timing == "edges" else None)

    @property
    def ultrasonic_timing(self) -> str:
        return self.hub.us.timing

    # -- hooks ------------------------------------------------------------------------------------------
    def _write_motors(self, left: int, right: int) -> None:
        raise NotImplementedError

    def _zero_channels(self) -> bool:
        raise NotImplementedError

    # -- motors (Backend interface) ---------------------------------------------------------------------
    def motion(self) -> tuple[int, int]:
        return self._motion

    def set_motors(self, left: int, right: int) -> bool:
        """False when forward duty was replaced by zero because the inhibit is latched."""
        left = max(-DUTY_MAX, min(DUTY_MAX, int(left)))
        right = max(-DUTY_MAX, min(DUTY_MAX, int(right)))
        if not self._motor_lock.acquire(timeout=STOP_LOCK_TIMEOUT_S):   # e.g. a stalled sensor probe
            raise HardwareFault("motor lock timeout")
        try:
            refused = False
            if self._stop_latched:
                left, right, refused = 0, 0, True
            elif self._inhibit and (left > 0 or right > 0):
                left, right, refused = min(left, 0), min(right, 0), True
            try:
                self._write_motors(left, right)
            except Exception as e:                     # noqa: BLE001 - any failed write is a hardware fault
                if self._zero_channels():
                    self._motion = (0, 0)
                elif (left, right) != (0, 0):
                    self._motion = (left, right)       # unconfirmed zero: stay non-zero so the stop is retried
                if isinstance(e, HardwareFault):
                    raise
                raise HardwareFault(f"motor write failed: {e}") from e
            self._motion = (left, right)
        finally:
            self._motor_lock.release()
        return not refused

    def stop_motors(self) -> bool:
        """Never raises. False when zeroing could not be confirmed (lock timeout or I2C error)."""
        try:
            if not self._motor_lock.acquire(timeout=STOP_LOCK_TIMEOUT_S):
                log.error("stop_motors: motor lock timeout")
                return False
            try:
                ok = self._zero_channels()
                if ok:
                    self._motion = (0, 0)
                return ok
            finally:
                self._motor_lock.release()
        except Exception:                              # noqa: BLE001
            log.exception("stop_motors failed")
            return False

    def inhibit_forward(self) -> None:
        """Latch the inhibit, then stop, so no forward write can slip in between."""
        self._inhibit = True
        if not self.stop_motors() and self._latch_cb:
            self._latch_cb("stop_failed")

    def clear_inhibit(self) -> None:
        self._inhibit = False

    def latch_stop(self) -> bool:
        """Latch the full stop and make sure the motors are off; True when they are confirmed off."""
        self._stop_latched = True                      # seen by any set_motors that has not taken the lock yet
        if not self._motor_lock.acquire(timeout=STOP_LOCK_TIMEOUT_S):
            return False
        try:
            moving = self._motion != (0, 0)            # a set_motors in flight has finished: the lock is ours
        finally:
            self._motor_lock.release()
        return self.stop_motors() if moving else True

    def release_stop(self) -> None:
        self._stop_latched = False

    # -- sensing ----------------------------------------------------------------------------------------
    def capabilities(self) -> dict[str, bool]:
        return self.hub.capabilities()

    def set_near_hint(self, cm: float) -> None:
        self.hub.set_near_hint(cm)

    def pose(self):
        return None

    def set_deadline_probe(self, fn: Optional[Callable[[], tuple[bool, bool]]]) -> None:
        """fn() -> (loop_ok, controller_ok); polled every sensor iteration; False while moving stops the motors."""
        self._deadline_probe = fn

    def set_latch_callback(self, cb: Optional[Callable[[str], None]]) -> None:
        """cb(reason) is called from the sensor thread after a deadline stop; reason "stop_failed" when the
        stop could not be confirmed."""
        self._latch_cb = cb

    def start(self, on_sample: OnSample) -> None:
        if self._thread is not None:
            return
        self._on_sample = on_sample
        self.hub.probe()
        self._thread = threading.Thread(target=self._run, name="picar-sensors", daemon=True)
        self._thread.start()

    def _check_deadlines(self) -> None:
        probe = self._deadline_probe
        if probe is None or self._motion == (0, 0):
            return
        loop_ok, controller_ok = probe()
        reason = None if loop_ok and controller_ok else ("loop_stalled" if not loop_ok else "controller_silent")
        if reason:
            log.error("deadline missed (%s) while moving: stopping", reason)
            stopped = self.stop_motors()
            if self._latch_cb:
                self._latch_cb(reason if stopped else "stop_failed")

    def _run(self) -> None:
        self._old_switch = sys.getswitchinterval()
        sys.setswitchinterval(SWITCH_INTERVAL_S)
        try:
            while not self._stop_evt.is_set():
                t0 = self.clock.monotonic()
                try:
                    self._check_deadlines()
                    sample = self.hub.read()
                    if self._on_sample is not None:
                        self._on_sample(sample)
                except Exception:                      # noqa: BLE001 - never die with the wheels turning
                    log.exception("sensor loop error")
                    self.stop_motors()
                self._stop_evt.wait(max(0.0, SAMPLE_PERIOD_S - (self.clock.monotonic() - t0)))
        finally:
            self.stop_motors()                         # also covers BaseException
            if self._old_switch:
                sys.setswitchinterval(self._old_switch)

    def _join_thread(self) -> None:
        self._stop_evt.set()                           # also ends an edge-timed ping's wait (us.stop)
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(1.0)
            if t.is_alive():
                # never close the line under a running ping (the fd could be reused); process exit releases it
                log.error("sensor thread did not stop within 1 s; leaving the ultrasonic line to process exit")
                self._thread = None
                return
        self._thread = None
        try:
            self.hub.us.close()                        # input, never driven (in edge mode RPi.GPIO never owns it)
        except Exception:                              # noqa: BLE001
            pass

    def _release_ultrasonic(self) -> None:
        try:
            self.hub.us.close()
        except Exception:                              # noqa: BLE001
            pass

    # -- steering helpers -------------------------------------------------------------------------------
    def _trim(self) -> float:
        return float(self.cal.get("steer_trim_deg", 0.0) or 0.0)

    def _servo_angle(self, angle_deg: float) -> float:
        a = max(-STEER_MAX_DEG, min(STEER_MAX_DEG, float(angle_deg)))
        return max(45.0, min(135.0, 90.0 + self._trim() + a))


class RealBackend(_HardwareBase):
    name = "real"

    def __init__(self, calibration: Optional[dict] = None, *, bus=None, gpio=None, clock=None, us_timing: str = "poll",
                 open_edges=None):
        super().__init__(calibration, bus, gpio, clock, us_timing=us_timing, open_edges=open_edges)
        self._angle = 0.0
        self._dir = {"left": False, "right": False}    # direction pin level (True = HIGH); pins start LOW
        try:
            # Motors first: zero both channels before anything else can leave them running.
            self._zero_pwm()
            for pin in (DIR_RIGHT_GPIO, DIR_LEFT_GPIO):
                self.gpio.setup(pin, self.gpio.OUT, initial=self.gpio.LOW)
            self.pca.setup()                            # 60 Hz; also switches every channel off
            self._zero_pwm()
            self.set_steering(0.0)
        except BaseException:
            self._release_ultrasonic()                  # the edge line is an exclusive request: never leak it
            raise

    def _zero_pwm(self) -> None:
        """Both channels are attempted (each with retries) before a failure is reported."""
        err = None
        for ch in (MOTOR_RIGHT_CH, MOTOR_LEFT_CH):
            try:
                self.pca.set_pwm_retry(ch, 0)
            except Exception as e:                     # noqa: BLE001
                err = err or e
        if err:
            raise err

    def _write_motors(self, left: int, right: int) -> None:
        g = self.gpio
        for (name, ch, pin), duty in ((_LEFT, left), (_RIGHT, right)):
            want_high = duty >= 0
            if duty != 0 and want_high != self._dir[name]:       # direction change: PWM 0 -> pin -> PWM
                self.pca.set_pwm_retry(ch, 0)
                g.output(pin, g.HIGH if want_high else g.LOW)
                self._dir[name] = want_high
            self.pca.set_pwm_retry(ch, abs(duty))

    def _zero_channels(self) -> bool:
        """Both channels attempted independently, each twice."""
        ok = True
        for ch in (MOTOR_RIGHT_CH, MOTOR_LEFT_CH):
            done = False
            for _ in range(2):
                try:
                    self.pca.set_pwm(ch, 0, timeout=STOP_LOCK_TIMEOUT_S)
                    done = True
                    break
                except Exception as e:                 # noqa: BLE001
                    log.error("zeroing ch%d failed: %s", ch, e)
            ok = ok and done
        return ok

    def set_steering(self, angle_deg: float) -> None:
        self._angle = float(angle_deg)
        self.pca.set_pwm_retry(SERVO_CH, servo_ticks(self._servo_angle(angle_deg)))

    def set_trim(self, trim_deg: float) -> None:
        """Live calibration: update the trim and re-apply the current steering angle."""
        self.cal["steer_trim_deg"] = float(trim_deg)
        self.set_steering(self._angle)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop_motors()
        self._join_thread()
        self.stop_motors()
        try:
            self.bus.close()
        except Exception:                              # noqa: BLE001
            pass
