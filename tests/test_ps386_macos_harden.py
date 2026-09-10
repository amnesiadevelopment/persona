#!/usr/bin/env python3
"""Tests for PS-386's macOS hardening seam and its posture verifier.

WHAT THESE CAN AND CANNOT COVER, STATED UP FRONT
------------------------------------------------
⛔ These tests CANNOT prove the app launches under the hardened runtime. That is
outcome 4 of PS-386 and it needs a real Mac: a build, an `open`, and an observed
engine spawn. Nothing here substitutes for it, and a green run of this file must
never be reported as evidence for it — the ticket's own words for that mistake
are "a green flag in a build log".

⛔ THEY ALSO CANNOT RUN `codesign`. This suite runs on Linux, so every test of
the hardener drives its PLANNING (what would be signed, in what order, at what
granularity) rather than its signing. That is a real bound, and it is why the
verifier below matters: the verifier drives PS-346's readers over REAL Mach-O
bytes, so the "did the posture land?" question is covered by measurement of
bytes even though the signing step itself is not exercised here.

What they DO cover:

  * the two entitlement declarations (pyproject's and the script's) cannot drift
    apart without a test failing;
  * `get-task-allow` is declared false in both;
  * `cs.allow-jit` is still declared TRUE — the blanket-removal guard, because
    the bundled CPython very probably needs it and removing it would trade a
    notarization prerequisite for an app that does not start;
  * ⭐ nested bundles are signed AT THEIR ROOT and deepest-first, which is what
    keeps `codesign --verify --deep` from failing on a stale seal;
  * the verifier reads the hardened-runtime flag and `get-task-allow` out of
    real Mach-O bytes, scopes its verdict to OUR slices, and refuses to call an
    unreadable slice clean;
  * the script refuses rather than pretends when it cannot do its job;
  * PS-346's instrument is untouched (it is the control for this change).
"""

from __future__ import annotations

import importlib.util
import re
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ps386_macos_harden.py"
VERIFIER = REPO / "scripts" / "ps386_verify_posture.py"
CONTROL = REPO / "scripts" / "ps346_signing_state.py"
PYPROJECT = REPO / "pyproject.toml"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load(SCRIPT, "ps386_macos_harden")


@pytest.fixture(scope="module")
def verifier():
    return _load(VERIFIER, "ps386_verify_posture")


@pytest.fixture(scope="module")
def declared() -> dict:
    with open(PYPROJECT, "rb") as fh:
        data = tomllib.load(fh)
    return data["tool"]["flet"]["macos"]["entitlement"]


# ── Mach-O fixture ───────────────────────────────────────────────────────────
#
# Deliberately the SAME shape as tests/test_ps346_signing_state.py's fixture:
# these tests drive the control's readers, so the bytes they are driven with
# must be the bytes those readers were hardened against. A simplified fixture
# here would test a reader nobody ships.


def _make_macho(
    *,
    adhoc: bool = True,
    cms_payload: bytes = b"",
    hardened: bool = False,
    entitlements: bytes | None = None,
) -> bytes:
    ident = b"dev.persona.test\0"

    flags = (0x2 if adhoc else 0) | (0x10000 if hardened else 0)
    cd_body = bytearray(64 + len(ident))
    struct.pack_into(">I", cd_body, 0, 0xFADE0C02)
    struct.pack_into(">I", cd_body, 8, 0x20400)
    struct.pack_into(">I", cd_body, 12, flags)
    struct.pack_into(">I", cd_body, 20, 64)
    struct.pack_into(">I", cd_body, 48, 0)
    cd_body[64 : 64 + len(ident)] = ident
    struct.pack_into(">I", cd_body, 4, len(cd_body))
    cd = bytes(cd_body)

    cms = struct.pack(">II", 0xFADE0B01, 8 + len(cms_payload)) + cms_payload

    ent = (
        struct.pack(">II", 0xFADE7171, 8 + len(entitlements)) + entitlements
        if entitlements is not None
        else b""
    )
    count = 3 if ent else 2
    header_len = 12 + count * 8
    cd_off = header_len
    cms_off = cd_off + len(cd)
    ent_off = cms_off + len(cms)
    total = ent_off + len(ent)
    sb = bytearray()
    sb += struct.pack(">III", 0xFADE0CC0, total, count)
    sb += struct.pack(">II", 0, cd_off)
    sb += struct.pack(">II", 0x10000, cms_off)
    if ent:
        sb += struct.pack(">II", 5, ent_off)
    sb += cd + cms + ent
    superblob = bytes(sb)

    lc = bytearray(16)
    struct.pack_into("<II", lc, 0, 0x1D, 16)
    sig_off = 32 + len(lc)
    struct.pack_into("<II", lc, 8, sig_off, len(superblob))

    mh = bytearray(32)
    struct.pack_into("<I", mh, 0, 0xFEEDFACF)
    struct.pack_into("<i", mh, 4, 0x0100000C)
    struct.pack_into("<I", mh, 16, 1)
    return bytes(mh) + bytes(lc) + superblob


def _ents(get_task_allow: bool) -> bytes:
    value = "true" if get_task_allow else "false"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        '<plist version="1.0"><dict>'
        "<key>com.apple.security.cs.allow-jit</key><true/>"
        f"<key>com.apple.security.get-task-allow</key><{value}/>"
        "</dict></plist>"
    ).encode()


def _streams(res) -> str:
    """Both captured streams as one string, treating None as empty.

    ⚠️ `capture_output=True` still yields `None` for a stream when the child
    produced nothing decodable, and `"x" in None` is a TypeError, not a failed
    assertion. That distinction matters here: a TypeError inside a REFUSAL test
    reports as an error in the test rather than as the refusal being absent, so
    the real signal is buried. Measured on CI run 34475127591 (windows leg).

    Reading both streams is also the honest check — the assertions below care
    that the refusal was SAID, not which fd it went to.
    """
    return (res.stdout or "") + (res.stderr or "")


def _app(tmp_path: Path, name: str = "persona.app") -> Path:
    app = tmp_path / name
    (app / "Contents" / "MacOS").mkdir(parents=True)
    return app


# ── the drift guard ──────────────────────────────────────────────────────────


def test_pyproject_and_script_entitlements_are_identical(mod, declared):
    """The two declarations must agree key-for-key.

    They exist separately for a real reason — pyproject's states intent into the
    generated plist, the script's is what codesign actually applies — and that
    separation is exactly what lets a future editor change one and ship the
    other. This is the test that makes that impossible.
    """
    assert declared == mod.ENTITLEMENTS, (
        "pyproject's [tool.flet.macos.entitlement] and "
        "ps386_macos_harden.ENTITLEMENTS have drifted apart"
    )


