"""Regressions for the safety review: guard/move races, unconfirmed stops, sensor gaps, session fallbacks,
rate-limit exemptions, name validation and project storage."""
import asyncio
import os
import threading

import pytest

from conftest import FakeBackend, Rig, make_sb3
from picar_server import protocol, safe_stop, safety
from picar_server.controller import CmdError
from picar_server.hal.base import DIST_NONE, HardwareFault
from picar_server.hal.real import RealBackend
from picar_server.hal.sim.world import WorldError, load_world
from picar_server.projects import ProjectStore, StoreError

from fakes_hw import FakeBus, FakeClock, FakeGPIO
from test_server import connect, hello, srv  # noqa: F401  (srv is a fixture)


# ---- guard stop vs. timed moves (finding 1) ----------------------------------------------------------------------------

async def test_stale_guard_result_does_not_cancel_a_newer_move():
    r = Rig()
    r.run(0.2)
    r.cmd("drive", dir="forward", speed=50)
    t = threading.Thread(target=lambda: r.sample(10.0))    # the sensor thread stops the car and defers "obstacle"
    t.start()
    t.join()
    r.cmd("move", dir="backward", speed=30, ms=200, _id=7)  # a queued client frame runs before the deferred drain
    assert r.ctl._move is not None and r.ctl._move.id == 7
    await asyncio.sleep(0.6)
    assert r.backend.duty == (0, 0) and (7, "completed") in r.dones


async def test_guard_stop_between_generation_bump_and_write_keeps_the_reverse_timer():
    """Final review: the sensor thread stops the PREVIOUS forward motion after the new command bumped the
    generation but before its motor write; that stop's deferred result must not finish the new move."""
    class Hooked(FakeBackend):
        before_backward = None

        def set_motors(self, left, right):
            hook, self.before_backward = (self.before_backward, None) if left < 0 else (None, self.before_backward)
            if hook:
                hook()
            return super().set_motors(left, right)

    b = Hooked()
    r = Rig(backend=b)
    r.run(0.2)
    r.cmd("drive", dir="forward", speed=50)
    def sensor_thread():                                   # the sensor callback fires inside the interleaving
        t = threading.Thread(target=lambda: r.sample(10.0))
        t.start()
        t.join()

    b.before_backward = sensor_thread
    r.cmd("move", dir="backward", speed=30, ms=100, _id=9)
    assert r.dones == [] and r.ctl._move is not None and r.ctl._move.id == 9
    assert b.duty[0] < 0
    await asyncio.sleep(0.3)
    assert b.duty == (0, 0), "the 100 ms reverse move must stop on its own timer"
    assert (9, "completed") in r.dones and (9, "obstacle") not in r.dones



async def test_sensor_thread_never_writes_the_generation():
    class Gated(FakeBackend):
        gate = entered = None

        def inhibit_forward(self):
            super().inhibit_forward()
            if self.gate and threading.current_thread() is not threading.main_thread():
                self.entered.set()
                self.gate.wait(2)

    b = Gated()
    r = Rig(backend=b)
    r.run(0.2)
    r.cmd("drive", dir="forward", speed=50)
    b.gate, b.entered = threading.Event(), threading.Event()
    t = threading.Thread(target=lambda: r.sample(10.0))
    t.start()
    b.entered.wait(2)                                      # between inhibit_forward() and the end of the guard
    r.cmd("move", dir="backward", speed=30, ms=200, _id=8)
    b.gate.set()
    t.join()
    r.ctl._deferred.clear()                                # isolate the generation from the stale result
    await asyncio.sleep(0.6)
    assert b.duty == (0, 0), "the move timer must still end the motion"


def test_tick_stops_motors_that_run_under_a_latch():
    r = Rig()
    r.run(0.2)
    r.ctl.hold = True                                      # a latch set while something still drives the motors
    r.backend.duty = (500, 500)
    r.run(0.1)
    assert r.backend.duty == (0, 0)


# ---- unconfirmed stops (finding 2) -------------------------------------------------------------------------------------

def test_failed_estop_stop_is_retried_until_confirmed():
    r = Rig()
    r.run(0.2)
    r.cmd("drive", dir="forward", speed=50)
    orig = r.backend.stop_motors, r.backend.latch_stop
    r.backend.stop_motors = r.backend.latch_stop = lambda: False   # the bus is down: duty unchanged
    r.ctl.estop("viewer")
    assert r.ctl.hw_fault and r.backend.duty != (0, 0)
    r.backend.stop_motors, r.backend.latch_stop = orig             # the bus recovers
    r.run(2.0)
    assert r.backend.duty == (0, 0)


