#!/bin/sh
# Tune the car's Raspberry Pi for its job: headless, fewer services, faster boot, quieter background.
# Run on the Pi as root (idempotent; prints what it changed):
#     sudo sh pi-tune.sh              apply
#     sudo sh pi-tune.sh --dry-run    show what would change, change nothing
#     sudo sh pi-tune.sh --revert     undo exactly what earlier runs changed (they are recorded)
# Takes effect fully after a reboot. Kept on purpose: SSH, avahi (picar.local), NetworkManager, cron, time sync,
# Raspberry Pi Connect (remote shell), log rotation, fstrim. Desktop packages stay installed (revert = desktop back).
set -eu

MODE=apply
case "${1:-}" in
    --dry-run) MODE=dry ;;
    --revert) MODE=revert ;;
    "") ;;
    *) echo "usage: $0 [--dry-run|--revert]" >&2; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

BOOT=${BOOT_DIR:-/boot/firmware}
CONFIG="$BOOT/config.txt"
CMDLINE="$BOOT/cmdline.txt"
STATE_DIR=/var/lib/car-scratch
STATE="$STATE_DIR/pi-tune.state"            # one line per change, for --revert
CLOUD_FLAG=/etc/cloud/cloud-init.disabled
JOURNALD=/etc/systemd/journald.conf.d/car-scratch.conf
BEGIN="# >>> car-scratch pi-tune (sudo sh pi-tune.sh --revert removes this block)"
END="# <<< car-scratch pi-tune"

SERVICES_OFF="lightdm.service bluetooth.service cups.service cups.socket cups.path udisks2.service \
accounts-daemon.service rpcbind.service rpcbind.socket nfs-blkmap.service nfs-client.target \
NetworkManager-wait-online.service glamor-test.service rp1-test.service e2scrub_reap.service"
TIMERS_OFF="apt-daily.timer apt-daily-upgrade.timer man-db.timer"
USER_MASK="pipewire.service pipewire.socket pipewire-pulse.service pipewire-pulse.socket wireplumber.service \
filter-chain.service mpris-proxy.service rpi-connect-wayvnc.service xdg-desktop-portal-rewrite-launchers.service"

CHANGED=""
note() { CHANGED="${CHANGED}  - $1
"; }
record() { [ "$MODE" = apply ] && echo "$1" >> "$STATE"; return 0; }
do_() { if [ "$MODE" = dry ]; then echo "  would: $*"; else "$@"; fi; }

exists() { [ -n "$(systemctl list-unit-files --no-legend "$1" 2>/dev/null)" ]; }
user_unit_exists() { [ -e "/usr/lib/systemd/user/$1" ] || [ -e "/lib/systemd/user/$1" ]; }

# ---------------------------------------------------------------------------------------------------- revert
if [ "$MODE" = revert ]; then
    [ -f "$STATE" ] || { echo "nothing to revert ($STATE not found)"; exit 0; }
    tac "$STATE" | while read -r kind arg; do
        case "$kind" in
            enable) systemctl enable "$arg" >/dev/null 2>&1 && echo "  - enabled $arg again" ;;
            user-unmask) [ "$(readlink "/etc/systemd/user/$arg" 2>/dev/null)" = /dev/null ] \
                && rm -f "/etc/systemd/user/$arg" && echo "  - unmasked user unit $arg" ;;
            default) systemctl set-default "$arg" >/dev/null && echo "  - default target $arg again" ;;
            cloud-init) rm -f "$CLOUD_FLAG" && echo "  - cloud-init enabled again" ;;
            config) sed -i "\|^$BEGIN\$|,\|^$END\$|d" "$CONFIG" && echo "  - removed the tuning block from $CONFIG" ;;
            cmdline) [ -f "$arg" ] && cp "$arg" "$CMDLINE" && echo "  - restored $CMDLINE from $arg" ;;
            journald) rm -f "$JOURNALD" && echo "  - removed $JOURNALD" ;;
        esac
    done
    rm -f "$STATE"
    systemctl daemon-reload
    echo "reverted; reboot to finish"
    exit 0
fi

[ "$MODE" = apply ] && mkdir -p "$STATE_DIR"

