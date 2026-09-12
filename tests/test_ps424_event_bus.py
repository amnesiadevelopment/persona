"""PS-424 — `src/core/events.py::EventBus`, the cross-layer UI-refresh bus, driven for real.

⛔ WHAT THIS FILE IS FOR
------------------------
`EventBus` is the ONLY notification path between the API/MCP layer and the
desktop window: 13 `bus.emit()` producers (`api/routes/browser.py`,
`profiles.py`, `trash.py`) and exactly one consumer,
`src/ui/app.py` → `c.event_bus.subscribe(self.state.schedule_refresh)`.

At `39c5efc` **every method body was unexercised** — `coverage` reported
`src/core/events.py  22 stmts / 13 miss / 41%`, with every executed line a
declaration (imports, `class`, four `def`s) and every body missing. A mutation
battery planting `raise AssertionError` in all three bodies left the failure set
**byte-identical** while a same-file control (`container.py::_get`) fired
selectively, so that was a measurement rather than an absent run.

The two `event_bus` mentions that existed in `tests/` were
`app.dependency_overrides[get_event_bus] = lambda: FakeBus()` — a two-line stub
with a no-op `emit`, in files that do not even collect here (`fastapi` absent).
A stub proves nothing about the object it replaces. **This file drives the REAL
`EventBus` on stdlib alone: no fake, no stub, no `fastapi`, no `flet`.**

WHAT WOULD GO RED — MEASURED, NOT ASSERTED
------------------------------------------
`raise AssertionError("MUTANT")` planted as the first statement of each body,
one body at a time, `src/core/events.py` restored from backup and re-verified
byte-identical after each arm. Counts are of THIS file's 17 tests:

* ``subscribe``   → **13 failed / 4 passed.** First to fail:
                    `test_subscribe_registers_a_callback_that_emit_then_calls`.
* ``unsubscribe`` → **4 failed / 13 passed** — the SELECTIVE arm, and the one
                    worth reading: exactly the four `unsubscribe` tests fail
                    (`..._removes_by_identity_not_equality`,
                    `..._stops_the_named_callback_from_being_called_again`,
                    `..._is_a_no_op_for_a_callback_never_added`,
                    `test_unsubscribing_during_emit_does_not_deadlock`) and
                    nothing else moves. A file that went uniformly red under
                    every mutant would be measuring its own imports.
* ``emit``        → **13 failed / 4 passed.**

Two SHARPER arms, because a planted `raise` only proves the line runs — it does
not prove the property is pinned. Both are plausible wrong implementations:

* `emit` holding the lock ACROSS the callbacks (drop the snapshot copy) →
  **3 failed / 14 passed in 15.4s** — the three re-entrancy tests, each
  reporting `emit did not return within 5.0s` as a FAILURE. ⭐ It did not hang:
  15.4s ≈ 3 × the bound, which is the bounded join doing its job.
* `unsubscribe` using `!=` instead of `is not` →
  **1 failed / 16 passed** — only `test_unsubscribe_removes_by_identity_not_equality`.

NEGATIVE CONTROL: unmutated, 17/17 pass. LINE COVERAGE: a `sys.settrace` +
`threading.settrace` pass over this file alone executes every line this ticket
recorded as missing — `events.py` 13-14, 17-18, 21-22, 25-31 and
`container.py:36` — `missing: NONE` for both.

THE THREE PROPERTIES, AND WHY EACH IS LOAD-BEARING
--------------------------------------------------
A. **One raising subscriber must not stop the rest** (`emit`'s `except` arm).
   The raiser is registered FIRST on purpose: without the `except`, the survivor
   is never reached and the assertion is what notices. Today's single consumer
   is `state.schedule_refresh`; the moment a second one is added, a throw from
   either would otherwise silence the other's redraw.

B. **`unsubscribe` is IDENTITY-based** (`s is not callback`). A value-equality
   removal would silently drop the WRONG callback, and `ui/app.py` registers a
   *bound method* — `self.state.schedule_refresh` — whose equality is by
   `__self__`/`__func__`, so this is not a hypothetical distinction. The test
   registers two callables whose `__eq__` returns True for anything and asserts
   the survivor is the one that was not named.

C. **A subscriber that subscribes DURING `emit` must not deadlock.** `emit`
   copies the list under the lock and calls OUTSIDE it (`:25-27`) — that copy is
   the only thing standing between a plain `threading.Lock` and a hang. The
   hazard is documented in this very codebase one file over:
   `container.py:20-24` records that a plain `Lock` *"deadlocked the first such
   build outright"* and had to become an `RLock`. Same hazard class, same
   codebase, still on a plain `Lock`.

   ⭐ C IS ASSERTED WITH A **BOUNDED JOIN**, never an unbounded one (PS-104 /
   PS-140: a suite that hangs has not produced a test result). The emit runs on
   a daemon thread, the test joins with a timeout, and a regression reports
   `thread did not return` as a FAILURE while the wedged daemon dies with the
   process.

D. `container.py:36` — `return self._get("eb", EventBus)` — was a coverage MISS
   between executing neighbours (`_get`'s own body is covered). A real
   `Container` is built here and `.event_bus` read, so the construction site is
   exercised by the same file that exercises what it constructs.

⚠️ **Not a security property.** Nothing here concerns isolation or leakage; a
regression in these three costs a stale window or a wedged emitting thread.
"""

