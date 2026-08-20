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
        self.stdout = os.fdopen(r)

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
