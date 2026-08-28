"""PS-208: a revert must SURVIVE the restart it demands.

`revert_to_previous_build` restored the retained bundle and returned, recording
nothing. So after the restart it itself instructs, the 60-second update poll
resolved the just-rejected release again and drove it straight back in — and on
Linux, where `auto_update` defaults to True, that re-install is UNATTENDED. The
loop, end to end: revert -> mandatory restart -> `_app_latest` resets to "" ->
the poll sees the rejected tag as unseen AND as newer than the restored build ->
the tag-keyed staged installer is still on disk -> `_when_update_ready` ->
`_apply_update`, with no dialog and nobody present.

The engine side solved exactly this one module over, with a settings pin read
before any check runs. This is the application-side counterpart.

THE DESIGN CHOICE THIS SUITE ENCODES (the ticket left it open and required it be
recorded rather than made silently): the hold names the REJECTED release, not
the reverted-to one. The engine chose the opposite — its pin names the build to
STAY ON and pauses updating outright — but an app revert is an INSTALL rather
than a change of which build directory launches, and the release after a bad one
is precisely the one likely to carry the fix. So a hold suppresses the rejected
release and everything not newer than it, while a STRICTLY newer release is
offered normally. `test_a_newer_release_still_installs_normally` is that choice
stated as a test, and it is what stops the hold from silently becoming permanent.

EVIDENCE DISCIPLINE (the owner's standing directive: a check that could not have
failed is not coverage). Every assertion below is on the DECISION THE UPDATE
PATH TOOK — was the installer downloaded, was it applied, what does the panel
render — and the hold itself is written by a REAL `revert_to_previous_build`
against real files on disk. Nothing here asserts that a setter was called, which
would pass against an implementation that records a hold nobody reads.
"""

import os
import time
from types import SimpleNamespace

from src.services.app_update import updater as au
from src.ui import app as ui_app

# The retention suite already owns a real packaged-Linux install fixture and a
# real end-to-end update driver; reusing them keeps this file asserting on the
# same ground truth rather than on a second, weaker imitation of it. Importing
# across test files is this suite's existing convention (test_app_update_linux_
# retention.py itself imports make_app from test_app_ui.py).
from tests.test_app_update_linux_retention import (
    _drive_linux_update,
    _linux_fixture,
    _stage,
)

# The release the operator rejects, and the one they land back on. APP_VERSION
# is the RUNNING build, which on the revert path is by definition the one being
# reverted FROM — reverting is a rename, so the process still executing is the
# bad build. Tests that need that asymmetry patch APP_VERSION explicitly.
REJECTED = "3.0.2"
RESTORED = "3.0.1"
FIXED = "3.0.3"


class _FakePage:
    def __init__(self):
        self.dialogs = []
        self.popped = 0

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self):
        self.popped += 1


def _fresh_process_app(monkeypatch, *, running=()):
    """An App in the state a FRESH PROCESS starts in — which is the whole point
    of AC3 and the trap a careless fix falls into.

    The revert's success message MANDATES a restart, and `__init__` sets
    `self._app_latest = ""` (app.py:141). So the in-memory dedup that would
    otherwise suppress a re-offer is wiped by the very restart the revert asks
    for: a fix that only cleared `_app_latest` would fix nothing at all. Every
    field below is therefore set to its `__init__` value, not to a convenient
    one, so the poll's `tag != self._app_latest` guard is genuinely open and the
    hold is the only thing that can close it.
    """
    app = ui_app.App.__new__(ui_app.App)
    app.page = _FakePage()
    app.bl = SimpleNamespace(running_profile_names=lambda: list(running))
    app._update_in_progress = False
    app._update_staged = ""
    app._app_latest = ""          # app.py:141 — the fresh-process value
    app._app_update_url = ""
    app._app_update_size = 0
    app._app_update_tag = ""
    app._app_update_status = ""
    app._app_update_done = 0
    app._app_update_total = 0
    app._app_rollback_status = ""
    app._update_start_t = 0.0
    app._onboarding_open = False
    app._pending_update = None
    app._ui = lambda fn: fn()
    app._refresh_sidebar = lambda: None
    app.logs = []
    app._log = app.logs.append
    app.applied = []
    app._apply_update = app.applied.append
    return app


