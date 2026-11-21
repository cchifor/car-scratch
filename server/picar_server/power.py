"""Power status of the Pi that runs the car (docs/protocol.md section 5, `power`).

The car has no battery-voltage sensor. What the Pi does tell us is whether its own supply is sagging
(`rpi_volt` hwmon alarm / `vcgencmd get_throttled`), which on a battery car means "the batteries are low".
Everything here is polled from the asyncio loop (sysfs reads are cheap, vcgencmd runs as an async subprocess)
and never touches the sensor thread, which busy-waits on the ultrasonic echo."""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

POLL_S = 1.0                  # sysfs / uptime / load
VCGENCMD_S = 10.0             # vcgencmd get_throttled
VCGENCMD_TIMEOUT_S = 2.0
OK_AFTER_S = 5.0              # `low` must stay false this long before power_ok

HWMON_GLOB = "/sys/class/hwmon/hwmon*"
THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"
UPTIME_PATH = "/proc/uptime"
VCGENCMD = "/usr/bin/vcgencmd"

T_UNDERVOLT, T_THROTTLED = 1 << 0, 1 << 2
T_UNDERVOLT_SINCE = 1 << 16

_THROTTLED_RE = re.compile(r"throttled=(0x[0-9a-fA-F]+)")

EventCb = Callable[[str, dict], None]
VcgencmdFn = Callable[[], Awaitable[str]]


def parse_throttled(text: str) -> Optional[int]:
    """`throttled=0x50005` -> 0x50005; None when the text is not a get_throttled answer."""
    m = _THROTTLED_RE.search(text or "")
    return int(m.group(1), 16) if m else None


def _read(path) -> Optional[str]:
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return None


def find_alarm(hwmon_glob: str = HWMON_GLOB) -> Optional[str]:
    """Path of the rpi_volt under-voltage alarm. The hwmon index changes between boots: find it by name."""
    for d in sorted(glob.glob(hwmon_glob)):
        if _read(os.path.join(d, "name")) == "rpi_volt":
            return os.path.join(d, "in0_lcrit_alarm")
    return None


async def run_vcgencmd() -> str:
    """Async `vcgencmd get_throttled` with a hard timeout. Raises on any failure (the monitor keeps old values)."""
    proc = await asyncio.create_subprocess_exec(VCGENCMD, "get_throttled", stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), VCGENCMD_TIMEOUT_S)
    except BaseException:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    return out.decode(errors="replace")


