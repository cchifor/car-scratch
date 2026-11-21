# Car Scratch architecture

Car Scratch has two halves. The **editor** is a fork of the Scratch 3 editor (`editor/`, built into static
files) with a Pi Car block extension, a Car Monitor and car-specific menus. The **car server**
(`server/picar_server`, Python + aiohttp) runs on the Raspberry Pi 3 B+ of the SunFounder PiCar-S, serves the
editor, and owns the motors, the sensors, the simulator and every safety rule. The browser can ask for motion;
it never has to remember to stop.

This page shows how the parts fit together. For details see:

- [components.md](components.md): one section per component (files, interfaces, tests, gotchas).
- [protocol.md](protocol.md): the wire protocol (WebSocket + REST), authoritative.
- [contracts.md](contracts.md): safety semantics, the HAL, the simulator world format, `CarLink`, the blocks.
- [safety.md](safety.md): the safety guide for the people driving the car.

## 1. System context

```mermaid
flowchart LR
    subgraph lan["Home LAN"]
        desk["Desktop browser"]
        ipad["iPad: Safari, Chrome<br/>or Home Screen app"]
        subgraph car["PiCar-S: Raspberry Pi 3 B+, picar.local"]
            srv["picar_server<br/>aiohttp, port 8080"]
            hw["Motors, steering servo,<br/>ultrasonic, light, line sensors"]
        end
        subgraph wkst["Workstation"]
            dock["git, and Docker: editor build,<br/>tests, sim-server on port 18080"]
        end
    end
    gitea["Gitea<br/>git.chifor.me/cchifor/car-scratch"]

    desk -->|"HTTP: editor, /examples, /source, /api/*"| srv
    ipad -->|"HTTP: editor, /examples, /source, /api/*"| srv
    desk -->|"WebSocket /ws, both ways"| srv
    ipad -->|"WebSocket /ws, both ways"| srv
    srv -->|"I2C and GPIO"| hw
    dock -->|"ssh + rsync, deploy/deploy.sh"| srv
    dock -->|"git push and pull"| gitea
```

Everything a browser needs comes from one origin, `http://picar.local:8080`, on the home LAN only (no internet
exposure). The workstation never talks to the car at run time: it builds the editor and the examples in Docker
and deploys a release over SSH.

| URL on the car | What |
|---|---|
| `/` and `/{path}` | the editor build (`www/`), precompressed `.gz` files served when the browser accepts gzip |
| `/ws` | the WebSocket: `hello`/`welcome`, commands, `tel` at 20 Hz, events ([protocol.md](protocol.md) sections 2 to 5) |
| `POST /api/estop` | latches the e-stop on the shared car; the E-STOP button sends it next to the WebSocket `estop` |
| `GET /api/status` | version, deployed `sha`, car availability, client counts (the deploy health check reads it) |
| `/api/projects`, `/api/projects/{name}.sb3` | "Save to car" / "Open from car" storage |
| `/examples/index.json`, `/examples/{id}.sb3` | the bundled example projects |
| `/source/` | the AGPL source tarball of the deployed commit |

The Host header must be in the allow-list and a present `Origin` must match `Host` (DNS-rebinding defence) for
`/ws` and every unsafe REST method; see [protocol.md](protocol.md) section 1.

## 2. Components

### 2.1 Editor side

