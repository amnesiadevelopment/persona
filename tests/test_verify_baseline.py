"""Tests for src/services/verify/baseline — the engine-continuity check.

NO BROWSER HERE, and there must not be one: ``playwright`` is a git-pinned
dependency that is not importable in this container. Every test below drives
the comparison with snapshot dicts and injects a fake recorder, exactly as
``test_verify_snapshot.py`` drives the runner with a fake ``evaluate``.

What these tests pin is the *verdict* logic — that a pass means "every probe
was read AND nothing moved", and specifically that the three ways a comparison
can be worthless (drift, a vanished probe, an unread probe) each produce a
failure rather than a quiet green. The end-to-end behaviour against a real
launched browser cannot run here; it is recorded in
``tests/fixtures/engine-fingerprint-baseline.md``.
"""

import json
import os
import time

import pytest

from src.services.verify import baseline

# --- helpers ----------------------------------------------------------------


def _snap(window=None, worker=None, *, build="firefox-20"):
    """A minimal snapshot document shaped like the real one."""
    return {
        "schema_version": 1,
        "engine": "firefox",
        "engine_build": build,
        "profile": baseline.BASELINE_PROFILE_NAME,
        "app_version": "9.9.9",
        "realms": ["window", "worker"],
        "probes": {
            "window": dict(window or {"navigator.userAgent": {"value": "FF"}}),
            "worker": dict(worker or {"navigator.userAgent": {"value": "FF"}}),
        },
    }


# --- the pinned profile -----------------------------------------------------


def test_the_baseline_profile_pins_every_input_that_moves_the_identity():
    p = baseline.baseline_profile()
    assert p.name == baseline.BASELINE_PROFILE_NAME
    assert p.os_type == "windows"
    assert p.device_type == "desktop"
    assert p.engine == "firefox"
    # Explicit geometry, never "auto" — "auto" resolves through a seed-indexed
    # preset table and leaves the recorded geometry implicit.
    assert p.resolution == "1920x1080"
    # No proxy: a proxy makes locale/timezone follow a geo lookup, and variance
    # the NETWORK introduced would read exactly like variance the ENGINE did.
    assert p.proxy is None
    assert p.certificate is None
    # [] is "explicitly cleared". None would mean "use the store's defaults",
    # making the reading depend on the operator's bookmark store.
    assert p.bookmarks == []
    assert p.bookmarks is not None


def test_the_seed_is_pinned_by_the_name_and_is_stable():
    # Profile.fingerprint_seed is crc32(name), so pinning the NAME is what pins
    # the whole derived identity. If this value moves, the committed artifact
    # is invalidated — which is precisely why it is asserted here.
    assert baseline.baseline_profile().fingerprint_seed == 1042768975
    assert (
        baseline.baseline_profile().fingerprint_seed
        == baseline.baseline_profile().fingerprint_seed
    )


def test_both_realms_are_recorded_and_neither_is_optional():
    # A spoof that lands on the page but not inside a Web Worker is the
    # historically load-bearing leak, and it is invisible unless the worker
    # realm is read.
    assert baseline.BASELINE_REALMS == ("window", "worker")


def test_provenance_records_how_the_artifact_was_produced():
    prov = baseline.provenance(baseline.baseline_profile())
    assert prov["profile_name"] == baseline.BASELINE_PROFILE_NAME
    assert prov["fingerprint_seed"] == 1042768975
    assert prov["proxy"] == "none"
    assert prov["resolution"] == "1920x1080"
    assert prov["realms"] == ["window", "worker"]


def test_provenance_names_the_host_dependent_probes():
    """Pinning the profile pins everything the SEED derives, but not what the
    GPU/driver and installed fonts report. Whoever reads a red diff needs to
    know which lines can move between two machines on the SAME engine, and it
    belongs in the artifact rather than only in the accompanying note."""
    prov = baseline.provenance(baseline.baseline_profile())
    assert "webgl.unmasked" in prov["env_sensitive_probes"]
    assert "fonts.measureText" in prov["env_sensitive_probes"]


def test_the_env_sensitivity_marker_carries_no_host_identifier():
    """Deliberately NAMES only. Recording an actual GPU/driver string would
    make the artifact's bytes depend on the machine that recorded it — the
    opposite of what a byte-stable reference needs."""
    prov = baseline.provenance(baseline.baseline_profile())
    # Every entry is a probe id that the snapshot actually uses...
    assert all(isinstance(p, str) for p in prov["env_sensitive_probes"])
    # ...and the block is identical on every machine, because it is a constant.
    assert prov["env_sensitive_probes"] == list(baseline.ENV_SENSITIVE_PROBES)


# --- the verdict ------------------------------------------------------------


def test_identical_snapshots_pass():
    result = baseline.compare(_snap(), _snap())
    assert result.ok
    assert not result.drifted
    assert "PASS" in result.report()


def test_a_changed_probe_fails_and_is_named_with_both_values():
    before = _snap(worker={"navigator.hardwareConcurrency": {"value": 12}})
    after = _snap(worker={"navigator.hardwareConcurrency": {"value": 8}})

    result = baseline.compare(before, after)

    assert not result.ok
    assert result.drifted
    report = result.report()
    # The operator must be able to decide "benign or leak?" without opening the
    # JSON: which probe, which realm, expected vs observed.
    assert "navigator.hardwareConcurrency" in report
    assert "worker" in report
    assert "12" in report and "8" in report
    assert "FAIL" in report


def test_a_drift_only_inside_the_worker_realm_is_caught():
    # The window realm agrees perfectly; only the worker moved. This is exactly
    # the shape of a page-only spoof that never reached the Worker.
    before = _snap(
        window={"navigator.hardwareConcurrency": {"value": 12}},
        worker={"navigator.hardwareConcurrency": {"value": 12}},
    )
    after = _snap(
        window={"navigator.hardwareConcurrency": {"value": 12}},
        worker={"navigator.hardwareConcurrency": {"value": 8}},
    )

    result = baseline.compare(before, after)

    assert not result.ok
    assert [e["realm"] for e in result.entries] == ["worker"]


def test_a_probe_missing_from_one_side_is_reported_not_skipped():
    before = _snap(worker={"a": {"value": 1}, "b": {"value": 2}})
    after = _snap(worker={"a": {"value": 1}})

    result = baseline.compare(before, after)

    assert not result.ok
    assert [(e["probe_id"], e["status"]) for e in result.entries] == [("b", "removed")]
    assert "b" in result.report()


def test_a_probe_added_on_the_new_side_is_reported():
    before = _snap(worker={"a": {"value": 1}})
    after = _snap(worker={"a": {"value": 1}, "b": {"value": 2}})

    result = baseline.compare(before, after)

    assert not result.ok
    assert [(e["probe_id"], e["status"]) for e in result.entries] == [("b", "added")]


# --- the green-from-two-non-readings trap -----------------------------------


