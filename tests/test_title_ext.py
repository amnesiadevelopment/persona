"""`document.title` must never carry the operator's profile label (PS-30).

WHAT THIS FILE USED TO BE. It asserted the opposite invariant:

    def test_script_embeds_profile_name(tmp_path):
        ext_dir = build_title_extension("acc-42", str(tmp_path / "ext"))
        js = (pathlib.Path(ext_dir) / "title.js").read_text(encoding="utf-8")
        assert "acc-42" in js                       # <- the leak, MANDATED

The suite REQUIRED the operator's own chosen label to be embedded in a content
script that ran on `<all_urls>`, so any page lifted it with one regex. Worse, the
label is the crc32 PREIMAGE of `Profile.fingerprint_seed`
(`src/models/profile.py:47`), so a page that read the title recovered the seed
that derives the profile's entire presented machine. That test is CORRECTED here
rather than deleted: deleted, nothing would stop the prefix being reintroduced.
It is now the assertion it should always have been — the name is ABSENT — and it
is stated against the RUNTIME title a page reads, not against a file's bytes.

WHY IT IS ASSERTED AT RUNTIME. A byte check on the emitted file is a poor witness
in both directions, the same trap `tests/native_mask_probe.py` documents for
PS-17 and `tests/test_canvas_ctx_ext.py` re-documents for PS-23. `assert name not
in js` passes against an implementation that reads the name from a manifest, from
a pref, or from a second file — and it cannot see the Firefox engine at all,
which ships the same behaviour through a Playwright init script rather than a
file. So these tests COLLECT THE SCRIPTS THE REAL WIRING ACTUALLY INJECTS, on
both engines, run them in an isolated node realm against a document stub, and ask
what a PAGE sees.

WHY THE PREFIX WAS REMOVED RATHER THAN REPLACED. AC 3 is the binding constraint,
not the label's content: a page that clears its title must read back EMPTY. Any
mechanism that keeps rewriting the title — even one carrying an opaque
per-profile token that leaks no name — is still observable, because no ordinary
page re-writes its own title from a `MutationObserver` on `<head>`. That
behavioural tell announces the masking whatever the label says, which is why
`test_page_title_is_not_rewritten_*` is asserted INDEPENDENTLY of the name tests
and holds even for an operator using meaningless profile names. Profile identity
is carried host-side instead, by the app_id (`--class` on Chromium,
`MOZ_APP_REMOTINGNAME` on Firefox) matched against the `.desktop`
`StartupWMClass` — a surface no page can read.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import types

import pytest

import src.services.browser.invisible_launch as invisible_launch
from src.models.profile import Profile
from tests.test_process import _spawn_chromium_args

# The operator-chosen label the tests hunt for. Distinctive enough that a
# substring hit anywhere in a realm is unambiguous, and shaped like a label a
# real operator would pick.
OPERATOR_LABEL = "acc-42-tania-payouts"

# A deliberately MEANINGLESS label, for the assertions that must hold even when
# the name itself discloses nothing (AC 3). If a test that uses this one fails,
# the finding is about the title MECHANISM, not about the label's content.
OPAQUE_LABEL = "p7"

# The regex from the ticket — literally the one line a page needs.
TITLE_PREFIX_RE = re.compile(r"^\[([^\]]+)\] ")

# The exact leaking content script as it stood at 9c13ce8, kept ONLY as the
# falsification control (AC 4). Injecting it into the same realm must turn the
# assertions below RED; if it does not, the harness is measuring nothing and no
# passing result from this file means anything. It is the `{prefix}` template
# from the deleted `src/services/browser/title_ext.py`, already formatted.
LEGACY_LEAK_SCRIPT = """\
const PREFIX = %s;
function apply() {
  if (!document.title.startsWith(PREFIX)) {
    document.title = PREFIX + document.title;
  }
}
apply();
const head = document.head || document.documentElement;
new MutationObserver(apply).observe(head, {
  subtree: true, childList: true, characterData: true,
});
"""


def legacy_leak_script(profile_name: str) -> str:
    """The pre-PS-30 leak, for a given profile name. Falsification control only."""
    return LEGACY_LEAK_SCRIPT % json.dumps(f"[{profile_name}] ")


# ---------------------------------------------------------------------------
# The realm. A document stub close enough to a browser's that the mechanism
# under test would work in it: `document.title` is a real accessor over a real
# backing string, and writing it notifies MutationObservers registered on
# <head> — which is precisely the loop the legacy script relied on to re-apply
# its prefix after a page set its own title. Without that notification the
# falsification control could not fire and the harness would be a no-op.
# ---------------------------------------------------------------------------
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const sandbox = {};
vm.createContext(sandbox);

// Build the realm from INSIDE the context so `globalThis` carries the context's
// own Object/Function/Reflect — the same reason native_mask_probe.py does it
// this way. A hand-built sandbox object has no Function, and every extension's
// `if (!G || !G.Function) return` bail-out would silently skip the measurement.
vm.runInContext(`
  globalThis.self = globalThis;
  globalThis.window = globalThis;
  globalThis.top = globalThis;

  const __observers = [];
  globalThis.MutationObserver = class MutationObserver {
    constructor(cb) { this.cb = cb; }
    observe(target, opts) { __observers.push({ cb: this.cb, target, opts }); }
    disconnect() {}
    takeRecords() { return []; }
  };
  // Flush every registered observer, as the engine would on a microtask after a
  // <title> text mutation. Bounded: a callback that mutates the title again
  // re-notifies, and an implementation that fights back forever would hang, so
  // the loop is capped and the cap is itself observable via the flush count.
  globalThis.__flushObservers = function () {
    for (let i = 0; i < 8; i++) {
      const before = document.title;
      for (const o of __observers) {
        try { o.cb([{ type: 'characterData', target: __head }], o.cb); } catch (e) {}
      }
      if (document.title === before) return i;
    }
    return 8;
  };
  globalThis.__observerCount = () => __observers.length;

  const __head = { nodeName: 'HEAD', childNodes: [] };
  let __title = '';
  globalThis.document = {
    head: __head,
    documentElement: { nodeName: 'HTML' },
    addEventListener(type, fn) { (this.__listeners ||= {})[type] = fn; },
    __listeners: {},
    createElement(tag) { return { tagName: String(tag).toUpperCase() }; },
    querySelector() { return null; },
    getElementsByTagName() { return []; },
  };
  Object.defineProperty(globalThis.document, 'title', {
    configurable: true,
    enumerable: true,
    get() { return __title; },
    set(v) { __title = String(v); },
  });
  globalThis.navigator = globalThis.navigator || { userAgent: 'probe', languages: ['en-US'] };
  globalThis.location = { href: 'https://example.test/', hostname: 'example.test',
                          protocol: 'https:', origin: 'https://example.test' };
`, sandbox, { filename: 'realm.js' });

// A snapshot of the realm's own enumerable globals BEFORE any injected script
// runs. The AC 7 check diffs against this to catch a page-reachable tag being
// introduced as a replacement for the title prefix.
const globalsBefore = vm.runInContext(
  'Object.keys(globalThis).sort()', sandbox, { filename: 'snap.js' });

// Every script the REAL wiring injects, each isolated: a script that throws in
// this reduced realm (it expects a canvas, a WebGLRenderingContext, …) must not
// abort the ones after it. Swallowing errors cannot hide a title leak — the
// falsification control is a script that runs cleanly here, so if this loop were
// a no-op the control would fail to leak and the AC 4 test would catch it.
const errors = [];
for (const s of cfg.scripts) {
  try {
    vm.runInContext(s.body, sandbox, { filename: s.name });
  } catch (e) {
    errors.push({ name: s.name, error: String(e && e.message || e) });
  }
}

// What a PAGE observes. Each step is what an ordinary page does, and the reads
// are taken after an observer flush so a re-prefix on a microtask is caught.
const probe = vm.runInContext(`(() => {
  const out = {};

  // 1. Title as it stands after document_start, before the page writes one.
  out.initial = document.title;
  out.initialFlushes = __flushObservers();
  out.afterInitialFlush = document.title;

  // 2. A page sets its own title.
  document.title = ${JSON.stringify("__PAGE_TITLE__")};
  out.afterSet = document.title;
  out.setFlushes = __flushObservers();
  out.afterSetFlush = document.title;

  // 3. A page CLEARS its title. This is the read that catches a prefix which
  //    survives on its own, and it is independent of what the label says.
  document.title = '';
  out.afterClear = document.title;
  out.clearFlushes = __flushObservers();
  out.afterClearFlush = document.title;

  // 4. The DOMContentLoaded listener the Firefox variant also registered.
  const dcl = document.__listeners && document.__listeners['DOMContentLoaded'];
  if (typeof dcl === 'function') { try { dcl(); } catch (e) {} }
  out.afterDomContentLoaded = document.title;

  out.observerCount = __observerCount();
  return out;
})()`, sandbox, { filename: 'probe.js' });

const globalsAfter = vm.runInContext(
  'Object.keys(globalThis).sort()', sandbox, { filename: 'snap2.js' });

// Any own enumerable global whose value stringifies to something carrying the
// label would be a relocation of the leak rather than a fix (AC 7).
const globalValues = vm.runInContext(`(() => {
  const out = {};
  for (const k of Object.keys(globalThis)) {
    let v;
    try { v = globalThis[k]; } catch (e) { continue; }
    const t = typeof v;
    if (t === 'string' || t === 'number' || t === 'boolean') out[k] = String(v);
  }
  return out;
})()`, sandbox, { filename: 'globals.js' });

console.log(JSON.stringify({
  probe, errors, globalsBefore, globalsAfter, globalValues,
}));
"""

