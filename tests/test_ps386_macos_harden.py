#!/usr/bin/env python3
"""Tests for PS-386's macOS hardening seam.

WHAT THESE CAN AND CANNOT COVER, STATED UP FRONT
------------------------------------------------
⛔ These tests CANNOT prove the app launches under the hardened runtime. That is
outcome 4 of PS-386 and it needs a real Mac: a build, an `open`, and an observed
engine spawn. Nothing here substitutes for it, and a green run of this file must
never be reported as evidence for it — the ticket's own words for that mistake
are "a green flag in a build log".

What they DO cover is the part that is mechanically checkable from any platform,
and the part most likely to rot silently:

  * the two entitlement declarations (pyproject's and the script's) cannot drift
    apart without a test failing;
  * `get-task-allow` is declared false in both;
  * `cs.allow-jit` is still declared TRUE — the blanket-removal guard, because
    the bundled CPython very probably needs it and removing it would trade a
    notarization prerequisite for an app that does not start;
  * the script refuses rather than pretends when it cannot do its job;
  * PS-346's instrument is untouched (it is the control for this change).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ps386_macos_harden.py"
PYPROJECT = REPO / "pyproject.toml"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("ps386_macos_harden", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def declared() -> dict:
    with open(PYPROJECT, "rb") as fh:
        data = tomllib.load(fh)
    return data["tool"]["flet"]["macos"]["entitlement"]


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
    perturbs one key and asserts the comparison notices. Without this, the test
    above could be comparing the same dict to itself through two names.
    """
    drifted = dict(declared)
    drifted["com.apple.security.get-task-allow"] = True
    assert drifted != mod.ENTITLEMENTS

    also_drifted = dict(declared)
    del also_drifted["com.apple.security.cs.allow-jit"]
    assert also_drifted != mod.ENTITLEMENTS


# ── the posture itself ───────────────────────────────────────────────────────


def test_get_task_allow_is_declared_false_in_both_places(mod, declared):
    """The notarization-rejection condition PS-346 measured, turned off.

    Declared `false` rather than omitted: the key is injected by ad-hoc signing
    itself, so being silent about it is how it got shipped true in the first
    place.
    """
    key = "com.apple.security.get-task-allow"
    assert declared[key] is False
    assert mod.ENTITLEMENTS[key] is False


def test_allow_jit_is_still_true(mod, declared):
    """⛔ THE BLANKET-REMOVAL GUARD — this is a FENCE, not a preference.

    persona ships a bundled CPython. `cs.allow-jit` was already true in the
    shipped 3.1.1 plist, which is evidence that JIT behaviour is live and
    load-bearing rather than incidental. Removing it to make a notarization
    checker green would trade a prerequisite for an app that does not launch,
    which PS-386 names explicitly as the mistake to avoid.
    """
    key = "com.apple.security.cs.allow-jit"
    assert declared[key] is True, "removing allow-jit very probably breaks the bundled interpreter"
    assert mod.ENTITLEMENTS[key] is True


def test_flets_own_five_defaults_are_all_still_present(mod):
    """The five keys flet-cli 0.85.3 seeds must survive our override.

    `merge_dict` merges ours OVER flet's, so a key we drop here does not revert
    to flet's default — it changes the posture silently. Measured set from
    flet_cli/commands/build_base.py:816-822.
    """
    for key in (
        "com.apple.security.app-sandbox",
        "com.apple.security.cs.allow-jit",
        "com.apple.security.network.client",
        "com.apple.security.network.server",
        "com.apple.security.files.user-selected.read-write",
    ):
        assert key in mod.ENTITLEMENTS, f"flet default {key} dropped from our set"


def test_the_set_is_exactly_flets_five_plus_get_task_allow(mod):
    """No key crept in beyond the documented six.

    A sixth-plus key added without a reason is how an entitlement set becomes a
    swept one rather than a deliberate one.
    """
    assert len(mod.ENTITLEMENTS) == 6, sorted(mod.ENTITLEMENTS)


# ── refusal behaviour ────────────────────────────────────────────────────────


def test_check_pyproject_runs_anywhere_and_passes():
    """The drift check must be runnable off a Mac — that is its whole value."""
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--check-pyproject"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr


