"""PS-320 AC1/AC2 — establish the reading BEFORE writing any fix.

The ticket's first acceptance criterion is a MEASUREMENT, not a change: in a
page-built child realm, record what `enumerateDevices()` actually returns
today, against what the top window returns in the same run. Its honest bound #1
is explicit that no browser was launched in research and that *"a child realm
actually reports a different device list is the implementer's first task, not
an inherited premise."*

So this script asserts NOTHING and fixes NOTHING. It prints what each realm
sees. Run it at the base to establish the premise (AC2), and again after the
move to show the mismatch is gone (AC5's falsification runs the same way).

⭐ WHY IT USES THE SHIPPED HARNESS RATHER THAN A HAND-ROLLED TWO-CONTEXT MODEL
-------------------------------------------------------------------------------
A first draft of this script modelled the child realm by collecting the
registered leaves out of the page realm and re-evaluating their `.toString()`
in a fresh `vm` context. **That model is structurally wrong and it produced a
confident false finding**, which is worth recording because the failure looks
exactly like the defect:

`device.js` is one big IIFE. `applyScreenPatch` and `applyHwPatch` are CLOSURE
-scoped inside it and are never globals, so nothing could be collected out of
the page context — the harness transported **zero** leaves, the child realm
received nothing at all, and it duly reported the engine's device list. That
renders as a page/child mismatch and is **indistinguishable from the real
one**, except that its cause is my harness rather than the product.

The instrument now REFUSES to report a comparison when it transported no leaf
(`instrument_error`), and it drives the real transport instead: the shipped
`__pnaInstall` bootstrap intercepts `Worker` construction and iframe adoption
from INSIDE the IIFE, and `tests/realm_harness.py` is the in-tree model of
exactly that. The ticket says to reuse that harness rather than write a second,
and this is also why.

WHAT THIS INSTRUMENT CAN AND CANNOT SAY
-----------------------------------------
It reads the PRODUCT's real generated `device.js`, so what it measures is the
shipped script rather than a paraphrase, and it transports through the shipped
bootstrap rather than through a model of it.

It is still `node:vm`, not a browser. So it is evidence about REACH — which
installs cross into a realm the page built — and it is NOT a live-browser
reading. The ticket's honest bound #1 stands: a live confirmation is a stronger
instrument and this does not claim to be one.

Run from the repo root::

    python3 -m scripts.ps320_realm_reading
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.services.browser.device_ext import build_device_extension  # noqa: E402
from tests.realm_harness import HARNESS  # noqa: E402

NODE = shutil.which("node")

SEED = 24601
GENERATION = 1


def _generated_device_js() -> str:
    d = tempfile.mkdtemp()
    build_device_extension(SEED, d, GENERATION)
    return (pathlib.Path(d) / "device.js").read_text(encoding="utf-8")


def _registered_leaves(js: str) -> "list[str]":
    """Which leaves the generated script registers with the realm registry.

    ⚠️ THE IDIOM IS `__pnaInstall(SELF, <leaf>)` — and the `SELF` matters as
    much as the call. Getting this wrong is the easy way to make the whole
    instrument lie, twice over:

    * A first draft matched `<leaf>(G` and `<leaf>.toString(`, both of which
      occur in `device.js`'s COMMENTS as often as in its code, so the answer
      was partly derived from prose.
    * A second draft matched any first argument and picked up `LEAF` — the
      installer's OWN parameter name, from the eight internal recursive calls
      (`__pnaInstall(w, LEAF)`, `__pnaInstall(cw, LEAF)`) by which the
      bootstrap covers child realms. `LEAF` is not a leaf; it is the variable
      holding one.

    `realm_bootstrap_js` emits exactly one top-level `__pnaInstall(SELF, ...)`
    per registered leaf, so that call — and nothing else — is the registration.
    """
    names = sorted(set(re.findall(r"__pnaInstall\(\s*SELF\s*,\s*(\w+)\s*\)", js)))
    if not names:
        raise SystemExit(
            "no `__pnaInstall(SELF, <leaf>)` call found in the generated "
            "script. Either the registration idiom changed or the build is "
            "broken — refusing to report a realm comparison whose transport is "
            "unproven, because an empty transport produces a mismatch that "
            "looks exactly like the defect."
        )
    return names


# The ENGINE's own default device list stands in for what an unspoofed realm
# reports. Deliberately distinct from anything the spoof produces, so "this
# realm was never reached" and "this realm was reached" cannot look alike.
_ENGINE_DEFAULT = r"""
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
    + _ENGINE_DEFAULT
    + r"""
const DEVICE_JS = fs.readFileSync(process.argv[2], "utf8");

// ── PAGE realm ──────────────────────────────────────────────────────────────
// The content script runs in full, exactly as chromium injects it.
const page = makeRealm();
vm.runInContext(
  "globalThis.navigator = { userAgent: 'probe' };" +
  "globalThis.screen = {};" +
  "globalThis.setTimeout = (f) => f();",
  page.ctx
);
vm.runInContext("(" + freshMediaDevices.toString() + ")", page.ctx);
vm.runInContext("globalThis.navigator.mediaDevices = (" +
  freshMediaDevices.toString() + ")();", page.ctx);

let pageError = null;
try { vm.runInContext(DEVICE_JS, page.ctx); } catch (e) { pageError = String(e); }

// ── CHILD realm ─────────────────────────────────────────────────────────────
// A realm the PAGE built at runtime: a Worker. `spawn()` returns the payload
// the shipped bootstrap PREPENDED to the worker body — i.e. exactly what the
// registry chose to transport, and nothing else. That is the real transport,
// not a model of it.
let workerPayload = null;
let spawnError = null;
try { workerPayload = spawn(page); } catch (e) { spawnError = String(e); }

const child = makeRealm();
vm.runInContext(
  "globalThis.navigator = { userAgent: 'probe' };" +
  "globalThis.screen = {};" +
  "globalThis.setTimeout = (f) => f();",
  child.ctx
);
vm.runInContext("globalThis.navigator.mediaDevices = (" +
  freshMediaDevices.toString() + ")();", child.ctx);

let childError = null;
if (workerPayload) {
  try { vm.runInContext(workerPayload, child.ctx); }
  catch (e) { childError = String(e); }
}

// Which leaves actually crossed, read off the transported payload rather than
// assumed. A payload that names no leaf transported nothing, and a comparison
// taken on it would measure the harness.
const leafNames = JSON.parse(process.argv[3]);
const transported = workerPayload
  ? leafNames.filter((n) => workerPayload.indexOf(n) !== -1)
  : [];

function readDevices(ctx, label, done) {
  let out;
  try {
    out = vm.runInContext("navigator.mediaDevices.enumerateDevices()", ctx);
  } catch (e) {
    return done({ realm: label, error: String(e) });
  }
  Promise.resolve(out).then(
    (list) => done({
      realm: label,
      devices: (list || []).map((d) => ({
        kind: d.kind,
        deviceId: String(d.deviceId).slice(0, 26),
        groupId: String(d.groupId).slice(0, 12),
      })),
    }),
    (e) => done({ realm: label, error: "rejected: " + e })
  );
}

const results = {
  page_error: pageError,
  spawn_error: spawnError,
  child_error: childError,
  transported_leaves: transported,
  payload_bytes: workerPayload ? workerPayload.length : 0,
};

// ⚠️ A transport that moved NOTHING produces a page/child mismatch that is
// indistinguishable from the real defect. Refuse to report a comparison whose
// own instrument is unproven, rather than let it read as a finding.
if (transported.length === 0) {
  results.instrument_error =
    "NO registered leaf appears in the transported payload, so this run " +
    "measures the harness rather than the product. Any mismatch would be an " +
    "artefact — this is the exact false finding an earlier draft produced.";
}

readDevices(page.ctx, "page", (p) => {
  results.page = p;
  readDevices(child.ctx, "child", (c) => {
    results.child = c;
    console.log(JSON.stringify(results, null, 2));
  });
});
"""
)


