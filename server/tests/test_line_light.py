"""Per-sensor light, light channel mapping, line polarity and thresholds, invalid line frames, line reads while
moving, live calibration over the protocol, the simulator's polarity and the calib.py maths."""
import copy
import json
import logging

import pytest

from conftest import Rig
from fakes_hw import FakeBus, FakeClock, FakeGPIO
from picar_server import config, protocol, safety
from picar_server.controller import CmdError
from picar_server.hal.base import DIST_OK, Sample
from picar_server.hal.real import RealBackend
from picar_server.hal.sim import world as W
from picar_server.hal.sim.backend import SimBackend
from tools import calib

CAL = config.merge(None)
GOOD = [0, 100] * 5                                     # five readings of 100
BAD = [0xFF] * 10                                       # the module's all-0xFF frame


def fr(*vals):
    out = []
    for v in vals:
        out += [v >> 8, v & 0xFF]
    return out


def make(cal=None):
    clock = FakeClock()
    bus, gpio = FakeBus(), FakeGPIO(clock)
    gpio.default = gpio.echo_cm(100)
    b = RealBackend(cal if cal is not None else {}, bus=bus, gpio=gpio, clock=clock)
    return b, bus, clock


def rblk(bus):
    return sum(1 for o in bus.ops if o[0] == "rblk")


def healthy(b, bus, n=10):
    """Stopped, line enabled and answering: n good probes."""
    bus.line_bytes = GOOD
    for _ in range(n):
        assert b.hub.read().line_raw == [100] * 5


# ---- config ---------------------------------------------------------------------------------------------------

def test_defaults_carry_the_measured_values():
    assert CAL["light_channels"] == [0, 1, 2] and CAL["line_black_high"] is True and CAL["line_threshold"] == 320


def test_old_scalar_calibration_file_still_loads(tmp_path):
    (tmp_path / "calibration.json").write_text(json.dumps(
        {"steer_trim_deg": 2.0, "line_enabled": True, "line_threshold": 300, "light_dark": 200, "light_bright": 20}))
    cal = config.load_calibration(tmp_path, tmp_path / "none.json")
    assert cal["line_threshold"] == 300 and cal["light_dark"] == 200 and cal["light_bright"] == 20
    assert cal["light_channels"] == [0, 1, 2] and cal["line_black_high"] is True and cal["line_enabled"] is True


def test_per_sensor_values_merge_and_bad_shapes_are_ignored():
    cal = config.merge({"light_dark": [88, 250, 252], "light_bright": [3, 19, 19],
                        "line_threshold": [300, 310, 320, 330, 340], "light_channels": [0, 2, 1],
                        "line_black_high": False})
    assert cal["light_dark"] == [88, 250, 252] and cal["light_channels"] == [0, 2, 1]
    assert cal["line_threshold"] == [300, 310, 320, 330, 340] and cal["line_black_high"] is False
    bad = config.merge({"light_dark": [1, 2], "light_bright": [1, "x", 3], "line_threshold": [1, 2, 3],
                        "light_channels": [0, 0, 1], "line_black_high": 1})
    assert bad == config.merge(None)
    for ch in ([0, 1, 4], [0, 1], [0, 1, True], [0.0, 1, 2], "012", [-1, 1, 2]):
        assert config.merge({"light_channels": ch})["light_channels"] == [0, 1, 2]


def test_saved_per_sensor_calibration_round_trips(tmp_path):
    cal = config.merge({"light_dark": [88, 250, 252], "light_channels": [0, 2, 1]})
    config.save_calibration(tmp_path, cal)
    assert config.load_calibration(tmp_path) == cal


# ---- light ---------------------------------------------------------------------------------------------------

def test_brightness_is_per_sensor():
    cal = config.merge({"light_dark": [88, 250, 252], "light_bright": [3, 19, 19]})
    raw = [88, 250, 19]
    assert [safety.brightness(v, cal, i) for i, v in enumerate(raw)] == [0, 0, 100]
    assert [safety.brightness(v, cal, i) for i, v in enumerate([3, 19, 135])] == [100, 100, 50]
    assert safety.brightness(110, CAL) == 50                      # scalar calibration still works
    assert safety.brightness(110, CAL, 2) == 50


