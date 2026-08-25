"""Does the ENGINE still give different profiles different GPUs?

This is the guard that makes PS-161's "defer to the engine" arms SAFE to hold.

WHY IT EXISTS
-------------
PS-161 settled who authors the WebGL identity pair per arm. On an arm listed in
``browser.gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS`` persona deliberately stops
spoofing it and lets fingerprint-chromium's own seed-derived value reach the
page — one author per vector, so the two-spoofer contradiction PS-155/PS-161
chased cannot recur there by construction.

That trade makes one of OUR invariants depend on a THIRD PARTY'S implementation
detail. The engine autobumps on a schedule. If a future build stops varying its
GPU by seed — or narrows its pool — every persona profile on that arm silently
shares a graphics card, which is a cross-profile identifier and a direct breach
of Level 2 of the project bar (mutual unlinkability). Nothing else in the
subsystem would notice: the value stays perfectly plausible, so no per-row
"is this a tell?" judgement fires, and `matrix_consistency` cannot see it either
because that module asks whether ONE record agrees with ITSELF. A shared card is
not a self-contradiction — every record is individually consistent, and the
whole population is linked. **The defect is a property of a SET of profiles, and
nothing in the subsystem held a set.** Hence a new lane rather than a reuse:
this is the same shape of gap `matrix_consistency`'s own header describes, one
axis over.

WHAT IT MEASURES, AND THE BAR IT HOLDS THE ENGINE TO
-----------------------------------------------------
For each engine-authored arm: launch N profiles that differ ONLY by seed, with
the masking layer OFF, and read UNMASKED_RENDERER_WEBGL. Then ask not merely
"did it vary?" but **"did it vary at least as well as the pool we gave up?"**

Counting distinct values is not enough, and macOS is the measured proof: across
30 seeds the engine returned two values, which "varies" would score as a pass,
but they were skewed 87/13 — a 76.9% chance that two profiles collide, WORSE
than the 50% of the two-entry pool persona removed. So the metric is the
**pairwise collision probability** (the chance two randomly chosen profiles are
handed the same card, i.e. the Simpson index), which is sensitive to that skew,
and the bar is the collision probability of the arm's own fallback pool in
``gpu_ext``. Deferring must not COST unlinkability relative to authoring it
ourselves; if it does, the arm belongs back under our own layer.

This is deliberately a comparison against something already in the tree rather
than a hand-chosen constant: the number it must beat moves automatically if the
fallback pool is ever edited, so the two cannot drift apart.

WHY THE SAMPLE SIZE IS PART OF THE VERDICT
--------------------------------------------
An estimate from a handful of seeds can clear a bar by luck. A run with too few
seeds is reported ``INCONCLUSIVE`` and is NOT a pass — the same discipline the
rest of this package keeps, where "we failed to look" and "we looked and it was
fine" must never wear the same code. That is what stops this gate from being
quietly satisfied by a cheap two-seed run.

WHERE IT RUNS — STATED PLAINLY, INCLUDING WHERE IT DOES NOT
-------------------------------------------------------------
The live half needs the product's own engine (fingerprint-chromium). Measured
at this commit: CI provisions ``browser_firefox`` only and names
``browser_chromium`` as a real capability nothing declares (``ci.yml``), and
``engine-autoupdate.yml``'s gate is firefox-only for its own recorded reason.
So this lane CANNOT run on the current CI jobs, and pretending otherwise would
be the false green this package is most careful about.

What that leaves is a two-part shape, and both parts are real:

* :func:`classify` is a PURE function over readings. It carries the whole
  verdict — the bar, the skew sensitivity, the sample-size floor — and it is
  exercised in CI on every run, including the cases where it must go RED. A
  regression in the judgement is caught by the normal test suite.
* :func:`measure` is the live half. It runs wherever the engine is installed
  (an operator machine, or the runner that eventually provisions the engine)
  via ``python -m src.services.verify.engine_gpu_variance check``.

The honest summary: the JUDGEMENT is automatically gated today; the READING is
automated but runs only where the engine exists. Wiring it into the chromium
engine's own bump is the remaining step, and it is named here rather than
quietly assumed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time

# The arms whose identity persona has handed to the engine. Imported rather than
# restated so this gate can never police a different set than the product ships:
# add an arm there and it is measured here automatically.
from ..browser.gpu_ext import ENGINE_AUTHORED_IDENTITY_ARMS

EXIT_PASS = 0
EXIT_FINDING = 1
EXIT_CANNOT_RUN = 2

# Tolerance for the bar comparison. Both sides are sums/quotients of floats, so
# an arm sitting EXACTLY at its bar lands a few ulp off it: five evenly-used
# identities over ten seeds gives 5*(0.2)^2 = 0.20000000000000004, which is
# strictly greater than the 0.2 bar and would flip a healthy arm to TOO_NARROW
# on a rounding artefact. Matching the pool we gave up costs nothing and must
# not be a finding, so the comparison is made with a relative tolerance rather
# than on raw `>`. Small enough that it cannot absorb a real narrowing: the
# smallest genuine step at these pool sizes is on the order of a percent.
BAR_TOLERANCE = 1e-9

# Below this many readable seeds an arm is INCONCLUSIVE rather than passed. A
# collision probability estimated from a couple of samples is not evidence, and
# a cheap run must not be able to certify the property.
MIN_SEEDS = 8

# Seeds used when the caller names none. Arbitrary but FIXED, so two runs are
# comparable, and spread rather than sequential.
DEFAULT_SEEDS = (
    9001, 4242, 1337, 7, 101, 555, 2024, 31337,
    86420, 12345, 99, 777, 31415, 271828, 161803,
)

SETTLE_SECONDS = 2.0


class VarianceCannotRun(RuntimeError):
    """The reading could not be taken, so nothing was established."""


def collision_probability(values: "list[str]") -> float:
    """P(two independently chosen profiles are handed the same identity).

    The Simpson index — sum of squared frequencies. Chosen over a bare distinct
    count because it is sensitive to SKEW, which is the failure the macOS
    measurement actually exhibited: two values split 87/13 collide 77% of the
    time, while two values split 50/50 collide 50% of the time. A distinct
    count scores those identically and would have called the first one a pass.

    1.0 means every profile shares one identity; lower is better.
    """
    if not values:
        return 1.0
    n = len(values)
    counts: "dict[str, int]" = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sum((c / n) ** 2 for c in counts.values())


def fallback_pool_size(arm: str) -> int:
    """How many entries OUR OWN pool for this arm holds.

    Read out of the emitted extension source rather than duplicated here, so
    the bar tracks the pool automatically. Returns 0 when the arm has no pool
    this module can find, which callers must treat as "no bar to compare
    against" rather than as a bar of zero.
    """
    from .. import browser  # noqa: F401  (kept for a stable import root)
    from ..browser import gpu_ext

    name = {
        "windows": "WIN_GPUS",
        "macos": "MAC_GPUS",
        "linux": "LINUX_GPUS",
        "android": "ANDROID_GPUS",
    }.get(arm)
    if not name:
        return 0
    src = gpu_ext._CONTENT_SCRIPT
    m = re.search(r"var " + name + r" = \[(.*?)\n  \];", src, re.S)
    if not m:
        return 0
    return m.group(1).count("unmaskedVendor")


def bar_for(arm: str) -> "float | None":
    """The collision probability the engine must BEAT on this arm.

    It is the collision probability of persona's own pool for the arm, assumed
    uniform (which is what ``pick()``'s modulo over a hash produces, and what
    the measured distributions confirm to within a fraction of a percent). None
    when there is no pool to compare against.
    """
    n = fallback_pool_size(arm)
    if n <= 0:
        return None
    return 1.0 / n


def classify(readings: "dict[str, dict[int, str | None]]") -> dict:
    """Turn per-arm, per-seed identity readings into a verdict. PURE.

    ``readings`` maps arm -> {seed: identity string or None}. A None is a seed
    that could not be read, and is EXCLUDED from the statistics rather than
    counted as a value — an unreadable cell and a colliding cell are different
    findings, and merging them would let a broken run read as a narrow pool.

    Every arm gets one of:

    ``OK``            varied at least as well as the pool we gave up.
    ``TOO_NARROW``    the finding. The engine's identities collide MORE often
                      than persona's own pool would have, so deferring is
                      costing unlinkability and the arm should return to
                      ``gpu_ext``'s authorship.
    ``CONSTANT``      every profile got the SAME identity — the severe form of
                      TOO_NARROW, called out separately because it is a flat
                      Level 2 breach rather than a degradation.
    ``INCONCLUSIVE``  too few readable seeds to say. NOT a pass.
    """
    per_arm: "dict[str, dict]" = {}
    for arm in sorted(readings):
        by_seed = readings[arm]
        readable = [v for v in by_seed.values() if v]
        distinct = sorted(set(readable))
        bar = bar_for(arm)
        entry: dict = {
            "seeds_requested": len(by_seed),
            "seeds_readable": len(readable),
            "distinct_identities": len(distinct),
            "identities": distinct,
            "fallback_pool_size": fallback_pool_size(arm),
            "bar_collision_probability": bar,
        }
        if len(readable) < MIN_SEEDS:
            entry["verdict"] = "INCONCLUSIVE"
            entry["collision_probability"] = None
            entry["detail"] = (
                f"only {len(readable)} of {len(by_seed)} seeds produced a "
                f"reading; {MIN_SEEDS} are required before this gate will say "
                "anything. This is NOT a pass — an estimate from too few "
                "samples can clear the bar by luck."
            )
            per_arm[arm] = entry
            continue

        p = collision_probability(readable)
        entry["collision_probability"] = p
        if len(distinct) == 1:
            entry["verdict"] = "CONSTANT"
            entry["detail"] = (
                f"every one of {len(readable)} profiles was handed the SAME "
                f"identity ({distinct[0]!r}). That is a shared cross-profile "
                "identifier and a direct breach of Level 2 (mutual "
                "unlinkability). This arm must NOT be left engine-authored: "
                "remove it from gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS so "
                "persona's own pool authors the identity again."
            )
        elif bar is not None and p > bar * (1.0 + BAR_TOLERANCE):
            entry["verdict"] = "TOO_NARROW"
            entry["detail"] = (
                f"two profiles collide {p:.1%} of the time, WORSE than the "
                f"{bar:.1%} of persona's own {entry['fallback_pool_size']}-entry "
                "pool for this arm. Deferring to the engine is now COSTING "
                "unlinkability rather than merely removing a second author, so "
                "the arm should return to gpu_ext's authorship."
            )
        else:
            entry["verdict"] = "OK"
            entry["detail"] = (
                f"{len(distinct)} distinct identities over {len(readable)} "
                f"seeds; two profiles collide {p:.1%} of the time"
                + (
                    f", at or below the {bar:.1%} of persona's own "
                    f"{entry['fallback_pool_size']}-entry pool"
                    if bar is not None
                    else ""
                )
            )
        per_arm[arm] = entry

    findings = [a for a, e in per_arm.items()
                if e["verdict"] in ("CONSTANT", "TOO_NARROW")]
    inconclusive = [a for a, e in per_arm.items()
                    if e["verdict"] == "INCONCLUSIVE"]
    return {
        "per_arm": per_arm,
        "findings": findings,
        "inconclusive": inconclusive,
        "arms_checked": sorted(per_arm),
    }


def exit_code_for(result: dict) -> int:
    """PASS / FINDING / CANNOT_RUN, keeping this package's discipline.

    An INCONCLUSIVE arm is EXIT_CANNOT_RUN, never EXIT_PASS: "we failed to
    look" must not wear the code that means "we looked and it was fine".
    """
    if result["findings"]:
        return EXIT_FINDING
    if result["inconclusive"] or not result["arms_checked"]:
        return EXIT_CANNOT_RUN
    return EXIT_PASS


def format_result(result: dict) -> str:
    lines = ["ENGINE GPU IDENTITY VARIANCE — do different profiles get different GPUs?"]
    if not result["arms_checked"]:
        lines.append("")
        lines.append(
            "  No arm is engine-authored, so there is nothing to police. "
            "(gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS is empty.)"
        )
        return "\n".join(lines)
    for arm in result["arms_checked"]:
        e = result["per_arm"][arm]
        p = e["collision_probability"]
        lines.append("")
        lines.append(f"  {arm}: {e['verdict']}")
        lines.append(
            f"    {e['distinct_identities']} distinct over "
            f"{e['seeds_readable']}/{e['seeds_requested']} readable seeds"
            + (f", collision {p:.1%}" if p is not None else "")
        )
        lines.append(f"    {e['detail']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The LIVE half. Everything above this line is pure and runs in CI.
# --------------------------------------------------------------------------

_IDENTITY_PROBE_PAGE = """<!doctype html>
<meta charset="utf-8"><title>engine gpu identity</title>
<body><pre id="out">reading...</pre>
<script>
(function () {
  var out = {};
  try {
    var c = document.createElement('canvas');
    var gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) { out.error = 'no webgl context'; }
    else {
      var d = gl.getExtension('WEBGL_debug_renderer_info');
      if (!d) { out.error = 'no debug_renderer_info'; }
      else {
        out.vendor = String(gl.getParameter(d.UNMASKED_VENDOR_WEBGL));
        out.renderer = String(gl.getParameter(d.UNMASKED_RENDERER_WEBGL));
      }
    }
  } catch (e) { out.error = String(e); }
  document.getElementById('out').textContent = JSON.stringify(out);
})();
</script></body>
"""


def _serve():
    """Loopback server for the probe page.

    The venue is 127.0.0.1 deliberately: this reads what the BROWSER reports to
    a page, and contacts no third party, so there is no address to leak and no
    exit to prove. That is the same venue ``local_probe`` establishes, and it is
    NOT a waiver of the proxied-exit rule, which governs checker reads.
    """
    import http.server
    import threading

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = _IDENTITY_PROBE_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a, **kw):
            pass

    class _S:
        def __enter__(self):
            self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
            self._t = threading.Thread(
                target=self._srv.serve_forever,
                kwargs={"poll_interval": 0.1}, daemon=True)
            self._t.start()
            h, p = self._srv.server_address[:2]
            self.url = f"http://{h}:{p}/"
            return self

        def __exit__(self, *e):
            try:
                self._srv.shutdown()
            finally:
                self._srv.server_close()
            self._t.join(timeout=5)

    return _S()


def measure(
    arms: "tuple[str, ...]", seeds: "tuple[int, ...]"
) -> "dict[str, dict[int, str | None]]":
    """Read the engine's own identity for each (arm, seed), layer OFF.

    ``install_layer=False`` is the whole point: this measures what the engine
    produces WITHOUT persona's masking, which is exactly what an
    engine-authored arm ships to a page.
    """
    from . import chromium_tier

    readings: "dict[str, dict[int, str | None]]" = {a: {} for a in arms}
    with _serve() as server:
        for arm in arms:
            for seed in seeds:
                value = None
                try:
                    session = chromium_tier.ChromiumSession(
                        "",
                        seed=seed,
                        declared_machine=arm,
                        allow_unsandboxed=True,
                        allow_no_proxy=True,
                        install_layer=False,
                    )
                    with session as live:
                        page = live.new_page()
                        page.goto(server.url, timeout=90000, wait_until="load")
                        time.sleep(SETTLE_SECONDS)
                        data = json.loads(page.inner_text("body"))
                    if data.get("vendor") and data.get("renderer"):
                        value = f"{data['vendor']} | {data['renderer']}"
                except Exception as exc:  # a cell that failed is recorded as None
                    print(
                        f"[variance] {arm}/{seed}: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                readings[arm][seed] = value
                print(f"[variance] {arm}/seed{seed}: {value}", flush=True)
    return readings


def _cmd_check(args: argparse.Namespace) -> int:
    arms = tuple(
        a.strip() for a in (args.arms or "").split(",") if a.strip()
    ) or tuple(sorted(ENGINE_AUTHORED_IDENTITY_ARMS))
    if not arms:
        print(
            "No arm is engine-authored, so there is nothing to police.",
            file=sys.stderr,
        )
        return EXIT_PASS
    seeds = tuple(
        int(s.strip()) for s in (args.seeds or "").split(",") if s.strip()
    ) or DEFAULT_SEEDS

    try:
        readings = measure(arms, seeds)
    except Exception as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Nothing was established, so this is NOT 'the engine varies'.",
            file=sys.stderr,
        )
        return EXIT_CANNOT_RUN

    result = classify(readings)
    print(format_result(result))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "measured_at": datetime.datetime.now(
                        datetime.timezone.utc).isoformat(),
                    "engine_authored_arms": sorted(ENGINE_AUTHORED_IDENTITY_ARMS),
                    "readings": {
                        a: {str(s): v for s, v in by.items()}
                        for a, by in readings.items()
                    },
                    "result": result,
                },
                fh, indent=2,
            )
    return exit_code_for(result)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="engine_gpu_variance", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser(
        "check",
        help="launch one profile per seed per engine-authored arm and verdict it",
    )
    c.add_argument("--arms", default="", help="override the arms to check")
    c.add_argument("--seeds", default="", help="override the seeds to use")
    c.add_argument("--output", default="", help="write the record here")
    c.set_defaults(func=_cmd_check)
    return ap


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
