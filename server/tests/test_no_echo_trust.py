"""Decision D7: a "nothing there" reading (a far ping, or the start of the ultrasonic module's long no-echo cycle) keeps
the distance fresh for forward motion for 0.48 s counted from its ping, unless an echo was heard after it, or an echo
closer than the near distance (1 m, or twice the full-speed margin) was heard in the last second."""
from picar_server import config, safety
from picar_server.controller import CmdError, Controller
from picar_server.hal.base import DIST_FAR, DIST_NONE, DIST_OK, Sample
from picar_server.hal.real import RealBackend

from conftest import FakeBackend, Rig
from fakes_hw import FakeBus, FakeClock, FakeGPIO

DETECT_S = 0.045        # a long cycle is known 40 ms after its ping, and the sample reaches the controller a bit later


def feed(r, state=DIST_NONE, cm=None, clear=False, clear_age=DETECT_S, echo_cm=None, step=0.04):
    """One sample `step` after the last: no reading by default; `clear` = this sample began a long cycle whose ping
    was `clear_age` ago. As on the car, that sample spans its ping and the 40 ms detection, so its ping comes after
    the previous sample."""
    r.clock.advance(max(step, clear_age + 0.003) if clear else step)
    r.ctl.touch()
    t = r.clock()
    r.ctl._on_sample(Sample(t, cm, state, None, None, clear_t=(t - clear_age) if clear else None,
                            echo_cm=echo_cm if echo_cm is not None else (cm if state == DIST_OK else None)))
    r.ctl.tick()
    return t - clear_age


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
    r.run(0.2, state=DIST_FAR)                          # fresh, nothing in range (no echo distance: D8 stays out)
    return r


def test_constants_are_the_owner_decision():
    assert safety.NO_ECHO_TRUST_S == 0.48 and safety.TRUST_NEAR_CM == 100.0 and safety.NEAR_ECHO_MEMORY_S == 1.0


def test_a_long_cycle_keeps_forward_motion_for_048_s_from_its_ping():
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    c0 = feed(r, clear=True)                            # the long cycle's ping was 45 ms ago
    while r.clock() + 0.04 - c0 <= safety.NO_ECHO_TRUST_S:
        feed(r)
        assert driving(r), r.clock() - c0
    feed(r)
    assert not driving(r) and "sensor_fault" in r.kinds()


def test_measured_open_space_rhythm_keeps_driving():
    # Measured on the car: long cycles start 409-413 ms apart; each is known 40 ms after its ping and its sample
    # reaches the controller about 8 ms later; in between, "no reading" samples every 40 ms.
    r = started_rig()
    r.cmd("drive", dir="forward", speed=50)
    t0 = ping = r.clock() + 0.01
    while ping < t0 + 5.0:
        r.clock.t = ping + 0.048
        r.ctl.touch()
        r.ctl._on_sample(Sample(r.clock(), None, DIST_NONE, None, None, clear_t=ping))
        r.ctl.tick()
        assert driving(r), r.clock() - t0
        nxt = ping + 0.413
        while r.clock() + 0.04 < nxt:
            feed(r)
            assert driving(r), r.clock() - t0
        ping = nxt
    assert "sensor_fault" not in r.kinds()


def test_a_trusted_no_echo_allows_a_forward_start_and_expires():
    r = started_rig()
    feed(r, clear=True)
    for _ in range(4):
        feed(r)                                         # the last ok reading is stale, the no-echo is not
    assert r.cmd("drive", dir="forward", speed=30) == {"speed": 30}
    r.cmd("stop")
    for _ in range(8):
        feed(r)                                         # now older than 0.48 s
    assert code(r, "drive", dir="forward", speed=30) == "sensor_fault"


def test_a_near_echo_in_the_last_second_disables_the_trust():
    r = Rig()
    r.run(0.2, cm=r.ctl._margin(30) + 1.0)              # something just beyond the margin (D8 cannot drive on it)
    feed(r, clear=True)
    for _ in range(4):
        feed(r)
    assert code(r, "drive", dir="forward", speed=30) == "sensor_fault"
    for _ in range(25):                                 # a second later, with fresh no-echo readings
        feed(r, clear=True)
    assert r.cmd("drive", dir="forward", speed=30) == {"speed": 30}


def test_an_echo_heard_in_an_inconclusive_sample_disables_the_trust():
    r = started_rig()
    feed(r, echo_cm=r.ctl._margin(30) + 1.0)            # e.g. one ping heard an echo, then the module went busy
    feed(r, clear=True, clear_age=0.0)
    for _ in range(4):
        feed(r)
    assert code(r, "drive", dir="forward", speed=30) == "sensor_fault"


def test_an_echo_after_the_no_echo_cancels_the_trust():
    # Review finding: an obstacle at 105 cm with stop_cm 100 must not be forgotten once its 150 ms reading expires.
    r = started_rig()
    r.cmd("config", stop_cm=100)
    feed(r, clear=True)
    feed(r, state=DIST_OK, cm=105.0)
    assert code(r, "drive", dir="forward", speed=100) == "obstacle_blocked"
    for _ in range(4):
        feed(r)
    assert code(r, "drive", dir="forward", speed=100) in ("sensor_fault", "obstacle_blocked")


