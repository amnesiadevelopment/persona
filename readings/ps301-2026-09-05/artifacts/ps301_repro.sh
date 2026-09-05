#!/usr/bin/env bash
# PS-301 — the DIRECT reproductions, outside the CDP harness.
#
# Every finding in REPORT.md that this script covers was measured TWICE: once
# through `chromium_tier.ChromiumSession` (the sanctioned CDP instrument) and
# once here, by driving the binary from the command line with --dump-dom. The
# second reading exists because the first one's most surprising result — a
# NEGATIVE measureText width — is exactly the kind of number that deserves an
# instrument that cannot have produced it as an artefact.
#
# It also carries the switch matrix the harness structurally CANNOT show. The
# harness always passes --fingerprint-platform=<its own arm>, so a reading
# taken through it can never demonstrate that the switch selects between arms;
# only a direct run with three different values can.
#
# ⚠️ This script drives the binary DIRECTLY and therefore does NOT go through
# `_engine_binary()`. That resolver's refusal to fall back to a chromium on
# PATH is respected everywhere it applies — the harness runs went through it —
# but a REPRODUCTION must name its binary by absolute path or it is not a
# reproduction. Both paths are explicit below and neither is discovered.
#
# Usage:  ps301_repro.sh <self-built-chrome> <stock-control-chrome>
set -uo pipefail

SELF="${1:?path to the self-built patched chrome}"
STOCK="${2:?path to the stock control chrome}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Headless is used HERE and nowhere else, deliberately. It is the wrong surface
# for a fingerprint reading — which is exactly why the harness refuses it — but
# these are single-vector arithmetic checks (is this width a multiple of that
# ratio?), and the headless/headed distinction cannot change a multiplication.
# Every VERDICT in the report comes from the headed harness run.
COMMON=(--no-sandbox --headless=new --disable-gpu --virtual-time-budget=2500)

run() { # run <binary> <url> <extra-args...>
  local bin="$1" url="$2"; shift 2
  # ⚠️ NO --user-data-dir. This is not tidiness — it is what makes the STOCK
  # control arm answer at all. Pointed at a fresh EMPTY profile dir, stock
  # Chrome-for-Testing runs its first-run path and blocks on GCM registration
  # (measured: PHONE_REGISTRATION_ERROR / DEPRECATED_ENDPOINT, then a hang past
  # a 150s timeout), so every control cell reads as an empty section — a false
  # negative that looks exactly like "stock produced no value". The self-built
  # engine is ungoogled and never had that path, so the flag is harmless there
  # and the asymmetry is invisible until the control is actually run.
  #
  # Dropping it lets each browser use its default profile. That is acceptable
  # HERE and nowhere else: these are single-vector arithmetic reproductions, not
  # the verdict run. Every VERDICT comes from the harness, which DOES use a
  # throwaway user-data-dir and removes it at teardown.
  "$bin" "${COMMON[@]}" "$@" --no-first-run --dump-dom "$url" 2>/dev/null
}

# Joined with a single-line separator rather than "\n": --dump-dom emits the
# <pre> body verbatim, so a multi-line result cannot be extracted with a
# line-oriented matcher and reads back as an EMPTY section — a false negative
# on the one finding this script exists to reproduce.
cat > "$TMP/mt.html" <<'EOF'
<body><pre id=o></pre><script>
var c=document.createElement("canvas"),x=c.getContext("2d");x.font="17px Arial";
var out=[];["A","hello","persona-PS301","The quick brown fox jumps"].forEach(function(s){
  var m=x.measureText(s);out.push(s+" | w="+m.width+" | abbL="+m.actualBoundingBoxLeft);});
document.getElementById("o").textContent=out.join("  ;;  ");
</script></body>
EOF

