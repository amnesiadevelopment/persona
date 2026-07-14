"""The add/edit proxy dialog must build headlessly and carry every field
through on_save; the check flow must hand geo (incl. lat/lon) to on_checked."""
import time

import flet as ft

from src.models.proxy import Proxy
from src.ui.dialogs.proxy import open_proxy_dialog


class _FakePage:
    def __init__(self):
        self.shown = None
        self.popped = False

    def show_dialog(self, dlg):
        self.shown = dlg

    def pop_dialog(self):
        self.popped = True

    def update(self):
        pass


class _FakeService:
    def check_proxy_detailed_sync(self, proxy_str, timeout=None):
        return (
            True, "Proxy working", "US", "United States",
            "9.9.9.9", "America/New_York", 25.77, -80.19,
        )


def _controls(dlg):
    return dlg.content.content.controls


def _field(dlg, label):
    stack = list(_controls(dlg))
    while stack:
        c = stack.pop()
        if isinstance(c, ft.TextField) and c.label == label:
            return c
        stack.extend(getattr(c, "controls", None) or [])
    raise AssertionError(f"no field labeled {label!r}")


def test_add_dialog_builds():
    page = _FakePage()
    open_proxy_dialog(page, _FakeService(), on_save=lambda *a: None)
    assert isinstance(page.shown, ft.AlertDialog)
    assert _field(page.shown, "Rotate URL (optional)").value == ""


def test_edit_dialog_prefills_rotate_url():
    page = _FakePage()
    px = Proxy("mob", "socks5://u:p@h:1", rotate_url="https://rotate.example/x")
    open_proxy_dialog(page, _FakeService(), on_save=lambda *a: None, proxy=px)
    assert _field(page.shown, "Rotate URL (optional)").value == "https://rotate.example/x"


def test_save_passes_rotate_url():
    page = _FakePage()
    saved = []

    def on_save(name, url, rotate_url):
        saved.append((name, url, rotate_url))
        return None

    open_proxy_dialog(page, _FakeService(), on_save=on_save)
    dlg = page.shown
    _field(dlg, "Name").value = "mob"
    _field(dlg, "Host").value = "h"
    _field(dlg, "Port").value = "1080"
    _field(dlg, "Rotate URL (optional)").value = " https://rotate.example/x "
    dlg.actions[1].on_click(None)
    assert saved == [("mob", "socks5://h:1080", "https://rotate.example/x")]
    assert page.popped is True


def test_check_passes_geo_with_lat_lon_to_on_checked():
    page = _FakePage()
    checked = []
    px = Proxy("mob", "socks5://u:p@h:1")
    open_proxy_dialog(
        page,
        _FakeService(),
        on_save=lambda *a: None,
        proxy=px,
        on_checked=lambda *a: checked.append(a),
    )
    dlg = page.shown
    check_btn = next(c for c in _controls(dlg) if isinstance(c, ft.OutlinedButton))
    check_btn.on_click(None)
    deadline = time.time() + 5
    while not checked:
        assert time.time() < deadline, "check never completed"
        time.sleep(0.01)
    assert checked == [
        ("mob", "US", "United States", "9.9.9.9", "America/New_York", 25.77, -80.19)
    ]
