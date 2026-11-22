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
3. The read takes only the I2C bus lock (like the light read), never the motor lock. A motor write or stop still
   waits for the bus lock while the sensor thread holds it; the lock gives no priority to waiters, so the wait can
   span the line read, its one retry and the light read (about 10 ms with a healthy module; see the residual risk
   below for a stalled bus).

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

## D5 — Ultrasonic long no-echo cycle is "busy", not a fault (2026-11-21)

**Symptom.** `sensor_fault` / `sensor_ok` for the ultrasonic flapped about once a second (45 to 54 times a
minute) while the car stood still facing a weak reflector about 110 cm away.

**Measured on the car (2026-11-21).**

- A passive observer read GPIO5 through `/dev/gpiomem` (never claiming the pin) while the server ran. In that pose
  most echoes were the module's normal no-echo timeout (27 ms, reported `far`), a few were real (6 ms, ~105 cm),
  and 56 of 204 pulses were 40.2 to 49 ms: the server cutting them at its 40 ms limit.
- With the server stopped and pulses never cut: about one ping in 15 (8 of 120) the module holds the line high for
  a fixed **401.5 ms** instead of 27 ms, ignoring triggers meanwhile, then answers normally. Two such cycles back to
  back occur. More rest between pings did not prevent it (2 to 60 ms tried).
- The old code cut the pulse at 40 ms, returned "no answer", and re-triggered: about 9 failed pings during one
  cycle, three `none` samples, so `fault`, then `ok` when the cycle ended. Each re-trigger also drove the pin low
  for 2 ms against the module's high output.

**Decision.**

