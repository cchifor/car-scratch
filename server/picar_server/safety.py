"""Pure safety helpers shared by the controller and the simulator (docs/contracts.md sections 1 and 3)."""
from __future__ import annotations

import math
from typing import Optional, Sequence

from .hal.base import DIST_FAR, DIST_FAULT, DIST_OK

SPEED_MAX = 100
STEER_MAX = 45.0
MOVE_MS_MAX = 30000
REVERSE_SPEED_MAX = 40
REVERSE_MS_MAX = 5000
STOP_CM_MIN = 10
STOP_CM_MAX = 100
HB_MS = 200
WD_MS = 750
FRESH_S = 0.5          # no ultrasonic sample for longer than this while moving forward -> stop
UNBLOCK_MARGIN_CM = 5  # `blocked` clears at margin + this
REACTION_S = 0.2       # sensing + reaction allowance folded into the margin
SENSOR_STALE_S = 0.15  # last ok/far sample older than this while moving forward -> sensor fault
NO_ECHO_TRUST_S = 0.48  # ... unless a "nothing there" reading (far, or a long cycle beginning) is at most this old (D7),
                        # counted from its ping
TRUST_NEAR_CM = 100.0   # no such trust after an echo closer than this (or than twice the full-speed margin) ...
NEAR_ECHO_MEMORY_S = 1.0  # ... was heard within this long
CLEAR_S = 1.0          # blocked clears after this long of consecutive clear samples
DRIVE_TIMEOUT_S = 5.0  # forward drive with the distance guard off
REVERSE_RESET_S = 1.0  # stopped this long resets the cumulative reverse budget
LIGHT_NONE_BELOW = 5   # brightness under this means "no light"
DUTY_MAX = 4095

DEFAULT_CONFIG = {"obstacle_stop": True, "stop_cm": 20, "speed_limit": 100}


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def interp(table: Sequence[Sequence[float]], x: float) -> float:
    """Piecewise-linear lookup in a sorted [[x, y], ...] table, clamped at both ends."""
    if not table:
        return 0.0
    if x <= table[0][0]:
        return float(table[0][1])
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            if x1 == x0:
                return float(y1)
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return float(table[-1][1])


def speed_to_duty_pct(speed: float, cal: dict) -> float:
    """User speed 0..100 -> duty percent: 0 at speed 0, else floor + (ceiling - floor) * speed / 100."""
    s = clamp(speed, 0, SPEED_MAX)
    if s <= 0:
        return 0.0
    floor, ceil = cal["duty_floor_pct"], cal["duty_ceiling_pct"]
    return floor + (ceil - floor) * s / 100.0


def speed_to_duty(speed: float, cal: dict) -> int:
    return int(round(DUTY_MAX * speed_to_duty_pct(speed, cal) / 100.0))


def duty_to_speed(duty: float, cal: dict) -> float:
    """Inverse of speed_to_duty for a signed or unsigned duty value (returns 0..100)."""
    pct = abs(duty) / DUTY_MAX * 100.0
    floor, ceil = cal["duty_floor_pct"], cal["duty_ceiling_pct"]
    if pct <= 0 or ceil <= floor:
        return 0.0
    return clamp((pct - floor) / (ceil - floor) * 100.0, 0, 100)


def speed_cm_s(speed: float, cal: dict) -> float:
    """Ground speed in cm/s at a user speed."""
    return interp(cal["speed_table"], speed_to_duty_pct(speed, cal))


def coast_cm(speed: float, cal: dict) -> float:
    return interp(cal["coast_table"], clamp(speed, 0, SPEED_MAX))


def margin_cm(stop_cm: float, speed: float, cal: dict) -> float:
    """Distance at which the obstacle guard stops the car at this speed."""
    return stop_cm + coast_cm(speed, cal) + speed_cm_s(speed, cal) * REACTION_S


def clamp_steer(angle: float) -> float:
    return clamp(angle, -STEER_MAX, STEER_MAX)


