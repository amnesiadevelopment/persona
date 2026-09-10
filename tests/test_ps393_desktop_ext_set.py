"""Pin the DESKTOP extension set against what the ENGINE actually reports.

PS-393. The measured defect, from two directions that AGREE:

  * runtime, OWNER'S WINDOWS HOST, real GPU, engine-only vs engine+extensions:
    engine 35 entries, persona 27 — EIGHT MISSING, shorter rather than renamed
    or reordered.
  * in-tree corpus, `readings/ps161-coherence-2026-08-25/`, GPU-LESS VM, layer
    off vs layer on: engine 36 (webgl1), persona 27 — NINE missing.

⭐ THE LIST IS REPLACED, NOT FILTERED, and that is why a stale entry costs
exactly as much as a wrong one. `gpu_ext.py`'s installer returns
`extList.slice()` — the whole hardcoded array substituted for the real one — so
anything absent from the literal is absent from the page, whatever the host
would have reported.

⛔ WHY A SHORTER LIST IS A TELL ON ITS OWN TERMS. A detector does not need to
know the right extension set for a GPU; it needs to know that this GPU/driver
combination reports N and this browser claims fewer. So this vector fails
independently of the vendor/renderer strings being right — which on the windows
arm they already are, because the ENGINE authors them there
(`ENGINE_AUTHORED_IDENTITY_ARMS`).

THE NINE SPLIT INTO TWO CLASSES NEEDING OPPOSITE TREATMENT, and this file pins
both halves, because pinning only the addition would let a future edit "finish
the job" by adding the three that must stay out:

  * SIX MODERN DESKTOP EXTENSIONS missing on BOTH hosts — the 2023-08-08 batch
    plus `EXT_sRGB`. Genuinely exposed by real Chrome on ANGLE/D3D11, and this
    module ALREADY SHIPS ALL SIX in `IOS_GL1_EXTS`. `DESKTOP_EXTS` predates them
    and was never updated alongside the iOS lists: STALE, not deliberate.
  * THREE MOBILE GLES FAMILIES (astc/etc/etc1) — the VM reported all three
    because SwiftShader advertises everything, the software-rasteriser signature
    this module treats as a hard cross-check failure on a claimed D3D11 card.
    They stay ABSENT. The two hosts differ by exactly one entry, so a real
    D3D11 card reports SOME of that family and not all of it, and nothing
    available to this seat says which. Guessing would re-create the
    "supports everything" contradiction (audit7 #3) to close a one-entry gap.

⚠️ SO THE EXPECTED POST-FIX READING IS 33 AGAINST THE ENGINE'S 35, NOT 35. The
two-entry residual is deliberate and is pinned below as a residual rather than
left to look like an incomplete fix.

⚠️ WHAT THIS FILE DOES NOT TEST, because no browser was launched: that the page
actually reads 33. That is the owner's measurement. These tests pin the emitted
literal and its relationship to the committed engine corpus.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from src.services.browser.engine_platform import engine_platform_for
from src.services.browser.gpu_ext import build_gpu_extension

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GPU_EXT_SOURCE = REPO_ROOT / "src" / "services" / "browser" / "gpu_ext.py"

#: The engine-vs-persona coherence corpus. Taken on a GPU-LESS VM, which is what
#: makes its mobile-GLES rows the software-rasteriser signature rather than a
#: real card's set — see the module docstring.
CORPUS = (
    REPO_ROOT
    / "readings"
    / "ps161-coherence-2026-08-25"
    / "engine-gpu-identity.json"
)

#: The six that were STALE: missing on both hosts, real on ANGLE/D3D11, and
#: already shipped by this module's own iOS lists.
MODERN_DESKTOP = (
    "EXT_clip_control",
    "EXT_depth_clamp",
    "EXT_polygon_offset_clamp",
    "EXT_sRGB",
    "EXT_texture_mirror_clamp_to_edge",
    "WEBGL_polygon_mode",
)

#: The three that must STAY OUT. Not an oversight — see the module docstring.
MOBILE_GLES = (
    "WEBGL_compressed_texture_astc",
    "WEBGL_compressed_texture_etc",
    "WEBGL_compressed_texture_etc1",
)

#: What the page is expected to read after this fix, and what the engine reads.
#: Stated as constants so the PR's falsifiable claim and the test are one value.
EXPECTED_PERSONA_COUNT = 33
ENGINE_COUNT_REAL_HARDWARE = 35
ENGINE_COUNT_GPULESS_VM = 36

#: ⛔ THE SIXTEEN WEBGL1 EXTENSIONS THAT WEBGL2 PROMOTED INTO CORE.
#:
#: A conforming WebGL2 context CANNOT list any of them — the functionality is in
#: the core API, so there is no extension object left to hand out. That makes
#: this a CATEGORY error rather than a MAGNITUDE one: a wrong count needs a
#: baseline to detect, while `webgl2` reporting `ANGLE_instanced_arrays` is a
#: one-line positive identification on any hardware.
#:
#: ⭐ NOT TRANSCRIBED FROM A REVIEW COMMENT — `test_the_promoted_set_is_the_same_
#: sixteen_from_two_independent_sources` re-derives this tuple from the corpus
#: AND from this module's own iOS pair, which are different hosts with different
#: totals (36/30 vs a WebKit device reading). Both produce exactly these sixteen,
#: because the set is a property of the WebGL2 spec rather than of a GPU.
PROMOTED_TO_WEBGL2_CORE = (
    "ANGLE_instanced_arrays",
    "EXT_blend_minmax",
    "EXT_disjoint_timer_query",
    "EXT_frag_depth",
    "EXT_sRGB",
    "EXT_shader_texture_lod",
    "OES_element_index_uint",
    "OES_fbo_render_mipmap",
    "OES_standard_derivatives",
    "OES_texture_float",
    "OES_texture_half_float",
    "OES_texture_half_float_linear",
    "OES_vertex_array_object",
    "WEBGL_color_buffer_float",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
)

#: The WebGL2-ONLY extensions the liaison measured on the owner's real Windows
#: host. ⛔ These appear in NO WebGL1 list and cannot be derived from one: the
#: GL1 array minus the promoted sixteen is 17 entries where the engine reports
#: 32, and these are the other half.
#:
#: Two of them (`KHR_parallel_shader_compile`, `WEBGL_blend_func_extended`) are
#: also the WebGL1 arm's two stated residuals. That is NOT a contradiction —
#: the engine reports them on both contexts, and each list is derived from its
#: OWN reading. Carrying a GL1 omission onto GL2 would be deriving one list from
#: the other, which is the thing this file forbids.
WEBGL2_ONLY = (
    "EXT_color_buffer_float",
    "EXT_conservative_depth",
    "EXT_disjoint_timer_query_webgl2",
    "EXT_render_snorm",
    "EXT_texture_norm16",
    "KHR_parallel_shader_compile",
    "NV_shader_noperspective_interpolation",
    "OES_draw_buffers_indexed",
    "OES_sample_variables",
    "OES_shader_multisample_interpolation",
    "OVR_multiview2",
    "WEBGL_blend_func_extended",
    "WEBGL_clip_cull_distance",
    "WEBGL_provoking_vertex",
    "WEBGL_stencil_texturing",
)

#: The WebGL2 count this rework claims, stated as a constant for the same reason
#: EXPECTED_PERSONA_COUNT is: so the PR's falsifiable claim and the guard are one
#: value. It equals the engine's own measured WebGL2 count on the owner's host.
EXPECTED_PERSONA_GL2_COUNT = 32
ENGINE_GL2_COUNT_REAL_HARDWARE = 32
ENGINE_GL2_COUNT_GPULESS_VM = 30


def _literal(name: str) -> list[str]:
    """The JS array as WRITTEN in the source, order preserved.

    Read off the source rather than the rendered extension because ORDER is part
    of what is being pinned and a set comparison would not see a reordering.
    """
    src = GPU_EXT_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"var " + name + r" = \[(.*?)\];", src, re.S)
    assert match, f"{name} is no longer a JS array literal in gpu_ext.py"
    return re.findall(r'"([^"]+)"', match.group(1))


def _corpus_list(arm: str, layer: str, ctx: str) -> list[str]:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    for cell in data["cells"]:
        if cell["arm"] == arm and cell["masking_layer"] == layer:
            reading = cell["reading"][ctx]
            keys = [k for k in reading if "ext" in k.lower()]
            assert keys, f"no extension key in the {arm}/{layer}/{ctx} reading"
            value = reading[keys[0]]
            if isinstance(value, str):
                return [x.strip() for x in value.split(",")]
            return [str(x).strip() for x in value]
    raise AssertionError(f"no {arm}/{layer} cell in the corpus")


# --- the addition ----------------------------------------------------------


def test_the_six_stale_modern_extensions_are_now_advertised() -> None:
    """THE FIX. Each of the six is present, named individually.

    Named one by one rather than asserted as a count, so a future edit that
    drops one while adding something else does not pass.
    """
    desktop = _literal("DESKTOP_EXTS")
    for ext in MODERN_DESKTOP:
        assert ext in desktop, (
            f"{ext} is missing from DESKTOP_EXTS. The engine reports it on a "
            "real ANGLE/D3D11 host, and a list shorter than the engine's is a "
            "tell on its own terms — see this module's docstring."
        )


def test_the_count_is_the_number_the_PR_claims() -> None:
    """The falsifiable claim, as a constant rather than a sentence in a PR.

    If this number and the PR's number ever disagree, the PR is wrong — that is
    the point of pinning it here.
    """
    desktop = _literal("DESKTOP_EXTS")
    assert len(desktop) == EXPECTED_PERSONA_COUNT, (
        f"DESKTOP_EXTS has {len(desktop)} entries; this slice claims "
        f"{EXPECTED_PERSONA_COUNT}. Update the claim deliberately, in the PR and "
        "here, rather than letting the two drift."
    )


def test_the_six_were_already_shipped_by_this_modules_own_ios_list() -> None:
    """⭐ THE EVIDENCE THAT THIS WAS STALENESS, NOT A DECISION.

    Every one of the six is already in `IOS_GL1_EXTS`, whose own comment names
    the same 2023-08-08 WebKit batch. So the project had already accepted these
    as real and shipping; only the desktop list was left behind. That is what
    makes adding them a correction rather than a new claim about hardware.
    """
    ios_gl1 = set(_literal("IOS_GL1_EXTS"))
    for ext in MODERN_DESKTOP:
        assert ext in ios_gl1, (
            f"{ext} is NOT in IOS_GL1_EXTS, so the staleness argument for "
            "adding it to DESKTOP_EXTS does not hold for this entry — it would "
            "be a NEW claim about what real hardware reports, which needs its "
            "own measurement rather than this slice's reasoning."
        )


# --- the refusal, pinned as hard as the addition ---------------------------


def test_the_mobile_gles_families_stay_absent_from_the_desktop_set() -> None:
    """⛔ THE HALF A FUTURE EDIT WILL WANT TO "FINISH". DO NOT.

    Adding these would close the remaining two-entry gap against the engine and
    would re-create the "supports everything" software-rasteriser signature on a
    claimed Direct3D11 card — the mirror of audit7 #3, which this module already
    classifies as a hard renderer<->extension impossibility.

    The two hosts differ by exactly one entry (36 on the GPU-less VM, 35 on real
    hardware), so a real D3D11 card reports SOME of this family and not all of
    it. Nothing available to this seat says which, and a guess here buys one
    entry at the cost of a contradiction.
    """
    desktop = set(_literal("DESKTOP_EXTS"))
    for ext in MOBILE_GLES:
        assert ext not in desktop, (
            f"{ext} was added to DESKTOP_EXTS. A claimed Direct3D11 renderer "
            "advertising the mobile GLES compression families is the "
            "software-rasteriser signature this module calls a hard cross-check "
            "failure (audit7 #3). If a real-hardware measurement now says a "
            "D3D11 card reports it, cite that measurement here and delete this "
            "assertion deliberately."
        )


def test_the_residual_against_the_engine_is_exactly_the_mobile_families() -> None:
    """The gap that REMAINS is fully accounted for, and is only that.

    A residual nobody has enumerated is indistinguishable from an incomplete
    fix. This pins that everything still missing is the deliberate refusal above
    — so if a SEVENTH entry ever goes missing, this fails rather than hiding
    behind the known three.
    """
    desktop = set(_literal("DESKTOP_EXTS"))
    engine_gl1 = set(_corpus_list("windows", "off", "webgl1"))

    still_missing = engine_gl1 - desktop
    assert still_missing == set(MOBILE_GLES), (
        "the residual against the engine's webgl1 set is no longer exactly the "
        "three mobile GLES families:\n"
        f"  still missing: {sorted(still_missing)}\n"
        f"  expected:      {sorted(MOBILE_GLES)}\n"
        "Anything else here is an unaccounted gap — enumerate it or close it."
    )

    assert not (desktop - engine_gl1), (
        "DESKTOP_EXTS now advertises an extension the engine does NOT report on "
        f"its webgl1 context: {sorted(desktop - engine_gl1)}. Claiming an "
        "extension the engine cannot back is a tell in the other direction."
    )


def test_the_order_is_the_engines_own_order_not_a_tidied_one() -> None:
    """⚠️ ORDER IS PART OF THE VECTOR — pinned against the MEASURED engine order.

    A detector that hashes the raw array catches a reordering instantly, so our
    list must be the engine's sequence minus the refused three.

    ⛔ A CORRECTION TO THE REASONING, MEASURED RATHER THAN INHERITED. The iOS
    lists carry a genuine non-sorted artifact and their note explains it, and an
    earlier draft of this file reused that argument here — asserting that
    `EXT_sRGB` precedes `EXT_shader_texture_lod` as a deliberate deviation. On
    the DESKTOP arm that assertion is vacuous: the measured engine order is
    EXACTLY codepoint-sorted, and `EXT_sRGB` sorts before
    `EXT_shader_texture_lod` anyway (uppercase `R` 0x52 < lowercase `h` 0x68), so
    a `sorted()` tidy leaves that pair untouched. A mutation that alphabetised
    the whole list therefore PASSED the artifact check — measured, which is how
    this was caught. The iOS artifact is real for WebKit's hand-written macro
    sequence; it is not a property of this list, and reasoning transplanted from
    one to the other is how a guard stops discriminating.

    So order is pinned the only way that actually binds: the exact measured
    engine sequence, filtered to what we advertise. That is sensitive to ANY
    reordering, including one that happens to agree with a sort.
    """
    desktop = _literal("DESKTOP_EXTS")
    engine_gl1 = _corpus_list("windows", "off", "webgl1")

    expected = [ext for ext in engine_gl1 if ext not in set(MOBILE_GLES)]
    assert desktop == expected, (
        "DESKTOP_EXTS is not the engine's measured order minus the refused "
        "three. Order is part of what a detector hashes, so a reordering is a "
        "tell even when every entry is right:\n"
        f"  ours:     {desktop}\n"
        f"  expected: {expected}"
    )


# --- the other arms are untouched -----------------------------------------


@pytest.mark.parametrize("name", ["APPLE_EXTS", "ANDROID_EXTS"])
def test_the_other_desktop_arms_are_not_swept_into_this_fix(name: str) -> None:
    """This slice is the WINDOWS/desktop arm's set, measured on that arm.

    macOS claims ANGLE-over-Metal and android claims a GLES renderer; neither
    was measured here, and sweeping a D3D11 reading onto them would be inventing
    values for arms this slice never observed. `linux` intentionally SHARES
    DESKTOP_EXTS (pinned by `test_linux_limits_and_extensions_are_the_desktop_defaults`),
    so it moves with this fix by design — it is a desktop ANGLE arm too.
    """
    other = set(_literal(name))
    assert not (set(MODERN_DESKTOP) <= other), (
        f"{name} now contains all six modern desktop extensions, which means "
        "this slice was applied to an arm it never measured. macOS and Android "
        "claim different renderers; each needs its own reading."
    )


def test_the_emitted_script_carries_the_new_set(tmp_path) -> None:
    """End to end through the real builder: the literal reaches gpu.js.

    Cheap, and it catches the one failure the source-literal tests cannot — a
    list that is correct in Python and not rendered into the script.
    """
    path = build_gpu_extension(
        1337,
        "windows",
        str(tmp_path / "w"),
        0,
        engine_platform=engine_platform_for("windows", "desktop"),
    )
    js = (pathlib.Path(path) / "gpu.js").read_text(encoding="utf-8")

    for ext in MODERN_DESKTOP:
        assert ext in js, f"{ext} never reached the emitted script"

    rendered = re.search(r"var DESKTOP_EXTS = \[(.*?)\];", js, re.S)
    assert rendered, "DESKTOP_EXTS is not in the emitted script at all"
    assert len(re.findall(r'"([^"]+)"', rendered.group(1))) == EXPECTED_PERSONA_COUNT


def test_the_list_is_still_replaced_wholesale_not_filtered() -> None:
    """The mechanism this fix depends on, pinned so the reasoning stays valid.

    The installer substitutes the whole array (`extList.slice()`). If that ever
    became a FILTER over the host's real list, a stale literal would stop being
    the thing that decides what the page sees, and every argument in this file
    would need re-deriving.
    """
    src = GPU_EXT_SOURCE.read_text(encoding="utf-8")
    assert "return extList.slice();" in src, (
        "getSupportedExtensions no longer returns the hardcoded list wholesale. "
        "If it now filters the host's real set, re-derive this file's premise: "
        "the literal would no longer be what the page reads."
    )


# --- the SECOND context, which is where the rework happened ----------------
#
# ⛔ THE GAP THIS BLOCK CLOSES, STATED PLAINLY. The first version of this file
# was 322 lines and nine tests, and NOT ONE of them mentioned `webgl2` / `GL2`.
# It was green while `gpu_ext.py` served the WebGL1 array verbatim to a WebGL2
# context, so the suite reported 33 as correct for the one context it looked at
# while the other advertised sixteen impossible entries. Green CI and a correct
# change are independent facts; a guard that inspects 1/N of a change is
# evidence about 1/N of it.


def test_the_webgl2_arm_no_longer_falls_through_to_the_webgl1_list() -> None:
    """⛔ THE BLOCKING DEFECT, pinned at the selection itself.

    Before the rework::

        var EXTS_GL2 = (OS === "ios") ? IOS_GL2_EXTS : STABLE_EXTS;

    Only the iOS arm split; every other platform handed the SAME array to both
    ``installOn`` call sites. This asserts on the source text of the selection
    rather than on the arrays, because it is the fall-through — not any
    particular list's contents — that is the defect.
    """
    src = GPU_EXT_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"var EXTS_GL2 = ([^;]+);", src)
    assert match, "the EXTS_GL2 selection is no longer a var declaration"
    selection = " ".join(match.group(1).split())
    assert "STABLE_EXTS" not in selection or "STABLE_GL2_EXTS" in selection, (
        "EXTS_GL2 falls through to the WebGL1 list again:\n"
        f"  {selection}\n"
        "A WebGL2 context served the WebGL1 array advertises the sixteen "
        "extensions WebGL2 promoted into core, which is impossible in every "
        "browser that has ever shipped — a positive identification needing no "
        "baseline. Give the arm its own measured GL2 list."
    )


@pytest.mark.parametrize(
    "name", ["DESKTOP_GL2_EXTS", "APPLE_GL2_EXTS", "ANDROID_GL2_EXTS"]
)
def test_no_core_promoted_extension_appears_on_any_webgl2_list(name: str) -> None:
    """⭐ THE ONE GUARD THAT NEEDS NO HOST, AND THE ONE THAT WAS MISSING.

    This assertion outlives any particular count. Counts move with hardware —
    35 on the owner's card, 36 on the GPU-less VM — but no hardware anywhere
    makes a promoted extension reachable on a WebGL2 context, so this binds on
    every arm including the two for which no WebGL2 reading exists.

    Parametrised across all three non-iOS arms deliberately: the defect was that
    ONE shared array reached both contexts, and a guard covering only the arm
    that happened to be measured would let the next arm re-create it.
    """
    gl2 = set(_literal(name))
    impossible = gl2 & set(PROMOTED_TO_WEBGL2_CORE)
    assert not impossible, (
        f"{name} advertises extensions a conforming WebGL2 context CANNOT "
        f"expose, because WebGL2 promoted them into core: {sorted(impossible)}. "
        "This is a category error, not a magnitude one — a detector needs no "
        "baseline and no host knowledge to read it, unlike a wrong count."
    )


def test_the_promoted_set_is_the_same_sixteen_from_two_independent_sources() -> None:
    """⭐ THE CONSTANT IS DERIVED, NOT TRANSCRIBED FROM A REVIEW COMMENT.

    ``PROMOTED_TO_WEBGL2_CORE`` is the load-bearing input to the guard above, so
    a typo in it would silently weaken that guard rather than fail. This
    re-derives it from two sources that share no provenance:

      * the in-tree corpus, GPU-LESS VM, engine layer OFF (36 GL1 / 30 GL2);
      * this module's OWN measured iOS pair, read from WebKit's hand-written
        macro sequences on a real device (39 GL1 / 36 GL2).

    Different engines, different hosts, different totals. If the same sixteen
    fall out of both, the set is a property of the WebGL2 SPECIFICATION rather
    than of any GPU — which is exactly the claim the guard above rests on.

    ⚠️ The iOS pair is asserted as a SUBSET, not an equality, and that is not a
    weakening. Safari does not ship `EXT_disjoint_timer_query` on either
    context (see the IOS_GL*_EXTS note: timer queries are a timing-attack
    surface), so it can appear in neither difference. An entry absent from both
    of a pair's lists says nothing about promotion, and demanding equality here
    would assert something the iOS reading cannot answer.
    """
    corpus_gl1 = set(_corpus_list("windows", "off", "webgl1"))
    corpus_gl2 = set(_corpus_list("windows", "off", "webgl2"))
    assert corpus_gl1 - corpus_gl2 == set(PROMOTED_TO_WEBGL2_CORE), (
        "the corpus' own GL1-minus-GL2 difference is no longer the sixteen this "
        "file calls the promoted set:\n"
        f"  corpus says: {sorted(corpus_gl1 - corpus_gl2)}\n"
        f"  we say:      {sorted(PROMOTED_TO_WEBGL2_CORE)}"
    )

    ios_only_gl1 = set(_literal("IOS_GL1_EXTS")) - set(_literal("IOS_GL2_EXTS"))
    assert ios_only_gl1 <= set(PROMOTED_TO_WEBGL2_CORE), (
        "this module's own iOS pair drops an extension from WebGL2 that the "
        f"promoted set does not name: {sorted(ios_only_gl1 - set(PROMOTED_TO_WEBGL2_CORE))}. "
        "Either the iOS pair or the promoted set is wrong; both are measured, "
        "so find out which before editing either."
    )


def test_the_webgl2_count_is_the_number_the_PR_claims() -> None:
    """The second falsifiable claim, pinned as a constant like the first.

    The liaison reads this number back off the host directly, so it lives here
    as a value rather than as a sentence in a PR body that can drift from the
    code.
    """
    gl2 = _literal("DESKTOP_GL2_EXTS")
    assert len(gl2) == EXPECTED_PERSONA_GL2_COUNT, (
        f"DESKTOP_GL2_EXTS has {len(gl2)} entries; this slice claims "
        f"{EXPECTED_PERSONA_GL2_COUNT}, which is also what the engine reports "
        "on the owner's host. Update the claim deliberately, in the PR and "
        "here, rather than letting the two drift."
    )
    assert len(set(gl2)) == len(gl2), f"DESKTOP_GL2_EXTS has duplicates: {gl2}"


def test_the_webgl2_only_extensions_are_present_and_could_not_have_been_derived() -> None:
    """⛔ THE HALF A SUBTRACTION CANNOT PRODUCE.

    Removing the promoted sixteen from the 33-entry WebGL1 list leaves 17. The
    engine reports 32. The difference is these WebGL2-only extensions, which
    appear in NO WebGL1 list on any arm — so a rework that had merely subtracted
    would have traded sixteen impossible entries for fifteen absences and looked
    complete.

    Asserting both halves: that each is present in the GL2 list, and that none
    of them is in the GL1 list (which is what makes them underivable).
    """
    gl2 = set(_literal("DESKTOP_GL2_EXTS"))
    gl1 = set(_literal("DESKTOP_EXTS"))
    for ext in WEBGL2_ONLY:
        assert ext in gl2, (
            f"{ext} is missing from DESKTOP_GL2_EXTS. The engine reports it on "
            "its WebGL2 context, and it exists in no WebGL1 list — so it can "
            "only come from the measured GL2 reading."
        )
        assert ext not in gl1, (
            f"{ext} is in DESKTOP_EXTS (the WebGL1 list). If a measurement now "
            "says the engine reports it on WebGL1 too, cite it — but note that "
            "would mean this file's 'underivable' argument needs re-deriving."
        )

    survivors = gl1 - set(PROMOTED_TO_WEBGL2_CORE)
    assert len(survivors) == 17, (
        f"the GL1 list minus the promoted set is now {len(survivors)}, not 17. "
        "The arithmetic in this file's reasoning (17 + 15 = 32) is stale; "
        "re-derive it before trusting the counts around it."
    )
    assert gl2 == survivors | set(WEBGL2_ONLY), (
        "DESKTOP_GL2_EXTS is no longer exactly the surviving GL1 entries plus "
        "the measured WebGL2-only set:\n"
        f"  unexpected: {sorted(gl2 - (survivors | set(WEBGL2_ONLY)))}\n"
        f"  missing:    {sorted((survivors | set(WEBGL2_ONLY)) - gl2)}"
    )


def test_the_mobile_gles_families_stay_absent_from_the_desktop_webgl2_set() -> None:
    """⛔ THE REFUSAL APPLIES TO BOTH CONTEXTS, NOT JUST THE ONE IT WAS WRITTEN FOR.

    The VM corpus lists astc/etc/etc1 on its WebGL2 context too, for the same
    reason it lists them on WebGL1: SwiftShader advertises everything. A guard
    that refused them on WebGL1 only would let the software-rasteriser signature
    in through the second context.
    """
    gl2 = set(_literal("DESKTOP_GL2_EXTS"))
    for ext in MOBILE_GLES:
        assert ext not in gl2, (
            f"{ext} was added to DESKTOP_GL2_EXTS. A claimed Direct3D11 "
            "renderer advertising the mobile GLES compression families is the "
            "software-rasteriser signature audit7 #3 exists to catch — on "
            "either context."
        )


def test_the_unmeasured_arms_got_the_subtraction_and_nothing_invented() -> None:
    """⚠️ THE WEAKER CLAIM, PINNED AS THE WEAKER CLAIM.

    No WebGL2 reading exists for macOS or Android, so their GL2 lists are their
    GL1 lists minus the promoted sixteen and NOTHING ELSE. That is deliberate
    and this test exists to keep it honest in BOTH directions: a future edit
    that pads them toward a plausible length by copying the desktop arm's
    WebGL2-only extensions would be inventing values for hardware nobody
    measured, and a real reading should replace this assertion rather than
    quietly satisfy it.
    """
    for gl1_name, gl2_name in (
        ("APPLE_EXTS", "APPLE_GL2_EXTS"),
        ("ANDROID_EXTS", "ANDROID_GL2_EXTS"),
    ):
        gl1 = _literal(gl1_name)
        gl2 = _literal(gl2_name)
        expected = [e for e in gl1 if e not in set(PROMOTED_TO_WEBGL2_CORE)]
        assert gl2 == expected, (
            f"{gl2_name} is not exactly {gl1_name} minus the promoted set:\n"
            f"  ours:     {gl2}\n"
            f"  expected: {expected}\n"
            "No WebGL2 reading exists for this arm. If one now does, cite it "
            "here and replace this assertion with the measured list — do not "
            "pad toward a plausible length from an arm claiming different "
            "hardware."
        )


def test_the_two_contexts_are_not_byte_identical_on_any_arm() -> None:
    """⭐ THE MEASURED SYMPTOM, ASSERTED DIRECTLY.

    The liaison's reading was ``33 for both webgl and webgl2, byte-identical``
    where the engine reports ``35 and 32 with different contents``. A script
    that asks both contexts and observes they match has a tell WITHOUT knowing
    the correct contents of either — so pin the inequality itself, at the level
    a page would see it, rather than only pinning the contents that happen to
    produce it today.
    """
    for gl1_name, gl2_name in (
        ("DESKTOP_EXTS", "DESKTOP_GL2_EXTS"),
        ("APPLE_EXTS", "APPLE_GL2_EXTS"),
        ("ANDROID_EXTS", "ANDROID_GL2_EXTS"),
        ("IOS_GL1_EXTS", "IOS_GL2_EXTS"),
    ):
        gl1 = _literal(gl1_name)
        gl2 = _literal(gl2_name)
        assert gl1 != gl2, (
            f"{gl1_name} and {gl2_name} are byte-identical. Real browsers never "
            "return the same extension list for a WebGL1 and a WebGL2 context, "
            "so two matching arrays are a tell regardless of their contents."
        )


def test_the_emitted_script_serves_a_different_list_to_each_context(tmp_path) -> None:
    """End to end through the real builder, on the arm that was broken.

    The source-literal tests above cannot see a selection that is correct in the
    literals and wrong at the call site. This runs the actual builder and reads
    the emitted script's own selection lines.
    """
    path = build_gpu_extension(
        1337,
        "windows",
        str(tmp_path / "w"),
        0,
        engine_platform=engine_platform_for("windows", "desktop"),
    )
    js = (pathlib.Path(path) / "gpu.js").read_text(encoding="utf-8")

    rendered = re.search(r"var DESKTOP_GL2_EXTS = \[(.*?)\];", js, re.S)
    assert rendered, "DESKTOP_GL2_EXTS never reached the emitted script"
    entries = re.findall(r'"([^"]+)"', rendered.group(1))
    assert len(entries) == EXPECTED_PERSONA_GL2_COUNT
    assert not (set(entries) & set(PROMOTED_TO_WEBGL2_CORE)), (
        "the emitted script's WebGL2 list carries core-promoted extensions"
    )

    assert "var EXTS_GL2 = (OS === \"ios\") ? IOS_GL2_EXTS : STABLE_GL2_EXTS;" in js, (
        "the emitted script's EXTS_GL2 selection is not the per-context one; "
        "the desktop arm may be falling through to the WebGL1 list again"
    )