1. An echo still high at 40 ms is `BUSY` (the module's long cycle), never `far`. While the line stays high the
   pin is not driven and not re-triggered.
2. A busy sample is `none` and never counts toward the 3-sample fault. A line high for longer than 0.6 s (one
   cycle is 401.5 ms) is a stuck line and counts as "no answer" as before, and so does no rising edge after the
   trigger. (A first version also faulted after 1.5 s without an `ok`/`far` reading. Facing open space that
   fired every few seconds: with the server stopped, 59 to 75 % of pings were long cycles at every rest between
   pings from 2 to 100 ms, and none of 300 pings returned a real echo. A cycle that starts on the trigger and ends
   by itself is a living module that heard nothing, so that rule was removed.)
3. Motion safety is unchanged: a forward start needs an `ok`/`far` distance measured less than 150 ms ago, and
   while driving forward the same check runs at every sensor sample (about every 40 ms); if samples stop arriving,
   the controller loop's own check stops forward motion after 0.5 s. During a busy cycle a forward drive is
   therefore refused or stopped; only the false fault flag and its events are gone. (Second review: freshness used
   to count from when a sample *arrived*, so a sample delayed by a slow line read made an old distance look fresh;
   it now counts from `Sample.t`, taken right after the pings. That flaw predates D5.)

**Review follow-ups (same day).** An independent review found two problems in the same path, both fixed with
tests: (a) the change kept the "last reading was far" memory across a busy cycle, so the next sample took one
ping; an obstacle that appeared during the blind window and a single missed echo would have published `far`. A
busy sample now clears that memory, so the next sample takes 3 pings. (b) Pre-existing: with 2 valid pings of 3
the median averaged them, so `far` (999) and a real 10 cm echo made 505 cm. The lower of two valid pings now
counts (`median_low`).

**Residual.** Driving forward toward a weak reflector, or into open space, still stops the car when the module goes
into its long cycle (no fresh reading for 150 ms); facing open space that is most of the time. Accepting "busy" as clear for up to one cycle while moving would remove those
stops at the cost of up to ~0.4 s without distance data (about 10 cm at full speed); that is an owner decision and
is not made here.

## D6 — Line follower: retry quick failures, lose the module only after 3 failed samples (2026-11-21)

**Symptom.** `sensor_fault` / `sensor_ok` for the line follower about every 10 to 20 s with the car standing
still, each time with "line follower absent", then "present" 2 s later.

**Measured on the car (2026-11-21, server stopped).** D4 assumed about 1 % invalid frames (from 40 reads). Longer
runs show:

- Back-to-back reads (1883 in 6 s): the module is busy for one read (2.9 ms) every 11 ms and answers all-0xFF
  then; the next read is valid. 31 % of back-to-back reads, about 15 % at the server's 40 ms cadence.
- The server's exact pattern (light reads, then a line read with one immediate retry, every 40 ms): 1500 samples
  with no I2C error and 2 samples invalid after the retry, never two in a row; in other runs about 1 sample in 300
  got EIO (errno 5), with or without ultrasonic pings, always followed by good reads.
- A missing device is NACKed at once (D4); an EIO here is a transfer error from a present module.

The old rules turned these into losses: one I2C error made the module absent at once (2 s back-off, then present
again: one flap every ~13 s at 25 samples a second), and while moving one error or one sample with two invalid
frames switched moving reads off until the next stop, which at these rates happens within seconds of driving.

**Decision.**

1. An invalid frame or an I2C error that returns quickly is retried once at once (as invalid frames already were).
2. A sample that still has no frame is skipped (telemetry bridges one skipped frame; the controller raises its line
   `sensor_fault` only after 3 samples without a frame). Only 3 failed samples in a row make a present module
   absent while stopped, or switch moving reads off until the next stop while moving.
3. A module that is not present (never seen, or absent and re-probed after its back-off) is read once per probe and
   backs off on an error, as before.
4. Unchanged from D4: a bus call slower than 15 ms switches moving reads off at once (the stalled-slave protection),
   and moving reads take only the bus lock. They need 10 good reads since the last failure, where a failure is now a
   slow bus call (stopped or moving, valid frame or not) or losing the module (3 failed samples); an isolated quick
   failed sample is not one, because it cannot stall the bus. (Second review: a slow failed read while stopped
   kept the qualification in the first version of D6; it now resets it.)

**Risk.** Bus time per sample is bounded as before: at most two quick reads (about 6 ms) per sample under the bus
lock, and a module that keeps failing is dropped after 3 samples (about 120 ms).

## D7 — Trust a "nothing there" reading for 0.48 s while driving forward (owner decision, 2026-11-21)

**Context.** Facing open space the ultrasonic module answers most pings with its 401.5 ms long cycle (D5), so an
`ok`/`far` reading younger than 150 ms is rare and forward driving was refused or stopped (`sensor_fault: no fresh
distance reading`, then `BLOCKED`).

**Owner decision.** Trust a "nothing there" reading for a short time while driving forward, accepting that the car
is blind for that long. Measured on the car with a passive pin observer (60 s facing open space): consecutive long
cycles start 409 to 413 ms apart, and a cycle is only known 40 ms after its ping, its sample reaching the controller
a few ms later; so counted from the ping the car is blind up to about 0.463 s between readings. 0.4 s (first
proposal) stopped the car about 80 times a minute. The owner chose **0.48 s counted from the ping**: blind for up to
11.7 cm at full speed (24.4 cm/s). (A first build counted 0.45 s from the detection, which really allowed about 0.49 s.)

**Rule.**

1. A "nothing there" reading (`Sample.clear_t`, the time of its ping) is a ping that ended `far`, or that began a long
   cycle cleanly (no echo for 40 ms without a timing gap), in a sample where no ping heard an echo. Finding the
   module still busy is not a new reading. A ping discarded for a timing gap that may have ended within range still
   counts as an echo (`Sample.echo_cm` = the closest it could have been).
2. Forward motion may start or continue when an `ok`/`far` distance was measured at most 150 ms ago (unchanged) **or**
   a "nothing there" reading is at most 0.48 s old **and** no echo was heard after it **and** no echo closer than the
   near distance was heard in the last second. The near distance is 1 m, or twice the full-speed stopping margin when
   that is more (a large `stop_cm`). An object last heard just under 1 m away cannot come closer than about 60 cm
   before the trust runs out, even at full speed.
3. While `blocked`, a "no reading" sample inside a trusted window counts like `far` toward releasing it.
4. Unchanged: a fault still stops and refuses; the obstacle guard acts on every `ok` reading; the controller loop
   stops forward motion when samples stop arriving for 0.5 s; the simulator sends no `clear_t`, so it behaves as
   before.

**Independent review (third round) before the first deploy** found three ways the first build relaxed more than
this: a near echo dropped for a timing gap vanished entirely; the window counted from the detection, so a stuck line
or a scheduling delay stretched it; and an obstacle whose 150 ms reading had expired could be forgotten in favour of
an older "nothing there". All three are fixed as above, each with a test, plus integration tests that run the real
sensor code into the controller on one clock (open-space rhythm keeps driving; a line stuck high after one cycle
stops within the window; a dropped near echo blocks the trust).

**Fourth review round** found three timing and settings gaps, fixed with tests that try a stall at every
instruction position: a stall between a pin read and its timestamp could hide a near echo (each read is now
bounded by the timestamp taken before it, and a ping whose echo window was not watched counts as a possible echo);
a stall at the rising edge could make the reading's time late (it is now taken before the trigger, so it is never
later than the ping); and echoes were judged "near" against the settings at the time they were heard (the last
second of echoes is kept and judged against the current stop distance).

