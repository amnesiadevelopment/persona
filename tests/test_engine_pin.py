"""Both invisible engine packages MUST be declared in pyproject, and
invisible_core MUST carry an exact `==` version pin.

flet build bundles deps from pyproject and does NOT resolve a git package's
transitive deps — so invisible_core (invisible_playwright's own dependency)
has to be declared here explicitly or the bundle ships without it and the
engine raises ImportError ("invisible-core is missing") / reads as "not ready"
forever (#234). It must be pinned to the EXACT version invisible_playwright
requires (its dependency list carries invisible_core==NN), so the version that
carries the release-pin check (invisible_core._pin) lands in the build. A git
pin drifted out of lockstep with playwright's `==` pin; the exact `==` pin
keeps them matched and lets `pip check` catch a drift before release.
"""
import pathlib
import re
import tomllib


def _deps():
    root = pathlib.Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def _line_for(pkg):
    return next((d for d in _deps() if re.match(rf"{pkg}\b", d)), None)


def test_both_invisible_packages_declared():
    for pkg in ("invisible_playwright", "invisible_core"):
        assert _line_for(pkg) is not None, (
            f"{pkg} must be declared in pyproject dependencies so flet bundles "
            f"it; a transitive-only {pkg} is not resolved into a git package's "
            "build and goes missing from the bundled app (#234)"
        )


def test_invisible_core_pinned_to_exact_version():
    line = _line_for("invisible_core")
    assert "==" in line and "git+" not in line, (
        f"invisible_core must be pinned to an exact PyPI version (found: {line!r}); "
        "it has to match the version invisible_playwright requires so the core "
        "carrying invisible_core._pin lands in the build (#234)"
    )
    m = re.search(r"invisible_core==([\d.]+)", line)
    assert m, f"could not parse invisible_core version from {line!r}"
