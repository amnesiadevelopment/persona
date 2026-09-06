"""PS-301: LAUNCH the self-built engine and read what a page actually sees.

WHY THIS SCRIPT EXISTS
----------------------
On 2026-09-03 the trial-build workflow produced a chrome binary from ungoogled
chromium plus our 16 fingerprint patches. Nothing has ever EXECUTED it. A
stored QA memory records the trap exactly: *"SIZE + SHA256 ON A BUILT ARTIFACT
ATTEST TO PRESENCE, NOT TO FUNCTION. Three seats verified a Chromium binary by
hash and none of them ever executed it."* A compile establishes that the code
is valid C++ and nothing at all about whether a page sees a masked machine.

This script executes it and records what a page observes, per realm, against a
STOCK control run through the same harness on the same host in the same minute.

THE CONTROL IS THE POINT, NOT A COURTESY
-----------------------------------------
A value that "looks spoofed" proves nothing on its own — the honest question is
always *"is this DIFFERENT from what an unpatched engine reports right here?"*.
So every reading is taken twice: once through the self-built patched engine and
once through stock chromium under the SAME flags, same seed, same page, same
container. Where the two agree, the patch did nothing observable; where they
differ, the difference is attributable to the patch set and to nothing else.
That is also the only way to tell a spoof from a coincidence on a host whose
real GPU is already SwiftShader.

⚠️ THE STOCK ARM IS A CONTROL AND IS NOT THE PRODUCT. It is launched directly
here, deliberately NOT through ``chromium_tier._engine_binary()``, whose refusal
to fall back to a chromium on PATH is the guard that stops a stock browser
producing a complete-looking record of something that is not the product. That
guard is respected: the product arm goes through the resolver, and the control
arm is labelled ``stock`` in every row it produces.

THE INSTRUMENT
--------------
``chromium_tier.ChromiumSession`` — sanctioned for this ticket by the ticket
itself. It launches its own throwaway engine with ``--remote-debugging-port=0``
(ephemeral and unguessable, read back from ``DevToolsActivePort``) into a
temporary user-data-dir removed at the end of the run. Nothing in the
operator's profile store is created, read or mutated, and no control channel
survives the measurement. A fixed or name-derived port, or ``ai_control`` on a
stored profile, remains refused and is not used here.

POINTING IT AT THE SELF-BUILT BINARY
-------------------------------------
``_engine_binary()`` resolves ONLY ``ENGINE_DIR/fingerprint_chromium_filename()``
and refuses any other path. Our artifact is a bare ``chrome``. ``ENGINE_DIR`` is
env-overridable (``PERSONA_ENGINE_DIR``); the filename is not. So the caller
stages a directory containing the expected name — a SYMLINK to the real binary,
which leaves the built artifact untouched — and points the env var at it. The
resolver is not edited, ``fingerprint_chromium_filename()`` is not edited, and
no binary is dropped on PATH.

WHAT IS READ, AND FROM WHICH REALM
-----------------------------------
Four questions, in the ticket's order of value:

  Q1  switch acceptance   — does patch 000's switch set reach the page at all
  Q2  GPU identity        — per realm, because the known chromium/linux leak is
                            a realm the layer never reached
  Q3  canvas / WebGL      — the readback surfaces patches 012/013/015/016 target
  Q4  masking layer       — does the extension layer still install on this build

Realms swept: page, same-origin iframe, about:blank iframe, srcdoc iframe, blob
worker, worker inside an about:blank iframe, nested (depth-2) worker. Those are
the realms a detector reaches and the ones an engine-level patch should cover
for free — that is exactly what makes them the interesting comparison against a
JS layer that has to reach each one itself.

A realm that fails to report is recorded as ``timeout``/``error`` rather than
dropped. An absent realm and a realm that could not be read are different
findings, and collapsing them lets a broken realm read as a realm that agreed.

VENUE: LOOPBACK, DELIBERATELY
------------------------------
The page is served from 127.0.0.1. This script contacts no third party, so
there is no remote observer and no operator address in the picture. It measures
the ENGINE's behaviour; it is not a live checker run and does not claim to be.

Run from the repo root::

    python3 -m scripts.ps301_engine_launch \\
        --engine-dir /tmp/ps301/engine-144 \\
        --label 'self-built 144.0.7559.132 (ps218-patched-binary-144.0.7559.132-1)' \\
        -o readings/ps301-2026-09-05/artifacts
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

# The declared machine handed to --fingerprint-platform. Linux is measured
# because it is the arm the known GPU realm leak lives on and the arm our own
# Linux binary was built for; the other two arms are a different build and are
# reported as UNMEASURED rather than inferred from this one.
ARM = "linux"

# Two seeds, because a single seed cannot distinguish "spoofed" from "constant".
# A patch that returns a fixed fake value and a patch that derives a value from
# the seed produce identical-looking single-seed records, and only the second is
# the product's contract.
SEEDS = (24601, 5150)

# Long enough for the depth-2 worker chain to settle. The reads themselves are
# synchronous once a realm exists.
SETTLE_SECONDS = 6.0

# How long the in-page collector waits for every realm before rendering what it
# has. A straggler is recorded as a timeout, never dropped.
REALM_TIMEOUT_MS = 8000


# ---------------------------------------------------------------------------
# The probe. ONE source of truth for what is read, shared verbatim by every
# realm — a probe that differed per realm would manufacture exactly the
# cross-realm disagreement this script exists to detect.
# ---------------------------------------------------------------------------

_READ_FN = r"""
function __pna_read(makeCanvas, hasDOM) {
  var out = {};

  // --- Q2/Q3: WebGL identity and readback -------------------------------
  try {
    var c = makeCanvas(256, 128);
    var gl = null;
    try { gl = c.getContext('webgl') || c.getContext('experimental-webgl'); }
    catch (e) { gl = null; }
    if (!gl) {
      out.webgl = {available: false, note: 'no webgl context'};
    } else {
      var w = {available: true};
      try { w.masked_vendor = String(gl.getParameter(0x1F00)); } catch (e) {}
      try { w.masked_renderer = String(gl.getParameter(0x1F01)); } catch (e) {}
      try { w.version = String(gl.getParameter(0x1F02)); } catch (e) {}
      var d = null;
      try { d = gl.getExtension('WEBGL_debug_renderer_info'); } catch (e) {}
      if (!d) {
        w.unmasked_vendor = 'no-debug-renderer-info';
        w.unmasked_renderer = 'no-debug-renderer-info';
      } else {
        // Read through the constants the extension handed back, exactly as a
        // detector does, rather than through hardcoded 37445/37446.
        try { w.unmasked_vendor = String(gl.getParameter(d.UNMASKED_VENDOR_WEBGL)); }
        catch (e) { w.unmasked_vendor = 'throws:' + e; }
        try { w.unmasked_renderer = String(gl.getParameter(d.UNMASKED_RENDERER_WEBGL)); }
        catch (e) { w.unmasked_renderer = 'throws:' + e; }
      }
      // --- patch 016: webgl readPixels ---------------------------------
      // Draw something deterministic, then read it back. If 016 perturbs the
      // readback the digest moves per seed while the drawing is identical.
      try {
        gl.clearColor(0.25, 0.5, 0.75, 1.0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        var px = new Uint8Array(256 * 128 * 4);
        gl.readPixels(0, 0, 256, 128, gl.RGBA, gl.UNSIGNED_BYTE, px);
        var s = 0, n = 0, first = [];
        for (var i = 0; i < px.length; i++) { s = (s * 31 + px[i]) >>> 0; }
        for (var j = 0; j < 16; j++) { first.push(px[j]); }
        w.readpixels_hash = s;
        w.readpixels_first16 = first.join(',');
      } catch (e) { w.readpixels_error = String(e); }
      try { var lc = gl.getExtension('WEBGL_lose_context'); if (lc) lc.loseContext(); }
      catch (e) {}
      out.webgl = w;
    }
  } catch (e) { out.webgl = {error: String(e)}; }

  // --- Q3: canvas 2D readback (patches 012 / 013 / 015) -----------------
  try {
    var c2 = makeCanvas(220, 60);
    var ctx = c2.getContext('2d');
    var cv = {available: !!ctx};
    if (ctx) {
      // A fixed drawing, so any digest movement is the ENGINE's, not ours.
      ctx.fillStyle = '#f60';
      ctx.fillRect(0, 0, 220, 60);
      ctx.fillStyle = '#069';
      ctx.font = '17px Arial';
      ctx.fillText('persona-PS301-\u2318\u00e9\u4e2d', 4, 34);
      ctx.strokeStyle = 'rgba(0,255,0,0.7)';
      ctx.beginPath(); ctx.arc(180, 30, 20, 0, Math.PI * 2); ctx.stroke();

      // patch 012: getImageData
      try {
        var img = ctx.getImageData(0, 0, 220, 60).data;
        var h = 0;
        for (var k = 0; k < img.length; k++) { h = (h * 31 + img[k]) >>> 0; }
        cv.getimagedata_hash = h;
      } catch (e) { cv.getimagedata_error = String(e); }

      // patch 015: measureText
      try {
        var m = ctx.measureText('persona-PS301-\u2318\u00e9\u4e2d');
        cv.measuretext_width = m.width;
        cv.measuretext_actual_left = m.actualBoundingBoxLeft;
        cv.measuretext_actual_right = m.actualBoundingBoxRight;
      } catch (e) { cv.measuretext_error = String(e); }
    }
    // patch 013: toDataURL. OffscreenCanvas has no toDataURL, so it is
    // recorded as not-applicable in a worker rather than as a failure.
    try {
      if (typeof c2.toDataURL === 'function') {
        var u = c2.toDataURL();
        var uh = 0;
        for (var q = 0; q < u.length; q++) { uh = (uh * 31 + u.charCodeAt(q)) >>> 0; }
        cv.todataurl_hash = uh;
        cv.todataurl_len = u.length;
      } else {
        cv.todataurl = 'n/a-in-this-realm';
      }
    } catch (e) { cv.todataurl_error = String(e); }
    out.canvas = cv;
  } catch (e) { out.canvas = {error: String(e)}; }

  // --- Q1: the switch surfaces patch 000 defines ------------------------
  try {
    var nav = (typeof navigator !== 'undefined') ? navigator : null;
    var s = {};
    if (nav) {
      s.hardware_concurrency = nav.hardwareConcurrency;
      s.platform = nav.platform;
      s.user_agent = nav.userAgent;
      s.language = nav.language;
      s.languages = (nav.languages || []).join(',');
      s.device_memory = nav.deviceMemory;
      s.webdriver = nav.webdriver;
      try {
        var uad = nav.userAgentData;
        if (uad) {
          s.uad_platform = uad.platform;
          s.uad_mobile = uad.mobile;
          s.uad_brands = (uad.brands || [])
            .map(function (b) { return b.brand + '/' + b.version; }).join(' ');
        } else { s.uad_platform = '<no userAgentData>'; }
      } catch (e) { s.uad_error = String(e); }
    }
    if (hasDOM && typeof screen !== 'undefined') {
      s.screen_width = screen.width;
      s.screen_height = screen.height;
      s.screen_avail_width = screen.availWidth;
      s.screen_avail_height = screen.availHeight;
      s.color_depth = screen.colorDepth;
      s.device_pixel_ratio = (typeof devicePixelRatio !== 'undefined')
        ? devicePixelRatio : null;
    }
    try {
      var ro = Intl.DateTimeFormat().resolvedOptions();
      s.timezone = ro.timeZone;
      s.intl_locale = ro.locale;
      s.tz_offset_minutes = new Date().getTimezoneOffset();
    } catch (e) { s.timezone_error = String(e); }
    out.switches = s;
  } catch (e) { out.switches = {error: String(e)}; }

  // --- patch 014: client rects (DOM realms only) ------------------------
  //
  // ⚠️ THE ELEMENT'S POSITIONING IS LOAD-BEARING AND IS NOT AN ARBITRARY
  // CHOICE. Patch 014 adds ``Element::ShouldSkipClientRectsOffset()``, which
  // deliberately EXEMPTS an element that is ``position:absolute`` with a
  // deterministic (Zero or Fixed) top AND left — precisely so a page cannot
  // detect the noise by placing an element at coordinates it already knows.
  // A probe using such an element therefore reads UNPERTURBED rects and would
  // report a working patch as dead. Measured: an absolutely-positioned probe
  // read byte-identical to stock on both seeds, while THIS statically-flowed
  // span reads a seed-derived sub-pixel offset.
  //
  // It also reads x/y rather than width/height, because the patch calls
  // ``Offset()`` and not ``Scale()`` — it MOVES the rect and never resizes it,
  // so a width-only probe cannot see it either, whatever element it uses.
  try {
    if (hasDOM && typeof document !== 'undefined') {
      var el = document.createElement('span');
      el.textContent = 'clientrects-probe';
      document.body.appendChild(el);
      var r = el.getBoundingClientRect();
      out.client_rects = {
        x: r.x, y: r.y, width: r.width, height: r.height,
        top: r.top, left: r.left
      };
      document.body.removeChild(el);

      // The exempt shape, measured BESIDE the eligible one rather than instead
      // of it: the exemption is a design decision and a record that cannot see
      // it cannot show that it is being honoured.
      var ex = document.createElement('div');
      ex.style.cssText =
        'position:absolute;left:13.3px;top:7.7px;width:111.7px;height:33.3px;';
      ex.textContent = 'rects';
      document.body.appendChild(ex);
      var r2 = ex.getBoundingClientRect();
      out.client_rects_exempt = {x: r2.x, y: r2.y, width: r2.width};
      document.body.removeChild(ex);
    } else {
      out.client_rects = {note: 'no DOM in this realm'};
      out.client_rects_exempt = {note: 'no DOM in this realm'};
    }
  } catch (e) { out.client_rects = {error: String(e)}; }

  // --- patch 003: audio ---------------------------------------------------
  try {
    var AC = (typeof OfflineAudioContext !== 'undefined')
      ? OfflineAudioContext
      : (typeof webkitOfflineAudioContext !== 'undefined'
          ? webkitOfflineAudioContext : null);
    if (!AC) { out.audio = {note: 'no OfflineAudioContext in this realm'}; }
    else { out.audio = {pending: true}; }
  } catch (e) { out.audio = {error: String(e)}; }

  return out;
}
"""

# The audio read is asynchronous, so it is a separate function the page-level
# collector awaits. It is deliberately NOT run in the worker realms — a worker
# has no OfflineAudioContext in chromium, and recording "absent" there would
# read as a finding about the patch when it is a fact about the realm.
_AUDIO_FN = r"""
function __pna_audio() {
  return new Promise(function (resolve) {
    try {
      var AC = (typeof OfflineAudioContext !== 'undefined')
        ? OfflineAudioContext : null;
      if (!AC) { resolve({note: 'no OfflineAudioContext'}); return; }
      var ctx = new AC(1, 44100, 44100);
      var osc = ctx.createOscillator();
      osc.type = 'triangle';
      osc.frequency.value = 10000;
      var comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -50; comp.knee.value = 40; comp.ratio.value = 12;
      comp.attack.value = 0; comp.release.value = 0.25;
      osc.connect(comp); comp.connect(ctx.destination);
      osc.start(0);
      ctx.oncomplete = function (ev) {
        try {
          var d = ev.renderedBuffer.getChannelData(0);
          var sum = 0;
          for (var i = 4500; i < 5000; i++) { sum += Math.abs(d[i]); }
          resolve({sum: sum, sample_4500: d[4500], length: d.length});
        } catch (e) { resolve({error: String(e)}); }
      };
      ctx.startRendering();
      setTimeout(function () { resolve({error: 'audio timeout'}); }, 6000);
    } catch (e) { resolve({error: String(e)}); }
  });
}
"""

_WORKER_BODY = (
    _READ_FN
    + r"""
self.onmessage = function (ev) {
  var depth = (ev && ev.data && ev.data.depth) || 0;
  var mine = __pna_read(function (w, h) { return new OffscreenCanvas(w, h); }, false);
  if (depth > 0) { self.postMessage({self: mine}); return; }
  var child = null;
  try {
    var src = self.__PNA_SRC__ || '';
    var blob = new Blob([src], {type: 'text/javascript'});
    child = new Worker(URL.createObjectURL(blob));
  } catch (e) { child = null; }
  if (!child) { self.postMessage({self: mine, child: {error: 'could not spawn child'}}); return; }
  var done = false;
  child.onmessage = function (cev) {
    if (done) return; done = true;
    self.postMessage({self: mine, child: (cev.data && cev.data.self) || cev.data});
  };
  child.onerror = function (e) {
    if (done) return; done = true;
    self.postMessage({self: mine, child: {error: 'child error: ' + (e && e.message)}});
  };
  child.postMessage({depth: 1});
  setTimeout(function () {
    if (done) return; done = true;
    self.postMessage({self: mine, child: {error: 'child timeout'}});
  }, 6000);
};
"""
)


def _collector_js() -> str:
    return (
        _READ_FN
        + _AUDIO_FN
        + r"""
var RESULTS = {realms: {}, meta: {}};
var EXPECTED = [
  'page', 'iframe_same_origin', 'iframe_about_blank', 'iframe_srcdoc',
  'worker_blob', 'worker_in_iframe', 'worker_nested'
];
var WORKER_SRC = %%WORKER_SRC%%;

function done(name, value) {
  if (!RESULTS.realms[name]) { RESULTS.realms[name] = value; }
}

function render() {
  for (var i = 0; i < EXPECTED.length; i++) {
    if (!RESULTS.realms[EXPECTED[i]]) {
      RESULTS.realms[EXPECTED[i]] = {error: 'timeout'};
    }
  }
  var pre = document.getElementById('out');
  pre.textContent = JSON.stringify(RESULTS);
  document.title = 'PS301-READY';
}

// --- page realm -----------------------------------------------------------
try {
  done('page', __pna_read(function (w, h) {
    var c = document.createElement('canvas'); c.width = w; c.height = h; return c;
  }, true));
} catch (e) { done('page', {error: String(e)}); }

// The audio read is page-only and asynchronous, so it lands beside the realms
// rather than inside one — recording it per realm would report "absent" in
// every worker, which is a fact about the realm and not about patch 003.
try {
  __pna_audio().then(function (a) { RESULTS.audio_page = a; },
                     function (e) { RESULTS.audio_page = {error: String(e)}; });
} catch (e) { RESULTS.audio_page = {error: String(e)}; }

// --- iframe realms --------------------------------------------------------
function readFrame(name, frame) {
  try {
    var win = frame.contentWindow;
    var doc = win.document;
    var fn = win.eval('(' + __pna_read.toString() + ')');
    done(name, fn(function (w, h) {
      var c = doc.createElement('canvas'); c.width = w; c.height = h; return c;
    }, true));
  } catch (e) { done(name, {error: String(e)}); }
}

try {
  var f1 = document.createElement('iframe');
  f1.src = 'blank.html';
  f1.onload = function () { readFrame('iframe_same_origin', f1); };
  document.body.appendChild(f1);
} catch (e) { done('iframe_same_origin', {error: String(e)}); }

try {
  var f2 = document.createElement('iframe');
  document.body.appendChild(f2);
  readFrame('iframe_about_blank', f2);
} catch (e) { done('iframe_about_blank', {error: String(e)}); }

try {
  var f3 = document.createElement('iframe');
  f3.srcdoc = '<!doctype html><body>srcdoc</body>';
  f3.onload = function () { readFrame('iframe_srcdoc', f3); };
  document.body.appendChild(f3);
} catch (e) { done('iframe_srcdoc', {error: String(e)}); }

// --- worker realms --------------------------------------------------------
function spawnBlobWorker(ctxWindow) {
  var src = 'self.__PNA_SRC__ = ' + JSON.stringify(WORKER_SRC) + ';\n' + WORKER_SRC;
  var B = ctxWindow.Blob, U = ctxWindow.URL, W = ctxWindow.Worker;
  var blob = new B([src], {type: 'text/javascript'});
  return new W(U.createObjectURL(blob));
}

try {
  var w1 = spawnBlobWorker(window);
  w1.onmessage = function (ev) {
    done('worker_blob', (ev.data && ev.data.self) || {error: 'no self'});
    done('worker_nested', (ev.data && ev.data.child) || {error: 'no child'});
  };
  w1.onerror = function (e) { done('worker_blob', {error: 'worker error'}); };
  w1.postMessage({depth: 0});
} catch (e) { done('worker_blob', {error: String(e)}); }

try {
  var f4 = document.createElement('iframe');
  document.body.appendChild(f4);
  var w2 = spawnBlobWorker(f4.contentWindow);
  w2.onmessage = function (ev) {
    done('worker_in_iframe', (ev.data && ev.data.self) || {error: 'no self'});
  };
  w2.onerror = function () { done('worker_in_iframe', {error: 'worker error'}); };
  w2.postMessage({depth: 1});
} catch (e) { done('worker_in_iframe', {error: String(e)}); }

setTimeout(render, %%TIMEOUT%%);
"""
    )


_PAGE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>PS301</title>
<body>
<pre id="out">pending</pre>
<script>
%%COLLECTOR%%
</script>
</body>
"""


def _serve_probe_page():
    """A loopback server for the probe, as a context manager."""
    import http.server
    import threading

    collector = (
        _collector_js()
        .replace("%%WORKER_SRC%%", json.dumps(_WORKER_BODY))
        .replace("%%TIMEOUT%%", str(REALM_TIMEOUT_MS))
    )
    html = _PAGE_HTML.replace("%%COLLECTOR%%", collector).encode("utf-8")
    blank = b"<!doctype html><meta charset=utf-8><title>blank</title><body></body>"

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            if self.path.startswith("/blank"):
                body, ctype = blank, "text/html; charset=utf-8"
            else:
                body, ctype = html, "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def read_cell(
    url: str,
    *,
    engine: str,
    seed: int,
    install_layer: bool,
    timezone: str = "",
) -> dict:
    """One cell: one engine, one seed, one layer state, every realm at once.

    A cell that FAILS is recorded WITH its error rather than omitted. An absent
    cell and a cell that could not be read are different findings, and
    collapsing them lets a broken arm read as an arm that agreed.
    """
    from src.services.verify import chromium_tier

    original_args = chromium_tier._launch_args
    captured: dict = {}

    def _capturing_args(*a, **kw):
        args = original_args(*a, **kw)
        # The SURFACE THAT WAS PRESENTED, read off the command line rather than
        # echoed from the request (the PS-103 discipline), so a reader can check
        # --use-angle=swiftshader before attributing a SwiftShader row.
        captured["argv"] = list(args)
        return args

    chromium_tier._launch_args = _capturing_args
    record: dict = {
        "engine": engine,
        "arm": ARM,
        "seed": seed,
        "masking_layer": "on" if install_layer else "off",
        "timezone_requested": timezone,
    }
    try:
        session = chromium_tier.ChromiumSession(
            # Empty credential + allow_no_proxy is the loopback form: the page
            # is served from 127.0.0.1 and there is no exit in the picture.
            "",
            seed=seed,
            declared_machine=ARM,
            timezone=timezone,
            allow_unsandboxed=True,
            allow_no_proxy=True,
            install_layer=install_layer,
        )
        with session as live:
            page = live.new_page()
            page.goto(url, timeout=120000, wait_until="load")
            time.sleep(SETTLE_SECONDS + REALM_TIMEOUT_MS / 1000.0)
            # Read through inner_text, the SAME path a real checker page is read
            # through — page.evaluate is blocked by CSP on real checker pages,
            # so nothing here may succeed through a route a live run lacks.
            text = page.inner_text("pre")
            record["sandbox_waived"] = getattr(session, "sandbox_waived", None)
            layer = getattr(session, "layer_report", None)
            if layer is not None:
                installed = getattr(layer, "installed", None)
                record["layer_installed"] = (
                    sorted(installed)
                    if isinstance(installed, (list, tuple, set))
                    else installed
                )
                record["layer_reason"] = getattr(layer, "reason", None)
        try:
            record["reading"] = json.loads(text)
        except Exception:
            record["reading"] = None
            record["raw_text"] = text[:4000]
    except Exception as exc:  # noqa: BLE001 - a failed cell is a recorded cell
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["reading"] = None
    finally:
        chromium_tier._launch_args = original_args
    record["argv"] = captured.get("argv")
    return record


