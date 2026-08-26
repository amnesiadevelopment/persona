"""PS-189: WHERE on the creepjs page does the SwiftShader string come from?

Three hypotheses have already been FALSIFIED by loopback measurement
(``scripts/ps189_realm_gpu.py``, layer ON, linux/seed24601). Every realm a
detector can reach reported OUR card — ``ANGLE (Intel, Mesa Intel(R) UHD
Graphics 630 (CFL GT2), OpenGL 4.6)`` — across all eleven of:

    page, page_webgl2, iframe_same_origin, iframe_about_blank, iframe_srcdoc,
    worker, worker_nested, worker_in_iframe, worker_http, worker_module,
    shared_worker

and ``navigator.gpu`` is absent, so WebGPU cannot be the vector either. ONE
distinct identity, and it is ours. So the leak PS-186 recorded is **not** a
realm our layer fails to reach.

WHAT IS LEFT, AND WHY IT NEEDS THE LIVE PAGE
----------------------------------------------
PS-186 read the value out of creepjs's rendered TEXT with

    gpu_renderer   (angle \\([^\\n]+\\))

which captures the FIRST ``ANGLE (...)`` substring anywhere in the page. CreepJS
renders more than the browser's own live reading: it also renders values from
its crowd-sourced trust-score database, and its ``gpu:`` row carries an optional
``confidence:`` line (the very reason the sibling ``gpu_vendor`` pattern has a
``(?:confidence:[^\\n]*\\n)?`` group). A pattern that takes the first match on
such a page can report a section OTHER than the one it is believed to report.

So this script does the one thing that settles it: it reads the LIVE page and
dumps EVERY ``ANGLE (`` occurrence with its surrounding text, plus the nearest
preceding heading-ish line. That says which SECTION each value sits in, which is
the provenance question — and no amount of re-reading either codebase answers it.

⚠️ THIS IS EVIDENCE-GATHERING AND IT IS NOT A VERDICT. Two outcomes are live and
they demand opposite fixes:

  * the SwiftShader string sits in a section fed by the BROWSER's own live read
    -> the leak is real and ours, on a path the realm sweep did not cover.
  * it sits in a section fed by creepjs's own database/prediction, or beside a
    heading naming something else
    -> PS-186's ``gpu_renderer`` row is a MEASUREMENT artefact of a
       first-match pattern, the product may not be leaking at all, and the fix
       is to the checker's pattern rather than to ``gpu_ext``.

Deciding between those from the code alone is exactly the move PS-14 forbids
(check the instrument before attributing anything to the product). Neither is
assumed here; the dump is written and read.

THE EXIT IS MANDATORY AND MUST NOT FALL BACK
----------------------------------------------
This is a CHECKER read of a third party, so PS-10's proxy rules bind in full: a
direct connection would hand the operator's real address to creepjs. The exit is
observed and RECORDED per record — ip, city and ASN — and a run that cannot
prove its exit ABORTS rather than falling back to a direct connection. PS-186
measured 5 distinct ASNs across 8 records, so the ASN is written from what was
observed on THIS run and never carried over as a constant.

Run from the repo root::

    .venv/bin/python -m scripts.ps189_live_creepjs -o readings/ps189-.../
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import time

CREEPJS_URL = "https://abrahamjuliot.github.io/creepjs"

# The same settle the checker catalogue uses for creepjs. Its worker and
# trust-score blocks populate late, and a short read would report a page that
# had not finished rendering as a page that said nothing.
SETTLE_SECONDS = 60.0

# Every ``ANGLE (`` occurrence is dumped with this much text either side, which
# is enough to carry the row label and the neighbouring rows without pasting the
# whole page into the record.
CONTEXT_CHARS = 260

# Markers that identify a SOFTWARE RASTERISER, kept identical in spirit to
# ``verify.matrix_consistency``'s own list rather than re-invented here.
SOFTWARE_MARKERS = ("swiftshader", "llvmpipe", "softpipe", "subzero")


def _credential() -> "tuple[str, dict]":
    """The proxy URL, and a CREDENTIAL-FREE note on which channel it came from.

    PS-186 recorded the two sources DISAGREEING on this host — the mounted file
    and the environment hold different credentials — so the channel and the
    divergence are recorded rather than left implicit. The resolver's own
    precedence is used (file wins, environment is the fallback); this script
    does not invent a second rule.

    ``proxy_url`` is credential-shaped and is returned for USE only. Only
    ``source``/``detail``/``diverged`` are put in the returned note, which is
    the split ``Credential`` exists to make possible: the run can state which
    channel it used without the value travelling alongside the statement.
    """
    from src.services.verify import exit_guard

    cred = exit_guard.resolve_credential()
    note = {
        "source": cred.source,
        "detail": cred.detail,
        "diverged": cred.diverged,
    }
    return cred.proxy_url, note


def _sections(text: str) -> "list[dict]":
    """Every ``ANGLE (`` occurrence, with context and its nearest heading.

    The nearest preceding SHORT line is used as the heading: creepjs renders its
    section titles as their own short lines, so the last such line before a
    match is the section the match sits in. Recorded as evidence to be read, not
    as a parsed verdict — a heading guessed this way is a lead, not a proof.
    """
    out = []
    for m in re.finditer(r"angle \(", text, re.IGNORECASE):
        start = max(0, m.start() - CONTEXT_CHARS)
        end = min(len(text), m.start() + CONTEXT_CHARS)
        before = text[:m.start()].splitlines()
        heading = ""
        for line in reversed(before[-40:]):
            stripped = line.strip()
            if stripped and len(stripped) <= 48 and not stripped.lower().startswith("angle ("):
                heading = stripped
                break
        # The full matched line, which is the value as a reader sees it.
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.start())
        value_line = text[line_start: line_end if line_end != -1 else len(text)].strip()
        out.append({
            "offset": m.start(),
            "value_line": value_line,
            "nearest_heading": heading,
            "software_rasteriser": any(k in value_line.lower() for k in SOFTWARE_MARKERS),
            "context": text[start:end],
        })
    return out


def read_cell(*, arm: str, seed: int, proxy_url: str, timezone: str) -> dict:
    """One live creepjs read on one arm, with the page text kept."""
    from src.services.verify import chromium_tier

    original_args = chromium_tier._launch_args
    captured: dict = {}

    def _capturing_args(*a, **kw):
        args = original_args(*a, **kw)
        # The surface actually presented, read off the command line rather than
        # echoed from the request (PS-103). This is what lets a reader check
        # --use-angle=swiftshader before attributing a SwiftShader row to the
        # product rather than to the harness (PS-14).
        captured["argv"] = list(args)
        return args

    chromium_tier._launch_args = _capturing_args
    record: dict = {"arm": arm, "seed": seed, "url": CREEPJS_URL}
    try:
        session = chromium_tier.ChromiumSession(
            proxy_url,
            seed=seed,
            declared_machine=arm,
            timezone=timezone,
            allow_unsandboxed=True,
            install_layer=True,
        )
        with session as live:
            page = live.new_page()
            page.goto(CREEPJS_URL, timeout=120000, wait_until="load")
            time.sleep(SETTLE_SECONDS)
            # inner_text, the SAME path the real matrix run reads through, so
            # nothing here can succeed via a route the live run does not have.
            text = page.inner_text("body")
            layer = getattr(session, "layer_report", None)
            if layer is not None:
                installed = getattr(layer, "installed", None)
                record["layer_installed"] = (
                    sorted(installed)
                    if isinstance(installed, (list, tuple, set))
                    else installed
                )
        record["page_text_chars"] = len(text)
        record["angle_occurrences"] = _sections(text)
        record["page_text"] = text
    except Exception as exc:  # noqa: BLE001 - a failed cell is a recorded cell
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        chromium_tier._launch_args = original_args
    record["argv"] = captured.get("argv")
    return record


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", required=True)
    parser.add_argument("--arms", default="linux")
    parser.add_argument("--seeds", default="24601")
    args = parser.parse_args(argv)

    from src.services.verify import exit_guard

    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())

    proxy_url, source = _credential()

    # REPORT, DON'T FALL BACK. A run that cannot prove its exit stops here; it
    # never continues on a direct connection, which would hand the operator's
    # real address to creepjs.
    exit_obs = exit_guard.observe_exit(proxy_url)
    print(f"[ps189] exit proven: {exit_obs.ip} / {exit_obs.city} / {exit_obs.org}", flush=True)

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for arm in arms:
        for seed in seeds:
            print(f"[ps189] live creepjs {arm}/seed{seed} (settle {SETTLE_SECONDS}s) ...", flush=True)
            # The exit is re-observed PER RECORD: this is a rotating mobile
            # exit and PS-186 measured 5 distinct ASNs across 8 records, so a
            # single observation carried across records would write an ASN that
            # was never true of the later ones.
            per_record_exit = exit_guard.observe_exit(proxy_url)
            rec = read_cell(
                arm=arm, seed=seed, proxy_url=proxy_url,
                timezone=per_record_exit.timezone,
            )
            rec["exit"] = per_record_exit.as_record()
            records.append(rec)
            occ = rec.get("angle_occurrences") or []
            print(f"[ps189]   -> {len(occ)} ANGLE occurrence(s); "
                  f"{sum(1 for o in occ if o['software_rasteriser'])} software-rasteriser",
                  flush=True)

    doc = {
        "schema_version": 1,
        "ticket": "PS-189",
        "observed_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "credential_source": source,
        "records": records,
    }
    (out_dir / "live-creepjs.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    lines = []
    for rec in records:
        ex = rec.get("exit") or {}
        lines.append(f"=== {rec['arm']}/seed{rec['seed']} "
                     f"exit={ex.get('ip')} {ex.get('city')} {ex.get('org')}")
        if rec.get("error"):
            lines.append(f"    ERROR: {rec['error']}")
            continue
        for occ in rec.get("angle_occurrences") or []:
            flag = "  <== SOFTWARE RASTERISER" if occ["software_rasteriser"] else ""
            lines.append(f"    [heading: {occ['nearest_heading']!r}]{flag}")
            lines.append(f"      {occ['value_line'][:200]}")
    summary = "\n".join(lines)
    (out_dir / "live-creepjs-summary.txt").write_text(summary + "\n", encoding="utf-8")
    print()
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
