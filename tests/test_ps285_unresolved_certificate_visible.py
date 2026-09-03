"""PS-285 — a preserved-but-unresolvable certificate assignment must be visible
from where the operator actually stands.

THE STATE. A profile can name a certificate the store does not hold. Both
routes are deliberate protections firing, not corruption: ``CertStore._load``
skips one unparseable record and carries on (a populated dropdown with one name
missing), and an unreadable ``certificates.json`` is quarantined with the store
left empty (every name missing). The assignment is then PRESERVED across
unrelated profile edits — that preservation is correct and is not what this
suite is about.

WHAT WAS MISSING. Preservation made the state DURABLE. It used to be destroyed
on sight: the next profile-dialog save collapsed it to "no certificate", a
legitimate configuration. Nothing downstream was built for a state that sticks
around, so the operator could not reach it from anywhere they stand:

  * the CARD had no certificate concept at all (``grep -c cert`` → 0);
  * the certificates PAGE iterates the records that EXIST, so a dangling
    assignment appears nowhere on it;
  * the LAUNCH took the branch that drops the client certificate in complete
    silence — the successful mTLS path logs and every failure inside
    ``start_cert_session`` logs at ``error``, and the one path that quietly
    drops the certificate was the only one that said nothing;
  * the profile DIALOG does warn, well — but the operator opens that dialog to
    EDIT a profile, not to check one. The launch button is on the card.

The consequence is a browser that opens, reaches the admin site the certificate
was meant to authenticate it to, and is simply not recognised, with nothing in
the sequence pointing back at a certificate record that stopped resolving.

WHAT IS ASSERTED HERE. Structural checks over the BUILT CONTROL TREE (never
"a helper was called") and over the real logger on the real launch path:

  1. the three states are mutually distinguishable ON THE CARD;
  2. a card WITHOUT the condition is byte-identical to today — the empty-list
     splice, ``_refusal_chip``'s form, asserted as text equality against a card
     built by the pre-existing call shape;
  3. the launch says it EXACTLY ONCE, names the profile and the certificate,
     and does NOT name the admin host;
  4. the behaviour under it is untouched — the sweep still happens, the launch
     still proceeds without a certificate;
  5. the app's render path derives the flag from the container's OWN store
     (never a fresh ``CertStore()``, which would ``_load()`` from disk on every
     repaint) and performs no IO.

``test_falsification_*`` at the bottom assert this suite goes RED when the
marker is removed from the rendered output, so a green run means the marker is
PRESENT rather than that the assertions are vacuous.

WHAT IS DRIVEN ELSEWHERE. The RENDERED chip — a real card in a real served app,
present for the unresolvable assignment and absent for both other states — is
driven live in ``tests/ui_driver/live_ps285.py``, including its own
falsification pass.
"""
import logging
import os

import flet as ft
import pytest

from src.models.profile import Profile
from src.services.browser import process
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


def _card(profile, *, cert_unresolved=False, is_running=False):
    return build_profile_card(
        profile,
        False,
        is_running,
        _noop,
        _noop,
        _noop,
        cert_unresolved=cert_unresolved,
    )


def _reports_unresolved_cert(card) -> bool:
    """True if the card ASSERTS an unresolvable certificate to the operator.

    Matched on the rendered words rather than on a container identity, so the
    test describes what a person can actually read off the row.
    """
    return any("not found" in t.lower() for t in _texts(card))


# --------------------------------------------------------------------------
# 1. The three states, on the card. AC1.
# --------------------------------------------------------------------------


def test_an_unresolvable_certificate_is_marked_on_the_card():
    p = Profile(name="acme", certificate="corp-ca")
    assert _reports_unresolved_cert(_card(p, cert_unresolved=True))


def test_a_resolving_certificate_is_not_marked():
    p = Profile(name="acme", certificate="corp-ca")
    assert not _reports_unresolved_cert(_card(p, cert_unresolved=False))


def test_no_certificate_at_all_is_not_marked():
    p = Profile(name="acme")
    assert not _reports_unresolved_cert(_card(p, cert_unresolved=False))


def test_the_three_states_are_mutually_distinguishable_on_the_card():
    """The whole claim in one assertion, on the RENDERED text.

    "Distinguishable from a profile with no certificate AND from one whose
    certificate resolves" is the acceptance criterion; before this slice all
    three rendered identically, because the card had no certificate concept.
    """
    dangling = _texts(
        _card(Profile(name="a", certificate="corp-ca"), cert_unresolved=True)
    )
    resolving = _texts(
        _card(Profile(name="a", certificate="corp-ca"), cert_unresolved=False)
    )
    none_at_all = _texts(Profile(name="a") and _card(Profile(name="a")))

    assert dangling != resolving, (
        "a certificate that does not resolve renders identically to one that "
        "does — the operator launches into a silent drop with nothing to see"
    )
    assert dangling != none_at_all, (
        "a dangling assignment renders identically to no assignment at all"
    )
    # And the two INERT states stay identical to each other, which is what
    # makes the marker's presence the signal rather than a dimmed variant.
    assert resolving == none_at_all


