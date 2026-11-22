# Car Scratch components

One section per component: what it is for, where it lives, how other code talks to it, how it is configured,
how it is tested and what tends to go wrong. [architecture.md](architecture.md) shows how the components fit
together; [protocol.md](protocol.md) and [contracts.md](contracts.md) are the specifications. Paths are relative
to the repository root; every test command runs in Docker.

Contents:

- Car server: [entry and app](#car-server-entry-and-app), [protocol](#protocol),
  [sessions and arbitration](#sessions-and-arbitration), [controller](#controller), [safety rules](#safety-rules),
  [configuration and calibration](#configuration-and-calibration), [power monitor](#power-monitor),
  [projects store](#projects-store), [safe_stop and sd_notify](#safe_stop-and-sd_notify), [HAL](#hal),
  [simulator](#simulator), [CLI tools](#cli-tools)
- Editor: [VM extension](#vm-extension), [GUI pieces](#gui-pieces)
- Around them: [examples generator](#examples-generator), [deploy](#deploy), [tests](#tests)

---

## Car server entry and app

**Purpose.** Process entry point: parses the command line, builds the aiohttp application, picks the hardware
backend, serves the editor and the other static files, and starts the shared car, the hub and the systemd
readiness/watchdog pings.

**Key files.** [`server/picar_server/__main__.py`](../server/picar_server/__main__.py),
[`server/picar_server/app.py`](../server/picar_server/app.py),
[`server/picar_server/__init__.py`](../server/picar_server/__init__.py) (`__version__`).

**Interfaces.**

- `python3 -m picar_server --port 8080 --host 0.0.0.0 --hardware auto|real|dryrun|sim|none --www DIR
  --examples DIR --source DIR --data DIR --config DIR [--allow-host H] [--allow-origin O] [-v]`
  (`--allow-*` are repeatable and accept comma-separated lists).
- `Settings` (dataclass), `create_app(settings)`, `make_backend(settings, cal)` (returns `(backend or None,
  {"error"?})` and never raises), `static_response(root, rel, request)` (path-confined, serves `name.gz` when the
  client accepts gzip, `Cache-Control: no-cache`).
- Routes: `GET /ws` (`session.ws_handler`), `GET /api/status`, `POST /api/estop`, the `/api/projects` routes
  ([projects store](#projects-store)), `GET /examples/{tail}`, `GET /source/` (a file listing), `GET /source`
  (redirect), and `GET /{tail}` for the editor.
- Startup (`_on_startup`): `load_calibration` -> `make_backend` -> `Controller(backend, cal, shared=True,
  start_on_hold=True)` -> `car.start()` (if that raises, the backend is closed and a backend-less controller takes
  its place) -> `Hub(...)` -> `hub.start()` -> the `_sd_loop` task. Cleanup stops the hub and closes the car.

**Configuration.** The flags above. `CAR_SCRATCH_SHA` (from the release's `REVISION.env`) becomes `server.sha`
in `welcome` and `/api/status`; without it the sha is `dev`. Defaults: `--data ~/.local/share/car-scratch`,
`--config ~/.config/car-scratch`. Request bodies are capped at `MAX_FILE + 1 MiB` (`client_max_size`).

**Tests.** [`server/tests/test_server.py`](../server/tests/test_server.py) (welcome and status, static files and
gzip, path traversal, examples and source, `--hardware none`, `auto` without hardware, a server without an editor
build). Run: `docker compose run --rm server-test`.

**Gotchas.**

- `--hardware auto` is the same as `real`: there is no automatic fallback to the simulator. A missing I2C bus or
  GPIO library makes the car **unavailable** (`car_available: false`, `hw.error` set); the editor still loads and
  CarLink switches itself to sim mode once.
- Without `www/index.html` the root answers 404 with a plain-text note; the API and `/ws` still work.
- The browser ignores the sha check when either side reports `dev`.

## Protocol

**Purpose.** Frame parsing and validation, number clamping, the protocol limits, the per-connection rate limit
and the Host/Origin policy. The wire format itself is specified in [protocol.md](protocol.md).

**Key files.** [`server/picar_server/protocol.py`](../server/picar_server/protocol.py).

**Interfaces.**

- Constants: `PROTOCOL_VERSION = 1`, `MAX_FRAME_BYTES = 4096`, `RATE_PER_S = 100`, `RATE_BURST = 50`,
  `IDLE_S = 10.0`, `TEL_HZ = 20`, `LEASE_S = 30.0`, close codes `CLOSE_BAD_FIRST = 4400`, `CLOSE_IDLE = 4408`,
  `CLOSE_VERSION = 4409`, and `LIMITS` (sent in `welcome.limits`, built from `safety.py`).
- `parse_frame(text)`, `validate_hello(msg)`, `validate_cmd(msg) -> (id, op, fields)` (numbers clamped into range,
  wrong types rejected), `validate_control(msg)`, `validate_mode(msg)`; all raise `ProtocolError(code, msg,
  cmd_id)`.
- `TokenBucket(rate, burst, clock)`, `dumps(obj)` (compact JSON).
- `HostPolicy(allow_hosts, allow_origins)`: `host_ok`, `origin_ok`, `allowed`. Accepted hosts are
  `picar.local`, `picar`, `localhost`, `127.0.0.1`, `[::1]`, the machine's own names and addresses (refreshed
  every 30 s) and `--allow-host` values; a present `Origin` must equal `Host` (host and port) or be listed with
  `--allow-origin`.

**Configuration.** `--allow-host`, `--allow-origin`.

**Tests.** [`test_safety_config.py`](../server/tests/test_safety_config.py) (validation, clamping, token bucket,
host policy), [`test_server.py`](../server/tests/test_server.py) (Origin and Host checks, first frame, 4409,
oversize frames closed with 1009, rate limit, idle close 4408).

**Gotchas.**

- A dev proxy on another port (for example the webpack dev server) needs `--allow-origin`, because its `Origin`
  differs from the `Host` the server sees.
- The viewer rule ("viewers may only send `estop`") is enforced in `Session._cmd`; `protocol.VIEWER_TYPES` is
  not used.
- The `world` command accepts the world id as `world` or `world_id`.

## Sessions and arbitration

**Purpose.** One `Session` per WebSocket (hello, liveness, validation, rate limit, an ordered send queue and a
latest-only telemetry slot) and one `Hub` per server: the shared car, its controller seat, the 30 s lease,
promotion of waiting viewers, and the fan-out of telemetry and events.

**Key files.** [`server/picar_server/session.py`](../server/picar_server/session.py).

**Interfaces.**

- `ws_handler(request)`: Host/Origin check (403), then `WebSocketResponse(heartbeat=5,
  max_msg_size=4096)` and `Session(hub, ws, peer).run()`.
- `Session`: `run()` (read loop with the idle timeout), `_on_hello`, `_on_text`, `_cmd`, `_control`,
  `_switch_mode`, `send(msg)` (reliable and ordered; more than 512 queued messages close the socket with 1008
  "slow client"), `post_tel(text)` (a newer frame replaces an unsent one), `_accept()` (liveness, and
  `ctl.touch()` for the watchdog when this session controls).
- `Hub`: `take(session, force)`, `release(session)`, `promote_waiting()`, `detach(session)`, `estop(session, by)`,
  `status()`, `hw_info()`, `broadcast_tel()`, `_tel_loop()` (20 Hz: `promote_waiting()` then `broadcast_tel()`),
  `_car_event` / `_car_done` (controller callbacks).
- Messages it produces: `welcome`, `role`, `tel`, `ack`, `err`, `done`, `event`, `world`
  ([protocol.md](protocol.md) section 4).

**Configuration.** `MAX_SIMS = 4`, `MAX_QUEUE = 512`, `RATE_EXEMPT_OPS = ("stop", "estop", "pause")`;
`idle_s` and `lease_s` come from `Settings`.

**Tests.** [`test_server.py`](../server/tests/test_server.py) (take, release and force; hand-over resets config
and holds; disconnect and lease; promotion after the lease, after a release, never during a lease, never for
view-only, failed-sim or duplicate sessions; mode switch; watchdog over the wire),
[`test_review_regressions.py`](../server/tests/test_review_regressions.py) (session fallbacks, rate-limit
exemptions).

**Gotchas.**

- Only accepted frames count: a rate-limited or rejected frame feeds neither the idle timer nor the watchdog,
  and a refused `mode` request does not either.
- `pause` is exempt from the rate limit, like `stop` and `estop`.
- `Hub.sessions` is in hello order, and promotion walks it, so the longest-waiting session wins. A session that
  sent `control release` is skipped until it asks again; a controller that was forced out stays eligible.
- A failed sim hello (`sim_busy`, unknown world) falls back to viewing the car and is never promoted.
- E-STOP from any session, in any mode, latches the **shared car** too, not only the session's sim.
- The car snapshot for `tel` is built once per tick and shared by every car-mode session.

## Controller

**Purpose.** One class for the shared car and for every private simulator: executes validated commands, owns the
single motor-write path, the stop latches, timed moves, the reverse budget, the watchdog, the obstacle guard
(called from the sensor thread), dead reckoning and the telemetry snapshot.

**Key files.** [`server/picar_server/controller.py`](../server/picar_server/controller.py).

**Interfaces.**

- `Controller(backend, calibration, *, shared, clock, on_event, on_done, save_calibration, start_on_hold,
  error)`; `start()`, `close()`.
- `command(op, fields, owner, cmd_id)` dispatches to `_cmd_<op>` and returns the extra `ack` fields, or raises
  `CmdError(code, msg)`.
- Safety entry points (loop thread): `estop(by)`, `stop_and_hold(reason, result, running_event)`,
  `stop_for_transition()`, `controller_lost()`, `new_controller(returning)`, `touch()`, `tick()` (every 50 ms).
- Telemetry: `snapshot()` (everything in `tel` except `t`, `seq`, `ts`, `mode`, `ctl`), `capabilities()`,
  `pose()`.
- Internals worth knowing: `_apply()` is the one motor path (latches, reverse budget, forward check at the
  proposed speed, generation bump, `_write()`, forward check again); `_halt()` stops and bumps the generation;
  `_on_sample()` / `_guard()` run in the sensor thread; `_deadline_probe()` / `_deadline_latch()` serve the
  backend's deadline check; `_defer()` / `_kick()` / `_drain()` hand results to the loop; `_sync_latch()` mirrors
  e-stop, hold and `hw_fault` into the backend's full-stop latch.

**Configuration.** `cfg` starts as `safety.DEFAULT_CONFIG` (`obstacle_stop: true`, `stop_cm: 20`,
`speed_limit: 100`) and is reset on every hand-over except for a returning lease holder. Calibration tables from
[config.py](#configuration-and-calibration).

**Tests.** [`test_controller.py`](../server/tests/test_controller.py) (the contract rules on a fake clock and a
fake backend from [`conftest.py`](../server/tests/conftest.py)),
[`test_review_regressions.py`](../server/tests/test_review_regressions.py) (guard/move races, unconfirmed stops,
sensor gaps), [`test_sim.py`](../server/tests/test_sim.py) (the controller on a `SimBackend`).

**Gotchas.**

- `_on_sample` runs in the sensor thread on real hardware. Code there may touch only the backend's thread-safe
  motor path, fields under `self._lock`, and `_defer` + `_kick`; never loop-only objects.
- A result deferred from the sensor thread is bound to the move object that was current, so an older stop never
  finishes a newer move. Timers (`_run_move`, `_reverse_deadline`) check the motion generation before acting.
- When the backend stopped on its own (guard or deadline), `tick()` notices and clears the motion intent.
- `motor_s` counts any non-zero duty; it feeds `power.motor_s` and is per server process.
- Dead reckoning uses `speed_table` and `wheelbase_cm`, so the pose (and the Car Monitor's room map built on it)
  is only as good as the calibration. On this car `wheelbase_cm` is an effective 7, not the measured axle
  distance (see [calibration.md](calibration.md)).
- `calibrate` is refused on a simulator (`bad_request`).

## Safety rules

**Purpose.** Pure functions and constants shared by the controller and the simulator: speed and duty mapping,
the stop margin, the guard decision and the clear rule, light and line helpers, the car geometry.

**Key files.** [`server/picar_server/safety.py`](../server/picar_server/safety.py).

**Interfaces.**

- Limits: `SPEED_MAX = 100`, `STEER_MAX = 45.0`, `MOVE_MS_MAX = 30000`, `REVERSE_SPEED_MAX = 40`,
  `REVERSE_MS_MAX = 5000`, `STOP_CM_MIN = 10`, `STOP_CM_MAX = 100`, `HB_MS = 200`, `WD_MS = 750`.
- Timing: `SENSOR_STALE_S = 0.15`, `CLEAR_S = 1.0`, `UNBLOCK_MARGIN_CM = 5`, `REACTION_S = 0.2`,
  `DRIVE_TIMEOUT_S = 5.0`, `REVERSE_RESET_S = 1.0`.
- `DEFAULT_CONFIG`, `speed_to_duty_pct`, `speed_to_duty`, `duty_to_speed`, `speed_cm_s`, `coast_cm`,
  `margin_cm(stop_cm, speed, cal)`, `effective_speed(direction, speed, speed_limit)`, `guard_decision(...)`,
  `is_clear_sample(...)` (a sample whose pings heard an echo within margin + 5 cm never counts toward releasing
  `blocked`, whatever its median), `brightness`, `brightest`, `line_on`, `line_pos`, `geometry(cal)`, `interp`,
  `clamp`, `clamp_steer`, `wrap_deg`.

**Configuration.** Calibration tables (`speed_table`, `coast_table`, duty floor and ceiling, light and line
thresholds).

**Tests.** [`test_safety_config.py`](../server/tests/test_safety_config.py).

**Gotchas.**

- `margin_cm = stop_cm + coast_cm(s) + speed_cm_s(s) * 0.2` at the effective speed. With the speed table saved
  on the car and the default coast table, speed 40 gives 20 + 2.4 + 10.0 * 0.2 = 24.4 cm.
- `geometry()` hard-codes the body size and `ultrasonic_x: 22`; the calibration field `ultrasonic_offset_cm` is
  not read anywhere.
- `FRESH_S` (0.5 s) is defined here, but `Controller.tick` uses a literal 0.5 for the loop's freshness check.

## Configuration and calibration

**Purpose.** Calibration defaults, validation, loading (with a one-time migration of the old trim file) and
atomic saving. How to measure the values: [calibration.md](calibration.md).

**Key files.** [`server/picar_server/config.py`](../server/picar_server/config.py); on the car
`~/.config/car-scratch/calibration.json` (`--config`).

**Interfaces.** `DEFAULT_CALIBRATION`, `merge(raw)` (defaults overlaid with valid entries; unknown keys dropped;
tables must be at least two `[x, y]` pairs sorted by x; a ceiling at or below the floor resets both),
`load_calibration(config_dir, legacy_path)`, `save_calibration(config_dir, cal)` (temp file, fsync,
`os.replace`). The `calibrate` command (`steer_trim_deg`, the light and line keys, `save`) changes them live and, with `save`, writes
the whole calibration.

**Configuration.** Fields and defaults are listed in [contracts.md](contracts.md) section 3. The car's file
(2026-11-21) holds the measured `speed_table`, `wheelbase_cm: 7` and `steer_trim_deg: -3.3`; the coast distances
were not measured, so `calibrated` stays `false` and the editor keeps its "not calibrated" banner.

**Tests.** [`test_safety_config.py`](../server/tests/test_safety_config.py) (defaults, round trip, invalid
entries, corrupt file, legacy migration), [`test_hal_tools.py`](../server/tests/test_hal_tools.py) (`calib`
applies and saves the trim).

**Gotchas.**

- The file is read **once, at server start**. After a hand edit restart the service
  (`systemctl --user restart car-scratch`); reconnecting the browser does not reload it.
- Every simulator session copies the server's calibration when it starts, so a sim on the car uses the car's
  (slow) speed table, and a sim in Docker uses the defaults.
- `calibrated` and `line_enabled` must be JSON booleans; anything else is ignored with a warning.

## Power monitor

**Purpose.** The `power` section of the car's telemetry and the `power_low` / `power_ok` events. The car has no
battery-voltage sensor; the Pi's own supply monitor is the proxy (on a battery car a sagging Pi supply means
low batteries).

**Key files.** [`server/picar_server/power.py`](../server/picar_server/power.py).

**Interfaces.** `PowerMonitor(...)` with `start()`, `stop()`, `poll()`, `feed_throttled(text)`,
`snapshot(motor_s)` (cached, no I/O; called at 20 Hz) and `on_event(kind, fields)`; `parse_throttled`,
`find_alarm` (finds the `rpi_volt` hwmon by name), `run_vcgencmd`, `sim_power(on_s, motor_s)` for sim sessions.
Fields are specified in [protocol.md](protocol.md) section 5.

**Configuration.** `POLL_S = 1.0` (sysfs, uptime, load), `VCGENCMD_S = 10.0` with a 2 s timeout,
`OK_AFTER_S = 5.0` (`power_ok` only after `low` stayed false for 5 s). Sources: hwmon `rpi_volt
in0_lcrit_alarm`, else `vcgencmd get_throttled` bit 0; bit 16 for "since boot", bit 2 for throttling;
`/sys/class/thermal/thermal_zone0/temp`, `/proc/uptime`, the load average.

**Tests.** [`test_power.py`](../server/tests/test_power.py) (throttled bits, hwmon by name, missing sources,
debounced `power_ok`, vcgencmd failures, a stalled sysfs read never blocks the loop, `motor_s`, `power` in car
and sim frames, events reach car clients).

**Gotchas.**

- File reads run in a worker thread (`asyncio.to_thread`), and a pass is skipped while the previous read is
  still stuck; state changes and events happen on the loop. Only the first pass, in `start()`, reads on the
  loop thread.
- In Docker or on a dev box nothing is readable: `source: "none"`, flags false, the power chip is hidden.
- The extension maps `power_low` to the `when car [battery gets low]` hat and `power.low` to the `battery low?`
  block; the Car Monitor shows a power chip and a BATTERY LOW banner.

## Projects store

**Purpose.** "Save to car" / "Open from car": `.sb3` files on the car's SD card behind a small REST API.

**Key files.** [`server/picar_server/projects.py`](../server/picar_server/projects.py).

**Interfaces.** `ProjectStore(root, quota, max_file)` with `path(name)`, `list()`, `used()`, `put(name, data)`
(must be a zip with `project.json`; written to a unique temp file, fsynced, the old file kept as `.bak`, then
`os.replace`), `delete(name)` (removes the `.bak` too); `add_routes(app, store)` for `GET /api/projects`,
`GET|PUT|DELETE /api/projects/{name}.sb3`. `StoreError(status, msg)` becomes a JSON `{"error": ...}` reply.

**Configuration.** `NAME_RE = [A-Za-z0-9 _-]{1,64}`, `MAX_FILE = 25 MB`, `QUOTA = 500 MB`; storage under
`--data`/projects (`~/.local/share/car-scratch/projects` on the car).

**Tests.** [`test_server.py`](../server/tests/test_server.py) (CRUD, atomic overwrite and `.bak`, bad names,
non-zip and missing `project.json`, size cap and quota, Host/Origin on unsafe methods),
[`test_review_regressions.py`](../server/tests/test_review_regressions.py).

**Gotchas.** The quota counts the `.bak` files. Only `PUT` and `DELETE` check Host/Origin. At most two request
bodies are buffered at once (a semaphore), and all file work runs in the default executor, never on the loop.

## safe_stop and sd_notify

**Purpose.** Two tiny modules for systemd: `safe_stop` zeroes the rear motors from outside the server, and
`sdnotify` tells systemd that the server is ready and its event loop alive.

**Key files.** [`server/picar_server/safe_stop.py`](../server/picar_server/safe_stop.py),
[`server/picar_server/sdnotify.py`](../server/picar_server/sdnotify.py),
[`deploy/car-scratch.service`](../deploy/car-scratch.service), the boot unit written by
[`deploy/pi-setup.sh`](../deploy/pi-setup.sh).

**Interfaces.**

- `python3 -m picar_server.safe_stop`: imports only `smbus2` and the standard library, writes zero to the
  PCA9685 registers of channels 4 and 5 (one retry), leaves the servo alone, and **always exits 0**.
- `sdnotify.ready()` / `sdnotify.watchdog()`: datagrams to `NOTIFY_SOCKET`; no-ops without it; never raise.
  `app._sd_loop` sends `READY=1` 0.1 s after startup and then `WATCHDOG=1` every 0.5 s.

**Configuration.** The user unit: `Type=notify`, `WatchdogSec=3`, `Restart=always`, `RestartSec=2`,
`TimeoutStopSec=2`, `ExecStartPre=-` and `ExecStopPost=-/usr/bin/python3 -m picar_server.safe_stop`. On SIGTERM the
app's `on_shutdown` stops the car (hold `shutdown`) and closes every WebSocket (1001) first; aiohttp then waits at most
0.5 s (`shutdown_timeout`) for open requests, so cleanup (backend close) finishes inside the 2 s. Before this, one open
socket kept the process alive 7.5 s and systemd killed it (2026-11-22). The system
unit `car-safe-stop.service` runs a root-owned copy (`/usr/local/lib/car-scratch/safe_stop.py`) through
`safe-stop-wait.sh`, which waits up to about 10 s for `/dev/i2c-1`.

**Tests.** [`test_hal_dryrun.py`](../server/tests/test_hal_dryrun.py) (only channels 4 and 5 zeroed, exit 0 with a
missing chip or bus or without hardware, sd_notify with and without a socket).

**Gotchas.**

- The boot copy is refreshed only by re-running `pi-setup.sh` after a deploy that changed `safe_stop.py`.
- The sd_notify pings prove that the event loop runs; the sensor thread is covered by its own deadline check and
  exception handling, not by systemd.

## HAL

**Purpose.** The hardware abstraction behind a controller: the backend interface, the PCA9685 register layer,
the real sensors, the real car and the dry-run car.

**Key files.** [`hal/base.py`](../server/picar_server/hal/base.py),
[`hal/pca9685.py`](../server/picar_server/hal/pca9685.py), [`hal/sensors.py`](../server/picar_server/hal/sensors.py),
[`hal/real.py`](../server/picar_server/hal/real.py), [`hal/dryrun.py`](../server/picar_server/hal/dryrun.py)
(all under `server/picar_server/`). The measured wiring is in [contracts.md](contracts.md) section 2.

**Interfaces.**

- `base.py`: `Sample(t, dist_cm, dist_state, line_raw, light_raw, ...)`; also `clear_t`, `echo_cm` and `echo_t`
  (D7, D8), and `dist_t`, the newest valid ping's trigger time, which the controller's distance freshness counts
  from. `DIST_OK/FAR/NONE/FAULT`, `HardwareFault`,
  and the `Backend` protocol: `capabilities()`, `start(on_sample)`, `set_motors(left, right) -> bool` (False when
  a forward duty was refused by a latch; raises `HardwareFault` after a failed retry), `set_steering(angle)`,
  `stop_motors() -> bool` (never raises; False = not confirmed), `inhibit_forward()`, `clear_inhibit()`,
  `latch_stop()`, `release_stop()`, `motion()`, `set_near_hint(cm)`, `pose()`, `close()`.
- `pca9685.py`: `PCA9685(bus, lock)` with `setup()` (60 Hz, prescale 101), `set_pwm(ch, off, timeout)`,
  `set_pwm_retry(ch, off)` (one retry, then `HardwareFault`); `servo_ticks(angle)`; channels `SERVO_CH = 0`,
  `MOTOR_RIGHT_CH = 4`, `MOTOR_LEFT_CH = 5`.
- `sensors.py`: `Ultrasonic` (GPIO5, polling `ping()`, `measure(n)`, `close()`), `SensorHub.read() -> Sample`
  (takes the ultrasonic object and a polling fallback), `set_near_hint`, `capabilities()`, `probe()`; PCF8591 light
  at `0x48`, line follower at `0x11`.
- `ultrasonic_edges.py` (decision D9):
  - `EdgeUltrasonic(line, clock, pin, stop)`: kernel-timestamped edges, with a pull-up trigger, so the line is
    never driven. It has the same interface, plus `diag`, `trigger_s` and `second_widths` (logged per minute),
    `probe()`, and `broken` (switches the hub to polling once the line is free).
  - `open_edge_ultrasonic()` finds `GPIO5` by name, checks that edges are accepted, and checks that the module
    answers pull-up triggers.
  - `select_ultrasonic(mode, ...)` picks the implementation for `--us-timing`.
- `gpio_cdev.py`: the GPIO character device (uAPI v2) for one line.
  - `EdgeLine`: `arm()` (both edges, pull-down), `pull_up()`, `input()`, `get()`, `wait(timeout)`, and `events()`
    returning `Edge(rising, t, seqno)`.
  - `find_line(name)`, `line_config()`, `parse_events()`.
  - The layouts are asserted at import against the values from `tools/diag/gpio_abi.c`.
- `real.py`: `_HardwareBase` (both locks, the forward inhibit and full-stop latches, the sensor thread
  `picar-sensors` every 40 ms, `set_deadline_probe`, `set_latch_callback`) and `RealBackend` (zeroes the motor
  channels before anything else, direction GPIO27 right / GPIO17 left with HIGH = forward, `set_trim`).
- `dryrun.py`: `DryRunBackend`, real sensors, motors zeroed once at start, motor and servo writes only logged.

**Configuration.** `--hardware`, `--us-timing poll|edges|auto` (default `$CAR_SCRATCH_US_TIMING`, else `poll`); calibration `steer_trim_deg`, `line_enabled`,
`light_channels`, `line_black_high`. Ultrasonic diagnostics for the car: `server/tools/diag/README.md`.

**Tests.** [`test_hal_real.py`](../server/tests/test_hal_real.py) (write order, retries, lock order, deadline
probe, sensor-thread failures), [`test_hal_sensors.py`](../server/tests/test_hal_sensors.py) (scripted echoes on
a fake clock), [`test_hal_dryrun.py`](../server/tests/test_hal_dryrun.py), with the fakes in
[`fakes_hw.py`](../server/tests/fakes_hw.py). `smbus2` and `RPi.GPIO` are imported lazily, so the tests need
neither.

**Gotchas.**

- Lock order: motor lock (outer), then the I2C bus lock (inner), never the other way round.
- An echo is `far` only when it ends between 12 and 40 ms. Still high at 40 ms means the module is in its long
  no-echo cycle (`BUSY`, decision D5): it is not re-triggered until the line drops, and a line high for over 0.6 s
  is "no answer". A ping whose busy-wait loop saw a gap over 1 ms is discarded. Walls hit at an angle can read `far` (see
  [safety.md](safety.md)).
- The line follower is off by default (`line_enabled: false`). While the car moves it is read only when the module
  has answered 10 times in a row, and the reads stay off until the next stop after 3 samples in a row without a
  frame or a slow (> 15 ms) bus call (decisions D4, D6). A moving read takes only the I2C bus lock, never the motor
  lock. An invalid frame or a quick I2C error is retried once at once; a sample that still fails is skipped.
- A line frame with any value above 1023 is invalid (the module answers all 0xFF about 1 % of the time); it is
  retried once (not after a slow call), then the sample is skipped.
- `light_raw` follows `light_channels` (left, middle, right); the sensor thread reads the calibration live.
- `sys.setswitchinterval(0.0005)` is process-wide while the sensor thread runs.

## Simulator

**Purpose.** A simulated car with the same backend interface, used by sim sessions, by `--hardware sim` (the
Docker `sim-server`), by the pytest suite and by the e2e tests.

**Key files.** [`hal/sim/backend.py`](../server/picar_server/hal/sim/backend.py),
[`hal/sim/world.py`](../server/picar_server/hal/sim/world.py),
[`hal/sim/worlds/`](../server/picar_server/hal/sim/worlds) (`arena` (default), `corridor`, `lamp`, `maze`,
`oval`).

**Interfaces.** `SimBackend(world, calibration, seed, clock)` implements the backend protocol plus
`reset_pose()`, `set_world(world)`, `teleport(x, y, dir)`, `step(dt)`, `make_sample()` and the `bumped` flag.
`world.py`: `World`, `validate_world`, `load_world(id)`, `list_worlds()`, `WORLD_DIR`, `DEFAULT_WORLD`, and the
geometry helpers (`ray_cast`, `polygon_hits_walls`, `polyline_distance`, `segments_intersect`). The world format
is in [contracts.md](contracts.md) section 4; the same object is sent as the `world` message.

**Configuration.** Physics at 50 Hz and samples at 25 Hz on the event loop; a first-order speed lag (0.2 s) and a
coast from `coast_table`; five ultrasonic rays within +-15 degrees, 200 cm range, 0.5 cm noise, and walls hit at
more than 40 degrees read `far` like the real sensor.

**Tests.** [`test_sim.py`](../server/tests/test_sim.py) (worlds, ray casting, sensors, physics, collisions, a
corridor move that ends with `obstacle` before the wall).

**Gotchas.** A sim session gets its own `Controller` (`shared=False`, never on hold at start) with a copy of the
calibration; at most 4 run at once, and each is destroyed when its socket closes. `drive.bumped` appears only in
sim telemetry.

## CLI tools

**Purpose.** Drive and calibrate the car from a terminal, without the editor.

**Key files.** [`server/tools/wsclient.py`](../server/tools/wsclient.py),
[`server/tools/calib.py`](../server/tools/calib.py).

**Interfaces.** Run from the `server/` directory (on the car: `~/car-scratch/current/server`), with aiohttp
installed:

- `python3 -m tools.wsclient [--url URL] [--client-id ID] [--force] status | watch | drive DIR SPEED [--hold S]
  [--steer A] | move DIR SPEED MS [--steer A] | stop | steer A | estop | reset | resume | config k=v ...`
  (`--url` defaults to `ws://picar.local:8080/ws`). `status`, `watch` and `estop` connect as viewers;
  `drive`/`move` send `resume` first; Ctrl-C while driving sends `stop`.
- `python3 -m tools.calib [--url URL] trim` (type a trim, `s` saves it to `calibration.json`) and
  `calib speed [--speeds 30,40,...] [--ms 1500] [--floor 25] [--ceiling 100]` (prints `speed_table` entries),
  `calib light [--seconds 2]` and `calib line [--seconds 2]` (guided; they read telemetry, send `calibrate` and
  never send a motion command; see [calibration.md](calibration.md)).

**Tests.** [`test_hal_tools.py`](../server/tests/test_hal_tools.py) (against a tiny fake server).

**Gotchas.** `wsclient` needs control for everything except `status`, `watch` and `estop`, and exits with 2 if a
browser holds it (use `--force`). When it disconnects as the controller its own `client_id` keeps the 30 s lease,
so a waiting browser tab is promoted only after that.

## VM extension

**Purpose.** The "Pi Car" block category and the transport to the car. The extension is built in and loaded
once by the GUI; it owns the page's only `CarLink`.

**Key files.** Under `editor/packages/scratch-vm/src/extensions/scratch3_picar/`:
[`index.js`](../editor/packages/scratch-vm/src/extensions/scratch3_picar/index.js) (`Scratch3PiCarBlocks`),
[`car-link.js`](../editor/packages/scratch-vm/src/extensions/scratch3_picar/car-link.js) (`CarLink`),
[`protocol.js`](../editor/packages/scratch-vm/src/extensions/scratch3_picar/protocol.js) (constants and URL
helpers). Registered in `scratch-vm/src/extension-support/extension-manager.js`.

**Interfaces.**

- Blocks (`picar_<opcode>`): the table in [contracts.md](contracts.md) section 6, including the newer
  `batteryLow` ("battery low?"), `onTimeMinutes` ("car on time (minutes)"), `setStageMirror` ("show the car
  moves on the stage [on/off]"), `stageMirrorOn` ("stage mirror on?") and the `battery_low` ("battery gets low")
  value of the `when car [EVENT]` hat.
- Runtime hooks: `PROJECT_START` arms the hats and sends `resume`; `PROJECT_STOP_ALL` disarms and sends `stop`;
  an `estop` or `hw_fault` (this browser's press, or the car this session controls) calls `runtime.stopAll()`;
  the project is also stopped on a reconnect, on viewer-to-controller and on a switch to car mode, because every
  acquisition starts on hold.
- Stage mirror: while on, the sprite named `Car` follows `tel.pose` (moved at least 0.5 cm or turned at least
  0.5 degrees) and a red trail is drawn on the extension's **own pen skin** on the pen layer
  (`renderer.createPenSkin`), so it needs no Pen extension and never touches the user's drawings. No trail across
  a jump over 50 cm; `link.clearTrail()` erases it.
- `CarLink` (contracts section 5): `start`, `stop`, `setMode(mode, world, {persist})`, `send(op, fields)`,
  `move(dir, speed, ms) -> Promise`, `estop()`, `control(action, force)`, `setStageMirror(on)`, `clearTrail()`,
  `getState()`, `subscribe(fn)`, `getTelemetry()`; events `telemetry`, `event`, `world`, `state`, `role`,
  `protocol_error`, `clearTrail`.
- `runtime.registerPeripheralExtension('picar', this)` is a lookup only (no scan, no connection modal).
  `?picar_debug=1` exposes `window.__picar = {link, runtime}` for the e2e tests.

**Configuration.** `__PICAR_BUILD_SHA__` (webpack, from `BUILD_SHA`) for the version check; `?picar_ws=` to
point at another server. Storage: `picar.clientId` (sessionStorage, one id per tab), `picar.mode` and
`picar.stageMirror` (localStorage). Timings in `protocol.js`: heartbeat `hb_ms` (200), coalescing 50 ms,
no frame for 2 s (5 s while connecting) forces a reconnect, backoff 0.5 to 5 s plus up to 250 ms jitter, a
`move` times out after `ms + 2000`, back-pressure above 64 KB, one REST e-stop per 250 ms.

**Tests.** `editor/packages/scratch-vm/test/unit/extension_picar.js`, `extension_picar_carlink.js`,
`extension_picar_examples.js` (loads every packed example in a VM; needs `out/examples`),
`extension_picar_finalreview.js`, `extension_picar_reverify.js`, `extension_picar_stagemirror.js`, with fakes in
`test/fixtures/picar/fakes.js`. Run: `docker compose run --rm editor-test`.

**Gotchas.**

- Coalescing: `drive`, `steer`, `set_speed`, `config` are latest-wins per op and flushed at most every 50 ms.
  `stop`, `estop`, `pause`, `resume`, `move`, `estop_reset` bypass it. A bypass op drops a pending `drive` or
  `set_speed`, but a pending `steer` or `config` is sent **before** `move`/`resume`/`estop_reset` and **after**
  `stop`/`pause` (on-car finding 2026-11-21: "steer left" right before a timed drive was lost, so the figure
  eight drove straight). `estop` drops pending settings.
- Speed 0 (`drive` or `set_speed`) is sent as `stop`. An identical `drive` is dropped only within 1 s, so the
  server's 5 s drive timeout (obstacle stop off) keeps getting fresh commands.
- `CarLink` never emits the EventEmitter `error` event (it throws when nobody listens).
- When `welcome` says `car_available: false`, CarLink switches to sim once without saving that as the user's
  choice.

## GUI pieces

**Purpose.** The car-specific parts of the editor: the Pi Car tab with the Car Monitor, the E-STOP and Reset
buttons, the Examples menu, Save/Open on the car, and page-wide helpers.

**Key files.** Under `editor/packages/scratch-gui/src/`:

| Piece | Files | What it does |
|---|---|---|
| Car Monitor | [`containers/car-monitor.jsx`](../editor/packages/scratch-gui/src/containers/car-monitor.jsx), [`components/car-monitor/car-monitor.jsx`](../editor/packages/scratch-gui/src/components/car-monitor/car-monitor.jsx) | Header: Car / Simulator switch (Car disabled without a car), world picker in sim mode, Room / Follow switch in car mode, power chip, status chip; banners; the canvas; a toolbar with Clear map, Reset position (controller only) and Mirror (stage mirror on/off) |
| Canvas | `monitor-canvas.jsx`, `car-renderer.js`, `draw-scene.js`, `room-map.js` | A `requestAnimationFrame` loop paints telemetry held in a ref (no React render per frame). Room view (default in car mode): fixed camera that grows to fit the path, at least 300 x 200 cm, with an obstacle map of ultrasonic hits (3 cm grid, up to 3000 points, readings up to 200 cm). Follow view tracks the car. Sim mode draws the world. Clear map forgets the obstacle points and erases the stage-mirror trail |
| Banners and chips | `monitor-flags.js`, `monitor-banners.jsx`, `status-chip.jsx`, `power-chip.jsx` | `telemetryFlags` reduces a frame to coarse flags; `computeBanners` picks reload, reconnecting, no car, hardware fault, E-STOP, paused, obstacle, BATTERY LOW, view only (with Take control), obstacle stop off, not calibrated, line offline |
| E-STOP | [`components/car-monitor/estop-button.jsx`](../editor/packages/scratch-gui/src/components/car-monitor/estop-button.jsx), [`containers/car-estop.jsx`](../editor/packages/scratch-gui/src/containers/car-estop.jsx) | Fires `link.estop()` on `pointerdown`. `CarEstop` puts E-STOP in the menu bar and, floating with a status chip, over the full-screen stage; while the e-stop or a hardware fault is latched and this session may reset it, **Reset** sits next to it (`estop_reset`) |
| Page-wide | [`containers/picar-global.jsx`](../editor/packages/scratch-gui/src/containers/picar-global.jsx), `components/car-monitor/rotate-hint.jsx`, `components/car-info-modal/*` | Portrait "rotate your iPad" overlay (with its own E-STOP), audio unlock on the first tap, About and Safety dialogs (requested from the menu through the `picar-info` window event) |
| Examples | [`components/menu-bar/examples-menu.jsx`](../editor/packages/scratch-gui/src/components/menu-bar/examples-menu.jsx) | Fetches `examples/index.json` when opened, loads the chosen `.sb3`, and in sim mode switches to the example's world |
| Projects on the car | `components/car-projects-modal/*`, `reducers/modals.js` (`carProjectsOpen`, `carProjectsSave`), `components/menu-bar/file-menu.jsx` | File > Save to car / Open from car |
| Tabs | `components/stage-pane-tabs/*` | Sprites / Pi Car; touch devices start on Pi Car, desktops on Sprites; the choice is stored |
| Helpers | `lib/picar/*` | `use-car-link.js` (`findCarLink`, `useCarLink`, `useCarState`), `projects-api.js`, `examples-api.js`, `load-project-buffer.js`, `storage.js`, `about-events.js`, `audio-unlock.js`, `scratch-characters.js` (filters the Scratch characters out of the sprite and costume libraries) |

**Interfaces.** Everything talks to the car through the `CarLink` from `useCarLink(vm)`, which re-checks on the
VM's `EXTENSION_ADDED` and `workspaceUpdate` events. `lib/vm-manager-hoc.jsx` loads the `picar` extension once
when the VM starts. REST calls use page-relative URLs (`api/projects`, `examples/index.json`).

**Configuration.** localStorage keys `picar.monitorView` (room or follow) and `picar.stagePaneTab`.
`webpack.config.js` defines `__PICAR_BUILD_SHA__` from `BUILD_SHA` and, in the dev server, proxies `/ws`, `/api`,
`/examples` and `/source` to `PICAR_SERVER` (default `http://localhost:8080`).

**Tests.** `editor/packages/scratch-gui/test/unit/picar/` (`car-monitor`, `car-monitor-toolbar`, `car-estop-reset`,
`car-projects`, `car-renderer`, `examples-menu`, `power-chip`, `room-map`, `final-review`) with `mock-link.js`.
Run: `docker compose run --rm editor-test`. End to end: [tests](#tests).

**Gotchas.**

- The Car Monitor has no E-STOP of its own (decision D2 in [decisions.md](decisions.md)); the floating E-STOP
  appears only in full-screen mode.
- The room map is drawn from dead-reckoned poses on the real car, so it drifts with calibration errors; Clear map
  or Reset position starts it again.
- Every file added or changed under `editor/` must be listed in
  [modified-upstream-files.md](modified-upstream-files.md) (AGPL-3.0 section 5(a)).

## Examples generator

**Purpose.** The example projects are written as Python and packed into `.sb3` files, so they can be reviewed as
code and rebuilt identically.

**Key files.** [`examples/tools/sb3.py`](../examples/tools/sb3.py) (a small DSL: `Project`, `Stage`, `Sprite`,
`Blk`, `V`, `Menu`, `Bc`, `Color`, and helpers for the Scratch and `picar_*` blocks),
[`examples/tools/pack.py`](../examples/tools/pack.py), [`examples/defs/`](../examples/defs) (`_common.py` for
the backdrop, the `Car` sprite from `assets/branding/car-sprite.svg` and button art, then one file per example).

| id | Title | World |
|---|---|---|
| `01-hello-car` | Hello car | arena |
| `02-touch-remote` | Touch remote | arena |
| `03-distance-meter` | Distance meter | corridor |
| `04-obstacle-stop` | Obstacle stop | corridor |
| `05-obstacle-avoider` | Obstacle avoider | arena |
| `06-parking-sensor` | Parking sensor | corridor |
| `07-line-follower` | Line follower | oval |
| `08-light-follower` | Light follower | lamp |
| `09-figure-eight` | Figure eight | corridor |
| `10-safety-lab` | Safety lab | corridor |

**Interfaces.** Each definition exports `META` (`id`, `title`, `description`, `level`, `needs`, `world`) and
`build() -> Project`. `python3 examples/tools/pack.py OUT_DIR` writes `OUT_DIR/<id>.sb3` and `OUT_DIR/index.json`.
Output is deterministic (counter ids, fixed zip timestamps); scripts are laid out in one column by estimated
block height (the `x, y` passed to `script()` are ignored).

**Configuration.** None. Build: `docker run --rm -v "$PWD:/repo" -w /repo python:3.13-slim python3
examples/tools/pack.py out/examples`; `deploy.sh` runs `pack.py` too.

**Tests.** `extension_picar_examples.js` (scratch-vm; loads every packed example from `out/examples`),
[`e2e/tests/09-examples.spec.ts`](../e2e/tests/09-examples.spec.ts), and the probes in `e2e/probe/`.

**Gotchas.** Field menus are stored under the argument name (`DIR`, `STEER`, `LIGHT`, ...), not the menu name;
the opposite once made every example drive straight and forward. The stage mirror looks for a sprite named
exactly `Car`.

## Deploy

**Purpose.** Build artefacts on the workstation, ship a release to the car, and set the Pi up once.

**Key files.** [`deploy/deploy.sh`](../deploy/deploy.sh), [`deploy/test-deploy.sh`](../deploy/test-deploy.sh),
[`deploy/pi-setup.sh`](../deploy/pi-setup.sh), [`deploy/ops-user-setup.sh`](../deploy/ops-user-setup.sh),
[`deploy/car-scratch.service`](../deploy/car-scratch.service),
[`docker/editor.Dockerfile`](../docker/editor.Dockerfile), [`docker/server.Dockerfile`](../docker/server.Dockerfile),
[`docker/deploy.Dockerfile`](../docker/deploy.Dockerfile), [`compose.yaml`](../compose.yaml).

**Interfaces.**

- `deploy.sh [--dry-run]`: clean-tree check; source tarball, examples and the staged server; refuses an
  `out/www` whose `build-sha.txt` is not HEAD; rsync to `~/car-scratch/releases/<sha>`; switch `current`; install
  the user unit, `daemon-reload`, `enable`, `restart`; health check (`/api/status` must report the sha, 10 tries 2 s
  apart); on any failure roll back to the previous release and unit; keep 3 releases. The flow is drawn in
  [architecture.md](architecture.md) section 7.
- `editor.Dockerfile` stages: `deps` (node 24.22.3, `npm ci`), `src` (builds the sibling packages), `build`
  (webpack, `build-sha.txt`, source maps removed, `.gz` copies of files over 1 KB), `test` (for `editor-test`),
  `www` (the output). `server.Dockerfile`: Debian trixie with the same apt packages as the Pi; `test` adds pytest.
  `deploy.Dockerfile`: Alpine with ssh, rsync, git and python3; copies `~/.ssh` to a private directory first.
- `compose.yaml` services: `server-test`, `editor-test`, `sim-server` (host port 18080), `e2e`, `deploy` (profile
  `deploy`; default command is the dry run).

**Configuration.** `PICAR_HOST`, `PICAR_USER` (deploy target), `PICAR_SSH_DIR` (key directory for the deploy
container), `BUILD_SHA` (editor build); `CAR_USER` for `pi-setup.sh`; `OPS_USER`, `OPS_FROM` for
`ops-user-setup.sh`.

**Tests.** `docker compose run --rm deploy sh deploy/test-deploy.sh` (stub ssh and rsync: a stale bundle is
refused, a dry run only warns, the happy path, a failed restart rolls back).

**Gotchas.**

- Build the editor with `--build-arg BUILD_SHA=$(git rev-parse --short=12 HEAD)`; without it the bundle says
  `dev` and `deploy.sh` refuses it.
- `deploy.sh` refuses a dirty tree; `out/` is gitignored, so build output does not count.
- `pi-setup.sh` turns Wi-Fi power-save off: its latency spikes trip the 750 ms watchdog. Re-run it after a deploy
  that changes `safe_stop.py`.
- Host port 8080 is reserved on the workstation, so the Docker simulator is on `http://localhost:18080`.

## Tests

**Purpose.** Everything runs in containers; nothing needs a local Python or Node toolchain.

| Suite | Location | Run | Covers |
|---|---|---|---|
| Server | [`server/tests/`](../server/tests) (pytest, `asyncio_mode = auto`; fakes in `conftest.py` and `fakes_hw.py`) | `docker compose run --rm server-test` | Controller rules, protocol, sessions and arbitration, REST, static files, HAL with fake bus/GPIO/clock, simulator, power, tools, review regressions |
| VM | `editor/packages/scratch-vm/test/unit/extension_picar*.js` (tap) | `docker compose run --rm editor-test` | Blocks, hats, CarLink coalescing/reconnect/estop, stage mirror, every packed example loads |
| GUI | `editor/packages/scratch-gui/test/unit/picar/` (jest) | same command | Car Monitor, toolbar, E-STOP Reset, projects modal, renderer, room map, power chip, Examples menu |
| End to end | [`e2e/tests/`](../e2e/tests) `01-smoke` to `11-ipad` (Playwright) | `docker compose run --rm e2e` (needs `out/www`; starts `sim-server`) | Smoke, sim driving, stop sign, e-stop, watchdog, visibility, viewer, reconnect, examples, save/open, iPad |
| Probes | [`e2e/probe/`](../e2e/probe) | `-c probe/playwright.probe.config.ts`, against the simulator | Diagnostic timelines of examples (`PROBE_SECONDS`), layout, stage mirror; not pass/fail suites |
| On car | [`e2e/oncar/`](../e2e/oncar) | `-c oncar/playwright.oncar.config.ts` with `BASE_URL=http://<car>:8080` | E-STOP then Reset from the menu bar against the real car; never sends a motion command |
| Deploy | [`deploy/test-deploy.sh`](../deploy/test-deploy.sh) | `docker compose run --rm deploy sh deploy/test-deploy.sh` | See [deploy](#deploy) |

The probe and on-car configs run inside the `e2e` service, for example
`docker compose run --rm e2e npx playwright test -c probe/playwright.probe.config.ts` once a default run has
installed `e2e/node_modules`.

**Gotchas.**

- The default e2e suite runs with one worker: the car-mode tests share one simulated car. The iPad projects
  (WebKit) run only the smoke and iPad specs; if WebKit has no WebGL headless, run them under `xvfb-run` (see
  `playwright.config.ts`).
- `editor-test` bind-mounts the scratch-vm and scratch-gui sources, so edits need no image rebuild, but
  `extension_picar_examples.js` needs `out/examples` packed first.
- `deploy.sh` refuses a bundle built from another commit, so rebuild `out/www` before a deploy, not only before
  e2e.
