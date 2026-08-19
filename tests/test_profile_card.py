import os
import socket
import time

import flet as ft

from src.models.profile import Profile
from src.models.proxy import Proxy
from src.ui.components.profile_card import (
    PROXY_STALE_AFTER_S,
    build_profile_card,
)


def _texts(control):
    out = []
    def walk(c):
        if isinstance(c, ft.Text) and isinstance(c.value, str):
            out.append(c.value)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, str):
                out.append(v)
            elif v is not None:
                walk(v)
    walk(control)
    return out


def _images(control):
    out = []
    def walk(c):
        if isinstance(c, ft.Image) and isinstance(getattr(c, "src", None), str):
            out.append(c.src)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v:
                    walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)
    walk(control)
    return out


def _noop(*a, **k):
    pass


def _icons(control):
    out = []
    def walk(c):
        if isinstance(c, ft.Icon):
            out.append(getattr(c, "icon", None))
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v:
                    walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)
    walk(control)
    return out


def test_direct_uses_home_icon_when_no_proxy():
    p = Profile(name="a", proxy=None, os_type="windows")
    card = build_profile_card(p, False, False, _noop, _noop, _noop)
    assert ft.Icons.HOME_OUTLINED in _icons(card)
    # 'direct' still appears in the meta line below the name
    assert any("direct" in t for t in _texts(card))


def test_no_standalone_proxy_button():
    # the old [ proxy ] button must be gone; checking is via the indicator
    p = Profile(name="a", proxy="P", os_type="windows")
    px = Proxy(name="P", url="socks5://1.2.3.4:1", country_code="ie", last_check_ok=True)
    card = build_profile_card(
        p, False, False, _noop, _noop, _noop,
        proxy=px, on_check_proxy=lambda n: None,
    )
    assert "[ proxy ]" not in _texts(card)


def test_flag_shown_for_checked_proxy():
    p = Profile(name="a", proxy="P", os_type="windows")
    # checked_at is explicit and fresh: the flag is earned by a recent check,
    # not by the mere presence of a stored country_code.
    px = Proxy(
        name="P", url="socks5://1.2.3.4:1", country_code="ie",
        last_check_ok=True, checked_at=time.time() - 30,
    )
    card = build_profile_card(
        p, False, False, _noop, _noop, _noop,
        proxy=px, on_check_proxy=lambda n: None,
    )
    imgs = _images(card)
    assert any(s.endswith("ie.svg") for s in imgs)


def test_failed_proxy_shows_cross():
    p = Profile(name="a", proxy="P", os_type="windows")
    px = Proxy(name="P", url="socks5://1.2.3.4:1", country_code="", last_check_ok=False)
    card = build_profile_card(
        p, False, False, _noop, _noop, _noop,
        proxy=px, on_check_proxy=lambda n: None,
    )
    assert "✕" in _texts(card)


def test_unchecked_proxy_shows_dot_placeholder():
    p = Profile(name="a", proxy="P", os_type="windows")
    px = Proxy(name="P", url="socks5://1.2.3.4:1", country_code="", last_check_ok=None)
    card = build_profile_card(
        p, False, False, _noop, _noop, _noop,
        proxy=px, on_check_proxy=lambda n: None,
    )
    assert "·" in _texts(card)


def test_indicator_click_checks_proxy():
    p = Profile(name="a", proxy="P", os_type="windows")
    px = Proxy(name="P", url="socks5://1.2.3.4:1", country_code="ie", last_check_ok=True)
    clicked = []
    card = build_profile_card(
        p, False, False, _noop, _noop, _noop,
        proxy=px, on_check_proxy=lambda n: clicked.append(n),
    )
    # find the clickable container wrapping the flag and fire its handler
    found = []
    def walk(c):
        cb = getattr(c, "on_click", None)
        if callable(cb) and getattr(c, "tooltip", "") == "Check this profile's proxy":
            found.append(cb)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v: walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)
    walk(card)
    assert found, "no clickable proxy indicator found"
    found[0](None)
    assert clicked == ["P"]


