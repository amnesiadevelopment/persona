"""PS-327 — a FORCED-resolution desktop profile must present a COHERENT window.

THE THREE RELATIONS, and why one of them cannot be fixed at the reporting layer
------------------------------------------------------------------------------
A real browser window always satisfies all three:

    R1  outer >= inner    a window contains its own content
    R2  inner <= screen   the content fits the monitor
    R3  outer <= screen   the window fits the monitor

`chromium:outer-size` was the masking matrix's one reverse cell — Firefox
COVERED, Chromium NOT_ESTABLISHED. It was established for #327 by MEASUREMENT
before any spoof was written, on real headful Chromium 152 under Xvfb at
1920x1080 with an operator-picked 1280x720 (reading committed at
readings/ps327-2026-09-05/ac1-live-reading.txt):

    FORCED  outer 1919x1079  vs screen 1280x720   -> R3 VIOLATED, both axes
    AUTO    outer 1919x1079  vs screen 2560x1440  -> already coherent

⭐ THE FIRST ATTEMPT FIXED R3 BY BREAKING R1, AND THAT IS THE INSTRUCTIVE PART.
Clamping the REPORTED `outer` down to the spoofed screen bought R3 and produced
`outerWidth < innerWidth` — a window smaller than its own content, negative
chrome, measured at -639/-256 on the 1280x720 pick. That is arithmetic, not an
implementation slip:

    live inner 1919, picked screen 1280
    R1 needs outer >= 1919;  R3 needs outer <= 1280;  the interval is EMPTY

So NO value the content script reports can satisfy both. The root is R2:
`inner` is the real window's content box, and it is not spoofable at the
reporting layer without breaking layout on real pages.

CAPPING THE WINDOW fixes R2 at its source, and then all three hold at once —
measured live under Xvfb with the window capped to the pick:

    inner [1280, 577]   outer [1280, 680]   screen [1280, 720]
    R1 1280>=1280 OK    R2 1280<=1280 OK    R3 1280<=1280 OK

This is Firefox's own answer to the same question (`_seed_window_size`, #216:
"a window can't be wider than its screen"), reached here through a launch flag
because Chromium has no persisted window-size seed.

WHAT THIS FILE ASSERTS, AND AT WHICH LAYER
------------------------------------------
The fix is now a LAUNCH ARG, so the argv tests drive the real `spawn_browser`
with only `Popen` swapped (the `tests/test_launch_reconciles_device_type.py`
pattern). The relation tests still run the generated extension in a node realm
and ask what a PAGE SEES — never what the source text says — because that is
what AC6 requires: reverting the fix must go red on a VALUE, not on a grep.

⚠️ A VENUE TRAP WORTH KNOWING: `chromium --headless=new` reports
outerWidth/outerHeight as **0**, on the exact property under test, which reads
as "no mismatch" and would establish the cell having measured nothing. The
extension is demonstrably live in such a run (screen.width still differs across
branches), so the zero is an artifact of the venue rather than evidence. The
live half of this ticket needed a real window.
"""

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


# ---------------------------------------------------------------------------
# Layer 1 — the launch argv, where the fix lives
# ---------------------------------------------------------------------------


class _Store:
    def resolve(self, name):
        return ""

    def get(self, name):
        return None


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _argv(monkeypatch, tmp_path, profile):
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
    monkeypatch.setattr(process._platform, "IS_LINUX", True)
    process.spawn_browser(profile)
    return captured["args"]


def _window_size(args):
    for a in args:
        if a.startswith("--window-size="):
            return a[len("--window-size=") :]
    return None


def test_a_forced_desktop_resolution_caps_the_window(monkeypatch, tmp_path):
    """THE FIX. The window is sized to the operator's pick, so the content box
    it produces cannot exceed the screen the extension reports."""
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps327-forced", os_type="windows", resolution="1280x720"),
    )
    assert _window_size(args) == "1280,720", (
        "a FORCED desktop profile must cap its window to the picked resolution; "
        f"argv carried --window-size={_window_size(args)}"
    )


def test_an_auto_desktop_profile_still_passes_no_window_size(monkeypatch, tmp_path):
    """AC4's negative assertion, at the layer the fix now lives in.

    AUTO is the POSITIVE CONTROL for this whole cell: it floors the spoofed
    screen at the live window, so it was already coherent and must stay
    EXACTLY as it was. `parse_resolution("auto")` is None, so the new arm
    cannot fire — asserted rather than assumed.
    """
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(name="ps327-auto", os_type="windows", resolution="auto"),
    )
    assert _window_size(args) is None, (
        "an AUTO desktop launch must pass NO --window-size, exactly as before; "
        f"argv carried --window-size={_window_size(args)}"
    )