def test_light_channels_map_inputs_to_left_middle_right():
    b, bus, _ = make({"light_channels": [0, 2, 1]})                # this car: middle = input 2, right = input 1
    bus.pcf_values = [11, 22, 33, 44]
    bus.ops.clear()
    s = b.hub.read()
    assert s.light_raw == [11, 33, 22]                              # left, middle, right
    assert [o[2] for o in bus.ops if o[0] == "wb"] == [0x40, 0x42, 0x41]


def test_light_channels_follow_live_calibration_and_bad_values_fall_back():
    cal = {}
    b, bus, _ = make(cal)
    bus.pcf_values = [11, 22, 33, 44]
    assert b.hub.read().light_raw == [11, 22, 33]
    cal["light_channels"] = [3, 1, 0]
    assert b.hub.read().light_raw == [44, 22, 11]
    cal["light_channels"] = [1, 1, 1]
    assert b.hub.read().light_raw == [11, 22, 33]


# ---- line polarity and thresholds -----------------------------------------------------------------------------

def test_line_polarity_and_per_sensor_thresholds():
    raw = [330, 330, 330, 310, 100]
    assert safety.line_on(raw, 320) == [True, True, True, False, False]
    assert safety.line_on(raw, 320, black_high=False) == [False, False, False, True, True]
    assert safety.line_on(raw, [340, 300, 320, 300, 50]) == [False, True, True, True, True]
    assert safety.line_on([320] * 5, 320) == [False] * 5            # equal is not "on" either way
    assert safety.line_on([320] * 5, 320, black_high=False) == [False] * 5


def test_snapshot_uses_per_sensor_threshold_polarity_and_brightness():
    r = Rig()
    r.cal.update(line_threshold=[300, 300, 300, 300, 500], light_dark=[100, 200, 300], light_bright=[0, 100, 200])
    r.ctl._on_sample(Sample(r.clock(), 100.0, DIST_OK, [400, 100, 100, 400, 400], [50, 150, 250]))
    t = r.ctl.snapshot()
    assert t["line"]["on"] == [True, False, False, True, False] and t["line"]["pos"] == -0.5
    assert t["light"]["bright"] == [50, 50, 50]
    r.cal["line_black_high"] = False
    assert r.ctl.snapshot()["line"]["on"] == [False, True, True, False, True]


# ---- invalid frames --------------------------------------------------------------------------------------------

def test_invalid_frame_is_retried_once_at_once():
    b, bus, _ = make({"line_enabled": True})
    bus.line_bytes = GOOD
    b.hub.read()
    n = rblk(bus)
    bus.line_script.extend([BAD, fr(1, 2, 3, 4, 5)])
    assert b.hub.read().line_raw == [1, 2, 3, 4, 5]
    assert rblk(bus) == n + 2 and b.capabilities()["line"] is True


def test_value_over_1023_is_invalid():
    b, bus, _ = make({"line_enabled": True})
    bus.line_bytes = GOOD
    b.hub.read()
    bus.line_script.extend([fr(1, 2, 3, 4, 1024), fr(1, 2, 3, 4, 1023)])
    assert b.hub.read().line_raw == [1, 2, 3, 4, 1023]


def test_still_invalid_frame_is_a_skipped_sample_not_a_loss():
    b, bus, clock = make({"line_enabled": True})
    healthy(b, bus, 1)
    probes = rblk(bus)
    bus.line_script.extend([BAD, BAD])
    assert b.hub.read().line_raw is None
    assert rblk(bus) == probes + 2
    assert b.capabilities()["line"] is True and b.hub.line_ok          # not flipped, no backoff
    assert b.hub.read().line_raw == [100] * 5                           # next sample probes again, at once


def test_three_consecutive_invalid_samples_count_as_absent():
    b, bus, clock = make({"line_enabled": True})
    healthy(b, bus, 1)
    bus.line_script.extend([BAD] * 6)
    assert [b.hub.read().line_raw for _ in range(3)] == [None] * 3
    assert b.hub.line_ok is False and b.capabilities()["line"] is False
    n = rblk(bus)
    b.hub.read()
    assert rblk(bus) == n                                               # backing off (2 s)
    clock.advance(2.5)
    assert b.hub.read().line_raw == [100] * 5 and b.hub.line_ok


def test_a_good_frame_resets_the_invalid_run():
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus, 1)
    for _ in range(4):
        bus.line_script.extend([BAD, BAD])
        assert b.hub.read().line_raw is None
        assert b.hub.read().line_raw == [100] * 5
    assert b.hub.line_ok


