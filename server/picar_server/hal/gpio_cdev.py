"""Linux GPIO character device (uAPI v2, kernel >= 5.10): one line with kernel-timestamped edges.

Just what the ultrasonic needs: switch the line between output-low, input, and input with both edges; set and read
its value; wait for and read edge events. Pure Python (fcntl, os, select, struct), no extra package.

Every call is one ioctl on a buffer built once. Arming edge detection right after the trigger races the echo's
rise (~383 us later): measured on the car (2026-11-22) a raw SET_CONFIG took 133 us median (p99 261 us), the same
through python3-libgpiod 203 us (p99 368 us).

Event timestamps use the kernel's default event clock, CLOCK_MONOTONIC: the clock behind time.monotonic() on Linux.
"""
from __future__ import annotations

import fcntl
import os
import select
import struct
from typing import NamedTuple, Optional

LINES_MAX = 64
NUM_ATTRS_MAX = 10
NAME_SIZE = 32

FLAG_INPUT = 1 << 2
FLAG_OUTPUT = 1 << 3
FLAG_EDGE_RISING = 1 << 4
FLAG_EDGE_FALLING = 1 << 5
ATTR_ID_OUTPUT_VALUES = 2
EVENT_RISING_EDGE = 1
EVENT_FALLING_EDGE = 2

_CFG_HEAD = struct.Struct("<QI20x")       # flags, num_attrs, padding[5]
_CFG_ATTR = struct.Struct("<IIQQ")        # attr {id, padding, union u64}, mask
CONFIG_SIZE = _CFG_HEAD.size + NUM_ATTRS_MAX * _CFG_ATTR.size                        # struct gpio_v2_line_config
REQUEST_SIZE = LINES_MAX * 4 + NAME_SIZE + CONFIG_SIZE + 4 + 4 + 5 * 4 + 4           # struct gpio_v2_line_request
_REQ_CONFIG_AT = LINES_MAX * 4 + NAME_SIZE
_REQ_COUNTS_AT = _REQ_CONFIG_AT + CONFIG_SIZE                                         # num_lines, event_buffer_size
_REQ_FD_AT = REQUEST_SIZE - 4
EVENT = struct.Struct("<QIIII24x")        # struct gpio_v2_line_event: timestamp_ns, id, offset, seqno, line_seqno
VALUES = struct.Struct("<QQ")             # struct gpio_v2_line_values: bits, mask