def test_the_drift_guard_can_actually_fail(mod, declared):
    """The guard above is worthless unless it FAILS on a real drift.

    A comparison of a thing with itself passes whatever is wrong, so this
    perturbs one key and asserts the comparison notices.
    """
    drifted = dict(declared)
    drifted["com.apple.security.get-task-allow"] = True
    assert drifted != mod.ENTITLEMENTS


def test_get_task_allow_is_declared_false_in_both_places(mod, declared):
    """The blocker this whole slice exists to remove."""
    key = "com.apple.security.get-task-allow"
    assert declared[key] is False
    assert mod.ENTITLEMENTS[key] is False


def test_allow_jit_is_still_true(mod, declared):
    """⛔ THE BLANKET-REMOVAL FENCE.

    PS-386 is explicit: do not sweep entitlements to make a checker green.
    persona ships a bundled CPython and `cs.allow-jit` was already true in the
    shipped plist, which is evidence the behaviour is live. Removing it would
    trade a notarization prerequisite for an app that does not start.
    """
    key = "com.apple.security.cs.allow-jit"
    assert declared[key] is True
    assert mod.ENTITLEMENTS[key] is True


def test_flets_own_five_defaults_are_all_still_present(mod):
    """We ADD to flet's defaults; we do not silently drop one.

    flet-cli 0.85.3 seeds exactly these five (build_base.py:816-822) and merges
    ours OVER them. Dropping one here would not remove it from the build — it
    would leave flet's value in place while this script's blob disagreed, which
    is the drift that makes the artifact unexplainable.
    """
    for key in (
        "com.apple.security.app-sandbox",
        "com.apple.security.cs.allow-jit",
        "com.apple.security.network.client",
        "com.apple.security.network.server",
        "com.apple.security.files.user-selected.read-write",
    ):
        assert key in mod.ENTITLEMENTS


def test_the_set_is_exactly_flets_five_plus_get_task_allow(mod):
    """No sixth idea sneaks in unnoticed.

    Any entitlement beyond this set is a widening of the app's runtime
    permissions and must be a deliberate, argued change — not a quiet addition
    made to get past a failing check.
    """
    assert len(mod.ENTITLEMENTS) == 6
    assert "com.apple.security.get-task-allow" in mod.ENTITLEMENTS


# ── refusal behaviour ────────────────────────────────────────────────────────


def test_check_pyproject_runs_anywhere_and_passes():
    """The drift check must work off-Mac — it is the only part CI can run."""
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--check-pyproject"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res.returncode == 0, res.stderr


def test_a_bad_app_path_refuses_with_2_not_0(tmp_path):
    """A path that is not an .app must not read as success."""
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--app", str(tmp_path / "nope.app")],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res.returncode == 2
    assert "CANNOT RUN" in _streams(res)


@pytest.mark.skipif(sys.platform == "darwin", reason="this asserts the non-Mac refusal")
def test_non_darwin_refuses_rather_than_claiming_success(tmp_path):
    """⚠️ EXIT 2 IS NOT A PASS — the question was not asked.

    The dangerous failure here is a script that quietly does nothing on Linux
    and exits 0, which would let CI report a hardened build it never hardened.
    """
    fake = _app(tmp_path)
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--app", str(fake)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res.returncode == 2
    blob = _streams(res)
    assert "NOT a pass" in blob or "CANNOT RUN" in blob


# ── Mach-O detection ─────────────────────────────────────────────────────────


def test_macho_detection_reads_magic_not_extension(mod, tmp_path):
    """Slices that matter have no useful extension; magic is the only reader."""
    macho = tmp_path / "no_extension_at_all"
    macho.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 64)
    assert mod.is_macho(macho) is True

    fat = tmp_path / "fat_binary"
    fat.write_bytes(b"\xca\xfe\xba\xbe" + b"\0" * 64)
    assert mod.is_macho(fat) is True

    text = tmp_path / "script.dylib"  # a lying extension
    text.write_text("#!/bin/sh\necho not a macho\n", encoding="utf-8")
    assert mod.is_macho(text) is False

    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert mod.is_macho(empty) is False


# ── ⭐ bundle awareness: the seal hazard ─────────────────────────────────────


def test_a_nested_framework_is_signed_at_its_root_not_at_its_binary(mod, tmp_path):
    """⭐ THE SEAL HAZARD, AND THE REASON THIS SCRIPT IS BUNDLE-AWARE.

    `codesign` seals a bundle over its contents — PS-346 measured 20 such seals
    inside this one `.app`. Re-signing a framework's INNER BINARY as a loose
    file rewrites bytes that the framework's own seal already committed to, so
    the seal goes stale and `codesign --verify --deep --strict` fails. Signing
    at the BUNDLE ROOT makes codesign rewrite the seal instead.

    So the framework root must be in the plan, and its main binary must NOT be.
    """
    app = _app(tmp_path)
    fw = app / "Contents" / "Frameworks" / "serious_python_darwin.framework"
    (fw / "Versions" / "A").mkdir(parents=True)
    binary = fw / "Versions" / "A" / "serious_python_darwin"
    binary.write_bytes(_make_macho())

    items, bundles = mod.plan(app)

    assert fw in items, "the framework root must be signed"
    assert fw in bundles
    assert binary not in items, (
        "the framework's main binary must NOT be signed as a loose file — that "
        "is what invalidates the framework's own seal"
    )


def test_the_seal_hazard_guard_can_fail(mod, tmp_path):
    """The guard above is only worth something if a loose binary IS collected.

    Without this, `binary not in items` could pass simply because the planner
    collects nothing at all. A dylib beside the framework binary — not any
    bundle's main executable — must be planned.
    """
    app = _app(tmp_path)
    fw = app / "Contents" / "Frameworks" / "serious_python_darwin.framework"
    (fw / "Versions" / "A").mkdir(parents=True)
    (fw / "Versions" / "A" / "serious_python_darwin").write_bytes(_make_macho())
    loose = fw / "Versions" / "A" / "libpython3.12.dylib"
    loose.write_bytes(_make_macho())

    items, _ = mod.plan(app)
    assert loose in items, "a loose dylib inside a framework must still be signed"


