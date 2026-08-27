"""PS-54 — appending to a hardware list must not move an existing profile.

THE DEFECT. Every seeded hardware pick was ``pool[seed % len(pool)]``. The
divisor is the list's length, so appending one entry changed the divisor and
re-indexed a large share of EXISTING profiles onto different hardware, under
their live cookie jars. The live divisors made it sharp: ``IOS_PRESETS`` is 2, so
one new iPhone re-indexed roughly two-thirds of iOS profiles.

WHAT "EVERY" RANGES OVER. That sentence is a universal, and an earlier revision
of this file stated it while covering only some of the pools — a review falsified
it by finding two live ones with no coverage here at all. The enumerated set of
seven pick sites, and the sweep that derives it, live in the
``hardware_generation`` module docstring; this file is the measurement half, and
there is a section below per site. If you add a pick site, it needs both.

HOW THIS FILE TESTS IT, AND WHY THAT SHAPE. The ticket is explicit that a test
asserting "the code has a stability mechanism" passes against an implementation
that still re-indexes. So nothing here greps for a mechanism. Every test below
does the same four steps the ticket asks for:

    1. take a set of existing profiles,
    2. RECORD what each one presents,
    3. APPEND an entry to the list — the real list, by patching the module the
       way a maintainer's commit would edit it,
    4. RE-RECORD and demand the values are identical.

The JS pickers are measured by EXECUTING the emitted extension under node and
reading what a page would see, not by inspecting the generated source — the
in-tree precedent from ``test_gpu_ext.py``'s ``_probe``. A source-level check
would pass on a file that merely mentions a generation while still dividing by
the whole array.

Each list also has a NON-INERTNESS test. "Nothing moved" is trivially satisfiable
by an append that nobody can ever be picked onto, which would be a broken product
(the curated lists exist to be refreshed). So for every list we also assert a
profile of the NEW generation can actually reach the new entry.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from src.models.hardware_generation import (
    CURRENT_HARDWARE_GENERATION,
    normalize_generation,
    visible_entries,
)
from src.models.profile import Profile
from src.services.browser import device_ext, gpu_ext
from src.services.browser.device_ext import build_device_extension
from src.services.browser.device_presets import (
    ANDROID_PRESETS,
    ANDROID_TOUCH_POINTS,
    IOS_PRESETS,
    DevicePreset,
    TouchPointsEntry,
    pick_preset,
    pick_touch_points,
    presets_for,
)
from src.services.browser.engine_platform import engine_platform_for
from src.services.browser.gpu_ext import build_gpu_extension
from src.services.browser.resolution import (
    DESKTOP_RESOLUTIONS,
    ResolutionEntry,
    resolve_resolution,
)

# A spread of seeds standing in for existing profiles. Deliberately wider than
# any pool's length so every residue class is represented — with 60 seeds against
# a 2-entry pool, a divisor change from 2 to 3 cannot hide in an unsampled gap.
SEEDS = list(range(60))

# The generation an appended entry would be tagged with. The production
# constant is 1 today (PS-183 bumped it from 0 when it widened MAC_GPUS); a
# maintainer adding hardware bumps it again and tags the new entries with the
# bumped value, so the append these tests simulate is `since=+1` whatever the
# constant currently is — which is why this is derived rather than hardcoded.
NEXT_GEN = CURRENT_HARDWARE_GENERATION + 1


# --------------------------------------------------------------------------
# resolution.py — DESKTOP_RESOLUTIONS
# --------------------------------------------------------------------------

def test_appending_a_resolution_moves_no_existing_profile(monkeypatch):
    before = {s: resolve_resolution("auto", s, 0) for s in SEEDS}

    # A maintainer appends a resolution, tagged with the bumped generation.
    monkeypatch.setattr(
        "src.services.browser.resolution.DESKTOP_RESOLUTIONS",
        DESKTOP_RESOLUTIONS + [ResolutionEntry(3840, 2160, since=NEXT_GEN)],
    )

    after = {s: resolve_resolution("auto", s, 0) for s in SEEDS}
    assert after == before


def test_appended_resolution_is_reachable_by_a_new_profile(monkeypatch):
    # The counterweight to the test above: an append nobody can be picked onto
    # would satisfy "nothing moved" while making the curated list unmaintainable.
    monkeypatch.setattr(
        "src.services.browser.resolution.DESKTOP_RESOLUTIONS",
        DESKTOP_RESOLUTIONS + [ResolutionEntry(3840, 2160, since=NEXT_GEN)],
    )
    reached = {resolve_resolution("auto", s, NEXT_GEN) for s in SEEDS}
    assert (3840, 2160) in reached
    # ...and it is invisible to the old generation, which is the other half.
    assert (3840, 2160) not in {resolve_resolution("auto", s, 0) for s in SEEDS}


def test_inserting_a_resolution_mid_list_moves_no_existing_profile(monkeypatch):
    # The filter is by tag, not by position, so a new entry does not have to go
    # at the end for old profiles to be safe. This is what keeps these lists
    # maintainable in a readable order rather than in append-only order.
    grown = list(DESKTOP_RESOLUTIONS)
    grown.insert(3, ResolutionEntry(3840, 2160, since=NEXT_GEN))
    before = {s: resolve_resolution("auto", s, 0) for s in SEEDS}
    monkeypatch.setattr(
        "src.services.browser.resolution.DESKTOP_RESOLUTIONS", grown
    )
    assert {s: resolve_resolution("auto", s, 0) for s in SEEDS} == before


# --------------------------------------------------------------------------
# device_presets.py — ANDROID_PRESETS / IOS_PRESETS
# --------------------------------------------------------------------------

def _preset_like(key: str, since: int) -> DevicePreset:
    return DevicePreset(
        key=key, os_type="ios", label=key,
        user_agent_template="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)",
        width=402, height=874, dpr=3.0,
        device_memory=8, hardware_concurrency=6,
        platform="iPhone", model="iPhone", since=since,
    )


def test_appending_an_iphone_moves_no_existing_ios_profile(monkeypatch):
    # The sharpest case in the whole ticket: IOS_PRESETS is 2 long, so appending
    # one iPhone moved the divisor 2 -> 3 and re-indexed ~2/3 of iOS profiles.
    before = {s: pick_preset(s, "ios", 0).key for s in SEEDS}

    monkeypatch.setattr(
        "src.services.browser.device_presets.IOS_PRESETS",
        IOS_PRESETS + [_preset_like("iphone-16", NEXT_GEN)],
    )

    after = {s: pick_preset(s, "ios", 0).key for s in SEEDS}
    assert after == before
    assert "iphone-16" not in set(after.values())


def test_appending_an_android_moves_no_existing_android_profile(monkeypatch):
    before = {s: pick_preset(s, "android", 0).key for s in SEEDS}

    appended = DevicePreset(
        key="pixel-9", os_type="android", label="Pixel 9",
        user_agent_template=(
            "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/{chrome} Mobile Safari/537.36"
        ),
        width=412, height=923, dpr=2.625,
        device_memory=12, hardware_concurrency=8,
        platform="Android", model="Pixel 9", since=NEXT_GEN,
    )
    monkeypatch.setattr(
        "src.services.browser.device_presets.ANDROID_PRESETS",
        ANDROID_PRESETS + [appended],
    )

    assert {s: pick_preset(s, "android", 0).key for s in SEEDS} == before


def test_appended_preset_is_reachable_by_a_new_profile(monkeypatch):
    monkeypatch.setattr(
        "src.services.browser.device_presets.IOS_PRESETS",
        IOS_PRESETS + [_preset_like("iphone-16", NEXT_GEN)],
    )
    reached = {pick_preset(s, "ios", NEXT_GEN).key for s in SEEDS}
    assert "iphone-16" in reached
    assert presets_for("ios", NEXT_GEN)[-1].key == "iphone-16"
    assert "iphone-16" not in {p.key for p in presets_for("ios", 0)}


# --------------------------------------------------------------------------
# gpu_ext.py — the emitted extension, measured by EXECUTION
# --------------------------------------------------------------------------

_GPU_READ = r"""
// Stub the two context ctors, run the emitted extension against them, then CALL
// the patched getParameter — what a page actually sees, not what the file says.
function makeRealm() {
  function WebGLRenderingContext() {}
  function WebGL2RenderingContext() {}
  for (const C of [WebGLRenderingContext, WebGL2RenderingContext]) {
    C.prototype.getParameter = function () { return "HOST_VALUE_NOT_SPOOFED"; };
    C.prototype.getExtension = function () { return null; };
    C.prototype.getSupportedExtensions = function () { return ["HOST_EXT"]; };
    C.prototype.getShaderPrecisionFormat = function () { return null; };
  }
  return { WebGLRenderingContext, WebGL2RenderingContext };
}
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const G = makeRealm();
const sandbox = { self: G, window: G, ...G };
require('vm').createContext(sandbox);
require('vm').runInContext(src, sandbox);
const gl = new G.WebGL2RenderingContext();
console.log(JSON.stringify({
  unmaskedVendor: gl.getParameter(0x9245),
  unmaskedRenderer: gl.getParameter(0x9246),
}));
"""

# The append a maintainer's commit would make to a GPU pool: one more card,
# tagged with the bumped generation. Anchored on the last entry's renderer string
# so the patch fails loudly if the list is edited, rather than silently
# appending nothing.
#
# MEASURED ON THE LINUX POOL, NOT THE WINDOWS ONE (PS-161). The invariant under
# test — appending to a pool must not re-index any existing profile — lives in
# the shared `visible()`/`pick()` pair and is arm-agnostic, so any pool can
# carry it. The arm is not: windows is now an engine-authored-identity arm, so
# `gpu_ext` deliberately does not write UNMASKED_VENDOR/RENDERER there and a
# page reads the engine's own value instead. Probing windows would therefore
# read the harness's fall-through sentinel on BOTH sides of the append and
# "pass" while measuring nothing at all — which is precisely what `_gpu_seen`'s
# own guard caught. linux keeps persona's authorship (the engine returns one
# identical SwiftShader string on every seed there, so deferring would breach
# mutual unlinkability), so the reading stays real.
#
# The appended entry is a fabricated card that exists only inside this
# monkeypatch, never in the shipped pool. It is deliberately built in the LINUX
# form — an ANGLE-over-Mesa string ending "OpenGL 4.6", not a Direct3D11 one —
# because a D3D11 string on a linux profile is an impossible value, and a test
# fixture that models an impossible append would not model a maintainer's real
# commit. The name is obviously synthetic so it can never be mistaken for one of
# the harvested tuples in tests/fixtures/linux-webgl-reference.md.
#
# APPENDED TO A PYTHON LIST, NOT TO A JS STRING (PS-190). Until PS-190 the four
# GPU pools existed ONLY as JS array literals inside `gpu_ext._CONTENT_SCRIPT`,
# so `_append_gpu` had to patch the pool by string replacement, anchored on the
# last entry's renderer text. They are now tagged `gpu_ext.GpuEntry` records
# that the emitted JS is rendered from, so the append is an ordinary list
# append and the anchor — along with the "did the anchor still match?" assert
# that guarded it — is gone. The anchor is not merely unnecessary now, it was
# LOAD-BEARING in a way worth recording: it made this helper's reach a function
# of one pool's formatting, which is why the tagged-path tests below could only
# ever be written against LINUX_GPUS.
_APPENDED_GPU = "AMD Radeon RX 9999 TEST"
_APPENDED_LINUX_RENDERER = (
    "ANGLE (AMD, AMD Radeon RX 9999 TEST (radeonsi testonly ACO), OpenGL 4.6)"
)


def _gpu_seen(tmp_path, seed, generation, tag):
    """What a page in this profile actually reads for vendor/renderer.

    The arm is ``linux`` deliberately — see the note on ``_LINUX_GPUS_TAIL``.
    A windows probe would read the engine-authored fall-through on both sides
    of the append and measure nothing, which the sentinel guard below catches.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = pathlib.Path(
        build_gpu_extension(seed, "linux", str(tmp_path / f"{tag}{seed}"), generation, engine_platform=engine_platform_for("linux", "desktop"))
    )
    harness = d / "harness.js"
    harness.write_text(_GPU_READ, encoding="utf-8")
    out = subprocess.run(
        [node, str(harness), str(d / "gpu.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert "HOST_VALUE_NOT_SPOOFED" not in seen.values(), (
        "the extension did not patch getParameter, so this measured nothing"
    )
    return seen


def _append_gpu(monkeypatch):
    """Append a tagged card to LINUX_GPUS, the way a maintainer's commit would.

    A plain list append now that the pools are Python records — no string
    anchor, and nothing that can silently append nothing.
    """
    monkeypatch.setattr(
        gpu_ext, "LINUX_GPUS",
        gpu_ext.LINUX_GPUS + [
            gpu_ext.GpuEntry(
                unmasked_vendor="Google Inc. (AMD)",
                unmasked_renderer=_APPENDED_LINUX_RENDERER,
                since=NEXT_GEN,
            )
        ],
    )
    # The pools are rendered THROUGH the registry, so the registry is what the
    # emitted JS reads. Patching only the module-level name would leave
    # GPU_POOLS pointing at the original list and append nothing — the modern
    # form of the failure the old tail anchor existed to catch.
    monkeypatch.setitem(gpu_ext.GPU_POOLS, "LINUX_GPUS", gpu_ext.LINUX_GPUS)


# A handful of seeds rather than all 60: each one spawns a node process. Chosen
# to cover every residue of the live 8-entry LINUX_GPUS pool, so a divisor
# change from 8 to 9 must move at least one of them.
_GPU_SEEDS = [1, 2, 3, 4, 5, 6, 7, 11, 42, 0xABCDEF]


def test_appending_a_gpu_moves_no_existing_profile(tmp_path, monkeypatch):
    before = {s: _gpu_seen(tmp_path, s, 0, "before") for s in _GPU_SEEDS}
    _append_gpu(monkeypatch)
    after = {s: _gpu_seen(tmp_path, s, 0, "after") for s in _GPU_SEEDS}

    assert after == before
    assert not any(
        _APPENDED_GPU in v["unmaskedRenderer"] for v in after.values()
    )


def test_appended_gpu_is_reachable_by_a_new_profile(tmp_path, monkeypatch):
    _append_gpu(monkeypatch)
    # Scan until found rather than sampling a fixed handful: with a 6-entry pool
    # a small fixed sample can miss the new entry by chance, which would make
    # this test's verdict depend on which seeds happen to be listed. The scan is
    # bounded and exits at the first hit, so the usual cost is a few node runs.
    for s in range(60):
        if _APPENDED_GPU in _gpu_seen(tmp_path, s, NEXT_GEN, "new")["unmaskedRenderer"]:
            return
    pytest.fail(
        "no seed in 0..59 was picked onto the appended GPU at the new "
        "generation — the append is inert and the list cannot be maintained"
    )


# --------------------------------------------------------------------------
# PS-190 — the GPU pools' UNTAGGED-append tripwire, and the pool-CLASS guard
# --------------------------------------------------------------------------
#
# WHAT WAS MISSING, PRECISELY. The two GPU tests above are genuinely
# behavioural, but `_append_gpu` appends a `since=NEXT_GEN` entry — a TAGGED
# one. So they prove the TAGGED path works and can never catch the edit a
# maintainer actually writes when widening a pool, which is an append with no
# `since` at all. Measured on the shipped tree before this section existed: an
# untagged append to `MAC_GPUS` (pool 2 -> 3) re-indexed 9 of 12 generation-0
# seeds onto a different graphics card — 75% of EXISTING profiles — and the
# full suite went GREEN on that commit. That is the same linkage event PS-54
# closed, arriving through the one pool family PS-54's guards did not cover.
#
# WHY A FROZEN CENSUS RATHER THAN `all(e.since == 0)`. The obvious guard is the
# one the python pools already carried, and it is POLARITY-INVERTED against the
# procedure it exists to protect: it goes GREEN on the unsafe edit (an untagged
# append IS `since == 0`, so it satisfies the assertion) and RED on the
# DOCUMENTED CORRECT one (bump the constant, tag the new entry `since=1` — now
# `all(e.since == 0)` is false and the suite fails a maintainer who followed
# the procedure exactly). Verified both ways by execution, not by reading.
#
# So the invariant below is stated the way it actually needs to hold across a
# bump: generation 0's VISIBLE pool must be the list as it SHIPPED — contents,
# ORDER, and therefore divisor — pinned against a frozen census, plus "no
# shipped entry's `since` exceeds CURRENT_HARDWARE_GENERATION". Both halves
# survive a bump: tagging a new entry `since=1` leaves generation 0's visible
# pool untouched (GREEN, correctly), while an UNTAGGED append lands in
# generation 0's pool and changes its divisor (RED, correctly).
#
# WHY THE CENSUS IS RENDERER STRINGS AND NOT A COUNT. A count catches an append
# but not a SUBSTITUTION or a REORDER, and both of those re-index exactly as
# hard: `pool[seed % len(pool)]` depends on order, not merely on length. The
# census pins the identity and the position of every shipped card.
_GPU_GENERATION_ZERO_CENSUS: dict[str, tuple[str, ...]] = {
    "WIN_GPUS": (
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002487) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 (0x00002484) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x0000A7A1) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0, D3D11)",
    ),
    "MAC_GPUS": (
        "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
        "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)",
    ),
    "ANDROID_GPUS": (
        "ANGLE (Qualcomm, Adreno (TM) 730, OpenGL ES 3.2)",
        "ANGLE (Qualcomm, Adreno (TM) 660, OpenGL ES 3.2)",
        "ANGLE (ARM, Mali-G78 MP20, OpenGL ES 3.2)",
        "ANGLE (ARM, Mali-G710 MC10, OpenGL ES 3.2)",
    ),
    "LINUX_GPUS": (
        "ANGLE (AMD, AMD Radeon RX 6800 (radeonsi navi21 ACO), OpenGL 4.6)",
        "ANGLE (AMD, AMD Radeon RX 7900 XTX (radeonsi navi31 ACO), OpenGL 4.6)",
        "ANGLE (AMD, AMD Radeon RX 7600 (radeonsi navi33 ACO), OpenGL 4.6)",
        "ANGLE (AMD, AMD Radeon RX 6600 (radeonsi navi23 LLVM 18.1.6), OpenGL 4.6)",
        "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)",
        "ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2), OpenGL 4.6)",
        "ANGLE (Intel, Mesa Intel(R) HD Graphics 530 (SKL GT2), OpenGL 4.6)",
        "ANGLE (Intel, Mesa Intel(R) UHD Graphics 770 (ADL-S GT1), OpenGL 4.6)",
    ),
}