**Residual.** For up to 0.48 s the car drives on a reading that said nothing was in range: something that moves into
the path in that time, or a reflector the module cannot hear (which can already read `far`), is not seen until the
next reading.

## D8 — Near an obstacle, drive through missed readings while the last echo is far enough (owner decision, 2026-11-21)

**Context.** A drive recorded on the car (8 minutes; telemetry, events and the sensor pin's raw pulses) showed
about 20 forward stops labelled `sensor_fault`, none of them a sensor fault: each was a run of good echoes, then one
long cycle (a missed reading, 400 ms) while driving toward something under 1 m away. Near an obstacle D7's trust is
off, so one missed reading stopped the car. (The battery was low: 7 `power_low` events, and the Pi throttled, but the
sensor still answered 99.9 % of pings with the motors running; low power was not the cause.)

**Owner decision.** Keep driving through missed readings while the last measured distance, minus the farthest the
car could have travelled since, stays above the stopping margin.

**Rule.**

1. Forward motion may also start or continue when every echo heard in the last 0.48 s (an `ok` reading, or any
   ping's echo in the sample, including the lower bound of a ping dropped for a timing gap), minus the speed limit's
   ground speed x (its age + 0.1 s), is at or beyond the stopping margin at the current speed. The 0.1 s covers the
   time to the next check: a sample every 40 ms, and the controller loop now checks freshness on every 50 ms tick
   as well, so this holds even if the sensor thread stalls (before, only a 0.5 s "no samples" fallback did).
2. It never releases `blocked`.
3. Example at the car's calibration (24.4 cm/s at full speed, 37 cm margin): an echo at 80 cm keeps it driving for up
   to 0.48 s without readings; an echo at 50 cm stops it after 0.15 s as before.

**Also changed (no decision needed).**

- Ultrasonic `sensor_fault` events carry `reason`: `no_answer` (a real fault, paired with `sensor_ok`), `no_reading`
  (a freshness stop) or `no_samples` (the sensor thread stopped delivering).
- A ping whose timing loop was interrupted is still `far` when the line was seen high past 12 ms: no echo can have
  ended within range. The recording showed 8 standstill faults made of good `far` answers thrown away this way.
- The reasons pings give no reading (`no_rise`, `gap`, `unwatched`, `stuck`) are counted and logged once a minute.

**Independent review (sixth round) before the first deploy** found three gaps, fixed with tests: a line that dipped
(echo ended) and rose again inside a timing stall could still read `far` (only highs seen before the first stall
count now); an echo was dated by its sample's end although its ping could be ~80 ms older (D8 now dates every echo
from the start of its sample's pings, D7's near memory still from the end); and a loop tick and a sample could both
report the same stop (the first to set `blocked` reports it). Testing every stall position also found that a stall
between the trigger and the first read could hide a whole echo; stalls are now measured from the trigger.

**Seventh round** found two more stall paths (a stall before the first read hid the rise; a stalled ping that ended
in a long cycle dropped its possible echo). Instead of patching paths one by one, the ping now follows one rule: a
stall anywhere from the trigger to the decision means the line was not watched, so the ping yields no distance and
no "nothing there" and records a possible echo at its safe lower bound (0 when the rise itself was not watched).
The only exception is `far` when the rise and the first 12 ms were watched without a stall. A property test checks
it for every stall position, three stall lengths and three pulse patterns (an echo hidden before a second high pulse
or a long cycle): the hidden echo always surfaces as an echo no farther than it was, and never as "nothing there".

**Eighth round** confirmed the stall rule (healthy pings classified exactly as before over 13,479 comparisons) and
found that a pause under the 1 ms stall threshold right at an echo's end made the distance read too far (46 cm for
34 cm). The end of an echo is now timed from the last read that saw the line high, so a reading is never farther
than the truth (normally under 1 cm closer).