```mermaid
flowchart TB
    subgraph gui["scratch-gui"]
        menu["Menu bar: Examples menu, File menu,<br/>CarEstop with E-STOP and Reset"]
        modal["CarProjectsModal<br/>Save to car, Open from car"]
        tabs["StagePaneTabs<br/>Sprites or Pi Car"]
        mon["CarMonitor: Car or Sim switch,<br/>Room or Follow, power chip,<br/>status chip, banners, toolbar"]
        canvas["MonitorCanvas<br/>car-renderer, draw-scene, room-map"]
        flt["Floating CarEstop<br/>over the full-screen stage"]
        glob["PicarGlobal<br/>rotate hint, audio unlock, About"]
        libp["lib/picar: useCarLink,<br/>projects-api, examples-api"]
    end
    subgraph vm["scratch-vm"]
        ext["Scratch3PiCarBlocks, extension picar<br/>blocks, hats, armed gate"]
        mirror["Stage mirror<br/>Car sprite + own pen skin"]
        clink["CarLink<br/>coalescer, bypass, heartbeat,<br/>reconnect, E-STOP"]
    end
    srv["Car server"]

    menu --> modal
    tabs --> mon --> canvas
    menu --> libp
    modal --> libp
    mon --> libp
    flt --> libp
    glob --> libp
    libp -->|"runtime.peripheralExtensions.picar.link"| clink
    ext --> clink
    ext --> mirror
    clink -.->|"telemetry events"| canvas
    clink -->|"WebSocket /ws, POST /api/estop"| srv
    libp -->|"REST /api/projects, GET /examples"| srv
```

The VM extension owns the one `CarLink` per page; every GUI piece finds it through
`vm.runtime.peripheralExtensions.picar.link` (the `useCarLink` hook) and never opens its own socket. The Car
Monitor canvas takes telemetry straight from the link and paints it in a `requestAnimationFrame` loop, so React
only re-renders when a coarse flag (e-stop, hold, power, role) changes.

### 2.2 Car side

```mermaid
flowchart TB
    subgraph proc["picar_server process"]
        app["app.py<br/>routes, static files, startup"]
        store["ProjectStore<br/>projects.py"]
        sess["Session per socket<br/>validate, rate limit, write loop"]
        hub["Hub<br/>arbitration, lease, promotion,<br/>telemetry 20 Hz"]
        car["Controller, shared car<br/>commands, safety, tick"]
        sim["Controller per sim session<br/>SimBackend, worlds"]
        pow["PowerMonitor<br/>power.py"]
        cfg["config.py<br/>calibration.json"]
        rules["safety.py<br/>pure rules"]
        hal["RealBackend or DryRunBackend<br/>motor lock, latches"]
        sens["Sensor thread<br/>SensorHub, about 25 Hz"]
        sdn["sd_notify loop"]
    end
    chip["PCA9685, TB6612,<br/>servo, sensors"]
    subgraph sysd["systemd"]
        unit["car-scratch.service<br/>user unit, WatchdogSec=3"]
        sstop["safe_stop<br/>ExecStartPre, ExecStopPost"]
        boot["car-safe-stop.service<br/>at boot"]
    end

    app --> sess --> hub --> car
    app --> store
    sess --> sim
    hub --> pow
    cfg --> car
    car --> rules
    sim --> rules
    car --> hal --> chip
    hal --> sens
    sens -->|"on_sample: obstacle guard"| car
    sdn -->|"READY, WATCHDOG"| unit
    unit -->|"starts"| app
    sstop -->|"zero ch4, ch5"| chip
    boot -->|"zero ch4, ch5"| chip
```

There is one shared-car `Controller` per server and one private `Controller` + `SimBackend` per simulator session
(at most 4). The same `Controller` class and the same `safety.py` rules run in both, so the simulator, the pytest
suite and the e2e tests exercise the code that drives the real car. `--hardware` picks the backend at start:
`auto` and `real` load `RealBackend`, `dryrun` reads the real sensors but only logs motor writes, `sim` puts a
simulated car behind the shared controller (the Docker `sim-server`), `none` runs without a car.

## 3. A timed drive, end to end

The block "drive forward at 40 % for 1 seconds" (`driveFor`) on the real car:

