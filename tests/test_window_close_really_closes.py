"""Clicking the X actually CLOSES persona — the window goes AND the process ends.

WHY THIS FILE EXISTS SEPARATELY FROM ``test_exit_confirm_dialog.py``.

That suite has 14 tests, every one of them green, and it was green throughout
the period in which persona could not be closed at all (PS-303: the owner
reported the X and the taskbar close both doing nothing, on Windows AND Linux).
It could not have caught the defect, and the reason is worth stating precisely
rather than treating as an oversight: it drives the close handler against a
``_FakeWindow`` whose ``destroy()`` is a plain method that sets a flag. So it
asserts that *persona called destroy*, which was true the whole time. What it
cannot assert — what no fake with a synchronous ``destroy()`` can assert — is
whether calling it *does anything*.

It does not, on the real object. In flet 0.85.3 ``Window.destroy`` is declared
``async def`` (``flet/controls/core/window.py``) and its body is a single
``await self._invoke_method("destroy")``. Calling it bare therefore constructs
a coroutine and discards it: the ``INVOKE_METHOD`` message is never sent, the
native client never hears about the close, and because ``prevent_close`` has
already swallowed the OS's own close, nothing ends the window or the process.
The only trace is a ``RuntimeWarning: coroutine 'Window.destroy' was never
awaited`` on a stream the flet launcher redirects.

So these tests are written against the REAL ``ft.Window`` and flet's REAL event
dispatch (``BaseControl._trigger_event``, the same code path that delivers a
native CLOSE), with a recording stand-in only at the session boundary — the
socket to the Flutter client, which genuinely cannot exist in a test. The
oracle is therefore not "was a method called" but **"did an instruction reach
the client"**, which is the distinction the whole bug lives in.

Two halves of "closed", and both are asserted here because the report named
both: the WINDOW must be destroyed (``test_the_close_reaches_the_client``) and
the PROCESS must end (``test_the_close_arms_the_process_exit``).

FALSIFICATION — these tests fail against the pre-fix code. Restoring the old
one-line body::

    def _destroy_window(self) -> None:
        page = self.page
        if page is None:
            return
        try:
            page.window.destroy()
        except Exception:
            logger.exception("Could not destroy the window on close")

leaves ``invoked == []`` in every test below that asserts ``"destroy" in
session.invoked``, and leaves the exit unarmed. Verified by reverting.
"""

import asyncio
import inspect
import warnings
import weakref

import flet as ft
import pytest

from src.ui.app import App


# --------------------------------------------------------------------------
# The real flet objects, wired to a recording session.
# --------------------------------------------------------------------------


class _RecordingSession:
    """Stands in for ``flet.messaging.Session`` at the ONE seam a test cannot
    have: the socket to the Flutter client.

    ``invoke_method`` is where an imperative window call becomes an actual
    ``INVOKE_METHOD`` message on the wire, so recording it answers the only
    question that matters — did the instruction leave Python. Everything above
    it (the Window control, the event payload, the dispatch) is the real thing.
    """

    def __init__(self):
        self.invoked: list[str] = []

    async def invoke_method(self, control_id, name, args=None, timeout=None):
        self.invoked.append(name)
        return None

    async def after_event(self, *a, **k):
        return None

    def error(self, *a, **k):  # pragma: no cover - flet's error sink
        pass

    @property
    def index(self):
        class _Index:
            def get(self, _):
                return None

        return _Index()


def _real_page():
    """A real ``ft.Page`` with a real ``ft.Window``, attached to a recorder.

    flet resolves ``control.page`` by walking ``_parent``, which is normally set
    when the control tree is mounted by the session. Nothing mounts anything
    here, so the link is made explicitly — this is plumbing to make the REAL
    objects usable, not a substitute for them.
    """
    session = _RecordingSession()
    page = ft.Page(sess=session)
    object.__setattr__(page.window, "_parent", weakref.ref(page))
    return page, session


def _app(page, running=(), survivors=()):
    """An App with only the collaborators the close path touches.

    ``App.__new__`` for the same reason the sibling suite uses it: the real
    constructor builds a whole container, none of which this path reads.
    """

    app = App.__new__(App)

    class _BL:
        def running_profile_names(self):
            return set(running)

        def survivors(self):
            return [type("R", (), {"profile": n})() for n in survivors]

        def shutdown_all(self):
            pass

        def close_all_survivors(self):
            return []

    app.bl = _BL()
    app.page = page
    # The REAL guard installation — the same call site startup uses. Wiring
    # the handler by hand would test a hand-wiring; this tests persona's.
    app._install_close_guard(page)
    assert page.window.prevent_close is True
    return app


def _exit_recorder(app):
    """Replace the process-exit backstop with a counter.

    It is a daemon thread that ends in ``os._exit(0)``; left live it would take
    the test runner down. Recording it keeps the behaviour assertable rather
    than merely disabled — "did this path arm the exit" is half the deliverable.
    """
    armed = {"n": 0}
    app._exit_process = lambda: armed.__setitem__("n", armed["n"] + 1)
    return armed


