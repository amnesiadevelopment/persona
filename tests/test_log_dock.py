"""PS-179 headless behaviour checks for the Activity Log console.

These cover the logic that does not need a browser: the parse, the row geometry,
the append contract and the follow/pause state machine.

AC1/AC2/AC6/AC7/AC8 are ALSO driven against the RUNNING app, by
``tests/ui_driver/live_log_dock.py`` (run it with
``python3 -m tests.ui_driver.live_log_dock``) — a handler called directly cannot
distinguish the fixed scrolling from the broken one, because the broken one has
a scroll region too. That driver is committed rather than scratch, so the check
that found the self-pausing-follow defect can be re-run by anyone.

AC3 (the resize grip) is NOT covered live: the grip is a GestureDetector with no
label and paints no semantics node, so it is not addressable by the driver. Its
clamp and its collapse/expand round trip are covered headless below.
"""

import os
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import flet as ft  # noqa: E402

from src.ui.components.log_dock import (  # noqa: E402
    COLLAPSED_HEIGHT,
    MAX_HEIGHT,
    MAX_ROWS,
    MIN_HEIGHT,
    OPEN_HEIGHT,
    RAIL_CONTENT_HEIGHT,
    LogDock,
    default_height,
    height_for_rows,
)
from src.ui.log_console import (  # noqa: E402
    NO_PROFILE,
    ROW_HEIGHT,
    SEV_COLOR,
    SEV_FAIL,
    SEV_IDLE,
    event_row,
    parse_event,
)


class Ev:
    """A scroll notification shaped like the ones flet really delivers."""

    def __init__(self, kind, pixels=0.0, max_extent=0.0):
        self.event_type = kind
        self.pixels = pixels
        self.max_scroll_extent = max_extent
        self.scroll_delta = None
        self.direction = None


def gesture(dock, pixels, extent):
    """One complete USER gesture: USER -> UPDATE -> END, as flet sends it."""
    dock._on_scroll(Ev("user", pixels, extent))
    dock._on_scroll(Ev("update", pixels, extent))
    dock._on_scroll(Ev("end", pixels, extent))


def autoscroll_frames(dock, extent, n=40):
    """What auto_scroll's own animation emits: no USER notification at all.

    This is the sequence that made the console pause itself — see
    LogDock._on_scroll. Every frame reports a position short of the extent it
    is animating toward.
    """
    dock._on_scroll(Ev("start", 0.0, extent))
    for i in range(n):
        dock._on_scroll(Ev("update", extent * (i / (n * 2.0)), extent))
    dock._on_scroll(Ev("end", extent * 0.5, extent))


ROSTER = {"shop-us-01", "shop-de-03", "mail-us-011", "shop-us-1"}


def test_ac5_profile_is_parsed_from_all_four_shapes():
    cases = [
        ("10:00:01  > Launching shop-de-03", "shop-de-03", "Launching"),
        (
            "10:00:02  > shop-us-01: LAUNCH_FAILED: engine firefox-142 missing",
            "shop-us-01",
            "LAUNCH_FAILED: engine firefox-142 missing",
        ),
        (
            "10:00:03  > Loaded 6 bookmarks, 0 pools for shop-us-01",
            "shop-us-01",
            "Loaded 6 bookmarks, 0 pools",
        ),
        ("10:00:04  > Session ended: mail-us-011", "mail-us-011", "Session ended"),
        # PS-298: the same event carrying WHY it ended. The Linux close is an
        # INFERENCE (persona decided the window was gone, from a content-process
        # count that has four other causes on a live browser), and it used to
        # render byte-identically to an operator close. The profile must still
        # resolve, and the reason must survive into the rendered row — falling
        # through to the generic name-substitution branch would leave a dangling
        # ": " and lose the distinction the suffix exists to carry.
        (
            "10:00:05  > Session ended: mail-us-011 (persona inferred the "
            "window was closed)",
            "mail-us-011",
            "Session ended (persona inferred the window was closed)",
        ),
    ]
    for line, want_profile, want_msg in cases:
        _, profile, msg, _ = parse_event(line, ROSTER)
        assert profile == want_profile, (line, profile)
        assert msg == want_msg, (line, msg)


