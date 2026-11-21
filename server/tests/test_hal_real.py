"""RealBackend: PCA9685 sequence, motor/steering writes, fault handling, sensor thread."""
import threading
import time

import pytest

from fakes_hw import PCA, FakeBus, FakeClock, FakeGPIO, ch_regs
from picar_server.hal.base import HardwareFault
from picar_server.hal.real import DIR_LEFT_GPIO, DIR_RIGHT_GPIO, RealBackend


def make(cal=None, **kw):
    clock = FakeClock()
    bus, gpio = FakeBus(), FakeGPIO(clock)
    b = RealBackend(cal or {}, bus=bus, gpio=gpio, clock=clock, **kw)
    return b, bus, gpio, clock


def test_constructor_zeroes_motors_first_then_initialises_chip():
    _, bus, gpio, _ = make()
    regs = [(reg, val) for addr, reg, val in bus.writes if addr == PCA]
    zero45 = [(r, 0) for r in ch_regs(4) + ch_regs(5)]
    assert regs[:8] == zero45                            # motors zeroed before anything else
    assert regs[8:8 + 4] == [(0xFA, 0), (0xFB, 0), (0xFC, 0), (0xFD, 0)]   # ALL_LED off
    assert regs[12:] == [
        (0x01, 0x04),            # MODE2 totem pole
        (0x00, 0x01),            # MODE1 awake
        (0x00, 0x11),            # sleep to change prescale
        (0xFE, 101),             # 60 Hz
        (0x00, 0x01),
        (0x00, 0x81),            # restart
        *zero45,                 # zeroed again after the chip init
        (0x06, 0), (0x07, 0), (0x08, 369 & 0xFF), (0x09, 369 >> 8),     # servo centred: 90 deg -> 369
    ]
    assert bus.pwm(4) == 0 and bus.pwm(5) == 0
    assert gpio.level[DIR_LEFT_GPIO] == 0 and gpio.level[DIR_RIGHT_GPIO] == 0


def test_motor_mapping_left_is_ch5_gpio17_right_is_ch4_gpio27():
    b, bus, gpio, _ = make()
    b.set_motors(2047, -1000)                            # left forward, right backward
    assert gpio.level[17] == 1 and bus.pwm(5) == 2047    # left: GPIO17 HIGH = forward, ch5
    assert gpio.level[27] == 0 and bus.pwm(4) == 1000    # right: GPIO27 LOW = backward, ch4
    b.set_motors(-4095, 4095)
    assert gpio.level[17] == 0 and bus.pwm(5) == 4095
    assert gpio.level[27] == 1 and bus.pwm(4) == 4095
    b.set_motors(9999, -9999)                            # clamped to the PCA9685 range
    assert bus.pwm(5) == 4095 and bus.pwm(4) == 4095


def test_stop_motors_zeroes_both_and_is_idempotent():
    b, bus, _, _ = make()
    b.set_motors(3000, 3000)
    b.stop_motors()
    b.stop_motors()
    assert bus.pwm(4) == 0 and bus.pwm(5) == 0


@pytest.mark.parametrize("trim,angle,ticks", [
    (0.0, 0.0, 369),
    (3.0, -20.0, 327),      # 73 deg
    (10.0, 45.0, 479),      # 145 -> clamped 135
    (-10.0, -45.0, 258),    # 35 -> clamped 45
    (0.0, 99.0, 479),       # angle itself is limited to +-45 -> 135
])
def test_steering_ticks_trim_and_clamp(trim, angle, ticks):
    b, bus, _, _ = make({"steer_trim_deg": trim})
    b.set_steering(angle)
    assert bus.pwm(0) == ticks


def test_set_trim_reapplies_current_angle():
    b, bus, _, _ = make()
    b.set_steering(0)
    b.set_trim(3.0)                                      # 93 deg
    assert bus.pwm(0) == round((600 + 1800 * 93 / 180) / 1e6 * 60 * 4096)


def test_single_i2c_error_on_motor_write_is_retried():
    b, bus, _, _ = make()
    bus.fail_writes = 1
    b.set_motors(1000, 1000)
    assert bus.pwm(5) == 1000 and bus.pwm(4) == 1000


