"""PS-288: the engine driver we pin MUST support every OS we ship a build for.

WHY THIS FILE EXISTS
--------------------
On 2026-08-26 the upstream driver stopped supporting macOS. Not by dropping a
file — by adding a PLATFORM CHECK. From ``invisible_core==20.16.0`` onward:

* ``download.ensure_binary()`` refuses when ``sys.platform`` is not in
  ``seal.GAMBE_SUPPORTATE`` — and that refusal sits ABOVE ``seal.asset_for()``,
  so it fires before any asset is looked at, and
* ``_headless.make_virtual_display()`` raises on ``darwin`` — on the LAUNCH
  path, downstream of any download, so it is not bypassable by handing the
  launcher a ``binary_path=``.

**Every guard we already had stays green through that.** The three
``engine-baseline.txt`` downgrade guards in ``release.yml`` compare
``BINARY_VERSION`` (``firefox-NN``) against the committed floor. The refusal
first shipped in core ``20.16.0``, which is still sealed to **``firefox-20``** —
our own pinned tag — and whose seal still NAMES both macOS assets. So
``firefox-20 >= firefox-20`` passes, the assets are present in the seal, and the
Mac is dead anyway. Measured, PS-288 §4:

    core 20.16.0, seal firefox-20 (assets include macos-arm64 + macos-x86_64)
      ARCHIVE_NAME('darwin', 'arm64') -> 'firefox-151.0-stealth-macos-arm64.tar.gz'
      ensure_binary()  on darwin      -> NotImplementedError: macOS non e' piu' supportata

``scripts/engine_autobump.py`` cannot see it either: it compares core MAJORS
(``core_major("20.16.0") == core_major("20.14.0") == 20``), so the move that
breaks the Mac is not even a "bump" by its own definition. It is one manual pin
edit, one ``requirements`` refresh, one lockfile change away — and it would ship
a macOS ``.app`` whose engine refuses to download AND refuses to launch, with
every existing check passing.

That is the specific hole this file closes: **nothing in persona asserted that
the driver we pin supports the platforms we build for.** The two facts lived in
two files that never met — ``release.yml``'s build jobs, and the driver package.

WHY IT PROBES BEHAVIOUR INSTEAD OF READING A CONSTANT
-----------------------------------------------------
``GAMBE_SUPPORTATE`` does not exist in the core we ship today (20.14.0); it was
introduced by the very commit that removed macOS. A guard keyed on that name
would answer "absent, so nothing to check" on precisely the version where the
check is cheap and correct, and would then be checking a constant whose spelling
the upstream author has already changed once.

So the question is asked of the CODE, on the two paths that actually refuse:
``make_virtual_display()`` (pure, no network, no filesystem) and the
``GAMBE_SUPPORTATE`` membership test when the symbol is there. Neither probe
downloads anything. A platform is "supported" only if BOTH agree — a driver that
would download for a platform it then refuses to launch on is not support.

WHY THE OS LIST IS PARSED FROM release.yml
-------------------------------------------
Writing ``{"darwin", "linux", "win32"}`` here would be a second copy of the fact
``release.yml`` already states, and the whole defect above is two copies of a
fact drifting apart. The build jobs ARE the declaration of what we ship: delete
``build-macos`` and this guard stops demanding macOS, in the same commit, with
no second edit to remember.
"""
import pathlib
import re
import sys
import unittest.mock as mock

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: release.yml build-job suffix -> the ``sys.platform`` value that OS reports.
#: The mapping is the only hand-written pairing here, and it is a property of
#: Python, not of this project: ``sys.platform`` is "darwin"/"linux"/"win32".
_JOB_TO_SYS_PLATFORM = {
    "macos": "darwin",
    "linux": "linux",
    "windows": "win32",
}


def _shipped_sys_platforms() -> set[str]:
    """The ``sys.platform`` values persona publishes a build for, read from the
    release workflow's own build jobs."""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    # Top-level job ids sit at exactly two spaces of indent: "  build-macos:".
    jobs = set(re.findall(r"^  build-([a-z0-9_]+):", text, flags=re.MULTILINE))
    return {_JOB_TO_SYS_PLATFORM[j] for j in jobs if j in _JOB_TO_SYS_PLATFORM}


