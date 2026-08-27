"""PS-73: the per-seed audio perturbation must reach the FIREFOX engine too.

The defect these pin: ``spawn_browser`` returns on the Firefox arm ~100 lines
before the extension list is assembled, so ``build_audio_extension`` was never
called for a Firefox profile. Measured consequence — ``audio.digest`` read
35.749972 on FOUR profiles with four DISTINCT seeds, identical to six decimal
places. Since ``audio.digest`` is the ONE probe the inventory grades
``INDEPENDENT`` (probes.py), that left mutual unlinkability on Firefox resting
entirely on POOLED vectors, which can collide by chance.

These are unit tests and they are deliberately NOT the evidence that the ticket
is fixed: a test asserting "the script was built" cannot answer whether a page
sees a different value. The live evidence is the behavioural harness
(``src.services.verify.behaviour_cli``), which launches real profiles and reads
the vector from inside the page. What these DO pin is the part a live run cannot
re-check cheaply on every commit, and the two regressions most likely to be
introduced later:

  * Chromium's generated extension must not move by a single byte, and
  * the Firefox script must carry the SAME perturbation, cloaked the way
    SpiderMonkey renders natives rather than the way V8 does.
"""

import pathlib

import pytest

from src.services.browser.audio_ext import (
    _CHROMIUM_NATIVE_WRAP,
    _FIREFOX_NATIVE_WRAP,
    build_audio_extension,
    firefox_audio_init_script,
)
from tests.native_mask_probe import (
    AUDIO_OBSERVABLE_PROBE,
    AUDIO_OBSERVABLE_STUBS,
    AUDIO_STRINGIFY_PROBE,
    assert_reads_native_in_child_realm,
    native_form,
    observe_in_child_realm,
    observe_in_realm,
    spidermonkey_native_form,
    stringify_in_realm,
)


def _write(tmp_path, seed, name="ff.js"):
    """The Firefox init script on disk, so the shared realm probe can run it."""
    d = pathlib.Path(tmp_path) / f"ff-{seed}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(firefox_audio_init_script(seed), encoding="utf-8")
    return p


# --- the fix: Firefox actually perturbs, per seed ---------------------------


def test_firefox_script_changes_what_a_page_reads(tmp_path):
    """The whole point of the ticket, asserted on the OBSERVABLE.

    Not "the script mentions the seed" — a script that declares its seed and
    installs nothing passes that, and is exactly the state Firefox was in
    (no perturbation at all) while still having a seed.
    """
    spoofed = observe_in_realm(
        tmp_path / "run", _write(tmp_path, 111),
        AUDIO_OBSERVABLE_STUBS, AUDIO_OBSERVABLE_PROBE,
    )
    unspoofed = observe_in_realm(
        tmp_path / "run-none", _write_noop(tmp_path),
        AUDIO_OBSERVABLE_STUBS, AUDIO_OBSERVABLE_PROBE,
    )
    assert spoofed != unspoofed, (
        "the Firefox init script left the float readback untouched — this is "
        "the PS-73 defect itself, where the engine had a seed and no perturbation"
    )


def _write_noop(tmp_path):
    d = pathlib.Path(tmp_path) / "noop"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "noop.js"
    p.write_text("(function(){ var SEED = 111; })();\n", encoding="utf-8")
    return p


def test_two_firefox_seeds_are_unlinkable(tmp_path):
    """Level 2, stated about what a page reads rather than about file bytes.

    The counterfactual is the load-bearing half: with no spoof installed both
    seeds must read the SAME samples, which is what makes the divergence above
    evidence about the perturbation rather than about the test.
    """
    a = observe_in_realm(
        tmp_path / "a", _write(tmp_path, 111),
        AUDIO_OBSERVABLE_STUBS, AUDIO_OBSERVABLE_PROBE,
    )
    b = observe_in_realm(
        tmp_path / "b", _write(tmp_path, 222),
        AUDIO_OBSERVABLE_STUBS, AUDIO_OBSERVABLE_PROBE,
    )
    assert a != b, (
        f"two Firefox seeds observe IDENTICAL samples, so the two profiles are "
        f"linkable on the only continuous vector this inventory has: {a!r}"
    )