def test_failed_stop_from_the_backend_latches_hw_fault_not_hold():
    r = Rig()
    r.run(0.2)
    r.backend.latch_cb("stop_failed")
    assert r.ctl.hw_fault and not r.ctl.hold
    r.backend.latch_cb("controller_silent")
    assert r.ctl.hold and r.ctl.hold_reason == "watchdog"


def _real():
    clock = FakeClock()
    return RealBackend({}, bus=FakeBus(), gpio=FakeGPIO(clock), clock=clock)


def test_real_set_motors_error_path_keeps_motion_when_zero_is_unconfirmed():
    b = _real()
    b._write_motors = lambda l, r: (_ for _ in ()).throw(OSError("i2c"))
    b._zero_channels = lambda: False
    with pytest.raises(HardwareFault):
        b.set_motors(2000, 2000)
    assert b.motion() != (0, 0)
    b._zero_channels = lambda: True
    assert b.stop_motors() and b.motion() == (0, 0)


def test_real_failed_stop_keeps_motion_and_deadline_reports_stop_failed():
    b = _real()
    b.set_motors(2000, 2000)
    b._zero_channels = lambda: False
    assert b.stop_motors() is False and b.motion() == (2000, 2000)
    reasons = []
    b.set_latch_callback(reasons.append)
    b.set_deadline_probe(lambda: (False, True))
    b._check_deadlines()
    assert reasons == ["stop_failed"]
    b.inhibit_forward()
    assert reasons == ["stop_failed", "stop_failed"]


# ---- command-time obstacle check (finding 3) --------------------------------------------------------------------------

def test_dropped_ping_does_not_hide_a_close_obstacle_at_command_time():
    r = Rig()
    r.run(0.2)
    r.clock.advance(0.04)
    r.ctl.touch()
    r.sample(12.0)
    r.clock.advance(0.04)
    r.ctl.touch()
    r.sample(state=DIST_NONE)
    with pytest.raises(CmdError) as e:
        r.cmd("drive", dir="forward", speed=100)
    assert e.value.code == "obstacle_blocked" and r.backend.duty == (0, 0)


def test_forward_is_refused_when_no_valid_sample_is_recent():
    r = Rig()
    r.run(0.2)
    r.run(1.0, state=DIST_NONE)
    with pytest.raises(CmdError) as e:
        r.cmd("drive", dir="forward", speed=30)
    assert e.value.code == "sensor_fault"


# ---- guard margin (finding 6) and calibrate (finding 7) ---------------------------------------------------------------

def test_effective_speed_is_set_before_the_motors_are_energised():
    seen = []

    class B(FakeBackend):
        def set_motors(self, left, right):
            seen.append(r.ctl._eff)
            return super().set_motors(left, right)

    r = Rig(backend=B())
    r.run(0.2)
    r.cmd("drive", dir="forward", speed=60)
    assert seen == [60]


def test_calibrate_hardware_fault_is_an_error_reply_and_latches():
    class B(FakeBackend):
        def set_trim(self, deg):
            raise HardwareFault("servo")

    r = Rig(backend=B())
    r.run(0.2)
    with pytest.raises(CmdError) as e:
        r.cmd("calibrate", steer_trim_deg=3)
    assert e.value.code == "hw_fault" and r.ctl.hw_fault


# ---- session (findings 4, 5, 8) ---------------------------------------------------------------------------------------

async def test_failed_sim_hello_falls_back_as_a_viewer(srv):
    ws = await connect(srv, mode="sim", world="nope")
    w = await ws.until(lambda d: d["t"] == "welcome")
    assert w["mode"] == "car" and w["role"] == "viewer"
    st = await (await srv.get("/api/status")).json()
    assert st["clients"]["car_controller"] is False
    other, wo = await hello(srv, cid="client-bbbb-0002")
    assert wo["role"] == "controller"


async def test_failed_sim_hello_when_busy_does_not_take_control(srv):
    for i in range(4):
        await hello(srv, cid=f"client-sim-000{i}", mode="sim")
    ws = await connect(srv, cid="client-sim-0005", mode="sim")
    w = await ws.until(lambda d: d["t"] == "welcome")
    assert w["mode"] == "car" and w["role"] == "viewer"


async def test_failed_mode_switch_keeps_control(srv):
    ws, _ = await hello(srv)
    await ws.send(t="mode", mode="sim", world="nope")
    assert (await ws.until(lambda d: d["t"] == "err"))["code"] == "bad_request"
    st = await (await srv.get("/api/status")).json()
    assert st["clients"]["car_controller"] is True


