#!/bin/bash
# PS-307 — REUSE the prepared Chromium tree between dispatches, safely.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE SAVING, AND THE THING THAT MAKES IT DANGEROUS
# ─────────────────────────────────────────────────────────────────────────────
# Every dispatch of `engine-trial-build` currently rebuilds the tree from
# nothing: download the ~1.7 GB tarball, unpack it, fetch the Rust and Clang
# toolchains, apply 111 de-googling patches, domain-substitute, `gn gen`. During
# the PS-299 rebase loop that is paid on every iteration, including on runs that
# die minutes later for reasons that have nothing to do with the tree.
#
# Preserving the tree (`clean: false` on the ucpl checkout) makes upstream's own
# stamp files do the skipping for us. At tag 152.0.7977.75-1, `scripts/shared.sh`
# guards each phase with a stamp INSIDE the source tree:
#
#     fetch_sources()  →  ${_src_dir}/.downloaded.stamp   (skips download+unpack)
#     apply_patches()  →  ${_src_dir}/.patched.stamp      (skips prune + patches.py apply)
#     apply_domsub()   →  ${_src_dir}/.domsub.stamp       (skips domain substitution)
#
# `.patched.stamp` records THAT patching happened, never WHICH series was
# applied. Our 16 fingerprint patches are appended to `patches/series` by
# ps218_stage_patches.sh — and `patches/series` lives OUTSIDE the source tree
# while the stamp lives INSIDE it. So a preserved, already-stamped tree makes
# `apply_patches()` a complete no-op, our 16 never enter the tree, and the
# compile succeeds and is labelled as carrying them. That is the failure this
# workflow's header forbids in as many words: "A build made to succeed by
# quietly dropping a patch measures nothing."
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY "JUST DELETE .patched.stamp" IS NOT THE FIX — MEASURED, NOT ASSUMED
# ─────────────────────────────────────────────────────────────────────────────
# The obvious repair is to remove `.patched.stamp` before the patched job
# prepares, so `apply_patches()` runs again and picks up the longer series. It
# does not work, and it fails in a way worth writing down.
#
# `apply_patches()` re-runs the WHOLE series, not the tail of it. The 111
# de-googling patches are already applied to the preserved tree, and GNU patch
# refuses an already-applied hunk:
#
#     $ patch -p1 --forward -i p.patch -d src      # first time
#     patching file f.txt                          → rc=0
#     $ patch -p1 --forward -i p.patch -d src      # second time
#     Reversed (or previously applied) patch detected!  Skipping patch.
#     1 out of 1 hunk ignored                      → rc=1
#
# and `utils/patches.py` runs each `patch` with `subprocess.run(cmd, check=True)`,
# so that rc=1 raises and the prepare phase dies. Deleting `.patched.stamp` on a
# preserved tree therefore does not silently drop patches — it breaks the build
# outright. Which is safe, but useless.
#
# So the stamps are not something to edit individually. They describe a tree
# STATE, and the honest unit of reuse is the whole state.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS SCRIPT DOES INSTEAD: REUSE THE TREE ONLY WHEN IT IS *THIS* TREE
# ─────────────────────────────────────────────────────────────────────────────
# After a successful prepare, the tree is SEALED with an identity naming exactly
# what it is:
#
#     ungoogled_tag       — which upstream tag it was built from
#     tree                — `unmodified` (111 de-googling patches) or `patched`
#                           (those plus our 16)
#     fingerprint_digest  — a digest over the CONTENT of our 16 patch files, so
#                           a rebase that changes a single hunk changes the
#                           identity
#
# On the next dispatch, `plan` compares the identity on disk against the
# identity THIS job needs. Equal → reuse, and the whole prepare collapses to
# almost nothing. Different in any field → the tree is DESTROYED and rebuilt.
#
# That is the same posture PS-244 established for the borrowed control and
# ps218_attribute.sh for the control log: an input from a previous run is
# VERIFIED, not trusted, and a mismatch stops rather than degrades. A tree from
# another tag is worse than no tree.
#
#   trees=both      the control seals `unmodified`, then the patched job finds a
#                   `unmodified` identity, refuses it, and rebuilds. The DOWNLOAD
#                   is still reused (see below), which is the bulk of the win on
#                   that path.
#   trees=patched   the rebase loop. A re-dispatch after an unrelated flake finds
#                   the identity it wants and skips the entire prepare. This is
#                   the case the operator actually asked for.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE DOWNLOAD CACHE IS PRESERVED EVEN WHEN THE TREE IS NOT
# ─────────────────────────────────────────────────────────────────────────────
# `build/download_cache` is a SIBLING of `build/src`, not a child of it
# (shared.sh's setup_paths). A wipe therefore takes `build/src` alone and leaves
# the cache, so even a full rebuild skips the ~1.7 GB download: `downloads.py`'s
# `_download_if_needed` returns early when the file is already there. The cached
# files are named for the Chromium version they carry, so a different tag simply
# does not hit them rather than mis-hitting them.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE SEAL IS BROKEN BEFORE PREPARE AND REWRITTEN AFTER IT
# ─────────────────────────────────────────────────────────────────────────────
# `plan` deletes the identity file whether it decided to reuse or to wipe, and
# `seal` writes it back only once prepare has SUCCEEDED. A run killed in the
# middle of prepare — the OOM, the segfault, the operator closing the laptop —
# therefore leaves an UNSEALED tree, and the next dispatch rebuilds instead of
# inheriting a half-mutated one. An interrupted run is exactly the "stale tree"
# the ticket names, and this is what makes it unreusable by construction rather
# than by anyone noticing.
#
# ⚠️ AND THE SEAL IS NOT THE SAFETY PROPERTY. It decides whether a prepare is
# SKIPPED, which is a cost question. Whether the tree that gets compiled actually
# carries our 16 patches is settled separately by
# ps307_verify_patches_in_tree.sh, which reads the SOURCE FILES and never this
# file. A stamp is the thing that lies in this whole story; it does not get to
# vouch for itself.
set -euo pipefail

