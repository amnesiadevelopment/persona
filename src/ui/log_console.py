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
line is always one click away in the fullscreen Activity Log.
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


#: The three densities a row can be rendered at. WHICH ONE IS IN USE IS A
#: FUNCTION OF THE CONSOLE'S HEIGHT, which is what makes this direction
#: different from simply making the panel taller.
#:
#: The reasoning: a six-line tail and a twenty-line console are not the same
#: reading task. At one line he is GLANCING — he wants the newest thing that
#: happened and nothing competing with it. At six he is WATCHING — the profile
#: column earns its width because several machines are interleaved. At fifteen
#: he is READING — now the stream is long enough that "where does this run of
#: events start" is a real question, so the separators that answer it earn
#: their space too.
#:
#: A fixed row layout has to pick one of those and be wrong for the other two.
TIER_TICKER = "ticker"
TIER_STANDARD = "standard"
TIER_READING = "reading"

#: Row counts at which the layout changes up a tier. Chosen against the
#: default: 6 rows (his number) sits in STANDARD with room either side, so the
#: console he opens is the one he described and the other two tiers are things
#: he moves INTO deliberately.
TICKER_MAX_ROWS = 2
STANDARD_MAX_ROWS = 9


def tier_for_rows(rows: int) -> str:
    """Which row layout a console showing ``rows`` rows should use."""
    if rows <= TICKER_MAX_ROWS:
        return TIER_TICKER
    if rows <= STANDARD_MAX_ROWS:
        return TIER_STANDARD
    return TIER_READING


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


def event_row(
    line: str,
    profiles: frozenset[str] | set[str],
    tier: str = TIER_STANDARD,
    show_profile: bool = True,
) -> ft.Control:
    """One event, at the density this console height calls for.

    ``severity | PROFILE | message | time`` is the STANDARD row — the profile
    column is a fixed left ruler, so names stack vertically and "everything
    about shop-de-03" is one glance down a column rather than eight sentences
    to re-read. The timestamp is right-aligned in its own column at the far
    edge, where it is available without competing with the message for the eye.

    THE TIER IS THE DIRECTION'S WHOLE ARGUMENT. The same six columns are wrong
    at one line and wrong again at twenty, for opposite reasons, so the row is
    not one layout stretched — it is three:

    * :data:`TIER_TICKER` (1-2 rows) — he is GLANCING. The profile column and
      the timestamp are dropped: at one line the newest event is the only
      thing on screen, and a 132px ruler with one name in it is 132px of
      nothing. Severity and message, full width.
    * :data:`TIER_STANDARD` (3-9 rows) — he is WATCHING several machines at
      once, so the profile ruler earns its width and the timestamp returns.
      This is the tier the default 6-row console opens in.
    * :data:`TIER_READING` (10+ rows) — he is READING a run of history. The
      timestamp gains its seconds-level prominence back (it is no longer the
      dimmest thing in the row) because at this length "when did this start"
      is a question he is actually asking.

    Every tier keeps ONE row height and ONE text size, so switching tiers
    cannot re-introduce the baseline stagger this module fixes — see
    :data:`TEXT_SIZE`. What changes is WHICH columns are present, never how
    tall they are.
    """
    stamp, profile, message, sev = parse_event(line, profiles)

    # Every cell is TEXT_SIZE (enforced in _cell), so the text boxes are the
    # same height and their glyphs share a baseline on any font. Hierarchy is
    # carried by colour and weight, which cost no vertical metric.
    time_col = ft.Container(
        width=TIME_COL_WIDTH,
        alignment=ft.Alignment.CENTER_RIGHT,
        content=_cell(
            stamp,
            # In READING the timestamp stops being the dimmest thing in the
            # row: at this length he is locating a run in time, not glancing.
            color=(
                COLORS["text_main"] if tier == TIER_READING else COLORS["text_sub"]
            ),
        ),
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

    # WHICH COLUMNS EXIST is the tier's whole effect. At TICKER the profile
    # ruler and the timestamp are dropped rather than shrunk — a 132px column
    # holding one name, on the only line on screen, is 132px that the message
    # should have. In TICKER the profile is folded into the message instead, so
    # nothing is actually lost from the line.
    if tier == TIER_TICKER:
        if profile:
            message = f"{profile}  {message}"
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
        columns = [dot_col, message_col]
    elif not show_profile:
        # READING, under a group separator that already names the profile. The
        # column is kept as EMPTY SPACE rather than removed: dropping it would
        # shift the message left and break the vertical ruler that every other
        # row in the console lines up on, so a run of grouped events would read
        # as a different layout instead of as the same one with its heading
        # factored out. Blank, aligned, and not repeating what the rule above
        # it already says.
        columns = [
            dot_col,
            ft.Container(width=PROFILE_COL_WIDTH),
            message_col,
            time_col,
        ]
    else:
        columns = [dot_col, profile_col, message_col, time_col]

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
            controls=columns,
        ),
    )


def group_separator(profile: str, stamp: str) -> ft.Control:
    """The rule that opens a run of events belonging to one profile.

    Only ever rendered in :data:`TIER_READING`, and that restriction is the
    point: a separator every few rows is noise in a six-line tail and structure
    in a twenty-line one. At reading length the console stops being a ticker
    and becomes a document, and a document needs to say where its sections
    start.

    Deliberately NOT a box. The owner's objection to the fullscreen view was
    "без этих дебильных рамок", and the same judgement applies here — the
    separator is a name, a time and the space around them, with no border
    anywhere.
    """
    return ft.Container(
        height=ROW_HEIGHT,
        padding=ft.Padding.only(left=14, right=14, top=4),
        content=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(width=7),
                _cell(
                    profile or NO_PROFILE,
                    color=COLORS["accent"],
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Container(expand=True, height=1, bgcolor=COLORS["border"]),
                _cell(stamp, color=COLORS["text_sub"]),
            ],
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
