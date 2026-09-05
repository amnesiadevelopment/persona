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
#
# ⚠️ `#` IS EXCLUDED FROM THE FIRST TOKEN, AND THAT ONE CHARACTER IS THE WHOLE
# DIFFERENCE BETWEEN THIS READER AND A DESTRUCTIVE ONE. `_header_if_new` writes
# a LEGEND into every journal — `#   BEGIN <tree>/<label>  the phase started`,
# and the matching `#   END   …` line. `#` is a non-space character, so an
# anchor of `^[^[:space:]]+` matches those legend lines and EVERY journal ever
# written starts life at begins=1, ends=1. That is invisible while a real phase
# is running (both counters gain one), and it bites on the journal with NO real
# phase lines — which is written by the exact failure this script exists for: a
# runner that dies after the `mark` milestones but before `ps218_build.sh`
# reaches `journal begin`. `1 > 1` is false, the run reads as FINISHED, and its
# record is deleted unread while the log says it "uploaded its own record".
# Excluding `#` makes the anchor mean "a timestamp", which is what this comment
# always claimed it meant.
JOURNAL_BEGIN_MATCH='^[^#[:space:]]+[[:space:]]+BEGIN[[:space:]]'
JOURNAL_END_MATCH='^[^#[:space:]]+[[:space:]]+END[[:space:]]'
# Any real (non-legend) event line, for reader-facing excerpts.
JOURNAL_EVENT_MATCH='^[^#[:space:]]+[[:space:]]+(BEGIN|MARK|ALIVE|END)[[:space:]]'

# ── the ONE predicate both readers use ───────────────────────────────────────
# `_previous_run_finished` (the gate that decides whether to salvage a stranded
# `record/`) and the orphan sweep (which decides whether to carry a journal out)
# ask the SAME question of a journal, so they must not answer it with two
# hand-rolled comparisons that can drift apart. A journal describes a run that
# FINISHED its phases only when it has at least one real BEGIN and an END for
# each of them. Both failure directions are unfinished:
#
#   begins == 0  → nothing ever started, so nothing finished. This is the dead
#                  run that never reached `journal begin`, and reading it as
#                  "finished" is the destructive answer.
#   begins > ends → a phase started and never ended: the classic dropout.
#
# Returns 0 (true) when the journal is UNFINISHED — i.e. when its material must
# be kept.
_journal_is_unfinished() {
  local j="$1" begins ends
  [ -f "$j" ] || return 0
  # `grep -c` prints a count and exits 1 when that count is zero, so the
  # `|| echo 0` idiom would emit TWO lines ("0\n0") and break the comparison.
  # `|| true` keeps the single number grep already printed.
  begins="$(grep -Ec "$JOURNAL_BEGIN_MATCH" "$j" 2>/dev/null || true)"; begins="${begins:-0}"
  ends="$(grep -Ec "$JOURNAL_END_MATCH" "$j" 2>/dev/null || true)";     ends="${ends:-0}"
  # `if`, not a trailing `[ … ] && return 0`: an AND-list whose test is false is
  # itself non-zero, and this function is also called from `|| continue` where
  # that would be read as a verdict rather than as a fall-through.
  if [ "$begins" -eq 0 ] || [ "$begins" -gt "$ends" ]; then
    return 0
  fi
  return 1
}

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

