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

# ── PS-289: the durable journal ──────────────────────────────────────────────
# Everything else this script writes lands in `record/`, which is only readable
# afterwards if an upload step runs. A runner that DIES mid-phase never reaches
# one — measured twice (runs 33170172175 and 33748889046: artifacts
# total_count = 0, and `gh run view --log` → `log not found`). The journal is
# written line-by-line and fsynced OUTSIDE the workspace, so a phase that never
# finishes still says how far it got.
#
# Resolved as an absolute path BEFORE the `cd "$UCPL_DIR"` below, and guarded on
# existence: journaling must never be the reason a build fails.
JOURNAL_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ps289_journal.sh"
journal() {
  [ -x "$JOURNAL_SH" ] || return 0
  RECORD_DIR="$REC" "$JOURNAL_SH" "$@" >/dev/null 2>&1 || true
  return 0
}

LOG="${REC}/${PHASE}-${TREE}.log"
MEMLOG="${REC}/${PHASE}-${TREE}-memory.tsv"
TIMING="${REC}/${PHASE}-${TREE}-timing.txt"
PROV="${REC}/${PHASE}-${TREE}.provenance"

# ── provenance stamp: which run, which tag, produced this log ────────────────
# `record/` sits in $GITHUB_WORKSPACE, outside both checkouts, and a self-hosted
# runner does not wipe _work between dispatches. The jobs now zero `record/` on
# entry, but that alone cannot protect a log CARRIED BETWEEN JOBS (the control
# log the patched job reads): a file that is present is not thereby a file from
# THIS run. So every log is stamped with the tag and run that produced it, and
# ps218_attribute.sh REFUSES a control whose stamp does not match rather than
# trusting it.
#
# Absence was already handled ("CONTROL UNKNOWN"). Staleness is the same false
# attribution arriving through a present file instead of a missing one.
{
  echo "phase=${PHASE}"
  echo "tree=${TREE}"
  echo "ungoogled_tag=${UNGOOGLED_TAG:-unknown}"
  echo "github_run_id=${GITHUB_RUN_ID:-local}"
  echo "github_run_attempt=${GITHUB_RUN_ATTEMPT:-1}"
  echo "recorded=$(date -Is)"
} > "$PROV"

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

# ── PS-289 heartbeat ─────────────────────────────────────────────────────────
# The memory sampler above writes into `record/`, which a dead run cannot ship.
# This one writes a fsynced ALIVE line into the durable journal, carrying the
# phase's elapsed time, the log's size, and its last line. That is what turns
# "it died somewhere inside a ten-minute step" into "it was 9m40s in, the log
# was 3.1 MB, and the last thing it printed was <X>".
#
# 30 s, not the sampler's 5 s: this fsyncs on every write, so the interval is
# the cost it imposes on a build that survives. At 30 s a five-hour compile
# writes 600 short lines and 600 fsyncs of a file measured in kilobytes — far
# below the noise floor of a Chromium build — while bounding what a death can
# lose to half a minute.
HEARTBEAT_SECONDS="${PS289_HEARTBEAT_SECONDS:-30}"
start_heartbeat() {
  hb_phase_start="$1"
  (
    while true; do
      sleep "$HEARTBEAT_SECONDS"
      journal alive "$TREE" "$PHASE" "$(( $(date +%s) - hb_phase_start ))" "$LOG"
    done
  ) &
  HB_PID=$!
}

