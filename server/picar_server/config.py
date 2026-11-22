"""Calibration defaults, loading and saving (docs/contracts.md section 3)."""
from __future__ import annotations

import copy
import json
import logging
import math
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_CALIBRATION: dict = {
    "calibrated": False,
    "line_enabled": False,
    "steer_trim_deg": 0.0,
    "duty_floor_pct": 25,
    "duty_ceiling_pct": 100,
    "speed_table": [[0, 0], [25, 0], [30, 5], [40, 15], [50, 25], [60, 35], [80, 55], [100, 70]],
    "coast_table": [[0, 0], [50, 3], [100, 12]],
    "wheelbase_cm": 14.0,
    "ultrasonic_offset_cm": 12.0,
    "line_sensor_spacing_cm": 2.0,
    "line_black_high": True,         # measured: a black line (or no ground) reads HIGH, white reads LOW
    "line_threshold": 320,           # number, or a list of 5 (leftmost first)
    "light_channels": [0, 1, 2],     # PCF8591 input of [left, middle, right]
    "light_dark": 200,               # number, or a list of 3 (left, middle, right)
    "light_bright": 20,
}

SENSOR_KEYS = ("light_channels", "light_dark", "light_bright", "line_black_high", "line_threshold")
LINE_SENSORS = 5
LIGHT_SENSORS = 3
LIGHT_RAW_MAX = 255
LINE_RAW_MAX = 1023              # a line follower frame with any value above this is invalid

FILE_NAME = "calibration.json"
LEGACY_PATH = Path.home() / "picar" / "calibration.json"


def _num(v) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:                   # an integer too large for a float (a JSON number with hundreds of digits)
        return False


def sim_calibration(cal: dict) -> dict:
    """A copy of the car's calibration for a simulator session: the car's own keys (speed table, wheelbase,
    trim ...) but the default sensor keys, because the simulator emits idealised readings."""
    out = copy.deepcopy(cal)
    for key in (*SENSOR_KEYS, "line_enabled"):
        out[key] = copy.deepcopy(DEFAULT_CALIBRATION[key])
    return out


def _table_ok(v) -> bool:
    return (isinstance(v, list) and len(v) >= 2
            and all(isinstance(p, (list, tuple)) and len(p) == 2 and _num(p[0]) and _num(p[1]) for p in v)
            and all(a[0] < b[0] for a, b in zip(v, v[1:])))


def valid_per_sensor(v, n: int, lo: float = -math.inf, hi: float = math.inf) -> bool:
    """A number, or a list of exactly n numbers (all within lo..hi)."""
    if isinstance(v, list):
        return len(v) == n and all(_num(x) and lo <= x <= hi for x in v)
    return _num(v) and lo <= v <= hi


def valid_light_channels(v) -> bool:
    """Three distinct PCF8591 inputs, each 0..3."""
    return (isinstance(v, list) and len(v) == LIGHT_SENSORS
            and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 3 for x in v)
            and len(set(v)) == LIGHT_SENSORS)


def _key_ok(key: str, value) -> bool:
    if key == "light_channels":
        return valid_light_channels(value)
    if key in ("light_dark", "light_bright"):
        return valid_per_sensor(value, LIGHT_SENSORS)
    if key == "line_threshold":
        return valid_per_sensor(value, LINE_SENSORS)
    if isinstance(DEFAULT_CALIBRATION[key], bool):
        return isinstance(value, bool)
    return _table_ok(value) if key.endswith("_table") else _num(value)


def merge(raw: Optional[dict]) -> dict:
    """Defaults overlaid with the valid entries of `raw` (unknown keys dropped)."""
    cal = copy.deepcopy(DEFAULT_CALIBRATION)
    for key, value in (raw or {}).items():
        if key not in cal:
            continue
        ok = _key_ok(key, value)
        if ok:
            if key.endswith("_table"):
                cal[key] = [list(p) for p in value]
            else:
                cal[key] = list(value) if isinstance(value, list) else value
        else:
            log.warning("calibration: ignoring invalid %s", key)
    if cal["duty_ceiling_pct"] <= cal["duty_floor_pct"]:
        cal["duty_floor_pct"] = DEFAULT_CALIBRATION["duty_floor_pct"]
        cal["duty_ceiling_pct"] = DEFAULT_CALIBRATION["duty_ceiling_pct"]
    return cal


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_calibration(config_dir, cal: dict) -> None:
    """Atomic write of calibration.json."""
    d = Path(config_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (FILE_NAME + ".tmp")
    with open(tmp, "w") as f:
        json.dump(cal, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, d / FILE_NAME)


def load_calibration(config_dir, legacy_path: Optional[Path] = None) -> dict:
    """Load <config_dir>/calibration.json; migrate the legacy {"steer_trim_deg"} file on first start."""
    path = Path(config_dir) / FILE_NAME
    raw = _read_json(path)
    if raw is not None:
        return merge(raw)
    legacy = _read_json(legacy_path if legacy_path is not None else LEGACY_PATH)
    if legacy and _num(legacy.get("steer_trim_deg")):
        cal = merge({"steer_trim_deg": legacy["steer_trim_deg"]})
        try:
            save_calibration(config_dir, cal)
            log.info("migrated legacy steer_trim_deg=%s", cal["steer_trim_deg"])
        except OSError as e:
            log.warning("could not write migrated calibration: %s", e)
        return cal
    return merge(None)
