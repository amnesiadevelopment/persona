"""PS-327 — window.outerWidth/outerHeight must FIT the reported screen.

ASSERT ON BEHAVIOUR, NOT ON THE EMITTED TEXT. Every branch of device_ext is
written into every generated file and chosen at RUNTIME from a baked value, so
a substring check proves nothing about what a given profile actually does —
the trap PS-12 documented and PS-22 re-documented. These tests run the
generated script in an isolated node realm and ask what a PAGE SEES, which is
also what AC6 requires: reverting the fix must go red on the VALUE THE PAGE
RECEIVED, never on a source-text assertion.

WHAT THE CELL IS, AND WHY THE AUTO LEG IS IN EVERY TEST HERE
------------------------------------------------------------
`chromium:outer-size` was the masking matrix's one reverse cell — Firefox
COVERED, Chromium NOT_ESTABLISHED. It was established for #327 by MEASUREMENT
before any spoof was written, on a real headful Chromium 152 under Xvfb at
1920x1080 with an operator-picked 1280x720 (the reading is committed at
readings/ps327-2026-09-05/ac1-live-reading.txt):

    FORCED  outer 1919x1079  vs screen 1280x720   -> outer > screen, BOTH axes
    AUTO    outer 1919x1079  vs screen 2560x1440  -> already coherent

AUTO is the POSITIVE CONTROL and is what makes this a finding rather than a
category: same module, same realm, same window, same seed — one branch cannot
produce the mismatch and the other did nothing to prevent it. So every test
below that asserts the FORCED fix also has an AUTO sibling; a fix that "worked"
by flattening both branches would pass the first and fail the second.

⚠️ A VENUE TRAP WORTH KNOWING, because it produces a FALSE NEGATIVE on exactly
this vector: `chromium --headless=new` reports outerWidth/outerHeight as **0**.
A headless reading therefore says `outer > screen` is false and "establishes"
the cell as already coherent, having measured nothing. The extension is
demonstrably live in such a run (screen.width still differs across branches),
so the zero is an artifact of the venue rather than evidence. The live half of
this ticket needed a real window; that is why the reading above is headful.
The node realm here has no window at all, so these tests supply inner/outer as
explicit stub values — which is the point: the harness states the geometry
instead of inheriting a host's.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.device_ext import build_device_extension

# A realm with NO window manager: inner/outer are whatever the stub declares.
# `outer > inner` models real window chrome; the numbers are the measured ones
# from the live reading so the unit and the live evidence describe one scenario.
_STUBS = r"""
globalThis.__geom = { innerWidth: 1919, innerHeight: 936,
                      outerWidth: 1919, outerHeight: 1079 };
for (const k of ["innerWidth", "innerHeight", "outerWidth", "outerHeight"]) {
  Object.defineProperty(globalThis, k, {
    configurable: true,
    get: function () { return globalThis.__geom[k]; },
  });
}
globalThis.screen = {
  width: 1919, height: 1079, availWidth: 1919, availHeight: 1079,
  colorDepth: 24, pixelDepth: 24,
};
globalThis.navigator = { userAgent: "Mozilla/5.0", hardwareConcurrency: 8 };
globalThis.matchMedia = function (q) {
  return { matches: false, media: q, addListener: function () {},
           removeListener: function () {} };
};
globalThis.document = { documentElement: {}, addEventListener: function () {} };
"""

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
const result = vm.runInContext(cfg.probe, sandbox, { filename: 'probe.js' });
console.log(JSON.stringify({ result: result }));
"""

_READ = (
    "JSON.stringify({inner:[innerWidth,innerHeight],"
    "outer:[outerWidth,outerHeight],"
    "screen:[screen.width,screen.height],"
    "avail:[screen.availWidth,screen.availHeight]})"
)


