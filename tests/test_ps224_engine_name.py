"""PS-224: our engine is named ``Personium`` TO THE OPERATOR, and that name
must never reach anything a web page can read.

THE TWO HALVES, AND WHY THE SECOND IS THE ONE THAT MATTERS. The rename itself
is small — two strings in the sidebar panel plus the profile-creation dropdown.
The constraint is what makes it safe: to a site the browser must present as
Chrome and nothing else, so a product-specific string appearing in a user
agent, a brand list, or any JS-reachable surface is a UNIQUE MARKER identifying
every one of our users. A change that satisfies the first half while breaking
the second is a failure regardless of how the panel looks.

WHAT A UNIT TEST CAN AND CANNOT ESTABLISH, stated plainly because PS-17's
closing rule says a green pipeline does not close this ticket. These tests pin
the SOURCE-LEVEL invariants — the name is absent from the launch argv, the
launch modules cannot even import the module that defines it, the stored key is
unchanged. They do NOT establish what a running site observes; that is a LIVE
reading, and it is recorded on the ticket rather than asserted here.

WHAT IS DELIBERATELY *NOT* RENAMED, each pinned below because each is a trap
the ticket names explicitly:

* ``fpchrome.AppImage`` — the UPSTREAM artifact's filename, resolved by the
  platform layer to find and launch the downloaded binary. Renaming it breaks
  the download and the launch.
* ``fingerprint-chromium/<version>`` — the verification tooling's recorded
  engine identifier. 38 committed reading sets carry it, and
  ``pool_depth.engine_report`` finds an arm by substring-matching "chromium"
  against it.
* the stored engine key ``"chromium"`` — a display rename must not become a
  data migration.
"""

import os
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import pytest  # noqa: E402

from src.core.strings import CHROMIUM_ENGINE_NAME  # noqa: E402


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "src")


# ---------------------------------------------------------------------------
# Half 1 — the operator sees OUR name
# ---------------------------------------------------------------------------


def test_the_name_is_personium():
    assert CHROMIUM_ENGINE_NAME == "Personium"


def test_engines_panel_row_and_tooltip_carry_our_name_not_the_upstream_project():
    """The two user-visible strings the ticket scopes: the row label and its
    tooltip.

    Asserted on the BUILT control tree rather than by grepping the source, so a
    string that exists in the module but is not what the panel renders cannot
    pass. Both directions are checked: our name is present AND the third
    party's project name is gone — a rename that merely appended would satisfy
    the first alone.

    The panel is built with its section OPEN, because the engine rows are only
    constructed in that branch: asserting against a closed panel would find no
    rows at all and pass vacuously. The row census below is what pins that.
    """
    import src.ui.app as app_mod
    from src.core.container import Container

    app = app_mod.App(Container())
    app._engines_open = True
    panel = app._build_engines_panel()

    texts = _all_text(panel)
    tooltips = _all_tooltips(panel)

    assert CHROMIUM_ENGINE_NAME in texts, (
        f"the engines panel does not render {CHROMIUM_ENGINE_NAME!r} anywhere. "
        f"Rendered text: {sorted(texts)}"
    )
    assert f"Check / update {CHROMIUM_ENGINE_NAME}" in tooltips, (
        f"the chromium row's tooltip must name OUR engine. "
        f"Rendered tooltips: {sorted(tooltips)}"
    )

    # NOT VACUOUS: the firefox row is rendered by the same builder, so its
    # presence proves the open-panel branch really ran and the assertions above
    # were made against a populated tree.
    assert "firefox" in texts, (
        "the engines panel rendered no firefox row — the panel was not open, "
        "so the assertions above proved nothing"
    )

    leaked = [t for t in texts | tooltips if "fp-chromium" in t or "fingerprint-chromium" in t]
    assert not leaked, (
        f"the engines panel still shows a third party's project name: {leaked}"
    )


def test_profile_creation_dropdown_carries_our_name():
    """The THIRD operator-visible occurrence, outside the sidebar panel.

    The ticket's instruction is to sweep wherever the operator can see the
    name, not only the panel — and this is the one place a rename scoped to
    "the engines panel" would miss.
    """
    from src.ui.theme.page import build_engine_dropdown

    opts = {o.key: o.text for o in build_engine_dropdown().options}
    assert opts["chromium"] == f'Chrome ("{CHROMIUM_ENGINE_NAME}")'
    assert "fingerprint-chromium" not in opts["chromium"]


def test_the_firefox_sibling_is_left_alone():
    """Scope fence. The Firefox option names a DIFFERENT third-party project
    and PS-224 renames our Chromium engine only. Pinned so a later sweep for
    "third-party names in the UI" does not quietly widen this ticket.
    """
    from src.ui.theme.page import build_engine_dropdown

    opts = {o.key: o.text for o in build_engine_dropdown().options}
    assert opts["firefox"] == 'Firefox ("invisible_playwright")'


# ---------------------------------------------------------------------------
# Half 2 — the name never reaches a page (the non-waivable part)
# ---------------------------------------------------------------------------


