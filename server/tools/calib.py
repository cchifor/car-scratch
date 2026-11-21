"""Calibration helper for the real car (run with the car on the floor, wheels down, clear space ahead).

  python3 -m tools.calib --url ws://picar.local:8080/ws trim
      Try steering trims interactively: type a number (degrees, + = right) to apply it, `s` to save
      (calibration.json on the car), `q` to quit. Drive with `wsclient drive forward 30` in another shell to
      see whether the car runs straight.

  python3 -m tools.calib --url ... speed [--speeds 30,40,50,60,80,100] [--ms 1500] [--floor 25 --ceiling 100]
      For each speed: the car drives forward for --ms, you measure the distance with a tape and type it in (cm).
      Prints the `speed_table` entries (duty % -> cm/s) to put in ~/.config/car-scratch/calibration.json.

  python3 -m tools.calib --url ... light [--seconds 2]
      Guided light-sensor calibration. Asks for room light, then a torch on the LEFT, MIDDLE and RIGHT sensor
      in turn, works out which PCF8591 input each sensor is wired to (`light_channels`) and the room and
      torch levels (`light_dark`, `light_bright`, one per sensor), and saves them on the car.
      Assumes the three connected sensors are on inputs 0..2 (input 3 is not connected on this car).

  python3 -m tools.calib --url ... line [--seconds 2]
      Guided line-follower calibration (needs `line_enabled` true in calibration.json). Reads the car on white
      paper, then while you slide a black line under all five sensors, works out the polarity
      (`line_black_high`) and a threshold per sensor (`line_threshold`), and saves them on the car.

  Neither subcommand ever sends a motion command.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
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


# ---- light and line calibration: pure maths (unit-tested) -------------------------------------------------------

MIN_LIGHT_DROP = 30       # a torch must lower its sensor's raw value by at least this much
MIN_LINE_DIFF = 40        # white and black must differ by at least this much on every line sensor
POSITIONS = ("left", "middle", "right")


class CalibError(Exception):
    """The measurements cannot be turned into a calibration; the message tells the user what to check."""


def medians(frames: list[list[float]]) -> list[float]:
    """Per-slot median over a list of equally long frames."""
    if not frames:
        raise CalibError("no telemetry frames were received (is the sensor answering?)")
    return [statistics.median(col) for col in zip(*frames)]


def infer_light(room: list[float], torch: dict[str, list[float]]) -> dict:
    """room: median of the three slots with no torch; torch[pos]: medians with the torch on that sensor.
    The slots are the PCF8591 inputs 0, 1, 2 (what the car reports after `light_channels=[0, 1, 2]`).
    Returns the calibrate fields; raises CalibError when a torch did not clearly light exactly one input."""
    channels = []
    for pos in POSITIONS:
        drops = [r - t for r, t in zip(room, torch[pos])]
        best = max(range(len(drops)), key=lambda i: drops[i])
        if drops[best] < MIN_LIGHT_DROP:
            raise CalibError(f"the torch on the {pos} sensor changed no input by {MIN_LIGHT_DROP} or more "
                             f"(drops {[round(d) for d in drops]}); hold it closer and retry")
        channels.append(best)
    if len(set(channels)) != 3:
        raise CalibError(f"two sensors map to the same input ({channels}); retry, lighting one sensor at a time")
    return {"light_channels": channels,
            "light_dark": [round(room[c]) for c in channels],
            "light_bright": [round(torch[pos][c]) for pos, c in zip(POSITIONS, channels)]}


def infer_line(white: list[float], extremes_hi: list[float], extremes_lo: list[float]) -> dict:
    """white: per-sensor median over white paper; extremes_hi / extremes_lo: per-sensor max / min seen while a
    black line slid under every sensor. Polarity = the direction in which the readings moved most overall.
    Returns the calibrate fields; raises CalibError when any sensor's white/black difference is too small."""
    up = sum(h - w for h, w in zip(extremes_hi, white))
    down = sum(w - lo for lo, w in zip(extremes_lo, white))
    black_high = up >= down
    black = extremes_hi if black_high else extremes_lo
    weak = [i + 1 for i, (w, b) in enumerate(zip(white, black)) if abs(b - w) < MIN_LINE_DIFF]
    if weak:
        raise CalibError(f"line sensor(s) {weak}: white and black differ by less than {MIN_LINE_DIFF} counts: "
                         "sensor too far from the ground or IR LEDs not powered")
    return {"line_black_high": black_high,
            "line_threshold": [round((w + b) / 2) for w, b in zip(white, black)]}


# ---- sampling (thin I/O) ----------------------------------------------------------------------------------------

def _raw(tel: Optional[dict], key: str) -> Optional[list]:
    part = (tel or {}).get(key) or {}
    return part.get("raw") if part.get("ok") else None


