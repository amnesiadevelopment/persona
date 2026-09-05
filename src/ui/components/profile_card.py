import time
from collections.abc import Callable

import flet as ft

from ...core.strings import CHROMIUM_ENGINE_NAME
from ...models.profile import Profile
from ...models.proxy import Proxy
from ...services.browser.refusal import Refusal
from ...services.proxy.freshness import PROXY_STALE_AFTER_S, proxy_indicator_state
from ...utils.timefmt import humanize_since
from ..flags import flag_path
from ..theme.colors import COLORS
from ..theme.styles import MONO, row_button
from .launch_button import build_launch_button

_OS_LABELS = {"windows": "windows", "macos": "macos", "linux": "linux"}

# The proxy/direct indicator and a flag share this footprint so the row never
# shifts when a flag replaces the placeholder.
_IND_W = 30
_IND_H = 20

# PROXY_STALE_AFTER_S and proxy_indicator_state are imported above from
# services.proxy.freshness, where they now live as ONE authority: the same
# question ("how much do we still believe this proxy's recorded geography?")
# governs both the glyph drawn here and whether a profile may LAUNCH declaring
# that geography, and src/services/ cannot import from src/ui/. They are
# re-exported by name because this module is where both were first published —
# callers and tests that import them from here keep working unchanged.
#
# PROXY_STALE_AFTER_S has no other use in this module: it is imported PURELY as
# that re-export (tests/test_profile_card.py:10 binds it by name from here), so
# __all__ states the intent rather than leaving it looking like a stray import.
# Same convention this repo already uses in src/services/verify/ and
# src/api/routes/__init__.py.
__all__ = [
    "PROXY_STALE_AFTER_S",
    "proxy_indicator_state",
    "build_profile_card",
]


def _tag_chips(tags: list[str]) -> ft.Control:
    if not tags:
        return ft.Container(width=0)
    return ft.Row(
        spacing=6,
        wrap=True,
        controls=[
            ft.Container(
                border_radius=3,
                border=ft.Border.all(1, COLORS["card_border"]),
                bgcolor=COLORS["bg"],
                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                content=ft.Text(
                    tag, size=10, color=COLORS["accent"], font_family=MONO
                ),
            )
            for tag in tags
        ],
    )


def _indicator_box(content: ft.Control, border: bool = True) -> ft.Container:
    return ft.Container(
        width=_IND_W,
        height=_IND_H,
        border_radius=2,
        border=ft.Border.all(1, COLORS["card_border"]) if border else None,
        alignment=ft.Alignment(0, 0),
        content=content,
    )


def _proxy_age_label(proxy: Proxy, state: str, now: float) -> str:
    """Human 'when was this last checked' phrase for the card's meta line.

    Deliberately NOT used in the indicator's tooltip: that string is an
    exact-equality contract in test_indicator_click_checks_proxy, so the age
    rides the meta line instead of being concatenated into it.
    """
    if state == "failed":
        if proxy.checked_at:
            return f"check failed {humanize_since(proxy.checked_at, now)}"
        return "check failed"
    if state == "unverified":
        return "not checked yet"
    return f"checked {humanize_since(proxy.checked_at, now)}"


