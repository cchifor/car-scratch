"""Fake I2C bus, GPIO and clock for the hardware-layer tests (no real smbus2 / RPi.GPIO needed)."""
from __future__ import annotations

from collections import deque

PCA = 0x40
LED0_ON_L = 0x06


def ch_regs(ch: int) -> list[int]:
    """The four registers (ON_L, ON_H, OFF_L, OFF_H) of a PCA9685 channel."""
    return [LED0_ON_L + 4 * ch + i for i in range(4)]


def echo_for_cm(cm: float) -> float:
    """Echo pulse width in seconds for a distance."""
    return cm * 2 / 34300


class FakeClock:
    """Time only moves when someone looks at it (monotonic ticks) or sleeps, so busy-waits finish instantly."""

    def __init__(self, tick: float = 0.00002):
        self.now, self.tick = 100.0, tick
        self._gap_in, self._gap_s = None, 0.0

    def gap_after(self, calls: int, seconds: float) -> None:
        """The monotonic() call number `calls` from now jumps ahead by `seconds` (a scheduler/GIL stall)."""
        self._gap_in, self._gap_s = calls, seconds

    def monotonic(self) -> float:
        self.now += self.tick
        if self._gap_in is not None:
            self._gap_in -= 1
            if self._gap_in <= 0:
                self.now += self._gap_s
                self._gap_in = None
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s

    def advance(self, s: float) -> None:
        self.now += s


class FakeBus:
    """Records every register write; can simulate absent devices and I2C errors."""

    def __init__(self):
        self.writes: list[tuple[int, int, int]] = []   # (addr, reg, val) for write_byte_data
        self.ops: list[tuple] = []                      # every op, for sequence assertions
        self.regs: dict[tuple[int, int], int] = {}
        self.missing: set[int] = set()                  # addresses that answer with OSError
        self.fail_writes = 0                            # next N write_byte_data calls raise OSError
        self.fail_all_writes = False
        self.pcf_values = [10, 20, 30, 40]
        self._pcf_ch = 0
        self._pcf_fresh = False
        self.line_bytes: list[int] | None = None        # None = absent (OSError)
        self.line_script: deque = deque()               # per read: a byte list, or None = OSError (before line_bytes)
        self.on_line_read = None                        # optional callback() at the start of each line block read
        self.closed = False
        self.on_write = None                            # optional callback(addr, reg, val) before each write

    def _check(self, addr: int) -> None:
        if addr in self.missing:
            raise OSError(121, "Remote I/O error")

    def write_byte_data(self, addr, reg, val):
        self._check(addr)
        if self.fail_all_writes or self.fail_writes > 0:
            self.fail_writes = max(0, self.fail_writes - 1)
            raise OSError(5, "Input/output error")
        if self.on_write:
            self.on_write(addr, reg, val)
        self.writes.append((addr, reg, val))
        self.ops.append(("wbd", addr, reg, val))
        self.regs[(addr, reg)] = val

    def read_byte_data(self, addr, reg):
        self._check(addr)
        self.ops.append(("rbd", addr, reg))
        return self.regs.get((addr, reg), 0)

    def write_byte(self, addr, val):
        self._check(addr)
        self.ops.append(("wb", addr, val))
        self._pcf_ch, self._pcf_fresh = val & 0x03, False

    def read_byte(self, addr):
        self._check(addr)
        self.ops.append(("rb", addr))
        if not self._pcf_fresh:                         # PCF8591 returns the previous conversion first
            self._pcf_fresh = True
            return 0
        return self.pcf_values[self._pcf_ch]

    def read_i2c_block_data(self, addr, reg, length):
        self._check(addr)
        self.ops.append(("rblk", addr, reg, length))
        if self.on_line_read:
            self.on_line_read()
        if self.line_script:
            item = self.line_script.popleft()
            if item is None:
                raise OSError(121, "Remote I/O error")
            return list(item)
        if self.line_bytes is None:
            raise OSError(121, "Remote I/O error")
        return list(self.line_bytes)

    def close(self):
        self.closed = True

    # -- helpers ----------------------------------------------------------------------------------------
    def pwm(self, ch: int) -> int:
        """Last OFF count written to a channel."""
        r = ch_regs(ch)
        return self.regs.get((PCA, r[2]), 0) | (self.regs.get((PCA, r[3]), 0) << 8)

    def pca_writes_to(self, *channels: int) -> list[tuple[int, int, int]]:
        regs = {r for ch in channels for r in ch_regs(ch)}
        return [w for w in self.writes if w[0] == PCA and w[1] in regs]


