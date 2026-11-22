"""Edge-timed ultrasonic (decision D9) with the pull-up trigger, on the fake kernel line.

The invariants, checked after every ping: a reading is never farther than the first pulse; FAR only when the first
pulse is longer than 12 ms; a cleanly started cycle (busy_started, D7's "nothing there") only when the first pulse
lasts over 40 ms; any other outcome of a ping whose module answered records lost_echo_cm = 0; the line is never
driven. Includes the regressions of the first implementation review."""
import asyncio
import errno
import threading

import pytest

from picar_server.hal import ultrasonic_edges as ue
from picar_server.hal.real import RealBackend
from picar_server.hal.sensors import BUSY, FAR

from fakes_hw import FakeBus, FakeClock, FakeEdgeLine, FakeGPIO, echo_for_cm

D = 0.00047                                             # the module's rise after our pull-up starts (measured 440-531 us)


def make(**line_kw):
    clock = FakeClock()
    line = FakeEdgeLine(clock)
    for k, v in line_kw.items():
        setattr(line, k, v)
    b = RealBackend({}, bus=FakeBus(), gpio=FakeGPIO(clock), clock=clock, us_timing="edges",
                    open_edges=lambda clk, stop=None: ue.EdgeUltrasonic(line, clk, stop=stop))
    return b, b.hub.us, line, clock


def check(us, line, v, first_width):
    """The invariants for one triggered ping whose module answered with a first pulse `first_width` long."""
    assert line.contention == 0 and line.mode != "output"
    if isinstance(v, float) and v < FAR:
        assert v <= first_width * ue.CM_PER_S + 1e-9, (v, first_width)
        assert us.lost_echo_cm is None
    elif v is FAR:
        assert first_width > 0.012 - 1e-9 and us.lost_echo_cm is None
    elif v is BUSY and us.busy_started:
        assert first_width > 0.04 - 1e-9 and us.lost_echo_cm is None
    elif us.triggered:
        assert us.lost_echo_cm == 0.0, (v, dict(us.discards), dict(us.diag))


# -- clean pings -----------------------------------------------------------------------------------------------
def test_selected_and_reported():
    b, us, line, clock = make()
    assert us.timing == "edges" and b.ultrasonic_timing == "edges"


def test_the_line_is_never_driven():
    b, us, line, clock = make()
    line.queue((D, echo_for_cm(50)))
    us.ping()
    assert line.mode == "armed" and line.out_value == 0 and line.pings == 1


def test_a_caught_echo_reads_at_most_the_allowance_closer():
    b, us, line, clock = make()
    line.queue((D, echo_for_cm(50)))
    v = us.ping()
    assert us.lost_echo_cm is None and 50 - ue.FALL_LAT_S * ue.CM_PER_S - 0.01 <= v <= 50
    assert us.diag["trigger_edges"] == 2                 # our own pull-up and pull-down, never taken for the echo


def test_far_and_its_clear_time():
    b, us, line, clock = make()
    line.queue((D, 0.027), (D, 0.027), (D, 0.027))
    s = b.hub.read()
    assert s.dist_state == "far" and s.clear_t is not None and s.echo_cm is None


def test_a_long_cycle_is_busy_then_followed_to_its_fall():
    b, us, line, clock = make()
    line.queue(("cycle", D, 0.4015))
    v = us.ping()
    assert v is BUSY and us.busy_started and us.lost_echo_cm is None
    pings = line.pings
    for _ in range(30):
        clock.advance(0.04)
        v = us.ping()
        if us._busy_since is None:
            break
        assert v is BUSY and not us.busy_started
    assert line.pings == pings + 1                       # the next trigger only after the cycle's fall (+ quiet)


def test_a_line_found_high_is_followed_and_never_clear():
    b, us, line, clock = make()
    line._pulses = [(clock.now - 0.1, clock.now + 0.2)]  # the module was already answering at start-up
    v = us.ping()
    assert v is BUSY and not us.busy_started and not us.triggered and us.diag["found_high"] == 1


