"""PS-440: the engine's GPU spoof must answer the CLAIMED card's capability
limits — and must never claim a capability the backend cannot deliver.

Before this ticket the engine patch renamed the renderer
(``gpu_fingerprint.cc``) and left every other getParameter() answer to the
backend that really renders. On the Linux dev VM that backend is SwiftShader,
so a profile claiming an NVIDIA GeForce RTX on Direct3D11 answered a software
rasteriser's limits — nine of them contradicting the card named in the
renderer string, and one of them (MAX_CUBE_MAP_TEXTURE_SIZE 16384 beside
MAX_TEXTURE_SIZE 8192) contradicting ITSELF, because no driver hands out a
cube map larger than its 2D texture limit.

THE DIRECTION OF THE LIE IS THE WHOLE DESIGN, and it is why these tests are
shaped the way they are. Reporting a limit BELOW the backend's own is safe:
the page is told about less capability than really exists, and every call it
then makes succeeds. Reporting one ABOVE is not cosmetic, because Blink's
internal validation keeps reading the real backend limit. Measured on
SwiftShader under persona's own Linux flags, reading the limit and then
allocating at it:

    texImage2D(..., 16384, 16384, ...)       -> GL_INVALID_VALUE
    renderbufferStorage(..., 16384, 16384)   -> GL_INVALID_VALUE
    viewport(0, 0, 32767, 32767)             -> no error, READS BACK 8192

The viewport case is the worst of the three because it fails SILENTLY: a page
that advertises 32767, sets exactly that, and reads back 8192 has caught us
with no reference card and no error to check. That is the same
self-contained-impossibility class as the cube-map pair, so an upward claim
would trade one such tell for three.

So every override is clamped: we report ``min(claimed, real)`` and never more.
The clamp is a NO-OP where the claim is true — on a real D3D11 backend the
measured answers ARE 16384/32767 — so the windows arm on real hardware is
unaffected, and the same code degrades correctly on a backend that cannot
back the claim.

These tests pin the properties of the fix, all read out of the patch file
itself. Like the rest of this suite they compile nothing: the compile happens
on the trial-build hardware, and what is pinned here is the set of properties
that must hold before it does. The one exception is the clamp ARITHMETIC,
which is re-implemented from the patch's own constants and exercised against
the measured backend tables — a property that can be checked without a
compiler, and the property the whole design rests on.

Four properties, and the reason each earns its place:

1. NEVER ABOVE THE BACKEND. Every getter takes the backend's real answer and
   returns no more than it. This is the property that makes the fix sound
   without a compiled binary: a value that is never above the backend's own
   cannot produce the INVALID_VALUE or the silent viewport contradiction,
   whatever the backend turns out to be.

2. SELF-CONSISTENCY. The cube-map answer may never exceed the 2D texture
   answer we REPORT. This is the one contradiction a checker catches with no
   reference card at all — only our own two numbers — so it must be
   structurally impossible to reintroduce. Clamping cube against its own
   backend value is NOT enough: SwiftShader answers 16384 there beside a real
   8192 texture limit, so that alone would leave the impossible pair intact.

3. CARD-FIDELITY. The claimed values are the measured Direct3D11 ANGLE
   answers (real RTX 3060, same engine build, measured 2026-09-16 for
   PS-440). They are ANGLE's feature-level-11 constants — the same for every
   card in the claimed pool — so the table is one table, not per-card guesses.

4. ARM SCOPING. The overrides fire ONLY on the windows arm, the one claimed
   platform whose backend we hold that measurement for. The linux and macos
   arms claim different backends (desktop-GL NVIDIA, Apple silicon); their
   real reference tables were never measured, and an invented table would
   trade the SwiftShader contradiction for a fabricated one. Those arms keep
   the host's real limits — the pre-ticket behaviour, unchanged.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH = REPO_ROOT / "engine" / "patches" / "fingerprint" / "011-gpu-info.patch"

GPU_FINGERPRINT_CC = "third_party/blink/renderer/modules/webgl/gpu_fingerprint.cc"
WEBGL_BASE_CC = "third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc"

# GL_MAX_CUBE_MAP_TEXTURE_SIZE — the pname that carries the extra cross-cap.
CUBE_MAP_ENUM = 0x851C
TEXTURE_SIZE_ENUM = 0x0D33

# The two backend tables this fix is measured against. Both are readings, not
# guesses: SwiftShader was read live under persona's own Linux flags, and the
# D3D11 column is the ticket's own reference-card measurement.
SWIFTSHADER_BACKEND = {
    0x0D33: 8192,   # GL_MAX_TEXTURE_SIZE
    0x84E8: 8192,   # GL_MAX_RENDERBUFFER_SIZE
    0x851C: 16384,  # GL_MAX_CUBE_MAP_TEXTURE_SIZE  <-- above its own 2D limit
    0x8DFB: 4096,   # GL_MAX_VERTEX_UNIFORM_VECTORS
    0x8DFD: 4096,   # GL_MAX_FRAGMENT_UNIFORM_VECTORS
    0x8DFC: 31,     # GL_MAX_VARYING_VECTORS
    0x8872: 32,     # GL_MAX_TEXTURE_IMAGE_UNITS
    0x8B4C: 32,     # GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS
}

D3D11_BACKEND = {
    0x0D33: 16384,
    0x84E8: 16384,
    0x851C: 16384,
    0x8DFB: 4095,
    0x8DFD: 1024,
    0x8DFC: 30,
    0x8872: 16,
    0x8B4C: 16,
}


@pytest.fixture(scope="module")
def patch_text() -> str:
    assert PATCH.is_file(), f"missing patch: {PATCH}"
    return PATCH.read_text(encoding="utf-8")


# ─── helpers ─────────────────────────────────────────────────────────────────


def _section(patch_text: str, path: str) -> str:
    """One patch section: everything from its `diff --git` line to the next."""
    matches = list(re.finditer(r"(?m)^diff --git ", patch_text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(patch_text)
        header = patch_text[m.start():patch_text.find("\n", m.start())]
        if f" a/{path} b/{path}" in header:
            return patch_text[m.start():end]
    raise AssertionError(f"{path} has no section in the patch")


def _added(patch_text: str, path: str) -> str:
    """The section's ADDED lines, diff marker stripped — the code as shipped."""
    lines = [l[1:] for l in _section(patch_text, path).splitlines()
             if l.startswith("+") and not l.startswith("+++")]
    assert lines, f"{path} adds no lines"
    return "\n".join(lines)


