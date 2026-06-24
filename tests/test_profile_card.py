import flet as ft

from src.models.profile import Profile
from src.models.proxy import Proxy
from src.ui.components.profile_card import build_profile_card


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
    px = Proxy(name="P", url="socks5://1.2.3.4:1", country_code="ie", last_check_ok=True)
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
