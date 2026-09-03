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

TREE="${1:?usage: ps218_record_env.sh <unmodified|patched> [pre-prepare|post-prepare]}"
# WHICH PASS THIS IS. The container-memory probe below can only succeed once
# `prepare` has built the image, so this script is invoked TWICE per job:
#   pre-prepare  — the machine as found (CPU, WSL2 guest memory, disk, docker)
#   post-prepare — the same, PLUS the container/cgroup figures that now exist
# Defaulting to pre-prepare keeps the original single-argument call working.
PHASE_LABEL="${2:-pre-prepare}"
case "$PHASE_LABEL" in
  pre-prepare|post-prepare) ;;
  *) echo "unknown phase label: $PHASE_LABEL (expected pre-prepare|post-prepare)" >&2; exit 2 ;;
esac

UCPL_DIR="${UCPL_DIR:?UCPL_DIR must point at the ungoogled-chromium-portablelinux checkout}"

# ── PS-249: host identity must never reach the PUBLIC repository ─────────────
# amnesiadevelopment/persona is public, so Actions logs AND workflow artifacts
# are world-readable. This record used to publish the runner's hostname and its
# exact CPU model — an identifiable personal workstation. Persona exists to stop
# a browser disclosing the machine behind it; publishing the maintainer's own
# machine is that same leak one layer up.
#
# The label definition is SHARED with ps218_verify_control.sh rather than copied.
# That is not tidiness: this script WRITES the record and that one READS two of
# them and compares. When only the writer was changed, a control recorded before
# the fix held raw values while this run emitted pseudonyms, so the comparator
# reported DIFFERS and refused every borrow of every existing control. One
# definition means the writer and the reader cannot drift apart.
#
# See scripts/ps218_host_id.sh for why a salted digest, and not a constant label,
# is the only option that keeps the borrow check both working and honest.
_PS218_HOST_ID_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ps218_host_id.sh"
# shellcheck source=scripts/ps218_host_id.sh
. "$_PS218_HOST_ID_LIB"

REC="record"
mkdir -p "$REC"
# The two passes write SEPARATE files rather than one overwriting the other:
# the pre-prepare reading is the machine as the job found it, and losing it
# would remove the baseline the post-prepare figures are read against.
if [ "$PHASE_LABEL" = "post-prepare" ]; then
  OUT="$REC/environment-${TREE}-post-prepare.txt"
else
  OUT="$REC/environment-${TREE}.txt"
fi

{
  echo "# PS-218 build environment — tree: ${TREE}"
  echo "# pass: ${PHASE_LABEL}"
  echo "# recorded: $(date -Is)"
  # PS-249: `uname -a` field 2 is the NODENAME (the hostname) — that field, and
  # only that field, is replaced. Everything else `uname -a` prints is kernel
  # release, kernel version, architecture and OS family, all of which the ticket
  # keeps because they make the readings interpretable and identify no machine.
  #
  # THE FIELD POSITIONS ARE A CONTRACT, NOT A FORMATTING CHOICE.
  # `ps218_verify_control.sh` reads this line with
  #     sed -n 's/^# host: //p' | awk '{print $i}'
  # taking i=2 for the nodename and i=3 for the kernel release. Rebuilding the
  # line from the same five components in the same order keeps that parser
  # working unchanged; dropping or reordering a field would silently shift the
  # kernel into the hostname slot and make the borrow check compare the wrong
  # things while still reporting success.
  echo "# host: $(uname -s) $(pseudonymise "$(uname -n)") $(uname -r) $(uname -v) $(uname -m) $(uname -o)"
  echo

  echo "== CPU as the RUNNER sees it =="
  # This runner is a hybrid-core desktop part: its performance and efficiency
  # cores are NOT equal, so work scheduled as though every thread were identical
  # will have stragglers, and a wall-clock longer than the
  # 130-core-hours ÷ cores prediction may be a property of the hardware rather
  # than a fault. Recording the topology is what lets a reader tell those two
  # apart instead of assuming either.
  #
  # PS-249: this comment used to name the exact retail CPU model in prose. That
  # is host identity COMMITTED to a public repository — it leaks with no run at
  # all, so scrubbing only the runtime output would have left it in place. The
  # asymmetry it explains is what matters here, not the part number.
  echo "nproc:            $(nproc)"
  echo "nproc --all:      $(nproc --all)"

  # PS-249: the CPU model is pseudonymised; the CAPACITY figures are kept.
  #
  # `Model name:` is preserved as a FIELD because `ps218_verify_control.sh`
  # (PS-244) reads it — `grep -iE '^[[:space:]]*model name[[:space:]]*:'` — to
  # refuse a control built on different hardware. Deleting the line would make
  # that check read an empty value (`is_readable()` refuses, so every borrow
  # fails); replacing it with a constant would make it compare a constant to
  # itself and pass for every host. The pseudonym is the only option that keeps
  # the check both WORKING and HONEST.
  echo "Model name:       $(pseudonymise "$(lscpu 2>/dev/null | sed -nE 's/^[[:space:]]*[Mm]odel name[[:space:]]*:[[:space:]]*//p' | head -1)")"

  # ⚠️ THE FIELDS ARE NOW LISTED EXPLICITLY, NOT MATCHED BY REGEX.
  # The previous pattern was `'model name|^cpu\(s\)|thread|core|socket|mhz'`,
  # and the bare `core` alternative also matches the `Flags:` line — every
  # modern x86 flag list contains `perfctr_core` — so the whole CPU flag string
  # was published too. That is a far narrower fingerprint than a model name: the
  # flag set pins microarchitecture, stepping and errata state. It was not in
  # the ticket's two cited lines; an explicit allow-list is what makes the leak
  # impossible to reintroduce by widening a pattern.
  #
  # Every field below is a COUNT or a CLOCK. None of them identifies a machine,
  # and all of them are load-bearing for reading the build's timing and OOM
  # behaviour, which the ticket requires be kept.
  lscpu 2>/dev/null | grep -E '^(CPU\(s\)|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|CPU max MHz|CPU min MHz|Architecture|Byte Order):' || true
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
  #
  # ⚠️ THIS BLOCK IS REACHABLE ONLY AFTER `prepare` HAS BUILT THE IMAGE.
  # The script runs once BEFORE prepare (to capture the machine as found) and
  # again AFTER it (to capture the container). On the first pass the image does
  # not exist yet and the else-branch fires; on the second it does. Before this
  # second invocation existed, the else-branch was the ONLY reachable branch on
  # a clean runner, so the file promised a follow-up probe that never happened
  # and the record claimed three levels while only ever holding one.
  if docker image inspect chromium-builder:trixie-slim >/dev/null 2>&1; then
    docker run --rm chromium-builder:trixie-slim bash -c '
      echo "nproc: $(nproc)"
      grep -E "MemTotal|SwapTotal" /proc/meminfo
      for f in /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory/memory.limit_in_bytes; do
        [ -r "$f" ] && echo "cgroup $f: $(cat $f)"
      done
    ' 2>&1 || echo "(container probe failed)"
  elif [ "$PHASE_LABEL" = "pre-prepare" ]; then
    echo "(build image does not exist yet — this is the PRE-PREPARE pass;"
    echo " the container figures are recorded in environment-${TREE}-post-prepare.txt)"
  else
    echo "(build image STILL absent after prepare — the image build did not"
    echo " succeed, so the container level genuinely cannot be read. This is a"
    echo " missing reading, NOT a reading of zero.)"
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
