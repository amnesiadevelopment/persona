"""The panic wipe clears the Activity Log the operator is actually looking at.

Clearing the FILE log (tests/test_wipe_clears_logs.py) is only half the surface.
`_load_recent_log_lines()` is the SEED, and it runs in exactly one place —
`AppState.__init__`, i.e. application startup. The Activity Log the operator
reads is the in-memory ring `AppState._log_lines`, which accumulates
independently via `add_log()` and is rendered by the bottom console dock
(`_flush_log`) and the fullscreen dialog (`get_all_log_lines`).

So before this change the primary flow still failed the ticket's own headline:
the operator hits panic wipe, opens the Activity Log, and reads the wiped
identity back. Only the wipe-then-RESTART flow looked clean. Measured against
the shipped code with the file-log clear already in place:

    AC3 seed lines naming wiped profile : []
    IN-MEMORY deque lines naming it     : ['... > Created profile: acme-bank-viktor',
                                           '... > [acme-bank-viktor] imported 12 cookies']

These tests drive the REAL gesture end to end — a real `App` on a real
`Container`, the real typed-confirmation dialog, the real `_do_wipe` — and
assert through the real reading surfaces. A test that called `clear_log()`
directly could not have caught this defect, because the defect was that nothing
called it.
"""
import pathlib
import threading

import flet as ft
import pytest

import src.ui.state as state
from src.core.container import Container
from src.core.logging import setup_logging
from src.ui.app import App
from src.ui.refs import UIRefs

NAME = "acme-bank-viktor"
SECOND_NAME = "hmrc-self-assessment"


class FakePage:
    """Stands in for ft.Page: captures the dialog the app shows and records
    repaints, so the real dialog callbacks can be driven from a test."""

    def __init__(self) -> None:
        self.dlg = None
        self.popped = 0
        self.updates = 0

    def show_dialog(self, dlg) -> None:
        self.dlg = dlg

    def pop_dialog(self) -> None:
        self.popped += 1

    def update(self) -> None:
        self.updates += 1

    def run_task(self, handler, *args, **kwargs):
        return None


