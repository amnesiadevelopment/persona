#!/usr/bin/env python3
"""PS-372 — watch Mozilla for a Firefox MAJOR past the one our engine ships, and
say what the decision would be if one has arrived.

─────────────────────────────────────────────────────────────────────────────
WHY THIS EXISTS
─────────────────────────────────────────────────────────────────────────────
We do not build the Firefox engine — feder-cr/firefox_antidetect_patch does, and
we consume its `firefox-NN` builds. Two numbering schemes are therefore in play,
and they move INDEPENDENTLY:

  * `firefox-NN`  — the PROVIDER'S BUILD TAG. Bumps on every rebuild, including
    a rebuild that changes nothing but packaging.
  * `151.0`       — the FIREFOX VERSION that build actually contains.

`engine-autoupdate.yml` already watches the first one and bumps us onto newer
builds. NOTHING watched the second. So "Mozilla shipped a new Firefox major and
our engine is now a major behind" was something that would HAPPEN to us rather
than something we chose — the same complaint PS-342 made about ungoogled-chromium
on the other engine.

⛔ THE TRAP THIS SCRIPT IS BUILT AROUND: COMPARING THE WRONG NUMBER
─────────────────────────────────────────────────────────────────────────────
Measured against the provider on 2026-09-09:

    firefox-20 (ours)  ->  Firefox 151.0
    firefox-21         ->  Firefox 151.0
    firefox-22         ->  Firefox 151.0
    …
    firefox-29         ->  Firefox 151.0

NINE build tags past ours, EVERY ONE of them still Firefox 151.0. A watcher that
compares build tags fires nine times and every one of those firings is upstream
rebuild churn the owner has explicitly ruled out. A watcher that compares the
FIREFOX MAJOR stays silent across all nine — which is the entire point of this
script and the reason `our_firefox_version()` goes to the trouble it does below
instead of reading `engine-baseline.txt` and calling it a version.

─────────────────────────────────────────────────────────────────────────────
WHERE "OUR" FIREFOX MAJOR COMES FROM — AND WHY IT IS NOT A CONSTANT
─────────────────────────────────────────────────────────────────────────────
A hardcoded `151` here would be a second place to update, and it would go stale
SILENTLY: `engine-autoupdate.yml` bumps the engine on its own schedule and would
never touch a constant in this file, so the day it moves us onto a build carrying
a newer Firefox, this watcher would keep comparing against a number nobody
maintains. That is the duplication `src/services/browser/engine_version.py` was
written to remove on the Chromium side, re-created on the Firefox side.

There is no `version.txt` equivalent for the Firefox lane — `engine-baseline.txt`
holds `firefox-20`, the BUILD TAG, and `services/engine/firefox.build_number()`
parses the `151.0` suffix off a cache-dir name and DISCARDS it. So the Firefox
version is not written down in this repository at all, and every `151.0` in the
tree is a comment.

It does not need to be written down, because the provider publishes it, keyed by
the tag we already keep current:

    engine-baseline.txt      ->  firefox-20            (maintained by
                                 scripts/engine_autobump.py, which rewrites this
                                 file on every engine bump)
    provider release firefox-20
        assets:  firefox-151.0-stealth-linux-x86_64.tar.gz   <- the version
        seal.json: {"upstream_version": "151.0", …}          <- and again

⚠️ THE BUILD TAG IS USED AS A LOOKUP KEY AND NEVER AS THE COMPARISON. Read that
sentence twice before editing `watch()`: the tag's only job is to name WHICH
provider release to ask about. The number that is compared against Mozilla is the
Firefox version that release contains.

Two sources are read for it, in this order, and BOTH are the provider's own
statement about its own build:

  1. `seal.json`'s `upstream_version` — the provider's explicit declaration.
  2. the asset names (`firefox-<VER>-stealth-<os>-<arch>.<ext>`) — the same fact
     carried in the filenames the engine package downloads.

(2) is a fallback rather than decoration: `seal.json` is a schema-2 artifact that
older releases predate, and a release can exist whose seal has not been uploaded.
When the two are BOTH present they must AGREE — a disagreement means the release
is describing itself two different ways and we refuse to pick a winner, because
guessing which one is right is exactly how a watcher starts comparing a number
nobody meant.

─────────────────────────────────────────────────────────────────────────────
WHAT IT DOES NOT DO — DELIBERATELY
─────────────────────────────────────────────────────────────────────────────
It does NOT bump `engine-baseline.txt`, does NOT bump a pin, does NOT build and
does NOT publish. A Firefox major arriving is a QUESTION for a human, and the
answer is not mechanical — see the `macos` reporting below, which exists because
the answer turns on a fact this script can look up but must not act on. PS-342
records the same rule for the Chromium lane and it is recorded here for the
Firefox one: an automatic engine move is how a masking layer and an engine
silently come to disagree.

─────────────────────────────────────────────────────────────────────────────
⭐ THE REPORT CARRIES A REASON, NOT A FACT
─────────────────────────────────────────────────────────────────────────────
"Firefox 152 exists" is not actionable on its own. When a major arrives the human
chooses between two options, and WHICH ONE is available is decided by a single
lookup this script performs:

  1. TAKE THE PROVIDER'S BUILD — available only if they shipped that major AND
     shipped it WITH macOS assets. persona ships macOS; a build without them
     cannot be the whole answer.
  2. SELF-BUILD — parked. The provider's patch source is public and buildable on
     our infrastructure plus a Mac. ⛔ It stays parked until a Firefox major
     arrives that the provider will not ship with macOS. It is named in the
     report so nobody re-derives it as a discovery.

⚠️ AND THAT TRIGGER IS NOT HYPOTHETICAL. Measured on 2026-09-09: the provider
shipped macOS assets on firefox-14 through firefox-20 and has shipped NONE since
— firefox-21 through firefox-29 are linux+win only. So the macOS answer is
reported as its own four-valued reading (`with_macos` / `without_macos` /
`not_shipped` / `unknown`) rather than a boolean, because "they have not shipped
this major at all" and "they shipped it without macOS" are different news with
different next steps, and "we could not tell" must never wear either's colour.

─────────────────────────────────────────────────────────────────────────────
STATUSES AND THIS SCRIPT'S OWN EXIT CODE
─────────────────────────────────────────────────────────────────────────────
    status               exit  meaning
    ───────────────────  ────  ───────────────────────────────────────────────
    up_to_date             0   Mozilla's latest major is not past ours. NO NEWS
                               — and this is the status upstream REBUILD CHURN
                               lands on, which is the whole ticket.
    newer_major            1   A Firefox major past ours exists. News, and a
                               decision. NOT a defect and NOT an instruction.
    upstream_unreadable    2   Mozilla's product-details endpoint did not
                               answer, or did not answer with a version. WE DO
                               NOT KNOW.
    provider_unreadable    2   We could not ask the provider what Firefox
                               version our own build contains, so we do not know
                               what "ours" is. Nothing was compared.
    baseline_unreadable    2   `engine-baseline.txt` is missing or does not hold
                               a `firefox-NN` tag, so we do not even know which
                               build is ours. Nothing was looked up.

⚠️ EVERY "WE DO NOT KNOW" IS A REPORTED STATUS, NEVER A RAISE — same rule PS-342
settled on, and for the same mechanical reason: an uncaught raise writes no
`$GITHUB_OUTPUT`, so the workflow's issue step is SKIPPED, the markdown is never
written, and the run is red in an Actions tab nobody is subscribed to. A watcher
that dies quietly on a network error looks IDENTICAL to one reporting good news.
The three unreadable statuses each get their OWN issue title, because the title
is the dedup key: sharing one means whichever news lands first suppresses the
other into a comment on itself.

⚠️ WHY `newer_major` EXITS 1 RATHER THAN 0. It is not a defect — but it is the
one outcome that needs a person, and a watcher whose interesting answer is green
is a watcher whose interesting answer is invisible. `up_to_date` is the only
exit-0 status, so green here means exactly one thing: nothing to decide today.

Usage:
    python3 scripts/ps372_firefox_major_watch.py
    python3 scripts/ps372_firefox_major_watch.py --latest-firefox 152.0   # falsify
    python3 scripts/ps372_firefox_major_watch.py --report-json /tmp/w.json \
        --report-md /tmp/w.md --github-output "$GITHUB_OUTPUT"
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_FILE = os.path.join(REPO_ROOT, "engine-baseline.txt")
# The repo-relative form, for the report a human reads: an absolute path is a
# runner's scratch directory and means nothing in a filed issue.
BASELINE_REL = "engine-baseline.txt"

# Mozilla's own product-details feed. `LATEST_FIREFOX_VERSION` is the current
# RELEASE channel version — deliberately not `LATEST_FIREFOX_DEVEL_VERSION`
# (beta) or `FIREFOX_NIGHTLY`, both of which sit a major or two ahead and would
# make this watcher fire on a major nobody has released yet.
MOZILLA_VERSIONS_URL = "https://product-details.mozilla.org/1.0/firefox_versions.json"
MOZILLA_KEY = "LATEST_FIREFOX_VERSION"

# The provider whose builds we consume. Its releases are the ONLY place that says
# which Firefox version a given `firefox-NN` build contains.
PROVIDER_REPO = "feder-cr/firefox_antidetect_patch"
PROVIDER_RELEASE_API = "https://api.github.com/repos/%s/releases/tags/%%s" % PROVIDER_REPO
PROVIDER_RELEASES_API = "https://api.github.com/repos/%s/releases?per_page=100" % PROVIDER_REPO

# `firefox-20`, optionally with the engine package's cache-dir suffix
# (`firefox-20_151.0_20260817150018`). Deliberately the same shape
# `src/services/engine/firefox._TAG_RE` accepts, so the watcher and the engine
# code cannot disagree about what a build tag looks like.
BUILD_TAG_RE = re.compile(r"^firefox-(\d+)(?:[_.].*)?$")

# `firefox-151.0-stealth-linux-x86_64.tar.gz` -> `151.0`. The version segment is
# dotted-numeric only, so `firefox-151.0b1-stealth-…` does not match — a beta
# asset must not be read as a release version.
ASSET_VERSION_RE = re.compile(r"^firefox-(\d+(?:\.\d+)*)-stealth-")

# A Firefox version as Mozilla states it: `151.0`, `155.0.1`. Anchored, so a
# beta/nightly string (`156.0b4`, `157.0a1`) is REFUSED rather than silently
# truncated to its major — reading `156.0b4` as major 156 would fire this watcher
# on a beta.
FIREFOX_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")

# ── the outcomes ─────────────────────────────────────────────────────────────
UP_TO_DATE = "up_to_date"
NEWER_MAJOR = "newer_major"
UPSTREAM_UNREADABLE = "upstream_unreadable"
PROVIDER_UNREADABLE = "provider_unreadable"
BASELINE_UNREADABLE = "baseline_unreadable"

# ── how the provider treats macOS for a given major ──────────────────────────
# Four values, not a boolean, because "no build exists" and "a build exists
# without macOS" are different news, and "we could not tell" must wear neither's
# colour. See `macos_state()`.
MACOS_WITH = "with_macos"
MACOS_WITHOUT = "without_macos"
MACOS_NOT_SHIPPED = "not_shipped"
MACOS_UNKNOWN = "unknown"

# Which statuses mean "we looked, and there is nothing to decide". Note what is
# NOT here: every unreadable status, and `newer_major` itself. This frozenset is
# the single place that question is answered and the tests assert on it directly.
GREEN_STATUSES = frozenset({UP_TO_DATE})

EXIT_FOR_STATUS = {
    UP_TO_DATE: 0,
    NEWER_MAJOR: 1,
    UPSTREAM_UNREADABLE: 2,
    PROVIDER_UNREADABLE: 2,
    BASELINE_UNREADABLE: 2,
}


def is_green(status):
    """Only a status that rests on an actual comparison AND found no news."""
    return status in GREEN_STATUSES


def exit_code_for(status):
    return EXIT_FOR_STATUS[status]


# ── version arithmetic ───────────────────────────────────────────────────────


def parse_firefox_version(value):
    """`155.0.1` -> (155, 0, 1); None when it is not a release version.

    ⚠️ REFUSES a beta/nightly string. `156.0b4` and `157.0a1` are real values in
    Mozilla's feed under OTHER keys, and a lenient parse that pulled the leading
    integer out of them would let a beta fire this watcher. Anything that is not
    dotted-numeric is not a version we compare.
    """
    text = (value or "").strip()
    if not FIREFOX_VERSION_RE.match(text):
        return None
    return tuple(int(part) for part in text.split("."))


def firefox_major(value):
    """The MAJOR of a Firefox version — the only component this watcher compares.

    ⭐ This is the whole discrimination the ticket turns on. `151.0` and `151.0.1`
    have the same major, so a Mozilla point release does not fire the watcher any
    more than a provider rebuild does; only `151 -> 152` does.
    """
    parsed = parse_firefox_version(value)
    return parsed[0] if parsed else None


def build_number(tag):
    """`firefox-20` -> 20; `firefox-20_151.0_2026…` -> 20; otherwise None.

    Deliberately the same parse `src/services/engine/firefox.build_number()`
    performs, and used for the same purpose it is used for there: naming a build.
    ⛔ The number it returns is NEVER compared against a Firefox version. It is a
    lookup key, and `tests/test_ps372_firefox_major_watch.py` asserts that the
    two numbering schemes stay apart.
    """
    m = BUILD_TAG_RE.match((tag or "").strip())
    return int(m.group(1)) if m else None


def canonical_build_tag(tag):
    """`firefox-20_151.0_2026…` -> `firefox-20`; None when it is not a build tag.

    The suffixed form is what an INSTALLED engine's cache directory is named, and
    the provider's release is tagged with the bare form — so a suffixed value has
    to be canonicalised before it can be looked up.
    """
    num = build_number(tag)
    return None if num is None else "firefox-%d" % num


# ── reading the two sides ────────────────────────────────────────────────────


def read_baseline_tag(path=None):
    """The build tag we ship, from the file the engine autobump keeps current.

    ⚠️ `path` defaults to None and resolves `BASELINE_FILE` INSIDE the call, not
    in the signature. A default argument is bound once at import, so
    `path=BASELINE_FILE` would freeze the module-level value and make the
    location unoverridable afterwards — which is not a style point: the
    baseline-unreadable tests redirect this at a temp file, and against a frozen
    default they silently fell through to the REAL file and made a live network
    call, passing for the wrong reason on a machine with a network and failing
    on one without.

    Raises rather than defaulting: there is no sensible fallback for "which build
    is ours", and a guessed one would make every comparison below meaningless
    while looking like it worked. `main()` catches this and reports it as
    `baseline_unreadable` — a REPORT, never a traceback.
    """
    with open(path or BASELINE_FILE, encoding="utf-8") as fh:
        value = fh.read().strip()
    tag = canonical_build_tag(value)
    if tag is None:
        raise ValueError("%s does not hold a firefox-NN build tag: %r"
                         % (path or BASELINE_FILE, value))
    return tag


def _fetch_json(url, token=None, timeout=30, opener=None):
    """GET + parse. Returns (payload, error); NEVER raises, NEVER defaults.

    An automation that cannot ask its question must SAY SO, because "no news" and
    "we could not look" are the same silence from outside and completely
    different facts.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "persona-ps372-firefox-major-watch",
        },
    )
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    fetch = opener or urllib.request.urlopen
    try:
        with fetch(req, timeout=timeout) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s %s from %s" % (e.code, e.reason, url)
    except Exception as e:  # URLError, timeout, malformed JSON, …
        return None, "%s: %s — %s" % (type(e).__name__, e, url)


