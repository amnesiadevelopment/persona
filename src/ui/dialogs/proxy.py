import threading
from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ...interfaces.protocols import IProxyService
from ...models.proxy import Proxy
from ...services.proxy.tz_names import is_declarable_zone
from ...utils.proxy_parse import parse_proxy_line
from ...utils.proxy_parser import build_proxy_url, split_proxy_url
from ...utils.validation import validate_proxy_format
from ..flags import flag_path
from ..theme.colors import COLORS
from ..theme.styles import (
    ACCENT_STYLE,
    DLG_INPUT_KWARGS,
    MONO,
    OUTLINE_STYLE,
    labeled,
    section_header,
)

_SCHEMES = ["socks5", "http", "https"]


def _flag_control(country_code: str) -> ft.Control:
    path = flag_path(country_code)
    if path:
        return ft.Image(src=path, width=28, height=20)
    return ft.Container(width=28, height=20)


def _fail_control() -> ft.Control:
    return ft.Container(
        width=28,
        height=20,
        alignment=ft.Alignment(0, 0),
        content=ft.Text("✕", size=18, color=COLORS["error"], font_family=MONO),
    )


def _initial_status_control(proxy: Proxy | None) -> ft.Control:
    if proxy is not None and proxy.last_check_ok is False:
        return _fail_control()
    return _flag_control(proxy.country_code if proxy is not None else "")


