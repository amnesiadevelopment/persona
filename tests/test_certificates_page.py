"""The certificates page must build headlessly and wire add/edit/delete."""
import flet as ft

from src.services.cert.store import Certificate
from src.ui.components.certificates_page import build_certificates_page


def _build(certs, **handlers):
    defaults = {
        "on_add": lambda _: None,
        "on_edit": lambda n: None,
        "on_delete": lambda n: None,
    }
    defaults.update(handlers)
    return build_certificates_page(certs, **defaults)


def _rows(page_container):
    return page_container.content.controls[2].controls


def _row_buttons(page_container):
    return _rows(page_container)[0].content.controls[1].controls


def test_builds_empty():
    assert isinstance(_build([]), ft.Container)


def test_add_button_invokes_handler():
    clicked = []
    page = _build([], on_add=lambda _: clicked.append(1))
    top = page.content.controls[0]
    add_btn = top.controls[1]
    add_btn.on_click(None)
    assert clicked == [1]


def test_row_has_edit_and_delete():
    page = _build([Certificate("admin", "/v/admin.p12")])
    labels = [b.content for b in _row_buttons(page)]
    assert labels == ["[ edit ]", "[ x ]"]


def test_edit_button_invokes_handler_with_name():
    clicked = []
    page = _build(
        [Certificate("admin", "/v/admin.p12")],
        on_edit=lambda n: clicked.append(n),
    )
    _row_buttons(page)[0].on_click(None)
    assert clicked == ["admin"]


def test_delete_button_invokes_handler_with_name():
    clicked = []
    page = _build(
        [Certificate("admin", "/v/admin.p12")],
        on_delete=lambda n: clicked.append(n),
    )
    _row_buttons(page)[1].on_click(None)
    assert clicked == ["admin"]


def test_row_shows_p12_filename_not_full_path():
    page = _build([Certificate("admin", "/vault/certs/admin.p12")])
    texts = _collect_text(page)
    assert any("admin.p12" in t for t in texts)
    assert not any("/vault/certs/" in t for t in texts)


def _collect_text(control, out=None):
    if out is None:
        out = []
    if isinstance(control, ft.Text) and control.value:
        out.append(control.value)
    for attr in ("content", "controls"):
        v = getattr(control, attr, None)
        if v is None:
            continue
        for child in (v if isinstance(v, list) else [v]):
            if isinstance(child, ft.BaseControl):
                _collect_text(child, out)
    return out
