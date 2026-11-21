"""Simulator: worlds, geometry, sensors, physics and the controller on a SimBackend."""
import asyncio
import copy
import json
import math

import pytest

from picar_server import config, safety
from picar_server.controller import CmdError, Controller
from picar_server.hal.base import DIST_FAR, DIST_OK
from picar_server.hal.sim import world as W
from picar_server.hal.sim.backend import SimBackend

CAL = config.merge(None)


def sim(world="arena", **kw):
    return SimBackend(W.load_world(world), copy.deepcopy(CAL), seed=1, **kw)


def settle(b, secs=1.0, hz=50):
    for _ in range(int(secs * hz)):
        b.step(1 / hz)


# ---- worlds ------------------------------------------------------------------------------------------------

def test_all_five_worlds_load_and_serialise():
    ids = [w["id"] for w in W.list_worlds()]
    assert sorted(ids) == ["arena", "corridor", "lamp", "maze", "oval"]
    for wid in ids:
        w = W.load_world(wid)
        d = json.loads(json.dumps(w.to_dict()))
        assert d["id"] == wid and d["walls"] and len(d["start"]) == 3
    assert W.load_world("oval").line["closed"] and W.load_world("lamp").lights


def test_world_validation_and_unknown_ids():
    with pytest.raises(KeyError):
        W.load_world("nope")
    with pytest.raises(KeyError):
        W.load_world("../arena")
    for bad in ({"id": "x", "size": [1]}, {"id": "x", "size": [1, 1], "walls": [[1, 2, 3]]},
                {"id": "x", "size": [1, 1], "line": {"points": [[0, 0]], "width": 1}},
                {"id": "BAD ID", "size": [1, 1]}, {"id": "x", "size": [1, 1], "lights": [{"x": 1}]}):
        with pytest.raises(W.WorldError):
            W.validate_world(bad)
    w = W.validate_world({"id": "x", "size": [10, 10]})
    assert w.walls == () and w.line is None and w.start == {"x": 0.0, "y": 0.0, "dir": 90.0}


def test_ray_cast_distance_and_incidence():
    walls = [(100.0, -100.0, 100.0, 100.0)]
    d, i, inc = W.ray_cast(0, 0, 1, 0, walls, 200)
    assert d == pytest.approx(100) and i == 0 and inc == pytest.approx(0)
    dx, dy = W.heading_vec(60 + 0)  # heading 60 deg from +y
    hit = W.ray_cast(0, 0, math.cos(math.radians(30)), math.sin(math.radians(30)), walls, 200)
    assert hit[2] == pytest.approx(30)
    assert W.ray_cast(0, 0, -1, 0, walls, 200) is None and W.ray_cast(0, 0, 1, 0, walls, 50) is None


def test_segments_and_polygon_collision():
    assert W.segments_intersect((0, 0, 10, 10), (0, 10, 10, 0))
    assert not W.segments_intersect((0, 0, 1, 1), (2, 2, 3, 3))
    assert not W.segments_intersect((0, 0, 1, 0), (0, 1, 1, 1))
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert W.polygon_hits_walls(square, [(5, -5, 5, 5)])
    assert W.polygon_hits_walls(square, [(4, 4, 6, 6)])         # a wall fully inside
    assert not W.polygon_hits_walls(square, [(20, 0, 20, 10)])


# ---- sensors -----------------------------------------------------------------------------------------------------

def test_ultrasonic_reads_distance_to_the_wall_with_small_noise():
    b = sim("corridor")                                  # sensor at x=70, end wall at 220
    state, cm = b.read_distance()
    assert state == DIST_OK and cm == pytest.approx(150, abs=3)
    b.teleport(0, 0, 90 + 0)
    readings = [b.read_distance()[1] for _ in range(200)]
    assert abs(sum(readings) / 200 - 198) < 0.3 and max(readings) - min(readings) > 0.5   # noise exists


def test_ultrasonic_far_beyond_range_and_on_oblique_walls():
    b = sim("corridor")
    b.teleport(-150, 0, 90)                              # 370 cm from the end wall
    assert b.read_distance() == (DIST_FAR, None)
    b.teleport(190, 0, 20)                               # the fan meets the end wall at 55+ degrees
    state, _ = b.read_distance()
    b.teleport(150, 0, 90 - 20)
    assert b.read_distance()[0] == DIST_OK              # 20 deg: still seen
    assert state == DIST_FAR


def test_line_sensors_on_the_oval_track():
    b = sim("oval")
    raw = b.read_line()
    assert [v < 300 for v in raw] == [False, False, True, False, False]
    b.teleport(0, 0, 90)
    assert all(v > 600 for v in b.read_line())
    c = sim("corridor")
    c.teleport(0, 0, 90)                                 # the line runs along the corridor axis
    assert [v < 300 for v in c.read_line()][2]


def test_light_is_lower_raw_when_closer_and_blocked_by_walls():
    b = sim("lamp")
    b.teleport(40, 40, 90)                               # facing the lamp at (100, 40)
    near = b.read_light()
    b.teleport(-100, 40, 90)
    far = b.read_light()
    # lamp intensity 10 (2026-11-21: seen from the start pose, ~2 m away): far is dim, near is saturated bright
    assert near[1] < far[1] and near[1] < 150 and 160 < far[1] < 200
    b.teleport(40, 40, 270)                              # facing away
    assert b.read_light() == [200, 200, 200]
    assert safety.brightest([safety.brightness(v, CAL) for v in near]) == "middle"
    m = sim("maze")
    assert m.read_light() == [200, 200, 200]             # no lights