def test_ac5_unresolvable_profile_shows_the_neutral_placeholder():
    _, profile, msg, _ = parse_event("10:00:05  > Engine update available", ROSTER)
    assert profile == ""
    assert (profile or NO_PROFILE) == NO_PROFILE
    assert msg == "Engine update available"


def test_ac5_a_longer_name_is_not_shadowed_by_its_own_prefix():
    _, profile, _, _ = parse_event("10:00:06  > Launching shop-us-1", ROSTER)
    assert profile == "shop-us-1"


def test_ac4_every_row_is_the_same_height_however_long_the_message():
    rows = [
        event_row("10:00:01  > Launching shop-de-03", ROSTER),
        event_row("10:00:02  > shop-us-01: " + "x" * 500, ROSTER),
        event_row("10:00:03  > Engine update available", ROSTER),
    ]
    assert {r.height for r in rows} == {ROW_HEIGHT}


def test_ac4_no_column_wraps_and_the_timestamp_is_right_aligned():
    row = event_row("10:00:01  > Launching shop-de-03", ROSTER)
    dot, profile, message, time_col = row.content.controls
    for col in (profile, message, time_col):
        text = col.content
        assert text.no_wrap is True
        assert text.max_lines == 1
    assert time_col.alignment.x == 1.0  # CENTER_RIGHT
    assert profile.width is not None  # a fixed ruler, not a shrink-to-fit


def _dock(profiles=("p",)):
    d = LogDock()
    d.set_profiles(set(profiles))
    return d


def _feed(dock, lines, msg):
    lines.append(f"10:00:00  > p: {msg}")
    dock.render(list(lines), seq=len(lines))


def test_ac7_events_are_appended_not_re_rendered():
    d, lines = _dock(), []
    for i in range(5):
        _feed(d, lines, f"event {i}")
    first = id(d.list.controls[0])
    _feed(d, lines, "one more")
    assert id(d.list.controls[0]) == first, "the existing rows were rebuilt"
    assert d.row_count == 6


def test_ac6_a_real_gesture_up_pauses_the_follow():
    d, lines = _dock(), []
    for i in range(40):
        _feed(d, lines, f"event {i}")
    assert d.state.following is True

    gesture(d, pixels=10.0, extent=900.0)
    assert d.state.following is False
    assert d._follow_label.value == "paused — reading"
    assert d.list.auto_scroll is False
    assert d._jump.visible is True


def test_ac6_auto_scroll_animation_never_pauses_the_follow():
    """The defect the live run found: the console paused itself.

    Appending grows max_scroll_extent immediately while auto_scroll ANIMATES
    pixels toward it, so every frame reports a position short of the end. Read
    as a gesture, that pauses a stream nobody touched.
    """
    d, lines = _dock(), []
    for i in range(40):
        _feed(d, lines, f"event {i}")
        autoscroll_frames(d, extent=40.0 + i * 22)

    assert d.state.following is True, "auto-scroll paused the stream by itself"
    assert d._follow_label.value == "following"
    assert d.state.missed == 0
    assert d._jump.visible is False


def test_ac6_the_position_survives_more_than_ten_flushes():
    d, lines = _dock(), []
    for i in range(40):
        _feed(d, lines, f"event {i}")
    gesture(d, pixels=10.0, extent=900.0)

    anchor, before = id(d.list.controls[0]), d.row_count
    for i in range(12):
        _feed(d, lines, f"while paused {i}")
        autoscroll_frames(d, extent=900.0 + i)

    assert id(d.list.controls[0]) == anchor, "rows shifted under the reader"
    assert d.row_count == before + 12
    assert d.state.following is False
    assert d.state.missed == 12
    assert d._jump_label.value == "12 new"


def test_ac6_jump_returns_to_the_newest_entry_in_one_call():
    d, lines = _dock(), []
    for i in range(20):
        _feed(d, lines, f"event {i}")
    gesture(d, pixels=10.0, extent=900.0)
    assert d.state.following is False

    d.resume()
    assert d.state.following is True
    assert d.state.missed == 0
    assert d.list.auto_scroll is True
    assert d._jump.visible is False
    assert d._follow_label.value == "following"


def test_ac6_scrolling_back_to_the_bottom_resumes_the_follow():
    d, lines = _dock(), []
    for i in range(20):
        _feed(d, lines, f"event {i}")
    gesture(d, pixels=10.0, extent=900.0)
    assert d.state.following is False

    gesture(d, pixels=900.0, extent=900.0)
    assert d.state.following is True