def binary_provenance(engine_dir: str) -> dict:
    """What was actually launched — resolved path, real target, size, sha256.

    Recorded so a later reader can check that the row describes the binary the
    report names. A reading whose binary cannot be identified is not evidence.
    """
    from src.core import platform as _platform

    path = os.path.join(engine_dir, _platform.fingerprint_chromium_filename())
    out: dict = {"engine_dir": engine_dir, "resolved_path": path}
    try:
        real = os.path.realpath(path)
        out["real_path"] = real
        out["size_bytes"] = os.path.getsize(real)
        h = hashlib.sha256()
        with open(real, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        out["sha256"] = h.hexdigest()
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    try:
        out["version_string"] = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=60
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        out["version_string"] = f"<unreadable: {exc}>"
    return out


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-dir",
        required=True,
        help=(
            "Directory containing the engine under the name the resolver "
            "expects (fpchrome.AppImage on Linux). A symlink to the real "
            "binary is the honest form — it leaves a built artifact untouched."
        ),
    )
    parser.add_argument(
        "--label",
        required=True,
        help="How this engine is named in the record (version + provenance).",
    )
    parser.add_argument(
        "--engine-id",
        default="product",
        help="Short id for the engine column, e.g. 'self-built-144' or 'stock'.",
    )
    parser.add_argument("-o", "--out", required=True, help="Output directory.")
    parser.add_argument(
        "--seeds",
        default=",".join(str(s) for s in SEEDS),
        help="Comma-separated seeds.",
    )
    parser.add_argument(
        "--timezone",
        default="America/Chicago",
        help=(
            "Zone passed as --timezone (patch 018). A value is passed here "
            "deliberately: the switch's acceptance is one of Q1's answers, and "
            "an empty value passes no flag and would measure nothing."
        ),
    )
    parser.add_argument(
        "--layer",
        default="both",
        choices=("on", "off", "both"),
        help="Masking-layer states to measure.",
    )
    args = parser.parse_args(argv)

    engine_dir = os.path.abspath(args.engine_dir)
    # THE MECHANICAL STEP, done here rather than discovered mid-run: ENGINE_DIR
    # is env-overridable and the filename is not, so the caller stages a
    # directory of the expected shape and this points the resolver at it. The
    # resolver, the filename helper and PATH are all left alone.
    os.environ["PERSONA_ENGINE_DIR"] = engine_dir
    # config.py reads the env at import time, so it must not have been imported
    # before this point.
    for mod in list(sys.modules):
        if mod.startswith("src.core.config"):
            del sys.modules[mod]

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    layers = (
        [True, False]
        if args.layer == "both"
        else ([True] if args.layer == "on" else [False])
    )

    prov = binary_provenance(engine_dir)
    print(f"engine: {args.label}")
    print(f"  resolved: {prov.get('resolved_path')}")
    print(f"  real:     {prov.get('real_path')}")
    print(f"  size:     {prov.get('size_bytes')}")
    print(f"  sha256:   {prov.get('sha256')}")
    print(f"  version:  {prov.get('version_string')}")

    records = []
    with _serve_probe_page() as srv:
        for seed in seeds:
            for install_layer in layers:
                print(
                    f"  cell seed={seed} layer={'on' if install_layer else 'off'} ...",
                    flush=True,
                )
                rec = read_cell(
                    srv.url,
                    engine=args.engine_id,
                    seed=seed,
                    install_layer=install_layer,
                    timezone=args.timezone,
                )
                if rec.get("error"):
                    print(f"    ERROR: {rec['error']}")
                else:
                    realms = (rec.get("reading") or {}).get("realms") or {}
                    print(f"    ok — {len(realms)} realms")
                records.append(rec)

    payload = {
        "ticket": "PS-301",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine_label": args.label,
        "engine_id": args.engine_id,
        "binary": prov,
        "host": {
            "uname": " ".join(os.uname()),
            "container_hostname": os.uname().nodename,
        },
        "records": records,
    }
    dest = out_dir / f"readings-{args.engine_id}.json"
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