async def _deliver_native_close(page):
    """Deliver a CLOSE through flet's OWN dispatch, as the OS does.

    ``_trigger_event`` is the method ``Session.dispatch_event`` calls when a
    native window event arrives, so the handler receives a genuine
    ``WindowEvent`` built by flet from the same payload shape — not a hand-rolled
    object with a ``type`` attribute that a mismatch in the real one would not
    reproduce.
    """
    await page.window._trigger_event("event", {"type": "close"})


# --------------------------------------------------------------------------
# The event actually arrives — the ticket's first lead, confirmed or killed.
# --------------------------------------------------------------------------


def test_flets_real_close_event_satisfies_the_handlers_guard():
    """The guard's early return is NOT what swallows the close.

    ``_on_window_event`` returns unless ``e.type == ft.WindowEventType.CLOSE``,
    and a shape mismatch there would look exactly like the reported symptom.
    Built by flet itself from a native close payload, the event satisfies it —
    so this lead is killed, and the failure is downstream of the guard.
    """
    page, _ = _real_page()
    seen = []
    page.window.on_event = seen.append

    asyncio.run(_deliver_native_close(page))

    assert len(seen) == 1, "flet delivered no window event at all"
    event = seen[0]
    assert isinstance(event, ft.WindowEvent)
    assert event.type == ft.WindowEventType.CLOSE
    assert getattr(event, "type", None) == ft.WindowEventType.CLOSE, (
        "the handler's own comparison must hold against the real payload"
    )


def test_window_destroy_is_a_coroutine_function_on_this_flet():
    """THE DEFECT, stated as an executable fact about the pinned dependency.

    This is the whole cause in one assertion: a call to ``destroy()`` produces
    an awaitable, so calling it without awaiting sends nothing. Pinned at
    ``flet==0.85.3``; if a future bump makes it synchronous this test fails and
    tells the reader to simplify ``_await_destroy`` rather than leaving dead
    compatibility code behind.
    """
    assert inspect.iscoroutinefunction(ft.Window.destroy), (
        "if destroy() is no longer async, _await_destroy's awaitable branch "
        "is dead code — revisit it rather than deleting this test"
    )


# --------------------------------------------------------------------------
# The window really closes.
# --------------------------------------------------------------------------


def test_the_close_reaches_the_client():
    """THE REGRESSION TEST. No browsers open, X clicked, and the instruction
    to destroy the window must actually leave Python.

    Against the pre-fix code this fails with ``invoked == []``: the coroutine
    was built and dropped, so the client was never told anything and the window
    stayed up — precisely what the owner reported.
    """
    page, session = _real_page()
    app = _app(page)
    _exit_recorder(app)

    async def scenario():
        await _deliver_native_close(page)
        # The handler schedules the destroy on the running loop; yield to it.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert "destroy" in session.invoked, (
        "persona called window.destroy() but nothing reached the client — the "
        "coroutine was never awaited, so the window never closed"
    )


def test_closing_leaves_no_un_awaited_coroutine_behind():
    """The symptom's only diagnostic trace, asserted directly.

    A dropped coroutine emits ``RuntimeWarning: coroutine 'Window.destroy' was
    never awaited`` at collection. Asserting its ABSENCE catches a regression
    that re-introduces the bug in a different shape — e.g. building the
    coroutine and then failing to hand it to a loop — which an assertion about
    ``invoked`` alone could miss if some other call happened to populate it.
    """
    page, _ = _real_page()
    app = _app(page)
    _exit_recorder(app)

    async def scenario():
        await _deliver_native_close(page)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        asyncio.run(scenario())
        import gc

        gc.collect()

    never_awaited = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
    ]
    assert not never_awaited, (
        "a window coroutine was built and dropped: "
        + "; ".join(str(w.message) for w in never_awaited)
    )


