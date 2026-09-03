#!/bin/bash
# PS-289 — DURABLE EVIDENCE FOR A RUN THAT LOSES ITS RUNNER MID-STEP.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE DEFECT THIS CLOSES
# ─────────────────────────────────────────────────────────────────────────────
# `engine-trial-build.yml` reads as though its evidence is protected. The long
# steps carry `continue-on-error: true` and every record-upload step carries
# `if: always()`. Both are real, and NEITHER of them fires when the RUNNER dies:
# a dead runner never reaches an upload step at all, and GitHub never flushes
# the job's log blob.
#
# Measured, twice. Run 33748889046 (2026-09-03) and run 33170172175
# (2026-08-28) both lost the runner inside the `prepare` step. Afterwards:
#
#     artifacts total_count = 0
#     gh run view --log  →  log not found: 100627638148
#
# Ten minutes of work — eight completed steps including a 5 GB checkout and a
# verified borrowed control — and NOTHING readable. The dropouts themselves were
# a host condition (a WSL2 idle timeout powering the VM off underneath the
# runner) and were fixed host-side; that is not this repository's territory and
# is not what this script addresses. What IS ours is that the blackout was
# total. The next dropout — a power cut, a kernel panic, a network drop — costs
# exactly the same blackout on a multi-hour Chromium compile, and that is an
# expensive thing to learn nothing from.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY A JOURNAL RATHER THAN A BETTER UPLOAD
# ─────────────────────────────────────────────────────────────────────────────
# Every upload-shaped remedy shares the defect it is meant to fix: it runs at
# the END. A step that runs at the end cannot run when the process is killed
# before the end. So the evidence has to be written AS IT IS PRODUCED, to a
# place that outlives both the process and the workspace, with each write
# actually pushed to disk rather than left in the page cache for a VM that is
# about to be powered off.
#
# Hence two properties, and they are the whole design:
#
#   1. WRITTEN INCREMENTALLY AND FSYNCED. Every line is appended and then
#      `sync`ed. A death loses at most the interval since the last heartbeat,
#      not the run.
#
#   2. WRITTEN OUTSIDE $GITHUB_WORKSPACE. `record/` already survives a runner
#      death on disk — but the NEXT dispatch's zeroing step deletes it, which is
#      correct (a stale control is the PS-192 shape this project has been bitten
#      by) and is also what turns "recoverable" into "gone". The journal root
#      therefore lives outside the workspace, where neither `actions/checkout`
#      nor the zeroing step can reach it.
#
# ─────────────────────────────────────────────────────────────────────────────
# AND THE SALVAGE, WHICH IS WHAT MAKES IT READABLE FROM GITHUB
# ─────────────────────────────────────────────────────────────────────────────
# A journal on the owner's disk answers the question for the owner. `salvage`
# answers it for everyone else: before zeroing `record/`, it copies the previous
# dispatch's leftovers — its logs, timings, environment records, and any journal
# with no END line — into THIS run's `record/salvaged/`, from where the existing
# `if: always()` upload ships them as an ordinary artifact.
#
# ⚠️ SALVAGED MATERIAL IS QUARANTINED, DELIBERATELY. It lands under
# `record/salvaged/` and never at the top level, because every consumer in this
# repo reads exact top-level paths (`record/compile-patched.log`,
# `record/environment-patched.txt`, `control/`). A salvaged file that landed
# beside a live one would be a stale control presented as this run's — the exact
# false attribution the zeroing step exists to prevent. Salvage must never buy
# durability at the cost of that guarantee, so the quarantine is not tidiness.
set -euo pipefail

RUN_ID="${GITHUB_RUN_ID:-local}"
RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"

# The journal root. Outside $GITHUB_WORKSPACE on purpose — see above. Override
# is provided for tests and for a runner whose HOME is not durable.
JOURNAL_ROOT="${PS289_JOURNAL_ROOT:-${HOME:-/tmp}/.persona-build-journal}"
RUN_DIR="${JOURNAL_ROOT}/${RUN_ID}-${RUN_ATTEMPT}"
JOURNAL="${RUN_DIR}/journal.txt"

