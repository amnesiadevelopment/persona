"""The Activity Log console: row layout, the profile parse, and stream state.

Three things live here, one per property the owner asked the row to have.

**The row.** A log line is stored as ``HH:MM:SS  > message`` — a single run of
text, which is why a wide row read as "mostly empty space with the text lost in
it": nothing in it was aligned to anything, so the eye had no column to run
down. :func:`event_row` splits it into real columns — severity, profile,
message, time — so a stack of rows forms vertical rulers and scanning becomes a
straight line down one column instead of a re-read of every row.

**The parse.** The profile name is not a field; it is buried in prose, in four
different shapes ("Launching X", "X: ...", "... for X", "Session ended: X").
:func:`parse_event` recovers it against the profiles that actually exist, so
the profile column is real data rather than a guess at a prefix.

**The stream.** :class:`StreamState` holds the follow/pause decision and the
count of what arrived while paused. The behaviour it exists to support: follow
the tail while the operator is at the bottom, stop dead the moment he scrolls
up to read, and offer an explicit way back.

UNIFORM HEIGHT IS A FEATURE, NOT AN OVERSIGHT. Every row is exactly
:data:`ROW_HEIGHT` tall and no column wraps. The console is watched while
profiles launch, and a wrapping row changes the region's extent on every
repaint — that jitter is a real part of what the owner called uncomfortable to
read. A long message is therefore ellipsised rather than reflowed; the full
line is one click away in the fullscreen Activity Log, where a cut message
carries a reveal that wraps it over up to five lines and is selectable so it
can be copied (``dialogs/log.py`` — :func:`~src.ui.dialogs.log.fullscreen_event_row`).

That escape hatch is a LIVE CLAIM, and it has been false once already: between
PS-229 and PS-266 the fullscreen view rendered through this very ``event_row``,
so the click led to the identical truncation. If the fullscreen message cell
ever loses its reveal, this sentence is what has to change with it — the
justification for truncating here rests on it.
"""

from __future__ import annotations

import re

import flet as ft

from .theme import COLORS

#: Severity vocabulary. Deliberately four values, not a colour per message:
#: the dot exists so FAILURE is findable in a peripheral glance, and a palette
#: with a dozen entries cannot do that.
SEV_FAIL = "fail"
SEV_OK = "ok"
SEV_INFO = "info"
SEV_IDLE = "idle"

SEV_COLOR = {
    SEV_FAIL: COLORS["error"],
    SEV_OK: COLORS["success"],
    SEV_INFO: "#5BC8FF",
    SEV_IDLE: COLORS["text_dim"],
}

MONO = "monospace"

#: One row, always. See the module docstring — this is what stops the region
#: from changing extent under a reader while events land.
ROW_HEIGHT = 22

#: ONE type size for every column in the row, and the fix for the misalignment
#: Mars reported on 3.0.1.
#:
#: THE DEFECT. The row shipped with THREE different sizes — profile 11,
#: message 11.5, timestamp 10 — each laid out by ``CrossAxisAlignment.CENTER``.
#: Centring is per-CELL: every cell gets its own text box, and a box's height
#: comes from the font's ascent+descent at ITS size. Three sizes therefore
#: produce three different box heights, each centred independently, so the
#: glyphs inside them land on three different baselines. The rendered text sits
#: a pixel or two out of line across the row, which is exactly "текст
#: съехавший".
#:
#: It is invisible on the Linux capture box and visible on his Windows machine
#: because the effect is a FONT-METRIC one: "monospace" resolves to DejaVu Sans
#: Mono here and Consolas there, whose ascent/descent ratios differ, so the
#: per-size rounding that cancels out on one lands off-by-one on the other.
#: That is why this is fixed by construction rather than nudged with padding —
#: a padding tuned on this box would re-break on his.
#:
#: ONE size across the row makes all three boxes identical, so the baselines
#: coincide on ANY font. Hierarchy is carried by COLOUR and WEIGHT instead,
#: which cost no vertical metric.
TEXT_SIZE = 11.5

#: The profile ruler. Wide enough for the names this product generates
#: (``shop-de-03``, ``mail-us-011``) and fixed, because a column that resizes
#: to its content is not a ruler.
PROFILE_COL_WIDTH = 132