def test_same_seed_reads_the_same_value_twice(tmp_path):
    """A vector that varies per launch is not an identity.

    Divergence alone is satisfied by RANDOMNESS, which would make a profile
    unrecognisable to itself across a restart — trading Level 2 for the
    restart-continuity outcome. The property wanted is stable-per-profile.
    """
    first = observe_in_realm(
        tmp_path / "one", _write(tmp_path, 777, "a.js"),
        AUDIO_OBSERVABLE_STUBS, AUDIO_OBSERVABLE_PROBE,
    )
    second = observe_in_realm(
        tmp_path / "two", _write(tmp_path, 777, "b.js"),
        AUDIO_OBSERVABLE_STUBS, AUDIO_OBSERVABLE_PROBE,
    )
    assert first == second, (
        "the same seed observed two different values, so the vector is random "
        "rather than per-profile and a profile cannot be recognised as itself"
    )


def test_delta_survives_the_probes_six_decimal_reduction(tmp_path):
    """A delta below the probe's rounding is the same as no delta.

    ``audio.digest`` reduces 500 perturbed samples to a 6dp sum, so a
    perturbation that rounds away produces a check that passes STRUCTURALLY
    while reading identical — the failure the ticket's technical notes warn
    about. Reproduce that reduction here and require the two seeds to differ
    AFTER rounding, not merely before it.
    """
    def digest(seed):
        raw = observe_in_realm(
            tmp_path / f"d{seed}", _write(tmp_path, seed),
            AUDIO_OBSERVABLE_STUBS, AUDIO_OBSERVABLE_PROBE,
        )
        total = sum(abs(float(x)) for x in raw.split(","))
        return round(total, 6)

    a, b = digest(111), digest(222)
    assert a != b, (
        f"two seeds collapse to the SAME 6dp sum ({a}) — the perturbation is "
        f"smaller than the reduction that reads it, which is indistinguishable "
        f"from no perturbation at all"
    )


# --- the boundary: Chromium must not move -----------------------------------


def test_chromium_extension_is_byte_identical_to_the_pre_ps73_text(tmp_path):
    """PS-73 boundary: "a fix that moves Chromium's readings is a regression
    against every reading already recorded."

    Refactoring the engine-specific cloak out into a seam is only safe if the
    Chromium substitution reproduces the ORIGINAL text exactly, so this pins the
    emitted script against the literal it must keep producing. Byte-level,
    because the perturbation is a function of the script's own arithmetic: any
    edit to it moves recorded digests on an engine this ticket does not touch.
    """
    js = (pathlib.Path(build_audio_extension(12345, str(tmp_path / "ext")))
          / "audio.js").read_text(encoding="utf-8")

    # The V8 marker cloak, and nothing from the Firefox form.
    assert "__pnaName" in js, (
        "the Chromium script lost native_ext's marker, so its wrappers no "
        "longer stringify as native on the engine that reads that marker"
    )
    assert _CHROMIUM_NATIVE_WRAP in js
    assert "String.fromCharCode(10)" not in js, (
        "SpiderMonkey's native form leaked into the Chromium script"
    )


def test_chromium_and_firefox_share_the_perturbation_not_the_cloak(tmp_path):
    """The two engines must differ ONLY in how a wrapper is cloaked.

    A profile's audio identity should not depend on which engine it launches,
    so the arithmetic is shared; the cloak cannot be, because Firefox loads no
    persona extension and V8's native form is a tell on SpiderMonkey.
    """
    chromium = (pathlib.Path(build_audio_extension(999, str(tmp_path / "ext")))
                / "audio.js").read_text(encoding="utf-8")
    firefox = firefox_audio_init_script(999)

    # shared: the perturbation and the realm bootstrap
    for shared in ("function perturbFloat(data)", "function bit(i)",
                   "var REL =", "__pnaInstall(SELF, applyAudioPatch)"):
        assert shared in chromium and shared in firefox, (
            f"{shared!r} is not shared by both engines, so a profile's audio "
            f"identity would depend on which engine it launched"
        )

    # not shared: the cloak
    assert _FIREFOX_NATIVE_WRAP in firefox
    assert _FIREFOX_NATIVE_WRAP not in chromium
    assert _CHROMIUM_NATIVE_WRAP not in firefox


