"""#405-C: the engine-autobump script detects a newer patched-Firefox engine
(via invisible_playwright's latest release + the invisible_core it pins) and
rewrites the pins so a release rebuilds engine+driver in lockstep. plan() is pure
with an injected fetch; apply() edits the pin files under a tmp tree."""
import json
import re

import pytest

import importlib.util
import pathlib
import sys

_SPEC = importlib.util.spec_from_file_location(
    "engine_autobump",
    str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "engine_autobump.py"),
)
ab = importlib.util.module_from_spec(_SPEC)
# Register before exec so @dataclass can resolve the module in sys.modules.
sys.modules["engine_autobump"] = ab
_SPEC.loader.exec_module(ab)


def _fetch_factory(releases, core_by_tag, tags=None):
    def fetch(url):
        if url.endswith("/releases?per_page=10") or "releases?" in url:
            return json.dumps(releases)
        if "tags?" in url:
            return json.dumps(tags or [])
        if "/pyproject.toml" in url:
            # url .../invisible_playwright/<tag>/pyproject.toml
            m = re.search(r"invisible_playwright/([^/]+)/pyproject.toml", url)
            tag = m.group(1)
            core = core_by_tag.get(tag, "")
            return f'dependencies = [\n    "invisible_core=={core}",\n]\n' if core else "x"
        raise RuntimeError("unexpected url " + url)
    return fetch


PYPROJECT_CORE18 = (
    'dependencies = [\n'
    '    "invisible_playwright @ git+https://github.com/feder-cr/'
    'invisible_playwright.git@e2deaa50594894b236d39ac763d244aa1327e9c5",\n'
    '    "invisible_core==18.13.0",\n'
    ']\n'
)


def test_core_major():
    assert ab.core_major("19.14.0") == 19
    assert ab.core_major("18.13.0") == 18
    assert ab.core_major("bad") == -1


def test_plan_detects_newer_engine():
    releases = [{"tag_name": "v0.7.0", "target_commitish": "main"}]
    fetch = _fetch_factory(releases, {"v0.7.0": "19.14.0"})
    bp = ab.plan(PYPROJECT_CORE18, "2.9.14", fetch=fetch)
    assert bp.needed is True
    assert bp.latest_tag == "v0.7.0"
    assert bp.latest_core == "19.14.0"
    assert bp.new_baseline == "firefox-19"
    assert bp.new_app_version == "2.9.15"
    assert "firefox-19" in bp.reason


def test_plan_no_bump_when_same_major():
    # latest playwright still pins core major 18 → nothing to do
    releases = [{"tag_name": "v0.6.1", "target_commitish": "main"}]
    fetch = _fetch_factory(releases, {"v0.6.1": "18.14.0"})
    bp = ab.plan(PYPROJECT_CORE18, "2.9.14", fetch=fetch)
    assert bp.needed is False
    assert "already on core major 18" in bp.reason


def test_plan_no_bump_when_older_major():
    releases = [{"tag_name": "v0.5.0", "target_commitish": "main"}]
    fetch = _fetch_factory(releases, {"v0.5.0": "17.0.0"})
    bp = ab.plan(PYPROJECT_CORE18, "2.9.14", fetch=fetch)
    assert bp.needed is False


def test_plan_skips_prereleases():
    releases = [
        {"tag_name": "v0.8.0", "prerelease": True, "target_commitish": "main"},
        {"tag_name": "v0.7.0", "target_commitish": "main"},
    ]
    fetch = _fetch_factory(releases, {"v0.7.0": "19.14.0"})
    bp = ab.plan(PYPROJECT_CORE18, "2.9.14", fetch=fetch)
    assert bp.latest_tag == "v0.7.0"  # the prerelease v0.8.0 was skipped


def test_apply_rewrites_all_pins(tmp_path):
    # lay down a minimal tree
    (tmp_path / "pyproject.toml").write_text(PYPROJECT_CORE18, encoding="utf-8")
    (tmp_path / "engine-baseline.txt").write_text("firefox-18\n", encoding="utf-8")
    up = tmp_path / "src" / "services" / "app_update"
    up.mkdir(parents=True)
    (up / "updater.py").write_text('APP_VERSION = "2.9.14"\n', encoding="utf-8")
    ui = tmp_path / "src" / "ui"
    ui.mkdir(parents=True)
    (ui / "changelog.py").write_text(
        "CHANGELOG: dict[str, list[str]] = {\n"
        '    "2.9.14": ["old"],\n}\n',
        encoding="utf-8",
    )

    sha = "a" * 40
    releases = [{"tag_name": "v0.7.0", "target_commitish": sha}]
    tags = [{"name": "v0.7.0", "commit": {"sha": sha}}]
    fetch = _fetch_factory(releases, {"v0.7.0": "19.14.0"}, tags=tags)

    bp = ab.plan(PYPROJECT_CORE18, "2.9.14", fetch=fetch)
    ab.apply(str(tmp_path), bp, fetch=fetch)

    pp = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert f"invisible_playwright.git@{sha}" in pp
    assert "invisible_core==19.14.0" in pp
    assert (tmp_path / "engine-baseline.txt").read_text(encoding="utf-8").strip() == "firefox-19"
    assert 'APP_VERSION = "2.9.15"' in (up / "updater.py").read_text(encoding="utf-8")
    ch = (ui / "changelog.py").read_text(encoding="utf-8")
    assert '"2.9.15":' in ch
    assert ch.index('"2.9.15"') < ch.index('"2.9.14"')  # newest first


def test_apply_resolves_sha_from_tags_when_target_is_branch(tmp_path):
    # target_commitish is a branch name; apply must resolve the real sha via tags.
    (tmp_path / "pyproject.toml").write_text(PYPROJECT_CORE18, encoding="utf-8")
    (tmp_path / "engine-baseline.txt").write_text("firefox-18\n", encoding="utf-8")
    up = tmp_path / "src" / "services" / "app_update"
    up.mkdir(parents=True)
    (up / "updater.py").write_text('APP_VERSION = "2.9.14"\n', encoding="utf-8")
    ui = tmp_path / "src" / "ui"
    ui.mkdir(parents=True)
    (ui / "changelog.py").write_text(
        'CHANGELOG: dict[str, list[str]] = {\n    "2.9.14": ["old"],\n}\n',
        encoding="utf-8",
    )
    real_sha = "b" * 40
    releases = [{"tag_name": "v0.7.0", "target_commitish": "main"}]  # branch, not sha
    tags = [{"name": "v0.7.0", "commit": {"sha": real_sha}}]
    fetch = _fetch_factory(releases, {"v0.7.0": "19.14.0"}, tags=tags)

    bp = ab.plan(PYPROJECT_CORE18, "2.9.14", fetch=fetch)
    ab.apply(str(tmp_path), bp, fetch=fetch)
    assert f"invisible_playwright.git@{real_sha}" in (
        tmp_path / "pyproject.toml"
    ).read_text(encoding="utf-8")
