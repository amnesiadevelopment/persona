"""Tests for src/services/verify/engine_gate — the engine-bump release gate.

NO BROWSER HERE, and there must not be one: ``playwright`` is a git-pinned
dependency that is not importable in this container. Every test below drives
the gate with snapshot dicts and injects a fake recorder, exactly as
``test_verify_baseline.py`` does.

What these tests pin is the gate's VERDICT logic, and the emphasis is
deliberately lopsided toward the false green. A false red gets investigated by
somebody; a false green does not, and it fires at precisely the moment the gate
was supposed to be looking. So the cases that get the most attention here are
the ones where the gate could report a confident pass over a comparison that
never happened:

  * both recordings landed on the SAME engine build (provisioning silently
    failed) — agrees by construction;
  * an engine build that could not be resolved at all;
  * a comparison resting only on readings nobody obtained;
  * a probe present on one side and absent on the other, which a comparator
    looping over the INTERSECTION of the two probe sets cannot see at all.

Plus the structural guarantee that the gate has no path to re-record the
committed reference — because a job that re-records itself until green destroys
the very artifact it exists to defend.
"""

import json

import pytest

from src.services.verify import engine_gate

# --- helpers ----------------------------------------------------------------


def _snap(window=None, worker=None, *, build="firefox-20", stack=None):
    """A minimal snapshot document shaped like the real one.

    ``stack`` defaults to a core version whose major TRACKS ``build``, which is
    the only pairing a real runner is allowed to produce: engine_autobump
    derives ``firefox-NN`` from ``core_major(latest_core)``, so the two majors
    are equal by construction. Pass it explicitly to build the misprovisioned
    pairing the lockstep guard exists to refuse.
    """
    if stack is None:
        major = engine_gate.build_major(build)
        stack = f"{major}.14.0" if major >= 0 else "0.14.0"
    return {
        "schema_version": 1,
        "engine": "firefox",
        "engine_build": build,
        engine_gate.STACK_FIELD: stack,
        "profile": "persona-fingerprint-baseline",
        "app_version": "9.9.9",
        "realms": ["window", "worker"],
        "probes": {
            "window": dict(window or {"navigator.userAgent": {"value": "FF"}}),
            "worker": dict(worker or {"navigator.userAgent": {"value": "FF"}}),
        },
    }


@pytest.fixture
def provisioned(monkeypatch):
    """A correctly-provisioned runner: engine binary and driver in lockstep.

    ``record`` resolves both from the environment, and neither is available in
    this container (no engine, and invisible_core is a git-pinned dep that is
    not importable here) — so without this the record tests would exercise the
    misprovisioned path rather than the one they are about.
    """
    monkeypatch.setattr(engine_gate, "engine_build", lambda engine: "firefox-20")
    monkeypatch.setattr(engine_gate, "installed_core_version", lambda: "20.14.0")


def _pair(*, after_window=None, after_build="firefox-21"):
    """A before/after pair that differs ONLY in the engine build by default."""
    return _snap(), _snap(after_window, build=after_build)


# --- the pass, and what it is allowed to rest on ----------------------------


def test_a_clean_comparison_across_two_builds_passes():
    code, report = engine_gate.gate(*_pair())
    assert code == engine_gate.EXIT_PASS
    assert "PASS" in report
    # The pass must name both builds: an operator reading a green log has to be
    # able to see WHICH transition was certified.
    assert "firefox-20" in report and "firefox-21" in report


def test_the_pass_reports_the_self_test_that_earned_it():
    _, report = engine_gate.gate(*_pair())
    assert "self-test" in report
    assert "moved reading" in report
    assert "absent probe" in report


# --- THE FALSE GREEN: the same engine on both sides -------------------------


def test_the_same_engine_build_on_both_sides_is_refused():
    """The load-bearing guard. If engine provisioning silently fails, both
    recordings are of the SAME engine, every probe agrees by construction, and
    the diff is empty — a confident pass over a comparison that never happened.
    """
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.gate(_snap(), _snap(build="firefox-20"))
    assert "firefox-20" in str(exc.value)


def test_the_same_engine_refusal_is_cannot_run_not_drift(tmp_path):
    """Never EXIT_DRIFT. Nothing was observed to move, and reporting drift here
    would be a false red on the most alarming signal this system has.

    Driven through the real CLI over two real files, so this asserts the
    same-engine refusal specifically — not the missing-file refusal, which
    also exits 2 and would let this test pass for the wrong reason.
    """
    same = tmp_path / "b.json"
    same.write_text(json.dumps(_snap()), encoding="utf-8")
    other = tmp_path / "a.json"
    other.write_text(json.dumps(_snap(build="firefox-20")), encoding="utf-8")
    code = engine_gate.main(["compare", str(same), str(other)])
    assert code == engine_gate.EXIT_CANNOT_RUN
    assert code != engine_gate.EXIT_DRIFT