def _claimed_int_constants(patch_text: str) -> dict[str, int]:
    """The kClaimed* scalar constants, by suffix: {MaxTextureSize: 16384, ...}."""
    pairs = re.findall(r"constexpr int32_t kClaimed(\w+) = (\d+);", patch_text)
    assert pairs, "no claimed int constants found in the patch"
    return {name: int(value) for name, value in pairs}


def _claimed_by_enum(patch_text: str) -> dict[int, int]:
    """Map GL enum -> claimed value, read from the patch's own switch arms.

    Built from the patch rather than restated here, so a constant renamed or
    rewired without its switch arm cannot slip past these tests.
    """
    added = _added(patch_text, GPU_FINGERPRINT_CC)
    consts = _claimed_int_constants(patch_text)
    arms = re.findall(
        r"case (0x[0-9A-F]{4}):  // GL_[A-Z_0-9]+\n      return kClaimed(\w+);",
        added,
    )
    assert arms, "ClaimedIntLimit has no readable switch arms"
    return {int(enum, 16): consts[name] for enum, name in arms}


def _claimed_pair(patch_text: str, name: str) -> tuple[float, float]:
    """A two-element kClaimed* array constant, as numbers."""
    m = re.search(
        rf"constexpr (?:int32_t|float) kClaimed{name}\[2\] = "
        rf"\{{\s*([0-9.]+)f?,\s*([0-9.]+)f?\s*\}};",
        patch_text)
    assert m, f"kClaimed{name}[2] not found in the patch"
    return float(m.group(1)), float(m.group(2))


