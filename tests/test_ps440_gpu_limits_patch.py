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
    # The WebGL2 companions, read live on the same context as the vectors
    # above. Pre-fix these reconcile exactly with the backend's own vectors
    # (4096*4, 4096*4, 31*4, 32+32) — it is the CLAMP that breaks the
    # identity unless the companions are clamped too, which is what the
    # cross-parameter invariant tests below pin.
    0x8B4A: 16384,  # GL_MAX_VERTEX_UNIFORM_COMPONENTS    (= 4096 * 4)
    0x8B49: 16384,  # GL_MAX_FRAGMENT_UNIFORM_COMPONENTS  (= 4096 * 4)
    0x8B4B: 124,    # GL_MAX_VARYING_COMPONENTS           (=   31 * 4)
    0x9122: 128,    # GL_MAX_VERTEX_OUTPUT_COMPONENTS
    0x9125: 128,    # GL_MAX_FRAGMENT_INPUT_COMPONENTS
    0x8B4D: 64,     # GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS (=  32 + 32)
}

# The claimed card. The eight originals are the ticket's own reference-card
# measurement; the six companions are what ANGLE's D3D11 backend computes
# beside them, from the SAME quantity, in GenerateCaps()
# (angle/src/libANGLE/renderer/d3d/d3d11/renderer11_utils.cpp):
#
#   maxShaderUniformComponents[Vertex]   = maxVertexUniformVectors   * 4
#   maxShaderUniformComponents[Fragment] = maxFragmentUniformVectors * 4
#   maxVaryingComponents       = GetMaximumVertexOutputVectors(fl) * 4
#   maxVaryingVectors          = GetMaximumVertexOutputVectors(fl)
#   maxVertexOutputComponents  = GetMaximumVertexOutputVectors(fl) * 4
#   maxFragmentInputComponents = GetMaximumPixelInputVectors(fl)   * 4
#   maxCombinedTextureImageUnits = texUnits[Vertex] + texUnits[Fragment]
#
# Both register files are 32 wide less the 2 reserved for dx_Position and
# gl_Position, so vertex-output and fragment-input alike are (32-2)*4 = 120.
# That this arithmetic reproduces the reference card's MEASURED
# MAX_VARYING_VECTORS of 30 is the check that we are reading the chain that
# produced the ticket's numbers rather than a plausible-looking other one.
D3D11_BACKEND = {
    0x0D33: 16384,
    0x84E8: 16384,
    0x851C: 16384,
    0x8DFB: 4095,
    0x8DFD: 1024,
    0x8DFC: 30,
    0x8872: 16,
    0x8B4C: 16,
    0x8B4A: 16380,  # 4095 * 4
    0x8B49: 4096,   # 1024 * 4
    0x8B4B: 120,    #   30 * 4
    0x9122: 120,    #   30 * 4
    0x9125: 120,    #   30 * 4
    0x8B4D: 32,     #   16 + 16
}

# The vector/components pairs whose relationship GLES3 and ANGLE's D3D11
# backend both make definitional: a vector is four components.
VECTOR_COMPONENT_PAIRS = (
    ("uniform (vertex)", 0x8DFB, 0x8B4A),
    ("uniform (fragment)", 0x8DFD, 0x8B49),
    ("varying", 0x8DFC, 0x8B4B),
    ("vertex output", 0x8DFC, 0x9122),
    ("fragment input", 0x8DFC, 0x9125),
)

# The per-stage texture-unit counts and the combined count that is their sum.
COMBINED_TEXTURE_UNITS = (0x8B4D, 0x8B4C, 0x8872)


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
    """The kClaimed* scalar constants, by suffix: {MaxTextureSize: 16384, ...}.

    Resolves DERIVED constants too. The companion limits are deliberately not
    literals in the source — they are written as arithmetic over the constants
    they are definitionally tied to, so an edit to a vector limit carries its
    companion with it. This helper therefore evaluates that arithmetic rather
    than only reading digits, which keeps these tests measuring the SHIPPED
    table instead of a copy of it.
    """
    consts: dict[str, int] = {}
    source = _added(patch_text, GPU_FINGERPRINT_CC)

    # Plain literals first, plus the small named factor the derivations use.
    for name, value in re.findall(
            r"constexpr int32_t (kComponentsPerVector|kClaimed\w+) = (\d+);",
            source):
        consts[name] = int(value)
    assert any(k.startswith("kClaimed") for k in consts), (
        "no claimed int constants found in the patch")

    # Then derivations, resolved against the literals above. Only `*` and `+`
    # over already-known constants are accepted: anything else is a shape this
    # helper cannot vouch for, and it fails loudly rather than guessing.
    derived = re.findall(
        r"constexpr int32_t (kClaimed\w+) =\s*"
        r"(k\w+)\s*([*+])\s*(k\w+);", source)
    for name, left, op, right in derived:
        assert left in consts and right in consts, (
            f"{name} is derived from {left}/{right}, which are not resolvable "
            f"constants — the test cannot read the shipped value")
        consts[name] = (consts[left] * consts[right] if op == "*"
                        else consts[left] + consts[right])

    return {name[len("kClaimed"):]: value for name, value in consts.items()
            if name.startswith("kClaimed")}


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


