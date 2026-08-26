"""PS-194: capture pixelscan's FULL RENDERED TEXT on both engines, one session.

DoD #1 of PS-194 is not "chromium is flagged" — it is a FIELD AND ITS VALUE.
Every record this project owns stores only the rows the catalogue knows how to
match, so a signal pixelscan renders that we have no pattern for is invisible in
all of them. This script keeps the WHOLE page text for both engines so the
differing row can be found by reading rather than by guessing which pattern to
add.

Both legs run WITH persona's masking layer ON — this is the PRODUCT surface,
unlike leg C which was a binary-attribution arm with the layer off.

    python3 -m scripts.ps194_pixelscan_text -o readings/ps194-2026-08-26/
"""

from __future__ import annotations

import argparse
import json
import os
import time

PIXELSCAN = "pixelscan.net"


def _pixelscan_checker():
    from src.services.verify.checkers import BROWSER_CHECKERS

    for c in BROWSER_CHECKERS:
        if c.id == PIXELSCAN:
            return c
    raise SystemExit(f"{PIXELSCAN} is not in BROWSER_CHECKERS")


def _read_chromium(proxy_url, timezone, seed=5150):
    from src.services.verify import chromium_tier

    checker = _pixelscan_checker()
    session = chromium_tier.ChromiumSession(
        proxy_url,
        seed=seed,
        declared_machine="windows",
        timezone=timezone,
        allow_unsandboxed=True,
        install_layer=True,
    )
    with session as live:
        page = live.new_page()
        page.goto(checker.url, timeout=90000, wait_until="load")
        time.sleep(checker.settle_seconds)
        return page.inner_text("body")


def _read_firefox(proxy_url, timezone, seed=5150):
    """Read pixelscan on the firefox engine through the tier's OWN launch path.

    `firefox_session` is deliberately THE ONE COPY of the launch-and-install
    wiring (see its docstring): driving it rather than re-implementing a launch
    here is what keeps this capture on the same surface a real reading uses. It
    yields the live CONTEXT, not a Browser — that distinction is what makes the
    masking layer install at all.

    Note there is no declared_machine: `InvisiblePlaywright` takes no OS
    argument and presents Windows regardless (#211).
    """
    from src.services.verify.browser_tier import firefox_session

    checker = _pixelscan_checker()
    with firefox_session(proxy_url, seed=seed, install_layer=True) as live:
        page = live.new_page()
        page.goto(checker.url, timeout=90000, wait_until="load")
        time.sleep(checker.settle_seconds)
        return page.inner_text("body")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--engines", default="chromium,firefox")
    ap.add_argument("--seed", type=int, default=5150)
    ap.add_argument("--label", default="")
    opts = ap.parse_args()

    from src.services.verify.exit_guard import prove_exit

    proxy_url, exit_, cred = prove_exit()
    print(f"exit proven: {exit_.ip} {exit_.city}/{exit_.country} {exit_.org}")

    record = {
        "ticket": "PS-194",
        "subject": (
            "pixelscan's FULL rendered text on both engines, layer ON "
            "(the product surface), for row-level identification of the signal."
        ),
        # The FILE-level exit is this invocation's only. It is NOT the exit of
        # legs merged in from an earlier run — read `engines[<leg>]["exit"]`
        # for that. See the merge note below.
        "exit": exit_.as_record(),
        "credential_source": cred.source,
        "engines": {},
    }

    out_path = ""
    if opts.out:
        os.makedirs(opts.out, exist_ok=True)
        out_path = os.path.join(opts.out, "pixelscan-page-text.json")
        if os.path.exists(out_path):
            # MERGE, and preserve each prior leg WITH ITS OWN EXIT. Round 1 of
            # PS-194 merged only `engines` while rebuilding `exit` from the
            # current run, so re-invoking for a second leg silently OVERWROTE
            # the exit the earlier legs had actually run through — leaving one
            # exit block for three legs and making them unrecoverable. The
            # ticket's method constraint is "record exit IP, city and ASN PER
            # RECORD", and the exit is per-LEG because each leg proves its own.
            with open(out_path) as fh:
                prior = json.load(fh)
            record["engines"] = prior.get("engines", {})
            # A leg written before this fix carries no per-leg exit. Backfill
            # from the file-level exit ONLY when it is unambiguous (a single
            # prior leg): with several, the file-level value belongs to
            # whichever invocation wrote last and attributing it to all of
            # them would MINT evidence. Leave those absent — derive.py reports
            # the gap.
            prior_legs = record["engines"]
            if len(prior_legs) == 1 and prior.get("exit"):
                only = next(iter(prior_legs.values()))
                if isinstance(only, dict):
                    only.setdefault("exit", prior["exit"])

    readers = {"chromium": _read_chromium, "firefox": _read_firefox}

    for eng in [e.strip() for e in opts.engines.split(",") if e.strip()]:
        print(f"--- {eng}")
        try:
            text = readers[eng](proxy_url, exit_.timezone, opts.seed)
            key = (opts.label or eng)
            record["engines"][key] = {
                "text": text,
                "chars": len(text),
                "seed": opts.seed,
                # PER-LEG exit: this leg proved this exit. The pool rotates, so
                # a later invocation's exit says nothing about this leg.
                "exit": exit_.as_record(),
            }
            print(f"    captured {len(text)} chars")
        except Exception as exc:
            record["engines"][(opts.label or eng)] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"    ERROR {type(exc).__name__}: {exc}")

        if out_path:
            with open(out_path, "w") as fh:
                json.dump(record, fh, indent=2)
            print(f"    (written {out_path})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