# -- never farther, nothing missed -----------------------------------------------------------------------------
WIDTHS = [0.0002, 0.0005, 0.001, 0.003, 0.0119, 0.01199, 0.012, 0.01201, 0.0121, 0.02, 0.027, 0.0399, 0.0401]


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("rise", [0.00044, 0.00047, 0.00053])
@pytest.mark.parametrize("lat", [(0.0, 0.0, 0.0), (0.0, 0.00005, 0.0), (0.00003, 0.0001, 0.00002), (0.0004, 0.0, 0.0)])
def test_never_farther_and_never_falsely_clear(width, rise, lat):
    irq, fall_late, rise_late = lat
    b, us, line, clock = make(irq_thread=irq, fall_late=fall_late, rise_late=rise_late)
    line.queue((rise, width))
    v = us.ping()
    check(us, line, v, width)
    if irq < 0.0001 and width < 0.0399:
        assert isinstance(v, float), (v, dict(us.discards))   # a clean ping always gives a reading


PATTERNS = {
    "echo 34 cm": (D, 0.002),
    "echo then long cycle": ("multi", [(D, 0.002), (D + 0.004, 0.4)]),
    "glitch then echo": ("multi", [(D, 0.0003), (D + 0.0005, echo_for_cm(110))]),
    "glitch then far": ("multi", [(D, 0.0003), (D + 0.0005, 0.027)]),
    "far": (D, 0.027),
    "long cycle": ("cycle", D, 0.4015),
}


def first_width(p):
    return p[1][0][1] if p[0] == "multi" else p[2] if p[0] == "cycle" else p[1]


@pytest.mark.parametrize("name", sorted(PATTERNS))
@pytest.mark.parametrize("stall_s", [0.0003, 0.002, 0.05])
@pytest.mark.parametrize("pull_s", [0.00004, 0.0001])
@pytest.mark.parametrize("irq", [(0.0, 0.0), (0.00003, 0.0001), (0.0004, 0.0)])
def test_a_stall_anywhere_never_breaks_the_invariants(name, stall_s, pull_s, irq):
    p = PATTERNS[name]
    for at in range(1, 45):
        b, us, line, clock = make(pull_s=pull_s, irq_thread=irq[0], fall_late=irq[1])
        line.queue(p)
        clock.gap_after(at, stall_s)
        v = us.ping()
        check(us, line, v, first_width(p))


def test_a_slow_trigger_call_is_uncertain():
    b, us, line, clock = make(pull_s=0.0005)
    line.queue((D, 0.027))
    v = us.ping()
    assert v is None and us.lost_echo_cm == 0.0 and us.discards["slow_trigger"] == 1


def test_a_fall_queued_while_the_deadline_passes_is_still_seen():
    for at in range(1, 45):
        b, us, line, clock = make()
        line.queue(("multi", [(D, 0.001), (D + 0.0012, 0.4)]))
        clock.gap_after(at, 0.05)
        check(us, line, us.ping(), 0.001)


# -- kernel quirks ---------------------------------------------------------------------------------------------
def test_a_pulse_merged_by_the_irq_thread_reads_out_of_order_and_is_discarded():
    b, us, line, clock = make(irq_thread=0.00005)
    line.queue(("multi", [(D, 0.00002), (D + 0.0002, echo_for_cm(100))]))
    v = us.ping()
    assert v is None and us.lost_echo_cm == 0.0 and us.discards["edges"] == 1


def test_a_lost_event_is_a_sequence_gap():
    b, us, line, clock = make()
    line.queue(("multi", [(D, 0.0003), (D + 0.0005, echo_for_cm(100))]))
    line.drop_next = 3                                   # our two trigger edges and the echo's rise
    v = us.ping()
    assert v is None and us.lost_echo_cm == 0.0 and us.discards["seq_gap"] == 1


def test_an_overflow_before_the_first_event_is_a_gap():
    b, us, line, clock = make()
    line.buffer = 2
    line.queue(("multi", [(D, 0.001), (D + 0.002, 0.027)]))
    waits = []

    def away():
        if not waits:
            clock.advance(0.035)                         # the thread is away while all the edges arrive
        waits.append(1)

    line.on_wait = away
    v = us.ping()
    check(us, line, v, 0.001)
    assert us.discards["seq_gap"] == 1


def test_an_early_rise_breaks_the_floor():
    b, us, line, clock = make()
    line.queue((0.0003, echo_for_cm(50)))
    v = us.ping()
    assert v is None and us.lost_echo_cm == 0.0 and us.discards["early_rise"] == 1


