import os

import pytest

from src.services.browser import cdp


def _write_active_port(tmp_path, monkeypatch, name, port):
    monkeypatch.setattr(cdp, "DATA_DIR", str(tmp_path))
    profile_dir = tmp_path / name
    profile_dir.mkdir()
    (profile_dir / "DevToolsActivePort").write_text(
        f"{port}\n/devtools/browser/abc-123\n", encoding="utf-8"
    )
    return profile_dir


def test_reads_port_from_devtools_active_port(tmp_path, monkeypatch):
    _write_active_port(tmp_path, monkeypatch, "acc", 51234)
    assert cdp.read_cdp_port("acc") == 51234


def test_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(cdp, "DATA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        cdp.read_cdp_port("not-running")


def test_ignores_stale_file_older_than_launch(tmp_path, monkeypatch):
    # A DevToolsActivePort left from a previous run must not be trusted as the
    # current port. read_cdp_port takes a launch timestamp and rejects a file
    # written before it.
    pd = _write_active_port(tmp_path, monkeypatch, "acc", 40000)
    stale_mtime = os.path.getmtime(pd / "DevToolsActivePort")
    with pytest.raises(RuntimeError):
        cdp.read_cdp_port("acc", not_before=stale_mtime + 100)


def test_no_predictable_port_from_name(tmp_path, monkeypatch):
    # The debugging port must NOT be derivable from the profile name — that was
    # the local-takeover surface. cdp_port_for is gone.
    assert not hasattr(cdp, "cdp_port_for")
