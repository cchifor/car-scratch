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
| Sensor validity | Before the first valid sample, after 3 consecutive `none` samples (`fault`), or when the last `ok`/`far` distance was measured more than 150 ms ago (`Sample.t`, not the arrival time) and no trusted "nothing there" reading covers it (decision D7: up to 0.48 s from its ping, no echo since, none closer than 1 m in the last second), and no recent echo is far enough away for the travel since (decision D8): forward motion is stopped/refused with `event sensor_fault` / `err sensor_fault` (never `obstacle_stop`, so avoidance hats do not fire). Backward motion is still allowed. |
| One motor path | Every motor write (drive, move start, `set_speed`, `config` change, speed-limit change) goes through **one** function that applies limits, the command-time obstacle check at the *proposed* effective speed, and the inhibit latch. Lowering `speed_limit` reduces running motion immediately. Each motion gets a **generation number**; timers and deferred callbacks may only touch the motors if their generation is still current. |
| Server start | The shared car starts **on hold** (a restart never resumes old motion). |
| Mode/control transitions | Switching mode, losing control, `world` change and sim `reset_pose` stop motion first (and latch hold for the car controller). |
| Watchdog | Controller silent (no **accepted** JSON frame — rate-limited or rejected frames do not count) for > `wd_ms` (750 ms) → stop and latch **hold** (whether or not the motors run), `event watchdog_stop` if they were running, `done{watchdog}` for a timed move. |
| Pause | `cmd pause` (tab hidden) → stop + latch hold. |
| Hold | Motion refused (`err hold`) until `resume`. Set by watchdog, pause, controller disconnect, and server shutdown (reason `shutdown`). |
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
    line_skipped: bool           # line_raw is None only because one frame was invalid (module still healthy)
    light_raw: list[int] | None  # 3 values 0..255 (left, middle, right) or None

class Backend(Protocol):
    name: str                                   # "real" | "dryrun" | "sim"
    def capabilities(self) -> dict[str, bool]: ...   # {"ultrasonic", "light", "line"}; line may change at runtime
    def start(self, on_sample: Callable[[Sample], None]) -> None: ...
        # real/dryrun: starts the sensor thread, on_sample is called FROM THAT THREAD (~25 Hz).
        # sim: requires a running asyncio loop, calls on_sample from the loop (25 Hz) and runs physics at 50 Hz.
    def set_motors(self, left: int, right: int) -> bool: ...   # signed duty -4095..4095, sign = direction;
                                                # False = a latch replaced the duty with zero (forward inhibit or
                                                # full-stop latch); raises HardwareFault after a failed retry
    def set_steering(self, angle_deg: float) -> None: ...      # -45..45 from straight (neg = left); trim applied inside
    def stop_motors(self) -> bool: ...          # zero both motors; idempotent; NEVER raises; any thread;
                                                # False = could not confirm (lock timeout / I2C error) → hw_fault
    def inhibit_forward(self) -> None: ...      # stop + latch: set_motors refuses forward duty until clear_inhibit()
    def clear_inhibit(self) -> None: ...
    def latch_stop(self) -> bool: ...           # full-stop latch, any thread: motors zeroed, and until release_stop()
                                                # every set_motors writes zero and returns False; True = confirmed off
    def release_stop(self) -> None: ...         # the controller mirrors estop / hold / hw_fault into this latch
    def motion(self) -> tuple[int, int]: ...    # snapshot of the last applied (left, right) duty, thread-safe
    def set_near_hint(self, cm: float) -> None: ...  # real/dryrun: ping median-of-3 below this distance; sim: no-op
    def pose(self) -> tuple[float, float, float] | None: ...    # sim: true pose (x, y, scratch_dir); real/dryrun: None
    def close(self) -> None: ...                # stop motors, stop threads
