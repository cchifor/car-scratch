"""aiohttp application: WebSocket, REST, static files and hardware selection."""
from __future__ import annotations

import asyncio
import gzip
import html
import importlib
import logging
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from aiohttp import web

from . import __version__, protocol, sdnotify
from .config import load_calibration, save_calibration
from .controller import Controller
from .hal.sim.backend import SimBackend
from .hal.sim.world import DEFAULT_WORLD, load_world
from .projects import MAX_FILE, ProjectStore, add_routes
from .session import Hub, ws_handler

log = logging.getLogger(__name__)

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/x.scratch.sb3", ".sb3")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")

MAX_BODY = MAX_FILE + (1 << 20)


@dataclass
class Settings:
    port: int = 8080
    hardware: str = "auto"                 # auto | real | dryrun | sim | none
    us_timing: str = "auto"                # auto | edges | poll: how the real/dry-run backend times echoes (D9)
    www: Optional[Path] = None
    examples: Optional[Path] = None
    source: Optional[Path] = None
    data: Path = Path.home() / ".local" / "share" / "car-scratch"
    config: Path = Path.home() / ".config" / "car-scratch"
    allow_hosts: list = field(default_factory=list)
    allow_origins: list = field(default_factory=list)
    backend_factory: Optional[Callable[[dict], object]] = None   # tests: replaces the hardware choice
    legacy_calibration: Optional[Path] = None
    idle_s: float = protocol.IDLE_S
    lease_s: float = protocol.LEASE_S


def make_backend(settings: Settings, cal: dict):
    """Returns (backend or None, {"error"?: str}). Never raises: a missing car means "car unavailable"."""
    if settings.backend_factory is not None:
        return settings.backend_factory(cal), {}
    kind = settings.hardware
    if kind == "none":
        return None, {"error": "hardware disabled (--hardware none)"}
    try:
        if kind == "sim":
            return SimBackend(load_world(DEFAULT_WORLD), cal), {}
        module, cls = ("dryrun", "DryRunBackend") if kind == "dryrun" else ("real", "RealBackend")
        return getattr(importlib.import_module(f"picar_server.hal.{module}"), cls)(cal, us_timing=settings.us_timing), {}
    except Exception as e:  # ImportError, missing I2C bus, permissions ...
        log.error("hardware unavailable (%s): %s", kind, e)
        return None, {"error": f"{type(e).__name__}: {e}"}


# ---- static files -------------------------------------------------------------------------------------

