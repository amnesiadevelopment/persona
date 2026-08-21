"""Tests for src/services/verify — the in-browser observation primitive.

Driven entirely by a FAKE ``evaluate``. There is no browser here and there must
not be one: ``playwright`` is a git-pinned dependency that is not importable in
this container, which is exactly why ``transport.py`` keeps its playwright
import function-local and why nothing in this file imports ``transport`` at
module scope.

What these tests pin is the machinery's contract, not any particular
fingerprint value: canonical ordering, byte-stability, that a throwing probe is
recorded rather than dropped, and that the differ reports added/removed probes
instead of skipping them.
"""

import copy
import json
import re
import sys

import pytest

from src.services.verify import diff, probes, runner, snapshot

# The marker every wrapper embeds, so a fake evaluate can tell which probe an
# expression belongs to without parsing JavaScript.
_MARKER_RE = re.compile(r"/\*probe:(.+?)\*/")


def _ids_in(expression):
    return _MARKER_RE.findall(expression)


def _fake_evaluate(window_values=None, worker_values=None, harness_error=None):
    """Build an ``evaluate`` that answers wrapper expressions from canned data.

    ``*_values`` map probe id -> the value to report, or an ``Exception``
    instance to report as a thrown error. Any probe not named gets a default.
    """
    window_values = window_values or {}
    worker_values = worker_values or {}

    def reply(value):
        if isinstance(value, BaseException):
            return {"e": f"{type(value).__name__}: {value}"}
        return {"v": value}

    def evaluate(expression):
        ids = _ids_in(expression)
        assert ids, "wrapper carried no probe marker"
        if len(ids) == 1 and "new Worker" not in expression:
            probe_id = ids[0]
            return reply(window_values.get(probe_id, f"window:{probe_id}"))
        # worker harness: one expression carrying every worker-eligible probe
        if harness_error is not None:
            return {"__harness_error": harness_error}
        return {
            probe_id: reply(worker_values.get(probe_id, f"worker:{probe_id}"))
            for probe_id in ids
        }

    return evaluate


def _run(**kwargs):
    return runner.run_probes(_fake_evaluate(**kwargs), (probes.WINDOW, probes.WORKER))


def _snapshot(**kwargs):
    return snapshot.build_snapshot(
        _run(**kwargs),
        engine="chromium",
        profile="acc",
        realms=(probes.WINDOW, probes.WORKER),
        version="9.9.9",
    )


# --- the inventory ----------------------------------------------------------


def test_every_probe_declares_at_least_one_realm():
    assert probes.PROBES, "the inventory must not be empty"
    for probe in probes.PROBES:
        assert probe.realms, f"{probe.id} declares no realm"
        assert set(probe.realms) <= set(probes.ALL_REALMS)


def test_probe_rejects_an_empty_realm_tuple():
    # Enforced in __post_init__ so an inventory edit cannot add a probe that is
    # never evaluated anywhere.
    with pytest.raises(ValueError):
        probes.Probe("x", (), "1")


def test_probe_ids_are_unique():
    ids = [p.id for p in probes.PROBES]
    assert len(ids) == len(set(ids))


def test_worker_realm_is_actually_populated():
    # Worker probing is first-wave, not a refinement: the worker realm is where
    # persona's spoofs have historically gone missing.
    assert len(probes.probes_for_realm(probes.WORKER)) >= 10


def test_no_probe_reads_a_live_clock():
    # A `new Date()` with no arguments would make every snapshot differ from
    # every other snapshot and destroy byte-stability at the source.
    for probe in probes.PROBES:
        assert "new Date()" not in probe.expr, probe.id
        assert "Date.now()" not in probe.expr, probe.id
        assert "Math.random" not in probe.expr, probe.id


# --- the runner -------------------------------------------------------------


def test_run_probes_covers_every_probe_in_every_requested_realm():
    results = _run()
    for realm in (probes.WINDOW, probes.WORKER):
        expected = {p.id for p in probes.probes_for_realm(realm)}
        assert set(results[realm]) == expected


def test_window_realm_probe_absent_from_worker_realm_results():
    results = _run()
    window_only = [p for p in probes.PROBES if probes.WORKER not in p.realms]
    assert window_only, "inventory should have at least one window-only probe"
    for probe in window_only:
        assert probe.id not in results[probes.WORKER]


def test_run_probes_rejects_an_unknown_realm():
    with pytest.raises(ValueError):
        runner.run_probes(_fake_evaluate(), ("window", "iframe"))


def test_a_throwing_probe_is_recorded_as_an_error_not_omitted():
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    results = _run(window_values={target: TypeError("nope")})
    assert results[probes.WINDOW][target] == {"error": "TypeError: nope"}
    # and every other probe still produced a value
    others = [v for k, v in results[probes.WINDOW].items() if k != target]
    assert all("value" in v for v in others)


def test_a_transport_failure_on_one_probe_does_not_sink_the_run():
    target = probes.probes_for_realm(probes.WINDOW)[1].id

    def evaluate(expression):
        ids = _ids_in(expression)
        if ids == [target]:
            raise RuntimeError("channel hiccup")
        if len(ids) == 1:
            return {"v": "ok"}
        return {pid: {"v": "ok"} for pid in ids}

    results = runner.run_probes(evaluate, (probes.WINDOW,))
    assert results[probes.WINDOW][target] == {"error": "RuntimeError: channel hiccup"}
    assert sum(1 for v in results[probes.WINDOW].values() if "error" in v) == 1


def test_worker_harness_failure_marks_every_worker_probe_inconclusive():
    results = _run(harness_error="TypeError: Worker is not available in this realm")
    worker = results[probes.WORKER]
    assert worker, "worker realm must still be present"
    for probe_id, entry in worker.items():
        assert "error" in entry, probe_id
        assert "Worker is not available" in entry["error"]
    # A harness failure must never leak into the window realm's readings.
    assert all("value" in v for v in results[probes.WINDOW].values())


def test_a_probe_the_worker_never_answered_is_reported_as_missing():
    dropped = probes.probes_for_realm(probes.WORKER)[0].id

    def evaluate(expression):
        ids = _ids_in(expression)
        if len(ids) == 1 and "new Worker" not in expression:
            return {"v": "w"}
        return {pid: {"v": "k"} for pid in ids if pid != dropped}

    # run_worker_realm directly, NOT through run_probes: run_probes has its own
    # completeness net, and going through it would let either layer alone pass
    # this test while the other silently rotted.
    out = runner.run_worker_realm(evaluate)
    assert "MissingResult" in out[dropped]["error"]
    assert set(out) == {p.id for p in probes.probes_for_realm(probes.WORKER)}


def test_run_probes_backfills_a_realm_runner_that_returned_nothing(monkeypatch):
    # The outer net, pinned on its own: whatever a realm runner did or failed to
    # do, the INVENTORY decides the key set that comes back.
    monkeypatch.setitem(runner._REALM_RUNNERS, probes.WINDOW, lambda _evaluate: {})
    results = runner.run_probes(_fake_evaluate(), (probes.WINDOW,))
    assert set(results[probes.WINDOW]) == {
        p.id for p in probes.probes_for_realm(probes.WINDOW)
    }
    assert all("MissingResult" in e["error"] for e in results[probes.WINDOW].values())


def test_a_malformed_reply_is_an_error_not_a_value():
    target = probes.probes_for_realm(probes.WINDOW)[0].id

    def evaluate(expression):
        ids = _ids_in(expression)
        if ids == [target]:
            return "just a string"
        return {"v": 1} if len(ids) == 1 else {pid: {"v": 1} for pid in ids}

    results = runner.run_probes(evaluate, (probes.WINDOW,))
    assert "ProtocolError" in results[probes.WINDOW][target]["error"]


def test_worker_harness_is_one_call_carrying_every_worker_probe():
    seen = []

    def evaluate(expression):
        seen.append(expression)
        return {pid: {"v": 1} for pid in _ids_in(expression)}

    runner.run_probes(evaluate, (probes.WORKER,))
    assert len(seen) == 1
    assert set(_ids_in(seen[0])) == {
        p.id for p in probes.probes_for_realm(probes.WORKER)
    }


def test_worker_expression_terminates_the_worker_on_every_path():
    expr = runner.worker_expression(probes.probes_for_realm(probes.WORKER))
    # Cleanup hangs off a promise `finally`, so it runs on success, on error
    # AND on the timeout path — nothing outlives the run.
    assert "['finally']" in expr
    assert "w.terminate()" in expr
    assert "revokeObjectURL" in expr
    assert "TimeoutError" in expr


def test_probe_expressions_are_embedded_verbatim_in_both_realms():
    # The SAME probe record drives both realms — no per-realm reimplementation.
    both = [p for p in probes.PROBES if set(p.realms) == set(probes.ALL_REALMS)]
    assert both
    probe = both[0]
    assert probe.expr in runner.window_expression(probe)
    assert probe.expr in runner.worker_source([probe])


# --- canonicalisation -------------------------------------------------------


def test_snapshot_records_every_inventory_probe_with_a_value_or_an_error():
    snap = _snapshot(window_values={probes.PROBES[0].id: ValueError("x")})
    for realm in snap["realms"]:
        expected = {p.id for p in probes.probes_for_realm(realm)}
        assert set(snap["probes"][realm]) == expected
        for probe_id, entry in snap["probes"][realm].items():
            assert set(entry) in ({"value"}, {"error"}), probe_id


