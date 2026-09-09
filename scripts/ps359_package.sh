#!/bin/bash
# PS-359 — TURN THE COMPILED TREE INTO SOMETHING SOMEBODY ELSE CAN RUN.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE DEFECT THIS CLOSES
# ─────────────────────────────────────────────────────────────────────────────
# `engine-trial-build.yml` uploaded exactly two paths per arm — the `chrome` and
# `chromedriver` executables out of `out/Default`. Those are two files out of a
# RUNTIME TREE. Launched on any machine that did not build them, the binary dies
# immediately:
#
#     ERROR:base/i18n/icu_util.cc:232] Invalid file descriptor to ICU data received.
#     rc=133
#
# and that surfaces through our harness as "persona's chromium exited before
# opening a debug port" — a message that reads like a broken compile and is not
# one. PS-301 hit exactly this (`readings/ps301-2026-09-05/REPORT.md` §2.2), and
# resolved it by hand-staging the binary into a foreign, VERSION-SKEWED
# Chrome-for-Testing resource tree. It recorded the skew honestly and recorded
# the real fix as a finding it was not in scope to make. This script is that fix.
#
# ⚠️ THIS FAILURE MODE IS THE REASON THE SCRIPT EXISTS, SO READ IT ONCE MORE:
# a green build whose output does not run. Every check below is aimed at that
# one shape — the packaging step that is observed to produce a FILE and was
# never observed to produce a RUNNABLE one.
#
# ─────────────────────────────────────────────────────────────────────────────
# WE DO NOT OWN A FILE LIST, AND THAT IS THE CENTRAL DESIGN CONSTRAINT
# ─────────────────────────────────────────────────────────────────────────────
# The obvious repair is to widen the workflow's `path:` block with the runtime
# files the binary needs. DO NOT DO THAT, and do not "improve" this script into
# it later. A list of upstream's runtime files copied into our repository is a
# SECOND INVENTORY of somebody else's build output. It is correct on the day it
# is written and silently rots the first time upstream adds, renames or drops a
# file — and it rots INVISIBLY, because the symptom is the same ICU-shaped death
# on a machine that is not ours, months after the change that caused it.
#
# Upstream already ships the packaging step. It is a direct sibling of the build
# script we ALREADY drive (`scripts/build.sh`, via `scripts/docker-build.sh`, in
# ps218_build.sh). This script calls it and does nothing else about which files
# a Chromium runtime tree contains — that question is upstream's to answer, and
# their answer travels with their tag.
#
# So: no file names here, none in the workflow, none in the tests. A reviewer
# who greps our tree for any of upstream's runtime resource file names must find
# nothing. That absence IS the feature.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY `package/docker-package.sh` AND NEVER `scripts/package.sh` DIRECTLY
# ─────────────────────────────────────────────────────────────────────────────
# `scripts/package.sh` CANNOT run in the builder image. It pipes its tarball
# through `pv`, and it builds the AppImage with `appimagetool`. Neither is in
# `chromium-builder:trixie-slim`; both are installed only by upstream's separate
# `docker/package.Dockerfile`. Calling `package.sh` directly looks simpler, and
# it dies at `pv` AFTER a multi-hour compile — the most expensive possible
# moment to discover a missing dependency.
#
# `package/docker-package.sh` is the containerised driver: it builds that
# packager image and then runs `package.sh` INSIDE it. That is the entry point,
# and the indirection is load-bearing rather than ceremonial.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS SCRIPT ADDS AROUND THAT CALL, AND WHY EACH PART IS HERE
# ─────────────────────────────────────────────────────────────────────────────
# 1. IT DESTROYS ANY PRE-EXISTING RELEASE DIRECTORY FIRST.
#
#    Upstream writes into `build/release`, which lives inside `build/` — the
#    untracked tree the ucpl checkout deliberately PRESERVES between dispatches
#    (`clean: false`, PS-307). Upstream's own cleanup removes its two scratch
#    directories and LEAVES the release directory behind. So a previous
#    dispatch's output is sitting there when this runs, and an output directory
#    whose contents predate the run reporting them is the exact stale-evidence
#    shape PS-244 and PS-307 both refuse.
#
#    Verifying freshness by timestamp would be the weaker answer. This removes
#    the directory outright and records an inventory of what was removed, so
#    everything present afterwards is THIS RUN'S BY CONSTRUCTION rather than by
#    inspection. Nothing is silently destroyed: the inventory is in the record.
#
# 2. IT PROVES THE SUBMODULE INIT IS A NO-OP INSTEAD OF ASSUMING IT.
#
#    `docker-package.sh` runs `git submodule update --init --recursive` if it
#    finds the `ungoogled-chromium` directory empty. Our checkout already uses
#    `submodules: recursive`, so it should never fire — but "should" is not a
#    measurement, and a submodule update reaching the network mid-package is
#    worth knowing about rather than assuming away. The state is read and
#    recorded BEFORE the call, so the record says which branch was taken.
#
# 3. IT NAMES THE ARM IN OUR OWN LAYER, WITHOUT TOUCHING UPSTREAM'S NAMING.
#
#    Upstream derives the artifact name from the ungoogled tag alone, with the
#    application name hardcoded. Both of our arms build the SAME tag, so both
#    emit a file with the SAME name. The GitHub artifact names differ, but a
#    reader who downloads both ends up with two identically-named files and no
#    way to tell the patched one from the control — the precise confusion
#    PS-244's artifact-name provenance rules exist to prevent.
#
#    Solved HERE, in our staging layer: each produced file is copied into
#    `package-out/<tree>/` under a `ps218-<tree>-` prefix, matching the existing
#    artifact-name convention in this workflow. Upstream's script is not edited
#    and upstream's own name is preserved inside the prefix, so provenance is
#    still legible.
#
#    ⛔ THIS IS NOT, AND MUST NOT BECOME, THE PUBLICATION NAME. The
#    `personium-…` name belongs to RELEASING.md and to PS-319. Renaming toward
#    it here is how "make it runnable" turns into "make it publishable", which
#    is a different ticket with a different owner.
#
# 4. IT WRITES A PROVENANCE SIDECAR AND A VERDICT FILE.
#
#    Same posture as ps218_build.sh's `.provenance` stamps: a file that is
#    present is not thereby a file from this run. Every staged artifact is
#    recorded with its size and sha256 next to the run and tag that produced it.
#
# ⚠️ NOTHING HERE PUBLISHES ANYTHING. No release is created, no tag is moved, no
# ref is written, and the workflow's token holds no write scope with which to do
# any of those. This produces an artifact attached to a run, and stops.
set -euo pipefail

