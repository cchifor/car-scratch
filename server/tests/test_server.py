"""WebSocket protocol, arbitration, REST and static serving against a real aiohttp test server."""
import asyncio
import gzip
import json
import time

import pytest
from aiohttp import WSMsgType

from picar_server.app import Settings, create_app

from conftest import make_sb3

CID = "client-aaaa-0001"


def settings(tmp_path, **kw):
    www = tmp_path / "www"
    www.mkdir(exist_ok=True)
    kw.setdefault("hardware", "sim")
    return Settings(www=www, examples=tmp_path / "examples", source=tmp_path / "source",
                    data=tmp_path / "data", config=tmp_path / "config",
                    legacy_calibration=tmp_path / "no-legacy.json", **kw)


@pytest.fixture
async def srv(aiohttp_client, tmp_path):
    return await aiohttp_client(create_app(settings(tmp_path)))


class WS:
    """Tiny JSON client around an aiohttp websocket."""

    def __init__(self, ws):
        self.ws, self.seq, self.mark = ws, 0, 0
        self.log = []

    async def send(self, **m):
        await self.ws.send_str(json.dumps(m))

    async def cmd(self, op, **f):
        self.seq += 1
        self.mark = len(self.log)
        await self.send(t="cmd", id=self.seq, op=op, **f)
        return self.seq

    async def recv(self, timeout=2.0):
        m = await asyncio.wait_for(self.ws.receive(), timeout)
        assert m.type == WSMsgType.TEXT, m
        d = json.loads(m.data)
        self.log.append(d)
        return d

    async def until(self, pred, timeout=3.0):
        end = time.monotonic() + timeout
        while True:
            d = await self.recv(max(0.05, end - time.monotonic()))
            if pred(d):
                return d

    async def reply(self, cid):
        return await self.until(lambda d: d.get("id") == cid and d["t"] in ("ack", "err"))

    async def ok(self, op, **f):
        r = await self.reply(await self.cmd(op, **f))
        assert r["t"] == "ack", r
        return r

    async def err(self, op, **f):
        r = await self.reply(await self.cmd(op, **f))
        assert r["t"] == "err", r
        return r["code"]

    async def event(self, kind, timeout=3.0, **match):
        """An event seen since the last cmd (events are sent before the ack, so look back first)."""
        def hit(d):
            return d["t"] == "event" and d["kind"] == kind and all(d.get(k) == v for k, v in match.items())
        for d in self.log[self.mark:]:
            if hit(d):
                return d
        return await self.until(hit, timeout)

    async def _unused_event(self, kind, timeout=3.0):
        return await self.until(lambda d: d["t"] == "event" and d["kind"] == kind, timeout)

    async def close_code(self, timeout=3.0):
        while True:
            m = await asyncio.wait_for(self.ws.receive(), timeout)
            if m.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING, WSMsgType.ERROR):
                return self.ws.close_code


async def connect(srv, cid=CID, want="control", mode="car", world=None, headers=None, v=1):
    ws = WS(await srv.ws_connect("/ws", headers=headers))
    hello = dict(t="hello", v=v, client_id=cid, want=want, mode=mode)
    if world:
        hello["world"] = world
    await ws.send(**hello)
    return ws


async def hello(srv, **kw):
    ws = await connect(srv, **kw)
    w = await ws.recv()
    assert w["t"] == "welcome", w
    return ws, w


async def go(ws):
    """Resume the shared car (it starts on hold) and wait for the first valid samples."""
    await ws.ok("resume")
    await asyncio.sleep(0.2)


# ---- handshake / welcome ---------------------------------------------------------------------------------------------

