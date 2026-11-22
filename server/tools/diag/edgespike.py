"""M8 spike: kernel-timestamped edges on the single-pin ultrasonic (GPIO5) with libgpiod v2.

Run on the car as c4 with car-scratch.service STOPPED (the server holds the line). Never drives motors.
  python3 edgespike.py <pings_per_mode> <out.jsonl>
Mode "events": trigger, arm edge detection, sleep in wait_edge_events (no polling).
Mode "poll": same, but also busy-poll get_value like the current server, to compare both timings on one echo.
"""
import json
import resource
import sys
import time

import gpiod
from gpiod.line import Clock, Direction, Edge, Value

CHIP, PIN = "/dev/gpiochip0", 5
N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
OUT = sys.argv[2] if len(sys.argv) > 2 else "edgespike.jsonl"
mono = time.monotonic_ns

OUT_LS = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)
IN_LS = gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.BOTH, event_clock=Clock.MONOTONIC)
RISE = gpiod.EdgeEvent.Type.RISING_EDGE


def drain(req):
    evs = []
    while req.wait_edge_events(0):
        evs += req.read_edge_events()
    return evs


def wait_low(req, limit_s=0.7):
    """The module may hold the line high (long cycle): never drive it low then."""
    t0 = mono()
    while req.get_value(PIN) == Value.ACTIVE:
        if (mono() - t0) / 1e9 > limit_s:
            return False
        req.wait_edge_events(0.05)
        drain(req)
    return True


def ping(req, mode):
    rec = {"mode": mode}
    if not wait_low(req):
        rec["stuck"] = True
        return rec
    drain(req)
    t_a = mono()
    req.reconfigure_lines({PIN: OUT_LS})
    rec["to_out_us"] = (mono() - t_a) / 1e3
    time.sleep(0.002)
    t_trig = mono()
    req.set_value(PIN, Value.ACTIVE)
    t_hi = mono()
    while mono() - t_hi < 10_000:
        pass
    req.set_value(PIN, Value.INACTIVE)
    t_trig_end = mono()
    req.reconfigure_lines({PIN: IN_LS})
    t_armed = mono()
    lvl = req.get_value(PIN) == Value.ACTIVE
    t_lvl = mono()
    rec.update(trig_us=(t_trig_end - t_trig) / 1e3, arm_us=(t_armed - t_trig_end) / 1e3,
               lvl_at_arm=lvl, lvl_read_us=(t_lvl - t_trig_end) / 1e3)
    poll = None
    if mode == "poll":
        # the current server's loop: wait for the rise, then for the fall, each read timestamped after
        prev = mono()
        rise = fall = None
        maxgap = 0
        while True:
            h = req.get_value(PIN) == Value.ACTIVE
            now = mono()
            maxgap = max(maxgap, now - prev)
            prev = now
            if rise is None:
                if h:
                    rise = now
                elif now - t_trig_end > 30_000_000:
                    break
            elif not h:
                fall = now
                break
            elif now - rise > 40_000_000:
                break
        poll = {"rise_us": None if rise is None else (rise - t_trig_end) / 1e3,
                "fall_us": None if fall is None else (fall - t_trig_end) / 1e3,
                "maxgap_us": maxgap / 1e3}
    # collect edges up to 45 ms after the trigger; if the line is still high, follow the long cycle to its end
    deadline = t_trig_end + 45_000_000
    evs = []
    while True:
        left = (deadline - mono()) / 1e9
        if left <= 0:
            break
        if req.wait_edge_events(left):
            evs += req.read_edge_events()
    busy = req.get_value(PIN) == Value.ACTIVE
    if busy:
        end = mono() + 700_000_000
        while req.get_value(PIN) == Value.ACTIVE and mono() < end:
            if req.wait_edge_events(0.05):
                evs += req.read_edge_events()
        evs += drain(req)
    rec.update(busy=busy, poll=poll, events=[
        ("R" if e.event_type == RISE else "F", (e.timestamp_ns - t_trig_end) / 1e3, e.line_seqno) for e in evs])
    return rec


def main():
    req = gpiod.request_lines(CHIP, consumer="edgespike", config={PIN: IN_LS}, event_buffer_size=256)
    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.monotonic()
    with open(OUT, "w") as f:
        for mode in ("events", "poll"):
            c0 = resource.getrusage(resource.RUSAGE_SELF)
            m0 = time.monotonic()
            for _ in range(N):
                f.write(json.dumps(ping(req, mode)) + "\n")
                time.sleep(0.010)
            c1 = resource.getrusage(resource.RUSAGE_SELF)
            cpu = (c1.ru_utime + c1.ru_stime) - (c0.ru_utime + c0.ru_stime)
            f.write(json.dumps({"summary": mode, "wall_s": time.monotonic() - m0, "cpu_s": cpu}) + "\n")
    req.reconfigure_lines({PIN: gpiod.LineSettings(direction=Direction.INPUT)})
    req.release()
    print("done", N, "pings per mode in", round(time.monotonic() - t0, 1), "s")


if __name__ == "__main__":
    main()
