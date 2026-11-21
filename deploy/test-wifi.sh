#!/bin/sh
# Shell-level tests for car-wifi (embedded in wifi-setup.sh) with FAKE nmcli/iw/systemd-run/systemctl/
# logger/sleep: no network, no radio, the real car is never contacted. Run only in a container:
#   docker run --rm -v "$PWD:/repo" -w /repo debian:trixie-slim sh deploy/test-wifi.sh
# shellcheck disable=SC2016  # the check snippets are meant to expand in the inner sh -c
set -eu
SRC=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
FAILS=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }
check() { # check DESCRIPTION CONDITION-COMMAND...
    d=$1; shift
    if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi
}

# the tool under test is the heredoc inside the installer
sed -n "/<<'CARWIFI_EOF'/,/^CARWIFI_EOF\$/p" "$SRC/deploy/wifi-setup.sh" | sed '1d;$d' > "$T/car-wifi"
chmod +x "$T/car-wifi"
[ -s "$T/car-wifi" ] || { echo "could not extract car-wifi"; exit 1; }

mkdir -p "$T/bin"
cat > "$T/bin/nmcli" <<'EOF'
#!/bin/sh
echo "nmcli $*" >> "$FAKE/calls"
a=$*
for last; do :; done
active() { cat "$FAKE/active" 2>/dev/null || true; }
case "$a" in
    *"GENERAL.STATE,GENERAL.CONNECTION device show"*)
        n=$(cat "$FAKE/polls" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE/polls"
        if [ -n "${HOME_AFTER_POLLS:-}" ] && [ -z "$(active)" ] && [ "$n" -gt "$HOME_AFTER_POLLS" ]; then
            printf '%s' "$HOME_PROFILE" > "$FAKE/active"
        fi
        if [ -n "$(active)" ]; then echo "100 (connected)"; active; echo; else echo "30 (disconnected)"; echo "--"; fi ;;
    *"GENERAL.CONNECTION device show"*) active; echo ;;
    *"connection up"*)
        name=; pwf=; prev=; for x; do [ "$prev" = id ] && name=$x; [ "$prev" = passwd-file ] && pwf=$x; prev=$x; done
        if [ "$name" = car-hotspot ]; then
            [ -z "${HOTSPOT_FAIL:-}" ] || exit 4
            # the profile keeps no secret (psk-flags 2): activation must supply it in a passwd-file
            [ -n "$pwf" ] && grep -q '^802-11-wireless-security.psk:' "$pwf" || exit 5
            cp "$pwf" "$FAKE/passwd.seen"; printf '%s' "$name" > "$FAKE/active"
        elif [ -n "${HOME_NEVER_ACTIVE:-}" ] || [ "$name" = "${HOME_FAIL_PROFILE:-}" ]; then : > "$FAKE/active"
        else printf '%s' "$name" > "$FAKE/active"; fi ;;
    *"connection down"*) [ "$(active)" = "$last" ] && : > "$FAKE/active"; exit 0 ;;
    *"device connect"*)  # the trap: NM may pick the hotspot
        printf '%s' car-hotspot > "$FAKE/active" ;;
    *"-f NAME,TYPE connection show"*) cat "$FAKE/profiles" ;;
    *"connection.id connection show"*) grep -q "^$last:" "$FAKE/profiles" || exit 10; echo "$last" ;;
    *"802-11-wireless.ssid connection show"*) cat "$FAKE/ssid.$last" 2>/dev/null || exit 10 ;;
    *"802-11-wireless.mode connection show"*) if [ "$last" = car-hotspot ]; then echo ap; else echo infrastructure; fi ;;
    *"device wifi list"*) cat "$FAKE/visible" ;;
    *"IP4.ADDRESS"*) echo "10.42.0.1/24" ;;
    *"connection add"*) echo "$a" >> "$FAKE/added"; echo "car-hotspot:802-11-wireless" >> "$FAKE/profiles" ;;
    *"connection modify"*) echo "$a" >> "$FAKE/modified" ;;
    *"connection delete"*) grep -v "^$last:" "$FAKE/profiles" > "$FAKE/p.new" || true; mv "$FAKE/p.new" "$FAKE/profiles" ;;
