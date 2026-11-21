"""Simulator worlds: loading, validation and the 2-D geometry the physics and sensors use."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

WORLD_DIR = Path(__file__).with_name("worlds")
DEFAULT_WORLD = "arena"
_ID_RE = re.compile(r"[a-z0-9_-]{1,32}")

Wall = tuple[float, float, float, float]


class WorldError(ValueError):
    pass


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass(frozen=True)
class World:
    id: str
    title: str
    size: tuple[float, float]
    walls: tuple[Wall, ...]
    line: Optional[dict]          # {"points": [[x, y], ...], "closed": bool, "width": float}
    lights: tuple[dict, ...]      # {"x", "y", "intensity"}
    start: dict                   # {"x", "y", "dir"}

    def to_dict(self) -> dict:
        """The object sent to clients as the `world` message body (the physics uses the same data)."""
        return {"id": self.id, "title": self.title, "size": list(self.size),
                "walls": [list(w) for w in self.walls], "line": self.line,
                "lights": [dict(l) for l in self.lights], "start": dict(self.start)}


def validate_world(data) -> World:
    if not isinstance(data, dict):
        raise WorldError("world must be an object")
    wid = data.get("id")
    if not isinstance(wid, str) or not _ID_RE.fullmatch(wid):
        raise WorldError("bad world id")
    title = data.get("title", wid)
    if not isinstance(title, str):
        raise WorldError("title must be a string")
    size = data.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(_num(v) and v > 0 for v in size)):
        raise WorldError("size must be [w, h]")
    walls = []
    for w in data.get("walls", []):
        if not (isinstance(w, list) and len(w) == 4 and all(_num(v) for v in w)):
            raise WorldError("each wall must be [x1, y1, x2, y2]")
        walls.append(tuple(float(v) for v in w))
    line = data.get("line")
    if line is not None:
        pts = line.get("points") if isinstance(line, dict) else None
        if not (isinstance(pts, list) and len(pts) >= 2
                and all(isinstance(p, list) and len(p) == 2 and all(_num(v) for v in p) for p in pts)):
            raise WorldError("line.points must be a list of [x, y]")
        if not (_num(line.get("width")) and line["width"] > 0):
            raise WorldError("line.width must be positive")
        line = {"points": [list(map(float, p)) for p in pts], "closed": bool(line.get("closed", False)),
                "width": float(line["width"])}
    lights = []
    for l in data.get("lights", []):
        if not (isinstance(l, dict) and all(_num(l.get(k)) for k in ("x", "y", "intensity"))):
            raise WorldError("each light needs x, y, intensity")
        lights.append({"x": float(l["x"]), "y": float(l["y"]), "intensity": float(l["intensity"])})
    start = data.get("start", {"x": 0, "y": 0, "dir": 90})
    if not (isinstance(start, dict) and all(_num(start.get(k)) for k in ("x", "y", "dir"))):
        raise WorldError("start needs x, y, dir")
    return World(wid, title, (float(size[0]), float(size[1])), tuple(walls), line, tuple(lights),
                 {"x": float(start["x"]), "y": float(start["y"]), "dir": float(start["dir"])})


def load_world(world_id: str, directory: Path = WORLD_DIR) -> World:
    """Raises KeyError for an unknown id, WorldError for an invalid file."""
    if not _ID_RE.fullmatch(world_id or ""):
        raise KeyError(world_id)
    path = Path(directory) / f"{world_id}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KeyError(world_id)
    except ValueError as e:
        raise WorldError(f"{path.name}: {e}")
    world = validate_world(raw)
    if world.id != world_id:
        raise WorldError(f"{path.name}: id mismatch")
    return world


def list_worlds(directory: Path = WORLD_DIR) -> list[dict]:
    out = []
    for p in sorted(Path(directory).glob("*.json")):
        try:
            w = load_world(p.stem, directory)
        except (KeyError, WorldError):
            continue
        out.append({"id": w.id, "title": w.title})
    return out


# ---- geometry ---------------------------------------------------------------------------------

def heading_vec(dir_deg: float) -> tuple[float, float]:
    """Unit vector of a Scratch heading (0 = +y, 90 = +x, clockwise)."""
    r = math.radians(dir_deg)
    return math.sin(r), math.cos(r)


def ray_cast(ox: float, oy: float, dx: float, dy: float, walls: Sequence[Wall],
             max_range: float) -> Optional[tuple[float, int, float]]:
    """Nearest wall hit along the unit ray: (distance, wall index, incidence angle in degrees from the
    wall normal) or None."""
    best: Optional[tuple[float, int, float]] = None
    for i, (x1, y1, x2, y2) in enumerate(walls):
        sx, sy = x2 - x1, y2 - y1
        denom = dx * sy - dy * sx
        if abs(denom) < 1e-9:
            continue
        t = ((x1 - ox) * sy - (y1 - oy) * sx) / denom
        u = ((x1 - ox) * dy - (y1 - oy) * dx) / denom
        if t < 0 or t > max_range or u < 0 or u > 1:
            continue
        if best is None or t < best[0]:
            length = math.hypot(sx, sy)
            cos = abs(dx * -sy + dy * sx) / length
            best = (t, i, math.degrees(math.acos(min(1.0, cos))))
    return best


def point_segment_distance(px, py, x1, y1, x2, y2) -> float:
    sx, sy = x2 - x1, y2 - y1
    l2 = sx * sx + sy * sy
    if l2 == 0:
        return math.hypot(px - x1, py - y1)
    u = max(0.0, min(1.0, ((px - x1) * sx + (py - y1) * sy) / l2))
    return math.hypot(px - (x1 + u * sx), py - (y1 + u * sy))


def polyline_distance(px: float, py: float, points: Sequence[Sequence[float]], closed: bool) -> float:
    n = len(points)
    best = math.inf
    for i in range(n if closed else n - 1):
        a, b = points[i], points[(i + 1) % n]
        best = min(best, point_segment_distance(px, py, a[0], a[1], b[0], b[1]))
    return best


def _orient(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segments_intersect(a: Wall, b: Wall) -> bool:
    ax, ay, bx, by = a
    cx, cy, dx, dy = b
    d1, d2 = _orient(cx, cy, dx, dy, ax, ay), _orient(cx, cy, dx, dy, bx, by)
    d3, d4 = _orient(ax, ay, bx, by, cx, cy), _orient(ax, ay, bx, by, dx, dy)
    return ((d1 > 0) != (d2 > 0) or d1 == 0 or d2 == 0) and ((d3 > 0) != (d4 > 0) or d3 == 0 or d4 == 0) \
        and not (d1 == d2 == 0 and not _overlap(a, b))


def _overlap(a: Wall, b: Wall) -> bool:
    """Collinear segments: do their bounding boxes overlap?"""
    return (min(a[0], a[2]) <= max(b[0], b[2]) and min(b[0], b[2]) <= max(a[0], a[2])
            and min(a[1], a[3]) <= max(b[1], b[3]) and min(b[1], b[3]) <= max(a[1], a[3]))


def _inside_convex(poly: Sequence[tuple[float, float]], x: float, y: float) -> bool:
    signs = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        signs.append(_orient(a[0], a[1], b[0], b[1], x, y) >= 0)
    return all(signs) or not any(signs)


def polygon_hits_walls(poly: Sequence[tuple[float, float]], walls: Sequence[Wall]) -> bool:
    edges = [(poly[i][0], poly[i][1], poly[(i + 1) % len(poly)][0], poly[(i + 1) % len(poly)][1])
             for i in range(len(poly))]
    for w in walls:
        if any(segments_intersect(e, w) for e in edges):
            return True
        if _inside_convex(poly, w[0], w[1]):
            return True
    return False