```mermaid
sequenceDiagram
    autonumber
    participant B as driveFor block, VM thread
    participant L as CarLink
    participant S as Session
    participant C as Controller
    participant H as RealBackend
    participant P as PCA9685 and TB6612
    participant T as Hub telemetry loop
    participant M as Car Monitor, stage mirror, reporters

    B->>L: move forward, speed 40, 1000 ms
    Note over L: move is a bypass op: a pending drive or set_speed is dropped,<br/>a pending steer or config is sent first
    L->>S: cmd id N, op move, dir forward, speed 40, ms 1000
    S->>S: rate limit, validate_cmd, controller check
    S->>C: command move
    C->>C: effective speed, latches, reverse budget,<br/>forward check at the proposed speed
    C->>H: set_motors 2252, 2252
    H->>P: under the motor lock: on a direction change PWM 0, then pins HIGH,<br/>then PWM 2252 on both channels
    C->>C: forward check again, start the 1 s move task
    S-->>L: ack id N, speed 40, ms 1000
    Note over S,C: an accepted frame feeds the watchdog
    loop every 50 ms
        T->>S: tel: drive, dist, pose, safety, power
        S-->>L: tel, latest-only
        L-->>M: telemetry event
    end
    C->>H: after 1 s: stop_motors
    H->>P: PWM 0 on both motor channels
    C-->>S: done id N, completed
    S-->>L: done id N, completed
    L-->>B: the promise resolves, the script continues
```

The timed move runs on the server, so a browser that freezes or disconnects cannot leave the motors running.
Speed 40 becomes duty 55 % (2252 of 4095) with the default floor 25 % and ceiling 100 %
([contracts.md](contracts.md) section 3). Plain `drive` blocks go through the 50 ms coalescer instead and get only
an `ack`; only `move` gets a `done`.

### Obstacle stop without the browser

```mermaid
sequenceDiagram
    participant G as Sensor thread
    participant C as Controller guard
    participant H as RealBackend
    participant P as PCA9685
    participant E as Event loop, Controller drain
    participant U as Hub and sessions
    participant W as Browser: CarLink, extension, monitor

    G->>G: SensorHub.read, median of 3 pings while driving forward
    G->>C: on_sample, dist ok, closer than margin_cm
    C->>C: snapshot under Controller._lock:<br/>current move, motion direction, margin
    C->>C: guard_decision returns obstacle
    C->>H: inhibit_forward
    H->>P: latch the inhibit, zero both motor channels under the motor lock
    C->>C: set blocked
    C->>E: defer result obstacle and event obstacle_stop,<br/>bound to that move, via call_soon_threadsafe
    E->>U: done id N, obstacle, to the move owner
    E->>U: event obstacle_stop with cm, to every car-mode session
    U-->>W: driveFor resolves, obstacle hats start if armed,<br/>the monitor flashes the obstacle banner
    Note over C,H: forward writes stay refused until 1 s of samples at margin_cm + 5 cm or far
```

The motors stop in the sensor thread, before the event loop hears about it. The loop only reports what already
happened; a stalled loop or a lost browser cannot delay the stop.

## 4. Concurrency on the car

```mermaid
flowchart LR
    subgraph loopt["Event loop"]
        l1["Sessions: read, validate,<br/>write loops"]
        l2["Hub telemetry 20 Hz<br/>+ promote_waiting"]
        l3["Controller.tick every 50 ms<br/>watchdog, reverse, freshness"]
        l4["Controller drain:<br/>finish moves, emit events"]
        l5["Move and reverse-deadline tasks"]
        l6["PowerMonitor apply,<br/>vcgencmd subprocess"]
        l7["sd_notify loop, 0.5 s"]
    end
    subgraph sthr["Sensor thread, 40 ms"]
        s1["Deadline probe check"]
        s2["SensorHub.read"]
        s3["Controller guard"]
    end
    subgraph pool["Executor threads"]
        w1["PowerMonitor sysfs reads"]
        w2["ProjectStore file I/O"]
    end
    subgraph hand["Hand-over points"]
        k1["Controller._lock"]
        k2["Motor lock, outer<br/>I2C bus lock, inner"]
        k3["Backend latches:<br/>forward inhibit, full stop"]
        k4["Deferred queue +<br/>call_soon_threadsafe"]
        k5["last_loop_tick,<br/>last_controller_frame"]
    end
    l1 -->|"touch"| k5
    l3 -->|"writes"| k5
    s1 -.->|"reads"| k5
    s3 --> k1
    l3 --> k1
    s3 --> k3
    l3 --> k3
    k3 --> k2
    s2 --> k2
    s3 --> k4 --> l4
    l6 -->|"to_thread"| w1
    l1 -->|"run_in_executor"| w2
```