async def test_rate_limit_exempts_only_safety_commands(srv):
    ws, _ = await hello(srv)
    for i in range(200):                                    # exhaust the bucket
        await ws.send(t="cmd", id=1000 + i, op="steer", angle=0)
    await ws.send(t="hb", note="stop estop")                # mentions the words, but is not a stop
    await ws.send(t="cmd", id=5000, op="steer", angle=0, note='"stop"')
    await ws.send(t="cmd", id=5001, op="estop")
    r = await ws.until(lambda d: d.get("id") == 5001)
    assert r["t"] == "ack"
    limited = [d for d in ws.log if d.get("code") == "rate_limited"]
    assert limited and all(d.get("id") != 5001 for d in limited)
    assert any(d.get("id", 0) >= 1000 for d in limited)     # a limited cmd frame is answered with its id
    assert not any(d.get("id") == 5000 and d["t"] == "ack" for d in ws.log)


# ---- names (finding 9) ------------------------------------------------------------------------------------------------

def test_names_with_a_trailing_newline_are_rejected(tmp_path):
    with pytest.raises(StoreError):
        ProjectStore(tmp_path).path("proj\n")
    with pytest.raises(protocol.ProtocolError):
        protocol._world_id({"world": "arena\n"})
    with pytest.raises(KeyError):
        load_world("arena\n")


# ---- projects (finding 10) --------------------------------------------------------------------------------------------

def test_delete_removes_the_backup_too(tmp_path):
    store = ProjectStore(tmp_path)
    store.put("a", make_sb3())
    store.put("a", make_sb3({"x.txt": "2"}))
    assert (tmp_path / "a.sb3.bak").exists()
    store.delete("a")
    assert not list(tmp_path.iterdir())


def test_put_uses_a_unique_temp_file_and_cleans_up(tmp_path):
    store = ProjectStore(tmp_path)
    store.put("a", make_sb3())
    assert not list(tmp_path.glob("*.tmp"))
    import picar_server.projects as pj
    orig = pj.os.replace
    pj.os.replace = lambda *a: (_ for _ in ()).throw(OSError("disk"))
    try:
        with pytest.raises(OSError):
            store.put("a", make_sb3({"y": "1"}))
    finally:
        pj.os.replace = orig
    assert not list(tmp_path.glob("*.tmp"))


async def test_concurrent_puts_do_not_interleave(srv, tmp_path):
    a, b = make_sb3({"a.txt": "A" * 5000}), make_sb3({"b.txt": "B" * 7000})
    rs = await asyncio.gather(*[srv.put("/api/projects/p.sb3", data=a if i % 2 else b) for i in range(8)])
    assert all(r.status == 200 for r in rs)
    assert (tmp_path / "data" / "projects" / "p.sb3").read_bytes() in (a, b)


async def test_put_over_25mb_is_rejected_from_content_length(srv):
    r = await srv.put("/api/projects/big.sb3", data=b"\0" * (26 * 1024 * 1024))
    assert r.status == 413


# ---- stop latch (C1) ----------------------------------------------------------------------------------------------

def test_hold_latched_between_validation_and_write_cannot_energise_the_motors():
    class B(FakeBackend):
        def set_motors(self, left, right):
            r.ctl.hold = True                              # the sensor thread latches hold right after the check
            r.ctl._sync_latch()
            return super().set_motors(left, right)

    r = Rig(backend=B())
    r.run(0.2)
    with pytest.raises(CmdError) as e:
        r.cmd("drive", dir="backward", speed=30)
    assert e.value.code == "hold" and r.backend.duty == (0, 0)


def test_real_backend_stop_latch_zeroes_and_refuses_writes_until_released():
    b = _real()
    b.set_motors(2000, 2000)
    assert b.latch_stop() and b.motion() == (0, 0)
    assert b.set_motors(-1000, -1000) is False and b.motion() == (0, 0)
    b.release_stop()
    assert b.set_motors(-1000, -1000) is True and b.motion() == (-1000, -1000)


def test_near_sample_between_validation_and_motor_start_still_stops():
    class B(FakeBackend):
        def set_motors(self, left, right):
            r.sample(8.0)                                  # the guard sees the car stopped, so it does nothing
            return super().set_motors(left, right)

    r = Rig(backend=B())
    r.run(0.2)
    with pytest.raises(CmdError) as e:
        r.cmd("drive", dir="forward", speed=60)
    assert e.value.code == "obstacle_blocked" and r.backend.duty == (0, 0)


def test_estop_latches_the_backend_and_reset_releases_it():
    r = Rig()
    r.run(0.2)
    r.ctl.estop("viewer")
    assert r.backend.stop_latched
    r.cmd("estop_reset")
    assert not r.backend.stop_latched


# ---- zeroing both channels (C3) -------------------------------------------------------------------------------------