def test_the_same_engine_refusal_never_reports_a_pass():
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.gate(_snap(), _snap(build="firefox-20"))
    assert "PASS" not in str(exc.value)


@pytest.mark.parametrize("bad", ["unknown", "", None, 17])
def test_an_unresolved_engine_build_is_refused(bad):
    """`snapshot.engine_build` never raises and answers "unknown" instead, so an
    unresolved build arrives here looking like data. Without a resolved build on
    both sides the gate cannot establish the engine actually moved.
    """
    before, after = _pair()
    after["engine_build"] = bad
    with pytest.raises(engine_gate.GateCannotRun):
        engine_gate.gate(before, after)


def test_an_unresolved_build_names_which_side_to_look_at():
    before, after = _pair()
    after["engine_build"] = "unknown"
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.gate(before, after)
    assert "after" in str(exc.value)

    before, after = _pair()
    before["engine_build"] = "unknown"
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.gate(before, after)
    assert "before" in str(exc.value)


def test_require_engine_moved_returns_both_builds_when_they_differ():
    assert engine_gate.require_engine_moved(*_pair()) == ("firefox-20", "firefox-21")


# --- THE STACK THE ENGINE-BUILD GUARD CANNOT SEE ----------------------------
#
# The second false green, one layer out from the first, and the reason these
# tests are not folded into the section above.
#
# The engine ships as a PAIR: the firefox-NN binary and the driver that pins
# invisible_core==NN. engine_autobump derives `firefox-{core_major(latest_core)}`,
# so the two majors are equal BY CONSTRUCTION — a mismatch is never incidental,
# it is always a provisioning failure.
#
# A CI step that installs the new binary and the new driver with `--no-deps`,
# and never installs the core, leaves the OLD core in place. `engine_build`
# genuinely MOVES, so `require_engine_moved` is SATISFIED and the gate proceeds
# to certify a recording of a stack nobody will ever run. That is the exact
# case these tests pin, and the first test below is the one that states why the
# older guard is not enough.


def test_a_stale_core_satisfies_the_engine_build_guard():
    """The premise of this whole section: the OLDER guard cannot catch this.

    Not a test of new code — a test that the gap the new guard fills is real.
    If this ever fails, `require_engine_moved` grew teeth here and the lockstep
    guard's justification needs re-reading.
    """
    before = _snap(build="firefox-20", stack="20.14.0")
    after = _snap(build="firefox-21", stack="20.14.0")  # binary moved, core did not
    assert engine_gate.require_engine_moved(before, after) == (
        "firefox-20",
        "firefox-21",
    )


def test_a_stale_core_on_the_after_side_is_refused():
    before = _snap(build="firefox-20", stack="20.14.0")
    after = _snap(build="firefox-21", stack="20.14.0")
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.gate(before, after)
    assert "MISPROVISIONED" in str(exc.value)


def test_a_stale_core_is_cannot_run_not_drift(tmp_path):
    """Never drift: nothing was OBSERVED to move, provisioning failed.

    Reporting a mismatched stack as drift would be a false red on the loudest
    signal this system has, and it trains an operator to disbelieve true reds.
    """
    before = tmp_path / "b.json"
    after = tmp_path / "a.json"
    before.write_text(json.dumps(_snap(build="firefox-20", stack="20.14.0")))
    after.write_text(json.dumps(_snap(build="firefox-21", stack="20.14.0")))
    code = engine_gate.main(["compare", str(before), str(after)])
    assert code == engine_gate.EXIT_CANNOT_RUN
    assert code != engine_gate.EXIT_DRIFT


def test_a_stale_core_never_reports_a_pass(tmp_path, capsys):
    before = tmp_path / "b.json"
    after = tmp_path / "a.json"
    before.write_text(json.dumps(_snap(build="firefox-20", stack="20.14.0")))
    after.write_text(json.dumps(_snap(build="firefox-21", stack="20.14.0")))
    engine_gate.main(["compare", str(before), str(after)])
    assert "PASS" not in capsys.readouterr().out


def test_the_refusal_names_both_halves_of_the_mismatched_pair():
    """The operator has to be able to act on it: which binary, which core."""
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.require_stack_lockstep("firefox-21", "20.14.0", side="after")
    message = str(exc.value)
    assert "firefox-21" in message
    assert "20.14.0" in message
    assert "after" in message


def test_a_stale_core_on_the_before_side_is_refused_too():
    """Both sides are checked. A stale core on the BEFORE side makes the
    baseline a reading of a stack nobody ships, which is just as unusable."""
    before = _snap(build="firefox-20", stack="19.14.0")
    after = _snap(build="firefox-21", stack="21.0.1")
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.gate(before, after)
    assert "before" in str(exc.value)


