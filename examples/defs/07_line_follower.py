from _common import new_project
from sb3 import *

META = dict(id="07-line-follower", title="Line follower", level="intermediate", needs=["line"],
            world="oval",
            description="Steer by how far the line is from the middle. Stop and say so if the line is lost.")


def build():
    p, car = new_project("Line follower: put the car on the line")
    sees_any = any_of(*[line_sees(i) for i in range(1, 6)])
    car.script(
        -200, 60, when_flag(),
        forever(
            if_else(sees_any,
                    [set_steering(mul(line_position(), 15)),
                     drive("forward", 30),
                     say("Following the line")],
                    [stop_motors(),
                     say("Lost the line")]),
        ),
    )
    return p
