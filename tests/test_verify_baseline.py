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
    will compare EQUAL against the same failure later and report agreement."""
    from src.services.verify import baseline_cli

    out = tmp_path / "out.json"
    monkeypatch.setattr(
        baseline_cli,
        "record_snapshot",
        lambda **kw: _snap(worker={"p": {"error": "TypeError: nope"}}),
    )

    assert baseline_cli.main(["record", "-o", str(out)]) == 1
    # It still writes the file so the errors can be inspected...
    assert out.exists()


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
