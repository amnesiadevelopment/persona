"""PS-298 — a live Firefox profile must not be killed by our own close-watch,
and when a session DOES end the record must say which of the two happened.

THE REPORT. The owner, 2026-09-03: *"есть баг что фф профили через какое то
рандомное время сами закрываются"* — Firefox profiles close by themselves after
some random amount of time. He is the user; he observed it in normal use.

THE MECHANISM, read in code. On the Linux fork path there is no window
enumeration at all (``_visible_window_pids`` is Windows-only), so the ONLY close
signal is the count of the profile's ``-isForBrowser`` content processes. Two
consecutive one-second polls seeing zero destroyed a live browser, because **the
watch returning IS the kill**: the caller runs ``session.teardown()`` and then
``_kill_profile_firefox`` immediately, with no confirmation step and no way for
the session to object. That is a dice roll repeated every second for the whole
life of every Firefox session — the shape of "after some random amount of time".

⚠️ THE HONEST BOUND ON THIS WHOLE FILE, STATED UP FRONT SO NO READER HAS TO
INFER IT. **Nobody has reproduced the owner's bug**, so nothing here is evidence
that his bug is fixed. A content count of zero has at least four causes on a
browser that is alive and wanted — every tab unloaded/discarded on an idle
profile, a content proc crashing between exit and respawn, a cross-origin
navigation replacing every tab's process at once, and ``pgrep`` returning empty
for a beat under load — and no test below establishes WHICH of them he hit.

What these tests DO establish is narrower and worth stating in exactly those
terms:

* the guard is wider than it was (four consecutive zero polls, not two);
* a zero content count that a SECOND, INDEPENDENT signal contradicts does not
  kill the session, and that deferral is bounded rather than open-ended;
* a "could not look" from that second signal decides nothing, in either
  direction (the PS-192/PS-204 discipline);
* and — the property that is worth landing on its own — an operator, and a
  future investigation, **can now tell a heuristic-inferred close from a real
  one**, which no evidence a user could collect could do before.

That last one is what makes the next report answerable, which is why the ticket
asked for it first. Before it, ``close=window-gone`` sat in
``_QUIET_CLOSE_REASONS`` and rendered ``"Session ended: <name>"`` — byte-
identical to the operator closing the window themselves, in the Activity Log and
in the engine log alike. A spurious kill and a deliberate close were literally
indistinguishable, so the bug was unfalsifiable from any evidence a user could
send in.
"""

from __future__ import annotations

import threading

import src.services.browser.invisible_launch as il
from src.services.browser.launcher import (
    _CLOSE_REASON,
    _INFERRED_CLOSE_REASONS,
    _QUIET_CLOSE_REASONS,
)


def _watch(monkeypatch, *, content, children=0, pid=4242, alive=True):
    """Drive ``_fork_close_watch`` over a synthetic content-count sequence.

    ``content`` is the per-poll ``_firefox_content_proc_count`` reading (an int,
    or None for "the scan could not run"). ``children`` is the corroborating
    ``_firefox_engine_child_count`` reading — an int, None, or a callable for a
    sequence. Returns ``(result, logs, leftover)``: ``leftover`` is what is left
    of the content sequence, so a test can assert the watch stopped on exactly
    the poll it should have — an empty leftover means the watch consumed the
    whole sequence without deciding a close.

    Running out of readings sets ``closed``, so the watch exits by the STOP
    path rather than by a ``StopIteration`` escaping through it. That matters:
    an exception thrown from inside the poll would abort the loop at a point of
    the test's choosing and could be mistaken for "did not close", which is the
    very property most of these tests assert.
    """
    seq = list(content)
    consumed = [0]
    closed = threading.Event()

    def count(d, parent=None):
        if consumed[0] >= len(seq):
            closed.set()
            return 1  # a live reading: the STOP exit must not look like a close
        value = seq[consumed[0]]
        consumed[0] += 1
        return value

    monkeypatch.setattr(il, "_firefox_pid", lambda d: pid)
    monkeypatch.setattr(
        il, "_pid_alive",
        alive if callable(alive) else (lambda p: alive),
    )
    monkeypatch.setattr(il, "_firefox_content_proc_count", count)
    monkeypatch.setattr(
        il, "_firefox_engine_child_count",
        children if callable(children) else (lambda parent: children),
    )
    logs: list[str] = []
    got = il._fork_close_watch(
        "/p", closed, interval=0.0, log=logs.append,
    )
    return got, logs, seq[consumed[0]:]