The server has one event loop, one sensor thread (`picar-sensors`, real and dry-run backends only) and
short-lived worker threads from asyncio's default executor. The sensor thread can stop the motors on its own at any time; everything
else that needs the loop (events, `done`, `role`) is handed over through a queue.

| Thread | Owns | Hands over through |
|---|---|---|
| Event loop | Sessions and arbitration, the controller's motion intent (`_dir`, `_eff`, timed moves, the motion generation `_gen`, the reverse budget), `cfg`, the e-stop and hold flags it sets, telemetry, every simulator backend and its physics, PowerMonitor state and events, sd_notify | Writes `last_loop_tick` (every tick) and `last_controller_frame` (`touch()` on an accepted controller frame); mirrors e-stop/hold/hw_fault into the backend with `_sync_latch()`; checks `_gen` before a timer touches the motors |
| Sensor thread (`_HardwareBase._run`) | The ultrasonic pin (GPIO5) and its timing, `SensorHub` state, sample production, the deadline check | Writes the controller's sensor fields (`_sample`, `_last_good_t`, `_last_ok`, ...) and `blocked` under `Controller._lock`; stops motors through the backend (`inhibit_forward`, `stop_motors`, both under the motor lock); latches hold or `hw_fault` via `_deadline_latch`; queues results with `_defer` and schedules `_drain` with `loop.call_soon_threadsafe` |
| Worker threads | Only file reads (hwmon `rpi_volt` alarm, thermal zone, `/proc/uptime`, load average) and project file I/O | Return values to the loop; the loop applies them (`PowerMonitor._apply`) |

Rules that keep this safe (verified in [controller.py](../server/picar_server/controller.py),
[hal/real.py](../server/picar_server/hal/real.py), [hal/sensors.py](../server/picar_server/hal/sensors.py),
[power.py](../server/picar_server/power.py), [session.py](../server/picar_server/session.py)):

- **Lock order:** the motor lock (outer) is held across a whole motor operation; the I2C bus lock (inner) is
  taken per transaction. Never take the motor lock while holding the bus lock. `stop_motors()` waits at most
  0.2 s for the motor lock and returns `False` instead (the controller then latches `hw_fault`).
- **Latches beat writes:** `set_motors()` checks the full-stop latch and the forward inhibit under the motor
  lock, so a stop can never interleave with a set, and a write that loses the race writes zero.
- **Deadline probe:** the sensor thread calls `Controller._deadline_probe()` every iteration while the motors run.
  If the loop has not ticked for 1 s, or the attached controller has been silent for more than `wd_ms` (750 ms),
  it stops the motors itself and calls `_deadline_latch()` (hold, reason `watchdog`; `hw_fault` if the stop could
  not be confirmed). There is no separate liveness thread.
- **Deferred results are bound to a move object:** a stop from the sensor thread finishes only the move that
  was current when it stopped the motors; a move started afterwards is never touched.
- **The sensor thread never dies with the wheels turning:** every exception stops the motors, and its `finally`
  stops them again. It sets the process-wide `sys.setswitchinterval(0.0005)` so GIL hand-offs do not stretch the
  echo timing.
- **The line follower is probed only while the motors are stopped** (it waits at most 50 ms for the motor lock,
  else skips that sample), and only with `line_enabled` in the calibration.