def _report_int(patch_text: str, pname: int, real_value: int) -> int:
    """Re-implementation of the patch's int-limit clamp, for arithmetic tests.

    Mirrors GetSpoofedIntLimitForFingerprint plus the call site's cube-map
    pre-resolve. Deliberately reads its claimed values from the patch (see
    _claimed_by_enum) so it tracks the shipped table instead of duplicating
    it — the ARITHMETIC is what is asserted here, not the numbers.
    """
    claimed = _claimed_by_enum(patch_text)
    if pname not in claimed:
        return real_value
    answer = min(claimed[pname], real_value)
    if pname == CUBE_MAP_ENUM:
        real_tex = _backend_for(real_value)
        reported_tex = min(claimed[TEXTURE_SIZE_ENUM], real_tex)
        answer = min(answer, reported_tex)
    return answer


# The cube-map clamp needs the 2D texture limit of the SAME backend, so the
# arithmetic helper is always driven through a whole table rather than a
# single value. _backend_for is set per-test by _with_backend.
_CURRENT_BACKEND: dict[int, int] = {}


def _backend_for(_unused: int) -> int:
    return _CURRENT_BACKEND[TEXTURE_SIZE_ENUM]


def _report_table(patch_text: str, backend: dict[int, int]) -> dict[int, int]:
    """What getParameter() would answer for each pname on a given backend."""
    global _CURRENT_BACKEND
    _CURRENT_BACKEND = backend
    return {pname: _report_int(patch_text, pname, real)
            for pname, real in backend.items()}


# ─── 1. Never above the backend ──────────────────────────────────────────────


def test_no_reported_limit_ever_exceeds_the_backends_own(patch_text):
    """The property the whole fix rests on, swept rather than sampled.

    An upward claim is what hands a page GL_INVALID_VALUE on allocation, and
    for the viewport it is worse — no error at all, then a readback that
    contradicts what was advertised. Neither needs a reference card. So this
    sweeps every claimed pname across a wide range of plausible backend
    answers and asserts the reported value is never above the real one.
    """
    claimed = _claimed_by_enum(patch_text)
    backend_values = [1, 8, 15, 16, 29, 30, 31, 32, 1023, 1024, 2048,
                      4095, 4096, 8192, 16384, 32768, 65536]
    texture_limits = [256, 1024, 2048, 4096, 8192, 16384, 32768]
    violations = []
    for tex in texture_limits:
        for pname in claimed:
            for real in backend_values:
                table = dict.fromkeys(claimed, real)
                table[TEXTURE_SIZE_ENUM] = tex
                table[pname] = real
                got = _report_table(patch_text, table)[pname]
                if got > real:
                    violations.append(
                        f"pname {pname:#06x}: backend {real}, reported {got}")
    assert not violations, (
        "reported a capability ABOVE the backend's own — a page that believes "
        "it is handed GL_INVALID_VALUE (or, for the viewport, a silent "
        "contradiction):\n  " + "\n  ".join(violations[:10]))


def test_viewport_and_point_size_are_clamped_not_asserted(patch_text):
    """The two array overrides clamp elementwise against the real values.

    The viewport is the silent-failure case measured for this ticket, so its
    getter must take the backend's dims and return no more than them; the
    point-size range must clamp its MAXIMUM down and its MINIMUM up (claiming
    a smaller minimum point size than the backend supports is the same
    upward lie in the other direction).
    """
    added = _added(patch_text, GPU_FINGERPRINT_CC)

    vp = re.search(
        r"std::array<int32_t, 2> GetSpoofedViewportDimsForFingerprint\("
        r"\s*const std::array<int32_t, 2>& real_dims\) \{\n(.*?)\n\}",
        added, re.S)
    assert vp, "GetSpoofedViewportDimsForFingerprint does not take the real dims"
    assert vp.group(1).count("std::min(") == 2, (
        "the viewport override must clamp BOTH dimensions against the real ones")

    pr = re.search(
        r"std::array<float, 2> GetSpoofedPointSizeRangeForFingerprint\("
        r"\s*const std::array<float, 2>& real_range\) \{\n(.*?)\n\}",
        added, re.S)
    assert pr, "GetSpoofedPointSizeRangeForFingerprint does not take the real range"
    body = pr.group(1)
    assert "std::max(kClaimedPointSizeRange[0], real_range[0])" in body, (
        "the point-size MINIMUM must be clamped UP to the backend's own")
    assert "std::min(kClaimedPointSizeRange[1], real_range[1])" in body, (
        "the point-size MAXIMUM must be clamped DOWN to the backend's own")


