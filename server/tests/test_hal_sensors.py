"""Ultrasonic, PCF8591 and line-follower behaviour (scripted echoes, fake clock: no real waiting)."""
import logging

import pytest

from fakes_hw import FakeBus, FakeClock, FakeGPIO
from picar_server.hal.real import RealBackend
from picar_server.hal.sensors import BUSY, FAR


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


def test_far_sample_is_a_no_echo_observation():
    b, bus, gpio, clock = make()
    gpio.default = (0.0003, 0.027)
    s = b.hub.read()
    assert s.dist_state == "far" and s.clear_t is not None and s.echo_cm is None
    assert s.clear_t <= s.t


def test_only_the_start_of_a_long_cycle_is_a_no_echo_observation():
    b, bus, gpio, clock = make()
    gpio.default = (0.0003, 0.027)
    b.hub.read()
    gpio.queue(gpio.long_cycle())
    clock.advance(0.04)
    s = b.hub.read()
    assert s.dist_state == "none" and s.clear_t is not None      # nothing heard for 40 ms: the cycle began
    clock.advance(0.1)
    s = b.hub.read()
    assert s.dist_state == "none" and s.clear_t is None          # still busy: no new observation


def test_any_echo_heard_cancels_the_no_echo_observation():
    b, bus, gpio, clock = make()
    far = (0.0003, 0.027)
    gpio.queue(far, gpio.echo_cm(50), gpio.long_cycle())         # far, an echo at 50 cm, then the module goes busy
    s = b.hub.read()
    assert s.clear_t is None and abs(s.echo_cm - 50) < 0.5
    gpio.queue(gpio.echo_cm(40))
    clock.advance(1.0)


def test_no_echo_time_is_the_ping_not_the_detection():
    b, bus, gpio, clock = make()
    gpio.default = (0.0003, 0.027)
    s = b.hub.read()
    assert s.t - s.clear_t >= 0.027                              # far: the ping, not the end of the 27 ms pulse
    gpio.queue(gpio.long_cycle())
    clock.advance(0.04)
    s = b.hub.read()
    assert s.clear_t is not None and s.t - s.clear_t >= 0.040    # long cycle: its ping, not the 40 ms detection


def test_a_discarded_near_echo_is_still_an_echo():
    # Review finding: a scheduling gap during a 50 cm echo drops the distance, but not the fact that it was heard.
    b, bus, gpio, clock = make()
    gpio.queue(gpio.echo_cm(50), (0.0003, 0.027), gpio.long_cycle())
    clock.gap_after(60, 0.002)
    s = b.hub.read()
    assert s.clear_t is None and s.echo_cm is not None and s.echo_cm <= 50.5


@pytest.mark.parametrize("gap_s", [0.004, 0.020])
def test_a_timing_gap_anywhere_never_hides_a_near_echo(gap_s):
    # Fourth review: a stall between the pin read and its timestamp hid a 50 cm echo or made it read ~80 cm.
    # Every stall position through the 50 cm ping: the echo is measured, or kept as an echo no farther than it was.
    for at in range(1, 200):                             # every position, odd and even
        b, bus, gpio, clock = make()
        gpio.queue(gpio.echo_cm(50), (0.0003, 0.027), gpio.long_cycle())
        clock.gap_after(at, gap_s)
        s = b.hub.read()
        assert s.echo_cm is not None and s.echo_cm <= 50.5, (at, s)
        assert s.clear_t is None, (at, s)


@pytest.mark.parametrize("gap_s", [0.004, 0.2])
def test_a_timing_gap_anywhere_never_makes_the_no_echo_later_than_the_ping(gap_s):
    # Fourth review: a stall at the rising edge made clear_t 200 ms late, stretching the D7 window.
    for at in range(1, 120):
        b, bus, gpio, clock = make()
        gpio.queue(gpio.long_cycle(width=5.0))
        clock.gap_after(at, gap_s)
        s = b.hub.read()
        if s.clear_t is not None:
            assert s.clear_t <= gpio._busy_from, (at, s.clear_t, gpio._busy_from)


def test_a_far_answer_survives_a_timing_gap_after_12_ms():
    # Measured on the car: good "far" answers were thrown away when the timing loop was interrupted. Once the line
    # has been seen high past 12 ms no echo can have ended within range, so the answer is certain.
    b, bus, gpio, clock = make()
    gpio.default = (0.0003, 0.027)
    clock.gap_after(900, 0.004)                          # about 17 ms into the pulse
    assert b.hub.us.ping() == FAR
    assert b.hub.us.ping_start is not None


def test_a_line_that_dips_and_rises_again_inside_a_gap_is_never_far():
    # Sixth review: a 2 ms echo (~34 cm) ended during a stall and the line rose again before polling resumed;
    # the later high past 12 ms made it "far". Only highs seen before the first stall count.
    b, bus, gpio, clock = make()
    gpio.default = gpio.multi((0.0003, 0.0020), (0.0040, 0.0233))
    clock.gap_after(65, 0.0035)                          # a stall from ~1 ms to ~4.5 ms into the echo
    assert b.hub.us.ping() is None
    assert b.hub.us.lost_echo_cm is not None and b.hub.us.lost_echo_cm <= 34.5
    s = b.hub.read()
    assert s.dist_state != "far" or s.clear_t is None


