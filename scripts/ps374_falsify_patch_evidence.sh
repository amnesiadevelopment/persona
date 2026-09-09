#!/bin/bash
# PS-374 — FALSIFY the static patch-presence guard on patch 001.
#
# THE CLAIM THIS SCRIPT PROVES BY EXECUTION
# ─────────────────────────────────────────
# `scripts/ps307_verify_patches_in_tree.sh` verifies our 16 fingerprint patches
# are in the tree about to be compiled, by looking for lines each patch adds.
# For 001-disable-runtime.enable.patch it looks for the WRONG SIX LINES.
#
# The extractor takes at most `PS307_MAX_EVIDENCE_PER_FILE` (shipped: 3) added
# lines per file per patch, in patch order. Each of 001's hunks opens with a
# multi-line explanatory comment block that precedes its one code line, so the
# cap is spent on COMMENTS before the extractor ever reaches the code:
#
#     v8-runtime-agent-impl.cc   3 claims, all `// persona fingerprint: ...`
#     v8-runtime-agent-impl.h    3 claims, all `// persona fingerprint: ...`
#     claims pinning `return false`, `if (!enabled())`, `if (enabled())`:  0
#
# So a tree in which the three CODE lines have been reverted to `m_enabled` —
# the exact shape a bad rebase or a careless revert leaves, comments intact —
# still satisfies all six claims. The guard reports PRESENT over a patch that
# is, functionally, entirely gone.
#
# ⛔ A GUARD THAT HAS NEVER BEEN SEEN TO FAIL IS DECORATION. This script is the
# seeing. It builds two synthetic trees — one genuinely patched, one sabotaged
# in exactly that way — runs the REAL verifier over both, and asserts the
# before/after difference. It is written to FAIL LOUDLY once the guard is fixed
# in either direction it should not be.
#
# Run from the repo root:
#     scripts/ps374_falsify_patch_evidence.sh
#
# Exit 0 = the demonstration reached its expected verdicts.
# Exit 1 = a verdict was not what the demonstration expects; read the output.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"
PATCH_DIR="${REPO}/engine/patches/fingerprint"
PATCH="${PATCH_DIR}/001-disable-runtime.enable.patch"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

SRC="${WORK}/ucpl/build/src/v8/src/inspector"
mkdir -p "$SRC"

# ── the PATCHED tree ────────────────────────────────────────────────────────
# Both files carry patch 001's post-image: the comments AND the code lines.
# Only the two regions the patch touches are reproduced; the verifier reads
# these files by content, so surrounding Chromium source is not needed.
cat > "${SRC}/v8-runtime-agent-impl.cc" <<'CC'
void V8RuntimeAgentImpl::addBindings(InspectedContext* context) {
  int contextGroupId = context->contextGroupId();
  int contextId = context->contextId();
  const String16 contextName = context->humanReadableName();
  // persona fingerprint: route the guard through enabled(), which is hard-coded
  // to false (see v8-runtime-agent-impl.h), so no binding is ever registered.
  // A ONE-LINE anchor: upstream may reshape the body below without rejecting
  // this, and unlike an unconditional early return it leaves no unreachable
  // code for -Wunreachable-code to trip on.
  if (!enabled()) return;

  protocol::DictionaryValue* globalBindings =
      m_state->getObject(V8RuntimeAgentImplState::globalBindings);
}

void V8RuntimeAgentImpl::messageAdded(V8ConsoleMessage* message) {
  // persona fingerprint: enabled() is hard-coded false, so nothing is reported
  // to the disabled Runtime domain.
  if (enabled()) reportMessage(message, true);
}
CC

cat > "${SRC}/v8-runtime-agent-impl.h" <<'HH'
  void messageAdded(V8ConsoleMessage*);
  // persona fingerprint: the Runtime domain always reports as disabled.
  // This single line is what disables Runtime.enable; the call sites in
  // v8-runtime-agent-impl.cc route their guards through it.
  bool enabled() const { return false; }

 private:
  bool reportMessage(V8ConsoleMessage*, bool generatePreview);
