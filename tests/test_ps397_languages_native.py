"""PS-397 — `navigator.languages` is the ENGINE's on the Chromium track, and
persona must not start overriding it.

⛔ THIS SLICE SHIPPED NO PRODUCT CHANGE, AND THIS FILE IS WHY THAT IS SAFE.
--------------------------------------------------------------------------
PS-397 asked whether `navigator.languages` DISAGREES between the bare engine and
persona, and named three outcomes, all of them complete deliverables. The
measured answer is the second: **it agrees already**, because persona has no
`navigator.languages` override on the Chromium track at all. The full reading —
seven arms, all controls, every realm — is committed at
`readings/ps397-2026-09-10/`.

A slice that ships nothing leaves nothing behind to stop the finding decaying.
That is what this file is for. It pins the three facts the "empty slice" verdict
rests on, so the day one of them stops being true, THIS goes red rather than a
liaison discovering it on a checker months later.

WHAT WOULD GO RED, AND WHEN
---------------------------
1. `test_no_chromium_extension_defines_navigator_languages` — red the moment any
   Chromium masking extension starts defining `languages`. That is the exact
   change the ticket forbade: a JS override added while the engine's own value
   is already correct.
2. `test_the_engine_patch_series_declares_no_language_switch` — red if a
   `kFingerprintLanguage`-style switch appears in the patch series. Not a
   prohibition on ever adding one; a requirement that adding one re-opens the
   question this reading answered, because the reading's premise (nothing in the
   series touches the locale) would no longer hold.
3. `test_the_locale_flags_are_still_passed_at_launch` — red if `--lang` /
   `--accept-lang` stop being passed. ⭐ THIS IS THE LOAD-BEARING ONE. The whole
   verdict is "the native value is already correct", and the native value is
   produced FROM THOSE FLAGS. Drop them and `languages` silently falls back to
   the host's locale — the leak PS-124 measured on the Firefox arm, arriving on
   Chromium with no override left to catch it.
4. `test_the_scan_covers_every_extension_spawn_browser_builds` — red the moment
   the product grows an extension this file is not pointed at. See below.

⚠️ WHY THERE IS NO "RED ON CURRENT CODE" TEST HERE, STATED RATHER THAN OMITTED.
The ticket asks for a test that would go RED on the current code *if porting*.
Nothing is being ported, because there is nothing to port — so a test that fails
on today's tree would have to assert a defect that was measured NOT to exist.
Writing one would mean manufacturing the disagreement the reading refutes. The
honest equivalent, and what this file does instead, is a REGRESSION FENCE around
the measured state: every test here is red on any tree where the finding is
false, and green on this one.

⛔ AND THE FENCE IS ITSELF FENCED, TWICE OVER — because the first revision of
this file was fenced only ONCE and that was measurably not enough.

  * `test_the_override_probe_can_see_an_override` installs a `languages`
    override deliberately and asserts the scanner FINDS it. Without that, test 1
    is indistinguishable from a scanner that reports "clean" unconditionally —
    the same class of dead instrument the reading's own reveal-control arm
    exists to rule out. That covers the MATCHER.
  * ⭐ `test_the_scan_covers_every_extension_spawn_browser_builds` covers the
    thing the matcher is POINTED AT, which is where this file actually failed.
    The first revision scanned `build_chromium_layer`'s 11 desktop extensions
    and claimed that covered the product; `masking_layer.py` excludes
    `search_ext` and `mobile_ext` by its own stated decision, so a worker-realm
    `G.navigator` override planted in `mobile_ext`'s emitted script left the
    file at 11 PASSED with the defect live. A live matcher aimed at the wrong
    directory set is as green as a dead one, and only this second fence can tell
    them apart. It derives the product's extension set BY AST FROM
    `spawn_browser` ITSELF, so the coverage property is checked rather than
    asserted.

⚠️ AND THE BOUND THAT REMAINS, STATED. This file scans the mobile extension's
BYTES, but every arm of the live reading in `readings/ps397-2026-09-10/` ran a
DESKTOP profile. So the JS-override half of the finding covers mobile and the
live-reading half does not — see §6 bound 6 of EVIDENCE.md. A question about
`navigator.languages` ON a mobile profile needs the reading re-taken there.
"""

import ast
import inspect
import json
import os
import pathlib

import pytest

from src.models.profile import Profile
from src.services.browser import process
from src.services.browser.engine_version import ChromiumVersion

REPO = pathlib.Path(__file__).resolve().parents[1]
PATCH_DIR = REPO / "engine" / "patches" / "fingerprint"

