"""PS-44: an edit that says nothing about the proxy must leave the proxy alone.

A profile with a proxy assigned could lose that assignment as a side effect of
an edit made for an unrelated reason — a rename, a note, a device type. Nothing
warned and nothing refused, and the profile then launched DIRECT on the
operator's real IP.

The chain, each link covered below:

1. The profile dialog fell back to DIRECT when it could not find the assigned
   proxy in the available list (``test_profile_dialog_unresolved_proxy.py``).
2. Submitting made that display fallback a stored un-assignment.
3. ``ProfileManager.update_profile`` took the proxy as a REQUIRED argument and
   assigned it unconditionally, so absence and emptiness both cleared it. That
   is what this module pins.
4. ``_require_proxy_resolved`` then had nothing to refuse — a profile with no
   proxy is a legitimate configuration. Covered here by asserting the guard's
   PREMISE survives, deliberately without touching the guard itself.

The two store conditions that make step 1 reachable are reproduced directly
against the real ``ProxyStore``, because the ticket is only a live defect if the
available list can genuinely be missing a name a profile still references.
"""
import json

import pytest

from src.models.profile import Profile
from src.services.browser.process import _require_proxy_resolved
from src.services.profile.manager import ProfileManager
from src.services.profile.proxy_assignment import (
    PROXY_NONE,
    PROXY_UNCHANGED,
    proxy_for_new_profile,
    resolve_proxy_assignment,
)


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    return ProfileManager()


# --------------------------------------------------------------------------
# The headline assertion, stated exactly as the ticket words it: an update that
# supplies no proxy leaves a proxied profile still proxied.
# --------------------------------------------------------------------------


def test_update_without_proxy_keeps_a_proxied_profile_proxied(mgr):
    """THE regression. Against the pre-fix model this fails: `new_proxy` was
    required and `profile.proxy = new_proxy or None` cleared it."""
    mgr.add_profile("shopper", "PL-residential", "windows")

    # A rename — an edit that says nothing whatsoever about the proxy.
    assert mgr.update_profile("shopper", "shopper-eu") is True

    assert mgr.profiles["shopper-eu"].proxy == "PL-residential"


def test_notes_edit_keeps_proxy(mgr):
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", new_notes="primary account")
    assert mgr.profiles["shopper"].proxy == "PL-residential"


def test_device_type_edit_keeps_proxy(mgr):
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", new_device_type="mobile")
    assert mgr.profiles["shopper"].proxy == "PL-residential"


def test_os_edit_keeps_proxy(mgr):
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", new_os="linux")
    assert mgr.profiles["shopper"].proxy == "PL-residential"
    assert mgr.profiles["shopper"].os_type == "linux"


def test_empty_string_proxy_is_not_a_clear(mgr):
    """An empty value reads as UNCHANGED, never as a clear. This is the exact
    value the dialog used to send after its DIRECT fallback."""
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", "")
    assert mgr.profiles["shopper"].proxy == "PL-residential"


def test_none_proxy_is_not_a_clear(mgr):
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", None)
    assert mgr.profiles["shopper"].proxy == "PL-residential"


def test_preserved_proxy_survives_reload(mgr):
    """Not just in memory — the preserved assignment is what got persisted."""
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper-eu", new_notes="renamed")
    assert ProfileManager().profiles["shopper-eu"].proxy == "PL-residential"


# --------------------------------------------------------------------------
# Clearing stays possible — but only by SAYING so.
# --------------------------------------------------------------------------


def test_proxy_none_clears_the_assignment(mgr):
    """A deliberate DIRECT is still expressible; it just has to be stated."""
    mgr.add_profile("shopper", "PL-residential", "windows")
    assert mgr.update_profile("shopper", "shopper", PROXY_NONE) is True
    assert mgr.profiles["shopper"].proxy is None


def test_proxy_none_clear_persists(mgr):
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", PROXY_NONE)
    assert ProfileManager().profiles["shopper"].proxy is None


def test_reassignment_still_works(mgr):
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", "NL-datacenter")
    assert mgr.profiles["shopper"].proxy == "NL-datacenter"


def test_assigning_to_a_direct_profile_still_works(mgr):
    mgr.add_profile("shopper", PROXY_NONE, "windows")
    assert mgr.profiles["shopper"].proxy is None
    mgr.update_profile("shopper", "shopper", "PL-residential")
    assert mgr.profiles["shopper"].proxy == "PL-residential"


def test_explicit_unchanged_directive_is_a_noop(mgr):
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", PROXY_UNCHANGED)
    assert mgr.profiles["shopper"].proxy == "PL-residential"


def test_directive_is_never_stored_as_a_proxy_name(mgr):
    """A directive object must never end up in the record pretending to be a
    name — it would serialize into profiles.json as garbage."""
    mgr.add_profile("a", PROXY_NONE, "windows")
    mgr.add_profile("b", PROXY_UNCHANGED, "windows")
    for name in ("a", "b"):
        assert mgr.profiles[name].proxy is None
    mgr.save_profiles()
    # Round-trips through JSON without raising, and carries no directive.
    from src.core.config import PROFILES_FILE
    raw = json.loads(open(PROFILES_FILE, encoding="utf-8").read())
    for rec in raw.values():
        assert rec.get("proxy") in (None, "")


