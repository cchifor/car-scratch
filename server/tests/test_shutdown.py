"""Shutdown: on SIGTERM the car stops and every WebSocket closes before aiohttp waits for open requests.

Measured 2026-11-22: with one open WebSocket the server took 7.5 s to exit (aiohttp waits for running handlers
before cleanup, and the sockets were only closed in cleanup), so systemd's 2 s stop timeout killed it, and the
in-process motor stop never ran (4 of 19 stops on the car)."""
import asyncio
import signal
import socket
import subprocess
import sys
import time

import aiohttp

from test_server import connect, go, hello, srv  # noqa: F401  (fixture)


async def test_shutdown_stops_the_car_and_closes_every_websocket(srv):  # noqa: F811
    ctl, _ = await hello(srv)
    view, _ = await hello(srv, cid="client-bbbb-0002", want="view")
    await go(ctl)
    await ctl.ok("drive", dir="forward", speed=40)
    car = srv.app["hub"].car
    assert car._moving()
    await srv.app.shutdown()
    assert not car._moving()
    assert car.hold and car.hold_reason == "shutdown"
    assert await ctl.close_code() == 1001
    assert await view.close_code() == 1001


async def test_shutdown_twice_is_harmless(srv):  # noqa: F811
    await srv.app.shutdown()
    await srv.app.shutdown()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_sigterm_with_an_open_websocket_exits_within_a_second(tmp_path):
    port = _free_port()
    p = subprocess.Popen([sys.executable, "-m", "picar_server", "--hardware", "sim", "--host", "127.0.0.1",
                          "--port", str(port), "--data", str(tmp_path), "--config", str(tmp_path),
                          "--www", str(tmp_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with aiohttp.ClientSession() as s:
            for _ in range(100):
                try:
                    async with s.get(f"http://127.0.0.1:{port}/api/status") as r:
                        if r.status == 200:
                            break
                except aiohttp.ClientError:
                    await asyncio.sleep(0.1)
            ws = await s.ws_connect(f"http://127.0.0.1:{port}/ws", headers={"Origin": f"http://127.0.0.1:{port}"})
            await ws.send_json({"t": "hello", "v": 1, "client_id": "client-cccc-0003", "want": "view", "mode": "car"})
            await ws.receive(timeout=2)
            t0 = time.monotonic()
            p.send_signal(signal.SIGTERM)
            while p.poll() is None and time.monotonic() - t0 < 5:
                try:
                    await ws.receive(timeout=0.05)
                except asyncio.TimeoutError:
                    pass
            assert p.poll() is not None and time.monotonic() - t0 < 1.0, time.monotonic() - t0
    finally:
        if p.poll() is None:
            p.kill()