def test_two_identically_failed_readings_do_not_count_as_agreement():
    """The trap this check exists to refuse.

    ``diff_snapshots`` compares entries verbatim, so ``{"error": X}`` on both
    sides compares EQUAL and the raw diff reports agreement. A pass derived from
    two probes that could not be read is not evidence of anything.
    """
    err = {"error": "TypeError: WebGL is not available"}
    before = _snap(worker={"webgl.unmasked": dict(err)})
    after = _snap(worker={"webgl.unmasked": dict(err)})

    result = baseline.compare(before, after)

    # The raw diff genuinely sees no movement...
    assert not result.drifted
    # ...but the verdict is still a failure, and it says why.
    assert not result.ok
    assert result.baseline_errors == 1
    assert result.observed_errors == 1
    assert "INCONCLUSIVE" in result.report()
    assert "FAIL" in result.report()


def test_an_unread_probe_is_never_announced_as_drift():
    """DRIFT is the alarm signal, so it must rest on an observation.

    ``diff_snapshots`` reports a both-sides-failed probe as an INCONCLUSIVE
    entry (deliberately — dropping it silently would be worse). Counting that
    entry as drift would headline "N probe(s) differ" about a probe nobody
    read: a confident claim derived from a non-reading, which is the drift-axis
    twin of the false green ``ok`` exists to refuse.
    """
    err = {"error": "TypeError: WebGL is not available"}
    result = baseline.compare(
        _snap(worker={"webgl.unmasked": dict(err)}),
        _snap(worker={"webgl.unmasked": dict(err)}),
    )
    report = result.report()

    assert not result.drifted
    assert "DRIFT:" not in report
    # It is still a failure, and the probe is still NAMED — not quietly dropped.
    assert not result.ok
    assert "webgl.unmasked" in report
    assert "FAIL" in report


def test_an_all_inconclusive_comparison_never_claims_the_readings_match():
    """The pass-side half of the same honesty.

    Nothing moved, but nothing was read either. Reporting "every reading
    matches the baseline" would reassure an operator about probes that were
    never obtained.
    """
    err = {"error": "TypeError: WebGL is not available"}
    result = baseline.compare(
        _snap(worker={"webgl.unmasked": dict(err)}),
        _snap(worker={"webgl.unmasked": dict(err)}),
    )
    report = result.report()

    assert "every reading matches" not in report
    assert "INCONCLUSIVE" in report


def test_the_drift_count_excludes_probes_that_were_never_read():
    """A real difference alongside an unread probe must report ONE drift, not
    two: the headline number is what a human acts on, so padding it with a
    non-reading overstates the finding."""
    err = {"error": "TypeError: WebGL is not available"}
    result = baseline.compare(
        _snap(worker={"moved": {"value": 1}, "unread": dict(err)}),
        _snap(worker={"moved": {"value": 2}, "unread": dict(err)}),
    )
    report = result.report()

    assert result.drifted
    assert len(result.entries) == 2
    assert result.inconclusive == 1
    assert "DRIFT: 1 probe(s) differ" in report
    # Both are still listed; only the COUNT discriminates.
    assert "worker/moved" in report
    assert "worker/unread" in report


def test_an_error_on_only_the_observed_side_is_inconclusive():
    before = _snap(worker={"p": {"value": 1}})
    after = _snap(worker={"p": {"error": "TypeError: nope"}})

    result = baseline.compare(before, after)

    assert not result.ok
    assert result.baseline_errors == 0
    assert result.observed_errors == 1


def test_count_errors_counts_across_every_realm():
    snap = _snap(
        window={"a": {"error": "x"}, "b": {"value": 1}},
        worker={"c": {"error": "y"}},
    )
    assert baseline.count_errors(snap) == 2


def test_count_errors_tolerates_a_malformed_document():
    assert baseline.count_errors({}) == 0
    assert baseline.count_errors({"probes": "not-a-dict"}) == 0


# --- provenance is compared on evidence, not on the build string ------------


def test_a_differing_engine_build_alone_is_not_a_failure():
    """An engine BUILD change is the REASON the check is being run, not the
    finding. It is the probe evidence that must not move."""
    before = _snap(build="firefox-20")
    after = _snap(build="firefox-21")

    result = baseline.compare(before, after)

    assert result.ok
    report = result.report()
    # Reported as context, so the operator can see which transition was checked.
    assert "firefox-20" in report and "firefox-21" in report


# --- the launch bound -------------------------------------------------------
#
# No browser here either: _await_started only needs an object with a .stdout
# that behaves like the real pipe, so a real os.pipe stands in for the session.


class _FakePipeProc:
    """A proc whose stdout is a real pipe we control from the test side."""

    def __init__(self):
        r, w = os.pipe()
        self._w = w
        self.stdout = os.fdopen(r, encoding="utf-8")

    def say(self, line: str) -> None:
        os.write(self._w, f"{line}\n".encode())

    def close(self) -> None:
        """Close the write end: the reader sees EOF."""
        try:
            os.close(self._w)
        except OSError:
            pass


def test_a_session_that_never_says_anything_times_out_rather_than_hanging():
    """The case that has teeth, and the one that was untested.

    A session that starts and then goes silent is the WEDGE a launch timeout
    exists to catch. A deadline tested only between reads cannot fire against
    it, because the blocking read never returns to let the loop re-check — so
    every test that drives this with output that arrives proves nothing about
    the bound. This one holds the pipe open and writes nothing.

    A hang here is worse than a failure: record_snapshot's `finally` teardown
    never runs, so the session leaks, and on the in_process path the only stop
    signal is unreachable from inside the blocking read.
    """
    proc = _FakePipeProc()  # opened, never written to, never closed
    started = time.time()

    with pytest.raises(baseline.BaselineUnavailable) as exc:
        baseline._await_started(proc, timeout=0.5)

    elapsed = time.time() - started
    assert "did not start within" in str(exc.value)
    # It must fire ON the bound, not merely eventually.
    assert elapsed < 10, f"the timeout did not bound the wait ({elapsed:.1f}s)"
    proc.close()


def test_a_slow_but_chatty_session_still_starts():
    """The bound must not become trigger-happy: noise before readiness is
    normal, and only the total budget should end the wait."""
    proc = _FakePipeProc()
    proc.say("some startup noise")
    proc.say("more noise")
    proc.say("BROWSER_STARTED")

    baseline._await_started(proc, timeout=10.0)  # returns, does not raise
    proc.close()


def test_a_session_that_ends_before_readiness_is_reported_as_such():
    """EOF is a different failure from a timeout and must not be reported as
    one — the operator needs to know the session DIED, not that it was slow."""
    proc = _FakePipeProc()
    proc.say("starting")
    proc.close()  # EOF without ever reporting readiness

    with pytest.raises(baseline.BaselineUnavailable) as exc:
        baseline._await_started(proc, timeout=10.0)

    assert "ended before it reported readiness" in str(exc.value)


