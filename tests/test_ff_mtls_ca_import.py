"""Firefox trusts the terminator's leaf by importing its CA into the profile's
own cert9.db with the bundled certutil — no OS trust store is touched."""
import src.services.browser.invisible_launch as il


def test_certutil_path_prefers_bundled(monkeypatch):
    monkeypatch.setattr(il._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(il._platform, "IS_MACOS", False)
    p = il._certutil_path()
    assert p is None or p.endswith("certutil") or p.endswith("certutil.exe")


def test_import_mtls_ca_builds_argv_and_env_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(il._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(il._platform, "IS_MACOS", False)
    calls = []

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, env=None, **k):
        calls.append((argv, env))
        return R()

    monkeypatch.setattr(il.subprocess, "run", fake_run)
    monkeypatch.setattr(il, "_certutil_path", lambda: "/bundle/certutil")
    monkeypatch.setattr(il, "_engine_lib_dir", lambda: "/eng")

    ca = tmp_path / "term_ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    assert il._import_mtls_ca(str(tmp_path), str(ca)) is True
    # a fresh profile (no cert9.db) gets an -N init then the -A import
    assert any("-N" in argv for argv, _ in calls)
    add_argv, add_env = next((a, e) for a, e in calls if "-A" in a)
    assert add_argv[0] == "/bundle/certutil"
    assert f"sql:{tmp_path}" in add_argv
    assert str(ca) in add_argv
    assert "CT,C,C" in add_argv
    # Linux points the loader at the engine's NSS libs
    assert add_env["LD_LIBRARY_PATH"] == "/eng"


def test_import_mtls_ca_windows_uses_bundled_dll_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(il._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(il._platform, "IS_MACOS", False)
    calls = []

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(il.subprocess, "run",
                        lambda argv, env=None, **k: calls.append((argv, env)) or R())
    # os.path.dirname uses the HOST's path semantics, so use a path its dirname
    # splits correctly on whatever OS runs the suite (the real value is a native
    # Windows path in production).
    import os
    tool = os.path.join("nss_win_dir", "certutil.exe")
    monkeypatch.setattr(il, "_certutil_path", lambda: tool)

    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    assert il._import_mtls_ca(str(tmp_path), str(ca)) is True
    _, env = next((a, e) for a, e in calls if "-A" in a)
    # the bundled certutil's own dir (with its NSS DLLs) is prepended to PATH
    assert env["PATH"].startswith("nss_win_dir")


def test_import_mtls_ca_noop_without_path(monkeypatch, tmp_path):
    monkeypatch.setattr(il, "_certutil_path", lambda: "/x")
    assert il._import_mtls_ca(str(tmp_path), None) is False
    assert il._import_mtls_ca(str(tmp_path), "") is False


def test_import_mtls_ca_missing_tool_is_soft_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(il, "_certutil_path", lambda: None)
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    assert il._import_mtls_ca(str(tmp_path), str(ca)) is False
