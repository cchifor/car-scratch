# Car Scratch internal contracts

Shared by the parallel work tracks. `docs/protocol.md` is the wire contract; this file covers safety
semantics, the Python HAL, the simulator world format, calibration, the browser `CarLink` API, the
Scratch blocks, and the GUI integration points. Change a contract only together with every consumer.

## 1. Safety semantics (server, authoritative)

Every controller (the shared car, and each private sim) applies these rules.

| Rule | Behaviour |
|---|---|
| Obstacle guard | Runs on **every sensor sample** (sensor thread on the real car). If the commanded motion is forward (read from the backend's motion snapshot, §2) and `dist.cm < margin_cm`, or the ultrasonic is in `fault`, or the last `ok`/`far` sample is older than 150 ms (consecutive `none` samples), then `backend.inhibit_forward()` (stops the motors and latches the inhibit **under the motor lock, in the sensor thread**), then hand off to the loop: `event obstacle_stop`, `done{obstacle}` for a timed move, `blocked = true`. Backward and steering stay allowed. |
| Blocked clear | `blocked` (and the backend inhibit) clears only after **≥ 1 s of consecutive** samples with `dist ≥ margin_cm(current speed setting) + 5` or `far`. |
| Command-time check | A forward motor write is refused with `err obstacle_blocked` when `blocked`, or when the latest `dist.cm < margin_cm(proposed effective speed)` (guard on), or with `err sensor_fault` when the sensor is not valid. |
| Margin | `margin_cm = stop_cm + coast_cm(s) + speed_cm_s(s) × 0.2 s` (0.2 s = sensing + reaction allowance), where `s` = effective user speed (after `speed_limit`), `coast_cm` from `coast_table[s]`, `speed_cm_s` from `speed_table[duty%(s)]` (§3). Default `stop_cm` 20. |
| Guard off | `config{obstacle_stop:false}` disables only the **distance** stop (telemetry shows it; UI shows a permanent banner). Sensor **fault/freshness/startup** protection stays on. While off, a forward `drive` auto-stops after **5 s** without a fresh motion command (`event drive_timeout`). |
| Sensor validity | Before the first valid sample, after 3 consecutive `none` samples (`fault`), or when the last `ok`/`far` sample is older than 150 ms: forward motion is stopped/refused with `event sensor_fault` / `err sensor_fault` (never `obstacle_stop`, so avoidance hats do not fire). Backward motion is still allowed. |
| One motor path | Every motor write (drive, move start, `set_speed`, `config` change, speed-limit change) goes through **one** function that applies limits, the command-time obstacle check at the *proposed* effective speed, and the inhibit latch. Lowering `speed_limit` reduces running motion immediately. Each motion gets a **generation number**; timers and deferred callbacks may only touch the motors if their generation is still current. |
| Server start | The shared car starts **on hold** (a restart never resumes old motion). |
| Mode/control transitions | Switching mode, losing control, `world` change and sim `reset_pose` stop motion first (and latch hold for the car controller). |
| Watchdog | Controller silent (no **accepted** JSON frame — rate-limited or rejected frames do not count) for > `wd_ms` (750 ms) → stop and latch **hold** (whether or not the motors run), `event watchdog_stop` if they were running, `done{watchdog}` for a timed move. |
| Pause | `cmd pause` (tab hidden) → stop + latch hold. |
| Hold | Motion refused (`err hold`) until `resume`. Set by watchdog, pause, and controller disconnect. |
| Disconnect | Controller socket closes → stop immediately, latch hold, 30 s lease for the `client_id`. |
| E-stop | `estop` from anyone (any mode) → stop, latch on the shared car (+ own sim). Motion refused (`err estop`) until the controller sends `estop_reset`. Also `POST /api/estop`. |
| Reverse | Backward speed clamped to 40 (also via `set_speed`). The **cumulative** backward-motion time of an episode is capped at 5000 ms, unaffected by replacement commands; it resets only after ≥ 1 s stopped or after forward motion. At the cap: stop, `event reverse_timeout` (`done{reverse_timeout}` for a move), then backward commands get `err reverse_timeout` until the reset condition. |
| Timed moves | Capped at 30000 ms; run as server tasks; cancelled by any newer motion command. `set_speed` changes a running move's speed. |
| Speed limit | Effective speed = `min(speed, speed_limit)`; default `speed_limit` 100 (owner decision). While `calibrated` is false the UI shows "not calibrated — stop distances are estimates". |
| Sensor freshness | The **loop** checks sample age: > 500 ms while moving forward → stop as for a fault. |
| Deadlines (real car) | No separate liveness thread. The loop writes `last_loop_tick` and `last_controller_frame` (monotonic floats). The **sensor thread**, every iteration, stops the motors and latches hold if either is stale (> 1 s / > `wd_ms`) while motors run. The sensor-thread body catches every exception (and stops the motors before dying). |
| HW fault | A motor write that fails twice, or `stop_motors()` returning False (lock timeout or I2C error) → `hw_fault` latched (like e-stop; cleared by `estop_reset` only after a successful zero write), `event hw_fault`. |
| Hand-over | e-stop persists; config resets to defaults; every acquisition of car control (new controller, take, mode to car, returning lease holder) starts on hold (reason `control`, or `boot`); only `resume` clears it. |

Pure helpers live in `server/picar_server/safety.py` and are reused by the sim.

## 2. Python HAL (`server/picar_server/hal/`)

```python
@dataclass(frozen=True)
class Sample:
    t: float                     # time.monotonic()
    dist_cm: float | None        # None unless dist_state == "ok"; measured from the sensor face
    dist_state: str              # "ok" | "far" | "none" (no echo this sample) | "fault" (3 consecutive "none")
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
    def stop_motors(self) -> bool: ...          # zero both motors; idempotent; NEVER raises; any thread;
                                                # False = could not confirm (lock timeout / I2C error) → hw_fault
    def inhibit_forward(self) -> None: ...      # stop + latch: set_motors refuses forward duty until clear_inhibit()
    def clear_inhibit(self) -> None: ...
    def motion(self) -> tuple[int, int]: ...    # snapshot of the last commanded (left, right) duty, thread-safe
    def set_near_hint(self, cm: float) -> None: ...  # real: ping median-of-3 below this distance; sim/dryrun: no-op
    def pose(self) -> tuple[float, float, float] | None: ...    # sim: true pose (x, y, scratch_dir); real: None
    def close(self) -> None: ...                # stop motors, stop threads
```

Threading rules (real/dry-run):

- **One motor lock** is held across a whole motor operation (direction pins + both PWM channels), so a
  stop can never interleave with a set. Direction change order: PWM 0 → direction pin → PWM.
  `stop_motors()` acquires it with a 0.2 s timeout (→ returns False). All I2C traffic shares one bus lock.
- `inhibit_forward()` runs in the sensor thread under the motor lock; while inhibited, `set_motors` with a
  positive (forward) duty on either side writes zero instead and returns False.
- The sensor thread calls `sys.setswitchinterval(0.0005)` at start (GIL jitter otherwise lengthens echo
  timing), uses median-of-3 always while the motion snapshot is forward, and discards a ping whose busy-wait
  loop saw a gap > 1 ms between iterations.
- The line follower is **never probed while the motors run**; `line_enabled` defaults to false (§3)
  until its cable is fixed.

Real backend (measured hardware, see `server/legacy/obstacle_stop.py`):

- PCA9685 at I2C bus 1, address `0x40`, 60 Hz (prescale 101). One `threading.Lock` guards all I2C
  traffic (PCA9685, PCF8591 `0x48`, line follower `0x11`).
- **Right motor**: PWM ch4, direction GPIO27. **Left motor**: PWM ch5, direction GPIO17.
  Direction pin **HIGH = forward** (both). Use `RPi.GPIO` (rpi-lgpio shim on the Pi).
- Steering: PWM ch0, `angle = clamp(90 + trim + angle_deg, 45, 135)`,
  `ticks = round((600 + 1800 * angle / 180) / 1e6 * 60 * 4096)` (90° → 369).
- Ultrasonic: single SIG pin GPIO5 (output-low 2 ms, high 10 µs, low, switch to input, time the echo).
  Echo-start timeout 30 ms → no answer. Echo that ends after 12 ms but before 40 ms → `far` (> ~2 m).
  Echo still high at 40 ms → no answer (stuck line — never treated as `far`).
  Median of 3 pings when the last reading was < the near hint or the car moves forward; else 1 ping.
  A sample with no valid ping → `none`; 3 consecutive `none` samples → `fault`.
- PCF8591: for ch in 0..2: `write_byte(0x48, 0x40|ch)`, discard one read, then read.
- Line follower: `read_i2c_block_data(0x11, 0, 10)` → 5 big-endian 16-bit values. On error: report
  `None`, re-probe with backoff 2 s → 10 s, log only state changes.
- `safe_stop.py`: standalone (no aiohttp import), zeroes ch4/ch5, exits 0 even if the chip is missing.
- dryrun backend: real sensors, motor/servo writes only logged.

## 3. Calibration (`~/.config/car-scratch/calibration.json`, all optional)

```json
{"calibrated": false, "line_enabled": false,
 "steer_trim_deg": 0.0,
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

Car geometry for drawing and physics is sent once in `welcome.geometry` (authoritative):
`{"body":{"length":26,"width":16,"rear_overhang":4},"wheelbase":14,"track":13,"ultrasonic_x":22,
"line_x":20,"line_spacing":2.0,"light_x":20,"light_spread":4}` — x offsets in cm forward of the rear axle
(the pose point). Optional world fields default to: no walls, no line, no lights, start `(0,0,90)`.

## 5. Browser `CarLink` (`scratch-vm/src/extensions/scratch3_picar/car-link.js`)

```js
class CarLink extends EventEmitter {           // 'events' package
  constructor ({url, WebSocketImpl = WebSocket, clock = {now, setTimeout, clearTimeout, setInterval, clearInterval},
                doc = globalThis.document, storage = globalThis.sessionStorage, fetchImpl = globalThis.fetch})
  start ()                                     // connect + auto-reconnect (not after close 4400/4409)
  stop ()                                      // close, no reconnect
  setMode (mode /* 'car'|'sim' */, world)      // persisted in localStorage 'picar.mode'
  send (op, fields)  -> id|null                // fire-and-forget cmd through the coalescer (below)
  move (dir, speed, ms) -> Promise<result>     // always resolves: done.result | err.code | 'disconnected' | 'timeout' (ms + 2000)
  estop ()                                     // WebSocket estop + POST /api/estop (keepalive); queued if not open
  control (action, force)
  getState () -> {connection: 'connecting'|'open'|'closed'|'reload', role, mode, carAvailable, hw, config,
                  safety, world, worlds, server, lastError}           // immutable snapshot
  subscribe (fn) -> unsubscribe                // fires on coarse state changes only (not every tel)
  getTelemetry () -> last tel or null (cleared on disconnect and on every mode switch)
  // events: 'telemetry' (tel), 'event' (evt), 'world' (world), 'state' (state), 'role' (msg), 'protocol_error' (err)
  // never emit the special EventEmitter 'error' event (it throws when unhandled)
}
```

- `client_id`: random id in **sessionStorage** `picar.clientId` (survives reload/tab restore, distinct per tab;
  in-memory fallback when storage throws). No `crypto.randomUUID` (not a secure context).
- Heartbeat: `hb` every `hb_ms` via `setInterval`.
- **Coalescing:** a command identical to the last one sent for the same op (`drive`, `steer`, `set_speed`,
  `config`) is dropped; otherwise per-op latest-wins, flushed at most every 50 ms (20 Hz). `stop`, `estop`,
  `pause`, `resume`, `move`, `estop_reset` bypass the coalescer and clear pending motion ops.
- Reconnect: backoff 0.5, 1, 2, 4, 5 s (+ up to 250 ms jitter); immediate reconnect on `visibilitychange`
  → visible, `pageshow`, `online`. No frame for 2 s while open → force reconnect. Close codes 4400/4409 →
  state `reload`, no reconnect. `welcome.server.sha` ≠ build sha (if known) → state `reload`.
- On `visibilitychange` → hidden and `pagehide`: send `pause` (stop + hold), best effort.
- Never replays commands after a reconnect (except one queued `estop`, sent right after `welcome`);
  pending `move` promises resolve `'disconnected'`.
- Mode: `hello` uses the localStorage value (default `car`); if the `welcome` says `car_available:false`
  CarLink switches to `sim` once. A mode switch puts CarLink in state `switching` (motion commands dropped)
  until the server's fresh `welcome` arrives — the server answers `mode` with a complete `welcome`.
- Back-pressure: when `ws.bufferedAmount > 64 KB` only `stop`, `estop`, `pause`, `hb` are sent.

## 6. Scratch blocks (extension id `picar`, category name "Pi Car", colour `#C62828`)

Opcodes appear in project.json as `picar_<opcode>`. **Field** menus (no `acceptReporters`) are stored as
a field named after the argument; the one **shadow** menu (`sensor`, `acceptReporters: true`) is stored
as an input whose shadow block is `picar_menu_sensor` with field `sensor`. Number arguments are shadow
`math_number` (field `NUM`). project.json must list `"extensions": ["picar"]`.

| opcode | blockType | text | arguments (name: kind = default) |
|---|---|---|---|
| `drive` | command | drive [DIR] at [SPEED] % | DIR: field `dir` = `forward` · SPEED: number = 50 |
| `driveFor` | command (returns Promise) | drive [DIR] at [SPEED] % for [SECS] seconds | DIR: field `dir` · SPEED: number = 50 · SECS: number = 1 |
| `stopMotors` | command | stop motors | – |
| `setSpeed` | command | set speed to [SPEED] % | SPEED: number = 50 |
| `steer` | command | steer [STEER] | STEER: field `steer` = `straight` |
| `setSteering` | command | set steering angle to [ANGLE] ° | ANGLE: number = 0 |
| `distance` | reporter | distance (cm) | – |
| `obstacleCloser` | Boolean | obstacle closer than [CM] cm? | CM: number = 20 |
| `whenObstacle` | hat, `isEdgeActivated: true` | when obstacle closer than [CM] cm | CM: number = 20 |
| `lineValue` | reporter | line sensor [SENSOR] value | SENSOR: shadow `sensor` = `1` |
| `lineSees` | Boolean | line sensor [SENSOR] sees line? | SENSOR: shadow `sensor` = `3` |
| `linePosition` | reporter | line position | – |
| `whenLineSees` | hat, `isEdgeActivated: true` | when line sensor [SENSOR] sees line | SENSOR: shadow `sensor` = `3` |
| `lightValue` | reporter | [LIGHT] light brightness | LIGHT: field `light` = `middle` |
| `brightestDirection` | reporter | brightest light direction | – |
| `setObstacleStop` | command | turn obstacle stop [ONOFF] | ONOFF: field `onoff` = `on` |
| `setStopDistance` | command | set obstacle stop distance to [CM] cm | CM: number = 20 |
| `setSpeedLimit` | command | set speed limit to [PERCENT] % | PERCENT: number = 100 |
| `emergencyStop` | command | emergency stop | – |
| `isConnected` | Boolean | car connected? | – |
| `carMode` | reporter | car mode | – |
| `carX` / `carY` / `carDirection` | reporter | car x / car y / car direction | – |
| `resetPosition` | command | reset car position | – |
| `whenCarEvent` | **hat, `isEdgeActivated: false`, `shouldRestartExistingThreads: false`**, func returns true | when car [EVENT] | EVENT: field `event` = `obstacle` |

Menu values (stored value → label): `dir`: `forward`, `backward` · `steer`: `left` (−30°), `straight` (0°),
`right` (+30°) · `sensor`: `1`..`5` (1 = leftmost) · `light`: `left`, `middle`, `right` · `onoff`: `on`, `off` ·
`event`: `obstacle` ("stops for an obstacle"), `estop` ("is emergency stopped"), `disconnected` ("loses connection").

Semantics:

- **Armed gate.** The extension keeps `armed`: true on `PROJECT_START`, false on `PROJECT_STOP_ALL`.
  Edge hats return false and `whenCarEvent` is not started unless `armed` and the link is open (edge hats
  otherwise fire on project load and after the stop sign — `runtime._step` evaluates them every tick).
- Commands are fire-and-forget (return immediately); `driveFor` returns the `move` promise (no-op when
  `SECS ≤ 0`; all numbers via `Cast.toNumber`, NaN/Infinity clamped). `drive` at speed 0 = stop.
- Reporters use only valid, current telemetry (cleared on disconnect/mode switch). `obstacleCloser` is
  **true** when the sensor is in `fault` (fail safe), false when disconnected.
- Reporter defaults when disconnected: `distance` 999, booleans false, `lineValue` 0, `linePosition` 0,
  `lightValue` 0, `brightestDirection` `none`, `carMode` `offline`, pose 0/0/90. `distance` returns 999 when
  far, **0 on fault**. `linePosition` is 0 both for "centred" and "no line" (use `lineSees`).
- `PROJECT_START` (green flag) → `resume`. `PROJECT_STOP_ALL` (stop sign, project load) → `stop` only.
- `event estop` / `event hw_fault` received by the session that controls that car (or its own sim) →
  `runtime.stopAll()`.
- `whenCarEvent` is started with `runtime.startHats('picar_whenCarEvent', {EVENT: value})` using a **fresh
  object per call** (startHats mutates it); events map: `obstacle_stop` → `obstacle`, `estop` → `estop`,
  link closed → `disconnected`.

## 7. GUI integration

- The extension calls `runtime.registerPeripheralExtension('picar', this)` **only as a lookup** (no
  `showStatusButton`, no connection modal, no scan flow): `scan/connect/disconnect` are no-ops and
  `isConnected()` returns whether the link is open. It exposes `this.link` (the CarLink). GUI code gets it
  through `vm.runtime.peripheralExtensions.picar.link` (re-check on the VM `EXTENSION_ADDED` event).
  Helper hook: `scratch-gui/src/lib/picar/use-car-link.js`. The Car | Sim switch lives in the Car Monitor.
- `scratch-gui/src/lib/vm-manager-hoc.jsx` loads the `picar` extension once when the VM initialises.
- `?picar_debug=1` exposes `window.__picar = {link, vm}` for e2e tests.
- The Car Monitor subscribes to `link.subscribe` (React state) and `link.on('telemetry')` (canvas,
  no React re-render per frame). The E-STOP button calls `link.estop()` on `pointerdown`.
- The Resume button sends `resume`; the e-stop banner's Reset sends `estop_reset` (controller only).