async def test_welcome_for_car_mode_and_status(srv):
    ws, w = await hello(srv)
    assert w["v"] == 1 and w["role"] == "controller" and w["mode"] == "car" and w["car_available"] is True
    assert w["hw"]["backend"] == "sim" and set(w["hw"]["capabilities"]) == {"ultrasonic", "light", "line"}
    assert w["safety"] == {"estop": False, "hold": True}           # the shared car starts on hold
    assert w["limits"]["reverse_speed_max"] == 40 and w["config"] == {"obstacle_stop": True, "stop_cm": 20, "speed_limit": 100}
    assert (w["hb_ms"], w["wd_ms"], w["tel_hz"]) == (200, 750, 20)
    assert w["geometry"]["body"]["length"] == 26 and w["calibrated"] is False
    assert {x["id"] for x in w["worlds"]} == {"arena", "corridor", "oval", "lamp", "maze"}
    assert w["server"]["sha"] and w["server"]["version"]
    st = await (await srv.get("/api/status")).json()
    assert st["car"]["available"] and st["car"]["backend"] == "sim" and st["clients"]["car_controller"] is True
    assert st["sha"] == w["server"]["sha"]


async def test_sim_mode_gets_private_world_and_own_controller(srv):
    a, wa = await hello(srv, mode="sim", world="corridor")
    assert wa["mode"] == "sim" and wa["role"] == "controller" and wa["hw"]["backend"] == "sim"
    world = await a.until(lambda d: d["t"] == "world")
    assert world["id"] == "corridor" and world["walls"] and world["start"]["x"] == 48
    b, _ = await hello(srv, cid="client-bbbb-0002", mode="sim", world="oval")
    st = await (await srv.get("/api/status")).json()
    assert st["clients"]["sim_sessions"] == 2 and st["clients"]["car_controller"] is False
    await asyncio.sleep(0.2)
    await a.ok("drive", dir="forward", speed=30)                    # a private sim does not start on hold
    t = await a.until(lambda d: d["t"] == "tel")
    assert t["mode"] == "sim" and t["pose"]["src"] == "sim"
    await a.ok("world", world="arena")
    assert (await a.until(lambda d: d["t"] == "world"))["id"] == "arena"
    assert await a.err("world", world="nope") == "bad_request"
    await b.ok("world", world="maze")


async def test_world_cmd_is_sim_only(srv):
    ws, _ = await hello(srv)
    assert await ws.err("world", world="arena") == "sim_only"


async def test_sim_busy_after_four_sessions(srv):
    socks = [(await hello(srv, cid=f"client-sim-000{i}", mode="sim"))[0] for i in range(4)]
    ws = await connect(srv, cid="client-sim-0005", mode="sim")
    err = await ws.until(lambda d: d["t"] == "err")
    assert err["code"] == "sim_busy"
    w = await ws.until(lambda d: d["t"] == "welcome")
    assert w["mode"] == "car"
    await socks[0].ws.close()
    await asyncio.sleep(0.1)
    await ws.send(t="mode", mode="sim")
    assert (await ws.until(lambda d: d["t"] == "welcome"))["mode"] == "sim"


# ---- origin / host / framing --------------------------------------------------------------------------------------------

async def test_origin_and_host_checks(srv):
    ok = await srv.ws_connect("/ws", headers={"Origin": f"http://127.0.0.1:{srv.port}"})
    await ok.close()
    for headers in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {"Host": "evil.example"}):
        r = await srv.get("/ws", headers=headers)
        assert r.status == 403, headers
    assert (await srv.get("/ws")).status == 400                      # no Origin: allowed, but not an upgrade


async def test_allow_origin_for_a_dev_proxy(aiohttp_client, tmp_path):
    c = await aiohttp_client(create_app(settings(tmp_path, allow_origins=["http://localhost:3000"])))
    ws = await c.ws_connect("/ws", headers={"Origin": "http://localhost:3000"})
    await ws.close()


async def test_first_frame_must_be_a_valid_hello(srv):
    ws = WS(await srv.ws_connect("/ws"))
    await ws.send(t="hb")
    assert await ws.close_code() == 4400
    ws = WS(await srv.ws_connect("/ws"))
    await ws.ws.send_str("not json")
    assert await ws.close_code() == 4400
    ws = WS(await srv.ws_connect("/ws"))
    await ws.send(t="hello", v=1, client_id="x", want="control", mode="car")
    assert await ws.close_code() == 4400


async def test_protocol_version_mismatch_closes_4409(srv):
    ws = await connect(srv, v=2)
    assert await ws.close_code() == 4409