def test_ac7_the_retained_tail_is_far_deeper_than_the_old_six_lines():
    d, lines = _dock(), []
    for i in range(MAX_ROWS + 50):
        lines.append(f"10:00:00  > p: e{i}")
    d.render(list(lines), seq=len(lines))

    assert d.row_count == MAX_ROWS
    assert d.row_count > 100
    # Materially more scrollback than the console is tall.
    assert d.scrollback_px > MAX_HEIGHT * 10


def test_ac7_the_cap_never_trims_rows_under_a_reader():
    d, lines = _dock(), []
    for i in range(MAX_ROWS + 50):
        lines.append(f"10:00:00  > p: e{i}")
    d.render(list(lines), seq=len(lines))

    gesture(d, pixels=10.0, extent=9000.0)
    anchor = id(d.list.controls[0])
    for i in range(30):
        _feed(d, lines, f"more{i}")

    assert id(d.list.controls[0]) == anchor
    assert d.row_count > MAX_ROWS, "trimming while paused would shift the view"

    d.resume()
    assert d.row_count == MAX_ROWS


def test_ac3_the_grip_is_clamped_at_both_ends():
    d = _dock()
    d.set_height(10_000)
    assert d.height == MAX_HEIGHT
    d.set_height(1)
    assert d.height == MIN_HEIGHT
    # A height in between is SNAPPED to whole rows rather than taken verbatim
    # — that is the direction's argument, not a rounding artefact. 300px is
    # 11 rows and a 4px sliver, and the sliver is the "пустое место" the
    # console is being rebuilt to remove. See quantize().
    d.set_height(300)
    assert d.height == height_for_rows(11) == 296
    assert d.rows == 11


def test_ac2_and_ac3_collapse_reports_and_the_height_survives_the_round_trip():
    d, lines = _dock(), []
    for i in range(5):
        _feed(d, lines, f"event {i}")
    chosen = height_for_rows(11)
    d.set_height(chosen)

    d.toggle()
    assert d.state.collapsed is True
    assert d.body.visible is False
    assert d.collapsed_strip.content.height == COLLAPSED_HEIGHT

    lines.append("10:00:00  > shop-de-03: LAUNCH_FAILED: engine firefox-142 missing")
    d.render(list(lines), seq=len(lines))
    lines.append("10:00:00  > p: arrived while shut")
    d.render(list(lines), seq=len(lines))

    assert d._counter.value == "+2"
    assert "arrived while shut" in d._peek.value

    d.toggle()
    assert d.state.collapsed is False
    assert d.height == chosen and d.body.height == chosen
    assert d._counter.value == ""


def test_a_cleared_ring_rebuilds_rather_than_appending_wiped_names():
    """The panic wipe resets the sequence; painted rows must not survive it."""
    d, lines = _dock(), []
    for i in range(10):
        _feed(d, lines, f"secret-name {i}")
    assert d.row_count == 10

    d.render([], seq=0)
    assert d.row_count == 0


def test_ac8_a_short_window_costs_the_dock_height_not_the_rail_its_controls():
    """Measured on the running app at the app's own minimum size (1024x680).

    A constant 236px dock left the rail 444px against the ~545px its own
    content needs, so the nav scrolled, `trash` fell below the fold and the
    engines dropdown was clipped. The dock is what yields, because it is the
    one the operator can drag back.
    """
    from src.ui.components.log_dock import RAIL_CONTENT_HEIGHT, default_height

    # At the minimum window size the rail keeps everything it needs.
    assert 680 - default_height(680) >= RAIL_CONTENT_HEIGHT
    # A roomy window still opens at the preferred height.
    assert default_height(950) == OPEN_HEIGHT
    assert default_height(1200) == OPEN_HEIGHT
    # Never below the grip's own floor, and safe when the height is unknown.
    assert default_height(300) == MIN_HEIGHT
    assert default_height(None) == OPEN_HEIGHT


def test_the_window_budget_reaches_the_console_it_sizes():
    d = LogDock(window_height=680)
    assert d.height == default_height(680)
    assert d.body.height == d.height


