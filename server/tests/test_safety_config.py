"""Pure helpers (safety.py), calibration (config.py) and protocol validation."""
import json

import pytest

from picar_server import config, protocol, safety
from picar_server.hal.base import DIST_FAR, DIST_FAULT, DIST_NONE, DIST_OK

CAL = config.merge(None)


def test_interp_clamps_and_interpolates():
    t = [[0, 0], [10, 100], [20, 100]]
    assert safety.interp(t, -5) == 0 and safety.interp(t, 5) == 50 and safety.interp(t, 99) == 100


def test_speed_to_duty_mapping():
    assert safety.speed_to_duty(0, CAL) == 0
    assert safety.speed_to_duty(100, CAL) == 4095
    assert safety.speed_to_duty(50, CAL) == round(4095 * 0.625)
    assert safety.speed_to_duty(1, CAL) == round(4095 * 0.2575)
    assert safety.speed_to_duty(500, CAL) == 4095
    floor_only = config.merge({"duty_floor_pct": 40, "duty_ceiling_pct": 80})
    assert safety.speed_to_duty(100, floor_only) == round(4095 * 0.8)
    assert safety.duty_to_speed(safety.speed_to_duty(50, CAL), CAL) == pytest.approx(50, abs=0.1)


def test_margin_chain_uses_effective_speed_duty_table_and_0_2s():
    # speed 100: coast 12 cm, 70 cm/s * 0.2 s = 14 cm
    assert safety.margin_cm(20, 100, CAL) == pytest.approx(46.0)
    # speed 50 -> duty 62.5% -> 37.5 cm/s -> 7.5 cm, coast 3 cm
    assert safety.margin_cm(20, 50, CAL) == pytest.approx(30.5)
    assert safety.margin_cm(20, 0, CAL) == 20


def test_effective_speed_limit_and_reverse_cap():
    assert safety.effective_speed("forward", 90, 60) == 60
    assert safety.effective_speed("backward", 90, 100) == 40
    assert safety.effective_speed("backward", 20, 100) == 20


@pytest.mark.parametrize("state,cm,fwd,guard,stale,expect", [
    (DIST_OK, 10, True, True, False, "obstacle"),
    (DIST_OK, 10, False, True, False, None),           # not moving forward: no stop
    (DIST_OK, 10, True, False, False, None),           # guard off ignores distance
    (DIST_OK, 60, True, True, False, None),
    (DIST_FAR, None, True, True, False, None),
    (DIST_FAULT, None, True, True, False, "sensor_fault"),
    (DIST_FAULT, None, True, False, False, "sensor_fault"),   # faults stay on with the guard off
    (DIST_NONE, None, True, True, True, "sensor_fault"),
    (DIST_NONE, None, True, True, False, None),        # one missed ping inside 150 ms is tolerated
    (DIST_FAULT, None, False, True, False, None),
])
def test_guard_decision_table(state, cm, fwd, guard, stale, expect):
    assert safety.guard_decision(state, cm, 30.0, moving_forward=fwd, distance_guard=guard, stale=stale) == expect


def test_clear_sample_rules():
    assert safety.is_clear_sample(DIST_FAR, None, 30, True)
    assert safety.is_clear_sample(DIST_OK, 35, 30, True)
    assert not safety.is_clear_sample(DIST_OK, 34.9, 30, True)
    assert safety.is_clear_sample(DIST_OK, 5, 30, False)
    assert not safety.is_clear_sample(DIST_NONE, None, 30, True)
    assert not safety.is_clear_sample(DIST_FAULT, None, 30, False)


def test_steer_light_and_line_helpers():
    assert safety.clamp_steer(99) == 45 and safety.clamp_steer(-99) == -45
    assert safety.brightness(200, CAL) == 0 and safety.brightness(20, CAL) == 100 and safety.brightness(110, CAL) == 50
    assert safety.brightest([10, 60, 30]) == "middle" and safety.brightest([60, 10, 30]) == "left"
    assert safety.brightest([1, 2, 3]) == "none"
    assert safety.line_on([100, 500, 299, 300, 700], 300) == [True, False, True, False, False]
    assert safety.line_pos([False, False, True, False, False]) == 0
    assert safety.line_pos([True, True, False, False, False]) == -1.5
    assert safety.line_pos([False] * 5) is None
    assert safety.wrap_deg(190) == -170 and safety.wrap_deg(-180) == 180


# ---- calibration -----------------------------------------------------------------------------------------------

