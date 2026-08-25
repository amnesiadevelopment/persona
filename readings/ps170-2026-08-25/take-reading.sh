#!/usr/bin/env bash
# PS-170 — the instrument that produced arm-a-postfix-layer-on.json.
#
# Committed BESIDE the record because a reading produced by an uncommitted (or
# a later) version of its own instrument is not reproducible, and that has
# already cost an audit round on this project. Re-run this to regenerate the
# record; see EVIDENCE.md §"Reproducibility" for what is expected to match and
# what is expected to rotate.
#
#   ./take-reading.sh <output.json>
#
# The condition below is NOT free choice: every flag is pinned to the committed
# pre-fix baseline (readings/ps150-2026-08-24/arm-a-baseline-layer-on.json) so
# the two records differ in the FIX and in the exit, and in nothing else.
#
#   --engine chromium            baseline engine (fingerprint-chromium/148.0.7778.215)
#   --declared-machine windows   baseline declared_machine  -> the WINDOWS / DESKTOP arm
#   --seed 9001                  baseline seed
#   (no --no-masking-layer)      layer ON, matching baseline masking_layer.route="extensions"
#   (no --match-product-geo)     baseline installed[] has no "geo"; adding it would move the
#                                surface being read and make the comparison invalid
#
# ⚠️ device_type is NOT selectable here. This tier reads the DESKTOP arm only,
#    which is exactly the arm PS-161 round 4 does not touch. See EVIDENCE.md
#    §"Bound 2" — a clean reading here is NOT evidence the device_type axis is
#    closed.
#
# ⚠️ --allow-unsandboxed-chromium is a DISCLOSED WAIVER, not a default. persona's
#    own launch path passes --no-sandbox nowhere, so this is not the product's
#    default surface. It is required because this host forbids the unprivileged
#    user namespace the sandbox needs. It applied equally to the baseline.
#
# The proxied exit is mandatory and does not fall back: checker_cli proves the
# exit BEFORE reading anything and refuses (exit 2) if it cannot. There is no
# flag that reads a checker over a direct connection.
set -euo pipefail

OUT="${1:?usage: take-reading.sh <output.json>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CREDENTIAL="${PERSONA_TEST_PROXY_FILE:-/workspace/_secrets/test-proxy.txt}"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

cd "$REPO_ROOT"

exec "$PYTHON" -m src.services.verify.checker_cli read \
  --engine chromium \
  --declared-machine windows \
  --seed 9001 \
  --allow-unsandboxed-chromium \
  --credential "$CREDENTIAL" \
  -o "$OUT"