def test_a_mobile_profile_still_uses_its_preset_window(monkeypatch, tmp_path):
    """The pre-existing mobile arm is untouched. It sizes the window to the
    device's CSS viewport, which is a different concern from this vector and is
    explicitly out of scope — asserted so this change cannot silently capture
    it."""
    args = _argv(
        monkeypatch,
        tmp_path,
        Profile(
            name="ps327-mobile",
            os_type="android",
            device_type="mobile",
            resolution="1280x720",
        ),
    )
    size = _window_size(args)
    assert size is not None and size != "1280,720", (
        "a mobile launch must keep sizing its window from the DEVICE PRESET, "
        f"not from the desktop resolution pick; got --window-size={size}"
    )


# ---------------------------------------------------------------------------
# Layer 2 — the three relations, as a page reads them
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
const result = vm.runInContext(cfg.probe, sandbox, { filename: 'probe.js' });
console.log(JSON.stringify({ result: result }));
"""

_READ = (
    "JSON.stringify({inner:[innerWidth,innerHeight],"
    "outer:[outerWidth,outerHeight],"
    "screen:[screen.width,screen.height],"
    "avail:[screen.availWidth,screen.availHeight]})"
)


def _stubs(inner_w, inner_h, outer_w, outer_h):
    """A realm with NO window manager: the geometry is whatever we declare, so
    the harness STATES the window rather than inheriting a host's."""
    return f"""
globalThis.__geom = {{ innerWidth: {inner_w}, innerHeight: {inner_h},
                       outerWidth: {outer_w}, outerHeight: {outer_h} }};
for (const k of ["innerWidth", "innerHeight", "outerWidth", "outerHeight"]) {{
  Object.defineProperty(globalThis, k, {{
    configurable: true,
    get: function () {{ return globalThis.__geom[k]; }},
  }});
}}
globalThis.screen = {{
  width: {outer_w}, height: {outer_h}, availWidth: {outer_w},
  availHeight: {outer_h}, colorDepth: 24, pixelDepth: 24,
}};
globalThis.navigator = {{ userAgent: "Mozilla/5.0", hardwareConcurrency: 8 }};
globalThis.matchMedia = function (q) {{
  return {{ matches: false, media: q, addListener: function () {{}},
           removeListener: function () {{}} }};
}};
globalThis.document = {{ documentElement: {{}}, addEventListener: function () {{}} }};
"""