def test_a_matched_stack_passes_on_both_sides():
    """The control: majors tracking their builds is the pass."""
    before = _snap(build="firefox-20", stack="20.14.0")
    after = _snap(build="firefox-21", stack="21.0.1")
    code, _ = engine_gate.gate(before, after)
    assert code == engine_gate.EXIT_PASS


def test_the_pass_reports_the_lockstep_it_rests_on():
    """A premise that was checked and not stated is a premise nobody can audit."""
    before = _snap(build="firefox-20", stack="20.14.0")
    after = _snap(build="firefox-21", stack="21.0.1")
    _, report = engine_gate.gate(before, after)
    assert "lockstep" in report.lower()
    assert "20.14.0" in report and "21.0.1" in report


@pytest.mark.parametrize("missing", [None, "", 0, [], {}])
def test_a_recording_with_no_stack_stamp_is_refused(missing):
    """An unchecked premise is not a premise that held — same rule the
    unresolved engine_build refusal follows."""
    before = _snap(build="firefox-20", stack="20.14.0")
    after = _snap(build="firefox-21")
    if missing is None:
        del after[engine_gate.STACK_FIELD]
    else:
        after[engine_gate.STACK_FIELD] = missing
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.gate(before, after)
    assert engine_gate.STACK_FIELD in str(exc.value)


def test_an_absent_core_install_is_refused():
    """`pip install --no-deps` and nothing else: no core at all."""
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.require_stack_lockstep("firefox-21", "", side="after")
    assert "no invisible_core" in str(exc.value)


def test_an_unparseable_core_version_is_refused():
    with pytest.raises(engine_gate.GateCannotRun):
        engine_gate.require_stack_lockstep("firefox-21", "not-a-version", side="after")


def test_an_unparseable_build_is_refused():
    with pytest.raises(engine_gate.GateCannotRun):
        engine_gate.require_stack_lockstep("unknown", "21.0.1", side="after")


@pytest.mark.parametrize(
    "version,expected", [("20.14.0", 20), ("21.0.1", 21), ("9", 9), ("", -1), ("x", -1)]
)
def test_major_of_reads_a_core_version(version, expected):
    assert engine_gate.major_of(version) == expected


@pytest.mark.parametrize(
    "build,expected",
    [("firefox-20", 20), ("firefox-115", 115), ("unknown", -1), ("", -1)],
)
def test_build_major_reads_an_engine_build(build, expected):
    assert engine_gate.build_major(build) == expected


# --- the stamp: record checks the premise, and carries its evidence ---------


def test_record_refuses_a_misprovisioned_runner_before_launching(tmp_path, monkeypatch):
    """Fails FAST: no browser is launched to produce a recording that could not
    mean anything, and the fault is named at the step that caused it."""
    launched = []
    monkeypatch.setattr(engine_gate, "engine_build", lambda engine: "firefox-21")
    monkeypatch.setattr(engine_gate, "installed_core_version", lambda: "20.14.0")
    monkeypatch.setattr(
        engine_gate,
        "record_snapshot",
        lambda **kw: launched.append(1) or _snap(build="firefox-21"),
    )
    out = tmp_path / "rec.json"
    assert engine_gate.main(["record", "-o", str(out)]) == engine_gate.EXIT_CANNOT_RUN
    assert launched == [], "a browser was launched despite a misprovisioned stack"
    assert not out.exists(), "a recording was written for a stack nobody ships"


def test_record_stamps_the_core_it_verified(tmp_path, monkeypatch, provisioned):
    """The artifact carries its own evidence, so `compare` does not have to
    trust that this side ever checked the premise."""
    monkeypatch.setattr(engine_gate, "record_snapshot", lambda **kw: _snap())
    out = tmp_path / "rec.json"
    assert engine_gate.main(["record", "-o", str(out)]) == engine_gate.EXIT_PASS
    assert json.loads(out.read_text())[engine_gate.STACK_FIELD] == "20.14.0"


def test_record_refuses_when_the_build_moves_under_the_recording(
    tmp_path, monkeypatch, provisioned
):
    """Checked before launch, re-checked against what the artifact claims. A
    disagreement means the stamp would attest to a pairing that never held."""
    monkeypatch.setattr(
        engine_gate, "record_snapshot", lambda **kw: _snap(build="firefox-21")
    )
    out = tmp_path / "rec.json"
    assert engine_gate.main(["record", "-o", str(out)]) == engine_gate.EXIT_CANNOT_RUN
    assert not out.exists()


