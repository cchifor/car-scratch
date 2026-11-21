"""Shared fixtures for the server-core tests (Track A)."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest

from picar_server import config
from picar_server.controller import Controller
from picar_server.hal.base import DIST_OK, HardwareFault, Sample


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeBackend:
    """Backend test double implementing the contracts v1.2 interface with a motor lock model."""
    name = "dryrun"

    def __init__(self):
        self.duty = (0, 0)
        self.inhibit = False
        self.stop_latched = False
        self.sets = []
        self.fail_sets = 0
        self.stop_result = True
        self.steering = []
        self.cb = None
        self.latch_cb = None
        self.probe = None
        self.closed = False

    def capabilities(self):
        return {"ultrasonic": True, "light": True, "line": False}

    def start(self, on_sample):
        self.cb = on_sample

    def set_motors(self, left, right):
        if self.fail_sets > 0:
            self.fail_sets -= 1
            raise HardwareFault("i2c")
        if self.stop_latched:
            self.duty = (0, 0)
            return False
        if self.inhibit and (left > 0 or right > 0):
            self.duty = (0, 0)
            return False
        self.sets.append((left, right))
        self.duty = (left, right)
        return True

    def set_steering(self, angle):
        self.steering.append(angle)

    def stop_motors(self):
        self.duty = (0, 0)
        return self.stop_result

    def inhibit_forward(self):
        self.inhibit = True
        self.duty = (0, 0)

    def clear_inhibit(self):
        self.inhibit = False

    def latch_stop(self):
        self.stop_latched = True
        self.duty = (0, 0)
        return True

    def release_stop(self):
        self.stop_latched = False

    def motion(self):
        return self.duty

    def set_near_hint(self, cm):
        pass

    def pose(self):
        return None

    def close(self):
        self.closed = True

    def set_deadline_probe(self, fn):
        self.probe = fn

    def set_latch_callback(self, cb):
        self.latch_cb = cb


class Rig:
    """A Controller on a fake clock + fake backend, recording events and done messages."""

    def __init__(self, *, hold=False, attached=True, shared=True, backend=None):
        self.clock = FakeClock()
        self.backend = backend or FakeBackend()
        self.events, self.dones = [], []
        self.cal = config.merge(None)
        self.ctl = Controller(self.backend, self.cal, shared=shared, clock=self.clock, start_on_hold=hold,
                              on_event=lambda k, f: self.events.append((k, f)),
                              on_done=lambda o, i, r: self.dones.append((i, r)))
        self.ctl.start(background=False)
        self.ctl.attached = attached
        self.ctl.touch()

    def sample(self, cm=100.0, state=DIST_OK):
        self.ctl._on_sample(Sample(self.clock(), cm if state == DIST_OK else None, state, None, None))

    def run(self, secs, cm=100.0, state=DIST_OK, step=0.04, touch=True):
        """Advance the clock, feeding samples and controller frames, ticking the loop side."""
        n = max(1, round(secs / step))
        for _ in range(n):
            self.clock.advance(step)
            if touch:
                self.ctl.touch()
            if state is not None:
                self.sample(cm, state)
            self.ctl.tick()

    def cmd(self, op, **f):
        return self.ctl.command(op, f, owner="me", cmd_id=f.pop("_id", 1))

    def kinds(self):
        return [k for k, _ in self.events]


@pytest.fixture
def rig():
    r = Rig()
    r.run(0.2)   # first valid sample
    return r


def make_sb3(extra: dict | None = None, project=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if project:
            z.writestr("project.json", json.dumps({"targets": []}))
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()
