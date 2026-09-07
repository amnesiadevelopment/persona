#!/usr/bin/env bash
# PS-365 rework — device-scale boundary sweep for INVARIANT 1.
#
# WHY
#   The committed control (ps365_domain_probe.html) measured "0 / 6008 rect
#   values off the 1/64 lattice" on stock Chromium and the report generalised
#   it to a property of stock Blink. That control ran at dpr 1 only.
#   persona itself passes --force-device-scale-factor from the host's Windows
#   DPI / macOS backing scale (process.py ~1253 -> launch_policy
#   _host_display_scale), so a scaled display is an ordinary launch.
#
#   This is the control that establishes where the committed reading stops
#   holding, and it tests THREE formulations rather than asserting one:
#
#     css_1_64   v*64 integral                        the committed claim
#     dev_1_64   v*dpr*64 integral (epsilon)          device space, naive
#     dev_exact  exists integer n: f32(n/64/dpr)==v   device space, EXACT
#
#   dev_exact is the load-bearing one. Blink divides the device-space
#   LayoutUnit by the scale factor and stores the CSS result through a FLOAT,
#   so multiplying back cannot recover an integer except to within a float32
#   ulp — an epsilon test measures its own tolerance. dev_exact replays the
#   forward pipeline and compares for exact equality instead.
#
# READING
#   A formulation is a usable detector invariant only if it reads 0 at EVERY
#   scale. One non-zero cell disqualifies it: a detector shipping it flags
#   stock Chrome.
#
# Usage:  ./ps365_dpr_sweep.sh [chromium-binary]
set -u
BIN="${1:-chromium}"
DIR="$(cd "$(dirname "$0")" && pwd)"
PROBE="file://$DIR/ps365_dpr_lattice_probe.html"

echo "binary: $($BIN --version 2>&1 | head -1)"
echo "probe:  ps365_dpr_lattice_probe.html  (500 simple divs x 8 rect fields)"
echo

printf '%-8s %-10s %-6s %8s %10s %10s %11s %9s\n' \
  scale dpr pow2 probed css_1_64 dev_1_64 dev_exact non_f32
printf '%-8s %-10s %-6s %8s %10s %10s %11s %9s\n' \
  -------- ---------- ------ ------ -------- -------- --------- -------

for S in 0.5 0.75 1 1.1 1.25 1.3333 1.5 1.75 2 2.5 3 4 5 6 8; do
  RAW="$("$BIN" --headless --disable-gpu --no-sandbox --enable-logging=stderr \
         --force-device-scale-factor="$S" --virtual-time-budget=6000 "$PROBE" 2>&1 \
         | grep -o 'PS365DPR .*' | head -1)"
  echo "$RAW" | S="$S" python3 -c '
import sys, os, json, re, struct

raw = sys.stdin.read()
m = re.search(r"PS365DPR (\{.*\})", raw)
if not m:
    print("%-8s PARSE FAILED (no probe output)" % os.environ["S"]); raise SystemExit
txt = m.group(1)
while txt:                       # chromium appends a console-source suffix
    try:
        d = json.loads(txt); break
    except json.JSONDecodeError:
        txt = txt[:-1]
else:
    print("%-8s PARSE FAILED (unparseable)" % os.environ["S"]); raise SystemExit

dpr  = d["dpr"]
vals = d["raw_values"]

def f32(x):
    try: return struct.unpack("f", struct.pack("f", x))[0]
    except OverflowError: return float("inf")

def eps(p): return max(1e-9, abs(p) * 1e-12)
def on_css(v): p = v*64.0;     return abs(p-round(p)) < eps(p)
def on_dev(v): p = v*dpr*64.0; return abs(p-round(p)) < eps(p)
def on_exact(v):
    n = round(v*dpr*64.0)
    return any(f32(c/64.0/dpr) == v for c in (n-1, n, n+1))

def pow2(x):
    if x <= 0: return False
    return (struct.unpack(">Q", struct.pack(">d", x))[0] & ((1<<52)-1)) == 0

print("%-8s %-10.6g %-6s %8d %10d %10d %11d %9d" % (
    os.environ["S"], dpr, pow2(dpr), len(vals),
    sum(1 for v in vals if not on_css(v)),
    sum(1 for v in vals if not on_dev(v)),
    sum(1 for v in vals if not on_exact(v)),
    sum(1 for v in vals if f32(v) != v)))
'
done

echo
echo "READING: a column that is 0 on EVERY row is a scale-independent invariant."
echo "         a column with any non-zero cell is CONDITIONAL on device scale."
echo "         Compare the clean rows against the pow2 column before concluding"
echo "         anything about integral vs fractional scale — they are not the"
echo "         same set, and the difference is the finding."
