#!/bin/sh
# Create (or update) the `carops` maintenance account on the Pi: key-only SSH, passwordless sudo, LAN only.
# It lets the workstation run root-level deploy/debug steps (apt, system units, journal, i2c) without the
# owner typing a sudo password. Run on the Pi as root, once:
#     sudo sh ops-user-setup.sh /tmp/carops.pub        # public key(s) allowed to log in as carops
# Idempotent; prints what it changed.  Remove the account again:
#     sudo sh ops-user-setup.sh --remove
# env: OPS_USER (default carops), OPS_FROM (SSH source networks for the key, default 192.168.0.0/23 = home LAN)
set -eu

OPS_USER=${OPS_USER:-carops}
OPS_FROM=${OPS_FROM:-192.168.0.0/23}
SUDOERS=/etc/sudoers.d/090-car-scratch-ops
CHANGED=""
note() { CHANGED="${CHANGED}  - $1
"; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

if [ "${1:-}" = "--remove" ]; then
    rm -f "$SUDOERS"
    if id "$OPS_USER" >/dev/null 2>&1; then
        pkill -u "$OPS_USER" 2>/dev/null || true
        userdel -r "$OPS_USER" 2>/dev/null || userdel "$OPS_USER"
    fi
    echo "removed $OPS_USER and $SUDOERS"
    exit 0
fi

KEYFILE=${1:-}
[ -n "$KEYFILE" ] && [ -f "$KEYFILE" ] || { echo "usage: $0 <public-key-file> | --remove" >&2; exit 2; }
grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+|sk-[a-z0-9@.-]+) ' "$KEYFILE" \
    || { echo "$KEYFILE does not look like an OpenSSH public key" >&2; exit 2; }

# 1. account: no usable password (key login only), hardware + log groups
GROUPS_WANTED=""
for g in adm systemd-journal gpio i2c spi dialout video; do
    getent group "$g" >/dev/null 2>&1 && GROUPS_WANTED="${GROUPS_WANTED:+$GROUPS_WANTED,}$g"
done
if ! id "$OPS_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --comment "Car Scratch ops (workstation deploy/debug)" \
        --groups "$GROUPS_WANTED" "$OPS_USER"
    note "created user $OPS_USER (groups $GROUPS_WANTED)"
else
    usermod --append --groups "$GROUPS_WANTED" "$OPS_USER"
fi
# '*' = no password can ever match, but the account is not "locked" (sshd still accepts keys)
if [ "$(getent shadow "$OPS_USER" | cut -d: -f2)" != "*" ]; then
    usermod --password '*' "$OPS_USER"
    note "$OPS_USER has no password (SSH key login only)"
fi

# 2. authorized_keys, each key restricted to the LAN and without agent/X11 forwarding
HOME_DIR=$(getent passwd "$OPS_USER" | cut -d: -f6)
install -d -m 700 -o "$OPS_USER" -g "$OPS_USER" "$HOME_DIR/.ssh"
TMP=$(mktemp)
grep -E '^(ssh-|ecdsa-|sk-)' "$KEYFILE" | while IFS= read -r key; do
    printf 'from="%s",no-agent-forwarding,no-X11-forwarding %s\n' "$OPS_FROM" "$key"
done > "$TMP"
if ! cmp -s "$TMP" "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null; then
    install -m 600 -o "$OPS_USER" -g "$OPS_USER" "$TMP" "$HOME_DIR/.ssh/authorized_keys"
    note "installed $(wc -l < "$TMP") key(s) for $OPS_USER, usable from $OPS_FROM only"
fi
rm -f "$TMP"

# 3. passwordless sudo, validated before it is installed (a broken sudoers file would lock out sudo)
TMP=$(mktemp)
printf '# Car Scratch maintenance account (deploy/ops-user-setup.sh). Remove: sudo sh ops-user-setup.sh --remove\n%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$OPS_USER" > "$TMP"
visudo -cqf "$TMP" || { rm -f "$TMP"; echo "sudoers check failed; nothing installed" >&2; exit 1; }
if ! cmp -s "$TMP" "$SUDOERS" 2>/dev/null; then
    install -m 440 -o root -g root "$TMP" "$SUDOERS"
    note "passwordless sudo for $OPS_USER ($SUDOERS)"
fi
rm -f "$TMP"

echo
if [ -n "$CHANGED" ]; then printf 'Changed:\n%s' "$CHANGED"; else echo "Nothing to change; already set up."; fi
echo "Test from the workstation:  ssh $OPS_USER@<car> sudo -n true && echo ok"
