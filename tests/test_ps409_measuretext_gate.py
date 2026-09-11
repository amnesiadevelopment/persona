"""PS-409 — the ``measuretext_ext`` repair is GATED on the installed engine.

WHAT THIS FILE GUARDS
---------------------
``browser/measuretext_ext.py`` exists for exactly one reason: to repair the
~1e-6 multiplicative scale the UNFIXED fingerprint engine applies to every
Canvas ``measureText`` metric. Its own header names what breaks without it —
Google Sheets' canvas grid laying glyphs against a width of ~0, and the
date-cell popover collapsing off-screen — and its guard repairs only absurdly
small widths::

    var corrupt = hasText && !(Math.abs(m.width) >= 1);
    if (!corrupt) return m;

PS-345 fixed that noise IN THE ENGINE, and PS-406 read positive, plausible
widths (298.41; 15.82 for ``"W"``) off a built artefact where the shipped engine
returns −0.0006. On a fixed engine real widths are ~200, so the guard CAN NEVER
FIRE — while the wrapper itself is observable (PR #327's Arm N: 14 widths,
14/14 identical with the repair installed, and ``measureText`` no longer
stringifying as ``[native code]``). Zero benefit, one new tell.

⛔ THE ASYMMETRY IS THE WHOLE DESIGN, AND IT IS WHAT THIS FILE MOSTLY TESTS.
The engine update is offered on a strict version compare, so every user who does
not take it stays on the old engine indefinitely. Two ways to get the gate
wrong, and they are NOT symmetric:

* PERMISSIVE error — the repair ships to an engine that does not need it. Cost:
  an observable wrapper with nothing to repair. A tell.
* STRICT error — the repair is withheld from an engine that DOES need it. Cost:
  ~1e-6 geometry with nothing repairing it. **Google Sheets breaks for a user
  who never updated.**

So every uncertainty must answer "the repair is required": no committed
threshold, an unreadable ``version.txt``, an unparseable tag on either side, a
malformed operator override. The tests below are mostly that single claim, read
once per way of being uncertain — because "it fails open" is the kind of
sentence that is written in a docstring and not implemented.

WHAT IS TESTED ELSEWHERE, DELIBERATELY
--------------------------------------
The BEHAVIOURAL pair — both arms read off a real launch's argv — lives in
``tests/test_engine_masking_matrix.py``
(``test_chromium_measuretext_is_gated_on_the_engine_version``), beside the AST
census that pins the condition's source text and beside the matrix cell that
states it. That is where every other Chromium-column gate is measured, and
splitting the gate away from its siblings would hide it from the census that
exists to catch exactly this kind of drift.

This file is the DECISION's own truth table, which needs no launch and no engine
on disk.
"""

import json

import pytest

from src.services.browser.engine_version import (
    carries_measuretext_fix,
    measuretext_repair_required,
)
from src.services.engine import policy


# --- the pure decision, over its whole truth table --------------------------


@pytest.mark.parametrize(
    "installed,threshold,carries",
    [
        # The release shape PS-406 established: a FIFTH component, because the
        # tag cannot repeat the string every existing install already carries.
        ("152.0.7977.75.1", "152.0.7977.75.1", True),   # exactly at it
        ("152.0.7977.75.2", "152.0.7977.75.1", True),   # past it
        ("153.0.8000.10", "152.0.7977.75.1", True),     # a later major
        ("152.0.7977.75", "152.0.7977.75.1", False),    # THE SHIPPED ENGINE
        ("152.0.7977.64", "152.0.7977.75.1", False),    # the macOS asset's build
        ("148.0.7778.215", "152.0.7977.75.1", False),   # an older engine
    ],
)
def test_the_comparison_is_a_version_order_over_the_raw_tag(
    installed, threshold, carries
):
    """``>=`` against the threshold, on the tag as published.

    ⭐ ROW 4 IS THE LOAD-BEARING ONE and it is why this is parametrized rather
    than written as two cases: ``152.0.7977.75`` and ``152.0.7977.75.1`` differ
    ONLY in a fifth component, and ``engine_version.parse`` truncates to four.
    An implementation that compared ``ChromiumVersion.full`` would read those
    two tags as EQUAL, answer ``True`` on row 4, and withhold the repair from
    the very engine that is broken — on every install in the field. That is the
    strict failure, reached by a change that looks like tidying.
    """
    assert carries_measuretext_fix(installed, threshold) is carries


