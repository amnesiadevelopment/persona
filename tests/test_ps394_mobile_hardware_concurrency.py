"""PS-394 — a MOBILE profile's ``--fingerprint-hardware-concurrency`` must come
from its DEVICE PRESET, not from the desktop CORES_MEMORY pool.

THE DEFECT
──────────
``process.py`` builds the launch argv OUTSIDE the mobile/desktop ``if/else``, so
a mobile profile reaches the shared list too. ``--fingerprint-device-memory``
already branches there — ``spec_device_memory(preset.device_memory) if
(is_mobile and preset is not None) else device_memory_for(...)`` — and carries a
comment saying that branch is load-bearing. ``--fingerprint-hardware-concurrency``,
five lines above it, did NOT: it was computed from
``hardware_concurrency_for(seed, generation)`` on every arm, so an Android
profile launched advertising a core count drawn from the DESKTOP distribution.

⛔ WHY THAT IS A REALM DISAGREEMENT AND NOT CODE TIDINESS. ``mobile_ext.py``'s
JS authors ``hardwareConcurrency`` from the preset in every realm its content
script can reach, so the PAGE reports the preset's value. The engine flag
authors the realms JS cannot reach — a ``ServiceWorkerGlobalScope`` above all,
which is never CONSTRUCTED by the page (so ``worker_wrap``'s chaining has no
constructor to intercept) and where an MV3 content script does not run. So the
page said one number and a ServiceWorker said another, INSIDE ONE LAUNCH.
Measured on the release engine before the fix (ticket PS-394):

    iphone-15   page=6  sw=4       galaxy-s23  page=8  sw=12
    iphone-15   page=6  sw=12      xiaomi-13   page=8  sw=4
    iphone-15   page=6  sw=6       xiaomi-13   page=8  sw=6

FIVE OF SIX DISAGREED; the one that agreed did so by coincidence, when the
desktop draw happened to equal the preset. A page that spawns a ServiceWorker
and compares ``navigator.hardwareConcurrency`` identifies a persona profile in
two lines with NO knowledge of the host — the exact class the pixelscan port
exists to remove.

⚠️ A DEDICATED WORKER CANNOT SEE THIS DEFECT. ``applyHwPatch`` carries the value
into Web and Shared Workers, so an earlier page-and-Worker probe found nothing
and the consequence was briefly believed falsified. The discriminating realm is
the ServiceWorker.

⛔ WHAT THESE TESTS CANNOT DO, stated so a green is not over-read. NO ENGINE IS
BUILT AND NO BROWSER IS LAUNCHED here. They pin what persona PASSES on the real
launch path (``spawn_browser`` with only the spawn call swapped); what a built
engine REPORTS in the ServiceWorker realm is the host measurement quoted above.

⚠️ COVER BOTH MOBILE OS FAMILIES, and the asymmetry is a trap. The iOS arm needs
no engine: iOS carries no UA-CH, so ``_mobile_chromium_version`` returns None
without reading the installed engine. An ANDROID profile REFUSES to launch when
that version cannot be read (correct behaviour — it must advertise the engine's
real Chromium version), so the Android arm must stub
``process.installed_chromium_version``. A guard written only against iOS looks
like it covers "mobile" and silently never touches Android — where the defect is
WORSE: all three Android presets declare 8 while the desktop pool emits
{4, 6, 8, 12, 16}, so roughly 65% of Android profiles launched advertising a
count no Android preset declares.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import pytest  # noqa: E402

from src.models.profile import Profile  # noqa: E402
from src.services.browser import process  # noqa: E402
from src.services.browser.device_ext import hardware_concurrency_for  # noqa: E402
from src.services.browser.device_presets import (  # noqa: E402
    ANDROID_PRESETS,
    IOS_PRESETS,
    pick_preset,
)
from src.services.browser.engine_version import ChromiumVersion  # noqa: E402

FLAG = "--fingerprint-hardware-concurrency="


class _Store:
    def get(self, *a, **k):
        return None

    def resolve(self, *a, **k):
        return None


class _Bookmarks:
    def resolve_selection(self, *a, **k):
        return []


def _argv(monkeypatch, tmp_path, profile):
    """Drive the REAL ``spawn_browser`` with only the spawn call swapped.

    The same harness shape ``test_ps354_service_worker_cores.py`` uses, so this
    reads the SHIPPED launcher rather than a re-implementation of it.
    """
    handed = {}

    def _popen(args, **kwargs):
        handed["args"] = list(args)
        raise RuntimeError("stop: the argv is all this test needs")

    monkeypatch.setattr(process, "popen_in_new_session", _popen)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process, "seed_bookmarks", lambda *a, **k: None)
    monkeypatch.setattr(process, "seed_profile_prefs", lambda *a, **k: None)
    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    # ⚠️ ANDROID REFUSES TO LAUNCH WITHOUT THIS. A profile that must advertise
    # the installed engine's Chromium version raises rather than guess one, and
    # no engine is installed in a test container. That refusal is correct
    # behaviour, not a bug — stub the reader, do not weaken the gate.
    monkeypatch.setattr(
        process, "installed_chromium_version", lambda: ChromiumVersion("152.0.7977.75")
    )

    with pytest.raises(RuntimeError):
        process.spawn_browser(profile)

    assert handed.get("args"), "the launcher never reached its spawn call"
    return handed["args"]


def _cores_flag(args) -> int:
    flags = [a for a in args if a.startswith(FLAG)]
    assert len(flags) == 1, f"expected exactly one cores flag, got {flags}"
    return int(flags[0].split("=", 1)[1])


#: Seeds whose DESKTOP pool pick differs from the preset's declared value on at
#: least one mobile arm, so the flag's source is actually discriminated.
#: ⚠️ seed 42 draws 6 from the desktop pool and iphone-15 declares 6 — it AGREES
#: by coincidence and is exactly how this defect survived a spot check. It is
#: kept deliberately as a non-discriminating control, and the sweep below
#: asserts that the discriminating seeds outnumber it.
_SEEDS = [1, 3, 42, 99, 1000, 1337, 0xDEADBEEF]


# ---------------------------------------------------------------------------
# 1. The defect, one arm at a time, on seeds that discriminate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 3, 1000, 0xDEADBEEF])
def test_an_iOS_profile_is_launched_with_its_PRESETS_cores(seed, monkeypatch, tmp_path):
    """⛔ THE RED ARM FOR iOS. Both iPhone presets declare 6 cores; each of these
    seeds draws something else from the desktop pool (8, 4, 8 and 16), so on the
    broken code the engine was told a count the profile's own JS contradicts."""
    preset = pick_preset(seed, "ios", 0)
    assert hardware_concurrency_for(seed, 0) != preset.hardware_concurrency, (
        f"seed {seed} no longer discriminates: the desktop pool and preset "
        f"{preset.key} both say {preset.hardware_concurrency}. Pick another seed "
        "or this test is green on the broken code."
    )

    profile = Profile(
        name=f"ps394-ios-{seed}",
        engine="chromium",
        os_type="ios",
        device_type="mobile",
        fingerprint_seed_value=seed,
    )
    passed = _cores_flag(_argv(monkeypatch, tmp_path, profile))

    assert passed == preset.hardware_concurrency, (
        f"seed {seed}: preset {preset.key} declares "
        f"{preset.hardware_concurrency} cores and its JS reports that in every "
        f"realm it can reach, but the engine was told {passed} — a realm "
        "disagreement inside one launch, visible to a page that spawns a "
        "ServiceWorker and compares"
    )


