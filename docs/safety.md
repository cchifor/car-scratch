# Car Scratch safety guide

This is for the person driving the car. Read it before the first run.

Car Scratch is a programming tool for a small robot car. The car can move and bump into things, so
it has several ways to stop. Nothing in your browser is needed for these to work: the safety rules run
on the car itself.

## What makes the car stop

| What happens | What the car does | What you do next |
|---|---|---|
| Something is in front of it, closer than the stop distance | Stops at once. Forward movement is blocked until the path clears. | Move the object, or drive backward or turn. The block lifts when the way is clear. |
| Emergency stop button (red, always on screen) | Stops at once and stays stopped. Every program stops too. | Fix the problem, then press **Reset** (only the car's controller can reset it). |
| Stop sign (Scratch's red stop button) | Stops all motors and ends the running programs. | Press the green flag to run again. |
| Green flag | Starts the programs again, and clears a pause. | Check the floor first. |
| Tab closed, tab hidden, iPad locked, app switched, Wi-Fi lost | Stops within about one second and **pauses**. The car will not move again by itself. | Come back to the page and press **Resume**. |
| Program stops responding | Stops and pauses (the watchdog). | Press **Resume** once the program is working again. |
| Battery switch | Cuts power to the motors. This is the last-resort stop. | Use it only when the software stop does not work. |

## Stop distance

- The default stop distance is **20 cm**. You can change it with the "set obstacle stop distance" block.
- The car stops earlier when it drives faster, because it keeps rolling after the motors stop. The car
  adds the coasting distance to your stop distance, so a fast car stops a lot further out.
- The car also stops when its sensor reads a fault (for example, it cannot get an echo three times in a
  row). A fault is treated as "something is there".
- The line follower (when enabled) is read while the car moves, under guards that switch the reads off at the
  first sign of trouble. A healthy read adds about 3 ms (up to about 10 ms with a retry and the light read) before
  the obstacle guard and before a loop-thread stop gets the bus lock. A stalled I2C bus blocks any motor write, up
  to the adapter timeout and the existing `hw_fault` and watchdog layers; the light sensor read has always
  carried the same risk. A slow call is never retried and switches the line reads off, so the line follower adds
  at most one stalled call per drive. See D4 in [decisions.md](decisions.md).
- You can switch the obstacle stop off with a block. While it is off, the screen shows a permanent
  warning and you are responsible for stopping the car.

## Things the sensor cannot see well

- **Walls hit at an angle may be detected late, or not at all.** Ultrasonic sound bounces away from a
  slanted wall. The car may read "far" even when the wall is close. Do not rely on the sensor for
  slanted walls, soft objects, or very thin objects such as chair legs.
- The sensor sees only what is in front of it. It does **not** see the back, the sides, the edges of a
  table, or stairs.
- The car does not know if it has been picked up or is wedged against something.

## Reversing

- Reverse is limited to **40%** speed.
- Reverse **stops after 5 seconds**, even if your program says to keep going. Start another move if you
  need more.
- There is **no sensor at the back**. Watch the car yourself when it moves backward.

## Keep hands and hair clear

- Keep fingers, cables, pets and loose clothing away from the wheels.
- The wheels can start turning at any time when a program runs.
- Do not hold the car while it is running.

## Test new programs safely

1. Lift the car so its wheels are off the floor. Put it on a box or a stand.
2. Run the program and check that the wheels turn the way the on-screen car moves.
3. Press the emergency stop once to check it works.
4. Only then put the car on the floor. Start with a clear area of about 2 m in every direction.
5. Start with a low speed (20–30%). Raise it only after a few safe runs.
6. Keep the owner nearby for the first runs on the floor.

## If something goes wrong

1. Press the red **emergency stop** button.
2. If the car does not stop, turn off the battery switch.
3. Lift the car and check the wheels and wiring before you run it again.
4. Tell the owner what happened, and what you were running.