async def test_malformed_frames_get_err_and_keep_the_socket(srv):
    ws, _ = await hello(srv)
    await ws.ws.send_str("garbage")
    assert (await ws.until(lambda d: d["t"] == "err"))["code"] == "bad_request"
    await ws.send(t="nope")
    assert (await ws.until(lambda d: d["t"] == "err"))["code"] == "bad_request"
    assert await ws.err("drive", dir="sideways", speed=5) == "bad_request"
    await ws.ws.send_bytes(b"\x00\x01")
    assert (await ws.until(lambda d: d["t"] == "err"))["code"] == "bad_request"
    await ws.send(t="hello", v=1, client_id=CID, want="control", mode="car")
    assert (await ws.until(lambda d: d["t"] == "err"))["code"] == "bad_request"
    await ws.ok("stop")


async def test_oversize_frame_closes_1009(srv):
    ws, _ = await hello(srv)
    await ws.ws.send_str(json.dumps({"t": "hb", "pad": "x" * 5000}))
    assert await ws.close_code() == 1009


async def test_rate_limit_drops_excess_but_never_stop_or_estop(srv):
    ws, _ = await hello(srv)
    await ws.ws.send_str("\n".join([]) or '{"t":"hb"}')
    for _ in range(300):
        await ws.ws.send_str('{"t":"hb"}')
    await ws.send(t="cmd", id=900, op="stop")
    await ws.send(t="cmd", id=901, op="estop")
    got = {}
    while 900 not in got or 901 not in got:
        d = await ws.recv()
        if d["t"] == "ack":
            got[d["id"]] = d
    assert any(d.get("code") == "rate_limited" for d in ws.log if d["t"] == "err")


async def test_idle_socket_closed_4408(aiohttp_client, tmp_path):
    c = await aiohttp_client(create_app(settings(tmp_path, idle_s=0.4)))
    ws, _ = await hello(c)
    t0 = time.monotonic()
    assert await ws.close_code() == 4408 and time.monotonic() - t0 < 1.5


async def test_rejected_frames_do_not_keep_a_socket_alive(aiohttp_client, tmp_path):
    c = await aiohttp_client(create_app(settings(tmp_path, idle_s=0.5)))
    ws, _ = await hello(c)
    for _ in range(12):
        await ws.ws.send_str("garbage")
        await asyncio.sleep(0.07)
    assert await ws.close_code() == 4408


# ---- telemetry -----------------------------------------------------------------------------------------------------------

async def test_telemetry_streams_at_about_20hz(srv):
    ws, _ = await hello(srv)
    await asyncio.sleep(0.1)
    n, t0 = 0, time.monotonic()
    while time.monotonic() - t0 < 0.5:
        d = await ws.recv(0.5)
        n += d["t"] == "tel"
    assert n >= 8
    tel = next(d for d in reversed(ws.log) if d["t"] == "tel")
    assert tel["mode"] == "car" and tel["ctl"] == {"role": "controller", "viewers": 0}
    assert {"drive", "steer", "dist", "obstacle", "line", "light", "pose", "safety", "move", "seq", "ts"} <= set(tel)


async def test_session_telemetry_slot_is_latest_only():
    from types import SimpleNamespace
    from picar_server.session import Session
    hub = SimpleNamespace(clock=time.monotonic, new_session_id=lambda: "s0")
    s = Session(hub, None)
    for i in range(1000):
        s.post_tel(str(i))
    assert s._tel == "999" and len(s._out) == 0


# ---- arbitration ---------------------------------------------------------------------------------------------------------

async def test_viewer_cannot_command_but_can_estop(srv):
    ctl, _ = await hello(srv)
    view, w = await hello(srv, cid="client-bbbb-0002", want="control")
    assert w["role"] == "viewer"                                    # control is taken
    assert await view.err("drive", dir="forward", speed=30) == "not_controller"
    assert await view.err("stop") == "not_controller"
    assert await view.err("estop_reset") == "not_controller"
    await view.ok("estop")
    ev = await ctl.event("estop")
    assert ev["by"] == "viewer"
    assert await ctl.err("drive", dir="forward", speed=30) == "estop"
    await ctl.ok("estop_reset")
    await ctl.event("estop_reset")


