#!/bin/bash
# PS-244 — VERIFY a BORROWED control before a patched-only build is allowed to
# proceed. This script is the whole safety case for skipping the control build.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS EXISTS TO PROTECT
# ─────────────────────────────────────────────────────────────────────────────
# PS-218's design is one sentence: "A patched build without its control cannot
# be attributed." A compile failure on a patched tree has two possible causes —
# our patches, or an environment that cannot build Chromium at all — and one run
# cannot separate them. That reasoning is NOT weakened here. What changes is
# only WHERE the control comes from: a previously-completed run instead of a
# fresh ~1h25m recompile of an unmodified tree we may already have built.
#
# The exchange is only honest if the borrowed control is VERIFIED, NOT TRUSTED.
# The operator naming a run id is an ASSERTION. This script refuses to act on an
# assertion and instead checks the control run's OWN RECORDED EVIDENCE:
#
#   artifact    — a record was actually retrieved (absent ⇒ expired or wrong run)
#   provenance  — the stamp says unmodified tree, and names the run the
#                 operator named (so the bytes on disk are the run they claimed)
#   tag         — it built the SAME ungoogled tag, per TWO independent witnesses
#   host        — it was built on the SAME machine
#   success     — it ACTUALLY COMPILED, and left a binary behind
#
# Any failure is a LOUD REFUSAL that stops the build. A borrowed control that
# cannot be verified must never degrade the run to an unattributed one — that is
# precisely the error PS-218 exists to correct.
#
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️ THE TRAP THIS SCRIPT IS BUILT AROUND: TWO ABSENCES ARE NOT AN AGREEMENT
# ─────────────────────────────────────────────────────────────────────────────
# The obvious way to write a comparator is `[ "$a" = "$b" ]`. When a field
# cannot be read on EITHER side, that expression compares "" against "" and
# returns TRUE — the comparator reports AGREEMENT between two failed readings
# and the build proceeds on a control nobody actually checked.
#
# This project has already been bitten by exactly that shape (a comparator that
# reported agreement between two identically-failed readings), and it is the
# named failure mode in PS-244. So every comparison here goes through
# `compare_or_fail`, which REFUSES an empty or `unknown` value on either side
# BEFORE testing equality. An unreadable field is a refusal, never a match.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT A PASS EMITS, AND WHY IT IS NOT JUST AN EXIT CODE
# ─────────────────────────────────────────────────────────────────────────────
# On success this writes a BORROW CERTIFICATE into the control directory.
# ps218_attribute.sh refuses any control whose provenance stamp does not name
# the current run; borrowing deliberately breaks that invariant, so the stamp
# check is WIDENED rather than removed — the certificate is what authorises the
# widening, and it is itself checked (it must name THIS run as the verifier, and
# the SAME control run the stamp names). A certificate left on a self-hosted
# runner by an earlier dispatch therefore cannot authorise anything.
set -euo pipefail

# ── PS-249: the SHARED host-label definition ────────────────────────────────
# Sourced rather than copied. This script READS two records and compares them,
# while ps218_record_env.sh WRITES one — when only the writer was changed, a
# control recorded before PS-249 held raw values while this run emitted
# pseudonyms, so the comparator reported DIFFERS and refused every borrow of
# every existing control. One definition means reader and writer cannot drift.
_PS218_HOST_ID_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ps218_host_id.sh"
# shellcheck source=scripts/ps218_host_id.sh
. "$_PS218_HOST_ID_LIB"

CONTROL_DIR="${CONTROL_DIR:-control}"
# The environment record THIS run wrote for its own machine, which is the side
# the borrowed control is compared against. It is produced by the pre-prepare
# pass of ps218_record_env.sh, so this script must run after that step.
CURRENT_ENV="${CURRENT_ENV:-record/environment-patched.txt}"
UNGOOGLED_TAG="${UNGOOGLED_TAG:-unknown}"
CONTROL_RUN_ID="${CONTROL_RUN_ID:-}"
THIS_RUN="${GITHUB_RUN_ID:-local}"

