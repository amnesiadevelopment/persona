"""The persisted running-session registry (PS-223).

WHAT THESE TESTS ARE FOR, and what they deliberately do NOT claim. The defect
is that persona's record of which profiles are running lives only in memory, so
after an unclean exit it reports a live browser as not running and offers a
second launch on the same profile directory. These tests cover the LOGIC of the
persisted replacement: what a record means, when a probe may refuse a launch,
and — the case most likely to be missed — when it must NOT.

They do not cross a real process boundary with a real browser; that is done by
hand on the user's path, per the project's closing rule (PS-17), and a green
run here is not a substitute for it. What they DO cover is the discrimination
that makes the guard safe: a record whose process is gone, a record whose pid
has been reused, and a record whose liveness cannot be established at all.
"""

import json
import os
import subprocess
import sys
import time

import pytest

from src.services.browser.session_registry import (
    Liveness,
    SessionRecord,
    SessionRegistry,
    capture_create_time,
    liveness_of,
    make_record,
)


def _record(**over) -> SessionRecord:
    base = dict(
        profile="p1",
        pid=os.getpid(),
        create_time=capture_create_time(os.getpid()),
        pgid=None,
        engine="chromium",
        started_at=time.time(),
        owner_pid=os.getpid(),
    )
    base.update(over)
    return SessionRecord(**base)


# --------------------------------------------------------------------------
# Liveness — the discrimination the whole guard rests on
# --------------------------------------------------------------------------


def test_live_process_with_matching_create_time_is_alive():
    """The positive case: this very process, recorded honestly, reads ALIVE."""
    assert liveness_of(_record()) is Liveness.ALIVE


def test_record_for_a_dead_process_is_gone_not_alive():
    """THE STALE-RECORD CASE — the one that turns a safety catch into a lockout.

    A process is started and REAPED, so the record describes something that is
    genuinely gone. The probe must say GONE. If this ever answers ALIVE (or
    UNKNOWN, which callers also decline to refuse on) the user is locked out of
    their own profile with no way back from the UI.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    ct = capture_create_time(proc.pid)
    proc.wait()  # reaped: the pid is released, not a zombie
    rec = _record(pid=proc.pid, create_time=ct)
    assert liveness_of(rec) is Liveness.GONE


def test_pid_reuse_is_detected_by_create_time():
    """Same pid, different process → GONE.

    Simulated by recording OUR pid with a create time from far in the past:
    the pid resolves, but the process wearing it is demonstrably not the one
    the record describes. Without the create-time discriminator this reads
    ALIVE and blocks a launch on behalf of an unrelated process.
    """
    rec = _record(create_time=1.0)
    assert liveness_of(rec) is Liveness.GONE


def test_missing_create_time_is_unknown_not_alive():
    """No reuse discriminator → UNKNOWN, which callers treat as "allow".

    A record whose create time could not be captured can see *a* process on the
    pid but cannot show it is *ours*. Answering ALIVE here would refuse a
    launch on no evidence — the fail-closed direction the ticket forbids.
    """
    assert liveness_of(_record(create_time=None)) is Liveness.UNKNOWN


def test_liveness_is_unknown_when_psutil_is_unavailable(monkeypatch):
    """No psutil → UNKNOWN, so the guard STOPS BEING ABLE TO REFUSE.

    An install that lost a declared dependency must not thereby start locking
    users out. This is the mirror of the trap process_group.py records, where a
    psutil-less container measured a teardown as clean because the measuring
    code quietly answered "nothing there".
    """
    import builtins

    real_import = builtins.__import__

    def no_psutil(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_psutil)
    assert liveness_of(_record()) is Liveness.UNKNOWN


# --------------------------------------------------------------------------
# The file — survives a restart, and degrades toward allowing launches
# --------------------------------------------------------------------------


def test_record_survives_a_new_registry_object(tmp_path):
    """THE DEFECT, INVERTED: a fresh registry object — standing in for a fresh
    persona process — still sees the session the previous one recorded."""
    path = str(tmp_path / "sessions.json")
    SessionRegistry(path).record(_record(profile="alpha"))

    reloaded = SessionRegistry(path).load()

    assert [r.profile for r in reloaded] == ["alpha"]


def test_forget_removes_only_the_named_profile(tmp_path):
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(_record(profile="a"))
    reg.record(_record(profile="b"))

    reg.forget("a")

    assert sorted(r.profile for r in reg.load()) == ["b"]


def test_forget_all_empties_the_file(tmp_path):
    """What makes "a record was on disk at startup" mean "we did not exit
    cleanly" — the entire survivor signal rests on this."""
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(_record(profile="a"))

    reg.forget_all()

    assert reg.load() == []


def test_forget_is_idempotent_for_an_unknown_profile(tmp_path):
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.forget("never-recorded")  # must not raise
    assert reg.load() == []