HIDDEN_CM = 0.0020 * 34300 / 2                          # a 2 ms echo: ~34.3 cm
PATTERNS = {
    "echo only": [(0.0003, 0.0020)],
    "echo, then high to 27 ms": [(0.0003, 0.0020), (0.0040, 0.0233)],
    "echo, then a long cycle": [(0.0003, 0.0020), (0.0040, 0.4015)],
}


@pytest.mark.parametrize("pattern", sorted(PATTERNS))
@pytest.mark.parametrize("gap_s", [0.0035, 0.0045, 0.020])
def test_a_hidden_echo_always_surfaces_as_an_echo_no_farther_than_it_was(pattern, gap_s):
    # Seventh review: stalls before the first read, or ending in a long cycle, hid a 34 cm echo. Property over
    # every stall position: the echo is measured, or kept as an echo no farther than it was; never "nothing there".
    for at in range(1, 160):
        b, bus, gpio, clock = make()
        gpio.queue(gpio.multi(*PATTERNS[pattern]), (0.0003, 0.027), (0.0003, 0.027))
        clock.gap_after(at, gap_s)
        s = b.hub.read()
        heard = [c for c in (s.echo_cm, s.dist_cm if s.dist_state == "ok" else None) if c is not None]
        assert heard and min(heard) <= HIDDEN_CM + 0.5, (pattern, gap_s, at, s)
        assert s.clear_t is None, (pattern, gap_s, at, s)


def test_echo_time_is_the_start_of_the_samples_pings():
    b, bus, gpio, clock = make()
    gpio.queue(gpio.echo_cm(50), gpio.echo_cm(50), gpio.echo_cm(50))
    s = b.hub.read()
    assert s.echo_t is not None and s.t - s.echo_t >= 3 * 0.0029


def test_an_echo_that_ends_during_a_timing_gap_is_discarded_and_kept_as_a_possible_echo():
    b, bus, gpio, clock = make()
    gpio.default = gpio.echo_cm(86)                      # a 5 ms echo
    clock.gap_after(200, 0.004)                          # a stall from ~3.6 ms to ~7.6 ms: the echo ends unseen
    assert b.hub.us.ping() is None
    assert b.hub.us.lost_echo_cm is not None and b.hub.us.lost_echo_cm <= 86.5
    assert b.hub.us.discards["gap"] == 1


def test_discard_reasons_are_counted_and_logged_once_a_minute(caplog):
    b, bus, gpio, clock = make()
    caplog.set_level(logging.INFO, logger="picar.sensors")
    gpio.default = None                                  # no answer
    b.hub.read()
    assert b.hub.us.discards["no_rise"] == 3
    clock.advance(61.0)
    gpio.default = (0.0003, 0.027)
    b.hub.read()
    msgs = [r.getMessage() for r in caplog.records if "discarded" in r.getMessage()]
    assert msgs and "no_rise 3" in msgs[-1]
    assert sum(b.hub.us.discards.values()) == 0          # counted afresh after each log line


def test_a_long_cycle_begun_during_a_timing_gap_is_not_a_no_echo():
    b, bus, gpio, clock = make()
    gpio.queue(gpio.long_cycle())
    clock.gap_after(100, 0.002)
    s = b.hub.read()
    assert s.dist_state == "none" and s.clear_t is None


def test_ok_sample_reports_the_closest_echo():
    b, bus, gpio, clock = make()
    gpio.queue(gpio.echo_cm(60), gpio.echo_cm(40), gpio.echo_cm(50))
    s = b.hub.read()
    assert s.dist_state == "ok" and abs(s.dist_cm - 50) < 0.5 and abs(s.echo_cm - 40) < 0.5 and s.clear_t is None


def test_open_space_long_cycles_are_never_a_fault():
    # Measured on the car facing open space: 59-75 % of pings are long cycles and none returns a real echo. A long
    # cycle that starts on the trigger and ends by itself is a living module that heard nothing: no reading, but no
    # fault (forward motion is still refused: it needs a fresh ok/far reading).
    b, bus, gpio, clock = make()
    gpio.default = gpio.long_cycle()
    states = _read_for(b, clock, 5.0)
    assert "fault" not in states and set(states) == {"none"}
    assert gpio.contention == 0


def test_no_rise_still_faults_while_long_cycles_come_and_go():
    b, bus, gpio, clock = make()
    gpio.default = None                                  # the module never answers the trigger
    gpio.queue(None, None, None, None, None, None)       # sample 1 and 2: no rise (3 pings each)
    assert [b.hub.read().dist_state for _ in range(2)] == ["none", "none"]
    gpio.queue(gpio.long_cycle())                        # a busy sample neither counts nor resets the run
    assert b.hub.read().dist_state == "none"
    clock.advance(0.5)
    assert b.hub.read().dist_state == "fault"            # the third no-rise sample
    gpio.queue(gpio.long_cycle())
    assert b.hub.read().dist_state == "fault"            # a busy sample keeps an established fault


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
    assert b.capabilities()["line"] is True              # one failed sample is skipped (measured: ~1 in 300 gets EIO)
    b.hub.read(); b.hub.read()
    assert b.capabilities()["line"] is False             # three in a row: absent