#: Timestamps are fixed-width CONTENT (always 8 chars), so pinning this column
#: cannot truncate anything — the one place a fixed width is safe.
TIME_COL_WIDTH = 62

#: The neutral stand-in for an event whose profile cannot be resolved. A
#: placeholder, deliberately not a guess: a wrong name in a scanning column is
#: worse than an admitted blank.
NO_PROFILE = "—"


def severity(message: str) -> str:
    """Classify one event message.

    Kept alongside (not merged into) ``log_format.log_message_color``: that one
    answers "what colour is this text", this one answers "what KIND of event is
    this", which the dot and the collapsed strip's pulse both need as a value
    rather than as a hex string.
    """
    low = message.lower()
    if (
        "fail" in low
        or "error" in low
        or "refused" in low
        or "missing" in low
        or "LAUNCH_FAILED" in message
        or low.startswith("session ended")
    ):
        return SEV_FAIL
    if (
        "started" in low
        or "installed" in low
        or "imported" in low
        or "exported" in low
        or "ready" in low
        or "reached" in low
        or "updated to" in low
        or "synced" in low
        or "frozen" in low
    ):
        return SEV_OK
    if (
        "available" in low
        or "downloading" in low
        or "update" in low
        or "launching" in low
        # A start-up purge DESTROYED key material. It is not a failure — the
        # retention floor working exactly as designed — but it is not idle
        # housekeeping either, and SEV_IDLE gave a permanent destruction the
        # dimmest dot in the console. "purged" is deliberately the whole token:
        # it is the only word in the app's messages that carries it (the two
        # purge lines in trash/service.py and main.py), so nothing unrelated is
        # reclassified by adding it.
        or "purged" in low
    ):
        return SEV_INFO
    return SEV_IDLE


def parse_event(line: str, profiles: frozenset[str] | set[str]) -> tuple:
    """Split a stored log line into ``(time, profile, message, severity)``.

    ``profiles`` is the set of names that really exist, which is what makes
    this a parse rather than a guess: "Loaded 6 bookmarks, 0 pools for
    shop-us-01" has no delimiter that marks the name, and a prefix heuristic
    would read "Session ended" as a profile on the line right below it.

    An event whose profile cannot be resolved against the roster returns an
    empty profile, which the row renders as :data:`NO_PROFILE`.
    """
    stamp, sep, rest = line.partition("  > ")
    if not sep:
        stamp, rest = "", line
    stamp = stamp.strip()
    rest = rest.strip()

    profile = ""

    # Shape 1: "<name>: the rest of the message"
    head, colon, tail = rest.partition(": ")
    if colon and head in profiles:
        profile, rest = head, tail.strip()
    else:
        # Shapes 2-4, longest name first so 'shop-us-1' cannot shadow
        # 'shop-us-11'.
        for name in sorted(profiles, key=len, reverse=True):
            if not name or name not in rest:
                continue
            pattern = re.escape(name)
            if re.search(rf"\bfor {pattern}\b", rest):
                profile = name
                rest = re.sub(rf"\s*\bfor {pattern}\b", "", rest).strip()
                break
            if re.search(rf"^Launching {pattern}$", rest):
                profile, rest = name, "Launching"
                break
            if re.search(rf"^Session ended: {pattern}$", rest):
                profile, rest = name, "Session ended"
                break
            if re.search(rf"\b{pattern}\b", rest):
                profile = name
                rest = re.sub(rf"\s*\b{pattern}\b\s*", " ", rest).strip()
                rest = rest.strip(":,-  ") or "event"
                break

    return stamp, profile, rest, severity(rest)


def _dot(sev: str) -> ft.Container:
    """The severity mark: 7px, vertically centred in a fixed-height row.

    First thing in the row and ahead of all text, so a failure is findable in a
    peripheral glance without reading a word.
    """
    return ft.Container(
        width=7,
        height=7,
        border_radius=4,
        bgcolor=SEV_COLOR[sev],
    )