def test_ac2_the_collapsed_pulse_agrees_with_the_row_it_stands_for():
    """The pulse must classify the SAME TEXT the row does.

    It used to classify the RAW stored line, and severity() has anchored rules
    — `startswith("session ended")` — that a leading timestamp silently
    defeats. So "10:00:04  > Session ended: mail-us-011" painted a red FAIL row
    in the open console and a grey IDLE pulse on the collapsed strip, for the
    same event. Collapsed is the one state where the pulse is the ONLY signal
    the operator gets, which is what made the disagreement worth blocking on.

    Asserted as an EQUALITY against the row's own colour rather than against a
    hardcoded hex, so any future anchored rule is covered by the same test.
    """
    roster = {"mail-us-011", "shop-de-03"}
    cases = [
        "10:00:04  > Session ended: mail-us-011",  # the anchored rule: was grey
        "10:00:05  > shop-de-03: LAUNCH_FAILED: engine firefox-142 missing",
        "10:00:06  > Launching shop-de-03",
        "10:00:07  > Browser started for shop-de-03",
        "10:00:08  > Engine update available",  # no resolvable profile
    ]
    for line in cases:
        d = _dock(roster)
        d.render([line], seq=1)
        row_sev = parse_event(line, frozenset(roster))[3]
        assert d._pulse.bgcolor == SEV_COLOR[row_sev], (
            f"pulse disagrees with its own row for {line!r}: "
            f"row={row_sev}/{SEV_COLOR[row_sev]} pulse={d._pulse.bgcolor}"
        )


def test_ac2_the_session_ended_pulse_is_specifically_not_idle():
    """Guards the exact regression, so the equality above cannot pass vacuously
    by both sides degrading to the same neutral value."""
    roster = {"mail-us-011"}
    d = _dock(roster)
    d.render(["10:00:04  > Session ended: mail-us-011"], seq=1)
    assert d._pulse.bgcolor == SEV_COLOR[SEV_FAIL]
    assert d._pulse.bgcolor != SEV_COLOR[SEV_IDLE]


def test_ac8_the_rail_budget_survives_a_RESIZE_not_only_a_launch():
    """AC8 is a property of the window SIZE, not of how the window got there.

    The budget was computed exactly once, at build time, from the startup
    height. The app opens at 1280x820 with a 1024x680 minimum, so dragging DOWN
    to the minimum is an ordinary supported gesture — and it left a 236px dock
    in a 680px window, i.e. 444px for a rail needing 560px: short by 116px, the
    same starvation the budget was written to fix, reached through the resize
    path instead of the launch path.
    """
    d = LogDock(window_height=820)
    assert 820 - d.height >= RAIL_CONTENT_HEIGHT, "premise: launching tall is fine"

    d.apply_window_height(680)
    assert 680 - d.height >= RAIL_CONTENT_HEIGHT, (
        f"resizing into the minimum starved the rail by "
        f"{RAIL_CONTENT_HEIGHT - (680 - d.height)}px"
    )
    # and the applied height reaches the control that actually paints it
    assert d.body.height == d.height


def test_ac8_a_resize_yields_height_but_does_not_overwrite_a_deliberate_drag():
    """A shrink borrows height; a regrow HANDS IT BACK.

    The operator's own deliberate height must not be permanently rewritten to
    the opening default just because he nudged the window smaller and back.
    """
    chosen = height_for_rows(15)
    d = LogDock(window_height=1200)
    d.set_height(chosen)
    assert d.height == chosen

    d.apply_window_height(680)
    assert 680 - d.height >= RAIL_CONTENT_HEIGHT, "the rail was starved on shrink"
    assert d.height < chosen, "the console did not yield any height"

    d.apply_window_height(1200)
    assert d.height == chosen, "his deliberate height was not handed back"


def test_ac8_an_unmeasurable_resize_leaves_the_console_alone():
    """A resize that cannot be measured must not collapse the console to the
    floor — a missing height is unknown, not zero."""
    kept = height_for_rows(11)
    d = LogDock(window_height=1200)
    d.set_height(kept)
    d.apply_window_height(None)
    assert d.height == kept
    d.apply_window_height(0)
    assert d.height == kept


# ---------------------------------------------------------------------------
# PS-229: the console is sized in ROWS, the grip is bounded by the rail's
# budget, and a drag accumulates. These complement the LIVE driving in
# tests/ui_driver/live_log_dock.py — they are not a substitute for it.
# ---------------------------------------------------------------------------


