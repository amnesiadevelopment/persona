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
from ...services.browser.invisible_launch import _system_dpr
from ...services.browser.resolution import parse_resolution
from ...utils.validation import validate_profile_name
from ..theme.colors import COLORS
from ..theme.page import build_engine_dropdown, build_os_dropdown
from ..theme.styles import ACCENT_STYLE, DLG_FIELD_KWARGS, MONO, OUTLINE_STYLE

_DIRECT = "(direct)"
_NO_POOL = "(none)"


def open_profile_dialog(
    page: ft.Page,
    proxy_service: IProxyService,
    on_save: Callable[
        [str, str, str, str, str, list[str], list[str], str, str, str], str | None
    ],
    profile: Profile | None = None,
    proxy_names: list[str] | None = None,
    pool_names: list[str] | None = None,
    all_bookmarks: list[Bookmark] | None = None,
    on_import_cookies_file: Callable[[], object] | None = None,
    on_export_cookies_file: Callable[[], object] | None = None,
    on_bulk: Callable[[], None] | None = None,
    on_add_proxy: Callable[[], None] | None = None,
) -> None:
    proxy_names = proxy_names or []
    pool_names = pool_names or []
    all_bookmarks = all_bookmarks or []
    is_edit = profile is not None
    title = "Edit Profile" if is_edit else get_string("create_new_profile")
    subtitle = (
        f"Editing: {profile.name}"
        if profile is not None
        else "Configure your browser identity"
    )
    save_label = "[ save ]" if is_edit else "[ create ]"

    name_field = ft.TextField(
        label=get_string("profile_name"),
        value=profile.name if profile is not None else "",
        hint_text="" if profile is not None else "Enter a profile name",
        **DLG_FIELD_KWARGS,
    )

    current_proxy = (profile.proxy or "") if profile is not None else ""
    proxy_value = current_proxy if current_proxy in proxy_names else _DIRECT
    proxy_dropdown = ft.Dropdown(
        label="Proxy",
        value=proxy_value,
        expand=True,
        options=[ft.dropdown.Option(_DIRECT)]
        + [ft.dropdown.Option(n) for n in proxy_names],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
        text_style=ft.TextStyle(font_family=MONO),
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
        "manage proxies on the network page",
        size=11,
        color=COLORS["text_sub"],
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
        label="Screen resolution",
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
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
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
        spacing=6,
        controls=[ft.Row(controls=[resolution_dropdown]), custom_row, res_hint],
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
        label="Default search engine",
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
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
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
        spacing=6,
        controls=[ft.Row(controls=[search_dropdown]), search_locked, search_hint],
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
        page.update()
        _refresh_search_controls()

    engine_dropdown.on_select = on_engine_change

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
        label="Bookmark pool",
        value=pool_value,
        expand=True,
        options=[ft.dropdown.Option(_NO_POOL)]
        + [ft.dropdown.Option(n) for n in pool_names],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
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
    bookmark_checks: dict[str, ft.Checkbox] = {}
    bookmark_rows: list[ft.Control] = []
    for b in all_bookmarks:
        cb = ft.Checkbox(
            value=b.name in selected_bookmarks,
            fill_color=COLORS["accent"],
            check_color=COLORS["bg"],
        )
        bookmark_checks[b.name] = cb
        bookmark_rows.append(
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    cb,
                    ft.Text(b.name, size=13, color=COLORS["text_main"], font_family=MONO),
                ],
            )
        )
    bookmark_section: list[ft.Control] = [
        ft.Text(
            "extra bookmarks (added on top of the pool):",
            size=11,
            color=COLORS["text_sub"],
            font_family=MONO,
        ),
        ft.Container(
            height=170,
            border_radius=3,
            border=ft.Border.all(1, COLORS["card_border"]),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            content=ft.Column(
                spacing=2,
                scroll=ft.ScrollMode.ALWAYS,
                controls=bookmark_rows,
            ),
        ),
    ] if bookmark_rows else []

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

    cookie_controls: list[ft.Control] = (
        [
            ft.Divider(height=10, color=COLORS["border"]),
            ft.Text(
                "cookies (import/export a JSON file):",
                size=11,
                color=COLORS["text_sub"],
                font_family=MONO,
            ),
            ft.Row(
                spacing=8,
                controls=[
                    ft.OutlinedButton(
                        "[ import file ]", height=34, on_click=do_import
                    ),
                    ft.OutlinedButton(
                        "[ export file ]", height=34, on_click=do_export
                    ),
                ],
            ),
            cookie_status,
        ]
        if is_edit
        else []
    )

    current_tags = ", ".join(profile.tags) if profile is not None else ""
    tags_field = ft.TextField(
        label="Tags (comma-separated)",
        value=current_tags,
        **DLG_FIELD_KWARGS,
    )

    current_notes = profile.notes if profile is not None else ""
    notes_field = ft.TextField(
        label="Notes",
        value=current_notes,
        multiline=True,
        min_lines=1,
        max_lines=2,
        **DLG_FIELD_KWARGS,
    )

    name_error = ft.Text("", size=12, color=COLORS["error"], visible=False)

    def on_submit(_: ft.ControlEvent) -> None:
        name = (name_field.value or "").strip()
        proxy = proxy_dropdown.value or _DIRECT
        proxy = "" if proxy == _DIRECT else proxy
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
        pool = pool_dropdown.value or _NO_POOL
        pool = "" if pool == _NO_POOL else pool
        bookmarks = [n for n, cb in bookmark_checks.items() if cb.value]
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
            resolution,
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
            width=520,
            padding=ft.Padding.symmetric(horizontal=4, vertical=4),
            content=ft.Column(
                tight=True,
                spacing=16,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Text(subtitle, size=13, color=COLORS["text_sub"]),
                    ft.Container(height=6),
                    ft.Row(controls=[name_field]),
                    name_error,
                    ft.Row(controls=[tags_field]),
                    ft.Row(controls=[notes_field]),
                    ft.Row(
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=proxy_row_controls,
                    ),
                    proxy_hint,
                    ft.Row(controls=[os_dropdown]),
                    ft.Row(controls=[engine_dropdown]),
                    engine_hint,
                    resolution_section,
                    search_section,
                    ft.Divider(height=10, color=COLORS["border"]),
                    ft.Row(controls=[pool_dropdown]),
                    *bookmark_section,
                    *cookie_controls,
                    ft.Container(height=10),
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
                    ft.TextButton(
                        "Cancel",
                        style=ft.ButtonStyle(color=COLORS["text_sub"]),
                        on_click=lambda _: page.pop_dialog(),
                    ),
                    ft.Button(
                        save_label,
                        style=ACCENT_STYLE,
                        on_click=on_submit,
                    ),
                ],
            ),
        ],
    )
    page.show_dialog(dlg)
