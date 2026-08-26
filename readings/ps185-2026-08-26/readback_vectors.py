#!/usr/bin/env python3
"""PS-185 — the READBACK VECTORS on BOTH engines, at two seeds, diffed.

WHAT THIS ANSWERS, AND WHY IT IS WORTH TAKING WHILE THE PROXY IS DOWN
----------------------------------------------------------------------
PS-16 Table 2 carries exactly one outright FAILURE: ``creepjs ::
webgl_pixel_hash`` reads ``51df3565`` for BOTH firefox seeds (1337 and 4242),
across two different exits and two different days. Two firefox profiles are
therefore LINKABLE to each other on a vector the project bar names explicitly.
The same row on chromium takes three distinct values at three seeds, which is
what makes it a masking gap rather than a checker constant.

That finding comes from CHECKER data. **PS-182 owns fixing it. This ticket does
not fix it** — it establishes whether the LOOPBACK probe sees the same collision
the checker saw. The two possible answers are genuinely different problems and
must not be averaged into one verdict:

* the probe **also** collides -> the defect is UPSTREAM OF DELIVERY. It is
  visible on a local page with no exit at all, so PS-182 can be worked and
  verified end-to-end without the proxy. That is the cheap outcome.
* the probe **differs** while the checker read identical -> the internal
  difference DOES NOT SURVIVE THE TRIP OUT. That is PS-97's exact lesson and a
  much harder problem: every local green would be meaningless for this vector.

THE TWO-ENGINE RULE IS THE POINT, NOT A COURTESY
-------------------------------------------------
PS-16's rule exists because PS-97 fixed a CHROMIUM path for a defect measured on
FIREFOX and closed; the firefox half went undelivered for three days. So this
runs BOTH engines at BOTH seeds — four readings — and a chromium-only result
would not be a finished measurement, it would be the discovery of the next one.

Chromium is not merely a control here. It is the COUNTER-EVIDENCE that makes a
firefox collision legible: if both engines collide on a vector, the vector is
suspect; if only firefox does, the firefox masking path is.

WHAT IS READ
------------
The whole local probe surface, via ``local_probe`` / ``layer_differential``,
which is the same wiring the real checker runs use (``read_probe_once`` reaches
the page through the SAME session functions). The vectors that matter here:

* ``webgl_pixel_hash``  — PS-90's must-differ vector, and the one PS-16 records
                          as failing on firefox.
* ``canvas_pixel_hash`` — PS-174's addition to the same gate.

Both are DELEGATED rather than spoofed by persona on at least one engine, which
is exactly why they need a loopback reading rather than an assertion.

A vector reading ``unavailable:`` or ``error:`` is the page saying it could not
compute that vector. Those are EXCLUDED from the differ/collide judgement rather
than counted as "unchanged" — two sides agreeing on ``unavailable:no-webgl``
is not evidence of a collision, and counting it as one would manufacture the
false result this package exists to prevent.

THE VERDICT VOCABULARY, AND WHY "INCONCLUSIVE" IS NOT A PASS
--------------------------------------------------------------
Per engine, per vector:

  ``DIFFERS``       the two seeds produced different values. What the bar wants.
  ``COLLIDES``      the two seeds produced the SAME value -> the profiles are
                    linkable on this vector. A finding.
  ``INCONCLUSIVE``  fewer than two readable values. NOT a pass, and never
                    written into PS-16 as one.

USAGE
-----
    python3 readback_vectors.py --engines chromium,firefox \
        --seeds 1337,4242 --output readback-vectors.json
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.verify import layer_differential as ld  # noqa: E402
from src.services.verify import local_probe  # noqa: E402

# The two vectors this ticket is about. Others are recorded but not verdicted.
READBACK_VECTORS = (local_probe.WEBGL_PIXEL_HASH, local_probe.CANVAS_PIXEL_HASH)

DIFFERS = "DIFFERS"
COLLIDES = "COLLIDES"
INCONCLUSIVE = "INCONCLUSIVE"


def _sha256(path: str) -> "str | None":
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def chromium_build() -> dict:
    """persona's chromium, proven by digest rather than asserted."""
    from src.core import platform as _platform
    from src.core.config import ENGINE_DIR

    binary = os.path.join(ENGINE_DIR, _platform.fingerprint_chromium_filename())
    version_file = os.path.join(ENGINE_DIR, "version.txt")
    builds_file = os.path.join(ENGINE_DIR, "builds.json")
    version = (
        open(version_file, encoding="utf-8").read().strip()
        if os.path.isfile(version_file) else None
    )
    declared = None
    if os.path.isfile(builds_file):
        try:
            declared = (
                json.load(open(builds_file, encoding="utf-8"))
                .get("current", {}).get("digest")
            )
        except (ValueError, OSError):
            pass
    actual = _sha256(binary) if os.path.isfile(binary) else None
    return {
        "engine": "fingerprint-chromium",
        "present": os.path.isfile(binary),
        "build": version,
        "sha256": actual,
        "declared_digest": declared,
        "digest_matches_manifest": (
            actual is not None and declared is not None and actual == declared
        ),
    }


