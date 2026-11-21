#!/bin/sh
# Install (or --uninstall) the car's WiFi fallback: when the home network is not reachable the
# car starts its own WPA2 hotspot (SSID PiCar-XXXX, http://10.42.0.1:8080). See docs/wifi.md.
#   scp deploy/wifi-setup.sh carops@<car>:/tmp/ && ssh carops@<car> sudo sh /tmp/wifi-setup.sh
# Idempotent. Never touches the home WiFi profile. CAR_WIFI_ROOT / CAR_WIFI_MAC / CAR_WIFI_IFACE
# exist only so deploy/test-wifi.sh can run this unprivileged against fakes.
set -eu

ROOT=${CAR_WIFI_ROOT:-}
IFACE=${CAR_WIFI_IFACE:-wlan0}
HOTSPOT=car-hotspot
TOOL=$ROOT/usr/local/sbin/car-wifi
UNITDIR=$ROOT/etc/systemd/system
CONF=$ROOT/etc/car-scratch
PSK_FILE=$CONF/hotspot.psk
MODE_FILE=$CONF/wifi-mode

say() { echo "wifi-setup: $*"; }
die() { echo "wifi-setup: ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || [ -n "$ROOT" ] || die "run as root (sudo sh wifi-setup.sh)"
command -v nmcli >/dev/null 2>&1 || die "nmcli (NetworkManager) is required"

# put PATH MODE < content: write only when it differs, so reruns report "unchanged"
put() {
    mkdir -p "$(dirname "$1")"
    tmp=$(mktemp "$1.XXXXXX")
    cat > "$tmp"
    chmod "$2" "$tmp"
    if [ -f "$1" ] && cmp -s "$tmp" "$1"; then
        rm -f "$tmp"
        chmod "$2" "$1"
        say "unchanged $1"
    else
        mv "$tmp" "$1"
        say "wrote $1"
    fi
}

if [ "${1:-}" = "--uninstall" ]; then
    was_hotspot=no
    if [ "$(nmcli -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null || true)" = "$HOTSPOT" ]; then was_hotspot=yes; fi
    systemctl disable --now car-wifi-watch.timer >/dev/null 2>&1 || true
    systemctl stop car-wifi-revert.timer car-wifi-revert.service >/dev/null 2>&1 || true
    systemctl disable car-wifi.service >/dev/null 2>&1 || true
    say "stopped and disabled the car-wifi units"
    if nmcli -g connection.id connection show id "$HOTSPOT" >/dev/null 2>&1; then
        nmcli connection down id "$HOTSPOT" >/dev/null 2>&1 || true
        nmcli connection delete id "$HOTSPOT" >/dev/null 2>&1 || true
        say "removed the NetworkManager connection $HOTSPOT"
    fi
    for f in "$TOOL" "$UNITDIR/car-wifi.service" "$UNITDIR/car-wifi-watch.service" \
        "$UNITDIR/car-wifi-watch.timer" "$MODE_FILE" "$PSK_FILE"; do
        if [ -e "$f" ]; then rm -f "$f"; say "removed $f"; fi
    done
    rmdir "$CONF" 2>/dev/null || true
    rm -rf "$ROOT/run/car-wifi"
    systemctl daemon-reload >/dev/null 2>&1 || true
    if [ "$was_hotspot" = yes ]; then
        say "the hotspot was active: asking NetworkManager to reconnect $IFACE to a saved network"
        nmcli device connect "$IFACE" >/dev/null 2>&1 || say "WARNING: could not reconnect $IFACE; power-cycle near the router"
    fi
    say "done; the home WiFi profile was not touched"
    exit 0
fi

# preconditions for the hotspot itself
[ -x /usr/sbin/dnsmasq ] || command -v dnsmasq >/dev/null 2>&1 || [ -n "$ROOT" ] || die "dnsmasq is missing (apt install dnsmasq-base): NetworkManager needs it to hand out addresses"
command -v iw >/dev/null 2>&1 || die "iw is missing (apt install iw): car-wifi counts hotspot clients with it"

# SSID = PiCar- + last 4 hex digits of the WiFi MAC, upper case
mac=${CAR_WIFI_MAC:-$(cat "/sys/class/net/$IFACE/address" 2>/dev/null || true)}
[ -n "$mac" ] || die "cannot read the MAC address of $IFACE"
suffix=$(printf '%s' "$mac" | tr -d ':' | tr 'a-f' 'A-F' | cut -c9-12)
[ "${#suffix}" -eq 4 ] || die "unexpected MAC address '$mac'"
SSID="PiCar-$suffix"

# passphrase: keep an existing one, else generate (no look-alike characters)
mkdir -p "$CONF"
if [ -s "$PSK_FILE" ]; then
    say "kept the existing passphrase in $PSK_FILE"
else
    (
        umask 077
        LC_ALL=C tr -dc 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789' < /dev/urandom | head -c 12 > "$PSK_FILE.new"
        echo >> "$PSK_FILE.new"
        mv "$PSK_FILE.new" "$PSK_FILE"
    )
    say "generated a passphrase (read it with: sudo car-wifi password)"
fi
if [ -z "$ROOT" ]; then chown root:root "$PSK_FILE"; fi
chmod 600 "$PSK_FILE"
psk=$(head -n 1 "$PSK_FILE")
[ "${#psk}" -ge 8 ] || die "$PSK_FILE does not hold an 8..63 character passphrase"
[ "${#psk}" -le 63 ] || die "$PSK_FILE does not hold an 8..63 character passphrase"

if [ -s "$MODE_FILE" ]; then say "kept the existing mode: $(cat "$MODE_FILE")"; else echo auto > "$MODE_FILE"; say "wrote $MODE_FILE (auto)"; fi

# the NetworkManager connection (created or brought back in line; never autoconnects)
set_profile() {
    "$@" \
        connection.autoconnect no \
        802-11-wireless.ssid "$SSID" 802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
        802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.proto rsn \
        802-11-wireless-security.pairwise ccmp 802-11-wireless-security.group ccmp \
        802-11-wireless-security.psk "$psk" \
        ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled > /dev/null
}
if nmcli -g connection.id connection show id "$HOTSPOT" >/dev/null 2>&1; then
    set_profile nmcli connection modify id "$HOTSPOT"
    say "updated the NetworkManager connection $HOTSPOT (SSID $SSID; re-activate it to apply changes)"
else
    set_profile nmcli connection add type wifi ifname "$IFACE" con-name "$HOTSPOT"
    say "created the NetworkManager connection $HOTSPOT (SSID $SSID, channel 6, 10.42.0.1/24)"
fi

# the runtime tool (embedded so this installer is one file)
put "$TOOL" 755 <<'CARWIFI_EOF'
#!/bin/sh
# car-wifi: keep the car reachable. Home WiFi when there is one, otherwise its own hotspot.
# Commands: status | auto | home | hotspot | test-hotspot MINUTES | password [NEW] | boot | watch
# Invariant: in auto mode the car is never left without the home network or the hotspot.
set -u

IFACE=${CAR_WIFI_IFACE:-wlan0}
CONF_DIR=${CAR_WIFI_CONF_DIR:-/etc/car-scratch}
MODE_FILE=${CAR_WIFI_MODE_FILE:-$CONF_DIR/wifi-mode}
PSK_FILE=${CAR_WIFI_PSK_FILE:-$CONF_DIR/hotspot.psk}
STATE_DIR=${CAR_WIFI_STATE_DIR:-/run/car-wifi}
HOTSPOT=${CAR_WIFI_HOTSPOT:-car-hotspot}
BOOT_WAIT=${CAR_WIFI_BOOT_WAIT:-45}
SWITCH_WAIT=${CAR_WIFI_SWITCH_WAIT:-30}
SELF=${CAR_WIFI_SELF:-/usr/local/sbin/car-wifi}
HOLD=$STATE_DIR/test-hold

log() {
    logger -t car-wifi -- "$*" 2>/dev/null || true
    if [ -t 2 ]; then echo "car-wifi: $*" >&2; fi
    return 0
}
die() {
    log "ERROR: $*"
    if [ ! -t 2 ]; then echo "car-wifi: $*" >&2; fi
    exit 1
}
unesc() { printf '%s\n' "$1" | sed 's/\\\(.\)/\1/g'; }

get_mode() {
    m=$(cat "$MODE_FILE" 2>/dev/null || true)
    case $m in auto | home | hotspot) echo "$m" ;; *) echo auto ;; esac
}

