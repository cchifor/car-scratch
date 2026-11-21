"""Ultrasonic, PCF8591 and line-follower behaviour (scripted echoes, fake clock: no real waiting)."""
import logging

from fakes_hw import FakeBus, FakeClock, FakeGPIO
from picar_server.hal.real import RealBackend
from picar_server.hal.sensors import BUSY


def make(cal=None):
    clock = FakeClock()
    bus, gpio = FakeBus(), FakeGPIO(clock)
    b = RealBackend(cal or {}, bus=bus, gpio=gpio, clock=clock)
    return b, bus, gpio, clock


def test_ok_reading_is_median_of_three_when_unknown():
    b, bus, gpio, _ = make()
    gpio.queue(gpio.echo_cm(50), gpio.echo_cm(200), gpio.echo_cm(60))
    s = b.hub.read()
    assert gpio.pings == 3
    assert s.dist_state == "ok" and abs(s.dist_cm - 60) < 0.5


def test_ping_sequence_on_the_wire():
    b, bus, gpio, _ = make()
    gpio.default = gpio.echo_cm(100)
    gpio.outputs.clear()
    b.hub.us.ping()
    assert [(p, v) for p, v in gpio.outputs if p == 5] == [(5, 0), (5, 1), (5, 0)]   # low, high, low


def test_ping_count_follows_near_hint_and_forward_motion():
    b, bus, gpio, _ = make()
    gpio.default = gpio.echo_cm(100)

    def pings():
        p = gpio.pings
        b.hub.read()
        return gpio.pings - p

    assert pings() == 3                                  # nothing known yet -> 3
    assert pings() == 1                                  # 100 cm >= near hint 60, stationary
    b.set_near_hint(150)
    assert pings() == 3                                  # 100 cm < hint
    b.set_near_hint(60)
    b.set_motors(1000, 1000)                             # forward -> always 3
    assert pings() == 3
    b.set_motors(-500, -500)                             # backward does not count as forward
    assert pings() == 1
    b.stop_motors()
    assert pings() == 1


def test_far_when_echo_ends_between_12_and_40ms():
    b, bus, gpio, _ = make()
    gpio.default = (0.0005, 0.015)
    s = b.hub.read()
    assert s.dist_state == "far" and s.dist_cm is None
    p = gpio.pings
    b.hub.read()
    assert gpio.pings - p == 1                           # far and stationary: one ping


def test_echo_still_high_at_40ms_is_busy_never_far():
    b, bus, gpio, _ = make()
    gpio.default = (0.0005, 0.100)
    assert b.hub.us.ping() is BUSY
    s = b.hub.read()
    assert s.dist_state == "none" and s.dist_cm is None


def _read_for(b, clock, seconds, period=0.04):
    """Samples at the sensor thread's cadence (40 ms) for `seconds` of fake time."""
    states, t_end = [], clock.now + seconds
    while clock.now < t_end:
        t0 = clock.now
        states.append(b.hub.read().dist_state)
        clock.advance(max(0.0, period - (clock.now - t0)))
    return states


def test_module_long_cycle_is_busy_not_a_fault():
    # Measured on the car: against a weak reflector about one ping in 15 makes the module hold the line high for
    # 401.5 ms (instead of its usual 27 ms no-echo timeout), ignoring triggers. That is a healthy module.
    b, bus, gpio, clock = make()
    far = (0.0003, 0.027)
    gpio.default = far
    assert _read_for(b, clock, 0.2)[-1] == "far"
    gpio.queue(gpio.long_cycle())
    states = _read_for(b, clock, 0.6)
    assert "fault" not in states
    assert "none" in states and states[-1] == "far"       # no reading during the cycle, then normal again
    assert gpio.contention == 0                           # never drove the line low against the module


def test_busy_module_is_not_retriggered():
    b, bus, gpio, clock = make()
    gpio.default = (0.0003, 0.027)
    gpio.queue(gpio.long_cycle())
    b.hub.read()
    pings, writes = gpio.pings, len(gpio.writes_to(5))
    clock.advance(0.1)
    assert b.hub.us.ping() is BUSY
    assert gpio.pings == pings and len(gpio.writes_to(5)) == writes