def test_int_getter_takes_the_real_value_and_returns_it_when_inactive(patch_text):
    """Off the windows arm the getter must return the backend's value as-is.

    The signature is what makes the clamp possible at all, so it is pinned:
    a getter that cannot see the real value cannot avoid claiming above it.
    """
    added = _added(patch_text, GPU_FINGERPRINT_CC)
    sig = re.search(
        r"int32_t GetSpoofedIntLimitForFingerprint\(uint32_t pname,\s*"
        r"int32_t real_value,\s*int32_t reported_max_texture_size\) \{\n(.*?)\n\}",
        added, re.S)
    assert sig, "GetSpoofedIntLimitForFingerprint does not take the real value"
    body = sig.group(1)
    assert "if (!IsWindowsGpuFingerprintActive()) {\n    return real_value;" in body, (
        "an inactive arm must answer the backend's own value, not a claim")
    assert "std::min(*claimed, real_value)" in body, (
        "the override must be clamped against the backend's real value")


# ─── 2. Self-consistency ─────────────────────────────────────────────────────


def test_cube_map_answer_never_exceeds_the_reported_2d_texture_limit(patch_text):
    """The one contradiction a checker needs no reference card to catch.

    Asserted as ARITHMETIC on both measured backends rather than as a
    comparison of two constants, because the constants alone cannot express
    it: on SwiftShader the backend answers cube 16384 beside texture 8192, so
    a cube clamp that consulted only its own backend value would return 16384
    and leave the impossible pair exactly as the ticket found it.
    """
    for label, backend in (("SwiftShader", SWIFTSHADER_BACKEND),
                           ("D3D11", D3D11_BACKEND)):
        reported = _report_table(patch_text, backend)
        assert reported[CUBE_MAP_ENUM] <= reported[TEXTURE_SIZE_ENUM], (
            f"on {label} the reported cube-map limit "
            f"{reported[CUBE_MAP_ENUM]} exceeds the reported 2D texture limit "
            f"{reported[TEXTURE_SIZE_ENUM]} — an impossible capability set a "
            f"checker catches with our own two numbers alone")


def test_cube_map_clamp_consults_the_reported_texture_size(patch_text):
    """Pin the mechanism, not just its result.

    The cross-cap is the difference between fixing the impossible pair and
    only appearing to, so the structure that produces it is asserted: the
    getter caps cube against `reported_max_texture_size`, and the call site
    resolves that by running the texture-size limit through the same clamp
    BEFORE answering the cube-map pname.
    """
    added_fp = _added(patch_text, GPU_FINGERPRINT_CC)
    assert re.search(
        r"if \(pname == 0x851C\) \{.*?"
        r"answer = std::min\(answer, reported_max_texture_size\);",
        added_fp, re.S), (
        "the cube-map arm does not cap against the REPORTED 2D texture size")

    added_base = _added(patch_text, WEBGL_BASE_CC)
    assert "if (pname == GL_MAX_CUBE_MAP_TEXTURE_SIZE) {" in added_base
    assert "ContextGL()->GetIntegerv(GL_MAX_TEXTURE_SIZE, &real_max_texture_size);" in added_base, (
        "the call site must read the REAL texture limit to resolve the cap")
    assert "reported_max_texture_size = GetSpoofedIntLimitForFingerprint(" in added_base, (
        "the cap must be the REPORTED texture size — the same clamp applied "
        "to GL_MAX_TEXTURE_SIZE — not the raw backend value")


def test_renderbuffer_answer_never_exceeds_the_reported_2d_texture_limit(patch_text):
    for label, backend in (("SwiftShader", SWIFTSHADER_BACKEND),
                           ("D3D11", D3D11_BACKEND)):
        reported = _report_table(patch_text, backend)
        assert reported[0x84E8] <= reported[TEXTURE_SIZE_ENUM], (
            f"on {label} the renderbuffer answer exceeds the 2D texture "
            f"answer — the same self-contained impossibility, one pname over")


# ─── 3. Card fidelity: the measured D3D11 ANGLE answers ─────────────────────


