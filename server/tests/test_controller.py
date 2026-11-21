"""Controller safety rules on a fake clock (docs/contracts.md section 1)."""
import asyncio
import math

import pytest

from picar_server import safety
from picar_server.controller import CmdError
from picar_server.hal.base import DIST_FAR, DIST_FAULT, DIST_NONE, DIST_OK

from conftest import FakeBackend, Rig


def code(rig, op, **f):
    with pytest.raises(CmdError) as e:
        rig.cmd(op, **f)
    return e.value.code


# ---- speed mapping and limits -----------------------------------------------------------------------

def test_drive_writes_calibrated_duty(rig):
    ack = rig.cmd("drive", dir="forward", speed=50)
    assert ack == {"speed": 50}
    assert rig.backend.duty == (2559, 2559)           # round(4095 * 62.5 / 100)
    rig.cmd("drive", dir="backward", speed=30)
    assert rig.backend.duty == (-round(4095 * 0.475),) * 2


def test_reverse_clamped_to_40_also_via_set_speed(rig):
    assert rig.cmd("drive", dir="backward", speed=90) == {"speed": 40}
    assert rig.cmd("set_speed", speed=90) == {"speed": 40}


def test_speed_limit_applies_and_lowering_reduces_running_motion(rig):
    rig.cmd("config", speed_limit=60)
    assert rig.cmd("drive", dir="forward", speed=100) == {"speed": 60}
    rig.cmd("config", speed_limit=30)
    assert rig.backend.duty == (safety.speed_to_duty(30, rig.cal),) * 2
    rig.cmd("config", speed_limit=100)                 # raising never speeds up
    assert rig.backend.duty == (safety.speed_to_duty(30, rig.cal),) * 2


def test_speed_zero_is_stop_and_set_speed_noop_when_stopped(rig):
    rig.cmd("drive", dir="forward", speed=40)
    assert rig.cmd("drive", dir="forward", speed=0) == {"speed": 0}
    assert rig.backend.duty == (0, 0)
    assert rig.cmd("set_speed", speed=80) == {"speed": 0}
    assert rig.backend.duty == (0, 0)


def test_identical_drive_is_idempotent(rig):
    rig.cmd("drive", dir="forward", speed=40)
    n = len(rig.backend.sets)
    since = rig.ctl._drive_since
    rig.clock.advance(1)
    rig.cmd("drive", dir="forward", speed=40)
    assert len(rig.backend.sets) == n and rig.ctl._drive_since == since


def test_steer_clamped_and_ack_carries_value(rig):
    assert rig.cmd("steer", angle=60) == {"angle": 45.0}
    assert rig.backend.steering[-1] == 45.0


# ---- obstacle guard ---------------------------------------------------------------------------------------

def test_guard_stops_forward_inhibits_and_blocks(rig):
    rig.cmd("drive", dir="forward", speed=50)
    rig.sample(cm=15)
    assert rig.backend.duty == (0, 0) and rig.backend.inhibit
    assert "obstacle_stop" in rig.kinds() and rig.ctl.blocked
    assert code(rig, "drive", dir="forward", speed=30) == "obstacle_blocked"
    rig.cmd("drive", dir="backward", speed=30)         # backward and steering stay allowed
    assert rig.backend.duty[0] < 0


def test_guard_ignores_backward_motion(rig):
    rig.cmd("drive", dir="backward", speed=30)
    rig.sample(cm=5)
    assert rig.backend.duty[0] < 0 and not rig.ctl.blocked


def test_blocked_clears_after_one_second_of_clear_samples(rig):
    rig.cmd("drive", dir="forward", speed=50)
    rig.sample(cm=15)
    rig.run(0.8, cm=200)
    assert rig.ctl.blocked and rig.backend.inhibit
    rig.run(0.5, cm=200)
    assert not rig.ctl.blocked and not rig.backend.inhibit


def test_blocked_clear_timer_restarts_on_a_close_sample(rig):
    rig.cmd("drive", dir="forward", speed=50)
    rig.sample(cm=15)
    rig.run(0.8, cm=100)
    rig.run(0.04, cm=25)                               # inside margin + 5: not a clear sample
    rig.run(0.8, cm=100)
    assert rig.ctl.blocked
    rig.run(0.4, cm=100)
    assert not rig.ctl.blocked