def test_reported_components_are_always_four_times_reported_vectors(patch_text):
    """A vector is four components — so the two answers must reconcile.

    This is the invariant that caught the defect this test exists for: the
    clamp moved the VECTORS side of each pair and left the COMPONENTS side at
    the backend's value, so components/4 stopped equalling vectors. Every one
    of those is catchable from our own two numbers on one context, with no
    reference card and no error to provoke — the same class as the cube-map
    pair, and strictly worse than the mismatches we knowingly leave.

    Asserted as ARITHMETIC over both measured backends rather than as a
    comparison of constants, for the reason the cube-map test gives: the
    constants alone cannot express what the clamp does to them on a backend
    that cannot back the claim.
    """
    for label, backend in (("SwiftShader", SWIFTSHADER_BACKEND),
                           ("D3D11", D3D11_BACKEND)):
        reported = _report_table(patch_text, backend)
        for name, vectors_enum, components_enum in VECTOR_COMPONENT_PAIRS:
            vectors = reported[vectors_enum]
            components = reported[components_enum]
            assert components == vectors * 4, (
                f"on {label} the {name} pair does not reconcile: reported "
                f"{vectors} vectors ({vectors_enum:#06x}) beside "
                f"{components} components ({components_enum:#06x}), but "
                f"{vectors} * 4 == {vectors * 4}. A page reads both from one "
                f"context and divides — an impossibility needing no "
                f"reference card")


def test_reported_combined_texture_units_are_the_sum_of_the_stages(patch_text):
    """The combined count is the sum of its stages on the card we claim.

    ANGLE's D3D11 backend computes it exactly that way, and leaving it at the
    backend's own value beside two clamped stage counts advertises more
    combined units than the stages can add up to. Unlike the components
    pairs, this one is readable on a WebGL1 context too — measured live,
    SwiftShader answers 64 for 0x8B4D on both contexts — so it is a tell even
    for a page that never asks for WebGL2.
    """
    combined_enum, vertex_enum, fragment_enum = COMBINED_TEXTURE_UNITS
    for label, backend in (("SwiftShader", SWIFTSHADER_BACKEND),
                           ("D3D11", D3D11_BACKEND)):
        reported = _report_table(patch_text, backend)
        vertex = reported[vertex_enum]
        fragment = reported[fragment_enum]
        assert reported[combined_enum] == vertex + fragment, (
            f"on {label} the combined texture-unit count "
            f"{reported[combined_enum]} is not the sum of the reported "
            f"stages ({vertex} vertex + {fragment} fragment = "
            f"{vertex + fragment})")


def test_every_clamped_vector_limit_has_its_companion_clamped_too(patch_text):
    """Guard the CLASS, not the six parameters that happen to be known.

    The defect this round fixes was one of scope, not of arithmetic: the
    clamp was extended to a vector limit and its companion was left behind,
    silently. So rather than listing today's pairs again, this asserts the
    closure property — if either half of a related pair is claimed, both
    halves must be. A future parameter added to ClaimedIntLimit without its
    companion fails here instead of shipping a fresh contradiction.
    """
    claimed = _claimed_by_enum(patch_text)
    related = [(name, a, b) for name, a, b in VECTOR_COMPONENT_PAIRS]
    related.append(("combined texture units",
                    COMBINED_TEXTURE_UNITS[1], COMBINED_TEXTURE_UNITS[0]))
    related.append(("combined texture units",
                    COMBINED_TEXTURE_UNITS[2], COMBINED_TEXTURE_UNITS[0]))
    half_spoofed = [
        f"{name}: {a:#06x} {'claimed' if a in claimed else 'NOT claimed'} "
        f"but {b:#06x} {'claimed' if b in claimed else 'NOT claimed'}"
        for name, a, b in related
        if (a in claimed) != (b in claimed)]
    assert not half_spoofed, (
        "a limit is spoofed while the parameter definitionally tied to it is "
        "not — the page reads both and the pair contradicts itself:\n  "
        + "\n  ".join(half_spoofed))


