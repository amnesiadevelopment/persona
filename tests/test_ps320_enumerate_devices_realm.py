"""PS-320 — `mediaDevices.enumerateDevices` must reach a page-built child realm.

The vector was installed at `device.js`'s TOP LEVEL, which reaches only the
realms chromium injects into: `all_frames: True` covers frames the BROWSER
creates, and covers nothing the PAGE builds at runtime — a Web Worker, a fresh
`about:blank`/`srcdoc` iframe, a worker spawned inside one. Every other
Chromium vector rides the realm registry; this one did not.

So a page-built child realm reported the ENGINE's device list while
`screen.width`, `devicePixelRatio`, `hardwareConcurrency` and `deviceMemory`
beside it were the profile's. **A cross-realm mismatch is a stronger tell than
a modified value** — the project's own founding rationale for the registry.

⭐ WHY THIS FILE READS A VALUE AND NOT A SOURCE STRING
-------------------------------------------------------
`tests/test_device_ext.py` asserts `"enumerateDevices" in js`. That substring
is present before and after this change, so it passes identically either way,
and it would pass on a build that installs the wrapper in no realm at all. The
ticket says so explicitly and forbids strengthening it into a second substring
assertion.

The assertions here therefore read what a realm actually RECEIVES, after the
real generated script has run and the shipped bootstrap has transported
whatever it chose to transport.

⚠️ THE INSTRUMENT TRAP THIS FILE IS BUILT AROUND
--------------------------------------------------
An earlier draft of the companion script modelled the child realm by collecting
the registered leaves out of the page context and re-evaluating their
`.toString()` in a fresh `vm` context. It reported a confident MISMATCH.

It was measuring itself. `device.js` is one big IIFE, so the leaves are
closure-scoped and are never globals — nothing could be collected, **zero**
leaves were transported, and the child realm reported the engine list because
it had received nothing at all. That renders identically to the real defect.

Hence `_transport()` below returns the payload the SHIPPED `__pnaInstall`
bootstrap actually prepends to a worker body, and
`test_the_transport_itself_is_real` asserts the transport moved something
before any other test is allowed to mean anything. A comparison whose harness
transported nothing is not evidence, and this file refuses to produce one.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from src.services.browser.device_ext import build_device_extension
from tests.realm_harness import HARNESS

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is needed to run the generated script in a realm"
)

SEED = 24601
GENERATION = 1

# The engine's own default list, standing in for what an UNSPOOFED realm
# reports. Deliberately unlike anything the spoof produces, so "this realm was
# never reached" and "this realm was reached" cannot be confused.
ENGINE_IDS = {"ENGINE-DEFAULT-MIC", "ENGINE-DEFAULT-CAM"}

_FRESH_MEDIA_DEVICES = r"""
function freshMediaDevices() {
  return {
    enumerateDevices: function () {
      return Promise.resolve([
        { deviceId: 'ENGINE-DEFAULT-MIC', groupId: 'ENGINE-GRP-A', kind: 'audioinput', label: '' },
        { deviceId: 'ENGINE-DEFAULT-CAM', groupId: 'ENGINE-GRP-B', kind: 'videoinput', label: '' },
      ]);
    },
  };
}
"""

_PROBE = (
    HARNESS
    + _FRESH_MEDIA_DEVICES
    + r"""
const DEVICE_JS = fs.readFileSync(process.argv[2], "utf8");

function prepare(realm) {
  vm.runInContext(
    "globalThis.navigator = { userAgent: 'probe' };" +
    "globalThis.screen = {};" +
    "globalThis.setTimeout = (f) => f();",
    realm.ctx
  );
  vm.runInContext(
    "globalThis.navigator.mediaDevices = (" + freshMediaDevices.toString() + ")();",
    realm.ctx
  );
}

// PAGE realm: the content script runs in full, as chromium injects it.
const page = makeRealm();
prepare(page);
let pageError = null;
try { vm.runInContext(DEVICE_JS, page.ctx); } catch (e) { pageError = String(e); }

// CHILD realm: a realm the PAGE built. `spawn()` returns the payload the
// SHIPPED bootstrap prepended to the worker body — the real transport, not a
// model of it.
let payload = null, spawnError = null;
try { payload = spawn(page); } catch (e) { spawnError = String(e); }

const child = makeRealm();
prepare(child);
let childError = null;
if (payload) {
  try { vm.runInContext(payload, child.ctx); } catch (e) { childError = String(e); }
}

function read(ctx, label, done) {
  let out;
  try { out = vm.runInContext("navigator.mediaDevices.enumerateDevices()", ctx); }
  catch (e) { return done({ error: String(e) }); }
  Promise.resolve(out).then(
    (list) => done({
      devices: (list || []).map((d) => ({
        kind: d.kind, deviceId: String(d.deviceId), groupId: String(d.groupId),
      })),
    }),
    (e) => done({ error: "rejected: " + e })
  );
}