def test_a_recorded_artifact_round_trips_through_compare(tmp_path, monkeypatch):
    """End to end: two recordings taken through `record` compare cleanly,
    which is what the workflow actually does."""
    for name, build, core in (("b", "firefox-20", "20.14.0"), ("a", "firefox-21", "21.0.1")):
        monkeypatch.setattr(engine_gate, "engine_build", lambda engine, b=build: b)
        monkeypatch.setattr(engine_gate, "installed_core_version", lambda c=core: c)
        monkeypatch.setattr(
            engine_gate, "record_snapshot", lambda **kw: _snap(build=build)
        )
        assert (
            engine_gate.main(["record", "-o", str(tmp_path / f"{name}.json")])
            == engine_gate.EXIT_PASS
        )
    assert (
        engine_gate.main(
            ["compare", str(tmp_path / "b.json"), str(tmp_path / "a.json")]
        )
        == engine_gate.EXIT_PASS
    )


def test_installed_core_version_answers_empty_when_absent(monkeypatch):
    """Absence must answer "" — which the caller reports as a refusal, never as
    a version.

    THE ABSENCE IS STAGED, DELIBERATELY. An earlier revision of this test
    asserted the same thing but staged nothing, resting on a docstring claim
    that "invisible_core is genuinely absent in this container". It is not:
    pyproject pins ``invisible_core==20.14.0`` and both CI gates install the
    project, so the package is present wherever this suite is green. The test
    took ``monkeypatch`` and never called it, so it read the real environment
    and failed on the runner the moment it landed. A test about what happens
    when a package is missing has to MAKE it missing.
    """
    from importlib import metadata

    def not_installed(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", not_installed)
    assert engine_gate.installed_core_version() == ""


def test_installed_core_version_reads_the_installed_distribution(monkeypatch):
    """The other half of the same contract, and the half that names the defect:
    the version comes from what is INSTALLED, not from what pyproject pins.

    Those two agree in a healthy environment, which is exactly why this needs a
    value that could only have come from installed metadata — the pin is
    ``20.14.0``, so a reader returning the pin would sail through an assertion
    written against it. The sentinel below is a version no pin file mentions.
    """
    from importlib import metadata

    seen = []

    def installed(name):
        seen.append(name)
        return "99.99.99"

    monkeypatch.setattr(metadata, "version", installed)
    assert engine_gate.installed_core_version() == "99.99.99"
    assert seen == [engine_gate.CORE_DISTRIBUTION]


# --- drift: the finding -----------------------------------------------------


def test_a_moved_probe_is_reported_as_drift():
    before, after = _pair(after_window={"navigator.userAgent": {"value": "CHANGED"}})
    code, report = engine_gate.gate(before, after)
    assert code == engine_gate.EXIT_DRIFT
    assert "DRIFT" in report


def test_the_drift_report_names_probe_realm_expected_and_observed():
    """The ticket's requirement: the operator reads which probe moved, in which
    realm, expected versus observed — without opening the JSON.
    """
    before, after = _pair(after_window={"navigator.userAgent": {"value": "CHANGED"}})
    _, report = engine_gate.gate(before, after)
    assert "navigator.userAgent" in report
    assert "window" in report
    assert "FF" in report and "CHANGED" in report


def test_drift_tells_the_operator_how_to_accept_a_move_by_hand():
    """A red gate must point at the DELIBERATE acceptance path, because the
    obvious alternative — re-record until green — destroys the reference.
    """
    before, after = _pair(after_window={"navigator.userAgent": {"value": "CHANGED"}})
    _, report = engine_gate.gate(before, after)
    assert "baseline_cli" in report
    assert "record" in report


# --- THE ABSENT PROBE: the case an intersection-loop cannot see -------------


def test_a_probe_absent_on_one_side_is_reported_not_skipped():
    """A comparator looping over the INTERSECTION of the two probe sets cannot
    see this case at all: the probe drops silently out of the iteration and the
    run goes green having quietly stopped checking it.
    """
    before, after = _pair()
    del after["probes"]["window"]["navigator.userAgent"]
    code, report = engine_gate.gate(before, after)
    assert code != engine_gate.EXIT_PASS
    assert "navigator.userAgent" in report


def test_a_probe_added_on_one_side_is_reported():
    before, after = _pair()
    after["probes"]["window"]["brand.new"] = {"value": 1}
    code, report = engine_gate.gate(before, after)
    assert code != engine_gate.EXIT_PASS
    assert "brand.new" in report


# --- inconclusive is never a pass (PS-29) -----------------------------------


def test_a_comparison_resting_only_on_unread_probes_is_not_a_pass():
    """An unobtained reading is inconclusive, and inconclusive is never
    agreement — this is what stops a gate going green off two non-readings.

    The recording here carries ONE readable probe (so the self-test can prove
    the comparator works) and one probe that errored on both sides. That errored
    probe is the entire content of the diff, so this exercises the all-
    inconclusive verdict branch rather than the earlier refusal.
    """
    both = {"reads": {"value": 1}, "errs": {"error": "boom"}}
    before = _snap(both, both)
    after = _snap(both, both, build="firefox-21")
    code, report = engine_gate.gate(before, after)
    assert code == engine_gate.EXIT_CANNOT_RUN
    assert "PASS" not in report
    assert "NOT a pass" in report


def test_an_all_inconclusive_result_is_cannot_run_not_drift():
    """Distinct from drift on purpose: "we failed to look" and "the identity
    moved" need different human responses.
    """
    both = {"reads": {"value": 1}, "errs": {"error": "boom"}}
    code, _ = engine_gate.gate(_snap(both, both), _snap(both, both, build="firefox-21"))
    assert code != engine_gate.EXIT_DRIFT


def test_a_recording_with_nothing_readable_at_all_is_refused(tmp_path):
    """The stronger case: NO probe anywhere carries a reading. The comparator
    cannot be shown to work at all, so the gate refuses before it computes a
    verdict — still exit 2, still never a pass, and never drift.
    """
    errored = {"navigator.userAgent": {"error": "boom"}}
    before = tmp_path / "b.json"
    before.write_text(json.dumps(_snap(errored, errored)), encoding="utf-8")
    after = tmp_path / "a.json"
    after.write_text(
        json.dumps(_snap(errored, errored, build="firefox-21")), encoding="utf-8"
    )
    code = engine_gate.main(["compare", str(before), str(after)])
    assert code == engine_gate.EXIT_CANNOT_RUN
    assert code != engine_gate.EXIT_DRIFT


def test_a_real_difference_outranks_an_inconclusive_one():
    """A probe that WAS readable and now errors is the strongest continuity
    signal this subsystem produces; it must not be routed into "look again".
    """
    before = _snap({"a": {"value": 1}, "b": {"error": "x"}})
    after = _snap({"a": {"value": 2}, "b": {"error": "x"}}, build="firefox-21")
    code, _ = engine_gate.gate(before, after)
    assert code == engine_gate.EXIT_DRIFT


# --- the self-test: prove the comparator is awake on every run --------------


def test_self_test_proves_both_defects_are_caught():
    proven = engine_gate.self_test(_snap())
    assert len(proven) == 2


def test_self_test_refuses_a_recording_with_nothing_readable():
    """With no obtained reading anywhere, the comparator cannot be shown to
    work, so its verdict cannot be trusted.
    """
    errored = {"navigator.userAgent": {"error": "boom"}}
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.self_test(_snap(errored, errored))
    assert "cannot be shown to work" in str(exc.value)


def test_the_planted_defects_do_not_mutate_the_real_recording():
    """The self-test perturbs COPIES. If it mutated the recording in place, the
    gate would then compare the doctored document and report phantom drift.
    """
    snap = _snap()
    original = json.dumps(snap, sort_keys=True)
    engine_gate.self_test(snap)
    assert json.dumps(snap, sort_keys=True) == original


def test_plant_moved_reading_changes_exactly_one_probe():
    snap = _snap()
    planted = engine_gate.plant_moved_reading(snap, "window", "navigator.userAgent")
    assert planted["probes"]["window"]["navigator.userAgent"] != snap["probes"][
        "window"
    ]["navigator.userAgent"]
    assert planted["probes"]["worker"] == snap["probes"]["worker"]


def test_plant_absent_probe_removes_exactly_one_probe():
    snap = _snap()
    planted = engine_gate.plant_absent_probe(snap, "window", "navigator.userAgent")
    assert "navigator.userAgent" not in planted["probes"]["window"]
    assert "navigator.userAgent" in snap["probes"]["window"]


def test_the_self_test_only_perturbs_a_probe_that_was_actually_read():
    """Perturbing an entry NEITHER side could read produces an `inconclusive`
    entry rather than a `changed` one, and the self-test would then fail for a
    reason that has nothing to do with the comparator working.
    """
    snap = _snap({"unread": {"error": "boom"}, "read": {"value": 1}})
    realm, probe_id = engine_gate._readable_probes(snap)[0]
    assert (realm, probe_id) != ("window", "unread")


def test_a_gate_whose_self_test_fails_certifies_nothing(monkeypatch):
    """If the comparator stops detecting movement, the gate must refuse rather
    than report the green it would otherwise compute.
    """
    monkeypatch.setattr(engine_gate, "diff_snapshots", lambda *a, **k: [])
    with pytest.raises(engine_gate.GateCannotRun) as exc:
        engine_gate.gate(*_pair())
    assert "SELF-TEST FAILED" in str(exc.value)


# --- refusing a non-snapshot (PS-41) ----------------------------------------


@pytest.mark.parametrize("junk", [[1, 2, 3], None, "hello", {}, {"probes": None}])
def test_a_non_snapshot_is_refused_rather_than_compared(junk):
    """With no probes on one side every probe diffs as added/removed and the
    gate would print a confident maximum-alarm DRIFT for a comparison that
    never happened. No traceback, so it reads as a real answer.
    """
    with pytest.raises(Exception) as exc:
        engine_gate.gate(_snap(), junk)
    assert not isinstance(exc.value, AssertionError)


# --- THE RE-RECORD TRAP, closed structurally --------------------------------


def test_the_gate_module_never_references_the_committed_artifact():
    """Structural, not conventional. `baseline_cli`'s --output default IS the
    committed reference, so any path from this module to that constant is a way
    for a CI invocation to overwrite the artifact it is supposed to defend.
    """
    assert not hasattr(engine_gate, "BASELINE_ARTIFACT")
    source = __import__("inspect").getsource(engine_gate)
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # Allowed in prose (the docstring explains the trap); never as a symbol.
    assert "BASELINE_ARTIFACT," not in code
    assert "import BASELINE_ARTIFACT" not in code
    assert "baseline.BASELINE_ARTIFACT" not in code


def test_record_has_no_default_output_and_requires_one():
    """`record` must not inherit baseline_cli's default, which is the committed
    reference itself.
    """
    parser = engine_gate.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["record"])