def test_items_are_ordered_deepest_first(mod, tmp_path):
    """⚠️ Ordering is load-bearing, not cosmetic.

    codesign seals a bundle over its contents, so an outer bundle signed before
    its nested code has its seal invalidated the instant the inner code changes.
    """
    app = _app(tmp_path)
    fw = app / "Contents" / "Frameworks" / "Inner.framework"
    (fw / "Versions" / "A").mkdir(parents=True)
    inner_lib = fw / "Versions" / "A" / "libinner.dylib"
    inner_lib.write_bytes(_make_macho())
    shallow = app / "Contents" / "MacOS" / "libshallow.dylib"
    shallow.write_bytes(_make_macho())

    items, _ = mod.plan(app)
    assert items.index(inner_lib) < items.index(fw), (
        "a framework's contents must be signed before the framework itself"
    )
    assert items.index(inner_lib) < items.index(shallow)


def test_entitlements_go_on_bundles_and_not_on_loose_dylibs(mod, tmp_path):
    """⚠️ Entitlements describe a PROCESS, so they belong on the executable.

    Stamping an entitlements blob onto each of ~200 loose dylibs would invent
    ~200 blobs where the shipped artifact has three, and each one is a new place
    for `get-task-allow` to be re-injected. PS-346 measured entitlements on 3
    slices of 225, which is the shape this preserves.
    """
    ents = tmp_path / "e.plist"
    bundle_cmd = mod.sign_cmd(tmp_path / "Some.framework", ents)
    loose_cmd = mod.sign_cmd(tmp_path / "libfoo.dylib", None)

    assert "--entitlements" in bundle_cmd
    assert "--entitlements" not in loose_cmd
    for cmd in (bundle_cmd, loose_cmd):
        assert "--options" in cmd and "runtime" in cmd, "the runtime option is the point"


# ── ⭐ four-valued classification: adhoc / unsigned / theirs / unknown ───────


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# The exact stderr real `codesign -dvvv` emits for a Mach-O carrying no
# signature. Written once, used by every test about that population, because a
# paraphrase here would test a string Apple does not print.
CODESIGN_UNSIGNED_STDERR = (
    "/tmp/x/aiohttp/_http_parser.cpython-312-darwin.so: code object is not signed at all\n"
)


def test_an_adhoc_slice_classifies_as_ours(mod, monkeypatch, tmp_path):
    """No Authority line and a clean exit means nobody with a certificate signed it."""
    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stderr="Signature=adhoc\n")
    )
    state, _ = mod.classify(tmp_path / "libfoo.dylib")
    assert state == "ADHOC"
    assert state in mod.OURS_STATES, "an ad-hoc slice is one we produced"


def test_an_unsigned_slice_is_OURS_and_gets_hardened(mod, monkeypatch, tmp_path):
    """⭐ THE REGRESSION THIS TEST EXISTS FOR — 28 of 225 slices, all ours.

    `codesign -dvvv` EXITS NON-ZERO on a Mach-O with no signature: it prints
    `code object is not signed at all` and returns 1. A classifier that decides
    "readable" on the exit code therefore files a measured answer under "could
    not tell", and every one of those slices is skipped instead of hardened.

    That is not a corner case. PS-346 measured this exact bundle
    (`readings/ps346-2026-09-07/artifacts/signing_state_run.txt:9`):

        arch slices: ADHOC=196, UNSIGNED=28, SIGNED_CMS=1, UNREADABLE=0

    and named the 28 (`REPORT.md:124-125`) as the compiled Python extensions —
    frozen into our bundle by our build, carrying no signature, and therefore
    the population most in need of the hardened runtime.

    ⚠️ The second half is worse than a skip: `ps386_verify_posture.py` splits on
    the CMS payload, so it counts those same slices as OURS, finds no runtime
    flag, and fails the release gate. The hardener saying "not ours" while its
    own verifier says "ours, and they failed" is the disagreement that turns a
    silent shortfall into a red build with no dmg.
    """
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(1, stderr=CODESIGN_UNSIGNED_STDERR),
    )
    state, detail = mod.classify(tmp_path / "_http_parser.cpython-312-darwin.so")
    assert state == "UNSIGNED", "a measured 'no signature' is an ANSWER, not an error"
    assert state in mod.OURS_STATES, "an unsigned slice of ours must be hardened"
    assert "not signed at all" in detail


def test_a_real_identity_classifies_as_third_party_and_names_it(mod, monkeypatch, tmp_path):
    """Node.js Foundation's `node` is the one real identity in this bundle.

    Re-signing it would DESTROY a genuine signature and replace it with our
    ad-hoc nothing, so it must be recognised — and NAMED, so a reader can tell
    which third party it belonged to rather than trusting the count.
    """
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(
            0, stderr="Authority=Developer ID Application: Node.js Foundation (HX7739G8FX)\n"
        ),
    )
    state, detail = mod.classify(tmp_path / "node")
    assert state == "THIRD_PARTY"
    assert state not in mod.OURS_STATES, "a real signature must never be overwritten"
    assert "Node.js Foundation" in detail


def test_a_nonzero_codesign_exit_is_UNREADABLE_and_never_OURS(mod, monkeypatch, tmp_path):
    """⭐ THE DEFECT THIS CLASSIFIER EXISTS TO FIX, AND IT IS STILL COVERED.

    `subprocess.run` without `check=True` does NOT raise on a non-zero exit, so
    a `codesign` that errored on a single slice used to fall through the
    `Authority=` test to False — and get RE-SIGNED as ours.

    ⚠️ Promoting the ONE self-describing non-zero exit (`not signed at all`) to
    a real answer must not re-open this. An exit that says something else still
    means we DO NOT KNOW, and must not read as ours.
    """
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(1, stderr="test.dylib: invalid format\n"),
    )
    state, detail = mod.classify(tmp_path / "weird.dylib")
    assert state == "UNREADABLE", "an errored probe must not be treated as ours"
    assert state not in mod.OURS_STATES
    assert "exit 1" in detail


def test_a_missing_codesign_is_UNREADABLE_not_OURS(mod, monkeypatch, tmp_path):
    """The original fail-safe's one covered case — still covered."""

    def boom(*a, **k):
        raise OSError("codesign not found")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    state, _ = mod.classify(tmp_path / "x.dylib")
    assert state == "UNREADABLE"
    assert state not in mod.OURS_STATES