# ---- physics --------------------------------------------------------------------------------------------------------

def test_speed_lag_cruise_and_coast():
    b = sim("lamp")
    b.teleport(-120, 0, 90)
    b.set_motors(2559, 2559)                             # user speed 50: 37.5 cm/s
    settle(b, 0.2)
    assert 0.4 * 37.5 < b._v < 0.8 * 37.5                # first-order lag, not instant
    settle(b, 1.5)
    assert b._v == pytest.approx(37.5, rel=0.02)
    x0 = b.pose()[0]
    b.set_motors(0, 0)
    settle(b, 1.0)
    coast = b.pose()[0] - x0
    assert coast == pytest.approx(3.0, abs=1.0)          # about 3 cm at 50 %
    assert b.motion() == (0, 0)


def test_steering_circle_radius_follows_wheelbase():
    b = sim("lamp")
    b.teleport(0, 0, 90)
    b.set_steering(30)
    b.set_motors(4095, 4095)
    radius = CAL["wheelbase_cm"] / math.tan(math.radians(30))
    settle(b, 1.0)
    b._v = 70.0
    for _ in range(100):
        b.step(0.02)
        x, y, _d = b.pose()
        assert math.hypot(x, y + radius) == pytest.approx(radius, rel=0.03)
    assert safety.wrap_deg(b.pose()[2] - 90) > 0


def test_wall_collision_stalls_and_sets_bumped_then_reverse_frees():
    b = sim("corridor")
    b.teleport(190, 0, 90)                               # front edge at 212 + ... close to the wall at 220
    b.set_motors(4095, 4095)
    settle(b, 1.0)
    assert b.bumped and b._v == 0.0
    x = b.pose()[0]
    assert x < 220 - 22 + 0.1                            # the nose stays outside the wall
    b.set_motors(-1500, -1500)
    settle(b, 0.4)
    assert not b.bumped and b.pose()[0] < x


def test_inhibit_latch_refuses_forward_but_not_backward():
    b = sim()
    b.inhibit_forward()
    assert b.set_motors(1000, 1000) is False and b.motion() == (0, 0)
    assert b.set_motors(-1000, -1000) is True and b.motion() == (-1000, -1000)
    b.clear_inhibit()
    assert b.set_motors(1000, 1000) is True
    assert b.stop_motors() is True and b.motion() == (0, 0)


def test_reset_pose_and_set_world():
    b = sim("arena")
    b.teleport(5, 5, 0)
    b.reset_pose()
    assert b.pose() == (-110.0, 0.0, 90.0)
    b.set_world(W.load_world("corridor"))
    assert b.pose() == (48.0, 0.0, 90.0)


# ---- controller + simulator, real asyncio time -------------------------------------------------------------------------

async def run_sim(world="corridor"):
    events, dones = [], []
    cal = copy.deepcopy(CAL)
    ctl = Controller(SimBackend(W.load_world(world), cal, seed=3), cal, shared=False,
                     on_event=lambda k, f: events.append((k, f)), on_done=lambda o, i, r: dones.append((i, r)))
    ctl.start()
    await asyncio.sleep(0.25)                            # first valid samples
    return ctl, events, dones


async def test_corridor_timed_move_ends_with_obstacle_before_the_wall():
    ctl, events, dones = await run_sim("corridor")
    try:
        ctl.backend.teleport(100, 0, 90)                 # sensor at 122, wall at 220: 98 cm ahead
        ctl.command("move", {"dir": "forward", "speed": 100, "ms": 30000}, owner="t", cmd_id=9)
        for _ in range(100):
            ctl.touch()
            await asyncio.sleep(0.05)
            if dones:
                break
        assert dones == [(9, "obstacle")]
        assert ("obstacle_stop", ) == (events[0][0],)
        await asyncio.sleep(0.5)
        x, _, _ = ctl.backend.pose()
        gap = 220 - (x + 22)
        assert 3 < gap < 40 and not ctl.backend.bumped   # stopped by the guard, not by the wall
        with pytest.raises(CmdError) as e:
            ctl.command("drive", {"dir": "forward", "speed": 30}, cmd_id=10)
        assert e.value.code == "obstacle_blocked"
        ctl.command("drive", {"dir": "backward", "speed": 30}, cmd_id=11)
    finally:
        ctl.close()


async def test_sim_timed_move_completes_in_open_space():
    ctl, events, dones = await run_sim("arena")
    try:
        ctl.command("move", {"dir": "forward", "speed": 40, "ms": 300}, owner="t", cmd_id=1)
        await asyncio.sleep(0.5)
        assert dones == [(1, "completed")] and ctl.backend.pose()[0] > -110
        ctl.command("reset_pose", {}, cmd_id=2)
        assert ctl.snapshot()["pose"] == {"x": -110.0, "y": 0.0, "dir": 90.0, "src": "sim"}
        assert "bumped" in ctl.snapshot()["drive"]
    finally:
        ctl.close()