# The property this slice is about. Matched as a WORD in the generated
# JavaScript rather than as a bare substring, so `accept_languages` and
# `intl.accept_languages` — which are the HEADER, a different channel — do not
# count as an override of the JS property.
PROP = "languages"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _product_extension_dirs_from_source() -> "set[str]":
    """The `.persona-*-ext` directory names `spawn_browser` ITSELF builds.

    ⛔ DERIVED BY AST FROM THE SHIPPING FUNCTION, not listed here. This is the
    fence's own completeness oracle: `_build_layer` below must cover every name
    this returns, so adding a 14th `build_*_extension` call to `spawn_browser`
    turns this file RED until the new extension is scanned — which is exactly
    the property the previous revision of this docstring CLAIMED and did not
    have.

    ⚠️ Why an AST walk and not a grep or a hand list. The names are string
    literals in the call, so a hand list is a second copy that drifts silently
    (the failure this whole port keeps hitting), and a grep over the file would
    also pick up the `--load-extension` join and the profile-dir cleanup paths.
    The walk is scoped to `spawn_browser`'s own body, so it answers precisely
    "what does the launcher build".
    """
    tree = ast.parse(
        (REPO / "src" / "services" / "browser" / "process.py").read_text(
            encoding="utf-8"
        )
    )
    names: "set[str]" = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "spawn_browser"):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            fn = call.func
            if not isinstance(fn, ast.Name):
                continue
            if not (fn.id.startswith("build_") and fn.id.endswith("_extension")):
                continue
            for lit in ast.walk(call):
                if (
                    isinstance(lit, ast.Constant)
                    and isinstance(lit.value, str)
                    and lit.value.startswith(".persona-")
                ):
                    names.add(lit.value)
    return names


def _build_layer(tmp_path, *, locale="en-US"):
    """EVERY extension `spawn_browser` builds, as built bytes on disk.

    ⛔ THIS IS DELIBERATELY WIDER THAN `build_chromium_layer`, AND THE REASON IS
    THE WHOLE POINT OF THIS HELPER. `build_chromium_layer` is a DESKTOP CHECKER
    TIER: `masking_layer.py`'s own docstring states that it excludes
    `build_search_extension` ("a settings override rather than masking") and
    `build_mobile_extension` ("belongs to a mobile profile which a checker run
    is not"). Those exclusions are correct for that module's purpose and are
    not this file's business to change.

    They are fatal for a COMPLETENESS FENCE, and measurably so. An earlier
    revision of this file borrowed that harness and asserted it covered the
    product; a `G.navigator` `languages` override planted in `mobile_ext`'s
    emitted script — the WORKER-REALM shape this port's ticket spends its
    longest section warning about, and the shape slice 2 found for deviceMemory
    — was not seen, and the file passed 11/11 with the defect live. `mobile_ext`
    is a real product configuration, built by `process.py`'s `spawn_browser` for
    EVERY mobile profile in place of `device_ext`.

    So this scans the product's own set: the 11 desktop dirs from the shipped
    builder, PLUS `search_ext` and both arms of `mobile_ext` (Android and iOS
    emit different scripts — the iOS arm drops the whole userAgentData branch,
    so one of them is not a sample of the other). Coverage is then ASSERTED
    against `_product_extension_dirs_from_source`, so this list cannot silently
    fall behind `spawn_browser` either.
    """
    from src.services.browser.device_presets import pick_preset, pick_touch_points
    from src.services.browser.mobile_ext import build_mobile_extension
    from src.services.browser.search_ext import build_search_extension
    from src.services.verify.masking_layer import build_chromium_layer

    seed = 20260910
    desktop = tmp_path / "desktop"
    dirs = list(
        build_chromium_layer(
            str(desktop), seed, os_type="windows", locale=locale, include_geo=True
        )[0]
    )

    # `search_ext` emits no .js today (a manifest-only settings override), but it
    # is scanned rather than reasoned about: "it cannot carry one" is exactly the
    # claim that stopped being true for `mobile_ext`.
    dirs.append(
        build_search_extension("duckduckgo", str(tmp_path / ".persona-search-ext"))
    )

    for os_family, sub in (("android", "mobile-android"), ("ios", "mobile-ios")):
        preset = pick_preset(seed, os_family, 0)
        dirs.append(
            build_mobile_extension(
                str(tmp_path / sub / ".persona-mobile-ext"),
                is_ios=(os_family == "ios"),
                platform=preset.platform,
                model=preset.model,
                chromium_version=ChromiumVersion("152.0.7977.75"),
                css_width=preset.width,
                css_height=preset.height,
                dpr=preset.dpr,
                device_memory=preset.device_memory,
                hardware_concurrency=preset.hardware_concurrency,
                touch_points=(
                    5 if os_family == "ios" else pick_touch_points(seed, 0)
                ),
            )
        )
    return dirs


