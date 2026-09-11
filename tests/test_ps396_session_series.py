"""PS-396 — the session records its own cpu/ctxt series, and NOTHING READS IT.

The properties pinned here are the ones that can rot silently. In particular
AC #3: the absence of a consumer is asserted, because "nothing acts on this"
is a claim that decays the moment somebody helpfully adds a threshold, and
nothing else in the tree would notice.

The LIVE two-armed falsification (AC #2) is not here — it needs a real browser
tree and a SIGSTOP, so it lives in `readings/ps396-2026-09-11/` with its
observed numbers. What IS here is every property a unit test can hold, plus
the specific lies this module exists to refuse.
"""

import json
import os
import threading

import pytest

from src.services.browser import session_series as ss


# ───────────────────────── AC #3 — NOTHING ACTS ─────────────────────────


def test_nothing_in_the_tree_reads_the_series():
    """⛔ THE HARD BOUNDARY, ASSERTED RATHER THAN PROMISED (AC #3).

    PS-349 Recommendation 1 forbids shipping anything that ACTS on this
    signal, and the reason is measured: on its own table a HEALTHY arm
    (`busymax`, cpu med 103.9, ctxt min 9) reads BELOW a WEDGED one
    (`jugwedge`, ctxt min 18) on the discriminating axis. A threshold written
    today is a guess with a number on it, and a killer acting on it would
    terminate a live session with the operator's account sessions open.

    So this asserts the shape of the absence: exactly ONE site in src/ may
    mention this module (the launcher, which STARTS it), and no site anywhere
    in src/ may READ the file back. A future reader that opens the series is
    the first step toward a verdict, and it fails here.
    """
    root = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src")
    mentions = []
    readers = []
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(dirpath, fname)
            if os.path.basename(path) == "session_series.py":
                continue
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            if "session_series" in body:
                mentions.append(os.path.relpath(path, root))
            # A READER would have to name the file or the path helper.
            if ss.SERIES_FILENAME in body or "series_path" in body:
                readers.append(os.path.relpath(path, root))

    assert mentions == [os.path.join("services", "browser", "launcher.py")], (
        "session_series is referenced from somewhere other than the launcher "
        f"that starts it: {mentions}. PS-396 ships a RECORDING, not a "
        "detection — nothing may read this series to decide anything. If a "
        "consumer is genuinely wanted, it needs the calibration PS-349 "
        "Recommendation 4 says does not exist yet (real pages, real "
        "hardware), not a constant."
    )
    assert readers == [], (
        f"something in src/ now opens the series file: {readers}. That is the "
        "first step toward a verdict on a signal whose healthy and wedged "
        "populations OVERLAP (PROBE.md:228-239). Read the boundary in "
        "session_series.py's module docstring before removing this."
    )


def test_the_module_contains_no_threshold_and_no_termination_path():
    """No verdict, no killer, no session handle in the recorder itself (AC #3).

    ⚠️ ASSERTED AGAINST THE PARSED TREE, NOT THE TEXT, and that is not
    fussiness — a text grep fails on this module's own honest prose. The
    docstring quotes `terminate(proc, ...)` when explaining why the launcher
    must guard the sampler's start separately, and `series_capability`'s `why`
    string NAMES psutil in order to explain why psutil is not used. Both
    sentences are the boundary being stated; a grep that fails on them invites
    the "fix" of deleting the explanation. So: imports and call targets, which
    is what the module DOING any of this would look like.
    """
    import ast

    path = os.path.abspath(ss.__file__)
    tree = ast.parse(open(path, encoding="utf-8").read())

    imported: "set[str]" = set()
    called: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Call):
            called.add(ast.unparse(node.func))

    assert "psutil" not in imported, (
        "the recorder imports psutil. Its Windows and macOS arms return a "
        "hard-coded 0 for the nonvoluntary ctxt leg, which is the exact lie "
        "AC #4 forbids; see series_capability()."
    )
    for forbidden in ("terminate", "os.kill", "signal.SIGKILL", "proc.kill",
                      "proc.terminate", "spawn_browser"):
        assert not any(c == forbidden or c.endswith("." + forbidden)
                       for c in called), (
            f"the recorder now CALLS {forbidden!r}. It holds no session handle "
            "and must never acquire one: its entire safety argument is that "
            "there is nothing it CAN do to the session it observes."
        )
    # The control: the walk actually found the module's real calls, so the
    # absences above mean something. An empty `called` would pass every
    # assertion here — the instrument-produced green PS-299 and PS-341 shipped.
    assert "os.listdir" in called and "json.dumps" in called

    # A threshold would need a constant to compare against. The module's only
    # numeric constants are the cadence, the byte cap and the schema — all
    # bounds on the RECORD, none a judgement about the session.
    assert sorted(
        n for n in dir(ss) if n.isupper() and not n.startswith("_")
    ) == ["IS_LINUX", "MAX_BYTES", "PERIOD_S", "SCHEMA", "SERIES_DIRNAME",
          "SERIES_FILENAME"], (
        "a new module-level constant appeared. If it is a threshold the "
        "series is compared against, that is exactly what PS-349 "
        "Recommendation 1 refuses; see this module's docstring."
    )


