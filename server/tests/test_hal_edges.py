"""Edge-timed ultrasonic (decision D9): every rule of the plan's table, on the fake kernel line.

The invariants: a reading is never farther than the first pulse; "nothing there" (FAR, or a cleanly started long
cycle, which give D7's clear_t) only when no pulse can have been missed; a ping that may have missed one records
lost_echo_cm = 0; the line is never driven while the module holds it high."""
import errno
import threading

import pytest

from picar_server.hal import ultrasonic_edges as ue
from picar_server.hal.real import RealBackend
from picar_server.hal.sensors import BUSY, FAR

from fakes_hw import FakeBus, FakeClock, FakeEdgeLine, FakeGPIO, echo_for_cm

D = 0.0004                                              # the module's rise delay (measured 383-417 us)


def make(**line_kw):
    clock = FakeClock()
    line = FakeEdgeLine(clock)
    for k, v in line_kw.items():
        setattr(line, k, v)
    b = RealBackend({}, bus=FakeBus(), gpio=FakeGPIO(clock), clock=clock, us_timing="edges",
                    open_edges=lambda clk, stop=None: ue.EdgeUltrasonic(line, clk, stop=stop))
    return b, b.hub.us, line, clock


def ping(us):
    v = us.ping()
    return v, us.lost_echo_cm, us.busy_started


# -- clean pings -----------------------------------------------------------------------------------------------
def test_selected_and_reported():
    b, us, line, clock = make()
    assert us.timing == "edges" and b.ultrasonic_timing == "edges"


def test_a_caught_echo_reads_at_most_the_allowance_closer():
    b, us, line, clock = make()
    line.queue((D, echo_for_cm(50)))
    v, lost, _ = ping(us)
    assert lost is None and 50 - ue.FALL_LAT_S * ue.CM_PER_S - 0.01 <= v <= 50


def test_far_and_its_clear_time():
    b, us, line, clock = make()
    line.queue((D, 0.027), (D, 0.027), (D, 0.027))
    s = b.hub.read()
    assert s.dist_state == "far" and s.clear_t is not None and s.echo_cm is None


def test_a_long_cycle_is_busy_then_followed_to_its_fall_without_driving_the_line():
    b, us, line, clock = make()
    line.queue(("cycle", D, 0.4015))
    v, lost, started = ping(us)
    assert v is BUSY and started and lost is None
    pings = line.pings
    n = 0
    while us._busy_since is not None or n == 0:
        clock.advance(0.04)
        v = us.ping()
        n += 1
        if us._busy_since is not None:
            assert v is BUSY and not us.busy_started
        assert n < 20
    assert line.pings == pings + 1 and line.contention == 0   # one new trigger, only after the fall
    assert v is not BUSY


def test_a_line_found_high_is_never_driven_and_never_clear():
    b, us, line, clock = make()
    line._pulses = [(clock.now - 0.1, clock.now + 0.2)]       # the module was already answering at start-up
    v, lost, started = ping(us)
    assert v is BUSY and not started and line.contention == 0 and us.diag["found_high"] == 1
    while clock.now < line._pulses[0][1] + 0.05:
        clock.advance(0.04)
        us.ping()
    assert line.contention == 0


# -- arming window ---------------------------------------------------------------------------------------------
def test_a_rise_before_arming_gives_a_reading_from_the_arm_time():
    b, us, line, clock = make(arm_latency=0.00042)
    line.queue((D, echo_for_cm(80)))
    v, lost, _ = ping(us)
    assert lost is None and us.blind[-1] <= ue.ARM_CLEAN_S
    assert 80 - 4.0 <= v <= 80


def test_a_rise_between_arming_and_the_level_read_is_the_rise_not_an_anomaly():
    # edges are on from arm_at of the arm call; the rise lands after that but before get(): high at arm AND an R
    b, us, line, clock = make(arm_latency=D - 0.00001)
    line.queue((D, echo_for_cm(80)))
    v, lost, _ = ping(us)
    assert lost is None and not us.discards, us.discards
    assert 80 - 2.0 <= v <= 80


