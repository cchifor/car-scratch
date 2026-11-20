"""Per-socket sessions and the hub that arbitrates the shared car (docs/protocol.md sections 2-5)."""
from __future__ import annotations

import asyncio
import collections
import copy
import itertools
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from aiohttp import WSMsgType, web

from . import protocol, safety
from .controller import CmdError, Controller
from .hal.sim.backend import SimBackend
from .hal.sim.world import DEFAULT_WORLD, WORLD_DIR, WorldError, list_worlds, load_world
from .protocol import ProtocolError, dumps

log = logging.getLogger(__name__)

MAX_SIMS = 4
MAX_QUEUE = 512
RATE_EXEMPT_OPS = ("stop", "estop", "pause")   # safety commands are never rate limited


@dataclass
class Lease:
    client_id: str
    expires: float


class Hub:
    """Shared state of one server: the shared car, its controller session, the lease and all sessions."""

    def __init__(self, car: Controller, calibration: dict, *, hw: dict, server_info: dict,
                 clock: Callable[[], float] = time.monotonic, idle_s: float = protocol.IDLE_S,
                 lease_s: float = protocol.LEASE_S, tel_hz: int = protocol.TEL_HZ,
                 world_dir=WORLD_DIR, max_sims: int = MAX_SIMS):
        self.car, self.cal, self.hw, self.server_info = car, calibration, hw, server_info
        self.clock, self.idle_s, self.lease_s, self.tel_hz = clock, idle_s, lease_s, tel_hz
        self.world_dir, self.max_sims = world_dir, max_sims
        self.sessions: dict[str, Session] = {}
        self.controller: Optional[Session] = None
        self.lease: Optional[Lease] = None
        self._seq = 0
        self._ids = itertools.count(1)
        self._tel_task: Optional[asyncio.Task] = None
        car.on_event = self._car_event
        car.on_done = self._car_done

    # ---- lifecycle ----------------------------------------------------------------------------
    def start(self) -> None:
        self._tel_task = asyncio.get_running_loop().create_task(self._tel_loop())

    async def stop(self) -> None:
        if self._tel_task:
            self._tel_task.cancel()
        for s in list(self.sessions.values()):
            s.dispose_sim()
            try:
                await s.ws.close(code=1001)
            except Exception:
                pass

    def new_session_id(self) -> str:
        return f"s{next(self._ids)}"

    # ---- queries -------------------------------------------------------------------------------
    def worlds(self) -> list[dict]:
        return list_worlds(self.world_dir)

    def status(self) -> dict:
        return {
            **self.server_info,
            "car": {"available": self.car.backend is not None, **self.hw_info()},
            "clients": {"car_controller": self.controller is not None,
                        "car_viewers": sum(1 for s in self.sessions.values()
                                           if s.mode == "car" and s is not self.controller),
                        "sim_sessions": self.sim_count()},
        }

    def hw_info(self) -> dict:
        info = {"backend": self.car.backend.name if self.car.backend is not None else "none",
                "capabilities": self.car.capabilities()}
        if self.hw.get("error"):
            info["error"] = self.hw["error"]
        return info

    def sim_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.sim is not None)

    def live_client(self, client_id: str, exclude: "Session") -> bool:
        return any(s is not exclude and s.client_id == client_id for s in self.sessions.values())

    def _lease_for(self, client_id: str) -> bool:
        return (self.lease is not None and self.lease.client_id == client_id
                and self.clock() < self.lease.expires)

    def _lease_active(self) -> bool:
        return self.lease is not None and self.clock() < self.lease.expires

    # ---- arbitration -----------------------------------------------------------------------------
    def take(self, s: "Session", force: bool = False) -> bool:
        if self.controller is s:
            return True
        free = self.controller is None and (not self._lease_active() or self._lease_for(s.client_id))
        if not (free or force):
            return False
        returning = self.controller is None and self._lease_for(s.client_id)
        old = self.controller
        if old is not None:
            self.car.stop_and_hold("control", "stopped")
            old.role = "viewer"
        self.controller, self.lease = s, None
        s.role = "controller"
        self.car.new_controller(returning)
        self._notify()
        return True

    def release(self, s: "Session") -> None:
        if self.controller is not s:
            return
        self.controller, self.lease = None, None
        self.car.attached = False
        self.car.stop_and_hold("control", "stopped")
        s.role = "viewer"
        self._notify()

    def detach(self, s: "Session") -> None:
        self.sessions.pop(s.id, None)
        if self.controller is s:
            self.controller = None
            self.lease = Lease(s.client_id, self.clock() + self.lease_s)
            self.car.controller_lost()
            self._notify()
        s.dispose_sim()

    def estop(self, s: Optional["Session"], by: str) -> None:
        """Latches the shared car (any session, any mode) and the session's own sim."""
        self.car.estop(by)
        if s is not None and s.sim is not None:
            s.sim.estop("controller")

    def _notify(self) -> None:
        present = self.controller is not None
        for s in list(self.sessions.values()):
            if s.mode == "car" and s.ready:
                s.send({"t": "role", "role": s.role, "controller_present": present})
        self._car_event("control_changed", {"controller_present": present})

    # ---- shared-car callbacks ------------------------------------------------------------------------
    def _car_event(self, kind: str, fields: dict) -> None:
        msg = {"t": "event", "kind": kind, **fields}
        for s in list(self.sessions.values()):
            if s.mode == "car" and s.ready:
                s.send(msg)

    def _car_done(self, owner, cmd_id: int, result: str) -> None:
        if isinstance(owner, Session) and owner.id in self.sessions and owner.mode == "car":
            owner.send({"t": "done", "id": cmd_id, "result": result})

    # ---- telemetry ----------------------------------------------------------------------------------
    async def _tel_loop(self) -> None:
        interval = 1.0 / self.tel_hz
        nxt = time.monotonic()
        while True:
            nxt += interval
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))
            nxt = max(nxt, time.monotonic() - interval)
            try:
                self.broadcast_tel()
            except Exception:
                log.exception("telemetry failed")

    def broadcast_tel(self) -> None:
        self._seq += 1
        ts = round(self.clock(), 2)
        base: Optional[dict] = None
        viewers = sum(1 for s in self.sessions.values() if s.mode == "car" and s is not self.controller)
        for s in list(self.sessions.values()):
            if not s.ready:
                continue
            if s.mode == "car":
                if base is None:
                    base = self.car.snapshot()
                snap, nview = base, viewers
            else:
                snap, nview = s.sim.snapshot(), 0
            frame = {"t": "tel", "seq": self._seq, "ts": ts, "mode": s.mode, **snap,
                     "ctl": {"role": s.role, "viewers": nview}}
            s.post_tel(dumps(frame))