# ───────────── AC #4 — UNREADABLE IS UNREAD, NEVER A ZERO ─────────────


def _fake_proc(tmp_path, pid, cmdline, *, stat=None, status=None):
    d = tmp_path / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(cmdline.encode() + b"\0")
    if stat is not None:
        (d / "stat").write_text(stat)
    if status is not None:
        (d / "status").write_text(status)
    return d


def _stat_line(utime, stime):
    fields = ["0"] * 40
    fields[11] = str(utime)
    fields[12] = str(stime)
    return "1 (engine (x) ) S " + " ".join(fields) + "\n"


def _status_body(vol, nonvol):
    return (
        f"Name:\tengine\nState:\tS\n"
        f"voluntary_ctxt_switches:\t{vol}\n"
        f"nonvoluntary_ctxt_switches:\t{nonvol}\n"
    )


def test_a_denied_sample_is_null_and_counted_never_zero(tmp_path):
    """⛔ THE SINGLE MOST IMPORTANT PROPERTY IN THE MODULE (AC #4).

    PS-349's venue note records `/proc/<pid>/syscall` and `/proc/<pid>/stack`
    answering `Operation not permitted` at its own privilege level, so "the
    reader is denied" is a state that HAPPENS. A denial recorded as `0` forges
    the `sigstop` wedged signature exactly — cpu 0.0, ctxt 0 — which is the
    reading a diagnostician would act on.

    So a denied read yields `null` WITH a `denied` count, and a reader can
    tell "this session did nothing" from "I was unable to look".
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pdir = "/data/profiles/subject"
    d = _fake_proc(proc_root, 4242, f"engine --user-data-dir={pdir}",
                   stat=_stat_line(10, 5), status=_status_body(7, 3))
    os.chmod(d / "stat", 0o000)

    prev = ss._Readings()
    sample = ss._sample(pdir, prev, 0.0, 100.0, now=1.0, proc_root=str(proc_root))

    assert sample["cpu"] is None, (
        "a denied /proc read produced a cpu NUMBER. null and 0 are different "
        "facts; 0 here is the wedged signature, invented."
    )
    assert sample["ctxt_v"] is None and sample["ctxt_nv"] is None
    assert sample["denied"] == 1, (
        "the denial was not counted, so the null is indistinguishable from "
        "'no processes matched'."
    )
    # And the honest converse: the tree WAS found. A null with nproc 1 says
    # "there is a session here and I could not read it", which is the state.
    assert sample["nproc"] == 1


def test_absent_ctxt_lines_count_as_denied_not_as_zero(tmp_path):
    """A kernel that does not answer is not a kernel answering zero (AC #4).

    This is the same lie arriving by a different door: the file opened fine
    and simply did not carry the lines. Recording 0 for a question nobody
    answered is what psutil's Windows/macOS arms do (`ntp.pctxsw(vol, 0)`),
    and it is why this module does not use them — see `series_capability`.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pdir = "/data/profiles/subject"
    _fake_proc(proc_root, 5151, f"engine --user-data-dir={pdir}",
               stat=_stat_line(10, 5), status="Name:\tengine\nState:\tS\n")

    prev = ss._Readings()
    ss._sample(pdir, prev, 0.0, 100.0, now=1.0, proc_root=str(proc_root))
    second = ss._sample(pdir, prev, 0.0, 100.0, now=2.0, proc_root=str(proc_root))

    assert second["ctxt_v"] is None and second["ctxt_nv"] is None
    assert second["denied"] == 1


def test_the_first_sample_of_a_session_is_null_not_zero(tmp_path):
    """There is no previous counter to subtract from, so there is no reading.

    The cheapest possible demonstration that null and 0 are kept apart: EVERY
    session's first sample lands here by construction. Recording it as 0 would
    open every series with a forged wedged sample.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pdir = "/data/profiles/subject"
    _fake_proc(proc_root, 6161, f"engine --user-data-dir={pdir}",
               stat=_stat_line(10, 5), status=_status_body(7, 3))

    first = ss._sample(ss and pdir, ss._Readings(), 0.0, 100.0, now=1.0,
                       proc_root=str(proc_root))
    assert first["cpu"] is None and first["ctxt_v"] is None
    assert first["denied"] == 0 and first["nproc"] == 1, (
        "the first sample must be null because nothing was SUBTRACTABLE, not "
        "because anything was denied — the two reasons for a null are "
        "distinguished by `denied`."
    )


def test_a_real_delta_is_a_number_so_the_null_means_something(tmp_path):
    """THE CONTROL FOR THE THREE TESTS ABOVE.

    ⚠️ Without this, a sampler that returned null unconditionally would pass
    every assertion above — the exact instrument-produced false green PS-299
    and PS-341 shipped. This fires: readable counters produce real numbers.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pdir = "/data/profiles/subject"
    d = _fake_proc(proc_root, 7171, f"engine --user-data-dir={pdir}",
                   stat=_stat_line(100, 0), status=_status_body(10, 2))

    prev = ss._Readings()
    ss._sample(pdir, prev, 0.0, 100.0, now=1.0, proc_root=str(proc_root))
    (d / "stat").write_text(_stat_line(150, 0))       # +50 ticks
    (d / "status").write_text(_status_body(40, 9))    # +30 vol, +7 nonvol
    second = ss._sample(pdir, prev, 0.0, 100.0, now=2.0, proc_root=str(proc_root))

    # 50 ticks / 100 Hz / 1.0 s = 0.5 core = 50%.
    assert second["cpu"] == pytest.approx(50.0)
    assert second["ctxt_v"] == 30
    assert second["ctxt_nv"] == 7
    assert second["denied"] == 0


def test_cpu_is_an_instantaneous_delta_not_a_lifetime_average(tmp_path):
    """⚠️ THE ONE DISTINCTION THE WHOLE RECORD TURNS ON.

    `ps pcpu` is cpu-since-process-start, which makes a process that spun for
    an hour and then WEDGED read identically to one spinning right now — so a
    lifetime average cannot tell spinning from blocked. Here: a process with
    a huge accumulated total that stops moving reads 0.0 on the NEXT sample,
    not its historical average.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pdir = "/data/profiles/subject"
    d = _fake_proc(proc_root, 8181, f"engine --user-data-dir={pdir}",
                   stat=_stat_line(360000, 0), status=_status_body(1, 1))

    prev = ss._Readings()
    ss._sample(pdir, prev, 0.0, 100.0, now=1.0, proc_root=str(proc_root))
    # Counters do not move: the process is blocked.
    second = ss._sample(pdir, prev, 0.0, 100.0, now=3.0, proc_root=str(proc_root))
    assert second["cpu"] == 0.0, (
        "a process with an hour of accumulated cpu that has stopped moving "
        "read non-zero — that is a lifetime average, and it cannot "
        "distinguish spinning from blocked."
    )
    assert second["cpu_from"] == 1, "and it WAS read — 0.0 here is a reading"
    d.name  # keep the fixture referenced


# ───────────── the matcher doctrine, inherited from PS-171/PS-349 ─────────────


def test_the_matcher_reads_cmdline_not_comm(tmp_path):
    """PS-171 arm H: `comm` found 1 process where the path matcher found 11.

    `comm` is capped at 15 characters and Firefox's children are named
    `Web Content` / `Socket Process` / `RDD Process`, none containing
    "firefox". A matcher that sees one process of an eleven-process tree
    reports a busy session as idle — which is a false reading of exactly the
    kind this record exists to make impossible.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pdir = "/data/profiles/subject"
    # A child whose comm says nothing, whose cmdline says everything.
    _fake_proc(proc_root, 111, f"/engine/firefox --profile {pdir}/.inv",
               stat=_stat_line(1, 1), status=_status_body(1, 1))
    _fake_proc(proc_root, 222, f"Web Content -childID 3 -parentBuildID {pdir}",
               stat=_stat_line(1, 1), status=_status_body(1, 1))
    # And a process of ANOTHER profile, which must not be counted.
    _fake_proc(proc_root, 333, "/engine/firefox --profile /data/profiles/other",
               stat=_stat_line(1, 1), status=_status_body(1, 1))

    assert ss.engine_pids_for(pdir, proc_root=str(proc_root)) == [111, 222]


def test_the_matcher_never_returns_the_observer(tmp_path):
    """⛔ THIS FIRED FOR REAL while building PS-396's own falsification arm.

    The harness took the profile dir as `argv[1]`, so its cmdline contained
    the path, so the matcher returned the OBSERVER as a member of the tree it
    was observing — and the SIGSTOP arm then froze the sampler along with the
    engine. That is PS-185's `pkill -f` lesson one level down; for a recorder
    the damage is quieter than a kill and no less wrong (persona's own cpu
    folded into the engine's series).
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    pdir = "/data/profiles/subject"
    # OUR pid, carrying the path — exactly the harness's shape.
    _fake_proc(proc_root, os.getpid(), f"python observer.py {pdir}",
               stat=_stat_line(1, 1), status=_status_body(1, 1))
    _fake_proc(proc_root, 999999, f"engine --user-data-dir={pdir}",
               stat=_stat_line(1, 1), status=_status_body(1, 1))

    assert ss.engine_pids_for(pdir, proc_root=str(proc_root)) == [999999]


# ───────── AC #9 / Correction 2 — the per-platform capability statement ─────────


def test_capability_is_stated_per_platform_and_never_claims_psutils_zero():
    """AC #9 and Correction 2: the narrowing is NAMED, not papered over.

    On Linux every leg is `measured`. Off Linux nothing is recorded at all —
    and specifically the NONVOLUNTARY leg says why psutil does not widen this:
    its Windows and macOS arms return a hard-coded 0 (`ntp.pctxsw(vol, 0)`),
    an unmeasured value shaped like a reading, which would forge the sigstop
    signature on the platforms where it is least checkable.
    """
    cap = ss.series_capability()
    assert set(cap) == {"platform", "source", "recorded", "cpu",
                        "ctxt_voluntary", "ctxt_nonvoluntary", "why"}
    if ss.IS_LINUX:
        assert cap["recorded"] is True
        assert cap["source"] == "/proc"
        assert cap["cpu"] == cap["ctxt_voluntary"] == "measured"
        assert cap["ctxt_nonvoluntary"] == "measured"
    else:
        assert cap["recorded"] is False
        assert cap["ctxt_nonvoluntary"] == "unmeasured", (
            "a non-Linux platform is claiming to measure the nonvoluntary "
            "leg. Read psutil's _pswindows.py / _psosx.py before believing "
            "it: both return a literal 0."
        )
    # The reasoning is IN the payload, because the payload is what a reader of
    # a series file on a strange platform will actually have.
    assert cap["why"]


def test_a_platform_that_cannot_measure_writes_no_file_at_all(tmp_path,
                                                              monkeypatch):
    """Not an EMPTY file, which would read as a session that produced nothing.

    "This platform does not record" and "this session produced no samples"
    are different facts, and only one of them is true off Linux.
    """
    monkeypatch.setattr(ss, "series_capability", lambda: {
        "platform": "windows", "source": None, "recorded": False,
        "cpu": "unmeasured", "ctxt_voluntary": "unmeasured",
        "ctxt_nonvoluntary": "unmeasured", "why": "no /proc",
    })
    rec = ss.SessionSeriesRecorder(str(tmp_path))
    stop = threading.Event()
    stop.set()
    rec.run(stop)
    assert not os.path.exists(rec.path), (
        "a platform that records nothing created a file anyway; an empty "
        "series reads as 'this session did nothing'."
    )


# ───────────── AC #6 — destination, key, and the PS-330 convention ─────────────


def test_the_series_lives_inside_the_profile_dir_so_the_wipe_reaches_it():
    """AC #6, the destination decision, pinned.

    It lives under `DATA_DIR/<profile>/.persona-session-series/`, which
    `delete_profile` MOVES to the trash with the profile and
    `wipe_all_profiles` rmtree's outright. `LOG_DIR` was rejected for a
    SECURITY reason: the panic wipe reaches that directory only through
    `_clear_logs_for_wipe`, whose glob is `persona_*.log` and nothing else, so
    a series file there would SURVIVE the wipe carrying the operator's profile
    names while the wipe reported success.
    """
    assert ss.series_path("/data/profiles/subject") == os.path.join(
        "/data/profiles/subject", ".persona-session-series", "series.jsonl"
    )
    # A dot-prefixed sibling of the launch's own `.persona-*-ext` dirs, so it
    # inherits their containment rather than inventing a second cleanup site.
    assert ss.SERIES_DIRNAME.startswith(".persona-")


def test_wipe_all_profiles_destroys_the_series(tmp_path, monkeypatch):
    """AC #6's assertion, run against the REAL wipe rather than reasoned about.

    ⚠️ The claim "the profile dir is covered for free" is exactly the kind
    that is true until somebody changes the wipe. This pins it.
    """
    from src.services.profile.manager import ProfileManager

    data_dir = tmp_path / "persona_data"
    data_dir.mkdir()
    monkeypatch.setattr("src.services.profile.manager.DATA_DIR", str(data_dir))
    monkeypatch.setattr("src.core.config.DATA_DIR", str(data_dir))
    monkeypatch.setattr("src.services.profile.manager.PROFILES_FILE",
                        str(tmp_path / "profiles.json"))

    mgr = ProfileManager()
    mgr.add_profile("subject", None, "windows")
    pdir = data_dir / "subject"
    pdir.mkdir(exist_ok=True)
    series = pdir / ss.SERIES_DIRNAME / ss.SERIES_FILENAME
    series.parent.mkdir(parents=True, exist_ok=True)
    series.write_text('{"t":1.0,"cpu":12.5}\n')
    assert series.exists()

    mgr.wipe_all_profiles()

    assert not series.exists(), (
        "the panic wipe left the session series on disk. The whole reason "
        "this record lives in the profile's own dir rather than LOG_DIR is "
        "that the wipe reaches it; if that stopped being true the destination "
        "decision (session_series.py, decision 2) has to be re-made."
    )
    assert not series.parent.exists()


def test_no_profile_name_is_written_into_the_record(tmp_path):
    """PS-330's sharing convention, answered in code (AC #6).

    *"Labels are user-identifying and are deliberately NOT recorded into a
    file the operator may share."* A series keyed by profile NAME is the
    operator's private label, so the profile is identified by WHERE the file
    is — a fact the filesystem already holds and the wipe already destroys.
    A record lifted out of its directory names nobody.

    Pinned at the CONSTRUCTOR: the recorder is handed a directory and never a
    Profile, so it cannot write a name even by accident.
    """
    pdir = tmp_path / "a-very-private-profile-label"
    pdir.mkdir()
    rec = ss.SessionSeriesRecorder(str(pdir), period_s=0.01)
    stop = threading.Event()

    t = threading.Thread(target=rec.run, args=(stop,), daemon=True)
    t.start()
    import time as _t
    _t.sleep(0.2)
    stop.set()
    t.join(5)

    body = open(rec.path, encoding="utf-8").read()
    assert "a-very-private-profile-label" not in body, (
        "the profile's name reached the record's CONTENT. It is the "
        "operator's private label (PS-330); the path carries the identity."
    )
    # The control: the file is not empty, so the absence above means something.
    assert json.loads(body.splitlines()[0])["meta"] == "header"


# ─────────────────────── AC #7 — the lifecycle is bounded ───────────────────────


def test_each_session_truncates_the_previous_series(tmp_path):
    """AC #7: per-session truncation, so exactly the LAST session exists.

    A retained history of every session is a dated record of the operator's
    browsing cadence sitting on their disk, which buys a diagnostician nothing
    — they look at the session that stalled.
    """
    pdir = tmp_path / "p"
    pdir.mkdir()
    rec = ss.SessionSeriesRecorder(str(pdir), period_s=0.01)
    os.makedirs(os.path.dirname(rec.path), exist_ok=True)
    with open(rec.path, "w") as fh:
        fh.write("PREVIOUS SESSION\n" * 100)

    stop = threading.Event()
    t = threading.Thread(target=rec.run, args=(stop,), daemon=True)
    t.start()
    import time as _t
    _t.sleep(0.15)
    stop.set()
    t.join(5)

    assert "PREVIOUS SESSION" not in open(rec.path).read()


def test_the_cap_stops_the_record_and_says_that_it_did(tmp_path):
    """AC #7's second bound, for the session that never ends.

    And it SAYS it capped: a file that merely stops has no way to tell a
    reader whether the session ended or the record did. It does not wrap,
    because a file missing its beginning reads as a session that started later
    than it did.
    """
    pdir = tmp_path / "p"
    pdir.mkdir()
    rec = ss.SessionSeriesRecorder(str(pdir), period_s=0.01, max_bytes=900)
    stop = threading.Event()
    t = threading.Thread(target=rec.run, args=(stop,), daemon=True)
    t.start()
    import time as _t
    _t.sleep(0.6)
    stop.set()
    t.join(5)

    rows = [json.loads(ln) for ln in open(rec.path) if ln.strip()]
    assert rows[-1].get("meta") == "capped", (
        "the record stopped without saying so; a reader cannot tell that from "
        "a session that ended."
    )
    assert os.path.getsize(rec.path) < 900 * 2


# ───────── AC #5 — the failure is contained and costs the series only ─────────


def test_a_recorder_that_cannot_start_returns_none_and_does_not_raise(
        monkeypatch, tmp_path):
    """⛔ AC #5, and the reason the launcher guards this SEPARATELY.

    `BrowserLauncher.start_thread` starts its monitor and wait threads inside
    one try whose except calls `terminate(proc, ...)` and unregisters the
    session — correct for those two (nobody would drain the engine's stdout or
    reap it) and CATASTROPHIC here. A thread-table exhaustion that stopped a
    RECORDING thread must not tear down a live browser with the operator's
    account sessions open. So this swallows and returns None.
    """
    def boom(*a, **k):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading, "Thread", boom)
    assert ss.start_recording(str(tmp_path), threading.Event()) is None


def test_a_raising_sampler_does_not_escape_run(tmp_path, monkeypatch):
    """The recorder's own failure is contained, and it SAYS so in the file.

    A skipped sample recorded as nothing leaves a gap indistinguishable from a
    session that was quiet for that interval.
    """
    pdir = tmp_path / "p"
    pdir.mkdir()
    calls = []

    def angry(*a, **k):
        calls.append(1)
        raise OSError("proc went away")

    monkeypatch.setattr(ss, "_sample", angry)
    rec = ss.SessionSeriesRecorder(str(pdir), period_s=0.01)
    stop = threading.Event()
    t = threading.Thread(target=rec.run, args=(stop,), daemon=True)
    t.start()
    import time as _t
    _t.sleep(0.15)
    stop.set()
    t.join(5)

    assert calls, "the sampler was never called, so nothing was proven"
    rows = [json.loads(ln) for ln in open(rec.path) if ln.strip()]
    assert any(r.get("meta") == "sample-error" for r in rows), (
        "a failed sample left no trace; the gap is indistinguishable from a "
        "quiet interval."
    )


def test_the_launcher_starts_the_recorder_outside_the_teardown_arm():
    """AC #5, pinned at the CALL SITE rather than only at the callee.

    The property that matters is structural: the `start_recording` call must
    NOT sit inside the `try` whose `except` calls `terminate(proc, ...)`. This
    reads the source and asserts the ordering, because a later edit moving the
    call three lines up would silently make a recording failure kill sessions.
    """
    from src.services.browser import launcher as launcher_mod

    src = open(os.path.abspath(launcher_mod.__file__), encoding="utf-8").read()
    start = src.index("start_recording(")
    # The teardown arm is the one that terminates a REGISTERED session because
    # a monitor thread could not start.
    arm = src.index("failed to start (rare: thread-table exhaustion)")
    tail = src.index("raise", arm)
    assert start > tail, (
        "start_recording moved INSIDE the monitor/wait thread-start guard. "
        "That arm calls terminate(proc, ...) and unregisters the session; a "
        "RECORDING thread that cannot start must cost the series and never "
        "the session (PS-396 AC #5)."
    )


def test_the_recorder_is_handed_the_session_stop_event(tmp_path):
    """It ends WITH the session and holds nothing that could keep it alive."""
    pdir = tmp_path / "p"
    pdir.mkdir()
    stop = threading.Event()
    t = ss.start_recording(str(pdir), stop)
    assert t is not None and t.daemon
    stop.set()
    t.join(5)
    assert not t.is_alive(), (
        "the recorder outlived its session's stop event; a daemon thread that "
        "ignores the notifier is a thread nothing can end."
    )