def test_snapshot_fills_in_a_probe_the_runner_dropped_entirely():
    results = _run()
    victim = probes.probes_for_realm(probes.WINDOW)[0].id
    del results[probes.WINDOW][victim]
    snap = snapshot.build_snapshot(
        results, engine="firefox", profile="acc", realms=(probes.WINDOW,)
    )
    assert "MissingResult" in snap["probes"][probes.WINDOW][victim]["error"]


def test_two_runs_over_identical_input_are_byte_identical():
    a = snapshot.dumps(_snapshot())
    b = snapshot.dumps(_snapshot())
    assert a == b
    assert a.encode("utf-8") == b.encode("utf-8")


def test_a_snapshot_carries_no_timestamp():
    # A clock reading would make every snapshot differ from every other one.
    text = snapshot.dumps(_snapshot())
    for banned in ("timestamp", "recorded_at", "created_at", "generated_at"):
        assert banned not in text


def test_snapshot_header_pins_schema_engine_profile_and_version():
    snap = _snapshot()
    assert snap["schema_version"] == snapshot.SCHEMA_VERSION
    assert snap["engine"] == "chromium"
    assert snap["profile"] == "acc"
    assert snap["app_version"] == "9.9.9"
    assert snap["realms"] == [probes.WINDOW, probes.WORKER]


def test_keys_are_sorted_at_every_depth():
    nested = {"z": 1, "a": {"y": 2, "b": [{"d": 4, "c": 3}]}}
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    text = snapshot.dumps(_snapshot(window_values={target: nested}))
    # json.loads with object_pairs_hook lets us assert the ON-DISK order.
    orders = []
    json.loads(text, object_pairs_hook=lambda pairs: orders.append([k for k, _ in pairs]) or dict(pairs))
    for keys in orders:
        assert keys == sorted(keys)


def test_input_key_order_does_not_change_the_bytes():
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    one = snapshot.dumps(_snapshot(window_values={target: {"b": 1, "a": 2}}))
    two = snapshot.dumps(_snapshot(window_values={target: {"a": 2, "b": 1}}))
    assert one == two


def test_floats_are_rounded_to_the_pinned_precision():
    assert snapshot.canonicalise(1.23456789) == round(1.23456789, snapshot.FLOAT_PRECISION)
    # Two readings that differ only below the pinned precision must collapse to
    # the same value, or a stable profile would look like a changing one.
    assert snapshot.canonicalise(0.1234567891) == snapshot.canonicalise(0.1234567892)


def test_negative_zero_is_folded_so_it_cannot_flip_the_bytes():
    assert snapshot.canonicalise(-0.0) == 0.0
    assert "-0.0" not in json.dumps(snapshot.canonicalise({"x": -0.0}))


def test_non_finite_floats_become_stable_strings_not_invalid_json():
    assert snapshot.canonicalise(float("nan")) == "NaN"
    assert snapshot.canonicalise(float("inf")) == "Infinity"
    assert snapshot.canonicalise(float("-inf")) == "-Infinity"
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    text = snapshot.dumps(_snapshot(window_values={target: [float("nan")]}))
    assert json.loads(text)["probes"][probes.WINDOW][target]["value"] == ["NaN"]


def test_booleans_survive_canonicalisation_as_booleans():
    # bool is an int in Python; a naive numeric branch would turn True into 1.
    assert snapshot.canonicalise({"t": True, "f": False}) == {"t": True, "f": False}


def test_an_unserialisable_value_is_described_not_crashed_on():
    assert snapshot.canonicalise(object()).startswith("<unserialisable object")


def test_a_reading_for_a_retired_probe_is_kept_not_silently_dropped():
    results = _run()
    results[probes.WINDOW]["legacy.retired"] = {"value": 1}
    snap = snapshot.build_snapshot(
        results, engine="chromium", profile="acc", realms=(probes.WINDOW,)
    )
    assert snap["probes"][probes.WINDOW]["legacy.retired"] == {"value": 1}


def test_snapshot_round_trips_through_a_file(tmp_path):
    snap = _snapshot()
    path = tmp_path / "snap.json"
    snapshot.write(snap, str(path))
    assert path.read_text(encoding="utf-8") == snapshot.dumps(snap)
    assert snapshot.load(str(path)) == snap


# --- the differ -------------------------------------------------------------


def test_identical_snapshots_diff_to_nothing():
    # The continuity claim: the same profile, observed twice, reads the same.
    assert diff.diff_snapshots(_snapshot(), _snapshot()) == []
    assert diff.format_diff([]) == "no differences"


def test_a_changed_probe_is_reported_with_both_readings():
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    entries = diff.diff_snapshots(
        _snapshot(window_values={target: "before"}),
        _snapshot(window_values={target: "after"}),
    )
    assert len(entries) == 1
    assert entries[0] == {
        "probe_id": target,
        "realm": probes.WINDOW,
        "status": diff.CHANGED,
        "expected": {"value": "before"},
        "observed": {"value": "after"},
    }


def test_a_probe_only_in_the_new_snapshot_is_reported_as_added():
    a = _snapshot()
    b = _snapshot()
    b["probes"][probes.WINDOW]["brand.new"] = {"value": 1}
    entries = diff.diff_snapshots(a, b)
    assert [e["status"] for e in entries] == [diff.ADDED]
    assert entries[0]["expected"] == diff.ABSENT
    assert entries[0]["observed"] == {"value": 1}


def test_a_probe_missing_from_the_new_snapshot_is_reported_as_removed():
    a = _snapshot()
    b = _snapshot()
    victim = probes.probes_for_realm(probes.WINDOW)[0].id
    del b["probes"][probes.WINDOW][victim]
    entries = diff.diff_snapshots(a, b)
    assert [e["status"] for e in entries] == [diff.REMOVED]
    assert entries[0]["probe_id"] == victim
    assert entries[0]["observed"] == diff.ABSENT


def test_a_probe_that_became_an_error_is_a_difference_not_a_pass():
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    entries = diff.diff_snapshots(
        _snapshot(),
        _snapshot(window_values={target: RuntimeError("gone")}),
    )
    assert len(entries) == 1
    assert entries[0]["observed"] == {"error": "RuntimeError: gone"}


def test_a_realm_present_in_only_one_snapshot_is_reported():
    a = _snapshot()
    b = snapshot.build_snapshot(
        _run(), engine="chromium", profile="acc", realms=(probes.WINDOW,), version="9.9.9"
    )
    entries = diff.diff_snapshots(a, b)
    assert entries, "dropping the whole worker realm must not read as agreement"
    assert {e["realm"] for e in entries} == {probes.WORKER}
    assert all(e["status"] == diff.REMOVED for e in entries)


def test_diff_entries_are_ordered_by_realm_then_probe_id():
    a = _snapshot()
    b = _snapshot()
    for realm in (probes.WINDOW, probes.WORKER):
        for probe_id in list(b["probes"][realm])[:4]:
            b["probes"][realm][probe_id] = {"value": "changed"}
    entries = diff.diff_snapshots(a, b)
    keys = [(e["realm"], e["probe_id"]) for e in entries]
    assert keys == sorted(keys)


def test_header_disagreements_are_opt_in():
    a = _snapshot()
    b = _snapshot()
    b["engine"] = "firefox"
    assert diff.diff_snapshots(a, b) == []
    meta = diff.diff_snapshots(a, b, include_meta=True)
    assert meta == [
        {
            "probe_id": "engine",
            "realm": diff.META_REALM,
            "status": diff.CHANGED,
            "expected": "chromium",
            "observed": "firefox",
        }
    ]


def test_realm_disagreement_is_reported_by_the_differ():
    # A vector persona spoofs should read the same in the window realm and in a
    # Worker. When it doesn't, that is the historically load-bearing defect —
    # surfaced here, fixed elsewhere.
    shared = [p for p in probes.PROBES if set(p.realms) == set(probes.ALL_REALMS)][0]
    snap = _snapshot(
        window_values={shared.id: "desktop"}, worker_values={shared.id: "leaked"}
    )
    entries = diff.diff_realms(snap, probes.WINDOW, probes.WORKER)
    ids = {e["probe_id"] for e in entries}
    assert shared.id in ids
    entry = next(e for e in entries if e["probe_id"] == shared.id)
    assert entry["expected"] == {"value": "desktop"}
    assert entry["observed"] == {"value": "leaked"}


def test_realm_comparison_ignores_probes_only_one_realm_declares():
    snap = _snapshot(window_values={}, worker_values={})
    entries = diff.diff_realms(snap, probes.WINDOW, probes.WORKER)
    window_only = {p.id for p in probes.PROBES if probes.WORKER not in p.realms}
    assert not ({e["probe_id"] for e in entries} & window_only)


def test_format_diff_renders_every_entry():
    entries = diff.diff_snapshots(
        _snapshot(), _snapshot(window_values={probes.PROBES[0].id: "x"})
    )
    text = diff.format_diff(entries)
    assert probes.PROBES[0].id in text
    assert "expected:" in text and "observed:" in text