def fetch_mozilla_latest(token=None, timeout=30, opener=None):
    """Mozilla's current RELEASE-channel Firefox version. Returns (version, error)."""
    payload, err = _fetch_json(MOZILLA_VERSIONS_URL, timeout=timeout, opener=opener)
    if err:
        return None, err
    if not isinstance(payload, dict):
        return None, "the Mozilla product-details feed did not come back as an object"
    value = payload.get(MOZILLA_KEY)
    if not value:
        return None, "the feed carried no %s" % MOZILLA_KEY
    if parse_firefox_version(value) is None:
        # Not a lenient parse: a value we cannot read is reported, never guessed
        # at. See parse_firefox_version's docstring for why a leading-integer
        # fallback would be actively harmful here.
        return None, "%s is not a release version: %r" % (MOZILLA_KEY, value)
    return str(value).strip(), None


def version_from_assets(asset_names):
    """The Firefox version an asset list declares, or None.

    Every stealth asset in a release carries the same version, so a release that
    somehow carried two would be describing itself two ways — refused rather than
    resolved, for the same reason `firefox_version_of_release` refuses a
    seal/asset disagreement.
    """
    found = set()
    for name in asset_names or []:
        m = ASSET_VERSION_RE.match(name or "")
        if m:
            found.add(m.group(1))
    if len(found) != 1:
        return None
    return found.pop()


