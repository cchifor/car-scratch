from _common import new_project
from sb3 import *

META = dict(id="09-figure-eight", title="Figure eight", level="advanced", needs=[], world="arena",
            description="Drive a figure eight. The stage draws the path using the car x, y and direction.")


def build():
    p, car = new_project("Figure eight: press the green flag")
    car.script(
        -200, 60, when_flag(),
        say("Drawing my path!"),
        stop_motors(),
        steer("straight"),
        reset_position(),
        pen_clear(), pen_size(3), pen_color("#C62828"),
        broadcast("trail"),
        # a little way in first, so both loops clear the walls (in the simulator's arena: and the box)
        drive_for("forward", 40, 0.5),
        # one loop at full steering is about 1.5 m of driving: about 5 s at 40 %
        steer("left"),
        drive_for("forward", 40, 5),
        steer("right"),
        drive_for("forward", 40, 5),
        steer("straight"),
        stop_motors(),
        say("A figure eight!"),
    )
    car.script(
        -200, 460, when_broadcast("trail"),
        pen_up(),
        goto_xy(car_x(), car_y()),
        point_dir(car_direction()),
        pen_down(),
        forever(goto_xy(car_x(), car_y()), point_dir(car_direction())),
    )
    return p