def test_the_four_states_are_genuinely_distinct(mod, monkeypatch, tmp_path):
    """A four-valued fact must not render as three.

    Without this, two arms could collapse to the same string and every test
    above would still pass individually — which is exactly how UNSIGNED came to
    be reported as UNREADABLE while four separate tests stayed green.

    ⭐ FOUR, NOT THREE, AND THE COUNT IS NOT ARBITRARY: it is the control's own
    vocabulary. `read_macho_slice` in `scripts/ps346_signing_state.py` reports
    ADHOC / UNSIGNED / SIGNED_CMS / UNREADABLE, and PS-346's committed tally is
    stated in all four. A classifier with fewer buckets than the instrument it
    is measured against has to merge two populations to fit, and the merge is
    silent.
    """
    seen = set()
    for rc, err in (
        (0, "Signature=adhoc\n"),
        (0, "Authority=Developer ID Application: Someone\n"),
        (1, CODESIGN_UNSIGNED_STDERR),
        (1, "broken\n"),
    ):
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, _rc=rc, _e=err, **k: _FakeCompleted(_rc, stderr=_e))
        seen.add(mod.classify(tmp_path / "x")[0])
    assert seen == {"ADHOC", "UNSIGNED", "THIRD_PARTY", "UNREADABLE"}


def test_the_two_ours_states_are_exactly_the_two_the_control_calls_ours(mod):
    """⛔ THE HARDENER AND ITS VERIFIER MUST AGREE ON WHO OWNS A SLICE.

    `ps386_verify_posture.py::split_ours` keys on the CMS payload — anything
    that is not `SIGNED_CMS` is ours — and it is right to, because a real
    identity is the only thing that makes a slice somebody else's. The hardener
    reaches the same split from the other side, through `codesign`, and the two
    must name the same population.

    If they drift, the failure is not symmetric: the hardener skipping a slice
    the verifier counts is a RED BUILD with no dmg, discovered on a Mac session
    that is expensive to get. This pins the two halves together.
    """
    assert set(mod.OURS_STATES) == {"ADHOC", "UNSIGNED"}
    control = CONTROL.read_text(encoding="utf-8")
    for state in mod.OURS_STATES:
        assert f'"{state}"' in control, (
            f"{state} is not a state the control reports — the vocabularies have drifted"
        )


def test_an_unreadable_slice_is_not_silently_folded_into_third_party(mod):
    """⚠️ SKIPPED FOR TWO DIFFERENT REASONS MUST READ DIFFERENTLY.

    Both an unreadable slice and a third-party one are left alone, so the
    SIGNING decision is the same. The REPORTING must not be: a count that mixes
    "Node.js Foundation signed this" with "I could not read this" gives the
    liaison a number with no way to tell a legitimate skip from a blind one.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "UNREADABLE (we could not tell)" in src
    assert "THIRD_PARTY (a real certificate)" in src
    assert "not over the bundle" in src, (
        "the success line must not claim completeness over slices it never classified"
    )


def test_the_ours_count_is_reported_split_and_not_only_as_a_total(mod):
    """A single OURS total cannot show the regression that produced this test.

    28 unsigned slices vanishing into UNREADABLE moves the OURS total by 28 and
    says nothing about WHY. Printing `ADHOC=n, UNSIGNED=m` makes the count
    readable directly against PS-346's committed tally, where an UNSIGNED of 0
    on this bundle is visibly wrong rather than merely lower than expected.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "ours_by_state" in src, "the OURS bucket must be counted per state"
    assert "OURS ({split})" in src or "{split}" in src, (
        "the per-state split must reach the printed line, not just a variable"
    )


