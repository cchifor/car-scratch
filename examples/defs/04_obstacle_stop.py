from _common import new_project
from sb3 import *

META = dict(id="04-obstacle-stop", title="Obstacle stop", level="beginner", needs=["ultrasonic"],
            world="corridor",
            description="Drive forward until something is closer than 30 cm, then stop and say how far it is.")


def build():
    p, car = new_project("Obstacle stop: press the green flag", car_x=-80)
    car.script(
        -200, 60, when_flag(),
        stop_distance(25),
        say("Driving until I see a wall."),
        drive("forward", 40),
        wait_until(obstacle_closer(30)),
        stop_motors(),
        say(join(join("I stopped! The wall is ", distance()), " cm away.")),
    )
    return p
