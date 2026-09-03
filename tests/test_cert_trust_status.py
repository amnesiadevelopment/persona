"""The Firefox mTLS CA import SOFT-FAILS: the launch proceeds with the assigned
certificate untrusted, and before this the outcome was announced once on stdout
and consumed by nobody. These tests pin the outcome to something that OUTLIVES
the session — the profile record — so a profile launched untrusted is no longer
indistinguishable from one whose trust imported cleanly.

Red-first: every assertion below fails on main today (`cert_trust_status` did
not exist, and no consumer of the MTLS_* messages existed).
"""
import io
import json
import socket
import threading

import pytest

import src.services.browser.launcher as launcher_mod
from src.models.profile import Profile
from src.services.browser.launcher import (
    BrowserLauncher,
    cert_trust_status_from,
)
from src.services.profile.manager import ProfileManager

# The engine's real emit strings, copied verbatim from invisible_launch.py
# (:2305 / :2312 / :2316). AC6 freezes these: if a change to the engine makes a
# literal below stop matching, the capture has silently broken and these tests
# are the alarm — do not "fix" them by loosening the match.
MSG_TRUSTED = "MTLS_CA_TRUSTED"
MSG_UNSUPPORTED = (
    "MTLS_UNSUPPORTED: Firefox certificates aren't available on this "
    "OS yet (use the Chromium engine for this profile)"
)
MSG_FAILED = "MTLS_CA_IMPORT_FAILED: opening without certificate trust"


# --------------------------------------------------------------------------
# The emit strings this feature binds to still exist in the engine, unchanged.
# --------------------------------------------------------------------------

def test_engine_still_emits_the_three_messages_this_feature_binds_to():
    # This capture is a string contract across a process boundary (the engine
    # writes to stdout, the launcher parses). Assert the literals are still
    # present at the source, so renaming one there fails HERE loudly instead of
    # silently reverting the profile to "no outcome recorded, ever".
    import pathlib

    src_text = pathlib.Path(
        launcher_mod.__file__
    ).parent.joinpath("invisible_launch.py").read_text(encoding="utf-8")
    assert 'emit("MTLS_CA_TRUSTED")' in src_text
    assert "MTLS_UNSUPPORTED: Firefox certificates aren't available on this " in src_text
    assert 'emit("MTLS_CA_IMPORT_FAILED: opening without certificate trust")' in src_text


# --------------------------------------------------------------------------
# The message -> status mapping.
# --------------------------------------------------------------------------

def test_trusted_message_maps_to_a_trusted_status():
    assert cert_trust_status_from(MSG_TRUSTED) == "trusted"


def test_import_failed_message_maps_to_an_untrusted_status():
    status = cert_trust_status_from(MSG_FAILED)
    assert status is not None
    # The operator must be able to tell this apart from success by reading it.
    assert not status.startswith("trusted")
    assert "opening without certificate trust" in status


def test_unsupported_message_maps_to_an_untrusted_status():
    status = cert_trust_status_from(MSG_UNSUPPORTED)
    assert status is not None
    assert not status.startswith("trusted")


def test_unrelated_engine_lines_map_to_nothing():
    # None is what lets the caller tell "not an mTLS line" from a real outcome;
    # a non-None here would overwrite a real status with noise on every line.
    for msg in (
        "BROWSER_STARTED",
        "BROWSER_CLOSED",
        "LAUNCH_FAILED: boom",
        "LAUNCH_CANCELLED",
        "LIFECYCLE close=stop-requested",
        "console.error: whatever",
        "",
    ):
        assert cert_trust_status_from(msg) is None, msg


# --------------------------------------------------------------------------
# Model + persistence boundary.
# --------------------------------------------------------------------------

def test_profile_defaults_cert_trust_status_none():
    assert Profile(name="a").cert_trust_status is None


