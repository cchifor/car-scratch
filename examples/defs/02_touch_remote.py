from _common import new_project, button_svg
from sb3 import *

META = dict(id="02-touch-remote", title="Touch remote", level="beginner", needs=[], world="arena",
            description="Tap the big arrow buttons to nudge the car. Slide the speed. Arrow keys work too.")

SPEED = V("speed")


def nudge_forward():
    return [drive_for("forward", SPEED, 0.5)]


def nudge_back():
    return [drive_for("backward", 30, 0.5)]


def nudge_turn(side):
    return [steer(side), drive_for("forward", SPEED, 0.5), steer("straight")]


def halt():
    return [stop_motors(), steer("straight")]


def build():
    p, car = new_project("Touch remote: tap the buttons", car_x=-130, car_y=-40, car_size=90)
    p.var("speed", 40, monitor=dict(mode="slider", x=5, y=5, min=0, max=60))
    p.var("distance", 0, monitor=dict(mode="default", x=5, y=35))

    car.script(-200, 60, when_flag(),
               say("Press the green flag, then tap the arrows!"),
               wait(3), say(""),
               forever(set_var("distance", distance())))

    # (name, art, colour, x, y, key, actions)
    buttons = [
        ("Forward", "up", "#43A047", 100, 95, "up arrow", nudge_forward),
        ("Backward", "down", "#FB8C00", 100, -95, "down arrow", nudge_back),
        ("Left", "left", "#1E88E5", 5, 0, "left arrow", lambda: nudge_turn("left")),
        ("Right", "right", "#1E88E5", 195, 0, "right arrow", lambda: nudge_turn("right")),
        ("Stop", "stop", "#C62828", 100, 0, "space", halt),
    ]
    for name, art, color, x, y, key, actions in buttons:
        s = p.sprite(name, x=x, y=y)
        s.costume(name, button_svg(art, color), 45, 45)
        s.script(-200, 60, when_clicked(), *actions())
        s.script(-200, 300, when_key(key), *actions())
    return p
