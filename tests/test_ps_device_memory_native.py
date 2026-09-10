"""PIXELSCAN PORT, SLICE 2: navigator.deviceMemory moves from a JS descriptor
to a native engine switch, and the JS goes in the SAME change.

WHY THE "SAME CHANGE" HALF IS THE POINT
───────────────────────────────────────
The owner's measurement established the rule this file exists to enforce: the
badge still returns with the extension layer loaded, so moving a spoof into the
engine while LEAVING the JS getter in place changes nothing measurable — both
authors stay live and the slice cannot be judged. A test that only checked the
patch would pass on exactly that non-change.

So the assertions here come in pairs: the native side is wired, AND the JS side
is gone.

WHAT WAS ACTUALLY WRONG (re-derived this session, at `d300635`)
───────────────────────────────────────────────────────────────
Two independent defects, and only the first was in the brief:

1. `005-hardware-concurrency-fingerprint.patch` did not merely lack a switch —
   it pinned the value to a HARDCODED CONSTANT::

       float NavigatorDeviceMemory::deviceMemory() const {
         return 8;
       }

   So the engine answered 8 for every profile, and no seed could move it.

2. The JS side could not move it either, and this one is easy to misread as an
   entropy bug. `device_ext.py` emitted `Math.min(HM[1], 8)` over a pool whose
   RAM axis is {8, 16} — so the clamp collapsed BOTH rungs onto 8. Measured
   over 4000 seeds, the only reachable value was 8.

⭐ THE CLAMP IS THE SPEC, AND IT MUST NOT BE "FIXED"
────────────────────────────────────────────────────
The Device Memory API reports RAM rounded DOWN to a power of two and clamped to
[0.25, 8]. An 8 GB and a 16 GB machine BOTH report 8 — the API discards the
difference by design. So the collapse is what every real browser on a 16 GB
host does, and making deviceMemory vary per seed would publish a figure that
contradicts the profile's own claimed RAM and that no capped browser produces.
That is a LOUDER tell than the constant, not a quieter one.

The value is still resolved per profile rather than hardcoded, so a future
sub-8GB pool entry starts reporting its own rung with no further change.

⛔ WHAT THESE TESTS CANNOT DO, STATED SO A GREEN IS NOT OVER-READ
─────────────────────────────────────────────────────────────────
NO ENGINE WAS BUILT AND NO BROWSER WAS LAUNCHED. This container has no compiler
for chromium and no Personium chromium binary. These tests pin the PATCH TEXT,
the PYTHON RESOLVER and the LAUNCH WIRING; they cannot observe what a built
engine returns. That the native value actually reaches a page — and the
ServiceWorker realm in particular — is the owner's measurement, not this file's
claim.
"""

from __future__ import annotations

import os
import pathlib
import re
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import pytest  # noqa: E402

