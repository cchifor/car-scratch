#!/usr/bin/env python3
"""Find which PCA9685 outputs really drive the motors. Wheels up!
Burst 1: output 4 only. Burst 2: output 5 only. Burst 3: outputs 1-3 and 6-15 together.
Each burst: both direction pins LOW 2 s, then both HIGH 2 s. 3 s pause between bursts.
Output 0 (steering servo) is never touched."""
import signal, sys, time
sys.path.insert(0, "/home/c4/picar")
import RPi.GPIO as GPIO
from obstacle_stop import Car, MOTOR_A_DIR, MOTOR_B_DIR

speed = int(sys.argv[1]) if len(sys.argv) > 1 else 50
GPIO.setwarnings(False); GPIO.setmode(GPIO.BCM)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1)); signal.signal(signal.SIGHUP, lambda *_: sys.exit(1))
car = Car()
duty = int(4095 * speed / 100)
OTHERS = [1, 2, 3] + list(range(6, 16))

def all_off():
    for ch in range(1, 16):
        car.pwm.set(ch, 0)

try:
    all_off()
    for n, (label, chans) in enumerate((("output 4", [4]), ("output 5", [5]), ("outputs 1-3,6-15", OTHERS)), 1):
        for level, way in ((0, "dir pins LOW"), (1, "dir pins HIGH")):
            print(f"burst {n} ({label}): {way}, 2 s", flush=True)
            GPIO.output(MOTOR_A_DIR, level); GPIO.output(MOTOR_B_DIR, level)
            for ch in chans:
                car.pwm.set(ch, duty)
            time.sleep(2)
            all_off(); time.sleep(0.3)
        print("   pause 3 s", flush=True)
        time.sleep(3)
finally:
    all_off()
    print("all outputs off", flush=True)
