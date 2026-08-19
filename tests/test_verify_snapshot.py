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

import json
import re

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