# Where the in-workspace copy goes, so a run that DOES survive ships its journal
# in the ordinary artifact too.
RECORD_DIR="${RECORD_DIR:-record}"

# Salvaged logs can be very large (a Chromium compile log runs to tens of MB).
# Anything above this is truncated to a head and a tail with the drop stated —
# a bounded artifact that says where the build got to beats an unbounded one
# nobody downloads.
SALVAGE_MAX_BYTES="${PS289_SALVAGE_MAX_BYTES:-4194304}"
SALVAGE_HEAD_LINES=400
SALVAGE_TAIL_LINES=2000

# Journals are kept this long and then pruned. Matches the 30-day retention of
# the record artifact, so the durable copy and the uploaded copy expire together
# rather than one quietly outliving the other.
JOURNAL_RETENTION_DAYS="${PS289_JOURNAL_RETENTION_DAYS:-30}"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# ── how a BEGIN / END line is RECOGNISED ─────────────────────────────────────
# `_append` writes `<timestamp>  BEGIN  <tree>/<label>` with a fixed number of
# padding spaces. Two separate readers count those lines (`_previous_run_finished`
# and the orphan sweep in `salvage`), and both used to hard-code the exact
# padding — `'  BEGIN  '`, `'  END    '`. That is a silent coupling between the
# writer's cosmetics and the reader's meaning, and it fails DESTRUCTIVELY: change
# the spacing and every journal reads as zero BEGINs and zero ENDs, i.e. every
# dead run reads as FINISHED and its record is deleted unread.
#
# So the patterns live here, once, and are anchored to the line's STRUCTURE
# rather than its whitespace: the keyword is the first token after the
# timestamp. Anchoring matters as much as looseness — an ALIVE heartbeat carries
# the build's `last="…"` output verbatim, so a bare `[[:space:]]END[[:space:]]`
# would be satisfied by a compiler printing the word END and would count a
# phase as ended that never was.
JOURNAL_BEGIN_MATCH='^[^[:space:]]+[[:space:]]+BEGIN[[:space:]]'
JOURNAL_END_MATCH='^[^[:space:]]+[[:space:]]+END[[:space:]]'

# ── the one primitive that matters ───────────────────────────────────────────
# Append, then push to disk. `sync FILE` is the whole point of this function: a
# line sitting in the page cache of a VM that is about to be powered off is a
# line that was never written. Failures are swallowed — journaling must never be
# the reason a build fails, since it exists only to describe one.
_append() {
  mkdir -p "$RUN_DIR" 2>/dev/null || return 0
  printf '%s\n' "$*" >> "$JOURNAL" 2>/dev/null || return 0
  sync "$JOURNAL" 2>/dev/null || sync 2>/dev/null || true

  # Mirror into the workspace record so a SURVIVING run's artifact carries the
  # journal as well. Best-effort: the mirror is a convenience, the durable copy
  # above is the guarantee.
  if [ -d "$RECORD_DIR" ]; then
    cp "$JOURNAL" "${RECORD_DIR}/journal-${RUN_ID}-${RUN_ATTEMPT}.txt" 2>/dev/null || true
  fi
  return 0
}

_header_if_new() {
  [ -f "$JOURNAL" ] && return 0
  mkdir -p "$RUN_DIR" 2>/dev/null || return 0
  {
    echo "# PS-289 — durable build journal"
    echo "#"
    echo "# Written line-by-line and fsynced as the build proceeds, OUTSIDE the"
    echo "# workspace, so that a run whose runner dies mid-step still says how far"
    echo "# it got. READ IT AS FOLLOWS:"
    echo "#"
    echo "#   BEGIN <tree>/<label>            the phase started"
    echo "#   ALIVE <tree>/<label> elapsed=…  the phase was still running then"
    echo "#   MARK  <tree> …                  a milestone between phases"
    echo "#   END   <tree>/<label> rc=<n>     the phase finished, with its exit code"
    echo "#"
    echo "# A BEGIN with no matching END means the phase NEVER FINISHED — the last"
    echo "# ALIVE line bounds how far it got. That is a different statement from"
    echo "# 'the phase failed', and this file is what keeps the two apart."
    echo "#"
    echo "# run:      ${RUN_ID}"
    echo "# attempt:  ${RUN_ATTEMPT}"
    echo "# created:  $(now)"
    echo "# host:     $(uname -n 2>/dev/null || echo unknown)"
  } > "$JOURNAL" 2>/dev/null || return 0
  sync "$JOURNAL" 2>/dev/null || true
  return 0
}

