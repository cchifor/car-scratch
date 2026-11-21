# Decision log

Important decisions were cross-reviewed by two independent reviewers (**A** and **B**); the best
answer was taken. Review texts: `out/reviews/` (not committed).

## D1 — Contracts gate 1 (2026-11-20)

Both reviewers independently flagged the same blockers; all were accepted into contracts v1.1/v1.2:

| Topic | Decision | Source |
|---|---|---|
| Stop vs. re-energise race | One motor path + backend motor lock + `inhibit_forward()` latch set in the sensor thread; motion generation numbers for timers | both |
| Background / disconnect | Hold latched on watchdog silence (motors running or not), on `pause` (tab hidden) and on controller disconnect; server starts on hold; only Resume button / green flag clear it | both |
| Stop sign | `PROJECT_STOP_ALL` → `stop` only and **disarms** car hats; `PROJECT_START` → `resume` + arm | both |
| Edge hats fire without green flag | `armed` gate in the extension | reviewer B (verified `runtime.js` `_step`) |
| `whenCarEvent` metadata | `BlockType.HAT`, `isEdgeActivated:false`, `shouldRestartExistingThreads:false` (reviewer A suggested EVENT; HAT is the proven upstream pattern, e.g. makeymakey) | reviewer B |
| Liveness | Only accepted, parsed JSON frames; never ping/pong | both |
| Obstacle check | Also at command time at the *proposed* speed; `blocked` clears after 1 s of clear samples | both |
| Sensor validity | `none` state; fault/freshness/startup protection independent of the obstacle-distance switch; distinct `sensor_fault` event | reviewer A (reviewer B proposed disabling both — rejected for safety) |
| Stuck echo | `far` only if the echo ends between 12 and 40 ms | reviewer A |
| `ack`/`err`/`done` | Exactly one `ack` or `err` per cmd; `done` only for `move`; `move()` resolves on `err` | both |
| E-stop reach | Always latches the shared car (any mode, viewers too) + `POST /api/estop` + queued estop on reconnect; extension calls `runtime.stopAll()` on e-stop | both / reviewer B |
| Reverse limit | Cumulative per episode, not reset by repeated commands | both |
| Command flood | CarLink de-dupe + 20 Hz per-op coalescing; back-pressure on `bufferedAmount` | both |
| Tab identity | `client_id` in sessionStorage; duplicate live id → viewer | both |
| Mode switch | Server answers with a full `welcome`; CarLink `switching` state drops motion | reviewer A |
| Peripheral flow | Dropped (no status button / modal / scan); registration only as a lookup | reviewer B (reviewer A: scan flow is fragile) |
| Host/Origin | Host allow-list (DNS rebinding) + `--allow-origin` for dev | reviewer B |
| Pi hang / reboot | `pi-setup.sh` enables the hardware watchdog and an early-boot system `safe_stop` unit | reviewer B (reviewer A: needs an independent cutoff) |
| GIL jitter in ultrasonic | `sys.setswitchinterval(0.0005)`, discard pings with > 1 ms loop gaps | reviewer B |
| Line follower bus risk | Never probed while motors run; `line_enabled` defaults false (the first part is superseded by D4) | reviewer B |
| Speed cap until calibrated | **Rejected** (owner removed the speed cap); UI shows "not calibrated" banner instead | reviewer B proposed, owner decision kept |
| Touch remote | Short timed nudges per tap (no open-ended drive from a tap) | reviewer A |
| iPad viewport forcing | Dropped (shrinks touch targets); responsive layout only | both |
| Geometry | `welcome.geometry` is authoritative for drawing | reviewer A |
| Scope | Examples: 6 first (01, 02, 04, 05, 07, 08), others if time; worlds arena/corridor first | reviewer A |

## D2 — UI layout gate 2 (2026-11-20, from desktop + iPad screenshots)

