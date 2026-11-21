#!/usr/bin/env python3
"""Live view of the PiCar-S line follower and light sensors, for calibrating them by hand.

Run it ON THE CAR (needs only python3 and smbus2, which the car already has):

    ssh -t c4@picar.local python3 ~/sensor_monitor.py

It pauses the Car Scratch server while it runs (both would talk to the light sensor converter at once and
garble each other's readings) and starts it again when you quit; the motors are switched off by the
server's stop hook. Keys: r = reset the min/max, q or Ctrl-C = quit.

Options:
    --keep-server   do not pause the server (light values may glitch while it runs)
    --once          print one frame and exit (no screen refresh, no keys)
    --hz N          refresh rate (default 10)
"""
from __future__ import annotations

import argparse
import os
import select
import signal
import subprocess
import sys
import time

from smbus2 import SMBus

BUS = 1
LINE_ADDR = 0x11          # 5-way line follower: 10 bytes = 5 big-endian 16-bit values, sensor 1 = leftmost
PCF_ADDR = 0x48           # PCF8591: light sensors on its analog inputs 0..3 (lower = brighter)
LINE_MAX_VALID = 1023     # a frame with a larger value (e.g. 65535 = all 0xFF) is invalid
SERVICE = "car-scratch"
BAR = 28


def read_line(bus: SMBus):
    """Five values, or None for an invalid frame / a bus error."""
    try:
        raw = bus.read_i2c_block_data(LINE_ADDR, 0, 10)
    except OSError:
        return None
    vals = [(raw[2 * i] << 8) | raw[2 * i + 1] for i in range(5)]
    return None if any(v > LINE_MAX_VALID for v in vals) else vals


def read_light(bus: SMBus):
    """Four inputs; each needs a dummy read because the PCF8591 returns the previous conversion first."""
    out = []
    try:
        for ch in range(4):
            bus.write_byte(PCF_ADDR, 0x40 | ch)
            bus.read_byte(PCF_ADDR)
            out.append(bus.read_byte(PCF_ADDR))
    except OSError:
        return None
    return out


def bar(value: float, full: float) -> str:
    n = max(0, min(BAR, int(round(BAR * value / full))))
    return "#" * n + "." * (BAR - n)


class Range:
    def __init__(self, n: int):
        self.n = n
        self.reset()

    def reset(self):
        self.lo = [None] * self.n
        self.hi = [None] * self.n

    def add(self, vals):
        for i, v in enumerate(vals):
            self.lo[i] = v if self.lo[i] is None else min(self.lo[i], v)
            self.hi[i] = v if self.hi[i] is None else max(self.hi[i], v)


def frame(line, light, lr: Range, gr: Range, stats: dict, server_paused: bool) -> str:
    out = ["Car Scratch sensor monitor   r = reset min/max   q = quit"
           + ("   (server paused)" if server_paused else "   (server running: light may glitch)"), ""]
    out.append("LINE FOLLOWER (0x11)   higher = darker / no reflection, lower = white close by")
    out.append("            value  " + " " * BAR + "    min    max  threshold")
    names = ["1 left ", "2      ", "3 mid  ", "4      ", "5 right"]
    for i in range(5):
        v = line[i] if line else None
        lo, hi = lr.lo[i], lr.hi[i]
        thr = "-" if lo is None or hi - lo < 40 else str((lo + hi) // 2)
        out.append(f"  {names[i]}  {'----' if v is None else f'{v:4d}'}  {bar(v or 0, 512)}  "
                   f"{'' if lo is None else lo:>5} {'' if hi is None else hi:>6}  {thr:>9}")
    out.append(f"  invalid frames: {stats['invalid']} of {stats['frames']}"
               "   (threshold shown once a sensor has seen both white and the line, 40+ counts apart)")
    out.append("")
    out.append("LIGHT (PCF8591 0x48)   lower = brighter   (this car: left = in0, middle = in2, right = in1)")
    out.append("            value  " + " " * BAR + "    min    max")
    for i in range(4):
        v = light[i] if light else None
        lo, hi = gr.lo[i], gr.hi[i]
        out.append(f"  input {i}   {'---' if v is None else f'{v:4d}'}  {bar(255 - (v or 255), 255)}  "
                   f"{'' if lo is None else lo:>5} {'' if hi is None else hi:>6}")
    out.append("  (bar = brightness: longer = brighter)")
    return "\n".join(out)


def service_active() -> bool:
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", SERVICE]).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep-server", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--hz", type=float, default=10.0)
    a = ap.parse_args()

    paused = False
    if not a.keep_server and service_active():
        subprocess.run(["systemctl", "--user", "stop", SERVICE], check=False)
        paused = True

    def restore(*_):
        if paused:
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, restore)
    signal.signal(signal.SIGHUP, restore)

    lr, gr = Range(5), Range(4)
    stats = {"frames": 0, "invalid": 0}
    tty = sys.stdin.isatty() and not a.once
    old = None
    if tty:
        import termios
        import tty as ttymod
        old = termios.tcgetattr(sys.stdin)
        ttymod.setcbreak(sys.stdin.fileno())
    try:
        with SMBus(BUS) as bus:
            while True:
                line = read_line(bus)
                stats["frames"] += 1
                if line is None:
                    stats["invalid"] += 1
                else:
                    lr.add(line)
                light = read_light(bus)
                if light:
                    gr.add(light)
                text = frame(line, light, lr, gr, stats, paused)
                if a.once:
                    print(text)
                    break
                sys.stdout.write("\x1b[H\x1b[2J" + text + "\n")
                sys.stdout.flush()
                if tty and select.select([sys.stdin], [], [], 1.0 / a.hz)[0]:
                    key = os.read(sys.stdin.fileno(), 1).lower()
                    if key == b"q":
                        break
                    if key == b"r":
                        lr.reset()
                        gr.reset()
                        stats.update(frames=0, invalid=0)
                elif not tty:
                    time.sleep(1.0 / a.hz)
    except KeyboardInterrupt:
        pass
    finally:
        if old is not None:
            import termios
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        if paused:
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
            print("\nserver started again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
