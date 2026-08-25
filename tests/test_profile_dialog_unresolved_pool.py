"""PS-157: the profile dialog must not present an unaccounted-for bookmark pool
as a deliberate "(none)".

The sibling of ``test_profile_dialog_unresolved_proxy.py`` (PS-44), for the
field whose model-side clear-by-omission this ticket ended.

The dialog computed its initial selection as "the profile's pool if that name
appears in the available list, otherwise (none)". When the name was absent the
control rendered as "(none)" — visually identical to a profile the operator
deliberately gave no pool — and submitting turned that display fallback into an
explicit ``POOL_NONE``, which the model is obliged to honour.

That matters more after the model fix, not less: the point of this ticket is
that "clearing is something a caller SAYS, never something it does by omitting
a value". A dialog that SAYS ``POOL_NONE`` on a path where it demonstrably does
not know has promoted an accident into an assertion.

What it costs is recoverability, not exposure. ``delete_pool`` records the
profiles referencing a pool so ``restore_pool`` can put them back, and computes
that list from this field. A quarantined ``bookmarks.json`` is exactly when
``pool_names`` is empty — so an operator opening the dialog to rename a profile
would reach ``delete_pool`` with the reference already gone.

The two conditions are real and are the same ones the proxy dropdown was
hardened against: the bookmark store skips a single malformed pool record
(populated dropdown, one name absent) and quarantines the whole file (every
name absent).
"""
import flet as ft

