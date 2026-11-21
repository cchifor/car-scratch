"""PowerMonitor on fake files / clock / vcgencmd, motor_s accounting, and `power` in tel frames."""
import asyncio

import pytest

from picar_server import power as pw
from picar_server.power import PowerMonitor, parse_throttled

from conftest import Rig
from test_server import hello, srv  # noqa: F401  (srv is a fixture)

KEYS = {"source", "low", "low_since_boot", "low_events", "throttled", "on_s", "motor_s", "cpu_temp_c", "cpu_load"}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(tmp_path, *, alarm=None, name="rpi_volt", temp="51234", uptime="1260.55 99.0", clock=None, events=None):
    """A monitor on fake sysfs files. alarm=None -> no hwmon dir at all."""
    hw = tmp_path / "hwmon"
    if alarm is not None:
        d = hw / "hwmon3"
        d.mkdir(parents=True, exist_ok=True)
        (d / "name").write_text(name + "\n")
        (d / "in0_lcrit_alarm").write_text(f"{alarm}\n")
    (tmp_path / "temp").write_text(temp + "\n")
    (tmp_path / "uptime").write_text(uptime + "\n")
    m = PowerMonitor(hwmon_glob=str(hw / "hwmon*"), thermal_path=str(tmp_path / "temp"),
                     uptime_path=str(tmp_path / "uptime"), vcgencmd=None, clock=clock or Clock(),
                     on_event=(lambda k, f: events.append((k, f))) if events is not None else None,
                     loadavg=lambda: (1.0, 0, 0), cpu_count=lambda: 4)
    m._vcgencmd = None
    return m


def set_alarm(tmp_path, v):
    (tmp_path / "hwmon" / "hwmon3" / "in0_lcrit_alarm").write_text(f"{v}\n")


# ---- parsing / discovery ------------------------------------------------------------------------

def test_parse_throttled():
    assert parse_throttled("throttled=0x50005\n") == 0x50005
    assert parse_throttled("throttled=0x0") == 0
    assert parse_throttled("garbage") is None and parse_throttled("") is None


def test_throttled_0x50005_is_low_throttled_and_low_since_boot(tmp_path):
    m = make(tmp_path)                                  # no hwmon: vcgencmd is the only low source
    assert m.snapshot()["source"] == "none"
    m.feed_throttled("throttled=0x50005")
    s = m.snapshot()
    assert (s["source"], s["low"], s["throttled"], s["low_since_boot"], s["low_events"]) == ("pi", True, True, True, 1)


def test_throttled_history_bit_alone_means_low_since_boot_only(tmp_path):
    m = make(tmp_path)
    m.feed_throttled("throttled=0x10000")
    s = m.snapshot()
    assert s["low"] is False and s["low_since_boot"] is True and s["throttled"] is False and s["low_events"] == 0


def test_hwmon_discovered_by_name_not_index(tmp_path):
    other = tmp_path / "hwmon" / "hwmon0"
    other.mkdir(parents=True)
    (other / "name").write_text("cpu_thermal\n")
    (other / "in0_lcrit_alarm").write_text("1\n")        # a decoy that must be ignored
    m = make(tmp_path, alarm=0)
    m.poll()
    assert m.snapshot()["source"] == "pi" and m.snapshot()["low"] is False
    set_alarm(tmp_path, 1)
    m.poll()
    assert m.snapshot()["low"] is True


def test_wrong_hwmon_name_is_not_a_source(tmp_path):
    m = make(tmp_path, alarm=1, name="something_else")
    m.poll()
    assert m.snapshot()["source"] == "none" and m.snapshot()["low"] is False


def test_other_fields(tmp_path):
    m = make(tmp_path, alarm=0)
    m.poll()
    s = m.snapshot(motor_s=3.14159)
    assert set(s) == KEYS
    assert (s["on_s"], s["cpu_temp_c"], s["cpu_load"], s["motor_s"]) == (1260, 51.2, 0.25, 3.1)


def test_missing_everything_is_source_none_and_never_raises(tmp_path):
    m = PowerMonitor(hwmon_glob=str(tmp_path / "nope*"), thermal_path=str(tmp_path / "t"),
                     uptime_path=str(tmp_path / "u"), loadavg=lambda: (_ for _ in ()).throw(OSError()))
    m.poll()
    m.feed_throttled("")
    assert m.snapshot() == {"source": "none", "low": False, "low_since_boot": False, "low_events": 0,
                            "throttled": False, "on_s": 0, "motor_s": 0.0, "cpu_temp_c": None, "cpu_load": None}


def test_alarm_file_vanishing_keeps_throttled_fallback(tmp_path):
    m = make(tmp_path, alarm=1)
    m.poll()
    assert m.snapshot()["low"] is True
    (tmp_path / "hwmon" / "hwmon3" / "in0_lcrit_alarm").unlink()
    m.poll()                                            # unreadable: falls back to (absent) vcgencmd -> unknown
    assert m.snapshot()["source"] == "none"


# ---- events -------------------------------------------------------------------------------------

