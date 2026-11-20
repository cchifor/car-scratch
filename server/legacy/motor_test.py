#!/usr/bin/env python3
"""One motor at a time, 2 s forward then 2 s backward, with register read-back. Wheels up!"""
import signal, sys, time
sys.path.insert(0, "/home/c4/picar")
import RPi.GPIO as GPIO
from obstacle_stop import Car, MOTOR_A_PWM, MOTOR_B_PWM, MOTOR_A_DIR, MOTOR_B_DIR, PCA_ADDR

speed = int(sys.argv[1]) if len(sys.argv) > 1 else 50
GPIO.setwarnings(False); GPIO.setmode(GPIO.BCM)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1)); signal.signal(signal.SIGHUP, lambda *_: sys.exit(1))
car = Car()
duty = int(4095 * speed / 100)

def readback(ch):
    b = car.pwm.bus; base = 0x06 + 4 * ch
    return b.read_byte_data(PCA_ADDR, base + 2) | (b.read_byte_data(PCA_ADDR, base + 3) << 8)

try:
    for name, ch, pin in (("MOTOR A (PWM ch4, dir GPIO17)", MOTOR_A_PWM, MOTOR_A_DIR),
                          ("MOTOR B (PWM ch5, dir GPIO27)", MOTOR_B_PWM, MOTOR_B_DIR)):
        for label, level in (("FORWARD  (dir LOW)", 0), ("BACKWARD (dir HIGH)", 1)):
            print(f"{name}: {label} at {speed}% for 2 s ...", flush=True)
            GPIO.output(pin, level); car.pwm.set(ch, duty)
            print(f"   read-back duty={readback(ch)} (expected {duty}), dir pin={GPIO.input(pin)}", flush=True)
            time.sleep(2)
            car.stop(); time.sleep(1.5)
finally:
    car.stop()
    print("all motors stopped", flush=True)