# --- an unobtained reading is INCONCLUSIVE, never agreement ------------------
# The department's rule — an unobtainable reading is inconclusive, and
# inconclusive is never a pass — is honoured at probe granularity by
# runner/snapshot (a throwing probe is recorded as {"error": ...}, never
# omitted, never coerced to a value) and was then LOST here: the comparator
# compared entries verbatim, so {"error": "X"} == {"error": "X"} counted as
# agreement. Two failed readings rendered as "no differences" and exited 0 —
# a claim of safety resting on evidence nobody gathered.
#
# The rule these pin is ONE rule, not a special case, and it turns on whether a
# reading was OBTAINED — not on whether an error is present anywhere:
#
#   * NEITHER side obtained (both errored, or both absent) — INCONCLUSIVE. No
#     evidence was gathered, so there was no comparison to make, and identical
#     errors are the same probe failing twice rather than two probes agreeing.
#   * At least ONE side obtained — the ordinary comparison. In particular the
#     ASYMMETRIC case (read before, throws now) is CHANGED, NOT inconclusive:
#     one side WAS read, and a vector that stopped being readable is the
#     loudest continuity signal here. Demoting it to "look again" is the bug —
#     it also demotes the CLI exit code from 1 to 3. Pinned by
#     ..._is_a_DIFFERENCE_not_a_retry (the status) and
#     ..._still_exits_one_and_not_the_inconclusive_code (the exit code itself,
#     which the status guard never reaches).
#   * Present on ONE side only — status stays ADDED/REMOVED, because the
#     inventory change is real information worth naming. But if no reading was
#     obtained for it, `inconclusive_count` still counts it: the inventory
#     moved, no reading did. The count keys off the readings an entry carries,
#     not off its status label.


def _errored(probe_ids, exc=None):
    return {probe_id: exc or TypeError("probe unavailable") for probe_id in probe_ids}


def _all_errored_snapshot():
    """A snapshot in which every single reading failed."""
    return _snapshot(
        window_values=_errored(p.id for p in probes.probes_for_realm(probes.WINDOW)),
        worker_values=_errored(p.id for p in probes.probes_for_realm(probes.WORKER)),
    )


def test_a_probe_errored_on_both_sides_is_inconclusive_not_agreement():
    # The realistic case: 77 vectors read fine, ONE errored on both sides.
    # Verbatim comparison called that agreement and told the operator the
    # identity survived on the exact vector it failed to read.
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    entries = diff.diff_snapshots(
        _snapshot(window_values={target: TypeError("no webgl")}),
        _snapshot(window_values={target: TypeError("no webgl")}),
    )
    assert len(entries) == 1, "an unread vector must not vanish into agreement"
    assert entries[0]["probe_id"] == target
    assert entries[0]["status"] == diff.INCONCLUSIVE
    assert entries[0]["expected"] == {"error": "TypeError: no webgl"}
    assert entries[0]["observed"] == {"error": "TypeError: no webgl"}


def test_two_different_errors_are_inconclusive_not_a_reported_change():
    # The same hole wearing different clothes: two FAILED readings that happen
    # to differ are not evidence the identity moved — nothing was ever read.
    # Reporting them as "changed" overclaims in the opposite direction.
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    entries = diff.diff_snapshots(
        _snapshot(window_values={target: TypeError("a")}),
        _snapshot(window_values={target: ValueError("b")}),
    )
    assert [e["status"] for e in entries] == [diff.INCONCLUSIVE]


def test_a_snapshot_of_nothing_but_errors_does_not_read_as_no_differences():
    # The total-failure case: every reading failed, diffed against itself.
    snap = _all_errored_snapshot()
    entries = diff.diff_snapshots(snap, snap)
    assert entries, "an all-errored snapshot must never diff clean"
    assert {e["status"] for e in entries} == {diff.INCONCLUSIVE}
    # Every recorded reading is accounted for, not just a sampled few.
    assert len(entries) == sum(len(r) for r in snap["probes"].values())
    text = diff.format_diff(entries)
    assert "no differences" not in text


def test_format_diff_names_the_inconclusive_count():
    snap = _all_errored_snapshot()
    total = sum(len(r) for r in snap["probes"].values())
    text = diff.format_diff(diff.diff_snapshots(snap, snap))
    assert str(total) in text and "inconclusive" in text.lower()


def test_diff_realms_reports_a_probe_errored_in_both_realms():
    # The sibling comparator: the window-vs-worker check read an unread vector
    # as the two realms agreeing.
    shared = [p for p in probes.PROBES if set(p.realms) == set(probes.ALL_REALMS)][0]
    snap = _snapshot(
        window_values={shared.id: TypeError("boom")},
        worker_values={shared.id: TypeError("boom")},
    )
    entries = diff.diff_realms(snap, probes.WINDOW, probes.WORKER)
    entry = next((e for e in entries if e["probe_id"] == shared.id), None)
    assert entry is not None, "a vector unread in BOTH realms is not agreement"
    assert entry["status"] == diff.INCONCLUSIVE


def test_a_vector_that_was_readable_and_now_throws_is_a_DIFFERENCE_not_a_retry():
    # The asymmetric case, and the line the whole classification turns on.
    # One side WAS read: "Apple GPU" in the baseline, a throw after the engine
    # update. That is not a failure to look — it is the strongest continuity
    # signal this subsystem can produce, so it must stay CHANGED and must NOT
    # be demoted into the "look again" bucket alongside a probe nobody read.
    target = probes.probes_for_realm(probes.WINDOW)[0].id
    entries = diff.diff_snapshots(
        _snapshot(), _snapshot(window_values={target: RuntimeError("gone")})
    )
    assert len(entries) == 1
    assert entries[0]["status"] == diff.CHANGED
    assert entries[0]["observed"] == {"error": "RuntimeError: gone"}
    # ...and it is not counted as an unobtained reading, because one was.
    assert diff.inconclusive_count(entries) == 0


def test_the_asymmetric_case_still_exits_one_and_not_the_inconclusive_code(tmp_path):
    # The regression this pins: classification is not the operator-facing
    # artifact — the EXIT CODE is. A previous round labelled the asymmetric
    # case inconclusive, which propagated through _exit_code and demoted it
    # from 1 to 3 while the guard above still passed, because that guard never
    # reaches the exit code. Assert the code itself, on a real cli.main call.
    from src.services.verify import cli

    target = probes.probes_for_realm(probes.WINDOW)[0].id
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_snapshot(), str(a))
    snapshot.write(_snapshot(window_values={target: RuntimeError("gone")}), str(b))
    assert cli.main(["diff", str(a), str(b)]) == 1, (
        "a vector that was readable and now throws is 'the identity moved', "
        "not 'we failed to look'"
    )


def test_a_vector_read_in_one_realm_and_unreadable_in_the_other_is_a_difference():
    # diff_realms' half of the same rule. A spoof that reached the window and
    # threw in the worker is the historically load-bearing defect class; it is
    # a disagreement BETWEEN the realms, not an unobtained reading.
    shared = [p for p in probes.PROBES if set(p.realms) == set(probes.ALL_REALMS)][0]
    snap = _snapshot(worker_values={shared.id: TypeError("boom")})
    entry = next(
        e
        for e in diff.diff_realms(snap, probes.WINDOW, probes.WORKER)
        if e["probe_id"] == shared.id
    )
    assert entry["status"] == diff.CHANGED
    assert diff.inconclusive_count([entry]) == 0


def test_the_inconclusive_status_is_exported_beside_the_others():
    from src.services import verify

    assert verify.INCONCLUSIVE == diff.INCONCLUSIVE
    assert "INCONCLUSIVE" in diff.__all__ and "INCONCLUSIVE" in verify.__all__


def test_cli_diff_does_not_exit_zero_when_readings_were_inconclusive(
    tmp_path, capsys
):
    # The operator-facing consequence: exit 0 is a claim that every vector was
    # read AND agreed. An unread vector must not buy that claim.
    from src.services.verify import cli

    snap = _all_errored_snapshot()
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(snap, str(a))
    snapshot.write(snap, str(b))
    code = cli.main(["diff", str(a), str(b)])
    assert code != 0
    assert "no differences" not in capsys.readouterr().out


def test_cli_diff_tells_failed_to_look_apart_from_moved(tmp_path, capsys):
    # A caller must distinguish the two from the exit code ALONE: collapsing
    # them would make "we never looked" indistinguishable from "it drifted".
    from src.services.verify import cli

    target = probes.probes_for_realm(probes.WINDOW)[0].id
    unread = _snapshot(window_values={target: TypeError("no webgl")})
    inc_a, inc_b = tmp_path / "ia.json", tmp_path / "ib.json"
    snapshot.write(unread, str(inc_a))
    snapshot.write(unread, str(inc_b))
    inconclusive_code = cli.main(["diff", str(inc_a), str(inc_b)])

    moved_a, moved_b = tmp_path / "ma.json", tmp_path / "mb.json"
    snapshot.write(_snapshot(), str(moved_a))
    snapshot.write(_snapshot(window_values={target: "drift"}), str(moved_b))
    moved_code = cli.main(["diff", str(moved_a), str(moved_b)])

    assert inconclusive_code != 0 and moved_code != 0
    assert inconclusive_code != moved_code


def test_cli_realms_does_not_exit_zero_when_readings_were_inconclusive(
    tmp_path, capsys
):
    from src.services.verify import cli

    path = tmp_path / "s.json"
    snapshot.write(_all_errored_snapshot(), str(path))
    assert cli.main(["realms", str(path)]) != 0


