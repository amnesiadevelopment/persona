"""EVIDENCE.md §2 must agree with the artifacts it cites. Pinned as a test.

WHY THIS FILE EXISTS
--------------------
Round 2 of PS-177 committed ``sweep.log`` and ``exit-recovery-probe.log`` and
added the sentence *"Everything in §2 can be checked against them."* The
reviewer checked, and three figures disagreed with the log they were attributed
to:

===============================  ==========================  ================
§2 claimed (round 2)             the committed log holds      status
===============================  ==========================  ================
20 probes over **38 minutes**    20 probes over **24.8 min**  wrong
window **21:49-22:29**           **22:01:07 - 22:25:55**      wrong
logged **one per minute**        mean **78s** cadence         wrong
===============================  ==========================  ================

The probe *count* was right; every figure attached to it was wrong, because the
numbers were typed from a terminal scrollback instead of derived from the
artifact. Committing the logs is what made that checkable — round 1 had blocked
on the same section being unverifiable.

WHAT THIS PINS, AND WHY IT IS A TEST RATHER THAN A README NOTE
--------------------------------------------------------------
Correcting three numbers by hand fixes today's document and does nothing about
tomorrow's. The defect class here is **prose drifting from its own evidence**,
and it survived two review rounds because nothing executed the claim. So the
correction ships as a *command* — ``probe-exit-recovery.py --verify`` — and this
file makes that command run in CI.

These tests assert on **the relationship between two committed files**, never on
text this branch generated (PS-11: a green test that asserts on your own output
is the failure mode this project has hit six times). Every check re-derives its
expected value from ``exit-recovery-probe.log`` at run time; not one figure is
hard-coded from the prose it is checking.

THE DISCRIMINATION CLAIM
------------------------
Restore any round-2 figure in EVIDENCE.md §2 and
``test_verifier_rejects_each_withdrawn_figure`` fails, one case per figure.
That is checked by mutation below rather than asserted in a comment.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PS177_DIR = os.path.join(REPO_ROOT, "readings", "ps177-2026-08-25")

PROBE_TOOL = os.path.join(PS177_DIR, "probe-exit-recovery.py")
PROBE_LOG = os.path.join(PS177_DIR, "exit-recovery-probe.log")
SWEEP_LOG = os.path.join(PS177_DIR, "sweep.log")
EVIDENCE = os.path.join(PS177_DIR, "EVIDENCE.md")
PATCH = os.path.join(PS177_DIR, "PS-16-PATCH.md")


def _load_probe():
    """Import the instrument by path — it lives beside the record, not in a package."""
    spec = importlib.util.spec_from_file_location("ps177_probe", PROBE_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _prose(doc):
    """EVIDENCE.md minus its blockquotes.

    Blockquote lines are the *withdrawal record* — they quote the wrong figures
    on purpose, because a correction that deletes the bad number leaves a reader
    unable to tell a fixed document from one that never had the defect. The
    guard therefore applies to the claims, not to the history.
    """
    return "\n".join(
        ln for ln in doc.splitlines() if not ln.lstrip().startswith(">")
    )


# --------------------------------------------------------------- the figures


def test_verify_passes_on_the_committed_tree():
    """The shipped document agrees with the shipped logs. The headline check."""
    # encoding is explicit: text=True would decode the child's stdout with the
    # LOCALE codec, which is cp1252 on the Windows CI runner, and the verifier
    # prints "§". Every file read in this test tree is likewise explicitly utf-8
    # — the first version of this suite passed on ubuntu/macos and failed on
    # windows-latest for exactly that reason.
    result = subprocess.run(
        [sys.executable, PROBE_TOOL, "--verify"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, (
        "EVIDENCE.md §2 disagrees with its own committed artifacts:\n"
        + result.stdout + result.stderr
    )
    assert "0 failed" in result.stdout


def test_figures_are_derived_from_the_log_not_the_prose():
    """Re-derive §2's numbers from the log and require the prose to match.

    This is the check that would have caught the round-2 defect. It reads the
    LOG first and the DOCUMENT second — never the reverse.
    """
    f = probe.derive_figures()
    doc = _read(EVIDENCE)

    assert f["probes"] == 20
    assert f["successes"] == 0
    assert f["socks5_auth_failures"] == f["probes"]

    # The three figures round 2 got wrong, each re-derived here.
    assert f["span_minutes"] == 24.8
    assert (f["first"], f["last"]) == ("22:01:07", "22:25:55")
    assert 77.0 <= f["mean_gap_seconds"] <= 79.0

    # ...and each one actually appears in the prose that quotes it.
    assert f"{f['span_minutes']} minutes" in doc
    assert f["first"] in doc and f["last"] in doc


@pytest.mark.parametrize(
    "stale",
    ["38 minutes", "one per minute", "21:49–22:29", "96 seconds"],
)
def test_verifier_rejects_each_withdrawn_figure(tmp_path, stale, monkeypatch):
    """Restoring ANY withdrawn figure as a claim must fail the verifier.

    Mutation-based: the real document is copied, the correction is undone for
    one figure, and the verifier must reject it. Without this, the guards could
    silently stop guarding and every other test here would still pass.
    """
    doc = _read(EVIDENCE)
    # Re-introduce the figure as a CLAIM (a non-blockquote line).
    mutated = doc + f"\n\nThe recovery run lasted {stale}.\n"
    target = tmp_path / "EVIDENCE.md"
    target.write_text(mutated, encoding="utf-8")

    monkeypatch.setattr(probe, "EVIDENCE", target)
    assert probe.verify() == 1, (
        f"restoring {stale!r} as a claim was NOT rejected — the regression "
        "guard for it is not guarding"
    )


def test_withdrawn_figures_are_absent_as_claims_but_present_as_history():
    """Both halves matter: the wrong number is gone, the correction is legible."""
    doc = _read(EVIDENCE)
    prose, withdrawal = _prose(doc), doc

    for stale in ["38 minutes", "one per minute", "21:49–22:29", "96 seconds"]:
        assert stale not in prose, f"{stale!r} is still asserted as a claim"
        assert stale in withdrawal, f"{stale!r} was deleted instead of withdrawn"


# ------------------------------------------------------------- the provenance


def test_sweep_log_carries_no_timestamps():
    """§2's per-row source column rests on this, so it is measured, not assumed.

    Round 2's timeline attributed 21:41 / 21:47:16 / 21:48:53 to ``sweep.log``.
    The file has never held a timestamp; it establishes order and outcome only.
    """
    assert len(re.findall(r"\d{2}:\d{2}", _read(SWEEP_LOG))) == 0


def test_the_only_committed_clock_time_is_the_records_observed_at():
    """21:48:53 was re-sourced rather than dropped, because it IS evidenced."""
    assert probe.derive_figures()["record_observed_at"] == "2026-08-25T21:48:53Z"
    assert "21:48:53" in _read(EVIDENCE)


def test_sweep_log_independently_corroborates_the_refusals():
    """Measurement 4's conclusion has a second, independent source."""
    assert probe.derive_figures()["sweep_refusals"] == 7


def test_unevidenced_measurements_are_marked_as_such():
    """Measurements 1-3 have no artifact. The document must say so, not imply it."""
    doc = _read(EVIDENCE)
    assert doc.count("ad hoc") >= 3
    assert "no artifact" in doc


def test_the_false_blanket_provenance_claim_is_not_restored():
    """The exact sentence that triggered round 3 must never come back."""
    assert "Everything in §2 can be checked against them." not in _read(EVIDENCE)


def test_no_withdrawn_figure_reached_the_knowledge_article_patch():
    """PS-16-PATCH.md is queued for a knowledge article — it must carry nothing withdrawn.

    Round 2's patch DID carry "96 seconds in". Anything false here would be
    copied into PS-16 and outlive this ticket, so it is pinned separately from
    EVIDENCE.md rather than covered by the same sweep.
    """
    patch = _read(PATCH)
    for stale in ["38 minutes", "one per minute", "21:49–22:29",
                  "96 seconds", "159.195"]:
        assert stale not in patch, (
            f"{stale!r} is queued for knowledge article PS-16 and is withdrawn"
        )
