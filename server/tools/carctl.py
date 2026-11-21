"""carctl: a terminal app to monitor and drive the car (docs/carctl.md).

  carctl                       live dashboard (same as `watch`)
  carctl drive [--force]       dashboard + keyboard control (cruise style: Up/Down = speed, Left/Right = steer)
  carctl status [--json]       one snapshot
  carctl estop | reset | resume | stop | steer ANGLE | move forward|backward SPEED MS
  carctl events                stream events and errors until Ctrl-C

Run on the car (`ssh -t c4@picar.local carctl`), from the repo (`python server/tools/carctl.py --url ws://HOST:8080/ws`)
or as `python -m tools.carctl` from server/. Needs aiohttp and rich (`pip install aiohttp rich`).
Add --sim [WORLD] to practise against the simulator without the car.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

if __package__ in (None, ""):                       # run by path: make `tools.*` importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:                                 # pragma: no cover - exercised by hand
    sys.exit("carctl needs the 'rich' package: sudo apt install python3-rich  (workstation: pip install aiohttp rich)")

from tools.wsclient import DEFAULT_URL, Client

COMMANDS = ("watch", "drive", "status", "estop", "reset", "resume", "stop", "steer", "move", "events")
FATAL_CLOSE_CODES = (4400, 4409)                    # protocol.md §1: never reconnect after these
EVENT_LIMIT = 50


# ---- URLs ---------------------------------------------------------------------------------------------------

def resolve_url(url: Optional[str], host: Optional[str]) -> str:
    if host:
        h = host if ":" in host else f"{host}:8080"
        return f"ws://{h}/ws"
    return url or DEFAULT_URL


def http_base(ws_url: str) -> str:
    """ws://h:p/ws -> http://h:p"""
    scheme, _, rest = ws_url.partition("://")
    return ("https" if scheme == "wss" else "http") + "://" + rest.split("/", 1)[0]


async def fetch_api(base: str, timeout: float = 2.0) -> Optional[dict]:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(base + "/api/status") as r:
                return await r.json() if r.status == 200 else None
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        return None


# ---- keyboard -----------------------------------------------------------------------------------------------

_CSI_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left"}
_WIN_KEYS = {"H": "up", "P": "down", "K": "left", "M": "right"}


def decode_char(ch: str) -> str:
    """One typed character -> key token (lower-case letter, ' ', 'ctrl-c', 'esc', or the char itself)."""
    if ch == "\x03":
        return "ctrl-c"
    if ch == "\x1b":
        return "esc"
    return ch.lower()