def test_the_gpu_census_covers_every_shipped_pool():
    # AC4, and the half that makes the tests below cover the pool CLASS rather
    # than the four pools someone thought of. A FIFTH pool added to
    # gpu_ext.GPU_POOLS later — a new arm's cards — fails HERE until its
    # shipped contents are pinned, so it cannot be added silently and then
    # widened untagged.
    #
    # This is the completeness claim PS-176 failed audit twice for making with
    # a probe that could not have matched the remaining case, so it is asserted
    # in BOTH directions: every registered pool has a census, and every census
    # names a registered pool (a stale key for a deleted pool would otherwise
    # sit here forever, quietly covering nothing).
    assert set(gpu_ext.GPU_POOLS) == set(_GPU_GENERATION_ZERO_CENSUS), (
        "GPU_POOLS and the generation-0 census disagree about which pools "
        "exist — a new pool must be pinned here before it ships, and a "
        "removed one must be unpinned"
    )
    # And the registry is what the product RENDERS FROM, not a list beside it:
    # if these came apart, the census could cover a pool the extension never
    # emits while the emitted one went unguarded.
    for name, pool in gpu_ext.GPU_POOLS.items():
        assert getattr(gpu_ext, name) is pool, (
            f"{name} in GPU_POOLS is not the module-level {name} the "
            "extension renders from"
        )


