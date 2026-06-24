#!/bin/sh
# persona installer — downloads the latest released binary from GitHub and puts
# it on your PATH. Usage:
#   curl -fsSL https://raw.githubusercontent.com/amnesiadevelopment/persona/main/install.sh | sh
set -e

REPO="amnesiadevelopment/persona"            # <-- set to your github user/repo
ASSET="persona-linux-x86_64"
DEST="${PERSONA_INSTALL_DIR:-$HOME/.local/bin}"

echo "persona installer"

# only x86_64 linux is published for now
arch="$(uname -m)"
os="$(uname -s)"
if [ "$os" != "Linux" ] || [ "$arch" != "x86_64" ]; then
  echo "This installer currently supports Linux x86_64 only (got $os/$arch)." >&2
  echo "Download a build manually from https://github.com/$REPO/releases" >&2
  exit 1
fi

# resolve the latest release's download URL via the GitHub API
api="https://api.github.com/repos/$REPO/releases/latest"
echo "Looking up the latest release..."
url="$(curl -fsSL "$api" | grep -o "https://github.com/$REPO/releases/download/[^\"]*$ASSET" | head -n1)"
if [ -z "$url" ]; then
  echo "Could not find a '$ASSET' asset in the latest release of $REPO." >&2
  exit 1
fi

mkdir -p "$DEST"
tmp="$(mktemp)"
echo "Downloading $ASSET ..."
curl -fSL --progress-bar "$url" -o "$tmp"
chmod +x "$tmp"
mv "$tmp" "$DEST/persona"
echo "Installed to $DEST/persona"

# Desktop integration: icon in the app menu / taskbar.
icon_dir="$HOME/.local/share/icons/hicolor/256x256/apps"
app_dir="$HOME/.local/share/applications"
mkdir -p "$icon_dir" "$app_dir"
icon_url="https://raw.githubusercontent.com/$REPO/main/src/assets/icon.png"
if curl -fsSL "$icon_url" -o "$icon_dir/persona.png" 2>/dev/null; then
  cat > "$app_dir/persona.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=persona
Comment=Anti-detect browser manager
Exec=$DEST/persona
Icon=persona
Categories=Network;WebBrowser;
Terminal=false
EOF
  # refresh caches so the icon shows without a relogin (best-effort)
  update-desktop-database "$app_dir" 2>/dev/null || true
  gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
  echo "Added persona to your application menu."
fi

# PATH hint
case ":$PATH:" in
  *":$DEST:"*) ;;
  *)
    echo ""
    echo "NOTE: $DEST is not on your PATH. Add this to ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"\$PATH:$DEST\""
    echo "Then run:  persona"
    ;;
esac

echo ""
echo "Done. Launch with:  persona"
echo "All data is stored under ~/.persona  (engine downloads on first run)."