REC="record"
mkdir -p "$REC"
REPORT="${REC}/control-borrow-verification.txt"

CONTROL_ENV="${CONTROL_DIR}/environment-unmodified.txt"
CONTROL_MANIFEST="${CONTROL_DIR}/MANIFEST-unmodified.md"
CONTROL_LOG="${CONTROL_DIR}/compile-unmodified.log"
CONTROL_PROV="${CONTROL_DIR}/compile-unmodified.provenance"
CERT="${CONTROL_DIR}/BORROWED-CONTROL.verified"

FAILURES=0
FAILED_CHECKS=""

fail() {
  # $1 = check name, $2 = explanation
  FAILURES=$((FAILURES + 1))
  case " ${FAILED_CHECKS} " in
    *" $1 "*) ;;
    *) FAILED_CHECKS="${FAILED_CHECKS}$1 " ;;
  esac
  echo "REFUSED [$1] — $2"
}

pass() {
  echo "ok       [$1] — $2"
}

# A value that could not be read. `unknown` is included because
# ps218_record_env.sh writes that literal when `git describe` fails, and a
# literal "unknown" on both sides is the same false agreement as two blanks.
is_readable() {
  case "$1" in
    ""|unknown|"(not recorded)"|"n/a") return 1 ;;
    *) return 0 ;;
  esac
}

# THE COMPARATOR. Refuses an unreadable value on EITHER side before testing
# equality — see the trap note at the top of this file.
compare_or_fail() {
  local check="$1" what="$2" got="$3" want="$4" got_src="$5" want_src="$6"

  if ! is_readable "$got"; then
    fail "$check" "cannot read ${what} from the BORROWED control (${got_src}); value was '${got}'. Two unreadable values must never compare EQUAL, so this is a refusal and not a match."
    return
  fi
  if ! is_readable "$want"; then
    fail "$check" "cannot read ${what} from THIS run (${want_src}); value was '${want}'. Refusing rather than reporting agreement between two failed readings."
    return
  fi
  if [ "$got" != "$want" ]; then
    fail "$check" "${what} DIFFERS — borrowed control has '${got}' (${got_src}), this run has '${want}' (${want_src})."
    return
  fi
  pass "$check" "${what} matches: '${got}'"
}