def has_macos_asset(asset_names):
    """True when the release ships a macOS stealth archive.

    persona ships macOS, so a provider build without one cannot be the whole
    answer to "can we just take their build?".
    """
    return any("-stealth-macos-" in (name or "") for name in asset_names or [])


def firefox_version_of_release(release):
    """Which Firefox version a provider release contains. Returns (version, error).

    TWO independent statements by the provider about its own build are read, and
    they must not disagree:

      * `seal.json`'s `upstream_version`, when the release carries a seal whose
        contents we have (the workflow does not download assets, so in practice
        this arrives only when a caller supplies it — see `seal` below).
      * the asset filenames, `firefox-<VER>-stealth-<os>-<arch>.<ext>`.

    ⚠️ A DISAGREEMENT IS AN ERROR, NOT A TIE TO BREAK. Picking a winner between
    two sources that contradict each other is how a watcher starts comparing a
    number nobody meant; the honest answer is that this release describes itself
    two different ways and we do not know which is true.
    """
    if not isinstance(release, dict):
        return None, "the provider release did not come back as an object"
    names = [a.get("name", "") for a in release.get("assets", []) if isinstance(a, dict)]
    from_assets = version_from_assets(names)
    seal = release.get("_seal") if isinstance(release.get("_seal"), dict) else None
    from_seal = None
    if seal:
        candidate = seal.get("upstream_version")
        if candidate and parse_firefox_version(candidate) is not None:
            from_seal = str(candidate).strip()

    if from_seal and from_assets and from_seal != from_assets:
        return None, (
            "the release describes itself two ways: seal.json says %r and its "
            "assets say %r" % (from_seal, from_assets)
        )
    version = from_seal or from_assets
    if version is None:
        return None, (
            "no Firefox version could be read from the release: it carries "
            "neither a usable seal.json nor a firefox-<VER>-stealth-… asset"
        )
    return version, None


