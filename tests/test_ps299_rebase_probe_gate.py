"""PS-299: the rebase probe is a GATE, so the thing to test is that it FAILS.

`scripts/ps299_rebase_probe.py` answers "do our 16 fingerprint patches still
apply at ungoogled tag X?" in about a minute, and it is explicitly built to run
UNATTENDED in the watch-and-bump automation — its own docstring says "exit
status is 0 only when all 16 apply with zero rejects, so this is usable as a
gate". Everything downstream of it bumps the tag on its say-so.

WHY THIS FILE EXISTS — A GATE THAT COULD NOT FAIL
─────────────────────────────────────────────────
The first version counted rejects by scraping stdout for ``Hunk #N FAILED`` and
never consulted ``returncode``. That is fine for the two shapes it was tested
against (a corrupted context line, an already-applied hunk), because GNU patch
DOES print that string for both. It fails open on the shape the probe's own
header calls out as the trap it exists to encode — **upstream renamed or
deleted a file we patch** — because for a missing target GNU patch prints no
FAILED line at all:

    can't find file to patch at input line 3
    No file to patch.  Skipping patch.
    1 out of 1 hunk ignored

…and exits 1. So ``rej`` stayed 0 and the patch reported ``OK``. Run against a
tree containing NOTHING AT ALL, the probe printed ``81/81 hunks, 0 rejects, ✅``
and exited 0 — it certified the patch set against an empty directory.

That is the PS-11 "emptiness rendered as success" shape, and 22 of our 38 paths
are touched by no ungoogled prerequisite, so nothing else in the run would have
noticed. The negative-testing instinct in the original PR was right; the sample
of breakages just happened to miss the one case that mattered.

TWO PROPERTIES, AND THE SECOND IS WHY THE FIX IS SAFE
─────────────────────────────────────────────────────
Consulting the exit status is only correct if a HEALTHY apply exits 0. Two
healthy shapes must keep passing, and both are asserted here rather than
assumed:

  * an ordinary hunk applying to a present file                    -> exit 0
  * a CREATE-file hunk (``--- /dev/null``) whose target is absent  -> exit 0

The second is the "AN ABSENT PATH IS NOT A DELETED PATH" trap from the probe's
header, seen from the other side: our own patches create ``fingerprint_data.h``,
``gpu_info.*`` and ``gpu_fingerprint.*``, so those files are legitimately
missing from the reconstructed tree and must NOT be read as breakage.

NO NETWORK. These tests drive ``apply_ours`` against fixture trees on disk, which
is exactly how the fail-open was reproduced in review. Nothing here clones,
fetches, or touches googlesource — so this is a gate on the gate that runs
anywhere, including offline CI.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "scripts" / "ps299_rebase_probe.py"
PATCH_DIR = REPO_ROOT / "engine" / "patches" / "fingerprint"

pytestmark = pytest.mark.skipif(
    shutil.which("patch") is None, reason="GNU patch is needed to exercise the probe"
)


def load_probe():
    """Import the probe as a module without executing its CLI."""
    spec = importlib.util.spec_from_file_location("ps299_rebase_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ps299_rebase_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── fixtures: a minimal patch set, so the assertions are about the PROBE ──────
#
# The probe requires exactly EXPECTED_PATCHES files, so each fixture tree gets a
# full set; `n` of them are real one-hunk patches and the rest are trivially
# satisfiable, which keeps the arithmetic in the assertions readable.

MODIFY_PATCH = """--- a/{path}
+++ b/{path}
@@ -1,5 +1,6 @@
 alpha
 bravo
+INSERTED_BY_TEST
 charlie
 delta
 echo
