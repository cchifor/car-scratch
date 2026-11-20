"""Minimal systemd sd_notify (stdlib). No-ops when NOTIFY_SOCKET is unset, never raises."""
from __future__ import annotations

import os
import socket


def _send(msg: bytes) -> bool:
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return False
    try:
        addr = "\0" + path[1:] if path.startswith("@") else path   # leading '@' = abstract socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.connect(addr)
            s.send(msg)
        return True
    except OSError:
        return False


def ready() -> bool:
    """Call once the server is listening (Type=notify)."""
    return _send(b"READY=1")


def watchdog() -> bool:
    """Call more often than WatchdogSec/2 (e.g. every 1 s) from a healthy loop; a hung loop gets restarted."""
    return _send(b"WATCHDOG=1")
