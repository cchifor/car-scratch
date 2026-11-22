"""Ultrasonic echo timing from kernel-timestamped edges (decision D9, plan item M8).

Same interface as the polling `sensors.Ultrasonic` (measure(), the D7/D8 fields and the BUSY/FAR/None results are
shared); only ping() and the lifecycle differ. The kernel timestamps every edge in its interrupt handler, so the
timing no longer depends on Python being scheduled, and the thread sleeps instead of spinning.

The line is never driven. It stays an input with both edges detected, held low by the internal pull-down; a ping
switches to the pull-up and straight back. The module times its answer from that RISE (measured on the car
2026-11-22: its echo rises 440-531 us after the pull-up starts, however long the pull-up is held, and a pull-up held
400 us or more hides that rise under itself), so the pull-down follows at once. Edge detection is on all the time:
there is no window in which an echo could come and go unseen, and nothing can fight the module's output.

What the rules rest on, measured on this module and kernel and checked on every ping (a ping that breaks one is
discarded with a possible echo; five in a row switch the server back to polling):

  RISE_MIN_S, RISE_MAX_S  the module's first rise comes this long after our pull-up starts (measured 440-531 us).
                 Earlier: the module answered something else. Later: a delayed IRQ thread merged the true first rise
                 away (it lies >= a glitch plus a dip, ~0.26 ms, after the true one).
  FALL_LAT_S     a fall's stamp is late by at most this (measured <= 75 us); subtracted from every width, so a
                 reading is never farther than the first echo while the kernel's IRQ entry latency stays below it
  TRIGGER_MAX_S  the whole trigger (pull-up start to the pull-down's return) takes at most this (measured
                 34-158 us). Our own edges are then stamped by t_fall_hi + FALL_LAT_S, before any module edge
                 (RISE_MIN_S > TRIGGER_MAX_S + FALL_LAT_S), so the two can't be mistaken for each other
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

RISE_MIN_S = 0.0004                         # from the start of our pull-up
RISE_MAX_S = 0.0006
FALL_LAT_S = 0.0001
TRIGGER_MAX_S = 0.00028                     # pull-up start to the pull-down call's return
QUIET_S = 0.002                             # no edge for this long (and the line low) before a trigger
QUIET_MAX_S = 0.05                          # still not quiet after this: treat the line as busy
LINGER_S = 0.0003                           # after the first fall, wait this long for a second pulse (>= 1 ms in
                                            # practice: the kernel poll rounds up to whole milliseconds)
SLICE_S = 0.04                              # longest single wait (the stop flag is checked between waits)
FALL_SETTLE_S = 0.002                       # a low line with no fall delivered: time for the kernel to queue it
FLOOR_BREAKS_MAX = 5                        # consecutive pings failing a check before falling back to polling
NO_ANSWER_MAX = 5                           # consecutive triggers with no answer before falling back to polling
PROBE_TRIES, PROBE_NEED = 5, 4              # start-up: the module must answer pull-up triggers
KEEP = 4096
CM_PER_S = 34300 / 2

assert RISE_MIN_S > TRIGGER_MAX_S + FALL_LAT_S

# auto mode falls back to polling only when the kernel interface is missing, rejects edges, or the module does not
# answer a pull-up trigger; a line held by another process (EBUSY) or no permission is an error, as when polling
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
        self.broken = False                        # the module's checks keep failing: the hub switches to polling
        self.floor_breaks = 0
        self.no_answer = 0
        self.answered = False                      # the last triggered ping saw the module answer
        self.triggered = False                     # the last ping() triggered the module
        self.diag: collections.Counter = collections.Counter()   # diagnostics, not discards (logged per minute)
        self.trigger_s: collections.deque = collections.deque(maxlen=KEEP)     # whole trigger durations
        self.second_widths: collections.deque = collections.deque(maxlen=256)  # second pulses (the double pulse)
        self.anomalies: collections.deque = collections.deque(maxlen=64)       # details of discarded pings
        self._armed = False
        self._seq = 0                              # a new request's events count from 1
        self._gap = False
        self._last_edge_t: Optional[float] = None
        self._unwatched_next = False
        self._cycle_fell = False
        self._broke = False
        self._pre: list = []                       # events stamped before our trigger began (but after the quiet check)
        self._ours = 0                             # events stamped inside our own trigger window
        self._win = (0.0, 0.0, 0.0)                # this ping's t_up_lo, t_fall_lo, t_fall_hi

    # -- events -----------------------------------------------------------------------------------------
    def _take(self) -> list:
        out = self.line.events()
        for e in out:
            if e.seqno != (self._seq + 1) & 0xFFFFFFFF:
                self._gap = True
            self._seq = e.seqno
            if self._last_edge_t is None or e.t > self._last_edge_t:
                self._last_edge_t = e.t
        return out

    def _pull(self, deadline: float) -> list:
        """New events, waiting until `deadline` at most (absolute, in slices, checking `stop`); after the deadline
        one last read, so events queued meanwhile are never missed. Events count by their stamps, not delivery."""
        c = self.clock
        while True:
            if self.stop.is_set():
                raise _Stopped
            got = self._take()
            if got:
                return got
            left = deadline - c.monotonic()
            if left <= 0:
                return self._take()
            self.line.wait(min(left, SLICE_S))

    def _collect(self, evs: list, until: float, t_ours: float, done: Callable[[list], bool]) -> None:
        while not done(evs):
            new = self._pull(until)
            if not new:
                return
            self._sort(new, evs, t_ours)

    def _sort(self, new: list, evs: list, t_ours: float) -> None:
        t_up_lo = self._win[0]
        for e in new:
            if e.t < t_up_lo:
                self._pre.append(e)                    # the line moved after the quiet check, before our trigger
            elif e.t <= t_ours:
                self._ours += 1                        # our own trigger's edges (merged or misread: 1 or 2)
                self.diag["trigger_edges"] += 1
            else:
                evs.append(e)

    def _wait_quiet(self) -> bool:
        """True once the line is low and has had no edge for QUIET_S (the module has finished answering)."""
        c, ln = self.clock, self.line
        end = c.monotonic() + QUIET_MAX_S
        while True:
            got = self._take()
            if got:
                self.diag["tail_edges"] += len(got)
            if ln.get() == 1:
                return False
            now = c.monotonic()
            last = self._last_edge_t
            if last is None or now - last >= QUIET_S:
                return True
            if now >= end:
                return False
            got = self._pull(min(last + QUIET_S, end))
            if got:
                self.diag["tail_edges"] += len(got)

    # -- ping -------------------------------------------------------------------------------------------
    def ping(self):
        self.busy_started = self.triggered = self.answered = False
        self.ping_start = self.lost_echo_cm = None
        self._broke = False
        if self.stop.is_set():
            return None
        try:
            r = self._ping()
        except _Stopped:
            return None
        except OSError as e:
            self.discards["io_error"] += 1
            self.lost_echo_cm = 0.0
            self._armed = False
            if self._busy_since is None:
                # unknown: the module may be answering. Follow it until the line is low (and, before the next trigger,
                # quiet); an I/O error is no evidence of a lost edge, so no "lost fall" when it is simply low.
                self._busy_since, self._cycle_fell = self.clock.monotonic(), True
            try:
                self.line.arm()                        # back to the pull-down (a trigger may be half done)
                self._armed = True
            except OSError:
                pass
            if self.discards["io_error"] == 1:
                log.warning("ultrasonic: GPIO error: %s", e)
            r = None
        if self.triggered and not self._broke:
            self.floor_breaks = 0                      # only a triggered ping that passed every check resets it
        return r

    def _ping(self):
        c, ln = self.clock, self.line
        if not self._armed:
            ln.arm()
            self._armed = True
        if self._busy_since is not None:
            r = self._follow_cycle()
            if r is not _FREE:
                return r
        if not self._wait_quiet():
            if self._busy_since is None:
                # High with no cycle known (start-up, a discard, a late pulse): follow it, never "nothing there"
                self._busy_since, self._cycle_fell = c.monotonic(), False
                self.diag["found_high"] += 1
            return BUSY
        unwatched, self._unwatched_next = self._unwatched_next, False
        if self._gap:                                  # events were lost while waiting for quiet: don't trust it
            unwatched, self.diag["quiet_gap"] = True, self.diag["quiet_gap"] + 1
        self._gap = False
        self.triggered = True
        self._pre, self._ours = [], 0
        self.ping_start = t_up_lo = c.monotonic()      # our pull-up (the trigger's rise, the module's time 0) is after this
        ln.pull_up()                                   # (the call itself holds it up ~20 us: enough for the module)
        t_fall_lo = c.monotonic()                      # the trigger's fall happens after this ...
        ln.arm()
        t_fall_hi = c.monotonic()                      # ... and before this
        self._win = (t_up_lo, t_fall_lo, t_fall_hi)
        self.trigger_s.append(t_fall_hi - t_up_lo)
        t_ours = t_fall_hi + FALL_LAT_S                # our own trigger edges are stamped by then

        def has_fall(es):
            return any(not e.rising for e in es)

        # 1. the first pulse: its rise, then its fall (40 ms after the rise at most)
        evs: list = []
        deadline = t_fall_lo + ECHO_START_TIMEOUT_S
        while not has_fall(evs):
            before = len(evs)
            self._collect(evs, deadline, t_ours, has_fall)
            if evs and evs[0].rising:
                deadline = max(deadline, evs[0].t + ECHO_STUCK_S)
            if len(evs) == before:
                break
        line_high = ln.get() == 1
        if not has_fall(evs) and not line_high:        # low, but no fall delivered: let the kernel queue it
            self._collect(evs, c.monotonic() + FALL_SETTLE_S, t_ours, has_fall)
            line_high = ln.get() == 1
        # 2. linger for a second pulse after the first fall, and follow it to its end (the double pulse)
        if has_fall(evs):
            f1 = next(e for e in evs if not e.rising)
            self._collect(evs, f1.t + LINGER_S, t_ours, lambda es: es[-1] is not f1)
            if evs[-1] is not f1 and evs[-1].rising:
                r2 = evs[-1]
                self._collect(evs, r2.t + ECHO_STUCK_S, t_ours, lambda es: not es[-1].rising)
            line_high = ln.get() == 1
        self._sort(self._take(), evs, t_ours)          # anything queued by the time of the level read
        return self._classify(evs, unwatched, t_up_lo, t_fall_lo, t_fall_hi, line_high)

    def _classify(self, evs: list, unwatched: bool, t_up_lo: float, t_fall_lo: float, t_fall_hi: float,
                  line_high: bool):
        c = self.clock
        self.answered = bool(evs)

        def discard(why: str):
            self.discards[why] += 1
            self.lost_echo_cm = 0.0                    # an echo may have come and gone unseen: at any distance
            self._record(why, evs, line_high)
            self._last_edge_t = c.monotonic()          # the next trigger waits for a quiet line from here
            if line_high:                              # the module is still answering: follow it
                self._busy_since, self._cycle_fell = c.monotonic(), False
                return BUSY
            return None

        if unwatched:
            return discard("unwatched")
        if t_fall_hi - t_up_lo > TRIGGER_MAX_S:
            self._count_break("slow_trigger")
            return discard("slow_trigger")             # a module edge could hide among our own trigger's
        if self._gap:
            return discard("seq_gap")
        if self._pre:
            return discard("pre_trigger")              # the module answered something else just before our trigger
        if self._ours > 2:
            self._count_break("trigger_window")
            return discard("trigger_window")           # more edges in our trigger window than our own two
        expect_rise = True
        for e in evs:
            if e.rising != expect_rise:
                return discard("edges")
            expect_rise = not expect_rise
        pulses = []                                    # (rise, fall or None)
        for e in evs:
            if e.rising:
                pulses.append([e.t, None])
            else:
                pulses[-1][1] = e.t
        if pulses and pulses[0][0] < t_up_lo + RISE_MIN_S:
            return self._floor_break("early_rise", evs, line_high)
        if pulses and pulses[0][0] > t_up_lo + RISE_MAX_S:
            return self._floor_break("late_rise", evs, line_high)
        if not pulses:
            if line_high:
                return discard("edges")                # high, but no rise was reported
            self.discards["no_rise"] += 1
            self.no_answer += 1
            if self.no_answer >= NO_ANSWER_MAX and not self.broken:
                log.error("ultrasonic: %d pull-up triggers in a row had no answer", self.no_answer)
                self.broken = True
            return None
        self.no_answer = 0
        r1, f1 = pulses[0]
        if f1 is None:
            if not line_high:
                return discard("lost_fall")
            # nothing ended within 40 ms of the rise, watched throughout: the module's long cycle ("nothing there")
            self._busy_since, self._cycle_fell = r1, False
            self.busy_started = True
            return BUSY
        if len(pulses) > 1:
            self.diag["multi"] += 1
            r2, f2 = pulses[1]
            if f2 is not None:
                self.second_widths.append(f2 - r2)
        if pulses[-1][1] is None and line_high:        # a later pulse still holds the line: follow it
            self._busy_since, self._cycle_fell = pulses[-1][0], False
        width = f1 - r1 - FALL_LAT_S
        if width > ECHO_STUCK_S:                       # a cycle that has already ended
            self.busy_started = True
            return BUSY
        width = max(0.0, width)
        if width > ECHO_FAR_S:
            return FAR
        return width * CM_PER_S

    def _count_break(self, why: str) -> None:
        """A check failed: FLOOR_BREAKS_MAX in a row switch the server to polling."""
        self._broke = True
        self.floor_breaks += 1
        if self.floor_breaks >= FLOOR_BREAKS_MAX and not self.broken:
            log.error("ultrasonic: %d pings in a row failed the edge timing's checks (last: %s)",
                      self.floor_breaks, why)
            self.broken = True

    def _floor_break(self, why: str, evs: list, line_high: bool):
        self._count_break(why)
        self.discards[why] += 1
        self.lost_echo_cm = 0.0
        self._record(why, evs, line_high)
        self._last_edge_t = self.clock.monotonic()
        if line_high:
            self._busy_since, self._cycle_fell = self.clock.monotonic(), False
            return BUSY
        return None

    def _record(self, why: str, evs: list, line_high: bool) -> None:
        """Keep a discarded ping's events, in us from our pull-up, for the per-minute log and diagnostics."""
        up, lo, hi = self._win
        us = lambda t: round((t - up) * 1e6)          # noqa: E731
        self.anomalies.append({
            "why": why, "fall_lo": us(lo), "fall_hi": us(hi), "high": line_high, "ours": self._ours,
            "pre": [("R" if e.rising else "F", us(e.t)) for e in self._pre],
            "events": [("R" if e.rising else "F", us(e.t)) for e in evs],
        })

    def ready_for_handover(self) -> bool:
        """True when the polling fallback may take the pin: no cycle followed, and the line verified low and quiet
        (the polling sensor drives the pin)."""
        try:
            return self._busy_since is None and self._wait_quiet()
        except (OSError, _Stopped):
            return False

    def _follow_cycle(self):
        """A cycle (or a line found high) is in progress: BUSY, None when stuck, or _FREE once it has ended."""
        c, ln = self.clock, self.line
        last = None                                    # the kind of the last event seen now (True = a rise)
        for e in self._take():
            last = e.rising
            self._cycle_fell = not e.rising            # a rise after a fall: the module is answering again
        if ln.get() == 1:
            if c.monotonic() - self._busy_since <= BUSY_CYCLE_MAX_S:
                return BUSY
            self.discards["stuck"] += 1
            return None
        if not self._cycle_fell:                       # low, but no fall delivered yet: let the kernel queue it
            end = c.monotonic() + FALL_SETTLE_S
            while not self._cycle_fell:
                new = self._pull(end)
                if not new:
                    break
                for e in new:
                    last = e.rising
                    self._cycle_fell = not e.rising
        if ln.get() == 1 or last is True:
            return BUSY                                # it rose again meanwhile: still answering
        if not self._cycle_fell:
            self.diag["lost_fall"] += 1
            self._unwatched_next = True                # the kernel missed edges: don't trust the next ping either
        if self._gap:
            self.diag["follow_gap"] += 1
            self._unwatched_next = True
            self._gap = False                          # recorded: the next ping is unwatched
        self._busy_since = None
        return _FREE

    def probe(self) -> int:
        """Start-up check: how many of PROBE_TRIES pull-up triggers the module answers. Waits out a busy module
        (up to ~3 s); the counters are reset afterwards."""
        answered = tried = 0
        end = self.clock.monotonic() + 3.0
        while tried < PROBE_TRIES and self.clock.monotonic() < end:
            self.ping()
            if not self.triggered:
                self.clock.sleep(0.05)
                continue
            tried += 1
            answered += self.answered
        self.discards.clear()
        self.diag.clear()
        self.trigger_s.clear()
        self.second_widths.clear()
        self.no_answer = self.floor_breaks = 0
        self.broken = False
        return answered

    def close(self) -> None:
        self.line.close()


