"""PS-395: no device preset may DECLARE a ``navigator.deviceMemory`` value the
Device Memory API cannot report, and no launch path may publish one.

WHAT WAS WRONG
──────────────
The ``xiaomi-13`` preset declared ``device_memory=12``. The Device Memory API
reports host RAM rounded DOWN to a power of two and capped at 8, so the legal
set is exhaustively ``(0.25, 0.5, 1, 2, 4, 8)`` and ``12`` is a value NO real
browser can produce. That makes it a POSITIVE IDENTIFICATION rather than a
disguise: a detector needs no baseline and no comparison, only the knowledge
that 12 is impossible.

⚠️ The 12 was not a typo ABOUT THE DEVICE. A Xiaomi 13 really does ship 12 GB
of RAM. It was a confusion between the hardware truth and what the API is
allowed to SAY about it — which is why a guard is worth more here than a
corrected number: the next device added will have the same true-but-illegal
figure sitting in its spec sheet.

⛔ WHY THE ONE-LINE TABLE FIX WAS NOT THE FIX
─────────────────────────────────────────────
Measured on the release engine before the change: a ``xiaomi-13`` profile
published ``deviceMemory = 12`` to the PAGE while the ENGINE reported ``8``.
Two clamps already existed — ``spec_device_memory()`` at the switch boundary in
``process.py``, and ``ClampToLegalDeviceMemory()`` inside patch 005 — and BOTH
of them sit on the ENGINE path. The JS author (``mobile_ext``'s
``def(nav, 'deviceMemory', MEM)``) had NEITHER, and JS wins in every realm it
reaches.

So the preset was identifiable TWO ways at once: an impossible value in the
page, and a page/ServiceWorker disagreement. Correcting the table closes both
FOR THIS PRESET and leaves the CLASS open. The fix is the clamp at the call
site; the table correction is a consequence, and this file is the guard that
keeps both true.

⭐ WHY THE EXISTING COVERAGE DID NOT CATCH IT — READ THIS BEFORE EDITING
────────────────────────────────────────────────────────────────────────
Two tests already walked the preset table and both passed on the defect:

- ``test_presets_have_required_fields`` (``test_mobile.py``) asserts
  ``p.device_memory > 0``. ``12`` passes.
- ``test_a_mobile_profile_is_launched_with_its_PRESETS_memory_not_the_desktop_pools``
  (``test_ps_device_memory_native.py``) asserts
  ``spec_device_memory(preset.device_memory) in LEGAL_DEVICE_MEMORY``.

⛔ THE SECOND ONE CANNOT FAIL. ``spec_device_memory`` RETURNS a member of
``LEGAL_DEVICE_MEMORY`` for every possible input, including a negative — so
that assertion is true by construction whatever the table says. It reads like a
legality guard over the presets and is actually a tautology about the clamp.
That is precisely how ``12`` survived a table the tests already swept.

⚠️ SO THE ASSERTIONS HERE ARE ON THE **RAW DECLARED** VALUE, never on the
clamped one. A guard written over ``spec_device_memory(...)`` output is not a
weaker version of this file — it is a test that cannot go red, which this
project has on record as indistinguishable from a broken one.
"""

from __future__ import annotations

import pathlib
import re
import tempfile

import pytest

from src.services.browser.device_ext import LEGAL_DEVICE_MEMORY, spec_device_memory
from src.services.browser.device_presets import ANDROID_PRESETS, IOS_PRESETS
from src.services.browser.engine_version import ChromiumVersion
from src.services.browser.mobile_ext import build_mobile_extension

#: Every preset a profile can be launched with, both OS arms.
ALL_PRESETS = ANDROID_PRESETS + IOS_PRESETS

#: Any engine version; the deviceMemory path does not read it, but an Android
#: build refuses to be built without one (it advertises the real Chromium major).
_V = ChromiumVersion(full="152.0.7977.75")


