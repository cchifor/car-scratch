"""Two pre-existing gaps the M8 reviews found, fixed for both ultrasonic implementations:

- a distance's freshness counts from when it was observed (the newest valid ping's trigger), not from when the
  sample was assembled: a stall after the pings must not make it look fresh;
- a sample whose pings heard an echo inside the margin never counts toward releasing `blocked`, even when its
  median reading is far.
"""
from picar_server import safety
from picar_server.hal.base import DIST_FAR, DIST_NONE, DIST_OK, Sample

from conftest import Rig
from test_hal_sensors import make


def test_distance_time_is_the_newest_valid_pings_trigger():
    b, bus, gpio, clock = make()
    gpio.queue(gpio.echo_cm(50), gpio.echo_cm(52), gpio.echo_cm(51))
    s = b.hub.read()
    assert s.dist_state == DIST_OK
    assert s.dist_t == b.hub.us.ping_start
    assert s.echo_t <= s.dist_t <= s.t


def test_a_stall_after_the_pings_shows_in_the_distance_age():
    b, bus, gpio, clock = make()
    gpio.queue(gpio.echo_cm(50), gpio.echo_cm(52), gpio.echo_cm(51))
    orig = b.hub._log_discards

    def stall():
        clock.advance(0.3)                               # the sensor thread stalls after the pings, before Sample.t
        return orig()

    b.hub._log_discards = stall
    s = b.hub.read()
    assert s.t - s.dist_t >= 0.3


def test_no_distance_time_without_a_valid_reading():
    b, bus, gpio, clock = make()
    gpio.default = None                                  # no answer
    s = b.hub.read()
    assert s.dist_t is None


def feed(r, state=DIST_NONE, cm=None, echo_cm=None, dist_age=None, step=0.04):
    r.clock.advance(step)
    r.ctl.touch()
    t = r.clock()
    r.ctl._on_sample(Sample(t, cm, state, None, None,
                            echo_cm=echo_cm if echo_cm is not None else (cm if state == DIST_OK else None),
                            dist_t=None if dist_age is None else t - dist_age))
    r.ctl.tick()


def test_freshness_counts_from_the_distance_time():
    r = Rig()
    r.run(0.2, cm=300.0)
    r.cmd("drive", dir="forward", speed=50)
    margin = r.ctl._margin(r.ctl._eff)
    feed(r, state=DIST_OK, cm=margin + 1.0)                             # close: only a fresh reading may drive on
    for _ in range(2):
        feed(r, state=DIST_OK, cm=margin + 1.0, dist_age=0.2)          # assembled now, observed 0.2 s ago
    assert r.backend.duty == (0, 0)
    assert ("sensor_fault", {"sensor": "ultrasonic", "reason": "no_reading"}) in r.events


def test_a_far_sample_with_a_close_echo_does_not_release_a_block():
    r = Rig()
    r.run(0.2, cm=300.0)
    r.cmd("drive", dir="forward", speed=50)
    feed(r, state=DIST_OK, cm=5.0)
    assert r.ctl.blocked
    n = round((safety.CLEAR_S + 0.5) / 0.04)
    for _ in range(n):
        feed(r, state=DIST_FAR, echo_cm=5.0)                             # far median, but one ping heard 5 cm
    assert r.ctl.blocked
    for _ in range(n):
        feed(r, state=DIST_FAR)
    assert not r.ctl.blocked


def test_a_far_echo_in_a_far_sample_still_releases():
    r = Rig()
    r.run(0.2, cm=300.0)
    r.cmd("drive", dir="forward", speed=50)
    feed(r, state=DIST_OK, cm=5.0)
    for _ in range(round((safety.CLEAR_S + 0.5) / 0.04)):
        feed(r, state=DIST_FAR, echo_cm=150.0)                         # an echo well beyond any margin
    assert not r.ctl.blocked