def test_a_late_arm_discards_and_records_a_possible_echo_even_before_far():
    b, us, line, clock = make(arm_latency=0.0006)
    line.queue((D, 0.027))
    v, lost, started = ping(us)
    assert v is None and lost == 0.0 and not started and us.discards["late_arm"] == 1


@pytest.mark.parametrize("arm_us", range(0, 1000, 10))
def test_a_hidden_first_pulse_never_yields_a_farther_reading_or_nothing_there(arm_us):
    # a 0.15 ms glitch (2.6 cm) then the real echo at 110 cm, or a 27 ms "far", or a long cycle
    glitch = (D, 0.00015)
    for second in ((D + 0.0003, echo_for_cm(110)), (D + 0.0003, 0.027), (D + 0.0003, 0.4015)):
        b, us, line, clock = make(arm_latency=arm_us / 1e6)
        line.queue(("multi", [glitch, second]))
        v, lost, started = ping(us)
        glitch_cm = glitch[1] * ue.CM_PER_S
        if lost is None:
            assert v is not None and v is not BUSY and v <= glitch_cm, (arm_us, second, v)
            assert not started
        else:
            assert lost == 0.0 and not started, (arm_us, second)


# -- never farther ---------------------------------------------------------------------------------------------
WIDTHS = [0.0002, 0.0005, 0.001, 0.003, 0.0119, 0.01199, 0.012, 0.01201, 0.0121, 0.02, 0.027]


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("arm_us", [0, 100, 200, 300, 350, 380, 400, 420, 440, 460, 600, 900])
@pytest.mark.parametrize("fall_late_us", [0, 50, 100])
def test_never_farther_than_the_first_pulse_and_far_only_beyond_12ms(width, arm_us, fall_late_us):
    b, us, line, clock = make(arm_latency=arm_us / 1e6, fall_late=fall_late_us / 1e6)
    line.queue((D, width))
    v, lost, _ = ping(us)
    true_cm = width * ue.CM_PER_S
    if v is FAR:
        assert width > 0.012 - 1e-9                     # (exactly 12 ms with the full allowance: a float tie)
    elif v is not None and v is not BUSY:
        assert v <= true_cm + 1e-9, (v, true_cm)


# -- kernel quirks ---------------------------------------------------------------------------------------------
def test_a_pulse_too_short_for_the_irq_thread_reads_out_of_order_and_is_discarded():
    b, us, line, clock = make(irq_thread=0.00005)
    line.queue(("multi", [(D, 0.00002), (D + 0.0002, echo_for_cm(100))]))
    v, lost, started = ping(us)
    assert v is None and lost == 0.0 and not started
    assert us.discards["edges"] + us.discards["short_pulse"] == 1


def test_a_lost_event_is_a_sequence_gap():
    b, us, line, clock = make()
    line.queue(("multi", [(D, 0.0003), (D + 0.0005, echo_for_cm(100))]))
    us.ping()                                            # arms; nothing queued yet... (first ping is real)
    line.queue(("multi", [(D, 0.0003), (D + 0.0005, echo_for_cm(100))]))
    clock.advance(0.05)
    line.drop_next = 1
    v, lost, _ = ping(us)
    assert v is None and lost == 0.0 and us.discards["seq_gap"] + us.discards["edges"] >= 1


def test_an_early_rise_breaks_the_floor_and_five_switch_the_hub_to_polling():
    b, us, line, clock = make()
    for _ in range(ue.FLOOR_BREAKS_MAX):
        line.queue((0.0002, echo_for_cm(50)))
        v, lost, _ = ping(us)
        assert v is None and lost == 0.0
        clock.advance(0.05)
    assert us.broken and us.discards["early_rise"] == ue.FLOOR_BREAKS_MAX
    b.hub.read()
    assert b.hub.us.timing == "poll" and line.closed


def test_a_short_pulse_breaks_the_floor():
    b, us, line, clock = make()
    line.queue((D, 0.00005))
    v, lost, _ = ping(us)
    assert v is None and lost == 0.0 and us.discards["short_pulse"] == 1


def test_a_valid_ping_resets_the_floor_count():
    b, us, line, clock = make()
    line.queue((0.0002, echo_for_cm(50)), (D, echo_for_cm(50)))
    ping(us)
    clock.advance(0.05)
    ping(us)
    assert us.floor_breaks == 0 and not us.broken