**Residual: a dip shorter than the polling can see.** The same round showed that a sensor whose output ends an echo
and goes high again within a fraction of a millisecond could hide that echo from any polling loop. Measured on the
car (passive observer, 8 minutes, about 20,300 pings at microsecond resolution): 1.9 % of pings have two pulses, and
the pattern is always a 0.2 to 0.7 ms glitch (it would be an object at 3 to 12 cm), a 0.06 to 0.2 ms dip, then the
real echo (median end 6.4 ms, about 1.1 m); the first pulse rises 416 us after the trigger like any echo. That is
the reverse of the hazard (a glitch before the real echo, not a stray pulse after a real one). When the polling sees
the dip it reads a false echo at about 8 cm, which is the safe direction: it only disables the D7/D8 trust for a
second. Not chased further.

**Residual.** As D7: something that moves toward the car while it drives on an echo-based bound is seen only at the
next reading.

## D9 — Time ultrasonic echoes from kernel edge timestamps (2026-11-22)

**Context.** The polling ping (D5, D8) has three limits:

- **Blind spot.** It can't see a dip shorter than its polling gap: the D8 residual.
- **CPU.** It holds a core while an echo is high: 21 % of a core in a 150-ping comparison, against 2 % for edges.
- **Accuracy.** It reads widths short: 15–156 µs, median 78 µs.

The owner asked for the kernel-timestamp version (plan item M8, plan in
`docs/plans/2026-11-22-ultrasonic-edge-timing-plan.md`).

**Decision.**

- **Timing.** The server times echoes from the GPIO character device's edge events. The kernel stamps each edge in
  its interrupt handler, on the clock behind `time.monotonic()`.
  - `hal/gpio_cdev.py` is a pure-Python uAPI v2 wrapper. Its layouts were checked against `linux/gpio.h`
    (`tools/diag/gpio_abi.c`).
  - `hal/ultrasonic_edges.py` holds `EdgeUltrasonic`. It has the polling sensor's interface, so `measure()`,
    D5, D7 and D8 are unchanged.
- **Trigger.** The line is never driven.
  - It stays an input with both edges detected, held low by the internal pull-down.
  - A ping switches to the pull-up for about 10 µs and back, and that fall triggers the module.
  - Edge detection is therefore on all the time. No echo can come and go between a trigger and re-arming, and
    nothing can fight the module's output.
- **Start-up check.** The server checks that the module answers pull-up triggers (at least 4 of 5).
- **Selection.** `--us-timing poll|edges|auto`, or `CAR_SCRATCH_US_TIMING`.
  - The default stays `poll` until the car validation below is complete.
  - `auto` falls back to polling only when the kernel lacks the interface or the module ignores a pull-up trigger.
    A line held elsewhere (EBUSY) is an error, as it is for polling.
  - `/api/status` and `car version` report the timing in use.

**Measured on the car (2026-11-22, kernel 6.18.50, Pi 3 B+).**

