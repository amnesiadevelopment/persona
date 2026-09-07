"""PS-352 — HiDPI: the window must fit the host, and the FINGERPRINT MUST NOT MOVE.

THE DEFECT
──────────
`--window-size` is interpreted in DIP (device-independent pixels).
`--force-device-scale-factor=1.5` REDEFINES what a DIP is. So a 2560x1440 pick
on a 150% host asked for 3840x2160 PHYSICAL — a window filling a 4K monitor edge
to edge. Two deliberate fixes, each green alone, wrong only in combination.

A second symptom the operator found on a dual-monitor host: the forced-vs-native
DPI mismatch MISLOCATED Chromium's own popups — the three-dot menu opened on the
adjacent monitor. ⚠️ THAT SYMPTOM HAS NO TEST IN THIS FILE AND PROBABLY CANNOT
HAVE ONE: it is a live-host, window-manager observation about where an OS-level
popup lands, which no headless realm can reproduce. It is recorded here so a
reader does not mistake this file's green for coverage of it.

THE FIX, and why it is a THREE-WAY split
────────────────────────────────────────
    macOS         -> keep forcing the Retina 2x (single-scale, no popup issue;
                     without it the UI paints tiny)
    Windows/Linux -> force NOTHING; Chromium's native per-monitor DPI does the
                     scaling the flag was trying to force
Plus a work-area cap for picks larger than the monitor — a SEPARATE constraint,
since a 2560x1440 pick on a 1920x1080 host overflows at scale 1.0 too.

⛔ THE LOAD-BEARING CHECK — WHY THIS FILE IS MOSTLY ABOUT THE FINGERPRINT
─────────────────────────────────────────────────────────────────────────
Removing a flag whose name contains "device-scale-factor" is EXACTLY the shape
that silently changes an identity. `devicePixelRatio` is a fingerprint surface;
if it moved, every profile on every HiDPI host would have a new identity — a
masking regression wearing the costume of a window fix.

It does not move, and the reason is structural rather than incidental: the
extension pins `var DPR = IS_MAC ? 2 : 1`, derived from the PROFILE'S DECLARED
OS and never from the host's scale. The launch flag only sets how many physical
pixels draw one CSS px.

⛔ BUT A SOURCE-LEVEL ARGUMENT IS NOT SUFFICIENT ON THIS AXIS — this is the class
where a plausible reading has been wrong before. So the assertions below READ THE
VALUES FROM A PAGE (the node realm harness PS-327 established), before and after,
and compare. A grep over argv would pass against a build that changed what the
page sees.

⭐ AND THE POSITIVE CONTROL IS MANDATORY. A guard that cannot detect a changed
fingerprint is not a guard. `test_the_fingerprint_guard_actually_detects_a_change`
deliberately alters the spoofed screen and REQUIRES the comparison to go red.
Without it, every assertion here would pass against a harness that reads nothing.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from src.models.profile import Profile
from src.services.browser import process
from src.services.browser.device_ext import build_device_extension
from src.services.browser.engine_version import ChromiumVersion
from src.services.browser.launch_policy import host_workarea_dip


# ---------------------------------------------------------------------------
# Layer 1 — the launch argv
# ---------------------------------------------------------------------------


class _Store:
    def resolve(self, name):
        return ""

    def get(self, name):
        return None


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _argv(monkeypatch, tmp_path, profile, *, is_macos=False, scale=1.0,
          workarea=None):
    """Drive the REAL spawn_browser with only Popen swapped; return its argv."""
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.pid = os.getpid()

    monkeypatch.setattr(
        process, "installed_chromium_version", lambda: ChromiumVersion("152.0.7977.75")
    )
    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    # ⚠️ The runner is Linux, so IS_LINUX stays TRUE for every case: flipping
    # IS_WINDOWS on sends the launch down real Win32 paths (subprocess
    # CREATE_NO_WINDOW, registry reads) that do not exist here, and the test
    # would be measuring the harness rather than the fix.
    #
    # That costs nothing on this vector, because the branch under test keys on
    # IS_MACOS alone: macOS forces a scale, everything else forces none. So the
    # "Windows" cases are expressed as `is_macos=False` plus the host scale the
    # Windows host would report, which is exactly what the branch reads.
    monkeypatch.setattr(process._platform, "IS_MACOS", is_macos)
    monkeypatch.setattr(process, "_host_display_scale", lambda: scale)
    monkeypatch.setattr(process, "host_workarea_dip", lambda: workarea)
    process.spawn_browser(profile)
    return captured["args"]


def _flag(args, name):
    for a in args:
        if a.startswith(f"--{name}="):
            return a[len(f"--{name}=") :]
    return None


# ---------------------------------------------------------------------------
# The defect: the DIP/scale collision
# ---------------------------------------------------------------------------


def test_a_hidpi_windows_launch_does_not_force_a_device_scale_factor(
    monkeypatch, tmp_path
):
    """THE FIX. On a 150% Windows host the flag that redefined the DIP is gone.

    With it present, --window-size=2560,1440 asked for 3840x2160 physical.
    """
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps352-win", os_type="windows", resolution="2560x1440"),
        scale=1.5,
    )
    assert _flag(args, "force-device-scale-factor") is None, (
        "a Windows launch must NOT force a device scale factor — the flag "
        "reinterprets --window-size's unit (2560x1440 DIP x1.5 = 3840x2160 "
        "physical) and mislocates Chromium's popups. Chromium's native "
        "per-monitor DPI does this scaling itself. argv carried "
        f"--force-device-scale-factor={_flag(args, 'force-device-scale-factor')}"
    )


def test_a_hidpi_linux_launch_does_not_force_a_device_scale_factor(
    monkeypatch, tmp_path
):
    """Linux takes the same branch. _host_display_scale() returns 1.0 on most
    Linux desktops so the flag rarely fired, but the branch must not depend on
    that — a fractional-scaling desktop would reintroduce the defect."""
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps352-linux", os_type="windows", resolution="2560x1440"),
        scale=1.5,
    )
    assert _flag(args, "force-device-scale-factor") is None, (
        "a Linux launch must not force a device scale factor either"
    )


def test_macos_keeps_its_explicit_retina_scale(monkeypatch, tmp_path):
    """⭐ THE ASYMMETRY, PINNED. macOS is single-scale, has no popup defect, and
    paints tiny without this flag — so it KEEPS forcing 2x while Windows/Linux
    force nothing.

    This test exists because the split looks like an inconsistency to a reader
    tidying the branch. Removing the macOS arm turns this red.
    """
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps352-mac", os_type="macos", resolution="2560x1440"),
        is_macos=True,
        scale=2.0,
    )
    assert _flag(args, "force-device-scale-factor") == "2", (
        "macOS must keep its explicit Retina 2x — without it the UI paints "
        "unreadably tiny. argv carried "
        f"--force-device-scale-factor={_flag(args, 'force-device-scale-factor')}"
    )


# ---------------------------------------------------------------------------
# The second, scale-independent constraint: fit the work area
# ---------------------------------------------------------------------------


def test_a_pick_larger_than_the_monitor_is_capped_to_the_work_area(
    monkeypatch, tmp_path
):
    """A 2560x1440 pick on a 1920x1040 work area overflows even at scale 1.0,
    so the work-area fit is a SEPARATE constraint from the scale collision."""
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps352-big", os_type="windows", resolution="2560x1440"),
        workarea=(1920, 1040),
    )
    assert _flag(args, "window-size") == "1920,1040", (
        "a pick larger than the host work area must be fitted to it; argv "
        f"carried --window-size={_flag(args, 'window-size')}"
    )


def test_a_pick_that_fits_is_left_exactly_alone(monkeypatch, tmp_path):
    """The cap is a min(), not a resize. A pick inside the work area must
    survive verbatim — otherwise every profile on a large monitor silently
    changes window size."""
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps352-fits", os_type="windows", resolution="1280x720"),
        workarea=(1920, 1040),
    )
    assert _flag(args, "window-size") == "1280,720", (
        f"a pick that fits must be untouched; got {_flag(args, 'window-size')}"
    )


def test_an_unknown_work_area_skips_the_fit_rather_than_zeroing_the_window(
    monkeypatch, tmp_path
):
    """⛔ THE CONVENTION TRAP, pinned.

    The underlying Win32 helper reports failure as (0, 0). If that value reached
    the min(), the window would be ZERO-SIZED — a plausible-looking number that
    produces an unusable browser. `host_workarea_dip()` translates it to None,
    and None must SKIP the fit.
    """
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps352-unknown", os_type="windows", resolution="1280x720"),
        workarea=None,
    )
    assert _flag(args, "window-size") == "1280,720", (
        "an unknown work area must leave the pick alone, not shrink it; got "
        f"{_flag(args, 'window-size')}"
    )


def test_a_zero_work_area_can_never_produce_a_zero_sized_window(monkeypatch):
    """The translation itself, at its own layer: (0,0) in -> None out.

    Asserted on the helper rather than only through a launch, because this is
    the one value whose leak is silently catastrophic.
    """
    import src.services.browser.launch_policy as lp

    monkeypatch.setattr(lp._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(lp, "_host_display_scale", lambda: 1.5)
    import src.services.browser.invisible_launch as il

    monkeypatch.setattr(il, "_work_area", lambda: (0, 0))
    assert host_workarea_dip() is None, (
        "(0, 0) is the Win32 helper's failure reading and MUST become None — "
        "as a number it survives min() and yields a zero-sized window"
    )


def test_the_work_area_is_converted_from_physical_pixels_to_dip(monkeypatch):
    """⭐ THE UNIT CONVERSION IS THE WHOLE POINT.

    `_work_area()` answers in PHYSICAL pixels; `--window-size` is read in DIP.
    Comparing them directly is the same units error as the original defect,
    just in the other direction — it would under-size the window on a scaled
    host. 2560x1400 physical at 150% is 1706x933 DIP.
    """
    import src.services.browser.launch_policy as lp
    import src.services.browser.invisible_launch as il

    monkeypatch.setattr(lp._platform, "IS_WINDOWS", True)
    monkeypatch.setattr(lp, "_host_display_scale", lambda: 1.5)
    monkeypatch.setattr(il, "_work_area", lambda: (2560, 1400))
    assert host_workarea_dip() == (1706, 933), (
        "the physical work area must be divided by the host scale to reach "
        f"DIP; got {host_workarea_dip()}"
    )


def test_an_auto_profile_is_still_untouched(monkeypatch, tmp_path):
    """AUTO must not acquire a --window-size. parse_resolution("auto") is None,
    so neither the cap nor the work-area fit can fire. PS-327 pins this too;
    re-asserted here because this change edits that exact arm."""
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps352-auto", os_type="windows", resolution="auto"),
        workarea=(1920, 1040),
    )
    assert _flag(args, "window-size") is None, (
        f"AUTO must pass no --window-size; got {_flag(args, 'window-size')}"
    )


# ---------------------------------------------------------------------------
# ⛔ THE NON-WAIVABLE FALSIFICATION — what a PAGE sees, before and after
# ---------------------------------------------------------------------------

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  "globalThis.self = globalThis; globalThis.window = globalThis; globalThis.top = globalThis;",
  sandbox
);
vm.runInContext(cfg.stubs, sandbox, { filename: 'stubs.js' });
for (const p of cfg.scripts) {
  vm.runInContext(fs.readFileSync(p, 'utf8'), sandbox, { filename: p });
}
const out = vm.runInContext(cfg.probe, sandbox, { filename: 'probe.js' });
console.log(JSON.stringify({ result: out }));
"""