def test_line_high_beyond_the_module_cycle_is_a_fault():
    b, bus, gpio, clock = make()
    gpio.default = (0.0003, 0.027)
    gpio.queue(gpio.long_cycle(width=5.0))               # stuck high: far longer than any module cycle
    states = _read_for(b, clock, 1.0)
    assert states[-1] == "fault"
    assert "fault" not in states[:10]                     # not before the module's cycle could have ended
    assert gpio.contention == 0                           # a stuck-high line is never driven against either


def test_fake_long_cycle_rises_only_after_its_delay():
    b, bus, gpio, clock = make()
    gpio.queue(gpio.long_cycle(delay=0.02))
    gpio.setup(5, gpio.IN)
    assert gpio.input(5) == 0
    clock.advance(0.01)
    assert gpio.input(5) == 0
    clock.advance(0.02)
    assert gpio.input(5) == 1


def test_after_a_busy_cycle_the_next_sample_takes_three_pings():
    # Review finding: a stationary car whose last reading was far must not resume with a single ping after the
    # blind window; an obstacle may have appeared and one missed echo would publish a false "far".
    b, bus, gpio, clock = make()
    far = (0.0003, 0.027)
    gpio.default = far
    _read_for(b, clock, 0.2)
    gpio.queue(gpio.long_cycle())
    while b.hub.read().dist_state != "none":
        clock.advance(0.04)
    while gpio.input(5) == 1:
        clock.advance(0.04)
    gpio.queue(far, gpio.echo_cm(10), gpio.echo_cm(10))     # obstacle now at 10 cm; the first ping misses it
    s = b.hub.read()
    assert s.dist_state == "ok" and abs(s.dist_cm - 10) < 0.5


def test_two_valid_pings_never_average_with_far():
    # Review finding: median of [FAR, 10 cm] was (999 + 10) / 2 = 505 cm, "ok": a close echo reported far away.
    b, bus, gpio, clock = make()
    far = (0.0003, 0.027)
    gpio.queue(None, far, gpio.echo_cm(10))
    s = b.hub.read()
    assert s.dist_state == "ok" and abs(s.dist_cm - 10) < 0.5


def test_valid_pings_then_busy_never_average_with_far():
    b, bus, gpio, clock = make()
    far = (0.0003, 0.027)
    gpio.queue(far, gpio.echo_cm(10), gpio.long_cycle())
    s = b.hub.read()
    assert s.dist_state == "ok" and abs(s.dist_cm - 10) < 0.5


def test_module_that_only_answers_with_long_cycles_becomes_a_fault():
    b, bus, gpio, clock = make()
    gpio.default = gpio.long_cycle()                     # every ping: a long cycle, never a reading
    states = _read_for(b, clock, 2.5)
    assert states[-1] == "fault"
    assert gpio.contention == 0


def test_none_samples_then_fault_after_three_consecutive():
    b, bus, gpio, _ = make()
    gpio.default = None
    states = [b.hub.read().dist_state for _ in range(4)]
    assert states == ["none", "none", "fault", "fault"]
    assert b.hub.read().dist_cm is None
    gpio.default = gpio.echo_cm(80)
    s = b.hub.read()
    assert s.dist_state == "ok" and abs(s.dist_cm - 80) < 0.5     # one valid sample clears the fault
    gpio.default = None
    assert b.hub.read().dist_state == "none"             # the count restarted


def test_no_answer_waits_30ms_for_echo_start():
    b, bus, gpio, clock = make()
    gpio.default = None
    t0 = clock.now
    assert b.hub.us.ping() is None
    assert 0.030 <= clock.now - t0 < 0.040


def test_single_miss_among_three_still_answers():
    b, bus, gpio, _ = make()
    gpio.queue(None, gpio.echo_cm(40), gpio.echo_cm(42))
    s = b.hub.read()
    assert s.dist_state == "ok" and 40 <= s.dist_cm <= 42


def test_ping_with_busy_wait_gap_over_1ms_is_discarded():
    b, bus, gpio, clock = make()
    gpio.queue(gpio.echo_cm(100), gpio.echo_cm(50), gpio.echo_cm(52))
    clock.gap_after(60, 0.002)                           # stall while the first ping's echo is high
    s = b.hub.read()
    assert s.dist_state == "ok" and abs(s.dist_cm - 50) < 0.3     # the lower of the two undisturbed pings


