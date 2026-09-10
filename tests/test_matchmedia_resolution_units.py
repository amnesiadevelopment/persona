"""The resolution media feature must answer the SAME in every equivalent unit.

THE DEFECT
──────────
With the extension loaded, `devicePixelRatio` reported 1 and
`matchMedia("(resolution: 1dppx)")` MATCHED, while
`matchMedia("(resolution: 96dpi)")` DID NOT — and no other dpi value matched
either. Those are the same query in two units. The bare engine, same flags, is
coherent on both forms. A page that answers a question in one unit and refuses
the identical question in another unit is in a state no real browser is in: it
is a positive persona identification available to any script in two lines, and
it needs no baseline and no host knowledge to detect.

⭐ THE CAUSE IS STRUCTURAL, AND IT IS WHY THIS FILE TESTS MORE THAN THE UNITS.
The old interception substring-tested the query
(`/resolution|dppx|device-pixel-ratio/.test(q)`) and then overwrote the WHOLE
`matches` result. That does not implement a subset of the media query language
— it ASSERTS CONTRADICTIONS IN IT. Measured on the built extension at DPR 1,
all of these answered `true` simultaneously:

    (resolution: 1dppx)                                   true
    not all and (resolution: 1dppx)                       true   <- Q and NOT-Q
    print and (resolution: 1dppx)                         true   <- in a screen realm
    (min-resolution: 1dppx) and (max-resolution: 0.5dppx) true   <- a contradiction
    (resolution: 1dppx) and (min-width: 999999px)         true

⛔ SO WIDENING THE PATTERN WOULD NOT HAVE BEEN A FIX. Covering dpi/dpcm/x/min-/
max- closes the unit gap and leaves every line above standing, because those
are caused by the test-then-override STRUCTURE and not by the pattern. The
remedy is to PARSE: evaluate the resolution feature, DELEGATE every other
feature and the media type to the real matchMedia, and — the load-bearing part
— override NOTHING that is not fully understood, so an unrecognised query
degrades to the engine's own honest answer instead of a fabricated one.

⛔ THE ORACLE IS A REAL ENGINE, NOT THIS FILE'S OPINION
──────────────────────────────────────────────────────
Every expected value below was MEASURED against stock Chromium
152.0.7977.82 (headless, `--force-device-scale-factor` to set the arm's dpr)
before it was written down. Three of those measurements contradicted the
reading a source-level argument would have produced, and each is pinned by a
test here so a future edit cannot quietly "tidy" it back:

  1. `1x` IS VALID and equals 1dppx. It is the unitless alias, it serializes
     back as `(resolution: 1x)`, and it must NOT be treated as a bare number.
  2. `dpcm` COMPARES OVER A BAND WHERE dpi AND dppx COMPARE EXACTLY. At dpr 1:
     37.607dpcm..37.982dpcm answer TRUE while 37.605 and 37.985 answer FALSE,
     yet 96.001dpi and 1.0001dppx both answer FALSE. Comparing dpcm exactly
     would introduce a NEW cross-unit contradiction — precisely the class of
     defect this file exists to remove.
  3. UNPREFIXED `(device-pixel-ratio: 1)` IS FALSE on this engine (unsupported;
     the `<ratio>` form `1/1` too), while `-webkit-device-pixel-ratio` is TRUE.
     The OLD code answered TRUE for the unprefixed form — a wrong-TRUE leak in
     its own right. Delegation gets this right for free.

⭐ AND THE POSITIVE CONTROL IS MANDATORY. A test that cannot fail is not a
test. `test_the_dpi_branch_is_load_bearing` deliberately removes the dpi
conversion from the built script and REQUIRES the cross-unit assertion to go
red. Without it, every assertion here would pass against a build that answers
nothing.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.device_ext import build_device_extension

# ---------------------------------------------------------------------------
# The realm harness — the same shape PS-327/PS-352 established.
# ---------------------------------------------------------------------------

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  "globalThis.self = globalThis; globalThis.window = globalThis; globalThis.top = globalThis;",
  sandbox
);
vm.runInContext(cfg.stubs, sandbox, { filename: 'stubs.js' });
for (const p of cfg.scripts) {
  vm.runInContext(fs.readFileSync(p, 'utf8'), sandbox, { filename: p });
}
const out = vm.runInContext(cfg.probe, sandbox, { filename: 'probe.js' });
console.log(JSON.stringify({ result: out }));
"""

