"""A REFUSED launch must land on the PROFILE that refused, say WHICH refusal
applied, and still be there an hour later.

Before this slice a refusal reached the operator through exactly one channel:
``log_callback(f"Error starting process: {e}")`` in the launcher. It scrolled
away in a shared panel, nothing was written to the profile, and the card that
refused was pixel-identical to one that was never clicked. Come back an hour
later and there was no way to tell which profiles refused or why; launch twenty
at once and the reasons interleaved with routine chatter.

That misreading has a DIRECTION, which is why it is worth a suite. A refusal is
the moment the product's fail-closed protection actually fires — the system
working. An operator who does not register it reads "the profile didn't open",
and the natural next move is to remove the protection.

These tests pin the MECHANISM and the MEANING:
  - every refusal stays distinguishable at the surface (the wording works hard
    to separate "never checked successfully" from "the last check FAILED" from
    "no zone for this country", and those distinctions have to survive the trip
    to the card — they carry three different remedies, and one of them is NOT
    a re-check);
  - an ordinary error does NOT mark a card (routine noise stays quiet, or the
    marker the operator must not skim past becomes one they learn to);
  - the marker survives being ignored, and carries its AGE so it cannot quietly
    become a claim about the present;
  - nothing here reads as an invitation to launch without a proxy.

``test_falsification_*`` at the bottom assert the suite goes RED when the
capture is removed, so a green run means the mechanism is present rather than
that the assertions are vacuous.
"""
import time

import flet as ft
import pytest

from src.models.profile import Profile
from src.services.browser.launcher import BrowserLauncher
from src.services.browser.refusal import Refusal, classify_refusal
from src.services.proxy.errors import (
    GeographyDisprovenError,
    GeographyUnknownError,
    ProxyUnresolvedError,
    TimezoneUnderivableError,
)
from src.ui.components.profile_card import build_profile_card


def _noop(*a, **k):
    pass


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
            elif v is not None and not isinstance(v, str):
                walk(v)

    walk(control)
    return out


def _tooltips(control):
    out = []

    def walk(c):
        tip = getattr(c, "tooltip", None)
        if isinstance(tip, str):
            out.append(tip)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v:
                    walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)

    walk(control)
    return out


def _card(profile, *, refusal, is_running=False):
    return build_profile_card(
        profile, False, is_running, _noop, _noop, _noop, refusal=refusal
    )


def _reports_refusal(card) -> bool:
    """True if the card ASSERTS a refusal to the operator.

    Matched on the rendered word rather than on a container identity, so the
    test describes what a person can actually read off the row.
    """
    return any("refused" in t.lower() for t in _texts(card))


# --------------------------------------------------------------------------
# The classifier: WHICH refusal applied. The distinction is the point.
# --------------------------------------------------------------------------

def test_a_failed_last_check_is_not_reported_as_never_checked():
    """The single most important assertion in this file.

    ``GeographyDisprovenError`` is a SUBCLASS of ``GeographyUnknownError`` (by
    deliberate design — every fail-closed ``except GeographyUnknownError`` in
    the plumbing must keep catching it). So an isinstance chain that tests the
    parent first SWALLOWS the child and tells an operator the proxy was "never
    checked" when it WAS checked and the check FAILED — sending them to re-run
    a check they already ran instead of investigating why it failed. The
    wording upstream works hard to keep these apart; this is the test that
    stops the distinction dying in transit.
    """
    r = classify_refusal(GeographyDisprovenError("last check failed"), 100.0)
    assert r is not None
    assert r.kind == "geography_disproven"
    assert "never" not in r.label.lower(), (
        f"a FAILED check was labelled {r.label!r}, which sends the operator to "
        "re-check a proxy they already checked"
    )


def test_an_underivable_timezone_is_not_reported_as_never_checked():
    """The same swallowing hazard as the test above, on a SECOND subclass.

    ``TimezoneUnderivableError`` also subclasses ``GeographyUnknownError``, so
    the parent's branch would catch it and label it "proxy never checked" —
    and here that is worse than merely imprecise. This proxy may have been
    checked SUCCESSFULLY seconds ago; the geo response simply carried no
    ``/``-form zone. Telling the operator to check it sends them to re-run a
    check that already passed and will keep passing forever, because the
    missing thing is a ``_COUNTRY_TZ`` row, not a check result.
    """
    r = classify_refusal(TimezoneUnderivableError("no zone for NG"), 100.0)
    assert r is not None
    assert r.kind == "timezone_underivable", (
        f"got kind {r.kind!r} — the parent's branch swallowed the subclass, so "
        "the operator is pointed at the wrong remedy"
    )
    assert "never" not in r.label.lower(), (
        f"an underivable zone was labelled {r.label!r}, which sends the "
        "operator to re-check a proxy whose check may already have passed"
    )


