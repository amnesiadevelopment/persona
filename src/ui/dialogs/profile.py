from collections.abc import Callable

import flet as ft

from ...core.logging import get_logger
from ...core.strings import get_string
from ...interfaces.protocols import IProxyService
from ...models.bookmark import Bookmark
from ...models.profile import Profile
from ...services.browser.profile_seed import (
    DEFAULT_SEARCH_ENGINE,
    SEARCH_ENGINE_LABELS,
)
from ...services.bookmark.store import DEFAULT_BOOKMARKS
from ...services.browser.device_presets import is_mobile_os
from ...services.profile.pool_assignment import (
    POOL_NONE,
    PoolDirective,
)
from ...services.profile.proxy_assignment import (
    PROXY_NONE,
    PROXY_UNCHANGED,
    ProxyDirective,
)
from ...services.browser.invisible_launch import _system_dpr
from ...services.browser.resolution import parse_resolution
from ...utils.validation import validate_profile_name
from ..theme.colors import COLORS
from ..theme.page import build_engine_dropdown, build_os_dropdown
from ..theme.styles import (
    ACCENT_STYLE,
    DLG_FIELD_KWARGS,
    DLG_INPUT_KWARGS,
    MONO,
    OUTLINE_STYLE,
    field_label,
    labeled,
    section_header,
)

_DIRECT = "(direct)"
#: Key prefix for the synthetic dropdown option that stands in for a profile's
#: assigned proxy when that name is absent from the available list. Prefixed so
#: it can never collide with a real proxy name, and read back on submit as
#: "leave the assignment alone" rather than as a selection.
_UNRESOLVED_PREFIX = "\x00unresolved:"
_NO_POOL = "(none)"
_NO_CERT = "(none)"


