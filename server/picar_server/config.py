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
    "line_threshold": 300,
    "light_dark": 200,
    "light_bright": 20,
}

FILE_NAME = "calibration.json"
LEGACY_PATH = Path.home() / "picar" / "calibration.json"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _table_ok(v) -> bool:
    return (isinstance(v, list) and len(v) >= 2
            and all(isinstance(p, (list, tuple)) and len(p) == 2 and _num(p[0]) and _num(p[1]) for p in v)
            and all(a[0] < b[0] for a, b in zip(v, v[1:])))


def merge(raw: Optional[dict]) -> dict:
    """Defaults overlaid with the valid entries of `raw` (unknown keys dropped)."""
    cal = copy.deepcopy(DEFAULT_CALIBRATION)
    for key, value in (raw or {}).items():
        if key not in cal:
            continue
        if isinstance(cal[key], bool):
            ok = isinstance(value, bool)
        else:
            ok = _table_ok(value) if key.endswith("_table") else _num(value)
        if ok:
            cal[key] = [list(p) for p in value] if key.endswith("_table") else value
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