def test_the_two_geography_subclasses_do_not_shadow_each_other():
    """The siblings must stay distinct from EACH OTHER, not just from the
    parent. Neither is a subclass of the other, so a chain that confused them
    would report a missing table row as a failed check, or vice versa — two
    completely different remedies."""
    assert not issubclass(TimezoneUnderivableError, GeographyDisprovenError)
    assert not issubclass(GeographyDisprovenError, TimezoneUnderivableError)
    assert classify_refusal(GeographyDisprovenError("x"), 1.0).kind == "geography_disproven"
    assert classify_refusal(TimezoneUnderivableError("y"), 1.0).kind == "timezone_underivable"


def test_every_refusal_is_distinguishable():
    """One distinct ``kind`` per refusal — no two may collapse.

    Named for the invariant rather than for a COUNT: the previous name said
    "three", which stopped being true the moment a fourth refusal landed, and a
    test whose name is a stale census is one nobody thinks to extend. The
    enumeration below must carry every exception ``classify_refusal`` answers,
    and the count is derived from it rather than written in — so adding a
    refusal here cannot silently leave the assertion measuring the old world.

    ``kind`` is a published contract (the API's 409 body and the MCP tool both
    branch on it), so a collapse is not cosmetic: it merges two states with two
    different remedies into one value a caller cannot tell apart.
    """
    excs = (
        ProxyUnresolvedError("x"),
        GeographyUnknownError("y"),
        GeographyDisprovenError("z"),
        TimezoneUnderivableError("w"),
    )
    kinds = {classify_refusal(exc, 1.0).kind for exc in excs}
    assert len(kinds) == len(excs), f"refusals collapsed into {kinds}"


def test_the_settled_sentence_is_passed_through_not_restated():
    # process.py composes the operator-facing sentence with the profile and
    # proxy named and the remedy spelled out. A copy in this layer would fork
    # from it at the first edit, so the exception's own message must arrive
    # intact.
    sentence = (
        "Profile 'acme' has proxy 'p1' assigned but its geography could not be "
        "established (the proxy has never been checked successfully)."
    )
    r = classify_refusal(GeographyUnknownError(sentence), 1.0)
    assert r.detail == sentence


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("engine crashed"), OSError("thread table full"), ValueError("x")],
)
def test_an_ordinary_error_is_not_a_refusal(exc):
    """Routine noise must stay quiet enough that a refusal reads as loud.

    If every transient spawn failure marked a card, the operator would learn to
    skim past the one marker that means a guard fired — which is the same
    defect as the scrolling log line, relocated.
    """
    assert classify_refusal(exc, 1.0) is None


# --------------------------------------------------------------------------
# The launcher: the refusal reaches the profile, and SURVIVES.
# --------------------------------------------------------------------------

def _refuse(bl, monkeypatch, name, exc):
    """Drive a real start_thread to a refusal, synchronously."""
    import src.services.browser.launcher as launcher_mod

    def boom(profile):
        raise exc

    monkeypatch.setattr(launcher_mod, "spawn_browser", boom)
    bl.start_thread(Profile(name=name), _noop)


def test_a_refused_launch_lands_on_the_profile_that_refused(monkeypatch):
    bl = BrowserLauncher()
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))
    r = bl.last_refusal("acme")
    assert r is not None, "the refusal reached only the log, which scrolls away"
    assert r.kind == "geography_unknown"


def test_a_refusal_does_not_bleed_onto_other_profiles(monkeypatch):
    bl = BrowserLauncher()
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))
    assert bl.last_refusal("other") is None


def test_an_ordinary_failure_leaves_no_marker(monkeypatch):
    bl = BrowserLauncher()
    _refuse(bl, monkeypatch, "acme", RuntimeError("engine crashed"))
    assert bl.last_refusal("acme") is None


def test_the_marker_survives_being_ignored(monkeypatch):
    """The whole point: it is still there an hour later.

    A session teardown must not retire it. ``_forget_session_facts`` drops facts
    about a LIVE session; a refusal describes a launch that never became one, so
    there is no session whose end could legitimately clear it.
    """
    bl = BrowserLauncher()
    _refuse(bl, monkeypatch, "acme", GeographyDisprovenError("check failed"))
    bl.stop_profile("acme")
    bl.shutdown_all()
    assert bl.last_refusal("acme") is not None, (
        "the refusal was cleared by a teardown — the marker has to outlive the "
        "moment it was created or it is just a log line again"
    )


