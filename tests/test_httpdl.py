"""The range-preserving redirect handler must carry the Range header onto the
follow-up request so a resumed download gets the tail (206), not the whole file
(200), after GitHub's 302 to a signed CDN URL."""

import urllib.request

from src.utils.httpdl import KeepRangeRedirect, range_opener


class _Headers(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def test_redirect_reattaches_range_header():
    handler = KeepRangeRedirect()
    req = urllib.request.Request("http://gh/asset")
    req.add_header("Range", "bytes=500-")
    new = handler.redirect_request(
        req, fp=None, code=302, msg="Found",
        headers=_Headers({"location": "http://cdn/asset"}),
        newurl="http://cdn/asset",
    )
    assert new is not None
    assert new.get_header("Range") == "bytes=500-"


def test_redirect_without_range_leaves_it_absent():
    handler = KeepRangeRedirect()
    req = urllib.request.Request("http://gh/asset")
    new = handler.redirect_request(
        req, fp=None, code=302, msg="Found",
        headers=_Headers({"location": "http://cdn/asset"}),
        newurl="http://cdn/asset",
    )
    assert new is not None
    assert new.get_header("Range") is None


def test_range_opener_installs_the_handler():
    opener = range_opener()
    assert any(isinstance(h, KeepRangeRedirect) for h in opener.handlers)
