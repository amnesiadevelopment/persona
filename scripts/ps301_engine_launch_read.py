r"""PS-301: LAUNCH persona's self-built engine and read what a page actually sees.

On 2026-09-03 the project compiled its own Chromium for the first time —
ungoogled plus all 16 fingerprint patches, 0 errors, a chrome binary on disk.
And that was the entire extent of what was known about it. **Nobody had ever
executed that binary.** This script is the execution.

THE TRAP THIS SCRIPT EXISTS TO AVOID
-------------------------------------
A stored QA memory on this project records it exactly: *"SIZE + SHA256 ON A
BUILT ARTIFACT ATTEST TO PRESENCE, NOT TO FUNCTION. Three seats verified a
Chromium binary by hash and none of them ever executed it."* A green compile
establishes that the code is valid C++. It establishes NOTHING about whether
the fingerprint switches are honoured, whether the GPU string is spoofed, or
whether a page sees a masked machine rather than the host's real one.

So every figure this script produces is read out of a LIVE page in a LAUNCHED
engine. Nothing here is inferred from a binary's contents, and a `strings`
count is never reported as a behaviour.

⭐ THE CONTROL IS THE WHOLE DESIGN — AND IT IS VERSION-MATCHED
---------------------------------------------------------------
A reading from our engine alone cannot distinguish *"our patch spoofed this"*
from *"upstream Chromium already did this"* from *"the harness did it"*. Three
different causes, one identical-looking green.

So every cell is read TWICE, in two engines that differ in exactly one way:

  * **patched**  — persona's self-built chrome (our 16 fingerprint patches)
  * **control**  — the SAME ungoogled release, same version tag, same host,
                   same flags, same probe, WITHOUT our patches

Both are `144.0.7559.132` / ungoogled revision `1`. A difference between the
two columns is therefore attributable to OUR PATCHES and to nothing else —
not to a version bump, not to ungoogled's own changes, not to the harness.

That control is what turns "the page reported Win32" into evidence. Upstream
reports Win32 too under `--fingerprint-platform` only if upstream honours that
switch — and the control column is what tells us whether it does.

⚠️ WHICH BINARY — READ THIS BEFORE QUOTING ANY NUMBER
-------------------------------------------------------
This measures **144.0.7559.132**, NOT the 152 engines the project has since
built. That is a deliberate, and disclosed, compromise:

  * The owner's three 152 artifacts live on the owner's own hosts
    (`/home/builder/personium-152-patched-local`, `C:\personium-152-win`,
    `~/personium-152-mac-Chromium.app`). They are NOT reachable from this
    worker container, and no CI artifact carries a patched 152 Linux binary —
    the only `ps218-patched-binary-*` artifact in the repository's entire
    artifact history is the **144** one (run 33748889046).
  * The 144 patched binary IS reachable, is retained until 2026-09-10, and
    the ticket explicitly sanctions measuring it: *"if that rebase is slow,
    measure the 144 binary we ALREADY HAVE first and on its own."*

So this is a 144 reading, tagged 144 everywhere, and it is a BASELINE. It is
not a claim about the 152 engines. Anyone quoting a figure from here must
carry the version with it — the whole point of the exercise is that an
untagged reading is unattributable.

THE RESOURCE PAIRING, STATED BECAUSE IT IS LOAD-BEARING
---------------------------------------------------------
The CI artifact uploads two executables (`chrome`, `chromedriver`) and NOT the
resource sidecars a Chromium needs to boot (`icudtl.dat`, the `.pak` files,
`v8_context_snapshot.bin`, `locales/`). Launched bare it dies before opening a
debug port: `Invalid file descriptor to ICU data received`.

So the run pairs our patched executable with the resources from the **exact
same upstream release tag** (`144.0.7559.132-1`, downloaded from
ungoogled-software/ungoogled-chromium-portablelinux). That pairing is sound
and it is checkable: all 16 patches touch **compiled C++ only** — Blink,
`ui/gfx`, `v8/src/inspector`, `components/ungoogled` — and not one of them
touches a `.pak`, `icudtl.dat` or a snapshot. The resources are version-matched
data our patches never modify.

It is also what makes the control honest: the control engine is that upstream
tree's OWN chrome, sitting on the very same resources.

THE CDP QUESTION IS SETTLED (PS-301, after PS-237)
----------------------------------------------------
This drives `chromium_tier.ChromiumSession`, which is SANCTIONED for this work:
it launches its own throwaway engine with `--remote-debugging-port=0`
(ephemeral and unguessable, read back from `<user-data-dir>/DevToolsActivePort`)
into a temporary user-data-dir that is removed at the end of the run. Nothing
in an operator's profile store is created, read or mutated.

STILL REFUSED, and not done here: a fixed or name-derived port, `ai_control` on
a stored profile, or any control channel persisting past the measurement.

The engine is reached the way the resolver insists on — `PERSONA_ENGINE_DIR`
pointing at a staged tree containing `fpchrome.AppImage`. `_engine_binary()`'s
refusal to fall back to a chromium on PATH is left intact and is NOT defeated:
stock chromium 152 is present at `/usr/bin/chromium` in this container and is
never launched.

WHAT IT READS, AND WHY EACH ANSWERS A TICKET QUESTION
-------------------------------------------------------
Q1  switches honoured  — `navigator.platform`, `userAgent`, `hardwareConcurrency`,
                         `deviceMemory`, UA-CH brands. Patch 000 defines the
                         switches every later patch reads; if these are not
                         honoured nothing else in the set can be.
Q2  GPU spoofed        — the WebGL identity pair from SEVEN realms, because the
                         known chromium/linux leak is a REALM leak: the top
                         document can read spoofed while a worker inside an
                         about:blank iframe reads the host's real SwiftShader.
                         A single-realm read would report that defect as clean.
Q3  canvas + WebGL     — canvas 2D `toDataURL` / `getImageData` digests,
    readback              `measureText` metrics, and `readPixels` digests, each
                         compared across two seeds AND against the control.
Q4  masking layer      — every cell read with persona's shipped extension layer
                         both OFF and ON, so "does our JS layer still work on
                         this engine" is a measured column and not an assumption.

HONEST BOUND
------------
One binary, one host, one container, one arm per cell is NOT a characterisation
of the engine. Every unread vector is recorded as UNMEASURED with its reason,
never inferred from a neighbouring one that passed. This host has no GPU and
both the product and this tier pass `--use-angle=swiftshader` on Linux, so
SwiftShader here is the host's real renderer under our own flags.

Run from the repo root::

    python3 -m scripts.ps301_engine_launch_read \
        --patched-dir /tmp/ps301/engine \
        --control-dir /tmp/ps301/engine-control \
        -o readings/ps301-.../
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import time

# Declared machine for the cells. `windows` is persona's most common declared
# arm and the one the fingerprint switches are most exercised against.
DEFAULT_ARM = "windows"

# Two seeds, because a spoof that is REAL varies with the seed and a spoof that
# is a constant does not. One seed cannot tell those apart.
DEFAULT_SEEDS = (4242, 1337)

# How long to let the realms settle after load. Copied from ps189_realm_gpu,
# which measured it against the same worker-spawn chain.
SETTLE_SECONDS = 2.5

# Per-realm ceiling. A realm that hangs must land as a TIMEOUT row rather than
# taking the cell down: a realm that could not report and a realm that reported
# nothing are different findings.
REALM_TIMEOUT_MS = 8000


# The identity read, as JS that runs INSIDE whichever realm is measured. Kept
# as ONE source so the page realm and the worker realms cannot silently read
# different things — a probe whose realms disagree because the PROBE differs
# per realm would manufacture exactly the finding this script looks for.
_READ_IDENTITY_FN = r"""
function __readIdentity(makeCanvas) {
  var out = {};
  try {
    var c = makeCanvas();
    var gl = null;
    try { gl = c.getContext('webgl') || c.getContext('experimental-webgl'); }
    catch (e) { gl = null; }
    if (!gl) { return {available: false, note: 'no webgl context'}; }
    out.available = true;
    try { out.masked_vendor = String(gl.getParameter(0x1F00)); } catch (e) {}
    try { out.masked_renderer = String(gl.getParameter(0x1F01)); } catch (e) {}
    var d = null;
    try { d = gl.getExtension('WEBGL_debug_renderer_info'); } catch (e) {}
    if (!d) {
      out.unmasked_vendor = 'no-debug-renderer-info';
      out.unmasked_renderer = 'no-debug-renderer-info';
    } else {
      try { out.unmasked_vendor = String(gl.getParameter(d.UNMASKED_VENDOR_WEBGL)); }
      catch (e) { out.unmasked_vendor = 'throws:' + e; }
      try { out.unmasked_renderer = String(gl.getParameter(d.UNMASKED_RENDERER_WEBGL)); }
      catch (e) { out.unmasked_renderer = 'throws:' + e; }
    }
    // Q3: WebGL readPixels — patch 016's target. Digest rather than pixels, so
    // the record stays readable while still discriminating.
    try {
      gl.clearColor(0.25, 0.5, 0.75, 1.0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      var px = new Uint8Array(4 * 64);
      gl.readPixels(0, 0, 8, 8, gl.RGBA, gl.UNSIGNED_BYTE, px);
      var h = 5381;
      for (var i = 0; i < px.length; i++) { h = ((h * 33) ^ px[i]) >>> 0; }
      out.readpixels_digest = String(h);
    } catch (e) { out.readpixels_digest = 'throws:' + e; }
    try {
      var lc = gl.getExtension('WEBGL_lose_context');
      if (lc) lc.loseContext();
    } catch (e) {}
  } catch (e) {
    out.error = String(e);
  }
  return out;
}
"""


# Q1 + Q3 page-realm reads. Q1 is the switch surface; Q3's canvas half needs a
# real 2D canvas, which a worker realm reaches only through OffscreenCanvas.
_READ_SURFACES_FN = r"""
function __readSwitches() {
  var out = {};
  try { out.platform = String(navigator.platform); } catch (e) { out.platform = 'throws'; }
  try { out.userAgent = String(navigator.userAgent); } catch (e) { out.userAgent = 'throws'; }
  try { out.hardwareConcurrency = navigator.hardwareConcurrency; } catch (e) {}
  try { out.deviceMemory = navigator.deviceMemory; } catch (e) {}
  try { out.webdriver = String(navigator.webdriver); } catch (e) {}
  try { out.languages = (navigator.languages || []).join(','); } catch (e) {}
  try {
    out.screen = String(screen.width) + 'x' + String(screen.height);
    out.devicePixelRatio = window.devicePixelRatio;
  } catch (e) {}
  try {
    out.timezone = String(Intl.DateTimeFormat().resolvedOptions().timeZone);
  } catch (e) {}
  try {
    var b = navigator.userAgentData && navigator.userAgentData.brands;
    out.ua_brands = b ? b.map(function (x) { return x.brand + '/' + x.version; }).join(' ') : 'absent';
    out.ua_platform = navigator.userAgentData ? String(navigator.userAgentData.platform) : 'absent';
  } catch (e) { out.ua_brands = 'throws'; }
  return out;
}

function __digest(s) {
  var h = 5381;
  for (var i = 0; i < s.length; i++) { h = ((h * 33) ^ s.charCodeAt(i)) >>> 0; }
  return String(h);
}

function __readCanvas() {
  var out = {};
  try {
    var c = document.createElement('canvas');
    c.width = 220; c.height = 60;
    var x = c.getContext('2d');
    // A fixed scene: same input in both engines, so a digest difference is a
    // difference in the ENGINE and never in what was drawn.
    x.textBaseline = 'top';
    x.font = '14px Arial';
    x.fillStyle = '#f60'; x.fillRect(0, 0, 100, 30);
    x.fillStyle = '#069'; x.fillText('persona PS-301 \u2014 canvas', 2, 15);
    x.fillStyle = 'rgba(102,204,0,0.7)'; x.fillText('persona PS-301 \u2014 canvas', 4, 25);
    // Patch 013 target.
    try { out.todataurl_digest = __digest(c.toDataURL()); }
    catch (e) { out.todataurl_digest = 'throws:' + e; }
    // Patch 012 target.
    try {
      var d = x.getImageData(0, 0, 220, 60).data;
      var h = 5381;
      for (var i = 0; i < d.length; i++) { h = ((h * 33) ^ d[i]) >>> 0; }
      out.getimagedata_digest = String(h);
    } catch (e) { out.getimagedata_digest = 'throws:' + e; }
    // Patch 015 target — measureText metrics, read to 6dp so a sub-pixel
    // perturbation is visible rather than rounded away.
    try {
      var m = x.measureText('persona PS-301 \u2014 canvas');
      out.measuretext = [
        m.width, m.actualBoundingBoxLeft, m.actualBoundingBoxRight,
        m.actualBoundingBoxAscent, m.actualBoundingBoxDescent
      ].map(function (v) { return (typeof v === 'number') ? v.toFixed(6) : String(v); }).join(',');
    } catch (e) { out.measuretext = 'throws:' + e; }
  } catch (e) { out.error = String(e); }
  return out;
}

function __readRects() {
  // Patch 014 target — getClientRects perturbation.
  var out = {};
  try {
    var el = document.getElementById('rects');
    var r = el.getBoundingClientRect();
    out.bounding = [r.x, r.y, r.width, r.height]
      .map(function (v) { return v.toFixed(6); }).join(',');
    var cr = el.getClientRects()[0];
    out.client = cr ? [cr.x, cr.y, cr.width, cr.height]
      .map(function (v) { return v.toFixed(6); }).join(',') : 'none';
  } catch (e) { out.error = String(e); }
  return out;
}

function __readAudio(done) {
  // Patch 003 target — offline audio context digest.
  try {
    var ctx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 4410, 44100);
    var osc = ctx.createOscillator();
    osc.type = 'triangle'; osc.frequency.value = 10000;
    var comp = ctx.createDynamicsCompressor();
    osc.connect(comp); comp.connect(ctx.destination);
    osc.start(0); ctx.startRendering();
    ctx.oncomplete = function (ev) {
      try {
        var d = ev.renderedBuffer.getChannelData(0);
        var sum = 0;
        for (var i = 0; i < d.length; i++) { sum += Math.abs(d[i]); }
        done(sum.toFixed(8));
      } catch (e) { done('throws:' + e); }
    };
    setTimeout(function () { done('timeout'); }, 6000);
  } catch (e) { done('throws:' + e); }
}
"""


_WORKER_BODY = (
    _READ_IDENTITY_FN
    + r"""
