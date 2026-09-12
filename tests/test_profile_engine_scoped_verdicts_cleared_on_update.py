"""PS-426: an edit that actually CHANGES the engine must clear the two
verdicts that were only ever true of the engine being replaced — and an edit
that does not change it must leave them exactly alone.

The sibling of ``test_profile_certificate_preserved_on_update.py`` (PS-263) and
``test_profile_proxy_preserved_on_update.py`` (PS-44), and the mirror image of
the first: those two pin what an unrelated edit must PRESERVE, this one pins
what a real engine change must DISCARD. Both halves live here, because the
conditional is the whole fix — an unconditional clear passes every assertion in
the first section below and fails every assertion in the second.

Every assertion is on the PERSISTED value read back through a FRESHLY
CONSTRUCTED ``ProfileManager`` — never on "a helper was called", and never on
the in-memory object, which would stay green against a change that never
reached disk.

WHY THESE TWO FIELDS. ``cookie_import_status`` describes an import into a
Chromium-shaped store (``Default/Cookies``, v10/AES values) that Firefox never
opens — which is why ``_cookie_engine_refusal`` refuses both import and export
on that engine, while the dialog's cookie render gate has no engine term at all
and keeps rendering ``last import: …`` beside the button that calls the same
operation unavailable. ``cert_trust_status`` is a Firefox-only soft-fail verdict
the Chromium arm cannot produce at all. Neither claim survives the swap.
"""
import pytest


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
    """A FRESH manager over the same file — the survives-a-restart reading.
    Asserting on the in-memory object would pass against a clear that never
    reached disk."""
    from src.services.profile import manager as mgr
    return mgr.ProfileManager()


def _seeded(pm, name="creep", engine="chromium"):
    """A profile carrying BOTH verdicts. ``proxy`` is a required positional on
    add_profile — a keyword-only call raises TypeError before it reaches the
    code under test, which is a harness error and not a result."""
    assert pm.add_profile(name, "", "windows", engine=engine)
    assert pm.set_cookie_status(name, "creep.json · 11 cookies")
    assert pm.set_cert_trust_status(name, "trusted")
    stored = _reloaded().profiles[name]
    assert stored.cookie_import_status == "creep.json · 11 cookies"
    assert stored.cert_trust_status == "trusted"
    return stored


# --------------------------------------------------------------------------
# AC1 / AC4 — a REAL engine change clears both, in BOTH directions.
# --------------------------------------------------------------------------


def test_chromium_to_firefox_clears_both_verdicts(pm):
    """THE regression, in the direction the harm is most legible: the cookies
    sit in a Default/Cookies tree Firefox never opens, and the dialog kept
    rendering an affirmative 'last import: …' over them."""
    _seeded(pm, engine="chromium")

    assert pm.update_profile("creep", "creep", new_engine="firefox")

    reloaded = _reloaded().profiles["creep"]
    assert reloaded.engine == "firefox"
    assert reloaded.cookie_import_status is None
    assert reloaded.cert_trust_status is None


def test_firefox_to_chromium_clears_both_verdicts(pm):
    """AC4's other direction. cert_trust_status is a Firefox-only verdict, so
    carrying it onto Chromium leaves a 'trusted' no Chromium launch could ever
    have written."""
    _seeded(pm, engine="firefox")

    assert pm.update_profile("creep", "creep", new_engine="chromium")

    reloaded = _reloaded().profiles["creep"]
    assert reloaded.engine == "chromium"
    assert reloaded.cookie_import_status is None
    assert reloaded.cert_trust_status is None


def test_the_engine_change_still_takes_effect(pm):
    """The clear must not have been bought by skipping the assignment it sits
    in front of."""
    _seeded(pm, engine="chromium")

    pm.update_profile("creep", "creep", new_engine="firefox")

    import json
    import os
    raw = json.load(open(os.environ["PERSONA_PROFILES_FILE"], encoding="utf-8"))
    assert raw["creep"]["engine"] == "firefox"
    assert raw["creep"]["cookie_import_status"] is None
    assert raw["creep"]["cert_trust_status"] is None


def test_an_engine_change_bundled_with_other_edits_still_clears(pm):
    """The dialog sends a whole form, not one field. A rename riding along must
    not talk the clear out of firing."""
    _seeded(pm, engine="chromium")

    assert pm.update_profile(
        "creep", "creep-renamed", new_engine="firefox", new_notes="and a note"
    )

    reloaded = _reloaded().profiles["creep-renamed"]
    assert reloaded.cookie_import_status is None
    assert reloaded.cert_trust_status is None


# --------------------------------------------------------------------------
# AC3 — an edit that does NOT change the engine preserves both. This is the
# conditional's whole reason for existing, and the section a careless
# unconditional clear fails.
# --------------------------------------------------------------------------