def test_gap_before_the_echo_starts_is_tolerated():
    # A late-seen rising edge can only shorten the measured echo (reads closer), so the ping is kept.
    b, bus, gpio, clock = make()
    gpio.default = gpio.echo_cm(100)
    clock.gap_after(5, 0.0003)                           # stall inside the wait-for-echo loop
    v = b.hub.us.ping()
    assert v is not None and v <= 100.5


def test_all_pings_discarded_gives_none_sample():
    b, bus, gpio, clock = make()
    gpio.default = gpio.echo_cm(100)
    clock.gap_after(60, 0.002)                           # stall while the echo is high: timing untrustworthy
    assert b.hub.us.ping() is None


def test_pcf8591_sequence_and_values():
    b, bus, gpio, _ = make()
    gpio.default = gpio.echo_cm(100)
    bus.pcf_values = [11, 22, 33]
    bus.ops.clear()
    s = b.hub.read()
    pcf = [o for o in bus.ops if o[1] == 0x48]
    assert pcf == [("wb", 0x48, 0x40), ("rb", 0x48), ("rb", 0x48),
                   ("wb", 0x48, 0x41), ("rb", 0x48), ("rb", 0x48),
                   ("wb", 0x48, 0x42), ("rb", 0x48), ("rb", 0x48)]
    assert s.light_raw == [11, 22, 33]
    assert b.capabilities()["light"] is True


def test_pcf8591_missing_gives_none():
    b, bus, gpio, _ = make()
    gpio.default = gpio.echo_cm(100)
    bus.missing.add(0x48)
    s = b.hub.read()
    assert s.light_raw is None and b.capabilities()["light"] is False


def test_line_follower_disabled_by_default_is_never_probed():
    b, bus, gpio, clock = make()
    gpio.default = gpio.echo_cm(100)
    bus.line_bytes = [0, 1] * 5
    b.hub.probe()
    s = b.hub.read()
    assert s.line_raw is None and b.capabilities()["line"] is False
    assert not any(o[0] == "rblk" for o in bus.ops)


def test_line_follower_decodes_big_endian_pairs():
    b, bus, gpio, _ = make({"line_enabled": True})
    gpio.default = gpio.echo_cm(100)
    bus.line_bytes = [0x01, 0x2C, 0, 5, 0, 0, 0x03, 0xFF, 0, 1]
    s = b.hub.read()
    assert s.line_raw == [300, 5, 0, 1023, 1]
    assert b.capabilities()["line"] is True
    assert ("rblk", 0x11, 0, 10) in bus.ops


def test_line_follower_absent_backoff_2s_then_10s_and_logs_state_changes_only(caplog):
    b, bus, gpio, clock = make({"line_enabled": True})
    gpio.default = gpio.echo_cm(100)
    caplog.set_level(logging.INFO, logger="picar.sensors")

    def attempts():
        return sum(1 for o in bus.ops if o[0] == "rblk")

    assert b.hub.read().line_raw is None
    n = attempts()
    assert n >= 1                                        # probed immediately, absent
    clock.advance(1.0)
    assert b.hub.read().line_raw is None
    assert attempts() == n                               # within the 2 s backoff: not probed again
    gaps = []
    for _ in range(4):                                   # delays between probes: 2, 4, 8, 10
        t_fail = b.hub._line_next_probe
        clock.advance(max(0, t_fail - clock.now) + 0.01)
        before = attempts()
        b.hub.read()
        assert attempts() == before + 1
        gaps.append(round(b.hub._line_next_probe - clock.now))
    assert gaps == [4, 8, 10, 10]
    assert [r.getMessage() for r in caplog.records if "line follower" in r.getMessage()] == \
        ["line follower absent"]                         # logged once despite many failed probes
    bus.line_bytes = [0, 1, 0, 2, 0, 3, 0, 4, 0, 5]      # cable fixed
    clock.advance(11)
    s = b.hub.read()
    assert s.line_raw == [1, 2, 3, 4, 5] and b.capabilities()["line"] is True
    assert [r.getMessage() for r in caplog.records if "line follower" in r.getMessage()][-1] == \
        "line follower present"


def test_line_loss_at_runtime_reports_none_and_capability_drops():
    b, bus, gpio, clock = make({"line_enabled": True})
    gpio.default = gpio.echo_cm(100)
    bus.line_bytes = [0, 1] * 5
    assert b.hub.read().line_raw is not None
    bus.line_bytes = None
    assert b.hub.read().line_raw is None
    assert b.capabilities()["line"] is False