class Session:
    def __init__(self, hub: Hub, ws: web.WebSocketResponse):
        self.hub, self.ws = hub, ws
        self.id = hub.new_session_id()
        self.client_id = ""
        self.want = "view"
        self.mode = "car"
        self.role = "viewer"
        self.world_id: Optional[str] = None
        self.sim: Optional[Controller] = None
        self.ready = False
        self.bucket = protocol.TokenBucket(clock=hub.clock)
        self.last_ok = hub.clock()      # last accepted JSON frame (liveness)
        self._out: collections.deque = collections.deque()
        self._tel: Optional[str] = None
        self._wake = asyncio.Event()
        self._writer: Optional[asyncio.Task] = None
        self._closing = False

    # ---- output ----------------------------------------------------------------------------------
    @property
    def ctl(self) -> Controller:
        return self.sim if self.mode == "sim" and self.sim is not None else self.hub.car

    def send(self, msg: dict) -> None:
        """Reliable message: ordered, never dropped (the socket is closed instead of growing a queue)."""
        if self._closing:
            return
        if len(self._out) >= MAX_QUEUE:
            self._closing = True
            asyncio.ensure_future(self.ws.close(code=1008, message=b"slow client"))
            return
        self._out.append(dumps(msg))
        self._wake.set()

    def post_tel(self, text: str) -> None:
        """Latest-only: a newer frame replaces an unsent one."""
        self._tel = text
        self._wake.set()

    async def _write_loop(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while self._out or self._tel is not None:
                    if self._out:
                        text = self._out.popleft()
                    else:
                        text, self._tel = self._tel, None
                    await self.ws.send_str(text)
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
            pass

    def err(self, code: str, msg: str, cmd_id: Optional[int] = None) -> None:
        m = {"t": "err", "code": code, "msg": msg}
        if cmd_id is not None:
            m["id"] = cmd_id
        self.send(m)

    # ---- sim controller ------------------------------------------------------------------------------
    def _make_sim(self, world_id: Optional[str]) -> Controller:
        world = load_world(world_id or DEFAULT_WORLD, self.hub.world_dir)
        cal = copy.deepcopy(self.hub.cal)
        sim = Controller(SimBackend(world, cal), cal, shared=False, on_event=self._sim_event, on_done=self._sim_done)
        sim.start()
        self.world_id = world.id
        return sim

    def _sim_event(self, kind: str, fields: dict) -> None:
        self.send({"t": "event", "kind": kind, **fields})

    def _sim_done(self, owner, cmd_id: int, result: str) -> None:
        self.send({"t": "done", "id": cmd_id, "result": result})

    def dispose_sim(self) -> None:
        if self.sim is not None:
            self.sim.close()
            self.sim = None

    # ---- messages -------------------------------------------------------------------------------------
    def welcome(self) -> dict:
        hub, ctl = self.hub, self.ctl
        hw = hub.hw_info() if self.mode == "car" else {"backend": "sim", "capabilities": ctl.capabilities()}
        return {
            "t": "welcome", "v": protocol.PROTOCOL_VERSION, "session": self.id, "role": self.role,
            "mode": self.mode, "safety": {"estop": ctl.estopped, "hold": ctl.hold},
            "car_available": hub.car.backend is not None, "calibrated": bool(hub.cal.get("calibrated")),
            "geometry": safety.geometry(hub.cal), "hw": hw, "limits": protocol.LIMITS, "config": dict(ctl.cfg),
            "hb_ms": safety.HB_MS, "wd_ms": safety.WD_MS, "tel_hz": hub.tel_hz,
            "worlds": hub.worlds(), "server": hub.server_info,
        }

    def _send_world(self) -> None:
        self.send({"t": "world", **self.sim.backend.world.to_dict()})

    def _announce(self) -> None:
        self.send(self.welcome())
        if self.mode == "sim":
            self._send_world()
        self.send({"t": "role", "role": self.role, "controller_present": self.hub.controller is not None})

    # ---- main loop --------------------------------------------------------------------------------------
    async def run(self) -> None:
        hub, ws = self.hub, self.ws
        self._writer = asyncio.get_running_loop().create_task(self._write_loop())
        try:
            while not self._closing:
                timeout = hub.idle_s - (hub.clock() - self.last_ok)
                try:
                    m = await asyncio.wait_for(ws.receive(), max(timeout, 0.001))
                except asyncio.TimeoutError:
                    await ws.close(code=protocol.CLOSE_IDLE, message=b"idle")
                    break
                if m.type == WSMsgType.TEXT:
                    if not await self._on_text(m.data):
                        break
                elif m.type == WSMsgType.BINARY:
                    if not self.ready:
                        await ws.close(code=protocol.CLOSE_BAD_FIRST, message=b"expected hello")
                        break
                    self.err("bad_request", "binary frames are not supported")
                else:
                    break
        finally:
            if self._writer:
                self._writer.cancel()
            hub.detach(self)
            if not ws.closed:
                await ws.close()

    async def _on_text(self, text: str) -> bool:
        """Returns False to end the session."""
        if not self.ready:
            return await self._on_hello(text)
        try:
            msg = protocol.parse_frame(text)
            t = msg["t"]
            if not self.bucket.allow() and not (t == "cmd" and msg.get("op") in RATE_EXEMPT_OPS):
                cid = msg.get("id")
                self.err("rate_limited", "too many frames",
                         cid if t == "cmd" and isinstance(cid, int) and not isinstance(cid, bool) else None)
                return True
            if t == "hb":
                self._accept()
            elif t == "cmd":
                cid, op, f = protocol.validate_cmd(msg)
                if self._cmd(cid, op, f):   # only an accepted command feeds the watchdog
                    self._accept(op)
            elif t == "control":
                action, force = protocol.validate_control(msg)
                self._accept()
                self._control(action, force)
            elif t == "mode":
                mode, world = protocol.validate_mode(msg)
                self._accept()
                self._switch_mode(mode, world)
            else:
                raise ProtocolError("bad_request", f"unexpected message {t!r}")
        except ProtocolError as e:
            self.err(e.code, e.msg, e.cmd_id)
        return True

    def _is_controller(self) -> bool:
        return self.mode == "sim" or self.hub.controller is self

    def _accept(self, op: Optional[str] = None) -> None:
        """A parsed, valid frame: liveness for the socket, and the watchdog feed if it is the controller's."""
        self.last_ok = self.hub.clock()
        if self._is_controller():
            self.ctl.touch()

    async def _on_hello(self, text: str) -> bool:
        hub = self.hub
        try:
            msg = protocol.parse_frame(text)
            if msg["t"] != "hello":
                raise ProtocolError("bad_request", "first frame must be hello")
        except ProtocolError as e:
            await self.ws.close(code=protocol.CLOSE_BAD_FIRST, message=e.msg.encode())
            return False
        if msg.get("v") != protocol.PROTOCOL_VERSION:
            await self.ws.close(code=protocol.CLOSE_VERSION, message=b"protocol mismatch, reload the page")
            return False
        try:
            h = protocol.validate_hello(msg)
        except ProtocolError as e:
            await self.ws.close(code=protocol.CLOSE_BAD_FIRST, message=e.msg.encode())
            return False
        self.last_ok = hub.clock()
        self.client_id, self.want = h["client_id"], h["want"]
        hub.sessions[self.id] = self
        dup = hub.live_client(self.client_id, self)
        mode, err = h["mode"], None
        if mode == "sim":
            err = self._enter_sim(h["world"])
            if err:
                mode = "car"
        if mode == "car":
            self._enter_car(dup, take=not err)   # a failed sim request never takes control of the car
        self.ready = True
        if err:
            self.send(err)
        self._announce()
        return True

    # ---- mode / control ------------------------------------------------------------------------------------
    def _enter_sim(self, world_id: Optional[str]) -> Optional[dict]:
        """Returns an err message dict on failure, None on success."""
        if self.hub.sim_count() >= self.hub.max_sims and self.sim is None:
            return {"t": "err", "code": "sim_busy", "msg": f"at most {self.hub.max_sims} simulator sessions"}
        try:
            self.sim = self._make_sim(world_id)
        except (KeyError, WorldError):
            return {"t": "err", "code": "bad_request", "msg": "unknown world"}
        self.mode, self.role = "sim", "controller"
        return None

    def _enter_car(self, dup: bool, take: bool = True) -> None:
        self.mode, self.role = "car", "viewer"
        if self.want == "control" and not dup and take:
            self.hub.take(self)

    def _control(self, action: str, force: bool) -> None:
        if self.mode != "car":
            self.send({"t": "role", "role": self.role, "controller_present": self.hub.controller is not None})
            return
        if action == "take":
            self.hub.take(self, force)
        else:
            self.hub.release(self)
        self.send({"t": "role", "role": self.role, "controller_present": self.hub.controller is not None})

    def _switch_mode(self, mode: str, world: Optional[str]) -> None:
        hub = self.hub
        if mode == "car":
            if self.mode == "sim":
                self.dispose_sim()
            elif self.hub.controller is self:
                return self._announce()
            dup = hub.live_client(self.client_id, self)
            self._enter_car(dup)
        else:
            if self.mode == "car":
                try:
                    load_world(world or DEFAULT_WORLD, hub.world_dir)
                except (KeyError, WorldError):
                    return self.err("bad_request", "unknown world")
                if hub.sim_count() >= hub.max_sims:
                    return self.err("sim_busy", f"at most {hub.max_sims} simulator sessions")
                hub.release(self)
                self._enter_sim(world)
            else:
                self.sim.stop_for_transition()
                try:
                    self.sim.backend.set_world(load_world(world or self.world_id or DEFAULT_WORLD, hub.world_dir))
                    self.world_id = self.sim.backend.world.id
                except (KeyError, WorldError):
                    return self.err("bad_request", "unknown world")
        self._announce()

    # ---- commands -------------------------------------------------------------------------------------------
    def _cmd(self, cid: int, op: str, f: dict) -> bool:
        """Runs one command; True when it was acked (an err reply returns False)."""
        hub = self.hub
        if self.mode == "car" and hub.controller is not self and op != "estop":
            self.err("not_controller", "only the controller can send this", cid)
            return False
        if op == "estop":
            by = "controller" if hub.controller is self else "viewer"
            hub.estop(self, by)
            self.send({"t": "ack", "id": cid, "ok": True})
            return True
        if op == "world":
            if self.mode != "sim":
                self.err("sim_only", "world applies to the simulator", cid)
                return False
            try:
                world = load_world(f["world"], hub.world_dir)
            except (KeyError, WorldError):
                self.err("bad_request", "unknown world", cid)
                return False
            self.sim.stop_for_transition()
            self.sim.backend.set_world(world)
            self.world_id = world.id
            self.send({"t": "ack", "id": cid, "ok": True})
            self._send_world()
            return True
        try:
            extra = self.ctl.command(op, f, owner=self, cmd_id=cid)
        except CmdError as e:
            self.err(e.code, e.msg, cid)
            return False
        self.send({"t": "ack", "id": cid, "ok": True, **extra})
        return True


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    hub: Hub = request.app["hub"]
    if not request.app["host_policy"].allowed(request.headers.get("Origin"), request.headers.get("Host")):
        raise web.HTTPForbidden(text="forbidden origin or host")
    ws = web.WebSocketResponse(heartbeat=5, max_msg_size=protocol.MAX_FRAME_BYTES)
    await ws.prepare(request)
    await Session(hub, ws).run()
    return ws