async def test_estop_from_sim_mode_and_rest_latches_the_shared_car(srv):
    ctl, _ = await hello(srv)
    sim, _ = await hello(srv, cid="client-sim-0001", mode="sim")
    await sim.ok("estop")
    assert await ctl.event("estop", by="viewer")
    await ctl.ok("estop_reset")
    r = await srv.post("/api/estop")
    assert r.status == 200
    assert await ctl.event("estop", by="api")
    assert (await srv.post("/api/estop", headers={"Origin": "http://evil.example"})).status == 403
    tel = await ctl.until(lambda d: d["t"] == "tel" and d["safety"]["estop"])
    assert tel["safety"]["estop"] is True


async def test_control_take_release_and_force(srv):
    a, _ = await hello(srv)
    b, wb = await hello(srv, cid="client-bbbb-0002")
    assert wb["role"] == "viewer"
    await go(a)
    await a.ok("drive", dir="backward", speed=30)
    await b.send(t="control", action="take")
    assert (await b.until(lambda d: d["t"] == "role"))["role"] == "viewer"      # refused: controller present
    await b.send(t="control", action="take", force=True)
    assert (await b.until(lambda d: d["t"] == "role" and d["role"] == "controller"))
    assert (await a.until(lambda d: d["t"] == "role" and d["role"] == "viewer"))["controller_present"] is True
    tel = await b.until(lambda d: d["t"] == "tel")
    assert tel["drive"]["moving"] is False                          # the car was stopped first
    assert await a.err("stop") == "not_controller"
    await b.send(t="control", action="release")
    assert (await b.until(lambda d: d["t"] == "role" and d["role"] == "viewer"))["controller_present"] is False
    await a.send(t="control", action="take")
    await a.until(lambda d: d["t"] == "role" and d["role"] == "controller")


async def test_hand_over_resets_config_and_clears_hold_for_a_new_controller(srv):
    a, _ = await hello(srv)
    await go(a)
    await a.ok("config", stop_cm=50, speed_limit=40)
    b, _ = await hello(srv, cid="client-bbbb-0002")
    await b.send(t="control", action="take", force=True)
    await b.until(lambda d: d["t"] == "role" and d["role"] == "controller")
    tel = await b.until(lambda d: d["t"] == "tel" and d["ctl"]["role"] == "controller")
    assert tel["safety"]["stop_cm"] == 20 and tel["safety"]["speed_limit"] == 100 and not tel["safety"]["hold"]


async def test_disconnect_stops_latches_hold_and_lease_holder_returns_on_hold(srv):
    a, _ = await hello(srv)
    await go(a)
    await a.ok("drive", dir="backward", speed=30)
    viewer, _ = await hello(srv, cid="client-bbbb-0002", want="view")
    await a.ws.close()
    ev = await viewer.event("hold")
    assert ev["reason"] == "disconnect"
    tel = await viewer.until(lambda d: d["t"] == "tel" and not d["drive"]["moving"] and d["safety"]["hold"])
    assert tel["ctl"]["role"] == "viewer"
    # another client cannot take control during the lease, the same client_id can (still on hold)
    other, wo = await hello(srv, cid="client-cccc-0003")
    assert wo["role"] == "viewer"
    await other.send(t="control", action="take")
    assert (await other.until(lambda d: d["t"] == "role"))["role"] == "viewer"
    back, wb = await hello(srv)
    assert wb["role"] == "controller" and wb["safety"]["hold"] is True
    assert await back.err("drive", dir="forward", speed=30) == "hold"
    await back.ok("resume")


async def test_lease_expires(aiohttp_client, tmp_path):
    c = await aiohttp_client(create_app(settings(tmp_path, lease_s=0.3)))
    a, _ = await hello(c)
    await a.ws.close()
    await asyncio.sleep(0.5)
    b, wb = await hello(c, cid="client-bbbb-0002")
    assert wb["role"] == "controller"


async def test_duplicate_live_client_id_becomes_viewer(srv):
    a, _ = await hello(srv)
    b, wb = await hello(srv)
    assert wb["role"] == "viewer"


