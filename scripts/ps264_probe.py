"""PS-264 premise-inversion probe (AC3) and behaviour-preservation sweep (AC5).

Two modes, both measuring what a PAGE reads out of the emitted device.js under
``node:vm`` — never the emitted source text.

  ``untagged``  — the AC3 red half: append an UNTAGGED resolution to each
                  ``ALL_RES`` arm the way a maintainer widening the pool would
                  actually write it, and report how many generation-0 profiles
                  are re-indexed onto a different monitor.
  ``sweep``     — the AC5 reading: dump the (width, height) every seed sees on
                  both ``os_type`` arms, for diffing across the lift.

Run from the repo root:  python3 scripts/ps264_probe.py untagged|sweep [out.json]
"""

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.services.browser import device_ext  # noqa: E402

_SCREEN_READ = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const sandbox = {};
require('vm').createContext(sandbox);
require('vm').runInContext(
  "globalThis.self = globalThis; globalThis.window = globalThis; " +
  "globalThis.top = globalThis; " +
  "globalThis.outerWidth = 800; globalThis.innerWidth = 800; " +
  "globalThis.outerHeight = 600; globalThis.innerHeight = 600; " +
  "globalThis.screen = { width: 1, height: 1, availWidth: 1, availHeight: 1," +
  " colorDepth: 1, pixelDepth: 1 };",
  sandbox
);
require('vm').runInContext(src, sandbox);
console.log(JSON.stringify(require('vm').runInContext(
  "({ width: screen.width, height: screen.height })", sandbox
)));
"""


def screen_seen(tmp, seed, generation, os_type, tag):
    d = pathlib.Path(
        device_ext.build_device_extension(
            seed, str(tmp / f"{tag}-{os_type}-{seed}"), generation,
            os_type=os_type,
        )
    )
    harness = d / "harness.js"
    harness.write_text(_SCREEN_READ, encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(d / "device.js")],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert seen["width"] != 1, "extension did not patch screen — measured nothing"
    return (seen["width"], seen["height"])


SEEDS = list(range(20))

# The edit a maintainer widening a pool actually writes: no generation tag at
# all. In the JS literal that is a two-element row (`(r[2] || 0)` reads it as
# generation 0); in the Python records it is an entry with no `since=`.
_MAC_TAIL = "[1728, 1117, 0], [2560, 1440, 0],"
_WIN_TAIL = "[1680, 1050, 0], [1920, 1200, 0], [2560, 1080, 0], [2560, 1440, 0],"


def _untagged_source(script):
    """Append `[3840, 2160]` — untagged — to both arms of the JS literal."""
    assert script.count(_MAC_TAIL) == 1 and script.count(_WIN_TAIL) == 1
    return script.replace(
        _MAC_TAIL, _MAC_TAIL + " [3840, 2160],"
    ).replace(_WIN_TAIL, _WIN_TAIL + " [3840, 2160],")


def main():
    mode = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    result = {}
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for os_type in ("windows", "macos"):
            before = {
                s: screen_seen(tmp, s, 0, os_type, "before") for s in SEEDS
            }
            result[os_type] = {"before": {str(k): v for k, v in before.items()}}
            if mode == "sweep":
                continue
            original = device_ext._CONTENT_SCRIPT
            try:
                # Prefer the registry when it exists (post-lift); fall back to
                # a textual append on the pre-lift tree.
                pools = getattr(device_ext, "SCREEN_RES_POOLS", None)
                if pools is None:
                    device_ext._CONTENT_SCRIPT = _untagged_source(original)
                else:
                    entry = device_ext.ScreenResolutionEntry(3840, 2160)
                    assert entry.since == 0
                    saved = {k: list(v) for k, v in pools.items()}
                    for k in pools:
                        pools[k] = saved[k] + [entry]
                after = {
                    s: screen_seen(tmp, s, 0, os_type, "after") for s in SEEDS
                }
            finally:
                device_ext._CONTENT_SCRIPT = original
                pools = getattr(device_ext, "SCREEN_RES_POOLS", None)
                if pools is not None:
                    for k in saved:
                        pools[k] = saved[k]
            moved = [s for s in SEEDS if after[s] != before[s]]
            result[os_type]["after"] = {str(k): v for k, v in after.items()}
            result[os_type]["moved"] = moved
            print(
                f"[{os_type}] gen-0 profiles MOVED by UNTAGGED append: "
                f"{len(moved)}/{len(SEEDS)} "
                f"({100.0 * len(moved) / len(SEEDS):.1f}%)"
            )
    if out_path:
        pathlib.Path(out_path).write_text(json.dumps(result, indent=2))
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
