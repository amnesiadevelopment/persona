#!/usr/bin/env bash
# The published AppImage, with --appimage-extract-and-run FIRST (the runtime
# consumes it and it must lead; the host has no FUSE). Same bytes, same binary
# — this wrapper adds one runtime flag and nothing else. It exists ONLY for the
# direct command-line reproductions, which name their binary by absolute path;
# the harness runs went through _engine_binary() unmodified.
exec /tmp/ps344/personium-152.0.7977.75-linux-x86_64.AppImage --appimage-extract-and-run "$@"
