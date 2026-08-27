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
error_file() {
  sed -E 's/:[0-9]+:[0-9]+:.*$//; s/^\.\.\/\.\.\///' <<<"$1" \
    | awk '{print $1}'
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
  mapfile -t ERRS < <(extract_errors "$PATCHED_LOG" | sort -u)

  if [ "${#ERRS[@]}" -eq 0 ]; then
    echo "No compile errors found in the patched tree's log."
    echo "If the build nonetheless failed, the cause is not a clang diagnostic —"
    echo "check the tail of ${PATCHED_LOG} (link failure, OOM kill, or timeout)."
    exit 0
  fi

  attributed=0
  preexisting=0
  unattributed=0

  for e in "${ERRS[@]}"; do
    f="$(error_file "$e")"

    if $CONTROL_AVAILABLE && grep -Fqx "$e" "${REC}/.control-errors"; then
      echo "[PRE-EXISTING — the unmodified tree had this too; NOT ours]"
      echo "    ${e}"
      echo
      preexisting=$((preexisting + 1))
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

  echo "== summary =="
  echo "total distinct clang diagnostics: ${#ERRS[@]}"
  echo "attributed to one of our patches: ${attributed}"
  echo "pre-existing (control had them too): ${preexisting}"
  echo "unattributed (file not touched by us): ${unattributed}"
  echo "failing build edges (counted separately, not attributed): ${#EDGES[@]}"
  $CONTROL_AVAILABLE || echo "control diff: NOT PERFORMED (no usable control log) — see note above"
} | tee "$OUT"

echo "wrote ${OUT}"
