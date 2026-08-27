#!/bin/bash
# PS-218 — drive the upstream build, in TWO SEPARATE PHASES, and time each.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE PHASE SPLIT IS THE POINT OF THIS SCRIPT
# ─────────────────────────────────────────────────────────────────────────────
# "Patch application and compilation are separate results and must be reported
# separately. Conflating them is the error this ticket exists to correct."
#
# Every figure the investigation produced measures whether a patch applies as
# TEXT (3 conflicts, 6 failed hunks at a one-major bump). NONE measures whether
# the result COMPILES. A patch that lands with nothing worse than line-offset
# drift can still break the build when a function it calls changed signature.
#
# Upstream's own CI entrypoint already splits exactly this way: `_prepare_only`
# runs fetch → patch → domain-substitute → gn gen and stops; the compile is a
# separate invocation. This script drives that same split rather than inventing
# one, so `prepare` failing and `compile` failing are two distinguishable
# outcomes with two distinguishable exit paths.
#
#   prepare  → fetch sources, apply patches, domain-substitute, gn gen
#   compile  → ninja
#
# ─────────────────────────────────────────────────────────────────────────────
# WALL-CLOCK AND PEAK MEMORY ARE DELIVERABLES, NOT DECORATION
# ─────────────────────────────────────────────────────────────────────────────
# The estimate under test is arithmetic: 130 core-hours ÷ core count, predicting
# ~4–5.5 h on 24 cores. Whether reality matches that division is itself a
# finding. So each phase is timed, and memory is SAMPLED THROUGHOUT rather than
# read once at the end — peak memory during LINKING is the number that matters
# and it is invisible to a single reading taken after the process exits.
set -euo pipefail

PHASE="${1:?usage: ps218_build.sh <prepare|compile> <unmodified|patched>}"
TREE="${2:?usage: ps218_build.sh <prepare|compile> <unmodified|patched>}"
UCPL_DIR="${UCPL_DIR:?UCPL_DIR must point at the ungoogled-chromium-portablelinux checkout}"
NINJA_JOBS="${NINJA_JOBS:-}"

REC="$(pwd)/record"
mkdir -p "$REC"

LOG="${REC}/${PHASE}-${TREE}.log"
MEMLOG="${REC}/${PHASE}-${TREE}-memory.tsv"
TIMING="${REC}/${PHASE}-${TREE}-timing.txt"

# ── memory sampler ───────────────────────────────────────────────────────────
# Sampled every 5 s for the whole phase. ungoogled's FAQ names exhausted memory
# at LINK time as the common build crash; a single reading taken afterwards
# would miss precisely that peak. Recorded as TSV so the maximum can be derived
# by a reader who does not trust ours.
start_mem_sampler() {
  printf 'epoch\tmem_used_kb\tmem_avail_kb\tswap_used_kb\n' > "$MEMLOG"
  (
    while true; do
      awk -v ts="$(date +%s)" '
        /^MemTotal:/     {t=$2}
        /^MemAvailable:/ {a=$2}
        /^SwapTotal:/    {st=$2}
        /^SwapFree:/     {sf=$2}
        END {printf "%s\t%d\t%d\t%d\n", ts, t-a, a, st-sf}
      ' /proc/meminfo >> "$MEMLOG" 2>/dev/null || true
      sleep 5
    done
  ) &
  MEM_PID=$!
}

stop_mem_sampler() {
  [ -n "${MEM_PID:-}" ] && kill "$MEM_PID" 2>/dev/null || true
  wait "${MEM_PID:-}" 2>/dev/null || true
}

# ── ninja parallelism ────────────────────────────────────────────────────────
# Upstream's build runs `ninja -C out/Default chrome chromedriver` with no -j,
# which means one job per core. If a reduction is needed to survive linking, it
# is passed in as an input and RECORDED — an unrecorded reduction makes this
# ticket's timing deliverable wrong in a way no reader can detect, which is
# exactly why it is threaded through explicitly instead of being hard-coded.
if [ -n "$NINJA_JOBS" ]; then
  export NINJA_STATUS="[%f/%t] "
  NINJA_EXTRA="-j${NINJA_JOBS}"
  echo "NINJA PARALLELISM REDUCED TO -j${NINJA_JOBS} (recorded)"
else
  NINJA_EXTRA=""
  echo "NINJA PARALLELISM: upstream default (unrestricted, one job per core)"
fi

cd "$UCPL_DIR"

phase_start="$(date +%s)"
echo "== PS-218 ${PHASE} / ${TREE} — started $(date -Is) ==" | tee "$LOG"

