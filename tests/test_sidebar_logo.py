import flet as ft

from src.ui.components.sidebar import build_sidebar


def _walk(control):
    yield control
    for attr in ("content", "controls", "actions"):
        v = getattr(control, attr, None)
        if v is None:
            continue
        children = v if isinstance(v, list) else [v]
        for child in children:
            if isinstance(child, ft.BaseControl):
                yield from _walk(child)


def _sidebar(**kwargs):
    return build_sidebar(
        active_page="profiles",
        on_navigate=kwargs.pop("on_navigate", lambda k: None),
        log_panel=ft.Text("log"),
        **kwargs,
    )


def _header(sidebar):
    return sidebar.content.controls[0]


def test_header_stays_plain_row_without_logo_click():
    header = _header(_sidebar())
    assert isinstance(header, ft.Row)


def test_logo_click_wraps_header_in_clickable():
    fired = []
    header = _header(_sidebar(on_logo_click=lambda: fired.append(1)))
    assert isinstance(header, ft.GestureDetector)
    assert header.mouse_cursor == ft.MouseCursor.CLICK
    clickable = header.content
    assert isinstance(clickable, ft.Container)
    assert clickable.ink is True
    assert clickable.tooltip == "home"
    # the original logo + title row is inside the clickable
    assert isinstance(clickable.content, ft.Row)
    texts = [c for c in _walk(clickable) if isinstance(c, ft.Text)]
    assert any(t.value for t in texts)
    clickable.on_click(None)
    assert fired == [1]


def test_logo_click_does_not_hit_navigation():
    navigated = []
    fired = []
    sidebar = _sidebar(
        on_navigate=lambda k: navigated.append(k),
        on_logo_click=lambda: fired.append(1),
    )
    _header(sidebar).content.on_click(None)
    assert fired == [1]
    assert navigated == []


def test_nav_buttons_still_navigate_with_logo_click_wired():
    navigated = []
    sidebar = _sidebar(
        on_navigate=lambda k: navigated.append(k),
        on_logo_click=lambda: None,
    )
    header = _header(sidebar)
    nav_containers = [
        c
        for c in _walk(sidebar)
        if isinstance(c, ft.Container)
        and c.on_click is not None
        and c is not header.content
    ]
    assert len(nav_containers) == 5
    for c in nav_containers:
        c.on_click(None)
    assert navigated == ["profiles", "network", "bookmarks", "tags", "connect"]