HH

evidence_for() {
  awk -v MAX_PER_FILE="${PS307_MAX_EVIDENCE_PER_FILE:-3}" \
      -f "${HERE}/ps307_patch_evidence.awk" "$PATCH" "$PATCH"
}

# Apply the verifier's own matcher — `grep -F` over a whitespace-stripped copy —
# so this demonstration cannot pass or fail for a reason the real gate would not.
check_tree() {
  local label="$1" ok=0 bad=0
  while IFS=$'\t' read -r rel kind text; do
    [ -n "$rel" ] || continue
    [ "$kind" = "noevidence" ] && continue
    local file="${WORK}/ucpl/build/src/${rel}"
    if sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$file" | grep -qF -- "$text"; then
      ok=$((ok + 1))
    else
      bad=$((bad + 1))
    fi
  done < <(evidence_for)
  echo "${label}: ${ok} of $((ok + bad)) claims hold"
  printf '%s' "$bad"
}

echo "== PS-374: falsifying the static patch-presence evidence for patch 001 =="
echo

echo "-- the six claims the extractor produces at the SHIPPED cap (MAX_PER_FILE=3):"
evidence_for | sed 's/^/     /'
echo
code_claims=$(evidence_for | awk -F'\t' '$2 != "noevidence" && $3 !~ /^\/\// ' | wc -l)
echo "   claims pinning CODE (not a comment): ${code_claims}"
echo

bad_patched=$(check_tree "PATCHED tree" | tail -1)
echo

# ── the SABOTAGED tree ──────────────────────────────────────────────────────
# Revert ONLY the three code lines, back to the raw `m_enabled` field the patch
# replaced. Every comment stays exactly where it was. Functionally the patch is
# gone: `enabled()` is upstream's again and both call sites read the field.
sed -i \
  -e 's|^  if (!enabled()) return;|  if (!m_enabled) return;|' \
  -e 's|^  if (enabled()) reportMessage(message, true);|  if (m_enabled) reportMessage(message, true);|' \
  "${SRC}/v8-runtime-agent-impl.cc"
sed -i \
  -e 's|^  bool enabled() const { return false; }|  bool enabled() const { return m_enabled; }|' \
  "${SRC}/v8-runtime-agent-impl.h"

echo "-- sabotage applied: the three CODE lines reverted to m_enabled, comments untouched"
grep -n "m_enabled\|enabled()" "${SRC}/v8-runtime-agent-impl.cc" "${SRC}/v8-runtime-agent-impl.h" | sed 's/^/     /'
echo

bad_sabotaged=$(check_tree "SABOTAGED tree" | tail -1)
echo

# ── the verdicts this demonstration exists to assert ────────────────────────
rc=0

if [ "$bad_patched" -ne 0 ]; then
  echo "::error::PS-374: the PATCHED tree failed a claim. The demonstration's baseline is broken;"
  echo "::error::PS-374: nothing below it can be attributed to the sabotage."
  rc=1
fi

if [ "$code_claims" -eq 0 ] && [ "$bad_sabotaged" -eq 0 ]; then
  echo "⛔ FALSE GREEN DEMONSTRATED — the guard reports every claim holding over a tree"
  echo "   in which patch 001 has been completely reverted. It is pinning six COMMENT"
  echo "   lines and zero code lines, so the sabotage is invisible to it."
elif [ "$code_claims" -gt 0 ] && [ "$bad_sabotaged" -gt 0 ]; then
  echo "✅ GUARD IS SIGHTED — ${code_claims} claim(s) pin code, and the sabotaged tree fails"
  echo "   ${bad_sabotaged} of them. A reverted patch 001 would now be caught."
else
  echo "::error::PS-374: inconsistent state — code_claims=${code_claims}, sabotaged failures=${bad_sabotaged}."
  echo "::error::PS-374: a guard pinning code lines must fail on the sabotage, and one pinning"
  echo "::error::PS-374: none must pass it. Neither holds here; read the claim list above."
  rc=1
fi

exit "$rc"
