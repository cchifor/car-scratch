from _common import new_project
from sb3 import *

META = dict(id="08-light-follower", title="Light follower", level="intermediate", needs=["light"],
            world="lamp",
            description="Turn towards the brightest light and drive to it. It stops when it gets dark.")


def build():
    p, car = new_project("Light follower: shine a torch at the car")
    bright = any_of(*[gt(light_value(w), 20) for w in ("left", "middle", "right")])
    car.script(
        -200, 60, when_flag(),
        forever(
            if_else(bright,
                    [if_else(eq(brightest_direction(), "left"),
                             [steer("left")],
                             [if_else(eq(brightest_direction(), "right"),
                                      [steer("right")], [steer("straight")])]),
                     drive("forward", 30),
                     say("I see the light!")],
                    [stop_motors(),
                     say("Too dark...")]),
        ),
    )
    return p