def test_the_name_is_absent_from_the_real_chromium_launch_argv():
    """THE CENTRAL ASSERTION, and it drives the REAL ``spawn_browser``.

    Not a re-implementation of the argv and not a grep: the actual product
    launch path is run with only the engine binary swapped for a script that
    records what it was called with, so the argv asserted on is the one a
    browser would really have received. A test that rebuilt the flag list
    itself would pass while the shipped path leaked.

    ``--fingerprint-brand=Chrome`` is asserted PRESENT in the same breath,
    because the constraint is not merely "our name is absent" — it is "the
    browser still presents as Chrome". Absence alone would also be satisfied by
    a launch that presented as nothing.
    """
    argv = _capture_launch_argv()

    joined = " ".join(argv)
    assert CHROMIUM_ENGINE_NAME.lower() not in joined.lower(), (
        f"{CHROMIUM_ENGINE_NAME!r} reached the browser command line: {joined!r}. "
        "Any product-specific string a page can observe is a unique marker "
        "identifying every one of our users."
    )
    assert "--fingerprint-brand=Chrome" in argv, (
        "the browser must still present as Chrome — the rename must not have "
        "disturbed the brand the engine reports to a page"
    )


def test_the_name_is_absent_from_every_extension_the_launch_injects():
    """The other page-reachable surface, and the one that is easy to forget.

    persona injects extensions into the launch (``--load-extension=``) whose
    JavaScript runs IN THE PAGE. A name that never touched the argv but landed
    in an injected script is just as observable, so the extension payloads are
    read off the real launch rather than assumed clean.
    """
    argv = _capture_launch_argv()
    ext_dirs: list[str] = []
    for arg in argv:
        if arg.startswith("--load-extension="):
            ext_dirs.extend(p for p in arg.split("=", 1)[1].split(",") if p)

    assert ext_dirs, "the chromium launch injects extensions; none were found"

    offenders = []
    for d in ext_dirs:
        for root, _dirs, files in os.walk(d):
            for fn in files:
                path = os.path.join(root, fn)
                try:
                    body = _read(path)
                except (OSError, UnicodeDecodeError):
                    continue
                if CHROMIUM_ENGINE_NAME.lower() in body.lower():
                    offenders.append(path)

    assert not offenders, (
        f"{CHROMIUM_ENGINE_NAME!r} appears in extension code that runs IN THE "
        f"PAGE: {offenders}"
    )


@pytest.mark.parametrize("package", ["services/browser", "services/engine"])
def test_the_launch_layer_cannot_even_import_the_module_that_defines_the_name(
    package,
):
    """A STRUCTURAL fence, not a spot check.

    The two assertions above test today's argv and today's extensions. This one
    makes the leak hard to reintroduce: the launch and updater layers do not
    import ``core.strings`` at all, so the name is not reachable from the code
    that builds a command line. A future edit that wants to put the name on the
    wire has to add an import first, and that is the line this test fails on.
    """
    offenders = []
    base = os.path.join(SRC, *package.split("/"))
    for root, _dirs, files in os.walk(base):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            body = _read(path)
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "core.strings" in stripped or "import strings" in stripped:
                    offenders.append(f"{path}: {stripped}")

    assert not offenders, (
        f"the {package} layer imports the module that defines "
        f"{CHROMIUM_ENGINE_NAME!r}; that layer builds command lines and page-"
        f"visible values and must not be able to reach it: {offenders}"
    )


# ---------------------------------------------------------------------------
# Half 3 — nothing on disk, on the wire, or in a record was renamed
# ---------------------------------------------------------------------------


def test_the_upstream_artifact_filename_is_unchanged():
    """``fpchrome.AppImage`` is the UPSTREAM build's filename, not ours.

    The platform layer resolves it to find the downloaded binary; renaming it
    while we are still downloading someone else's build breaks the download and
    the launch. It becomes ours only once we ship a binary we built.
    """
    from src.core import platform as _platform

    assert _platform.fingerprint_chromium_filename() == "fpchrome.AppImage"


def test_the_stored_engine_key_is_unchanged():
    """A display rename must not become a data migration.

    The persisted value is ``"chromium"``. If the display string were also the
    stored key, every existing installation's saved profiles would stop
    resolving on upgrade — so the dropdown option that now DISPLAYS our name
    must still PERSIST the old key.
    """
    from src.models.profile import Profile
    from src.ui.theme.page import build_engine_dropdown

    assert Profile(name="ps224").engine == "chromium"

    keys = [o.key for o in build_engine_dropdown().options]
    assert keys == ["chromium", "firefox"], (
        "the dropdown's STORED keys must be untouched by a display rename"
    )