def test_an_untagged_gpu_append_really_does_re_index_existing_profiles(
    tmp_path, monkeypatch
):
    # AC3 — the BEHAVIOURAL half, and the control that stops the census guard
    # from being a test of its own fixture.
    #
    # READ THE CLAIM CAREFULLY. It is NOT "an untagged append moves nobody" —
    # that is false and always will be, because nothing in the generation
    # mechanism can protect a profile from an append it can SEE. An untagged
    # entry is `since=0`, so it lands squarely in generation 0's visible pool
    # and re-indexes it; that is the hazard, not a bug to be fixed here.
    #
    # So what is asserted is the LINK the tripwire rests on, in both
    # directions: (a) an untagged append genuinely moves existing profiles when
    # MEASURED THROUGH A PAGE, and (b) the census guard rejects exactly that
    # pool state. Without (a) the guard could be pinning a census that no
    # longer corresponds to anything a page reads; without (b) the measurement
    # would be a finding nobody acts on.
    #
    # linux, NEVER windows — windows is engine-authored, so this extension
    # deliberately does not write the identity pair there and the harness would
    # read its fall-through sentinel on BOTH sides of the append and "pass"
    # while measuring nothing. `_gpu_seen` asserts the sentinel is absent, so
    # that trap fails loudly rather than passing quietly.
    before = {s: _gpu_seen(tmp_path, s, 0, "ubefore") for s in _GPU_SEEDS}

    # The edit a maintainer widening the pool actually writes: NO `since` tag.
    untagged = gpu_ext.GpuEntry(
        unmasked_vendor="Google Inc. (AMD)",
        unmasked_renderer=_APPENDED_LINUX_RENDERER,
    )
    assert untagged.since == 0, (
        "an untagged entry must default to generation 0 — that default is "
        "what makes this append dangerous, and what the census guard catches"
    )
    monkeypatch.setattr(gpu_ext, "LINUX_GPUS", gpu_ext.LINUX_GPUS + [untagged])
    monkeypatch.setitem(gpu_ext.GPU_POOLS, "LINUX_GPUS", gpu_ext.LINUX_GPUS)

    after = {s: _gpu_seen(tmp_path, s, 0, "uafter") for s in _GPU_SEEDS}

    # (a) It really moves EXISTING (generation-0) profiles, page-observably.
    moved = [s for s in _GPU_SEEDS if after[s] != before[s]]
    assert moved, (
        "an untagged append to LINUX_GPUS moved NO profile across "
        f"{len(_GPU_SEEDS)} seeds — the divisor cannot have changed, so this "
        "test is no longer measuring the hazard the census guard exists for"
    )

    # (b) And the guard rejects this exact pool state. Asserted by CALLING the
    # guard, so the two cannot drift apart: if someone weakens the census, this
    # fails here rather than leaving a green suite over a re-indexing pool.
    with pytest.raises(AssertionError):
        test_generation_zero_sees_every_gpu_pool_exactly_as_it_shipped()


