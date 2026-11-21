"""carctl: keys, drive state machine, dashboard rendering, and one-shot commands against the real app (sim hardware)."""
import asyncio
import json

import pytest

from picar_server.app import Settings, create_app
from tools import carctl
from tools.wsclient import Client


# ---- keys ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("data,keys", [
    ("\x1b[A", ["up"]), ("\x1b[B", ["down"]), ("\x1b[C", ["right"]), ("\x1b[D", ["left"]),
    ("\x1bOA", ["up"]), ("\x1b[1;2D", ["left"]), ("w", ["w"]), ("W", ["w"]), (" ", [" "]), ("\x03", ["ctrl-c"]),
    ("\x1b", ["esc"]), ("\x1b[A\x1b[A q", ["up", "up", " ", "q"]),
])
def test_decode_posix(data, keys):
    assert carctl.decode_posix(data) == keys


@pytest.mark.parametrize("first,second,key", [
    ("\xe0", "H", "up"), ("\xe0", "P", "down"), ("\xe0", "K", "left"), ("\xe0", "M", "right"),
    ("\x00", "H", "up"), ("\x00", "M", "right"), ("\xe0", "Z", "?"), ("q", None, "q"), ("Q", None, "q"),
    (" ", None, " "), ("\x03", None, "ctrl-c"),
])
def test_decode_windows(first, second, key):
    assert carctl.decode_windows(first, second) == key


@pytest.mark.parametrize("key,action", [
    ("up", "speed+"), ("w", "speed+"), ("down", "speed-"), ("s", "speed-"), ("left", "steer-"), ("a", "steer-"),
    ("right", "steer+"), ("d", "steer+"), ("c", "center"), (" ", "stop"), ("r", "resume"), ("e", "estop"),
    ("x", "reset"), ("q", "quit"), ("ctrl-c", "quit"), ("z", None), ("?", None), ("esc", None),
])
def test_key_to_action(key, action):
    assert carctl.key_to_action(key) == action


# ---- drive state machine ------------------------------------------------------------------------------------

def test_speed_stepping_and_zero_is_stop():
    s = carctl.DriveState()
    assert s.apply("speed+") == [("drive", {"dir": "forward", "speed": 10})]
    assert s.apply("speed+") == [("drive", {"dir": "forward", "speed": 20})]
    assert s.apply("speed-") == [("drive", {"dir": "forward", "speed": 10})]
    assert s.apply("speed-") == [("stop", {})]                      # 0 -> stop, never drive speed 0
    assert s.speed == 0
    assert s.apply("speed-") == [("drive", {"dir": "backward", "speed": 10})]
    assert s.apply("stop") == [("stop", {})] and s.speed == 0


def test_speed_clamps_to_server_limits():
    s = carctl.DriveState.from_welcome({"limits": {"speed_max": 100, "reverse_speed_max": 40, "steer_max": 45}})
    for _ in range(15):
        s.apply("speed+")
    assert s.speed == 100
    for _ in range(30):
        s.apply("speed-")
    assert s.speed == -40
    assert s.apply("speed-") == [("drive", {"dir": "backward", "speed": 40})]


def test_steer_stepping_clamping_and_center():
    s = carctl.DriveState()
    assert s.apply("steer+") == [("steer", {"angle": 10})]
    for _ in range(10):
        s.apply("steer+")
    assert s.steer == 45
    for _ in range(20):
        s.apply("steer-")
    assert s.steer == -45
    assert s.apply("center") == [("steer", {"angle": 0})] and s.steer == 0


def test_other_actions_and_quit():
    s = carctl.DriveState()
    s.apply("speed+")
    assert s.apply("resume") == [("resume", {})]
    assert s.apply("reset") == [("estop_reset", {})]
    assert s.apply("estop") == [("estop", {})] and s.speed == 0
    s.apply("speed+")
    assert s.apply("quit") == [("stop", {})] and s.speed == 0 and s.quit
    assert s.apply("bogus") == []


class FakeClient:
    def __init__(self, replies=None):
        self.sent, self.frames, self.replies, self.role = [], [], replies or {}, "controller"

    async def cmd(self, op, **f):
        self.sent.append((op, f))
        return self.replies.get(op, {"t": "ack", "ok": True, **({"speed": f["speed"]} if "speed" in f else {})})

    async def send(self, msg):
        self.frames.append(msg)


class FakeLink:
    generation = 1
    welcome = {"limits": {"speed_max": 100, "reverse_speed_max": 40, "steer_max": 45}}

    def __init__(self, client):
        self.client, self.errors = client, []

    def note_error(self, text):
        self.errors.append(text)