def _real_refs() -> UIRefs:
    """The real UIRefs the render path writes into — real flet controls, so
    _flush_log()'s repaint of the sidebar can be asserted rather than assumed."""
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


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A real App on a real Container, pointed at a tmp PERSONA_HOME. Nothing
    about the wipe path is stubbed — the profile manager, the state and the
    dialog are all the shipped objects."""
    monkeypatch.setenv("PERSONA_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))

    log_dir = tmp_path / "logs"
    import src.core.config as cfg

    monkeypatch.setattr(cfg, "LOG_DIR", str(log_dir), raising=False)
    monkeypatch.setattr(state, "LOG_DIR", str(log_dir), raising=False)
    setup_logging(str(log_dir))

    import src.services.profile.manager as mod

    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(tmp_path / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path / "data"), raising=False)

    a = App(container=Container())
    a.page = FakePage()
    a.refs = _real_refs()
    return a


def _wipe_through_the_real_dialog(app) -> None:
    """Perform the operator's actual gesture: open the wipe confirmation, type
    the confirmation word, click the destructive button. This runs the shipped
    `_do_wipe` — the callback the dialog was handed — not a test-local copy of
    it."""
    app._on_wipe_all()
    dlg = app.page.dlg
    assert dlg is not None, "the wipe confirmation dialog never opened"
    field = dlg.content.controls[1]
    confirm_btn = dlg.actions[1]

    field.value = "DELETE"
    field.on_change(None)
    assert not confirm_btn.disabled, "typing DELETE should arm the wipe button"
    confirm_btn.on_click(None)


def _naming(lines, name=NAME) -> list[str]:
    return [ln for ln in lines if name in ln]


def _seed_a_session(app, name=NAME) -> None:
    """Create through the REAL manager so the REAL log site fires, and put a
    line in the ring the way the running app does — via the app's own _log(),
    which is what every UI action calls."""
    app.pm.add_profile(name, "", "windows")
    app._log(f"[{name}] imported 12 cookies")


# --- the premise, guarded so nothing below can pass vacuously ---


def test_the_activity_log_really_names_the_profile_before_the_wipe(app):
    _seed_a_session(app)
    assert _naming(app.state.get_all_log_lines()), (
        "premise: the in-memory Activity Log shows the name before the wipe"
    )


# --- the operator-facing surfaces, after the real gesture ---


def test_the_activity_log_names_no_wiped_profile_after_the_wipe(app):
    # THE headline: the operator performs the wipe and the Activity Log must
    # not list the identity. Asserted through the same reader the UI uses.
    _seed_a_session(app)
    _wipe_through_the_real_dialog(app)

    assert _naming(app.state.get_all_log_lines()) == [], app.state.get_all_log_lines()


def test_the_fullscreen_activity_log_dialog_names_no_wiped_profile(app):
    # The fullscreen dialog renders state.get_all_log_lines() (handlers.py
    # open_log_fullscreen). Drive that surface, not the deque directly.
    _seed_a_session(app)
    _wipe_through_the_real_dialog(app)

    shown: list[list[str]] = []
    import src.ui.handlers as handlers

    original = handlers.open_log_dialog
    try:
        handlers.open_log_dialog = lambda page, log_lines: shown.append(log_lines)
        app.h.open_log_fullscreen()
    finally:
        handlers.open_log_dialog = original

    assert shown, "the fullscreen Activity Log dialog never opened"
    assert _naming(shown[0]) == [], shown[0]


def test_the_console_dock_repaints_empty_after_the_wipe(app):
    # The console keeps its last painted ROWS until a flush happens, so clearing
    # the ring without marking a flush pending would leave the names on screen
    # anyway. _do_wipe clears BEFORE _refresh_profiles() so the repaint lands in
    # the same pass — assert the real controls, not the intent.
    #
    # This asserts the dock, which is the surface that renders the ring since
    # the log moved out of the rail (PS-179). The console appends by sequence
    # number rather than rebuilding, so the wipe is specifically the one case
    # that MUST discard painted rows: clear_log() drives the seq backwards, and
    # a console that ignored that would keep showing the wiped identity.
    from src.ui.components.log_dock import LogDock

    app._dock = LogDock()
    _seed_a_session(app)
    app._flush_log()
    assert app._dock.row_count, "premise: the console had painted rows"
    assert _naming([r for r in app.state.get_all_log_lines()]), "premise: naming it"

    _wipe_through_the_real_dialog(app)

    assert app._dock.row_count == 0, "the console still holds rows after the wipe"


def test_every_wiped_profile_is_gone_from_the_log_not_just_the_first(app):
    _seed_a_session(app, NAME)
    _seed_a_session(app, SECOND_NAME)
    _wipe_through_the_real_dialog(app)

    lines = app.state.get_all_log_lines()
    assert _naming(lines, NAME) == [], lines
    assert _naming(lines, SECOND_NAME) == [], lines


# --- the ring and the file agree; the wipe still does its job ---


def test_the_wipe_still_destroys_the_profiles_it_cleared_the_log_for(app):
    # The clearing must not have quietly replaced the destruction: the wipe's
    # own contract still holds through the real gesture.
    _seed_a_session(app)
    data_dir = app.pm._data_path(NAME)
    pathlib.Path(data_dir).mkdir(parents=True, exist_ok=True)
    pathlib.Path(data_dir, "Cookies").write_text("logged-in", encoding="utf-8")

    _wipe_through_the_real_dialog(app)

    assert app.pm.list_profiles() == []
    assert not pathlib.Path(data_dir).exists()


def test_logging_after_the_wipe_reaches_the_activity_log_again(app):
    # Clearing the ring must not break it: the Activity Log keeps working, so
    # the post-wipe session still reports what it is doing.
    _seed_a_session(app)
    _wipe_through_the_real_dialog(app)

    app._log("Browser started!")

    lines = app.state.get_all_log_lines()
    assert any("Browser started!" in ln for ln in lines), lines
    assert _naming(lines) == [], lines


def test_a_profile_created_after_the_wipe_is_shown_normally(app):
    _seed_a_session(app)
    _wipe_through_the_real_dialog(app)
    _seed_a_session(app, "fresh-start")

    assert _naming(app.state.get_all_log_lines(), "fresh-start"), (
        "the Activity Log must keep working for profiles created after a wipe"
    )


def test_clear_log_is_safe_against_a_concurrent_writer(app):
    # add_log() runs from launcher threads while the UI thread wipes. The ring
    # is lock-guarded; this asserts the clear holds that contract rather than
    # racing a writer into a half-cleared deque.
    _seed_a_session(app)
    stop = threading.Event()

    def _spam() -> None:
        while not stop.is_set():
            app.state.add_log("background chatter")

    t = threading.Thread(target=_spam, daemon=True)
    t.start()
    try:
        for _ in range(50):
            app.state.clear_log()
    finally:
        stop.set()
        t.join(timeout=5)

    # Whatever the writer added, nothing is corrupt and the wiped name is gone.
    assert _naming(app.state.get_all_log_lines()) == []
