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
for p in python3-aiohttp python3-smbus2 rsync; do
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
write_file() {   # write_file <path> ; content on stdin ; returns 0 when the file changed
    tmp=$(mktemp)
    cat > "$tmp"
    if [ -f "$1" ] && cmp -s "$tmp" "$1"; then rm -f "$tmp"; return 1; fi
    mkdir -p "$(dirname "$1")"
    install -m 644 "$tmp" "$1"
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

# 6. boot-time motor zeroing (system unit, runs as root before the user service exists)
if write_file /etc/systemd/system/car-safe-stop.service <<CONF
[Unit]
Description=Zero the Car Scratch rear motors at boot
After=local-fs.target dev-i2c\x2d1.device

[Service]
Type=oneshot
Environment=PYTHONPATH=$HOME_DIR/car-scratch/current/server
ExecStart=-/usr/bin/python3 -m picar_server.safe_stop

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