from src.models.bookmark import Bookmark
from src.models.profile import Profile
from src.services.profile.pool_assignment import (
    POOL_NONE,
    POOL_UNCHANGED,
    resolve_pool_assignment,
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


def _open(profile, pool_names, on_save=lambda *a: None):
    page = _FakePage()
    open_profile_dialog(
        page,
        object(),
        on_save=on_save,
        profile=profile,
        proxy_names=[],
        pool_names=pool_names,
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


_NO_POOL = "(none)"


def _pool_dropdown(page, pool_names=()):
    """The POOL dropdown specifically.

    Deliberately not "the dropdown whose options contain '(none)'" — the
    certificate dropdown uses the same "(none)" sentinel, so that naive
    predicate returns the wrong control. Identified instead by carrying a pool
    name, or (when no name resolves) by being the last "(none)" dropdown, which
    is where the bookmark section sits.
    """
    candidates = [
        c
        for c in _walk(page.shown)
        if isinstance(c, ft.Dropdown)
        and _NO_POOL in [o.key for o in (c.options or [])]
    ]
    for c in candidates:
        keys = [o.key for o in (c.options or [])]
        if any(n in keys for n in pool_names):
            return c
        if any(str(k).startswith("\x00unresolved:") for k in keys):
            return c
    return candidates[-1] if candidates else None


def _texts(page):
    return [
        str(c.value)
        for c in _walk(page.shown)
        if isinstance(getattr(c, "value", None), str)
    ]


def _submit(page):
    btn = next(
        c
        for c in _walk(page.shown)
        if isinstance(c, ft.Button)
        and getattr(c, "content", None) in ("[ create ]", "[ save ]")
    )
    btn.on_click(None)


def _captured_pool(captured):
    # on_save(name, proxy, os_type, search, pool, bookmarks, tags, ...)
    return captured["args"][4]


# --------------------------------------------------------------------------
# The missing-name state is rendered as ITSELF, not as a deliberate "(none)".
# --------------------------------------------------------------------------


def test_unresolved_pool_is_not_rendered_as_no_pool():
    """THE dialog regression. Pre-fix the dropdown read exactly `(none)`."""
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    dd = _pool_dropdown(_open(prof, pool_names=["other-pool"]), ["other-pool"])

    assert dd is not None
    assert dd.value != _NO_POOL


def test_unresolved_pool_option_names_the_missing_pool():
    """The operator is told WHICH pool is unaccounted for."""
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    page = _open(prof, pool_names=["other-pool"])
    dd = _pool_dropdown(page, ["other-pool"])

    selected = next(o for o in dd.options if o.key == dd.value)
    assert "corp-pool" in (selected.text or "")


def test_unresolved_state_is_distinguishable_from_a_deliberate_no_pool():
    """The two states must be tellable apart — that is the whole requirement."""
    missing = _pool_dropdown(
        _open(Profile(name="a", bookmark_pool="corp-pool"), ["other-pool"]),
        ["other-pool"],
    )
    deliberate = _pool_dropdown(
        _open(Profile(name="b", bookmark_pool=None), ["other-pool"]), ["other-pool"]
    )

    assert deliberate.value == _NO_POOL
    assert missing.value != deliberate.value


def test_unresolved_state_explains_itself_in_the_hint():
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    page = _open(prof, pool_names=["other-pool"])

    joined = " ".join(_texts(page))
    assert "corp-pool" in joined
    assert "not found" in joined.lower()
    assert "keeps it assigned" in joined or "keep assigned" in joined


def test_quarantined_pool_store_still_flags_the_assignment():
    """The quarantine condition: EVERY name is absent, not just one. This is
    the case the cascade cares about most — an empty pool_names is exactly a
    quarantined bookmarks.json."""
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    dd = _pool_dropdown(_open(prof, pool_names=[]), [])

    assert dd.value != _NO_POOL
    selected = next(o for o in dd.options if o.key == dd.value)
    assert "corp-pool" in (selected.text or "")


# --------------------------------------------------------------------------
# Saving from that state does not discard the assignment.
# --------------------------------------------------------------------------


def test_saving_an_unresolved_pool_sends_leave_unchanged():
    """Link 2, and the finding this test exists for. Pre-fix this sent
    POOL_NONE — an explicit instruction to destroy a reference the dialog could
    not account for."""
    captured = {}
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    page = _open(
        prof,
        ["other-pool"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_pool(captured) is POOL_UNCHANGED


def test_saving_an_unresolved_pool_preserves_the_stored_value_end_to_end():
    """Bound to the RESOLVED OUTCOME, not to 'a directive was sent': the pool
    the model would actually store must still be the original one."""
    captured = {}
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    page = _open(
        prof,
        [],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    resolved = resolve_pool_assignment(_captured_pool(captured), prof.bookmark_pool)
    assert resolved == "corp-pool"


def test_saving_a_deliberate_no_pool_sends_pool_none():
    """Clearing stays expressible — AC3. A profile with no pool saved from the
    "(none)" selection still SAYS clear."""
    captured = {}
    prof = Profile(name="acct", bookmark_pool=None)
    page = _open(
        prof,
        ["other-pool"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_pool(captured) is POOL_NONE


def test_saving_a_resolved_pool_sends_the_name():
    captured = {}
    prof = Profile(name="acct", bookmark_pool="other-pool")
    page = _open(
        prof,
        ["other-pool"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_pool(captured) == "other-pool"


def test_operator_can_still_switch_away_from_an_unresolved_pool():
    """The unresolved state is not a trap: picking a real pool reassigns."""
    captured = {}
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    page = _open(
        prof,
        ["other-pool"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _pool_dropdown(page, ["other-pool"]).value = "other-pool"
    _submit(page)

    assert _captured_pool(captured) == "other-pool"


def test_operator_can_deliberately_clear_an_unresolved_pool():
    """And the operator can still decide the missing pool should just go — by
    SAYING so (picking "(none)"), which is the whole point. Bound to the
    resolved outcome so this is a real clear, not merely a directive."""
    captured = {}
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    page = _open(
        prof,
        ["other-pool"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _pool_dropdown(page, ["other-pool"]).value = _NO_POOL
    _submit(page)

    assert _captured_pool(captured) is POOL_NONE
    assert resolve_pool_assignment(_captured_pool(captured), "corp-pool") is None


# --------------------------------------------------------------------------
# The synthetic option must not contaminate the normal cases.
# --------------------------------------------------------------------------


def test_no_unresolved_option_when_the_pool_resolves():
    prof = Profile(name="acct", bookmark_pool="other-pool")
    dd = _pool_dropdown(
        _open(prof, ["other-pool", "spare-pool"]), ["other-pool", "spare-pool"]
    )

    assert [o.key for o in dd.options] == [_NO_POOL, "other-pool", "spare-pool"]


def test_no_unresolved_option_on_a_new_profile():
    dd = _pool_dropdown(_open(None, ["other-pool"]), ["other-pool"])

    assert dd.value == _NO_POOL
    assert [o.key for o in dd.options] == [_NO_POOL, "other-pool"]


def test_new_profile_defaults_to_pool_none_not_an_empty_string():
    captured = {}
    page = _open(
        None,
        ["other-pool"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    next(c for c in _walk(page.shown) if isinstance(c, ft.TextField)).value = "newp"
    _submit(page)

    assert _captured_pool(captured) is POOL_NONE


def test_unresolved_option_key_cannot_collide_with_a_real_pool_name():
    """The synthetic key is prefixed so a pool literally named after it still
    resolves normally rather than being mistaken for the sentinel."""
    prof = Profile(name="acct", bookmark_pool="corp-pool")
    dd = _pool_dropdown(_open(prof, ["other-pool"]), ["other-pool"])

    assert dd.value not in ("corp-pool", "other-pool", _NO_POOL)
    assert isinstance(dd.value, str)