# ---------------------------------------------------------------------------------------------- 1. boot target
cur=$(systemctl get-default)
if [ "$cur" != multi-user.target ]; then
    do_ systemctl set-default multi-user.target >/dev/null
    record "default $cur"
    note "boot to the console (multi-user.target) instead of the desktop ($cur)"
fi

# ------------------------------------------------------------------------------------ 2. system services/timers
for u in $SERVICES_OFF $TIMERS_OFF; do
    exists "$u" || continue
    state=$(systemctl is-enabled "$u" 2>/dev/null || true)
    case "$state" in
        enabled|enabled-runtime)
            do_ systemctl disable "$u" >/dev/null 2>&1
            record "enable $u"
            note "disabled $u" ;;
    esac
done

# ------------------------------------------------------------------- 3. desktop-only user services, all users
for u in $USER_MASK; do
    user_unit_exists "$u" || continue
    [ "$(readlink "/etc/systemd/user/$u" 2>/dev/null)" = /dev/null ] && continue
    [ -e "/etc/systemd/user/$u" ] && continue          # an admin's own override: leave it alone
    do_ systemctl --global mask "$u" >/dev/null 2>&1
    record "user-unmask $u"
    note "masked user unit $u (all users)"
done

# ------------------------------------------------------------------------------------------- 4. cloud-init
if [ -d /etc/cloud ] && [ ! -e "$CLOUD_FLAG" ]; then
    do_ touch "$CLOUD_FLAG"
    record "cloud-init"
    note "cloud-init off ($CLOUD_FLAG); network settings on the SD card are no longer applied at boot"
fi

# ---------------------------------------------------------------------------------------- 5. config.txt
if [ -f "$CONFIG" ] && ! grep -qxF "$BEGIN" "$CONFIG"; then
    if [ "$MODE" = dry ]; then
        echo "  would: append a tuning block to $CONFIG"
    else
        cp "$CONFIG" "$STATE_DIR/config.txt.before-pi-tune"
        cat >> "$CONFIG" <<CONF

$BEGIN
[all]
disable_splash=1
boot_delay=0
camera_auto_detect=0
display_auto_detect=0
dtparam=audio=off
dtoverlay=disable-bt
$END
CONF
    fi
    record "config"
    note "$CONFIG: no splash, no boot delay, no camera/display probing, audio and Bluetooth off"
fi

# ---------------------------------------------------------------------------------------- 6. cmdline.txt
if [ -f "$CMDLINE" ] && grep -qw splash "$CMDLINE"; then
    if [ "$MODE" = dry ]; then
        echo "  would: remove 'splash' from $CMDLINE"
    else
        backup="$STATE_DIR/cmdline.txt.before-pi-tune"
        cp "$CMDLINE" "$backup"
        new=$(sed -e 's/ splash\b//' -e 's/^splash //' "$CMDLINE")
        # refuse anything that could stop the Pi from booting: one line, root= still there
        if [ "$(printf '%s\n' "$new" | wc -l)" -eq 1 ] && printf '%s' "$new" | grep -q 'root='; then
            printf '%s\n' "$new" > "$CMDLINE"
            record "cmdline $backup"
            note "$CMDLINE: no boot splash (backup $backup)"
        else
            echo "WARNING: left $CMDLINE unchanged (unexpected content)" >&2
        fi
    fi
fi

# ------------------------------------------------------------------------------------------ 7. journald
if [ ! -f "$JOURNALD" ]; then
    if [ "$MODE" = dry ]; then
        echo "  would: write $JOURNALD (SystemMaxUse=64M)"
    else
        mkdir -p "$(dirname "$JOURNALD")"
        printf '[Journal]\nSystemMaxUse=64M\nRuntimeMaxUse=32M\n' > "$JOURNALD"
    fi
    record "journald"
    note "journal capped at 64 MB on the SD card"
fi

[ "$MODE" = apply ] && systemctl daemon-reload

echo
if [ -n "$CHANGED" ]; then
    [ "$MODE" = dry ] && echo "Would change:" || echo "Changed (reboot to finish):"
    printf '%s' "$CHANGED"
else
    echo "Nothing to change; already tuned."
fi