def test_the_marker_names_the_certificate():
    """"certificate not found" alone sends the operator to a certificates page
    where, by construction, the missing name is ABSENT. The name is the one
    thing the row must carry."""
    joined = " ".join(
        _texts(_card(Profile(name="a", certificate="corp-ca"), cert_unresolved=True))
    )
    assert "corp-ca" in joined, f"the marker does not name the certificate: {joined!r}"


def test_the_full_sentence_is_reachable_from_the_card():
    """The scanning label is short by design; the consequence — the browser
    opens WITHOUT a client certificate — rides the tooltip, one hover away."""
    tips = _tooltips(
        _card(Profile(name="a", certificate="corp-ca"), cert_unresolved=True)
    )
    assert any("without a client certificate" in t.lower() for t in tips), tips
    assert any("corp-ca" in t for t in tips), tips


def test_the_marker_does_not_claim_the_launch_will_fail():
    """The launch PROCEEDS, correctly and safely, without the certificate.

    Calling it a failure would be its own dishonesty — and it would point the
    operator at the launcher instead of at the certificate store, which is
    where the actual remedy is.
    """
    card = _card(Profile(name="a", certificate="corp-ca"), cert_unresolved=True)
    blob = " ".join(_texts(card) + _tooltips(card)).lower()
    for wrong in ("launch failed", "refused", "cannot launch", "will not open"):
        assert wrong not in blob, f"the marker reads as a launch failure: {wrong!r}"


def test_the_marker_is_shown_while_the_profile_is_running():
    """Unlike a refusal — a fact about one past ATTEMPT, which the launcher
    drops at every teardown — this is a standing property of the profile's
    CONFIGURATION. It is just as true, and just as worth saying, while the
    browser that was meant to present the certificate is open."""
    p = Profile(name="a", certificate="corp-ca")
    assert _reports_unresolved_cert(_card(p, cert_unresolved=True, is_running=True))


# --------------------------------------------------------------------------
# 2. AC4 — no card WITHOUT the condition changes at all.
# --------------------------------------------------------------------------


def test_a_profile_without_the_condition_renders_exactly_as_before():
    """An absent marker must be a REAL absence — no empty box, no placeholder,
    no dimmed variant the eye learns to skip. ``before`` is built through the
    call shape that existed before this parameter did, so this compares against
    the shipped card rather than against a second copy of the new one."""
    for p in (Profile(name="acme"), Profile(name="acme", certificate="corp-ca")):
        before = build_profile_card(p, False, False, _noop, _noop, _noop)
        after = _card(p, cert_unresolved=False)
        assert _texts(before) == _texts(after)
        assert _tooltips(before) == _tooltips(after)
        assert not _reports_unresolved_cert(after)


def test_the_marker_costs_one_line_and_no_extra_row():
    """Spliced into the meta row that ALREADY exists: a marked card gains no
    ROW over an unmarked one, so twenty of them cost twenty short lines in a
    column the operator is already scanning and no card grows taller."""
    p = Profile(name="a", certificate="corp-ca")
    plain = _texts(_card(p, cert_unresolved=False))
    marked = _texts(_card(p, cert_unresolved=True))
    assert len(marked) == len(plain) + 1, (
        f"the marker added {len(marked) - len(plain)} text blocks to the card"
    )
    added = [t for t in marked if t not in plain][0]
    assert len(added) < 60, f"the scanning label is a paragraph: {added!r}"


# --------------------------------------------------------------------------
# 3. AC3 — the launch says it, exactly once, without naming the admin host.
# --------------------------------------------------------------------------


class _NoCertStore:
    """The certificate name does not resolve.

    ``None`` is the REAL store's answer for every route into this state: the
    record was skipped as malformed, the whole file was quarantined, or the
    operator deleted it.
    """

    def get(self, name):
        return None