# ---- line read while moving ------------------------------------------------------------------------------------

def test_not_read_while_moving_until_ten_good_reads():
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus, 9)
    b.set_motors(1000, 1000)
    n = rblk(bus)
    assert b.hub.read().line_raw is None and rblk(bus) == n
    b.stop_motors()
    assert b.hub.read().line_raw == [100] * 5                           # the tenth good read
    b.set_motors(-500, -500)                                            # any motion counts
    assert b.hub.read().line_raw == [100] * 5 and rblk(bus) == n + 2


def test_moving_read_takes_only_the_bus_lock():
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus)
    seen = []

    def spy():
        got_motor = b._motor_lock.acquire(blocking=False)
        if got_motor:
            b._motor_lock.release()
        got_bus = b.lock.acquire(blocking=False)
        if got_bus:
            b.lock.release()
        seen.append((got_motor, got_bus))

    bus.on_line_read = spy
    b.set_motors(1000, 1000)
    assert b.hub.read().line_raw == [100] * 5
    assert seen == [(True, False)]                                      # motor lock free, bus lock held
    b.stop_motors()
    seen.clear()
    b.hub.read()
    assert seen == [(False, False)]                                     # stopped probe holds both


def test_one_quick_error_is_retried_at_once_moving_and_stopped():
    # Measured: about 1 sample in 300 gets EIO from a module that is present; the retry reads it fine.
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus)
    bus.line_script.extend([None, fr(7, 7, 7, 7, 7)])
    assert b.hub.read().line_raw == [7] * 5 and b.hub.line_ok
    b.set_motors(1000, 1000)
    bus.line_script.extend([None, fr(8, 8, 8, 8, 8)])
    assert b.hub.read().line_raw == [8] * 5
    assert b.hub.read().line_raw == [100] * 5                           # not suppressed


def test_a_failed_sample_is_skipped_not_a_loss_moving_and_stopped(caplog):
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus)
    caplog.set_level(logging.WARNING, logger="picar.sensors")
    bus.line_script.extend([None, None])                                # stopped: error twice
    assert b.hub.read().line_raw is None and b.hub.line_skipped and b.hub.line_ok
    assert b.hub.read().line_raw == [100] * 5
    b.set_motors(1000, 1000)
    bus.line_script.extend([None, BAD])                                 # moving: error, then an invalid frame
    assert b.hub.read().line_raw is None and b.hub.line_skipped
    assert b.hub.read().line_raw == [100] * 5                           # still read while moving
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_moving_three_failed_samples_suppress_until_stop_then_need_ten_good_reads(caplog):
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus)
    caplog.set_level(logging.WARNING, logger="picar.sensors")
    b.set_motors(1000, 1000)
    bus.line_script.extend([None] * 6)
    assert [b.hub.read().line_raw for _ in range(3)] == [None] * 3
    n = rblk(bus)
    for _ in range(5):
        assert b.hub.read().line_raw is None
    assert rblk(bus) == n                                               # suppressed: no more bus traffic
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    b.stop_motors()
    assert b.hub.read().line_raw == [100] * 5                           # stopped probe works
    b.set_motors(1000, 1000)
    assert b.hub.read().line_raw is None                                # only 1 good read since the failure
    b.stop_motors()
    for _ in range(9):
        b.hub.read()
    b.set_motors(1000, 1000)
    assert b.hub.read().line_raw == [100] * 5                           # healthy again


def test_moving_two_invalid_frames_are_one_skipped_sample():
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus)
    b.set_motors(1000, 1000)
    bus.line_script.extend([BAD, BAD])
    assert b.hub.read().line_raw is None and b.hub.line_skipped
    assert b.hub.read().line_raw == [100] * 5


def test_moving_one_invalid_frame_is_retried_and_does_not_suppress():
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus)
    b.set_motors(1000, 1000)
    bus.line_script.extend([BAD, fr(7, 7, 7, 7, 7)])
    assert b.hub.read().line_raw == [7] * 5
    assert b.hub.read().line_raw == [100] * 5