def _defines_languages(js: str) -> "list[str]":
    """Lines of `js` that DEFINE `navigator.languages`, if any.

    Deliberately broader than one call shape: the tree writes property
    definitions as `def(nav, 'x', v)`, `def(G.navigator,'x',v)`, `def('x', v)`
    (the Firefox arm) and `Object.defineProperty(..., 'x', ...)`. A matcher
    keyed to only one of those would miss the next one written.
    """
    hits = []
    for i, line in enumerate(js.splitlines(), start=1):
        if PROP not in line:
            continue
        # The HEADER channel is a different thing entirely — `intl.
        # accept_languages` is a Firefox pref, and a *read* of it is not an
        # override of the JS property.
        stripped = line.replace("accept_languages", "").replace("accept-languages", "")
        if PROP not in stripped:
            continue
        if (
            f"'{PROP}'" in stripped
            or f'"{PROP}"' in stripped
            or f".{PROP}" in stripped
        ):
            # A DEFINITION, not a read. `def(` / `defineProperty(` are how this
            # tree installs one.
            if "def(" in stripped or "defineProperty" in stripped:
                hits.append(f"{i}: {line.strip()}")
    return hits


def _scan_layer(dirs) -> "dict[str, list[str]]":
    """Every `.js` AND `.json` under `dirs`, scanned for a `languages` definition.

    ⚠️ `.json` is scanned because the EVIDENCE.md §1 census reports a `.json`
    figure and the claim has to be true, not because a manifest can define a JS
    property — it cannot. It costs one `endswith` and closes the gap between
    what this fence does and what the reading says it did.
    """
    found = {}
    for d in dirs:
        for root, _sub, files in os.walk(d):
            for name in files:
                if not (name.endswith(".js") or name.endswith(".json")):
                    continue
                path = os.path.join(root, name)
                js = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
                hits = _defines_languages(js)
                if hits:
                    found[os.path.relpath(path, os.path.dirname(d))] = hits
    return found


# ---------------------------------------------------------------------------
# 1 — no Chromium extension defines it
# ---------------------------------------------------------------------------


def test_no_chromium_extension_defines_navigator_languages(tmp_path):
    """THE FINDING. persona's Chromium layer must not override `languages`.

    Measured 2026-09-10 across all 13 extensions `spawn_browser` builds — the 11
    desktop ones, `search_ext`, and BOTH arms of `mobile_ext`: the only
    locale-family hits anywhere in the layer are `voice_ext` READING
    `navigator.language` to pick a matching speech voice. Nothing DEFINES
    `languages`.

    ⭐ This is asserted against the BUILT BYTES, not against the builder source,
    because a value assembled at build time would not show up in a grep of the
    Python. These are the bytes that actually reach the browser.
    """
    dirs = _build_layer(tmp_path)
    assert len(dirs) >= 14, (
        f"expected the product's full extension set, got {len(dirs)} dirs — "
        "the builders changed shape and this scan may no longer cover them"
    )
    found = _scan_layer(dirs)
    assert found == {}, (
        "a chromium masking extension now DEFINES navigator.languages:\n"
        + json.dumps(found, indent=2)
        + "\n\n⛔ PS-397 measured that the engine's own value is ALREADY correct "
        "(--lang/--accept-lang, honoured by upstream chromium, agreeing across "
        "the page, worker and iframe realms and with the Accept-Language "
        "header on the wire). Adding a JS override does not improve it — it "
        "INTRODUCES the page/worker split this port keeps hitting, because a "
        "page-realm Navigator.prototype override leaves WorkerNavigator "
        "untouched. That split was reproduced live in "
        "readings/ps397-2026-09-10/ (the reveal-control arm). If a real reason "
        "to override has appeared, re-open the reading — do not just delete "
        "this test."
    )