import threading

import pytest

from src.core.container import Container
from src.core.events import EventBus

#: Every bounded wait in this file. Generous against a loaded CI box, and still
#: two orders of magnitude below the 120s per-test bound — so a genuine
#: deadlock is reported by THIS file's own assertion (naming the property that
#: broke) rather than by a timeout plugin that may not even be installed.
JOIN_TIMEOUT_SECONDS = 5.0


class _EqualsAnything:
    """A callable that claims equality with everything.

    This is the instrument for property B. `list.remove` and an `==`-based
    comprehension would both delete the FIRST element here — i.e. the wrong one
    — while `is not` deletes only the object actually named.
    """

    def __init__(self, tag: str, calls: list[str]) -> None:
        self.tag = tag
        self._calls = calls

    def __call__(self) -> None:
        self._calls.append(self.tag)

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return 1


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


def test_subscribe_registers_a_callback_that_emit_then_calls():
    """A subscribed callback is CALLED by emit — not merely accepted.

    Pins `subscribe`'s body. Asserting on the observed call rather than on
    `_subscribers` keeps the claim about behaviour: a `subscribe` that appended
    to the wrong list would still satisfy a length check.
    """
    bus = EventBus()
    calls: list[str] = []
    bus.subscribe(lambda: calls.append("one"))

    bus.emit()

    assert calls == ["one"], f"the subscribed callback did not run: {calls!r}"


def test_subscribe_keeps_every_subscriber_and_emit_calls_all_of_them():
    """Subscribing twice registers two callbacks, and both are called.

    An `emit` that stopped after the first — or a `subscribe` that replaced
    rather than appended — leaves the second consumer's window stale forever.
    """
    bus = EventBus()
    calls: list[str] = []
    bus.subscribe(lambda: calls.append("first"))
    bus.subscribe(lambda: calls.append("second"))

    bus.emit()

    assert calls == ["first", "second"], f"not every subscriber ran, in order: {calls!r}"


# ---------------------------------------------------------------------------
# A — one raising subscriber does not stop the rest
# ---------------------------------------------------------------------------


def test_a_raising_subscriber_does_not_stop_the_others():
    """THE RAISER IS REGISTERED FIRST, and that ordering is the whole test.

    Without `emit`'s `except` arm the exception propagates out of the loop, the
    survivor is never reached, and `calls` stays empty — so this asserts the
    survivor ACTUALLY RAN rather than that `emit` merely returned.
    """
    bus = EventBus()
    calls: list[str] = []

    def raises() -> None:
        raise RuntimeError("subscriber blew up")

    bus.subscribe(raises)
    bus.subscribe(lambda: calls.append("survivor"))

    bus.emit()  # must not propagate

    assert calls == ["survivor"], (
        "a raising subscriber stopped the ones after it — the surviving "
        f"callback did not run: {calls!r}"
    )


def test_emit_swallows_the_exception_instead_of_propagating_to_the_producer():
    """A lone raising subscriber must not blow up the REST/MCP caller.

    The 13 producers call `bus.emit()` on the request path with no `try`; a
    propagating subscriber error would turn a successful profile creation into
    a 500 *after the profile was already created*.
    """
    bus = EventBus()
    bus.subscribe(lambda: 1 / 0)

    bus.emit()  # the assertion is that this line does not raise


def test_emit_logs_the_subscriber_error_it_swallows(caplog):
    """Swallowed is not the same as silent — the `except` arm must record it.

    Pins the logging line specifically, so an `except Exception: pass`
    refactor (a swallow with no trace, which is how a dead subscriber becomes
    unfindable) goes red.
    """
    bus = EventBus()

    def raises() -> None:
        raise RuntimeError("a distinctive PS-424 failure")

    bus.subscribe(raises)

    with caplog.at_level("ERROR", logger="persona.events"):
        bus.emit()

    messages = [r.getMessage() for r in caplog.records]
    assert any("a distinctive PS-424 failure" in m for m in messages), (
        f"the swallowed subscriber error was not logged: {messages!r}"
    )