set_mode() {
    mkdir -p "$(dirname "$MODE_FILE")"
    printf '%s\n' "$1" > "$MODE_FILE.tmp" && mv "$MODE_FILE.tmp" "$MODE_FILE"
}

# name of the connection active on the WiFi interface, empty when it is not connected
active_conn() {
    info=$(nmcli -g GENERAL.STATE,GENERAL.CONNECTION device show "$IFACE" 2>/dev/null) || return 0
    case $(printf '%s\n' "$info" | sed -n 1p) in
        100*) unesc "$(printf '%s\n' "$info" | sed -n 2p)" ;;
    esac
}

has_hotspot() { nmcli -g connection.id connection show id "$HOTSPOT" >/dev/null 2>&1; }

# stations connected to the hotspot; 1 when it cannot be told (never touch what we cannot see)
station_count() {
    out=$(iw dev "$IFACE" station dump 2>/dev/null) || { echo 1; return 0; }
    printf '%s\n' "$out" | grep -c '^Station ' || true
}

# wait_active NAME|"" SECONDS: NAME active, or (empty NAME) any non-hotspot connection active
wait_active() {
    w=0
    while :; do
        c=$(active_conn)
        if [ -n "$1" ]; then
            [ "$c" = "$1" ] && return 0
        elif [ -n "$c" ] && [ "$c" != "$HOTSPOT" ]; then
            return 0
        fi
        [ "$w" -ge "$2" ] && return 1
        sleep 3
        w=$((w + 3))
    done
}

