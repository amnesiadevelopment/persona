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
# patch measures nothing." If the count is not 16 the staging is wrong, and
# failing here is far better than producing a compile result for an unknown
# subset of our patch layer and reporting it as though it were all 16.
if [ "$count" -ne 16 ]; then
  echo "::error::Expected exactly 16 fingerprint patches, staged ${count}."
  echo "::error::PS-218 measures OUR 16 PATCHES. A build of some other number measures nothing and must not be reported as this ticket's result."
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
