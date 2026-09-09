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
#
# ─────────────────────────────────────────────────────────────────────────────
# THE TWO MODES DECIDE DIFFERENTLY, AND THEY HAVE TO (PS-382)
# ─────────────────────────────────────────────────────────────────────────────
# "A patch passes when every one of its claims holds" is stated above and is
# right for `present`. Read as a symmetric rule it makes `absent` fire when ANY
# ONE claim holds — and that is what shipped until PS-382, with a perverse
# consequence: the MORE evidence a patch carries, the MORE likely its
# absent-check false-positives, because every claim is an independent chance of
# an accidental upstream match. Richer evidence should mean more confidence, not
# more fragility.
#
# It fired for real. Run 34405524686 (engine-trial-build on `main` @ ef87ca3)
# failed the whole job on ONE of `014-client-rects.patch`'s 16 claims:
#
#     const auto [min, max] = Extents();
#
# That is a plain C++17 structured binding over an API upstream already ships,
# and vanilla Chromium 152.0.7977.75 writes it verbatim at `quad_f.cc:171`
# inside `QuadF::IntersectsRect` — nothing to do with our patch. The control was
# clean. The instrument was wrong, and it stopped a build that had every right
# to run.
#
# So `absent` now requires CORROBORATION: a patch is called present in the
# control when a MAJORITY of its claims hold (`hits * 2 > claims`).
#
#   * `present` is unchanged and stays all-or-nothing. Its failure direction is
#     safe — it refuses to compile — so there is no reason to relax it.
#   * `absent` fails when the evidence AGREES, not when any single piece of it
#     does. One coincidence no longer condemns a tree; a contaminated one is
#     still caught, because a patch that is actually IN the tree holds nearly
#     all of its claims (a real contamination measures 16/16, not 1/16).
#
# ⚠️ WHY A FRACTION AND NOT A FIXED `N > 1`. 009-webdriver.patch and
# 010-headless.patch yield exactly ONE claim each. Any fixed threshold above one
# makes those two patches UNDETECTABLE in the control forever: a genuinely
# contaminated tree carrying 009 would pass silently. That converts a false
# positive into a false negative, which is strictly worse here — a false
# positive stops a build that should have run, a false negative lets a
# contaminated control attribute things it cannot attribute. The majority rule
# degrades to "its single claim decides" at claims=1, which keeps full
# sensitivity exactly where no corroboration is available to ask for.
#
# ⚠️ AND THE UNCORROBORATED CASE IS NAMED IN THE REPORT rather than left for a
# reader to work out. A verdict resting on one claim is a weaker statement than
# one resting on nine, and the operator looking at a red build is entitled to
# know which they are holding — that is the exact distinction whose absence cost
# a build here.
#
# ⛔ A SUB-THRESHOLD HIT IS NOT SILENCE. Claims that hold but do not reach the
# majority are recorded as NOTED COINCIDENCE, in the report and on stdout, and
# the run stays green. Passing quietly would hide the drift until it crossed the
# threshold and failed a build with no history; this way the coincidence is
# visible on the green run that precedes it, and a claim that has stopped
# discriminating can be re-chosen deliberately.
#
# ⛔ WHAT WAS NOT DONE, DELIBERATELY: the failing claim was not deleted and
# `014` was not allowlisted. Either would have gone green today by blinding the
# instrument at the exact site it was built to watch. The evidence extractor now
# RANKS candidates by specificity instead (see ps307_patch_evidence.awk), so the
# generic idiom is no longer chosen while `if (WithinEpsilon(width, 0.0f) ...` —
# twice as long and genuinely ours — sits unused two lines below it. The two
# changes are complementary and neither is sufficient alone: ranking lowers the
# RATE of accidental matches, corroboration stops any single one from condemning
# a tree.
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

# PS-408: the measured set of lines our patches add that UNMODIFIED upstream
# already writes. Such a line holds against a clean control and proves nothing,
# so the extractor drops it as a candidate. Defaulted to the vendored file and
# overridable, and the extractor is inert when it is unset — see filter 5 in
# ps307_patch_evidence.awk, and scripts/ps408_upstream_claim_sweep.sh which
# regenerates it against the real upstream source at the pinned tag.
UPSTREAM_LINES="${PS307_UPSTREAM_LINES:-${PATCH_DIR}/UPSTREAM_LINES.txt}"
if [ ! -f "$UPSTREAM_LINES" ]; then
  # Named rather than silently ignored: a missing file means every claim is
  # back in play, which is a real change in what this gate checks.
  echo "::warning::PS-408: ${UPSTREAM_LINES} not found — upstream-collision filtering is OFF for this run."
  UPSTREAM_LINES=""
