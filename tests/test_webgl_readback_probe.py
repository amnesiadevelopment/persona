"""PS-90: the `webgl.readback` probe — the SECOND vector on the must-differ axis.

WHY THIS PROBE EXISTS. `compare_profiles` is the machine-enforced gate for
Level 2 (mutual unlinkability), and it compares exactly the probes classified
`probes.INDEPENDENT`. Until this one landed that was a set of ONE
(`audio.digest`). A single-vector gate is one upstream change away from
comparing nothing at all, and an empty comparison result reads as a PASS —
which is the failure mode the charter names. Meanwhile `webgl_ext.py` already
shipped a per-seed `readPixels` delta *because* the GPU-less VM renders through
a software renderer where the real pixels collide across profiles and link them
(`webgl_ext.py:5-7`) — and no probe read it.

WHAT THESE TESTS ASSERT ON. The observable, never the generated text. A test
that greps `webgl.js` for "readPixels" passes against a spoof that never loads,
which is precisely the class of false green this project has shipped six times
(the `TestInventoryHonesty` docstring and PS-63 both name it). So the REAL probe
expression is imported from `PROBES` and EXECUTED against the REAL generated
extension in an isolated realm. Neither side is retyped here, so neither can
drift from what ships.

THE HONEST BOUND ON THEM. These run in a `node:vm` realm against a framebuffer
stub, so what they pin is the probe's REDUCTION — that it reads the perturbed
bytes, that the digest is position-sensitive, that it releases its context, that
it returns null rather than throwing. They are NOT evidence that a real engine's
software renderer produces a varying readback; a stub cannot answer that, and a
test that pretended to would be the false green again. That question was settled
by MEASUREMENT on a live chromium under ANGLE/SwiftShader before this probe was
written, and the numbers are recorded on the ticket:

    unspoofed baseline 3192686533
    seed 111  1296905148     seed 222  2139043386
    seed 333  3447875548     seed 444  2544399124
    seed 111, second fresh profile: 1296905148  (bit-identical)
    mid: 3072 of 4096 bytes eligible for perturbation

The live re-measurement lives with the behavioural harness
(`src.services.verify.behaviour_cli`), which launches real profiles; these are
the part that can run on every commit.
"""

import pathlib

import pytest

from src.services.browser.webgl_ext import build_webgl_extension
from src.services.verify import probes
from tests.native_mask_probe import (
    GL_NO_CONTEXT_STUBS,
    GL_READBACK_STUBS,
    dead_spoof_script,
    observe_in_realm,
)

# The probe under test, taken from the INVENTORY rather than retyped. If the
# shipped expression changes, these tests execute the change.
READBACK_EXPR = next(p for p in probes.PROBES if p.id == "webgl.readback").expr

# The probe returns an object; the realm harness hands back parsed JSON.
PROBE = "JSON.stringify(" + READBACK_EXPR + ")"

# How many times the probe released a WebGL context, per the stub's counter.
RELEASE_PROBE = "JSON.stringify([" + READBACK_EXPR + ", globalThis.__released])"


def _observe(tmp_path, seed, tag, *, stubs=GL_READBACK_STUBS, probe=PROBE):
    """Run the REAL probe against the REAL generated spoof for `seed`."""
    ext = pathlib.Path(
        build_webgl_extension(seed, str(pathlib.Path(tmp_path) / f"ext-{tag}"))
    )
    import json

    return json.loads(
        observe_in_realm(
            pathlib.Path(tmp_path) / f"run-{tag}", ext / "webgl.js", stubs, probe
        )
    )


def _observe_unspoofed(tmp_path, tag, seed=111, *, stubs=GL_READBACK_STUBS):
    """The counterfactual: the same probe with the spoof NEUTERED (seed declared,
    nothing installed). Every divergence claim below is measured against this."""
    import json

    return json.loads(
        observe_in_realm(
            pathlib.Path(tmp_path) / f"dead-{tag}",
            dead_spoof_script(tmp_path, seed),
            stubs,
            PROBE,
        )
    )


# --- the classification -----------------------------------------------------


def test_the_probe_is_in_the_inventory_and_is_must_differ():
    # AC1. Classification is what puts a vector on the Level-2 gate at all:
    # `compare_profiles` walks `must_differ_probes()`, so an unclassified probe
    # is silently never compared.
    assert "webgl.readback" in probes.must_differ_ids()
    probe = next(p for p in probes.PROBES if p.id == "webgl.readback")
    assert probe.variance == probes.INDEPENDENT


def test_the_must_differ_axis_is_no_longer_a_single_vector():
    # THE POINT OF THE TICKET, stated as the property rather than as the number
    # 2: a gate resting on ONE vector silently compares nothing the day that
    # vector stops being readable, and an empty comparison result reads as a
    # pass. Asserted as ">= 2" deliberately — pinning the literal count would
    # make this test fail on the next probe somebody classifies correctly,
    # training its reader to edit the number rather than think.
    assert len(probes.must_differ_ids()) >= 2
    assert {"audio.digest", "webgl.readback"} <= probes.must_differ_ids()


