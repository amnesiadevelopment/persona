#!/bin/bash
# PS-218 — attribute every compile error to the patch it came from, and
# DISTINGUISH errors the unmodified control had too.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY THE CONTROL DIFF IS THE WHOLE POINT
# ─────────────────────────────────────────────────────────────────────────────
# The ticket requires, for every compile failure: "what failed, which patch it
# came from, and WHETHER THE UNMODIFIED TREE HAD IT TOO."
#
# That last clause is the one that does the work. A Chromium build throws
# warnings and occasionally errors that have nothing to do with us. Billing one
# of those to a fingerprint patch would be exactly the false attribution this
# ticket exists to prevent — the patched-tree equivalent of PS-192's reviewer
# measuring the product clean on a broken instrument. So every error found in
# the patched log is checked against the unmodified log FIRST, and anything
# present in both is reported as PRE-EXISTING and explicitly NOT attributed to
# our patch layer.
#
# ─────────────────────────────────────────────────────────────────────────────
# HOW ATTRIBUTION IS DONE, AND ITS HONEST LIMIT
# ─────────────────────────────────────────────────────────────────────────────
# Each of our 16 patches names the files it touches (its `+++ b/...` headers).
# A compile error names the file it occurred in. Attribution is the join of
# those two: an error in a file our patch modified is attributed to that patch.
#
# THE LIMIT, STATED RATHER THAN HIDDEN: this attributes by FILE, so an error in
# a file none of our patches touched — the classic signature-change break, where
# our patch calls a function whose declaration moved elsewhere — lands in
# UNATTRIBUTED. That bucket is reported prominently rather than dropped,
# because on this ticket an unattributed error in a tree that differs from a
# working control by exactly 16 patches is a REAL finding and not noise.
set -euo pipefail

UCPL_DIR="${UCPL_DIR:?}"
PATCH_DIR="${PATCH_DIR:?}"

REC="record"
mkdir -p "$REC"
OUT="${REC}/attribution.txt"

PATCHED_LOG="${REC}/compile-patched.log"
# The control's log is brought in EXPLICITLY by a download-artifact step in the
# patched job (into control/), never picked up incidentally from a shared
# workspace — see the provenance check below for why that distinction matters.
CONTROL_DIR="${CONTROL_DIR:-control}"
CONTROL_LOG="${CONTROL_DIR}/compile-unmodified.log"
CONTROL_PROV="${CONTROL_DIR}/compile-unmodified.provenance"

# ─────────────────────────────────────────────────────────────────────────────
# TWO KINDS OF LINE, AND WHY THEY MUST NOT SHARE A BUCKET
# ─────────────────────────────────────────────────────────────────────────────
# ninja emits, for ONE broken edge:
#   FAILED: obj/third_party/blink/.../webgl_rendering_context_base.o
#   ../../third_party/blink/.../webgl_rendering_context_base.cc:1234:5: error: ...
#
# The clang line carries a SOURCE path and joins against OWNER, which is keyed
# on the `+++ b/` headers of our patches — all source paths. The `FAILED:` line
# carries an OBJECT path (`obj/...`), which can NEVER match any of those keys.
#
# Folding both into one list therefore sent every `FAILED:` line to the
# UNATTRIBUTED bucket for a purely mechanical reason — and that bucket tells the
# reader, in bold, that an entry there is "a REAL finding, not noise". Since
# ninja emits one `FAILED:` per broken edge IN ADDITION to the diagnostics, the
# bucket the reader is told to trust most became the one dominated by artefacts,
# and all three summary counts were wrong.
#
# So they are separated: clang diagnostics are ATTRIBUTED, `FAILED:` lines are
# COUNTED as failing edges and never attributed.
extract_errors() {
  local log="$1"
  [ -f "$log" ] || return 0
  grep -E ': (error|fatal error): ' "$log" 2>/dev/null || true
}

extract_failed_edges() {
  local log="$1"
  [ -f "$log" ] || return 0
  grep -E '^FAILED: ' "$log" 2>/dev/null || true
}

