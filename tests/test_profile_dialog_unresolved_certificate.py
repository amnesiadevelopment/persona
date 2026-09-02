"""PS-263: the profile dialog must not present an unaccounted-for mTLS
certificate as a deliberate "(none)".

The third sibling of ``test_profile_dialog_unresolved_proxy.py`` (PS-44) and
``test_profile_dialog_unresolved_pool.py`` (PS-157), for the last field of the
family that never got the option.

The dialog computed its initial selection as "the profile's certificate if that
name appears in the available list, otherwise (none)". When the name was absent
the control rendered as "(none)" — visually identical to a profile the operator
deliberately gave no certificate — and submitting mapped that display fallback
to ``""``, which the model reads as an explicit clear.

**This field's model-side semantics differ from the other two, and the tests
below pin that difference deliberately.** ``new_certificate`` was already
``None``-defaulted and guarded, so ABSENCE already preserved and ``""`` was free
to keep meaning "clear" — it is the operator's own "(none)", and
``test_profile_certificate_persist.py::test_update_can_clear_certificate`` pins
it. So there is one directive here, ``CERT_UNCHANGED``, and no ``CERT_NONE``:
the state that had no spelling was "I cannot account for this", not "clear it".

What the accidental clear costs is neither exposure nor recoverability but
INTENT. The launch path already handles a dangling reference correctly (it
sweeps the key material and launches without a client certificate), so nothing
breaks while the name is missing. What breaks is later: the profile stops being
"a profile whose certificate could not be found" and becomes "a profile that has
no certificate", a legitimate configuration nothing will ever flag — so when the
store recovers, the proxy and the pool come back attached and the certificate
does not. The recorded ``cert_trust_status`` goes with it, because
``update_profile`` clears the verdict on a real reassignment and the accidental
clear is indistinguishable from one.

The two conditions are real and are the same ones the proxy and pool dropdowns
were hardened against (``services/cert/store.py``): the cert store skips a
single unparseable record (populated dropdown, one name absent) and quarantines
an unreadable ``certificates.json`` (every name absent).
"""
import flet as ft

