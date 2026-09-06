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
  engine identifier. 36 committed reading sets carry it (26 of them as an
  ``"engine":`` header value), and
  ``pool_depth.engine_report`` finds an arm by substring-matching "chromium"
  against it.
* the stored engine key ``"chromium"`` — a display rename must not become a
  data migration.
"""

import ast
import json
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


def test_our_name_never_reaches_the_resolved_upstream_artifact(monkeypatch):
    """The property the ticket's trap is actually about, asserted on EVERY OS.

    The boundary is "nothing on disk was renamed by accident", not "this one
    Linux string is literally present" — so the assertion that has to hold
    everywhere is that OUR name is absent from whatever this platform resolves.
    Round 1 asserted the Linux VALUE unconditionally and therefore failed on
    macOS and Windows, where the same function correctly answers something
    else; the value test is below, and it drives all three branches explicitly
    rather than assuming the branch this runner happens to take.

    All three branches are exercised here too, because a rename that touched
    only the Windows arm would otherwise be invisible to a Linux CI run.
    """
    from src.core import platform as _platform

    for windows, macos in ((True, False), (False, True), (False, False)):
        monkeypatch.setattr(_platform, "IS_WINDOWS", windows)
        monkeypatch.setattr(_platform, "IS_MACOS", macos)
        resolved = _platform.fingerprint_chromium_filename()
        assert CHROMIUM_ENGINE_NAME.lower() not in resolved.lower(), (
            f"{CHROMIUM_ENGINE_NAME!r} leaked into the artifact filename this "
            f"platform layer resolves ({resolved!r}). The downloaded binary is "
            "still someone else's build; renaming what the updater resolves by "
            "name breaks the download and the launch."
        )


def test_the_upstream_artifact_filename_is_unchanged_on_every_platform(monkeypatch):
    """``fpchrome.AppImage`` is the UPSTREAM build's filename, not ours.

    The platform layer resolves it to find the downloaded binary; renaming it
    while we are still downloading someone else's build breaks the download and
    the launch. It becomes ours only once we ship a binary we built.

    ⚠️ THE FUNCTION IS PER-OS BY DESIGN (``src/core/platform.py``): the Windows
    package exposes ``chrome.exe`` and the macOS bundle
    ``Chromium.app/Contents/MacOS/Chromium``. So the three expected values are
    driven from the module's own OS flags instead of read off whichever runner
    executes this — which pins all three arms on all three platforms, rather
    than pinning one arm on one and failing on the other two.
    """
    from src.core import platform as _platform

    expected = {
        (True, False): "chrome.exe",
        (False, True): "Chromium.app/Contents/MacOS/Chromium",
        (False, False): "fpchrome.AppImage",
    }
    for (windows, macos), name in expected.items():
        monkeypatch.setattr(_platform, "IS_WINDOWS", windows)
        monkeypatch.setattr(_platform, "IS_MACOS", macos)
        assert _platform.fingerprint_chromium_filename() == name


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

    Pinned as a test because the cost of it drifting is SILENT: 26 committed
    reading sets carry this identifier as an ``"engine":`` header value (36
    carry it somewhere) — re-derive with ``git ls-files readings/ | xargs grep
    -lE '"engine"[[:space:]]*:[[:space:]]*"fingerprint-chromium/'`` — and a
    comparison against a record with a different header does not announce that
    it is comparing incomparable things.

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
    """Run the REAL ``spawn_browser`` and return the argv the engine was
    actually launched with.

    Not a re-implementation of the argv and not a grep: the product launch path
    runs in full, and the argv returned here is read back out of a real child
    process that was really executed — ``sys.argv`` as the OS delivered it.

    ⚠️ PORTABLE BY CONSTRUCTION, AND THAT IS THE POINT OF THE SEAM.
    Round 1 recorded by patching ``FINGERPRINT_CHROMIUM`` to a ``#!/bin/sh``
    script. Windows has no shebang handling, so ``CreateProcess`` refused it
    with ``OSError: [WinError 193] %1 is not a valid Win32 application`` and
    the two most load-bearing tests in this file — the ones that pin the
    non-waivable constraint — died in the helper on one of the three platforms
    we ship to. The recorder is now the current interpreter running a plain
    ``.py`` file, which needs no shebang and no shell, so these tests run on
    all three rather than being skipped where they matter.

    THE INTERCEPT IS AT THE LAUNCH SEAM, NOT AT THE BINARY. The engine path is
    left exactly as the product resolves it and ``popen_in_new_session`` — the
    single call ``spawn_browser`` launches through — is wrapped so the recorder
    is prepended to the real argv. Everything upstream of that line is the
    untouched product path: the same extensions are built on disk, the same
    flags assembled, in the same order.

    BOTH READINGS ARE CROSS-CHECKED below: what persona HANDED to the launcher
    and what the OS DELIVERED to the child must agree. A divergence would mean
    the recorder was measuring something other than the launch.
    """
    import sys

    from src.models.profile import Profile
    from src.services.browser import process as _process

    tmp = tempfile.mkdtemp(prefix="ps224-")
    out = os.path.join(tmp, "argv.json")
    recorder = os.path.join(tmp, "recorder.py")
    with open(recorder, "w", encoding="utf-8") as fh:
        fh.write(
            "import json, sys\n"
            "with open(sys.argv[1], 'w', encoding='utf-8') as fh:\n"
            "    json.dump(sys.argv[2:], fh)\n"
        )

    class _Store:
        def get(self, *a, **k):
            return None

        def resolve(self, *a, **k):
            return None

    class _Bookmarks:
        def resolve_selection(self, *a, **k):
            return []

    import unittest.mock as _mock

    handed: list[list[str]] = []
    _real_popen = _process.popen_in_new_session

    def _recording_popen(args, **kwargs):
        handed.append(list(args))
        return _real_popen([sys.executable, recorder, out, *args], **kwargs)

    patches = [
        _mock.patch.object(_process, "DATA_DIR", os.path.join(tmp, "data")),
        _mock.patch.object(_process, "ProxyStore", _Store),
        _mock.patch.object(_process, "BookmarkStore", _Bookmarks),
        _mock.patch.object(_process, "write_window_entry", lambda name: None),
        _mock.patch.object(_process, "seed_bookmarks", lambda *a, **k: None),
        _mock.patch.object(_process, "seed_profile_prefs", lambda *a, **k: None),
        _mock.patch.object(_process, "popen_in_new_session", _recording_popen),
    ]
    for p in patches:
        p.start()
    try:
        proc = _process.spawn_browser(Profile(name="ps224-argv"))
        proc.wait(timeout=60)
    finally:
        for p in reversed(patches):
            p.stop()

    with open(out, encoding="utf-8") as fh:
        delivered = json.load(fh)

    assert delivered, "the launch recorded no arguments"
    assert handed and delivered == handed[0], (
        "what persona handed to the launcher and what the OS delivered to the "
        f"child disagree — the recorder is not measuring the launch.\n"
        f"handed:    {handed[0] if handed else None}\ndelivered: {delivered}"
    )
    return delivered


# ---------------------------------------------------------------------------
# Half 4 — PS-318: the name is SOURCED, never TYPED, across every operator
#                  surface (the sweep PS-224 scoped to three strings)
# ---------------------------------------------------------------------------
#
# WHY THIS IS AST-BASED AND NOT A GREP, stated because the naive version is the
# obvious one and it is wrong in a way that costs a whole session.
#
# `grep -rn Chromium src/ui/` counts 62 occurrences on the pre-PS-318 tree, of
# which 17 are `#` comments and 26 are docstrings — including the long
# explanatory notes in `core/strings.py`'s siblings that exist precisely to
# explain the rename. Only 19 were real operator-facing strings. A guard built
# on that grep starts RED at 62, stays red after a correct fix, and sends its
# reader to rewrite prose whose whole purpose is documenting the rule. So the
# scope here is STRING LITERALS ONLY, with docstrings excluded by identity
# rather than by heuristic, which is exactly the population an operator can
# read.
#
# THE ASSERTION IS "SOURCED, NOT TYPED", NOT "SAYS PERSONIUM". A literal
# reading "Personium engine update available" would satisfy a spelling check
# and would silently re-introduce the defect this ticket exists to close: the
# next rename would again be a 19-site sweep instead of one edit. Hence the
# check is for the ABSENCE of a hardcoded engine name in a literal — the value
# must arrive by interpolating CHROMIUM_ENGINE_NAME.


_UI = os.path.join(SRC, "ui")

#: ⚠️ THE SCOPE BUG THIS TUPLE EXISTS TO CLOSE, recorded because it cost a
#: review round and because the NEXT rename will re-create it if nobody knows.
#:
#: Round 1 of PS-318 scanned ``src/ui`` ONLY, and the "19 operator-facing
#: strings" it was scoped to came from a census that was ALSO ``src/ui``-only.
#: Guard and census therefore shared one blind spot and neither could see the
#: other's, so the sweep looked complete while 15 operator-facing strings in
#: ``src/services`` still said "Chromium". The result was visible to an
#: operator: the Activity Log printed BOTH names in the same scroll, because
#: ``App._log`` is handed straight into the service layer
#: (``engine.revert_to_previous_build(log=self._log)``).
#:
#: So the scan is now rooted at ``src`` — the whole tree — and the genuine
#: non-operator uses are named ONE BY ONE below. An allow-list makes every
#: exclusion a decision somebody recorded and can be argued with; a narrower
#: root makes it a directory nobody looked in.
_SCAN_ROOT = SRC

#: Files whose "Chromium" literals are NOT our engine's operator-facing name.
#: Each entry is a decision with a reason, not a convenience.
#:
#: THE ORGANISING DISTINCTION, and the one to apply when adding an entry: our
#: engine's BRAND is "Personium", but the upstream PROJECT, its VERSION NUMBER,
#: and its ON-DISK ARTEFACTS are all still legitimately called "Chromium".
#: Renaming any of the latter would either break an install or put a
#: product-specific marker somewhere a page can read it.
_NOT_OUR_ENGINE_NAME = {
    # ON-DISK PATHS. The macOS bundle really is named Chromium.app; these are
    # filesystem coordinates the updater resolves, not text an operator reads.
    # The ticket names this exclusion explicitly.
    "core/platform.py",
    # THE DEFINITION ITSELF. core/strings.py is where the name is declared.
    "core/strings.py",
    # WIRE / UA CONCEPTS — the Chromium VERSION a page observes. Renaming any of
    # these would either change what a site sees or make the refusal message
    # describe a thing that does not exist. `engine_version.py:158` is the one
    # borderline case and is deliberately left: it names the upstream TAG, and
    # it sits in the module most tightly bound to UA derivation, which is
    # exactly where the PS-224 fence is strictest.
    "services/browser/engine_version.py",
    "services/browser/device_presets.py",
    "services/browser/process.py",
    "services/browser/mobile_ext.py",
    # INJECTED EXTENSION PAYLOADS. These are JavaScript that runs IN THE PAGE;
    # "Chromium" appears in cloaking comments. Our name must NEVER reach here —
    # `test_the_name_is_absent_from_every_extension_the_launch_injects` asserts
    # the opposite direction on the real launch.
    "services/browser/audio_ext.py",
    "services/browser/canvas_ctx_ext.py",
    "services/browser/device_ext.py",
    "services/browser/gpu_ext.py",
    "services/browser/voice_ext.py",
    "services/browser/worker_wrap.py",
    # MEASUREMENT PROVENANCE, explicitly deferred by the PS-318 ticket: these
    # values are written into committed reading artifacts, and changing one
    # makes a new reading incomparable with 36 recorded ones without saying so.
    # `test_the_verification_engine_identifier_is_unchanged` pins this.
    "services/verify/checker_cli.py",
    "services/verify/chromium_tier.py",
    "services/verify/baseline.py",
    "services/verify/behaviour.py",
    "services/verify/local_probe.py",
    "services/verify/probes.py",
}

#: Individual (file, substring) pairs allowed inside files that are OTHERWISE
#: scanned. Narrower than excluding a whole file, and used where one literal in
#: a converted file is a genuine non-brand use.
_ALLOWED_FRAGMENTS = (
    # The macOS bundle path and its staging/backup siblings, inside the updater
    # — a file whose operator-facing strings ARE scanned. Excluding the whole
    # file would have hidden the 11 strings this round had to fix.
    ("services/engine/updater.py", "Chromium.app"),
    # The upstream MAJOR VERSION in the policy refusal. The sentence now reads
    # "Personium engine <tag> is above the maximum Chromium major …" — the
    # brand is ours, the version number is upstream's, and both are correct.
    ("services/engine/policy.py", "maximum Chromium major"),
    ("services/engine/policy.py", "(Chromium "),
)

#: Spellings of our Chromium engine that must never be TYPED into an
#: operator-facing string literal. "Chromium" alone is the one that matters
#: most: it is what 19 literals said before PS-318, and it is a word a future
#: edit will reach for without thinking.
#:
#: ⚠️ OUR OWN NAME IS IN THIS TUPLE, AND IT IS THE ENTRY THAT IS EASY TO LEAVE
#: OUT — measured, not supposed. With the list holding only the three names
#: ABOVE, a mutant that replaced an interpolation with a hardcoded
#: ``f"Personium engine update available ({tag})"`` passed the whole file: it
#: reads correctly on screen, so nothing else in this suite can see it, and it
#: silently restores the 19-site sweep this ticket exists to end. The header
#: note above states the rule as "SOURCED, not TYPED"; without this entry only
#: the first half was enforced. Sourced from the constant so that renaming the
#: constant cannot leave the guard checking a name we no longer use.
_TYPED_ENGINE_NAMES = (
    "Chromium",
    "fingerprint-chromium",
    "fp-chromium",
    CHROMIUM_ENGINE_NAME,
)


def _operator_string_literals(root):
    """Every string literal under `root` that is NOT a docstring.

    Docstrings are excluded by NODE IDENTITY — the first statement of a module,
    function or class when it is a bare string expression — rather than by any
    textual heuristic. A heuristic ("starts with three quotes", "is long")
    would misclassify both ways: a triple-quoted operator message would be
    skipped, and an assigned module-level string would be treated as prose.

    Yields (path, lineno, value).
    """
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            tree = ast.parse(_read(path))

            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(
                    node,
                    (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    body = node.body
                    if (
                        body
                        and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)
                    ):
                        docstrings.add(id(body[0].value))

            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings
                ):
                    yield path, node.lineno, node.value


def _is_allowed(rel, value):
    """True when this literal is a recorded non-brand use of "Chromium".

    `rel` is the repo-relative path with forward slashes, so the allow-list
    entries read the same on every platform.
    """
    if any(rel.endswith(f) or rel == f"src/{f}" for f in _NOT_OUR_ENGINE_NAME):
        return True
    return any(
        rel.endswith(f) and frag in value for f, frag in _ALLOWED_FRAGMENTS
    )


def test_no_operator_string_literal_TYPES_the_engine_name_instead_of_SOURCING_it():
    """AC1 + AC2, scanned over ALL of `src` — not just `src/ui`.

    ⚠️ SCOPED TO LITERALS, NOT TO THE FILE. See the note above this test: the
    same sweep expressed as a grep counts comments and docstrings and is
    unfixable by construction.

    ⚠️ AND SCOPED TO THE TREE, NOT TO A DIRECTORY — this is the round-2 fix and
    the more important of the two scopings. See `_SCAN_ROOT`: the first version
    of this guard watched `src/ui` only, which is exactly why 15 operator-facing
    strings in `src/services` survived a sweep that looked complete and put two
    different engine names in the same Activity Log.

    MUTATION-CHECKED IN BOTH TERRITORIES, and this is not a claim — it was run.
    Re-introducing `f"Chromium engine check failed: {e}"` in `app.py` turns this
    RED at that line; so does re-hardcoding `"Chromium engine: automatic updates
    resumed"` in `services/engine/updater.py`, which is the NEW territory and
    would have passed round 1's guard. Removing each turns it green again.
    """
    offenders = []
    for path, lineno, value in _operator_string_literals(_SCAN_ROOT):
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        if _is_allowed(rel, value):
            continue
        for typed in _TYPED_ENGINE_NAMES:
            if typed in value:
                offenders.append(f"{rel}:{lineno}: {value[:80]!r}")

    assert not offenders, (
        "an operator-facing string literal TYPES our Chromium engine's name "
        f"instead of interpolating {CHROMIUM_ENGINE_NAME!r} from "
        "core.strings.CHROMIUM_ENGINE_NAME (in the service layer, via "
        "services.engine_naming.engine_display_name). Sourcing it is what "
        "makes the next rename ONE edit instead of a sweep:\n  "
        + "\n  ".join(offenders)
    )


def test_the_allow_list_is_honest_and_not_a_place_to_hide_a_defect():
    """The allow-list must EARN each entry, or it becomes a silent opt-out.

    An allow-list is only better than a narrow scan root if its entries are
    real. Two ways it could rot, both checked here:

    * a STALE entry — a file that no longer contains any matching literal, left
      behind to excuse a defect somebody might add later;
    * an OVER-BROAD entry — a fragment allowance that would swallow the plain
      "Chromium engine:" brand string this ticket exists to eliminate.
    """
    seen = {}
    for path, _lineno, value in _operator_string_literals(_SCAN_ROOT):
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        if any(typed in value for typed in _TYPED_ENGINE_NAMES):
            seen.setdefault(rel, []).append(value)

    stale = [
        f
        for f in _NOT_OUR_ENGINE_NAME
        if not any(r.endswith(f) for r in seen)
    ]
    assert not stale, (
        "these allow-list entries no longer match any literal — remove them "
        f"rather than leaving a standing excuse: {sorted(stale)}"
    )

    stale_frags = [
        (f, frag)
        for f, frag in _ALLOWED_FRAGMENTS
        if not any(r.endswith(f) and frag in v for r, vs in seen.items() for v in vs)
    ]
    assert not stale_frags, (
        f"these fragment allowances match nothing any more: {stale_frags}"
    )

    # No fragment allowance may excuse the brand string itself.
    for _f, frag in _ALLOWED_FRAGMENTS:
        assert "Chromium engine:" not in frag, (
            f"the fragment allowance {frag!r} would excuse the very string "
            "this guard exists to catch"
        )


def test_the_guard_above_is_not_vacuous():
    """The scan must actually be READING the tree, and reading a lot of it.

    Without this, a broken walk (wrong path, a parse that silently yields
    nothing) makes the guard pass by finding no literals at all — the classic
    way a sweep test goes green by testing nothing. The floor is deliberately
    well below the real count so ordinary edits never trip it.

    BOTH ROOTS ARE PINNED. `src/ui` is checked on its own as well as the whole
    tree, because an allow-list bug that accidentally excluded the entire UI
    directory would still leave the tree-wide count comfortably above its floor
    — and `src/ui` is where the operator strings this ticket started from live.
    """
    literals = list(_operator_string_literals(_SCAN_ROOT))
    assert len(literals) > 2000, (
        f"the literal scan found only {len(literals)} strings in src — it "
        "is not reading the tree, so the guard above proves nothing"
    )

    ui_literals = list(_operator_string_literals(_UI))
    assert len(ui_literals) > 500, (
        f"the literal scan found only {len(ui_literals)} strings in src/ui — "
        "the UI subtree is not being read"
    )

    # ...and the SERVICE layer, the territory round 1's guard could not see, is
    # genuinely in scope now rather than nominally.
    service_literals = [
        v
        for p, _l, v in literals
        if "services/engine" in p.replace(os.sep, "/")
    ]
    assert len(service_literals) > 100, (
        "the scan is not reaching src/services/engine — the exact blind spot "
        "that let 15 operator strings survive round 1"
    )

    # ...and it must be finding the CONVERTED sites, not merely some strings:
    # each of these is one of the 19 literals PS-318 rewrote, now carrying the
    # interpolated name.
    values = [v for _p, _l, v in literals]
    assert any("engine check failed" in v for v in values)
    assert any("engine update available" in v for v in values)
    assert any("keeps one engine build at a time" in v for v in values)


def test_the_scan_would_actually_catch_a_hardcoded_name():
    """The mutation, executed IN-PROCESS rather than described.

    The test above says it was mutation-checked by hand. This one makes that
    property permanent: the same predicate is run against a synthetic module
    carrying exactly the defect, and it must flag it. If someone later
    "simplifies" the scan into something that cannot see a literal, this fails
    even though the real tree happens to be clean.
    """
    import tempfile as _tf

    with _tf.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "mutant.py"), "w", encoding="utf-8") as fh:
            fh.write(
                '"""A docstring naming Chromium — prose, must NOT be flagged."""\n'
                "# A comment naming Chromium — must NOT be flagged either.\n"
                'MESSAGE = "Chromium engine check failed"\n'
            )
        found = [
            (ln, v)
            for _p, ln, v in _operator_string_literals(tmp)
            if any(t in v for t in _TYPED_ENGINE_NAMES)
        ]

    assert len(found) == 1, (
        f"the scan must flag the LITERAL and only the literal; it found {found}"
    )
    assert found[0][0] == 3, f"it flagged the wrong line: {found}"


def test_no_user_facing_fingerprint_chromium_in_readme_or_site_source():
    """AC3. The two surfaces outside src/ui an operator reads.

    ⚠️ `ungoogled-chromium` IS ALLOWED AND IS NOT AN OVERSIGHT. It names the
    upstream project we genuinely build on — the exact analogue of the Firefox
    option naming `invisible_playwright`, which the fence above pins. What the
    owner's decision removed is `fingerprint-chromium`: we dropped it from our
    flow, so crediting it was a claim that had stopped being true. Crediting
    ungoogled-chromium is a claim that is still true, and dropping it would be
    the opposite defect.
    """
    banned = ("fingerprint-chromium", "fp-chromium")

    targets = [os.path.join(REPO_ROOT, "README.MD")]
    site_src = os.path.join(REPO_ROOT, "site", "src")
    for dirpath, _dirs, files in os.walk(site_src):
        targets.extend(os.path.join(dirpath, fn) for fn in sorted(files))

    offenders = []
    for path in targets:
        try:
            body = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(body.splitlines(), 1):
            if any(b in line for b in banned):
                offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{i}: {line.strip()[:90]}")

    assert not offenders, (
        "a user-facing surface still credits fingerprint-chromium, a "
        "dependency we no longer have:\n  " + "\n  ".join(offenders)
    )

    # NOT VACUOUS: the README really was read, and it really does name our
    # engine. A walk that found nothing would pass the assertion above.
    readme = _read(os.path.join(REPO_ROOT, "README.MD"))
    assert CHROMIUM_ENGINE_NAME in readme, (
        f"the README does not name {CHROMIUM_ENGINE_NAME} at all — the scan "
        "above read nothing, or the rename did not reach it"
    )


def test_the_upstream_we_DO_depend_on_is_still_credited():
    """The other direction of AC3, and the reason it is not a blanket ban.

    A sweep that removed every "chromium" token from the README would pass the
    test above and would delete a TRUE attribution. ungoogled-chromium is what
    we actually build on; saying so is accuracy, not a leftover.
    """
    readme = _read(os.path.join(REPO_ROOT, "README.MD"))
    assert "ungoogled-chromium" in readme


def test_an_existing_profile_on_disk_still_resolves_to_the_chromium_engine(
    tmp_path, monkeypatch
):
    """AC4, driven through the REAL loader rather than asserted on a fresh
    dataclass default.

    `test_the_stored_engine_key_is_unchanged` above pins the default and the
    dropdown keys — both properties of code that was never written to disk.
    This one pins what an UPGRADING operator actually cares about: a
    `profiles.json` written before the rename is read back by the shipped
    `ProfileManager` and still resolves to the Chromium engine. A display
    rename that quietly became a data migration fails HERE, on a fixture, and
    not in the field on somebody's saved profiles.

    The fixture is deliberately a MINIMAL record — name, engine, os_type — the
    shape an older build wrote. The loader's own absent-key defaults do the
    rest, which is the migration path being asserted.
    """
    import json as _json
    import pathlib as _pathlib

    from src.services.profile import manager as manager_mod

    profiles_file = tmp_path / "profiles.json"
    profiles_file.write_text(
        _json.dumps(
            {
                "ps318-existing": {
                    "name": "ps318-existing",
                    "engine": "chromium",
                    "os_type": "windows",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(manager_mod, "PROFILES_FILE", str(profiles_file))

    mgr = manager_mod.ProfileManager.__new__(manager_mod.ProfileManager)
    mgr.profiles = {}
    mgr._load_profiles_locked()

    assert "ps318-existing" in mgr.profiles, (
        f"the pre-rename fixture did not load at all: "
        f"{_pathlib.Path(profiles_file).read_text(encoding='utf-8')}"
    )
    loaded = mgr.profiles["ps318-existing"]
    assert loaded.engine == "chromium", (
        "a saved profile's stored engine key changed — a display rename became "
        "a data migration, and every existing installation's profiles would "
        "stop resolving on upgrade"
    )
    assert loaded.to_dict()["engine"] == "chromium", (
        "the round-trip must write back the SAME key it read"
    )

    # AND THE LAUNCH SIDE AGREES. Loading the record is only half of AC4: what
    # an upgrading operator depends on is that the profile still LAUNCHES the
    # Chromium engine. `effective_engine` is the resolver the launch path
    # actually consults (`services/browser/process.py`) — `profile.engine` is
    # deliberately NOT what it reads — so a rename that survived the loader and
    # broke the resolver would pass every assertion above.
    from src.services.browser.process import effective_engine

    assert effective_engine(loaded) == "chromium", (
        "the launch path no longer resolves a pre-rename profile to the "
        "chromium engine"
    )


def test_the_shipped_changelog_entries_name_the_engine_we_actually_ship():
    """The three changelog literals, asserted on the RENDERED dict.

    The literal scan proves no entry TYPES "Chromium"; it cannot prove the
    entries still name the engine at all — deleting the three sentences would
    satisfy it just as well. This reads the built `CHANGELOG` and asserts our
    name is really there, which is the only check that distinguishes a rename
    from a deletion.

    ⛔ SHIPPED HISTORY IS REWRITTEN ON PURPOSE (the PS-318 owner decision).
    These are notes for 3.0.2 and earlier. The engine was already ours when
    they shipped — they named it by a label that has since been corrected — so
    leaving them would keep an inaccurate name in front of every operator who
    opens the "what's new" panel, indefinitely.
    """
    from src.ui.changelog import CHANGELOG

    named = [
        entry
        for entries in CHANGELOG.values()
        for entry in entries
        if CHROMIUM_ENGINE_NAME in entry
    ]
    assert len(named) >= 3, (
        "the shipped changelog entries naming our Chromium engine are gone or "
        f"were never renamed; found {len(named)}: {named}"
    )


def test_the_sidebar_rail_measurement_still_bounds_the_engine_name():
    """AC6, as an executable bound rather than a comment.

    The 200px rail was measured against "fp-chromium" (11 chars). Our name is
    shorter, so the measurement still holds — but "shorter" is the load-bearing
    fact, and it is currently recorded only in a comment in `app.py`. Stated
    here so a LONGER future name fails a test instead of silently ellipsising
    the version cell to "ch…" on somebody's screen.
    """
    assert len(CHROMIUM_ENGINE_NAME) <= len("fp-chromium"), (
        f"{CHROMIUM_ENGINE_NAME!r} is longer than the 11-char string the 200px "
        "sidebar rail was measured against. Re-measure the rail (see the note "
        "at App._engine_row) before widening the name."
    )
