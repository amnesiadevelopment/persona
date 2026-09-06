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
# PS-244 — where this run's control came from. Empty on the ordinary `both`
# path, where the control is built in this very run; set to the borrowed run's
# id on a patched-only dispatch. This is READ, not asserted: it decides only
# what the manifest SAYS, while whether the borrow was allowed at all is decided
# by ps218_verify_control.sh, which stops the build before this script runs.
CONTROL_RUN_ID="${CONTROL_RUN_ID:-}"
# PS-307 — was the prepared source tree REUSED from a previous dispatch, or
# built fresh? "true"/"false" from ps307_tree_state.sh, or empty on a run that
# predates the mechanism. This is reported because a reused tree changes what
# the prepare wall-clock figure MEANS, and that figure is one of this workflow's
# deliverables: a reader comparing 40 seconds against 12 minutes must be able to
# see that the two measure different things rather than a machine that got
# faster.
TREE_REUSED="${TREE_REUSED:-}"
# PS-307 — the outcome of the tree-evidence check that our 16 fingerprint
# patches are actually IN the compiled tree. `success` is the only value that
# licenses the "16 fingerprint patches" claim below; anything else means the
# claim is unbacked and the manifest says so instead of asserting it.
PATCHES_VERIFIED="${PATCHES_VERIFIED:-}"

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
    # PS-307 — the patch-layer row states what was VERIFIED, never what was
    # intended. Before this, the row asserted "16" on the strength of the
    # staging step having run — and on a preserved tree staging can run
    # perfectly while upstream's apply_patches() skips itself and puts none of
    # them in the tree. The row now reports the tree-evidence check's verdict.
    case "$PATCHES_VERIFIED" in
      success)
        echo "| our fingerprint patches | 16, from fingerprint-chromium \`${FINGERPRINT_TAG:-144.0.7559.132}\` — **VERIFIED PRESENT IN THE TREE** (see \`patch-presence-patched.txt\`) |"
        ;;
      failure)
        echo "| our fingerprint patches | ❌ **NOT ALL PRESENT IN THE TREE** — the presence check FAILED. Nothing here may be read as a measurement of our patch layer. See \`patch-presence-patched.txt\`. |"
        ;;
      "")
        echo "| our fingerprint patches | 16 STAGED, presence in the tree NOT CHECKED (this run predates the PS-307 verification) |"
        ;;
      *)
        echo "| our fingerprint patches | 16 staged; presence check reported \`${PATCHES_VERIFIED}\` — treat the patch layer as UNCONFIRMED |"
        ;;
    esac
    # PS-244 — THE PROVENANCE OF THE CONTROL, IN THE SUMMARY TABLE.
    # An attribution resting on a control built in another run is a weaker
    # claim than one resting on an in-run control, and a reader must be able to
    # tell which they are holding without opening the workflow.
    if [ -n "$CONTROL_RUN_ID" ]; then
      echo "| control | ⚠️ **BORROWED** from run \`${CONTROL_RUN_ID}\` — this run did NOT build its own |"
    else
      echo "| control | IN-RUN — the unmodified tree was built by this dispatch |"
    fi
  else
    echo "| our fingerprint patches | NONE — this is the instrument check |"
  fi
  # PS-307 — reused or fresh, in the summary table where the cost figures are
  # read from. Both rows below are about the SAME dispatch, and a reader who
  # takes the prepare wall-clock without this row will misread it.
  case "$TREE_REUSED" in
    true)
      echo "| source tree | ♻️ **REUSED** from a previous dispatch — the prepare figure below is a WARM one and is NOT comparable to a cold prepare |"
      ;;
    false)
      echo "| source tree | BUILT FRESH this dispatch (the download cache may still have been reused) |"
      ;;
    *)
      echo "| source tree | not recorded (this run predates PS-307 tree reuse) |"
      ;;
  esac
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

  # ── PS-244: where the control came from, stated in PROSE as well ───────────
  # The table row above is the scannable form; this is the one a reader who
  # skips tables still meets. Both are needed because the claim being qualified
  # ("errors the control had too are NOT ours") is made in prose in
  # attribution.txt, and a qualification that lives only in a table is easy to
  # carry away unattached to the claim it qualifies.
  if [ "$TREE" = "patched" ]; then
    echo "### Where the control came from"
    echo
    if [ -n "$CONTROL_RUN_ID" ]; then
      echo "⚠️ **This run did not build its own control.** The unmodified tree it is"
      echo "compared against was built by run \`${CONTROL_RUN_ID}\` and BORROWED, which is"
      echo "what let this dispatch skip a full unmodified compile (PS-244)."
      echo
      echo "The borrow was **verified, not trusted**: before any of this ran, that run's"
      echo "own recorded evidence was checked to establish it built the **same ungoogled"
      echo "tag**, on the **same host**, and that it **actually compiled and left a"
      echo "binary**. Any of those failing stops the build rather than degrading it to an"
      echo "unattributed one. The per-check record is in"
      echo "\`control-borrow-verification.txt\`."
      echo
      echo "**Read the attribution accordingly.** \"Pre-existing — the unmodified tree had"
      echo "this too\" here means *the tree run \`${CONTROL_RUN_ID}\` built had it too*, not"
      echo "*a tree built alongside this one*. The two are the same tag on the same"
      echo "machine, which is why the borrow is permitted — but they are not the same"
      echo "build, and this manifest will not present them as though they were."
    else
      echo "The unmodified control was built **by this dispatch** (\`trees=both\`), so the"
      echo "attribution rests on an in-run control. This is the strongest form and the"
      echo "default."
    fi
    echo
  fi

  echo "## Cost"
  echo
  # PS-307 — the reuse note sits IMMEDIATELY ABOVE the timing blocks rather than
  # only in the table, because the number it qualifies is right below it and a
  # qualification a reader meets after the figure has already been taken is a
  # qualification that did not happen.
  if [ "$TREE_REUSED" = "true" ]; then
    echo "> ♻️ **The source tree was REUSED from a previous dispatch.** The prepare"
    echo "> figure below therefore measures a WARM start: the ~1.7 GB download, the"
    echo "> unpack, the toolchain fetch and the application of the 111 de-googling"
    echo "> patches were all SKIPPED because upstream's stamp files were already in"
    echo "> the preserved tree. It is not comparable to a cold prepare, and it says"
    echo "> nothing about how long preparing this tree from nothing takes."
    echo ">"
    echo "> The COMPILE figure is unaffected: ninja builds the same sources either way."
    echo
  elif [ "$TREE_REUSED" = "false" ]; then
    echo "> The source tree was built FRESH this dispatch, so the prepare figure below"
    echo "> is a cold one. (The download cache is preserved separately from the tree,"
    echo "> so the ~1.7 GB tarball may still not have been re-downloaded — see"
    echo "> \`tree-reuse-${TREE}.txt\` for what was on disk.)"
    echo
  fi
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

  # PS-307 — the reuse decision and the patch-presence verdict, in full, before
  # the environment dump. Both raw reports also travel in `record/`.
  echo "## Tree provenance (PS-307)"
  echo
  echo "Whether this dispatch reused a prepared tree, and — for the patched tree —"
  echo "whether our patches were proven to be IN it. These are separate questions:"
  echo "the first is about COST, the second is about whether the artifact's label is"
  echo "true. A stamp file can answer the first and cannot answer the second, which"
  echo "is why the presence check reads the source files instead."
  echo
  echo '```'
  cat "${REC}/tree-reuse-${TREE}.txt" 2>/dev/null || echo "(not recorded — this run predates PS-307 tree reuse)"
  echo '```'
  echo
  if [ -f "${REC}/patch-presence-${TREE}.txt" ]; then
    echo "### Are our patches in the tree?"
    echo
    echo '```'
    cat "${REC}/patch-presence-${TREE}.txt"
    echo '```'
    echo
  fi

  echo "### As the job found the machine (before prepare)"
  echo '```'
  cat "${REC}/environment-${TREE}.txt" 2>/dev/null || echo "(not recorded)"
  echo '```'
  echo
  # The SECOND pass is the one carrying the container/cgroup figures: the probe
  # needs the build image, and `prepare` is what creates it. Printing only the
  # first pass would reproduce the defect this fixed — a record asserting three
  # memory levels while holding one.
  echo "### As the BUILD CONTAINER sees it (after prepare)"
  echo
  echo "The ticket's instruction is explicit that these are not the same claim:"
  echo "\"WSL2's memory allocation is not the host's.\" This is the level that"
  echo "governs the link step, which is where ungoogled's FAQ places the common"
  echo "out-of-memory crash."
  echo
  echo '```'
  cat "${REC}/environment-${TREE}-post-prepare.txt" 2>/dev/null \
    || echo "(not recorded — the post-prepare pass did not run)"
  echo '```'
} > "$OUT"

echo "wrote ${OUT}"
cat "$OUT"
