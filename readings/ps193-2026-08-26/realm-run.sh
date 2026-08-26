#!/bin/bash
exec > /tmp/ps193/realm.log 2>&1
XROOT=/tmp/ps193/xroot
export LD_LIBRARY_PATH=$XROOT/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
export LIBGL_DRIVERS_PATH=/usr/lib/x86_64-linux-gnu/dri
export __EGL_VENDOR_LIBRARY_DIRS=$XROOT/usr/share/glvnd/egl_vendor.d
pkill -f "Xvfb :98" 2>/dev/null; sleep 1; rm -f /tmp/.X98-lock /tmp/.X11-unix/X98
setsid $XROOT/usr/bin/proot -b $XROOT/usr/bin/xkbcomp:/usr/bin/xkbcomp \
  $XROOT/usr/bin/Xvfb :98 -screen 0 1920x1080x24 -xkbdir $XROOT/usr/share/X11/xkb \
  < /dev/null > /tmp/ps193/xvfb-realm.log 2>&1 &
for i in $(seq 1 40); do [ -e /tmp/.X11-unix/X98 ] && break; sleep 0.5; done
export DISPLAY=:98
timeout 300 /tmp/ps195venv/bin/python /tmp/ps193/realm_probe.py
echo "RC=$?"
pkill -f "Xvfb :98" 2>/dev/null
