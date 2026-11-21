# Tuning the car's Raspberry Pi

The car runs Raspberry Pi OS (Debian 13) as installed by the Imager, with a full desktop. On a car nobody
looks at the desktop, and the extra services cost boot time, memory and background CPU (which the ultrasonic
timing feels). [`deploy/pi-tune.sh`](../deploy/pi-tune.sh) turns the Pi into a headless car computer.

```
scp deploy/pi-tune.sh carops@picar.local:/tmp/
ssh carops@picar.local sudo sh /tmp/pi-tune.sh --dry-run   # what would change
ssh carops@picar.local sudo sh /tmp/pi-tune.sh             # apply (idempotent), then reboot
ssh carops@picar.local sudo sh /tmp/pi-tune.sh --revert    # undo exactly what it changed, then reboot
```

Every change is recorded in `/var/lib/car-scratch/pi-tune.state`, so `--revert` restores only what this
script changed (a unit that was already disabled stays disabled). Backups of `config.txt` and `cmdline.txt`
are kept next to it.

## What it changes

| Area | Change |
|---|---|
| Boot target | console (`multi-user.target`) instead of the desktop; the desktop packages stay installed |
| System services off | lightdm, Bluetooth, CUPS printing, udisks2 (automount), accounts-daemon, NFS client (rpcbind, nfs-blkmap, nfs-client.target), NetworkManager-wait-online, the Pi 5 test units glamor-test and rp1-test, e2scrub_reap |
| Timers off | apt-daily, apt-daily-upgrade (no package updates in the background while driving), man-db |
| User services masked (all users) | pipewire, pipewire-pulse, wireplumber, filter-chain (audio), mpris-proxy, xdg-desktop-portal-rewrite-launchers, rpi-connect-wayvnc (Pi Connect screen sharing, which restarted every 5 s without a desktop) |
| cloud-init | off (`/etc/cloud/cloud-init.disabled`): its first-boot job is done; network settings put on the SD card are no longer applied at boot |
| `config.txt` | `disable_splash=1`, `boot_delay=0`, `camera_auto_detect=0`, `display_auto_detect=0`, `dtparam=audio=off`, `dtoverlay=disable-bt`, in a marked block |
| `cmdline.txt` | `splash` removed |
| Journal | capped at 64 MB on the SD card (`/etc/systemd/journald.conf.d/car-scratch.conf`) |

Kept on purpose: SSH, avahi (`picar.local`), NetworkManager and wpa_supplicant, cron, time sync, Raspberry Pi
Connect (its remote shell), log rotation, fstrim.

## Measured on the car (2026-11-21)

| | Before | After |
|---|---|---|
| Boot (`systemd-analyze`) | 42.0 s (5.5 s kernel + 36.5 s userspace) | 29.8 s (4.8 s + 25.0 s) |
| Boot target reached | 29.7 s (graphical) | 24.3 s (multi-user) |
| Running system services | 21 | 14 |
| Car user's services | 10 | 3 (car-scratch, dbus, rpi-connect) |
| Memory used | 371 MB | 182 MB |

## Next: NetworkManager's 15 s

What is left of the boot is mostly NetworkManager (15.4 s): with this OS's netplan integration it runs the
netplan generator four times during start-up, about 3.4 s each. Moving the WiFi profile from netplan to a
NetworkManager keyfile should save another 10-14 s. That rewrites the car's only network configuration, so
it waits until the WiFi hotspot fallback ([wifi.md](wifi.md)) is installed, which keeps the car reachable
even if the home WiFi profile breaks.
