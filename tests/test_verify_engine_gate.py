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


def _snap(window=None, worker=None, *, build="firefox-20"):
    """A minimal snapshot document shaped like the real one."""
    return {
        "schema_version": 1,
        "engine": "firefox",
        "engine_build": build,
        "profile": "persona-fingerprint-baseline",
        "app_version": "9.9.9",
        "realms": ["window", "worker"],
        "probes": {
            "window": dict(window or {"navigator.userAgent": {"value": "FF"}}),
            "worker": dict(worker or {"navigator.userAgent": {"value": "FF"}}),
        },
    }


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


def test_cli_record_writes_the_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_gate, "record_snapshot", lambda **kw: _snap())
    out = tmp_path / "rec.json"
    assert engine_gate.main(["record", "-o", str(out)]) == engine_gate.EXIT_PASS
    assert json.loads(out.read_text())["engine_build"] == "firefox-20"


def test_cli_record_without_a_display_exits_2(tmp_path, monkeypatch):
    """"Could not run" — never drift, and never a silent success."""

    def _no_display(**kw):
        raise engine_gate.BaselineUnavailable("no DISPLAY")

    monkeypatch.setattr(engine_gate, "record_snapshot", _no_display)
    code = engine_gate.main(["record", "-o", str(tmp_path / "rec.json")])
    assert code == engine_gate.EXIT_CANNOT_RUN


def test_cli_record_keeps_a_reading_that_has_errors(tmp_path, monkeypatch):
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
