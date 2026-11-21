from _common import new_project
from sb3 import *

META = dict(id="01-hello-car", title="Hello car", level="beginner", needs=[], world="arena",
            description="Drive forward, wiggle left and right, then back up. The car says what it does.")


def build():
    p, car = new_project("Hello car: press the green flag", car_x=-60)
    car.script(
        -200, 60, when_flag(),
        steer("straight"),
        say("Here I go!"),
        drive_for("forward", 40, 1),
        say("Wiggle left..."),
        steer("left"),
        drive_for("forward", 40, 0.5),
        say("...and right!"),
        steer("right"),
        drive_for("forward", 40, 0.5),
        steer("straight"),
        say("Now backwards."),
        drive_for("backward", 30, 1),
        stop_motors(),
        say("All done!"),
    )
    return p