def test_claimed_values_match_the_measured_reference(patch_text):
    """Value-for-value against the PS-440 measurement (RTX 3060, D3D11 ANGLE).

    Written out per-parameter ON PURPOSE, the way PS-22's WebGL2 reference is:
    a wrong-but-VALID value still reads as success unless each expectation is
    named. These are the nine limits the ticket measured contradicting the
    claimed card, plus MAX_VERTEX_TEXTURE_IMAGE_UNITS kept coherent with its
    fragment sibling at the claimed backend's 16.
    """
    consts = _claimed_int_constants(patch_text)
    assert consts["MaxTextureSize"] == 16384
    assert consts["MaxRenderbufferSize"] == 16384
    assert consts["MaxCubeMapTextureSize"] == 16384
    assert consts["MaxVertexUniformVectors"] == 4095, (
        "the claimed D3D11 backend answers 4095 — SwiftShader's 4096 is one "
        "of the nine contradictions the ticket measured")
    assert consts["MaxFragmentUniformVectors"] == 1024
    assert consts["MaxVaryingVectors"] == 30
    assert consts["MaxTextureImageUnits"] == 16
    assert consts["MaxVertexTextureImageUnits"] == 16
    assert _claimed_pair(patch_text, "MaxViewportDims") == (32767.0, 32767.0), (
        "a desktop viewport is required for coherence with a desktop D3D11 card")
    assert _claimed_pair(patch_text, "PointSizeRange") == (1.0, 1024.0)


def test_on_a_real_d3d11_backend_the_clamp_is_a_no_op(patch_text):
    """The windows arm on real hardware must be untouched by the clamp.

    This is what makes one table correct on both backends: where the claim is
    true, min(claimed, real) IS the claim, so a Windows user on native D3D11
    keeps reporting exactly the measured reference values and nothing about
    their fingerprint moves.
    """
    reported = _report_table(patch_text, D3D11_BACKEND)
    assert reported == D3D11_BACKEND, (
        "the clamp altered a value on a backend that can honour the claim: "
        f"{ {k: v for k, v in reported.items() if v != D3D11_BACKEND[k]} }")


def test_on_swiftshader_the_fixable_mismatches_are_fixed(patch_text):
    """The five limits a downward clamp genuinely closes, named individually.

    Stated as its own test so the fix's real reach is pinned rather than
    implied. These are the parameters where the claimed card's answer is LOWER
    than SwiftShader's, so reporting the claim costs nothing and removes the
    contradiction outright.
    """
    reported = _report_table(patch_text, SWIFTSHADER_BACKEND)
    assert reported[0x8DFB] == 4095, "MAX_VERTEX_UNIFORM_VECTORS not corrected"
    assert reported[0x8DFD] == 1024, "MAX_FRAGMENT_UNIFORM_VECTORS not corrected"
    assert reported[0x8DFC] == 30, "MAX_VARYING_VECTORS not corrected"
    assert reported[0x8872] == 16, "MAX_TEXTURE_IMAGE_UNITS not corrected"
    assert reported[0x8B4C] == 16, "MAX_VERTEX_TEXTURE_IMAGE_UNITS not corrected"
    # And the impossible pair, which is the headline fix on this backend.
    assert reported[CUBE_MAP_ENUM] == 8192, (
        "the impossible cube-map pair survives on SwiftShader")


def test_on_swiftshader_the_unfixable_mismatches_are_left_honest(patch_text):
    """The cost of the design, pinned so it cannot be quietly "fixed" upward.

    Where the claimed card's limit is ABOVE what SwiftShader can do, we report
    the backend's value and the mismatch against a reference card SURVIVES.
    That is deliberate: closing it would mean claiming a capability the
    machine does not have, which is catchable from our own context alone.
    A future change that raises any of these is reintroducing the tell this
    ticket's rework removed, and should fail here rather than ship.
    """
    reported = _report_table(patch_text, SWIFTSHADER_BACKEND)
    assert reported[TEXTURE_SIZE_ENUM] == 8192, (
        "MAX_TEXTURE_SIZE was raised above SwiftShader's real ceiling — "
        "measured: allocating at 16384 returns GL_INVALID_VALUE")
    assert reported[0x84E8] == 8192, (
        "MAX_RENDERBUFFER_SIZE was raised above SwiftShader's real ceiling — "
        "measured: renderbufferStorage at 16384 returns GL_INVALID_VALUE")