def test_floor_breaks_count_in_a_row():
    b, us, line, clock = make()
    for i in range(9):
        line.queue((0.0003, echo_for_cm(50)) if i != 4 else (D, echo_for_cm(50)))
        us.ping()
        clock.advance(0.05)
    assert not us.broken
    line.queue((0.0003, echo_for_cm(50)))
    us.ping()
    assert us.broken


def test_no_answer_to_pull_triggers_falls_back_to_polling():
    b, us, line, clock = make(pull_trigger=False)
    for _ in range(ue.NO_ANSWER_MAX):
        assert us.ping() is None
        clock.advance(0.05)
    assert us.broken and us.discards["no_rise"] == ue.NO_ANSWER_MAX
    b.hub.read()
    assert b.hub.us.timing == "poll" and line.closed


# -- cycles ----------------------------------------------------------------------------------------------------
def test_a_lost_fall_of_a_cycle_makes_the_next_ping_unwatched():
    b, us, line, clock = make()
    line.queue(("cycle", D, 0.2))
    assert us.ping() is BUSY
    line.drop_next = 1                                   # the cycle's fall never reaches us
    clock.advance(0.25)
    line.queue((D, echo_for_cm(60)))
    v = us.ping()
    assert us.diag["lost_fall"] == 1 and v is None and us.lost_echo_cm == 0.0 and us.discards["unwatched"] == 1


def test_a_cycle_that_rises_again_during_the_settle_wait_stays_busy():
    b, us, line, clock = make()
    line.queue(("multi", [(D, 0.2), (D + 0.2005, 0.3)]))
    assert us.ping() is BUSY and us.busy_started
    t0 = clock.now
    line.drop_next = 1
    clock.now = t0 + 0.2003                              # inside the 0.5 ms dip, the fall lost
    pings = line.pings
    assert us.ping() is BUSY and line.pings == pings


def test_a_gap_while_following_a_cycle_makes_the_next_ping_unwatched():
    b, us, line, clock = make()
    line.queue(("multi", [(D, 0.2), (D + 0.25, 0.01)]))
    assert us.ping() is BUSY
    line.drop_next = 1                                   # the cycle's fall is lost, the later pulse arrives
    clock.now += 0.3
    line.queue((D, echo_for_cm(60)))
    v = us.ping()
    assert v is None and us.lost_echo_cm == 0.0 and us.discards["unwatched"] == 1


# -- the double pulse and late pulses ----------------------------------------------------------------------
def test_the_double_pulse_reads_the_first_pulse_and_leaves_the_line_free():
    b, us, line, clock = make()
    line.queue(("multi", [(D, 0.0003), (D + 0.00045, echo_for_cm(110))]))
    v = us.ping()
    assert v <= 0.0003 * ue.CM_PER_S and us.diag["multi"] == 1
    assert abs(us.second_widths[-1] - echo_for_cm(110)) < 1e-6
    clock.advance(0.03)
    line.queue((D, echo_for_cm(60)))
    v = us.ping()
    assert us.lost_echo_cm is None and 58 < v <= 60 and us.diag["found_high"] == 0


@pytest.mark.parametrize("gap_ms", [0.4, 0.8, 1.5, 1.9])
def test_a_late_second_pulse_is_never_taken_for_the_next_pings_echo(gap_ms):
    # second reviewer: check every ping, not the median. Within the measured double-pulse envelope (dips up to
    # 0.2 ms, here up to 1.9 ms) the quiet guard keeps the late pulse out of the next ping. A pulse more than 2 ms
    # after the answer ended would look like an answer to the next trigger (a residual, D9).
    b, us, line, clock = make()
    line.default = (D, echo_for_cm(40))
    line.queue(("multi", [(D, 0.0003), (D + 0.0003 + gap_ms / 1000, echo_for_cm(80))]))
    first = us.ping()
    assert first <= 0.0003 * ue.CM_PER_S
    for _ in range(2):
        v = us.ping()
        assert v is not FAR and not (isinstance(v, float) and v > 40.0), (gap_ms, v)
        if not isinstance(v, float):
            assert us.lost_echo_cm == 0.0 or not us.triggered, (gap_ms, v, dict(us.discards))


def test_untriggered_pings_do_not_reset_the_failure_count():
    # second reviewer: an early-rising module answering with long cycles; the cycle-following pings in between
    # must not keep zeroing the count, or the fallback is never reached
    b, us, line, clock = make()
    line.default = ("cycle", 0.0003, 0.4015)
    for _ in range(200):
        us.ping()
        clock.advance(0.04)
        if us.broken:
            break
    assert us.broken and us.discards["early_rise"] == ue.FLOOR_BREAKS_MAX