self.onmessage = function (ev) {
  var depth = (ev && ev.data && ev.data.depth) || 0;
  var mine = __readIdentity(function () { return new OffscreenCanvas(64, 64); });
  if (depth > 0) { self.postMessage({self: mine}); return; }
  var child = null;
  try {
    var src = self.__PNA_SRC__;
    var url = URL.createObjectURL(new Blob([src], {type: 'text/javascript'}));
    child = new Worker(url);
    var done = false;
    child.onmessage = function (m) {
      if (done) return; done = true;
      self.postMessage({self: mine, nested: (m.data && m.data.self) || null});
    };
    child.onerror = function (e) {
      if (done) return; done = true;
      self.postMessage({self: mine, nested: {error: 'child worker error'}});
    };
    child.postMessage({depth: 1});
    setTimeout(function () {
      if (done) return; done = true;
      self.postMessage({self: mine, nested: {error: 'nested worker timeout'}});
    }, 5000);
  } catch (e) {
    self.postMessage({self: mine, nested: {error: 'spawn failed: ' + e}});
  }
};
"""
)


def _collector_js() -> str:
    """The page-side collector: fan out to every realm, then render as text.

    Read back through ``inner_text`` — the SAME path a real checker page is
    read through — rather than ``page.evaluate``. A probe that answered through
    a route real checker pages block (CSP) would prove the harness works and
    say nothing about the product.
    """
    return (
        _READ_IDENTITY_FN
        + _READ_SURFACES_FN
        + r"""
