#!/usr/bin/env bash
# PS-193 loopback mechanism probe: own an Xvfb, run realm_probe.py, tear it down.
#
#   usage: realm-run.sh [out.json]
#
# Committed BESIDE the records and invoking the probe committed BESIDE IT, per
# the pattern in readings/ps186-2026-08-26/take-sweep.sh — see run.sh for why.
#
# OVERRIDABLE INPUTS (defaults in parentheses)
#   PYTHON  interpreter with playwright installed ($REPO_ROOT/.venv/bin/python)
#   XROOT   prefix holding the extracted Xvfb/proot/xkb tree (/tmp/ps193/xroot)
#   WORKDIR scratch dir for this runner's own logs           (/tmp/ps193)
#
# XROOT IS NOT COMMITTABLE — see the note in run.sh and EVIDENCE.md §7.
# This probe takes NO exit: it is loopback-only, which is why §3's realm
# finding is renderer- and network-independent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
XROOT="${XROOT:-/tmp/ps193/xroot}"
WORKDIR="${WORKDIR:-/tmp/ps193}"
PROBE="$SCRIPT_DIR/realm_probe.py"
OUT="${1:-$WORKDIR/realm-probe.json}"

mkdir -p "$WORKDIR"
exec > "$WORKDIR/realm.log" 2>&1

if [ ! -x "$PYTHON" ]; then
  echo "ENGINE_NOT_PROVISIONED: no interpreter at $PYTHON (override with PYTHON=)"
  exit 91
fi
if [ ! -f "$PROBE" ]; then
  echo "ENGINE_NOT_PROVISIONED: realm_probe.py missing beside runner at $PROBE"
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
  -xkbdir "$XROOT/usr/share/X11/xkb" < /dev/null > "$WORKDIR/xvfb-realm.log" 2>&1 &

for _ in $(seq 1 40); do
  [ -e /tmp/.X11-unix/X98 ] && break
  sleep 0.5
done
if [ ! -e /tmp/.X11-unix/X98 ]; then
  echo "ENGINE_NOT_PROVISIONED: Xvfb never bound :98 (XROOT=$XROOT)"
  cat "$WORKDIR/xvfb-realm.log" 2>/dev/null || true
  exit 90
fi
echo "XVFB_UP :98"
export DISPLAY=:98

RC=0
timeout 300 "$PYTHON" "$PROBE" --out "$OUT" || RC=$?
echo "RC=$RC"
pkill -f "Xvfb :98" 2>/dev/null || true
exit $RC
