"""Wire protocol: frame parsing, validation, limits, rate limiting, Origin check (docs/protocol.md)."""
from __future__ import annotations

import json
import math
import re
import socket
import time
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

from . import safety

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 4096
RATE_PER_S = 100
RATE_BURST = 50
IDLE_S = 10.0
TEL_HZ = 20
LEASE_S = 30.0

CLOSE_BAD_FIRST = 4400
CLOSE_IDLE = 4408
CLOSE_VERSION = 4409

LIMITS = {
    "speed_max": safety.SPEED_MAX,
    "steer_max": int(safety.STEER_MAX),
    "move_ms_max": safety.MOVE_MS_MAX,
    "reverse_speed_max": safety.REVERSE_SPEED_MAX,
    "reverse_ms_max": safety.REVERSE_MS_MAX,
    "stop_cm_min": safety.STOP_CM_MIN,
    "stop_cm_max": safety.STOP_CM_MAX,
}

# Viewers may send only these (cmd ops are checked separately: only `estop`).
VIEWER_TYPES = {"hb", "control", "mode", "hello"}

_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ProtocolError(Exception):
    def __init__(self, code: str, msg: str, cmd_id: Optional[int] = None):
        super().__init__(msg)
        self.code, self.msg, self.cmd_id = code, msg, cmd_id


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _num(msg: dict, key: str, cmd_id=None, required=True) -> Optional[float]:
    v = msg.get(key)
    if v is None and not required:
        return None
    if not _is_num(v):
        raise ProtocolError("bad_request", f"{key} must be a number", cmd_id)
    return v


def parse_frame(text: str) -> dict:
    try:
        obj = json.loads(text)
    except ValueError:
        raise ProtocolError("bad_request", "invalid JSON")
    if not isinstance(obj, dict) or not isinstance(obj.get("t"), str):
        raise ProtocolError("bad_request", "frame must be an object with a string 't'")
    return obj


def validate_hello(msg: dict) -> dict:
    """Returns {client_id, want, mode, world}. Raises ProtocolError(bad_request); the caller checks `v` first."""
    cid = msg.get("client_id")
    if not isinstance(cid, str) or not 8 <= len(cid) <= 64 or not cid.isprintable():
        raise ProtocolError("bad_request", "client_id must be a string of 8..64 characters")
    want = msg.get("want")
    if want not in ("control", "view"):
        raise ProtocolError("bad_request", "want must be control or view")
    mode = msg.get("mode")
    if mode not in ("car", "sim"):
        raise ProtocolError("bad_request", "mode must be car or sim")
    return {"client_id": cid, "want": want, "mode": mode, "world": _world_id(msg)}


def _world_id(msg: dict, key: str = "world") -> Optional[str]:
    w = msg.get(key)
    if w is None:
        return None
    if not isinstance(w, str) or not _ID_RE.fullmatch(w):
        raise ProtocolError("bad_request", "invalid world id", msg.get("id") if isinstance(msg.get("id"), int) else None)
    return w


def _direction(msg: dict, cid) -> str:
    d = msg.get("dir")
    if d not in ("forward", "backward"):
        raise ProtocolError("bad_request", "dir must be forward or backward", cid)
    return d


def validate_cmd(msg: dict) -> tuple[int, str, dict]:
    """Returns (id, op, normalised fields). Numbers are range-clamped, wrong types are rejected."""
    cid = msg.get("id")
    if not isinstance(cid, int) or isinstance(cid, bool):
        raise ProtocolError("bad_request", "cmd id must be an integer")
    op = msg.get("op")
    f: dict = {}
    if op in ("drive", "move"):
        f["dir"] = _direction(msg, cid)
        f["speed"] = int(round(safety.clamp(_num(msg, "speed", cid), 0, safety.SPEED_MAX)))
        if op == "move":
            f["ms"] = int(round(safety.clamp(_num(msg, "ms", cid), 1, safety.MOVE_MS_MAX)))
    elif op == "set_speed":
        f["speed"] = int(round(safety.clamp(_num(msg, "speed", cid), 0, safety.SPEED_MAX)))
    elif op == "steer":
        f["angle"] = safety.clamp_steer(float(_num(msg, "angle", cid)))
    elif op == "config":
        if "obstacle_stop" in msg:
            if not isinstance(msg["obstacle_stop"], bool):
                raise ProtocolError("bad_request", "obstacle_stop must be a boolean", cid)
            f["obstacle_stop"] = msg["obstacle_stop"]
        if "stop_cm" in msg:
            f["stop_cm"] = int(round(safety.clamp(_num(msg, "stop_cm", cid), safety.STOP_CM_MIN, safety.STOP_CM_MAX)))
        if "speed_limit" in msg:
            f["speed_limit"] = int(round(safety.clamp(_num(msg, "speed_limit", cid), 1, safety.SPEED_MAX)))
    elif op == "world":
        w = msg.get("world", msg.get("world_id"))
        if not isinstance(w, str) or not _ID_RE.fullmatch(w):
            raise ProtocolError("bad_request", "world must be a world id string", cid)
        f["world"] = w
    elif op == "calibrate":
        if "steer_trim_deg" in msg:
            f["steer_trim_deg"] = safety.clamp(float(_num(msg, "steer_trim_deg", cid)), -20.0, 20.0)
        if "save" in msg:
            if not isinstance(msg["save"], bool):
                raise ProtocolError("bad_request", "save must be a boolean", cid)
            f["save"] = msg["save"]
    elif op in ("stop", "pause", "estop", "estop_reset", "resume", "reset_pose"):
        pass
    else:
        raise ProtocolError("bad_request", f"unknown op {op!r}", cid)
    return cid, op, f