- **Recurring I/O stays off the loop:** PowerMonitor runs its sysfs reads with `asyncio.to_thread` (skipping a
  pass while the previous read is still stuck) and `vcgencmd` as an async subprocess with a timeout; project
  files go through `run_in_executor`. Only one-off work runs on the loop: the startup reads and a
  `calibrate` save.
- **The simulator has no sensor thread:** `SimBackend` samples on the loop at 25 Hz and the controller runs the
  deadline check inside the guard itself.

## 5. Safety layers

```mermaid
flowchart TB
    subgraph L1["Layer 1: browser"]
        direction TB
        b1["pause when the tab is hidden"]
        b2["E-STOP: WebSocket estop<br/>+ POST /api/estop"]
    end
    subgraph L2["Layer 2: event loop"]
        direction TB
        c1["Command-time checks:<br/>latches, forward check"]
        c2["Watchdog: 750 ms silence,<br/>stop + hold"]
        c3["Hold on every acquisition,<br/>pause and disconnect"]
        c4["Latched E-STOP from anyone,<br/>REST too, and hw_fault"]
        c5["Reverse: speed 40 max,<br/>5 s budget"]
    end
    subgraph L3["Layer 3: sensor thread"]
        direction TB
        s1["Obstacle guard<br/>on every sample"]
        s2["Sensor validity: first sample,<br/>fault, 150 ms staleness"]
        s3["Deadline probe: loop 1 s,<br/>controller 750 ms"]
    end
    subgraph L4["Layer 4: backend"]
        direction TB
        h1["Forward inhibit and full-stop latch<br/>under the motor lock"]
    end
    subgraph L5["Layer 5: systemd"]
        direction TB
        p1["WatchdogSec=3,<br/>Restart=always"]
        p2["safe_stop: ExecStartPre<br/>and ExecStopPost"]
    end
    subgraph L6["Layer 6: boot, hardware"]
        direction TB
        q1["car-safe-stop.service<br/>at boot"]
        q2["Hardware watchdog,<br/>RuntimeWatchdogSec=10"]
        q3["Battery switch"]
    end
    L1 --> L2 --> L3 --> L4 --> L5 --> L6
```

Each layer works without the ones above it. The browser layer is a convenience only: the server never relies on
it. The sensor thread stops the car
when the loop stalls, systemd restarts a hung server and zeroes the motors after any exit, and the SoC watchdog
reboots a hung Pi, after which `car-safe-stop.service` zeroes the motors again. The rules and their numbers are
specified in [contracts.md](contracts.md) section 1; what they mean for the people at the car is in
[safety.md](safety.md).

## 6. Control arbitration

Only car-mode sessions compete for the car; a sim session always controls its own private simulator. The seat:

```mermaid
stateDiagram-v2
    state "Seat free" as Free
    state "Seat leased" as Leased

    [*] --> Free: server start, car on hold, reason boot
    Free --> Controlled: hello want control, control take, mode car, or promote_waiting
    Controlled --> Controlled: take with force by another session, the old controller becomes a viewer
    Controlled --> Free: control release, or the controller switches to sim
    Controlled --> Leased: the controller socket closes
    Leased --> Controlled: the same client_id sends hello or take
    Leased --> Controlled: take with force by anyone
    Leased --> Free: the 30 s lease expires

    note right of Controlled
        Every entry starts on hold.
        Only resume clears it.
        The e-stop survives a hand-over.
    end note
    note right of Leased
        Car stopped, hold latched.
        No promotion while the lease is live.
    end note
    note left of Free
        Checked on every telemetry tick:
        the longest-waiting viewer that asked
        for control is promoted.
    end note
```

And one session's role:

