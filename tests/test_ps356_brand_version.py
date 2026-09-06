"""PS-356: the Client Hints version must be the engine's — and when it cannot be
read, the flag is SKIPPED rather than refused.

THE DEFECT, IN ONE SENTENCE
──────────────────────────
`--fingerprint-brand-version` was never passed, so the engine fell through to a
HARDCODED table in `002-user-agent-fingerprint.patch`::

    constexpr const char* kChromiumVersions[] = {
        "144.0.7559.132", "144.0.7559.109", "144.0.7559.96", "144.0.7559.59" };
    ...
    return kChromiumVersions[seed % std::size(kChromiumVersions)];

A profile on the **152** engine therefore advertised Client Hints saying **144**
while its reduced user agent said **152**. Three independent checkers read that
as a lie — pixelscan reports *"masking detected"*, which is a positive
identification that this is a masking tool rather than a lost point.

⭐ THE UNREADABLE CASE: SKIP, NOT REFUSE (owner ruling, 2026-09-06)
──────────────────────────────────────────────────────────────────
An earlier round of this ticket failed CLOSED here, mirroring
`_mobile_chromium_version`. That was REVERSED, and the reasoning is worth
keeping because the two arms look inconsistent unless you know why:

An absent `version.txt` is a REACHABLE TRANSIENT STATE — the error's own
docstring says so — which a profile launched mid-update sees. Failing closed
converts that self-healing condition into "no Chromium profile launches at all"
for ~99% of launches.

And the skip is SAFE ON THIS ARM SPECIFICALLY: with no flag, the engine answers
all three shapes from its own built-in default, so UA / brands / uaFullVersion
still agree WITH EACH OTHER. The tell this ticket closes is the DISAGREEMENT,
and skipping does not reintroduce it — the claim is merely less current. On the
MOBILE arm the same skip would make the layer TYPE a version the engine does not
match, a genuine contradiction, so `_mobile_chromium_version` still refuses.
⛔ That asymmetry is deliberate and is pinned by
`test_android_still_fails_closed_the_asymmetry_is_deliberate`.

WHAT THESE TESTS PIN, AND WHY EACH IS A REAL FALSIFIER
──────────────────────────────────────────────────────
1. **The flag is present, with the `.full` shape.** Asserted against the argv a
   REAL child process received (via the PS-224 recorder seam), not a rebuilt
   list — a test that re-implemented the argv would pass while the shipped path
   omitted the flag.
2. **The gate is intact.** `--fingerprint-brand-version` is read by the engine
   ONLY inside `if (brand == "chrome")`. The brand flag is therefore load-bearing
   for a *different* flag than the one it appears to serve, and dropping it
   fails SILENTLY — no error, no log, the profile just reverts to 144. Nothing
   else in the suite would catch that, which is exactly why it is pinned here.
3. **An unreadable version SKIPS — passing NO flag, never a constant, and
   loudly.** A fallback literal would re-create the duplication
   `engine_version.py` exists to remove; a silent skip would leave an operator
   with nothing to find. Both are pinned separately.
4. **The three shapes are derived correctly.** `.reduced` in this flag would
   land verbatim in `uaFullVersion` — the tell `engine_version.parse()` already
   refuses.

⚠️ WHAT THESE TESTS DO **NOT** ESTABLISH: that a live page receives the three
shapes correctly. That is AC2 / the non-waivable falsification, it requires a
real launch of the shipped engine, and an argv assertion is explicitly not
evidence for it. See the PR for the live reading and its venue.
"""

from __future__ import annotations

import re
import unittest.mock as mock

import pytest

from src.models.profile import Profile
from src.services.browser import process as _process
from src.services.browser.engine_version import (
    ChromiumVersion,
    EngineVersionUnreadableError,
)

ENGINE = ChromiumVersion(full="152.0.7977.75")


