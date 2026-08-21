"""The advertised Chromium version is DERIVED from the installed engine.

The defect (PS-42): the Chromium major was written down twice — once by the
engine that gets installed, once by hand in the masking layer — and nothing
could detect that the two had drifted. ``engine/policy.MAX_TESTED_MAJOR``
existed only to stop the engine from ever getting AHEAD of those constants, at
the cost of making every routine engine update need a human.

EVERY VERSION IN THIS FILE IS DELIBERATELY UNLIKE ANY VALUE THAT WAS EVER
HARDCODED. The old constants were ``148`` / ``148.0.0.0``; these tests run the
engine at 151, 152 and 203. A test that used 148 could not tell a derived value
from a coincidentally-equal constant, which is exactly the assertion this file
exists to make — so if a fallback constant ever creeps back in, these go red.
"""

import json
import os
import pathlib

import pytest

import src.services.browser.process as process
from src.models.profile import Profile
from src.services.browser import engine_version
from src.services.browser.device_presets import ANDROID_PRESETS, IOS_PRESETS, get_preset
from src.services.browser.engine_version import (
    ChromiumVersion,
    EngineVersionUnreadableError,
    parse,
)
from src.services.browser.mobile_ext import build_mobile_extension

# Nothing in persona has ever hardcoded these.
ENGINE_TAG = "151.0.7778.215"
ENGINE_MAJOR = "151"
ENGINE_REDUCED = "151.0.0.0"

# The values that USED to be hardcoded. Nothing derived may equal them while the
# engine reports the versions above.
OLD_MAJOR = "148"
OLD_FULL = "148.0.0.0"


# --------------------------------------------------------------------------
# reading the engine's version
# --------------------------------------------------------------------------


def test_parse_reads_the_three_shapes_a_page_actually_sees():
    """A Chromium version reaches a page in three DIFFERENT shapes, and the
    whole point is that all three come from one reading."""
    v = parse(ENGINE_TAG)
    assert v.full == ENGINE_TAG          # uaFullVersion / fullVersionList
    assert v.major == ENGINE_MAJOR       # Client Hints brands (bare major)
    assert v.reduced == ENGINE_REDUCED   # the UA's frozen Chrome/... form


def test_the_ua_form_is_reduced_not_the_true_build():
    """Chrome freezes the UA's minor/build/patch. Emitting the true build in the
    UA would be the anomaly, not the fidelity — and the full version still has
    to travel intact in uaFullVersion, so the two must not be the same string."""
    v = parse("152.0.8123.47")
    assert v.reduced == "152.0.0.0"
    assert v.full == "152.0.8123.47"
    assert v.reduced != v.full


def test_the_installed_version_is_read_from_the_engines_own_record(monkeypatch):
    """Derived from the record the update machinery already writes, rather than
    from a second way of asking — one source means the advertised version and
    the governed version cannot disagree."""
    import src.services.engine.updater as updater

    monkeypatch.setattr(updater, "current_version", lambda: "203.0.1.9")
    assert engine_version.installed_chromium_version().major == "203"


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("151.0.7778.215", "151.0.7778.215"),
        ("v151.0.7778.215", "151.0.7778.215"),   # a 'v' prefix is common upstream
        ("151", "151.0.0.0"),                    # short tags pad, the major is what matters
        ("151.0", "151.0.0.0"),
        ("151.0.8000.10-beta", "151.0.8000.10"),  # trailing junk drops component-wise
    ],
)
def test_parse_accepts_the_tag_shapes_upstream_actually_publishes(tag, expected):
    assert parse(tag).full == expected


@pytest.mark.parametrize("junk", ["", "   ", "nightly", "v", "..."])
def test_an_unreadable_version_raises_instead_of_defaulting(junk):
    """THE ANTI-FALLBACK ASSERTION. A default here is the whole defect: it would
    silently re-create the mismatch the moment the engine moved. Raising is the
    considered answer — loud, and recoverable by an engine check."""
    with pytest.raises(EngineVersionUnreadableError):
        parse(junk)


# --------------------------------------------------------------------------
# the derived version reaches every advertised surface
# --------------------------------------------------------------------------


def test_the_client_hints_brands_carry_the_ENGINES_major(tmp_path):
    js = _android_ext(tmp_path)
    assert f"{{ brand: 'Chromium', version: '{ENGINE_MAJOR}' }}" in js
    assert f"{{ brand: 'Google Chrome', version: '{ENGINE_MAJOR}' }}" in js
    # and not the value that used to be hardcoded there
    assert f"version: '{OLD_MAJOR}'" not in js