def _emitted_device_memory(preset, *, clamp: bool = True) -> float:
    """Build the REAL mobile extension for ``preset`` and read back the
    ``deviceMemory`` its JS actually publishes.

    ⚠️ This drives the shipped ``build_mobile_extension`` and parses the emitted
    ``mobile.js`` rather than re-implementing the substitution. A test that
    asserted against a re-derived string would pass while the real emitter was
    broken — the defect this whole file exists for lived in exactly that gap
    between "what the table says" and "what the script says".

    ``clamp`` mirrors what ``process.py`` does at the call site, so a caller can
    ask for the UNCLAMPED emission to prove the clamp is load-bearing.
    """
    value = spec_device_memory(preset.device_memory) if clamp else preset.device_memory
    ext_dir = tempfile.mkdtemp()
    build_mobile_extension(
        ext_dir,
        is_ios=(preset.os_type == "ios"),
        platform=preset.platform,
        model=preset.model,
        chromium_version=None if preset.os_type == "ios" else _V,
        css_width=preset.width,
        css_height=preset.height,
        dpr=preset.dpr,
        device_memory=value,
        hardware_concurrency=preset.hardware_concurrency,
        touch_points=5,
    )
    js = pathlib.Path(ext_dir, "mobile.js").read_text(encoding="utf-8")
    # ⛔ THE DECLARATION IS NOT THE PUBLICATION. An earlier draft of this helper
    # read `var MEM = …` alone and went GREEN against a mutant that deleted the
    # `def(nav, 'deviceMemory', MEM)` line entirely — the constant was still
    # declared, just consumed by nothing, so every "what the page receives"
    # assertion in this file was really measuring a variable nobody read. The
    # author line is checked FIRST so that a script publishing no deviceMemory
    # at all fails here with a reason, rather than passing as "legal".
    assert re.search(r"def\(\s*nav\s*,\s*'deviceMemory'\s*,\s*MEM\s*\)", js), (
        "the emitted mobile.js declares MEM but never defines "
        "navigator.deviceMemory from it — the value reaches no page, and the "
        "engine switch would become the only author"
    )
    match = re.search(r"var MEM\s*=\s*([0-9.]+);", js)
    assert match, "the emitted mobile.js no longer declares `var MEM = …`"
    return float(match.group(1))


# ---------------------------------------------------------------------------
# 1. THE GUARD THE TICKET ASKS FOR — over the RAW declared value.
# ---------------------------------------------------------------------------


def test_the_preset_table_is_not_empty_and_this_guard_discriminates():
    """⛔ THE POSITIVE CONTROL FOR EVERY SWEEP BELOW.

    A `for preset in ALL_PRESETS` assertion over an EMPTY list passes while
    testing nothing. This project has that failure on record twice (PS-299's
    rebase probe reporting "81/81 hunks, 0 rejects" against an empty directory;
    PS-341's "8 of 8 moved"), so the sweeps are anchored rather than trusted.
    """
    assert len(ALL_PRESETS) >= 5, "the preset table shrank; the sweeps below weaken"
    assert ANDROID_PRESETS, "no Android presets — the Android arm tests nothing"
    assert IOS_PRESETS, "no iOS presets — the iOS arm tests nothing"


@pytest.mark.parametrize("preset", ALL_PRESETS, ids=lambda p: p.key)
def test_no_preset_DECLARES_a_device_memory_the_spec_cannot_report(preset):
    """⛔ THE GUARD. THE CLASS, NOT THE INSTANCE.

    ⚠️ ASSERTED ON THE RAW ``preset.device_memory``. Do not "improve" this by
    wrapping it in ``spec_device_memory`` — that function returns a legal value
    for every input, so the assertion would become a tautology about the clamp
    and could never go red. See the module docstring: an existing test already
    made exactly that mistake and passed on the ``12``.

    The legal set is IMPORTED from ``device_ext`` rather than re-typed here, so
    the guard cannot drift from the authority it claims to enforce.
    """
    assert float(preset.device_memory) in LEGAL_DEVICE_MEMORY, (
        f"preset {preset.key!r} declares device_memory="
        f"{preset.device_memory}, which navigator.deviceMemory cannot report. "
        f"The API rounds host RAM DOWN to a power of two and caps at 8, so the "
        f"only legal values are {LEGAL_DEVICE_MEMORY}. A value outside that set "
        f"identifies persona to any script that knows the spec — no baseline "
        f"needed. If this device really has that much RAM, declare what the API "
        f"would SAY ({spec_device_memory(preset.device_memory)}), not what the "
        f"spec sheet says."
    )