# The source file a clang diagnostic refers to, normalised to repo-relative.
# `FAILED:` lines never reach this function any more.
#
# ⚠️ DEPENDENCY, STATED BECAUSE IT IS NOT VISIBLE FROM THE CALL SITE: the final
# `awk '{print $1}'` takes the FIRST WHITESPACE-DELIMITED FIELD of the line, so
# this is correct only while the path is field 1 — the ordinary clang shape
#     ../../path/to/file.cc:123:5: error: message
# If you ever widen `extract_errors` to match a line whose path is NOT the first
# field (a linker line, a `FAILED:` line, anything prefixed with a timestamp or
# a `[1234/50000]` progress counter), this silently returns the wrong token and
# the join against OWNER misfires — producing a confident, wrong attribution
# rather than an obvious failure. Widen the sed to anchor on the path instead of
# relying on position if that day comes.
error_file() {
  sed -E 's/:[0-9]+:[0-9]+:.*$//; s/^\.\.\/\.\.\///' <<<"$1" \
    | awk '{print $1}'
}

# ─────────────────────────────────────────────────────────────────────────────
# A THIRD POPULATION: TOOLCHAIN/DRIVER DIAGNOSTICS, WHICH NAME NO SOURCE FILE
# ─────────────────────────────────────────────────────────────────────────────
# `extract_errors` matches `: error: `, and that matches MORE than the ordinary
# source diagnostic. When the OOM reaper kills the linker, the clang DRIVER
# emits its own errors, and they carry no file:line:col at all:
#
#   clang++: error: unable to execute command: Killed
#   clang++: error: linker command failed due to signal (use -v to see invocation)
#   ld.lld: error: out of memory
#
# `error_file()` reduces each of those to `clang++:` / `ld.lld:` — a tool name,
# not a path — which can never join OWNER. So without this split they land in
# UNATTRIBUTED, the one bucket whose text tells the reader it is "a REAL
# finding, not noise", and the three attribution counts are wrong again.
#
# This is the SAME defect as the `FAILED:` one directly above, arriving through
# a different line shape: a line that cannot be attributed by file being counted
# as though it had been. It matters because it fires in exactly the ticket's
# named KNOWN failure mode — memory at link — where these are the only
# diagnostics in the log, so the whole attributable population would be
# artefacts.
#
# The discriminator is the shape `error_file()` already depends on: a source
# diagnostic is `path:LINE:COL: error:`. If the line carries no line:col, there
# is no file to attribute to, and we say so instead of guessing.
is_source_diagnostic() {
  [[ "$1" =~ ^(\.\./\.\./)?[^[:space:]]+:[0-9]+:[0-9]+:[[:space:]](error|fatal\ error): ]]
}

