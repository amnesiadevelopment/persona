"""The flet build configuration in pyproject.toml.

The window starts HIDDEN (hide_window_on_start) so the user's first frame is
persona's own centred fingerprint splash — never the client's off-centre corner
spinner nor the jump to centre. The old ban (visible-start) feared a hidden
window that a crashed Python session never shows becoming an invisible zombie;
that's now prevented by revealing the window from Python's first frame AND
force-revealing it on any startup error (see App._main / _finish_startup), plus
scrubbing FLET_HIDE_WINDOW_ON_START out of any relaunch env so it can't leak.
These tests pin that design: hidden start, native screens off (they'd never be
seen behind the hidden window), and NO `window.visible = False` anywhere (only
the reveal to True is allowed).
"""

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_built_app_starts_visible():
    # hide_window_on_start MUST be false: Flet 0.85.3's macOS merged UI/platform
    # thread deadlocks the native launch when the window starts hidden (Python
    # main() never runs -> invisible zombie). Verified on a built .app.
    app = _pyproject()["tool"]["flet"]["app"]
    assert app["hide_window_on_start"] is False


def test_native_boot_and_startup_screens_are_off():
    # With the window hidden until Python's first frame, the client's boot/startup
    # spinners would never be visible anyway — keep them off so nothing but
    # persona's own centred splash ever paints.
    app = _pyproject()["tool"]["flet"]["app"]
    assert app["boot_screen"]["show"] is False
    assert app["startup_screen"]["show"] is False


def test_app_code_never_hides_the_window():
    # Revealing the window (`window.visible = True`) is required; HIDING it
    # (`= False`) anywhere would recreate the invisible-zombie failure — a live
    # window nothing is guaranteed to bring back. Only the True reveal is allowed.
    for py in (ROOT / "src").rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "window.visible = False" not in src, f"window hidden in {py}"


def test_relaunch_env_scrubs_every_client_env_gate(monkeypatch):
    # every var the build template applies with putIfAbsent (an inherited
    # value silently beats the fresh one) plus the client's hide gate, which
    # hides the window when merely PRESENT — none may survive into anything
    # that outlives this persona and starts the next one
    from src.services.app_update import updater as au

    gates = (
        "FLET_PLATFORM",
        "FLET_SERVER_PORT",
        "FLET_SERVER_UDS_PATH",
        "FLET_PYTHON_CALLBACK_SOCKET_ADDR",
        "FLET_APP_CONSOLE",
        "FLET_APP_STORAGE_DATA",
        "FLET_APP_STORAGE_TEMP",
        "FLET_ASSETS_DIR",
        "FLET_HIDE_WINDOW_ON_START",
        "PYTHONPATH",
        "PYTHONHOME",
    )
    for var in gates:
        assert var in au._RUNTIME_ENV_VARS, var
        monkeypatch.setenv(var, "stale")
    env = au._relaunch_env()
    for var in gates:
        assert var not in env, f"{var} leaked into the relaunch environment"


# ---------------------------------------------------------------------------
# The macOS two-architecture dependency trap (PS-162).
#
# flet/serious_python does NOT resolve macOS dependencies once. It runs pip
# TWICE — once for arm64, once for x86_64 — into two separate trees, then
# merges them file-by-file (serious_python's macos_utils.mergeMacOsSitePackages).
# Any dependency whose macOS wheel coverage differs between those two
# architectures therefore resolves to DIFFERENT VERSIONS in the two passes, and
# the merge silently collides them whenever the compiled extension's filename
# does not carry the version — which, for a stable-ABI (`abi3`) extension, it
# never does.
#
# That is not hypothetical. It shipped: cryptography 49.0.0 dropped the macOS
# `universal2` wheel and went arm64-only, so `cryptography>=42.0.0` resolved
# 50.0.0 for arm64 and 48.0.1 for x86_64. The merge overwrote `_rust.abi3.so`
# while the other version's Python files and .dist-info survived, producing one
# bundle that says 50.0.0 in metadata and 48.0.1 in its shared object.
# cryptography checks precisely that at import time and refuses:
#   "The version of cryptography does not match the loaded shared object."
# The 3.0.0 release died there, after a green build, at the smoke check.
#
# These tests pin the BOUND and the REASON. They are deliberately about the
# declaration rather than about a built bundle, because that is the only part
# reachable without a macOS runner — see the docstrings for what each does and
# does not prove.
# ---------------------------------------------------------------------------