async def test_mode_switch_stops_car_releases_control_and_answers_with_welcome(srv):
    a, _ = await hello(srv)
    await go(a)
    await a.ok("drive", dir="backward", speed=30)
    await a.send(t="mode", mode="sim", world="lamp")
    w = await a.until(lambda d: d["t"] == "welcome")
    assert w["mode"] == "sim" and w["role"] == "controller" and w["hw"]["backend"] == "sim"
    assert (await a.until(lambda d: d["t"] == "world"))["id"] == "lamp"
    st = await (await srv.get("/api/status")).json()
    assert st["clients"]["car_controller"] is False and st["clients"]["sim_sessions"] == 1
    await a.send(t="mode", mode="car")
    w = await a.until(lambda d: d["t"] == "welcome" and d["mode"] == "car")
    assert w["role"] == "controller" and w["safety"]["hold"] is False      # released: a new controller clears hold
    tel = await a.until(lambda d: d["t"] == "tel" and d["mode"] == "car")
    assert tel["drive"]["moving"] is False
    st = await (await srv.get("/api/status")).json()
    assert st["clients"]["sim_sessions"] == 0


# ---- safety over the wire ---------------------------------------------------------------------------------------------------

async def test_watchdog_over_the_wire_stops_and_holds(srv):
    a, _ = await hello(srv, mode="sim", world="arena")
    await asyncio.sleep(0.2)
    await a.ok("drive", dir="forward", speed=30)
    ev = await a.event("watchdog_stop", 3.0)                         # silent for > 750 ms
    hold = await a.event("hold")
    assert hold["reason"] == "watchdog"
    assert await a.err("drive", dir="forward", speed=30) == "hold"
    await a.ok("resume")
    await a.event("resumed")
    await a.ok("drive", dir="forward", speed=30)


async def test_heartbeats_keep_the_controller_alive(srv):
    a, _ = await hello(srv, mode="sim", world="arena")
    await asyncio.sleep(0.2)
    await a.ok("drive", dir="forward", speed=30)
    for _ in range(8):
        await a.send(t="hb")
        await asyncio.sleep(0.2)
    assert not any(d["t"] == "event" and d["kind"] == "watchdog_stop" for d in a.log)


async def test_corridor_move_ends_with_obstacle_over_the_wire(srv):
    a, _ = await hello(srv, mode="sim", world="corridor")
    await asyncio.sleep(0.2)
    r = await a.ok("move", dir="forward", speed=100, ms=30000)
    assert r["speed"] == 100 and r["ms"] == 30000
    mid = r["id"]
    async def beat():
        while True:
            await a.send(t="hb")
            await asyncio.sleep(0.2)
    hb = asyncio.ensure_future(beat())
    try:
        done = await a.until(lambda d: d["t"] == "done", timeout=6.0)
    finally:
        hb.cancel()
    assert done == {"t": "done", "id": mid, "result": "obstacle"}
    ev = await a.event("obstacle_stop") if not any(d.get("kind") == "obstacle_stop" for d in a.log) else None
    assert any(d.get("kind") == "obstacle_stop" for d in a.log)


# ---- hardware selection ----------------------------------------------------------------------------------------------------

async def test_hardware_none_means_car_unavailable_but_editor_and_sim_work(aiohttp_client, tmp_path):
    c = await aiohttp_client(create_app(settings(tmp_path, hardware="none")))
    ws, w = await hello(c)
    assert w["car_available"] is False and w["hw"]["backend"] == "none" and w["hw"]["error"]
    assert await ws.err("drive", dir="forward", speed=10) == "hw_unavailable"
    await ws.ok("stop")
    st = await (await c.get("/api/status")).json()
    assert st["car"] == {"available": False, "backend": "none", "error": st["car"]["error"],
                         "capabilities": {"ultrasonic": False, "light": False, "line": False}}
    s, ws2 = await hello(c, cid="client-bbbb-0002", mode="sim")
    assert ws2["mode"] == "sim"


