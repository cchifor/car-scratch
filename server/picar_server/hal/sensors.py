"""Real sensors: ultrasonic (GPIO5), PCF8591 light (0x48), 5-way line follower (0x11).

Shared by the real and dry-run backends. All I2C traffic takes the backend-wide lock; the ultrasonic is
bit-banged on a GPIO and needs no lock. Timing sources are injectable so tests stay fast.
"""
from __future__ import annotations

import collections
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
ECHO_STUCK_S = 0.040             # echo still high at 40 ms: the module's long cycle (BUSY) or a stuck line, never "far"
BUSY_CYCLE_MAX_S = 0.6           # the long cycle holds the line high 401.5 ms (measured); longer than this = stuck line
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


class _Busy:
    def __repr__(self) -> str:
        return "BUSY"


# ping() / measure(): the module is in its long no-echo cycle. Measured on the car (2026-11-21): against a weak
# reflector about one ping in 15 the module holds the line high for 401.5 ms instead of its usual 27 ms no-echo
# timeout, and ignores triggers until that cycle ends. No reading, but not evidence of a fault.
BUSY = _Busy()


class SystemClock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class Ultrasonic:
    def __init__(self, gpio, clock, pin: int = US_PIN):
        self.gpio, self.clock, self.pin = gpio, clock, pin
        self._busy_since: Optional[float] = None   # start of an echo still high at ECHO_STUCK_S (pin left as input)
        self.busy_started = False                  # the last ping() began a long cycle cleanly (not: found it busy)
        self.ping_start: Optional[float] = None    # the last ping()'s echo rise (its "nothing there" time)
        self.lost_echo_cm: Optional[float] = None  # the last ping() was discarded but may have been an echo this close
        self.discards: collections.Counter = collections.Counter()   # why pings gave no reading (logged per minute)
        self.clear_t: Optional[float] = None       # measure(): last ping that heard nothing in range (far / cycle began)
        self.echo_cm: Optional[float] = None       # measure(): closest echo heard
        self.batch_t: Optional[float] = None       # measure(): when its pings began
        self.meas_t: Optional[float] = None        # measure(): trigger time of the newest valid ping

    def ping(self):
        """cm; FAR for an echo ending between 12 and 40 ms; BUSY while the module is in its long no-echo cycle;
        None for no valid ping (no echo, a line high for longer than any module cycle, or a busy-wait gap above
        1 ms while the echo is high, which makes the timing untrustworthy)."""
        g, c, p = self.gpio, self.clock, self.pin
        self.busy_started = False
        self.ping_start = self.lost_echo_cm = None
        if self._busy_since is not None:
            # Never trigger while the module holds the line high: it ignores triggers until its cycle ends, and
            # driving the pin low would fight the module's output. Report busy until the line drops; a line high
            # for longer than any module cycle is stuck (None, which counts toward the fault).
            if g.input(p) == 1:
                if c.monotonic() - self._busy_since <= BUSY_CYCLE_MAX_S:
                    return BUSY
                self.discards["stuck"] += 1
                return None
            self._busy_since = None
        g.setup(p, g.OUT, initial=g.LOW)
        c.sleep(0.002)
        # The "nothing there" time of this ping (D7) is taken before the trigger: a stall anywhere after this only
        # makes it earlier than the real ping, never later.
        self.ping_start = c.monotonic()
        g.output(p, 1)
        c.sleep(0.00001)
        g.output(p, 0)
        g.setup(p, g.IN)
        # One rule for every stall (a gap over MAX_GAP_S between two timestamps, from the trigger on): a stall means
        # the line was not watched, so an echo may have come and gone unseen (and the line risen again). A stalled
        # ping never yields a distance or "nothing there"; it records a possible echo at its safe lower bound. The
        # one exception: "far" is certain when the rise and the first 12 ms were watched without a stall.
        t0 = c.monotonic()
        stalled = t0 - self.ping_start > MAX_GAP_S
        prev = t0
        while True:                            # wait for the rise
            high = g.input(p) == 1
            now = c.monotonic()
            if now - prev > MAX_GAP_S:
                stalled = True
            prev = now
            if high:
                break
            if now - t0 > ECHO_START_TIMEOUT_S:
                if stalled:
                    self.lost_echo_cm = 0.0    # not watched for a while: a whole echo may have come and gone
                self.discards["unwatched" if stalled else "no_rise"] += 1
                return None
        rise_clean = not stalled               # the rise seen is the ping's first one
        start = now                            # just after the read that saw the line high
        clean_high: Optional[float] = None     # latest time the line is known high with no stall since the trigger
        while True:
            high = g.input(p) == 1
            now = c.monotonic()
            if now - prev > MAX_GAP_S:
                stalled = True
            seen_after = prev                  # this read happened after `prev` was taken (before `now`)
            prev = now
            if not high:
                break
            if not stalled:
                clean_high = seen_after
            if now - start > ECHO_STUCK_S:
                self._busy_since = start       # the pin stays an input; the next ping checks the line first
                if stalled:
                    self._lost_echo(rise_clean, clean_high, start)
                else:
                    self.busy_started = True   # 40 ms without an echo, watched throughout: "nothing there"
                return BUSY
        if stalled:
            if rise_clean and clean_high is not None and clean_high - start > ECHO_FAR_S:
                return FAR                     # watched from the rise past 12 ms: no echo ended within range
            self._lost_echo(rise_clean, clean_high, start)
            self.discards["gap"] += 1
            return None
        # The echo ended after the last read that saw it high (clean_high) and at or before this one: time it from
        # clean_high, the lower bound, so a pause right at the fall can only make the reading closer, never farther.
        dur = 0.0 if clean_high is None else max(0.0, clean_high - start)
        if dur > ECHO_FAR_S:
            return FAR
        return dur * 34300 / 2

    def _lost_echo(self, rise_clean: bool, clean_high: Optional[float], start: float) -> None:
        """A stalled ping may have hidden an echo: at least this close (the first pulse lasted past clean_high), or
        anywhere (0) when the rise itself was not watched."""
        lo = max(0.0, clean_high - start) if rise_clean and clean_high is not None else 0.0
        self.lost_echo_cm = min(lo, ECHO_FAR_S) * 34300 / 2

    def measure(self, n: int):
        """Median of n pings (needs 2 valid when n == 3); BUSY when the module went into its long cycle before
        enough valid pings (later pings would only see it busy); None = no valid ping. With two valid pings the
        lower one counts (median_low): averaging FAR (999) with a real 10 cm echo would report 505 cm."""
        need = 2 if n >= 3 else 1
        vals: list[float] = []
        self.clear_t = self.echo_cm = self.meas_t = None
        self.batch_t = self.clock.monotonic()          # no echo heard in this measurement is older than this
        for _ in range(n):
            v = self.ping()
            if self.lost_echo_cm is not None:
                self.echo_cm = self.lost_echo_cm if self.echo_cm is None else min(self.echo_cm, self.lost_echo_cm)
            if v is BUSY:
                if self.busy_started:                      # nothing heard for 40 ms: "nothing there" at the ping
                    self.clear_t = self.ping_start
                return statistics.median_low(vals) if len(vals) >= need else BUSY
            if v is not None:
                self.meas_t = self.ping_start
                if v >= FAR:
                    self.clear_t = self.ping_start
                else:
                    self.echo_cm = v if self.echo_cm is None else min(self.echo_cm, v)
                vals.append(v)
        if len(vals) < need:
            return None
        return statistics.median_low(vals)


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
        self._discard_log_t = clock.monotonic()
        self.clear_t: Optional[float] = None       # this sample's "nothing there" time (D7), see Sample.clear_t
        self.echo_cm: Optional[float] = None       # this sample's closest echo, see Sample.echo_cm
        self.echo_t: Optional[float] = None        # when this sample's pings began, see Sample.echo_t
        self.dist_t: Optional[float] = None        # trigger time of the newest valid ping, see Sample.dist_t
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
        self._log_discards()
        self.echo_cm = self.us.echo_cm
        self.echo_t = self.us.batch_t
        self.dist_t = None if v is None or v is BUSY else self.us.meas_t
        self.clear_t = self.us.clear_t if self.us.echo_cm is None else None   # any echo heard cancels "nothing there"
        if v is BUSY:
            # The module's long no-echo cycle: it answered the trigger and heard nothing (facing open space this is
            # its usual answer: 59-75 % of pings, measured). No reading this sample, but not a fault: it neither
            # counts toward FAULT_AFTER nor resets it. A line stuck high is caught in ping() (over 0.6 s). It clears
            # the near/far memory, so the first sample after the blind window takes 3 pings (an obstacle may have
            # appeared meanwhile). Forward motion still needs a fresh ok/far reading (controller, unchanged).
            self._last_cm, self._last_far = None, False
            return None, DIST_NONE if self._nones < FAULT_AFTER else DIST_FAULT
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

    def _log_discards(self) -> None:
        now = self.clock.monotonic()
        if now - self._discard_log_t < 60.0:
            return
        d = self.us.discards
        if sum(d.values()):
            log.info("ultrasonic: pings discarded in the last %.0f s: %s", now - self._discard_log_t,
                     " ".join(f"{k} {v}" for k, v in sorted(d.items())))
        d.clear()
        self._discard_log_t = now

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
    def _read_frame(self) -> tuple[Optional[list[int]], float, bool]:
        """One block read under the I2C lock only. Returns (five values or None, seconds the bus call took, True if
        the call failed). Measured on the car (2026-11-21): the module is busy for one read every 11 ms and answers
        all-0xFF then (about 15 % of reads at a 40 ms cadence), and about 1 sample in 300 gets EIO; any value above
        LINE_RAW_MAX marks the frame invalid."""
        with self.lock:
            t0 = self.clock.monotonic()
            try:
                raw = self.bus.read_i2c_block_data(LINE_ADDR, 0, 10)
            except OSError:
                return None, self.clock.monotonic() - t0, True
            took = self.clock.monotonic() - t0
        try:
            vals = [(raw[2 * i] << 8) | raw[2 * i + 1] for i in range(5)]
        except (IndexError, TypeError):
            return None, took, True
        return (None if max(vals) > LINE_RAW_MAX else vals), took, False

    def _read_checked(self, attempts: int = 2) -> tuple[Optional[list[int]], bool, bool]:
        """A frame, retried once at once when invalid or when the call failed fast (the next read is outside the
        module's busy window), but never after a slow bus call (a stalling module must hold the bus lock only once).
        Returns (values or None, True if a bus call was slow, True if the last call failed)."""
        slow = failed = False
        for _ in range(attempts):
            vals, took, failed = self._read_frame()
            if took > LINE_MOVING_SLOW_S:
                slow = True
            if vals is not None or slow:
                return vals, slow, failed
        return None, slow, failed

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
        vals, slow, failed = self._read_checked()
        if slow:
            self._suppress_line(f"a read took more than {LINE_MOVING_SLOW_S * 1000:.0f} ms")
            return vals                                    # a valid frame is still used, once
        if vals is None:                                   # failed twice, fast: skip this sample
            self._line_invalid_run += 1
            if self._line_invalid_run >= LINE_ABSENT_AFTER:
                self._suppress_line(f"{LINE_ABSENT_AFTER} samples in a row without a frame"
                                    + (" (I2C errors)" if failed else ""))
            else:
                self.line_skipped = True
            return None
        self._line_invalid_run = 0
        self._line_good += 1
        return vals

    def _suppress_line(self, why: str) -> None:
        self._line_suppressed = True
        self._line_good = 0
        self._line_invalid_run = 0
        log.warning("line follower: not reading while moving until the car stops (%s)", why)

    def _probe_line(self, now: float) -> Optional[list[int]]:
        vals, slow, failed = self._read_checked(2 if self.line_ok else 1)   # absent / never seen: one read per probe
        if vals is None and failed and not self.line_ok:
            return self._line_failed(now)                  # not present (never seen, or absent): back off at once
        if vals is None:                                   # failed twice (or once and slow): skip this sample
            if slow:
                self._line_good = 0                        # seen stalling: no moving reads until 10 good reads again
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
                      line_skipped=self.line_skipped, clear_t=self.clear_t, echo_cm=self.echo_cm,
                      echo_t=self.echo_t, dist_t=self.dist_t)
