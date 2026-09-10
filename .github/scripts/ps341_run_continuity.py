#!/usr/bin/env python3
"""PS-375 — resolve two published engine builds, read PS-341's vector on both,
and hand the reading to the verdict.

WHAT THIS IS
────────────
The other half of the caller PS-341's instrument never had. It:

  1. asks the product's OWN discovery which engine versions are published
     (``updater.engine_versions_newest_first``) — the same call the operator's
     app reaches through,
  2. resolves build **N** through ``updater.fetch_latest_checked``, which
     applies ``policy.check`` so a build persona already refuses is NOT
     measured (the refusal is correct and this job does not re-litigate it),
  3. resolves build **N−1** through ``updater.fetch_release_full``, whose
     ``('','','')`` answer for a yanked or deleted release is a NAMED outcome
     here rather than a substitution,
  4. downloads both through the product's own ``download_engine`` (same digest
     gate, same install path — which is what makes this a reading of the bytes
     an operator receives rather than of a file this script happened to fetch),
  5. imports ``scripts/ps341_gpu_seeds`` and calls its ``read_pair`` per seed on
     both binaries — **imported, never forked**, so there is one probe and one
     venue, and
  6. hands the reading to ``.github/scripts/ps341_continuity_verdict.py``.

⚠️ THE VENUE IS NOT NEGOTIABLE, AND THIS IS WHY THE PROBE IS IMPORTED
─────────────────────────────────────────────────────────────────────
``read_pair`` launches HEADFUL under the inherited ``DISPLAY`` and reads over
CDP. It would be much simpler to shell out to ``--headless=new --dump-dom`` and
diff two strings — and that exact substitution is what made PS-341's first
probe report **8 of 8 moved**, *"a clean, confident, and COMPLETELY FALSE
result"*: the old build's headless arm returned no reading at all for every
seed, and "no reading" compared against a real string is unequal, so every row
scored MOVED. **The instrument, not the engine, produced the 8/8.**

Importing the shipped ``read_pair`` rather than re-implementing it is what makes
that impossible to reintroduce by accident here, and it is also the ticket's own
bound: *do not grow a second harness*.

⚠️ TWO BINARIES, TWO DIRECTORIES, ONE PROCESS — AND THE ORDER IS LOAD-BEARING
──────────────────────────────────────────────────────────────────────────────
``updater.ENGINE_BINARY`` is computed AT IMPORT TIME from ``ENGINE_DIR``, which
``src/core/config`` reads from ``PERSONA_ENGINE_DIR``. So the two builds cannot
be installed into two directories by one import of the updater: whichever
directory the env named at import wins, and the second ``download_engine`` would
overwrite the first build in place — leaving both legs pointing at the SAME
binary, which compares perfectly equal and reports a confident ``held`` for a
comparison that never happened. That is the mirror image of the false 8/8 and
would be far harder to notice, because its output looks like the good news.

So each build is staged by a SEPARATE CHILD PROCESS (:func:`stage_build`), each
with its own ``PERSONA_ENGINE_DIR``, and this parent then verifies that the two
staged files have DIFFERENT sha256 digests before reading either of them. Two
identical binaries would be a positive-control failure, not a reading, and it is
refused as ``record_inconsistent`` rather than reported as continuity.

⛔ WHAT THIS DOES NOT DO
────────────────────────
It does not drive ``updater.revert_to_previous_build``, and that is the choice
PS-375 asked to be made explicitly. ``scripts/ps341_engine_continuity.py`` is
the stronger two-leg arm — it exercises the real rollback gesture and reads a
live profile through the product launcher — and costs the same two transfers
PLUS a profile lifecycle and a mutation of the machine's engine state. The
GPU-seed arm needs **no profile and no revert**: it asks the engine directly per
seed, which is exactly the question PS-341's finding is about (the pair is
ENGINE-authored on this arm), and it is the arm whose reading is committed.
⛔ Only one is built. This is that one.

USAGE
─────
    xvfb-run -a python3 .github/scripts/ps341_run_continuity.py \\
        --work /tmp/ps375 --out /tmp/ps375/report

    # a manual re-read of two named versions (the falsification path)
    xvfb-run -a python3 .github/scripts/ps341_run_continuity.py \\
        --new-version 152.0.7977.75 --old-version 148.0.7778.215 ...
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

VERDICT_MOD = HERE / "ps341_continuity_verdict.py"
PROBE_MOD = REPO / "scripts" / "ps341_gpu_seeds.py"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _log(msg: str) -> None:
    print(msg, flush=True)


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── staging, in a child so each build gets its own ENGINE_DIR ────────────────

_STAGE_CHILD = r'''
import json, os, sys
sys.path.insert(0, %(repo)r)
from src.services.engine import updater

url, digest, tag = sys.argv[1], sys.argv[2], sys.argv[3]
target = updater.ENGINE_BINARY
want = os.path.realpath(os.environ["PERSONA_ENGINE_DIR"])
if os.path.realpath(os.path.dirname(target)) != want:
    sys.exit("PERSONA_ENGINE_DIR did not reach the updater: %%r vs %%r"
             %% (os.path.dirname(target), want))
if not updater.download_engine(url, digest=digest, tag=tag):
    sys.exit("download_engine refused or failed for %%s" %% tag)
updater.write_version(tag)
print(json.dumps({"binary": target, "size": os.path.getsize(target)}))
'''


def stage_build(version: str, url: str, digest: str, engine_dir: pathlib.Path) -> dict:
    """Download ONE build into its own directory, via the product's downloader.

    A child process, because ``updater.ENGINE_BINARY`` is bound at import from
    ``PERSONA_ENGINE_DIR`` — see the module header for what a single import
    would silently do to the second leg.
    """
    engine_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PERSONA_ENGINE_DIR"] = str(engine_dir)
    # The AppImage runtime consumes this itself; the runner may have no FUSE.
    env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    _log("staging engine %s -> %s" % (version, engine_dir))
    proc = subprocess.run(
        [sys.executable, "-c", _STAGE_CHILD % {"repo": str(REPO)},
         url, digest or "", version],
        env=env, capture_output=True, text=True,
    )
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError("could not stage engine %s: %s"
                           % (version, proc.stderr.strip().splitlines()[-1:]))
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    binary = pathlib.Path(info["binary"])
    info["sha256"] = _sha256(binary)
    _log("staged %s: %s (%d bytes, sha256 %s)"
         % (version, binary, info["size"], info["sha256"][:16]))
    return info


# ── resolution ───────────────────────────────────────────────────────────────


def resolve(verdict, new_version: str = "", old_version: str = "",
            timeout: int = 30) -> tuple:
    """Resolve build N and build N−1, or a NAMED non-measurement result.

    Returns ``(plan, result)`` — exactly one of which is None. Every failure
    path here produces a RESULT rather than raising, because the report is this
    job's deliverable: a traceback writes no step output, files no issue and
    uploads no artifact, leaving the run red and silent in an Actions tab this
    project has already recorded that nobody receives.
    """
    from src.services.engine import updater

    published = updater.engine_versions_newest_first(timeout=timeout)
    if not published:
        # ⚠️ `[]` is `engine_versions_newest_first`'s answer for BOTH "the API
        # did not answer" and "no engine tag is published at all" — its own
        # docstring says the caller must treat it as "nothing published, never
        # GitHub is unreachable". Neither is a comparison, and neither is a
        # pass, so both land on a non-green status; this one names the ambiguity
        # rather than picking a side it cannot see.
        return None, verdict.discovery_failed_result(
            "updater.engine_versions_newest_first() returned no versions. "
            "Either no `personium-` release is published or the tag list could "
            "not be read — this call cannot distinguish them, and neither is a "
            "measurement."
        )
    _log("published engine versions (newest first): %s" % ", ".join(published))

    # ⚠️ THE PREDECESSOR QUESTION IS ANSWERED FIRST, FROM THE TAG LIST ALONE,
    # and the ordering is deliberate rather than incidental. Whether a
    # predecessor EXISTS is settled by the list we have just read successfully;
    # it does not depend on N's asset resolving, on N's policy verdict, or on
    # anything else being reachable. Asking those first would turn "there is
    # only one release, so there is nothing to compare" — a definitive answer —
    # into whatever the next network call happened to return, which is how a
    # settled fact gets reported as an outage. It is also the cheap order: on a
    # repository with one engine tag this returns before spending a request.
    candidate = new_version or published[0]
    older = [v for v in published if updater.is_newer(candidate, v)]
    if not old_version and not older:
        return None, verdict.no_predecessor_result(candidate, published)

    if new_version:
        # The manual re-read path. Resolved through the SAME by-tag fetch the
        # scheduled path uses for N−1, so a hand run exercises this wiring
        # rather than a parallel one.
        n_ver = new_version
        n_v, n_url, n_digest = updater.fetch_release_full(n_ver, timeout=timeout)
        if not n_url:
            return None, verdict.predecessor_unreachable_result(n_ver, n_ver)
    else:
        # ⚠️ UNPINNED, and through the GOVERNED fetch. `fetch_latest_checked`
        # applies `policy.check`, so a build already on the known-bad list is
        # refused here rather than measured. The risk is whatever the newest
        # published release IS, which is exactly what a pin would hide.
        n_ver, n_url, n_digest, pol, msg = updater.fetch_latest_checked(timeout=timeout)
        if pol != "ok":
            return None, verdict.refused_by_policy_result(n_ver, pol, msg)
        if not n_url:
            return None, verdict.discovery_failed_result(
                "the newest engine release %r could not be resolved to an asset "
                "for this OS." % n_ver)
        # The governed fetch resolves the newest tag itself. If that disagrees
        # with the list the predecessor decision was taken from, re-take it —
        # never carry a predecessor chosen for a different N.
        if n_ver != candidate:
            older = [v for v in published if updater.is_newer(n_ver, v)]
            if not old_version and not older:
                return None, verdict.no_predecessor_result(n_ver, published)

    # Build N−1 is the next version DOWN from N in the published list, so the
    # comparison is between two of OUR OWN releases and in the direction an
    # operator actually travels (N−1 -> N).
    o_ver = old_version or older[0]

    o_v, o_url, o_digest = updater.fetch_release_full(o_ver, timeout=timeout)
    if not o_url:
        # ⛔ NAMED, not substituted. `fetch_release_full`'s own docstring:
        # "a rollback that silently installs something else is worse than one
        # that refuses."
        return None, verdict.predecessor_unreachable_result(n_ver, o_ver)

    return {
        "new_version": n_ver, "new_url": n_url, "new_digest": n_digest,
        "old_version": o_ver, "old_url": o_url, "old_digest": o_digest,
        "published_versions": published,
    }, None


# ── the reading ──────────────────────────────────────────────────────────────


def read_both(probe, new_binary: str, old_binary: str, seeds) -> dict:
    """PS-341's own ``read_pair``, per seed, on both builds.

    ⛔ The ``None`` return is preserved and is the whole point: a leg that
    produced no reading is recorded as ``None`` and the verdict EXCLUDES it from
    the moved/same tally rather than scoring it as a difference.
    """
    rows = []
    for seed in seeds:
        new = probe.read_pair(new_binary, seed)
        old = probe.read_pair(old_binary, seed)
        readable = new is not None and old is not None
        rows.append({
            "seed": seed,
            "new": new,
            "old": old,
            "readable": readable,
            "moved": (new != old) if readable else None,
        })
        _log("seed %-12s %s" % (
            seed,
            ("MOVED" if new != old else "same") if readable else "UNREADABLE"))
    return {"rows": rows}


def run(args) -> int:
    verdict = _load(VERDICT_MOD, "ps341_continuity_verdict")
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    plan, result = resolve(
        verdict, new_version=args.new_version, old_version=args.old_version)
    if result is not None:
        _log("no comparison was made: %s" % result["status"])
        return verdict.emit(result, None, args)

    new_info = stage_build(plan["new_version"], plan["new_url"],
                           plan["new_digest"], work / "engine-new")
    old_info = stage_build(plan["old_version"], plan["old_url"],
                           plan["old_digest"], work / "engine-old")

    # THE POSITIVE CONTROL, and it is not decoration. Two legs pointing at the
    # same bytes compare perfectly equal and report a confident `held` for a
    # comparison that never happened — the failure mode whose output looks like
    # good news. PS-341's own reading carries a three-axis positive control for
    # the same reason, and its note says why: "the record and the bytes can both
    # move while the process a page actually talks to does not."
    if new_info["sha256"] == old_info["sha256"]:
        return verdict.emit({
            "status": verdict.RECORD_INCONSISTENT,
            "new_version": plan["new_version"],
            "old_version": plan["old_version"],
            "error": (
                "both legs staged the SAME bytes (sha256 %s). Two identical "
                "binaries compare equal for every seed and would report a "
                "confident 'held' for a comparison that never happened, so "
                "nothing is concluded from this run."
                % new_info["sha256"][:16]),
            "measured_at": verdict.utcnow(),
            **verdict.tally([]),
        }, None, args)

    probe = _load(PROBE_MOD, "ps341_gpu_seeds")
    seeds = probe.SEEDS
    _log("reading %d seeds on both builds, headful under DISPLAY=%s"
         % (len(seeds), os.environ.get("DISPLAY", "<unset>")))
    record = read_both(probe, new_info["binary"], old_info["binary"], seeds)
    record.update({
        "new_version": plan["new_version"],
        "old_version": plan["old_version"],
        "new_binary": new_info["binary"],
        "old_binary": old_info["binary"],
        "new_sha256": new_info["sha256"],
        "old_sha256": old_info["sha256"],
        "measured_at": verdict.utcnow(),
    })
    # The instrument's own summary is written out, and the verdict RE-DERIVES it
    # from the rows and cross-checks — a summary nothing checks is a claim that
    # cannot become false.
    record.update({k: v for k, v in verdict.tally(record["rows"]).items()
                   if k in ("seeds_attempted", "seeds_scored",
                            "seeds_unreadable", "moved", "same")})

    if args.reading_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.reading_json)) or ".",
                    exist_ok=True)
        with open(args.reading_json, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)

    return verdict.emit(verdict.classify(record), record, args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default="/tmp/ps375")
    ap.add_argument("--new-version", default="",
                    help="measure THIS version as build N (manual re-read only; "
                         "the scheduled run is deliberately unpinned)")
    ap.add_argument("--old-version", default="",
                    help="compare against THIS version as build N−1")
    ap.add_argument("--reading-json", default="")
    ap.add_argument("--report-json", default="")
    ap.add_argument("--report-md", default="")
    ap.add_argument("--github-output", default="")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
