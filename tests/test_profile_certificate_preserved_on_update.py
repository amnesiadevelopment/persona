"""PS-263: an edit made for an unrelated reason must not un-assign a
certificate the certificate list could not account for.

The model-side half of ``test_profile_dialog_unresolved_certificate.py``, and
the sibling of ``test_profile_proxy_preserved_on_update.py`` (PS-44) and
``test_profile_bookmark_pool_assignment.py`` (PS-157).

Every assertion here is on the PERSISTED value after a real ``update_profile``
against a real ``ProfileManager`` — never on "a helper was called" and never on
a directive constant existing. A test that asserts ``CERT_UNCHANGED is
CERT_UNCHANGED`` would stay green with the whole write path removed.

The asymmetry with the two siblings is pinned here on purpose. On this field
``""`` still CLEARS and ``None`` still preserves, both unchanged — see
``services/profile/cert_assignment.py``. The state that had no spelling was
neither of those: it was "I cannot account for the stored assignment", which the
dialog used to spell ``""``.
"""
import pytest

from src.services.profile.cert_assignment import (
    CERT_UNCHANGED,
    CertDirective,
    cert_for_new_profile,
    resolve_cert_assignment,
)


@pytest.fixture
def pm(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(tmp_path / "p.json"))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(tmp_path / "data"))
    import importlib

    from src.core import config as cfg
    importlib.reload(cfg)
    from src.services.profile import manager as mgr
    importlib.reload(mgr)
    return mgr.ProfileManager()


def _reloaded():
    """A FRESH manager over the same file — AC4's round trip. Asserting on the
    in-memory object would pass against a change that never reached disk."""
    from src.services.profile import manager as mgr
    return mgr.ProfileManager()


# --------------------------------------------------------------------------
# AC1 — the defect itself, on the persisted value.
# --------------------------------------------------------------------------


def test_unrelated_edit_preserves_an_unresolvable_certificate(pm):
    """THE regression. The dialog, unable to find 'corp-ca' in cert_names, now
    sends CERT_UNCHANGED instead of "" — and a notes edit leaves the assignment
    alone."""
    pm.add_profile("acct", "", "windows", certificate="corp-ca")

    pm.update_profile(
        "acct", "acct", "", "windows",
        new_notes="renamed a note, said nothing about the certificate",
        new_certificate=CERT_UNCHANGED,
    )

    assert pm.profiles["acct"].certificate == "corp-ca"


def test_the_preserved_assignment_survives_a_round_trip(pm):
    """AC4: a fresh ProfileManager over the same file still sees it."""
    pm.add_profile("acct", "", "windows", certificate="corp-ca")
    pm.update_profile(
        "acct", "acct", "", "windows",
        new_notes="unrelated",
        new_certificate=CERT_UNCHANGED,
    )

    assert _reloaded().profiles["acct"].certificate == "corp-ca"


def test_a_rename_preserves_an_unresolvable_certificate(pm):
    """The other unrelated edit the dialog performs — and the one PS-44's
    docstring names first."""
    pm.add_profile("acct", "", "windows", certificate="corp-ca")

    pm.update_profile(
        "acct", "acct-renamed", "", "windows", new_certificate=CERT_UNCHANGED
    )

    assert _reloaded().profiles["acct-renamed"].certificate == "corp-ca"


# --------------------------------------------------------------------------
# AC6 — the recorded verdict survives as a consequence, not as a second fix.
# --------------------------------------------------------------------------


def test_the_recorded_trust_verdict_survives_the_unrelated_edit(pm):
    """cert_trust_status was collateral: update_profile clears it on a real
    reassignment, and the accidental clear looked exactly like one. The
    conditional is untouched — once the assignment survives, so does the
    verdict."""
    pm.add_profile("acct", "", "windows", certificate="corp-ca")
    pm.set_cert_trust_status("acct", "trusted")

    pm.update_profile(
        "acct", "acct", "", "windows",
        new_notes="unrelated",
        new_certificate=CERT_UNCHANGED,
    )

    reloaded = _reloaded().profiles["acct"]
    assert reloaded.certificate == "corp-ca"
    assert reloaded.cert_trust_status == "trusted"


