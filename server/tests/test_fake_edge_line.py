"""The fake kernel edge line behaves like the kernel and the measurements it models (gpiolib-cdev, BCM2835 edge flow:
interrupts not masked, one timestamp slot per line, one event per IRQ-thread run)."""
from fakes_hw import FakeClock, FakeEdgeLine, echo_for_cm


def trigger(line):
    line.output_low()
    line.set(1)
    line.clock.advance(0.00001)
    line.set(0)
    t_end = line.clock.now
    line.arm()
    return t_end


def collect(line, until):
    ev = []
    while line.clock.now < until and line.wait(until - line.clock.now):
        ev += line.events()
    return ev + line.events()


def shape(ev):
    return "".join("R" if e.rising else "F" for e in ev)


def test_a_caught_echo_has_exact_edges():
    line = FakeEdgeLine(FakeClock())
    line.queue((0.0004, echo_for_cm(50)))
    t = trigger(line)
    ev = collect(line, t + 0.04)
    assert shape(ev) == "RF"
    assert abs(ev[0].t - (t + 0.0004)) < 1e-9 and abs(ev[1].t - ev[0].t - echo_for_cm(50)) < 1e-9
    assert [e.seqno for e in ev] == [1, 2]


def test_a_rise_before_arming_is_missed_and_the_line_reads_high():
    line = FakeEdgeLine(FakeClock())
    line.arm_latency = 0.0005
    line.queue((0.0004, echo_for_cm(50)))
    t = trigger(line)
    assert line.get() == 1
    assert shape(collect(line, t + 0.04)) == "F"


def test_two_edges_within_the_irq_thread_latency_merge_into_one_event():
    line = FakeEdgeLine(FakeClock())
    line.irq_thread = 0.00005
    line.queue((0.0004, 0.00002))                        # a 20 us pulse
    t = trigger(line)
    ev = collect(line, t + 0.04)
    assert shape(ev) == "F" and abs(ev[0].t - (t + 0.00042)) < 1e-9   # the last stamp, the level after
    assert [e.seqno for e in ev] == [1]                  # no gap: the kernel never knew


def test_three_edges_merge_into_one_plausible_rise():
    line = FakeEdgeLine(FakeClock())
    line.irq_thread = 0.00005
    line.queue(("multi", [(0.0004, 0.00002), (0.00043, echo_for_cm(80))]))   # glitch, 10 us dip, echo
    t = trigger(line)
    ev = collect(line, t + 0.04)
    assert shape(ev) == "RF" and abs(ev[0].t - (t + 0.00043)) < 1e-9   # the glitch has vanished


def test_late_stamps():
    line = FakeEdgeLine(FakeClock())
    line.fall_late = 0.0001
    line.queue((0.0004, 0.001))
    t = trigger(line)
    ev = collect(line, t + 0.04)
    assert abs(ev[1].t - ev[0].t - 0.0011) < 1e-9


def test_long_cycle_fall_is_recorded():
    line = FakeEdgeLine(FakeClock())
    line.queue(("cycle", 0.0004, 0.4015))
    t = trigger(line)
    ev = collect(line, t + 0.5)
    assert shape(ev) == "RF" and abs(ev[1].t - (t + 0.4019)) < 1e-9


def test_dropped_events_leave_a_seqno_gap():
    line = FakeEdgeLine(FakeClock())
    line.queue(("multi", [(0.0004, 0.0003), (0.0009, 0.002)]))
    t = trigger(line)
    line.clock.now = t + 0.0005
    ev = line.events()                                  # the first rise
    line.drop_next = 1                                  # the first fall is lost
    ev += collect(line, t + 0.04)
    assert [(e.rising, e.seqno) for e in ev] == [(True, 1), (True, 3), (False, 4)]


def test_fifo_overflow_drops_the_oldest():
    line = FakeEdgeLine(FakeClock())
    line.buffer = 2
    line.queue(("multi", [(0.0004, 0.0003), (0.0009, 0.002)]))
    t = trigger(line)
    line.clock.advance(0.01)                            # nobody reads meanwhile
    ev = line.events()
    assert [(e.rising, e.seqno) for e in ev] == [(True, 3), (False, 4)]


def test_the_pending_thread_runs_on_disarm():
    line = FakeEdgeLine(FakeClock())
    line.irq_thread = 0.0005
    line.queue((0.0004, 0.0001))
    t = trigger(line)
    line.clock.advance(0.00055)                         # both edges seen, the thread not run yet
    line.output_low()
    assert shape(line.events()) == "F"


def test_driving_low_while_the_module_is_high_is_contention():
    line = FakeEdgeLine(FakeClock())
    line.queue(("multi", [(0.0004, 0.0003), (0.0012, 0.003)]))
    t = trigger(line)
    line.clock.now = t + 0.0008                         # between the pulses
    line.output_low()                                   # the second pulse rises while we drive low
    line.clock.advance(0.002)
    line.arm()
    assert line.contention == 1


def test_triggers_are_ignored_while_the_module_answers_and_pulses_persist():
    line = FakeEdgeLine(FakeClock())
    line.queue(("cycle", 0.0004, 0.2), (0.0004, echo_for_cm(30)))
    t = trigger(line)
    line.clock.now = t + 0.1
    trigger(line)                                       # mid-cycle: ignored, the script entry stays
    assert line.pings == 2 and len(line.script) == 1 and line.get() == 1


def test_wait_rounds_up_to_whole_milliseconds_like_the_kernel_poll():
    line = FakeEdgeLine(FakeClock())
    line.arm()
    t = line.clock.now
    assert line.wait(0.0003) is False
    assert abs(line.clock.now - (t + 0.001)) < 1e-9
