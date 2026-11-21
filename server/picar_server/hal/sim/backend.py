"""Simulated car: bicycle-model physics at 50 Hz, sensor samples at 25 Hz (docs/contracts.md section 2 and 4)."""
from __future__ import annotations

import asyncio
import math
import random
import threading
import time
from typing import Callable, Optional

from ... import config, safety
from ..base import DIST_FAR, DIST_OK, DUTY_MAX, OnSample, Sample
from .world import (World, heading_vec, load_world, DEFAULT_WORLD, polygon_hits_walls, polyline_distance,
                    ray_cast, segments_intersect)

PHYSICS_HZ = 50
SAMPLE_HZ = 25
ACCEL_TAU_S = 0.2          # first-order speed lag when speeding up
RANGE_CM = 200.0
RAY_ANGLES = (-15.0, -7.5, 0.0, 7.5, 15.0)
MAX_INCIDENCE_DEG = 40.0   # an oblique wall reads "far" (the real sensor misses it)
DIST_SIGMA_CM = 0.5
LINE_ON_RAW, LINE_OFF_RAW = 420, 200      # real polarity: black line HIGH, white ground LOW (defaults: threshold 320)
LIGHT_DARK_RAW = 200
LIGHT_K = 100000.0


class SimBackend:
    name = "sim"

    def __init__(self, world: Optional[World] = None, calibration: Optional[dict] = None, *,
                 seed: Optional[int] = None, clock: Callable[[], float] = time.monotonic):
        self.cal = calibration if calibration is not None else config.merge(None)
        self.world = world or load_world(DEFAULT_WORLD)
        self.geo = safety.geometry(self.cal)
        self._rng = random.Random(seed)
        self._clock = clock
        self._lock = threading.Lock()
        self._duty = (0, 0)
        self._inhibit = False
        self._stop_latched = False
        self._steer = 0.0
        self._v = 0.0
        self._coast_tau = 0.1
        self.bumped = False
        self._x = self._y = self._dir = 0.0
        self._on_sample: Optional[OnSample] = None
        self._task: Optional[asyncio.Task] = None
        self.reset_pose()

    # ---- Backend interface ----------------------------------------------------------------
    def capabilities(self) -> dict[str, bool]:
        return {"ultrasonic": True, "light": True, "line": True}

    def start(self, on_sample: OnSample) -> None:
        self._on_sample = on_sample
        self._task = asyncio.get_running_loop().create_task(self._run())

    def set_motors(self, left: int, right: int) -> bool:
        left = max(-DUTY_MAX, min(DUTY_MAX, int(left)))
        right = max(-DUTY_MAX, min(DUTY_MAX, int(right)))
        with self._lock:
            ok = True
            if self._stop_latched:
                left = right = 0
                ok = False
            elif self._inhibit and (left > 0 or right > 0):
                left = right = 0
                ok = False
            self._set_duty_locked((left, right))
        return ok

    def set_steering(self, angle_deg: float) -> None:
        self._steer = safety.clamp_steer(float(angle_deg))

    def stop_motors(self) -> bool:
        with self._lock:
            self._set_duty_locked((0, 0))
        return True

    def inhibit_forward(self) -> None:
        with self._lock:
            self._inhibit = True
            self._set_duty_locked((0, 0))

    def clear_inhibit(self) -> None:
        with self._lock:
            self._inhibit = False

    def latch_stop(self) -> bool:
        with self._lock:
            self._stop_latched = True
            self._set_duty_locked((0, 0))
        return True

    def release_stop(self) -> None:
        with self._lock:
            self._stop_latched = False

    def motion(self) -> tuple[int, int]:
        with self._lock:
            return self._duty

    def set_near_hint(self, cm: float) -> None:
        pass

    def pose(self) -> tuple[float, float, float]:
        return (self._x, self._y, self._dir)

    def close(self) -> None:
        self.stop_motors()
        if self._task:
            self._task.cancel()
            self._task = None

    # ---- simulator extras -------------------------------------------------------------------
    def reset_pose(self) -> None:
        s = self.world.start
        self.teleport(s["x"], s["y"], s["dir"])

    def set_world(self, world: World) -> None:
        self.world = world
        self.reset_pose()

    def teleport(self, x: float, y: float, dir_deg: float) -> None:
        self._x, self._y, self._dir = float(x), float(y), safety.wrap_deg(dir_deg)
        self._v = 0.0
        self.bumped = False

    def _set_duty_locked(self, duty: tuple[int, int]) -> None:
        if duty == (0, 0) and self._duty != (0, 0):
            # Coasting: roll about coast_table[speed] cm after the motors stop.
            prev = (self._duty[0] + self._duty[1]) / 2
            speed = safety.duty_to_speed(prev, self.cal)
            target = safety.speed_cm_s(speed, self.cal)
            coast = safety.coast_cm(speed, self.cal)
            self._coast_tau = min(1.0, max(0.02, coast / target)) if target > 0.5 else 0.05
        self._duty = duty

    # ---- physics ----------------------------------------------------------------------------
    def _target_speed(self) -> float:
        l, r = self.motion()
        d = (l + r) / 2.0
        if d == 0:
            return 0.0
        v = safety.interp(self.cal["speed_table"], abs(d) / DUTY_MAX * 100.0)
        return v if d > 0 else -v

    def body_polygon(self, x: float, y: float, dir_deg: float) -> list[tuple[float, float]]:
        b = self.geo["body"]
        fx, fy = heading_vec(dir_deg)
        rx, ry = fy, -fx  # right-hand vector
        rear, front, half = -b["rear_overhang"], b["length"] - b["rear_overhang"], b["width"] / 2.0
        return [(x + fx * a + rx * s * half, y + fy * a + ry * s * half)
                for a, s in ((rear, -1), (front, -1), (front, 1), (rear, 1))]

    def step(self, dt: float) -> None:
        target = self._target_speed()
        tau = self._coast_tau if target == 0.0 else ACCEL_TAU_S
        self._v += (target - self._v) * (1.0 - math.exp(-dt / tau))
        if target == 0.0 and abs(self._v) < 0.05:
            self._v = 0.0
        if self._v == 0.0:
            return
        L = self.cal["wheelbase_cm"]
        dth = self._v / L * math.tan(math.radians(self._steer)) * dt
        mid = math.radians(self._dir) + dth / 2.0
        nx = self._x + self._v * dt * math.sin(mid)
        ny = self._y + self._v * dt * math.cos(mid)
        ndir = safety.wrap_deg(self._dir + math.degrees(dth))
        if polygon_hits_walls(self.body_polygon(nx, ny, ndir), self.world.walls):
            self._v = 0.0
            self.bumped = True
        else:
            self._x, self._y, self._dir = nx, ny, ndir
            self.bumped = False

    # ---- sensors ----------------------------------------------------------------------------
    def _point(self, forward: float, lateral: float) -> tuple[float, float]:
        fx, fy = heading_vec(self._dir)
        return (self._x + fx * forward + fy * lateral, self._y + fy * forward - fx * lateral)

    def read_distance(self) -> tuple[str, Optional[float]]:
        ox, oy = self._point(self.geo["ultrasonic_x"], 0.0)
        best = None
        for off in RAY_ANGLES:
            dx, dy = heading_vec(self._dir + off)
            hit = ray_cast(ox, oy, dx, dy, self.world.walls, RANGE_CM)
            if hit and (best is None or hit[0] < best[0]):
                best = hit
        if best is None or best[2] > MAX_INCIDENCE_DEG:
            return DIST_FAR, None
        return DIST_OK, max(0.0, best[0] + self._rng.gauss(0.0, DIST_SIGMA_CM))

    def read_line(self) -> list[int]:
        line = self.world.line
        out = []
        for i in range(5):
            px, py = self._point(self.geo["line_x"], (i - 2) * self.geo["line_spacing"])
            on = line is not None and polyline_distance(px, py, line["points"], line["closed"]) <= line["width"] / 2
            out.append(int(round(self._rng.gauss(LINE_ON_RAW if on else LINE_OFF_RAW, 5))))
        return out

    def read_light(self) -> list[int]:
        fx, fy = heading_vec(self._dir)
        out = []
        for lat in (-self.geo["light_spread"], 0.0, self.geo["light_spread"]):
            sx, sy = self._point(self.geo["light_x"], lat)
            total = 0.0
            for lamp in self.world.lights:
                dx, dy = lamp["x"] - sx, lamp["y"] - sy
                d = max(1.0, math.hypot(dx, dy))
                cos = (fx * dx + fy * dy) / d
                if cos <= 0:
                    continue
                seg = (sx, sy, lamp["x"], lamp["y"])
                if any(segments_intersect(seg, w) for w in self.world.walls):
                    continue
                total += lamp["intensity"] * LIGHT_K * cos / (d * d)
            out.append(int(round(max(0.0, min(255.0, LIGHT_DARK_RAW - total)))))
        return out

    def make_sample(self) -> Sample:
        state, cm = self.read_distance()
        return Sample(self._clock(), cm, state, self.read_line(), self.read_light())

    async def _run(self) -> None:
        dt = 1.0 / PHYSICS_HZ
        every = PHYSICS_HZ // SAMPLE_HZ
        last = time.monotonic()
        nxt = last + dt
        n = 0
        while True:
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))
            now = time.monotonic()
            self.step(min(now - last, 0.1))
            last = now
            nxt = max(nxt + dt, now)
            n += 1
            if n % every == 0 and self._on_sample:
                try:
                    self._on_sample(self.make_sample())
                except Exception:
                    pass
