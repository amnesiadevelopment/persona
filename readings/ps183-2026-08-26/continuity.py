"""Per-seed continuity: does ANY existing (gen-0) profile change its card?

Aggregate parity is NOT proof — two profiles could swap cards and leave the
collision figure identical. This compares the ACTUAL string, seed by seed,
between the pristine HEAD worktree and the edited tree.
"""
import json, os, pathlib, subprocess, shutil, sys, tempfile

NODE = shutil.which("node")
# Resolve the harness RELATIVE TO THIS FILE, never from the authoring session's
# /tmp. harness.js is committed beside this script; pointing at a scratch copy
# made the reading unreproducible in a fresh checkout, which silently converts a
# re-derivable figure into an archived assertion you can only believe.
HARNESS = str(pathlib.Path(__file__).resolve().parent / "harness.js")

def seen(tree, seed, generation, tmp):
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from src.services.browser.gpu_ext import build_gpu_extension\n"
        "from src.services.browser.engine_platform import engine_platform_for\n"
        "print(build_gpu_extension(%d, 'macos', %r, %d, engine_platform=engine_platform_for('macos','desktop')))\n"
        % (tree, seed, str(tmp / f"{pathlib.Path(tree).name}_g{generation}s{seed}"), generation)
    )
    d = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=tree, timeout=120)
    assert d.returncode == 0, d.stderr
    d = pathlib.Path(d.stdout.strip())
    h = d / "harness.js"
    h.write_text(pathlib.Path(HARNESS).read_text(), encoding="utf-8")
    out = subprocess.run([NODE, str(h), str(d / "gpu.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    v = json.loads(out.stdout)
    assert "HOST_VALUE_NOT_SPOOFED" not in v.values(), "measured NOTHING"
    return v["unmaskedRenderer"]

# BASE is a PRISTINE worktree of the merge-base; NEW is this checkout. Neither
# may be a fixed authoring-session path — /tmp/ps183/base does not exist in a
# fresh clone, so the script died before measuring anything.
#
# NEW is derived from this file's own location (readings/<slug>/ -> repo root).
# BASE must be supplied, because only the caller knows which commit is the
# merge-base; create one with, from the repo root:
#
#     git worktree add --detach /tmp/ps183-base <merge-base-sha>
#     python3 readings/ps183-2026-08-26/continuity.py /tmp/ps183-base
#
# It is an ARGUMENT rather than a hard-coded sha so this stays runnable after
# the branch merges and the merge-base moves.
NEW = str(pathlib.Path(__file__).resolve().parents[2])
if len(sys.argv) > 1:
    BASE = sys.argv[1]
else:
    BASE = os.environ.get("PS183_BASE", "")
if not BASE or not pathlib.Path(BASE).is_dir():
    sys.exit(
        "usage: continuity.py <path-to-pristine-merge-base-worktree>\n"
        "  (or set PS183_BASE). Create one with:\n"
        "    git worktree add --detach /tmp/ps183-base <merge-base-sha>\n"
        f"  got: {BASE!r}"
    )
seeds = list(range(1, 41)) + [1337, 9001, 4242, 31337, 0xABCDEF, 24601, 5150]

moved = []
with tempfile.TemporaryDirectory() as td:
    tmp = pathlib.Path(td)
    for s in seeds:
        b = seen(BASE, s, 0, tmp)
        n = seen(NEW,  s, 0, tmp)
        if b != n:
            moved.append((s, b, n))

short = lambda x: x.split("Renderer: ")[-1].rstrip(")").split(",")[0]
print(f"gen-0 seeds compared: {len(seeds)}")
print(f"MOVED: {len(moved)}/{len(seeds)} = {len(moved)/len(seeds):.1%}")
for s, b, n in moved[:10]:
    print(f"   seed {s}: {short(b)} -> {short(n)}  *** MOVED ***")
if not moved:
    print("=> every existing macOS profile keeps the EXACT card it had. Continuity held.")