async def test_auto_falls_back_to_unavailable_without_hardware(aiohttp_client, tmp_path):
    c = await aiohttp_client(create_app(settings(tmp_path, hardware="auto")))
    st = await (await c.get("/api/status")).json()
    assert st["car"]["backend"] in ("none", "real")
    if st["car"]["backend"] == "none":
        assert st["car"]["available"] is False and st["car"]["error"]


# ---- static ----------------------------------------------------------------------------------------------------------------------

@pytest.fixture
async def site(aiohttp_client, tmp_path):
    www = tmp_path / "www"
    www.mkdir()
    (www / "index.html").write_text("<h1>editor</h1>")
    (www / "app.js").write_text("plain")
    (www / "app.js.gz").write_bytes(gzip.compress(b"zipped"))
    (www / "only.css.gz").write_bytes(gzip.compress(b"body{}"))
    (www / "a.wasm").write_bytes(b"\0asm")
    (www / "m.webmanifest").write_text("{}")
    (www / "p.sb3").write_bytes(b"x")
    (www / "sub").mkdir()
    (www / "sub" / "index.html").write_text("sub")
    (tmp_path / "secret.txt").write_text("secret")
    (tmp_path / "www-evil").mkdir()
    (tmp_path / "www-evil" / "x.txt").write_text("evil")
    ex = tmp_path / "examples"
    ex.mkdir()
    (ex / "index.json").write_text("[]")
    (ex / "01.sb3").write_bytes(make_sb3())
    src = tmp_path / "source"
    src.mkdir()
    (src / "car-scratch-abc.tar.gz").write_bytes(b"tar")
    return await aiohttp_client(create_app(settings(tmp_path)))


async def test_static_index_and_gzip_negotiation(site):
    r = await site.get("/")
    assert r.status == 200 and "editor" in await r.text() and r.headers["Content-Type"].startswith("text/html")
    r = await site.get("/app.js", headers={"Accept-Encoding": "gzip"}, auto_decompress=False)
    assert r.headers["Content-Encoding"] == "gzip" and gzip.decompress(await r.read()) == b"zipped"
    assert "javascript" in r.headers["Content-Type"] and "Accept-Encoding" in r.headers["Vary"]
    r = await site.get("/app.js", headers={"Accept-Encoding": "identity"})
    assert await r.text() == "plain" and "Content-Encoding" not in r.headers
    r = await site.get("/only.css", headers={"Accept-Encoding": "gzip"}, auto_decompress=False)
    assert gzip.decompress(await r.read()) == b"body{}" and r.headers["Content-Type"].startswith("text/css")
    r = await site.get("/only.css", headers={"Accept-Encoding": "identity"})
    assert await r.read() == b"body{}"
    assert (await site.get("/sub/")).status == 200


async def test_static_mime_types(site):
    assert (await site.get("/a.wasm")).headers["Content-Type"] == "application/wasm"
    assert (await site.get("/m.webmanifest")).headers["Content-Type"] == "application/manifest+json"
    assert (await site.get("/p.sb3")).headers["Content-Type"] == "application/x.scratch.sb3"


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/sub/../../secret.txt",
                                  "/%2e%2e%2fwww-evil/x.txt", "/nope", "/app.js%00.png"])
async def test_static_path_traversal_is_confined(site, path):
    r = await site.get(path, skip_auto_headers=())
    body = await r.text()
    assert "secret" not in body and "evil" not in body and "root:" not in body
    assert r.status in (400, 404)


async def test_examples_and_source(site):
    assert await (await site.get("/examples/index.json")).json() == []
    r = await site.get("/examples/01.sb3")
    assert r.status == 200 and (await r.read())[:2] == b"PK"
    assert (await site.get("/examples/../secret.txt")).status == 404
    page = await (await site.get("/source/")).text()
    assert "car-scratch-abc.tar.gz" in page
    assert await (await site.get("/source/car-scratch-abc.tar.gz")).read() == b"tar"
    assert (await site.get("/source/%2e%2e/secret.txt")).status == 404