# ── field extraction from a ps218_record_env.sh environment record ───────────
# `head -1` throughout: the post-prepare record repeats `nproc:` inside the
# container probe block, and the first occurrence is always the runner-level
# reading these comparisons are about.
#
# ⚠️ EVERY HELPER MUST RETURN CLEANLY ON A MISSING FILE, AND THAT IS NOT STYLE.
# This script runs under `set -euo pipefail`, and the ABSENT-ARTIFACT case is
# the one where these files are all missing. A bare `sed ... | head -1` on a
# nonexistent path fails the pipeline, and a failing command substitution in an
# assignment kills the script outright — so the expired-artifact refusal exited
# 2 with EMPTY STDOUT and never printed the message it exists to print. The
# loudest guard here died the most quietly. Hence the `-f` test and `|| true`.
env_field() {
  # $1 = file, $2 = sed expression
  [ -f "$1" ] || return 0
  sed -n "$2" "$1" 2>/dev/null | head -1 || true
}
env_host_field() {
  # $1 = file, $2 = awk field index of `uname -a` output
  [ -f "$1" ] || return 0
  sed -n 's/^# host: //p' "$1" 2>/dev/null | head -1 | awk -v i="$2" '{print $i}' || true
}
# ── PS-249: host identity is CANONICALISED on the way in ────────────────────
# `canon_host_id` (scripts/ps218_host_id.sh, shared with ps218_record_env.sh)
# brings both sides to the same footing before anything compares or PRINTS them:
# an already-anonymised `anon-…` passes through, an unreadable value passes
# through so `is_readable` can refuse it, and a RAW legacy value is
# pseudonymised here with the same salt.
#
# That single choke point fixes two defects at once, which is why it lives here
# rather than at each call site:
#
#   1. A control recorded BEFORE PS-249 holds a raw hostname and CPU model.
#      Without this, the comparator saw a raw string on one side and a pseudonym
#      on the other, reported DIFFERS, and refused EVERY borrow of every control
#      that already exists. Hashing the legacy value with the same salt means
#      the same machine still MATCHES and a different machine still REFUSES —
#      the security semantics are unchanged, only the representation is.
#
#   2. `compare_or_fail` prints both values in its match line AND in all three
#      refusal branches, into `$REPORT` and stdout — both world-readable. So the
#      borrow path used to republish exactly the values this ticket removes, and
#      defect 1 guaranteed the printing branch fired. Canonicalising at entry
#      means every downstream emission can only ever carry a pseudonym; scrubbing
#      the individual `echo`s would have left the next new message free to leak.
env_nodename() { canon_host_id "$(env_host_field "$1" 2)"; }
env_kernel()   { env_host_field "$1" 3; }
env_nproc()    { env_field "$1" 's/^nproc:[[:space:]]*//p'; }
env_nproc_all(){ env_field "$1" 's/^nproc --all:[[:space:]]*//p'; }
env_tag()      { env_field "$1" 's/^portablelinux tag:[[:space:]]*//p'; }
env_cpu() {
  [ -f "$1" ] || return 0
  canon_host_id "$(grep -iE '^[[:space:]]*model name[[:space:]]*:' "$1" 2>/dev/null | head -1 \
    | sed -E 's/.*[Mm]odel name[[:space:]]*:[[:space:]]*//')"
}

# A markdown table cell from the manifest: `| **2. Tree COMPILED** | YES |`
manifest_cell() {
  [ -f "$1" ] || return 0
  grep -F "$2" "$1" 2>/dev/null | head -1 | awk -F'|' '{print $3}' \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' || true
}

