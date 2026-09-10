#!/usr/bin/env python3
"""PS-299 — measure our 16 fingerprint patches against ANY ungoogled tag,
without a checkout of Chromium and without a compile.

─────────────────────────────────────────────────────────────────────────────
WHY THIS EXISTS: THE NEXT TAG MUST BE ROUTINE, NOT A REBASE PROJECT
─────────────────────────────────────────────────────────────────────────────
The owner's standing instruction is that every subsequent ungoogled release
"переезжал ЧИСТО и по возможности автоматически". The expensive part of a
rebase is not fixing a hunk — it is DISCOVERING that a hunk broke, which today
costs a full source checkout and, at worst, a 1h25m compile on the build host.

This script answers "do our 16 patches still apply?" in about a minute, from
any machine with network, by reconstructing only the ~38 files our patches
actually touch:

  1. clone the ungoogled-chromium-portablelinux tag (shallow, with submodule)
  2. read `chromium_version.txt` for the Chromium tag it pins
  3. read `DEPS` for the v8 revision it pins  ← v8 lives in a SEPARATE repo
  4. fetch those ~38 files, per-path, from googlesource
  5. apply ungoogled's OWN prerequisite patches, filtered to those files
  6. apply our 16 on top and report per-patch rejects

⚠️ TWO TRAPS THIS ENCODES SO THEY ARE NOT REDISCOVERED (PS-299 measured both):

  * AN ABSENT PATH IS NOT A DELETED PATH. A plain existence probe reports 10
    of our 38 files "missing" at a new tag and would misclassify 6 of 16
    patches as needing a rewrite. They split three ways, all harmless:
      - files OUR patches create   (fingerprint_data.h, gpu_info.*, gpu_fingerprint.*)
      - files UNGOOGLED creates    (components/ungoogled/*)
      - v8/*, which is a DIFFERENT REPOSITORY and simply is not in chromium/src
    So this script resolves v8 through its own repo at the pinned revision and
    lets the create-file patches create their files.

  * SECTION-FILTER UNGOOGLED'S PATCHES, DO NOT STUB THEIR TARGETS. The earlier
    dry run stubbed `bromite_flag_entries.h` empty and bought itself an
    artificial reject in UNGOOGLED's patches, not ours. Filtering each
    prerequisite patch down to the file-sections that touch OUR files avoids
    the whole class: 7 prerequisites apply, 108 are skipped as irrelevant, 0
    fail.

─────────────────────────────────────────────────────────────────────────────
FUZZ=0 IS THE BAR, AND IT IS DELIBERATELY STRICTER THAN THE BUILD
─────────────────────────────────────────────────────────────────────────────
ungoogled applies patches with `patch -p1 --ignore-whitespace` and no --fuzz,
so GNU patch's default fuzz of 2 is live in the real build. This script
defaults to --fuzz=0 anyway. A hunk that only lands with fuzz is a hunk whose
context has ALREADY drifted; it passes today and rejects at the next tag. At
152 exactly three patches (003, 007, 013) were in that state and were
re-anchored, which is why the current set is fuzz-0 clean.

Usage:
    python3 scripts/ps299_rebase_probe.py                       # newest tag
    python3 scripts/ps299_rebase_probe.py --tag 152.0.7977.75-1
    python3 scripts/ps299_rebase_probe.py --tag <t> --keep      # keep the tree

Exit status is 0 only when all 16 apply with zero rejects, so this is usable
as a gate in the watch-and-bump automation. Because it IS a gate, it must be
able to FAIL: see the exit-status note in apply_ours() and the fetch-error note
in main(). A gate that cannot fail is worse than no gate, because the
automation it guards bumps the tag on its say-so.

  0 = all 16 apply, zero rejects, zero fuzz
  1 = rejects and/or fuzz — a rebase is needed
  2 = the measurement could not be made at all (clone failed, a file could not
      be fetched, ungoogled's own prerequisites failed, wrong patch count).
      NOT the same as "the patches are fine"; nothing was measured.
"""