def validate_control(msg: dict) -> tuple[str, bool]:
    action = msg.get("action")
    if action not in ("take", "release"):
        raise ProtocolError("bad_request", "action must be take or release")
    force = msg.get("force", False)
    if not isinstance(force, bool):
        raise ProtocolError("bad_request", "force must be a boolean")
    return action, force


def validate_mode(msg: dict) -> tuple[str, Optional[str]]:
    mode = msg.get("mode")
    if mode not in ("car", "sim"):
        raise ProtocolError("bad_request", "mode must be car or sim")
    return mode, _world_id(msg)


class TokenBucket:
    """rate per second with a burst allowance."""

    def __init__(self, rate: float = RATE_PER_S, burst: float = RATE_BURST,
                 clock: Callable[[], float] = time.monotonic):
        self.rate, self.burst, self._clock = rate, burst, clock
        self._tokens = float(burst)
        self._t = clock()

    def allow(self) -> bool:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._t) * self.rate)
        self._t = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


def dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))


def split_host(host: Optional[str]) -> str:
    """Lower-cased host without the port ('[::1]:80' -> '[::1]')."""
    h = (host or "").strip().lower()
    if h.startswith("["):
        return h[: h.find("]") + 1] if "]" in h else h
    return h.rsplit(":", 1)[0] if ":" in h else h


def local_addresses() -> set[str]:
    """The machine's own IPv4 addresses and host names (best effort; Linux uses SIOCGIFADDR)."""
    out: set[str] = set()
    try:
        name = socket.gethostname().lower()
        out.update({name, name + ".local"})
        out.update(a[4][0] for a in socket.getaddrinfo(name, None, socket.AF_INET))
    except OSError:
        pass
    try:
        import fcntl
        import struct
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for _, ifname in socket.if_nameindex():
                try:
                    packed = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", ifname.encode()[:15]))
                    out.add(socket.inet_ntoa(packed[20:24]))
                except OSError:
                    pass
    except (ImportError, OSError):
        pass
    return out


class HostPolicy:
    """Host allow-list (DNS rebinding) plus the Origin rule for /ws and unsafe REST methods."""

    BASE = ("picar.local", "picar", "localhost", "127.0.0.1", "[::1]")

    def __init__(self, allow_hosts: Iterable[str] = (), allow_origins: Iterable[str] = (),
                 local_addrs: Callable[[], set[str]] = local_addresses, refresh_s: float = 30.0):
        self._extra = {split_host(h) for h in allow_hosts}
        self._origins = {o.strip().lower().rstrip("/") for o in allow_origins}
        self._local, self._refresh = local_addrs, refresh_s
        self._cache: set[str] = set()
        self._cache_t = -1e9

    def _hosts(self) -> set[str]:
        now = time.monotonic()
        if now - self._cache_t > self._refresh:
            try:
                self._cache = {split_host(a) for a in self._local()}
            except Exception:
                self._cache = set()
            self._cache_t = now
        return set(self.BASE) | self._extra | self._cache

    def host_ok(self, host: Optional[str]) -> bool:
        return bool(host) and split_host(host) in self._hosts()

    def origin_ok(self, origin: Optional[str], host: Optional[str]) -> bool:
        """True when there is no Origin (CLI tools), or its host:port equals Host, or it is allow-listed."""
        if origin is None:
            return True
        if origin.strip().lower().rstrip("/") in self._origins:
            return True
        try:
            netloc = urlsplit(origin).netloc
        except ValueError:
            return False
        return bool(netloc) and bool(host) and netloc.lower() == (host or "").lower()

    def allowed(self, origin: Optional[str], host: Optional[str]) -> bool:
        return self.host_ok(host) and self.origin_ok(origin, host)


def dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))