def test_a_real_difference_still_outranks_an_inconclusive_one(tmp_path, capsys):
    # When a vector MOVED and another was unread, the exit code must still say
    # "moved" — the louder fact — while the report names both.
    from src.services.verify import cli

    window_probes = probes.probes_for_realm(probes.WINDOW)
    moved, unread = window_probes[0].id, window_probes[1].id
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_snapshot(window_values={unread: TypeError("boom")}), str(a))
    snapshot.write(
        _snapshot(window_values={unread: TypeError("boom"), moved: "drift"}), str(b)
    )
    code = cli.main(["diff", str(a), str(b)])
    out = capsys.readouterr().out
    assert code == 1, "a moved vector must still report as moved"
    assert moved in out and unread in out


# --- the engine build in the header -----------------------------------------
# The package's stated workflow (cli.py:1-13) is: record before.json, UPDATE
# THE ENGINE, record after.json, diff. The one variable that workflow turns on
# is which engine build — and `engine` is only the family, so firefox-19 and
# firefox-20 produced byte-identical headers. An EMPTY diff then could not tell
# "the identity survived the update" from "nothing was updated". These pin the
# fact being recorded; they deliberately do not interpret what a change means.


def _fx(monkeypatch, value):
    """Force the firefox accessor to answer `value` (or raise, if it's an
    exception). The resolver from-imports at CALL time, so patching the module
    attribute is what it will actually see."""
    from src.services.engine import firefox

    def current_version():
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(firefox, "current_version", current_version)


def _built(engine="firefox", **kwargs):
    """A snapshot whose engine_build comes from the RESOLVER, not an argument.
    Everything else is fixed, so the build is the only variable."""
    return snapshot.build_snapshot(
        _run(), engine=engine, profile="acc",
        realms=(probes.WINDOW, probes.WORKER), version="9.9.9", **kwargs
    )


def test_the_header_records_the_resolved_engine_build(monkeypatch):
    _fx(monkeypatch, "firefox-20")
    assert _built()["engine_build"] == "firefox-20"


def test_an_injected_build_wins_over_the_resolver(monkeypatch):
    # Mirrors how `version="9.9.9"` is injected: tests need no monkeypatching,
    # and a caller that already knows the build can say so.
    _fx(monkeypatch, "firefox-20")
    assert _built(build="firefox-15")["engine_build"] == "firefox-15"


def test_a_build_change_is_reported_as_exactly_one_meta_disagreement(monkeypatch):
    # AC2. Built through the resolver on both sides, so this goes RED if
    # build_snapshot ever stops resolving the field (see the counterfactual
    # test below) rather than merely if the dict key is missing.
    _fx(monkeypatch, "firefox-19")
    before = _built()
    _fx(monkeypatch, "firefox-20")
    after = _built()

    assert diff.diff_snapshots(before, after, include_meta=True) == [
        {
            "probe_id": "engine_build",
            "realm": diff.META_REALM,
            "status": diff.CHANGED,
            "expected": "firefox-19",
            "observed": "firefox-20",
        }
    ]


def test_a_build_change_alone_is_not_a_probe_difference(monkeypatch):
    # AC3 — the property that keeps AC2 honest: this field is PROVENANCE, not
    # evidence. Every probe agrees, so the default differ must say so.
    _fx(monkeypatch, "firefox-19")
    before = _built()
    _fx(monkeypatch, "firefox-20")
    after = _built()

    assert before["engine_build"] != after["engine_build"]
    assert diff.diff_snapshots(before, after) == []


def test_the_resolver_is_wired_into_build_snapshot_not_merely_defined(monkeypatch):
    # AC4's counterfactual, as an assertion: prove the header field is
    # RESOLVED rather than defaulted. A test that passed whether or not
    # build_snapshot called the resolver would be testing nothing.
    _fx(monkeypatch, "firefox-20")
    assert _built()["engine_build"] == "firefox-20"
    _fx(monkeypatch, "firefox-19")
    assert _built()["engine_build"] == "firefox-19"


def test_a_throwing_accessor_never_destroys_the_run(monkeypatch):
    # AC4. The resolver runs inside document assembly, AFTER the readings have
    # been collected — an exception here would throw away a completed run.
    _fx(monkeypatch, RuntimeError("engine inventory exploded"))
    snap = _built()
    assert snap["engine_build"] == "unknown"
    # and the document is intact, not truncated
    assert snap["probes"][probes.WINDOW]
    assert snap["realms"] == [probes.WINDOW, probes.WORKER]


def test_the_build_resolves_to_unknown_when_the_engine_package_is_absent(monkeypatch):
    # AC5. FORCED rather than ambient: `invisible_playwright` IS importable in
    # this container today, so an env-dependent assertion here would be a flaky
    # test in waiting. None in sys.modules makes the lazy import raise exactly
    # as a bare checkout would.
    monkeypatch.setitem(sys.modules, "src.services.engine.firefox", None)
    assert snapshot.engine_build("firefox") == "unknown"
    assert _built()["engine_build"] == "unknown"


def test_an_uninstalled_engine_is_recorded_as_unknown_never_as_empty(monkeypatch):
    # AC6. Both accessors answer "" when their engine isn't installed. An empty
    # string in the document would read like a value.
    _fx(monkeypatch, "")
    assert snapshot.engine_build("firefox") == "unknown"
    assert _built()["engine_build"] == "unknown"


def test_a_non_string_falsy_reading_is_unknown_not_the_word_None(monkeypatch):
    # Defensive, and NOT reachable through either accessor today (both are
    # annotated `-> str` and answer "" on their failure paths). Pinned because
    # `str(x) or "unknown"` stringifies BEFORE the `or`, so a None reading
    # would bake the literal "None" into a byte-stable artifact -- a non-value
    # that reads like a value, which is the exact failure AC6 exists to
    # prevent. The resolver is guarded everywhere else; this closes the one
    # line that trusted a return type.
    _fx(monkeypatch, None)
    assert snapshot.engine_build("firefox") == "unknown"
    assert _built()["engine_build"] == "unknown"


def test_an_unrecognised_engine_family_resolves_to_unknown():
    assert snapshot.engine_build("safari") == "unknown"
    assert snapshot.engine_build("") == "unknown"


def test_the_engine_build_survives_a_file_round_trip(tmp_path, monkeypatch):
    _fx(monkeypatch, "firefox-20")
    path = tmp_path / "before.json"
    snapshot.write(_built(), str(path))
    assert snapshot.load(str(path))["engine_build"] == "firefox-20"


def test_a_failed_write_leaves_the_previous_artifact_byte_identical(tmp_path):
    """``record``'s default ``-o`` is the COMMITTED baseline, so the write path
    can destroy the reference every future check compares against. A write that
    fails must leave the old artifact exactly as it was — not truncated, not
    half-replaced."""
    path = tmp_path / "ref.json"
    snapshot.write({"probes": {"window": {"a": {"value": 1}}}}, str(path))
    before = path.read_bytes()

    # allow_nan=False: a non-finite float makes dumps() raise, so the failure
    # lands mid-write rather than before it.
    with pytest.raises(ValueError):
        snapshot.write({"probes": {"window": {"a": {"value": float("nan")}}}}, str(path))

    assert path.read_bytes() == before


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path):
    """The atomic promote uses a temp file beside the target. If a failure left
    it there, the baseline's own directory would slowly fill with debris that
    looks like artifacts."""
    path = tmp_path / "ref.json"
    snapshot.write({"probes": {}}, str(path))

    with pytest.raises(ValueError):
        snapshot.write({"probes": {"window": {"a": {"value": float("inf")}}}}, str(path))

    assert [p.name for p in tmp_path.iterdir()] == ["ref.json"]


def test_an_unwritable_target_still_raises_oserror(tmp_path):
    """The CLI maps OSError from this call to "could not run" (exit 2). Going
    through a temp file must not swallow it into some other type, or that
    mapping silently stops working."""
    unwritable = tmp_path / "ro"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    try:
        with pytest.raises(OSError):
            snapshot.write({"probes": {}}, str(unwritable / "x.json"))
    finally:
        unwritable.chmod(0o700)


def test_the_atomic_write_produces_the_same_canonical_bytes(tmp_path):
    """Atomicity is about WHEN the bytes appear, never about what they are: the
    artifact is a byte-stable reference, so the promote must not alter encoding,
    ordering or the trailing newline."""
    path = tmp_path / "a.json"
    payload = {"probes": {"window": {"z": {"value": "ü"}, "a": {"value": 1}}}}
    snapshot.write(payload, str(path))

    assert path.read_bytes() == snapshot.dumps(payload).encode("utf-8")
    assert snapshot.load(str(path)) == payload


def test_the_engine_build_is_a_static_string_not_a_clock(monkeypatch):
    # The module's reason to exist is byte-stability (snapshot.py:1-18). A
    # build tag is admissible precisely because it does not vary with time.
    _fx(monkeypatch, "firefox-20")
    assert snapshot.dumps(_built()) == snapshot.dumps(_built())


# --- the environment trap ---------------------------------------------------


def test_the_package_imports_without_playwright():
    # playwright is a git-pinned dependency that is NOT importable here. A
    # module-level import in transport.py would make the whole package
    # unimportable in exactly the environment this suite runs in.
    import importlib

    importlib.import_module("src.services.verify")
    assert "playwright" not in probes.__dict__