def test_a_real_reassignment_still_invalidates_the_verdict(pm):
    """The defence AC6 requires to be byte-identical, exercised from the other
    side: a genuine change still clears the verdict, because it describes the
    OTHER certificate's CA."""
    pm.add_profile("acct", "", "windows", certificate="corp-ca")
    pm.set_cert_trust_status("acct", "trusted")

    pm.update_profile("acct", "acct", "", "windows", new_certificate="other-ca")

    reloaded = _reloaded().profiles["acct"]
    assert reloaded.certificate == "other-ca"
    assert reloaded.cert_trust_status is None


# --------------------------------------------------------------------------
# AC5 — the two meanings this field ALREADY had are unchanged.
# --------------------------------------------------------------------------


def test_the_empty_string_still_clears(pm):
    """The instruction test_update_can_clear_certificate pins, restated here so
    this file records the whole contract in one place. If the directive idiom
    had been copied literally from resolve_proxy_assignment, this would be the
    assertion that broke."""
    pm.add_profile("acct", "", "windows", certificate="corp-ca")

    pm.update_profile("acct", "acct", "", "windows", new_certificate="")

    assert _reloaded().profiles["acct"].certificate is None


def test_saying_nothing_still_preserves(pm):
    """Absence already preserved on this field BEFORE the fix — that is what
    made it different from the proxy and pool, and it must stay true."""
    pm.add_profile("acct", "", "windows", certificate="corp-ca")

    pm.update_profile("acct", "acct", "", "windows", new_notes="unrelated")

    assert _reloaded().profiles["acct"].certificate == "corp-ca"


def test_a_name_still_reassigns(pm):
    pm.add_profile("acct", "", "windows", certificate="corp-ca")

    pm.update_profile("acct", "acct", "", "windows", new_certificate="other-ca")

    assert _reloaded().profiles["acct"].certificate == "other-ca"


# --------------------------------------------------------------------------
# The directive can never be mistaken for a certificate NAME.
# --------------------------------------------------------------------------


def test_the_directive_is_never_stored_as_a_certificate_name(pm):
    """A directive object is TRUTHY, so `certificate or None` would have stored
    it. Whatever ends up on disk must be a string or None — never a repr."""
    pm.add_profile("acct", "", "windows", certificate="corp-ca")
    pm.update_profile("acct", "acct", "", "windows", new_certificate=CERT_UNCHANGED)

    import json
    import os
    raw = json.load(open(os.environ["PERSONA_PROFILES_FILE"], encoding="utf-8"))
    assert raw["acct"]["certificate"] == "corp-ca"
    assert isinstance(raw["acct"]["certificate"], str)


def test_the_directive_cannot_compare_equal_to_a_certificate_name():
    """A class, not a sentinel string: it can never collide with a real name,
    and it cannot survive a JSON round trip pretending to be one."""
    assert CERT_UNCHANGED != "CERT_UNCHANGED"
    assert CERT_UNCHANGED != ""
    assert not isinstance(CERT_UNCHANGED, str)
    assert isinstance(CERT_UNCHANGED, CertDirective)


def test_a_directive_reaching_the_create_path_stores_no_certificate(pm):
    """The dialog shares ONE value across create and edit. The unresolved state
    cannot arise on create (there is no profile), but the guard is what keeps a
    directive from ever being stored as a name if that changes — exactly what
    proxy_for_new_profile documents."""
    pm.add_profile("fresh", "", "windows", certificate=CERT_UNCHANGED)

    assert _reloaded().profiles["fresh"].certificate is None


# --------------------------------------------------------------------------
# The resolver, stated directly. (Unit-level, and deliberately NOT a substitute
# for the persisted-value assertions above.)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "supplied,stored,expected",
    [
        (CERT_UNCHANGED, "corp-ca", "corp-ca"),
        (CERT_UNCHANGED, None, None),
        (None, "corp-ca", "corp-ca"),
        ("", "corp-ca", None),
        ("other-ca", "corp-ca", "other-ca"),
        ("other-ca", None, "other-ca"),
    ],
)
def test_resolve_cert_assignment(supplied, stored, expected):
    assert resolve_cert_assignment(supplied, stored) == expected