from src.models.hardware_generation import (  # noqa: E402
    CURRENT_HARDWARE_GENERATION,
)
from src.services.browser.device_ext import (  # noqa: E402
    CORES_MEMORY,
    LEGAL_DEVICE_MEMORY,
    build_device_extension,
    cores_memory_pick,
    device_memory_for,
    spec_device_memory,
)
from src.services.browser.device_presets import (  # noqa: E402
    ANDROID_PRESETS,
    IOS_PRESETS,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCHES = REPO_ROOT / "engine" / "patches" / "fingerprint"
SWITCH_PATCH = PATCHES / "000-add-fingerprint-switches.patch"
MEMORY_PATCH = PATCHES / "005-hardware-concurrency-fingerprint.patch"
DEVICE_EXT = REPO_ROOT / "src" / "services" / "browser" / "device_ext.py"
PROCESS_PY = REPO_ROOT / "src" / "services" / "browser" / "process.py"

SWITCH_NAME = "fingerprint-device-memory"
SWITCH_SYMBOL = "kFingerprintDeviceMemory"

_SEEDS = [0, 1, 2, 3, 7, 42, 99, 1000, 65535, 123456789, 0xCAFE, 0xDEADBEEF, 0xFFFFFFFF]
_GENERATIONS = list(range(CURRENT_HARDWARE_GENERATION + 1))


# ---------------------------------------------------------------------------
# 1. The switch is declared in ALL THREE places a switch needs to exist.
# ---------------------------------------------------------------------------


def test_the_switch_is_declared_defined_AND_propagated_to_the_renderer():
    """⛔ THREE SITES, AND MISSING THE THIRD FAILS SILENTLY.

    A `--fingerprint-*` switch needs its definition (`ungoogled_switches.cc`),
    its declaration (`.h`) and an entry in
    `RenderProcessHostImpl::PropagateBrowserCommandLineToRenderer`. Without the
    third, the browser process sees the switch and the RENDERER — where
    `NavigatorDeviceMemory::deviceMemory()` actually runs — never does.

    That is not hypothetical: persona already ships FOUR switches that are
    declared AND propagated and read by no patch at all
    (`fingerprint-screen-width`, `-screen-height`, `-device-scale-factor`,
    `-location`), and passing any of them today does nothing, silently, with no
    error. The lesson recorded from that is "grep for a CONSUMER, never for the
    declaration" — so this asserts all three sites plus, below, the consumer.
    """
    text = SWITCH_PATCH.read_text(encoding="utf-8")

    assert f'const char {SWITCH_SYMBOL}[] = "{SWITCH_NAME}";' in text, (
        f"{SWITCH_SYMBOL} is not DEFINED in ungoogled_switches.cc"
    )
    assert f"COMPONENT_EXPORT(UNGOOGLED) extern const char {SWITCH_SYMBOL}[];" in text, (
        f"{SWITCH_SYMBOL} is not DECLARED in ungoogled_switches.h"
    )
    assert f"switches::{SWITCH_SYMBOL}," in text, (
        f"{SWITCH_SYMBOL} is not in PropagateBrowserCommandLineToRenderer, so "
        "the RENDERER never receives it and the switch is silently inert — the "
        "exact shape of the four already-dead switches in this file"
    )


def test_every_hunk_in_both_touched_patches_counts_its_own_lines():
    """A diff whose `@@` counts disagree with its body does not apply.

    Neither patch can be applied in this container (no chromium checkout), so
    the arithmetic is checked directly. This is the one structural error that
    would send the owner a patch that fails at `git apply` after a 3-minute
    rebuild — the cheapest possible thing to get wrong and the most annoying to
    receive.
    """
    for patch in (SWITCH_PATCH, MEMORY_PATCH):
        lines = patch.read_text(encoding="utf-8").splitlines()
        i = 0
        hunks = 0
        while i < len(lines):
            m = re.match(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@", lines[i])
            if not m:
                i += 1
                continue
            hunks += 1
            declared_old, declared_new = int(m.group(2)), int(m.group(4))
            j = i + 1
            counted_old = counted_new = 0
            while j < len(lines) and not lines[j].startswith(("@@", "diff --git")):
                body = lines[j]
                if body.startswith("-"):
                    counted_old += 1
                elif body.startswith("+"):
                    counted_new += 1
                else:
                    counted_old += 1
                    counted_new += 1
                j += 1
            assert (counted_old, counted_new) == (declared_old, declared_new), (
                f"{patch.name} hunk at line {i + 1} declares "
                f"-{declared_old},+{declared_new} but its body counts "
                f"-{counted_old},+{counted_new}. This diff will not apply."
            )
            i = j
        assert hunks, f"{patch.name} contains no hunks at all"


# ---------------------------------------------------------------------------
# 2. The native consumer exists, and the host-RAM call is NOT what it returns.
# ---------------------------------------------------------------------------


def test_the_engine_reads_the_switch_instead_of_returning_a_constant():
    """The defect the owner corrected in the brief, asserted directly.

    `005` did not merely lack a switch — it hardcoded `return 8;`, so no
    profile's seed could move the value. This pins that the constant is gone
    and a real `HasSwitch`/`GetSwitchValueASCII` read replaced it.
    """
    text = MEMORY_PATCH.read_text(encoding="utf-8")
    device_half = text.split("navigator_device_memory.cc", 1)[-1]

    assert "+  return 8;" not in device_half, (
        "patch 005 still hardcodes `return 8;` for deviceMemory — every "
        "profile would report the same value regardless of its seed"
    )
    assert f"HasSwitch(switches::{SWITCH_SYMBOL})" in device_half, (
        "the deviceMemory body does not test for the switch"
    )
    assert f"GetSwitchValueASCII(switches::{SWITCH_SYMBOL})" in device_half, (
        "the deviceMemory body does not read the switch's value"
    )


def test_the_host_RAM_call_is_never_the_value_returned():
    """⛔ INVARIANT #0 — the profile must not report the real machine.

    `ApproximatedDeviceMemory::GetApproximatedDeviceMemory()` is the HOST's
    actual RAM. It must not survive as a live fallback: an unflagged launch
    reporting the host's memory is precisely the leak this patch closes, and it
    would be invisible on the owner's own hardware if his host happened to have
    8 GB.
    """
    text = MEMORY_PATCH.read_text(encoding="utf-8")
    device_half = text.split("navigator_device_memory.cc", 1)[-1]

    for line in device_half.splitlines():
        if not line.startswith("+"):
            continue
        body = line[1:].strip()
        if body.startswith("//"):
            continue  # a comment naming it is how we warn the next reader
        assert "GetApproximatedDeviceMemory" not in body, (
            "the patched deviceMemory body can still return the HOST's real "
            f"RAM: {body!r}"
        )


# ---------------------------------------------------------------------------
# 3. RULE 2 — the JS override is GONE, in both realms.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1337, 0xDEADBEEF])
def test_the_emitted_device_js_defines_no_deviceMemory_in_EITHER_realm(seed, tmp_path):
    """⛔ THE HALF THAT MAKES THE SLICE MEASURABLE.

    Two sites had to go together — the page realm and the WORKER realm
    (`applyHwPatch`, which re-defines the property on `G.navigator` AFTER the
    top-level IIFE and is therefore the one the page actually ends up with).
    Deleting only the page copy would leave the detectable descriptor live in
    workers while the PR claimed the spoof had moved natively.

    Asserted against the REAL emitted script rather than the module source,
    because the script is built by `.replace()` into a template — a source-level
    grep passes on a file that merely mentions the property.
    """
    base = tmp_path / f"seed{seed}"
    build_device_extension(seed, str(base), CURRENT_HARDWARE_GENERATION,
                           os_type="windows")
    js = (base / "device.js").read_text(encoding="utf-8")

    assert "'deviceMemory'" not in js and '"deviceMemory"' not in js, (
        "device.js still declares navigator.deviceMemory. The engine is the "
        "sole author now; a JS descriptor restores the detectable getter and "
        "can disagree with the native value."
    )

    worker_half = js.split("function applyHwPatch", 1)
    assert len(worker_half) == 2, "applyHwPatch is gone — this test is now blind"
    assert "deviceMemory" not in worker_half[1], (
        "the WORKER realm twin still sets deviceMemory. This is the copy that "
        "runs last and wins, so leaving it is equivalent to not doing the slice"
    )


def test_hardwareConcurrency_is_UNTOUCHED_by_this_slice(tmp_path):
    """The negative control: one surface per PR.

    `hardwareConcurrency` shares the pool, the hash, the salt and both realms
    with `deviceMemory`, so a careless deletion takes it with them. It must
    still be authored in both realms exactly as before.
    """
    base = tmp_path / "control"
    build_device_extension(1337, str(base), CURRENT_HARDWARE_GENERATION,
                           os_type="windows")
    js = (base / "device.js").read_text(encoding="utf-8")

    assert "'hardwareConcurrency'" in js, "the page realm lost hardwareConcurrency"
    worker = js.split("function applyHwPatch", 1)[1]
    assert "hardwareConcurrency" in worker, "the worker realm lost hardwareConcurrency"


# ---------------------------------------------------------------------------
# 4. The clamp: spec-legal values only, and the collapse is CORRECT.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.25, 0.25), (0.4, 0.25), (0.5, 0.5), (1, 1.0), (1.9, 1.0),
        (2, 2.0), (3, 2.0), (4, 4.0), (6, 4.0), (8, 8.0),
        (12, 8.0), (16, 8.0), (64, 8.0), (1024, 8.0),
    ],
)
def test_spec_device_memory_rounds_down_to_a_power_of_two_and_caps_at_eight(raw, expected):
    """The W3C rule, stated as a table rather than as prose."""
    assert spec_device_memory(raw) == expected