def test_a_corrupt_registry_reads_as_empty(tmp_path):
    """An unreadable registry must refuse NOTHING.

    Failing open is the deliberate behaviour: a corrupt file cannot justify
    locking a user out of a profile. Losing the file costs a missed refusal;
    honouring garbage could cost the profile entirely.
    """
    path = tmp_path / "s.json"
    path.write_text("{ this is not json", encoding="utf-8")

    assert SessionRegistry(str(path)).load() == []


def test_an_unknown_version_reads_as_empty(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps({"version": 999, "sessions": [{"profile": "a"}]}),
        encoding="utf-8",
    )

    assert SessionRegistry(str(path)).load() == []


def test_one_corrupt_entry_does_not_discard_the_others(tmp_path):
    """A single bad record costs one refusal, not the whole registry."""
    path = tmp_path / "s.json"
    good = _record(profile="good").to_json()
    path.write_text(
        json.dumps({"version": 1, "sessions": [{"profile": None}, good]}),
        encoding="utf-8",
    )

    assert [r.profile for r in SessionRegistry(str(path)).load()] == ["good"]


def test_a_write_failure_does_not_raise_at_the_call_site(tmp_path, monkeypatch):
    """A registry that cannot be written must cost the GUARD, never the launch.

    This runs inside start_thread's outer handler, which converts any raise
    into "the launch failed" while the browser is already spawned and
    registered — so a raise here would report a failure over a live browser.
    """
    import src.services.browser.session_registry as mod

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "atomic_write_json", boom)
    SessionRegistry(str(tmp_path / "s.json")).record(_record())  # must not raise


# --------------------------------------------------------------------------
# live_records — the split that licenses (or forbids) a refusal
# --------------------------------------------------------------------------


def test_live_records_splits_alive_from_indeterminate(tmp_path):
    """``alive`` may refuse a launch; ``indeterminate`` may not.

    They are returned separately rather than summed precisely because they
    license different actions.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(_record(profile="live"))
    reg.record(_record(profile="cannot-tell", create_time=None))

    alive, unknown = reg.live_records()

    assert [r.profile for r in alive] == ["live"]
    assert [r.profile for r in unknown] == ["cannot-tell"]


def test_live_records_drops_dead_records_from_the_file(tmp_path):
    """A record that probes GONE is removed, so it stops costing a probe — and,
    more importantly, stops being a thing that could ever refuse a launch."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    ct = capture_create_time(proc.pid)
    proc.wait()

    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(_record(profile="ghost", pid=proc.pid, create_time=ct))
    reg.record(_record(profile="live"))

    alive, unknown = reg.live_records()

    assert [r.profile for r in alive] == ["live"]
    assert unknown == []
    assert sorted(r.profile for r in reg.load()) == ["live"]


# --------------------------------------------------------------------------
# make_record
# --------------------------------------------------------------------------


def test_make_record_captures_the_pid_and_a_create_time():
    """The create time is captured AT REGISTRATION, while the handle is live.

    Capturing it later, from the record, would compare the process against
    itself and could never detect reuse.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        rec = make_record("p", proc, "chromium")
        assert rec.pid == proc.pid
        assert rec.create_time is not None
        assert rec.engine == "chromium"
        assert liveness_of(rec) is Liveness.ALIVE
    finally:
        proc.kill()
        proc.wait()


def test_records_round_trip_through_json(tmp_path):
    rec = _record(profile="rt", pgid=1234, engine="firefox")
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(rec)

    (back,) = reg.load()

    assert back.profile == "rt"
    assert back.pid == rec.pid
    assert back.pgid == 1234
    assert back.engine == "firefox"
    assert back.create_time == pytest.approx(rec.create_time)


# --------------------------------------------------------------------------
# PS-353 — a handle the persistence format CANNOT represent.
#
# `InvisibleProcess` runs the Firefox session on a THREAD of persona wherever
# `needs_fork_launch()` is false (a WINDOWS or macOS host), so the handle
# honestly reports `pid = 0`. `make_record` used to store that verbatim and
# `from_json`'s `pid <= 0` guard dropped it on the way back in — a write that
# was a no-op, with no path on which the writer could report it.
# --------------------------------------------------------------------------


class _ThreadArmHandle:
    """The shape `InvisibleProcess` presents on its non-fork arm: no pid."""

    pid = 0


def test_make_record_refuses_a_handle_with_no_probeable_pid():
    """The seam, closed at the writer.

    `None` is the honest answer for a handle whose identity cannot be written
    down. Returning a record would produce a row the reader discards — and, at
    the next `record()` of any profile, ERASES — so the caller could never learn
    its write had not happened.
    """
    from src.services.browser.session_registry import make_record

    assert make_record("ff-thread", _ThreadArmHandle(), "firefox") is None


def test_make_record_still_builds_a_record_for_a_real_handle():
    """THE CONTROL. Without it the test above reads as "make_record returns
    None", rather than locating the refusal at the one shape that has no pid."""
    from src.services.browser.session_registry import make_record

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        rec = make_record("cr-fork", proc, "chromium")
        assert rec is not None and rec.pid == proc.pid
    finally:
        proc.kill()
        proc.wait()


