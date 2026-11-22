"""Ultrasonic echo timing from kernel-timestamped edges (decision D9, plan item M8).

Same interface as the polling `sensors.Ultrasonic` (measure(), the D7/D8 fields and the BUSY/FAR/None results are
shared); only ping() and the lifecycle differ. The kernel timestamps every edge in its interrupt handler, so the
timing no longer depends on Python being scheduled, and the thread sleeps instead of spinning.

Edge detection works only on an input, so every ping drives the trigger, then re-arms. Arming takes ~0.1-0.5 ms,
and the module raises its line ~0.38 ms after the trigger: the rules below make a reading never farther than the
first echo, and "nothing there" only when no echo can have been missed. They rest on three measured properties of
this module and kernel, each checked on every ping (a broken floor discards the ping; five in a row switch the
server back to polling):

  RISE_MIN_S   the module raises its line no earlier than this after the trigger falls (measured >= 383 us)
  PULSE_MIN_S  no real pulse is shorter (1.7 cm, inside the module's 2 cm minimum range; glitches >= 0.2 ms)
  FALL_LAT_S   a fall's timestamp is late by at most this (measured <= 75 us); subtracted from every width
"""
from __future__ import annotations

import collections
import errno
import logging
import threading
from typing import Callable, Optional

from . import gpio_cdev
from .sensors import BUSY, BUSY_CYCLE_MAX_S, ECHO_FAR_S, ECHO_START_TIMEOUT_S, ECHO_STUCK_S, FAR, US_PIN, Ultrasonic

log = logging.getLogger("picar.sensors")

RISE_MIN_S = 0.00035
PULSE_MIN_S = 0.0001
FALL_LAT_S = 0.0001
ARM_CLEAN_S = RISE_MIN_S + PULSE_MIN_S      # a blind window up to this cannot hide a whole pulse
LINGER_S = 0.0003                           # after the first fall, wait this long for a second pulse
SLICE_S = 0.04                              # longest single wait (the stop flag is checked between waits)
FALL_SETTLE_S = 0.002                       # a low line with no fall delivered: time for the kernel to queue it
SETTLE_LOW_S = 0.002                        # the line is held low this long before the trigger (as when polling)
TRIGGER_S = 0.00001
FLOOR_BREAKS_MAX = 5                        # consecutive pings breaking a floor before falling back to polling
BLIND_KEEP = 4096
CM_PER_S = 34300 / 2

# auto mode falls back to polling only when the kernel interface is missing or rejects edges; a line held by another
# process (EBUSY) or no permission is an error, as it is for the polling implementation
FALLBACK_ERRNOS = frozenset({errno.ENOENT, errno.ENOTTY, errno.EINVAL, errno.ENODEV, errno.EOPNOTSUPP, errno.ENOSYS})


class _Stopped(Exception):
    pass


_FREE = object()


