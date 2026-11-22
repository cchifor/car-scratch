"""On-car check of the edge-timed ultrasonic (plan M8, step 0). Run as the car user with the server STOPPED (it owns
GPIO5); nothing moves. From the release's server directory:

    systemctl --user stop car-scratch
    python3 tools/diag/edgeping.py a [seconds] [load_procs] [switch_ms]   # the real EdgeUltrasonic, pinging
    python3 tools/diag/edgeping.py b [tries]                              # does a pull-up alone trigger the module?
    systemctl --user start car-scratch

a: opens the edge sensor as the server does (line lookup, start-up probe), then runs EdgeUltrasonic.measure(3) at
   the server's cadence for `seconds`, with `load_procs` busy processes and the GIL switch interval at `switch_ms`;
   prints the trigger call percentiles, results, discards, diagnostics and CPU.
b: the raw pull-up trigger test (edges armed, the line never driven): does the module answer, and when.
"""
import collections
import fcntl
import multiprocessing
import os
import resource
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from picar_server.hal import gpio_cdev as cd                         # noqa: E402
from picar_server.hal.sensors import BUSY, FAR, SystemClock           # noqa: E402
from picar_server.hal.ultrasonic_edges import TRIGGER_MAX_S, open_edge_ultrasonic   # noqa: E402


def burn():
    while True:
        pass


def pct(xs, scale=1e6):
    xs = sorted(xs)
    if not xs:
        return "-"
    return " ".join(f"p{p}={xs[min(len(xs) - 1, int(p / 100 * (len(xs) - 1) + 0.5))] * scale:.0f}"
                    for p in (0, 50, 90, 99, 100))


def part_a(seconds: float, load: int, switch_ms: float) -> None:
    procs = [multiprocessing.Process(target=burn, daemon=True) for _ in range(load)]
    for p in procs:
        p.start()
    sys.setswitchinterval(switch_ms / 1000.0)
    us = open_edge_ultrasonic(SystemClock())
    results = collections.Counter()
    readings = []
    trig_all = []
    c0 = resource.getrusage(resource.RUSAGE_SELF)
    t_end = time.monotonic() + seconds
    samples = 0
    try:
        while time.monotonic() < t_end:
            t0 = time.monotonic()
            v = us.measure(3)
            samples += 1
            trig_all += list(us.trigger_s)
            us.trigger_s.clear()
            if v is BUSY:
                results["busy"] += 1
            elif v is None:
                results["none"] += 1
            elif v >= FAR:
                results["far"] += 1
            else:
                results["ok"] += 1
                readings.append(v)
            time.sleep(max(0.0, 0.04 - (time.monotonic() - t0)))
    finally:
        us.close()
    c1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (c1.ru_utime + c1.ru_stime) - (c0.ru_utime + c0.ru_stime)
    print(f"A load={load} switch={switch_ms}ms: {samples} samples, {len(trig_all)} triggers, CPU {cpu / seconds * 100:.1f} %")
    print(f"  trigger fall call us {pct(trig_all)}  over the {TRIGGER_MAX_S * 1e6:.0f} us limit: "
          f"{sum(b > TRIGGER_MAX_S for b in trig_all)} of {len(trig_all)}")
    print(f"  results {dict(results)}  readings cm {pct(readings, 1)}")
    print(f"  discards {dict(us.discards)}  diag {dict(us.diag)}  second pulses ms {pct(list(us.second_widths), 1e3)}")


def part_b(tries: int) -> None:
    path, off = cd.find_line("GPIO5")
    line = cd.EdgeLine(off, chip=path, consumer="edgeping")
    base = cd.FLAG_INPUT | cd.FLAG_EDGE_RISING | cd.FLAG_EDGE_FALLING
    up, down = cd.line_config(base | (1 << 8)), cd.line_config(base | (1 << 9))
    shapes = collections.Counter()
    delays = []
    try:
        fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, down)
        time.sleep(0.05)
        line.events()
        for _ in range(tries):
            if line.get():
                time.sleep(0.5)                       # the module is answering something: wait
                line.events()
                continue
            t0 = time.monotonic()
            fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, up)
            time.sleep(0.00002)
            t1 = time.monotonic()
            fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, down)
            evs = []
            end = time.monotonic() + 0.06
            while time.monotonic() < end:
                if line.wait(end - time.monotonic()):
                    evs += line.events()
            shape = "".join("R" if e.rising else "F" for e in evs)
            shapes[shape[:8]] += 1
            later = [e for e in evs if e.t > t1 + 0.0001]
            if later and later[0].rising:
                delays.append(later[0].t - t1)
            if line.get():
                time.sleep(0.45)
                line.events()
            time.sleep(0.03)
    finally:
        line.close()
    print(f"B {tries} pull-up triggers: event shapes {dict(shapes)}")
    print(f"  a rise after the pull-up ended (us): {pct(delays)}  answered {len(delays)}/{tries}")


if __name__ == "__main__":
    if sys.argv[1] == "a":
        part_a(float(sys.argv[2]) if len(sys.argv) > 2 else 60, int(sys.argv[3]) if len(sys.argv) > 3 else 0,
               float(sys.argv[4]) if len(sys.argv) > 4 else 0.5)
    else:
        part_b(int(sys.argv[2]) if len(sys.argv) > 2 else 100)