var RESULTS = {};
var WORKER_SRC = """
        + json.dumps(_WORKER_BODY)
        + r""";

function finish() {
  document.getElementById('out').textContent = JSON.stringify(RESULTS);
  document.title = 'PS301-DONE';
}

function blobWorker() {
  var src = 'self.__PNA_SRC__ = ' + JSON.stringify(WORKER_SRC) + ';\n' + WORKER_SRC;
  return new Worker(URL.createObjectURL(new Blob([src], {type: 'text/javascript'})));
}

function readWorkerRealms(cb) {
  var w;
  var done = false;
  function land(v) {
    if (done) return; done = true;
    RESULTS.worker = (v && v.self) || {error: 'no self'};
    RESULTS.worker_nested = (v && v.nested) || {error: 'no nested'};
    try { if (w) w.terminate(); } catch (e) {}
    cb();
  }
  try {
    w = blobWorker();
    w.onmessage = function (m) { land(m.data); };
    w.onerror = function () { land({self: {error: 'worker error'}}); };
    w.postMessage({depth: 0});
    setTimeout(function () { land({self: {error: 'worker timeout'}}); }, """
        + str(REALM_TIMEOUT_MS)
        + r""");
  } catch (e) { land({self: {error: 'spawn failed: ' + e}}); }
}

