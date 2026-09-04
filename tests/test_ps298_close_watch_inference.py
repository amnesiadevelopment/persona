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
* a second, INDEPENDENT measurement — the browser's engine-child fleet — is
  recorded on every close line, so the next report is answerable;
* ⚠️ that measurement **gates nothing**, deliberately. A gate on it would need
  a premise this codebase contradicts three times in live-measured comments
  (``_run_invisible_forked``'s teardown, ``_thread_close_watch``, and the very
  existence of ``_kill_profile_firefox``, which force-kills the GPU/socket
  children AFTER the watch returns). If the fleet does outlive the window here
  too, a gate would defer every ordinary close and flag it as an anomaly —
  worse than the defect it guards. Nobody has measured which way it goes on
  this path, so the number goes on the record and a handful of real operator
  logs get to settle it;
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
    or None for "the scan could not run"). ``children`` is the RECORDED
    ``_firefox_engine_child_count`` reading — an int, None, or a callable for a
    sequence. It gates nothing (see
    ``test_the_engine_child_reading_is_recorded_and_never_obeyed``); it is here
    so a test can assert what reaches the close line. Returns ``(result, logs, leftover)``: ``leftover`` is what is left
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


def test_the_engine_child_reading_is_recorded_and_never_obeyed():
    """⚠️ THE DELIBERATE NON-CHANGE, and the reason it is deliberate.

    The obvious next step after widening the debounce is to GATE the kill on a
    second signal: measure whether the browser still has an engine child fleet,
    and defer while it does. This test pins that the gate does **not** exist,
    because the premise it would need is one this codebase contradicts three
    times in live-measured comments (all of them predating this change):

      * ``_run_invisible_forked``'s teardown — "parent + GPU/content/socket
        children stayed alive after an X-close";
      * ``_thread_close_watch`` — "the multi-process Firefox does NOT exit when
        the window is X-closed — GPU/content/socket firefox.exe children (and
        the connected parent) keep running";
      * ``_kill_profile_firefox`` exists to force-kill "a parent's GPU/content/
        socket children" AFTER the watch returned — which only makes sense if
        that fleet is still up at that moment.

    A gate would need the opposite to be true. If it is not, then on a GENUINE
    close this count stays non-zero for as long as the parent lingers (60-90s,
    #168), so every ordinary close would be deferred for the whole length of the
    gate's fallback and stamped as a disagreement — the one diagnostic this
    ticket adds would read maximally alarming on a perfectly healthy host. That
    is a worse defect than the one it would be guarding against, and nobody has
    measured which world the Linux fork path is in.

    So the number is put ON the close line and obeyed by nothing. The sequence
    below is the exact shape that reconciliation turns on: content procs die in
    one poll (#168's live-measured 6 → 0) while the socket/GPU children linger
    with the parent. The close must fire on the 4th zero poll — the debounce
    alone — and carry ``engine_children=2`` so a real operator log can settle
    the question a gate would have had to assume.
    """
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        got, logs, leftover = _watch(
            monkeypatch,
            content=[6, 6, 0, 0, 0, 0, 6, 6, 6],
            children=2,  # socket + GPU still up, as the three comments describe
        )
    finally:
        monkeypatch.undo()

    assert got == {4242}
    assert leftover == [6, 6, 6], (
        "the close was delayed past the 4th zero poll — the engine-child count "
        "is gating the kill, on a premise this file contradicts three times"
    )
    close = _close_line(logs)
    assert "close=window-gone-inferred" in close, (
        "a lingering child fleet changed the reason token, so an ordinary close "
        "on such a host would be reported as an anomaly"
    )
    assert "engine_children=2" in close, (
        "the measurement is not on the record, so nothing a user sends in can "
        "settle whether the fleet outlives the window on this path"
    )
    assert not any("close-deferred" in m for m in logs)


