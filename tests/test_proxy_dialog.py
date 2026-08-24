"""The add/edit proxy dialog must build headlessly and carry every field
through on_save; the check flow must hand geo (incl. lat/lon) to on_checked."""
import time

import flet as ft

from src.models.proxy import Proxy
from src.ui.dialogs.proxy import open_proxy_dialog
from src.utils.validation import PROXY_SCHEMES


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


def _walk(control):
    """Yield every control in the dialog tree (depth-first)."""
    yield control
    for attr in ("content", "controls", "actions"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        items = child if isinstance(child, list) else [child]
        for c in items:
            if c is not None and hasattr(c, "__dict__"):
                yield from _walk(c)


def _label_text_of(control):
    val = getattr(control, "value", None)
    if isinstance(val, str):
        return val
    for k in getattr(control, "controls", None) or []:
        v = getattr(k, "value", None)
        if isinstance(v, str):
            return v
    return None


def _control_under_label(dlg, label, kind):
    # Labels now sit ABOVE the field (via labeled()): find the column whose first
    # child's text matches `label`, then return the field of `kind` in it.
    for col in _walk(dlg):
        controls = getattr(col, "controls", None)
        if not controls or len(controls) < 2:
            continue
        if _label_text_of(controls[0]) == label:
            for c in controls:
                if isinstance(c, kind):
                    return c
    raise AssertionError(f"no {kind.__name__} labeled {label!r}")


def _field(dlg, label):
    return _control_under_label(dlg, label, ft.TextField)


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


def test_paste_fills_all_fields_from_provider_string():
    page = _FakePage()
    open_proxy_dialog(page, _FakeService(), on_save=lambda *a: None)
    dlg = page.shown
    paste = _field(dlg, "Paste proxy string")
    paste.value = (
        "socks5://01kx0f7zfhvrexcnfgeh4hm0t4:RXuosXF1wj26ySsn@190.2.142.241:10496"
        ":MobUnited States - Miami"
        "[https://api.asocks.com/proxy/4e712f5b-7aab-11f1-ae21-bc24114c89e8/refresh-ip]"
    )
    paste.on_change(None)
    assert _field(dlg, "Host").value == "190.2.142.241"
    assert _field(dlg, "Port").value == "10496"
    assert _field(dlg, "Username (optional)").value == "01kx0f7zfhvrexcnfgeh4hm0t4"
    assert _field(dlg, "Password (optional)").value == "RXuosXF1wj26ySsn"
    assert _field(dlg, "Name").value == "MobUnited States - Miami"
    assert _field(dlg, "Rotate URL (optional)").value == (
        "https://api.asocks.com/proxy/4e712f5b-7aab-11f1-ae21-bc24114c89e8/refresh-ip"
    )
    dd = _control_under_label(dlg, "Type", ft.Dropdown)
    assert dd.value == "socks5"
    assert paste.value == ""


def test_bare_ip_port_splits_into_host_and_port():
    page = _FakePage()
    open_proxy_dialog(page, _FakeService(), on_save=lambda *a: None)
    dlg = page.shown
    paste = _field(dlg, "Paste proxy string")
    paste.value = "190.2.142.241:10496"
    paste.on_change(None)
    assert _field(dlg, "Host").value == "190.2.142.241"
    assert _field(dlg, "Port").value == "10496"
    assert paste.value == ""


def test_paste_splits_on_blur_when_change_didnt_fire():
    # macOS flet may not emit on_change for a paste; on_blur must still split
    # the pasted string when the user clicks away (#220).
    page = _FakePage()
    open_proxy_dialog(page, _FakeService(), on_save=lambda *a: None)
    dlg = page.shown
    paste = _field(dlg, "Paste proxy string")
    # simulate a paste that fired NO change event: set value, then blur
    paste.value = "190.2.142.241:10496"
    assert paste.on_blur is not None
    paste.on_blur(None)
    assert _field(dlg, "Host").value == "190.2.142.241"
    assert _field(dlg, "Port").value == "10496"


def test_empty_paste_keeps_manual_entry():
    page = _FakePage()
    open_proxy_dialog(page, _FakeService(), on_save=lambda *a: None)
    dlg = page.shown
    _field(dlg, "Name").value = "manual"
    _field(dlg, "Host").value = "h"
    _field(dlg, "Port").value = "1080"
    paste = _field(dlg, "Paste proxy string")
    paste.value = ""
    paste.on_change(None)
    assert _field(dlg, "Name").value == "manual"
    assert _field(dlg, "Host").value == "h"
    assert _field(dlg, "Port").value == "1080"


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
    check_btn = next(
        c for c in _walk(dlg)
        if isinstance(c, ft.OutlinedButton) and getattr(c, "content", None) == "[ check ]"
    )
    check_btn.on_click(None)
    deadline = time.time() + 5
    while not checked:
        assert time.time() < deadline, "check never completed"
        time.sleep(0.01)
    assert checked == [
        ("mob", "US", "United States", "9.9.9.9", "America/New_York", 25.77, -80.19)
    ]


def _check_btn(dlg):
    return next(
        c for c in _walk(dlg)
        if isinstance(c, ft.OutlinedButton) and getattr(c, "content", None) == "[ check ]"
    )


def test_check_of_edited_unsaved_url_does_not_persist_geo():
    # audit6 #6: in edit mode, checking a URL the user changed but hasn't SAVED
    # must NOT write its geo onto the stored proxy (cancel wouldn't undo it),
    # else the stored record's geo/tz disagree with its actual exit — a silent
    # fingerprint mismatch. The flag icon still updates; only the DB write is
    # gated on the checked URL matching the stored url.
    page = _FakePage()
    checked = []
    px = Proxy("mob", "socks5://u:p@h:1")
    open_proxy_dialog(
        page, _FakeService(), on_save=lambda *a: None, proxy=px,
        on_checked=lambda *a: checked.append(a),
    )
    dlg = page.shown
    # change the host to something the stored url doesn't have (unsaved edit)
    _field(dlg, "Host").value = "different-host"
    btn = _check_btn(dlg)
    btn.on_click(None)
    deadline = time.time() + 5
    while btn.disabled:
        assert time.time() < deadline, "check never completed"
        time.sleep(0.01)
    time.sleep(0.05)
    assert checked == [], "geo of an unsaved URL must not be persisted"


def test_preflight_invalid_input_does_not_flag_stored_proxy():
    # audit6 #6: clearing the host mid-edit then clicking [check] is an input
    # error, not a proxy failure — it must NOT call on_check_failed and flag a
    # working stored proxy as bad.
    page = _FakePage()
    failed = []
    px = Proxy("mob", "socks5://u:p@h:1")
    open_proxy_dialog(
        page, _FakeService(), on_save=lambda *a: None, proxy=px,
        on_check_failed=lambda name: failed.append(name),
    )
    dlg = page.shown
    _field(dlg, "Host").value = ""   # blank host -> pre-flight invalid
    _field(dlg, "Port").value = ""
    _check_btn(dlg).on_click(None)
    time.sleep(0.05)
    assert failed == [], "a pre-flight input error must not flag the stored proxy"


def test_type_dropdown_offers_every_accepted_scheme():
    """The Type dropdown must be the schemes validation ACCEPTS, not a subset.

    validation.PROXY_SCHEMES is the single source of truth for what persona
    takes; a second hardcoded list in the dialog is the drift that comment
    forbids, and here it silently narrows what an operator can even pick.
    """
    page = _FakePage()
    open_proxy_dialog(page, _FakeService(), on_save=lambda *a: None)
    dd = _control_under_label(page.shown, "Type", ft.Dropdown)
    assert {o.key for o in dd.options} == set(PROXY_SCHEMES)


def test_editing_a_socks5h_proxy_keeps_its_scheme():
    """Opening a stored socks5h proxy and saving must not rewrite it to socks5.

    socks5h is an accepted, meaningful stored scheme — exit_guard reads the
    credential back in socks5h form, and the Chromium seam normalises it to
    socks5 on its own. A dropdown that can't hold the value silently downgraded
    the record on a save the operator made for an unrelated field.
    """
    page = _FakePage()
    saved = []
    px = Proxy("dc", "socks5h://u:p@1.2.3.4:1080")
    open_proxy_dialog(
        page,
        _FakeService(),
        on_save=lambda name, url, rot: saved.append((name, url, rot)),
        proxy=px,
    )
    dlg = page.shown
    assert _control_under_label(dlg, "Type", ft.Dropdown).value == "socks5h"
    dlg.actions[1].on_click(None)
    assert saved == [("dc", "socks5h://u:p@1.2.3.4:1080", "")]


def test_paste_of_a_socks4_line_selects_socks4():
    """A pasted provider line's scheme must survive into the Type dropdown.

    An unrecognised scheme left the dropdown on its socks5 default, so a socks4
    line pasted into a fresh dialog saved as socks5 — a proxy that cannot
    connect, reported as if the paste had been understood.
    """
    page = _FakePage()
    open_proxy_dialog(page, _FakeService(), on_save=lambda *a: None)
    dlg = page.shown
    paste = _field(dlg, "Paste proxy string")
    paste.value = "socks4://198.51.100.7:1080"
    paste.on_change(None)
    assert _control_under_label(dlg, "Type", ft.Dropdown).value == "socks4"