def test_moving_slow_read_suppresses_with_a_fake_clock():
    b, bus, clock = make({"line_enabled": True})
    healthy(b, bus)
    b.set_motors(1000, 1000)
    bus.on_line_read = lambda: clock.advance(0.020)                     # the bus call takes 20 ms
    assert b.hub.read().line_raw == [100] * 5                           # this frame is valid, so it is used
    n = rblk(bus)
    assert b.hub.read().line_raw is None and rblk(bus) == n            # then moving reads stop
    bus.on_line_read = lambda: clock.advance(0.010)                     # 10 ms is fine after the next stop
    b.stop_motors()
    for _ in range(10):
        assert b.hub.read().line_raw == [100] * 5
    b.set_motors(1000, 1000)
    assert b.hub.read().line_raw == [100] * 5 and b.hub.read().line_raw == [100] * 5


def test_moving_reads_need_line_enabled_and_a_present_module():
    cal = {"line_enabled": True}
    b, bus, _ = make(cal)
    healthy(b, bus)
    b.set_motors(1000, 1000)
    cal["line_enabled"] = False
    n = rblk(bus)
    assert b.hub.read().line_raw is None and rblk(bus) == n
    b2, bus2, _ = make({"line_enabled": True})
    bus2.line_bytes = None                                              # absent at the stop: never read moving
    b2.hub.read()
    b2.set_motors(1000, 1000)
    n2 = rblk(bus2)
    assert b2.hub.read().line_raw is None and rblk(bus2) == n2


# ---- controller: a skipped frame is not a sensor fault ------------------------------------------------------------

def line_sample(r, raw, skipped=False):
    r.clock.advance(0.04)
    r.ctl._on_sample(Sample(r.clock(), 100.0, DIST_OK, raw, None, line_skipped=skipped))


def test_a_single_missing_line_frame_neither_faults_nor_blanks_telemetry():
    r = Rig()
    line_sample(r, [400] * 5)
    line_sample(r, None, skipped=True)
    assert "sensor_fault" not in r.kinds() and r.ctl.snapshot()["line"]["ok"] is True
    line_sample(r, None, skipped=True)
    line_sample(r, None, skipped=True)
    assert ("sensor_fault", {"sensor": "line"}) in r.events          # three in a row
    r.clock.advance(0.5)
    line_sample(r, None, skipped=True)
    assert r.ctl.snapshot()["line"]["ok"] is False                   # held frame expired
    line_sample(r, [400] * 5)
    assert ("sensor_ok", {"sensor": "line"}) in r.events


def test_two_skipped_frames_in_a_row_are_never_bridged():
    # re-review: two skips 40 ms apart both fell inside the 0.1 s hold and kept a stale frame marked ok
    r = Rig()
    line_sample(r, [400] * 5)
    line_sample(r, None, skipped=True)
    assert r.ctl.snapshot()["line"]["ok"] is True                    # one skip: bridged
    line_sample(r, None, skipped=True)
    assert r.ctl.snapshot()["line"]["ok"] is False                   # the second: not bridged


def test_a_module_never_seen_raises_no_line_event():
    r = Rig()
    for _ in range(6):
        line_sample(r, None)
    assert not [e for e in r.events if e[1].get("sensor") == "line"]


# ---- calibrate over the protocol ---------------------------------------------------------------------------------

def cmd(**f):
    return protocol.validate_cmd({"t": "cmd", "id": 1, "op": "calibrate", **f})[2]


def test_calibrate_validation_accepts_the_new_fields():
    f = cmd(light_channels=[0, 2, 1], light_dark=[88, 250, 252], light_bright=5, line_black_high=True,
            line_threshold=[300, 310, 320, 330, 340], save=True)
    assert f == {"light_channels": [0, 2, 1], "light_dark": [88, 250, 252], "light_bright": 5,
                 "line_black_high": True, "line_threshold": [300, 310, 320, 330, 340], "save": True}
    assert cmd(line_threshold=320) == {"line_threshold": 320}


@pytest.mark.parametrize("bad", [
    {"light_channels": [0, 0, 1]}, {"light_channels": [0, 1]}, {"light_channels": [0, 1, 4]},
    {"light_channels": "012"}, {"light_channels": [0, 1, True]},
    {"light_dark": [1, 2]}, {"light_dark": "x"}, {"light_dark": 256}, {"light_bright": [1, 2, -1]},
    {"line_threshold": [1, 2, 3]}, {"line_threshold": 1024}, {"line_threshold": [1, 2, 3, 4, None]},
    {"line_black_high": 1}, {"line_black_high": "true"}, {"light_dark": True},
])
def test_calibrate_validation_rejects_bad_shapes(bad):
    with pytest.raises(protocol.ProtocolError) as e:
        cmd(**bad)
    assert e.value.code == "bad_request"