{
  echo "# PS-218 — compile failure attribution"
  echo "# generated: $(date -Is)"
  echo

  if [ ! -f "$PATCHED_LOG" ]; then
    echo "No patched compile log found at ${PATCHED_LOG}."
    echo "The compile phase did not run, so there is nothing to attribute."
    echo "NOTE: 'not attempted' is not the same as 'compiled cleanly'."
    exit 0
  fi

  # ── which files does each of our patches touch? ────────────────────────────
  echo "== files touched by each of our 16 patches =="
  echo
  declare -A OWNER
  for p in $(ls "${PATCH_DIR}"/*.patch | sort); do
    name="$(basename "$p")"
    files=$(grep -E '^\+\+\+ b/' "$p" | sed 's|^+++ b/||' | sort -u)
    printf '%s\n' "$name"
    while read -r f; do
      [ -z "$f" ] && continue
      printf '    %s\n' "$f"
      # A file can be touched by more than one patch (011 and 016 both modify
      # webgl_rendering_context_base.cc), so owners accumulate rather than
      # overwrite — reporting one of two candidates as though it were certain
      # would be a false attribution of its own.
      if [ -n "${OWNER[$f]:-}" ]; then
        OWNER[$f]="${OWNER[$f]}, ${name}"
      else
        OWNER[$f]="${name}"
      fi
    done <<<"$files"
  done
  echo

  # ── the control's errors, if a log for THIS RUN is available ──────────────
  # Presence is not provenance. A control log is usable only when its stamp says
  # it came from this dispatch and this tag; anything else is refused and
  # reported, because a control from another run is worse than no control — it
  # licenses "PRE-EXISTING — NOT ours" against a tree that was never built here.
  CONTROL_AVAILABLE=false
  CONTROL_REFUSED=""
  if [ -f "$CONTROL_LOG" ]; then
    if [ -f "$CONTROL_PROV" ]; then
      # shellcheck disable=SC1090
      ctl_tag="$(sed -n 's/^ungoogled_tag=//p' "$CONTROL_PROV")"
      ctl_run="$(sed -n 's/^github_run_id=//p' "$CONTROL_PROV")"
      want_tag="${UNGOOGLED_TAG:-unknown}"
      want_run="${GITHUB_RUN_ID:-local}"
      if [ "$ctl_tag" = "$want_tag" ] && [ "$ctl_run" = "$want_run" ]; then
        CONTROL_AVAILABLE=true
      else
        CONTROL_REFUSED="stamp mismatch — control was tag='${ctl_tag}' run='${ctl_run}', this run is tag='${want_tag}' run='${want_run}'"
      fi
    else
      CONTROL_REFUSED="control log carries NO provenance stamp, so it cannot be shown to belong to this run"
    fi
  fi

  if $CONTROL_AVAILABLE; then
    extract_errors "$CONTROL_LOG" | sort -u > "${REC}/.control-errors" || true
    echo "== control (unmodified tree) errors: $(wc -l < "${REC}/.control-errors") =="
    echo "control provenance: tag=${ctl_tag} run=${ctl_run} — matches this run"
  else
    : > "${REC}/.control-errors"
    if [ -n "$CONTROL_REFUSED" ]; then
      echo "== control log REFUSED =="
      echo
      echo "A control log was on disk but was NOT used:"
      echo "    ${CONTROL_REFUSED}"
      echo
      echo "record/ lives outside both checkouts and a self-hosted runner does not"
      echo "wipe it between dispatches, so a log being PRESENT does not make it"
      echo "THIS run's. Using it would mark errors 'PRE-EXISTING — NOT ours'"
      echo "against a control built from a different tree, which is the false"
      echo "attribution this ticket exists to prevent."
    else
      echo "== control log NOT PRESENT in this job's workspace =="
      echo
      echo "The unmodified tree's compile log is uploaded as a SEPARATE artifact"
      echo "(ps218-unmodified-*). To complete the attribution, download that"
      echo "artifact and diff the two error sets."
    fi
    echo
    echo "Every error below is therefore marked 'CONTROL UNKNOWN' rather than"
    echo "being asserted as ours. This is stated rather than silently assumed:"
    echo "claiming an error is ours without having checked the control is"
    echo "precisely the error PS-218 exists to correct."
  fi
  echo

  # ── attribute ──────────────────────────────────────────────────────────────
  echo "== compile errors in the PATCHED tree =="
  echo
  # BOTH populations are extracted BEFORE the no-diagnostics guard, and the
  # ordering is load-bearing rather than stylistic.
  #
  # The guard below exits early when there are no clang diagnostics. That is the
  # OOM-AT-LINK case, which the ticket names as the EXPECTED failure of this
  # build: "Memory during linking is the known failure mode... ungoogled's FAQ
  # names exhausted memory as the common build crash." A link that dies to the
  # OOM killer emits ninja `FAILED:` edges and a `Killed` — and NO clang
  # diagnostics at all.
  #
  # So if the edges were extracted below the guard (as they once were), the
  # single most likely real failure of this build would be the one case where
  # this script printed "no errors" and reported its failing edges NOWHERE.
  # Extracting both here means the early-exit path can state the edge count, and
  # the reader holding a build that died gets a number instead of a silence.
  mapfile -t EDGES < <(extract_failed_edges "$PATCHED_LOG" | sort -u)
  mapfile -t ERRS  < <(extract_errors "$PATCHED_LOG" | sort -u)

  if [ "${#ERRS[@]}" -eq 0 ]; then
    # "No clang diagnostics" is the TRUE claim. "No compile errors" is not — a
    # build killed at link failed to compile, and saying otherwise beside a log
    # full of `FAILED:` edges reads as a green to someone holding a red.
    echo "No clang diagnostics found in the patched tree's log."
    echo
    echo "failing build edges (ninja \`FAILED:\`): ${#EDGES[@]}"
    if [ "${#EDGES[@]}" -eq 0 ]; then
      echo "    none"
    else
      printf '    %s\n' "${EDGES[@]}"
    fi
    echo
    # The two zero-diagnostic cases are OPPOSITE outcomes and must not share
    # wording. Edges present = the build broke at link (the ticket's known
    # failure mode). Edges absent = nothing failed in this log at all, and
    # telling that reader to go hunt for an OOM kill would manufacture a
    # suspicion the evidence does not support.
    if [ "${#EDGES[@]}" -gt 0 ]; then
      echo "Edges but no diagnostics is the signature of a build that died at"
      echo "LINK rather than at compile — OOM kill or timeout. The ticket names"
      echo "that as the KNOWN failure mode for this build. These edges are the"
      echo "evidence the build failed; they are NOT attributed to a patch,"
      echo "because they name object paths which cannot join the ownership map."
      echo "Check the tail of ${PATCHED_LOG} to confirm which."
    else
      echo "No diagnostics AND no failing edges: this log records no compile"
      echo "failure. Whether the build SUCCEEDED is a separate question answered"
      echo "by the on-disk binary (see the manifest's compiled flag), not by the"
      echo "absence of errors here — a log truncated early also looks like this."
    fi
    echo
    # Same key set as the main summary below, so the two paths stay
    # comparable and a reader (or a grep) never has to know which one ran.
    # With zero diagnostics every attribution count is necessarily zero.
    echo "== summary =="
    echo "total distinct clang diagnostics: 0"
    echo "  of which attributable (name a source file): 0"
    echo "attributed to one of our patches: 0"
    echo "pre-existing (control had them too): 0"
    echo "unattributed (file not touched by us): 0"
    echo "toolchain/driver (name no source file, not attributed): 0"
    echo "failing build edges (counted separately, not attributed): ${#EDGES[@]}"
    $CONTROL_AVAILABLE || echo "control diff: NOT PERFORMED (no usable control log) — see note above"
    exit 0
  fi

  attributed=0
  preexisting=0
  unattributed=0
  toolchain=0
  TOOLCHAIN_LINES=()

  for e in "${ERRS[@]}"; do
    f="$(error_file "$e")"

    if $CONTROL_AVAILABLE && grep -Fqx "$e" "${REC}/.control-errors"; then
      echo "[PRE-EXISTING — the unmodified tree had this too; NOT ours]"
      echo "    ${e}"
      echo
      preexisting=$((preexisting + 1))
      continue
    fi

    # A driver/linker diagnostic names a TOOL, not a source file, so there is
    # nothing to join against OWNER. Counted separately rather than dropped into
    # UNATTRIBUTED, which would assert it as "a REAL finding" about our patches
    # when it is actually the shape of a link that ran out of memory.
    if ! is_source_diagnostic "$e"; then
      TOOLCHAIN_LINES+=("$e")
      toolchain=$((toolchain + 1))
      continue
    fi

    if [ -n "${OWNER[$f]:-}" ]; then
      echo "[ATTRIBUTED -> ${OWNER[$f]}]"
      echo "    file:  ${f}"
      echo "    error: ${e}"
      $CONTROL_AVAILABLE || echo "    (CONTROL UNKNOWN — control log not usable for this run)"
      echo
      attributed=$((attributed + 1))
    else
      echo "[UNATTRIBUTED — in a file none of our 16 patches modify]"
      echo "    file:  ${f}"
      echo "    error: ${e}"
      echo "    NOTE: this is the signature-change shape — our patch calls into"
      echo "          code that changed elsewhere. In a tree differing from a"
      echo "          working control by exactly our 16 patches, this is a REAL"
      echo "          finding, not noise."
      $CONTROL_AVAILABLE || echo "    (CONTROL UNKNOWN — control log not usable for this run)"
      echo
      unattributed=$((unattributed + 1))
    fi
  done

  # The heading above must never stand empty. Every diagnostic in this log can
  # legitimately be a toolchain/driver line (the OOM-at-link case), in which
  # case nothing was printed under it — and a bare heading reads as "no errors"
  # to someone holding a build that failed. Say which it is.
  if [ "$((attributed + preexisting + unattributed))" -eq 0 ]; then
    echo "None of this log's ${#ERRS[@]} diagnostic(s) name a source file, so none"
    echo "could be attributed to a patch. They are listed under toolchain/driver"
    echo "below. This is NOT the same as a clean compile."
    echo
  fi

  # ── failing edges, counted and NEVER attributed ────────────────────────────
  # ninja's `FAILED:` lines name an OBJECT path (obj/...). OWNER is keyed on the
  # `+++ b/` headers of our patches, which are all SOURCE paths, so an object
  # path cannot match any key — not because the error is unrelated to our
  # patches, but for a purely mechanical reason. Attributing them would put
  # every one in UNATTRIBUTED, a bucket whose text tells the reader it is "a
  # REAL finding, not noise".
  #
  # Each failing edge is ALREADY represented among the clang diagnostics above
  # (ninja emits both for one broken edge), so this is a count of broken build
  # edges for cross-checking, not a second population of errors. It is
  # deliberately excluded from the three attribution counts.
  mapfile -t EDGES < <(extract_failed_edges "$PATCHED_LOG" | sort -u)
  echo "== failing build edges (ninja \`FAILED:\`) — counted, not attributed =="
  echo
  if [ "${#EDGES[@]}" -eq 0 ]; then
    echo "none"
  else
    for x in "${EDGES[@]}"; do
      echo "    ${x}"
    done
    echo
    echo "These name object files, not sources, so they are NOT joined against"
    echo "the patch-ownership map. The diagnostics above are the attributable"
    echo "population; this is the edge count for cross-checking against it."
  fi
  echo

  # ── toolchain/driver diagnostics, counted and NEVER attributed ─────────────
  # These match `: error: ` but name a TOOL (`clang++:`, `ld.lld:`) rather than
  # a source path, so they cannot be joined against the ownership map. Reported
  # as their own population for the same reason the failing edges are: putting
  # them in UNATTRIBUTED would tell the reader they are "a REAL finding" about
  # our patches, when the usual cause is a link that ran out of memory.
  echo "== toolchain/driver diagnostics — counted, not attributed =="
  echo
  if [ "${#TOOLCHAIN_LINES[@]}" -eq 0 ]; then
    echo "none"
  else
    for x in "${TOOLCHAIN_LINES[@]}"; do
      echo "    ${x}"
    done
    echo
    echo "These carry no file:line:col, so there is no file to attribute them"
    echo "to. A 'Killed' or 'out of memory' here is the OOM-at-link signature"
    echo "the ticket names as the KNOWN failure mode — a property of the build"
    echo "machine, NOT evidence about our 16 patches."
  fi
  echo

  echo "== summary =="
  echo "total distinct clang diagnostics: ${#ERRS[@]}"
  echo "  of which attributable (name a source file): $((attributed + preexisting + unattributed))"
  echo "attributed to one of our patches: ${attributed}"
  echo "pre-existing (control had them too): ${preexisting}"
  echo "unattributed (file not touched by us): ${unattributed}"
  echo "toolchain/driver (name no source file, not attributed): ${toolchain}"
  echo "failing build edges (counted separately, not attributed): ${#EDGES[@]}"
  $CONTROL_AVAILABLE || echo "control diff: NOT PERFORMED (no usable control log) — see note above"
} | tee "$OUT"

echo "wrote ${OUT}"
