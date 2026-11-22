# Ultrasonic echo timing from kernel edge timestamps (M8)

## Context

The ultrasonic sensor (one SIG pin, GPIO5) is timed today by a Python busy-wait that polls the pin
(`Ultrasonic.ping()` in `server/picar_server/hal/sensors.py`). Three problems follow from polling:

1. **A blind spot (D8 residual).** A dip in the line shorter than the polling gap can't be seen. A stall over 1 ms
   (GIL, scheduler) makes the ping untrustworthy, and the stall rule discards it. Below 1 ms, a dip and rise are
   invisible. D8 documents this as the open residual.
2. **CPU.** The busy-wait holds a core while the echo is high, and the GIL switch interval is cut to 0.5 ms for it.
   That slows every other thread.
3. **Accuracy.** Polling sees the rise late (one GPIO read is ~70 µs on the Pi 3), so widths come out short.

The kernel's GPIO character device (uAPI v2) timestamps every edge in the interrupt handler (CLOCK_MONOTONIC, ns)
and queues the edges, whatever Python is doing. The plan listed this as M8.

### Measured on the car (2026-11-22, kernel 6.18.50+rpt-rpi-v8, Pi 3 B+)

| Quantity | Value |
|---|---|
| Echo rise after the trigger ends (kernel timestamps, 300 pings) | 383–417 µs |
| CPU, 150 pings: waiting for edges vs polling | 0.18 s vs 1.81 s (2 % vs 21 % of a core) |
| Polling width error against kernel timestamps | 15–156 µs short (median 78 µs, ~1.3 cm closer) |
| Enable edge detection, raw `GPIO_V2_LINE_SET_CONFIG` ioctl (server running) | median 133 µs, p99 261 µs, max 461 µs |
| The same through python3-libgpiod 2.2.1 `reconfigure_lines` | median 203 µs, p99 368 µs, max 418 µs |
| Output→input+edges with libgpiod, measured after the trigger | 218–544 µs; rise missed in 45 % of pings |
| Edge sequence gaps, spurious edges on arming | none |

Edge detection can only be on while the line is an input (the kernel rejects output + edges). Each ping must
therefore re-arm after the trigger. Arming takes longer than the module's rise delay in a minority of pings, and
the design must handle that.

## Approach

### 1. A small GPIO character-device module: `hal/gpio_cdev.py`

A pure-Python wrapper over the uAPI v2 ioctls (`fcntl`, `os`, `select`, `struct`); no new package. Why not
python3-libgpiod: its per-call overhead adds ~70 µs to arming (table above), which is the difference between
catching the rise in ~99 % or ~55 % of pings.

- `EdgeLine(chip="/dev/gpiochip0", offset=5, consumer="car-scratch")` requests the line as an input, with no edges
  and an event buffer of 64.
- `output_low()`: SET_CONFIG output with output value 0 (disarms edges).
- `set(v)`: SET_VALUES.
- `arm()`: SET_CONFIG input with both edges and the monotonic event clock.
- `get()`: GET_VALUES.
- `wait(timeout_s)`: `select.poll` on the request fd.
- `events()`: non-blocking read of 48-byte `gpio_v2_line_event` records, returned as `(kind, t_s, line_seqno)`.
- `close()`: input with no edges, then close the fd, so the line is never left driven.
- The config buffers are built once at start-up; each call is one ioctl on a prebuilt `bytes` buffer.
- Struct sizes (592, 272, 48 and 16 bytes) and ioctl numbers (`0xC250B407`, `0xC110B40D`, `0xC010B40E`,
  `0xC010B40F`) are asserted at import.

### 2. `EdgeUltrasonic(Ultrasonic)` in `hal/sensors.py`

It keeps the public state that `measure()`, `SensorHub` and the controller rely on: `ping_start`,
`busy_started`, `lost_echo_cm`, `discards`, `clear_t`, `echo_cm`, `batch_t`, BUSY/FAR/None. Only `ping()` and the
lifecycle change. `measure()`, `Sample`, D7 and D8 are unchanged.

**Ping sequence**

1. If a long cycle is in progress (`_busy_since`), stay armed. Read the events. A fall ends the cycle at its kernel
   timestamp. With no fall yet, return BUSY while the cycle is ≤ 0.6 s old; after that the line is stuck: None,
   counted as `stuck`.
2. If the line reads high when no cycle is known, the module is busy, possibly from a previous run. Record
   `_busy_since = now` and return BUSY. This never sets `busy_started`, and the line is never driven low against
   the module.
3. Drain stale events. Edges seen while idle are counted as `stray`.
4. `output_low()`, then sleep 2 ms (as today).
5. Take `ping_start = monotonic()`, which stays the D7 "nothing there" time.
6. Trigger: `set(1)`, sleep 10 µs, `set(0)`. Then `t_end = monotonic()`.
7. `arm()`, then `t_armed = monotonic()` and `high = get()`.
8. Wait for edges with `poll` until a fall ends the first pulse, or until `t_end + 40 ms`.

**Rules: a reading is never farther than the echo**