def test_calibrate_applies_live_and_saves():
    r = Rig()
    saved = []
    r.ctl._save_cal = lambda c: saved.append(copy.deepcopy(c))
    out = r.cmd("calibrate", light_channels=[0, 2, 1], light_dark=[88, 250, 252], light_bright=[3, 19, 19],
                line_black_high=False, line_threshold=250)
    assert r.cal["light_channels"] == [0, 2, 1] and r.cal["line_black_high"] is False
    assert out["light_channels"] == [0, 2, 1] and out["line_threshold"] == 250 and not saved
    r.cmd("calibrate", save=True)
    assert saved[-1]["light_dark"] == [88, 250, 252] and saved[-1]["line_threshold"] == 250
    assert config.merge(saved[-1]) == saved[-1]                       # what is saved loads back unchanged


def test_calibrate_reaches_the_sensor_thread_through_the_shared_dict():
    r = Rig()
    clock = FakeClock()
    bus, gpio = FakeBus(), FakeGPIO(clock)
    gpio.default = gpio.echo_cm(100)
    b = RealBackend(r.cal, bus=bus, gpio=gpio, clock=clock)           # same dict as the controller (app.py does this)
    bus.pcf_values = [11, 22, 33, 44]
    r.ctl._save_cal = lambda c: None
    r.cmd("calibrate", light_channels=[2, 0, 1], save=True)
    assert b.hub.read().light_raw == [33, 11, 22]


# ---- simulator polarity ---------------------------------------------------------------------------------------------

def test_sim_line_matches_the_default_calibration():
    cal = copy.deepcopy(CAL)
    on_sim = SimBackend(W.load_world("oval"), copy.deepcopy(cal), seed=1)
    on = safety.line_on(on_sim.read_line(), cal["line_threshold"], cal["line_black_high"])
    assert on == [False, False, True, False, False]
    on_sim.teleport(0, 0, 90)                                           # off the line
    assert safety.line_on(on_sim.read_line(), cal["line_threshold"], cal["line_black_high"]) == [False] * 5


# ---- calib.py maths ---------------------------------------------------------------------------------------------------

def test_medians():
    assert calib.medians([[1, 10], [3, 30], [2, 20]]) == [2, 20]
    with pytest.raises(calib.CalibError):
        calib.medians([])


def test_infer_light_finds_this_cars_wiring():
    # all four inputs; measured on the car: left = input 3, middle = input 2, right = input 1; input 0 is a light
    # sensor on the converter board that a torch near the left or right sensor also lights
    room = [90, 252, 250, 252]
    torch = {"left": [60, 252, 250, 13], "middle": [88, 252, 19, 252], "right": [5, 19, 250, 252]}
    assert calib.infer_light(room, torch, ignore=(0,)) == {
        "light_channels": [3, 2, 1], "light_dark": [252, 250, 252], "light_bright": [13, 19, 19]}


def test_infer_light_without_ignore_can_pick_the_board_sensor():
    # the reason for --ignore-inputs: torch spill on the board sensor (input 0) wins for "right"
    room = [90, 252, 250, 252]
    torch = {"left": [88, 252, 250, 13], "middle": [88, 252, 19, 252], "right": [0, 200, 250, 252]}
    assert calib.infer_light(room, torch)["light_channels"] == [3, 2, 0]
    assert calib.infer_light(room, torch, ignore=(0,))["light_channels"] == [3, 2, 1]


def test_infer_light_refuses_when_too_few_inputs_remain():
    with pytest.raises(calib.CalibError):
        calib.infer_light([90, 252, 250, 252], {}, ignore=(0, 1))


def test_infer_light_refuses_weak_or_ambiguous_torches():
    room = [88, 252, 250]
    weak = {"left": [80, 252, 250], "middle": [88, 252, 19], "right": [88, 19, 250]}
    with pytest.raises(calib.CalibError):
        calib.infer_light(room, weak)
    same = {"left": [3, 252, 250], "middle": [3, 252, 250], "right": [88, 19, 250]}
    with pytest.raises(calib.CalibError):
        calib.infer_light(room, same)


