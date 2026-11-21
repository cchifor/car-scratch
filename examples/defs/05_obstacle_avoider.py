from _common import new_project
from sb3 import *

META = dict(id="05-obstacle-avoider", title="Obstacle avoider", level="intermediate",
            needs=["ultrasonic"], world="arena",
            description="Drive on and on. When something is close, or the car is stuck, back up and turn away, "
                        "then carry on.")

# React at 40 cm: further out than the car's own safety stop at this speed (about 28 cm at 40 %), so the
# program turns away before the safety stop has to step in.
REACT_CM = 40


def build():
    p, car = new_project("Obstacle avoider: press the green flag")
    # "avoid" = 1 asks the avoiding script below to back up and turn. Both the obstacle hat and the stuck check
    # only set it, so the manoeuvre lives in one place and never runs twice at the same time.
    p.var("avoid", 0)
    p.var("last distance", 0)
    car.script(
        -260, 40, when_flag(),
        set_var("avoid", 0),
        steer("straight"),
        say("Exploring!"),
        forever(if_(eq(V("avoid"), 0), drive("forward", 40))),
    )
    car.script(
        -260, 260, when_obstacle(REACT_CM),
        set_var("avoid", 1),
    )
    # Stuck check: something is near (under 1 m) but the distance has not changed for 1.5 s. That happens when
    # a corner of the car touches a wall the sensor cannot see (walls at an angle), or when the car's safety
    # stop has already stopped it.
    car.script(
        -260, 360, when_flag(),
        set_var("last distance", distance()),
        forever(
            wait(1.5),
            if_(and_(and_(eq(V("avoid"), 0), lt(distance(), 100)),
                     and_(lt(sub(distance(), V("last distance")), 3), lt(sub(V("last distance"), distance()), 3))),
                say("I'm stuck!"),
                set_var("avoid", 1)),
            set_var("last distance", distance()),
        ),
    )
    car.script(
        220, 40, when_flag(),
        forever(
            wait_until(eq(V("avoid"), 1)),
            stop_motors(),
            say("Something is in the way!"),
            steer("right"),
            drive_for("backward", 30, 1.2),
            steer("left"),
            drive_for("forward", 40, 1),
            steer("straight"),
            say("Exploring!"),
            set_var("avoid", 0),
        ),
    )
    return p