def _driver_launches_on(platform_key: str) -> tuple[bool, str]:
    """Does the INSTALLED driver support ``platform_key``?

    Returns ``(supported, why_not)``. Probes the two real refusal sites without
    touching the network:

    1. ``invisible_core.seal.GAMBE_SUPPORTATE`` membership, when that symbol
       exists — this is the declaration ``ensure_binary()`` reads to decide
       whether to refuse before it resolves an asset.
    2. ``invisible_core._headless.make_virtual_display()`` under a patched
       ``sys.platform`` — the launch-path gate, which raises rather than
       returning on an unsupported platform.
    """
    from invisible_core import _headless

    try:
        from invisible_core import seal as _seal

        legs = getattr(_seal, "GAMBE_SUPPORTATE", None)
    except Exception:  # pragma: no cover - core without a seal module
        legs = None

    if legs is not None:
        declared = {p for p, _arch in legs}
        if platform_key not in declared:
            return False, (
                f"invisible_core.seal.GAMBE_SUPPORTATE declares "
                f"{sorted(declared)} — ensure_binary() refuses {platform_key!r} "
                f"before it ever resolves an asset, so publishing a correctly "
                f"named {platform_key} asset does not help"
            )

    with mock.patch.object(_headless.sys, "platform", platform_key):
        try:
            _headless.make_virtual_display()
        except Exception as exc:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return False, (
                f"invisible_core.make_virtual_display() raises on "
                f"{platform_key!r} at LAUNCH: {type(exc).__name__}: {first}"
            )
    return True, ""


@pytest.mark.parametrize("platform_key", sorted(_shipped_sys_platforms()))
def test_pinned_driver_supports_every_os_we_ship(platform_key):
    """A build job for an OS the driver refuses ships an unlaunchable browser.

    This is the assertion the engine-baseline guards structurally cannot make:
    they compare ``firefox-NN`` numbers, and the macOS removal happened at a
    fixed ``firefox-NN`` (PS-288 §4). If this fails on ``darwin``, the pinned
    ``invisible_core`` has dropped macOS and a Mac operator gets an app whose
    engine cannot be obtained OR started — do NOT fix it by deleting the
    ``build-macos`` job: the owner's 2026-09-03 instruction is that macOS is
    required and a negative finding is a report, not a licence to drop the
    platform.
    """
    pytest.importorskip(
        "invisible_core",
        reason="the engine driver is not installed in this environment",
    )
    supported, why_not = _driver_launches_on(platform_key)
    assert supported, (
        f"persona's release workflow builds for {platform_key!r}, but the pinned "
        f"invisible_core does not support it.\n  {why_not}\n"
        f"Shipping this pairing produces an application whose browser engine "
        f"cannot be obtained or launched on that OS. See PS-288 / "
        f"readings/ps288-2026-09-03/REPORT.md."
    )


def test_the_guard_reads_a_real_job_list():
    """The parse is load-bearing: an empty set would make the guard above
    vacuous (parametrize over nothing = zero tests, silently green). Assert it
    found the jobs release.yml actually declares."""
    shipped = _shipped_sys_platforms()
    assert shipped, (
        "no build-<os> jobs parsed out of release.yml — the guard above would "
        "parametrize over an empty set and check nothing"
    )
    assert "darwin" in shipped, (
        "release.yml no longer declares a build-macos job. If macOS was "
        "deliberately dropped this test is what should be updated, and PS-288 "
        "records that the owner required macOS on 2026-09-03 — so a human "
        "decision belongs in that commit message"
    )


def test_the_probe_can_actually_fail():
    """A guard that cannot go red is not a guard.

    Verifies the probe reports UNSUPPORTED for a platform no driver has ever
    supported, rather than defaulting to True whenever it cannot tell. Without
    this, a refactor that swallowed the raise would leave the test above green
    forever on a driver that supports nothing.
    """
    pytest.importorskip("invisible_core")
    supported, why_not = _driver_launches_on("nonexistent-os")
    assert not supported, (
        "the probe answered 'supported' for a platform that cannot exist — it "
        "is defaulting to True and the guard above is vacuous"
    )
    assert why_not, "an unsupported verdict must say why"


def test_shipped_platforms_do_not_silently_include_an_unmapped_os():
    """If release.yml grows a build job for an OS this file has no
    ``sys.platform`` mapping for, that OS is skipped by the guard — silently.
    Name the gap instead of dropping it."""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    jobs = set(re.findall(r"^  build-([a-z0-9_]+):", text, flags=re.MULTILINE))
    unmapped = jobs - set(_JOB_TO_SYS_PLATFORM)
    assert not unmapped, (
        f"release.yml builds for {sorted(unmapped)}, which this guard has no "
        f"sys.platform mapping for — add it to _JOB_TO_SYS_PLATFORM or that OS "
        f"ships unchecked"
    )


def test_sys_platform_mapping_matches_this_interpreter():
    """Anchor the hand-written mapping to a fact this interpreter can confirm,
    so a wrong spelling (e.g. 'win32' typed as 'windows') cannot sit here
    unnoticed on a CI runner of that OS."""
    here = sys.platform
    expected = {v for v in _JOB_TO_SYS_PLATFORM.values()}
    # Only the leg we are actually running on is checkable; the others are
    # checked by the same assertion on their own runners.
    assert here in expected or not here.startswith(("darwin", "linux", "win")), (
        f"this interpreter reports sys.platform={here!r}, which is not among "
        f"{sorted(expected)} — the mapping is wrong for the OS running the suite"
    )
