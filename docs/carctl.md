# carctl: monitor and drive the car from a terminal

`carctl` is a terminal app for watching the car's telemetry live and, when you want, driving it with the keyboard.
It speaks the normal WebSocket protocol ([protocol.md](protocol.md)), so it follows the same rules as the Scratch
editor: one controller at a time, every safety decision on the server.

## What it shows

`carctl` (same as `carctl watch`) opens a full-screen dashboard. It joins as a **viewer** and never sends motion.

| Panel | Contents |
|---|---|
| Header | URL, server version and commit, car or sim mode (and hardware backend), your role, connection state and reconnect count, other clients (from `GET /api/status`) |
| Safety | E-STOP, hold (with the reason), obstacle blocked, obstacle stop on/off, stop distance, current margin, speed limit, calibrated, hardware fault |
| Drive | direction, speed, duty left/right, steering angle, timed move remaining (in `drive`: your target and the speed the server really applied) |
| Distance | centimetres with a bar, and the state `ok` / `far` / `none` / `fault` |
| Line follower | the 5 sensors (raw value, bar, on/off), the position -2..2 on a little track, or "offline" |
| Light | left / middle / right brightness and raw values, brightest direction |
| Pose | x, y, heading, source (dead reckoning or simulator) |
| Power | source, low now, low since boot, low events, throttled, on time, motor time, CPU temperature and load (or "no power data") |
| Events | the last events with local times: `obstacle_stop`, `estop`, `hold`, `resumed`, `sensor_fault`, `power_low`, `control_changed`, ... |
| Footer | the last error, and the key help in `drive` |

Terminals at least 100 columns by 38 rows get the panel layout; smaller ones (80x24, a phone ssh client) get a compact
one-line-per-sensor layout that shows the same data, with as many events as fit. If the socket drops, the header shows
`reconnecting` and carctl reconnects on its own.

Compact layout (80x24), as rendered by the tests:

```
carctl drive  ws://picar.local:8080/ws
link connected  role controller  mode car/real
server v1.4.0 (abc1234)  clients controller yes, viewers 1, sims 0
SAFETY estop ok  running  blocked no  hw ok
        obstacle-stop on  stop 20cm  margin 24.5  limit 100  cal no
DRIVE  forward 40  duty 1720/1720  steer -12  | target +40/-10 server 35
DIST    42.3 cm ███░░░░░░░░░ ok
LINE   ● ○ ○ ○ ●  pos +0  raw [900, 120, 80, 130, 910]
LIGHT  L/M/R 41/63/30%  brightest middle
POSE   x 12.4 y 31.0 dir 86 (dr)
POWER  low no since-boot YES events 0 throttled no
        on 0:21:00 motor 0:01:24 cpu 52C load 0.31
EVENTS
22:13:24 control_changed controller_present=True
22:13:23 estop by=ws
22:13:22 obstacle_stop cm=18.2
22:13:21 resumed
22:13:20 hold reason=control
last error 22:13:30 drive: hold: motion is on hold
Up/Down speed  Left/Right steer  C center  Space stop  R resume  E E-STOP  X reset  Q quit
```

## Running it

On the car (the launcher is installed by `deploy/pi-setup.sh`; `-t` is needed for the keyboard and the full screen):

```
ssh -t c4@picar.local carctl
ssh -t c4@picar.local carctl drive
```

From a workstation in the repo (Python 3.11+, `pip install aiohttp rich`):

```
python server/tools/carctl.py --url ws://192.168.0.209:8080/ws
python server/tools/carctl.py --host picar.local drive
cd server && python -m tools.carctl --host 192.168.0.209:8080 status
```

Windows PowerShell:

```powershell
py -m pip install aiohttp rich
py server\tools\carctl.py --url ws://192.168.0.209:8080/ws drive
```

The default URL is `ws://picar.local:8080/ws`; `--host HOST[:PORT]` is a shortcut (port 8080 by default).
Without `rich` it exits with an install hint (`sudo apt install python3-rich`, or `pip install rich`).

`--sim [WORLD]` connects in simulator mode, so you can practise without the car:
`carctl drive --sim` or `carctl --sim maze watch`.

## Driving: `carctl drive`

Terminals have no key-up events, so driving is cruise-control style: a key changes the **target**, and the car keeps
doing it until you change it.

| Key | Action |
|---|---|
| Up / W | speed +10 (signed: below 0 is backward) |
| Down / S | speed -10 |
| Left / A | steer -10 degrees (clamped to -45..45) |
| Right / D | steer +10 degrees |
| C | center the steering |
| Space | stop (speed 0) |
| R | resume: clear hold (the car starts on hold) |
| E | E-STOP (latched) |
| X | reset the E-STOP |
| Q or Ctrl-C | quit: stop, release control, exit |

Speed 0 sends `stop`; any other speed sends `drive` with the direction and `abs(speed)`. The server caps reverse at
40 and applies the speed limit, so the Drive panel shows both your target and the speed the server acknowledged.
After an obstacle stop, a watchdog stop or any other safety stop your target resets to 0.

`drive` takes control. If another client is the controller it exits with a message; add `--force` to take control
from it (the car is stopped first).

## One-shot commands

Exit code 0 on success, 1 on an error reply or a refusal. Commands that need control take it (`--force` to take it
from another controller).

| Command | What it does |
|---|---|
| `carctl status [--json]` | one snapshot. `--json` prints the raw `welcome`, one `tel` frame and `/api/status` |
| `carctl estop` | latch the E-STOP (works from a viewer) |
| `carctl reset` | reset the E-STOP |
| `carctl resume` | clear hold. The hold latches again when carctl disconnects, so this is mainly for checking the car accepts it; use `drive` for a session |
| `carctl stop` | stop the motors |
| `carctl steer ANGLE` | set the steering angle |
| `carctl move forward\|backward SPEED MS` | resume, run a timed move on the server, wait for `done`; exit 0 only for `completed` |
| `carctl events` | print events, errors and connection changes as lines until Ctrl-C |

## Safety notes

- It starts **on hold** like every controller: press R (or run `move`, which resumes first) before the car moves.
- E is the E-STOP key; X resets it. `carctl estop` works from any terminal, even as a viewer.
- On exit (Q, Ctrl-C, an exception, or the terminal closing) carctl sends `stop` and releases control.
- If the terminal or the network freezes, the heartbeat stops and the server watchdog (750 ms) stops the car and
  latches hold. After a dropped connection carctl reconnects, drops your old target (the car is stopped and on hold)
  and you press R to continue.
- Obstacle stop is still enforced by the server. Reverse is capped at 40 and limited to 5 s.