# ---------------------------------------------------------------------------
# B — unsubscribe is identity-based
# ---------------------------------------------------------------------------


def test_unsubscribe_removes_by_identity_not_equality():
    """Two callables that compare equal; only the one NAMED is removed.

    `x == y` is True here, so an `==`-based removal drops `x` *and* `y` (or
    drops `y` while `x` survives, depending on the idiom). Identity removal
    leaves exactly `y`.
    """
    bus = EventBus()
    calls: list[str] = []
    x = _EqualsAnything("x", calls)
    y = _EqualsAnything("y", calls)
    assert x == y, "the instrument is broken: these must compare equal"
    assert x is not y

    bus.subscribe(x)
    bus.subscribe(y)
    bus.unsubscribe(x)

    bus.emit()

    assert calls == ["y"], (
        "unsubscribe did not remove by identity: expected only the callback "
        f"that was NOT named to survive, got {calls!r}"
    )


def test_unsubscribe_stops_the_named_callback_from_being_called_again():
    """The unsubscribed callback ACTUALLY does not run on the next emit."""
    bus = EventBus()
    calls: list[str] = []

    def leaving() -> None:
        calls.append("leaving")

    bus.subscribe(leaving)
    bus.emit()
    assert calls == ["leaving"], "precondition failed: the callback never ran at all"

    bus.unsubscribe(leaving)
    bus.emit()

    assert calls == ["leaving"], (
        f"the unsubscribed callback ran again on a later emit: {calls!r}"
    )


def test_unsubscribe_is_a_no_op_for_a_callback_never_added():
    """Removing a stranger must not raise, and must not evict the incumbents.

    A `list.remove`-based implementation raises `ValueError` here — which on
    the UI teardown path would turn an idempotent cleanup into a crash.
    """
    bus = EventBus()
    calls: list[str] = []
    bus.subscribe(lambda: calls.append("incumbent"))

    bus.unsubscribe(lambda: None)  # must not raise

    bus.emit()
    assert calls == ["incumbent"], (
        f"unsubscribing a stranger disturbed the real subscribers: {calls!r}"
    )


# ---------------------------------------------------------------------------
# C — re-entrancy during emit, on a BOUNDED join
# ---------------------------------------------------------------------------


def _emit_on_a_bounded_thread(bus: EventBus) -> threading.Thread:
    """Run `emit` on a daemon thread and join it with a bound.

    ⭐ THE BOUND IS THE POINT (PS-104/PS-140). A re-entrancy regression here is
    a DEADLOCK, and a deadlock asserted with `thread.join()` hangs the suite
    instead of failing it. Daemon so a wedged thread dies with the process
    rather than keeping the interpreter alive after pytest reports.
    """
    returned = threading.Event()

    def run() -> None:
        bus.emit()
        returned.set()

    thread = threading.Thread(target=run, daemon=True, name="ps424-emit")
    thread.start()
    thread.join(JOIN_TIMEOUT_SECONDS)

    assert returned.is_set(), (
        f"emit did not return within {JOIN_TIMEOUT_SECONDS}s — the emitting "
        "thread is deadlocked. EventBus._lock is a plain threading.Lock, so "
        "emit MUST copy the subscriber list under the lock and call the "
        "callbacks outside it (see src/core/events.py:25-27)."
    )
    assert not thread.is_alive()
    return thread


def test_subscribing_during_emit_does_not_deadlock():
    """A subscriber that calls `subscribe` re-enters the lock `emit` holds.

    `emit` copies under the lock and calls outside it, so the re-entrant
    `subscribe` acquires an unheld lock. Hold the lock across the callbacks
    instead — the obvious implementation — and this wedges forever on a plain
    `Lock`, exactly as `container.py` records happening to `_get`.
    """
    bus = EventBus()
    late_calls: list[str] = []

    def resubscribes() -> None:
        bus.subscribe(lambda: late_calls.append("late"))

    bus.subscribe(resubscribes)

    _emit_on_a_bounded_thread(bus)

    # The newcomer joined AFTER this emit's snapshot, so it runs on the NEXT
    # one. Asserting both halves keeps "no deadlock" from passing vacuously on
    # an emit that quietly did nothing.
    assert late_calls == [], f"the newcomer ran inside the emit it joined: {late_calls!r}"
    bus.emit()
    assert late_calls == ["late"], (
        f"the subscription made during emit did not take effect: {late_calls!r}"
    )


