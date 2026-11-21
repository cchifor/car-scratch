# Connecting to the car without a router

The car normally joins your home WiFi. When it cannot (you took it to the park, the router is off), it
starts its own WiFi network, a **hotspot**, and you connect to that directly.

## What it is and when it starts

- Network name (SSID): `PiCar-XXXX`, where XXXX is the last four characters of the Pi's WiFi MAC address.
- Security: WPA2 with a random 12-character passphrase.
- Address of the car on the hotspot: `10.42.0.1`. Connected devices get addresses automatically.
- It starts only in **auto** mode (the default), and only if the car finds no known WiFi network within
  about 45 seconds of booting. If the home network is there, the hotspot never starts.
- While the hotspot is on and nobody is connected to it, the car checks every 2 minutes whether a known
  network has come back in range. If so it leaves the hotspot and rejoins it. It never switches while
  someone is connected to the hotspot.
- If the car is on the home network and that network later disappears, the car gives NetworkManager about
  2 to 4 minutes (two consecutive checks, 2 minutes apart) to reconnect, then starts the hotspot. A failed hotspot start is
  retried at every check.

## Connect from an iPad or a laptop

1. Read the passphrase (once): `ssh carops@<car> sudo car-wifi password`. Over the hotspot itself you can
   use `ssh carops@10.42.0.1`. Write it down.
2. Join the `PiCar-XXXX` network with that passphrase.
3. Open `http://10.42.0.1:8080` (or `http://picar.local:8080` if mDNS works on your device).

iPadOS may say "No Internet Connection" for this network. That is expected, the hotspot has no
internet. Stay connected. Devices connected to the hotspot cannot reach the internet, and the iPad will
use cellular for other apps if it has it.

## Modes and commands

Run on the car as root (`sudo car-wifi ...`):

| Command | Effect |
|---|---|
| `status` | mode, active connection, SSID, IPv4, number of hotspot clients |
| `auto` | the default: home WiFi, hotspot as fallback (persisted) |
| `home` | home WiFi only, never the hotspot (persisted) |
| `hotspot` | always the hotspot (persisted) |
| `test-hotspot MINUTES` | hotspot now, back to the saved mode after MINUTES; nothing is persisted |
| `password` | show the passphrase |
| `password --prompt` | set a new passphrase, typed twice without echo (8 to 63 printable ASCII characters) |
| `password --stdin` | set a new passphrase read from one line of standard input (never pass it as an argument: it would land in shell history and sudo logs) |

`home` mode has **no fallback**. If the home network disappears, the car is unreachable until you
power-cycle it near a known network or switch the mode with a console. Use `auto` unless you have a reason.

Decisions are logged: `journalctl -t car-wifi`.

## Install

```
scp deploy/wifi-setup.sh carops@<car>:/tmp/
ssh carops@<car> sudo sh /tmp/wifi-setup.sh
```

It is safe to run again; it prints what it did and keeps the existing passphrase and mode. It does not
switch networks, and it never changes the home WiFi profile. To remove everything it installed:
`sudo sh /tmp/wifi-setup.sh --uninstall`.

## Test it safely over SSH

```
ssh carops@<car> sudo car-wifi test-hotspot 10
```

The return is scheduled first, then the car switches to the hotspot and your SSH session drops. Join
`PiCar-XXXX` and check the app. During the test the car does not leave the hotspot on its own. After
10 minutes it goes back to the saved mode (home WiFi in `auto` mode) and your devices can rejoin the home
network. If anything goes wrong before then, power-cycling the car also clears the test.

## Recovery

- Auto mode: power-cycle the car near the router (or any known network). It rejoins within a minute.
- No home network available: join the hotspot and `ssh carops@10.42.0.1`, then `sudo car-wifi status`.
- The hotspot is the problem: `sudo car-wifi home` (while on the hotspot you will lose the connection),
  or `sudo sh wifi-setup.sh --uninstall`.

## Limits

- 2.4 GHz only (channel 6), WPA2.
- Range is roughly 10 to 30 m, depending on obstacles. The Pi's antenna is small.
- No internet for connected devices.
- The Pi has one radio: the home network and the hotspot are never both active. Joining the hotspot
  means the car is off the home network until it switches back.
- Scanning for the home network briefly disturbs the hotspot, which is why the car only does it when
  nobody is connected.
