#!/bin/bash
# PS-193 runner: own an Xvfb, run the census, tear it down.
# Self-logs because background processes die when the calling shell returns.
#
# usage: run.sh <mode> <out.json> <logfile> [wait_secs] [spoof_seed]
MODE="$1"; OUT="$2"; LOG="$3"; WAIT="${4:-90}"; SPOOF="${5:-}"
SPOOF_ARG=""
if [ -n "$SPOOF" ]; then SPOOF_ARG="--spoof-seed $SPOOF"; fi
exec > "$LOG" 2>&1

XROOT=/tmp/ps193/xroot
export LD_LIBRARY_PATH=$XROOT/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export LIBGL_DRIVERS_PATH=/usr/lib/x86_64-linux-gnu/dri
export __EGL_VENDOR_LIBRARY_DIRS=$XROOT/usr/share/glvnd/egl_vendor.d

pkill -f "Xvfb :98" 2>/dev/null
sleep 1
rm -f /tmp/.X98-lock /tmp/.X11-unix/X98

setsid $XROOT/usr/bin/proot -b $XROOT/usr/bin/xkbcomp:/usr/bin/xkbcomp \
  $XROOT/usr/bin/Xvfb :98 -screen 0 1920x1080x24 \
  -xkbdir $XROOT/usr/share/X11/xkb < /dev/null > /tmp/ps193/xvfb-$MODE.log 2>&1 &

for i in $(seq 1 40); do
  [ -e /tmp/.X11-unix/X98 ] && break
  sleep 0.5
done
if [ ! -e /tmp/.X11-unix/X98 ]; then
  echo "ENGINE_NOT_PROVISIONED: Xvfb never bound :98"
  cat /tmp/ps193/xvfb-$MODE.log
  exit 90
fi
echo "XVFB_UP :98"
export DISPLAY=:98

# The env credential is a DIFFERENT provider carrying a stale session token;
# unset it so the file channel is pinned and a failure is unambiguous.
env -u PERSONA_TEST_PROXY /tmp/ps195venv/bin/python /tmp/ps193/census.py \
    --mode "$MODE" --out "$OUT" --wait "$WAIT" $SPOOF_ARG
RC=$?
echo "CENSUS_RC=$RC"
pkill -f "Xvfb :98" 2>/dev/null
exit $RC