def test_unsubscribing_during_emit_does_not_deadlock():
    """The mirror case: a subscriber that removes itself while `emit` runs.

    This is the realistic shape — a UI component tearing itself down inside the
    refresh it was handed — and it re-enters the same lock from the same
    thread.
    """
    bus = EventBus()
    calls: list[str] = []

    def unsubscribes_itself() -> None:
        calls.append("self-removing")
        bus.unsubscribe(unsubscribes_itself)

    bus.subscribe(unsubscribes_itself)

    _emit_on_a_bounded_thread(bus)

    assert calls == ["self-removing"], f"the subscriber never ran: {calls!r}"
    bus.emit()
    assert calls == ["self-removing"], (
        f"the self-removing subscriber was called again after leaving: {calls!r}"
    )


def test_emit_iterates_a_snapshot_taken_under_the_lock():
    """Mutating the roster mid-emit must not disturb THIS emit's fan-out.

    Pins the `list(self._subscribers)` copy itself rather than only its
    deadlock consequence: iterating the live list while a callback appends to
    it is the other half of that line's job.
    """
    bus = EventBus()
    calls: list[str] = []

    def churns() -> None:
        calls.append("churns")
        bus.subscribe(lambda: calls.append("newcomer"))
        bus.unsubscribe(tail)

    def tail() -> None:
        calls.append("tail")

    bus.subscribe(churns)
    bus.subscribe(tail)

    _emit_on_a_bounded_thread(bus)

    assert calls == ["churns", "tail"], (
        "emit did not iterate a snapshot: a roster change made mid-emit "
        f"altered the fan-out already in progress. got {calls!r}"
    )


def test_concurrent_subscribes_do_not_lose_a_subscriber():
    """The lock's actual job: parallel `subscribe` calls all land.

    An unlocked `append` is very hard to lose under CPython's GIL, so this is
    a guard against a future non-atomic `subscribe` (read-modify-write on
    `_subscribers`) rather than a reproduction of a present defect — and it is
    bounded like every other thread here.
    """
    bus = EventBus()
    calls: list[int] = []
    lock = threading.Lock()
    start = threading.Event()
    count = 24

    def make(i: int):
        def cb() -> None:
            with lock:
                calls.append(i)

        return cb

    def worker(i: int) -> None:
        start.wait(JOIN_TIMEOUT_SECONDS)
        bus.subscribe(make(i))

    threads = [
        threading.Thread(target=worker, args=(i,), daemon=True) for i in range(count)
    ]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(JOIN_TIMEOUT_SECONDS)
        assert not t.is_alive(), "a subscribe() call did not return within the bound"

    bus.emit()

    assert sorted(calls) == list(range(count)), (
        f"subscribers were lost under concurrent registration: {sorted(calls)!r}"
    )


# ---------------------------------------------------------------------------
# D — the construction site
# ---------------------------------------------------------------------------


def test_container_event_bus_is_a_real_shared_event_bus():
    """`Container.event_bus` builds a REAL EventBus and returns the same one.

    Executes `container.py:36` — `return self._get("eb", EventBus)` — which was
    the local coverage miss between executing neighbours. Sharing matters
    beyond caching: the API layer and the UI resolve the bus from this one
    container, so a per-call instance would mean the window subscribes to a bus
    nobody emits on and never redraws again.
    """
    container = Container()

    bus = container.event_bus

    assert isinstance(bus, EventBus), f"not a real EventBus: {type(bus)!r}"
    assert container.event_bus is bus, "Container handed out a second EventBus"


def test_the_bus_the_container_builds_actually_delivers():
    """End-to-end through the composition root: subscribe here, emit there.

    The producers hold `container.event_bus` and the UI subscribes on the
    object it got from the same container — so the claim worth asserting is
    that a callback registered through one read is called by an emit issued
    through another.
    """
    container = Container()
    calls: list[str] = []

    container.event_bus.subscribe(lambda: calls.append("redraw"))
    container.event_bus.emit()

    assert calls == ["redraw"], (
        f"an emit on the container's bus did not reach its subscriber: {calls!r}"
    )


@pytest.mark.parametrize("method", ["subscribe", "unsubscribe", "emit"])
def test_every_public_method_is_exercised_by_this_file(method):
    """A cheap tripwire on the SHAPE this file's falsification claim assumes.

    The file header names the measured per-method failure counts. If a method
    is renamed or removed, that mapping silently stops meaning anything — this
    fails loudly instead.
    """
    assert callable(getattr(EventBus, method, None)), (
        f"EventBus.{method} is gone; the falsification mapping in this file's "
        "header no longer describes the object under test"
    )