def decode_posix(data: str) -> list[str]:
    """A chunk read from a terminal in cbreak mode -> key tokens (arrows arrive as ESC [ A..D or ESC O A..D)."""
    out: list[str] = []
    i = 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b" and i + 1 < len(data) and data[i + 1] in "[O":
            j = i + 2
            while j < len(data) and data[j] in "0123456789;":
                j += 1
            out.append(_CSI_KEYS.get(data[j], "?") if j < len(data) else "esc")
            i = j + 1
        else:
            out.append(decode_char(ch))
            i += 1
    return out


def decode_windows(first: str, second: Optional[str] = None) -> str:
    """msvcrt.getwch() results: arrows come as '\\xe0' or '\\x00' followed by H/P/K/M."""
    if first in ("\x00", "\xe0"):
        return _WIN_KEYS.get(second or "", "?")
    return decode_char(first)


KEY_ACTIONS = {
    "up": "speed+", "w": "speed+", "down": "speed-", "s": "speed-",
    "left": "steer-", "a": "steer-", "right": "steer+", "d": "steer+",
    "c": "center", " ": "stop", "space": "stop", "r": "resume", "e": "estop", "x": "reset",
    "q": "quit", "ctrl-c": "quit",
}


def key_to_action(key: str) -> Optional[str]:
    return KEY_ACTIONS.get(key)


class KeyReader:
    """Reads keys on a helper thread (termios cbreak + select, or msvcrt) and feeds an asyncio queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[str]"):
        self.loop, self.queue, self._stop = loop, queue, False
        self._thread = None
        self._saved = None

    def _push(self, key: str) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, key)

    def start(self) -> None:
        import threading
        if os.name == "nt":
            target = self._run_windows
        else:
            import termios
            import tty
            fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~termios.ISIG                # Ctrl-C arrives as a key, so we always get to stop the car
            termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
            target = self._run_posix
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._saved is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved)
            self._saved = None

    def _run_posix(self) -> None:
        import select
        fd = sys.stdin.fileno()
        while not self._stop:
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    continue
                data = os.read(fd, 64)
            except (OSError, ValueError):
                break
            if not data:
                self._push("ctrl-c")                 # stdin closed: stop the car and leave
                break
            for k in decode_posix(data.decode("utf-8", "ignore")):
                self._push(k)

    def _run_windows(self) -> None:
        import msvcrt
        while not self._stop:
            if not msvcrt.kbhit():
                time.sleep(0.02)
                continue
            ch = msvcrt.getwch()
            self._push(decode_windows(ch, msvcrt.getwch()) if ch in ("\x00", "\xe0") else decode_windows(ch))


# ---- drive state machine ------------------------------------------------------------------------------------

Cmd = tuple  # (op, fields)


class DriveState:
    """Cruise-control state: signed target speed (negative = backward) and steering angle; no I/O."""
    SPEED_STEP = 10
    STEER_STEP = 10

    def __init__(self, speed_max: int = 100, reverse_max: int = 40, steer_max: int = 45):
        self.speed_max, self.reverse_max, self.steer_max = speed_max, reverse_max, steer_max
        self.speed = 0
        self.steer = 0
        self.quit = False

    @classmethod
    def from_welcome(cls, welcome: dict) -> "DriveState":
        lim = welcome.get("limits") or {}
        return cls(int(lim.get("speed_max", 100)), int(lim.get("reverse_speed_max", 40)), int(lim.get("steer_max", 45)))

    def _drive(self) -> Cmd:
        if self.speed == 0:
            return ("stop", {})
        return ("drive", {"dir": "forward" if self.speed > 0 else "backward", "speed": abs(self.speed)})

    def apply(self, action: str) -> list[Cmd]:
        """Update the state for one action and return the commands to send, in order."""
        if action == "speed+":
            self.speed = min(self.speed_max, self.speed + self.SPEED_STEP)
            return [self._drive()]
        if action == "speed-":
            self.speed = max(-self.reverse_max, self.speed - self.SPEED_STEP)
            return [self._drive()]
        if action in ("steer-", "steer+"):
            delta = -self.STEER_STEP if action == "steer-" else self.STEER_STEP
            self.steer = max(-self.steer_max, min(self.steer_max, self.steer + delta))
            return [("steer", {"angle": self.steer})]
        if action == "center":
            self.steer = 0
            return [("steer", {"angle": 0})]
        if action == "stop":
            self.speed = 0
            return [("stop", {})]
        if action == "resume":
            return [("resume", {})]
        if action == "estop":
            self.speed = 0
            return [("estop", {})]
        if action == "reset":
            return [("estop_reset", {})]
        if action == "quit":
            self.speed = 0
            self.quit = True
            return [("stop", {})]
        return []


STOPPING_EVENTS = ("obstacle_stop", "estop", "watchdog_stop", "hold", "reverse_timeout", "drive_timeout",
                   "sensor_fault", "hw_fault")


class Driver:
    """Runs DriveState actions against a connected client (anything with async cmd(op, **fields) and send(msg))."""

    def __init__(self, link: "Link"):
        self.link = link
        self.state = DriveState()
        self.ack_speed: Optional[int] = None
        self.ack_steer: Optional[float] = None
        self._generation = -1
        self.done = asyncio.Event()

    def sync(self) -> None:
        """A new connection starts on hold with the car stopped: forget the old target."""
        if self._generation != self.link.generation:
            self._generation = self.link.generation
            self.state = DriveState.from_welcome(self.link.welcome)
            self.ack_speed = None

    def on_event(self, kind: str) -> None:
        if kind in STOPPING_EVENTS:
            self.state.speed = 0
            self.ack_speed = None

    async def handle(self, action: str) -> None:
        self.sync()
        client = self.link.client
        for op, fields in self.state.apply(action):
            if client is None:
                self.link.note_error("not connected")
                continue
            try:
                reply = await client.cmd(op, **fields)
            except (asyncio.TimeoutError, ConnectionError, RuntimeError) as e:
                self.link.note_error(f"{op}: {e or 'no reply'}")
                continue
            if reply.get("t") == "ack":
                if op == "drive":
                    self.ack_speed = reply.get("speed")
                elif op in ("stop", "estop"):
                    self.ack_speed = None
                elif op == "steer":
                    self.ack_steer = reply.get("angle")
            else:
                self.link.note_error(f"{op}: {reply.get('code')}: {reply.get('msg')}")
                if op == "drive":                    # refused: the car is not moving, do not keep a stale target
                    self.state.speed = 0
                    self.ack_speed = None
        if self.state.quit:
            self.done.set()

    async def shutdown(self) -> None:
        """Always: stop the car and release control (also after an exception)."""
        client = self.link.client
        if client is None:
            return
        try:
            await asyncio.wait_for(client.cmd("stop"), 1.5)
        except Exception:                            # noqa: BLE001
            pass
        try:
            await client.send({"t": "control", "action": "release"})
        except Exception:                            # noqa: BLE001
            pass

    def target(self) -> dict:
        return {"speed": self.state.speed, "steer": self.state.steer, "ack_speed": self.ack_speed}


# ---- link: connection supervisor ----------------------------------------------------------------------------

@dataclass
class View:
    """Everything the dashboard shows; plain data so tests can build one."""
    url: str = ""
    title: str = "watch"
    conn: str = "connected"
    conn_error: str = ""
    reconnects: int = 0
    role: str = "viewer"
    welcome: dict = field(default_factory=dict)
    tel: Optional[dict] = None
    tel_age: Optional[float] = None
    api: Optional[dict] = None
    events: list = field(default_factory=list)      # [(unix_ts, kind, detail)] oldest first
    hold_reason: Optional[str] = None
    last_error: Optional[tuple] = None              # (unix_ts, text)
    target: Optional[dict] = None                   # drive mode: speed, steer, ack_speed
    help: str = ""


def event_detail(m: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in m.items() if k not in ("t", "kind"))


class Link:
    """One logical viewer/controller session that reconnects by itself and keeps the data the dashboard needs."""

    def __init__(self, url: str, want: str = "view", client_id: Optional[str] = None, force: bool = False,
                 mode: str = "car", world: Optional[str] = None, factory: Callable[..., Client] = Client,
                 api_poll: float = 2.0, on_event: Optional[Callable[[dict], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None, on_state: Optional[Callable[[str], None]] = None):
        self.url, self.want, self.client_id, self.force = url, want, client_id, force
        self.mode, self.world, self.factory, self.api_poll = mode, world, factory, api_poll
        self._extra_event, self._extra_error, self._extra_state = on_event, on_error, on_state
        self.client: Optional[Client] = None
        self.conn = "connecting"
        self.conn_error = ""
        self.reconnects = 0
        self.generation = 0
        self.fatal: Optional[str] = None
        self.welcome: dict = {}
        self.api: Optional[dict] = None
        self.events: deque = deque(maxlen=EVENT_LIMIT)
        self.hold_reason: Optional[str] = None
        self.last_error: Optional[tuple] = None
        self.first = asyncio.Event()                # set on the first connection or a fatal failure
        self._stop = asyncio.Event()
        self._tel_seq: Any = None
        self._tel_at: Optional[float] = None
        self.listeners: list[Callable[[str], None]] = []

    # -- data from the client
    def _on_event(self, m: dict) -> None:
        kind = m.get("kind", "?")
        self.events.append((time.time(), kind, event_detail(m)))
        if kind == "hold":
            self.hold_reason = m.get("reason")
        elif kind == "resumed":
            self.hold_reason = None
        for fn in self.listeners:
            fn(kind)
        if self._extra_event:
            self._extra_event(m)

    def _on_error(self, m: dict) -> None:
        self.note_error(f"{m.get('code')}: {m.get('msg')}")

    def note_error(self, text: str) -> None:
        self.last_error = (time.time(), text)
        if self._extra_error:
            self._extra_error(text)

    def _set_conn(self, state: str, detail: str = "") -> None:
        changed = state != self.conn
        self.conn, self.conn_error = state, detail
        if changed and self._extra_state:
            self._extra_state(state + (f" ({detail})" if detail else ""))

    # -- supervisor
    async def _sleep(self, secs: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), secs)
        except asyncio.TimeoutError:
            pass

    async def _poll_api(self) -> None:
        base = http_base(self.url)
        while not self._stop.is_set():
            self.api = await fetch_api(base)
            await self._sleep(self.api_poll)

    async def run(self) -> None:
        poller = asyncio.create_task(self._poll_api()) if self.api_poll else None
        delay, ever = 0.5, False
        try:
            while not self._stop.is_set():
                c = self.factory(self.url, want=self.want, client_id=self.client_id, quiet=True, mode=self.mode,
                                 world=self.world, on_event=self._on_event, on_error=self._on_error)
                try:
                    await c.__aenter__()
                except (ConnectionError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                    msg = str(e) or type(e).__name__
                    if any(str(code) in msg for code in FATAL_CLOSE_CODES):
                        self.fatal = msg
                        self._set_conn("closed", msg)
                        self.first.set()
                        return
                    self._set_conn("reconnecting" if ever else "connecting", msg)
                    await self._sleep(delay)
                    delay = min(delay * 2, 3.0)
                    continue
                self.client, self.welcome = c, c.welcome
                self.generation += 1
                if ever:
                    self.reconnects += 1
                ever, delay = True, 0.5
                self.hold_reason = "control" if self.want == "control" else None
                if self.want == "control" and self.force and c.role != "controller":
                    await c.send({"t": "control", "action": "take", "force": True})
                    for _ in range(20):
                        if c.role == "controller":
                            break
                        await asyncio.sleep(0.05)
                self._set_conn("connected")
                self.first.set()
                closed = asyncio.create_task(c.closed.wait())
                stopped = asyncio.create_task(self._stop.wait())
                await asyncio.wait({closed, stopped}, return_when=asyncio.FIRST_COMPLETED)
                closed.cancel()
                stopped.cancel()
                code = c.close_code
                await c.close(stop=self.want == "control" and self._stop.is_set())
                self.client = None
                if self._stop.is_set():
                    return
                if code in FATAL_CLOSE_CODES:
                    self.fatal = f"server closed the connection (code {code}); not reconnecting"
                    self._set_conn("closed", self.fatal)
                    return
                self._set_conn("reconnecting", f"connection lost (code {code})")
        finally:
            if poller:
                poller.cancel()
            self.first.set()

    async def stop(self) -> None:
        self._stop.set()

    # -- snapshot
    def snapshot(self, title: str = "watch", target: Optional[dict] = None, help: str = "") -> View:
        c = self.client
        tel = c.tel if c is not None else None
        age = None
        if tel is not None:
            now = time.monotonic()
            if tel.get("seq") != self._tel_seq:
                self._tel_seq, self._tel_at = tel.get("seq"), now
            age = now - (self._tel_at or now)
        return View(url=self.url, title=title, conn=self.conn, conn_error=self.conn_error,
                    reconnects=self.reconnects, role=c.role if c is not None else "-", welcome=self.welcome,
                    tel=tel, tel_age=age, api=self.api, events=list(self.events), hold_reason=self.hold_reason,
                    last_error=self.last_error, target=target, help=help)


# ---- dashboard rendering ------------------------------------------------------------------------------------

def _fmt(v: Any, spec: str = "", unit: str = "") -> str:
    if v is None:
        return "-"
    return (format(v, spec) if spec else str(v)) + unit


def _hms(s: Any) -> str:
    if s is None:
        return "-"
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _flag(on: Any, bad: bool, yes: str = "YES", no: str = "no") -> Text:
    """A coloured yes/no. `bad` says whether `on` being true is a problem."""
    on = bool(on)
    return Text(yes if on else no, style=("bold red" if bad else "green") if on else ("green" if bad else "dim"))


def _bar(frac: Optional[float], width: int = 12, style: str = "green") -> Text:
    if frac is None:
        return Text("." * width, style="dim")
    n = max(0, min(width, round(frac * width)))
    t = Text("█" * n, style=style)
    t.append("░" * (width - n), style="dim")
    return t


def _kv(label: str, value: Any, w: int = 15) -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(f"{label:<{w}}", style="dim")
    t.append(value if isinstance(value, Text) else Text(str(value)))
    return t


def _line(*parts: Any) -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")
    for p in parts:
        t.append(p if isinstance(p, Text) else Text(str(p)))
    return t


def _sections(v: View) -> dict:
    t = v.tel or {}
    return {k: t.get(k) or {} for k in ("drive", "dist", "line", "light", "pose", "safety", "power", "ctl", "move")} | {
        "steer": t.get("steer"), "obstacle": t.get("obstacle"), "mode": t.get("mode")}


def _mode_label(v: View) -> str:
    mode = (v.tel or {}).get("mode") or v.welcome.get("mode") or "-"
    backend = (v.welcome.get("hw") or {}).get("backend")
    return mode if mode == "sim" or not backend else f"{mode}/{backend}"


def header_lines(v: View) -> list[Text]:
    srv = v.welcome.get("server") or {}
    conn_style = {"connected": "bold green", "connecting": "yellow", "reconnecting": "bold yellow"}.get(v.conn, "bold red")
    conn = Text(v.conn, style=conn_style)
    if v.reconnects:
        conn.append(f"  reconnects {v.reconnects}", style="dim")
    stale = Text(f"  {v.conn_error}", style="dim") if v.conn_error and v.conn != "connected" else Text("")
    if v.conn == "connected" and v.tel is None:
        stale = Text("  waiting for telemetry", style="yellow")
    elif v.tel_age is not None and v.tel_age > 1.5:
        stale = Text(f"  telemetry stale {v.tel_age:.0f}s", style="bold yellow")
    cl = (v.api or {}).get("clients")
    others = (f"controller {'yes' if cl.get('car_controller') else 'no'}, viewers {cl.get('car_viewers')}, "
              f"sims {cl.get('sim_sessions')}") if cl else "-"
    role_style = "bold green" if v.role == "controller" else "cyan"
    return [
        _line(Text("carctl ", style="bold"), Text(v.title, style="bold cyan"), "  ", v.url),
        _line(Text("link ", style="dim"), conn, stale, "  role ", Text(v.role, style=role_style), "  mode ", _mode_label(v)),
        _line(Text("server ", style="dim"), f"v{srv.get('version', '-')} ({srv.get('sha', '-')})", "  ",
              Text("clients ", style="dim"), others),
    ]


def safety_lines(v: View) -> list[Text]:
    s = _sections(v)["safety"]
    hold = s.get("hold")
    hold_t = _flag(hold, True, "HOLD", "running")
    if hold and v.hold_reason:
        hold_t.append(f" ({v.hold_reason})", style="red")
    fault = s.get("hw_fault")
    return [
        _kv("E-STOP", _flag(s.get("estop"), True, "LATCHED", "ok")),
        _kv("Hold", hold_t),
        _kv("Blocked", _flag(s.get("blocked"), True, "BLOCKED", "clear")),
        _kv("Obstacle stop", _flag(s.get("obstacle_stop"), False, "on", "OFF")),
        _kv("Stop dist", _fmt(s.get("stop_cm"), "", " cm")),
        _kv("Margin now", _fmt(s.get("margin_cm"), ".1f", " cm")),
        _kv("Speed limit", _fmt(s.get("speed_limit"))),
        _kv("Calibrated", _flag(s.get("calibrated"), False, "yes", "no")),
        _kv("HW fault", Text(str(fault), style="bold red") if fault else Text("none", style="green")),
    ]


def drive_lines(v: View) -> list[Text]:
    s = _sections(v)
    d, steer = s["drive"], s["steer"]
    direction = d.get("dir") or "stopped"
    duty = d.get("duty") or [None, None]
    mv = s["move"]
    lines = [
        _kv("Direction", Text(direction, style="bold" if d.get("dir") else "dim")),
        _kv("Speed", _fmt(d.get("speed"))),
        _kv("Duty L/R", f"{_fmt(duty[0])} / {_fmt(duty[1])}"),
        _kv("Steering", f"{_fmt(steer, '+.0f' if steer is not None else '', ' deg')}"),
        _kv("Timed move", f"#{mv.get('id')} {mv.get('remaining_ms')} ms left" if mv else "none"),
    ]
    if d.get("bumped") is not None:
        lines.append(_kv("Bumped", _flag(d.get("bumped"), True)))
    if v.target is not None:
        ack = v.target.get("ack_speed")
        lines.append(_kv("Target", Text(f"speed {v.target['speed']:+d}  steer {v.target['steer']:+d}", style="bold cyan")))
        lines.append(_kv("Server speed", _fmt(ack) if ack is not None else "-"))
    return lines


def dist_lines(v: View) -> list[Text]:
    d, s = _sections(v)["dist"], _sections(v)["safety"]
    cm, state = d.get("cm"), d.get("state") or "-"
    margin = s.get("margin_cm") or 0
    style = "red" if cm is not None and cm < margin else ("yellow" if cm is not None and cm < 2 * margin else "green")
    bar = _bar(None if cm is None else min(cm, 200) / 200, 20, style)
    label = {"ok": "ok", "far": "far (nothing in range)", "none": "no echo", "fault": "SENSOR FAULT"}.get(state, state)
    return [_line(Text(f"{_fmt(cm, '.1f', ' cm'):>9} ", style="bold"), bar),
            _kv("State", Text(label, style="bold red" if state == "fault" else ("yellow" if state == "none" else "")))]


def line_lines(v: View) -> list[Text]:
    ln = _sections(v)["line"]
    if not ln or not ln.get("ok"):
        return [Text("line follower offline", style="dim")]
    raw, on, pos = ln.get("raw") or [None] * 5, ln.get("on") or [None] * 5, ln.get("pos")
    top = max([r for r in raw if r is not None] + [255])
    out = []
    for i in range(5):
        mark = Text("●", style="bold green") if on[i] else Text("○", style="dim")
        out.append(_line(f"S{i + 1} ", _bar(None if raw[i] is None else raw[i] / top, 10), f" {_fmt(raw[i]):>5} ", mark))
    track = Text("  ")
    for p in (-2, -1, 0, 1, 2):
        track.append(" ● " if p == pos else " · ", style="bold green" if p == pos else "dim")
    out.append(_line(Text("pos ", style="dim"), _fmt(pos, "+d" if pos is not None else ""), "  [", track, "]"))
    return out


def light_lines(v: View) -> list[Text]:
    lt = _sections(v)["light"]
    if not lt or not lt.get("ok"):
        return [Text("light sensors offline", style="dim")]
    bright, raw = lt.get("bright") or [None] * 3, lt.get("raw") or [None] * 3
    out = [_line(f"{n} ", _bar(None if b is None else b / 100, 12, "yellow"), f" {_fmt(b):>3}%  raw {_fmt(r)}")
           for n, b, r in zip(("L", "M", "R"), bright, raw)]
    out.append(_kv("Brightest", lt.get("max") or "-"))
    return out


def pose_lines(v: View) -> list[Text]:
    p = _sections(v)["pose"]
    if not p:
        return [Text("no pose", style="dim")]
    return [_kv("x / y", f"{_fmt(p.get('x'), '.1f')} / {_fmt(p.get('y'), '.1f')} cm"),
            _kv("Heading", _fmt(p.get("dir"), ".1f", " deg")), _kv("Source", {"dr": "dead reckoning", "sim": "simulator"}.get(p.get("src"), p.get("src") or "-"))]


def power_lines(v: View) -> list[Text]:
    p = _sections(v)["power"]
    if not p:
        return [Text("no power data", style="dim")]
    src = p.get("source") or "none"
    temp = p.get("cpu_temp_c")
    temp_t = Text(_fmt(temp, ".1f", " C"), style="red" if temp and temp > 75 else ("yellow" if temp and temp > 65 else ""))
    lines = [_kv("Source", src + ("  (no supply monitor: values below are not measured)" if src == "none" else ""))]
    if src == "none":
        return [_kv("Source", "none: no power data")] + [_kv("CPU temp", temp_t), _kv("CPU load", _fmt(p.get("cpu_load"), ".2f"))]
    return lines + [
        _kv("Low now", _flag(p.get("low"), True)),
        _kv("Low since boot", _flag(p.get("low_since_boot"), True)),
        _kv("Low events", _fmt(p.get("low_events"))),
        _kv("Throttled", _flag(p.get("throttled"), True)),
        _kv("On time", _hms(p.get("on_s"))),
        _kv("Motor time", _hms(p.get("motor_s"))),
        _kv("CPU temp", temp_t),
        _kv("CPU load", _fmt(p.get("cpu_load"), ".2f")),
    ]


def event_lines(v: View, n: int) -> list[Text]:
    if n <= 0:
        return []
    if not v.events:
        return [Text("no events yet", style="dim")]
    out = []
    for ts, kind, detail in v.events[-n:][::-1]:
        bad = kind in ("obstacle_stop", "estop", "watchdog_stop", "sensor_fault", "hw_fault", "power_low", "reverse_timeout", "drive_timeout")
        out.append(_line(Text(_clock(ts) + " ", style="dim"), Text(kind, style="bold red" if bad else "bold"), " ", detail))
    return out


def footer_lines(v: View) -> list[Text]:
    out = []
    if v.last_error:
        out.append(_line(Text("last error ", style="dim"), Text(_clock(v.last_error[0]) + " ", style="dim"),
                         Text(v.last_error[1], style="red")))
    else:
        out.append(Text("last error: none", style="dim"))
    if v.help:
        out.append(_line(Text(v.help, style="cyan")))
    return out


def _compact(v: View, height: int) -> list[Text]:
    s = _sections(v)
    sf, d, dist, ln, lt, pw = s["safety"], s["drive"], s["dist"], s["line"], s["light"], s["power"]
    fault = sf.get("hw_fault")
    hold = "HOLD" + (f"({v.hold_reason})" if v.hold_reason else "") if sf.get("hold") else "running"
    lines = header_lines(v)[:2] + [header_lines(v)[2]]
    lines.append(_line(Text("SAFETY ", style="bold"), "estop ", _flag(sf.get("estop"), True, "LATCHED", "ok"), "  ",
                       Text(hold, style="red" if sf.get("hold") else "green"), "  blocked ",
                       _flag(sf.get("blocked"), True, "YES", "no"), "  hw ",
                       Text(str(fault), style="red") if fault else Text("ok", style="green")))
    lines.append(_line(Text("        ", style="bold"),
                       f"obstacle-stop {'on' if sf.get('obstacle_stop') else 'OFF'}  stop {_fmt(sf.get('stop_cm'))}cm  "
                       f"margin {_fmt(sf.get('margin_cm'), '.1f')}  limit {_fmt(sf.get('speed_limit'))}  "
                       f"cal {'yes' if sf.get('calibrated') else 'no'}"))
    tgt = ""
    if v.target is not None:
        tgt = f"  | target {v.target['speed']:+d}/{v.target['steer']:+d} server {_fmt(v.target.get('ack_speed'))}"
    lines.append(_line(Text("DRIVE  ", style="bold"),
                       f"{d.get('dir') or 'stopped'} {_fmt(d.get('speed'))}  duty {_fmt((d.get('duty') or [None, None])[0])}/"
                       f"{_fmt((d.get('duty') or [None, None])[1])}  steer {_fmt(s['steer'], '+.0f')}{tgt}"))
    cm = dist.get("cm")
    lines.append(_line(Text("DIST   ", style="bold"), f"{_fmt(cm, '.1f', ' cm'):>8} ",
                       _bar(None if cm is None else min(cm, 200) / 200, 12, "green"), f" {dist.get('state') or '-'}"))
    if ln.get("ok"):
        on = ln.get("on") or []
        lines.append(_line(Text("LINE   ", style="bold"), " ".join("●" if x else "○" for x in on),
                           f"  pos {_fmt(ln.get('pos'), '+d' if ln.get('pos') is not None else '')}  raw {ln.get('raw')}"))
    else:
        lines.append(_line(Text("LINE   ", style="bold"), Text("offline", style="dim")))
    if lt.get("ok"):
        lines.append(_line(Text("LIGHT  ", style="bold"), "L/M/R ", "/".join(_fmt(b) for b in lt.get("bright") or []),
                           f"%  brightest {lt.get('max')}"))
    else:
        lines.append(_line(Text("LIGHT  ", style="bold"), Text("offline", style="dim")))
    p = s["pose"]
    lines.append(_line(Text("POSE   ", style="bold"), f"x {_fmt(p.get('x'), '.1f')} y {_fmt(p.get('y'), '.1f')} "
                       f"dir {_fmt(p.get('dir'), '.0f')} ({p.get('src') or '-'})"))
    if pw and (pw.get("source") or "none") != "none":
        lines.append(_line(Text("POWER  ", style="bold"), "low ", _flag(pw.get("low"), True), " since-boot ",
                           _flag(pw.get("low_since_boot"), True), f" events {pw.get('low_events')} ",
                           "throttled ", _flag(pw.get("throttled"), True)))
        lines.append(_line(Text("        ", style="bold"),
                           f"on {_hms(pw.get('on_s'))} motor {_hms(pw.get('motor_s'))} cpu {_fmt(pw.get('cpu_temp_c'), '.0f', 'C')} "
                           f"load {_fmt(pw.get('cpu_load'), '.2f')}"))
    else:
        lines.append(_line(Text("POWER  ", style="bold"), Text("no power data", style="dim"),
                           f"  cpu {_fmt(pw.get('cpu_temp_c'), '.0f', 'C')} load {_fmt(pw.get('cpu_load'), '.2f')}" if pw else ""))
    foot = footer_lines(v)
    room = height - len(lines) - len(foot) - 1
    if room > 0:
        lines.append(Text("EVENTS", style="bold"))
        lines += event_lines(v, room)
    return lines + foot


def _panel(title: str, lines: list[Text], rows: int, style: str = "") -> Panel:
    return Panel(Group(*lines), title=title, title_align="left", height=rows + 2, border_style=style or "dim")


def render_dashboard(v: View, width: int = 100, height: int = 40) -> Any:
    """The whole screen as a rich renderable. Wide and tall terminals get panels; others a compact text view."""
    if width < 100 or height < 38:
        return Group(*_compact(v, height))
    row1 = [("Safety", safety_lines(v)), ("Drive", drive_lines(v)), ("Distance", dist_lines(v))]
    row2 = [("Line follower", line_lines(v)), ("Light", light_lines(v)), ("Pose", pose_lines(v))]
    ev = event_lines(v, 10)
    row3 = [("Power", power_lines(v)), ("Events (newest first)", ev)]
    n3 = max(len(row3[0][1]), len(ev), 1)
    table = Table.grid(expand=True)                 # power (1/3) + events (2/3) under the 3-column rows
    table.add_column(ratio=1)
    table.add_column(ratio=2)
    table.add_row(_panel(row3[0][0], row3[0][1], n3), _panel(row3[1][0], row3[1][1], n3))
    top = Table.grid(expand=True)
    top.add_column(ratio=1)
    top.add_column(ratio=1)
    top.add_column(ratio=1)
    for row in (row1, row2):
        n = max(len(x[1]) for x in row)
        top.add_row(*[_panel(t, ls, n) for t, ls in row])
    return Group(Panel(Group(*header_lines(v)), title="carctl", title_align="left", border_style="cyan"), top, table,
                 *footer_lines(v))


def view_text(v: View, width: int, height: int = 60) -> str:
    """Render to plain text (tests, docs, one-shot status)."""
    c = Console(width=width, height=height, record=True, file=_Sink(), force_terminal=False, color_system=None)
    c.print(render_dashboard(v, width, height))
    return c.export_text()


class _Sink:
    def write(self, s: str) -> int:
        return len(s)

    def flush(self) -> None:
        pass


# ---- commands -----------------------------------------------------------------------------------------------

def _sim(args: argparse.Namespace) -> tuple[str, Optional[str]]:
    if args.sim is None:
        return "car", None
    return "sim", args.sim or None


DRIVE_HELP = "Up/Down speed  Left/Right steer  C center  Space stop  R resume  E E-STOP  X reset  Q quit"


def _help_for(v: View, driving: bool) -> str:
    if not driving:
        return "viewing only (carctl drive to take control); Ctrl-C quits"
    if v.role != "controller":
        return "VIEW ONLY: another client has control (restart with --force to take it). Q quits"
    safety = (v.tel or {}).get("safety") or {}
    if safety.get("estop"):
        return "E-STOP LATCHED: X resets it.  " + DRIVE_HELP
    if safety.get("hold"):
        return "ON HOLD: press R to resume.  " + DRIVE_HELP
    return DRIVE_HELP


async def run_dashboard(args: argparse.Namespace, driving: bool) -> int:
    mode, world = _sim(args)
    console = Console()
    if driving and not (sys.stdin.isatty() and console.is_terminal):
        print("drive needs an interactive terminal (over ssh use: ssh -t HOST carctl drive)", flush=True)
        return 1
    want = "control" if driving else "view"
    link = Link(args.url, want=want, client_id=args.client_id or _default_id("" if driving else "-watch"),
                force=args.force and driving, mode=mode, world=world)
    driver = Driver(link) if driving else None
    if driver:
        link.listeners.append(driver.on_event)
    runner = asyncio.create_task(link.run())
    keys = consumer = None
    try:
        if driving:
            try:
                await asyncio.wait_for(link.first.wait(), 10)
            except asyncio.TimeoutError:
                pass
            if link.client is None:
                print(f"cannot connect to {args.url}: {link.fatal or link.conn_error or 'timed out'}", flush=True)
                return 1
            if link.client.role != "controller":
                print("someone else is driving the car; use --force to take control", flush=True)
                return 1
            queue: asyncio.Queue = asyncio.Queue()
            keys = KeyReader(asyncio.get_running_loop(), queue)
            keys.start()

            async def consume() -> None:
                while True:
                    action = key_to_action(await queue.get())
                    if action:
                        await driver.handle(action)

            consumer = asyncio.create_task(consume())
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while not runner.done() and not (driver and driver.done.is_set()):
                if driver:
                    driver.sync()
                v = link.snapshot("drive" if driving else "watch", driver.target() if driver else None)
                v.help = _help_for(v, driving)
                w, h = console.size
                live.update(render_dashboard(v, w, h), refresh=True)
                await asyncio.sleep(0.125)
        if link.fatal:
            print(link.fatal, flush=True)
            return 1
        return 0
    finally:
        if keys:
            keys.stop()
        if consumer:
            consumer.cancel()
        if driving:
            await driver.shutdown()
        await link.stop()
        try:
            await asyncio.wait_for(runner, 3)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):    # noqa: BLE001
            runner.cancel()


def _default_id(suffix: str = "") -> str:
    host = re.sub(r"[^A-Za-z0-9_-]", "", socket.gethostname())[:30] or "host"
    return f"carctl-{host}{suffix}"                 # stable per host: a re-run regains control within the lease


def _fmt_event_line(ts: float, kind: str, detail: str) -> str:
    return f"{_clock(ts)} {kind} {detail}".rstrip()


async def run_events(args: argparse.Namespace) -> int:
    mode, world = _sim(args)

    def on_event(m: dict) -> None:
        print(_fmt_event_line(time.time(), m.get("kind", "?"), event_detail(m)), flush=True)

    link = Link(args.url, want="view", client_id=args.client_id or _default_id("-events"), mode=mode, world=world,
                api_poll=0, on_event=on_event,
                on_error=lambda t: print(f"{_clock(time.time())} ERROR {t}", flush=True),
                on_state=lambda s: print(f"{_clock(time.time())} connection {s}", flush=True))
    try:
        await link.run()
    finally:
        await link.stop()
    return 1 if link.fatal else 0


def _reply_ok(reply: dict) -> bool:
    if reply.get("t") == "ack":
        return True
    print(f"ERR {reply.get('code')}: {reply.get('msg')}", flush=True)
    return False


async def run_oneshot(args: argparse.Namespace) -> int:
    cmd = args.command
    mode, world = _sim(args)
    want = "view" if cmd in ("status", "estop") else "control"
    async with Client(args.url, want=want, client_id=args.client_id or _default_id(), quiet=True, mode=mode,
                      world=world) as c:
        if want == "control" and args.force and c.role != "controller":
            await c.send({"t": "control", "action": "take", "force": True})
            for _ in range(20):
                if c.role == "controller":
                    break
                await asyncio.sleep(0.05)
        if want == "control" and c.role != "controller":
            print("not the controller: someone else is driving (use --force to take control)", flush=True)
            return 1
        if cmd == "status":
            tel = await c.wait_tel()
            api = await fetch_api(http_base(args.url))
            if args.json:
                print(json.dumps({"welcome": c.welcome, "tel": tel, "api_status": api}, indent=2), flush=True)
                return 0
            v = View(url=args.url, title="status", role=c.role, welcome=c.welcome, tel=tel, api=api, tel_age=0 if tel else None)
            width = max(60, min(Console().width, 140))
            print(view_text(v, width, 24 if width < 100 else 60).rstrip(), flush=True)
            return 0 if tel else 1
        if cmd == "move":
            for _ in range(40):                          # a fresh server needs ~1 sensor sample before it will move
                if ((await c.wait_tel(1)) or {}).get("dist", {}).get("state") in ("ok", "far"):
                    break
                await asyncio.sleep(0.05)
            if not _reply_ok(await c.cmd("resume")):
                return 1
            r = await c.cmd("move", want_done=True, dir=args.dir, speed=args.speed, ms=args.ms)
            fut = r.pop("_done")
            if not _reply_ok(r):
                return 1
            done = await asyncio.wait_for(fut, args.ms / 1000 + 5)
            print(f"done: {done.get('result')}", flush=True)
            return 0 if done.get("result") == "completed" else 1
        op, fields = {
            "estop": ("estop", {}), "reset": ("estop_reset", {}), "resume": ("resume", {}), "stop": ("stop", {}),
            "steer": ("steer", {"angle": getattr(args, "angle", 0)}),
        }[cmd]
        reply = await c.cmd(op, **fields)
        ok = _reply_ok(reply)
        if ok:
            extra = {k: val for k, val in reply.items() if k not in ("t", "id", "ok")}
            print(f"{op}: ok" + (f" {json.dumps(extra)}" if extra else ""), flush=True)
        return 0 if ok else 1


# ---- CLI ----------------------------------------------------------------------------------------------------

def _common(p: argparse.ArgumentParser, top: bool) -> None:
    d = (lambda v: v) if top else (lambda v: argparse.SUPPRESS)
    p.add_argument("--url", default=d(None), help=f"WebSocket URL (default {DEFAULT_URL})")
    p.add_argument("--host", default=d(None), help="HOST[:PORT] shortcut for --url (port 8080 by default)")
    p.add_argument("--client-id", default=d(None))
    p.add_argument("--force", action="store_true", default=d(False), help="take control from another controller")
    p.add_argument("--sim", nargs="?", const="", default=d(None), metavar="WORLD",
                   help="connect in simulator mode (practice without the car); optional world id")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="carctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _common(p, True)
    sub = p.add_subparsers(dest="command")
    for name, text in (("watch", "live dashboard (default)"), ("drive", "dashboard + keyboard control"),
                       ("estop", "latch the emergency stop"), ("reset", "reset the emergency stop"),
                       ("resume", "clear hold"), ("stop", "stop the motors"), ("events", "stream events until Ctrl-C")):
        _common(sub.add_parser(name, help=text), False)
    s = sub.add_parser("status", help="one snapshot")
    _common(s, False)
    s.add_argument("--json", action="store_true", help="raw welcome + one tel frame + /api/status as JSON")
    st = sub.add_parser("steer", help="set the steering angle")
    _common(st, False)
    st.add_argument("angle", type=float)
    m = sub.add_parser("move", help="timed move")
    _common(m, False)
    m.add_argument("dir", choices=["forward", "backward"])
    m.add_argument("speed", type=int)
    m.add_argument("ms", type=int)
    return p


def _fix_sim(argv: list[str]) -> list[str]:
    """`--sim` takes an optional world id; keep it from swallowing a following sub-command or option."""
    out = list(argv)
    for i, a in enumerate(out):
        if a == "--sim" and (i + 1 >= len(out) or out[i + 1] in COMMANDS or out[i + 1].startswith("-")):
            out[i] = "--sim="
    return out


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    args = build_parser().parse_args(_fix_sim(sys.argv[1:] if argv is None else argv))
    args.command = args.command or "watch"
    args.url = resolve_url(args.url, args.host)
    args.json = getattr(args, "json", False)
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command in ("watch", "drive"):
            return asyncio.run(run_dashboard(args, args.command == "drive"))
        if args.command == "events":
            return asyncio.run(run_events(args))
        return asyncio.run(run_oneshot(args))
    except KeyboardInterrupt:
        print("interrupted (stop sent)" if args.command not in ("watch", "events") else "", flush=True)
        return 0 if args.command in ("watch", "events") else 130
    except (ConnectionError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        print(f"connection failed: {e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