def _proxy_indicator(
    proxy: Proxy | None,
    on_check_proxy: Callable[[str], None] | None,
    is_checking: bool,
    now: float,
) -> ft.Control:
    """Left-of-name indicator that doubles as the proxy check button.

    - no proxy        -> a 'direct' box (not clickable)
    - checking        -> a spinner
    - checked ok      -> the country flag (click to re-check)
    - stale           -> the country code, dimmed, NOT a flag (click to re-check)
    - check failed    -> an ✕ (click to re-check)
    - not checked yet -> a dot placeholder (click to check)

    The flag is deliberately not drawn once the check is older than
    PROXY_STALE_AFTER_S: the operator launches from this row, and a rotating
    exit moves underneath a stored country code with no event to tell us. The
    age itself is carried on the card's meta line (see build_profile_card), so
    the indicator never asserts a country without its provenance sitting beside
    it. Reading a timestamp is the whole mechanism — nothing here re-checks.
    """
    if proxy is None:
        return _indicator_box(
            ft.Icon(ft.Icons.HOME_OUTLINED, size=15, color=COLORS["text_dim"]),
            border=False,
        )

    state = proxy_indicator_state(proxy, now)

    if is_checking:
        inner: ft.Control = _indicator_box(
            ft.ProgressRing(
                width=12, height=12, stroke_width=2, color=COLORS["accent"]
            ),
            border=False,
        )
    elif state == "failed":
        inner = _indicator_box(
            ft.Text("✕", size=14, color=COLORS["error"], font_family=MONO)
        )
    else:
        path = (
            flag_path(proxy.country_code)
            if state == "verified" and proxy.country_code
            else None
        )
        if path:
            inner = ft.Image(src=path, width=_IND_W, height=_IND_H, border_radius=2)
        elif state == "stale" and proxy.country_code:
            # Distinct from the flag on purpose: the country is what we last
            # saw, not what we know now, so it is reported as text-with-an-age
            # rather than drawn as the confident graphic.
            inner = _indicator_box(
                ft.Text(
                    proxy.country_code.strip().lower(),
                    size=11,
                    color=COLORS["text_dim"],
                    font_family=MONO,
                    italic=True,
                )
            )
        else:
            # has a proxy but no successful check yet (or a stale check with no
            # country on record)
            inner = _indicator_box(
                ft.Text("·", size=14, color=COLORS["text_dim"], font_family=MONO)
            )

    if on_check_proxy is None or is_checking:
        return inner
    # Clickability is never gated on freshness: a stale or failed indicator is
    # exactly the one the operator most needs to be able to re-check.
    return ft.Container(
        content=inner,
        on_click=lambda _, n=proxy.name: on_check_proxy(n),
        ink=True,
        # The action label is a stable, asserted contract; the age rides the
        # meta line instead of being concatenated in here.
        tooltip="Check this profile's proxy",
    )


def _notes_field(profile, on_notes_change):
    """Inline, editable notes, vertically centred in the row and dim so it
    doesn't draw the eye. Saved on blur or Enter — no dialog needed. A fixed
    width keeps the notes aligned in a column across every card."""
    field = ft.TextField(
        value=getattr(profile, "notes", ""),
        hint_text="notes…",
        text_size=12,
        text_align=ft.TextAlign.CENTER,
        text_style=ft.TextStyle(
            font_family=MONO, italic=True, color=COLORS["text_dim"]
        ),
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        color=COLORS["text_dim"],
        border=ft.InputBorder.NONE,
        content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        multiline=False,
        on_blur=(
            (lambda e, n=profile.name: on_notes_change(n, e.control.value or ""))
            if on_notes_change
            else None
        ),
        on_submit=(
            (lambda e, n=profile.name: on_notes_change(n, e.control.value or ""))
            if on_notes_change
            else None
        ),
    )
    # Fixed width, centred by the overlay row, so notes line up down the
    # middle of every card regardless of name/button widths.
    return ft.Container(width=260, content=field)