# ⭐ A FAITHFUL MediaQueryList, and the fidelity is load-bearing twice over.
#
# (1) `matches` lives on the PROTOTYPE and the instance owns NOTHING — measured
#     on real Chromium: `Object.getOwnPropertyNames(matchMedia(q))` is `[]`. A
#     stub that puts `matches` directly on the object would make the
#     own-property assertion below vacuous.
# (2) The stand-in engine answers the NON-resolution features only, and its
#     `devicePixelRatio` is the HOST's (1.5) rather than the profile's. So if
#     the patch ever delegates a resolution query it does not understand, the
#     host's scale leaks into the answer and the test SEES it.
_STUBS = """
globalThis.devicePixelRatio = 1.5;
globalThis.innerWidth = 1280; globalThis.innerHeight = 577;
globalThis.outerWidth = 1280; globalThis.outerHeight = 680;
globalThis.screen = { width: 1280, height: 720, availWidth: 1280,
                      availHeight: 720, colorDepth: 24, pixelDepth: 24 };
globalThis.navigator = { userAgent: "Mozilla/5.0", hardwareConcurrency: 8 };
function MediaQueryList() {}
Object.defineProperty(MediaQueryList.prototype, 'matches',
  { get: function () { return this._m; }, configurable: true });
Object.defineProperty(MediaQueryList.prototype, 'media',
  { get: function () { return this._q; }, configurable: true });
MediaQueryList.prototype.addListener = function () {};
MediaQueryList.prototype.removeListener = function () {};
globalThis.MediaQueryList = MediaQueryList;
globalThis.__engineAnswer = function (q) {
  var s = String(q).trim().toLowerCase();
  var m = s.match(/^\\(min-width:\\s*(\\d+)px\\)$/); if (m) return 1280 >= +m[1];
  m = s.match(/^\\(max-width:\\s*(\\d+)px\\)$/); if (m) return 1280 <= +m[1];
  if (s === 'screen' || s === 'all') return true;
  if (s === 'print') return false;
  return false;
};
globalThis.matchMedia = function (q) {
  var o = new MediaQueryList(); o._q = q; o._m = globalThis.__engineAnswer(q);
  return o;
};
globalThis.document = { documentElement: {}, addEventListener: function () {} };
"""


