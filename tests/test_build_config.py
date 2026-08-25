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
    """The declared requirement for `name` from pyproject's dependency list.

    Asserts there is exactly ONE declaration, so a dependency that has been
    split across environment markers cannot be read through this helper — it
    would silently return whichever line happens to come first and hide the
    other. Use `_dep_specs` / `_dep_spec_for` for a marker-split dependency.
    """
    matches = _dep_specs(name)
    if not matches:
        raise AssertionError(f"{name} is not declared in pyproject dependencies")
    assert len(matches) == 1, (
        f"{name} has {len(matches)} declarations ({matches}) — it is split "
        "across environment markers, so a single spec is ambiguous; assert "
        "per-platform with _dep_spec_for instead"
    )
    return matches[0]


def _dep_specs(name: str) -> list[str]:
    """Every declared requirement for `name` (a marker split yields several)."""
    return [
        dep
        for dep in _pyproject()["project"]["dependencies"]
        if dep.lower().startswith(name.lower())
    ]


def _dep_spec_for(name: str, sys_platform: str) -> str:
    """The requirement for `name` that APPLIES on `sys_platform`.

    Evaluates each declaration's PEP 508 marker the way pip does, so these
    tests assert what a given platform actually resolves rather than what the
    first matching line happens to say.
    """
    from packaging.requirements import Requirement

    env = {"sys_platform": sys_platform}
    applicable = [
        dep
        for dep in _dep_specs(name)
        if (m := Requirement(dep).marker) is None or m.evaluate(env)
    ]
    assert len(applicable) == 1, (
        f"expected exactly one {name} requirement to apply on {sys_platform}, "
        f"got {applicable} — overlapping or gapped markers mean the resolved "
        "version depends on declaration order"
    )
    return applicable[0]


def test_cryptography_is_capped_below_the_arm64_only_releases():
    """cryptography must not be allowed to resolve >=49 ON macOS.

    49.0.0 is the release that dropped the `universal2` wheel. At or above it
    the x86_64 pass cannot resolve the same version as the arm64 pass, and the
    merged bundle is corrupt. This asserts the cap EXISTS and is at 49 — not
    merely that some upper bound is present, since a cap set too high (say
    <51) would still admit the broken pair.

    Asserted against the DARWIN requirement specifically: the cap is scoped by
    marker, because the two-pass merge that makes >=49 dangerous happens only
    on macOS. See the companion test below for the other half of that scope.
    """
    from packaging.requirements import Requirement
    from packaging.version import Version

    spec = Requirement(_dep_spec_for("cryptography", "darwin")).specifier
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


def test_the_macos_cap_does_not_reach_windows_or_linux():
    """The cap must NOT downgrade the platforms that were never broken.

    This is the guard that was missing when an unconditional `<49` shipped: the
    whole suite passed while the bound silently moved Windows and Linux from
    cryptography 50.0.0 back to 48.0.1 — two major versions of a SECURITY
    library, on the one platform (Linux) that had just passed 11/11 imports.

    Measured with pip's own resolver against the targets we ship:
        >=42.0.0       win_amd64 -> 50.0.0    manylinux -> 50.0.0
        >=42.0.0,<49   win_amd64 -> 48.0.1    manylinux -> 48.0.1
    so an unscoped cap is NOT free for them, whatever a comment claims.

    flet resolves macOS in two arch passes and merges; Windows and Linux each
    resolve ONCE and have no merge step, so they cannot hit the collision and
    must keep tracking latest.
    """
    from packaging.requirements import Requirement
    from packaging.version import Version

    for platform in ("win32", "linux"):
        spec = Requirement(_dep_spec_for("cryptography", platform)).specifier
        assert Version("50.0.0") in spec, (
            f"the macOS-only cryptography cap has leaked onto {platform}: "
            "50.0.0 is no longer admitted, so this platform is being dragged "
            "back two major versions to fix a macOS-only packaging defect. "
            "Scope the cap with a `sys_platform == 'darwin'` marker."
        )
        assert Version("49.0.0") in spec, (
            f"{platform} must still admit 49.0.0 — it has no two-pass merge "
            "and the universal2 drop is irrelevant to it"
        )