hotspot_up() {
    has_hotspot || { log "ERROR: no $HOTSPOT connection (run wifi-setup.sh again)"; return 1; }
    if nmcli -w "$SWITCH_WAIT" connection up id "$HOTSPOT" >/dev/null 2>&1; then
        log "hotspot $HOTSPOT is active"
        return 0
    fi
    log "ERROR: could not activate $HOTSPOT"
    return 1
}

# ask NetworkManager for any saved network; 0 when one is active within SWITCH_WAIT
try_home() {
    nmcli -w "$SWITCH_WAIT" device connect "$IFACE" >/dev/null 2>&1 || true
    wait_active "" "$SWITCH_WAIT"
}

# saved infrastructure WiFi profiles (one per line), the hotspot excluded
infra_profiles() {
    nmcli -t -f NAME,TYPE connection show 2>/dev/null | while IFS= read -r line; do
        [ "${line##*:}" = 802-11-wireless ] || continue
        name=$(unesc "${line%:*}")
        [ "$name" = "$HOTSPOT" ] && continue
        mode=$(nmcli -g 802-11-wireless.mode connection show id "$name" 2>/dev/null || true)
        case $mode in '' | infrastructure) printf '%s\n' "$name" ;; esac
    done
}

# rescan and print the first saved profile whose SSID is in range
visible_profile() {
    seen=$(nmcli -t -f SSID device wifi list ifname "$IFACE" --rescan yes 2>/dev/null | while IFS= read -r l; do unesc "$l"; done)
    profs=$(infra_profiles)
    oldifs=$IFS
    IFS='
'
    set -f
    for p in $profs; do
        s=$(nmcli -g 802-11-wireless.ssid connection show id "$p" 2>/dev/null | sed -n 1p)
        s=$(unesc "$s")
        [ -n "$s" ] || continue
        if printf '%s\n' "$seen" | grep -qxF -- "$s"; then
            set +f
            IFS=$oldifs
            printf '%s\n' "$p"
            return 0
        fi
    done
    set +f
    IFS=$oldifs
    return 0
}

cancel_revert() { systemctl stop car-wifi-revert.timer car-wifi-revert.service >/dev/null 2>&1 || true; }

cmd_boot() {
    rm -f "$HOLD"
    mode=$(get_mode)
    log "boot: mode $mode"
    case $mode in
        home) log "boot: home mode, leaving WiFi to NetworkManager (no hotspot fallback)" ;;
        hotspot) hotspot_up ;;
        auto)
            if [ "$(active_conn)" = "$HOTSPOT" ]; then log "boot: hotspot already active"; return 0; fi
            if wait_active "" "$BOOT_WAIT"; then
                log "boot: home network connected ($(active_conn)), no hotspot"
            else
                log "boot: no WiFi network after ${BOOT_WAIT}s, starting the hotspot"
                hotspot_up
            fi
            ;;
    esac
}