def test_profile_to_dict_roundtrips_cert_trust_status():
    p = Profile(name="a", cert_trust_status="trusted")
    d = p.to_dict()
    assert d["cert_trust_status"] == "trusted"
    assert Profile(**d).cert_trust_status == "trusted"


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    pf = tmp_path / "profiles.json"
    dd = tmp_path / "data"
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(pf))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(dd))
    import src.core.config as cfg
    import src.services.profile.manager as mod

    monkeypatch.setattr(cfg, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(cfg, "DATA_DIR", str(dd))
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(mod, "DATA_DIR", str(dd))
    return ProfileManager()


def test_set_cert_trust_status_persists_to_disk(mgr, tmp_path):
    mgr.add_profile("p1", "", "windows")
    assert mgr.set_cert_trust_status("p1", "NOT TRUSTED: x") is True
    assert mgr.profiles["p1"].cert_trust_status == "NOT TRUSTED: x"

    raw = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert raw["p1"]["cert_trust_status"] == "NOT TRUSTED: x"


def test_set_cert_trust_status_unknown_profile_returns_false(mgr):
    assert mgr.set_cert_trust_status("ghost", "x") is False


@pytest.mark.parametrize("status", ["trusted", "NOT TRUSTED: opening without trust"])
def test_cert_trust_status_survives_reload(mgr, status):
    """AC3 — the round trip across the persistence boundary, which an
    in-memory assertion cannot see. The load path used to be a HAND-ENUMERATED
    allow-list where a field absent from the list was silently dropped on
    reload even though to_dict() had saved it (transfer.py:117 records this as
    a repeat offence); PS-269 derived those keys from
    dataclasses.fields(Profile), so this field now round-trips because it is a
    dataclass field rather than because someone remembered to list it. The
    boundary still needs testing: the derived build keeps two explicit
    migration post-steps, and a reload is the only place a regression in
    either of them — or in the derivation itself — becomes visible."""
    mgr.add_profile("p1", "", "windows")
    mgr.set_cert_trust_status("p1", status)

    import src.services.profile.manager as mod

    fresh = mod.ProfileManager()
    assert fresh.profiles["p1"].cert_trust_status == status


# --------------------------------------------------------------------------
# End-to-end through the launcher: an engine that says the trust FAILED must
# leave that fact on the profile. This is the mechanism, not the call shape.
# --------------------------------------------------------------------------

class _Stdout:
    def __init__(self, lines, eof):
        self._io = io.StringIO("".join(line + "\n" for line in lines))
        self._eof = eof

    def readline(self, *a):
        line = self._io.readline()
        if not line:
            self._eof.set()
        return line

    def close(self):
        pass


class _Proc:
    def __init__(self, lines, returncode=0):
        self._exited = threading.Event()
        self.stdout = _Stdout(lines, self._exited)
        self.returncode = returncode

    def wait(self, timeout=None):
        self._exited.wait(timeout)
        return self.returncode

    def poll(self):
        return self.returncode if self._exited.is_set() else None

    def terminate(self):
        self._exited.set()

    def kill(self):
        self._exited.set()


def _launch_and_collect(monkeypatch, lines, pm, profile_name="fox", expect_status=True):
    """Run one full session through the REAL start_thread/_monitor_process and
    return once the engine's output has been fully consumed. Nothing about the
    callback's name or position is asserted — only that launching an engine
    which emits these lines ends with the outcome recorded on the manager.

    NOTE on synchronisation: launch_or_stop wires BOTH on_ready and on_stop to
    set_loading(name, False), and on_ready fires at monitor START (before any
    line is read) — so an on_stop-style event is NOT a safe signal that the
    output was consumed. Wait on the process' own exit, then settle.
    """
    import time

    from src.ui.actions.browser import launch_or_stop

    proc = _Proc(lines)
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: proc)
    bl = BrowserLauncher()

    class _State:
        def is_loading(self, name):
            return False

        def set_loading(self, name, value):
            pass

        def schedule_refresh(self):
            pass

    # Route through the real UI action so the wiring itself is under test.
    monkeypatch.setattr(
        "src.services.browser.process.effective_engine", lambda p: "chromium"
    )
    monkeypatch.setattr(
        "src.services.engine.updater.is_installed", lambda: True
    )
    launch_or_stop(profile_name, pm, bl, _State(), lambda m: None)

    assert proc._exited.wait(5), "engine output was never fully consumed"
    deadline = time.time() + 5
    while time.time() < deadline:
        recorded = pm.profiles[profile_name].cert_trust_status is not None
        if recorded == expect_status:
            break
        time.sleep(0.01)
    # Give the monitor thread a moment to finish its own teardown either way.
    time.sleep(0.05)
    return proc