def test_margin_grows_with_speed(rig):
    assert code_margin(rig, 30) < code_margin(rig, 100)
    m = safety.margin_cm(20, 100, rig.cal)
    assert m == pytest.approx(20 + 12 + 70 * 0.2)


def code_margin(rig, speed):
    return safety.margin_cm(20, speed, rig.cal)


def test_command_time_check_uses_proposed_speed(rig):
    rig.run(0.1, cm=40)
    assert rig.cmd("drive", dir="forward", speed=30) == {"speed": 30}   # margin(30) is about 26
    rig.cmd("stop")
    assert code(rig, "drive", dir="forward", speed=100) == "obstacle_blocked"   # margin(100) = 46
    rig.cmd("config", speed_limit=30)
    assert rig.cmd("drive", dir="forward", speed=100) == {"speed": 30}


def test_guard_off_ignores_distance_but_not_sensor_faults(rig):
    rig.cmd("config", obstacle_stop=False)
    rig.cmd("drive", dir="forward", speed=50)
    rig.sample(cm=5)
    assert rig.backend.duty[0] > 0
    rig.sample(state=DIST_FAULT)
    assert rig.backend.duty == (0, 0)
    assert ("sensor_fault", {"sensor": "ultrasonic"}) in rig.events and "obstacle_stop" not in rig.kinds()


def test_guard_off_forward_drive_times_out_after_5s(rig):
    rig.cmd("config", obstacle_stop=False)
    rig.cmd("drive", dir="forward", speed=30)
    rig.run(4.5, cm=100)
    assert rig.backend.duty[0] > 0 and "drive_timeout" not in rig.kinds()
    rig.run(1.0, cm=100)
    assert rig.backend.duty == (0, 0) and "drive_timeout" in rig.kinds()


def test_sensor_fault_is_not_an_obstacle_stop(rig):
    rig.cmd("drive", dir="forward", speed=50)
    rig.sample(state=DIST_FAULT)
    assert rig.backend.duty == (0, 0)
    assert "sensor_fault" in rig.kinds() and "obstacle_stop" not in rig.kinds()
    assert code(rig, "drive", dir="forward", speed=30) == "sensor_fault"
    rig.cmd("drive", dir="backward", speed=30)         # backward still allowed


def test_stale_good_sample_stops_forward(rig):
    rig.cmd("drive", dir="forward", speed=50)
    rig.clock.advance(0.2)
    rig.sample(state=DIST_NONE)                        # last ok/far sample is older than 150 ms
    assert rig.backend.duty == (0, 0) and "sensor_fault" in rig.kinds()


def test_far_samples_are_valid(rig):
    rig.run(0.1, state=DIST_FAR)
    assert rig.cmd("drive", dir="forward", speed=60) == {"speed": 60}


def test_forward_refused_before_first_sample():
    r = Rig()
    assert code(r, "drive", dir="forward", speed=30) == "sensor_fault"
    assert r.cmd("drive", dir="backward", speed=30)


def test_loop_freshness_stops_forward_when_samples_vanish(rig):
    rig.cmd("drive", dir="forward", speed=30)
    rig.run(0.7, state=None)                           # no samples at all
    assert rig.backend.duty == (0, 0) and rig.ctl.blocked
    assert ("sensor_fault", {"sensor": "ultrasonic"}) in rig.events


# ---- watchdog, hold, pause, disconnect --------------------------------------------------------------------

def test_watchdog_stops_latches_hold_and_resume_clears(rig):
    rig.cmd("drive", dir="forward", speed=30)
    rig.run(0.5)
    rig.run(0.9, touch=False)
    assert rig.backend.duty == (0, 0) and rig.ctl.hold
    assert "watchdog_stop" in rig.kinds() and ("hold", {"reason": "watchdog"}) in rig.events
    assert code(rig, "drive", dir="forward", speed=30) == "hold"
    rig.cmd("resume")
    assert "resumed" in rig.kinds() and not rig.ctl.hold
    assert rig.cmd("drive", dir="forward", speed=30)


def test_watchdog_latches_hold_even_when_stopped(rig):
    rig.run(1.0, touch=False)
    assert rig.ctl.hold and "watchdog_stop" not in rig.kinds()