def test_raising_stop_cm_reclassifies_recent_echoes():
    # Fourth review: an echo heard as "not near" must count as near once stop_cm is raised within the memory.
    r = started_rig()
    feed(r, state=DIST_OK, cm=105.0)                    # not near with the default stop distance
    feed(r, clear=True)                                 # newer: nothing there
    for _ in range(4):
        feed(r)                                         # the 105 cm reading is no longer fresh; the trust is
    assert r.cmd("drive", dir="forward", speed=30) == {"speed": 30}
    r.cmd("stop")
    r.cmd("config", stop_cm=100)                        # now 105 cm, heard 0.2 s ago, is within the near distance
    assert code(r, "drive", dir="forward", speed=100) == "sensor_fault"


def test_a_far_echo_before_the_no_echo_keeps_the_trust():
    r = started_rig()
    feed(r, echo_cm=150.0)
    feed(r, clear=True, clear_age=0.0)
    for _ in range(4):
        feed(r)
    assert r.cmd("drive", dir="forward", speed=30) == {"speed": 30}


def test_the_near_distance_grows_with_a_large_stop_distance():
    r = started_rig()
    r.cmd("config", stop_cm=100)                        # full-speed margin over 100 cm: near is twice that
    v = safety.speed_cm_s(100, r.ctl.cal)
    cm = r.ctl._margin(30) + v * (0.2 + safety.KNOWN_SLACK_S) - 5.0   # beyond 1 m, too close for D8
    assert safety.TRUST_NEAR_CM < cm < 2.0 * r.ctl._margin(safety.SPEED_MAX)
    feed(r, echo_cm=cm)
    feed(r, clear=True, clear_age=0.0)
    for _ in range(4):
        feed(r)
    assert code(r, "drive", dir="forward", speed=30) == "sensor_fault"


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


# ---- the real sensor code feeding the controller, on one fake clock ------------------------------------------------

class Car:
    """RealBackend's sensor hub (fake GPIO / bus) delivering samples to a Controller, at the sensor thread's cadence.
    The I2C reads take `i2c_s` per sample (measured on the car: the busy cycles start 409-413 ms apart)."""

    def __init__(self, i2c_s=0.008):
        self.hw = FakeClock()
        self.bus, self.gpio = FakeBus(), FakeGPIO(self.hw)
        self.bus.line_bytes = None
        self.b = RealBackend({}, bus=self.bus, gpio=self.gpio, clock=self.hw)
        self.i2c_s = i2c_s
        self.backend = FakeBackend()
        self.ctl = Controller(self.backend, config.merge(None), shared=True, clock=lambda: self.hw.now,
                              start_on_hold=False, on_event=lambda k, f: None, on_done=lambda o, i, r: None)
        self.ctl.start(background=False)
        self.ctl.attached = True

    def step(self):
        t0 = self.hw.now
        s = self.b.hub.read()
        self.hw.advance(self.i2c_s)
        self.ctl.touch()
        self.ctl._on_sample(s)
        self.ctl.tick()
        self.hw.advance(max(0.0, 0.04 - (self.hw.now - t0)))

    def drive(self):
        self.b.set_motors(1000, 1000)                   # the hub pings 3 times while moving forward
        return self.ctl.command("drive", {"dir": "forward", "speed": 50}, owner="me", cmd_id=1)

    def driving(self):
        return self.backend.duty != (0, 0)


def test_integrated_open_space_long_cycles_keep_driving():
    car = Car()
    car.gpio.default = (0.0003, 0.027)                  # far
    for _ in range(5):
        car.step()
    assert car.drive() == {"speed": 50}
    car.gpio.default = car.gpio.long_cycle()            # open space: every ping is a long cycle
    t0 = car.hw.now
    while car.hw.now - t0 < 5.0:
        car.step()
        assert car.driving(), car.hw.now - t0


def test_integrated_stuck_line_after_one_cycle_stops_within_the_window():
    car = Car()
    car.gpio.default = (0.0003, 0.027)
    for _ in range(5):
        car.step()
    car.drive()
    car.gpio.queue(car.gpio.long_cycle(width=5.0))      # the line goes high and stays high
    while not car.gpio._busy():
        car.step()
    rise = car.gpio._busy_from
    while car.driving():
        car.step()
        assert car.hw.now - rise < 5.0
    stopped_after = car.hw.now - rise
    assert stopped_after <= safety.NO_ECHO_TRUST_S + 0.05, stopped_after


def test_integrated_a_stall_anywhere_never_stretches_the_window():
    # Fourth review: a 200 ms stall at the rising edge kept forward motion on trust 608 ms after the ping.
    for at in range(1, 60, 3):
        car = Car()
        car.gpio.default = (0.0003, 0.027)
        for _ in range(5):
            car.step()
        car.drive()
        car.gpio.queue(car.gpio.long_cycle(width=5.0))
        car.hw.gap_after(at, 0.2)
        while not car.gpio._busy():
            car.step()
        rise = car.gpio._busy_from
        while car.driving():
            car.step()
            assert car.hw.now - rise < 5.0
        assert car.hw.now - rise <= safety.NO_ECHO_TRUST_S + 0.05, (at, car.hw.now - rise)


def test_integrated_discarded_near_echo_blocks_the_trust():
    car = Car()
    car.gpio.default = (0.0003, 0.027)
    for _ in range(5):
        car.step()
    car.gpio.queue(car.gpio.echo_cm(50), (0.0003, 0.027), car.gpio.long_cycle())
    car.hw.gap_after(60, 0.002)                         # a scheduling gap while the 50 cm echo is high
    car.step()
    for _ in range(5):
        car.step()                                      # the module is busy: no readings
    try:
        car.drive()
        started = True
    except CmdError:
        started = False
    assert not started