def test_transport_never_imports_playwright_at_module_scope():
    import pathlib

    # Located via the package, not the cwd: another test in the suite chdirs.
    # Read as TEXT rather than imported — the point is what the module does at
    # import time, and asserting on the source proves it without running it.
    source = pathlib.Path(probes.__file__).with_name("transport.py").read_text(
        encoding="utf-8"
    )
    for line in source.splitlines():
        if line.startswith(("import ", "from ")):
            assert "playwright" not in line, line
    assert "from playwright.async_api import async_playwright" in source


def test_cli_parses_record_and_diff_without_touching_a_browser():
    from src.services.verify import cli

    args = cli.build_parser().parse_args(["record", "acc", "-o", "snap.json"])
    assert args.profile == "acc"
    assert args.realms == (probes.WINDOW, probes.WORKER)

    args = cli.build_parser().parse_args(["diff", "a.json", "b.json"])
    assert (args.expected, args.observed) == ("a.json", "b.json")


def test_cli_rejects_an_unknown_realm():
    from src.services.verify import cli

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["record", "acc", "--realms", "iframe"])


def test_cli_diff_exits_zero_when_two_snapshots_agree(tmp_path, capsys):
    from src.services.verify import cli

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_snapshot(), str(a))
    snapshot.write(_snapshot(), str(b))
    assert cli.main(["diff", str(a), str(b)]) == 0
    assert "no differences" in capsys.readouterr().out


def test_cli_diff_exits_nonzero_when_they_disagree(tmp_path, capsys):
    from src.services.verify import cli

    target = probes.probes_for_realm(probes.WINDOW)[0].id
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_snapshot(), str(a))
    snapshot.write(_snapshot(window_values={target: "drift"}), str(b))
    assert cli.main(["diff", str(a), str(b)]) == 1
    assert target in capsys.readouterr().out


def test_cli_list_prints_the_whole_inventory(capsys):
    from src.services.verify import cli

    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    for probe in probes.PROBES:
        assert probe.id in out


# --- transport error messages -------------------------------------------
# These RENDER the operator-facing strings. A careful reading of the raise
# site proves its *shape*; only building the message proves what an operator
# actually sees. A missing `f` prefix on one segment of an implicitly
# concatenated literal is invisible to the former and obvious to the latter.


@pytest.mark.parametrize(
    "cause",
    [
        "DevToolsActivePort for 'acc' is stale (pre-launch)",
        "no live CDP port for 'acc' (DevToolsActivePort unreadable)",
    ],
)
def test_chromium_no_debug_port_message_renders_its_cause(monkeypatch, cause):
    from src.services.browser import cdp
    from src.services.verify import transport

    monkeypatch.setattr(
        cdp,
        "read_cdp_port",
        lambda name, **kw: (_ for _ in ()).throw(RuntimeError(cause)),
    )
    with pytest.raises(transport.TransportUnavailable) as err:
        transport._chromium_transport("acc")

    rendered = str(err.value)
    # The message promises the cause in parentheses — it must deliver it.
    assert cause in rendered
    assert "acc" in rendered
    # No unrendered template syntax survived into operator-facing output.
    assert "{" not in rendered and "}" not in rendered


def test_firefox_no_eval_hook_message_renders_cleanly(monkeypatch):
    from src.services.browser import invisible_launch
    from src.services.verify import transport

    monkeypatch.setattr(invisible_launch, "get_ff_eval", lambda name: None)
    with pytest.raises(transport.TransportUnavailable) as err:
        transport._firefox_transport("acc")

    rendered = str(err.value)
    assert "acc" in rendered
    assert "{" not in rendered and "}" not in rendered


def test_no_implicit_concatenation_drops_an_interpolation():
    """Guard the defect class, not just the one site that shipped it.

    Python concatenates adjacent string literals, but the ``f`` prefix binds
    per *segment*. So in a group where one segment interpolates, a segment
    written WITHOUT the prefix but containing ``{name}`` is a dropped
    interpolation — the braces reach the operator verbatim.

    This reads source TOKENS rather than the AST on purpose: the AST decodes
    an escaped ``{{`` in an f-string down to a bare ``{``, making a legitimate
    literal brace (this package builds JS, which is full of them) and a real
    defect indistinguishable. The prefix only survives in the token.
    """
    import io
    import pathlib
    import tokenize

    # A brace wrapping an identifier-ish expression and nothing else:
    # matches ``{exc}`` / ``{obj.attr}``, not JS like ``{return 'x';}``.
    interpolation = re.compile(r"\{[A-Za-z_][A-Za-z0-9_.\[\]!:]*\}")

    def groups(source):
        """Yield runs of adjacent string segments (implicit concatenation).

        Each segment is ``(prefix, text, lineno)``. Python 3.12 splits an
        f-string into FSTRING_START/MIDDLE/END rather than emitting one STRING
        token, so those are recombined here — otherwise the interpolating
        segment vanishes from the group and the guard never fires.
        """
        fstring_start = getattr(tokenize, "FSTRING_START", -1)
        fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", -1)
        fstring_end = getattr(tokenize, "FSTRING_END", -1)
        skip = {
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.COMMENT,
            tokenize.INDENT,
            tokenize.DEDENT,
        }
        run, depth, cur = [], 0, None
        readline = io.StringIO(source).readline
        for tok in tokenize.generate_tokens(readline):
            if tok.type == fstring_start:
                depth += 1
                if depth == 1:
                    cur = [tok.string.rstrip("\"'").lower(), "", tok.start[0]]
            elif tok.type == fstring_middle:
                if depth == 1:
                    cur[1] += tok.string
            elif tok.type == fstring_end:
                depth -= 1
                if depth == 0:
                    run.append(tuple(cur))
                    cur = None
            elif depth:
                continue  # tokens inside an f-string belong to that f-string
            elif tok.type == tokenize.STRING:
                body = tok.string.lstrip("bBfFrRuU")
                prefix = tok.string[: len(tok.string) - len(body)].lower()
                run.append((prefix, tok.string, tok.start[0]))
            elif tok.type in skip:
                continue
            elif run:
                yield run
                run = []
        if run:
            yield run

    package = pathlib.Path(probes.__file__).parent
    offenders = []
    checked = 0
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for run in groups(source):
            if len(run) < 2 or not any("f" in p for p, _, _ in run):
                continue  # single literal, or nothing here interpolates
            checked += 1
            for prefix, text, lineno in run:
                if "f" in prefix:
                    continue
                if interpolation.search(text):
                    offenders.append(f"{path.name}:{lineno}: {text}")
    # Guard the guard: if the tokenizer shape ever changes again, this test
    # must fail loudly rather than pass by finding nothing to inspect.
    assert checked, "found no interpolating concatenation groups to check"
    assert not offenders, (
        "a segment of an interpolating string group is missing its `f` "
        "prefix, so its braces render verbatim: " + "; ".join(offenders)
    )


# --- cross-profile comparison: AGREEMENT is the finding ---------------------
# The third comparison mode, and the only one whose polarity is inverted.
#
# `diff_snapshots` (one profile, two times) and `diff_realms` (one snapshot,
# two realms) both ask "did these agree?" and treat agreement as the pass. For
# Level 2 — mutual unlinkability — that is exactly backwards: two DIFFERENT
# profiles agreeing on a seed-derived vector is two identities a site can link.
# The comparator reported that as "no differences", exit 0.
#
# Two rules these pin, both of which are easy to get wrong in the tempting
# direction:
#
#   * Only vectors that MUST vary are compared. Two profiles agreeing on
#     operator-chosen configuration (os_type, resolution, engine, locale) is the
#     operator's own choice; a POOLED vector colliding is pigeonhole; and the
#     masking.*/realm.* probes observe the MECHANISM, not the identity. The iOS
#     GPU pair is the sharp case — one compile-time constant for every iOS
#     device, so two iOS profiles MUST agree there (gpu_ext.py's `var IOS_GPU =`
#     constant; the reasoning is in the `build_gpu_extension` docstring).
#   * The "no evidence" line moves. `diff_snapshots` calls the ASYMMETRIC case
#     (read on one side, errored on the other) CHANGED, because a vector that
#     stopped being readable is the loudest continuity signal there is. Here it
#     is INCONCLUSIVE: holding profile A's digest and not holding profile B's is
#     one reading and one hole, not evidence they differ. Claiming distinctness
#     from it would manufacture an unlinkability pass out of a reading nobody
#     obtained — the PS-29 defect, reintroduced on this axis.


def _profile_snapshot(profile, *, engine="chromium", **kwargs):
    """A snapshot with a nameable profile, otherwise identical to `_snapshot`."""
    return snapshot.build_snapshot(
        _run(**kwargs),
        engine=engine,
        profile=profile,
        realms=(probes.WINDOW, probes.WORKER),
        version="9.9.9",
    )


def _must_differ_probe():
    """One INDEPENDENT probe, taken from the inventory rather than hard-coded."""
    targets = probes.must_differ_ids()
    assert targets, "the inventory classifies no vector as must-differ"
    return sorted(targets)[0]


# --- the classification -----------------------------------------------------


def test_every_probe_declares_a_known_variance():
    for probe in probes.PROBES:
        assert probe.variance in probes.VARIANCE_KINDS, probe.id


def test_an_unknown_variance_is_rejected_at_construction():
    with pytest.raises(ValueError, match="unknown variance"):
        probes.Probe("p", probes.BOTH, "1", variance="sometimes")


def test_variance_defaults_to_shared_so_a_new_probe_is_never_must_differ():
    # The safe default matters: an unclassified vector must produce silence,
    # not a false leak report, the day somebody adds a probe and forgets this
    # axis exists.
    assert probes.Probe("p", probes.BOTH, "1").variance == probes.SHARED