def test_watchdog_does_not_fire_without_an_attached_controller():
    r = Rig(attached=False)
    r.run(2.0, touch=False)
    assert not r.ctl.hold


async def test_watchdog_finishes_timed_move_with_done_watchdog(rig):
    rig.cmd("move", dir="forward", speed=30, ms=5000, _id=7)
    rig.run(1.0, touch=False)
    await asyncio.sleep(0)
    assert (7, "watchdog") in rig.dones


def test_pause_stops_and_latches_hold(rig):
    rig.cmd("drive", dir="forward", speed=30)
    rig.cmd("pause")
    assert rig.backend.duty == (0, 0) and rig.ctl.hold
    assert ("hold", {"reason": "pause"}) in rig.events
    assert code(rig, "drive", dir="forward", speed=30) == "hold"


def test_shared_car_starts_on_hold_and_boot_hold_survives_a_new_controller():
    r = Rig(hold=True)
    r.run(0.2)
    assert code(r, "drive", dir="forward", speed=30) == "hold"
    r.ctl.new_controller(returning=False)
    assert r.ctl.hold                                  # only resume clears a boot hold
    r.cmd("resume")
    assert r.cmd("drive", dir="forward", speed=30)


def test_every_acquisition_of_control_starts_on_hold_and_only_resume_clears_it(rig):
    assert not rig.ctl.hold
    rig.ctl.new_controller(returning=False)            # a take / mode->car on a car that is not on hold
    assert rig.ctl.hold and rig.ctl.hold_reason == "control"
    assert code(rig, "drive", dir="forward", speed=30) == "hold"
    rig.cmd("resume")
    assert not rig.ctl.hold and rig.cmd("drive", dir="forward", speed=30)
    rig.ctl.new_controller(returning=True)             # a returning lease holder too, and the car stops
    assert rig.ctl.hold and rig.backend.duty == (0, 0)


def test_disconnect_latches_hold_and_every_new_controller_keeps_it_until_resume(rig):
    rig.cmd("drive", dir="forward", speed=30)
    rig.ctl.controller_lost()
    assert rig.backend.duty == (0, 0) and rig.ctl.hold and not rig.ctl.attached
    rig.ctl.new_controller(returning=True)
    assert rig.ctl.hold
    rig.ctl.controller_lost()
    rig.ctl.new_controller(returning=False)
    assert rig.ctl.hold


def test_new_controller_resets_config_but_estop_persists(rig):
    rig.cmd("config", obstacle_stop=False, stop_cm=50, speed_limit=40)
    rig.cmd("estop")
    rig.ctl.new_controller(returning=False)
    assert rig.ctl.cfg == safety.DEFAULT_CONFIG and rig.ctl.estopped
    rig.ctl.cfg["stop_cm"] = 70
    rig.ctl.new_controller(returning=True)
    assert rig.ctl.cfg["stop_cm"] == 70                # a returning lease holder keeps its config


# ---- e-stop ------------------------------------------------------------------------------------------------

def test_estop_latches_and_reset_clears(rig):
    rig.cmd("drive", dir="forward", speed=30)
    rig.ctl.estop("viewer")
    assert rig.backend.duty == (0, 0) and ("estop", {"by": "viewer"}) in rig.events
    assert code(rig, "drive", dir="forward", speed=30) == "estop"
    rig.cmd("stop")                                    # stop and steer stay allowed
    rig.cmd("steer", angle=10)
    rig.cmd("estop_reset")
    assert "estop_reset" in rig.kinds()
    assert rig.cmd("drive", dir="forward", speed=30)


# ---- reverse budget -----------------------------------------------------------------------------------------

def test_reverse_budget_is_cumulative_then_refused_until_reset(rig):
    rig.cmd("drive", dir="backward", speed=30)
    rig.run(2.0)
    rig.cmd("drive", dir="backward", speed=20)         # a replacement command does not reset the budget
    rig.run(2.9)
    assert rig.backend.duty[0] < 0
    rig.run(0.3)
    assert rig.backend.duty == (0, 0) and "reverse_timeout" in rig.kinds()
    assert code(rig, "drive", dir="backward", speed=20) == "reverse_timeout"
    rig.run(1.2)                                       # stopped for >= 1 s resets
    assert rig.cmd("drive", dir="backward", speed=20)


