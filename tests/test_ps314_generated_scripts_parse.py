"""PS-314 — EVERY generated script must PARSE, and every leaf is covered.

This file exists because PS-314's own review found `mobile.js` shipping a
**SyntaxError**. A `try {` was consumed while re-housing `patchedMM` into a
method shorthand, leaving a dangling `catch`, and both the android and the ios
arms produced a script node refuses to parse.

⚠️ THE BLAST RADIUS OF A PARSE ERROR IS TOTAL, NOT PARTIAL, and that is why this
guard is worth its own file. A syntax error is not a runtime error one of the
surrounding `try` blocks absorbs — the script never executes **at all**. A
mobile profile with an unparseable `mobile.js` reports no `navigator.platform`,
no `maxTouchPoints`, no `userAgentData`, no screen geometry and no `matchMedia`
override: it silently loses its entire spoof and reports the HOST's real values.
A ticket about masking *invisibility* had, for a while, shipped a *host-fact*
leak on the mobile branch.

WHY NOTHING CAUGHT IT — the third instance of one pattern on this ticket
--------------------------------------------------------------------------
Four test files build the mobile extension and they all passed against the
broken artifact, because every assertion was a **source-text substring check**:

    js = (p / "mobile.js").read_text(encoding="utf-8")
    assert "maxTouchPoints" in js

`"maxTouchPoints" in js` is true of a file that is 100% syntactically broken.
That is the same vacuity PS-314's AC10 removed from `test_ff_audio_seed.py`'s
allowlist, and the same one `test_ps314_native_wrapper_shape.py`'s docstring
names — *"a test asserting the generated source contains `({ m()` would pass
against a script that never ran"*. **A green that cannot go red, guarding the
exact thing that broke.**

So this file asserts the one property a substring check can never reach: that
the bytes we ship are a program. It is deliberately the CHEAPEST possible
check — `node --check`, no realm, no stubs, no evaluation — because its job is
breadth, not depth. Depth is
`test_ps314_native_wrapper_shape.py`'s, and the two are complementary: that
file proves a handful of wrappers have the right SHAPE, this one proves every
leaf is PARSEABLE at all.

⭐ IT COVERS EVERY LEAF, NOT THE ONE THAT BROKE. Fixing only `mobile_ext` would
leave the same trap armed on the other ten builders PS-314 rewrote.

⚠️ The builder list below IS hand-written, and an earlier revision of this
docstring claimed it was "derived by IMPORT rather than hand-listed" — which
was false, and false in the exact way this file exists to catch: an unenforced
promise that reads as a guarantee. It was complete on the day it was written
and nothing tied it to disk, so the next leaf added to
`src/services/browser/` would have been silently uncovered while this paragraph
went on claiming otherwise.

So the promise is now ENFORCED instead of asserted:
`test_the_builder_list_covers_every_shipped_builder` walks the package, finds
every `build_*_extension` callable, and fails naming any that `BUILDERS` omits.
The list stays hand-written — each builder needs real arguments, and inventing
them by signature inspection would be a second, subtler unenforced promise —
but it can no longer fall behind the tree without a test going red.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from src.services.browser.audio_ext import build_audio_extension
from src.services.browser.canvas_ctx_ext import build_canvas_ctx_extension
from src.services.browser.device_ext import build_device_extension
from src.services.browser.engine_version import ChromiumVersion
from src.services.browser.geo_ext import build_geo_extension
from src.services.browser.gpu_ext import build_gpu_extension
from src.services.browser.locale_ext import build_locale_extension
from src.services.browser.measuretext_ext import build_measuretext_extension
from src.services.browser.mobile_ext import build_mobile_extension
from src.services.browser.native_ext import build_native_extension
from src.services.browser.search_ext import build_search_extension
from src.services.browser.stealth_ext import build_stealth_extension
from src.services.browser.voice_ext import build_voice_extension
from src.services.browser.webgl_ext import build_webgl_extension

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is needed to parse the generated scripts"
)

_CHROMIUM = ChromiumVersion("144.0.7559.132")


# Every shipped builder, with one representative set of arguments. The point is
# to reach the generator, not to sweep its parameter space — a syntax error
# lives in the TEMPLATE and shows up on any input that renders it.
#
# `mobile` appears TWICE because its two arms take different template branches
# and the PS-314 break hit BOTH; a single arm would have looked like coverage.
BUILDERS: "list[tuple[str, object]]" = [
    ("audio", lambda d: build_audio_extension(4242, d)),
    ("canvas_ctx", lambda d: build_canvas_ctx_extension("windows", d)),
    ("device", lambda d: build_device_extension(4242, d, 0)),
    ("geo", lambda d: build_geo_extension(52.2297, 21.0122, d)),
    (
        "gpu",
        lambda d: build_gpu_extension(
            4242, "windows", d, 0, engine_platform="windows"
        ),
    ),
    ("locale", lambda d: build_locale_extension("en-US", d)),
    ("measuretext", lambda d: build_measuretext_extension(d)),
    (
        "mobile_android",
        lambda d: build_mobile_extension(
            d,
            is_ios=False,
            platform="Linux armv81",
            model="Pixel 7",
            chromium_version=_CHROMIUM,
            css_width=412,
            css_height=915,
            dpr=2.625,
            device_memory=8,
            hardware_concurrency=8,
        ),
    ),
    (
        "mobile_ios",
        lambda d: build_mobile_extension(
            d,
            is_ios=True,
            platform="iPhone",
            model="iPhone 15",
            chromium_version=None,
            css_width=393,
            css_height=852,
            dpr=3.0,
            device_memory=8,
            hardware_concurrency=6,
        ),
    ),
    ("native", lambda d: build_native_extension(d)),
    ("search", lambda d: build_search_extension("google", d)),
    ("stealth", lambda d: build_stealth_extension(d)),
    ("voice", lambda d: build_voice_extension("en-US", d)),
    ("webgl", lambda d: build_webgl_extension(4242, d)),
]

# Builders that legitimately emit NO JavaScript, with the reason. This is an
# ALLOWLIST and it is deliberately tiny: the default is "a leaf emits a script",
# and a leaf that stops emitting one is a fact worth failing over rather than
# skipping past. `search_ext` configures chromium's default search provider
# entirely through `manifest.json`'s `chrome_settings_overrides` — there is no
# content script to be malformed. Verified by building it: `manifest.json` is
# the only file produced.
NO_SCRIPT_BY_DESIGN = {
    "search": "configures the search provider through manifest.json alone",
}


def _parse(path: pathlib.Path) -> "tuple[int, str]":
    """`node --check` one file. Returns (returncode, stderr)."""
    proc = subprocess.run(
        [NODE, "--check", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    return proc.returncode, proc.stderr


@pytest.mark.parametrize("name,build", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_every_generated_script_is_parseable_javascript(name, build, tmp_path):
    """The bytes we ship are a PROGRAM, not merely a string containing keywords.

    A substring assertion cannot distinguish these two, which is how a
    SyntaxError reached `mobile.js` past four test files that all built it.
    """
    base = tmp_path / name
    base.mkdir()
    build(str(base))

    scripts = sorted(base.rglob("*.js"))
    # A builder that emitted NO script would pass a per-file loop vacuously —
    # the emptiness-rendered-as-success shape. So the absence has to be a
    # DECLARED fact rather than a silent one: a leaf that stops emitting a
    # script is a real regression, and only the allowlisted ones may be empty.
    if name in NO_SCRIPT_BY_DESIGN:
        assert not scripts, (
            f"{name} is allowlisted as script-less ("
            f"{NO_SCRIPT_BY_DESIGN[name]}) but it emitted {len(scripts)}. "
            "The allowlist is now wrong — remove the entry so the script is parsed."
        )
        return
    assert scripts, f"{name} generated no .js at all, so nothing was parsed"

    broken = []
    for js in scripts:
        rc, err = _parse(js)
        if rc != 0:
            first = next(
                (ln for ln in err.splitlines() if "Error" in ln), err.strip()[:200]
            )
            broken.append(f"{js.relative_to(base)}: {first}")

    assert not broken, (
        f"{name} generated JavaScript that does not PARSE, so the script never "
        f"runs at all and the profile silently reports the HOST's real values:\n  "
        + "\n  ".join(broken)
    )


def test_every_generated_manifest_is_parseable_json(tmp_path):
    """The same argument one file over.

    An unparseable manifest is not a degraded extension; chromium declines to
    load it, so the leaf is simply absent — the same total blast radius as a
    SyntaxError, reached a different way.
    """
    broken = []
    seen = 0
    for name, build in BUILDERS:
        base = tmp_path / name
        base.mkdir()
        build(str(base))
        for man in sorted(base.rglob("manifest.json")):
            seen += 1
            try:
                json.loads(man.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                broken.append(f"{name}/{man.relative_to(base)}: {exc}")

    assert seen, "no manifest was parsed, so this asserted nothing"
    assert not broken, "generated manifests that do not parse:\n  " + "\n  ".join(
        broken
    )


def test_the_parse_gate_can_actually_fail(tmp_path):
    """The guard's own negative control.

    A parse gate that cannot go red is worth exactly as much as the substring
    assertions it replaces, so it is falsified here rather than trusted. This
    reproduces the SHAPE of the real defect — a dangling `catch` left behind
    when a `try {` is consumed — and asserts `_parse` rejects it.
    """
    good = tmp_path / "good.js"
    good.write_text("try { var x = 1; } catch (e) {}\n", encoding="utf-8")
    rc, _ = _parse(good)
    assert rc == 0, "the control file should parse; the gate is misconfigured"

    bad = tmp_path / "bad.js"
    bad.write_text(
        "try { var x = 1; } catch (e) {}\n  var y = 2;\n} catch (e) {}\n",
        encoding="utf-8",
    )
    rc, err = _parse(bad)
    assert rc != 0, "a dangling catch must be REJECTED — the gate cannot fail"
    assert "SyntaxError" in err


def test_the_builder_list_covers_every_shipped_builder():
    """`BUILDERS` must not fall behind the tree.

    This is the enforcement behind the docstring's coverage claim. The list
    above is hand-written — every builder needs real arguments and inventing
    them from a signature would be its own unenforced promise — so the risk is
    that someone adds a leaf and nobody adds it here. That failure is silent:
    the parse gate would keep passing while covering one builder fewer than it
    says it does.

    An earlier revision of this file claimed the list was "derived by IMPORT",
    which was simply untrue. Rather than soften the claim, this test makes it
    hold: walk `src/services/browser/`, collect every public
    `build_*_extension` callable, and fail naming whatever `BUILDERS` omits.
    """
    import importlib
    import pkgutil

    import src.services.browser as browser_pkg

    shipped: set[str] = set()
    for mod in pkgutil.iter_modules(browser_pkg.__path__):
        if not mod.name.endswith("_ext"):
            continue
        m = importlib.import_module(f"src.services.browser.{mod.name}")
        for attr in dir(m):
            if attr.startswith("build_") and attr.endswith("_extension"):
                if callable(getattr(m, attr)):
                    shipped.add(attr)

    # Guard the guard: if the walk finds nothing, this test would pass
    # vacuously while asserting nothing at all.
    assert len(shipped) >= 10, (
        f"only {len(shipped)} builders discovered — the package walk is broken, "
        f"so this test is not actually checking coverage"
    )

    covered = set()
    for name, build in BUILDERS:
        # The lambdas close over exactly one builder each; recover it by name.
        covered.update(fn for fn in shipped if fn in build.__code__.co_names)

    missing = sorted(shipped - covered)
    assert not missing, (
        "these shipped builders emit scripts that NOTHING in this file parses:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd a BUILDERS row for each (with real arguments), or — if it "
        "legitimately emits no JavaScript — a NO_SCRIPT_BY_DESIGN entry stating "
        "why."
    )
