"""PS-1 round 2 — the Activity Log console: row layout and stream state.

Shared by all three round-2 directions, so the three differ in PLACEMENT and
SIZING (what the owner asked to compare) rather than in row design, which he
asked to be fixed the same way everywhere.

Three things live here, one per "must have" in the round-1 feedback:

**The row (must-have 2).** A log line is stored as ``HH:MM:SS  > message`` — a
single run of text, which is why a wide row read as "mostly empty space with
the text lost in it": nothing in it was aligned to anything, so the eye had no
column to run down. :func:`event_row` splits it into real columns — severity,
profile, message, time — so a stack of rows forms vertical rulers and scanning
becomes a straight line down one column instead of a re-read of every row.

**The parse.** The profile name is not a field; it is buried in prose, in four
different shapes ("Launching X", "X: ...", "... for X", "Session ended: X").
:func:`parse_event` recovers it against the profiles that actually exist, so
the profile column is real data rather than a guess at a prefix.

**The stream (must-have 3).** :class:`StreamState` holds the follow/pause
decision and the count of what arrived while paused. The behaviour it exists
to support: follow the tail while the operator is at the bottom, stop dead the
moment he scrolls up to read, and offer an explicit way back.

GROWTH: no row here clamps, ellipsises or fixes a height. The message column
wraps and the row grows taller. At dock width a real event (30-60 chars) sits
well inside one line, so rows are uniform in practice without being uniform by
force — the rare long line reflows instead of being silently swallowed.
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


def severity(message: str) -> str:
    """Classify one event message.

    Kept alongside (not merged into) ``log_format.log_message_color``: that one
    answers "what colour is this text", this one answers "what KIND of event is
    this", which the dot, the collapsed strip and the digest column all need as
    a value rather than as a hex string.
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
    """Split a stored log line into (time, profile, message, severity).

    ``profiles`` is the set of names that really exist, which is what makes
    this a parse rather than a guess: "Loaded 6 bookmarks, 0 pools for
    shop-us-01" has no delimiter that marks the name, and a prefix heuristic
    would read "Session ended" as a profile on the line right below it.
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
    """The severity mark. 7px and top-margined so it sits on the first text
    line even when the message wraps to a second."""
    return ft.Container(
        width=7,
        height=7,
        border_radius=4,
        bgcolor=SEV_COLOR[sev],
        margin=ft.Margin.only(top=5),
    )


def event_row(
    line: str,
    profiles: frozenset[str] | set[str],
    layout: str = "profile_first",
) -> ft.Control:
    """One event, as aligned columns.

    ``layout`` is the one knob the three directions turn, because the right
    answer depends on how much width the direction has and on what its owner
    is scanning FOR:

    ``profile_first``
        severity | PROFILE | message | time. The profile column is a fixed
        left ruler, so the names stack vertically and "everything about
        shop-de-03" is one glance down a column. For watching many profiles.

    ``status_first``
        severity | message | profile | time, with the message carrying the
        weight. For a narrower column that shares its row, where WHAT happened
        matters more than which profile it happened to.

    ``message_first``
        severity | message ... | profile · time as a dim trailing tail. For a
        reading surface, where the metadata should recede.
    """
    stamp, profile, message, sev = parse_event(line, profiles)

    time_text = ft.Text(
        stamp,
        size=10,
        color=COLORS["text_sub"],
        font_family=MONO,
        no_wrap=True,
    )
    # Timestamps are fixed-width CONTENT (always 8 chars), so pinning this
    # column cannot truncate anything — the one place a fixed width is safe.
    time_col = ft.Container(width=58, content=time_text)

    profile_text = ft.Text(
        profile or "—",
        size=11,
        color=COLORS["accent"] if profile else COLORS["text_sub"],
        font_family=MONO,
        selectable=True,
    )
    message_text = ft.Text(
        message,
        size=11.5,
        color=(
            COLORS["error"]
            if sev == SEV_FAIL
            else COLORS["text_main"]
            if sev == SEV_OK
            else COLORS["text_dim"]
        ),
        font_family=MONO,
        selectable=True,
    )

    if layout == "profile_first":
        controls = [
            _dot(sev),
            # Fixed so names form a ruler; the text WRAPS inside it, so a
            # longer name makes the row taller instead of being cut.
            ft.Container(width=132, content=profile_text),
            ft.Container(expand=True, content=message_text),
            time_col,
        ]
    elif layout == "status_first":
        controls = [
            _dot(sev),
            ft.Container(expand=True, content=message_text),
            ft.Container(width=118, content=profile_text),
            time_col,
        ]
    else:  # message_first
        controls = [
            _dot(sev),
            ft.Container(expand=True, content=message_text),
            ft.Container(
                content=ft.Text(
                    f"{profile or '—'}  ·  {stamp}",
                    size=10,
                    color=COLORS["text_sub"],
                    font_family=MONO,
                ),
            ),
        ]

    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=14, vertical=3),
        content=ft.Row(
            spacing=12,
            # TOP, not CENTER: a wrapped message must push the row down with
            # its columns still aligned to the first line.
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=controls,
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

    def on_events(self, count: int, last_line: str = "") -> None:
        self.total += count
        if last_line:
            self.last_line = last_line
        if not self.following or self.collapsed:
            self.missed += count

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