```mermaid
stateDiagram-v2
    state "Viewer, waiting for control" as Waiting
    state "Viewer, not waiting" as Passive
    state "Controller" as Ctl
    state "Sim session" as Sim

    [*] --> Ctl: hello want control, seat free or own lease
    [*] --> Waiting: hello want control, seat taken or client_id already live
    [*] --> Passive: hello want view, or a failed sim hello
    [*] --> Sim: hello mode sim
    Waiting --> Ctl: promote_waiting, or take with force
    Passive --> Ctl: control take, seat free or force
    Passive --> Waiting: control take refused
    Ctl --> Passive: control release
    Ctl --> Waiting: another session takes with force
    Ctl --> [*]: socket closes, lease for its client_id
    Ctl --> Sim: mode sim, control released
    Waiting --> Sim: mode sim
    Sim --> Ctl: mode car, seat free
    Sim --> Waiting: mode car, seat taken
```

`Hub.take()` grants the seat when it is free and no other client holds a live lease, or always with `force`
(the car is stopped and held first). `Hub.detach()` turns a lost controller into a 30 s lease for its
`client_id`: an iPad tab that comes back gets the car again without `force`, on hold, with its config kept.
`Hub.promote_waiting()` runs on every telemetry tick: when there is no controller and no live lease it hands the
car to the longest-waiting car-mode session that asked for control (hello order), never to one that sent
`control release` or to a duplicate of a live `client_id`. This promotion was added on the car on 2026-11-21:
without it a tab opened during a lease stayed VIEW ONLY and could not reset an e-stop. The rules are specified in
[protocol.md](protocol.md) section 2.

The hold latch is set with a reason and cleared only by `resume` (the Resume button, the green flag, or
`wsclient drive`/`move`):

| Reason | Set by |
|---|---|
| `boot` | server start (`Controller(start_on_hold=True)`) |
| `control` | every acquisition of the seat (`new_controller`), a forced take-over, a release |
| `pause` | `cmd pause` (CarLink sends it when the page is hidden) |
| `watchdog` | the tick watchdog, or the sensor thread's deadline probe |
| `disconnect` | the controller's socket closed (`controller_lost`) |

The e-stop is a separate latch: anyone can set it (any mode, viewers, `POST /api/estop`), only the controller
can clear it with `estop_reset`. The GUI shows **Reset** next to E-STOP while it is latched and this session may
reset it.

## 7. Build and deployment

```mermaid
flowchart TB
    commit["Commit, clean tree"]
    build["docker buildx build -f docker/editor.Dockerfile<br/>--target www, BUILD_SHA = 12-char sha<br/>into out/www with build-sha.txt"]
    dep["docker compose run --rm deploy<br/>sh deploy/deploy.sh"]
    clean{"git tree clean?"}
    art["Source tarball to out/source,<br/>pack.py to out/examples,<br/>server + unit to out/stage"]
    sha{"out/www/build-sha.txt<br/>equals HEAD?"}
    up["rsync to ~/car-scratch/releases/SHA"]
    sw["Switch the current symlink,<br/>install the user unit, daemon-reload,<br/>enable, restart"]
    health{"GET /api/status reports SHA<br/>within 10 tries, 2 s apart?"}
    prune["Keep the last 3 releases"]
    rb["Roll back: previous current,<br/>previous unit, restart"]
    refuse["Refuse, nothing sent"]

    commit --> build --> dep --> clean
    clean -->|"no"| refuse
    clean -->|"yes"| art --> sha
    sha -->|"no"| refuse
    sha -->|"yes"| up --> sw --> health
    sw -->|"a step fails"| rb
    health -->|"yes"| prune
    health -->|"no"| rb
```

The editor is built on the workstation (the Pi 3 cannot run webpack) with
`--build-arg BUILD_SHA=$(git rev-parse --short=12 HEAD)`; `deploy.sh` refuses a bundle built from any other
commit. The deploy container (`docker/deploy.Dockerfile`) runs the whole script; without arguments
(`docker compose run --rm deploy`) it is a dry run that prints the plan and contacts nothing. `PICAR_HOST` and
`PICAR_USER` choose the target.

