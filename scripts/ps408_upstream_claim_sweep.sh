#!/usr/bin/env bash
# PS-408 — WHICH OF OUR EVIDENCE CLAIMS DOES UNMODIFIED UPSTREAM ALREADY WRITE?
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY THIS EXISTS
# ─────────────────────────────────────────────────────────────────────────────
# `ps307_verify_patches_in_tree.sh` decides whether our patches are in a tree by
# grepping for lines they add. A line UNMODIFIED UPSTREAM ALSO WRITES proves
# nothing: it holds against a perfectly clean control, and on the absence gate
# that fails a build for a reason that is not true. Run 34513197109 failed
# exactly that way — `014-client-rects.patch` was declared PRESENT IN THE
# CONTROL on ONE claim out of sixteen, and the line was Chromium's own.
#
# ⛔ THAT QUESTION CANNOT BE ANSWERED FROM THIS REPOSITORY. It is a claim about
# TWO artifacts — our patch and upstream's source — and only one of them is
# here. So this fetches the other one.
#
# ⭐ READ THE MATCH IN CONTEXT BEFORE ACTING ON IT. "The string is present" and
# "the string is evidence of our patch" are different claims, and only the
# surrounding function tells them apart. The one hit in our set sits inside
# `QuadF::IntersectsRect` while our patch adds `QuadF::Offset` — same file, same
# pre-existing API, unrelated functions. Print the context and look at it.
#
# ─────────────────────────────────────────────────────────────────────────────
# USAGE
# ─────────────────────────────────────────────────────────────────────────────
#   scripts/ps408_upstream_claim_sweep.sh            # report; exit 1 if any hit
#   TAG=152.0.7977.75 scripts/ps408_upstream_claim_sweep.sh
#
# Re-run it after every patch rebase. A line that is distinctive at one tag can
# become upstream's at the next, and nothing else in the repo would say so.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"
PATCH_DIR="${PATCH_DIR:-${REPO}/engine/patches/fingerprint}"

# The tag the build actually uses, read from the vendored pin rather than
# defaulted to `main` — `main` is not what any build compiles, so a sweep
# against it would answer a question nobody asked.
if [ -z "${TAG:-}" ]; then
  TAG="$(sed -n 's/^[[:space:]]*\([0-9][0-9.]*\)-[0-9].*$/\1/p' "${PATCH_DIR}/CURRENT_TAG.txt" | head -1)"
fi
[ -n "$TAG" ] || { echo "::error::PS-408: could not determine the Chromium tag" >&2; exit 2; }

CACHE="${CACHE:-${TMPDIR:-/tmp}/ps408-upstream-${TAG}}"
mkdir -p "$CACHE"

echo "PS-408 upstream claim sweep"
echo "chromium tag: ${TAG}"
echo "patch dir:    ${PATCH_DIR}"
echo

fetch() {
  # ⚠️ `?format=TEXT` + base64 is REQUIRED, not stylistic: googlesource has no
  # raw endpoint and serves HTML otherwise. Piping the HTML to grep "works" and
  # matches syntax-highlighting markup rather than source — a false positive
  # that looks exactly like a real one.
  local rel="$1" key="${CACHE}/$(printf '%s' "$1" | tr '/' '_')"
  if [ ! -f "$key" ]; then
    curl -sfL --max-time 90 \
      "https://chromium.googlesource.com/chromium/src/+/${TAG}/${rel}?format=TEXT" \
      | base64 -d > "${key}.tmp" 2>/dev/null || : 
    mv -f "${key}.tmp" "$key" 2>/dev/null || : > "$key"
  fi
  cat "$key" 2>/dev/null || true
}

hits=0
checked=0
missing_files=0

for patch in "${PATCH_DIR}"/*.patch; do
  name="$(basename "$patch")"
  # NOTE the sweep deliberately runs the extractor with NO `UPSTREAM_LINES`, so
  # it sees the claims as they would be WITHOUT the filter. Feeding it the
  # filter's own output would make the sweep confirm itself and report a clean
  # result forever — the exact false green this family of scripts exists to stop.
  while IFS=$'\t' read -r rel kind text; do
    [ "$kind" = "added" ] || continue
    [ -n "$text" ] || continue
    checked=$((checked + 1))
    body="$(fetch "$rel")"
    if [ -z "$body" ]; then
      # A file our patch CREATES does not exist upstream — that is the expected
      # answer for a `newfile` section, not a fetch failure. Counted and named
      # rather than silently skipped.
      missing_files=$((missing_files + 1))
      continue
    fi
    if printf '%s' "$body" | grep -Fq -- "$text"; then
      hits=$((hits + 1))
      echo "⚠️  UPSTREAM ALREADY WRITES THIS CLAIM"
      echo "    patch: ${name}"
      echo "    file:  ${rel}"
      echo "    line:  ${text}"
      echo "    context (upstream ${TAG}):"
      printf '%s' "$body" | grep -nF -- "$text" | sed 's/^/      /'
      echo
    fi
  done < <(awk -v MAX_PER_FILE="${MAX_PER_FILE:-3}" \
               -f "${HERE}/ps307_patch_evidence.awk" "$patch" "$patch")
done

echo "checked:            ${checked} added-claims"
echo "not fetchable:      ${missing_files} (files upstream does not ship — expected for newfile sections)"
echo "upstream collisions: ${hits}"
echo

# ⭐ THE ZERO IS PRINTED, not inferred from an absent line: "no claim coincides"
# has to be something the output SAYS.
if [ "$hits" -eq 0 ]; then
  echo "✅ no claim in our patch set is written by unmodified upstream at ${TAG}"
  exit 0
fi

echo "❌ ${hits} claim(s) above are not evidence of our patches."
echo "   Add each line to ${PATCH_DIR}/UPSTREAM_LINES.txt — with its context —"
echo "   or reword the patch so the claim is distinctive. ⛔ Do NOT allowlist the"
echo "   patch: every other claim it makes must stay load-bearing."
exit 1
