#!/bin/bash
# PS-249 — the ONE definition of a non-identifying host label.
#
# WHY THIS FILE EXISTS (and why it is not two copies)
# ───────────────────────────────────────────────────
# `amnesiadevelopment/persona` is PUBLIC, so Actions logs and workflow artifacts
# are world-readable. The build record used to publish the runner's hostname and
# its exact CPU model — an identifiable personal workstation.
#
# But TWO scripts touch those fields, and the first fix only changed one of them:
#
#   ps218_record_env.sh    WRITES the record   (pseudonymised)
#   ps218_verify_control.sh READS two records and COMPARES them
#
# That one-sidedness produced both audit defects, and they compound:
#
#   DEFECT 1 — a control recorded BEFORE the fix holds a RAW hostname and CPU
#   model. This run emits pseudonyms. The comparator sees two different strings,
#   reports DIFFERS, and REFUSES EVERY BORROW of every existing control — which
#   is every control that exists, including the only successful one.
#
#   DEFECT 2 — the comparator's refusal message prints BOTH sides verbatim into
#   `record/control-borrow-verification.txt` and stdout, both public. So the
#   borrow path republished exactly the values this ticket removes. Defect 1
#   guaranteed the refusal branch fired, so defect 1 was the delivery mechanism
#   for defect 2.
#
# Sharing the definition is what makes the fix two-sided by construction: the
# writer and the reader cannot drift apart, because there is only one function.
#
# THE SALT IS WHAT MAKES IT IRREVERSIBLE, and it is not optional. Both inputs are
# low-entropy — a WSL hostname is a short fixed prefix plus a handful of
# characters, and there are only a few hundred retail CPU models — so an
# UNSALTED digest of either is trivially brute-forced from a public log, which
# would leave the value disclosed in all but appearance. The salt is generated
# locally, kept 0600, and never recorded.
#
# FAIL-CLOSED, DELIBERATELY: if the salt cannot be established the value becomes
# `unknown`, which `is_readable()` in the verify script already refuses. A salt
# failure therefore REFUSES a borrow loudly instead of silently comparing two
# constants. It never falls back to the real value.

PS218_HOST_SALT_FILE="${PS218_HOST_SALT_FILE:-${HOME:-/tmp}/.persona-ps218-host-salt}"

# The marker that makes a record SELF-DESCRIBING. Without it the reader cannot
# tell an already-anonymised field from a raw one, and would re-pseudonymise the
# digest on every pass — a stable value that no longer matches anything.
PS218_ANON_PREFIX="anon-"

_host_salt() {
  if [ ! -s "$PS218_HOST_SALT_FILE" ]; then
    ( umask 077
      mkdir -p "$(dirname "$PS218_HOST_SALT_FILE")" 2>/dev/null || return 1
      head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$PS218_HOST_SALT_FILE"
    ) 2>/dev/null || return 1
  fi
  [ -s "$PS218_HOST_SALT_FILE" ] || return 1
  cat "$PS218_HOST_SALT_FILE" 2>/dev/null
}

# ── THE DIGEST TOOL IS RESOLVED, NOT ASSUMED ────────────────────────────────
#
# ⚠️ `sha256sum` IS NOT PORTABLE. It ships with GNU coreutils, so it is present
# on Linux and ABSENT on macOS, which carries `shasum` instead. Hardcoding it
# made every macOS caller fall through to the `unknown` path, and because
# `is_readable()` correctly refuses `unknown`, that REFUSED EVERY BORROW — the
# exact symptom this file was written to cure, reached by a different route.
#
# The fail-closed design worked as intended (it refused rather than leaked, and
# disclosed nothing), so this was a portability defect and never a security one.
# But a guard that refuses everything is as useless as one that refuses nothing.
#
# All three commands compute the SAME SHA-256 over the same bytes, so a record
# written on one platform still compares equal to one read on another — the
# digest is a property of the input, not of the tool.
#
# THEIR OUTPUT FORMATS DIFFER, which is the trap:
#     sha256sum          -> "<64 hex>  -"
#     shasum -a 256      -> "<64 hex>  -"
#     openssl dgst       -> "(stdin)= <64 hex>"   (or "SHA2-256(stdin)= ...")
# `cut -c1-12` is correct for the first two and silently WRONG for the third —
# it would yield "(stdin)= 3f" and hand that out as a label. Extracting the hex
# run by pattern is format-independent, so a future tool cannot corrupt it.
_ps218_digest() {
  local out=""
  if command -v sha256sum >/dev/null 2>&1; then
    out="$(sha256sum 2>/dev/null)" || true
  elif command -v shasum >/dev/null 2>&1; then
    out="$(shasum -a 256 2>/dev/null)" || true
  elif command -v openssl >/dev/null 2>&1; then
    out="$(openssl dgst -sha256 2>/dev/null)" || true
  else
    return 1
  fi
  # The 64-hex run, wherever it sits in the line. Empty output => no digest.
  printf '%s' "$out" | grep -oE '[0-9a-f]{64}' | head -1 || true
}

# A stable, non-identifying label for one input.
#
# Empty input stays `unknown` rather than becoming a digest of the empty string,
# which would be a value every machine with a missing reading would share — a
# false match rather than a refusal.
pseudonymise() {
  local value="$1" salt digest hex
  [ -n "$value" ] || { echo "unknown"; return 0; }
  salt="$(_host_salt)" || { echo "unknown"; return 0; }
  [ -n "$salt" ] || { echo "unknown"; return 0; }
  hex="$(printf '%s\n' "${salt}:${value}" | _ps218_digest)" || true
  digest="$(printf '%s' "$hex" | cut -c1-12)" || true
  # Still fail-closed: with no usable digest tool the value becomes `unknown`,
  # which is_readable() refuses. It NEVER falls back to the real value.
  [ -n "$digest" ] && echo "${PS218_ANON_PREFIX}${digest}" || echo "unknown"
}

# ── THE COMPATIBILITY SHIM, and the whole of defect 1's fix ──────────────────
#
# Bring ANY host field to the same footing before it is compared or printed:
#
#   already anonymised (`anon-…`)  -> returned unchanged
#   unreadable (empty/unknown/…)   -> passed through so is_readable() refuses it
#   a RAW legacy value             -> pseudonymised HERE, with the SAME salt
#
# The third case is what lets a control recorded before this change still be
# borrowed. The same machine hashes to the same digest on both sides, so the
# comparison still MATCHES; a different machine hashes differently, so it still
# REFUSES. The security semantics are preserved exactly — the comparator's
# behaviour is unchanged, only the representation it sees is.
#
# ⚠️ THIS IS ALSO DEFECT 2's FIX, and that is deliberate rather than incidental.
# Canonicalising at the single point where values ENTER the comparator means
# every downstream emission — the match line, both unreadable branches, the
# DIFFERS branch, the report file and stdout — can only ever print a pseudonym.
# Scrubbing the individual `echo`s would have left the next new message free to
# reintroduce the leak.
canon_host_id() {
  local value="$1"
  case "$value" in
    "${PS218_ANON_PREFIX}"*)            echo "$value" ;;
    ""|unknown|"(not recorded)"|"n/a")  echo "$value" ;;
    *)                                  pseudonymise "$value" ;;
  esac
}