@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_a_nonpositive_memory_never_yields_a_zero_or_negative_reading(bad):
    """A 0 is not a legal reading and would itself be a tell."""
    assert spec_device_memory(bad) in LEGAL_DEVICE_MEMORY
    assert spec_device_memory(bad) > 0


def test_every_value_the_launcher_can_ever_pass_is_spec_legal():
    """⛔ THE CLAMP MUST HOLD FOR THE WHOLE POOL, not for today's entries.

    Swept over the seed spread and every generation, so an appended pool entry
    with an illegal RAM figure (12, 24, 6) is caught here rather than published.
    """
    for seed in _SEEDS:
        for gen in _GENERATIONS:
            value = device_memory_for(seed, gen)
            assert value in LEGAL_DEVICE_MEMORY, (
                f"seed {seed} gen {gen} would be launched with "
                f"--{SWITCH_NAME}={value}, which no real browser reports"
            )


def test_the_collapse_to_eight_is_the_SPEC_and_is_not_a_bug_to_fix():
    """⭐ THE FINDING MOST LIKELY TO BE 'CORRECTED' BY A LATER READER.

    Every profile persona can generate reports 8, because the pool's RAM axis
    is {8, 16} and the API caps at 8. That looks like lost entropy and is not:
    it is what a real browser on a 16 GB host reports.

    This test states the fact and pins the REASON, so anyone who arrives
    intending to "restore the variation" reads why that would publish a value
    contradicting the profile's own claimed RAM.
    """
    reachable = {device_memory_for(seed, gen) for seed in _SEEDS for gen in _GENERATIONS}
    ram_axis = {entry.memory_gb for entry in CORES_MEMORY}

    assert reachable == {8.0}, (
        f"deviceMemory now varies across {sorted(reachable)}. If a sub-8GB pool "
        "entry was added deliberately this is correct and the assertion should "
        "be updated; if someone removed the cap to 'add entropy', it is not — "
        "the cap is the Device Memory API's own rule."
    )
    assert min(ram_axis) >= 8, (
        f"the pool's RAM axis is now {sorted(ram_axis)}; the assertion above "
        "rests on every entry being at or above the 8 GB cap"
    )