MODE="${1:?usage: ps307_tree_state.sh <plan|seal> <unmodified|patched>}"
TREE="${2:?usage: ps307_tree_state.sh <plan|seal> <unmodified|patched>}"
UCPL_DIR="${UCPL_DIR:?UCPL_DIR must point at the ungoogled-chromium-portablelinux checkout}"
UNGOOGLED_TAG="${UNGOOGLED_TAG:-unknown}"
# Only meaningful for the patched tree; the control has no fingerprint layer.
PATCH_DIR="${PATCH_DIR:-}"

case "$TREE" in
  unmodified|patched) ;;
  *) echo "::error::PS-307: tree must be 'unmodified' or 'patched', got '${TREE}'"; exit 2 ;;
esac

SRC_DIR="${UCPL_DIR}/build/src"
CACHE_DIR="${UCPL_DIR}/build/download_cache"
IDENTITY="${SRC_DIR}/.persona-tree-identity"

# Bumped whenever the identity format changes. An unrecognised schema is treated
# as a mismatch, so a format change rebuilds the tree rather than misreading an
# older stamp as agreement.
SCHEMA=1

mkdir -p record
REPORT="record/tree-reuse-${TREE}.txt"

# ── the digest of our patch layer ───────────────────────────────────────────
# Over the CONTENT of the 16 files, not their names: a rebase that adjusts one
# hunk must change this, or the tree it produced would be reused for a patch set
# it does not carry. `sha256sum` output is sorted so the digest does not depend
# on directory order.
fingerprint_digest() {
  if [ "$TREE" != "patched" ]; then
    echo "none"
    return 0
  fi
  if [ -z "$PATCH_DIR" ] || [ ! -d "$PATCH_DIR" ]; then
    # Not a soft failure. Without the digest the identity cannot distinguish one
    # patch set from another, which is the entire point of it.
    echo "::error::PS-307: PATCH_DIR ('${PATCH_DIR}') is not a directory, so the fingerprint patch layer cannot be digested." >&2
    echo "::error::PS-307: refusing to compute a tree identity that could not tell one patch set from another." >&2
    exit 1
  fi
  ( cd "$PATCH_DIR" && sha256sum ./*.patch 2>/dev/null | sort ) | sha256sum | cut -d' ' -f1
}

want_digest="$(fingerprint_digest)"

read_identity_field() {
  # `.persona-tree-identity` is ours and is `key=value`, one per line.
  [ -f "$IDENTITY" ] || return 1
  awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; found=1; exit } END { exit !found }' "$IDENTITY"
}

case "$MODE" in

  # ═══════════════════════════════════════════════════════════════════════════
  # plan — decide, and act on the decision, BEFORE prepare runs
  # ═══════════════════════════════════════════════════════════════════════════
  plan)
    echo "== PS-307: deciding whether the prepared tree on disk can be reused =="
    echo "tree:            ${TREE}"
    echo "ungoogled tag:   ${UNGOOGLED_TAG}"
    echo "source dir:      ${SRC_DIR}"
    echo "download cache:  ${CACHE_DIR}"
    echo

    REUSED=false
    REASON=""

    if [ ! -d "$SRC_DIR" ]; then
      REASON="no prepared tree on disk (${SRC_DIR} does not exist) — this is a cold start"
    elif [ ! -f "$IDENTITY" ]; then
      # The common and important case: a tree left by a run that did not finish
      # preparing, or by a build predating this mechanism. Both are trees whose
      # contents nothing vouches for.
      REASON="the tree on disk is UNSEALED (no ${IDENTITY##*/}) — it was left by an interrupted run or predates PS-307, so nothing states what it contains"
    else
      have_schema="$(read_identity_field schema || echo '')"
      have_tag="$(read_identity_field ungoogled_tag || echo '')"
      have_tree="$(read_identity_field tree || echo '')"
      have_digest="$(read_identity_field fingerprint_digest || echo '')"

      echo "identity found on disk:"
      sed 's/^/    /' "$IDENTITY"
      echo

      if [ "$have_schema" != "$SCHEMA" ]; then
        REASON="identity schema is '${have_schema}', this build speaks '${SCHEMA}' — an unrecognised stamp is a mismatch, never an assumed agreement"
      elif [ "$have_tag" != "$UNGOOGLED_TAG" ]; then
        REASON="the tree was prepared for ungoogled tag '${have_tag}', this dispatch builds '${UNGOOGLED_TAG}' — a tree from another tag is worse than no tree"
      elif [ "$have_tree" != "$TREE" ]; then
        REASON="the tree on disk is the '${have_tree}' tree, this job needs the '${TREE}' tree — upstream's apply_patches() re-runs the WHOLE series, so a differently-patched tree cannot be converted into this one"
      elif [ "$have_digest" != "$want_digest" ]; then
        REASON="our fingerprint patch layer has CHANGED since this tree was prepared (tree carries ${have_digest:0:16}…, this dispatch stages ${want_digest:0:16}…)"
      else
        REUSED=true
        REASON="the tree on disk is this exact tree: same tag (${UNGOOGLED_TAG}), same role (${TREE}), same patch layer (${want_digest:0:16}…)"
      fi
    fi

    if [ "$REUSED" = true ]; then
      echo "== PS-307: REUSING the prepared tree =="
      echo "   ${REASON}"
      echo
      echo "upstream's stamps present in the reused tree:"
      for s in .downloaded.stamp .patched.stamp .domsub.stamp; do
        if [ -f "${SRC_DIR}/${s}" ]; then
          echo "    ${s}   present  → the phase it guards will be SKIPPED"
        else
          echo "    ${s}   absent   → the phase it guards will RUN"
        fi
      done
      echo
      echo "⚠️  This decides only what prepare SKIPS. Whether the compiled tree"
      echo "    actually carries our patches is settled by"
      echo "    ps307_verify_patches_in_tree.sh, from the source files themselves."
    else
      echo "== PS-307: DESTROYING the tree and preparing a fresh one =="
      echo "   ${REASON}"
      echo
      if [ -d "$SRC_DIR" ]; then
        # `build/src` only. `build/download_cache` is its SIBLING and is kept, so
        # even a full rebuild skips the ~1.7 GB download.
        echo "removing ${SRC_DIR} (keeping ${CACHE_DIR}) ..."
        wipe_start="$(date +%s)"
        rm -rf "$SRC_DIR"
        wipe_end="$(date +%s)"
        echo "removed in $((wipe_end - wipe_start))s"
      fi
      if [ -d "$CACHE_DIR" ]; then
        echo "download cache KEPT: $(find "$CACHE_DIR" -maxdepth 1 -type f | wc -l) file(s) — the tarball download will be skipped"
        find "$CACHE_DIR" -maxdepth 1 -type f -printf '    %f  %s bytes\n' 2>/dev/null || true
      else
        echo "download cache is absent — this dispatch pays the full ~1.7 GB download"
      fi
    fi
    echo

    # ── BREAK THE SEAL, ALWAYS ────────────────────────────────────────────────
    # Including on the reuse path. From here until `seal` runs, the tree is
    # unsealed, so a run that dies inside prepare leaves a tree the next dispatch
    # will rebuild rather than inherit. There is no path that leaves a seal
    # standing over a prepare that did not finish.
    rm -f "$IDENTITY"

    {
      echo "# PS-307 — tree reuse decision (tree: ${TREE})"
      echo "# recorded: $(date -Is)"
      echo
      echo "reused: ${REUSED}"
      echo "reason: ${REASON}"
      echo "ungoogled_tag: ${UNGOOGLED_TAG}"
      echo "fingerprint_digest: ${want_digest}"
      echo "src_dir: ${SRC_DIR}"
      echo "download_cache_present: $([ -d "$CACHE_DIR" ] && echo true || echo false)"
      echo
      echo "# What this does and does NOT establish:"
      echo "#   DOES: whether the prepare phase below started from an existing tree,"
      echo "#         which is what the prepare wall-clock figure must be read against."
      echo "#   DOES NOT: whether our 16 fingerprint patches are in the compiled tree."
      echo "#         That is ps307_verify_patches_in_tree.sh's answer, read from the"
      echo "#         source files. The stamp is the thing that lies in this story."
    } > "$REPORT"

    if [ -n "${GITHUB_OUTPUT:-}" ] && [ "${GITHUB_OUTPUT}" != "/dev/null" ]; then
      echo "reused=${REUSED}" >> "$GITHUB_OUTPUT"
    fi

    echo "recorded -> ${REPORT}"
    ;;

  # ═══════════════════════════════════════════════════════════════════════════
  # seal — run ONLY after a successful prepare
  # ═══════════════════════════════════════════════════════════════════════════
  seal)
    echo "== PS-307: sealing the prepared tree so the next dispatch can identify it =="

    if [ ! -d "$SRC_DIR" ]; then
      echo "::error::PS-307: cannot seal — ${SRC_DIR} does not exist. Prepare did not produce a tree."
      exit 1
    fi

    # A tree with no `.downloaded.stamp` never completed fetch_sources, whatever
    # the step outcome said. Sealing it would hand the next dispatch a tree that
    # skips a download it never actually did.
    if [ ! -f "${SRC_DIR}/.downloaded.stamp" ]; then
      echo "::error::PS-307: cannot seal — ${SRC_DIR}/.downloaded.stamp is missing, so upstream's fetch_sources() never completed."
      echo "::error::PS-307: sealing here would let the next dispatch skip a download this one did not finish."
      exit 1
    fi
    if [ ! -f "${SRC_DIR}/.patched.stamp" ]; then
      echo "::error::PS-307: cannot seal — ${SRC_DIR}/.patched.stamp is missing, so upstream's apply_patches() never completed."
      exit 1
    fi

    {
      echo "schema=${SCHEMA}"
      echo "ungoogled_tag=${UNGOOGLED_TAG}"
      echo "tree=${TREE}"
      echo "fingerprint_digest=${want_digest}"
      echo "sealed_run=${GITHUB_RUN_ID:-local}"
      echo "sealed_attempt=${GITHUB_RUN_ATTEMPT:-1}"
      echo "sealed_at=$(date -Is)"
    } > "$IDENTITY"

    echo "sealed ${IDENTITY}:"
    sed 's/^/    /' "$IDENTITY"

    {
      echo
      echo "# sealed after a successful prepare"
      sed 's/^/sealed_identity: /' "$IDENTITY"
    } >> "$REPORT"
    ;;

  *)
    echo "::error::PS-307: unknown mode '${MODE}' (expected 'plan' or 'seal')"
    exit 2
    ;;
esac