def fetch_provider_release(tag, token=None, timeout=30, opener=None):
    """The provider release for one build tag. Returns (release, error)."""
    payload, err = _fetch_json(PROVIDER_RELEASE_API % tag, token=token,
                               timeout=timeout, opener=opener)
    if err:
        return None, err
    if not isinstance(payload, dict) or not payload.get("tag_name"):
        return None, "the provider release for %s did not come back as a release" % tag
    return payload, None


def fetch_provider_releases(token=None, timeout=30, opener=None):
    """Every provider release. Returns (list, error).

    Used ONLY to answer "has the provider shipped this Firefox major, and with
    macOS?" — a question about the DECISION, never about what our own build is.
    """
    payload, err = _fetch_json(PROVIDER_RELEASES_API, token=token,
                               timeout=timeout, opener=opener)
    if err:
        return None, err
    if not isinstance(payload, list):
        return None, "the provider release list did not come back as a list"
    return payload, None


def macos_state(releases, major):
    """How the provider treats macOS for Firefox `major`. Returns (state, tags).

    ⚠️ FOUR VALUES, NOT A BOOLEAN, and the fourth is the important one:

      with_macos    they shipped this major AND at least one such build carries
                    macOS assets  ->  option 1 is live
      without_macos they shipped this major and NOT ONE of those builds carries
                    macOS  ->  option 1 is not the whole answer; the parked
                    self-build's trigger has fired
      not_shipped   they have not shipped this major at all  ->  there is
                    nothing to take yet; the question is when, not whether
      unknown       we could not ask  ->  reported as such, never as any of the
                    three above

    `tags` names the builds the verdict rests on, so the report can point at
    something a human can open rather than asserting a conclusion.

    Measured 2026-09-09: for major 151 this returns `with_macos` (firefox-18/19/20
    ship macOS) even though firefox-21..29 do not — because ONE build carrying it
    is enough to make "take their build" available. The `without_macos` reading is
    reserved for a major where NOTHING carries it.
    """
    if releases is None:
        return MACOS_UNKNOWN, []
    matching = []
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = canonical_build_tag(rel.get("tag_name", ""))
        if tag is None:
            continue
        names = [a.get("name", "") for a in rel.get("assets", []) if isinstance(a, dict)]
        version = version_from_assets(names)
        if firefox_major(version) != major:
            continue
        matching.append((tag, has_macos_asset(names)))
    if not matching:
        return MACOS_NOT_SHIPPED, []
    with_mac = [tag for tag, mac in matching if mac]
    if with_mac:
        return MACOS_WITH, sorted(with_mac)
    return MACOS_WITHOUT, sorted(tag for tag, _ in matching)