def test_companion_limits_are_derived_from_their_vector_constants(patch_text):
    """Pin the mechanism, like the cube-map test does — not just the values.

    Hand-writing 16380/4096/120/32 as independent literals would produce the
    right answers today and desync the moment someone edits a vector limit
    without remembering its companion, which is exactly the failure being
    fixed. So the companions must be DERIVED in the source from the
    constants they are tied to.
    """
    added = _added(patch_text, GPU_FINGERPRINT_CC)
    for const, expr in (
        ("kClaimedMaxVertexUniformComponents",
         "kClaimedMaxVertexUniformVectors * kComponentsPerVector"),
        ("kClaimedMaxFragmentUniformComponents",
         "kClaimedMaxFragmentUniformVectors * kComponentsPerVector"),
        ("kClaimedMaxVaryingComponents",
         "kClaimedMaxVaryingVectors * kComponentsPerVector"),
        ("kClaimedMaxVertexOutputComponents",
         "kClaimedMaxVaryingVectors * kComponentsPerVector"),
        ("kClaimedMaxFragmentInputComponents",
         "kClaimedMaxVaryingVectors * kComponentsPerVector"),
        ("kClaimedMaxCombinedTextureImageUnits",
         "kClaimedMaxVertexTextureImageUnits + kClaimedMaxTextureImageUnits"),
    ):
        assert re.search(
            rf"constexpr int32_t {const} =\s*{re.escape(expr)};", added), (
            f"{const} must be derived as `{expr}`, not written out as a "
            f"literal — a literal cannot track an edit to its vector limit")


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
    # The six companions. Their claimed values are DERIVED in the source, so
    # naming them here is not a restatement of a literal — it checks that the
    # derivation lands on what ANGLE's D3D11 backend actually computes.
    assert consts["MaxVertexUniformComponents"] == 16380, (
        "4095 vertex uniform vectors * 4 components")
    assert consts["MaxFragmentUniformComponents"] == 4096, (
        "1024 fragment uniform vectors * 4 components")
    assert consts["MaxVaryingComponents"] == 120, "30 varying vectors * 4"
    assert consts["MaxVertexOutputComponents"] == 120, (
        "(D3D11_VS_OUTPUT_REGISTER_COUNT 32 - 2 reserved) * 4 — the same "
        "quantity the varying-vector limit derives from, which is why it "
        "reproduces the reference card's measured 30 varyings")
    assert consts["MaxFragmentInputComponents"] == 120, (
        "(D3D11_PS_INPUT_REGISTER_COUNT 32 - 2 reserved) * 4")
    assert consts["MaxCombinedTextureImageUnits"] == 32, (
        "16 vertex + 16 fragment — the sum of the stages, as ANGLE's D3D11 "
        "backend computes it and as gpu_ext.py's desktop table already pins")
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
    assert arms, "ClaimedIntLimit has no readable switch arms"
    # Tied to the backend tables rather than a bare count, so adding a pname
    # to the spoof without measuring it on both backends fails here. A naked
    # integer would have to be bumped by hand on every extension, which is
    # how a parameter gets clamped without its companion in the first place.
    expected = set(SWIFTSHADER_BACKEND) & set(D3D11_BACKEND)
    got = {int(enum, 16) for enum, _, _ in arms}
    assert got == expected, (
        "the spoofed pnames and the measured backend tables disagree — "
        f"spoofed but unmeasured: {sorted(hex(e) for e in got - expected)}; "
        f"measured but unspoofed: {sorted(hex(e) for e in expected - got)}")
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


def test_no_two_hunks_share_identical_leading_context(patch_text):
    """A hunk must be anchorable, not merely correct.

    CAUGHT IN CI, NOT BY REVIEW: the two array-helper hunks
    (GetWebGLFloatArrayParameter and GetWebGLIntArrayParameter) end their
    switch the same way, so at the default 3 lines of context BOTH carried the
    identical leading context `default:` / `NOTIMPLEMENTED();` / `}`. GNU
    patch matched the first hunk fuzzily at the second one's site, consumed
    it, and the second then had nowhere to land — `Hunk #6 FAILED`. The real
    Chromium tree happened to absorb this; the synthetic fixture tree that
    PS-307 builds did not, which is exactly what that fixture is for.

    It is not enough to widen the context once and move on: the next hunk
    added near a `default:`/`NOTIMPLEMENTED();` pair would reintroduce it
    silently, and the failure surfaces far from the edit as a mis-anchored
    apply. So the property is asserted directly — every hunk in the patch
    must be distinguishable from every other by its leading context alone.
    """
    for section_path in (WEBGL_BASE_CC, GPU_FINGERPRINT_CC):
        try:
            section = _section(patch_text, section_path)
        except AssertionError:
            continue  # a new-file section has a single hunk; nothing to clash
        leads: dict[tuple[str, ...], str] = {}
        for h in re.finditer(r"(?m)^(@@ -\d+,\d+ \+\d+,\d+ @@.*?)(?=^@@ -|\Z)",
                             section, re.S):
            body = h.group(0).splitlines()[1:]
            lead = tuple(l[1:].strip() for l in body
                         if l.startswith(" "))[:3]
            lead = tuple(l for l in lead if l)
            if not lead:
                continue
            header = h.group(0).splitlines()[0]
            assert lead not in leads, (
                f"in {section_path}, two hunks share the identical leading "
                f"context {list(lead)}:\n  {leads[lead]}\n  {header}\n"
                f"patch(1) can anchor one on the other's site — widen the "
                f"context of at least one until they differ")
            leads[lead] = header


def test_identity_spoof_still_reads_process_global_state(patch_text):
    """The PS-218 premise survives the limits work unchanged.

    The identity pair and the limits both resolve from
    base::CommandLine::ForCurrentProcess() — process-global state — which is
    why covering another realm still needs no plumbing.
    """
    assert "kUnmaskedRendererWebgl" in patch_text
    assert "kUnmaskedVendorWebgl" in patch_text
    assert "base::CommandLine::ForCurrentProcess()" in patch_text
