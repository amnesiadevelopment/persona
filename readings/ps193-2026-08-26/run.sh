#!/usr/bin/env bash
# PS-193 runner: own an Xvfb, run the census, tear it down.
# Self-logs because background processes die when the calling shell returns.
#
#   usage: run.sh <mode> <out.json> <logfile> [wait_secs] [spoof_seed]
#
# Committed BESIDE the records and invoking the census script committed BESIDE
# IT, per the pattern in readings/ps186-2026-08-26/take-sweep.sh: a reading
# whose runner executes a scratch copy of its own instrument is not
# re-derivable, because the scratch copy is gone the moment the container is.
#
# OVERRIDABLE INPUTS (defaults in parentheses)
#   PYTHON  interpreter with playwright installed ($REPO_ROOT/.venv/bin/python)
#   XROOT   prefix holding the extracted Xvfb/proot/xkb tree (/tmp/ps193/xroot)
#   WORKDIR scratch dir for this runner's own Xvfb logs      (/tmp/ps193)
#
# XROOT IS NOT COMMITTABLE and that is a real limitation, stated rather than
# hidden: it is a tree of Debian .debs unpacked into a prefix (§7 of
# EVIDENCE.md describes how it is built — this container has no root, so Xvfb
# cannot be installed normally). Re-deriving this reading means rebuilding that
# prefix first and pointing XROOT at it. Everything else here is committed.
set -euo pipefail

MODE="${1:?usage: run.sh <mode> <out.json> <logfile> [wait_secs] [spoof_seed]}"
OUT="${2:?usage: run.sh <mode> <out.json> <logfile> [wait_secs] [spoof_seed]}"
LOG="${3:?usage: run.sh <mode> <out.json> <logfile> [wait_secs] [spoof_seed]}"
WAIT="${4:-90}"
SPOOF="${5:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
XROOT="${XROOT:-/tmp/ps193/xroot}"
WORKDIR="${WORKDIR:-/tmp/ps193}"
CENSUS="$SCRIPT_DIR/census.py"

SPOOF_ARGS=()
if [ -n "$SPOOF" ]; then SPOOF_ARGS=(--spoof-seed "$SPOOF"); fi

mkdir -p "$WORKDIR"
exec > "$LOG" 2>&1

if [ ! -x "$PYTHON" ]; then
  echo "ENGINE_NOT_PROVISIONED: no interpreter at $PYTHON (override with PYTHON=)"
  exit 91
fi
if [ ! -f "$CENSUS" ]; then
  echo "ENGINE_NOT_PROVISIONED: census.py missing beside runner at $CENSUS"
  exit 92
fi

export LD_LIBRARY_PATH="$XROOT/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export LIBGL_DRIVERS_PATH=/usr/lib/x86_64-linux-gnu/dri
export __EGL_VENDOR_LIBRARY_DIRS="$XROOT/usr/share/glvnd/egl_vendor.d"

pkill -f "Xvfb :98" 2>/dev/null || true
sleep 1
rm -f /tmp/.X98-lock /tmp/.X11-unix/X98

setsid "$XROOT/usr/bin/proot" -b "$XROOT/usr/bin/xkbcomp:/usr/bin/xkbcomp" \
  "$XROOT/usr/bin/Xvfb" :98 -screen 0 1920x1080x24 \
  -xkbdir "$XROOT/usr/share/X11/xkb" < /dev/null > "$WORKDIR/xvfb-$MODE.log" 2>&1 &

for _ in $(seq 1 40); do
  [ -e /tmp/.X11-unix/X98 ] && break
  sleep 0.5
done
if [ ! -e /tmp/.X11-unix/X98 ]; then
  echo "ENGINE_NOT_PROVISIONED: Xvfb never bound :98 (XROOT=$XROOT)"
  cat "$WORKDIR/xvfb-$MODE.log" 2>/dev/null || true
  exit 90
fi
echo "XVFB_UP :98"
export DISPLAY=:98

# The env credential is a DIFFERENT provider carrying a stale session token;
# unset it so the file channel is pinned and a failure is unambiguous.
RC=0
env -u PERSONA_TEST_PROXY "$PYTHON" "$CENSUS" \
    --mode "$MODE" --out "$OUT" --wait "$WAIT" "${SPOOF_ARGS[@]}" || RC=$?
echo "CENSUS_RC=$RC"
pkill -f "Xvfb :98" 2>/dev/null || true
exit $RC
