#!/bin/bash
# PS-307 — PROVE our 16 fingerprint patches are in the tree about to be compiled.
#
# ─────────────────────────────────────────────────────────────────────────────
# THIS IS THE GUARD THE WHOLE TICKET EXISTS FOR
# ─────────────────────────────────────────────────────────────────────────────
# Reusing the prepared tree preserves upstream's stamp files, and `.patched.stamp`
# records only THAT patching happened — never WHICH series was applied. A tree
# stamped by the control job would make `apply_patches()` a complete no-op in the
# patched job: our 16 never enter the tree, the compile succeeds, and the
# artifact is labelled as carrying 16 fingerprint patches while carrying none.
#
# ps307_tree_state.sh makes that specific sequence unreachable by refusing to
# reuse a tree whose identity is not the identity this job needs. But a guard
# that has never been seen to fire is not evidence, and "the tree we destroyed
# and rebuilt must therefore be correct" is an inference, not a measurement.
# So this script does not reason about what SHOULD be in the tree. It looks.
#
# ⚠️ IT DELIBERATELY NEVER READS `.patched.stamp`, `patches/series`, OR ANY
# STAMP. The stamp is the artefact that lies in this story, and a check reading
# it would report success in precisely the scenario that drops all 16 patches.
# The only thing consulted is the CONTENT OF THE SOURCE FILES.
#
# ─────────────────────────────────────────────────────────────────────────────
# "PRESENT IN THE TREE" IS NOT "APPLIED CLEANLY", AND PRESENT IS THE ONE THAT
# PROTECTS THE ARTIFACT'S LABEL
# ─────────────────────────────────────────────────────────────────────────────
# Whether a patch applied cleanly is already measured, twice: `patch` exits
# non-zero on a rejected hunk and `utils/patches.py` runs it under `check=True`,
# so a failed application fails the prepare step and is reported as
# "DID NOT APPLY". That result is not in question here.
#
# The question this ticket raises is different and was previously unasked: is the
# code IN THE TREE THAT IS ABOUT TO BE COMPILED? A skipped apply_patches() is not
# a failed application — nothing fails, nothing is rejected, and every existing
# signal stays green. Only the tree can tell you, so the tree is what is read.
#
# ─────────────────────────────────────────────────────────────────────────────
# HOW THE EVIDENCE IS DERIVED
# ─────────────────────────────────────────────────────────────────────────────
# ps307_patch_evidence.awk turns each patch into tree-checkable claims (see its
# header for the four filters that decide what counts as usable evidence):
#
#     newfile   the patch CREATES this file          → the file must EXIST
#     added     a line the patch inserts             → must be FOUND in the file
#     removed   a line the patch deletes, emitted    → must be ABSENT from the file
#               only where the patch adds nothing
#
# A patch passes when every one of its claims holds. Anything else — a missing
# file, a line that is not there, a deleted line still present — fails the run.
#
# ⚠️ Evidence is matched as a FIXED STRING with leading and trailing whitespace
# stripped (`grep -F` over a whitespace-normalised file), never as a regex.
# Chromium source is full of `(`, `[`, `*`, `.` and `?`, and a regex match would
# turn our own C++ into a pattern that matches things it should not. The
# whitespace strip is what makes the comparison survive the re-indentation
# `patch --ignore-whitespace` tolerates by design.
#
# ─────────────────────────────────────────────────────────────────────────────
# TWO MODES, BECAUSE A CHECK THAT CANNOT FAIL IS NOT COVERAGE
# ─────────────────────────────────────────────────────────────────────────────
#   present  (patched tree)     every patch must be found. Missing → EXIT 1.
#   absent   (unmodified tree)  no patch may be found. Present → EXIT 1.
#
# The `absent` mode is not symmetry for its own sake. It is the NEGATIVE CONTROL
# that makes the positive result mean something: run it on the control tree,
# which is known to carry none of our patches, and a check that "passes"
# everywhere is caught immediately. Without it, a `present` check with a broken
# matcher — a grep whose pattern always hits, an evidence file that came out
# empty — would report all 16 patches found on every tree forever, which is the
# same class of false green the whole workflow is written against.
#
# It also stands on its own: if our patches turn up in the CONTROL tree, the
# control is contaminated and every "the unmodified tree had this error too"
# attribution built on it is wrong.
set -euo pipefail

MODE="${1:?usage: ps307_verify_patches_in_tree.sh <present|absent> [tree-label]}"
TREE="${2:-${MODE}}"
UCPL_DIR="${UCPL_DIR:?UCPL_DIR must point at the ungoogled-chromium-portablelinux checkout}"
PATCH_DIR="${PATCH_DIR:?PATCH_DIR must point at our vendored fingerprint patches}"

case "$MODE" in
  present|absent) ;;
  *) echo "::error::PS-307: mode must be 'present' or 'absent', got '${MODE}'"; exit 2 ;;
esac

SRC_DIR="${UCPL_DIR}/build/src"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVIDENCE_AWK="${HERE}/ps307_patch_evidence.awk"