fi

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

# How many CHECKABLE claims an evidence file carries — every record except the
# `noevidence` diagnostics, which the consumer loop skips without checking.
#
# Split out as a function rather than inlined so the guard above and the loop
# below cannot drift apart about what counts as a claim: this is the same
# predicate the loop applies (`kind = noevidence` → `continue`), stated once.
count_claims() {
  awk -F'\t' '$1 != "" && $2 != "noevidence"' "$1" | wc -l
}

FAILURES=0
CHECKED=0
PATCHES_OK=0
PATCHES_BAD=0
# Claims that hold in `absent` mode but stay under the corroboration threshold.
# Counted so the summary can state them: a green run that noted a coincidence is
# a different fact from a green run that noted none, and only one of them is a
# claim quietly drifting toward failing a build.
COINCIDENCES=0

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
  #
  # ⚠️ `LC_ALL=C` IS LOAD-BEARING, NOT TIDINESS (PS-382). The extractor measures
  # candidates with `length()`, and awk implementations disagree about what that
  # counts: mawk counts BYTES, and gawk counts CHARACTERS in a UTF-8 locale.
  # 014-client-rects.patch adds the comment `// 计算轴对齐边界框的宽高` — 36 bytes
  # but 14 characters — so under gawk it falls below the 30-character floor of
  # filter 1 and is discarded, while under mawk it is kept. It was measured
  # producing DIFFERENT EVIDENCE on the two: gawk dropped the comment and
  # re-selected `const auto [min, max] = Extents();`, the very line whose
  # accidental match with upstream failed run 34405524686.
  #
  # That divergence pre-dates PS-382 (the floor is original), but it only became
  # CONSEQUENTIAL once selection started ranking candidates rather than taking
  # the first three — so it is pinned here rather than left to the host's awk.
  # Byte semantics are chosen because they are what this check has always had on
  # the Linux self-hosted runner it runs on; the point is that the evidence is
  # now the same wherever it runs, not which of the two is philosophically right.
  LC_ALL=C awk -v MAX_PER_FILE="$MAX_PER_FILE" -v UPSTREAM_LINES="$UPSTREAM_LINES" \
    -f "$EVIDENCE_AWK" "$patch_path" "$patch_path" > "$ev"

  claims=0
  adverse=0
  decisive=0
  detail=""

  # A patch that yields no claims cannot be verified, and "nothing to check"
  # must never read as "checked and fine". That is the shape of the very defect
  # this ticket is about, arriving through the checker instead of the build.
  #
  # ⚠️ AN EMPTY EVIDENCE FILE IS ONLY *ONE* OF THE TWO WAYS A PATCH ARRIVES HERE
  # WITH NOTHING CHECKABLE, AND IT IS THE RARER ONE. The extractor emits an
  # explicit `noevidence` record for a file section it could not draw a usable
  # candidate from, so a patch whose every section is `noevidence` produces a
  # NON-EMPTY file that yields ZERO claims — `! -s` never fires, the consumer
  # loop `continue`s past each record without incrementing `claims`, and the
  # patch reaches the verdict with `adverse=0`. It is then printed as
  # `PRESENT (0/0 claims hold)`: a pass, in `present` mode, for a patch that may
  # be wholly absent from the tree.
  #
  # Measured, not hypothetical: a patch whose only section is a short BUILD.gn
  # source-list edit — the exact shape the `noevidence` comment below names —
  # was reported PRESENT against a tree that did not carry it, and was the only
  # "passing" patch in a run where all fifteen others correctly failed.
  #
  # Our current set does not hit it (011-gpu-info and 002-user-agent-fingerprint
  # each have one `noevidence` section but carry 22 and 21 real claims from
  # their other files), but that is a property of THIS patch set at THIS tag,
  # and the premise of this whole ticket is that a rebase changes the patch
  # layer. So the guard is on the CLAIM COUNT, which is the thing the verdict
  # actually rests on, rather than on the file being empty, which is a proxy for
  # it that the extractor's own diagnostics falsify.
  if [ ! -s "$ev" ] || [ "$(count_claims "$ev")" -eq 0 ]; then
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

    # `adverse` counts claims pointing the wrong way for this mode: in `present`
    # a claim that did NOT hold (the patch is missing), in `absent` a claim that
    # DID (the patch's effect is in the control). It is not yet a verdict — what
    # a count of adverse claims means differs by mode, and that is decided below.
    if [ "$ok" != "yes" ]; then
      adverse=$((adverse + 1))
      # ⛔ PS-408: A HOLDING `newfile` CLAIM IS DECISIVE IN ABSENCE MODE, and it
      # is NOT subject to the majority below. The extractor's own header says
      # why: "the file existing is unambiguous evidence, and it is the strongest
      # kind we have — no coincidence can conjure a file upstream does not
      # ship." The majority rule exists to stop an ACCIDENTAL TEXT MATCH from
      # condemning a clean tree; a file that exists only because our patch
      # creates it cannot be an accidental match, so weighing it like a text
      # claim answers a question it was never asked.
      #
      # MEASURED, and this is a real regression the first cut of this fix
      # shipped: `011-gpu-info.patch` yields 22 claims, 4 of them `newfile`
      # (gpu_fingerprint.{cc,h}, gpu_info.{cc,h}). A control carrying ALL FOUR
      # of those files and nothing else lands at 4/22 — comfortably under the
      # majority — and was certified `ABSENT ... NOTED COINCIDENCE, exit 0`,
      # where the pre-threshold code correctly FAILED it. That trades the false
      # POSITIVE this ticket fixes for a false NEGATIVE, which is strictly worse
      # on a control: a contaminated control cannot attribute anything, and
      # nothing anywhere would have said the check had stopped covering it.
      if [ "$MODE" = "absent" ] && [ "$kind" = "newfile" ]; then
        decisive=$((decisive + 1))
      fi
      detail="${detail}"$'\n'"    ${kind}  ${rel}"
      if [ -n "$text" ]; then
        detail="${detail}"$'\n'"        looked for: ${text:0:100}"
      fi
      detail="${detail}"$'\n'"        result: ${found}"
    fi
  done < "$ev"

  # ── the verdict ───────────────────────────────────────────────────────────
  # The two modes weigh the same count differently, for the reasons set out in
  # the header. `present` is all-or-nothing: one missing claim means the layer
  # cannot be shown to be in the tree, and refusing to compile is the safe
  # direction. `absent` requires a MAJORITY of claims to agree before it calls a
  # patch present in the control, so one accidental match with upstream cannot
  # condemn a clean tree — while a real contamination, which holds nearly all of
  # its claims, is still caught. At claims=1 the majority test is that single
  # claim, so 009-webdriver and 010-headless keep full sensitivity.
  if [ "$MODE" = "present" ]; then
    if [ "$adverse" -eq 0 ]; then patch_failed=no; else patch_failed=yes; fi
  else
    # ⭐ THE THRESHOLD, AND WHY IT IS A MAJORITY RATHER THAN "ALL" (PS-408).
    #
    # `all` is the strict reading of "a patch is present when its claims hold",
    # and it was considered and REJECTED — on a control it is the WEAKEST rule
    # available, not the strongest. A tree carrying a patch that was later
    # partially reverted, or carrying 15 of a patch's 16 claims for any reason,
    # would be certified clean under `all`. On this gate the artifact being
    # protected is the CONTROL, whose entire purpose is to license "the
    # unmodified tree had this error too" — so a false NEGATIVE here silently
    # invalidates every attribution made against it, while a false POSITIVE
    # merely fails a build loudly. The rule has to be sensitive, not lenient.
    #
    # A majority is the weakest rule that still cannot be tripped by ONE
    # accidental coincidence, which is the failure actually observed (run
    # 34513197109: 1 of 16 claims, and the line was upstream's own).
    #
    # ⚠️ A FRACTION, NOT A FIXED `N > 1`. Two of our sixteen patches yield
    # exactly ONE claim (009-webdriver, 010-headless). Any fixed threshold above
    # one makes them undetectable FOREVER — a genuinely contaminated tree
    # carrying them passes silently, and nothing says the check stopped covering
    # them. At claims=1 the majority test IS that single claim, so sensitivity is
    # preserved exactly where no corroboration can be asked for.
    #
    # ⛔ AND `decisive` IS NOT SUBJECT TO IT. See the `newfile` note above: a
    # file that exists only because our patch creates it is not a coincidence
    # any threshold should be able to outvote.
    if [ "$decisive" -gt 0 ] || [ "$((adverse * 2))" -gt "$claims" ]; then
      patch_failed=yes
    else
      patch_failed=no
    fi
  fi

  if [ "$patch_failed" = "no" ]; then
    PATCHES_OK=$((PATCHES_OK + 1))
    if [ "$MODE" = "present" ]; then
      printf '%-45s PRESENT   (%d/%d claims hold)\n' "$patch_name" "$claims" "$claims" >> "$REPORT"
    elif [ "$adverse" -eq 0 ]; then
      printf '%-45s ABSENT    (%d/%d claims hold — correctly not in this tree)\n' "$patch_name" "$claims" "$claims" >> "$REPORT"
    else
      # Below the corroboration threshold: the tree passes, and the coincidence
      # is NAMED anyway. A claim that has stopped discriminating should be
      # visible on the green run before it, not discovered when it finally
      # crosses the threshold and fails a build with no history behind it.
      COINCIDENCES=$((COINCIDENCES + adverse))
      printf '%-45s ABSENT    (%d of %d claims hold — ⚠️ NOTED COINCIDENCE, below the majority needed to call this patch present)%s\n' \
        "$patch_name" "$adverse" "$claims" "$detail" >> "$REPORT"
      echo "::warning::PS-307: ${patch_name}: ${adverse} of ${claims} evidence claims hold against the ${TREE} tree, which is below the majority required to call the control contaminated. Read as an accidental match with upstream — but the claim has stopped discriminating and should be re-chosen."
    fi
  else
    PATCHES_BAD=$((PATCHES_BAD + 1))
    FAILURES=$((FAILURES + adverse))
    if [ "$MODE" = "present" ]; then
      printf '%-45s ❌ NOT IN THE TREE (%d of %d claims failed)%s\n' "$patch_name" "$adverse" "$claims" "$detail" >> "$REPORT"
      echo "::error::PS-307: ${patch_name} is NOT in the tree — ${adverse} of ${claims} evidence claims failed."
    else
      # ⭐ THE VERDICT NAMES WHICH RULE DECIDED IT (PS-408). "PRESENT" on its own
      # sent a reader hunting for contamination that was not there; the ratio
      # alone is what let the 1-of-16 misreading be caught by hand. Two rules can
      # fail a patch here and they mean different things — a corroborated
      # majority of text claims, or a single file that only our patch creates —
      # so the line says which, and always carries the ratio.
      if [ "$decisive" -gt 0 ]; then
        why="${decisive} of them a file only our patch CREATES, which no coincidence can conjure"
      else
        why="a majority, so this is corroborated and not a coincidence"
      fi
      printf '%-45s ❌ PRESENT IN THE CONTROL (%d of %d claims hold — %s)%s\n' "$patch_name" "$adverse" "$claims" "$why" "$detail" >> "$REPORT"
      echo "::error::PS-307: ${patch_name} appears to be PRESENT in the ${TREE} tree, which must carry none of our patches — ${adverse} of ${claims} evidence claims hold (${why})."
    fi
  fi
done

{
  echo
  echo "patches checked:  ${#PATCHES[@]}"
  echo "evidence claims:  ${CHECKED}"
  echo "patches passing:  ${PATCHES_OK}"
  echo "patches failing:  ${PATCHES_BAD}"
  if [ "$MODE" = "absent" ]; then
    # Stated on EVERY absent run, zero included, so "nothing coincided" is
    # something the report SAYS rather than something a reader infers from a
    # line that is not there.
    echo "noted coincidences: ${COINCIDENCES}   (claims holding against the control but below the majority needed to call a patch present)"
  fi
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
  if [ "$COINCIDENCES" -ne 0 ]; then
    echo "PS-307: ${COINCIDENCES} claim(s) DID hold against the control and were read as accidental matches with upstream, being below the majority a patch needs to be called present. The tree is not contaminated — but each such claim has stopped discriminating and should be re-chosen before it drifts into failing a build. See ${REPORT}."
  fi
fi