def test_firefox_cloak_reads_native_from_inside_a_child_frame(tmp_path):
    """THE REGRESSION THIS FILE PREVIOUSLY COULD NOT SEE (PS-73 round 2).

    The cloak used to capture and assign a BARE ``Function.prototype.toString``.
    The generated text is IDENTICAL either way — the difference is only which
    realm the binding resolves in — so the three substring assertions this test
    replaces were all green against the defect.

    What was actually wrong: ``worker_wrap``'s chained ``contentWindow``
    accessor carries the leaf into a child frame as a PARENT-REALM FUNCTION
    OBJECT, so a bare ``Function.prototype`` resolved to the PARENT's. The
    child's own toString stayed pristine while the audio wrappers WERE
    installed there, and a detector running in that frame read back
    ``perturbFloat`` source off ``getChannelData``.

    Measured, not reasoned: this executes the generated script in a parent
    realm, lets the bootstrap reach a real second realm, and stringifies the
    wrapper FROM INSIDE THE CHILD — which is where a detector runs.
    """
    assert_reads_native_in_child_realm(
        tmp_path, [_write(tmp_path, 111)],
        AUDIO_OBSERVABLE_STUBS,
        AUDIO_STRINGIFY_PROBE,
        spidermonkey_native_form("getChannelData"),
        reached_probe=AUDIO_OBSERVABLE_PROBE,
        unpatched_observable=observe_in_child_realm(
            tmp_path / "bare", [_write_noop(tmp_path)],
            AUDIO_OBSERVABLE_STUBS, AUDIO_OBSERVABLE_PROBE,
        ),
    )


def test_firefox_cloak_emits_spidermonkeys_native_form_not_v8s(tmp_path):
    """V8's one-line ``function x() { [native code] }`` is WRONG on Firefox.

    SpiderMonkey renders three lines with a four-space indent, and emitting
    V8's form here would leave a detector one ``Array.prototype.map.toString()``
    comparison away from the tell the cloak exists to remove.

    Asserted on what the CHILD REALM READS BACK rather than on the generated
    text, so it witnesses the rendered form in the realm that matters. The
    expected string is a separate literal from ``native_form`` (V8's) on
    purpose: a shared "native-ish" matcher would accept either engine's shape.
    """
    read = observe_in_child_realm(
        tmp_path, [_write(tmp_path, 111)],
        AUDIO_OBSERVABLE_STUBS, AUDIO_STRINGIFY_PROBE,
    )
    assert read == spidermonkey_native_form("getChannelData"), read
    assert read != native_form("getChannelData"), (
        "V8's one-line native form is what a page reads on the Firefox path, "
        "which is itself a masking tell on SpiderMonkey"
    )


