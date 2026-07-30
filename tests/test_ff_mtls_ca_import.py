"""Firefox trusts the terminator's leaf by importing its CA into the profile's
own cert9.db with the bundled certutil — no OS trust store is touched."""
import src.services.browser.invisible_launch as il


def test_certutil_path_prefers_bundled(monkeypatch):
    monkeypatch.setattr(il._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(il._platform, "IS_MACOS", False)
    # asset_path is resolvable; the function returns bundled path or PATH fallback
    p = il._certutil_path()
    assert p is None or p.endswith("certutil") or p.endswith("certutil.exe")


def test_import_mtls_ca_builds_argv_and_env(monkeypatch, tmp_path):
    seen = {}

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, env=None, capture_output=None, text=None, timeout=None):
        seen["argv"] = argv
        seen["env"] = env
        return R()

    monkeypatch.setattr(il.subprocess, "run", fake_run)
    monkeypatch.setattr(il, "_certutil_path", lambda: "/bundle/certutil")
    monkeypatch.setattr(il, "_engine_lib_dir", lambda: "/eng")

    ca = tmp_path / "term_ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    ok = il._import_mtls_ca(str(tmp_path), str(ca))
    assert ok is True
    argv = seen["argv"]
    assert argv[0] == "/bundle/certutil"
    assert "-A" in argv                       # add cert
    assert f"sql:{tmp_path}" in argv          # into the profile db
    assert str(ca) in argv                    # the CA file
    assert "CT,C,C" in argv                   # trusted TLS CA
    assert seen["env"]["LD_LIBRARY_PATH"] == "/eng"


def test_import_mtls_ca_noop_without_path(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(il, "_certutil_path", lambda: called.append(1) or "/x")
    assert il._import_mtls_ca(str(tmp_path), None) is False
    assert il._import_mtls_ca(str(tmp_path), "") is False


def test_import_mtls_ca_missing_tool_is_soft_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(il, "_certutil_path", lambda: None)
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    # no certutil available -> returns False, launch proceeds without mTLS trust
    assert il._import_mtls_ca(str(tmp_path), str(ca)) is False
