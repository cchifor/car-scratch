"""M8 spike 3: kernel edge timestamps against known edge times, on an UNUSED input pin (GPIO6), by switching its
internal pull resistor (never driven). Runs beside the server.  python3 tools/diag/irqlat.py [n] [load_procs]

A: one edge per SET_CONFIG: latency bound = event t - ioctl start (the edge is made inside the ioctl).
B: low pulses of growing width (pull-down then pull-up): are both edges reported, with the right kinds, and how
   does the measured width compare with the userspace bracket?
"""
import multiprocessing
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from picar_server.hal import gpio_cdev as cd  # noqa: E402

PIN = 6
N = int(sys.argv[1]) if len(sys.argv) > 1 else 400
LOAD = int(sys.argv[2]) if len(sys.argv) > 2 else 0
UP, DOWN = 1 << 8, 1 << 9
EDGES = cd.FLAG_INPUT | cd.FLAG_EDGE_RISING | cd.FLAG_EDGE_FALLING
mono = time.monotonic


def pct(xs):
    xs = sorted(xs)
    if not xs:
        return {}
    return {p: round(xs[min(len(xs) - 1, int(p / 100 * (len(xs) - 1) + 0.5))] * 1e6, 1) for p in (0, 50, 90, 99, 100)}


def burn():
    while True:
        pass


def collect(line, until):
    ev = []
    while True:
        left = until - mono()
        if left <= 0:
            break
        if line.wait(left):
            ev += line.events()
    return ev + line.events()


def main():
    procs = [multiprocessing.Process(target=burn, daemon=True) for _ in range(LOAD)]
    for p in procs:
        p.start()
    line = cd.EdgeLine(PIN, consumer="irqlat")
    cfg_up, cfg_down = cd.line_config(EDGES | UP), cd.line_config(EDGES | DOWN)
    import fcntl
    fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, cd.line_config(cd.FLAG_INPUT | UP))
    time.sleep(0.01)
    fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, cfg_up)
    time.sleep(0.01)
    line.events()
    lat_f, lat_r, after_f, kinds_bad, seq = [], [], [], 0, []
    for _ in range(N):                                   # A: single edges
        for cfg, rising, store in ((cfg_down, False, lat_f), (cfg_up, True, lat_r)):
            t0 = mono()
            fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, cfg)
            t1 = mono()
            ev = collect(line, t1 + 0.003)
            seq += [e.seqno for e in ev]
            if len(ev) != 1 or ev[0].rising != rising:
                kinds_bad += 1
                continue
            store.append(ev[0].t - t0)
            if not rising:
                after_f.append(ev[0].t - t1)
    gaps = sum(1 for a, b in zip(seq, seq[1:]) if b != a + 1)
    print(f"A load={LOAD}: fall t-ioctl_start us {pct(lat_f)}")
    print(f"           rise t-ioctl_start us {pct(lat_r)}")
    print(f"           fall t-ioctl_end us   {pct(after_f)}  bad={kinds_bad}/{2 * N} seqgaps={gaps}")
    for d in (0.0, 0.00002, 0.00005, 0.0001, 0.0002, 0.0005):   # B: low pulses
        ok, shapes, err = 0, {}, []
        for _ in range(N // 2):
            t0 = mono()
            fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, cfg_down)
            t1 = mono()
            end = t1 + d
            while mono() < end:
                pass
            t2 = mono()
            fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, cfg_up)
            t3 = mono()
            ev = collect(line, t3 + 0.003)
            shape = "".join("R" if e.rising else "F" for e in ev)
            shapes[shape] = shapes.get(shape, 0) + 1
            if shape == "FR":
                ok += 1
                w = ev[1].t - ev[0].t
                # the physical low lasted between (t2 - t1) and (t3 - t0): errors outside the bracket
                err.append(max(0.0, w - (t3 - t0)) - max(0.0, (t2 - t1) - w))
            time.sleep(0.002)
        print(f"B pulse>={d * 1e6:5.0f}us: shapes {shapes} width outside bracket us {pct(err)}")
    fcntl.ioctl(line.fd, cd.SET_CONFIG_IOCTL, cd.line_config(cd.FLAG_INPUT | UP))
    line.close()


if __name__ == "__main__":
    main()
