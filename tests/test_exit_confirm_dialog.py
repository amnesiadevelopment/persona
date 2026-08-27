"""Closing persona with browsers open ASKS FIRST (PS-223 outcome 2).

The owner's decision: a dialog naming how many profiles are open, offering to
close them and exit, or to cancel. Not a silent teardown — an accidental click
on the window's X must not destroy work sitting in a browser.

WHAT THESE TESTS COVER AND WHAT THEY DO NOT. They drive the App's real close
handler with a fake flet page, so the BRANCHING is exercised: when the question
is asked, when it is skipped, what cancel does, what confirm does, and — the
one that would wedge the product — what happens when something raises after
`prevent_close` has already intercepted the close. What they cannot do is press
a real X on a real window; that is done by hand on the user's path (PS-17).

This dialog covers the CLEAN path only, and deliberately so. A crash or a kill
from Task Manager never reaches it, which is why the persisted registry exists
and why the launch guard must not depend on this dialog having run.
"""

import flet as ft
import pytest

from src.ui.app import App


class _FakeWindow:
    def __init__(self):
        self.prevent_close = False
        self.on_event = None
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class _FakePage:
    def __init__(self):
        self.window = _FakeWindow()
        self.dialogs = []
        self.popped = 0

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self):
        self.popped += 1

    def update(self):
        pass


class _CloseEvent:
    type = ft.WindowEventType.CLOSE


class _OtherEvent:
    type = ft.WindowEventType.RESIZE


def _app(running=(), survivors=(), shutdown_raises=False):
    """An App with its collaborators stubbed down to what the close path uses.

    Built with __new__ rather than a real constructor: App.__init__ builds a
    whole container (profile manager, proxy service, stores), none of which the
    close handler touches, and standing all of it up would test the constructor
    rather than the behaviour under examination.
    """
    app = App.__new__(App)
    calls = {"shutdown": 0}

    class _BL:
        def running_profile_names(self):
            return set(running)

        def survivors(self):
            return [type("R", (), {"profile": n})() for n in survivors]

        def shutdown_all(self):
            calls["shutdown"] += 1
            if shutdown_raises:
                raise RuntimeError("teardown blew up")

    app.bl = _BL()
    app.page = _FakePage()
    return app, calls


def _buttons(dlg):
    """(cancel, confirm) — the dialog builds them in that order."""
    return dlg.actions[0], dlg.actions[1]


def test_the_guard_intercepts_the_close():
    app, _ = _app()

    app._install_close_guard(app.page)

    assert app.page.window.prevent_close is True
    assert app.page.window.on_event == app._on_window_event


def test_closing_with_no_browsers_open_does_not_ask():
    """A question with one possible answer is a nuisance, and a user who meets
    a pointless dialog on every exit learns to dismiss the one that matters."""
    app, calls = _app(running=())

    app._on_window_event(_CloseEvent())

    assert app.page.dialogs == []
    assert app.page.window.destroyed is True


def test_closing_with_a_browser_open_asks_first_and_does_not_close_yet():
    app, calls = _app(running=("alpha",))

    app._on_window_event(_CloseEvent())

    assert len(app.page.dialogs) == 1
    assert app.page.window.destroyed is False, "the window must wait for an answer"
    assert calls["shutdown"] == 0, "nothing is torn down before the user answers"


def test_the_dialog_names_the_profiles_that_are_open():
    """Naming them, not just counting them: the user's real question is whether
    the profile they care about is in the list."""
    app, _ = _app(running=("alpha", "beta"))

    app._on_window_event(_CloseEvent())

    body = app.page.dialogs[0].content.value
    assert "alpha" in body and "beta" in body
    assert "2" in app.page.dialogs[0].title.value


def test_survivors_are_counted_among_the_open_browsers():
    """A browser left by a PREVIOUS persona is still a window the user is about
    to lose sight of, so it belongs in the question."""
    app, _ = _app(running=(), survivors=("ghost",))

    app._on_window_event(_CloseEvent())

    assert len(app.page.dialogs) == 1
    assert "ghost" in app.page.dialogs[0].content.value


def test_cancel_leaves_everything_running_and_the_window_open():
    """THE ACCIDENTAL CLOSE, refused — the whole point of the dialog."""
    app, calls = _app(running=("alpha",))
    app._on_window_event(_CloseEvent())
    cancel, _ = _buttons(app.page.dialogs[0])

    cancel.on_click(None)

    assert app.page.window.destroyed is False
    assert calls["shutdown"] == 0
    assert app.page.popped == 1, "the dialog is dismissed"


def test_confirm_tears_the_browsers_down_and_closes():
    """Confirm routes through shutdown_all — the SAME teardown atexit runs, and
    the one PS-192 made reap the whole process group. Not a second, weaker
    path: the one that already works, invoked on a gesture."""
    app, calls = _app(running=("alpha",))
    app._on_window_event(_CloseEvent())
    _, confirm = _buttons(app.page.dialogs[0])

    confirm.on_click(None)

    assert calls["shutdown"] == 1
    assert app.page.window.destroyed is True


def test_a_teardown_failure_still_closes_the_window():
    """Once prevent_close has intercepted the close, failing to destroy leaves
    a window that CANNOT BE CLOSED. Failing toward closing is the only safe
    direction from here."""
    app, calls = _app(running=("alpha",), shutdown_raises=True)
    app._on_window_event(_CloseEvent())
    _, confirm = _buttons(app.page.dialogs[0])

    confirm.on_click(None)

    assert app.page.window.destroyed is True


def test_a_handler_failure_closes_rather_than_wedging_the_window():
    """Same reasoning, one level up: if deciding what to ask raises, close."""
    app, _ = _app(running=("alpha",))

    def boom():
        raise RuntimeError("cannot read the running set")

    app._open_browser_names = boom

    app._on_window_event(_CloseEvent())

    assert app.page.window.destroyed is True


def test_non_close_window_events_are_ignored():
    """A resize must not tear down the browsers."""
    app, calls = _app(running=("alpha",))

    app._on_window_event(_OtherEvent())

    assert app.page.dialogs == []
    assert app.page.window.destroyed is False
    assert calls["shutdown"] == 0


def test_installing_the_guard_never_breaks_startup():
    """A guard that cannot be installed must leave persona CLOSABLE, never
    wedge it shut."""
    app, _ = _app()

    class _Hostile:
        window = property(lambda self: (_ for _ in ()).throw(RuntimeError("nope")))

    app._install_close_guard(_Hostile())  # must not raise