def open_proxy_dialog(
    page: ft.Page,
    proxy_service: IProxyService,
    on_save: Callable[[str, str, str], str | None],
    proxy: Proxy | None = None,
    on_checked: Callable[..., None] | None = None,
    on_check_failed: Callable[[str], None] | None = None,
    ui: Callable[[Callable[[], None]], None] | None = None,
    on_declare_timezone: Callable[[str, str], str | None] | None = None,
) -> None:
    is_edit = proxy is not None
    fields = split_proxy_url(proxy.url) if proxy is not None else split_proxy_url("")

    _hint = ft.TextStyle(color=COLORS["text_dim"], font_family=MONO)
    paste_field = ft.TextField(
        hint_text="scheme://user:pass@host:port  — paste any provider line",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    name_field = ft.TextField(
        value=proxy.name if proxy is not None else "",
        hint_text="e.g. home-socks", hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    type_dd = ft.Dropdown(
        value=fields["scheme"] if fields["scheme"] in _SCHEMES else "socks5",
        options=[ft.dropdown.Option(s) for s in _SCHEMES],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        text_style=ft.TextStyle(font_family=MONO),
    )
    host_field = ft.TextField(
        value=fields["host"], hint_text="proxy.example.com",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    port_field = ft.TextField(
        value=fields["port"], hint_text="1080",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    user_field = ft.TextField(
        value=fields["username"], hint_text="optional",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    pass_field = ft.TextField(
        value=fields["password"], hint_text="optional",
        hint_style=_hint, password=True, can_reveal_password=True,
        expand=True, **DLG_INPUT_KWARGS,
    )
    rotate_field = ft.TextField(
        value=proxy.rotate_url if proxy is not None else "",
        hint_text="provider endpoint that forces a new exit IP",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    # THE DOOR PS-274 ADDS. `Proxy.timezone` is what the CHECK measured and is
    # never edited here; this field is the operator's own declaration, for the
    # ordinary case where the check reports a country and no usable zone and
    # the country has no `_COUNTRY_TZ` row — a state whose only previously
    # documented remedy was editing a python dict inside an installed desktop
    # app, and whose one available UI action (re-check) loops forever.
    tz_field = ft.TextField(
        value=proxy.manual_timezone if proxy is not None else "",
        hint_text="e.g. Europe/Bucharest  — only needed if launching is refused",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    tz_error = ft.Text("", size=12, color=COLORS["error"], visible=False)
    name_error = ft.Text("", size=12, color=COLORS["error"], visible=False)
    addr_error = ft.Text("", size=12, color=COLORS["error"], visible=False)

    flag_holder = ft.Container(content=_initial_status_control(proxy))

    # WHAT A [ check ] RUN *INSIDE THIS DIALOG* LEARNED, keyed by the URL it was
    # run against. `on_check_result` can only PERSIST a result when the stored
    # record already carries that URL (audit6 #6, and that gate is correct) — so
    # on an ADD, where no record exists yet, and on an EDIT whose URL was
    # changed, the operator watches the flag turn and the store learns nothing.
    #
    # That is what made the declaration gate's own remedy unable to clear the
    # gate it names: the gate reads `proxy.country_code` off a snapshot, the
    # sentence says "press [ check ] first", and pressing it wrote nowhere the
    # gate could see. Holding the result here lets the SAVE persist it the
    # moment the record exists at the checked URL — the same condition
    # `on_check_result` requires, just satisfied one gesture later.
    #
    # Values are `(success, code, country, ip, tz, lat, lon)`; a FAILED check is
    # recorded too, so the rule is one rule ("the check you ran on the URL you
    # saved is what gets recorded") rather than a success-only special case.
    checked_geo: dict[str, tuple] = {}

    def on_paste(_: ft.ControlEvent) -> None:
        raw = (paste_field.value or "").strip()
        # A multi-line paste (several provider lines at once) used to be parsed as
        # ONE line and silently discarded — this dialog adds a single proxy, so
        # take the FIRST non-empty line and fill the fields from it rather than
        # dropping everything (audit6 LOW d). A true bulk import is out of scope.
        if "\n" in raw:
            first = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
            raw = first
        if ":" not in raw:
            return
        parsed = parse_proxy_line(raw)
        if parsed is None or not parsed["ip"] or not parsed["port"]:
            return
        host_field.value = parsed["ip"]
        port_field.value = parsed["port"]
        user_field.value = parsed["login"]
        pass_field.value = parsed["password"]
        if parsed["scheme"] in _SCHEMES:
            type_dd.value = parsed["scheme"]
        if parsed["name"]:
            name_field.value = parsed["name"]
        if parsed["rotate_url"]:
            rotate_field.value = parsed["rotate_url"]
        paste_field.value = ""
        page.update()

    # on_change catches keystroke entry; on_blur catches a PASTE that some
    # platforms (macOS flet) don't emit a change event for — a bare "ip:port"
    # pasted then clicked away wasn't splitting into fields (#220). Both call the
    # same splitter, which no-ops when the field doesn't hold a proxy string.
    paste_field.on_change = on_paste
    paste_field.on_blur = on_paste

    def current_url() -> str:
        return build_proxy_url(
            type_dd.value or "socks5",
            (host_field.value or "").strip(),
            (port_field.value or "").strip(),
            (user_field.value or "").strip(),
            (pass_field.value or "").strip(),
        )

    check_btn = ft.OutlinedButton("[ check ]", height=38, style=OUTLINE_STYLE)

    async def on_copy(_: ft.ControlEvent) -> None:
        # Copy the whole proxy line to the clipboard so it can be pasted into
        # another tool or profile. Uses the assembled URL, matching what persona
        # stores.
        url = current_url()
        if not url or url.endswith("://") or "://:" in url:
            addr_error.value = "Enter host and port before copying"
            addr_error.color = COLORS["warning"]
            addr_error.visible = True
            page.update()
            return
        await ft.Clipboard().set(url)
        copy_btn.content = ft.Text("[ copied ]", font_family=MONO, color=COLORS["success"])
        page.update()
        # Revert the label after a beat so it doesn't stay "[ copied ]" and go
        # stale once the fields are edited (audit6 LOW e).
        import asyncio

        await asyncio.sleep(1.5)
        copy_btn.content = "[ copy proxy ]"
        page.update()

    copy_btn = ft.OutlinedButton(
        "[ copy proxy ]",
        height=38,
        style=OUTLINE_STYLE,
        tooltip="Copy the full proxy string to the clipboard",
        on_click=on_copy,
    )

    def on_check_result(
        success: bool,
        code: str,
        country: str,
        ip: str,
        tz: str,
        lat: float | None,
        lon: float | None,
        checked_url: str = "",
    ) -> None:
        # Only PERSIST a check result onto the stored proxy when the URL that was
        # actually checked equals the stored proxy.url. In edit mode the dialog
        # fields may hold an UNSAVED URL; persisting its geo (or a failure flag)
        # onto the stored record — which cancel won't undo — gives a proxy whose
        # exit is in one country but whose persisted geo/tz says another, a silent
        # fingerprint mismatch across every profile using it (audit6 #6). The flag
        # icon still reflects the live check either way; only the DB write is
        # gated.
        persist = proxy is not None and checked_url == proxy.url
        # Remember it either way. This is NOT a second persist path — nothing is
        # written here — it is the dialog remembering what it just measured so a
        # SAVE of that same URL can record it once a record exists to record it
        # onto. See the `checked_geo` block above.
        if checked_url:
            checked_geo[checked_url] = (success, code, country, ip, tz, lat, lon)
        if success:
            flag_holder.content = _flag_control(code)
            if persist and on_checked is not None:
                on_checked(proxy.name, code, country, ip, tz, lat, lon)
        else:
            flag_holder.content = _fail_control()
            if persist and on_check_failed is not None:
                on_check_failed(proxy.name)

    def on_check_click(_: ft.ControlEvent) -> None:
        flag_holder.content = ft.ProgressRing(
            width=18, height=18, stroke_width=2, color=COLORS["accent"]
        )
        page.update()
        _do_check(
            page, current_url, addr_error, check_btn, proxy_service,
            on_check_result, ui=ui,
        )

    check_btn.on_click = on_check_click

    def on_submit(_: ft.ControlEvent) -> None:
        name = (name_field.value or "").strip()
        name_error.visible = addr_error.visible = tz_error.visible = False

        if not name:
            name_error.value = "Name cannot be empty"
            name_error.visible = True
            page.update()
            return
        if not (host_field.value or "").strip() or not (port_field.value or "").strip():
            addr_error.value = "Host and port are required"
            addr_error.visible = True
            page.update()
            return

        url = current_url()
        valid, err = validate_proxy_format(url)
        if not valid:
            addr_error.value = err
            addr_error.visible = True
            page.update()
            return

        # VALIDATE BEFORE ANYTHING IS WRITTEN. The store validates too — it is
        # the field's owner and a second caller must not be able to skip it —
        # but doing it here as well means a typo never half-saves the proxy and
        # then reports an error about a different field. The oracle is the same
        # vendored name set in both places, so the two cannot disagree.
        zone = (tz_field.value or "").strip()
        # PREFILLED FROM THE RAW STORED STRING, not from the country-gated
        # `declared_timezone`, so this is what "the operator did not touch the
        # field" looks like. Every test below is scoped to a CHANGED value: an
        # untouched field must not be able to fail a save (a legacy record
        # carrying a zone with no country would otherwise become unrenameable).
        prefilled = proxy.manual_timezone if proxy is not None else ""
        declaring = zone != prefilled
        if declaring and zone and not is_declarable_zone(zone):
            tz_error.value = (
                f"'{zone}' is not a timezone name. Use IANA Region/City form, "
                "e.g. Europe/Bucharest"
            )
            tz_error.visible = True
            page.update()
            return
        # A DECLARATION IS MADE *FOR* A COUNTRY, so it needs one on file. Caught
        # here, BEFORE on_save, so an add does not create the proxy and then
        # report an error about a different field — and so the operator is told
        # rather than handed a silent success that never activates (the store
        # used to store the zone with an empty country, which no later check
        # ever re-bound: `mark_checked` writes the six measured fields and is
        # untouched by this feature).
        #
        # A URL CHANGE RETIRES THE *STORED* COUNTRY, because the save is about
        # to invalidate all six geo fields plus the declaration (`update()`'s
        # `keep_geo` term) — the exit moved, so nothing measured about the old
        # one describes the new one.
        #
        # ⚠️ BUT THE STORED COUNTRY IS NOT THE ONLY COUNTRY THE DIALOG HAS, and
        # reading only it is what made this gate's own remedy unable to clear
        # it. The sentence below says "press [ check ] first"; the dialog HAS a
        # [ check ] button; and on an ADD (no record yet) or a URL-changed EDIT
        # (`checked_url != proxy.url`) `on_check_result` deliberately does not
        # persist, so pressing it turned the flag Romanian and left the gate
        # reading an empty snapshot. The operator was told to do the thing they
        # had just done — the same looping remedy this whole ticket exists to
        # remove (`launch_policy.py:340-347`), reintroduced one layer up. So the
        # gate consults what a [ check ] run IN THIS DIALOG measured for the URL
        # actually being saved, and the save persists it below before declaring.
        moved = proxy is not None and url != proxy.url
        # A FAILED last check does not count as a country on file either: the
        # code is the PREVIOUS exit's and nothing currently confirms the proxy
        # exits there. The store refuses it too (it owns the field), but catching
        # it here keeps the refusal from arriving AFTER `on_save` has already
        # landed a rename.
        stored_country = (
            proxy.country_code
            if proxy is not None and not moved and proxy.last_check_ok is not False
            else ""
        )
        pending_check = checked_geo.get(url)
        checked_country = (
            pending_check[1] if pending_check and pending_check[0] else ""
        )

        def _other_edits_pending() -> bool:
            """Is the operator losing anything BESIDES the declaration?

            Only asked when the gate below is about to refuse, and only to
            decide whether to say so. On an ADD every field is pending by
            definition (nothing exists yet); on an EDIT it is the fields
            `on_save` would have written, compared against the stored record.
            """
            if proxy is None:
                return True
            return (
                name != proxy.name
                or url != proxy.url
                or (rotate_field.value or "").strip() != proxy.rotate_url
            )
        if declaring and zone and not (stored_country or checked_country):
            # WHICH sentence depends on which state the operator is actually
            # in, because the remedy differs: "press [ check ]" is useless
            # advice to someone whose check just failed, and it is exactly the
            # loop this gate was found to create. A check that RAN and failed —
            # in this dialog just now, or on the stored record — gets the
            # fix-the-proxy sentence; only a genuinely unchecked proxy is told
            # to check.
            check_failed = (pending_check is not None and not pending_check[0]) or (
                proxy is not None and not moved and proxy.last_check_ok is False
            )
            tz_error.value = (
                "The check failed for this proxy, so its exit country is not "
                "known — a timezone is declared for that country. Fix the "
                "proxy and check it again, then declare the zone."
                if check_failed
                else "Press [ check ] first — a timezone is declared for this "
                "proxy's exit country, and there isn't one on file yet."
            )
            # ⚠️ AND SAY WHAT THIS COSTS THE REST OF THE GESTURE. The gate runs
            # BEFORE `on_save` for a good reason (an add must not create the
            # proxy and then report an error about a different field), and the
            # price is that an unrelated edit made in the same gesture — a
            # rename, a rotate-URL change — is refused along with the
            # declaration. The fields still hold what was typed, but pressing
            # [ save ] again just re-refuses, so without this sentence the
            # operator has no way to tell that the save is being blocked by the
            # zone box rather than being broken. Clearing it is the escape, and
            # it works: `declaring` goes False against an empty prefill, and
            # against a non-empty one the gate's own `zone` term stops matching.
            if _other_edits_pending():
                tz_error.value += (
                    " Your other changes here have NOT been saved — clear the "
                    "timezone box to save them without a declaration."
                )
            tz_error.visible = True
            page.update()
            return

        error = on_save(name, url, (rotate_field.value or "").strip())
        if error:
            name_error.value = error
            name_error.visible = True
            page.update()
            return
        # RECORD THE IN-DIALOG CHECK, now that a record exists at this URL.
        # `on_check_result` could not: on an add there was nothing to write
        # onto, and on a URL-changed edit writing then would have stamped the
        # unsaved URL's geography onto the stored record, which cancel would not
        # undo (audit6 #6). Both objections are about the record, and `on_save`
        # has just settled it — the SAME condition, satisfied one gesture later.
        # Only for the URLs that check could not persist itself, so an unchanged
        # edit is not re-stamped with a fresh `checked_at` for a check that was
        # already recorded.
        if pending_check is not None and (proxy is None or moved):
            ok, code, country, ip, tz, lat, lon = pending_check
            if ok and on_checked is not None:
                on_checked(name, code, country, ip, tz, lat, lon)
            elif not ok and on_check_failed is not None:
                # A failed check is recorded too. Dropping it would leave a
                # brand-new proxy looking never-checked when it was checked and
                # found broken — the network page's own ✕ state.
                on_check_failed(name)
        # AFTER the save, and keyed on the SAVED name: on an add there is no
        # record to hang a declaration off until on_save creates it, and on a
        # rename the record now lives under the new name. Both paths therefore
        # declare against `name`, not against `proxy.name`.
        #
        # ⚠️ ONLY WHEN THE OPERATOR ACTUALLY TOUCHED THE FIELD. An unconditional
        # call re-submits a value nobody typed, and that bit two ways: a bare
        # [ save ] after the exit moved RO->CZ re-armed a declaration the
        # country gate had deliberately retired (the CZ exit then launched with
        # a Romanian clock), and a URL edit re-wrote the declaration `update()`
        # had just invalidated, leaving the half-record the store's docstring
        # says cannot exist. The store refuses to re-stamp an unchanged zone
        # too — it owns the field and a second caller must not be able to skip
        # the rule — but a dialog should not be issuing a write for a field the
        # operator never touched regardless.
        if on_declare_timezone is not None and declaring:
            tz_err = on_declare_timezone(name, zone)
            if tz_err:
                tz_error.value = tz_err
                tz_error.visible = True
                page.update()
                return
        page.pop_dialog()

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        title=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                flag_holder,
                ft.Text(
                    "Edit Proxy" if is_edit else "Add Proxy",
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
            ],
        ),
        content=ft.Container(
            width=460,
            padding=ft.Padding.only(left=4, top=4, bottom=4, right=14),
            content=ft.Column(
                tight=True,
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    # ---- QUICK PASTE ----
                    section_header("QUICK PASTE", icon=ft.Icons.CONTENT_PASTE),
                    labeled(
                        "Paste proxy string",
                        paste_field,
                        icon=ft.Icons.BOLT,
                    ),
                    # ---- DETAILS ----
                    section_header("DETAILS", icon=ft.Icons.LAN_OUTLINED),
                    labeled("Name", name_field, icon=ft.Icons.LABEL_OUTLINE),
                    name_error,
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                width=140,
                                content=labeled("Type", type_dd, icon=ft.Icons.TUNE),
                            ),
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Host", host_field, icon=ft.Icons.DNS
                                ),
                            ),
                            ft.Container(
                                width=110,
                                content=labeled(
                                    "Port", port_field, icon=ft.Icons.TAG
                                ),
                            ),
                        ],
                    ),
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Username (optional)",
                                    user_field,
                                    icon=ft.Icons.PERSON_OUTLINE,
                                ),
                            ),
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Password (optional)",
                                    pass_field,
                                    icon=ft.Icons.LOCK_OUTLINE,
                                ),
                            ),
                        ],
                    ),
                    labeled(
                        "Rotate URL (optional)",
                        rotate_field,
                        icon=ft.Icons.AUTORENEW,
                    ),
                    labeled(
                        "Exit timezone (optional)",
                        tz_field,
                        icon=ft.Icons.SCHEDULE,
                    ),
                    tz_error,
                    addr_error,
                    ft.Row(spacing=10, controls=[check_btn, copy_btn]),
                ],
            ),
        ),
        actions_alignment=ft.MainAxisAlignment.END,
        actions=[
            ft.OutlinedButton(
                "[ cancel ]",
                style=OUTLINE_STYLE,
                height=40,
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ save ]" if is_edit else "[ add ]",
                style=ACCENT_STYLE,
                height=40,
                on_click=on_submit,
            ),
        ],
    )
    page.show_dialog(dlg)