```

Backend extras outside the protocol (`hal/real.py`, `hal/dryrun.py`, `hal/sim/backend.py`; the controller uses
them when they exist):

- `set_deadline_probe(fn)` (real and dry run, `_HardwareBase`): `fn() -> (loop_ok, controller_ok)` is polled
  by the sensor thread on every iteration while the motors run; a False stops the motors from that thread.
  The controller passes `Controller._deadline_probe`. Without it (the simulator) the controller checks the
  deadlines itself, inside the obstacle guard.
- `set_latch_callback(cb)` (real and dry run): `cb(reason)` is called from the sensor thread after a deadline
  stop (`"loop_stalled"` or `"controller_silent"`), or with `"stop_failed"` when a deadline stop or
  `inhibit_forward()` could not confirm the motors are off. The controller passes `Controller._deadline_latch`
  (hold with reason `watchdog`, or `hw_fault`).
- `set_trim(trim_deg)` (real and dry run): live steering trim for the `calibrate` command; the real backend
  re-applies the current steering angle with the new trim.
- The simulator adds `reset_pose()`, `set_world(world)`, `teleport(x, y, dir)` and a `bumped` flag.

Threading rules (real/dry-run):

- **One motor lock** is held across a whole motor operation (direction pins + both PWM channels), so a
  stop can never interleave with a set. Direction change order: PWM 0 → direction pin → PWM.
  `stop_motors()` acquires it with a 0.2 s timeout (→ returns False). All I2C traffic shares one bus lock.
- `inhibit_forward()` runs in the sensor thread under the motor lock; while inhibited, `set_motors` with a
  positive (forward) duty on either side writes zero instead and returns False.
- The sensor thread calls `sys.setswitchinterval(0.0005)` at start (GIL jitter otherwise lengthens echo
  timing), uses median-of-3 always while the motion snapshot is forward, and discards a ping whose busy-wait
  loop saw a gap > 1 ms between iterations.
- The line follower is probed with a back-off while stopped, and read **while the car moves** only under the
  guards of decision D4 (`docs/decisions.md`): `line_enabled`, a healthy module (10 good reads in a row since its
  last failure), only the I2C bus lock (never the motor lock), and a switch-off until the next stop after
  3 samples in a row without a frame or a bus call over 15 ms (decision D6). An invalid frame or a quick I2C error
  is retried once at once; a sample that still fails is skipped. `line_enabled` defaults to false (§3).

Real backend (measured hardware, see `server/legacy/obstacle_stop.py`):

- PCA9685 at I2C bus 1, address `0x40`, 60 Hz (prescale 101). One `threading.Lock` guards all I2C
  traffic (PCA9685, PCF8591 `0x48`, line follower `0x11`).
- **Right motor**: PWM ch4, direction GPIO27. **Left motor**: PWM ch5, direction GPIO17.
  Direction pin **HIGH = forward** (both). Use `RPi.GPIO` (rpi-lgpio shim on the Pi).
- Steering: PWM ch0, `angle = clamp(90 + trim + angle_deg, 45, 135)`,
  `ticks = round((600 + 1800 * angle / 180) / 1e6 * 60 * 4096)` (90° → 369).
- Ultrasonic: single SIG pin GPIO5 (output-low 2 ms, high 10 µs, low, switch to input, time the echo).
  Echo-start timeout 30 ms → no answer. Echo that ends after 12 ms but before 40 ms → `far` (> ~2 m).
  Echo still high at 40 ms → the module is **busy** (never treated as `far`): no new trigger while the line is
  high (the module ignores triggers, and driving the pin low would fight its output); still high after 0.6 s →
  no answer (stuck line). See decision D5.
  Median of 3 pings when the last reading was < the near hint, after `none` or a busy cycle, or when the car
  moves forward; else 1 ping. With only 2 valid pings the lower one counts (never an average with `far`).
  A sample with no valid ping → `none`; 3 consecutive `none` samples → `fault`. A busy sample is `none` and never
  counts toward the fault (facing open space most pings are long cycles).
- PCF8591: for each input `ch` in `light_channels` (left, middle, right; default 0, 1, 2):
  `write_byte(0x48, 0x40|ch)`, discard one read, then read. `light_raw` is ordered left, middle, right.
- Line follower: `read_i2c_block_data(0x11, 0, 10)` → 5 big-endian 16-bit values. A frame with any value
  above 1023 (the module sometimes answers all 0xFF, about 1 % of frames) is invalid and is read once more at
  once (never after a bus call that took over 15 ms); if that is invalid too the sample is skipped (`line_raw` is `None` for that sample, the module is not
  marked absent). 3 consecutive skipped samples, or an I2C error, mark it absent: re-probe with backoff
  2 s → 10 s, log only state changes. The controller reports a line `sensor_fault` only after 3 samples in a row
  without a frame and repeats the last frame in telemetry for 0.1 s, and only for a single skipped frame (`Sample.line_skipped`), never for
  a suppressed or absent module.
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
 "line_black_high": true, "line_threshold": 320,
 "light_channels": [0, 1, 2], "light_dark": 200, "light_bright": 20}
```