@pytest.mark.parametrize("seed", [3, 42, 1337, 0xDEADBEEF])
def test_an_Android_profile_is_launched_with_its_PRESETS_cores(
    seed, monkeypatch, tmp_path
):
    """⛔ THE RED ARM FOR ANDROID, and the SHARPER one.

    All three Android presets declare 8, so EVERY desktop-pool value other than
    8 is a visible contradiction — and the pool emits {4, 6, 8, 12, 16}. These
    seeds draw 4, 6, 8 and 16 respectively; the 8 is a coincidental agreement
    kept as a control.
    """
    preset = pick_preset(seed, "android", 0)
    profile = Profile(
        name=f"ps394-android-{seed}",
        engine="chromium",
        os_type="android",
        device_type="mobile",
        fingerprint_seed_value=seed,
    )
    passed = _cores_flag(_argv(monkeypatch, tmp_path, profile))

    assert passed == preset.hardware_concurrency, (
        f"seed {seed}: preset {preset.key} declares "
        f"{preset.hardware_concurrency} cores but the engine was told {passed}, "
        "which is the DESKTOP pool's draw"
    )


# ---------------------------------------------------------------------------
# 2. THE TICKET'S ACCEPTANCE CRITERION, stated as written.
# ---------------------------------------------------------------------------