async def test_driver_sends_commands_and_quit_stops_and_releases():
    c = FakeClient()
    d = carctl.Driver(FakeLink(c))
    for a in ("resume", "speed+", "speed+", "steer-", "speed-", "speed-", "quit"):
        await d.handle(a)
    assert c.sent == [("resume", {}), ("drive", {"dir": "forward", "speed": 10}), ("drive", {"dir": "forward", "speed": 20}),
                      ("steer", {"angle": -10}), ("drive", {"dir": "forward", "speed": 10}), ("stop", {}), ("stop", {})]
    assert d.done.is_set()
    c.sent.clear()
    await d.shutdown()                                           # always: stop, then give control back
    assert c.sent == [("stop", {})] and c.frames == [{"t": "control", "action": "release"}]


async def test_driver_refused_drive_clears_target_and_records_error():
    c = FakeClient({"drive": {"t": "err", "code": "hold", "msg": "motion is on hold; send resume"}})
    link = FakeLink(c)
    d = carctl.Driver(link)
    await d.handle("speed+")
    assert d.state.speed == 0 and d.ack_speed is None
    assert link.errors == ["drive: hold: motion is on hold; send resume"]


async def test_driver_forgets_target_after_reconnect_and_safety_events():
    c = FakeClient()
    link = FakeLink(c)
    d = carctl.Driver(link)
    await d.handle("speed+")
    assert d.state.speed == 10 and d.ack_speed == 10
    d.on_event("obstacle_stop")
    assert d.state.speed == 0
    await d.handle("speed+")
    link.generation = 2                                          # reconnect: the car is stopped and on hold again
    d.sync()
    assert d.state.speed == 0


# ---- dashboard rendering ------------------------------------------------------------------------------------

WELCOME = {"t": "welcome", "role": "controller", "mode": "car", "hw": {"backend": "real"},
           "server": {"version": "1.4.0", "sha": "abc1234"}, "limits": {"speed_max": 100, "reverse_speed_max": 40}}
TEL = {"t": "tel", "seq": 812, "ts": 40.62, "mode": "car",
       "drive": {"dir": "forward", "speed": 40, "duty": [1720, 1720], "moving": True}, "steer": -12,
       "dist": {"cm": 42.3, "state": "ok"}, "obstacle": False,
       "line": {"ok": True, "raw": [900, 120, 80, 130, 910], "on": [True, False, False, False, True], "pos": 0},
       "light": {"ok": True, "raw": [118, 96, 131], "bright": [41, 63, 30], "max": "middle"},
       "pose": {"x": 12.4, "y": 31.0, "dir": 85.5, "src": "dr"},
       "safety": {"estop": False, "hold": False, "calibrated": False, "obstacle_stop": True, "stop_cm": 20,
                  "margin_cm": 24.5, "speed_limit": 100, "blocked": False, "hw_fault": None},
       "power": {"source": "pi", "low": False, "low_since_boot": True, "low_events": 0, "throttled": False,
                 "on_s": 1260, "motor_s": 84.5, "cpu_temp_c": 52.1, "cpu_load": 0.31},
       "ctl": {"role": "controller", "viewers": 1}, "move": {"id": 17, "remaining_ms": 640}}
API = {"version": "1.4.0", "sha": "abc1234", "car": {"available": True, "backend": "real"},
       "clients": {"car_controller": True, "car_viewers": 1, "sim_sessions": 0}}
EVENTS = [(1_700_000_000 + i, k, d) for i, (k, d) in enumerate([
    ("hold", "reason=control"), ("resumed", ""), ("obstacle_stop", "cm=18.2"), ("estop", "by=ws"),
    ("control_changed", "controller_present=True")])]


def view(**kw):
    base = dict(url="ws://picar.local:8080/ws", welcome=WELCOME, tel=TEL, tel_age=0.1, api=API, events=EVENTS,
                role="controller", target={"speed": 40, "steer": -10, "ack_speed": 35},
                help=carctl.DRIVE_HELP, last_error=(1_700_000_010, "drive: hold: motion is on hold"), title="drive")
    base.update(kw)
    return carctl.View(**base)


@pytest.mark.parametrize("width,height", [(120, 50), (100, 40), (80, 24), (60, 20)])
def test_render_full_tel_frame(width, height):
    out = carctl.view_text(view(), width, height)
    assert "ws://picar.local:8080/ws" in out and "abc1234" in out and "controller" in out
    for word in (("E-STOP", "42.3", "1720", "85.5") if width >= 100 else ("42.3",) + (("1720",) if width >= 80 else ())):
        assert word in out
    assert "obstacle_stop" in out or height < 30
    assert all(len(line) <= width for line in out.splitlines())


