from src.services.engine.updater import (
    appimage_url_for,
    is_newer,
    parse_version,
)


def test_parse_version():
    assert parse_version("144.0.7559.132") == (144, 0, 7559, 132)
    assert parse_version("") == ()
    assert parse_version("v143.0.1") == (143, 0, 1)


def test_is_newer():
    assert is_newer("144.0.7559.132", "143.0.7000.10") is True
    assert is_newer("144.0.7559.132", "144.0.7559.132") is False
    assert is_newer("144.0.7559.100", "144.0.7559.132") is False


def test_is_newer_edges():
    assert is_newer("144.0.0.1", "") is True       # nothing installed
    assert is_newer("", "144.0.0.1") is False       # no latest info


def test_appimage_url():
    url = appimage_url_for("144.0.7559.132")
    assert url.endswith("/144.0.7559.132/ungoogled-chromium-144.0.7559.132-1-x86_64.AppImage")
    assert url.startswith("https://github.com/adryfish/fingerprint-chromium/")
