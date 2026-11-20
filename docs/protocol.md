# Car Scratch wire protocol (v1)

The car server (`server/picar_server`) and the editor talk over **one WebSocket** (`/ws`) plus a few
REST endpoints, all on the same origin as the editor (`http://picar.local:8080`).

All safety decisions are made **on the server**. The browser can ask for motion; it never has to
remember to stop.

## 1. Transport

- URL: `(location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host + '/ws'`.
  Dev override: query parameter `?picar_ws=ws://host:port/ws`.
- Handshake: the server rejects with HTTP 403 when the `Origin` header is present and its host:port
  differs from `Host` (stops other web pages from driving the car). Missing `Origin` (CLI tools) is allowed.
- Frames: UTF-8 JSON text, one object per frame, field `t` = message type. Client frames are at most
  4096 bytes (server closes with 1009 otherwise). Server ping/pong every 5 s (aiohttp `heartbeat=5`).
- Rate limit: 100 client frames/s per connection, burst 50. Excess frames get `err{code:"rate_limited"}`
  and are dropped (a `stop` or `estop` is never dropped).
- Protocol version `v = 1`. A client whose major version differs is closed with code **4409**
  ("protocol mismatch, reload the page").

## 2. Session lifecycle

1. Client connects and sends `hello` as its **first** frame (else closed with 4400).
2. Server answers `welcome` (+ `world` if the session is in sim mode), then streams `tel` at `tel_hz`.
3. Client sends `hb` every `hb_ms` (any client frame also counts as liveness).
4. Either side may close. The server closes idle sockets that sent nothing for 10 s with 4408.

### Modes

- `car`: the session is attached to the **shared car controller** (one physical car; arbitration below).
- `sim`: the session gets its **own private simulator controller** and is always its controller.
  A session can switch with `mode`. Switching away from `car` stops the car if this session was its
  controller and releases control.

### Car arbitration (mode `car` only)

- Roles: `controller` (at most one) or `viewer`.
- `hello{want:"control"}` gets control if it is free, or if this `client_id` holds the **lease**
  (see below); otherwise the session becomes a viewer.
- Viewers may send only `hb`, `estop`, `control`, `mode`. Any other `cmd` → `err{code:"not_controller"}`.
- `control{action:"take"}` succeeds if control is free; `control{action:"take", force:true}` always
  succeeds: the car is **stopped first**, the old controller becomes a viewer (`role` message).
- `control{action:"release"}` stops the car and frees control.
- When the controller's socket closes, the car stops immediately and the `client_id` keeps a
  **30 s lease**: only the same `client_id` (e.g. an iPad tab that came back) gets control without `force`.

## 3. Client → server

| `t` | Fields | Meaning |
|---|---|---|
| `hello` | `v:1`, `client_id:str(8..64)`, `want:"control"\|"view"`, `mode:"car"\|"sim"`, `world?:str` | first frame |
| `hb` | – | liveness |
| `cmd` | `id:int`, `op:str`, op fields | see table below; `id` is unique per connection, increasing |
| `control` | `action:"take"\|"release"`, `force?:bool` | car arbitration |
| `mode` | `mode:"car"\|"sim"`, `world?:str` | switch mode (sim world id optional) |

### Commands (`cmd.op`)

| op | Fields | Reply | Notes |
|---|---|---|---|
| `drive` | `dir:"forward"\|"backward"`, `speed:0..100` | `ack` | keeps driving until another motion command, `stop`, a safety stop, or the reverse timeout |
| `move` | `dir`, `speed`, `ms:1..30000` | `ack`, later `done` | timed move executed **on the server** |
| `stop` | – | `ack` | stops motors; cancels a timed move (`done{result:"stopped"}`) |
| `set_speed` | `speed:0..100` | `ack` | changes the speed of the current drive; no effect when stopped |
| `steer` | `angle:-45..45` | `ack` | degrees from straight, negative = left |
| `config` | any of `obstacle_stop:bool`, `stop_cm:10..100`, `speed_limit:1..100` | `ack` | per controller; car config resets to defaults when control changes hands |
| `estop` | – | `ack` | latched emergency stop (viewers allowed) |
| `estop_reset` | – | `ack` | controller only |
| `resume` | – | `ack` | clears a watchdog **hold** (sent by the Resume button, the green flag, and the stop sign) |
| `reset_pose` | – | `ack` | dead-reckoned pose back to `(0, 0, 90)`; in sim: back to the world start |
| `world` | `id:str` | `ack` + `world` | sim mode only |
| `calibrate` | `steer_trim_deg?:float(-20..20)`, `save?:bool` | `ack` | car controller only; `save` persists to calibration.json |

Any new motion command (`drive`, `move`, `stop`) **supersedes** a running timed move
(`done{result:"superseded"}` for `drive`/`move`, `"stopped"` for `stop`).

