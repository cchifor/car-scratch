#!/bin/sh
# Remove the desktop from the car's Pi: X/Wayland, the desktop session, browsers and desktop apps, printing,
# Bluetooth, audio and the boot splash. Run pi-tune.sh first (it already switches them off).
#     sudo sh pi-slim.sh --dry-run    list what apt would remove; change nothing
#     sudo sh pi-slim.sh              remove it
# Guard: everything the car needs is marked "manually installed" first (several of those packages are only
# installed as desktop dependencies, e.g. openssh-server, python3, iw, dnsmasq-base, netplan.io), then the purge is
# simulated, and the script refuses to go on if anything the car needs would be removed.
# Raspberry Pi Connect is switched to rpi-connect-lite (remote shell only; screen sharing needs the desktop) when
# the package servers can be reached; otherwise Pi Connect is left as it is.
# Not undone by a flag: putting the desktop back needs the internet, e.g.
#     sudo apt install rpd-wayland-core rpd-x-core rpd-preferences rpd-applications rpd-utilities
set -eu

DRY=0
case "${1:-}" in
    --dry-run) DRY=1 ;;
    "") ;;
    *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

# What the car needs. Never removed; marked manual so autoremove cannot take them.
KEEP="openssh-server openssh-client sudo dbus systemd systemd-timesyncd cron ca-certificates curl \
network-manager wpasupplicant netplan.io iw dnsmasq-base avahi-daemon libnss-mdns \
python3 python3-aiohttp python3-smbus2 python3-smbus python3-rpi-lgpio python3-rich rsync i2c-tools \
raspi-utils-core raspi-config raspberrypi-sys-mods raspi-firmware firmware-brcm80211 linux-image-rpi-v8 \
initramfs-tools rpi-connect-lite"

# Top-level desktop packages; apt's --autoremove takes their now-unused dependencies along.
PURGE="raspberrypi-ui-mods lightdm lightdm-gtk-greeter labwc wayfire xwayland xserver-xorg-core x11-common \
chromium chromium-common chromium-l10n firefox geany thonny vlc vlc-bin vlc-data vlc-l10n squeekboard \
realvnc-vnc-server lxpanel-pi lxsession lxsession-data lxsession-logout lxtask lxterminal lxpolkit lxmenu-data \
pcmanfm cups cups-browsed cups-client cups-common cups-daemon cups-filters cups-core-drivers \
cups-filters-core-drivers cups-ipp-utils cups-pk-helper cups-ppdc cups-server-common bluez bluez-firmware \
pipewire pipewire-bin pipewire-pulse pipewire-libcamera wireplumber plymouth plymouth-label plymouth-themes \
wayvnc"

installed() { dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null | grep -q '^ii'; }

# rpd-* meta packages, whatever this image has
RPD=$(dpkg-query -W -f='${Package} ${db:Status-Abbrev}\n' 'rpd-*' 2>/dev/null | awk '$2 == "ii" {print $1}')

# Pi Connect: swap the full client (needs wayvnc and the desktop) for the lite one, if it can be installed
SWAP_CONNECT=0
if installed rpi-connect && ! installed rpi-connect-lite; then
    if apt-get -s install rpi-connect-lite >/dev/null 2>&1 && \
       apt-get -q download --print-uris rpi-connect-lite 2>/dev/null | grep -q "^'http"; then
        SWAP_CONNECT=1
    else
        echo "note: rpi-connect-lite cannot be fetched; Raspberry Pi Connect and wayvnc are kept"
        PURGE=$(echo "$PURGE" | sed 's/\bwayvnc\b//')
        KEEP="$KEEP rpi-connect wayvnc"
    fi
fi

TARGETS=""
for p in $PURGE $RPD; do installed "$p" && TARGETS="$TARGETS $p"; done
[ "$SWAP_CONNECT" -eq 1 ] && TARGETS="$TARGETS rpi-connect"
[ -n "$TARGETS" ] || { echo "Nothing to remove; the desktop is already gone."; exit 0; }

# 1. protect what the car needs
PROTECT=""
for p in $KEEP; do installed "$p" && PROTECT="$PROTECT $p"; done
# Marking manual only stops autoremove from taking a package, so it is done in a dry run too: the simulation
# below is then exactly the real one.
# shellcheck disable=SC2086
apt-mark manual $PROTECT >/dev/null

# 2. simulate and check
# shellcheck disable=SC2086
SIM=$(apt-get -s purge --autoremove $TARGETS 2>&1) || { echo "$SIM" >&2; exit 1; }
REMOVED=$(printf '%s\n' "$SIM" | awk '/^Purg / {print $2}')
BAD=""
for p in $PROTECT; do
    if printf '%s\n' "$REMOVED" | grep -qx "$p"; then BAD="$BAD $p"; fi
done
if [ -n "$BAD" ]; then
    echo "REFUSING: apt would remove packages the car needs:$BAD" >&2
    exit 1
fi
COUNT=$(printf '%s\n' "$REMOVED" | grep -c . || true)
KB=0
for p in $REMOVED; do
    s=$(dpkg-query -W -f='${Installed-Size}' "$p" 2>/dev/null || echo 0)
    KB=$((KB + ${s:-0}))
done
echo "apt would remove $COUNT packages, about $((KB / 1024)) MB:"
printf '%s\n' "$REMOVED" | sort | tr '\n' ' ' | fold -s -w 110
echo
[ "$DRY" -eq 1 ] && { [ "$SWAP_CONNECT" -eq 1 ] && echo "would install rpi-connect-lite (Pi Connect remote shell)"; exit 0; }

# 3. Pi Connect lite first (fetching it needs the network we still have), then the purge
if [ "$SWAP_CONNECT" -eq 1 ]; then
    apt-get -q -y download rpi-connect-lite >/dev/null 2>&1 || true
fi
# shellcheck disable=SC2086
apt-get -q -y purge --autoremove $TARGETS
if [ "$SWAP_CONNECT" -eq 1 ]; then
    apt-get -q -y install --no-install-recommends rpi-connect-lite || echo "WARNING: rpi-connect-lite not installed"
    rm -f rpi-connect-lite_*.deb
fi
apt-get -q -y clean

# 4. verify the car still has what it needs
fail=0
for cmd in /usr/sbin/sshd /usr/bin/nmcli /usr/sbin/iw /usr/sbin/dnsmasq /usr/sbin/avahi-daemon /usr/bin/vcgencmd \
           /usr/sbin/i2cdetect /usr/bin/rsync /usr/bin/python3; do
    [ -x "$cmd" ] || { echo "MISSING: $cmd" >&2; fail=1; }
done
python3 -c 'import aiohttp, smbus2, RPi.GPIO' 2>/dev/null || { echo "MISSING: python modules for the car" >&2; fail=1; }
systemctl is-active --quiet ssh || { echo "WARNING: ssh is not active" >&2; fail=1; }
systemctl is-active --quiet NetworkManager || { echo "WARNING: NetworkManager is not active" >&2; fail=1; }
df -h / | tail -1
[ "$fail" -eq 0 ] && echo "done: the car's services are intact; reboot to finish" || exit 1