esac
exit 0
EOF
cat > "$T/bin/iw" <<'EOF'
#!/bin/sh
echo "iw $*" >> "$FAKE/calls"
[ -z "${IW_FAIL:-}" ] || exit 1
i=0
while [ "$i" -lt "$(cat "$FAKE/stations")" ]; do echo "Station 02:00:00:00:00:0$i (on wlan0)"; echo "	inactive time:	10 ms"; i=$((i + 1)); done
exit 0
EOF
cat > "$T/bin/systemd-run" <<'EOF'
#!/bin/sh
echo "systemd-run $*" >> "$FAKE/calls"
exit "${SYSTEMD_RUN_FAIL:-0}"
EOF
cat > "$T/bin/systemctl" <<'EOF'
#!/bin/sh
echo "systemctl $*" >> "$FAKE/calls"
exit 0
EOF
cat > "$T/bin/logger" <<'EOF'
#!/bin/sh
shift 3
echo "$*" >> "$FAKE/journal"
EOF
cat > "$T/bin/sleep" <<'EOF'
#!/bin/sh
echo "sleep $*" >> "$FAKE/calls"
EOF
printf '#!/bin/sh\nexit 0\n' > "$T/bin/dnsmasq"
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH"

export FAKE="$T/fake" HOME_PROFILE=netplan-wlan0-CasaChifor
export CAR_WIFI_CONF_DIR="$T/conf" CAR_WIFI_STATE_DIR="$T/state" CAR_WIFI_SELF="$T/car-wifi"
unset HOME_AFTER_POLLS HOME_NEVER_ACTIVE HOME_FAIL_PROFILE CAR_WIFI_NOW IW_FAIL SYSTEMD_RUN_FAIL HOTSPOT_FAIL CAR_WIFI_ROOT || true

# fresh FAKE state: a saved home profile and a hotspot profile, nothing active, nobody connected
fresh() {
    rm -rf "$FAKE" "$T/state" "$T/conf"
    mkdir -p "$FAKE" "$T/conf"
    unset HOME_AFTER_POLLS HOME_NEVER_ACTIVE HOME_FAIL_PROFILE CAR_WIFI_NOW IW_FAIL SYSTEMD_RUN_FAIL HOTSPOT_FAIL
    printf '%s\n' "netplan-wlan0-CasaChifor:802-11-wireless" "car-hotspot:802-11-wireless" "Wired connection 1:802-3-ethernet" > "$FAKE/profiles"
    echo CasaChifor > "$FAKE/ssid.netplan-wlan0-CasaChifor"
    echo PiCar-ABCD > "$FAKE/ssid.car-hotspot"
    : > "$FAKE/active"; : > "$FAKE/visible"; : > "$FAKE/calls"; : > "$FAKE/journal"
    echo 0 > "$FAKE/stations"
    echo "$1" > "$T/conf/wifi-mode"
    echo "initialpass1" > "$T/conf/hotspot.psk"
}
cw() { sh "$T/car-wifi" "$@" > "$T/out" 2>&1; }
cwi() { printf '%s
' "$1" | sh "$T/car-wifi" password --stdin > "$T/out" 2>&1; }
active() { cat "$FAKE/active"; }
called() { grep -q -- "$1" "$FAKE/calls"; }
logged() { grep -q -- "$1" "$FAKE/journal"; }

# 1. boot, auto: the home network connects while we wait -> no hotspot
fresh auto; HOME_AFTER_POLLS=3 cw boot
check "boot auto: home connects -> no hotspot" sh -c '[ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ] && ! grep -q "connection up" "$FAKE/calls"'

# 2. boot, auto: nothing connects -> the hotspot after the full wait
fresh auto; cw boot
check "boot auto: no home -> hotspot after the wait" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ] && [ "$(grep -c "^sleep" "$FAKE/calls")" -ge 15 ]'
check "boot auto: the decision is logged" logged "starting the hotspot"