@pytest.mark.parametrize(
    "installed,threshold,why",
    [
        ("152.0.7977.75.1", "", "no threshold committed — the state persona ships in"),
        ("", "152.0.7977.75.1", "version.txt absent or undecodable (mid-update)"),
        ("nightly", "152.0.7977.75.1", "an installed tag with no numeric component"),
        ("152.0.7977.75.1", "latest", "a threshold with no numeric component"),
        ("", "", "neither is readable"),
    ],
)
def test_every_uncertainty_answers_THE_REPAIR_IS_REQUIRED(installed, threshold, why):
    """Fail OPEN, one row per way of being uncertain.

    ⚠️ THE FOURTH ROW IS THE ONE THAT DISCRIMINATES, and it is the one tuple
    ordering gets wrong on its own — measured by deleting the guard and watching
    exactly this row go red. ``parse_version`` yields an empty tuple for an
    unreadable tag, and an empty tuple compares BELOW everything — so an
    unreadable INSTALLED tag (rows 2, 3, 5) already answers "not fixed" by
    ordering alone, while an unreadable THRESHOLD compares at or below every
    install and would read as "every engine is fixed". One direction is safe by
    accident; the other has to be refused on purpose. The accidentally-safe rows
    are kept deliberately: they are the states a real install reaches (a profile
    launched mid-update reads an empty ``version.txt``), and a regression that
    changed ``parse_version``'s answer for them would turn them red here rather
    than reaching a user.
    """
    assert carries_measuretext_fix(installed, threshold) is False, why


def test_an_unset_threshold_is_not_read_as_every_engine_being_fixed():
    """The committed default is ``""``, and that must mean the OPPOSITE of done.

    Stated as its own test because it is the state the product ships in today,
    and because the empty string is the value most likely to be read as "nothing
    to do here". No published engine carries the fix — ``personium-152.0.7977.75``
    is the only release in ``engine/releases/`` and PS-406 measured its shipped
    binary returning −0.0006 — so the honest committed answer is "we cannot name
    a fixed build", which must leave the repair installed for everyone.

    ⭐ THIS IS THE ONE TEST IN THIS FILE THAT GOES RED ON RELEASE DAY, AND IT IS
    MEANT TO. It is a tripwire on the release obligation, not a claim about the
    gate — its first assertion pins a CONSTANT, and discharging the obligation is
    exactly the edit that moves it. That is why the failure message below tells
    the reader how to tell a discharge from a mistake.

    ⚠️ AND IT BEING THE ONLY ONE IS ITSELF LOAD-BEARING, measured rather than
    assumed. An earlier round wired the six malformed-override rows below to
    ``measuretext_fix_min_version() == ""`` — the fallback VALUE — so setting
    this constant turned SEVEN tests red, and the shortest path back to green
    was to edit six assertions that were correct about intent. Those rows now
    assert the BEHAVIOUR they are named for, against a NON-EMPTY committed
    default, so they survive the edit and keep testing the fail-open rule. If
    you are here on release day, set the constant and expect exactly this one
    failure; more than one means something else moved too.

    ⚠️ WHAT THIS ROW DOES **NOT** DISCRIMINATE, stated because it was MEASURED
    rather than assumed. ``carries_measuretext_fix`` refuses an unset threshold
    twice over: an explicit early return, and then the unparseable-tag guard
    (``parse_version("")`` is ``()``, which the second guard rejects). Deleting
    the early return ALONE leaves this test — and every test in this file —
    green; deleting BOTH turns ten of them red. So the behaviour is fenced by
    the conjunction and not per line, and a reader must not take the early
    return's presence as the only thing holding it. It is kept because it states
    the intent at the top of the function, where a reader looking for the
    fail-open decision will find it; the second guard is the backstop, and
    neither may be removed on the grounds that the other exists.
    """
    assert policy.MEASURETEXT_FIX_MIN_VERSION == "", (
        "the committed threshold is no longer empty. If a fixed engine has been "
        "PUBLISHED, this is the correct edit and the release obligation below is "
        "discharged — update that test too. If it was set in anticipation of a "
        "release, REVERT IT: a threshold at or below an installed version "
        "switches the repair off, and the engines in the field are unfixed."
    )
    assert carries_measuretext_fix("152.0.7977.75", "") is False
    assert carries_measuretext_fix("999.0.9999.99", "") is False


