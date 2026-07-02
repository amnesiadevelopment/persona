from src.services.browser.resolution import (
    DESKTOP_RESOLUTIONS,
    parse_resolution,
    resolve_resolution,
)


def test_presets_are_common_desktop_sizes():
    # A few sizes that must be offered.
    assert (1920, 1080) in DESKTOP_RESOLUTIONS
    assert (1366, 768) in DESKTOP_RESOLUTIONS
    assert (2560, 1440) in DESKTOP_RESOLUTIONS


def test_parse_valid():
    assert parse_resolution("1920x1080") == (1920, 1080)
    assert parse_resolution("1366X768") == (1366, 768)
    assert parse_resolution(" 1440 x 900 ") == (1440, 900)


def test_parse_invalid_returns_none():
    assert parse_resolution("") is None
    assert parse_resolution("auto") is None
    assert parse_resolution("1920") is None
    assert parse_resolution("axb") is None
    assert parse_resolution("0x0") is None
    assert parse_resolution("100x100") is None  # below the sane minimum


def test_resolve_explicit_wxh():
    assert resolve_resolution("1600x900", seed=1) == (1600, 900)


def test_resolve_auto_is_a_known_preset():
    w, h = resolve_resolution("auto", seed=12345)
    assert (w, h) in DESKTOP_RESOLUTIONS


def test_resolve_auto_is_deterministic_per_seed():
    a = resolve_resolution("auto", seed=999)
    b = resolve_resolution("auto", seed=999)
    assert a == b


def test_resolve_auto_varies_across_seeds():
    picks = {resolve_resolution("auto", seed=s) for s in range(50)}
    assert len(picks) > 1  # not all seeds collapse to one resolution


def test_resolve_blank_or_garbage_falls_back_to_auto():
    # An empty / unparseable value behaves like "auto": a valid preset.
    assert resolve_resolution("", seed=7) in DESKTOP_RESOLUTIONS
    assert resolve_resolution("nonsense", seed=7) in DESKTOP_RESOLUTIONS
