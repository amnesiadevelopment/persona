#!/usr/bin/env python3
"""PS-182 — emit the SHIPPED Firefox WebGL init script, one file per seed.

This exists so `harness.js` executes **the product's own text** rather than a
transcription of it. A transcription would prove only that the harness is
self-consistent — the PS-11 "asserts on what was written, not on what happens"
failure class, arriving inside the instrument built to avoid it.

Run from the repo root:

    python3 readings/ps182-2026-08-26/emit_scripts.py
    node readings/ps182-2026-08-26/harness.js

The emitted scripts are committed alongside the harness so the reading is
re-derivable exactly as taken, without needing the tree at that commit.
"""

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

sys.path.insert(0, str(REPO))

from src.services.browser.webgl_ext import firefox_webgl_init_script  # noqa: E402

SEEDS = (111, 1337, 4242, 9001)


def main() -> None:
    out = HERE / "scripts"
    out.mkdir(exist_ok=True)
    for seed in SEEDS:
        js = firefox_webgl_init_script(seed)
        (out / f"ff_{seed}.js").write_text(js, encoding="utf-8")
        print(f"wrote scripts/ff_{seed}.js  ({len(js)} bytes)")


if __name__ == "__main__":
    main()