def _close_line(logs):
    for m in logs:
        if "LIFECYCLE close=" in m:
            return m
    raise AssertionError(f"no close line in {logs!r}")


# --------------------------------------------------------------- the guard


def test_three_consecutive_zero_polls_no_longer_kill_a_live_session():
    """THE CENTRAL CLAIM, and the one to read most sceptically.

    At HEAD before this change ``gone_streak_needed`` was 2, so this sequence —
    content procs seen, then zero on three consecutive polls, then back — was a
    KILL. The browser was alive the whole time (the parent never died, the
    content procs came back), and persona destroyed it.

    Note what this test is and is not. It is real evidence **about the guard**:
    the watch is driven with a sequence that used to close and asserted not to.
    It is NOT evidence about the owner's report, because nothing here reproduces
    the cause that produced his zeros. Widening a debounce makes a coincidence
    rarer; it cannot make it impossible, and this test does not pretend
    otherwise.

    The sequence is deliberately three zeros, not one: a one-zero test would
    pass on the OLD code too, and a check that could not have failed is not
    coverage.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        got, logs, leftover = _watch(
            monkeypatch,
            # seen → three zeros (would have killed at 2) → alive again.
            content=[6, 0, 0, 0, 6, 6],
        )
    finally:
        monkeypatch.undo()

    assert got == {4242}, "the watch ran to the end of the sequence, as intended"
    assert leftover == [], "the whole sequence was consumed — nothing closed early"
    assert not any("LIFECYCLE close=window-gone" in m for m in logs), (
        "three consecutive zero-content polls killed a live browser; the old "
        "two-poll guard is still in force"
    )


def test_a_genuine_sustained_close_still_closes():
    """⛔ The counterweight, and it is not optional.

    The ticket is explicit: do NOT simply remove the close detection. Without
    it a profile the user genuinely closed stays reported as "running" for the
    parent's whole 60-90s shutdown (#168), the card lies, and #143/#168 come
    straight back — a fix that trades a false kill for a false "still running"
    has moved the defect, not removed it.

    So a REAL close — content procs gone AND the engine child fleet gone with
    them — must still be detected promptly. This test is what stops the one
    above from being satisfied by breaking the feature.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        got, logs, leftover = _watch(
            monkeypatch,
            content=[6, 6, 0, 0, 0, 0, 6, 6, 6],  # tail must never be reached
            children=0,                            # the fleet went with it
        )
    finally:
        monkeypatch.undo()

    assert got == {4242}
    assert leftover == [6, 6, 6], (
        "closed on the 4th zero poll, not later — the debounce widened by two "
        "polls (~2s), it did not become open-ended"
    )
    assert "close=window-gone-inferred" in _close_line(logs)


