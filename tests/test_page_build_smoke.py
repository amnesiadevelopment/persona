"""Every main content page must BUILD headlessly without raising, both empty and
populated.

The page builders render lists of cards/rows and format per-item strings; a bad
index, an unguarded empty list, or a None field surfaces only when the tree is
actually constructed. These smokes build each page in both states so a render
regression fails a test instead of showing the user a broken page.
"""
import flet as ft

from src.models.bookmark import Bookmark, Pool
from src.models.profile import Profile
from src.models.proxy import Proxy
from src.services.ssh.store import SSHHost
from src.ui.components.bookmarks_page import build_bookmarks_page
from src.ui.components.connect_page import build_connect_page
from src.ui.components.network_page import build_network_page
from src.ui.components.ssh_page import build_ssh_section
from src.ui.components.tags_page import build_tags_page


def _noop(*_a, **_k):
    return None


def _run(*_a, **_k):
    return (0, "", "")


_PROFILES = [
    Profile(name="alpha", tags=["work", "eu"]),
    Profile(name="beta", engine="firefox", tags=["work"]),
    Profile(name="gamma"),
]
_BOOKMARKS = [
    Bookmark("browserleaks", "https://browserleaks.com/"),
    Bookmark("iphey", "https://iphey.com/"),
]
_POOLS = [Pool(name="pool1", bookmark_names=["iphey"])]
_PROXIES = [
    Proxy(name="p1", url="socks5://u:p@1.2.3.4:1080", country_code="US",
          country_name="United States", last_ip="1.2.3.4", last_check_ok=True),
    Proxy(name="p2", url="socks5://9.9.9.9:1080"),  # sparse: no geo, no check
]
_HOSTS = [
    SSHHost(name="box", host="1.2.3.4", port=2222, username="root", profile="alpha"),
    SSHHost(name="bare", host="5.6.7.8"),
]


def _assert_container(ctrl):
    assert ctrl is not None
    # every builder returns a Container (or a tuple whose first item is a control)
    first = ctrl[0] if isinstance(ctrl, tuple) else ctrl
    assert isinstance(first, ft.Control)


def test_bookmarks_page_populated_builds():
    _assert_container(build_bookmarks_page(
        _BOOKMARKS, _POOLS, _noop, _noop, _noop, _noop, _noop, _noop
    ))


def test_bookmarks_page_empty_builds():
    _assert_container(build_bookmarks_page(
        [], [], _noop, _noop, _noop, _noop, _noop, _noop
    ))


def test_tags_page_populated_builds():
    _assert_container(build_tags_page(_PROFILES, _noop, _noop))


def test_tags_page_no_tags_builds():
    _assert_container(build_tags_page([Profile(name="x")], _noop, _noop))


def test_tags_page_empty_builds():
    _assert_container(build_tags_page([], _noop, _noop))


def test_network_page_populated_builds():
    _assert_container(build_network_page(
        _PROXIES, _noop, _noop, _noop, _noop, _noop
    ))


def test_network_page_empty_builds():
    _assert_container(build_network_page([], _noop, _noop, _noop, _noop, _noop))


def test_network_page_with_checking_set_builds():
    _assert_container(build_network_page(
        _PROXIES, _noop, _noop, _noop, _noop, _noop, checking={"p1"}
    ))


def test_ssh_section_populated_builds():
    _assert_container(build_ssh_section(_HOSTS, _noop, _noop, _noop, _run))


def test_ssh_section_empty_builds():
    _assert_container(build_ssh_section([], _noop, _noop, _noop, _run))


def test_connect_page_populated_builds():
    _assert_container(build_connect_page(
        _PROFILES, "tok", "add cmd", '{"json": true}', _noop,
        True, _noop, "http://127.0.0.1:8765", _HOSTS, _noop, _noop, _noop, _run,
    ))


def test_connect_page_empty_builds():
    _assert_container(build_connect_page(
        [], "", "", "", _noop, False, _noop, "", [], _noop, _noop, _noop, _run,
    ))