def test_render_full_layout_has_all_panels():
    out = carctl.view_text(view(), 120, 50)
    for title in ("Safety", "Drive", "Distance", "Line follower", "Light", "Pose", "Power", "Events"):
        assert title in out
    for word in ("Brightest", "dead reckoning", "On time", "0:21:00", "obstacle_stop", "Target"):
        assert word in out


def test_render_tel_with_missing_sections():
    tel = {"t": "tel", "seq": 1, "mode": "car", "drive": None, "steer": None, "dist": None, "line": None, "light": None,
           "pose": None, "safety": None, "power": None, "ctl": None, "move": None}
    for t in (tel, {}, None, {"t": "tel", "line": {"ok": False, "raw": None, "on": None, "pos": None},
                              "light": {"ok": False}, "power": {"source": "none", "cpu_temp_c": None, "cpu_load": None},
                              "dist": {"cm": None, "state": "fault"}, "safety": {"hw_fault": "i2c"}}):
        for w, h in ((120, 50), (80, 24)):
            out = carctl.view_text(view(tel=t, api=None, events=[], last_error=None, target=None), w, h)
            assert "carctl" in out
    out = carctl.view_text(view(tel=None, tel_age=None, conn="reconnecting", conn_error="refused", reconnects=3), 80, 24)
    assert "reconnecting" in out and "reconnects 3" in out
    off = carctl.view_text(view(tel={"t": "tel", "line": {"ok": False}, "light": {"ok": False},
                                     "power": {"source": "none"}}), 120, 50)
    assert "line follower offline" in off and "light sensors offline" in off and "no power data" in off


def test_render_sim_mode_and_alerts():
    tel = dict(TEL, mode="sim", pose={"x": 0, "y": 0, "dir": 90, "src": "sim"},
               power={"source": "sim", "low": True, "low_since_boot": True, "low_events": 2, "throttled": True,
                      "on_s": 5, "motor_s": 1.0, "cpu_temp_c": None, "cpu_load": None},
               safety=dict(TEL["safety"], estop=True, hold=True, blocked=True, hw_fault="i2c write"))
    out = carctl.view_text(view(welcome=dict(WELCOME, mode="sim", hw={"backend": "sim"}), tel=tel, hold_reason="control"), 120, 50)
    assert "simulator" in out and "LATCHED" in out and "HOLD" in out and "BLOCKED" in out and "i2c write" in out
    assert "sim" in carctl.view_text(view(tel=tel), 80, 24)


def test_help_line_follows_state():
    v = view(tel=dict(TEL, safety=dict(TEL["safety"], hold=True)))
    assert "press R" in carctl._help_for(v, True)
    assert "X resets" in carctl._help_for(view(tel=dict(TEL, safety=dict(TEL["safety"], estop=True))), True)
    assert "VIEW ONLY" in carctl._help_for(view(role="viewer"), True)
    assert "viewing only" in carctl._help_for(view(), False)


def test_url_helpers_and_argv():
    assert carctl.resolve_url(None, "car.local") == "ws://car.local:8080/ws"
    assert carctl.resolve_url(None, "1.2.3.4:9000") == "ws://1.2.3.4:9000/ws"
    assert carctl.resolve_url("ws://x:1/ws", None) == "ws://x:1/ws"
    assert carctl.resolve_url(None, None) == "ws://picar.local:8080/ws"
    assert carctl.http_base("ws://h:8080/ws") == "http://h:8080"
    assert carctl.http_base("wss://h/ws") == "https://h"
    a = carctl.parse_args(["--sim", "watch"])
    assert (a.command, a.sim) == ("watch", "")
    a = carctl.parse_args(["move", "forward", "40", "500", "--sim", "maze"])
    assert (a.command, a.sim, a.ms) == ("move", "maze", 500)
    a = carctl.parse_args(["drive", "--force", "--host", "car"])
    assert (a.command, a.force, a.url, a.sim) == ("drive", True, "ws://car:8080/ws", None)
    a = carctl.parse_args(["steer", "-20"])
    assert a.angle == -20
    assert carctl.parse_args([]).command == "watch"


# ---- end to end against the real app (sim hardware) ---------------------------------------------------------