def test_a_tagged_gpu_append_moves_nobody_and_keeps_the_guard_green(
    tmp_path, monkeypatch
):
    # The counterweight, and the half that proves the tripwire is not simply
    # "any edit fails". The DOCUMENTED CORRECT procedure — tag the new entry
    # with the bumped generation — leaves every existing profile's identity
    # untouched AND leaves the census guard green, so a maintainer who follows
    # the procedure is not fought by the suite.
    #
    # This is the polarity the old `all(e.since == 0)` latch got backwards: it
    # went green on the untagged append above and RED on this one.
    before = {s: _gpu_seen(tmp_path, s, 0, "tbefore") for s in _GPU_SEEDS}
    _append_gpu(monkeypatch)
    after = {s: _gpu_seen(tmp_path, s, 0, "tafter") for s in _GPU_SEEDS}

    assert after == before
    assert not any(
        _APPENDED_GPU in v["unmaskedRenderer"] for v in after.values()
    )
    # The guard stays green on the correct edit — no exception.
    test_generation_zero_sees_every_gpu_pool_exactly_as_it_shipped()


def test_generation_zero_sees_every_gpu_pool_exactly_as_it_shipped():
    # THE TRIPWIRE. An UNTAGGED append to any GPU pool lands in generation 0's
    # visible pool, changing its length and therefore the divisor of every
    # existing profile's pick — so it fails here. A correctly TAGGED append
    # (since = the bumped CURRENT_HARDWARE_GENERATION) is invisible to
    # generation 0 and leaves this untouched, so the documented procedure stays
    # green. Reordering or substituting a shipped card fails too: the census
    # pins order and identity, not just the count.
    for name, pool in gpu_ext.GPU_POOLS.items():
        shipped = tuple(
            e.unmasked_renderer
            for e in gpu_ext.gpu_pool_for_generation(pool, 0)
        )
        # An unpinned pool is a MISSING GUARD, not a KeyError. Say so, because
        # the maintainer who trips this is the one adding a fifth pool and the
        # remedy is not obvious from a bare key name.
        assert name in _GPU_GENERATION_ZERO_CENSUS, (
            f"{name} is registered in GPU_POOLS but has no entry in "
            "_GPU_GENERATION_ZERO_CENSUS, so nothing is guarding it against an "
            "untagged append. Pin its shipped contents there (renderer "
            "strings, in shipped order) in the same commit that adds the pool."
        )
        assert shipped == _GPU_GENERATION_ZERO_CENSUS[name], (
            f"generation 0's visible {name} is no longer the pool as it "
            f"shipped. An existing profile picks with "
            f"`pool[seed % len(visible(pool, 0))]`, so this re-indexes live "
            f"profiles onto different graphics cards under their session "
            f"cookies. If you are ADDING a card: bump "
            f"CURRENT_HARDWARE_GENERATION and tag the new entry "
            f"`since=<that number>` — see models/hardware_generation.py."
        )