_prune() {
  [ -d "$JOURNAL_ROOT" ] || return 0
  find "$JOURNAL_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -mtime "+${JOURNAL_RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true
  return 0
}

# ── copy one file into the salvage area, truncating an oversized log ──────────
# ⚠️ EVERY VARIABLE HERE IS `local`, AND THAT IS LOAD-BEARING. Bash variables are
# global by default, so an earlier version assigning a bare `dest="$2"` here
# overwrote the CALLER's `dest` — the salvage loop's destination directory — and
# every file after the first was written INSIDE the previous file's path
# (`…/b.log/a.provenance/c.txt`). The first file salvaged fine, so the feature
# looked to work while silently recovering one file out of N. A dead run's whole
# record collapsing to its alphabetically-last log is precisely the loss this
# script exists to prevent, so the scoping is not style.
_salvage_file() {
  local src="$1"
  local dest="$2"
  local bytes total dropped
  mkdir -p "$(dirname "$dest")" 2>/dev/null || return 0

  bytes="$(wc -c < "$src" 2>/dev/null || echo 0)"
  if [ "$bytes" -le "$SALVAGE_MAX_BYTES" ]; then
    cp "$src" "$dest" 2>/dev/null || true
    return 0
  fi

  total="$(wc -l < "$src" 2>/dev/null || echo 0)"
  dropped=$(( total - SALVAGE_HEAD_LINES - SALVAGE_TAIL_LINES ))
  [ "$dropped" -lt 0 ] && dropped=0
  {
    head -n "$SALVAGE_HEAD_LINES" "$src" 2>/dev/null || true
    echo
    echo "[PS-289 SALVAGE] ---------------------------------------------------"
    echo "[PS-289 SALVAGE] TRUNCATED. Original: ${bytes} bytes, ${total} lines."
    echo "[PS-289 SALVAGE] ${dropped} middle lines were dropped to keep the"
    echo "[PS-289 SALVAGE] recovered artifact bounded. The head and the TAIL are"
    echo "[PS-289 SALVAGE] both kept, and the tail is where a dead run stopped."
    echo "[PS-289 SALVAGE] ---------------------------------------------------"
    echo
    tail -n "$SALVAGE_TAIL_LINES" "$src" 2>/dev/null || true
  } > "$dest" 2>/dev/null || true
  return 0
}

# Which run produced the material currently sitting in `record/`? Read from the
# provenance stamps ps218_build.sh writes. Absent stamps are reported as
# `unknown` rather than guessed — a dispatch that died BEFORE prepare has no
# stamp at all, and that is exactly the case this must not silently mislabel.
_owning_run_of_record() {
  local prov id found="" other=0
  for prov in "${RECORD_DIR}"/*.provenance; do
    [ -f "$prov" ] || continue
    id="$(sed -n 's/^github_run_id=//p' "$prov" 2>/dev/null | head -1)"
    [ -n "$id" ] || continue
    if [ -z "$found" ]; then
      found="$id"
    elif [ "$id" != "$found" ]; then
      other=1
    fi
  done
  # ⚠️ READ EVERY STAMP, NOT THE FIRST. Stopping at the first stamp the glob
  # yielded made the label a coin-flip in exactly the situation salvage exists
  # to meet: leftovers from more than one dispatch. `mixed` is reported rather
  # than one of the ids picked arbitrarily, because a directory named
  # `record-from-run-555` that also contains run 554's bytes is a worse answer
  # than one that says it does not know. It is also the SAFE answer for the
  # gate: `mixed` matches no journal directory, so `_previous_run_finished`
  # says "not finished" and the material is kept.
  if [ -z "$found" ]; then
    echo "unknown"
  elif [ "$other" -eq 1 ]; then
    echo "mixed"
  else
    echo "$found"
  fi
}

# Did the run that owns the leftover `record/` FINISH its phases?
#
# WHY THIS GATE EXISTS. Without it every dispatch salvages its predecessor's
# record — including a predecessor that completed normally and uploaded that
# very record as its own artifact. The evidence would then exist twice, and the
# second copy would be filed under `salvaged/`, which is the directory that says
# "this run died". A recovery notice on every ordinary run is a false signal
# that teaches a reader to ignore the notice on the rare run where it is true.
#
# The discriminator is the same one the journal was built to provide: a phase
# that BEGAN and never ENDED. A run with NO journal at all is treated as
# unfinished and IS salvaged — that is the pre-PS-289 dispatch, and the run that
# died before it could write anything, which are exactly the cases where losing
# the record costs the most. The default is deliberately toward keeping.
# ⚠️ THE LOOP MUST SEE EVERY ATTEMPT, AND AN EARLIER VERSION DID NOT. A run id
# has attempts (`555-1`, `555-2`), the provenance stamp carries the run id ONLY,
# and an unconditional `return 0` at the bottom of the loop body meant only the
# FIRST directory the glob yielded was ever examined. Glob order is lexical, so
# on the single most likely shape here — a long build re-dispatched, attempt 1
# finished, attempt 2 died — attempt 1 was read, the run was declared finished,
# and the DEAD attempt's record was deleted unread by the `rm -rf` below while
# the log announced "finished its phases and uploaded its own record". The gate
# failed in the destructive direction and said the opposite. Hence: no early
# return, and a `saw` flag so "no journal at all" still answers "not finished"
# (recover) rather than falling out of an empty loop as "finished".
_previous_run_finished() {
  local run="$1" d j begins ends saw=0
  [ -n "$run" ] && [ "$run" != "unknown" ] || return 1
  for d in "${JOURNAL_ROOT}/${run}-"*/; do
    j="${d}journal.txt"
    [ -f "$j" ] || continue
    saw=1
    begins="$(grep -Ec "$JOURNAL_BEGIN_MATCH" "$j" 2>/dev/null || true)"; begins="${begins:-0}"
    ends="$(grep -Ec "$JOURNAL_END_MATCH" "$j" 2>/dev/null || true)";     ends="${ends:-0}"
    # Any unfinished phase in ANY attempt means the run did not finish cleanly.
    [ "$begins" -gt "$ends" ] && return 1
  done
  [ "$saw" -eq 1 ]
}

CMD="${1:-}"
case "$CMD" in

  root) echo "$JOURNAL_ROOT" ;;
  path) echo "$JOURNAL" ;;

  # begin <tree> <label>
  begin)
    _header_if_new
    _append "$(now)  BEGIN  ${2:-job}/${3:-unnamed}"
    ;;

  # mark <tree> <text...>
  mark)
    _header_if_new
    tree="${2:-job}"
    shift 2 2>/dev/null || true
    _append "$(now)  MARK   ${tree}  $*"
    ;;

  # alive <tree> <label> <elapsed_seconds> [logfile]
  # The heartbeat. This is the line that turns "it died somewhere in a ten
  # minute step" into "it was ninety seconds into linking and the last thing it
  # printed was X".
  alive)
    tree="${2:-job}"; label="${3:-unnamed}"; elapsed="${4:-0}"; logfile="${5:-}"
    detail=""
    if [ -n "$logfile" ] && [ -f "$logfile" ]; then
      lb="$(wc -c < "$logfile" 2>/dev/null || echo 0)"
      last="$(tail -n 1 "$logfile" 2>/dev/null | tr -d '\r' | cut -c1-200)"
      detail="  log_bytes=${lb}  last=\"${last}\""
    fi
    _header_if_new
    _append "$(now)  ALIVE  ${tree}/${label}  elapsed=${elapsed}s${detail}"
    ;;

  # end <tree> <label> <rc>
  end)
    _header_if_new
    _append "$(now)  END    ${2:-job}/${3:-unnamed}  rc=${4:-?}"
    ;;

  # ── salvage: recover the PREVIOUS dispatch's evidence, then zero ──────────
  # This REPLACES the bare `rm -rf record` the jobs used to run. The zeroing
  # itself is unchanged in effect — nothing from an earlier dispatch remains at
  # the top level of `record/`, so ps218_attribute.sh's staleness guarantee is
  # untouched — but the bytes are carried into a quarantined subdirectory first
  # instead of being deleted unread.
  salvage)
    _prune
    stage=""
    prev_run="none"

    if [ -d "$RECORD_DIR" ] && [ -n "$(ls -A "$RECORD_DIR" 2>/dev/null || true)" ]; then
      prev_run="$(_owning_run_of_record)"
      if [ "$prev_run" = "$RUN_ID" ]; then
        # Our own material, from an earlier step of THIS run. Not salvage.
        prev_run="none"
      elif _previous_run_finished "$prev_run"; then
        # That run completed its phases and therefore reached its upload step —
        # its record is already a GitHub artifact. Re-shipping it under
        # `salvaged/` would file a healthy run as a dead one.
        echo "previous dispatch (run ${prev_run}) finished its phases and uploaded its own record; nothing to salvage"
        prev_run="none"
      else
        stage="${JOURNAL_ROOT}/.salvage-staging-$$"
        rm -rf "$stage" 2>/dev/null || true
        mkdir -p "$stage" 2>/dev/null || true
        cp -R "$RECORD_DIR"/. "$stage"/ 2>/dev/null || true
      fi
    fi

    # The zeroing, unchanged in effect.
    rm -rf "$RECORD_DIR"
    mkdir -p "$RECORD_DIR"

    if [ -n "$stage" ] && [ -n "$(ls -A "$stage" 2>/dev/null || true)" ]; then
      dest="${RECORD_DIR}/salvaged/record-from-run-${prev_run}"
      mkdir -p "$dest"
      # ⚠️ THE LOOP BODY RUNS IN A SUBSHELL. `find … | while read` puts the
      # while on the right of a pipe, so anything the body ASSIGNS is lost when
      # the pipe closes. It is harmless today only because `_salvage_file`
      # writes to disk and sets nothing the caller reads. Do NOT add a counter
      # here expecting to print it afterwards — it will read zero, which is why
      # the `orphans` counter below is deliberately NOT inside a pipe.
      find "$stage" -type f 2>/dev/null | while IFS= read -r f; do
        rel="${f#"$stage"/}"
        _salvage_file "$f" "${dest}/${rel}"
      done
      echo "salvaged the previous dispatch's record/ (run ${prev_run}) -> ${dest}"
    fi
    rm -rf "$stage" 2>/dev/null || true

    # Journals from earlier runs that never recorded an END for a phase they
    # BEGAN. Those are precisely the dead runs, and they are the reason this
    # script exists — so they ride out on the next dispatch's artifact.
    orphans=0
    if [ -d "$JOURNAL_ROOT" ]; then
      for d in "$JOURNAL_ROOT"/*/; do
        [ -d "$d" ] || continue
        name="$(basename "$d")"
        [ "$name" = "${RUN_ID}-${RUN_ATTEMPT}" ] && continue
        j="${d}journal.txt"
        [ -f "$j" ] || continue
        # `grep -c` prints a count and exits 1 when that count is zero, so the
        # `|| echo 0` idiom would emit TWO lines ("0\n0") and break the
        # comparison below. `|| true` keeps the single number grep already
        # printed.
        begins="$(grep -Ec "$JOURNAL_BEGIN_MATCH" "$j" 2>/dev/null || true)"
        ends="$(grep -Ec "$JOURNAL_END_MATCH" "$j" 2>/dev/null || true)"
        begins="${begins:-0}"; ends="${ends:-0}"
        [ "$begins" -le "$ends" ] && continue
        mkdir -p "${RECORD_DIR}/salvaged/journals"
        cp "$j" "${RECORD_DIR}/salvaged/journals/journal-${name}.txt" 2>/dev/null || true
        orphans=$((orphans + 1))
      done
    fi
    # Written as an `if` and not `[ … ] && echo …`: under `set -e` a trailing
    # AND-list whose test is false makes the whole command non-zero and kills
    # the script, which here would abort the salvage on the ordinary path where
    # there is nothing to salvage.
    if [ "$orphans" -gt 0 ]; then
      echo "salvaged ${orphans} unfinished journal(s) from earlier dispatches"
    fi

    if [ -d "${RECORD_DIR}/salvaged" ]; then
      {
        echo "# SALVAGED EVIDENCE — FROM AN EARLIER DISPATCH, NOT FROM THIS RUN"
        echo
        echo "Recovered by \`scripts/ps289_journal.sh salvage\` at $(now), at the"
        echo "start of run \`${RUN_ID}\` (attempt ${RUN_ATTEMPT})."
        echo
        echo "## ⚠️ Nothing in this directory describes THIS run"
        echo
        echo "\`record/\` is not cleaned between dispatches by anything except the"
        echo "zeroing step this salvage replaces, so a run whose runner DIED left its"
        echo "logs on disk with no way to reach them — no artifact was uploaded and"
        echo "GitHub never flushed the job log. These files are those leftovers,"
        echo "carried out on the next dispatch's artifact rather than deleted unread."
        echo
        echo "They are quarantined under \`salvaged/\` and are never placed beside"
        echo "this run's files, because every consumer here (\`ps218_attribute.sh\`,"
        echo "\`ps218_verify_control.sh\`, \`ps218_manifest.sh\`) reads exact top-level"
        echo "paths. A salvaged control sitting at the top level would be a stale"
        echo "control read as this run's — the false attribution the zeroing step"
        echo "exists to prevent."
        echo
        echo "## What is here"
        echo
        echo "- \`record-from-run-<id>/\` — the earlier dispatch's whole \`record/\`."
        echo "  \`<id>\` is read from its own provenance stamps; \`unknown\` means it"
        echo "  died before \`prepare\` wrote one, which is itself a finding about how"
        echo "  far it got."
        echo "- \`journals/\` — durable journals whose phases BEGAN and never ENDED."
        echo "  Read the last \`ALIVE\` line: it bounds where the run stopped."
        echo
        echo "Large logs are truncated head+tail with the drop stated in-line."
      } > "${RECORD_DIR}/salvaged/SALVAGE.md" 2>/dev/null || true
    fi

    _header_if_new
    _append "$(now)  MARK   job  workspace zeroed; salvage from previous dispatch: run=${prev_run} orphan_journals=${orphans}"

    # ── SAY IT WHERE A READER WILL ACTUALLY SEE IT ───────────────────────────
    # An artifact somebody has to know to download is only half a remedy. When a
    # dispatch recovers a dead one's evidence, that fact belongs on the run's
    # summary page, because the person looking for the lost run is looking at
    # GitHub — not at a file listing. Guarded and best-effort: the summary is a
    # convenience, the artifact is the guarantee.
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ] && [ -d "${RECORD_DIR}/salvaged" ]; then
      {
        echo "### ♻️ Recovered evidence from an earlier dispatch (PS-289)"
        echo
        echo "A previous run left \`record/\` behind without uploading it — the signature"
        echo "of a runner that died mid-step. Its evidence was recovered into this run's"
        echo "record artifact under \`salvaged/\`, rather than being deleted unread."
        echo
        echo "| what | value |"
        echo "|---|---|"
        echo "| previous run | \`${prev_run}\` |"
        echo "| unfinished journals recovered | ${orphans} |"
        echo
        if [ -d "${RECORD_DIR}/salvaged/journals" ]; then
          echo "Last recorded position of each unfinished run — a \`BEGIN\` with no \`END\`"
          echo "means the phase never finished, which is NOT the same as failing:"
          echo
          echo '```'
          for jf in "${RECORD_DIR}"/salvaged/journals/*.txt; do
            [ -f "$jf" ] || continue
            echo "--- $(basename "$jf") ---"
            grep -E '  (BEGIN|MARK|ALIVE|END) ' "$jf" 2>/dev/null | tail -6 || true
          done
          echo '```'
        fi
      } >> "$GITHUB_STEP_SUMMARY" 2>/dev/null || true
    fi
    ;;

  *)
    echo "usage: ps289_journal.sh <root|path|begin|mark|alive|end|salvage> ..." >&2
    exit 2
    ;;
esac