`light_dark`, `light_bright` take one number or a list of 3 (left, middle, right); `line_threshold` one number or a
list of 5 (leftmost first); `light_channels` is a permutation of 3 distinct PCF8591 inputs 0..3. An old file with
only the scalar values still loads (an invalid value is ignored with a warning and the default is used).

- User speed 0..100 → duty %: `0` if speed is 0, else `floor + (ceiling − floor) × speed / 100`;
  duty value = `round(4095 × duty% / 100)`.
- `speed_table`: duty % → cm/s (dead reckoning, margin). `coast_table`: user speed → cm rolled after stop.
- Light: `bright = clamp(100 × (light_dark − raw) / (light_dark − light_bright), 0, 100)`, with the dark and
  bright values of that sensor (a lower raw value means more light; measured).
- Line: with `line_black_high` (default; measured) a sensor sees the line when `raw > threshold`, otherwise when
  `raw < threshold`; the threshold is that sensor's own when `line_threshold` is a list.
- Legacy `~/picar/calibration.json` `{"steer_trim_deg": x}` is migrated on first start.
- `ultrasonic_offset_cm` is reserved and **not used yet**: `safety.geometry()` sends a fixed `ultrasonic_x: 22`
  (section 4), which the simulator and the Car Monitor use.
- The file is read once, when the server starts; only `calibrate` changes values while it runs (the steering
  trim and the light and line keys above).

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
  constructor ({url,                           // default: ?picar_ws= override, else same-origin /ws
                WebSocketImpl = globalThis.WebSocket,
                clock = {now, setTimeout, clearTimeout, setInterval, clearInterval},   // now = performance.now
                doc = globalThis.document,     // visibilityState + visibilitychange
                win = globalThis.window,       // pageshow, pagehide, online
                sessionStore = sessionStorage, // alias: storage; holds picar.clientId
                localStore = localStorage,     // picar.mode, picar.stageMirror
                fetchImpl = globalThis.fetch,  // POST /api/estop
                random = Math.random,          // client id and reconnect jitter
                buildSha = null})              // editor build sha; a different welcome.server.sha -> 'reload'
  start ()                                     // connect + auto-reconnect (not after close 4400/4409)
  stop ()                                      // close, no reconnect
  setMode (mode /* 'car'|'sim' */, world, {persist} = {})   // persisted in localStorage 'picar.mode' unless persist === false
  send (op, fields)  -> id|0|null              // fire-and-forget cmd through the coalescer (below); 0 = queued for the next flush
  move (dir, speed, ms) -> Promise<result>     // always resolves: done.result | err.code | 'disconnected' | 'timeout' (ms + 2000)
                                               //   | 'rate_limited' (socket back-pressured, nothing sent)
  estop ()                                     // WebSocket estop + POST /api/estop (keepalive); queued if not open;
                                               // emits a local 'event' {kind: 'estop', local: true} at once
  control (action, force)
  setStageMirror (on)                          // stage mirror on/off, persisted in localStorage 'picar.stageMirror'
  clearTrail ()                                // emits 'clearTrail' (the extension erases the stage-mirror trail)
  getState () -> {connection: 'connecting'|'open'|'switching'|'closed'|'reload', role, mode, stageMirror,
                  carAvailable, hw, config, safety, world, worlds, geometry, server, lastError}   // immutable snapshot
  subscribe (fn) -> unsubscribe                // fires on coarse state changes only (not every tel)
  getTelemetry () -> last tel or null (cleared on disconnect and on every mode switch)
  // events: 'telemetry' (tel), 'event' (evt), 'world' (world), 'state' (state), 'role' (msg),
  //         'protocol_error' ({reason, data}), 'clearTrail' ()
  // never emit the special EventEmitter 'error' event (it throws when unhandled)
}
```

`geometry` is `welcome.geometry` (section 4); `stageMirror` is the stage-mirror switch (section 6).

- `client_id`: random id in **sessionStorage** `picar.clientId` (survives reload/tab restore, distinct per tab;
  in-memory fallback when storage throws). No `crypto.randomUUID` (not a secure context).
- Heartbeat: `hb` every `hb_ms` via `setInterval`.
- **Coalescing:** a command identical to the last one sent for the same op (`drive`, `steer`, `set_speed`,
  `config`) is dropped; otherwise per-op latest-wins, flushed at most every 50 ms (20 Hz). `stop`, `estop`,
  `pause`, `resume`, `move`, `estop_reset` bypass the coalescer and clear pending motion ops (`drive`,
  `set_speed`). Pending settings (`steer`, `config`) are sent before `move`, `resume` and `estop_reset`, after
  `stop` and `pause`, and dropped by `estop`.
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
| `batteryLow` | Boolean | battery low? | – |
| `onTimeMinutes` | reporter | car on time (minutes) | – |
| `carX` / `carY` / `carDirection` | reporter | car x / car y / car direction | – |
| `resetPosition` | command | reset car position | – |
| `setStageMirror` | command | show the car moves on the stage [ONOFF] | ONOFF: field `onoff` = `on` |
| `stageMirrorOn` | Boolean | stage mirror on? | – |
| `whenCarEvent` | **hat, `isEdgeActivated: false`, `shouldRestartExistingThreads: false`**, func returns true | when car [EVENT] | EVENT: field `event` = `obstacle` |

Menu values (stored value → label): `dir`: `forward`, `backward` · `steer`: `left` (−30°), `straight` (0°),
`right` (+30°) · `sensor`: `1`..`5` (1 = leftmost) · `light`: `left`, `middle`, `right` · `onoff`: `on`, `off` ·
`event`: `obstacle` ("stops for an obstacle"), `estop` ("is emergency stopped"), `disconnected` ("loses connection"),
`battery_low` ("battery gets low").

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
  link closed → `disconnected`, `power_low` -> `battery_low`.
- `batteryLow` is `tel.power.low` (false when disconnected); `onTimeMinutes` is `tel.power.on_s / 60`, rounded
  (0 when disconnected).
- Stage mirror: while on (`setStageMirror`, or the Car Monitor's Mirror button; read it with
  `CarLink.getState().stageMirror`), the sprite named `Car` follows `tel.pose` (`setXY` / `setDirection` when
  it moved at least 0.5 cm or turned at least 0.5 degrees), and a trail is drawn on the extension's own pen skin
  on the pen layer (no Pen extension needed, the user's pen drawings are untouched). No trail across a pose jump
  of more than 50 cm; turning the mirror on and `CarLink.clearTrail()` erase it. `stageMirrorOn` reports the
  switch.

## 7. GUI integration

- The extension calls `runtime.registerPeripheralExtension('picar', this)` **only as a lookup** (no
  `showStatusButton`, no connection modal, no scan flow): `scan/connect/disconnect` are no-ops and
  `isConnected()` returns whether the link is open. It exposes `this.link` (the CarLink). GUI code gets it
  through `vm.runtime.peripheralExtensions.picar.link` (re-check on the VM `EXTENSION_ADDED` event).
  Helper hook: `scratch-gui/src/lib/picar/use-car-link.js`. The Car | Sim switch lives in the Car Monitor.
- `scratch-gui/src/lib/vm-manager-hoc.jsx` loads the `picar` extension once when the VM initialises.
- `?picar_debug=1` exposes `window.__picar = {link, runtime}` (the CarLink and the VM runtime) for e2e tests.
- The Car Monitor subscribes to `link.subscribe` (React state) and `link.on('telemetry')` (canvas,
  no React re-render per frame). The E-STOP button calls `link.estop()` on `pointerdown`.
- The Resume button sends `resume`; the e-stop banner's Reset sends `estop_reset` (controller only).
