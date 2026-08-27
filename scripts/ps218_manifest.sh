#!/bin/bash
# PS-218 — write the per-tree manifest: the record a reader can act on without
# re-running anything.
#
# THE ONE THING THIS FILE MUST NEVER DO is let "it compiled" and "it was never
# compiled" collapse into each other. The ticket is explicit that this is the
# unacceptable outcome. So the manifest states the two results SEPARATELY and
# names the tree they belong to, and it distinguishes THREE states rather than
# two:
#
#   applied / compiled          — both phases ran and succeeded
#   applied / DID NOT COMPILE   — the patches landed, the build broke  ← the
#                                 number this ticket exists to produce
#   DID NOT APPLY / not reached — the compile never happened, and saying so is
#                                 different from saying it failed
#
# `skipped` is reported as "NOT ATTEMPTED", never as a pass and never as a
# failure — a phase that did not run is a third thing.
set -euo pipefail

TREE="${1:?usage: ps218_manifest.sh <unmodified|patched>}"
UCPL_DIR="${UCPL_DIR:?}"
UNGOOGLED_TAG="${UNGOOGLED_TAG:-unknown}"
NINJA_JOBS="${NINJA_JOBS:-}"
PREPARE_RESULT="${PREPARE_RESULT:-unknown}"
COMPILE_RESULT="${COMPILE_RESULT:-unknown}"

REC="record"
mkdir -p "$REC"
OUT="${REC}/MANIFEST-${TREE}.md"

CHROME="${UCPL_DIR}/build/src/out/Default/chrome"

# The binary's existence is checked on DISK, not inferred from an exit code.
# An exit code says what a script claimed; a file says what actually exists.
if [ -x "$CHROME" ]; then
  BINARY_STATE="PRESENT ($(du -h "$CHROME" | cut -f1))"
  BINARY_SHA="$(sha256sum "$CHROME" | cut -d' ' -f1)"
else
  BINARY_STATE="ABSENT — no chrome binary was produced"
  BINARY_SHA="n/a"
fi

# Translate a GitHub step outcome into a statement that cannot be misread.
say() {
  case "$1" in
    success) echo "YES" ;;
    failure) echo "NO — FAILED" ;;
    skipped) echo "NOT ATTEMPTED (the phase did not run)" ;;
    *)       echo "UNKNOWN ($1)" ;;
  esac
}

{
  echo "# PS-218 — build manifest: \`${TREE}\` tree"
  echo
  echo "Generated $(date -Is) on the self-hosted runner."
  echo
  echo "## What was built"
  echo
  echo "| field | value |"
  echo "|---|---|"
  echo "| tree | \`${TREE}\` |"
  echo "| ungoogled-chromium-portablelinux tag | \`${UNGOOGLED_TAG}\` |"
  if [ -f "${UCPL_DIR}/ungoogled-chromium/chromium_version.txt" ]; then
    echo "| Chromium version | \`$(cat "${UCPL_DIR}/ungoogled-chromium/chromium_version.txt")\` |"
  fi
  if [ -f "${UCPL_DIR}/ungoogled-chromium/revision.txt" ]; then
    echo "| ungoogled revision | \`$(cat "${UCPL_DIR}/ungoogled-chromium/revision.txt")\` |"
  fi
  if [ "$TREE" = "patched" ]; then
    echo "| our fingerprint patches | 16, from fingerprint-chromium \`${FINGERPRINT_TAG:-144.0.7559.132}\` |"
  else
    echo "| our fingerprint patches | NONE — this is the instrument check |"
  fi
  echo
  echo "## The two results, stated separately"
  echo
  echo "These are deliberately two rows and not one. Every figure the"
  echo "investigation produced measures the first; none measures the second."
  echo
  echo "| result | verdict |"
  echo "|---|---|"
  echo "| **1. Patches APPLIED** (as text) | $(say "$PREPARE_RESULT") |"
  echo "| **2. Tree COMPILED** | $(say "$COMPILE_RESULT") |"
  echo "| chrome binary on disk | ${BINARY_STATE} |"
  echo "| chrome sha256 | \`${BINARY_SHA}\` |"
  echo

  # The interpretation, written out so a reader does not have to derive it —
  # and so the three states stay distinguishable in prose as well as in a table.
  echo "### Reading of the above"
  echo
  if [ "$PREPARE_RESULT" = "failure" ]; then
    echo "The patches **did not apply**. The compile was therefore **never attempted**,"
    echo "which is a different statement from \"the compile failed\". Nothing in this"
    echo "run says anything about whether this tree would compile."
  elif [ "$COMPILE_RESULT" = "failure" ]; then
    echo "The patches **applied cleanly** and the tree **failed to compile**."
    echo
    if [ "$TREE" = "patched" ]; then
      echo "This is the measurement PS-218 exists to produce: a patch set that lands as"
      echo "text and still breaks the build. See \`attribution.txt\` for which of our 16"
      echo "patches each error belongs to, and which errors the unmodified control had too."
    else
      echo "**This is a finding about the BUILD ENVIRONMENT, not about our patches.**"
      echo "The instrument failed, so nothing may be attributed to the patch layer."
    fi
  elif [ "$COMPILE_RESULT" = "success" ] && [ -x "$CHROME" ]; then
    echo "The patches applied **and** the tree compiled, and a binary exists on disk."
    if [ "$TREE" = "unmodified" ]; then
      echo "The instrument is sound: this environment CAN build Chromium, so a failure"
      echo "on the patched tree is attributable to our patches rather than to the setup."
    fi
  elif [ "$COMPILE_RESULT" = "skipped" ]; then
    echo "The compile **was not attempted**. This is neither a pass nor a failure."
  else
    echo "Compile reported \`${COMPILE_RESULT}\` but the binary is ${BINARY_STATE}."
    echo "**Trust the binary over the exit code** and treat this as unresolved."
  fi
  echo

  echo "## Cost"
  echo
  # Wall-clock and peak memory are deliverables of this ticket, so they are in
  # the manifest itself rather than only in the raw timing files.
  for phase in prepare compile; do
    f="${REC}/${phase}-${TREE}-timing.txt"
    if [ -f "$f" ]; then
      echo "### ${phase}"
      echo '```'
      cat "$f"
      echo '```'
      echo
    fi
  done

  if [ -n "$NINJA_JOBS" ]; then
    echo "> ⚠️ **Ninja parallelism was REDUCED to \`-j${NINJA_JOBS}\`.**"
    echo "> The wall-clock figure above is therefore NOT comparable to the"
    echo "> 130-core-hours ÷ core-count prediction without accounting for this."
  else
    echo "> Ninja parallelism was **not** reduced (upstream default, one job per core)."
  fi
  echo

  echo "## Environment"
  echo
  echo '```'
  cat "${REC}/environment-${TREE}.txt" 2>/dev/null || echo "(not recorded)"
  echo '```'
} > "$OUT"

echo "wrote ${OUT}"
cat "$OUT"
