"""BEFORE reading: what a macOS page ACTUALLY reads, on the real selection path.

Readings come from EXECUTING the emitted gpu.js under node (the _GPU_READ
harness pattern already in tests/test_hardware_generation.py). The arithmetic
is the INSTRUMENT'S OWN pure collision_probability() — not a second instrument.
"""
import json, pathlib, subprocess, shutil, sys, tempfile

# Resolve the TREE UNDER MEASUREMENT from this file's own location
# (readings/<slug>/ -> repo root), never from a fixed absolute path.
#
# This is not a portability nicety, it is the difference between a reading and a
# fabrication. A hard-coded "/workspace/persona" does not FAIL in a fresh
# checkout — /workspace/persona exists in every container this project runs, so
# the script imports THAT tree's gpu_ext, succeeds, and prints a confident
# figure for a pool it never measured. Placed in a 2-entry worktree it reported
# 21.9% over eleven cards that do not exist there; corrected, the same command
# in the same tree reports the true 53.1% over two. A crash is merely useless;
# this agreed with whatever it was asked no matter which tree it stood in.
#
# Same expression as continuity.py:50 (NEW) — one spelling, deliberately.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from src.services.browser.gpu_ext import build_gpu_extension
from src.services.browser.engine_platform import engine_platform_for
from src.services.verify.engine_gpu_variance import collision_probability

NODE = shutil.which("node")
# Resolve the harness RELATIVE TO THIS FILE, never from the authoring session's
# /tmp. harness.js is committed beside this script; pointing at a scratch copy
# made the reading unreproducible in a fresh checkout, which silently converts a
# re-derivable figure into an archived assertion you can only believe.
HARNESS = str(pathlib.Path(__file__).resolve().parent / "harness.js")

def seen(seed, generation, tmp):
    d = pathlib.Path(build_gpu_extension(
        seed, "macos", str(tmp / f"g{generation}s{seed}"), generation,
        engine_platform=engine_platform_for("macos", "desktop")))
    h = d / "harness.js"
    h.write_text(pathlib.Path(HARNESS).read_text(), encoding="utf-8")
    out = subprocess.run([NODE, str(h), str(d / "gpu.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    v = json.loads(out.stdout)
    assert "HOST_VALUE_NOT_SPOOFED" not in v.values(), \
        "extension did not patch getParameter — this measured NOTHING"
    return v["unmaskedRenderer"]

def run(seeds, generation):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        vals = [seen(s, generation, tmp) for s in seeds]
    p = collision_probability(vals)
    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    return p, counts, len(vals)

if __name__ == "__main__":
    gen = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 64
    seeds = list(range(1, n + 1))
    p, counts, total = run(seeds, gen)
    print(f"gen={gen}  seeds={total}  collision(Simpson)={p:.4f} = {p:.1%}")
    for k, c in sorted(counts.items(), key=lambda x: -x[1]):
        short = k.split("Renderer: ")[-1].rstrip(")").split(",")[0]
        print(f"   {c:4d}  {c/total:6.1%}  {short}")