function readIframeRealms(cb) {
  // about:blank and srcdoc child realms, read through the PARENT's reference
  // to the child window — the shape a detector uses.
  try {
    var f = document.createElement('iframe');
    document.body.appendChild(f);
    var w = f.contentWindow;
    RESULTS.iframe_about_blank = w.eval(
      '(' + __readIdentity.toString() + ')(function(){var c=document.createElement("canvas");c.width=64;c.height=64;return c;})'
    );
  } catch (e) { RESULTS.iframe_about_blank = {error: String(e)}; }
  try {
    var f2 = document.createElement('iframe');
    f2.srcdoc = '<!doctype html><body></body>';
    document.body.appendChild(f2);
    f2.onload = function () {
      try {
        var w2 = f2.contentWindow;
        RESULTS.iframe_srcdoc = w2.eval(
          '(' + __readIdentity.toString() + ')(function(){var c=document.createElement("canvas");c.width=64;c.height=64;return c;})'
        );
      } catch (e) { RESULTS.iframe_srcdoc = {error: String(e)}; }
      cb();
    };
    setTimeout(function () {
      if (!RESULTS.iframe_srcdoc) { RESULTS.iframe_srcdoc = {error: 'srcdoc timeout'}; cb(); }
    }, 4000);
  } catch (e) { RESULTS.iframe_srcdoc = {error: String(e)}; cb(); }
}

