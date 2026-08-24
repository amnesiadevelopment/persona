import importlib
import os
import sys


def _reload_config(monkeypatch, **env):
    for k in [
        "PERSONA_HOME", "PERSONA_PROFILES_FILE", "PERSONA_PROXIES_FILE",
        "PERSONA_BOOKMARKS_FILE", "PERSONA_DATA_DIR", "PERSONA_LOG_DIR",
        "PERSONA_ENGINE_DIR", "PERSONA_CERTS_FILE", "PERSONA_CERTS_DIR",
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


# --- PS-127 rework: the absoluteness PREDICATE itself ---
#
# The first revision used a bare os.path.isabs and turned Windows CI red. The
# tests above could not catch it, for the same structural reason the ORIGINAL
# suite could not catch the original defect: every "absolute" spelling they try
# is derived from tmp_path, which on Windows is drive-absolute (C:\...), so the
# uncovered shape was ROOTED-BUT-DRIVELESS ('/custom/p.json'). That shape is
# what the one hardcoded literal in this file happens to use, which is why CI
# caught what the new tests did not.
#
# These exercise the predicate DIRECTLY with an injected path flavour, so the
# Windows branch is provable from a POSIX run. A defect invisible on POSIX is
# exactly the kind that reaches CI.

import ntpath
import posixpath

from src.core.config import _is_already_absolute


def test_rooted_driveless_path_is_absolute_on_windows_flavour():
    """The regression that turned Windows CI red at cdcb5cf.

    Python 3.13 changed ntpath.isabs: a rooted but driveless path stopped
    counting as absolute ('/custom/p.json' -> True on 3.12, False on 3.13).
    CI pins 3.13, so a bare isabs sent that operator-spelled path to abspath,
    which pinned it to the process's current drive ('C:\\custom\\p.json') — a
    RELOCATION, which the ticket names as a regression, and which reintroduces
    drive-dependence into the function whose job is to remove cwd-dependence.
    """
    for rooted in ["/custom/p.json", r"\custom\p.json", "/data", "/"]:
        assert _is_already_absolute(rooted, ntpath), (
            f"{rooted!r} must count as absolute on a Windows path flavour; "
            "abspath would otherwise pin it to the current drive"
        )


def test_drive_absolute_and_unc_are_absolute_on_windows_flavour():
    for val in ["C:/data", r"C:\data", "//server/share/x", r"\\server\share\x"]:
        assert _is_already_absolute(val, ntpath)


def test_genuinely_relative_is_not_absolute_on_windows_flavour():
    """The other half: the rooted allowance must not swallow real relatives,
    or the normalisation this ticket exists to add would stop happening."""
    for val in ["mydata", "persona_data", "a/b", r"a\b", "", "C:mydata"]:
        assert not _is_already_absolute(val, ntpath), (
            f"{val!r} is relative and must still be anchored"
        )


def test_predicate_is_exactly_isabs_on_posix():
    """Linux/macOS behaviour must be untouched by the Windows accommodation.

    This is a real constraint, not a formality: a backslash is a LEGAL
    character in a POSIX filename, so '\\custom\\p.json' on Linux is a
    RELATIVE file whose name merely contains backslashes. An ungated rooted
    test would return it verbatim and leave a cwd-dependent constant — the
    very defect this ticket fixes, reintroduced on the other platform.
    """
    for val in [
        "/x/y", "mydata", r"\custom\p.json", r"a\b", "C:/data", "",
        "/tmp/abs/", "/tmp/e/../e", "//server/share/x",
    ]:
        assert _is_already_absolute(val, posixpath) is posixpath.isabs(val), (
            f"POSIX behaviour diverged from os.path.isabs for {val!r}"
        )


def test_rooted_driveless_override_is_returned_exactly_as_given(
    monkeypatch, tmp_path
):
    """The end-to-end case, with a hardcoded literal rather than a tmp_path
    string — the spelling the tmp_path-derived cases structurally cannot reach.
    """
    cfg = _reload_config(
        monkeypatch,
        PERSONA_HOME=str(tmp_path),
        PERSONA_PROFILES_FILE="/custom/p.json",
        PERSONA_DATA_DIR="/custom/data",
    )
    assert cfg.PROFILES_FILE == "/custom/p.json"
    assert cfg.DATA_DIR == "/custom/data"


# --- PS-127 rework: the blast radius is EIGHT constants, not three ---
#
# _under_home backs eight constants (PROFILES_FILE .. ENGINE_DIR in
# src/core/config.py). The first revision's sweep enumerated
# DATA_DIR/LOG_DIR/ENGINE_DIR only — and PROFILES_FILE is the one that
# actually blew up on Windows CI.

ALL_UNDER_HOME = {
    "PROFILES_FILE": ("PERSONA_PROFILES_FILE", "profiles.json"),
    "PROXIES_FILE": ("PERSONA_PROXIES_FILE", "proxies.json"),
    "CERTS_FILE": ("PERSONA_CERTS_FILE", "certificates.json"),
    "CERTS_DIR": ("PERSONA_CERTS_DIR", "certificates"),
    "BOOKMARKS_FILE": ("PERSONA_BOOKMARKS_FILE", "bookmarks.json"),
    "DATA_DIR": ("PERSONA_DATA_DIR", "persona_data"),
    "LOG_DIR": ("PERSONA_LOG_DIR", "logs"),
    "ENGINE_DIR": ("PERSONA_ENGINE_DIR", "engine"),
}


def test_all_eight_constants_are_absolute_under_every_override_shape(
    monkeypatch, tmp_path
):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    shapes = {
        "unset": lambda name: None,
        "absolute": lambda name: str(tmp_path / f"abs_{name}"),
        "relative": lambda name: f"rel_{name}",
    }
    for shape, spell in shapes.items():
        overrides = {
            env: spell(name)
            for name, (env, _) in ALL_UNDER_HOME.items()
            if spell(name) is not None
        }
        cfg = _reload_config(
            monkeypatch, PERSONA_HOME=str(tmp_path / "home"), **overrides
        )
        for const in ALL_UNDER_HOME:
            value = getattr(cfg, const)
            assert os.path.isabs(value), (
                f"{const} not absolute under {shape} override: {value!r}"
            )


def test_all_eight_constants_default_layout_is_unchanged(monkeypatch, tmp_path):
    """"Normalisation, not relocation", stated over the whole blast radius."""
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    cfg = _reload_config(monkeypatch, PERSONA_HOME=str(home))
    for const, (_, basename) in ALL_UNDER_HOME.items():
        assert getattr(cfg, const) == os.path.join(str(home), basename)


def test_all_eight_constants_are_cwd_invariant_under_every_shape(
    monkeypatch, tmp_path
):
    """The FOURTH shape — rooted-but-driveless — and the property that is
    actually true universally, rather than the one that cannot be.

    The sweep above asserts isabs over unset/absolute/relative. It deliberately
    omits '/custom/data', because on a Windows path flavour under Python 3.13
    that value is returned verbatim (correctly — an absolute override is
    returned exactly as given) and ntpath.isabs reports False for it, so an
    isabs assertion over this shape would be asserting something the function
    does not promise and must not deliver. Adding it there would turn Windows
    CI red for the second time, on the same shape as the first.

    So this asserts the WEAKER, UNIVERSAL guarantee the docstring now states in
    its place: the value does not move when the process's cwd moves. That holds
    on every platform for every shape, including the rooted one, and it is the
    property a consumer actually needs in order to retire a local cwd join —
    which is what this ticket exists to let them do. It is the caveat made
    enforceable instead of merely promised.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    shapes = {
        "unset": lambda name: None,
        "absolute": lambda name: str(tmp_path / f"abs_{name}"),
        "relative": lambda name: f"rel_{name}",
        # hardcoded literal, NOT tmp_path-derived: a tmp_path string is
        # drive-absolute on Windows, which is exactly the blindness that let
        # the round-1 defect reach CI.
        "rooted": lambda name: f"/custom/{name.lower()}",
    }

    for shape, spell in shapes.items():
        monkeypatch.chdir(workdir)
        overrides = {
            env: spell(name)
            for name, (env, _) in ALL_UNDER_HOME.items()
            if spell(name) is not None
        }
        cfg = _reload_config(
            monkeypatch, PERSONA_HOME=str(tmp_path / "home"), **overrides
        )
        # NOTE: compare the RESOLVED location, not the joined string. A plain
        # os.path.join of a RELATIVE constant returns the same string before
        # and after a chdir ('rel_data_dir/acme' both times) while naming a
        # different directory each time — so a string comparison here passes
        # against the very defect this ticket fixes. abspath is what makes the
        # assertion bite: it is the operation every real consumer's filesystem
        # call performs implicitly.
        before = {
            c: os.path.abspath(os.path.join(getattr(cfg, c), "acme"))
            for c in ALL_UNDER_HOME
        }

        # the cwd moves out from under the already-imported module
        monkeypatch.chdir(elsewhere)
        after = {
            c: os.path.abspath(os.path.join(getattr(cfg, c), "acme"))
            for c in ALL_UNDER_HOME
        }

        for const in ALL_UNDER_HOME:
            assert before[const] == after[const], (
                f"{const} moved when cwd moved under {shape} override: "
                f"{before[const]!r} -> {after[const]!r}"
            )


def test_rooted_driveless_override_is_returned_verbatim_for_all_eight(
    monkeypatch, tmp_path
):
    """The other half of the rooted shape: it is returned EXACTLY as given.

    Pairs with the cwd-invariance test above. Together they pin the whole of
    what the docstring's caveat claims for this shape — verbatim, and stable —
    without asserting the absoluteness that is false for it on Windows.
    """
    overrides = {
        env: f"/custom/{name.lower()}" for name, (env, _) in ALL_UNDER_HOME.items()
    }
    cfg = _reload_config(
        monkeypatch, PERSONA_HOME=str(tmp_path / "home"), **overrides
    )
    for name, (env, _) in ALL_UNDER_HOME.items():
        assert getattr(cfg, name) == f"/custom/{name.lower()}", (
            f"{name} was rewritten; a rooted override must come back verbatim"
        )


# --- The CALL-TIME arm of the sweep -----------------------------------------
#
# ALL_UNDER_HOME above covers the eight module-level constants. _under_home has
# ELEVEN call sites, and the other three do not resolve at import at all — they
# call it per use. They are the sites where this ticket's change alters
# behaviour AND where cwd-invariance does NOT hold, so they are the most
# interesting rows in the sweep and were missing from it.
CALL_TIME_SITES = {
    # call-site label -> the env var that overrides it. The matching resolver
    # callables live in _call_time_resolvers() below (lazily imported).
    "core/settings.py:68": "PERSONA_SETTINGS_FILE",
    "core/single_instance.py:47": "PERSONA_LOCK_FILE",
    "api/mcp_token.py:16": "PERSONA_MCP_TOKEN_FILE",
}


def _call_time_resolvers():
    """The three per-use call sites, as callables. Imported lazily so this
    module's other tests don't pay for the api/ import graph."""
    from src.api import mcp_token
    from src.core import settings, single_instance

    return {
        "core/settings.py:68": settings._path,
        "core/single_instance.py:47": single_instance._lock_path,
        "api/mcp_token.py:16": mcp_token._path,
    }


def test_call_time_sites_are_absolute_on_every_call(monkeypatch, tmp_path):
    """What IS true for the three call-time consumers: every call returns an
    absolute path, under every override shape.

    This is the honest universal the docstring now states — 'every return is
    absolute' — as distinct from the stronger cwd-invariance that only the
    eight import-bound constants get. Asserted per call, because that is the
    granularity at which these sites actually consume the value.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    shapes = {
        "unset": lambda label: None,
        "absolute": lambda label: str(tmp_path / "abs_file"),
        "relative": lambda label: "rel_file",
    }
    for shape, spell in shapes.items():
        monkeypatch.setenv("PERSONA_HOME", str(tmp_path / "home"))
        for label, env in CALL_TIME_SITES.items():
            value = spell(label)
            if value is None:
                monkeypatch.delenv(env, raising=False)
            else:
                monkeypatch.setenv(env, value)
        for label, resolve in _call_time_resolvers().items():
            got = resolve()
            assert os.path.isabs(got), (
                f"{label} not absolute under {shape} override: {got!r}"
            )


def test_call_time_sites_track_the_cwd_unlike_the_eight_constants(
    monkeypatch, tmp_path
):
    """What is NOT true for them, pinned so the docstring cannot drift back.

    Under a RELATIVE override these three re-read getcwd() on every call, so
    they are absolute each time but resolve to a DIFFERENT absolute path once
    the process chdirs. The eight constants, bound once at import, do not move.
    That asymmetry is the whole of what the round-3 docstring got wrong by
    claiming invariance universally, so it is asserted rather than described:
    if someone 'fixes' the docstring back to a universal invariance claim, or
    freezes these sites at import, this test says so.

    single_instance._lock_path's docstring ("RESOLVED AT CALL TIME, NOT AT
    IMPORT") documents the call-time resolution deliberately — freezing it
    would break the override contract for anything that sets the env var
    after that module loads, the test fixtures included.
    """
    a = tmp_path / "cwd_a"
    a.mkdir()
    b = tmp_path / "cwd_b"
    b.mkdir()

    monkeypatch.setenv("PERSONA_HOME", str(tmp_path / "home"))
    for env in CALL_TIME_SITES.values():
        monkeypatch.setenv(env, "rel_file")

    resolvers = _call_time_resolvers()

    monkeypatch.chdir(a)
    before = {label: resolve() for label, resolve in resolvers.items()}
    monkeypatch.chdir(b)
    after = {label: resolve() for label, resolve in resolvers.items()}

    for label in resolvers:
        # absolute on BOTH calls ...
        assert os.path.isabs(before[label]) and os.path.isabs(after[label]), (
            f"{label} returned a non-absolute path: "
            f"{before[label]!r} / {after[label]!r}"
        )
        # ... but a DIFFERENT absolute path, because it re-resolves per call.
        assert before[label] != after[label], (
            f"{label} did NOT track the cwd. If this site was deliberately "
            f"changed to bind at import, update _under_home's docstring: it "
            f"names this site as call-time resolved. Got {before[label]!r} "
            f"both times."
        )
        assert before[label] == os.path.join(str(a), "rel_file")
        assert after[label] == os.path.join(str(b), "rel_file")


def test_the_eight_constants_do_not_track_the_cwd(monkeypatch, tmp_path):
    """The control for the test above: same relative shape, same chdir, and
    the eight import-bound constants stay put. Without this, the asymmetry
    isn't demonstrated — only half of it is.
    """
    a = tmp_path / "cwd_a"
    a.mkdir()
    b = tmp_path / "cwd_b"
    b.mkdir()

    monkeypatch.chdir(a)
    overrides = {env: f"rel_{name}" for name, (env, _) in ALL_UNDER_HOME.items()}
    cfg = _reload_config(
        monkeypatch, PERSONA_HOME=str(tmp_path / "home"), **overrides
    )
    before = {c: getattr(cfg, c) for c in ALL_UNDER_HOME}

    monkeypatch.chdir(b)
    after = {c: getattr(cfg, c) for c in ALL_UNDER_HOME}

    for const in ALL_UNDER_HOME:
        assert before[const] == after[const], (
            f"{const} moved when cwd moved; the eight constants are bound "
            f"once at import and must not track the cwd"
        )
        assert before[const] == os.path.join(str(a), f"rel_{const}")