What a release looks like on the car:

```mermaid
flowchart LR
    subgraph base["~/car-scratch"]
        cur["current symlink"]
        rel["releases/SHA: server, www,<br/>examples, source, REVISION.env,<br/>car-scratch.service"]
        old["up to 2 older releases"]
    end
    unitf["~/.config/systemd/user/<br/>car-scratch.service"]
    calib["~/.config/car-scratch/<br/>calibration.json"]
    data["~/.local/share/car-scratch/<br/>projects"]
    subgraph rootf["Root-owned, from pi-setup.sh"]
        lib["Files in /usr/local/lib/car-scratch:<br/>safe_stop.py, safe-stop-wait.sh"]
        bootu["car-safe-stop.service"]
        wdog["car-scratch-watchdog.conf<br/>RuntimeWatchdogSec=10"]
    end
    cur --> rel
    unitf -->|"PYTHONPATH, --www, --examples, --source"| cur
    unitf -->|"--config"| calib
    unitf -->|"--data"| data
    bootu --> lib
```

`REVISION.env` carries `CAR_SCRATCH_SHA`, which the server reports as `server.sha` and in `/api/status`; the
browser compares it with its own build sha and asks for a reload on a mismatch. Calibration and saved projects
live outside the releases, so a deploy or a rollback never touches them.

One-time setup on the Pi, run with `sudo`:

- [deploy/pi-setup.sh](../deploy/pi-setup.sh) (idempotent): apt packages (`python3-aiohttp`, `python3-smbus2`,
  `rsync`, `python3-rpi-lgpio`), linger for the car user (the user unit starts at boot), Wi-Fi power-save off
  (its latency spikes would trip the 750 ms watchdog), the directories above, the hardware watchdog drop-in, and
  the boot-time `car-safe-stop.service` with a root-owned copy of `safe_stop.py`. Re-run it after a deploy that
  changes `safe_stop.py`.
- [deploy/ops-user-setup.sh](../deploy/ops-user-setup.sh): the optional `carops` maintenance account (SSH key
  only, keys limited to the home LAN with `from=`, passwordless sudo validated with `visudo`); `--remove` deletes
  it again.

## 8. Where things live

```
editor/packages/scratch-vm/src/extensions/scratch3_picar/   blocks (index.js), CarLink, protocol constants
editor/packages/scratch-gui/src/components/car-monitor/      Car Monitor, E-STOP button, banners, canvas, room map
editor/packages/scratch-gui/src/components/car-projects-modal/   Save to car / Open from car
editor/packages/scratch-gui/src/components/menu-bar/examples-menu.jsx   Examples menu
editor/packages/scratch-gui/src/components/stage-pane-tabs/  Sprites | Pi Car tabs
editor/packages/scratch-gui/src/containers/                  car-monitor.jsx, car-estop.jsx, picar-global.jsx
editor/packages/scratch-gui/src/lib/picar/                   link hooks, REST helpers, storage helpers
server/picar_server/                 app, protocol, session, controller, safety, config, power, projects,
                                     safe_stop, sdnotify
server/picar_server/hal/             base, pca9685, sensors, real, dryrun
server/picar_server/hal/sim/         simulator backend, world geometry, worlds/*.json
server/tools/                        wsclient.py, calib.py
server/tests/                        pytest suite
server/legacy/                       the original hardware test scripts (not deployed)
examples/defs/, examples/tools/      example definitions; sb3.py DSL and pack.py
e2e/tests/, e2e/probe/, e2e/oncar/   Playwright: default suite, simulator probes, real-car checks
deploy/                              deploy.sh, test-deploy.sh, pi-setup.sh, ops-user-setup.sh, the user unit
docker/, compose.yaml                editor, server and deploy images; dev, test, simulator, deploy services
assets/branding/                     icons and the car sprite used by the examples
out/                                 build output, gitignored: www/, examples/, source/, stage/, e2e/
```