def test_the_docstring_does_not_claim_windows_binaries_are_in_this_bundle(mod):
    """⛔ CROSS-ASSET CONFLATION — a wrong justification for a right guard.

    PS-346 measured SIGNED_CMS=1 in the macOS bundle: Node.js Foundation's
    `node`. Microsoft's signed `d3dcompiler_47.dll` / `dxil.dll` are real, but
    they are PE files in the WINDOWS engine zip and are not in this bundle at
    all. A guard justified by a fact about a different asset is a guard someone
    later removes on finding the fact false.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "ONE identity, not two" in src
    assert "WINDOWS engine zip" in src


# ── ⭐ the verifier: does the posture land in the BYTES? ─────────────────────


def test_verifier_reads_the_hardened_flag_out_of_real_macho_bytes(verifier, tmp_path):
    """The measurement that the hardener structurally cannot make.

    `codesign` exiting 0 says the tool accepted its arguments. Whether the
    produced CodeDirectory carries CS_RUNTIME is a question about bytes.
    """
    app = _app(tmp_path)
    (app / "Contents" / "MacOS" / "persona").write_bytes(
        _make_macho(hardened=True, entitlements=_ents(False))
    )

    control = verifier.load_control()
    rows = verifier.read_app(app, control)
    ours, theirs = verifier.split_ours(rows)
    ok, reasons = verifier.verdict(ours)

    assert ours and all(r["hardened_runtime"] for r in ours)
    assert ok, reasons


def test_verifier_fails_when_the_runtime_flag_is_missing(verifier, tmp_path):
    """The negative case — without it the check above proves nothing."""
    app = _app(tmp_path)
    (app / "Contents" / "MacOS" / "persona").write_bytes(
        _make_macho(hardened=False, entitlements=_ents(False))
    )

    control = verifier.load_control()
    ours, _ = verifier.split_ours(verifier.read_app(app, control))
    ok, reasons = verifier.verdict(ours)

    assert not ok
    assert any("hardened runtime" in r for r in reasons)


def test_verifier_fails_when_get_task_allow_is_still_true(verifier, tmp_path):
    """The OTHER blocker, measured out of the entitlements slot.

    ⭐ This is the case that matters most in practice: ad-hoc `codesign` injects
    `get-task-allow` itself, so a build can carry the hardened runtime and STILL
    be rejected. A verifier that only read the runtime flag would pass it.
    """
    app = _app(tmp_path)
    (app / "Contents" / "MacOS" / "persona").write_bytes(
        _make_macho(hardened=True, entitlements=_ents(True))
    )

    control = verifier.load_control()
    ours, _ = verifier.split_ours(verifier.read_app(app, control))
    ok, reasons = verifier.verdict(ours)

    assert not ok
    assert any("get-task-allow" in r for r in reasons)


def test_verifier_scopes_its_verdict_to_our_slices_only(verifier, tmp_path):
    """⭐ THE HONEST-TARGET RULE.

    A vendored binary carrying a REAL identity (Node.js Foundation's `node` is
    the one in this bundle) is not ours, was not signed by us, and must not be
    counted against us — nor may we claim credit for it. PS-386 forbids a
    bundle-wide 225/225 figure for exactly this reason.

    So: a third-party slice with NO hardened runtime must not fail our verdict.
    """
    app = _app(tmp_path)
    (app / "Contents" / "MacOS" / "persona").write_bytes(
        _make_macho(hardened=True, entitlements=_ents(False))
    )
    vendored = app / "Contents" / "Resources" / "node"
    vendored.parent.mkdir(parents=True, exist_ok=True)
    vendored.write_bytes(_make_macho(adhoc=False, cms_payload=b"\x30\x82fake", hardened=False))

    control = verifier.load_control()
    ours, theirs = verifier.split_ours(verifier.read_app(app, control))

    assert len(theirs) == 1, "the CMS-signed slice is theirs"
    assert all("node" not in r["path"] for r in ours)

    ok, reasons = verifier.verdict(ours)
    assert ok, reasons


def test_verifier_refuses_a_verdict_when_ZERO_slices_are_ours(verifier, tmp_path):
    """⛔ `0/0` MUST NOT READ AS SUCCESS — a guard that measured nothing passed.

    Every check in `verdict()` is a "no offenders found" test, so over an EMPTY
    list every one of them is vacuously satisfied and the function returns
    `(True, [])`. A bundle whose Mach-Os are all third-party would therefore
    print `hardened runtime, OUR slices: 0/0` and exit 0 — a green build that
    asserted nothing about anything we produce.

    ⚠️ THIS IS UNREACHABLE FOR PERSONA TODAY and is refused anyway. PS-346
    measured 113 files with exactly one SIGNED_CMS, so `ours` cannot be empty on
    this bundle — but that is a fact about the BUNDLE, not about this function,
    and it would stop being true the moment the vendored set changed. PS-386's
    own acceptance criterion is that "a guard only ever observed passing is
    indistinguishable from a broken one"; `0/0 -> PASS` is precisely that shape.

    ⛔ Distinct from the no-Mach-O case, which exits 2 ("nothing was read").
    Here the bundle WAS read and every slice in it belonged to someone else.
    """
    # Direct, because the state is about the list rather than about bytes.
    ok, reasons = verifier.verdict([])
    assert not ok, "zero judged slices must not be a pass"
    assert any("0 slices" in r or "not a pass" in r.lower() for r in reasons), (
        f"the refusal must say WHY it is not a pass; got {reasons}"
    )

    # And through the real reader, over a bundle whose only Mach-O is theirs.
    app = _app(tmp_path)
    vendored = app / "Contents" / "Resources" / "node"
    vendored.parent.mkdir(parents=True, exist_ok=True)
    vendored.write_bytes(
        _make_macho(adhoc=False, cms_payload=b"\x30\x82fake", hardened=False)
    )
    control = verifier.load_control()
    ours, theirs = verifier.split_ours(verifier.read_app(app, control))
    assert len(theirs) == 1 and ours == [], "precondition: everything is theirs"
    ok, reasons = verifier.verdict(ours)
    assert not ok, "the all-third-party bundle must not pass either"


def test_verifier_refuses_to_call_an_unreadable_entitlements_blob_clean(
    verifier, tmp_path
):
    """⚠️ THREE-VALUED, NOT TWO.

    The control returns None for "no blob, or unparseable", which is NOT the
    claim that the entitlement is absent. A slice carrying a blob we could not
    parse must fail the gate — an unread blob is not a clean one. This is the
    exact false-CLEAN shape PS-346's instrument was hardened against.
    """
    app = _app(tmp_path)
    (app / "Contents" / "MacOS" / "persona").write_bytes(
        _make_macho(hardened=True, entitlements=b"\x00\x01not a plist at all")
    )

    control = verifier.load_control()
    ours, _ = verifier.split_ours(verifier.read_app(app, control))
    ok, reasons = verifier.verdict(ours)

    assert not ok
    assert any("could not be parsed" in r for r in reasons)


def test_verifier_does_not_fail_a_slice_with_no_entitlements_blob(verifier, tmp_path):
    """The other side of the three-valued reading.

    Most loose dylibs carry no entitlements blob at all, by design. Treating
    "no blob" as a failure would make the gate unpassable and would push a
    future editor toward stamping blobs everywhere — the regression the hardener
    deliberately avoids.
    """
    app = _app(tmp_path)
    (app / "Contents" / "MacOS" / "libfoo.dylib").write_bytes(
        _make_macho(hardened=True, entitlements=None)
    )

    control = verifier.load_control()
    ours, _ = verifier.split_ours(verifier.read_app(app, control))
    ok, reasons = verifier.verdict(ours)

    assert ok, reasons


def test_verifier_refuses_an_empty_bundle_rather_than_passing_it(tmp_path):
    """⛔ NOTHING READ IS NOT A PASS.

    A bundle with no Mach-O in it means the build produced nothing, or the path
    is wrong. Exit 0 there would be the platonic false-CLEAN: a green gate over
    an artifact nobody looked at.
    """
    app = _app(tmp_path)
    res = subprocess.run(
        [sys.executable, str(VERIFIER), "--app", str(app)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res.returncode == 2
    assert "NOT a pass" in _streams(res)


def test_verifier_exit_codes_are_distinct_end_to_end(tmp_path):
    """0 / 1 / 2 must be three different answers, driven through the real CLI.

    The unit tests above drive `verdict()`; this drives the process, because the
    CI gate reads the exit code and nothing else.
    """
    good = _app(tmp_path / "good")
    (good / "Contents" / "MacOS" / "persona").write_bytes(
        _make_macho(hardened=True, entitlements=_ents(False))
    )
    bad = _app(tmp_path / "bad")
    (bad / "Contents" / "MacOS" / "persona").write_bytes(
        _make_macho(hardened=False, entitlements=_ents(True))
    )

    ok = subprocess.run(
        [sys.executable, str(VERIFIER), "--app", str(good)], capture_output=True, text=True, encoding="utf-8"
    )
    fail = subprocess.run(
        [sys.executable, str(VERIFIER), "--app", str(bad)], capture_output=True, text=True, encoding="utf-8"
    )
    missing = subprocess.run(
        [sys.executable, str(VERIFIER), "--app", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert fail.returncode == 1
    assert missing.returncode == 2


def test_verifier_states_plainly_that_a_pass_is_not_notarized(tmp_path):
    """PS-386 outcome 5: what remains gated on the purchase is stated plainly.

    The failure this prevents is a green line in a build log being read — by a
    human skimming, or by a later agent quoting it — as "macOS is signed now".
    """
    app = _app(tmp_path)
    (app / "Contents" / "MacOS" / "persona").write_bytes(
        _make_macho(hardened=True, entitlements=_ents(False))
    )
    res = subprocess.run(
        [sys.executable, str(VERIFIER), "--app", str(app)], capture_output=True, text=True, encoding="utf-8"
    )
    assert res.returncode == 0
    assert "NOT A SIGNED OR NOTARIZED ARTIFACT" in res.stdout
    assert "NOT OUTCOME 4" in res.stdout


# ── the control must stay untouched ──────────────────────────────────────────


def test_ps346_instrument_is_not_modified_by_this_work():
    """⛔ PS-346's instrument is the CONTROL for this change.

    It was hardened through five rework rounds specifically against false-CLEAN
    readings, and re-touching its readers invalidates the before/after
    comparison this whole slice rests on. Adding a CALLER is fine; changing a
    reader is not.

    This asserts the reader functions PS-386 depends on still exist with their
    names intact — a rename would silently break both the verifier and the
    liaison's runbook.
    """
    src = CONTROL.read_text(encoding="utf-8")
    for symbol in (
        "def entitlement_get_task_allow",
        "def read_macho_slice",
        "def classify_tally",
        "def macho_slices",
    ):
        assert symbol in src, f"{symbol} missing — the control's readers changed"


def test_the_hardener_does_not_import_or_wrap_the_control(mod):
    """The SIGNER and the READER stay independent.

    If the thing that changes the bytes also produced the reading, a bug in it
    could move both together and the reading would confirm the change rather
    than test it. The verifier imports the control; the hardener must not.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    stripped = src.replace("scripts/ps346_signing_state.py", "").replace(
        "ps346_signing_state.py", ""
    )
    assert "ps346_signing_state" not in stripped, "the hardener must not import the control"


