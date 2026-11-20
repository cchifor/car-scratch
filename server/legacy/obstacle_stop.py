#!/usr/bin/env python3
"""PiCar-S obstacle-stop test.

Drives straight ahead slowly and stops the rear wheels as soon as the ultrasonic
sensor sees something closer than --stop-cm. Fail-safe: a sensor fault, an error,
Ctrl-C, a closed SSH session (SIGHUP), SIGTERM or the --duration limit all stop
the motors.

Hardware (probed on this car, 2026-11-20):
  PCA9685 @ I2C-1 0x40, 60 Hz: ch0 steering servo, ch4/ch5 rear motor speed
  TB6612 direction pins: GPIO27 (motor A, right wheel), GPIO17 (motor B, left wheel)
  Ultrasonic module: single SIG pin on GPIO5 (trigger and echo on the same pin)

  --dry-run       read the sensor and print decisions; motors and servo untouched
  --once          stop at the first obstacle and exit (default: wait until clear, resume)
  --invert-a/-b   flip a motor's forward direction if a wheel turns backwards
"""
import argparse
import json
import math
import signal
import statistics
import sys
import time
from pathlib import Path

import RPi.GPIO as GPIO
import smbus

PCA_ADDR = 0x40
SERVO_CH, MOTOR_A_PWM, MOTOR_B_PWM = 0, 4, 5
# Measured on this car (2026-11-20): the direction wires are swapped vs SunFounder's
# back_wheels.py (17/27). Output 4 = right wheel, direction GPIO27; output 5 = left, GPIO17.
MOTOR_A_DIR, MOTOR_B_DIR = 27, 17
US_PIN = 5
FREQ = 60
# Saved steering trim (degrees added to the 90-degree centre); written by --save-trim.
CAL_FILE = Path(__file__).with_name("calibration.json")