class FakeGPIO:
    BCM, OUT, IN, HIGH, LOW = "BCM", "OUT", "IN", 1, 0

    def __init__(self, clock: FakeClock, us_pin: int = 5):
        self.clock, self.us_pin = clock, us_pin
        self.outputs: list[tuple[int, int]] = []        # every output() / setup(initial=)
        self.level: dict[int, int] = {}
        self.script: deque = deque()                    # per ping: (delay_s, width_s) or None = no echo
        self.default = None                             # used when the script is empty
        self.pings = 0
        self._active = None
        self._t_in = 0.0
        self._busy_from = 0.0                           # long cycle: the module drives the line high from here ...
        self._busy_until = 0.0                          # ... until here, and ignores triggers until then
        self.contention = 0                             # times the pin was driven low while the module held it high

    def setwarnings(self, flag):
        pass

    def setmode(self, mode):
        pass

    def queue(self, *entries):
        self.script.extend(entries)

    def echo_cm(self, cm: float, delay: float = 0.0005):
        return (delay, echo_for_cm(cm))

    def multi(self, *pulses):
        """Several high pulses after one trigger, each (delay, width) from the trigger."""
        return ("multi", list(pulses))

    def long_cycle(self, width: float = 0.4015, delay: float = 0.0003):
        """The module's long no-echo cycle, measured on the car: from `delay` after the trigger it holds the line
        high for `width` (401.5 ms) and ignores every trigger meanwhile."""
        return ("cycle", delay, width)

    def _busy(self) -> bool:
        """Mid-cycle: the module ignores triggers."""
        return self.clock.now < self._busy_until

    def _driving_high(self) -> bool:
        return self._busy_from <= self.clock.now < self._busy_until

    def setup(self, pin, mode, initial=None):
        if initial is not None:
            self.outputs.append((pin, initial))
            self.level[pin] = initial
            if pin == self.us_pin and initial == 0 and self._driving_high():
                self.contention += 1
        if pin == self.us_pin and mode == self.IN:     # the pin turns into an input: the echo window starts
            self.pings += 1
            if self._busy():                           # the module is mid-cycle: the trigger is ignored
                self._active = None
                return
            entry = self.script.popleft() if self.script else self.default
            self._t_in = self.clock.now
            if entry is not None and entry[0] == "cycle":
                _, delay, width = entry
                self._busy_from = self.clock.now + delay
                self._busy_until = self.clock.now + delay + width
                entry = (delay, width)
            self._active = entry

    def output(self, pin, value):
        self.outputs.append((pin, value))
        self.level[pin] = value
        if pin == self.us_pin and value == 0 and self._driving_high():
            self.contention += 1

    def input(self, pin):
        if pin == self.us_pin and self._busy():
            return 1 if self._driving_high() else 0
        if pin != self.us_pin or self._active is None:
            return 0
        t = self.clock.now - self._t_in
        if self._active[0] == "multi":
            return 1 if any(d <= t < d + w for d, w in self._active[1]) else 0
        delay, width = self._active
        return 1 if delay <= t < delay + width else 0

    def writes_to(self, pin):
        return [v for p, v in self.outputs if p == pin]


