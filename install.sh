#!/bin/sh
# persona installer — downloads the latest released binary from GitHub and puts
# it on your PATH. Usage:
#   curl -fsSL https://raw.githubusercontent.com/amnesiadevelopment/persona/main/install.sh | sh
set -e

REPO="amnesiadevelopment/persona"            # <-- set to your github user/repo
ASSET="persona-x86_64.AppImage"
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

# Flet (the UI runtime inside persona) calls the OS for the documents directory
# at startup via the `xdg-user-dir` binary. On minimal/headless systems (e.g.
# Whonix) that binary is missing and persona fails to start with
# "MissingPlatformDirectoryException". Make sure it's present and the XDG dirs
# exist. Best-effort — skipped if apt/sudo aren't available.
if ! command -v xdg-user-dir >/dev/null 2>&1; then
  echo "Setting up XDG user directories (needed by the UI runtime)..."
  if command -v sudo >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update >/dev/null 2>&1 || true
    sudo apt-get install -y --no-install-recommends xdg-user-dirs >/dev/null 2>&1 || true
  fi
fi
if command -v xdg-user-dirs-update >/dev/null 2>&1; then
  xdg-user-dirs-update --force >/dev/null 2>&1 || xdg-user-dirs-update >/dev/null 2>&1 || true
fi
# guarantee a Documents dir exists regardless
docs="$( (command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DOCUMENTS) || echo "$HOME/Documents")"
mkdir -p "$docs" 2>/dev/null || true

# resolve the latest release (tag + download URL) via the GitHub API
api="https://api.github.com/repos/$REPO/releases/latest"
echo "Looking up the latest release..."
meta="$(curl -fsSL "$api")"
url="$(printf '%s' "$meta" | grep -o "https://github.com/$REPO/releases/download/[^\"]*$ASSET" | head -n1)"
tag="$(printf '%s' "$meta" | grep -o '"tag_name"[ ]*:[ ]*"[^"]*"' | head -n1 | sed 's/.*"\([^"]*\)"$/\1/')"
if [ -z "$url" ]; then
  echo "Could not find a '$ASSET' asset in the latest release of $REPO." >&2
  exit 1
fi

mkdir -p "$DEST"
# Resumable download: over Tor/slow links the connection often drops mid-file,
# so retry with -C - (continue) until the whole file is here. We keep a stable
# partial file (not mktemp) so each attempt resumes instead of restarting.
tmp="$DEST/.persona.partial"
echo "Downloading persona ${tag:-latest} (resumable; safe to re-run if it drops) ..."
attempt=1
max=50
while [ "$attempt" -le "$max" ]; do
  # -C - resume, --retry handles transient errors, generous timeouts for Tor
  if curl -fL -C - --retry 5 --retry-delay 3 \
          --connect-timeout 60 --speed-time 60 --speed-limit 1024 \
          --progress-bar "$url" -o "$tmp"; then
    break
  fi
  rc=$?
  # curl 33 = server doesn't support resume; 416 = already complete -> treat as done
  if [ "$rc" = "33" ] || [ "$rc" = "22" ]; then
    # can't resume: wipe and retry from scratch a couple of times
    rm -f "$tmp"
  fi
  echo "  download interrupted (attempt $attempt/$max), retrying..."
  attempt=$((attempt + 1))
  sleep 3
done
if [ ! -s "$tmp" ]; then
  echo "Download failed after $max attempts. Try again later, or grab the file" >&2
  echo "manually from https://github.com/$REPO/releases/latest" >&2
  exit 1
fi
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