# The <pre> content, extracted by CONTENT rather than by tag: --dump-dom's
# output is one long line, so a tag-anchored matcher is fragile in exactly the
# way that produces a silently empty section.
mt() { local bin="$1"; shift; run "$bin" "file://$TMP/mt.html" "$@" \
        | grep -o 'A | w=.*;;  The quick brown fox jumps | w=[^<]*' \
        | tr ';' '\n' | sed 's/^ *//; /^$/d' || echo '  <no reading>'; }

cat > "$TMP/plat.html" <<'EOF'
<body><pre id=o></pre><script>document.getElementById('o').textContent=JSON.stringify({
plat:navigator.platform,ua:navigator.userAgent,
uad:navigator.userAgentData&&navigator.userAgentData.platform,
sw:screen.width,sh:screen.height,dpr:devicePixelRatio,
hc:navigator.hardwareConcurrency,tz:Intl.DateTimeFormat().resolvedOptions().timeZone});</script></body>
EOF

cat > "$TMP/rects.html" <<'EOF'
<body style="margin:0"><div style="height:7px"></div><span id=s>measure me</span><pre id=o></pre><script>
var r=document.getElementById('s').getBoundingClientRect();
var e=document.createElement('div');
e.style.cssText='position:absolute;left:13.3px;top:7.7px;width:111.7px;height:33.3px';
e.textContent='x';document.body.appendChild(e);
var r2=e.getBoundingClientRect();
document.getElementById('o').textContent=JSON.stringify(
 {eligible_x:r.x,eligible_y:r.y,eligible_w:r.width,exempt_x:r2.x,exempt_w:r2.width});
</script></body>
EOF

echo "############################################################"
echo "# PS-301 direct reproductions"
echo "# self-built: $SELF"
echo "#   $("$SELF" --version 2>/dev/null)"
echo "# stock:      $STOCK"
echo "#   $("$STOCK" --version 2>/dev/null)"
echo "# date: $(date -u +%FT%TZ)"
echo "############################################################"

echo
echo "=== 1. IT LAUNCHES AND RENDERS ==============================="
run "$SELF" 'data:text/html,<h1>HELLO-PS301</h1>'

echo
echo "=== 2. patch 015 measureText — the DEFECT ===================="
echo "--- self-built, --fingerprint=24601 (string / width / actualBoundingBoxLeft)"
mt "$SELF" --fingerprint=24601
echo "--- self-built, --fingerprint=5150"
mt "$SELF" --fingerprint=5150
echo "--- self-built, NO --fingerprint (the patch stands down)"
mt "$SELF" 
echo "--- STOCK control, --fingerprint=24601 (switch is inert in stock)"
mt "$STOCK" --fingerprint=24601
echo
echo "READ: with a seed, every width is NEGATIVE and the observed/stock ratio is"
echo "      IDENTICAL across all four strings — i.e. the width was MULTIPLIED by"
echo "      the noise factor, not perturbed by it. The constant ratio equals the"
echo "      actualBoundingBoxLeft value printed beside it. A negative width is"
echo "      impossible per spec, so this is trivially detectable."

echo
echo "=== 2b. THE VERDICT — and this one can FAIL ==================="
# Everything above PRINTS. This step DECIDES, and it is the reason the script
# has a meaningful exit code at all.
#
# The original version of this file ended section 2 with a prose paragraph
# beginning "READ:" that told the reader what to conclude, and exited 0 whatever
# the numbers were — including on its own disconfirming case. A check that
# cannot go red is not evidence, so the arithmetic now runs through a guard that
# exits non-zero when the defect is present. See ps301_measuretext_repro.py,
# whose --self-test demonstrates it reaching DEFECT, PLAUSIBLE and INDETERMINATE.
VERDICT_PY="$(dirname "$0")/../../../scripts/ps301_measuretext_repro.py"
[ -f "$VERDICT_PY" ] || VERDICT_PY="$(dirname "$0")/ps301_measuretext_repro.py"
MT_RC=0
if [ -f "$VERDICT_PY" ]; then
  # Re-capture section 2 into a transcript the verdict can parse, then judge it.
  { echo "--- self-built, --fingerprint=24601"; mt "$SELF" --fingerprint=24601
    echo "--- STOCK control, --fingerprint=24601"; mt "$STOCK" --fingerprint=24601
  } > "$TMP/mt-transcript.txt"
  python3 "$VERDICT_PY" --transcript "$TMP/mt-transcript.txt" || MT_RC=$?
  echo "  (measureText verdict exit code: $MT_RC — 1 = DEFECT PRESENT)"
