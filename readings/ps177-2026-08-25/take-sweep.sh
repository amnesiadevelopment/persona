#!/usr/bin/env bash
# PS-177 — the instrument that produced this directory's records.
#
# Committed BESIDE the records because a reading produced by an uncommitted (or
# a later) version of its own instrument is not reproducible. Re-run this to
# regenerate the sweep; see EVIDENCE.md §"Reproducibility" for what is expected
# to match and what is expected to rotate.
#
#   ./take-sweep.sh <output-directory>
#
# WHAT THIS IS. Every full checker-matrix reading this project owned before it
# sat in ONE cell: chromium / declared_machine=windows / desktop / seed 9001
# (ps143, ps150, ps161-live, ps170). They differ in the fix under test and the
# exit, not in the profile variant. This is the BASELINE SWEEP across the axes
# `checker_cli read` has accepted the whole time and nobody ran.
#
#   --engine both                          the two engines persona ships
#   --declared-machine windows,macos,linux the three DECLARED_MACHINES exist
#   --seed 5150,24601                      TWO seeds — the load-bearing axis
#
# THE SEED AXIS IS THE POINT, not one more dimension. Level 2 of the bar
# (mutual unlinkability) asks whether TWO profiles can be tied to each other,
# which NO single-profile reading can answer at any level of detail, ever.
# Two seeds through the same checker, diffed against each other, is what
# converts that bar level from "assumed" to "known". If this ever has to be cut
# down, CUT THE DECLARED-MACHINE AXIS AND KEEP BOTH SEEDS: a one-machine
# two-seed reading answers a bar level; a three-machine one-seed reading
# answers none.
#
# WHY 5150 AND 24601. Neither appears anywhere under readings/ — the existing
# corpus is 9001 (31 rows), 4242 and 1337 (19 each). Picking two unused seeds
# makes this sweep independent of the corpus rather than half-anchored to it,
# so a similarity found here cannot be an artefact of a seed some earlier run
# already exercised.
#
# THE PLAN IS 8 RECORDS, NOT 12. Firefox COLLAPSES the machine axis to one run
# (product issue #211): `InvisiblePlaywright` has no OS/platform parameter, the
# engine presents Windows regardless, and the record states
# declared_machine_honoured: false rather than echoing the request. So:
#
#   chromium x {windows, macos, linux} x {5150, 24601}  = 6 records
#   firefox  x {windows}               x {5150, 24601}  = 2 records
#
# The collapse is PRINTED by the tool and is expected output, not a failure.
# Do NOT "square the grid" by echoing the requested machine into a firefox
# record: a later comparison would read the fabricated difference as a product
# coupling.
#
# ⚠️ device_type is NOT selectable from this tier. This sweep reads the DESKTOP
#    arm only. `windows`+`mobile` — the arm PS-161 round 4 actually repaired —
#    cannot be read here at all. A clean reading in this directory is NOT
#    evidence about any mobile arm. See EVIDENCE.md §"Not covered".
#
# ⚠️ --allow-unsandboxed-chromium is a DISCLOSED WAIVER, not a default.
#    persona's own launch path passes --no-sandbox NOWHERE, so a reading taken
#    with it is not the product's default surface. It is required because this
#    host forbids the unprivileged user namespace the sandbox needs. It applied
#    equally to the ps170 baseline this sweep sits beside.
#
#    --allow-small-dev-shm is deliberately NOT passed: /dev/shm on this host is
#    1.0 GiB, above the tier's 256 MiB floor, so no waiver is needed and none
#    is taken.
#
# The proxied exit is mandatory and does not fall back: checker_cli proves the
# exit BEFORE reading anything and refuses (exit 2) if it cannot. There is no
# flag that reads a checker over a direct connection.
#
# EXIT CODES (checker_cli's own convention — do not collapse them):
#   0  every configuration was read and gathered enough to be read as a reading
#   3  INCONCLUSIVE — at least one record was written that did not gather
#      enough evidence to mean anything. The record exists and says so. This is
#      a statement about the RUN, never a verdict about persona.
#   2  REFUSED — the exit could not be proven for at least one configuration.
#   1  the run itself broke.
set -euo pipefail

OUT="${1:?usage: take-sweep.sh <output-directory>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CREDENTIAL="${PERSONA_TEST_PROXY_FILE:-/workspace/_secrets/test-proxy.txt}"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

cd "$REPO_ROOT"

# A multi-configuration run writes one record per configuration and treats -o
# as a DIRECTORY. (Single-config runs treat it as a FILE path — passing a
# directory there fails at the write step AFTER spending the whole browser run.)
exec "$PYTHON" -m src.services.verify.checker_cli read \
  --engine both \
  --declared-machine windows,macos,linux \
  --seed 5150,24601 \
  --allow-unsandboxed-chromium \
  --credential "$CREDENTIAL" \
  -o "$OUT"
