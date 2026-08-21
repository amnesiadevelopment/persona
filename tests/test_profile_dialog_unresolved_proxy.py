"""PS-44: the profile dialog must not present an unaccounted-for proxy as DIRECT.

Link 1 of the chain. The dialog computed its initial selection as "the profile's
proxy if that name appears in the available list, otherwise DIRECT". When the
name was absent the control rendered as DIRECT — visually identical to a profile
the operator deliberately set to direct — and submitting turned that display
fallback into a stored un-assignment.

The operator must be able to tell "this profile has no proxy" apart from "this
profile's proxy could not be found", and saving from the second state must not
discard the assignment.
"""
import flet as ft
import pytest

from src.models.bookmark import Bookmark
from src.models.profile import Profile
from src.services.profile.proxy_assignment import (
    PROXY_NONE,
    PROXY_UNCHANGED,
    ProxyDirective,
)
from src.ui.dialogs.profile import open_profile_dialog


class _FakePage:
    def __init__(self):
        self.shown = None

    def show_dialog(self, dlg):
        self.shown = dlg

    def pop_dialog(self):
        pass

    def update(self):
        pass


def _open(profile, proxy_names, on_save=lambda *a: None):
    page = _FakePage()
    open_profile_dialog(
        page,
        object(),
        on_save=on_save,
        profile=profile,
        proxy_names=proxy_names,
        pool_names=[],
        all_bookmarks=[Bookmark("browserleaks", "https://browserleaks.com/")],
        cert_names=[],
    )
    return page