# --- the policy layer: a committed default plus an operator override --------


def test_the_operator_override_is_read_at_call_time(tmp_path, monkeypatch):
    """An engine fixed before persona's constant catches up is the operator's
    escape hatch, on the same two-layer pattern ``KNOWN_BAD_VERSIONS`` uses."""
    pf = tmp_path / "engine-policy.json"
    pf.write_text('{"measuretext_fix_min_version": "152.0.7977.75.1"}',
                  encoding="utf-8")
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))

    assert policy.measuretext_fix_min_version() == "152.0.7977.75.1"
    assert carries_measuretext_fix(
        "152.0.7977.75.1", policy.measuretext_fix_min_version()
    ) is True
    # ...and the engine in the field is still unfixed under that same override.
    assert carries_measuretext_fix(
        "152.0.7977.75", policy.measuretext_fix_min_version()
    ) is False


# The tag the release obligation names (PS-406: a FIFTH component). Used as the
# COMMITTED DEFAULT in the tests below, so they read the way they will read on
# release day rather than the way they read while the constant is empty.
_RELEASED = "152.0.7977.75.1"


def _repair_required_on(installed_tag, monkeypatch):
    """``measuretext_repair_required()`` with ``version.txt`` saying
    ``installed_tag`` — the real gate, reading a real policy lookup."""
    import src.services.engine.updater as updater

    monkeypatch.setattr(updater, "current_version", lambda: installed_tag)
    return measuretext_repair_required()