# ── the decision ─────────────────────────────────────────────────────────────


def watch(baseline_tag, token=None, forced_latest=None, forced_our_version=None,
          timeout=30, opener=None, now=None):
    """The whole decision, as a pure-ish function returning a result dict.

    ⭐ READ THE ORDER OF THE TWO READS. `our_firefox_version` is resolved from the
    PROVIDER, keyed by `baseline_tag`; `latest_firefox` comes from MOZILLA. The
    comparison at the bottom is between two FIREFOX VERSIONS. `baseline_tag` — a
    build tag — appears in the report for provenance and is compared against
    nothing.
    """
    result = {
        "baseline_tag": baseline_tag,
        "our_firefox_version": None,
        "our_major": None,
        "latest_firefox": None,
        "latest_major": None,
        "status": None,
        "macos": MACOS_UNKNOWN,
        "macos_tags": [],
        "error": None,
        "measured_at": now or _utcnow(),
        "forced": forced_latest is not None,
    }

    # ── our side ─────────────────────────────────────────────────────────────
    if forced_our_version is not None:
        our_version, err = forced_our_version, None
        if parse_firefox_version(our_version) is None:
            our_version, err = None, (
                "--our-firefox %r is not a Firefox release version" % forced_our_version
            )
    else:
        release, err = fetch_provider_release(baseline_tag, token=token,
                                              timeout=timeout, opener=opener)
        our_version = None
        if release is not None:
            our_version, err = firefox_version_of_release(release)
    if our_version is None:
        # We do not know what OUR OWN engine contains, so there is nothing to
        # compare Mozilla against. Reported, not raised.
        result["status"] = PROVIDER_UNREADABLE
        result["error"] = err
        return result
    result["our_firefox_version"] = our_version
    result["our_major"] = firefox_major(our_version)

    # ── Mozilla's side ───────────────────────────────────────────────────────
    if forced_latest is not None:
        if parse_firefox_version(forced_latest) is None:
            result["status"] = UPSTREAM_UNREADABLE
            result["error"] = (
                "--latest-firefox %r is not a Firefox release version; expected "
                "the dotted-numeric form, e.g. 152.0. Nothing was compared."
                % forced_latest
            )
            return result
        latest = str(forced_latest).strip()
    else:
        latest, err = fetch_mozilla_latest(timeout=timeout, opener=opener)
        if latest is None:
            result["status"] = UPSTREAM_UNREADABLE
            result["error"] = err
            return result
    result["latest_firefox"] = latest
    result["latest_major"] = firefox_major(latest)

    # ── ⭐ THE COMPARISON: MAJOR AGAINST MAJOR, AND NOTHING ELSE ─────────────
    #
    # Strictly greater. A Mozilla POINT release (151.0 -> 151.0.1) has the same
    # major and does not fire, exactly as a provider REBUILD (firefox-20 ->
    # firefox-29 at an unchanged 151.0) does not fire.
    if result["latest_major"] <= result["our_major"]:
        result["status"] = UP_TO_DATE
        return result

    result["status"] = NEWER_MAJOR

    # ── the reason, not just the fact ────────────────────────────────────────
    #
    # Only asked when there IS a decision to make. A failure here is NOT promoted
    # to an unreadable status: we have already established the news, and losing
    # it because a secondary lookup failed would be worse than reporting the news
    # with `macos: unknown` beside it.
    releases, _rel_err = fetch_provider_releases(token=token, timeout=timeout,
                                                 opener=opener)
    state, tags = macos_state(releases, result["latest_major"])
    result["macos"] = state
    result["macos_tags"] = tags
    return result


