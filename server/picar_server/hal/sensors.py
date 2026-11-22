"""Real sensors: ultrasonic (GPIO5), PCF8591 light (0x48), 5-way line follower (0x11).

Shared by the real and dry-run backends. All I2C traffic takes the backend-wide lock; the ultrasonic is
bit-banged on a GPIO and needs no lock. Timing sources are injectable so tests stay fast.
"""
from __future__ import annotations

import logging
import statistics
import time
from typing import Callable, Optional, Sequence

from ..config import LINE_RAW_MAX, valid_light_channels
from .base import DIST_FAR, DIST_FAULT, DIST_NONE, DIST_OK, Sample

log = logging.getLogger("picar.sensors")

US_PIN = 5
PCF_ADDR = 0x48
LINE_ADDR = 0x11
ECHO_START_TIMEOUT_S = 0.030     # no rising edge within 30 ms: no answer
ECHO_FAR_S = 0.012               # echo ending after 12 ms (and before 40 ms): nothing within ~2 m
ECHO_STUCK_S = 0.040             # echo still high at 40 ms: stuck line, no answer (never "far")
MAX_GAP_S = 0.001                # a busy-wait iteration gap above this (GIL / scheduler) discards the ping
FAR = 999.0
FAULT_AFTER = 3                  # consecutive "none" samples
DEFAULT_NEAR_HINT_CM = 60.0
LINE_BACKOFF_S = (2.0, 10.0)     # re-probe delay: starts at 2 s, doubles up to 10 s
LINE_LOCK_TIMEOUT_S = 0.05        # a line probe waits this long for the motor lock, else it skips the sample
LINE_MOVING_MIN_GOOD = 10      # consecutive good frames (since the last failure) before the line is read while moving
LINE_MOVING_SLOW_S = 0.015      # a line read taking longer than this while moving suppresses moving reads
LINE_ABSENT_AFTER = 3           # consecutive invalid samples (two bad frames each) that count as "absent"
DEFAULT_LIGHT_CHANNELS = (0, 1, 2)


class SystemClock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class Ultrasonic:
    def __init__(self, gpio, clock, pin: int = US_PIN):
        self.gpio, self.clock, self.pin = gpio, clock, pin

    def ping(self) -> Optional[float]:
        """cm, FAR for an echo ending between 12 and 40 ms, None for no valid ping (no echo, stuck high, or a
        busy-wait gap above 1 ms, which makes the timing untrustworthy)."""
        g, c, p = self.gpio, self.clock, self.pin
        g.setup(p, g.OUT, initial=g.LOW)
        c.sleep(0.002)
        g.output(p, 1)
        c.sleep(0.00001)
        g.output(p, 0)
        g.setup(p, g.IN)
        t0 = c.monotonic()
        # A late-detected rising edge only shortens the echo (reads closer: the safe side), so gaps while
        # waiting for it are tolerated. A late-detected falling edge lengthens it (reads farther: unsafe),
        # so any gap while the echo is high discards the ping, and the end time is taken inside the loop.
        while g.input(p) == 0:
            if c.monotonic() - t0 > ECHO_START_TIMEOUT_S:
                return None
        start = prev = c.monotonic()
        gap = False
        while True:
            high = g.input(p) == 1
            now = c.monotonic()
            gap = gap or now - prev > MAX_GAP_S
            prev = now
            if not high:
                break
            if now - start > ECHO_STUCK_S:
                return None
        dur = now - start
        if gap:
            return None
        if dur > ECHO_FAR_S:
            return FAR
        return dur * 34300 / 2

    def measure(self, n: int) -> Optional[float]:
        """Median of n pings (needs 2 valid when n == 3); None = no valid ping."""
        vals = [v for v in (self.ping() for _ in range(n)) if v is not None]
        if len(vals) < (2 if n >= 3 else 1):
            return None
        return statistics.median(vals)