def test_the_gate_exposes_no_accept_or_bless_subcommand():
    """Accepting a move stays a deliberate, reviewable human act. A job that can
    re-record its own reference manufactures evidence of continuity across
    exactly the event it exists to police.
    """
    parser = engine_gate.build_parser()
    for forbidden in ("accept", "bless", "update", "rerecord", "re-record"):
        with pytest.raises(SystemExit):
            parser.parse_args([forbidden])


# --- the CLI: exit codes are the contract CI reads --------------------------


def _write(tmp_path, name, doc):
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_cli_compare_exits_0_on_a_clean_bump(tmp_path):
    before, after = _pair()
    code = engine_gate.main(
        ["compare", _write(tmp_path, "b.json", before), _write(tmp_path, "a.json", after)]
    )
    assert code == engine_gate.EXIT_PASS


def test_cli_compare_exits_1_on_drift(tmp_path):
    before, after = _pair(after_window={"navigator.userAgent": {"value": "X"}})
    code = engine_gate.main(
        ["compare", _write(tmp_path, "b.json", before), _write(tmp_path, "a.json", after)]
    )
    assert code == engine_gate.EXIT_DRIFT


def test_cli_compare_exits_2_when_both_sides_are_the_same_engine(tmp_path):
    code = engine_gate.main(
        [
            "compare",
            _write(tmp_path, "b.json", _snap()),
            _write(tmp_path, "a.json", _snap()),
        ]
    )
    assert code == engine_gate.EXIT_CANNOT_RUN


