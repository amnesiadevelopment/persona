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
Since PS-176 this reading is WIRED TO A PATH THAT CAN GO RED:
``.github/workflows/engine-gpu-variance.yml``, daily at 06:40 UTC. That job
provisions the engine itself (xvfb + the tree's own driver pins + a
``download_engine`` of whatever upstream is serving) and fails on a narrowed
arm. Before PS-176 the judgement was gated and the reading was wired to
nothing; the header below used to say so, and no longer needs to.

⚠️ THE JOB IS DELIBERATELY UNPINNED, and that is the whole design. The
tempting shape is to pin a known engine build so the job is reproducible — but
THE RISK IS UPSTREAM'S ``/releases/latest``, which is exactly what a pin hides.
A gate on a pinned build stays green forever while the build users actually
receive goes bad. So the job measures the same bytes ``updater.fetch_latest()``
hands the operator's app. The cost is accepted knowingly: this job can go red
because upstream changed something, which IS the signal.

It is NOT wired into ``engine-autoupdate.yml``, and that is not an oversight.
Verified by re-running the greps: that job bumps the FIREFOX engine and only
Firefox (``engine-baseline.txt`` is ``firefox-20``, both provisioning steps
import ``services.engine.firefox``), and fingerprint-chromium is touched by no
workflow at all. A chromium variance check hosted there would be a gate that
can never fire on the event it exists to catch.

The two halves, and both are real:

* :func:`classify` is a PURE function over readings. It carries the whole
  verdict — the bar, the skew sensitivity, the sample-size floor — and it is
  exercised in CI on every run, including the cases where it must go RED. A
  regression in the judgement is caught by the normal test suite.
* :func:`measure` is the live half. It needs the product's own engine, which
  the normal CI jobs do not provision (``browser_firefox`` only, see
  ``ci.yml``). It runs in the scheduled job above, and on any operator machine
  via ``python -m src.services.verify.engine_gpu_variance check``.

Because a live ``check`` can only ever demonstrate the outcome the engine
happens to produce today — a pass — the scheduled job runs
``... engine_gpu_variance selftest`` FIRST. That drives synthesised
low-variance readings through the same ``classify`` → ``exit_code_for`` path
and asserts each lands on the exit code it must, so the gate's ability to FAIL
is demonstrated on every run rather than assumed. A check only ever observed
passing is not coverage.

⚠️ WHAT IS STILL NOT COVERED, named rather than left to be discovered:
DETECTION IS DAILY, INSTALLATION IS HOURLY. ``app.py:_check_engines_periodic``
polls every hour, unattended, and installs whatever upstream published, with
``policy.KNOWN_BAD_VERSIONS`` empty and no ceiling — so a bad build can reach
machines up to ~24h before this gate reads it. That window is NOT closable by
measuring at install time: you cannot seed-vary a build before installing it,
and a 15-launch, minutes-long measurement inside an unattended install would
wedge the app. The remedy for a red run is therefore to name the tag in
``policy.KNOWN_BAD_VERSIONS`` — every chromium install passes through
``policy.check()``, so that refusal reaches operators by name without waiting
for a persona release. The record this module writes carries ``engine_build``
for exactly that reason: a finding you cannot attribute to a tag cannot be
acted on.
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


# The arms this module knows persona ships a fallback pool for, and the JS
# variable each one is emitted as. Separated from the scrape itself so
# "this arm has no pool by design" and "this arm HAS a pool and we failed to
# read it" can be told apart — they are different facts and must not share a
# return value.
_POOL_VAR_FOR_ARM = {
    "windows": "WIN_GPUS",
    "macos": "MAC_GPUS",
    "linux": "LINUX_GPUS",
    "android": "ANDROID_GPUS",
}


def has_known_pool(arm: str) -> bool:
    """Whether persona is KNOWN to ship a fallback pool for this arm.

    Answered from the arm name alone, never from the scrape — so a scrape that
    returns nothing on an arm that IS in this map reads as a broken scrape
    rather than as an arm with no pool.
    """
    return arm in _POOL_VAR_FOR_ARM