cmd_watch() {
    [ "$(get_mode)" = auto ] || return 0
    [ ! -e "$HOLD" ] || return 0
    [ "$(active_conn)" = "$HOTSPOT" ] || return 0
    [ "$(station_count)" -eq 0 ] || return 0
    target=$(visible_profile)
    [ -n "$target" ] || return 0
    # someone may have joined while we scanned
    [ "$(station_count)" -eq 0 ] || { log "watch: a client joined during the scan, staying on the hotspot"; return 0; }
    log "watch: no clients and '$target' is in range, leaving the hotspot"
    nmcli connection down id "$HOTSPOT" >/dev/null 2>&1 || true
    nmcli -w "$SWITCH_WAIT" connection up id "$target" >/dev/null 2>&1 || true
    if wait_active "$target" "$SWITCH_WAIT"; then
        log "watch: connected to '$target'"
    else
        log "watch: '$target' did not come up within ${SWITCH_WAIT}s, restoring the hotspot"
        hotspot_up
    fi
}

# the return from test-hotspot (run by systemd-run): go back to the persisted mode
cmd_revert() {
    rm -f "$HOLD"
    mode=$(get_mode)
    log "revert: back to mode $mode"
    case $mode in
        hotspot) [ "$(active_conn)" = "$HOTSPOT" ] || hotspot_up ;;
        auto | home)
            [ "$(active_conn)" = "$HOTSPOT" ] || return 0
            nmcli connection down id "$HOTSPOT" >/dev/null 2>&1 || true
            if try_home; then
                log "revert: home network connected ($(active_conn))"
            elif [ "$mode" = auto ]; then
                log "revert: home network not available, restoring the hotspot"
                hotspot_up
            else
                log "ERROR: revert: home mode and no network (no fallback in home mode)"
                return 1
            fi
            ;;
    esac
}

apply_mode() {
    case $1 in
        hotspot) [ "$(active_conn)" = "$HOTSPOT" ] || hotspot_up ;;
        home)
            c=$(active_conn)
            if [ -n "$c" ] && [ "$c" != "$HOTSPOT" ]; then return 0; fi
            if [ "$c" = "$HOTSPOT" ]; then nmcli connection down id "$HOTSPOT" >/dev/null 2>&1 || true; fi
            try_home || { log "ERROR: home mode and no WiFi network connected (no hotspot fallback)"; return 1; }
            ;;
        auto)
            [ -z "$(active_conn)" ] || return 0
            wait_active "" 10 || hotspot_up
            ;;
    esac
}

cmd_set_mode() {
    set_mode "$1"
    rm -f "$HOLD"
    cancel_revert
    log "mode set to $1"
    if [ "$1" = home ]; then
        echo "car-wifi: home mode never starts the hotspot. If the home network is gone, the car is unreachable until you power-cycle it near the router." >&2
    fi
    apply_mode "$1"
}

cmd_test_hotspot() {
    n=${1:-}
    case $n in '' | *[!0-9]*) die "usage: car-wifi test-hotspot MINUTES (1..1440)" ;; esac
    [ "$n" -ge 1 ] || die "MINUTES must be 1..1440"
    [ "$n" -le 1440 ] || die "MINUTES must be 1..1440"
    has_hotspot || die "no $HOTSPOT connection (run wifi-setup.sh again)"
    mkdir -p "$STATE_DIR"
    cancel_revert
    # schedule the way back BEFORE leaving the current network, so a failure cannot strand us
    systemd-run --unit=car-wifi-revert --on-active="${n}m" \
        --description="car-wifi: return from the hotspot test" "$SELF" revert >/dev/null 2>&1 \
        || die "could not schedule the return (systemd-run failed); not switching"
    : > "$HOLD"
    log "test-hotspot: hotspot now, returning to mode $(get_mode) in $n min"
    if ! hotspot_up; then
        rm -f "$HOLD"
        cancel_revert
        return 1
    fi
    echo "hotspot is up; it returns to '$(get_mode)' mode in $n minutes (this session will drop)"
}