def test_the_ios_gpu_pair_is_not_in_the_must_differ_set():
    # THE trap. gpu_ext.py's `var IOS_GPU =` constant pins ONE vendor/renderer
    # pair for every iOS profile on earth, deliberately not seed-varied, because
    # a diversified one "would itself be the tell" (the `build_gpu_extension`
    # docstring). Two iOS profiles MUST agree here — requiring difference would
    # flag persona's most deliberately correct behaviour as a leak.
    assert "webgl.unmasked" not in probes.must_differ_ids()


def test_masking_and_realm_probes_are_not_in_the_must_differ_set():
    # These observe the MECHANISM (what a page reads off a spoofed function,
    # whether the realm registry booted), not the identity it produces. They
    # SHOULD agree across profiles; requiring them to differ is backwards.
    targets = probes.must_differ_ids()
    observed = {
        p.id
        for p in probes.PROBES
        if p.id.startswith(("masking.", "realm."))
    }
    assert observed, "inventory carries no masking./realm. probes to check"
    assert not (observed & targets)


def test_operator_chosen_configuration_is_not_in_the_must_differ_set():
    # os_type / resolution / engine / search_engine are operator-set fields
    # (profile.py:6-36), so two profiles may LEGITIMATELY agree on the vectors
    # they drive. Demanding otherwise produces constant false findings.
    targets = probes.must_differ_ids()
    assert "navigator.platform" not in targets
    assert "screen.geometry" not in targets


def test_pooled_vectors_are_excluded_because_a_collision_is_pigeonhole():
    # Seed-derived, but drawn from a small fixed set — hardwareConcurrency and
    # deviceMemory come from a SIX-entry pool (device_ext.py:218), so a
    # collision is ordinary chance rather than a linkable identity.
    targets = probes.must_differ_ids()
    for probe in probes.probes_with_variance(probes.POOLED):
        assert probe.id not in targets


def test_must_differ_ids_are_derived_from_the_inventory():
    # Classifying a probe must mean editing its record and nothing else
    # (probes.py:8) — a comparator holding its own id list would keep answering
    # after the inventory moved.
    assert probes.must_differ_ids() == frozenset(
        p.id for p in probes.PROBES if p.variance == probes.INDEPENDENT
    )


# --- the comparator ---------------------------------------------------------


def test_two_profiles_agreeing_on_a_seed_derived_vector_is_the_finding():
    # AC1/AC2. THE premise, and the whole point of the mode: two snapshots
    # differing ONLY in the profile header, with a byte-identical observed
    # identity — the worst possible Level 2 failure. The continuity comparator
    # calls this "no differences" and exits 0.
    a = _profile_snapshot("alice")
    b = _profile_snapshot("bob")
    assert a["probes"] == b["probes"], "the premise is a byte-identical identity"

    # The old question, unchanged: these two "agree".
    assert diff.diff_snapshots(a, b) == []

    # The new one: they agree, and that is the defect.
    entries = diff.compare_profiles(a, b)
    assert entries, "two linkable profiles must not compare clean"
    target = _must_differ_probe()
    entry = next(e for e in entries if e["probe_id"] == target)
    assert entry["status"] == diff.COLLIDING
    # AC1: the entry carries the probe id, its realm, and the SHARED value.
    assert entry["realm"] in probes.ALL_REALMS
    assert entry["value"] == f"window:{target}"


def test_profiles_that_differ_on_every_must_differ_vector_compare_clean():
    # AC3: the pass, and the guard against a mode that cries leak constantly.
    a = _profile_snapshot("alice")
    b = _profile_snapshot(
        "bob",
        window_values={pid: f"bob:{pid}" for pid in probes.must_differ_ids()},
    )
    assert diff.compare_profiles(a, b) == []
    assert "no collisions" in diff.format_comparison([])


def test_configuration_the_profiles_share_is_not_reported_as_a_collision():
    # Two profiles on the same OS report the same navigator.platform. That is
    # the operator's choice, not a leak, and must stay silent even though the
    # two snapshots agree on it byte for byte.
    a = _profile_snapshot("alice")
    b = _profile_snapshot(
        "bob",
        window_values={pid: f"bob:{pid}" for pid in probes.must_differ_ids()},
    )
    assert a["probes"][probes.WINDOW]["navigator.platform"] == (
        b["probes"][probes.WINDOW]["navigator.platform"]
    )
    assert diff.compare_profiles(a, b) == []


def test_a_vector_errored_on_both_sides_is_inconclusive_never_distinctness():
    # AC4, and the PS-29 rule carried onto this axis: two probes that BOTH
    # errored are the same failure twice, not two profiles differing. Calling
    # that distinctness would manufacture an unlinkability PASS out of evidence
    # nobody obtained.
    target = _must_differ_probe()
    a = _profile_snapshot("alice", window_values={target: TypeError("no audio")})
    b = _profile_snapshot("bob", window_values={target: TypeError("no audio")})
    entries = diff.compare_profiles(a, b)
    assert [e["status"] for e in entries] == [diff.INCONCLUSIVE]
    assert diff.inconclusive_count(entries) == len(entries)


def test_a_vector_read_for_one_profile_only_is_inconclusive_not_a_pass():
    # The asymmetric case, and the line this axis draws DIFFERENTLY from the
    # continuity comparator. There, one side read + one side thrown is CHANGED
    # (the loudest continuity signal there is). Here it is no evidence at all:
    # holding alice's digest and not holding bob's says nothing about whether
    # the two identities differ, so it must never read as distinctness.
    target = _must_differ_probe()
    a = _profile_snapshot("alice")
    b = _profile_snapshot("bob", window_values={target: TypeError("no audio")})
    entries = diff.compare_profiles(a, b)
    assert [e["status"] for e in entries] == [diff.INCONCLUSIVE]


def test_a_missing_probe_is_not_reported_as_the_profiles_differing():
    # A vector absent from one snapshot is not two profiles differing on it —
    # and, just as sharply, it is not the profiles being UNLINKABLE either. An
    # empty entry list is this mode's PASS, so skipping the vector (as an
    # intersection-driven loop does) certifies distinctness nobody measured.
    # ABSENT is one of the three cases `_unread` names; the comparator must
    # reach it, which means walking the INVENTORY rather than the intersection.
    target = _must_differ_probe()
    a = _profile_snapshot("alice")
    b = _profile_snapshot("bob")
    del b["probes"][probes.WINDOW][target]
    entries = diff.compare_profiles(a, b)
    assert [e["status"] for e in entries] == [diff.INCONCLUSIVE]
    assert entries[0]["probe_id"] == target
    assert entries[0]["observed"] == diff.ABSENT
    assert diff.inconclusive_count(entries) == len(entries)


def test_a_target_recorded_in_NEITHER_snapshot_never_reads_as_a_pass(tmp_path):
    # The reviewer's case, and the one an operator hits by accident: `record
    # --realms worker` is a documented option (cli.py), and every must-differ
    # vector on this inventory is WINDOW_ONLY. An intersection-driven loop
    # compares ZERO vectors, returns [], and exits 0 — "the profiles differ on
    # every vector compared" when nothing was compared and nothing was read.
    # That is the PS-29 defect on the unlinkability axis: an unlinkability pass
    # manufactured out of evidence nobody obtained.
    from src.services.verify import cli

    target = _must_differ_probe()
    a = _profile_snapshot("alice")
    b = _profile_snapshot("bob")
    for snap in (a, b):
        del snap["probes"][probes.WINDOW][target]

    entries = diff.compare_profiles(a, b)
    assert [e["status"] for e in entries] == [diff.INCONCLUSIVE], (
        "a vector absent from BOTH snapshots is no evidence, not a clean bill"
    )

    a_path, b_path = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(a, str(a_path))
    snapshot.write(b, str(b_path))
    assert cli.main(["compare", str(a_path), str(b_path)]) == 3, (
        "a comparison of zero READ vectors must exit 3, never 0"
    )


def test_comparison_entries_are_ordered_by_realm_then_probe_id():
    a = _profile_snapshot("alice")
    b = _profile_snapshot("bob")
    entries = diff.compare_profiles(a, b)
    keys = [(e["realm"], e["probe_id"]) for e in entries]
    assert keys == sorted(keys)


def test_the_comparison_reports_no_score_only_which_vectors_collide():
    # The charter forbids turning the bar into a number, and a cross-profile
    # comparator is the single most tempting place in the codebase to produce
    # one. Every entry names a vector; none carries a similarity percentage.
    entries = diff.compare_profiles(_profile_snapshot("alice"), _profile_snapshot("bob"))
    assert entries
    for entry in entries:
        assert set(entry) <= {"probe_id", "realm", "status", "expected", "observed", "value"}
    text = diff.format_comparison(entries)
    assert "%" not in text
    assert "score" not in text.lower()


def test_format_comparison_does_not_call_a_collision_no_differences():
    # The renderer's vocabulary is inverted too. Rendering a leak through
    # `format_diff` would report it as "1 differing" — telling an operator the
    # bad news in the words that describe the good news.
    entries = diff.compare_profiles(_profile_snapshot("alice"), _profile_snapshot("bob"))
    text = diff.format_comparison(entries)
    assert "no differences" not in text
    assert "colliding" in text and "LINKABLE" in text


# --- the CLI ----------------------------------------------------------------


