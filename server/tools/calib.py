"""Calibration helper for the real car (run with the car on the floor, wheels down, clear space ahead).

  python3 -m tools.calib --url ws://picar.local:8080/ws trim
      Try steering trims interactively: type a number (degrees, + = right) to apply it, `s` to save
      (calibration.json on the car), `q` to quit. Drive with `wsclient drive forward 30` in another shell to
      see whether the car runs straight.

  python3 -m tools.calib --url ... speed [--speeds 30,40,50,60,80,100] [--ms 1500] [--floor 25 --ceiling 100]
      For each speed: the car drives forward for --ms, you measure the distance with a tape and type it in (cm).
      Prints the `speed_table` entries (duty % -> cm/s) to put in ~/.config/car-scratch/calibration.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional

from .wsclient import Client, _reply_ok


async def ask(prompt: str) -> str:
    return (await asyncio.get_running_loop().run_in_executor(None, input, prompt)).strip()


def duty_pct(speed: int, floor: float, ceiling: float) -> float:
    """Contract §3: user speed 0..100 -> duty %."""
    return 0.0 if speed <= 0 else floor + (ceiling - floor) * speed / 100


def speed_entry(speed: int, ms: int, cm: float, floor: float, ceiling: float) -> list:
    return [round(duty_pct(speed, floor, ceiling), 1), round(cm / (ms / 1000), 1)]


async def trim(c: Client) -> int:
    current = 0.0
    print("steering trim: number = try it, s = save, q = quit", flush=True)
    while True:
        a = await ask(f"trim [{current:+.1f} deg] > ")
        if a == "q":
            return 0
        if a == "s":
            if _reply_ok(await c.cmd("calibrate", steer_trim_deg=current, save=True)):
                print(f"saved steer_trim_deg={current}", flush=True)
            continue
        try:
            current = max(-20.0, min(20.0, float(a)))
        except ValueError:
            print("enter a number, s or q", flush=True)
            continue
        if _reply_ok(await c.cmd("calibrate", steer_trim_deg=current)):
            await c.cmd("steer", angle=0)                     # re-apply straight with the new trim
            print(f"trim {current:+.1f} applied (not saved yet)", flush=True)


async def speed(c: Client, speeds: list[int], ms: int, floor: float, ceiling: float) -> int:
    table: list[list] = []
    if not _reply_ok(await c.cmd("resume")):               # the car is on hold until resumed
        return 1
    for sp in speeds:
        if await ask(f"speed {sp}%: put the car on the start mark, Enter to run (q to finish) > ") == "q":
            break
        r = await c.cmd("move", want_done=True, dir="forward", speed=sp, ms=ms)
        fut = r.pop("_done")
        if not _reply_ok(r):
            continue
        done = await asyncio.wait_for(fut, ms / 1000 + 5)
        print(f"  move {done.get('result')}", flush=True)
        a = await ask("  distance travelled in cm (blank = skip) > ")
        if a:
            try:
                table.append(speed_entry(sp, ms, float(a), floor, ceiling))
            except ValueError:
                print("  not a number, skipped", flush=True)
    full = [[0, 0], [round(floor, 1), 0], *table]
    print("\n" + json.dumps({"speed_table": full}), flush=True)
    return 0


async def run(a: argparse.Namespace) -> int:
    async with Client(a.url, want="control", client_id=a.client_id) as c:
        if c.role != "controller":
            print("not the controller (stop other clients first)", flush=True)
            return 2
        if a.what == "trim":
            return await trim(c)
        return await speed(c, [int(x) for x in a.speeds.split(",")], a.ms, a.floor, a.ceiling)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="ws://picar.local:8080/ws")
    p.add_argument("--client-id", default=None)
    sub = p.add_subparsers(dest="what", required=True)
    sub.add_parser("trim")
    s = sub.add_parser("speed")
    s.add_argument("--speeds", default="30,40,50,60,80,100")
    s.add_argument("--ms", type=int, default=1500)
    s.add_argument("--floor", type=float, default=25)
    s.add_argument("--ceiling", type=float, default=100)
    args = p.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (KeyboardInterrupt, EOFError):
        return 130


if __name__ == "__main__":
    sys.exit(main())
