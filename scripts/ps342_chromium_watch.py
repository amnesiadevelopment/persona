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
}


def issue_title(result):
    """A stable title, so re-running does not file the same news twice.

    The tag AND the status are both in it deliberately: the same tag going from
    `unmeasured` to `clean` is genuinely different news and deserves its own
    record, rather than quietly editing away the fact that we once could not
    measure it.
    """
    status = result["status"]
    if status == DISCOVERY_FAILED:
        return "[chromium-watch] could not reach the ungoogled-chromium tag list"
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
    if result.get("forced") and status not in (UP_TO_DATE, DISCOVERY_FAILED):
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
            "2. If it builds, update `engine/patches/fingerprint/CURRENT_TAG.txt` "
            "and the \"Current target\" line in `%s` together." % REBASING_DOC
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
        # VALIDATE IT HERE, EVEN THOUGH main() ALREADY DID. This is the less
        # trusted of the two inputs — it arrives from a workflow_dispatch box a
        # human types into — and `read_current_tag()` has validated its own file
        # since the first commit, so leaving the CLI value unchecked was exactly
        # backwards. Unvalidated, an embedded newline forged additional step
        # outputs: `--tag $'evil\ngreen=true'` reached `$GITHUB_OUTPUT` through
        # issue_title(), where each line is written as `key=value` with no
        # delimiter — so the dispatcher could hand themselves `green=true` on a
        # run that measured nothing. Refusing is correct rather than sanitising:
        # a tag that is not a tag is a mistake to report, not one to repair.
        if not TAG_RE.match(forced_tag.strip()):
            result["status"] = DISCOVERY_FAILED
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

    current = args.current_tag or read_current_tag()
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