def build_profile_card(
    profile: Profile,
    is_loading: bool,
    is_running: bool,
    on_launch: Callable[[str], None],
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    is_selected: bool = False,
    on_select: Callable[[str], None] | None = None,
    proxy: Proxy | None = None,
    on_check_proxy: Callable[[str], None] | None = None,
    proxy_checking: bool = False,
    on_notes_change: Callable[[str, str], None] | None = None,
    cdp_channel_open: bool = False,
    refusal: Refusal | None = None,
    cert_unresolved: bool = False,
) -> ft.Container:
    """Build a single profile row as a terminal-style line."""
    launch_btn = build_launch_button(
        profile.name,
        is_loading,
        is_running,
        on_launch,
        engine=getattr(profile, "engine", "chromium"),
    )
    action_buttons = _build_action_buttons(
        profile.name, on_edit, on_delete, is_running=is_running
    )
    select_box = _build_select_box(profile.name, is_selected, on_select)
    # Obtained here, exactly as the network page does at build time, so the
    # caller's signature is untouched and no re-check is implied by a redraw.
    now = time.time()
    indicator = _proxy_indicator(proxy, on_check_proxy, proxy_checking, now)

    if is_running:
        border_color = COLORS["accent"]
    elif is_selected:
        border_color = COLORS["text_sub"]
    else:
        border_color = COLORS["card_border"]

    os_label = _OS_LABELS.get(profile.os_type, profile.os_type)
    proxy_label = profile.proxy if profile.proxy else "direct"
    # A running profile is already shown by the accent border and the stop
    # button; a "· running" suffix here would be redundant.
    meta = f"{os_label} · {proxy_label}"
    if proxy is not None:
        # The age rides the meta line, so the operator reads it while
        # scanning. Same phrasing as the network page — one vocabulary for
        # one fact.
        meta += (
            f" · {_proxy_age_label(proxy, proxy_indicator_state(proxy, now), now)}"
        )

    left_block = ft.Row(
        spacing=14,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            select_box,
            indicator,
            ft.Column(
                spacing=3,
                alignment=ft.MainAxisAlignment.CENTER,
                controls=[
                    ft.Text(
                        profile.name,
                        size=15,
                        weight=ft.FontWeight.BOLD,
                        color=COLORS["text_main"],
                        font_family=MONO,
                    ),
                    *([_tag_chips(profile.tags)] if profile.tags else []),
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text(
                                meta,
                                size=11,
                                color=(
                                    COLORS["accent"] if is_running
                                    else COLORS["text_sub"]
                                ),
                                font_family=MONO,
                            ),
                            # Gated on is_running as well as on the captured
                            # fact: a stopped profile renders exactly as it did
                            # before this indicator existed. The two conditions
                            # are independent — the launcher drops the captured
                            # fact at every teardown path, and this is the
                            # render-side belt to that braces.
                            *(
                                _automation_channel_chip(cdp_channel_open)
                                if is_running
                                else []
                            ),
                            # Spliced into the meta row that ALREADY EXISTS
                            # rather than given a row of its own: that is what
                            # keeps a bulk launch of twenty refusals to twenty
                            # short lines in a column the operator is already
                            # scanning, with no card growing taller.
                            #
                            # Gated on NOT running as a render-side belt. The
                            # launcher already drops the verdict when a new
                            # attempt starts, and a refusal badge over a live
                            # browser would be the exact stale dishonesty this
                            # design avoids.
                            #
                            # NOT a second independent guard, and the comment
                            # said so before: `is_running` returns True while a
                            # name sits in `_starting`, and the launcher's pop
                            # happens in the SAME critical section that adds it
                            # there — so this condition is strictly implied by
                            # the launcher's, never true when the launcher's is
                            # false. It is defence in depth (cheap, and it keeps
                            # the render honest if that ordering ever moves),
                            # not a condition covering a case the launcher
                            # leaves open. Read it as a belt, not as braces.
                            *(
                                _refusal_chip(refusal, now)
                                if not is_running
                                else []
                            ),
                            # Same splice, same row, same list-returning form
                            # as the two above — see _unresolved_cert_chip for
                            # why this state needs a marker at all. NOT gated
                            # on is_running: unlike a refusal (a fact about one
                            # past attempt) this is a standing property of the
                            # profile's configuration, and it is just as true —
                            # and just as worth saying — while the browser this
                            # certificate was meant to authenticate is open.
                            *_unresolved_cert_chip(
                                cert_unresolved, profile.certificate
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    right_block = ft.Row(
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[launch_btn, *action_buttons],
    )

    # The name block hugs the left, the action buttons hug the right, and the
    # notes sit centred in the expanding middle. Notes live IN the row (not a
    # Stack overlay) so their container can't blanket the card and swallow clicks
    # meant for the launch/edit/delete buttons. The middle container expands to
    # absorb the slack and centres the fixed-width notes field within it, so the
    # row always fits the card width and the buttons never run off the edge.
    notes_middle = ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        content=_notes_field(profile, on_notes_change),
    )
    row = ft.Row(
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[left_block, notes_middle, right_block],
    )

    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, border_color),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=18, vertical=14),
        content=row,
    )