def test_infer_line_black_high_midpoint_thresholds():
    white = [200, 190, 180, 200, 210]
    hi = [420, 421, 425, 424, 420]
    lo = [195, 185, 178, 198, 205]
    out = calib.infer_line(white, hi, lo)
    assert out["line_black_high"] is True
    assert out["line_threshold"] == [310, 306, 302, 312, 315]


def test_infer_line_detects_inverted_polarity():
    white = [420] * 5
    out = calib.infer_line(white, [425] * 5, [200] * 5)
    assert out["line_black_high"] is False and out["line_threshold"] == [310] * 5


def test_infer_line_refuses_a_sensor_too_far_from_the_ground():
    white = [423] * 5
    with pytest.raises(calib.CalibError) as e:
        calib.infer_line(white, [425, 424, 423, 430, 440], [420, 421, 423, 420, 400])
    assert "sensor too far from the ground or IR LEDs not powered" in str(e.value) and "[1, 2, 3, 4, 5]" in str(e.value)


# ---- calib.py end to end against a fake car: calibrates, never moves ---------------------------------------------

def _fake_car(log, state):
    import asyncio
    from aiohttp import WSMsgType, web

    async def ws(request):
        sock = web.WebSocketResponse()
        await sock.prepare(request)

        async def tel():
            seq = 0
            while not sock.closed:
                seq += 1
                light = ([state["inputs"][i] for i in state.get("map", [0, 1, 2])] if "inputs" in state
                         else state["light"])          # with "inputs", the fake honours the live input map
                await sock.send_json({"t": "tel", "seq": seq, "line": {"ok": True, "raw": state["line"]},
                                      "light": {"ok": True, "raw": light}})
                await asyncio.sleep(0.01)

        task = None
        async for msg in sock:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            log.append(m)
            if m["t"] == "hello":
                await sock.send_json({"t": "welcome", "v": 1, "role": "controller", "car_available": True,
                                      "hw": {"backend": "real"}, "config": {}, "hb_ms": 200, "server": {}})
                task = asyncio.create_task(tel())
            elif m["t"] == "cmd":
                if m.get("op") == "calibrate" and "light_channels" in m:
                    state["map"] = list(m["light_channels"])
                extra = ({"previous": {"light_channels": state["previous"]}}
                         if "previous" in state and "light_channels" in m else {})
                await sock.send_json({"t": "ack", "id": m["id"], "ok": True, **extra})
        if task:
            task.cancel()
        return sock

    app = web.Application()
    app.router.add_get("/ws", ws)
    return app


async def test_calib_line_saves_polarity_and_thresholds_and_never_moves(aiohttp_client, monkeypatch):
    import asyncio
    log, state = [], {"line": [200] * 5, "light": [1, 2, 3]}
    c = await aiohttp_client(_fake_car(log, state))
    url = f"ws://{c.server.host}:{c.server.port}/ws"
    asked = []

    async def fake_ask(_prompt):
        asked.append(_prompt)
        if len(asked) == 2:
            state["line"] = [420] * 5
            await asyncio.sleep(0.15)
        return ""
    monkeypatch.setattr(calib, "ask", fake_ask)
    a = type("A", (), {"url": url, "client_id": None, "what": "line", "seconds": 0.2})()
    assert await calib.run(a) == 0
    cal = [m for m in log if m.get("op") == "calibrate"]
    assert cal[-1]["save"] is True and cal[-1]["line_black_high"] is True and cal[-1]["line_threshold"] == [310] * 5
    assert not {"drive", "move", "steer", "resume", "set_speed"} & {m.get("op") for m in log}
    assert sum(1 for m in log if m["t"] == "hb") >= 1


