"""The Chromium verification tier reports its EXIT'S timezone TO A PAGE.

PS-132. Read this file's reason for existing before changing it.

The defect was NOT in the product. ``services/browser/process.py`` has always
pinned a concrete zone on every launch. What had no zone was the VERIFICATION
TIER — the instrument — so a reading taken behind a proven Warsaw exit came
back UTC+0 (the container's own clock), and the checker's free
timezone-against-address cross-check called the product spoofed for it. The
harness manufactured the verdict it then reported.

WHY THESE TESTS LAUNCH A REAL BROWSER, when a unit test over ``_launch_args``
is a thousand times faster and already exists next door in
``test_verify_engine_selection.py``:

    The finding was precisely that WHAT THE PAGE SAW disagreed with WHAT THE
    PRODUCT INTENDED.

A test asserting the flag is on the command line asserts the intention — the
exact half that was never in doubt — and it would pass just as happily if
Chromium ignored the flag outright. That is the project's own recurring
failure mode (PS-11: "tests that assert on what was written, not on what
happens"), and reproducing it in the fix for a page-observability defect would
be the joke writing itself. So these read ``Intl``, ``Date`` and a derived
format OUT OF A RUNNING PAGE, over CDP.

ALL THREE SURFACES, because the ticket asks for it and because they can
genuinely disagree: the IANA name comes from ICU, the offset comes from the
time zone the process resolved, and the formatted string is rendered from
both. Fixing one while another still reads the host clock relocates the defect
rather than closing it, and only reading all three can tell those apart.

These are SKIPPED, never silently passed, wherever the engine or a display is
missing — an absent engine must not read as a clean bill of health.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

from src.services.verify import chromium_tier

# The three surfaces, read in one page evaluation so they describe ONE moment
# in ONE browser rather than three separate launches that could each have gone
# differently. The instant is fixed and in summer so the expected Warsaw offset
# is unambiguous (CEST, UTC+2) rather than depending on when the suite runs.
_INSTANT = "2026-08-23T12:00:00Z"
_PROBE = (
    "({"
    "iana: Intl.DateTimeFormat().resolvedOptions().timeZone,"
    f"offsetMin: new Date('{_INSTANT}').getTimezoneOffset(),"
    f"dateString: new Date('{_INSTANT}').toString(),"
    "formatted: new Intl.DateTimeFormat('en-US', {timeZoneName:'long',"
    " year:'numeric', month:'short', day:'2-digit'})"
    f".format(new Date('{_INSTANT}')),"
    "})"
)

_ZONE = "Europe/Warsaw"


def _engine_present() -> bool:
    try:
        chromium_tier._engine_binary()
        return True
    except Exception:
        return False


def _display_present() -> bool:
    import shutil

    return bool(os.environ.get("DISPLAY", "").strip()) or bool(
        shutil.which("Xvfb")
    )


# Gated rather than assumed, and the reasons are separate because the remedies
# are: no engine is a provisioning problem, no display is an apt-get.
requires_engine = pytest.mark.skipif(
    not _engine_present(),
    reason="persona's chromium engine is not installed on this host",
)
requires_display = pytest.mark.skipif(
    not _display_present(),
    reason="no DISPLAY and no Xvfb: persona ships a HEADED browser",
)
# No `slow` marker: this project registers no custom markers, and an
# unregistered one is inert metadata that selects nothing and warns on every
# run. These are gated by the two conditions that actually matter instead.
pytestmark = [requires_engine, requires_display]


def _read_zone_from_a_running_page(timezone: str) -> dict:
    """Launch the tier's OWN command line and ask the PAGE what zone it is in.

    The args come from ``chromium_tier._launch_args`` rather than from a list
    written here, which is the whole point: a copy would keep passing after
    the tier stopped emitting the flag, and a divergent second copy of the
    launch path is the defect class this ticket belongs to (PS-103). The only
    thing added is the CDP port, because a test has to get in.
    """
    profile_dir = tempfile.mkdtemp(prefix="ps132-tz-")
    args = chromium_tier._launch_args(
        chromium_tier._engine_binary(),
        profile_dir,
        seed=1337,
        declared_machine="windows",
        # No exit at this venue and none needed: the zone is the variable
        # under test, and routing through a real proxy would make a network
        # the suite does not own into a dependency of a timezone assertion.
        proxy_server=chromium_tier.NO_PROXY,
        timezone=timezone,
        # This host forbids the user namespace chromium's sandbox needs
        # (measured: unshare(CLONE_NEWUSER) -> EPERM), and PS-128's reading
        # carried the same waiver. It governs process isolation, not which
        # zone the browser reports — the mechanism under test is untouched by
        # it — but it is stated rather than hidden.
        allow_unsandboxed=not chromium_tier.sandbox_available(),
        extension_dirs=[],
    )
    args = [a for a in args if a != "about:blank"]
    args += ["--remote-debugging-port=0", "--remote-allow-origins=*"]
    args.append("about:blank")

    display, xvfb = chromium_tier._ensure_display()
    env = dict(os.environ, DISPLAY=display)
    # A case that pins no zone must really inherit none, whatever the shell
    # that started the suite happened to export.
    env.pop("TZ", None)

    proc = subprocess.Popen(
        args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        port = _wait_for_port(proc, profile_dir)
        _wait_for_cdp(port)
        return _evaluate(port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - slow teardown
            proc.kill()
        if xvfb is not None:
            xvfb.terminate()


def _wait_for_port(proc, profile_dir: str) -> int:
    """The AppImage extracts itself on first run, so this waits generously."""
    path = os.path.join(profile_dir, "DevToolsActivePort")
    deadline = time.monotonic() + chromium_tier.CDP_READY_TIMEOUT
    while time.monotonic() < deadline:
        if os.path.exists(path):
            head = open(path).read().split("\n")[0].strip()
            if head:
                return int(head)
        if proc.poll() is not None:
            raise AssertionError(
                f"the engine exited rc={proc.returncode} before opening a "
                f"debug port"
            )
        time.sleep(0.3)
    raise AssertionError("the engine never published a DevToolsActivePort")


def _wait_for_cdp(port: int) -> None:
    """The port file lands BEFORE the HTTP endpoint answers.

    Its own wait with its own deadline: reusing the already-consumed one above
    is how this reports a false "chromium is broken" on a cold container that
    spent its whole budget extracting the AppImage.
    """
    deadline = time.monotonic() + 60.0
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=2
            ) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # not up yet
            last = exc
            time.sleep(0.5)
    raise AssertionError(f"the CDP endpoint never answered: {last}")


def _evaluate(port: int) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = None
        last = None
        for _ in range(30):
            try:
                browser = pw.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}"
                )
                break
            except Exception as exc:
                last = exc
                time.sleep(1.0)
        if browser is None:
            raise AssertionError(f"could not attach over CDP: {last}")
        try:
            context = browser.contexts[0]
            # A FRESH page: the restored startup tab can be torn down under
            # the evaluation, which reads as a defect and is only ever the
            # instrument.
            page = context.new_page()
            page.goto("about:blank")
            return page.evaluate(_PROBE)
        finally:
            browser.close()


@pytest.fixture(scope="module")
def zoned_page() -> dict:
    """One launch, shared by the assertions below.

    Module-scoped because it is a real browser start (tens of seconds on a
    cold AppImage) and every assertion is a different question about the SAME
    reading — which is also what makes "the surfaces agree" meaningful: three
    launches could each be individually right and still not establish that.
    """
    return _read_zone_from_a_running_page(_ZONE)


def test_the_page_reports_the_exits_zone_by_name(zoned_page):
    """The row PS-128 read as ``Africa/Abidjan`` behind a Warsaw exit.

    This is the reported surface, and it is the one a checker quotes.
    """
    assert zoned_page["iana"] == _ZONE, (
        f"the page reported {zoned_page['iana']!r}. A UTC+0 zone here is the "
        f"container's own clock reaching the page — exactly the PS-132 "
        f"finding — rather than a badly chosen plausible one."
    )


def test_the_offset_date_reports_agrees_with_that_zone(zoned_page):
    """A second surface, which can disagree with the first.

    ``getTimezoneOffset`` is minutes BEHIND UTC, so Warsaw in summer (CEST,
    UTC+2) is -120. Asserting the name alone would let a fix that renamed the
    zone while ``Date`` still ran on the host clock pass — which is the
    "relocates the defect" outcome the ticket names.
    """
    assert zoned_page["offsetMin"] == -120, (
        f"the page reported offset {zoned_page['offsetMin']} minutes; "
        f"Europe/Warsaw at {_INSTANT} is UTC+2, i.e. -120. An offset of 0 is "
        f"the host's UTC clock."
    )


def test_formatting_derived_from_the_zone_agrees_too(zoned_page):
    """The third surface: what a page RENDERS, not what it computes.

    ``Date.prototype.toString`` and a long-form ``Intl`` format both spell the
    zone out, and both are read here because they are rendered from the
    resolved zone rather than re-derived from the name.
    """
    assert "GMT+0200" in zoned_page["dateString"], zoned_page["dateString"]
    assert "Central European Summer Time" in zoned_page["dateString"], (
        zoned_page["dateString"]
    )
    assert "Central European Summer Time" in zoned_page["formatted"], (
        zoned_page["formatted"]
    )


def test_reverting_the_fix_turns_this_red(zoned_page):
    """The ticket asks for this explicitly, so it is asserted, not assumed.

    Launching the SAME way with no zone is what the tier did before the fix.
    If that still reported Warsaw, every assertion above would be passing for
    some reason other than the change — a green that proves nothing. It must
    read the host clock instead.

    This is the control arm, and it is the one test here that would still have
    passed BEFORE the fix. That is deliberate: it pins the contrast, and it
    is what makes the other three mean what they say.
    """
    unzoned = _read_zone_from_a_running_page("")
    assert unzoned["iana"] != _ZONE, (
        "with no zone pinned the engine must fall back to the host clock; "
        "reporting the exit's zone anyway would mean these tests are not "
        "measuring the flag at all"
    )
    assert unzoned["offsetMin"] == 0, (
        f"this host runs on UTC, so the un-pinned launch should report offset "
        f"0; got {unzoned['offsetMin']}. If this fails the host's own clock "
        f"is not UTC and the contrast above is weaker than it looks, not wrong"
    )