def test_recording_an_unprobeable_row_is_refused_rather_than_lost(tmp_path):
    """A SILENT NO-OP IS WORSE THAN A REFUSAL, and this is why.

    Such a row does not merely fail to load: `record()` rebuilds the file from
    `_load_locked()`, which applies the `pid <= 0` guard on the way in, so the
    NEXT record() of ANY profile physically rewrites the file without it. A
    post-mortem reader of the registry would not even find the entry that
    explains the missing guard.

    So the write is refused and says so — and the control proves the file, the
    writer and the reader all work.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))
    subject = _record(profile="ff-thread", pid=0, create_time=None,
                      engine="firefox")
    control = _record(profile="cr-fork", engine="chromium")

    assert reg.record(subject) is False, "an unwritable row must be reported"
    assert reg.record(control) is True

    assert [r.profile for r in reg.load()] == ["cr-fork"], (
        "the control must survive in the same file, or this test says nothing "
        "about WHERE the loss happens"
    )
    assert "ff-thread" not in (tmp_path / "s.json").read_text(encoding="utf-8"), (
        "the refused row must never reach the file at all"
    )


def test_make_record_for_pid_records_a_pid_the_handle_never_carried(tmp_path):
    """THE REPAIR PATH's own unit.

    A thread-arm session has no pid on its handle but its browser IS a real set
    of OS processes, which the engine's close-watch resolves and announces. This
    is what turns one of those pids into a record a probe can ask about.

    `pgid` is None by construction here and that is correct, not an omission:
    `_child` calls `start_own_session()` only on the FORK arm, so the thread
    arm's engine sits in PERSONA'S OWN group — recording that number would aim a
    later group teardown at persona itself.
    """
    from src.services.browser.session_registry import make_record_for_pid

    rec = make_record_for_pid("ff-thread", os.getpid(), "firefox")

    assert rec is not None
    assert rec.pid == os.getpid()
    assert rec.pgid is None
    assert rec.engine == "firefox"
    assert liveness_of(rec) is Liveness.ALIVE, (
        "a record built this way must be probeable — that is its whole point"
    )
    assert make_record_for_pid("ff-thread", 0, "firefox") is None


def test_record_reports_a_write_that_did_not_happen(tmp_path):
    """A WRITE THE FILESYSTEM REFUSED IS A NO-OP, AND `record()` MUST SAY SO.

    This is the OTHER way a row fails to stick, and it is the one the pid
    guard above cannot see: the record is perfectly representable, the guard
    passes, and `_save_locked` then cannot write the file at all.

    ⛔ THE FAILURE IS STILL SWALLOWED, AND THAT IS DELIBERATE — an unwritable
    registry must never cost the user a session, which is this module's whole
    fail-open direction. What must NOT also be swallowed is the FACT. Before
    PS-353's audit, `record()` returned True here regardless, so
    `launcher.py`'s monitor logged "the restart guard now covers this session"
    on the line after the registry warned that it would not — a writer whose
    success signal could not express its own no-op, which is the very defect
    class this ticket was filed on, reproduced in the API added to fix it.

    ⭐ THE CONTROL IS LOAD-BEARING. Without it a False here would be satisfied
    by a `record()` that had simply stopped working; with it, the refusal is
    located precisely at the unwritable path, and the same record on a
    writable one is kept and reloads.
    """
    blocker = tmp_path / "notadir"
    blocker.write_text("I am a file, so nothing can live underneath me")

    unwritable = SessionRegistry(str(blocker / "s.json"))
    subject = _record(profile="ff-thread", engine="firefox")

    assert unwritable.record(subject) is False, (
        "record() answered True for a write _save_locked could not perform — "
        "a caller gating a 'the guard now covers this session' claim on this "
        "value would announce a guard that does not exist"
    )
    assert unwritable.load() == [], "nothing can have been written"

    writable = SessionRegistry(str(tmp_path / "s.json"))
    assert writable.record(subject) is True, (
        "the control must be kept, or this test says nothing about WHERE the "
        "refusal comes from"
    )
    assert [r.profile for r in writable.load()] == ["ff-thread"]


def test_a_registry_that_cannot_be_written_still_refuses_no_launch(tmp_path):
    """FAIL-OPEN SURVIVES THE NEW RETURN VALUE.

    Reporting the no-op must not become ENFORCING it. An unwritable registry
    is the least informed state there is, and this module's header is explicit
    that a false "already running" is worse than the double launch it set out
    to prevent — so a False from `record()` is a statement about the GUARD,
    never a veto over the session.
    """
    blocker = tmp_path / "notadir"
    blocker.write_text("x")
    reg = SessionRegistry(str(blocker / "s.json"))

    reg.record(_record(profile="ff-thread", engine="firefox"))

    assert reg.load() == []
    assert reg.live_records() == ([], []), (
        "an unwritable registry must yield no ALIVE record, so nothing it "
        "holds can justify refusing a launch"
    )
