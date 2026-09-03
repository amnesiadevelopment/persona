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
as a gate in the watch-and-bump automation.
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
import urllib.request

UCPL_REPO = "https://github.com/ungoogled-software/ungoogled-chromium-portablelinux.git"
UCPL_API = "https://api.github.com/repos/ungoogled-software/ungoogled-chromium-portablelinux"
GOOGLESOURCE = "https://chromium.googlesource.com"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_DIR = os.path.join(REPO_ROOT, "engine", "patches", "fingerprint")
EXPECTED_PATCHES = 16


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def newest_tag():
    """Newest ungoogled tag by version order.

    NOTE the deliberate choice: the TAG LIST, not `releases/latest`. The two
    genuinely disagree — on 2026-09-03 the tag list held 152.0.7977.75-1 while
    releases/latest was still 152.0.7977.64-1, a full patch-level apart. The
    tag list is what `actions/checkout` resolves, and what the trial-build
    workflow consumes, so it is the honest answer to "what can we build".
    """
    with urllib.request.urlopen(UCPL_API + "/tags?per_page=100", timeout=60) as r:
        tags = json.load(r)

    def key(t):
        return [int(x) for x in re.findall(r"\d+", t["name"])]

    named = [t for t in tags if re.match(r"^\d+\.\d+\.\d+\.\d+-\d+$", t["name"])]
    named.sort(key=key, reverse=True)
    return named[0]["name"]


def fetch_text(repo, ref, path):
    url = "%s/%s/+/%s/%s?format=TEXT" % (GOOGLESOURCE, repo, ref, path)
    with urllib.request.urlopen(url, timeout=90) as r:
        return base64.b64decode(r.read())


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


def apply_ours(tree, fuzz):
    """Apply our 16 with the flags the real build uses, plus a fuzz bound."""
    names = sorted(n for n in os.listdir(PATCH_DIR) if n.endswith(".patch"))
    if len(names) != EXPECTED_PATCHES:
        # The same guard ps218_stage_patches.sh carries, for the same reason:
        # a measurement of some OTHER number of patches measures nothing.
        print("::error::expected %d patches, found %d" % (EXPECTED_PATCHES, len(names)))
        return None

    total_h = total_r = total_fuzz = 0
    rows = []
    for name in names:
        path = os.path.join(PATCH_DIR, name)
        text = open(path, encoding="utf-8", errors="replace").read()
        hunks = len(re.findall(r"^@@ ", text, re.M))
        r = subprocess.run(
            ["patch", "-p1", "--ignore-whitespace", "--fuzz=%d" % fuzz,
             "--no-backup-if-mismatch", "-f", "--forward"],
            input=text, text=True, cwd=tree, capture_output=True)
        out = r.stdout + r.stderr
        rej = len(re.findall(r"^Hunk #\d+ FAILED", out, re.M))
        fz = len(re.findall(r"with fuzz \d", out))
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
                if "FAILED" in l or "can't find file" in l:
                    print("        " + l)
    print("   " + "-" * 70)
    print("   %-46s %6d %8d %6d" % ("TOTAL", total_h, total_r, total_fuzz))
    return total_h, total_r, total_fuzz


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", help="ungoogled portablelinux tag (default: newest)")
    ap.add_argument("--fuzz", type=int, default=0,
                    help="patch fuzz bound (default 0 — stricter than the build on purpose)")
    ap.add_argument("--keep", action="store_true", help="keep the reconstructed tree")
    ap.add_argument("--workdir", help="where to build the tree (default: a temp dir)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    tag = args.tag or newest_tag()
    print("== PS-299 rebase probe ==")
    print("   ungoogled tag: %s" % tag)

    work = args.workdir or tempfile.mkdtemp(prefix="ps299-")
    os.makedirs(work, exist_ok=True)
    ucpl = os.path.join(work, "ucpl")
    tree = os.path.join(work, "src")

    if not os.path.exists(ucpl):
        r = run(["git", "clone", "--depth", "1", "--branch", tag,
                 "--recurse-submodules", "--shallow-submodules", UCPL_REPO, ucpl])
        if r.returncode != 0:
            print("::error::clone of tag %s failed\n%s" % (tag, r.stderr))
            return 2

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
        try:
            return dest, fetch_text(repo, ref, path)
        except Exception:
            return dest, None

    specs = [("chromium/src", chromium_tag, p, p) for p in chromium_paths]
    if v8_rev:
        specs += [("v8/v8", v8_rev, p[len("v8/"):], p) for p in v8_paths]

    ok = created = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for dest, data in ex.map(get, specs):
            if data is None:
                # NOT an error: our patches and ungoogled's create files that do
                # not exist upstream. See the header's first trap.
                created += 1
                continue
            full = os.path.join(tree, dest)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "wb").write(data)
            ok += 1
    print("   fetched %d, %d absent upstream (created by a patch — expected)" % (ok, created))

    wanted = set(chromium_paths) | set(v8_paths)
    print()
    if not apply_prereqs(ucpl, tree, wanted, args.verbose):
        print("::error::ungoogled's OWN prerequisite patches failed — this is not our patch set")
        return 2

    result = apply_ours(tree, args.fuzz)
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