def test_confirming_the_dialog_reaches_the_client_too():
    """The PS-223 path closes for real as well.

    The confirmation is the owner's feature and stays; this asserts that saying
    "close them and exit" now ends in an actual close rather than in the same
    silently-dropped coroutine. The dialog's button is clicked directly, which
    is how the sibling suite drives it.
    """
    page, session = _real_page()
    app = _app(page, running=("alpha",))
    _exit_recorder(app)

    shown = []
    page.show_dialog = shown.append
    page.pop_dialog = lambda: None

    async def scenario():
        await _deliver_native_close(page)
        assert len(shown) == 1, "the confirmation must still be asked"
        assert "destroy" not in session.invoked, (
            "nothing may close before the user answers"
        )
        _cancel, confirm = shown[0].actions[0], shown[0].actions[1]
        confirm.on_click(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert "destroy" in session.invoked, (
        "the user confirmed the exit and the window was still never destroyed"
    )


def test_cancelling_does_not_reach_the_client():
    """Cancel is unchanged: nothing closes, nothing is sent.

    The positive control for the tests above — if ``destroy`` appeared here,
    they would be passing for a reason unrelated to the gesture under test.
    """
    page, session = _real_page()
    app = _app(page, running=("alpha",))
    armed = _exit_recorder(app)

    shown = []
    page.show_dialog = shown.append
    page.pop_dialog = lambda: None

    async def scenario():
        await _deliver_native_close(page)
        shown[0].actions[0].on_click(None)  # cancel
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert session.invoked == [], "a cancelled close must send nothing"
    assert armed["n"] == 0, "a cancelled close must not arm the process exit"


def test_a_resize_does_not_close_the_window():
    """Non-CLOSE window events stay inert — asserted against a REAL event.

    The sibling suite makes this claim with a hand-rolled object; here flet
    builds the payload, so a change in how it deserialises event types cannot
    turn a resize into a close without this failing.
    """
    page, session = _real_page()
    app = _app(page, running=("alpha",))
    armed = _exit_recorder(app)

    async def scenario():
        await page.window._trigger_event("event", {"type": "resized"})
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert session.invoked == []
    assert armed["n"] == 0


# --------------------------------------------------------------------------
# The PROCESS really ends. The other half of "closed".
# --------------------------------------------------------------------------


def test_the_close_arms_the_process_exit():
    """THE SECOND HALF OF THE REPORT: the pid must not survive the close.

    A window that disappears while the interpreter keeps running is
    indistinguishable, to the user, from a window that will not close — and it
    is the shape the taskbar-close half of the report points at. So the close
    path arms the exit backstop, and that is asserted rather than assumed.
    """
    page, _ = _real_page()
    app = _app(page)
    armed = _exit_recorder(app)

    asyncio.run(_deliver_native_close(page))

    assert armed["n"] == 1, "closing must end the process, not just the window"


def test_the_exit_is_armed_even_when_there_is_no_page():
    """A close with no page must still exit rather than wedge.

    Pre-fix this returned early and did nothing at all — an intercepted close
    that goes nowhere. Once ``prevent_close`` has swallowed the OS gesture,
    doing nothing is the one unacceptable outcome.
    """
    app = App.__new__(App)
    app.page = None
    armed = _exit_recorder(app)

    app._destroy_window()

    assert armed["n"] == 1


def test_the_exit_is_armed_even_when_destroy_itself_raises():
    """Failing toward closing, verified rather than asserted in a docstring.

    ``_install_close_guard``'s stated rule is that nothing after the
    interception may leave persona unclosable. If the destroy blows up, the
    process exit is still armed.
    """
    page, _ = _real_page()
    app = _app(page)
    armed = _exit_recorder(app)

    def _boom(_page):
        raise RuntimeError("the client is gone")

    app._await_destroy = _boom

    app._destroy_window()

    assert armed["n"] == 1


def test_the_exit_is_armed_only_once():
    """Two closes in flight must not race two exits.

    A user can click X twice, and the confirm path can be re-entered; arming
    the teardown twice would run ``shutdown_all`` concurrently with itself.
    """
    page, _ = _real_page()
    app = _app(page)
    app._exiting = False
    exits = []
    app._finish_exit = lambda: exits.append(1)

    app._exit_process()
    app._exit_process()
    app._exit_process()

    assert app._exiting is True


def test_the_exit_backstop_tears_down_the_browsers_before_it_forces_the_exit():
    """``os._exit`` skips ``atexit``, so the teardown must be explicit.

    ``BrowserLauncher`` registers ``shutdown_all`` with ``atexit`` — which
    ``os._exit`` does not run. Forcing the exit without calling it first would
    orphan every browser process persona launched, trading an unclosable window
    for leaked chromiums. This drives ``_finish_exit`` directly (the thread it
    normally runs on would end the test session) with the force patched out.
    """
    page, _ = _real_page()
    app = _app(page)

    order = []
    app.bl.shutdown_all = lambda: order.append("shutdown_all")
    app.stop_api_server = lambda: order.append("stop_api_server")

    import src.ui.app as app_mod

    real_exit = app_mod.os._exit
    real_grace = app_mod._EXIT_GRACE_SECONDS
    app_mod.os._exit = lambda code: order.append(f"os._exit({code})")
    app_mod._EXIT_GRACE_SECONDS = 0
    try:
        app._finish_exit()
    finally:
        app_mod.os._exit = real_exit
        app_mod._EXIT_GRACE_SECONDS = real_grace

    assert order == ["stop_api_server", "shutdown_all", "os._exit(0)"], (
        "the browsers must be reaped before the process is forced down"
    )


# --------------------------------------------------------------------------
# The same defect class, one file over.
# --------------------------------------------------------------------------


def test_window_center_is_also_driven_rather_than_dropped():
    """``configure_page``'s fallback centring had the identical bug.

    ``Window.center`` is ``async def`` too, so the bare call in the
    unreadable-work-area fallback was a silent no-op. Not the reported symptom
    — a window opens off-centre rather than refusing to close — but the same
    mistake, and left in place it is the shape a future reader copies.
    """
    from src.ui.theme.page import _center_window

    page, session = _real_page()

    async def scenario():
        _center_window(page)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        asyncio.run(scenario())
        import gc

        gc.collect()

    assert "center" in session.invoked, "center() never reached the client"
    assert not [
        w for w in caught
        if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
    ]