def test_cli_compare_exits_2_on_a_missing_file(tmp_path):
    """A typo'd path must not read as drift: 1 is the DRIFT code."""
    code = engine_gate.main(
        ["compare", _write(tmp_path, "b.json", _snap()), str(tmp_path / "nope.json")]
    )
    assert code == engine_gate.EXIT_CANNOT_RUN


def test_cli_compare_exits_2_on_a_valid_json_non_snapshot(tmp_path):
    code = engine_gate.main(
        [
            "compare",
            _write(tmp_path, "b.json", _snap()),
            _write(tmp_path, "a.json", [1, 2, 3]),
        ]
    )
    assert code == engine_gate.EXIT_CANNOT_RUN


def test_a_refusal_never_prints_a_pass(tmp_path, capsys):
    engine_gate.main(
        [
            "compare",
            _write(tmp_path, "b.json", _snap()),
            _write(tmp_path, "a.json", _snap()),
        ]
    )
    out = capsys.readouterr()
    assert "PASS" not in out.out


def test_cli_record_writes_the_recording(tmp_path, monkeypatch, provisioned):
    monkeypatch.setattr(engine_gate, "record_snapshot", lambda **kw: _snap())
    out = tmp_path / "rec.json"
    assert engine_gate.main(["record", "-o", str(out)]) == engine_gate.EXIT_PASS
    assert json.loads(out.read_text())["engine_build"] == "firefox-20"


def test_cli_record_without_a_display_exits_2(tmp_path, monkeypatch, provisioned):
    """"Could not run" — never drift, and never a silent success."""

    def _no_display(**kw):
        raise engine_gate.BaselineUnavailable("no DISPLAY")

    monkeypatch.setattr(engine_gate, "record_snapshot", _no_display)
    code = engine_gate.main(["record", "-o", str(tmp_path / "rec.json")])
    assert code == engine_gate.EXIT_CANNOT_RUN