def test_a_new_attempt_supersedes_the_previous_verdict(monkeypatch):
    """A stale 'refused' badge on a profile that now launches fine is its own
    dishonesty, so the verdict is dropped at the ATTEMPT."""
    import src.services.browser.launcher as launcher_mod

    bl = BrowserLauncher()
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))
    assert bl.last_refusal("acme") is not None

    # Second attempt: spawn succeeds. Nothing refused, so nothing is claimed.
    # The stub carries the surface the launcher's monitor/teardown actually
    # touches (stdout/poll/terminate/wait) — a thinner one leaks thread
    # exceptions that would sit in the output looking like this test's noise
    # and could hide a real failure.
    class _Proc:
        stdout = None
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: _Proc())
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    bl.start_thread(Profile(name="acme"), _noop)
    assert bl.last_refusal("acme") is None, (
        "a profile that just launched is still showing a refusal badge"
    )


def test_the_refusal_is_stamped_with_when_it_happened(monkeypatch):
    bl = BrowserLauncher()
    before = time.time()
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))
    after = time.time()
    assert before <= bl.last_refusal("acme").at <= after


def test_last_refusal_does_no_io(monkeypatch):
    # It sits on a render path that runs on every redraw.
    import socket

    bl = BrowserLauncher()
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))

    opened = []
    real = socket.socket

    class _Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            opened.append(a)
            super().__init__(*a, **k)

    socket.socket = _Spy
    try:
        bl.last_refusal("acme")
    finally:
        socket.socket = real
    assert not opened


# --------------------------------------------------------------------------
# The card: what the operator can actually READ.
# --------------------------------------------------------------------------

def test_the_card_reports_a_refusal():
    p = Profile(name="acme")
    r = Refusal("geography_unknown", "proxy never checked", "full sentence", time.time())
    assert _reports_refusal(_card(p, refusal=r))


def test_a_profile_with_no_refusal_renders_exactly_as_before():
    """An absent marker must be a REAL absence — no empty box, no placeholder,
    no dimmed variant the eye learns to skip."""
    p = Profile(name="acme")
    before = build_profile_card(p, False, False, _noop, _noop, _noop)
    after = _card(p, refusal=None)
    assert _texts(before) == _texts(after)
    assert not _reports_refusal(after)


def test_the_card_says_which_refusal_applied():
    """The distinction has to reach the SURFACE, not just the classifier."""
    now = time.time()
    failed = _texts(
        _card(
            Profile(name="a"),
            refusal=Refusal("geography_disproven", "last check failed", "d", now),
        )
    )
    never = _texts(
        _card(
            Profile(name="a"),
            refusal=Refusal("geography_unknown", "proxy never checked", "d", now),
        )
    )
    assert failed != never, (
        "a failed check and a never-checked proxy render identically — the "
        "operator is sent to re-check a proxy they already checked"
    )


def test_the_card_carries_the_age_so_it_cannot_read_as_now():
    """A marker meant to be found an hour later must say WHEN.

    Without the age, a refusal from an hour ago is read as one that just
    happened — the badge quietly stops being a historical fact and becomes a
    false claim about the present.
    """
    old = time.time() - 3600
    joined = " ".join(
        _texts(_card(Profile(name="a"), refusal=Refusal("k", "l", "d", old)))
    )
    assert "1h ago" in joined, f"no age on the marker: {joined!r}"


def test_the_full_settled_sentence_is_reachable_from_the_card():
    # The scanning label is short by design; the full sentence — which names the
    # remedy — rides the tooltip, one hover away.
    sentence = "Re-check the proxy to resolve it."
    card = _card(
        Profile(name="a"),
        refusal=Refusal("geography_disproven", "last check failed", sentence, time.time()),
    )
    assert any(sentence in t for t in _tooltips(card))


def test_a_running_profile_shows_no_refusal_badge():
    # Render-side belt: a refusal over a live browser would be exactly the stale
    # dishonesty this design exists to avoid.
    p = Profile(name="a")
    r = Refusal("geography_unknown", "proxy never checked", "d", time.time())
    assert not _reports_refusal(_card(p, refusal=r, is_running=True))


def test_bulk_refusals_stay_one_line_each():
    """Bulk launch is real, so twenty refusals must not become a wall of prose.

    The chip is spliced into the meta row that ALREADY exists, so a refused card
    gains no extra ROW versus an unrefused one — twenty refusals cost twenty
    short lines in a column the operator is already scanning.
    """
    p = Profile(name="a")
    r = Refusal("geography_unknown", "proxy never checked", "d" * 400, time.time())
    plain = _texts(_card(p, refusal=None))
    refused = _texts(_card(p, refusal=r))
    assert len(refused) == len(plain) + 1, (
        f"a refusal added {len(refused) - len(plain)} text blocks to the card"
    )
    added = [t for t in refused if t not in plain][0]
    assert len(added) < 60, f"the scanning label is a paragraph: {added!r}"


