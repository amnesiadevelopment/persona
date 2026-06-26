from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ...interfaces.protocols import IProxyService
from ...models.bookmark import Bookmark
from ...models.profile import Profile
from ...services.browser.profile_seed import (
    DEFAULT_SEARCH_ENGINE,
    SEARCH_ENGINE_LABELS,
)
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
        [str, str, str, str, str, list[str], list[str], str, str], str | None
    ],
    profile: Profile | None = None,
    proxy_names: list[str] | None = None,
    pool_names: list[str] | None = None,
    all_bookmarks: list[Bookmark] | None = None,
    on_import_cookies_file: Callable[[], object] | None = None,
    on_export_cookies_file: Callable[[], object] | None = None,
    on_bulk: Callable[[], None] | None = None,
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

    engine_dropdown = build_engine_dropdown(
        getattr(profile, "engine", "chromium") if profile is not None else "chromium",
    )
    engine_dropdown.expand = True

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

    selected_bookmarks = set(profile.bookmarks) if profile is not None else set()
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
        search = search_dropdown.value or DEFAULT_SEARCH_ENGINE
        pool = pool_dropdown.value or _NO_POOL
        pool = "" if pool == _NO_POOL else pool
        bookmarks = [n for n, cb in bookmark_checks.items() if cb.value]
        tags = [s.strip() for s in (tags_field.value or "").split(",") if s.strip()]
        notes = (notes_field.value or "").strip()
        engine = engine_dropdown.value or "chromium"
        name_error.visible = False

        valid_name, name_err = validate_profile_name(name)
        if not valid_name:
            name_error.value = name_err
            name_error.visible = True
            page.update()
            return

        error = on_save(
            name, proxy, os_type, search, pool, bookmarks, tags, notes, engine
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
                    ft.Row(controls=[proxy_dropdown]),
                    proxy_hint,
                    ft.Row(controls=[os_dropdown]),
                    ft.Row(controls=[engine_dropdown]),
                    ft.Row(controls=[search_dropdown]),
                    search_hint,
                    ft.Divider(height=10, color=COLORS["border"]),
                    ft.Row(controls=[pool_dropdown]),
                    *bookmark_section,
                    *cookie_controls,
                    ft.Container(height=10),
                ],
            ),
        ),
        actions=[
            ft.TextButton(
                "Cancel",
                style=ft.ButtonStyle(color=COLORS["text_sub"]),
                on_click=lambda _: page.pop_dialog(),
            ),
            *(
                [
                    ft.OutlinedButton(
                        "[ bulk ]",
                        style=OUTLINE_STYLE,
                        on_click=lambda _: (page.pop_dialog(), on_bulk()),
                    )
                ]
                if on_bulk is not None
                else []
            ),
            ft.Button(
                save_label,
                style=ACCENT_STYLE,
                on_click=on_submit,
            ),
        ],
    )
    page.show_dialog(dlg)
