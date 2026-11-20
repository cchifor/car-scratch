"""A tiny DSL for writing Scratch 3 projects (project.json + assets) from Python.

    p = Project()
    car = p.sprite("Car", x=0, y=0)
    car.costume("car", CAR_SVG, 60, 40)
    car.script(0, 0, when_flag(), say("Hi"), drive_for("forward", 40, 1), stop_motors())
    p.save_sb3("out/hello.sb3")

Blocks are plain nested calls.  An argument may be:
  number / str        -> a number / text shadow
  Blk                 -> a reporter or boolean plugged into the input
  list of Blk         -> a substack (SUBSTACK / SUBSTACK2)
  V("name")           -> a variable reporter
  Menu / Bc / Color   -> a shadow menu, a broadcast message, a colour
The output is deterministic: ids come from counters, zip timestamps are fixed.
"""
import hashlib
import io
import json
import math
import struct
import wave
import zipfile

SEMVER = "3.0.0"


class Blk:
    def __init__(self, opcode, inputs=None, fields=None):
        self.opcode = opcode
        self.inputs = inputs or {}
        self.fields = fields or {}


class V:
    """Reference to a (stage-wide) variable by name."""

    def __init__(self, name):
        self.name = name


class Menu:
    """A shadow menu block, e.g. Menu('picar_menu_sensor', 'sensor', 3)."""

    def __init__(self, opcode, field, value):
        self.opcode, self.field, self.value = opcode, field, value


class Bc:
    """A broadcast message, by name."""

    def __init__(self, name):
        self.name = name


class Color:
    def __init__(self, hexstr):
        self.hex = hexstr


def num(n):
    if isinstance(n, float) and n == int(n):
        n = int(n)
    return str(n)


def blk(opcode, fields=None, **inputs):
    return Blk(opcode, inputs, fields)


# ---------------------------------------------------------------- hats / events
def when_flag():
    return blk("event_whenflagclicked")


def when_clicked():
    return blk("event_whenthisspriteclicked")


def when_key(key):
    return blk("event_whenkeypressed", {"KEY_OPTION": key})


def when_broadcast(name):
    return blk("event_whenbroadcastreceived", {"BROADCAST_OPTION": Bc(name)})


def broadcast(name):
    return blk("event_broadcast", BROADCAST_INPUT=Bc(name))


# ---------------------------------------------------------------- control
def forever(*body):
    return blk("control_forever", SUBSTACK=list(body))


def repeat(times, *body):
    return blk("control_repeat", TIMES=times, SUBSTACK=list(body))


def repeat_until(cond, *body):
    return blk("control_repeat_until", CONDITION=cond, SUBSTACK=list(body))


def if_(cond, *body):
    return blk("control_if", CONDITION=cond, SUBSTACK=list(body))


def if_else(cond, then, otherwise):
    return blk("control_if_else", CONDITION=cond, SUBSTACK=list(then), SUBSTACK2=list(otherwise))


def wait(secs):
    return blk("control_wait", DURATION=secs)


def wait_until(cond):
    return blk("control_wait_until", CONDITION=cond)


# ---------------------------------------------------------------- operators / data
def _op2(opcode, a, b, names=("OPERAND1", "OPERAND2")):
    return blk(opcode, **{names[0]: a, names[1]: b})


def add(a, b): return _op2("operator_add", a, b, ("NUM1", "NUM2"))
def sub(a, b): return _op2("operator_subtract", a, b, ("NUM1", "NUM2"))
def mul(a, b): return _op2("operator_multiply", a, b, ("NUM1", "NUM2"))
def div(a, b): return _op2("operator_divide", a, b, ("NUM1", "NUM2"))
def lt(a, b): return _op2("operator_lt", a, b)
def gt(a, b): return _op2("operator_gt", a, b)
def eq(a, b): return _op2("operator_equals", a, b)
def and_(a, b): return _op2("operator_and", a, b)
def or_(a, b): return _op2("operator_or", a, b)
def not_(a): return blk("operator_not", OPERAND=a)
def join(a, b): return _op2("operator_join", a, b, ("STRING1", "STRING2"))


def any_of(*conds):
    out = conds[0]
    for c in conds[1:]:
        out = or_(out, c)
    return out


def set_var(name, value):
    return blk("data_setvariableto", {"VARIABLE": V(name)}, VALUE=value)


def change_var(name, by):
    return blk("data_changevariableby", {"VARIABLE": V(name)}, VALUE=by)


# ---------------------------------------------------------------- motion / looks / sound / pen
def goto_xy(x, y): return blk("motion_gotoxy", X=x, Y=y)
def point_dir(d): return blk("motion_pointindirection", DIRECTION=d)
def say(text): return blk("looks_say", MESSAGE=text)
def say_for(text, secs): return blk("looks_sayforsecs", MESSAGE=text, SECS=secs)
def set_size(pct): return blk("looks_setsizeto", SIZE=pct)
def show(): return blk("looks_show")
def hide(): return blk("looks_hide")