def test_cores_and_memory_come_from_ONE_machine():
    """The pair must not be resolved through two independent paths.

    `hardware_concurrency_for` and `device_memory_for` both read
    `cores_memory_pick`, so a profile cannot publish cores from one machine and
    RAM from another. Asserted by comparing against the pick itself rather than
    by reading the implementations.
    """
    for seed in _SEEDS:
        for gen in _GENERATIONS:
            cores, ram = cores_memory_pick(seed, gen)
            assert device_memory_for(seed, gen) == spec_device_memory(ram), (
                f"seed {seed} gen {gen}: the memory flag does not come from the "
                f"same (cores={cores}, ram={ram}) pick the cores flag uses"
            )


# ---------------------------------------------------------------------------
# 5. The launch carries it — and mobile takes its own value.
# ---------------------------------------------------------------------------


def test_the_launch_wiring_passes_the_switch():
    """The flag is actually on the command line, with a resolved value.

    ⚠️ A substring check on argv is NOT evidence that the realm is covered —
    that is the owner's measurement to make. What it honestly answers is
    whether the launcher passes the switch at all, and whether the value is
    resolved per profile rather than typed as a literal.
    """
    source = PROCESS_PY.read_text(encoding="utf-8")

    assert f"--{SWITCH_NAME}=" in source, "the launch never passes the switch"
    assert "device_memory_for(" in source, (
        "the launch does not resolve the value from the profile — a literal "
        "here would be the hardcoded 8 relocated from C++ into Python"
    )


def test_a_mobile_profile_is_launched_with_its_PRESETS_memory_not_the_desktop_pools():
    """⛔ THE BRANCH THAT KEEPS MOBILE HONEST, and it is easy to miss.

    The argv list is built OUTSIDE the mobile/desktop `if/else`, so a mobile
    profile reaches it too. Mobile's deviceMemory comes from its DEVICE PRESET
    (an iPhone reports 4), not from the desktop `CORES_MEMORY` pool — so
    passing the desktop pick would put desktop RAM in the ServiceWorker realm
    while `mobile_ext.py` said 4 in every realm JS can reach. That is a
    disagreement INSIDE one launch, which is the tell this slice removes on the
    desktop arm; re-introducing it on the mobile arm would be a net loss.

    ⚠️ This asserts the VALUES the two arms resolve to, not the branch's text.
    """
    source = PROCESS_PY.read_text(encoding="utf-8")
    assert "spec_device_memory(preset.device_memory)" in source, (
        "the mobile arm does not take its memory from the device preset"
    )

    for preset in ANDROID_PRESETS + IOS_PRESETS:
        passed = spec_device_memory(preset.device_memory)
        assert passed in LEGAL_DEVICE_MEMORY, (
            f"preset {preset.key} would be launched with an illegal "
            f"--{SWITCH_NAME}={passed}"
        )

    iphones = [p for p in IOS_PRESETS if p.device_memory == 4]
    assert iphones, "no 4 GB iOS preset left; this test no longer discriminates"
    assert spec_device_memory(iphones[0].device_memory) == 4.0, (
        "an iPhone profile would report 8 GB natively while its own JS says 4"
    )
