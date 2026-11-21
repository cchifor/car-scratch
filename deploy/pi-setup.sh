#!/bin/sh
# One-time Pi setup for Car Scratch. Run on the Pi as root:  sudo sh pi-setup.sh   (idempotent; prints what it changed)
set -eu

CAR_USER=${CAR_USER:-c4}
CHANGED=""
note() { CHANGED="${CHANGED}  - $1
"; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
id "$CAR_USER" >/dev/null 2>&1 || { echo "user $CAR_USER does not exist" >&2; exit 1; }
HOME_DIR=$(getent passwd "$CAR_USER" | cut -d: -f6)

# 1. packages (the server runs on apt packages only)
MISSING=""
for p in python3-aiohttp python3-rich python3-smbus2 rsync; do
    dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"
done
if [ -n "$MISSING" ]; then
    apt-get update -qq
    # shellcheck disable=SC2086
    apt-get install -y --no-install-recommends $MISSING
    note "installed apt packages:$MISSING"
fi
# RPi.GPIO comes from rpi-lgpio on the Pi; the real backend needs it.
if ! python3 -c 'import RPi.GPIO' >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends python3-rpi-lgpio
    note "installed python3-rpi-lgpio"
    python3 -c 'import RPi.GPIO' >/dev/null 2>&1 || echo "WARNING: RPi.GPIO still not importable" >&2
fi

# 2. user services keep running without a login session
if [ "$(loginctl show-user "$CAR_USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]; then
    loginctl enable-linger "$CAR_USER"
    note "enabled linger for $CAR_USER (user service survives logout and starts at boot)"
fi

# 3. WiFi power-save off: its latency spikes would trip the 750 ms watchdog.
#    Applied to the saved connection (and live via iw); the connection is NOT restarted, so SSH stays up.
if command -v nmcli >/dev/null 2>&1; then
    WIFI=$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2 ~ /wireless/ {print $1; exit}')
    if [ -n "$WIFI" ]; then
        if [ "$(nmcli -g 802-11-wireless.powersave connection show "$WIFI")" != "2" ]; then
            nmcli connection modify "$WIFI" 802-11-wireless.powersave 2
            note "WiFi power-save disabled in NetworkManager connection '$WIFI' (fully effective after reconnect/reboot)"
        fi
        if command -v iw >/dev/null 2>&1; then
            IFACE=$(iw dev | awk '$1 == "Interface" {print $2; exit}')
            [ -n "$IFACE" ] && iw dev "$IFACE" set power_save off 2>/dev/null && note "iw: power_save off on $IFACE (live)"
        fi
    else
        echo "no active WiFi connection found; skipped power-save"
    fi
else
    echo "WARNING: nmcli not found; WiFi power-save not changed" >&2
fi

# 4. directories
for d in car-scratch/releases .config/car-scratch .config/systemd/user .local/share/car-scratch; do
    if [ ! -d "$HOME_DIR/$d" ]; then
        install -d -o "$CAR_USER" -g "$CAR_USER" "$HOME_DIR/$d"
        note "created $HOME_DIR/$d"
    fi
done
# parents created by install -d above are root-owned only if they did not exist: fix ownership
chown "$CAR_USER:$CAR_USER" "$HOME_DIR/.config" "$HOME_DIR/.config/systemd" "$HOME_DIR/.local" "$HOME_DIR/.local/share" \
    "$HOME_DIR/car-scratch" 2>/dev/null || true

# 5. hardware watchdog: the SoC reboots the Pi if systemd itself hangs (motors are zeroed again on boot, see 6)
write_file() {   # write_file <path> [mode] ; content on stdin ; returns 0 when the file changed
    tmp=$(mktemp)
    cat > "$tmp"
    if [ -f "$1" ] && cmp -s "$tmp" "$1"; then rm -f "$tmp"; return 1; fi
    mkdir -p "$(dirname "$1")"
    install -m "${2:-644}" "$tmp" "$1"
    rm -f "$tmp"
}
if write_file /etc/systemd/system.conf.d/car-scratch-watchdog.conf <<'CONF'
[Manager]
RuntimeWatchdogSec=10
CONF
then
    systemctl daemon-reexec
    note "hardware watchdog on: /etc/systemd/system.conf.d/car-scratch-watchdog.conf (RuntimeWatchdogSec=10)"
fi

# 6. boot-time motor zeroing (system unit, runs as root before the user service exists).
#    Root must not run Python from a user-writable tree: safe_stop.py (stdlib + smbus2, standalone) is copied to a
#    root-owned directory, and a root-owned wrapper waits (up to ~10 s) for /dev/i2c-1 before running it.
#    Re-run this script after a deploy that changes safe_stop.py to refresh the copy.
LIBDIR=/usr/local/lib/car-scratch
SRC_STOP="$HOME_DIR/car-scratch/current/server/picar_server/safe_stop.py"
install -d -m 0755 -o root -g root "$LIBDIR"
if [ -f "$SRC_STOP" ]; then
    if ! cmp -s "$SRC_STOP" "$LIBDIR/safe_stop.py" 2>/dev/null; then
        install -m 0644 -o root -g root "$SRC_STOP" "$LIBDIR/safe_stop.py"
        note "installed $LIBDIR/safe_stop.py (root-owned)"
    fi
elif [ ! -f "$LIBDIR/safe_stop.py" ]; then
    echo "WARNING: no release deployed yet; re-run pi-setup.sh after the first deploy to install safe_stop.py" >&2
fi
if write_file "$LIBDIR/safe-stop-wait.sh" 0755 <<'CONF'
#!/bin/sh
# wait up to ~10 s for the I2C bus device, then zero the rear motors; never fails the boot
i=0
while [ ! -e /dev/i2c-1 ] && [ "$i" -lt 20 ]; do sleep 0.5; i=$((i + 1)); done
[ -f /usr/local/lib/car-scratch/safe_stop.py ] || { echo "safe_stop.py not installed"; exit 0; }
exec /usr/bin/python3 /usr/local/lib/car-scratch/safe_stop.py
CONF
then
    note "installed $LIBDIR/safe-stop-wait.sh"
fi
if write_file /etc/systemd/system/car-safe-stop.service <<CONF
[Unit]
Description=Zero the Car Scratch rear motors at boot
After=local-fs.target

[Service]
Type=oneshot
ExecStart=-$LIBDIR/safe-stop-wait.sh

[Install]
WantedBy=multi-user.target
CONF
then
    systemctl daemon-reload
    note "installed /etc/systemd/system/car-safe-stop.service"
fi
if ! systemctl is-enabled car-safe-stop.service >/dev/null 2>&1; then
    systemctl enable car-safe-stop.service >/dev/null 2>&1
    note "enabled car-safe-stop.service"
fi

# 6b. `carctl` (terminal dashboard + keyboard driving, docs/carctl.md): a root-owned launcher that runs the
#     deployed copy as the calling user. ssh -t c4@picar.local carctl
if write_file /usr/local/bin/carctl 0755 <<CONF
#!/bin/sh
PYTHONPATH=$HOME_DIR/car-scratch/current/server exec python3 -m tools.carctl "\$@"
CONF
then
    note "installed /usr/local/bin/carctl (root-owned)"
fi

# 7. hardware access groups (informational)
for g in gpio i2c; do
    id -nG "$CAR_USER" | tr ' ' '\n' | grep -qx "$g" || echo "WARNING: $CAR_USER is not in group $g"
done

echo
if [ -n "$CHANGED" ]; then
    echo "Changed:"
    printf '%s' "$CHANGED"
else
    echo "Nothing to change; already set up."
fi
