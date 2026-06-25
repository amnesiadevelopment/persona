import flet as ft

from .theme import COLORS

_LOG_BLUE = "#5BC8FF"


def log_message_color(message: str) -> str:
    """Pick a colour for a log line from its content: failures red, completions
    green, version/engine updates blue, everything else dim."""
    low = message.lower()
    if (
        "fail" in low
        or "error" in low
        or "LAUNCH_FAILED" in message
        or low.startswith("session ended:")
    ):
        return COLORS["error"]
    if "available" in low or "update" in low or "downloading" in low:
        return _LOG_BLUE
    if (
        message == "Browser started!"
        or "started" in low
        or "installed" in low
        or "imported" in low
        or "exported" in low
        or "ready" in low
        or "updated to" in low
    ):
        return COLORS["success"]
    return COLORS["text_dim"]


def log_line_control(line: str, wrap: bool = True) -> ft.Text:
    """Build a terminal-style coloured row for one stored log line.

    Stored lines look like ``HH:MM:SS  > message``; the timestamp is rendered
    dim and the message is coloured by type. Lines without the expected shape
    fall back to a single dim run. With ``wrap=False`` the line stays on one
    row (used in the narrow sidebar, where wrapping made the panel overflow its
    fixed height and the list jittered); the fullscreen dialog wraps.
    """
    no_wrap = not wrap
    max_lines = 1 if no_wrap else None
    stamp, sep, rest = line.partition("  > ")
    if not sep:
        return ft.Text(
            line, size=11, color=COLORS["text_dim"], font_family="monospace",
            no_wrap=no_wrap, max_lines=max_lines,
            overflow=ft.TextOverflow.ELLIPSIS if no_wrap else None,
        )
    return ft.Text(
        spans=[
            ft.TextSpan(
                f"{stamp} ",
                ft.TextStyle(color=COLORS["text_sub"], size=11),
            ),
            ft.TextSpan(
                rest,
                ft.TextStyle(color=log_message_color(rest), size=11),
            ),
        ],
        font_family="monospace",
        size=11,
        no_wrap=no_wrap,
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS if no_wrap else None,
        selectable=True,
    )