def test_a_session_reporting_closure_fails_immediately_and_names_the_signal():
    proc = _FakePipeProc()
    proc.say("BROWSER_CLOSED")

    with pytest.raises(baseline.BaselineUnavailable) as exc:
        baseline._await_started(proc, timeout=10.0)

    assert "BROWSER_CLOSED" in str(exc.value)
    proc.close()


# --- the command wiring -----------------------------------------------------


def test_check_compares_a_fresh_reading_against_the_committed_baseline(tmp_path):
    artifact = tmp_path / "baseline.json"
    artifact.write_text(json.dumps(_snap()), encoding="utf-8")

    passing = baseline.check(str(artifact), recorder=lambda: _snap())
    assert passing.ok

    drifted = baseline.check(
        str(artifact),
        recorder=lambda: _snap(worker={"navigator.userAgent": {"value": "CHANGED"}}),
    )
    assert not drifted.ok


def test_the_cli_exit_code_is_the_verdict(tmp_path, monkeypatch):
    from src.services.verify import baseline_cli

    artifact = tmp_path / "baseline.json"
    artifact.write_text(json.dumps(_snap()), encoding="utf-8")

    monkeypatch.setattr(baseline, "record_snapshot", lambda **kw: _snap())
    assert baseline_cli.main(["check", "--baseline", str(artifact)]) == 0

    monkeypatch.setattr(
        baseline,
        "record_snapshot",
        lambda **kw: _snap(worker={"navigator.userAgent": {"value": "CHANGED"}}),
    )
    assert baseline_cli.main(["check", "--baseline", str(artifact)]) == 1


def test_the_cli_reports_a_missing_precondition_rather_than_crashing(
    tmp_path, monkeypatch
):
    from src.services.verify import baseline_cli

    artifact = tmp_path / "baseline.json"
    artifact.write_text(json.dumps(_snap()), encoding="utf-8")

    def _no_display(**kwargs):
        raise baseline.BaselineUnavailable("no DISPLAY: ...")

    monkeypatch.setattr(baseline, "record_snapshot", _no_display)
    # Exit 2 = "could not run", distinct from 1 = "ran and found a difference".
    assert baseline_cli.main(["check", "--baseline", str(artifact)]) == 2


def test_a_missing_baseline_exits_2_not_1_so_it_is_not_read_as_drift(
    tmp_path, monkeypatch
):
    """Exit 1 is the DRIFT signal. A baseline that isn't there was never
    compared, so reporting it as 1 would tell a caller the identity moved when
    nothing was read at all — a false red on the most alarming signal we have.
    """
    from src.services.verify import baseline_cli

    # The recorder must NOT be what fails: this is about the reference side.
    monkeypatch.setattr(baseline, "record_snapshot", lambda **kw: _snap())

    missing = tmp_path / "definitely-not-here.json"
    assert not missing.exists()

    assert baseline_cli.main(["check", "--baseline", str(missing)]) == 2


@pytest.mark.parametrize(
    "name, contents",
    [
        ("malformed.json", b"{not valid json"),
        ("truncated.json", b'{"engine_build": "firefox-20", "probes":'),
        # Not valid UTF-8: raises UnicodeDecodeError, which is a ValueError but
        # NOT a json.JSONDecodeError. Pinned because catching only the narrower
        # type would let this one traceback out as exit 1.
        ("binary.json", b"\xff\xfe\x00garbage"),
    ],
)
def test_an_unreadable_baseline_exits_2_not_1(tmp_path, monkeypatch, name, contents):
    """A corrupt reference is "the check could not run", never "the identity
    moved". Same contract as the missing case, different failure mode."""
    from src.services.verify import baseline_cli

    monkeypatch.setattr(baseline, "record_snapshot", lambda **kw: _snap())

    artifact = tmp_path / name
    artifact.write_bytes(contents)

    assert baseline_cli.main(["check", "--baseline", str(artifact)]) == 2


@pytest.mark.parametrize(
    "name, contents",
    [
        # Parses fine, is not a mapping at all: these reached diff.py and died
        # with an uncaught AttributeError — a traceback surfacing on the DRIFT
        # code, which is the same defect as the corrupt cases above arriving
        # through a later door.
        ("a_list.json", b"[1, 2, 3]"),
        ("null.json", b"null"),
        ("a_string.json", b'"not a snapshot"'),
        # A mapping with no probes is the WORSE one: it compares cleanly
        # against nothing, so every observed probe reads as "added" and the
        # tool prints a confident maximum-alarm FAIL for a comparison that
        # never happened. No traceback to give it away.
        ("empty_object.json", b"{}"),
        # The same shape reached by a realistic typo: a real, tracked JSON file
        # in this repository that is not a snapshot.
        ("looks_like_package_json.json", b'{"name": "site", "version": "1.0.0"}'),
        # Structurally a snapshot, but carrying zero readings — what a refused
        # or truncated recording leaves behind. Also all-"added", also a false
        # red.
        (
            "no_readings.json",
            b'{"engine_build": "firefox-20", "probes": {"window": {}, "worker": {}}}',
        ),
    ],
)
def test_a_valid_json_non_snapshot_exits_2_not_1(tmp_path, monkeypatch, name, contents):
    """Parsing is not the same question as BEING a baseline.

    A file can read back perfectly and still be nothing to compare against. The
    contract is about whether a comparison HAPPENED, not about whether bytes
    came off disk — so a wrong-but-readable artifact is "could not run" (2),
    never the drift signal (1).
    """
    from src.services.verify import baseline_cli

    # The reference side is what's under test; the reading must not be what
    # fails, or the exit code would prove nothing about the guard.
    monkeypatch.setattr(baseline, "record_snapshot", lambda **kw: _snap())

    artifact = tmp_path / name
    artifact.write_bytes(contents)

    assert baseline_cli.main(["check", "--baseline", str(artifact)]) == 2


def test_a_non_snapshot_baseline_never_claims_drift(tmp_path, monkeypatch, capsys):
    """The exit code is for callers; the operator reads the screen.

    A baseline with no probes used to print "DRIFT: N probe(s)" — the most
    alarming output this system has, produced by a reading that never happened.
    Because it carries no traceback it looks like a real answer, so it gets
    believed. The message must refuse instead of accusing.
    """
    from src.services.verify import baseline_cli

    monkeypatch.setattr(baseline, "record_snapshot", lambda **kw: _snap())

    artifact = tmp_path / "not-a-baseline.json"
    artifact.write_bytes(b"{}")

    assert baseline_cli.main(["check", "--baseline", str(artifact)]) == 2

    captured = capsys.readouterr()
    assert "DRIFT" not in captured.out
    assert "FAIL" not in captured.out
    # And it must name the cause and the way out, not merely decline.
    assert "NOT drift" in captured.err
    assert "probes" in captured.err
    assert "record" in captured.err