def test_the_corroborating_signal_defers_a_kill_the_browser_contradicts():
    """The second, INDEPENDENT signal — the part that is not just a bigger number.

    A longer streak only makes a coincidence rarer. This asks a genuinely
    different question: is the browser still running a process FLEET? A live
    Firefox window is never a lone parent — the socket process, the GPU process
    and the tab content processes are all ``firefox`` binaries under it — and a
    real window close takes that fleet down in the same phase that kills the
    content procs. So engine children still running while the content count
    reads zero is the browser CONTRADICTING the inference.

    Here the content count is zero for six straight polls — well past the
    four-poll guard, so this cannot pass by the widened debounce alone — and the
    session survives because the fleet says it is alive.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        got, logs, leftover = _watch(
            monkeypatch,
            content=[4, 0, 0, 0, 0, 0, 0, 4],
            children=3,  # the browser is plainly still running
        )
    finally:
        monkeypatch.undo()

    assert leftover == [], "nothing closed — the whole sequence was consumed"
    assert got == {4242}
    # The only close on the record is the harness's own STOP at the end of the
    # sequence — never a window-gone verdict.
    assert not any("close=window-gone" in m for m in logs)
    assert "close=stop-requested" in _close_line(logs)


def test_the_deferral_is_bounded_and_cannot_wedge_a_profile_running():
    """The cost of being wrong about the corroboration is BOUNDED.

    If ``_firefox_engine_child_count`` is wrong on some host — reporting
    children for a browser that really is gone — an unbounded deferral would
    wedge the profile "running" forever, which is the #143 defect wearing a new
    hat. So after ``unconfirmed_streak_needed`` zero-content polls the close
    fires anyway, and it fires under a DIFFERENT reason token that records the
    disagreement.

    A run whose closes are mostly ``unconfirmed`` is the fingerprint of exactly
    the defect this ticket describes — now readable from a log instead of
    invisible.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        got, logs, _ = _watch(
            monkeypatch,
            content=[4] + [0] * 40,
            children=3,  # never agrees
        )
    finally:
        monkeypatch.undo()

    assert got == {4242}, "the bounded fallback must still close"
    close = _close_line(logs)
    assert "close=window-gone-unconfirmed" in close, (
        "a close taken over the corroborating signal's objection must not wear "
        "the same token as one it agreed with"
    )
    assert "engine_children=3" in close, (
        "the objection itself must be on the record, not merely its verdict"
    )


def test_a_no_verdict_corroboration_decides_nothing_in_either_direction():
    """"Could not look" is not an answer — the PS-192/PS-204 discipline.

    ``None`` from the scan must neither BLOCK a close (which would wedge the
    profile "running" whenever the process table was briefly unreadable) nor be
    read as "no children" (which would strengthen an inference on the strength
    of a failed scan). It leaves the behaviour exactly as it was before the
    corroboration existed: the widened debounce alone decides.

    This is the asymmetry the ticket named — a successful scan finding nothing
    and a scan that could not run were much closer than the code assumed.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        got, logs, leftover = _watch(
            monkeypatch,
            content=[4, 0, 0, 0, 0, 9, 9],
            children=None,  # could not look
        )
    finally:
        monkeypatch.undo()

    assert got == {4242}
    assert leftover == [9, 9], "closed on the 4th zero poll — the debounce decided"
    assert "close=window-gone-inferred" in _close_line(logs)


def test_the_streak_and_the_deferral_both_reset_on_a_live_poll():
    """A profile that recovers is fully forgiven, not merely paused.

    Three zeros, then live, then three zeros again must NOT accumulate into a
    close: the second episode starts from zero. Otherwise a long-lived session
    would eventually be killed by the SUM of unrelated transients, which is the
    same unbounded-random-delay shape the owner reported, reached a different
    way.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        got, logs, leftover = _watch(
            monkeypatch,
            content=[5, 0, 0, 0, 5, 0, 0, 0, 5],
            children=0,  # would confirm instantly if a streak ever reached 4
        )
    finally:
        monkeypatch.undo()

    assert leftover == []
    assert got == {4242}
    assert not any("close=window-gone" in m for m in logs)
    assert "close=stop-requested" in _close_line(logs)


# ------------------------------------------------- the observability half


