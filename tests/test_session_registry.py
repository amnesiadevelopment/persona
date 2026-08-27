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
    path.write_text("{ this is not json")

    assert SessionRegistry(str(path)).load() == []


def test_an_unknown_version_reads_as_empty(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"version": 999, "sessions": [{"profile": "a"}]}))

    assert SessionRegistry(str(path)).load() == []


def test_one_corrupt_entry_does_not_discard_the_others(tmp_path):
    """A single bad record costs one refusal, not the whole registry."""
    path = tmp_path / "s.json"
    good = _record(profile="good").to_json()
    path.write_text(
        json.dumps({"version": 1, "sessions": [{"profile": None}, good]})
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
