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
# The control's log is uploaded by the `unmodified` job as a separate artifact,
# so it is not on disk here. If a copy has been placed alongside, use it.
CONTROL_LOG="${REC}/compile-unmodified.log"

# Extract compile diagnostics. Matches clang's `path:line:col: error:` shape
# plus bare `FAILED:` lines that ninja emits for a failing edge.
extract_errors() {
  local log="$1"
  [ -f "$log" ] || return 0
  grep -E '(^FAILED: |: (error|fatal error): )' "$log" 2>/dev/null || true
}

# The file a diagnostic refers to, normalised to a repo-relative path.
error_file() {
  sed -E 's/^FAILED: +//; s/:[0-9]+:[0-9]+:.*$//; s/^\.\.\/\.\.\///' <<<"$1" \
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

  # ── the control's errors, if the log is available ─────────────────────────
  CONTROL_AVAILABLE=false
  if [ -f "$CONTROL_LOG" ]; then
    CONTROL_AVAILABLE=true
    extract_errors "$CONTROL_LOG" | sort -u > "${REC}/.control-errors" || true
    echo "== control (unmodified tree) errors: $(wc -l < "${REC}/.control-errors") =="
  else
    : > "${REC}/.control-errors"
    echo "== control log NOT PRESENT in this job's workspace =="
    echo
    echo "The unmodified tree's compile log is uploaded as a SEPARATE artifact"
    echo "(ps218-unmodified-*). Every error below is therefore marked"
    echo "'CONTROL UNKNOWN' rather than being asserted as ours. To complete the"
    echo "attribution, download that artifact and diff the two error sets."
    echo
    echo "This is stated rather than silently assumed: claiming an error is ours"
    echo "without having checked the control is precisely the error PS-218 exists"
    echo "to correct."
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
      $CONTROL_AVAILABLE || echo "    (CONTROL UNKNOWN — control log not present in this workspace)"
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
      $CONTROL_AVAILABLE || echo "    (CONTROL UNKNOWN — control log not present in this workspace)"
      echo
      unattributed=$((unattributed + 1))
    fi
  done

  echo "== summary =="
  echo "total distinct errors: ${#ERRS[@]}"
  echo "attributed to one of our patches: ${attributed}"
  echo "pre-existing (control had them too): ${preexisting}"
  echo "unattributed (file not touched by us): ${unattributed}"
  $CONTROL_AVAILABLE || echo "control diff: NOT PERFORMED (control log absent) — see note above"
} | tee "$OUT"

echo "wrote ${OUT}"