- **Pull-up trigger:**
  - The module answered 1000 of 1000 triggers on an idle CPU, and 498 of 500 under a 3-core load.
  - It rises 289–402 µs after the trigger, timed from before the pull-down call.
  - The real sensor answered 2247 of 2247 pings idle, and 2239 of 2244 under load.
  - The trigger call took 16–62 µs; one preempted call took 574 µs.
- **The rejected alternative: a driven trigger, then re-arming edge detection.** On an idle CPU (clocked down),
  re-arming outran the module's rise in 54 % of pings, and the arming window reached 459 µs.
- **Kernel timestamps** (pull toggles on an unused pin):
  - Edges are stamped 15–75 µs after the start of the ioctl that makes them.
  - Measured widths never fell outside the userspace bracket.
  - Edge kinds are wrong for pulses of about 40 µs, and right from about 60 µs.

**The rules** (`ultrasonic_edges.py`):

- **Our own trigger's edges.** Events stamped up to the trigger call's end plus 0.1 ms are ours and are ignored.
  The module rises no earlier than 0.25 ms after the call starts, and a trigger call slower than 0.14 ms makes the
  ping uncertain. So the two never mix.
- **Never farther.** A fall-latency allowance of 0.1 ms (1.7 cm) is subtracted from every width. A reading is
  never farther than the first pulse, as long as the kernel's interrupt entry latency stays under 0.1 ms
  (measured ≤ 75 µs). That condition can't be checked from user space, which is one reason the default stays
  polling until the car validation is done.
- **"Nothing there"** (FAR, or a cleanly started long cycle) is reported only from a first pulse whose rise and fall
  were both seen. Every uncertain ping records a possible echo at 0 cm. The uncertain cases:
  - a slow trigger;
  - events out of order;
  - a sequence gap (counted from the request's start);
  - a lost fall;
  - a gap while following a cycle;
  - an I/O error.
- **Before every trigger,** the line must have been low with no edge for 2 ms. A late pulse of the previous answer
  is never taken for this ping's echo.
- **The double pulse** (D8). The first pulse is the reading. A second one is followed to its end and counted as
  `multi`.
- **Fallback.** Five pings in a row that break a measured limit, or five triggers in a row with no answer, switch
  the server to polling. The switch happens once the line is free, and the batch's evidence is kept.

**Review.** The plan had one round, and the implementation one round with two independent reviewers. No
reading farther than the first pulse was found (120,000 fuzzed pings). The findings were fixed with tests:

- the driven trigger could fight the module, so the pull-up trigger replaced it;
- a fall queued at the deadline;
- sequence tracking before the first event;
- a cycle that rose again during the settle wait;
- the fallback's state;
- an I/O error left the line driven;
- a leaked line on a failed start;
- an unbounded socket close on shutdown;
- a fake that did not model the kernel's merges.

**Also fixed (pre-existing, found by the plan reviews).**

- **Distance age.** A distance's freshness counts from its newest valid ping (`Sample.dist_t`).
- **Releasing `blocked`.** A sample whose pings heard an echo inside the margin never counts toward release.
- **Server stop.** An open WebSocket used to keep the server alive 7.5 s on stop.

**Residuals.**

- **Merged edges.** The kernel keeps one timestamp per line and derives an edge's kind from the level in its
  interrupt thread. Edges closer together than its handling latency (tens of µs) can merge with no sequence gap.
  - An odd number merged shows as an out-of-order sequence and is discarded.
  - An even number merged is invisible: a dip of a few tens of µs inside an echo, or a stray pulse of that length.
  - This replaces the polling blind spot, which covered dips under ~70 µs normally and up to 1 ms under a stall.
- **The rise floor** describes this module. A module that answers faster than 0.25 ms breaks it, which shows as
  `early_rise` and falls back to polling.
- **From D7 and D8:** something moving toward the car is seen only at the next reading.

**Still to do before edges become the default:**

1. A 10-minute stationary run under the server.
2. A drive test with the owner. Each motion step is announced first.

Then switch the default and record the results here.
