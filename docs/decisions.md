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
| Line follower bus risk | Never probed while motors run; `line_enabled` defaults false | reviewer B |
| Speed cap until calibrated | **Rejected** (owner removed the speed cap); UI shows "not calibrated" banner instead | reviewer B proposed, owner decision kept |
| Touch remote | Short timed nudges per tap (no open-ended drive from a tap) | reviewer A |
| iPad viewport forcing | Dropped (shrinks touch targets); responsive layout only | both |
| Geometry | `welcome.geometry` is authoritative for drawing | reviewer A |
| Scope | Examples: 6 first (01, 02, 04, 05, 07, 08), others if time; worlds arena/corridor first | reviewer A |