"""

CREATE_PATCH = """--- /dev/null
+++ b/{path}
@@ -0,0 +1,2 @@
+created by a patch
+not present upstream
"""

FILE_BODY = "alpha\nbravo\ncharlie\ndelta\necho\n"


def build_case(tmp_path, patches, files):
    """Write a patch dir and a source tree; return (patch_dir, tree)."""
    pdir = tmp_path / "patches"
    pdir.mkdir()
    for name, text in patches.items():
        (pdir / name).write_text(text, encoding="utf-8")
    tree = tmp_path / "src"
    tree.mkdir()
    for rel, body in files.items():
        f = tree / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return pdir, tree


def one_per_patch(count, kind="modify"):
    """`count` patches, each touching its own file."""
    tmpl = MODIFY_PATCH if kind == "modify" else CREATE_PATCH
    return {
        "%03d-test.patch" % i: tmpl.format(path="f%d.txt" % i) for i in range(count)
    }


def run_apply(mod, pdir, tree, count):
    """Drive apply_ours against a fixture set of `count` patches."""
    mod.PATCH_DIR = str(pdir)
    mod.EXPECTED_PATCHES = count
    return mod.apply_ours(str(tree), 0)


# ── the regression: a missing target file must be REPORTED, not waved through ─


def test_missing_target_file_is_counted_as_rejects(tmp_path, capsys):
    """The exact fail-open: upstream deleted/renamed a file we patch.

    GNU patch emits no `Hunk #N FAILED` for an absent target, so a
    stdout-scraping probe scores it 0 rejects. It must be caught by the exit
    status instead.
    """
    mod = load_probe()
    n = 4
    patches = one_per_patch(n)
    files = {"f%d.txt" % i: FILE_BODY for i in range(n)}
    del files["f2.txt"]  # upstream renamed or deleted this one
    pdir, tree = build_case(tmp_path, patches, files)

    total_h, total_r, total_fuzz = run_apply(mod, pdir, tree, n)

    assert total_h == n, "each fixture patch carries exactly one hunk"
    assert total_r > 0, (
        "a patch whose target file is ABSENT must count as rejecting; scoring it "
        "0 is the fail-open that let the probe certify a patch set against a "
        "tree that did not contain the file"
    )

    out = capsys.readouterr().out
    assert "002-test.patch" in out, "the failing patch must be NAMED, not merely counted"
    assert "::error::" in out, "an unattended gate must emit an error annotation"


def test_empty_tree_is_not_a_clean_apply(tmp_path):
    """The worst case, and the one that was reproduced in review.

    Against a tree containing nothing at all the probe reported
    `81/81 hunks, 0 rejects, ✅` and exited 0. Every patch must reject.
    """
    mod = load_probe()
    n = 5
    pdir, tree = build_case(tmp_path, one_per_patch(n), {})  # no files whatsoever

    total_h, total_r, _ = run_apply(mod, pdir, tree, n)

    assert total_r == total_h, (
        "against an EMPTY tree every hunk must be counted as rejecting — "
        "anything less certifies a patch set against nothing"
    )


def test_missing_file_does_not_mask_a_real_reject_elsewhere(tmp_path):
    """The two failure paths must add, not shadow each other."""
    mod = load_probe()
    n = 3
    files = {"f0.txt": FILE_BODY, "f1.txt": FILE_BODY.replace("charlie", "CHANGED")}
    # f2.txt absent entirely; f1's context is corrupted -> a classic FAILED hunk
    pdir, tree = build_case(tmp_path, one_per_patch(n), files)

    total_h, total_r, _ = run_apply(mod, pdir, tree, n)

    assert total_r == 2, (
        "one corrupted-context reject plus one missing-file reject must both be "
        "counted (got %d)" % total_r
    )


# ── the other half: the fix must not make healthy shapes fail ─────────────────


def test_clean_apply_still_reports_zero(tmp_path):
    """Consulting the exit status must add no false positive on a good tree."""
    mod = load_probe()
    n = 4
    files = {"f%d.txt" % i: FILE_BODY for i in range(n)}
    pdir, tree = build_case(tmp_path, one_per_patch(n), files)

    total_h, total_r, total_fuzz = run_apply(mod, pdir, tree, n)

    assert (total_h, total_r, total_fuzz) == (n, 0, 0)
    for i in range(n):
        assert "INSERTED_BY_TEST" in (tree / ("f%d.txt" % i)).read_text(
            encoding="utf-8"
        )


def test_create_file_patch_against_absent_target_is_not_a_reject(tmp_path):
    """AN ABSENT PATH IS NOT A DELETED PATH — the probe header's first trap.

    Our own patches create `fingerprint_data.h`, `gpu_info.*` and
    `gpu_fingerprint.*`; those files are legitimately missing from the
    reconstructed tree. A create-file hunk exits 0, so the exit-status check
    must leave them alone.
    """
    mod = load_probe()
    n = 3
    pdir, tree = build_case(tmp_path, one_per_patch(n, kind="create"), {})

    total_h, total_r, total_fuzz = run_apply(mod, pdir, tree, n)

    assert (total_r, total_fuzz) == (0, 0), (
        "a patch that CREATES its own file must not be scored as a reject just "
        "because the file is absent — that would misclassify 6 of our 16 patches"
    )
    for i in range(n):
        assert (tree / ("f%d.txt" % i)).read_text(encoding="utf-8").startswith(
            "created by a patch"
        )


def test_wrong_patch_count_refuses_to_measure(tmp_path):
    """A measurement of some OTHER number of patches measures nothing."""
    mod = load_probe()
    pdir, tree = build_case(tmp_path, one_per_patch(3), {})
    mod.PATCH_DIR = str(pdir)
    mod.EXPECTED_PATCHES = 16
    assert mod.apply_ours(str(tree), 0) is None


# ── the fetch path: a network failure must not be laundered into "created" ────


def test_fetch_failure_is_not_reported_as_absent_upstream():
    """A transient network error must be distinguishable from a real 404.

    The original `except Exception: return dest, None` made them identical: the
    None fell into the counted-as-created branch, the file was never written,
    and the patch needing it then "applied" against a tree missing its target.
    Combined with the exit-status hole above, a failed fetch produced a green ✅.
    """
    import urllib.error

    mod = load_probe()
    src = PROBE.read_text(encoding="utf-8")

    assert "except urllib.error.HTTPError" in src, (
        "the fetch handler must distinguish a 404 from every other failure"
    )
    assert "e.code == 404" in src

    # There must be no bare `except Exception` that returns a bare None pair in
    # the fetch path — that is the exact shape that laundered the error.
    assert "        except Exception:\n            return dest, None\n" not in src

    # And the error must actually reach the caller, which must stop rather than
    # measure against a partial tree.
    assert "the reconstructed tree is" in src or "INCOMPLETE" in src

    # HTTPError is a subclass of URLError, so ordering in the handler matters:
    assert issubclass(urllib.error.HTTPError, urllib.error.URLError)


# ── belt and braces: the real patch set is still internally consistent ────────


def test_real_patch_set_has_expected_count_and_parses():
    """Guards the fixtures above against drifting away from reality."""
    mod = load_probe()
    names = sorted(p.name for p in PATCH_DIR.glob("*.patch"))
    assert len(names) == mod.EXPECTED_PATCHES, (
        "the probe's EXPECTED_PATCHES (%d) and the tree (%d) disagree"
        % (mod.EXPECTED_PATCHES, len(names))
    )
    chromium, v8 = mod.patch_paths()
    assert chromium and v8, "the patch set touches both chromium/src and v8/v8"
    assert all(p.startswith("v8/") for p in v8)


def test_probe_is_executable_and_compiles():
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(PROBE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