def test_the_verifier_uses_the_controls_readers_and_not_its_own(verifier):
    """⛔ NO SECOND READER — PS-386 says so, and the reason is not stylistic.

    A fresh parser written by the same change is a parser that agrees with the
    change. The verifier must get its signing facts from the audited control,
    and must fail loudly if that control's readers are renamed away.
    """
    src = VERIFIER.read_text(encoding="utf-8")
    for symbol in ("macho_slices", "read_macho_slice", "entitlement_get_task_allow"):
        assert symbol in src

    module = verifier.load_control()
    assert module.__file__ and module.__file__.endswith("ps346_signing_state.py")

    # Not a re-implementation: the flag constants and the bit test live in the
    # control. Checked against the PARSED module rather than the raw text,
    # because the docstring legitimately NAMES `CS_RUNTIME` when explaining what
    # is being measured — a substring search would fail on the explanation.
    import ast

    tree = ast.parse(src)
    assigned = {
        t.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    for constant in ("CS_RUNTIME", "CS_ADHOC", "SLOT_ENTITLEMENTS", "SLOT_CMS"):
        assert constant not in assigned, (
            f"the verifier re-declares {constant} — it must read the control's"
        )
    assert not any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd)
        for node in ast.walk(tree)
    ), "the verifier must not hand-roll a flag bit test; the control reads flags"


def test_the_verifier_refuses_when_the_controls_readers_are_gone(verifier, tmp_path, monkeypatch):
    """The import guard must FAIL, not fall back to something homemade.

    If a future change renames a reader, the honest outcome is a loud refusal —
    not a verifier that quietly starts measuring with its own parser.
    """
    stub = tmp_path / "ps346_signing_state.py"
    stub.write_text("def macho_slices(d):\n    return []\n", encoding="utf-8")
    monkeypatch.setattr(verifier, "CONTROL", stub)
    with pytest.raises(ImportError, match="read_macho_slice"):
        verifier.load_control()


# ── no credential, ever ──────────────────────────────────────────────────────


def test_neither_script_takes_an_identity_and_both_sign_adhoc_only():
    """⛔ No credential is acquired, held, placed or echoed.

    The only signing identity available is `-` (ad-hoc). There must be no flag,
    env var or argument through which a real identity could be passed, because
    an agent must never handle one.
    """
    hardener = SCRIPT.read_text(encoding="utf-8")
    assert '"--sign", "-"' in hardener, "signing must be ad-hoc"

    for path in (SCRIPT, VERIFIER):
        src = path.read_text(encoding="utf-8")
        for forbidden in (
            "Developer ID:",
            "--keychain",
            "notarytool",
            "altool",
            "APPLE_ID",
            "AC_PASSWORD",
            "--notarize",
        ):
            assert forbidden not in src, f"{forbidden!r} in {path.name} — no credential path"


def test_the_scripts_state_they_produce_nothing_notarized():
    """A pass must not be readable as "signed and notarized".

    The slice cannot produce either, and PS-386 requires that what remains gated
    on the purchase is stated plainly rather than left ambiguous.
    """
    hardener = SCRIPT.read_text(encoding="utf-8")
    assert "NOT A NOTARIZED OR IDENTITY-SIGNED BUILD" in hardener
    assert "NOT OUTCOME 4" in hardener


def test_the_refusal_messages_survive_a_non_utf8_console():
    """⚠️ THE REFUSAL IS THE MESSAGE THAT MUST NOT FAIL TO BE WRITTEN.

    Every refusal here carries a ⚠️ or a ⛔, and on Windows `sys.stderr`
    resolves to cp1252, which cannot encode either — so the write raises inside
    the refusal path and the one line whose whole job is to say "this is NOT a
    pass" is the line that never appears. Measured on CI run 34475127591.

    Both scripts must pin their own stdio, and must do it in `main()` before any
    output — pinning after the first write is pinning after the failure.
    """
    for path in (SCRIPT, VERIFIER):
        src = path.read_text(encoding="utf-8")
        assert "_pin_stdio_to_utf8" in src, f"{path.name} does not pin its stdio"
        assert 'errors="replace"' in src, f"{path.name} must degrade, not raise"
        body = src.split("def main() -> int:", 1)[1]
        first_call = body.index("_pin_stdio_to_utf8()")
        assert first_call < body.index("ap.parse_args()"), (
            f"{path.name} must pin stdio before it can emit anything"
        )


# ── ⭐ the wiring: a script nobody calls changes no shipped byte ─────────────
#
# These matter more than they look. The first attempt at this slice landed both
# scripts and wired NEITHER into any workflow — a complete, tested, reviewable
# change that would have shipped a byte-identical dmg. A test suite that passes
# on inert code is exactly the "green flag in a build log" PS-386 warns about,
# one level up.

RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def macos_steps() -> list[dict]:
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    return data["jobs"]["build-macos"]["steps"]