def test_no_shipped_gpu_entry_is_tagged_beyond_the_current_generation():
    # The other half of the generalised latch, and the one that survives a
    # bump. An entry tagged with a generation NOBODY has been minted into yet
    # is unreachable until the constant catches up — the "tag first, bump
    # never" mistake the module docstring warns about, which silently makes a
    # newly-added card dead rather than loudly failing.
    for name, pool in gpu_ext.GPU_POOLS.items():
        for e in pool:
            assert 0 <= e.since <= CURRENT_HARDWARE_GENERATION, (
                f"{name} has an entry tagged since={e.since}, but "
                f"CURRENT_HARDWARE_GENERATION is "
                f"{CURRENT_HARDWARE_GENERATION} — bump the constant in the "
                f"same commit, or nothing can ever be picked onto it: "
                f"{e.unmasked_renderer}"
            )


# --------------------------------------------------------------------------
# device_ext.py — the screen pool that actually drives an "auto" profile
# --------------------------------------------------------------------------
#
# This is the fourth pick site, and it is not one of the three the ticket names.
# It carries its own copy of the desktop resolution list and its own
# `fits[h(...) % fits.length]`, and it is the one that sets what `screen.width`
# reports for an "auto" profile — process.py passes resolution=None in that case,
# so the JS pool, not resolution.py's, decides. Fixing only the named three would
# have left the most visible value of the four still re-rolling.