def test_ps229_every_height_the_console_can_hold_is_a_whole_number_of_rows():
    """The direction's argument, asserted as a property rather than a case.

    A console sized in PIXELS lands mid-row — five rows and a two-pixel
    sliver of a sixth — and that sliver is exactly the "пустое место" the
    owner reported. So no reachable height may sit between two row counts.
    """
    from src.ui.components.log_dock import (
        MAX_ROWS_VISIBLE,
        MIN_ROWS,
        height_for_rows,
        quantize,
        rows_for_height,
    )

    # Every height in the console's whole range snaps onto a row boundary.
    for px in range(MIN_HEIGHT - 40, MAX_HEIGHT + 40):
        snapped = quantize(px)
        assert snapped == height_for_rows(rows_for_height(snapped)), px
        assert MIN_HEIGHT <= snapped <= MAX_HEIGHT, px

    # And the two functions are genuine inverses across the range.
    for rows in range(MIN_ROWS, MAX_ROWS_VISIBLE + 1):
        assert rows_for_height(height_for_rows(rows)) == rows


def test_ps229_the_console_opens_at_exactly_six_rows():
    """His number, and the reason the default is derived rather than typed.

    The render measured a 186px band on a 980px window. That is EVIDENCE FROM
    ONE MACHINE, not the spec — the spec is "six whole rows at the shipped row
    metrics", so this asserts the ROW COUNT and lets the pixel figure fall out
    of the row height. A future ROW_HEIGHT change must move the pixels and
    keep the six.
    """
    d = LogDock(window_height=980)
    assert d.rows == 6
    assert d.height == height_for_rows(6)
    assert OPEN_HEIGHT == height_for_rows(6)


def test_ps229_a_drag_accumulates_so_growing_works_as_well_as_shrinking():
    """THE BUG: snapping each frame made the gesture one-directional.

    A drag arrives as ~10px frames and a row is 22px, so no single frame is a
    whole row. Quantizing per frame FLOORS, so growing by 10px landed back
    inside the row it started in and the console never grew however far he
    dragged, while shrinking crossed a boundary immediately and worked. Up was
    dead, down worked — which reads as the grip being broken again.
    """
    d = LogDock(window_height=1400)
    start = d.rows

    # Ten upward frames, none of them a whole row on its own.
    for _ in range(10):
        d.drag_by(-10.0)
    assert d.rows > start, "the console did not grow under an accumulated drag"

    grown = d.rows
    for _ in range(10):
        d.drag_by(+10.0)
    assert d.rows < grown, "the console did not shrink back"


def test_ps229_the_grip_is_bounded_by_the_rails_budget_not_only_by_max_height():
    """THE KNOWN-OPEN ITEM, and the owner's decision on it.

    Two paths changed the dock's height and only ONE respected the rail:

        window resize -> apply_window_height() -> affordable_height()  OK
        pointer drag  -> drag_by()             -> MAX_HEIGHT only      NOT

    Measured at the app minimum (680px): the grip would drag the dock to 494px,
    leaving 186px for a fixed cluster that needs 560px — starving it by 374px,
    after which any window event silently healed it. That asymmetry is the
    whole of "the ACTIVITY panel wrecks the layout generally", and it is also
    why elements animate inside a rail whose height is being rewritten
    underneath them mid-gesture.

    The owner was asked rather than second-guessed: "боковая панель важнее —
    ограничить лог". The rail wins. So the requirement is "continuously, from
    1 row up TO WHATEVER THE WINDOW AFFORDS", and the ~3-row cap at the app
    minimum is deliberate, not a defect to work around.
    """
    from src.ui.components.log_dock import affordable_height

    # At the app's own minimum window, drag as hard as the operator can.
    d = LogDock(window_height=680)
    for _ in range(400):
        d.drag_by(-10.0)

    assert d.height <= affordable_height(680)
    assert 680 - d.height >= RAIL_CONTENT_HEIGHT, "the drag starved the rail"
    # The documented consequence of the owner's call, stated as a number so a
    # future change that quietly raises it fails here.
    assert d.rows == 3

    # A roomy window still gives him the full range — the cap is the WINDOW's,
    # not a new global ceiling.
    tall = LogDock(window_height=1400)
    for _ in range(400):
        tall.drag_by(-10.0)
    assert tall.height == MAX_HEIGHT
    assert tall.rows == 20


