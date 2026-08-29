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

# A stable, non-identifying label for one input.
#
# Empty input stays `unknown` rather than becoming a digest of the empty string,
# which would be a value every machine with a missing reading would share — a
# false match rather than a refusal.
pseudonymise() {
  local value="$1" salt digest
  [ -n "$value" ] || { echo "unknown"; return 0; }
  salt="$(_host_salt)" || { echo "unknown"; return 0; }
  [ -n "$salt" ] || { echo "unknown"; return 0; }
  digest="$(printf '%s\n' "${salt}:${value}" | sha256sum 2>/dev/null | cut -c1-12)" || true
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