# Read ONE field of the provenance stamps ps218_build.sh writes, and say who
# owns the material currently sitting in `record/`. Absent stamps are reported
# as `unknown` rather than guessed — a dispatch that died BEFORE prepare has no
# stamp at all, and that is exactly the case this must not silently mislabel.
#
# ⚠️ READ EVERY STAMP, NOT THE FIRST. Stopping at the first stamp the glob
# yielded made the label a coin-flip in exactly the situation salvage exists to
# meet: leftovers from more than one dispatch. `mixed` is reported rather than
# one of the values picked arbitrarily, because a directory named
# `record-from-run-555` that also contains run 554's bytes is a worse answer
# than one that says it does not know. It is also the SAFE answer for the gate:
# `mixed` matches no journal directory, so `_previous_run_finished` says "not
# finished" and the material is kept.
#
# ⚠️ ONE READER, PARAMETERISED BY KEY — not two hand-rolled copies. The run id
# and the attempt are asked the same question of the same files, and two
# separate implementations of "read every stamp, report `mixed` on disagreement,
# `unknown` when absent" would be free to drift apart, which is the same reason
# `_journal_is_unfinished` was consolidated for the gate and the orphan sweep.
_owning_field_of_record() {
  local key="$1" prov val found="" other=0
  for prov in "${RECORD_DIR}"/*.provenance; do
    [ -f "$prov" ] || continue
    val="$(sed -n "s/^${key}=//p" "$prov" 2>/dev/null | head -1)"
    [ -n "$val" ] || continue
    if [ -z "$found" ]; then
      found="$val"
    elif [ "$val" != "$found" ]; then
      other=1
    fi
  done
  if [ -z "$found" ]; then
    echo "unknown"
  elif [ "$other" -eq 1 ]; then
    echo "mixed"
  else
    echo "$found"
  fi
}

_owning_run_of_record()     { _owning_field_of_record github_run_id; }
_owning_attempt_of_record() { _owning_field_of_record github_run_attempt; }

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
  local run="$1" d j saw=0
  [ -n "$run" ] && [ "$run" != "unknown" ] || return 1
  for d in "${JOURNAL_ROOT}/${run}-"*/; do
    j="${d}journal.txt"
    [ -f "$j" ] || continue
    saw=1
    # Any unfinished attempt means the run did not finish cleanly — including
    # an attempt whose journal holds no real phase line at all.
    _journal_is_unfinished "$j" && return 1
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
    # ⚠️ THE PRUNE RUNS FIRST, AND THAT IS A RETENTION DECISION WITH A COST.
    # A journal whose run died is carried out by the orphan sweep below — but
    # only if a dispatch happens within `JOURNAL_RETENTION_DAYS` of the death.
    # A self-hosted runner can sit idle for weeks, so a dropout followed by a
    # long quiet period loses its journal to the prune before any dispatch
    # carries it out. That is deliberate (the durable copy expires with the
    # artifact it mirrors) but it means the durable copy does NOT always outlive
    # the incident: it outlives it for 30 days.
    _prune
    # ⚠️ SWEEP UP A CARRY-FORWARD THAT NEVER LANDED. Between the `mv` and the
    # lay-down below, the previous dispatch's salvaged tree lives at
    # `${JOURNAL_ROOT}/.salvage-carried-$$` — outside `record/`, so no upload can
    # ship it, and hidden from the `*/` orphan glob, so no later dispatch finds
    # it. The window is milliseconds against a ten-minute step and a `mv` is the
    # right primitive precisely because it is near-atomic, but a death inside it
    # would strand those bytes on disk forever and leak the disk they sit on.
    # Recovering them is nearly free here: anything left from a PID that is no
    # longer us is laid back down under this run's `record/salvaged/` with the
    # rest, by the same code path, further down.
    inherited=()
    for stale in "${JOURNAL_ROOT}"/.salvage-carried-*; do
      [ -d "$stale" ] || continue
      [ "$stale" = "${JOURNAL_ROOT}/.salvage-carried-$$" ] && continue
      inherited+=("$stale")
    done
    stage=""
    carried=""
    prev_run="none"
    prev_attempt="unknown"
    salvaged_record=0
    carried_forward=0

    if [ -d "$RECORD_DIR" ] && [ -n "$(ls -A "$RECORD_DIR" 2>/dev/null || true)" ]; then
      prev_run="$(_owning_run_of_record)"
      prev_attempt="$(_owning_attempt_of_record)"
      # ⚠️ A RUN ID IS NOT A DISPATCH'S IDENTITY ON A PLATFORM WITH RE-RUNS, AND
      # AN EARLIER VERSION COMPARED IDS ALONE. GitHub keeps `github_run_id`
      # CONSTANT across attempts and increments `github_run_attempt` — that is
      # what the "Re-run jobs" button does, and it is how this ticket's own run
      # 33748889046 was re-run at 11:51Z. So on attempt 2 the leftovers of a
      # DEAD attempt 1 carry the same run id, an id-only comparison read them as
      # "our own material from an earlier step of THIS run", no staging copy was
      # taken, and the `rm -rf "$RECORD_DIR"` below destroyed them — while the
      # summary reported "no stranded `record/`".
      #
      # The failure INVERTED with how far the dead attempt got: a dispatch that
      # died in the first twenty seconds wrote no stamp, so it fell to the
      # `unknown` path and was recovered; one that survived the 5 GB checkout,
      # staged all 16 patches and then died four hours into the compile was
      # deleted, BECAUSE getting that far is what writes the stamp that tripped
      # the branch. The more expensive the loss, the more certain it was.
      #
      # Both terms are required for "ours", and the non-equal arms are chosen
      # DELIBERATELY rather than incidentally:
      #   attempt == ours  → genuinely our own material (the second job of a
      #                      `both` dispatch seeing its sibling's output). Skip.
      #   attempt differs  → a PREVIOUS attempt of this same run. Salvage it.
      #   `unknown`        → a stamp carrying a run id but no attempt, i.e. one
      #                      written before ps218_build.sh:80 began recording
      #                      the attempt. Cannot be shown to be ours, so it is
      #                      KEPT. The cost of being wrong here is our own
      #                      material quarantined under `salvaged/` and labelled
      #                      with our own run id — legible and harmless. The cost
      #                      of being wrong the other way is the deletion this
      #                      whole script exists to prevent.
      #   `mixed`          → leftovers spanning more than one attempt, so at
      #                      least one of them is not ours. KEPT, same reasoning.
      if [ "$prev_run" = "$RUN_ID" ] && [ "$prev_attempt" = "$RUN_ATTEMPT" ]; then
        # Our own material, from an earlier step of THIS run AND THIS attempt.
        # Not salvage.
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
        # ── the previous dispatch's OWN salvaged/ — LIFTED ASIDE, NOT DROPPED ──
        # `record/salvaged/` is itself part of `record/`, so copying `record/.`
        # wholesale nests the recovered tree one level deeper on every dispatch
        # — `salvaged/record-from-run-unknown/salvaged/record-from-run-unknown/…`
        # — until it is unreadable. That is not exotic: the PS-244 borrowed-
        # control refusal exits before any provenance stamp is written, so
        # consecutive refusals each leave an unprovenanced `record/` behind.
        #
        # ⚠️ BUT IT MUST NOT SIMPLY BE DELETED, AND AN EARLIER VERSION DELETED
        # IT. The comment justifying that read "the earlier material already
        # rode out on the artifact of the dispatch that recovered it" — which is
        # true only when the RECOVERING dispatch survived to reach its upload
        # step. This script exists for dispatches that do not. Chain it: A dies;
        # B salvages A into `record/salvaged/` and then B ALSO dies, so B never
        # uploads and A's bytes now exist in exactly ONE place on disk — inside
        # B's `record/salvaged/`. A dispatch C that dropped that directory as
        # "already shipped" would destroy A's environment record, staged patch
        # set and verified borrowed control, and would then post a recovery
        # notice naming only B. Two deaths in a row is the CURRENT normal on
        # this project's `persona-build` label (two heterogeneous machines
        # answer it; the dockerless macOS host kills `prepare` in ~20s), not a
        # tail case.
        #
        # So the tree is flattened by CARRYING FORWARD, not by discarding: the
        # previous salvage is moved out of the staging copy here, and laid back
        # down under THIS run's `record/salvaged/` below. The result is exactly
        # one level deep — the nesting nit is still fixed — and the recovery
        # chain survives an arbitrary run of consecutive deaths.
        carried="${JOURNAL_ROOT}/.salvage-carried-$$"
        rm -rf "$carried" 2>/dev/null || true
        if [ -d "${stage}/salvaged" ]; then
          mv "${stage}/salvaged" "$carried" 2>/dev/null || true
        fi
        # Belt and braces: if the `mv` could not happen (a cross-device staging
        # root, a permission problem) the directory must still not nest, so it
        # is removed only on the path where carrying it forward was impossible.
        rm -rf "${stage}/salvaged" 2>/dev/null || true
      fi
    fi

    # The zeroing, unchanged in effect.
    rm -rf "$RECORD_DIR"
    mkdir -p "$RECORD_DIR"

    # ── lay the carried-forward salvage back down, exactly one level deep ─────
    # Placed BEFORE this run's own copy so that on the (unlikely) collision of
    # two dispatches recovering the same run id, THIS dispatch's fresher bytes
    # win rather than the inherited ones.
    #
    # `$inherited` is the same material from a dispatch that DIED between its
    # own `mv` and this lay-down; it is laid down first, so a live carry-forward
    # for the same run id still overwrites it with the fresher copy.
    for stale in ${inherited+"${inherited[@]}"}; do
      [ -d "$stale" ] || continue
      mkdir -p "${RECORD_DIR}/salvaged" 2>/dev/null || true
      if cp -R "$stale"/. "${RECORD_DIR}/salvaged"/ 2>/dev/null; then
        carried_forward=1
        echo "recovered a stranded carry-forward staging directory ($(basename "$stale")) — a dispatch died mid-salvage"
        # Removed only when the bytes actually moved: this script's bookkeeping
        # is sound only when it is conditioned on the copy succeeding.
        rm -rf "$stale" 2>/dev/null || true
      fi
    done

    if [ -n "$carried" ] && [ -d "$carried" ]; then
      mkdir -p "${RECORD_DIR}/salvaged" 2>/dev/null || true
      cp -R "$carried"/. "${RECORD_DIR}/salvaged"/ 2>/dev/null || true
      # The inherited SALVAGE.md names the EARLIER dispatch's run id and
      # timestamp. It is regenerated below from this run's values, but drop it
      # first: if that write ever fails, no legend is a better answer than a
      # legend attributing this artifact to the wrong run.
      rm -f "${RECORD_DIR}/salvaged/SALVAGE.md" 2>/dev/null || true
      if [ -n "$(ls -A "${RECORD_DIR}/salvaged" 2>/dev/null || true)" ]; then
        carried_forward=1
        echo "carried forward an earlier dispatch's salvaged evidence (it was never uploaded — its recoverer died too)"
      fi
    fi
    rm -rf "$carried" 2>/dev/null || true

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
      salvaged_record=1
    fi
    rm -rf "$stage" 2>/dev/null || true

    # Journals from earlier runs that never recorded an END for a phase they
    # BEGAN — or that never recorded a BEGIN at all. Those are precisely the
    # dead runs, and they are the reason this script exists, so they ride out on
    # the next dispatch's artifact.
    #
    # ⚠️ EACH ONE IS CARRIED OUT EXACTLY ONCE, AND THE SENTINEL IS WHAT MAKES
    # THAT TRUE. Without it this sweep re-finds the same unfinished journals on
    # EVERY future dispatch until the 30-day prune, so a run whose predecessor
    # was perfectly healthy still creates `record/salvaged/` and still posts
    # "♻️ Recovered evidence from an earlier dispatch" to its summary — the
    # exact false signal the gate above exists to prevent, sustained for a
    # month after every dropout. The sentinel is a hidden file INSIDE the run's
    # journal directory, so it does not appear in the `*/` glob and is pruned
    # with the journal it belongs to.
    orphans=0
    orphan_names=""
    if [ -d "$JOURNAL_ROOT" ]; then
      for d in "$JOURNAL_ROOT"/*/; do
        [ -d "$d" ] || continue
        name="$(basename "$d")"
        [ "$name" = "${RUN_ID}-${RUN_ATTEMPT}" ] && continue
        j="${d}journal.txt"
        [ -f "$j" ] || continue
        [ -f "${d}.salvaged" ] && continue
        _journal_is_unfinished "$j" || continue
        mkdir -p "${RECORD_DIR}/salvaged/journals"
        # ⚠️ THE SENTINEL IS GATED ON THE COPY, NOT ON THE ATTEMPT. The `cp` is
        # deliberately non-fatal — journaling must never fail a build — but an
        # unconditional `: > .salvaged` afterwards burns the journal's ONE
        # chance to be carried out even when nothing was copied (an unreadable
        # journal passes `[ -f ]` and then fails `cp`). It would also count into
        # `orphans`, so the log line and the run summary would both claim a
        # recovery that did not happen. The sentinel must record a RESULT, which
        # is the same lesson as the carry-forward above: this script's
        # bookkeeping is only sound when it is conditioned on the bytes having
        # actually moved.
        if cp "$j" "${RECORD_DIR}/salvaged/journals/journal-${name}.txt" 2>/dev/null; then
          : > "${d}.salvaged" 2>/dev/null || true
          orphans=$((orphans + 1))
          orphan_names="${orphan_names}${orphan_names:+, }${name}"
        fi
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
        echo "- \`journals/\` — durable journals of runs that did not finish: a phase"
        echo "  that BEGAN and never ENDED, or a run that died before any phase began"
        echo "  at all. Read the last \`ALIVE\` line: it bounds where the run stopped."
        echo
        echo "This directory may hold evidence from MORE THAN ONE earlier dispatch."
        echo "When a dispatch recovers a dead run and then dies itself, it never"
        echo "reaches its upload step, so what it recovered exists only on disk; the"
        echo "next dispatch carries that material FORWARD rather than dropping it as"
        echo "\"already shipped\". The tree stays exactly one level deep either way."
        echo
        echo "Large logs are truncated head+tail with the drop stated in-line."
      } > "${RECORD_DIR}/salvaged/SALVAGE.md" 2>/dev/null || true
    fi

    _header_if_new
    _append "$(now)  MARK   job  workspace zeroed; salvage from previous dispatch: run=${prev_run} orphan_journals=${orphans} carried_forward=${carried_forward}"

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
        echo "A previous run left evidence behind without uploading it — the signature"
        echo "of a runner that died mid-step. It was recovered into this run's record"
        echo "artifact under \`salvaged/\`, rather than being deleted unread."
        echo
        echo "| what | value |"
        echo "|---|---|"
        # ⚠️ NAME WHAT WAS ACTUALLY RECOVERED. `prev_run` is reset to `none` on
        # the healthy-predecessor path, so reusing it here would print
        # "previous run: none" on a notice that only fires because a JOURNAL was
        # recovered — a reader following the notice would be told a recovery
        # happened from a run called "none", with the dead run's identity
        # nowhere on the page.
        if [ "$salvaged_record" -eq 1 ]; then
          # The attempt is named too, because a re-run keeps the run id: on the
          # "Re-run jobs" shape the row would otherwise print this run's own id
          # and read as though it recovered itself.
          echo "| record recovered from run | \`${prev_run}\` (attempt \`${prev_attempt}\`) |"
        else
          echo "| record recovered from run | none (no stranded \`record/\`) |"
        fi
        if [ "$orphans" -gt 0 ]; then
          echo "| unfinished journals recovered | ${orphans} (\`${orphan_names}\`) |"
        else
          echo "| unfinished journals recovered | 0 |"
        fi
        # A dispatch that recovered a dead run and then died itself never
        # uploaded what it recovered, so this run inherits it. Say so — a
        # reader who is told only about the LATEST death would otherwise never
        # learn that older material is in the same artifact.
        if [ "$carried_forward" -eq 1 ]; then
          echo "| earlier dispatch's salvage carried forward | yes (its recoverer died before uploading) |"
        fi
        echo
        if [ -d "${RECORD_DIR}/salvaged/journals" ]; then
          echo "Last recorded position of each unfinished run — a \`BEGIN\` with no \`END\`"
          echo "means the phase never finished, which is NOT the same as failing:"
          echo
          echo '```'
          for jf in "${RECORD_DIR}"/salvaged/journals/*.txt; do
            [ -f "$jf" ] || continue
            echo "--- $(basename "$jf") ---"
            grep -E "$JOURNAL_EVENT_MATCH" "$jf" 2>/dev/null | tail -6 || true
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