# 3. boot, auto: hotspot profile missing and no home -> error (and no crash loop of nmcli ups)
fresh auto; grep -v '^car-hotspot:' "$FAKE/profiles" > "$FAKE/p.new"; mv "$FAKE/p.new" "$FAKE/profiles"
if cw boot; then bad "boot with no hotspot profile reported success"; else ok "boot auto: missing hotspot profile fails loudly"; fi

# 4. boot, hotspot mode: the hotspot at once, without waiting
fresh hotspot; printf '%s' "$HOME_PROFILE" > "$FAKE/active"; cw boot
check "boot hotspot mode: hotspot at once" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ] && ! grep -q "^sleep" "$FAKE/calls"'

# 5. boot, home mode: nothing is touched
fresh home; cw boot
check "boot home mode: does nothing" sh -c '! grep -Eq "connection (up|down)|device connect" "$FAKE/calls"'

# 6. watch with a client connected: no change even though home is visible
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; echo 2 > "$FAKE/stations"; echo CasaChifor > "$FAKE/visible"; cw watch
check "watch: clients connected -> no change" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ] && ! grep -Eq "connection (up|down)" "$FAKE/calls"'

# 7. watch, station list unreadable: treated as in use
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; echo CasaChifor > "$FAKE/visible"; IW_FAIL=1 cw watch
check "watch: unreadable station list -> no change" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ]'

# 8. watch, idle, known SSID visible -> switch home (hotspot down, then home up)
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; printf '%s\n' Other CasaChifor > "$FAKE/visible"; cw watch
check "watch: idle + home visible -> switched home" sh -c '[ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ] && grep -n "connection down id car-hotspot" "$FAKE/calls" | head -1 | cut -d: -f1 | { read -r d; u=$(grep -n "connection up id netplan" "$FAKE/calls" | head -1 | cut -d: -f1); [ "$d" -lt "$u" ]; }'
check "watch: the switch is logged" logged "is in range"

# 9. watch, idle, home visible but does not come up -> hotspot back
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; echo CasaChifor > "$FAKE/visible"; HOME_NEVER_ACTIVE=1 cw watch
check "watch: home fails -> hotspot restored" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ] && grep -q "restoring the hotspot" "$FAKE/journal"'

# 10. watch, idle, no known SSID in range -> nothing
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; echo SomeoneElse > "$FAKE/visible"; cw watch
check "watch: nothing known in range -> no change" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ] && ! grep -Eq "connection (up|down)" "$FAKE/calls"'

# 11. watch outside auto mode, or when the hotspot is not the active connection
fresh hotspot; printf '%s' car-hotspot > "$FAKE/active"; echo CasaChifor > "$FAKE/visible"; cw watch
check "watch: hotspot mode -> no change" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ]'
fresh auto; printf '%s' "$HOME_PROFILE" > "$FAKE/active"; cw watch
check "watch: already on home -> no scan, no change" sh -c '! grep -q "device wifi list" "$FAKE/calls"'