def test_two_i2c_errors_raise_hardware_fault_and_try_to_zero():
    b, bus, _, _ = make()
    b.set_motors(1000, 1000)
    bus.fail_writes = 2
    with pytest.raises(HardwareFault):
        b.set_motors(2000, 2000)
    assert bus.pwm(5) == 0 and bus.pwm(4) == 0           # best-effort zero after the fault


def test_stop_motors_never_raises_and_reports_failure():
    b, bus, gpio, _ = make()
    b.set_motors(1000, 1000)
    assert b.stop_motors() is True
    bus.fail_all_writes = True
    assert b.stop_motors() is False                      # swallowed, reported
    bus.missing.add(PCA)
    assert b.stop_motors() is False
    b.close()


def test_stop_motors_attempts_both_channels_even_if_one_fails():
    b, bus, gpio, _ = make()
    b.set_motors(1000, 1000)
    attempted = []

    def on_write(addr, reg, val):
        attempted.append(reg)
        if reg in ch_regs(4):                            # right motor channel keeps failing
            raise OSError(5, "io")
    bus.on_write = on_write
    assert b.stop_motors() is False
    assert any(r in ch_regs(5) for r in attempted)       # left was still attempted
    assert bus.pwm(5) == 0


def test_stop_motors_returns_false_on_motor_lock_timeout():
    b, bus, gpio, _ = make()
    b.set_motors(1000, 1000)
    started, release = threading.Event(), threading.Event()

    def hog():
        with b._motor_lock:
            started.set()
            release.wait(2)
    t = threading.Thread(target=hog)
    t.start()
    started.wait(1)
    t0 = time.monotonic()
    assert b.stop_motors() is False
    assert 0.15 < time.monotonic() - t0 < 1.0
    release.set()
    t.join()
    assert b.stop_motors() is True


def test_direction_change_order_pwm0_then_pin_then_pwm():
    b, bus, gpio, _ = make()
    b.set_motors(1000, 1000)                             # both forward
    events = []
    off_l = ch_regs(5)[2]
    bus.on_write = lambda addr, reg, val: events.append(("pwm", val)) if reg == off_l else None
    orig = gpio.output

    def output(pin, v):
        if pin == 17:
            events.append(("pin", v))
        orig(pin, v)
    gpio.output = output
    b.set_motors(-800, 1000)                             # left reverses
    assert events == [("pwm", 0), ("pin", 0), ("pwm", 800 & 0xFF)]
    assert bus.pwm(5) == 800 and gpio.level[17] == 0


def test_all_motor_i2c_writes_happen_under_motor_lock_then_bus_lock():
    b, bus, gpio, _ = make()
    seen = []
    bus.on_write = lambda addr, reg, val: seen.append((b._motor_lock.locked(), b.lock.locked()))
    b.set_motors(1000, -1000)
    b.stop_motors()
    b.inhibit_forward()
    assert seen and all(m and l for m, l in seen)        # outer motor lock, inner bus lock


def test_concurrent_motor_and_sensor_traffic_does_not_deadlock():
    b, bus, gpio, _ = make()
    gpio.default = gpio.echo_cm(100)
    stop = threading.Event()

    def hammer(fn):
        while not stop.is_set():
            fn()
    fns = [lambda: b.set_motors(500, 500), b.stop_motors, b.inhibit_forward, b.clear_inhibit, b.hub.read]
    ts = [threading.Thread(target=hammer, args=(f,), daemon=True) for f in fns]
    for t in ts:
        t.start()
    time.sleep(0.4)
    stop.set()
    for t in ts:
        t.join(3)
    assert not any(t.is_alive() for t in ts)


def test_inhibit_forward_stops_and_refuses_forward_until_cleared():
    b, bus, gpio, _ = make()
    assert b.set_motors(1000, 1000) is True and b.motion() == (1000, 1000)
    b.inhibit_forward()
    assert b.motion() == (0, 0) and bus.pwm(4) == 0 and bus.pwm(5) == 0
    assert b.set_motors(1000, 1000) is False             # refused: zero written
    assert b.motion() == (0, 0) and bus.pwm(5) == 0
    assert b.set_motors(1000, -500) is False             # forward side zeroed, backward side allowed
    assert b.motion() == (0, -500) and bus.pwm(5) == 0 and bus.pwm(4) == 500
    assert b.set_motors(-600, -600) is True and b.motion() == (-600, -600)
    b.clear_inhibit()
    assert b.set_motors(700, 700) is True and bus.pwm(5) == 700