def _dep_spec(name: str) -> str:
    """The declared requirement for `name` from pyproject's dependency list."""
    for dep in _pyproject()["project"]["dependencies"]:
        if dep.lower().startswith(name.lower()):
            return dep
    raise AssertionError(f"{name} is not declared in pyproject dependencies")


def test_cryptography_is_capped_below_the_arm64_only_releases():
    """cryptography must not be allowed to resolve >=49 on macOS.

    49.0.0 is the release that dropped the `universal2` wheel. At or above it
    the x86_64 pass cannot resolve the same version as the arm64 pass, and the
    merged bundle is corrupt. This asserts the cap EXISTS and is at 49 — not
    merely that some upper bound is present, since a cap set too high (say
    <51) would still admit the broken pair.
    """
    from packaging.requirements import Requirement
    from packaging.version import Version

    spec = Requirement(_dep_spec("cryptography")).specifier
    # The two versions that actually collided in the failed 3.0.0 build.
    assert Version("50.0.0") not in spec, (
        "cryptography 50.0.0 is arm64-only on macOS; admitting it lets the "
        "arm64 and x86_64 resolution passes disagree and corrupts the bundle"
    )
    assert Version("49.0.0") not in spec, (
        "49.0.0 is the release that dropped the macOS universal2 wheel"
    )
    # ...and the last good one must still be reachable, so this is a bound and
    # not an accidental exclusion of everything.
    assert Version("48.0.1") in spec, (
        "48.0.1 is the last macOS universal2 release and MUST stay installable"
    )


def test_cryptography_bound_is_a_range_not_a_frozen_pin():
    """The cap must not freeze the patch stream.

    A `==` pin would stop 48.0.x security patches from ever landing, which for
    a cryptography library is a worse failure than the packaging bug it would
    be fixing. The bound exists to exclude a macOS-incompatible MAJOR line, so
    it must still admit patch releases within the good line.
    """
    from packaging.requirements import Requirement
    from packaging.version import Version

    spec = Requirement(_dep_spec("cryptography")).specifier
    assert Version("48.0.0") in spec and Version("48.0.1") in spec, (
        "the bound must admit the whole 48.0.x patch stream, not one pinned build"
    )
    assert "==" not in str(spec), f"expected a range, got a pin: {spec}"


def test_the_two_dependency_declarations_agree_on_cryptography():
    """pyproject and requirements.txt must not disagree.

    They feed DIFFERENT resolutions: release.yml installs requirements.txt onto
    the runner, while `flet build` resolves the bundle from pyproject. If the
    two specs drift, the runner validates one version and the bundle ships
    another — and every guard in the release workflow runs against the wrong
    one. Keeping them in lockstep is what makes the runner-side checks mean
    anything about the artifact.
    """
    req_lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    req_spec = next(l for l in req_lines if l.lower().startswith("cryptography"))
    assert req_spec == _dep_spec("cryptography"), (
        f"requirements.txt says {req_spec!r} but pyproject says "
        f"{_dep_spec('cryptography')!r} — the runner and the bundle would "
        "resolve different versions"
    )


def test_the_macos_bound_records_why_it_exists():
    """A bare `<49` invites a future maintainer to 'tidy' it away.

    The mechanism (two arch passes, dropped universal2 wheel, abi3 filename
    collision) is not guessable from the version number alone, so the comment
    carrying it is load-bearing. This asserts the explanation stays next to the
    constraint it explains.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    head = text.split('"cryptography>=42.0.0,<49"')[0]
    for token in ("universal2", "x86_64", "arm64", "abi3"):
        assert token in head, (
            f"the cryptography bound no longer explains {token!r} — without the "
            "mechanism this cap reads as an arbitrary pin and will be lifted"
        )
