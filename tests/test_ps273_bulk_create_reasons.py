"""PS-273 — the UI lane of "which five, and why".

The service-layer half lives in ``tests/test_bulk.py`` (the reason text reaching
the caller, for all three reasoned causes). This file covers the two things
that happen ABOVE it, and that a service test cannot see:

* ``bulk_create_profiles.on_create`` returns a MESSAGE instead of ``None`` when
  anything was refused — which is the whole mechanism by which the dialog stays
  open (``dialogs/bulk.py`` treats ``None`` as success and pops the dialog).
* the Activity Log gets a durable line PER NAME, so the record survives the
  dialog being dismissed.

WHAT IS ASSERTED, AND WHAT IS DELIBERATELY NOT
----------------------------------------------
Every assertion here is about CONTENT: the reason sentence, the refused name,
and the "already saved" reassurance are each matched as text. Asserting that a
message is non-empty, or that a key exists, would pass against a build that
returned "Error" for all five refusals — which is the defect this ticket is
about, wearing the fix's shape.

The RENDERING of that message (error_text becoming visible, the dialog staying
on screen) is NOT asserted here, because a fake page proves nothing about a
real widget. It is DRIVEN live, through a real pointer, in
``tests/ui_driver/live_ps273.py``.
"""
import flet as ft
import pytest