def test_the_close_line_carries_the_evidence_the_decision_rested_on():
    """The pre-kill content count and streak are ON the line.

    Before this the line said ``close=window-gone pid=N streak=2`` and nothing
    about WHAT was measured. Without the evidence on the record no fix to the
    heuristic could be shown to have worked, which is why the ticket asked for
    observability FIRST.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        _, logs, _ = _watch(
            monkeypatch, content=[7, 0, 0, 0, 0], children=0, pid=1234,
        )
    finally:
        monkeypatch.undo()

    close = _close_line(logs)
    assert "pid=1234" in close
    assert "content=0" in close
    assert "streak=4" in close
    assert "engine_children=0" in close


def test_a_declined_kill_is_on_the_record_too():
    """The deferral is LOGGED, not silent.

    A kill that was declined is exactly as interesting as one that fired — it is
    the direct evidence that the corroborating signal is doing something on this
    host. Emitted once per episode (the streak resets on any live poll) so a
    long idle profile cannot flood the Activity Log, but the FIRST deferral is
    always on the record.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        _, logs, _ = _watch(
            monkeypatch,
            content=[4, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0, 4],
            children=2,
        )
    finally:
        monkeypatch.undo()

    deferrals = [m for m in logs if "close-deferred" in m]
    assert deferrals, "a declined kill left no trace"
    assert "engine_children=2" in deferrals[0]
    assert len(deferrals) == 2, (
        "one line per deferral EPISODE (two episodes here), not one per poll — "
        "a per-poll emit would flood the log on an idle profile"
    )


def test_the_inferred_close_never_claims_the_observed_close_s_token():
    """``window-gone`` is what a REAL window enumeration produces.

    The thread path (Windows) emits it after actually looking at the window
    list. The fork path has no window enumeration at all and infers the close
    from a process count, so it must not emit the same word — otherwise the two
    populations are unseparable in a log, which is the state this ticket exists
    to end.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        _, logs, _ = _watch(monkeypatch, content=[3, 0, 0, 0, 0], children=0)
    finally:
        monkeypatch.undo()

    token = _CLOSE_REASON.search(_close_line(logs)).group(1)
    assert token != "window-gone"
    assert token in _INFERRED_CLOSE_REASONS


def test_every_new_reason_is_still_parsed_and_still_quiet():
    """The consumer half of the rename, in the SAME change.

    ``launcher.py`` matches ``LIFECYCLE close=(\\S+)`` and looks the token up in
    ``_QUIET_CLOSE_REASONS``. A new reason that is not added there makes every
    NORMAL Linux close read "Session ended unexpectedly" in the Activity Log —
    a second false signal introduced by fixing the first. (PS-204 shipped that
    exact pairing; this is its guard, re-armed for the new tokens.)
    """
    import pytest

    for children, expected in ((0, "window-gone-inferred"),
                               (3, "window-gone-unconfirmed")):
        monkeypatch = pytest.MonkeyPatch()
        try:
            _, logs, _ = _watch(
                monkeypatch,
                content=[3] + [0] * 40,
                children=children,
            )
        finally:
            monkeypatch.undo()
        m = _CLOSE_REASON.search(_close_line(logs))
        assert m, "the launcher's own regex does not parse the close line"
        assert m.group(1) == expected
        assert m.group(1) in _QUIET_CLOSE_REASONS, (
            f"{m.group(1)!r} is not quiet, so an ordinary Linux close now "
            "reports as an unexpected end"
        )


def test_the_pid_unresolved_fallback_is_labelled_an_inference_too():
    """The #203 degraded path infers as well, and says so.

    When the pid never resolves, the close signal becomes "no firefox process
    anywhere in our own subtree" — different evidence, and stronger than a
    content count, but still an inference rather than an observed window close.
    It keeps its own two-poll debounce (the stronger signal does not need the
    widened one) and carries the inferred token plus a note of what it measured.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(il, "_firefox_pid", lambda d: None)
        alive = iter([True, True, False, False])
        monkeypatch.setattr(il, "_forked_firefox_alive", lambda: next(alive))
        logs: list[str] = []
        got = il._fork_close_watch(
            "/p", threading.Event(), no_process_timeout=0.0, interval=0.0,
            log=logs.append,
        )
    finally:
        monkeypatch.undo()

    assert got is None
    close = _close_line(logs)
    assert "close=window-gone-inferred" in close
    assert "evidence=no-firefox-in-subtree" in close
    assert _CLOSE_REASON.search(close).group(1) in _QUIET_CLOSE_REASONS