async def sample_for(c: Client, key: str, seconds: float, stop: Optional[asyncio.Event] = None) -> list[list]:
    """Raw frames of tel[key] (one per new telemetry frame) for `seconds`, or until `stop` is set."""
    frames, last = [], None
    end = time.monotonic() + seconds
    while time.monotonic() < end and not (stop is not None and stop.is_set()) and not c.closed.is_set():
        tel = c.tel
        raw = _raw(tel, key)
        if tel is not None and tel.get("seq") != last and raw is not None:
            last = tel.get("seq")
            frames.append(list(raw))
        await asyncio.sleep(0.02)
    return frames


async def _save(c: Client, fields: dict) -> int:
    print(json.dumps(fields), flush=True)
    if _reply_ok(await c.cmd("calibrate", save=True, **fields)):
        print("saved", flush=True)
        return 0
    return 1


async def light(c: Client, seconds: float) -> int:
    reply = await c.cmd("calibrate", light_channels=[0, 1, 2])     # slots = inputs 0, 1, 2 (live, not saved)
    if not _reply_ok(reply):
        return 1
    original = (reply.get("previous") or {}).get("light_channels")
    saved = False
    try:
        rc = await _light_steps(c, seconds)
        saved = rc == 0
        return rc
    finally:
        if not saved and original is not None:         # failed, cancelled or disconnected: put the wiring back
            await _restore(c, light_channels=original)


async def _restore(c: Client, **fields) -> None:
    try:
        reply = await asyncio.wait_for(c.cmd("calibrate", **fields), 3)
    except Exception:                                  # noqa: BLE001 - best effort; the socket may be gone
        reply = None
    if reply is not None and _reply_ok(reply):
        print("restored the car's previous " + ", ".join(fields), flush=True)
    else:
        why = f" ({reply.get('code')})" if isinstance(reply, dict) and reply.get("code") else ""
        print("could NOT restore the car's previous " + ", ".join(fields) + why
              + ": set it again with calib, or restart the service (the saved file is unchanged)", flush=True)


async def _light_steps(c: Client, seconds: float) -> int:
    await asyncio.sleep(0.3)
    await ask("light: room lighting as normal, nothing shining on the sensors. Enter to sample > ")
    try:
        room = medians(await sample_for(c, "light", seconds))
        print(f"  room (inputs 0,1,2): {[round(v) for v in room]}", flush=True)
        torch: dict[str, list[float]] = {}
        for pos in POSITIONS:
            await ask(f"hold the torch close on the {pos.upper()} sensor, Enter to sample > ")
            torch[pos] = medians(await sample_for(c, "light", seconds))
            print(f"  torch {pos}: {[round(v) for v in torch[pos]]}", flush=True)
        fields = infer_light(room, torch)
    except CalibError as e:
        print(f"not saved: {e}", flush=True)
        return 1
    return await _save(c, fields)


async def line(c: Client, seconds: float) -> int:
    await ask("line: put the car on plain WHITE paper at driving height. Enter to sample > ")
    try:
        white = medians(await sample_for(c, "line", seconds))
    except CalibError as e:
        print(f"not saved: {e} (is line_enabled true in calibration.json?)", flush=True)
        return 1
    print(f"  white: {[round(v) for v in white]}", flush=True)
    stop = asyncio.Event()
    window = asyncio.create_task(sample_for(c, "line", 600.0, stop))
    await ask("slide a black line slowly under ALL five sensors (by hand), then press Enter > ")
    stop.set()
    frames = await window
    try:
        if not frames:
            raise CalibError("no line frames were received while you slid the line")
        hi, lo = [max(col) for col in zip(*frames)], [min(col) for col in zip(*frames)]
        print(f"  max {hi}  min {lo}", flush=True)
        fields = infer_line(white, hi, lo)
    except CalibError as e:
        print(f"not saved: {e}", flush=True)
        return 1
    return await _save(c, fields)


async def run(a: argparse.Namespace) -> int:
    async with Client(a.url, want="control", client_id=a.client_id) as c:
        if c.role != "controller":
            print("not the controller (stop other clients first)", flush=True)
            return 2
        if a.what == "trim":
            return await trim(c)
        if a.what == "light":
            return await light(c, getattr(a, "seconds", 2.0))
        if a.what == "line":
            return await line(c, getattr(a, "seconds", 2.0))
        return await speed(c, [int(x) for x in a.speeds.split(",")], a.ms, a.floor, a.ceiling)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="ws://picar.local:8080/ws")
    p.add_argument("--client-id", default=None)
    sub = p.add_subparsers(dest="what", required=True)
    sub.add_parser("trim")
    for name in ("light", "line"):
        sub.add_parser(name).add_argument("--seconds", type=float, default=2.0)
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