def test_a_stalled_pull_up_is_a_slow_trigger():
    for at in range(1, 8):
        b, us, line, clock = make()
        line.queue((D, 0.027))
        orig = us._wait_quiet

        def quiet():
            ok = orig()
            clock.gap_after(2, 0.001)                    # the call after ping_start: inside the pull-up
            return ok

        us._wait_quiet = quiet
        v = us.ping()
        assert v is not FAR or us.discards["slow_trigger"] == 0
        if us.discards["slow_trigger"]:
            assert v is None and us.lost_echo_cm == 0.0


def test_a_gap_while_waiting_for_quiet_makes_the_ping_unwatched():
    b, us, line, clock = make()
    line.queue(("multi", [(D, 0.0003), (D + 0.0003 + 0.0015, echo_for_cm(80))]))
    us.ping()                                            # the second pulse comes while the next ping waits for quiet
    line.drop_next = 1                                   # ... and one of its edges is lost
    line.queue((D, echo_for_cm(40)))
    for _ in range(10):
        v = us.ping()
        if us.triggered:
            break
        clock.advance(0.01)
    assert v is None and us.lost_echo_cm == 0.0 and us.discards["unwatched"] == 1
    assert us.diag["quiet_gap"] + us.diag["follow_gap"] == 1


def test_an_unknown_timing_mode_is_rejected(monkeypatch):
    import importlib
    with pytest.raises(ValueError):
        ue.select_ultrasonic("polling", FakeClock(), lambda: "poll")
    monkeypatch.setenv("CAR_SCRATCH_US_TIMING", "polling")
    from picar_server import __main__ as m
    m = importlib.reload(m)
    monkeypatch.setattr(m.web, "run_app", lambda app, **kw: None)
    with pytest.raises(SystemExit):
        m.main(["--hardware", "none"])


# -- errors and lifecycle ------------------------------------------------------------------------------------
@pytest.mark.parametrize("call", ["pull_up", "arm", "get", "events", "wait"])
def test_an_io_error_discards_with_a_possible_echo_and_rearms(call):
    b, us, line, clock = make()
    line.queue((D, echo_for_cm(60)))
    us.ping()
    clock.advance(0.05)
    line.queue((D, echo_for_cm(60)))
    line.fail.add(call)
    v = us.ping()
    if us.discards["io_error"]:
        assert v is None and us.lost_echo_cm == 0.0 and line.mode == "armed"
    clock.advance(0.05)
    line.queue((D, echo_for_cm(60)))
    v = us.ping()
    assert us.lost_echo_cm is None and isinstance(v, float)


def test_stop_during_a_wait_ends_the_ping():
    b, us, line, clock = make()
    line.queue((D, 0.4))
    line.on_wait = us.stop.set
    assert us.ping() is None


def test_close_releases_the_line_as_an_input():
    b, us, line, clock = make()
    b.close()
    assert line.closed and line.mode == "input"


def test_a_join_that_times_out_leaves_the_line_open():
    b, us, line, clock = make()
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    b._thread = t
    b._join_thread()                                     # waits 1 s, then gives up
    assert not line.closed and us.stop.is_set()
    release.set()
    t.join()


def test_a_backend_that_fails_after_selecting_edges_releases_the_line():
    clock = FakeClock()
    line = FakeEdgeLine(clock)

    class BadGPIO(FakeGPIO):
        def setup(self, pin, mode, initial=None):
            raise OSError("gpio setup failed")

    with pytest.raises(OSError):
        RealBackend({}, bus=FakeBus(), gpio=BadGPIO(clock), clock=clock, us_timing="edges",
                    open_edges=lambda clk, stop=None: ue.EdgeUltrasonic(line, clk, stop=stop))
    assert line.closed


# -- the switch to polling -----------------------------------------------------------------------------------
def break_floors(us, line, clock, n):
    for _ in range(n):
        line.queue((0.0003, echo_for_cm(50)))
        us.ping()
        clock.advance(0.05)


