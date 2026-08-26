"""PS-194: the THREE-LEGGED pixelscan reading — stock vs packaged vs firefox.

PS-194 exists because a confound (the absent GPU) was mistaken for a cause for
three days across four tickets. The cross-engine comparison is the instrument
here, so the legs must be taken on ONE host in ONE session or the comparison is
worth nothing (PS-16's two-engine rule).

Legs A and B (firefox and packaged `fingerprint-chromium`) are taken by
`checker_cli read --engine both`, which writes full 61-row records. This script
takes the MISSING THIRD LEG:

    LEG C — Debian's STOCK /usr/bin/chromium, no persona masking layer at all.

WHY A SEPARATE SCRIPT. `checker_cli`'s chromium tier REFUSES to substitute a
chromium found on PATH (`chromium_tier._engine_binary`), precisely so a stock
reading can never masquerade as a reading of the product. That refusal is
overridden HERE, in a script whose only purpose is the control arm, and never
in the tier itself. This is the same override PS-159 used, kept in the same
shape on purpose.

WHAT THIS LEG DISCRIMINATES. PS-194 has two live candidates for what pixelscan
reads on the chromium path:

  1. something the packaged `fingerprint-chromium` build emits that stock
     Chromium does not — a patch artefact;
  2. something persona's own extension layer emits on the chromium path only.

Candidate 2 is already pointed away from by `ps143`'s layer-off arm, and this
script's leg is taken with `install_layer=False` as well, so BOTH chromium legs
here run with persona's layer OFF. What separates them is the BINARY:

    stock chromium 151  vs  fingerprint-chromium 148

If stock is clean and packaged is flagged, the signal is in the engine's
patches. If BOTH are flagged, the signal is not a persona artefact at all and
the remedy question changes completely.

⚠️ A STOCK READING IS NOT A READING OF PERSONA. Nothing this script produces may
be attributed to persona's product behaviour: its whole subject is a browser
that is not persona.

⚠️ THIS LEG IS NOT A LIKE-FOR-LIKE OF LEGS A/B. It reads pixelscan ALONE (one
page), not the 61-row matrix, and it runs with the layer off. It is a control
arm for ONE question — does the packaged binary differ from stock — and it is
recorded as such rather than scored as a profile.

Run it from the repo root:

    python3 -m scripts.ps194_three_engine_pixelscan -o readings/ps194-2026-08-26/
"""

from __future__ import annotations

import argparse
import json
import os
import time

STOCK = "/usr/bin/chromium"
PIXELSCAN = "pixelscan.net"

# The rows pixelscan renders that carry the verdict. Recorded explicitly so a
# leg that rendered NO verdict block is distinguishable from one that rendered
# it and passed — the coverage-hole trap PS-186's worker found (memory
# 971d07f3): `absent` on an adverse row means only "the adverse pattern did not
# match", never "we passed".
VERDICT_ROWS = ("fingerprint_consistent", "fingerprint_inconsistent", "masking_detected")


def _pixelscan_checker():
    from src.services.verify.checkers import BROWSER_CHECKERS

    for c in BROWSER_CHECKERS:
        if c.id == PIXELSCAN:
            return c
    raise SystemExit(f"{PIXELSCAN} is not in BROWSER_CHECKERS")


def _read_under(binary: str, *, proxy_url: str, timezone: str, label: str):
    """Read pixelscan under ``binary`` with persona's masking layer OFF.

    Returns ``(rows, argv, text)``. The argv actually used is returned so the
    record states the SURFACE THAT WAS PRESENTED rather than the one requested
    (PS-103 discipline) — an axis that silently failed to apply would otherwise
    read as "changing it changed nothing", the exact wrong conclusion.
    """
    from src.services.verify import chromium_tier
    from src.services.verify.browser_tier import readings_from_texts

    checker = _pixelscan_checker()

    original_binary = chromium_tier._engine_binary
    original_args = chromium_tier._launch_args
    captured: dict = {}

    def _patched_args(*a, **kw):
        args = original_args(*a, **kw)
        captured["argv"] = list(args)
        return args

    # Overridden ONLY for the stock leg. The packaged leg uses the tier's own
    # resolution, so it reads exactly the binary the product ships.
    if binary != "PACKAGED":
        chromium_tier._engine_binary = lambda: binary
    chromium_tier._launch_args = _patched_args

    try:
        session = chromium_tier.ChromiumSession(
            proxy_url,
            seed=5150,
            declared_machine="windows",
            timezone=timezone,
            allow_unsandboxed=True,
            # BOTH chromium legs run with the layer OFF, so the ONLY difference
            # between them is the binary. That is what makes this a clean
            # binary-attribution arm rather than another layer arm.
            install_layer=False,
        )
        with session as live:
            page = live.new_page()
            page.goto(checker.url, timeout=90000, wait_until="load")
            time.sleep(checker.settle_seconds)
            text = page.inner_text("body")
    finally:
        chromium_tier._engine_binary = original_binary
        chromium_tier._launch_args = original_args

    rows = readings_from_texts({checker.id: {"text": text}}, checkers=(checker,))
    return rows, captured.get("argv", []), text


def _verdicts(rows):
    out = {}
    for r in rows:
        d = r.as_record() if hasattr(r, "as_record") else dict(r)
        out[d.get("item")] = {"state": d.get("state"), "value": d.get("value")}
    return out


