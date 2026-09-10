#!/usr/bin/env python3
"""PS-342 — watch ungoogled-chromium upstream and say whether our 16 patches
still apply to whatever it has published.

─────────────────────────────────────────────────────────────────────────────
WHY THIS EXISTS
─────────────────────────────────────────────────────────────────────────────
We build and ship our own Chromium engine (ungoogled-chromium + the 16
fingerprint patches under engine/patches/fingerprint/). Until this script,
NOTHING anywhere noticed when a newer ungoogled-chromium existed:

  * `engine-autoupdate.yml` watches the patched-FIREFOX engine and only that —
    both of its provisioning steps import `src.services.engine.firefox`, and
    its gate is `engine_gate record`/`compare`.
  * `engine-gpu-variance.yml` reads the ALREADY PUBLISHED Personium release. It
    measures the build users receive; it does not look upstream at all.

So falling behind on the engine we now own was something that would *happen* to
us. This makes it a dated decision instead.

─────────────────────────────────────────────────────────────────────────────
WHAT IT DOES, AND — MORE IMPORTANTLY — WHAT IT DOES NOT
─────────────────────────────────────────────────────────────────────────────
It WATCHES, MEASURES and REPORTS. It does not bump the target, does not build,
does not publish. A Chromium major brings security fixes and breaks patches in
the same move, and weighing those against each other is a human judgement. The
deliverable is a signal with a date on it, not a pipeline.

Three steps:

  1. discover the newest ungoogled-chromium-portablelinux tag
  2. compare it to the tag we currently target
     (engine/patches/fingerprint/CURRENT_TAG.txt)
  3. if it is newer, run `scripts/ps299_rebase_probe.py --tag <new>` — the
     probe PS-299 built to be exactly this gate — and classify its exit status

⚠️ THE ONE WAY TO GET THIS WRONG, AND IT IS THE WHOLE REASON classify() IS A
SEPARATE, TESTED FUNCTION:

The probe has a THREE-value exit contract, and the third is not a pass:

    0 = all 16 apply, zero rejects, zero fuzz
    1 = rejects and/or fuzz — a rebase is needed
    2 = THE MEASUREMENT COULD NOT BE MADE AT ALL

`engine/patches/fingerprint/REBASING.md` warns in its own words that `2` is
"**not** 'the patches are fine'. Nothing was measured. Do not let an automation
treat it as anything other than a hard stop." A watcher that reports 2 as green
is worse than no watcher, because it MANUFACTURES EVIDENCE OF CONTINUITY: a
green run that says our patches still apply, produced by a run that measured
nothing. The probe's own history is why — its first version printed
"81/81 hunks, 0 rejects, ✅" against an EMPTY DIRECTORY (REBASING.md:44-49).

So classify() maps 0 -> CLEAN, 1 -> REJECTS, 2 -> UNMEASURED, and **every other
exit status to UNMEASURED as well** (crash, signal, timeout, a future exit code
this script has not heard of). The default for an unrecognised answer is "we did
not establish anything", never "fine".

─────────────────────────────────────────────────────────────────────────────
STATUSES AND THIS SCRIPT'S OWN EXIT CODE
─────────────────────────────────────────────────────────────────────────────
    status            exit  meaning
    ────────────────  ────  ──────────────────────────────────────────────────
    up_to_date          0   nothing newer upstream. No news.
    clean               0   NEWER TAG, and our 16 apply to it cleanly. News,
                            not a defect — a move we can choose to make.
    rejects             1   NEWER TAG, and our patches DO NOT apply to it. A
                            rebase stands between us and upstream currency.
    unmeasured          2   a newer tag may or may not be fine — WE DO NOT
                            KNOW. Nothing was measured.
    discovery_failed    2   we could not even ask what the newest tag is. Also
                            "we do not know", and reported as such.
    invalid_tag         2   an input we were HANDED is not a tag, so there was
                            nothing to look up. Also "we do not know" — but a
                            DIFFERENT cause, and therefore a different status.
    baseline_unreadable 2   we cannot read CURRENT_TAG.txt, so we do not know
                            what "our current target" even is. Nothing was
                            looked up and nothing was measured.

⚠️ WHY `invalid_tag` IS ITS OWN STATUS RATHER THAN A SECOND USE OF
`discovery_failed` — the three non-green "we do not know" cases are not
interchangeable, and the reason is mechanical, not stylistic:

  * `discovery_failed` means UPSTREAM DID NOT ANSWER. Its headline, its body
    and its issue title all say so ("could not reach the ungoogled-chromium tag
    list"), and every one of those sentences is FALSE when the real cause is a
    dispatcher typing `152.0.7977.75` without the `-1` packaging revision: no
    request was made at all.
  * Worse, the issue title is the DEDUP KEY. The workflow's "File or update the
    report issue" step matches an open issue by exact title and COMMENTS rather
    than filing when it finds one. So a typo'd dispatch filing under the
    outage's title would swallow a real googlesource outage that night into a
    comment on a typo. Different news must own a different record; that is the
    whole reason the title carries the status.
  * `baseline_unreadable` is the same argument applied to OUR OWN file rather
    than to an input: `CURRENT_TAG.txt` missing or corrupt is neither an
    upstream outage nor a dispatcher's typo. Reusing `invalid_tag` for it would
    put a corrupt repo file and a typo'd dispatch box under ONE title — so the
    day someone fat-fingers the dispatch, an unreadable baseline is suppressed
    into a comment on it — AND would tell the reader "re-dispatch with the full
    N.N.N.N-N form", which is advice they cannot act on for a file on main.

⚠️ AND WHY `baseline_unreadable` IS A REPORT RATHER THAN A TRACEBACK. This was
the LAST red-and-silent path in the script: `read_current_tag()` raises, and an
uncaught raise writes no `$GITHUB_OUTPUT`, so the workflow's "File or update the
report issue" step is SKIPPED (`report` is empty), the markdown is never written
and the artifact upload finds nothing. The run goes red in an Actions tab nobody
is subscribed to — which this workflow's own comment names as NOT "a form a
human actually receives". A watcher whose baseline is corrupt must file *I
cannot read my own baseline*, not vanish. Note it is reached by EVERY SCHEDULED
RUN (the CLI flags are only reachable by a hand-dispatch), which is why leaving
it raising was the wrong asymmetry.

The three exit values mirror the probe's own contract deliberately, so the
distinction the probe is careful to preserve survives one more layer of wiring.

Usage:
    python3 scripts/ps342_chromium_watch.py
    python3 scripts/ps342_chromium_watch.py --tag 144.0.7559.132-1   # falsify
    python3 scripts/ps342_chromium_watch.py --report-json /tmp/w.json \
        --report-md /tmp/w.md --github-output "$GITHUB_OUTPUT"
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_DIR = os.path.join(REPO_ROOT, "engine", "patches", "fingerprint")
CURRENT_TAG_FILE = os.path.join(PATCH_DIR, "CURRENT_TAG.txt")
# The repo-relative form, for the report a human reads: the absolute path is a
# runner's scratch directory and means nothing to someone reading a filed issue.
CURRENT_TAG_REL = "engine/patches/fingerprint/CURRENT_TAG.txt"
PROBE = os.path.join(REPO_ROOT, "scripts", "ps299_rebase_probe.py")
REBASING_DOC = "engine/patches/fingerprint/REBASING.md"

UCPL_API = "https://api.github.com/repos/ungoogled-software/ungoogled-chromium-portablelinux"

# ungoogled-chromium-portablelinux tags look like `152.0.7977.75-1`: a four-part
# Chromium version plus a packaging revision.
TAG_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+-\d+$")

# ── the three outcomes of a measurement, plus the two that are not one ────────
CLEAN = "clean"
REJECTS = "rejects"
UNMEASURED = "unmeasured"
UP_TO_DATE = "up_to_date"
DISCOVERY_FAILED = "discovery_failed"
# An input WE WERE HANDED is not a tag. Non-green like DISCOVERY_FAILED and with
# the same exit code, but a separate status because it has a separate CAUSE, a
# separate remedy, and — load-bearing — a separate issue title, which is the
# dedup key the workflow matches on.
INVALID_TAG = "invalid_tag"
# WE CANNOT READ OUR OWN BASELINE. `CURRENT_TAG.txt` is missing, unreadable, or
# does not hold a tag, so there is no "our current target" to compare anything
# against. Third distinct cause, third distinct status, for the same mechanical
# reason INVALID_TAG is not DISCOVERY_FAILED — see the docstring above.
BASELINE_UNREADABLE = "baseline_unreadable"

# Which statuses mean "we know our patches are fine". Note what is NOT here:
# UNMEASURED. This set is the single place that question is answered, and the
# tests assert on it directly.
GREEN_STATUSES = frozenset({UP_TO_DATE, CLEAN})

EXIT_FOR_STATUS = {
    UP_TO_DATE: 0,
    CLEAN: 0,
    REJECTS: 1,
    UNMEASURED: 2,
    DISCOVERY_FAILED: 2,
    INVALID_TAG: 2,
    BASELINE_UNREADABLE: 2,
}


def classify(probe_exit):
    """Map the rebase probe's exit status onto a watcher status.

    ⚠️ READ THE `else` BEFORE CHANGING ANYTHING HERE. The probe documents three
    exit values; this maps FOUR cases, because an automation must have an answer
    for a status it does not recognise. That answer is UNMEASURED — the probe
    was run and we did not obtain a verdict from it — and never CLEAN.

    The shapes that land in the catch-all are real, not hypothetical:
      * a traceback out of an unguarded fetch (measured: the probe's
        `fetch_text` for DEPS is outside its error handling and escapes as a
        bare exception, which Python reports as exit 1 — see the REJECTS note
        below for why that one is safe, and note it is only safe by accident)
      * a signal (-9 / 137 on an OOM-killed or cancelled runner)
      * a timeout, which this module reports as 124 by convention
      * any exit code a future version of the probe introduces

    Every one of those means "nothing was established". None of them may wear
    the colour of a pass.
    """
    if probe_exit == 0:
        return CLEAN
    if probe_exit == 1:
        # Rejects and/or fuzz. This is a FINDING — the probe looked and our
        # patches did not land. Distinguishable from UNMEASURED, which is the
        # distinction the falsification for this ticket turns on.
        return REJECTS
    if probe_exit == 2:
        return UNMEASURED
    return UNMEASURED


def is_green(status):
    """Only a status that rests on an actual measurement is green."""
    return status in GREEN_STATUSES


def exit_code_for(status):
    return EXIT_FOR_STATUS[status]


def parse_tag(name):
    """`152.0.7977.75-1` -> (152, 0, 7977, 75, 1); None when it is not a tag."""
    if not TAG_RE.match((name or "").strip()):
        return None
    return tuple(int(x) for x in re.findall(r"\d+", name.strip()))


def select_newest(names):
    """Newest release tag by version order, ignoring anything non-conforming.

    Deliberately the SAME rule the rebase probe's own `newest_tag()` uses — the
    TAG LIST, filtered to `N.N.N.N-N`, sorted numerically — and
    `tests/test_ps342_chromium_watch.py` asserts the two agree on a fixture, so
    the watcher and the thing it drives cannot drift into disagreeing about
    what "newest" means.

    The tag list, not `releases/latest`: the two genuinely disagree. On
    2026-09-03 the tag list held 152.0.7977.75-1 while releases/latest was still
    152.0.7977.64-1. `actions/checkout` and `engine-trial-build.yml` resolve a
    git ref, so the tag list is the honest answer to "what can we build".
    """
    parsed = [(parse_tag(n), n) for n in names]
    parsed = [p for p in parsed if p[0] is not None]
    if not parsed:
        return None
    parsed.sort(key=lambda p: p[0], reverse=True)
    return parsed[0][1]


def read_current_tag(path=CURRENT_TAG_FILE):
    with open(path, encoding="utf-8") as fh:
        value = fh.read().strip()
    if parse_tag(value) is None:
        raise ValueError("%s does not hold an ungoogled tag: %r" % (path, value))
    return value


def windows_counterpart_tag(linux_tag):
    """`152.0.7977.75-1` -> `152.0.7977.75-1.1`: the WINDOWS sibling's tag.

    ⚠️ THE TWO GRAMMARS ARE DIFFERENT AND ARE NOT INTERCHANGEABLE.
    `ungoogled-chromium-portablelinux` tags read `N.N.N.N-N`; the
    `ungoogled-chromium-windows` sibling's read `N.N.N.N-N.N` — a second
    packaging component. `scripts/ps299_rebase_probe.py` carries a per-platform
    `tag_re` in `PLATFORMS` for exactly this reason, and says why at length: one
    shared regex matches NOTHING on the other platform, and `newest_tag` then
    IndexErrors rather than saying so.

    ⛔ THIS FUNCTION EXISTS SO THE DERIVATION IS WRITTEN ONCE. The `.1` suffix
    is a rule about how upstream names two repositories, not an incidental
    string, and three call sites doing `tag + ".1"` inline are three places a
    future grammar change has to be found. PS-390 put it here rather than in the
    probe because this module already owns `read_current_tag()` — the reader of
    the pin and the deriver of its counterpart belong together, and the probe is
    a measurement tool that is handed a tag rather than one that resolves it.

    ⚠️ IT IS A NAMING RULE, NOT A CLAIM THAT THE TAG EXISTS. Upstream publishes
    the two siblings independently; that they have moved in lockstep at every
    tag we have measured is an observation, not a guarantee. Whether the
    counterpart is actually published is a network question and is deliberately
    NOT asked here.

    Raises ValueError when the input is not a portablelinux tag, because
    silently deriving a counterpart of something that is not a tag would hand a
    caller a plausible-looking string that clones nothing.
    """
    if parse_tag(linux_tag) is None:
        raise ValueError(
            "%r is not an ungoogled-chromium-portablelinux tag, so it has no "
            "windows counterpart" % (linux_tag,)
        )
    return "%s.1" % linux_tag.strip()


def _counterpart_for_report(linux_tag):
    """Render the windows counterpart for a human-readable report, or say why not.

    `render_report` must never raise: it is the LAST thing that runs on every
    non-green path, and a traceback here is red-and-silent — no issue filed, no
    `$GITHUB_OUTPUT` written. That is the exact failure shape
    `invalid_tag_result` exists to prevent one layer up. So a tag we cannot
    derive a counterpart from is reported as unknown rather than allowed to
    escape.
    """
    try:
        return "`%s`" % windows_counterpart_tag(linux_tag)
    except (ValueError, AttributeError, TypeError):
        return (
            "the counterpart of the linux tag (append `.1`) — this report could "
            "not derive it, so read it off the upstream tag list"
        )


def is_newer(candidate, current):
    """True when `candidate` is a strictly newer tag than `current`."""
    c, cur = parse_tag(candidate), parse_tag(current)
    if c is None or cur is None:
        return False
    return c > cur


def discover_newest_tag(token=None, timeout=60, opener=None):
    """Ask GitHub for the newest ungoogled tag. Returns (tag, error).

    A failure here is returned, never raised and never swallowed into a
    plausible-looking default: an automation that cannot ask the question must
    say so, because "no newer tag" and "we could not look" are the same silence
    from outside and completely different facts.
    """
    req = urllib.request.Request(
        UCPL_API + "/tags?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "persona-ps342-chromium-watch",
        },
    )
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    fetch = opener or urllib.request.urlopen
    try:
        with fetch(req, timeout=timeout) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        return None, "HTTP %s %s from the tag list" % (e.code, e.reason)
    except Exception as e:  # URLError, timeout, malformed JSON…
        return None, "%s: %s" % (type(e).__name__, e)
    if not isinstance(payload, list):
        return None, "the tag list did not come back as a list"
    newest = select_newest([t.get("name", "") for t in payload if isinstance(t, dict)])
    if newest is None:
        return None, "no tag in the response matched N.N.N.N-N"
    return newest, None


def run_probe(tag, timeout=2700, runner=None):
    """Run the PS-299 rebase probe against `tag`. Returns (exit_code, output).

    A timeout is reported as 124 — which classify() folds into UNMEASURED along
    with every other unrecognised code, which is exactly right: a probe we cut
    off measured nothing.
    """
    cmd = [sys.executable, PROBE, "--tag", tag, "--fuzz", "0"]
    if runner is not None:
        return runner(cmd, timeout)
    try:
        # `encoding="utf-8"` is load-bearing, not decoration. The probe prints
        # ✅ / ⚠️ / em-dashes (22 non-ASCII lines in ps299_rebase_probe.py), this
        # captured text becomes `probe_log`, and `probe_log` is embedded verbatim
        # into the filed issue body. Bare `text=True` decodes with the platform
        # preferred encoding — cp1252 on a Windows host — so the reporting path
        # would either mojibake or raise UnicodeDecodeError. The workflow pins
        # ubuntu-24.04 so the scheduled run is safe, but this script is also
        # documented for hand-running. (PS-184 class; the encoding-discipline
        # guard only scans tests/ and src/, so scripts/ is unwatched here.)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout, cwd=REPO_ROOT)
    except subprocess.TimeoutExpired:
        return 124, ("the rebase probe did not finish within %ds and was killed; "
                     "nothing was measured" % timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ── the report a human actually receives ─────────────────────────────────────

HEADLINE = {
    UP_TO_DATE: "Up to date — nothing newer upstream",
    CLEAN: "A NEWER ungoogled-chromium exists, and our 16 patches apply to it",
    REJECTS: "A NEWER ungoogled-chromium exists, and our 16 patches DO NOT apply to it",
    UNMEASURED: "A NEWER ungoogled-chromium exists — WE COULD NOT MEASURE IT",
    DISCOVERY_FAILED: "COULD NOT ASK what the newest ungoogled-chromium is",
    INVALID_TAG: "THE TAG WE WERE GIVEN IS NOT A TAG — nothing was looked up",
    BASELINE_UNREADABLE: "WE CANNOT READ OUR OWN BASELINE — nothing was looked up",
}


def issue_title(result):
    """A stable title, so re-running does not file the same news twice.

    The tag AND the status are both in it deliberately: the same tag going from
    `unmeasured` to `clean` is genuinely different news and deserves its own
    record, rather than quietly editing away the fact that we once could not
    measure it.

    ⚠️ THIS TITLE IS THE DEDUP KEY. The workflow's issue step matches an OPEN
    issue by EXACT title and comments on it instead of filing a new one. So two
    unrelated causes sharing a title means the second one is SUPPRESSED into a
    comment on the first. `INVALID_TAG` therefore gets its own line rather than
    borrowing `DISCOVERY_FAILED`'s: a dispatcher's typo must not be able to
    swallow that night's real googlesource outage.

    `INVALID_TAG` deliberately does NOT interpolate the offending value into the
    title. The value is by definition not a tag — it can carry newlines, and the
    title is written into `$GITHUB_OUTPUT` as a bare `key=value` line. The bad
    value belongs in the BODY, where it is already reported via `error`.
    """
    status = result["status"]
    if status == DISCOVERY_FAILED:
        return "[chromium-watch] could not reach the ungoogled-chromium tag list"
    if status == INVALID_TAG:
        return "[chromium-watch] the requested tag is not a valid ungoogled tag"
    if status == BASELINE_UNREADABLE:
        # NOT the INVALID_TAG title. A corrupt CURRENT_TAG.txt on main and a
        # typo in a dispatch box are different news with different remedies,
        # and this title is the dedup key: sharing one would let whichever
        # landed first swallow the other into a comment on itself.
        return "[chromium-watch] cannot read our own baseline (CURRENT_TAG.txt)"
    return "[chromium-watch] ungoogled %s vs our %s — %s" % (
        result.get("newest_tag") or "?", result["current_tag"], status
    )


def headline(result):
    """The headline, corrected for the forced path.

    `HEADLINE` says "A NEWER ungoogled-chromium exists" because on the SCHEDULED
    path that is guaranteed: `is_newer()` gates every non-`up_to_date` status.
    The forced path deliberately skips that gate — measuring an OLDER tag is the
    whole point of the falsification run (`--tag 144.0.7559.132-1`) — so on that
    path the stock wording asserts something false about the tag it measured.
    The measurement itself is unaffected; only the sentence describing it was.
    """
    status = result["status"]
    text = HEADLINE[status]
    if result.get("forced") and status not in (UP_TO_DATE, DISCOVERY_FAILED,
                                               INVALID_TAG, BASELINE_UNREADABLE):
        newest, current = result.get("newest_tag"), result.get("current_tag")
        relation = "an OLDER" if (newest and current and is_newer(current, newest)) \
            else "a HAND-PICKED"
        text = text.replace("A NEWER ungoogled-chromium exists",
                            "%s ungoogled-chromium tag was measured on request" % relation)
    return text


def render_report(result):
    """Markdown for the issue body / job summary."""
    status = result["status"]
    lines = []
    lines.append("## %s" % headline(result))
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append("| our current target | `%s` |" % result["current_tag"])
    lines.append("| newest upstream tag | `%s` |" % (result.get("newest_tag") or "—"))
    lines.append("| verdict | **%s** |" % status)
    if result.get("probe_exit") is not None:
        lines.append("| rebase probe exit | `%s` |" % result["probe_exit"])
    lines.append("| measured at | %s |" % result.get("measured_at", "—"))
    lines.append("")

    if status == UP_TO_DATE:
        lines.append(
            "We are on the newest published tag. Nothing to decide today."
        )
    elif status == CLEAN:
        lines.append(
            "`scripts/ps299_rebase_probe.py` applied all 16 fingerprint patches "
            "at `%s` with zero rejects and zero fuzz." % result["newest_tag"]
        )
        lines.append("")
        lines.append(
            "**This is news, not a defect, and not an instruction to bump.** A "
            "clean textual apply is necessary and NOT sufficient: the probe "
            "measures TEXT and compiles nothing. PS-309 is the standing proof — "
            "all 16 applied at 152 with 81/81 hunks and fuzz 0, and the build "
            "still failed on `timezone_controller.o`. See "
            "`%s` -> \"What broke at COMPILE time\"." % REBASING_DOC
        )
        lines.append("")
        lines.append("**To act on this:**")
        lines.append("")
        lines.append(
            "1. Dispatch `engine-trial-build.yml` with `ungoogled_tag=%s` and "
            "`trees=both` (a first run on a NEW tag must be `both` — a borrowed "
            "control from another tag is correctly refused)." % result["newest_tag"]
        )
        lines.append(
            "2. If it builds, update `%s` and the \"Current target\" line in "
            "`%s` together." % (CURRENT_TAG_REL, REBASING_DOC)
        )
        # ⛔ PS-390 — THE THIRD STEP EXISTS BECAUSE THE PIN IS WRITTEN ON TWO
        # PLATFORMS AND THIS LIST ONLY EVER NAMED ONE. Since PS-361 the Windows
        # arm carries its own copy of the tag and of the base commit, as
        # workflow-level env fallbacks AND as workflow_dispatch input defaults.
        # Nothing bumps them, and the arm's pull_request trigger fires on any
        # change to engine/patches/fingerprint/*.patch — which is exactly what a
        # rebase PR is. So a bump that stops at step 2 leaves the Windows arm
        # measuring the tag we are LEAVING, and reporting green about it. The
        # probe's --expect-base cannot catch that: the stale tag and the stale
        # base are CONSISTENT with each other, and a mismatch assertion is
        # structurally blind to a coherent-but-obsolete pair.
        lines.append(
            "3. ⚠️ **AND move the WINDOWS arm's pin in the same change** — "
            "`.github/workflows/engine-trial-build-windows.yml` writes it in "
            "FOUR places: the `env:` fallbacks `UNGOOGLED_TAG` / `EXPECT_BASE`, "
            "and the `workflow_dispatch` input defaults `ungoogled_tag` / "
            "`expect_base` (the latter pair is what you see in the GitHub UI "
            "when you dispatch it by hand)."
        )
        lines.append("")
        counterpart = _counterpart_for_report(result.get("newest_tag"))
        lines.append(
            "   * The Windows tag is the COUNTERPART of the linux one — %s — "
            "note the grammar: portablelinux is `-1`, windows is `-1.1`, and "
            "they are not interchangeable." % counterpart
        )
        lines.append(
            "   * `EXPECT_BASE` is the `ungoogled-chromium` submodule commit "
            "BOTH siblings pin. It is a 40-char sha and is **not derivable from "
            "a tag** — read it with `git ls-tree HEAD ungoogled-chromium` on "
            "each checkout, and if the two siblings ever disagree the "
            "cross-platform argument in `%s` has expired and the Windows arm "
            "must not be bumped on it." % REBASING_DOC
        )
        lines.append("")
        lines.append(
            "   A test (`tests/test_ps342_chromium_watch.py`) fails if step 3 "
            "is skipped, so this is a reminder rather than the guard."
        )
    elif status == REJECTS:
        lines.append(
            "`scripts/ps299_rebase_probe.py` ran and reported **rejects and/or "
            "fuzz** at `%s`. Upstream currency now costs a rebase."
            % result["newest_tag"]
        )
        lines.append("")
        lines.append(
            "This is a MEASURED failure — the probe looked and our patches did "
            "not land. It is not the same as the \"could not measure\" case; see "
            "the per-patch table in the log below for which hunks rejected."
        )
        lines.append("")
        lines.append(
            "Re-anchoring guidance (including the include-block trap that caused "
            "nine of the thirteen rejects at 144 -> 152) is in `%s`." % REBASING_DOC
        )
    elif status == UNMEASURED:
        lines.append(
            "⛔ **NOTHING WAS MEASURED. This is not a pass.** The rebase probe "
            "exited `%s`, which its own manual (`%s`) describes as \"**not** 'the "
            "patches are fine'\"." % (result.get("probe_exit"), REBASING_DOC)
        )
        lines.append("")
        lines.append(
            "Whether our 16 patches apply at `%s` is **unknown**. Do not read "
            "this run as continuity. Usual causes: the tag could not be cloned, "
            "a source file could not be fetched, ungoogled's own prerequisite "
            "patches failed, or the patch count in the tree is not 16."
            % (result.get("newest_tag") or "the newest tag")
        )
        lines.append("")
        lines.append(
            "Re-run by hand — `python3 scripts/ps299_rebase_probe.py --tag %s` — "
            "and read the log before concluding anything."
            % (result.get("newest_tag") or "<tag>")
        )
    elif status == DISCOVERY_FAILED:
        lines.append(
            "⛔ **NOTHING WAS MEASURED.** We could not even retrieve the "
            "ungoogled-chromium tag list, so we do not know whether a newer "
            "release exists: `%s`" % result.get("error")
        )
        lines.append("")
        lines.append(
            "\"No newer tag\" and \"we could not look\" are the same silence from "
            "outside and completely different facts. This run is the second one."
        )
    elif status == INVALID_TAG:
        lines.append(
            "⛔ **NOTHING WAS MEASURED — and nothing was even looked up.** The "
            "tag this run was asked to measure is not an ungoogled tag, so no "
            "request was made and no probe was run: `%s`" % result.get("error")
        )
        lines.append("")
        lines.append(
            "**This is not an upstream problem.** Upstream was never contacted. "
            "The overwhelmingly likely cause is the `tag` box of a "
            "`workflow_dispatch` — the usual slip is dropping the `-1` "
            "packaging revision (`152.0.7977.75` instead of "
            "`152.0.7977.75-1`). Re-dispatch with the full `N.N.N.N-N` form, or "
            "leave the box blank for the normal watch."
        )
        lines.append("")
        lines.append(
            "It is reported non-green and exits `2` on purpose: a request we "
            "could not act on established nothing about our patches. It is "
            "filed under its OWN title rather than the tag-list-outage one so "
            "that a typo here can never suppress a real upstream outage into a "
            "comment on it."
        )
    elif status == BASELINE_UNREADABLE:
        lines.append(
            "⛔ **NOTHING WAS MEASURED — and nothing was even looked up.** The "
            "watcher could not read the tag it is supposed to be comparing "
            "against, so it does not know what \"our current target\" is: `%s`"
            % result.get("error")
        )
        lines.append("")
        lines.append(
            "**This is not an upstream problem, and not a bad dispatch input.** "
            "Upstream was never contacted. `%s` is a file in this repository, "
            "and it is missing, unreadable, or does not hold an `N.N.N.N-N` "
            "tag. The usual causes are a bad rebase deleting it, or an editor "
            "writing it with a UTF-8 BOM or CRLF." % CURRENT_TAG_REL
        )
        lines.append("")
        lines.append(
            "Restore it to the tag named on the \"Current target\" line of `%s` "
            "— the two are asserted to agree by "
            "`tests/test_ps342_chromium_watch.py`, so CI on any PR will "
            "disagree with you if you pick the wrong one." % REBASING_DOC
        )
        lines.append("")
        lines.append(
            "It is reported here rather than raised because a traceback out of "
            "this script writes no step outputs, files no issue and uploads no "
            "artifact — the run would be red and SILENT, which is the failure "
            "mode this watcher exists to end."
        )

    log = result.get("probe_log")
    if log:
        lines.append("")
        lines.append("<details><summary>rebase probe output</summary>")
        lines.append("")
        lines.append("```")
        lines.append(log.strip()[-40000:])
        lines.append("```")
        lines.append("")
        lines.append("</details>")

    lines.append("")
    lines.append(
        "---\n*Filed by `.github/workflows/chromium-upstream-watch.yml` "
        "(PS-342). This job watches and reports; it never bumps, builds or "
        "publishes.*"
    )
    return "\n".join(lines) + "\n"


def watch(current_tag, token=None, forced_tag=None, probe_timeout=2700,
          runner=None, opener=None, now=None):
    """The whole decision, as a pure-ish function returning a result dict."""
    result = {
        "current_tag": current_tag,
        "newest_tag": None,
        "status": None,
        "probe_exit": None,
        "probe_log": None,
        "error": None,
        "measured_at": now or _utcnow(),
        "forced": forced_tag is not None,
    }

    if forced_tag is not None:
        # The falsification path, and the manual "measure this specific tag"
        # path. Discovery is skipped; everything downstream is identical, which
        # is what makes a hand-run genuinely exercise the wiring the schedule
        # uses rather than a parallel one.
        #
        # VALIDATE IT HERE — this is the LAST line of defence, and `main()`
        # now also refuses it at the argparse boundary (the `bad = None` /
        # `invalid_tag_result(...)` block just below `ap.parse_args`).
        # Both, deliberately: `watch()` is importable and is called directly by
        # tests and by hand, so a guard that lived only in `main()` would be a
        # guard the function itself does not have.
        #
        # This is the less trusted of the two inputs — it arrives from a
        # workflow_dispatch box a human types into — and `read_current_tag()`
        # has validated its own file since the first commit, so leaving the CLI
        # value unchecked was exactly backwards. Unvalidated, an embedded
        # newline forged additional step outputs: `--tag $'evil\ngreen=true'`
        # reached `$GITHUB_OUTPUT` through issue_title(), where each line is
        # written as `key=value` with no delimiter — so the dispatcher could
        # hand themselves `green=true` on a run that measured nothing. Refusing
        # is correct rather than sanitising: a tag that is not a tag is a
        # mistake to report, not one to repair.
        #
        # The status is INVALID_TAG and NOT DISCOVERY_FAILED. The distinction is
        # not cosmetic: DISCOVERY_FAILED's headline, body and — critically —
        # ISSUE TITLE all say upstream did not answer, which is false here (no
        # request was made), and that title is the dedup key, so borrowing it
        # would let a typo suppress a real outage into a comment on itself.
        if not TAG_RE.match(forced_tag.strip()):
            result["status"] = INVALID_TAG
            result["error"] = (
                "--tag %r is not a valid ungoogled tag; expected the "
                "N.N.N.N-N shape, e.g. 152.0.7977.75-1. Nothing was measured."
                % forced_tag
            )
            return result
        newest = forced_tag.strip()
    else:
        newest, err = discover_newest_tag(token=token, opener=opener)
        if newest is None:
            result["status"] = DISCOVERY_FAILED
            result["error"] = err
            return result

    result["newest_tag"] = newest

    if not forced_tag and not is_newer(newest, current_tag):
        result["status"] = UP_TO_DATE
        return result

    code, log = run_probe(newest, timeout=probe_timeout, runner=runner)
    result["probe_exit"] = code
    result["probe_log"] = log
    result["status"] = classify(code)
    return result


def _utcnow():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def invalid_tag_result(flag, value, current_tag=None, now=None):
    """The result for "an input we were handed is not a tag".

    Built here rather than raising, because the REPORT is this script's
    deliverable: an `argparse` error would exit 2 with a usage message, write no
    `$GITHUB_OUTPUT`, file no issue and upload no artifact — the run would be
    red and silent, which is the failure mode this whole ticket exists to end.

    ⚠️ Note what is NOT interpolated anywhere that reaches `$GITHUB_OUTPUT`: the
    offending value. It is by definition not a tag, so it may contain newlines,
    and outputs are bare `key=value` lines with no delimiter. It goes in
    `error`, which is rendered into the issue BODY (a file, via `--body-file`)
    and never into `title=`.
    """
    return {
        "current_tag": current_tag if current_tag and parse_tag(current_tag) else "<unknown>",
        "newest_tag": None,
        "status": INVALID_TAG,
        "probe_exit": None,
        "probe_log": None,
        "error": ("%s %r is not a valid ungoogled tag; expected the N.N.N.N-N "
                  "shape, e.g. 152.0.7977.75-1. Nothing was measured."
                  % (flag, value)),
        "measured_at": now or _utcnow(),
        "forced": False,
    }


def baseline_unreadable_result(error, now=None):
    """The result for "we cannot read our own baseline".

    Same argument as `invalid_tag_result` — the REPORT is this script's
    deliverable, so a cause that stops us measuring must still travel the normal
    reporting path rather than escaping as a traceback. This one matters MORE
    than its sibling, not less: the CLI flags are only reachable from a hand
    dispatch, while `CURRENT_TAG.txt` is read by EVERY SCHEDULED RUN. Left
    raising, a corrupt or deleted file meant the watcher silently stopped
    watching, on a schedule, with the only trace a red run in an Actions tab
    this workflow's own comment says nobody receives.

    ⚠️ The path is interpolated but the FILE CONTENTS are not: `error` carries
    the offending value (via `read_current_tag`'s `%r`) and `error` reaches only
    the issue BODY, written with `--body-file`. It never reaches `title=`, which
    is a bare `key=value` line in `$GITHUB_OUTPUT` that an embedded newline
    would forge additional outputs through.
    """
    return {
        "current_tag": "<unreadable>",
        "newest_tag": None,
        "status": BASELINE_UNREADABLE,
        "probe_exit": None,
        "probe_log": None,
        "error": error,
        "measured_at": now or _utcnow(),
        "forced": False,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", help="measure THIS tag instead of discovering the "
                                  "newest (the falsification / manual path)")
    ap.add_argument("--current-tag", help="override the pinned current target")
    ap.add_argument("--probe-timeout", type=int, default=2700)
    ap.add_argument("--report-json", help="write the machine-readable result here")
    ap.add_argument("--report-md", help="write the markdown report here")
    ap.add_argument("--github-output", help="append step outputs here ($GITHUB_OUTPUT)")
    args = ap.parse_args(argv)

    # EVERY TAG-SHAPED INPUT IS VALIDATED AT THE BOUNDARY, not just `--tag`.
    #
    # `--current-tag` was the last unvalidated route into `$GITHUB_OUTPUT`: it is
    # written out verbatim as `current_tag=…` AND interpolated into `title=`, so
    # `--current-tag $'x\ngreen=true'` forged a green step output on a run that
    # measured nothing — the exact hole the `--tag` guard closed, surviving on
    # the sibling flag. `read_current_tag()` has always validated the FILE form
    # of this same value, so leaving the CLI override unchecked was inconsistent
    # on top of unsafe.
    #
    # `--tag` is checked here too so the comment in `watch()` is true of the CLI
    # as well; `watch()` keeps its own guard because it is importable and is
    # called directly.
    bad = None
    if args.current_tag is not None and parse_tag(args.current_tag) is None:
        bad = ("--current-tag", args.current_tag)
    elif args.tag is not None and parse_tag(args.tag) is None:
        bad = ("--tag", args.tag)

    if bad is not None:
        result = invalid_tag_result(bad[0], bad[1],
                                    current_tag=args.current_tag)
    else:
        # READING OUR OWN BASELINE IS ALSO A WAY THIS RUN CAN FAIL, and until
        # this was wrapped it was the ONLY tag input on the scheduled path still
        # failing red-and-silent — the exact mode `invalid_tag_result`'s
        # docstring condemns, applied to the two flags a human dispatch reaches
        # but not to the file every scheduled run reads.
        #
        # Caught by class rather than by a bare `except Exception`. The two
        # named classes cover every way reading this file fails:
        #   * `OSError` — missing (FileNotFoundError), a directory
        #     (IsADirectoryError), unreadable (PermissionError)
        #   * `ValueError` — the file exists but does not hold a tag. This
        #     covers BOTH `read_current_tag`'s own raise (BOM, CRLF, a
        #     half-written rebase artefact) AND `UnicodeDecodeError` from the
        #     `open(..., encoding="utf-8")`, which is a ValueError subclass and
        #     not an OSError one (checked, not assumed) — so a UTF-16 or
        #     latin-1 file lands here rather than escaping.
        # A genuine programming error in this module still escapes and still
        # reddens the run, which is right: that one is a bug to fix, not news to
        # file.
        try:
            current = args.current_tag or read_current_tag()
        except (ValueError, OSError) as e:
            result = baseline_unreadable_result(
                "%s could not be read as a tag — %s: %s"
                % (CURRENT_TAG_REL, type(e).__name__, e))
        else:
            result = watch(
                current,
                token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
                forced_tag=args.tag,
                probe_timeout=args.probe_timeout,
            )

    body = render_report(result)
    print("== PS-342 chromium upstream watch ==")
    print("   our target:   %s" % result["current_tag"])
    print("   newest tag:   %s" % (result["newest_tag"] or "<not discovered>"))
    print("   probe exit:   %s" % result["probe_exit"])
    print("   STATUS:       %s  (%s)"
          % (result["status"], "green" if is_green(result["status"]) else "NOT green"))
    if result["probe_log"]:
        print(result["probe_log"])

    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    if args.report_md:
        with open(args.report_md, "w", encoding="utf-8") as fh:
            fh.write(body)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write("status=%s\n" % result["status"])
            fh.write("newest_tag=%s\n" % (result["newest_tag"] or ""))
            fh.write("current_tag=%s\n" % result["current_tag"])
            fh.write("probe_exit=%s\n" % (result["probe_exit"] if result["probe_exit"] is not None else ""))
            fh.write("green=%s\n" % str(is_green(result["status"])).lower())
            # Anything other than up_to_date is worth a human's attention —
            # including CLEAN, which is the good news this job exists to
            # deliver, and including UNMEASURED, which must never be silent
            # merely because it is not a finding.
            fh.write("report=%s\n" % str(result["status"] != UP_TO_DATE).lower())
            fh.write("title=%s\n" % issue_title(result))

    if not is_green(result["status"]):
        print("::error::chromium upstream watch: %s — %s"
              % (result["status"], headline(result)))

    return exit_code_for(result["status"])


if __name__ == "__main__":
    sys.exit(main())
