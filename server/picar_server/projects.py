"""'Save to car' project storage and its REST endpoints (docs/protocol.md section 6)."""
from __future__ import annotations

import asyncio
import io
import os
import re
import zipfile
from pathlib import Path

from aiohttp import web

NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")
MAX_FILE = 25 * 1024 * 1024
QUOTA = 500 * 1024 * 1024
SUFFIX = ".sb3"


class StoreError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


class ProjectStore:
    def __init__(self, root, quota: int = QUOTA, max_file: int = MAX_FILE):
        self.root, self.quota, self.max_file = Path(root), quota, max_file

    def path(self, name: str) -> Path:
        if not NAME_RE.match(name):
            raise StoreError(400, "invalid project name")
        return self.root / (name + SUFFIX)

    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for p in sorted(self.root.glob("*" + SUFFIX)):
                name = p.name[: -len(SUFFIX)]
                if NAME_RE.match(name):
                    st = p.stat()
                    out.append({"name": name, "size": st.st_size, "mtime": int(st.st_mtime)})
        return out

    def used(self) -> int:
        return sum(p.stat().st_size for p in self.root.iterdir() if p.is_file()) if self.root.is_dir() else 0

    def put(self, name: str, data: bytes) -> dict:
        path = self.path(name)
        if len(data) > self.max_file:
            raise StoreError(413, "project is larger than 25 MB")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "project.json" not in z.namelist():
                    raise StoreError(400, "not an sb3 project (no project.json)")
        except zipfile.BadZipFile:
            raise StoreError(400, "not a zip file")
        self.root.mkdir(parents=True, exist_ok=True)
        old = path.stat().st_size if path.exists() else 0
        if self.used() - old + len(data) > self.quota:
            raise StoreError(413, "project storage is full")
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.replace(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)
        st = path.stat()
        return {"name": name, "size": st.st_size, "mtime": int(st.st_mtime)}

    def delete(self, name: str) -> None:
        path = self.path(name)
        if not path.exists():
            raise StoreError(404, "no such project")
        path.unlink()


def _name_from(request: web.Request) -> str:
    fname = request.match_info["file"]
    if not fname.endswith(SUFFIX):
        raise StoreError(404, "not found")
    return fname[: -len(SUFFIX)]


def add_routes(app: web.Application, store: ProjectStore) -> None:
    loop_run = lambda fn, *a: asyncio.get_running_loop().run_in_executor(None, fn, *a)  # noqa: E731

    def guard(request: web.Request) -> None:
        if not request.app["host_policy"].allowed(request.headers.get("Origin"), request.headers.get("Host")):
            raise web.HTTPForbidden(text="forbidden origin or host")

    def fail(e: StoreError) -> web.Response:
        return web.json_response({"error": e.msg}, status=e.status)

    async def list_(request):
        return web.json_response(await loop_run(store.list))

    async def get(request):
        try:
            path = store.path(_name_from(request))
        except StoreError as e:
            return fail(e)
        if not path.is_file():
            return web.json_response({"error": "no such project"}, status=404)
        return web.FileResponse(path, headers={"Content-Type": "application/x.scratch.sb3"})

    async def put(request):
        guard(request)
        try:
            name = _name_from(request)
            store.path(name)
            if request.content_length is not None and request.content_length > store.max_file:
                raise StoreError(413, "project is larger than 25 MB")
            buf = bytearray()
            async for chunk in request.content.iter_chunked(1 << 16):
                buf += chunk
                if len(buf) > store.max_file:
                    raise StoreError(413, "project is larger than 25 MB")
            return web.json_response(await loop_run(store.put, name, bytes(buf)))
        except StoreError as e:
            return fail(e)

    async def delete(request):
        guard(request)
        try:
            await loop_run(store.delete, _name_from(request))
        except StoreError as e:
            return fail(e)
        return web.json_response({"ok": True})

    app.router.add_get("/api/projects", list_)
    app.router.add_get("/api/projects/{file}", get)
    app.router.add_put("/api/projects/{file}", put)
    app.router.add_delete("/api/projects/{file}", delete)
