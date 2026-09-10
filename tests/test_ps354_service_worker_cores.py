"""PS-354: the ServiceWorker realm gets its cores from the ENGINE, and that
value must equal the page realm's own pick.

THE GAP. ``applyHwPatch`` carries ``hardwareConcurrency`` into Web and Shared
Workers, but a ``ServiceWorkerGlobalScope`` is reached by NEITHER of persona's
identity authors — it is never CONSTRUCTED by the page, so ``worker_wrap``'s
chaining has no constructor to intercept, and an MV3 content script does not run
there. The realm fell through to the engine's seed fallback, or on arms the
engine does not spoof, to the HOST.

THE FIX is the engine flag ``--fingerprint-hardware-concurrency``, which authors
the value before any of our code runs and therefore covers every realm natively.

⛔ WHAT THESE TESTS ARE FOR, AND WHAT THEY ARE NOT. Argv containing the flag is
NOT evidence that the realm is covered — the ticket says so outright, and that
is the substring-check failure that let PS-314's mobile extension parse clean
while failing to execute at all. The realm coverage is a LIVE reading, recorded
on the ticket and in the PR:

    engine personium-152.0.7977.75, xvfb, host cores = 8
    before (no flag):   {"page":8,  "sw":8}    <- both the HOST's value
    after  (flag=13):   {"page":13, "sw":13}
    after  (flag=4):    {"page":4,  "sw":4}

13 and 4 are values the host does not have, so the flag demonstrably reaches the
service worker.

What THIS file pins is the half a unit test genuinely can decide, and it is the
half that decides CORRECTNESS: that the number we pass is the SAME number the
page realm computes, for every profile — not just for the one someone spot-
checked. A hardcoded 8 would satisfy the live reading above and be wrong for
every profile whose pool entry is not 8, which is most of them.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import pytest  # noqa: E402

from src.models.hardware_generation import (  # noqa: E402
    CURRENT_HARDWARE_GENERATION,
)
from src.services.browser.device_ext import (  # noqa: E402
    CORES_MEMORY,
    CORES_MEMORY_SALT,
    build_device_extension,
    cores_memory_for_generation,
    cores_memory_pick,
    hardware_concurrency_for,
)


_GENERATIONS = list(range(CURRENT_HARDWARE_GENERATION + 1))

#: Spread deliberately across the 32-bit space, including the edges. The hash
#: masks at several steps, so a Python port that forgot one mask diverges only
#: on large values — a small-seed sample would pass while shipping two different
#: machines to the page and the engine.
_SEEDS = [0, 1, 2, 3, 7, 42, 99, 1000, 65535, 123456789, 0xCAFE, 0xDEADBEEF, 0xFFFFFFFF]


# ---------------------------------------------------------------------------
# THE LOAD-BEARING TEST: the Python resolver and the emitted JS agree.
# ---------------------------------------------------------------------------


def test_the_python_pick_matches_the_REAL_emitted_javascript(tmp_path):
    """⛔ THE ASSERTION THIS TICKET RESTED ON — AND ITS PREMISE IS NOW GONE.

    PS-354's hazard was a SECOND IMPLEMENTATION: the engine needs the cores
    value before any JS runs, so Python recomputed what the emitted `device.js`
    also computed, and two implementations of one rule drift by construction.
    The only honest check was to EXECUTE the shipped script and compare, which
    is what this test did — rendering the real `device.js`, pulling the pool the
    generation filter actually produced out of `var HCMEM = …`, and running the
    script's own `h32`/`pick` in node against the Python answer.

    ⭐ THERE IS NO LONGER A JS IMPLEMENTATION TO DRIFT AGAINST. The pixelscan
    port deleted both `hardwareConcurrency` installs (and both `deviceMemory`
    ones before them), because the engine authors those properties in every
    realm via `NavigatorConcurrentHardware`/`NavigatorDeviceMemory` on the
    shared `NavigatorBase`. With the installs went `pick`, and with `pick` went
    the `__HCMEM__` substitution — the emitted script no longer carries the pool
    at all. So this comparison has one side.

    ⚠️ THAT IS A REAL REDUCTION IN COVERAGE AND IS RECORDED AS ONE, not waved
    through. What PS-354 could check and nobody can check any more is whether
    the Python resolver agrees with a shipped JS twin. What REPLACES it is
    stronger where it overlaps and weaker where it does not:

    * STRONGER: the page and worker realms can no longer disagree with the
      engine or with each other, because there is only one author. The class of
      defect this test watched for is now unconstructible rather than merely
      unobserved.
    * WEAKER: nothing executes the resolver against an independent oracle. A
      bug in `_h32`/`cores_memory_pick` now reaches the engine flag unchallenged
      by any second implementation.

    So the test is REPOINTED rather than deleted: it pins the two facts that
    keep the reduction honest — the JS twin is genuinely absent (not merely
    renamed), and the Python resolver still exercises the pool rather than
    having collapsed to a constant. `test_ps_device_memory_native.py`'s
    `test_cores_and_memory_come_from_ONE_machine` is the sibling that keeps the
    two flags reading one pick.
    """
    base = tmp_path / "emitted"
    build_device_extension(1337, str(base), CURRENT_HARDWARE_GENERATION,
                           os_type="windows")
    js = (base / "device.js").read_text(encoding="utf-8")

    # (1) The JS twin is really gone — and asserted on the ARTIFACT, not the
    #     module source, because the script is built by `.replace()` into a
    #     template and a source grep passes on a template that merely mentions
    #     the name.
    assert "var HCMEM" not in js and "HCMEM" not in js, (
        "the cores/RAM pool is being substituted into device.js again. If a JS "
        "author for hardwareConcurrency/deviceMemory has returned, restore the "
        "node cross-check above with it — a second implementation without the "
        "comparison is the drift hazard PS-354 was filed on."
    )
    assert "pick(" not in js, (
        "`pick` is back in the emitted script. Its only callsite was the "
        "hardwareConcurrency install; a dead readable copy is what let a "
        "PS-314 falsification arm pass against code that never runs."
    )
    for prop in ("hardwareConcurrency", "deviceMemory"):
        installs = [
            line.strip() for line in js.splitlines()
            if "def(" in line and prop in line
        ]
        assert not installs, (
            f"device.js installs {prop} again: {installs}. The engine is the "
            "sole author; a JS descriptor here restores an own property on the "
            "navigator INSTANCE, which is the position leak this slice closed."
        )

    # (2) The surviving single implementation is not a constant — the check that
    #     would otherwise be satisfied by a hardcoded resolver.
    seen = {
        cores_memory_pick(seed, gen)
        for seed in _SEEDS
        for gen in _GENERATIONS
    }
    assert len({cores for cores, _ in seen}) > 1, (
        f"every sampled profile resolves to the same core count ({seen}) — the "
        "resolver can no longer be distinguished from a constant, and there is "
        "no JS twin left to catch that."
    )


def test_the_flag_value_is_the_page_realms_cores_not_a_constant():
    """The narrower statement of the same rule, on the exported helper.

    ``hardware_concurrency_for`` is what ``process.py`` passes. It must be the
    first element of the page realm's own pick — asserted against
    ``cores_memory_pick`` rather than against a literal, so the two cannot be
    made to agree by editing this test.
    """
    for seed in _SEEDS:
        for gen in _GENERATIONS:
            assert hardware_concurrency_for(seed, gen) == cores_memory_pick(seed, gen)[0]


def test_a_hardcoded_eight_would_be_WRONG_for_most_profiles():
    """⛔ THE FALSIFICATION OF THE OBVIOUS SHORTCUT.

    The owner's live check used a profile reporting 8, and a hardcoded ``8``
    would have passed it. This states, as an executable fact, that most
    profiles are NOT 8 — so the shortcut is not a harmless simplification, it
    is a page/engine mismatch on the majority of profiles.
    """
    values = {
        hardware_concurrency_for(seed, gen)
        for seed in _SEEDS
        for gen in _GENERATIONS
    }
    assert values - {8}, (
        "no sampled profile resolved to anything but 8, so this test cannot "
        "tell a real resolver from a hardcoded constant"
    )
    non_eight = sorted(values - {8})
    assert len(non_eight) >= 2, (
        f"expected several distinct non-8 core counts across the pool, got "
        f"{non_eight}"
    )


def test_the_pick_is_generation_filtered_not_the_whole_pool(monkeypatch):
    """The divisor must be the FILTERED pool's length.

    Dividing by the whole list's length is the original defect this module's
    comments describe: it re-indexes existing profiles onto a different machine
    the moment anyone appends an entry.

    ⚠️ THIS TEST INJECTS A FUTURE-GENERATION ENTRY, AND THAT IS THE ONLY WAY IT
    CAN BITE. Measured: every entry in the shipped ``CORES_MEMORY`` is
    ``since=0``, so today the gen-0 pool, the gen-1 pool and the whole list are
    all the SAME six pairs. Asserting against the live pool therefore cannot
    distinguish a filtered resolver from an unfiltered one — mutating
    ``cores_memory_pick`` to use the whole list left this file entirely GREEN
    before the injection was added, which is a guard that proves nothing.

    So a ``since=CURRENT+1`` entry is appended for the duration of the test.
    An OLD-generation profile must not be able to resolve to it; only a profile
    of the new generation may. That is exactly the promise ``since`` makes to
    profiles already pinned to the pool.
    """
    from src.services.browser import device_ext as de

    future_gen = CURRENT_HARDWARE_GENERATION + 1
    injected = de.CoresMemoryEntry(64, 128, since=future_gen)
    monkeypatch.setattr(de, "CORES_MEMORY", list(de.CORES_MEMORY) + [injected])

    # NOT VACUOUS: the injected entry must really be reachable by SOMEBODY,
    # otherwise "no old profile picks it" is true of a pool nobody can reach.
    new_pool = de.cores_memory_for_generation(future_gen)
    assert injected.pair in new_pool, (
        "the injected future entry is not in its own generation's pool — the "
        "filter is not behaving as this test assumes, so the assertion below "
        "would be meaningless"
    )

    for gen in _GENERATIONS:
        pool = de.cores_memory_for_generation(gen)
        assert injected.pair not in pool, (
            f"generation {gen}'s pool contains a since={future_gen} entry"
        )
        for seed in _SEEDS:
            pick = de.cores_memory_pick(seed, gen)
            assert pick in pool, (
                f"seed {seed} resolved outside generation {gen}'s pool {pool}"
            )
            assert pick != injected.pair, (
                f"seed {seed} at generation {gen} resolved to a FUTURE-generation "
                "entry — the pick is reading the unfiltered pool, which "
                "re-indexes existing profiles onto a different machine"
            )


# ---------------------------------------------------------------------------
# The deviceMemory half — already agreeing, and pinned so it STAYS agreeing.
# ---------------------------------------------------------------------------


def test_every_pool_entry_has_at_least_8gb_so_deviceMemory_cannot_diverge():
    """⭐ THE GUARD FOR A DEFECT THAT DOES NOT EXIST YET (PS-354).

    ⭐ ITS PREMISE IS NOW DISCHARGED, AND THE TEST IS KEPT WITH A NARROWER JOB.
    PS-354 wrote this against a specific hazard: patch 005 pinned the engine's
    ``NavigatorDeviceMemory::deviceMemory()`` to a hardcoded ``return 8;`` with
    NO switch, while the page realm emitted ``Math.min(HM[1], 8)`` — two
    independent authors that agreed only because every ``CORES_MEMORY`` entry
    happened to carry at least 8 GB. Its own instruction was *"give patch 005 a
    --fingerprint-device-memory switch before adding this entry"*.

    ⛔ THAT SWITCH NOW EXISTS (pixelscan port, slice 2). The engine reads
    ``--fingerprint-device-memory``, the launcher passes the profile's own pool
    value through ``spec_device_memory``, and BOTH JS overrides are deleted —
    so the engine is the SOLE author and a page/engine divergence is no longer
    constructible. A sub-8GB entry is therefore SAFE today, and this test is no
    longer the gate it was written as.

    It is kept because the pool's RAM axis is still worth watching: this now
    pins that every entry maps onto a LEGAL Device Memory rung, which is a
    different and still-live claim (a 12 GB entry would be legal as a machine
    spec and illegal as a reported value — see ``spec_device_memory``).
    """
    from src.services.browser.device_ext import (
        LEGAL_DEVICE_MEMORY,
        spec_device_memory,
    )

    for entry in CORES_MEMORY:
        reported = spec_device_memory(entry.memory_gb)
        assert reported in LEGAL_DEVICE_MEMORY, (
            f"entry {entry} maps to {reported}, which is not a value the "
            f"Device Memory API can report ({LEGAL_DEVICE_MEMORY})"
        )


def test_the_page_realm_no_longer_authors_deviceMemory_at_all():
    """The other side of the same coin, INVERTED by the slice that closed it.

    This test used to pin ``min(ram, 8) == 8`` for every pool entry — the page
    realm's emitted value — against the engine's hardcoded 8, so that "they
    already agree" was executable rather than a comment.

    ⛔ THE PAGE REALM NO LONGER EMITS A ``deviceMemory`` AT ALL. Both JS sites
    were deleted when the engine gained its switch, because a JS descriptor is
    the detectable surface this port exists to remove — so the agreement is now
    structural (one author) rather than arithmetic (two authors that match).
    Asserting on the page's emitted value would assert on something that is not
    there, which is why the claim is restated rather than kept.
    """
    import tempfile as _tempfile

    from src.services.browser.device_ext import build_device_extension

    with _tempfile.TemporaryDirectory() as tmp:
        build_device_extension(1337, tmp, CURRENT_HARDWARE_GENERATION,
                               os_type="windows")
        js = pathlib.Path(tmp, "device.js").read_text(encoding="utf-8")

    assert "'deviceMemory'" not in js and '"deviceMemory"' not in js, (
        "device.js defines navigator.deviceMemory again. The engine is the "
        "sole author now (--fingerprint-device-memory); a JS descriptor here "
        "restores the detectable getter AND can disagree with the engine."
    )


# ---------------------------------------------------------------------------
# The launch actually carries it.
# ---------------------------------------------------------------------------


def test_the_launch_passes_the_profiles_own_cores_to_the_engine(monkeypatch, tmp_path):
    """The wiring, asserted on the VALUE rather than on the flag's presence.

    ⛔ NOT a substring check for the flag name. The ticket forbids reading argv
    as evidence of realm coverage, and it is right — but there IS one thing
    argv can honestly answer: whether the number we passed is this profile's
    own pick. So this drives the real launch path and compares the flag's VALUE
    against the independently-computed page-realm pick.

    ⚠️ DRIVEN THROUGH THE CHROMIUM ARM, DELIBERATELY. The flag is a
    fingerprint-patch switch on OUR Chromium engine, so it is assembled in
    ``spawn_browser``'s chromium branch — NOT in ``_spawn_invisible``, which is
    the Firefox launcher and builds a completely different command line. An
    earlier draft of this test drove the Firefox arm and read an empty arg list,
    which would have "passed" the moment anyone loosened the assertion.

    Asserted for several seeds, because a single profile could match a constant
    by coincidence — which is precisely how a hardcoded 8 would have survived.
    """
    from src.models.profile import Profile
    from src.services.browser import process

    for seed in (1, 42, 0xDEADBEEF):
        handed = {}

        def _popen(args, **kwargs):
            handed["args"] = list(args)
            raise RuntimeError("stop: the argv is all this test needs")

        monkeypatch.setattr(process, "popen_in_new_session", _popen)
        monkeypatch.setattr(process, "write_window_entry", lambda name: None)
        monkeypatch.setattr(process, "seed_bookmarks", lambda *a, **k: None)
        monkeypatch.setattr(process, "seed_profile_prefs", lambda *a, **k: None)
        monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))

        class _Store:
            def get(self, *a, **k):
                return None

            def resolve(self, *a, **k):
                return None

        class _Bookmarks:
            def resolve_selection(self, *a, **k):
                return []

        monkeypatch.setattr(process, "ProxyStore", _Store)
        monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)

        # engine="chromium" explicitly: this switch is our Chromium engine's.
        profile = Profile(
            name=f"ps354-{seed}", engine="chromium", fingerprint_seed_value=seed
        )

        with pytest.raises(RuntimeError):
            process.spawn_browser(profile)

        assert handed.get("args"), "the launcher never reached its spawn call"

        flags = [
            a for a in handed["args"]
            if a.startswith("--fingerprint-hardware-concurrency=")
        ]
        assert len(flags) == 1, (
            f"expected exactly one hardware-concurrency flag, got {flags}"
        )
        passed = int(flags[0].split("=", 1)[1])
        expected = hardware_concurrency_for(seed, profile.hardware_generation)
        assert passed == expected, (
            f"seed {seed}: the engine was told {passed} cores but the page realm "
            f"resolves to {expected} — a page/engine mismatch, the same tell "
            "PS-354 exists to remove"
        )