def test_the_switch_to_polling_waits_for_a_free_line():
    b, us, line, clock = make()
    break_floors(us, line, clock, ue.FLOOR_BREAKS_MAX - 1)
    line.queue(("cycle", 0.0003, 0.4), (D, echo_for_cm(100)), (D, echo_for_cm(100)))
    s = b.hub.read()                                     # the fifth break starts a long cycle
    assert us.broken and b.hub.us is us and s.echo_cm == 0.0
    while clock.now < line._busy_until + 0.05:
        clock.advance(0.04)
        b.hub.read()
    assert b.hub.us.timing == "poll" and line.closed


def test_the_batch_that_trips_the_switch_keeps_its_evidence():
    b, us, line, clock = make()
    break_floors(us, line, clock, ue.FLOOR_BREAKS_MAX - 1)
    line.queue((0.0003, echo_for_cm(50)), (D, echo_for_cm(100)), (D, echo_for_cm(100)))
    s = b.hub.read()
    assert b.hub.us.timing == "poll"
    assert s.echo_cm == 0.0 and s.echo_t is not None and s.dist_t is not None


# -- selection and start-up -----------------------------------------------------------------------------------
def test_selection_falls_back_only_when_the_kernel_or_module_cannot():
    clock = FakeClock()

    def poll():
        return "poll"

    def raising(err):
        def f(clk, stop=None):
            raise OSError(err, "x")
        return f

    for err in (errno.ENOENT, errno.EINVAL, errno.EOPNOTSUPP):
        assert ue.select_ultrasonic("auto", clock, poll, open_edges=raising(err)) == "poll"
    with pytest.raises(OSError):
        ue.select_ultrasonic("auto", clock, poll, open_edges=raising(errno.EBUSY))
    with pytest.raises(OSError):
        ue.select_ultrasonic("edges", clock, poll, open_edges=raising(errno.ENOENT))
    assert ue.select_ultrasonic("poll", clock, poll, open_edges=raising(errno.ENOENT)) == "poll"


def opener(line):
    return lambda name: ("/dev/gpiochip0", 5)


def test_start_up_probe_accepts_a_module_that_answers():
    clock = FakeClock()
    line = FakeEdgeLine(clock)
    line.default = (D, echo_for_cm(70))
    us = ue.open_edge_ultrasonic(clock, find=opener(line), line_cls=lambda off, chip: line)
    assert us.timing == "edges" and not line.closed and line.pings == ue.PROBE_TRIES
    assert not us.discards and not us.broken


def test_start_up_probe_rejects_a_module_that_ignores_pull_triggers_and_releases_the_line():
    clock = FakeClock()
    line = FakeEdgeLine(clock)
    line.pull_trigger = False
    with pytest.raises(OSError) as e:
        ue.open_edge_ultrasonic(clock, find=opener(line), line_cls=lambda off, chip: line)
    assert e.value.errno == errno.EOPNOTSUPP and line.closed


def test_start_up_probe_waits_out_a_busy_module():
    clock = FakeClock()
    line = FakeEdgeLine(clock)
    line._pulses = [(clock.now - 0.1, clock.now + 0.3)]
    line.default = (D, echo_for_cm(70))
    ue.open_edge_ultrasonic(clock, find=opener(line), line_cls=lambda off, chip: line)
    assert line.pings == ue.PROBE_TRIES


def test_the_timing_defaults_to_polling_until_validated(monkeypatch):
    import importlib
    monkeypatch.delenv("CAR_SCRATCH_US_TIMING", raising=False)
    from picar_server import __main__ as m
    m = importlib.reload(m)
    seen = {}
    monkeypatch.setattr(m.web, "run_app", lambda app, **kw: seen.setdefault("app", app))
    m.main(["--hardware", "none"])
    assert seen["app"]["settings"].us_timing == "poll"
    seen.clear()
    monkeypatch.setenv("CAR_SCRATCH_US_TIMING", "edges")
    m = importlib.reload(m)
    monkeypatch.setattr(m.web, "run_app", lambda app, **kw: seen.setdefault("app", app))
    m.main(["--hardware", "none"])
    assert seen["app"]["settings"].us_timing == "edges"


def test_status_reports_the_timing():
    from picar_server.controller import Controller
    from picar_server.session import Hub
    b, us, line, clock = make()
    hub = Hub(Controller(b, {}, shared=True), {}, hw={}, server_info={})
    assert hub.hw_info()["ultrasonic_timing"] == "edges"
    b.close()