function readWorkerInIframe(cb) {
  // THE REALM THE KNOWN LEAK LIVES IN: a Worker created from INSIDE an
  // about:blank child frame. Read explicitly rather than assumed to inherit.
  var done = false;
  function land(v) {
    if (done) return; done = true;
    RESULTS.worker_in_iframe = v;
    cb();
  }
  try {
    var f = document.createElement('iframe');
    document.body.appendChild(f);
    var w = f.contentWindow;
    var src = 'self.__PNA_SRC__ = ' + JSON.stringify(WORKER_SRC) + ';\n' + WORKER_SRC;
    var blob = new w.Blob([src], {type: 'text/javascript'});
    var worker = new w.Worker(w.URL.createObjectURL(blob));
    worker.onmessage = function (m) { land((m.data && m.data.self) || {error: 'no self'}); };
    worker.onerror = function () { land({error: 'worker-in-iframe error'}); };
    worker.postMessage({depth: 1});
    setTimeout(function () { land({error: 'worker-in-iframe timeout'}); }, """
        + str(REALM_TIMEOUT_MS)
        + r""");
  } catch (e) { land({error: 'spawn failed: ' + e}); }
}

// Sequence the realms so one slow arm cannot hide another's result.
RESULTS.switches = __readSwitches();
RESULTS.canvas = __readCanvas();
RESULTS.rects = __readRects();
RESULTS.page = __readIdentity(function () {
  var c = document.createElement('canvas'); c.width = 64; c.height = 64; return c;
});
__readAudio(function (v) {
  RESULTS.audio_digest = v;
  readIframeRealms(function () {
    readWorkerRealms(function () {
      readWorkerInIframe(function () { finish(); });
    });
  });
});
setTimeout(function () { if (document.title !== 'PS301-DONE') finish(); }, 30000);
"""
    )


_PAGE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>PS301</title>
<body>
<div id="rects" style="width:123.45px;height:67.89px;padding:3px;font:13px Arial">rects</div>
<pre id="out">PENDING</pre>
<script>%%COLLECTOR%%</script>
"""


def _serve_probe_page():
    """A loopback server for the probe, as a context manager.

    This script contacts NO third party: the page is served from 127.0.0.1, so
    there is no remote observer and no address to protect. That also means it
    reads the engine's own behaviour rather than a checker's opinion of it.
    """
    import http.server
    import threading

    html = _PAGE_HTML.replace("%%COLLECTOR%%", _collector_js()).encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *a, **kw):
            pass

    class _Server:
        def __enter__(self):
            self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            self._t = threading.Thread(
                target=self._srv.serve_forever,
                kwargs={"poll_interval": 0.1},
                daemon=True,
            )
            self._t.start()
            host, port = self._srv.server_address[:2]
            self.url = f"http://{host}:{port}/"
            return self

        def __exit__(self, *exc):
            try:
                self._srv.shutdown()
            finally:
                self._srv.server_close()
            self._t.join(timeout=5)

    return _Server()