async def test_calib_light_saves_mapping_and_never_moves(aiohttp_client, monkeypatch):
    # this car: left = input 3, middle = input 2, right = input 1, input 0 = a light sensor on the board
    log, state = [], {"line": None, "inputs": [90, 252, 250, 252]}
    c = await aiohttp_client(_fake_car(log, state))
    url = f"ws://{c.server.host}:{c.server.port}/ws"
    steps = iter([[90, 252, 250, 252], [60, 252, 250, 13], [88, 252, 19, 252], [5, 19, 250, 252]])

    async def fake_ask(_prompt):
        state["inputs"] = next(steps)
        return ""
    monkeypatch.setattr(calib, "ask", fake_ask)
    a = type("A", (), {"url": url, "client_id": None, "what": "light", "seconds": 0.3, "ignore_inputs": "0"})()
    assert await calib.run(a) == 0
    cal = [m for m in log if m.get("op") == "calibrate"]
    assert cal[0]["light_channels"] == [0, 1, 2] and "save" not in cal[0]
    assert {tuple(m["light_channels"]) for m in cal[1:-1]} == {(0, 1, 2), (3, 1, 2)}   # all four inputs scanned
    assert cal[-1]["light_channels"] == [3, 2, 1] and cal[-1]["light_dark"] == [252, 250, 252]
    assert cal[-1]["light_bright"] == [13, 19, 19] and cal[-1]["save"] is True
    assert not {"drive", "move", "steer", "resume", "set_speed"} & {m.get("op") for m in log}


async def test_calib_line_refuses_to_save_when_the_sensors_do_not_move(aiohttp_client, monkeypatch):
    log, state = [], {"line": [423] * 5, "light": None}
    c = await aiohttp_client(_fake_car(log, state))
    url = f"ws://{c.server.host}:{c.server.port}/ws"

    async def fake_ask(_prompt):
        return ""
    monkeypatch.setattr(calib, "ask", fake_ask)
    a = type("A", (), {"url": url, "client_id": None, "what": "line", "seconds": 0.1})()
    assert await calib.run(a) == 1
    assert not [m for m in log if m.get("op") == "calibrate"]


def test_telemetry_hold_is_one_skipped_sample_only():
    r = Rig()
    line_sample(r, [400] * 5)
    line_sample(r, None, skipped=True)
    assert r.ctl.snapshot()["line"]["ok"] is True                      # one skipped frame is bridged
    line_sample(r, None, skipped=True)
    line_sample(r, None, skipped=True)
    assert r.ctl.snapshot()["line"]["ok"] is False                     # 0.12 s old: not held any more


def test_no_hold_when_the_module_is_suppressed_or_failed():
    r = Rig()
    line_sample(r, [400] * 5)
    line_sample(r, None)                                               # not a skipped frame: suppressed / absent
    assert r.ctl.snapshot()["line"] == {"ok": False, "raw": None, "on": None, "pos": None}


# ---- slow bus calls are never retried -----------------------------------------------------------------------------

def test_moving_slow_invalid_read_is_not_retried():
    b, bus, clock = make({"line_enabled": True})
    healthy(b, bus)
    b.set_motors(1000, 1000)
    bus.line_script.extend([BAD, fr(7, 7, 7, 7, 7)])
    bus.on_line_read = lambda: clock.advance(0.020)
    n = rblk(bus)
    assert b.hub.read().line_raw is None
    assert rblk(bus) == n + 1                                          # the bus lock was taken once, no retry
    bus.on_line_read = None
    assert b.hub.read().line_raw is None and rblk(bus) == n + 1       # and moving reads are off


def test_stopped_slow_invalid_read_is_not_retried_and_is_a_skipped_sample():
    b, bus, clock = make({"line_enabled": True})
    healthy(b, bus, 1)
    n = rblk(bus)
    bus.line_script.extend([BAD, fr(7, 7, 7, 7, 7)])
    bus.on_line_read = lambda: clock.advance(0.020)
    s = b.hub.read()
    assert s.line_raw is None and s.line_skipped and rblk(bus) == n + 1 and b.hub.line_ok


def test_slow_standstill_reads_do_not_qualify_for_moving_reads():
    b, bus, clock = make({"line_enabled": True})
    bus.line_bytes = GOOD
    bus.on_line_read = lambda: clock.advance(0.020)
    for _ in range(12):
        assert b.hub.read().line_raw == [100] * 5                      # valid data is still reported
    b.set_motors(1000, 1000)
    n = rblk(bus)
    assert b.hub.read().line_raw is None and rblk(bus) == n            # never became healthy
    b.stop_motors()
    bus.on_line_read = None
    for _ in range(10):
        b.hub.read()
    b.set_motors(1000, 1000)
    assert b.hub.read().line_raw == [100] * 5