def _do_check(
    page: ft.Page,
    current_url: Callable[[], str],
    addr_error: ft.Text,
    check_btn: ft.OutlinedButton,
    proxy_service: IProxyService,
    on_result: Callable[..., None],
    ui: Callable[[Callable[[], None]], None] | None = None,
) -> None:
    def _post(fn: Callable[[], None]) -> None:
        # Marshal UI mutations onto the flet session thread. The check runs on a
        # worker; touching controls / page.update() from it is the #124 freeze
        # class (audit6 #9). When no marshaler is supplied (older callers/tests),
        # run inline.
        if ui is not None:
            ui(fn)
        else:
            fn()

    url = current_url()
    if not url or "://:" in url or url.endswith("://"):
        # Pre-flight input validation: this is a "you didn't type a host/port"
        # message, NOT a proxy-check failure. Do NOT call on_result — flagging
        # the stored proxy as failed here permanently marked a working proxy bad
        # just because a field was momentarily blank mid-edit (audit6 #6).
        addr_error.value = "Enter host and port to check"
        addr_error.color = COLORS["warning"]
        addr_error.visible = True
        page.update()
        return

    check_btn.content = ft.Text(get_string("proxy_checking"), font_family=MONO)
    check_btn.disabled = True
    addr_error.visible = False
    page.update()

    def do_check() -> None:
        success, message, code, name, ip, tz, lat, lon = proxy_service.check_proxy_detailed_sync(
            url
        )

        def apply() -> None:
            check_btn.content = ft.Text("[ check ]", font_family=MONO)
            check_btn.disabled = False
            addr_error.value = message
            addr_error.color = COLORS["success"] if success else COLORS["error"]
            addr_error.visible = True
            on_result(success, code, name, ip, tz, lat, lon, url)
            page.update()

        _post(apply)

    threading.Thread(target=do_check, daemon=True).start()