const results = {
  page_error: pageError,
  spawn_error: spawnError,
  child_error: childError,
  payload_bytes: payload ? payload.length : 0,
  payload_names_leaves: payload
    ? ["applyScreenPatch", "applyHwPatch", "applyDevicesPatch"]
        .filter((n) => payload.indexOf(n) !== -1)
    : [],
};
read(page.ctx, "page", (p) => {
  results.page = p;
  read(child.ctx, "child", (c) => {
    results.child = c;
    console.log(JSON.stringify(results));
  });
});
"""
)


def _run() -> dict:
    """Build the real extension, run the probe, return both realms' readings."""
    d = tempfile.mkdtemp()
    build_device_extension(SEED, d, GENERATION)
    work = pathlib.Path(tempfile.mkdtemp())
    (work / "probe.js").write_text(_PROBE, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(work / "probe.js"), str(pathlib.Path(d) / "device.js")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"the probe did not run, so it measured NOTHING:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])


def test_the_transport_itself_is_real():
    """The harness must move something before any other test here means anything.

    This is the guard against the false finding described in the module
    docstring: a transport that carries NOTHING makes the child realm report
    the engine's list, which is byte-identical to the defect. Without this
    assertion the rest of the file could pass, or fail, for reasons that have
    nothing to do with the product.
    """
    out = _run()
    assert not out["spawn_error"], out["spawn_error"]
    assert out["payload_bytes"] > 1000, (
        f"the bootstrap transported {out['payload_bytes']} bytes — too small to "
        f"be the real payload, so this harness is measuring itself"
    )
    assert out["payload_names_leaves"], (
        "the transported payload names NO registered leaf, so nothing crossed "
        "into the child realm and any reading taken from it is an artefact"
    )


def test_enumerate_devices_reaches_a_page_built_child_realm():
    """THE ACCEPTANCE CRITERION: a child realm reads the PROFILE's device list.

    Reverting the move to `applyDevicesPatch` reddens this on the VALUE the
    child realm received — never on a source-text assertion.
    """
    out = _run()
    assert not out.get("page_error"), out["page_error"]
    assert not out.get("child_error"), out["child_error"]

    page = out["page"]["devices"]
    child = out["child"]["devices"]

    # The page realm must be spoofed, or the probe is broken rather than the
    # product: every assertion below rests on this.
    assert {d["deviceId"] for d in page} != ENGINE_IDS, (
        "the PAGE realm reported the engine's list — the content script did not "
        "run, so this measures the harness"
    )

    assert {d["deviceId"] for d in child} != ENGINE_IDS, (
        "the CHILD realm reports the ENGINE's device list while the page realm "
        "reports the profile's. That page/child mismatch is the tell PS-320 "
        "exists to close — `enumerateDevices` is not riding the realm registry."
    )
    assert child == page, (
        "the child realm's device list differs from the page realm's:\n"
        f"  page : {page}\n  child: {child}\n"
        "Both realms must read the SAME profile-derived list; a difference is "
        "itself a cross-realm tell."
    )


def test_the_child_realm_list_is_the_profiles_and_not_a_constant():
    """A spoof that is real varies with the seed; a constant does not.

    Guards the assertion above against passing on a hardcoded list: two seeds
    must give the child realm two different answers.
    """
    out_a = _run()
    global SEED
    original = SEED
    try:
        SEED = 99999
        out_b = _run()
    finally:
        SEED = original

    a = {d["deviceId"] for d in out_a["child"]["devices"]}
    b = {d["deviceId"] for d in out_b["child"]["devices"]}
    assert a != b, (
        f"the child realm reported the SAME device ids under two seeds ({a}) — "
        f"that is a constant, not a per-profile spoof"
    )


def test_no_spoofed_value_moved():
    """Out-of-scope guard: this ticket is DELIVERY only.

    The device list's contents, its salts and its ordering must be exactly what
    the page realm reported before the move. A delivery fix that quietly
    changes a spoofed value would be a fingerprint change wearing a refactor's
    clothes.
    """
    out = _run()
    page = out["page"]["devices"]

    assert [d["kind"] for d in page] == [
        "audioinput",
        "audioinput",
        "videoinput",
        "audiooutput",
        "audiooutput",
    ], f"the device list's shape moved: {page}"

    # The two 'default' ids and the shared group ids are structural, and are
    # what a checker reads first.
    assert page[0]["deviceId"] == "default"
    assert page[3]["deviceId"] == "default"
    assert page[0]["groupId"] == page[1]["groupId"], "the mic pair must share a group"
    assert page[3]["groupId"] == page[4]["groupId"], "the speaker pair must share a group"
    assert len({d["groupId"] for d in page}) == 3, "mic/cam/speaker are three groups"


def test_every_device_install_rides_a_registered_leaf():
    """AC3 — the uncovered-install census for `device` reads 0 for this vector.

    The census is what turns "we moved one line" into "the module now matches
    its nine siblings". Brace-scan the generated script, find every statement
    that INSTALLS this spoof, and assert each sits inside a leaf the registry
    carries.
    """
    import re

    d = tempfile.mkdtemp()
    build_device_extension(SEED, d, GENERATION)
    js = (pathlib.Path(d) / "device.js").read_text(encoding="utf-8")

    def spans(name: str) -> "list[tuple[int, int]]":
        out = []
        for m in re.finditer(r"function\s+" + re.escape(name) + r"\s*\(", js):
            i = js.index("{", m.end() - 1)
            depth = 0
            for j in range(i, len(js)):
                if js[j] == "{":
                    depth += 1
                elif js[j] == "}":
                    depth -= 1
                    if depth == 0:
                        out.append((js[:i].count("\n") + 1, js[:j].count("\n") + 1))
                        break
        return out

    covered: "list[tuple[int, int]]" = []
    for leaf in ("applyScreenPatch", "applyHwPatch", "applyDevicesPatch", "__pnaInstall"):
        covered += spans(leaf)
    assert covered, "no leaf spans found — the brace scan is broken"

    installs = [
        (i, line.strip())
        for i, line in enumerate(js.splitlines(), 1)
        if re.search(r"enumerateDevices\s*=\s*\w+\(", line)
    ]
    assert installs, (
        "no enumerateDevices install found in the generated script — this test "
        "would pass vacuously"
    )

    uncovered = [
        (i, text) for i, text in installs if not any(a <= i <= b for a, b in covered)
    ]
    assert not uncovered, (
        "these enumerateDevices installs sit OUTSIDE every registered leaf, so "
        "they reach only the realms chromium injects into:\n  "
        + "\n  ".join(f"L{i}: {t}" for i, t in uncovered)
    )