Motion is refused with `err` while: e-stopped (`estop`), on hold (`hold`), forward-blocked by an obstacle
(`obstacle_blocked`), not the controller (`not_controller`), or the car is unavailable (`hw_unavailable`).
`steer`, `config`, `stop` are always allowed for the controller.

## 4. Server → client

| `t` | Fields |
|---|---|
| `welcome` | `v`, `session:str`, `role`, `mode`, `car_available:bool`, `hw:{backend:"real"\|"dryrun"\|"sim"\|"none", error?:str, capabilities:{ultrasonic,light,line}}`, `limits:{speed_max:100, steer_max:45, move_ms_max:30000, reverse_speed_max:40, reverse_ms_max:5000, stop_cm_min:10, stop_cm_max:100}`, `config:{obstacle_stop, stop_cm, speed_limit}`, `hb_ms:200`, `wd_ms:750`, `tel_hz:20`, `worlds:[{id,title}]`, `server:{version, sha, source_url}` |
| `tel` | telemetry, see §5 |
| `ack` | `id`, `ok:true` |
| `done` | `id`, `result:"completed"\|"stopped"\|"superseded"\|"obstacle"\|"estop"\|"hold"\|"watchdog"\|"disconnected"\|"reverse_timeout"` |
| `err` | `id?`, `code`, `msg` — codes: `not_controller`, `estop`, `hold`, `obstacle_blocked`, `bad_request`, `rate_limited`, `hw_unavailable`, `hw_fault`, `sim_only` |
| `event` | `kind`, extra fields — kinds: `obstacle_stop{cm}`, `estop{by}`, `estop_reset`, `watchdog_stop`, `hold`, `resumed`, `reverse_timeout`, `control_changed{controller_present}`, `sensor_fault{sensor}`, `sensor_ok{sensor}`, `hw_fault{msg}` |
| `role` | `role:"controller"\|"viewer"`, `controller_present:bool` |
| `world` | sim world description, see `docs/contracts.md` §4 |

## 5. Telemetry (`tel`, 20 Hz, latest-only — a slow client skips frames, never queues them)

```json
{"t":"tel","seq":812,"ts":40.62,"mode":"car",
 "drive":{"dir":"forward","speed":40,"duty":[1720,1720],"moving":true},
 "steer":-12,
 "dist":{"cm":42.3,"state":"ok"},
 "obstacle":false,
 "line":{"ok":false,"raw":null,"on":null,"pos":null},
 "light":{"ok":true,"raw":[118,96,131],"bright":[41,63,30],"max":"middle"},
 "pose":{"x":12.4,"y":31.0,"dir":85.5,"src":"dr"},
 "safety":{"estop":false,"hold":false,"obstacle_stop":true,"stop_cm":20,"margin_cm":24.5,"speed_limit":100,"blocked":false,"hw_fault":null},
 "ctl":{"role":"controller","viewers":1},
 "move":{"id":17,"remaining_ms":640}}
```

- `drive.dir`: `"forward"|"backward"|null` (null when stopped). `duty`: `[left, right]` PCA9685 values.
- `dist.state`: `ok` | `far` (nothing in range; `cm` is null) | `fault` (no echo 3× in a row; `cm` null).
- `obstacle`: true when `dist.cm < safety.margin_cm`.
- `line.on`: 5 booleans, sensor 1 = leftmost; `line.pos`: −2 (left) … +2 (right) or null when no sensor sees a line.
- `light.bright`: 0..100, higher = brighter; `light.max`: `left|middle|right|none`.
- `pose`: x/y in cm, `dir` in **Scratch degrees** (90 = the start heading, clockwise positive), so a stage
  sprite can mirror it directly. `src`: `dr` (dead reckoning on the real car) or `sim`.
- `move`: the running timed move or null.

## 6. REST

| Method + path | Body / result |
|---|---|
| `GET /api/status` | `{version, sha, source_url, car:{available, backend, error?, capabilities}, clients:{car_controller:bool, car_viewers:int, sim_sessions:int}}` |
| `GET /api/projects` | `[{name, size, mtime}]` |
| `GET /api/projects/{name}.sb3` | the file |
| `PUT /api/projects/{name}.sb3` | raw sb3 bytes (≤ 25 MB, must be a zip with `project.json`); atomic write, previous version kept as `.bak` |
| `DELETE /api/projects/{name}.sb3` | – |
| `GET /examples/index.json`, `GET /examples/{id}.sb3` | bundled examples |
| `GET /source/` | AGPL source tarball(s) of the deployed commit |

`name` matches `^[A-Za-z0-9 _-]{1,64}$`. Unsafe methods (`PUT`, `DELETE`) require `Origin` to match
`Host` when present. Total project storage quota 500 MB (`413` when exceeded).