def test_cli_compare_inverts_the_exit_code_of_the_premise(tmp_path):
    # AC2 at the operator-facing artifact, which is the EXIT CODE, not the
    # classification: the SAME pair that exits 0 through `diff` must exit
    # non-zero through `compare`. Asserted on a real cli.main call, because
    # that is what a caller actually reads.
    from src.services.verify import cli

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_profile_snapshot("alice"), str(a))
    snapshot.write(_profile_snapshot("bob"), str(b))

    assert cli.main(["diff", str(a), str(b)]) == 0, (
        "the premise: today two byte-identical identities read as agreement"
    )
    assert cli.main(["compare", str(a), str(b)]) == 1, (
        "two profiles agreeing on a seed-derived vector are LINKABLE — the "
        "comparison must not exit 0"
    )


def test_cli_compare_exits_zero_when_the_profiles_are_distinct(tmp_path):
    from src.services.verify import cli

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_profile_snapshot("alice"), str(a))
    snapshot.write(
        _profile_snapshot(
            "bob",
            window_values={pid: f"bob:{pid}" for pid in probes.must_differ_ids()},
        ),
        str(b),
    )
    assert cli.main(["compare", str(a), str(b)]) == 0


def test_cli_compare_exits_three_when_nothing_was_read(tmp_path):
    # AC4's exit-code half: a comparison resting only on unobtained readings
    # exits 3, never 0. The status guard above never reaches the exit code —
    # that gap is exactly how a previous round shipped a demotion nobody saw.
    from src.services.verify import cli

    target = _must_differ_probe()
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(
        _profile_snapshot("alice", window_values={target: TypeError("x")}), str(a)
    )
    snapshot.write(
        _profile_snapshot("bob", window_values={target: TypeError("x")}), str(b)
    )
    assert cli.main(["compare", str(a), str(b)]) == 3


# --- the premise: is this comparison CONTROLLED at all? ----------------------
# The cross-profile question carries a premise the two continuity comparators
# do not: that these are two DIFFERENT identities, observed under conditions
# that make a difference between them attributable to the SEEDS rather than to
# the setup. `compare_profiles` reads only `probes`, so it was blind to both
# ways that premise fails — and the two failures point in OPPOSITE directions,
# which is why neither can be left to the operator to notice:
#
#   * same profile both sides -> every vector agrees -> a loud, confident FALSE
#     LEAK, telling an operator a profile is linkable to ITSELF. Not exotic:
#     the CLI header documents `before.json`/`after.json` (one profile, twice)
#     for `diff`, so reaching for the wrong subcommand is the expected mistake.
#   * different engines -> a vector may differ BECAUSE of the engine -> exit 0,
#     a FALSE CERTIFICATE of unlinkability that was never demonstrated. This is
#     the dangerous direction, and `diff_snapshots` already reasons about
#     exactly this ("a chromium snapshot vs a firefox one is not a regression,
#     it is a different question").
#
# Both are REFUSED rather than answered, because both available verdicts are
# wrong. The refusal is exit 2 — never 1 — since a refusal is not a finding.


def test_comparing_a_profile_with_itself_is_refused_not_reported_as_a_leak():
    # The false-leak direction. Two snapshots of ONE profile agree on every
    # vector by construction; reporting that as a collision would tell an
    # operator an identity is linkable to itself.
    a = _profile_snapshot("acc")
    b = _profile_snapshot("acc")
    with pytest.raises(diff.ComparisonNotControlled, match="both snapshots are profile"):
        diff.compare_profiles(a, b)


def test_the_before_after_workflow_through_compare_refuses_instead_of_crying_leak(
    tmp_path, capsys
):
    # The mistake as an operator actually makes it: the CLI header's own
    # documented before/after recording, put through the wrong subcommand. It
    # must not answer, and it must not answer with a 1 that a future gate would
    # read as a leak.
    from src.services.verify import cli

    before, after = tmp_path / "before.json", tmp_path / "after.json"
    snapshot.write(_profile_snapshot("acc"), str(before))
    snapshot.write(_profile_snapshot("acc"), str(after))

    # The RIGHT subcommand for this pair still works exactly as before.
    assert cli.main(["diff", str(before), str(after)]) == 0

    code = cli.main(["compare", str(before), str(after)])
    assert code == 2, "a refusal is not a finding — it must not be 1"
    err = capsys.readouterr().err
    assert "use `diff`" in err, "the refusal must name the subcommand that fits"


def test_a_snapshot_that_does_not_name_its_profile_cannot_answer_the_question():
    # This mode needs positive evidence that the two identities DIFFER. "No
    # name recorded" is not that evidence, so it is refused on the same footing
    # as two identical names rather than being optimistically compared.
    a = _profile_snapshot("alice")
    b = _profile_snapshot("bob")
    del b["profile"]
    with pytest.raises(diff.ComparisonNotControlled, match="name their profile"):
        diff.compare_profiles(a, b)


def test_two_engines_are_refused_because_a_difference_may_be_the_ENGINE():
    # The false-CERTIFICATE direction, and the dangerous one: these two differ
    # on the must-differ vector, so the mode would have exited 0 — certifying
    # unlinkability on a difference the ENGINE could account for.
    a = _profile_snapshot("alice", engine="chromium")
    b = _profile_snapshot(
        "bob",
        engine="firefox",
        window_values={pid: f"bob:{pid}" for pid in probes.must_differ_ids()},
    )
    with pytest.raises(diff.ComparisonNotControlled, match="different engines"):
        diff.compare_profiles(a, b)

    # And the opt-in genuinely opts in, rather than merely existing.
    assert diff.compare_profiles(a, b, allow_cross_engine=True) == []


def test_a_snapshot_that_does_not_name_its_engine_cannot_answer_the_question():
    # The other half of the engine guard, and the same false-CERTIFICATE
    # direction as the test above — reached not by two engines DIFFERING but by
    # neither being recorded. `None != None` is False, so an equality-only
    # guard reads "no idea what these ran on" as "same engine" and exits 0.
    #
    # Guarded on PRESENCE as well as equality for the reason the profile field
    # already is: this mode needs positive evidence that a differing vector is
    # attributable to the SEEDS, and an unrecorded engine is not that evidence.
    a = _profile_snapshot("alice")
    b = _profile_snapshot(
        "bob",
        window_values={pid: f"bob:{pid}" for pid in probes.must_differ_ids()},
    )
    # These differ on every must-differ vector, so the mode WOULD have exited 0.
    assert diff.compare_profiles(a, b) == []

    for drop in ("a", "b", "both"):
        x, y = copy.deepcopy(a), copy.deepcopy(b)
        if drop in ("a", "both"):
            del x["engine"]
        if drop in ("b", "both"):
            del y["engine"]
        with pytest.raises(diff.ComparisonNotControlled, match="name their engine"):
            diff.compare_profiles(x, y)


def test_allow_cross_engine_does_not_relax_an_UNRECORDED_engine(tmp_path, capsys):
    # The judgement call, pinned as behaviour so it cannot be softened by
    # accident. `--allow-cross-engine` opts in to a KNOWN, NAMED difference
    # whose caveat the operator has weighed; an unrecorded engine gives them
    # nothing to weigh, so the flag must NOT buy a pass here the way it does
    # for chromium-vs-firefox.
    from src.services.verify import cli

    a = _profile_snapshot("alice")
    b = _profile_snapshot(
        "bob",
        window_values={pid: f"bob:{pid}" for pid in probes.must_differ_ids()},
    )
    del a["engine"], b["engine"]
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(a, str(pa))
    snapshot.write(b, str(pb))

    assert cli.main(["compare", str(pa), str(pb)]) == 2
    code = cli.main(["compare", str(pa), str(pb), "--allow-cross-engine"])
    assert code == 2, "an unrecorded engine is not an opted-into engine difference"
    assert "name their engine" in capsys.readouterr().err


def test_cli_compare_refuses_cross_engine_by_default_and_allows_it_on_request(
    tmp_path, capsys
):
    from src.services.verify import cli

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_profile_snapshot("alice", engine="chromium"), str(a))
    snapshot.write(
        _profile_snapshot(
            "bob",
            engine="firefox",
            window_values={pid: f"bob:{pid}" for pid in probes.must_differ_ids()},
        ),
        str(b),
    )
    assert cli.main(["compare", str(a), str(b)]) == 2
    assert "--allow-cross-engine" in capsys.readouterr().err
    assert cli.main(["compare", str(a), str(b), "--allow-cross-engine"]) == 0


def test_a_cross_engine_COLLISION_is_still_a_finding_once_allowed():
    # Why cross-engine is an opt-in rather than a wall: the flag suppresses a
    # false PASS, it must not suppress a real finding. Two profiles agreeing on
    # a seed-derived digest DESPITE running on different engines is if anything
    # a stronger signal, so it must still report.
    a = _profile_snapshot("alice", engine="chromium")
    b = _profile_snapshot("bob", engine="firefox")
    entries = diff.compare_profiles(a, b, allow_cross_engine=True)
    assert [e["status"] for e in entries] == [diff.COLLIDING]


def test_the_refusal_is_raised_before_any_probe_is_read():
    # Stated as behaviour rather than trusted from reading order: a pair whose
    # premise fails is refused even when the probe data is missing entirely, so
    # the guard cannot be reached only on some paths through the comparator.
    a = {"profile": "acc", "engine": "chromium"}
    b = {"profile": "acc", "engine": "chromium"}
    with pytest.raises(diff.ComparisonNotControlled):
        diff.compare_profiles(a, b)