def test_the_reason_a_check_could_not_run_says_it_is_not_drift(tmp_path, capsys):
    """The exit code carries the contract, but a human reads the message. It
    has to say plainly that nothing was compared, or the operator seeing red
    still concludes the engine changed the identity."""
    from src.services.verify import baseline_cli

    missing = tmp_path / "gone.json"
    assert baseline_cli.main(["check", "--baseline", str(missing)]) == 2
    message = capsys.readouterr().err

    assert "NOT drift" in message
    # And it has to say what to do about it, not just what went wrong.
    assert "record" in message
    assert str(missing) in message


def test_a_clean_reading_that_cannot_be_written_is_could_not_run_not_a_verdict(
    tmp_path, monkeypatch
):
    """Recording a good reading and failing to persist it is a run that did not
    happen (exit 2), not a bad baseline (exit 1) — and never a traceback."""
    from src.services.verify import baseline_cli

    monkeypatch.setattr(baseline_cli, "record_snapshot", lambda **kw: _snap())

    unwritable = tmp_path / "no-such-dir" / "out.json"
    assert baseline_cli.main(["record", "-o", str(unwritable)]) == 2


def test_a_refusal_still_refuses_when_the_rejected_copy_cannot_be_parked(
    tmp_path, monkeypatch, capsys
):
    """The refusal is the verdict and it stands on its own. Failing to park the
    unusable reading downgrades the message, but must not turn the refusal into
    a traceback — and must stay exit 1, because probes really were unread."""
    from src.services.verify import baseline_cli

    monkeypatch.setattr(
        baseline_cli,
        "record_snapshot",
        lambda **kw: _snap(worker={"p": {"error": "TypeError: nope"}}),
    )

    unwritable = tmp_path / "no-such-dir" / "out.json"
    assert baseline_cli.main(["record", "-o", str(unwritable)]) == 1

    message = capsys.readouterr().err
    assert "REFUSING" in message
    assert "is UNCHANGED" in message


def test_record_refuses_to_write_a_baseline_that_has_unread_probes(
    tmp_path, monkeypatch
):
    """A baseline with holes is not a baseline: every probe it could not read
    will compare EQUAL against the same failure later and report agreement.

    "Refuses to write" means the BLESSED PATH is not written. The failed
    reading is still kept, but beside it, so it can be inspected.
    """
    from src.services.verify import baseline_cli

    out = tmp_path / "out.json"
    monkeypatch.setattr(
        baseline_cli,
        "record_snapshot",
        lambda **kw: _snap(worker={"p": {"error": "TypeError: nope"}}),
    )

    assert baseline_cli.main(["record", "-o", str(out)]) == 1
    # The reference path was never written at all...
    assert not out.exists()
    # ...but the unusable reading is available for inspection beside it.
    rejected = tmp_path / "out.json.rejected"
    assert rejected.exists()
    assert baseline.count_errors(json.loads(rejected.read_text(encoding="utf-8"))) == 1


def test_a_refused_recording_leaves_an_existing_baseline_byte_identical(
    tmp_path, monkeypatch
):
    """The assertion with teeth, and the one this ticket most depends on.

    ``--output`` defaults to the COMMITTED artifact, and re-recording after an
    accepted bump is the documented, encouraged workflow — so a write-then-
    validate order destroys the reference exactly when the operator is doing
    the right thing. The operator sees exit 1 and a refusal message, reasonably
    concludes nothing was written, and the corruption surfaces later as a
    ``check`` passing against a holed baseline.
    """
    from src.services.verify import baseline_cli

    out = tmp_path / "baseline.json"
    good = json.dumps(_snap(), indent=2)
    out.write_text(good, encoding="utf-8")
    before = out.read_bytes()

    monkeypatch.setattr(
        baseline_cli,
        "record_snapshot",
        lambda **kw: _snap(worker={"p": {"error": "TypeError: nope"}}),
    )

    assert baseline_cli.main(["record", "-o", str(out)]) == 1
    # Not "still exists" — byte-for-byte the reference we started with.
    assert out.read_bytes() == before
    # And the recording that was refused is still recoverable for diagnosis.
    assert (tmp_path / "baseline.json.rejected").exists()


