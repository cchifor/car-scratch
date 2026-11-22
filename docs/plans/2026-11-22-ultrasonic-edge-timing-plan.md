# Ultrasonic echo timing from kernel edge timestamps (M8)

## Context

The ultrasonic sensor (one SIG pin, GPIO5) is timed today by a Python busy-wait that polls the pin
(`Ultrasonic.ping()` in `server/picar_server/hal/sensors.py`). Polling causes three problems:

1. **A blind spot (the D8 residual).** A dip in the line shorter than the polling gap can't be seen. A stall over 1 ms
   (GIL, scheduler) makes the ping untrustworthy, and the stall rule discards it. Below 1 ms, a dip followed by a
   rise is invisible.
2. **CPU.** The busy-wait holds a core while the echo is high.
3. **Accuracy.** Polling sees the rise late, so widths come out short.

The kernel's GPIO character device (uAPI v2) timestamps edges in the hard-IRQ handler (`ktime_get_ns`,
CLOCK_MONOTONIC) and queues them. This was plan item M8.

### What the kernel does (checked against rpi-6.18.y source by two independent reviews)

- **Edges on inputs only.** Edge flags are valid only on inputs (EINVAL otherwise). `SET_CONFIG` can switch a live
  request between output and input+edges.
  - Switching to output calls `edge_detector_stop` (`free_irq`), but queued events are **not** flushed.
  - Switching to input+edges calls `request_threaded_irq`, which creates an IRQ thread. That makes arming slow.
- **Event kind and timestamp.** The timestamp is taken in the hard handler. For both edges, the event kind is the
  level read later, in the IRQ thread.
  - The BCM2835 edge flow (`handle_edge_irq`) doesn't mask the line, and there is one timestamp slot per line.
  - Edges closer together than the handling latency **merge, with no `line_seqno` gap**.
  - An odd merge shows as R R, F F or F-first. An even merge looks plausible: R1 F1 R2 becomes one R at R2, and
    F1 R2 F2 becomes one F at F2.
- **Overflow** drops the oldest event and leaves a visible `line_seqno` gap.

### Measured on the car (2026-11-22, kernel 6.18.50+rpt-rpi-v8, Pi 3 B+)

| Quantity | Value | Source |
|---|---|---|
| Echo rise after the trigger (`t` read after `set(0)`), where R was captured | 383–417 µs (200 of 300 pings) | spike 1, libgpiod |
| CPU, 150 pings: waiting for edges vs polling | 0.18 s vs 1.81 s (2 % vs 21 % of a core) | spike 1 |
| Polling width vs kernel width, same pulse (spike's poll loop, not the server's) | 15–156 µs short, median 78 µs | spike 1 |
| Arm after the trigger, libgpiod (events-mode pings) | 218–544 µs, median 274; rise missed in 68 of 150 | spike 1 |
| Input→input+edges, idle GPIO6: raw ioctl vs libgpiod | median 133 vs 203 µs, p99 261 vs 368 µs | spike 2 |
| Edge timestamp after the start of the ioctl that makes the edge (GPIO6 pull toggles) | 15–75 µs (one outlier of 852 µs under 3-core load) | spike 3 |
| Measured low-pulse width vs the userspace bracket | never outside the bracket (2 400 pulses) | spike 3 |
| Kernel edge kinds for low pulses of ~40 µs / ~60 µs / ≥ 90 µs | 43 / 1 / 0 wrong out of 200 each (R R or a lone R) | spike 3 |

**Still to measure:** the real raw sequence on GPIO5 (output → trigger → input+edges), under the server's GIL
setting. The constants in §2 are fixed from it (step 0 of the implementation).

## Approach

### 0. Measure first (car, server stopped about 2 minutes, nothing moves)

The spike scripts live in `server/tools/diag/` (`edgespike.py`, `irqlat.py` and the passive observer
`sigwatch.py`), with their invocation documented.

1. **Raw ping sequence.** Measure the blind window `B = t_armed − t_fall_lo` (defined in §2) over 1 000 pings,
   with and without 3 busy processes, and with the switch interval at 0.5 ms and at the 5 ms default.
2. **Trigger by pull-up.** Does the module trigger when its SIG line is raised only by the internal pull-up, with
   edges kept armed? If it does, the line never has to become an output and arming disappears:
   - the trigger's own edges get kernel timestamps;
   - there is no blind window at all.

   This uses the weak internal pull resistors only (no contention possible). If it works reliably over 1 000
   pings, §2 switches to it, and the rules for the arming window drop out. Otherwise §2 as written.

### 1. `hal/gpio_cdev.py`: a uAPI v2 wrapper, no new package

Already written and checked on the car on an unused input line: request, arm, read, wait, release. Changes:

