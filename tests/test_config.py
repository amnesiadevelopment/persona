import os
import subprocess
import sys


def _import_config_with(env_overrides, tmp_home):
    env = dict(os.environ)
    env["PERSONA_HOME"] = str(tmp_home)
    env.update(env_overrides)
    code = (
        "import src.core.config as c;"
        "print('PORT', c.API_PORT);"
        "print('TIMEOUT', c.PROXY_CHECK_TIMEOUT)"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
     encoding="utf-8")
    return r


def test_non_numeric_api_port_falls_back_not_crash(tmp_path):
    # #15: a bad PERSONA_API_PORT must not crash the app at import with a raw
    # traceback and no window — it falls back to the default.
    r = _import_config_with({"PERSONA_API_PORT": "not-a-port"}, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "PORT 8000" in r.stdout


def test_non_numeric_proxy_timeout_falls_back(tmp_path):
    r = _import_config_with({"PERSONA_PROXY_TIMEOUT": "abc"}, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "TIMEOUT 10" in r.stdout


def test_valid_int_env_still_honored(tmp_path):
    r = _import_config_with(
        {"PERSONA_API_PORT": "9099", "PERSONA_PROXY_TIMEOUT": "30"}, tmp_path
    )
    assert r.returncode == 0, r.stderr
    assert "PORT 9099" in r.stdout
    assert "TIMEOUT 30" in r.stdout


def test_out_of_range_api_port_falls_back(tmp_path):
    # audit5 LOW: a port outside 1..65535 (0, negative, >65535) must fall back to
    # the default, not sail through to bind() and fail with an opaque error.
    for bad in ("0", "-1", "99999", "70000"):
        r = _import_config_with({"PERSONA_API_PORT": bad}, tmp_path)
        assert r.returncode == 0, r.stderr
        assert "PORT 8000" in r.stdout, f"{bad} should clamp to default"


def test_boundary_api_ports_honored(tmp_path):
    for good in ("1", "65535"):
        r = _import_config_with({"PERSONA_API_PORT": good}, tmp_path)
        assert r.returncode == 0, r.stderr
        assert f"PORT {good}" in r.stdout


def test_unwritable_persona_home_falls_back_not_crash(tmp_path):
    # audit5 #7: an unmakeable PERSONA_HOME must not abort the import of
    # core.config (loaded extremely early) with a bare traceback and no window.
    # Point PERSONA_HOME at a path UNDER a regular file — makedirs raises, and
    # the import must survive by falling back.
    afile = tmp_path / "iam_a_file"
    afile.write_text("x", encoding="utf-8")
    bad_home = str(afile / "nested" / "home")  # parent is a file → OSError
    env = dict(os.environ)
    env["PERSONA_HOME"] = bad_home
    code = "import src.core.config as c; print('HOME', c.PERSONA_HOME)"
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
     encoding="utf-8")
    assert r.returncode == 0, r.stderr
    # it fell back to ~/.persona, not the impossible path
    assert bad_home not in r.stdout
    assert "HOME" in r.stdout