def _verdict_rendered(verdicts) -> bool:
    """Did pixelscan render its verdict block AT ALL on this leg?

    The discriminator is whether EITHER polarity reached `state: read`. Without
    this, a leg where the page never rendered looks identical to a leg that
    rendered and passed — and would be scored as a pass.
    """
    return any(
        (verdicts.get(k) or {}).get("state") == "read"
        for k in ("fingerprint_consistent", "fingerprint_inconsistent")
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="", help="directory to write the record into")
    ap.add_argument(
        "--legs",
        default="stock,packaged",
        help="comma-separated legs to run (stock, packaged)",
    )
    opts = ap.parse_args()

    from src.services.verify.exit_guard import prove_exit

    # MANDATORY and there is no fallback (PS-10). A checker read over a direct
    # connection hands the operator's real address to the service, so a refusal
    # stops the run rather than degrading it.
    proxy_url, exit_, cred = prove_exit()
    print(f"exit proven: {exit_.ip} {exit_.city}/{exit_.country} {exit_.org}")
    print(f"credential source: {cred.source}")
    print()

    import subprocess

    def _ver(path):
        try:
            return subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=30
            ).stdout.strip()
        except Exception as exc:
            return f"unavailable: {exc}"

    packaged_path = None
    try:
        from src.services.verify import chromium_tier

        packaged_path = chromium_tier._engine_binary()
    except Exception as exc:
        packaged_path = f"unavailable: {exc}"

    record: dict = {
        "ticket": "PS-194",
        "subject": (
            "LEG C of the three-legged pixelscan reading: STOCK chromium vs "
            "the PACKAGED fingerprint-chromium, both with persona's masking "
            "layer OFF, so the only difference between them is THE BINARY. "
            "The stock leg is NOT a reading of persona and nothing in it may "
            "be attributed to persona's product behaviour."
        ),
        "exit": exit_.as_record(),
        "credential_source": cred.source,
        "waivers": {
            "allow_unsandboxed_chromium": (
                "REQUIRED on both legs — this host forbids the unprivileged "
                "user namespace. Not the product's default surface; disclosed "
                "rather than left to pass silently."
            ),
            "install_layer_false": (
                "BOTH legs run with persona's masking layer OFF, deliberately. "
                "This arm attributes to the BINARY, not to the layer."
            ),
        },
        "host": {
            "dev_dri_present": os.path.exists("/dev/dri"),
            "stock_chromium": _ver(STOCK),
            "packaged_path": packaged_path,
            "packaged_chromium": (
                _ver(packaged_path)
                if packaged_path and os.path.isfile(str(packaged_path))
                else packaged_path
            ),
        },
        "legs": {},
    }

    out_path = ""
    if opts.out:
        os.makedirs(opts.out, exist_ok=True)
        out_path = os.path.join(opts.out, "leg-c-stock-vs-packaged.json")
        # MERGE rather than clobber: each leg is a separate live read of a
        # settle-delayed page, so a run that died part-way must leave the legs
        # it did complete rather than nothing.
        if os.path.exists(out_path):
            with open(out_path) as fh:
                prior = json.load(fh)
            record["legs"] = prior.get("legs", {})
            record["prior_exits"] = prior.get("prior_exits", [])
            if prior.get("exit", {}).get("ip") != exit_.ip:
                record["prior_exits"] = record["prior_exits"] + [prior["exit"]]

    binaries = {"stock": STOCK, "packaged": "PACKAGED"}

    for leg in [x.strip() for x in opts.legs.split(",") if x.strip()]:
        binary = binaries[leg]
        print(f"--- leg {leg!r} ({binary})")
        entry: dict = {"binary": binary if leg != "packaged" else packaged_path}
        try:
            rows, argv, text = _read_under(
                binary, proxy_url=proxy_url, timezone=exit_.timezone, label=leg
            )
            v = _verdicts(rows)
            entry["verdicts"] = v
            entry["verdict_block_rendered"] = _verdict_rendered(v)
            entry["applied"] = {
                "no_sandbox": "--no-sandbox" in argv,
                "swiftshader_forced": any("swiftshader" in x for x in argv),
            }
            entry["page_text_chars"] = len(text)
            # The WHOLE page text is retained, not just the catalogue rows.
            # DoD #1 needs a FIELD AND ITS VALUE, and the row that actually
            # differs across engines (pixelscan's "Browser" candidate-set row)
            # has no pattern in BROWSER_CHECKERS — so a record that kept only
            # matched rows cannot attribute it. Keeping the text is what makes
            # this leg able to answer the row-level question rather than only
            # the verdict-level one.
            entry["page_text"] = text
            for k in VERDICT_ROWS:
                print(f"    {k:28s} {json.dumps(v.get(k))}")
            if not entry["verdict_block_rendered"]:
                print(
                    "    ⚠️  NO VERDICT BLOCK RENDERED — this leg is a coverage "
                    "hole, NOT a pass."
                )
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"    ERROR {entry['error']}")

        record["legs"][leg] = entry

        if out_path:
            with open(out_path, "w") as fh:
                json.dump(record, fh, indent=2)
            print(f"    (record updated: {out_path})")
        print()

    print(json.dumps(record["legs"], indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