def test_cli_record_keeps_a_reading_that_has_errors(tmp_path, monkeypatch, provisioned):
    """Errors are not fatal on ONE side: whether an unread probe matters is a
    question only the comparison can answer, and it answers it as inconclusive
    rather than as agreement.
    """
    monkeypatch.setattr(
        engine_gate, "record_snapshot", lambda **kw: _snap({"p": {"error": "x"}})
    )
    out = tmp_path / "rec.json"
    assert engine_gate.main(["record", "-o", str(out)]) == engine_gate.EXIT_PASS
    assert out.exists()


# --- the three exit codes are distinct and mean one thing each --------------


def test_the_three_exit_codes_are_distinct():
    codes = {
        engine_gate.EXIT_PASS,
        engine_gate.EXIT_DRIFT,
        engine_gate.EXIT_CANNOT_RUN,
    }
    assert len(codes) == 3
    assert engine_gate.EXIT_PASS == 0  # only 0 lets the bump proceed


# --- an UNREADABLE recording is a refusal, never drift ----------------------
#
# Exit 1 out of this module means one thing: the engine moved what a site sees.
# It is the loudest signal this subsystem produces, and the remedy an operator
# reaches for on a genuine red (investigate the fingerprint, re-record, refuse
# the bump) is not the remedy for a corrupt artifact or a mistyped path.
#
# The two recordings are written by a SEPARATE `record` step and the epilog
# requires them re-recorded per-runner in the same job, which is exactly the
# configuration where a partial write or a wrong path happens. So a file that
# was never successfully read must refuse on exit 2 and must never be reported
# as the engine having moved.
#
# THE FIXTURES ARE REAL BYTES ON A REAL FILESYSTEM, and the directory case is a
# real directory. What reaches this arm is a property of `open()` and
# `json.load`, so a raised stub would let the guard be "tested" against an
# exception those two never actually produce.


def _unreadable(tmp_path, kind):
    """One genuinely unreadable recording of each class, built from real bytes."""
    if kind == "binary":
        # Non-UTF-8 bytes: UnicodeDecodeError, which IS a ValueError but is NOT
        # a JSONDecodeError — the case a narrower guard silently misses.
        path = tmp_path / "binary.json"
        path.write_bytes(b"\xff\xfe\x00\x81\x82\x83 not utf-8 at all")
    elif kind == "invalid_json":
        # A proxy/CDN error page saved under a .json name.
        path = tmp_path / "invalid.json"
        path.write_text("<html><body>502 Bad Gateway</body></html>", encoding="utf-8")
    elif kind == "empty":
        path = tmp_path / "empty.json"
        path.write_bytes(b"")
    elif kind == "truncated":
        # A recording killed mid-write — the per-runner failure this gate's own
        # epilog makes likely.
        path = tmp_path / "truncated.json"
        path.write_text('{"schema_version": 1, "engine_build": "firef', encoding="utf-8")
    elif kind == "directory":
        # An OSError (IsADirectoryError) and NOT a ValueError. This is the case
        # that proves the guard's width: a fix written only to the corrupt-bytes
        # story passes every row above and still tracebacks on this one.
        path = tmp_path / "a_directory"
        path.mkdir()
    else:  # pragma: no cover - guards the parametrisation itself
        raise AssertionError(f"unknown fixture kind {kind!r}")
    return str(path)


UNREADABLE_KINDS = ["binary", "invalid_json", "empty", "truncated", "directory"]


@pytest.mark.parametrize("kind", UNREADABLE_KINDS)
@pytest.mark.parametrize("position", ["before", "after"])
def test_an_unreadable_recording_is_cannot_run_not_drift(tmp_path, kind, position):
    """Exit 2, and specifically NOT 1, for every class of file that cannot be read.

    Both argument positions, because there are two `load()` calls and guarding
    only the first leaves the `after` recording tracebacking out identically.
    """
    good = _write(tmp_path, "good.json", _snap())
    bad = _unreadable(tmp_path, kind)
    argv = [bad, good] if position == "before" else [good, bad]

    code = engine_gate.main(["compare", *argv])

    assert code == engine_gate.EXIT_CANNOT_RUN
    # The whole point of the ticket: 1 is the DRIFT code and this is not drift.
    assert code != engine_gate.EXIT_DRIFT