- **Line identity.** Find the line by its name `GPIO5` (`GPIO_V2_GET_LINEINFO_IOCTL`) on the chip labelled
  `pinctrl-bcm2835`/`pinctrl-bcm2711`, instead of assuming `gpiochip0`.
- **Bias.** Set `BIAS_DISABLED` explicitly on every input config, as rpi-lgpio does today.
- **Events.**
  - Validate the event id (1 or 2) and the line offset of each event.
  - Keep `line_seqno` continuity across drains and reconfigurations (uint32 wraparound).
  - Timestamps are integer ns converted once to float seconds (exact to well under 1 µs).
- **`wait()`.** Takes an absolute deadline, rounds milliseconds up (no busy loop), and raises `OSError` on
  POLLERR/POLLHUP/POLLNVAL.
- **Cleanup.**
  - Idempotent.
  - Releases the fds on a partial initialization.
  - `close()` restores input before release. After SIGKILL the line keeps its last state, which is input except
    for the ~0.1 ms trigger.
- **ABI check.** A one-off C program (`server/tools/diag/gpio_abi.c`, built in a Debian container against
  `linux/gpio.h`) prints sizeof, offsetof and the ioctl numbers. A test compares them with the Python constants.

### 2. `EdgeUltrasonic(Ultrasonic)` in `hal/sensors.py`

It keeps the state that `measure()`, `SensorHub` and the controller use: `ping_start`, `busy_started`,
`lost_echo_cm`, `discards`, `clear_t`, `echo_cm`, `batch_t`, and the results BUSY / FAR / None. New:

- `meas_t`: the observation time of the newest valid ping (see §4).
- `stop`: a `threading.Event` checked between pings and in every wait slice.

**Constants (module properties; the constants they rely on are counted when broken)**