def test_real_startup_zeroing_attempts_both_channels():
    b = _real()
    tried = []

    def set_pwm_retry(ch, value):
        tried.append(ch)
        if ch == 4:
            raise OSError("i2c")

    b.pca.set_pwm_retry = set_pwm_retry
    with pytest.raises(OSError):
        b._zero_pwm()
    assert sorted(tried) == [4, 5]


def test_safe_stop_zeroes_the_other_motor_when_one_fails():
    writes = []

    class Bus:
        def write_byte_data(self, addr, reg, val):
            if reg < 0x06 + 4 * 5:                          # channel 4 registers
                raise OSError("i2c")
            writes.append(reg)

    with pytest.raises(OSError):
        safe_stop.zero_motors(Bus())
    assert len(writes) == 4                                 # channel 5 was still zeroed


# ---- reverse budget (C6) -------------------------------------------------------------------------------------------

def test_reverse_burst_between_ticks_is_charged_in_full():
    r = Rig()
    r.run(0.2)
    r.cmd("drive", dir="backward", speed=30)
    r.clock.advance(6.0)                                    # no tick in between: the old 250 ms cap undercounted
    r.ctl.touch()
    r.sample()
    r.ctl.tick()
    assert r.backend.duty == (0, 0) and "reverse_timeout" in r.kinds()


def test_reverse_budget_is_checked_at_the_next_command():
    r = Rig()
    r.run(0.2)
    r.cmd("drive", dir="backward", speed=30)
    r.clock.advance(5.5)
    with pytest.raises(CmdError) as e:
        r.cmd("drive", dir="backward", speed=20)
    assert e.value.code == "reverse_timeout"


async def test_reverse_deadline_task_stops_without_a_tick(monkeypatch):
    monkeypatch.setattr(safety, "REVERSE_MS_MAX", 100)
    r = Rig()
    r.run(0.2)
    r.cmd("drive", dir="backward", speed=30)
    await asyncio.sleep(0.3)
    assert r.backend.duty == (0, 0) and "reverse_timeout" in r.kinds()


# ---- speed limit (C7) ------------------------------------------------------------------------------------------------

def test_lowering_speed_limit_stops_when_the_running_motion_no_longer_validates():
    r = Rig()
    r.run(0.2)
    r.cmd("drive", dir="forward", speed=60)
    assert r.backend.duty != (0, 0)
    r.ctl._last_good_t = r.clock() - 10.0                   # the distance sensor went stale
    r.cmd("config", speed_limit=30)
    assert r.backend.duty == (0, 0)


# ---- watchdog feed (C8) -----------------------------------------------------------------------------------------------

async def test_rejected_commands_do_not_feed_the_watchdog(srv):
    ws, _ = await hello(srv)
    car = srv.app["hub"].car
    await asyncio.sleep(0.1)
    before = car.last_controller_frame
    assert await ws.err("drive", dir="forward", speed=30) == "hold"   # the car starts on hold
    await asyncio.sleep(0.05)
    assert car.last_controller_frame == before
    await ws.send(t="hb")
    await asyncio.sleep(0.05)
    assert car.last_controller_frame > before
    mid = car.last_controller_frame
    await ws.ok("resume")
    assert car.last_controller_frame > mid


# ---- line probe vs. motor lock (C9) -----------------------------------------------------------------------------------

def test_line_probe_never_runs_while_the_motor_lock_is_taken():
    b = _real()
    b.cal["line_enabled"] = True
    reads = []
    b.bus.read_i2c_block_data = lambda *a: reads.append(a) or [0] * 10
    with b._motor_lock:                                     # a motor operation is in progress
        assert b.hub._read_line() is None
    assert reads == []
    assert b.hub._read_line() is not None and len(reads) == 1


def test_set_motors_gives_up_on_a_held_motor_lock():
    b = _real()
    with b._motor_lock:
        with pytest.raises(HardwareFault):
            b.set_motors(1000, 1000)


# ---- projects / static (C11, C12) -----------------------------------------------------------------------------------------

def test_quota_counts_the_retained_backup(tmp_path):
    sb3 = make_sb3()
    store = ProjectStore(tmp_path, quota=2 * len(sb3) + 10)
    store.put("a", sb3)
    store.put("b", sb3)
    with pytest.raises(StoreError) as e:                    # a + b + the .bak the overwrite would keep
        store.put("a", sb3)
    assert e.value.status == 413


async def test_directory_index_symlink_cannot_escape_the_web_root(srv, tmp_path):
    secret = tmp_path / "secret.html"
    secret.write_text("SECRET")
    d = tmp_path / "www" / "d"
    d.mkdir(parents=True)
    os.symlink(secret, d / "index.html")
    r = await srv.get("/d/")
    assert r.status == 404 and "SECRET" not in await r.text()