def test_record_accepts_a_clean_reading(tmp_path, monkeypatch):
    from src.services.verify import baseline_cli

    out = tmp_path / "out.json"
    monkeypatch.setattr(baseline_cli, "record_snapshot", lambda **kw: _snap())

    assert baseline_cli.main(["record", "-o", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["engine_build"] == "firefox-20"


# --- the committed artifact -------------------------------------------------


def _artifact():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    return root / baseline.BASELINE_ARTIFACT


def test_the_committed_baseline_exists_and_is_a_clean_reading():
    path = _artifact()
    assert path.exists(), f"the committed baseline is missing: {path}"
    snap = json.loads(path.read_text(encoding="utf-8"))

    # A baseline with an unread probe would silently compare "equal" against
    # the same failure on a future engine.
    assert baseline.count_errors(snap) == 0
    assert snap["engine"] == "firefox"
    assert snap["profile"] == baseline.BASELINE_PROFILE_NAME
    assert set(snap["realms"]) == {"window", "worker"}
    assert snap["probes"]["window"] and snap["probes"]["worker"]


def test_the_committed_baseline_records_which_engine_it_was_taken_under():
    # "Is this baseline still current?" must be answerable by READING the file,
    # without launching anything.
    snap = json.loads(_artifact().read_text(encoding="utf-8"))
    assert snap["engine_build"].startswith("firefox-")
    assert snap["engine_build"] != "unknown"


def test_the_committed_baseline_carries_its_own_provenance():
    snap = json.loads(_artifact().read_text(encoding="utf-8"))
    prov = snap["provenance"]
    # The recorded provenance must match the profile the code would build now:
    # if someone edits the pinned profile, this fails rather than letting the
    # artifact and its stated origin drift apart.
    assert prov == baseline.provenance(baseline.baseline_profile())


def test_the_committed_baseline_compares_clean_against_itself():
    snap = json.loads(_artifact().read_text(encoding="utf-8"))
    result = baseline.compare(snap, json.loads(json.dumps(snap)))
    assert result.ok, result.report()


@pytest.mark.parametrize(
    "realm, probe_id",
    [("window", "navigator.userAgent"), ("worker", "navigator.userAgent")],
)
def test_the_committed_baseline_covers_both_realms_with_real_readings(realm, probe_id):
    snap = json.loads(_artifact().read_text(encoding="utf-8"))
    entry = snap["probes"][realm][probe_id]
    assert "value" in entry, entry


def test_perturbing_the_committed_baseline_is_caught():
    """The gate must be shown to FAIL, against the real artifact — a gate that
    cannot fail has not been tested."""
    snap = json.loads(_artifact().read_text(encoding="utf-8"))
    perturbed = json.loads(json.dumps(snap))
    perturbed["probes"]["worker"]["navigator.userAgent"] = {"value": "TAMPERED"}

    result = baseline.compare(snap, perturbed)

    assert not result.ok
    assert "TAMPERED" in result.report()
    assert "worker" in result.report()


def test_the_baseline_profile_is_never_written_to_the_profile_store():
    # The baseline identity must not be editable by a human out from under the
    # artifact, so it is constructed as a dataclass rather than looked up.
    import inspect

    src = inspect.getsource(baseline.baseline_profile)
    assert "ProfileManager" not in src
    assert "add_profile" not in src


# --- the artifact must track the probe inventory ----------------------------
#
# PS-48 shipped a broken gate through a fully green suite, and the hole was
# structural rather than careless. The change reshaped `realm.bootMarkers` and
# added `realm.seedRecoverable`, but the committed artifact — the reference
# every `check` compares against — was updated for neither. `baseline check`
# then reported four differing probes on a COMPLETELY HEALTHY browser.
#
# Nothing above caught it, because nothing above could:
# `test_the_committed_baseline_compares_clean_against_itself` compares the
# fixture to ITSELF, so it stays green no matter how far the fixture drifts
# from what the code would now produce. Every other artifact test reads a probe
# id it names literally, so a probe nobody thought to name is invisible.
#
# These close that: the artifact's probe-id set must equal the LIVE inventory's,
# per realm. The failure mode they prevent is the worst kind for this gate — see
# engine-fingerprint-baseline.md:182, a baseline that fails for an unexplained
# reason "trains the operator to ignore the command", and the next REAL engine
# drift then lands in a diff nobody reads.


def _inventory_mismatch(snap, realm, live_ids=None):
    """(missing, stale) between the artifact's probe ids and the inventory's.

    `missing` exist in the inventory but not the artifact (a `check` reports
    them ADDED on a healthy browser); `stale` are the reverse (reported
    REMOVED). `live_ids` is injectable ONLY so the falsification below can drive
    this same code with a probe the inventory does not really have — the two
    tests share one implementation so the negative case exercises the real
    comparison rather than re-deriving it.
    """
    from src.services.verify.probes import probes_for_realm

    if live_ids is None:
        live_ids = {p.id for p in probes_for_realm(realm)}
    recorded = set(snap["probes"][realm])
    return sorted(set(live_ids) - recorded), sorted(recorded - set(live_ids))


@pytest.mark.parametrize("realm", ["window", "worker"])
def test_the_committed_baseline_records_exactly_the_live_probe_inventory(realm):
    """Reshape or add a probe and this fails until the artifact is re-recorded.

    A SET comparison against the inventory, not a count, so the message names
    the probe that moved instead of reporting that two numbers differ.
    """
    snap = json.loads(_artifact().read_text(encoding="utf-8"))
    missing, stale = _inventory_mismatch(snap, realm)

    assert not missing, (
        f"{realm}: {missing} exist in the probe inventory but are absent from "
        f"the committed baseline, so `check` reports them ADDED on a healthy "
        f"browser. Re-record: "
        f"xvfb-run -a python -m src.services.verify.baseline_cli record"
    )
    assert not stale, (
        f"{realm}: {stale} are recorded in the committed baseline but no longer "
        f"exist in the probe inventory, so `check` reports them REMOVED on a "
        f"healthy browser. Re-record the artifact."
    )


def test_a_probe_added_without_re_recording_the_baseline_is_caught():
    """The guard above must be shown to FAIL, or it is the same green-but-blind
    test it was written to replace.

    Drives the REAL comparison with an inventory carrying one probe the artifact
    does not, which is exactly the PS-48 mistake.
    """
    from src.services.verify.probes import probes_for_realm

    snap = json.loads(_artifact().read_text(encoding="utf-8"))
    live = {p.id for p in probes_for_realm("window")} | {"realm.newlyAddedProbe"}

    missing, stale = _inventory_mismatch(snap, "window", live_ids=live)

    assert missing == ["realm.newlyAddedProbe"]
    assert not stale


def test_a_probe_dropped_from_the_inventory_is_caught():
    """The other direction: the artifact still carries a probe the code removed.

    Distinct from the case above because the two produce opposite `check`
    verdicts (REMOVED vs ADDED) and a guard that only tested one would miss a
    deletion entirely.
    """
    from src.services.verify.probes import probes_for_realm

    snap = json.loads(_artifact().read_text(encoding="utf-8"))
    live = {p.id for p in probes_for_realm("window")} - {"realm.seedRecoverable"}

    missing, stale = _inventory_mismatch(snap, "window", live_ids=live)

    assert stale == ["realm.seedRecoverable"]
    assert not missing


# --- the engine the profile ACTUALLY launches on (PS-237) -------------------
#
# NO BROWSER HERE either, and the fakes are chosen so they cannot manufacture
# the result. The seam under test is which CHANNEL `record_snapshot` reaches
# for, so the launch and the channel are both faked and the assertion is on the
# RETURNED SNAPSHOT DOCUMENT — never on source text and never on "a helper was
# called". A test that asserted the latter would still pass if the document
# came out stamped with the wrong engine, which is half the defect.


def _chromium_effective_profile(name="ps237-chromium-effective"):
    """A profile STORED as firefox that LAUNCHES on chromium.

    macos/desktop is deliberate and is the corrected, wider claim: it is not a
    mobile edge case. `coherent_engine` reconciles every non-Windows OS toward
    chromium (chromium honours os_type; stealth-Firefox reports Windows
    regardless — process.py's own comment says so), so windows+desktop is the
    ONE pairing that stays on firefox.
    """
    from src.models.profile import Profile

    return Profile(
        name=name,
        proxy=None,
        os_type="macos",
        device_type="desktop",
        engine="firefox",
        resolution="1920x1080",
        search_engine="duckduckgo",
        bookmarks=[],
        certificate=None,
        ai_control=False,
        hardware_generation_value=0,
    )


def test_a_profile_stored_as_firefox_but_launching_on_chromium_is_the_defect():
    """The premise. If this ever stops holding, the rest of this section is
    testing a problem that no longer exists and must be re-derived."""
    from src.services.browser.process import effective_engine

    profile = _chromium_effective_profile()
    assert profile.engine == "firefox", "stored engine"
    assert effective_engine(profile) == "chromium", (
        "the stored engine and the launched engine must diverge for this "
        "section to be meaningful"
    )


def _no_launch(*a, **kw):
    """A spawn_browser that fails the test instead of starting a browser.

    Not decoration. When AC8's falsification reverts the engine resolution, a
    chromium-effective profile falls back into the FIREFOX arm and this suite
    really does try to launch a browser — observed during the falsification
    run, which crashed a chromium child rather than failing an assertion. A
    test whose failure mode is "spawn a real browser" is unacceptable in a
    suite that promises not to, so every chromium-arm test below pins this and
    the falsification reads as a clean AssertionError.
    """
    raise AssertionError("no test on the chromium arm may launch a browser")


class _FakeTransport:
    """A live chromium channel, shaped like transport.Transport.

    ``evaluate`` speaks the runner's actual wire protocol — a ``{"v": ...}`` /
    ``{"e": ...}`` reply, not a bare value. A fake that returned a bare string
    would be normalised into a ProtocolError entry by ``runner._as_entry``, so
    the "a real reading came back" assertion below would be passing over 49
    errored probes.
    """

    def __init__(self, value="CHROME"):
        self.engine = "chromium"
        self.closed = False
        self._value = value

    def evaluate(self, expression):
        return {"v": self._value}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


def test_a_chromium_effective_profile_is_recorded_through_the_chromium_channel(
    monkeypatch,
):
    """AC1 + AC3, asserted on the returned document.

    Pre-fix this raised BaselineUnavailable("...published no eval hook..."):
    the read was an unconditional get_ff_eval while the launch routed on
    effective_engine. The firefox hook is left DELIBERATELY UNSET below, so a
    regression cannot pass by quietly falling back to it.
    """
    from src.services.verify import baseline as bl

    monkeypatch.setattr(bl, "_require_display", lambda: None)

    transport = _FakeTransport()
    monkeypatch.setattr(
        "src.services.verify.transport.transport_for",
        lambda name, engine: transport,
    )

    # If anything on this path tries to LAUNCH, the test fails loudly rather
    # than silently exercising the firefox arm.
    def _must_not_launch(*a, **kw):
        raise AssertionError("the chromium arm must not launch a browser")

    monkeypatch.setattr("src.services.browser.process.spawn_browser", _must_not_launch)

    snap = bl.record_snapshot(
        profile=_chromium_effective_profile(), fresh=False, realms=("window",)
    )

    # THE HEADER STOPS ASSERTING A CONSTANT. This is the whole of AC3: the
    # document says what was observed, not what BASELINE_ENGINE says.
    assert snap["engine"] == "chromium"
    assert snap["engine"] != bl.BASELINE_ENGINE
    # A real reading came back through that channel — not an empty document.
    assert bl.count_errors(snap) == 0
    assert snap["probes"]["window"]["navigator.userAgent"]["value"] == "CHROME"
    # The channel was released.
    assert transport.closed, "the transport must be closed after recording"


def test_the_firefox_arm_still_launches_in_process_and_reads_its_hook(monkeypatch):
    """AC5. The pinned baseline profile is windows/desktop/firefox, so it must
    take the byte-identical path it always did — launch in-process, read the
    per-process eval hook, tear the session down."""
    from src.services.verify import baseline as bl

    monkeypatch.setattr(bl, "_require_display", lambda: None)
    monkeypatch.setattr(bl, "_await_started", lambda proc, timeout: None)

    torn_down = []
    monkeypatch.setattr(bl, "_teardown", lambda proc, name: torn_down.append(name))

    launched = {}

    def _spawn(profile, in_process=False):
        launched["name"] = profile.name
        launched["in_process"] = in_process
        return object()

    monkeypatch.setattr("src.services.browser.process.spawn_browser", _spawn)
    # The runner's wire protocol is a {"v": ...} / {"e": ...} reply, not a bare
    # value — a fake returning "FF" directly would be normalised into a
    # ProtocolError entry and the reading assertion below would pass over 49
    # errored probes.
    monkeypatch.setattr(
        "src.services.browser.invisible_launch.get_ff_eval",
        lambda name: {"eval": lambda expr: {"v": "FF"}},
    )
    # The chromium channel must NOT be consulted for a firefox profile.
    monkeypatch.setattr(
        "src.services.verify.transport.transport_for",
        lambda name, engine: (_ for _ in ()).throw(
            AssertionError("firefox must not go through the chromium transport")
        ),
    )

    snap = bl.record_snapshot(fresh=False, realms=("window",))

    assert snap["engine"] == "firefox" == bl.BASELINE_ENGINE
    assert snap["profile"] == bl.BASELINE_PROFILE_NAME
    assert snap["probes"]["window"]["navigator.userAgent"]["value"] == "FF"
    # in_process is load-bearing: the hook is published per-process.
    assert launched == {"name": bl.BASELINE_PROFILE_NAME, "in_process": True}
    assert torn_down == [bl.BASELINE_PROFILE_NAME]


# --- the trap: an unreachable arm is never a pass (AC4) ---------------------


def test_an_unreachable_chromium_arm_raises_rather_than_returning_a_blank(
    monkeypatch,
):
    """The named trap, from the failing side.

    `diff_snapshots` compares entries verbatim, so two identically-FAILED
    readings compare EQUAL and are reported as AGREEMENT. An arm that could not
    be read must therefore never become a document at all — it raises. This
    asserts the refusal is a raised BaselineUnavailable, not a returned
    snapshot carrying errors.
    """
    from src.services.verify import baseline as bl
    from src.services.verify.transport import TransportUnavailable

    monkeypatch.setattr(bl, "_require_display", lambda: None)
    monkeypatch.setattr("src.services.browser.process.spawn_browser", _no_launch)
    monkeypatch.setattr(
        "src.services.verify.transport.transport_for",
        lambda name, engine: (_ for _ in ()).throw(
            TransportUnavailable("no debug port — not running under automation")
        ),
    )

    with pytest.raises(bl.BaselineUnavailable) as exc:
        bl.record_snapshot(
            profile=_chromium_effective_profile(), fresh=False, realms=("window",)
        )

    msg = str(exc.value)
    assert "no debug port" in msg, "the actionable cause must survive"
    assert "nothing is certified" in msg.lower()


def test_the_chromium_arm_records_with_no_display_because_it_does_not_launch(
    monkeypatch,
):
    """The round-3 blocker, from the side that was unobservable.

    THIS TEST DELIBERATELY DOES NOT STUB ``_require_display``. Every other
    chromium-arm test in this file does, and that is exactly why the defect
    survived a full green suite: the gate was stubbed away at all seven call
    sites, so nothing ever pointed the instrument at the gate itself.

    The gate used to run unconditionally at the top of ``record_snapshot``,
    which was correct while there was one path and it always launched. The
    chromium arm attaches to a session the operator already started, so it
    needs no display — and gating it on one refused the exact deployment this
    ticket exists to reach: a headless host running chromium under automation,
    where a chromium-effective profile was STILL unobservable to Levels 1 and
    2. Same "the instrument cannot be pointed at chromium" defect, with the
    display gate substituted for the hardcoded ``get_ff_eval``.

    DISPLAY is unset FOR REAL, and ``IS_LINUX`` is pinned True so the
    assertion's colour tracks the code rather than the CI runner's OS — on a
    macOS or Windows runner the gate is a no-op and this test would pass
    without ever exercising it.
    """
    from src.services.verify import baseline as bl

    monkeypatch.setattr("src.core.platform.IS_LINUX", True, raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    transport = _FakeTransport()
    monkeypatch.setattr(
        "src.services.verify.transport.transport_for",
        lambda name, engine: transport,
    )
    monkeypatch.setattr("src.services.browser.process.spawn_browser", _no_launch)

    snap = bl.record_snapshot(
        profile=_chromium_effective_profile(), fresh=False, realms=("window",)
    )

    # A real reading, on a host with no display server at all.
    assert snap["engine"] == "chromium"
    assert bl.count_errors(snap) == 0
    assert snap["probes"]["window"]["navigator.userAgent"]["value"] == "CHROME"


def test_the_firefox_arm_still_refuses_without_a_display(monkeypatch):
    """The companion, and the reason this pair must be read together.

    Moving the gate must not DELETE it. The firefox arm really does launch a
    real browser, so on Linux with no display it must still refuse loudly —
    and the message it refuses with ("launches a real browser", "xvfb-run") is
    true on this arm, which is what makes it the right home for the gate.

    Without this test, the fix for the blocker above is indistinguishable from
    simply dropping the gate.
    """
    from src.services.verify import baseline as bl

    monkeypatch.setattr("src.core.platform.IS_LINUX", True, raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    # The refusal must arrive BEFORE any launch is attempted.
    monkeypatch.setattr("src.services.browser.process.spawn_browser", _no_launch)

    with pytest.raises(bl.BaselineUnavailable) as exc:
        bl.record_snapshot(profile=bl.baseline_profile(), fresh=False)

    msg = str(exc.value)
    assert "no DISPLAY" in msg
    assert "xvfb-run" in msg, "the remedy must survive, and it is true here"


def test_a_fresh_recording_of_a_chromium_profile_is_refused_not_downgraded(
    monkeypatch,
):
    """fresh=True means wipe-then-launch, and the chromium arm may not launch.
    Wiping a LIVE session's directory is corruption, not a clean start, so this
    is refused rather than silently served as a warm read whose provenance
    would claim a freshness it does not have."""
    from src.services.verify import baseline as bl

    monkeypatch.setattr(bl, "_require_display", lambda: None)
    monkeypatch.setattr("src.services.browser.process.spawn_browser", _no_launch)

    wiped = []
    monkeypatch.setattr(
        "shutil.rmtree", lambda p, **kw: wiped.append(p)
    )

    with pytest.raises(bl.BaselineUnavailable) as exc:
        bl.record_snapshot(
            profile=_chromium_effective_profile(), fresh=True, realms=("window",)
        )

    assert "fresh" in str(exc.value).lower()
    assert not wiped, "a live profile's data directory must not be wiped"


def test_readings_or_refuse_still_refuses_an_unreadable_chromium_recording():
    """AC4's second net, at the comparator door.

    Even if an errored document reached the behavioural checks, no comparison
    may be performed over it. This is the guard that stops two identical
    FAILURES being reported as agreement.
    """
    from src.services.verify.behaviour import BehaviourCheckError, _readings_or_refuse

    unreadable = _snap(
        window={"navigator.userAgent": {"error": "Target closed"}},
        worker={"navigator.userAgent": {"error": "Target closed"}},
    )
    unreadable["engine"] = "chromium"

    with pytest.raises(BehaviourCheckError) as exc:
        _readings_or_refuse(unreadable, "chromium")

    detail = str(exc.value)
    assert "unreadable probe" in detail
    assert "Nothing was certified" in detail

    # And the empty case, which is the shape an unreachable arm would take.
    empty = _snap(window={}, worker={})
    empty["probes"] = {}
    with pytest.raises(BehaviourCheckError) as exc:
        _readings_or_refuse(empty, "chromium")
    assert "no probes at all" in str(exc.value)


def test_two_identically_failed_readings_would_otherwise_compare_equal():
    """The reason the guard above must exist, demonstrated rather than asserted
    in prose. If this ever stops being true the guard is still correct, but its
    rationale has changed and should be re-read."""
    from src.services.verify.diff import diff_snapshots

    failed = _snap(
        window={"navigator.userAgent": {"error": "Target closed"}},
        worker={"navigator.userAgent": {"error": "Target closed"}},
    )
    other = json.loads(json.dumps(failed))

    entries = diff_snapshots(failed, other)
    changed = [e for e in entries if e.get("status") == "changed"]
    assert not changed, (
        "two identical failures compare EQUAL — which is exactly why an "
        "unreadable arm must never reach a comparator"
    )


# --- the comparator needs no edit (AC3) -------------------------------------


def test_the_comparator_already_reports_an_engine_mismatch_as_meta():
    """CONFIRMED, not assumed. `engine` is already in diff._META_FIELDS, so a
    firefox snapshot compared against a chromium one reports the engine
    difference as a __meta__ entry — 'a different question', not drift. This
    test exists so that fact stays true, since the recorder now emits both."""
    from src.services.verify.diff import META_REALM, _META_FIELDS, diff_snapshots

    assert "engine" in _META_FIELDS

    ff = _snap()
    chrome = _snap()
    chrome["engine"] = "chromium"

    entries = diff_snapshots(ff, chrome, include_meta=True)
    meta = [e for e in entries if e["realm"] == META_REALM]
    engine_entry = [e for e in meta if e["probe_id"] == "engine"]

    assert engine_entry, "an engine mismatch must be reported as a meta entry"
    assert engine_entry[0]["expected"] == "firefox"
    assert engine_entry[0]["observed"] == "chromium"


# --- isolation (AC6) --------------------------------------------------------


def test_no_verification_path_persists_ai_control(monkeypatch):
    """The isolation line, asserted on the objects the code actually builds.

    The chromium arm reads a session the operator already opened; it must never
    turn ai_control ON to manufacture a debugging port. The pinned baseline
    profile has it off, and the scratch profiles the behavioural checks build
    have it off.
    """
    from src.services.verify.behaviour import Context

    assert baseline.baseline_profile().ai_control is False

    created = {}

    class _FakeManager:
        profiles = {}

        def add_profile(self, name, proxy, **opts):
            created.update(opts)
            from src.models.profile import Profile

            self.profiles[name] = Profile(
                name=name, proxy=None, **{
                    k: v for k, v in opts.items() if k != "bookmarks"
                }, bookmarks=[]
            )
            return True

    ctx = Context(home="/tmp/scratch-ps237")
    ctx._manager = _FakeManager()
    monkeypatch.setattr(ctx, "manager", lambda: ctx._manager)

    ctx.make_profile("ps237-scratch")

    assert created["ai_control"] is False, (
        "a verification scratch profile must never be stored with ai_control on"
    )


# --- AC3 round 2: the header reports the CHANNEL, not the RECORD -------------
#
# Round-2 review found the header stamped `effective_engine(profile)` — the
# profile RECORD — while the channel was picked by a hardcoded literal. A record
# is an ASSERTION; a transport is an OBSERVATION. The tests below drive the two
# apart on purpose, because a test in which they agree cannot tell the fix from
# the defect.


def test_both_arms_report_the_engine_their_own_transport_reports(monkeypatch):
    """The two channel constants ARE the adapters' own `engine` values.

    Cited by name from `baseline._record_on_firefox` and from the constants
    themselves, so those docstrings do not promise a guard that is not here.

    Read off REAL adapter instances rather than compared as literals: two
    equal string constants prove nothing about the objects they stand for, and
    the whole defect being fixed was two spellings of "the engine" drifting
    apart. `_ChromiumTransport.__init__` sets `.engine` before it attaches, so
    the attach is stubbed out — that is what makes the object reachable with no
    browser, not a weakening of the assertion.
    """
    from src.services.verify import baseline as bl
    from src.services.verify import transport as tr

    firefox = tr._FirefoxTransport("p", {"eval": lambda expr: {"v": 1}})
    assert firefox.engine == bl._FIREFOX_CHANNEL

    async def _no_attach(self):
        return None

    monkeypatch.setattr(tr._ChromiumTransport, "_attach", _no_attach)
    chromium = tr._ChromiumTransport("p", 9222)
    try:
        assert chromium.engine == bl._CHROMIUM_CHANNEL
    finally:
        chromium.close()

    # And they are genuinely two different channels, so a test that confused
    # them could not pass by both constants being the same string.
    assert bl._FIREFOX_CHANNEL != bl._CHROMIUM_CHANNEL


def test_the_header_is_stamped_by_the_transport_not_by_the_profile_record(
    monkeypatch,
):
    """THE MISMATCH, BUILT AND WATCHED. This is the discriminating test.

    `test_a_chromium_effective_profile_is_recorded_through_the_chromium_channel`
    above passes against the DEFECT as well as against the fix, because for that
    profile `effective_engine` and the channel happen to agree — so agreement
    cannot tell them apart. Here the transport deliberately reports something
    the profile record does not, and only a header sourced from the transport
    can report it.

    `engine_build` is asserted through a STUBBED accessor rather than against
    the string "unknown": chromium is not installed in this container, so
    `engine_build("chromium")` answers "unknown" here for an environmental
    reason that has nothing to do with this defect. Asserting on that literal
    would be a test whose colour tracks the container (PS-14).
    """
    from src.services.verify import baseline as bl

    monkeypatch.setattr(bl, "_require_display", lambda: None)
    monkeypatch.setattr("src.services.browser.process.spawn_browser", _no_launch)

    # The channel answers with an engine name the profile record does not carry
    # and `effective_engine` never returns. Under the round-1 spelling the
    # header took `effective_engine(profile)` -> "chromium" and this string
    # could not appear in the document at all.
    observed = "chromium-cdp-attached"

    transport = _FakeTransport()
    transport.engine = observed
    monkeypatch.setattr(
        "src.services.verify.transport.transport_for",
        lambda name, engine: transport,
    )

    monkeypatch.setattr(
        "src.services.engine.updater.current_version",
        lambda: "chromium-148.0.7778.215",
    )

    profile = _chromium_effective_profile()
    snap = bl.record_snapshot(profile=profile, fresh=False, realms=("window",))

    from src.services.browser.process import effective_engine

    assert snap["engine"] == observed, (
        "the header must report the channel that did the reading"
    )
    assert snap["engine"] != effective_engine(profile), (
        "sourcing the header from the profile record is the defect under test"
    )
    assert snap["engine"] != profile.engine, "nor from the stored engine field"
    # A real reading came back, so this is not a refusal wearing a pass's shape.
    assert bl.count_errors(snap) == 0
    assert snap["probes"]["window"]["navigator.userAgent"]["value"] == "CHROME"


def test_the_engine_build_follows_the_observed_engine(monkeypatch):
    """AC3's provenance half: the build is resolved for the OBSERVED family.

    Round-2 review measured `engine_build` degrading to "unknown" because the
    header carried a family no accessor recognises, silently losing the build
    the artifact was actually recorded under. Stubbed accessor, so this
    discriminates on a machine with no chromium installed.
    """
    from src.services.verify import baseline as bl

    monkeypatch.setattr(bl, "_require_display", lambda: None)
    monkeypatch.setattr("src.services.browser.process.spawn_browser", _no_launch)
    monkeypatch.setattr(
        "src.services.verify.transport.transport_for",
        lambda name, engine: _FakeTransport(),
    )
    monkeypatch.setattr(
        "src.services.engine.updater.current_version",
        lambda: "chromium-148.0.7778.215",
    )

    snap = bl.record_snapshot(
        profile=_chromium_effective_profile(), fresh=False, realms=("window",)
    )

    assert snap["engine"] == "chromium"
    assert snap["engine_build"] == "chromium-148.0.7778.215", (
        "the build must be resolved for the engine that was observed"
    )


@pytest.mark.parametrize("stored", ["Chromium", "webkit", "CHROMIUM"])
def test_an_engine_this_recorder_cannot_speak_is_refused_never_read(
    monkeypatch, stored
):
    """The `else` branch, decided deliberately: refuse, do not guess.

    `coherent_engine` returns a coherent value UNCHANGED and only firefox pairs
    are constrained, so 'Chromium' and 'webkit' are both storable through the
    ordinary door and both reach `effective_engine` verbatim — round-2 review
    executed exactly that. The old bare `else` sent them to the chromium
    adapter and then stamped them with their own unknown name.

    Refusing is this module's posture: an engine nobody can name is an
    inconclusive, and inconclusive is never a pass. Asserts the refusal is a
    RAISE and that nothing was read — a refusal that still opened a channel
    would be the defect with a message attached.
    """
    from src.models.profile import Profile
    from src.services.verify import baseline as bl

    monkeypatch.setattr(bl, "_require_display", lambda: None)
    monkeypatch.setattr("src.services.browser.process.spawn_browser", _no_launch)

    def _must_not_open(name, engine):
        raise AssertionError(
            f"an unspeakable engine ({stored!r}) must be refused BEFORE a "
            "channel is opened"
        )

    monkeypatch.setattr(
        "src.services.verify.transport.transport_for", _must_not_open
    )

    profile = Profile(
        name="ps237-unspeakable",
        proxy=None,
        os_type="windows",
        device_type="desktop",
        engine=stored,
        resolution="1920x1080",
        search_engine="duckduckgo",
        bookmarks=[],
        certificate=None,
        ai_control=False,
        hardware_generation_value=0,
    )

    # Premise: this really is storable and really does survive to the router.
    from src.services.browser.process import effective_engine

    assert effective_engine(profile) == stored, (
        "premise: the stored spelling reaches the router unnormalised"
    )

    with pytest.raises(bl.BaselineUnavailable) as exc:
        bl.record_snapshot(profile=profile, fresh=False, realms=("window",))

    msg = str(exc.value)
    assert stored in msg, "the refusal must name the engine it could not speak"
    assert "nothing is certified" in msg.lower()