@pytest.mark.parametrize(
    "raw,why",
    [
        ('{"measuretext_fix_min_version": true}', "a BOOLEAN — 'trust me, it's in'"),
        ('{"measuretext_fix_min_version": 152}', "a bare int, naming no build"),
        ('{"measuretext_fix_min_version": ""}', "an empty string"),
        ('{"measuretext_fix_min_version": "   "}', "whitespace"),
        ('{"measuretext_fix_min_version": "fixed"}', "no digits to compare"),
        ("not json at all", "a corrupt policy file"),
        ('{"measuretext_fix_min_version": null}', "an explicit JSON null"),
        ('{"measuretext_fix_min_version": false}', "a boolean the OTHER way"),
        ('["not", "an", "object"]', "valid JSON that is not a policy object"),
    ],
)
def test_a_MALFORMED_override_cannot_switch_the_repair_off(
    raw, why, tmp_path, monkeypatch
):
    """A typo must not break Sheets.

    ⛔ ASSERTED AGAINST A **NON-EMPTY** COMMITTED DEFAULT, AND THAT IS THE WHOLE
    POINT OF THE TEST. An earlier round asserted
    ``measuretext_fix_min_version() == ""`` — the fallback VALUE — which is
    true today only because the committed default happens to be ``""`` too. Under
    that oracle every row here passed while the code did the OPPOSITE of what
    this test is named for: a malformed override fell back to the committed
    default, so the day that default named a released tag, a typo in an
    operator's policy file would switch the repair off and break Google Sheets
    on an unfixed engine. So these rows pin the BEHAVIOUR (``is the repair
    installed?``) with the default set to the tag the release obligation names,
    which is the state that makes the question non-vacuous.

    ⭐ THE ``true`` ROW IS THE ONE WORTH HAVING. It is the shape an operator
    would most plausibly reach for, and it is precisely the inherited claim
    PS-406's thread was written to stamp out: a boolean asserts the fix is in
    with no version to check it against.

    ⚠️ ONLY ONE ROW DISCRIMINATES THE TYPE CHECK, AND IT IS NOT THAT ONE —
    measured, not reasoned. Replacing ``isinstance(raw, str)`` with a blanket
    ``str(raw)`` coercion turns exactly the **bare-int** row red and leaves the
    others green, because every other malformed value survives coercion into
    something the digit guard below it already refuses (``str(True)`` is
    ``"True"``, which carries no digits; ``""`` and whitespace are empty). So
    ``152`` is the row that fences the type check, and the ``true`` row is
    fenced by the digit guard instead. Both rows stay: the first is what a
    mutation can see, the second is the case a human would actually write, and a
    test suite that only kept the mutation-visible one would stop documenting
    the hazard. ⛔ Do not "simplify" the assertion into the single row that
    happens to be load-bearing today.

    ⚠️ THE LAST TWO ROWS ARE NEW AND THEY FENCE ``_local_policy_entry``'s OWN
    SPLIT. ``null`` is what JSON produces for a key an operator half-cleared,
    and it must NOT read as "absent" — a ``.get()``-shaped lookup with a
    sentinel default would collapse the two and send this row to the committed
    default. A non-object document is the ``_UNREADABLE`` arm, which must land
    with the malformed values rather than with silence.
    """
    pf = tmp_path / "engine-policy.json"
    pf.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))
    monkeypatch.setattr(policy, "MEASURETEXT_FIX_MIN_VERSION", _RELEASED)

    assert _repair_required_on("152.0.7977.75", monkeypatch) is True, why
    # ...and on the released engine too: an unusable override states no
    # threshold AT ALL, which is not the same as falling back to one.
    assert _repair_required_on(_RELEASED, monkeypatch) is True, why


def test_the_operator_can_PUT_THE_REPAIR_BACK_on_a_machine_they_control(
    tmp_path, monkeypatch
):
    """⛔ THE ESCAPE HATCH ``process.py``'s remediation line names, and the one
    state a ``.get()``-shaped lookup cannot express.

    An operator whose canvas geometry breaks after an engine change is told, in
    the launch log, to put the repair back. That instruction has to be an action
    that WORKS, and "delete the key" cannot be it: deleting is
    byte-indistinguishable from never having written one, so it lands on the
    committed default — which, once non-empty, is the very thing withholding the
    repair. The only value that would restore it under that shape is an absurdly
    high threshold nobody would guess.

    ⭐ SO AN EXPLICITLY PRESENT EMPTY STRING IS THE GESTURE, and this test is
    what makes it real: same committed default, same engine, the ONLY difference
    between the two arms is whether the key is present. It is measured against a
    NON-EMPTY default because with an empty one both arms pass for free.
    """
    monkeypatch.setattr(policy, "MEASURETEXT_FIX_MIN_VERSION", _RELEASED)
    pf = tmp_path / "engine-policy.json"
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))

    # ARM 1 — the operator says NOTHING. The committed default speaks, and on a
    # released engine the repair is correctly omitted. (This is the arm that
    # makes arm 2 non-vacuous: without it, "the repair is installed" could just
    # mean the gate never switches off at all.)
    pf.write_text("{}", encoding="utf-8")
    assert policy.measuretext_fix_min_version() == _RELEASED
    assert _repair_required_on(_RELEASED, monkeypatch) is False

    # ARM 2 — the operator sets the key to an explicit empty string. NO
    # threshold is in force, so the repair comes back on the SAME engine.
    pf.write_text('{"measuretext_fix_min_version": ""}', encoding="utf-8")
    assert policy.measuretext_fix_min_version() == ""
    assert _repair_required_on(_RELEASED, monkeypatch) is True, (
        "the documented escape hatch is a no-op. An operator following the "
        "launch log's remediation line gets the identical command line, and the "
        "only lever that would work is an absurdly high threshold the log does "
        "not name."
    )