def main() -> int:
    if NODE is None:
        print("node is required to evaluate the generated script", file=sys.stderr)
        return 2

    js = _generated_device_js()
    leaves = _registered_leaves(js)

    work = pathlib.Path(tempfile.mkdtemp())
    (work / "device.js").write_text(js, encoding="utf-8")
    (work / "probe.js").write_text(_PROBE, encoding="utf-8")

    proc = subprocess.run(
        [NODE, str(work / "probe.js"), str(work / "device.js"), json.dumps(leaves)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        print("the probe did not run, so it measured NOTHING:", file=sys.stderr)
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return 1

    out = json.loads(proc.stdout)

    print("PS-320 — what each realm reports for enumerateDevices()")
    print("=" * 74)
    print(f"  registered leaves       : {leaves}")
    print(f"  transported to child    : {out['transported_leaves']}")
    print(f"  payload bytes           : {out['payload_bytes']}")
    for key, label in (
        ("page_error", "page realm error"),
        ("spawn_error", "spawn error"),
        ("child_error", "child realm error"),
    ):
        if out.get(key):
            print(f"  {label:<23} : {out[key]}")
    if out.get("instrument_error"):
        print()
        print("  ⛔ INSTRUMENT ERROR — the reading below is NOT evidence:")
        print(f"     {out['instrument_error']}")
    print()

    for label in ("page", "child"):
        r = out.get(label) or {}
        print(f"  {label.upper()} realm")
        if r.get("error"):
            print(f"      ERROR: {r['error']}")
        for d in r.get("devices", []):
            print(
                f"      {d['kind']:<12} deviceId={d['deviceId']:<28} "
                f"groupId={d['groupId']}"
            )
        print()

    print("-" * 74)
    if out.get("instrument_error"):
        print(
            "  VERDICT: NOT ESTABLISHED — the instrument transported no leaf, so\n"
            "  a page/child difference here says nothing about the product."
        )
        return 1

    page = (out.get("page") or {}).get("devices")
    child = (out.get("child") or {}).get("devices")
    if page is None or child is None:
        print("  VERDICT: a realm could not be read — see the error above.")
        return 1

    engine_ids = {"ENGINE-DEFAULT-MIC", "ENGINE-DEFAULT-CAM"}
    page_is_engine = {d["deviceId"] for d in page} == engine_ids
    child_is_engine = {d["deviceId"] for d in child} == engine_ids

    if not page_is_engine and child_is_engine:
        print(
            "  VERDICT: MISMATCH PRESENT — the page realm reports the profile's\n"
            "  spoofed device list while the child realm reports the ENGINE's\n"
            "  default, in a run where the registry DID transport its leaves.\n"
            "  That is the page/child divergence PS-320 exists to close."
        )
    elif not page_is_engine and not child_is_engine:
        print(
            "  VERDICT: NO MISMATCH — both realms report the spoofed list.\n"
            "  If this is the reading at your BASE, the premise has been\n"
            "  overtaken and AC2 says STOP AND COMMENT rather than proceed."
        )
    else:
        print(
            "  VERDICT: unexpected — the PAGE realm did not receive the spoof.\n"
            "  Fix the instrument before drawing any conclusion from it."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