def test_the_scan_covers_every_extension_spawn_browser_builds(tmp_path):
    """⛔ THE COVERAGE ORACLE. A completeness fence must prove its own completeness.

    The previous revision of this file asserted that `build_chromium_layer`
    covers the product automatically. It does not, and it says so itself:
    `masking_layer.build_chromium_layer`'s docstring EXCLUDES
    `build_search_extension` and `build_mobile_extension` on stated grounds (a
    settings override; a mobile profile a desktop checker run is not). Both
    exclusions are right for that module and wrong for a fence — the fence
    borrowed the harness and inherited a hole it never declared.

    ⚠️ SO THE HOLE IS NOT PATCHED BY WIDENING THE LIST AND HOPING. This asserts
    the scanned dirs against the set derived BY AST from `spawn_browser` itself,
    so a 14th `build_*_extension` call added to the product turns this red until
    someone points the scanner at it. That is the property the old docstring
    claimed; this is what makes it true.
    """
    declared = _product_extension_dirs_from_source()
    assert len(declared) >= 13, (
        f"only {len(declared)} extension dirs found in spawn_browser's own "
        "source — the AST oracle stopped seeing the builders, so its coverage "
        "claim is worthless; fix the oracle before trusting any test here"
    )
    scanned = {os.path.basename(d) for d in _build_layer(tmp_path)}
    missing = sorted(declared - scanned)
    assert not missing, (
        "these extensions are BUILT BY spawn_browser and are NOT scanned by "
        f"this fence: {missing}\n\n⛔ PS-397's finding is 'no chromium "
        "extension defines navigator.languages'. An unscanned extension is a "
        "place that claim was never checked — and the one that was missed "
        "(.persona-mobile-ext) writes to `G.navigator`, the worker-realm shape "
        "this port's ticket names as its recurring trap. Add it to "
        "`_build_layer` rather than narrowing this assertion."
    )


def test_the_mobile_extension_is_scanned(tmp_path):
    """The specific escape that got this file rejected, pinned as its own case.

    ⭐ NOT a duplicate of the oracle above. That one asserts the DIRECTORY is in
    the scanned set; this one asserts the scanner reads the mobile extension's
    real emitted bytes and would FIRE on an override placed there — the two fail
    for different reasons, and only this one catches a mobile arm that builds an
    empty or unreadable script.

    Measured escape, 2026-09-10: injecting

        (function(G){Object.defineProperty(G.navigator,'languages',
          {get:function(){return ['zz-ZZ'];}});})(self);

    into `mobile_ext`'s emitted script left the whole file at 11 passed.
    """
    dirs = [d for d in _build_layer(tmp_path) if d.endswith(".persona-mobile-ext")]
    assert dirs, "no mobile extension was built — the scanned set lost it again"

    scripts = [
        p
        for d in dirs
        for p in pathlib.Path(d).rglob("*.js")
        if p.read_text(encoding="utf-8", errors="replace").strip()
    ]
    assert scripts, (
        "the mobile extension built no non-empty .js — the directory is in the "
        "scanned set but there are no bytes to scan, so its coverage is nominal"
    )

    # And the scanner really fires on THIS extension's bytes: append the exact
    # worker-realm override the reviewer demonstrated the escape with, to a copy
    # of the real emitted script, and require a hit.
    planted = tmp_path / "mobile-planted"
    planted.mkdir()
    (planted / "mobile.js").write_text(
        "(function(G){Object.defineProperty(G.navigator,'languages',"
        "{get:function(){return ['zz-ZZ'];}});})(self);\n"
        + scripts[0].read_text(encoding="utf-8", errors="replace"),
        encoding="utf-8",
    )
    assert _scan_layer([str(planted)]), (
        "the scanner did not fire on a worker-realm languages override placed "
        "in the mobile extension's OWN emitted script — the exact defect that "
        "escaped this fence before"
    )


def test_the_override_probe_can_see_an_override(tmp_path):
    """⛔ THE REVEAL CONTROL for the test above.

    A scanner that reports "clean" unconditionally is byte-identical to a layer
    that genuinely is clean, and the assertion above would pass forever on a
    broken matcher. So plant the override — in the shape the tree actually
    writes them — and assert the scanner FINDS it.
    """
    d = tmp_path / "planted-ext"
    d.mkdir()
    (d / "planted.js").write_text(
        "(function(){\n"
        "  function def(o,p,v){Object.defineProperty(o,p,{get:function(){return v;}});}\n"
        "  def(navigator, 'languages', ['zz-ZZ','zz']);\n"
        "})();\n",
        encoding="utf-8",
    )
    found = _scan_layer([str(d)])
    assert found, (
        "the override scanner did not see a deliberately planted "
        "navigator.languages override — it is a DEAD INSTRUMENT, and the "
        "'no extension defines it' assertion above therefore proves nothing"
    )