@pytest.mark.parametrize("kind", UNREADABLE_KINDS)
@pytest.mark.parametrize("position", ["before", "after"])
def test_an_unreadable_recording_does_not_traceback(tmp_path, kind, position):
    """It must REFUSE, not crash. An escaping exception is what produced the
    false drift signal in the first place: an unhandled raise out of `main()`
    exits 1, which this module documents as "the engine moved what a site sees".
    """
    good = _write(tmp_path, "good.json", _snap())
    bad = _unreadable(tmp_path, kind)
    argv = [bad, good] if position == "before" else [good, bad]

    # No pytest.raises: any exception escaping here fails the test, which is
    # precisely the behaviour being pinned.
    assert engine_gate.main(["compare", *argv]) == engine_gate.EXIT_CANNOT_RUN


@pytest.mark.parametrize("kind", UNREADABLE_KINDS)
@pytest.mark.parametrize("position", ["before", "after"])
def test_the_unreadable_refusal_names_the_file_and_denies_drift(
    tmp_path, kind, position, capsys
):
    """An operator has to be able to act on it: WHICH file, and the explicit
    statement that nothing was compared so this is not a drift finding.
    """
    good = _write(tmp_path, "good.json", _snap())
    bad = _unreadable(tmp_path, kind)
    argv = [bad, good] if position == "before" else [good, bad]

    engine_gate.main(["compare", *argv])
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    # It must name the file the operator typed. Literally — `quote_path` exists
    # so a Windows path is not repr()-escaped into a path that does not exist.
    assert bad in combined
    # It must say which of the two arguments was the bad one.
    assert position in combined
    assert "NOT drift" in combined
    assert "Nothing was compared" in combined
    # And it must never print the gate's agreement signal for a comparison that
    # never happened.
    assert "PASS" not in captured.out


@pytest.mark.parametrize("kind", UNREADABLE_KINDS)
def test_an_unreadable_recording_is_refused_even_against_a_drifting_pair(
    tmp_path, kind
):
    """The refusal OUTRANKS a verdict it could otherwise have computed.

    Without this, a test could pass merely because the readable side happened
    to agree. Here the readable side is one half of a genuinely DRIFTING pair,
    so the only way to reach exit 1 is to have read both files — and the only
    honest answer is still the refusal.
    """
    before, after = _pair(after_window={"navigator.userAgent": {"value": "X"}})
    good = _write(tmp_path, "b.json", before)
    bad = _unreadable(tmp_path, kind)

    assert engine_gate.main(["compare", good, bad]) == engine_gate.EXIT_CANNOT_RUN


# --- the controls: this change must not move any of them --------------------


def test_a_missing_recording_still_refuses_in_its_own_words(tmp_path, capsys):
    """CONTROL, not a target. `FileNotFoundError` is an `OSError`, so the new
    guard could have swallowed the absent-file case and re-labelled it "could
    not be read". An absent recording and a corrupt one are different operator
    problems and keep their own messages.
    """
    code = engine_gate.main(
        ["compare", _write(tmp_path, "b.json", _snap()), str(tmp_path / "nope.json")]
    )
    assert code == engine_gate.EXIT_CANNOT_RUN
    combined = "".join(capsys.readouterr())
    assert "nothing is certified" in combined
    # The unreadable-guard's wording must NOT have taken over this arm.
    assert "could not be read" not in combined


def test_a_valid_json_non_snapshot_still_refuses_via_not_a_snapshot(tmp_path, capsys):
    """CONTROL. `NotASnapshot` subclasses `ValueError`, so a guard placed around
    `gate()` instead of around the two `load()` calls would catch a file that
    read back perfectly well and mislabel it as unreadable. It parsed fine; it
    simply is not a snapshot, and it keeps saying so.
    """
    code = engine_gate.main(
        [
            "compare",
            _write(tmp_path, "b.json", _snap()),
            _write(tmp_path, "a.json", [1, 2, 3]),
        ]
    )
    assert code == engine_gate.EXIT_CANNOT_RUN
    combined = "".join(capsys.readouterr())
    assert "is not a snapshot" in combined
    assert "could not be read" not in combined


def test_the_guard_leaves_a_genuine_drift_and_a_genuine_pass_alone(tmp_path):
    """CONTROL. The refusal must not have cost the gate its other two verdicts:
    a real difference is still 1 and a real clean bump is still 0.
    """
    before, after = _pair(after_window={"navigator.userAgent": {"value": "X"}})
    assert (
        engine_gate.main(
            ["compare", _write(tmp_path, "db.json", before), _write(tmp_path, "da.json", after)]
        )
        == engine_gate.EXIT_DRIFT
    )

    clean_before, clean_after = _pair()
    assert (
        engine_gate.main(
            [
                "compare",
                _write(tmp_path, "pb.json", clean_before),
                _write(tmp_path, "pa.json", clean_after),
            ]
        )
        == engine_gate.EXIT_PASS
    )