def test_ABSENT_and_PRESENT_BUT_EMPTY_are_not_the_same_fact(tmp_path, monkeypatch):
    """The distinction the hatch rests on, at the policy layer, over every way
    of saying nothing.

    ⚠️ NO FILE AT ALL and A FILE WITH NO SUCH KEY are both silence and must both
    reach the committed default — an operator who has never opened the policy
    file has expressed no opinion, and neither has one who set only
    ``max_tested_major``. Everything else in that file is an utterance.
    """
    monkeypatch.setattr(policy, "MEASURETEXT_FIX_MIN_VERSION", _RELEASED)
    pf = tmp_path / "engine-policy.json"
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))

    # No file on disk at all.
    assert not pf.exists()
    assert policy.measuretext_fix_min_version() == _RELEASED

    # A real file that says something ELSE.
    pf.write_text('{"max_tested_major": 160}', encoding="utf-8")
    assert policy.measuretext_fix_min_version() == _RELEASED

    # The same file, now carrying the key explicitly emptied.
    pf.write_text(
        '{"max_tested_major": 160, "measuretext_fix_min_version": ""}',
        encoding="utf-8",
    )
    assert policy.measuretext_fix_min_version() == ""


def test_the_OTHER_policy_readers_keep_their_own_fallback_direction(
    tmp_path, monkeypatch
):
    """⛔ THE NARROWED FALLBACK IS SCOPED TO THIS ONE VALUE, ON PURPOSE.

    ``known_bad_versions`` and ``max_tested_major`` fall back to the committed
    default on EVERY unusable override, and that is correct for them because
    their committed defaults are the safe answer — a local known-bad entry "only
    ever ADDs", so an unusable one can leave a build blocked and nothing worse.
    ``measuretext_fix_min_version``'s committed default REMOVES a repair, which
    inverts the safety direction and is why it does not share
    ``_local_policy``'s collapse.

    This test exists because the obvious "consistency" refactor is to give all
    three the same lookup. Doing so in either direction breaks one of them, so
    the divergence is asserted rather than left to a comment.
    """
    pf = tmp_path / "engine-policy.json"
    pf.write_text("not json at all", encoding="utf-8")
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))
    monkeypatch.setattr(
        policy, "KNOWN_BAD_VERSIONS", frozenset({"148.0.7778.215"})
    )
    monkeypatch.setattr(policy, "MEASURETEXT_FIX_MIN_VERSION", _RELEASED)

    # A corrupt file cannot UN-BLOCK a build persona ships knowing is broken...
    assert "148.0.7778.215" in policy.known_bad_versions()
    # ...and cannot invent a ceiling either.
    assert policy.max_tested_major() == policy.NO_CEILING
    # ...but it DOES state "no measureText threshold", because here the
    # committed default is the dangerous direction.
    assert policy.measuretext_fix_min_version() == ""


# --- the read: the version comes from the INSTALLED engine ------------------


def test_the_gate_reads_the_version_from_what_is_ACTUALLY_INSTALLED(monkeypatch):
    """Acceptance item 3, at the unit level.

    The version must come from ``version.txt`` — the record the update machinery
    itself writes and reads — and not from a constant anywhere in the masking
    layer. So this test moves ONLY that record and watches the answer follow it.
    """
    import src.services.engine.updater as updater

    monkeypatch.setattr(
        policy, "measuretext_fix_min_version", lambda: "152.0.7977.75.1"
    )

    monkeypatch.setattr(updater, "current_version", lambda: "152.0.7977.75")
    assert measuretext_repair_required() is True, (
        "the shipped engine is unfixed and must still get the repair"
    )

    monkeypatch.setattr(updater, "current_version", lambda: "152.0.7977.75.1")
    assert measuretext_repair_required() is False, (
        "a fixed engine must not carry the wrapper"
    )


