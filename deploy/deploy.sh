#!/bin/sh
# Deploy the committed tree to the Pi car.
#   deploy/deploy.sh [--dry-run]        env: PICAR_HOST (default 192.168.0.209), PICAR_USER (default c4)
# Refuses a dirty tree, ships out/www + server/ + out/examples + out/source to
# ~/car-scratch/releases/<sha>/, switches the `current` symlink atomically, restarts the user unit,
# health-checks /api/status for the new sha and rolls back on failure. Keeps the last 3 releases.
# --dry-run prints the plan and makes no ssh/rsync call (a dirty tree is only a warning then).
set -eu

HOST=${PICAR_HOST:-192.168.0.209}
USER_=${PICAR_USER:-c4}
TARGET="$USER_@$HOST"
BASE='~/car-scratch'                       # expanded by the remote shell
KEEP=3
DRY=0
case "${1:-}" in
    --dry-run) DRY=1 ;;
    "") ;;
    *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."
git() { command git -c safe.directory='*' "$@"; }      # the repo may be mounted with another owner (docker)

say() { printf '%s\n' "$*"; }
step() { say "==> $*"; }
# run: print the command; execute it unless --dry-run
run() { say "    + $*"; [ "$DRY" -eq 1 ] || "$@"; }
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
remote() { say "    + ssh $TARGET $*"; [ "$DRY" -eq 1 ] || ssh $SSH_OPTS "$TARGET" "$@"; }

# --- 1. clean tree ---------------------------------------------------------------------------------------
if [ -n "$(git status --porcelain)" ]; then
    if [ "$DRY" -eq 1 ]; then
        say "WARNING: git tree is dirty; a real deploy would refuse."
    else
        echo "refusing to deploy: git tree is dirty (commit or stash first)" >&2
        git status --short >&2
        exit 1
    fi
fi
# One sha format everywhere: the 12-char short sha (CAR_SCRATCH_SHA / /api/status, the release dir, the editor's
# build-sha.txt written by `docker buildx build --build-arg BUILD_SHA=$(git rev-parse --short=12 HEAD)`).
SHA=$(git rev-parse --short=12 HEAD)
REL="$BASE/releases/$SHA"
say "deploy $SHA -> $TARGET:$REL$([ "$DRY" -eq 1 ] && echo '  (dry run)')"

# --- 2. build artefacts ----------------------------------------------------------------------------------
step "source tarball (AGPL section 13 offer, served at /source/)"
run mkdir -p out/source
run git archive --format=tar.gz --prefix="car-scratch-$SHA/" -o "out/source/car-scratch-$SHA.tar.gz" HEAD

step "examples"
if [ -f examples/tools/pack.py ]; then
    run python3 examples/tools/pack.py out/examples
else
    say "WARNING: examples/tools/pack.py not found; skipping examples (out/examples is deployed only if present)"
fi

step "stage the committed server tree and unit"
STAGE=out/stage
run rm -rf "$STAGE"
run mkdir -p "$STAGE"
if [ "$DRY" -eq 1 ]; then
    say "    + git archive HEAD server deploy/car-scratch.service | tar -x -C $STAGE"
else
    git archive HEAD server deploy/car-scratch.service | tar -x -C "$STAGE"
    printf 'CAR_SCRATCH_SHA=%s\n' "$SHA" > "$STAGE/REVISION.env"
fi
say "    (REVISION.env: CAR_SCRATCH_SHA=$SHA)"

if [ ! -d out/www ]; then
    if [ "$DRY" -eq 1 ]; then say "WARNING: out/www missing (build the editor first); a real deploy would refuse."
    else echo "out/www missing: build the editor first (docker buildx ... --output type=local,dest=out/www)" >&2; exit 1; fi
fi

# The editor bundle must be built from the very commit being deployed (never a stale out/www with a newer server).
BUILT=$(head -n 1 out/www/build-sha.txt 2>/dev/null | tr -d ' ' || true)
if [ -z "$BUILT" ] || [ "$BUILT" != "$SHA" ]; then
    MSG="out/www was built from '${BUILT:-unknown (no build-sha.txt)}', not from $SHA. Rebuild the editor:
  docker buildx build -f docker/editor.Dockerfile --target www --build-arg BUILD_SHA=\$(git rev-parse --short=12 HEAD) --output type=local,dest=out/www ."
    if [ "$DRY" -eq 1 ]; then say "WARNING: $MSG
a real deploy would refuse."
    else echo "refusing to deploy: $MSG" >&2; exit 1; fi
fi