Both reviewers agreed: one E-STOP in the menu bar (≥ 44 px) plus the floating one in full screen (the
monitor's duplicate removed); a single-row monitor header; banners out of the canvas; Examples label never
collapses; Tutorials/Debug removed (online upstream content, not car-related); Pi Car category icon; a
car-specific default project title. HUD as one bottom strip (reviewer B). Touch targets ≥ 44 px for the
stage controls too (reviewer A).

Disagreement — default tab: reviewer B proposed Pi Car on every device; reviewer A proposed Pi Car only on touch
devices. **Taken: reviewer A** — on desktop the Sprites list is how you choose whose code you edit, and several
examples have multiple sprites; a stored choice always wins.

## D3 — Final review (2026-11-20/21)

| Topic | Decision | Source |
|---|---|---|
| Hold on control acquisition | **Every** acquisition of car control (hello, take, mode→car, returning lease) starts on hold; only Resume / green flag clear it. Supersedes D1's "hold cleared for a new controller". The extension also stops the project on viewer→controller, mode→car and reconnect | reviewer B (B1), consistent with reviewer A's earlier "atomic stop-and-hold on transitions" |
| Reverse-move race | Deferred guard results bound to the move object, not a generation number | both |
| Zero speed / de-dupe | `set_speed 0` / `drive 0` sent as `stop`; drive and set_speed invalidate each other's de-dupe | both |
| E-STOP stops the project locally | CarLink emits a local estop event → `runtime.stopAll()` without waiting for the server | reviewer B |
| Bypass flood | Identical bypass ops de-duplicated within 50 ms; REST e-stop throttled | reviewer B |
| Version stamping | `BUILD_SHA` stamped into the editor build; deploy refuses a mismatched `out/www`; browser check ignores `dev` | both |
| Rotate overlay / hw_fault | E-STOP inside the rotate overlay; persistent hardware-fault banner with Reset | reviewer A |
| Trademarked characters | Scratch characters filtered out of the sprite/costume libraries; time-travel logos removed; own favicon | both |
| Product name | "Car Scratch" kept (owner decision); both reviewers note the Scratch TRADEMARK file restricts using the name to promote derivatives — owner to revisit before any public release | owner |

## D4 — Line follower read while moving (2026-11-21)

**Context.** D1 said the line follower is never read while the motors run. That was decided when the module did
not answer at all, and a slave that hangs the bus could hold the shared I2C lock and delay a motor stop. A line
follower that is only read at standstill cannot follow a line, which is what it is for.

**Measured on the car (2026-11-21).**

- A read (`read_i2c_block_data(0x11, 0, 10)`) takes about 2.9 ms; the maximum over 40 reads was 3.0 ms. No bus
  error was seen. About 1 % of frames come back as all 0xFF (65535 in every slot): those are invalid.
- Polarity: white or reflective close to the sensor reads LOW (about 170 to 300); black, or nothing in range,
  reads HIGH (about 420 to 425). A black line therefore reads HIGH, the opposite of what the first code assumed.
- A non-answering device is NACKed at once; the risk is a slave that stalls, not one that is missing.

**Decision.** The line follower is read while the car moves, only when all of these hold:

1. `line_enabled` is true and the module is present.
2. It has answered well: 10 good reads in a row since its last failure (a failure is an I2C error, or a frame
   that was invalid after the immediate retry).
3. The read takes only the I2C bus lock (like the light read), never the motor lock, so a motor write or stop is
   never queued behind it for longer than one bus transaction.

Moving reads are switched off, with one WARNING in the log, until the car has been stopped when a moving read
raises an error, returns an invalid frame twice in a row, or takes more than 15 ms (measured with a monotonic
clock around the bus call). Telemetry then shows `line.ok: false` (telemetry repeats the last frame for 0.1 s, and only across a single skipped frame) and
the controller reports a line `sensor_fault` event after 3 samples without a frame. After the stop the module
must answer 10 times in a row again before moving reads resume. The stopped behaviour is unchanged (probe with
a 2 s to 10 s back-off while absent).

An invalid frame is retried once at once, except after a bus call over 15 ms (a stalling module holds the bus lock
once, then moving reads stop). At standstill a slow read does not count as a good read. A frame still invalid is a skipped sample and does not mark the module
absent. Three skipped samples in a row, or an I2C error, do.

**Residual risk (measured worst case).** A healthy line read adds about 3 ms, and up to about 10 ms with a retry
plus the light read, before the obstacle guard and before a loop-thread stop gets the bus lock. A stalled bus
blocks any motor write; the PCF8591 light read has always shared this. The wait is bounded by the I2C adapter
timeout and by the existing layers behind it (the `hw_fault` latch when the motor lock times out, and the
systemd and hardware watchdogs). A slow call is never retried and switches moving reads off, so the line
follower adds at most one stalled call before it is off for that drive. The owner can turn the feature off with
`line_enabled: false`.