async def test_server_starts_without_a_built_editor(aiohttp_client, tmp_path):
    s = settings(tmp_path)
    s.www = tmp_path / "missing"
    c = await aiohttp_client(create_app(s))
    assert (await c.get("/")).status == 404
    assert (await c.get("/api/status")).status == 200


# ---- projects ---------------------------------------------------------------------------------------------------------------------

async def test_projects_crud_atomic_overwrite_and_bak(srv, tmp_path):
    assert await (await srv.get("/api/projects")).json() == []
    sb3 = make_sb3()
    r = await srv.put("/api/projects/My Project-1.sb3", data=sb3)
    assert r.status == 200 and (await r.json())["size"] == len(sb3)
    listing = await (await srv.get("/api/projects")).json()
    assert [p["name"] for p in listing] == ["My Project-1"] and listing[0]["size"] == len(sb3)
    r = await srv.get("/api/projects/My%20Project-1.sb3")
    assert r.status == 200 and await r.read() == sb3
    sb3b = make_sb3({"extra.txt": "v2"})
    assert (await srv.put("/api/projects/My Project-1.sb3", data=sb3b)).status == 200
    d = tmp_path / "data" / "projects"
    assert (d / "My Project-1.sb3.bak").read_bytes() == sb3 and (d / "My Project-1.sb3").read_bytes() == sb3b
    assert not list(d.glob("*.tmp"))
    assert [p["name"] for p in await (await srv.get("/api/projects")).json()] == ["My Project-1"]   # .bak not listed
    assert (await srv.delete("/api/projects/My Project-1.sb3")).status == 200
    assert (await srv.delete("/api/projects/My Project-1.sb3")).status == 404
    assert (await srv.get("/api/projects/My Project-1.sb3")).status == 404


@pytest.mark.parametrize("name", ["..%2fx", "a%2fb", "x.y", "", "a" * 65, "bad%00", "a*b", "a%5Cb"])
async def test_projects_reject_bad_names(srv, name):
    r = await srv.put(f"/api/projects/{name}.sb3", data=make_sb3())
    assert r.status in (400, 404)


async def test_projects_reject_non_zip_and_missing_project_json_and_wrong_suffix(srv):
    assert (await srv.put("/api/projects/a.sb3", data=b"not a zip")).status == 400
    assert (await srv.put("/api/projects/a.sb3", data=make_sb3(project=False))).status == 400
    assert (await srv.put("/api/projects/a.txt", data=make_sb3())).status == 404
    assert (await srv.get("/api/projects/..%2fa.sb3")).status in (400, 404)


async def test_projects_size_cap_and_quota(srv, tmp_path):
    big = make_sb3({"big.bin": b"\0" * (26 * 1024 * 1024)})
    r = await srv.put("/api/projects/big.sb3", data=big)
    assert r.status == 413
    from picar_server.projects import ProjectStore, StoreError
    store = ProjectStore(tmp_path / "q", quota=4000, max_file=3000)
    sb3 = make_sb3()
    store.put("a", sb3)
    with pytest.raises(StoreError) as e:
        store.put("b", sb3 + b"\0" * 5000)
    assert e.value.status == 413
    n = 0
    with pytest.raises(StoreError) as e:
        while True:
            store.put(f"p{n}", sb3)
            n += 1
    assert e.value.status == 413 and store.used() <= 4000
    with pytest.raises(StoreError):                                 # an overwrite keeps a .bak, which counts
        store.put("a", sb3)
    store.delete("p0")
    store.put("a", sb3)                                             # freed space takes the overwrite


async def test_projects_unsafe_methods_check_origin_and_host(srv):
    sb3 = make_sb3()
    assert (await srv.put("/api/projects/a.sb3", data=sb3, headers={"Origin": "http://evil.example"})).status == 403
    assert (await srv.put("/api/projects/a.sb3", data=sb3, headers={"Host": "evil.example"})).status == 403
    assert (await srv.put("/api/projects/a.sb3", data=sb3, headers={"Origin": f"http://127.0.0.1:{srv.port}"})).status == 200
    assert (await srv.delete("/api/projects/a.sb3", headers={"Origin": "http://evil.example"})).status == 403
    assert (await srv.delete("/api/projects/a.sb3")).status == 200