_SCREEN_READ = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const sandbox = {};
require('vm').createContext(sandbox);
require('vm').runInContext(
  "globalThis.self = globalThis; globalThis.window = globalThis; " +
  "globalThis.top = globalThis; " +
  // A small window extent so every entry in the pool passes the `fits` filter
  // and the modulo is taken over the whole visible pool.
  "globalThis.outerWidth = 800; globalThis.innerWidth = 800; " +
  "globalThis.outerHeight = 600; globalThis.innerHeight = 600; " +
  "globalThis.screen = { width: 1, height: 1, availWidth: 1, availHeight: 1," +
  " colorDepth: 1, pixelDepth: 1 };",
  sandbox
);
require('vm').runInContext(src, sandbox);
console.log(JSON.stringify(require('vm').runInContext(
  "({ width: screen.width, height: screen.height })", sandbox
)));
"""

_RES_TAIL = "[1680, 1050, 0], [1920, 1200, 0], [2560, 1080, 0], [2560, 1440, 0],"
_RES_APPENDED = _RES_TAIL + " [3840, 2160, %d]," % NEXT_GEN


def _screen_seen(tmp_path, seed, generation, tag):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = pathlib.Path(
        build_device_extension(seed, str(tmp_path / f"{tag}{seed}"), generation)
    )
    harness = d / "harness.js"
    harness.write_text(_SCREEN_READ, encoding="utf-8")
    out = subprocess.run(
        [node, str(harness), str(d / "device.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert seen["width"] != 1, (
        "the extension did not patch screen, so this measured nothing"
    )
    return (seen["width"], seen["height"])


def _append_screen_res(monkeypatch):
    assert _RES_TAIL in device_ext._CONTENT_SCRIPT, (
        "RES tail anchor no longer matches — re-derive it before trusting this "
        "test, which otherwise silently appends nothing"
    )
    monkeypatch.setattr(
        device_ext, "_CONTENT_SCRIPT",
        device_ext._CONTENT_SCRIPT.replace(_RES_TAIL, _RES_APPENDED),
    )


_SCREEN_SEEDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 42]


def test_appending_a_screen_resolution_moves_no_existing_profile(
    tmp_path, monkeypatch
):
    before = {s: _screen_seen(tmp_path, s, 0, "before") for s in _SCREEN_SEEDS}
    _append_screen_res(monkeypatch)
    after = {s: _screen_seen(tmp_path, s, 0, "after") for s in _SCREEN_SEEDS}

    assert after == before
    assert (3840, 2160) not in set(after.values())


def test_appended_screen_resolution_is_reachable_by_a_new_profile(
    tmp_path, monkeypatch
):
    _append_screen_res(monkeypatch)
    # Scan until found rather than sampling a fixed handful — same reason as the
    # GPU case above: a fixed sample can miss the new entry by chance and make
    # the verdict depend on which seeds happen to be listed.
    for s in range(60):
        if _screen_seen(tmp_path, s, NEXT_GEN, "new") == (3840, 2160):
            return
    pytest.fail(
        "no seed in 0..59 was picked onto the appended resolution at the new "
        "generation — the append is inert and the list cannot be maintained"
    )


# --------------------------------------------------------------------------
# device_ext.py — the cores/RAM pool behind hardwareConcurrency + deviceMemory
# --------------------------------------------------------------------------
#
# The fifth and sixth pick sites. device_ext.py carried the (cores, GB-RAM) pool
# TWICE as hand-maintained JS literals: once in the page-realm IIFE (`HCMEM`) and
# once inside `applyHwPatch` (`P`), the worker-realm twin. Both were
# `pool[h(...) % pool.length]` over a 6-entry list, so appending one pair
# re-indexed a large majority of existing profiles — measured at 82% here before
# the fix, on values a page reads directly as navigator.hardwareConcurrency and
# navigator.deviceMemory. That is the same sharpness as the IOS_PRESETS 2 -> 3
# case the ticket leads with, not a large-pool tail perturbation.
#
# ONE PRECISION THAT DECIDES THE FIX'S SHAPE: `applyHwPatch` runs AFTER the
# top-level IIFE and re-defines both properties from its own pool, so `P` is the
# divisor the page actually ends up with and `HCMEM` is effectively shadowed.
# Fixing only `HCMEM` would change nothing observable — which is exactly why
# these tests read the value out of an executed extension rather than checking
# that the source mentions a generation. Both realms now render from the single
# Python-side CORES_MEMORY list, so they cannot drift apart by hand either.

_HW_READ = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const sandbox = {};
require('vm').createContext(sandbox);
require('vm').runInContext(
  "globalThis.self = globalThis; globalThis.window = globalThis; " +
  "globalThis.top = globalThis; " +
  "globalThis.outerWidth = 800; globalThis.innerWidth = 800; " +
  "globalThis.outerHeight = 600; globalThis.innerHeight = 600; " +
  "globalThis.screen = { width: 1, height: 1, availWidth: 1, availHeight: 1," +
  " colorDepth: 1, pixelDepth: 1 };" +
  // Sentinels: if the extension fails to patch, we read these back and fail
  // loudly rather than silently comparing two identical unpatched values.
  "globalThis.navigator = { hardwareConcurrency: -1, deviceMemory: -1 };",
  sandbox
);
require('vm').runInContext(src, sandbox);
console.log(JSON.stringify(require('vm').runInContext(
  "({ cores: navigator.hardwareConcurrency, memory: navigator.deviceMemory })",
  sandbox
)));
"""

# The append a maintainer's commit would make: one more plausible modern pair,
# tagged with the bumped generation. Appended to the REAL CORES_MEMORY list, the
# way a maintainer would edit it.
_APPENDED_PAIR = device_ext.CoresMemoryEntry(24, 32, since=NEXT_GEN)


