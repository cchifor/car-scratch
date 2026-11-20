#!/bin/sh
# Shell-level tests for deploy.sh with stub ssh/rsync (no network, the real car is never contacted).
#   docker compose run --rm deploy sh deploy/test-deploy.sh
set -eu
SRC=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
FAILS=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

# a throwaway repo with the files deploy.sh ships, plus stubs for ssh and rsync
mkdir -p "$T/bin" "$T/repo/deploy" "$T/repo/server" "$T/repo/out/www"
cp "$SRC/deploy/deploy.sh" "$T/repo/deploy/deploy.sh"
echo '[Unit]' > "$T/repo/deploy/car-scratch.service"
echo 'x = 1' > "$T/repo/server/app.py"
cat > "$T/bin/ssh" <<'EOF'
#!/bin/sh
# the last argument is the remote command
for last; do :; done
echo "$last" >> "$STUB_LOG"
case "$last" in
    readlink*) echo "releases/oldrel" ;;
    *"systemctl --user daemon-reload"*"restart"*) [ -z "${FAIL_INSTALL:-}" ] || exit 1 ;;
    python3*) echo "{\"sha\": \"$EXPECT_SHA\"}" ;;
esac
exit 0
EOF
printf '#!/bin/sh\nexit 0\n' > "$T/bin/rsync"
chmod +x "$T/bin/ssh" "$T/bin/rsync"
cd "$T/repo"
git init -q . && git config user.email t@t && git config user.name t
git add deploy server && git commit -q -m init
SHA=$(git rev-parse --short=12 HEAD)
echo "$SHA" > out/www/build-sha.txt
echo "out/" > .gitignore && git add .gitignore && git commit -q -m ignore
SHA=$(git rev-parse --short=12 HEAD)
export PATH="$T/bin:$PATH" STUB_LOG="$T/log" EXPECT_SHA="$SHA"
deploy() { : > "$T/log"; sh deploy/deploy.sh "$@" > "$T/out" 2>&1; }

# 1. stale editor bundle: refused, and nothing is sent
echo "0123456789ab" > out/www/build-sha.txt
if deploy; then bad "stale out/www was deployed"; else
    grep -q "built from '0123456789ab'" "$T/out" && grep -q -- "--build-arg BUILD_SHA=" "$T/out" && [ ! -s "$T/log" ] \
        && ok "stale out/www refused with the build command" || bad "stale out/www: wrong message or ssh was called"; fi

# 2. a stale bundle is only a warning in --dry-run
if deploy --dry-run && grep -q "WARNING: out/www was built from" "$T/out"; then ok "dry run warns about a stale bundle"; else bad "dry run"; fi

# 3. happy path
echo "$SHA" > out/www/build-sha.txt
if deploy && grep -q "deployed $SHA" "$T/out"; then ok "matching bundle deploys"; else bad "happy path: $(cat "$T/out")"; fi

# 4. a failing restart rolls back (current re-pointed to the previous release, restart again)
if FAIL_INSTALL=1 deploy; then bad "failed restart reported success"; else
    if grep -q "ln -sfn releases/oldrel" "$T/log" && grep -q "rolling back to oldrel" "$T/out" \
        && [ "$(grep -c 'systemctl --user restart car-scratch' "$T/log")" -ge 2 ] && ! grep -q "python3" "$T/log"; then
        ok "restart failure takes the rollback path"; else bad "rollback not performed: $(cat "$T/out") / $(cat "$T/log")"; fi; fi

[ "$FAILS" -eq 0 ] && echo "all deploy tests passed" || { echo "$FAILS deploy test(s) failed"; exit 1; }