{
  echo "# PS-244 — borrowed-control verification"
  echo "# generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "this run:            run=${THIS_RUN} tag=${UNGOOGLED_TAG}"
  echo "control named by op: run=${CONTROL_RUN_ID}"
  echo "control directory:   ${CONTROL_DIR}"
  echo

  # ── CHECK 1: the artifact was actually retrieved ───────────────────────────
  # Its own case, and its own message. The record artifact is uploaded with
  # `retention-days: 30`, so a control older than that is GONE — which is a
  # completely different situation from "the run you named built another tag",
  # and an operator told the wrong one will debug the wrong thing.
  if ! is_readable "$CONTROL_RUN_ID"; then
    fail "artifact" "no control run id was supplied. A patched-only build must NAME the completed run whose control it is borrowing."
  elif [ ! -f "$CONTROL_MANIFEST" ] && [ ! -f "$CONTROL_PROV" ] && [ ! -f "$CONTROL_ENV" ]; then
    fail "artifact" "no unmodified record was retrieved from run ${CONTROL_RUN_ID}. Either that run never produced one, or its artifact has EXPIRED — records are kept for 30 days, so a control older than that cannot be borrowed and the dispatch must fall back to trees=both."
  else
    pass "artifact" "an unmodified record was retrieved from run ${CONTROL_RUN_ID}"
  fi

  # ── CHECK 2: provenance — are these bytes the run the operator named? ──────
  if [ -f "$CONTROL_PROV" ]; then
    ctl_tree="$(sed -n 's/^tree=//p' "$CONTROL_PROV" | head -1)"
    ctl_phase="$(sed -n 's/^phase=//p' "$CONTROL_PROV" | head -1)"
    ctl_run="$(sed -n 's/^github_run_id=//p' "$CONTROL_PROV" | head -1)"
    ctl_prov_tag="$(sed -n 's/^ungoogled_tag=//p' "$CONTROL_PROV" | head -1)"

    compare_or_fail provenance "recorded tree" "$ctl_tree" "unmodified" \
      "${CONTROL_PROV}" "the definition of a control"
    compare_or_fail provenance "recorded phase" "$ctl_phase" "compile" \
      "${CONTROL_PROV}" "the phase a control diff needs"
    # The stamp must name the run the OPERATOR named. This is what makes the
    # retrieved bytes traceable to the claim, rather than to whatever happened
    # to be lying in the control directory.
    compare_or_fail provenance "originating run id" "$ctl_run" "$CONTROL_RUN_ID" \
      "${CONTROL_PROV}" "the run id given to this dispatch"
  else
    ctl_prov_tag=""
    fail "provenance" "the borrowed control carries NO provenance stamp (${CONTROL_PROV} is absent), so it cannot be shown to come from the run it is claimed to come from."
  fi

  # ── CHECK 3: the same ungoogled tag, from TWO independent witnesses ────────
  # The provenance stamp and the environment record are written by different
  # scripts from different sources (a dispatch input vs `git describe` in the
  # checkout). Requiring both to agree with this run AND with each other means a
  # single mis-stamped file cannot pass a control off as the right tree.
  ctl_env_tag="$(env_tag "$CONTROL_ENV")"
  compare_or_fail tag "ungoogled tag (provenance stamp)" "$ctl_prov_tag" "$UNGOOGLED_TAG" \
    "${CONTROL_PROV}" "this dispatch's ungoogled_tag input"
  compare_or_fail tag "ungoogled tag (checkout, git describe)" "$ctl_env_tag" "$UNGOOGLED_TAG" \
    "${CONTROL_ENV}" "this dispatch's ungoogled_tag input"

  # ── CHECK 4: the same host ────────────────────────────────────────────────
  # Wall-clock, memory ceiling and toolchain all belong to the machine, and a
  # control built somewhere else is not a control for this build.
  #
  # The KERNEL RELEASE is deliberately reported but NOT compared: it changes on
  # an ordinary WSL kernel update, which does not make an otherwise-identical
  # machine a different one, and refusing on it would make the borrow useless
  # while teaching the operator to stop reading refusals. Identity is taken from
  # the fields that actually characterise the build host — its name, its CPU and
  # its core count.
  compare_or_fail host "hostname" "$(env_nodename "$CONTROL_ENV")" "$(env_nodename "$CURRENT_ENV")" \
    "${CONTROL_ENV}" "${CURRENT_ENV}"
  compare_or_fail host "CPU model" "$(env_cpu "$CONTROL_ENV")" "$(env_cpu "$CURRENT_ENV")" \
    "${CONTROL_ENV}" "${CURRENT_ENV}"
  compare_or_fail host "nproc" "$(env_nproc "$CONTROL_ENV")" "$(env_nproc "$CURRENT_ENV")" \
    "${CONTROL_ENV}" "${CURRENT_ENV}"
  compare_or_fail host "nproc --all" "$(env_nproc_all "$CONTROL_ENV")" "$(env_nproc_all "$CURRENT_ENV")" \
    "${CONTROL_ENV}" "${CURRENT_ENV}"
  echo "note     [host] kernel release recorded but NOT compared — borrowed='$(env_kernel "$CONTROL_ENV")' this='$(env_kernel "$CURRENT_ENV")'"

  # ── CHECK 5: it actually succeeded ────────────────────────────────────────
  # The record artifact is uploaded with `if: always()`, so a FAILED control
  # produces an artifact too. The artifact merely EXISTING is not evidence of
  # anything; the manifest's two results are.
  if [ -f "$CONTROL_MANIFEST" ]; then
    applied="$(manifest_cell "$CONTROL_MANIFEST" '**1. Patches APPLIED**')"
    compiled="$(manifest_cell "$CONTROL_MANIFEST" '**2. Tree COMPILED**')"
    binary="$(manifest_cell "$CONTROL_MANIFEST" '| chrome binary on disk |')"

    compare_or_fail success "control patches-applied verdict" "$applied" "YES" \
      "${CONTROL_MANIFEST}" "the only verdict a usable control may carry"
    compare_or_fail success "control compiled verdict" "$compiled" "YES" \
      "${CONTROL_MANIFEST}" "the only verdict a usable control may carry"

    # The manifest itself says "trust the binary over the exit code". A control
    # claiming a green compile with no binary on disk is unresolved, and an
    # unresolved control is not a control.
    case "$binary" in
      PRESENT*) pass success "the control left a chrome binary on disk (${binary})" ;;
      "")       fail success "the control's manifest records no binary state at all, so its compile verdict cannot be corroborated." ;;
      *)        fail success "the control's manifest reports the chrome binary as '${binary}'. PS-218's own manifest says to trust the binary over the exit code, so a green verdict without a binary is unresolved — not a usable control." ;;
    esac
  else
    fail success "the borrowed control has no manifest (${CONTROL_MANIFEST}), so there is no record of whether it succeeded. Presence of an artifact is NOT evidence of success — records are uploaded even when the build fails."
  fi

  # The compile log is the material the control diff is actually performed on.
  # A verified control with no log would pass every check above and then produce
  # an attribution with nothing to diff against.
  if [ -f "$CONTROL_LOG" ]; then
    pass success "the control's compile log is present ($(wc -l < "$CONTROL_LOG" | tr -d ' ') lines)"
  else
    fail success "the borrowed control has no compile log (${CONTROL_LOG}); there would be nothing to diff the patched tree against."
  fi

  echo
  if [ "$FAILURES" -eq 0 ]; then
    echo "VERDICT: control from run ${CONTROL_RUN_ID} is VERIFIED and may be borrowed."
  else
    echo "VERDICT: REFUSED — ${FAILURES} check(s) failed: ${FAILED_CHECKS}"
  fi
