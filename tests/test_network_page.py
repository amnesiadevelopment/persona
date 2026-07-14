"""The network page must build headlessly and wire the per-proxy buttons."""
import flet as ft

from src.models.proxy import Proxy
from src.ui.components.network_page import build_network_page


def _build(proxies, **handlers):
    defaults = {
        "on_add": lambda _: None,
        "on_edit": lambda n: None,
        "on_delete": lambda n: None,
        "on_check": lambda n: None,
        "on_rotate": lambda n: None,
    }
    defaults.update(handlers)
    return build_network_page(proxies, **defaults)


def _row_buttons(page_container):
    rows = page_container.content.controls[2].controls
    return rows[0].content.controls[1].controls


def test_builds_empty():
    assert isinstance(_build([]), ft.Container)


def test_row_has_rotate_button():
    page = _build([Proxy("mob", "socks5://u:p@h:1")])
    labels = [b.content for b in _row_buttons(page)]
    assert labels == ["[ check ]", "[ rotate ]", "[ edit ]", "[ x ]"]


def test_rotate_button_invokes_handler_with_name():
    clicked = []
    page = _build(
        [Proxy("mob", "socks5://u:p@h:1")],
        on_rotate=lambda n: clicked.append(n),
    )
    rotate_btn = _row_buttons(page)[1]
    rotate_btn.on_click(None)
    assert clicked == ["mob"]


def test_buttons_disabled_while_checking():
    page = build_network_page(
        [Proxy("mob", "socks5://u:p@h:1")],
        on_add=lambda _: None,
        on_edit=lambda n: None,
        on_delete=lambda n: None,
        on_check=lambda n: None,
        on_rotate=lambda n: None,
        checking={"mob"},
    )
    check_btn, rotate_btn = _row_buttons(page)[:2]
    assert check_btn.disabled is True
    assert rotate_btn.disabled is True