def test_hub_stop_is_bounded_with_an_unresponsive_peer():
    from picar_server.controller import Controller
    from picar_server.session import Hub

    class Hung:
        async def close(self, code=1000, message=b""):
            await asyncio.sleep(10)

    class Sess:
        def __init__(self):
            self.ws = Hung()

        def dispose_sim(self):
            pass

    async def run():
        hub = Hub(Controller(None, {}, shared=True), {}, hw={}, server_info={})
        hub.sessions = {"a": Sess(), "b": Sess()}
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await hub.stop()
        return loop.time() - t0

    assert asyncio.run(run()) < 1.0


# -- round 2 of the implementation review ------------------------------------------------------------------
def test_activity_between_the_quiet_check_and_the_trigger_makes_the_ping_uncertain():
    # first reviewer: a pause after the quiet check; a noise-triggered answer comes and goes in it, and a 27 ms pulse
    # starts right where our echo would. Before, the noise was taken for our own trigger edges and the pulse for FAR.
    b, us, line, clock = make()
    line.queue((D, echo_for_cm(60)))
    us.ping()                                            # a normal ping first
    clock.advance(0.05)
    orig_quiet = us._wait_quiet

    def quiet_then_noise():
        ok = orig_quiet()
        n = clock.now
        line._pulses += [(n + 0.0003, n + 0.0006)]       # a short answer to a noise trigger
        line._pulses.sort()
        clock.gap_after(1, 0.004)                        # ... while the sensor thread pauses before triggering
        return ok

    def far_after_our_trigger(t0=None):
        line.pings += 1
        line._pulses += [(t0 + D, t0 + D + 0.027)]
        line._pulses.sort()

    us._wait_quiet = quiet_then_noise
    line._trigger = far_after_our_trigger
    v = us.ping()
    assert v is not FAR and us.lost_echo_cm == 0.0 and us.discards["pre_trigger"] == 1, (v, dict(us.discards))


def test_an_io_error_mid_cycle_never_hands_the_pin_to_polling_while_high():
    # first reviewer: an I/O error on a batch's only ping left no cycle state, and the hub switched to polling
    b, us, line, clock = make()
    line.queue((D, 0.027), (D, 0.027), (D, 0.027))
    b.hub.read()                                        # far: the next sample pings once
    clock.advance(0.04)
    us.broken = True                                    # as if the checks had just failed for the fifth time
    line.queue(("cycle", D, 0.4))
    fired = []

    def fail_once():
        if not fired:
            fired.append(1)
            line.fail.add("events")

    line.on_wait = fail_once
    b.hub.read()                                        # the cycle starts; reading its events fails
    line.on_wait = None
    assert us.discards["io_error"] == 1
    assert line._busy() and b.hub.us is us              # still the edge sensor while the module holds the line
    while clock.now < line._busy_until + 0.05:
        clock.advance(0.04)
        b.hub.read()
    assert b.hub.us.timing == "poll" and line.closed


def test_a_first_pulse_merged_into_a_later_rise_is_never_far():
    # first reviewer: a delayed IRQ thread merges the first rise and fall with the second rise
    for irq in (0.0003, 0.00038, 0.0005):
        b, us, line, clock = make(irq_thread=irq)
        line.queue(("multi", [(D, 0.0003), (D + 0.00036, 0.027)]))   # a 0.06 ms dip
        v = us.ping()
        assert v is not FAR and not us.busy_started and us.lost_echo_cm == 0.0, (irq, v, dict(us.discards))


def test_a_late_first_rise_breaks_the_floor():
    b, us, line, clock = make()
    line.queue((0.0006, echo_for_cm(50)))
    v = us.ping()
    assert v is None and us.lost_echo_cm == 0.0 and us.discards["late_rise"] == 1


def test_repeated_slow_triggers_fall_back_to_polling():
    b, us, line, clock = make(pull_s=0.0005)
    for _ in range(ue.FLOOR_BREAKS_MAX):
        line.queue((D, echo_for_cm(50)))
        us.ping()
        clock.advance(0.05)
    assert us.broken and us.discards["slow_trigger"] == ue.FLOOR_BREAKS_MAX


def test_discarded_pings_keep_their_events_for_the_log():
    b, us, line, clock = make()
    line.queue((0.0003, echo_for_cm(50)))
    us.ping()
    a = us.anomalies[-1]
    assert a["why"] == "early_rise" and a["events"][0][0] == "R" and a["ours"] == 2