def test_failed_ca_import_is_recorded_on_the_profile(monkeypatch, mgr):
    """AC1 (failure half) + AC2. On main this fails: nothing consumed the
    message and the field did not exist."""
    mgr.add_profile("fox", "", "windows")
    mgr.profiles["fox"].certificate = "admin-cert"
    mgr.profiles["fox"].engine = "firefox"

    _launch_and_collect(monkeypatch, [MSG_FAILED, "BROWSER_CLOSED"], mgr)

    status = mgr.profiles["fox"].cert_trust_status
    assert status is not None, "a soft-failed CA import left no trace"
    assert not status.startswith("trusted")
    # and it OUTLIVES the session
    import src.services.profile.manager as mod

    assert mod.ProfileManager().profiles["fox"].cert_trust_status == status


def test_successful_ca_import_is_recorded_on_the_profile(monkeypatch, mgr):
    """AC1 (success half) — assert BOTH outcomes, so the test cannot pass by
    writing a constant."""
    mgr.add_profile("fox", "", "windows")
    mgr.profiles["fox"].certificate = "admin-cert"
    mgr.profiles["fox"].engine = "firefox"

    _launch_and_collect(monkeypatch, [MSG_TRUSTED, "BROWSER_CLOSED"], mgr)

    assert mgr.profiles["fox"].cert_trust_status == "trusted"


def test_the_two_outcomes_are_distinguishable(monkeypatch, mgr):
    """The whole point: a trusted launch and a soft-failed one must not leave
    the profile in the same state. Guards against a persistence call that
    writes the same value on every path."""
    mgr.add_profile("ok", "", "windows")
    mgr.add_profile("bad", "", "windows")
    for n in ("ok", "bad"):
        mgr.profiles[n].certificate = "admin-cert"
        mgr.profiles[n].engine = "firefox"

    _launch_and_collect(monkeypatch, [MSG_TRUSTED, "BROWSER_CLOSED"], mgr, "ok")
    _launch_and_collect(monkeypatch, [MSG_FAILED, "BROWSER_CLOSED"], mgr, "bad")

    assert (
        mgr.profiles["ok"].cert_trust_status
        != mgr.profiles["bad"].cert_trust_status
    )


def test_profile_without_certificate_is_untouched(monkeypatch, mgr):
    """AC5 — a profile with no certificate assigned is byte-identical to today.
    The engine emits no MTLS_* line for it, so no field is written."""
    mgr.add_profile("plain", "", "windows")
    before = json.dumps(mgr.profiles["plain"].to_dict(), sort_keys=True)

    _launch_and_collect(
        monkeypatch, ["BROWSER_STARTED", "BROWSER_CLOSED"], mgr, "plain"
    )

    assert mgr.profiles["plain"].cert_trust_status is None
    after = json.dumps(mgr.profiles["plain"].to_dict(), sort_keys=True)
    assert after == before


def test_existing_log_lines_still_reach_the_activity_log(monkeypatch, mgr):
    """The capture must not swallow the message or disturb the other four
    special-cased shapes — the Activity Log reads as it did before."""
    proc = _Proc(["BROWSER_STARTED", MSG_FAILED, "BROWSER_CLOSED"])
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: proc)
    bl = BrowserLauncher()
    messages: list[str] = []
    stopped = threading.Event()
    bl.start_thread(
        Profile(name="fox", engine="firefox", certificate="c"),
        messages.append,
        on_stop=stopped.set,
    )
    assert stopped.wait(5)
    assert any("Browser started!" == m for m in messages)
    assert any(MSG_FAILED in m for m in messages), (
        "the mTLS line stopped reaching the log"
    )


