"""PS-345: pin the measureText guard's behaviour on the PUBLISHED-152 readings.

WHY THIS FILE EXISTS, AND WHY IT IS NOT A SECOND GUARD
───────────────────────────────────────────────────────
PS-345 measured the patch-015 defect on the shipped `personium-152.0.7977.75`
engine and drew a third seed, **777**, whose noise factor is POSITIVE: widths
come back spec-legal and still collapsed by seven orders of magnitude. Since
`norm_x`'s sign is a coin flip, roughly half of all seeds are in that class,
where the two seeds every earlier reading drew (24601, 5150) were not.

An earlier draft of PS-345 claimed that case was a **blind spot in
`scripts/ps301_measuretext_repro.py`** and shipped a second guard to close it.
Both halves were wrong: the guard's `constant and implausible` branch was
written for exactly this shape and catches it, and the second guard was
behaviourally identical to the first. The claim is retracted in that reading's
REPORT.md §2.3 and the duplicate is gone.

So these tests exist to make sure the *correction* stays corrected. They pin
behaviour the guard ALREADY HAD, which is the only thing that keeps a future
reader from making the same mistake in the other direction — seeing the
negative-width branch fire on the two famous seeds, concluding the
constant-ratio branch below it is redundant, and deleting the one rule that
catches half of all seeds.

THE LOAD-BEARING TEST IS `test_seed_777_is_caught_by_the_constant_ratio_branch`.
It asserts not merely that the verdict is DEFECT, but that it is reached
**without** any negative width present — because a test that only checked the
exit code would keep passing if the strong branch were deleted and the case
happened to be caught by something else. The mechanism is the claim, so the
mechanism is what is asserted.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ps301_measuretext_repro.py"
ARTIFACTS = REPO / "readings" / "ps345-2026-09-07" / "artifacts"


def _load():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("ps301_measuretext_repro", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load()


def _widths(guard, name: str) -> dict[str, float]:
    return guard.widths_from_measure_json(ARTIFACTS / name)


# ---------------------------------------------------------------------------
# The committed readings reach the right verdict THROUGH THIS GUARD.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reading,want_exit",
    [
        ("patched-24601.json", 1),   # negative widths
        ("patched-5150.json", 1),    # negative widths
        ("patched-777.json", 1),     # POSITIVE widths, still collapsed
        ("patched-noseed.json", 0),  # patch correctly stands down
    ],
)
def test_committed_readings_reach_the_recorded_verdict(guard, reading, want_exit):
    code, headline, _ = guard.verdict(_widths(guard, reading), _widths(guard, "stock.json"))
    assert code == want_exit, f"{reading}: {headline}"


def test_seed_777_is_caught_by_the_constant_ratio_branch(guard):
    """The whole point of the reading, asserted by MECHANISM not by exit code.

    Seed 777 must be condemned, and must be condemned *without* the
    negative-width rule having anything to say — that is what makes it the case
    a `width < 0` check alone would pass.
    """
    observed = _widths(guard, "patched-777.json")
    stock = _widths(guard, "stock.json")

    # The precondition that makes this case interesting at all: every width is
    # POSITIVE, so the negative-width branch cannot be what fires.
    assert all(w > 0 for w in observed.values()), (
        "seed 777 is only the interesting case while its widths are positive; "
        "if this fails the fixture changed and the test below proves nothing"
    )

    code, headline, detail = guard.verdict(observed, stock)
    assert code == 1, headline
    assert detail["negative_widths"] == []
    assert detail["constant_across_strings"] is True
    assert "CONSTANT" in headline, headline

    # And the magnitude is the actual discriminator: ~1e-06 where a healthy
    # factor is 1 ± 5e-6.
    assert detail["implausible_ratios"], "the ratio must read as implausible"


def test_a_negative_width_only_rule_would_have_passed_seed_777(guard):
    """Pin the claim §2.3 actually makes, so it cannot drift back to the false one.

    This is the honest form of the finding: the RULE is insufficient; the guard
    is not. Asserting it here means the report's sentence has a test behind it.
    """
    observed = _widths(guard, "patched-777.json")
    negative_only_verdict = any(w < 0 for w in observed.values())
    assert negative_only_verdict is False, (
        "a rule keyed on width < 0 alone finds nothing wrong with seed 777"
    )
    # ...while the real guard condemns it.
    assert guard.verdict(observed, _widths(guard, "stock.json"))[0] == 1


def test_the_constant_ratio_branch_does_not_condemn_a_healthy_factor(guard):
    """The branch's own falsification: a CORRECT Shuffle() factor is constant too.

    This is why the constant-ratio signature alone must not condemn, and why the
    branch is guarded by `implausible`. Without this the guard would report the
    fixed engine broken.
    """
    stock = _widths(guard, "stock.json")
    fixed = {k: v * 1.0000032705225304 for k, v in stock.items()}
    code, headline, detail = guard.verdict(fixed, stock)
    assert detail["constant_across_strings"] is True, "the fixture must be constant"
    assert code == 0, headline


# ---------------------------------------------------------------------------
# The reader added for this reading's JSON shape.
# ---------------------------------------------------------------------------


def test_reader_parses_all_four_strings(guard):
    w = _widths(guard, "patched-777.json")
    assert len(w) == 4, w
    assert all(isinstance(v, float) for v in w.values())


def test_reader_skips_cells_without_a_numeric_width_rather_than_guessing(guard, tmp_path):
    """A malformed cell must reduce the sample, never be invented.

    Too few samples then yields INDETERMINATE from verdict() — "I could not
    measure this" and "this is fine" being different answers is the whole
    premise of the guard.
    """
    p = tmp_path / "broken.json"
    p.write_text(json.dumps({
        "label": "x", "binary": "y", "args": [],
        "metrics": {"ok": {"width": 1.0}, "bad": {"width": None}, "worse": "not-a-dict"},
    }), encoding="utf-8")
    w = guard.widths_from_measure_json(p)
    assert w == {"ok": 1.0}
    assert guard.verdict(w, {"ok": 1.0})[0] == 2  # INDETERMINATE, not a guess


def test_reader_returns_empty_on_a_file_with_no_metrics_object(guard, tmp_path):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"label": "x", "metrics": "harness-error"}), encoding="utf-8")
    assert guard.widths_from_measure_json(p) == {}


# ---------------------------------------------------------------------------
# The self-test must keep reaching every verdict, seed 777 included.
# ---------------------------------------------------------------------------


def test_self_test_still_passes_and_carries_the_seed_777_case(guard, capsys):
    assert guard._self_test() == 0
    out = capsys.readouterr().out
    assert "777" in out, "the seed-777 case must stay in the self-test"
    # Every verdict is still reachable — a guard that can only pass is not a guard.
    assert "want exit 1" in out and "want exit 0" in out and "want exit 2" in out


def test_there_is_exactly_one_authoritative_measuretext_guard():
    """PS-345 dropped its duplicate; this keeps it dropped.

    The readings/ps301-2026-09-05 sibling is deliberately excluded: it is a
    frozen reading artifact, and ps301_repro.sh falls back to it while
    ANNOUNCING the fallback, so its drift is visible by design rather than
    silent.
    """
    stale = REPO / "readings" / "ps345-2026-09-07" / "artifacts" / "ps345_verdict.py"
    assert not stale.exists(), (
        "ps345_verdict.py was a behavioural duplicate of "
        "scripts/ps301_measuretext_repro.py and was removed; do not reintroduce "
        "it — add an input reader to the one guard instead"
    )