@pytest.mark.parametrize("preset", ALL_PRESETS, ids=lambda p: p.key)
def test_hardware_concurrency_is_a_plausible_core_count(preset):
    """The sibling field, declared on the same line as the defect.

    ⚠️ Deliberately WEAKER than the deviceMemory guard, because the two are not
    the same kind of value: ``hardwareConcurrency`` has NO spec-enumerated legal
    set — it is a plain core count and a browser may report any positive
    integer. So this asserts plausibility (a positive, even-or-one integer
    within a range real phones occupy) rather than membership, and says so
    instead of implying a spec bound that does not exist.
    """
    cores = preset.hardware_concurrency
    assert isinstance(cores, int), f"{preset.key}: core count must be an integer"
    assert 1 <= cores <= 32, (
        f"preset {preset.key!r} declares hardware_concurrency={cores}, outside "
        f"the range any real phone reports"
    )


# ---------------------------------------------------------------------------
# 2. THE INVARIANT THAT ACTUALLY BROKE — the two authors must agree.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset", ALL_PRESETS, ids=lambda p: p.key)
def test_the_page_JS_and_the_engine_flag_publish_the_SAME_device_memory(preset):
    """⛔ THE REGRESSION THAT A LEGALITY-ONLY GUARD WOULD HAVE STAYED GREEN
    THROUGH.

    deviceMemory has TWO authors on a mobile profile: the engine switch
    (``--fingerprint-device-memory``, which reaches EVERY realm including
    ServiceWorker) and the mobile extension's JS (which reaches every realm it
    is injected into, and wins there). If they disagree, one launch reports two
    different values depending on where you ask — and no real browser does
    that, so the disagreement is a tell in its own right, independent of
    whether either value is legal.

    ⭐ THIS IS THE ONE THAT WOULD HAVE CAUGHT THE DEFECT AT ITS CAUSE. Before
    the fix, ``xiaomi-13`` emitted 12 in the page and 8 at the switch. A guard
    that only checked legality would have gone green the moment the table was
    corrected, while the unclamped JS path stayed open for the next preset.
    """
    from_js = _emitted_device_memory(preset)
    from_engine = spec_device_memory(preset.device_memory)
    assert from_js == from_engine, (
        f"preset {preset.key!r} publishes deviceMemory={from_js} to the page "
        f"but {from_engine} through the engine switch. One launch, two answers "
        f"— a realm disagreement no real browser produces."
    )


@pytest.mark.parametrize("preset", ALL_PRESETS, ids=lambda p: p.key)
def test_what_the_page_ACTUALLY_RECEIVES_is_spec_legal(preset):
    """The end-to-end claim, read off the REAL emitted script.

    The declaration guard above is about the table; this is about the artifact.
    They can diverge — a legal declaration rendered wrongly is still an illegal
    publication — so both are asserted rather than one being inferred from the
    other.
    """
    emitted = _emitted_device_memory(preset)
    assert emitted in LEGAL_DEVICE_MEMORY, (
        f"preset {preset.key!r} publishes deviceMemory={emitted} to the page, "
        f"which is not in {LEGAL_DEVICE_MEMORY}"
    )


# ---------------------------------------------------------------------------
# 3. THE CLAMP IS LOAD-BEARING — proven by removing it, not by assertion.
# ---------------------------------------------------------------------------


def test_the_call_site_clamps_rather_than_passing_the_raw_preset_value():
    """⛔ THE CAUSE FIX, PINNED AT ITS SOURCE.

    Reverting ``process.py`` to pass ``preset.device_memory`` raw re-opens the
    class even with a legal table, because the NEXT preset is what the clamp
    protects against. This asserts the wiring by name so that revert fails here
    with a reason attached.
    """
    source = pathlib.Path("src/services/browser/process.py").read_text(encoding="utf-8")
    assert "device_memory=spec_device_memory(preset.device_memory)" in source, (
        "process.py no longer clamps the value it hands the mobile extension. "
        "The engine path clamps twice; the JS path has no other defence, and "
        "JS wins in every realm it reaches."
    )
    assert "device_memory=preset.device_memory," not in source, (
        "process.py still passes the RAW preset value to build_mobile_extension"
    )