# --- 3. upload -------------------------------------------------------------------------------------------
RSYNC="rsync -rlt --delete --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r -e 'ssh $SSH_OPTS'"
step "upload"
remote "mkdir -p $REL $BASE/releases ~/.config/systemd/user ~/.config/car-scratch ~/.local/share/car-scratch"
run_rsync() { say "    + $RSYNC $*"; [ "$DRY" -eq 1 ] || eval "$RSYNC" '"$@"'; }
run_rsync --exclude tests --exclude legacy --exclude __pycache__ --exclude '*.pyc' "$STAGE/server/" "$TARGET:$REL/server/"
run_rsync out/www/ "$TARGET:$REL/www/"
if [ -d out/examples ] || [ "$DRY" -eq 1 ]; then run_rsync out/examples/ "$TARGET:$REL/examples/"; fi
run_rsync out/source/ "$TARGET:$REL/source/"
run_rsync "$STAGE/REVISION.env" "$TARGET:$REL/REVISION.env"
run_rsync "$STAGE/deploy/car-scratch.service" "$TARGET:$REL/car-scratch.service"   # installed in step 4
remote "touch $REL"

# --- 4. switch + restart ---------------------------------------------------------------------------------
step "switch current -> $SHA, install the unit, restart"
if [ "$DRY" -eq 1 ]; then
    PREV="<previous release>"
    say "    + ssh $TARGET readlink ~/car-scratch/current   (remember for rollback)"
else
    PREV=$(ssh $SSH_OPTS "$TARGET" "readlink $BASE/current || true")
fi
UNIT='~/.config/systemd/user/car-scratch.service'
switch() { remote "ln -sfn releases/$1 $BASE/current.new && mv -T -f $BASE/current.new $BASE/current"; }
# Any failure from here on (switch, unit install, daemon-reload, enable, restart, health) takes the rollback path.
FAILED=""
switch "$SHA" || FAILED="could not switch the current symlink"
if [ -z "$FAILED" ]; then
    remote "mkdir -p ~/.config/systemd/user && rm -f $BASE/unit.prev && { [ ! -f $UNIT ] || cp -p $UNIT $BASE/unit.prev; } && cp $REL/car-scratch.service $UNIT && systemctl --user daemon-reload && systemctl --user enable car-scratch && systemctl --user restart car-scratch"         || FAILED="unit install / daemon-reload / enable / restart failed"
fi

# --- 5. health check -------------------------------------------------------------------------------------
PROBE='python3 -c "import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:8080/api/status\",timeout=2).read().decode())"'
if [ -z "$FAILED" ]; then
    step "health check (up to ~20 s): /api/status must report sha $SHA"
    OK=0
    if [ "$DRY" -eq 1 ]; then
        say "    + ssh $TARGET $PROBE   (retry every 2 s, 10 tries; grep sha $SHA)"
        OK=1
    else
        i=0
        while [ $i -lt 10 ]; do
            i=$((i + 1))
            if OUT=$(ssh $SSH_OPTS "$TARGET" "$PROBE" 2>/dev/null) && printf '%s' "$OUT" | grep -Eq "\"sha\": *\"$SHA"; then
                OK=1; break
            fi
            sleep 2
        done
    fi
    [ "$OK" -eq 1 ] || FAILED="health check failed"
fi

if [ -n "$FAILED" ]; then
    echo "DEPLOY FAILED for $SHA: $FAILED" >&2
    ssh $SSH_OPTS "$TARGET" "systemctl --user status car-scratch --no-pager -l | tail -n 15" >&2 || true
    if [ -n "$PREV" ]; then
        echo "rolling back to ${PREV##*/}" >&2
        switch "${PREV##*/}" || echo "WARNING: could not re-point current to ${PREV##*/}" >&2
        # reinstall the previous unit file if the new one replaced it, then restart on the previous release
        remote "if [ -f $BASE/unit.prev ] && ! cmp -s $BASE/unit.prev $UNIT; then cp -p $BASE/unit.prev $UNIT && systemctl --user daemon-reload; fi; systemctl --user restart car-scratch"             || echo "WARNING: the rollback restart failed; check the car service by hand" >&2
    else
        echo "no previous release to roll back to" >&2
    fi
    exit 1
fi

# --- 6. prune --------------------------------------------------------------------------------------------
step "keep the last $KEEP releases"
remote "cd $BASE/releases && cur=\$(basename \"\$(readlink ../current)\") && ls -1t | grep -vx \"\$cur\" | tail -n +$KEEP | xargs -r rm -rf --"
say "deployed $SHA"
