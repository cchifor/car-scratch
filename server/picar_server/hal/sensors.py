"""Real sensors: ultrasonic (GPIO5), PCF8591 light (0x48), 5-way line follower (0x11).

Shared by the real and dry-run backends. All I2C traffic takes the backend-wide lock; the ultrasonic is
bit-banged on a GPIO and needs no lock. Timing sources are injectable so tests stay fast.
"""
from __future__ import annotations

import logging
import statistics
import time
from typing import Callable, Optional

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
                 line_enabled: Callable[[], bool] = lambda: False, us_pin: int = US_PIN):
        self.bus, self.lock, self.clock, self.motion, self.line_enabled = bus, lock, clock, motion, line_enabled
        self.us = Ultrasonic(gpio, clock, us_pin)
        self.near_hint_cm = DEFAULT_NEAR_HINT_CM
        self._nones = 0
        self._last_cm: Optional[float] = None     # last "ok" reading; None after far / none = see _last_far
        self._last_far = False
        self.light_ok = False
        self.line_ok = False
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
    def _read_light(self) -> Optional[list[int]]:
        try:
            out = []
            with self.lock:
                for ch in range(3):
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
    def _read_line(self) -> Optional[list[int]]:
        """Only when enabled in calibration and the motors are stopped (an I2C stall must not overlap motion)."""
        if not self.line_enabled() or self._moving():
            return None
        now = self.clock.monotonic()
        if not self.line_ok and now < self._line_next_probe:
            return None
        try:
            with self.lock:
                raw = self.bus.read_i2c_block_data(LINE_ADDR, 0, 10)
            vals = [(raw[2 * i] << 8) | raw[2 * i + 1] for i in range(5)]
            self._line_backoff = LINE_BACKOFF_S[0]
            self.line_ok = True
        except (OSError, IndexError):
            vals = None
            self.line_ok = False
            self._line_next_probe = now + self._line_backoff
            self._line_backoff = min(self._line_backoff * 2, LINE_BACKOFF_S[1])
        if self._line_known != self.line_ok:
            log.info("line follower %s", "present" if self.line_ok else "absent")
            self._line_known = self.line_ok
        return vals

    def probe(self) -> None:
        """Initial capability probe so capabilities() is meaningful before the first sample."""
        self._read_light()
        self._read_line()

    def read(self) -> Sample:
        cm, state = self._read_distance()
        return Sample(t=self.clock.monotonic(), dist_cm=cm, dist_state=state,
                      line_raw=self._read_line(), light_raw=self._read_light())