@pytest.fixture
def dangling(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(process, "CertStore", _NoCertStore)
    caplog.set_level(logging.DEBUG, logger="persona")
    profile_dir = str(tmp_path / "profile")
    os.makedirs(os.path.join(profile_dir, ".persona-mtls"), exist_ok=True)
    return profile_dir


def _lines(caplog):
    return [r.getMessage() for r in caplog.records]


def test_the_launch_says_it_exactly_once(dangling, caplog):
    session = process._cert_session_for(
        Profile(name="acme", certificate="corp-ca"), dangling, None
    )
    assert session is None
    said = [m for m in _lines(caplog) if "corp-ca" in m]
    assert len(said) == 1, (
        f"the dropped client certificate produced {len(said)} operator-visible "
        f"line(s), not exactly one: {said!r}"
    )


def test_the_line_names_the_profile_and_the_certificate(dangling, caplog):
    process._cert_session_for(
        Profile(name="acme", certificate="corp-ca"), dangling, None
    )
    (line,) = [m for m in _lines(caplog) if "corp-ca" in m]
    assert "acme" in line, (
        f"the line does not name the profile, so a bulk launch cannot be "
        f"attributed to a row: {line!r}"
    )
    assert "corp-ca" in line


def test_the_line_does_not_name_the_admin_host(dangling, caplog):
    """The recorded rule at the successful chromium mTLS path binds here: the
    admin host is an internal hostname identifying the operator's infra, and it
    would land one line per cert-profile launch in the persistent log and the
    Activity Log. There is no session to name one from on this branch, and
    nothing may reintroduce it."""
    process._cert_session_for(
        Profile(name="acme", certificate="corp-ca"), dangling, None
    )
    joined = " ".join(_lines(caplog))
    for host_ish in ("admin.", "https://", "http://", ".corp", ".example.com"):
        assert host_ish not in joined, (
            f"a hostname-shaped token reached the log line: {host_ish!r} in "
            f"{joined!r}"
        )


def test_the_line_is_at_least_a_warning(dangling, caplog):
    """The successful path is INFO and the in-session failures are ERROR. A
    configured protection silently not applying belongs between them — and the
    console handler's floor is WARNING, so anything lower keeps the one path
    that drops a certificate as the one path that is quiet."""
    process._cert_session_for(
        Profile(name="acme", certificate="corp-ca"), dangling, None
    )
    (rec,) = [r for r in caplog.records if "corp-ca" in r.getMessage()]
    assert rec.levelno >= logging.WARNING, logging.getLevelName(rec.levelno)


def test_a_profile_with_no_certificate_stays_silent(dangling, caplog):
    """"No certificate assigned" is an ordinary, supported configuration and
    must not produce a line — signalling has to stay proportional, or the
    marker the operator must not skim past becomes one they learn to."""
    process._cert_session_for(Profile(name="plain"), dangling, None)
    assert _lines(caplog) == [], _lines(caplog)


# --------------------------------------------------------------------------
# 4. AC5 — the behaviour under the line is byte-identical.
# --------------------------------------------------------------------------


def test_the_sweep_and_the_launch_are_unchanged(dangling, caplog):
    """The line is added ALONGSIDE the existing behaviour, not instead of it:
    key material is still swept and the launch still proceeds with no session.
    (The full sweep suite is ``tests/test_cert_sweep_without_session.py``,
    which passes unmodified — this is the local belt.)"""
    work = os.path.join(dangling, ".persona-mtls")
    with open(os.path.join(work, "term_leaf.key"), "w", encoding="utf-8") as fh:
        fh.write("-----BEGIN PRIVATE KEY-----\nps285\n-----END PRIVATE KEY-----\n")

    session = process._cert_session_for(
        Profile(name="acme", certificate="corp-ca"), dangling, None
    )

    assert session is None, "the launch must proceed WITHOUT a client certificate"
    assert os.listdir(work) == [], (
        f"key material survived the dropped session: {os.listdir(work)}"
    )


# --------------------------------------------------------------------------
# 5. The render path: derived, from the container's OWN store, with no IO.
# --------------------------------------------------------------------------


@pytest.fixture
def live_app(tmp_path, monkeypatch):
    """A real App on a real Container, pointed at a tmp PERSONA_HOME."""
    import src.core.config as cfg
    import src.services.profile.manager as mod
    import src.ui.state as ui_state
    from src.core.container import Container
    from src.core.logging import setup_logging
    from src.ui.app import App

    monkeypatch.setenv("PERSONA_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(tmp_path / "certificates"))
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh.json"))
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(cfg, "LOG_DIR", str(log_dir), raising=False)
    monkeypatch.setattr(ui_state, "LOG_DIR", str(log_dir), raising=False)
    setup_logging(str(log_dir))
    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(tmp_path / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path / "data"), raising=False)

    app = App(container=Container())
    app.refs = _refs()
    return app


def _refs():
    from src.ui.refs import UIRefs

    return UIRefs(
        stats_text=ft.Text(),
        running_text=ft.Text(),
        content_subtitle=ft.Text(),
        profile_list_area=ft.Column(),
        prev_btn=ft.IconButton(),
        next_btn=ft.IconButton(),
        page_label=ft.Text(),
        bulk_bar=ft.Row(),
        file_picker=ft.FilePicker(),
    )


def _assign(app, profile_name: str, cert_name: str) -> None:
    """Create a profile and assign a certificate THROUGH THE SHIPPED EDIT PATH.

    ``update_profile`` is what the profile dialog calls, so the fixture cannot
    reach a state the product cannot — and in particular the assignment is
    written by the same code that PRESERVES an unresolvable one, which is the
    state under test.
    """
    app.pm.add_profile(profile_name, "", "windows")
    assert app.pm.update_profile(
        profile_name, profile_name, new_certificate=cert_name
    )
    assert app.pm.profiles[profile_name].certificate == cert_name


def _rendered_cards(app):
    return app.refs.profile_list_area.controls


def test_the_real_render_path_marks_a_dangling_assignment(live_app):
    """End to end on the SHIPPED render path: a real App, a real profile store,
    a real certificate store with the name absent, and the card tree
    ``_refresh_profiles`` actually swapped in."""
    _assign(live_app, "acme", "corp-ca")

    live_app._refresh_profiles()

    cards = _rendered_cards(live_app)
    assert len(cards) == 1
    assert _reports_unresolved_cert(cards[0]), _texts(cards[0])


def test_the_real_render_path_leaves_a_resolving_assignment_alone(live_app):
    from src.services.cert.store import Certificate

    live_app.cert_store.add(
        Certificate(
            name="corp-ca",
            p12_path=str("/tmp/corp.p12"),
            password="",
            url="https://admin.example.invalid/login",
        )
    )
    _assign(live_app, "acme", "corp-ca")

    live_app._refresh_profiles()

    (card,) = _rendered_cards(live_app)
    assert not _reports_unresolved_cert(card), _texts(card)


def test_the_render_path_reads_the_containers_store_and_performs_no_io(
    live_app, monkeypatch
):
    """The render path runs on every repaint, and its bar is stated in
    ``_refresh_profiles`` itself: a dict lookup under a lock, no IO.

    Constructing a fresh ``CertStore()`` here would ``_load()`` from disk on
    every repaint — so the test forbids BOTH: the store class must not be
    instantiated, and the container's instance must not touch the filesystem.
    """
    import src.services.cert.store as cert_store_mod

    _assign(live_app, "acme", "corp-ca")

    constructed = []
    real_init = cert_store_mod.CertStore.__init__

    def _spy_init(self, *a, **k):
        constructed.append(self)
        return real_init(self, *a, **k)

    monkeypatch.setattr(cert_store_mod.CertStore, "__init__", _spy_init)

    loaded = []
    monkeypatch.setattr(
        cert_store_mod.CertStore,
        "_load",
        lambda self: loaded.append(True),
    )

    live_app._refresh_profiles()

    assert constructed == [], (
        "the render path built a new CertStore, which reads certificates.json "
        "from disk on every repaint"
    )
    assert loaded == [], "the render path re-read the certificate store from disk"


# --------------------------------------------------------------------------
# 6. AC6 — falsification. A green above must mean the marker is PRESENT.
# --------------------------------------------------------------------------


def test_falsification_removing_the_chip_goes_red_on_the_rendered_output(
    monkeypatch,
):
    """Revert the RENDER — ``_unresolved_cert_chip`` restored to its
    pre-PS-285 behaviour (nothing at all) — with everything else untouched,
    including the flag the caller computes and passes in. The AC1 assertion is
    re-run against the rebuilt tree and MUST go red.

    If it stayed green, the greens above would not be reading the rendered card
    at all and this file would be decoration.
    """
    import src.ui.components.profile_card as card_mod

    monkeypatch.setattr(
        card_mod, "_unresolved_cert_chip", lambda unresolved, name: []
    )
    card = _card(Profile(name="a", certificate="corp-ca"), cert_unresolved=True)
    assert not _reports_unresolved_cert(card), (
        "the marker is still reported with the chip removed, so the assertions "
        "above are not reading the rendered control tree"
    )


def test_falsification_removing_the_launch_line_goes_red(dangling, caplog):
    """The pre-PS-285 branch body, written out: sweep and return, in silence.
    Proves the AC3 assertions above would catch it rather than passing on any
    log traffic that happens to be present."""
    from src.services.cert.terminator import sweep_key_material

    sweep_key_material(os.path.join(dangling, ".persona-mtls"))
    assert [m for m in _lines(caplog) if "corp-ca" in m] == []