from src.models.bookmark import Bookmark
from src.models.profile import Profile
from src.services.profile.cert_assignment import (
    CERT_UNCHANGED,
    resolve_cert_assignment,
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


def _open(profile, cert_names, on_save=lambda *a: None):
    page = _FakePage()
    open_profile_dialog(
        page,
        object(),
        on_save=on_save,
        profile=profile,
        proxy_names=[],
        pool_names=[],
        all_bookmarks=[Bookmark("browserleaks", "https://browserleaks.com/")],
        cert_names=cert_names,
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


_NO_CERT = "(none)"


def _cert_dropdown(page):
    """The CERTIFICATE dropdown specifically, found BY LABEL.

    Deliberately not "the dropdown whose options contain '(none)'" — the pool
    dropdown uses the same sentinel, so that predicate is ambiguous, and
    ``test_profile_dialog_unresolved_pool.py``'s helper documents the same trap
    from the other side. Nor "the dropdown carrying a cert name", which is
    unusable here: the whole point of these tests is the state where NO cert
    name resolves.

    So: walk to the Column that ``labeled()`` builds, whose first control is the
    field label reading "Certificate (mTLS)", and take the Dropdown inside it.
    """
    for c in _walk(page.shown):
        if not isinstance(c, ft.Column):
            continue
        controls = c.controls or []
        if not controls:
            continue
        label_texts = [
            str(t.value)
            for t in _walk(controls[0])
            if isinstance(getattr(t, "value", None), str)
        ]
        if not any("Certificate (mTLS)" in t for t in label_texts):
            continue
        for inner in controls[1:]:
            for d in _walk(inner):
                if isinstance(d, ft.Dropdown):
                    return d
    return None


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


def _captured_cert(captured):
    # on_save(name, proxy, os, search, pool, bookmarks, tags, notes, engine,
    #         resolution, certificate) -> certificate is the 11th positional
    return captured["args"][10]


# --------------------------------------------------------------------------
# The helper itself, so a silently-wrong lookup cannot make every test below
# vacuous. (The pool dropdown shares the "(none)" sentinel with this one.)
# --------------------------------------------------------------------------


def test_the_cert_dropdown_helper_finds_the_cert_dropdown_not_the_pool_one():
    prof = Profile(name="acct", certificate="corp-ca", bookmark_pool="corp-pool")
    page = _open(prof, cert_names=["corp-ca"])
    dd = _cert_dropdown(page)

    assert dd is not None
    keys = [o.key for o in (dd.options or [])]
    assert "corp-ca" in keys, keys
    assert "corp-pool" not in keys, keys


# --------------------------------------------------------------------------
# The missing-name state is rendered as ITSELF, not as a deliberate "(none)".
# --------------------------------------------------------------------------


def test_unresolved_certificate_is_not_rendered_as_no_cert():
    """THE dialog regression. Pre-fix the dropdown read exactly `(none)`."""
    prof = Profile(name="acct", certificate="corp-ca")
    dd = _cert_dropdown(_open(prof, cert_names=["other-ca"]))

    assert dd is not None
    assert dd.value != _NO_CERT


def test_unresolved_certificate_option_names_the_missing_certificate():
    """The operator is told WHICH certificate is unaccounted for."""
    prof = Profile(name="acct", certificate="corp-ca")
    dd = _cert_dropdown(_open(prof, cert_names=["other-ca"]))

    selected = next(o for o in dd.options if o.key == dd.value)
    assert "corp-ca" in (selected.text or "")


def test_unresolved_state_is_distinguishable_from_a_deliberate_no_cert():
    """The two states must be tellable apart — that is the whole requirement
    (AC3)."""
    missing = _cert_dropdown(
        _open(Profile(name="a", certificate="corp-ca"), ["other-ca"])
    )
    deliberate = _cert_dropdown(
        _open(Profile(name="b", certificate=None), ["other-ca"])
    )

    assert deliberate.value == _NO_CERT
    assert missing.value != deliberate.value


def test_unresolved_state_is_flagged_by_the_border_colour():
    """AC3's second half: mirror the proxy's warning border rather than
    inventing a third presentation."""
    from src.ui.theme.colors import COLORS

    missing = _cert_dropdown(
        _open(Profile(name="a", certificate="corp-ca"), ["other-ca"])
    )
    deliberate = _cert_dropdown(
        _open(Profile(name="b", certificate=None), ["other-ca"])
    )

    assert missing.border_color == COLORS["warning"]
    assert deliberate.border_color == COLORS["card_border"]


def test_unresolved_state_explains_itself_in_the_hint():
    prof = Profile(name="acct", certificate="corp-ca")
    page = _open(prof, cert_names=["other-ca"])

    joined = " ".join(_texts(page))
    assert "corp-ca" in joined
    assert "not found" in joined.lower()
    assert "keeps it assigned" in joined or "keep assigned" in joined


def test_quarantined_cert_store_still_flags_the_assignment():
    """Route B: EVERY name is absent, not just one — an unreadable
    certificates.json is quarantined and the store left empty, which is exactly
    an empty cert_names."""
    prof = Profile(name="acct", certificate="corp-ca")
    dd = _cert_dropdown(_open(prof, cert_names=[]))

    assert dd.value != _NO_CERT
    selected = next(o for o in dd.options if o.key == dd.value)
    assert "corp-ca" in (selected.text or "")


def test_skipped_malformed_record_still_flags_the_assignment():
    """Route A: ONE name absent from an otherwise populated dropdown — the
    store skips a single unparseable record and continues."""
    prof = Profile(name="acct", certificate="corp-ca")
    dd = _cert_dropdown(_open(prof, cert_names=["other-ca"]))

    assert dd.value != _NO_CERT
    keys = [o.key for o in dd.options]
    assert "other-ca" in keys, "the surviving certificate should still be offered"


# --------------------------------------------------------------------------
# Saving from that state does not discard the assignment.
# --------------------------------------------------------------------------


def test_saving_an_unresolved_certificate_sends_leave_unchanged():
    """Link 2, and the finding this test exists for. Pre-fix this sent `""` —
    the model's explicit clear, produced from a state the dialog could not
    account for."""
    captured = {}
    prof = Profile(name="acct", certificate="corp-ca")
    page = _open(
        prof,
        ["other-ca"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_cert(captured) is CERT_UNCHANGED


def test_saving_an_unresolved_certificate_preserves_the_stored_value_end_to_end():
    """Bound to the RESOLVED OUTCOME, not to 'a directive was sent': the
    certificate the model would actually store must still be the original."""
    captured = {}
    prof = Profile(name="acct", certificate="corp-ca")
    page = _open(
        prof,
        [],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    resolved = resolve_cert_assignment(_captured_cert(captured), prof.certificate)
    assert resolved == "corp-ca"


def test_saving_a_deliberate_no_cert_still_sends_the_empty_string():
    """Clearing stays expressible AND KEEPS ITS SPELLING (AC5). Unlike the
    proxy and pool dialogs, which had to switch to a *_NONE directive, this one
    must go on sending "" — the model reads it as the clear and
    test_update_can_clear_certificate pins that. A directive here would be a
    second way to say something already expressible, and would put that test at
    risk."""
    captured = {}
    prof = Profile(name="acct", certificate=None)
    page = _open(
        prof,
        ["other-ca"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_cert(captured) == ""


def test_saving_a_resolved_certificate_sends_the_name():
    captured = {}
    prof = Profile(name="acct", certificate="other-ca")
    page = _open(
        prof,
        ["other-ca"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _submit(page)

    assert _captured_cert(captured) == "other-ca"


def test_operator_can_still_switch_away_from_an_unresolved_certificate():
    """The unresolved state is not a trap: picking a real certificate
    reassigns."""
    captured = {}
    prof = Profile(name="acct", certificate="corp-ca")
    page = _open(
        prof,
        ["other-ca"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _cert_dropdown(page).value = "other-ca"
    _submit(page)

    assert _captured_cert(captured) == "other-ca"


def test_operator_can_still_clear_an_unresolved_certificate():
    """And can deliberately un-assign it: picking "(none)" from the unresolved
    state is an explicit choice and still sends the clear."""
    captured = {}
    prof = Profile(name="acct", certificate="corp-ca")
    page = _open(
        prof,
        ["other-ca"],
        on_save=lambda *a: captured.setdefault("args", a) and None,
    )
    _cert_dropdown(page).value = _NO_CERT
    _submit(page)

    assert _captured_cert(captured) == ""


# --------------------------------------------------------------------------
# The other two fields of the family are untouched (AC7).
# --------------------------------------------------------------------------


def test_the_proxy_and_pool_controls_are_unaffected():
    """A profile whose certificate is unresolved but whose proxy and pool
    resolve normally must still render those two exactly as before."""
    prof = Profile(
        name="acct", certificate="corp-ca", proxy="p1", bookmark_pool="pool1"
    )
    page = _FakePage()
    open_profile_dialog(
        page,
        object(),
        on_save=lambda *a: None,
        profile=prof,
        proxy_names=["p1"],
        pool_names=["pool1"],
        all_bookmarks=[],
        cert_names=[],
    )
    values = [
        c.value for c in _walk(page.shown) if isinstance(c, ft.Dropdown)
    ]
    assert "p1" in values
    assert "pool1" in values