start_mem_sampler
set +e

case "$PHASE" in
  prepare)
    # `_prepare_only=true` + `CI` selects upstream's .github/scripts/build.sh,
    # which runs everything up to and including `gn gen` and then STOPS. That
    # is the patch-application result, isolated from any compilation.
    #
    # `-c` (clone) is deliberately NOT passed: the clone route needs depot_tools
    # and the ticket forbids installing it on the owner's machine. The default
    # route retrieves and unpacks the release tarball instead, which is exactly
    # why depot_tools is not needed.
    CI=1 _prepare_only=true GITHUB_OUTPUT="${GITHUB_OUTPUT:-/dev/null}" \
      ./scripts/docker-build.sh 2>&1 | tee -a "$LOG"
    rc=${PIPESTATUS[0]}
    ;;

  compile)
    # The compile proper. `_gha_final=true` so upstream's 5 h ninja timeout is
    # treated as a REAL failure rather than a "continue in the next run" —
    # this ticket wants one honest wall-clock figure, not a resumed build whose
    # elapsed time means nothing.
    if [ -n "$NINJA_EXTRA" ]; then
      # Reducing -j means bypassing upstream's fixed ninja line, so the compile
      # is invoked directly in the same image with the same environment.
      _use_existing_image=1 CI= _gha_final=true \
        docker run --rm -i -u "$(id -u):$(id -g)" -v "$(pwd):/repo" \
        chromium-builder:trixie-slim \
        bash -c "cd /repo/build/src && ninja ${NINJA_EXTRA} -C out/Default chrome chromedriver" \
        2>&1 | tee -a "$LOG"
      rc=${PIPESTATUS[0]}
    else
      CI=1 _prepare_only=false _gha_final=true GITHUB_OUTPUT="${GITHUB_OUTPUT:-/dev/null}" \
        ./scripts/docker-build.sh 2>&1 | tee -a "$LOG"
      rc=${PIPESTATUS[0]}
    fi
    ;;

  *)
    echo "unknown phase: $PHASE" >&2
    exit 2
    ;;
esac

set -e
stop_mem_sampler

phase_end="$(date +%s)"
elapsed=$((phase_end - phase_start))

# Peak memory across the whole phase, derived from the samples.
peak_used_kb="$(awk 'NR>1 && $2>m {m=$2} END {print m+0}' "$MEMLOG")"
peak_swap_kb="$(awk 'NR>1 && $4>m {m=$4} END {print m+0}' "$MEMLOG")"

{
  echo "phase:            ${PHASE}"
  echo "tree:             ${TREE}"
  echo "exit_code:        ${rc}"
  echo "started:          $(date -Is -d @${phase_start} 2>/dev/null || echo ${phase_start})"
  echo "ended:            $(date -Is -d @${phase_end} 2>/dev/null || echo ${phase_end})"
  echo "elapsed_seconds:  ${elapsed}"
  echo "elapsed_human:    $((elapsed / 3600))h $(((elapsed % 3600) / 60))m $((elapsed % 60))s"
  echo "cores_visible:    $(nproc)"
  # The reduction, stated in the timing record itself so it travels with the
  # number it affects rather than living in a separate file someone may not read.
  if [ -n "$NINJA_JOBS" ]; then
    echo "ninja_jobs:       ${NINJA_JOBS}  (REDUCED from the default — this changes the wall-clock figure)"
  else
    echo "ninja_jobs:       default (unrestricted, one job per core — NOT reduced)"
  fi
  echo "peak_mem_used_kb: ${peak_used_kb}"
  echo "peak_mem_used_gb: $(awk -v k="$peak_used_kb" 'BEGIN{printf "%.1f", k/1048576}')"
  echo "peak_swap_used_kb:${peak_swap_kb}"
  echo "peak_swap_used_gb:$(awk -v k="$peak_swap_kb" 'BEGIN{printf "%.1f", k/1048576}')"
  # The prediction under test, computed here so the comparison is in the record
  # rather than left as arithmetic for the reader.
  if [ "$PHASE" = "compile" ]; then
    echo "predicted_hours:  $(awk -v c="$(nproc)" 'BEGIN{printf "%.1f", 130/c}')  (130 core-hours / $(nproc) threads)"
    echo "actual_hours:     $(awk -v e="$elapsed" 'BEGIN{printf "%.1f", e/3600}')"
  fi
} | tee "$TIMING"

echo "timing -> ${TIMING}"
echo "memory samples -> ${MEMLOG}"

exit "$rc"