def test_low_events_and_debounced_power_ok(tmp_path):
    clock, ev = Clock(), []
    m = make(tmp_path, alarm=0, clock=clock, events=ev)
    m.poll()
    assert ev == []
    set_alarm(tmp_path, 1)
    m.poll()
    assert ev == [("power_low", {"low_events": 1})]
    m.poll()                                            # still low: no second event
    assert len(ev) == 1
    set_alarm(tmp_path, 0)
    for _ in range(5):                                  # 0 s .. 4 s of "ok": not yet
        m.poll()
        clock.t += 1
    assert [k for k, _ in ev] == ["power_low"]
    clock.t += 0.5
    m.poll()
    assert ev[-1] == ("power_ok", {}) and len(ev) == 2
    m.poll()
    assert len(ev) == 2                                 # power_ok only once


def test_dip_within_debounce_does_not_emit_power_ok_but_counts(tmp_path):
    clock, ev = Clock(), []
    m = make(tmp_path, alarm=1, clock=clock, events=ev)
    m.poll()
    set_alarm(tmp_path, 0)
    m.poll()
    clock.t += 3
    m.poll()
    set_alarm(tmp_path, 1)
    m.poll()                                            # back down: a second low event, restarts the debounce
    set_alarm(tmp_path, 0)
    m.poll()
    clock.t += 4
    m.poll()
    assert [k for k, _ in ev] == ["power_low", "power_low"] and m.snapshot()["low_events"] == 2
    clock.t += 1.5
    m.poll()
    assert [k for k, _ in ev] == ["power_low", "power_low", "power_ok"]


def test_vcgencmd_drives_events_when_no_hwmon(tmp_path):
    clock, ev = Clock(), []
    m = make(tmp_path, clock=clock, events=ev)
    m.feed_throttled("throttled=0x1")
    m.feed_throttled("throttled=0x10000")
    clock.t += 6
    m.feed_throttled("throttled=0x10000")
    assert [k for k, _ in ev] == ["power_low", "power_ok"]
    assert m.snapshot()["low_since_boot"] is True


async def test_vcgencmd_loop_failures_keep_last_values(tmp_path, monkeypatch):
    monkeypatch.setattr(pw, "VCGENCMD_S", 0.01)
    answers = iter(["throttled=0x50005", RuntimeError("boom"), "nonsense"])

    async def fake():
        a = next(answers, "nonsense")
        if isinstance(a, Exception):
            raise a
        return a

    m = make(tmp_path)
    m._vcgencmd = fake
    m.start()
    await asyncio.sleep(0.15)
    m.stop()
    s = m.snapshot()
    assert s["source"] == "pi" and s["low"] is True and s["throttled"] is True


# ---- motor_s ------------------------------------------------------------------------------------

def test_motor_s_counts_only_while_a_duty_is_nonzero():
    rig = Rig()
    rig.run(0.2)
    assert rig.ctl.motor_s == 0.0
    rig.cmd("drive", dir="forward", speed=40)
    rig.run(1.0)
    rig.cmd("stop")
    rig.run(1.0)                                        # stopped: not counted
    rig.cmd("drive", dir="backward", speed=30)
    rig.run(0.5)
    assert rig.ctl.motor_s == pytest.approx(1.5, abs=0.1)


def test_sim_controller_snapshot_has_contract_power():
    rig = Rig(shared=False)
    rig.run(2.5)
    p = rig.ctl.snapshot()["power"]
    assert set(p) == KEYS and p["source"] == "sim" and p["on_s"] == 2 and p["cpu_temp_c"] is None
    assert not (set(Rig().ctl.snapshot()) & {"power"})   # the shared car's power is added by the Hub


# ---- server level -------------------------------------------------------------------------------

async def test_tel_frames_carry_power_in_car_and_sim_mode(srv):  # noqa: F811
    car, _ = await hello(srv)
    sim, _ = await hello(srv, cid="client-bbbb-0002", mode="sim", world="arena")
    c = await car.until(lambda d: d["t"] == "tel")
    s = await sim.until(lambda d: d["t"] == "tel")
    assert set(c["power"]) == KEYS and set(s["power"]) == KEYS
    assert s["power"]["source"] == "sim" and s["power"]["low"] is False
    assert c["power"]["source"] in ("none", "pi")


async def test_power_events_reach_car_clients_and_are_logged(srv, tmp_path):  # noqa: F811
    ws, _ = await hello(srv)
    m = srv.app["hub"].power
    d = tmp_path / "hwmon" / "hwmon7"
    d.mkdir(parents=True)
    (d / "name").write_text("rpi_volt\n")
    (d / "in0_lcrit_alarm").write_text("1\n")
    m.hwmon_glob = str(tmp_path / "hwmon" / "hwmon*")
    m._alarm_path = None
    m.poll()
    ev = await ws.until(lambda x: x["t"] == "event" and x["kind"] == "power_low")
    assert ev == {"t": "event", "kind": "power_low", "low_events": 1}
    tel = await ws.until(lambda x: x["t"] == "tel" and x["power"]["low"])
    assert tel["power"]["source"] == "pi" and tel["power"]["low_events"] == 1 and tel["power"]["low_since_boot"]