def open_profile_dialog(
    page: ft.Page,
    proxy_service: IProxyService,
    #: (name, proxy, os, search, pool, bookmarks, tags, notes, engine,
    #: resolution, certificate) -> error message, or None on success.
    #: The proxy position accepts a ``ProxyDirective`` as well as a name:
    #: the dialog sends ``PROXY_NONE`` for a deliberate direct connection and
    #: ``PROXY_UNCHANGED`` when it could not account for the profile's assigned
    #: proxy, so that absence is never mistaken for "clear the assignment".
    #: The pool position accepts a ``PoolDirective`` on the same terms: the
    #: dialog sends ``POOL_NONE`` for a deliberate "(none)", because the model
    #: no longer reads an empty string as a clear.
    on_save: Callable[
        [
            str,
            str | ProxyDirective,
            str,
            str,
            str | PoolDirective,
            list[str],
            list[str],
            str,
            str,
            str,
            str,
        ],
        str | None,
    ],
    profile: Profile | None = None,
    proxy_names: list[str] | None = None,
    pool_names: list[str] | None = None,
    all_bookmarks: list[Bookmark] | None = None,
    cert_names: list[str] | None = None,
    on_import_cookies_file: Callable[[], object] | None = None,
    on_export_cookies_file: Callable[[], object] | None = None,
    on_bulk: Callable[[], None] | None = None,
    on_add_proxy: Callable[[], None] | None = None,
) -> None:
    proxy_names = proxy_names or []
    pool_names = pool_names or []
    all_bookmarks = all_bookmarks or []
    cert_names = cert_names or []

    from ...core.assets import asset_path
    import os as _os

    def _engine_icon(size: int):
        # persona's own V-engine mark, matching the sidebar; falls back to a
        # Material icon if the asset is missing (e.g. a source run).
        path = asset_path("v_engine.png")
        if _os.path.exists(path):
            return ft.Image(src=path, width=size, height=size)
        return ft.Icons.BOLT

    def _brand_icon_src(engine_key: str) -> str | None:
        # Grey brand mark for the selected browser, shown beside the Engine field
        # label (the section divider already carries the V-engine, so the field
        # shows WHICH browser instead).
        fname = (
            "engine_firefox_grey.svg"
            if engine_key in ("firefox", "camoufox")
            else "engine_chrome_grey.svg"
        )
        p = asset_path(fname)
        return p if _os.path.exists(p) else None

    # A mutable icon beside the Engine label that swaps to the picked browser.
    _brand_holder = ft.Container(width=13, height=13)

    def _refresh_brand_icon() -> None:
        src = _brand_icon_src(engine_dropdown.value or "chromium")
        _brand_holder.content = (
            ft.Image(src=src, width=13, height=13)
            if src
            else ft.Icon(ft.Icons.WEB_ASSET, size=13, color=COLORS["text_sub"])
        )
    is_edit = profile is not None
    title = "Edit Profile" if is_edit else get_string("create_new_profile")
    subtitle = (
        f"Editing: {profile.name}"
        if profile is not None
        else "Configure your browser identity"
    )
    save_label = "[ save ]" if is_edit else "[ create ]"

    name_field = ft.TextField(
        value=profile.name if profile is not None else "",
        hint_text="e.g. Amazon US Shopper",
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        expand=True,
        **DLG_INPUT_KWARGS,
    )

    current_proxy = (profile.proxy or "") if profile is not None else ""
    # A profile whose assigned proxy is NOT in the available list must NOT be
    # rendered as DIRECT. That fallback was visually identical to a profile the
    # operator deliberately set to direct, and saving from it turned a display
    # fallback into a stored un-assignment — after which the launch guard had
    # nothing left to refuse and the profile launched on the real IP.
    #
    # The list can legitimately be missing a name the profile still references:
    # the proxy store skips a single malformed record (populated dropdown, one
    # name absent) or quarantines the whole file (every name absent). Both are
    # deliberate protections and neither reached this dialog in any form.
    #
    # So the unaccounted-for assignment gets its OWN option, carrying the name,
    # selected and visibly distinct from DIRECT. Submitting while it is selected
    # sends PROXY_UNCHANGED, so an operator who opened the dialog to rename the
    # profile comes out with the same protection they went in with.
    proxy_unresolved = bool(current_proxy) and current_proxy not in proxy_names
    unresolved_option_key = f"{_UNRESOLVED_PREFIX}{current_proxy}"
    if proxy_unresolved:
        proxy_value = unresolved_option_key
    elif current_proxy:
        proxy_value = current_proxy
    else:
        proxy_value = _DIRECT
    proxy_options = [ft.dropdown.Option(_DIRECT)]
    if proxy_unresolved:
        proxy_options.append(
            ft.dropdown.Option(
                key=unresolved_option_key,
                text=f"{current_proxy} — NOT FOUND (keep assigned)",
            )
        )
    proxy_options += [ft.dropdown.Option(n) for n in proxy_names]
    proxy_dropdown = ft.Dropdown(
        value=proxy_value,
        expand=True,
        options=proxy_options,
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=(
            COLORS["warning"] if proxy_unresolved else COLORS["card_border"]
        ),
        focused_border_color=COLORS["accent"],
        border_radius=3,
        text_style=ft.TextStyle(font_family=MONO),
    )

    current_cert = (profile.certificate or "") if profile is not None else ""
    cert_value = current_cert if current_cert in cert_names else _NO_CERT
    cert_dropdown = ft.Dropdown(
        value=cert_value,
        expand=True,
        options=[ft.dropdown.Option(_NO_CERT)]
        + [ft.dropdown.Option(n) for n in cert_names],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        text_style=ft.TextStyle(font_family=MONO),
    )

    # The Firefox CA import soft-fails: the launch proceeds with the certificate
    # UNTRUSTED. Without this line the dropdown showing a certificate selected is
    # the only thing the operator sees, and it reads as a confident state with no
    # provenance. Render-only — this reports the LAST recorded outcome, it never
    # probes (see the socket-spy test).
    # Gated on the CERTIFICATE as well as the status: the recorded outcome
    # describes one certificate's CA, so it must never be rendered against a
    # different one (or against none). update_profile clears it on reassignment;
    # this is the second line of defence, so no other write path can resurrect
    # a stale verdict here.
    _cert_trust = (
        profile.cert_trust_status
        if profile is not None and profile.certificate and profile.cert_trust_status
        else ""
    )
    cert_trust_text = ft.Text(
        f"last launch: {_cert_trust}" if _cert_trust else "",
        size=11,
        color=(
            COLORS["error"]
            if _cert_trust and not _cert_trust.startswith("trusted")
            else COLORS["text_sub"]
        ),
        font_family=MONO,
    )

    def go_add_proxy(_: ft.ControlEvent) -> None:
        page.pop_dialog()
        if on_add_proxy is not None:
            on_add_proxy()

    proxy_row_controls: list[ft.Control] = [proxy_dropdown]
    if on_add_proxy is not None:
        proxy_row_controls.append(
            ft.OutlinedButton(
                "[ + proxy ]",
                style=OUTLINE_STYLE,
                height=48,
                on_click=go_add_proxy,
                tooltip="Add a new proxy on the network page",
            )
        )
    proxy_hint = ft.Text(
        (
            f"proxy {current_proxy!r} is assigned but was not found — "
            "saving keeps it assigned"
            if proxy_unresolved
            else "manage proxies on the network page"
        ),
        size=11,
        color=COLORS["warning"] if proxy_unresolved else COLORS["text_sub"],
        font_family=MONO,
    )
    os_dropdown = build_os_dropdown(
        profile.os_type if profile is not None else "windows",
    )
    os_dropdown.expand = True

    engine_value = (
        getattr(profile, "engine", "chromium") if profile is not None else "chromium"
    )
    engine_dropdown = build_engine_dropdown(engine_value)
    engine_dropdown.expand = True
    # A profile is bound to its engine: the data dir layout and the whole
    # fingerprint mechanism are engine-specific, so switching after creation
    # would break the existing profile. Lock it when editing.
    engine_hint: ft.Control = ft.Container()
    if is_edit:
        engine_dropdown.disabled = True
        engine_hint = ft.Text(
            "engine is fixed after a profile is created",
            size=11,
            color=COLORS["text_sub"],
            font_family=MONO,
        )

    current_res = (
        getattr(profile, "resolution", "auto") if profile is not None else "auto"
    )
    # Ordered largest-to-smallest with a human label, so the common sizes are
    # easy to find. 4K (3840x2160) is intentionally absent: on the Firefox engine
    # it can't be offered honestly. The patched Firefox derives window
    # devicePixelRatio from the render scale, which must equal the host's display
    # scale for readable text — so on a HiDPI host (e.g. 150%) a 4K screen reports
    # screen.width * devicePixelRatio = 5760, a physical size no real monitor has
    # (an instant fingerprint tell), and the engine's launch also hangs at that
    # size on such hosts. Every size below stays readable AND plausible at the
    # host scale (2560 * 1.5 = 3840, a real 4K-at-150% panel).
    res_choices = [
        ("2560x1440", "2K QHD"),
        ("1920x1080", "Full HD"),
        ("1600x900", "HD+"),
        ("1536x864", "HD+"),
        ("1440x900", "WXGA+"),
        ("1366x768", "HD"),
        ("1280x800", "WXGA"),
    ]
    res_presets = [r for r, _ in res_choices]
    is_preset = current_res in res_presets
    res_value = current_res if (current_res == "auto" or is_preset) else "custom"
    custom_w = ft.TextField(
        label="width",
        value="" if res_value != "custom" else current_res.split("x")[0],
        width=110,
        **DLG_FIELD_KWARGS,
    )
    custom_h = ft.TextField(
        label="height",
        value="" if res_value != "custom" else current_res.split("x")[-1],
        width=110,
        **DLG_FIELD_KWARGS,
    )
    custom_row = ft.Row(
        spacing=10,
        visible=res_value == "custom",
        controls=[custom_w, ft.Text("x", color=COLORS["text_sub"]), custom_h],
    )

    def on_res_change(_: ft.ControlEvent) -> None:
        custom_row.visible = resolution_dropdown.value == "custom"
        page.update()

    resolution_dropdown = ft.Dropdown(
        value=res_value,
        expand=True,
        options=(
            [ft.dropdown.Option(key="auto", text="Auto (random)")]
            + [
                ft.dropdown.Option(
                    key=r, text=f"{r.replace('x', ' x ')}  ({label})"
                )
                for r, label in res_choices
            ]
            + [ft.dropdown.Option(key="custom", text="Custom…")]
        ),
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        text_style=ft.TextStyle(font_family=MONO),
    )
    resolution_dropdown.on_select = on_res_change

    # The Firefox engine renders at the host display's scale for readable text, so
    # a scanner reads the reported resolution as chosen * host-scale. On a HiDPI
    # host (e.g. 150%) a 2560 pick reports 3840. Warn the user their monitor's
    # scale multiplies the value so the shown resolution isn't a surprise.
    _dpr = _system_dpr()
    if _dpr and _dpr != 1.0:
        res_hint_text = (
            f"note: your display scale is {int(_dpr * 100)}% — the reported "
            f"resolution is your choice x{_dpr:g} (e.g. 2560 shows as "
            f"{int(2560 * _dpr)})"
        )
    else:
        res_hint_text = (
            "note: the reported resolution follows your monitor's display scale"
        )
    res_hint = ft.Text(
        res_hint_text, size=11, color=COLORS["text_sub"], font_family=MONO
    )

    # A mobile profile's screen geometry comes from its device preset (the
    # phone/tablet the fingerprint impersonates), not this desktop picker — a 4K
    # "phone" is an instant tell. Hide the whole resolution picker for mobile OSes
    # so it's never even offered there.
    resolution_section = ft.Column(
        spacing=8,
        controls=[
            labeled(
                "Screen resolution",
                resolution_dropdown,
                icon=ft.Icons.ASPECT_RATIO,
            ),
            custom_row,
            res_hint,
        ],
    )
    resolution_section.visible = not is_mobile_os(
        os_dropdown.value or "windows"
    )

    current_search = (
        profile.search_engine if profile is not None else DEFAULT_SEARCH_ENGINE
    )
    if current_search not in SEARCH_ENGINE_LABELS:
        current_search = DEFAULT_SEARCH_ENGINE
    search_dropdown = ft.Dropdown(
        value=current_search,
        expand=True,
        options=[
            ft.dropdown.Option(key=k, text=v)
            for k, v in SEARCH_ENGINE_LABELS.items()
        ],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        text_style=ft.TextStyle(font_family=MONO),
    )
    search_hint = ft.Text(
        "applied to new profiles only",
        size=11,
        color=COLORS["text_sub"],
        font_family=MONO,
    )
    # For the Firefox engine the search engine is fixed to DuckDuckGo (no
    # per-profile setting exists), so instead of a dropdown that can still be
    # opened, show a static locked field. A disabled ft.Dropdown on this Flet
    # still opens its option list on click, which read as "the fix didn't work";
    # a plain read-only display can't be opened at all.
    search_locked = ft.Container(
        padding=ft.Padding.symmetric(horizontal=14, vertical=14),
        border=ft.Border.all(1, COLORS["card_border"]),
        border_radius=3,
        bgcolor=COLORS["input_bg"],
        content=ft.Column(
            spacing=2,
            controls=[
                ft.Text(
                    "Default search engine",
                    size=11,
                    color=COLORS["text_sub"],
                    font_family=MONO,
                ),
                ft.Text(
                    "DuckDuckGo  (fixed for Firefox)",
                    color=COLORS["text_sub"],
                    font_family=MONO,
                ),
            ],
        ),
    )
    search_section = ft.Column(
        spacing=8,
        controls=[
            labeled(
                "Default search engine",
                search_dropdown,
                icon=ft.Icons.SEARCH,
            ),
            search_locked,
            search_hint,
        ],
    )

    def _engine() -> str:
        return engine_dropdown.value or "chromium"

    # The Firefox engine has no per-profile default search engine — it's pinned
    # globally to DuckDuckGo for every Firefox profile. Show the static locked
    # field then (nothing to open); chromium keeps the live dropdown.
    def _apply_engine_dependent() -> None:
        firefox = _engine() == "firefox"
        search_dropdown.visible = not firefox
        search_locked.visible = firefox
        if firefox:
            search_dropdown.value = DEFAULT_SEARCH_ENGINE
            search_hint.value = "fixed to DuckDuckGo for the Firefox engine"
        else:
            search_hint.value = "applied to new profiles only"

    _apply_engine_dependent()

    def _refresh_search_controls() -> None:
        # Update the specific controls whose visibility we toggled. page.update()
        # alone sometimes doesn't repaint a control's `visible` change nested deep
        # inside a dialog on this Flet, so nudge each control directly too (guarded
        # — calling .update() before the control is on the page raises).
        for ctl in (search_dropdown, search_locked, search_hint):
            try:
                ctl.update()
            except Exception:
                pass

    # The full engine/OS option sets, captured before any constraint narrows
    # them, so the restrictions below can restore the complete choice.
    _all_engine_options = list(engine_dropdown.options)
    _all_os_options = list(os_dropdown.options)

    def _apply_firefox_os_lock() -> None:
        # stealth-Firefox reports a Windows platform regardless of os_type
        # (#211): a macOS/Linux Firefox profile is an inconsistent lie. Pin the
        # Firefox engine to windows and drop every other OS so none can be
        # picked; chromium (which honors os_type) gets the full OS list back.
        if _engine() == "firefox":
            os_dropdown.value = "windows"
            os_dropdown.options = [
                o for o in _all_os_options if o.key == "windows"
            ]
        else:
            os_dropdown.options = _all_os_options

    def on_engine_change(_: ft.ControlEvent) -> None:
        get_logger("ui.dialog").info(
            "engine on_select fired: engine=%s", engine_dropdown.value
        )
        _apply_firefox_os_lock()
        _apply_engine_dependent()
        _refresh_brand_icon()
        page.update()
        _refresh_search_controls()

    engine_dropdown.on_select = on_engine_change
    _refresh_brand_icon()

    # invisible_playwright is desktop Firefox with no mobile mode, so a mobile
    # profile must use chromium (which has real device presets). When the user
    # picks a mobile OS: force the engine to chromium, drop the Firefox option so
    # it can't be chosen, and hide the desktop resolution picker. Restore the full
    # engine choice when they switch back to a desktop OS.
    def on_os_change(_: ft.ControlEvent) -> None:
        os_value = os_dropdown.value or "windows"
        mobile = is_mobile_os(os_value)
        # A non-windows OS is incompatible with the windows-only Firefox engine:
        # flip to chromium (mobile already does this; extend it to macOS/Linux).
        if os_value != "windows" and _engine() == "firefox":
            engine_dropdown.value = "chromium"
        if mobile:
            engine_dropdown.options = [
                o for o in _all_engine_options if o.key != "firefox"
            ]
        else:
            engine_dropdown.options = _all_engine_options
        resolution_section.visible = not mobile
        _apply_firefox_os_lock()
        _apply_engine_dependent()
        page.update()

    os_dropdown.on_select = on_os_change
    # Apply the constraints for a profile that already has a mobile OS (editing
    # one, or a create dialog defaulted to mobile) or the Firefox engine.
    if is_mobile_os(os_dropdown.value or "windows") or _engine() == "firefox":
        on_os_change(None)  # type: ignore[arg-type]

    current_pool = (profile.bookmark_pool or "") if profile is not None else ""
    pool_value = current_pool if current_pool in pool_names else _NO_POOL
    pool_dropdown = ft.Dropdown(
        value=pool_value,
        expand=True,
        options=[ft.dropdown.Option(_NO_POOL)]
        + [ft.dropdown.Option(n) for n in pool_names],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        text_style=ft.TextStyle(font_family=MONO),
    )

    # On create nothing is pre-checked — the user picks their own selection
    # (saving with none checked stores an explicit [] = empty toolbar). When
    # editing, an explicit list (including []) is shown as-is; bookmarks is None
    # means never configured, so the stock defaults are pre-checked to reflect
    # what the profile actually opens with.
    if not is_edit:
        selected_bookmarks: set[str] = set()
    elif profile.bookmarks is not None:
        selected_bookmarks = set(profile.bookmarks)
    else:
        selected_bookmarks = {n for n in DEFAULT_BOOKMARKS if n in {b.name for b in all_bookmarks}}
    # Bookmarks are chosen as toggle CHIPS (like the tag filters): a selected
    # chip is filled accent, an unselected one is a plain outline. The picked
    # set lives in `bookmark_selected`; on_submit reads it directly.
    bookmark_selected: set[str] = set(
        n for n in selected_bookmarks if n in {b.name for b in all_bookmarks}
    )
    bookmark_chip_holders: dict[str, ft.Container] = {}

    def _chip_content(name: str, on: bool) -> ft.Control:
        return ft.Container(
            border_radius=3,
            border=ft.Border.all(
                1, COLORS["accent"] if on else COLORS["card_border"]
            ),
            bgcolor=(
                ft.Colors.with_opacity(0.12, COLORS["accent"])
                if on
                else COLORS["card_bg"]
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            content=ft.Row(
                spacing=7,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(
                        width=6,
                        height=6,
                        border_radius=3,
                        bgcolor=COLORS["accent"] if on else COLORS["text_dim"],
                    ),
                    ft.Text(
                        name,
                        size=13,
                        color=COLORS["accent"] if on else COLORS["text_sub"],
                        font_family=MONO,
                    ),
                ],
            ),
        )

    def _toggle_bookmark(name: str) -> None:
        if name in bookmark_selected:
            bookmark_selected.discard(name)
        else:
            bookmark_selected.add(name)
        holder = bookmark_chip_holders[name]
        holder.content = _chip_content(name, name in bookmark_selected)
        page.update()

    bookmark_chips: list[ft.Control] = []
    for b in all_bookmarks:
        holder = ft.Container(
            content=_chip_content(b.name, b.name in bookmark_selected),
            on_click=lambda _, n=b.name: _toggle_bookmark(n),
            ink=True,
        )
        bookmark_chip_holders[b.name] = holder
        bookmark_chips.append(holder)

    bookmark_section: list[ft.Control] = [
        field_label("extra bookmarks (added on top of the pool)"),
        ft.Row(spacing=8, wrap=True, controls=bookmark_chips),
    ] if bookmark_chips else []

    _prior_status = (
        profile.cookie_import_status
        if profile is not None and profile.cookie_import_status
        else ""
    )
    cookie_status = ft.Text(
        f"last import: {_prior_status}" if _prior_status else "",
        size=11,
        color=COLORS["text_sub"],
        font_family=MONO,
    )

    def _set_status(msg: str, ok: bool = True) -> None:
        cookie_status.value = msg
        cookie_status.color = COLORS["success"] if ok else COLORS["error"]
        page.update()

    async def do_import(_: ft.ControlEvent) -> None:
        if on_import_cookies_file is None:
            return
        msg = await on_import_cookies_file()
        if msg:
            _set_status(msg, ok="imported" in msg.lower())

    async def do_export(_: ft.ControlEvent) -> None:
        if on_export_cookies_file is None:
            return
        msg = await on_export_cookies_file()
        if msg:
            _set_status(msg, ok="exported" in msg.lower())

    # Cookies attach to a profile's data dir, which only exists after the profile
    # is created — so on CREATE the section is shown (so the feature is
    # discoverable) but the buttons are disabled with a hint, and on EDIT they
    # work.
    cookie_controls: list[ft.Control] = [
        section_header("COOKIES", icon=ft.Icons.COOKIE_OUTLINED),
        field_label("import / export a cookies JSON file"),
        ft.Row(
            spacing=8,
            controls=[
                ft.OutlinedButton(
                    "[ import file ]",
                    height=34,
                    style=OUTLINE_STYLE,
                    disabled=not is_edit,
                    on_click=do_import,
                ),
                ft.OutlinedButton(
                    "[ export file ]",
                    height=34,
                    style=OUTLINE_STYLE,
                    disabled=not is_edit,
                    on_click=do_export,
                ),
            ],
        ),
        (
            cookie_status
            if is_edit
            else ft.Text(
                "available after you create the profile",
                size=11,
                color=COLORS["text_dim"],
                font_family=MONO,
            )
        ),
    ]

    current_tags = ", ".join(profile.tags) if profile is not None else ""
    tags_field = ft.TextField(
        value=current_tags,
        hint_text="shopping, us, amazon",
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        **DLG_INPUT_KWARGS,
    )

    current_notes = profile.notes if profile is not None else ""
    notes_field = ft.TextField(
        value=current_notes,
        hint_text="optional",
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        multiline=True,
        min_lines=1,
        max_lines=2,
        **DLG_INPUT_KWARGS,
    )

    name_error = ft.Text("", size=12, color=COLORS["error"], visible=False)

    def on_submit(_: ft.ControlEvent) -> None:
        name = (name_field.value or "").strip()
        # Three outcomes, not two. The unresolved option means "I could not
        # account for this assignment", which must travel as PROXY_UNCHANGED so
        # saving an unrelated edit cannot discard the proxy. DIRECT stays
        # expressible as a deliberate choice, and now says so explicitly with
        # PROXY_NONE instead of relying on an empty string the model used to
        # read as a clear-by-omission.
        _picked = proxy_dropdown.value or _DIRECT
        proxy: str | ProxyDirective
        if _picked.startswith(_UNRESOLVED_PREFIX):
            proxy = PROXY_UNCHANGED
        elif _picked == _DIRECT:
            proxy = PROXY_NONE
        else:
            proxy = _picked
        os_type = os_dropdown.value or "windows"
        engine = engine_dropdown.value or "chromium"
        # Firefox has no per-profile default search engine — it's pinned to
        # DuckDuckGo globally for all Firefox profiles. Ignore the picker's value
        # (the section is hidden for Firefox) so the stored engine is always the
        # global one, regardless of what the dropdown last held.
        search = (
            DEFAULT_SEARCH_ENGINE
            if engine == "firefox"
            else (search_dropdown.value or DEFAULT_SEARCH_ENGINE)
        )
        # Two outcomes, mirroring the proxy block above. "(none)" is a
        # deliberate choice and now SAYS so with POOL_NONE, instead of relying
        # on an empty string the model used to read as a clear — it no longer
        # does, so sending "" here would leave the old pool in place and the
        # dialog would silently fail to clear it.
        _picked_pool = pool_dropdown.value or _NO_POOL
        pool: str | PoolDirective
        pool = POOL_NONE if _picked_pool == _NO_POOL else _picked_pool
        certificate = cert_dropdown.value or _NO_CERT
        certificate = "" if certificate == _NO_CERT else certificate
        bookmarks = [b.name for b in all_bookmarks if b.name in bookmark_selected]
        tags = [s.strip() for s in (tags_field.value or "").split(",") if s.strip()]
        notes = (notes_field.value or "").strip()
        name_error.visible = False

        res_choice = resolution_dropdown.value or "auto"
        if res_choice == "custom":
            w = (custom_w.value or "").strip()
            h = (custom_h.value or "").strip()
            if parse_resolution(f"{w}x{h}") is None:
                name_error.value = "Enter a valid custom resolution (e.g. 1920 x 1080)"
                name_error.visible = True
                page.update()
                return
            resolution = f"{w}x{h}"
        else:
            resolution = res_choice

        valid_name, name_err = validate_profile_name(name)
        if not valid_name:
            name_error.value = name_err
            name_error.visible = True
            page.update()
            return

        error = on_save(
            name, proxy, os_type, search, pool, bookmarks, tags, notes, engine,
            resolution, certificate,
        )
        if error:
            name_error.value = error
            name_error.visible = True
            page.update()
        else:
            page.pop_dialog()

    dlg = ft.AlertDialog(
        modal=True,
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        bgcolor=COLORS["card_bg"],
        title=ft.Text(
            title,
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Container(
            width=470,
            height=600,
            content=ft.Stack(
              controls=[
                ft.Container(
                  padding=ft.Padding.only(left=4, top=4, bottom=4, right=4),
                  content=ft.Column(
                    tight=True,
                    spacing=18,
                    scroll=ft.ScrollMode.AUTO,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[
                    # right padding on each control's own container keeps the
                    # scrollbar in a gutter clear of the field borders
                    ft.Container(padding=ft.Padding.only(right=14), content=ft.Column(
                      tight=True, spacing=18, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
                    ft.Text(subtitle, size=13, color=COLORS["text_sub"]),
                    # ---- IDENTITY ----
                    section_header("IDENTITY", icon=ft.Icons.PERSON_OUTLINE),
                    labeled(
                        "Profile name",
                        ft.Row(controls=[name_field]),
                        icon=ft.Icons.BADGE_OUTLINED,
                    ),
                    name_error,
                    ft.Row(
                        spacing=16,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Tags", tags_field, icon=ft.Icons.LABEL_OUTLINE
                                ),
                            ),
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Notes", notes_field, icon=ft.Icons.NOTES
                                ),
                            ),
                        ],
                    ),
                    # ---- NETWORK ----
                    # Section dividers carry the SAME icon as the sidebar nav
                    # entry; the field inside uses a different one so they don't
                    # duplicate (network=LAN like the sidebar; proxy=globe).
                    section_header("NETWORK", icon=ft.Icons.LAN_OUTLINED),
                    labeled(
                        "Proxy",
                        ft.Row(
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=proxy_row_controls,
                        ),
                        hint=proxy_hint,
                        icon=ft.Icons.PUBLIC,
                    ),
                    labeled(
                        "Certificate (mTLS)",
                        cert_dropdown,
                        hint=cert_trust_text if _cert_trust else None,
                        icon=ft.Icons.DESCRIPTION_OUTLINED,
                    ),
                    # ---- ENGINE ----
                    # Section divider = the sidebar's V-engine mark; the Engine
                    # field itself uses a plain Material icon so the two don't
                    # duplicate (the selected browser's brand logo already shows
                    # inside the dropdown's option row).
                    section_header("ENGINE", icon=_engine_icon(15)),
                    # Engine BEFORE OS: the engine decides whether OS even matters
                    # (Firefox is Windows-only, so its OS spoof is a no-op), so the
                    # operator picks the engine first and the OS field follows.
                    labeled(
                        "Engine",
                        engine_dropdown,
                        hint=engine_hint if is_edit else None,
                        icon=_brand_holder,
                    ),
                    ft.Row(
                        spacing=16,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Operating system",
                                    os_dropdown,
                                    icon=ft.Icons.COMPUTER,
                                ),
                            ),
                            ft.Container(
                                expand=True,
                                content=resolution_section,
                            ),
                        ],
                    ),
                    search_section,
                    # ---- BOOKMARKS ----
                    section_header("BOOKMARKS", icon=ft.Icons.BOOKMARK_BORDER),
                    labeled(
                        "Bookmark pool",
                        pool_dropdown,
                        icon=ft.Icons.FOLDER_OUTLINED,
                    ),
                    *bookmark_section,
                    *cookie_controls,
                      ]),
                    ),
                    ],
                  ),
                ),
                # Bottom fade — a soft gradient over the lower edge hints that the
                # form scrolls (content dissolves into the dialog background
                # instead of ending on a hard cut). Ignores clicks.
                ft.Container(
                  bottom=0, left=0, right=0, height=36,
                  content=ft.Container(
                    gradient=ft.LinearGradient(
                      begin=ft.Alignment.TOP_CENTER,
                      end=ft.Alignment.BOTTOM_CENTER,
                      colors=[
                        ft.Colors.with_opacity(0, COLORS["card_bg"]),
                        COLORS["card_bg"],
                      ],
                    ),
                  ),
                ),
              ],
            ),
        ),
        actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        actions=[
            (
                ft.OutlinedButton(
                    "[ bulk ]",
                    style=OUTLINE_STYLE,
                    on_click=lambda _: (page.pop_dialog(), on_bulk()),
                )
                if on_bulk is not None
                else ft.Container()
            ),
            ft.Row(
                spacing=8,
                tight=True,
                controls=[
                    ft.OutlinedButton(
                        "[ cancel ]",
                        style=OUTLINE_STYLE,
                        height=40,
                        on_click=lambda _: page.pop_dialog(),
                    ),
                    ft.Button(
                        save_label,
                        style=ACCENT_STYLE,
                        height=40,
                        on_click=on_submit,
                    ),
                ],
            ),
        ],
    )
    page.show_dialog(dlg)