# 12. test-hotspot: hotspot now, mode not persisted, the return scheduled first, watch held off
fresh auto; printf '%s' "$HOME_PROFILE" > "$FAKE/active"; cw test-hotspot 10
check "test-hotspot: hotspot active, mode unchanged" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ] && [ "$(cat "$CAR_WIFI_CONF_DIR/wifi-mode")" = auto ]'
check "test-hotspot: schedules the return after 10m" sh -c 'grep -q "systemd-run.*--on-active=10m.*car-wifi revert" "$FAKE/calls"'
check "test-hotspot: the return is scheduled before the switch" sh -c 'a=$(grep -n "^systemd-run" "$FAKE/calls" | head -1 | cut -d: -f1); b=$(grep -n "connection up id car-hotspot" "$FAKE/calls" | head -1 | cut -d: -f1); [ "$a" -lt "$b" ]'
echo CasaChifor > "$FAKE/visible"; : > "$FAKE/calls"; cw watch
check "test-hotspot: watch leaves the hotspot alone during the test" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ] && ! grep -Eq "connection (up|down)" "$FAKE/calls"'
cw revert
check "revert: back on the home network, hold cleared" sh -c '[ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ] && [ ! -e "$CAR_WIFI_STATE_DIR/test-hold" ]'
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; HOME_NEVER_ACTIVE=1 cw revert
check "revert: home gone in auto mode -> hotspot restored" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ]'
fresh auto; printf '%s' "$HOME_PROFILE" > "$FAKE/active"; SYSTEMD_RUN_FAIL=1 cw test-hotspot 5 || true
check "test-hotspot: cannot schedule -> does not switch" sh -c '[ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ] && ! grep -q "connection up" "$FAKE/calls"'
for bad_arg in "" abc 0 -3 99999; do
    fresh auto
    if cw test-hotspot $bad_arg; then bad "test-hotspot accepted '$bad_arg'"; else ok "test-hotspot rejects '$bad_arg'"; fi
done

# 13. persistent mode commands
fresh auto; printf '%s' "$HOME_PROFILE" > "$FAKE/active"; cw hotspot
check "hotspot: persisted and applied" sh -c '[ "$(cat "$CAR_WIFI_CONF_DIR/wifi-mode")" = hotspot ] && [ "$(cat "$FAKE/active")" = car-hotspot ]'
cw home
check "home: persisted, hotspot left for the home network" sh -c '[ "$(cat "$CAR_WIFI_CONF_DIR/wifi-mode")" = home ] && [ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ]'

# 14. status
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; echo 3 > "$FAKE/stations"; cw status
check "status: mode, connection and client count" sh -c 'grep -q "mode:  *auto" "$0" && grep -q "connection:  *car-hotspot" "$0" && grep -q "hotspot clients: 3" "$0"' "$T/out"

# 15. password
fresh auto
for p in short "$(printf 'x%.0s' $(seq 1 64))" "caf$(printf '\303\251')1234" "tab$(printf '\t')12345"; do
    if cwi "$p"; then bad "password accepted an invalid value"; else ok "password rejects invalid value"; fi
done
check "password: invalid values change nothing" sh -c '[ "$(cat "$CAR_WIFI_CONF_DIR/hotspot.psk")" = initialpass1 ] && ! grep -q "connection modify" "$FAKE/calls"'
cwi "good pass 123"
check "password: set updates the file (0600), nmcli is not involved" sh -c '[ "$(cat "$CAR_WIFI_CONF_DIR/hotspot.psk")" = "good pass 123" ] && [ "$(stat -c %a "$CAR_WIFI_CONF_DIR/hotspot.psk")" = 600 ] && ! grep -q "connection modify" "$FAKE/calls"'
cw password
check "password: show prints it" sh -c '[ "$(cat "$0")" = "good pass 123" ]' "$T/out"
rm -f "$CAR_WIFI_CONF_DIR/hotspot.psk"
if cw password; then bad "password with no file succeeded"; else ok "password: no file fails"; fi
: > "$FAKE/calls"
if cw password "good pass 123"; then bad "password accepted the passphrase as an argument"; else ok "password: the argv form is refused"; fi
check "password: argv attempt changed nothing" sh -c '! grep -q "connection modify" "$FAKE/calls"'

