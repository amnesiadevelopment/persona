import asyncio
import contextlib
import os
import sys
import threading

import flet as ft

from ..core.container import Container
from ..core.logging import get_logger
from ..core.strings import get_string
from ..interfaces.protocols import IBrowserLauncher, IProfileManager, IProxyService
from .components import (
    build_bookmarks_page,
    build_tags_page,
    build_connect_page,
    build_certificates_page,
    build_content_area,
    build_empty_state,
    build_network_page,
    build_profile_card,
    build_sidebar,
    build_top_bar,
    build_trash_page,
    build_ui_refs,
    rebuild_bulk_bar,
)
from ..services.engine import policy as engine_policy
from ..services.engine import updater as engine
from ..services.app_update import updater as app_update
from ..core import platform as _platform
from ..core import settings as app_settings
from .components.onboarding import Onboarding
from .components import splash as splash_mod
from . import progress_fmt as pf
from .dialogs.proxy import open_proxy_dialog
from .dialogs.bookmark import open_bookmark_dialog
from .dialogs.pool import open_pool_dialog
from .dialogs.confirm import open_confirm_dialog
from .handlers import AppHandlers
from .refs import UIRefs
from .state import ITEMS_PER_PAGE, AppState
from .theme import ACCENT_STYLE, COLORS, configure_page

logger = get_logger("app")


from ..services.profile.filter import all_tags, filter_by_tag, filter_profiles


#: How much of an engine version string the 200px rail can carry on one line.
#: Monospace at size 12 runs ~6.2px per character, and the version cell gets
#: roughly 110px of the rail once the icon, the name and the state dot have
#: taken theirs — so ~17 characters, with the ellipsis inside that budget.
_VERSION_MAX_CHARS = 17

#: How many monospace characters ONE FULL-WIDTH line of the 200px rail carries.
#: PS-229's number, ADOPTED rather than re-derived, so the two panels sharing
#: the rail are held to one bound instead of two. It is deliberately WIDER than
#: :data:`_VERSION_MAX_CHARS`, which budgets the ~110px engine version CELL —
#: the app version panel's status line is a top-level child of the panel and
#: gets the whole rail, so measuring it against the cell's number would draw a
#: reveal chevron on lines that are already whole (see
#: :meth:`App._status_needs_reveal` for why that is a defect and not a
#: kindness).
_RAIL_MAX_CHARS = 22

#: How many lines an EXPANDED status may occupy before it is cut. The reveal
#: has to be bounded too, or "expand" just re-creates the defect one click
#: later: a stack trace pasted into a status string would push the panel out of
#: shape exactly as the unbounded row did. Three lines is enough to read a real
#: engine error ("firefox-142: signature check failed / expected sha256 ... /
#: got ...") and small enough that the panel's shape survives it.
_STATUS_EXPANDED_MAX_LINES = 3


def sidebar_status_text(
    value: str,
    *,
    size: int = 10,
    color: str | None = None,
    expanded: bool = False,
) -> ft.Text:
    """A status/label line that CANNOT push the engines panel out of shape.

    THE COMPLAINT THIS ANSWERS, and why round 1 did not close it. The version
    lines were bounded (see :func:`_short_engine_version` and the two engine
    Text controls in ``__init__``) — but the GREY LABEL rows beneath them were
    not. ``_engine_rollback_row``, ``_engine2_rollback_row`` and
    ``_engine_rollback_pending_row`` each built a bare ``ft.Text(label, size=10,
    color=text_dim)`` with no ``no_wrap``, no ``max_lines`` and no ``overflow``,
    and every one of them interpolates a RUNTIME string:

        f"resume updates (pinned to {pin})"
        f"go back to {target}"
        "rollback available after the next two engine updates"

    That is the grey text seen running past the panel's right edge. The engine
    STATUS strings are the other half: ``_engine2_status`` is assigned raw
    service text (``"couldn't go back — see the log"``, and on the failure path
    an arbitrary exception message), so its upper bound is whatever the engine
    check happened to produce — the "single enormous run of text".

    TWO THINGS ARE REQUIRED AND ONE ALONE IS NOT ENOUGH, which is the trap:

    1. ``max_lines``/``overflow`` bound the text's own layout, and
    2. ``expand=True`` bounds its WIDTH.

    A ``Text`` inside a ``Row`` is given its intrinsic width — a Row does not
    squeeze a child that did not ask to flex — so a long single-line string is
    laid out at full length and simply overflows the Row, with the ellipsis
    never engaging because, as far as the control is concerned, it was granted
    all the room it asked for. Bounding the lines without bounding the width is
    therefore precisely the fix that looks right and changes nothing.

    ``expanded`` is the REVEAL, not a second layout: the same control, allowed
    ``_STATUS_EXPANDED_MAX_LINES`` wrapped lines instead of one. It is still
    bounded (see that constant) — a reveal that is unbounded is the original
    defect deferred by one click.
    """
    return ft.Text(
        value,
        size=size,
        color=color or COLORS["text_dim"],
        font_family="monospace",
        expand=True,
        no_wrap=not expanded,
        max_lines=_STATUS_EXPANDED_MAX_LINES if expanded else 1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )


#: The rollback row's two labels, as FIXED PHRASES rather than f-strings.
#:
#: THE COMPLAINT THIS ANSWERS. The labels were built as ``f"go back to
#: {target}"`` and ``f"resume updates (pinned to {pin})"``, where the
#: interpolated value is a COMPLETE BUILD IDENTIFIER — a Firefox one reads
#: ``firefox-20_151.0_20260817150018``, 30 characters. Inside a 200px rail that
#: cannot fit and does not fit: it is what pushed the row past the panel's edge
#: and produced the enormous run of text under one of the engines.
#:
#: THE IDENTIFIER IS NOT DROPPED, IT IS RELOCATED. The row's tooltip already
#: named the build verbatim, so taking it out of the visible text loses the
#: operator nothing — it moves a detail from a place that cannot hold it to a
#: place that already did.
#:
#: What the LABEL has to carry is WHICH GESTURE this is, and that is a fixed
#: phrase in both states. A fixed phrase also has a fixed width, which is the
#: property the old label lacked: no runtime string can widen these.
_ROLLBACK_LABEL = "previous version"
_RESUME_LABEL = "resume updates"


def rollback_row(
    *,
    label: str,
    icon: str,
    tooltip: str,
    cost: str = "",
    cost_color: str | None = None,
    on_click=None,
    indent: int = 36,
) -> ft.Control:
    """One engine's rollback row: icon + SHORT label, with the COST of the
    gesture on its own short second line.

    WHY THE COST IS ON SCREEN AND NOT ONLY IN THE TOOLTIP — this is the one
    part of the old label that must NOT simply move into the tooltip with the
    build identifier, and the two are different in kind:

      * the build identifier answers "which build?", which an operator asks
        deliberately, after deciding to revert. A tooltip is the right home.
      * the COST answers "what will this do to me?", which an operator needs
        BEFORE deciding. Firefox's revert moves no bytes — both builds are
        already unpacked in versioned cache dirs. Chromium's RE-DOWNLOADS
        hundreds of megabytes, over Tor, because Chromium keeps a single
        un-versioned tree and the previous build's files are genuinely gone.

    A tooltip needs a hover that a trackpad operator may never perform, so a
    cost carried only there is a cost the operator meets AFTER clicking. A
    one-word label that hides a several-hundred-megabyte transfer is worse than
    the long label it replaced.

    IT IS A SECOND LINE RATHER THAN A SUFFIX because the label has to fit the
    rail with room to spare. ``previous version · re-downloads ~300 MB`` is 39
    monospace characters against a content width of about 22, so appending it
    would re-create the exact overflow this row exists to remove. Split across
    two lines, the longest thing ever rendered here is 20 characters.

    BOTH LINES GO THROUGH :func:`sidebar_status_text`, so both are
    width-bounded and single-line. The bound round 2 established is not
    weakened by adding a line beneath it — it is applied to that line too.

    ``indent`` is the row's LEFT inset and defaults to the engine rows' 36px,
    which aligns them under the engine name they belong to. The APP version
    panel's rollback row is not nested under anything — it is a top-level
    child of that panel, level with the version line and the status line
    beneath it — so it passes ``indent=0``. This is a POSITION parameter only:
    the bound above applies identically at either value.
    """
    text_line = ft.Row(
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(icon, size=13, color=COLORS["text_dim"]),
            sidebar_status_text(label),
        ],
    )
    if not cost:
        body: ft.Control = text_line
    else:
        body = ft.Column(
            spacing=1,
            controls=[
                text_line,
                ft.Row(
                    spacing=6,
                    controls=[
                        # Aligns the cost under the LABEL rather than under the
                        # icon: 13px icon + the Row's 6px spacing.
                        ft.Container(width=13),
                        sidebar_status_text(
                            cost, size=9, color=cost_color or COLORS["text_dim"]
                        ),
                    ],
                ),
            ],
        )
    return ft.Container(
        padding=ft.Padding.only(left=indent, right=10, top=4, bottom=2),
        on_click=(lambda _: on_click()) if on_click else None,
        ink=bool(on_click),
        tooltip=tooltip,
        content=body,
    )


def _short_engine_version(version: str) -> str:
    """An engine version the rail can actually show on one line.

    THE COMPLAINT: "под хромиумом длина текста больше чем «Война и мир»". The
    Chromium row carried a full upstream build string — ``148.0.7778.215``, and
    with the old "update → " prefix 23 characters — into a cell with room for
    about 17. It wrapped, and a wrapping line in a fixed-height panel is what
    made the text visibly shift when the section opened.

    An operator glancing at the rail needs to know WHICH engine and WHETHER it
    is current. A Chromium build's identity is its MAJOR: 148 vs 147 is the
    answer to "what am I running", while ".0.7778.215" distinguishes builds
    nobody chooses between by hand. So a 4-part Chromium version is cut to
    ``148.0.7778`` — the part that still reads as a version — and anything
    else is ellipsised at the budget rather than being silently clipped.

    WHERE THE FULL STRING GOES — AND THE ANSWER IS "NOWHERE", FOR THE ENGINE
    ROWS THIS SERVES. An earlier version of this docstring said the row's
    tooltip carries it verbatim. That is TRUE OF THE ROLLBACK ROW, whose
    tooltip really does interpolate the build identifier, and it is FALSE of
    the two engine status rows every caller of this function feeds: their
    tooltips are the static gesture strings "Check / update fp-chromium" and
    "Check / update the Firefox engine", with no version in either. It is not
    behind the reveal chevron either — see :meth:`_engine_row`, which works
    the arithmetic through: this function caps AT ``_VERSION_MAX_CHARS`` and
    :meth:`_status_needs_reveal` fires only ABOVE it, so shortening a version
    is exactly what turns the chevron off.

    So on an engine row the ellipsised tail is genuinely unreachable. That is
    accepted (PS-229): the line answers "which engine, and am I current?",
    which the shortened form and the accent dot answer between them, and the
    tail is a build timestamp nobody picks between by hand. Restoring an
    affordance would break item 6's rule that a whole line gets no reveal
    control.

    Shortening happens HERE, at the source of the value, rather than by
    clamping the Text control — a clamp would hide the overflow instead of
    removing it, and the panel would still be sized for text nobody can read.
    """
    text = (version or "").strip()
    if len(text) <= _VERSION_MAX_CHARS:
        return text
    parts = text.split(".")
    if len(parts) >= 4 and all(p.isdigit() for p in parts[:3]):
        trimmed = ".".join(parts[:3])
        if len(trimmed) <= _VERSION_MAX_CHARS:
            return trimmed
    return text[: _VERSION_MAX_CHARS - 1] + "…"