def test_checking_shows_spinner_not_clickable():
    p = Profile(name="a", proxy="P", os_type="windows")
    px = Proxy(name="P", url="socks5://1.2.3.4:1", country_code="ie", last_check_ok=True)
    card = build_profile_card(
        p, False, False, _noop, _noop, _noop,
        proxy=px, on_check_proxy=lambda n: None, proxy_checking=True,
    )
    # a ProgressRing exists somewhere
    rings = []
    def walk(c):
        if isinstance(c, ft.ProgressRing):
            rings.append(c)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v: walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)
    walk(card)
    assert rings


def test_notes_do_not_overlay_the_action_buttons():
    # Regression: notes lived in a Stack overlay whose (unbounded) container
    # blanketed the whole card and swallowed clicks meant for launch/edit/delete
    # — after creating a profile the buttons stopped responding. Notes must be a
    # sibling of the buttons inside a Row, never a full-card overlay on top.
    p = Profile(name="a", proxy=None, os_type="windows")
    card = build_profile_card(p, False, False, _noop, _noop, _noop)
    assert not isinstance(card.content, ft.Stack), (
        "card content is a Stack — notes overlay can cover the buttons"
    )
    assert isinstance(card.content, ft.Row)
    # the notes field and the action buttons share one Row, so nothing is layered
    # on top of the clickable controls
    kinds = [type(c).__name__ for c in card.content.controls]
    assert "Stack" not in kinds


# --- proxy check freshness (PS-15) -------------------------------------------
# The indicator is a function of AGE as well as outcome: a stored country_code
# with no recent check must never render as a confident green flag.


def _flag_srcs(card):
    # Only COUNTRY flags. The launch button legitimately renders its own
    # engine_*.svg from src/assets/, so a bare ".svg" match would count it as a
    # proxy flag and make these assertions lie.
    return [s for s in _images(card) if os.sep + "flags" + os.sep in s]


def _card_with(px):
    p = Profile(name="a", proxy="P", os_type="windows")
    return build_profile_card(
        p, False, False, _noop, _noop, _noop,
        proxy=px, on_check_proxy=lambda n: None,
    )


def test_stale_threshold_is_a_named_module_constant():
    # AC7: the boundary is one named knob, not a literal buried in a branch.
    assert isinstance(PROXY_STALE_AFTER_S, (int, float))
    assert PROXY_STALE_AFTER_S > 0


def test_fresh_check_still_shows_the_country_flag():
    # AC2: a check 30s old renders exactly as it does today — this slice must
    # not disturb the healthy state.
    px = Proxy(
        name="P", url="socks5://1.2.3.4:1", country_code="ie",
        last_check_ok=True, checked_at=time.time() - 30,
    )
    card = _card_with(px)
    assert any(s.endswith("ie.svg") for s in _flag_srcs(card))


def test_stale_check_does_not_render_the_verified_flag():
    # AC3: past the threshold the state is visibly DIFFERENT and no bare
    # verified flag is emitted.
    px = Proxy(
        name="P", url="socks5://1.2.3.4:1", country_code="ie",
        last_check_ok=True, checked_at=time.time() - (PROXY_STALE_AFTER_S + 1),
    )
    card = _card_with(px)
    assert not any(s.endswith("ie.svg") for s in _flag_srcs(card)), (
        "a stale check still drew the confident country flag"
    )


def test_fresh_and_stale_render_differently():
    # The core of the ticket: two proxies identical but for checked_at must not
    # be pixel-identical. Compare the whole rendered text+image projection.
    now = time.time()
    def shape(age_s):
        card = _card_with(Proxy(
            name="P", url="socks5://1.2.3.4:1", country_code="ie",
            last_check_ok=True, checked_at=now - age_s,
        ))
        return (_texts(card), _images(card))
    assert shape(30) != shape(PROXY_STALE_AFTER_S + 1)


