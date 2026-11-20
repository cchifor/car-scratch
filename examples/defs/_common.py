"""Shared pieces for the example projects: backdrop, Car sprite, button art."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

from sb3 import Project  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
CAR_SVG = (ROOT / "assets" / "branding" / "car-sprite.svg").read_text(encoding="utf-8")


def backdrop_svg(caption):
    lines = []
    for x in range(0, 481, 40):
        lines.append('<line x1="%d" y1="0" x2="%d" y2="360" stroke="#CFD8DC" stroke-width="1"/>' % (x, x))
    for y in range(0, 361, 40):
        lines.append('<line x1="0" y1="%d" x2="480" y2="%d" stroke="#CFD8DC" stroke-width="1"/>' % (y, y))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360" viewBox="0 0 480 360">'
            '<rect width="480" height="360" fill="#F5F7F8"/>' + "".join(lines) +
            '<text x="240" y="350" font-family="Helvetica, Arial, sans-serif" font-size="14" '
            'text-anchor="middle" fill="#78909C">%s</text></svg>' % caption)


def new_project(caption, car_x=0, car_y=0, car_size=100):
    """A project with a grid backdrop and the Car sprite.  Returns (project, car)."""
    p = Project()
    p.stage.costume("grid", backdrop_svg(caption), 240, 180)
    car = p.sprite("Car", x=car_x, y=car_y, size=car_size, direction=90)
    car.costume("car", CAR_SVG, 60, 40)
    return p, car


def button_svg(kind, color="#1E88E5"):
    """A 90x90 rounded button with an arrow / stop square.  kind: up|down|left|right|stop."""
    shapes = {
        "up": '<path d="M45 18 L72 58 L18 58 Z" fill="#FFFFFF"/>',
        "down": '<path d="M45 72 L72 32 L18 32 Z" fill="#FFFFFF"/>',
        "left": '<path d="M18 45 L58 18 L58 72 Z" fill="#FFFFFF"/>',
        "right": '<path d="M72 45 L32 18 L32 72 Z" fill="#FFFFFF"/>',
        "stop": '<rect x="25" y="25" width="40" height="40" rx="5" fill="#FFFFFF"/>',
    }
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="90" height="90" viewBox="0 0 90 90">'
            '<rect x="2" y="2" width="86" height="86" rx="18" fill="%s" stroke="#0D47A1" stroke-width="3"/>'
            '%s</svg>' % (color, shapes[kind]))
