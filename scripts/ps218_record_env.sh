#!/bin/bash
# PS-218 — record what the build environment ACTUALLY SEES, before building.
#
# WHY THIS EXISTS. The ticket's instruction is explicit: "WSL2's memory
# allocation is not the host's. Confirm what the build environment actually sees
# before concluding anything about memory." 54 GB configured in WSL2 is not the
# same claim as 54 GB visible inside the Docker container that runs the compile,
# and the compile is what exhausts memory at link time.
#
# So this records memory at THREE levels, separately, and never conflates them:
#   1. the runner (WSL2 guest) — what `free` reports to the job
#   2. the container         — what the build itself is limited to
#   3. cgroup limits         — the ceiling Docker actually enforces
#
# A reader who later sees an OOM at link time can tell from this file which of
# the three was the binding constraint, instead of guessing.
set -euo pipefail

TREE="${1:?usage: ps218_record_env.sh <unmodified|patched>}"
UCPL_DIR="${UCPL_DIR:?UCPL_DIR must point at the ungoogled-chromium-portablelinux checkout}"

REC="record"
mkdir -p "$REC"
OUT="$REC/environment-${TREE}.txt"

{
  echo "# PS-218 build environment — tree: ${TREE}"
  echo "# recorded: $(date -Is)"
  echo "# host: $(uname -a)"
  echo

  echo "== CPU as the RUNNER sees it =="
  # The i9-14900K is 8 performance + 16 efficiency cores. Work scheduled as
  # though all 32 threads were equal will have stragglers, so a wall-clock
  # longer than the 130-core-hours ÷ cores prediction may be a property of the
  # hardware rather than a fault. Recording the topology is what lets a reader
  # tell those two apart instead of assuming either.
  echo "nproc:            $(nproc)"
  echo "nproc --all:      $(nproc --all)"
  lscpu 2>/dev/null | grep -Ei 'model name|^cpu\(s\)|thread|core|socket|mhz' || true
  echo

  echo "== MEMORY as the RUNNER (WSL2 guest) sees it =="
  free -h || true
  echo
  grep -E 'MemTotal|MemAvailable|SwapTotal|SwapFree' /proc/meminfo || true
  echo

  echo "== DISK =="
  df -h . || true
  echo

  echo "== DOCKER =="
  docker version --format '{{.Server.Version}}' 2>/dev/null || echo "docker version unavailable"
  echo

  echo "== MEMORY as the BUILD CONTAINER sees it =="
  # This is the number that actually governs the compile, and the one the
  # ticket warns is not the same claim as the WSL2 figure above.
  if docker image inspect chromium-builder:trixie-slim >/dev/null 2>&1; then
    docker run --rm chromium-builder:trixie-slim bash -c '
      echo "nproc: $(nproc)"
      grep -E "MemTotal|SwapTotal" /proc/meminfo
      for f in /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory/memory.limit_in_bytes; do
        [ -r "$f" ] && echo "cgroup $f: $(cat $f)"
      done
    ' 2>&1 || echo "(container probe failed)"
  else
    echo "(build image not built yet — probed after the image exists in the prepare step)"
  fi
  echo

  echo "== SOURCE PROVENANCE =="
  # Pinning exactly which tree was built. A report that cannot name the tag and
  # Chromium version it used cannot be acted on without re-running everything.
  ( cd "$UCPL_DIR" && echo "portablelinux HEAD: $(git rev-parse HEAD 2>/dev/null || echo unknown)" )
  ( cd "$UCPL_DIR" && echo "portablelinux tag:  $(git describe --tags --always 2>/dev/null || echo unknown)" )
  if [ -f "$UCPL_DIR/ungoogled-chromium/chromium_version.txt" ]; then
    echo "chromium version:   $(cat "$UCPL_DIR/ungoogled-chromium/chromium_version.txt")"
  fi
  if [ -f "$UCPL_DIR/ungoogled-chromium/revision.txt" ]; then
    echo "ungoogled revision: $(cat "$UCPL_DIR/ungoogled-chromium/revision.txt")"
  fi
} | tee "$OUT"

echo "recorded environment -> $OUT"
