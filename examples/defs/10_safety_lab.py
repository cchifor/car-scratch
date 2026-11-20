from _common import new_project
from sb3 import *

META = dict(id="10-safety-lab", title="Safety lab", level="advanced", needs=["ultrasonic"],
            world="corridor",
            description="Try the safety blocks: speed limit, stop distance, car events and the emergency stop.")


def build():
    p, car = new_project("Safety lab: press space for emergency stop", car_x=-100)
    car.script(
        -200, 60, when_flag(),
        speed_limit(40),
        stop_distance(30),
        say("Speed limit 40, stop distance 30."),
        wait(1.5),
        say("I ask for 60 but only get 40."),
        drive("forward", 60),
    )
    car.script(-200, 400, when_car("obstacle"), say("The car stopped for an obstacle!"))
    car.script(-200, 520, when_car("estop"), say("Emergency stop! The car is stopped."))
    car.script(-200, 640, when_key("space"), emergency_stop())
    return p
