# Calibration guide

Calibration tells the car how strong its motors are, how far it travels at each speed, and where its
sensors are. The values live in `~/.config/car-scratch/calibration.json` on the car. Every field is
optional. If a field is missing, the car uses its built-in default.

Measure with a **tape measure**, on a flat floor, with the car's battery charged. Do the tests with the
owner present and with the clear 2 m area (see `docs/safety.md`).

## The file

```json
{"steer_trim_deg": 0.0,
 "duty_floor_pct": 25, "duty_ceiling_pct": 100,
 "speed_table": [[0,0],[25,0],[30,5],[40,15],[50,25],[60,35],[80,55],[100,70]],
 "coast_table": [[0,0],[50,3],[100,12]],
 "wheelbase_cm": 14.0, "ultrasonic_offset_cm": 12.0, "line_sensor_spacing_cm": 2.0,
 "line_black_high": true, "line_threshold": 320,
 "light_channels": [0, 1, 2], "light_dark": 200, "light_bright": 20}
```

The first time the server starts, it reads an old `~/picar/calibration.json` that only holds
`steer_trim_deg` and copies that value across.

## What each field means

| Field | Meaning | Unit / range | How to measure |
|---|---|---|---|
| `steer_trim_deg` | Offset added to the steering servo, so the car drives straight when you ask for straight. Positive turns the wheels toward the right. | degrees, −20 to +20 | Straight-line drift test (below) |
| `duty_floor_pct` | Lowest motor power that moves the car. Below this the motors only buzz. | % of full power | Slowly raise power until the wheels turn |
| `duty_ceiling_pct` | Highest motor power the car will use. | % of full power | Usually left at 100 |
| `speed_table` | For each motor power (duty %), how far the car moves per second. Used for dead reckoning and the stop distance. | pairs `[duty %, cm/s]` | 2-second run test (below) |
| `coast_table` | For each user speed, how far the car rolls after you stop the motors. | pairs `[user speed %, cm]` | Coast test (below) |
| `wheelbase_cm` | Distance between the front and rear axles. Used for turning geometry. | cm | Tape measure, axle centre to axle centre |
| `ultrasonic_offset_cm` | Distance from the car's pose point (the rear axle) to the ultrasonic sensor. **Reserved, not used yet:** the server draws and simulates the sensor at a fixed 22 cm (`ultrasonic_x` in `safety.geometry()`). | cm | Tape measure |
| `line_sensor_spacing_cm` | Distance between two neighbouring line sensors. | cm | Tape measure, centre to centre |
| `line_black_high` | `true` when a black line reads a HIGH value (measured on this car). | true or false | Line procedure (below) |
| `line_threshold` | The raw value that separates "line" from "ground". With `line_black_high`, a sensor sees the line when its raw value is **above** this value; otherwise when it is **below**. One number for all five sensors, or a list of 5 (leftmost first). | raw value, 0–1023 | Line procedure (below) |
| `light_channels` | Which PCF8591 input (0 to 3) each light sensor is wired to, as `[left, middle, right]`. This car: `[0, 2, 1]`. | 3 distinct integers | Light procedure (below) |
| `light_dark` | Raw light reading in the room (the "dark" end). One number, or a list of 3 (left, middle, right). | raw value, 0–255 | Light procedure (below) |
| `light_bright` | Raw light reading with a torch on the sensor. One number, or a list of 3. | raw value, 0–255 | Light procedure (below) |

Notes:

- **User speed** runs from 0 to 100. The car turns it into motor power with
  `duty % = floor + (ceiling − floor) × speed / 100`, and sends `round(4095 × duty % / 100)` to the motor
  driver.
- For light, **a lower raw value means more light** on this sensor. The brightness shown in the monitor
  is `100 × (light_dark − raw) / (light_dark − light_bright)`, limited to 0–100, using the values of that
  sensor. Telemetry and blocks always give left, middle, right, whatever the wiring.
- Old calibration files with one number for `light_dark`, `light_bright` and `line_threshold` still load.