| Name | Value | Meaning |
|---|---|---|
| `RISE_MIN_S` | 0.35 ms | The module raises its line no earlier than this after the trigger falls (measured ≥ 383 µs). |
| `PULSE_MIN_S` | 0.1 ms | No real echo is shorter (1.7 cm, inside the module's 2 cm minimum range; measured glitches ≥ 0.2 ms). |
| `FALL_LAT_S` | 0.1 ms | Allowance for a late fall timestamp (spike 3: ≤ 75 µs). It is subtracted from every width. |
| `ARM_CLEAN_S` | `RISE_MIN_S + PULSE_MIN_S` = 0.45 ms | With the blind window up to this, no complete pulse can hide before arming. |

**Ping sequence**

1. **Long cycle in progress (`_busy_since` set).** The line stays armed.
   - Read the events. A delivered fall, followed by a low level, ends the cycle. A new rise after it means the
     module is still answering, so keep waiting.
   - With no fall yet, return BUSY while the cycle is ≤ 0.6 s old; after that, None (`stuck`).
   - A low level with no fall delivered is `lost_fall`: the cycle ends, and the next ping is treated as unwatched
     (`lost_echo_cm = 0`).
   - Ending a cycle never creates `clear_t` and never re-dates an old echo.
2. **Line high with no cycle known** (start-up, or after a discarded ping).
   - Arm, record `_busy_since = now` once (no reset on later calls), and return BUSY with `busy_started = False`.
   - Never drive the line.
3. `output_low()`, then **drain**. Every queued event is now from before this ping: count it as `tail` (the
   previous ping's extra pulses), and keep the seqno continuity.
4. Sleep 2 ms. Check that the line still reads low (else go to 2).
5. `ping_start = monotonic()` (D7's "nothing there" time, as today).
6. `set(1)`, sleep 10 µs.
7. `t_fall_lo = monotonic()`, then `set(0)`. The physical fall happens after `t_fall_lo`.
8. `arm()`, then `t_armed = monotonic()` and `high = get()`; `t_high = monotonic()`.
   - The blind window is `B = t_armed − t_fall_lo` (an upper bound).
9. **Wait** in slices of ≤ 40 ms against absolute deadlines until the first pulse ends (or 40 ms after its rise):
   - no rise by `t_fall_lo + 30 ms` gives `no_rise`.
   - Before any timeout decision, drain once more; an event counts by its timestamp, not by its delivery time.
10. **After the first fall**, linger 0.3 ms. If a second rise comes, follow it to its fall (at most 40 ms) before
    returning, and count it as `multi` with its width. The reading is still the first pulse.
    - Without this, the glitch-then-echo pattern (1.9 % of pings) would make the next ping find the line high.

**Rules: a reading is never farther than the first echo, and "nothing there" only when no echo can have been missed**

`width = F − R − FALL_LAT_S` (with the rise bound below), floored at 0.

| Case | Result |
|---|---|
| `B > ARM_CLEAN_S` | A complete pulse may have hidden before arming. No distance, no FAR, no `clear_t`, `busy_started = False`; `lost_echo_cm = 0`; `late_arm`. If the line is high at arm, keep tracking it as a cycle. |
| First event R (with `R ≥ t_fall_lo + RISE_MIN_S`), then F | The width above. FAR if it exceeds 12 ms. |
| R earlier than `t_fall_lo + RISE_MIN_S` | `early_rise`: the floor is broken. Discard the ping (`lost_echo_cm = 0`) and log an error. Five in a row switch the server to the polling implementation, logged. |
| No R, first event F, line high at arm, `B ≤ ARM_CLEAN_S` | The rise came before arming: rise bound = `t_armed` (upper), so the width is a lower bound. `F ≤ t_armed` reads as 0 cm. |
| Any pulse shorter than `PULSE_MIN_S` | `short_pulse`: the floor is broken. Discard and log, as for `early_rise`. |
| Rise known (R, or high at arm with `B ≤ ARM_CLEAN_S`), no F by rise + 40 ms, line still high | BUSY. `busy_started = True` only if nothing in this table made the ping uncertain. Stay armed. |
| No F delivered but the line is low at the deadline | `lost_fall`: discard, `lost_echo_cm = 0`. |
| Line low at arm and no event by 30 ms | None (`no_rise`); `lost_echo_cm = 0` if `B > ARM_CLEAN_S`. |
| Events out of order: two of one kind, or F first while low at arm | Discard (`edges`), `lost_echo_cm = 0`. |
| `line_seqno` gap | Discard (`seq_gap`), `lost_echo_cm = 0`. |
| A read or ioctl error | Discard (`io_error`) and log. `lost_echo_cm = 0`, so the fault path counts it. |

**Residuals (documented in D9):**

- **Merged edges.** Edges closer together than the kernel's handling latency (tens of µs; kinds correct from about
  60 µs, spike 3) merge silently in pairs.
  - The cases that matter: a pulse with a dip shorter than that, or a stray pulse ending within it.
  - Odd merges are caught by the order rule.
  - This replaces the polling blind spot (dips under ~70 µs normally, up to 1 ms under a stall).
- **Module behaviour.** The floors (`RISE_MIN_S`, `PULSE_MIN_S`) describe this module. They are checked on every
  ping and fall back to polling when broken.

### 3. Selection, fallback, lifecycle

- **`--us-timing auto|edges|poll`** (default `auto`).
  - `auto` probes edge support at start (request input, arm, disarm, while the line is idle).
  - It falls back to polling only when the uAPI is missing or rejects edges (ENOENT, ENOTTY, EINVAL).
  - EBUSY (the line is held elsewhere) fails loudly: the backend is unavailable, as it is today with rpi-lgpio.
  - `edges` never falls back.
  - Linux-only imports stay out of the simulator and forced-poll paths.
- **Injection.** `SensorHub(..., ultrasonic=None)` takes an object. The default stays the polling `Ultrasonic` on
  the given GPIO, so existing tests don't change. `RealBackend` and `DryRunBackend` pass the selected one.
- **GIL switch interval.** Keep 0.5 ms in both modes: a 5 ms GIL wait between `set(0)` and `arm()` would make
  late arms common. Restore it only when this backend changed it. Step 0 confirms the choice.
- **Shutdown.**
  - `_join_thread` sets `us.stop`, joins for 1 s, and only then calls `us.close()`. If the thread is still alive,
    it logs and leaves the line to process exit.
  - Polling `Ultrasonic.close()` is today's `gpio.setup(pin, IN)`.
  - In edge mode, RPi.GPIO never touches GPIO5.
- **Reporting.** `/api/status` → `car.ultrasonic_timing: "edges" | "poll"`, a separate field (capabilities stay
  `dict[str, bool]`). `car version` prints it.

### 4. Two fixes that apply to both implementations

Found by the reviews; they are pre-existing.

- **Observation time.** `Sample.t` is taken after the pings and the line/light reads. A stall after the echo makes
  a distance look fresher than it is.
  - New `Sample.dist_t` = the newest valid ping's observation time: its fall timestamp (edges), or the moment the
    fall was seen (polling).
  - The controller's distance freshness uses `min(now, s.t, s.dist_t)`.
- **Releasing `blocked`.** A sample whose `echo_cm` is inside the margin never counts as clear, even if its median
  reading does. Today a `far` median with a dropped close echo counted toward release.
  - The ordinary freshness rule is unchanged: a newer clean reading still supersedes an older possible echo.

### 5. Already done on this branch

- **Stop timeout** (`a7c1711`). With one open WebSocket the server took 7.5 s to exit, and systemd killed it at 2 s.
  - Now `on_shutdown` holds the car and closes every socket first, and `shutdown_timeout` is 0.5 s.
  - It exits in under 1 s; tested.
- **`gpio_cdev.py` and `FakeEdgeLine`.** The fake gets merge modelling (pairs within `irq_thread`) and delayed
  delivery.

### Not in scope

- Returning FAR at 12 ms.
- Treating the double pulse as one echo (the `multi` statistics come first).
- Removing the polling implementation (it stays as the fallback).

## Critical files

| File | Role |
|---|---|
| `server/picar_server/hal/gpio_cdev.py` | uAPI v2 wrapper (line lookup, bias, validation, deadlines). |
| `server/picar_server/hal/sensors.py` | `EdgeUltrasonic`, constants, discard reasons, `meas_t`, `stop`, `SensorHub(ultrasonic=)`, `dist_t`. |
| `server/picar_server/hal/base.py` | `Sample.dist_t`. |
| `server/picar_server/hal/real.py`, `dryrun.py` | Selection, shutdown order, switch interval. |
| `server/picar_server/controller.py` | Freshness from `dist_t`; block release with `echo_cm`. |
| `server/picar_server/__main__.py`, `app.py`, `deploy/car` | `--us-timing`; `ultrasonic_timing` in status and `car version`. |
| `server/tools/diag/*` | The measurement scripts and the ABI check. |
| `server/tests/fakes_hw.py` | `FakeEdgeLine`: physical waveform from the trigger's fall, arm latency, IRQ-thread kinds, merges, late stamps, delayed delivery, losses. |
| `server/tests/test_gpio_cdev.py`, `test_hal_edges.py`, `test_shutdown.py` | New tests. |
| `server/tests/test_hal_sensors.py`, `test_no_echo_trust.py`, `test_known_distance.py`, `test_hal_real.py`, `test_hal_dryrun.py`, `test_server.py` | Shared safety properties run over both implementations. Polling-only stall tests stay polling-only. |
| `docs/decisions.md` (D9), `components.md`, `contracts.md`, `protocol.md`, `pi-tune.md`, `on-car-checklist.md` | Docs. |

## Verification

1. **Tests (Docker).** The full suite passes, including these properties.
   - **Never farther.** Over arm latency 0–1 ms, rise delay 0.35–0.45 ms, widths 0.1–27 ms (dense around 12 ms),
     IRQ latency up to `FALL_LAT_S` and a stall at every clock call, the reading is never farther than the first
     pulse and never FAR when the first pulse ended within 12 ms.
   - **Hidden first pulse.** It is followed by an echo, FAR, or a long cycle, with plausible alternating events and
     no seqno gap. It always gives `lost_echo_cm` and never `clear_t`, or else `B ≤ ARM_CLEAN_S` makes it impossible.
   - **Long cycles.**
     - Start-up high, stuck high, a fall without an event, overflow while busy, a discard followed by busy.
     - No output while the module drives high.
     - No new `clear_t` for an existing cycle.
     - D5's fault-counter behaviour is preserved.
   - **Controller.**
     - Mixed valid and discarded batches.
     - Delayed delivery after the echo and after the deadline.
     - Stale buffered readings.
     - Release of `blocked`.
     - The integrated `Car` test at full speed with the edge implementation.
     - Checks on `clear_t`, `echo_cm`, `echo_t`, `dist_t`, motor state and the stopping deadline.
   - **Lifecycle.**
     - Selection (missing uAPI, EINVAL on arm, EBUSY).
     - `close()` during a wait.
     - A timed-out join.
     - No fd leak.
2. **ABI.** The C check matches the Python constants.
3. **On the car, stationary.**
   - **10 minutes on edges, under load.** Log per minute: total pings, every discard and diagnostic counter, `B`
     percentiles, and the raw event sequences of every anomalous ping. Expected: `late_arm` < 0.1 % (or the
     pull-up trigger makes it 0), `early_rise` = `short_pulse` = 0.
   - **Controlled source.** The GPIO6 pull-toggle test (spike 3) checks the timestamps and the short-pulse limit
     under the same load.
   - **Passive observer.** It runs beside the server and reports each mismatch with the server's readings (its own
     limits are recorded: polling at about 2 µs, one core).
   - **Server CPU.** Compared with polling mode.
   - **Restarts.** 20 stop/start cycles, all under 1 s, with coexisting rpi-lgpio lines (GPIO17/27) as the
     service user.
4. **On the car, with the owner.** Each motion step is announced and starts on "go":
   - A wall approach at 30/60/100 %: stops not later than polling D8.
   - An open-space drive: no `sensor_fault`, D7 trust as before, `multi` statistics collected.
5. **Fallback.**
   - `--us-timing poll` behaves as `913aed2`.
   - `auto` on a kernel without edge support falls back.
   - EBUSY makes the backend unavailable with a clear error.