def test_a_lost_fall_of_a_cycle_makes_the_next_ping_unwatched():
    b, us, line, clock = make()
    line.queue(("cycle", D, 0.2))
    assert us.ping() is BUSY
    line.drop_next = 1                                   # the cycle's fall never reaches us
    clock.advance(0.25)
    line.queue((D, echo_for_cm(60)))
    v, lost, _ = ping(us)
    assert us.diag["lost_fall"] == 1 and v is None and lost == 0.0 and us.discards["unwatched"] == 1


def test_an_io_error_discards_with_a_possible_echo():
    b, us, line, clock = make()
    line.queue((D, echo_for_cm(60)))
    line.fail.add("get")
    v, lost, _ = ping(us)
    assert v is None and lost == 0.0 and us.discards["io_error"] == 1
    clock.advance(0.05)
    line.queue((D, echo_for_cm(60)))
    v, lost, _ = ping(us)
    assert lost is None and v is not None


# -- the double pulse --------------------------------------------------------------------------------------
def test_the_double_pulse_reads_the_first_pulse_and_leaves_the_line_free():
    b, us, line, clock = make()
    line.queue(("multi", [(D, 0.0003), (D + 0.00045, echo_for_cm(110))]))
    v, lost, _ = ping(us)
    assert v <= 0.0003 * ue.CM_PER_S and us.diag["multi"] == 1
    assert abs(us.second_widths[-1] - echo_for_cm(110)) < 1e-6
    clock.advance(0.03)
    line.queue((D, echo_for_cm(60)))
    v, lost, _ = ping(us)
    assert lost is None and 57 < v <= 60 and line.contention == 0 and us.diag["found_high"] == 0


# -- lifecycle ---------------------------------------------------------------------------------------------
def test_stop_ends_a_wait_at_once():
    b, us, line, clock = make()
    line.queue((D, 0.4))                                  # a long pulse
    us.stop.set()
    assert us.ping() is None


def test_close_releases_the_line_as_an_input():
    b, us, line, clock = make()
    b.close()
    assert line.closed and line.mode == "input"


def test_selection_falls_back_only_when_the_kernel_lacks_edges():
    clock = FakeClock()

    def poll():
        return "poll"

    def raising(err):
        def f(clk, stop=None):
            raise OSError(err, "x")
        return f

    assert ue.select_ultrasonic("auto", clock, poll, open_edges=raising(errno.ENOENT)) == "poll"
    assert ue.select_ultrasonic("auto", clock, poll, open_edges=raising(errno.EINVAL)) == "poll"
    with pytest.raises(OSError):
        ue.select_ultrasonic("auto", clock, poll, open_edges=raising(errno.EBUSY))
    with pytest.raises(OSError):
        ue.select_ultrasonic("edges", clock, poll, open_edges=raising(errno.ENOENT))
    assert ue.select_ultrasonic("poll", clock, poll, open_edges=raising(errno.ENOENT)) == "poll"


def test_open_edge_ultrasonic_probes_edges_and_releases_on_failure():
    clock = FakeClock()
    closed = []

    class Line:
        def __init__(self, offset, chip):
            self.offset = offset

        def arm(self):
            raise OSError(errno.EINVAL, "no edges")

        def input(self):
            pass

        def close(self):
            closed.append(True)

    with pytest.raises(OSError):
        ue.open_edge_ultrasonic(clock, find=lambda name: ("/dev/gpiochip0", 5), line_cls=Line)
    assert closed == [True]


def test_status_reports_the_timing():
    from picar_server.controller import Controller
    from picar_server.session import Hub
    b, us, line, clock = make()
    car = Controller(b, {}, shared=True)
    hub = Hub(car, {}, hw={}, server_info={})
    assert hub.hw_info()["ultrasonic_timing"] == "edges"
    b.close()


def test_the_stop_event_is_the_backends():
    b, us, line, clock = make()
    assert isinstance(us.stop, threading.Event)
    b._join_thread()
    assert us.stop.is_set() and line.closed
