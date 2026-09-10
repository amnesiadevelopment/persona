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