def read_cell(
    url: str,
    *,
    engine: str,
    engine_dir: str,
    seed: int,
    arm: str,
    install_layer: bool,
) -> dict:
    """One cell: one engine, one seed, one layer state, every realm at once.

    A cell that FAILS is recorded WITH its error rather than omitted. An absent
    cell and a cell that could not be read are different findings, and
    collapsing them lets a broken engine read as an engine that agreed.

    ``PERSONA_ENGINE_DIR`` is set for the duration of the cell and the config
    module is reloaded, because ``ENGINE_DIR`` is resolved at import time. That
    is the honest way to point the SHIPPED resolver at a staged tree: the
    resolver's own refusal to fall back to a PATH chromium stays intact.
    """
    import importlib
    import os

    from src.core import config as _config
    from src.services.verify import chromium_tier

    record: dict = {
        "engine": engine,
        "engine_dir": engine_dir,
        "seed": seed,
        "arm": arm,
        "masking_layer": "on" if install_layer else "off",
    }

    prev = os.environ.get("PERSONA_ENGINE_DIR")
    os.environ["PERSONA_ENGINE_DIR"] = engine_dir
    importlib.reload(_config)

    original_args = chromium_tier._launch_args
    captured: dict = {}

    def _capturing_args(*a, **kw):
        args = original_args(*a, **kw)
        # The SURFACE THAT WAS PRESENTED, read off the command line rather than
        # echoed from the request (the PS-103 discipline). This is what lets a
        # reader check --use-angle=swiftshader before attributing a SwiftShader
        # row to the product rather than to the harness (PS-14).
        captured["argv"] = list(args)
        return args

    chromium_tier._launch_args = _capturing_args
    session = None
    try:
        session = chromium_tier.ChromiumSession(
            "",
            seed=seed,
            declared_machine=arm,
            allow_unsandboxed=True,
            allow_no_proxy=True,
            install_layer=install_layer,
        )
        record["binary"] = chromium_tier._engine_binary()
        with session as live:
            page = live.new_page()
            page.goto(url, timeout=90000, wait_until="load")
            time.sleep(SETTLE_SECONDS)
            deadline = time.time() + 45
            text = ""
            while time.time() < deadline:
                text = page.inner_text("#out")
                if text and text != "PENDING":
                    break
                time.sleep(0.5)
            if not text or text == "PENDING":
                record["error"] = "probe never reported (still PENDING at deadline)"
            else:
                record["realms"] = json.loads(text)
        try:
            record["layer_report"] = str(session.layer_report)
        except Exception:  # pragma: no cover - defensive
            pass
    except Exception as exc:  # noqa: BLE001 - the error IS the record
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        chromium_tier._launch_args = original_args
        if prev is None:
            os.environ.pop("PERSONA_ENGINE_DIR", None)
        else:
            os.environ["PERSONA_ENGINE_DIR"] = prev
        importlib.reload(_config)
    record["argv"] = captured.get("argv")
    return record


def _identity(realm: dict) -> str:
    if not isinstance(realm, dict):
        return "?"
    if realm.get("error"):
        return f"ERROR({realm['error']})"
    if realm.get("available") is False:
        return f"unavailable({realm.get('note', '')})"
    return f"{realm.get('unmasked_vendor', '?')} | {realm.get('unmasked_renderer', '?')}"


REALMS = (
    "page",
    "iframe_about_blank",
    "iframe_srcdoc",
    "worker",
    "worker_nested",
    "worker_in_iframe",
)