def test_the_verification_engine_identifier_is_unchanged(monkeypatch):
    """The DELIBERATE non-change the ticket asked to be decided rather than
    let drift (see ``checker_cli._chromium_label``'s docstring for the full
    reasoning).

    Pinned as a test because the cost of it drifting is SILENT: 38 committed
    reading sets carry this identifier in their headers, and a comparison
    against a record with a different header does not announce that it is
    comparing incomparable things.

    ⚠️ THE VERSION-BEARING PATH IS DRIVEN EXPLICITLY, and that is the whole
    point of the stub. In a container with no engine installed
    ``current_version()`` returns "" and the label falls to the
    ``…/unknown`` branch — so a test that just called the function would
    exercise ONE of the three return paths and MISS the branch that actually
    lands in a reading header. Measured: a mutant that renamed only the
    version-bearing f-string passed against the un-stubbed version of this
    test. Both branches are asserted below.
    """
    from src.services.verify import checker_cli
    from src.services.engine import updater

    # The branch that produces a real reading header.
    monkeypatch.setattr(updater, "current_version", lambda: "148.0.7778.215")
    label = checker_cli._chromium_label()
    assert label == "fingerprint-chromium/148.0.7778.215", label
    assert CHROMIUM_ENGINE_NAME.lower() not in label.lower()

    # And the no-engine-installed branch, which must not drift either.
    monkeypatch.setattr(updater, "current_version", lambda: "")
    unknown = checker_cli._chromium_label()
    assert unknown == "fingerprint-chromium/unknown", unknown
    assert CHROMIUM_ENGINE_NAME.lower() not in unknown.lower()


def test_renaming_the_verification_identifier_would_break_the_pool_depth_lookup():
    """WHY that identifier is not merely 'left alone for now'.

    ``PoolDepthReport.engine_report`` finds an arm by case-insensitive
    SUBSTRING of the engine header. The current identifier contains
    "chromium"; our name does not. This test states that consequence as an
    executable fact rather than a claim in a comment, so a future rename fails
    HERE — with the reason attached — instead of raising a bare KeyError deep
    in a pool-depth run.
    """
    from src.services.verify.browser_tier import CHROMIUM
    from src.services.verify.pool_depth import EngineReport, PoolDepthReport

    def _report(engine_label):
        return PoolDepthReport(
            engines=(
                EngineReport(engine=engine_label, identities=(), vectors=()),
            ),
            excluded=(),
        )

    # Today's identifier: the lookup resolves.
    assert _report("fingerprint-chromium/148.0.7778.215").engine_report(
        CHROMIUM
    ).engine.startswith("fingerprint-chromium/")

    # Renamed: the lookup goes blind.
    with pytest.raises(KeyError):
        _report(f"{CHROMIUM_ENGINE_NAME}/148.0.7778.215").engine_report(CHROMIUM)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _walk(control):
    """Every control in a Flet tree, depth-first."""
    seen = set()
    stack = [control]
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        for attr in ("controls", "content", "leading", "trailing", "title"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            if isinstance(child, (list, tuple)):
                stack.extend(c for c in child if hasattr(c, "__dict__"))
            elif hasattr(child, "__dict__"):
                stack.append(child)


def _all_text(control) -> set:
    out = set()
    for node in _walk(control):
        value = getattr(node, "value", None)
        if isinstance(value, str) and value:
            out.add(value)
    return out


def _all_tooltips(control) -> set:
    out = set()
    for node in _walk(control):
        tip = getattr(node, "tooltip", None)
        if isinstance(tip, str) and tip:
            out.add(tip)
        elif tip is not None:
            message = getattr(tip, "message", None)
            if isinstance(message, str) and message:
                out.add(message)
    return out


def _capture_launch_argv() -> list[str]:
    """Run the REAL ``spawn_browser`` against a recording stand-in binary.

    Returns the argv the engine would have been launched with. The profile is
    a default one — no proxy, no certificate — because the assertion is about
    values persona ALWAYS emits, not about a particular profile's extras.
    """
    import json
    import subprocess

    from src.models.profile import Profile
    from src.services.browser import process as _process

    tmp = tempfile.mkdtemp(prefix="ps224-")
    recorder = os.path.join(tmp, "recorder.sh")
    out = os.path.join(tmp, "argv.json")
    with open(recorder, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > {out}\n'
            "sleep 30 &\n"
            "exit 0\n"
        )
    os.chmod(recorder, 0o755)

    class _Store:
        def get(self, *a, **k):
            return None

        def resolve(self, *a, **k):
            return None

    class _Bookmarks:
        def resolve_selection(self, *a, **k):
            return []

    import unittest.mock as _mock

    patches = [
        _mock.patch.object(_process, "DATA_DIR", os.path.join(tmp, "data")),
        _mock.patch.object(_process, "ProxyStore", _Store),
        _mock.patch.object(_process, "BookmarkStore", _Bookmarks),
        _mock.patch.object(_process, "write_window_entry", lambda name: None),
        _mock.patch.object(_process, "seed_bookmarks", lambda *a, **k: None),
        _mock.patch.object(_process, "seed_profile_prefs", lambda *a, **k: None),
        _mock.patch.object(_process, "FINGERPRINT_CHROMIUM", recorder),
    ]
    for p in patches:
        p.start()
    try:
        proc = _process.spawn_browser(Profile(name="ps224-argv"))
        proc.wait(timeout=30)
    finally:
        for p in reversed(patches):
            p.stop()

    with open(out, encoding="utf-8") as fh:
        argv = [ln for ln in fh.read().split("\n") if ln]
    assert argv, "the launch recorded no arguments"
    del json, subprocess
    return argv