def test_defaults_when_missing(tmp_path):
    cal = config.load_calibration(tmp_path / "cfg", legacy_path=tmp_path / "nope.json")
    assert cal == config.DEFAULT_CALIBRATION and cal["calibrated"] is False and cal["line_enabled"] is False


def test_save_and_load_round_trip_and_invalid_entries_ignored(tmp_path):
    cal = config.merge({"steer_trim_deg": 2.5})
    config.save_calibration(tmp_path, cal)
    assert config.load_calibration(tmp_path)["steer_trim_deg"] == 2.5
    (tmp_path / "calibration.json").write_text(json.dumps(
        {"steer_trim_deg": "x", "speed_table": [[5, 1], [1, 2]], "calibrated": 1, "bogus": 1, "coast_table": [[0, 0], [50, 9]]}))
    cal = config.load_calibration(tmp_path)
    assert cal["steer_trim_deg"] == 0.0 and cal["speed_table"] == config.DEFAULT_CALIBRATION["speed_table"]
    assert cal["calibrated"] is False and "bogus" not in cal and cal["coast_table"] == [[0, 0], [50, 9]]


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "calibration.json").write_text("{not json")
    assert config.load_calibration(tmp_path) == config.DEFAULT_CALIBRATION


def test_legacy_steer_trim_is_migrated_once(tmp_path):
    legacy = tmp_path / "picar" / "calibration.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"steer_trim_deg": -3.0}))
    cfg = tmp_path / "cfg"
    assert config.load_calibration(cfg, legacy_path=legacy)["steer_trim_deg"] == -3.0
    assert json.loads((cfg / "calibration.json").read_text())["steer_trim_deg"] == -3.0
    legacy.write_text(json.dumps({"steer_trim_deg": 9.0}))
    assert config.load_calibration(cfg, legacy_path=legacy)["steer_trim_deg"] == -3.0   # new file wins


# ---- protocol validation ------------------------------------------------------------------------------------------

def test_validate_cmd_clamps_numbers_and_rejects_wrong_types():
    assert protocol.validate_cmd({"t": "cmd", "id": 1, "op": "drive", "dir": "forward", "speed": 250}) == (
        1, "drive", {"dir": "forward", "speed": 100})
    assert protocol.validate_cmd({"t": "cmd", "id": 2, "op": "move", "dir": "backward", "speed": 5, "ms": 99999})[2]["ms"] == 30000
    assert protocol.validate_cmd({"t": "cmd", "id": 3, "op": "steer", "angle": -99})[2] == {"angle": -45.0}
    assert protocol.validate_cmd({"t": "cmd", "id": 4, "op": "config", "stop_cm": 3, "speed_limit": 0})[2] == {
        "stop_cm": 10, "speed_limit": 1}
    assert protocol.validate_cmd({"t": "cmd", "id": 5, "op": "world", "world": "oval"})[2] == {"world": "oval"}
    for bad in ({"id": "1", "op": "stop"}, {"id": 1, "op": "fly"}, {"id": 1, "op": "drive", "dir": "up", "speed": 1},
                {"id": 1, "op": "drive", "dir": "forward", "speed": "fast"},
                {"id": 1, "op": "drive", "dir": "forward", "speed": True},
                {"id": 1, "op": "config", "obstacle_stop": "yes"}, {"id": True, "op": "stop"}):
        with pytest.raises(protocol.ProtocolError):
            protocol.validate_cmd({"t": "cmd", **bad})


def test_parse_frame_rejects_garbage():
    for text in ("nope", "[]", '{"x":1}', '{"t":5}'):
        with pytest.raises(protocol.ProtocolError):
            protocol.parse_frame(text)


def test_token_bucket_burst_and_refill():
    t = [0.0]
    b = protocol.TokenBucket(clock=lambda: t[0])
    assert sum(b.allow() for _ in range(80)) == 50
    t[0] += 0.1
    assert sum(b.allow() for _ in range(80)) == 10


def test_host_policy():
    p = protocol.HostPolicy(["car.example"], ["http://localhost:3000"], local_addrs=lambda: {"192.168.1.50"})
    for host in ("picar.local", "picar.local:8080", "localhost:8080", "127.0.0.1:1", "192.168.1.50:8080", "car.example",
                 "[::1]:8080"):
        assert p.host_ok(host), host
    for host in ("evil.com", "192.168.1.51", None, ""):
        assert not p.host_ok(host), host
    assert p.origin_ok(None, "picar.local") and p.origin_ok("http://picar.local:8080", "picar.local:8080")
    assert not p.origin_ok("http://evil.com", "picar.local:8080") and not p.origin_ok("null", "picar.local")
    assert p.origin_ok("http://localhost:3000", "picar.local")