def test_a_skipped_frame_sets_the_flag_and_other_nones_do_not():
    b, bus, _ = make({"line_enabled": True})
    healthy(b, bus, 1)
    bus.line_script.extend([BAD, BAD])
    assert b.hub.read().line_skipped is True
    bus.line_bytes = None
    s = b.hub.read()
    assert s.line_raw is None and s.line_skipped is True               # errors on a present module: skipped ...
    s = b.hub.read()
    assert s.line_raw is None and s.line_skipped is False              # ... until the third failed sample: a loss
    assert b.hub.line_ok is False


# ---- huge numbers --------------------------------------------------------------------------------------------------

BIG = int("1" + "0" * 400)


@pytest.mark.parametrize("key", ["light_dark", "light_bright", "line_threshold", "steer_trim_deg"])
def test_oversized_integers_are_bad_request_not_a_crash(key):
    for v in (BIG, [BIG] * 3, [BIG] * 5):
        with pytest.raises(protocol.ProtocolError) as e:
            cmd(**{key: v})
        assert e.value.code == "bad_request"
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_cmd({"t": "cmd", "id": 1, "op": "drive", "dir": "forward", "speed": BIG})


def test_oversized_integers_in_the_calibration_file_are_ignored():
    cal = config.merge({"light_dark": BIG, "line_threshold": [BIG] * 5, "wheelbase_cm": BIG})
    assert cal == config.merge(None)


# ---- calib.py puts the light wiring back when it does not finish --------------------------------------------------

def _light_args(url, seconds=0.1):
    return type("A", (), {"url": url, "client_id": None, "what": "light", "seconds": seconds})()


async def test_calib_light_restores_the_wiring_when_it_fails(aiohttp_client, monkeypatch):
    log, state = [], {"line": None, "light": [88, 252, 250], "previous": [0, 2, 1]}
    c = await aiohttp_client(_fake_car(log, state))
    url = f"ws://{c.server.host}:{c.server.port}/ws"

    async def fake_ask(_prompt):
        return ""                                                       # no torch ever changes the readings
    monkeypatch.setattr(calib, "ask", fake_ask)
    assert await calib.run(_light_args(url)) == 1
    cal = [m for m in log if m.get("op") == "calibrate"]
    assert cal[0]["light_channels"] == [0, 1, 2] and cal[-1]["light_channels"] == [0, 2, 1]
    assert "save" not in cal[-1]


async def test_calib_light_restores_the_wiring_on_an_interrupted_prompt(aiohttp_client, monkeypatch):
    log, state = [], {"line": None, "light": [88, 252, 250], "previous": [0, 2, 1]}
    c = await aiohttp_client(_fake_car(log, state))
    url = f"ws://{c.server.host}:{c.server.port}/ws"

    async def fake_ask(_prompt):
        raise EOFError
    monkeypatch.setattr(calib, "ask", fake_ask)
    with pytest.raises(EOFError):
        await calib.run(_light_args(url))
    cal = [m for m in log if m.get("op") == "calibrate"]
    assert cal[-1]["light_channels"] == [0, 2, 1]


async def test_calib_light_does_not_restore_after_a_save(aiohttp_client, monkeypatch):
    log, state = [], {"line": None, "light": [88, 252, 250], "previous": [0, 2, 1]}
    c = await aiohttp_client(_fake_car(log, state))
    url = f"ws://{c.server.host}:{c.server.port}/ws"
    steps = iter([[88, 252, 250], [3, 252, 250], [88, 252, 19], [88, 19, 250]])

    async def fake_ask(_prompt):
        state["light"] = next(steps)
        return ""
    monkeypatch.setattr(calib, "ask", fake_ask)
    assert await calib.run(_light_args(url)) == 0
    cal = [m for m in log if m.get("op") == "calibrate"]
    assert cal[-1]["save"] is True                                   # the save is the last command
    assert not [m for m in cal if m.get("light_channels") == [0, 2, 1] and not m.get("save")]   # no restore


def test_calibrate_ack_reports_previous_values_and_reverts_a_failed_save():
    r = Rig()
    out = r.cmd("calibrate", light_channels=[0, 2, 1])
    assert out["previous"] == {"light_channels": [0, 1, 2]}

    def boom(_):
        raise OSError("disk full")
    r.ctl._save_cal = boom
    with pytest.raises(CmdError):
        r.cmd("calibrate", light_channels=[2, 1, 0], line_threshold=100, save=True)
    assert r.cal["light_channels"] == [0, 2, 1] and r.cal["line_threshold"] == 320
