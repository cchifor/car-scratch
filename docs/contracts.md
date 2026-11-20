# Car Scratch internal contracts

Shared by the parallel work tracks. `docs/protocol.md` is the wire contract; this file covers safety
semantics, the Python HAL, the simulator world format, calibration, the browser `CarLink` API, the
Scratch blocks, and the GUI integration points. Change a contract only together with every consumer.

## 1. Safety semantics (server, authoritative)

Every controller (the shared car, and each private sim) applies these rules.

| Rule | Behaviour |
|---|---|
| Obstacle guard | Runs on **every sensor sample** (sensor thread on the real car). If moving forward and `dist.cm < margin_cm`, or the ultrasonic is in `fault`, call `backend.stop_motors()` immediately, then (on the loop) emit `event obstacle_stop`, finish a timed move with `done{obstacle}`, set `blocked`. While `blocked`, forward motion → `err obstacle_blocked`; backward and steering allowed. `blocked` clears when a sample shows `dist` ≥ `margin_cm + 5` or `far`. |
| Margin | `margin_cm = stop_cm + coast_cm(speed) + speed_cm_s(speed) × 0.1 s` (tables in §3). Default `stop_cm` 20. Guard on by default; `config{obstacle_stop:false}` disables it (telemetry shows it; the UI shows a permanent banner). |
| Watchdog | Controller silent (no frame) for > `wd_ms` (750 ms) **while motors run** → stop, `event watchdog_stop`, `done{watchdog}`, latch **hold**. |
| Hold | Motion refused (`err hold`) until a `resume` command. |
| Disconnect | Controller socket closes → stop immediately (`done{disconnected}`), lease starts. |
| E-stop | `estop` from anyone → stop, latch; motion refused (`err estop`) until the controller sends `estop_reset`. |
| Reverse | Backward speed capped at 40; any backward drive auto-stops after 5000 ms (`event reverse_timeout`, `done{reverse_timeout}`). |
| Timed moves | Capped at 30000 ms; run as server tasks; cancelled by any newer motion command. |
| Speed limit | Effective speed = `min(speed, speed_limit)`; default `speed_limit` 100. |
| Sensor freshness | No ultrasonic sample for > 500 ms while moving forward → stop as for a fault. |
| Loop liveness | A watchdog thread stops the motors if the asyncio loop has not ticked for > 1 s (real backend). |
| HW fault | A motor write that fails twice → `hw_fault` latched (like e-stop, cleared by `estop_reset` after a successful zero write), `event hw_fault`. |

Pure helpers live in `server/picar_server/safety.py` and are reused by the sim.

## 2. Python HAL (`server/picar_server/hal/`)

```python
@dataclass(frozen=True)
class Sample:
    t: float                     # time.monotonic()
    dist_cm: float | None        # None when far or fault
    dist_state: str              # "ok" | "far" | "fault"
    line_raw: list[int] | None   # 5 values (sensor 1 = leftmost) or None when the module is absent
    light_raw: list[int] | None  # 3 values 0..255 (left, middle, right) or None

class Backend(Protocol):
    name: str                                   # "real" | "dryrun" | "sim"
    def capabilities(self) -> dict[str, bool]: ...   # {"ultrasonic", "light", "line"}; line may change at runtime
    def start(self, on_sample: Callable[[Sample], None]) -> None: ...
        # real/dryrun: starts the sensor thread, on_sample is called FROM THAT THREAD (~25 Hz).
        # sim: requires a running asyncio loop, calls on_sample from the loop (25 Hz) and runs physics at 50 Hz.
    def set_motors(self, left: int, right: int) -> None: ...   # signed duty -4095..4095, sign = direction
    def set_steering(self, angle_deg: float) -> None: ...      # -45..45 from straight (neg = left); trim applied inside
    def stop_motors(self) -> None: ...          # zero both motors; idempotent; NEVER raises; any thread
    def pose(self) -> tuple[float, float, float] | None: ...    # sim: true pose (x, y, scratch_dir); real: None
    def close(self) -> None: ...                # stop motors, stop threads
```

Real backend (measured hardware, see `server/legacy/obstacle_stop.py`):

- PCA9685 at I2C bus 1, address `0x40`, 60 Hz (prescale 101). One `threading.Lock` guards all I2C
  traffic (PCA9685, PCF8591 `0x48`, line follower `0x11`).
- **Right motor**: PWM ch4, direction GPIO27. **Left motor**: PWM ch5, direction GPIO17.
  Direction pin **HIGH = forward** (both). Use `RPi.GPIO` (rpi-lgpio shim on the Pi).