# ── the value, and the three shapes it has to yield ─────────────────────────
def test_the_flag_carries_full_not_reduced_and_not_major():
    """`.full` is the only value that yields all three correct shapes.

    The engine takes ONE input and fans it out itself (patch 002, :357-401):
    `brand_version_list` <- GetMajorVersion(v), `brand_full_version_list` <- v,
    `metadata->full_version` <- v. So the switch must carry the TRUE build.
    """
    assert ENGINE.full == "152.0.7977.75"
    assert ENGINE.major == "152"
    assert ENGINE.reduced == "152.0.0.0"

    # The three distinct shapes a page sees. Emitting one string in all three
    # is itself a tell, which is why the engine derives the major rather than
    # us passing it.
    assert ENGINE.full != ENGINE.reduced
    assert len(ENGINE.full.split(".")) == 4
    assert "." not in ENGINE.major


def test_passing_reduced_would_reproduce_the_tell_the_parser_refuses():
    """A guard on the value we must NOT pass.

    `uaFullVersion` takes the switch value verbatim, so `152.0.0.0` there makes
    uaFullVersion and the reduced UA byte-identical — the shape
    `engine_version.parse()` refuses outright ("a real Chrome never reports a
    .0.0 full version"). This asserts the refusal still exists, so the reason
    `.full` is correct cannot quietly stop being true.
    """
    from src.services.browser.engine_version import parse

    with pytest.raises(EngineVersionUnreadableError, match="no real build"):
        parse("152.0.0.0")


# ── the argv, read off a real launch ────────────────────────────────────────
def _argv_with(version: ChromiumVersion | None = None, skip_version: bool = False,
               **overrides) -> list[str]:
    """The argv a real child process received, via the PS-224 recorder seam.

    ⚠️ The seam patches `installed_chromium_version` ITSELF (it has to — this
    container has no engine installed), so an outer patch of that name here
    would be SHADOWED by the seam's inner one. This helper therefore patches
    `_process.ChromiumVersion`, the constructor the seam's stub calls to build
    its return value: the seam resolves that attribute at call time, so the
    override reaches it and the launch runs at an arbitrary version.
    ⛔ Do not "simplify" this into an outer patch of `installed_chromium_version`
    — it will be silently overwritten and the test will assert on the seam's
    default rather than on the version you asked for.

    `skip_version=True` is the unreadable-engine path: the CALLER supplies the
    raising patch (so the same stub drives the helper under test and this
    launch), and this helper simply does not install a version override.
    """
    import importlib.util
    import sys
    from pathlib import Path

    # Resolve the sibling test file from THIS file's location, never from the
    # process working directory. A relative "tests/..." path works when pytest
    # is invoked from the repo root and fails everywhere else — which showed up
    # as these tests passing alone and failing in a full-suite run, the exact
    # shape that reads like flakiness or pollution and is neither.
    seam = Path(__file__).resolve().parent / "test_ps224_engine_name.py"
    spec = importlib.util.spec_from_file_location("_ps224", seam)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_ps224"] = mod
    spec.loader.exec_module(mod)

    if skip_version:
        return mod._capture_launch_argv(stub_engine_version=False)

    if version is None or version == ENGINE:
        return mod._capture_launch_argv()

    with mock.patch.object(
        _process, "ChromiumVersion", lambda full: version
    ):
        return mod._capture_launch_argv()


def test_the_flag_reaches_a_real_launch_with_the_installed_version():
    """AC1, asserted against the argv the OS delivered — not a rebuilt list."""
    argv = _argv_with(ENGINE)
    assert f"--fingerprint-brand-version={ENGINE.full}" in argv, (
        "the switch never reached the engine, so it falls back to its "
        "hardcoded 144.x table: %r" % [a for a in argv if "fingerprint" in a]
    )