TREE="${1:?usage: ps359_package.sh <unmodified|patched>}"
UCPL_DIR="${UCPL_DIR:?UCPL_DIR must point at the ungoogled-chromium-portablelinux checkout}"
UNGOOGLED_TAG="${UNGOOGLED_TAG:-unknown}"

case "$TREE" in
  unmodified|patched) ;;
  *) echo "unknown tree: $TREE (expected unmodified|patched)" >&2; exit 2 ;;
esac

REC="$(pwd)/record"
STAGE="$(pwd)/package-out/${TREE}"
mkdir -p "$REC"

REPORT="${REC}/package-${TREE}.txt"
LOG="${REC}/package-${TREE}.log"

# Resolved to an absolute path BEFORE any `cd`, for the same reason
# ps218_build.sh resolves its journal path early.
UCPL_ABS="$(cd "$UCPL_DIR" && pwd)"

DRIVER="${UCPL_ABS}/package/docker-package.sh"
RELEASE_DIR="${UCPL_ABS}/build/release"
SUBMODULE_DIR="${UCPL_ABS}/ungoogled-chromium"

{
  echo "# PS-359 — packaging the compiled tree with UPSTREAM'S OWN packager"
  echo "# tree:      ${TREE}"
  echo "# tag:       ${UNGOOGLED_TAG}"
  echo "# run:       ${GITHUB_RUN_ID:-local} (attempt ${GITHUB_RUN_ATTEMPT:-1})"
  echo "# recorded:  $(date -Is)"
  echo "#"
  echo "# No list of runtime files appears in this repository. Which files a"
  echo "# Chromium runtime tree needs is upstream's question, answered by"
  echo "# upstream's own script at the tag we build, so it can never drift out"
  echo "# of step with the tree it describes."
  echo
} > "$REPORT"

say() { echo "$@" | tee -a "$REPORT"; }

# ── the driver must exist ────────────────────────────────────────────────────
# Checked by hand rather than left to `set -e` on the invocation, because the
# two failures are different findings: "upstream moved its packaging entry
# point at this tag" is a fact about the tag, and it must not read like a
# packaging crash.
if [ ! -f "$DRIVER" ]; then
  say "verdict:          FAILED — upstream's packaging driver is not present at this tag"
  say "expected driver:  ${DRIVER}"
  say ""
  say "This is a statement about the TAG, not about the build. The compiled tree"
  say "is untouched and is still on disk. Do NOT respond by hand-rolling a file"
  say "list: find where upstream moved its packaging entry point, and call that."
  exit 1
