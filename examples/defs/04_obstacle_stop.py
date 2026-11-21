from _common import new_project
from sb3 import *

META = dict(id="04-obstacle-stop", title="Obstacle stop", level="beginner", needs=["ultrasonic"],
            world="corridor",
            description="Drive forward until something is closer than 45 cm, then stop and say how far it is.")


def build():
    p, car = new_project("Obstacle stop: press the green flag", car_x=-80)
    car.script(
        -200, 60, when_flag(),
        stop_distance(25),
        say("Driving until I see a wall."),
        drive("forward", 40),
        # 45 cm: the car's own safety stop (25 cm + braking distance at 40 %, about 33 cm) must not stop it
        # first, or this would wait for ever
        wait_until(obstacle_closer(45)),
        stop_motors(),
        say(join(join("I stopped! The wall is ", distance()), " cm away.")),
    )
    return p