def open_edge_ultrasonic(clock, stop: Optional[threading.Event] = None, name: str = "GPIO5",
                         find=gpio_cdev.find_line, line_cls=gpio_cdev.EdgeLine) -> EdgeUltrasonic:
    """The edge implementation on the line called `name`, after checking that the kernel accepts edges on it and
    that the module answers a pull-up trigger."""
    path, offset = find(name)
    line = line_cls(offset, chip=path)
    try:
        line.arm()
        us = EdgeUltrasonic(line, clock, offset, stop=stop)
        answered = us.probe()
        if answered < PROBE_NEED:
            raise OSError(errno.EOPNOTSUPP, f"the module answered {answered} of {PROBE_TRIES} pull-up triggers")
        return us
    except BaseException:
        line.close()
        raise


MODES = ("poll", "edges", "auto")


def select_ultrasonic(mode: str, clock, make_poll: Callable[[], Ultrasonic], stop: Optional[threading.Event] = None,
                      open_edges=open_edge_ultrasonic) -> Ultrasonic:
    """`poll`, `edges` (no fallback at start) or `auto` (edges; polling when the kernel lacks the interface or the
    module does not answer a pull-up trigger). At run time either edge mode hands over to polling when the module
    keeps failing the checks."""
    if mode not in MODES:
        raise ValueError(f"unknown ultrasonic timing {mode!r} (expected one of {', '.join(MODES)})")
    if mode == "poll":
        return make_poll()
    try:
        return open_edges(clock, stop=stop)
    except OSError as e:
        if mode == "auto" and e.errno in FALLBACK_ERRNOS:
            log.warning("ultrasonic: kernel edge timing unavailable (%s); timing echoes by polling", e)
            return make_poll()
        raise