fi
say "driver:           ${DRIVER}"

# ── HAZARD 4: destroy any release directory left by an earlier dispatch ──────
# `build/` survives between dispatches by design, upstream's cleanup leaves
# `release/` behind, and stale output presented as this run's is the failure
# both PS-244 and PS-307 are built to refuse. Removing beats verifying: what is
# there afterwards is this run's because nothing else could have put it there.
say ""
say "## Pre-existing release directory (PS-307 preserves \`build/\` between dispatches)"
if [ -d "$RELEASE_DIR" ]; then
  say "state:            PRESENT before this run — inventory recorded, then REMOVED"
  # Recorded before removal, so nothing is destroyed unread.
  find "$RELEASE_DIR" -maxdepth 1 -mindepth 1 -printf '    %y %10s  %f\n' 2>/dev/null \
    | sort >> "$REPORT" || true
  rm -rf "$RELEASE_DIR"
  say "removed:          yes — anything below is THIS run's by construction, not by inspection"
else
  say "state:            absent — this is a cold package"
fi

# ── HAZARD 5: is upstream's submodule init going to fire? ────────────────────
# The driver runs `git submodule update --init --recursive` only when it finds
# this directory empty. Our checkout uses `submodules: recursive`, so it should
# not — recorded rather than assumed, because a submodule update reaching the
# network in the middle of packaging is worth seeing.
say ""
say "## Upstream's conditional submodule init"
if [ -n "$(ls -A "$SUBMODULE_DIR" 2>/dev/null || true)" ]; then
  say "submodule dir:    POPULATED ($(find "$SUBMODULE_DIR" -maxdepth 1 -mindepth 1 | wc -l) entries)"
  say "consequence:      upstream's \`git submodule update --init --recursive\` is a NO-OP"
  say "                  — the workflow's own \`submodules: recursive\` checkout already did it"
else
  say "submodule dir:    EMPTY"
  say "consequence:      upstream WILL run \`git submodule update --init --recursive\`,"
  say "                  which reaches the network. The checkout's \`submodules: recursive\`"
  say "                  did not take effect — worth investigating rather than ignoring."
fi

# ── run upstream's packager ──────────────────────────────────────────────────
# `cd` to the ucpl root first: the driver's `docker buildx build … .` uses the
# CURRENT directory as its build context, so where this is invoked from matters.
say ""
say "## Invoking upstream's packager"
say "command:          package/docker-package.sh   (NOT scripts/package.sh — see the header)"

pkg_start="$(date +%s)"
set +e
( cd "$UCPL_ABS" && bash "$DRIVER" ) 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
set -e
pkg_end="$(date +%s)"
elapsed=$((pkg_end - pkg_start))

say "elapsed:          $((elapsed / 60))m $((elapsed % 60))s"
say "exit code:        ${rc}"

if [ "$rc" -ne 0 ]; then
  say ""
  say "verdict:          FAILED — packaging did not complete"
  say ""
  say "⚠️ THIS IS A PACKAGING FAILURE, NOT A COMPILE FAILURE. The tree compiled;"
  say "   what failed is turning it into a runnable artifact. Read the two apart"
  say "   before concluding anything about the patch layer."
  say ""
  say "The most likely cause is NOT the build. Upstream's packager image is built"
  say "fresh here, and building it reaches the network UNAUTHENTICATED: it queries"
  say "the GitHub releases API for its AppImage tool and then downloads it. On a"
  say "self-hosted runner that is an unauthenticated API call subject to rate"
  say "limiting, and a rate limit is an infrastructure condition, not a defect in"
  say "this repository. Check \`package-${TREE}.log\` for where it stopped before"
  say "attributing this anywhere near the compile."
  exit 1
fi

# ── what did it actually produce? ────────────────────────────────────────────
# Read from DISK. An exit code says what a script claimed; the files say what
# exists — the same rule ps218_build.sh applies to the chrome binary, and the
# rule that matters most here, because "it produced a file" is the very claim
# this ticket refuses to accept on its own.
if [ ! -d "$RELEASE_DIR" ]; then
  say ""
  say "verdict:          FAILED — packaging reported success and produced no output directory"
  say "Trust the filesystem over the exit code."
  exit 1
