"""The fake kernel edge line behaves like the measurements it models."""
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
    while line.wait(until - line.clock.now):
        ev += line.events()
    return ev


def test_a_caught_echo_has_exact_edges():
    c = FakeClock()
    line = FakeEdgeLine(c)
    line.queue((0.0004, echo_for_cm(50)))
    t = trigger(line)
    ev = collect(line, t + 0.04)
    assert [e.rising for e in ev] == [True, False]
    assert abs(ev[0].t - (t + 0.0004)) < 1e-9 and abs(ev[1].t - ev[0].t - echo_for_cm(50)) < 1e-9
    assert [e.seqno for e in ev] == [1, 2]


def test_a_rise_before_arming_is_missed_and_the_line_reads_high():
    c = FakeClock()
    line = FakeEdgeLine(c)
    line.arm_latency = 0.0005
    line.queue((0.0004, echo_for_cm(50)))
    t = trigger(line)
    assert line.get() == 1
    ev = collect(line, t + 0.04)
    assert [e.rising for e in ev] == [False]


def test_a_short_pulse_inside_the_irq_thread_window_reads_as_two_falls_with_the_second_late():
    c = FakeClock()
    line = FakeEdgeLine(c)
    line.irq_thread = 0.00005
    line.queue((0.0004, 0.00002))
    t = trigger(line)
    ev = collect(line, t + 0.04)
    assert [e.rising for e in ev] == [False, False]
    assert ev[1].t - ev[0].t > 0.00002                  # the second timestamp is late: the unsafe direction


def test_long_cycle_ignores_triggers_and_its_fall_is_recorded():
    c = FakeClock()
    line = FakeEdgeLine(c)
    line.queue(("cycle", 0.0004, 0.4015))
    t = trigger(line)
    ev = collect(line, t + 0.5)
    assert [e.rising for e in ev] == [True, False] and abs(ev[1].t - (t + 0.4019)) < 1e-9


def test_dropped_events_leave_a_seqno_gap():
    c = FakeClock()
    line = FakeEdgeLine(c)
    line.queue(("multi", [(0.0004, 0.0003), (0.0009, 0.002)]))
    t = trigger(line)
    ev = collect(line, t + 0.0005)                      # the first rise
    line.drop_next = 1                                  # the first fall is lost
    ev += collect(line, t + 0.04)
    assert [(e.rising, e.seqno) for e in ev] == [(True, 1), (True, 3), (False, 4)]