def _refusal_chip(refusal, now: float) -> list[ft.Control]:
    """The 'this profile's launch was REFUSED' marker, or nothing at all.

    Returned as a list so the caller splices it in — a profile with no refusal
    on record renders EXACTLY as it did before this marker existed, no empty
    box and no spacing change. The marker's PRESENCE is the signal, so an
    absent marker must be a real absence rather than a dimmed variant the eye
    learns to skip.

    WHY THIS EXISTS. A refusal used to reach the operator only as
    ``log_callback(f"Error starting process: {e}")`` — one line in a shared
    panel that scrolls, with nothing landing on the profile. A card that
    refused was pixel-identical to one that was never clicked, so an hour later
    there was no way to tell which profiles refused or why. That misreading has
    a direction: "the profile didn't open" invites the operator to remove the
    protection that just fired correctly.

    SIZED FOR TWENTY, NOT FOR ONE. Bulk launch is a real workflow, so the
    failure mode to design against is a wall of identical prose. This is a
    one-line chip spliced into the meta row that ALREADY EXISTS on every card:
    twenty refusals cost twenty short lines in a column the operator is already
    scanning, with no card growing taller and no layout shifting. The full
    settled sentence rides the TOOLTIP, where it is one hover away for the row
    the operator actually cares about instead of twenty paragraphs competing at
    once.

    IT CARRIES ITS AGE, and that is what makes it honest rather than merely
    persistent. The marker is meant to be found long after the fact, and a
    marker that cannot say when it happened gets read as NOW. "refused 40m ago"
    is a timestamped historical claim that stays true however long it sits
    there; a bare "refused" would quietly become a claim about the present.

    Drawn in ``error`` red and never dimmed: this is the loudest thing on the
    row, deliberately, because it is the one event the ticket says routine
    noise must not be allowed to drown.

    Pure render — it draws the value it is handed and performs no IO.
    """
    if refusal is None:
        return []
    return [
        ft.Container(
            border_radius=3,
            border=ft.Border.all(1, COLORS["error"]),
            padding=ft.Padding.symmetric(horizontal=6, vertical=1),
            # The full, settled sentence — composed in process.py with the
            # profile and proxy named and the one-click remedy spelled out. It
            # is passed through verbatim, never restated here: the wording
            # deliberately distinguishes "never checked successfully" from "the
            # last check FAILED", and a copy in the UI layer would fork from it
            # at the first edit.
            tooltip=(
                f"Launch REFUSED {humanize_since(refusal.at, now)}.\n\n"
                f"{refusal.detail}\n\n"
                "This is the fail-closed guard working, not a crash: the "
                "profile was not opened, so nothing was disclosed."
            ),
            content=ft.Text(
                f"refused · {refusal.label} · {humanize_since(refusal.at, now)}",
                size=10,
                color=COLORS["error"],
                font_family=MONO,
            ),
        )
    ]


def _unresolved_cert_chip(
    cert_unresolved: bool, cert_name: str | None
) -> list[ft.Control]:
    """The 'this profile's certificate does not resolve' marker, or nothing.

    Returned as a list so the caller splices it in — a profile whose
    certificate resolves, and a profile with no certificate at all, render
    EXACTLY as they did before this marker existed: no empty box, no
    placeholder, no spacing change. The marker's PRESENCE is the signal, on
    ``_refusal_chip``'s argument.

    WHY THIS EXISTS. A profile can name a certificate the store does not hold —
    a record skipped as malformed (``cert/store.py``), or a whole quarantined
    ``certificates.json``. That assignment used to be destroyed on sight: the
    next profile-dialog save collapsed it to "no certificate". Since the
    preserve fix it is DURABLE and deliberate, and nothing downstream was built
    for a state that sticks around — the launch drops the client certificate in
    silence, the certificates page lists only records that EXIST, and the one
    warning that does exist lives inside the profile dialog, which the operator
    opens to EDIT a profile, not to check one. The launch button is here, on the
    card.

    IT IS DERIVED, NEVER REMEMBERED. The caller answers it from the certificate
    store on the render path (a dict lookup under a lock, no IO); nothing is
    persisted and no verdict is resurrected. The pre-launch clear of
    ``cert_trust_status`` stays exactly as it is — that clear is correct, and a
    marker rebuilt from a stale affirmative verdict would be the precise
    dishonesty it exists to prevent.

    IT NAMES THE CERTIFICATE. "certificate not found" alone sends the operator
    to a certificates page where, by construction, the missing name is absent —
    so the name is the one thing the row must carry. The full sentence, with
    what actually happens at launch, rides the TOOLTIP: one hover away for the
    row the operator cares about, rather than a paragraph on every card.

    WHAT IT DOES NOT CLAIM. It says the assignment does not resolve, not that
    the launch will fail: the launch proceeds, correctly and safely, WITHOUT the
    client certificate. That is exactly the outcome the operator cannot
    currently see, and stating it as a failure would be its own dishonesty.

    Pure render — it draws the values it is handed and performs no IO.
    """
    if not cert_unresolved:
        return []
    name = cert_name or ""
    return [
        ft.Container(
            border_radius=3,
            border=ft.Border.all(1, COLORS["warning"]),
            padding=ft.Padding.symmetric(horizontal=6, vertical=1),
            tooltip=(
                f"The certificate {name!r} is assigned to this profile but was "
                "not found.\n\n"
                "Launching opens the browser WITHOUT a client certificate, so "
                "a site that expects one will not recognise this profile.\n\n"
                "The assignment is kept, not silently dropped — re-add the "
                "certificate under the same name, or reassign one in the "
                "profile dialog."
            ),
            content=ft.Text(
                f"cert not found · {name}",
                size=10,
                color=COLORS["warning"],
                font_family=MONO,
            ),
        )
    ]


