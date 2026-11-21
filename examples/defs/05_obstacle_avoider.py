from _common import new_project
from sb3 import *

META = dict(id="05-obstacle-avoider", title="Obstacle avoider", level="intermediate",
            needs=["ultrasonic"], world="arena",
            description="Drive on and on. When something is close, back up and turn away, then carry on.")


def build():
    p, car = new_project("Obstacle avoider: press the green flag")
    # "avoiding" keeps the forever loop from driving forward while the backing-up manoeuvre runs.
    p.var("avoiding", 0)
    car.script(
        -200, 60, when_flag(),
        set_var("avoiding", 0),
        steer("straight"),
        say("Exploring!"),
        forever(if_(eq(V("avoiding"), 0), drive("forward", 40))),
    )
    car.script(
        -200, 260, when_obstacle(30),
        set_var("avoiding", 1),
        stop_motors(),
        say("Something is in the way!"),
        steer("right"),
        drive_for("backward", 30, 1),
        steer("left"),
        drive_for("forward", 40, 0.8),
        steer("straight"),
        say("Exploring!"),
        set_var("avoiding", 0),
    )
    return p
