"""python3 -m picar_server --port 8080 --hardware auto|real|dryrun|sim|none --www DIR ..."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from aiohttp import web

from .app import Settings, create_app


def _split(values: list[str]) -> list[str]:
    return [v.strip() for item in values for v in item.split(",") if v.strip()]


SHUTDOWN_TIMEOUT_S = 0.5


def main(argv=None) -> None:
    d = Settings()
    p = argparse.ArgumentParser(prog="picar_server")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--hardware", choices=["auto", "real", "dryrun", "sim", "none"], default="auto")
    p.add_argument("--www", type=Path, default=Path("www"), help="editor build directory")
    p.add_argument("--examples", type=Path, default=Path("examples"))
    p.add_argument("--source", type=Path, default=Path("source"), help="AGPL source tarballs")
    p.add_argument("--data", type=Path, default=d.data, help="projects are stored under DATA/projects")
    p.add_argument("--config", type=Path, default=d.config, help="calibration.json directory")
    p.add_argument("--allow-host", action="append", default=[], help="extra accepted Host name (repeatable)")
    p.add_argument("--allow-origin", action="append", default=[], help="extra accepted Origin (repeatable)")
    p.add_argument("--us-timing", choices=["auto", "edges", "poll"], default="auto",
                   help="ultrasonic echo timing: kernel edge timestamps, polling, or edges with a polling fallback")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings(port=a.port, hardware=a.hardware, us_timing=a.us_timing, www=a.www, examples=a.examples, source=a.source,
                        data=a.data, config=a.config, allow_hosts=_split(a.allow_host),
                        allow_origins=_split(a.allow_origin))
    # shutdown_timeout: how long aiohttp waits for open requests after on_shutdown closed the sockets; cleanup
    # (backend close) must still finish inside systemd's TimeoutStopSec=2.
    web.run_app(create_app(settings), host=a.host, port=a.port, access_log=None, print=None,
                shutdown_timeout=SHUTDOWN_TIMEOUT_S)


if __name__ == "__main__":
    main()