def test_start_thread_is_source_compatible_for_existing_callers():
    """The new callback is KEYWORD-ONLY, so the existing third POSITIONAL
    argument at ui/actions/browser.py (and the api/mcp call sites) cannot shift
    meaning."""
    import inspect

    sig = inspect.signature(BrowserLauncher.start_thread)
    p = sig.parameters["on_cert_trust"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is None
    # positional order of the pre-existing params is unchanged
    positional = [
        n for n, q in sig.parameters.items()
        if q.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    assert positional == [
        "self", "profile", "log_callback", "on_start", "on_ready", "on_stop",
    ]


def test_protocol_matches_the_concrete_launcher():
    """ui/actions/browser.py types bl as IBrowserLauncher, so a signature added
    only to the concrete class is a type mismatch."""
    import inspect

    from src.interfaces.protocols import IBrowserLauncher, IProfileManager

    assert (
        inspect.signature(IBrowserLauncher.start_thread).parameters.keys()
        == inspect.signature(BrowserLauncher.start_thread).parameters.keys()
    )
    # pm is typed IProfileManager and the wiring calls this on it
    assert hasattr(IProfileManager, "set_cert_trust_status")


# --------------------------------------------------------------------------
# AC7 — the surface is render-only.
# --------------------------------------------------------------------------

def test_building_the_dialog_opens_no_socket_and_starts_no_engine(monkeypatch):
    """Mirrors PS-15's AC6. The status line reports a LAST-KNOWN outcome; a
    surface that re-probed on draw would make the check's cadence attributable
    to a human opening a window."""
    import flet as ft

    from src.ui.dialogs.profile import open_profile_dialog

    opened: list = []
    real_socket = socket.socket

    class _Spy(real_socket):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            opened.append(a)
            super().__init__(*a, **k)

    spawned: list = []
    monkeypatch.setattr(
        launcher_mod, "spawn_browser", lambda p: spawned.append(p)
    )

    class _FakePage:
        def __init__(self):
            self.shown = None

        def show_dialog(self, dlg):
            self.shown = dlg

        def pop_dialog(self):
            self.shown = None

        def update(self):
            pass

    page = _FakePage()
    profile = Profile(
        name="fox",
        engine="firefox",
        certificate="admin-cert",
        cert_trust_status="NOT TRUSTED: opening without certificate trust",
    )

    socket.socket = _Spy  # type: ignore[misc]
    try:
        open_profile_dialog(
            page,
            object(),  # proxy_service: only types the param; dialog builds from lists
            on_save=lambda *a, **k: None,
            profile=profile,
            proxy_names=[],
            cert_names=["admin-cert"],
        )
    finally:
        socket.socket = real_socket  # type: ignore[misc]

    assert opened == [], f"building the dialog opened a socket: {opened}"
    assert spawned == [], "building the dialog started an engine"
    assert page.shown is not None


def _texts(control) -> list[str]:
    out: list[str] = []

    def walk(c):
        v = getattr(c, "value", None)
        if isinstance(v, str):
            out.append(v)
        for attr in ("title", "content", "controls", "actions"):
            child = getattr(c, attr, None)
            if child is None:
                continue
            items = child if isinstance(child, list) else [child]
            for x in items:
                if x is not None and hasattr(x, "__dict__"):
                    walk(x)

    walk(control)
    return out


def _dialog_texts(profile, cert_names=("admin-cert",)):
    from src.ui.dialogs.profile import open_profile_dialog

    class _FakePage:
        def __init__(self):
            self.shown = None

        def show_dialog(self, dlg):
            self.shown = dlg

        def pop_dialog(self):
            self.shown = None

        def update(self):
            pass

    page = _FakePage()
    open_profile_dialog(
        page,
        object(),  # proxy_service: only types the param; dialog builds from lists
        on_save=lambda *a, **k: None,
        profile=profile,
        proxy_names=[],
        cert_names=list(cert_names),
    )
    return _texts(page.shown)


def test_untrusted_outcome_is_surfaced_in_the_dialog():
    """The defect was that the dialog showed the certificate selected and said
    nothing about it being untrusted."""
    texts = _dialog_texts(
        Profile(
            name="fox",
            engine="firefox",
            certificate="admin-cert",
            cert_trust_status="NOT TRUSTED: opening without certificate trust",
        )
    )
    assert any("NOT TRUSTED" in t for t in texts), (
        "the dialog renders the certificate as selected but never says the "
        "trust failed"
    )


def test_profile_with_no_recorded_outcome_renders_no_status_line():
    """AC5 on the surface — a profile that never recorded an outcome shows no
    new text at all."""
    texts = _dialog_texts(Profile(name="plain"))
    assert not any("last launch:" in t for t in texts)


# --------------------------------------------------------------------------
# The recorded outcome describes ONE certificate's CA. When the assigned
# certificate changes, the verdict stops applying to what is on the profile.
#
# Left stale it does not merely go quiet — it makes an AFFIRMATIVE claim. A
# profile whose cert A imported cleanly ("trusted"), then swapped to cert B and
# never launched, renders "last launch: trusted" in the MUTED colour: a clean
# bill of health for a CA whose trust was never attempted. That is the same
# defect this ticket exists to close (a stored field rendered as a confident
# state with no provenance), pointed the other way — the original bug hid a
# failure, this one manufactures a reassurance.
# --------------------------------------------------------------------------

def test_swapping_the_certificate_clears_the_recorded_outcome(mgr):
    """The verdict describes the OLD certificate's CA — it must not survive
    onto a different one."""
    mgr.add_profile("fox", "", "windows", engine="firefox", certificate="cert-a")
    mgr.set_cert_trust_status("fox", "trusted")

    assert mgr.update_profile("fox", "fox", "", "windows", new_certificate="cert-b") is True

    assert mgr.profiles["fox"].certificate == "cert-b"
    assert mgr.profiles["fox"].cert_trust_status is None, (
        "the outcome recorded for cert-a is still on the profile after it was "
        "reassigned to cert-b"
    )


def test_removing_the_certificate_clears_the_recorded_outcome(mgr):
    """AC5 on the write path — a profile with NO certificate assigned carries
    no outcome. The dialog maps the 'no certificate' choice to '', which
    update_profile turns into None."""
    mgr.add_profile("fox", "", "windows", engine="firefox", certificate="cert-a")
    mgr.set_cert_trust_status("fox", "NOT TRUSTED: opening without certificate trust")

    assert mgr.update_profile("fox", "fox", "", "windows", new_certificate="") is True

    assert mgr.profiles["fox"].certificate is None
    assert mgr.profiles["fox"].cert_trust_status is None


def test_editing_an_unrelated_field_keeps_the_recorded_outcome(mgr):
    """The clear must be conditional on the certificate ACTUALLY changing.
    update_profile is called for every field edit, so an unconditional clear
    would silently discard a real verdict on a rename or a notes edit — its own
    bug, and the reason this test sits beside the two above."""
    mgr.add_profile("fox", "", "windows", engine="firefox", certificate="cert-a")
    mgr.set_cert_trust_status("fox", "NOT TRUSTED: opening without certificate trust")

    assert mgr.update_profile(
        "fox", "fox", "", "windows",
        new_notes="unrelated edit",
        new_certificate="cert-a",
    ) is True

    assert mgr.profiles["fox"].notes == "unrelated edit"
    assert mgr.profiles["fox"].cert_trust_status == (
        "NOT TRUSTED: opening without certificate trust"
    ), "an unrelated edit discarded a real trust verdict"


def test_a_stale_trusted_verdict_is_never_rendered_after_swapping_certificate(mgr):
    """Case C regression guard — the serious one — end to end.

    Cert A imports cleanly ("trusted"), the operator swaps to cert B and has
    NEVER launched with it. Before the fix the dialog rendered "last launch:
    trusted" in the MUTED colour: an affirmative clean bill of health for a CA
    whose trust was never attempted.

    This drives the REAL edit path (update_profile) and then renders the
    resulting profile, because the clear is where the staleness is actually
    resolvable. The render gate cannot decide this case on its own: Profile
    records the verdict but NOT which certificate it describes, so at render
    time `certificate="cert-b"` + "trusted" is indistinguishable from a genuine
    verdict for cert-b. Binding this to the edit path is what makes it a real
    guard rather than one that passes for the wrong reason.
    """
    mgr.add_profile("fox", "", "windows", engine="firefox", certificate="cert-a")
    mgr.set_cert_trust_status("fox", "trusted")

    mgr.update_profile("fox", "fox", "", "windows", new_certificate="cert-b")

    texts = _dialog_texts(mgr.profiles["fox"], cert_names=("cert-a", "cert-b"))
    assert not any("last launch:" in t for t in texts), (
        "the dialog reports 'trusted' against cert-b, whose CA trust was never "
        "attempted"
    )


def test_a_verdict_with_no_certificate_assigned_is_not_rendered():
    """AC5 on the surface, for the removal case — certificate gone, leftover
    status must not render."""
    texts = _dialog_texts(
        Profile(
            name="fox",
            engine="firefox",
            certificate=None,
            cert_trust_status="NOT TRUSTED: opening without certificate trust",
        )
    )
    assert not any("last launch:" in t for t in texts)