# 17. watch with no WiFi connection at all (home lost after boot): grace, then hotspot, retried
fresh auto; cw watch
check "watch: no connection, first run -> grace, no hotspot" sh -c '[ -z "$(cat "$FAKE/active")" ] && ! grep -q "connection up" "$FAKE/calls"'
HOTSPOT_FAIL=1 cw watch
check "watch: no connection, second run, hotspot start fails -> nothing active" sh -c '[ -z "$(cat "$FAKE/active")" ] && grep -q "connection up id car-hotspot" "$FAKE/calls"'
cw watch
check "watch: failed hotspot is retried on the next run" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ]'
fresh auto; cw watch; printf '%s' "$HOME_PROFILE" > "$FAKE/active"; cw watch; : > "$FAKE/active"; cw watch
check "watch: reconnecting resets the grace counter" sh -c '[ -z "$(cat "$FAKE/active")" ] && ! grep -q "connection up" "$FAKE/calls"'
fresh home; cw watch; cw watch; cw watch
check "watch: home mode never starts the hotspot" sh -c '! grep -q "connection up" "$FAKE/calls"'
fresh auto; : > "$FAKE/calls"; echo 1 > "$FAKE/stations"; cw watch; cw watch
check "watch: no connection and the hotspot gets started even with the stale station file (no AP active)" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ]'

# 18. returning home brings up an explicit profile, never what "device connect" would pick
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; echo CasaChifor > "$FAKE/visible"; cw home
check "home from the hotspot: explicit profile on wlan0, no device connect" sh -c '[ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ] && grep -q "connection up id netplan-wlan0-CasaChifor ifname wlan0" "$FAKE/calls" && ! grep -q "device connect" "$FAKE/calls"'
fresh auto; printf '%s' "$HOME_PROFILE" > "$FAKE/active"; cw test-hotspot 5; cw revert
check "test revert lands on the home profile, not the hotspot" sh -c '[ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ]'
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; : > "$FAKE/calls"; cw home
check "home: never tries the hotspot profile as home" sh -c '! grep -q "connection up id car-hotspot" "$FAKE/calls"'