def test_cryptography_bound_is_a_range_not_a_frozen_pin():
    """The macOS cap must exclude a MAJOR line, not freeze one build.

    A `==` pin would stop even a 48.0.2 from ever landing. The bound exists to
    exclude the arm64-only major line, so within the good line it must stay
    open.

    HONEST LIMIT — this is weaker than it looks, and the docstring says so
    rather than overclaiming. Upstream ships FORWARD rather than backporting:
    48.0.0 -> 48.0.1 -> 49.0.0 -> 50.0.0, with 49 landing days after 48.0.1.
    So admitting 48.0.x is not the same as a live patch stream, and if a CVE is
    fixed only in 50.x this range does NOT deliver it to macOS. What actually
    keeps this from being a standing security hole is the OTHER half of the
    marker split — Windows and Linux stay uncapped (asserted directly in
    test_the_macos_cap_does_not_reach_windows_or_linux) — plus the documented
    exit condition in pyproject. Read this test as "the cap is no wider than it
    has to be", not as "macOS keeps receiving security patches".
    """
    from packaging.requirements import Requirement
    from packaging.version import Version

    spec = Requirement(_dep_spec_for("cryptography", "darwin")).specifier
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

    Compared per-PLATFORM rather than as raw strings, because the bound is
    split across environment markers: what has to match is the requirement each
    platform actually resolves, not the quoting or ordering of the two lines.
    A raw string comparison would break on `'darwin'` vs `"darwin"` while
    passing on a genuine drift that swapped the two markers' specifiers.
    """
    from packaging.requirements import Requirement

    req_lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    req_specs = [l for l in req_lines if l.lower().startswith("cryptography")]
    assert req_specs, "cryptography is not declared in requirements.txt"

    for platform in ("darwin", "win32", "linux"):
        env = {"sys_platform": platform}
        applicable = [
            s
            for s in req_specs
            if (m := Requirement(s).marker) is None or m.evaluate(env)
        ]
        assert len(applicable) == 1, (
            f"expected exactly one cryptography line in requirements.txt to "
            f"apply on {platform}, got {applicable}"
        )
        req = Requirement(applicable[0])
        proj = Requirement(_dep_spec_for("cryptography", platform))
        assert req.specifier == proj.specifier, (
            f"on {platform} requirements.txt resolves {str(req.specifier)!r} "
            f"but pyproject resolves {str(proj.specifier)!r} — the runner "
            "would validate one version and the bundle would ship another"
        )


def test_the_macos_bound_records_why_it_exists():
    """A bare `<49` invites a future maintainer to 'tidy' it away.

    The mechanism (two arch passes, dropped universal2 wheel, abi3 filename
    collision) is not guessable from the version number alone, so the comment
    carrying it is load-bearing. This asserts the explanation stays next to the
    constraint it explains.

    `darwin` is in the required token list because the marker SCOPE is part of
    the mechanism, not decoration: an unconditional cap downgrades Windows and
    Linux two major versions (that shipped once — see the companion test). A
    comment that explains the collision but not why it is macOS-only invites
    exactly that simplification back in.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # Anchor on the DARWIN declaration — the capped one the comment explains.
    anchor = "\"cryptography>=42.0.0,<49; sys_platform == 'darwin'\""
    assert anchor in text, (
        "the darwin-scoped cryptography declaration is gone or reformatted; if "
        "the cap was made unconditional again, read "
        "test_the_macos_cap_does_not_reach_windows_or_linux before proceeding"
    )
    head = text.split(anchor)[0]
    for token in ("universal2", "x86_64", "arm64", "abi3", "darwin"):
        assert token in head, (
            f"the cryptography bound no longer explains {token!r} — without the "
            "mechanism this cap reads as an arbitrary pin and will be lifted"
        )