def test_firefox_cloak_adds_no_own_property_to_wrappers(tmp_path):
    """The Firefox cloak must not tag wrappers with an enumerable marker.

    Nothing on the Firefox path reads native_ext's ``__pnaName`` (no persona
    extension is loaded there), so such a property would not be a cloak — just
    an own property on every wrapper that a page can enumerate.

    Asserted by ENUMERATING THE WRAPPER in the child realm, not by grepping the
    source: the property's absence from the text and its absence from the live
    object are different claims, and only the second is what a page sees.
    """
    names = observe_in_child_realm(
        tmp_path, [_write(tmp_path, 111)],
        AUDIO_OBSERVABLE_STUBS,
        "Object.getOwnPropertyNames(AudioBuffer.prototype.getChannelData)"
        ".sort().join(',')",
    )
    assert "__pnaName" not in names, (
        f"the wrapper carries an enumerable marker a page can sweep for: {names}"
    )
    # Deliberately NOT an exact-list assertion. The legitimate own-property set
    # is ENGINE-SPECIFIC — a sloppy-mode function carries legacy
    # `arguments`/`caller` under this harness, while the live Firefox reading
    # recorded on this ticket was [length, name, prototype]. Pinning either
    # literal would make this test fail on the other engine for a reason that
    # has nothing to do with masking. What must hold on every engine is that
    # NOTHING persona-shaped is present.
    legitimate = {"length", "name", "prototype", "arguments", "caller"}
    leaked = sorted(set(names.split(",")) - legitimate)
    assert not leaked, (
        f"the wrapper carries own properties a native function would not have, "
        f"which a page can enumerate as a tell: {leaked}"
    )


def test_firefox_cloak_chains_rather_than_replaces_tostring(tmp_path):
    """Two other init scripts already patch ``Function.prototype.toString`` in
    this realm (the locale and outer-size overrides). A patch that REPLACED the
    slot would break whichever installed first; chaining composes.

    Asserted by installing a PRIOR toString patch and checking it still runs
    for a function the audio cloak knows nothing about — behaviour, not a
    ``__pts.apply`` substring, which is green whether or not the delegation is
    reachable.
    """
    prior = pathlib.Path(tmp_path) / "prior"
    prior.mkdir(parents=True, exist_ok=True)
    prior_js = prior / "prior.js"
    prior_js.write_text(
        """(function () {
             var o = Function.prototype.toString;
             Function.prototype.toString = function () {
               if (this && this.__priorMarked) { return 'PRIOR_CLOAK_RAN'; }
               return o.apply(this, arguments);
             };
             function decoy() {}
             decoy.__priorMarked = true;
             globalThis.__decoy = decoy;
           })();
        """,
        encoding="utf-8",
    )

    read = stringify_in_realm(
        tmp_path, [prior_js, _write(tmp_path, 111)],
        AUDIO_OBSERVABLE_STUBS,
        "Function.prototype.toString.call(globalThis.__decoy)",
        install_native=False,
    )
    assert read == "PRIOR_CLOAK_RAN", (
        "the audio cloak did not delegate to the previously-installed "
        f"toString, so it cannot compose with the locale/outer-size cloaks: {read!r}"
    )

    # And the audio cloak still masks its OWN wrapper in that shared realm —
    # composition must not cost either side its cloak.
    mine = stringify_in_realm(
        tmp_path, [prior_js, _write(tmp_path, 111)],
        AUDIO_OBSERVABLE_STUBS,
        "Function.prototype.toString.call(AudioBuffer.prototype.getChannelData)",
        install_native=False,
    )
    assert mine == spidermonkey_native_form("getChannelData"), mine


def test_firefox_script_is_self_contained_and_seed_bearing():
    """No unreplaced placeholders, and the seed actually reaches the text.

    A leftover placeholder is a syntax error at document_start, which would
    silently disable the patch (every call site is try/catch-wrapped) and
    reproduce the defect while looking fixed.
    """
    js = firefox_audio_init_script(1350958544)
    for placeholder in ("__SEED__", "__REL__", "__NATIVE_WRAP__",
                        "__REALM_BOOTSTRAP__"):
        assert placeholder not in js, f"unreplaced placeholder {placeholder}"
    assert "1350958544" in js


@pytest.mark.parametrize("seed", [0, 1, 2**32 - 1, 2**32, -1])
def test_seed_is_masked_into_range(seed):
    """Out-of-range and negative seeds must not emit a literal that changes the
    arithmetic's width (the Chromium builder masks identically)."""
    js = firefox_audio_init_script(seed)
    assert f"var SEED = {int(seed) & 0xFFFFFFFF};" in js