def test_the_readback_probe_is_window_only():
    # A CORRECTNESS constraint, not a style choice, and the reasoning is easy to
    # lose. MEASURED on chromium: inside a Worker, `getContext('webgl')` returns
    # null and only 'webgl2' yields a context — but `_JS_WITH_GL` asks for
    # 'webgl' then 'experimental-webgl', so it returns null in that realm.
    #
    # A null is RECORDED as {"value": null}, and `diff._unread` keys on the
    # PRESENCE of "value", not its content. So two profiles both reading null
    # compare EQUAL and are reported COLLIDING — a false leak report on every
    # pair, on the very axis this probe was added to strengthen. Harmless on a
    # SHARED probe (never compared); on an INDEPENDENT one it manufactures the
    # exact finding the gate exists to detect.
    probe = next(p for p in probes.PROBES if p.id == "webgl.readback")
    assert probe.realms == probes.WINDOW_ONLY


# --- the observable ---------------------------------------------------------


def test_two_profiles_read_different_digests(tmp_path):
    # AC3, and the Level-2 claim itself: two profiles must not be linkable on
    # this vector. Asserted on what the probe READS, with the counterfactual
    # that makes it evidence about the spoof rather than about the test.
    a = _observe(tmp_path, 111, "a")
    b = _observe(tmp_path, 222, "b")
    assert a["digest"] != b["digest"], (
        f"seeds 111 and 222 produced the SAME digest ({a['digest']}), so a page "
        f"can link the two profiles on the WebGL readback vector"
    )

    # FALSIFICATION: with the spoof neutered the same probe must read ONE value
    # for both seeds. Without this, the test above is green against a probe
    # that is reading something other than the spoof.
    dead_a = _observe_unspoofed(tmp_path, "a", seed=111)
    dead_b = _observe_unspoofed(tmp_path, "b", seed=222)
    assert dead_a["digest"] == dead_b["digest"], (
        "two seeds diverged with NO spoof installed — this probe is reading "
        "something other than the readback perturbation"
    )
    assert a["digest"] != dead_a["digest"], (
        "the spoofed digest equals the unspoofed one, so the extension changed "
        "nothing this probe can read"
    )


def test_a_profile_reads_the_same_digest_twice(tmp_path):
    # AC4, and the half that divergence ALONE cannot establish. A random vector
    # satisfies "two profiles differ" perfectly while making a profile
    # unrecognisable to ITSELF across a restart — which is a different leak, not
    # a fix. (3) and (4) together are the property; either alone is satisfiable
    # by a bug.
    first = _observe(tmp_path, 111, "first")
    again = _observe(tmp_path, 111, "again")
    assert first == again, (
        f"one profile observed two different readings ({first} then {again}) — "
        f"the vector is random rather than per-profile"
    )


def test_three_profiles_are_pairwise_unlinkable(tmp_path):
    # Pairwise rather than "some differ": the gate's claim is about every PAIR
    # of profiles, and a vector where two of three collide is linkable for that
    # pair no matter how the third behaves.
    seeds = (111, 222, 333)
    seen = {s: _observe(tmp_path, s, f"p{s}")["digest"] for s in seeds}
    for i, a in enumerate(seeds):
        for b in seeds[i + 1:]:
            assert seen[a] != seen[b], (
                f"profiles {a} and {b} are LINKABLE on webgl.readback: both "
                f"read {seen[a]}"
            )


# --- the reduction ----------------------------------------------------------


def test_the_digest_is_a_stable_scalar(tmp_path):
    # AC5. `audio.digest` rounds its float sum for exactly this reason: a raw
    # float array makes a snapshot comparison hostage to float formatting, and
    # two readings that are numerically equal can then compare unequal — noise
    # on an axis where a DIFFERENCE is the silent pass and would hide a real
    # collision behind a formatting artefact.
    reading = _observe(tmp_path, 111, "scalar")
    assert isinstance(reading["digest"], int)
    assert 0 <= reading["digest"] <= 0xFFFFFFFF


def test_the_sampled_surface_is_mid_range_not_black(tmp_path):
    # THE TRAP that would make a WORKING spoof read as a dead one. perturbBytes
    # nudges a byte only `if (v > 1 && v < 254)` (webgl_ext.py:66), deliberately,
    # so it does not produce obviously-wrong pixels. A probe that cleared to
    # black or white would therefore read back a surface the spoof COULD NOT
    # TOUCH, observe no variance at all, and look like a total masking failure.
    #
    # `mid` is the probe's own self-check: how many bytes were even eligible.
    # Exactly 3/4 here — the RGB channels sit mid-range and alpha is pinned at
    # 255 and correctly skipped. The same 3072/4096 the live engine reported.
    reading = _observe(tmp_path, 111, "midrange")
    assert reading["mid"] == reading["bytes"] * 3 // 4, (
        f"only {reading['mid']} of {reading['bytes']} bytes are perturbable — "
        f"the draw is no longer mid-range, so this probe would read a working "
        f"spoof as a dead one"
    )