def _automation_channel_chip(cdp_channel_open: bool) -> list[ft.Control]:
    """A distinct 'automation channel open' marker, or nothing at all.

    Returned as a list so the caller splices it in: when no channel is open the
    card renders EXACTLY as it did before this indicator existed — no empty
    box, no placeholder, no spacing change. The marker's presence is the
    signal, so an absent marker must be a real absence and not a dimmed variant
    the eye learns to skip.

    ``cdp_channel_open`` is a fact about the LIVE SESSION, captured by the
    launcher when the browser was spawned (``BrowserLauncher.cdp_channel_open``).
    It is deliberately NOT ``profile.ai_control``: that stored field can be
    flipped mid-session by the connect-page checkbox without touching the port
    chromium already bound, so rendering it would produce a quiet card over a
    listening unauthenticated channel.

    Pure render: it draws the boolean it is handed and performs no IO. Resolving
    an actual CDP port means a file read (``read_cdp_port``) or live network IO
    (``cdp_info_for``); neither may sit on a render path, because an indicator
    that probes on every redraw is a different security object from one whose
    cadence is a human opening a window.
    """
    if not cdp_channel_open:
        return []
    return [
        ft.Container(
            border_radius=3,
            border=ft.Border.all(1, COLORS["warning"]),
            padding=ft.Padding.symmetric(horizontal=6, vertical=1),
            # Named in full rather than as a glyph or the bare acronym: this is
            # the one surface that tells an operator an unauthenticated channel
            # is open on a browser they are watching, and "CDP" alone assumes
            # the reader already knows what it costs.
            tooltip=(
                "This session was launched with AI/CDP control: "
                f"{CHROMIUM_ENGINE_NAME} has an "
                "unauthenticated remote-debugging port open, which any process "
                "running as this user can drive. It closes when the session ends.\n\n"
                "Reflects the RUNNING session — toggling AI control now does not "
                "close a port that is already open."
            ),
            content=ft.Text(
                "automation",
                size=10,
                color=COLORS["warning"],
                font_family=MONO,
            ),
        )
    ]


def _build_action_buttons(
    name: str,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    is_running: bool = False,
) -> list[ft.Button]:
    return [
        # Editing a profile while its browser is open can corrupt its data dir /
        # fingerprint mid-session, so disable edit until it's stopped.
        row_button(
            "[ edit ]",
            lambda _, n=name: on_edit(n),
            kind="edit",
            disabled=is_running,
            tooltip="Stop the profile to edit it" if is_running else "Edit profile",
        ),
        row_button(
            "[ x ]",
            lambda _, n=name: on_delete(n),
            kind="delete",
            tooltip="Delete profile",
        ),
    ]


def _build_select_box(
    name: str,
    is_selected: bool,
    on_select: Callable[[str], None] | None,
) -> ft.Container:
    return ft.Container(
        alignment=ft.Alignment(0, 0),
        on_click=lambda _, n=name: on_select(n) if on_select else None,
        ink=True,
        tooltip="Select profile",
        content=ft.Text(
            "[x]" if is_selected else "[ ]",
            size=13,
            color=COLORS["accent"] if is_selected else COLORS["text_dim"],
            font_family=MONO,
        ),
    )