def test_the_brand_gate_is_present_in_the_same_launch():
    """⚠️ THE SILENT-FAILURE GUARD.

    The engine reads `--fingerprint-brand-version` ONLY when
    `ToLowerASCII(--fingerprint-brand) == "chrome"`. So this line is the GATE
    for the flag above it, and removing or varying it — e.g. adding an
    Edge/Opera/Vivaldi brand option — disables the version flag with NO error
    and NO log, silently reverting every profile to 144.

    Asserted in the same launch as the version flag deliberately: the property
    is that the PAIR travels together, and two tests that each pass alone would
    not establish it.
    """
    argv = _argv_with(ENGINE)
    assert "--fingerprint-brand=Chrome" in argv
    assert f"--fingerprint-brand-version={ENGINE.full}" in argv

    brand = next(a for a in argv if a.startswith("--fingerprint-brand="))
    assert brand.split("=", 1)[1].lower() == "chrome", (
        "the engine gates the version switch on brand == 'chrome' (after "
        "ToLowerASCII); any other brand silently disables it: %r" % brand
    )


def test_the_version_flag_is_passed_exactly_once():
    """Chromium takes the LAST occurrence of a repeated switch, so a duplicate
    is a real hazard rather than untidiness."""
    argv = _argv_with(ENGINE)
    assert sum(a.startswith("--fingerprint-brand-version=") for a in argv) == 1


def test_the_flag_tracks_the_engine_rather_than_a_constant():
    """The anti-duplication property, proven the only way it can be: run the
    source at a value that appears NOWHERE in the tree as a constant.

    A test using the real 152 could not tell derivation from coincidence — the
    engine's own fallback table and the repo are both full of real versions.
    """
    invented = ChromiumVersion(full="199.0.9999.42")
    argv = _argv_with(invented)
    assert "--fingerprint-brand-version=199.0.9999.42" in argv
    assert not any("144." in a for a in argv if "brand-version" in a)


# ── the skip (owner ruling 2026-09-06: desktop skips, mobile still refuses) ──
def test_an_unreadable_version_skips_the_flag_rather_than_refusing():
    """AC3, as REVERSED by the owner on 2026-09-06.

    An unreadable version.txt is a REACHABLE TRANSIENT STATE mid-update —
    `EngineVersionUnreadableError`'s own docstring says the file may be "absent
    (a reachable state)". Failing closed here would convert that transient,
    self-healing condition into "no Chromium profile launches at all" for ~99%
    of launches.

    Skipping is safe on THIS arm specifically: the engine then answers all three
    shapes from its own built-in default, so UA / brands / uaFullVersion still
    agree WITH EACH OTHER. The tell this ticket closes is the DISAGREEMENT, and
    skipping does not reintroduce it — it only makes the claim less current.
    """
    with mock.patch.object(
        _process,
        "installed_chromium_version",
        mock.Mock(side_effect=EngineVersionUnreadableError("version.txt absent")),
    ):
        assert _process._chromium_brand_version(Profile(name="ps356-skip")) is None


def test_the_skip_passes_no_flag_rather_than_a_fallback_constant():
    """⛔ THE TRAP THE OWNER NAMED EXPLICITLY.

    "Skip" must mean PASS NO FLAG. Substituting a literal like "152.0.7977.75"
    would re-create, in a new place, the hand-written duplication
    `engine_version.py` exists to remove — and it would go stale INVISIBLY the
    moment the engine moves, which is this ticket's entire subject.

    Asserted on a REAL launch's argv (not a rebuilt list): no
    --fingerprint-brand-version at all, and no version-shaped literal smuggled
    in beside it.
    """
    with mock.patch.object(
        _process,
        "installed_chromium_version",
        mock.Mock(side_effect=EngineVersionUnreadableError("version.txt absent")),
    ):
        args = _argv_with(None, skip_version=True)

    assert not any(a.startswith("--fingerprint-brand-version") for a in args), (
        "the skip must omit the flag entirely, not pass an empty or default value"
    )
    # and no hardcoded engine version anywhere in argv
    assert not re.search(r"\b\d+\.0\.\d{4}\.\d+\b", " ".join(args)), (
        "a fallback CONSTANT was smuggled into argv — that is the duplication "
        "engine_version.py exists to remove"
    )