def test_the_digest_is_position_sensitive_not_a_sum(tmp_path):
    # WHY NOT A SUM, pinned by execution against the SHIPPED reduction.
    #
    # The perturbation is +/-1 per touched byte over ~241 touched indices, so a
    # SUM of the buffer is a random walk in a ~+/-40 window: two seeds collide
    # by ARITHMETIC rather than by identity. That is precisely the pigeonhole
    # property that makes a vector POOLED (it is why `webgl.unmasked` is not on
    # this axis) — and it would have produced a probe that is INDEPENDENT by
    # declaration and POOLED in behaviour, i.e. a gate reporting collisions that
    # mean nothing.
    #
    # Asked of the REAL probe expression, not of a retyped copy of its hash: the
    # stub swaps two bytes holding DIFFERENT values, so the buffer's sum and its
    # multiset of bytes are both unchanged and ONLY the arrangement moves. A
    # summing reduction returns the same digest here by construction; a
    # position-sensitive one cannot.
    plain = _observe(tmp_path, 111, "arr-plain")
    swapped = _observe(
        tmp_path, 111, "arr-swap",
        stubs=GL_READBACK_STUBS + "\nglobalThis.__swap = true;\n",
    )
    assert plain["mid"] == swapped["mid"], (
        "the fixture moved more than the arrangement — the two readbacks no "
        "longer hold the same bytes, so this proves nothing about position"
    )
    assert plain["digest"] != swapped["digest"], (
        "the same bytes in a different ORDER produced the same digest, so the "
        "reduction is position-blind: collisions on this vector would be "
        "arithmetic rather than identity, which is the POOLED property and "
        "disqualifies it from the must-differ axis"
    )


# --- resource discipline and the absent-context path ------------------------


def test_the_probe_releases_its_webgl_context(tmp_path):
    # Contexts are a scarce PER-PROCESS resource, which is why `_JS_WITH_GL`
    # releases in a `finally`. A probe that leaked one per run would eventually
    # evict the page's own context — the verification harness would then be
    # changing what it is trying to observe.
    ext = pathlib.Path(build_webgl_extension(111, str(tmp_path / "ext-rel")))
    import json

    reading, released = json.loads(
        observe_in_realm(
            tmp_path / "run-rel", ext / "webgl.js", GL_READBACK_STUBS, RELEASE_PROBE
        )
    )
    assert reading is not None
    assert released == 1, (
        f"the probe released {released} contexts, not 1 — a probe run that "
        f"leaks a context evicts the page's own"
    )


def test_the_probe_returns_null_rather_than_throwing_without_webgl(tmp_path):
    # AC2, matching the existing GL probes (probes.py:286, :305, :312). The
    # distinction is load-bearing rather than cosmetic: the runner records a
    # throw as {"error": ...}, which `_unread` treats as NO EVIDENCE, whereas a
    # returned null is a READING. Both are honest here — a realm with no WebGL
    # genuinely has nothing to report — but a probe that THREW would make an
    # ordinary absence look like a harness failure on every headless run.
    ext = pathlib.Path(build_webgl_extension(111, str(tmp_path / "ext-nogl")))
    import json

    reading = json.loads(
        observe_in_realm(
            tmp_path / "run-nogl", ext / "webgl.js", GL_NO_CONTEXT_STUBS, PROBE
        )
    )
    assert reading is None


# --- the Firefox gap this probe is the instrument for -----------------------


def test_firefox_has_no_webgl_readback_spoof_by_any_route():
    # NOT a fix, and deliberately not a skip: a REGRESSION-DIRECTION marker for
    # a gap this ticket is scoped out of closing.
    #
    # `spawn_browser` returns on the Firefox arm (process.py:353) ~100 lines
    # before the extension list is assembled, so `build_webgl_extension` never
    # runs for Firefox — exactly the shape of the defect PS-73 fixed for audio.
    # PS-73 landed audio-ONLY, so Firefox today has no WebGL readback spoof by
    # any route, and this probe is the instrument that would have caught it.
    #
    # This test PASSES on today's gap and goes RED when somebody closes it, at
    # which point the correct action is to delete this test and give Firefox the
    # coverage `tests/test_ff_audio_seed.py` gives audio. Written this way round
    # because the alternative — a silent absence — is what let the gap persist.
    # Resolved from THIS FILE, never from the cwd: another test in the suite
    # chdirs, so a relative path here passes when this file runs alone and fails
    # in a full run. Same idiom as test_verify_baseline.py's `_artifact()`.
    root = pathlib.Path(__file__).resolve().parents[1]
    launch = (root / "src/services/browser/invisible_launch.py").read_text()
    assert "readPixels" not in launch, (
        "Firefox appears to have gained a WebGL readback spoof. That is GOOD "
        "news and this marker is now stale: delete this test and pin the "
        "Firefox readback vector behaviourally, the way test_ff_audio_seed.py "
        "pins audio."
    )
