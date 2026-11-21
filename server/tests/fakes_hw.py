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
        delay, width = self._active
        t = self.clock.now - self._t_in
        return 1 if delay <= t < delay + width else 0

    def writes_to(self, pin):
        return [v for p, v in self.outputs if p == pin]
