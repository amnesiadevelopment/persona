"""PS-356: the Client Hints version must be the engine's, and must REFUSE.

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
3. **An unreadable version REFUSES.** Skipping the flag is not a safe
   degradation; it IS the defect, because omission falls back to the 144 table
   rather than to "no claim".
4. **The three shapes are derived correctly.** `.reduced` in this flag would
   land verbatim in `uaFullVersion` — the tell `engine_version.parse()` already
   refuses.

⚠️ WHAT THESE TESTS DO **NOT** ESTABLISH: that a live page receives the three
shapes correctly. That is AC2 / the non-waivable falsification, it requires a
real launch of the shipped engine, and an argv assertion is explicitly not
evidence for it. See the PR for the live reading and its venue.
"""

from __future__ import annotations

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
def _argv_with(version: ChromiumVersion | None = None, **overrides) -> list[str]:
    """The argv a real child process received, via the PS-224 recorder seam.

    ⚠️ The seam patches `installed_chromium_version` ITSELF (it has to — this
    container has no engine installed and the launch now refuses without one),
    so an outer patch here is shadowed by the inner one. Patching `parse`
    instead puts the value UPSTREAM of the seam's own stub, which is what lets
    this helper drive the launch at an arbitrary version.
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


# ── the refusal ─────────────────────────────────────────────────────────────
def test_an_unreadable_version_refuses_rather_than_skipping_the_flag():
    """AC3. Omitting the flag is NOT a safe degradation — it falls back to the
    engine's 144 table, i.e. to advertising a version the engine is not. So the
    unreadable case must refuse, exactly as `_mobile_chromium_version` does."""
    with mock.patch.object(
        _process,
        "installed_chromium_version",
        mock.Mock(side_effect=EngineVersionUnreadableError("version.txt absent")),
    ):
        with pytest.raises(EngineVersionUnreadableError) as exc:
            _process._chromium_brand_version(Profile(name="ps356-refuse"))

    assert "engine check" in str(exc.value), (
        "the refusal must name the remedy, matching _mobile_chromium_version's "
        "wording and spirit"
    )
    assert "ps356-refuse" in str(exc.value), "the refusal must name the profile"


def test_the_refusal_is_not_scoped_to_mobile():
    """⭐ THE SCOPE DECISION, STATED AS A TEST.

    `_mobile_chromium_version` is Android-scoped because only a mobile profile
    is passed `--user-agent` at all. Client Hints are NOT: the engine emits
    sec-ch-ua on EVERY profile, so a desktop launch was exactly as exposed to
    the 144 table — and desktop is in fact what was reported. Inheriting the
    mobile scope here would have left the reported defect open on the very
    profiles that reported it.

    A plain desktop Profile (no mobile preset) must therefore refuse too.
    """
    desktop = Profile(name="ps356-desktop")
    assert _process._mobile_chromium_version(desktop, None) is None, (
        "precondition: a desktop profile has no mobile UA version"
    )

    with mock.patch.object(
        _process,
        "installed_chromium_version",
        mock.Mock(side_effect=EngineVersionUnreadableError("version.txt absent")),
    ):
        with pytest.raises(EngineVersionUnreadableError):
            _process._chromium_brand_version(desktop)


def test_a_readable_version_does_not_refuse():
    """The guard must not refuse the ordinary case it sits in front of."""
    with mock.patch.object(
        _process, "installed_chromium_version", lambda: ENGINE
    ):
        assert _process._chromium_brand_version(Profile(name="ok")) == ENGINE


def test_the_firefox_arm_is_untouched_by_the_refusal():
    """A Firefox profile must not be refused for a CHROMIUM engine version it
    never advertises. `spawn_browser` returns on the firefox arm before any of
    this runs; asserted so a later refactor cannot move the resolution above
    that early return and start refusing Firefox launches."""
    import inspect

    src = inspect.getsource(_process.spawn_browser)
    firefox_at = src.index('if engine == "firefox"')
    brand_at = src.index("_chromium_brand_version(")
    assert firefox_at < brand_at, (
        "the Chromium version is resolved BEFORE the firefox early return, so "
        "an unreadable Chromium version would refuse a Firefox launch"
    )