def test_every_claimed_value_is_reachable_through_its_gl_enum(patch_text):
    """A constant no switch arm returns is dead weight; a switch arm with no
    constant is a spoof that answers nothing. Both fail loudly here.

    The GL enums are written as hex literals with the GL name in the comment
    (the patch's own file includes no GL header), so this pairs each
    `case 0x....:` with the constant its arm returns and checks the comment
    names the parameter the enum actually is.
    """
    added = _added(patch_text, GPU_FINGERPRINT_CC)
    arms = re.findall(
        r"case (0x[0-9A-F]{4}):  // (GL_[A-Z_0-9]+)\n      return kClaimed(\w+);",
        added,
    )
    assert len(arms) == 8, f"expected 8 int-limit arms, found {len(arms)}: {arms}"
    seen_enums: set[str] = set()
    for enum, gl_name, const_name in arms:
        assert enum not in seen_enums, f"duplicate switch arm for {enum}"
        seen_enums.add(enum)
        assert f"constexpr int32_t kClaimed{const_name}" in added
        # The comment is the only human-readable binding of enum to name, so
        # it must exist and must be the parameter's GL name.
        assert re.search(rf"{re.escape(enum)}\b.*// {re.escape(gl_name)}\b",
                         added), f"{enum} is not commented as {gl_name}"
    assert "GetSpoofedViewportDimsForFingerprint" in patch_text
    assert "GetSpoofedPointSizeRangeForFingerprint" in patch_text


# ─── 4. Arm scoping and page-visibility ──────────────────────────────────────


def test_limit_overrides_gate_on_the_windows_arm_only(patch_text):
    """The overrides fire only where the claimed backend is the measured one.

    The gate must require the DECLARED platform to be windows — the same
    string the identity spoofs resolve — so the linux arm (claiming a
    desktop-GL NVIDIA card) and the macos arm (claiming Apple silicon) keep
    the host's real limits rather than answering values invented for a
    backend they do not claim.
    """
    assert "bool IsWindowsGpuFingerprintActive()" in patch_text
    added = _added(patch_text, GPU_FINGERPRINT_CC)
    gate = re.search(
        r"bool IsWindowsGpuFingerprintActive\(\) \{\n(.*?)\n\}", added, re.S)
    assert gate, "IsWindowsGpuFingerprintActive has no readable body"
    assert 'GetDeclaredPlatform() == "windows"' in gate.group(1)
    # All three public override getters must consult that gate, not a weaker
    # check, so no pname can leak the override onto an unmeasured arm.
    for fn in ("GetSpoofedIntLimitForFingerprint",
               "GetSpoofedViewportDimsForFingerprint",
               "GetSpoofedPointSizeRangeForFingerprint"):
        body = re.search(rf"{fn}\((?:[^)]|\n)*?\) \{{\n(.*?)\n\}}", added, re.S)
        assert body, f"{fn} has no readable body"
        assert "IsWindowsGpuFingerprintActive()" in body.group(1), (
            f"{fn} does not gate on the windows arm")


def test_overrides_are_page_visible_only(patch_text):
    """The backend's real limits stay authoritative for GL itself.

    The hook lives in the page-facing getParameter helpers, and must not touch
    the internals that feed Blink's own validation (`max_texture_size_` is
    read straight off ContextGL for texture-size checks). Spoofing those would
    move the real ceiling rather than what we report about it — a different
    change with a different blast radius, and not what this ticket asked for.
    """
    hits = [l for l in patch_text.splitlines()
            if (l.startswith("+") or l.startswith("-"))
            and "GetIntegerv(GL_MAX_TEXTURE_SIZE, &max_texture_size_)" in l]
    assert not hits, f"the patch rewrites an internal-validation line: {hits}"