def test_an_ILLEGAL_declared_value_would_reach_the_page_WITHOUT_the_clamp():
    """⭐ THE POSITIVE CONTROL FOR THE CLAMP ITSELF.

    ⛔ A clamp tested only on already-legal inputs is untested: every assertion
    passes whether or not the clamp is there. So this drives a deliberately
    illegal figure through the emitter BOTH WAYS and shows the two outcomes
    differ — proving the clamp is what produces the legal one, rather than the
    input having been harmless all along.

    This is the original defect reproduced in miniature: 12 in, 12 out, with no
    clamp in the path.
    """

    class _FakePreset:
        key = "synthetic-illegal"
        os_type = "android"
        platform = "Android"
        model = "SYNTHETIC"
        width = 393
        height = 873
        dpr = 2.75
        device_memory = 12
        hardware_concurrency = 8

    unclamped = _emitted_device_memory(_FakePreset, clamp=False)
    clamped = _emitted_device_memory(_FakePreset, clamp=True)

    assert unclamped == 12.0, (
        "the emitter no longer publishes a raw illegal value — if this changed "
        "deliberately, the clamp may now be redundant, but prove it rather "
        "than deleting this control"
    )
    assert unclamped not in LEGAL_DEVICE_MEMORY, "12 must not be a legal value"
    assert clamped == 8.0 and clamped in LEGAL_DEVICE_MEMORY, (
        "the clamp failed to bring an illegal declaration into the legal set"
    )
    assert unclamped != clamped, (
        "clamped and unclamped emissions are identical, so this control proves "
        "nothing about the clamp"
    )


def test_a_legal_sub_1_value_is_not_truncated_to_zero_on_the_way_out():
    """⚠️ A LATENT TRAP CLOSED WHILE THE INVARIANT WAS BEING ESTABLISHED.

    The emitter rendered MEM with ``str(int(device_memory))``. ``0.25`` and
    ``0.5`` are LEGAL values, and ``int()`` maps both to ``0`` — which is
    itself a figure no browser reports. So a correctly-clamped legal value
    would have become illegal at the last possible moment.

    ⛔ NO PRESET DECLARES A SUB-1 FIGURE TODAY, so this is a trap rather than a
    live defect, and it is asserted here rather than described in a comment
    because the clamp above is what makes sub-1 values reachable at all.
    """

    class _FakeSmallPreset:
        key = "synthetic-small"
        os_type = "android"
        platform = "Android"
        model = "SYNTHETIC"
        width = 393
        height = 873
        dpr = 2.75
        device_memory = 0.5
        hardware_concurrency = 4

    emitted = _emitted_device_memory(_FakeSmallPreset, clamp=True)
    assert emitted == 0.5, (
        f"a legal 0.5 GB declaration was published as {emitted} — int() "
        f"truncation turns a legal value into an impossible one"
    )
    assert emitted in LEGAL_DEVICE_MEMORY


# ---------------------------------------------------------------------------
# 4. THE SPECIFIC DEFECT, pinned so it cannot silently return.
# ---------------------------------------------------------------------------


def test_xiaomi_13_declares_what_the_API_would_say_not_what_the_spec_sheet_says():
    """The instance the ticket was filed about.

    ⚠️ Kept DESPITE the class guard above, because the two fail with different
    messages: the sweep says "some preset is illegal", this says "the known
    defect came back". The device genuinely ships 12 GB, so the tempting
    "correction" back to 12 is a real risk from a reader checking the spec
    sheet.
    """
    xiaomi = [p for p in ANDROID_PRESETS if p.key == "xiaomi-13"]
    assert xiaomi, "the xiaomi-13 preset is gone; this guard no longer discriminates"
    assert xiaomi[0].device_memory == 8, (
        "xiaomi-13 declares device_memory=12 again. The device really has 12 GB "
        "of RAM — and navigator.deviceMemory caps at 8, so a real Xiaomi 13 "
        "reports 8. Declare what the API says, not what the hardware has."
    )