def _step_index(steps: list[dict], needle: str) -> int:
    for i, step in enumerate(steps):
        if needle in (step.get("run") or ""):
            return i
    raise AssertionError(f"no build-macos step runs {needle!r} — the script is dead code")


def test_the_hardener_is_actually_invoked_by_the_release_job(macos_steps):
    """⛔ THE DEAD-CODE FENCE.

    A hardening script that no workflow calls leaves the shipped bytes exactly
    as PS-346 measured them, while every test in this file passes.
    """
    _step_index(macos_steps, "ps386_macos_harden.py")


def test_the_posture_gate_is_actually_invoked_too(macos_steps):
    """The measurement must run in CI, not only be available to run."""
    _step_index(macos_steps, "ps386_verify_posture.py")


def test_hardening_happens_before_the_dmg_is_built(macos_steps):
    """Order is the whole point: hdiutil wraps whatever is on disk.

    Harden after packaging and the dmg carries the un-hardened bundle while the
    log cheerfully reports a hardened one.
    """
    assert _step_index(macos_steps, "ps386_macos_harden.py") < _step_index(
        macos_steps, "hdiutil create"
    )


def test_the_posture_gate_runs_after_hardening_and_before_packaging(macos_steps):
    """A gate before the change measures the baseline; after packaging is too late."""
    harden = _step_index(macos_steps, "ps386_macos_harden.py")
    gate = _step_index(macos_steps, "ps386_verify_posture.py --app \"$APP\"\n")
    assert harden < gate < _step_index(macos_steps, "hdiutil create")


def test_the_posture_gate_cannot_be_swallowed(macos_steps):
    """⛔ A `|| true` on the AFTER gate would hollow this whole slice out.

    The BEFORE reading is deliberately allowed to fail (it is the baseline, and
    a pre-hardening bundle is expected to fail it). The AFTER gate must not be:
    it is the only thing that can catch codesign re-injecting get-task-allow.
    """
    gate = [
        s
        for s in macos_steps
        if "ps386_verify_posture.py" in (s.get("run") or "")
        and "BEFORE" not in (s.get("name") or "")
    ]
    assert gate, "no non-baseline posture gate found"
    for step in gate:
        assert "|| true" not in step["run"], "the posture gate must not be swallowed"
        assert step.get("continue-on-error") is not True


def test_the_frozen_bundle_smoke_test_runs_AFTER_hardening_too(macos_steps):
    """⭐ THE FALSIFICATION STEP.

    Hardening re-signs the compiled extensions inside the bundle's own
    site-packages — the very .so files the smoke test imports. A smoke test that
    only ran BEFORE hardening validated bytes that are not the ones shipping,
    so re-signing could break an import and CI would never notice.

    ⚠️ This does NOT make CI sufficient for outcome 4; the smoke test documents
    its own bound (the Flutter host layer is not exercised). It makes CI able to
    catch the specific breakage hardening can cause in the Python payload.
    """
    smoke = [i for i, s in enumerate(macos_steps) if "smoke_frozen_bundle.py" in (s.get("run") or "")]
    assert len(smoke) >= 2, (
        "the frozen-bundle smoke test must run again after hardening — hardening "
        "rewrites the extensions it imports"
    )
    harden = _step_index(macos_steps, "ps386_macos_harden.py")
    assert any(i > harden for i in smoke), "no smoke run happens after hardening"
    assert all(i < _step_index(macos_steps, "hdiutil create") for i in smoke), (
        "the smoke gate must stay before packaging"
    )


def test_ci_never_passes_a_signing_identity(macos_steps):
    """⛔ No credential is placed by the workflow either.

    The scripts have no path to an identity; this asserts the CALLER does not
    invent one via an env var or an extra argument.
    """
    for step in macos_steps:
        blob = (step.get("run") or "") + str(step.get("env") or "")
        for forbidden in ("APPLE_ID", "AC_PASSWORD", "notarytool", "altool", "--keychain"):
            assert forbidden not in blob, f"{forbidden!r} in the macOS job — no credential path"


# ── the runbook's one un-runnable step ───────────────────────────────────────

RUNBOOK = REPO / "readings" / "ps386-liaison-runbook.md"


def test_the_runbook_pins_the_origin_reading_to_the_merge_base():
    """⛔ THE BASELINE MUST NOT BE TAKEN ON THIS BRANCH.

    `[tool.flet.macos.entitlement]` is a LIVE merge seam: flet merges it over
    its five defaults, so a `flet build macos` on this branch writes a generated
    `Release.entitlements` that already mentions `get-task-allow`. A Step 0 run
    here therefore reads OUR OWN COMMIT and calls it the baseline.

    Worse, the discriminator inverts: the runbook's stop-branch is "if the key
    is in the generated file, the analysis is wrong" — which on this tree fires
    on a tree where the analysis is right, sending the liaison to report a
    defect that does not exist.

    The reading Step 0 exists to take — does ad-hoc codesign inject the key when
    NOTHING declares it? — is obtainable only where nothing declares it.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    step0 = text.split("## Step 1")[0]
    assert "d300635" in step0, "Step 0 must name the merge-base to build at"
    assert "git worktree add" in step0, "Step 0 must build from a separate merge-base checkout"
    assert "MERGE-BASE, NOT ON THIS BRANCH" in step0


def test_the_runbook_does_not_call_the_branch_build_a_baseline():
    """The phrasing that caused the defect must not survive the fix.

    "Build ONCE without the hardening" reads as achievable on this branch. It is
    not — the entitlement declaration is already in the tree.
    """
    step0 = RUNBOOK.read_text(encoding="utf-8").split("## Step 1")[0]
    assert "Build ONCE without the hardening" not in step0


def test_the_runbook_keeps_unreadable_distinct_from_third_party():
    """The liaison must be told these are two different skips.

    A count that mixes "Node.js Foundation signed this" with "I could not read
    this" gives no way to tell a legitimate skip from a blind one.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "UNREADABLE` is not a synonym for `THIRD_PARTY" in text
    assert "four** counts" in text or "four counts" in text