def test_motion_snapshot_tracks_applied_duty_and_failed_write_zeroes():
    b, bus, gpio, _ = make()
    b.set_motors(100, -200)
    assert b.motion() == (100, -200)
    bus.fail_writes = 2
    with pytest.raises(HardwareFault):
        b.set_motors(300, 300)
    assert b.motion() == (0, 0)


def test_deadline_probe_stops_motors_and_calls_latch_callback_from_sensor_thread():
    b, bus, gpio, _ = make()
    gpio.default = gpio.echo_cm(100)
    state = {"loop_ok": True}
    b.set_deadline_probe(lambda: (state["loop_ok"], True))
    latched, done = [], threading.Event()
    b.set_latch_callback(lambda reason: (latched.append((reason, threading.current_thread().name)), done.set()))
    b.start(lambda s: None)
    b.set_motors(1000, 1000)
    time.sleep(0.15)
    assert b.motion() == (1000, 1000) and not latched    # healthy: untouched
    state["loop_ok"] = False
    assert done.wait(3)
    assert latched == [("loop_stalled", "picar-sensors")]
    assert b.motion() == (0, 0) and bus.pwm(4) == 0
    b.close()


def test_deadline_probe_ignored_while_stopped_and_controller_reason():
    b, bus, gpio, _ = make()
    calls = []
    b.set_deadline_probe(lambda: (True, False))
    b.set_latch_callback(calls.append)
    b._check_deadlines()
    assert calls == []                                   # motors stopped: nothing to do
    b.set_motors(500, 500)
    b._check_deadlines()
    assert calls == ["controller_silent"] and b.motion() == (0, 0)


def test_sensor_thread_exception_stops_motors_and_keeps_running():
    b, bus, gpio, _ = make()
    gpio.default = gpio.echo_cm(100)
    n, done = [], threading.Event()

    def on_sample(s):
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("controller bug")
        done.set()
    b.start(on_sample)
    b.set_motors(1000, 1000)
    assert done.wait(3)                                  # kept sampling after the exception
    assert b.motion() == (0, 0)
    b.close()


def test_sensor_thread_sets_switch_interval_and_close_restores_it():
    import sys
    before = sys.getswitchinterval()
    b, bus, gpio, _ = make()
    gpio.default = gpio.echo_cm(100)
    got = threading.Event()
    b.start(lambda s: got.set())
    assert got.wait(3)
    assert abs(sys.getswitchinterval() - 0.0005) < 1e-6
    b.close()
    assert abs(sys.getswitchinterval() - before) < 1e-6


def test_hardware_fault_is_importable_from_real():
    from picar_server.hal import real
    assert real.HardwareFault is HardwareFault


def test_sensor_thread_calls_on_sample_from_its_own_thread_and_close_joins():
    b, bus, gpio, _ = make({"line_enabled": True})
    gpio.default = gpio.echo_cm(100)
    bus.line_bytes = [0, 10, 0, 20, 1, 0, 0, 40, 0, 50]
    got, done = [], threading.Event()

    def on_sample(s):
        got.append((threading.current_thread().name, s))
        if len(got) >= 3:
            done.set()

    b.start(on_sample)
    assert done.wait(5)
    b.set_motors(1000, 1000)
    b.close()
    assert not any(t.name == "picar-sensors" and t.is_alive() for t in threading.enumerate())
    name, s = got[0]
    assert name == "picar-sensors" and s.dist_state == "ok" and 99 < s.dist_cm < 101
    assert s.line_raw == [10, 20, 256, 40, 50] and s.light_raw == [10, 20, 30]
    assert b.capabilities() == {"ultrasonic": True, "light": True, "line": True}
    assert bus.pwm(4) == 0 and bus.pwm(5) == 0           # close() stopped the motors
    assert bus.closed


def test_capabilities_line_absent():
    b, bus, gpio, _ = make({"line_enabled": True})
    b.hub.probe()
    assert b.capabilities()["line"] is False
    assert b.pose() is None and b.name == "real"