PAGE_TITLE = "Quarterly Report — example.test"


def _run_realm(tmp_path, scripts):
    """Run `scripts` in an isolated node realm and report what a page observes.

    `scripts` is a list of (name, body) pairs — the actual text the engine
    injects, not a re-creation of it.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    harness = tmp_path / "harness.js"
    harness.write_text(
        _HARNESS.replace("__PAGE_TITLE__", PAGE_TITLE), encoding="utf-8"
    )
    cfg = tmp_path / "cfg.json"
    cfg.write_text(
        json.dumps({"scripts": [{"name": n, "body": b} for n, b in scripts]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(harness), str(cfg)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"probe harness failed: {proc.stderr[-4000:]}")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Script collection — from the REAL wiring on each engine, so these tests see
# whatever a launch actually injects. A leak reintroduced through a different
# file, a different extension, or a different transport is still caught.
# ---------------------------------------------------------------------------

def _chromium_scripts(monkeypatch, tmp_path, profile_name):
    """Every content script Chromium is told to load, via the real spawn path."""
    try:
        captured = _spawn_chromium_args(
            monkeypatch, tmp_path, Profile(name=profile_name), linux=True
        )
        ext_dirs = []
        for arg in captured["args"]:
            if arg.startswith("--load-extension="):
                ext_dirs = [d for d in arg.split("=", 1)[1].split(",") if d]
        scripts = []
        for d in ext_dirs:
            for js in sorted(pathlib.Path(d).glob("*.js")):
                scripts.append((str(js), js.read_text(encoding="utf-8")))
    finally:
        # `_spawn_chromium_args` swaps `process.subprocess.Popen` for a fake —
        # and `process.subprocess` IS the stdlib module object, so that fake is
        # installed GLOBALLY. Left in place it breaks this file's own
        # `subprocess.run` call into node ("'_FakePopen' object does not support
        # the context manager protocol"). The spawn has already been captured by
        # here, so release the patches before the realm runs.
        monkeypatch.undo()
    return scripts, ext_dirs


def _firefox_scripts(monkeypatch, tmp_path, profile_name):
    """Every init script the Firefox context registers, via the real child path."""
    collected = []

    class FakeCtx:
        pages = [object()]

        def add_init_script(self, script=None, *a, **k):
            if isinstance(script, str):
                collected.append(script)

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def _default_context_kwargs(self):
            return {}

        def __enter__(self):
            return FakeCtx()

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)
    monkeypatch.setattr(invisible_launch, "_thread_close_watch", lambda *a, **k: None)
    monkeypatch.setattr(
        invisible_launch, "_kill_profile_firefox",
        lambda d, pids=None, rescan=True: None,
    )
    monkeypatch.setattr(invisible_launch, "_raise_profile_window", lambda *a, **k: None)
    monkeypatch.setattr(invisible_launch, "_ensure_firefox_policies", lambda: None)

    stop = threading.Event()
    stop.set()
    r, w = os.pipe()
    try:
        invisible_launch._child(
            {"profile_dir": str(tmp_path / "ffdir"),
             "profile_name": profile_name, "seed": 1},
            w, stop_event=stop,
        )
    finally:
        os.close(r)
    return [(f"init_script_{i}", s) for i, s in enumerate(collected)]


def _scripts_for(engine, monkeypatch, tmp_path, profile_name):
    if engine == "chromium":
        return _chromium_scripts(monkeypatch, tmp_path, profile_name)[0]
    return _firefox_scripts(monkeypatch, tmp_path, profile_name)


ENGINES = ["chromium", "firefox"]


# ---------------------------------------------------------------------------
# AC 1 + AC 2 + AC 6 — the operator's label never reaches the page's title.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("engine", ENGINES)
def test_profile_name_absent_from_runtime_title(engine, monkeypatch, tmp_path):
    # AC 1: no SUBSTRING of the operator-chosen name appears in document.title,
    # at any point in a page's life. Asserted on the runtime title, so an
    # implementation that reconstructs the name from a pref or a second file is
    # caught just the same as one that inlines it.
    scripts = _scripts_for(engine, monkeypatch, tmp_path, OPERATOR_LABEL)
    out = _run_realm(tmp_path, scripts)
    titles = out["probe"]
    for key, value in titles.items():
        if isinstance(value, str):
            assert OPERATOR_LABEL not in value, f"{key} leaked the profile label"
    # and no fragment of it either — the label split on its separators
    for part in re.split(r"[-_ ]", OPERATOR_LABEL):
        if len(part) < 4:
            continue
        for key, value in titles.items():
            if isinstance(value, str):
                assert part not in value, f"{key} leaked label fragment {part!r}"


@pytest.mark.parametrize("engine", ENGINES)
def test_no_bracket_prefix_on_the_title(engine, monkeypatch, tmp_path):
    # AC 2: the ticket's own one-line detection must come up empty. Run against
    # the RUNTIME title rather than the emitted bytes.
    scripts = _scripts_for(engine, monkeypatch, tmp_path, OPERATOR_LABEL)
    out = _run_realm(tmp_path, scripts)
    for key in ("initial", "afterInitialFlush", "afterSet", "afterSetFlush",
                "afterClear", "afterClearFlush", "afterDomContentLoaded"):
        assert TITLE_PREFIX_RE.match(out["probe"][key]) is None, (
            f"{key} still matches the /^\\[([^\\]]+)\\] / prefix probe"
        )


@pytest.mark.parametrize("engine", ENGINES)
def test_injected_scripts_do_not_carry_the_profile_name(engine, monkeypatch, tmp_path):
    # AC 6, in the direction the old test_script_embeds_profile_name should have
    # had it. This is the byte-level half — deliberately KEPT ALONGSIDE the
    # runtime assertions, not instead of them: it pins the leak from the other
    # side, so a name that is embedded but not yet applied is still caught.
    scripts = _scripts_for(engine, monkeypatch, tmp_path, OPERATOR_LABEL)
    for name, body in scripts:
        assert OPERATOR_LABEL not in body, f"{name} embeds the profile label"


def test_no_title_extension_is_built_for_chromium(monkeypatch, tmp_path):
    # The Chromium half concretely: the title extension is gone from the load
    # list, and no directory is written for it. `--load-extension` is the only
    # channel a content script reaches the page through on this engine.
    scripts, ext_dirs = _chromium_scripts(monkeypatch, tmp_path, OPERATOR_LABEL)
    assert ext_dirs, "no extensions loaded at all — harness is not measuring"
    # Match on the BASENAME: pytest's own tmpdir is named after this test, so
    # every absolute path here contains "title" and a substring check over the
    # full path would fire on all of them.
    assert not any("title" in os.path.basename(d) for d in ext_dirs), ext_dirs
    profile_dir = tmp_path / OPERATOR_LABEL
    assert not (profile_dir / ".persona-title-ext").exists()


def test_title_ext_module_is_gone():
    # The builder itself no longer exists, so it cannot be re-wired by accident.
    with pytest.raises(ModuleNotFoundError):
        __import__("src.services.browser.title_ext")


# ---------------------------------------------------------------------------
# AC 3 — the masking TELL, asserted independently of the label's content.
# These use the meaningless label, so they still hold for an operator whose
# profile names disclose nothing.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("engine", ENGINES)
def test_page_title_is_not_rewritten(engine, monkeypatch, tmp_path):
    # AC 3, first half: a page that sets its title observes it UNCHANGED after a
    # microtask and after a MutationObserver flush. No ordinary page re-writes
    # its own title from an observer on <head>; doing so is the tell that
    # announces the masking regardless of what the label says.
    scripts = _scripts_for(engine, monkeypatch, tmp_path, OPAQUE_LABEL)
    out = _run_realm(tmp_path, scripts)["probe"]
    assert out["afterSet"] == PAGE_TITLE
    assert out["afterSetFlush"] == PAGE_TITLE
    assert out["afterDomContentLoaded"] == ""  # the page cleared it in step 3


@pytest.mark.parametrize("engine", ENGINES)
def test_cleared_page_title_reads_back_empty(engine, monkeypatch, tmp_path):
    # AC 3, second half — the sharpest of the three: a page that CLEARS its
    # title must read back "", not "[label] ". This is what rules out replacing
    # the operator's name with an opaque per-profile tag: the tag would still be
    # sitting here.
    scripts = _scripts_for(engine, monkeypatch, tmp_path, OPAQUE_LABEL)
    out = _run_realm(tmp_path, scripts)["probe"]
    assert out["afterClear"] == ""
    assert out["afterClearFlush"] == ""


@pytest.mark.parametrize("engine", ENGINES)
def test_title_is_empty_before_the_page_writes_one(engine, monkeypatch, tmp_path):
    # A document that never sets a title must stay title-less. A prefix applied
    # at document_start shows up here even on a page that writes nothing.
    scripts = _scripts_for(engine, monkeypatch, tmp_path, OPAQUE_LABEL)
    out = _run_realm(tmp_path, scripts)["probe"]
    assert out["initial"] == ""
    assert out["afterInitialFlush"] == ""


# ---------------------------------------------------------------------------
# AC 7 — the replacement introduces no page-reachable surface of its own.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("engine", ENGINES)
def test_no_new_page_reachable_global_carries_profile_identity(
    engine, monkeypatch, tmp_path
):
    # AC 7: trading the title leak for a `window.__personaTag` is not a fix. Two
    # profiles are run through the identical realm; any per-profile
    # page-reachable tag would differ between them, whether it is named after
    # the profile or holds it as a value.
    a = _run_realm(tmp_path, _scripts_for(engine, monkeypatch, tmp_path, "alpha-one"))
    b = _run_realm(tmp_path, _scripts_for(engine, monkeypatch, tmp_path, "beta-two"))

    assert a["globalsAfter"] == b["globalsAfter"], (
        "a global's NAME varies with the profile — a per-profile page-reachable tag"
    )
    for label, run in (("alpha-one", a), ("beta-two", b)):
        for key, value in run["globalValues"].items():
            assert label not in value, f"global {key} carries the profile label"

    # and no title-carrying global was added as a replacement channel.
    #
    # Scoped to "title" deliberately. The realm legitimately gains a set of
    # `__persona*` globals from the OTHER masking extensions (audio, gpu, hw,
    # locale, stealth, webgl) — those are pre-existing and out of scope here, and
    # a blanket /persona/ match would fire on all of them and pin an unrelated
    # invariant. What AC 7 forbids is this slice trading the title prefix for a
    # new page-reachable surface, and the two checks above already cover that
    # generally: a per-profile tag would either differ in NAME between the two
    # profiles (first assert) or hold the label as a VALUE (second).
    for run in (a, b):
        added = set(run["globalsAfter"]) - set(run["globalsBefore"])
        assert not any(re.search(r"title", name, re.I) for name in added), added


# ---------------------------------------------------------------------------
# AC 4 — FALSIFICATION (rung 3, non-waivable).
#
# Everything above is only worth its runtime if it goes RED against the
# implementation it claims to forbid. These tests put the containment change
# back to how it was — by injecting the exact pre-PS-30 content script into the
# same realm, through the same harness — and assert the probe CATCHES it.
#
# They are bound to observable title behaviour, not to "a helper was called": the
# control is a real script doing the real mutation, so a test that passed against
# an implementation which still leaked would fail here.
# ---------------------------------------------------------------------------

def test_falsification_probe_catches_the_reverted_leak(tmp_path):
    # With the leak restored, EVERY containment assertion above must break.
    out = _run_realm(
        tmp_path, [("legacy_title_ext", legacy_leak_script(OPERATOR_LABEL))]
    )["probe"]

    # AC 1 would have failed:
    assert OPERATOR_LABEL in out["afterSetFlush"]
    # AC 2 would have failed:
    m = TITLE_PREFIX_RE.match(out["afterSetFlush"])
    assert m is not None and m.group(1) == OPERATOR_LABEL
    # AC 3 would have failed — the cleared title reads back the label:
    assert out["afterClearFlush"] == f"[{OPERATOR_LABEL}] "
    # and the page's own title was rewritten under it:
    assert out["afterSet"] != PAGE_TITLE or out["afterSetFlush"] != PAGE_TITLE


def test_falsification_control_leaks_even_with_a_meaningless_label(tmp_path):
    # The AC 3 tests must be falsifiable INDEPENDENTLY of AC 1 — that is the
    # whole point of asserting them with a meaningless label. With the leak
    # restored under OPAQUE_LABEL, the name-based checks would find nothing
    # interesting, but the behavioural ones still go red.
    out = _run_realm(
        tmp_path, [("legacy_title_ext", legacy_leak_script(OPAQUE_LABEL))]
    )["probe"]
    assert out["afterSetFlush"] != PAGE_TITLE, (
        "the mechanism rewrote the page's title — this is the tell AC 3 forbids"
    )
    assert out["afterClearFlush"] == f"[{OPAQUE_LABEL}] "
    assert out["observerCount"] >= 1


def test_falsification_harness_actually_runs_injected_scripts(tmp_path):
    # Guards the guard. Every collection helper feeds scripts through _run_realm,
    # and the harness swallows per-script errors so a reduced realm doesn't abort
    # the run. If that loop were a no-op, every containment test above would pass
    # vacuously. This pins that an injected script really executes and really
    # reaches document.title.
    out = _run_realm(tmp_path, [("marker", "document.title = 'HARNESS-RAN';")])
    assert out["probe"]["initial"] == "HARNESS-RAN"
    assert out["errors"] == []