def test_stale_check_surfaces_its_age():
    # The flag carries its own provenance: the operator can read HOW OLD the
    # evidence is, via the shared humanize_since vocabulary.
    px = Proxy(
        name="P", url="socks5://1.2.3.4:1", country_code="ie",
        last_check_ok=True, checked_at=time.time() - (45 * 86400),
    )
    texts = _texts(_card_with(px))
    assert any("45d ago" in t for t in texts), texts


def test_stored_country_with_no_timestamp_is_not_verified():
    # AC4: last_check_ok=True but checked_at=0.0 -> not-yet-verified, NEVER a
    # flag. This is the "a stored field alone produced a green state" case the
    # roadmap charter forbids.
    px = Proxy(
        name="P", url="socks5://1.2.3.4:1", country_code="ie",
        last_check_ok=True, checked_at=0.0,
    )
    card = _card_with(px)
    assert not _flag_srcs(card), "a country_code with no timestamp drew a flag"
    assert "·" in _texts(card)


def test_failed_check_stays_a_cross_at_any_age():
    # AC5: a failure does not age into something softer.
    now = time.time()
    for age in (30, PROXY_STALE_AFTER_S + 1, 400 * 86400):
        px = Proxy(
            name="P", url="socks5://1.2.3.4:1", country_code="ie",
            last_check_ok=False, checked_at=now - age,
        )
        texts = _texts(_card_with(px))
        assert "✕" in texts, f"failure at age {age}s stopped rendering as ✕"
        assert not _flag_srcs(_card_with(px))


def test_building_a_card_opens_no_socket():
    # AC6: explicitly render-only. Crossing the staleness threshold must not
    # trigger a probe — the indicator reports a last-known state, it does not
    # refresh on draw (an indicator whose cadence is attributable to a human
    # opening a window is a different security object).
    opened = []
    real_socket = socket.socket

    class _Spy(real_socket):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            opened.append(a)
            super().__init__(*a, **k)

    now = time.time()
    fixtures = [
        Proxy(name="P", url="socks5://1.2.3.4:1", country_code="ie",
              last_check_ok=True, checked_at=now - 30),
        Proxy(name="P", url="socks5://1.2.3.4:1", country_code="ie",
              last_check_ok=True, checked_at=now - (PROXY_STALE_AFTER_S + 1)),
        Proxy(name="P", url="socks5://1.2.3.4:1", country_code="ie",
              last_check_ok=True, checked_at=0.0),
        Proxy(name="P", url="socks5://1.2.3.4:1", country_code="",
              last_check_ok=False, checked_at=now - 999),
    ]
    socket.socket = _Spy
    try:
        for px in fixtures:
            _card_with(px)
    finally:
        socket.socket = real_socket
    assert opened == [], f"building a card opened a socket: {opened}"


def test_stale_indicator_is_still_clickable_to_recheck():
    # Clickability must not be gated on freshness — the stale indicator is
    # precisely the one the operator needs to be able to re-check.
    p = Profile(name="a", proxy="P", os_type="windows")
    px = Proxy(
        name="P", url="socks5://1.2.3.4:1", country_code="ie",
        last_check_ok=True, checked_at=time.time() - (PROXY_STALE_AFTER_S + 1),
    )
    clicked = []
    card = build_profile_card(
        p, False, False, _noop, _noop, _noop,
        proxy=px, on_check_proxy=lambda n: clicked.append(n),
    )
    found = []
    def walk(c):
        cb = getattr(c, "on_click", None)
        if callable(cb) and getattr(c, "tooltip", "") == "Check this profile's proxy":
            found.append(cb)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v: walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)
    walk(card)
    assert found, "stale indicator lost its re-check click target"
    found[0](None)
    assert clicked == ["P"]