- Steering: PWM ch0, `angle = clamp(90 + trim + angle_deg, 45, 135)`,
  `ticks = round((600 + 1800 * angle / 180) / 1e6 * 60 * 4096)` (90° → 369).
- Ultrasonic: single SIG pin GPIO5 (output-low 2 ms, high 10 µs, low, switch to input, time the echo).
  Echo-start timeout 30 ms → no answer (`None`); echo longer than 12 ms → `far` (> ~2 m).
  Median of 3 pings when the last reading was < 2 × stop margin or the car moves forward; else 1 ping.
  3 consecutive no-answers → `fault`.
- PCF8591: for ch in 0..2: `write_byte(0x48, 0x40|ch)`, discard one read, then read.
- Line follower: `read_i2c_block_data(0x11, 0, 10)` → 5 big-endian 16-bit values. On error: report
  `None`, re-probe with backoff 2 s → 10 s, log only state changes.
- `safe_stop.py`: standalone (no aiohttp import), zeroes ch4/ch5, exits 0 even if the chip is missing.
- dryrun backend: real sensors, motor/servo writes only logged.

## 3. Calibration (`~/.config/car-scratch/calibration.json`, all optional)

```json
{"steer_trim_deg": 0.0,
 "duty_floor_pct": 25, "duty_ceiling_pct": 100,
 "speed_table": [[0,0],[25,0],[30,5],[40,15],[50,25],[60,35],[80,55],[100,70]],
 "coast_table": [[0,0],[50,3],[100,12]],
 "wheelbase_cm": 14.0, "ultrasonic_offset_cm": 12.0, "line_sensor_spacing_cm": 2.0,
 "line_threshold": 300, "light_dark": 200, "light_bright": 20}
```

- User speed 0..100 → duty %: `0` if speed is 0, else `floor + (ceiling − floor) × speed / 100`;
  duty value = `round(4095 × duty% / 100)`.
- `speed_table`: duty % → cm/s (dead reckoning, margin). `coast_table`: user speed → cm rolled after stop.
- Light: `bright = clamp(100 × (light_dark − raw) / (light_dark − light_bright), 0, 100)`
  (on this module a lower raw value means more light; confirm on the car).
- Line: sensor sees line when `raw < line_threshold`.
- Legacy `~/picar/calibration.json` `{"steer_trim_deg": x}` is migrated on first start.

## 4. Simulator worlds (`server/picar_server/hal/sim/worlds/<id>.json`)

Coordinates in cm, origin at the world centre, +x right, +y up (same as the Scratch stage).
Headings in Scratch degrees (0 = up, 90 = right, clockwise).

```json
{"id":"corridor","title":"Corridor (obstacle stop)","size":[400,200],
 "walls":[[-200,-100,200,-100],[200,-100,200,100]],
 "line":{"points":[[-150,0],[150,0]],"closed":false,"width":2.0},
 "lights":[{"x":150,"y":60,"intensity":1.0}],
 "start":{"x":-150,"y":0,"dir":90}}
```

The same object is sent to clients as the `world` message (`{"t":"world", ...}`) so the Car Monitor
draws exactly what the physics uses. Worlds: `arena`, `corridor`, `oval`, `lamp`, `maze`.
Default world: `arena`.

Car geometry for drawing and physics: body 26 × 16 cm, rear axle at the pose point, wheelbase from
calibration, ultrasonic at the front centre, 5 line sensors across the front underside.

## 5. Browser `CarLink` (`scratch-vm/src/extensions/scratch3_picar/car-link.js`)

```js
class CarLink extends EventEmitter {           // 'events' package
  constructor ({url, WebSocketImpl = WebSocket, clock = {now, setTimeout, clearTimeout, setInterval, clearInterval},
                doc = globalThis.document, storage = globalThis.localStorage})
  start ()                                     // connect + auto-reconnect forever
  stop ()                                      // close, no reconnect
  setMode (mode /* 'car'|'sim' */, world)      // persisted in storage 'picar.mode'
  send (op, fields)  -> id                     // fire-and-forget cmd; dropped (returns null) when not open
  move (dir, speed, ms) -> Promise<result>     // always resolves: done.result | 'disconnected' | 'timeout'
  control (action, force)
  getState () -> {connection: 'connecting'|'open'|'closed', role, mode, carAvailable, hw, config,
                  safety, world, worlds, server, lastError}           // immutable snapshot
  subscribe (fn) -> unsubscribe                // fires on coarse state changes only (not every tel)
  getTelemetry () -> last tel or null
  // events: 'telemetry' (tel), 'event' (evt), 'world' (world), 'state' (state), 'role' (msg), 'error' (err)
}
```