def test_forward_motion_resets_reverse_budget(rig):
    rig.cmd("drive", dir="backward", speed=30)
    rig.run(4.0)
    rig.cmd("drive", dir="forward", speed=30)
    rig.run(0.2)
    assert rig.ctl._rev_used == 0.0


async def test_backward_move_ends_with_done_reverse_timeout(rig):
    rig.cmd("move", dir="backward", speed=30, ms=9000, _id=4)
    rig.run(5.3)
    await asyncio.sleep(0)
    assert (4, "reverse_timeout") in rig.dones


# ---- timed moves ----------------------------------------------------------------------------------------------

async def test_timed_move_completes(rig):
    assert rig.cmd("move", dir="forward", speed=30, ms=60, _id=3) == {"speed": 30, "ms": 60}
    assert rig.ctl.snapshot()["move"]["id"] == 3
    await asyncio.sleep(0.15)
    assert rig.dones == [(3, "completed")] and rig.backend.duty == (0, 0)
    assert rig.ctl.snapshot()["move"] is None


async def test_move_superseded_by_drive_and_move_and_stopped_by_stop(rig):
    rig.cmd("move", dir="forward", speed=30, ms=5000, _id=1)
    rig.cmd("drive", dir="forward", speed=40)
    rig.cmd("move", dir="forward", speed=30, ms=5000, _id=2)
    rig.cmd("move", dir="forward", speed=30, ms=5000, _id=3)
    rig.cmd("stop")
    assert rig.dones == [(1, "superseded"), (2, "superseded"), (3, "stopped")]
    await asyncio.sleep(0.05)
    assert len(rig.dones) == 3                         # cancelled timers never complete


async def test_move_ends_with_obstacle_and_estop(rig):
    rig.cmd("move", dir="forward", speed=30, ms=5000, _id=1)
    rig.sample(cm=10)
    assert rig.dones == [(1, "obstacle")]
    rig.cmd("drive", dir="backward", speed=20)
    rig.cmd("move", dir="backward", speed=20, ms=5000, _id=2)
    rig.ctl.estop("controller")
    assert rig.dones[-1] == (2, "estop")


async def test_stale_move_timer_cannot_touch_the_motors(rig):
    rig.cmd("move", dir="forward", speed=30, ms=80, _id=1)
    rig.cmd("drive", dir="backward", speed=30)         # supersedes; the old timer is cancelled
    await asyncio.sleep(0.2)
    assert rig.backend.duty[0] < 0


async def test_zero_speed_move_waits_then_completes(rig):
    rig.cmd("move", dir="forward", speed=0, ms=50, _id=5)
    assert rig.backend.duty == (0, 0)
    await asyncio.sleep(0.12)
    assert rig.dones == [(5, "completed")]


# ---- hardware faults -------------------------------------------------------------------------------------------

async def test_motor_write_failing_twice_latches_hw_fault(rig):
    rig.backend.fail_sets = 2
    assert code(rig, "drive", dir="forward", speed=30) == "hw_fault"
    assert rig.ctl.hw_fault and "hw_fault" in rig.kinds()
    assert code(rig, "drive", dir="forward", speed=30) == "hw_fault"
    rig.cmd("estop_reset")
    assert rig.ctl.hw_fault is None


def test_single_motor_write_failure_is_terminal(rig):
    rig.backend.fail_sets = 1   # the HAL already retried; the controller does not retry again
    assert code(rig, "drive", dir="forward", speed=30) == "hw_fault"
    assert rig.ctl.hw_fault


def test_stop_motors_false_latches_hw_fault_and_reset_needs_a_good_zero_write(rig):
    rig.backend.stop_result = False
    rig.cmd("stop")
    assert rig.ctl.hw_fault and "hw_fault" in rig.kinds()
    assert code(rig, "estop_reset") == "hw_fault"
    rig.backend.stop_result = True
    rig.cmd("estop_reset")
    assert rig.ctl.hw_fault is None


def test_car_unavailable():
    r = Rig()
    r.ctl.backend = None
    assert code(r, "drive", dir="forward", speed=30) == "hw_unavailable"
    r.cmd("stop")
    r.ctl.estop("api")
    assert r.ctl.snapshot()["safety"]["estop"]