def _wire_update_path(monkeypatch, tmp_path, *, tag, linux=True, auto=True):
    """Wire a discoverable, installable release `tag`.

    Deliberately generous: the download SUCCEEDS, verification PASSES, and on
    Linux auto-update is ON with no profiles running. Every gate other than the
    hold is therefore open, so a test that observes no install has observed the
    hold doing it — not some unrelated refusal.
    """
    staged = tmp_path / f"persona-update-{tag}.AppImage"
    downloads = []

    def fake_download(url, progress=None, size=0, tag="", **k):
        downloads.append(url)
        staged.write_bytes(b"payload")
        return str(staged)

    monkeypatch.setattr(ui_app.app_update, "can_self_update", lambda: True)
    monkeypatch.setattr(
        ui_app.app_update, "find_ready_staged", lambda url, size=0, tag="": ""
    )
    monkeypatch.setattr(ui_app.app_update, "download_update", fake_download)
    monkeypatch.setattr(
        ui_app.app_update, "verify_staged_installer",
        lambda s, tag="", log=None: True,
    )
    monkeypatch.setattr(ui_app._platform, "IS_LINUX", linux)
    monkeypatch.setattr(
        ui_app.app_settings, "is_auto_update_enabled", lambda: auto
    )
    return staged, downloads


