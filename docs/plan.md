# Car Scratch — implementation plan

## Context

The SunFounder PiCar-S (Raspberry Pi 3 B+, `picar` / 192.168.0.209) now works end to end from a
Python test program (`~/picar/obstacle_stop.py`): wiring was fixed, motor direction pins and
"forward" were measured, the servo arm was refit, the ultrasonic obstacle stop is proven on the floor.
The goal now is to program the car **visually from Scratch**, from any browser on the home LAN
**including an iPad**, with a live **animation** of what the car is doing, a **simulator** for
working without the car, and a set of **example programs**. Name: **Car Scratch** (owner's choice).
Implemented headless today in Docker Compose (see §11a); deployed and tested on the car tomorrow
together with the owner.

**Decided with the owner:** new Gitea repo `git.chifor.me/cchifor/car-scratch`; **home LAN only**
(`http://picar.local:8080`, no internet exposure); base = **official Scratch 3**
(`scratchfoundation/scratch-editor` monorepo @ `105e22ea`, v15.3.0, AGPL-3.0-only, Node 24.22.3;
the standalone scratch-vm repo was archived 2026-06-10).

### Hardware map (measured 2026-11-20; source of truth for the HAL)

| Part | Connection |
|---|---|
| PCA9685 @ I2C-1 `0x40`, 60 Hz | ch0 steering servo (600–2400 µs over 0–180°, 90 = straight, <90 left, usable 45–135); **ch4 = right motor**, **ch5 = left motor** speed |
| TB6612 direction | **GPIO27 = right motor, GPIO17 = left motor** (swapped vs SunFounder `back_wheels.py`); **HIGH = forward** |
| Ultrasonic | single SIG pin **GPIO5** (trigger, then input); ~6 ms/ping, "far" at oblique walls |
| PCF8591 `0x48` | light follower, channels 0–2 |
| Line follower `0x11` | 5 IR sensors — **not responding (cable)**; supported as optional/degraded |
| Pi | Debian 13 trixie, Python 3.13, user `c4` (gpio,i2c), sudo needs owner password; has python3-rpi-lgpio, smbus, smbus2, i2c-tools |

Reuse: `PCA9685`, `Car`, `Ultrasonic`, `load_trim/save_trim` from
the owner's test program `~/picar/obstacle_stop.py` on the car
(copied into the repo first, as `server/legacy/`).

## Architecture

```
 Browser (desktop / iPad Safari)                     Raspberry Pi 3 (picar.local:8080)
 ┌──────────────────────────────────────┐   HTTP    ┌──────────────────────────────────────┐
 │ Car Scratch = scratch-gui fork    │◄──────────│ picar_server (Python, aiohttp)       │
 │  • "Pi Car" blocks (scratch-vm ext)  │  static   │  • static build, examples, /source   │
 │  • CarLink (WebSocket client)        │◄────────► │  • /ws  protocol + control arbitration│
 │  • Car Monitor (canvas animation)    │  /ws JSON │  • Controller + safety (server-side) │
 │  • Examples menu, Save/Open on car   │  /api     │  • HAL: real | dryrun | sim backend  │
 └──────────────────────────────────────┘           └──────────────────────────────────────┘
```

Editor is built on the workstation in Docker (the Pi 3 cannot run webpack) and deployed as static
files. **All safety is enforced on the car**, never in the browser.

## 1. Repo layout (`cchifor/car-scratch`, AGPL-3.0-only)

```
editor/        squashed git-subtree of scratch-editor@105e22ea + our changes (keep ~15 small insertion points)
server/picar_server/   Python server;  server/tests/ (pytest);  server/tools/ (obstacle_stop.py on the HAL, wsclient.py, calib.py)
examples/src/<id>/     unpacked project.json + assets (reviewable);  examples/index.json;  examples/tools/{pack,unpack}.py
e2e/           Playwright specs (chromium + WebKit iPad profiles)
docker/        editor.Dockerfile, server.Dockerfile (debian:trixie-slim), deploy.Dockerfile
deploy/        deploy.sh, pi-setup.sh (idempotent one-time setup), car-scratch.service (systemd user unit)
docs/          protocol.md, safety.md, calibration.md, on-car-checklist.md, ipad.md
compose.yaml  LICENSE  NOTICE (upstream attribution + modified-files list)  .gitattributes (LF for sh/py/service)
```

Upstream sync: `git subtree pull --prefix=editor … develop --squash` (history is 5 GB; HEAD tree 92 MB).
Conventional Commits (upstream commitlint), no AI attribution, PRs on Gitea.

## 2. Car server — `server/picar_server` (Python 3.13, apt packages only)

Pi needs `python3-aiohttp` (3.11.16) + `python3-smbus2`; lgpio already installed. One asyncio loop
(aiohttp, controller, telemetry, simulators) + a **sensor thread** (ultrasonic busy-wait never on the
loop) + a **liveness thread** (stops motors if the loop stalls > 1 s).

| Module | Role |
|---|---|
| `__main__.py` | `--port 8080 --hardware auto\|real\|dryrun\|sim --www --examples --data --config` |
| `app.py` | routes: static (path-confined, serves `.gz`/`.br` siblings), `/ws`, `/api/status`, `/api/projects*`, `/examples/*`, `/source/*` |
| `protocol.py`, `session.py` | message validation; per-socket role/mode/liveness; telemetry "latest-only", acks/events reliable |
| `controller.py` | arbitration, command execution, server-side timed moves (cancel on supersede/stop), dead reckoning, watchdog, telemetry |
| `safety.py` | pure functions: `ObstacleGuard.evaluate`, `speed_to_duty`, steering clamp, watchdog policy |
| `config.py` | defaults + `~/.config/car-scratch/calibration.json` (migrates legacy `~/picar/calibration.json` `steer_trim_deg`) |
| `projects.py` | "Save to car" storage (`~/.local/share/car-scratch/projects/`, name regex, zip+`project.json` check, atomic write, quota) |
| `safe_stop.py` | standalone, zeroes PCA9685 ch4/ch5, always exits 0 (systemd pre/post hook) |
| `hal/{base,pca9685,sensors,real,dryrun}.py`, `hal/sim/{backend,world}.py`, `hal/sim/worlds/*.json` | backends behind one interface: `start(on_sample)`, `set_motors(left,right)` (signed duty), `set_steering(angle −45..45)`, `stop_motors()` (never raises), `capabilities` |

HAL details: one I2C lock for PCA9685/PCF8591/line follower; ultrasonic keeps current logic with a
12 ms echo timeout while driving forward (far = >2 m), median-of-3 near obstacles, 3× None = fault;
line follower re-probed with backoff and reported `ok:false` while absent; light reported raw +
normalised brightness (direction of "brighter" confirmed on the car).

Speed: user 0–100 → duty via calibrated floor (motor deadband) and ceiling. **Default speed limit 100**
(owner removed the cap on the test program); instead the obstacle guard uses a **speed-dependent stop
margin** (`stop_cm + coast(speed)`, coast measured in M7). Reverse capped at 40% and auto-stops after
5 s (no rear sensor). Timed moves capped at 30 s. `--hardware auto` falls back to "car unavailable"
(editor + simulator still work).

## 3. WebSocket protocol (`/ws`, JSON `{"t":…}`, same origin; 403 if `Origin` ≠ `Host`)

- **Client → server:** `hello{v, client_id, want:control|view, mode:car|sim, world?}` · `hb` (5 Hz) ·
  `cmd{id, op}` with ops `drive{dir,speed}`, `move{dir,speed,ms}`, `stop`, `steer{angle}`,
  `set_speed`, `config{obstacle_stop,stop_cm,speed_limit}`, `estop`, `estop_reset`, `resume`,
  `reset_pose`, `world{id}`, `calibrate{trim…}` · `control{take|release, force?}` · `mode{car|sim}`.
- **Server → client:** `welcome{…limits, config, hb_ms, wd_ms, tel_hz, server{version,sha,source_url}}` ·
  `tel` 20 Hz (~450 B: drive/duty, steer, dist{cm,state}, obstacle, line, light, pose{x,y,dir}, safety, ctl, move) ·
  `ack` · `done{id, result: completed|stopped|superseded|obstacle|estop|hold|watchdog|disconnected}` ·
  `err{code}` · `event{obstacle_stop|estop|watchdog_stop|hold|resumed|control_changed|sensor_fault|…}` · `role` · `world`.
- **Arbitration:** first `want:control` controls, others are viewers (viewers may only `estop`);
  `take` with `force` demotes (car stops first); 30 s control lease for a reconnecting client
  (iPad tab restore). Simulator sessions are private per client.
- Full spec in `docs/protocol.md`; version mismatch closes with 4409 ("reload").

## 4. Safety (layered, server-authoritative)

1. **Obstacle guard in the sensor thread:** moving forward and `dist < stop_cm + coast(speed)`, or 3 sensor
   faults → `stop_motors()` immediately, then `event` + `done{obstacle}`; forward commands rejected while
   blocked. On by default; "off" allowed from blocks but shows a permanent banner. Sim uses the same function.
2. **Dead-man watchdog:** controller silent > 750 ms while moving → stop and **latch hold**.
3. **Hold** rejects motion until Resume button / green flag / stop sign (a backgrounded iPad tab never
   silently resumes a forever loop).
4. Controller WebSocket closes → stop. **Stop sign** (`PROJECT_STOP_ALL`) → `stop` (also on project load).
5. **E-stop** latched; anyone (incl. viewers) triggers on `pointerdown`; only the controller resets, UI only.
6. Process level: liveness thread; systemd `WatchdogSec=3` (sd_notify every 0.5 s); `ExecStartPre=-` and
   `ExecStopPost=` run `safe_stop`; motors zeroed first in HAL init; no sensor sample > 500 ms while moving → stop.
7. Browser: heartbeats via `setInterval` (stop when the tab is hidden → watchdog); `visibilitychange`/`pagehide` send `stop`.
8. I2C is the only stop path: on motor-write error retry, then latch `hw_fault` + e-stop banner
   ("lift the car / switch off"); the battery switch is the last resort.

## 5. Editor changes (paths verified at `105e22ea`)

**scratch-vm** (`editor/packages/scratch-vm/`)
- `src/extension-support/extension-manager.js`: add `picar: () => require('../extensions/scratch3_picar')` to `builtinExtensions`.
- New `src/extensions/scratch3_picar/{index.js, car-link.js, protocol.js}`: extension id `picar` ("Pi Car"),
  `showStatusButton`, `registerPeripheralExtension` with `scan()` listing **Pi Car (this car)** and
  **Simulator** (never emit `PERIPHERAL_REQUEST_ERROR` during scan — it shows the Scratch Link screen).
- New `test/unit/extension_picar.js` (Tap, same style as `extension_microbit.js`, fake WebSocket).

**Blocks** (commands are fire-and-forget over one ordered socket; timed blocks return a Promise
that always resolves on `done`/disconnect/timeout; reporters read cached telemetry):

| Group | Blocks |
|---|---|
| Motion | drive [forward/backward] at (50)% · drive [dir] at (50)% for (1) s · stop motors · set speed to (N)% · steer [left/straight/right] · set steering angle to (0)° |
| Distance | (distance cm) (999 = far, 0 = fault/disconnected) · ⟨obstacle closer than (20) cm?⟩ · **when obstacle closer than (20) cm** (edge hat) |
| Line | (line sensor [1–5] value) · ⟨line sensor [n] sees line?⟩ · (line position −2..2) · when line sensor [n] sees line |
| Light | (light [left/middle/right] brightness) · (brightest direction) |
| Safety | turn obstacle stop [on/off] · set obstacle stop distance (20) · set speed limit (N)% · emergency stop |
| Status | ⟨car connected?⟩ · (car mode) · (car x / y / direction) · reset position · when car [stops for obstacle / is e-stopped / loses connection] |
| Later (M8) | drive (20) cm · turn [left] (90)° (needs calibration) |

**CarLink:** auto-connect (`ws(s)://location.host/ws`, `?picar_ws=` override for dev), mode from
localStorage; reconnect with backoff 0.5→5 s and immediately on `visibilitychange:visible`/`pageshow`/`online`;
no command replay after reconnect; connection-lost alert only after 5 s; `getState()`/`subscribe()` for
React, `'telemetry'` event for the canvas; `window.__picar` only with `?picar_debug=1` (e2e).

**scratch-gui** (`editor/packages/scratch-gui/`)

| File | Change |
|---|---|
| `src/lib/libraries/extensions/index.jsx` (+ `picar/` images) | first, featured "Pi Car" entry, no Bluetooth/Internet flags |
| `src/lib/vm-manager-hoc.jsx` | auto-load `picar` once on VM init |
| `src/components/gui/gui.jsx` + new `src/components/stage-pane-tabs/*` | bottom-right pane becomes tabs **Sprites \| Pi Car** (stacked only on tall desktop screens) |
| new `src/components/car-monitor/*`, `src/containers/car-monitor.jsx`, `src/lib/picar/use-car-link.js` | the animation panel (§6) |
| `src/components/menu-bar/menu-bar.jsx` + new `examples-menu.jsx` | Pi Car logo, **Examples** menu (modelled on `file-menu.jsx`), pinned **E-STOP** |
| `src/components/menu-bar/file-menu.jsx`, new `src/lib/picar/{projects-api,load-project-buffer}.js`, `src/components/car-projects-modal/*`, `src/reducers/modals.js` | **Open from car… / Save to car…** (load flow mirrors `sb-file-uploader-hoc.jsx`) |
| `src/components/stage-wrapper/stage-wrapper.jsx` | floating E-STOP + status chip in full-screen stage |
| `src/playground/render-gui.jsx`, `src/playground/index.ejs`, `webpack.config.js`, `src/lib/titled-hoc.jsx` | title "Car Scratch", About / Source code (AGPL) / Safety help menu, iPad meta + manifest + viewport-fit script, dev-server proxy for `/ws` `/api` `/examples` `/source` |
| `src/lib/default-project/*`, `static/favicon.ico`, `static/pi-car/*` | car sprite instead of Scratch Cat, icons, `manifest.webmanifest` |

## 6. Car Monitor (animation)

2D canvas with `requestAnimationFrame`, interpolating the 20 Hz pose: top-down car (front wheels
turn with steering; rear-wheel treads scroll with speed — green forward / orange reverse / grey stopped),
**trail** of the dead-reckoned path, **ultrasonic cone** (length = distance; green / amber / red below the
stop line, faded when far, hatched on fault), **5 line-sensor dots**, **3 light glows** + arrow to the
brightest, HUD (speed, direction, steering, distance), banners (OBSTACLE STOP, E-STOP + Reset, PAUSED +
Resume, VIEW ONLY + Take control, line sensor offline, reconnecting). Header: Car | Sim switch, world
picker, E-STOP. In sim mode it draws the world (walls, line track, lamps) exactly as the physics uses it.

## 7. Simulator (server-side, one implementation for UI, pytest and e2e)

Per-session `Controller` + `SimBackend` at 50 Hz: bicycle model with speed lag and coast; wall
collisions stall the car; ultrasonic as 5 rays over ±15° with noise and "far" above 40° incidence
(mimics the real oblique-wall miss); line and light sensors on the same raw scales as the hardware.
Worlds: `arena`, `corridor`, `oval` (line track), `lamp`, `maze`. `--hardware sim` makes the shared car a
sim too (workstation dev + e2e of arbitration/watchdog).

## 8. iPad (Safari / Chrome on iPadOS ≥ 17.4, both WebKit)

- **Landscape first.** Tabbed bottom pane whenever height < 950 px or `pointer: coarse`; E-STOP always
  visible (menu bar + full-screen stage). Inline viewport-fit script: below 1024 px real width
  (smaller iPads, Split View) switch the viewport to `width=1024` so Safari scales to fit (**spike on a
  real iPad in M4**); portrait shows a "rotate" hint.
- **Touch:** our controls ≥ 48 CSS px, pointer events only, no hover-only UI, `touch-action` set;
  block dragging uses Scratch's existing touch support.
- **No keyboard:** on-screen **Touch remote** example; touch buttons in other examples.
- **Saving:** Save to car / Open from car, optional per-device draft autosave (home-screen mode has no
  `beforeunload`, iPadOS evicts tabs).
- **Backgrounding / screen lock:** JS suspends → watchdog stops the car + hold; on return CarLink
  reconnects and the user taps **Resume** (documented in `docs/ipad.md`).
- Audio unlock on first tap (+ `navigator.audioSession` when available); `picar.local` resolves natively
  (IP fallback documented); **Add to Home Screen** manifest + apple meta → full-screen app with icon.
  Not a secure context: no service worker, no camera-based extensions.

## 9. Example projects (Examples menu; authored in the simulator, stored unpacked, packed at deploy)

| # | Example | Demonstrates |
|---|---|---|
| 01 | Hello car | timed moves, steering wiggle |
| 02 | **Touch remote** (iPad-first) | sprite buttons ▲ ▼ ◀ ▶ ■, speed slider, distance readout; arrow keys on desktop |
| 03 | Distance meter | stationary sensor readout, bar + pen graph |
| 04 | Obstacle stop | port of `obstacle_stop.py` |
| 05 | Obstacle avoider | obstacle hat → back up while steering → continue |
| 06 | Parking sensor | beep rate follows distance |
| 07 | Line follower | proportional steering from line position (`oval` world; car after cable fix) |
| 08 | Light follower | steer to the brightest side, stop when dark |
| 09 | Figure eight + trail | car x/y/direction reporters draw on stage |
| 10 | Safety lab | speed limit, stop distance, obstacle event hat, e-stop block |

## 10. Build, deploy, run

- **`docker/editor.Dockerfile`** (`node:24.22.3-trixie`, `HUSKY=0`, `npm ci` with cache mount, root
  `npm run build`; output `packages/scratch-gui/build/`; strip `*.map`, precompress gzip/brotli; `www`
  stage exported with `buildx --output type=local,dest=out/www`). Needs internet (micro:bit hex prepare step).
- **`compose.yaml`:** `server-test` (debian:trixie-slim + the same apt packages as the Pi), `editor-test`
  (Tap + Jest), `sim-server` (`--hardware sim`, serves `out/www`, :8080), `editor-dev` (`webpack serve`
  proxied to sim-server), `e2e` (`mcr.microsoft.com/playwright:v1.65.1-noble`), `deploy`.
- **`deploy/deploy.sh`:** refuse a dirty tree; build source tarball of the exact commit (AGPL §13 offer
  served at `/source/`); rsync www + server + examples to `c4@192.168.0.209:~/car-scratch/releases/<sha>/`;
  switch `current` symlink; `systemctl --user restart car-scratch`; health-check `/api/status` reports the
  sha, else roll back; keep 3 releases.
- **systemd user unit** (no sudo per deploy): `Type=notify`, `WatchdogSec=3`, `Restart=always`,
  `ExecStartPre=-`/`ExecStopPost=` `safe_stop`, `ExecStart=python3 -m picar_server --port 8080 --hardware auto …`.
- **`deploy/pi-setup.sh`** (idempotent, owner runs once with sudo): `apt install python3-aiohttp python3-smbus2`,
  `loginctl enable-linger c4`, WiFi power-save off (Pi 3 latency spikes would trip the watchdog).
  Address: rely on `picar.local`; a DHCP reservation is a separate owner decision (IPAM registry
  `docs/network-plan.md` in ailab has no free statics).
- The existing `~/picar/` test tools stay as they are until M2 replaces them with HAL-based versions.

## 11. Milestones

| # | Scope | Exit criteria |
|---|---|---|
| M0 | Repo, subtree import, LICENSE/NOTICE, compose, unmodified editor build served by a stub server; open on desktop **and iPad** (layout baseline) | `out/www` loads from the container |
| M1 | Server core: protocol, controller, safety, sim backend, telemetry, static, projects REST | pytest green (~15 s) |
| M2 | Real HAL (refactor of `obstacle_stop.py`), `safe_stop`, dryrun, `wsclient.py`, deploy, unit, pi-setup | today: unit tests with fake smbus/GPIO; **tomorrow:** on-car Stage 0 + Stage 1 with the CLI client |
| M3 | VM extension + CarLink, library entry, auto-load, Tap tests | blocks drive the sim from a browser |
| M4 | Car Monitor, Sprites \| Pi Car tabs, E-STOP, branding, manifest/meta, viewport-fit spike | monitor animates live; iPad profile passes |
| M5 | Per-session simulator + worlds, Car/Sim switch | everything works without the car |
| M6 | Examples, Examples menu, Save/Open on car, full e2e incl. WebKit iPad | e2e green in compose |
| M7 | Floor stages + calibration (speed table, coast per speed, wheelbase/steering ratio, trim UI), real-iPad pass | checklist signed off |
| M8 (opt.) | `drive cm` / `turn °`, line calibration, cliff detection via line IR, kernel-timestamped ultrasonic | — |

**Today (headless, ≤ 10 h): M0, M1, M2 (code + fakes only), M3–M6, all in Docker Compose.**
**Tomorrow, together with the owner: deploy + on-car Stages 0–5 + M7 calibration.** No deploy and no
motor command to the car today.

## 11a. Execution plan (first day, about 10 hours)

**Working rules:** every important decision gets two independent reviews; the better solution is taken
and recorded with its reasons in `docs/decisions.md`. All builds and tests run in `docker compose`. Each
slice is reviewed and committed on its own (Conventional Commits). Gitea repo `cchifor/car-scratch`.

| Time | Work |
|---|---|
| 0:00–0:45 | Repo + `.gitattributes`/LICENSE/NOTICE; import scratch-editor@105e22ea into `editor/` (shallow fetch + `git subtree add --squash`, fallback: tarball snapshot commit); compose skeleton; **start the unmodified editor Docker build** (long pole, background) |
| 0:45–1:30 | **Contracts:** `docs/protocol.md` (§3), block list + CarLink API (§5), HAL interface (§2), safety rules (§4), world JSON schema. **Decision gate 1:** independent reviews → merge best |
| 1:30–6:00 | Parallel tracks on disjoint paths: **A** server core + sim + safety + projects REST + pytest · **B** HAL real/dryrun/safe_stop + fake smbus/GPIO tests + `wsclient.py` + deploy.sh/unit/pi-setup.sh · **C** scratch-vm extension + CarLink + Tap tests · **D** scratch-gui: tabs, Car Monitor, E-STOP, menus, Save/Open on car, branding, iPad meta/viewport (starts on mocks of C's API) · **E** docs + icons/manifest + kids' safety help |
| 4:00 | **Decision gate 2** (iPad layout approach + Monitor UX, after first screenshots from Playwright WebKit) |
| 6:00–7:30 | Integration: editor build with changes, `sim-server` + browser in compose; examples generated by `examples/tools/build_examples.py` (Python DSL → project.json → sb3) and run in the sim |
| 7:30–9:00 | e2e Playwright (chromium + WebKit iPad profiles), fix loop |
| 9:00–9:45 | **Final review:** independent reviews of the full diff (safety + protocol focus); fix blockers |
| 9:45–10:00 | Push, short report for tomorrow: what's done, test results, deploy steps, on-car checklist |

**If time runs short, cut in this order:** draft autosave, `maze` world, examples 09–10, stacked desktop
layout, brotli. **Never cut:** server-side safety, watchdog/hold, e-stop, stop sign, simulator,
iPad touch layout, e2e for safety paths.

## 12. Verification

1. **`docker compose run server-test`** (fast, first): PCA9685 prescale/ticks (90° → 369) incl. trim; speed
   mapping; ObstacleGuard table (forward/back, far, fault×3, off, speed-dependent margin); watchdog trip →
   hold → resume; arbitration (viewer rejected, viewer e-stop, force take, lease, disconnect stop);
   timed move completes/superseded/obstacle in `corridor`; e-stop latch; malformed/oversize/rate limit;
   Origin rejection; telemetry rate; static `.gz` + path traversal; projects API; calibration migration;
   sim raycast/line/light.
2. **`docker compose run editor-test`:** Tap extension tests (getInfo, message shapes, promise resolution,
   disconnected defaults, edge hats, stop-sign → `stop`, heartbeat, reconnect/visibility, no replay);
   Jest for renderer thresholds, banners, Examples menu, projects modal, menu-bar.
3. **Editor build**, then **e2e** against `sim-server`: chromium 1280×800 and WebKit iPad Pro 11 /
   gen 7 / Mini / gen 11 (944 px viewport-fit case). Title, Pi Car category, block drives pose (via
   `__picar` state), example 04 stops before the wall, stop sign stops < 200 ms, viewer VIEW ONLY + viewer
   E-STOP, frozen heartbeat → stop + hold < 1 s, server restart → reconnect < 5 s, all examples load,
   Save/Open round-trip; iPad: no horizontal overflow, touch targets ≥ 44 px, touch-remote tap moves the sim car.
4. **On the car** (`docs/on-car-checklist.md`, owner present): Stage 0 dryrun (sensors + monitor react,
   line shows offline) → Stage 1 wheels up (directions match animation, e-stop, stop sign, tab close /
   airplane mode / screen lock stop < 1 s, `kill -9` → ExecStopPost stop, SIGSTOP → watchdog restart) →
   Stage 2 floor (stop distance at 30/50/80/100%, speed table, turning radius, oblique wall) →
   Stage 3 examples 02/04/05/08 → Stage 4 real iPad (Safari + Chrome, browser + home-screen: drag
   blocks, touch remote, E-STOP, lock-screen stop + Resume, reconnect, Save/Open, audio, rotate hint)
   → Stage 5 controller + viewer on two devices.

## 13. Risks

- **AGPL-3.0 §13:** serving the modified editor requires offering source to its users → `/source/` tarball
  of the deployed commit, linked from About; keep upstream notices; server licensed AGPL too.
- **Trademark:** name set by the owner to **Car Scratch**. Upstream `TRADEMARK` also covers the Scratch
  logo, Cat and Gobo: replace logo, favicon and default Cat sprite, and add "not affiliated with or
  endorsed by the Scratch Foundation" to About.
- **I2C is the only stop path** (one direction pin per motor): hw_fault latch + battery switch as last
  resort; the non-responding line-follower cable should be fixed or unplugged so it can't disturb the bus.
- **Pi 3 load:** ~5–7 MB gzipped bundle (3–10 s first load), ultrasonic busy-wait ~40% of a core;
  precompression, adaptive pinging; M8 kernel edge timestamps.
- **Block latency** ~100–150 ms from Scratch loops; safety does not depend on it; line following at 30–40%.
- **Oblique walls read "far"** → speed-dependent margin, documented, reproduced in the simulator.
- **iPad:** Playwright WebKit on Linux ≠ iPad Safari (real-device Stage 4 is the gate); viewport-fit needs
  device validation; single pointer only.
- **LAN security:** anyone on the LAN can drive → Origin checks, optional `--control-pin`, guest WiFi isolation.
- **Upstream drift:** small insertion points; rerun Tap/Jest/e2e after each subtree pull.