- `client_id`: random id persisted in storage `picar.clientId` (no `crypto.randomUUID`: not a secure context).
- Heartbeat: `hb` every `hb_ms` via `setInterval`.
- Reconnect: backoff 0.5, 1, 2, 4, 5 s (+ up to 250 ms jitter); immediate reconnect on `visibilitychange`
  → visible, `pageshow`, `online`. No frame for 2 s while open → force reconnect.
- On `visibilitychange` → hidden and `pagehide`: send `stop` (best effort).
- Never replays commands after a reconnect; pending `move` promises resolve `'disconnected'`.
- Default mode: storage value, else `car` if `welcome.car_available`, else `sim`.

## 6. Scratch blocks (extension id `picar`, category name "Pi Car")

Opcodes (`picar_<opcode>` in project.json). Menus accept reporters where marked *.

| opcode | type | text | args / menus |
|---|---|---|---|
| `drive` | command | drive [DIR] at [SPEED] % | DIR menu `dir` (forward, backward); SPEED number 50 |
| `driveFor` | command (async) | drive [DIR] at [SPEED] % for [SECS] seconds | SECS number 1 |
| `stopMotors` | command | stop motors | |
| `setSpeed` | command | change speed to [SPEED] % | |
| `steer` | command | steer [STEER] | STEER menu `steer` (left, straight, right) = −30, 0, +30° |
| `setSteering` | command | set steering angle to [ANGLE] ° | ANGLE number 0 (−45..45) |
| `distance` | reporter | distance (cm) | 999 when far, 0 when fault/disconnected |
| `obstacleCloser` | boolean | obstacle closer than [CM] cm? | CM number 20 |
| `whenObstacle` | hat (edge) | when obstacle closer than [CM] cm | CM number 20 |
| `lineValue` | reporter | line sensor [SENSOR] value | SENSOR menu `sensor`* (1..5) |
| `lineSees` | boolean | line sensor [SENSOR] sees line? | |
| `linePosition` | reporter | line position | −2..2, 0 when no line |
| `whenLineSees` | hat (edge) | when line sensor [SENSOR] sees line | |
| `lightValue` | reporter | [LIGHT] light brightness | LIGHT menu `light` (left, middle, right) |
| `brightestDirection` | reporter | brightest light direction | left/middle/right/none |
| `setObstacleStop` | command | turn obstacle stop [ONOFF] | ONOFF menu `onoff` (on, off) |
| `setStopDistance` | command | set obstacle stop distance to [CM] cm | |
| `setSpeedLimit` | command | set speed limit to [PERCENT] % | PERCENT number 100 |
| `emergencyStop` | command | emergency stop | |
| `isConnected` | boolean | car connected? | |
| `carMode` | reporter | car mode | `car` / `simulator` / `offline` |
| `carX` / `carY` / `carDirection` | reporter | car x / car y / car direction | pose from telemetry |
| `resetPosition` | command | reset car position | |
| `whenCarEvent` | hat (event) | when car [EVENT] | EVENT menu `event` (stops for obstacle, is emergency stopped, loses connection) |

- Commands are fire-and-forget (return immediately); `driveFor` returns the `move` promise.
- Reporters/booleans read `getTelemetry()`; safe defaults when disconnected (0 / false / `offline`).
- `PROJECT_START` (green flag) → `resume`. `PROJECT_STOP_ALL` (stop sign, project load) → `stop`, then `resume`.
- `whenCarEvent` is triggered with `runtime.startHats('picar_whenCarEvent', {EVENT: value})` from server events.

## 7. GUI integration

- The extension registers itself with `runtime.registerPeripheralExtension('picar', this)` and exposes
  `this.link` (the CarLink). GUI code gets it through `vm.runtime.peripheralExtensions.picar.link`
  (re-check on the VM `EXTENSION_ADDED` event). Helper hook: `scratch-gui/src/lib/picar/use-car-link.js`.
- `scratch-gui/src/lib/vm-manager-hoc.jsx` loads the `picar` extension once when the VM initialises.
- `?picar_debug=1` exposes `window.__picar = {link, vm}` for e2e tests.
- The Car Monitor subscribes to `link.subscribe` (React state) and `link.on('telemetry')` (canvas,
  no React re-render per frame).