# ⚠️ REDIRECT, NOT `| tee`. A pipeline runs its left-hand side in a SUBSHELL, so
# every `FAILURES` increment above would be discarded and this script would exit
# 0 — writing a borrow certificate for a control it had just refused. That was
# real: the first run of this script against a different-tag fixture printed
# four REFUSED lines, exited 0, and certified the control anyway.
#
# It is precisely the defect PS-244 names — "a guard that has never been seen to
# fire" — reproduced inside the guard itself, and it is why the refusal path is
# exercised by tests rather than reasoned about. `> "$REPORT"` keeps the block
# in THIS shell (the same form ps218_manifest.sh uses); the report is echoed
# afterwards so the log still shows it.
} > "$REPORT"

cat "$REPORT"

if [ "$FAILURES" -ne 0 ]; then
  # Fail-closed, loudly, on the same path the workflow already uses when a
  # control is missing. Degrading to an unattributed patched build is the one
  # outcome this must never produce.
  echo "::error::BORROWED CONTROL REFUSED — failed checks: ${FAILED_CHECKS}"
  echo "::error::See ${REPORT} for the per-check detail. The patched build is STOPPED rather than run without an attributable control."
  echo "::error::Per PS-218 a patched build without its control cannot be attributed. Re-dispatch with trees=both to build a fresh control."
  rm -f "$CERT"
  exit 1
fi

# ── the borrow certificate ───────────────────────────────────────────────────
# This is what authorises ps218_attribute.sh to accept a control whose stamp
# names a DIFFERENT run. It names the verifying run so that a certificate left
# behind on the self-hosted runner by an earlier dispatch authorises nothing.
{
  echo "verified_by_run=${THIS_RUN}"
  echo "verified_by_attempt=${GITHUB_RUN_ATTEMPT:-1}"
  echo "control_run_id=${CONTROL_RUN_ID}"
  echo "ungoogled_tag=${UNGOOGLED_TAG}"
  echo "verified_at=$(date -Is)"
} > "$CERT"

echo "wrote ${CERT}"
echo "wrote ${REPORT}"