class FakeEdgeLine:
    """The kernel side of `gpio_cdev.EdgeLine` for one ultrasonic line, on a FakeClock.

    Script entries are FakeGPIO's, timed from the trigger's falling edge (`set(0)` in output mode): (delay, width),
    multi(...), long_cycle(...) or None. While armed, every edge of the module's output is recorded with its exact
    time, like the kernel's hard-IRQ timestamp, and becomes readable once the clock passes it. Knobs:
      arm_latency  seconds arm() takes (the clock advances by it); a list is used one per arm()
      arm_at       fraction of arm() after which edges are recorded (1.0 = only when it returns)
      irq_thread   the kernel's IRQ-thread latency: an edge's kind is the level read this long after it, and an
                   edge inside a previous edge's thread window is timestamped when the window ends (oneshot IRQ)
      drop_next    the next N recorded events are lost (a line_seqno gap)
      rise_late, fall_late   hard-IRQ entry latency added to rising / falling edges' timestamps
      fail         an OSError raised by the next call of that name ("arm", "get", "events", ...)
    """
    RISE_DELAY = 0.0004                                 # measured 383-417 us after the trigger

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.script: deque = deque()
        self.default = None
        self.arm_latency = 0.00013
        self.arm_at = 1.0
        self.irq_thread = 0.0
        self.drop_next = 0
        self.rise_late = self.fall_late = 0.0
        self.fail: set = set()
        self.mode = "input"                             # input | armed | output
        self.out_value = 0
        self.pings = self.contention = self.arms = 0
        self.closed = False
        self._pulses: list[tuple[float, float]] = []    # absolute (rise, fall) of the module's output
        self._busy_until = 0.0
        self._armed_from: float | None = None
        self._emitted = 0.0                             # edges at or before this time are already recorded
        self._queue: list = []                          # recorded, not yet read: (t, rising, seqno)
        self._seq = 0
        self._last_irq = -1.0

    def queue(self, *entries):
        self.script.extend(entries)

    # -- the module ---------------------------------------------------------------------------------------
    def _module_high(self, t: float) -> bool:
        return any(r <= t < f for r, f in self._pulses)

    def _busy(self) -> bool:
        return self.clock.now < self._busy_until

    def _trigger(self) -> None:
        self.pings += 1
        if self._busy():
            return                                      # mid-cycle: the module ignores the trigger
        entry = self.script.popleft() if self.script else self.default
        t0 = self.clock.now
        if entry is None:
            return
        if entry[0] == "cycle":
            _, delay, width = entry
            self._busy_until = t0 + delay + width
            pulses = [(delay, width)]
        elif entry[0] == "multi":
            pulses = entry[1]
        else:
            pulses = [entry]
        self._pulses = [(t0 + d, t0 + d + w) for d, w in pulses]

    # -- the kernel ---------------------------------------------------------------------------------------
    def _record(self) -> None:
        if self.mode != "armed" or self._armed_from is None:
            return
        now = self.clock.now
        edges = sorted([(r, True) for r, _ in self._pulses] + [(f, False) for _, f in self._pulses])
        for t, kind in edges:
            if t <= self._emitted or t < self._armed_from or t > now:
                continue
            self._emitted = t
            ts = t + (self.rise_late if kind else self.fall_late)
            if self.irq_thread and t < self._last_irq + self.irq_thread:
                ts = self._last_irq + self.irq_thread    # masked until the previous edge's thread finished
            if ts > now:
                self._emitted = t - 1e-12                # not handled yet; look again later
                break
            self._last_irq = ts
            rising = self._module_high(ts + self.irq_thread)
            self._seq += 1
            if self.drop_next:
                self.drop_next -= 1
                continue
            self._queue.append((ts, rising, self._seq))

    def _next_edge_after(self, t: float):
        """When the next not-yet-recorded edge becomes readable (its timestamp, latencies included)."""
        times = [x + (self.rise_late if x == r else self.fall_late)
                 for r, f in self._pulses for x in (r, f) if x > t]
        return min(times) if times else None

    # -- EdgeLine API -------------------------------------------------------------------------------------
    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            self.fail.discard(name)
            raise OSError(5, f"fake {name} failure")

    def output_low(self) -> None:
        self._maybe_fail("output_low")
        if self._module_high(self.clock.now):
            self.contention += 1
        self.mode, self.out_value, self._armed_from = "output", 0, None
        self.clock.advance(0.00004)

    def set(self, value: int) -> None:
        assert self.mode == "output", "set() on an input line"
        if self.out_value and not value:
            self._trigger()
        self.out_value = value

    def arm(self) -> None:
        self._maybe_fail("arm")
        lat = self.arm_latency.pop(0) if isinstance(self.arm_latency, list) else self.arm_latency
        self.arms += 1
        start = self.clock.now
        self.mode = "armed"
        self._armed_from = start + lat * self.arm_at
        self._emitted = max(self._emitted, self._armed_from - 1e-12)
        self.clock.advance(lat)

    def input(self) -> None:
        self.mode, self._armed_from = "input", None

    def get(self) -> int:
        self._maybe_fail("get")
        if self.mode == "output":
            return self.out_value
        return 1 if self._module_high(self.clock.now) else 0

    def wait(self, timeout_s: float) -> bool:
        self._record()
        if self._queue:
            return True
        end = self.clock.now + max(0.0, timeout_s)
        nxt = self._next_edge_after(self._emitted) if self.mode == "armed" else None
        if nxt is not None and self.irq_thread:
            nxt = max(nxt, self._last_irq + self.irq_thread)
        if nxt is not None and nxt <= end:
            self.clock.now = max(self.clock.now, nxt)
            self._record()
            return bool(self._queue) or self.wait(end - self.clock.now)
        self.clock.now = max(self.clock.now, end)
        self._record()
        return bool(self._queue)

    def events(self) -> list:
        from picar_server.hal.gpio_cdev import Edge
        self._record()
        out = [Edge(rising, t, seq) for t, rising, seq in self._queue]
        self._queue.clear()
        return out

    def close(self) -> None:
        self.input()
        self.closed = True