def test_an_UNREADABLE_installed_version_still_gets_the_repair(monkeypatch):
    """The state a profile launched mid-update sees.

    ⛔ AND IT MUST NOT REFUSE THE LAUNCH. ``_mobile_chromium_version`` fails
    CLOSED on the same unreadable record, and that is correct there: it is about
    to ADVERTISE a version, and advertising a guessed one is a tell a checker
    reads. This gate is about whether a REPAIR is installed, and the two have
    opposite safe directions — so the asymmetry with its neighbour is deliberate
    rather than an inconsistency, and this test is where that is pinned.
    """
    import src.services.engine.updater as updater

    monkeypatch.setattr(
        policy, "measuretext_fix_min_version", lambda: "152.0.7977.75.1"
    )
    # What `current_version` returns for a missing or undecodable file — its own
    # contract, not an invention of this test.
    monkeypatch.setattr(updater, "current_version", lambda: "")

    assert measuretext_repair_required() is True


# --- the release obligation, kept visible -----------------------------------


def test_the_release_obligation_is_stated_where_it_must_be_edited():
    """The gate is inert until a fixed engine is PUBLISHED, and that is a
    sequencing dependency rather than an omission — so the line that has to be
    edited must say so in the tree, not only on a ticket.

    PS-349's follow-up was named in a merged report, nobody filed it, and the
    board could not see it because the ticket was closed. PS-409 was filed in
    the same motion as its own parent PR for that reason. This test is the same
    defence one layer down: a reader who finds ``MEASURETEXT_FIX_MIN_VERSION``
    empty must be able to tell "not yet published" from "somebody forgot".
    """
    import inspect

    src = inspect.getsource(policy)
    head = src[: src.index("MEASURETEXT_FIX_MIN_VERSION: str")]
    for needle in ("PS-345", "PS-406", "152.0.7977.75.1"):
        assert needle in head, (
            f"the comment above MEASURETEXT_FIX_MIN_VERSION no longer names "
            f"{needle!r}. That block is the release obligation — which tag to "
            "set, and why it cannot be the one every install already carries. "
            "Do not leave the constant empty with nothing saying what fills it."
        )


# --- the committed reading, fenced ------------------------------------------