def switch_costume(name):
    return blk("looks_switchcostumeto", COSTUME=Menu("looks_costume", "COSTUME", name))


def play_sound(name):
    return blk("sound_play", SOUND_MENU=Menu("sound_sounds_menu", "SOUND_MENU", name))


def play_sound_done(name):
    return blk("sound_playuntildone", SOUND_MENU=Menu("sound_sounds_menu", "SOUND_MENU", name))


def pen_down(): return blk("pen_penDown")
def pen_up(): return blk("pen_penUp")
def pen_clear(): return blk("pen_clear")
def pen_size(n): return blk("pen_setPenSizeTo", SIZE=n)
def pen_color(hexstr): return blk("pen_setPenColorToColor", COLOR=Color(hexstr))


# ---------------------------------------------------------------- Pi Car (docs/contracts.md section 6)
def sensor(n): return Menu("picar_menu_sensor", "sensor", str(n))
def drive(direction, speed): return blk("picar_drive", {"dir": direction}, SPEED=speed)
def drive_for(direction, speed, secs): return blk("picar_driveFor", {"dir": direction}, SPEED=speed, SECS=secs)
def stop_motors(): return blk("picar_stopMotors")
def set_speed(speed): return blk("picar_setSpeed", SPEED=speed)
def steer(where): return blk("picar_steer", {"steer": where})
def set_steering(angle): return blk("picar_setSteering", ANGLE=angle)
def distance(): return blk("picar_distance")
def obstacle_closer(cm): return blk("picar_obstacleCloser", CM=cm)
def when_obstacle(cm): return blk("picar_whenObstacle", CM=cm)
def line_value(n): return blk("picar_lineValue", SENSOR=sensor(n))
def line_sees(n): return blk("picar_lineSees", SENSOR=sensor(n))
def line_position(): return blk("picar_linePosition")
def when_line_sees(n): return blk("picar_whenLineSees", SENSOR=sensor(n))
def light_value(where): return blk("picar_lightValue", {"light": where})
def brightest_direction(): return blk("picar_brightestDirection")
def obstacle_stop(onoff): return blk("picar_setObstacleStop", {"onoff": onoff})
def stop_distance(cm): return blk("picar_setStopDistance", CM=cm)
def speed_limit(pct): return blk("picar_setSpeedLimit", PERCENT=pct)
def emergency_stop(): return blk("picar_emergencyStop")
def car_x(): return blk("picar_carX")
def car_y(): return blk("picar_carY")
def car_direction(): return blk("picar_carDirection")
def reset_position(): return blk("picar_resetPosition")
def when_car(event): return blk("picar_whenCarEvent", {"event": event})


