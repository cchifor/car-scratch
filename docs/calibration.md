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
 "line_threshold": 300, "light_dark": 200, "light_bright": 20}
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
| `ultrasonic_offset_cm` | Distance from the car's pose point (the rear axle) to the ultrasonic sensor. | cm | Tape measure |
| `line_sensor_spacing_cm` | Distance between two neighbouring line sensors. | cm | Tape measure, centre to centre |
| `line_threshold` | A line sensor "sees the line" when its raw reading is **below** this value. | raw value | Line test (below) |
| `light_dark` | Raw light reading in darkness. | raw value, 0–255 | Cover the light sensor |
| `light_bright` | Raw light reading next to a bright lamp. | raw value, 0–255 | Point the sensor at a lamp at about 30 cm |

Notes:

- **User speed** runs from 0 to 100. The car turns it into motor power with
  `duty % = floor + (ceiling − floor) × speed / 100`, and sends `round(4095 × duty % / 100)` to the motor
  driver.
- For light, **a lower raw value means more light** on this sensor. The brightness shown in the monitor
  is `100 × (light_dark − raw) / (light_dark − light_bright)`, limited to 0–100.

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

### 6. Line sensors (after the line follower cable is fixed)

1. Measure the centre-to-centre distance between two neighbouring sensors.
2. Enter it in `line_sensor_spacing_cm`.
3. Hold the car over the **white floor** and note the `line.raw` value for each sensor from the monitor
   (or the sensor block). Write them down.
4. Put a sensor over the **black line** and note the raw value.
5. Set `line_threshold` halfway between the white and black readings. A sensor sees the line when its raw
   value is below the threshold.

Until the cable is fixed, the line sensor reports offline. Leave these fields at their defaults.

### 7. Light sensors

1. Cover all three light sensors with your hand or a dark card. Note the raw values. Set `light_dark`
   to the average.
2. Point the car at a lamp about 30 cm away, in a dim room, so the sensor reads its brightest
   value. Note the raw values. Set `light_bright` to the lowest (brightest) average.
3. Check the monitor: moving the lamp should move the brightness bars and the arrow to the brightest
   sensor.

## After you change the file

- Save the file, or use `calibrate` with `save` from a controller client.
- Restart the car service, or reconnect the browser, so the new values load.
- Repeat the Stage 2 floor test (in `docs/on-car-checklist.md`) to confirm the changes.