@pytest.fixture
async def car(aiohttp_client, tmp_path):
    www = tmp_path / "www"
    www.mkdir()
    s = Settings(www=www, examples=tmp_path / "examples", source=tmp_path / "source", data=tmp_path / "data",
                 config=tmp_path / "config", legacy_calibration=tmp_path / "no-legacy.json", hardware="sim")
    client = await aiohttp_client(create_app(s))
    url = f"ws://127.0.0.1:{client.server.port}/ws"
    async with Client(url, want="view", client_id="carctl-test-warm", quiet=True) as c:   # first sensor sample
        for _ in range(100):
            if ((await c.wait_tel(1)) or {}).get("dist", {}).get("state") in ("ok", "far"):
                break
            await asyncio.sleep(0.05)
    return url


async def ctl(url, *argv):
    return await carctl.run_oneshot(carctl.parse_args(["--url", url, *argv]))


async def test_status_json_has_welcome_tel_and_api(car, capsys):
    assert await ctl(car, "status", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["welcome"]["t"] == "welcome" and out["welcome"]["server"]["version"]
    assert out["tel"]["t"] == "tel" and "safety" in out["tel"] and "dist" in out["tel"]
    assert out["api_status"]["car"]["available"] is True and "clients" in out["api_status"]


async def test_status_human_readable(car, capsys):
    assert await ctl(car, "status") == 0
    out = capsys.readouterr().out
    assert "E-STOP" in out or "SAFETY" in out
    assert "ws://127.0.0.1" in out


async def test_estop_then_reset(car, capsys):
    assert await ctl(car, "estop") == 0
    assert "estop: ok" in capsys.readouterr().out
    assert await ctl(car, "status", "--json") == 0
    assert json.loads(capsys.readouterr().out)["tel"]["safety"]["estop"] is True
    assert await ctl(car, "reset") == 0
    capsys.readouterr()
    assert await ctl(car, "status", "--json") == 0
    assert json.loads(capsys.readouterr().out)["tel"]["safety"]["estop"] is False


async def test_resume_stop_steer_ack(car, capsys):
    assert await ctl(car, "resume") == 0
    assert await ctl(car, "stop") == 0
    assert await ctl(car, "steer", "-20") == 0
    assert '"angle": -20' in capsys.readouterr().out


async def test_move_returns_done(car, capsys):
    rc = await ctl(car, "move", "forward", "30", "300")
    out = capsys.readouterr().out
    assert rc == 0 and "done: completed" in out


async def test_command_error_is_exit_1(car, capsys):
    assert await ctl(car, "estop") == 0
    assert await ctl(car, "move", "forward", "30", "300") == 1          # e-stopped: refused
    assert "ERR estop" in capsys.readouterr().out


async def test_needs_control_unless_forced(car, capsys):
    async with Client(car, want="control", client_id="somebody-else-1", quiet=True) as other:
        assert other.role == "controller"
        assert await ctl(car, "stop") == 1
        assert "--force" in capsys.readouterr().out
        assert await ctl(car, "stop", "--force") == 0


async def test_sim_mode_one_shots(car, capsys):
    assert await ctl(car, "--sim", "status", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["welcome"]["mode"] == "sim" and out["tel"]["mode"] == "sim"
    assert await ctl(car, "move", "forward", "30", "200", "--sim") == 0


async def test_link_collects_telemetry_events_and_renders(car):
    link = carctl.Link(car, want="view", client_id="carctl-test-view", api_poll=0.1)
    runner = asyncio.create_task(link.run())
    try:
        await asyncio.wait_for(link.first.wait(), 5)
        assert link.conn == "connected" and link.client.role == "viewer"
        await link.client.wait_tel()
        async with Client(car, want="view", client_id="carctl-test-estop", quiet=True) as c:
            await c.cmd("estop")
        for _ in range(50):
            if any(k == "estop" for _, k, _ in link.events) and link.api and ((link.client.tel or {}).get("safety") or {}).get("estop"):
                break
            await asyncio.sleep(0.05)
        v = link.snapshot()
        assert any(k == "estop" for _, k, _ in v.events) and v.api["car"]["available"]
        out = carctl.view_text(v, 120, 50)
        assert "estop" in out and "connected" in out and "LATCHED" in out
    finally:
        await link.stop()
        await asyncio.wait_for(runner, 5)


async def test_link_reconnects_after_drop(car):
    link = carctl.Link(car, want="view", client_id="carctl-test-recon", api_poll=0)
    runner = asyncio.create_task(link.run())
    try:
        await asyncio.wait_for(link.first.wait(), 5)
        first = link.client
        await first._ws.close()                                  # simulate a dropped socket
        for _ in range(100):
            if link.reconnects >= 1 and link.conn == "connected":
                break
            await asyncio.sleep(0.05)
        assert link.reconnects == 1 and link.client is not first and link.generation == 2
    finally:
        await link.stop()
        await asyncio.wait_for(runner, 5)