def _show_window(page: ft.Page) -> None:
    """Reveal the window (it starts hidden via hide_window_on_start). Idempotent
    and best-effort: called once the centred splash is the current frame, and
    again from the startup-error path so a crash can never leave a hidden window
    with the process alive. A failure to set visibility must not itself crash
    startup, so it's swallowed."""
    try:
        page.window.visible = True
        # Centre on macOS now that the native window is realised. Setting
        # left/top in configure_page (before reveal) crashed the built
        # flet-desktop app on launch; here the window exists, so it's safe.
        # A centring failure must NOT skip the page.update() below — otherwise a
        # hiccup while positioning would leave the window unrevealed (the exact
        # invisible-zombie we're fixing), so positioning gets its own guard.
        if sys.platform == "darwin":
            try:
                from .theme.page import _primary_work_rect

                wx, wy, ww, wh = _primary_work_rect()
                win_w = int(getattr(page.window, "width", None) or 1280)
                win_h = int(getattr(page.window, "height", None) or 820)
                if ww and wh:
                    page.window.left = wx + max(0, (ww - win_w) // 2)
                    page.window.top = wy + max(0, (wh - win_h) // 2)
            except Exception:
                logger.exception("Could not centre the window on macOS")
        page.update()
    except Exception:
        logger.exception("Could not reveal the window")


class App:
    def __init__(
        self,
        container: Container | None = None,
        api_server=None,
        api_server_factory=None,
    ) -> None:
        c = container or Container()
        # api_server may be supplied ready-made (tests) or built lazily off the
        # startup path via api_server_factory (production — see main.py); it's
        # only needed when the user turns Claude control on.
        self.api_server = api_server
        self._api_server_factory = api_server_factory
        self.pm: IProfileManager = c.profile_manager
        self.bl: IBrowserLauncher = c.browser_launcher
        self.ps: IProxyService = c.proxy_service
        # Delete/wipe must stop a running browser before rmtree'ing its data dir.
        self.pm.set_stop_hook(self.bl.stop_profile)
        # Delete/wipe/rename/overwrite must ALSO tell the launcher the name has
        # stopped meaning what it meant, so per-name state is not inherited by
        # whatever takes the name next. Separate from the stop hook above on
        # purpose: that one is about a live session (whose facts a refusal is
        # built to OUTLIVE), this one is about the identity going away.
        self.pm.set_forget_identity_hook(self.bl.forget_refusal)
        # Engine pruning deletes whole build trees (~320-600MB each) and keeps
        # only the HIGHEST build — so a profile still running on the PREVIOUS
        # build when a new one lands would have the tree it is executing from
        # deleted out from under it. POSIX does not refuse that unlink (only
        # Windows does, by accident), so pruning has to ASK. engine_install
        # sits below the launcher in the layering and cannot import it, so the
        # oracle is injected here, at construction — strictly before any prune
        # can run, which is what makes the otherwise-unguarded startup prune
        # (_auto_update_engine2 → prune_superseded_builds) safe by construction.
        # running_profile_names() reaps dead sessions via poll() before
        # answering, so it is the already-trusted oracle the app updater uses.
        self._wire_engine_prune_guard()
        self.pstore = c.proxy_store
        self.ssh_store = c.ssh_host_store
        self.bstore = c.bookmark_store
        self.cert_store = c.cert_store
        self.trash_service = c.trash_service
        self.state = AppState()
        self.page: ft.Page | None = None
        self._reconcile_started = False
        # one reusable logo-scan overlay + a generation token so rapid clicks
        # restart the sweep instead of stacking beams
        self._scan_flash = None
        self._scan_gen = 0
        # Set once the session loop is servicing tasks after the first page
        # build; until then worker threads must not marshal into it (see _ui).
        self._ui_ready = threading.Event()
        self._ui_backlog: list = []
        self._ui_backlog_lock = threading.Lock()
        self.refs: UIRefs | None = None
        self._active_page = "profiles"
        self._search_query = ""
        self._active_tag = ""
        self._page_host: ft.Container | None = None
        self._sidebar_host: ft.Container | None = None
        self._app_latest = ""
        self._app_update_url = ""
        self._app_update_size = 0
        self._app_update_tag = ""
        self._app_update_status = ""  # '', downloading, ready, failed
        # The last tag we told the operator was held. Purely a log de-duper:
        # the hold gate CLEARS _app_latest, which reopens the 60s poll's own
        # dedup (`tag != self._app_latest`), so without this the same "held"
        # line would be written once a minute forever.
        self._app_held_logged = ""
        self._app_update_done = 0
        self._app_update_total = 0
        self._update_in_progress = False
        self._update_staged = ""
        self._update_start_t = 0.0
        # Why a REFUSED app rollback needs its own rendered field: the gesture
        # is a rename, so it finishes in milliseconds with no progress bar to
        # watch. Without a status line on the row, a refusal is
        # indistinguishable from a dead button — and _log alone is not a
        # surface, because the sidebar log panel is hidden entirely when
        # collapsed. Mirrors _engine2_status on the Firefox engine row.
        self._app_rollback_status: str = ""
        # WHICH status string is currently REVEALED on the version panel, held
        # as the string itself rather than as a bool. The status line is
        # ellipsised into ~22 characters of rail, so "couldn't go back — see
        # the log" reaches the operator as "couldn't go back — s…" and loses
        # the actionable half; the reveal is what makes the tail recoverable,
        # exactly as _status_reveal_button does for the engine statuses next
        # door. It holds the STRING because a reveal belongs to the message it
        # was opened on: the next refusal is a different sentence and must
        # arrive collapsed rather than inheriting an open panel it never asked
        # for. Compared, never trusted — see _app_status_expanded.
        self._app_status_revealed: str = ""
        self._checking_proxies: set[str] = set()
        self._engine_latest: str = ""
        # _engine_busy = a real download is in flight (show the progress bar).
        # _engine_checking = a version check over the network is in flight (show
        # only a spinner, NEVER the bar — a version check moves no bytes, so the
        # leftover download bar reading "189 MB of 189 MB" was wrong).
        self._engine_busy = False
        self._engine_checking = False
        # Set when persona REFUSED the newest Chromium build (known-bad by name,
        # or above a ceiling the OPERATOR set in their own engine policy file —
        # see services/engine/policy.py). persona itself ships no Chromium
        # ceiling since PS-42, so this is never "persona is behind". Shown in the
        # engine row so the refusal is visible rather than looking like a stalled
        # check. Mirrors _engine2_status on the Firefox row.
        self._engine_status: str = ""
        # The build whose unattended install is currently DEFERRED because a
        # profile is running. Set when the deferral is first logged and cleared
        # once it installs, so the hourly retry doesn't repeat the same line
        # forever at an operator who keeps a profile open all day. Holds the tag
        # rather than a bool so a NEWER build supersedes it and speaks up again.
        self._engine_deferred_tag: str = ""
        # The build persona REFUSED because upstream published no sha256 for it
        # (PS-49). Distinct from _engine_deferred_tag above because the two
        # resolve differently: a deferral ends when the operator closes their
        # profiles, so retrying it hourly is the mechanism that lands it. This
        # does not resolve until UPSTREAM publishes a digest, so retrying it is
        # pure noise — the same fetch, the same refusal, every hour, forever.
        #
        # Consulted by _engine_update_available, which is the single predicate
        # behind the row's "update → NN" text, the sidebar dot, the click
        # handler AND the unattended hourly fetch. Keying the suppression there
        # rather than at the four call sites is what makes the refusal actually
        # stick: that docstring already promises a build persona would refuse is
        # never advertised as available, and before this the fourth refusal was
        # the one case that broke the promise — policy says OK, so nothing else
        # suppressed the offer.
        #
        # Holds the TAG, not a bool, for the same reason the deferral does: a
        # NEWER build is a new fact that upstream may well have published a
        # digest for, so it supersedes this and is offered normally.
        self._engine_unverifiable_tag: str = ""
        # The refusal's own words, kept so the click path can REPLAY them rather
        # than paraphrase them. _unverifiable_message is deliberately the single
        # source of this wording (it names the specific asset that could not be
        # verified); re-typing a shorter version here would be a fifth wording
        # for a situation that already has exactly one, free to drift from it.
        self._engine_unverifiable_msg: str = ""
        self._engines_open = False
        # Whether each engine's status line is currently REVEALED (wrapped to
        # _STATUS_EXPANDED_MAX_LINES) rather than ellipsised to one line. Per
        # engine, not one shared flag: an operator reading a Firefox error must
        # not have the Chromium row silently change height underneath them.
        self._engine_status_expanded = False
        self._engine2_status_expanded = False
        # An onboarding/changelog dialog owns the screen at startup; a staged
        # update that lands while it's open is held here and offered once the
        # onboarding closes, so the two dialogs never stack (#226).
        self._onboarding_open = False
        self._pending_update: tuple[str, str] | None = None
        self._engine2_latest: str = ""
        # False when the newest firefox-NN release doesn't ship the asset the
        # bundled engine package expects — it needs a persona update, not an
        # engine download.
        self._engine2_compatible = True
        self._engine2_busy = False
        self._engine2_status: str = ""
        self._engine2_checking = False
        self._engine2_start_t = 0.0
        self._engine_throttle = pf.ProgressThrottle()
        self._engine2_throttle = pf.ProgressThrottle()
        self._engine_pstate = pf.ProgressState()
        self._engine2_pstate = pf.ProgressState()
        self._engine2_bar = ft.ProgressBar(
            value=None, color=COLORS["accent"], bgcolor=COLORS["input_bg"], height=4,
        )
        # Bounded for the same reason as the grey label rows: this line carries
        # progress prose assembled at runtime ("142.3 MB of 380.1 MB — 12s
        # left"), so its length is a property of the download, not of the
        # design. See sidebar_status_text for why expand=True is half the fix.
        self._engine2_detail = sidebar_status_text(
            "", color=COLORS["text_sub"]
        )
        # SINGLE LINE, BOTH OF THEM. These are the two controls the owner was
        # looking at when he said the Chromium text was longer than War and
        # Peace: given a narrow cell and no wrap rule, flet breaks a version
        # across as many lines as it needs ("148.0" / ".7778" / ".215"), which
        # is both the length complaint AND the "съехавший" shift — the panel's
        # height changes with the text. Ellipsis instead of wrap makes the row
        # a fixed one-line object. The ellipsised tail is NOT recovered from a
        # tooltip: these two rows' tooltips are static gesture strings with no
        # version interpolated (the ROLLBACK row is the one whose tooltip
        # carries a build identifier). See _short_engine_version.
        self._engine2_text = ft.Text(
            "", size=12, color=COLORS["text_main"], font_family="monospace",
            no_wrap=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
            text_align=ft.TextAlign.RIGHT,
        )
        self.engine_text = ft.Text(
            "...",
            size=12,
            color=COLORS["text_main"],
            font_family="monospace",
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            text_align=ft.TextAlign.RIGHT,
        )
        self._engine_start_t = 0.0
        self._engine_bar = ft.ProgressBar(
            value=None,
            color=COLORS["accent"],
            bgcolor=COLORS["input_bg"],
            height=4,
        )
        # Bounded for the same reason as its Firefox twin above.
        self._engine_detail = sidebar_status_text(
            "", color=COLORS["text_sub"]
        )
        self.count_text = ft.Text(
            "0",
            size=14,
            color=COLORS["text_sub"],
            font_family="monospace",
        )
        c.event_bus.subscribe(self.state.schedule_refresh)
        self.h = AppHandlers(
            pm=self.pm,
            bl=self.bl,
            ps=self.ps,
            state=self.state,
            get_page=lambda: self.page,
            get_refs=lambda: self.refs,
            log_fn=self._log,
            refresh_fn=self._refresh_profiles,
            get_page_profiles=self._get_page_profiles,
            get_proxy_names=lambda: self.pstore.names(),
            get_pool_names=lambda: self.bstore.pool_names(),
            get_bookmarks=lambda: self.bstore.list_bookmarks(),
            get_cert_names=lambda: self.cert_store.names(),
            import_cookies_file=self._import_cookies_file,
            export_cookies_file=self._export_cookies_file,
            open_add_proxy=self._goto_add_proxy,
            ui_fn=self._ui,
        )

    def run(self) -> None:
        ft.run(self._main)

    def _main(self, page: ft.Page) -> None:
        # The splash must be the whole first frame: flet flushes the initial
        # patch when _main returns, so anything built here delays that first
        # paint and the window sits on the client's startup screen. Add only
        # the splash, then build the real UI in the first serviced task while
        # the scan animation runs.
        self.page = page
        try:
            configure_page(page)
            # Ask before closing while browsers are open (PS-223 outcome 2).
            # Installed HERE, beside configure_page, rather than after the UI
            # finishes building: the X is clickable from the moment the window
            # is revealed, and the splash can be on screen for seconds. A hook
            # installed after the first paint would leave that whole stretch
            # closing silently — the exact accidental close this exists to stop.
            self._install_close_guard(page)
            self._splash = splash_mod.Splash()
            page.add(self._splash.control)
            self._splash.start(page)
        finally:
            # The window starts HIDDEN (pyproject hide_window_on_start) so the
            # user never sees the client's off-centre corner spinner or the jump
            # to centre — the splash is the first frame. Reveal the window in a
            # `finally` so it shows even if building the splash above raised:
            # with the window hidden, an unrevealed window + a live process is
            # exactly the invisible zombie the old ban feared. Revealed, any
            # failure is SEEN (blank/partial window > invisible process). The
            # _finish_startup except also re-reveals before painting its error.
            _show_window(page)
        page.run_task(self._finish_startup)

    async def _finish_startup(self) -> None:
        """First task the session loop services after _main: flet runs a sync
        main() directly ON the session loop, so nothing scheduled with
        page.run_task executes until _main returns and the initial page patch
        (the splash) has been flushed. Do all the loading the first screen
        depends on BEHIND the splash — so the swap below brings in a finished
        UI, nothing pops in afterwards — keep it up for at least one full scan
        cycle, then swap the root layout in. Everything gated here is local,
        bounded work (files + process checks); network work stays in the
        background with its own progress UI, so the splash cannot hang on a
        dead circuit. The window is visible throughout, so a failure here is
        caught and painted as a readable error instead of leaving the splash
        sweeping forever over a broken app."""
        import time

        page = self.page
        assert page is not None
        started = time.monotonic()
        try:
            fp = ft.FilePicker()
            page.services.append(fp)
            self.refs = build_ui_refs(
                pm=self.pm,
                on_change_page=self._change_page,
                file_picker=fp,
            )
            root = self._build_root_layout(self.refs)
            self._render_active_page()
            self._refresh_profiles()
            self._refresh_engine_text()
            self.state._last_running_snapshot = self.bl.running_profile_names()
            # Find browsers a PREVIOUS persona left running, BEFORE the first
            # paint uses is_running to decide what each card offers. Scanning
            # after the render would paint LAUNCH over a profile that already
            # has a live browser — the very click this ticket exists to refuse.
            self._scan_survivors()
        except Exception as e:
            logger.exception("Startup failed while building the first screen")
            # Force the window visible before painting the error — with
            # hide_window_on_start the window may still be hidden if _main's
            # reveal didn't land, and a hidden error screen would be an invisible
            # zombie. This guarantees the failure is SEEN.
            _show_window(page)
            self._splash.stop()
            page.controls.clear()
            page.add(
                ft.Text(
                    f"persona failed to start: {e}",
                    color=COLORS["text_main"],
                    selectable=True,
                )
            )
            page.update()
            return
        remaining = splash_mod.MIN_SECONDS - (time.monotonic() - started)
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._splash.stop()
        page.controls.clear()
        page.add(root)
        self._show_startup_notice()
        self._check_app_update_async()
        self._check_engines_periodic()
        self._auto_update_engine2_async()
        self._start_server_if_enabled()
        if not self._reconcile_started:
            self._reconcile_started = True
            page.run_task(self._ui_reconcile_loop)
        await self._on_session_ready()

    async def _on_session_ready(self) -> None:
        """The window is built: flush the UI callbacks workers queued while it
        was still building (see _ui), then start the engine bootstraps —
        kicked off during the build they streamed marshaled updates into a
        loop that wasn't servicing tasks yet and froze the first window build
        on a fresh install (#124)."""
        with self._ui_backlog_lock:
            self._ui_ready.set()
            backlog = self._ui_backlog
            self._ui_backlog = []
        for fn in backlog:
            try:
                fn()
            except Exception as e:
                logger.error("Error in UI callback: %s", e)
        # During onboarding the engine download is user-driven (on_finish
        # kicks it); don't start a second one underneath the dialog.
        if app_settings.is_onboarding_done():
            self._check_engine_async()
            self._ensure_engine2_async()

    def _build_sidebar(self) -> ft.Container:
        r = self.refs
        assert r is not None
        # DIRECTION A: the Activity Log no longer lives in the sidebar at all —
        # it is docked full-width along the bottom of the window (see
        # _build_log_dock). The sidebar keeps only navigation and the engine /
        # version cluster, so nothing is crushed when the window is short.
        engine_panel = self._build_engines_panel()
        return build_sidebar(
            active_page=self._active_page,
            on_navigate=self._navigate,
            log_panel=None,
            engine_panel=engine_panel,
            version_panel=self._build_version_panel(),
            on_logo_click=self._on_logo_click,
            trash_expiring=self._trash_expiring_count(),
        )

    def _trash_expiring_count(self) -> int:
        """How many trashed entries are inside the near-expiry window.

        Asked on every sidebar rebuild — which is navigation and refresh, both
        paths that already exist, so NO timer and NO polling loop is added for
        this. The query is read-only by construction (see
        ``TrashStore.expiring_within``), which is what makes it safe to ask on
        a repaint.

        Never raises. A count is decoration on a rail that must paint; a
        corrupt or quarantined trash.json has to cost the operator the badge,
        not the window.
        """
        try:
            return len(self.trash_service.expiring_within())
        except Exception:
            logger.exception("Could not read the trash near-expiry count")
            return 0

    def _update_button(self, label: str) -> ft.Control:
        # full-width, single-line: the sidebar is only ~200px so a default
        # Button wraps "[ update to vX.Y.Z ]" onto two lines and tears the box.
        return ft.Container(
            on_click=lambda _: self._apply_update_now(),
            ink=True,
            height=30,
            border_radius=3,
            border=ft.Border.all(1, COLORS["accent"]),
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.symmetric(horizontal=6, vertical=0),
            content=ft.Text(
                label,
                size=11,
                color=COLORS["accent"],
                font_family="monospace",
                no_wrap=True,
                max_lines=1,
                text_align=ft.TextAlign.CENTER,
            ),
        )

    def _app_rollback_row(self) -> ft.Control | None:
        """The undo gesture for a bad app update — the macOS .app counterpart of
        _engine2_rollback_row, and it follows the same rule that row states:

          * a previous bundle is retained — offer "go back to it".
          * nothing retained — render NOTHING AT ALL (return None). A revert
            with no retained bundle cannot work, and a button that cannot work
            is worse than no button: it promises the machine can undo something
            it cannot.

        THERE IS NOW A HELD STATE TO MIRROR, and it is the third one this row
        renders (PS-208). The engine rows have carried a PINNED state all along
        — "the operator already went back, so offer resume" — and the app
        updater simply had no standing instruction to show. It has one now, so
        the states are "held", "retained" and "not", read in that order for the
        same reason the engine rows read the pin first: after a revert BOTH are
        true (the reverted-from build occupies the retained slot, so a "go back"
        button is still live), and offering "go back" to someone who just went
        back is the wrong gesture. Resume is what they need next.

        This is also the surface AC5 is about. A hold the operator cannot see
        and cannot clear is worse than the loop it fixes: _on_app_rollback's own
        docstring argues at length that _log is NOT a visible surface, because
        the sidebar log panel renders only while expanded. So the held state is
        rendered on the row they are already looking at, and it is a BUTTON."""
        if self._update_in_progress or self._update_staged:
            return None
        try:
            held = app_update.held_version()
        except Exception:
            # Never let an unreadable settings file take the resume gesture
            # away silently — that strands the operator held with no way out
            # and nothing on screen saying why. Same lesson as the Chromium
            # row, which logs its read failure rather than only degrading.
            self._log("Update: couldn't read the update-hold state")
            held = ""
        if held:
            # THE HELD VERSION LEAVES THE LABEL AND STAYS IN THE TOOLTIP —
            # the same relocation PS-229 performed on the engine rows, for the
            # same reason. `f"resume updates (held {held})"` is 27 characters
            # with a short tag and 35 with `3.0.10-beta.1`, against a rail with
            # room for about 22: an interpolated identifier in a visible label
            # is unbounded by construction. The tooltip directly below already
            # names the held build verbatim, so nothing is lost by taking it
            # out of the text — it moves from a place that cannot hold it to a
            # place that already did. See _RESUME_LABEL.
            return rollback_row(
                label=_RESUME_LABEL,
                icon=ft.Icons.HISTORY,
                # THE SECOND LINE SAYS THE STATE, NOT THE GESTURE — the phrase
                # both engine resume rows already use, because the app updater
                # and the engines must not describe the same situation
                # differently. What the operator cannot see from the label is
                # WHY the row is offering this at all: the hold is keeping
                # automatic updates off right now.
                cost="auto-update held off",
                tooltip=(
                    f"persona {held} is held back because you went back from "
                    "it. Clear the hold and let it install again."
                ),
                on_click=self._on_app_resume_updates,
                # FLUSH LEFT, unlike the engine rows. Theirs sit beneath an
                # engine name and are indented under it; this one is a
                # top-level child of the version panel, level with the version
                # line and the status line beneath it. See rollback_row.
                indent=0,
            )
        try:
            target = app_update.rollback_target()
        except Exception:
            # the panel must render even if the install location is unreadable
            return None
        if not target:
            return None

        return rollback_row(
            label=_ROLLBACK_LABEL,
            icon=ft.Icons.HISTORY,
            tooltip=(
                "Go back to the previous version of persona, kept from the "
                "last update — no download needed"
            ),
            on_click=self._on_app_rollback,
            indent=0,
        )

    def _app_status_expanded(self) -> bool:
        """Whether the version panel's CURRENT status is revealed.

        READ THROUGH THIS, NEVER OFF THE ATTRIBUTE DIRECTLY, for the same
        coupling reason :meth:`_status_expanded` states: ``_build_version_panel``
        is reachable from construction paths that never run ``__init__`` (the
        panel specs build the app with ``App.__new__(App)``), so a builder that
        hard-requires an ``__init__``-only attribute raises ``AttributeError``
        on every one of them while working fine in the real app.

        THE FLAG IS COMPARED, NOT TRUSTED. It holds the string it was opened
        on, so a reveal cannot outlive its own message: the operator opens
        "couldn't go back — see the log", the next click refuses differently,
        and the new sentence arrives collapsed rather than inheriting an open
        panel it never asked for. A bool could not express that — it would
        silently re-reveal whatever came next.
        """
        current = self._app_rollback_status
        return bool(current) and getattr(
            self, "_app_status_revealed", ""
        ) == current

    def _toggle_app_status(self) -> None:
        """Reveal / re-collapse the version panel's status line in place."""
        self._app_status_revealed = (
            "" if self._app_status_expanded() else self._app_rollback_status
        )
        self._refresh_sidebar()

    def _app_status_reveal_button(self, expanded: bool) -> ft.Control:
        """The gesture that shows a truncated app status in full.

        THE SAME AFFORDANCE THE ENGINE STATUSES ALREADY HAVE, deliberately
        identical rather than merely similar: two panels in one rail that
        truncate the same way must not recover differently, which is this
        ticket's whole thesis. See :meth:`_status_reveal_button` for why it is
        expand-in-place and not a tooltip — invisible in a screenshot, needs a
        hover a trackpad operator may never perform, and the tooltip gesture on
        the rollback row already means something else.

        WHY THE STATUS LINE NEEDS IT AT ALL, which is the half a bound alone
        does not supply. ``_on_app_rollback``'s own docstring argues that a
        refusal must be VISIBLE because ``_log`` is not a surface — the sidebar
        log renders only while expanded. Ellipsised into ~22 characters,
        "couldn't go back — see the log" reaches the operator as roughly
        "couldn't go back — s…", and *see the log* is the entire actionable
        half of the sentence. Bounded-and-recoverable is the fix; bounded alone
        would trade an overflow for a silent truncation of the one channel the
        refusal has.

        IT IS ITS OWN CONTROL, not the status line's click, for the same reason
        the engine one is: the row above already owns a click that reverts the
        application.
        """
        return ft.Container(
            on_click=lambda _: self._toggle_app_status(),
            ink=True,
            width=16,
            height=16,
            border_radius=3,
            alignment=ft.Alignment.CENTER,
            tooltip=(
                "Hide the full status" if expanded else "Show the full status"
            ),
            content=ft.Icon(
                ft.Icons.UNFOLD_LESS if expanded else ft.Icons.UNFOLD_MORE,
                size=12,
                color=COLORS["text_dim"],
            ),
        )

    def _on_app_resume_updates(self) -> None:
        """Clear the app-update hold: the operator saying "go forward again".
        The release they went back from is offered once more on the next check.

        Mirrors _on_engine_resume, and carries the same asymmetry the app
        rollback row already has: nothing moves here and nothing is on disk to
        switch to, so going forward is an ordinary download on the next poll.

        Refused while an update is pending for the same reason the revert is —
        a hold cleared mid-install would let _when_update_ready's second gate
        wave through the very build the operator is still deciding about."""
        if self._update_in_progress or self._update_staged:
            self._app_rollback_status = "can't resume while an update is pending"
            self._refresh_sidebar()
            return
        try:
            app_update.resume_app_updates(log=self._log)
        except Exception as e:
            self._log(f"Update: couldn't resume updates ({e})")
            self._app_rollback_status = "couldn't resume — see the log"
            self._refresh_sidebar()
            return
        # Drop the stale "restart to run the previous version" line: it named a
        # hold that no longer exists, and leaving it up would tell the operator
        # they are still held when they are not.
        self._app_rollback_status = ""
        # The next poll must be able to re-offer the release just un-held. The
        # 60s loop dedups on `tag != self._app_latest`, so a tag still sitting
        # in that field from the held check would be skipped forever — the
        # resume would be nominal until a THIRD release appeared.
        self._app_latest = ""
        self._refresh_sidebar()

    def _on_app_rollback(self) -> None:
        """Put the retained previous bundle back. Instant — it is a rename, not
        a download — so there is no progress bar, which is exactly why a
        refusal must be VISIBLE rather than log-only: without it a refused
        revert is indistinguishable from a dead button. The service call owns
        the decision; this only reports it.

        _log is NOT that visible surface: it reaches the sidebar log panel,
        which renders only when the operator has it expanded, so a refusal
        that goes solely to the log has no user-visible surface at all with the
        panel collapsed. The status line on the row is where they are already
        looking, and it is set on every exit below.

        REFUSED WHILE AN UPDATE IS PENDING, and this guard is not decoration
        just because _app_rollback_row() already declines to render in the same
        condition: reaching here means the row was built BEFORE the update
        arrived and the operator clicked one that has since gone stale — which
        is precisely the window the guard exists for, not a case it may assume
        away.

          * The race. On macOS the install runs in a daemon thread spawned
            AFTER the dialog is popped (_offer_install -> on_install), so the
            sidebar stays live for the whole of a sha256 re-verify, a checksum
            fetch, `hdiutil attach` and `ditto` — seconds to minutes. A click
            in that window renames app -> app.reverting and app.bak -> app
            while _apply_macos is mid-`ditto` INTO app: two uncoordinated
            renames of the same paths, one of them mid-copy. _apply_update
            refuses the mirror-image case one gesture over ("execv would kill
            the writer mid-extract"); the artifact corrupted here would be the
            .app itself — the exact brick this ticket ships retention to make
            survivable.
          * The contradiction. _update_staged survives a revert untouched, so
            without this the panel renders "[ restart to update ]" and
            "restart to run the previous version" at once, telling the operator
            to restart into two opposite versions with no way to tell which
            wins.

        BOTH flags are needed and neither is redundant: _update_in_progress
        does not by itself cover the install, because its `finally` clears it
        when the DOWNLOAD thread ends, long before the operator clicks "install
        now" — _update_staged is what spans the install window itself.

        THE ONE PLACE THIS DIFFERS FROM THE ENGINE SIBLING is that it says so
        instead of returning silently. That sibling's panel carries its own
        busy/checking indicator, so its silent return is still legible; this
        panel has none, and a stale row that swallows the click with no
        explanation is the dead-button defect this whole group of tests exists
        to prevent. "An update is pending" is also a state the operator can act
        on."""
        if self._update_in_progress or self._update_staged:
            self._app_rollback_status = "can't go back while an update is pending"
            self._refresh_sidebar()
            return
        try:
            went = app_update.revert_to_previous_build(log=self._log)
        except Exception as e:
            self._log(f"Update: going back failed ({e})")
            self._app_rollback_status = "couldn't go back — see the log"
            self._refresh_sidebar()
            return
        if went:
            self._log("Update: restart persona to run the previous version.")
            # No refusal to explain, and any stale complaint from an earlier
            # failed attempt must not outlive the attempt that succeeded.
            self._app_rollback_status = "restart to run the previous version"
        else:
            # Re-derive WHICH refusal it was rather than restating "no": the
            # two are not interchangeable. A retained bundle still on disk
            # means the RENAME was refused (a non-writable /Applications is the
            # ordinary case) and the log carries the OS error; no retained
            # bundle means there was never anything to go back to.
            try:
                retained = bool(app_update.rollback_target())
            except Exception:
                retained = False
            self._app_rollback_status = (
                "couldn't go back — see the log"
                if retained
                else "nothing to go back to"
            )
        self._refresh_sidebar()

    def _build_version_panel(self) -> ft.Control:
        from . import progress_fmt as pf

        ver = app_update.APP_VERSION
        has_update = bool(self._app_latest) and self._app_latest != ver
        auto_on = app_settings.is_auto_update_enabled()

        rows: list[ft.Control] = []

        # version (clickable = check/act now, #228) + a small accent dot when an
        # update is available.
        version_label = ft.Container(
            on_click=lambda _: self._on_version_click(),
            ink=True,
            tooltip="check for updates",
            border_radius=3,
            padding=ft.Padding.symmetric(horizontal=2, vertical=1),
            content=ft.Row(
                spacing=8,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(
                        f"persona v{ver}",
                        size=11,
                        color=COLORS["text_sub"],
                        font_family="monospace",
                    ),
                    *(
                        [
                            ft.Container(
                                width=7, height=7, border_radius=4,
                                bgcolor=COLORS["accent"],
                            )
                        ]
                        if has_update
                        else []
                    ),
                ],
            ),
        )

        # auto-update toggle, in persona's bracket style — sits on the same line
        # as the version so the version block stays compact.
        toggle = ft.Container(
            on_click=lambda _: self._set_auto_update(not auto_on),
            ink=True,
            border_radius=3,
            border=ft.Border.all(
                1,
                COLORS["accent_dim"] if auto_on else COLORS["card_border"],
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            tooltip=(
                "Auto-update is ON: new versions download and install\n"
                "automatically when no profiles are running. Click to turn off."
                if auto_on else
                "Auto-update is OFF: you update manually from here.\n"
                "Click to turn on automatic updates."
            ),
            content=ft.Text(
                f"[ auto-update: {'on' if auto_on else 'off'} ]",
                size=10,
                color=COLORS["accent"] if auto_on else COLORS["text_dim"],
                font_family="monospace",
                no_wrap=True,
            ),
        )

        # The sidebar is only ~200px, so the version + the full "[ auto-update:
        # on ]" bracket don't fit on one line (the toggle clipped). Keep the
        # version on its own line and the toggle stretched full-width below it.
        rows.append(version_label)
        toggle.alignment = ft.Alignment.CENTER
        rows.append(toggle)

        # status line / action
        if self._app_update_status == "downloading":
            done, total = self._app_update_done, self._app_update_total
            import time

            elapsed = max(time.monotonic() - self._update_start_t, 0.001)
            target = self._app_latest or "new version"
            if done <= 0:
                # no bytes yet: a Tor circuit can take a while to deliver the
                # first byte \u2014 say so instead of showing a frozen "0.0 MB".
                label = "connecting\u2026"
            elif total > 0:
                label = f"{pf.percent(done, total)}%"
            else:
                label = pf.fmt_mb(done)
            rows.append(
                ft.Text(
                    f"updating to {target} \u00b7 {label}",
                    size=10, color=COLORS["accent"], font_family="monospace",
                )
            )
            rows.append(
                ft.ProgressBar(
                    value=pf.fraction(done, total) if done > 0 else None,
                    color=COLORS["accent"], bgcolor=COLORS["input_bg"], height=4,
                )
            )
            rows.append(
                ft.Text(
                    pf.fmt_line(done, total, elapsed),
                    size=9, color=COLORS["text_sub"], font_family="monospace",
                )
            )
        elif self._update_staged or self._app_update_status == "ready":
            rows.append(self._update_button("[ restart to update ]"))
        elif has_update:
            rows.append(
                self._update_button(f"[ update to {self._app_latest} ]")
            )

        rollback = self._app_rollback_row()
        if rollback is not None:
            rows.append(rollback)

        # The status line is rendered INDEPENDENTLY of the rollback row above,
        # and that separation is load-bearing rather than tidiness. The row
        # obeys the "nothing retained — render nothing at all" rule and so
        # returns None in exactly the case one of the two refusals reports:
        # a bundle that vanished between the render and the click. Hanging the
        # status off the row would therefore drop the refusal precisely when it
        # needs to be read. Rendered here, every outcome of the gesture has a
        # visible surface whether or not the button survives it.
        if self._app_rollback_status:
            # BOUNDED AND RECOVERABLE — both halves, because the first alone
            # trades one defect for another.
            #
            # BOUNDED: the status is service prose ("can't go back while an
            # update is pending" is 40 characters) rendered into a rail with
            # room for about 22, and a bare ft.Text lays it out at full length
            # and runs past the panel's edge. sidebar_status_text is the same
            # bound PS-229 put on the identically-long engine statuses.
            #
            # RECOVERABLE: PS-229 did not only bound those statuses, it also
            # gave them a REVEAL (_status_needs_reveal / _status_reveal_button
            # / _status_control), so an ellipsised engine status can still be
            # read in full. Bounding this line without that half would leave
            # "couldn't go back — see the log" reaching the operator as
            # "couldn't go back — s…", silently dropping the actionable half of
            # the one channel a refusal has — _on_app_rollback's docstring is
            # explicit that _log is NOT a visible surface. So the chevron is
            # drawn on exactly the statuses that do not fit, and never on the
            # one that does ("nothing to go back to", 21).
            #
            # AGAINST _RAIL_MAX_CHARS, not _VERSION_MAX_CHARS: this line is a
            # top-level child of the panel and gets the whole rail, unlike an
            # engine status sharing its row with an icon, a name and a dot.
            #
            # INSIDE A ROW, deliberately, and not appended straight to the
            # panel Column: expand=True is what bounds the WIDTH, and it does
            # that on a Row's MAIN axis. It is the shape every other converted
            # site uses (rollback_row's text_line, _engine_rollback_pending_row)
            # — see sidebar_status_text for why bounding lines without width is
            # the fix that looks right and changes nothing.
            expanded = self._app_status_expanded()
            status_controls: list[ft.Control] = [
                sidebar_status_text(self._app_rollback_status, expanded=expanded)
            ]
            if self._status_needs_reveal(
                self._app_rollback_status, expanded, _RAIL_MAX_CHARS
            ):
                status_controls.append(self._app_status_reveal_button(expanded))
            rows.append(
                ft.Row(
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=status_controls,
                )
            )

        return ft.Container(
            border_radius=3,
            border=ft.Border.all(1, COLORS["card_border"]),
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            margin=ft.Margin.only(bottom=10),
            content=ft.Column(spacing=4, controls=rows),
        )

    def _build_root_layout(self, r: UIRefs) -> ft.Control:
        """The Activity Log as a full-width console docked along the bottom.

        The log spans the WHOLE window under both the 200px rail and the page,
        rather than living inside the rail. That is what frees the sidebar (its
        bottom cluster no longer competes with a log panel for a fixed 200px of
        width) and what gives the stream a reading line wide enough for the row
        to have real columns — see components/log_dock.py.

        The console's opening height is a BUDGET against the window, not a
        constant: at the app's own minimum size a fixed dock left the rail too
        short to show its own bottom cluster, so the dock yields there instead
        (it is the element the operator can drag back).
        """
        from .components.log_dock import LogDock

        self._sidebar_host = ft.Container(content=self._build_sidebar())
        self._page_host = ft.Container(expand=True)
        window_height = None
        with contextlib.suppress(Exception):
            page = self.page
            window_height = getattr(page, "height", None) or getattr(
                page.window, "height", None
            )
        self._dock = LogDock(
            on_fullscreen=self.h.open_log_fullscreen, window_height=window_height
        )
        with contextlib.suppress(Exception):
            self._dock.set_profiles(p.name for p in self.pm.list_profiles())

        # The budget above is computed from the height the window has RIGHT
        # NOW, and the window can be resized afterwards. The app opens at
        # 1280x820 with a 1024x680 minimum, so dragging down to the minimum is
        # an ordinary gesture — and without this the dock kept its launch-time
        # height and starved the rail by 116px, which is the same fault the
        # budget was written to fix, only reached through the resize path.
        with contextlib.suppress(Exception):
            self.page.on_resize = self._on_window_resize

        upper = ft.Row(
            expand=True,
            spacing=0,
            controls=[
                self._sidebar_host,
                ft.VerticalDivider(width=1, color=COLORS["border"]),
                self._page_host,
            ],
        )

        # The console is the LAST child of a full-height Column, so it spans
        # sidebar AND page: the log's width is no longer hostage to the rail.
        return ft.Column(
            expand=True,
            spacing=0,
            controls=[upper, self._dock.root],
        )

    def _on_window_resize(self, e=None) -> None:
        """Re-apply the console's rail budget after the window changes size.

        AC8 is a property of the window SIZE — "at 1024x680 the trash entry
        keeps visible separation" — not of how the window arrived there. The
        launch path honoured that and the resize path did not, so an operator
        who dragged a 1280x820 window down to the minimum got a dock still
        sized for the taller window and a rail 116px short of showing its own
        bottom cluster.

        The event carries the new height, but it is not trusted blindly: flet
        reports page height, and a headless/served session can report nothing
        at all, so a missing or unusable value falls back to the page and then
        to the window. A resize that cannot be measured must leave the console
        alone rather than collapse it to the minimum.
        """
        dock = getattr(self, "_dock", None)
        if dock is None:
            return
        height = None
        for candidate in (
            getattr(e, "height", None),
            getattr(self.page, "height", None),
            getattr(getattr(self.page, "window", None), "height", None),
        ):
            with contextlib.suppress(TypeError, ValueError):
                if candidate and float(candidate) > 0:
                    height = float(candidate)
                    break
        if height is None:
            return
        dock.apply_window_height(height)
        self._safe_update()

    def _on_logo_click(self) -> None:
        """Logo click = "scan": a single red beam sweeps down-and-back-up over
        the sidebar logo, and we land home on the profiles page with the list
        and live statuses reloaded (even when profiles is already active).

        ONE reusable ScanFlash lives in the overlay for the app's lifetime, so
        rapid clicks never stack a second beam — each click just re-homes the
        beam to the top and restarts the sweep (bumping a generation token so a
        still-running previous sweep bows out)."""
        page = self.page
        if page is not None and self._scan_flash is None:
            self._scan_flash = splash_mod.ScanFlash()
            page.overlay.append(self._scan_flash.control)
        self._active_page = "profiles"
        if self._sidebar_host is not None:
            self._sidebar_host.content = self._build_sidebar()
        self._render_active_page()
        self._refresh_profiles()  # pushes the page update, overlay included
        if page is not None and self._scan_flash is not None:
            self._scan_flash.reset()
            self._scan_gen += 1
            page.run_task(self._run_scan_flash, self._scan_gen)

    async def _run_scan_flash(self, gen: int) -> None:
        flash = self._scan_flash
        if flash is None:
            return
        try:
            await flash.play(lambda: gen != self._scan_gen)
        except Exception as e:
            logger.error("scan flash failed: %s", e)

    def _navigate(self, page_name: str) -> None:
        if page_name == self._active_page:
            return
        self._active_page = page_name
        if self._sidebar_host is not None:
            self._sidebar_host.content = self._build_sidebar()
        self._render_active_page()
        self._safe_update()

    def _render_active_page(self) -> None:
        if self._page_host is None:
            return
        if self._active_page == "network":
            self._page_host.content = build_network_page(
                self.pstore.list_proxies(),
                on_add=lambda _: self._open_proxy_dialog(),
                on_edit=self._edit_proxy,
                on_delete=self._delete_proxy,
                on_check=self._check_proxy,
                on_rotate=self._rotate_proxy,
                checking=self._checking_proxies,
            )
        elif self._active_page == "bookmarks":
            self._page_host.content = build_bookmarks_page(
                self.bstore.list_bookmarks(),
                self.bstore.list_pools(),
                on_add_bookmark=lambda _: self._open_bookmark_dialog(),
                on_edit_bookmark=self._edit_bookmark,
                on_delete_bookmark=self._delete_bookmark,
                on_make_pool=self._make_pool_from,
                on_edit_pool=self._edit_pool,
                on_delete_pool=self._delete_pool,
            )
        elif self._active_page == "connect":
            from ..api.mcp_config import claude_add_command, client_config_json, mcp_url
            from ..api.mcp_token import get_or_create_token
            tok = get_or_create_token()
            self._page_host.content = build_connect_page(
                self.pm.list_profiles(),
                token=tok,
                add_command=claude_add_command(tok),
                config_json=client_config_json(tok),
                on_toggle_ai=self._toggle_ai,
                # Reflect the user's INTENT (the saved setting), not the raw thread
                # liveness. uvicorn stops asynchronously (stop() only sets
                # should_exit), so is_running lagged a click behind and the toggle
                # needed pressing twice to show "disabled".
                server_running=app_settings.is_server_enabled(),
                on_toggle_server=self._set_server,
                endpoint=mcp_url(),
                ssh_hosts=self.ssh_store.list(),
                on_ssh_add=lambda: self._open_ssh_host_dialog(),
                on_ssh_edit=self._edit_ssh_host,
                on_ssh_delete=self._delete_ssh_host,
                on_ssh_run=self._ssh_run,
            )
        elif self._active_page == "tags":
            self._page_host.content = build_tags_page(
                self.pm.list_profiles(),
                on_assign=self._assign_tag,
                on_remove_tag=self._remove_tag,
            )
        elif self._active_page == "certificates":
            self._page_host.content = build_certificates_page(
                self.cert_store.list(),
                on_add=lambda _: self._open_certificate_dialog(),
                on_edit=self._edit_certificate,
                on_delete=self._delete_certificate,
            )
        elif self._active_page == "trash":
            self._page_host.content = build_trash_page(
                self.trash_service.list(),
                on_restore=self._restore_from_trash,
                on_delete_permanently=self._delete_from_trash_permanently,
                on_empty=self._empty_trash,
            )
        else:
            self._page_host.content = self._build_profiles_page()

    # --- Trash ---

    def _restore_from_trash(self, entry_id: str) -> None:
        entry = self.trash_service.get(entry_id)
        ok, msg = self.trash_service.restore(entry_id)
        if ok and entry is not None:
            self._log(f"restored {entry.label}: {entry.name}")
        elif not ok:
            # A refused restore is explained, never silently turned into a
            # rename: a profile's fingerprint derives from its name, so restoring
            # under another name would hand back its cookies under a different
            # identity.
            self._log(f"restore failed: {msg}")
            page = self.page
            if page is not None:
                open_confirm_dialog(
                    page,
                    "",
                    lambda: None,
                    title="Cannot restore",
                    body=msg,
                )
        self._refresh_profiles()
        self._render_active_page()
        # A restored entry is no longer counting down, so the rail's badge is
        # stale the instant this returns. Rebuilt through the SAME path
        # navigation and the engine rows already use — no timer, no poll.
        self._refresh_sidebar()
        self._safe_update()

    def _delete_from_trash_permanently(self, entry_id: str) -> None:
        page = self.page
        assert page is not None
        entry = self.trash_service.get(entry_id)
        if entry is None:
            return

        def do_delete() -> None:
            ok, _ = self.trash_service.delete_permanently(entry_id)
            if ok:
                self._log(f"permanently deleted {entry.label}: {entry.name}")
            self._render_active_page()
            self._refresh_sidebar()
            self._safe_update()

        # This dialog DOES claim irreversibility, because this path really is
        # irreversible — unlike the ordinary delete, which no longer claims it.
        extra = (
            " Its stored credentials are removed from disk."
            if entry.holds_secret_material
            else ""
        )
        open_confirm_dialog(
            page,
            entry.name,
            do_delete,
            title=f"Permanently delete {entry.label} '{entry.name}'?",
            body=(
                "This deletes it and its data for good. This action cannot be "
                "undone." + extra
            ),
        )

    def _empty_trash(self) -> None:
        page = self.page
        assert page is not None
        count = len(self.trash_service.list())
        if not count:
            return

        def do_empty() -> None:
            deleted = self.trash_service.empty()
            self._log(f"emptied trash ({deleted} item(s))")
            self._render_active_page()
            self._refresh_sidebar()
            self._safe_update()

        open_confirm_dialog(
            page,
            "",
            do_empty,
            title=f"Permanently delete {count} item{'s' if count != 1 else ''}?",
            body=(
                "Everything in the trash, and all its data — including stored "
                "credentials and certificate key bundles — is deleted for good. "
                "This action cannot be undone."
            ),
        )

    def _ssh_run(self, host_name: str, command: str) -> tuple[int, str, str]:
        from ..services.ssh import client as ssh
        from ..services.ssh.resolver import target_for

        from ..services.proxy.errors import ProxyUnresolvedError

        host = self.ssh_store.get(host_name)
        if host is None:
            return 1, "", f"host {host_name!r} not found"
        try:
            target = target_for(host, self.pm, self.pstore)
        except ProxyUnresolvedError as e:
            # Fail closed: refuse to connect DIRECT from the real IP.
            return 1, "", str(e)
        return ssh.run_command(target, command)

    def _open_ssh_host_dialog(self, name: str | None = None) -> None:
        from .dialogs.ssh_host import open_ssh_host_dialog

        host = self.ssh_store.get(name) if name else None
        profile_names = [p.name for p in self.pm.list_profiles()]

        def on_save(h) -> str | None:
            if name:
                if not self.ssh_store.update(name, h):
                    return "Update failed (name conflict?)"
            else:
                if not self.ssh_store.add(h):
                    return "Host name already exists"
            self._render_active_page()
            self._safe_update()
            return None

        open_ssh_host_dialog(self.page, host, profile_names, on_save)

    def _edit_ssh_host(self, name: str) -> None:
        self._open_ssh_host_dialog(name)

    def _delete_ssh_host(self, name: str) -> None:
        self.ssh_store.remove(name)
        self._render_active_page()
        self._safe_update()

    def _open_certificate_dialog(self, name: str | None = None) -> None:
        from ..services.cert.store import Certificate
        from .dialogs.certificate import open_certificate_dialog

        existing = self.cert_store.get(name) if name else None

        def on_save(c: Certificate) -> str | None:
            # A newly picked file lives outside persona; copy it into the store
            # so the certificate lives with the rest of persona's data. An edit
            # that kept the existing file already points inside the store.
            stored_path = c.p12_path
            if not stored_path.startswith(self._certs_dir()):
                try:
                    stored_path = self.cert_store.import_p12(c.name, c.p12_path)
                except OSError as e:
                    return f"could not store certificate file: {e}"
            # Rebuild from ALL of c's fields (overriding only the stored path), so
            # a hand-enumerated constructor can't silently drop one — the url was
            # being omitted here, so mTLS persisted DISABLED (start_terminator
            # bails on an empty url) on every add/edit, with data loss (audit7 #4).
            import dataclasses

            saved = dataclasses.replace(c, p12_path=stored_path)
            if name:
                if not self.cert_store.update(name, saved):
                    return "Update failed (name conflict?)"
            else:
                if not self.cert_store.add(saved):
                    return "Certificate name already exists"
            self._render_active_page()
            self._safe_update()
            return None

        open_certificate_dialog(
            self.page, existing, self.refs.file_picker, on_save
        )

    def _certs_dir(self) -> str:
        from ..services.cert.store import _certs_dir

        return _certs_dir()

    def _edit_certificate(self, name: str) -> None:
        self._open_certificate_dialog(name)

    def _delete_certificate(self, name: str) -> None:
        page = self.page
        assert page is not None

        def do_delete() -> None:
            self.cert_store.remove(name)
            self._render_active_page()
            self._safe_update()

        open_confirm_dialog(
            page,
            name,
            do_delete,
            title=f"Delete certificate '{name}'?",
            body="Any profile assigned this certificate will launch without a "
            "client certificate until you reassign one.",
        )

    def _build_profiles_page(self) -> ft.Container:
        r = self.refs
        assert r is not None
        search_field = ft.TextField(
            value=self._search_query,
            on_change=self._on_search,
            hint_text="search profiles...",
            width=220,
            height=40,
            border_radius=3,
            bgcolor=COLORS["input_bg"],
            color=COLORS["text_main"],
            border_color=COLORS["card_border"],
            focused_border_color=COLORS["accent"],
            text_style=ft.TextStyle(font_family="monospace", size=13),
            hint_style=ft.TextStyle(font_family="monospace", size=13),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )
        top_bar = build_top_bar(
            self.count_text,
            search_field,
            on_new=lambda _: self.h.open_add_dialog(),
            on_import=self.h.on_import,
            on_export=lambda _: self.h.on_export_open(),
            on_wipe=lambda _: self._on_wipe_all(),
        )
        content = build_content_area(
            r.content_subtitle,
            r.profile_list_area,
            r.prev_btn,
            r.next_btn,
            r.page_label,
            r.bulk_bar,
        )
        return ft.Container(
            expand=True,
            bgcolor=COLORS["bg"],
            padding=ft.Padding.symmetric(horizontal=32, vertical=24),
            content=ft.Column(
                spacing=0,
                expand=True,
                controls=[top_bar, self._build_tag_chips(), content],
            ),
        )

    def _tag_chips_content(self) -> ft.Control | None:
        """The row of tag-filter chips, or None when there are no tags. Built
        from the CURRENT profiles so a tag added after startup shows up."""
        tags = all_tags(self.pm.list_profiles())
        if not tags:
            return None
        chips: list[ft.Control] = []
        for tag in tags:
            active = self._active_tag.lower() == tag.lower()
            chips.append(
                ft.Container(
                    on_click=lambda _, tg=tag: self._toggle_tag_filter(tg),
                    ink=True,
                    border_radius=3,
                    border=ft.Border.all(1, COLORS["accent"] if active else COLORS["card_border"]),
                    bgcolor=COLORS["accent"] if active else COLORS["card_bg"],
                    padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                    content=ft.Text(
                        tag,
                        size=12,
                        color=COLORS["bg"] if active else COLORS["text_sub"],
                        font_family="monospace",
                    ),
                )
            )
        return ft.Row(spacing=8, wrap=True, controls=chips)

    def _build_tag_chips(self) -> ft.Control:
        # A live container so the chip row can be refreshed when a profile with a
        # new tag is created after startup (a fixed build meant chips only ever
        # showed the tags that existed when the layout was first built).
        content = self._tag_chips_content()
        self._tag_chips_row = ft.Container(
            padding=ft.Padding.only(bottom=12 if content is not None else 0),
            content=content,
        )
        return self._tag_chips_row

    def _refresh_tag_chips(self) -> None:
        row = getattr(self, "_tag_chips_row", None)
        if row is None:
            return
        content = self._tag_chips_content()
        row.content = content
        row.padding = ft.Padding.only(bottom=12 if content is not None else 0)

    def _toggle_tag_filter(self, tag: str) -> None:
        if self._active_tag.lower() == tag.lower():
            self._active_tag = ""
        else:
            self._active_tag = tag
        self.state.current_page = 1
        self._render_active_page()
        self._refresh_profiles()
        self._safe_update()

    def _toggle_ai(self, name: str, enabled: bool) -> None:
        # The checkbox flips itself in place (see connect_page._checkbox_toggle),
        # so DON'T rebuild the connect page here — a full rebuild reset the
        # scroll position to the top on every toggle. Just persist + log.
        self.pm.set_ai_control(name, enabled)
        state = "enabled" if enabled else "disabled"
        self._log(f"AI control {state} for '{name}'")

    def _save_notes_inline(self, name: str, notes: str) -> None:
        """Save a profile's notes edited inline on the card (no dialog)."""
        self.pm.set_notes(name, notes)

    def _engine2_version_text(self) -> str:
        from ..services.browser.invisible_launch import (
            installed_version,
            is_invisible_installed,
        )

        if not is_invisible_installed():
            return "not installed"
        return installed_version() or "installed"

    def _engine2_update_available(self) -> bool:
        from ..services.browser.invisible_launch import (
            is_invisible_installed,
            pinned_build,
        )
        from ..services.engine import firefox as ff_engine

        if not self._engine2_compatible or not is_invisible_installed():
            return False
        # A deliberate revert is a standing "not this build". This is the one
        # place "is there an update to offer?" is decided, so the pin belongs
        # here rather than only on the unattended path: the row's text, its
        # update dot and the click that starts a download all read this.
        #
        # Offering an update while pinned is worse than merely noisy. The
        # build being advertised is ALREADY UNPACKED ON DISK (the pin is what
        # kept it there), so the click would re-download hundreds of megabytes
        # over Tor to arrive at a directory that already exists — and then
        # leave the pin, and so the launched build, exactly as it was. The
        # bytes move, the success line prints, and the operator's goal does
        # not happen. Going forward again is the "resume updates" gesture on
        # the rollback row, which is instant and moves nothing.
        if pinned_build():
            return False
        return ff_engine.is_newer(self._engine2_latest, ff_engine.current_version())

    def _engine2_status_text(self) -> str:
        if self._engine2_checking:
            return "checking..."
        if self._engine2_status:
            return self._engine2_status
        if self._engine2_update_available():
            # No "update →" prose, and shortened at the source — the same
            # treatment the Chromium row already had, for the same reasons.
            #
            # The prose is redundant EXACTLY WHEN IT IS MOST EXPENSIVE: this
            # branch is the one condition under which the accent dot is drawn
            # (the row passes dot=self._engine2_update_available()), so the
            # words say what the dot beside them is already saying. Nine of
            # the cell's ~17 characters went on them, and what got ellipsised
            # away to pay for it was the build number — the only thing the
            # line exists to tell anyone. Firefox tags are long:
            #
            #   "update → firefox-20_151.0_20260817150018"  (40) → "update → firefox…"
            #   "firefox-20_151.0_20260817150018"           (31) → "firefox-20_151.0…"
            #
            # The second keeps the build (20) and the upstream version legible.
            # The trailing "_20260817150018" is then reachable from NOWHERE —
            # not this row's tooltip (a static gesture string) and not the
            # reveal chevron, because shortening caps the value AT
            # _VERSION_MAX_CHARS while _status_needs_reveal fires only ABOVE
            # it. _engine_row's docstring works that arithmetic through, and
            # test_a_shortened_version_line_draws_no_reveal_so_its_tail_is_
            # unreachable pins the consequence. Accepted in review (PS-229):
            # the build and upstream version are what the line is for, and a
            # reveal on a whole line is the affordance item 6 forbids.
            return _short_engine_version(self._engine2_latest)
        return self._engine2_version_text()

    def _engine2_rollback_row(self) -> ft.Control:
        """The undo gesture for a bad engine update, and its way back.

        Three states, and only one is ever shown:
          * PINNED — the operator already went back. Offer "resume updates",
            because a pin holds the automatic update off and there must be a
            way out of that state from the same place they entered it.
          * a retained build exists — offer "go back to <build>".
          * nothing retained — render nothing at all. A revert with no retained
            build cannot work, and a button that cannot work is worse than no
            button: it promises the machine can undo something it cannot.
        """
        from ..services.browser import invisible_launch as inv

        try:
            pin = inv.pinned_build()
            target = "" if pin else inv.rollback_target()
        except Exception:
            # The panel must render even if the engine cache is unreadable.
            return ft.Container(height=0)

        # THE BUILD IDENTIFIER LIVES IN THE TOOLTIP, NOT THE LABEL. A Firefox
        # build reads `firefox-20_151.0_20260817150018` — 30 characters into a
        # rail with room for about 22 — and interpolating it into the visible
        # text is what pushed this row past the panel's edge. The tooltip named
        # it already, so nothing is lost by taking it out of the label.
        #
        # THE COST STAYS ON SCREEN. Firefox's revert moves NO BYTES: both
        # builds are already unpacked in versioned cache dirs, so the retained
        # one is on disk and switching to it is instant. That is the half of
        # PS-79's trade that belongs to this engine, and it must survive the
        # shortening — see rollback_row for why cost and identity part company.
        if pin:
            return rollback_row(
                label=_RESUME_LABEL,
                icon=ft.Icons.PLAY_ARROW,
                # THE SECOND LINE SAYS THE STATE, NOT THE GESTURE. "resume
                # updates / updates resume" is a stutter that costs a line and
                # tells the operator nothing. What they cannot see from the
                # label is WHY the row is offering this at all: the pin is
                # holding automatic updates off right now.
                cost="auto-update held off",
                tooltip=(
                    f"Clear the pin (currently held at {pin}) and let the "
                    "Firefox engine update again"
                ),
                on_click=self._on_engine2_resume,
            )
        if target:
            return rollback_row(
                label=_ROLLBACK_LABEL,
                icon=ft.Icons.HISTORY,
                cost="instant · no download",
                tooltip=(
                    f"Go back to the retained {target} build. Firefox keeps "
                    "each build in its own cache directory, so this one is "
                    "already on disk — nothing is downloaded."
                ),
                on_click=self._on_engine2_rollback,
            )
        return ft.Container(height=0)

    def _on_engine2_rollback(self) -> None:
        """Go back to the retained previous build. Instant: both builds are
        already on disk, so nothing is downloaded and there is no progress bar
        to show. Refused when a profile is running or nothing is retained —
        the service call owns that decision.

        A REFUSAL MUST BE VISIBLE ON THE ROW, not only in the log. This gesture
        has no progress bar and completes in milliseconds, so a refused revert
        is indistinguishable from a dead button: the operator clicks "go back
        to firefox-19", nothing moves, and the only explanation is a log line
        they may not have open. The status line is the one place they are
        already looking."""
        if self._engine2_busy or self._engine2_checking:
            return
        from ..services.browser import invisible_launch as inv

        try:
            went = inv.revert_to_previous_build(log=self._log)
        except Exception as e:
            self._log(f"Firefox engine: going back failed ({e})")
            self._engine2_status = "couldn't go back — see the log"
            self._refresh_engine_text()
            self._refresh_sidebar()
            return
        if went:
            self._engine2_status = ""
        else:
            # Re-derive WHICH refusal it was rather than restating the service's
            # decision: a running profile is the case an operator can actually
            # act on ("close them and click again"), and it is the common one.
            try:
                retained = bool(inv.rollback_target())
            except Exception:
                retained = False
            self._engine2_status = (
                "close your profiles to go back"
                if retained
                else "nothing to go back to"
            )
        self._refresh_engine_text()
        self._refresh_sidebar()

    def _on_engine2_resume(self) -> None:
        """Clear the pin and resume automatic updates."""
        if self._engine2_busy or self._engine2_checking:
            return
        from ..services.browser import invisible_launch as inv

        try:
            inv.resume_engine_updates(log=self._log)
        except Exception as e:
            self._log(f"Firefox engine: couldn't resume updates ({e})")
            return
        self._engine2_status = ""
        self._refresh_engine_text()
        self._refresh_sidebar()

    def _engine_rollback_row(self) -> ft.Control:
        """The undo gesture for a bad CHROMIUM update, and its way back.

        Four states, and only one is ever shown — the same shape as
        _engine2_rollback_row next door, deliberately, because the two engines
        must not describe the same situation differently:
          * PINNED — the operator already went back. Offer "resume updates",
            because a pin holds the automatic update off and there must be a
            way out of that state from the same place they entered it.
          * a previous build is RECORDED — offer "go back to <tag>".
          * an engine IS INSTALLED but nothing is recorded for it — say so, as
            a plain line with no gesture on it. See below.
          * no engine at all — render nothing. There is no update to undo.

        WHY THE THIRD STATE EXISTS (PS-172). The rule "a button that cannot work
        is worse than no button" is still right and is NOT overturned here: this
        state offers no button. But an empty space is a claim too, and it was
        read as the wrong one. The owner installed v3.0.0, saw Firefox's revert
        and nothing here, and concluded the capability was Firefox-only — it
        wasn't, it simply had nothing to point at yet. A sentence explaining why
        there is no button beats both a broken button and silence.

        Chromium's retention is a RECORD (the "previous" entry in builds.json,
        written on a swap) while Firefox's is the FILESYSTEM (versioned cache
        dirs that are themselves the retention). So a Chromium machine that
        upgraded INTO v3.0.0 with an engine already present has a version.txt
        and no record — ensure_engine short-circuits on is_installed() and never
        records — and gets no target from its first bump. That is the population
        this line is for, and on 25 Aug it is nearly every reporting operator.

        THE EXPOSURE IS A FIXED NUMBER OF BUMPS AND THEN SELF-HEALS, and the
        number is NOT the same for both machines — which is why the message is
        chosen by _engine_rollback_pending_row rather than being one constant.
        A machine with `current` recorded is one bump from a rollback target; a
        machine with nothing recorded is two, because its next swap has nothing
        to demote and only records what it installs. Both are stated literally:
        the wording names when the gesture starts working, and is not a
        euphemism for later.

        THE "no engine" CASE STAYS SILENT and is not folded in with it. Before
        the engine is installed there is no update to go back FROM, so promising
        a rollback "after the next update" would be answering a question the
        operator has not asked yet — and this row renders during the fresh
        download, where that line would be pure noise.

        THE ONE PLACE THIS DIFFERS FROM THE FIREFOX ROW IS THE TOOLTIP, and the
        difference is the whole of PS-79's trade. Firefox's revert moves no
        bytes — both builds are already unpacked in versioned cache dirs. This
        one RE-DOWNLOADS, because Chromium keeps a single un-versioned tree and
        the previous build's files are genuinely gone. Saying so on the control
        is not a detail: an operator on Tor deserves to know the gesture costs
        hundreds of megabytes before they click it, not after.
        """
        try:
            pin = engine.pinned_build()
            target = "" if pin else engine.rollback_target()[0]
        except Exception as e:
            # The panel must render even if the record/settings are unreadable.
            # LOG THE REASON rather than only degrading: unlike the Firefox row
            # this handler also covers `settings` being unreadable, and a
            # corrupt settings file therefore takes the RESUME gesture away
            # silently — leaving an operator pinned with no way back out of the
            # pin, and nothing on screen saying why the control vanished.
            self._log(f"Chromium engine: couldn't read the rollback state ({e})")
            return ft.Container(height=0)

        # THE BUILD IDENTIFIER LIVES IN THE TOOLTIP, NOT THE LABEL — the same
        # relocation as the Firefox row next door, for the same reason, and the
        # two engines must not describe the same situation differently.
        #
        # THE COST STAYS ON SCREEN, AND HERE IT IS THE EXPENSIVE ONE. Chromium
        # keeps ONE un-versioned tree, so the previous build's files are
        # genuinely gone and going back RE-DOWNLOADS the whole engine, over
        # Tor. Firefox's revert next door moves no bytes at all. That asymmetry
        # is PS-79's trade, it is the single most consequential thing this row
        # can tell an operator, and it is exactly what a one-word label would
        # have hidden. It is carried in the WARNING colour because it is a
        # warning: the other engine's line is not.
        #
        # THE COST IS STATED WITHOUT A SIZE, DELIBERATELY — see the WHY below.
        if pin:
            return rollback_row(
                label=_RESUME_LABEL,
                icon=ft.Icons.PLAY_ARROW,
                # Says the STATE, not the gesture — same reasoning as the
                # Firefox row next door, and the two engines must not describe
                # the same situation differently.
                cost="auto-update held off",
                tooltip=(
                    f"Clear the pin (currently held at {pin}) and let the "
                    "Chromium engine update again"
                ),
                on_click=self._on_engine_resume,
            )
        if target:
            return rollback_row(
                label=_ROLLBACK_LABEL,
                icon=ft.Icons.HISTORY,
                # NO SIZE IS QUOTED, AND THAT IS A CORRECTION TO THE RENDER.
                # The render's label read "downloads 300-600MB". That figure
                # was checked against the code before shipping it and it is
                # NOT a value this product knows: the three occurrences of
                # "300-600MB" in the tree are all PROSE COMMENTS in
                # engine/updater.py, arguing about whether to keep a second
                # engine tree on disk (lines 52, 651, 1175). No constant, no
                # field, no service call carries it. The only real total is
                # the Content-Length/Content-Range that httpdl.resumable_
                # download learns AT TRANSFER TIME — i.e. after the operator
                # has already clicked, which is exactly too late to be a
                # warning.
                #
                # So the number would have been an unsourced claim presented
                # as measured, on a line an operator on Tor budgets an
                # afternoon against. Per the planner's ruling on this ticket:
                # the cost warning survives, only the fabricated precision
                # goes. Inventing a substitute range would be manufacturing
                # the very thing the ticket forbids.
                #
                # THE VERB STAYS SHORT FOR THE SAME REASON THE NUMBER WENT.
                # "re-downloads the engine" is 23 characters against a rail
                # that holds about 22, so it would ellipsise — and a cost line
                # cut off mid-word is the overflow defect this panel is being
                # rebuilt to remove, re-introduced by the fix for a different
                # one. The render had already dropped the "re-" prefix under
                # the same budget. "downloads" is 20 and fits; the tooltip
                # below still says "re-downloads the whole engine" in full, so
                # the precision is relocated rather than lost.
                #
                # WHAT MUST SURVIVE, AND DOES: the Chromium-vs-Firefox
                # asymmetry. This line says bytes MOVE; the Firefox row next
                # door says it is instant and downloads nothing. An operator
                # on a slow or metered link still learns which of the two they
                # are about to trigger, which is the whole purpose of the line.
                cost="downloads the engine",
                cost_color=COLORS["warning"],
                tooltip=(
                    f"Go back to {target}. Chromium keeps one engine build at "
                    "a time, so the previous build's files are gone and this "
                    "re-downloads the whole engine, over Tor, and replaces "
                    "the engine you have now."
                ),
                on_click=self._on_engine_rollback,
            )
        # No target — but WHICH of the three "no target" states is this?
        # is_installed() swallows its own OSError and answers False, so it
        # needs no guard of its own, and False is the safe direction: it
        # renders nothing, exactly as this row did before the state existed.
        if not engine.is_installed():
            return ft.Container(height=0)
        try:
            recorded = engine.current_build_recorded()
        except Exception as e:
            # Same fail-quiet direction as above: an unreadable record must
            # not promise a specific number of updates. Log and say nothing.
            self._log(f"Chromium engine: couldn't read the build record ({e})")
            return ft.Container(height=0)
        return self._engine_rollback_pending_row(recorded)

    def _engine_rollback_pending_row(self, recorded: bool) -> ft.Control:
        """The line an operator sees when the engine is installed but nothing is
        recorded to go back TO — Chromium only.

        NOT A BUTTON AND NOT A DISABLED BUTTON. It is a statement, and it must
        not look clickable: `on_click`, `ink` and the HISTORY icon are all
        deliberately absent, because the whole complaint this closes was a
        gesture that appeared to exist and did not. A greyed-out control invites
        the same click and answers it with nothing.

        `recorded` IS engine.current_build_recorded(), AND IT CHANGES THE COUNT,
        which is why this takes an argument instead of asking one question. Both
        machines below have an engine and an empty rollback_target(), and the
        number of updates until the gesture appears is NOT the same:
          * recorded=True — a clean install. "current" names the build on disk,
            so the NEXT swap demotes it: ONE update.
          * recorded=False — upgraded into v3.0.0 with an engine already there.
            ensure_engine short-circuited on is_installed() and wrote nothing,
            so the next swap has nothing to demote and records only what it
            installs; the swap AFTER that is the first reversible one. TWO.

        SAYING "the next update" TO THE SECOND MACHINE WOULD BE A NEW BROKEN
        PROMISE — the operator watches an update land, looks here, and finds the
        same emptiness plus a sentence that just proved itself wrong. That is
        the defect this row exists to close, re-committed in words. The second
        machine is also the LARGER population: PS-79's record shipped in v3.0.0,
        so on 25 Aug essentially every operator who upgraded is in it. The
        counts are measured against record_installed_build, not reasoned from
        its docstring — see tests/test_engine_retention_origin.py.

        THE WORDING CARRIES THE BOUND, not just the fact — the gap is a fixed
        number of update cycles, not a permanent state, and an operator who
        reads "the next update"/"the next two" knows it ends. That is the
        difference between an explanation and an apology.

        Sized and coloured as the row it replaces (size 10, text_dim, monospace,
        the same left inset) so the panel's rhythm is unchanged whichever state
        is showing — this line sits exactly where the button would.
        """
        if recorded:
            label = "rollback available after the next engine update"
            tip = (
                "Chromium keeps one engine build at a time, so going back needs "
                "the build it replaced to be recorded first. The build you have "
                "now is recorded, so the next update leaves you a way back to it."
            )
        else:
            label = "rollback available after the next two engine updates"
            tip = (
                "Chromium keeps one engine build at a time, so going back needs "
                "the build it replaced to be recorded first. This engine was "
                "installed before that record existed, so the next update has "
                "nothing to record — the one after it does, and from then on "
                "you can go back."
            )

        return ft.Container(
            padding=ft.Padding.only(left=36, right=10, top=4, bottom=2),
            tooltip=tip,
            content=ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(
                        ft.Icons.INFO_OUTLINE, size=13, color=COLORS["text_dim"]
                    ),
                    sidebar_status_text(label),
                ],
            ),
        )

    def _on_engine_rollback(self) -> None:
        """Go back to the previous Chromium build by re-downloading it.

        UNLIKE THE FIREFOX REVERT THIS IS A DOWNLOAD, so it runs on a background
        thread with the row's progress bar, exactly as an update does — running
        it inline would freeze the UI for minutes. It also claims _engine_busy
        the same way _update_engine_async does, so a revert and an update can
        never race into ENGINE_DIR together.

        A REFUSAL MUST BE VISIBLE ON THE ROW, not only in the log — the operator
        is looking at the status line, not necessarily at the log. The service
        owns every refusal decision and its wording; this only renders it.
        """
        if self._engine_busy or self._engine_checking:
            return
        self._engine_busy = True
        # ARM THE BAR BEFORE ANY BYTE MOVES, exactly as both sibling download
        # paths do (_download_engine_fresh, _update_engine_async). Without this
        # the revert inherits the LAST download's finished state: ProgressState
        # is monotonic (progress_fmt.py `self.done = max(self.done, done)`) and
        # resets only when `total` CHANGES, and the builds either side of a
        # revert are
        # sibling Chromium releases of near-identical size — so the reset branch
        # usually never fires and the row sits pinned at "100% 189.0 MB of
        # 189.0 MB" for the whole multi-minute re-download. When the server
        # omits Content-Length (common over Tor, which is this row's operator)
        # total is 0, never satisfies the reset condition under ANY
        # circumstances, and the stale line is frozen there permanently.
        # It also fixes what the operator sees AT THE CLICK: _refresh_sidebar()
        # below re-inserts the bar/detail controls still holding the previous
        # download's values, so a full 100% bar appeared instantly — reading as
        # "already done" for a gesture that had not moved a byte.
        self._engine_progress_start()
        self._engine_status = ""
        self._refresh_engine_text()
        self._refresh_sidebar()

        def work() -> None:
            # _engine_busy cleared in finally so a raise can't wedge it True and
            # dead-end every later engine action this session.
            try:
                ok, message = engine.revert_to_previous_build(
                    progress=self._engine_progress_cb, log=self._log
                )
                if ok:
                    # current_version() is now the restored build, so the row's
                    # own text is correct again; clear any stale status over it.
                    self._engine_status = ""
                    # The build we just left is the recorded "previous" now, and
                    # _engine_latest still names it — but the pin means no
                    # update is offered until the operator resumes. Leave the
                    # refusal/deferral trackers alone: they are keyed by tag and
                    # a revert does not resolve them.
                else:
                    # The service's message is the operator-facing one; the row
                    # gets a short form of the same thing rather than a second
                    # vocabulary for the same outcome.
                    self._engine_status = (
                        "couldn't go back — see the log"
                        if message
                        else "nothing to go back to"
                    )
            except Exception as e:
                self._log(f"Chromium engine: going back failed ({e})")
                self._engine_status = "couldn't go back — see the log"
            finally:
                # Clear the detail line here, mirroring _update_engine_async's
                # finally. It matters MOST on the refusal paths (nothing to go
                # back to / close your running profiles / the yanked-tag
                # message), which are the likeliest outcomes and all return in
                # milliseconds having moved no bytes — without this they leave
                # the previous download's byte count sitting under a revert that
                # never started.
                self._engine_busy = False
                self._engine_detail.value = ""
                self._refresh_engine_text()
                self._refresh_sidebar()

        threading.Thread(target=work, daemon=True).start()

    def _on_engine_resume(self) -> None:
        """Clear the Chromium pin and resume automatic updates.

        Instant and moves nothing — but note the asymmetry with the Firefox
        resume: the build reverted FROM is not on disk any more, so going
        forward is an ordinary download on the next check rather than a change
        of which tree launches. That is the cost of not keeping a second engine.
        """
        if self._engine_busy or self._engine_checking:
            return
        try:
            engine.resume_engine_updates(log=self._log)
        except Exception as e:
            self._log(f"Chromium engine: couldn't resume updates ({e})")
            return
        self._engine_status = ""
        self._refresh_engine_text()
        self._refresh_sidebar()

    def _assign_tag(self, names: list[str], tag: str) -> None:
        n = self.pm.assign_tag(names, tag)
        if n:
            self._log(f"Tagged {n} profile(s) with '{tag.strip()}'")
        self._render_active_page()
        self._refresh_profiles()
        self._safe_update()

    def _remove_tag(self, tag: str) -> None:
        n = self.pm.remove_tag(tag)
        if n:
            self._log(f"Removed tag '{tag}' from {n} profile(s)")
        if self._active_tag.lower() == tag.lower():
            self._active_tag = ""
        self._render_active_page()
        self._refresh_profiles()
        self._safe_update()

    def _goto_add_proxy(self) -> None:
        """Jump from the profile dialog to the network page with the add-proxy
        dialog already open."""
        self._navigate("network")
        self._open_proxy_dialog()

    def _open_proxy_dialog(self, name: str | None = None) -> None:
        page = self.page
        assert page is not None
        existing = self.pstore.get(name) if name else None

        def on_save(new_name: str, new_url: str, new_rotate_url: str) -> str | None:
            if existing is None:
                if not self.pstore.add(new_name, new_url, new_rotate_url):
                    return "Proxy name already exists"
            else:
                if not self.pstore.update(
                    existing.name, new_name, new_url, new_rotate_url
                ):
                    return "Proxy name already exists"
            self._render_active_page()
            self._safe_update()
            return None

        def on_checked(
            proxy_name: str,
            code: str,
            country: str,
            ip: str,
            tz: str,
            lat: float | None = None,
            lon: float | None = None,
        ) -> None:
            self.pstore.mark_checked(
                proxy_name, code, country, ip, tz, lat, lon
            )

        def on_check_failed(proxy_name: str) -> None:
            self.pstore.mark_check_failed(proxy_name)

        open_proxy_dialog(
            page,
            self.ps,
            on_save,
            existing,
            on_checked=on_checked,
            on_check_failed=on_check_failed,
            ui=self._ui,
        )

    def _edit_proxy(self, name: str) -> None:
        self._open_proxy_dialog(name)

    def _delete_proxy(self, name: str) -> None:
        page = self.page
        assert page is not None
        in_use = [
            p.name for p in self.pm.list_profiles() if p.proxy == name
        ]

        def do_delete() -> None:
            # pstore.delete owns the whole operation: it records which profiles
            # used the proxy and THEN drops the reference from each of them. Do
            # not clear the references here — doing so first is exactly the bug
            # that made a restored proxy come back with nothing pointing at it.
            self.pstore.delete(name)
            self._render_active_page()
            self._safe_update()

        if in_use:
            shown = ", ".join(in_use[:5])
            more = f" and {len(in_use) - 5} more" if len(in_use) > 5 else ""
            body = (
                f"{len(in_use)} profile(s) use it ({shown}{more}); "
                "they will fall back to a direct connection."
            )
        else:
            body = "No profiles use this proxy."

        open_confirm_dialog(
            page,
            name,
            do_delete,
            title=f"Delete proxy '{name}'?",
            body=body,
        )

    def _refresh_proxy_views(self) -> None:
        # the flag/spinner lives on both the network page and the
        # profile cards; refresh whichever is active.
        def apply() -> None:
            if self._active_page == "profiles":
                self._refresh_profiles()
            else:
                self._render_active_page()
            self._safe_update()

        self._ui(apply)

    def _check_proxy(self, name: str) -> None:
        proxy = self.pstore.get(name)
        if proxy is None or name in self._checking_proxies:
            return
        self._checking_proxies.add(name)
        self._refresh_proxy_views()

        def do_check() -> None:
            try:
                ok, message, code, country, ip, tz, lat, lon = (
                    self.ps.check_proxy_detailed_sync(proxy.url)
                )
                self._log(f"[{name}] {message}")
                if ok:
                    self.pstore.mark_checked(
                        name, code, country, ip, tz, lat, lon
                    )
                else:
                    self.pstore.mark_check_failed(name)
            finally:
                self._checking_proxies.discard(name)
                self._refresh_proxy_views()

        threading.Thread(target=do_check, daemon=True).start()

    def _rotate_proxy(self, name: str) -> None:
        proxy = self.pstore.get(name)
        if proxy is None or name in self._checking_proxies:
            return
        self._checking_proxies.add(name)
        self._refresh_proxy_views()

        def do_rotate() -> None:
            try:
                old_ip = proxy.last_ip
                url, note = self.ps.rotate_proxy(proxy.url, proxy.rotate_url)
                if note:
                    self._log(f"[{name}] {note}")
                if url != proxy.url:
                    self.pstore.set_url(name, url)
                ok, message, code, country, ip, tz, lat, lon = (
                    self.ps.check_proxy_detailed_sync(url)
                )
                if ok:
                    self.pstore.mark_checked(name, code, country, ip, tz, lat, lon)
                    # Never print the exit IP to the disk-backed activity log —
                    # a timestamped IP history de-anonymizes the operator. Report
                    # only whether the exit changed.
                    if old_ip and ip == old_ip:
                        self._log(
                            f"Proxy {name}: exit unchanged — "
                            "this proxy may be static or sticky"
                        )
                    else:
                        self._log(f"Proxy {name}: rotated to a new exit")
                else:
                    self.pstore.mark_check_failed(name)
                    self._log(f"[{name}] {message}")
            finally:
                self._checking_proxies.discard(name)
                self._refresh_proxy_views()

        threading.Thread(target=do_rotate, daemon=True).start()

    # --- Bookmarks ---

    def _open_bookmark_dialog(self, name: str | None = None) -> None:
        page = self.page
        assert page is not None
        existing = self.bstore.get(name) if name else None

        def on_save(new_name: str, new_url: str) -> str | None:
            if existing is None:
                if not self.bstore.add(new_name, new_url):
                    return "Bookmark name already exists"
            else:
                if not self.bstore.update(existing.name, new_name, new_url):
                    return "Bookmark name already exists"
            self._render_active_page()
            self._safe_update()
            return None

        open_bookmark_dialog(page, on_save, existing)

    def _edit_bookmark(self, name: str) -> None:
        self._open_bookmark_dialog(name)

    def _delete_bookmark(self, name: str) -> None:
        page = self.page
        assert page is not None

        def do_delete() -> None:
            self.bstore.delete(name)
            self._render_active_page()
            self._safe_update()

        open_confirm_dialog(
            page,
            name,
            do_delete,
            title=f"Delete bookmark '{name}'?",
            body="It will also be removed from any pools.",
        )

    def _open_pool_dialog(
        self, name: str | None = None, preselected: list[str] | None = None
    ) -> None:
        page = self.page
        assert page is not None
        existing = self.bstore.get_pool(name) if name else None

        def on_save(new_name: str, members: list[str]) -> str | None:
            if existing is None:
                if not self.bstore.add_pool(new_name, members):
                    return "Pool name already exists"
            else:
                if not self.bstore.update_pool(existing.name, new_name, members):
                    return "Pool name already exists"
                # Propagate a rename to every profile referencing the old pool
                # name, so the reference stays valid (audit5 #4).
                if new_name != existing.name:
                    self.pm.rename_bookmark_pool(existing.name, new_name)
            self._render_active_page()
            self._safe_update()
            return None

        open_pool_dialog(
            page,
            self.bstore.list_bookmarks(),
            on_save,
            existing,
            preselected=preselected,
        )

    def _make_pool_from(self, bookmark_names: list[str]) -> None:
        self._open_pool_dialog(preselected=bookmark_names)

    def _edit_pool(self, name: str) -> None:
        self._open_pool_dialog(name)

    def _delete_pool(self, name: str) -> None:
        page = self.page
        assert page is not None

        def do_delete() -> None:
            # bstore.delete_pool owns the whole operation: it records which
            # profiles referenced the pool and THEN drops the reference from
            # each. Do not clear the references here — doing so first is exactly
            # the bug that made a restored pool come back unreferenced.
            self.bstore.delete_pool(name)
            self._render_active_page()
            self._safe_update()

        open_confirm_dialog(
            page,
            name,
            do_delete,
            title=f"Delete pool '{name}'?",
            body="The bookmarks themselves are kept.",
        )

    # --- Cookies ---

    def _profile_dir(self, name: str) -> str:
        import os

        from ..core.config import DATA_DIR

        # No getcwd() anchor: config._under_home anchors DATA_DIR at import, so
        # it does not move when the process's cwd moves (PS-127). The join used
        # to be load-bearing under a RELATIVE PERSONA_DATA_DIR — the shape
        # .env.example ships — and inert otherwise, so the call site could not
        # tell whether it was compensating. It now never is.
        #
        # Relying on cwd-INVARIANCE rather than on absoluteness is deliberate:
        # the latter is not universal. On a Windows path flavour a rooted-but-
        # driveless override ('/custom/data') comes back verbatim and is not
        # isabs — see the CAVEAT in _under_home's docstring. Re-adding a getcwd()
        # join would not help that shape anyway (it would pin it to the current
        # drive, relocating a path the operator spelled); invariance is the
        # property this call site actually needs, and it holds under every shape.
        return os.path.join(DATA_DIR, name)

    async def _import_cookies_file(self, profile_name: str) -> str | None:
        from ..services.cookie.store import import_cookies, parse_cookies_json

        assert self.refs is not None
        files = await self.refs.file_picker.pick_files(
            allow_multiple=False,
            allowed_extensions=["json"],
            dialog_title=f"Import cookies into {profile_name}",
        )
        if not files or not files[0].path:
            return None
        import os

        fname = os.path.basename(files[0].path)
        try:
            with open(files[0].path, encoding="utf-8") as f:
                cookies = parse_cookies_json(f.read())
            n = import_cookies(self._profile_dir(profile_name), cookies)
        except Exception as e:
            self._log(f"[{profile_name}] cookie import failed: {e}")
            return f"import failed: {e}"
        status = f"{fname} · {n} cookies"
        self.pm.set_cookie_status(profile_name, status)
        self._log(f"[{profile_name}] imported {n} cookies from {fname}")
        return f"imported {status}"

    async def _export_cookies_file(self, profile_name: str) -> str | None:
        import json

        from ..services.cookie.store import export_cookies

        assert self.refs is not None
        path = await self.refs.file_picker.save_file(
            dialog_title=f"Export cookies from {profile_name}",
            file_name=f"{profile_name}-cookies.json",
            allowed_extensions=["json"],
        )
        if not path:
            return None
        try:
            cookies = export_cookies(self._profile_dir(profile_name))
            if not path.endswith(".json"):
                path += ".json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
        except Exception as e:
            self._log(f"[{profile_name}] cookie export failed: {e}")
            return f"export failed: {e}"
        self._log(f"[{profile_name}] exported {len(cookies)} cookies")
        return f"exported {len(cookies)} cookies"

    # --- Engine update ---

    def _engine_icon(self) -> ft.Control:
        from ..core.assets import asset_path

        path = asset_path("v_engine.png")
        if os.path.exists(path):
            return ft.Image(src=path, width=18, height=18)
        return ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=15, color=COLORS["text_sub"])

    def _engine_update_available(self) -> bool:
        """True when a newer Chromium build exists AND persona is willing to
        install it.

        The policy gate is deliberately part of THIS predicate rather than only
        of the download: it drives the sidebar dot, the row's "update → NN" text
        and the click handler, so a build persona would refuse must not be
        advertised as an available update — otherwise the operator clicks, the
        download is refused, and the row goes back to offering it forever.
        Mirrors _engine2_update_available's `not self._engine2_compatible` gate.

        THE UNVERIFIABLE REFUSAL IS FOLDED IN HERE FOR EXACTLY THAT REASON
        (PS-49). It is the one refusal the policy gate below cannot express:
        KNOWN_BAD and ABOVE_CEILING both make is_installable False, which
        suppresses the offer on their own. A digest-less release is policy-OK —
        `policy.check` returns ('ok', '') for it — so without this clause the
        row goes on advertising "update → NN" for a build persona has already
        declined, the refusal message computed for the row is outranked and
        never painted, and _auto_update_engine (which gates on this same
        predicate and nothing else) re-fetches and re-refuses it every hour
        forever. That is precisely the failure this docstring warns about two
        paragraphs up.
        """
        # A DELIBERATE REVERT IS A STANDING "NOT THIS BUILD" (PS-79), and this
        # is the one place "is there an update to offer?" is decided — the row's
        # text, its update dot, the click handler AND _auto_update_engine all
        # read this predicate and nothing else. So the pin belongs here rather
        # than only on the unattended path.
        #
        # Without it the reversal lasts under an hour. The operator goes back to
        # the build that was working; the very next hourly check sees the build
        # they just rejected as newer than what is now installed, and puts them
        # straight back on it, unattended, with no operator present. That is the
        # whole failure the pin exists to end.
        #
        # Mirrors _engine2_update_available's `if pinned_build(): return False`,
        # but reads the CHROMIUM pin — a different settings key from Firefox's,
        # deliberately: sharing one flat-store key would make a revert on either
        # engine mute the other engine's update row.
        if engine.pinned_build():
            return False
        if not engine.is_newer(self._engine_latest, engine.current_version()):
            return False
        if self._engine_unverifiable_tag == self._engine_latest:
            return False
        return engine_policy.is_installable(self._engine_latest)

    def _record_engine_check(self, tag: str) -> str:
        """Record the result of a Chromium version check and return the log line.

        Every check path (panel open, hourly poll, explicit click, startup)
        funnels through here so one policy decision is made in one place — four
        copies of "is this installable?" is how the two engines drifted apart in
        the first place. Sets _engine_latest and _engine_status; the caller logs
        the returned line (empty when there is nothing worth saying).
        """
        if not tag:
            return ""
        self._engine_latest = tag
        if self._engine_update_available():
            self._engine_status = ""
            return f"Chromium engine update available ({tag})"
        # A build refused as unverifiable keeps its refusal on the row (PS-49).
        # Checked BEFORE engine_policy.check below, because that call answers OK
        # for a digest-less build — so the bottom of this method would fall
        # through to `self._engine_status = ""` and erase the refusal on the very
        # next hourly check, one tick after the operator was told about it. The
        # three policy verdicts don't hit this because a refused verdict is
        # never OK; the fourth refusal is decided at the transfer, not by the
        # policy module, so it has to be re-asserted here.
        if self._engine_unverifiable_tag == tag:
            return ""
        # Not offered. Distinguish "persona refused this build" from the
        # ordinary "already current" case, so a declined engine is never
        # silently indistinguishable from being up to date.
        verdict, message = engine_policy.check(tag)
        if verdict != engine_policy.OK and engine.is_newer(
            tag, engine.current_version()
        ):
            # ABOVE_CEILING no longer means "persona is behind" — persona ships
            # no Chromium ceiling now that the advertised version is derived
            # from the installed engine (PS-42). The only way to reach it is an
            # operator who set max_tested_major themselves, so point at THEIR
            # policy file rather than telling them to update persona, which
            # would not lift a limit they imposed. The Firefox row keeps the
            # "update persona" wording because its cap really is shipped.
            self._engine_status = (
                "engine pinned by your policy file"
                if verdict == engine_policy.ABOVE_CEILING
                else "engine update blocked"
            )
            return message
        self._engine_status = ""
        return ""

    def _engine_logo(self, engine_key: str, size: int = 18) -> ft.Control:
        from ..core.assets import asset_path

        fname = (
            "engine_firefox.svg"
            if engine_key in ("firefox", "camoufox")
            else "engine_chrome.svg"
        )
        path = asset_path(fname)
        # The SVGs are square (viewBox 0 0 24 24); fit=CONTAIN scales them to the
        # box without cropping and the centred container keeps them from drifting.
        if os.path.exists(path):
            inner: ft.Control = ft.Image(
                src=path, width=size, height=size, fit=ft.BoxFit.CONTAIN
            )
        else:
            inner = ft.Icon(ft.Icons.PUBLIC, size=size, color=COLORS["text_sub"])
        return ft.Container(
            width=size, height=size, alignment=ft.Alignment.CENTER, content=inner
        )

    @staticmethod
    def _status_needs_reveal(
        value: str, expanded: bool, limit: int = _VERSION_MAX_CHARS
    ) -> bool:
        """Whether this status string is longer than its one-line cell.

        Character-budgeted rather than measured, because flet gives no text
        metrics back: the version cell is ~110px of a 200px rail and monospace
        at size 12 runs ~6.2px per character (the same budget
        :data:`_VERSION_MAX_CHARS` is derived from). A string at or under it
        fits and gets NO reveal control — an affordance on a line that is
        already whole is noise, and worse, it invites a click that visibly
        does nothing.

        ``limit`` DEFAULTS TO THE ENGINE CELL'S BUDGET so both engine call
        sites are unchanged, and exists because the second caller measures a
        different cell: the APP version panel's status line is a top-level
        child of the panel and gets the FULL rail
        (:data:`_RAIL_MAX_CHARS`, 22), not the ~110px version cell an engine
        status shares with an icon, a name and a state dot. Passing the
        narrower number there would draw the chevron on lines that already
        fit — precisely the click-that-does-nothing this method exists to
        avoid.
        """
        if expanded:
            return True
        return len(value or "") > limit

    @staticmethod
    def _status_expanded_attr(which: str) -> str:
        """The instance attribute holding one engine's reveal flag."""
        return (
            "_engine_status_expanded"
            if which == "chromium"
            else "_engine2_status_expanded"
        )

    def _status_expanded(self, which: str) -> bool:
        """Whether one engine's status line is currently revealed.

        READ THROUGH THIS, NEVER OFF THE ATTRIBUTE DIRECTLY, and the reason is
        a coupling one rather than a style one. ``_build_engines_panel`` is
        reachable from construction paths that do not run ``__init__`` — the
        progress tests build the app with ``App.__new__(App)`` precisely to
        exercise the panel without standing up a page — so a panel builder that
        hard-requires an attribute set only in ``__init__`` raises
        ``AttributeError`` on every one of them, and would do so again for any
        future partial construction.

        The default is ``False`` — COLLAPSED — which is also the safe
        direction: collapsed is the state that keeps the long-lived control
        the download-progress callback writes to, so a panel built without
        ``__init__`` renders the live row rather than a frozen snapshot.
        """
        return bool(getattr(self, self._status_expanded_attr(which), False))

    def _toggle_engine_status(self, which: str) -> None:
        """Reveal / re-collapse one engine's full status text in place."""
        attr = self._status_expanded_attr(which)
        setattr(self, attr, not self._status_expanded(which))
        self._refresh_sidebar()

    def _status_reveal_button(self, which: str, expanded: bool) -> ft.Control:
        """The gesture that shows a truncated status in full.

        WHY EXPAND-IN-PLACE AND NOT A TOOLTIP. A tooltip was the cheaper
        answer and is the wrong one here for three reasons: it is invisible in
        a screenshot (which is the artifact this design is judged from), it
        needs a hover a touch/trackpad operator may never perform, and it is
        the mechanism the row ALREADY uses for the full version string — so a
        second, different meaning on the same gesture would collide with it.
        Expanding in place is visible, clickable, and reversible.

        IT IS ITS OWN CONTROL, NOT THE ROW'S CLICK. The row's ``on_click``
        already means "check / update this engine" — a download over Tor on the
        Chromium row. Overloading that gesture with "show me the text" would
        make reading an error message start a hundreds-of-megabyte transfer.
        """
        return ft.Container(
            on_click=lambda _: self._toggle_engine_status(which),
            ink=True,
            width=16,
            height=16,
            border_radius=3,
            alignment=ft.Alignment.CENTER,
            tooltip=(
                "Hide the full status" if expanded else "Show the full status"
            ),
            content=ft.Icon(
                ft.Icons.UNFOLD_LESS if expanded else ft.Icons.UNFOLD_MORE,
                size=12,
                color=COLORS["text_dim"],
            ),
        )

    def _engine_row(
        self, badge: ft.Control, name: str, status: ft.Control, checking: bool,
        dot: bool = False, reveal: ft.Control | None = None,
    ) -> ft.Control:
        """One engine, on ONE line: what it is, and what state it is in.

        THE COMPLAINT THIS ANSWERS. "под хромиумом длина текста больше чем
        «Война и мир»" — the Chromium entry read as a paragraph in a 200px
        rail. Two mechanisms produced that, and both are fixed here rather than
        padded around:

        1. **The row was a two-line block.** Name above, version below, in a
           ``Column``. Two engines therefore cost four lines plus their
           spacing, in the rail that is already short at the app's minimum
           window size — which is the same budget that was clipping `trash`.
           Name and version now share one line, halving the panel.

        2. **The version line wrapped, so it became THREE lines.** It carried
           "update → 148.0.7778.215" — 23 characters of monospace in ~120px of
           usable rail — and nothing stopped it wrapping, so the text visibly
           shifted the moment the section opened. That is the "съехавший" half
           of the report. The version is now shortened at the source (see
           :func:`_short_engine_version`) and pinned to a single line.

        The "update →" prose is gone because it was saying what the accent DOT
        already says — on BOTH rows: this is the shared builder for the
        Chromium and Firefox entries, and a claim made here that held for only
        one of its two callers is exactly the stale reference that costs the
        next reader a round. Name, version, state — nothing else.

        WHERE THE FULL VERSION IS *NOT*: neither of these two rows' tooltips.
        Both are static strings ("Check / update fp-chromium", "Check / update
        the Firefox engine") naming the GESTURE, with no version interpolated
        into either. The row that DOES carry a build identifier in its tooltip
        is the ROLLBACK row, which is a different control.

        AND IT IS NOT BEHIND THE REVEAL EITHER, ONCE THE LINE HAS BEEN
        SHORTENED — stated plainly because the arithmetic is easy to get
        backwards. :meth:`_status_needs_reveal` draws the chevron on
        ``len(value) > _VERSION_MAX_CHARS``, and it is fed the ALREADY
        shortened value, which :func:`_short_engine_version` caps AT that
        budget. So the reveal fires on a long *status* (a service string, an
        exception message) and NOT on a long *version*:

            "update → firefox-20_151.0_20260817150018"  40 → reveal drawn
            "firefox-20_151.0…"                        17 → no reveal

        That is item 6's rule working as intended — a whole line gets no
        affordance, because one invites a click that visibly does nothing —
        but the consequence is real and is not hidden here: in the
        update-available state the trailing "_20260817150018" of a long
        Firefox tag is currently reachable from nowhere in the UI. The build
        and the upstream version, which is what the line is for, both survive.
        Surfaced in review rather than papered over.

        `status` is the LIVE Text control the progress callback writes to,
        embedded as-is: a string snapshot here is what froze the row's percent
        while the byte counter beneath it kept moving.
        """
        trailing: list[ft.Control] = []
        if checking:
            trailing.append(
                ft.ProgressRing(
                    width=11, height=11, stroke_width=2, color=COLORS["accent"]
                )
            )
        elif dot:
            trailing.append(
                ft.Container(width=7, height=7, border_radius=4, bgcolor=COLORS["accent"])
            )
        return ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                badge,
                # NAME ABOVE, VERSION BELOW — measured, not preferred. Putting
                # the two on ONE line looked like the tighter answer and is
                # not: the rail is 200px, and once its padding, the 18px icon,
                # "fp-chromium" and the state dot have taken their share the
                # version cell is left about 26px, which ellipsised even
                # "checking..." down to "ch…". A cell too narrow to hold the
                # value is not a shorter row, it is a row that says nothing.
                #
                # So the stacked block stays and the WRAP is what goes. Both
                # texts are single-line with an ellipsis (see where they are
                # constructed), which is the actual defect behind "текст
                # съевший": an unbounded version string broke across three
                # lines and changed the panel's height when the section opened.
                ft.Column(
                    spacing=1,
                    expand=True,
                    controls=[
                        ft.Text(
                            name, size=11, color=COLORS["text_sub"],
                            font_family="monospace", no_wrap=True, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        status,
                    ],
                ),
                # The reveal sits OUTSIDE the expanding Column, so widening the
                # status never pushes it off the rail, and the Column's
                # expand=True is what guarantees the status text is squeezed
                # rather than allowed its intrinsic width.
                *([reveal] if reveal is not None else []),
                *trailing,
            ],
        )

    @staticmethod
    def _apply_status_bounds(control: ft.Text, expanded: bool) -> None:
        """Re-apply the one-line / revealed bound to a LONG-LIVED status Text.

        WHY THIS EXISTS AT ALL, because it is not obvious and it cost a capture.
        The two engine status controls (``engine_text``, ``_engine2_text``) are
        built ONCE in ``__init__`` and embedded in the row by reference — that
        is deliberate and must stay, because the download callback writes to
        them live and a snapshot here would freeze the percent mid-transfer.

        But it means the reveal flag cannot be expressed by constructing the
        control differently: the object the panel renders is the same object
        every rebuild, carrying whatever bounds it was born with. Toggling
        ``_engine_status_expanded`` therefore flipped the chevron's icon and
        tooltip and changed NOTHING about the text — the row still ellipsised
        to one line, so the reveal was a control that appeared to work and did
        not. Caught by looking at the render, not at the semantics tree, which
        reported the toggle as successful.

        So the bound is applied to the LIVE control at build time instead. The
        collapsed branch restates the one-line bound rather than assuming it,
        because a control that was previously expanded has to be put back.
        """
        control.no_wrap = not expanded
        control.max_lines = _STATUS_EXPANDED_MAX_LINES if expanded else 1
        control.overflow = ft.TextOverflow.ELLIPSIS
        # A revealed status reads as a block of prose, so it is left-aligned;
        # collapsed it is a value at the end of a row and stays right-aligned.
        control.text_align = (
            ft.TextAlign.LEFT if expanded else ft.TextAlign.RIGHT
        )

    def _status_control(
        self, live: ft.Text, expanded: bool
    ) -> ft.Control:
        """The status control to render for one engine row.

        TWO CONTROLS, CHOSEN BY STATE, rather than one control mutated — and
        the difference is not stylistic, it is the second bug this round.

        Mutating the long-lived control's ``no_wrap``/``max_lines`` in place
        (see :meth:`_apply_status_bounds`) sets the properties correctly and
        the client does not repaint them: the panel hands flet the SAME object
        it handed it last rebuild, so the reveal flipped the chevron's icon and
        tooltip while the text stayed ellipsised to one line. The semantics
        tree reported that as a successful toggle — only the pixels disagreed.

        So the REVEALED state gets a freshly constructed control, which flet
        has no choice but to paint. The COLLAPSED state keeps the long-lived
        one, because that is the object the download progress callback writes
        to live and swapping it would freeze the percent mid-transfer. The
        trade lands on the right side: a revealed status is a static string
        being read, while a collapsed one is the line that has to stay live.
        """
        if expanded:
            return sidebar_status_text(
                live.value or "",
                size=12,
                color=COLORS["text_main"],
                expanded=True,
            )
        self._apply_status_bounds(live, False)
        return live

    def _build_engines_panel(self) -> ft.Control:
        header = ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            on_click=lambda _: self._toggle_engines(),
            ink=True,
            tooltip="Browser engines — open to check both",
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self._engine_icon(),
                            ft.Text(
                                "engines", size=11, color=COLORS["text_sub"],
                                font_family="monospace",
                            ),
                            *(
                                [
                                    ft.Container(
                                        width=7, height=7, border_radius=4,
                                        bgcolor=COLORS["accent"],
                                    )
                                ]
                                if (
                                    self._engine_update_available()
                                    or self._engine2_update_available()
                                )
                                else []
                            ),
                        ],
                    ),
                    ft.Icon(
                        ft.Icons.KEYBOARD_ARROW_UP
                        if self._engines_open
                        else ft.Icons.KEYBOARD_ARROW_DOWN,
                        size=16,
                        color=COLORS["text_sub"],
                    ),
                ],
            ),
        )

        def _bar_block(bar, detail) -> ft.Control:
            return ft.Container(
                padding=ft.Padding.only(left=36, right=10, top=2, bottom=2),
                content=ft.Column(spacing=2, controls=[bar, detail]),
            )

        body: list[ft.Control] = []
        if self._engines_open:
            body = [ft.Divider(height=10, color=COLORS["border"])]
            # fp-chromium row, with its own progress bar directly beneath it
            body.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10),
                    on_click=lambda _: self._on_engine_click(),
                    ink=True,
                    tooltip="Check / update fp-chromium",
                    content=self._engine_row(
                        self._engine_logo("chromium"),
                        "fp-chromium",
                        self._status_control(
                            self.engine_text, self._status_expanded("chromium")
                        ),
                        checking=self._engine_busy or self._engine_checking,
                        dot=self._engine_update_available(),
                        reveal=(
                            self._status_reveal_button(
                                "chromium", self._status_expanded("chromium")
                            )
                            if self._status_needs_reveal(
                                self.engine_text.value or "",
                                self._status_expanded("chromium"),
                            )
                            else None
                        ),
                    ),
                )
            )
            # The progress bar belongs to a DOWNLOAD only. A version check shows
            # just the spinner in the row above — no bar (it would display stale
            # bytes from a past download).
            if self._engine_busy:
                body.append(_bar_block(self._engine_bar, self._engine_detail))
            # Going BACK, for Chromium. Same three-state slot as the Firefox row
            # further down and for the same reasons — except this one DOWNLOADS
            # (PS-79): Chromium keeps one un-versioned tree, so the previous
            # build's files are genuinely gone and only its identity survives.
            # The tooltip says so, because "go back" that costs hundreds of
            # megabytes over Tor must not look like the instant Firefox gesture.
            body.append(self._engine_rollback_row())
            body.append(ft.Container(height=8))
            # firefox engine row, with its own progress bar directly beneath it
            self._engine2_text.value = self._engine2_status_text()
            body.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10),
                    on_click=lambda _: self._on_engine2_click(),
                    ink=True,
                    tooltip="Check / update the Firefox engine",
                    content=self._engine_row(
                        self._engine_logo("firefox"),
                        "firefox",
                        self._status_control(
                            self._engine2_text, self._status_expanded("firefox")
                        ),
                        checking=self._engine2_busy or self._engine2_checking,
                        dot=self._engine2_update_available(),
                        reveal=(
                            self._status_reveal_button(
                                "firefox", self._status_expanded("firefox")
                            )
                            if self._status_needs_reveal(
                                self._engine2_text.value or "",
                                self._status_expanded("firefox"),
                            )
                            else None
                        ),
                    ),
                )
            )
            if self._engine2_busy:
                body.append(_bar_block(self._engine2_bar, self._engine2_detail))
            # Going BACK. Offered only when there is a retained build to go back
            # to (rollback_target) — a revert with nothing retained is a button
            # that cannot work, and showing it would promise an undo the machine
            # cannot perform. While pinned, the same slot becomes "resume", so
            # the operator always has the way out of the state they are in.
            body.append(self._engine2_rollback_row())
            body.append(ft.Container(height=6))

        return ft.Container(
            border_radius=3,
            border=ft.Border.all(1, COLORS["card_border"]),
            margin=ft.Margin.only(bottom=10),
            content=ft.Column(spacing=0, controls=[header, *body]),
        )

    def _toggle_engines(self) -> None:
        self._engines_open = not self._engines_open
        # Rebuild the sidebar FIRST so the panel actually opens; the engine
        # check is a best-effort extra that must never block or break the toggle
        # (a slow/raising check was leaving the panel stuck closed).
        self._refresh_sidebar()
        if self._engines_open:
            try:
                self._check_both_engines()
            except Exception as e:
                logger.error("engine check failed on panel open: %s", e)

    def _check_both_engines(self) -> None:
        """Opening the panel checks both engines for an upstream update over
        the network — each runs on its own thread with its own spinner."""
        if (
            not self._engine_busy
            and not self._engine_checking
            and not self._engine_update_available()
        ):
            self._engine_checking = True
            self._refresh_engine_text("checking...")

            def work() -> None:
                # Clear the in-flight flag in finally so a network error in
                # fetch_latest() can't wedge the button on "checking..." forever.
                try:
                    tag, _url = engine.fetch_latest()
                    line = self._record_engine_check(tag)
                    if line:
                        self._log(line)
                except Exception as e:
                    self._log(f"Chromium engine check failed: {e}")
                finally:
                    self._engine_checking = False
                    self._refresh_engine_text()

            threading.Thread(target=work, daemon=True).start()

        # The Firefox engine updates on-demand like fp-chromium: binary-only
        # rebuilds are published as firefox-NN releases on the engine repo,
        # and the bundled package can download any build that still ships its
        # expected per-OS asset. Missing → download it; installed → check the
        # repo for a newer build.
        from ..services.browser import invisible_launch as inv

        try:
            if not inv.is_invisible_installed() and not self._engine2_busy:
                self._ensure_engine2_async()
            elif (
                not self._engine2_busy
                and not self._engine2_checking
                and not self._engine2_update_available()
            ):
                self._check_engine2_async()
        except Exception as e:
            logger.error("firefox engine check failed: %s", e)

    def _check_engine2_async(self) -> None:
        """Check the engine repo for a newer firefox-NN build. Spinner only —
        a version check moves no bytes, so never the download bar (same rules
        as the chromium row)."""
        from ..services.engine import firefox as ff_engine

        self._engine2_checking = True
        self._refresh_engine_text()

        def work() -> None:
            from ..services.browser.invisible_launch import pinned_build

            tag, compatible, capped_by = ff_engine.fetch_latest_full()
            if tag:
                self._engine2_latest = tag
                self._engine2_compatible = compatible
            self._engine2_checking = False
            pin = pinned_build()
            if pin:
                # While pinned, this row's job is to explain why it is NOT
                # updating. Clicking the row lands here (there is no update to
                # offer any more), so this is the branch the operator actually
                # reaches — leaving the status empty would fall back to the
                # bare version and silently drop the only sentence telling
                # them a revert is in force and how to leave it.
                self._engine2_status = f"pinned to {pin}"
            elif self._engine2_update_available():
                self._engine2_status = ""
                self._log(f"Firefox engine update available ({tag})")
                if capped_by:
                    # The offer is real, but it is not the newest build that
                    # exists — say so in the same breath rather than letting
                    # the operator install it and only then discover they are
                    # capped (which is the state the next branch handles).
                    self._log(
                        f"Firefox engine {capped_by} needs a newer persona — "
                        "update the app to get it"
                    )
            elif (
                tag
                and not compatible
                and ff_engine.is_newer(tag, ff_engine.current_version())
            ):
                # The newest build ships assets this persona's engine package
                # doesn't know how to fetch or drive — downloading it here
                # would 404 or produce an unlaunchable engine.
                self._engine2_status = "update persona for the newest engine"
                self._log(
                    f"Firefox engine {tag} needs a newer persona — "
                    "update the app to get it"
                )
            elif capped_by:
                # PS-112: THE OPERATOR IS ALREADY ON THE HIGHEST DRIVABLE BUILD
                # AND UPSTREAM IS ABOVE THE PIN. Nothing above reaches this
                # state: `compatible` is True (so the branch above is
                # unreachable) and the offered tag is what is already
                # installed (so `is_newer` is False and no update is
                # available). Without this branch the row would fall back to
                # the bare version — blank — while firefox-NN exists upstream.
                #
                # This is where preferring the drivable build leads on its own
                # success path: an operator offered the drivable build installs
                # it and lands here permanently. Same sentence as the branch
                # above, about `capped_by` rather than `tag`, because it is the
                # same fact — a build exists that this persona cannot drive.
                self._engine2_status = "update persona for the newest engine"
                self._log(
                    f"Firefox engine {capped_by} needs a newer persona — "
                    "update the app to get it"
                )
            self._refresh_engine_text()

        threading.Thread(target=work, daemon=True).start()

    def _wire_engine_prune_guard(self) -> None:
        """Tell the engine-install layer how to ask whether a profile is running,
        so pruning defers instead of deleting a build out from under a live
        session. Called once from __init__ — before any prune path exists — so
        every prune (startup housekeeping and post-download alike) is covered
        without each call site needing its own condition.

        Wires the SAME oracle into the Chromium updater, for the same reason at
        a different moment: pruning must not DELETE a build a session is running
        from, and an unattended install must not REPLACE one. Chromium keeps a
        single un-versioned tree, so its install path is the more dangerous of
        the two — see updater.set_in_use_provider. One method because there is
        one oracle and one moment it must be wired by; splitting it invites an
        app that wires half of it.

        Best-effort: a failure to wire must never stop the app from starting.
        Note the two layers answer an unwired provider DIFFERENTLY on purpose —
        pruning proceeds (fail-open, bounded cost), the Chromium install defers
        (fail-closed, the cost is a live session). So a wiring failure degrades
        to today's behaviour on one side and to "wait for the next check" on the
        other, and neither degrades to corrupting a running browser."""
        try:
            from ..services.browser import invisible_launch as inv

            inv.set_in_use_provider(
                lambda: len(self.bl.running_profile_names()) > 0
            )
        except Exception:
            logger.exception("Could not wire the engine-prune in-use guard")
        try:
            engine.set_in_use_provider(
                lambda: len(self.bl.running_profile_names()) > 0
            )
        except Exception:
            logger.exception("Could not wire the Chromium engine in-use guard")

    def _auto_update_engine2_async(self) -> None:
        """On startup: check the engine repo and, if a newer AND compatible
        firefox-NN build exists, download it automatically in the background —
        so a stale engine (e.g. an old firefox-13 with flat emoji) is upgraded
        without the user hunting through the cache or clicking anything. The new
        build lands in its own versioned dir, marker-last, so the running engine
        is untouched until it's whole; the next launch uses it. An incompatible
        newer build (needs a newer persona) is surfaced, not downloaded."""
        threading.Thread(target=self._auto_update_engine2, daemon=True).start()

    def _auto_update_engine2(self) -> None:
        """The startup engine check (runs on the thread _auto_update_engine2_async
        spawns; split out so it's directly testable). Downloads a newer,
        compatible build; surfaces an incompatible one; no-ops when current."""
        from ..services.browser import invisible_launch as inv
        from ..services.engine import firefox as ff_engine

        if self._engine2_busy or not inv.is_invisible_installed():
            return
        # An operator who deliberately went BACK to an older build has said, in
        # the only way the product offers, that the newer one is bad for them.
        # Updating anyway would walk them straight back onto it unattended —
        # the revert would last until this check ran and no longer, which is a
        # nominal undo, not a real one. So a pin holds the automatic update off
        # entirely; clearing the pin is the operator saying "go forward again".
        #
        # Deliberately BEFORE the prune too: the prune spares the pinned build
        # by itself, but returning here keeps the whole pinned state inert
        # rather than resting that on a second guard.
        pin = inv.pinned_build()
        if pin:
            self._engine2_status = f"pinned to {pin}"
            self._log(
                f"Firefox engine is pinned to {pin} — automatic updates are "
                "paused until you resume them"
            )
            self._refresh_engine_text()
            return
        # Reclaim any engine build a past update left stale (e.g. the ~600MB
        # pinned firefox-15 an upgrade to firefox-16 kept around) before the
        # network check, so disk is freed even when nothing new is fetched.
        try:
            inv.prune_superseded_builds(log=self._log)
        except Exception:
            pass
        try:
            tag, compatible, capped_by = ff_engine.fetch_latest_full()
        except Exception:
            return
        if not tag:
            return
        self._engine2_latest = tag
        self._engine2_compatible = compatible
        current = ff_engine.current_version()
        if not ff_engine.is_newer(tag, current):
            # PS-112: `tag` is now the newest DRIVABLE build, not the newest
            # build that exists, so "not newer than installed" no longer means
            # "up to date". When a higher release was passed over, saying "up
            # to date" here is affirmatively FALSE — upstream has something,
            # the operator simply cannot drive it until they update the app.
            # This is the state the fix's own success path leads to: install
            # the offered drivable build and you land here every startup.
            if capped_by:
                self._engine2_status = "update persona for the newest engine"
                self._log(
                    f"Firefox engine {capped_by} needs a newer persona — "
                    "update the app to get it"
                )
            else:
                self._log(f"Firefox engine is up to date ({current})")
            self._refresh_engine_text()
            return
        if not compatible:
            self._engine2_status = "update persona for the newest engine"
            self._log(
                f"Firefox engine {tag} needs a newer persona — "
                "update the app to get it"
            )
            self._refresh_engine_text()
            return
        if capped_by:
            # The build being fetched is a real update, but it is not the
            # newest one upstream ships. Say both, in that order, so the
            # download line still reads as success and the cap is not silent.
            self._log(
                f"Firefox engine {capped_by} needs a newer persona — "
                "update the app to get it"
            )
        self._log(f"Firefox engine {current} is out of date — fetching {tag}")
        self._update_engine2_async()

    def _on_engine2_click(self) -> None:
        """Clicking the Firefox-engine row: download the engine if it's
        missing, download the newer build when one is known, otherwise
        re-check the repo."""
        if self._engine2_busy or self._engine2_checking:
            return
        from ..services.browser import invisible_launch as inv

        if not inv.is_invisible_installed():
            # Claim the busy flag SYNCHRONOUSLY, before spawning the worker, so a
            # second click during the gap between here and the worker actually
            # flipping _engine2_busy can't start a second download (#234: Mars
            # clicked while a download was already in flight and it restarted
            # from scratch). The worker keeps its own is_invisible_installed
            # guard and clears the flag if there's nothing to fetch.
            self._engine2_busy = True
            self._ensure_engine2_async()
        elif self._engine2_update_available():
            self._update_engine2_async()
        else:
            self._check_engine2_async()

    def _refresh_engine_text(self, status: str = "") -> None:
        def apply() -> None:
            cur = _short_engine_version(engine.current_version() or "unknown")
            if status:
                self.engine_text.value = status
            elif self._engine_update_available():
                # No "update →" prose: the accent dot beside the row already
                # says an update is available, and the words were most of what
                # made this line wrap in a 200px rail.
                self.engine_text.value = _short_engine_version(self._engine_latest)
            elif self._engine_status:
                # A build persona refused. Without this the row would fall
                # through to the installed version and read as "up to date",
                # hiding the fact that an upstream build exists and was declined
                # — the same reason the Firefox row surfaces _engine2_status.
                self.engine_text.value = self._engine_status
            else:
                self.engine_text.value = cur
            if self._sidebar_host is not None:
                self._sidebar_host.content = self._build_sidebar()
            self._safe_update()

        self._ui(apply)

    def _check_app_update_async(self) -> None:
        """Check for a newer release on startup and then periodically. The
        actual download/restart decision is made by _on_update_found per the
        auto-update setting and whether profiles are running."""
        import threading

        def loop() -> None:
            import time

            while True:
                if not self._update_in_progress and not self._update_staged:
                    tag, url, size = app_update.check_for_update()
                    if tag and url and tag != self._app_latest:
                        self._app_latest = tag
                        self._app_update_url = url
                        self._app_update_size = size
                        self._app_update_tag = tag
                        self._on_update_found(tag, url)
                time.sleep(60)

        threading.Thread(target=loop, daemon=True).start()

    def _check_engines_periodic(self) -> None:
        """Quietly poll the chromium engine for an upstream update once an hour
        (no spinner), then fetch an acceptable build unattended.

        This poll used to ONLY refresh the 'latest' version — installing stayed
        a click, which is exactly how an operator who never read the row kept an
        old engine forever (PS-43). It now ends at _auto_update_engine, so the
        hourly tick is both the discovery of a new build AND the retry that
        eventually installs one: a build deferred because a profile was running
        is picked up by a later tick, once the profiles have closed, with no
        extra timer of its own.

        The Firefox engine is auto-updated once at startup
        (_auto_update_engine2_async) rather than polled hourly — it can install
        under a live session safely, because each of its builds lands in its own
        versioned dir instead of replacing one shared tree."""
        import threading
        import time

        def loop() -> None:
            while True:
                time.sleep(3600)
                try:
                    if not self._engine_busy:
                        tag, _url = engine.fetch_latest()
                        line = self._record_engine_check(tag)
                        if line:
                            self._log(line)
                        self._auto_update_engine()
                except Exception:
                    pass
                self._refresh_sidebar()

        threading.Thread(target=loop, daemon=True).start()

    def _clear_held_discovery(self, tag: str) -> None:
        """Un-discover a held release: forget the tag and the URL the caller
        just recorded, then say so once.

        RETURNING WITHOUT THIS IS NOT ENOUGH, and that gap is the whole reason
        this method exists. Every discovery path writes the three fields BEFORE
        it calls the gate — the 60s poll at _check_app_update_async and the
        manual check at _check_app_update_now both do

            self._app_latest = tag; self._app_update_url = url; ...
            self._on_update_found(tag, url)

        so a gate that merely returns leaves all three set. _build_version_panel
        then computes `has_update = bool(self._app_latest) and ... != ver` as
        True and renders "[ update to <held tag> ]" DIRECTLY ABOVE the "resume
        updates (held <tag>)" line — the panel offering to install the very
        release it is explaining is held. That is the same contradiction
        _set_update_staged's docstring already refuses one gesture over
        ("restart into two opposite versions with no way to tell which wins"),
        landing on the row AC5 is about.

        And the button is LIVE: its on_click is _apply_update_now, which
        downloads from _app_update_url and installs. So the fix has to empty
        the FIELDS, not just decline to act on them. Emptying both closes every
        route that reads them — the panel button, _on_version_click's resume-
        the-download arm, and _set_auto_update's kick-off when auto-update is
        switched back on.

        This mirrors _on_app_resume_updates, which zeroes _app_latest in the
        OTHER direction so the poll's `tag != self._app_latest` dedup reopens.
        Here that same dedup reopening is why the log line needs its own
        de-duper: with _app_latest cleared the poll re-announces this tag every
        60 seconds, so _app_held_logged keeps the explanation to once per tag
        instead of once a minute.

        _app_held_logged is PROCESS-LIFETIME, and deliberately so: it is set in
        __init__ beside the other update fields, so the explanation reappears
        once per restart. That is the right scope rather than an oversight — a
        fresh process has an empty log panel, and an operator who restarts into
        a still-held state is exactly the one owed the sentence explaining why
        nothing is updating. Once per launch, not once a minute."""
        self._app_latest = ""
        self._app_update_url = ""
        self._app_update_size = 0
        self._app_update_tag = ""
        self._app_update_status = ""
        if tag and tag != self._app_held_logged:
            self._app_held_logged = tag
            self._log(
                f"Update {tag} is held — you went back from it. Resume "
                "updates to install it again."
            )
        self._refresh_sidebar()

    def _on_update_found(self, tag: str, url: str) -> None:
        """A newer version exists: download it in the background right away,
        then ask before installing (see _when_update_ready)."""
        # A DELIBERATE REVERT IS A STANDING "NOT THAT RELEASE" (PS-208), and
        # this is the one place every discovery path converges before anything
        # is fetched, staged or installed — the 60s poll, the manual check and
        # the version-line click all end here. So the hold is read here rather
        # than only on the unattended arm, mirroring app.py's engine gate at
        # `if engine.pinned_build(): return False`.
        #
        # Placed ABOVE the staged short-circuit deliberately: the installer for
        # the rejected tag is very likely still on disk (find_ready_staged is
        # tag-keyed and the revert does not delete it), so a gate below it would
        # let the rejected build through the readiest path of the three.
        #
        # Without this the revert lasts under a MINUTE. The restart it demands
        # resets _app_latest to "" (__init__), the next poll sees the rejected
        # tag as "not seen yet" AND as newer than the restored build, and on
        # Linux with auto-update on _when_update_ready installs it with nobody
        # present. That is the whole failure the hold exists to end.
        if app_update.update_held(tag):
            self._clear_held_discovery(tag)
            return
        # Always refresh the sidebar so the "new version" badge shows.
        self._refresh_sidebar()
        if not app_update.can_self_update():
            # running from source: just surface it, can't self-update
            self._log(f"New version {tag} available (update from source).")
            return
        # A previous run may have already finished downloading this update; if a
        # complete staged file is on disk, offer it instead of downloading again
        # (e.g. the user reopened the app before it restarted).
        if not self._update_staged:
            ready = app_update.find_ready_staged(
                url, size=self._app_update_size, tag=self._app_update_tag
            )
            if ready:
                self._set_update_staged(ready)
                self._app_update_status = "ready"
                self._log(f"Update {tag} ready — restart to apply.")
                self._refresh_sidebar()
                self._when_update_ready(tag, ready)
                return
        self._log(f"New version {tag} found — downloading...")
        self._start_app_update(url)

    def _start_app_update(self, url: str) -> None:
        import threading

        if self._update_in_progress:
            return
        self._set_update_in_progress(True)

        def work() -> None:
            import time

            # Reset the in-flight flag in finally: a transient raise in
            # download_update (conn reset, disk full, staging perm) would
            # otherwise leave it True forever, dead-ending every later update.
            try:
                self._update_start_t = time.monotonic()
                self._app_update_status = "downloading"
                self._refresh_sidebar()
                staged = app_update.download_update(
                    url, progress=self._update_progress_cb, size=self._app_update_size,
                    tag=self._app_update_tag,
                )
                if staged and not app_update.verify_staged_installer(
                    staged, tag=self._app_update_tag, log=self._log
                ):
                    try:
                        os.remove(staged)  # a full-size corrupt file would otherwise
                    except OSError:        # be matched again by find_ready_staged
                        pass
                    staged = ""
                if staged:
                    self._set_update_staged(staged)
                    self._app_update_status = "ready"
                    self._log("Update downloaded.")
                    self._refresh_sidebar()
                    self._when_update_ready(self._app_latest, staged)
                else:
                    self._app_update_status = "failed"
                    # clear the seen tag so the periodic check re-triggers a fresh
                    # download next cycle (it skips tags it already announced)
                    self._app_latest = ""
                    self._log("Update download failed — will retry.")
                    self._refresh_sidebar()
            except Exception as e:
                self._app_update_status = "failed"
                self._app_latest = ""
                self._log(f"Update download failed: {e}")
                self._refresh_sidebar()
            finally:
                self._set_update_in_progress(False)

        threading.Thread(target=work, daemon=True).start()

    def _on_version_click(self) -> None:
        """Click the version line to act now instead of waiting on the 60s
        poll (#228): install a staged update, resume a download that stalled
        over a slow Tor circuit, or check the repo for a newer release."""
        if self._update_staged:
            self._apply_update_now()
            return
        if self._update_in_progress:
            return  # a download is live; the resume loop handles the stalls
        if self._app_latest and self._app_update_url:
            # a newer version is known but not yet on disk — (re)start the
            # download; it resumes from any partial left on disk
            self._log(f"Resuming download of {self._app_latest}...")
            self._start_app_update(self._app_update_url)
            return
        self._check_app_update_now()

    def _check_app_update_now(self) -> None:
        """Force one update check off the UI thread, outside the 60s poll."""
        import threading

        def work() -> None:
            self._log("Checking for updates...")
            try:
                tag, url, size = app_update.check_for_update()
            except Exception:
                tag = url = ""
                size = 0
            if tag and url and tag != app_update.APP_VERSION:
                self._app_latest = tag
                self._app_update_url = url
                self._app_update_size = size
                self._app_update_tag = tag
                self._on_update_found(tag, url)
            else:
                self._log("persona is up to date.")

        threading.Thread(target=work, daemon=True).start()

    def _when_update_ready(self, tag: str, staged: str) -> None:
        """A verified update is staged. The Linux AppImage with auto-update on
        installs unattended when idle (headless boxes rely on that); everywhere
        else the user decides, so ask first."""
        # Second reading of the hold, and NOT redundant with the one in
        # _on_update_found. This is the arm where being wrong costs the most —
        # on Linux it installs with no dialog and no operator — and it is
        # reachable from callers that did not come through that gate: the
        # download thread in _start_app_update calls this directly on
        # completion, so a hold recorded WHILE a download was in flight (the
        # operator clicking "go back" mid-download) would otherwise be honoured
        # nowhere. A guard on the unattended path is the one place worth
        # paying for twice.
        if app_update.update_held(tag):
            self._app_update_status = ""
            self._log(
                f"Update {tag} is held — you went back from it. Resume "
                "updates to install it again."
            )
            self._refresh_sidebar()
            return
        if (
            _platform.IS_LINUX
            and app_settings.is_auto_update_enabled()
            and len(self.bl.running_profile_names()) == 0
        ):
            self._log("Restarting into the new version...")
            self._apply_update(staged)
            return
        self._offer_install(tag, staged)

    def _offer_install(self, tag: str, staged: str) -> None:
        from .dialogs.update_ready import open_update_ready_dialog

        # Don't stack the install prompt on top of the first-run onboarding
        # (#226) — hold it and offer it the moment onboarding finishes.
        if self._onboarding_open:
            self._pending_update = (tag, staged)
            return

        def on_install() -> None:
            import threading

            self._log("Installing update...")
            # off the UI thread: the install re-verifies the file (sha256 +
            # a checksum fetch), which must not freeze the window
            threading.Thread(
                target=lambda: self._apply_update(staged), daemon=True
            ).start()

        def show() -> None:
            page = self.page
            if page is None:
                return
            open_update_ready_dialog(page, tag=tag, on_install=on_install)

        self._ui(show)

    def _update_progress_cb(self, done: int, total: int) -> None:
        import time

        elapsed = max(time.monotonic() - self._update_start_t, 0.001)
        self._app_update_done = done
        self._app_update_total = total
        self._refresh_sidebar()

    def _set_update_staged(self, staged: str) -> None:
        """The single writer for _update_staged, so a rollback status line can
        never outlive the state it describes.

        WHY THIS IS A SETTER AND NOT A GATE AT THE RENDER SITE. The defect is
        the REVERSE ORDER, which is the canonical sequence for this feature:
        you revert precisely BECAUSE a release was bad, and upstream then ships
        the fix. The revert leaves "restart to run the previous version" on the
        panel; the poll then stages the fix and adds "[ restart to update ]"
        beside it, telling the operator to restart into two opposite versions
        with no way to tell which wins.

        Gating the status render on `not self._update_staged` would suppress
        that pair, and would ALSO suppress "can't go back while an update is
        pending" — which must render EXACTLY when an update is pending, since
        it is the refusal explaining a click the guard just swallowed. That
        gate would silently reintroduce the dead-button defect one gesture
        over. So the fix is on the WRITE, not the read: the moment the staged
        pointer changes, whatever the operator was last told about a rollback
        stopped being true, because every one of those messages is about an
        action they can no longer coherently take.

        Clearing on the transition in BOTH directions is deliberate: the
        un-stage direction matters as much as the stage one, because
        `_apply_update` un-stages through here when a verify-refusal deleted
        the file, which retires "can't go back while an update is pending"
        once nothing is staged any more.

        THE SCOPE OF THAT LAST SENTENCE, STATED EXACTLY, because an earlier
        version of this docstring overclaimed it and a reader who trusts an
        overclaim stops checking. This setter retires the sticky refusal only
        on the `_update_staged` route. The guard at `_on_app_rollback` is a
        two-arm disjunction (`_update_in_progress or _update_staged`), and the
        download-failure path never writes `_update_staged` at all, so this
        setter never fires on it. The other arm is retired by
        `_set_update_in_progress` below, which exists for that reason."""
        self._update_staged = staged
        self._app_rollback_status = ""

    def _set_update_in_progress(self, running: bool) -> None:
        """The single writer for _update_in_progress, for exactly the reason
        _set_update_staged is the single writer for the other arm of the same
        guard: a rollback status line must not outlive the state it describes.

        WHY THIS FLAG NEEDS ITS OWN SETTER. The refusal at _on_app_rollback is
        written when EITHER flag is set, but only one of them had a clearing
        rule. A download that FAILS never writes _update_staged — it goes down
        the `else` arm to _app_update_status = "failed" and then clears this
        flag in its `finally` — so the staged setter never fires, and the
        refusal survives an update that is no longer pending. The panel then
        offers "go back to the previous version" and, directly beneath it,
        explains that you can't: the live gesture and a false denial of it in
        the same box.

        BOTH transitions are legitimate clear points, and they retire
        different messages. Going True retires "restart to run the previous
        version" — a download starting makes that stale for the same reason
        staging does, since the operator is being told to restart into a
        version they are in the act of replacing. Going False retires "can't
        go back while an update is pending", which is simply false once
        nothing is pending.

        THERE IS NO SUPPRESSION TRAP ON THIS ARM, which is what makes clearing
        on the write safe here. The mirror-image concern on _set_update_staged
        was that a gate could erase the very refusal it explains; here the
        ordering rules that out — _on_app_rollback reads this flag, writes the
        refusal AFTER it, and returns, so it never re-enters this setter and
        cannot erase its own message."""
        self._update_in_progress = running
        self._app_rollback_status = ""

    def _apply_update(self, staged: str) -> None:
        """Launch the staged installer/AppImage. apply_and_restart doesn't return
        on success (the process is replaced), so reaching here means it failed.
        If the staged file is gone — a verify-refusal deleted a corrupt installer
        — clear our pointer so the periodic checker re-downloads a fresh one next
        cycle (otherwise the update is wedged: the check is gated on
        `not self._update_staged`). If the file is still there (relaunch failed),
        keep the 'ready' state so the restart button still works."""
        # Restarting via execv/os._exit skips atexit, so shut the engines down
        # explicitly first — otherwise the running browser processes and proxy
        # bridges are orphaned across the update. And don't restart on top of an
        # in-flight engine download: execv would kill the writer mid-extract and
        # leave a corrupt engine cache.
        if getattr(self, "_engine_busy", False) or getattr(
            self, "_engine2_busy", False
        ):
            self._log("Engine download in progress — restart deferred.")
            self._app_update_status = "ready"
            self._refresh_sidebar()
            return
        with contextlib.suppress(Exception):
            self.bl.shutdown_all()
        app_update.apply_and_restart(staged, log=self._log)
        if not staged or not os.path.isfile(staged):
            self._set_update_staged("")
            self._app_update_status = ""
        else:
            self._app_update_status = "ready"
        self._refresh_sidebar()

    def _apply_update_now(self) -> None:
        """Manual 'update now' click: download if needed, then restart.

        THE THIRD HOLD GATE, and defence in depth rather than duplication
        (PS-208). The argument _when_update_ready makes for its own second
        reading — "reachable from callers that did not come through
        _on_update_found" — applies to this method with more force, because it
        is the only one of the three that installs on a DIRECT operator click:
        it is _update_button's on_click and _on_version_click's staged arm, and
        neither routes through _on_update_found or _when_update_ready. So
        without a gate here the discovery gates can be perfectly correct and a
        held build still installs.

        HONEST SCOPE, because an earlier draft of this comment overclaimed it
        and an overclaim is worse than no comment — a reader who trusts one
        stops checking. I could NOT construct a reachable state in which this
        gate is the only thing standing between a held release and an install.
        The staged arm looks like one, but is not: _on_app_rollback refuses to
        revert while _update_staged OR _update_in_progress is set, so no hold
        can be written while an installer is staged, and _update_staged is
        process-lifetime ("" in __init__) so the mandatory restart empties it.
        Every writer of it (_on_update_found, _start_app_update, and this
        method) now sits downstream of a hold gate.

        So this is DEFENCE IN DEPTH against a state that is currently
        unreachable by construction, not a live hole being closed. It is worth
        its four lines anyway: the three guards that make it unreachable are in
        two other modules and none of them exist to protect the hold, so a
        later change to any one of them would reopen this path silently. The
        cost of being wrong here is installing the exact build the operator
        walked away from.

        Reads _app_update_tag first and falls back to _app_latest, because the
        staged arm is reachable with _app_latest already emptied. An unknown
        tag ("" from both) is NOT held: update_held("") is False, so a click
        with nothing identifiable proceeds exactly as it does today."""
        held_tag = self._app_update_tag or self._app_latest
        if app_update.update_held(held_tag):
            self._clear_held_discovery(held_tag)
            return
        if self._update_staged:
            self._log("Restarting into the new version...")
            self._apply_update(self._update_staged)
        elif self._app_update_url and not self._update_in_progress:
            # download then restart regardless of running profiles (user asked)
            import threading

            def work() -> None:
                import time

                self._set_update_in_progress(True)
                try:
                    self._update_start_t = time.monotonic()
                    self._app_update_status = "downloading"
                    self._refresh_sidebar()
                    staged = app_update.download_update(
                        self._app_update_url,
                        progress=self._update_progress_cb,
                        size=self._app_update_size,
                        tag=self._app_update_tag,
                    )
                    if staged:
                        self._set_update_staged(staged)
                        self._log("Update downloaded — restarting...")
                        self._apply_update(staged)
                    else:
                        self._app_update_status = "failed"
                        self._log("Update download failed — try again.")
                        self._refresh_sidebar()
                except Exception as e:
                    self._app_update_status = "failed"
                    self._log(f"Update download failed: {e}")
                    self._refresh_sidebar()
                finally:
                    self._set_update_in_progress(False)

            threading.Thread(target=work, daemon=True).start()

    def _set_auto_update(self, enabled: bool) -> None:
        app_settings.set_auto_update_enabled(enabled)
        self._refresh_sidebar()
        # if turning on and an update is already known + idle, kick it off
        if (
            enabled
            and self._app_update_url
            and not self._update_in_progress
            and not self._update_staged
            and len(self.bl.running_profile_names()) == 0
        ):
            self._start_app_update(self._app_update_url)

    def _ensure_api_server(self):
        """The Claude control server, built on first need from the factory (its
        FastAPI/uvicorn import is kept off the startup path). Returns None only
        when neither a server nor a factory was supplied (some tests)."""
        if self.api_server is None and self._api_server_factory is not None:
            self.api_server = self._api_server_factory()
        return self.api_server

    def stop_api_server(self) -> None:
        """Stop the server on shutdown — only if it was ever built."""
        if self.api_server is not None:
            self.api_server.stop()

    def _server_running(self) -> bool:
        return bool(self.api_server is not None and self.api_server.is_running)

    def _set_server(self, enabled: bool) -> None:
        server = self._ensure_api_server()
        if server is None:
            return
        if enabled and not server.is_running:
            server.start()
            self._log("Claude control server started")
        elif not enabled and server.is_running:
            server.stop()
            self._log("Claude control server stopped")
        app_settings.set_server_enabled(enabled)
        self._render_active_page()
        self._safe_update()

    def _start_server_if_enabled(self) -> None:
        # Build + start (if the user left it enabled) in the background so the
        # FastAPI/uvicorn import never blocks the just-shown window.
        if app_settings.is_server_enabled():
            threading.Thread(
                target=lambda: self._set_server(True), daemon=True
            ).start()

    def _install_close_guard(self, page: ft.Page) -> None:
        """Intercept the window's close so it can ask first (PS-223 outcome 2).

        ``prevent_close`` makes the X emit a CLOSE event instead of ending the
        process, so the handler below decides. Every failure path here ends in
        the window closing normally: a guard that cannot be installed must
        leave persona closable, never wedge it shut.
        """
        try:
            page.window.prevent_close = True
            page.window.on_event = self._on_window_event
        except Exception:
            logger.exception(
                "Could not install the close guard; persona will close without "
                "asking about open browsers"
            )

    def _on_window_event(self, e) -> None:
        """Window events; only CLOSE is acted on.

        The flow, and why each branch ends where it does:

        * **No browsers open** — destroy immediately. Asking a question with
          one possible answer is not a safety feature, it is a nuisance, and a
          user who meets a pointless dialog on every exit learns to dismiss the
          one that matters without reading it.
        * **Browsers open** — ask, naming them. Cancel leaves everything
          running and the window open.
        * **Anything raises** — destroy. See _install_close_guard: with
          ``prevent_close`` set, a handler that throws before calling destroy()
          leaves a window that CANNOT BE CLOSED. Failing toward closing is the
          only safe direction once the close has been intercepted.
        """
        try:
            if getattr(e, "type", None) != ft.WindowEventType.CLOSE:
                return
        except Exception:
            return

        page = self.page
        try:
            names = sorted(self._open_browser_names())
            if not names:
                self._destroy_window()
                return

            from .dialogs.exit_confirm import open_exit_confirm_dialog

            def _confirmed() -> None:
                # shutdown_all is the SAME teardown atexit runs — the PS-192
                # process-group reap — reached here explicitly because the user
                # said yes. Not a second, weaker path: the one that already
                # works, invoked on a gesture instead of on process exit.
                with contextlib.suppress(Exception):
                    self.bl.shutdown_all()
                # AND THE SURVIVORS, WHICH shutdown_all STRUCTURALLY CANNOT
                # REACH. It reaps _active_sessions, and a browser inherited
                # from a previous persona is by definition not in it. The
                # dialog above named those profiles in the sentence "closing
                # persona will close the browser(s) for: ...", so without this
                # the promise was false for exactly the browsers this ticket is
                # about. THIS CLICK IS THE CONSENT: the user was shown the
                # names and pressed "close them and exit", which is what makes
                # killing a process we did not start legitimate here — the
                # ticket forbids a SILENT kill, not a requested one.
                stubborn: list[str] = []
                with contextlib.suppress(Exception):
                    stubborn = self.bl.close_all_survivors()
                if stubborn:
                    # Said out loud rather than swallowed. We are on our way out
                    # so the log is the only surface left, but a browser we
                    # promised to close and did not is exactly the thing a user
                    # reading a log after the fact needs to find. Its registry
                    # record is deliberately kept (close_all_survivors forgets
                    # only what closed), so the next start still guards it.
                    logger.warning(
                        "Could not close the leftover browser(s) for: %s; "
                        "they may still be running.", ", ".join(stubborn),
                    )
                self._destroy_window()

            assert page is not None
            open_exit_confirm_dialog(page, names, _confirmed)
        except Exception:
            logger.exception(
                "The close-confirmation dialog failed; closing persona rather "
                "than leaving a window that cannot be closed"
            )
            self._destroy_window()

    def _open_browser_names(self) -> set[str]:
        """Profiles with a browser open RIGHT NOW — this session's and survivors'.

        Survivors are included because closing persona is about to close them
        too: ``shutdown_all`` reaps what this process launched, and a survivor
        it cannot reach is still a window the user is about to lose sight of.
        Naming only the sessions we happen to hold a handle for would
        under-report exactly the browsers this ticket is about.
        """
        names: set[str] = set()
        with contextlib.suppress(Exception):
            names |= set(self.bl.running_profile_names())
        with contextlib.suppress(Exception):
            names |= {r.profile for r in self.bl.survivors()}
        return names

    def _destroy_window(self) -> None:
        """Close for real, past the ``prevent_close`` interception."""
        page = self.page
        if page is None:
            return
        try:
            page.window.destroy()
        except Exception:
            logger.exception("Could not destroy the window on close")

    def _scan_survivors(self) -> None:
        """Startup: find browsers a PREVIOUS persona left running, and SAY SO.

        The user should learn about a survivor from persona, not from a launch
        that fails with chromium's own words. Measured on a real engine
        (PS-223): a second launch against a live profile dir aborts with exit
        21 and ``Failed to create a ProcessSingleton for your profile
        directory`` — which names no profile, offers no action, and leaves
        persona still believing nothing is running.

        NEITHER SILENTLY ADOPTED NOR SILENTLY KILLED, which is the ticket's
        boundary and the reason this only logs. The survivor becomes visible
        (the card renders as running, and its [ close ] ends it) and the user
        decides. Killing it here would destroy work in a browser nobody asked
        us to close; ignoring it would hand back the double launch.

        The INDETERMINATE bucket is reported separately and blocks nothing —
        those launches stay allowed, so the user is told the check could not be
        made rather than being left to assume it passed.

        Never raises: a survivor scan that fails must cost the guard, not the
        startup it runs inside.
        """
        try:
            survivors, unknown = self.bl.scan_survivors()
        except Exception:
            logger.exception(
                "Could not scan for browsers left running by a previous session"
            )
            return
        if survivors:
            self._log(
                get_string(
                    "survivors_found",
                    count=len(survivors),
                    names=", ".join(sorted(r.profile for r in survivors)),
                )
            )
        if unknown:
            self._log(
                get_string(
                    "survivors_unknown",
                    count=len(unknown),
                    names=", ".join(sorted(r.profile for r in unknown)),
                )
            )

    def _show_startup_notice(self) -> None:
        """First frame after the UI builds: show the full onboarding on a real
        first run, a short what's-new changelog after an update, or nothing when
        the version is unchanged (#214/#215). The current version is recorded
        LAST so the next start knows what the user has already seen — a lost
        onboarding_done no longer re-triggers the welcome (#214)."""
        from .changelog import notes_for
        from .startup_notice import Notice, decide_startup_notice

        current = app_update.APP_VERSION
        notice = decide_startup_notice(
            onboarding_done=app_settings.is_onboarding_done(),
            last_version=app_settings.last_seen_version(),
            current_version=current,
            # Existing profiles prove a prior run even if settings.json read
            # empty on a self-update relaunch — never re-onboard over real data.
            has_profiles=bool(self.pm.list_profiles()),
        )
        if notice is Notice.ONBOARDING:
            self._show_onboarding()
        elif notice is Notice.CHANGELOG:
            notes = notes_for(current)
            if notes:
                from .dialogs.changelog import open_changelog_dialog

                page = self.page
                if page is not None:
                    open_changelog_dialog(page, current, notes)
        # Record what this session runs so the next start can tell first-run
        # from update from unchanged. NOT for ONBOARDING: _show_onboarding opens
        # a non-blocking dialog and returns here immediately, so recording the
        # version now would mark first-run "seen" before the user finishes (or
        # even sees) it. Quit mid-welcome and decide_startup_notice would never
        # return ONBOARDING again — and the engine bootstrap, gated behind the
        # still-False onboarding flag, would be dead too. on_finish() records the
        # version when onboarding actually completes.
        if notice is not Notice.ONBOARDING:
            app_settings.set_last_seen_version(current)

    def _show_onboarding(self) -> None:
        page = self.page
        assert page is not None

        def start_engine(progress, done) -> None:
            def work() -> None:
                if engine.is_installed():
                    done(True)
                    return
                self._engine_busy = True
                self._engine_progress_start()

                # mirror progress to BOTH the onboarding dialog and the
                # sidebar panel, so if the user closes onboarding mid-
                # download the sidebar keeps showing live progress instead
                # of a bare 'unknown'.
                def both(done_bytes, total):
                    try:
                        progress(done_bytes, total)
                    except Exception:
                        pass
                    self._engine_progress_cb(done_bytes, total)

                # Reset _engine_busy in finally: a raise in ensure_engine would
                # otherwise wedge it True forever, dead-ending every later
                # engine download/launch this session.
                ok = False
                try:
                    ok, _msg = engine.ensure_engine(progress=both, log=self._log)
                    if ok:
                        self._engine_latest = engine.current_version()
                except Exception as e:
                    self._log(f"Engine install failed: {e}")
                finally:
                    self._engine_busy = False
                self._refresh_engine_text()
                self._refresh_sidebar()
                done(ok)

            threading.Thread(target=work, daemon=True).start()

        def on_finish() -> None:
            self._onboarding_open = False
            app_settings.mark_onboarding_done()
            # Record the version only now that onboarding actually completed, not
            # at dialog-open — so an early quit re-triggers the welcome (#214,
            # audit5 #2) instead of silently skipping it forever.
            app_settings.set_last_seen_version(app_update.APP_VERSION)
            # if the operator skipped the download, fetch in the background
            if not engine.is_installed() and not self._engine_busy:
                self._check_engine_async()
            # both engines are required: pull the Firefox engine too
            self._ensure_engine2_async()
            self._refresh_engine_text()
            self._safe_update()
            # an update that staged while onboarding was open was held back so
            # the dialogs wouldn't stack — offer it now (#226)
            pending = self._pending_update
            if pending is not None:
                self._pending_update = None
                self._offer_install(*pending)

        self._onboarding_open = True
        ob = Onboarding(
            page,
            on_finish=on_finish,
            start_engine=start_engine,
            engine_already_installed=engine.is_installed(),
            on_ui=self._ui,
        )
        ob.open()

    def _refresh_sidebar(self) -> None:
        # Rebuilding the sidebar swaps a control subtree; do the swap and the
        # update together on the UI thread so a worker can't tear the tree
        # mid-patch.
        def apply() -> None:
            if self._sidebar_host is not None:
                self._sidebar_host.content = self._build_sidebar()
                self._safe_update()

        self._ui(apply)

    def _engine_progress_start(self) -> None:
        import time

        self._engine_start_t = time.monotonic()
        self._engine_throttle = pf.ProgressThrottle()
        self._engine_pstate = pf.ProgressState()
        self._engine_bar.value = None
        self._engine_detail.value = ""
        # _engine_busy is already True here; rebuild so the progress
        # bar/detail are inserted into the sidebar tree.
        self._refresh_sidebar()

    def _engine_progress_cb(self, done: int, total: int) -> None:
        import time

        now = time.monotonic()
        # Feed every chunk into the smoothed, monotonic state (cheap), but only
        # repaint when the throttle allows — a chunk-rate repaint flickers the
        # sidebar. The state keeps percent from jumping backwards on a retry and
        # EMA-smooths the speed so the numbers move steadily.
        self._engine_pstate.update(done, total, now)
        if not self._engine_throttle.should_emit(done, total, now):
            return
        st = self._engine_pstate
        self._engine_bar.value = st.fraction
        # With a known size show a percentage; when the server omits
        # Content-Length (common over Tor) show the live downloaded amount
        # so it's obvious bytes are flowing rather than a bar spinning idle.
        label = f"{st.percent}%" if st.total > 0 else pf.fmt_mb(st.done)
        self.engine_text.value = f"downloading {label}"
        self._engine_detail.value = st.line()
        # The bar/detail/text controls are already in the sidebar tree, so
        # updating their .value and pushing the page reflects the change
        # without rebuilding the whole sidebar on every chunk (which made
        # unrelated controls flicker).
        self._safe_update()

    def _engine_tree_in_use(self) -> bool:
        """True while any profile is running — i.e. while a Chromium install
        would be replacing the tree a live session is executing FROM.

        Chromium keeps ONE un-versioned tree: a launch runs
        ENGINE_DIR/<binary> directly (services/browser/process.py's
        FINGERPRINT_CHROMIUM, passed as argv[0]), and every install path
        replaces entries of that same dir in place — os.replace onto the Linux
        AppImage, per-entry os.replace in _promote_staging on Windows,
        os.replace onto Chromium.app on macOS. POSIX does not refuse an
        os.replace over a file a running process has open, so on Linux/macOS
        that swap is SILENT; only Windows locks a running executable and turns
        it into a loud, recoverable failure. (Firefox sidesteps all of this by
        keeping each build in its own versioned dir, which is why its unattended
        fetch is safe by construction — see PS-43 for the full comparison.)

        THIS CHECK IS AN OPTIMISATION, NOT THE GUARD. It answers about the
        moment the FETCH is decided, and the download that follows takes
        minutes — a profile can launch inside that window, which would make
        acting on this answer a TOCTOU. The real guard is re-asked inside
        updater.download_engine, under the install lock, adjacent to the
        replacement (see updater.set_in_use_provider). What this saves is a
        pointless multi-hundred-MB download while a profile is obviously
        already running.

        Fails CLOSED (a broken oracle reports "in use"), which is deliberately
        the OPPOSITE of engine_install._engine_in_use's fail-open default. The
        two are protecting different things at different costs: there, a raising
        provider must not permanently wedge DISK RECLAMATION, and the cost of
        proceeding is bounded. Here, the only cost of a false "in use" is that
        an unattended update waits for the next hourly check — while the cost of
        a false "idle" is swapping the binary under a running browser.
        """
        try:
            return len(self.bl.running_profile_names()) > 0
        except Exception:
            logger.exception(
                "Could not tell whether a profile is running; "
                "treating the engine tree as in use"
            )
            return True

    def _auto_update_engine(self) -> None:
        """Fetch an acceptable newer Chromium build without being asked.

        The check paths only ever RECORDED a verdict (_record_engine_check) and
        the fetch routine had exactly one caller: the click handler on the
        engine row. So an operator who never read that row kept an old engine
        forever — nothing errored, nothing degraded, it just silently stalled.
        This is the missing unattended caller, shaped after the Firefox startup
        path (_auto_update_engine2): decide in the background, act when the
        answer is yes, surface instead of fetching when it is no.

        It adds no policy of its own. _engine_update_available() already folds
        in engine_policy.is_installable, and the refusal messages ("update
        persona for the newest engine" / "engine update blocked" / the network's
        "Engine update failed") are the ones the check and _update_engine_async
        already produce — a refused build is skipped here and stays visible in
        the row, rather than getting a second vocabulary for the same outcomes.

        Runs on the CALLER's thread: _update_engine_async claims _engine_busy
        synchronously before spawning its worker, so a click landing during an
        in-flight background fetch is refused by that same flag (#234) rather
        than starting a second download.

        Deliberately NOT the cold-start path: an app with no engine at all is
        worse than one with an untested engine, so a missing engine keeps its
        own unattended download (_download_engine_fresh) with its own refusal
        handling. Hence the is_installed() guard — this only ever UPGRADES.

        SAFETY AGAINST A LIVE SESSION IS NOT DECIDED HERE. Chromium keeps one
        un-versioned tree and installing replaces it in place, so an unattended
        install must not land while a profile is executing from it. The check
        below is only a cheap early exit — the download it precedes takes
        minutes, and a profile can launch inside that window, so acting on this
        answer alone would be a TOCTOU. The binding guard is re-asked inside
        download_engine under the install lock, immediately before the
        replacement (unattended=True is what arms it), and raises InstallDeferred
        there. The verified asset stays on disk, so the retry that follows
        installs without downloading again.
        """
        if self._engine_busy or not engine.is_installed():
            return
        if not self._engine_update_available():
            # Nothing to do, or persona refused this build. Either way the
            # check has already recorded the state and said so in the log.
            return
        if self._engine_tree_in_use():
            # Don't even spend the bytes while a profile is obviously already
            # running — the install would only defer at the end of it. Logged at
            # most once per build (see _engine_deferred_tag): the hourly check
            # retries, so an operator who keeps a profile open all day would
            # otherwise get this identical line every hour, forever.
            if self._engine_deferred_tag != self._engine_latest:
                self._engine_deferred_tag = self._engine_latest
                self._log(
                    f"Chromium engine {self._engine_latest} ready — waiting for "
                    "running profiles to close before updating"
                )
            return
        self._log(
            f"Chromium engine {engine.current_version() or 'unknown'} is out of "
            f"date — fetching {self._engine_latest}"
        )
        self._update_engine_async(unattended=True)

    def _check_engine_async(self) -> None:
        def work() -> None:
            if not engine.is_installed():
                self._download_engine_fresh()
                return
            tag, _url = engine.fetch_latest()
            line = self._record_engine_check(tag)
            if line:
                self._log(line)
            self._refresh_engine_text()
            self._auto_update_engine()

        threading.Thread(target=work, daemon=True).start()

    def _ensure_engine2_async(self) -> None:
        """Both engines are required, not optional. If the Firefox engine binary
        isn't present (fresh install, or an update that added it to an install
        that only had chromium), fetch it in the background with a visible
        status — the same first-run treatment fp-chromium gets."""
        from ..services.browser import invisible_launch as inv

        def work() -> None:
            import time

            if inv.is_invisible_installed():
                # nothing to fetch — release the flag the click may have claimed
                # synchronously (#234) so the row is interactive again.
                self._engine2_busy = False
                return
            # Everything after claiming the flag runs under try/finally so a rare
            # exception (a refresh_sidebar failure, an unexpected raise) can't
            # leave _engine2_busy stuck True and wedge the Firefox-engine row for
            # the whole session (audit5 LOW — the other engine flows use finally).
            ok = False
            try:
                self._engine2_busy = True
                self._engine2_status = "downloading..."
                self._engine2_start_t = time.monotonic()
                self._engine2_throttle = pf.ProgressThrottle()
                self._engine2_pstate = pf.ProgressState()
                self._engine2_bar.value = None
                self._engine2_detail.value = "connecting..."
                self._log("Firefox engine not found — downloading...")
                self._refresh_sidebar()
                # the binary is ~80MB over Tor; retry a few times so a dropped
                # circuit doesn't leave the (required) engine uninstalled
                for attempt in range(3):
                    try:
                        ok = inv.ensure_invisible_installed(
                            progress=self._engine2_progress_cb, log=self._log
                        )
                    except Exception as e:
                        self._log(f"Firefox engine download error: {e}")
                        ok = False
                    if ok:
                        break
                    if attempt < 2:
                        self._log("Firefox engine download interrupted — retrying...")
            finally:
                self._engine2_busy = False
            self._engine2_detail.value = ""
            if ok:
                # Show the installed version straight away — clearing the status
                # first would flash "not installed" until the version resolved.
                self._engine2_status = inv.installed_version()
                self._log(f"Firefox engine installed: {inv.installed_version()}")
            else:
                self._engine2_status = ""
                self._log("Firefox engine download failed — will retry on next start")
            self._refresh_sidebar()

        threading.Thread(target=work, daemon=True).start()

    def _engine2_progress_cb(self, done: int, total: int) -> None:
        import time

        now = time.monotonic()
        if done > 0:
            self._engine2_pstate.update(done, total, now)
        # Always let the pre-transfer "connecting" ticks and completion through;
        # throttle only the steady stream of transfer chunks so the download
        # renders as smoothly as chromium's without flickering the sidebar.
        if done > 0 and not self._engine2_throttle.should_emit(done, total, now):
            return
        elapsed = max(now - self._engine2_start_t, 0.001)
        if done <= 0:
            # The fetch can sit a while before the first byte arrives (longest
            # over Tor). Show a ticking "connecting" so it reads as alive, not
            # frozen. No "over Tor" wording — on a direct connection (Windows/mac
            # host) there's no Tor, so the generic text is correct everywhere.
            self._engine2_bar.value = None
            self._engine2_status = "connecting..."
            self._engine2_detail.value = f"connecting... {int(elapsed)}s"
        else:
            st = self._engine2_pstate
            self._engine2_bar.value = st.fraction
            # At 100% the bytes are down but extraction still runs; show
            # "installing..." so the row reads as progressing instead of snapping
            # from a percent to a momentary blank / "not installed".
            if st.total > 0 and st.done >= st.total:
                self._engine2_status = "installing..."
                self._engine2_detail.value = "installing..."
            else:
                self._engine2_status = (
                    f"{st.percent}%" if st.total > 0 else pf.fmt_mb(st.done)
                )
                self._engine2_detail.value = st.line()
        # Bar, detail and the row's status text are live controls already in
        # the tree; update in place instead of rebuilding the sidebar on every
        # chunk (the flicker source).
        self._engine2_text.value = self._engine2_status
        self._safe_update()

    def _download_engine_fresh(self) -> None:
        """First-run: no engine installed yet, fetch it before anything can
        launch. Runs on the engine-check thread; shows progress in the panel.
        """
        self._engine_busy = True
        self._log("No browser engine found — downloading...")
        self._engine_progress_start()
        self.engine_text.value = "connecting..."
        self._refresh_sidebar()

        # Reset _engine_busy in finally so a raise in ensure_engine doesn't wedge
        # the flag True and dead-end every later launch/download this session.
        try:
            ok, _msg = engine.ensure_engine(
                progress=self._engine_progress_cb, log=self._log
            )
            if ok:
                self._engine_latest = engine.current_version()
                self._log(f"Engine installed: {engine.current_version()}")
            # No else, and the message is deliberately unused: ensure_engine has
            # already logged the reason through the `log` callback, in the only
            # words that can tell a refusal apart from a failure. Re-labelling it
            # here is what turned "persona declined this build" into "Engine
            # download failed: ..." — the wording the ticket exists to stop using
            # for a decision persona made. The message stays in the return tuple
            # for programmatic callers.
        except Exception as e:
            # A RAISE, on the other hand, genuinely is a failure and never
            # reached ensure_engine's own logging — so it is reported here.
            self._log(f"Engine download failed: {e}")
        finally:
            self._engine_busy = False
        self._engine_detail.value = ""
        self._refresh_engine_text()
        self._refresh_sidebar()

    def _on_engine_click(self) -> None:
        if self._engine_busy or self._engine_checking:
            return
        if self._engine_update_available():
            self._update_engine_async()
        else:
            # A version check moves no bytes: spinner only, no download bar.
            self._engine_checking = True
            self._refresh_engine_text("checking...")

            def work() -> None:
                # Clear the in-flight flag in finally so a network error in
                # fetch_latest() can't wedge the button on "checking..." forever.
                try:
                    tag, _url = engine.fetch_latest()
                    # Match the Firefox line's shape: name the engine and its
                    # version so the log isn't a bare, ambiguous "up to date".
                    line = self._record_engine_check(tag)
                    if line:
                        self._log(line)
                    elif self._engine_unverifiable_tag == tag:
                        # NOT "up to date" — a refused build is newer than what
                        # is installed, and saying otherwise would be a plain
                        # lie to an operator who just asked (PS-49 round 3).
                        # Suppressing the offer is what routes a click here at
                        # all, so this branch is a consequence of that gate and
                        # has to answer for it.
                        #
                        # Answered on the CLICK but not on the hourly tick, on
                        # purpose: this is an explicit gesture, and a question
                        # asked deserves an answer every time. _record_engine_check
                        # stays silent for the automatic path precisely so the
                        # same sentence doesn't accumulate once an hour forever.
                        #
                        # REPLAYS the refusal's own words rather than
                        # paraphrasing them: _unverifiable_message is the single
                        # source of this wording and names the specific asset
                        # that could not be verified. A second wording here
                        # would be a fifth message for a situation that already
                        # has exactly one, free to drift from it.
                        self._log(self._engine_unverifiable_msg)
                    else:
                        self._log(
                            f"Chromium engine is up to date ({engine.current_version()})"
                        )
                except Exception as e:
                    self._log(f"Chromium engine check failed: {e}")
                finally:
                    self._engine_checking = False
                    self._refresh_engine_text()

            threading.Thread(target=work, daemon=True).start()

    def _update_engine2_async(self) -> None:
        """Download the newer firefox-NN build. It lands in its own versioned
        cache dir with a completion marker written last, so the active build is
        untouched until the new one is whole; the next launch picks it up.

        A successful download then prunes superseded builds — which would delete
        the build a profile started on the PREVIOUS build is executing from, so
        pruning defers entirely while any profile runs (the guard wired in
        _wire_engine_prune_guard). Disk is reclaimed on a later prune instead."""
        from ..services.engine import firefox as ff_engine

        tag = self._engine2_latest

        def work() -> None:
            import time

            self._engine2_busy = True
            self._engine2_status = "downloading..."
            self._engine2_start_t = time.monotonic()
            self._engine2_throttle = pf.ProgressThrottle()
            self._engine2_pstate = pf.ProgressState()
            self._engine2_bar.value = None
            self._engine2_detail.value = "connecting..."
            self._log(f"Downloading Firefox engine {tag}...")
            self._refresh_sidebar()
            ok = False
            # Reset _engine2_busy in finally so a raise in download_engine can't
            # wedge it True and dead-end every later FF-engine download.
            try:
                for attempt in range(3):
                    ok = ff_engine.download_engine(
                        tag, progress=self._engine2_progress_cb, log=self._log
                    )
                    if ok:
                        break
                    if attempt < 2:
                        self._log("Firefox engine download interrupted — retrying...")
            except Exception as e:
                self._log(f"Firefox engine download failed: {e}")
            finally:
                self._engine2_busy = False
            self._engine2_detail.value = ""
            self._engine2_status = ""
            if ok:
                self._log(f"Firefox engine updated to {tag}")
            else:
                self._log("Firefox engine update failed — will retry on next check")
            self._refresh_sidebar()

        threading.Thread(target=work, daemon=True).start()

    def _update_engine_async(self, unattended: bool = False) -> None:
        """Fetch and install the newest acceptable Chromium build.

        `unattended` marks the background caller (_auto_update_engine) rather
        than the operator's click. It changes exactly one thing: the install is
        allowed to DEFER if a profile is running when the bytes are ready. The
        click never defers — the operator asked for it, and a silent no-op would
        look like the stall this work exists to remove.

        The deferral decision deliberately does NOT live here. Asking "is a
        profile running?" before the download and installing on that answer
        minutes later is a TOCTOU — a profile can launch inside the window. So
        the question is asked inside download_engine, under the install lock,
        adjacent to the replacement itself."""
        self._engine_busy = True
        self._engine_progress_start()
        self._refresh_engine_text("downloading...")
        self._log(f"Downloading engine {self._engine_latest}...")

        def work() -> None:
            # Reset _engine_busy in finally so a raise in fetch/download can't
            # wedge it True and dead-end every later engine action this session.
            try:
                tag, url, digest, verdict, message = engine.fetch_latest_checked()
                if verdict != engine_policy.OK:
                    # persona REFUSED this build (known-bad, or above a ceiling
                    # the OPERATOR set in their policy file). Say so in those
                    # terms — "update failed" would blame the network for a
                    # decision persona made, and the operator would retry
                    # forever. persona itself ships no Chromium ceiling since
                    # PS-42, so ABOVE_CEILING points at the operator's own
                    # setting rather than at a persona update.
                    self._engine_status = (
                        "engine pinned by your policy file"
                        if verdict == engine_policy.ABOVE_CEILING
                        else "engine update blocked"
                    )
                    self._log(message)
                    return
                ok = engine.download_engine(
                    url,
                    digest=digest,
                    progress=self._engine_progress_cb,
                    defer_if_in_use=unattended,
                    log=self._log,
                    tag=tag,
                )
                if ok:
                    # Record the tag from the fetch that produced THESE BYTES,
                    # not self._engine_latest — that one came from the earlier
                    # background check and is stale if upstream published in
                    # between. version.txt feeds current_version(), is_newer()
                    # and every verification snapshot's engine_build header, so a
                    # stale value there labels a snapshot with an engine build it
                    # was not taken under, and can make the NEXT real update look
                    # already-installed.
                    # BEFORE write_version, always. version.txt has ONE slot, so
                    # the moment it is overwritten the tag of the build being
                    # replaced is gone from the machine — and that name, not the
                    # bytes, is what a rollback needs. Recording first demotes
                    # the outgoing build to "previous" while it can still be
                    # read. The digest is the one THESE BYTES verified against,
                    # so a later revert checks against it rather than trusting
                    # whatever upstream advertises for that tag by then (PS-49).
                    engine.record_installed_build(tag, digest)
                    engine.write_version(tag)
                    self._engine_latest = tag
                    # The deferral (if any) is resolved — clear it so a LATER
                    # build's deferral is announced instead of being mistaken
                    # for this one and silently swallowed.
                    self._engine_deferred_tag = ""
                    # Same for a refusal, for the same reason (PS-49). Bytes
                    # that verified are the direct contradiction of "this build
                    # cannot be verified", so the suppression must not outlive
                    # them: left set, it would go on hiding a build that just
                    # installed successfully.
                    self._engine_unverifiable_tag = ""
                    self._engine_unverifiable_msg = ""
                    self._log(f"Engine updated to {tag}")
                else:
                    self._log("Engine update failed")
            except engine.EngineUnverifiable as e:
                # A REFUSAL, NOT A FAILURE — and this is the path an EXISTING
                # operator actually hits (PS-49 round 2). The first-install path
                # goes through ensure_engine, which logs its own refusal; this
                # one calls download_engine directly, so before the refusal was
                # raised from inside the transfer this branch fell to the `else`
                # above and said "Engine update failed". That blames the network
                # for a decision persona made, about a condition retrying cannot
                # change — so the operator retries forever. The message names
                # the asset that could not be verified and says so explicitly.
                #
                # RECORDING THE TAG IS WHAT MAKES THE REFUSAL STICK (PS-49
                # round 3). Setting _engine_status alone was inert: the row's
                # renderer tests _engine_update_available() FIRST, and a
                # digest-less release is policy-OK (`policy.check` -> ('ok',
                # '')) while the refusal never calls write_version — so
                # current_version stays old, _engine_latest stays new, the
                # predicate stayed True and painted "update → NN" straight over
                # this message. The status was computed and never rendered.
                #
                # The tag also puts a lid on the repeat. _auto_update_engine
                # gates on that same predicate and nothing else, so the hourly
                # tick re-fetched and re-refused this build every hour, logging
                # the full refusal each time — forever, because unlike the
                # deferral below it does not resolve when the operator closes a
                # profile. Keyed by tag, so a NEWER build (which upstream may
                # well have published a digest for) supersedes it and is
                # offered normally.
                self._engine_unverifiable_tag = tag or self._engine_latest
                self._engine_unverifiable_msg = str(e)
                self._engine_status = "engine could not be verified"
                self._log(str(e))
            except engine.InstallDeferred:
                # NOT a failure, and must not be reported as one: the network
                # worked, the bytes are verified and on disk, and a profile was
                # running at the moment of replacement. Saying "Engine update
                # failed" here would blame the network for a decision persona
                # made — the same confusion the refuse/failed vocabulary exists
                # to prevent — and an operator would retry forever.
                #
                # Logged at most once per build (see _engine_deferred_tag): the
                # hourly poll retries, so an operator who keeps a profile open
                # all day would otherwise get this identical line eight times.
                if self._engine_deferred_tag != (tag or self._engine_latest):
                    self._engine_deferred_tag = tag or self._engine_latest
                    self._log(
                        f"Chromium engine {tag or self._engine_latest} downloaded "
                        "— installing once running profiles close"
                    )
            except Exception as e:
                self._log(f"Engine update failed: {e}")
            finally:
                # In the FINALLY, not after it: the refusal path above returns
                # from inside the try, and when these two lines sat after the
                # try/finally that return skipped both. _refresh_engine_text()
                # is the only thing that renders _engine_status, so a refusal
                # computed the right message and then never painted it — the row
                # stayed on "downloading..." with a stale byte count under it
                # until the operator clicked it (nothing recomputes the Chromium
                # row the way _build_sidebar recomputes the Firefox one). That is
                # worse than no guard at all, and it is the same leftover-progress
                # bug class the comment at _engine_progress_start warns about.
                self._engine_busy = False
                self._engine_detail.value = ""
                self._refresh_engine_text()

        threading.Thread(target=work, daemon=True).start()

    def _on_search(self, e: ft.ControlEvent) -> None:
        self._search_query = e.control.value or ""
        self.state.current_page = 1
        self._refresh_profiles()

    def _get_page_profiles(self) -> tuple[list, list, int]:
        all_profiles = filter_by_tag(
            filter_profiles(self.pm.list_profiles(), self._search_query),
            self._active_tag,
        )
        total = max(1, (len(all_profiles) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        self.state.current_page = min(self.state.current_page, total)
        start = (self.state.current_page - 1) * ITEMS_PER_PAGE
        return all_profiles, all_profiles[start : start + ITEMS_PER_PAGE], total

    def _refresh_profiles(self) -> None:
        r = self.refs
        assert r is not None
        self._update_stats()
        self._flush_log()
        all_profiles, page_profiles, total_pages = self._get_page_profiles()

        all_names = {p.name for p in all_profiles}
        for stale in self.state.selected_names() - all_names:
            self.state.toggle_selection(stale)

        r.profile_list_area.controls = (
            [
                build_profile_card(
                    p,
                    self.state.is_loading(p.name),
                    self.bl.is_running(p.name),
                    self.h.on_launch,
                    self.h.on_edit,
                    self.h.on_delete,
                    is_selected=self.state.is_selected(p.name),
                    on_select=self.h.on_toggle_select,
                    proxy=self.pstore.get(p.proxy) if p.proxy else None,
                    on_check_proxy=self._check_proxy,
                    on_notes_change=self._save_notes_inline,
                    # The LIVE session's fact, from the launcher — deliberately
                    # not p.ai_control, which set_ai_control can flip while the
                    # session runs without closing the port already bound.
                    cdp_channel_open=self.bl.cdp_channel_open(p.name),
                    # The most recent REFUSED launch, from the launcher. A
                    # refusal is the fail-closed guard firing, and it used to
                    # reach the operator only as a log line that scrolled away
                    # — leaving the card that refused identical to one that was
                    # never clicked. Like the fact above it is a dict lookup
                    # under a lock, no IO, so it is safe on this render path.
                    refusal=self.bl.last_refusal(p.name),
                    proxy_checking=(
                        p.proxy in self._checking_proxies if p.proxy else False
                    ),
                )
                for p in page_profiles
            ]
            if page_profiles
            else [build_empty_state(lambda _: self.h.open_add_dialog())]
        )
        rebuild_bulk_bar(
            r.bulk_bar,
            self.state,
            page_profiles,
            {
                "launch": self.h.on_bulk_launch,
                "stop": self.h.on_bulk_stop,
                "delete": self.h.on_bulk_delete,
                "select_page": self.h.on_select_all_page,
                "deselect_page": self.h.on_deselect_page,
                "clear": self.h.on_clear_selection,
            },
        )
        r.content_subtitle.value = self._profiles_subtitle()
        total_count = len(self.pm.profiles)
        self.count_text.value = (
            f"{len(all_profiles)}/{total_count}"
            if self._search_query.strip()
            else str(total_count)
        )
        r.page_label.value = get_string(
            "page_of",
            current=self.state.current_page,
            total=total_pages,
        )
        r.prev_btn.disabled = self.state.current_page <= 1
        r.next_btn.disabled = self.state.current_page >= total_pages
        self._refresh_tag_chips()
        self._safe_update()

    def _change_page(self, delta: int) -> None:
        self.state.current_page += delta
        self._refresh_profiles()

    def _on_wipe_all(self) -> None:
        from .dialogs.wipe_confirm import open_wipe_confirm_dialog

        count = len(self.pm.profiles)
        if not count:
            return

        def _do_wipe() -> None:
            self.pm.wipe_all_profiles()
            # wipe_all_profiles() clears the FILE log, but the Activity Log the
            # operator is looking at is an in-memory ring that is only ever
            # SEEDED from that file at startup — it accumulates independently
            # afterwards, so without this the wiped names stay on screen (both
            # the sidebar panel and the fullscreen dialog) until the next
            # launch. Cleared BEFORE _refresh_profiles() so its _flush_log()
            # repaints the emptied panel in the same pass.
            self.state.clear_log()
            self.state.current_page = 1
            self._refresh_profiles()
            self._update_stats()
            # The wipe PURGES THE TRASH IN FULL (wipe_all_profiles ->
            # _purge_trash_for_wipe -> trash.clear()), so nothing is counting
            # down any more — same reasoning as the three trash handlers. Left
            # out, the rail keeps asserting "N items are about to be destroyed"
            # over a trash that has just been destroyed in full, until the
            # operator's next navigation happens to rebuild it. An operator who
            # has typed DELETE is the last person who should be told something
            # recoverable survives.
            self._refresh_sidebar()

        open_wipe_confirm_dialog(self.page, count, _do_wipe)

    def _profiles_subtitle(self) -> str:
        r = self.bl.running_count()
        return f"* {r} running" if r else ""

    def _update_stats(self) -> None:
        r = self.refs
        if r:
            cnt = self.bl.running_count()
            r.stats_text.value = get_string(
                "total_profiles",
                count=len(self.pm.profiles),
            )
            r.running_text.value = (
                f"●  {cnt} browser{'s' if cnt != 1 else ''} running"
                if cnt
                else "○  No active sessions"
            )

    def _log(self, message: str) -> None:
        logger.info(message)
        if self.state.add_log(message):
            self.state.schedule_refresh()

    def _flush_log(self) -> None:
        """Feed the Activity Log console, and let IT decide about scrolling.

        The tail is handed over with the ring's sequence number, which is what
        turns a flush into an APPEND: the console paints only the rows that are
        genuinely new and leaves every existing row — and therefore every
        scroll position the operator has established — exactly where it is. The
        old panel replaced its whole child list here on every flush, up to every
        ~0.15s while profiles launch, which is what made the region impossible
        to read while it was busy.

        THE CONSOLE IS THE ONLY LOG SURFACE THIS PAINTS. The sidebar panel that
        used to be repainted here is gone with the log's move out of the rail,
        and the two paths once cited as its readers do not read it: the
        fullscreen dialog takes state.get_all_log_lines() (handlers.py) and the
        panic wipe calls state.clear_log() — both go to STATE, not to controls.
        So the legacy paint built 14 flet Text controls on every flush, up to
        every ~0.15s while profiles launch, into a container that was then
        hidden unconditionally: pure dead work on the one hot path this ticket
        exists to make cheaper.
        """
        text = self.state.flush_log()
        if text is not None and self.refs:
            lines = [ln for ln in text.split("\n") if ln]
            dock = getattr(self, "_dock", None)
            if dock is not None:
                with contextlib.suppress(Exception):
                    dock.set_profiles(p.name for p in self.pm.list_profiles())
                dock.render(lines, seq=self.state.log_seq())

    def _ui(self, fn) -> None:
        """Run fn on the flet session (UI) thread. Flet's control tree and
        page.update() are not thread-safe; page.run_task is the only entry
        that marshals onto the session's event loop from a worker thread
        (page.run_thread goes the other way — into the executor). When
        already on the session loop, or before a page exists, fn runs
        inline so event-handler code keeps its current ordering. Before the
        loop services its first task the callback is held in a backlog
        (flushed by _on_session_ready) — marshaling into the loop while it
        is still building the first window froze the build (#124)."""
        page = self.page
        if page is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                if not self._ui_ready.is_set():
                    with self._ui_backlog_lock:
                        if not self._ui_ready.is_set():
                            self._ui_backlog.append(fn)
                            return

                async def call() -> None:
                    try:
                        fn()
                    except Exception as e:
                        logger.error("Error in UI callback: %s", e)

                try:
                    page.run_task(call)
                except Exception as e:
                    logger.error("Error scheduling UI callback: %s", e)
                return
        try:
            fn()
        except Exception as e:
            logger.error("Error in UI callback: %s", e)

    def _safe_update(self) -> None:
        if not self.page:
            return

        def push() -> None:
            if not self.page:
                return
            try:
                with self.state._ui_update_lock:
                    self.page.update()
            except Exception as e:
                logger.error("Error updating UI: %s", e)

        self._ui(push)

    async def _ui_reconcile_loop(self) -> None:
        while self.page:
            try:
                running_now = self.bl.running_profile_names()
                changed = running_now != self.state._last_running_snapshot
                if changed:
                    self.state._last_running_snapshot = running_now
                if changed or self.state.consume_refresh():
                    self._refresh_profiles()
            except Exception as e:
                logger.error("Error in UI reconcile loop: %s", e)
            await asyncio.sleep(0.12)