def summarise(records: "list[dict]") -> str:
    """A patched-vs-control comparison, per question, in plain text.

    Deliberately reports DIFFERENCES rather than verdicts: this ticket measures
    and does not fix, so the summary's job is to say what the two engines did,
    not to rule on whether it is good enough.
    """
    lines: "list[str]" = []

    def cell(engine: str, seed: int, layer: str) -> "dict | None":
        for r in records:
            if (
                r["engine"] == engine
                and r["seed"] == seed
                and r["masking_layer"] == layer
            ):
                return r
        return None

    seeds = sorted({r["seed"] for r in records})
    layers = sorted({r["masking_layer"] for r in records})

    lines.append("PS-301 — persona's self-built engine, LAUNCHED and READ")
    lines.append("=" * 70)
    lines.append("")
    lines.append("Engine under test : patched  (persona's 16 fingerprint patches)")
    lines.append("Control           : control  (same ungoogled release, unpatched)")
    lines.append("Both              : Chromium 144.0.7559.132 / ungoogled rev 1")
    lines.append("Venue             : loopback 127.0.0.1 — no third party contacted")
    lines.append("")

    errs = [r for r in records if r.get("error")]
    if errs:
        lines.append("CELLS THAT COULD NOT BE READ (recorded, not omitted):")
        for r in errs:
            lines.append(
                f"  {r['engine']}/seed{r['seed']}/layer-{r['masking_layer']}: {r['error']}"
            )
        lines.append("")

    # -- Q1 -------------------------------------------------------------
    lines.append("Q1 — ARE OUR FINGERPRINT SWITCHES HONOURED?")
    lines.append("-" * 70)
    for seed in seeds:
        for layer in layers:
            p, c = cell("patched", seed, layer), cell("control", seed, layer)
            if not p or not c or "realms" not in p or "realms" not in c:
                continue
            ps, cs = p["realms"].get("switches", {}), c["realms"].get("switches", {})
            lines.append(f"  seed {seed} / layer {layer}")
            for k in (
                "platform",
                "userAgent",
                "hardwareConcurrency",
                "deviceMemory",
                "ua_platform",
                "ua_brands",
                "webdriver",
                "screen",
                "timezone",
            ):
                pv, cv = ps.get(k), cs.get(k)
                mark = "DIFFERS" if pv != cv else "same   "
                lines.append(f"    {mark}  {k}")
                lines.append(f"        patched: {pv}")
                lines.append(f"        control: {cv}")
            lines.append("")

    # -- Q2 -------------------------------------------------------------
    lines.append("Q2 — IS THE GPU SPOOFED, AND IN WHICH REALMS?")
    lines.append("-" * 70)
    lines.append("  A realm-by-realm read. The known chromium/linux leak is a REALM")
    lines.append("  leak, so a single-realm read would report it as clean.")
    lines.append("")
    for seed in seeds:
        for layer in layers:
            p, c = cell("patched", seed, layer), cell("control", seed, layer)
            if not p or not c or "realms" not in p or "realms" not in c:
                continue
            lines.append(f"  seed {seed} / layer {layer}")
            for realm in REALMS:
                pv = _identity(p["realms"].get(realm, {}))
                cv = _identity(c["realms"].get(realm, {}))
                mark = "DIFFERS" if pv != cv else "same   "
                lines.append(f"    {mark}  {realm}")
                lines.append(f"        patched: {pv}")
                lines.append(f"        control: {cv}")
            lines.append("")

    # -- Q3 -------------------------------------------------------------
    lines.append("Q3 — CANVAS AND WEBGL READBACK")
    lines.append("-" * 70)
    for layer in layers:
        lines.append(f"  layer {layer}")
        for key, label in (
            ("todataurl_digest", "canvas toDataURL   (patch 013)"),
            ("getimagedata_digest", "canvas getImageData (patch 012)"),
            ("measuretext", "canvas measureText  (patch 015)"),
        ):
            for engine in ("patched", "control"):
                vals = {}
                for seed in seeds:
                    r = cell(engine, seed, layer)
                    if r and "realms" in r:
                        vals[seed] = r["realms"].get("canvas", {}).get(key)
                varies = len(set(vals.values())) > 1
                lines.append(
                    f"    {engine:8s} {label}: "
                    f"{'VARIES with seed' if varies else 'constant across seeds'}"
                )
                for s, v in vals.items():
                    lines.append(f"        seed {s}: {v}")
        # readPixels lives on the identity read, per realm.
        for engine in ("patched", "control"):
            vals = {}
            for seed in seeds:
                r = cell(engine, seed, layer)
                if r and "realms" in r:
                    vals[seed] = (
                        r["realms"].get("page", {}) or {}
                    ).get("readpixels_digest")
            varies = len(set(vals.values())) > 1
            lines.append(
                f"    {engine:8s} webgl readPixels    (patch 016): "
                f"{'VARIES with seed' if varies else 'constant across seeds'}"
            )
            for s, v in vals.items():
                lines.append(f"        seed {s}: {v}")
        # Rects (patch 014) and audio (patch 003) ride the same axis.
        for engine in ("patched", "control"):
            vals = {}
            for seed in seeds:
                r = cell(engine, seed, layer)
                if r and "realms" in r:
                    vals[seed] = r["realms"].get("rects", {}).get("bounding")
            varies = len(set(vals.values())) > 1
            lines.append(
                f"    {engine:8s} client rects        (patch 014): "
                f"{'VARIES with seed' if varies else 'constant across seeds'}"
            )
            for s, v in vals.items():
                lines.append(f"        seed {s}: {v}")
        for engine in ("patched", "control"):
            vals = {}
            for seed in seeds:
                r = cell(engine, seed, layer)
                if r and "realms" in r:
                    vals[seed] = r["realms"].get("audio_digest")
            varies = len(set(vals.values())) > 1
            lines.append(
                f"    {engine:8s} offline audio       (patch 003): "
                f"{'VARIES with seed' if varies else 'constant across seeds'}"
            )
            for s, v in vals.items():
                lines.append(f"        seed {s}: {v}")
        lines.append("")

    # -- Q4 -------------------------------------------------------------
    lines.append("Q4 — DOES persona's MASKING LAYER STILL WORK ON THIS ENGINE?")
    lines.append("-" * 70)
    if "on" not in layers or "off" not in layers:
        lines.append("  UNMEASURED — the run did not read both layer states.")
    else:
        for seed in seeds:
            on, off = cell("patched", seed, "on"), cell("patched", seed, "off")
            if not on or not off or "realms" not in on or "realms" not in off:
                lines.append(f"  seed {seed}: UNMEASURED (a cell did not report)")
                continue
            lines.append(f"  seed {seed} — patched engine, layer OFF vs ON")
            lines.append(f"    layer report: {on.get('layer_report')}")
            for realm in REALMS:
                ov = _identity(off["realms"].get(realm, {}))
                nv = _identity(on["realms"].get(realm, {}))
                mark = "CHANGED" if ov != nv else "same   "
                lines.append(f"    {mark}  {realm}")
                lines.append(f"        layer off: {ov}")
                lines.append(f"        layer on : {nv}")
            lines.append("")

    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", required=True, help="output directory")
    parser.add_argument(
        "--patched-dir",
        required=True,
        help="staged engine dir containing fpchrome.AppImage (persona's build)",
    )
    parser.add_argument(
        "--control-dir",
        required=True,
        help="staged engine dir containing the UNPATCHED upstream chrome",
    )
    parser.add_argument("--arm", default=DEFAULT_ARM, help="declared machine")
    parser.add_argument(
        "--seeds", default=",".join(str(s) for s in DEFAULT_SEEDS),
        help="comma-separated fingerprint seeds",
    )
    parser.add_argument(
        "--layer", default="both", choices=("on", "off", "both"),
        help="masking layer state(s) to read",
    )
    parser.add_argument(
        "--engine-version", default="144.0.7559.132",
        help="the version tag recorded beside every reading",
    )
    args = parser.parse_args(argv)

    seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())
    states = (
        (True, False)
        if args.layer == "both"
        else ((True,) if args.layer == "on" else (False,))
    )
    engines = (("patched", args.patched_dir), ("control", args.control_dir))

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: "list[dict]" = []
    with _serve_probe_page() as server:
        for engine, engine_dir in engines:
            for seed in seeds:
                for install_layer in states:
                    state = "on" if install_layer else "off"
                    print(
                        f"[ps301] reading {engine}/seed{seed}/layer-{state} ...",
                        flush=True,
                    )
                    rec = read_cell(
                        server.url,
                        engine=engine,
                        engine_dir=engine_dir,
                        seed=seed,
                        arm=args.arm,
                        install_layer=install_layer,
                    )
                    rec["engine_version"] = args.engine_version
                    records.append(rec)
                    print(
                        "[ps301]   -> "
                        + (
                            "ERROR: " + rec["error"]
                            if rec.get("error")
                            else "read"
                        ),
                        flush=True,
                    )

    doc = {
        "schema_version": 1,
        "ticket": "PS-301",
        "observed_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "engine_version": args.engine_version,
        "venue": "loopback (127.0.0.1) — no third party contacted, no exit in the picture",
        "records": records,
    }
    (out_dir / "engine-launch.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8"
    )
    summary = summarise(records)
    (out_dir / "engine-launch-summary.txt").write_text(summary + "\n", encoding="utf-8")
    print()
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