def _hw_seen(tmp_path, seed, generation, tag):
    """What a page actually reads for (hardwareConcurrency, deviceMemory)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = pathlib.Path(
        build_device_extension(seed, str(tmp_path / f"{tag}{seed}"), generation)
    )
    harness = d / "hwharness.js"
    harness.write_text(_HW_READ, encoding="utf-8")
    out = subprocess.run(
        [node, str(harness), str(d / "device.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert seen["cores"] != -1 and seen["memory"] != -1, (
        "the extension did not patch navigator, so this measured nothing"
    )
    return (seen["cores"], seen["memory"])


def _append_cores_memory(monkeypatch):
    monkeypatch.setattr(
        device_ext, "CORES_MEMORY", device_ext.CORES_MEMORY + [_APPENDED_PAIR]
    )


# Fewer seeds than SEEDS because each one spawns a node process; still wider than
# the 6-entry pool's length, so every residue class is represented and a 6 -> 7
# divisor change cannot hide in an unsampled gap.
_HW_SEEDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 42, 4154289201]


def test_appending_a_cores_memory_pair_moves_no_existing_profile(
    tmp_path, monkeypatch
):
    # Measured end to end: build the extension, EXECUTE it, read what a page
    # would see for navigator.hardwareConcurrency / deviceMemory, append to the
    # real pool, re-read, demand identical values. Before the fix this moved 82%.
    before = {s: _hw_seen(tmp_path, s, 0, "hwbefore") for s in _HW_SEEDS}
    _append_cores_memory(monkeypatch)
    after = {s: _hw_seen(tmp_path, s, 0, "hwafter") for s in _HW_SEEDS}

    assert after == before
    # And nobody was moved ONTO the new pair — the positive form of the same
    # claim, which catches a filter that lets the entry through at generation 0.
    assert 24 not in {cores for cores, _ in after.values()}


def test_appended_cores_memory_pair_is_reachable_by_a_new_profile(
    tmp_path, monkeypatch
):
    # The counterweight: an append nobody can ever be picked onto would satisfy
    # "nothing moved" while making the list unmaintainable.
    _append_cores_memory(monkeypatch)
    for s in range(60):
        if _hw_seen(tmp_path, s, NEXT_GEN, "hwnew")[0] == 24:
            return
    pytest.fail(
        "no seed in 0..59 was picked onto the appended cores/RAM pair at the "
        "new generation — the append is inert and the list cannot be maintained"
    )


def test_worker_realm_reports_the_same_machine_as_the_page(tmp_path, monkeypatch):
    # The two realms render from ONE list, so they cannot disagree. A page/worker
    # mismatch in cores or RAM is itself a detection tell, and the duplicated
    # literals these replaced had to be kept in sync by eye.
    #
    # BUILT AT THE NEW GENERATION, WITH AN APPEND, DELIBERATELY. At generation 0
    # a stale hard-coded literal and a correctly-filtered pool are byte-identical,
    # so a generation-0 version of this test passes against a realm that was never
    # wired to CORES_MEMORY at all — it did, during falsification. Appending and
    # building at NEXT_GEN is what makes the two forms diverge: the filtered pool
    # has the new pair, a stale literal does not.
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    _append_cores_memory(monkeypatch)
    d = pathlib.Path(
        build_device_extension(42, str(tmp_path / "realms"), NEXT_GEN)
    )
    js = (d / "device.js").read_text(encoding="utf-8")
    pools = re.findall(r"var (?:HCMEM|P)\s*=\s*(\[\[.*?\]\])", js)
    assert len(pools) == 2, (
        f"expected both realm pools in the emitted script, found {len(pools)}"
    )
    page, worker = json.loads(pools[0]), json.loads(pools[1])
    assert page == worker, (
        "the page and worker realms rendered DIFFERENT cores/RAM pools — one of "
        "them is not reading CORES_MEMORY, so they will drift apart by hand"
    )
    # Both must actually track the list, not merely agree with each other: two
    # identical stale literals would satisfy the equality above.
    assert list(_APPENDED_PAIR.pair) in page, (
        "neither realm picked up the appended pair at the new generation — the "
        "pools are hard-coded rather than rendered from CORES_MEMORY"
    )


def test_every_shipped_cores_memory_entry_is_generation_zero():
    # Same promise as the other lists: generation 0's pool must be the entire
    # shipped list, in shipped order, hence the shipped divisor.
    assert all(e.since == 0 for e in device_ext.CORES_MEMORY)
    assert device_ext.cores_memory_for_generation(0) == [
        e.pair for e in device_ext.CORES_MEMORY
    ]


# --------------------------------------------------------------------------
# device_presets.py — Android maxTouchPoints
# --------------------------------------------------------------------------
#
# The seventh pick site, named by nobody: `(5, 10)[seed % 2]` in process.py.
# A bare tuple literal rather than a curated list, which is why it reads as
# incidental — but the divisor is still a pool length and the value is still
# page-visible hardware (navigator.maxTouchPoints). Measured: appending a third
# plausible value moved 50% of Android profiles. iOS is a constant 5 with no
# pool, so it has nothing to re-index.


def test_appending_a_touch_points_value_moves_no_existing_android_profile(
    monkeypatch,
):
    before = {s: pick_touch_points(s, 0) for s in SEEDS}
    monkeypatch.setattr(
        "src.services.browser.device_presets.ANDROID_TOUCH_POINTS",
        ANDROID_TOUCH_POINTS + [TouchPointsEntry(2, since=NEXT_GEN)],
    )
    after = {s: pick_touch_points(s, 0) for s in SEEDS}

    assert after == before
    assert 2 not in set(after.values())


def test_appended_touch_points_value_is_reachable_by_a_new_profile(monkeypatch):
    monkeypatch.setattr(
        "src.services.browser.device_presets.ANDROID_TOUCH_POINTS",
        ANDROID_TOUCH_POINTS + [TouchPointsEntry(2, since=NEXT_GEN)],
    )
    assert any(pick_touch_points(s, NEXT_GEN) == 2 for s in SEEDS), (
        "no seed was picked onto the appended touch-points value at the new "
        "generation — the append is inert"
    )


def test_every_shipped_touch_points_entry_is_generation_zero():
    assert all(e.since == 0 for e in ANDROID_TOUCH_POINTS)
    assert visible_entries(ANDROID_TOUCH_POINTS, 0) == ANDROID_TOUCH_POINTS


# --------------------------------------------------------------------------
# The migration: an existing profile carries no generation and must not move
# --------------------------------------------------------------------------

def test_profile_without_a_stored_generation_reads_as_the_original_lists():
    # A profile written before this field exists has hardware_generation_value
    # None. It must read as generation 0 — the lists as they shipped — because
    # that is what it has always presented. Defaulting to the CURRENT generation
    # instead would re-roll every profile on the machine at once.
    legacy = Profile(name="acme-bank")
    assert legacy.hardware_generation_value is None
    assert legacy.hardware_generation == 0


def test_legacy_profiles_present_what_they_always_did_after_an_append(
    monkeypatch,
):
    # The end-to-end statement of the ticket's "Wanted", through the model
    # rather than through raw seeds: profiles that predate generations keep
    # their exact screen and phone across a list append.
    #
    # MANY NAMES, NOT ONE. A single profile is a bad witness here: its seed
    # lands on one residue, and a re-indexing bug that moves two-thirds of
    # profiles still leaves one-third sitting still. A single-name version of
    # this test PASSED against a deliberately reverted picker during
    # falsification — it can pass by luck. With 40 names the ~2/3 of iOS
    # profiles an IOS_PRESETS append re-indexes cannot all miss.
    legacy = [
        Profile(name=f"acme-bank-{i}", os_type="ios") for i in range(40)
    ]
    assert all(p.hardware_generation_value is None for p in legacy)

    def presented(p):
        return (
            resolve_resolution("auto", p.fingerprint_seed, p.hardware_generation),
            pick_preset(p.fingerprint_seed, "ios", p.hardware_generation).key,
        )

    before = {p.name: presented(p) for p in legacy}

    monkeypatch.setattr(
        "src.services.browser.resolution.DESKTOP_RESOLUTIONS",
        DESKTOP_RESOLUTIONS + [ResolutionEntry(3840, 2160, since=NEXT_GEN)],
    )
    monkeypatch.setattr(
        "src.services.browser.device_presets.IOS_PRESETS",
        IOS_PRESETS + [_preset_like("iphone-16", NEXT_GEN)],
    )

    assert {p.name: presented(p) for p in legacy} == before


# --------------------------------------------------------------------------
# The filter itself
# --------------------------------------------------------------------------

def test_generation_zero_sees_every_python_pool_exactly_as_it_shipped():
    # Generation 0 is what every pre-existing profile reads, so its pool must be
    # the entire shipped list — contents, ORDER and therefore divisor. If a
    # shipped entry were ever renumbered above 0, the profiles pinned to it would
    # be the ones this whole mechanism exists to protect.
    #
    # THIS USED TO ASSERT `all(e.since == 0)`, WHICH IS POLARITY-INVERTED
    # AGAINST THE PROCEDURE IT PROTECTS (PS-190). Verified by execution, both
    # ways: an UNSAFE untagged append satisfies `since == 0` and went GREEN,
    # while the DOCUMENTED CORRECT edit — bump CURRENT_HARDWARE_GENERATION, tag
    # the new entry `since=1` — made it go RED and failed a maintainer who
    # followed the documented procedure exactly. A guard that fires on the
    # correct edit and not on the dangerous one is worse than no guard: it
    # trains people to work around it.
    #
    # Stated instead as the invariant that actually needs to hold, and that
    # SURVIVES A BUMP: generation 0's VISIBLE pool is the list as it shipped.
    # A tagged append is invisible to generation 0 (green, correctly); an
    # untagged one lands in it and changes the divisor (red, correctly). The
    # census pins contents AND order, because `pool[seed % len(pool)]` depends
    # on both — a reorder or a substitution re-indexes as hard as an append,
    # and a count-only check would miss either.
    assert [e.size for e in visible_entries(DESKTOP_RESOLUTIONS, 0)] == [
        (1366, 768), (1440, 900), (1536, 864), (1600, 900), (1920, 1080),
        (1680, 1050), (1920, 1200), (2560, 1080), (2560, 1440),
    ]
    assert [p.key for p in visible_entries(IOS_PRESETS, 0)] == [
        "iphone-15", "iphone-14",
    ]
    assert [p.key for p in visible_entries(ANDROID_PRESETS, 0)] == [
        "pixel-7", "galaxy-s23", "xiaomi-13",
    ]


def test_no_shipped_python_entry_is_tagged_beyond_the_current_generation():
    # The other half of the generalised latch — the bump-surviving replacement
    # for `all(e.since == 0)`. An entry tagged with a generation nobody has been
    # minted into yet is unreachable until the constant catches up: the "tag
    # first, bump never" mistake, which makes a newly-added entry silently dead
    # rather than loudly failing.
    for label, pool in (
        ("DESKTOP_RESOLUTIONS", DESKTOP_RESOLUTIONS),
        ("IOS_PRESETS", IOS_PRESETS),
        ("ANDROID_PRESETS", ANDROID_PRESETS),
        ("ANDROID_TOUCH_POINTS", ANDROID_TOUCH_POINTS),
        ("CORES_MEMORY", device_ext.CORES_MEMORY),
    ):
        for e in pool:
            assert 0 <= e.since <= CURRENT_HARDWARE_GENERATION, (
                f"{label} has an entry tagged since={e.since}, but "
                f"CURRENT_HARDWARE_GENERATION is {CURRENT_HARDWARE_GENERATION}"
                " — bump the constant in the same commit, or nothing can ever "
                "be picked onto it"
            )


def test_normalize_generation_never_yields_an_empty_pool():
    # Anything unusable reads as 0 rather than as something that would hide every
    # entry and leave nothing to pick from.
    for bad in (None, -1, -999, "3", 3.5, True, False, object()):
        assert normalize_generation(bad) == 0
    assert normalize_generation(2) == 2