def test_a_rename_preserves_both_verdicts(pm):
    _seeded(pm)

    assert pm.update_profile("creep", "creep-renamed")

    reloaded = _reloaded().profiles["creep-renamed"]
    assert reloaded.cookie_import_status == "creep.json · 11 cookies"
    assert reloaded.cert_trust_status == "trusted"


def test_a_notes_edit_preserves_both_verdicts(pm):
    _seeded(pm)

    assert pm.update_profile("creep", "creep", new_notes="unrelated")

    reloaded = _reloaded().profiles["creep"]
    assert reloaded.cookie_import_status == "creep.json · 11 cookies"
    assert reloaded.cert_trust_status == "trusted"


def test_a_patch_re_sending_the_same_engine_preserves_both_verdicts(pm):
    """The criterion a careless fix fails. The REST lane passes
    ``new_engine=supplied.get("engine")`` straight through, so a client that
    round-trips the whole record re-sends the engine it already has on EVERY
    edit — and that is not a change."""
    _seeded(pm, engine="chromium")

    assert pm.update_profile("creep", "creep", new_engine="chromium")

    reloaded = _reloaded().profiles["creep"]
    assert reloaded.engine == "chromium"
    assert reloaded.cookie_import_status == "creep.json · 11 cookies"
    assert reloaded.cert_trust_status == "trusted"


def test_the_retired_camoufox_spelling_is_not_an_engine_change(pm):
    """``camoufox`` is the retired name of the Firefox engine
    (``coherence.normalize_engine``). A legacy record re-sent as ``firefox`` is
    the SAME engine spelled currently — a raw ``!=`` would discard two verdicts
    that are still exactly about the engine the profile already had."""
    _seeded(pm, engine="chromium")
    # Reach past add_profile's coherence gate: only a record already on disk can
    # carry the retired spelling, which is the population this guards.
    pm.profiles["creep"].engine = "camoufox"
    pm.save_profiles()

    assert pm.update_profile("creep", "creep", new_engine="firefox")

    reloaded = _reloaded().profiles["creep"]
    assert reloaded.engine == "firefox"
    assert reloaded.cookie_import_status == "creep.json · 11 cookies"
    assert reloaded.cert_trust_status == "trusted"


# --------------------------------------------------------------------------
# AC6 — the adjacent conditional cert-clear is the model, not a casualty.
# --------------------------------------------------------------------------


def test_a_certificate_reassignment_still_clears_only_the_cert_verdict(pm):
    """The idiom this fix mirrors, exercised from the other side: a real
    certificate change clears cert_trust_status and says nothing about the
    cookie verdict, which is not scoped to the certificate."""
    _seeded(pm)
    pm.update_profile("creep", "creep", new_certificate="corp-ca")
    pm.set_cert_trust_status("creep", "trusted")

    assert pm.update_profile("creep", "creep", new_certificate="other-ca")

    reloaded = _reloaded().profiles["creep"]
    assert reloaded.certificate == "other-ca"
    assert reloaded.cert_trust_status is None
    assert reloaded.cookie_import_status == "creep.json · 11 cookies"


# --------------------------------------------------------------------------
# CONTROL — bound 1: this is state coherence, not a masking change.
# --------------------------------------------------------------------------


def test_the_fingerprint_seed_is_untouched_by_the_engine_edit(pm):
    """Bound 1 of the ticket, asserted rather than asserted-about: no
    fingerprint moves here. If this ever goes red the change has grown a
    masking dimension it was explicitly scoped not to have."""
    seeded = _seeded(pm, engine="chromium")
    before = seeded.fingerprint_seed

    pm.update_profile("creep", "creep", new_engine="firefox")

    assert _reloaded().profiles["creep"].fingerprint_seed == before


# --------------------------------------------------------------------------
# Bound 4 — a third engine-scoped field must be one line, not a fourth branch.
# --------------------------------------------------------------------------


def test_every_named_engine_scoped_field_is_cleared(pm):
    """Drives the registry rather than the two names, so a field added to
    ENGINE_SCOPED_VERDICT_FIELDS without a clear cannot pass, and a field
    removed from it cannot pass silently either."""
    from src.services.profile.manager import ENGINE_SCOPED_VERDICT_FIELDS

    assert ENGINE_SCOPED_VERDICT_FIELDS, "registry must not be empty"
    _seeded(pm, engine="chromium")

    pm.update_profile("creep", "creep", new_engine="firefox")

    reloaded = _reloaded().profiles["creep"]
    for field in ENGINE_SCOPED_VERDICT_FIELDS:
        assert getattr(reloaded, field) is None, field
