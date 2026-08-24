"""PS-150: is it the PACKAGED ENGINE or the HOST? Read pixelscan under STOCK chromium.

PS-143 cleared persona's masking layer (both verdicts fire with it on and off).
PS-150 arm A/B cleared the verification tier's ``geo_ext`` gap (both verdicts
fire with the gap closed, and the geo vector was proved to reach the page).
That leaves two live candidates, and ONE observation separates them:

* **the packaged engine** — ``ungoogled-chromium`` patched with ``--fingerprint``,
  which persona SHIPS. A verdict caused by it is a finding about the product.
* **the host** — a containerised, sandbox-less browser has tells of its own, and
  every reading in this campaign carries ``--allow-unsandboxed-chromium``.

So read the SAME page, through the SAME proven exit, under a browser that is
NOT persona's: Debian's stock ``/usr/bin/chromium``. If pixelscan still says
``masking_detected`` / ``fingerprint_inconsistent`` under a vanilla browser
carrying none of persona's code, the verdicts are a fact about this HOST. If
stock comes back clean where the packaged engine did not, they belong to the
ENGINE persona ships.

⚠️ A STOCK READING IS NOT A READING OF PERSONA, and this script never pretends
otherwise. ``chromium_tier._engine_binary`` REFUSES to substitute a chromium
found on PATH precisely so a stock reading can never be mistaken for the
product; that refusal is overridden here deliberately, in a script whose only
purpose is the control arm, and never in the tier itself.

Run it from the repo root:

    xvfb-run -a .venv/bin/python -m scripts.ps150_stock_control
"""

from __future__ import annotations

import json
import time

STOCK = "/usr/bin/chromium"
PIXELSCAN = "pixelscan.net"


def _pixelscan_checker():
    from src.services.verify.checkers import BROWSER_CHECKERS

    for c in BROWSER_CHECKERS:
        if c.id == PIXELSCAN:
            return c
    raise SystemExit(f"{PIXELSCAN} is not in BROWSER_CHECKERS")


def _read_under(binary: str, *, proxy_url: str, timezone: str, install_layer: bool):
    """Read pixelscan under ``binary``. Returns (rows, layer_installed)."""
    from src.services.verify import chromium_tier
    from src.services.verify.browser_tier import readings_from_texts

    checker = _pixelscan_checker()

    original = chromium_tier._engine_binary
    chromium_tier._engine_binary = lambda: binary
    try:
        session = chromium_tier.ChromiumSession(
            proxy_url,
            seed=9001,
            declared_machine="windows",
            timezone=timezone,
            allow_unsandboxed=True,
            install_layer=install_layer,
        )
        with session as live:
            installed = session.layer_report.installed
            page = live.new_page()
            page.goto(checker.url, timeout=90000, wait_until="load")
            time.sleep(checker.settle_seconds)
            text = page.inner_text("body")
    finally:
        chromium_tier._engine_binary = original

    rows = readings_from_texts({checker.id: {"text": text}}, checkers=(checker,))
    return rows, installed


def _verdicts(rows):
    out = {}
    for r in rows:
        d = r.as_record() if hasattr(r, "as_record") else dict(r)
        out[d.get("item")] = d.get("value")
    return out


def main() -> int:
    from src.services.verify.chromium_tier import _engine_binary
    from src.services.verify.exit_guard import prove_exit

    proxy_url, exit_ = prove_exit()
    print(f"exit proven: {exit_.ip} {exit_.city}/{exit_.country} {exit_.timezone}")
    print()

    arms = {}

    # CONTROL: Debian's stock chromium. None of persona's code, none of its
    # layer — but the same host, the same exit, the same no-sandbox waiver.
    print(f"reading {PIXELSCAN} under STOCK {STOCK} ...")
    stock_rows, _ = _read_under(
        STOCK, proxy_url=proxy_url, timezone=exit_.timezone, install_layer=False
    )
    arms["stock_chromium"] = _verdicts(stock_rows)

    # REFERENCE: persona's packaged engine, layer OFF — the engine alone.
    packaged = _engine_binary()
    print(f"reading {PIXELSCAN} under PACKAGED {packaged} (layer OFF) ...")
    pkg_rows, _ = _read_under(
        packaged, proxy_url=proxy_url, timezone=exit_.timezone, install_layer=False
    )
    arms["packaged_engine_layer_off"] = _verdicts(pkg_rows)

    print()
    print(json.dumps({"exit": exit_.as_record(), "arms": arms}, indent=2))

    print()
    print("%-26s %-22s %-22s" % ("item", "stock chromium", "packaged (layer off)"))
    keys = sorted(set(arms["stock_chromium"]) | set(arms["packaged_engine_layer_off"]))
    for k in keys:
        s = json.dumps(arms["stock_chromium"].get(k))[:20]
        p = json.dumps(arms["packaged_engine_layer_off"].get(k))[:20]
        mark = "   <== DIFFERS" if s != p else ""
        print("%-26s %-22s %-22s%s" % (k, s, p, mark))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