def _utcnow():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def baseline_unreadable_result(error, now=None):
    """The result for "we cannot tell which build is ours".

    Built rather than raised, for the reason PS-342's own
    `baseline_unreadable_result` records: the REPORT is this script's
    deliverable, and a traceback writes no step outputs, files no issue and
    uploads no artifact — the run would be red and SILENT, which is the failure
    mode this watcher exists to end. This path is reached by EVERY SCHEDULED RUN
    (the CLI overrides are only reachable by a hand dispatch), which is why
    leaving it raising would be the wrong asymmetry.

    ⚠️ The file CONTENTS reach `error` (via `read_baseline_tag`'s `%r`) and
    `error` reaches only the issue BODY, written with `--body-file`. It never
    reaches `title=`, which is a bare `key=value` line in `$GITHUB_OUTPUT` that
    an embedded newline would forge additional outputs through.
    """
    return {
        "baseline_tag": "<unreadable>",
        "our_firefox_version": None,
        "our_major": None,
        "latest_firefox": None,
        "latest_major": None,
        "status": BASELINE_UNREADABLE,
        "macos": MACOS_UNKNOWN,
        "macos_tags": [],
        "error": error,
        "measured_at": now or _utcnow(),
        "forced": False,
    }


# ── the report a human actually receives ─────────────────────────────────────

HEADLINE = {
    UP_TO_DATE: "No Firefox major past ours — nothing to decide",
    NEWER_MAJOR: "A NEWER FIREFOX MAJOR exists than the one our engine ships",
    UPSTREAM_UNREADABLE: "COULD NOT ASK MOZILLA what the current Firefox is",
    PROVIDER_UNREADABLE: "COULD NOT ESTABLISH WHICH FIREFOX OUR OWN ENGINE SHIPS",
    BASELINE_UNREADABLE: "WE CANNOT READ OUR OWN BASELINE — nothing was looked up",
}


def issue_title(result):
    """A stable title, so re-running does not file the same news twice.

    ⚠️ THIS TITLE IS THE DEDUP KEY. The workflow's issue step matches an OPEN
    issue by EXACT title and comments on it instead of filing a new one — so two
    unrelated causes sharing a title means the second is SUPPRESSED into a
    comment on the first. Each unreadable cause therefore owns its own line: a
    corrupt `engine-baseline.txt`, a Mozilla outage and a GitHub outage are three
    different remedies, and a day where two of them happen must not lose one.

    The majors are in the `newer_major` title so that 151->152 and a later
    151->153 are different records, while the SAME news re-checked daily is one.
    ⛔ The macOS reading is deliberately NOT in the title: it can legitimately
    change from `not_shipped` to `with_macos` while the news stays the same, and
    a title that moved with it would file the identical decision twice.
    """
    status = result["status"]
    if status == UPSTREAM_UNREADABLE:
        return "[firefox-watch] could not read Mozilla's current Firefox version"
    if status == PROVIDER_UNREADABLE:
        return "[firefox-watch] could not establish which Firefox our engine ships"
    if status == BASELINE_UNREADABLE:
        return "[firefox-watch] cannot read our own baseline (engine-baseline.txt)"
    return "[firefox-watch] Firefox %s vs our %s — %s" % (
        result.get("latest_major") if result.get("latest_major") is not None else "?",
        result.get("our_major") if result.get("our_major") is not None else "?",
        status,
    )


def headline(result):
    return HEADLINE[result["status"]]


MACOS_SENTENCE = {
    MACOS_WITH: (
        "**The provider has shipped this major WITH macOS assets.** Option 1 is "
        "live: their build can be taken as-is."
    ),
    MACOS_WITHOUT: (
        "⚠️ **The provider has shipped this major, but NOT ONE of those builds "
        "carries macOS assets.** persona ships macOS, so their build is not the "
        "whole answer — this is the trigger the parked self-build option waits on."
    ),
    MACOS_NOT_SHIPPED: (
        "**The provider has not shipped this Firefox major at all yet.** There is "
        "nothing to take today; the open question is when they will, and whether "
        "macOS comes with it."
    ),
    MACOS_UNKNOWN: (
        "⛔ **We could not establish what the provider has shipped for this "
        "major.** This is NOT \"they shipped nothing\" — the release list could "
        "not be read. Check by hand before concluding anything about the options "
        "below."
    ),
}