def _ask(tmp_path, queries, *, os_type="windows", resolution=(1920, 1080),
         tag="a", mutate=None, extra_probe=""):
    """Ask a realm carrying the built extension for each query's `matches`."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    work = pathlib.Path(tmp_path) / f"r{tag}"
    work.mkdir(parents=True, exist_ok=True)
    ext = build_device_extension(
        12345, str(work / "dev"), 0, resolution=resolution, os_type=os_type
    )
    script = pathlib.Path(ext) / "device.js"
    if mutate is not None:
        script.write_text(mutate(script.read_text(encoding="utf-8")), encoding="utf-8")
    probe = (
        "var qs = %s; var o = {};"
        "qs.forEach(function (q) { o[q] = matchMedia(q).matches; });"
        "%s JSON.stringify(o)" % (json.dumps(list(queries)), extra_probe)
    )
    (work / "harness.js").write_text(_HARNESS, encoding="utf-8")
    (work / "cfg.json").write_text(
        json.dumps({"stubs": _STUBS, "scripts": [str(script)], "probe": probe}),
        encoding="utf-8",
    )
    out = subprocess.run(
        [node, str(work / "harness.js"), str(work / "cfg.json")],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(json.loads(out.stdout)["result"])


# ---------------------------------------------------------------------------
# ⛔ THE HEADLINE: the same question in two units must get the same answer.
# ---------------------------------------------------------------------------

# Every equivalent spelling of "this display is exactly 1x", and of "exactly 2x".
# dpi = dppx * 96; dpcm = dppx * 96 / 2.54.
_EQUIVALENT = {
    1: ["(resolution: 1dppx)", "(resolution: 96dpi)", "(resolution: 1x)",
        "(resolution: 37.795dpcm)", "(resolution: 1.0dppx)"],
    2: ["(resolution: 2dppx)", "(resolution: 192dpi)", "(resolution: 2x)",
        "(resolution: 75.591dpcm)", "(resolution: 2.0dppx)"],
}


@pytest.mark.parametrize(
    "os_type,resolution,dpr,tag",
    [("windows", (1920, 1080), 1, "win"), ("macos", (1728, 1117), 2, "mac")],
)
def test_every_equivalent_unit_agrees_with_the_dppx_form(
    tmp_path, os_type, resolution, dpr, tag
):
    """⛔ THE NON-WAIVABLE ASSERTION, and the reason this ticket exists.

    For the profile's OWN dpr every spelling must answer true; for the other
    dpr every spelling must answer false. A single disagreement inside either
    row is the defect — a page answering a question in one unit and refusing
    the identical question in another.
    """
    all_q = _EQUIVALENT[1] + _EQUIVALENT[2]
    got = _ask(tmp_path, all_q, os_type=os_type, resolution=resolution, tag=tag)

    for value, queries in _EQUIVALENT.items():
        want = value == dpr
        answers = {q: got[q] for q in queries}
        assert set(answers.values()) == {want}, (
            f"the {value}x row must answer {want} in EVERY equivalent unit on a "
            f"DPR-{dpr} profile — a split row is the cross-unit incoherence "
            f"this file exists to remove.\n"
            + "\n".join(f"    {v!s:5}  {q}" for q, v in answers.items())
        )


def test_the_dpi_branch_is_load_bearing(tmp_path):
    """⭐ THE POSITIVE CONTROL. A guard that cannot go red is not a guard.

    Remove the dpi conversion from the built script and the cross-unit
    assertion above MUST fail. If this test ever passes without the removal
    doing anything, the assertion above is measuring nothing.
    """
    def drop_dpi(js):
        # The conversion table is the single point the dpi form flows through.
        assert "dpi: 1 / 96" in js, (
            "the dpi conversion factor is no longer spelled 'dpi: 1 / 96' — "
            "this control must be re-anchored to whatever replaced it, NOT "
            "deleted, or the cross-unit test loses its falsifier"
        )
        return js.replace("dpi: 1 / 96,", "dpi: undefined,")

    def _guarded(js):
        out = drop_dpi(js)
        assert out != js, "the dpi mutation was a no-op — this control is inert"
        return out

    got = _ask(tmp_path, _EQUIVALENT[1], tag="nodpi", mutate=_guarded)
    assert got["(resolution: 96dpi)"] is False, (
        "with the dpi branch removed the dpi form MUST stop matching — it "
        "still answered true, so the cross-unit test is not actually "
        "exercising the dpi conversion and would pass against a broken build"
    )
    assert got["(resolution: 1dppx)"] is True, (
        "removing the dpi branch must leave the dppx form alone; if this "
        "moved, the mutation is too blunt to isolate the dpi conversion"
    )


def test_the_dpcm_branch_is_load_bearing(tmp_path):
    """The same control for dpcm — a second unit, a second falsifier."""
    def drop_dpcm(js):
        assert "dpcm: 2.54 / 96" in js, (
            "the dpcm conversion factor moved; re-anchor this control"
        )
        # ⚠️ NOTE THE ABSENT TRAILING COMMA — dpcm is the LAST key in RESU, and
        # an earlier version of this control replaced "dpcm: 2.54 / 96," which
        # matched NOTHING, silently mutating nothing and testing nothing. The
        # assertion below is what caught it. Keep the two forms distinct.
        out = js.replace("dpcm: 2.54 / 96", "dpcm: undefined")
        assert out != js, "the dpcm mutation was a no-op — this control is inert"
        return out

    got = _ask(tmp_path, _EQUIVALENT[1], tag="nodpcm", mutate=drop_dpcm)
    assert got["(resolution: 37.795dpcm)"] is False
    assert got["(resolution: 96dpi)"] is True


# ---------------------------------------------------------------------------
# min-/max- prefixes, in both units.
# ---------------------------------------------------------------------------


def test_min_and_max_resolution_answer_in_dpi_as_they_do_in_dppx(tmp_path):
    """The prefixed forms were BOTH broken, and not only in dpi.

    `(min-resolution: 96dpi)` answered false (a unit gap), and so did
    `(min-resolution: 0.5dppx)` (a VALUE gap — the old regex tested equality
    against the pinned DPR, so any bound naming a different number missed).
    Both are asserted here, in both units, so neither can regress alone.
    """
    pairs = [
        ("(min-resolution: 96dpi)", "(min-resolution: 1dppx)", True),
        ("(max-resolution: 96dpi)", "(max-resolution: 1dppx)", True),
        ("(min-resolution: 48dpi)", "(min-resolution: 0.5dppx)", True),
        ("(max-resolution: 192dpi)", "(max-resolution: 2dppx)", True),
        ("(min-resolution: 192dpi)", "(min-resolution: 2dppx)", False),
        ("(max-resolution: 48dpi)", "(max-resolution: 0.5dppx)", False),
    ]
    got = _ask(tmp_path, [q for a, b, _ in pairs for q in (a, b)], tag="minmax")
    for dpi_q, dppx_q, want in pairs:
        assert got[dpi_q] == got[dppx_q] == want, (
            f"a DPR-1 profile must answer {want} for BOTH forms of the same "
            f"bound.\n    {got[dpi_q]!s:5}  {dpi_q}\n    {got[dppx_q]!s:5}  {dppx_q}"
        )


def test_the_webkit_device_pixel_ratio_family_agrees_with_the_resolution_family(
    tmp_path,
):
    """`-webkit-device-pixel-ratio` is the same question in a third spelling.

    ⚠️ The UNPREFIXED `(device-pixel-ratio: 1)` is deliberately NOT asserted
    true here: stock Chromium 152.0.7977.82 answers FALSE for it (unsupported,
    as it does for the `<ratio>` form `1/1`). The old code answered TRUE — a
    wrong-TRUE leak of its own. See the dedicated test below.
    """
    got = _ask(tmp_path, [
        "(-webkit-device-pixel-ratio: 1)", "(-webkit-device-pixel-ratio: 2)",
        "(-webkit-min-device-pixel-ratio: 0.5)", "(-webkit-max-device-pixel-ratio: 2)",
        "(-webkit-min-device-pixel-ratio: 2)", "(-webkit-max-device-pixel-ratio: 0.5)",
    ], tag="wk")
    assert got["(-webkit-device-pixel-ratio: 1)"] is True
    assert got["(-webkit-device-pixel-ratio: 2)"] is False
    assert got["(-webkit-min-device-pixel-ratio: 0.5)"] is True
    assert got["(-webkit-max-device-pixel-ratio: 2)"] is True
    assert got["(-webkit-min-device-pixel-ratio: 2)"] is False
    assert got["(-webkit-max-device-pixel-ratio: 0.5)"] is False


def test_the_unprefixed_device_pixel_ratio_is_not_answered_true(tmp_path):
    """⛔ A MEASURED ENGINE BEHAVIOUR THAT CONTRADICTS THE OBVIOUS READING.

    `(device-pixel-ratio: 1)` looks like it should be true at dpr 1. On stock
    Chromium 152.0.7977.82 it is FALSE — the unprefixed feature is not
    supported, and neither is the `<ratio>` form. The OLD code answered true
    for it, which is a leak in the opposite direction from the unit gap: a
    query a real browser refuses, which persona affirmed.

    This is pinned because the natural "tidy-up" is to make this consistent
    with the -webkit- form, and that would REINTRODUCE the tell.
    """
    got = _ask(tmp_path, [
        "(device-pixel-ratio: 1)", "(device-pixel-ratio: 1/1)",
        "(min-device-pixel-ratio: 0.5)", "(max-device-pixel-ratio: 2)",
    ], tag="unpre")
    for q, v in got.items():
        assert v is False, (
            f"{q} must answer false — stock Chromium 152 does not support the "
            f"unprefixed feature, and answering true is a positive tell"
        )


# ---------------------------------------------------------------------------
# ⛔ The class the brief did not name: a query that contradicts itself.
# ---------------------------------------------------------------------------


def test_a_query_and_its_negation_cannot_both_match(tmp_path):
    """⭐ THE STRONGEST TELL ON THIS SURFACE, and the cheapest to probe.

    A dpi/dppx disagreement requires the prober to know 1dppx == 96dpi. `Q`
    and `not Q` both answering true requires no unit table, no baseline and no
    host knowledge at all. The old code answered true to both.
    """
    got = _ask(tmp_path, [
        "(resolution: 1dppx)", "not all and (resolution: 1dppx)",
        "(resolution: 2dppx)", "not all and (resolution: 2dppx)",
    ], tag="neg")
    for q in ("(resolution: 1dppx)", "(resolution: 2dppx)"):
        neg = "not all and " + q
        assert got[q] != got[neg], (
            f"a query and its negation MUST disagree; both answered "
            f"{got[q]}.\n    {q}\n    {neg}"
        )


def test_a_self_contradicting_conjunction_is_false(tmp_path):
    """`(min-resolution: 1dppx) and (max-resolution: 0.5dppx)` describes no
    display that can exist. The old code answered true."""
    q = "(min-resolution: 1dppx) and (max-resolution: 0.5dppx)"
    assert _ask(tmp_path, [q], tag="contra")[q] is False


def test_a_non_matching_media_type_is_not_overridden_to_true(tmp_path):
    """`print and (resolution: 1dppx)` in a screen realm is false.

    The media TYPE is delegated to the real matchMedia rather than decided
    here — this code has no business ruling on whether a realm is `print`.
    """
    got = _ask(tmp_path, [
        "print and (resolution: 1dppx)",
        "screen and (resolution: 96dpi)",
        "only screen and (resolution: 96dpi)",
    ], tag="mtype")
    assert got["print and (resolution: 1dppx)"] is False
    assert got["screen and (resolution: 96dpi)"] is True
    assert got["only screen and (resolution: 96dpi)"] is True


def test_a_second_term_is_honoured_rather_than_discarded(tmp_path):
    """A resolution term ANDed with an unrelated false term must be false.

    This is the term-delegation working: the second term is handed to the real
    matchMedia and AND'd in, instead of being overwritten by the resolution
    answer. The old code discarded it.
    """
    got = _ask(tmp_path, [
        "(resolution: 1dppx) and (min-width: 999999px)",
        "(resolution: 1dppx) and (min-width: 1px)",
    ], tag="and")
    assert got["(resolution: 1dppx) and (min-width: 999999px)"] is False
    assert got["(resolution: 1dppx) and (min-width: 1px)"] is True


def test_a_comma_list_is_a_disjunction(tmp_path):
    """A comma list matches when ANY branch matches, including a branch this
    code does not own (which is delegated on its own)."""
    got = _ask(tmp_path, [
        "(resolution: 96dpi), (min-width: 999999px)",
        "(resolution: 2dppx), (min-width: 1px)",
        "(resolution: 2dppx), (min-width: 999999px)",
    ], tag="comma")
    assert got["(resolution: 96dpi), (min-width: 999999px)"] is True
    assert got["(resolution: 2dppx), (min-width: 1px)"] is True
    assert got["(resolution: 2dppx), (min-width: 999999px)"] is False


# ---------------------------------------------------------------------------
# Measured engine quirks that a source-level argument would get wrong.
# ---------------------------------------------------------------------------


def test_the_unitless_x_alias_is_a_resolution_not_a_bare_number(tmp_path):
    """⛔ MEASURED: `(resolution: 1x)` is VALID and true at dpr 1 on stock
    Chromium 152.0.7977.82, serializing back as `(resolution: 1x)`.

    A bare number is NOT valid — `(resolution: 96)` is false. Both are pinned,
    because treating `x` as "no unit" would break the first and treating a
    bare number as dppx would break the second.
    """
    got = _ask(tmp_path, [
        "(resolution: 1x)", "(resolution: 2x)", "(resolution: 96)",
    ], tag="xalias")
    assert got["(resolution: 1x)"] is True
    assert got["(resolution: 2x)"] is False
    assert got["(resolution: 96)"] is False


def test_dpcm_matches_over_the_band_the_engine_uses(tmp_path):
    """⛔ MEASURED, AND IT CONTRADICTS THE OBVIOUS IMPLEMENTATION.

    dpi and dppx compare EXACTLY (96.001dpi and 1.0001dppx are both false),
    but dpcm compares over a BAND: at dpr 1, 37.607dpcm and 37.982dpcm answer
    TRUE while 37.605 and 37.985 answer FALSE.

    Comparing dpcm exactly — the obvious implementation — would make
    `(resolution: 37.795dpcm)` disagree with `(resolution: 96dpi)`, i.e. it
    would introduce a NEW cross-unit contradiction of exactly the kind this
    file exists to remove. The tolerance is therefore deliberate.
    """
    got = _ask(tmp_path, [
        "(resolution: 37.795dpcm)", "(resolution: 37.7952755905dpcm)",
        "(resolution: 37.8dpcm)", "(resolution: 37.65dpcm)", "(resolution: 37.95dpcm)",
        "(resolution: 40dpcm)", "(resolution: 35dpcm)",
        "(resolution: 96.001dpi)", "(resolution: 95.999dpi)",
        "(resolution: 1.0001dppx)", "(resolution: 0.999dppx)",
    ], tag="dpcm")
    for q in ("(resolution: 37.795dpcm)", "(resolution: 37.7952755905dpcm)",
              "(resolution: 37.8dpcm)", "(resolution: 37.65dpcm)",
              "(resolution: 37.95dpcm)"):
        assert got[q] is True, f"{q} is inside the engine's dpcm band and must match"
    for q in ("(resolution: 40dpcm)", "(resolution: 35dpcm)"):
        assert got[q] is False, f"{q} is outside the band and must not match"
    for q in ("(resolution: 96.001dpi)", "(resolution: 95.999dpi)",
              "(resolution: 1.0001dppx)", "(resolution: 0.999dppx)"):
        assert got[q] is False, (
            f"{q} must NOT match — dpi and dppx compare exactly, and widening "
            f"their tolerance to match dpcm's would be the wrong repair"
        )


def test_the_dpcm_band_edges_match_the_engines_measured_edges(tmp_path):
    """⛔ THE BAND IS NOT APPROXIMATELY RIGHT, IT IS EDGE-FOR-EDGE RIGHT.

    Measured on stock Chromium 152.0.7977.82 at dpr 1, the true/false
    transition sits BETWEEN these pairs:

        37.605dpcm  false        37.982dpcm  true
        37.607dpcm  true         37.985dpcm  false

    A tolerance that merely "looks about right" would put its own edge
    somewhere else in that interval and produce a NEW disagreement with the
    engine at the boundary — which is the same class of tell as the original
    unit gap, just narrower. Both edges are pinned so a future adjustment of
    the constant cannot silently move them.
    """
    got = _ask(tmp_path, [
        "(resolution: 37.605dpcm)", "(resolution: 37.607dpcm)",
        "(resolution: 37.982dpcm)", "(resolution: 37.985dpcm)",
    ], tag="band")
    assert got["(resolution: 37.605dpcm)"] is False, "below the engine's lower edge"
    assert got["(resolution: 37.607dpcm)"] is True, "above the engine's lower edge"
    assert got["(resolution: 37.982dpcm)"] is True, "below the engine's upper edge"
    assert got["(resolution: 37.985dpcm)"] is False, "above the engine's upper edge"


def test_syntax_variants_reach_the_parser(tmp_path):
    """Whitespace, case, sign, exponent and Chromium's tolerance of an
    unclosed feature must all reach the same answer.

    ⚠️ `(resolution: 96dpi` — no closing paren — genuinely parses on stock
    Chromium and answers true. It is asserted here because a parser that let
    it fall through would DELEGATE it, and the stand-in engine's dpr is the
    HOST's 1.5 — so the leak would be silent.
    """
    got = _ask(tmp_path, [
        "(resolution:96dpi)", "( resolution : 96dpi )", "(RESOLUTION: 96DPI)",
        "(resolution: 1DPPX)", "(resolution: +1dppx)", "(resolution: 1e0dppx)",
        "(resolution: 1.00dppx)", "(resolution: 96dpi",
    ], tag="syn")
    for q, v in got.items():
        assert v is True, f"{q} must reach the parser and answer true at dpr 1"


def test_an_invalid_resolution_value_is_false_not_true(tmp_path):
    """Zero, negative and non-numeric resolutions are invalid; an invalid
    media query is false. The failure to avoid is answering TRUE because the
    query merely CONTAINS the word 'resolution'."""
    got = _ask(tmp_path, [
        "(resolution: 0dppx)", "(resolution: -1dppx)", "(resolution: abc)",
    ], tag="invalid")
    for q, v in got.items():
        assert v is False, f"{q} is invalid and must answer false, not true"


# ---------------------------------------------------------------------------
# ⭐ The cloak: do not install an own property where none is needed.
# ---------------------------------------------------------------------------


def test_an_agreeing_query_leaves_the_prototype_position_intact(tmp_path):
    """⭐ NATIVELY `matches` LIVES ON MediaQueryList.prototype AND THE INSTANCE
    OWNS NOTHING — measured on real Chromium:
    `Object.getOwnPropertyNames(matchMedia(q))` is `[]`.

    So every own `matches` installed on a MediaQueryList is itself a POSITION
    tell, independent of how well the descriptor is cloaked. The old code
    installed one on EVERY query containing the word 'resolution', including
    the ones where it agreed with the engine anyway.

    This asserts the override is written ONLY where the engine's answer is
    actually wrong — the count of own properties must not grow on a query the
    patch does not need to correct.
    """
    got = _ask(tmp_path, ["(min-width: 100px)"], tag="pos", extra_probe=(
        "o['__own_plain'] = Object.getOwnPropertyNames("
        "  matchMedia('(min-width: 100px)')).indexOf('matches') >= 0;"
        "o['__own_agree'] = Object.getOwnPropertyNames("
        "  matchMedia('(resolution: 2dppx)')).indexOf('matches') >= 0;"
        "o['__own_correct'] = Object.getOwnPropertyNames("
        "  matchMedia('(resolution: 96dpi)')).indexOf('matches') >= 0;"
    ))
    assert got["__own_plain"] is False, (
        "an untouched query must not acquire an own `matches`"
    )
    assert got["__own_agree"] is False, (
        "a resolution query the patch AGREES with the engine on must not "
        "acquire an own `matches` — natively the instance owns nothing, so an "
        "unnecessary own property is a position tell in its own right"
    )
    assert got["__own_correct"] is True, (
        "the query the patch must actually correct is expected to carry the "
        "override; if this is false the patch is not doing anything"
    )


def test_an_unrelated_query_is_not_touched(tmp_path):
    """THE QUIET ARM. A query with nothing to do with resolution must answer
    exactly what the engine answered — the patch must be invisible on it."""
    got = _ask(tmp_path, [
        "(min-width: 100px)", "(max-width: 999999px)", "(min-width: 999999px)",
        "screen", "print", "garbage", "(bogus: 1)",
    ], tag="quiet")
    assert got["(min-width: 100px)"] is True
    assert got["(max-width: 999999px)"] is True
    assert got["(min-width: 999999px)"] is False
    assert got["screen"] is True
    assert got["print"] is False
    assert got["garbage"] is False
    assert got["(bogus: 1)"] is False


def test_the_host_dpr_never_leaks_through_any_form(tmp_path):
    """⛔ THE LEAK THIS WHOLE SURFACE EXISTS TO PREVENT.

    The harness realm's own `devicePixelRatio` is 1.5 and its stand-in engine
    answers resolution queries from that host value. A DPR-1 profile must
    answer FALSE to every spelling of 1.5x — if any form is delegated instead
    of parsed, the host's real scale shows through.
    """
    got = _ask(tmp_path, [
        "(resolution: 1.5dppx)", "(resolution: 144dpi)", "(resolution: 1.5x)",
        "(resolution: 56.693dpcm)", "(-webkit-device-pixel-ratio: 1.5)",
        "(min-resolution: 1.5dppx)", "(min-resolution: 144dpi)",
    ], tag="leak")
    for q, v in got.items():
        assert v is False, (
            f"{q} answered true on a DPR-1 profile — the HOST's 1.5 scale is "
            f"leaking through this form instead of being answered from the "
            f"pinned DPR"
        )