@pytest.mark.parametrize(
    "supplied,expected",
    [(CERT_UNCHANGED, None), (None, None), ("", None), ("corp-ca", "corp-ca")],
)
def test_cert_for_new_profile(supplied, expected):
    assert cert_for_new_profile(supplied) == expected


# --------------------------------------------------------------------------
# AC7 — the proxy and pool paths are untouched.
# --------------------------------------------------------------------------


def test_the_proxy_and_pool_assignments_are_untouched_by_a_cert_edit(pm):
    from src.services.profile.pool_assignment import POOL_UNCHANGED
    from src.services.profile.proxy_assignment import PROXY_UNCHANGED

    pm.add_profile(
        "acct", "p1", "windows", bookmark_pool="pool1", certificate="corp-ca"
    )

    pm.update_profile(
        "acct", "acct", PROXY_UNCHANGED, "windows",
        new_bookmark_pool=POOL_UNCHANGED,
        new_certificate=CERT_UNCHANGED,
    )

    reloaded = _reloaded().profiles["acct"]
    assert reloaded.proxy == "p1"
    assert reloaded.bookmark_pool == "pool1"
    assert reloaded.certificate == "corp-ca"


# --------------------------------------------------------------------------
# END TO END — the real dialog driving the real manager onto real disk.
#
# This is the test AC9's falsification targets: restore the
# `certificate = "" if certificate == _NO_CERT` fallback with everything else
# in place and this goes RED on the PERSISTED value, not on "a directive
# constant exists". The two halves above each pin one end of the chain; only
# this one pins that they are actually connected.
# --------------------------------------------------------------------------


def test_dialog_to_disk_an_unrelated_edit_keeps_an_unresolvable_certificate(pm):
    flet = pytest.importorskip("flet")
    from src.ui.dialogs.profile import open_profile_dialog

    pm.add_profile("acct", "", "windows", certificate="corp-ca")
    pm.set_cert_trust_status("acct", "trusted")
    profile = pm.profiles["acct"]

    class _FakePage:
        def show_dialog(self, dlg):
            self.shown = dlg

        def pop_dialog(self):
            pass

        def update(self):
            pass

    def on_save(*a):
        # The operator opened the dialog to edit the NOTES. Everything the
        # dialog hands back for the certificate is forwarded verbatim, exactly
        # as ui/actions/profile.py does.
        pm.update_profile(
            "acct", a[0], a[1], a[2], a[3], a[4], a[5], a[6],
            new_notes="just a note",
            new_engine=a[8], new_resolution=a[9], new_certificate=a[10],
        )
        return None

    page = _FakePage()
    open_profile_dialog(
        page, object(), on_save=on_save, profile=profile,
        proxy_names=[], pool_names=[], all_bookmarks=[],
        # Route B: an unreadable certificates.json is quarantined, so the store
        # is empty and EVERY name is absent.
        cert_names=[],
    )

    def _walk(c):
        yield c
        for attr in ("content", "controls", "actions"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            for x in (child if isinstance(child, list) else [child]):
                if x is not None and hasattr(x, "__dict__"):
                    yield from _walk(x)

    btn = next(
        c for c in _walk(page.shown)
        if isinstance(c, flet.Button)
        and getattr(c, "content", None) in ("[ create ]", "[ save ]")
    )
    btn.on_click(None)

    reloaded = _reloaded().profiles["acct"]
    assert reloaded.notes == "just a note", "the edit the operator MEANT to make"
    assert reloaded.certificate == "corp-ca", (
        "an edit made for an unrelated reason un-assigned a certificate the "
        "dialog could not account for"
    )
    assert reloaded.cert_trust_status == "trusted", (
        "the recorded verdict was destroyed as collateral of the accidental "
        "reassignment"
    )