def test_the_skip_is_logged_because_it_is_a_degraded_state():
    """⚠️ The owner required the skip be VISIBLE, and that it carry a REASON
    rather than being a bare boolean.

    A profile launching without this flag advertises the engine's built-in
    version rather than its real one. An operator wondering why their Client
    Hints look old must have something to find.
    """
    with mock.patch.object(
        _process,
        "installed_chromium_version",
        mock.Mock(side_effect=EngineVersionUnreadableError("version.txt absent")),
    ):
        with mock.patch.object(_process.logger, "warning") as warn:
            _process._chromium_brand_version(Profile(name="ps356-loud"))

    assert warn.called, "a silent skip leaves an operator with nothing to find"
    rendered = (warn.call_args[0][0] % warn.call_args[0][1:]).lower()
    assert "ps356-loud" in rendered, "the log must name the profile"
    assert "version.txt absent" in rendered, (
        "the log must carry the REASON (the underlying read failure), not just "
        "the fact that something was skipped"
    )
    assert "engine check" in rendered, "the log must name the remedy"


def test_the_brand_flag_survives_the_skip():
    """⛔ --fingerprint-brand=Chrome is NOT contingent on the version.

    It is the gate the version flag needs when the version IS readable, and the
    brand claim ("presents as Chrome and nothing else") stands on its own. If a
    later refactor were to make the pair conditional as a unit, a transient
    unreadable version would silently change the profile's BRAND too.
    """
    with mock.patch.object(
        _process,
        "installed_chromium_version",
        mock.Mock(side_effect=EngineVersionUnreadableError("version.txt absent")),
    ):
        args = _argv_with(None, skip_version=True)

    assert "--fingerprint-brand=Chrome" in args, (
        "the brand flag must be passed unconditionally, skip or no skip"
    )


def test_android_still_fails_closed_the_asymmetry_is_deliberate():
    """⭐⭐ THE ASYMMETRY, PINNED SO NOBODY "FIXES" IT.

    Desktop skips; Android REFUSES. That is not an inconsistency to tidy — the
    two arms have different failure modes:

      * desktop skip -> the engine answers from its own default, so UA, brands
        and full-version still agree with each other. Coherent, less current.
      * mobile skip  -> the layer would TYPE a version into --user-agent that
        the engine underneath does not match. A genuine contradiction, and
        permanently seen by whatever pages saw it.

    `_mobile_chromium_version` is deliberately untouched by PS-356. This test
    fails if someone unifies the two behaviours in either direction.
    """
    android = Profile(name="ps356-android")
    preset = mock.Mock(os_type="android")

    with mock.patch.object(
        _process,
        "installed_chromium_version",
        mock.Mock(side_effect=EngineVersionUnreadableError("version.txt absent")),
    ):
        # mobile: still refuses
        with pytest.raises(EngineVersionUnreadableError) as exc:
            _process._mobile_chromium_version(android, preset)
        # desktop: skips
        assert _process._chromium_brand_version(android) is None

    assert "engine check" in str(exc.value), (
        "the mobile refusal must keep naming the remedy"
    )


def test_a_readable_version_is_returned_unchanged():
    """The skip must not swallow the ordinary case it sits in front of."""
    with mock.patch.object(
        _process, "installed_chromium_version", lambda: ENGINE
    ):
        assert _process._chromium_brand_version(Profile(name="ok")) == ENGINE


def test_the_firefox_arm_never_resolves_a_chromium_version():
    """A Firefox profile must not resolve — or log about — a CHROMIUM engine
    version it never advertises. `spawn_browser` returns on the firefox arm
    before any of this runs; asserted so a later refactor cannot move the
    resolution above that early return."""
    import inspect

    src = inspect.getsource(_process.spawn_browser)
    firefox_at = src.index('if engine == "firefox"')
    brand_at = src.index("_chromium_brand_version(")
    assert firefox_at < brand_at, (
        "the Chromium version is resolved BEFORE the firefox early return, so a "
        "Firefox launch would resolve (and log about) a version it never uses"
    )