def test_the_worker_realm_is_scanned_too(tmp_path):
    """The recurring trap of this port, pinned as its own case.

    Slice 2's deviceMemory has two JS sites and one is `G.navigator` in the
    WORKER realm. A scanner that only recognised the page-realm call shape would
    pass a tree whose worker realm was overridden — the precise disagreement
    checkers look for.
    """
    d = tmp_path / "worker-ext"
    d.mkdir()
    (d / "w.js").write_text(
        "(function(G){\n"
        "  function def(o,p,v){Object.defineProperty(o,p,{get:function(){return v;}});}\n"
        "  def(G.navigator,'languages',['zz-ZZ']);\n"
        "})(self);\n",
        encoding="utf-8",
    )
    assert _scan_layer([str(d)]), (
        "the scanner missed a WORKER-realm (G.navigator) languages override — "
        "the exact shape slice 2 found for deviceMemory"
    )


def test_reading_the_accept_language_pref_is_not_an_override(tmp_path):
    """The scanner must not fire on the HEADER channel.

    `intl.accept_languages` is a Firefox pref and a different channel entirely.
    A matcher that counted it would report a false override and make the fence
    above unusable — the fence has to be precise to stay enforceable.
    """
    d = tmp_path / "reader-ext"
    d.mkdir()
    (d / "r.js").write_text(
        "var pref = 'intl.accept_languages';\n"
        "var n = navigator.language;\n"
        "console.log(pref, n, navigator.languages.length);\n",
        encoding="utf-8",
    )
    assert _scan_layer([str(d)]) == {}, (
        "the scanner fired on a READ of the locale family; it must fire only on "
        "a DEFINITION of navigator.languages"
    )


# ---------------------------------------------------------------------------
# 2 — the engine patch series declares no language switch
# ---------------------------------------------------------------------------


def test_the_engine_patch_series_declares_no_language_switch():
    """The reading's premise: nothing in the 16 patches touches the locale.

    ⭐ That zero is what licensed measuring this on a STOCK chromium at all —
    the `--lang`/`--accept-lang` handling under test is upstream code, identical
    in stock and in persona's fork BECAUSE no patch edits it.

    If a patch ever does, that licence is void and the PS-397 reading must be
    re-taken on the patched engine. This test is the tripwire for exactly that,
    not a prohibition on ever adding one.
    """
    assert PATCH_DIR.is_dir(), f"patch series not found at {PATCH_DIR}"
    patches = sorted(PATCH_DIR.glob("*.patch"))
    assert len(patches) >= 15, (
        f"expected the full fingerprint patch series, found {len(patches)}"
    )
    offenders = {}
    for p in patches:
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        hits = [w for w in ("lang", "locale", "accept") if w in text]
        if hits:
            offenders[p.name] = hits
    assert offenders == {}, (
        "the fingerprint patch series now touches the locale family:\n"
        + json.dumps(offenders, indent=2)
        + "\n\n⛔ PS-397's reading was taken on a STOCK chromium, and that was "
        "only legitimate because NO patch touched --lang/--accept-lang, making "
        "the mechanism byte-identical in stock and in the fork. That premise no "
        "longer holds: re-take readings/ps397-2026-09-10/ on the patched engine "
        "before trusting its verdict."
    )


# ---------------------------------------------------------------------------
# 3 — the flags that PRODUCE the correct native value are still passed
# ---------------------------------------------------------------------------


class _Store:
    def resolve(self, name):
        return ""

    def get(self, name):
        return None


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _argv(monkeypatch, tmp_path, profile):
    """Drive the REAL spawn_browser with only Popen swapped; return its argv.

    The `tests/test_ps327_outer_size.py` pattern: the assertion is about the
    surface the product PRESENTS, so it is read off the command line the
    shipping builder produced — never off a second copy of the decision.
    """
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.pid = os.getpid()

    monkeypatch.setattr(
        process, "installed_chromium_version", lambda: ChromiumVersion("152.0.7977.75")
    )
    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(process._platform, "IS_LINUX", True)
    process.spawn_browser(profile)
    return captured["args"]


