"""Standalone motor zeroing for systemd (ExecStartPre / ExecStopPost): `python3 -m picar_server.safe_stop`.

Imports only smbus2 and the stdlib. Zeroes PCA9685 ch4/ch5 (rear motors), leaves the servo alone, and always
exits 0: a missing chip or bus prints a one-line warning.
"""
from __future__ import annotations

import sys

ADDR = 0x40
LED0_ON_L = 0x06
MOTOR_CHANNELS = (4, 5)


def zero_motors(bus) -> None:
    """Both channels are attempted before a failure is raised."""
    err = None
    for ch in MOTOR_CHANNELS:
        try:
            for i in range(4):                       # ON_L, ON_H, OFF_L, OFF_H = 0
                bus.write_byte_data(ADDR, LED0_ON_L + 4 * ch + i, 0)
        except OSError as e:
            err = err or e
    if err:
        raise err


def main(bus_factory=None) -> int:
    try:
        if bus_factory is None:
            import smbus2
            bus_factory = lambda: smbus2.SMBus(1)
        bus = bus_factory()
        try:
            try:
                zero_motors(bus)
            except OSError:
                zero_motors(bus)                      # one retry
        finally:
            close = getattr(bus, "close", None)
            if close:
                close()
        print("safe_stop: motors zeroed")
    except Exception as e:                            # noqa: BLE001 - must always exit 0
        print(f"safe_stop: warning, could not zero motors ({type(e).__name__}: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