def _run(tmp_path, *, resolution, os_type="windows", probe=_READ, tag=""):
    """Build the device extension and read the geometry a page would see."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    work = pathlib.Path(tmp_path) / f"p{os_type}{tag}{resolution}"
    work.mkdir(parents=True, exist_ok=True)
    ext = build_device_extension(
        12345, str(work / "dev"), 0, resolution=resolution, os_type=os_type
    )
    harness = work / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    cfg = work / "cfg.json"
    cfg.write_text(
        json.dumps(
            {
                "stubs": _STUBS,
                "scripts": [str(pathlib.Path(ext) / "device.js")],
                "probe": probe,
            }
        ),
        encoding="utf-8",
    )
    out = subprocess.run(
        [node, str(harness), str(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(json.loads(out.stdout)["result"])


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------


def test_a_forced_resolution_smaller_than_the_window_reports_a_window_that_fits(
    tmp_path,
):
    """THE AC6 TEST. Revert the pin and this goes red on the reported VALUE.

    A window wider than its own monitor is something no real un-maximized
    window shows, and it additionally hands the page the true window extent
    (1919) that the spoofed screen exists to conceal.
    """
    g = _run(tmp_path, resolution=(1280, 720))

    assert g["screen"] == [1280, 720], (
        "the operator's pick must survive verbatim — a changed screen here is "
        f"#167 re-opening, not this fix working. Got {g['screen']}."
    )
    assert g["outer"][0] <= g["screen"][0], (
        f"outerWidth {g['outer'][0]} exceeds screen.width {g['screen'][0]}: a "
        "window wider than its monitor, and the real window extent is "
        "recoverable from the pair."
    )
    assert g["outer"][1] <= g["screen"][1], (
        f"outerHeight {g['outer'][1]} exceeds screen.height {g['screen'][1]}. "
        "BOTH axes failed in the live reading; a width-only pin leaves the "
        "pair impossible."
    )


def test_the_auto_branch_is_unchanged_and_is_the_control(tmp_path):
    """AC4's negative assertion, and the control that gives the test above its
    meaning. AUTO floors the screen at the window by construction, so it was
    already coherent and must stay EXACTLY as it was."""
    g = _run(tmp_path, resolution=None)

    assert g["screen"][0] >= 1919 and g["screen"][1] >= 1079, (
        "the AUTO branch picks a screen that CONTAINS the window; a screen "
        f"smaller than the window here means the floor broke. Got {g['screen']}."
    )
    assert g["outer"] == [1919, 1079], (
        "AUTO's reported outer must be the live window untouched — clamping it "
        f"here would be the fix leaking onto the control. Got {g['outer']}."
    )


def test_the_pick_is_honoured_outright_so_167_stays_closed(tmp_path):
    """#167: gating FORCED on the window extent leaked the render scale — a
    chosen 2560 failed containment, fell through to the auto-pick and reported
    ~4K. Pinning outer cannot re-open it, because it never participates in
    choosing W/H. Asserted with a pick LARGER than the window, which is the
    shape that leaked."""
    g = _run(tmp_path, resolution=(2560, 1440), tag="big")

    assert g["screen"] == [2560, 1440], (
        f"a pick larger than the window must be honoured outright; got "
        f"{g['screen']} — this is #167's exact failure shape."
    )
    assert g["outer"] == [1919, 1079], (
        "with the screen larger than the window there is nothing to clamp: the "
        f"real window extent is already coherent. Got {g['outer']}."
    )


def test_the_clamp_is_not_an_inflation(tmp_path):
    """A genuinely small window must keep reporting its own small extent.

    The clamp is min(inner + chrome, screen) — it may only ever REDUCE. If it
    were written as `= screen` it would pass the headline test above while
    telling every small window to claim it fills the monitor, which is its own
    tell and would be invisible to a test that only checks outer <= screen.

    ⚠️ THE FIXTURE HAS TO DESCRIBE A WINDOW THAT GENUINELY FITS. A first draft
    used inner 800x600 / outer 814x691 against a 1280x720 pick, and the clamp
    correctly trimmed 691 to 680 — because availHeight is 720-40 and a window
    691 tall would overlap its own taskbar. That was the FIXTURE describing the
    very defect under test, not the clamp over-reaching. 600x400 fits with room
    to spare, so any trimming here is the fix leaking.
    """
    small = _STUBS.replace(
        "innerWidth: 1919, innerHeight: 936,\n"
        "                      outerWidth: 1919, outerHeight: 1079",
        "innerWidth: 600, innerHeight: 400,\n"
        "                      outerWidth: 614, outerHeight: 491",
    )
    assert small != _STUBS, "the stub substitution did not take"

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    work = pathlib.Path(tmp_path) / "small"
    work.mkdir(parents=True, exist_ok=True)
    ext = build_device_extension(
        12345, str(work / "dev"), 0, resolution=(1280, 720), os_type="windows"
    )
    (work / "harness.js").write_text(_HARNESS, encoding="utf-8")
    (work / "cfg.json").write_text(
        json.dumps(
            {
                "stubs": small,
                "scripts": [str(pathlib.Path(ext) / "device.js")],
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
    g = json.loads(json.loads(out.stdout)["result"])

    assert g["outer"] == [614, 491], (
        "a small window must report its OWN extent (inner + real chrome), not "
        f"be inflated to the spoofed screen. Got {g['outer']} against a "
        "declared 614x491."
    )
    assert g["outer"][0] < g["screen"][0], "still fits its screen"


def test_the_mac_preset_clamps_against_its_own_inset(tmp_path):
    """The clamp uses the SAME INSET the availHeight pin uses (25 mac / 40
    win), so the reported window fits the reported WORK AREA and not merely the
    raw screen. A mac profile is the cheap way to prove the inset is read
    rather than hardcoded to 40."""
    g = _run(tmp_path, resolution=(1440, 900), os_type="macos", tag="mac")

    assert g["screen"] == [1440, 900]
    assert g["avail"][1] == 900 - 25, (
        f"mac inset should be 25; availHeight reads {g['avail'][1]}"
    )
    assert g["outer"][1] <= g["avail"][1], (
        f"outerHeight {g['outer'][1]} exceeds the mac work area "
        f"{g['avail'][1]} — the clamp is not reading the same inset."
    )