fi

mkdir -p "$STAGE"

say ""
say "## Produced artifacts, staged with THIS ARM's provenance in the name"
say ""
say "Upstream derives its artifact name from the ungoogled tag alone, so BOTH"
say "arms emit an identically-named file. The GitHub artifact names differ, but a"
say "reader who downloads both would hold two files with the same name and no way"
say "to tell the patched one from the control. The prefix below is applied in OUR"
say "staging layer; upstream's script and upstream's own name are untouched."
say ""

staged=0
total_bytes=0
appimage_name=""
appimage_bytes=0

# `-maxdepth 1 -type f`: upstream's own cleanup removes its scratch directories,
# so the release directory holds the finished artifacts and nothing else. Any
# directory left there is deliberately NOT staged — it would be an unfinished
# intermediate, and shipping one as a deliverable is the shape of failure this
# whole script is written against.
while IFS= read -r src; do
  base="$(basename "$src")"
  dest="${STAGE}/ps218-${TREE}-${base}"
  cp -p "$src" "$dest"
  bytes="$(stat -c %s "$dest")"
  sha="$(sha256sum "$dest" | cut -d' ' -f1)"
  staged=$((staged + 1))
  total_bytes=$((total_bytes + bytes))
  say "  ${base}"
  say "      staged as:  ps218-${TREE}-${base}"
  say "      bytes:      ${bytes}  ($(awk -v b="$bytes" 'BEGIN{printf "%.1f", b/1048576}') MiB)"
  say "      sha256:     ${sha}"
  # Recorded for the launch falsification: it is the single-file runnable, and
  # the criterion this ticket refuses to waive is that it LAUNCHES, not that it
  # exists.
  case "$base" in
    *.AppImage)
      appimage_name="ps218-${TREE}-${base}"
      appimage_bytes="$bytes"
      ;;
  esac
done < <(find "$RELEASE_DIR" -maxdepth 1 -type f | sort)

if [ "$staged" -eq 0 ]; then
  say ""
  say "verdict:          FAILED — the packager exited 0 and staged nothing"
  say "Trust the filesystem over the exit code."
  exit 1
fi

# ── provenance sidecar ───────────────────────────────────────────────────────
# Same reason ps218_build.sh stamps its logs: `build/` survives between
# dispatches on a self-hosted runner, so a file that is PRESENT is not thereby a
# file from THIS run. The removal above makes that true by construction; this
# makes it legible to a reader holding only the downloaded artifact.
{
  echo "tree=${TREE}"
  echo "ungoogled_tag=${UNGOOGLED_TAG}"
  echo "github_run_id=${GITHUB_RUN_ID:-local}"
  echo "github_run_attempt=${GITHUB_RUN_ATTEMPT:-1}"
  echo "packaged_by=upstream package/docker-package.sh at tag ${UNGOOGLED_TAG}"
  echo "packaged_at=$(date -Is)"
  echo "artifact_count=${staged}"
  echo "total_bytes=${total_bytes}"
} > "${STAGE}/PROVENANCE.txt"

say ""
say "artifacts staged: ${staged}"
say "total bytes:      ${total_bytes}  ($(awk -v b="$total_bytes" 'BEGIN{printf "%.1f", b/1048576}') MiB)"
say ""
say "verdict:          PACKAGED"
say ""
say "⚠️ WHAT THIS DOES AND DOES NOT ESTABLISH"
say ""
say "  DOES: upstream's packager ran to completion and produced the files above,"
say "        from the tree this job compiled, with no file list of ours involved."
say ""
say "  DOES NOT: that the artifact LAUNCHES. A packaging step observed only to"
say "        produce a file is not known to produce a runnable one, and the ICU"
say "        failure this ticket exists to close is exactly the shape of a green"
say "        step whose output does not run. That is settled by launching it and"
say "        reading a page through it, on a machine that did not build it."

if [ -n "${GITHUB_OUTPUT:-}" ] && [ "${GITHUB_OUTPUT}" != "/dev/null" ]; then
  {
    echo "packaged=true"
    echo "artifact_count=${staged}"
    echo "total_bytes=${total_bytes}"
    echo "appimage=${appimage_name}"
    echo "appimage_bytes=${appimage_bytes}"
  } >> "$GITHUB_OUTPUT"
fi

echo "package report -> ${REPORT}"
echo "staged artifacts -> ${STAGE}"