# --------------------------------------------------------------------------
# The guard's PREMISE. The launch guard itself is out of scope and untouched —
# what this asserts is that the assignment it keys on still EXISTS after an
# unrelated edit, which is the thing that used to disappear.
# --------------------------------------------------------------------------


def test_guard_still_has_something_to_refuse_after_an_unrelated_edit(mgr):
    """Before the fix, a rename cleared the proxy, so _require_proxy_resolved
    correctly decided there was nothing to guard and the profile launched
    DIRECT. The assignment now survives, so an unresolvable proxy is still
    refused. The guard is unchanged; only its premise was being destroyed."""
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper-eu", new_notes="unrelated edit")

    edited = mgr.profiles["shopper-eu"]
    # resolve() yielded nothing for the assigned name (the store lost it).
    with pytest.raises(Exception) as exc:
        _require_proxy_resolved(edited, None)
    assert "Refusing to launch DIRECT" in str(exc.value)


def test_guard_stays_silent_for_a_deliberately_direct_profile(mgr):
    """The other arm: a profile the operator deliberately set to DIRECT is a
    legitimate configuration and must still launch. Proving the guard is not
    being made to fire on everything."""
    mgr.add_profile("shopper", "PL-residential", "windows")
    mgr.update_profile("shopper", "shopper", PROXY_NONE)
    _require_proxy_resolved(mgr.profiles["shopper"], None)  # must not raise


# --------------------------------------------------------------------------
# resolve_proxy_assignment / proxy_for_new_profile in isolation.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "supplied,stored,expected",
    [
        (PROXY_UNCHANGED, "PL", "PL"),
        (PROXY_UNCHANGED, None, None),
        (PROXY_NONE, "PL", None),
        (PROXY_NONE, None, None),
        ("", "PL", "PL"),          # empty preserves, never clears
        (None, "PL", "PL"),
        ("NL", "PL", "NL"),        # a real name reassigns
        ("NL", None, "NL"),
    ],
)
def test_resolve_proxy_assignment(supplied, stored, expected):
    assert resolve_proxy_assignment(supplied, stored) == expected


@pytest.mark.parametrize(
    "supplied,expected",
    [(PROXY_UNCHANGED, None), (PROXY_NONE, None), ("", None),
     (None, None), ("PL", "PL")],
)
def test_proxy_for_new_profile(supplied, expected):
    assert proxy_for_new_profile(supplied) == expected


# --------------------------------------------------------------------------
# The two store conditions, reproduced directly against the REAL ProxyStore.
# Both are deliberate protections and are NOT modified here — they are the
# conditions that make the dialog's missing-name state reachable in the first
# place, which is what separates this defect from a theoretical one.
# --------------------------------------------------------------------------


def _store(tmp_path, monkeypatch, payload: str):
    from src.services.proxy.store import ProxyStore

    path = tmp_path / "proxies.json"
    path.write_text(payload, encoding="utf-8")
    return ProxyStore(path=str(path))


def _good(name):
    return {"name": name, "url": f"socks5://127.0.0.1:1080#{name}"}


def test_one_malformed_record_leaves_exactly_that_name_absent(
    tmp_path, monkeypatch
):
    """Condition A: the store skips a record it cannot parse and carries on.
    The operator then sees a NORMAL, POPULATED dropdown with one name missing —
    which is why the old DIRECT fallback was so convincing."""
    payload = json.dumps({
        "PL-residential": {"name": "PL-residential"},   # no url -> unparseable
        "NL-datacenter": _good("NL-datacenter"),
        "DE-mobile": _good("DE-mobile"),
    })
    store = _store(tmp_path, monkeypatch, payload)

    names = store.names()
    assert "PL-residential" not in names       # the profile's proxy is absent
    assert "NL-datacenter" in names            # but the list is not empty
    assert "DE-mobile" in names


def test_quarantined_store_leaves_every_name_absent(tmp_path, monkeypatch):
    """Condition B: an unreadable file is moved aside and the store is left
    empty on purpose, so a later save cannot overwrite the operator's saved
    proxies and their credentials with an empty set."""
    store = _store(tmp_path, monkeypatch, "{ this is not json")

    assert store.names() == []
    # The protection itself: the original was preserved beside the path, not
    # destroyed. Asserted so a future change that weakens it fails HERE too.
    moved = list(tmp_path.glob("proxies.json.corrupt-*"))
    assert moved, "the unreadable file must be preserved, not overwritten"


def test_profile_survives_an_edit_while_its_proxy_is_unlisted(
    mgr, tmp_path, monkeypatch
):
    """The two halves joined: the store genuinely cannot offer the name, and an
    edit made in that state still leaves the profile proxied. This is the
    end-to-end shape of the reported defect, minus the Flet layer (covered in
    test_profile_dialog_unresolved_proxy.py)."""
    payload = json.dumps({
        "PL-residential": {"name": "PL-residential"},   # unparseable
        "NL-datacenter": _good("NL-datacenter"),
    })
    store = _store(tmp_path, monkeypatch, payload)
    mgr.add_profile("shopper", "PL-residential", "windows")

    assert "PL-residential" not in store.names()   # the precondition holds
    mgr.update_profile("shopper", "shopper-eu", new_notes="just renaming")

    assert mgr.profiles["shopper-eu"].proxy == "PL-residential"