def effective_speed(direction: Optional[str], speed: float, speed_limit: float) -> int:
    """min(speed, speed_limit), and at most the reverse cap when backing up."""
    s = min(clamp(speed, 0, SPEED_MAX), clamp(speed_limit, 1, SPEED_MAX))
    if direction == "backward":
        s = min(s, REVERSE_SPEED_MAX)
    return int(round(s))


def guard_decision(dist_state: str, dist_cm: Optional[float], margin: float, *, moving_forward: bool,
                   distance_guard: bool, stale: bool) -> Optional[str]:
    """Obstacle guard for one sample. Returns None, "sensor_fault" or "obstacle".

    Sensor validity (fault state, or the last ok/far sample is stale) is always enforced; the distance
    comparison only when `distance_guard` is on. Nothing stops unless the car is moving forward."""
    if not moving_forward:
        return None
    if dist_state == DIST_FAULT or stale:
        return "sensor_fault"
    if distance_guard and dist_state == DIST_OK and dist_cm is not None and dist_cm < margin:
        return "obstacle"
    return None


def is_clear_sample(dist_state: str, dist_cm: Optional[float], margin: float, distance_guard: bool) -> bool:
    """A sample that counts toward clearing `blocked`: far, or ok and at least margin + 5 cm away
    (any valid reading when the distance guard is off)."""
    if dist_state == DIST_FAR:
        return True
    if dist_state == DIST_OK and dist_cm is not None:
        return (not distance_guard) or dist_cm >= margin + UNBLOCK_MARGIN_CM
    return False


def per_sensor(value, i: int) -> float:
    """Entry i of a calibration value that is either one number for every sensor or a list (one per sensor)."""
    return value[i] if isinstance(value, (list, tuple)) else value


def brightness(raw: float, cal: dict, i: int = 0) -> int:
    """Light module raw (lower = brighter) -> 0..100 (higher = brighter). `i` = 0 left, 1 middle, 2 right."""
    dark, bright = per_sensor(cal["light_dark"], i), per_sensor(cal["light_bright"], i)
    if dark == bright:
        return 0
    return int(round(clamp(100.0 * (dark - raw) / (dark - bright), 0, 100)))


def brightest(bright: Sequence[int]) -> str:
    """left, middle, right or none for three brightness values."""
    top = max(bright)
    if top < LIGHT_NONE_BELOW:
        return "none"
    if bright[1] == top:
        return "middle"
    return ("left", "middle", "right")[list(bright).index(top)]


def line_on(raw: Sequence[int], threshold, black_high: bool = True) -> list[bool]:
    """Which sensors see the line. `threshold`: one number or one per sensor. A black line reads HIGH on this
    module (`black_high`, measured): on = raw > threshold; otherwise on = raw < threshold."""
    return [(v > per_sensor(threshold, i)) if black_high else (v < per_sensor(threshold, i))
            for i, v in enumerate(raw)]


def line_pos(on: Sequence[bool]) -> Optional[float]:
    """-2 (leftmost sensor) .. +2 (rightmost); mean position of the sensors that see the line."""
    idx = [i - 2 for i, v in enumerate(on) if v]
    if not idx:
        return None
    return round(sum(idx) / len(idx), 2)


def geometry(cal: dict) -> dict:
    """Car geometry (x offsets in cm forward of the rear axle = pose point), sent in welcome.geometry."""
    return {"body": {"length": 26, "width": 16, "rear_overhang": 4}, "wheelbase": cal["wheelbase_cm"],
            "track": 13, "ultrasonic_x": 22, "line_x": 20, "line_spacing": cal["line_sensor_spacing_cm"],
            "light_x": 20, "light_spread": 4}


def wrap_deg(d: float) -> float:
    """Wrap to (-180, 180] like Scratch directions."""
    d = math.fmod(d, 360.0)
    if d > 180:
        d -= 360
    elif d <= -180:
        d += 360
    return d
