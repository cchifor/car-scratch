"""Decision D7: a "nothing there" reading (a far ping, or the start of the ultrasonic module's long no-echo cycle) keeps
the distance fresh for forward motion for 0.45 s, unless an echo closer than 1 m was heard in the last second."""
from picar_server import safety
from picar_server.controller import CmdError
from picar_server.hal.base import DIST_NONE, DIST_OK, Sample

from conftest import Rig


def feed(r, state=DIST_NONE, cm=None, clear=False, echo_cm=None, step=0.04):
    """One sample `step` after the last: no reading by default; `clear` = this sample began a long cycle."""
    r.clock.advance(step)
    r.ctl.touch()
    t = r.clock()
    r.ctl._on_sample(Sample(t, cm, state, None, None, clear_t=t if clear else None,
                            echo_cm=echo_cm if echo_cm is not None else (cm if state == DIST_OK else None)))
    r.ctl.tick()


def code(r, op, **f):
    try:
        r.cmd(op, **f)
    except CmdError as e:
        return e.code
    return None


def driving(r):
    return r.backend.duty != (0, 0)


def started_rig():
    r = Rig()
    r.run(0.2, cm=150.0)                                # fresh, nothing near
    return r


def test_constants_are_the_owner_decision():
    assert safety.NO_ECHO_TRUST_S == 0.45 and safety.TRUST_NEAR_CM == 100.0 and safety.NEAR_ECHO_MEMORY_S == 1.0


def test_a_long_cycle_keeps_forward_motion_for_045_s():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    feed(r, clear=True)                                 # the long cycle starts: nothing heard
    t0 = r.clock()
    while r.clock() - t0 + 0.04 <= safety.NO_ECHO_TRUST_S:
        feed(r)                                         # the module is busy: no reading
        assert driving(r), r.clock() - t0
    feed(r)                                             # now older than 0.45 s
    assert not driving(r) and "sensor_fault" in r.kinds()


def test_back_to_back_long_cycles_keep_driving():
    # Measured on the car facing open space: long cycles start 409-413 ms apart.
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    for _ in range(10):
        feed(r, clear=True)
        for _ in range(9):
            feed(r, step=0.041)
        assert driving(r)
    assert "sensor_fault" not in r.kinds()


def test_a_trusted_no_echo_allows_a_forward_start_and_expires():
    r = started_rig()
    feed(r, clear=True)
    for _ in range(5):
        feed(r)                                         # 0.2 s: the last ok reading is stale, the no-echo is not
    assert r.cmd("drive", dir="forward", speed=30) == {"speed": 30}
    r.cmd("stop")
    for _ in range(7):
        feed(r)                                         # the no-echo is now 0.48 s old
    assert code(r, "drive", dir="forward", speed=30) == "sensor_fault"


def test_a_near_echo_in_the_last_second_disables_the_trust():
    r = Rig()
    r.run(0.2, cm=80.0)                                 # something 80 cm ahead
    feed(r, clear=True)
    for _ in range(5):
        feed(r)
    assert code(r, "drive", dir="forward", speed=30) == "sensor_fault"
    for _ in range(25):                                 # a second later, with fresh no-echo readings
        feed(r, clear=True)
    assert r.cmd("drive", dir="forward", speed=30) == {"speed": 30}


def test_an_echo_heard_in_an_inconclusive_sample_disables_the_trust():
    r = started_rig()
    feed(r, echo_cm=50.0)                               # e.g. one ping heard 50 cm, then the module went busy
    feed(r, clear=True)
    for _ in range(5):
        feed(r)
    assert code(r, "drive", dir="forward", speed=30) == "sensor_fault"


def test_far_echo_does_not_disable_the_trust():
    r = started_rig()
    feed(r, echo_cm=150.0)
    feed(r, clear=True)
    for _ in range(5):
        feed(r)
    assert r.cmd("drive", dir="forward", speed=30) == {"speed": 30}


def test_trusted_no_echo_samples_release_a_block_after_the_near_memory():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    feed(r, state=DIST_OK, cm=10.0)                     # obstacle: stop, blocked
    assert r.ctl.blocked and not driving(r)
    t_near = r.clock()
    released_at = None
    while r.clock() - t_near < 3.0:
        k = round((r.clock() - t_near) / 0.04)
        feed(r, clear=(k % 10 == 0))                    # the car was turned to open space: long cycles
        if released_at is None and not r.ctl.blocked:
            released_at = r.clock() - t_near
    assert released_at is not None
    assert released_at >= safety.NEAR_ECHO_MEMORY_S + safety.CLEAR_S - 0.05


def test_samples_without_clear_t_behave_as_before():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    for _ in range(4):
        feed(r)                                         # no reading, no no-echo: stale after 0.15 s
    assert not driving(r) and "sensor_fault" in r.kinds()