else
  echo "  !! verdict script not found; NOT claiming a pass" >&2
  MT_RC=2
fi

echo
echo "=== 3. patch 002 platform — WORKS across all three arms ======"
for arm in linux windows macos; do
  printf '  self-built --fingerprint-platform=%-8s ' "$arm"
  run "$SELF" "file://$TMP/plat.html" --fingerprint=24601 --fingerprint-platform="$arm" \
    | grep -o '"plat":"[^"]*","ua":"[^"]*"' | head -1
done
printf '  STOCK      --fingerprint-platform=%-8s ' windows
run "$STOCK" "file://$TMP/plat.html" --fingerprint=24601 --fingerprint-platform=windows \
  | grep -o '"plat":"[^"]*","ua":"[^"]*"' | head -1

echo
echo "=== 4. the DEAD switches — declared, forwarded, never read ==="
echo "  (screen-width/height and device-scale-factor are accepted and ignored)"
printf '  self-built --fingerprint-screen-width=2560 --fingerprint-screen-height=1440 -> '
run "$SELF" "file://$TMP/plat.html" --fingerprint=24601 \
  --fingerprint-screen-width=2560 --fingerprint-screen-height=1440 \
  | grep -o '"sw":[0-9]*,"sh":[0-9]*'
printf '  self-built --fingerprint-device-scale-factor=2                             -> '
run "$SELF" "file://$TMP/plat.html" --fingerprint=24601 --fingerprint-device-scale-factor=2 \
  | grep -o '"dpr":[0-9.]*'
printf '  self-built --fingerprint-hardware-concurrency=6  (a LIVE switch, contrast) -> '
run "$SELF" "file://$TMP/plat.html" --fingerprint=24601 --fingerprint-hardware-concurrency=6 \
  | grep -o '"hc":[0-9]*'

echo
echo "=== 5. patch 014 client rects — WORKS, and its exemption holds ="
for s in 24601 5150 none; do
  printf '  self-built seed=%-6s ' "$s"
  if [ "$s" = none ]; then run "$SELF" "file://$TMP/rects.html" | grep -o '{"eligible_x".*}'
  else run "$SELF" "file://$TMP/rects.html" --fingerprint="$s" | grep -o '{"eligible_x".*}'; fi
done
printf '  STOCK      seed=24601  '
run "$STOCK" "file://$TMP/rects.html" --fingerprint=24601 | grep -o '{"eligible_x".*}'
echo
echo "READ: eligible_x/y move per seed; eligible_w does NOT (the patch calls"
echo "      Offset, not Scale); exempt_x does NOT (ShouldSkipClientRectsOffset"
echo "      deliberately exempts position:absolute with fixed top+left)."

echo
echo "############################################################"
echo "# EXIT STATUS"
echo "############################################################"
# The script's own exit code now carries the measureText verdict, so a caller
# (or CI) can act on it instead of parsing prose. Non-zero = the defect is still
# present, which is the CURRENT expected state of the 144 engine: this is a
# measure-not-fix ticket, so a red exit here is the finding being reported, not
# a broken script. It turns green the day patch 015 is repaired.
if [ "$MT_RC" -eq 0 ]; then
  echo "measureText: PLAUSIBLE (exit 0)"
elif [ "$MT_RC" -eq 1 ]; then
  echo "measureText: DEFECT PRESENT (exit 1) — patch 015 Shuffle/offset mismatch"
else
  echo "measureText: INDETERMINATE (exit $MT_RC) — measured nothing; NOT reported as a pass"
fi
exit "$MT_RC"