# Every fingerprint surface this change could plausibly touch, read as a page
# reads it — including the matchMedia dppx answers, which are the ones a
# source-level argument is least able to vouch for.
_READ = (
    "JSON.stringify({"
    "dpr: devicePixelRatio,"
    "screen: [screen.width, screen.height],"
    "avail: [screen.availWidth, screen.availHeight],"
    "depth: [screen.colorDepth, screen.pixelDepth],"
    "dppx1: matchMedia('(resolution: 1dppx)').matches,"
    "dppx2: matchMedia('(resolution: 2dppx)').matches,"
    "dppx15: matchMedia('(resolution: 1.5dppx)').matches,"
    "ratio1: matchMedia('(device-pixel-ratio: 1)').matches,"
    "ratio2: matchMedia('(device-pixel-ratio: 2)').matches,"
    "minres: matchMedia('(min-resolution: 2dppx)').matches"
    "})"
)


def _stubs(host_dpr):
    """A realm whose HOST dpr is stated explicitly.

    ⭐ `host_dpr` is the point of this harness. It stands in for what
    --force-device-scale-factor would have imposed on the real engine. If the
    extension's pinned values were derived from the host scale — the failure
    this file exists to exclude — changing it here would move them.
    """
    return f"""
globalThis.devicePixelRatio = {host_dpr};
globalThis.innerWidth = 1280; globalThis.innerHeight = 577;
globalThis.outerWidth = 1280; globalThis.outerHeight = 680;
globalThis.screen = {{ width: 1280, height: 720, availWidth: 1280,
                       availHeight: 720, colorDepth: 24, pixelDepth: 24 }};
globalThis.navigator = {{ userAgent: "Mozilla/5.0", hardwareConcurrency: 8 }};
globalThis.matchMedia = function (q) {{
  return {{ matches: false, media: q, addListener: function () {{}},
           removeListener: function () {{}} }};
}};
globalThis.document = {{ documentElement: {{}}, addEventListener: function () {{}} }};
"""