def test_the_full_version_list_carries_the_ENGINES_full_version(tmp_path):
    js = _android_ext(tmp_path)
    assert f'var FULLVER  = "{ENGINE_TAG}"' in js
    # fullVersionList is built by mapping the brands onto FULLVER, so the true
    # build reaches it without a second constant
    assert "return { brand: b.brand, version: FULLVER };" in js
    assert OLD_FULL not in js


def test_the_grease_brand_is_untouched(tmp_path):
    """'Not.A/Brand' is a real GREASE entry with its own version that has
    nothing to do with the engine — deriving it would be wrong."""
    js = _android_ext(tmp_path)
    assert "{ brand: 'Not.A/Brand', version: '24' }" in js


def test_no_placeholder_survives_into_the_built_script(tmp_path):
    js = _android_ext(tmp_path)
    assert "__MAJOR__" not in js
    assert "__FULLVER__" not in js


def test_every_android_preset_advertises_the_engines_version():
    """Not one preset, all of them — deriving some and leaving others constant
    would reintroduce the same defect in a narrower form."""
    v = parse(ENGINE_TAG)
    for preset in ANDROID_PRESETS:
        ua = preset.user_agent_for(v)
        assert f"Chrome/{ENGINE_REDUCED}" in ua, preset.key
        assert OLD_FULL not in ua, preset.key
        assert "{chrome}" not in ua, preset.key
        # the device identity is still the preset's own
        assert preset.model in ua, preset.key


def test_an_android_extension_cannot_be_built_without_the_engines_version(tmp_path):
    """There is no default to fall back to, so this is a raise rather than a
    quietly-placeholdered extension shipped into a live profile."""
    with pytest.raises(ValueError):
        build_mobile_extension(
            str(tmp_path / "m"), is_ios=False, platform="Android",
            model="Pixel 7", chromium_version=None,
            css_width=412, css_height=915, dpr=2.625,
            device_memory=8, hardware_concurrency=8,
        )


# --------------------------------------------------------------------------
# a major bump moves every surface together — the ticket's core claim
# --------------------------------------------------------------------------


def test_raising_the_engine_by_a_major_moves_every_advertised_surface(tmp_path):
    """THE TICKET, STATED AS ONE ASSERTION.

    Same profile, same preset, two different engines a major apart. Every
    advertised surface must move with the engine, with no source edit in
    between — and the reading must stay COHERENT: the UA's major and the brand
    list's major are the same number at both versions.
    """
    def advertised(tag):
        v = parse(tag)
        js = _android_ext(tmp_path / tag, version=v)
        ua = get_preset("pixel-7").user_agent_for(v)
        return ua, js

    ua_a, js_a = advertised("151.0.7778.215")
    ua_b, js_b = advertised("152.0.8123.47")

    # the UA moved
    assert "Chrome/151.0.0.0" in ua_a and "Chrome/152.0.0.0" in ua_b
    # the brands moved with it
    assert "version: '151'" in js_a and "version: '152'" in js_b
    # the full-version list moved with it
    assert 'FULLVER  = "151.0.7778.215"' in js_a
    assert 'FULLVER  = "152.0.8123.47"' in js_b
    # and nothing was left behind at the other version
    assert "152" not in ua_a and "151" not in ua_b
    assert "version: '152'" not in js_a and "version: '151'" not in js_b


# --------------------------------------------------------------------------
# at launch: the profile actually presents the derived version
# --------------------------------------------------------------------------


class _Store:
    def resolve(self, name):
        return ""

    def get(self, name):
        return None


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _spawn(monkeypatch, tmp_path, profile, tag=ENGINE_TAG):
    """Launch a profile against an engine reporting ``tag``, returning the argv
    the launcher handed to Popen. Mirrors tests/test_browser_env_policy.py."""
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.pid = os.getpid()

    import src.services.engine.updater as updater

    monkeypatch.setattr(updater, "current_version", lambda: tag)
    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    process.spawn_browser(profile)
    return captured["args"]


def _ua_arg(args):
    for a in args:
        if a.startswith("--user-agent="):
            return a.split("=", 1)[1]
    return None


def test_a_launched_android_profile_presents_the_installed_engines_version(
    monkeypatch, tmp_path
):
    """End to end: the engine reports 151, so the launched profile says 151 —
    in the UA it passes AND in the extension it writes into the profile dir."""
    args = _spawn(
        monkeypatch, tmp_path, Profile(name="droid", os_type="android")
    )
    ua = _ua_arg(args)
    assert ua is not None
    assert f"Chrome/{ENGINE_REDUCED}" in ua
    assert OLD_FULL not in ua

    # the extension regenerated into the profile dir agrees with the UA
    js = (
        pathlib.Path(tmp_path) / "droid" / ".persona-mobile-ext" / "mobile.js"
    ).read_text()
    assert f"version: '{ENGINE_MAJOR}'" in js
    assert f'FULLVER  = "{ENGINE_TAG}"' in js