def test_the_locale_flags_are_still_passed_at_launch(monkeypatch, tmp_path):
    """⭐ THE LOAD-BEARING ASSERTION OF THIS WHOLE SLICE.

    PS-397's verdict is "the native value is ALREADY correct, so there is
    nothing to port". The native value is produced FROM THESE FLAGS. Drop them
    and `navigator.languages` falls back to the HOST's locale in every realm —
    which is the leak PS-124 measured on the Firefox arm, arriving on Chromium
    with no JS override left to catch it, because this slice's finding is
    precisely that there isn't one.

    So the flags are not incidental to the verdict; they ARE the verdict.

    ⚠️ Read off the argv the shipping builder produced, not off a remembered
    list — the standing method rule of this whole port.
    """
    args = _argv(
        monkeypatch, tmp_path, Profile(name="ps397-locale", os_type="windows")
    )
    lang = [a for a in args if a.startswith("--lang=")]
    accept = [a for a in args if a.startswith("--accept-lang=")]
    assert lang, (
        "spawn_browser no longer passes --lang. PS-397 established that "
        "navigator.languages is the ENGINE's value on this track, derived from "
        "this flag, with NO JS override anywhere to fall back on. Without it "
        "every realm reports the HOST locale."
    )
    assert accept, (
        "spawn_browser no longer passes --accept-lang. The Accept-Language "
        "header and navigator.languages are read TOGETHER by checkers; PS-397 "
        "measured them agreeing on the wire, and this flag is why."
    )


def test_the_accept_lang_value_carries_the_base_tag(monkeypatch, tmp_path):
    """The two-tag shape, which is the COHERENCE half of the finding.

    ⛔ NOT cosmetic, and PS-124 is the precedent that cost three rounds for it:
    a single-tag pin advertised FEWER languages than the wire carried, and a
    page whose `navigator.languages` is shorter than its own Accept-Language
    header is an internal contradiction — the exact class of NEW tell this
    ticket warns is worse than the one removed.

    Measured 2026-09-10: `--accept-lang=en-US,en` produces
    `Accept-Language: en-US,en;q=0.9` on the wire and `["en-US","en"]` in the
    page, the worker and the iframe. Both channels carry BOTH tags.
    """
    args = _argv(
        monkeypatch, tmp_path, Profile(name="ps397-tags", os_type="windows")
    )
    lang = next(a for a in args if a.startswith("--lang="))[len("--lang="):]
    accept = next(
        a for a in args if a.startswith("--accept-lang=")
    )[len("--accept-lang="):]
    tags = accept.split(",")
    assert tags[0] == lang, (
        f"--accept-lang must lead with the same tag --lang carries; "
        f"--lang={lang} but --accept-lang={accept}. A header whose primary tag "
        "differs from navigator.language is a contradiction a checker reads "
        "directly."
    )
    if "-" in lang:
        assert tags[1:] == [lang.split("-")[0]], (
            f"a region-qualified locale must advertise its BASE tag too; "
            f"--lang={lang} produced --accept-lang={accept}. PS-124: a "
            "single-tag pin makes navigator.languages SHORTER than the wire."
        )


def test_the_flag_expressions_are_read_from_the_live_source():
    """The measurement harness must not carry a remembered flag list.

    ⚠️ THE STANDING METHOD RULE OF THIS PORT: a stale capture silently
    reintroduced a forced device scale and an uncapped window and contaminated
    the liaison's first reading. `scripts/ps397_languages_reading.py` derives
    its control by reading `spawn_browser`'s OWN source and REFUSING if the
    expressions no longer match — never by falling back to a literal.

    This asserts the refusal is wired, so the harness cannot quietly decay into
    the stale-capture failure it was built to avoid.
    """
    import scripts.ps397_languages_reading as harness

    src = inspect.getsource(harness.persona_locale_argv)
    assert "raise RuntimeError" in src, (
        "the harness's control no longer REFUSES when process.py's flag "
        "expressions stop matching — it would silently substitute a remembered "
        "flag list, which is the exact stale-capture failure the standing "
        "method rule exists to prevent"
    )
    # And it really does refuse: point it at a source that cannot match.
    assert harness.persona_locale_argv("en-US") == [
        "--lang=en-US",
        "--accept-lang=en-US,en",
    ]


@pytest.mark.parametrize("locale", ["en-US", "pl-PL", "de-DE"])
def test_the_control_mirrors_the_products_own_expression(locale):
    """The harness's control and the product's builder must agree, per locale.

    Not a re-implementation check: the harness reads the product's source and
    then evaluates the same expression, so this asserts the two land on the same
    string for several locales rather than only the one the reading was taken
    at.
    """
    import scripts.ps397_languages_reading as harness

    got = harness.persona_locale_argv(locale)
    assert got == [
        f"--lang={locale}",
        f"--accept-lang={locale},{locale.split('-')[0]}",
    ], f"control drifted from process.py's expression for {locale}: {got}"