def _settle(cond, timeout=5.0):
    """Wait for an async decision to land. Returns True if it did."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _really_revert(monkeypatch, tmp_path):
    """Perform a REAL revert on a REAL packaged Linux install, and return the
    install path.

    The hold under test is written by `revert_to_previous_build` itself, so
    stubbing that call would remove the very thing being tested. This drives a
    genuine update (the bad build lands at the install path) and then a genuine
    revert (the previous binary comes back), exactly as the retention suite
    does — the hold is a side effect of that real gesture, never injected.
    """
    target = _linux_fixture(monkeypatch, tmp_path)
    staged = _stage(monkeypatch, tmp_path, b"the-bad-build")
    _drive_linux_update(monkeypatch, staged)

    # The operator is running the bad build when they press "go back", so that
    # is what APP_VERSION reads at revert time — and therefore what is held.
    monkeypatch.setattr(au, "APP_VERSION", REJECTED)
    assert au.revert_to_previous_build(log=lambda m: None) == str(target)
    return target


# --- AC1 + AC3: the rejected release is not re-offered after the restart -----


def test_a_reverted_release_is_not_re_offered_after_the_restart(
    monkeypatch, tmp_path
):
    # THE HEADLINE. AC1 and AC3 together, because the restart is what makes AC1
    # hard: the process that comes back has no memory of the release it just
    # rejected, so the hold is the only thing standing between the operator and
    # the build they deliberately walked away from.
    _really_revert(monkeypatch, tmp_path)

    # The restart happens here. Everything in-memory is gone; the app is now
    # running the RESTORED build and re-discovers the rejected release.
    monkeypatch.setattr(au, "APP_VERSION", RESTORED)
    app = _fresh_process_app(monkeypatch)
    staged, downloads = _wire_update_path(monkeypatch, tmp_path, tag=REJECTED)

    app._on_update_found(REJECTED, "http://releases/persona-3.0.2")

    # The decision, not the bookkeeping: nothing was fetched and nothing was
    # installed. Asserting that a setter ran would pass against a hold that is
    # recorded and never read, which is precisely the defect being fixed.
    assert downloads == [], (
        "the rejected release was downloaded again after the revert"
    )
    assert app.applied == [], (
        "the rejected release was INSTALLED again after the revert"
    )
    assert app.page.dialogs == [], (
        "the operator was re-prompted to install the release they rejected"
    )


def test_a_staged_installer_left_on_disk_does_not_slip_past_the_hold(
    monkeypatch, tmp_path
):
    # The readiest path of the three, and the one a gate placed too low would
    # miss. find_ready_staged is TAG-KEYED and the revert does not delete the
    # installer, so after a revert the rejected build's installer is very
    # likely still sitting there — a short-circuit that skips the download
    # entirely and goes straight to "ready". The hold must be read ABOVE it.
    _really_revert(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "APP_VERSION", RESTORED)

    app = _fresh_process_app(monkeypatch)
    leftover = tmp_path / "leftover-3.0.2.AppImage"
    leftover.write_bytes(b"the-bad-build")
    _wire_update_path(monkeypatch, tmp_path, tag=REJECTED)
    monkeypatch.setattr(
        ui_app.app_update, "find_ready_staged",
        lambda url, size=0, tag="": str(leftover),
    )

    app._on_update_found(REJECTED, "http://releases/persona-3.0.2")

    assert app.applied == [], "the leftover installer re-installed the rejection"
    assert app._update_staged == "", (
        "the rejected build was offered as a ready update"
    )


# --- AC4: the Linux unattended arm specifically -----------------------------


def test_the_linux_unattended_arm_does_not_fire_for_a_held_release(
    monkeypatch, tmp_path
):
    # The one arm where being wrong is a genuine loss of operator control:
    # Linux + auto-update on + no profiles running installs with NO DIALOG.
    # Driven directly at _when_update_ready rather than through the funnel
    # above, because this arm is reachable without passing that funnel — the
    # download thread calls it on completion — so a single gate upstream would
    # leave it open for a hold recorded mid-download.
    _really_revert(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "APP_VERSION", RESTORED)

    app = _fresh_process_app(monkeypatch, running=())   # idle: the arm's condition
    staged = tmp_path / "ready-3.0.2.AppImage"
    staged.write_bytes(b"the-bad-build")
    monkeypatch.setattr(ui_app._platform, "IS_LINUX", True)
    monkeypatch.setattr(
        ui_app.app_settings, "is_auto_update_enabled", lambda: True
    )

    app._when_update_ready(REJECTED, str(staged))

    assert app.applied == [], (
        "the unattended Linux arm re-installed the rejected build with no "
        "operator present — the whole failure this hold exists to end"
    )
    assert app.page.dialogs == []


def test_the_unattended_arm_still_fires_for_a_release_that_is_not_held(
    monkeypatch, tmp_path
):
    # The paired positive control. Without it, an implementation that simply
    # broke the unattended path would pass the test above — and headless boxes
    # rely on that path working.
    app = _fresh_process_app(monkeypatch, running=())
    staged = tmp_path / "ready-3.0.3.AppImage"
    staged.write_bytes(b"the-fix")
    monkeypatch.setattr(ui_app._platform, "IS_LINUX", True)
    monkeypatch.setattr(
        ui_app.app_settings, "is_auto_update_enabled", lambda: True
    )

    app._when_update_ready(FIXED, str(staged))

    assert app.applied == [str(staged)], (
        "with no hold recorded the unattended arm must still install"
    )


# --- AC6: the hold must not become silently permanent -----------------------


def test_a_newer_release_still_installs_normally(monkeypatch, tmp_path):
    # THE DESIGN CHOICE, stated as a test. A hold that swallowed every future
    # release would be a worse defect than the loop it closes: the operator
    # reverted BECAUSE a release was bad, and the next one probably carries the
    # fix. Holding the REJECTED release rather than pausing updates outright is
    # what makes this pass — and it is why this suite does not simply mirror
    # the engine's pin.
    _really_revert(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "APP_VERSION", RESTORED)

    app = _fresh_process_app(monkeypatch)
    staged, downloads = _wire_update_path(monkeypatch, tmp_path, tag=FIXED)

    app._on_update_found(FIXED, "http://releases/persona-3.0.3")

    assert _settle(lambda: app.applied), "the fix release was never installed"
    assert downloads == ["http://releases/persona-3.0.3"]
    assert app.applied == [str(staged)], (
        "a release NEWER than the rejected one must update normally, or the "
        "hold has silently become permanent"
    )


def test_the_hold_covers_the_rejected_release_and_anything_older(
    monkeypatch, tmp_path
):
    # The boundary, at the service layer where it is decided. "Not newer than
    # the thing I rejected" is not an upgrade in any case, so the hold covers
    # the rejection and everything below it — and stops there.
    _really_revert(monkeypatch, tmp_path)

    assert au.update_held(REJECTED) is True     # the rejection itself
    assert au.update_held("3.0.0") is True      # older than it
    assert au.update_held(FIXED) is False       # strictly newer — the fix
    assert au.update_held("") is False          # no release is not a held one


# --- AC5: the operator can SEE the hold, and can clear it -------------------


def _panel_texts(app, monkeypatch):
    """Every string the version panel renders."""
    from tests.test_app_ui import _walk_texts

    monkeypatch.setattr(
        ui_app.app_settings, "is_auto_update_enabled", lambda: False
    )
    app._app_latest = ""
    app._app_update_status = ""
    app._update_staged = ""
    return _walk_texts(app._build_version_panel())


def test_the_held_state_is_visible_on_the_panel_not_only_in_the_log(
    monkeypatch, tmp_path
):
    # _on_app_rollback's own docstring argues at length that _log is NOT a
    # visible surface — the sidebar log panel renders only while expanded — so
    # a hold that announced itself only there would be invisible to exactly the
    # operator who needs to know their updates are being held. It goes on the
    # row they are already looking at.
    _really_revert(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "APP_VERSION", RESTORED)
    app = _fresh_process_app(monkeypatch)

    texts = _panel_texts(app, monkeypatch)

    assert f"resume updates (held {REJECTED})" in texts, texts


def test_a_panel_with_no_hold_offers_no_resume_row(monkeypatch, tmp_path):
    # The negative control: without it, a row rendered unconditionally would
    # pass the test above. It also pins the state ORDER — with no hold the row
    # falls through to the ordinary "go back" gesture rather than shadowing it.
    app = _fresh_process_app(monkeypatch)
    monkeypatch.setattr(ui_app.app_update, "rollback_target", lambda: "")

    texts = _panel_texts(app, monkeypatch)

    assert not any("resume updates" in t for t in texts), texts


def test_the_operator_can_clear_the_hold_and_the_release_is_offered_again(
    monkeypatch, tmp_path
):
    # A hold with no way out is a trap, not a safeguard. Clicking resume must
    # clear it AND actually restore the offer — asserted by driving the update
    # path afterwards rather than by reading the setting back, because "the
    # setting is empty" would pass against a resume that left the release
    # unreachable anyway.
    _really_revert(monkeypatch, tmp_path)
    monkeypatch.setattr(au, "APP_VERSION", RESTORED)
    app = _fresh_process_app(monkeypatch)

    app._on_app_resume_updates()

    assert au.held_version() == ""
    staged, downloads = _wire_update_path(monkeypatch, tmp_path, tag=REJECTED)
    app._on_update_found(REJECTED, "http://releases/persona-3.0.2")

    assert _settle(lambda: app.applied), (
        "after resuming, the release must be installable again"
    )
    assert downloads == ["http://releases/persona-3.0.2"]
    # and the panel stops claiming a hold that no longer exists
    assert not any("resume updates" in t for t in _panel_texts(app, monkeypatch))


def test_resuming_clears_the_seen_tag_so_the_poll_can_re_offer(
    monkeypatch, tmp_path
):
    # The subtle half of "the operator can clear it". The 60s poll dedups on
    # `tag != self._app_latest`, so a resume that left the held tag sitting in
    # that field would be NOMINAL — the poll would skip the release forever and
    # the operator would wait for a third one to appear.
    _really_revert(monkeypatch, tmp_path)
    app = _fresh_process_app(monkeypatch)
    app._app_latest = REJECTED

    app._on_app_resume_updates()

    assert app._app_latest == "", (
        "the poll's dedup must be reopened, or the resume never re-offers"
    )


# --- the revert must not be turned into a failure by an unwritable store -----


def test_an_unwritable_settings_file_does_not_fail_a_completed_revert(
    monkeypatch, tmp_path
):
    # The engine's _set_pin records the rule this mirrors: "a settings file
    # that cannot be written must not turn a COMPLETED revert into a reported
    # failure... a missing pin costs the reversal its DURABILITY, not its
    # correctness." The bundle on disk is already the reverted one.
    target = _linux_fixture(monkeypatch, tmp_path)
    original = target.read_bytes()
    staged = _stage(monkeypatch, tmp_path, b"the-bad-build")
    _drive_linux_update(monkeypatch, staged)

    def _boom(_version):
        raise OSError("read-only file system")

    monkeypatch.setattr(
        "src.core.settings.set_app_update_hold", _boom, raising=True
    )

    msgs = []
    went = au.revert_to_previous_build(log=msgs.append)

    assert went == str(target), "a settings failure aborted a completed revert"
    assert target.read_bytes() == original, "the revert itself must still stand"
    # and the operator is TOLD the durability was lost, rather than being left
    # to discover it when the update returns
    assert any("couldn't record" in m for m in msgs), msgs


def test_an_unreadable_settings_file_does_not_brick_the_update_path(
    monkeypatch, tmp_path
):
    # The fail-soft DIRECTION matters and is not symmetric with the write. An
    # unreadable store must degrade to "not held" — normal updating — because
    # the alternative is an install that can never take a security update
    # again. Same direction as engine/updater.py's pinned_build().
    def _boom():
        raise OSError("settings unreadable")

    monkeypatch.setattr("src.core.settings.app_update_hold", _boom, raising=True)

    assert au.update_held(FIXED) is False
    assert au.held_version() == ""


# --- the hold survives the process, which is the entire point ---------------


def test_the_hold_is_written_where_a_later_process_will_read_it(
    monkeypatch, tmp_path
):
    # Durability stated at its narrowest: the hold is in the settings FILE, not
    # in a module global that the mandatory restart would wipe. Read back
    # through a fresh call chain, exactly as the process after the restart does.
    _really_revert(monkeypatch, tmp_path)

    from src.core import settings

    assert os.path.exists(settings._path()), "no settings file was written"
    assert settings.app_update_hold() == REJECTED