def test_the_committed_reading_still_says_what_the_gate_claims():
    """The live-launch reading this change rests on, re-read rather than cited.

    The repo's own convention for a NOT_APPLICABLE / measured-outcome claim (see
    ``test_the_measuretext_not_applicable_cell_rests_on_a_measured_outcome`` and
    its stealth sibling): a cell resting on a reading must have the reading
    present in the tree AND still saying what the cell claims. A file that was
    emptied or re-measured to a different answer would satisfy an
    existence-check while the claim went on citing it.

    ⭐ THE FOUR ARMS ARE ASSERTED AS A PAIR OF PAIRS, not as four numbers. What
    the reading establishes is a DIFFERENCE attributable to the gate: B and C run
    the SAME binary and differ only in the gate's answer, and A and D likewise.
    Pinning the arms' raw widths would pin a host's font stack instead (the same
    reason ``fonts.measureText`` is in ``baseline.ENV_SENSITIVE_PROBES``), so the
    assertions are on RATIOS against that arm's own DOM reference — which is
    host-invariant by construction.
    """
    import pathlib

    reading = (
        pathlib.Path(__file__).resolve().parents[1]
        / "readings" / "ps409-2026-09-11" / "reading.json"
    )
    assert reading.is_file(), (
        "the live-launch reading PS-409's gate was established from is gone. "
        "Either restore it or re-measure with scripts/ps409_gate_reading.py — do "
        "not leave the gate resting on a measurement the tree no longer holds."
    )
    record = json.loads(reading.read_text(encoding="utf-8"))
    arms, ratios = record["arms"], record["ratios"]

    # Every verdict the instrument emits passed on the committed run.
    assert all(record["verdicts"].values()), record["verdicts"]

    # THE GATE: the unfixed arm carries the repair, the fixed arm does not, and
    # the only other difference between those two lists is nothing.
    assert "measuretext" in arms["A"]["_vectors"]
    assert "measuretext" not in arms["B"]["_vectors"]
    assert set(arms["A"]["_vectors"]) - set(arms["B"]["_vectors"]) == {"measuretext"}

    # ⭐ THE CLAIM, on ratios: geometry is sane wherever the repair is needed AND
    # present (A) or not needed at all (B, C), and COLLAPSED exactly where it is
    # needed and absent (D). D is the cost of the strict failure.
    for arm in ("A", "B", "C"):
        assert 0.9 < ratios[arm] < 1.1, f"arm {arm} geometry is not sane"
    assert abs(ratios["D"]) < 0.01, (
        "arm D no longer reads the ~1e-6 collapse, so the reading no longer "
        "demonstrates what omitting the repair on an unfixed engine costs"
    )

    # ⭐ AND THE INERTNESS, which is why omitting costs nothing: the same FIXED
    # binary with the wrapper installed (C) and absent (B) reports identical
    # widths. This is the Chromium counterpart of PR #327's Arm N.
    assert arms["B"]["widths"] == arms["C"]["widths"], (
        "the wrapper is no longer a structural no-op on a fixed engine, which is "
        "the premise the omission rests on. RE-MEASURE."
    )
    assert arms["B"]["_engine"] == arms["C"]["_engine"], (
        "arms B and C are no longer the same binary, so their agreement no "
        "longer isolates the gate"
    )


def test_the_reading_RECORDS_that_acceptance_2s_named_evidence_cannot_discriminate():
    """⛔ THE CORRECTION, pinned so it cannot quietly be forgotten.

    PS-409's acceptance item 2 names ``[native code]`` plus an unchanged
    ``getOwnPropertyNames`` as the evidence that no wrapper is present. The
    reading measured BOTH TRUE ON ALL FOUR ARMS — including the arm where the
    repair is installed and firing — because PS-368 gave every Chromium leaf its
    own toString cloak. The ticket's premise came from PR #327's Arm N, which is
    FIREFOX, where no such cloak exists.

    ⭐ SO THIS TEST ASSERTS THE NON-DISCRIMINATION ITSELF, and that is deliberate
    rather than perverse. If a future change made those two facts discriminate
    again — a leaf that stopped cloaking, say — this goes red and the next reader
    is told the finding has expired, instead of inheriting a correction that is
    no longer true. The discriminator the reading DID find (a native accessor
    invoked with the repair's Proxy as receiver throws) is asserted beside it.
    """
    import pathlib

    record = json.loads(
        (
            pathlib.Path(__file__).resolve().parents[1]
            / "readings" / "ps409-2026-09-11" / "reading.json"
        ).read_text(encoding="utf-8")
    )
    arms = record["arms"]

    for name, arm in arms.items():
        assert arm["isNativeCode"] is True, (
            f"arm {name} no longer reads [native code]. If a leaf stopped "
            "cloaking, PS-409's EVIDENCE.md §3 is out of date — RE-MEASURE and "
            "restate it rather than editing this assertion."
        )
        assert arm["ownProps"] == ["length", "name"], (
            f"arm {name}'s measureText own-property list moved. A third own name "
            "is the PS-368 tell returning."
        )

    def threw(arm):
        return str(arm["probeResults"]["widthGetterOnProxy"]).startswith("threw:")

    assert threw(arms["A"]), (
        "the repair no longer returns a Proxy on an unfixed engine, so the one "
        "observable that DID discriminate is gone. RE-MEASURE."
    )
    for name in ("B", "C", "D"):
        assert not threw(arms[name]), f"arm {name} unexpectedly reads as proxied"