def _inside(root: Path, p: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def static_response(root: Optional[Path], rel: str, request: web.Request) -> web.StreamResponse:
    """Path-confined static file with precompressed `.gz` negotiation."""
    if root is None or not root.is_dir() or "\0" in rel:
        raise web.HTTPNotFound()
    base = root.resolve()
    target = (base / rel.lstrip("/")).resolve()
    if not _inside(base, target):
        raise web.HTTPNotFound()
    if target.is_dir():
        target = (target / "index.html").resolve()
        if not _inside(base, target):
            raise web.HTTPNotFound()
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    headers = {"Vary": "Accept-Encoding", "Cache-Control": "no-cache"}
    gz = target.with_name(target.name + ".gz")
    gz_ok = gz.is_file() and _inside(base, gz.resolve())
    accepts = "gzip" in request.headers.get("Accept-Encoding", "")
    if gz_ok and accepts:
        return web.FileResponse(gz, headers={**headers, "Content-Type": ctype, "Content-Encoding": "gzip"})
    if target.is_file():
        return web.FileResponse(target, headers={**headers, "Content-Type": ctype})
    if gz_ok:  # only the compressed file exists and the client cannot take it
        return web.Response(body=gzip.decompress(gz.read_bytes()), headers={**headers, "Content-Type": ctype})
    raise web.HTTPNotFound()


def _listing(root: Optional[Path], title: str) -> web.Response:
    if root is None or not root.is_dir():
        raise web.HTTPNotFound()
    names = sorted(p.name for p in root.iterdir() if p.is_file())
    items = "".join(f'<li><a href="{html.escape(n)}">{html.escape(n)}</a></li>' for n in names)
    return web.Response(text=f"<!doctype html><title>{html.escape(title)}</title><h1>{html.escape(title)}</h1>"
                             f"<ul>{items}</ul>", content_type="text/html")


async def _www(request: web.Request) -> web.StreamResponse:
    root = request.app["settings"].www
    rel = request.match_info["tail"]
    try:
        return static_response(root, rel, request)
    except web.HTTPNotFound:
        if rel == "":
            return web.Response(text="Car Scratch server is running; the editor is not installed (no www/index.html).",
                                content_type="text/plain", status=404)
        raise


async def _examples(request: web.Request) -> web.StreamResponse:
    return static_response(request.app["settings"].examples, request.match_info["tail"], request)


async def _source_redirect(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/source/")


async def _source(request: web.Request) -> web.StreamResponse:
    rel = request.match_info["tail"]
    root = request.app["settings"].source
    if rel == "":
        return _listing(root, "Car Scratch source")
    return static_response(root, rel, request)


# ---- API -----------------------------------------------------------------------------------------------

async def _status(request: web.Request) -> web.Response:
    return web.json_response(request.app["hub"].status())


async def _estop(request: web.Request) -> web.Response:
    if not request.app["host_policy"].allowed(request.headers.get("Origin"), request.headers.get("Host")):
        raise web.HTTPForbidden(text="forbidden origin or host")
    request.app["hub"].estop(None, "api")
    return web.json_response({"ok": True})


async def _sd_loop() -> None:
    await asyncio.sleep(0.1)
    sdnotify.ready()
    while True:
        sdnotify.watchdog()
        await asyncio.sleep(0.5)


def create_app(settings: Settings) -> web.Application:
    app = web.Application(client_max_size=MAX_BODY)
    app["settings"] = settings
    app["host_policy"] = protocol.HostPolicy(settings.allow_hosts, settings.allow_origins)
    add_routes(app, ProjectStore(Path(settings.data) / "projects"))
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/status", _status)
    app.router.add_post("/api/estop", _estop)
    app.router.add_get("/examples/{tail:.*}", _examples)
    app.router.add_get("/source/{tail:.*}", _source)
    app.router.add_get("/source", _source_redirect)
    app.router.add_get("/{tail:.*}", _www)
    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    app.on_cleanup.append(_on_cleanup)
    return app


async def _on_startup(app: web.Application) -> None:
    s: Settings = app["settings"]
    cal = load_calibration(s.config, s.legacy_calibration)
    backend, hw = make_backend(s, cal)
    car = Controller(backend, cal, shared=True, start_on_hold=True, error=hw.get("error"),
                     save_calibration=lambda c: save_calibration(s.config, c))
    try:
        car.start()
    except Exception as e:
        log.error("car failed to start: %s", e)
        try:
            backend.close()
        except Exception:
            pass
        hw = {"error": f"{type(e).__name__}: {e}"}
        car = Controller(None, cal, shared=True, start_on_hold=True, error=hw["error"])
        car.start()
    info = {"version": __version__, "sha": os.environ.get("CAR_SCRATCH_SHA") or "dev", "source_url": "/source/"}
    hub = Hub(car, cal, hw=hw, server_info=info, idle_s=s.idle_s, lease_s=s.lease_s)
    hub.start()
    app["hub"] = hub
    app["sd_task"] = asyncio.get_running_loop().create_task(_sd_loop())


async def _on_shutdown(app: web.Application) -> None:
    """Runs before aiohttp waits for open requests: stop the car, then close every WebSocket. An open socket
    otherwise kept the process alive past systemd's 2 s stop timeout (7.5 s measured with one viewer), and the
    motors were left to ExecStopPost."""
    hub: Optional[Hub] = app.get("hub")
    if hub is None:
        return
    try:
        hub.car.stop_and_hold("shutdown")
    except Exception:                                  # noqa: BLE001 - closing the sockets must still happen
        log.exception("stop on shutdown failed")
    await hub.stop()


async def _on_cleanup(app: web.Application) -> None:
    hub: Optional[Hub] = app.get("hub")
    if "sd_task" in app:
        app["sd_task"].cancel()
    if hub is not None:
        await hub.stop()
        hub.car.close()