def test_the_two_continuity_comparators_gained_no_such_guard():
    # AC6, stated as behaviour. `diff` and `realms` are SUPPOSED to compare one
    # profile with itself — that is their entire job — so the premise guard
    # must not have leaked into them.
    a = _profile_snapshot("acc")
    b = _profile_snapshot("acc")
    assert diff.diff_snapshots(a, b) == []
    assert diff.diff_realms(a, probes.WINDOW, probes.WORKER)


# --- the new metadata must not leak into the artifact -----------------------


def test_probe_variance_never_reaches_a_snapshot():
    # AC7: `variance` is operator-facing metadata like `note` — it describes how
    # to READ a reading, it is not a reading. A snapshot recorded before this
    # field existed must stay loadable and comparable.
    text = snapshot.dumps(_snapshot())
    assert "variance" not in text
    for kind in probes.VARIANCE_KINDS:
        assert f'"{kind}"' not in text


def test_a_snapshot_recorded_before_variance_existed_still_compares():
    # The compatibility claim, stated as behaviour: nothing in the comparison
    # path reads a field off the snapshot that an older file would lack.
    a = _profile_snapshot("alice")
    b = _profile_snapshot("bob")
    assert diff.compare_profiles(a, b), "an existing-shape snapshot must compare"
    assert diff.diff_snapshots(a, b) == []


# --- a comparison of zero probes is not agreement (PS-41) -------------------
#
# The rule this module states at the top — an unobtainable reading is
# inconclusive, and inconclusive is never a pass — enforced one rung ABOVE the
# entries, on whether the file handed in is a snapshot at all.
#
# The defect these pin, executed at `0da857f` before the guard existed:
#
#   cli diff site/package.json site/package.json   ->  "no differences", exit 0
#   cli realms site/package.json                   ->  "no differences", exit 0
#   cli diff [1,2,3] ...                           ->  AttributeError,   exit 1
#
# `_probes` coerced a missing `probes` to `{}`, so zero probes compared, the
# comparator returned `[]`, and an empty list IS the agreement signal. Note the
# asymmetry, which is the whole argument: a non-snapshot on ONE side was
# already caught (the real side's probes all read `removed`, exit 1); a
# non-snapshot on BOTH sides was a clean pass. The tool was at its most
# confident exactly when it held the least evidence.


def _not_a_snapshot_files(tmp_path):
    """Valid JSON that is not a snapshot, in the shapes an operator produces.

    `object` is the typo case — a real file, wrong file. The other three are
    valid JSON that is not even an object; they used to reach `_probes` as an
    AttributeError traceback on the DRIFT exit code.
    """
    cases = {
        "object": {"name": "persona-site", "version": "1.0.0"},
        "list": [1, 2, 3],
        "null": None,
        "string": "hello",
    }
    out = {}
    for label, payload in cases.items():
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        out[label] = path
    return out


def test_diff_refuses_two_non_snapshots_instead_of_reporting_agreement(tmp_path):
    # AC1. The false GREEN, and the reason this is worse than the false red the
    # same hole produces on the baseline path: a red gets investigated, and
    # "no differences" gets believed.
    from src.services.verify import cli

    for label, path in _not_a_snapshot_files(tmp_path).items():
        code = cli.main(["diff", str(path), str(path)])
        assert code == 2, (
            f"{label}: a comparison of zero probes is not agreement — it must "
            "not exit 0, and a refusal is not a finding so it must not be 1"
        )


def test_realms_refuses_a_non_snapshot_instead_of_reporting_agreement(tmp_path):
    # AC2. Same hole, the other entry point: two empty realms agree about
    # everything, so this returned [] and rendered "no differences" too.
    from src.services.verify import cli

    for label, path in _not_a_snapshot_files(tmp_path).items():
        code = cli.main(["realms", str(path)])
        assert code == 2, f"{label}: zero realms compared is not agreement"


def test_a_refusal_never_renders_as_no_differences(tmp_path, capsys):
    # AC4, as behaviour rather than as an exit code: the OUTPUT must not say
    # anything about differences either. "no differences" over a file that was
    # never compared is the sentence this ticket exists to delete.
    from src.services.verify import cli

    path = _not_a_snapshot_files(tmp_path)["object"]
    assert cli.main(["diff", str(path), str(path)]) == 2
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert "differen" not in combined.lower(), (
        "the refusal must not use the language of a comparison verdict"
    )
    assert "NOT agreement" in combined, "it must say plainly what did NOT happen"
    assert str(path) in combined, "it must name the file the operator typed"
    assert "record" in combined, "it must point at the command that makes one"


def test_valid_json_that_is_not_an_object_refuses_without_a_traceback(tmp_path):
    # AC3. These parsed fine and died in `_probes` with `'list' object has no
    # attribute 'get'` — an uncaught traceback surfacing on exit 1, the DRIFT
    # code. A traceback is not a diff verdict.
    from src.services.verify import cli

    files = _not_a_snapshot_files(tmp_path)
    for label in ("list", "null", "string"):
        path = files[label]
        # No `pytest.raises`: the point is that nothing propagates.
        assert cli.main(["diff", str(path), str(path)]) == 2, label
        assert cli.main(["realms", str(path)]) == 2, label


def test_the_comparators_themselves_refuse_not_merely_the_cli(tmp_path):
    # The guard must live in the comparator, not only at the CLI boundary, so
    # `baseline.py` and any future caller inherit it rather than each having to
    # remember. Asserted on the real functions, not on a helper being called.
    bad = {"name": "persona-site", "version": "1.0.0"}
    good = _snapshot()

    with pytest.raises(diff.NotASnapshot, match="no 'probes' object"):
        diff.diff_snapshots(bad, bad)
    with pytest.raises(diff.NotASnapshot):
        diff.diff_realms(bad, probes.WINDOW, probes.WORKER)

    # A non-snapshot on ONE side was already loud (exit 1, everything
    # `removed`), but it is still not a comparison — refuse both orders.
    with pytest.raises(diff.NotASnapshot):
        diff.diff_snapshots(good, bad)
    with pytest.raises(diff.NotASnapshot):
        diff.diff_snapshots(bad, good)


def test_a_snapshot_carrying_an_EMPTY_probes_object_still_compares(tmp_path):
    # The boundary, pinned deliberately so the guard is not quietly widened
    # later. `probes: {}` is a structurally valid snapshot that `record` can
    # legitimately produce, and this slice answers only "is this a snapshot at
    # all" — validating probe CONTENTS is the named successor slice, not this
    # one. It must NOT be refused here.
    empty = dict(_snapshot())
    empty["probes"] = {}
    assert diff.diff_snapshots(empty, empty) == []
    assert diff.require_snapshot(empty) is empty


# --- AC5: the regression guard. These describe the UNCHANGED behaviour ------


def test_a_real_snapshot_pair_still_agrees_and_still_says_no_differences(tmp_path):
    # AC5. The control that makes every assertion above evidence rather than
    # noise: the guard must refuse non-snapshots WITHOUT touching what a real
    # pair does. An all-value identical pair still exits 0 and still renders
    # the exact string.
    from src.services.verify import cli

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_snapshot(), str(a))
    snapshot.write(_snapshot(), str(b))

    assert cli.main(["diff", str(a), str(b)]) == 0
    assert diff.format_diff(diff.diff_snapshots(_snapshot(), _snapshot())) == (
        "no differences"
    )


def test_a_real_snapshot_read_on_neither_side_still_exits_three(tmp_path):
    # AC5's other half, and the one that proves the guard did not swallow the
    # inconclusive path PS-29 shipped: every reading unobtained is still 3 —
    # NOT 0 (that would be the old false green) and NOT 2 (this IS a snapshot,
    # so it was not refused; it was compared and found to rest on nothing).
    from src.services.verify import cli

    target = _must_differ_probe()
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(
        _profile_snapshot("acc", window_values={target: TypeError("x")}), str(a)
    )
    snapshot.write(
        _profile_snapshot("acc", window_values={target: TypeError("x")}), str(b)
    )

    entries = diff.diff_snapshots(
        snapshot.load(str(a)), snapshot.load(str(b))
    )
    assert entries, "an unread probe must be REPORTED, not skipped"
    assert all(e["status"] == diff.INCONCLUSIVE for e in entries)
    assert cli.main(["diff", str(a), str(b)]) == 3


def test_compares_refusal_is_untouched_and_still_distinguishable(tmp_path):
    # AC5. PS-35's `compare` refusal shares the exit code with the new one, so
    # pin that BOTH still fire and still raise their OWN exception type — a
    # guard that swallowed the other would be invisible from the exit code
    # alone, since both are 2.
    from src.services.verify import cli

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    snapshot.write(_profile_snapshot("acc"), str(a))
    snapshot.write(_profile_snapshot("acc"), str(b))

    assert cli.main(["compare", str(a), str(b)]) == 2
    with pytest.raises(diff.ComparisonNotControlled):
        diff.compare_profiles(_profile_snapshot("acc"), _profile_snapshot("acc"))

    # And the two refusals are not the same exception wearing two names.
    assert not issubclass(diff.NotASnapshot, diff.ComparisonNotControlled)
    assert not issubclass(diff.ComparisonNotControlled, diff.NotASnapshot)
