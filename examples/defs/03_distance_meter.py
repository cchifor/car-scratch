from _common import new_project
from sb3 import *

META = dict(id="03-distance-meter", title="Distance meter", level="beginner", needs=["ultrasonic"],
            world="corridor",
            description="The car stays still. A bar and a graph show how far the nearest thing is.")

BAR_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="24" viewBox="0 0 100 24">'
           '<rect width="100" height="24" fill="#43A047"/></svg>')
DOT_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="6" height="6" viewBox="0 0 6 6">'
           '<circle cx="3" cy="3" r="3" fill="#1E88E5"/></svg>')


def build():
    p, car = new_project("Distance meter: press the green flag", car_x=190, car_y=140, car_size=50)
    p.var("d", 0)
    p.var("t", -220)

    car.script(-200, 60, when_flag(),
               forever(say(join(join("Distance: ", distance()), " cm")), wait(0.2)))

    # The bar is 100 px wide at size 100, so size = distance in cm gives 1 px per cm.
    bar = p.sprite("Bar", x=-220, y=-140)
    bar.costume("bar", BAR_SVG, 0, 12)
    bar.script(-200, 60, when_flag(),
               goto_xy(-220, -140),
               forever(
                   set_var("d", distance()),
                   if_(gt(V("d"), 200), set_var("d", 200)),
                   if_(lt(V("d"), 3), set_var("d", 3)),
                   set_size(V("d")),
                   wait(0.1)))

    graph = p.sprite("Graph", x=-220, y=-40, visible=False)
    graph.costume("dot", DOT_SVG, 3, 3)
    graph.script(-200, 60, when_flag(),
                 pen_clear(), pen_size(3), pen_color("#1E88E5"),
                 set_var("t", -220),
                 pen_up(),
                 forever(
                     set_var("d", distance()),
                     if_(gt(V("d"), 200), set_var("d", 200)),
                     if_(gt(V("t"), 220), pen_up(), pen_clear(), set_var("t", -220)),
                     goto_xy(V("t"), sub(mul(V("d"), 0.6), 40)),
                     pen_down(),
                     change_var("t", 2),
                     wait(0.1)))
    return p