| Case | Result |
|---|---|
| Arm late: `t_armed − t_end > ARM_LATE_S` (0.6 ms) | Discard as `late_arm`; `lost_echo_cm = 0`. A pulse may have ended unseen. |
| First event R, then F | Width = F − R, exact to the interrupt latency. |
| First event F, and the line was high at arm (rise before arming) | Rise bound = `t_armed`, an upper bound, so the width is a lower bound and the reading is never farther. Error ≤ `t_armed − (t_end + 383 µs)` ≤ ~3.7 cm. |
| Width > 12 ms (on the lower bound) | FAR, certain. |
| Rise seen or line high, no fall by 40 ms | BUSY, `busy_started = True` (the kernel saw every edge after arming). `_busy_since` = rise bound; stay armed. |
| Line low at arm and no event by 30 ms | None (`no_rise`). If the arm was later than `RISE_MIN_S` (350 µs), `lost_echo_cm = 0`. |
| Events out of order (two of one kind, or F first while low at arm) | Discard as `edges`; `lost_echo_cm = 0`. The kernel reads the level in its interrupt thread, so a pulse shorter than that thread's latency shows as F F. |
| `line_seqno` gap (kernel buffer overflow) | Discard as `seq_gap`; `lost_echo_cm = 0`. |
| Pulses after the first one | Counted as `multi`, with the second pulse's width logged. The first pulse wins (today's semantics, safe direction). |
| R earlier than `RISE_MIN_S` after the trigger | Counted as `early_rise`. This is evidence against the measured rise floor; the reading is still used. |

**Remaining blind spot:** a pulse that starts and ends between the trigger and a ≤ 0.6 ms arm. It rises
≥ 383 µs after the trigger (measured), so it is shorter than ~220 µs, an object closer than ~3.7 cm. That is
at the module's minimum range and far inside every stop margin, so the car stops long before. This replaces
the polling blind spot, which allowed dips anywhere in the echo.

### 3. Selection, fallback and lifecycle (`hal/real.py`, `__main__.py`)

- **Selection.** A new option `--us-timing auto|edges|poll` (default `auto`) picks the implementation.
  - `auto` uses edges when the line request succeeds, otherwise polling with one warning in the log.
  - The dry-run backend, which uses the real sensors, follows the same rule.
- **`SensorHub`** takes the ultrasonic object instead of constructing it (an injectable factory). That lets
  tests run both implementations.
- **GIL switch interval.** The 0.5 ms setting applies only in polling mode.
- **Shutdown.**
  - `_join_thread` calls `hub.us.close()` instead of `gpio.setup(pin, IN)`, because in edge mode RPi.GPIO must
    never claim GPIO5.
  - The edge wait is cut into `poll` slices of ≤ 40 ms, so the thread sees the stop flag within one sample.
- **Reporting.** `/api/status` capabilities gain `ultrasonic_timing: "edges" | "poll"`; `car version` prints it.

### 4. Pre-existing stop timeout (separate task, root cause first)

4 of the last 19 server stops exceeded `TimeoutStopSec=2` and were SIGKILLed. ExecStopPost zeroed the motors, so
this is not a safety issue. The cause is unknown, so no fix until it is measured:
- Add debug log lines with timestamps for each shutdown step (aiohttp cleanup, controller, `backend.close`, join).
- Reproduce with 20 stop/start cycles on the car.
- Fix what the timestamps show.

### Not in scope

- Returning FAR at 12 ms instead of at the fall.
- Treating a double pulse as one echo. That needs open-space edge data first: the `multi` counter collects it.
- Triggering by toggling the pull-up so edges stay armed (an untested hardware behaviour).
- Removing the polling implementation: it stays as the fallback.

## Critical files

| File | Role |
|---|---|
| `server/picar_server/hal/gpio_cdev.py` | New uAPI v2 wrapper. |
| `server/picar_server/hal/sensors.py` | `EdgeUltrasonic`, `RISE_MIN_S`, `ARM_LATE_S`, discard reasons, `SensorHub` factory. |
| `server/picar_server/hal/real.py` | Selection, switch interval only when polling, `close()`. |
| `server/picar_server/__main__.py`, `app.py` | `--us-timing`, `ultrasonic_timing` in status. |
| `deploy/car`, `car-scratch.service` | `car version` output; no unit change expected. |
| `server/tests/fakes_hw.py` | `FakeEdgeLine`. It replays pulse schedules on the fake clock (multi pulses, long cycles), with injectable arm latency, lost or misread edges and seqno gaps. |
| `server/tests/test_gpio_cdev.py` | Struct layout, ioctl numbers, event parsing (against a fake fd). |
| `server/tests/test_hal_edges.py` | Every rule in the table above. |
| `server/tests/test_hal_sensors.py`, `test_no_echo_trust.py` | Existing property and integrated tests, parametrized over both implementations where the rule is shared. |
| `docs/decisions.md` (D9), `components.md`, `contracts.md`, `pi-tune.md`, `on-car-checklist.md` | Docs. |

## Verification

1. **Tests (Docker).** `docker compose run --rm server-test` must pass in full, including these properties:
   - For every arm latency 0–0.6 ms and every echo width 0.2–27 ms, the reading is never farther than the echo.
   - A pulse that could have ended unseen always surfaces as `lost_echo_cm`.
   - Long cycles are tracked to their fall.
   - The integrated `Car` test stops at full speed in front of a hidden echo with the edge implementation.
2. **On the car, stationary (no motion).** Deploy, then for 10 minutes compare against the passive `/dev/gpiomem`
   observer (`sigwatch.py`) on the same pings:
   - Every edge reading is within the measured bounds of the observer's.
   - Per-minute discard counters: `late_arm` < 0.5 %, `early_rise` = 0, `edges` and `seq_gap` = 0.
   - Server CPU (`ps`) against polling mode.
   - 20 stop/start cycles without a timeout, once part 4 is done.
3. **On the car, with the owner.** Each motion step is announced first and starts only on "go":
   - A wall approach at 30/60/100 %: stop distances not worse than D8 on polling.
   - An open-space drive: no `sensor_fault`; collect the `multi` statistics.
4. **Fallback.** `--us-timing poll` behaves exactly as `913aed2`; `auto` with the line held by another process
   falls back with one warning.