def test_ps229_the_grip_follows_the_window_it_is_budgeted_against():
    """A stale budget is the same defect one resize later.

    drag_by has no event carrying a window height, so it consults the last one
    the dock was told about. If a resize did not refresh that, an operator who
    shrank the window and then dragged would starve exactly the rail this
    budget protects.
    """
    d = LogDock(window_height=1400)
    for _ in range(400):
        d.drag_by(-10.0)
    assert d.rows == 20

    # The window shrinks to the app minimum, then he drags again.
    d.apply_window_height(680)
    for _ in range(400):
        d.drag_by(-10.0)
    assert 680 - d.height >= RAIL_CONTENT_HEIGHT, "the drag used a stale budget"
    assert d.rows == 3


def test_ps229_a_console_whose_window_is_unknown_is_not_clamped_to_the_floor():
    """A headless or served session reports no height. Unknown constrains
    nothing — it must not silently collapse the console."""
    d = LogDock(window_height=None)
    for _ in range(400):
        d.drag_by(-10.0)
    assert d.height == MAX_HEIGHT


def test_ps229_the_one_line_state_is_a_real_log_that_keeps_reporting():
    """The floor is one ROW of the live console, not a separate fixed strip.

    The distinction matters: a collapsed strip is a different control with a
    different reader. At one row he still has the log, and events still land in
    it.
    """
    from src.ui.components.log_dock import MIN_ROWS

    d, lines = _dock(), []
    d.set_rows(MIN_ROWS)
    assert d.rows == 1
    assert d.height == height_for_rows(1)
    assert d.state.collapsed is False, "one row is the LOG, not the collapsed strip"
    assert d.body.visible is True

    _feed(d, lines, "shop-de-03: still arriving")
    assert d.row_count == 1
    assert "still arriving" in d._peek.value


def test_ps229_the_size_readout_is_in_his_unit_and_is_right_from_the_first_frame():
    """"8 rows" is checkable against what he can see; "289px" is not."""
    d = LogDock(window_height=980)
    assert d._size_label.value == "6 rows"
    d.set_rows(1)
    assert d._size_label.value == "1 row", "singular, or the readout reads as a typo"
    d.set_rows(12)
    assert d._size_label.value == "12 rows"


def test_ps229_every_cell_in_the_row_shares_one_type_size():
    """THE MISALIGNMENT MARS REPORTED, fixed by construction.

    The row shipped with THREE type sizes — profile 11, message 11.5,
    timestamp 10 — each centred INSIDE ITS OWN CELL. A text box's height comes
    from the font's ascent+descent at its own size, so three sizes give three
    box heights, centred independently, and the glyphs land on three different
    baselines.

    It is invisible on Linux and visible on Windows because the error is
    font-metric: "monospace" resolves to DejaVu Sans Mono here and Consolas
    there, so per-size rounding that cancels on one lands off-by-one on the
    other. That is precisely why this is asserted as ONE SIZE rather than as a
    pixel offset measured on this box — a padding tuned where the bug does not
    reproduce would re-break where it does.

    Hierarchy is carried by COLOUR and WEIGHT instead, neither of which costs a
    vertical metric.
    """
    from src.ui.log_console import TEXT_SIZE

    row = event_row("10:00:02  > shop-de-03: LAUNCH_FAILED: boom", ROSTER)
    _dot, profile, message, time_col = row.content.controls

    sizes = {col.content.size for col in (profile, message, time_col)}
    assert sizes == {TEXT_SIZE}, f"three box heights again: {sizes}"

    # The hierarchy that replaced the size difference is still doing its job:
    # the columns are not all one colour.
    colours = {col.content.color for col in (profile, message, time_col)}
    assert len(colours) > 1, "size was removed without colour taking over"


def test_ps229_the_severity_dot_is_centred_by_the_row_not_by_the_font():
    """A 7px child and a ~15px text box under CrossAxisAlignment.CENTER round
    their offsets independently — the same per-cell rounding that staggered the
    text. Giving the dot a full-height box makes its centring a property of the
    ROW rather than of the font."""
    row = event_row("10:00:01  > Launching shop-de-03", ROSTER)
    dot_col = row.content.controls[0]
    assert dot_col.height == ROW_HEIGHT
    assert row.content.vertical_alignment == ft.CrossAxisAlignment.STRETCH