def _seen(tmp_path, *, resolution, inner, outer, os_type="windows", tag=""):
    """What a page in a window of this geometry sees, under this resolution."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    work = pathlib.Path(tmp_path) / f"r{os_type}{tag}{resolution}{inner}"
    work.mkdir(parents=True, exist_ok=True)
    ext = build_device_extension(
        12345, str(work / "dev"), 0, resolution=resolution, os_type=os_type
    )
    (work / "harness.js").write_text(_HARNESS, encoding="utf-8")
    (work / "cfg.json").write_text(
        json.dumps(
            {
                "stubs": _stubs(inner[0], inner[1], outer[0], outer[1]),
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
    return json.loads(json.loads(out.stdout)["result"])


def _assert_coherent(g, label):
    """All three relations, asserted together and named individually."""
    assert g["outer"][0] >= g["inner"][0] and g["outer"][1] >= g["inner"][1], (
        f"{label}: R1 broken — outer {g['outer']} is SMALLER than inner "
        f"{g['inner']}. A window cannot be smaller than its own content; this "
        "is the negative-chrome signature invisible_launch.py's outer-size "
        "probe calibrates as leaking."
    )
    assert g["inner"][0] <= g["screen"][0] and g["inner"][1] <= g["screen"][1], (
        f"{label}: R2 broken — inner {g['inner']} exceeds screen {g['screen']}. "
        "The content box does not fit the monitor it claims."
    )
    assert g["outer"][0] <= g["screen"][0] and g["outer"][1] <= g["screen"][1], (
        f"{label}: R3 broken — outer {g['outer']} exceeds screen {g['screen']}. "
        "A window wider than its own monitor, and the real window extent is "
        "recoverable from the pair."
    )


def test_a_capped_window_satisfies_all_three_relations(tmp_path):
    """THE AC6 TEST, at the layer the page reads.

    The window is capped to the pick (what the launch arg now does), so the
    content box fits and every relation holds. Remove the cap — restore a
    window larger than the pick — and R3 goes red on the reported VALUE, which
    is the test below.
    """
    g = _seen(tmp_path, resolution=(1280, 720), inner=(1280, 577), outer=(1280, 680))
    assert g["screen"] == [1280, 720], (
        f"the operator's pick must survive verbatim — a changed screen is #167 "
        f"re-opening, not this fix working. Got {g['screen']}."
    )
    _assert_coherent(g, "capped FORCED")


def test_without_the_cap_the_window_is_incoherent(tmp_path):
    """THE DEFECT ITSELF, pinned so the cap cannot be removed silently.

    This is the geometry the launch produced BEFORE the cap: a real 1920x1080
    window against a 1280x720 pick. It reproduces the AC1 reading, and it is
    what goes red if the `--window-size` arm is deleted from spawn_browser.
    """
    g = _seen(tmp_path, resolution=(1280, 720), inner=(1919, 936), outer=(1919, 1079))

    assert g["screen"] == [1280, 720]
    assert g["outer"][0] > g["screen"][0], (
        "the uncapped window is the defect this ticket established by "
        f"measurement; got outer {g['outer']} vs screen {g['screen']}"
    )
    # R2 is what trips FIRST, and that ordering is the finding rather than an
    # accident of assertion order: the uncapped window's CONTENT already
    # exceeds the picked screen (1919 > 1280), which is why no reported `outer`
    # can satisfy R1 and R3 together, and why the cap has to act on the window
    # instead of on the reported size.
    with pytest.raises(AssertionError, match="R2 broken"):
        _assert_coherent(g, "uncapped FORCED")
    # ...and R3 is violated too — asserted directly so the test names BOTH
    # broken relations rather than only the one that happens to raise first.
    assert g["outer"][0] > g["screen"][0] and g["outer"][1] > g["screen"][1], (
        f"R3 must also be broken here: outer {g['outer']} vs screen {g['screen']}"
    )


def test_the_auto_branch_is_coherent_and_is_the_control(tmp_path):
    """AC4 at the reporting layer. AUTO floors the spoofed screen at the live
    window by construction, so it is coherent WITHOUT any cap — which is what
    makes the FORCED reading a finding rather than a category."""
    g = _seen(tmp_path, resolution=None, inner=(1919, 936), outer=(1919, 1079))

    assert g["screen"][0] >= 1919 and g["screen"][1] >= 1079, (
        f"AUTO must pick a screen that CONTAINS the window; got {g['screen']}"
    )
    assert g["outer"] == [1919, 1079], (
        f"AUTO's reported outer must be the live window untouched; got {g['outer']}"
    )
    _assert_coherent(g, "AUTO control")


def test_a_pick_larger_than_the_window_keeps_167_closed(tmp_path):
    """#167: gating FORCED on the window extent leaked the render scale — a
    chosen 2560 failed containment, fell through to the auto-pick and reported
    ~4K. Nothing here participates in choosing W/H, so the pick is honoured
    outright. Asserted with the shape that leaked."""
    g = _seen(tmp_path, resolution=(2560, 1440), inner=(1919, 936), outer=(1919, 1079))

    assert g["screen"] == [2560, 1440], (
        f"a pick larger than the window must be honoured outright; got "
        f"{g['screen']} — this is #167's exact failure shape."
    )
    _assert_coherent(g, "pick larger than window")


def test_the_mac_preset_is_coherent_against_its_own_inset(tmp_path):
    """The mac arm carries a 25px menu bar against Windows' 40px taskbar. A
    capped mac window must be coherent against ITS inset, which is the cheap
    way to prove the inset is read rather than hardcoded."""
    g = _seen(
        tmp_path,
        resolution=(1440, 900),
        inner=(1440, 780),
        outer=(1440, 875),
        os_type="macos",
        tag="mac",
    )
    assert g["screen"] == [1440, 900]
    assert g["avail"][1] == 900 - 25, (
        f"mac inset should be 25; availHeight reads {g['avail'][1]}"
    )
    _assert_coherent(g, "capped mac FORCED")