## Measured on the sensors (2026-11-21)

- **Light (PCF8591 at I2C-1 0x48).** With a torch on one sensor at a time: left = input 0, middle = input 2,
  right = input 1; input 3 is not connected (it floats). Lower raw = brighter. Room light: left about 85–92,
  middle about 250, right about 252. Torch on the sensor: left about 3, middle about 19, right about 19. The
  sensors differ so much that one dark and one bright value for all three cannot work: use lists.
- **Line follower (I2C-1 0x11).** Five 16-bit values, sensor 1 is the leftmost. A read takes about 2.9 ms
  (3.0 ms at most over 40 reads) with no bus error. About 1 % of frames are all 0xFF (65535 in every slot);
  the server treats a frame with any value above 1023 as invalid and reads again. White or reflective close to
  the sensor reads LOW (about 170–300); black, or nothing in range, reads HIGH (about 420–425). With the module
  at its current mounting height white paper reads about 423, so it sees almost no difference; the module has
  to be lowered before the thresholds mean anything. Until then, expect the guided procedure to refuse.

## How to measure

Always write the result down in the table in `docs/on-car-checklist.md`, then copy the numbers into
`calibration.json`.

### 1. Steering trim (straight-line drift)

1. Put the car on a flat floor with a straight line marked on it, or use a long strip of tape.
2. Set steering to straight (`steer straight`).
3. Drive forward at **40%** for 1 m (use the timed drive block, about 4–5 s, then stop).
4. Measure how far the car has drifted sideways from the line at the 1 m mark.
5. If the car drifts **left**, the wheels point slightly left. Try `steer_trim_deg` = **+1.0**, and
   repeat.
6. If the car drifts **right**, try **−1.0**, and repeat.
7. Stop when the drift is under about 2 cm per metre. If the drift gets **worse** after a change, the
   sign is the wrong way round: reverse the trim.

### 2. Speed table (2-second runs)

1. Mark a start line on the floor. Leave at least 1 m of clear floor after it.
2. For each user speed you want to measure (try 30, 40, 50, 60, 80, 100), drive forward for exactly
   **2 seconds** from the start line.
3. Measure the distance from the start line to the front of the car. Divide by 2 to get cm/s.
4. Record the duty % for each speed: `floor + (ceiling − floor) × speed / 100`.
5. Enter `[duty %, cm/s]` pairs in `speed_table`. Keep the list sorted by duty %.
6. Run the test again in the other direction (backward), if you need reverse speeds. Reverse is limited
   to 40%.

### 3. Coast after stop

1. From the start line, drive forward at one user speed until the car reaches the mark.
2. Press **stop motors** (or the stop block) at the mark. Do not touch the car.
3. Measure how far the car rolled after the stop, from the mark to the front of the car.
4. Repeat each speed three times and take the average.
5. Enter `[user speed %, cm]` in `coast_table`. Add a point at 0 if you like.

The obstacle guard uses this table. A longer coast means the car needs more room to stop.

### 4. Wheelbase

1. With the car stopped and straight, measure from the centre of the front axle to the centre of the rear
   axle.
2. Enter it in `wheelbase_cm`.
3. Check: at full steering, the turning radius should be about `wheelbase / tan(steer angle)`. Measure
   the radius once for a rough check. This is for your results table, not a calibration field.

### 5. Ultrasonic offset

1. Measure from the rear axle centre to the front of the ultrasonic sensor.
2. Enter it in `ultrasonic_offset_cm`.

The server does not read this field yet: the geometry it sends to the editor and uses in the simulator has a
fixed `ultrasonic_x` of 22 cm (`safety.geometry()` in `server/picar_server/safety.py`). Record the value anyway,
so it is ready when the field is wired in.

### 6. Line sensors

The line follower must be enabled (`"line_enabled": true` in calibration.json, then restart the service) and
mounted low enough that white paper and a black line read clearly different values.

1. Measure the centre-to-centre distance between two neighbouring sensors and enter it in
   `line_sensor_spacing_cm`.
