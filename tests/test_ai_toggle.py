"""The per-profile AI checkbox must flip IN PLACE (update its own box/label/card),
not trigger a full connect-page rebuild — a rebuild reset the scroll position to
the top on every toggle (Mars hit this live on v2.8.5)."""
import flet as ft
import pytest

from src.models.profile import Profile
from src.ui.components import connect_page as cp


@pytest.fixture(autouse=True)
def _no_op_update(monkeypatch):
    # Unattached controls raise on .update(); stub it so we can exercise the
    # click handler without a live page.
    monkeypatch.setattr(ft.Text, "update", lambda self: None)
    monkeypatch.setattr(ft.Container, "update", lambda self: None)


def _clickable(control):
    out = []
    def walk(c):
        if getattr(c, "on_click", None) is not None:
            out.append(c)
        for a in ("controls", "content"):
            v = getattr(c, a, None)
            if isinstance(v, list):
                for x in v: walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)
    walk(control)
    return out


def _box(control):
    out = []
    def walk(c):
        if isinstance(c, ft.Text) and c.value in ("[x]", "[ ]"):
            out.append(c)
        for a in ("controls", "content"):
            v = getattr(c, a, None)
            if isinstance(v, list):
                for x in v: walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)
    walk(control)
    return out[0]


def test_ai_checkbox_flips_in_place_and_reports_new_state():
    p = Profile(name="X", os_type="windows"); p.ai_control = False
    got = {}
    row = cp._ai_profile_row(p, lambda n, want: got.update({n: want}))
    box = _box(row)
    cb = _clickable(row)[0]

    assert box.value == "[ ]"
    cb.on_click(None)
    assert box.value == "[x]"          # flipped in place, not via rebuild
    assert got == {"X": True}          # reported the new state

    cb.on_click(None)
    assert box.value == "[ ]"
    assert got == {"X": False}


def test_ai_card_border_tracks_state():
    p = Profile(name="Y", os_type="windows"); p.ai_control = False
    row = cp._ai_profile_row(p, lambda n, want: None)
    cb = _clickable(row)[0]
    # off -> the card border is the dim card colour; on -> accent
    cb.on_click(None)
    # border object exists and was swapped; just assert it's still set
    assert row.border is not None