def firefox_build() -> dict:
    """The firefox engine's real build string, asked of the binary itself."""
    info: dict = {"engine": "invisible_playwright/firefox"}
    try:
        import invisible_playwright  # noqa: F401
        info["invisible_playwright_importable"] = True
    except Exception as exc:
        info["invisible_playwright_importable"] = False
        info["import_error"] = f"{type(exc).__name__}: {exc}"
    try:
        import invisible_core
        info["invisible_core_version"] = getattr(
            invisible_core, "__version__", None)
    except Exception:
        info["invisible_core_version"] = None

    binary = None
    cache = pathlib.Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        for candidate in sorted(cache.glob("firefox-*/firefox/firefox")):
            binary = str(candidate)
            break
    info["binary"] = binary
    info["present"] = bool(binary)
    if binary:
        try:
            out = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=60
            ).stdout.strip()
            # The sandbox warning on stderr is noise; the version is on stdout.
            info["build"] = out.splitlines()[-1].strip() if out else None
        except (OSError, subprocess.SubprocessError) as exc:
            info["build"] = None
            info["version_error"] = f"{type(exc).__name__}: {exc}"
    return info


def read_one(engine: str, seed: int, url: str) -> dict:
    """One engine, one seed, layer ON — the product's own surface."""
    arm = ld.read_probe_once(
        url,
        seed=seed,
        engine=engine,
        install_layer=True,
        allow_unsandboxed=True,
    )
    record = arm.as_record()
    record["engine"] = engine
    return record


def verdict_for(values: "list[str | None]") -> "tuple[str, str]":
    """Two readings of one vector -> a verdict and its plain-English detail."""
    usable = [
        v for v in values
        if v and not v.startswith("unavailable:") and not v.startswith("error:")
    ]
    if len(usable) < 2:
        return (
            INCONCLUSIVE,
            f"only {len(usable)} of {len(values)} readings produced a usable "
            "value, so the two seeds could not be compared. This is NOT a "
            "pass — it is a failure to look, and must not be recorded as one.",
        )
    if len(set(usable)) == 1:
        return (
            COLLIDES,
            f"both seeds produced the SAME value ({usable[0]!r}). Two profiles "
            "are linkable to each other on this vector.",
        )
    return (
        DIFFERS,
        f"the seeds produced {len(set(usable))} distinct values, so this vector "
        "does not tie the two profiles together.",
    )


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engines", default="chromium,firefox")
    ap.add_argument("--seeds", default="1337,4242")
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]

    readings: "dict[str, dict[str, dict]]" = {}
    with local_probe.serve_probe_page() as server:
        for engine in engines:
            readings[engine] = {}
            for seed in seeds:
                print(f"[readback] {engine}/seed{seed} ...", flush=True)
                try:
                    rec = read_one(engine, seed, server.url)
                except Exception as exc:
                    rec = {
                        "engine": engine,
                        "seed": seed,
                        "error": f"{type(exc).__name__}: {exc}",
                        "reading": {"vectors": {}, "note": "engine refused"},
                    }
                readings[engine][str(seed)] = rec
                vec = rec.get("reading", {}).get("vectors", {})
                for name in READBACK_VECTORS:
                    print(f"    {name} = {vec.get(name)!r}", flush=True)
                if rec.get("error"):
                    print(f"    ERROR: {rec['error']}", flush=True)

    # ---- verdicts, per engine per vector -------------------------------
    per_engine: dict = {}
    for engine in engines:
        per_vector = {}
        for name in READBACK_VECTORS:
            values = [
                readings[engine][str(s)].get("reading", {})
                .get("vectors", {}).get(name)
                for s in seeds
            ]
            v, detail = verdict_for(values)
            per_vector[name] = {
                "seeds": {str(s): values[i] for i, s in enumerate(seeds)},
                "verdict": v,
                "detail": detail,
            }
        per_engine[engine] = per_vector

    # ---- the cross-engine contrast, stated rather than averaged --------
    contrast = {}
    for name in READBACK_VECTORS:
        verdicts = {e: per_engine[e][name]["verdict"] for e in engines}
        if set(verdicts.values()) == {DIFFERS}:
            reading = (
                "Both engines vary this vector by seed on the loopback probe."
            )
        elif COLLIDES in verdicts.values() and DIFFERS in verdicts.values():
            collide = [e for e, v in verdicts.items() if v == COLLIDES]
            differ = [e for e, v in verdicts.items() if v == DIFFERS]
            reading = (
                f"SPLIT: {', '.join(collide)} COLLIDES while "
                f"{', '.join(differ)} DIFFERS. The two engines are not in the "
                "same state on this vector, so a fix on one does not close the "
                "other (PS-16's two-engine rule)."
            )
        elif set(verdicts.values()) == {COLLIDES}:
            reading = (
                "Both engines collide — the vector does not separate profiles "
                "on either engine at these seeds."
            )
        else:
            reading = (
                "At least one engine could not be read; the contrast is "
                "incomplete and is NOT a pass."
            )
        contrast[name] = {"per_engine": verdicts, "reading": reading}

    record = {
        "ticket": "PS-185",
        "measured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "venue": "loopback (127.0.0.1) — no proxy, no exit, no third party",
        "layer": "installed (install_layer=True) — the product's own surface",
        "seeds": seeds,
        "engines": engines,
        "engine_builds": {
            "chromium": chromium_build(),
            "firefox": firefox_build(),
        },
        "readings": readings,
        "verdicts": per_engine,
        "cross_engine_contrast": contrast,
    }
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        print(f"\nwrote {args.output}")

    print("\nREADBACK VECTORS — two seeds, both engines")
    for engine in engines:
        print(f"\n  {engine}:")
        for name in READBACK_VECTORS:
            e = per_engine[engine][name]
            print(f"    {name}: {e['verdict']}")
            for s, v in e["seeds"].items():
                print(f"      seed {s}: {v}")
    print("\n  CROSS-ENGINE CONTRAST")
    for name, c in contrast.items():
        print(f"    {name}: {c['per_engine']}")
        print(f"      {c['reading']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