# 16. installer: install, idempotence, uninstall leaves the home profile
fresh auto; rm -rf "$T/conf"
grep -v "^car-hotspot:" "$FAKE/profiles" > "$FAKE/p.new"; mv "$FAKE/p.new" "$FAKE/profiles"
export CAR_WIFI_ROOT="$T/root" CAR_WIFI_MAC=b8:27:eb:12:ab:cd
sh "$SRC/deploy/wifi-setup.sh" > "$T/inst1" 2>&1 || { bad "installer failed: $(cat "$T/inst1")"; }
R=$T/root
check "install: tool, units, mode and passphrase in place" sh -c '[ -x "$0/usr/local/sbin/car-wifi" ] && [ -f "$0/etc/systemd/system/car-wifi.service" ] && [ -f "$0/etc/systemd/system/car-wifi-watch.timer" ] && [ -f "$0/etc/systemd/system/car-wifi-watch.service" ] && [ "$(cat "$0/etc/car-scratch/wifi-mode")" = auto ] && [ "$(stat -c %a "$0/etc/car-scratch/hotspot.psk")" = 600 ]' "$R"
check "install: 12 character passphrase without look-alikes" sh -c 'p=$(cat "$0/etc/car-scratch/hotspot.psk"); [ "${#p}" -eq 12 ] && ! printf %s "$p" | grep -q "[0OIl1]"' "$R"
check "install: hotspot profile has the SSID, AP mode, channel 6, shared 10.42.0.1/24, no autoconnect" sh -c 'grep -q "connection add type wifi ifname wlan0 con-name car-hotspot" "$FAKE/added" && grep -q "802-11-wireless.ssid PiCar-ABCD" "$FAKE/added" && grep -q "802-11-wireless.mode ap" "$FAKE/added" && grep -q "802-11-wireless.channel 6" "$FAKE/added" && grep -q "ipv4.method shared ipv4.addresses 10.42.0.1/24" "$FAKE/added" && grep -q "connection.autoconnect no" "$FAKE/added" && grep -q "ipv6.method disabled" "$FAKE/added"'
check "install: the passphrase is not printed" sh -c '! grep -qF "$(cat "$0/etc/car-scratch/hotspot.psk")" "$1"' "$R" "$T/inst1"
check "install: units are enabled" sh -c 'grep -q "systemctl enable car-wifi.service" "$FAKE/calls" && grep -q "systemctl enable --now car-wifi-watch.timer" "$FAKE/calls"'
check "install: the unit runs the installed tool" grep -q "ExecStart=/usr/local/sbin/car-wifi boot" "$R/etc/systemd/system/car-wifi.service"
psk1=$(cat "$R/etc/car-scratch/hotspot.psk"); echo hotspot > "$R/etc/car-scratch/wifi-mode"
sh "$SRC/deploy/wifi-setup.sh" > "$T/inst2" 2>&1 || bad "second install failed: $(cat "$T/inst2")"
check "reinstall: idempotent (passphrase, mode kept; profile modified not duplicated)" sh -c '[ "$(cat "$0/etc/car-scratch/hotspot.psk")" = "$1" ] && [ "$(cat "$0/etc/car-scratch/wifi-mode")" = hotspot ] && [ "$(grep -c "^car-hotspot:" "$FAKE/profiles")" -eq 1 ] && grep -q "unchanged" "$2"' "$R" "$psk1" "$T/inst2"
check "install/reinstall: psk-flags 2, and the passphrase on no command line at all" sh -c 'grep -q "802-11-wireless-security.psk-flags 2" "$FAKE/added" && grep -q "802-11-wireless-security.psk-flags 2" "$FAKE/modified" && ! grep -qF "$0" "$FAKE/calls" "$FAKE/added" "$FAKE/modified"' "$psk1"
: > "$FAKE/calls"
sh "$SRC/deploy/wifi-setup.sh" --uninstall > "$T/inst3" 2>&1 || bad "uninstall failed: $(cat "$T/inst3")"
check "uninstall: everything it installed is gone" sh -c '[ ! -e "$0/usr/local/sbin/car-wifi" ] && [ ! -e "$0/etc/systemd/system/car-wifi.service" ] && [ ! -e "$0/etc/systemd/system/car-wifi-watch.timer" ] && [ ! -e "$0/etc/systemd/system/car-wifi-watch.service" ] && [ ! -e "$0/etc/car-scratch" ] && ! grep -q "^car-hotspot:" "$FAKE/profiles"' "$R"
check "uninstall: the home profile is still there and was never touched" sh -c 'grep -q "^netplan-wlan0-CasaChifor:" "$FAKE/profiles" && ! grep -Eq "(modify|delete|down).*netplan-wlan0-CasaChifor" "$FAKE/calls" && ! grep -q "netplan-wlan0-CasaChifor" "$FAKE/added"'
rm -rf "$R"; fresh auto; printf '%s' car-hotspot > "$FAKE/active"
sh "$SRC/deploy/wifi-setup.sh" > /dev/null 2>&1; : > "$FAKE/calls"
sh "$SRC/deploy/wifi-setup.sh" --uninstall > /dev/null 2>&1
check "uninstall while the hotspot is active: asks NM to reconnect wlan0" grep -q "device connect wlan0" "$FAKE/calls"
unset CAR_WIFI_ROOT CAR_WIFI_MAC

# 19. several saved profiles, names with spaces: the list must not be glued together
two_profiles() {
    printf '%s\n' "Home WiFi:802-11-wireless" "Backup net:802-11-wireless" "car-hotspot:802-11-wireless" > "$FAKE/profiles"
    echo "Casa Chifor" > "$FAKE/ssid.Home WiFi"; echo "Other Net" > "$FAKE/ssid.Backup net"
}
fresh auto; two_profiles; printf '%s' car-hotspot > "$FAKE/active"; echo "Casa Chifor" > "$FAKE/visible"
HOME_FAIL_PROFILE="Home WiFi" cw home
check "home: first (visible) profile fails -> the second one, by its own name, is tried and active" sh -c '[ "$(cat "$FAKE/active")" = "Backup net" ] && grep -q "connection up id Home WiFi ifname wlan0" "$FAKE/calls" && grep -q "connection up id Backup net ifname wlan0" "$FAKE/calls" && ! grep -q "Home WiFiBackup" "$FAKE/calls"'
fresh auto; two_profiles; printf '%s' car-hotspot > "$FAKE/active"; echo "Casa Chifor" > "$FAKE/visible"; cw home
check "home: two profiles, visible one with a space in its name and SSID -> connected" sh -c '[ "$(cat "$FAKE/active")" = "Home WiFi" ]'