def _seen(tmp_path, *, resolution, os_type, host_dpr, tag, screen_override=None):
    """What a page sees, with the host DPR stated."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    work = pathlib.Path(tmp_path) / f"r{tag}"
    work.mkdir(parents=True, exist_ok=True)
    ext = build_device_extension(
        12345, str(work / "dev"), 0, resolution=resolution, os_type=os_type
    )
    script = pathlib.Path(ext) / "device.js"
    if screen_override is not None:
        # The POSITIVE CONTROL's mutation: rewrite the pinned screen so the
        # comparison has something real to detect.
        text = script.read_text(encoding="utf-8")
        text = text.replace(
            f"[{resolution[0]}, {resolution[1]}]",
            f"[{screen_override[0]}, {screen_override[1]}]",
        )
        script.write_text(text, encoding="utf-8")
    (work / "harness.js").write_text(_HARNESS, encoding="utf-8")
    (work / "cfg.json").write_text(
        json.dumps(
            {
                "stubs": _stubs(host_dpr),
                "scripts": [str(script)],
                "probe": _READ,
            }
        ),
        encoding="utf-8",
    )
    out = subprocess.run(
        [node, str(work / "harness.js"), str(work / "cfg.json")],
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(json.loads(out.stdout)["result"])


def test_the_fingerprint_is_identical_with_and_without_a_forced_scale(tmp_path):
    """⛔ THE NON-WAIVABLE ASSERTION.

    BEFORE  = the old behaviour: the engine ran under a forced 1.5 device scale.
    AFTER   = the new behaviour: no forced scale, so the host's native DPR shows.

    Every fingerprint surface must read IDENTICALLY across that change. This is
    measured on what a page sees, not on the argv, because the argv cannot tell
    you what a scanner reads.
    """
    before = _seen(
        tmp_path, resolution=(2560, 1440), os_type="windows", host_dpr=1.5, tag="before"
    )
    after = _seen(
        tmp_path, resolution=(2560, 1440), os_type="windows", host_dpr=1.0, tag="after"
    )
    assert before == after, (
        "the fingerprint MOVED when the forced device scale was removed — this "
        "is a masking regression, not a window fix.\n"
        f"  before (forced 1.5): {before}\n"
        f"  after  (native)    : {after}"
    )
    # And it must be the PROFILE'S value, not the host's, in both arms.
    assert before["dpr"] == 1, (
        "a Windows profile must report devicePixelRatio 1 regardless of the "
        f"host's scale; got {before['dpr']}"
    )
    assert before["screen"] == [2560, 1440], (
        f"the operator's pick must survive verbatim; got {before['screen']}"
    )


def test_a_macos_profile_reports_retina_regardless_of_host_scale(tmp_path):
    """The other half of the DPR rule: macOS profiles pin 2, and that is
    derived from the DECLARED OS, not from the host. A Retina profile launched
    on a scale-1.0 host still reports 2."""
    on_retina = _seen(
        tmp_path, resolution=(2560, 1440), os_type="macos", host_dpr=2.0, tag="macret"
    )
    on_flat = _seen(
        tmp_path, resolution=(2560, 1440), os_type="macos", host_dpr=1.0, tag="macflat"
    )
    assert on_retina == on_flat, (
        f"a macOS profile's fingerprint must not depend on the host scale.\n"
        f"  host 2.0: {on_retina}\n  host 1.0: {on_flat}"
    )
    assert on_flat["dpr"] == 2, (
        f"a macOS profile must report DPR 2; got {on_flat['dpr']}"
    )


def test_the_dppx_answers_agree_with_the_pinned_dpr(tmp_path):
    """The matchMedia dppx answers are a fingerprint surface in their own right
    and are the ones a source argument vouches for least well. A profile
    reporting DPR 1 must answer 1dppx true and 2dppx false — consistently, in
    both arms."""
    win = _seen(
        tmp_path, resolution=(1280, 720), os_type="windows", host_dpr=1.5, tag="dppxwin"
    )
    assert win["dppx1"] is True and win["dppx2"] is False, (
        f"a DPR-1 profile must answer 1dppx true / 2dppx false; got {win}"
    )
    assert win["dppx15"] is False, (
        "the HOST's 1.5 scale must never leak into the dppx answers; got "
        f"dppx15={win['dppx15']}"
    )
    mac = _seen(
        tmp_path, resolution=(1280, 720), os_type="macos", host_dpr=1.0, tag="dppxmac"
    )
    assert mac["dppx2"] is True and mac["dppx1"] is False, (
        f"a DPR-2 profile must answer 2dppx true / 1dppx false; got {mac}"
    )


def test_the_fingerprint_guard_actually_detects_a_change(tmp_path):
    """⭐ THE POSITIVE CONTROL, and it is REQUIRED by the ticket.

    A comparison that cannot go red is not a guard — it would pass against a
    harness that reads nothing, or against an extension that failed to load. So
    deliberately alter the spoofed screen and require the same comparison used
    above to REJECT it.
    """
    honest = _seen(
        tmp_path, resolution=(2560, 1440), os_type="windows", host_dpr=1.0, tag="ctlA"
    )
    tampered = _seen(
        tmp_path,
        resolution=(2560, 1440),
        os_type="windows",
        host_dpr=1.0,
        tag="ctlB",
        screen_override=(1920, 1080),
    )
    assert honest != tampered, (
        "the fingerprint comparison CANNOT DETECT A CHANGED SCREEN — every "
        "other assertion in this file is therefore vacuous. The harness is not "
        "reading what it claims to read."
    )
    assert tampered["screen"] == [1920, 1080], (
        f"the control's mutation did not take effect; got {tampered['screen']}"
    )
