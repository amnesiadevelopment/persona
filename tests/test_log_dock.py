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

from src.ui.components.log_dock import (  # noqa: E402
    COLLAPSED_HEIGHT,
    MAX_HEIGHT,
    MAX_ROWS,
    MIN_HEIGHT,
    OPEN_HEIGHT,
    RAIL_CONTENT_HEIGHT,
    LogDock,
    default_height,
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
    d.set_height(300)
    assert d.height == 300


def test_ac2_and_ac3_collapse_reports_and_the_height_survives_the_round_trip():
    d, lines = _dock(), []
    for i in range(5):
        _feed(d, lines, f"event {i}")
    d.set_height(300)

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
    assert d.height == 300 and d.body.height == 300
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

    The operator's own 400px drag must not be permanently rewritten to the
    opening default just because he nudged the window smaller and back.
    """
    d = LogDock(window_height=1200)
    d.set_height(400)
    assert d.height == 400

    d.apply_window_height(680)
    assert 680 - d.height >= RAIL_CONTENT_HEIGHT, "the rail was starved on shrink"
    assert d.height < 400, "the console did not yield any height"

    d.apply_window_height(1200)
    assert d.height == 400, "his deliberate height was not handed back"


def test_ac8_an_unmeasurable_resize_leaves_the_console_alone():
    """A resize that cannot be measured must not collapse the console to the
    floor — a missing height is unknown, not zero."""
    d = LogDock(window_height=1200)
    d.set_height(300)
    d.apply_window_height(None)
    assert d.height == 300
    d.apply_window_height(0)
    assert d.height == 300