2. Guided, from a laptop that can reach the car (no motion is ever sent; the car stays where you put it):
   `python3 -m tools.calib --url ws://picar.local:8080/ws line`
   - Put the car on plain white paper at driving height; Enter. It takes the median of each sensor over about
     2 seconds.
   - Slide a black line slowly under all five sensors by hand, then press Enter. It records each sensor's
     extreme reading.
   - It works out the polarity (`line_black_high`) and a threshold per sensor, halfway between white and
     black, prints them and saves them on the car at once.
   - If any sensor's white and black readings differ by less than 40 counts it refuses to save and prints
     "sensor too far from the ground or IR LEDs not powered". Lower the module or check its supply and retry.
3. By hand instead: note the `line.raw` values on white and on black, set `line_black_high` and put
   `line_threshold` halfway (one number, or a list of 5), then restart or use `calibrate`.

### 7. Light sensors

Guided: `python3 -m tools.calib --url ws://picar.local:8080/ws light` (no motion is ever sent).

1. Room light as normal, nothing shining on the sensors; Enter. It takes about 2 seconds of readings.
2. Hold a torch close on the LEFT sensor, Enter; then the MIDDLE sensor; then the RIGHT sensor. For each it
   notes which input dropped.
3. It works out `light_channels` (which input is which sensor), `light_dark` (the room reading of each sensor)
   and `light_bright` (the torch reading of each sensor), prints them and saves them on the car at once. It
   refuses to save if a torch changed no input by at least 30, or if two sensors map to the same input.

If the procedure stops without saving (a refusal, Ctrl-C, a lost connection), it puts the car's previous
`light_channels` back.

The procedure assumes the three connected sensors are on inputs 0 to 2, as on this car (input 3 is not
connected and cannot be a sensor). Afterwards the monitor should show the brightness bar of the sensor you
light, and the arrow should point to it.

## After you change the file

- Save the file, or use `calibrate` with `save` from a controller client.
- Restart the car service so the new values load: `systemctl --user restart car-scratch` on the car, or a
  deploy (which restarts it). The server reads `calibration.json` only once, when it starts; reconnecting the
  browser does not reload it. The exception is `calibrate`: `steer_trim_deg`, `light_channels`, `light_dark`, `light_bright`,
  `line_black_high` and `line_threshold` apply at once (with `save` they are also written to the file;
  `line_enabled` is not part of `calibrate`).
- Repeat the Stage 2 floor test (in `docs/on-car-checklist.md`) to confirm the changes.

## Measured on the car (2026-11-21, fresh batteries)

Floor runs with `wsclient move` (obstacle stop on), measured with a tape from the front bumper:

| Run | Result |
|---|---|
| 40 % for 1.5 s | 13 cm, slight drift right |
| 60 % for 1.5 s | 19 cm, straight |
| 80 % for 1.0 s | 16 cm, slight drift right |
| 100 % for 1.0 s | 19.5 cm, slight drift right |
| 60 % for 3 s (trim −2°) | 40 cm, 10° right |
| 60 % for 3 s (trim −4°) | 42 cm, 5° left |
| full left, 60 %, ~1 s (stopped by the guard) | 45° and 60° left, bumper 14 cm from the start |

The two 60 % runs give a start-up lag of about 0.2 s and a steady 14.7 cm/s; the table holds steady speeds
(the 1 s runs corrected by the 0.2 s lag). Saved in `~/.config/car-scratch/calibration.json` on the car:

- `speed_table`: `[[0,0],[25,0],[55,10.0],[70,14.7],[85,20.0],[100,24.4]]` (duty % → cm/s)
- `wheelbase_cm`: 7 (effective: the wheels turn more than the commanded angle, so the car turns twice as
  sharply as the 14 cm placeholder predicted)
- `steer_trim_deg`: −3.3 (interpolated between the −2° and −4° checks)
- stop distances (`coast_table`) were not measured, so `calibrated` stays `false` and the banner stays.

This car is slow (about 25 cm/s flat out), so at 40 % it covers only ~10 cm per second.
