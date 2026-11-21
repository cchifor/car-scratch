"""DryRunBackend and safe_stop."""
import subprocess
import sys
from pathlib import Path

from fakes_hw import PCA, FakeBus, FakeClock, FakeGPIO, ch_regs
from picar_server import safe_stop
from picar_server.hal.dryrun import DryRunBackend


def make():
    clock = FakeClock()
    bus, gpio = FakeBus(), FakeGPIO(clock)
    return DryRunBackend({}, bus=bus, gpio=gpio, clock=clock), bus, gpio


def test_dryrun_zeroes_motors_at_start_and_never_writes_motor_or_servo_after():
    b, bus, gpio = make()
    allowed = set(ch_regs(4) + ch_regs(5))
    assert bus.writes and all(a == PCA and r in allowed and v == 0 for a, r, v in bus.writes)
    n_writes, n_gpio = len(bus.writes), len(gpio.outputs)
    b.set_motors(3000, -3000)
    b.set_steering(30)
    b.set_trim(2.0)
    b.stop_motors()
    assert len(bus.writes) == n_writes                   # nothing written to the chip
    assert [o for o in gpio.outputs[n_gpio:] if o[0] in (17, 27)] == []    # direction pins untouched
    assert not any(a == PCA and r in ch_regs(0) for a, r, _ in bus.writes)


def test_dryrun_sensors_are_real_and_name_is_dryrun():
    b, bus, gpio = make()
    gpio.default = gpio.echo_cm(70)
    s = b.hub.read()
    assert b.name == "dryrun" and s.dist_state == "ok" and abs(s.dist_cm - 70) < 0.5
    assert b.pose() is None
    b.close()
    assert bus.closed


def test_safe_stop_zeroes_only_ch4_ch5_with_a_fake_bus():
    bus = FakeBus()
    assert safe_stop.main(lambda: bus) == 0
    allowed = set(ch_regs(4) + ch_regs(5))
    assert len(bus.writes) == 8 and {r for _, r, _ in bus.writes} == allowed
    assert not any(r in ch_regs(0) for _, r, _ in bus.writes)    # servo left alone


def test_safe_stop_exits_zero_when_chip_missing_or_bus_broken(capsys):
    bus = FakeBus()
    bus.missing.add(PCA)
    assert safe_stop.main(lambda: bus) == 0
    assert "warning" in capsys.readouterr().out

    def no_bus():
        raise FileNotFoundError("/dev/i2c-1")
    assert safe_stop.main(no_bus) == 0


def test_safe_stop_module_exits_zero_without_hardware():
    server = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-m", "picar_server.safe_stop"], cwd=server, capture_output=True,
                       text=True, env={"PYTHONPATH": str(server), "PATH": "/usr/bin:/bin"}, timeout=30)
    assert r.returncode == 0
    assert len(r.stdout.strip().splitlines()) == 1


def test_sdnotify_is_a_noop_without_socket(monkeypatch):
    from picar_server import sdnotify
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sdnotify.ready() is False and sdnotify.watchdog() is False


def test_sdnotify_sends_to_the_socket(monkeypatch, tmp_path):
    import socket
    from picar_server import sdnotify
    path = str(tmp_path / "notify.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(path)
    srv.settimeout(2)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    assert sdnotify.ready() and srv.recv(64) == b"READY=1"
    assert sdnotify.watchdog() and srv.recv(64) == b"WATCHDOG=1"
    srv.close()


def test_dryrun_tracks_motion_and_inhibit_without_touching_hardware():
    b, bus, gpio = make()
    n = len(bus.writes)
    assert b.set_motors(500, 500) is True and b.motion() == (500, 500)
    b.inhibit_forward()
    assert b.motion() == (0, 0)
    assert b.set_motors(500, 500) is False and b.motion() == (0, 0)
    b.clear_inhibit()
    assert b.set_motors(500, 500) is True
    assert b.stop_motors() is True and b.motion() == (0, 0)
    assert len(bus.writes) == n