def _walk(control):
    yield control
    for attr in ("content", "controls", "actions"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        items = child if isinstance(child, list) else [child]
        for c in items:
            if c is not None and hasattr(c, "__dict__"):
                yield from _walk(c)


def _proxy_dropdown(page):
    """The proxy dropdown: the one whose options include the DIRECT sentinel."""
    for c in _walk(page.shown):
        if isinstance(c, ft.Dropdown):
            keys = [o.key for o in (c.options or [])]
            if "(direct)" in keys:
                return c
    return None


def _texts(page):
    return [
        str(c.value) for c in _walk(page.shown)
        if isinstance(getattr(c, "value", None), str)
    ]


def _submit(page):
    btn = next(
        c for c in _walk(page.shown)
        if isinstance(c, ft.Button)
        and getattr(c, "content", None) in ("[ create ]", "[ save ]")
    )
    btn.on_click(None)


_DIRECT = "(direct)"


# --------------------------------------------------------------------------
# The missing-name state is rendered as ITSELF, not as DIRECT.
# --------------------------------------------------------------------------


def test_unresolved_proxy_is_not_rendered_as_direct():
    """THE dialog regression. Pre-fix the dropdown read exactly `(direct)`."""
    prof = Profile(name="shopper", proxy="PL-residential")
    dd = _proxy_dropdown(_open(prof, proxy_names=["NL-datacenter"]))

    assert dd is not None
    assert dd.value != _DIRECT


def test_unresolved_proxy_option_names_the_missing_proxy():
    """The operator is told WHICH proxy is unaccounted for, not merely that
    something is wrong."""
    prof = Profile(name="shopper", proxy="PL-residential")
    page = _open(prof, proxy_names=["NL-datacenter"])
    dd = _proxy_dropdown(page)

    selected = next(o for o in dd.options if o.key == dd.value)
    assert "PL-residential" in (selected.text or "")


def test_unresolved_state_is_distinguishable_from_deliberate_direct():
    """The two states must be tellable apart — that is the whole requirement.
    A profile with no proxy and a profile whose proxy is missing must not
    render identically."""
    missing = _proxy_dropdown(
        _open(Profile(name="a", proxy="PL-residential"), ["NL-datacenter"])
    )
    deliberate = _proxy_dropdown(_open(Profile(name="b", proxy=None), ["NL-datacenter"]))

    assert deliberate.value == _DIRECT
    assert missing.value != deliberate.value


def test_unresolved_state_explains_itself_in_the_hint():
    prof = Profile(name="shopper", proxy="PL-residential")
    page = _open(prof, proxy_names=["NL-datacenter"])

    joined = " ".join(_texts(page))
    assert "PL-residential" in joined
    assert "not found" in joined.lower()
    # and it says what saving will do, so the operator is not left guessing
    assert "keeps it assigned" in joined or "keep assigned" in joined


def test_empty_proxy_store_still_flags_the_assignment():
    """The quarantine condition: EVERY name is absent, not just one. The
    profile must still not read as direct."""
    prof = Profile(name="shopper", proxy="PL-residential")
    dd = _proxy_dropdown(_open(prof, proxy_names=[]))

    assert dd.value != _DIRECT
    selected = next(o for o in dd.options if o.key == dd.value)
    assert "PL-residential" in (selected.text or "")


# --------------------------------------------------------------------------
# Saving from that state does not discard the assignment.
# --------------------------------------------------------------------------


def _captured_proxy(page_holder):
    return page_holder["args"][1]


def test_saving_an_unresolved_proxy_sends_leave_unchanged():
    """Link 2. Pre-fix this sent "" — the value the model read as a clear."""
    captured = {}
    prof = Profile(name="shopper", proxy="PL-residential")
    page = _open(
        prof, ["NL-datacenter"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_proxy(captured) is PROXY_UNCHANGED


def test_saving_a_deliberate_direct_sends_proxy_none():
    """DIRECT stays expressible — and now says so explicitly rather than
    relying on an empty string the model used to read as a clear."""
    captured = {}
    prof = Profile(name="shopper", proxy=None)
    page = _open(
        prof, ["NL-datacenter"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_proxy(captured) is PROXY_NONE


def test_saving_a_resolved_proxy_sends_the_name():
    captured = {}
    prof = Profile(name="shopper", proxy="NL-datacenter")
    page = _open(
        prof, ["NL-datacenter"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_proxy(captured) == "NL-datacenter"


def test_operator_can_still_switch_away_from_an_unresolved_proxy():
    """The unresolved state is not a trap: picking a real proxy from the same
    dropdown reassigns normally."""
    captured = {}
    prof = Profile(name="shopper", proxy="PL-residential")
    page = _open(
        prof, ["NL-datacenter"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _proxy_dropdown(page).value = "NL-datacenter"
    _submit(page)

    assert _captured_proxy(captured) == "NL-datacenter"


def test_operator_can_deliberately_clear_an_unresolved_proxy():
    """And the operator can still decide the missing proxy should just go —
    by SAYING so (picking DIRECT), which is the whole point."""
    captured = {}
    prof = Profile(name="shopper", proxy="PL-residential")
    page = _open(
        prof, ["NL-datacenter"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _proxy_dropdown(page).value = _DIRECT
    _submit(page)

    assert _captured_proxy(captured) is PROXY_NONE


# --------------------------------------------------------------------------
# The synthetic option must not contaminate the normal cases.
# --------------------------------------------------------------------------


def test_no_unresolved_option_when_the_proxy_resolves():
    prof = Profile(name="shopper", proxy="NL-datacenter")
    dd = _proxy_dropdown(_open(prof, ["NL-datacenter", "DE-mobile"]))

    assert [o.key for o in dd.options] == [_DIRECT, "NL-datacenter", "DE-mobile"]


def test_no_unresolved_option_on_a_new_profile():
    dd = _proxy_dropdown(_open(None, ["NL-datacenter"]))

    assert dd.value == _DIRECT
    assert [o.key for o in dd.options] == [_DIRECT, "NL-datacenter"]


def test_new_profile_defaults_to_proxy_none_not_an_empty_string():
    captured = {}
    page = _open(
        None, ["NL-datacenter"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    # name is required by validation
    next(c for c in _walk(page.shown) if isinstance(c, ft.TextField)).value = "newp"
    _submit(page)

    assert _captured_proxy(captured) is PROXY_NONE


def test_unresolved_option_key_cannot_collide_with_a_real_proxy_name():
    """The synthetic key is prefixed so a proxy literally named after it still
    resolves normally rather than being mistaken for the sentinel."""
    prof = Profile(name="shopper", proxy="PL-residential")
    dd = _proxy_dropdown(_open(prof, ["NL-datacenter"]))

    assert dd.value not in ("PL-residential", "NL-datacenter", _DIRECT)
    assert isinstance(dd.value, str)
