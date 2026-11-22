"""Decision D8: near an obstacle the car may drive through missed readings while the closest echo heard in the last
0.48 s, minus the farthest the car could have travelled since (at the speed limit's ground speed, plus the time to
the next check), stays at or above the stopping margin. Also: every loop tick enforces freshness, and the
ultrasonic sensor_fault events say why."""
from picar_server import safety
from picar_server.controller import CmdError
from picar_server.hal.base import DIST_FAULT, DIST_NONE, DIST_OK, Sample

from conftest import Rig


def feed(r, state=DIST_NONE, cm=None, echo_cm=None, step=0.04, echo_age=None):
    r.clock.advance(step)
    r.ctl.touch()
    t = r.clock()
    r.ctl._on_sample(Sample(t, cm, state, None, None,
                            echo_cm=echo_cm if echo_cm is not None else (cm if state == DIST_OK else None),
                            echo_t=None if echo_age is None else t - echo_age))
    r.ctl.tick()
    return t


def code(r, op, **f):
    try:
        r.cmd(op, **f)
    except CmdError as e:
        return e.code
    return None


def driving(r):
    return r.backend.duty != (0, 0)


def v_max(r):
    return safety.speed_cm_s(min(safety.SPEED_MAX, r.ctl.cfg["speed_limit"]), r.ctl.cal)


def holds(r, cm, age, eff):
    """The D8 bound for one echo `age` seconds old at effective speed `eff`."""
    return age <= safety.NO_ECHO_TRUST_S and cm - v_max(r) * (age + safety.KNOWN_SLACK_S) >= r.ctl._margin(eff)


def started_rig():
    r = Rig()
    r.run(0.2, cm=300.0)
    return r


def test_constants():
    assert safety.KNOWN_SLACK_S == 0.1


def test_drives_through_missed_readings_while_the_bound_holds_then_stops():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    eff = r.ctl._eff
    cm = r.ctl._margin(eff) + v_max(r) * (0.3 + safety.KNOWN_SLACK_S)   # the bound holds until the echo is 0.3 s old
    t_echo = feed(r, state=DIST_OK, cm=cm)
    while True:
        feed(r)                                                         # the sensor misses: no reading
        age = r.clock() - t_echo
        if age <= safety.SENSOR_STALE_S or holds(r, cm, age, eff):
            assert driving(r), age
        else:
            assert not driving(r), age
            break
    assert 0.3 <= r.clock() - t_echo <= 0.3 + 0.05
    assert ("sensor_fault", {"sensor": "ultrasonic", "reason": "no_reading"}) in r.events


def test_far_obstacle_is_trusted_at_most_048_s():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    t_echo = feed(r, state=DIST_OK, cm=2000.0)
    while r.clock() + 0.04 - t_echo <= safety.NO_ECHO_TRUST_S:
        feed(r)
        assert driving(r), r.clock() - t_echo
    feed(r)
    assert not driving(r)


def test_a_forward_start_uses_the_bound():
    r = started_rig()
    feed(r, state=DIST_OK, cm=2000.0)
    for _ in range(4):
        feed(r)                                                         # 0.16 s: the reading itself is stale
    assert r.cmd("drive", dir="forward", speed=30) == {"speed": 30}
    r.cmd("stop")
    r2 = started_rig()
    eff = 30
    feed(r2, state=DIST_OK, cm=r2.ctl._margin(eff) + 1.0)               # too close to drive on without a reading
    for _ in range(4):
        feed(r2)
    assert code(r2, "drive", dir="forward", speed=30) == "sensor_fault"


def test_the_closest_recent_echo_counts():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    eff = r.ctl._eff
    feed(r, state=DIST_OK, cm=r.ctl._margin(eff) + 1.0)                 # close
    feed(r, state=DIST_OK, cm=2000.0)                                   # newer and far (another object)
    for _ in range(4):
        feed(r)                                                         # the far reading is stale now
    assert not driving(r)


