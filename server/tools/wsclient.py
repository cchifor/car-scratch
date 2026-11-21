"""Command-line client for the car WebSocket (docs/protocol.md), for wheels-up tests without the GUI.

  python3 -m tools.wsclient --url ws://picar.local:8080/ws status
  python3 -m tools.wsclient --url ... drive forward 40 [--hold 3] [--steer -20]
  python3 -m tools.wsclient --url ... move forward 40 1000
  python3 -m tools.wsclient --url ... stop | steer -20 | estop | reset | resume | watch
  python3 -m tools.wsclient --url ... config obstacle_stop=false stop_cm=30 speed_limit=60

Needs control for everything except status/watch/estop (add --force to take it from another controller).
`drive`/`move` send `resume` first (the car is on hold after start and after any disconnect).
Ctrl-C while driving sends `stop` before the socket closes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import sys
from typing import Any, Optional

import aiohttp

DEFAULT_URL = "ws://picar.local:8080/ws"


def summarize_tel(t: dict) -> str:
    d, dist, s = t.get("drive") or {}, t.get("dist") or {}, t.get("safety") or {}
    cm = dist.get("cm")
    drive = f"{d['dir']}@{d.get('speed')}" if d.get("dir") else "stopped"
    return (f"tel #{t.get('seq')} {drive} duty={d.get('duty')} steer={t.get('steer')} "
            f"dist={'-' if cm is None else f'{cm:.1f}cm'}({dist.get('state')}) "
            f"margin={s.get('margin_cm')} blocked={s.get('blocked')} estop={s.get('estop')} "
            f"hold={s.get('hold')} hw_fault={s.get('hw_fault')}")


class Client:
    """One WebSocket session: hello, heartbeats, telemetry, ack/done tracking."""

    def __init__(self, url: str, want: str = "control", client_id: Optional[str] = None, quiet: bool = False,
                 mode: str = "car", world: Optional[str] = None, on_event=None, on_error=None):
        host = re.sub(r"[^A-Za-z0-9_-]", "", socket.gethostname())[:40] or "host"
        self.url, self.want, self.quiet = url, want, quiet
        self.mode, self.world = mode, world
        self.on_event, self.on_error = on_event, on_error      # optional callbacks(msg) for event / unmatched err
        self.client_id = client_id or f"wsclient-{host}"        # stable: a re-run regains control within the lease
        self.welcome: dict = {}
        self.tel: Optional[dict] = None
        self.role = "viewer"
        self.closed = asyncio.Event()
        self._next_id = 0
        self._replies: dict[int, asyncio.Future] = {}
        self._done: dict[int, asyncio.Future] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._tasks: list[asyncio.Task] = []
        self._tel_event = asyncio.Event()

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    async def __aenter__(self) -> "Client":
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self.url, heartbeat=5)
            await self._ws.send_json({"t": "hello", "v": 1, "client_id": self.client_id,
                                      "want": self.want, "mode": self.mode,
                                      **({"world": self.world} if self.world else {})})
            while True:
                msg = await asyncio.wait_for(self._ws.receive(), 5)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    raise ConnectionError(f"closed during handshake ({msg.type}, code {self._ws.close_code})")
                m = json.loads(msg.data)
                if m.get("t") == "welcome":
                    self.welcome, self.role = m, m.get("role", "viewer")
                    break
                if m.get("t") == "err":
                    raise ConnectionError(f"{m.get('code')}: {m.get('msg')}")
        except BaseException:
            await self._session.close()
            raise
        hb = max(0.05, self.welcome.get("hb_ms", 200) / 1000)
        self._tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._heartbeat(min(hb, 0.2)))]
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close(stop=self.want == "control")

    async def _heartbeat(self, period: float) -> None:
        while not self._ws.closed:
            await asyncio.sleep(period)
            try:
                await self._ws.send_json({"t": "hb"})
            except (ConnectionError, RuntimeError):
                return

    async def _reader(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                m = json.loads(msg.data)
                t = m.get("t")
                if t == "tel":
                    self.tel = m
                    self._tel_event.set()
                elif t in ("ack", "err") and m.get("id") in self._replies:
                    self._replies.pop(m["id"]).set_result(m)
                elif t == "err":
                    if self.on_error:
                        self.on_error(m)
                    self.log(f"ERR {m.get('code')}: {m.get('msg')}")
                elif t == "done" and m.get("id") in self._done:
                    self._done.pop(m["id"]).set_result(m)
                elif t == "role":
                    self.role = m.get("role", self.role)
                    self.log(f"role -> {self.role}")
                elif t == "event":
                    if self.on_event:
                        self.on_event(m)
                    self.log("event " + json.dumps({k: v for k, v in m.items() if k != "t"}))
        finally:
            self.closed.set()
            for f in [*self._replies.values(), *self._done.values()]:
                if not f.done():
                    f.set_result({"t": "err", "code": "disconnected", "msg": "socket closed"})

    async def cmd(self, op: str, timeout: float = 5.0, want_done: bool = False, **fields: Any) -> dict:
        """Send a cmd and return the ack/err message. With want_done the reply carries `_done` (a future)."""
        self._next_id += 1
        i = self._next_id
        loop = asyncio.get_running_loop()
        self._replies[i] = loop.create_future()
        done_fut = loop.create_future() if want_done else None
        if done_fut is not None:
            self._done[i] = done_fut
        await self._ws.send_json({"t": "cmd", "id": i, "op": op, **fields})
        reply = await asyncio.wait_for(self._replies[i], timeout)
        if want_done:
            reply["_done"] = done_fut
        return reply

    async def send(self, msg: dict) -> None:
        await self._ws.send_json(msg)

    async def wait_tel(self, timeout: float = 3.0) -> Optional[dict]:
        try:
            await asyncio.wait_for(self._tel_event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return self.tel

    @property
    def close_code(self) -> Optional[int]:
        return self._ws.close_code if self._ws is not None else None

    async def close(self, stop: bool = True) -> None:
        """Send `stop` first (when controlling), then close."""
        if self._ws is not None and not self._ws.closed:
            if stop:
                try:
                    await asyncio.wait_for(self._ws.send_json({"t": "cmd", "id": 1_000_000, "op": "stop"}), 1)
                    await asyncio.sleep(0.1)                   # let the frame leave before closing
                except Exception:                              # noqa: BLE001
                    pass
            try:
                await self._ws.close()
            except Exception:                                  # noqa: BLE001
                pass
        for t in self._tasks:
            t.cancel()
        if self._session is not None:
            await self._session.close()
            self._session = None


def _reply_ok(reply: dict) -> bool:
    if reply.get("t") == "ack":
        return True
    print(f"ERR {reply.get('code')}: {reply.get('msg')}", flush=True)
    return False


def _parse_kv(items: list[str]) -> dict:
    out: dict[str, Any] = {}
    for it in items:
        k, _, v = it.partition("=")
        if not _:
            raise SystemExit(f"expected key=value, got {it!r}")
        out[k] = json.loads(v) if v.lower() in ("true", "false") or re.fullmatch(r"-?\d+(\.\d+)?", v) else v
    return out


async def _hold(c: Client, seconds: Optional[float]) -> None:
    """Print telemetry (2 Hz) until `seconds` elapse, the socket closes, or Ctrl-C (None = until Ctrl-C)."""
    loop = asyncio.get_running_loop()
    end = None if seconds is None else loop.time() + seconds
    while not c.closed.is_set() and (end is None or loop.time() < end):
        if c.tel:
            print(summarize_tel(c.tel), flush=True)
        await asyncio.sleep(0.5)


async def run(a: argparse.Namespace) -> int:
    want = "view" if a.command in ("status", "watch", "estop") else "control"
    async with Client(a.url, want=want, client_id=a.client_id) as c:
        w = c.welcome
        print(f"connected as {c.role}; car_available={w.get('car_available')} hw={json.dumps(w.get('hw'))} "
              f"config={json.dumps(w.get('config'))} server={json.dumps(w.get('server'))}", flush=True)
        if want == "control" and a.force and c.role != "controller":
            await c.send({"t": "control", "action": "take", "force": True})
            await asyncio.sleep(0.3)
        if want == "control" and c.role != "controller":
            print("not the controller (use --force to take control)", flush=True)
            return 2
        cmd = a.command
        if cmd == "status":
            t = await c.wait_tel()
            print(summarize_tel(t) if t else "no telemetry received", flush=True)
            return 0
        if cmd == "watch":
            await _hold(c, a.seconds)
            return 0
        if cmd in ("drive", "move"):
            # The shared car starts on hold and every disconnect latches hold again: clear it explicitly.
            if not _reply_ok(await c.cmd("resume")):
                return 1
        if cmd in ("drive", "move") and a.steer is not None:   # only drive/move have --steer
            if not _reply_ok(await c.cmd("steer", angle=a.steer)):
                return 1
        if cmd == "drive":
            if not _reply_ok(await c.cmd("drive", dir=a.dir, speed=a.speed)):
                return 1
            print("driving; Ctrl-C stops", flush=True)
            await _hold(c, a.hold)
            return 0
        if cmd == "move":
            r = await c.cmd("move", want_done=True, dir=a.dir, speed=a.speed, ms=a.ms)
            fut = r.pop("_done")
            if not _reply_ok(r):
                return 1
            done = await asyncio.wait_for(fut, a.ms / 1000 + 5)
            print(f"done: {done.get('result')}", flush=True)
            return 0 if done.get("result") in ("completed", "stopped") else 1
        simple = {
            "stop": ("stop", {}), "estop": ("estop", {}), "reset": ("estop_reset", {}),
            "resume": ("resume", {}), "steer": ("steer", {"angle": a.angle if cmd == "steer" else 0}),
            "config": ("config", _parse_kv(a.pairs) if cmd == "config" else {}),
        }
        op, fields = simple[cmd]
        ok = _reply_ok(await c.cmd(op, **fields))
        print(f"{op}: {'ok' if ok else 'failed'}", flush=True)
        return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--client-id", default=None)
    p.add_argument("--force", action="store_true", help="take control from another controller first")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    w = sub.add_parser("watch")
    w.add_argument("--seconds", type=float, default=None)
    d = sub.add_parser("drive")
    d.add_argument("dir", choices=["forward", "backward"])
    d.add_argument("speed", type=int)
    d.add_argument("--hold", type=float, default=None, help="seconds to keep driving (default: until Ctrl-C)")
    d.add_argument("--steer", type=float, default=None)
    m = sub.add_parser("move")
    m.add_argument("dir", choices=["forward", "backward"])
    m.add_argument("speed", type=int)
    m.add_argument("ms", type=int)
    m.add_argument("--steer", type=float, default=None)
    sub.add_parser("stop")
    s = sub.add_parser("steer")
    s.add_argument("angle", type=float)
    sub.add_parser("estop")
    sub.add_parser("reset")
    sub.add_parser("resume")
    c = sub.add_parser("config")
    c.add_argument("pairs", nargs="+", help="obstacle_stop=false stop_cm=30 speed_limit=60")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("interrupted: stop sent", flush=True)
        return 130
    except (ConnectionError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        print(f"connection failed: {e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