# 20. backoff for a visible-but-unusable home network
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; echo CasaChifor > "$FAKE/visible"; export CAR_WIFI_NOW=1000
HOME_NEVER_ACTIVE=1 cw watch
check "backoff: first failed switch restores the hotspot" sh -c '[ "$(cat "$FAKE/active")" = car-hotspot ]'
: > "$FAKE/calls"; CAR_WIFI_NOW=1300 cw watch
check "backoff: 5 min later the profile is not retried (no teardown)" sh -c '! grep -Eq "connection (up|down)" "$FAKE/calls"'
: > "$FAKE/calls"; CAR_WIFI_NOW=1601 HOME_NEVER_ACTIVE=1 cw watch
check "backoff: after 10 min it is retried (and fails again)" sh -c 'grep -q "connection down id car-hotspot" "$FAKE/calls"'
: > "$FAKE/calls"; CAR_WIFI_NOW=2700 cw watch
check "backoff: the second interval is 20 min" sh -c '! grep -Eq "connection (up|down)" "$FAKE/calls"'
: > "$FAKE/calls"; CAR_WIFI_NOW=2802 cw watch
check "backoff: ...and is retried after it (success resets)" sh -c '[ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ]'
check "backoff: success removes the state" sh -c '! ls "$CAR_WIFI_STATE_DIR" | grep -q "^backoff"'
printf '%s' car-hotspot > "$FAKE/active"; : > "$FAKE/calls"; CAR_WIFI_NOW=2803 cw watch
check "backoff: after a success the next switch is immediate again" sh -c '[ "$(cat "$FAKE/active")" = netplan-wlan0-CasaChifor ]'
fresh auto; printf '%s' car-hotspot > "$FAKE/active"; echo CasaChifor > "$FAKE/visible"
for t in 0 4000 8000 12000 16000; do : > "$FAKE/calls"; printf '%s' car-hotspot > "$FAKE/active"; CAR_WIFI_NOW=$((100000 + t)) HOME_NEVER_ACTIVE=1 cw watch; done
check "backoff: the interval is capped at 1 h" sh -c '[ "$(cat "$CAR_WIFI_STATE_DIR"/backoff.* | cut -d" " -f2)" = 3600 ]'
unset CAR_WIFI_NOW
# a backed-off profile does not stop another visible profile
fresh auto; two_profiles; printf '%s' car-hotspot > "$FAKE/active"; printf '%s\n' "Casa Chifor" "Other Net" > "$FAKE/visible"
CAR_WIFI_NOW=500 HOME_FAIL_PROFILE="Home WiFi" cw watch
: > "$FAKE/calls"; printf '%s' car-hotspot > "$FAKE/active"; CAR_WIFI_NOW=600 cw watch
check "backoff: a backed-off profile is skipped for another visible one" sh -c '[ "$(cat "$FAKE/active")" = "Backup net" ]'

# 21. the passphrase never appears on any (faked) command line, and still reaches NM
fresh auto; : > "$FAKE/calls"
cwi "s3cret pass 456"
CAR_WIFI_NOW=1 cw hotspot
check "passphrase: used for activation through the passwd-file" sh -c '[ "$(cat "$FAKE/passwd.seen")" = "802-11-wireless-security.psk:s3cret pass 456" ]'
check "passphrase: never on an nmcli/iw/systemd-run command line" sh -c '! grep -rqF "s3cret" "$FAKE/calls" "$FAKE/journal"'
check "passphrase: the temp passwd-file is removed" sh -c '! ls "$CAR_WIFI_STATE_DIR" | grep -q "^pw\."'

if [ "$FAILS" -eq 0 ]; then echo "all wifi tests passed"; else echo "$FAILS wifi test(s) failed"; exit 1; fi