def test_the_runbook_tells_the_liaison_a_zero_UNSIGNED_count_is_a_defect():
    """⭐ THE ONE READING THAT LOOKS LIKE GOOD NEWS AND IS NOT.

    Every other bucket in the dry-run reads the obvious way: a big OURS is
    progress, a THIRD_PARTY of 1 is expected, an UNREADABLE of 0 is clean. An
    UNSIGNED of 0 reads clean too — and on this bundle it means the classifier
    has stopped seeing 28 slices it is supposed to harden, which Step 3 then
    fails on.

    The liaison has one shot at this on a Mac that is expensive to get, so the
    runbook has to say so before Step 2 rather than leave it to be inferred from
    a failure two steps later.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    step1 = text.split("## Step 2")[0]
    assert "UNSIGNED" in step1, "the runbook must name the UNSIGNED bucket"
    assert "28" in step1, "it must give the expected count from PS-346's measurement"

    # ⚠️ SCOPED TO THE UNSIGNED SENTENCE, NOT TO THE WHOLE SECTION. A bare
    # `"stop and report" in step1` passes on Step 0's own unrelated stop-branch,
    # so it would stay green with this warning deleted — a check that cannot
    # fail is not coverage. Read the line that actually carries the number.
    stop_lines = [
        ln for ln in step1.splitlines() if "UNSIGNED" in ln and "0" in ln
    ]
    assert any("stop and report" in ln.lower() for ln in stop_lines), (
        "a zero UNSIGNED count must carry its OWN stop-branch, on its own line"
    )


def test_the_runbook_predicts_the_liaisons_screen_in_ITEMS_not_arch_slices():
    """⭐ THE NUMBER THE LIAISON COMPARES AGAINST MUST BE IN THE TOOL'S OWN UNIT.

    ⚠️ THIS TEST IS SCOPED TO THE *WHOLE* DOCUMENT, DELIBERATELY, AND THAT IS
    THE POINT OF IT. Its sibling above scopes to `text.split("## Step 2")[0]`,
    and the line that actually predicts the liaison's screen — in "What to
    report back" — sits AFTER Step 2 and was therefore uncovered by
    construction. A test that cannot reach the wrong number is not coverage of
    it: the runbook shipped `"UNSIGNED should be ~28"` under a green suite.

    The two tools share a vocabulary and not a unit. `classify()` is called once
    per PATH (unit = ITEM); the control's `macho_slices()` returns one entry per
    architecture (unit = ARCH SLICE). PS-346's committed artifact states both in
    one line — 113 files, 225 slices — so on universal binaries the dry-run
    prints roughly HALF of PS-346's figures, and a liaison told to expect 28
    reads a correct 14 as a half-broken classifier, on a Mac session that is
    expensive to get.
    """
    text = RUNBOOK.read_text(encoding="utf-8")

    # The unit distinction must be NAMED, not left to be inferred.
    assert "ITEM" in text and "SLICE" in text.upper(), (
        "the runbook must name both units explicitly"
    )

    # The report-back section is the one that predicts what the screen shows.
    report_back = text.split("## What to report back")[1]
    assert "14" in report_back, (
        "the report-back prediction must be stated in ITEMS (~14), not in "
        "PS-346's arch-slice figure"
    )

    # ⛔ AND THE THIN-ARCH READING MUST BE PRESENT WHEREVER THE PREDICTION IS.
    # Nobody has read this bundle's `lipo -archs`, so ~28 is not WRONG — it is
    # the correct answer for a thin-arch build. Stating only one of the two
    # turns a correct reading into a suspected defect, which is the same failure
    # one number over.
    assert "thin-arch" in report_back or "thin arch" in report_back, (
        "the prediction must state the thin-arch fallback, so a correct ~28 "
        "reading is never mistaken for a broken classifier"
    )

    # The stop condition is a ZERO and only a zero — neither ~14 nor ~28.
    assert "0" in report_back and "defect" in report_back.lower(), (
        "the report-back section must still say a 0 is the defect"
    )


def test_the_runbook_shows_the_slice_to_item_arithmetic_rather_than_asserting_it():
    """The 2x factor must be DERIVED on the page, not handed down as a number.

    A bare "expect ~14" is a claim the liaison cannot check. The reconciliation
    against PS-346's committed artifact is forced rather than merely consistent
    (196/2 + 28/2 + 1 = 113 == the measured file count; 28 single-arch files
    would demand 2.33 slices/file across the remainder, which arm64+x86_64
    cannot reach), and showing it is what lets a reader confirm the translation
    instead of trusting it.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "113" in text, "the file count from PS-346's artifact must be shown"
    assert "225" in text, "the slice count from PS-346's artifact must be shown"
    # The arithmetic itself, in whatever spacing the page uses.
    assert re.search(r"196\s*/\s*2", text), "show 196/2 = 98 ADHOC items"
    assert re.search(r"28\s*/\s*2", text), "show 28/2 = 14 UNSIGNED items"
    # And the one command that settles the unit outright, in either direction.
    assert "lipo -archs" in text, (
        "the runbook must give the liaison the single command that settles "
        "the unit question on the actual bundle"
    )


def test_the_runbook_test_count_is_not_stale():
    """⚠️ A NUMBER IN A HANDOVER DOCUMENT THAT NOTHING CHECKS WILL DRIFT.

    This one has, every commit: 36 -> 46 -> 47 -> 51 -> 55, and the runbook was
    still saying 36 three commits after it stopped being true. It is a small
    lie in a document whose whole value is that a human can trust its numbers
    on hardware that is expensive to get, and it costs one assertion to make
    impossible. Same class as the units defect above, one order of magnitude
    less serious: an unverified number in the handover.
    """
    declared = re.search(
        r"\*\*new\.\*\* (\d+) tests", RUNBOOK.read_text(encoding="utf-8")
    )
    assert declared, "the runbook must state how many tests this file carries"
    actual = len(
        re.findall(r"^def test_", Path(__file__).read_text(encoding="utf-8"), re.M)
    )
    assert int(declared.group(1)) == actual, (
        f"the runbook claims {declared.group(1)} tests; this file has {actual}"
    )


def test_the_hardener_does_not_claim_its_counts_read_straight_against_PS346():
    """⛔ THE OVER-CLAIM THAT MADE THE WRONG PREDICTION LOOK JUSTIFIED.

    `OURS_STATES`' comment used to argue that sharing the control's vocabulary
    meant "this script's counts can be read straight against that measured
    baseline instead of translated". Sharing the words is real and worth having;
    the conclusion does not follow, because the two tools count different
    things. Asserting no translation is needed is worse than saying nothing,
    because it is the sentence a future reader would cite when writing the
    prediction the liaison then reads.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "read straight against" not in src, (
        "the counts need translating: items here, arch slices there"
    )
    # The correction must be present, not merely the over-claim absent.
    assert "THE UNIT IS NOT" in src.upper(), (
        "the comment must state that the vocabulary is shared and the unit is not"
    )