cmd_password() {
    if [ $# -eq 0 ]; then
        [ -r "$PSK_FILE" ] || die "cannot read $PSK_FILE (run as root; installed by wifi-setup.sh)"
        head -n 1 "$PSK_FILE"
        return 0
    fi
    p=$1
    [ "${#p}" -ge 8 ] || die "the passphrase must be 8..63 characters"
    [ "${#p}" -le 63 ] || die "the passphrase must be 8..63 characters"
    rest=$(printf '%s' "$p" | LC_ALL=C tr -d ' -~'; echo x)
    [ "$rest" = x ] || die "the passphrase may only contain printable ASCII characters"
    has_hotspot || die "no $HOTSPOT connection (run wifi-setup.sh again)"
    mkdir -p "$(dirname "$PSK_FILE")"
    (
        umask 077
        printf '%s\n' "$p" > "$PSK_FILE.new" && mv "$PSK_FILE.new" "$PSK_FILE"
    )
    nmcli connection modify id "$HOTSPOT" 802-11-wireless-security.psk "$p" >/dev/null || die "could not update the $HOTSPOT connection"
    log "hotspot passphrase changed"
    if [ "$(active_conn)" = "$HOTSPOT" ]; then
        echo "the hotspot is active: it keeps the old passphrase until it restarts (reboot, or car-wifi test-hotspot)"
    fi
}

cmd_status() {
    echo "mode:        $(get_mode)"
    c=$(active_conn)
    echo "connection:  ${c:-none}"
    if [ -n "$c" ]; then
        s=$(nmcli -g 802-11-wireless.ssid connection show id "$c" 2>/dev/null | sed -n 1p)
        echo "ssid:        $(unesc "$s")"
    fi
    echo "ipv4:        $(nmcli -g IP4.ADDRESS device show "$IFACE" 2>/dev/null | tr '\n' ' ')"
    if [ "$c" = "$HOTSPOT" ]; then echo "hotspot clients: $(station_count)"; fi
    if [ -e "$HOLD" ]; then echo "test:        hotspot test in progress (returns automatically)"; fi
}

cmd=${1:-}
case $cmd in
    boot | watch | auto | home | hotspot | revert | test-hotspot)
        mkdir -p "$STATE_DIR"
        if [ -z "${CAR_WIFI_LOCKED:-}" ] && command -v flock >/dev/null 2>&1; then
            CAR_WIFI_LOCKED=1 exec flock "$STATE_DIR/lock" "$0" "$@"
        fi
        ;;
esac
case $cmd in
    status) cmd_status ;;
    auto | home | hotspot) cmd_set_mode "$cmd" ;;
    test-hotspot) shift; cmd_test_hotspot "$@" ;;
    password) shift; cmd_password "$@" ;;
    boot) cmd_boot ;;
    watch) cmd_watch ;;
    revert) cmd_revert ;;
    *) echo "usage: car-wifi status | auto | home | hotspot | test-hotspot MINUTES | password [NEW]" >&2; exit 2 ;;
esac
CARWIFI_EOF

put "$UNITDIR/car-wifi.service" 644 <<'UNIT_EOF'
[Unit]
Description=Car WiFi: home network, or the car's own hotspot when there is none
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=180
ExecStart=/usr/local/sbin/car-wifi boot

[Install]
WantedBy=multi-user.target
UNIT_EOF

put "$UNITDIR/car-wifi-watch.service" 644 <<'UNIT_EOF'
[Unit]
Description=Car WiFi: leave the idle hotspot when a known network is in range
After=NetworkManager.service

[Service]
Type=oneshot
TimeoutStartSec=180
ExecStart=/usr/local/sbin/car-wifi watch
UNIT_EOF

put "$UNITDIR/car-wifi-watch.timer" 644 <<'UNIT_EOF'
[Unit]
Description=Car WiFi: look for the home network every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT_EOF

systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable car-wifi.service >/dev/null 2>&1 || say "WARNING: could not enable car-wifi.service"
systemctl enable --now car-wifi-watch.timer >/dev/null 2>&1 || say "WARNING: could not enable car-wifi-watch.timer"
say "enabled car-wifi.service (at boot) and car-wifi-watch.timer"

say "done. Hotspot SSID: $SSID (passphrase: sudo car-wifi password), mode: $(cat "$MODE_FILE"). Nothing was switched now; try it with: sudo car-wifi test-hotspot 10"
