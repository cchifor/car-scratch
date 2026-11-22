"""The GPIO character-device wrapper: uAPI v2 layouts and event parsing (no kernel needed)."""
import struct

import pytest

from picar_server.hal import gpio_cdev as cd


def test_ioctl_numbers_and_struct_sizes_match_the_kernel_abi():
    # include/uapi/linux/gpio.h: _IOWR(0xB4, nr, struct ...), sizes on every 64-bit and 32-bit ABI alike
    assert cd.GET_LINE_IOCTL == 0xC250B407
    assert cd.SET_CONFIG_IOCTL == 0xC110B40D
    assert cd.GET_VALUES_IOCTL == 0xC010B40E
    assert cd.SET_VALUES_IOCTL == 0xC010B40F
    assert (cd.CONFIG_SIZE, cd.REQUEST_SIZE, cd.EVENT.size, cd.VALUES.size) == (272, 592, 48, 16)


def test_line_config_without_attributes():
    b = cd.line_config(cd.FLAG_INPUT | cd.FLAG_EDGE_RISING | cd.FLAG_EDGE_FALLING)
    assert len(b) == 272
    flags, num_attrs = struct.unpack_from("<QI", b, 0)
    assert flags == 0b110100 and num_attrs == 0
    assert b[12:] == bytes(260)


def test_line_config_with_an_output_value():
    b = cd.line_config(cd.FLAG_OUTPUT, output_value=0)
    flags, num_attrs = struct.unpack_from("<QI", b, 0)
    assert flags == cd.FLAG_OUTPUT and num_attrs == 1
    attr_id, pad, values, mask = struct.unpack_from("<IIQQ", b, 32)
    assert (attr_id, pad, values, mask) == (cd.ATTR_ID_OUTPUT_VALUES, 0, 0, 1)
    assert struct.unpack_from("<IIQQ", cd.line_config(cd.FLAG_OUTPUT, output_value=1), 32)[2] == 1


def test_line_request_layout():
    cfg = cd.line_config(cd.FLAG_INPUT)
    b = cd.line_request(5, "car-scratch", cfg, 64)
    assert len(b) == 592
    assert struct.unpack_from("<I", b, 0)[0] == 5
    assert b[256:256 + 11] == b"car-scratch" and b[256 + 11] == 0
    assert bytes(b[288:288 + 272]) == cfg
    assert struct.unpack_from("<II", b, 560) == (1, 64)
    assert struct.unpack_from("<i", b, 588)[0] == 0


def test_consumer_name_is_truncated_and_terminated():
    b = cd.line_request(5, "x" * 40, cd.line_config(cd.FLAG_INPUT), 64)
    assert b[256:256 + 31] == b"x" * 31 and b[256 + 31] == 0


def _event(ts_ns, eid, line_seqno, offset=5, seqno=0):
    return struct.pack("<QIIII24x", ts_ns, eid, offset, seqno, line_seqno)


def test_parse_events():
    data = _event(1_000_000_123, 1, 7) + _event(1_003_000_000, 2, 8)
    assert cd.parse_events(data) == [cd.Edge(True, 1.000000123, 7), cd.Edge(False, 1.003, 8)]


def test_parse_events_ignores_a_partial_record():
    assert cd.parse_events(_event(5, 1, 1) + b"\0" * 10) == [cd.Edge(True, 5e-9, 1)]


def test_event_reading_from_a_nonblocking_fd(monkeypatch):
    import os
    r, w = os.pipe()
    line = cd.EdgeLine.__new__(cd.EdgeLine)
    line.fd = r
    os.set_blocking(r, False)
    import select
    line._poll = select.poll()
    line._poll.register(r, select.POLLIN)
    assert line.events() == [] and not line.wait(0)
    os.write(w, b"".join(_event(i * 1000, 1 + i % 2, i) for i in range(20)))   # more than one read chunk
    assert line.wait(0)
    ev = line.events()
    assert [e.seqno for e in ev] == list(range(20)) and ev[1].rising is False
    os.close(w)
    os.close(r)


def test_an_unavailable_chip_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        cd.EdgeLine(5, chip=str(tmp_path / "no-gpiochip"))
