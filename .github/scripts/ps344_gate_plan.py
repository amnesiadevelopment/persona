#!/usr/bin/env python3
"""Resolve the PLAN for one published-engine verdict run: which engine, which
version-matched stock control, and — derived from both — the arm ids and the
exact filenames the verdict will be pointed at.

WHY THIS FILE EXISTS (PS-370)
──────────────────────────────
PS-344 built the instrument: ``scripts/ps344_launch_published.py`` (three arms)
and ``scripts/ps344_verdict.py`` (the judge, three exit codes, a non-waivable
falsification arm). It had **zero callers in ``.github/``** — measured, not
inferred — so the engine an operator downloads unattended was re-verified by
nobody when a release landed. This script is half of the caller; the other half
is ``.github/workflows/published-engine-verdict.yml``.

⭐ THE HAZARD THIS FILE EXISTS TO REMOVE, AND IT IS NOT COSMETIC
────────────────────────────────────────────────────────────────
The launcher writes each arm to ``readings-{engine_id}.json`` and the verdict's
``--product`` / ``--control`` defaults were the literals
``readings-published-152.json`` / ``readings-stock-cft-152.json``. So the arm id
IS the filename IS the verdict's default, and the string ``152`` was
load-bearing at all three hops. An unpinned gate that resolved a *different* tag
and did not rewrite both ends would either write files the verdict cannot find
(loud, exit 2) or — the dangerous one — read a **stale file left by an earlier
run** and report a verdict about last release's binary while naming this one.

So every id here is DERIVED from the version actually resolved, and the plan
carries the filenames rather than letting two sides agree by convention.

⭐ THE THIRD OUTCOME: "NO VERSION-MATCHED CONTROL" IS NOT A FAILURE AND NOT A PASS
──────────────────────────────────────────────────────────────────────────────────
PS-344's central claim is that *a difference between the arms cannot be a
difference between two Chromium releases* — which rests entirely on the control
being the SAME Chromium version as the engine. Chrome for Testing publishes a
per-version archive, and it is real but NOT guaranteed: a Personium build can
land on a Chromium patch level CfT never published. Measured live:

    CfT 152.0.7977.75 -> HTTP 200   (the version PS-344 measured)
    CfT 152.0.7977.82 -> HTTP 200   (a different patch level: obtainable)
    CfT 152.0.7977.99 -> HTTP 404   (plausible-looking, does not exist)

Three named outcomes, and the whole point is that they are distinguishable in
the report rather than collapsed into a colour:

    EXACT     CfT publishes this exact version. The strong claim holds.
    NEAREST   CfT does not, but publishes another patch level of the SAME
              Chromium build line (``<major>.<minor>.<build>.*``). The run
              proceeds AND THE ARM LABEL SAYS SO, because the strong claim is
              now weakened — a difference could in principle be a difference
              between two patch levels. Legitimate only *with* that label.
    NONE      CfT publishes nothing in that build line at all. There is no
              control, so there is no comparison, so there is NOTHING TO
              CONCLUDE. Exit 3, named, red — never green.

⚠️ THE EXIT CODES ARE ALL RED, AND THAT IS THE POINT. This project's standing
register (``engine-gpu-variance.yml``, ``behaviour-launch-lane.yml``) is that
"we failed to look" must never wear the colour of "we looked and it was fine".
What distinguishes the outcomes is the CODE and the NAME, not the colour:

    0  PLAN_OK                    a plan was produced (EXACT or NEAREST)
    2  CANNOT_PLAN                indeterminate — the release list or the CfT
                                  index could not be read. Nothing is claimed.
    3  NO_MATCHED_CONTROL         no CfT build in the engine's Chromium line
    4  ENGINE_REFUSED_BY_POLICY   persona itself refuses to install this build
                                  (``policy.KNOWN_BAD_VERSIONS`` / a ceiling).
                                  Measuring a build persona will not install is
                                  the wrong question; the refusal is correct and
                                  this job must not re-litigate it.

Exit 2 shares the verdict's own vocabulary on purpose: ``ps344_verdict.py``
already spends 2 on INDETERMINATE, and two different numbers for "nothing was
established" would be two vocabularies for one idea.

USAGE
─────
    python3 .github/scripts/ps344_gate_plan.py --out /tmp/ps344-plan.json

``--engine-version`` names ONE published build instead of resolving the newest
— the release-day gesture for a human who has just published an engine tag, and
how a red run is re-read after a fix. It does NOT pin the scheduled job: the
workflow never passes it, because the risk is whatever the newest published
engine release is, which is exactly what a pin hides.

⭐ THAT FLAG RESOLVES A REAL RELEASE. IT USED NOT TO, AND THE ARM WAS INERT.
────────────────────────────────────────────────────────────────────────────
The first cut of this file treated an explicit version as "skip the release
lookup": it derived the tag from the string and left ``engine_url`` and
``engine_digest`` EMPTY while returning ``PLAN_OK``. That plan printed a
correct-looking tag, control and filenames, spent the Chrome for Testing
download, and then died inside the runner — ``updater.download_engine("")``
returns False on its first statement, so the whole dispatch arm could only ever
reach exit 2 INDETERMINATE. The two moments the workflow names as the reasons
this arm exists (a fresh release; re-reading after a red) were exactly the two
it could not serve, and it failed wearing the colour of a transient upstream
flake.

So an explicit version now goes through ``updater.fetch_release_full`` — THE
SINGLE RELEASE-READING PATH, the same function ``fetch_latest_full`` reaches its
own answer through, which accepts a bare version or a prefixed tag and returns
the ``(version, url, digest)`` this branch needs. Two consequences are load-
bearing rather than incidental:

* ``('','','')`` is a REAL ANSWER from that function — the honest response to a
  yanked or deleted release, a tag that never existed, or an APPLICATION tag
  handed over by mistake. It lands on ``CANNOT_PLAN`` NAMING THE TAG, never on
  a ``PLAN_OK`` whose URL is empty. A plan that says OK about something it did
  not resolve is the precise failure mode this file exists to resist.
* ``policy.check`` IS APPLIED HERE TOO. The scheduled arm gets it free from
  ``fetch_latest_checked``; this branch calls it explicitly, so a dispatched tag
  reaches ``ENGINE_REFUSED_BY_POLICY`` exactly as a resolved one does.

  ⛔ AND THERE IS DELIBERATELY NO OVERRIDE FLAG. A human dispatching a specific
  tag is the caller MOST likely to name a build that was just blocklisted —
  because naming it in ``policy.KNOWN_BAD_VERSIONS`` is the documented remedy
  for a red run here, so "re-read the tag I just blocklisted" is a natural next
  gesture. Measuring a build persona refuses is the wrong question whoever
  asked it, and a job whose own remedy can be un-done by its own re-run switch
  would be re-litigating a refusal it exists to produce. An operator who really
  wants that reading removes the tag from the list, which is a visible edit.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# The published index of every Chrome for Testing build, with per-platform
# download URLs. Read rather than probed: enumerating the index answers "is
# there a control for this version" AND "what is the nearest one" in one
# request, where a 404 probe per candidate answers only the first.
CFT_INDEX_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "known-good-versions-with-downloads.json"
)

# PS-344 is a LINUX x86_64 reading and this gate inherits that bound. The
# platform key is stated here rather than derived from the runner, so a job
# accidentally scheduled on another OS fails to plan instead of quietly
# measuring a control for a platform the engine arm is not on.
CFT_PLATFORM = "linux64"

PLAN_OK = 0
CANNOT_PLAN = 2
NO_MATCHED_CONTROL = 3
ENGINE_REFUSED_BY_POLICY = 4

MATCH_EXACT = "exact"
MATCH_NEAREST = "nearest"


def _version_tuple(version: str) -> "tuple[int, ...]":
    parts = []
    for chunk in (version or "").split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def build_line(version: str) -> str:
    """``152.0.7977.75`` -> ``152.0.7977`` — the Chromium BUILD line.

    Two versions sharing this prefix differ only in patch level: same branch,
    same feature set, security fixes between them. That is the widest window in
    which "nearest available control" is defensible at all, and it is why NONE
    is a distinct outcome rather than "just take any 152".
    """
    return ".".join((version or "").split(".")[:3])


def fetch_cft_index(url: str = CFT_INDEX_URL, timeout: int = 30) -> "list[dict]":
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    versions = payload.get("versions")
    if not isinstance(versions, list) or not versions:
        raise ValueError("chrome-for-testing index carried no versions")
    return versions


def _linux_chrome_url(entry: dict) -> str:
    for row in (entry.get("downloads") or {}).get("chrome") or []:
        if row.get("platform") == CFT_PLATFORM:
            return row.get("url") or ""
    return ""


def choose_control(engine_version: str, index: "list[dict]") -> "dict | None":
    """The version-matched stock control for ``engine_version``, or None.

    Returns ``{version, url, match}`` where ``match`` is EXACT or NEAREST.
    None means CfT publishes nothing in the engine's Chromium build line — the
    NO_MATCHED_CONTROL outcome, which is deliberately NOT silently downgraded
    to "some other 152": a control from a different build line would make the
    arms differ for reasons that are not the patch set, which is precisely the
    confound the version match exists to remove.
    """
    line = build_line(engine_version)
    candidates = []
    for entry in index:
        version = entry.get("version") or ""
        if build_line(version) != line:
            continue
        url = _linux_chrome_url(entry)
        if not url:
            # An entry with no linux64 chrome download cannot be a control here,
            # whatever its version says.
            continue
        candidates.append((version, url))

    if not candidates:
        return None

    for version, url in candidates:
        if version == engine_version:
            return {"version": version, "url": url, "match": MATCH_EXACT}

    target = _version_tuple(engine_version)

    def _distance(item):
        version, _ = item
        cand = _version_tuple(version)
        # Compare on the patch component; pad so a malformed version cannot
        # raise. Ties break toward the HIGHER patch level (the newer build),
        # which is the one an operator's Chromium would more likely be.
        span = max(len(target), len(cand))
        t = target + (0,) * (span - len(target))
        c = cand + (0,) * (span - len(cand))
        return (abs(c[-1] - t[-1]), -c[-1])

    version, url = min(candidates, key=_distance)
    return {"version": version, "url": url, "match": MATCH_NEAREST}


def _product_label(engine_tag: str, digest: str) -> str:
    return (
        f"PUBLISHED {engine_tag} — the artifact an operator downloads "
        f"unattended (THE PRODUCT). asset digest: {digest or '<unrecorded>'}"
    )


def _stock_label(control: dict, engine_version: str) -> str:
    if control["match"] == MATCH_EXACT:
        return (
            f"STOCK Chrome for Testing {control['version']} (CONTROL, "
            f"EXACTLY version-matched to the engine's {engine_version}) — a "
            "difference between the arms cannot be a difference between two "
            "Chromium releases."
        )
    return (
        f"STOCK Chrome for Testing {control['version']} (CONTROL, ⚠️ NOT "
        f"exactly version-matched: the engine is {engine_version} and Chrome "
        "for Testing never published that patch level. Same Chromium build "
        f"line {build_line(engine_version)}.x, so the control differs from the "
        "engine by a patch level as well as by the patch set. The claim "
        "'a difference between the arms is the patch set or it is nothing' is "
        "correspondingly WEAKER on this run.)"
    )


def _refusal(tag: str, version: str, verdict: str, message: str) -> dict:
    return {
        "outcome": "ENGINE_REFUSED_BY_POLICY",
        "engine_tag": tag,
        "engine_version": version,
        "policy_verdict": verdict,
        "reason": (
            f"persona itself refuses to install {tag}: {message}. "
            "Measuring a build persona will not install is the wrong "
            "question — the refusal is the correct outcome."
        ),
    }


def plan(
    *,
    engine_version: "str | None" = None,
    resolve_engine=None,
    resolve_release=None,
    policy_check=None,
    index_fetcher=None,
) -> "tuple[int, dict]":
    """Produce the run plan. Returns ``(exit_code, plan_dict)``.

    Every collaborator is injectable so the wiring can be driven at a version
    other than 152 in a test WITHOUT a network call or a 200 MB download — which
    is the only way to prove the ids and filenames actually follow the resolved
    tag rather than merely containing an f-string.

    ⚠️ ``engine_version`` IS NOT A TEST HOOK. It resolves a real release through
    ``resolve_release``; a test that wants determinism injects that collaborator
    rather than relying on the flag skipping the lookup. That collision — one
    flag serving both "drive the wiring offline" and "measure this build" — is
    exactly what let the dispatch arm ship returning ``PLAN_OK`` with an empty
    URL, exercised by tests that never looked at the URL.
    """
    from src.services.engine import policy, updater

    resolve_engine = resolve_engine or (
        lambda: updater.fetch_latest_checked(timeout=30)
    )
    resolve_release = resolve_release or (
        lambda tag: updater.fetch_release_full(tag, timeout=30)
    )
    policy_check = policy_check or policy.check
    index_fetcher = index_fetcher or fetch_cft_index

    if engine_version:
        # An explicit version still goes through the same derivation below, and
        # through the SAME release lookup: the flag chooses WHICH release to
        # read, never whether to read one. See the module docstring.
        requested = updater.version_from_tag(engine_version)
        try:
            found_version, url, digest = resolve_release(requested)
        except Exception as exc:  # noqa: BLE001
            return CANNOT_PLAN, {
                "outcome": "CANNOT_PLAN",
                "engine_tag": updater.engine_tag(requested),
                "engine_version": requested,
                "reason": (
                    f"could not read the release for {requested} — "
                    f"{type(exc).__name__}: {exc}"
                ),
            }
        if not found_version or not url:
            # ('','','') is fetch_release_full's HONEST answer to a yanked or
            # deleted release, a tag that never existed, or an APPLICATION tag
            # handed over by mistake. None of those is a plan.
            return CANNOT_PLAN, {
                "outcome": "CANNOT_PLAN",
                "engine_tag": updater.engine_tag(requested),
                "engine_version": requested,
                "reason": (
                    f"no published engine release resolves for "
                    f"{updater.engine_tag(requested)} — it may have been "
                    "yanked or deleted, may never have existed, or may be an "
                    "APPLICATION tag rather than an engine one. Nothing was "
                    "measured and nothing is claimed."
                ),
            }
        version = updater.version_from_tag(found_version)
        tag = updater.engine_tag(version)
        # `fetch_latest_checked` gives the scheduled arm this for free; the
        # dispatch arm must ask for it explicitly or a human could re-read the
        # very build they just blocklisted. See the module docstring on why
        # there is no override flag.
        verdict, message = policy_check(version)
        if verdict != policy.OK:
            return ENGINE_REFUSED_BY_POLICY, _refusal(tag, version, verdict, message)
    else:
        try:
            raw_tag, url, digest, verdict, message = resolve_engine()
        except Exception as exc:  # noqa: BLE001
            return CANNOT_PLAN, {
                "outcome": "CANNOT_PLAN",
                "reason": (
                    "could not resolve the newest published engine release — "
                    f"{type(exc).__name__}: {exc}"
                ),
            }
        if not raw_tag:
            return CANNOT_PLAN, {
                "outcome": "CANNOT_PLAN",
                "reason": "the release lookup returned no tag at all",
            }
        # `version_from_tag` / `engine_tag` rather than string-slicing: the
        # module owns which side of its API boundary carries the prefix, and a
        # hand-rolled slice here would be a second, driftable copy of that rule.
        version = updater.version_from_tag(raw_tag)
        tag = updater.engine_tag(version)
        if verdict != "ok":
            return ENGINE_REFUSED_BY_POLICY, _refusal(tag, version, verdict, message)

    # ⭐ THE INVARIANT, ASSERTED ONCE FOR BOTH BRANCHES: a plan is a set of
    # instructions the runner can ACT on, and an engine URL is the first of
    # them. `ps344_run_verdict.stage_engine` hands this straight to
    # `updater.download_engine`, whose first statement is `if not url: return
    # False` — so an empty URL here does not fail loudly at plan time, it
    # travels through a PLAN_OK, spends the Chrome for Testing download, and
    # surfaces as INDETERMINATE, a word this vocabulary reserves for "an arm
    # could not be produced or read". That is a plan lying about its own
    # success, which is the one failure mode this file exists to resist.
    #
    # Placed AFTER the branch rather than inside either one on purpose: each
    # branch already refuses its own known empty cases, and this catches the
    # ones neither anticipated (an asset that stops matching, an upstream shape
    # change) without either branch having to remember to.
    if not url:
        return CANNOT_PLAN, {
            "outcome": "CANNOT_PLAN",
            "engine_tag": tag,
            "engine_version": version,
            "reason": (
                f"the release for {tag} resolved without a downloadable engine "
                "asset for this platform, so there is nothing to measure. "
                "NOTHING is claimed about the engine."
            ),
        }

    try:
        index = index_fetcher()
    except Exception as exc:  # noqa: BLE001
        return CANNOT_PLAN, {
            "outcome": "CANNOT_PLAN",
            "engine_tag": tag,
            "engine_version": version,
            "reason": (
                "could not read the Chrome for Testing index — "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    control = choose_control(version, index)
    if control is None:
        return NO_MATCHED_CONTROL, {
            "outcome": "NO_MATCHED_CONTROL",
            "engine_tag": tag,
            "engine_version": version,
            "cft_build_line": build_line(version),
            "reason": (
                f"Chrome for Testing publishes no {CFT_PLATFORM} build in the "
                f"{build_line(version)}.x line, so there is no version-matched "
                "stock control for engine "
                f"{version}. Without a control there is no comparison and "
                "NOTHING is concluded about the patches — this is neither a "
                "pass nor a finding about the engine."
            ),
        }

    # THE DERIVATION. Arm id -> filename -> the verdict's --product/--control.
    product_id = f"published-{version}"
    control_id = f"stock-cft-{control['version']}"
    falsification_id = f"stock-as-product-{version}"

    return PLAN_OK, {
        "outcome": "PLAN_OK",
        "engine_tag": tag,
        "engine_version": version,
        "engine_url": url,
        "engine_digest": digest,
        "control_version": control["version"],
        "control_url": control["url"],
        "control_match": control["match"],
        "cft_build_line": build_line(version),
        "product_id": product_id,
        "control_id": control_id,
        "falsification_id": falsification_id,
        "product_file": f"readings-{product_id}.json",
        "control_file": f"readings-{control_id}.json",
        "falsification_file": f"readings-{falsification_id}.json",
        "product_label": _product_label(tag, digest),
        "stock_label": _stock_label(control, version),
    }


def _emit_github_output(body: dict, path: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in body.items():
            text = "" if value is None else str(value)
            if "\n" in text:
                fh.write(f"{key}<<__PS370_EOF__\n{text}\n__PS370_EOF__\n")
            else:
                fh.write(f"{key}={text}\n")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="Write the plan as JSON to this path")
    ap.add_argument(
        "--engine-version",
        help=(
            "Measure this published engine build instead of resolving the "
            "newest. It is READ FROM THE RELEASE like any other — the flag "
            "chooses which release, never whether to read one — and it passes "
            "through policy.check exactly as the resolved path does. NOT used "
            "by the scheduled job: see the module docstring on why the gate is "
            "unpinned."
        ),
    )
    args = ap.parse_args(argv)

    code, body = plan(engine_version=args.engine_version)

    # ECHOED unconditionally, red or green. The remedy for a bad build is to
    # name its tag in policy.KNOWN_BAD_VERSIONS, which is impossible if the run
    # does not print the tag.
    print(f"outcome        : {body.get('outcome')}")
    print(f"engine tag     : {body.get('engine_tag', '<unresolved>')}")
    print(f"engine version : {body.get('engine_version', '<unresolved>')}")
    if code == PLAN_OK:
        print(f"engine asset   : {body['engine_url']}")
        print(f"control        : Chrome for Testing {body['control_version']}")
        print(f"control match  : {body['control_match'].upper()}")
        print(f"product file   : {body['product_file']}")
        print(f"control file   : {body['control_file']}")
        print(f"falsify file   : {body['falsification_file']}")
        if body["control_match"] == MATCH_NEAREST:
            print(
                "::warning::the stock control is NOT exactly version-matched "
                f"({body['control_version']} vs engine {body['engine_version']})"
                " — the arm label records it and the claim is weaker on this run"
            )
    else:
        print(f"reason         : {body.get('reason')}")

    if args.out:
        out_path = pathlib.Path(args.out)
        # PARENTS TOO. Found by the first CI run: the local reproduction had
        # /tmp/ps370 left over from an earlier step, so `--out` into a directory
        # that does not exist yet raised FileNotFoundError AFTER a correct plan
        # had already been printed — a red run whose log said PLAN_OK.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(body, indent=2, sort_keys=True), encoding="utf-8"
        )

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        _emit_github_output(body, gh_out)

    return code


if __name__ == "__main__":
    raise SystemExit(main())
