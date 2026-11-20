#!/usr/bin/env python3
"""Both motors together with the corrected direction pins. Wheels up!
Phase 1: direction pins LOW 2 s. Pause 2 s. Phase 2: direction pins HIGH 2 s."""
import signal, sys, time
sys.path.insert(0, "/home/c4/picar")
import RPi.GPIO as GPIO
from obstacle_stop import Car, MOTOR_A_PWM, MOTOR_B_PWM, MOTOR_A_DIR, MOTOR_B_DIR

GPIO.setwarnings(False); GPIO.setmode(GPIO.BCM)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1)); signal.signal(signal.SIGHUP, lambda *_: sys.exit(1))
car = Car(); duty = int(4095 * 50 / 100)
try:
    for n, level in ((1, 0), (2, 1)):
        print(f"phase {n}: both motors, dir pins {'HIGH' if level else 'LOW'} (A=GPIO{MOTOR_A_DIR}, B=GPIO{MOTOR_B_DIR}), 2 s", flush=True)
        GPIO.output(MOTOR_A_DIR, level); GPIO.output(MOTOR_B_DIR, level)
        car.pwm.set(MOTOR_A_PWM, duty); car.pwm.set(MOTOR_B_PWM, duty)
        time.sleep(2); car.stop(); time.sleep(2)
finally:
    car.stop(); print("all motors stopped", flush=True)