from src.core.strings import get_string
from src.services.profile.manager import ProfileManager
from src.ui.actions.profile import bulk_create_profiles


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    pf = tmp_path / "profiles.json"
    dd = tmp_path / "data"
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(pf))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(dd))
    import src.core.config as cfg
    import src.services.profile.manager as mod

    monkeypatch.setattr(cfg, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(cfg, "DATA_DIR", str(dd))
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(mod, "DATA_DIR", str(dd))
    return ProfileManager()


class _CapturePage:
    """A page that records whether the dialog was popped.

    ``pop_dialog`` is the observable consequence of ``on_create`` returning
    ``None``, so the harness records it rather than the test having to trust
    the return value alone.
    """

    def __init__(self):
        self.shown = None
        self.popped = 0

    def show_dialog(self, dlg):
        self.shown = dlg

    def pop_dialog(self):
        self.popped += 1
        self.shown = None

    def update(self):
        pass


def _harness(mgr):
    """Open the real bulk dialog and hand back its real ``on_create``.

    ``bulk_create_profiles`` closes over ``on_create`` and passes it to
    ``open_bulk_dialog``; the dialog stores it on its ``[ create ]`` button.
    Pulling it back out of the built control tree means these tests exercise
    the function the BUTTON calls, not a re-implementation of it.
    """
    page = _CapturePage()
    log: list[str] = []
    refreshed: list[int] = []
    captured: dict = {}

    import src.ui.actions.profile as mod

    real_open = mod.open_bulk_dialog

    def _capture(p, on_create):
        captured["on_create"] = on_create
        return real_open(p, on_create)

    mod.open_bulk_dialog = _capture
    try:
        bulk_create_profiles(page, mgr, log.append, lambda: refreshed.append(1))
    finally:
        mod.open_bulk_dialog = real_open

    assert "on_create" in captured, "the dialog was never handed an on_create"
    return captured["on_create"], log, page


# --- AC2: the message that keeps the dialog open ---------------------------


def test_a_clean_batch_returns_none_so_the_dialog_closes(mgr):
    on_create, _log, _page = _harness(mgr)

    assert on_create("alpha\nbeta", "windows", "", []) is None, (
        "nothing was refused, so there is nothing to keep the dialog open for"
    )
    assert set(mgr.profiles) == {"alpha", "beta"}


def test_an_invalid_name_returns_its_reason_to_the_dialog(mgr):
    on_create, _log, _page = _harness(mgr)

    msg = on_create("good\nbad/name", "windows", "", [])

    assert msg is not None, (
        "a None return is read as success by dialogs/bulk.py and CLOSES the "
        "dialog — the exact defect PS-273 fixes"
    )
    assert "bad/name" in msg, "the operator must be told WHICH name"
    assert "Name contains invalid characters: /" in msg, (
        f"the operator must be told WHY, with the offending character; got "
        f"{msg!r}"
    )
    # And the name that worked is not listed as a problem.
    assert "good" in mgr.profiles


def test_an_existing_name_returns_its_reason_to_the_dialog(mgr):
    mgr.add_profile("alpha", "", "windows")
    on_create, _log, _page = _harness(mgr)

    msg = on_create("alpha\nbeta", "windows", "", [])

    assert msg is not None
    assert "alpha" in msg
    assert get_string("bulk_create_exists") in msg, (
        f"the already-exists cause must be distinguishable from the other "
        f"two; got {msg!r}"
    )
    assert "left unchanged" in msg, (
        f"the operator's real question is what happened to the EXISTING "
        f"profile; got {msg!r}"
    )


def test_an_incoherent_batch_returns_the_coherence_reason(mgr):
    """The case that matters most: the whole batch is refused, as one integer.

    Every name shares the batch's os_type, so an unstorable spelling refuses
    all of them — "skipped 50" with the cause available at the moment of the
    skip and thrown away.
    """
    on_create, _log, _page = _harness(mgr)

    msg = on_create("one\ntwo\nthree", "win", "", [])

    assert msg is not None
    for name in ("one", "two", "three"):
        assert name in msg, f"{name!r} was refused and must be named; got {msg!r}"
    assert "os_type 'win'" in msg
    assert "'windows'" in msg, (
        f"the refusal must name the spelling that WOULD work; got {msg!r}"
    )


def test_a_partial_success_says_the_created_ones_are_already_saved(mgr):
    """The dialog now STAYS OPEN on a partial success, so the operator's first
    question is whether the successes need re-submitting. They do not, and the
    message must say so — otherwise "keep the dialog open" invents a new way to
    create duplicates-by-anxiety."""
    mgr.add_profile("alpha", "", "windows")
    on_create, _log, _page = _harness(mgr)

    msg = on_create("alpha\nbeta\ngamma", "windows", "", [])

    assert set(mgr.profiles) == {"alpha", "beta", "gamma"}
    assert "Created 2 profiles" in msg, f"got {msg!r}"
    assert "already saved" in msg and "no need to submit them again" in msg, (
        f"the reassurance is load-bearing, not decoration; got {msg!r}"
    )


def test_a_wholly_refused_batch_does_not_claim_anything_was_created(mgr):
    on_create, _log, _page = _harness(mgr)

    msg = on_create("bad/one\nbad:two", "windows", "", [])

    assert mgr.profiles == {}
    assert "No profiles created" in msg, f"got {msg!r}"
    assert "already saved" not in msg


def test_repeats_in_the_paste_are_accounted_for(mgr):
    """`created + skipped` is fewer than the rows pasted when the paste repeats
    a name — the repeat is dropped before the loop and counted nowhere. Said
    out loud so the arithmetic on screen adds up.

    ASSERTED AGAINST THE REPEATS LINE ITSELF, not against the whole message
    (PR #209 review round 2): ``"repeated name" in msg and "alpha" in msg`` is
    satisfied by the head line and the refusal line independently, so it was
    weaker than its own name — it would pass against a build whose repeats line
    named the wrong profile, or none.
    """
    on_create, _log, _page = _harness(mgr)

    msg = on_create("alpha\nalpha\nbad/name", "windows", "", [])

    assert msg is not None
    repeat_line = next(
        (line for line in msg.split("\n") if "repeated name" in line), None
    )
    assert repeat_line is not None, f"no repeats line at all; got {msg!r}"
    assert "alpha" in repeat_line, (
        f"the repeats line must name the repeated profile itself, not rely on "
        f"the name appearing elsewhere in the message; got {repeat_line!r}"
    )
    assert "1 repeated name" in repeat_line, f"got {repeat_line!r}"


def test_repeats_alone_do_not_open_the_dialog_report(mgr):
    """A repeat is NOT a refusal. A paste whose only oddity is a repeated name
    created everything the operator asked for, so it must still close."""
    on_create, _log, _page = _harness(mgr)

    assert on_create("alpha\nalpha\nbeta", "windows", "", []) is None
    assert set(mgr.profiles) == {"alpha", "beta"}


def test_a_repeat_is_recorded_even_when_nothing_was_refused(mgr):
    """The clean-paste case, and the one the arithmetic gap is WORST in.

    "Must not hold the dialog open" and "must not be recorded at all" are
    different claims. A paste of three rows that yields two profiles, with a
    dialog that closes and a log reading "created 2, skipped 0", accounts for
    the third row NOWHERE — which is exactly the unexplained arithmetic this
    ticket's third decision is about. The dialog still closes (a repeat is not
    a refusal); the durable record is what carries it.
    """
    on_create, log, _page = _harness(mgr)

    assert on_create("alpha\nalpha\nbeta", "windows", "", []) is None, (
        "a repeat must not hold the dialog open — nothing was refused"
    )
    blob = "\n".join(log)
    assert "alpha was listed more than once" in blob, (
        f"the operator pasted 3 rows and got 2 profiles; the third must be "
        f"accounted for somewhere. got {log!r}"
    )
    assert "created once" in blob, (
        f"the record must say what HAPPENED to the repeat (it was created, "
        f"once) — not merely that one existed; got {log!r}"
    )
    # The record is per name, on the bulk-delete model: two distinct repeated
    # names are two lines, not one aggregate.
    on_create2, log2, _p2 = _harness(mgr)
    on_create2("x\nx\ny\ny\nz", "windows", "", [])
    blob2 = "\n".join(log2)
    for name in ("x", "y"):
        assert f"{name} was listed more than once" in blob2, f"got {log2!r}"


def test_the_repeat_log_line_carries_no_severity_token(mgr):
    """A repeat is an ordinary batch record, not a failure — it must not paint
    the red dot in the Activity Log. BOTH wordings, since the refused variant
    is the one whose vocabulary is closest to the SEV_FAIL substrings
    ("refused" is one of them, which is why it says "not created")."""
    from src.ui.log_console import SEV_IDLE, severity

    assert severity(get_string("bulk_create_repeat_logged", name="alpha")) == SEV_IDLE
    assert (
        severity(get_string("bulk_create_repeat_refused_logged", name="alpha"))
        == SEV_IDLE
    )


# --- the repeat record must not claim an outcome the batch did not produce ---
#
# MEASURED (PR #209 review round 2). `repeats` is a property of the PASTE — it
# is computed from the text before `bulk_create` runs — but the line it wrote
# ("was listed more than once - created once") is a claim about the OUTCOME.
# For a repeated name that was REFUSED that claim is false, and it is durable:
# it survives the dialog, it sits one row below the refusal line contradicting
# it, and it carries the idle dot so nothing marks it suspect. Nothing in the
# shipped suite separated the two behaviours — the nearest test pasted 150
# repeated names that all DID get created, pinning only the true case.


def test_a_repeated_name_that_was_REFUSED_is_not_logged_as_created(mgr):
    """The defect, at its smallest: one name, listed twice, refused."""
    mgr.add_profile("alpha", "", "windows")
    on_create, log, _page = _harness(mgr)

    on_create("alpha\nalpha\nbeta", "windows", "", [])

    line = next(
        (line for line in log if "alpha" in line and "listed more than once" in line),
        None,
    )
    assert line is not None, f"the repeat must still be recorded; got {log!r}"
    assert "created once" not in line, (
        f"alpha was listed twice and created ZERO times — the durable record "
        f"must not claim a creation the batch did not produce. This line sits "
        f"one row below 'alpha not created: ...' in the same log. got {line!r}"
    )
    assert "not created" in line, (
        f"the record must say what actually happened, not merely omit the "
        f"false claim; got {line!r}"
    )
    # ...and the name that genuinely WAS created still gets the creating
    # wording, so this is a per-name choice rather than a blanket downgrade.
    beta_line = next(
        (line for line in log if "beta" in line and "listed more than once" in line),
        None,
    )
    assert beta_line is None, "beta was not repeated, so it gets no repeat line"


def test_an_invalid_repeated_name_is_never_logged_as_created(mgr):
    """`bad/name` contains "/", which `validate_profile_name` refuses precisely
    because such a profile can never exist. A durable log line saying it was
    created is not merely imprecise — it names a thing the system is incapable
    of having made."""
    on_create, log, _page = _harness(mgr)

    on_create("bad/name\nbad/name\nfresh", "windows", "", [])

    assert set(mgr.profiles) == {"fresh"}
    blob = "\n".join(log)
    assert "bad/name was listed more than once - created once" not in blob, (
        f"a name containing '/' cannot be a profile; got {log!r}"
    )
    assert "bad/name was listed more than once - not created" in blob, f"got {log!r}"


def test_a_wholly_refused_batch_logs_no_creation_claim_for_any_repeat(mgr):
    """The case the ticket singles out as mattering most, and the case this
    defect was WORST in.

    The batch shares one os_type, so an unstorable spelling refuses EVERY
    name — and every repeated name then got a "created once" line. At 200
    names that is 200 durable lines asserting creation against zero profiles:
    the majority of what the operator's log holds after the batch, and
    affirmatively wrong rather than merely uninformative like the aggregate
    integer it replaced.
    """
    names = [f"n{i}" for i in range(5)]
    paste = "\n".join([n for n in names for _ in (0, 1)])
    on_create, log, _page = _harness(mgr)

    msg = on_create(paste, "win", "", [])

    assert mgr.profiles == {}, "the whole batch must have been refused"
    assert msg is not None and "No profiles created" in msg
    blob = "\n".join(log)
    assert "created once" not in blob, (
        f"zero profiles were created; no line may say otherwise. got {log!r}"
    )
    # The repeats are STILL recorded — the operator did type each name twice,
    # the inline cap defers to this record, and dropping the lines would leave
    # `bulk_create_repeats_more` pointing at nothing for exactly these names.
    for n in names:
        assert f"{n} was listed more than once - not created" in blob, (
            f"{n!r} missing its (corrected) repeat record; got {log!r}"
        )


def test_the_inline_repeats_line_does_not_claim_an_outcome_either(mgr):
    """The same conflation one level softer: the inline line said the repeated
    names were "entered once" while rendering directly under a refusal line
    saying one of them was not created at all. The two surfaces must not
    disagree, so the inline line states the ATTEMPT (true of every name in the
    list, whatever happened next) and leaves the outcome to the refusal lines
    above it and the per-name log lines behind it."""
    on_create, _log, _page = _harness(mgr)

    msg = on_create("bad/name\nbad/name\nfresh", "windows", "", [])

    repeat_line = next(
        (line for line in msg.split("\n") if "repeated name" in line), None
    )
    assert repeat_line is not None, f"got {msg!r}"
    assert "bad/name" in repeat_line
    assert "entered once" not in repeat_line, (
        f"'entered once' reads as an outcome for a name that was refused, "
        f"directly under the line refusing it; got {repeat_line!r}"
    )
    assert "attempted once" in repeat_line, f"got {repeat_line!r}"


def test_a_long_repeat_list_is_capped_like_the_refusal_list(mgr):
    """MEASURED (PR #209 review): the refusal block was capped and the repeats
    line was not, so a paste repeating 150 names rendered a ~1550-char single
    line — 2x the painted height of the whole error region — pushing the
    "Operating system" and "Tags" controls, which sit BELOW error_text in the
    layout, out of the dialog's scroll viewport. That is the exact failure
    _INLINE_REFUSAL_LIMIT exists to prevent, reached through the other door.
    """
    from src.ui.actions.profile import _INLINE_REPEAT_LIMIT

    names = [f"n{i}" for i in range(150)]
    paste = "\n".join(names + names + ["bad/name"])
    on_create, log, _page = _harness(mgr)

    msg = on_create(paste, "windows", "", [])

    assert msg is not None
    repeat_line = next(
        (line for line in msg.split("\n") if "repeated name" in line and "..." not in line),
        None,
    )
    assert repeat_line is not None, f"got {msg!r}"
    assert len(repeat_line) < 200, (
        f"the repeats line is unbounded again: {len(repeat_line)} chars. It "
        f"renders above the controls the operator corrects the paste with."
    )
    # The TRUE count is still stated even though the names are not all listed —
    # the cap must not misreport the size of the problem.
    assert "150 repeated names" in repeat_line, f"got {repeat_line!r}"
    assert "more repeated names" in msg and "Activity Log" in msg, (
        f"the overflow must be pointed at, not dropped; got {msg!r}"
    )
    # NOTHING is lost: the durable record carries every one of them, which is
    # what makes the cap safe (the same contract the refusal cap relies on).
    blob = "\n".join(log)
    for n in names:
        assert f"{n} was listed more than once" in blob, f"{n!r} missing from the log"
    # And the whole message stays small enough to leave the dialog usable.
    assert len(msg) < 1200, f"message is {len(msg)} chars: {msg[:200]!r}..."
    assert _INLINE_REPEAT_LIMIT <= 12


def test_a_worst_case_paste_bounds_the_whole_message(mgr):
    """Both lists long at once — the message must stay bounded on BOTH axes,
    not on whichever one happens to be capped."""
    bad = [f"bad{i}/x" for i in range(60)]
    rep = [f"r{i}" for i in range(120)]
    on_create, _log, _page = _harness(mgr)

    msg = on_create("\n".join(bad + rep + rep), "windows", "", [])

    assert msg.count("\n") < 20, f"{msg.count(chr(10))} lines: {msg!r}"
    assert len(msg) < 1500, f"{len(msg)} chars"
    assert "60" in msg and "120 repeated names" in msg, (
        f"both true counts must survive the capping; got {msg!r}"
    )


def test_a_long_refusal_list_defers_to_the_log_instead_of_flooding(mgr):
    names = [f"bad{i}/x" for i in range(30)]
    on_create, log, _page = _harness(mgr)

    msg = on_create("\n".join(names), "windows", "", [])

    assert msg.count("\n") < 20, (
        "a 30-refusal paste must not push the paste field and [ create ] off "
        "the screen — that turns 'fix it in place' back into 'you cannot'"
    )
    assert "more refusals" in msg and "Activity Log" in msg, f"got {msg!r}"
    # NOTHING is lost: the log still carries every one of them.
    for n in names:
        assert any(n in line for line in log), f"{n!r} missing from the log"


def test_the_log_line_leads_with_the_name_so_parse_event_renders_it(mgr):
    """MEASURED, and the reason the wording is not "Not created: {name} - ...".

    `log_console.parse_event` HOISTS a known profile name out of the prose into
    the row's own profile column. A message that puts the name in the MIDDLE
    renders with a dangling separator where the name was ("Not created: - a
    profile with that name exists"). Leading with the name reads correctly both
    when it is hoisted and when it is not.
    """
    from src.ui.log_console import parse_event

    mgr.add_profile("alpha", "", "windows")
    on_create, log, _page = _harness(mgr)
    on_create("alpha\nbad/name", "windows", "", [])

    roster = set(mgr.profiles)
    for line in log:
        if "not created" not in line:
            continue
        _stamp, profile, message, _sev = parse_event(f"12:00:00  > {line}", roster)
        assert not message.startswith("Not created: -"), (
            f"the hoist left a dangling separator: {message!r}"
        )
        assert message.strip(), f"the hoist emptied the message: {line!r}"
        if profile:
            # The name went to its own column; what remains must still read as
            # a sentence about that profile.
            assert message.startswith("not created:"), f"got {message!r}"
        else:
            assert "not created:" in message, f"got {message!r}"


# --- AC3: the durable per-name record --------------------------------------


def test_the_log_carries_a_line_per_refused_name_with_its_reason(mgr):
    mgr.add_profile("alpha", "", "windows")
    on_create, log, _page = _harness(mgr)

    on_create("alpha\nbeta\nbad/name", "windows", "", [])

    blob = "\n".join(log)
    # The aggregate line is KEPT as the batch header — it is not the defect,
    # being the whole story was.
    assert "bulk create: created 1, skipped 2" in blob, f"got {log!r}"
    # Per NAME, with the reason, on the model of the bulk DELETE lane.
    assert (
        f"alpha not created: {get_string('bulk_create_exists')}" in blob
    ), f"got {log!r}"
    assert (
        "bad/name not created: Name contains invalid characters: /" in blob
    ), f"got {log!r}"
    # The successes are named too, matching single-create's "Created: {name}".
    assert "Created: beta" in blob, f"got {log!r}"


def test_the_log_record_survives_without_the_dialog(mgr):
    """AC3 is about DURABILITY: the log lines are written whether or not the
    operator ever reads the inline message."""
    on_create, log, _page = _harness(mgr)

    on_create("one\ntwo", "win", "", [])

    for name in ("one", "two"):
        line = next((line for line in log if f"{name} not created:" in line), None)
        assert line is not None, f"no per-name log line for {name!r}: {log!r}"
        assert "os_type 'win'" in line, (
            f"the log line must carry the REASON, not just the name; got {line!r}"
        )


def test_no_severity_token_was_added_for_the_new_lines(mgr):
    """Out of scope, and pinned: `log_console.severity()` classifies the new
    per-name line by the SAME rules as the aggregate line and "Created: {name}"
    — no token was added and none was changed."""
    from src.ui.log_console import SEV_IDLE, severity

    assert severity("bulk create: created 1, skipped 2") == SEV_IDLE
    assert severity("Created: beta") == SEV_IDLE
    # The refusal line's own words carry no token either. (An interpolated
    # REASON can contain "error"/"fail" and would then read as SEV_FAIL —
    # which is honest for a refusal, and is data classified by the existing
    # rules, not a rule change.)
    assert severity("alpha not created: a name this build cannot store") == SEV_IDLE
    # THE MEASURED TRAP, pinned here rather than left to be rediscovered:
    # severity() matches "ready" INSIDE "already", so `profile_exists`
    # ("Profile already exists!") classifies as SEV_OK and would paint the
    # GREEN SUCCESS dot on a refusal. That is why the bulk lane uses
    # `bulk_create_exists` and why severity() itself is untouched.
    assert severity("Profile already exists!") != SEV_IDLE
    # THE SECOND TRAP, from the same family: the DEFENSIVE fallback used for a
    # skipped name that arrived without a reason. It used to be
    # get_string("error") — the bare word "Error" — which severity() matches,
    # so the one line the lane writes while knowing LESS than usual would have
    # been the LOUDEST line it writes. Unreachable today (bulk_create writes
    # `skipped` and `reasons` together through _refuse), so it is pinned here
    # rather than left to be rediscovered when a future caller reaches it.
    assert severity(get_string("error")) != SEV_IDLE, (
        "if this ever stops matching, the fallback below is over-cautious "
        "rather than wrong — but check before reverting it"
    )
    assert (
        severity(
            get_string(
                "bulk_create_not_created",
                name="x",
                reason=get_string("bulk_create_no_reason"),
            )
        )
        == SEV_IDLE
    ), "the no-reason fallback must classify like every other line this lane writes"
    for key in ("bulk_create_exists", "bulk_create_no_reason"):
        assert severity(f"x not created: {get_string(key)}") == SEV_IDLE, (
            f"{key} carries a severity token and would mis-paint a refusal"
        )


# --- the dialog still builds ------------------------------------------------


def test_the_bulk_dialog_still_builds_with_the_real_on_create(mgr):
    """The message path is only reachable if the dialog builds at all."""
    _on_create, _log, page = _harness(mgr)

    assert page.shown is not None
    assert isinstance(page.shown, ft.AlertDialog)