class EdgeUltrasonic(Ultrasonic):
    timing = "edges"

    def __init__(self, line, clock, pin: int = US_PIN, stop: Optional[threading.Event] = None):
        super().__init__(None, clock, pin)
        self.line = line
        self.stop = stop if stop is not None else threading.Event()
        self.broken = False                        # floors broken FLOOR_BREAKS_MAX times in a row: use polling
        self.floor_breaks = 0
        self.diag: collections.Counter = collections.Counter()   # diagnostics, not discards (logged per minute)
        self.blind: collections.deque = collections.deque(maxlen=BLIND_KEEP)   # blind window of each ping, s
        self.second_widths: collections.deque = collections.deque(maxlen=256)  # second pulses (the double pulse)
        self._armed = False
        self._seq: Optional[int] = None
        self._gap = False
        self._unwatched_next = False
        self._cycle_fell = False

    # -- events -----------------------------------------------------------------------------------------
    def _take(self) -> list:
        out = self.line.events()
        for e in out:
            if self._seq is not None and e.seqno != (self._seq + 1) & 0xFFFFFFFF:
                self._gap = True
            self._seq = e.seqno
        return out

    def _pull(self, deadline: float) -> list:
        """New events, waiting until `deadline` at most (absolute, in slices, checking `stop`); [] once the
        deadline has passed and a last read found nothing. Events count by their timestamps, not delivery."""
        c = self.clock
        while True:
            if self.stop.is_set():
                raise _Stopped
            got = self._take()
            if got:
                return got
            left = deadline - c.monotonic()
            if left <= 0:
                return []
            self.line.wait(min(left, SLICE_S))

    def _collect(self, evs: list, until: float, t_floor: float, done: Callable[[list], bool]) -> None:
        while not done(evs):
            new = self._pull(until)
            if not new:
                return
            for e in new:
                if e.t < t_floor:
                    self.diag["stale"] += 1                # queued before this ping: never part of it
                else:
                    evs.append(e)

    # -- ping -------------------------------------------------------------------------------------------
    def ping(self):
        self.busy_started = False
        self.ping_start = self.lost_echo_cm = None
        if self.stop.is_set():
            return None
        try:
            return self._ping()
        except _Stopped:
            return None
        except OSError as e:
            self._armed = False
            self.discards["io_error"] += 1
            self.lost_echo_cm = 0.0
            if self.discards["io_error"] == 1:
                log.warning("ultrasonic: GPIO error: %s", e)
            return None

    def _ping(self):
        c, ln = self.clock, self.line
        if self._busy_since is not None:
            r = self._follow_cycle()
            if r is not _FREE:
                return r
        else:
            if not self._armed:
                ln.arm()
                self._armed = True
            if ln.get() == 1:
                # High with no cycle known (start-up, or after a discarded ping): the module is answering
                # something. Never drive it; follow it like a cycle (never "nothing there").
                self._busy_since, self._cycle_fell = c.monotonic(), False
                self.diag["found_high"] += 1
                return BUSY
        unwatched, self._unwatched_next = self._unwatched_next, False
        ln.output_low()
        self._armed = False
        # output_low() freed the IRQ (free_irq waits for its thread), so everything queued is from before this
        # ping: the previous ping's later pulses.
        tail = self._take()
        if tail:
            self.diag["tail_edges"] += len(tail)
        self._gap = False
        c.sleep(SETTLE_LOW_S)
        self.ping_start = c.monotonic()
        ln.set(1)
        c.sleep(TRIGGER_S)
        t_fall_lo = c.monotonic()                      # the trigger's fall happens after this
        ln.set(0)
        ln.arm()
        self._armed = True
        t_armed = c.monotonic()
        high = ln.get() == 1
        t_high = c.monotonic()                         # the level read happened before this
        blind = t_armed - t_fall_lo                    # an upper bound on the time edges were not watched
        self.blind.append(blind)

        # 1. the first pulse: a rise (event, or before arming when high), then its fall
        evs: list = []
        deadline = t_armed + ECHO_STUCK_S if high else t_fall_lo + ECHO_START_TIMEOUT_S

        def has_fall(es):
            return any(not e.rising for e in es)

        while not has_fall(evs):
            before = len(evs)
            self._collect(evs, deadline, t_fall_lo, has_fall)
            if not high and evs and evs[0].rising:
                deadline = max(deadline, evs[0].t + ECHO_STUCK_S)
            if len(evs) == before:
                break
        if not has_fall(evs) and ln.get() == 0:
            # low, but no fall delivered: give the kernel's IRQ thread a moment to queue it
            self._collect(evs, c.monotonic() + FALL_SETTLE_S, t_fall_lo, has_fall)
        # 2. linger for a second pulse after the first fall, and follow it to its end (the double pulse)
        if has_fall(evs):
            f1 = next(e for e in evs if not e.rising)
            self._collect(evs, f1.t + LINGER_S, t_fall_lo, lambda es: es[-1] is not f1)
            if evs[-1] is not f1 and evs[-1].rising:
                r2 = evs[-1]
                self._collect(evs, r2.t + ECHO_STUCK_S, t_fall_lo, lambda es: not es[-1].rising)
        if high and evs and evs[0].rising and evs[0].t <= t_high:
            # The rise came after edge detection was on (it has an event) but before the level read: it is the
            # first pulse's rise, not a sign of a lost fall.
            high = False
        line_high = ln.get() == 1
        return self._classify(evs, high, blind, unwatched, t_fall_lo, t_armed, line_high)

    def _classify(self, evs: list, high: bool, blind: float, unwatched: bool, t_fall_lo: float, t_armed: float,
                  line_high: bool):
        c = self.clock

        def discard(why: str):
            self.discards[why] += 1
            self.lost_echo_cm = 0.0                    # an echo may have come and gone unseen: at any distance
            if line_high:                              # the module still holds the line: never drive it
                self._busy_since, self._cycle_fell = c.monotonic(), False
                return BUSY
            return None

        if unwatched:
            return discard("unwatched")
        if blind > ARM_CLEAN_S:
            return discard("late_arm")
        if self._gap:
            return discard("seq_gap")
        expect_rise = not high
        for e in evs:
            if e.rising != expect_rise:
                return discard("edges")
            expect_rise = not expect_rise
        # pulses: (rise, fall or None, rise observed)
        pulses = []
        rise, observed = (t_armed, False) if high else (None, False)
        for e in evs:
            if e.rising:
                rise, observed = e.t, True
            else:
                pulses.append((rise, e.t, observed))
                rise = None
        if rise is not None:
            pulses.append((rise, None, observed))
        if pulses and pulses[0][2] and pulses[0][0] < t_fall_lo + RISE_MIN_S:
            return self._floor_break("early_rise")
        if any(o and f is not None and f - r < PULSE_MIN_S for r, f, o in pulses):
            return self._floor_break("short_pulse")
        if not pulses:
            if line_high:
                return discard("edges")                # high now, but no rise was reported
            self.discards["no_rise"] += 1
            return None
        r1, f1, _ = pulses[0]
        if f1 is None:
            if not line_high:
                return discard("lost_fall")
            # nothing ended within 40 ms of the rise, watched throughout: the module's long cycle ("nothing there")
            self._busy_since, self._cycle_fell = r1, False
            self.busy_started = True
            self.floor_breaks = 0
            return BUSY
        self.floor_breaks = 0
        if len(pulses) > 1:
            self.diag["multi"] += 1
            r2, f2, _ = pulses[1]
            if f2 is not None:
                self.second_widths.append(f2 - r2)
        last_r, last_f, _ = pulses[-1]
        if last_f is None and line_high:               # a later pulse still holds the line
            self._busy_since, self._cycle_fell = last_r, False
        if f1 - r1 > ECHO_STUCK_S:                     # its fall came after the 40 ms limit: a cycle that ended
            self.busy_started = True
            return BUSY
        width = max(0.0, f1 - r1 - FALL_LAT_S)
        if width > ECHO_FAR_S:
            return FAR
        return width * CM_PER_S

    def _floor_break(self, why: str):
        self.floor_breaks += 1
        self.discards[why] += 1
        self.lost_echo_cm = 0.0
        log.error("ultrasonic: %s (a measured property of the module did not hold)", why)
        if self.floor_breaks >= FLOOR_BREAKS_MAX:
            self.broken = True
        if self.line.get() == 1:
            self._busy_since, self._cycle_fell = self.clock.monotonic(), False
            return BUSY
        return None

    def _follow_cycle(self):
        """A cycle (or a line found high) is in progress: BUSY, None when stuck, or _FREE when it has ended."""
        c, ln = self.clock, self.line
        for e in self._take():
            self._cycle_fell = not e.rising            # a rise after a fall: the module is answering again
        if ln.get() == 1:
            if c.monotonic() - self._busy_since <= BUSY_CYCLE_MAX_S:
                return BUSY
            self.discards["stuck"] += 1
            return None
        if not self._cycle_fell:
            end = c.monotonic() + FALL_SETTLE_S
            while not self._cycle_fell:
                new = self._pull(end)
                if not new:
                    break
                for e in new:
                    self._cycle_fell = not e.rising
            if not self._cycle_fell:
                self.diag["lost_fall"] += 1
                self._unwatched_next = True            # the kernel missed edges: don't trust the next ping either
        self._busy_since = None
        return _FREE

    def close(self) -> None:
        self.line.close()


def open_edge_ultrasonic(clock, stop: Optional[threading.Event] = None, name: str = "GPIO5",
                         find=gpio_cdev.find_line, line_cls=gpio_cdev.EdgeLine) -> EdgeUltrasonic:
    """The edge implementation on the line called `name`, after checking the kernel accepts edges on it."""
    path, offset = find(name)
    line = line_cls(offset, chip=path)
    try:
        line.arm()                                     # the line is idle: arming only checks edge support
        line.input()
    except BaseException:
        line.close()
        raise
    return EdgeUltrasonic(line, clock, offset, stop=stop)


def select_ultrasonic(mode: str, clock, make_poll: Callable[[], Ultrasonic], stop: Optional[threading.Event] = None,
                      open_edges=open_edge_ultrasonic) -> Ultrasonic:
    """`poll`, `edges` (never falls back) or `auto` (edges; polling only when the kernel lacks the interface)."""
    if mode == "poll":
        return make_poll()
    try:
        return open_edges(clock, stop=stop)
    except OSError as e:
        if mode == "auto" and e.errno in FALLBACK_ERRNOS:
            log.warning("ultrasonic: kernel edge timestamps unavailable (%s); timing echoes by polling", e)
            return make_poll()
        raise