def test_refusal_wording_is_not_a_nudge_to_drop_the_proxy():
    """The standing caution: an honest "cannot establish where this profile
    would exit" must read as NOT YET KNOWN, never as an invitation to give up
    on the guard. A label like "no geography — launch direct?" would convert
    the product's own protection into a prompt to switch it off.
    """
    now = time.time()
    for exc in (
        ProxyUnresolvedError("x"),
        GeographyUnknownError("y"),
        GeographyDisprovenError("z"),
        TimezoneUnderivableError("w"),
    ):
        label = classify_refusal(exc, now).label.lower()
        for nudge in ("direct", "without a proxy", "disable", "skip", "anyway"):
            assert nudge not in label, (
                f"the refusal label {label!r} reads as an invitation to {nudge!r}"
            )


# --------------------------------------------------------------------------
# Falsification — a green suite must mean the mechanism is PRESENT.
# --------------------------------------------------------------------------

def test_falsification_removing_the_capture_goes_red(monkeypatch):
    """If the launcher stops recording refusals, the suite must fail.

    Simulates the pre-slice behaviour (refusal reaches the log only) and
    asserts the load-bearing assertion above would catch it.
    """
    import src.services.browser.launcher as launcher_mod

    monkeypatch.setattr(
        launcher_mod, "classify_refusal", lambda exc, now: None
    )
    bl = BrowserLauncher()
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))
    assert bl.last_refusal("acme") is None


def test_falsification_parent_first_ordering_goes_red():
    """If the isinstance chain were reordered to test the parent first, a FAILED
    check would be reported as "never checked". Proves the ordering test above
    is not vacuous.
    """
    exc = GeographyDisprovenError("last check failed")
    # The buggy chain, written out: parent first swallows the child.
    if isinstance(exc, GeographyUnknownError):
        buggy_kind = "geography_unknown"
    else:
        buggy_kind = "geography_disproven"
    assert buggy_kind == "geography_unknown"
    assert classify_refusal(exc, 1.0).kind != buggy_kind


# --------------------------------------------------------------------------
# The verdict outlives the SESSION, never the SUBJECT.
#
# `_last_refusal` is keyed by profile NAME, and in this codebase a name is not a
# stable identity: ProfileManager deletes them, wipes them, re-keys them on
# rename, and overwrites them on import, after which the SAME STRING can name a
# different profile. The survival property the tests above pin is deliberate and
# correct for a TEARDOWN — but applied to a destroyed identity it inverts into
# the exact dishonesty this ticket exists to prevent: a brand-new profile that
# has never been clicked rendering a red "refused" chip which, because the age is
# re-derived against the current clock, reads "just now".
#
# It also points the operator at a proxy check for a proxy that may be perfectly
# healthy — the wasted trip the disproven/unknown wording split exists to avoid.
# --------------------------------------------------------------------------

def _manager(tmp_path, monkeypatch):
    """A real ProfileManager on a temp store, wired to a real launcher exactly
    as src/ui/app.py wires them."""
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    from src.services.profile.manager import ProfileManager

    pm = ProfileManager()
    bl = BrowserLauncher()
    # The production wiring (src/ui/app.py) — both hooks, so this test cannot
    # pass against a wiring the app does not actually perform.
    pm.set_stop_hook(bl.stop_profile)
    pm.set_forget_identity_hook(bl.forget_refusal)
    return pm, bl


def test_a_deleted_profile_does_not_hand_its_refusal_to_its_replacement(
    tmp_path, monkeypatch
):
    """THE defect: delete a refused profile, recreate the name, and the new card
    must be clean.

    Name reuse after a delete is ordinary operator behaviour, not an exotic
    path. Left unfixed, a profile that has never been launched renders a red
    refusal chip claiming a guard fired on it, and the tooltip explains that
    "the profile was not opened, so nothing was disclosed" about a launch that
    never happened.
    """
    pm, bl = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))
    assert bl.last_refusal("acme") is not None, "precondition: the refusal landed"

    assert pm.delete_profile("acme") is True
    pm.add_profile("acme", "", "windows")

    assert bl.last_refusal("acme") is None, (
        "the verdict outlived the profile it described and was inherited by a "
        "different profile of the same name"
    )
    card = _card(pm.profiles["acme"], refusal=bl.last_refusal("acme"))
    assert not _reports_refusal(card), (
        "a never-clicked profile renders a refusal chip — and the age is "
        "re-derived from the clock, so it reads 'just now'"
    )