def _iowr(nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (0xB4 << 8) | nr


GET_LINE_IOCTL = _iowr(0x07, REQUEST_SIZE)
SET_CONFIG_IOCTL = _iowr(0x0D, CONFIG_SIZE)
GET_VALUES_IOCTL = _iowr(0x0E, VALUES.size)
SET_VALUES_IOCTL = _iowr(0x0F, VALUES.size)

# The uAPI is a fixed ABI; a mistake here would corrupt the kernel's view of the request, so fail at import.
assert (CONFIG_SIZE, REQUEST_SIZE, EVENT.size, VALUES.size) == (272, 592, 48, 16)
assert (GET_LINE_IOCTL, SET_CONFIG_IOCTL, GET_VALUES_IOCTL, SET_VALUES_IOCTL) == \
    (0xC250B407, 0xC110B40D, 0xC010B40E, 0xC010B40F)


class Edge(NamedTuple):
    rising: bool      # the kernel's reading of the level when it handled the edge (see EdgeLine.events)
    t: float          # CLOCK_MONOTONIC seconds, taken in the interrupt handler
    seqno: int        # line_seqno: consecutive per line; a gap means the kernel dropped events


def line_config(flags: int, output_value: Optional[int] = None) -> bytes:
    """A gpio_v2_line_config for a one-line request, optionally with its output value."""
    attrs = b""
    if output_value is not None:
        attrs = _CFG_ATTR.pack(ATTR_ID_OUTPUT_VALUES, 0, 1 if output_value else 0, 1)
    head = _CFG_HEAD.pack(flags, 1 if attrs else 0)
    return head + attrs.ljust(NUM_ATTRS_MAX * _CFG_ATTR.size, b"\0")


def line_request(offset: int, consumer: str, config: bytes, event_buffer_size: int) -> bytearray:
    """A gpio_v2_line_request for one line."""
    buf = bytearray(REQUEST_SIZE)
    struct.pack_into("<I", buf, 0, offset)
    name = consumer.encode()[:NAME_SIZE - 1]
    buf[LINES_MAX * 4:LINES_MAX * 4 + len(name)] = name
    buf[_REQ_CONFIG_AT:_REQ_CONFIG_AT + CONFIG_SIZE] = config
    struct.pack_into("<II", buf, _REQ_COUNTS_AT, 1, event_buffer_size)
    return buf


def parse_events(data: bytes) -> list[Edge]:
    n = len(data) - len(data) % EVENT.size
    out = []
    for i in range(0, n, EVENT.size):
        ts, eid, _offset, _seqno, line_seqno = EVENT.unpack_from(data, i)
        out.append(Edge(eid == EVENT_RISING_EDGE, ts / 1e9, line_seqno))
    return out


class EdgeLine:
    """One requested GPIO line. Raises OSError when the line can't be requested (absent chip, line held by another
    process, no permission): the caller falls back to polling."""

    READ_CHUNK = 16

    def __init__(self, offset: int, chip: str = "/dev/gpiochip0", consumer: str = "car-scratch",
                 event_buffer_size: int = 64):
        self._input = line_config(FLAG_INPUT)
        self._armed = line_config(FLAG_INPUT | FLAG_EDGE_RISING | FLAG_EDGE_FALLING)
        self._output_low = line_config(FLAG_OUTPUT, output_value=0)
        self._high, self._low, self._read = VALUES.pack(1, 1), VALUES.pack(0, 1), VALUES.pack(0, 1)
        chip_fd = os.open(chip, os.O_RDWR | os.O_CLOEXEC)
        try:
            req = line_request(offset, consumer, self._input, event_buffer_size)
            fcntl.ioctl(chip_fd, GET_LINE_IOCTL, req, True)
        finally:
            os.close(chip_fd)
        self.fd: Optional[int] = struct.unpack_from("<i", req, _REQ_FD_AT)[0]
        os.set_blocking(self.fd, False)
        self._poll = select.poll()
        self._poll.register(self.fd, select.POLLIN)

    def output_low(self) -> None:
        """Drive the line low (edge detection off)."""
        fcntl.ioctl(self.fd, SET_CONFIG_IOCTL, self._output_low)

    def arm(self) -> None:
        """Input with both edges, timestamped by the kernel."""
        fcntl.ioctl(self.fd, SET_CONFIG_IOCTL, self._armed)

    def input(self) -> None:
        """Input, edge detection off."""
        fcntl.ioctl(self.fd, SET_CONFIG_IOCTL, self._input)

    def set(self, value: int) -> None:
        fcntl.ioctl(self.fd, SET_VALUES_IOCTL, self._high if value else self._low)

    def get(self) -> int:
        buf = bytearray(self._read)
        fcntl.ioctl(self.fd, GET_VALUES_IOCTL, buf, True)
        return VALUES.unpack(buf)[0] & 1

    def wait(self, timeout_s: float) -> bool:
        """True when events are queued; waits at most timeout_s (never busy)."""
        return bool(self._poll.poll(max(0.0, timeout_s) * 1000.0))

    def events(self) -> list[Edge]:
        """Every queued event, oldest first. For both edges the kernel derives `rising` from the level it reads in
        its interrupt thread, so a pulse shorter than that thread's latency can show as two events of one kind;
        the timestamps are still the edges' own."""
        out: list[Edge] = []
        while True:
            try:
                data = os.read(self.fd, EVENT.size * self.READ_CHUNK)
            except BlockingIOError:
                break
            out += parse_events(data)
            if len(data) < EVENT.size * self.READ_CHUNK:
                break
        return out

    def close(self) -> None:
        """Leave the line an input (never driven), then release it."""
        if self.fd is None:
            return
        try:
            self.input()
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None