# How many evidence lines to take per file per patch. Three is enough to be
# specific without making the check a performance problem on a tree this size,
# and every one of them must hold.
MAX_PER_FILE="${PS307_MAX_EVIDENCE_PER_FILE:-3}"

mkdir -p record
REPORT="record/patch-presence-${TREE}.txt"

echo "== PS-307: verifying our fingerprint patches ${MODE^^} in the tree that is about to be compiled =="
echo "mode:        ${MODE}"
echo "tree:        ${TREE}"
echo "source tree: ${SRC_DIR}"
echo "patches:     ${PATCH_DIR}"
echo

if [ ! -f "$EVIDENCE_AWK" ]; then
  echo "::error::PS-307: ${EVIDENCE_AWK} is missing; the evidence extractor is part of this check and it cannot run without it."
  exit 1
fi

if [ ! -d "$SRC_DIR" ]; then
  echo "::error::PS-307: ${SRC_DIR} does not exist, so there is no tree to verify."
  echo "::error::PS-307: this check reports on the TREE, and an absent tree is not a pass."
  exit 1
fi

shopt -s nullglob
PATCHES=( "${PATCH_DIR}"/*.patch )
shopt -u nullglob

# The same count guard ps218_stage_patches.sh carries, for the same reason. A
# verification of some other number of patches would report a verdict about a
# patch layer that is not ours.
if [ "${#PATCHES[@]}" -ne 16 ]; then
  echo "::error::PS-307: expected exactly 16 fingerprint patches in ${PATCH_DIR}, found ${#PATCHES[@]}."
  echo "::error::PS-307: a presence check over some other number measures a patch layer that is not the one this build claims to carry."
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── the matcher ─────────────────────────────────────────────────────────────
# `grep -F -q` over a whitespace-normalised copy of the file. Fixed string, so
# C++ punctuation is never read as a pattern; normalised, so the comparison
# tolerates the re-indentation `patch --ignore-whitespace` permits.
#
# The normalised copy is cached per file: several patches touch the same file
# (element.cc, navigator.cc and webgl_rendering_context_base.cc each appear in
# two), and these are large Chromium sources.
normalised_copy() {
  local rel="$1"
  local key="${TMP}/norm/${rel}"
  if [ ! -f "$key" ]; then
    mkdir -p "$(dirname "$key")"
    sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "${SRC_DIR}/${rel}" > "$key"
  fi
  printf '%s' "$key"
}

FAILURES=0
CHECKED=0
PATCHES_OK=0
PATCHES_BAD=0

# ⚠️ NO PIPELINE AROUND THIS LOOP, AND NO `| tee` AROUND THE WHOLE SCRIPT.
# PS-244 lost exactly this: ps218_verify_control.sh wrapped its checks in
# `{ ... } | tee "$REPORT"`, the pipeline ran the left side in a SUBSHELL, every
# FAILURES increment was discarded, and the script printed its REFUSED lines and
# then exited 0. Output is written to the report with an explicit append instead.
: > "$REPORT"
{
  echo "# PS-307 — is our fingerprint patch layer in the tree?"
  echo "# mode: ${MODE}   tree: ${TREE}"
  echo "# recorded: $(date -Is)"
  echo "# source tree: ${SRC_DIR}"
  echo "#"
  echo "# Read from the SOURCE FILES. No stamp, no series file, no exit code was"
  echo "# consulted: .patched.stamp records only THAT patching happened, never"
  echo "# WHICH series, so it is the one artefact that cannot answer this."
  echo
} >> "$REPORT"

for patch_path in "${PATCHES[@]}"; do
  patch_name="$(basename "$patch_path")"
  ev="${TMP}/evidence"
  # Two passes over the same file: pass 1 builds the exclusion set, pass 2 emits.
  awk -v MAX_PER_FILE="$MAX_PER_FILE" -f "$EVIDENCE_AWK" "$patch_path" "$patch_path" > "$ev"

  claims=0
  bad=0
  detail=""

  # A patch that yields no claims cannot be verified, and "nothing to check"
  # must never read as "checked and fine". That is the shape of the very defect
  # this ticket is about, arriving through the checker instead of the build.
  if [ ! -s "$ev" ]; then
    echo "::error::PS-307: ${patch_name} produced NO verifiable evidence."
    echo "::error::PS-307: a patch that cannot be checked must not be reported as checked. Refusing."
    printf '%-45s UNVERIFIABLE — the evidence extractor produced no claims\n' "$patch_name" >> "$REPORT"
    FAILURES=$((FAILURES + 1))
    PATCHES_BAD=$((PATCHES_BAD + 1))
    continue
  fi

  while IFS=$'\t' read -r rel kind text; do
    [ -n "$rel" ] || continue

    if [ "$kind" = "noevidence" ]; then
      # One file section of a multi-file patch yielded nothing usable (both our
      # BUILD.gn edits are like this: short `"gpu_info.cc",` source-list lines,
      # correctly rejected as too short to prove anything). The PATCH is still
      # verified through its other sections; this is recorded, not counted.
      printf '    %-70s (no usable evidence in this file — checked via the patch'"'"'s other files)\n' "$rel" >> "$REPORT"
      continue
    fi

    claims=$((claims + 1))
    CHECKED=$((CHECKED + 1))
    full="${SRC_DIR}/${rel}"

    case "$kind" in
      newfile)
        if [ -f "$full" ]; then
          found=yes
        else
          found=no
        fi
        ;;
      added)
        if [ ! -f "$full" ]; then
          found=nofile
        elif grep -qF -- "$text" "$(normalised_copy "$rel")"; then
          found=yes
        else
          found=no
        fi
        ;;
      removed)
        # Inverted: the patch DELETES this line, so a patched tree must NOT have
        # it. `found=yes` here means "the patch's effect is present".
        if [ ! -f "$full" ]; then
          found=nofile
        elif grep -qF -- "$text" "$(normalised_copy "$rel")"; then
          found=no
        else
          found=yes
        fi
        ;;
    esac

    # `present` wants found=yes; `absent` wants found=no. `nofile` is a failure
    # in `present` mode and a PASS in `absent` mode for a newfile/added claim —
    # a file our patch creates is legitimately missing from an unpatched tree.
    #
    # ⚠️ Written as a `case` rather than as `[ ... ] && ok=yes`. Under `set -e`
    # that idiom is a live hazard here: a failing `[` as the LAST command of an
    # `if` branch makes the whole compound return non-zero, and the script dies
    # at the first evidence item that does not hold — i.e. exactly when it is
    # supposed to be recording a failure and carrying on to report all of them.
    ok=no
    if [ "$MODE" = "present" ]; then
      case "$found" in yes) ok=yes ;; esac
    else
      case "$found" in no|nofile) ok=yes ;; esac
    fi

    if [ "$ok" != "yes" ]; then
      bad=$((bad + 1))
      detail="${detail}"$'\n'"    ${kind}  ${rel}"
      if [ -n "$text" ]; then
        detail="${detail}"$'\n'"        looked for: ${text:0:100}"
      fi
      detail="${detail}"$'\n'"        result: ${found}"
    fi
  done < "$ev"

  if [ "$bad" -eq 0 ]; then
    PATCHES_OK=$((PATCHES_OK + 1))
    if [ "$MODE" = "present" ]; then
      printf '%-45s PRESENT   (%d/%d claims hold)\n' "$patch_name" "$claims" "$claims" >> "$REPORT"
    else
      printf '%-45s ABSENT    (%d/%d claims hold — correctly not in this tree)\n' "$patch_name" "$claims" "$claims" >> "$REPORT"
    fi
  else
    PATCHES_BAD=$((PATCHES_BAD + 1))
    FAILURES=$((FAILURES + bad))
    if [ "$MODE" = "present" ]; then
      printf '%-45s ❌ NOT IN THE TREE (%d of %d claims failed)%s\n' "$patch_name" "$bad" "$claims" "$detail" >> "$REPORT"
      echo "::error::PS-307: ${patch_name} is NOT in the tree — ${bad} of ${claims} evidence claims failed."
    else
      printf '%-45s ❌ PRESENT IN THE CONTROL (%d of %d claims hold)%s\n' "$patch_name" "$bad" "$claims" "$detail" >> "$REPORT"
      echo "::error::PS-307: ${patch_name} appears to be PRESENT in the ${TREE} tree, which must carry none of our patches."
    fi
  fi
done

{
  echo
  echo "patches checked:  ${#PATCHES[@]}"
  echo "evidence claims:  ${CHECKED}"
  echo "patches passing:  ${PATCHES_OK}"
  echo "patches failing:  ${PATCHES_BAD}"
  echo "verdict:          $([ "$FAILURES" -eq 0 ] && echo PASS || echo FAIL)"
} >> "$REPORT"

echo
cat "$REPORT"
echo
echo "recorded -> ${REPORT}"

if [ "$FAILURES" -ne 0 ]; then
  echo
  if [ "$MODE" = "present" ]; then
    echo "::error::PS-307: the tree about to be compiled does NOT carry our full fingerprint patch layer."
    echo "::error::PS-307: ${PATCHES_BAD} of ${#PATCHES[@]} patches could not be found in the source files."
    echo "::error::PS-307: this is the exact failure tree reuse makes possible — a preserved, already-stamped tree lets upstream's apply_patches() skip itself, and the build would then be labelled as carrying 16 patches while carrying fewer."
    echo "::error::PS-307: STOPPING. A compile from here would measure nothing, and reporting it as this ticket's result would be worse than reporting no result."
  else
    echo "::error::PS-307: our fingerprint patches are present in the ${TREE} tree, which is supposed to be the UNMODIFIED control."
    echo "::error::PS-307: a contaminated control cannot attribute anything: every 'the unmodified tree had this error too' claim resting on it would be false."
  fi
  exit 1
fi

if [ "$MODE" = "present" ]; then
  echo "PS-307: all ${#PATCHES[@]} fingerprint patches VERIFIED PRESENT in the tree, from ${CHECKED} pieces of evidence read out of the source files."
else
  echo "PS-307: all ${#PATCHES[@]} fingerprint patches verified ABSENT from the ${TREE} tree, as a control must be. ${CHECKED} claims checked."
fi
