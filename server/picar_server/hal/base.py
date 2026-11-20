"""Backend interface shared by the real car, the dry-run car and the simulator (docs/contracts.md §2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

DIST_OK = "ok"
DIST_FAR = "far"
DIST_FAULT = "fault"

DUTY_MAX = 4095
STEER_MAX_DEG = 45.0


@dataclass(frozen=True)
class Sample:
    """One sensor reading. Produced by the backend, consumed by the controller's obstacle guard."""

    t: float                              # time.monotonic() when the reading was taken
    dist_cm: Optional[float]              # None when far or fault
    dist_state: str                       # DIST_OK | DIST_FAR | DIST_FAULT
    line_raw: Optional[list[int]] = None  # 5 values, sensor 1 = leftmost; None when the module is absent
    light_raw: Optional[list[int]] = None  # 3 values 0..255 (left, middle, right); None when absent


OnSample = Callable[[Sample], None]


class Backend(Protocol):
    """Hardware (or simulated hardware) behind one car controller.

    Threading: `stop_motors` must be safe to call from any thread at any time and must never raise.
    Real/dry-run backends call `on_sample` from their sensor thread; the simulator calls it from the
    asyncio loop.
    """

    name: str  # "real" | "dryrun" | "sim"

    def capabilities(self) -> dict[str, bool]:
        """{"ultrasonic": bool, "light": bool, "line": bool}; "line" may change at runtime."""
        ...

    def start(self, on_sample: OnSample) -> None:
        """Begin sampling (and, for the simulator, physics)."""
        ...

    def set_motors(self, left: int, right: int) -> None:
        """Signed duty -4095..4095 per rear motor; the sign selects the direction."""
        ...

    def set_steering(self, angle_deg: float) -> None:
        """-45..45 degrees from straight, negative = left. Calibration trim is applied inside."""
        ...

    def stop_motors(self) -> None:
        """Zero both motors. Idempotent, thread-safe, never raises."""
        ...

    def pose(self) -> Optional[tuple[float, float, float]]:
        """Simulator: true pose (x_cm, y_cm, scratch_dir_deg). Real hardware: None."""
        ...

    def close(self) -> None:
        """Stop the motors and all backend threads/tasks."""
        ...
