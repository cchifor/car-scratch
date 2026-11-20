"""One controller class for the shared car and for every private simulator session (docs/contracts.md 1).

Threading: commands, `tick` and the timed-move tasks run on the asyncio loop. `_on_sample` (the obstacle
guard) runs in the backend's sensor thread on real hardware, so it touches only the backend's own
thread-safe motor path (`inhibit_forward`, `motion`) and a few fields guarded by `self._lock`; everything
that needs the loop (events, `done`) is handed over through `_defer` + `_kick`.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import math
import threading
import time
from typing import Any, Callable, Optional

from . import safety
from .hal.base import DIST_FAR, DIST_FAULT, DIST_NONE, DIST_OK, HardwareFault, Sample

log = logging.getLogger(__name__)

EventCb = Callable[[str, dict], None]
DoneCb = Callable[[Any, int, str], None]


class CmdError(Exception):
    def __init__(self, code: str, msg: str = ""):
        super().__init__(msg or code)
        self.code, self.msg = code, msg or code


class _Move:
    __slots__ = ("id", "owner", "end_t", "task", "gen")

    def __init__(self, cmd_id: int, owner: Any, end_t: float, gen: int):
        self.id, self.owner, self.end_t, self.task, self.gen = cmd_id, owner, end_t, None, gen


class Controller:
    def __init__(self, backend, calibration: dict, *, shared: bool = True,
                 clock: Callable[[], float] = time.monotonic, on_event: Optional[EventCb] = None,
                 on_done: Optional[DoneCb] = None, save_calibration: Optional[Callable[[dict], None]] = None,
                 start_on_hold: bool = False, error: Optional[str] = None):
        self.backend = backend                # None = car unavailable
        self.cal = calibration
        self.shared = shared
        self.error = error
        self._clock = clock
        self.on_event, self.on_done, self._save_cal = on_event, on_done, save_calibration
        self.cfg = dict(safety.DEFAULT_CONFIG)
        self.estopped = False
        self.hold = start_on_hold
        self.hold_reason: Optional[str] = "boot" if start_on_hold else None
        self.hw_fault: Optional[str] = None
        self.blocked = False
        self.attached = not shared            # the hub sets this when a controller session exists
        self._lock = threading.Lock()
        self._deferred: collections.deque = collections.deque()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread = 0
        self._tasks: list[asyncio.Task] = []
        # motion intent (loop thread)
        self._dir: Optional[str] = None
        self._eff = 0
        self._speed_setting = 0               # last non-zero effective speed, used for the stationary margin
        self._steer = 0.0
        self._gen = 0                         # motion generation, written on the loop thread only
        self._move: Optional[_Move] = None
        self._drive_since: Optional[float] = None
        self._rev_used = 0.0                  # accounted at every motion transition
        self._rev_t: Optional[float] = None   # when the current backward run started being accounted
        self._rev_task: Optional[asyncio.Task] = None
        self._stop_since: Optional[float] = None
        # deadlines (read by the sensor thread on real hardware)
        now = clock()
        self.last_loop_tick = now
        self.last_controller_frame = now
        self._tick_t = now
        # sensor state (guarded by self._lock)
        self._sample: Optional[Sample] = None
        self._sample_at = now
        self._last_good_t: Optional[float] = None   # last ok/far sample; None until the first one
        self._clear_since: Optional[float] = None
        self._last_ok: Optional[tuple[float, float]] = None   # (cm, time) of the latest ok sample, cleared by a far one
        self._us_fault = False
        self._line_ok: Optional[bool] = None
        self._pose = [0.0, 0.0, 90.0]

    # ---- lifecycle --------------------------------------------------------------------------
    def start(self, background: bool = True) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:   # unit tests drive the controller without a loop
            self._loop = None
        self._loop_thread = threading.get_ident()
        now = self._clock()
        self.last_loop_tick = self.last_controller_frame = self._tick_t = self._sample_at = now
        b = self.backend
        if b is None:
            return
        if hasattr(b, "set_deadline_probe"):
            b.set_deadline_probe(self._deadline_probe)
        if hasattr(b, "set_latch_callback"):
            b.set_latch_callback(self._deadline_latch)
        self._sync_latch()
        b.start(self._on_sample)
        self._set_hint()
        if background:
            self._tasks.append(asyncio.get_running_loop().create_task(self._tick_loop()))

    def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        if self._rev_task:
            self._rev_task.cancel()
        if self._move and self._move.task:
            self._move.task.cancel()
        self._move = None
        if self.backend is not None:
            try:
                self.backend.close()
            except Exception:
                log.exception("backend close failed")

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")

    def touch(self) -> None:
        """An accepted JSON frame from this controller's client."""
        self.last_controller_frame = self._clock()

    # ---- deferred hand-off to the loop --------------------------------------------------------
    def _defer(self, result: Optional[str], events: list, gen: Optional[int] = None) -> None:
        """Queue a move result and events for the loop. With `gen` (from the sensor thread) the result
        finishes only a move that was already running when the stop was issued (move.gen <= gen)."""
        self._deferred.append((result, events, gen))

    def _kick(self) -> None:
        if self._loop is None or threading.get_ident() == self._loop_thread:
            self._drain()
            return
        try:
            self._loop.call_soon_threadsafe(self._drain)
        except RuntimeError:
            pass

    def _drain(self) -> None:
        while True:
            try:
                result, events, gen = self._deferred.popleft()
            except IndexError:
                return
            if result is not None and (gen is None or (self._move is not None and self._move.gen <= gen)):
                self._finish_move(result)
            for kind, fields in events:
                self._emit(kind, **fields)

    def _emit(self, kind: str, **fields) -> None:
        if self.on_event:
            try:
                self.on_event(kind, fields)
            except Exception:
                log.exception("event callback failed")

    def _finish_move(self, result: str) -> None:
        mv, self._move = self._move, None
        if mv is None:
            return
        if mv.task is not None and mv.task is not asyncio.current_task():
            mv.task.cancel()
        if self.on_done:
            try:
                self.on_done(mv.owner, mv.id, result)
            except Exception:
                log.exception("done callback failed")

    # ---- motor path -----------------------------------------------------------------------
    def _stop_motors(self, gen: Optional[int] = None) -> bool:
        try:
            ok = self.backend.stop_motors()
        except Exception as e:  # stop_motors must not raise; treat it as unconfirmed
            log.error("stop_motors raised: %s", e)
            ok = False
        if ok is False:
            self._latch_hw_fault("could not confirm the motors stopped", gen)
            return False
        return True

    def _latch_hw_fault(self, msg: str, gen: Optional[int] = None) -> None:
        with self._lock:
            if self.hw_fault:
                return
            self.hw_fault = msg
        self._sync_latch()
        self._defer("hw_fault", [("hw_fault", {"msg": msg})], gen)

    def _sync_latch(self) -> None:
        """Mirror the stop latches (estop / hold / hw_fault) into the backend, which refuses every motor write
        while it is latched. Idempotent; either thread."""
        if self.backend is None:
            return
        try:
            if self.estopped or self.hold or self.hw_fault:
                self.backend.latch_stop()
            else:
                self.backend.release_stop()
        except Exception:
            log.exception("stop latch failed")

    def _enforce_latches(self, gen: Optional[int] = None) -> None:
        """While a latch is set the motors must be off: re-issue the stop if they are not (or are unconfirmed)."""
        if self.backend is None:
            return
        self._sync_latch()
        forward_blocked = self.blocked and self._motion_dir() == "forward"
        if (self.estopped or self.hold or self.hw_fault or forward_blocked) and any(self.backend.motion()):
            self._stop_motors(gen)

    def _halt(self, result: Optional[str] = None, events: Optional[list] = None) -> None:
        """Stop the motors and drop the motion intent (loop thread). Bumps the motion generation."""
        self._gen += 1
        if self._rev_task:
            self._rev_task.cancel()
            self._rev_task = None
        if self.backend is not None:
            self._stop_motors()
            self._account_reverse()
        self._dir, self._eff, self._drive_since = None, 0, None
        self._defer(result, events or [])

    def _write(self, direction: str, eff: int) -> str:
        """The one motor-write path. Returns "ok" or "inhibited"; raises CmdError on a hardware fault."""
        duty = safety.speed_to_duty(eff, self.cal)
        sign = 1 if direction == "forward" else -1
        try:
            res = self.backend.set_motors(sign * duty, sign * duty)
            return "inhibited" if res is False else "ok"
        except Exception as e:   # the HAL already retried (HardwareFault): a failed write is terminal
            err = e
        self._latch_hw_fault(f"motor write failed: {err}")
        try:
            self.backend.stop_motors()
        except Exception:
            pass
        self._halt("hw_fault")
        raise CmdError("hw_fault", "motor write failed")

    def _account_reverse(self) -> None:
        """Charge the elapsed backward time to the budget; call before and after every motion change."""
        if self.backend is None:
            return
        now = self._clock()
        if self._rev_t is not None:
            self._rev_used += now - self._rev_t
        self._rev_t = now if self._motion_dir() == "backward" else None

    def _arm_reverse_deadline(self) -> None:
        if self._rev_task:
            self._rev_task.cancel()
            self._rev_task = None
        if self._loop is None or self._motion_dir() != "backward":
            return
        remaining = max(0.0, safety.REVERSE_MS_MAX / 1000.0 - self._rev_used)
        self._rev_task = self._loop.create_task(self._reverse_deadline(self._gen, remaining))

    async def _reverse_deadline(self, gen: int, secs: float) -> None:
        await asyncio.sleep(secs)
        if self._gen == gen and self._motion_dir() == "backward":
            self._halt("reverse_timeout", [("reverse_timeout", {})])
            self._drain()

    def _moving(self) -> bool:
        return self.backend is not None and any(self.backend.motion())

    def _motion_dir(self) -> Optional[str]:
        if self.backend is None:
            return None
        l, r = self.backend.motion()
        if l > 0 or r > 0:
            return "forward"
        if l < 0 or r < 0:
            return "backward"
        return None

    def _margin(self, speed: float) -> float:
        return safety.margin_cm(self.cfg["stop_cm"], speed, self.cal)

    def _set_hint(self) -> None:
        fn = getattr(self.backend, "set_near_hint", None)
        if fn:
            try:
                fn(2.0 * self._margin(self._eff or self._speed_setting or safety.SPEED_MAX))
            except Exception:
                pass

    def _sensor_state(self, now: float) -> str:
        """"ok" or the sensor_fault reason when forward motion must not be allowed."""
        with self._lock:
            if self._last_good_t is None:
                return "no valid sample yet"
            s = self._sample
            if s is not None and s.dist_state == DIST_FAULT:
                return "ultrasonic fault"
            if now - self._last_good_t > safety.SENSOR_STALE_S:
                return "no fresh distance reading"
        return "ok"

    def _check_forward(self, eff: int) -> None:
        """Command-time check for forward motion at the proposed effective speed."""
        why = self._sensor_state(self._clock())
        if why != "ok":
            raise CmdError("sensor_fault", why)
        if self.blocked:
            raise CmdError("obstacle_blocked", "forward motion is blocked")
        if self.cfg["obstacle_stop"]:
            with self._lock:
                last = self._last_ok
            now = self._clock()
            if last is not None and now - last[1] <= safety.SENSOR_STALE_S and last[0] < self._margin(eff):
                raise CmdError("obstacle_blocked", "obstacle closer than the stop distance")

    def _check_latches(self) -> None:
        if self.estopped:
            raise CmdError("estop", "emergency stop is latched")
        if self.hw_fault:
            raise CmdError("hw_fault", self.hw_fault)
        if self.hold:
            raise CmdError("hold", "motion is on hold; send resume")

    def _apply(self, direction: str, eff: int, *, new_command: bool) -> None:
        """Validate and write a motion at the proposed effective speed. Raises CmdError."""
        if self.backend is None:
            raise CmdError("hw_unavailable", self.error or "car unavailable")
        self._check_latches()
        self._account_reverse()
        if direction == "backward" and self._rev_used >= safety.REVERSE_MS_MAX / 1000.0:
            raise CmdError("reverse_timeout", "reverse budget used up")
        if direction == "forward":
            self._check_forward(eff)
        if new_command:
            self._gen += 1
        prev = self._dir, self._eff
        self._dir, self._eff = direction, eff   # before the write: the sensor thread sizes its margin from _eff
        if self._write(direction, eff) == "inhibited":
            self._dir, self._eff = prev
            self._check_latches()   # a latch set since the check above refused the write
            raise CmdError("obstacle_blocked", "forward motion is inhibited")
        self._account_reverse()
        if direction == "forward":
            try:
                self._check_forward(eff)   # a sample that arrived between validation and the motor start
            except CmdError:
                self._halt("stopped" if self._move else None)
                raise
        else:
            self._arm_reverse_deadline()
        now = self._clock()
        if new_command or self._drive_since is None:
            self._drive_since = now
        self._speed_setting = eff
        self._stop_since = None
        self._set_hint()

    # ---- commands (loop thread) -----------------------------------------------------------------
    def command(self, op: str, f: dict, owner: Any = None, cmd_id: int = 0) -> dict:
        """Execute one validated command. Returns extra `ack` fields; raises CmdError."""
        try:
            return getattr(self, "_cmd_" + op)(f, owner, cmd_id)
        finally:
            self._drain()

    def _need_car(self) -> None:
        if self.backend is None:
            raise CmdError("hw_unavailable", self.error or "car unavailable")

    def _cmd_drive(self, f, owner, cmd_id):
        return self._motion(f, owner, cmd_id, timed=False)

    def _cmd_move(self, f, owner, cmd_id):
        return self._motion(f, owner, cmd_id, timed=True)

    def _motion(self, f, owner, cmd_id, timed: bool) -> dict:
        self._need_car()
        direction = f["dir"]
        eff = safety.effective_speed(direction, f["speed"], self.cfg["speed_limit"])
        if eff == 0:   # speed 0 behaves like stop
            self._finish_move("stopped") if self._move else None
            self._halt()
        else:
            same = (not timed and self._dir == direction and self._eff == eff and self._moving())
            if same:
                if self._move:
                    self._finish_move("superseded")
                return {"speed": eff}
            self._apply(direction, eff, new_command=True)
            if self._move:
                self._finish_move("superseded")
        if timed:
            ms = f["ms"]
            mv = _Move(cmd_id, owner, self._clock() + ms / 1000.0, self._gen)
            self._move = mv
            mv.task = asyncio.get_running_loop().create_task(self._run_move(mv, ms / 1000.0))
            return {"speed": eff, "ms": ms}
        return {"speed": eff}

    async def _run_move(self, mv: _Move, secs: float) -> None:
        await asyncio.sleep(secs)
        if self._move is mv and self._gen == mv.gen:
            self._halt("completed")
            self._drain()

    def _cmd_stop(self, f, owner, cmd_id):
        self._halt("stopped" if self._move else None)
        return {}

    def _cmd_pause(self, f, owner, cmd_id):
        self.stop_and_hold("pause", "stopped")
        return {}

    def _cmd_set_speed(self, f, owner, cmd_id):
        self._need_car()
        direction = self._motion_dir()
        if direction is None:
            return {"speed": 0}
        eff = safety.effective_speed(direction, f["speed"], self.cfg["speed_limit"])
        if eff == 0:
            self._halt("stopped" if self._move else None)
            return {"speed": 0}
        self._apply(direction, eff, new_command=False)
        return {"speed": eff}

    def _cmd_steer(self, f, owner, cmd_id):
        self._need_car()
        angle = safety.clamp_steer(f["angle"])
        try:
            self.backend.set_steering(angle)
        except Exception as e:
            raise CmdError("hw_fault", f"steering write failed: {e}")
        self._steer = angle
        return {"angle": angle}

    def _cmd_config(self, f, owner, cmd_id):
        for k in ("obstacle_stop", "stop_cm", "speed_limit"):
            if k in f:
                self.cfg[k] = f[k]
        if self.backend is not None and "speed_limit" in f:
            direction = self._motion_dir()
            if direction is not None:
                eff = safety.effective_speed(direction, self._eff, self.cfg["speed_limit"])
                # Lowering the limit reduces running motion immediately; raising it never speeds up.
                if eff != self._eff:
                    if eff == 0:
                        self._halt("stopped" if self._move else None)
                    else:
                        try:
                            self._apply(direction, eff, new_command=False)
                        except CmdError:   # the running motion is invalid at the new limit: never leave it running
                            self._halt("stopped" if self._move else None)
        self._set_hint()
        return {"config": dict(self.cfg)}

    def _cmd_estop(self, f, owner, cmd_id):
        self.estop("controller")
        return {}

    def _cmd_estop_reset(self, f, owner, cmd_id):
        if self.backend is not None:
            try:
                ok = self.backend.stop_motors()
            except Exception:
                ok = False
            if ok is False:
                raise CmdError("hw_fault", "could not confirm the motors stopped")
        if self.estopped or self.hw_fault:
            self.estopped, self.hw_fault = False, None
            self._sync_latch()
            self._emit("estop_reset")
        return {}

    def _cmd_resume(self, f, owner, cmd_id):
        if self.hold:
            self.hold, self.hold_reason = False, None
            self._sync_latch()
            self.touch()
            self._emit("resumed")
        return {}

    def _cmd_reset_pose(self, f, owner, cmd_id):
        self._halt("stopped" if self._move else None)
        if self.backend is not None and hasattr(self.backend, "reset_pose"):
            self.backend.reset_pose()
        self._pose = [0.0, 0.0, 90.0]
        return {}

    def _cmd_calibrate(self, f, owner, cmd_id):
        if not self.shared:
            raise CmdError("bad_request", "calibrate applies to the car, not the simulator")
        self._need_car()
        if "steer_trim_deg" in f:
            self.cal["steer_trim_deg"] = f["steer_trim_deg"]
            fn = getattr(self.backend, "set_trim", None)
            try:
                if fn:
                    fn(f["steer_trim_deg"])
                self.backend.set_steering(self._steer)
            except Exception as e:
                self._latch_hw_fault(f"steering write failed: {e}")
                self._halt("hw_fault")
                raise CmdError("hw_fault", f"steering write failed: {e}")
        if f.get("save") and self._save_cal:
            try:
                self._save_cal(self.cal)
            except OSError as e:
                raise CmdError("hw_fault", f"could not save calibration: {e}")
        return {"steer_trim_deg": self.cal["steer_trim_deg"]}

    # ---- safety entry points (loop thread) ------------------------------------------------------
    def estop(self, by: str) -> None:
        """Latched emergency stop; always latches, even without a car."""
        self.estopped = True
        self._sync_latch()
        self._halt("estop", [("estop", {"by": by})])
        self._drain()

    def stop_and_hold(self, reason: str, result: Optional[str] = "stopped", running_event: bool = False) -> None:
        was_running = self._moving()
        events = []
        if running_event and was_running:
            events.append(("watchdog_stop", {}))
        if not self.hold:
            self.hold, self.hold_reason = True, reason
            events.append(("hold", {"reason": reason}))
        self._sync_latch()
        self._halt(result, events)
        self._drain()

    def stop_for_transition(self) -> None:
        """Control/mode/world change: stop motion first."""
        self._halt("stopped" if self._move else None)
        self._drain()

    def controller_lost(self) -> None:
        self.attached = False
        self.stop_and_hold("disconnect")

    def new_controller(self, returning: bool) -> None:
        """Hand-over: e-stop persists; config resets; hold clears for a new controller (not a boot hold)."""
        self.attached = True
        if not returning:
            self.cfg = dict(safety.DEFAULT_CONFIG)
            if self.hold and self.hold_reason != "boot":
                self.hold, self.hold_reason = False, None
        self._sync_latch()
        self.touch()
        self._set_hint()

    def _clear_block(self, force: bool = False) -> None:
        if self.blocked:
            self.blocked = False
            self._clear_since = None
            if self.backend is not None:
                try:
                    self.backend.clear_inhibit()
                except Exception:
                    log.exception("clear_inhibit failed")

    # ---- sensor thread: obstacle guard ---------------------------------------------------------
    def _on_sample(self, s: Sample) -> None:
        try:
            self._guard(s)
        except Exception:
            log.exception("guard failed; stopping the motors")
            try:
                self.backend.stop_motors()
            except Exception:
                pass

    def _guard(self, s: Sample) -> None:
        now = self._clock()
        gen = self._gen   # read before any stop: only a move at least this old is finished by this stop
        forward = self._motion_dir() == "forward"
        eff = self._eff
        distance_guard = self.cfg["obstacle_stop"]
        margin = self._margin(eff if forward else (self._speed_setting or 0))
        events: list = []
        with self._lock:
            self._sample, self._sample_at = s, now
            if s.dist_state in (DIST_OK, DIST_FAR):
                self._last_good_t = now
                self._last_ok = (s.dist_cm, now) if s.dist_state == DIST_OK and s.dist_cm is not None else None
            fault_now = s.dist_state == DIST_FAULT
            if fault_now != self._us_fault:
                self._us_fault = fault_now
                events.append(("sensor_fault" if fault_now else "sensor_ok", {"sensor": "ultrasonic"}))
            line_ok = s.line_raw is not None
            if self._line_ok is not None and line_ok != self._line_ok:
                events.append(("sensor_ok" if line_ok else "sensor_fault", {"sensor": "line"}))
            if self._line_ok is None:
                self._line_ok = True if line_ok else None
            else:
                self._line_ok = line_ok
            stale = self._last_good_t is None or now - self._last_good_t > safety.SENSOR_STALE_S
        reason = safety.guard_decision(s.dist_state, s.dist_cm, margin, moving_forward=forward,
                                       distance_guard=distance_guard, stale=stale)
        if reason:
            self.backend.inhibit_forward()
            with self._lock:
                self.blocked, self._clear_since = True, None
            if reason == "obstacle":
                events.append(("obstacle_stop", {"cm": None if s.dist_cm is None else round(s.dist_cm, 1)}))
            else:
                events.append(("sensor_fault", {"sensor": "ultrasonic"}))
            self._defer("obstacle", events, gen)
        else:
            self._update_clear(s, now, margin, distance_guard)
            if events:
                self._defer(None, events)
        if not hasattr(self.backend, "set_deadline_probe"):
            self._check_deadlines_here(now)
        self._enforce_latches(gen)
        self._kick()

    def _update_clear(self, s: Sample, now: float, margin: float, distance_guard: bool) -> None:
        if not self.blocked:
            return
        if safety.is_clear_sample(s.dist_state, s.dist_cm, margin, distance_guard):
            if self._clear_since is None:
                self._clear_since = now
            elif now - self._clear_since >= safety.CLEAR_S:
                self._clear_block()
        else:
            self._clear_since = None

    # ---- deadlines ---------------------------------------------------------------------------
    def _deadline_probe(self) -> tuple[bool, bool]:
        now = self._clock()
        return (now - self.last_loop_tick <= 1.0,
                now - self.last_controller_frame <= safety.WD_MS / 1000.0 or not self.attached)

    def _deadline_latch(self, reason: str) -> None:
        """Called from the sensor thread after the backend tried to stop the motors."""
        gen = self._gen
        if reason == "stop_failed":
            self._latch_hw_fault("could not confirm the motors stopped", gen)
        else:
            with self._lock:
                self.hold, self.hold_reason = True, "watchdog"
            self._sync_latch()
            self._defer("watchdog", [("watchdog_stop", {}), ("hold", {"reason": "watchdog"})], gen)
        self._kick()

    def _check_deadlines_here(self, now: float) -> None:
        loop_ok, ctl_ok = self._deadline_probe()
        if (loop_ok and ctl_ok) or not self._moving():
            return
        stopped = self.backend.stop_motors() is not False
        self._deadline_latch(("loop_stalled" if not loop_ok else "controller_silent") if stopped else "stop_failed")

    # ---- the loop tick -------------------------------------------------------------------------
    def tick(self) -> None:
        now = self._clock()
        self.last_loop_tick = now
        dt = min(max(now - self._tick_t, 0.0), 0.25)
        self._tick_t = now
        self._drain()
        if self.backend is None:
            return
        self._enforce_latches()
        motion_dir = self._motion_dir()
        moving = motion_dir is not None
        if not moving and self._dir is not None:
            self._dir, self._eff = None, 0   # the backend stopped on its own (guard / deadline)
        # watchdog: silence of the attached controller latches hold whether or not the motors run
        if self.attached and not self.hold and now - self.last_controller_frame > safety.WD_MS / 1000.0:
            self.stop_and_hold("watchdog", "watchdog", running_event=True)
        # reverse budget
        self._account_reverse()
        if motion_dir == "backward":
            self._stop_since = None
            if self._rev_used >= safety.REVERSE_MS_MAX / 1000.0:
                self._halt("reverse_timeout", [("reverse_timeout", {})])
        elif motion_dir == "forward":
            self._rev_used, self._stop_since = 0.0, None
        else:
            if self._stop_since is None:
                self._stop_since = now
            elif now - self._stop_since >= safety.REVERSE_RESET_S:
                self._rev_used = 0.0
        # forward drive with the distance guard off
        if (motion_dir == "forward" and not self.cfg["obstacle_stop"] and self._move is None
                and self._drive_since is not None and now - self._drive_since > safety.DRIVE_TIMEOUT_S):
            self._halt(None, [("drive_timeout", {})])
        # sensor freshness: the loop's own check (the sensor thread may be gone)
        if motion_dir == "forward" and now - self._sample_at > 0.5:
            self.backend.inhibit_forward()
            with self._lock:
                self.blocked, self._clear_since = True, None
            self._halt("obstacle", [("sensor_fault", {"sensor": "ultrasonic"})])
        self._dead_reckon(dt)
        self._drain()

    # ---- dead reckoning -----------------------------------------------------------------------
    def dead_reckon(self, dt: float) -> None:
        self._dead_reckon(dt)

    def _dead_reckon(self, dt: float) -> None:
        if self.backend is None or self.backend.pose() is not None or dt <= 0:
            return
        l, r = self.backend.motion()
        d = (l + r) / 2.0
        if d == 0:
            return
        v = safety.interp(self.cal["speed_table"], abs(d) / safety.DUTY_MAX * 100.0) * (1 if d > 0 else -1)
        x, y, heading = self._pose
        dth = v / self.cal["wheelbase_cm"] * math.tan(math.radians(self._steer)) * dt
        mid = math.radians(heading) + dth / 2.0
        self._pose = [x + v * dt * math.sin(mid), y + v * dt * math.cos(mid),
                      safety.wrap_deg(heading + math.degrees(dth))]

    # ---- telemetry -----------------------------------------------------------------------------
    def pose(self) -> tuple[list, str]:
        p = self.backend.pose() if self.backend is not None else None
        if p is not None:
            return list(p), "sim"
        return list(self._pose), "dr"

    def snapshot(self) -> dict:
        """Everything in `tel` except t/seq/ts/mode/ctl."""
        now = self._clock()
        b = self.backend
        with self._lock:
            s, sample_at = self._sample, self._sample_at
        caps = b.capabilities() if b is not None else {}
        l, r = b.motion() if b is not None else (0, 0)
        direction = "forward" if (l > 0 or r > 0) else "backward" if (l < 0 or r < 0) else None
        moving = direction is not None
        speed = self._eff if moving else 0
        drive = {"dir": direction, "speed": speed, "duty": [l, r], "moving": moving}
        if b is not None and getattr(b, "bumped", None) is not None:
            drive["bumped"] = bool(b.bumped)
        if s is None or b is None or now - sample_at > 1.0:
            dist = {"cm": None, "state": DIST_FAULT}
        else:
            dist = {"cm": round(s.dist_cm, 1) if s.dist_state == DIST_OK and s.dist_cm is not None else None,
                    "state": s.dist_state}
        margin = self._margin(self._eff if moving else self._speed_setting)
        line = {"ok": False, "raw": None, "on": None, "pos": None}
        if s is not None and s.line_raw is not None and now - sample_at <= 1.0:
            on = safety.line_on(s.line_raw, self.cal["line_threshold"])
            line = {"ok": True, "raw": list(s.line_raw), "on": on, "pos": safety.line_pos(on)}
        light = {"ok": False, "raw": None, "bright": None, "max": "none"}
        if s is not None and s.light_raw is not None and now - sample_at <= 1.0:
            bright = [safety.brightness(v, self.cal) for v in s.light_raw]
            light = {"ok": True, "raw": list(s.light_raw), "bright": bright, "max": safety.brightest(bright)}
        (x, y, d), src = self.pose()
        mv = self._move
        return {
            "drive": drive, "steer": self._steer, "dist": dist,
            "obstacle": dist["cm"] is not None and dist["cm"] < margin,
            "line": line, "light": light,
            "pose": {"x": round(x, 1), "y": round(y, 1), "dir": round(d, 1), "src": src},
            "safety": {"estop": self.estopped, "hold": self.hold, "calibrated": bool(self.cal.get("calibrated")),
                       "obstacle_stop": self.cfg["obstacle_stop"], "stop_cm": self.cfg["stop_cm"],
                       "margin_cm": round(margin, 1), "speed_limit": self.cfg["speed_limit"],
                       "blocked": self.blocked, "hw_fault": self.hw_fault},
            "move": None if mv is None else {"id": mv.id, "remaining_ms": max(0, int((mv.end_t - now) * 1000))},
        }

    def capabilities(self) -> dict:
        caps = self.backend.capabilities() if self.backend is not None else {}
        return {"ultrasonic": bool(caps.get("ultrasonic")), "light": bool(caps.get("light")),
                "line": bool(caps.get("line"))}
