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
    )
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