import argparse
import base64
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

UCPL_REPO = "https://github.com/ungoogled-software/ungoogled-chromium-portablelinux.git"
UCPL_API = "https://api.github.com/repos/ungoogled-software/ungoogled-chromium-portablelinux"
GOOGLESOURCE = "https://chromium.googlesource.com"

# PS-361 — the SAME probe, pointed at a different ungoogled platform sibling.
#
# ⚠️ THIS IS A PARAMETERISATION, NOT A SECOND PROBE. The ticket's constraint 1
# is explicit that the Windows route means "parameterising the repo, not writing
# a new probe", and the reason is that this file already encodes two traps that
# cost PS-299 real time to find (an absent path is not a deleted path; section-
# filter prerequisites rather than stubbing their targets). A separate Windows
# probe would rediscover both. `linux` remains the DEFAULT, so every existing
# caller — chromium-upstream-watch.yml, REBASING.md, the tests — is unchanged.
#
# The tag GRAMMARS genuinely differ and cannot be shared: portablelinux tags are
# `152.0.7977.75-1` while the windows sibling's are `152.0.7977.75-1.1`. Reusing
# the linux regex against the windows tag list matches NOTHING, and `newest_tag`
# would then IndexError rather than say why — so each platform carries its own.
PLATFORMS = {
    "linux": {
        "repo": UCPL_REPO,
        "api": UCPL_API,
        "tag_re": r"^\d+\.\d+\.\d+\.\d+-\d+$",
        "label": "ungoogled-chromium-portablelinux",
    },
    "windows": {
        "repo": "https://github.com/ungoogled-software/ungoogled-chromium-windows.git",
        "api": "https://api.github.com/repos/ungoogled-software/ungoogled-chromium-windows",
        "tag_re": r"^\d+\.\d+\.\d+\.\d+-\d+\.\d+$",
        "label": "ungoogled-chromium-windows",
    },
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_DIR = os.path.join(REPO_ROOT, "engine", "patches", "fingerprint")
EXPECTED_PATCHES = 16


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def newest_tag(platform="linux"):
    """Newest ungoogled tag by version order, for the named platform sibling.

    NOTE the deliberate choice: the TAG LIST, not `releases/latest`. The two
    genuinely disagree — on 2026-09-03 the tag list held 152.0.7977.75-1 while
    releases/latest was still 152.0.7977.64-1, a full patch-level apart. The
    tag list is what `actions/checkout` resolves, and what the trial-build
    workflow consumes, so it is the honest answer to "what can we build".

    PS-361: `platform` defaults to "linux" so every existing caller keeps its
    exact behaviour. The tag regex comes from PLATFORMS because the two
    siblings' grammars differ (`-1` vs `-1.1`) — see the note there.
    """
    spec = PLATFORMS[platform]
    with urllib.request.urlopen(spec["api"] + "/tags?per_page=100", timeout=60) as r:
        tags = json.load(r)

    def key(t):
        return [int(x) for x in re.findall(r"\d+", t["name"])]

    named = [t for t in tags if re.match(spec["tag_re"], t["name"])]
    named.sort(key=key, reverse=True)
    return named[0]["name"]


def fetch_text(repo, ref, path, attempts=4):
    """Fetch one file from googlesource, with bounded retry on TRANSIENT errors.

    PS-361 — measured on this ticket: an unretried run against the 38 paths hit
    `HTTP 429 Too Many Requests` on 18 of them, and a later one `HTTP 503` on 2.
    Both are transient, and both correctly produced exit 2 ("NOTHING WAS
    MEASURED") rather than a false pass — the fail-closed behaviour is right and
    is NOT what is being changed here.

    ⚠️ WHAT IS RETRIED IS DELIBERATELY NARROW. Only 429/5xx and transport
    errors, which are statements about the SERVER's availability. A 404 is a
    statement about the FILE and is never retried — that is the "an absent path
    is not a deleted path" case the header describes, and retrying it would
    just be slow before reaching the same, correct, answer.
    """
    url = "%s/%s/+/%s/%s?format=TEXT" % (GOOGLESOURCE, repo, ref, path)
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return base64.b64decode(r.read())
        except urllib.error.HTTPError as e:
            # 404 = genuinely absent upstream. Re-raise immediately; the caller
            # distinguishes it from a transport failure and MUST keep doing so.
            if e.code == 404 or e.code < 429 or i == attempts - 1:
                raise
        except Exception:
            if i == attempts - 1:
                raise
        # Back off. Serialised retries also relieve the 429 this provoked.
        time.sleep(2 ** i * 3)
    raise RuntimeError("unreachable")


def patch_paths():
    """Every source path our 16 patches touch, split chromium vs v8."""
    chromium, v8 = set(), set()
    for name in sorted(os.listdir(PATCH_DIR)):
        if not name.endswith(".patch"):
            continue
        with open(os.path.join(PATCH_DIR, name), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)", line)
                if not m:
                    continue
                p = m.group(1)
                if p == "/dev/null":
                    continue
                (v8 if p.startswith("v8/") else chromium).add(p)
    return sorted(chromium), sorted(v8)


def diff_sections(text):
    """Split a unified diff into (path, section) pairs on its '--- /+++' headers."""
    lines = text.split("\n")
    out, cur, curpath = [], [], None
    for i, ln in enumerate(lines):
        if ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            if cur:
                out.append((curpath, "\n".join(cur)))
            cur = []
            a = ln[4:].split("\t")[0]
            b = lines[i + 1][4:].split("\t")[0]
            p = b if b != "/dev/null" else a
            curpath = re.sub(r"^[ab]/", "", p)
        cur.append(ln)
    if cur:
        out.append((curpath, "\n".join(cur)))
    return out


def apply_prereqs(ucpl, tree, wanted, verbose):
    """Apply ungoogled's own patches, filtered to sections touching OUR files."""
    series = []
    for base, sfile in (
        (os.path.join(ucpl, "ungoogled-chromium", "patches"),
         os.path.join(ucpl, "ungoogled-chromium", "patches", "series")),
        (os.path.join(ucpl, "patches"), os.path.join(ucpl, "patches", "series")),
    ):
        if not os.path.exists(sfile):
            continue
        for line in open(sfile):
            line = line.strip()
            if line and not line.startswith("#"):
                series.append(os.path.join(base, line))

    applied = skipped = 0
    failed = []
    for p in series:
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        keep = [s for path, s in diff_sections(text) if path in wanted]
        if not keep:
            skipped += 1
            continue
        sub = "\n".join(keep)
        if not sub.endswith("\n"):
            sub += "\n"
        r = subprocess.run(
            ["patch", "-p1", "--ignore-whitespace", "--no-backup-if-mismatch", "-r", "-", "-s", "-f"],
            input=sub, text=True, cwd=tree, capture_output=True)
        name = os.path.relpath(p, ucpl)
        if r.returncode != 0:
            failed.append(name)
            print("   prereq FAIL %s" % name)
            print("      " + (r.stdout + r.stderr).strip().replace("\n", "\n      "))
        else:
            applied += 1
            if verbose:
                print("   prereq ok   %s" % name)
    print("   ungoogled prerequisites: applied=%d skipped=%d failed=%d"
          % (applied, skipped, len(failed)))
    return not failed


def staged_patch_list(ucpl):
    """Our 16, read from the checkout's OWN `patches/series` after staging.

    PS-361 — AC2 requires our patches to reach the tree "through upstream's own
    `patches/series` mechanism". If the probe kept reading them straight out of
    `engine/patches/fingerprint`, that staging would be DECORATIVE: the series
    file could be malformed, mis-ordered, or empty and the measurement would be
    identical. Reading the list back out of the series is what makes the staging
    load-bearing — the same file `build.py`'s `generate_patches_from_series`
    consumes is the file this measurement depends on.

    Returns (paths, error). Order is the series' order, which is the order the
    real build applies in and is why 000 (which declares the switches every
    later patch reads) must come first.
    """
    sfile = os.path.join(ucpl, "patches", "series")
    if not os.path.exists(sfile):
        return None, "no patches/series at %s" % sfile
    out = []
    for line in open(sfile, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("fingerprint/"):
            continue          # upstream's own entries — prerequisites, not ours
        p = os.path.join(ucpl, "patches", line)
        if not os.path.exists(p):
            return None, "series names %s but the file is absent" % line
        out.append(p)
    return out, None


def apply_ours(tree, fuzz, paths=None):
    """Apply our 16 with the flags the real build uses, plus a fuzz bound.

    `paths` (PS-361) lets the caller supply the patch files read back out of a
    staged `patches/series`; the default keeps the pre-PS-361 behaviour of
    reading `engine/patches/fingerprint` directly.
    """
    if paths is None:
        names = sorted(n for n in os.listdir(PATCH_DIR) if n.endswith(".patch"))
        paths = [os.path.join(PATCH_DIR, n) for n in names]
    if len(paths) != EXPECTED_PATCHES:
        # The same guard ps218_stage_patches.sh carries, for the same reason:
        # a measurement of some OTHER number of patches measures nothing.
        print("::error::expected %d patches, found %d" % (EXPECTED_PATCHES, len(paths)))
        return None

    total_h = total_r = total_fuzz = 0
    rows = []
    for path in paths:
        name = os.path.basename(path)
        text = open(path, encoding="utf-8", errors="replace").read()
        hunks = len(re.findall(r"^@@ ", text, re.M))
        r = subprocess.run(
            ["patch", "-p1", "--ignore-whitespace", "--fuzz=%d" % fuzz,
             "--no-backup-if-mismatch", "-f", "--forward"],
            input=text, text=True, cwd=tree, capture_output=True)
        out = r.stdout + r.stderr
        rej = len(re.findall(r"^Hunk #\d+ FAILED", out, re.M))
        fz = len(re.findall(r"with fuzz \d", out))

        # ⚠️ THE EXIT STATUS IS PART OF THE MEASUREMENT — scraping "Hunk #N
        # FAILED" alone FAILS OPEN on the single most likely breakage at a new
        # upstream tag: UPSTREAM RENAMED OR DELETED A FILE WE PATCH.
        #
        # GNU patch (2.8, measured) does NOT emit that string when the target
        # file is absent. It emits, and exits 1:
        #     can't find file to patch at input line 3
        #     No file to patch.  Skipping patch.
        #     1 out of 1 hunk ignored
        # so `rej` stays 0 and the patch used to be reported OK. Against a tree
        # containing NOTHING AT ALL this printed "81/81 hunks, 0 rejects, ✅"
        # and exited 0 — a gate that certifies a patch set against an empty
        # tree is worse than no gate, because the watch-and-bump automation
        # bumps the tag on its say-so.
        #
        # Adding no false positive is not an assumption, it is measured: a
        # clean apply exits 0, and so does a legitimate CREATE-file hunk
        # (--- /dev/null) against a tree where the file is absent — which is
        # exactly the "absent path is not a deleted path" case in the header.
        # `apply_prereqs` has relied on this same invariant all along.
        if r.returncode != 0 and rej == 0:
            rej = hunks
            why = "target file missing/renamed?"
            for l in out.splitlines():
                if "can't find file to patch" in l or "hunks ignored" in l or "hunk ignored" in l:
                    why = l.strip()
                    break
            print("::error::%s did not apply (patch exit %d, no FAILED hunk reported) — %s"
                  % (name, r.returncode, why))

        total_h += hunks
        total_r += rej
        total_fuzz += fz
        rows.append((name, hunks, rej, fz, out))

    print()
    print("   %-46s %6s %8s %6s" % ("patch", "hunks", "rejects", "fuzz"))
    print("   " + "-" * 70)
    for name, hunks, rej, fz, out in rows:
        flag = "OK" if rej == 0 and fz == 0 else ("***" if rej else "fuzz")
        print("   %-46s %6d %8d %6d  %s" % (name, hunks, rej, fz, flag))
        if rej:
            for l in out.splitlines():
                if ("FAILED" in l or "can't find file" in l
                        or "hunk ignored" in l or "hunks ignored" in l):
                    print("        " + l)
    print("   " + "-" * 70)
    print("   %-46s %6d %8d %6d" % ("TOTAL", total_h, total_r, total_fuzz))
    return total_h, total_r, total_fuzz


def base_commit(ucpl):
    """The ungoogled-chromium submodule commit this platform checkout pins.

    PS-361 — this is the load-bearing fact behind reusing our 16 unchanged on a
    second platform, and it is worth a guard rather than a comment. Our patches
    touch 38 files; they intersect NEITHER platform layer (0 against windows, 0
    against linux) and overlap only the shared ungoogled BASE layer, in 16 files.
    So the content our hunks anchor against is byte-identical across platforms —
    *provided both siblings pin the same base commit*. They do today (cacf0f0).
    If a future tag pairing ever diverges, that symmetry silently expires, and
    this is what notices instead of a confusing reject much later.
    """
    r = run(["git", "ls-tree", "HEAD", "ungoogled-chromium"], cwd=ucpl)
    if r.returncode != 0:
        return None
    parts = r.stdout.split()
    return parts[2] if len(parts) >= 3 else None


def build_parser():
    """The REAL argument parser, extracted so tests can read it.

    PS-361 — this is not cosmetic. The first version of the PS-361 test suite
    asserted the `--platform` default by REBUILDING a parser with the same flag,
    and a mutation flipping the real default to "windows" passed all 24 tests.
    A test that reconstructs the thing it is checking is not a test of it; it is
    a copy that agrees with itself. Extracting the parser gives the tests the
    actual object every caller gets.
    """
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", choices=sorted(PLATFORMS), default="linux",
                    help="which ungoogled platform sibling to measure against "
                         "(default: linux — every pre-PS-361 caller keeps its behaviour)")
    ap.add_argument("--tag", help="ungoogled tag for the chosen platform (default: newest)")
    ap.add_argument("--expect-base",
                    help="require the ungoogled-chromium submodule to pin this commit; "
                         "exit 2 if it does not. Use it to assert that a second "
                         "platform builds on the SAME base as the linux arm.")
    ap.add_argument("--staged", action="store_true",
                    help="stage our 16 through the checkout's OWN patches/series "
                         "(ps218_stage_patches.sh, 16-count guard active) and apply "
                         "them from there, rather than reading engine/patches/fingerprint "
                         "directly. Makes the series mechanism load-bearing.")
    ap.add_argument("--trees", choices=("both", "unmodified", "patched"), default="patched",
                    help="'unmodified' stops after upstream's own prerequisites — the "
                         "INSTRUMENT CHECK, with none of our patches applied. 'patched' "
                         "(default) is the pre-PS-361 behaviour.")
    ap.add_argument("--fuzz", type=int, default=0,
                    help="patch fuzz bound (default 0 — stricter than the build on purpose)")
    ap.add_argument("--keep", action="store_true", help="keep the reconstructed tree")
    ap.add_argument("--workdir", help="where to build the tree (default: a temp dir)")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main():
    args = build_parser().parse_args()

    spec = PLATFORMS[args.platform]
    tag = args.tag or newest_tag(args.platform)
    print("== PS-299 rebase probe ==")
    print("   platform:      %s (%s)" % (args.platform, spec["label"]))
    print("   ungoogled tag: %s" % tag)

    work = args.workdir or tempfile.mkdtemp(prefix="ps299-")
    os.makedirs(work, exist_ok=True)
    ucpl = os.path.join(work, "ucpl")
    tree = os.path.join(work, "src")

    if not os.path.exists(ucpl):
        r = run(["git", "clone", "--depth", "1", "--branch", tag,
                 "--recurse-submodules", "--shallow-submodules", spec["repo"], ucpl])
        if r.returncode != 0:
            print("::error::clone of tag %s failed\n%s" % (tag, r.stderr))
            return 2

    base = base_commit(ucpl)
    print("   ungoogled base: %s" % (base or "UNREADABLE"))
    if args.expect_base:
        if base is None:
            print("::error::could not read the ungoogled-chromium submodule pin, so the "
                  "base-commit assertion could not be made. NOTHING WAS MEASURED.")
            return 2
        if not base.startswith(args.expect_base) and not args.expect_base.startswith(base):
            print("::error::base commit mismatch: %s pins %s, expected %s. Our patches "
                  "anchor against the SHARED base layer, so a divergent base means the "
                  "cross-platform symmetry argument no longer holds and this result "
                  "would not mean what it appears to mean."
                  % (spec["label"], base, args.expect_base))
            return 2
        print("   base assertion: OK (matches %s)" % args.expect_base)

    chromium_tag = open(os.path.join(ucpl, "ungoogled-chromium", "chromium_version.txt")).read().strip()
    print("   chromium tag:  %s" % chromium_tag)

    chromium_paths, v8_paths = patch_paths()

    # v8 is a SEPARATE repository, pinned by DEPS. Reading the pin matters:
    # measuring 001 against v8 main HEAD instead reports a reject that the real
    # build would never see (PS-299's ticket body carried exactly that caveat).
    v8_rev = None
    if v8_paths:
        deps = fetch_text("chromium/src", chromium_tag, "DEPS").decode("utf-8", "replace")
        m = re.search(r"'v8_revision'\s*:\s*'([0-9a-f]{40})'", deps)
        v8_rev = m.group(1) if m else None
        print("   v8 revision:   %s" % (v8_rev or "NOT FOUND — v8 patches unverifiable"))

    print("\n   reconstructing %d chromium + %d v8 files..."
          % (len(chromium_paths), len(v8_paths)))

    def get(spec):
        repo, ref, path, dest = spec
        # ⚠️ ONLY a 404 means "absent upstream". A bare `except Exception` here
        # made a TRANSIENT NETWORK FAILURE indistinguishable from a file our
        # patches legitimately create: the None fell into the counted-as-created
        # branch below, the file was never written, and the patch that needed it
        # then "applied" against a tree missing its target. Combined with the
        # exit-status hole in apply_ours() that produced a green ✅ from a failed
        # fetch. Re-raise anything that is not a 404 so the run dies loudly.
        try:
            return dest, fetch_text(repo, ref, path), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return dest, None, None      # genuinely absent upstream — expected
            return dest, None, "HTTP %s %s" % (e.code, e.reason)
        except Exception as e:               # URLError, timeout, incomplete read…
            return dest, None, "%s: %s" % (type(e).__name__, e)

    specs = [("chromium/src", chromium_tag, p, p) for p in chromium_paths]
    if v8_rev:
        specs += [("v8/v8", v8_rev, p[len("v8/"):], p) for p in v8_paths]

    ok = created = 0
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for dest, data, err in ex.map(get, specs):
            if err is not None:
                errors.append((dest, err))
                continue
            if data is None:
                # NOT an error: our patches and ungoogled's create files that do
                # not exist upstream. See the header's first trap. This branch is
                # now reached ONLY on a real 404, never on a network failure.
                created += 1
                continue
            full = os.path.join(tree, dest)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "wb").write(data)
            ok += 1
    print("   fetched %d, %d absent upstream (created by a patch — expected)" % (ok, created))

    if errors:
        # A tree we could not fully reconstruct cannot measure anything. Say so
        # and stop, rather than measuring our patches against a partial tree.
        for dest, err in errors:
            print("::error::could not fetch %s — %s" % (dest, err))
        print("::error::%d file(s) failed to fetch — the reconstructed tree is "
              "INCOMPLETE, so any apply result would be meaningless" % len(errors))
        if not (args.keep or args.workdir):
            shutil.rmtree(work, ignore_errors=True)
        return 2

    wanted = set(chromium_paths) | set(v8_paths)
    print()
    if not apply_prereqs(ucpl, tree, wanted, args.verbose):
        print("::error::ungoogled's OWN prerequisite patches failed — this is not our patch set")
        return 2

    # PS-361 — THE UNMODIFIED CONTROL, i.e. the instrument check.
    #
    # `ps218_verify_control.sh` states the whole safety case and it transfers
    # verbatim: a failure on a PATCHED tree has two possible causes — our
    # patches, or an environment that cannot do this at all — and one run cannot
    # separate them. That is MORE likely on a platform we have never measured,
    # not less. On the prepare-only route the "environment" is the reconstructed
    # tree plus upstream's own prerequisites, so the control is: stop HERE,
    # having applied upstream's layer and none of ours, and report that it stood
    # up. If this fails, the finding is about the Windows tree and says nothing
    # whatsoever about our 16.
    if args.trees == "unmodified":
        print()
        print("   ✅ CONTROL (unmodified): the %s tree at %s reconstructed and "
              "upstream's own prerequisites applied cleanly." % (spec["label"], tag))
        print("      %d files reconstructed; OUR PATCHES WERE NOT APPLIED." % len(wanted))
        print("      This is the instrument check. It says the tree is sound; it "
              "says NOTHING about our 16.")
        if args.keep or args.workdir:
            print("   tree kept at %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)
        return 0

    # PS-361 — AC2: reach the tree through UPSTREAM'S OWN series mechanism.
    staged = None
    if args.staged:
        r = run(["bash", os.path.join(REPO_ROOT, "scripts", "ps218_stage_patches.sh")],
                env={**os.environ, "UCPL_DIR": ucpl, "PATCH_DIR": PATCH_DIR},
                cwd=REPO_ROOT)
        print(r.stdout.strip())
        if r.returncode != 0:
            # The 16-count guard lives in that script and is the whole point of
            # routing through it: "A build made to succeed by quietly dropping a
            # patch measures nothing."
            print(r.stderr.strip())
            print("::error::staging our patches into %s failed — NOTHING WAS MEASURED."
                  % spec["label"])
            return 2
        staged, err = staged_patch_list(ucpl)
        if err:
            print("::error::could not read our patches back out of the staged series: %s" % err)
            return 2
        print("   read %d patches back out of the STAGED series (AC2: upstream's own "
              "mechanism, not a side channel)" % len(staged))

    result = apply_ours(tree, args.fuzz, paths=staged)
    if result is None:
        return 2
    total_h, total_r, total_fuzz = result

    print()
    if total_r == 0 and total_fuzz == 0:
        print("   ✅ all %d patches apply at %s: %d/%d hunks clean, fuzz=%d"
              % (EXPECTED_PATCHES, tag, total_h, total_h, args.fuzz))
        rc = 0
    elif total_r == 0:
        print("   ⚠️  all %d patches apply, but %d hunk(s) needed fuzz — re-anchor them"
              % (EXPECTED_PATCHES, total_fuzz))
        print("      A fuzzed hunk passes today and rejects at the NEXT tag.")
        rc = 1
    else:
        print("   ❌ %d of %d hunks reject at %s — rebase needed" % (total_r, total_h, tag))
        rc = 1

    if args.keep or args.workdir:
        print("   tree kept at %s" % work)
    else:
        shutil.rmtree(work, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
