"""tools.wsclient against a tiny fake car server."""
import asyncio
import json

import pytest
from aiohttp import WSMsgType, web

from tools import wsclient


def make_app(log):
    async def ws(request):
        sock = web.WebSocketResponse()
        await sock.prepare(request)
        async for msg in sock:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            log.append(m)
            if m["t"] == "hello":
                await sock.send_json({"t": "welcome", "v": 1, "role": "controller", "car_available": True,
                                      "hw": {"backend": "real"}, "config": {}, "hb_ms": 200, "server": {}})
                await sock.send_json({"t": "tel", "seq": 1, "drive": {"dir": None}, "dist": {"cm": 42.0, "state": "ok"},
                                      "safety": {}})
            elif m["t"] == "cmd":
                await sock.send_json({"t": "ack", "id": m["id"], "ok": True})
                if m["op"] == "move":
                    await sock.send_json({"t": "done", "id": m["id"], "result": "completed"})
        return sock

    app = web.Application()
    app.router.add_get("/ws", ws)
    return app


def args(*argv, url):
    return wsclient.build_parser().parse_args(["--url", url, *argv])


@pytest.mark.asyncio
async def test_drive_hold_sends_hb_then_stop(aiohttp_client):
    log = []
    c = await aiohttp_client(make_app(log))
    url = f"ws://{c.server.host}:{c.server.port}/ws"
    assert await wsclient.run(args("drive", "forward", "40", "--hold", "0.7", "--steer", "-20", url=url)) == 0
    await asyncio.sleep(0.1)
    ops = [m.get("op") for m in log if m["t"] == "cmd"]
    assert ops == ["resume", "steer", "drive", "stop"]
    assert [m for m in log if m.get("op") == "steer"][0]["angle"] == -20
    assert sum(1 for m in log if m["t"] == "hb") >= 2
    assert log[0]["t"] == "hello" and log[0]["want"] == "control" and log[0]["v"] == 1


@pytest.mark.asyncio
async def test_cancel_while_driving_sends_stop_before_closing(aiohttp_client):
    log = []
    c = await aiohttp_client(make_app(log))
    url = f"ws://{c.server.host}:{c.server.port}/ws"
    task = asyncio.create_task(wsclient.run(args("drive", "forward", "40", url=url)))
    for _ in range(100):
        if any(m.get("op") == "drive" for m in log):
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)
    assert [m.get("op") for m in log if m["t"] == "cmd"][-1] == "stop"


@pytest.mark.asyncio
async def test_move_waits_for_done(aiohttp_client):
    log = []
    c = await aiohttp_client(make_app(log))
    url = f"ws://{c.server.host}:{c.server.port}/ws"
    assert await wsclient.run(args("move", "forward", "40", "1000", url=url)) == 0
    mv = [m for m in log if m.get("op") == "move"][0]
    assert (mv["dir"], mv["speed"], mv["ms"]) == ("forward", 40, 1000)


@pytest.mark.asyncio
async def test_status_is_view_only_and_sends_no_stop(aiohttp_client):
    log = []
    c = await aiohttp_client(make_app(log))
    url = f"ws://{c.server.host}:{c.server.port}/ws"
    assert await wsclient.run(args("status", url=url)) == 0
    await asyncio.sleep(0.1)
    assert log[0]["want"] == "view" and not [m for m in log if m["t"] == "cmd"]


def test_parse_config_pairs():
    assert wsclient._parse_kv(["obstacle_stop=false", "stop_cm=30", "x=a"]) == \
        {"obstacle_stop": False, "stop_cm": 30, "x": "a"}


def test_calib_speed_entry_uses_duty_formula():
    from tools import calib
    assert calib.speed_entry(40, 1500, 30.0, 25, 100) == [55.0, 20.0]     # duty 25 + 75*0.4, 30 cm / 1.5 s


@pytest.mark.asyncio
async def test_calib_trim_applies_then_saves(aiohttp_client, monkeypatch):
    from tools import calib
    log = []
    c = await aiohttp_client(make_app(log))
    url = f"ws://{c.server.host}:{c.server.port}/ws"
    answers = iter(["3", "s", "q"])

    async def fake_ask(_prompt):
        return next(answers)
    monkeypatch.setattr(calib, "ask", fake_ask)
    a = type("A", (), {"url": url, "client_id": None, "what": "trim"})()
    assert await calib.run(a) == 0
    cal = [m for m in log if m.get("op") == "calibrate"]
    assert cal[0]["steer_trim_deg"] == 3.0 and "save" not in cal[0]
    assert cal[1]["save"] is True
