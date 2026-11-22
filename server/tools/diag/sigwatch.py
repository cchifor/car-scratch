"""Passive observer of the ultrasonic SIG line (GPIO5): reads the GPIO level register through /dev/gpiomem
(read-only mapping, never claims or drives the pin) and records every high pulse while the car server keeps
running. Trigger pulses are ~10 us; echo pulses are what the sensor answers."""
import mmap, os, struct, sys, time

PIN = 5
SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 10
GPLEV0 = 0x34

fd = os.open("/dev/gpiomem", os.O_RDONLY | os.O_SYNC)
m = mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ)
unpack = struct.unpack_from
mask = 1 << PIN
now = time.perf_counter_ns

pulses = []          # (start_ns, width_ns)
max_gap = 0
t_end = now() + int(SECS * 1e9)
prev_t = now()
level = unpack("<I", m, GPLEV0)[0] & mask
rise = None
while True:
    t = now()
    if t > t_end:
        break
    g = t - prev_t
    if g > max_gap:
        max_gap = g
    prev_t = t
    lv = unpack("<I", m, GPLEV0)[0] & mask
    if lv != level:
        if lv:
            rise = t
        elif rise is not None:
            pulses.append((rise, t - rise))
        level = lv

trig = [p for p in pulses if p[1] < 200_000]          # < 0.2 ms: trigger (or a glitch)
echo = [p for p in pulses if p[1] >= 200_000]
buckets = [(0.2, 1), (1, 6), (6, 7.5), (7.5, 12), (12, 20), (20, 30), (30, 40), (40, 100), (100, 1e9)]
print("observed %.0f s: %d pulses, %d short (trigger), %d echo; observer max loop gap %.2f ms"
      % (SECS, len(pulses), len(trig), len(echo), max_gap / 1e6))
for lo, hi in buckets:
    n = sum(1 for _, w in echo if lo <= w / 1e6 < hi)
    print("  echo %6.1f-%-6s ms: %4d" % (lo, ("%.0f" % hi) if hi < 1e9 else "inf", n))
# triggers that got no echo before the next trigger
starts = sorted(pulses)
no_echo = 0
delays = []
for i, (s, w) in enumerate(starts):
    if w >= 200_000:
        continue
    nxt = starts[i + 1] if i + 1 < len(starts) else None
    if nxt is None or nxt[1] < 200_000:
        no_echo += 1
    else:
        delays.append((nxt[0] - (s + w)) / 1e3)
print("triggers without an echo before the next trigger: %d" % no_echo)
if delays:
    delays.sort()
    print("trigger->echo rise us: min %.0f median %.0f max %.0f" % (delays[0], delays[len(delays) // 2], delays[-1]))
print("echo widths in order (ms):")
print(" ".join("%.1f" % (w / 1e6) for _, w in echo[:200]))