stop_heartbeat() {
  [ -n "${HB_PID:-}" ] && kill "$HB_PID" 2>/dev/null || true
  wait "${HB_PID:-}" 2>/dev/null || true
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

# PS-289: BEGIN is written before any work starts, so a run that dies in the
# first seconds of a phase is still distinguishable from one that never reached
# the phase at all.
journal begin "$TREE" "$PHASE"

start_mem_sampler
start_heartbeat "$phase_start"
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
      # is invoked directly in the same image.
      #
      # ⚠️ THIS BRANCH IS THE ONE LEAST ABLE TO AFFORD MISSING GUARDS. It is
      # taken precisely when memory is tight enough to need a reduced -j, i.e.
      # the run most likely to thrash or hang at link. Two things upstream's
      # entrypoint does (.github/scripts/build.sh:21-30) were dropped here and
      # are restored:
      #
      #   1. `timeout -k 5m -s INT 18000s` — without it the only bound was the
      #      job's `timeout-minutes: 900`, which reports as a JOB TIMEOUT rather
      #      than a build result. A hung link would arrive as infrastructure
      #      noise instead of the finding this ticket is paying for.
      #   2. the `-x chrome && -x chromedriver` check — ninja can exit 0 having
      #      produced nothing usable; the binaries on disk settle it.
      #
      # The env prefix that used to sit here (`_use_existing_image=1 CI=
      # _gha_final=true`) was INERT and is gone rather than left to mislead:
      # those three are read only by docker-build.sh, which this branch
      # deliberately does not invoke, and `docker run` does not forward host
      # variables without `-e`. Keeping a prefix that reads as "same
      # environment" while doing nothing is the sort of confident-but-false
      # signal this whole review was about.
      timeout -k 5m -s INT 18000s \
        docker run --rm -i -u "$(id -u):$(id -g)" -v "$(pwd):/repo" \
        chromium-builder:trixie-slim \
        bash -c "cd /repo/build/src && ninja ${NINJA_EXTRA} -C out/Default chrome chromedriver" \
        2>&1 | tee -a "$LOG"
      rc=${PIPESTATUS[0]}
      if [ "$rc" -eq 0 ]; then
        if [ -x build/src/out/Default/chrome ] && [ -x build/src/out/Default/chromedriver ]; then
          :
        else
          echo "ninja exited 0 but chrome/chromedriver are not both present and executable" | tee -a "$LOG"
          rc=1
        fi
      fi
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
stop_heartbeat

phase_end="$(date +%s)"
elapsed=$((phase_end - phase_start))

# PS-289: END, with the exit code, written durably. A BEGIN without a matching
# END is the signature of a runner death; a BEGIN followed by `END rc=1` is an
# ordinary build failure. The two used to be indistinguishable from outside.
journal end "$TREE" "$PHASE" "$rc"

# Peak memory across the whole phase, derived from the samples.
peak_used_kb="$(awk 'NR>1 && $2>m {m=$2} END {print m+0}' "$MEMLOG")"
peak_swap_kb="$(awk 'NR>1 && $4>m {m=$4} END {print m+0}' "$MEMLOG")"

# ── did a binary actually land? ──────────────────────────────────────────────
# Checked ON DISK rather than inferred from the exit code, because those two can
# disagree and the binary is the one that settles it. This also populates the
# job's `compiled` output, which was DECLARED in the workflow
# (`outputs.compiled: ${{ steps.compile.outputs.compiled }}`) while nothing ever
# wrote it — so it always resolved to the empty string, advertising a gate that
# did not exist. Nothing consumes it yet; making the declaration true is the
# minimal fix, and inventing a consumer would be a redesign this ticket forbids.
CHROME_BIN="$(pwd)/build/src/out/Default/chrome"
if [ -x "$CHROME_BIN" ]; then
  CHROME_STATE="present ($(stat -c %s "$CHROME_BIN" 2>/dev/null || echo '?') bytes)"
  COMPILED=true
else
  CHROME_STATE="ABSENT"
  COMPILED=false
fi
if [ "$PHASE" = "compile" ] && [ -n "${GITHUB_OUTPUT:-}" ] && [ "${GITHUB_OUTPUT}" != "/dev/null" ]; then
  echo "compiled=${COMPILED}" >> "$GITHUB_OUTPUT"
fi

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
  if [ "$PHASE" = "compile" ]; then
    echo "chrome_binary:    ${CHROME_STATE}"
  fi
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