def load_trim():
    try:
        return float(json.loads(CAL_FILE.read_text())["steer_trim_deg"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def save_trim(deg):
    CAL_FILE.write_text(json.dumps({"steer_trim_deg": deg}, indent=2) + chr(10))


class PCA9685:
    MODE1, MODE2, PRESCALE, LED0_ON_L, ALL_LED_ON_L = 0x00, 0x01, 0xFE, 0x06, 0xFA

    def __init__(self, bus=1, addr=PCA_ADDR):
        self.bus, self.addr = smbus.SMBus(bus), addr

    def _w(self, reg, val):
        self.bus.write_byte_data(self.addr, reg, val)

    def setup(self, freq=FREQ):
        for i, v in enumerate((0, 0, 0, 0)):            # all channels off
            self._w(self.ALL_LED_ON_L + i, v)
        self._w(self.MODE2, 0x04)                         # totem-pole outputs
        self._w(self.MODE1, 0x01)                         # ALLCALL, awake
        time.sleep(0.005)
        prescale = int(math.floor(25_000_000.0 / 4096.0 / freq - 1.0 + 0.5))
        old = self.bus.read_byte_data(self.addr, self.MODE1)
        self._w(self.MODE1, (old & 0x7F) | 0x10)          # sleep to change prescale
        self._w(self.PRESCALE, prescale)
        self._w(self.MODE1, old)
        time.sleep(0.005)
        self._w(self.MODE1, old | 0x80)                   # restart

    def set(self, ch, off):
        base = self.LED0_ON_L + 4 * ch
        for i, v in enumerate((0, 0, off & 0xFF, off >> 8)):
            self._w(base + i, v)


class Car:
    def __init__(self, invert_a=False, invert_b=False):
        self.pwm = PCA9685()
        self.pwm.setup()
        GPIO.setup([MOTOR_A_DIR, MOTOR_B_DIR], GPIO.OUT, initial=GPIO.LOW)
        # Measured on this car (wheels-up test, 2026-11-20): direction pins HIGH roll it forward
        self.fwd_a, self.fwd_b = int(not invert_a), int(not invert_b)
        self.stop()

    def steer_straight(self, trim_deg=0.0):
        # SunFounder servo map: 0..180 deg -> 600..2400 us; 90 = straight, <90 = left, >90 = right.
        # One PCA9685 step is ~0.41 deg, so trims finer than that round to the nearest step.
        angle = max(45.0, min(135.0, 90.0 + trim_deg))
        pulse_us = 600 + (2400 - 600) * angle / 180
        self.pwm.set(SERVO_CH, int(round(pulse_us / 1e6 * FREQ * 4096)))

    def forward(self, speed):
        GPIO.output(MOTOR_A_DIR, self.fwd_a)
        GPIO.output(MOTOR_B_DIR, self.fwd_b)
        duty = int(4095 * max(0, min(100, speed)) / 100)
        self.pwm.set(MOTOR_A_PWM, duty)
        self.pwm.set(MOTOR_B_PWM, duty)

    def stop(self):
        self.pwm.set(MOTOR_A_PWM, 0)
        self.pwm.set(MOTOR_B_PWM, 0)


class Ultrasonic:
    FAR = 999.0  # echo longer than the timeout: nothing in range, i.e. clear

    def __init__(self, pin=US_PIN, timeout=0.03):
        self.pin, self.timeout = pin, timeout

    def ping(self):
        """cm, FAR if nothing in range, None if the sensor never answered (fault)."""
        p = self.pin
        GPIO.setup(p, GPIO.OUT, initial=GPIO.LOW)
        time.sleep(0.002)
        GPIO.output(p, 1)
        time.sleep(0.00001)
        GPIO.output(p, 0)
        GPIO.setup(p, GPIO.IN)
        t0 = time.monotonic()
        while GPIO.input(p) == 0:
            if time.monotonic() - t0 > self.timeout:
                return None
        start = time.monotonic()
        while GPIO.input(p) == 1:
            if time.monotonic() - start > self.timeout:
                return self.FAR
        return (time.monotonic() - start) * 34300 / 2

    def distance(self, n=3):
        vals = [v for v in (self.ping() for _ in range(n)) if v is not None]
        return statistics.median(vals) if len(vals) >= 2 else None


class Stop(Exception):
    pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--speed", type=int, default=30, help="motor duty %% (default 30, max 100)")
    ap.add_argument("--stop-cm", type=float, default=25.0)
    ap.add_argument("--resume-cm", type=float, default=35.0)
    ap.add_argument("--clear-s", type=float, default=1.0, help="how long it must stay clear before resuming")
    ap.add_argument("--duration", type=float, default=20.0, help="hard time limit in seconds")
    ap.add_argument("--trim", type=float, default=None,
                    help="steering trim in degrees (+ = right, - = left); default: saved value")
    ap.add_argument("--save-trim", action="store_true", help="save --trim as the new default")
    ap.add_argument("--invert-a", action="store_true")
    ap.add_argument("--invert-b", action="store_true")
    a = ap.parse_args()
    trim = load_trim() if a.trim is None else a.trim
    if a.save_trim:
        if a.trim is None:
            ap.error("--save-trim needs --trim")
        save_trim(a.trim)
    if not 0 < a.speed <= 100:
        ap.error("--speed must be 1..100")
    if a.resume_cm <= a.stop_cm:
        ap.error("--resume-cm must be greater than --stop-cm")

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    def on_signal(signum, _frame):
        raise Stop(signal.Signals(signum).name)

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, on_signal)

    us = Ultrasonic()
    car = None
    t_start = time.monotonic()
    log = lambda msg: print(f"[{time.monotonic() - t_start:6.2f}s] {msg}", flush=True)
    stops = 0
    try:
        if not a.dry_run:
            car = Car(a.invert_a, a.invert_b)
            car.steer_straight(trim)
            log(f"steering trim {trim:+.1f} deg" + (" (saved)" if a.save_trim else ""))
            for i in (3, 2, 1):
                log(f"wheels start in {i}... (Ctrl-C to abort)")
                time.sleep(1)
        else:
            log("DRY RUN: motors and servo are not touched")

        state, faults, clear_since, last_print = "STOPPED", 0, None, 0.0
        go = lambda: (car.forward(a.speed) if car else None)
        halt = lambda: (car.stop() if car else None)
        t_start = time.monotonic()
        while True:
            now = time.monotonic()
            if now - t_start > a.duration:
                halt()
                log(f"time limit {a.duration:.0f}s reached: stopped")
                break
            d = us.distance()
            if d is None:
                faults += 1
                if faults >= 3:
                    halt()
                    if state != "FAULT":
                        log("SENSOR FAULT (no echo): stopped")
                    state, clear_since = "FAULT", None
                continue
            faults = 0
            if state == "DRIVING" and d < a.stop_cm:
                halt()
                stops += 1
                state, clear_since = "STOPPED", None
                log(f"OBSTACLE at {d:.0f} cm (< {a.stop_cm:.0f}): STOP #{stops}")
                if a.once:
                    break
            elif state != "DRIVING":
                if d > a.resume_cm:
                    clear_since = clear_since or now
                    if now - clear_since >= (0 if stops == 0 and state == "STOPPED" else a.clear_s):
                        go()
                        state = "DRIVING"
                        log(f"clear at {'far' if d >= Ultrasonic.FAR else f'{d:.0f} cm'}: "
                            f"{'(dry run) would drive' if a.dry_run else 'driving'} forward at {a.speed}%")
                else:
                    clear_since = None
            if now - last_print >= 0.5:
                last_print = now
                log(f"{state:8} distance {'far' if d >= Ultrasonic.FAR else f'{d:5.1f} cm'}")
            time.sleep(0.02)
    except Stop as e:
        log(f"{e}: stopping")
    finally:
        if car:
            car.stop()
        GPIO.setup([US_PIN], GPIO.IN)
        log(f"motors stopped. obstacle stops: {stops}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
