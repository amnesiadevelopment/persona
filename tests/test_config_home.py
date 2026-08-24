import importlib
import os
import sys


def _reload_config(monkeypatch, **env):
    for k in [
        "PERSONA_HOME", "PERSONA_PROFILES_FILE", "PERSONA_PROXIES_FILE",
        "PERSONA_BOOKMARKS_FILE", "PERSONA_DATA_DIR", "PERSONA_LOG_DIR",
        "PERSONA_ENGINE_DIR",
    ]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import src.core.config as cfg
    return importlib.reload(cfg)


def test_defaults_under_persona_home(monkeypatch, tmp_path):
    cfg = _reload_config(monkeypatch, PERSONA_HOME=str(tmp_path))
    assert cfg.PROFILES_FILE == os.path.join(str(tmp_path), "profiles.json")
    assert cfg.PROXIES_FILE == os.path.join(str(tmp_path), "proxies.json")
    assert cfg.BOOKMARKS_FILE == os.path.join(str(tmp_path), "bookmarks.json")
    assert cfg.DATA_DIR == os.path.join(str(tmp_path), "persona_data")
    assert cfg.LOG_DIR == os.path.join(str(tmp_path), "logs")
    assert cfg.ENGINE_DIR == os.path.join(str(tmp_path), "engine")


def test_home_is_created(monkeypatch, tmp_path):
    home = tmp_path / "fresh"
    _reload_config(monkeypatch, PERSONA_HOME=str(home))
    assert home.is_dir()


def test_explicit_file_override_wins(monkeypatch, tmp_path):
    cfg = _reload_config(
        monkeypatch,
        PERSONA_HOME=str(tmp_path),
        PERSONA_PROFILES_FILE="/custom/p.json",
    )
    assert cfg.PROFILES_FILE == "/custom/p.json"
    # others still under home
    assert cfg.DATA_DIR == os.path.join(str(tmp_path), "persona_data")


def test_default_home_is_dot_persona(monkeypatch):
    cfg = _reload_config(monkeypatch)
    assert cfg.PERSONA_HOME == os.path.expanduser("~/.persona")


# --- PS-127: _under_home's result is absolute for every input ---
#
# The pre-existing cases above all use absolute overrides (tmp_path, /custom),
# so they passed against the unfixed code and prove nothing about this. The
# RELATIVE case is the one that failed, and it has to be constructed on purpose:
# it is the shape `.env.example` ships (`PERSONA_DATA_DIR=persona_data`).

OVERRIDE_ENV = {
    "DATA_DIR": "PERSONA_DATA_DIR",
    "LOG_DIR": "PERSONA_LOG_DIR",
    "ENGINE_DIR": "PERSONA_ENGINE_DIR",
}


def test_relative_override_is_anchored_to_cwd_not_returned_verbatim(
    monkeypatch, tmp_path
):
    """The defect: a relative override used to come back verbatim, so every
    consumer joining onto it resolved against whatever cwd it happened to hold.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    cfg = _reload_config(
        monkeypatch,
        PERSONA_HOME=str(tmp_path / "home"),
        PERSONA_DATA_DIR="mydata",
        PERSONA_LOG_DIR="mylogs",
        PERSONA_ENGINE_DIR="myeng",
    )

    assert cfg.DATA_DIR == str(workdir / "mydata")
    assert cfg.LOG_DIR == str(workdir / "mylogs")
    assert cfg.ENGINE_DIR == str(workdir / "myeng")


def test_relative_override_does_not_move_when_cwd_moves(monkeypatch, tmp_path):
    """The reason the anchoring matters. persona's cwd is not fixed — main.py's
    _ensure_valid_cwd() relocates the process to ~ / $HOME / /tmp / / when a
    self-update re-exec strands it. Under the old behaviour the SAME constant
    named a different directory after that move; now it does not.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(workdir)

    cfg = _reload_config(
        monkeypatch,
        PERSONA_HOME=str(tmp_path / "home"),
        PERSONA_DATA_DIR="mydata",
    )
    resolved_before = os.path.join(cfg.DATA_DIR, "acme")

    # the cwd moves out from under the running process
    monkeypatch.chdir(elsewhere)
    resolved_after = os.path.join(cfg.DATA_DIR, "acme")

    assert resolved_before == resolved_after == str(workdir / "mydata" / "acme")


def test_every_constant_is_absolute_under_all_override_shapes(
    monkeypatch, tmp_path
):
    """The contract itself, across the three shapes an operator can produce."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    shapes = {
        "unset": {},
        "absolute": {
            "PERSONA_DATA_DIR": str(tmp_path / "abs_data"),
            "PERSONA_LOG_DIR": str(tmp_path / "abs_logs"),
            "PERSONA_ENGINE_DIR": str(tmp_path / "abs_engine"),
        },
        "relative": {
            "PERSONA_DATA_DIR": "mydata",
            "PERSONA_LOG_DIR": "mylogs",
            "PERSONA_ENGINE_DIR": "myeng",
        },
    }

    for shape, overrides in shapes.items():
        cfg = _reload_config(
            monkeypatch, PERSONA_HOME=str(tmp_path / "home"), **overrides
        )
        for const in OVERRIDE_ENV:
            value = getattr(cfg, const)
            assert os.path.isabs(value), f"{const} not absolute under {shape}: {value!r}"


def test_absolute_override_is_returned_exactly_as_given(monkeypatch, tmp_path):
    """"Normalisation, not relocation." An absolute override is passed through
    untouched — deliberately NOT through abspath/normpath, which would rewrite a
    trailing slash or an embedded '..' and thereby move a path the operator
    spelled on purpose.
    """
    monkeypatch.chdir(tmp_path)
    spellings = [
        str(tmp_path / "abs_data") + "/",
        str(tmp_path / "sub" / ".." / "abs_data"),
        str(tmp_path / "." / "abs_data"),
    ]
    for spelling in spellings:
        cfg = _reload_config(
            monkeypatch,
            PERSONA_HOME=str(tmp_path / "home"),
            PERSONA_DATA_DIR=spelling,
        )
        assert cfg.DATA_DIR == spelling


def test_default_home_layout_is_unchanged_by_normalisation(monkeypatch, tmp_path):
    """The other "must not change": an operator who set no relative override
    sees nothing move. Guards against a fix that relocates the default layout.
    """
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    cfg = _reload_config(monkeypatch, PERSONA_HOME=str(home))

    assert cfg.DATA_DIR == os.path.join(str(home), "persona_data")
    assert cfg.LOG_DIR == os.path.join(str(home), "logs")
    assert cfg.ENGINE_DIR == os.path.join(str(home), "engine")
