"""Dry-run backend: real sensors, motor and servo commands are only logged."""
from __future__ import annotations

import logging
from typing import Optional

from .pca9685 import MOTOR_LEFT_CH, MOTOR_RIGHT_CH
from .real import _HardwareBase

log = logging.getLogger("picar.dryrun")


class DryRunBackend(_HardwareBase):
    name = "dryrun"

    def __init__(self, calibration: Optional[dict] = None, *, bus=None, gpio=None, clock=None):
        super().__init__(calibration, bus, gpio, clock)
        # The only hardware write: zero the rear motors once (no chip setup, servo untouched).
        for ch in (MOTOR_RIGHT_CH, MOTOR_LEFT_CH):
            try:
                self.pca.set_pwm(ch, 0)
            except Exception as e:                     # noqa: BLE001
                log.warning("dry run: could not zero ch%d: %s", ch, e)

    def _write_motors(self, left: int, right: int) -> None:
        if (left, right) != self._motion:
            log.info("dry run: set_motors left=%d right=%d", left, right)

    def _zero_channels(self) -> bool:
        if self._motion != (0, 0):
            log.info("dry run: stop_motors")
        return True

    def set_steering(self, angle_deg: float) -> None:
        log.info("dry run: set_steering %.1f deg (servo angle %.1f)", angle_deg, self._servo_angle(angle_deg))

    def set_trim(self, trim_deg: float) -> None:
        self.cal["steer_trim_deg"] = float(trim_deg)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._join_thread()
        try:
            self.bus.close()
        except Exception:                              # noqa: BLE001
            pass