class PowerMonitor:
    def __init__(self, *, hwmon_glob: str = HWMON_GLOB, thermal_path: str = THERMAL_PATH,
                 uptime_path: str = UPTIME_PATH, vcgencmd: Optional[VcgencmdFn] = None,
                 clock: Callable[[], float] = time.monotonic, on_event: Optional[EventCb] = None,
                 loadavg: Callable[[], tuple] = os.getloadavg, cpu_count: Callable[[], Optional[int]] = os.cpu_count):
        self.hwmon_glob, self.thermal_path, self.uptime_path = hwmon_glob, thermal_path, uptime_path
        self._vcgencmd = vcgencmd if vcgencmd is not None else (run_vcgencmd if os.path.exists(VCGENCMD) else None)
        self._clock, self.on_event = clock, on_event
        self._loadavg, self._cpu_count = loadavg, cpu_count
        self._alarm_path: Optional[str] = None
        self._alarm: Optional[bool] = None        # last alarm read; None = unreadable
        self._throttled: Optional[int] = None     # last get_throttled value; None = never answered
        self.low_events = 0
        self._low = False
        self._seen_low = False
        self._ok_since: Optional[float] = None    # when `low` went false after a power_low
        self._pending_ok = False                  # a power_low was sent and no power_ok yet
        self._cache: dict = self._build()
        self._tasks: list[asyncio.Task] = []

    # ---- polling -------------------------------------------------------------------------------
    def poll(self) -> None:
        """One 1 Hz pass over the cheap sources. Never raises."""
        try:
            self._poll()
        except Exception:
            log.exception("power poll failed")

    def _poll(self) -> None:
        if self._alarm_path is None:
            self._alarm_path = find_alarm(self.hwmon_glob)
        v = _read(self._alarm_path) if self._alarm_path else None
        self._alarm = None if v is None else v == "1"
        if v is None:
            self._alarm_path = None               # rediscover next time (the index may have changed)
        self._update_low(self._clock())
        self._cache = self._build()

    def feed_throttled(self, text: str) -> None:
        """A vcgencmd answer. Unparseable text keeps the last value."""
        val = parse_throttled(text)
        if val is None:
            return
        self._throttled = val
        self._update_low(self._clock())
        self._cache = self._build()

    def _current_low(self) -> Optional[bool]:
        if self._alarm is not None:
            return self._alarm
        if self._throttled is not None:
            return bool(self._throttled & T_UNDERVOLT)
        return None

    def _update_low(self, now: float) -> None:
        cur = self._current_low()
        if cur is None:
            return
        if cur:
            if not self._low:
                self.low_events += 1
                self._seen_low = True
                self._pending_ok = True
                self._emit("power_low", {"low_events": self.low_events})
            self._ok_since = None
        elif self._pending_ok:
            if self._ok_since is None:
                self._ok_since = now
            elif now - self._ok_since >= OK_AFTER_S:      # a dip that comes back within 5 s is not "ok" yet
                self._pending_ok, self._ok_since = False, None
                self._emit("power_ok", {})
        self._low = cur

    def _emit(self, kind: str, fields: dict) -> None:
        if self.on_event:
            try:
                self.on_event(kind, fields)
            except Exception:
                log.exception("power event handler failed")

    # ---- snapshot ------------------------------------------------------------------------------
    def _build(self) -> dict:
        known = self._current_low() is not None
        t = self._throttled or 0
        temp = _read(self.thermal_path)
        try:
            cpu_temp = round(int(temp) / 1000.0, 1) if temp is not None else None
        except ValueError:
            cpu_temp = None
        up = _read(self.uptime_path)
        try:
            on_s = int(float(up.split()[0])) if up else 0
        except (ValueError, IndexError):
            on_s = 0
        load = None
        try:
            n = self._cpu_count()
            if n:
                load = round(self._loadavg()[0] / n, 2)
        except (OSError, AttributeError, TypeError):
            pass
        return {"source": "pi" if known else "none", "low": bool(self._low),
                "low_since_boot": bool(self._seen_low or t & T_UNDERVOLT_SINCE),
                "low_events": self.low_events, "throttled": bool(t & T_THROTTLED), "on_s": on_s,
                "motor_s": 0.0, "cpu_temp_c": cpu_temp, "cpu_load": load}

    def snapshot(self, motor_s: float = 0.0) -> dict:
        """The cached `power` dict (called at 20 Hz, so no I/O) with the controller's motor time filled in."""
        return {**self._cache, "motor_s": round(motor_s, 1)}

    # ---- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.poll()
        self._tasks = [loop.create_task(self._poll_loop())]
        if self._vcgencmd is not None:
            self._tasks.append(loop.create_task(self._vcgencmd_loop()))

    def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(POLL_S)
            self.poll()

    async def _vcgencmd_loop(self) -> None:
        while True:
            try:
                self.feed_throttled(await asyncio.wait_for(self._vcgencmd(), VCGENCMD_TIMEOUT_S + 1))
            except asyncio.CancelledError:
                raise
            except Exception:
                pass                                  # absent / slow / failing vcgencmd: keep the last values
            await asyncio.sleep(VCGENCMD_S)


def sim_power(on_s: float, motor_s: float) -> dict:
    return {"source": "sim", "low": False, "low_since_boot": False, "low_events": 0, "throttled": False,
            "on_s": int(on_s), "motor_s": round(motor_s, 1), "cpu_temp_c": None, "cpu_load": None}