def render_report(result):
    """Markdown for the issue body / job summary."""
    status = result["status"]
    lines = []
    lines.append("## %s" % headline(result))
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append("| our engine build | `%s` |" % result["baseline_tag"])
    lines.append("| the Firefox it ships | `%s` |"
                 % (result.get("our_firefox_version") or "—"))
    lines.append("| Mozilla's current release | `%s` |"
                 % (result.get("latest_firefox") or "—"))
    lines.append("| verdict | **%s** |" % status)
    lines.append("| measured at | %s |" % result.get("measured_at", "—"))
    lines.append("")

    if status == UP_TO_DATE:
        lines.append(
            "Mozilla's current release is Firefox `%s` (major `%s`) and our engine "
            "build `%s` ships Firefox `%s` (major `%s`). **Nothing to decide today.**"
            % (result["latest_firefox"], result["latest_major"],
               result["baseline_tag"], result["our_firefox_version"],
               result["our_major"])
        )
        lines.append("")
        lines.append(
            "Note this status is what upstream REBUILD CHURN lands on, and that is "
            "the point of this watcher: the provider ships fresher `firefox-NN` "
            "builds of the same Firefox version regularly, and none of them is "
            "news. Only a Mozilla MAJOR past ours is."
        )
    elif status == NEWER_MAJOR:
        lines.append(
            "Mozilla's current release is Firefox **%s** (major **%s**). Our engine "
            "build `%s` ships Firefox **%s** (major **%s**), so we are **%d major%s "
            "behind**."
            % (result["latest_firefox"], result["latest_major"],
               result["baseline_tag"], result["our_firefox_version"],
               result["our_major"],
               result["latest_major"] - result["our_major"],
               "" if result["latest_major"] - result["our_major"] == 1 else "s")
        )
        lines.append("")
        lines.append(
            "**This is news and a decision, not a defect and not an instruction to "
            "bump.** This job watches and reports; it never bumps a pin, builds, or "
            "publishes."
        )
        lines.append("")
        lines.append("### What the provider has done with this major")
        lines.append("")
        lines.append(MACOS_SENTENCE[result["macos"]])
        if result.get("macos_tags"):
            lines.append("")
            lines.append("Builds this rests on: %s"
                         % ", ".join("`%s`" % t for t in result["macos_tags"]))
        lines.append("")
        lines.append("### The two options")
        lines.append("")
        lines.append(
            "1. **Take the provider's build.** Available when they have shipped "
            "this major **with macOS assets** — persona ships macOS, so a build "
            "without them is not the whole answer. The reading above is exactly "
            "this question."
        )
        lines.append(
            "2. **Self-build.** ⛔ **PARKED.** The provider's patch source is "
            "public and buildable on our infrastructure plus a Mac. It stays "
            "parked until a Firefox major arrives that the provider will not ship "
            "with macOS — it is named here so nobody re-derives it as a "
            "discovery. A `%s` reading above is that trigger firing."
            % MACOS_WITHOUT
        )
    elif status == UPSTREAM_UNREADABLE:
        lines.append(
            "⛔ **NOTHING WAS COMPARED.** Mozilla's product-details feed could not "
            "be read, so we do not know what the current Firefox is: `%s`"
            % result.get("error")
        )
        lines.append("")
        lines.append(
            "\"No newer major\" and \"we could not look\" are the same silence from "
            "outside and completely different facts. This run is the second one. "
            "Re-run by hand — `python3 scripts/ps372_firefox_major_watch.py` — or "
            "read %s in a browser." % MOZILLA_VERSIONS_URL
        )
    elif status == PROVIDER_UNREADABLE:
        lines.append(
            "⛔ **NOTHING WAS COMPARED — and the missing half is OURS, not "
            "Mozilla's.** We could not establish which Firefox version our own "
            "engine build `%s` contains: `%s`"
            % (result["baseline_tag"], result.get("error"))
        )
        lines.append("")
        lines.append(
            "The Firefox version is not written down in this repository — "
            "`%s` holds the provider's BUILD TAG, and that tag has to be resolved "
            "against the provider's release (its `seal.json` `upstream_version`, "
            "or its `firefox-<VER>-stealth-…` asset names) to get a Firefox "
            "version. That lookup is what failed. Usual causes: the GitHub "
            "releases API did not answer, or the release for `%s` has no assets "
            "and no seal."
            % (BASELINE_REL, result["baseline_tag"])
        )
        lines.append("")
        lines.append(
            "⚠️ It is reported rather than assumed away on purpose. Substituting "
            "a default for \"which Firefox do we ship\" would make every "
            "comparison this watcher performs meaningless while it kept looking "
            "like it worked."
        )
    elif status == BASELINE_UNREADABLE:
        lines.append(
            "⛔ **NOTHING WAS LOOKED UP.** The watcher could not read which engine "
            "build is ours, so there was nothing to resolve a Firefox version "
            "for: `%s`" % result.get("error")
        )
        lines.append("")
        lines.append(
            "**This is not an upstream problem.** Neither Mozilla nor the provider "
            "was contacted. `%s` is a file in this repository and it is missing, "
            "unreadable, or does not hold a `firefox-NN` tag. It is written by "
            "`scripts/engine_autobump.py`, so the usual causes are a bad rebase "
            "deleting it or an editor writing it with a BOM or CRLF."
            % BASELINE_REL
        )
        lines.append("")
        lines.append(
            "It is reported here rather than raised because a traceback out of "
            "this script writes no step outputs, files no issue and uploads no "
            "artifact — the run would be red and SILENT, which is the failure mode "
            "this watcher exists to end."
        )

    lines.append("")
    lines.append(
        "---\n*Filed by `.github/workflows/firefox-major-watch.yml` (PS-372). "
        "This job watches and reports; it never bumps, builds or publishes. It is "
        "deliberately silent across the provider's `firefox-NN` rebuild churn — "
        "only a Mozilla MAJOR past ours is news.*"
    )
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--latest-firefox",
                    help="pretend Mozilla published THIS version (the "
                         "falsification path: a higher major must fire, our own "
                         "must not)")
    ap.add_argument("--our-firefox",
                    help="override the Firefox version our engine ships, instead "
                         "of resolving it from the provider")
    ap.add_argument("--baseline-tag", help="override the firefox-NN build tag we ship")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--report-json", help="write the machine-readable result here")
    ap.add_argument("--report-md", help="write the markdown report here")
    ap.add_argument("--github-output", help="append step outputs here ($GITHUB_OUTPUT)")
    args = ap.parse_args(argv)

    # READING OUR OWN BASELINE IS A WAY THIS RUN CAN FAIL, and it is on the
    # SCHEDULED path — the CLI overrides are only reachable from a hand dispatch.
    # Caught by class rather than with a bare `except`:
    #   * OSError — missing, a directory, unreadable
    #   * ValueError — the file exists but holds no `firefox-NN` tag. Covers both
    #     `read_baseline_tag`'s own raise AND UnicodeDecodeError from the
    #     `open(..., encoding="utf-8")`, which is a ValueError subclass and not an
    #     OSError one — so a UTF-16 file lands here rather than escaping.
    # A genuine programming error in this module still escapes and still reddens
    # the run, which is right: that one is a bug to fix, not news to file.
    baseline_error = None
    baseline = None
    if args.baseline_tag is not None:
        baseline = canonical_build_tag(args.baseline_tag)
        if baseline is None:
            baseline_error = ("--baseline-tag %r is not a firefox-NN build tag"
                              % args.baseline_tag)
    else:
        try:
            baseline = read_baseline_tag()
        except (ValueError, OSError) as e:
            baseline_error = ("%s could not be read as a build tag — %s: %s"
                              % (BASELINE_REL, type(e).__name__, e))

    if baseline_error is not None:
        result = baseline_unreadable_result(baseline_error)
    else:
        result = watch(
            baseline,
            token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
            forced_latest=args.latest_firefox,
            forced_our_version=args.our_firefox,
            timeout=args.timeout,
        )

    body = render_report(result)
    print("== PS-372 firefox major watch ==")
    print("   our build:      %s" % result["baseline_tag"])
    print("   our firefox:    %s" % (result["our_firefox_version"] or "<unknown>"))
    print("   mozilla latest: %s" % (result["latest_firefox"] or "<unknown>"))
    print("   macOS:          %s" % result["macos"])
    print("   STATUS:         %s  (%s)"
          % (result["status"], "green" if is_green(result["status"]) else "NOT green"))
    if result.get("error"):
        print("   error:          %s" % result["error"])

    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    if args.report_md:
        with open(args.report_md, "w", encoding="utf-8") as fh:
            fh.write(body)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write("status=%s\n" % result["status"])
            fh.write("our_major=%s\n"
                     % (result["our_major"] if result["our_major"] is not None else ""))
            fh.write("latest_major=%s\n"
                     % (result["latest_major"] if result["latest_major"] is not None else ""))
            fh.write("macos=%s\n" % result["macos"])
            fh.write("green=%s\n" % str(is_green(result["status"])).lower())
            # Anything other than up_to_date is worth a human's attention —
            # including every "we could not look", which must never be silent
            # merely because it is not a finding.
            fh.write("report=%s\n" % str(result["status"] != UP_TO_DATE).lower())
            fh.write("title=%s\n" % issue_title(result))

    if not is_green(result["status"]):
        print("::error::firefox major watch: %s — %s"
              % (result["status"], headline(result)))

    return exit_code_for(result["status"])


if __name__ == "__main__":
    sys.exit(main())