# ---------------------------------------------------------------- assets
def make_wav(freq=880, ms=80, rate=22050, volume=0.6):
    """Mono 16-bit PCM beep with a short fade in/out (no clicks).  Returns (bytes, samples, rate)."""
    n = rate * ms // 1000
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / (rate * 0.005), (n - 1 - i) / (rate * 0.005))
        s = int(32767 * volume * env * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue(), n, rate


class Target:
    def __init__(self, project, name, is_stage):
        self.project, self.name, self.is_stage = project, name, is_stage
        self.costumes, self.sounds, self.scripts = [], [], []

    def costume(self, name, svg, cx, cy):
        data = svg.encode("utf-8")
        aid = self.project._asset(data, "svg")
        self.costumes.append({"assetId": aid, "name": name, "bitmapResolution": 1,
                              "md5ext": aid + ".svg", "dataFormat": "svg",
                              "rotationCenterX": cx, "rotationCenterY": cy})
        return self

    def sound(self, name, wav):
        data, count, rate = wav
        aid = self.project._asset(data, "wav")
        self.sounds.append({"assetId": aid, "name": name, "dataFormat": "wav", "format": "",
                            "rate": rate, "sampleCount": count, "md5ext": aid + ".wav"})
        return self

    def script(self, x, y, *blocks):
        """A top-level script: blocks[0] is the hat; the rest are its stack."""
        self.scripts.append((x, y, list(blocks)))
        return self

    def _json(self, layer):
        blocks = {}
        for x, y, stack in self.scripts:
            self.project._stack(stack, None, blocks, top=(x, y))
        return {"isStage": self.is_stage, "name": self.name, "variables": {}, "lists": {},
                "broadcasts": {}, "blocks": blocks, "comments": {}, "currentCostume": 0,
                "costumes": self.costumes, "sounds": self.sounds, "volume": 100,
                "layerOrder": layer}


class Stage(Target):
    pass


class Sprite(Target):
    def __init__(self, project, name, x, y, size, direction, visible):
        super().__init__(project, name, False)
        self.x, self.y, self.size, self.direction, self.visible = x, y, size, direction, visible


class Project:
    def __init__(self):
        self.stage = Stage(self, "Stage", True)
        self.sprites = []
        self.variables = {}      # name -> value (stage-wide)
        self.monitors = []
        self.broadcasts = {}     # id -> name
        self.assets = {}         # md5ext -> bytes
        self.uses_pen = False
        self._n = 0

    # -- building
    def sprite(self, name, x=0, y=0, size=100, direction=90, visible=True):
        s = Sprite(self, name, x, y, size, direction, visible)
        self.sprites.append(s)
        return s

    def var(self, name, value=0, monitor=None):
        """Declare a stage variable.  monitor = dict(x, y, mode='default'|'slider'|'large', min, max)."""
        self.variables[name] = value
        if monitor:
            m = {"id": self._vid(name), "mode": monitor.get("mode", "default"),
                 "opcode": "data_variable", "params": {"VARIABLE": name}, "spriteName": None,
                 "value": value, "width": 0, "height": 0, "x": monitor.get("x", 5),
                 "y": monitor.get("y", 5), "visible": True}
            if m["mode"] == "slider":
                m.update(sliderMin=monitor.get("min", 0), sliderMax=monitor.get("max", 100),
                         isDiscrete=True)
            self.monitors.append(m)

    # -- ids
    @staticmethod
    def _vid(name):
        return "var-" + name

    @staticmethod
    def _bid(name):
        return "bc-" + name

    def _asset(self, data, ext):
        aid = hashlib.md5(data).hexdigest()
        self.assets[aid + "." + ext] = data
        return aid

    def _new_id(self):
        self._n += 1
        return "b%04d" % self._n

    # -- block flattening
    def _stack(self, stack, parent, blocks, top=None):
        prev, first = None, None
        for i, b in enumerate(stack):
            bid = self._block(b, prev if prev else parent, blocks, top if i == 0 else None)
            if prev:
                blocks[prev]["next"] = bid
            else:
                first = bid
            prev = bid
        return first

    def _input(self, value, parent, blocks):
        if isinstance(value, Blk):
            return [3, self._block(value, parent, blocks, None), [10, ""]]
        if isinstance(value, V):
            if value.name not in self.variables:
                raise KeyError("undeclared variable " + value.name)
            return [3, [12, value.name, self._vid(value.name)], [10, ""]]
        if isinstance(value, Menu):
            mid = self._new_id()
            blocks[mid] = {"opcode": value.opcode, "next": None, "parent": parent, "inputs": {},
                           "fields": {value.field: [str(value.value), None]},
                           "shadow": True, "topLevel": False}
            return [1, mid]
        if isinstance(value, Bc):
            self.broadcasts[self._bid(value.name)] = value.name
            return [1, [11, value.name, self._bid(value.name)]]
        if isinstance(value, Color):
            return [1, [9, value.hex]]
        if isinstance(value, bool):
            raise TypeError("bool input")
        if isinstance(value, (int, float)):
            return [1, [4, num(value)]]
        return [1, [10, str(value)]]

    def _field(self, value):
        if isinstance(value, V):
            return [value.name, self._vid(value.name)]
        if isinstance(value, Bc):
            self.broadcasts[self._bid(value.name)] = value.name
            return [value.name, self._bid(value.name)]
        return [str(value), None]

    def _block(self, b, parent, blocks, top):
        if b.opcode.startswith("pen_"):
            self.uses_pen = True
        bid = self._new_id()
        d = {"opcode": b.opcode, "next": None, "parent": parent, "inputs": {}, "fields": {},
             "shadow": False, "topLevel": top is not None}
        blocks[bid] = d
        for name, value in b.inputs.items():
            if isinstance(value, list):
                first = self._stack(value, bid, blocks)
                if first:
                    d["inputs"][name] = [2, first]
            else:
                d["inputs"][name] = self._input(value, bid, blocks)
        for name, value in b.fields.items():
            d["fields"][name] = self._field(value)
        if top is not None:
            d["x"], d["y"] = top
        return bid

    # -- output
    def to_json(self):
        self._n = 0
        self.broadcasts = {}
        self.uses_pen = False
        stage = self.stage._json(0)
        targets = [stage]
        for i, s in enumerate(self.sprites):
            d = s._json(i + 1)
            d.update(visible=s.visible, x=s.x, y=s.y, size=s.size, direction=s.direction,
                     draggable=False, rotationStyle="all around")
            targets.append(d)
        stage["variables"] = {self._vid(n): [n, v] for n, v in self.variables.items()}
        stage["broadcasts"] = dict(sorted(self.broadcasts.items()))
        stage.update(tempo=60, videoTransparency=50, videoState="off", textToSpeechLanguage=None)
        ext = ["picar"] + (["pen"] if self.uses_pen else [])
        return {"targets": targets, "monitors": self.monitors, "extensions": ext,
                "meta": {"semver": SEMVER, "vm": "0.2.0", "agent": "Car Scratch examples"}}

    def save_sb3(self, path):
        pj = json.dumps(self.to_json(), separators=(",", ":"), ensure_ascii=True).encode()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in [("project.json", pj)] + sorted(self.assets.items()):
                zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                z.writestr(zi, data)
