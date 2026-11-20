# On-car test checklist

Staged test plan for the first real run of Car Scratch on the PiCar-S. Based on `docs/plan.md` §12
(item 4). Work through the stages in order. Do not skip a stage. Tick each box as you go and write
results in the tables at the end.

The owner must be present for every stage that moves the car.

## Before you start

- [ ] Battery charged, and the battery switch is **off** while you set up the wiring.
- [ ] Car on a box or stand for the wheels-up stages (wheels clear of the floor).
- [ ] Clear floor, at least **2 m** in every direction, with no cables or pets nearby.
- [ ] Owner is present and has the **emergency stop** in reach.
- [ ] `deploy/pi-setup.sh` has been run **once** on the Pi with `sudo` (owner enters the password).
- [ ] The car is reachable at `http://picar.local:8080`. If not, the owner has the IP address ready.
- [ ] The build from the agreed commit is deployed (`deploy/deploy.sh`). The status page shows the same
      commit hash.
- [ ] The line follower cable is either fixed or unplugged, so it cannot disturb the I2C bus.
- [ ] Owner has shown you where the **battery switch** is, and it is switched on for the tests.

## Stage 0: dry run (no motors)

Goal: the sensors and the car monitor work, and nothing moves.

- [ ] Car is on the bench or box. Motors are not connected to the power yet, or are disabled.
- [ ] Server starts in **dryrun** mode. The welcome message shows the backend as `dryrun`.
- [ ] Distance reading changes when you move a hand in front of the sensor.
- [ ] Distance reads `far` when nothing is in range.
- [ ] Car monitor shows the sensor cone and it changes colour when something is close.
- [ ] Light readings change when you cover and uncover the light sensors.
- [ ] Line sensor shows **offline** (expected while the cable is not fixed).
- [ ] Driving blocks send commands and the monitor shows the car moving. No wheel moves.

## Stage 1: wheels up

Goal: the directions match the animation, and every stop works. The car is on a box, wheels off the
floor.

- [ ] Forward drive turns the wheels forward. The monitor's arrow points forward.
- [ ] Backward drive turns the wheels backward. The monitor shows reverse.
- [ ] Steering left and right: the front wheels turn the right way, and the monitor matches.
- [ ] **Emergency stop** stops the wheels within 1 s, while a program is running.
- [ ] **Stop sign** stops the wheels within 200 ms.
- [ ] Closing the tab stops the wheels within 1 s.
- [ ] Turning on airplane mode on the device stops the wheels within 1 s.
- [ ] Locking the screen (iPad or phone) stops the wheels within 1 s. Resume works after unlocking.
- [ ] Killing the server process (`kill -9`) stops the wheels (the post-stop hook runs `safe_stop`).
- [ ] Pausing the server process (`SIGSTOP`) causes the watchdog to stop the car within about 1 s.
      Restart the service afterwards.
- [ ] Reverse stops by itself after 5 s.
- [ ] Battery switch cuts the motors (test once, with the owner).

## Stage 2: floor

Goal: real stopping distances, speed table, turning radius, and the oblique-wall behaviour. Owner
holds the car's battery switch within reach. The car starts 2 m or more from the wall.

- [ ] Obstacle stop at **30%** speed: stopping distance recorded (table below).
- [ ] Obstacle stop at **50%** speed: recorded.
- [ ] Obstacle stop at **80%** speed: recorded.
- [ ] Obstacle stop at **100%** speed: recorded. Stop at the wall without contact, or with light contact
      only.
- [ ] Speed table filled in, using 2-second runs (see `docs/calibration.md`).
- [ ] Coast distance recorded at each speed.
- [ ] Turning radius measured at full steering to each side.
- [ ] Oblique wall (about 45°) tested. Record whether the car stopped in time, and where.
- [ ] Steering trim checked on a straight run (see `docs/calibration.md`).

## Stage 3: example programs

Goal: the examples behave as intended on the real floor.

- [ ] Example **02 Touch remote**: each button moves the car the right way, and ■ stops it.
- [ ] Example **04 Obstacle stop**: stops before the wall at every speed you tested.
- [ ] Example **05 Obstacle avoider**: backs up, steers away, and continues.
- [ ] Example **08 Light follower**: steers toward the lamp and stops when it is dark.

## Stage 4: real iPad

Goal: Car Scratch works on the actual iPad, in Safari and as a Home Screen app.

Test in **Safari** and **Chrome** (both WebKit on iPadOS), in the browser and as a Home Screen app.

- [ ] Opens at `http://picar.local:8080`, landscape, with no sideways scrolling.
- [ ] Blocks can be dragged with a finger.
- [ ] Touch remote works. Buttons respond to taps.
- [ ] E-STOP works from the menu bar and from the full-screen stage.
- [ ] Locking the iPad stops the car. Resume works after unlocking.
- [ ] Car reconnects after switching apps.
- [ ] Save to car and Open from car both work.
- [ ] Sound plays after the first tap.
- [ ] Portrait shows the "rotate your iPad" hint.
- [ ] Home Screen icon and full-screen mode work.

## Stage 5: controller and viewer

Goal: two devices can share the car safely.

- [ ] Device A takes control and drives.
- [ ] Device B opens the page and becomes a viewer. It sees the monitor but cannot drive.
- [ ] Device B's E-STOP stops the car, and Device A sees it.
- [ ] Device B's "Take control" with force stops the car first, then gives control to Device B.
- [ ] Device A is shown as a viewer after the takeover.
- [ ] Closing Device A's tab stops the car. Device A can reclaim control within 30 s.

## Results

### Stop distance by speed

| Speed (%) | Trial 1 (cm) | Trial 2 (cm) | Trial 3 (cm) | Average (cm) | Notes |
|---|---|---|---|---|---|
| 30 | | | | | |
| 50 | | | | | |
| 80 | | | | | |
| 100 | | | | | |

### Speed table (2-second runs)

| Speed (%) | Duty (%) | Distance in 2 s (cm) | cm/s | Notes |
|---|---|---|---|---|
| 30 | | | | |
| 50 | | | | |
| 80 | | | | |
| 100 | | | | |

### Coast after stop

| Speed (%) | Coast (cm) | Notes |
|---|---|---|
| 30 | | |
| 50 | | |
| 80 | | |
| 100 | | |

### Turning radius and trim

| Test | Result | Notes |
|---|---|---|
| Full left turn, radius (cm) | | |
| Full right turn, radius (cm) | | |
| Straight-line drift over 1 m (cm, left or right) | | |
| Steering trim value after correction (deg) | | |

### Sign-off

- Tested by: ____________________   Date: ____________
- Owner present: [ ] yes
- Results copied into `calibration.json` (see `docs/calibration.md`): [ ] yes
