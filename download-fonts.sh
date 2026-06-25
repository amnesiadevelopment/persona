#!/bin/bash
# Fetch the per-OS bundled fonts persona ships at runtime. They are NOT stored
# in git; this populates src/assets/fonts/ from the standard free font packages
# (croscore, Noto CJK/core, DejaVu — all OFL / Apache-2.0).
#
# On Debian/Ubuntu (incl. Whonix) this pulls the .deb packages without root and
# copies the exact faces persona expects. On other systems, install the same
# packages with your package manager and copy the faces listed below.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
FONTS="$ROOT/src/assets/fonts"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$FONTS"/{common,windows,macos,linux,base}

echo "Downloading font packages..."
cd "$TMP"
apt-get download fonts-croscore fonts-noto-cjk fonts-noto-core fonts-dejavu-core fonts-noto-color-emoji 2>/dev/null || {
  echo "apt-get download failed. Install these packages and copy the faces manually:"
  echo "  fonts-croscore fonts-noto-cjk fonts-noto-core fonts-dejavu-core fonts-noto-color-emoji"
  exit 1
}
for deb in *.deb; do dpkg-deb -x "$deb" extracted; done

find_face() { find "$TMP/extracted" -name "$1" -print -quit; }

cp "$(find_face NotoSansCJK-Regular.ttc)"  "$FONTS/common/"  2>/dev/null || true
cp "$(find_face NotoSerifCJK-Regular.ttc)" "$FONTS/common/"  2>/dev/null || true
cp "$(find_face NotoColorEmoji.ttf)"       "$FONTS/common/"  2>/dev/null || true
cp "$(find_face Arimo-Regular.ttf)"        "$FONTS/windows/" 2>/dev/null || true
cp "$(find_face Tinos-Regular.ttf)"        "$FONTS/windows/" 2>/dev/null || true
cp "$(find_face Cousine-Regular.ttf)"      "$FONTS/windows/" 2>/dev/null || true
cp "$(find_face NotoSans-Regular.ttf)"     "$FONTS/macos/"   2>/dev/null || true
for d in windows macos linux base; do
  cp "$(find_face DejaVuSans.ttf)"     "$FONTS/$d/" 2>/dev/null || true
  cp "$(find_face DejaVuSerif.ttf)"    "$FONTS/$d/" 2>/dev/null || true
  cp "$(find_face DejaVuSansMono.ttf)" "$FONTS/$d/" 2>/dev/null || true
done
# macos keeps only Serif + Noto Sans (distinct rendering); drop the rest there
rm -f "$FONTS/macos/DejaVuSans.ttf" "$FONTS/macos/DejaVuSansMono.ttf" 2>/dev/null || true

echo "Done. Installed faces:"
find "$FONTS" -name '*.ttf' -o -name '*.ttc' | sort