def _cell(text: str, **kw) -> ft.Text:
    """One column's text: single line, ellipsised, never wrapped.

    Every caller passes through here so no column can accidentally reflow and
    make its row taller than its neighbours.

    SIZE IS NOT A PARAMETER. Every cell in the row is :data:`TEXT_SIZE`, which
    is what puts the columns on a common baseline on every font — see the
    constant. Callers vary colour and weight instead, neither of which changes
    a text box's height.
    """
    kw.pop("size", None)
    return ft.Text(
        text,
        size=TEXT_SIZE,
        font_family=MONO,
        no_wrap=True,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
        **kw,
    )


def event_row(line: str, profiles: frozenset[str] | set[str]) -> ft.Control:
    """One event, as aligned columns of fixed height.

    ``severity | PROFILE | message | time`` — the profile column is a fixed
    left ruler, so names stack vertically and "everything about shop-de-03" is
    one glance down a column rather than eight sentences to re-read. The
    timestamp is right-aligned in its own column at the far edge, where it is
    available without competing with the message for the eye.
    """
    stamp, profile, message, sev = parse_event(line, profiles)

    # Every cell is TEXT_SIZE (enforced in _cell), so the three text boxes are
    # the same height and their glyphs share a baseline on any font. Hierarchy
    # is carried by colour and weight, which cost no vertical metric.
    time_col = ft.Container(
        width=TIME_COL_WIDTH,
        alignment=ft.Alignment.CENTER_RIGHT,
        content=_cell(stamp, color=COLORS["text_sub"]),
    )
    profile_col = ft.Container(
        width=PROFILE_COL_WIDTH,
        content=_cell(
            profile or NO_PROFILE,
            color=COLORS["accent"] if profile else COLORS["text_sub"],
        ),
    )
    message_col = ft.Container(
        expand=True,
        content=_cell(
            message,
            color=(
                COLORS["error"]
                if sev == SEV_FAIL
                else COLORS["text_main"]
                if sev == SEV_OK
                else COLORS["text_dim"]
            ),
        ),
    )

    # The dot is centred inside a box of the ROW's own height rather than being
    # centred by the Row. A 7px child and a ~15px text box under
    # CrossAxisAlignment.CENTER round their offsets independently, which is the
    # same per-cell rounding that staggered the text; giving the dot a
    # full-height box makes its centring a property of the row, not of the
    # font.
    dot_col = ft.Container(
        width=7,
        height=ROW_HEIGHT,
        alignment=ft.Alignment.CENTER,
        content=_dot(sev),
    )

    return ft.Container(
        height=ROW_HEIGHT,
        padding=ft.Padding.symmetric(horizontal=14, vertical=0),
        content=ft.Row(
            spacing=12,
            # STRETCH, not CENTER: every child is a full-height box that
            # centres its own content, so no cell computes a vertical offset
            # from its own font metrics. That is what keeps the columns on one
            # line across fonts — see TEXT_SIZE.
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[dot_col, profile_col, message_col, time_col],
        ),
    )


class StreamState:
    """Follow-the-tail state for one console.

    The rule the owner asked for, stated once: **follow while he is at the
    bottom, stop the instant he scrolls up, and count what he missed** so the
    way back can say how much is waiting. ``following`` is the whole
    behaviour; ``missed`` is what makes the return trip informative rather
    than a bare arrow.
    """

    #: How far from the bottom (px) still counts as "at the bottom". A few
    #: pixels of slack, because a wheel notch that lands 3px short is not a
    #: decision to stop following.
    BOTTOM_SLACK = 24.0

    def __init__(self) -> None:
        self.following: bool = True
        self.missed: int = 0
        self.total: int = 0
        self.collapsed: bool = False
        self.last_line: str = ""

    def on_scroll(self, pixels: float, max_extent: float) -> bool:
        """Fold a scroll position into the follow decision.

        Returns True when the decision CHANGED, so the caller repaints only
        on a real transition rather than on every scroll frame.
        """
        at_bottom = pixels >= (max_extent - self.BOTTOM_SLACK)
        if at_bottom and not self.following:
            self.following = True
            self.missed = 0
            return True
        if not at_bottom and self.following:
            self.following = False
            return True
        return False

    def resume(self) -> None:
        self.following = True
        self.missed = 0