# ---------------------------------------- the second signal's own contract


def test_the_engine_child_count_treats_could_not_look_as_no_verdict():
    """The helper itself must never render a failed scan as "no processes".

    That rendering is precisely what PS-192's false clean was caused by, and
    what PS-204 was written to stop. Here it would be worse than a wrong number:
    it would silently CONFIRM every kill on a host where the process table is
    unreadable, i.e. turn the new safety check into a rubber stamp.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(il, "_session_descendants", lambda roots: None)
        assert il._firefox_engine_child_count(4242) is None
    finally:
        monkeypatch.undo()


def test_the_engine_child_count_counts_only_firefox_binaries():
    """A live browser's fleet, not "any child process".

    The fork child also has non-browser descendants (the node driver, helper
    shells), and counting those would make the signal permanently non-zero —
    a corroboration that can never agree is a corroboration that always defers,
    which the bounded fallback would then paper over. Matched on the engine
    binary path, which always carries "firefox" (.../firefox-NN/firefox), the
    same needle ``_forked_firefox_alive`` uses.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(il._platform, "IS_WINDOWS", False)
        monkeypatch.setattr(il, "_session_descendants", lambda roots: {5, 6, 7})
        cmds = {
            5: b"node\x00/driver/cli.js\x00run-driver",
            6: b"/cache/firefox-15/firefox\x00-contentproc\x00-isForBrowser",
            7: b"/cache/firefox-15/firefox\x00-contentproc\x00-childID\x002",
        }
        monkeypatch.setattr(il, "_proc_cmdline", lambda p: cmds.get(p))
        assert il._firefox_engine_child_count(4242) == 2

        monkeypatch.setattr(il, "_session_descendants", lambda roots: {5})
        assert il._firefox_engine_child_count(4242) == 0, (
            "a confident zero must be reachable, or the signal can never agree"
        )
    finally:
        monkeypatch.undo()


def test_the_engine_child_count_uses_the_one_snapshot_walk():
    """Cost, and it is a correctness matter here rather than a nicety.

    This runs on EVERY poll of EVERY open profile. PS-204 measured the per-node
    ``pgrep -P`` walk at 84ms and 13 subprocesses per scan against a modest
    tabbed Firefox, and replaced it with one snapshot (a /proc read on Linux,
    one ``ps`` on macOS) for exactly that reason. Adding a second per-second
    scan on the old walk would have re-imported the cost that walk exists to
    avoid — and a scan slow enough to fall behind is a scan whose answer is
    stale, which is the wrong property for a signal that gates a kill.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(il._platform, "IS_WINDOWS", False)
        monkeypatch.setattr(il, "_proc_cmdline", lambda p: b"firefox")
        calls: list = []
        monkeypatch.setattr(
            il, "_session_descendants",
            lambda roots: calls.append(roots) or set(),
        )

        def _forbidden(root):
            raise AssertionError(
                "_descendant_pids is the per-node pgrep walk PS-204 removed "
                "from the poll path; it must not be reintroduced here"
            )

        monkeypatch.setattr(il, "_descendant_pids", _forbidden)
        assert il._firefox_engine_child_count(4242) == 0
        assert calls == [{4242}], "anchored on the tracked parent only"
    finally:
        monkeypatch.undo()


def test_the_engine_child_count_is_no_verdict_on_windows():
    """The Windows path has real window enumeration and does not use this."""
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(il._platform, "IS_WINDOWS", True)
        assert il._firefox_engine_child_count(4242) is None
    finally:
        monkeypatch.undo()
