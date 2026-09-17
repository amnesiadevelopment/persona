"""PS-440: the engine's GPU spoof must answer the CLAIMED card's capability limits.

Before this ticket the engine patch renamed the renderer
(``gpu_fingerprint.cc``) and left every other getParameter() answer to the
backend that really renders. On the Linux dev VM that backend is SwiftShader,
so a profile claiming an NVIDIA GeForce RTX on Direct3D11 answered a software
rasteriser's limits — nine of them contradicting the card named in the
renderer string, and one of them (MAX_CUBE_MAP_TEXTURE_SIZE 16384 beside
MAX_TEXTURE_SIZE 8192) contradicting ITSELF, because no driver hands out a
cube map larger than its 2D texture limit.

These tests pin the properties of the fix, all read out of the patch file
itself. Like the rest of this suite they compile nothing: the compile happens
on the trial-build hardware, and what is pinned here is the set of properties
that must hold before it does.

Three properties, and the reason each earns its place:

1. SELF-CONSISTENCY. The cube-map override may never exceed the 2D texture
   override. This is the one contradiction a checker catches with no
   reference card at all — only our own two numbers — so it is the one that
   must be structurally impossible to reintroduce.

2. CARD-FIDELITY. The values are the measured Direct3D11 ANGLE answers
   (real RTX 3060, same engine build, measured 2026-09-16 for PS-440). They
   are ANGLE's feature-level-11 constants — the same for every card in the
   claimed pool — so the table is one table, not per-card guesses.

3. ARM SCOPING. The overrides fire ONLY on the windows arm, the one claimed
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


@pytest.fixture(scope="module")
def patch_text() -> str:
    assert PATCH.is_file(), f"missing patch: {PATCH}"
    return PATCH.read_text(encoding="utf-8")


# ─── 1. Self-consistency ─────────────────────────────────────────────────────


def _spoofed_int_constants(patch_text: str) -> dict[str, int]:
    """The kSpoofed* scalar constants, by suffix: {MaxTextureSize: 16384, ...}."""
    pairs = re.findall(
        r"constexpr int32_t kSpoofed(\w+) = (\d+);", patch_text)
    assert pairs, "no spoofed int constants found in the patch"
    return {name: int(value) for name, value in pairs}


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


def test_cube_map_limit_never_exceeds_the_2d_texture_limit(patch_text):
    """The one contradiction a checker needs no reference card to catch.

    MAX_CUBE_MAP_TEXTURE_SIZE above MAX_TEXTURE_SIZE is impossible on its own
    terms — no driver does it — so reporting it is a self-contained confession
    that the capability set is not one card's. The override table must keep
    the two equal (the claimed D3D11 backend answers 16384 for both).
    """
    consts = _spoofed_int_constants(patch_text)
    assert consts["MaxCubeMapTextureSize"] <= consts["MaxTextureSize"], (
        f"cube-map override {consts['MaxCubeMapTextureSize']} exceeds the 2D "
        f"texture override {consts['MaxTextureSize']} — an impossible "
        f"capability set a checker catches with our own two numbers alone")


def test_renderbuffer_limit_never_exceeds_the_2d_texture_limit(patch_text):
    consts = _spoofed_int_constants(patch_text)
    assert consts["MaxRenderbufferSize"] <= consts["MaxTextureSize"], (
        "renderbuffer override exceeds the 2D texture override — the same "
        "self-contained impossibility, one pname over")


# ─── 2. Card fidelity: the measured D3D11 ANGLE answers ─────────────────────


def test_override_values_match_the_measured_reference(patch_text):
    """Value-for-value against the PS-440 measurement (RTX 3060, D3D11 ANGLE).

    Written out per-parameter ON PURPOSE, the way PS-22's WebGL2 reference is:
    a wrong-but-VALID value still reads as success unless each expectation is
    named. These are the nine limits the ticket measured contradicting the
    claimed card, plus MAX_VERTEX_TEXTURE_IMAGE_UNITS kept coherent with its
    fragment sibling at the claimed backend's 16.
    """
    consts = _spoofed_int_constants(patch_text)
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
    # The two float/array overrides, named out the same way.
    assert "constexpr int32_t kSpoofedMaxViewportDims[2] = {32767, 32767};" in patch_text, (
        "a desktop viewport is required for coherence with a desktop D3D11 card")
    assert "constexpr float kSpoofedPointSizeRange[2] = {1.0f, 1024.0f};" in patch_text


def test_every_override_is_reachable_through_its_gl_enum(patch_text):
    """A constant no switch arm returns is dead weight; a switch arm with no
    constant is a spoof that answers nothing. Both fail loudly here.

    The GL enums are written as hex literals with the GL name in the comment
    (the patch's own file includes no GL header), so this pairs each
    `case 0x....:` with the constant its arm returns and checks the comment
    names the parameter the enum actually is.
    """
    added = _added(patch_text, "third_party/blink/renderer/modules/webgl/gpu_fingerprint.cc")
    arms = re.findall(
        r"case (0x[0-9A-F]{4}):  // (GL_[A-Z_]+)\n      return kSpoofed(\w+);",
        added,
    )
    assert len(arms) == 8, f"expected 8 int-limit arms, found {len(arms)}: {arms}"
    seen_enums: set[str] = set()
    for enum, gl_name, const_name in arms:
        assert enum not in seen_enums, f"duplicate switch arm for {enum}"
        seen_enums.add(enum)
        assert f"constexpr int32_t kSpoofed{const_name}" in added
        # The comment is the only human-readable binding of enum to name, so
        # it must exist and must be the parameter's GL name.
        assert re.search(rf"{re.escape(enum)}\b.*// {re.escape(gl_name)}\b",
                         added), f"{enum} is not commented as {gl_name}"
    # The two array overrides must also be wired to their call sites, which
    # the patch lands in the getParameter switch itself (see the next test).
    assert "GetSpoofedViewportDimsForFingerprint" in patch_text
    assert "GetSpoofedPointSizeRangeForFingerprint" in patch_text


# ─── 3. Arm scoping and page-visibility ──────────────────────────────────────


def test_limit_overrides_gate_on_the_windows_arm_only(patch_text):
    """The overrides fire only where the claimed backend is the measured one.

    The gate must require the DECLARED platform to be windows — the same
    string the identity spoofs resolve — so the linux arm (claiming a
    desktop-GL NVIDIA card) and the macos arm (claiming Apple silicon) keep
    the host's real limits rather than answering values invented for a
    backend they do not claim.
    """
    assert "bool IsWindowsGpuFingerprintActive()" in patch_text
    added = _added(patch_text, "third_party/blink/renderer/modules/webgl/gpu_fingerprint.cc")
    gate = re.search(
        r"bool IsWindowsGpuFingerprintActive\(\) \{\n(.*?)\n\}", added, re.S)
    assert gate, "IsWindowsGpuFingerprintActive has no readable body"
    assert 'GetDeclaredPlatform() == "windows"' in gate.group(1)
    # All three public override getters must consult that gate, not a weaker
    # check, so no pname can leak the override onto an unmeasured arm.
    for fn in ("GetSpoofedIntLimitForFingerprint",
               "GetSpoofedViewportDimsForFingerprint",
               "GetSpoofedPointSizeRangeForFingerprint"):
        body = re.search(rf"{fn}\([^)]*\) \{{\n(.*?)\n\}}", added, re.S)
        assert body, f"{fn} has no readable body"
        assert "IsWindowsGpuFingerprintActive()" in body.group(1), (
            f"{fn} does not gate on the windows arm")


def test_overrides_are_page_visible_only(patch_text):
    """The backend's real limits stay authoritative for GL itself.

    The hook lives in getParameter()'s case arms — the page-visible answer —
    and must not touch the internals that feed Blink's own validation
    (`max_texture_size_` is read straight off ContextGL for texture-size
    checks) or the shared Get*Parameter helpers other pnames route through.
    A spoof of those would let a page ask for a 16384 texture without the
    backend ever being able to say no, which is a different bug.
    """
    # The getParameter switch arms are hooked (this is where the answers live).
    assert "GetSpoofedIntLimitForFingerprint(static_cast<uint32_t>(pname))" in patch_text
    # The internal validation read is untouched: it appears in the patch, if
    # at all, only as unchanged context — never added or removed.
    for line in ("ContextGL()->GetIntegerv(GL_MAX_TEXTURE_SIZE, &max_texture_size_);",
                 "ScriptValue WebGLRenderingContextBase::GetIntParameter("):
        hits = [l for l in patch_text.splitlines()
                if (l.startswith("+") or l.startswith("-")) and line in l]
        assert not hits, f"the patch rewrites an internal-validation line: {hits}"


def test_the_limits_hook_sits_inside_the_getparameter_switch(patch_text):
    """The new case arms must be the switch's own, not a helper rewrite.

    Asserted by hunk context: the hunk that adds GetSpoofedIntLimitForFingerprint
    must carry `case GL_MAX_TEXTURE_SIZE:` as context or an added case label,
    and the `case GL_PACK_ALIGNMENT:` arm that follows the block in the real
    switch must appear as its trailing context — so the hook provably landed
    inside WebGLRenderingContextBase::getParameter and nowhere else.
    """
    hunk = None
    for candidate in re.finditer(r"(?m)^@@ -\d+,\d+ \+\d+,\d+ @@.*?(?=^@@ -|\Z)",
                                 _section(patch_text,
                                          "third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc"),
                                 re.S):
        if "GetSpoofedIntLimitForFingerprint" in candidate.group(0):
            hunk = candidate.group(0)
            break
    assert hunk, "no getParameter hunk adds GetSpoofedIntLimitForFingerprint"
    # Strip the diff markers so the switch's own context lines read as code.
    text = "\n".join(l[1:] if l[:1] in "+- " else l for l in hunk.splitlines())
    assert "case GL_MAX_TEXTURE_SIZE:" in text
    assert "case GL_PACK_ALIGNMENT:" in text
    assert "return GetIntParameter(script_state, pname);" in text


def test_identity_spoof_still_reads_process_global_state(patch_text):
    """The PS-218 premise survives the limits work unchanged.

    The identity pair and the limits both resolve from
    base::CommandLine::ForCurrentProcess() — process-global state — which is
    why covering another realm still needs no plumbing.
    """
    assert "kUnmaskedRendererWebgl" in patch_text
    assert "kUnmaskedVendorWebgl" in patch_text
    assert "base::CommandLine::ForCurrentProcess()" in patch_text