def test_the_limits_hook_mutates_the_value_the_backend_already_answered(patch_text):
    """The clamp must land where the REAL value is in hand.

    The three Get*Parameter helpers are the page-facing readers: each has
    already called ContextGL()->Get*v and holds the backend's own answer,
    which is exactly what the clamp needs and what the getParameter switch
    arms do not have. Because WebGL2 routes through these same helpers, one
    hook answers both contexts.

    What a patch file can prove about that is the hook's SHAPE, so that is
    what is asserted: each hunk rewrites the value the helper is about to
    return, in place, immediately before its own return statement — and adds
    no backend read of its own for the pname being answered, so the value it
    clamps can only be the one the helper already read.
    """
    section = _section(patch_text, WEBGL_BASE_CC)
    hunks = list(re.finditer(r"(?m)^@@ -\d+,\d+ \+\d+,\d+ @@.*?(?=^@@ -|\Z)",
                             section, re.S))
    assert hunks, "no hunks in the webgl_rendering_context_base.cc section"

    def hunk_with(needle: str) -> str:
        for h in hunks:
            if needle in h.group(0):
                return "\n".join(l[1:] if l[:1] in "+- " else l
                                 for l in h.group(0).splitlines())
        raise AssertionError(f"no hunk adds {needle}")

    # The int helper: the clamp assigns back into `value`, and the helper's
    # own return of that same `value` is the hunk's trailing context.
    int_hunk = hunk_with("GetSpoofedIntLimitForFingerprint")
    assert re.search(
        r"value = GetSpoofedIntLimitForFingerprint\(static_cast<uint32_t>\(pname\),\s*"
        r"value, reported_max_texture_size\);", int_hunk), (
        "the int clamp does not rewrite the value the helper already read")
    assert "return WebGLAny(script_state, value);" in int_hunk, (
        "the int clamp does not sit at the helper's own return")

    # The two array helpers: same shape, elementwise, immediately before the
    # DOM*Array the helper hands back.
    vp_hunk = hunk_with("GetSpoofedViewportDimsForFingerprint")
    assert "value[0] = spoofed[0];" in vp_hunk and "value[1] = spoofed[1];" in vp_hunk, (
        "the viewport clamp does not rewrite the values the helper already read")
    assert "DOMInt32Array::Create(base::span(value).first(length))" in vp_hunk

    ps_hunk = hunk_with("GetSpoofedPointSizeRangeForFingerprint")
    assert "value[0] = spoofed[0];" in ps_hunk and "value[1] = spoofed[1];" in ps_hunk, (
        "the point-size clamp does not rewrite the values the helper already read")
    assert "DOMFloat32Array::Create(base::span(value).first(length))" in ps_hunk

    # The ONLY backend LIMIT read this patch adds is the cube-map cross-cap's
    # deliberate second read of GL_MAX_TEXTURE_SIZE. Anything else would mean
    # the clamp is reading its own value instead of the helper's. (GetString
    # reads belong to the identity spoof, which predates this work.)
    added = [l[1:] for l in section.splitlines()
             if l.startswith("+") and not l.startswith("+++")]
    reads = [l.strip() for l in added if "ContextGL()->GetIntegerv" in l]
    assert reads == [
        "ContextGL()->GetIntegerv(GL_MAX_TEXTURE_SIZE, &real_max_texture_size);"
    ], f"the patch adds an unexpected backend limit read: {reads}"


def test_the_getparameter_switch_is_left_at_its_upstream_shape(patch_text):
    """The switch arms themselves must be untouched by this patch.

    Hooking the helpers instead of the switch is what keeps the patch small
    and keeps WebGL2 working through one hook; it also means the switch
    reverts to upstream's own shape. If a future change moves the hook back
    into the switch, the helper tests above would still pass while the real
    value stopped being available — so the absence is asserted here.
    """
    section = _section(patch_text, WEBGL_BASE_CC)
    added = [l[1:] for l in section.splitlines()
             if l.startswith("+") and not l.startswith("+++")]
    stray = [l for l in added if re.match(r"\s*case GL_MAX_", l)]
    assert not stray, (
        "the patch adds getParameter switch arms — the clamp belongs in the "
        f"Get*Parameter helpers, where the real value is in hand: {stray}")


def test_identity_spoof_still_reads_process_global_state(patch_text):
    """The PS-218 premise survives the limits work unchanged.

    The identity pair and the limits both resolve from
    base::CommandLine::ForCurrentProcess() — process-global state — which is
    why covering another realm still needs no plumbing.
    """
    assert "kUnmaskedRendererWebgl" in patch_text
    assert "kUnmaskedVendorWebgl" in patch_text
    assert "base::CommandLine::ForCurrentProcess()" in patch_text