def test_relaunching_the_same_profile_on_a_newer_engine_needs_no_migration(
    monkeypatch, tmp_path
):
    """The extensions are regenerated inside the profile's own directory on
    EVERY launch, so an existing profile picks the new version up by itself.
    Confirmed rather than assumed: the SAME profile directory is launched
    twice, against two engines a major apart."""
    profile = Profile(name="droid", os_type="android")

    ua_first = _ua_arg(_spawn(monkeypatch, tmp_path, profile, tag="151.0.7778.215"))
    js_path = pathlib.Path(tmp_path) / "droid" / ".persona-mobile-ext" / "mobile.js"
    assert "Chrome/151.0.0.0" in ua_first
    assert "version: '151'" in js_path.read_text()

    # engine bumped a major underneath the existing profile; no migration step
    ua_second = _ua_arg(_spawn(monkeypatch, tmp_path, profile, tag="152.0.8123.47"))
    assert "Chrome/152.0.0.0" in ua_second
    js = js_path.read_text()
    assert "version: '152'" in js
    assert "version: '151'" not in js, "the stale extension was not regenerated"


def test_an_android_launch_refuses_rather_than_advertising_a_guessed_version(
    monkeypatch, tmp_path
):
    """THE FALLBACK DECISION, ASSERTED. When the engine's version cannot be read
    the launch fails CLOSED. Advertising a guess would be the mismatch this work
    removes, shipped silently — and unfixable after the fact, because the pages
    that saw it already saw it."""
    with pytest.raises(EngineVersionUnreadableError) as excinfo:
        _spawn(monkeypatch, tmp_path, Profile(name="droid", os_type="android"), tag="")

    # the refusal has to be actionable, not just loud
    msg = str(excinfo.value)
    assert "droid" in msg
    assert "engine check" in msg


# --------------------------------------------------------------------------
# the profiles that were never coupled must read identically
# --------------------------------------------------------------------------


def test_a_desktop_profile_passes_no_user_agent_at_all(monkeypatch, tmp_path):
    """Desktop profiles inherit whatever the engine reports and were ALREADY
    version-independent. This change must not introduce a coupling that did not
    exist — including a --user-agent that was never passed before."""
    args = _spawn(monkeypatch, tmp_path, Profile(name="deskt", os_type="windows"))
    assert _ua_arg(args) is None
    assert not any("user-agent" in a.lower() for a in args)


def test_a_desktop_profile_launches_even_when_the_engine_version_is_unreadable(
    monkeypatch, tmp_path
):
    """The fail-closed gate is scoped to the profiles that actually advertise a
    Chromium version. A desktop profile advertises none, so an unreadable
    version is none of its business and must not block it."""
    args = _spawn(
        monkeypatch, tmp_path, Profile(name="deskt", os_type="windows"), tag=""
    )
    assert _ua_arg(args) is None


def test_an_ios_profile_advertises_no_chromium_version(monkeypatch, tmp_path):
    """Real iOS Safari ships no UA-CH and its UA carries no Chromium version, so
    there is nothing to derive — and nothing to refuse when the engine's version
    cannot be read."""
    args = _spawn(monkeypatch, tmp_path, Profile(name="fone", os_type="ios"), tag="")
    ua = _ua_arg(args)
    assert ua is not None
    assert "Chrome/" not in ua
    assert "iPhone" in ua

    js = (
        pathlib.Path(tmp_path) / "fone" / ".persona-mobile-ext" / "mobile.js"
    ).read_text()
    assert "__FULLVER__" not in js and "__MAJOR__" not in js
    assert OLD_FULL not in js and f"version: '{OLD_MAJOR}'" not in js


def test_ios_presets_carry_no_version_slot():
    v = parse(ENGINE_TAG)
    for preset in IOS_PRESETS:
        ua = preset.user_agent_for(v)
        assert "Chrome/" not in ua, preset.key
        assert ENGINE_MAJOR not in ua, preset.key


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _android_ext(base, version: ChromiumVersion | None = None) -> str:
    """Build an Android mobile extension and return its emitted mobile.js."""
    d = build_mobile_extension(
        str(base), is_ios=False, platform="Android", model="Pixel 7",
        chromium_version=version or parse(ENGINE_TAG),
        css_width=412, css_height=915, dpr=2.625,
        device_memory=8, hardware_concurrency=8, touch_points=5,
    )
    return (pathlib.Path(d) / "mobile.js").read_text()