def test_a_no_verdict_engine_reading_is_recorded_as_a_failed_scan():
    """"Could not look" must be visibly a failed scan, not a confident zero.

    ``None`` from the helper means the process table could not be read. On a
    line an operator sends in, rendering that as ``engine_children=0`` would be
    the PS-192 false clean all over again — a reader counting confident zeros
    would count failures among them. It reaches the line as ``None``, and it
    changes no decision (there is no decision for it to change: see the test
    above).
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
    close = _close_line(logs)
    assert "close=window-gone-inferred" in close
    assert "engine_children=None" in close, (
        "a scan that could not run is rendered as a confident zero"
    )


def test_the_streak_resets_on_a_live_poll():
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
            children=0,
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
    """⛔ WITHDRAWN — there is no declined kill to record.

    The earlier revision of this change gated the kill on the engine-child
    count and logged a ``close-deferred`` line whenever it declined. That gate
    is gone (see ``test_the_engine_child_reading_is_recorded_and_never_obeyed``
    for why: its premise is contradicted three times in the file it lives in
    and has never been measured on this path), so there is no deferral, and a
    ``close-deferred`` line would be a claim about a decision nothing makes.

    This test is kept as its INVERSE rather than deleted, because a
    silently-vanished test is how a removed behaviour comes back by accident:
    if a future change reintroduces the gate, this fails and sends the reader
    to the reasoning instead of letting it land unnoticed.
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

    assert not any("close-deferred" in m for m in logs), (
        "a deferral was logged, so the kill is being gated on the engine-child "
        "count again — read that helper's docstring before restoring it"
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
    exact pairing; this is its guard, re-armed for the new token.)

    Driven at both ends of the engine-child reading — a confident zero and a
    fleet still up — because the token must NOT vary with it: that number is
    recorded, not obeyed, so an ordinary close on a host where the fleet
    lingers has to read exactly like one where it does not.
    """
    import pytest

    for children in (0, 3, None):
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
        assert m.group(1) == "window-gone-inferred", (
            f"the reason token varied with the engine-child reading "
            f"({children!r}), so an ordinary close on some hosts would be "
            "reported as a different kind of event"
        )
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
    what PS-204 was written to stop. Nothing consults this value — it gates no
    decision (see `test_the_engine_child_reading_is_recorded_and_never_obeyed`)
    — so the harm is not a wrong kill but a wrong RECORD, which on this change
    is the whole deliverable: a host whose process table is unreadable would
    put a confident `engine_children=0` on every close line, and the operator
    logs this ticket exists to make answerable would then read as "the fleet
    was down" on exactly the runs where we could not look at all. None must
    stay None so a failed scan is visibly a failed scan.
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
    a number that can never read zero is a number that measures nothing, and
    the second assertion below pins that a confident zero is reachable at all.
    That matters because the value is the RECORD a future gate would be built
    on: an always-positive count would look like "the fleet always lingers" on
    every host and would settle the open question the wrong way by construction.
    Matched on the engine binary path, which always carries "firefox"
    (.../firefox-NN/firefox), the same needle ``_forked_firefox_alive`` uses.
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


def test_the_engine_child_count_is_anchored_on_the_tracked_pid():
    """SCOPE, not speed — and the distinction is the whole safety argument.

    ⚠️ This does NOT run every poll. It is called once per session, on the
    single poll whose debounce has already decided to close (pinned by
    `test_the_engine_child_reading_is_recorded_and_never_obeyed`, which drives a
    six-poll session and asserts exactly one call), so PS-204's 84ms/13-
    subprocess figure — a measurement of a per-second, per-profile walk — is not
    what is being avoided here and must not be cited as if it were.

    The reason for `_session_descendants` is PS-204/#150's anchoring: it walks
    down from the pids WE tracked, so a concurrently relaunching Firefox of the
    same profile can never enter the count, where a profile-dir rescan would
    match it (the #150 race the teardown's ``rescan=False`` exists to avoid).
    ``_descendant_pids`` is refused for the same reason it was removed
    elsewhere, and `calls == [{4242}]` is the assertion that actually carries
    the property: the scan starts at the tracked parent and nowhere else.
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
                "_descendant_pids rescans by pgrep from a root rather than "
                "walking the tracked pid set; use _session_descendants"
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
