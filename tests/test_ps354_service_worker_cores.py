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
    """⛔ THE ASSERTION THE WHOLE TICKET RESTS ON.

    The engine needs the cores value BEFORE any JS runs, so it cannot ask the
    page script — the number has to be recomputed in Python. That is a second
    implementation of a rule that already exists in JS, i.e. a drift hazard by
    construction, and the only honest way to hold them together is to EXECUTE
    the real emitted script and compare.

    So this renders the actual ``device.js`` for each profile, pulls the pool
    the generation filter really produced, and runs the script's own
    ``h32``/``pick`` in node against the Python answer. A test that
    re-implemented the hash in Python on both sides would agree with itself
    while both drifted from the shipped script.

    Every generation x a seed spread across the 32-bit range, because the hash
    masks at several steps and a missing mask shows up only on large values.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    mismatches = []
    seen_cores = set()
    for seed in _SEEDS:
        for gen in _GENERATIONS:
            base = tmp_path / f"s{seed}g{gen}"
            build_device_extension(seed, str(base), gen, os_type="windows")
            js = (base / "device.js").read_text(encoding="utf-8")

            # The pool as the SHIPPED script actually received it, not as this
            # test believes the filter behaves.
            hcmem = js.split("var HCMEM =")[1].split(";")[0].strip()

            prog = (
                f"var SEED={seed & 0xFFFFFFFF};"
                "function h32(x){var h=SEED^(x|0);"
                "h=Math.imul(h^(h>>>16),0x85ebca6b);"
                "h=Math.imul(h^(h>>>13),0xc2b2ae35);"
                "return (h^(h>>>16))>>>0;}"
                "function pick(a,s){return a[h32(s)%a.length];}"
                f"var HCMEM={hcmem};"
                f"console.log(JSON.stringify(pick(HCMEM,{CORES_MEMORY_SALT})));"
            )
            out = subprocess.run(
                [node, "-e", prog],
                capture_output=True,
                text=True,
                timeout=60,
                encoding="utf-8",
            )
            assert out.returncode == 0, out.stderr
            js_pick = tuple(json.loads(out.stdout.strip()))
            py_pick = cores_memory_pick(seed, gen)
            seen_cores.add(js_pick[0])
            if js_pick != py_pick:
                mismatches.append((seed, gen, js_pick, py_pick))

    assert not mismatches, (
        "the Python resolver and the emitted device.js disagree about which "
        "machine this profile is, so the ENGINE would author a different core "
        "count than the PAGE — a page/engine mismatch, which is the same tell "
        f"PS-354 exists to remove: {mismatches}"
    )

    # NOT VACUOUS, and this is the assertion that makes the comparison mean
    # something: the sample must actually EXERCISE several pool entries. If
    # every seed landed on the same pair, a hardcoded resolver would pass every
    # comparison above.
    assert len(seen_cores) > 1, (
        f"every sampled profile resolved to the same core count {seen_cores} — "
        "this comparison cannot distinguish a real resolver from a constant"
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

    Patch 005 pins the engine's ``NavigatorDeviceMemory::deviceMemory()`` to a
    hardcoded ``return 8;`` with NO switch, while the page realm emits
    ``Math.min(HM[1], 8)``. Those two agree ONLY because every entry in
    ``CORES_MEMORY`` currently has at least 8 GB of RAM, which makes the min
    exactly 8 every time.

    ⚠️ THAT AGREEMENT IS COINCIDENTAL, NOT DESIGNED. Adding a
    ``CoresMemoryEntry`` with less than 8 GB would silently re-open the realm
    mismatch this ticket closed: the page would report 4 while the
    engine-authored service worker reported 8. Nothing else in the suite can
    see that, because the page and the Web-Worker twin read the SAME pool and
    would agree with each other perfectly — the divergence is only visible
    against the ENGINE, which no unit test launches.

    So this fails LOUDLY at the moment of the edit rather than shipping a tell.
    If you are here because you added a sub-8GB entry: the fix is not to relax
    this test, it is that patch 005's hardcoded 8 needs a switch first.
    """
    smallest = min(entry.memory_gb for entry in CORES_MEMORY)
    assert smallest >= 8, (
        f"CORES_MEMORY now contains an entry with only {smallest} GB of RAM. "
        "PS-354: the page emits Math.min(ram, 8) while the engine's patch 005 "
        "pins deviceMemory to a hardcoded 8 with no switch, so this entry "
        "makes the page and the ServiceWorker realm report DIFFERENT memory — "
        "a page/realm mismatch, which is exactly the tell PS-354 closed for "
        "hardwareConcurrency. Give patch 005 a --fingerprint-device-memory "
        "switch before adding this entry."
    )


def test_the_page_realm_emits_exactly_eight_for_every_current_entry():
    """The other side of the same coin, stated on the VALUE rather than the pool.

    Pins what the page actually emits (``min(ram, 8)``) against the engine's
    hardcoded 8, so the claim "they already agree" is executable rather than a
    comment. If the pool guard above ever has to change, this says what the
    consequence would be.
    """
    for entry in CORES_MEMORY:
        assert min(entry.memory_gb, 8) == 8, (
            f"entry {entry} makes the page report {min(entry.memory_gb, 8)} while the "
            "engine reports 8"
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