class SensorHub:
    """Produces one `Sample` per `read()`. `motion()` is the backend's applied (left, right) duty."""

    def __init__(self, bus, gpio, lock, clock, motion: Callable[[], tuple[int, int]],
                 line_enabled: Callable[[], bool] = lambda: False, us_pin: int = US_PIN, motor_lock=None,
                 light_channels: Callable[[], Sequence[int]] = lambda: DEFAULT_LIGHT_CHANNELS):
        self.motor_lock = motor_lock               # outer lock: held across "motors stopped?" and the line probe
        self.bus, self.lock, self.clock, self.motion, self.line_enabled = bus, lock, clock, motion, line_enabled
        self.us = Ultrasonic(gpio, clock, us_pin)
        self.near_hint_cm = DEFAULT_NEAR_HINT_CM
        self._nones = 0
        self._last_cm: Optional[float] = None     # last "ok" reading; None after far / none = see _last_far
        self._last_far = False
        self.light_ok = False
        self.line_ok = False
        self.light_channels = light_channels
        self._line_good = 0                       # consecutive good frames since the last failure
        self._line_invalid_run = 0                # consecutive samples whose frame was invalid twice
        self._line_suppressed = False             # moving reads off until the car has been stopped
        self.line_skipped = False                 # the last None was one skipped frame (telemetry may bridge it)
        self._line_next_probe = 0.0               # monotonic time of the next re-probe while absent
        self._line_backoff = LINE_BACKOFF_S[0]
        self._line_known: Optional[bool] = None   # last logged state, for state-change logging only

    def set_near_hint(self, cm: float) -> None:
        """Controller hint: below this distance the sensor takes 3 pings (default 60 cm)."""
        self.near_hint_cm = float(cm)

    def capabilities(self) -> dict[str, bool]:
        return {"ultrasonic": True, "light": self.light_ok, "line": self.line_enabled() and self.line_ok}

    def _moving(self) -> bool:
        l, r = self.motion()
        return l != 0 or r != 0

    # -- ultrasonic -------------------------------------------------------------------------------------
    def _read_distance(self) -> tuple[Optional[float], str]:
        l, r = self.motion()
        forward = l > 0 or r > 0
        near = not self._last_far and (self._last_cm is None or self._last_cm < self.near_hint_cm)
        v = self.us.measure(3 if (forward or near) else 1)
        if v is None:
            self._nones += 1
            self._last_cm, self._last_far = None, False
            return None, DIST_FAULT if self._nones >= FAULT_AFTER else DIST_NONE
        self._nones = 0
        if v >= FAR:
            self._last_cm, self._last_far = None, True
            return None, DIST_FAR
        self._last_cm, self._last_far = v, False
        return v, DIST_OK

    # -- PCF8591 ----------------------------------------------------------------------------------------
    def _channels(self) -> Sequence[int]:
        """PCF8591 inputs of [left, middle, right] from calibration (the default if it is malformed)."""
        try:
            ch = list(self.light_channels())
        except Exception:                                 # noqa: BLE001 - a bad getter must not stop sampling
            return DEFAULT_LIGHT_CHANNELS
        return ch if valid_light_channels(ch) else DEFAULT_LIGHT_CHANNELS

    def _read_light(self) -> Optional[list[int]]:
        try:
            out = []
            with self.lock:
                for ch in self._channels():
                    self.bus.write_byte(PCF_ADDR, 0x40 | ch)
                    self.bus.read_byte(PCF_ADDR)          # first read returns the previous conversion
                    out.append(self.bus.read_byte(PCF_ADDR))
            ok = True
        except OSError:
            out, ok = None, False
        if ok != self.light_ok:
            log.info("light sensor %s", "present" if ok else "not responding")
            self.light_ok = ok
        return out

    # -- line follower ----------------------------------------------------------------------------------
    def _read_frame(self) -> tuple[Optional[list[int]], float]:
        """One block read under the I2C lock only. Returns (five values, or None for an invalid frame, seconds the
        bus call took). The module answers all-0xFF now and then (~1 % of frames); any value above LINE_RAW_MAX
        marks the frame invalid. OSError / IndexError propagate."""
        with self.lock:
            t0 = self.clock.monotonic()
            raw = self.bus.read_i2c_block_data(LINE_ADDR, 0, 10)
            took = self.clock.monotonic() - t0
        vals = [(raw[2 * i] << 8) | raw[2 * i + 1] for i in range(5)]
        return (None if max(vals) > LINE_RAW_MAX else vals), took

    def _read_checked(self) -> tuple[Optional[list[int]], bool]:
        """A frame, retried once at once when invalid, but never after a slow bus call (a stalling module must
        hold the bus lock only once). Returns (values or None, True if a bus call was slow)."""
        slow = False
        for _ in range(2):
            vals, took = self._read_frame()
            if took > LINE_MOVING_SLOW_S:
                slow = True
            if vals is not None or slow:
                return vals, slow
        return None, slow

    def _read_line(self) -> Optional[list[int]]:
        """Stopped: probe under the motor lock (with backoff while absent). Moving: only when healthy (see
        `_read_line_moving`). Needs `line_enabled` in calibration."""
        self.line_skipped = False
        if not self.line_enabled():
            return None
        if self._moving():
            return self._read_line_moving()
        self._line_suppressed = False                      # the car has been stopped
        now = self.clock.monotonic()
        if not self.line_ok and now < self._line_next_probe:
            return None
        held = self.motor_lock is not None and self.motor_lock.acquire(timeout=LINE_LOCK_TIMEOUT_S)
        if self.motor_lock is not None and not held:
            return None
        try:
            if not self._moving():
                return self._probe_line(now)
        finally:
            if held:
                self.motor_lock.release()
        return self._read_line_moving()                    # the car started moving while we took the lock

    def _read_line_moving(self) -> Optional[list[int]]:
        """Line read while the car moves: only the I2C bus lock is taken (never the motor lock), and only when
        the module has answered well (>= LINE_MOVING_MIN_GOOD good reads since its last failure). One error,
        two invalid frames in a row, or a bus call over LINE_MOVING_SLOW_S switches moving reads off until the
        car has been stopped (docs/decisions.md D4)."""
        if self._line_suppressed or not self.line_ok or self._line_good < LINE_MOVING_MIN_GOOD:
            return None
        try:
            vals, slow = self._read_checked()
        except (OSError, IndexError) as e:
            self._suppress_line(f"read failed ({e})")
            return None
        if vals is None:
            self._suppress_line(f"a read took more than {LINE_MOVING_SLOW_S * 1000:.0f} ms" if slow
                                else "two invalid frames in a row")
            return None
        if slow:
            self._suppress_line(f"a read took more than {LINE_MOVING_SLOW_S * 1000:.0f} ms")
        else:
            self._line_good += 1
        return vals

    def _suppress_line(self, why: str) -> None:
        self._line_suppressed = True
        self._line_good = 0
        log.warning("line follower: not reading while moving until the car stops (%s)", why)

    def _probe_line(self, now: float) -> Optional[list[int]]:
        try:
            vals, slow = self._read_checked()
        except (OSError, IndexError):
            return self._line_failed(now)
        if vals is None:                                   # invalid (twice, or once and slow): skip this sample
            self._line_good = 0
            self._line_invalid_run += 1
            if self._line_invalid_run >= LINE_ABSENT_AFTER:
                return self._line_failed(now)
            self.line_skipped = True
            return None
        self._line_invalid_run = 0
        if slow:
            self._line_good = 0                            # a slow module does not qualify for moving reads
        else:
            self._line_good += 1
        self._line_backoff = LINE_BACKOFF_S[0]
        self.line_ok = True
        self._log_line_state(now)
        return vals

    def _line_failed(self, now: float) -> None:
        self.line_ok = False
        self._line_good = 0
        self._line_invalid_run = 0
        self._line_next_probe = now + self._line_backoff
        self._line_backoff = min(self._line_backoff * 2, LINE_BACKOFF_S[1])
        self._log_line_state(now)
        return None

    def _log_line_state(self, now: float) -> None:
        if self._line_known != self.line_ok:
            log.info("line follower %s", "present" if self.line_ok else "absent")
            self._line_known = self.line_ok

    def probe(self) -> None:
        """Initial capability probe so capabilities() is meaningful before the first sample."""
        self._read_light()
        self._read_line()

    def read(self) -> Sample:
        cm, state = self._read_distance()
        return Sample(t=self.clock.monotonic(), dist_cm=cm, dist_state=state,
                      line_raw=self._read_line(), light_raw=self._read_light(),
                      line_skipped=self.line_skipped)
