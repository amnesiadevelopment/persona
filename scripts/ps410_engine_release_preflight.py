#!/usr/bin/env python3
"""PS-410 — refuse a `personium-` tag that is not STRICTLY NEWER than the
newest already-published Personium engine release.

WHAT THIS EXISTS TO STOP
────────────────────────
The APPLICATION release has a tag↔version preflight (`release.yml`, job
`preflight`): the tag must equal `APP_VERSION`, and a mismatch fails the run
before a single OS spends build time. Its own comment names the cost it
prevents — a manifest version the installed code disagrees with, i.e. a
perpetual update loop.

The ENGINE release had no counterpart, and its failure mode is WORSE because it
is SILENT. The update offer is a strict compare:

    src/ui/app.py   ->  engine.is_newer(self._engine_latest, engine.current_version())

so an engine published under a version string users already carry simply never
fires the offer. Nothing errors, nothing is logged, the release page looks
perfect, and every existing install keeps the old engine forever. The app's
mismatch is caught before a byte is built; the engine's is discovered weeks
later when someone asks why nobody's fingerprint improved.

THE GATE IS ONE COMPARISON, AND IT IS THE CLIENT'S OWN
──────────────────────────────────────────────────────
`updater.is_newer` / `updater.parse_version` — the SAME comparison
`_engine_update_available` makes at UPDATE time, run here at PUBLISH time.
Deliberately not a re-implementation: `parse_version` keeps every numeric chunk
with no arity cap, which is what makes a five-component revision
(`152.0.7977.75.1`) sort correctly above its four-component predecessor. A
second comparator is a second place for the two to drift apart, and the drift
would be invisible on exactly the release it mattered for.

THREE EXIT STATUSES, AND THE THIRD IS NOT A PASS
────────────────────────────────────────────────
    0   the tag is strictly newer than the newest published engine release
    1   REFUSED — it is not, and publishing it would reach nobody
    2   the published set COULD NOT BE READ (GitHub unreachable, a refs
        document that did not come back as a list, a release document that
        answered something other than 200/404, or the probe bound reached
        with no published release found). Nothing was measured.

Exit 2 FAILS the workflow, deliberately. A gate that waves a tag through when
it could not look is the "green that proves nothing" this whole guard exists to
remove, and the two costs are not symmetric: a false refusal costs a re-run of a
twenty-second job, while a false pass costs a dead release nobody notices for
weeks. Same three-status vocabulary as `scripts/ps343_verify_release_provenance.py`
and `scripts/ps299_rebase_probe.py`.

USAGE
─────
    # in CI: the tag comes from GITHUB_REF_NAME
    python3 scripts/ps410_engine_release_preflight.py

    # dry-run a tag you are ABOUT to push, or re-read a refusal
    python3 scripts/ps410_engine_release_preflight.py --tag personium-152.0.7977.75.1
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.services import egress  # noqa: E402
from src.services.engine import updater  # noqa: E402

ALLOW = 0
REFUSE = 1
UNMEASURED = 2

# How many engine tags to descend past looking for one with a release behind
# it. THE CLIENT'S OWN BOUND, imported rather than re-chosen: a baseline taken
# over a different depth than `fetch_latest_full` uses is a baseline the client
# would not agree with, which is the one thing this gate must not be.
MAX_TAG_PROBES = updater._MAX_TAG_PROBES


class Unmeasured(Exception):
    """The published set could not be read. NOT "there is nothing published"."""


def verdict(candidate: str, newest_published: str) -> tuple[int, str]:
    """The whole decision, with no network in it.

    `candidate` and `newest_published` are BARE versions (no `personium-`
    prefix); `newest_published` is "" when the repository has published no
    engine release yet, which is an ALLOW — there is nothing for the tag to
    fail to advance past, and `is_newer` already answers that correctly.

    Returns (exit_code, message). The refusal message NAMES THE REMEDY, on the
    app preflight's model: an operator should not have to work out what to bump.
    """
    if not candidate:
        return (
            UNMEASURED,
            "no tag to check — GITHUB_REF_NAME is empty and no --tag was given",
        )

    if not updater.is_engine_tag(updater.engine_tag(candidate)):
        # Unreachable through engine_tag() today: main() hands this the output
        # of version_from_tag(), and engine_tag() prefixes ANYTHING, so a
        # `v3.1.1` becomes `personium-v3.1.1`, passes is_engine_tag, parses to
        # () and lands on REFUSE with a Chromium-bump remedy instead. That is
        # the right VERDICT for a tag this gate cannot judge, wearing a
        # misleading message; harmless at a `personium-*`-triggered workflow.
        # Kept as a named refusal rather than an assert so a future caller that
        # does reach it gets an answer instead of a traceback.
        return (
            UNMEASURED,
            f"{candidate!r} is not an engine version — this gate judges "
            f"{updater.ENGINE_TAG_PREFIX}* tags only",
        )

    if updater.is_newer(candidate, newest_published):
        if not newest_published:
            return (
                ALLOW,
                f"{updater.engine_tag(candidate)} is the FIRST published engine "
                "release in this repository — nothing to advance past",
            )
        return (
            ALLOW,
            f"{updater.engine_tag(candidate)} is strictly newer than the newest "
            f"published engine release {updater.engine_tag(newest_published)}",
        )

    return (
        REFUSE,
        f"tag {updater.engine_tag(candidate)} is not newer than the published "
        f"engine release {updater.engine_tag(newest_published)} — the update "
        "offer is a strict compare (engine.is_newer), so publishing this would "
        "reach NOBODY: every installed persona keeps the old engine and nothing "
        "reports it. Bump the Chromium version, or append a revision component "
        f"(e.g. {newest_published}.1), retag, and publish that.",
    )


def _published_engine_versions(timeout: int = 20) -> list[str]:
    """Every `personium-` TAG in the repository, newest version first.

    NOT `updater.engine_versions_newest_first()`, and the reason is the whole
    point of this gate rather than a preference: that function swallows every
    failure into `[]`, because for the CLIENT "no engine release" and "GitHub is
    unreachable" both correctly mean "do not offer an update". At a GATE those
    two are opposites — the first is an ALLOW (this is the first release ever)
    and the second must never be one. So the fetch is made here where the
    exception survives, and every judgement about the document is still made by
    the module's own predicates.
    """
    try:
        refs = egress.fetch_json(updater.ENGINE_TAG_REFS_API, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - re-raised as a named refusal
        raise Unmeasured(f"could not list engine tags: {exc}") from exc

    # ⚠️ A MALFORMED SUCCESS IS NOT AN EMPTY LIST. The client writes
    # `refs if isinstance(refs, list) else []` here and is RIGHT to: its `[]`
    # means "do not offer an update", the safe direction. At this gate `[]`
    # becomes "" and "" is an ALLOW, so silently discarding a non-list document
    # would turn the gate into the thing it exists to prevent — and this is
    # reachable, not theoretical: api.github.com answers some rate-limit
    # refusals as a 200-shaped JSON OBJECT, and the proxied branch of
    # `egress.fetch_json` explicitly admits `dict | list`. Fixing the fetch's
    # EXCEPTION without fixing its malformed SUCCESS left the inversion one
    # line further down. Named non-measurement, on `ps342_chromium_watch`'s
    # model ("the tag list did not come back as a list").
    if not isinstance(refs, list):
        raise Unmeasured(
            "the engine tag list did not come back as a list "
            f"(got {type(refs).__name__}) — nothing was measured"
        )

    versions = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        name = ref.get("ref", "") or ""
        prefix = "refs/tags/"
        if not name.startswith(prefix):
            continue
        tag = name[len(prefix):]
        if not updater.is_engine_tag(tag):
            continue
        versions.append(updater.version_from_tag(tag))
    versions.sort(key=updater.parse_version, reverse=True)
    return versions


def _is_not_found(exc: BaseException) -> bool:
    """Is this exception GitHub saying "no release behind that tag"?

    TWO SHAPES, because `egress.fetch_json` has two branches and they do not
    raise alike: the DIRECT branch is `urlopen`, which raises `HTTPError` with
    a `.code`; the PROXIED branch hands off to `proxy_checker`, which turns a
    non-200 into `RuntimeError("request failed: HTTP 404")` with no code
    attribute at all. Recognising only the first would make this gate refuse
    every tag on a proxied host — the case where it least deserves to be
    believed is the case where an operator's own egress policy is in force.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 404
    return isinstance(exc, RuntimeError) and "HTTP 404" in str(exc)