# ---- deadlines (real backend probe) ---------------------------------------------------------------------------

def test_deadline_probe_and_latch_callback_wiring(rig):
    b = rig.backend
    assert b.probe() == (True, True)
    rig.cmd("drive", dir="forward", speed=30)
    rig.clock.advance(1.2)
    assert b.probe() == (False, False)
    b.latch_cb("controller_silent")                    # the sensor thread reports a deadline stop
    assert rig.ctl.hold and ("hold", {"reason": "watchdog"}) in rig.events and "watchdog_stop" in rig.kinds()


# ---- calibrate ----------------------------------------------------------------------------------------------------

def test_calibrate_sets_trim_and_saves(rig):
    saved = []
    rig.ctl._save_cal = saved.append
    rig.backend.set_trim = lambda d: saved.append(("trim", d))
    assert rig.cmd("calibrate", steer_trim_deg=2.5, save=True) == {"steer_trim_deg": 2.5}
    assert ("trim", 2.5) in saved and saved[-1]["steer_trim_deg"] == 2.5


def test_calibrate_refused_for_a_private_sim():
    r = Rig(shared=False)
    assert code(r, "calibrate", steer_trim_deg=1.0) == "bad_request"


# ---- dead reckoning and telemetry ----------------------------------------------------------------------------------

def test_dead_reckoning_straight_and_circle(rig):
    rig.cmd("drive", dir="forward", speed=100)
    for _ in range(100):
        rig.ctl.dead_reckon(0.01)
    s = rig.ctl.snapshot()["pose"]
    assert s["src"] == "dr" and s["x"] == pytest.approx(70.0, abs=0.2) and abs(s["y"]) < 0.1 and s["dir"] == 90.0
    rig.cmd("reset_pose")
    assert rig.ctl.snapshot()["pose"]["x"] == 0
    rig.cmd("steer", angle=30)
    rig.cmd("drive", dir="forward", speed=100)
    radius = rig.cal["wheelbase_cm"] / math.tan(math.radians(30))
    pts = []
    for _ in range(300):
        rig.ctl.dead_reckon(0.01)
        pts.append(tuple(rig.ctl._pose[:2]))
    # the heading is +x and the car steers right, so the circle centre is at (0, -radius)
    for x, y in pts[::30]:
        assert math.hypot(x, y + radius) == pytest.approx(radius, rel=0.01)
    assert safety.wrap_deg(rig.ctl.snapshot()["pose"]["dir"] - 90) > 0      # turned clockwise


def test_snapshot_shape(rig):
    rig.cmd("drive", dir="forward", speed=40)
    t = rig.ctl.snapshot()
    assert t["drive"]["dir"] == "forward" and t["drive"]["moving"] and t["drive"]["speed"] == 40
    assert t["dist"] == {"cm": 100.0, "state": "ok"}
    assert t["line"]["ok"] is False and t["light"]["ok"] is False
    s = t["safety"]
    assert set(s) == {"estop", "hold", "calibrated", "obstacle_stop", "stop_cm", "margin_cm", "speed_limit",
                      "blocked", "hw_fault"}
    assert s["margin_cm"] == pytest.approx(safety.margin_cm(20, 40, rig.cal), abs=0.05)
    assert t["obstacle"] is False and t["move"] is None


def test_snapshot_obstacle_flag_and_line_light(rig):
    from picar_server.hal.base import Sample
    rig.ctl._on_sample(Sample(rig.clock(), 12.0, DIST_OK, [700, 100, 100, 100, 100], [20, 200, 200]))
    t = rig.ctl.snapshot()
    assert t["obstacle"] is True
    assert t["line"]["on"] == [True, False, False, False, False] and t["line"]["pos"] == -2
    assert t["light"]["bright"] == [100, 0, 0] and t["light"]["max"] == "left"


def test_sensor_events_fault_and_recovery(rig):
    rig.sample(state=DIST_FAULT)
    rig.sample(cm=50)
    assert [e for e in rig.events if e[0] in ("sensor_fault", "sensor_ok")] == [
        ("sensor_fault", {"sensor": "ultrasonic"}), ("sensor_ok", {"sensor": "ultrasonic"})]
