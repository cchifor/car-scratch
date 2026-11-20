from _common import new_project
from sb3 import *

META = dict(id="06-parking-sensor", title="Parking sensor", level="intermediate",
            needs=["ultrasonic"], world="corridor",
            description="Creep forward slowly. The beeps come faster the closer the wall gets.")


def build():
    p, car = new_project("Parking sensor: press the green flag", car_x=-80)
    car.sound("beep", make_wav(880, 80, 22050))
    p.var("gap", 100)
    car.script(
        -200, 60, when_flag(),
        stop_distance(15),
        say("Beep beep... closer means faster!"),
        drive("forward", 25),
        repeat_until(obstacle_closer(20),
                     play_sound_done("beep"),
                     set_var("gap", distance()),
                     if_(gt(V("gap"), 150), set_var("gap", 150)),
                     if_(lt(V("gap"), 10), set_var("gap", 10)),
                     wait(div(V("gap"), 200))),
        stop_motors(),
        say("Parked!"),
    )
    return p