def _is_published_release(version: str, timeout: int = 20) -> bool:
    """Does this engine TAG have a published release behind it?

    A tag can exist with nothing published against it — pushed minutes before
    the release is cut (which is exactly the state this gate runs in, for the
    candidate's own tag), a release still in DRAFT, a release deleted while its
    tag stayed. The client descends past those; so must the baseline, or the
    gate would compare against a version no user can ever be offered.

    404 is the honest "no release behind this tag" and is NOT a failure. Every
    OTHER transport error IS: a transient 500 on the newest tag would otherwise
    silently lower the baseline to an older release and make this gate MORE
    permissive precisely when it is least able to see.
    """
    url = updater.RELEASE_BY_TAG_API.format(tag=updater.engine_tag(version))
    try:
        data = egress.fetch_json(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - re-raised as a named refusal
        if _is_not_found(exc):
            return False
        raise Unmeasured(f"release lookup for {version} failed: {exc}") from exc

    if not isinstance(data, dict):
        raise Unmeasured(f"release lookup for {version} returned a non-document")
    if data.get("draft"):
        # A draft is not published work. The unauthenticated by-tag endpoint
        # answers 404 for one anyway, so this is defence in depth — the same
        # reasoning `updater._release_asset` states for its own draft check.
        return False
    # The endpoint is by-tag, so this can only disagree with what we asked for
    # if GitHub served something else entirely. Judge it with the module's own
    # predicate rather than trusting the URL we built.
    return updater.is_engine_tag(data.get("tag_name", "") or "")


def newest_published_release(timeout: int = 20) -> str:
    """The newest engine version that has a PUBLISHED RELEASE behind it.

    ⚠️ THE CANDIDATE'S OWN TAG IS **NOT** EXCLUDED, AND THAT IS THE WHOLE GATE.
    This gate runs ON the tag push, so `personium-<candidate>` is already in the
    ref list by the time it can be read, and the obvious move — drop any ref
    equal to the candidate — was written first and MEASURED WRONG: re-publishing
    the already-live `152.0.7977.75` then reported "(none) — the FIRST published
    engine release" and PASSED, which is byte-for-byte the failure this ticket
    exists to refuse.

    The discriminator is not whether the TAG exists, it is whether a RELEASE is
    already published behind it, and that separates the two cases on its own:

      * a fresh tag push has no release behind it yet (`_is_published_release`
        answers False, the honest 404), so it is skipped and the baseline is
        the real predecessor;
      * a version that is ALREADY PUBLISHED answers True, becomes its own
        baseline, and `is_newer(v, v)` is False — REFUSED, which is correct,
        because every installed persona already carries that version and the
        strict compare in the update offer would never fire.

    Returns "" only when EVERY engine tag in the repository was inspected and
    none carried a published release — the honest answer for the very first
    engine release. Raises `Unmeasured` when the question could not be asked,
    AND when the probe bound was reached without an answer: see below.
    """
    versions = _published_engine_versions(timeout)
    for version in versions[:MAX_TAG_PROBES]:
        if _is_published_release(version, timeout=timeout):
            return version

    # ⚠️ A BOUND THAT WAS REACHED IS AN UNMEASURED ANSWER, NOT A NEGATIVE ONE.
    # `MAX_TAG_PROBES` is the client's own and borrowing it is right for
    # AGREEMENT — but the bound means different things in the two places. For
    # the client, stopping at five degrades to "no update offered", which is
    # safe. Here `""` is an ALLOW, so stopping early would report "the first
    # published engine release" for a repository that has published dozens.
    # The trigger state is the one RELEASING.md already warns about — a run of
    # engine tags left without releases behind them — and FOUR stranded tags
    # suffice in practice, because the candidate's own fresh tag spends a probe
    # too. Two facts, kept apart: everything was looked at and nothing was
    # found (a measured negative, ALLOW) versus I stopped looking (unmeasured).
    if len(versions) > MAX_TAG_PROBES:
        raise Unmeasured(
            f"none of the {MAX_TAG_PROBES} newest engine tags has a published "
            f"release behind it, and {len(versions) - MAX_TAG_PROBES} older "
            "tag(s) were never looked at — the newest published release could "
            "not be established. Cut or delete the stranded tags (RELEASING.md)"
        )
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tag",
        default=os.environ.get("GITHUB_REF_NAME", ""),
        help="the tag to judge (default: $GITHUB_REF_NAME)",
    )
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)

    candidate = updater.version_from_tag((args.tag or "").strip())
    if not candidate:
        print("::error::PS-410 preflight: no tag to check (GITHUB_REF_NAME empty)")
        return UNMEASURED

    try:
        baseline = newest_published_release(timeout=args.timeout)
    except Unmeasured as exc:
        print(
            "::error::PS-410 preflight: the published engine releases could NOT "
            f"be read, so this tag was NOT checked — {exc}. This is not a pass; "
            "re-run the job."
        )
        return UNMEASURED

    code, message = verdict(candidate, baseline)
    print(f"candidate={updater.engine_tag(candidate)}  "
          f"newest_published={updater.engine_tag(baseline) if baseline else '(none)'}")
    if code == ALLOW:
        print(f"PS-410 preflight OK: {message}")
    else:
        print(f"::error::{message}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
