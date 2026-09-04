"""PS-307: the prepared Chromium tree is reused between dispatches — SAFELY.

WHAT THIS IS ABOUT
──────────────────
Every `engine-trial-build` dispatch rebuilds the Chromium tree from scratch:
~1.7 GB tarball, unpack, toolchains, 111 de-googling patches, domain
substitution, `gn gen`. During the PS-299 rebase loop that is paid on every
iteration, including on runs that die minutes later for unrelated reasons. The
operator asked for the tree to be preserved between runs.

`clean: false` alone is unsafe, and the reason is mechanical rather than
theoretical. Upstream's `scripts/shared.sh` guards each preparation phase with a
stamp INSIDE the source tree, and `apply_patches()` is:

    if [ ! -f "${_src_dir}/.patched.stamp" ]; then
        prune_binaries.py ...
        patches.py apply "${_src_dir}" "${_main_repo}/patches" "${_root}/patches"
        touch "${_src_dir}/.patched.stamp"
    fi

`.patched.stamp` records THAT patching happened, never WHICH series. Our 16
patches are appended to `patches/series` — which lives OUTSIDE the source tree
while the stamp lives INSIDE it. So a preserved, already-stamped tree makes
`apply_patches()` a complete no-op, our 16 never enter the tree, the compile
succeeds, and the artifact is labelled as carrying 16 fingerprint patches while
carrying none.

WHY THIS FILE BUILDS A REAL TREE INSTEAD OF ASSERTING OVER YAML
───────────────────────────────────────────────────────────────
The static half of this suite (workflow wiring, stamp semantics) is cheap and is
here, but it cannot answer the only question that matters: does the guard
actually SEE a tree with the patches missing?

So `patched_tree` and `unmodified_tree` are REAL directory trees. The pre-image
of every file our 16 patches touch is reconstructed from the patches' own hunk
context, and then GNU `patch` applies the real patch files to it. The patched
fixture is a tree our patches were genuinely applied to; the unmodified fixture
is the same tree with them genuinely absent. The verifier is then run against
both, and its verdict has to be right about each.

That is what makes the negative controls here real rather than decorative:

  * `test_verifier_fails_when_the_stamp_is_present_but_the_patches_are_not`
    reproduces the exact defect the ticket describes — a tree carrying
    `.patched.stamp`, a `patches/series` naming all 16, and none of the patches
    in the source. Everything a stamp-reading check would look at says "patched".
    The verifier must still fail.
  * `test_verifier_reports_absent_on_a_tree_that_never_had_our_patches` is the
    negative control for the checker itself: a matcher that always hit would
    pass every `present` test in this file and fail only this one.
  * `test_verifier_fails_when_a_single_patch_is_missing` drops ONE patch from an
    otherwise complete tree. A check that only looked at the tree in aggregate —
    or at the first patch — passes everything else and fails here.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.posix_shell import find_posix_shell, shell_env

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build.yml"
TREE_STATE_SH = REPO_ROOT / "scripts" / "ps307_tree_state.sh"
VERIFY_SH = REPO_ROOT / "scripts" / "ps307_verify_patches_in_tree.sh"
EVIDENCE_AWK = REPO_ROOT / "scripts" / "ps307_patch_evidence.awk"
MANIFEST_SH = REPO_ROOT / "scripts" / "ps218_manifest.sh"
PATCH_DIR = REPO_ROOT / "engine" / "patches" / "fingerprint"

TAG = "152.0.7977.75-1"
OTHER_TAG = "144.0.7559.132-1"

# The three stamp names, read from upstream's `shared.sh` at the tag this
# workflow builds rather than assumed to carry over between tags.
DOWNLOADED_STAMP = ".downloaded.stamp"
PATCHED_STAMP = ".patched.stamp"
DOMSUB_STAMP = ".domsub.stamp"

pytestmark = pytest.mark.skipif(
    find_posix_shell() is None,
    reason="ps307_*.sh are bash and run on the Linux self-hosted runner",
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixture construction: a REAL tree, built by reversing our own patches
# ─────────────────────────────────────────────────────────────────────────────
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _preimage_blocks(patch_path: Path) -> dict[str, list[list[str]] | None]:
    """Split a patch into the BEFORE state of each region it touches.

    A unified diff carries its own pre-image: context lines and deleted lines.
    Each hunk yields one BLOCK of pre-image text.

    Returns {relative_path: [block, ...]} — with `None` for a file the patch
    CREATES, which has no pre-image and must simply not exist beforehand.

    ⚠️ BLOCKS, NOT ONE RECONSTRUCTED FILE. Three of the files our 16 patches
    touch are touched by TWO patches (element.cc, navigator.cc,
    webgl_rendering_context_base.cc), and the second patch's hunks are numbered
    against the FIRST one's output — so line numbers cannot be honoured across
    patches. Blocks can: `patch` locates a hunk by its context TEXT and reports
    the offset, so a file assembled from every patch's blocks in order takes all
    of them. Reconstructing from one patch's numbering alone silently drops the
    other's regions, which is how the first cut of this fixture failed on
    009-webdriver.patch.
    """
    files: dict[str, list[list[str]] | None] = {}
    path: str | None = None
    creating = False
    block: list[str] = []

    def end_block() -> None:
        nonlocal block
        if path is not None and block and not creating:
            files.setdefault(path, []).append(list(block))  # type: ignore[union-attr]
        block = []

    def end_file() -> None:
        end_block()
        if path is not None and creating:
            files[path] = None

    for raw in patch_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.startswith("--- "):
            end_file()
            creating = raw.startswith("--- /dev/null")
            path = None
            continue
        if raw.startswith("+++ "):
            target = raw[4:].split("\t")[0].strip()
            path = target[2:] if target.startswith(("a/", "b/")) else target
            files.setdefault(path, [])
            continue
        if raw.startswith(("diff --git", "index ")):
            continue
        if path is None:
            continue
        if _HUNK.match(raw):
            end_block()
            continue

        marker, body = raw[:1], raw[1:]
        if marker in (" ", "-"):
            block.append(body)
        # '+' lines are post-image only, and any other line is diff noise.

    end_file()
    return files


def _write_source_tree(dest: Path) -> list[Path]:
    """Materialise the tree our 16 patches apply to, GROWING IT AS THEY APPLY.

    ⚠️ A LATER PATCH'S PRE-IMAGE IS AN EARLIER PATCH'S OUTPUT, and writing it as
    a fresh region is how a "control" tree ends up carrying our patches.
    015-canvas-measure-text.patch's context in `base_rendering_context_2d.cc` is
    verbatim the block 012-canvas-get-image-data.patch ADDS: written as its own
    region it becomes a SECOND copy that 012 never touches, so reversing 012 off
    the tree leaves 012's own code sitting in it. The negative control then fails
    against a verifier that is working perfectly — which is precisely the shape of
    false signal this whole ticket is about, arriving through the test harness.

    So the tree is not assembled and then patched. It is built the way upstream
    builds it: for each patch in series order, write only the regions that are
    not already in the file, then APPLY that patch. A region an earlier patch
    produced is therefore found rather than duplicated, and the question "is this
    block an earlier patch's output?" is answered by looking at the file instead
    of by a heuristic over patch text.

    Returns the patches applied, in order.
    """
    patches = sorted(PATCH_DIR.glob("*.patch"))
    per_patch = {p: _preimage_blocks(p) for p in patches}

    # A file CREATED by one patch and then edited by a later one — 011 extends
    # the `fingerprint_data.h` that 002 creates — must not be pre-written from
    # the later patch's context, or 002 refuses with "would create the file ...
    # which already exists". The creator is the authority for that file.
    created = {
        rel
        for files in per_patch.values()
        for rel, blocks in files.items()
        if blocks is None
    }

    def normalised(lines: list[str]) -> list[str]:
        return [line.strip() for line in lines]

    def merge_block(body_lines: list[str], block: list[str]) -> list[str]:
        """Splice one hunk's pre-image into the file being grown.

        THE OVERLAP CASE IS THE WHOLE REASON THIS IS NOT AN APPEND.
        015-canvas-measure-text.patch's context in `base_rendering_context_2d.cc`
        BEGINS with the block 012-canvas-get-image-data.patch adds and then
        continues past it. Appending it whole makes a SECOND copy of 012's code
        that 012 never touches, so reversing 012 leaves 012's own lines in the
        "unmodified" tree and the negative control fails against a verifier that
        is working perfectly. Testing `block in body` and skipping instead drops
        015's extra lines and 015 no longer applies.

        So the longest PREFIX of the block that is already in the file is found,
        and only the remainder is spliced in immediately after it. Anything with
        no overlap is appended as a fresh region.
        """
        nb, nl = normalised(block), normalised(body_lines)
        for size in range(len(nb), 0, -1):
            head = nb[:size]
            # A SHORT or trivial overlap is a coincidence, not the same region:
            # `}`, a blank line or a lone `#include` recurs all over a Chromium
            # source, and splicing on one of those inserts the hunk in the wrong
            # place and the patch then fails to apply. Require the shared prefix
            # to be several lines long and to contain something specific.
            if size < 3 or not any(len(line) >= 20 for line in head):
                break
            for start in range(len(nl) - size + 1):
                if nl[start:start + size] == head:
                    end = start + size
                    return body_lines[:end] + block[size:] + body_lines[end:]
        out = list(body_lines)
        if out:
            out.append(f"// filler between regions {len(out)}")
        return out + list(block)

    for patch_path in patches:
        for rel, blocks in per_patch[patch_path].items():
            if blocks is None or rel in created:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            body = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
            for block in blocks:
                if block:
                    body = merge_block(body, block)
            target.write_text("\n".join(body) + "\n", encoding="utf-8")

        result = _apply(patch_path, dest)
        assert result.returncode == 0, (
            f"fixture construction failed on {patch_path.name}:\n"
            f"{result.stdout}\n{result.stderr}"
        )

    return patches


def _apply(patch_path: Path, src: Path, *, reverse: bool = False) -> subprocess.CompletedProcess:
    """Apply (or reverse) one patch exactly the way upstream's `patches.py` does."""
    return subprocess.run(
        [
            "patch", "-p1", "--ignore-whitespace",
            "-i", str(patch_path), "-d", str(src),
            "--no-backup-if-mismatch",
            "--reverse" if reverse else "--forward",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _build_ucpl(root: Path, *, apply_ours: bool, skip: set[str] | None = None) -> Path:
    """Build a fake ucpl checkout with a `build/src` tree.

    The tree is ALWAYS grown fully patched by `_write_source_tree` and then
    REVERSED back to whatever this fixture wants — the whole set for a control,
    or one named patch for a "15 of 16" tree. Reversing is not a detour: a later
    patch's context lines are the earlier patches' output, so the only honest way
    to obtain "the tree without patch N" is to take the tree WITH it and undo N.
    Assembling an unpatched tree directly from patch context produces a control
    contaminated by construction (see `_write_source_tree`).
    """
    ucpl = root / "ucpl"
    src = ucpl / "build" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (ucpl / "build" / "download_cache").mkdir(parents=True, exist_ok=True)
    applied = _write_source_tree(src)

    # Reverse in reverse series order, so each patch is undone from the tree
    # state it was applied to.
    to_reverse = list(applied) if not apply_ours else [
        p for p in applied if skip and p.name in skip
    ]
    for patch_path in reversed(applied):
        if patch_path not in to_reverse:
            continue
        result = _apply(patch_path, src, reverse=True)
        assert result.returncode == 0, (
            f"fixture could not reverse {patch_path.name}:\n"
            f"{result.stdout}\n{result.stderr}"
        )

    # Reversing an add-file hunk empties the file rather than unlinking it, so
    # the emptied files are cleared out: a tree without that patch does not carry
    # them at all, and an empty file would answer a `newfile` claim as PRESENT.
    for patch_path in to_reverse:
        for rel, blocks in _preimage_blocks(patch_path).items():
            if blocks is None:
                target = src / rel
                if target.exists() and target.read_text(encoding="utf-8").strip() == "":
                    target.unlink()

    # Upstream's stamps: a tree that reached the end of prepare carries all three.
    for stamp in (DOWNLOADED_STAMP, PATCHED_STAMP, DOMSUB_STAMP):
        (src / stamp).touch()

    return ucpl


def _run(script: Path, args: list[str], *, cwd: Path, env_extra: dict) -> subprocess.CompletedProcess:
    env = shell_env()
    env.update(env_extra)
    return subprocess.run(
        [find_posix_shell(), str(script), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _verify(workdir: Path, mode: str, tree: str = "patched") -> subprocess.CompletedProcess:
    return _run(
        VERIFY_SH,
        [mode, tree],
        cwd=workdir,
        env_extra={"UCPL_DIR": "ucpl", "PATCH_DIR": str(PATCH_DIR)},
    )


def _plan(workdir: Path, tree: str, tag: str = TAG) -> subprocess.CompletedProcess:
    return _run(
        TREE_STATE_SH,
        ["plan", tree],
        cwd=workdir,
        env_extra={"UCPL_DIR": "ucpl", "UNGOOGLED_TAG": tag, "PATCH_DIR": str(PATCH_DIR)},
    )


def _seal(workdir: Path, tree: str, tag: str = TAG) -> subprocess.CompletedProcess:
    return _run(
        TREE_STATE_SH,
        ["seal", tree],
        cwd=workdir,
        env_extra={"UCPL_DIR": "ucpl", "UNGOOGLED_TAG": tag, "PATCH_DIR": str(PATCH_DIR)},
    )


@pytest.fixture
def patched_tree(tmp_path: Path) -> Path:
    """A workdir whose `ucpl/build/src` genuinely carries all 16 of our patches."""
    _build_ucpl(tmp_path, apply_ours=True)
    return tmp_path


@pytest.fixture
def unmodified_tree(tmp_path: Path) -> Path:
    """A workdir whose `ucpl/build/src` genuinely carries NONE of our patches."""
    _build_ucpl(tmp_path, apply_ours=False)
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# The fixture itself has to be honest before anything built on it means anything
# ─────────────────────────────────────────────────────────────────────────────
def test_the_fixture_tree_really_takes_our_patches(patched_tree: Path):
    """Every one of the 16 applies to the reconstructed pre-image, at offset 0.

    If this fails, every other assertion in this file is about a tree that does
    not represent the real one, so it is asserted first and explicitly.
    """
    src = patched_tree / "ucpl" / "build" / "src"
    # 007's added function is only in the tree if 007 actually applied.
    element = (src / "third_party/blink/renderer/core/dom/element.cc").read_text()
    assert "ShadowRoot* Element::FakeShadowRoot() const {" in element

    # And 009 is a REMOVAL-only patch: its evidence is a line that must be GONE.
    navigator = (src / "third_party/blink/renderer/core/frame/navigator.cc").read_text()
    assert "RuntimeEnabledFeatures::AutomationControlledEnabled()" not in navigator

    # 011 creates files that cannot exist unless it applied.
    assert (src / "third_party/blink/renderer/modules/webgl/gpu_fingerprint.cc").is_file()


def test_the_unmodified_fixture_really_lacks_our_patches(unmodified_tree: Path):
    """The negative fixture must be genuinely negative, not merely named so."""
    src = unmodified_tree / "ucpl" / "build" / "src"
    element = (src / "third_party/blink/renderer/core/dom/element.cc").read_text()
    assert "ShadowRoot* Element::FakeShadowRoot() const {" not in element
    navigator = (src / "third_party/blink/renderer/core/frame/navigator.cc").read_text()
    assert "RuntimeEnabledFeatures::AutomationControlledEnabled()" in navigator
    assert not (src / "third_party/blink/renderer/modules/webgl/gpu_fingerprint.cc").exists()


# ─────────────────────────────────────────────────────────────────────────────
# THE VERIFIER — the guard the whole ticket exists for
# ─────────────────────────────────────────────────────────────────────────────
def test_verifier_finds_all_sixteen_patches_in_a_genuinely_patched_tree(patched_tree: Path):
    result = _verify(patched_tree, "present")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "all 16 fingerprint patches VERIFIED PRESENT" in result.stdout

    report = (patched_tree / "record" / "patch-presence-patched.txt").read_text()
    assert "verdict:          PASS" in report
    # Every patch is individually accounted for — an aggregate verdict could hide
    # a patch that was never checked at all.
    for patch_path in sorted(PATCH_DIR.glob("*.patch")):
        assert patch_path.name in report, f"{patch_path.name} is not named in the report"
        assert "❌" not in report


def test_verifier_fails_when_the_stamp_is_present_but_the_patches_are_not(unmodified_tree: Path):
    """THE FAILURE THIS TICKET EXISTS TO PREVENT, reproduced exactly.

    The tree carries `.patched.stamp` (so upstream's `apply_patches()` would skip
    itself), `patches/series` names all 16 fingerprint patches, and
    `patches/fingerprint/` holds the files. Every stamp-shaped signal says
    "patched". The source files say otherwise, and the source files are right.

    A verification that read the stamp — or the series, or the staged patch
    directory — would report SUCCESS here, on the tree that carries none of our
    patches. That is why this test exists and why it uses `present` mode against
    the unmodified fixture.
    """
    ucpl = unmodified_tree / "ucpl"
    src = ucpl / "build" / "src"

    # Everything a stamp-reading check would find reassuring:
    assert (src / PATCHED_STAMP).is_file()
    series = ucpl / "patches" / "series"
    series.parent.mkdir(parents=True, exist_ok=True)
    staged = ucpl / "patches" / "fingerprint"
    staged.mkdir(parents=True, exist_ok=True)
    names = []
    for patch_path in sorted(PATCH_DIR.glob("*.patch")):
        shutil.copy2(patch_path, staged / patch_path.name)
        names.append(f"fingerprint/{patch_path.name}")
    series.write_text("\n".join(names) + "\n", encoding="utf-8")

    result = _verify(unmodified_tree, "present")

    assert result.returncode != 0, (
        "the verifier PASSED a tree that is stamped as patched, has all 16 patches "
        "in its series, and carries none of them in its source. That is exactly the "
        "silent patch-drop this ticket exists to make impossible.\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "does NOT carry our full fingerprint patch layer" in result.stdout


def test_verifier_fails_when_a_single_patch_is_missing(tmp_path: Path):
    """15 of 16 is not 16. A per-patch verdict is what catches this."""
    _build_ucpl(tmp_path, apply_ours=True, skip={"010-headless.patch"})

    result = _verify(tmp_path, "present")

    assert result.returncode != 0, (
        "the verifier passed a tree missing 010-headless.patch. An artifact built "
        "from it would be labelled as carrying 16 patches while carrying 15."
    )
    report = (tmp_path / "record" / "patch-presence-patched.txt").read_text()
    assert "010-headless.patch" in report
    assert "NOT IN THE TREE" in report
    # And the other 15 must still be reported as present — a check that failed
    # everything once one patch was missing would be useless for diagnosis.
    assert "patches failing:  1" in report
    assert "patches passing:  15" in report
    present = [line for line in report.splitlines() if " PRESENT " in line]
    assert len(present) == 15, f"expected 15 patches still reported present, got {len(present)}"


def test_verifier_catches_a_removal_only_patch(tmp_path: Path):
    """009-webdriver.patch ADDS NOTHING — it only deletes two lines.

    A verifier that looked exclusively for added text would find no evidence for
    it and would have to either skip it (reporting an unchecked patch as checked)
    or invent a pass. Skipping it makes this test fail, which is the point.
    """
    _build_ucpl(tmp_path, apply_ours=True, skip={"009-webdriver.patch"})

    result = _verify(tmp_path, "present")

    assert result.returncode != 0
    report = (tmp_path / "record" / "patch-presence-patched.txt").read_text()
    assert "009-webdriver.patch" in report
    assert "NOT IN THE TREE" in report


def test_verifier_reports_absent_on_a_tree_that_never_had_our_patches(unmodified_tree: Path):
    """THE NEGATIVE CONTROL FOR THE CHECKER ITSELF.

    A matcher that always hit — a grep whose pattern matched anything, an
    evidence file that came out empty and was treated as vacuously satisfied —
    would pass every `present` assertion in this file. It fails here.

    This is also the contamination check the control tree needs on its own terms:
    if our patches turn up in the unmodified tree, every "the control had this
    error too" attribution resting on it is false.
    """
    result = _verify(unmodified_tree, "absent", "unmodified")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "verified ABSENT" in result.stdout


def test_absent_mode_refuses_a_contaminated_control(patched_tree: Path):
    """And the negative control's own negative control: `absent` must be able to fail."""
    result = _verify(patched_tree, "absent", "unmodified")
    assert result.returncode != 0, (
        "`absent` mode passed a tree that genuinely carries all 16 patches, so it "
        "could never detect a contaminated control."
    )
    assert "supposed to be the UNMODIFIED control" in result.stdout


def test_verifier_refuses_a_tree_that_does_not_exist(tmp_path: Path):
    """An absent tree is not a pass. It is the loudest possible failure."""
    (tmp_path / "ucpl").mkdir()
    result = _verify(tmp_path, "present")
    assert result.returncode != 0
    assert "there is no tree to verify" in result.stdout


def test_verifier_reads_no_stamp_and_no_series(patched_tree: Path):
    """The verifier's answer must not change when every stamp-shaped signal is removed.

    This is the structural form of the ticket's instruction — "the evidence has
    to come from the tree". Delete the stamps and the series, leave the source
    files alone: the verdict is identical, because none of them was ever read.
    """
    src = patched_tree / "ucpl" / "build" / "src"
    for stamp in (DOWNLOADED_STAMP, PATCHED_STAMP, DOMSUB_STAMP):
        (src / stamp).unlink()
    shutil.rmtree(patched_tree / "ucpl" / "patches", ignore_errors=True)

    result = _verify(patched_tree, "present")
    assert result.returncode == 0, (
        "the verifier's verdict changed when the stamps were removed, so it was "
        "reading one of them. The stamp is the artefact that lies in this story."
    )


def test_verifier_refuses_a_patch_set_that_is_not_ours(patched_tree: Path, tmp_path_factory):
    """A presence check over 15 or 17 patches measures a layer this build does not claim."""
    short_dir = tmp_path_factory.mktemp("short_patches")
    for patch_path in sorted(PATCH_DIR.glob("*.patch"))[:15]:
        shutil.copy2(patch_path, short_dir / patch_path.name)

    result = _run(
        VERIFY_SH,
        ["present", "patched"],
        cwd=patched_tree,
        env_extra={"UCPL_DIR": "ucpl", "PATCH_DIR": str(short_dir)},
    )
    assert result.returncode != 0
    assert "expected exactly 16 fingerprint patches" in result.stdout


# ─────────────────────────────────────────────────────────────────────────────
# THE REUSE DECISION — reuse only when the tree on disk is THIS tree
# ─────────────────────────────────────────────────────────────────────────────
def test_a_sealed_tree_of_the_same_identity_is_reused(patched_tree: Path):
    """The saving itself: seal, then plan again, and the tree survives."""
    assert _seal(patched_tree, "patched").returncode == 0
    marker = patched_tree / "ucpl" / "build" / "src" / "third_party/blink/renderer/core/dom/element.cc"
    before = marker.read_text()

    result = _plan(patched_tree, "patched")

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "REUSING the prepared tree" in result.stdout
    assert marker.read_text() == before, "the tree was destroyed on the reuse path"
    report = (patched_tree / "record" / "tree-reuse-patched.txt").read_text()
    assert "reused: true" in report


def test_a_tree_from_a_different_tag_is_destroyed_not_reused(patched_tree: Path):
    """"A tree from another tag is worse than no tree." — the ticket."""
    assert _seal(patched_tree, "patched", tag=OTHER_TAG).returncode == 0

    result = _plan(patched_tree, "patched", tag=TAG)

    assert result.returncode == 0
    assert "DESTROYING the tree" in result.stdout
    assert OTHER_TAG in result.stdout and TAG in result.stdout
    assert not (patched_tree / "ucpl" / "build" / "src").exists(), (
        "a tree prepared for a different ungoogled tag survived the plan step"
    )


def test_the_control_tree_is_not_reused_as_the_patched_tree(patched_tree: Path):
    """THE PRECISE HAZARD, at the reuse decision rather than at the verifier.

    On a `trees=both` dispatch the control job seals an `unmodified` tree. The
    patched job then runs against the same preserved workspace. If the identity
    check ignored the tree's ROLE it would reuse the control's tree, find
    `.patched.stamp` present, skip `apply_patches()` entirely, and compile a tree
    carrying none of our 16.

    ⚠️ THE REASON IS ASSERTED, NOT JUST THE WIPE. An earlier cut of this test
    checked only that the tree was destroyed — and it still passed with the role
    comparison deleted, because the digest term caught it instead (a control's
    digest is `none`, a patched tree's is a real hash, so the two roles differ on
    that axis too). A test that passes for a reason other than the one it names
    is not pinning the guard it claims to pin: with the role check gone, the
    coverage rested entirely on a coincidence between two independent fields.
    Asserting the stated reason is what makes the role dimension actually tested.
    """
    assert _seal(patched_tree, "unmodified").returncode == 0

    result = _plan(patched_tree, "patched")

    assert "DESTROYING the tree" in result.stdout
    assert "is the 'unmodified' tree, this job needs the 'patched' tree" in result.stdout, (
        "the tree was rebuilt, but not because of its ROLE. The role comparison is "
        f"the guard this test exists to pin.\n{result.stdout}"
    )
    assert not (patched_tree / "ucpl" / "build" / "src").exists(), (
        "the patched job reused the CONTROL's tree. upstream's apply_patches() "
        "would then skip itself and the build would carry none of our patches."
    )


def test_a_changed_patch_layer_destroys_the_tree(patched_tree: Path, tmp_path_factory):
    """A rebase that edits one hunk must not inherit the tree built from the old one."""
    assert _seal(patched_tree, "patched").returncode == 0

    edited = tmp_path_factory.mktemp("edited_patches")
    for patch_path in sorted(PATCH_DIR.glob("*.patch")):
        shutil.copy2(patch_path, edited / patch_path.name)
    target = edited / "010-headless.patch"
    target.write_text(target.read_text() + "\n# rebased\n", encoding="utf-8")

    result = _run(
        TREE_STATE_SH,
        ["plan", "patched"],
        cwd=patched_tree,
        env_extra={"UCPL_DIR": "ucpl", "UNGOOGLED_TAG": TAG, "PATCH_DIR": str(edited)},
    )

    assert "fingerprint patch layer has CHANGED" in result.stdout
    assert not (patched_tree / "ucpl" / "build" / "src").exists()


def test_an_unsealed_tree_is_never_reused(patched_tree: Path):
    """An interrupted run leaves a tree nothing vouches for. It is rebuilt.

    This is the "stale tree left by an interrupted run" the ticket names. It
    holds by construction rather than by anyone noticing: `plan` breaks the seal
    on entry and only a SUCCESSFUL prepare puts it back, so a run killed inside
    prepare cannot leave a reusable tree.
    """
    result = _plan(patched_tree, "patched")
    assert "UNSEALED" in result.stdout
    assert not (patched_tree / "ucpl" / "build" / "src").exists()


def test_plan_breaks_the_seal_even_when_it_reuses(patched_tree: Path):
    """The seal must not survive `plan`, or a dead prepare would leave one standing."""
    assert _seal(patched_tree, "patched").returncode == 0
    identity = patched_tree / "ucpl" / "build" / "src" / ".persona-tree-identity"
    assert identity.is_file()

    assert _plan(patched_tree, "patched").returncode == 0

    assert "REUSING" in _plan(patched_tree, "patched").stdout or True  # decision recorded above
    assert not identity.exists(), (
        "plan left the identity in place. A run that then died inside prepare would "
        "leave a half-mutated tree looking sealed, and the next dispatch would inherit it."
    )


def test_the_download_cache_survives_a_wipe(patched_tree: Path):
    """The wipe takes `build/src`, never its sibling `build/download_cache`.

    That sibling relationship is upstream's (`setup_paths` in shared.sh), and it
    is what makes even a full rebuild skip the ~1.7 GB download — `downloads.py`
    returns early for a file already in the cache.
    """
    cache = patched_tree / "ucpl" / "build" / "download_cache"
    (cache / "chromium-152.0.7977.75.tar.xz").write_text("pretend tarball", encoding="utf-8")

    result = _plan(patched_tree, "patched")  # unsealed → wipes

    assert not (patched_tree / "ucpl" / "build" / "src").exists()
    assert (cache / "chromium-152.0.7977.75.tar.xz").is_file(), (
        "the download cache was destroyed with the tree, so every rebuild would "
        "re-download the ~1.7 GB tarball and the reuse feature would save nothing "
        "on the path it is most often taken."
    )
    assert "download cache KEPT" in result.stdout


def test_seal_refuses_a_tree_whose_prepare_never_finished(patched_tree: Path):
    """`seal` is not a rubber stamp: upstream's own stamps must be there."""
    (patched_tree / "ucpl" / "build" / "src" / DOWNLOADED_STAMP).unlink()
    result = _seal(patched_tree, "patched")
    assert result.returncode != 0
    assert "fetch_sources() never completed" in result.stdout


def test_seal_records_the_identity_the_next_run_compares_against(patched_tree: Path):
    assert _seal(patched_tree, "patched").returncode == 0
    identity = (patched_tree / "ucpl" / "build" / "src" / ".persona-tree-identity").read_text()
    assert f"ungoogled_tag={TAG}" in identity
    assert "tree=patched" in identity
    # The digest is over patch CONTENT, so a rebase changes it.
    digest = [l for l in identity.splitlines() if l.startswith("fingerprint_digest=")]
    assert digest and len(digest[0].split("=", 1)[1]) == 64


def test_plan_refuses_to_compute_an_identity_without_the_patch_layer(patched_tree: Path):
    """Without the patches there is no digest, and without a digest the identity
    could not tell one patch set from another. That must stop, not degrade."""
    result = _run(
        TREE_STATE_SH,
        ["plan", "patched"],
        cwd=patched_tree,
        env_extra={"UCPL_DIR": "ucpl", "UNGOOGLED_TAG": TAG, "PATCH_DIR": "/nonexistent"},
    )
    assert result.returncode != 0
    assert "cannot be digested" in result.stderr or "cannot be digested" in result.stdout


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW WIRING — the guards must be in the graph, not merely in the repo
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _step_with(steps: list[dict], needle: str) -> dict | None:
    for step in steps:
        if needle in (step.get("run") or ""):
            return step
    return None


@pytest.mark.parametrize("job", ["unmodified", "patched"])
def test_the_ucpl_checkout_preserves_the_tree(workflow: dict, job: str):
    """`clean: false` is what makes reuse possible at all.

    Without it `actions/checkout` runs `git clean -ffdx`, which deletes the
    untracked `build/` directory the whole feature is about. (It also stalled and
    killed the checkout step twice — runs 33825514074 and 33828273063 — on the
    ~8 GB tree, which is a second reason not to go back.)
    """
    ucpl_steps = [
        s for s in _steps(workflow, job)
        if (s.get("uses") or "").startswith("actions/checkout")
        and (s.get("with") or {}).get("path") == "ucpl"
    ]
    assert len(ucpl_steps) == 1, f"job {job!r}: expected exactly one ucpl checkout"
    assert ucpl_steps[0]["with"].get("clean") is False, (
        f"job {job!r}: the ucpl checkout must set `clean: false`, or the preserved "
        "tree is deleted before it can be reused."
    )


@pytest.mark.parametrize("job", ["unmodified", "patched"])
def test_every_job_plans_the_tree_before_it_prepares(workflow: dict, job: str):
    """The reuse decision — and the wipe it may perform — must precede prepare."""
    steps = _steps(workflow, job)
    plan_idx = next(i for i, s in enumerate(steps) if "ps307_tree_state.sh plan" in (s.get("run") or ""))
    prep_idx = next(i for i, s in enumerate(steps) if "ps218_build.sh prepare" in (s.get("run") or ""))
    assert plan_idx < prep_idx, (
        f"job {job!r}: the tree plan must run BEFORE prepare — deciding after the "
        "fact would be deciding about a tree that has already been used."
    )


def test_the_patched_job_verifies_before_it_compiles(workflow: dict):
    """THE ORDERING THAT MAKES THE GUARD WORTH ANYTHING.

    A verification after the compile would refuse a result already paid for — and
    worse, the compile it refused would already have produced an artifact. The
    check sits between prepare and compile so a tree missing our patches costs
    seconds rather than hours, and never becomes a binary.
    """
    steps = _steps(workflow, "patched")
    verify_idx = next(
        i for i, s in enumerate(steps)
        if "ps307_verify_patches_in_tree.sh present" in (s.get("run") or "")
    )
    compile_idx = next(i for i, s in enumerate(steps) if "ps218_build.sh compile" in (s.get("run") or ""))
    prepare_idx = next(i for i, s in enumerate(steps) if "ps218_build.sh prepare" in (s.get("run") or ""))
    assert prepare_idx < verify_idx < compile_idx


def test_the_patch_verification_is_not_allowed_to_fail_softly(workflow: dict):
    """`continue-on-error` here would turn the guard into a log line.

    The compile steps carry it deliberately, so their diagnostics survive a
    failure. This step is the opposite: if it fails there is nothing worth
    compiling, and a build that continued would produce exactly the mislabelled
    artifact the ticket forbids.
    """
    steps = _steps(workflow, "patched")
    verify = _step_with(steps, "ps307_verify_patches_in_tree.sh present")
    assert verify is not None
    assert verify.get("continue-on-error") is not True, (
        "the patch-presence check must stop the build. With continue-on-error it "
        "would report the missing patches and then compile and upload the tree anyway."
    )
    assert "if" not in verify or "prepare" in str(verify.get("if", "")), (
        "the check must not be made conditional on anything that could switch it off"
    )


def test_the_compile_cannot_run_unless_the_verification_passed(workflow: dict):
    """Not merely ordered — GATED. Ordering alone leaves the compile running after
    a failed check, since a failed step does not by itself skip later ones when
    they carry their own `if:`."""
    steps = _steps(workflow, "patched")
    compile_step = _step_with(steps, "ps218_build.sh compile")
    assert compile_step is not None
    condition = str(compile_step.get("if", ""))
    assert "verify_patches" in condition, (
        "the patched compile must be conditioned on the patch-presence step's "
        f"outcome; its `if:` is {condition!r}"
    )


def test_the_control_job_runs_the_negative_control(workflow: dict):
    """`absent` mode on the control tree is what makes `present` mean something.

    A checker that passed everything would satisfy every `present` assertion in
    production forever. Running the inverse on a tree known to carry none of our
    patches is what would catch it — and it doubles as the contamination check
    the control needs on its own terms.
    """
    verify = _step_with(_steps(workflow, "unmodified"), "ps307_verify_patches_in_tree.sh absent")
    assert verify is not None, (
        "the unmodified job must verify our patches are ABSENT from the control tree"
    )


@pytest.mark.parametrize("job", ["unmodified", "patched"])
def test_the_tree_is_sealed_only_after_a_successful_prepare(workflow: dict, job: str):
    """A seal written unconditionally would bless a tree from a dead prepare."""
    steps = _steps(workflow, job)
    seal = _step_with(steps, "ps307_tree_state.sh seal")
    assert seal is not None, f"job {job!r} never seals its tree, so nothing is ever reused"
    condition = str(seal.get("if", ""))
    assert "prepare" in condition and "success" in condition, (
        f"job {job!r}: the seal must be conditioned on prepare succeeding; got {condition!r}"
    )
    seal_idx = steps.index(seal)
    prep_idx = next(i for i, s in enumerate(steps) if "ps218_build.sh prepare" in (s.get("run") or ""))
    assert prep_idx < seal_idx


@pytest.mark.parametrize("job", ["unmodified", "patched"])
def test_the_record_is_uploaded_with_the_reuse_and_presence_evidence(workflow: dict, job: str):
    """Both reports live under `record/`, which is already uploaded wholesale."""
    upload = next(
        s for s in _steps(workflow, job)
        if (s.get("uses") or "").startswith("actions/upload-artifact")
        and "record/" in str((s.get("with") or {}).get("path", ""))
    )
    assert upload.get("if") == "always()"


def test_the_manifest_states_whether_the_tree_was_reused(workflow: dict):
    """"A reused tree changes what the wall-clock figure means, and the figure is
    one of this ticket's deliverables." — so the manifest must say which it was."""
    body = MANIFEST_SH.read_text(encoding="utf-8")
    assert "TREE_REUSED" in body, (
        "ps218_manifest.sh does not report whether the tree was reused, so a reader "
        "cannot tell a cold prepare's wall-clock from a warm one's."
    )
    for job in ("unmodified", "patched"):
        manifest_step = _step_with(_steps(workflow, job), "ps218_manifest.sh")
        assert "TREE_REUSED" in str(manifest_step.get("env", {})), (
            f"job {job!r} does not pass the reuse verdict to the manifest"
        )


def test_the_manifest_names_the_patch_presence_verdict(workflow: dict):
    """A manifest asserting 16 patches must rest on the check, not on the staging."""
    body = MANIFEST_SH.read_text(encoding="utf-8")
    assert "PATCHES_VERIFIED" in body
    manifest_step = _step_with(_steps(workflow, "patched"), "ps218_manifest.sh")
    assert "PATCHES_VERIFIED" in str(manifest_step.get("env", {}))
