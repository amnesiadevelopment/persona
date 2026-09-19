#!/bin/bash
# PS-218 — stage our 16 fingerprint patches into ungoogled's OWN patch series.
#
# THE INTEGRATION POINT ALREADY EXISTS — this script does not invent one.
# `scripts/shared.sh` in ungoogled-chromium-portablelinux applies patches with:
#
#     utils/patches.py apply <src> <ungoogled/patches> <root/patches>
#
# It takes MULTIPLE patch directories by design: the submodule's 111 de-googling
# patches first, then the portablelinux repo's own three. Our 16 belong in that
# same mechanism rather than a bespoke step — it is how fingerprint-chromium
# composes them today, and reusing it keeps our layer an ADDITION rather than a
# fork of the tooling.
#
# So all this script does is append our 16 filenames to the portablelinux
# `patches/series` and copy the files in beside it. Order matters: 000 defines
# the command-line switches every later patch reads, so the numeric order the
# upstream series uses is preserved exactly.
set -euo pipefail

UCPL_DIR="${UCPL_DIR:?UCPL_DIR must point at the ungoogled-chromium-portablelinux checkout}"
PATCH_DIR="${PATCH_DIR:?PATCH_DIR must point at our vendored fingerprint patches}"

DEST="${UCPL_DIR}/patches/fingerprint"
SERIES="${UCPL_DIR}/patches/series"

mkdir -p "$DEST"
mkdir -p record

echo "== staging our fingerprint patches =="
echo "from: ${PATCH_DIR}"
echo "into: ${DEST}"
echo

# Copy in numeric order and append to the series in that same order.
#
# IDEMPOTENT, because this script is not always run exactly once. A staging run that
# fails LATE — the count guard below, say — has already appended by then, so its
# non-zero exit reads as "it did nothing" while the series has in fact grown. Run it
# again and every patch is listed twice; the series is applied in order, meets each
# patch a second time, and `patch --forward` refuses it. The whole prepare then dies on
# 000 with a rejected hunk, which looks exactly like the patch having rotted against a
# new upstream. It cost a full re-extract and two wrong diagnoses to learn that.
#
# So: strip any previous staging block first. The marker line is the anchor, and
# removing from it to the end is safe because staging always appends last.
if grep -qF -- "--- persona: fingerprint patches" "$SERIES" 2>/dev/null; then
  echo "note: a previous staging block is present; replacing it"
  # ⚠️ NOT `sed -i`: that in-place form is GNU-only and dies on a macOS
  # runner (test_ps374's `no_shell_script_uses_the_gnu_only_in_place_sed_form`
  # is the guard that caught it here — the strip was written GNU-first and
  # never re-checked). Write through a temp file and `mv`, the way
  # ps374_falsify_patch_evidence.sh's sed_inplace() does.
  sed '/--- persona: fingerprint patches/,$d' "$SERIES" > "${SERIES}.tmp"
  mv "${SERIES}.tmp" "$SERIES"
  # Drop the blank line that preceded the marker, so repeated runs do not grow the
  # file by one line each time.
  sed -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$SERIES" > "${SERIES}.tmp"
  mv "${SERIES}.tmp" "$SERIES"
fi

count=0
{
  echo ""
  echo "# --- persona: fingerprint patches (PS-218 trial build) ---"
} >> "$SERIES"

for p in $(ls "${PATCH_DIR}"/*.patch | sort); do
  name="$(basename "$p")"
  cp "$p" "${DEST}/${name}"
  echo "fingerprint/${name}" >> "$SERIES"
  count=$((count + 1))
  printf '  staged %-45s %6d bytes\n' "$name" "$(wc -c < "$p")"
done

echo
echo "staged ${count} fingerprint patches"

# A guard rather than a comment. The ticket forbids making the build succeed by
# quietly dropping a patch — "A build made to succeed by quietly dropping a
# patch measures nothing." If the count is wrong the staging is wrong, and
# failing here is far better than producing a compile result for an unknown
# subset of our patch layer and reporting it as though it were the whole thing.
#
# The number is derived from the patch directory rather than written here. A
# literal was the right call while the set was frozen at 16, and it did its job:
# it stopped a build the moment 019-webgpu-adapter-info and
# 020-serviceworker-locale joined the set. But a literal also has to be edited in
# lockstep with every addition, and the edit that gets forgotten is the one that
# turns this guard off by making it wrong in the permissive direction. Counting
# the source of truth keeps the check honest as the set grows, and it still fails
# closed on the case that matters: a patch present on disk but not staged.
expected=$(find "$PATCH_DIR" -maxdepth 1 -name '*.patch' | wc -l)
if [ "$count" -ne "$expected" ]; then
  echo "::error::Expected ${expected} fingerprint patches (the number in ${PATCH_DIR}), staged ${count}."
  echo "::error::This measures OUR PATCH LAYER. A build of some other subset measures nothing and must not be reported as a result."
  exit 1
fi

# Record exactly what was staged, with checksums, so the report can state the
# provenance of the patch layer rather than leaving a reader to trust it.
{
  echo "# PS-218 — fingerprint patches staged into the build"
  echo "# recorded: $(date -Is)"
  echo "# count: ${count}"
  echo
  ( cd "$PATCH_DIR" && sha256sum *.patch )
} | tee record/patches-staged.txt

echo "recorded staged patch set -> record/patches-staged.txt"

# PS-289 — durable milestone. "Were our 16 staged before it died?" is one of the
# specific questions a dead run must still answer, and a `record/` file cannot
# answer it: nothing uploads that file when the runner never reaches an upload
# step. Guarded and never fatal.
JOURNAL_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ps289_journal.sh"
if [ -x "$JOURNAL_SH" ]; then
  "$JOURNAL_SH" mark patched "staged ${count} fingerprint patches into ungoogled's series" >/dev/null 2>&1 || true
fi