def fallback_pool_size(arm: str) -> int:
    """How many entries OUR OWN pool for this arm holds.

    Read out of the emitted extension source rather than duplicated here, so
    the bar tracks the pool automatically.

    Returns 0 in TWO different situations, which callers must NOT conflate:
    the arm has no pool at all (:func:`has_known_pool` is False), or the arm
    has one and this scrape could not find it — a regex that drifted out of
    step with the pool literals' formatting. Pair every call with
    :func:`has_known_pool`: 0 on a known-pool arm means WE FAILED TO LOOK, and
    a missing bar must never be read as a bar that was met.
    """
    from .. import browser  # noqa: F401  (kept for a stable import root)
    from ..browser import gpu_ext

    name = _POOL_VAR_FOR_ARM.get(arm)
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
    ``INCONCLUSIVE``  we could not say. Too few readable seeds, OR the arm's
                      own bar could not be read (see ``bar_missing`` below).
                      NOT a pass.

    ⚠️ A MISSING BAR IS NOT A MET BAR. When an arm persona ships a pool for
    (:func:`has_known_pool`) yields no bar, the comparison this gate exists to
    make cannot be made at all, and the arm is ``INCONCLUSIVE`` rather than
    falling through to ``OK`` on the weaker "did it vary at all?" question.
    That weaker question is demonstrably insufficient here: macos varies (2
    distinct values) while colliding 76.9% of the time, so it passes "varied"
    and fails the bar. The same "we failed to look ≠ we looked and it was fine"
    discipline this module applies to ``MIN_SEEDS``, applied to the other input.
    """
    per_arm: "dict[str, dict]" = {}
    for arm in sorted(readings):
        by_seed = readings[arm]
        readable = [v for v in by_seed.values() if v]
        distinct = sorted(set(readable))
        bar = bar_for(arm)
        bar_missing = bar is None and has_known_pool(arm)
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
        elif bar_missing:
            # The arm HAS a pool and we could not read it — the bar this gate
            # compares against is missing, so the comparison cannot be made.
            # Falling through to OK here would silently downgrade the gate to
            # "did it vary at all?", which macos passes while colliding 76.9%
            # of the time. A missing bar is a failure to look, not a pass.
            entry["verdict"] = "INCONCLUSIVE"
            entry["detail"] = (
                f"persona ships a fallback pool for {arm!r}, but this module "
                "could not read its size out of the emitted extension source "
                "— most likely the pool literals were reformatted and "
                "fallback_pool_size's regex no longer matches. Without the bar "
                "there is nothing to compare against, so this is NOT a pass: "
                f"the {p:.1%} collision rate measured here is unjudged. Fix "
                "the scrape in fallback_pool_size and re-run."
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


def engine_build() -> str:
    """The chromium build this reading was taken under, or ``"unknown"``.

    A variance reading whose build is unknown cannot be acted on: the whole
    remedy for a finding is to name the bad tag (``policy.KNOWN_BAD_VERSIONS``),
    and you cannot blocklist a build you cannot name. So the record carries it.

    ⚠️ Resolved from ``version.txt``, which ``download_engine`` does NOT write —
    the UI writes it after a successful install (``app.py``). A provisioning
    step that only downloads therefore leaves this ``"unknown"``, which is why
    the workflow writes it explicitly. Mirrors ``snapshot.engine_build``'s
    contract: never raises, and an unresolved value reads as ``"unknown"``
    rather than as an empty string that looks like a value.
    """
    try:
        from ..engine.updater import current_version

        resolved = current_version()
        return str(resolved) if resolved else "unknown"
    except Exception:
        return "unknown"


def _record(readings: dict, result: dict) -> dict:
    """The artifact written by both ``check`` and ``replay``."""
    return {
        "measured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine_build": engine_build(),
        "engine_authored_arms": sorted(ENGINE_AUTHORED_IDENTITY_ARMS),
        "readings": {
            a: {str(s): v for s, v in by.items()} for a, by in readings.items()
        },
        "result": result,
    }


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
            json.dump(_record(readings, result), fh, indent=2)
    return exit_code_for(result)


def _cmd_replay(args: argparse.Namespace) -> int:
    """Re-verdict readings from a record file, taking no new measurement.

    THIS IS WHAT MAKES THE WIRING'S REDNESS PROVABLE. A live ``check`` can only
    demonstrate the outcome the engine happens to produce today — which is a
    pass, and a gate only ever seen passing is a gate nobody has evidence can
    fail. Replaying a synthesised low-variance record drives the SAME
    ``classify`` → ``exit_code_for`` → process-exit-code path the scheduled job
    gates on, so the job's red half is exercised on every run rather than
    asserted in a comment.

    It deliberately CANNOT be mistaken for a measurement: it takes no reading,
    it is a separate subcommand from ``check``, and the workflow runs it only
    against a file it generated itself.
    """
    try:
        with open(args.record, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"REFUSED: cannot read {args.record}: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    raw = doc.get("readings")
    if not isinstance(raw, dict) or not raw:
        print(
            f"REFUSED: {args.record} carries no readings to re-verdict.",
            file=sys.stderr,
        )
        return EXIT_CANNOT_RUN

    # Seeds round-trip through JSON as strings; classify only counts values, but
    # the ints are restored so a replayed record is shaped like a measured one.
    readings: "dict[str, dict[int, str | None]]" = {}
    for arm, by_seed in raw.items():
        if not isinstance(by_seed, dict):
            print(f"REFUSED: {arm!r} readings are malformed.", file=sys.stderr)
            return EXIT_CANNOT_RUN
        restored: "dict[int, str | None]" = {}
        for seed, value in by_seed.items():
            try:
                key = int(seed)
            except (TypeError, ValueError):
                print(f"REFUSED: {arm!r} has non-integer seed {seed!r}.",
                      file=sys.stderr)
                return EXIT_CANNOT_RUN
            restored[key] = value if isinstance(value, str) and value else None
        readings[arm] = restored

    result = classify(readings)
    print(format_result(result))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(_record(readings, result), fh, indent=2)
    return exit_code_for(result)


# The synthesised readings the self-test drives the gate with. Each is a
# (name, builder, expected exit code) triple. Built from a size the arm's own
# pool makes meaningful rather than from literals, so these keep testing the
# real bar if a pool is ever edited.
def _selftest_cases(arm: str) -> "list[tuple[str, dict, int]]":
    seeds = list(DEFAULT_SEEDS)
    n = len(seeds)
    one = "Vendor | RENDERER-A"
    two = "Vendor | RENDERER-B"
    return [
        # Every profile handed the same card: a flat Level 2 breach.
        ("CONSTANT", {arm: {s: one for s in seeds}}, EXIT_FINDING),
        # Varies (2 distinct, so "did it vary?" would PASS it) but skewed hard,
        # which is the macOS-shaped failure the collision metric exists to catch.
        ("TOO_NARROW",
         {arm: {s: (two if i >= n - 2 else one) for i, s in enumerate(seeds)}},
         EXIT_FINDING),
        # Too few readable seeds. Must be CANNOT_RUN, never PASS.
        ("INCONCLUSIVE",
         {arm: {s: (one if i < 3 else None) for i, s in enumerate(seeds)}},
         EXIT_CANNOT_RUN),
    ]


def _cmd_selftest(args: argparse.Namespace) -> int:
    """Prove this gate can still FAIL, before trusting a green from it.

    A gate is only worth wiring if it can go red, and the live ``check`` cannot
    demonstrate that: the engine currently varies, so a scheduled job would
    only ever be observed passing. That is precisely the "check that could not
    have failed" this project does not count as coverage.

    So the job runs this FIRST. It drives synthesised low-variance readings
    through the same ``classify`` → ``exit_code_for`` path the live check
    gates on and asserts each lands on the exit code it must. If the judgement
    is ever broken such that a shared graphics card reads as a pass, THIS goes
    red — on the gate's own path, on every run — instead of the job quietly
    reporting a green it is no longer able to withhold.
    """
    arm = (args.arm or "").strip() or next(
        iter(sorted(ENGINE_AUTHORED_IDENTITY_ARMS)), ""
    )
    if not arm:
        print(
            "No arm is engine-authored, so there is nothing to police.",
            file=sys.stderr,
        )
        return EXIT_PASS

    failures = []
    for name, readings, expected in _selftest_cases(arm):
        actual = exit_code_for(classify(readings))
        ok = actual == expected
        print(
            f"[selftest] {name:<13} expected exit {expected}, got {actual} "
            f"— {'ok' if ok else 'WRONG'}"
        )
        if not ok:
            failures.append((name, expected, actual))

    if failures:
        print(
            "\nSELF-TEST FAILED: this gate can no longer be trusted to fail.",
            file=sys.stderr,
        )
        for name, expected, actual in failures:
            print(
                f"  {name}: should exit {expected}, exited {actual}",
                file=sys.stderr,
            )
        print(
            "A green from the live check below would be meaningless while this "
            "is broken, so the job stops here rather than reporting one.",
            file=sys.stderr,
        )
        return EXIT_FINDING

    print(
        f"[selftest] the gate still goes red on {arm!r} for a narrowed pool, "
        "and refuses to pass an under-sampled run."
    )
    return EXIT_PASS


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

    r = sub.add_parser(
        "replay",
        help="re-verdict a record file without measuring (proves the gate red)",
    )
    r.add_argument("record", help="a record file written by `check --output`")
    r.add_argument("--output", default="", help="write the re-verdict here")
    r.set_defaults(func=_cmd_replay)

    s = sub.add_parser(
        "selftest",
        help="prove the gate can still go red, without needing the engine",
    )
    s.add_argument("--arm", default="", help="arm to synthesise readings for")
    s.set_defaults(func=_cmd_selftest)
    return ap


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
