"""PCA9685 PWM driver (I2C bus 1, 0x40, 60 Hz) as measured on the car; sequence taken from legacy/obstacle_stop.py."""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

from .base import HardwareFault  # noqa: F401 - re-exported

ADDR = 0x40
FREQ_HZ = 60
MODE1, MODE2, PRESCALE, LED0_ON_L, ALL_LED_ON_L = 0x00, 0x01, 0xFE, 0x06, 0xFA

SERVO_CH, MOTOR_RIGHT_CH, MOTOR_LEFT_CH = 0, 4, 5


def prescale_for(freq_hz: float) -> int:
    return int(math.floor(25_000_000.0 / 4096.0 / freq_hz - 1.0 + 0.5))   # 60 Hz -> 101


def servo_ticks(angle_deg: float) -> int:
    """SunFounder servo map: 0..180 deg -> 600..2400 us at 60 Hz, 4096 steps per period (90 deg -> 369)."""
    return int(round((600 + 1800 * angle_deg / 180) / 1e6 * FREQ_HZ * 4096))


class PCA9685:
    """Thin register layer. `lock` is the backend-wide I2C lock shared with the sensor code."""

    def __init__(self, bus, lock, addr: int = ADDR, sleep: Callable[[float], None] = time.sleep):
        self.bus, self.lock, self.addr, self._sleep = bus, lock, addr, sleep

    def _w(self, reg: int, val: int) -> None:
        self.bus.write_byte_data(self.addr, reg, val)

    def setup(self, freq_hz: float = FREQ_HZ) -> None:
        with self.lock:
            for i in range(4):                       # ALL_LED_ON/OFF = 0: every channel off
                self._w(ALL_LED_ON_L + i, 0)
            self._w(MODE2, 0x04)                     # totem-pole outputs
            self._w(MODE1, 0x01)                     # ALLCALL, awake
            self._sleep(0.005)
            old = self.bus.read_byte_data(self.addr, MODE1)
            self._w(MODE1, (old & 0x7F) | 0x10)      # sleep to change the prescale
            self._w(PRESCALE, prescale_for(freq_hz))
            self._w(MODE1, old)
            self._sleep(0.005)
            self._w(MODE1, old | 0x80)               # restart

    def set_pwm(self, ch: int, off: int, timeout: Optional[float] = None) -> None:
        """Channel on at count 0, off at `off` (0..4095). With `timeout`, a busy bus lock raises OSError."""
        off = max(0, min(4095, int(off)))
        base = LED0_ON_L + 4 * ch
        if not self.lock.acquire(timeout=-1 if timeout is None else timeout):
            raise OSError("I2C bus lock timeout")
        try:
            for i, v in enumerate((0, 0, off & 0xFF, off >> 8)):
                self._w(base + i, v)
        finally:
            self.lock.release()

    def set_pwm_retry(self, ch: int, off: int) -> None:
        """One retry on an I2C error, then HardwareFault."""
        try:
            self.set_pwm(ch, off)
        except OSError:
            try:
                self.set_pwm(ch, off)
            except OSError as e:
                raise HardwareFault(f"PCA9685 ch{ch} write failed twice: {e}") from e