def test_a_bad_app_path_refuses_with_2_not_0(tmp_path):
    """⚠️ A refusal must be distinguishable from a pass.

    Exit 2 means "the question was not asked". Returning 0 here would make a
    machine that cannot do the work report the same success as one that did it —
    the precise false-green shape PS-386's control was hardened against.
    """
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--app", str(tmp_path / "nope.app")],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2, f"expected 2, got {res.returncode}"
    assert res.returncode != 0


def test_non_darwin_refuses_rather_than_claiming_success(tmp_path):
    """On a non-Mac the script must refuse even when --app looks plausible."""
    if sys.platform == "darwin":
        pytest.skip("this asserts the non-darwin refusal path; we are on darwin")
    fake = tmp_path / "persona.app"
    fake.mkdir()
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--app", str(fake)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "NOT a pass" in res.stderr or "CANNOT RUN" in res.stderr


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
    text.write_text("#!/bin/sh\necho not a macho\n")
    assert mod.is_macho(text) is False

    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert mod.is_macho(empty) is False


def test_slices_are_ordered_deepest_first(mod, tmp_path):
    """⚠️ Ordering is load-bearing, not cosmetic.

    codesign seals a bundle over its contents, so an outer bundle signed before
    its nested code has its seal invalidated the instant the inner code changes.
    """
    app = tmp_path / "persona.app"
    deep = app / "Contents" / "Frameworks" / "Inner.framework" / "Versions" / "A"
    deep.mkdir(parents=True)
    (app / "Contents" / "MacOS").mkdir(parents=True)

    shallow = app / "Contents" / "MacOS" / "persona"
    nested = deep / "Inner"
    for path in (shallow, nested):
        path.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 64)

    order = mod.find_slices(app)
    assert order.index(nested) < order.index(shallow), (
        "nested code must be signed before the bundle that contains it"
    )


# ── the control must stay untouched ──────────────────────────────────────────


def test_ps346_instrument_is_not_modified_by_this_work():
    """⛔ PS-346's instrument is the CONTROL for this change.

    It was hardened through five rework rounds specifically against false-CLEAN
    readings, and re-touching its readers invalidates the before/after
    comparison this whole slice rests on. Adding a CALLER is fine; changing a
    reader is not.

    This asserts the reader functions PS-386 depends on still exist with their
    names intact — a rename would silently break the liaison's runbook.
    """
    instrument = REPO / "scripts" / "ps346_signing_state.py"
    src = instrument.read_text(encoding="utf-8")
    for symbol in (
        "def entitlement_get_task_allow",
        "def read_macho_slice",
        "def classify_tally",
    ):
        assert symbol in src, f"{symbol} missing — the control's readers changed"


def test_this_script_does_not_import_or_wrap_the_control(mod):
    """The two instruments stay independent.

    If the hardener imported the reader, a bug in the hardener could change what
    the control reports — and the control's independence is the only reason its
    before/after reading means anything.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "ps346_signing_state" not in src.replace(
        "scripts/ps346_signing_state.py", ""
    ).replace("ps346_signing_state.py", ""), "the hardener must not import the control"


# ── no credential, ever ──────────────────────────────────────────────────────


def test_the_script_signs_adhoc_only_and_never_takes_an_identity():
    """⛔ No credential is acquired, held, placed or echoed.

    The only signing identity this script can use is `-` (ad-hoc). There must be
    no flag, env var or argument through which a real identity could be passed,
    because an agent must never handle one.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"--sign", "-"' in src, "signing must be ad-hoc"
    for forbidden in (
        "Developer ID",
        "--keychain",
        "notarytool",
        "altool",
        "APPLE_ID",
        "AC_PASSWORD",
        "--notarize",
    ):
        assert forbidden not in src, f"{forbidden!r} must not appear — no credential path"


def test_the_script_states_it_is_not_notarized():
    """A pass must not be readable as "signed and notarized".

    The slice cannot produce either, and PS-386 requires that what remains gated
    on the purchase is stated plainly rather than left ambiguous.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "NOT A NOTARIZED OR IDENTITY-SIGNED BUILD" in src
    assert "NOT OUTCOME 4" in src