def test_a_wipe_leaves_no_verdict_for_a_recreated_name(tmp_path, monkeypatch):
    """The wipe frees every name at once, so it is the delete case multiplied.
    Its 'this cannot be undone' has to be true of the markers too."""
    pm, bl = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")
    pm.add_profile("beta", "", "windows")
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))
    _refuse(bl, monkeypatch, "beta", ProxyUnresolvedError("unresolved"))

    assert pm.wipe_all_profiles() == 2
    for name in ("acme", "beta"):
        assert bl.last_refusal(name) is None, f"{name}'s verdict survived a wipe"


def test_a_rename_does_not_orphan_the_verdict_under_the_freed_name(
    tmp_path, monkeypatch
):
    """A rename re-keys `self.profiles` and used to leave the launcher holding a
    verdict under the ORIGINAL name: invisible to the operator (the card now
    looks under the new name) while sitting on a key another profile can take.

    Dropped rather than moved to the new key on purpose — `detail` is the
    settled sentence composed in process.py with the profile NAMED inside it, so
    re-keying would render a chip whose full text names a profile that no longer
    exists. "No verdict yet" is a state the card renders honestly.
    """
    pm, bl = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))

    assert pm.update_profile("acme", "acme-eu") is True
    assert bl.last_refusal("acme") is None, (
        "the verdict was orphaned under the freed name, where a future profile "
        "taking that name would inherit it"
    )

    # And the freed name is safe to re-take.
    pm.add_profile("acme", "", "windows")
    card = _card(pm.profiles["acme"], refusal=bl.last_refusal("acme"))
    assert not _reports_refusal(card)


def test_an_overwriting_import_does_not_pass_the_verdict_to_the_new_record(
    tmp_path, monkeypatch
):
    """An overwrite REPLACES the record holding the name — the same
    identity-is-gone event as a delete, reached by a different door."""
    pm, bl = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")
    _refuse(bl, monkeypatch, "acme", GeographyDisprovenError("check failed"))

    # export_profile takes a DIRECTORY and mints the archive name itself.
    outdir = tmp_path / "exports"
    outdir.mkdir()
    ok, archive = pm.export_profile("acme", str(outdir), include_data=False)
    assert ok, f"precondition: the export succeeded ({archive})"

    ok, _ = pm.import_profile(archive, overwrite=True)
    assert ok, "precondition: the overwriting import succeeded"
    assert bl.last_refusal("acme") is None, (
        "the replaced profile's verdict was inherited by the imported record"
    )


def test_the_marker_still_survives_a_teardown_of_a_LIVE_profile(
    tmp_path, monkeypatch
):
    """The counterweight to the four tests above, and the reason the disposal is
    a separate hook instead of a line in `_forget_session_facts`.

    Stopping a browser must NOT clear the verdict — that is the survival
    property the whole ticket is about. Only destroying the identity may. A fix
    that made the tests above pass by folding the drop into the session teardown
    would break this one.
    """
    pm, bl = _manager(tmp_path, monkeypatch)
    pm.add_profile("acme", "", "windows")
    _refuse(bl, monkeypatch, "acme", GeographyDisprovenError("check failed"))

    bl.stop_profile("acme")
    bl.shutdown_all()
    assert bl.last_refusal("acme") is not None, (
        "a session teardown cleared the verdict — the disposal was wired to the "
        "wrong event"
    )


def test_falsification_no_disposal_hands_the_verdict_to_the_replacement(
    tmp_path, monkeypatch
):
    """Simulates the pre-fix wiring (delete stops the browser but never tells the
    launcher the identity is gone) and asserts the defect is REAL — so the tests
    above are pinning a mechanism rather than a tautology.
    """
    pm, bl = _manager(tmp_path, monkeypatch)
    # Un-wire only the identity hook; the stop hook stays, exactly as before.
    pm.set_forget_identity_hook(None)
    pm.add_profile("acme", "", "windows")
    _refuse(bl, monkeypatch, "acme", GeographyUnknownError("no geography"))
    pm.delete_profile("acme")
    pm.add_profile("acme", "", "windows")

    assert bl.last_refusal("acme") is not None
    card = _card(pm.profiles["acme"], refusal=bl.last_refusal("acme"))
    assert _reports_refusal(card), (
        "the un-wired build must show the false chip, or these tests prove "
        "nothing"
    )