def test_a_mobile_and_a_desktop_profile_at_the_SAME_seed_report_DIFFERENT_cores(
    monkeypatch, tmp_path
):
    """⭐ AC #1, verbatim: *"A mobile profile and a desktop profile at the SAME
    seed must be able to report different core counts."*

    Same seed, same generation — only the device type differs. On the broken
    code both arms resolved through ``hardware_concurrency_for`` and were
    IDENTICAL by construction, so this could not pass however the seed was
    chosen. That is what makes it the acceptance criterion rather than a
    restatement of the arms above.
    """
    seed = 0xDEADBEEF  # desktop pool draws 16; every mobile preset declares 6 or 8

    desktop = Profile(
        name="ps394-desktop", engine="chromium", os_type="windows",
        fingerprint_seed_value=seed,
    )
    desktop_cores = _cores_flag(_argv(monkeypatch, tmp_path, desktop))
    assert desktop_cores == hardware_concurrency_for(seed, desktop.hardware_generation), (
        "⛔ THE DESKTOP ARM MOVED. This ticket's scope fences the desktop pool "
        "and the seed derivation explicitly: the defect is that mobile does not "
        "consult its own value, NOT that the desktop value is wrong."
    )

    for os_type in ("android", "ios"):
        mobile = Profile(
            name=f"ps394-{os_type}", engine="chromium", os_type=os_type,
            device_type="mobile", fingerprint_seed_value=seed,
        )
        mobile_cores = _cores_flag(_argv(monkeypatch, tmp_path, mobile))
        preset = pick_preset(seed, os_type, mobile.hardware_generation)

        assert mobile_cores == preset.hardware_concurrency
        assert mobile_cores != desktop_cores, (
            f"at seed {seed} the {os_type} profile and the desktop profile were "
            f"both launched with {mobile_cores} cores — the mobile arm is still "
            "reading the desktop pool"
        )


# ---------------------------------------------------------------------------
# 3. The sweep: how much of the mobile population the defect actually touched.
# ---------------------------------------------------------------------------


def test_most_sampled_mobile_profiles_would_have_disagreed_before_the_fix():
    """⚠️ THE NON-VACUITY CONTROL, and the reason the arms above use several
    seeds.

    A single mobile profile whose desktop draw happens to equal its preset
    passes on the BROKEN code — that is how this shipped. This states, as an
    executable fact, that such agreement is the minority: if a future pool or
    preset edit made every sampled seed agree, the arms above would silently
    stop discriminating and this goes red first, naming the reason.
    """
    disagreeing = agreeing = 0
    for seed in _SEEDS:
        for os_type in ("android", "ios"):
            preset = pick_preset(seed, os_type, 0)
            if hardware_concurrency_for(seed, 0) == preset.hardware_concurrency:
                agreeing += 1
            else:
                disagreeing += 1

    assert disagreeing > agreeing, (
        f"only {disagreeing} of {disagreeing + agreeing} sampled mobile profiles "
        "would have disagreed with the desktop pool. The seeds in this file no "
        "longer discriminate a preset-aware flag from a desktop-derived one — "
        "choose seeds whose desktop draw differs from the preset's value."
    )


def test_every_mobile_preset_declares_a_plausible_core_count():
    """The value the flag now publishes comes from the preset table, so a bad
    entry there becomes a launched tell. Real phones report a power-of-two-ish
    small count; nothing here should be a desktop figure."""
    for preset in ANDROID_PRESETS + IOS_PRESETS:
        assert 1 <= preset.hardware_concurrency <= 16, (
            f"preset {preset.key} declares "
            f"{preset.hardware_concurrency} cores, which is not a count a phone "
            "reports — it is now passed to the engine verbatim"
        )


# ---------------------------------------------------------------------------
# 4. The structural claim: the two sibling flags branch the SAME way.
# ---------------------------------------------------------------------------


def test_the_cores_flag_and_the_memory_flag_branch_on_the_same_condition():
    """⭐ THE POINT OF THE FIX'S SHAPE, not a grep for the fix.

    ``--fingerprint-device-memory`` already took the mobile path
    (``is_mobile and preset is not None``); this ticket is that same branch
    missing on its sibling five lines above. Pinning that they share one
    condition is what stops the pair drifting apart again — the next reader who
    adds a third hardware flag has the shape stated rather than inferred.

    ⚠️ A source assertion, and honest about it: the VALUES are asserted on the
    real argv above. This adds only the claim that the two lines agree in shape.
    """
    import pathlib

    source = pathlib.Path(
        process.__file__
    ).read_text(encoding="utf-8")

    cores_line = [
        line for line in source.splitlines() if FLAG in line and "f\"" in line
    ]
    assert cores_line, "the cores flag is no longer built in process.py"

    assert "preset.hardware_concurrency" in source, (
        "the launch never reads the preset's own core count — the mobile arm "
        "is back on the desktop pool"
    )
    assert "hardware_concurrency_for(" in source, (
        "⛔ the DESKTOP resolver is gone. This ticket fences the desktop pool "
        "and the seed derivation: desktop profiles must still resolve their "
        "cores through hardware_concurrency_for"
    )
