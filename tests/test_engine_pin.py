"""Both invisible engine packages MUST be pinned to the SAME git source in
pyproject. flet build bundles deps from pyproject; when invisible_core was only
a transitive dep, flet could resolve it to an OLD PyPI build lacking the
release-pin check (invisible_core._pin) that the newer invisible_playwright
imports at startup. That mismatch made the bundled Firefox engine raise
ImportError / read as "not ready yet" and re-download forever on the boevaya
2.6.3 build (#234). Guard both are declared, from git, so they stay in lockstep.
"""
import pathlib
import tomllib


def _deps():
    root = pathlib.Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def test_both_invisible_packages_pinned_from_git():
    deps = _deps()
    joined = "\n".join(deps)
    for pkg in ("invisible_playwright", "invisible_core"):
        line = next((d for d in deps if d.startswith(pkg)), None)
        assert line is not None, (
            f"{pkg} must be declared in pyproject dependencies so flet bundles "
            f"it; a transitive-only {pkg} can resolve to an incompatible version"
        )
        assert "git+" in line and "github.com/feder-cr" in line, (
            f"{pkg} must be pinned to the feder-cr git source (found: {line!r}); "
            "a PyPI/transitive resolution can drift out of lockstep with the "
            "other package and break the bundled engine (#234)"
        )
    assert "invisible_playwright" in joined and "invisible_core" in joined