def test_the_bound_counts_from_the_start_of_the_samples_pings():
    # Sixth review: an echo from the first of a sample's pings is older than the sample's timestamp.
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    eff = r.ctl._eff
    cm = r.ctl._margin(eff) + v_max(r) * (0.2 + safety.KNOWN_SLACK_S)
    t = feed(r, state=DIST_OK, cm=cm, echo_age=0.08)                    # its pings began 80 ms earlier
    for _ in range(4):
        feed(r)                                                         # 0.16 s after the sample, 0.24 s after the echo
    assert not driving(r), r.clock() - t


def test_one_stop_and_one_event_when_the_tick_and_a_sample_expire_together():
    # Sixth review: the tick read "forward", a sample stopped the car for staleness, then the tick stopped it again.
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    feed(r, state=DIST_OK, cm=r.ctl._margin(r.ctl._eff) + 1.0)
    r.clock.advance(0.2)
    stale = Sample(r.clock(), None, DIST_NONE, None, None)
    orig = r.ctl._account_reverse

    def interleave():
        orig()
        r.ctl._on_sample(stale)                                         # the sensor thread, mid-tick

    r.ctl._account_reverse = interleave
    r.ctl.touch()
    r.ctl.tick()
    faults = [e for e in r.events if e[0] == "sensor_fault"]
    assert faults == [("sensor_fault", {"sensor": "ultrasonic", "reason": "no_reading"})], faults
    assert not driving(r)


def test_a_dropped_echo_blocks_the_bound():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    feed(r, state=DIST_OK, cm=2000.0)
    feed(r, echo_cm=0.0)                                                # an echo the sensor could not time
    for _ in range(3):
        feed(r)
    assert not driving(r)


def test_a_lower_speed_limit_allows_more():
    def ends_driving(limit, cm):
        r = started_rig()
        r.cmd("config", speed_limit=limit)
        r.cmd("drive", dir="forward", speed=30)
        feed(r, state=DIST_OK, cm=cm)
        for _ in range(6):
            feed(r)                                                     # 0.24 s without a reading
        return driving(r)

    r = Rig()
    margin = r.ctl._margin(30)
    v_hi = safety.speed_cm_s(100, r.ctl.cal)
    cm = margin + v_hi * (0.24 + safety.KNOWN_SLACK_S) - 5.0            # fails at the full speed limit
    assert not ends_driving(100, cm)
    assert ends_driving(30, cm)


def test_the_bound_does_not_release_a_block():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    feed(r, state=DIST_OK, cm=10.0)
    assert r.ctl.blocked
    for _ in range(30):
        feed(r, state=DIST_OK, cm=2000.0) if _ == 0 else feed(r)        # one far echo, then nothing
    assert r.ctl.blocked


def test_the_loop_tick_enforces_freshness_without_samples():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    eff = r.ctl._eff
    t_echo = feed(r, state=DIST_OK, cm=r.ctl._margin(eff) + 1.0)
    while driving(r):
        r.clock.advance(0.05)
        r.ctl.touch()
        r.ctl.tick()                                                    # no samples at all
        assert r.clock() - t_echo < 1.0
    assert r.clock() - t_echo <= safety.SENSOR_STALE_S + 0.05
    assert ("sensor_fault", {"sensor": "ultrasonic", "reason": "no_reading"}) in r.events


def test_the_loop_tick_enforces_the_048_s_cap_without_samples():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    t_echo = feed(r, state=DIST_OK, cm=2000.0)
    while driving(r):
        r.clock.advance(0.05)
        r.ctl.touch()
        r.ctl.tick()
        assert r.clock() - t_echo < 1.0
    assert safety.NO_ECHO_TRUST_S - 0.05 <= r.clock() - t_echo <= safety.NO_ECHO_TRUST_S + 0.05


def test_sensor_fault_events_say_why():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    feed(r, state=DIST_FAULT)
    assert ("sensor_fault", {"sensor": "ultrasonic", "reason": "no_answer"}) in r.events
    feed(r, state=DIST_OK, cm=300.0)
    assert ("sensor_ok", {"sensor": "ultrasonic"}) in r.events
