"""PS-189: WHICH REALM does each checker read the WebGL identity from?

PS-186's sweep left two symptoms on the arms PS-161 never touched:

  * ``chromium / linux``  — creepjs reads the container's REAL SwiftShader
    while pixelscan.net reads a plausible Mesa card from ``LINUX_GPUS``.
  * ``chromium / macos``  — creepjs reads ``Apple M2`` / ``Apple M4`` while
    pixelscan.net reads ``Apple M1`` / ``Apple M2 Pro``.

The ticket asks whether those are ONE defect or TWO, and requires the answer be
settled by MEASUREMENT rather than by reading the code and reasoning about it.

THE HYPOTHESIS THIS SCRIPT EXISTS TO FALSIFY
---------------------------------------------
Both symptoms are ONE defect: our JS layer authors the identity pair in the
realms it reaches, and FAILS TO REACH the realm creepjs reads. Where the engine
has its own value, the un-reached realm shows the ENGINE's value; where the
engine has none, it shows the HOST's.

That predicts something specific and checkable, which is what makes it a
hypothesis rather than a story:

  * the values creepjs saw are NOT in our pools, and ARE the engine's own
    (``gpu_ext.py:753-761`` records the engine as SwiftShader on linux and
    M2/M4 on macos — measured under PS-161, not asserted here), and
  * a realm sweep with the layer ON will find SOME realm still reporting the
    engine/host value on macos and linux, and NONE on windows.

The windows arm agreeing is NOT evidence our realm coverage works there. On
windows ``ENGINE_AUTHORED_IDENTITY_ARMS`` makes us stand down entirely, so the
engine authors EVERY realm and consistency is free. That is exactly why windows
cannot be used as the control for realm coverage, and why this script reads the
arms separately instead of generalising from the clean one.

WHAT IT READS
-------------
The identity pair from every realm a detector can reach, at one instant, in one
launch, per arm:

  ``page``                  the top document
  ``iframe_same_origin``    a child frame the browser created from a URL
  ``iframe_about_blank``    a child frame the PAGE created at runtime
  ``iframe_srcdoc``         the srcdoc form of the same
  ``worker``                a blob Worker with an OffscreenCanvas
  ``worker_in_iframe``      a Worker created from inside an about:blank frame
  ``worker_nested``         a Worker spawned by a Worker (depth 2)

Those last four are the realms ``worker_wrap`` exists to cover and the ones its
own docstring names as where "creepjs read the real GPU from an OffscreenCanvas
in a worker created inside an about:blank iframe".

THE INSTRUMENT IS CHECKED BEFORE THE PRODUCT (PS-14)
------------------------------------------------------
This container has no GPU and BOTH the product (``process.py:630``) and the
verify tier (``chromium_tier.py:486``) pass ``--use-angle=swiftshader`` on
Linux. So SwiftShader here is the HOST's real renderer under our own flags, not
an artefact of the harness — and the owner's standing ruling (PS-10,
2026-08-22) is that the engine is expected to present a plausible GPU wherever
it runs, GPU-less containers included. The argv actually used is captured into
every record so a reader can check the flags before attributing anything.

THE VENUE IS LOOPBACK, DELIBERATELY
-------------------------------------
This script contacts no third party: the page is served from 127.0.0.1, so
there is no remote observer and no address to protect. It does NOT discharge
PS-189's live half — "verified on a live checker read through the proxied exit"
is a CHECKER run and still requires the exit. This is the mechanism
measurement that says WHAT to fix; the live run is what proves it fixed.

Run from the repo root::

    .venv/bin/python -m scripts.ps189_realm_gpu -o readings/ps189-.../
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import time

# The two arms under investigation plus windows as the CONTRAST. Windows is not
# a control for realm coverage (see the docstring) — it is here because a run
# where windows also split would mean something entirely different from one
# where only macos/linux do, and that distinction is worth one extra cell.
ARMS = ("linux", "macos", "windows")

# Both PS-186 seeds, so this grid is directly comparable to the records that
# raised the ticket rather than starting an incomparable new series.
SEEDS = (24601, 5150)

# Long enough for the worker chain (depth-2 workers, each re-blobbed) to
# settle. The reads themselves are synchronous once a realm exists.
SETTLE_SECONDS = 6.0

# How long the in-page collector waits for every realm to report before giving
# up on the stragglers and rendering what it has. A realm that never answers is
# recorded as 'timeout' rather than dropped — an absent realm and a realm that
# failed to report are different findings.
REALM_TIMEOUT_MS = 8000


# The identity read, as a string of JS that runs INSIDE whichever realm is
# being measured. Kept as one source so the page realm and the worker realms
# cannot silently read different things — a probe whose realms disagree because
# the PROBE differs per realm would manufacture exactly the finding this script
# is looking for.
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
      // Read through the constants the extension handed back, exactly as a
      // detector does, rather than through hardcoded 37445/37446.
      try { out.unmasked_vendor = String(gl.getParameter(d.UNMASKED_VENDOR_WEBGL)); }
      catch (e) { out.unmasked_vendor = 'throws:' + e; }
      try { out.unmasked_renderer = String(gl.getParameter(d.UNMASKED_RENDERER_WEBGL)); }
      catch (e) { out.unmasked_renderer = 'throws:' + e; }
    }
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

# The worker body. Reads through OffscreenCanvas — the only canvas a worker
# has, and the exact path worker_wrap's docstring names as where creepjs caught
# the real GPU. `depth` lets one body serve both the plain worker and the
# nested one: at depth 0 it spawns a child of itself and reports BOTH.
_WORKER_BODY = (
    _READ_IDENTITY_FN
    + r"""
self.onmessage = function (ev) {
  var depth = (ev && ev.data && ev.data.depth) || 0;
  var mine = __readIdentity(function () { return new OffscreenCanvas(64, 64); });
  if (depth > 0) { self.postMessage({self: mine}); return; }
  // Depth 0 also spawns a CHILD worker. A depth-2 realm that silently receives
  // no patch does not throw — it just reports the real GPU — which is why it
  // is read explicitly rather than assumed to inherit.
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
      self.postMessage({self: mine, nested: {error: 'child worker error: ' + (e && e.message)}});
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


# A ServiceWorker body. THE REALM THIS SCRIPT WAS EXTENDED FOR.
#
# ``worker_wrap`` chains ``Worker`` and ``SharedWorker`` (worker_wrap.py:331-332)
# and NOTHING chains ``ServiceWorker`` — a service worker is not constructed by
# the page at all, it is REGISTERED with the browser and started by it, so a
# constructor wrapper can never intercept one. The live creepjs read
# (``scripts/ps189_live_creepjs.py``) printed ``ServiceWorkerGlobalScope``
# directly above the leaked ``gpu:`` row, and it was the only worker-scope label
# on the page. This arm is what turns that from a strong reading of someone
# else's page text into our own measurement.
_SERVICE_WORKER_BODY = (
    _READ_IDENTITY_FN
    + r"""
self.addEventListener('install', function (e) { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('message', function (e) {
  var reading;
  try {
    reading = __readIdentity(function () { return new OffscreenCanvas(64, 64); });
  } catch (err) {
    reading = {error: 'sw read failed: ' + err};
  }
  try {
    if (e.source && e.source.postMessage) { e.source.postMessage(reading); return; }
  } catch (err) {}
  try {
    if (e.ports && e.ports[0]) { e.ports[0].postMessage(reading); return; }
  } catch (err) {}
  self.clients.matchAll().then(function (cs) {
    cs.forEach(function (c) { c.postMessage(reading); });
  });
});
"""
)


# A SharedWorker body. Kept separate from the dedicated Worker body because the
# connection shape differs (onconnect + a MessagePort), not because the read
# differs — the read is the same ``__readIdentity`` both use.
#
# SharedWorker is probed because it is a realm a dedicated Worker does not stand
# for: it is process-shared and its global is constructed differently, so a
# bootstrap that reaches `Worker` does not automatically reach it.
_SHARED_WORKER_BODY = (
    _READ_IDENTITY_FN
    + r"""
self.onconnect = function (e) {
  var port = e.ports[0];
  port.onmessage = function () {
    port.postMessage(__readIdentity(function () { return new OffscreenCanvas(64, 64); }));
  };
  port.start();
};
"""
)


def _collector_js() -> str:
    """The page script that reads every realm and renders one JSON blob.

    Written as a template filled here rather than inline in the HTML so the
    worker body can be embedded as a JSON string literal (it contains quotes
    and newlines that would not survive being pasted into a <script>).
    """
    return (
        _READ_IDENTITY_FN
        + "\nvar WORKER_SRC = "
        + json.dumps(_WORKER_BODY)
        + ";\nvar SHARED_SRC = "
        + json.dumps(_SHARED_WORKER_BODY)
        + ";\nvar SW_SRC = "
        + json.dumps(_SERVICE_WORKER_BODY)
        + ";\n"
        + r"""
var RESULTS = {};
var PENDING = {};

function done(name, value) {
  if (RESULTS[name] !== undefined) return;
  RESULTS[name] = value;
  delete PENDING[name];
  render();
}
function expect(name) { PENDING[name] = true; }

function render() {
  var el = document.getElementById('out');
  if (!el) return;
  el.textContent = JSON.stringify({
    realms: RESULTS,
    still_pending: Object.keys(PENDING)
  }, null, 2);
}

// --- page realm ------------------------------------------------------------
done('page', __readIdentity(function () {
  var c = document.createElement('canvas'); c.width = 64; c.height = 64; return c;
}));

// --- iframe realms ---------------------------------------------------------
// The canvas is created by the CHILD realm's own document, so the WebGL
// context belongs to that realm rather than to ours. Creating it here and
// appending it there would read this realm's patch and prove nothing.
function readIframe(name, setup) {
  expect(name);
  try {
    var f = document.createElement('iframe');
    f.style.display = 'none';
    setup(f);
    f.onload = function () {
      try {
        var w = f.contentWindow;
        var fn = w.eval('(' + __readIdentity.toString() + ')');
        done(name, fn(function () {
          var c = w.document.createElement('canvas');
          c.width = 64; c.height = 64; return c;
        }));
      } catch (e) { done(name, {error: 'iframe read failed: ' + e}); }
    };
    document.body.appendChild(f);
    // about:blank frames can be ready before onload ever fires.
    setTimeout(function () {
      if (RESULTS[name] !== undefined) return;
      try {
        var w = f.contentWindow;
        if (!w || !w.document) return;
        var fn = w.eval('(' + __readIdentity.toString() + ')');
        done(name, fn(function () {
          var c = w.document.createElement('canvas');
          c.width = 64; c.height = 64; return c;
        }));
      } catch (e) { done(name, {error: 'iframe late read failed: ' + e}); }
    }, 600);
  } catch (e) { done(name, {error: 'iframe setup failed: ' + e}); }
}

readIframe('iframe_same_origin', function (f) { f.src = 'blank.html'; });
readIframe('iframe_about_blank', function (f) { f.src = 'about:blank'; });
readIframe('iframe_srcdoc', function (f) { f.srcdoc = '<!doctype html><title>s</title>'; });

// --- worker realms ---------------------------------------------------------
// The body carries its OWN source under __PNA_SRC__ so a worker can spawn a
// clone of itself without the page handing it a second copy.
function workerSource() {
  return 'self.__PNA_SRC__ = ' + JSON.stringify(WORKER_SRC) + ';\n' + WORKER_SRC;
}

function readWorker(name, nestedName, realm) {
  expect(name);
  if (nestedName) expect(nestedName);
  try {
    var W = realm.Worker, U = realm.URL, B = realm.Blob;
    var url = U.createObjectURL(new B([workerSource()], {type: 'text/javascript'}));
    var w = new W(url);
    w.onmessage = function (m) {
      done(name, (m.data && m.data.self) || {error: 'no self reading'});
      if (nestedName) done(nestedName, (m.data && m.data.nested) || {error: 'no nested reading'});
    };
    w.onerror = function (e) {
      done(name, {error: 'worker error: ' + (e && e.message)});
      if (nestedName) done(nestedName, {error: 'parent worker error'});
    };
    w.postMessage({depth: 0});
  } catch (e) {
    done(name, {error: 'worker spawn failed: ' + e});
    if (nestedName) done(nestedName, {error: 'parent spawn failed'});
  }
}

readWorker('worker', 'worker_nested', window);

// --- http(s)-loaded worker -------------------------------------------------
// A DIFFERENT worker_wrap code path from the blob worker above: a blob/data
// worker is re-blobbed with the fragment prepended, while an http(s) worker
// gets an importScripts shim instead. A layer that covers one and not the
// other looks completely clean to a blob-only probe — and a real checker
// (creepjs) loads its worker from a real URL, not from a blob.
expect('worker_http');
try {
  var hw = new Worker('worker.js');
  hw.onmessage = function (m) { done('worker_http', (m.data && m.data.self) || {error: 'no reading'}); };
  hw.onerror = function (e) { done('worker_http', {error: 'http worker error: ' + (e && e.message)}); };
  hw.postMessage({depth: 1});
} catch (e) { done('worker_http', {error: 'http worker spawn failed: ' + e}); }

// --- module worker ---------------------------------------------------------
// The third worker_wrap path (a module blob that dynamic-imports the
// original), read for the same reason: three paths, three chances to miss.
expect('worker_module');
try {
  var mw = new Worker('worker.js', {type: 'module'});
  mw.onmessage = function (m) { done('worker_module', (m.data && m.data.self) || {error: 'no reading'}); };
  mw.onerror = function (e) { done('worker_module', {error: 'module worker error: ' + (e && e.message)}); };
  mw.postMessage({depth: 1});
} catch (e) { done('worker_module', {error: 'module worker spawn failed: ' + e}); }

// --- SharedWorker ----------------------------------------------------------
// A realm a dedicated Worker does NOT stand for: process-shared, and its
// global is constructed differently, so a bootstrap that chains `Worker` does
// not automatically reach it.
expect('shared_worker');
try {
  var swSrc = """ + "\n  " + r"""SHARED_SRC;
  var swUrl = URL.createObjectURL(new Blob([swSrc], {type: 'text/javascript'}));
  var sw = new SharedWorker(swUrl);
  sw.port.onmessage = function (m) { done('shared_worker', m.data || {error: 'no reading'}); };
  sw.onerror = function (e) { done('shared_worker', {error: 'sharedworker error: ' + (e && e.message)}); };
  sw.port.start();
  sw.port.postMessage({});
} catch (e) { done('shared_worker', {error: 'sharedworker spawn failed: ' + e}); }

// --- ServiceWorker ---------------------------------------------------------
// THE REALM THIS PROBE WAS EXTENDED FOR, and the one a constructor wrapper can
// never reach: a service worker is not built by the page with `new`, it is
// REGISTERED with the browser and started by it, so worker_wrap's
// Worker/SharedWorker chaining has nothing to intercept. Read explicitly
// because "we did not look" and "it agreed" must not collapse into each other.
expect('service_worker');
try {
  if (!navigator.serviceWorker) {
    done('service_worker', {available: false, note: 'navigator.serviceWorker absent'});
  } else {
    navigator.serviceWorker.addEventListener('message', function (m) {
      done('service_worker', m.data || {error: 'no reading'});
    });
    navigator.serviceWorker.register('sw.js').then(function (reg) {
      function poke() {
        var target = reg.active || (navigator.serviceWorker && navigator.serviceWorker.controller);
        if (!target) return false;
        try { target.postMessage({}); return true; } catch (e) { return false; }
      }
      if (!poke()) {
        // A freshly registered worker is `installing`; it can only be messaged
        // once it reaches `activated`.
        var tries = 0;
        var iv = setInterval(function () {
          tries++;
          if (poke() || tries > 40) clearInterval(iv);
        }, 200);
      }
    }).catch(function (e) {
      done('service_worker', {error: 'register failed: ' + e});
    });
  }
} catch (e) { done('service_worker', {error: 'serviceworker setup failed: ' + e}); }

// --- IS A FIX REACHABLE FROM THIS LAYER? ------------------------------------
// Asked as a MEASUREMENT rather than concluded from the spec, because "this
// cannot be done" is exactly the claim this project has seen be rigorous and
// still wrong (PS-36: a predecessor declared the Linux GPU values unreachable
// and the ticket shipped anyway). Each arm below tries a technique that
// ALREADY WORKS somewhere else in this codebase and records whether it
// transfers to the ServiceWorker realm.
//
// (1) RE-BLOB. worker_wrap's whole method for Worker/SharedWorker: rebuild the
//     body with our fragment prepended and hand back a blob: URL. If a service
//     worker can be registered from a blob: URL, the existing technique
//     transfers and the leak is fixable here.
expect('fix_blob_registration');
try {
  if (!navigator.serviceWorker) {
    done('fix_blob_registration', {note: 'navigator.serviceWorker absent'});
  } else {
    var blobUrl = URL.createObjectURL(new Blob(['/* noop */'], {type: 'text/javascript'}));
    navigator.serviceWorker.register(blobUrl).then(function () {
      done('fix_blob_registration', {accepted: true, note: 'blob: SW registration ACCEPTED'});
    }).catch(function (e) {
      done('fix_blob_registration', {accepted: false, refusal: String(e)});
    });
  }
} catch (e) { done('fix_blob_registration', {accepted: false, refusal: 'threw: ' + e}); }

// (2) CROSS-ORIGIN SCRIPT. If a SW script could be served from an origin we
//     control (an extension URL), we could author the realm on any site. The
//     same-origin rule is what would forbid it — measured, not assumed.
expect('fix_cross_origin_registration');
try {
  if (!navigator.serviceWorker) {
    done('fix_cross_origin_registration', {note: 'navigator.serviceWorker absent'});
  } else {
    navigator.serviceWorker.register('https://example.invalid/sw.js').then(function () {
      done('fix_cross_origin_registration', {accepted: true});
    }).catch(function (e) {
      done('fix_cross_origin_registration', {accepted: false, refusal: String(e)});
    });
  }
} catch (e) { done('fix_cross_origin_registration', {accepted: false, refusal: 'threw: ' + e}); }

// (3) IS THE ENTRY POINT EVEN PATCHABLE? A page-realm wrapper on
//     ServiceWorkerContainer.prototype.register is the hook a fix would have
//     to hang off. Patchability alone is NOT a fix — it only decides whether
//     (1) or (2) would have anywhere to stand — so it is recorded separately
//     from them rather than folded into a single verdict.
done('fix_register_patchable', (function () {
  try {
    var proto = window.ServiceWorkerContainer && window.ServiceWorkerContainer.prototype;
    if (!proto || !proto.register) return {patchable: false, note: 'no register on prototype'};
    var d = Object.getOwnPropertyDescriptor(proto, 'register');
    return {patchable: !!(d && (d.writable || d.configurable)),
            writable: !!(d && d.writable), configurable: !!(d && d.configurable)};
  } catch (e) { return {patchable: false, note: String(e)}; }
})());

// --- WebGL2 in the page realm ---------------------------------------------
// gpu_ext installs on WebGLRenderingContext AND WebGL2RenderingContext, but
// they are separate prototypes and a fix that reached only one would look
// clean to a WebGL1-only probe.
done('page_webgl2', (function () {
  try {
    var c = document.createElement('canvas'); c.width = 64; c.height = 64;
    var gl = c.getContext('webgl2');
    if (!gl) return {available: false, note: 'no webgl2 context'};
    var out = {available: true};
    var d = gl.getExtension('WEBGL_debug_renderer_info');
    if (!d) { out.unmasked_renderer = 'no-debug-renderer-info'; return out; }
    out.unmasked_vendor = String(gl.getParameter(d.UNMASKED_VENDOR_WEBGL));
    out.unmasked_renderer = String(gl.getParameter(d.UNMASKED_RENDERER_WEBGL));
    return out;
  } catch (e) { return {error: String(e)}; }
})());

// --- WebGPU ----------------------------------------------------------------
// A SECOND, INDEPENDENT graphics identity surface. gpu_ext patches WebGL only;
// navigator.gpu.requestAdapter().info exposes vendor/architecture/device and
// is not covered by any WebGL override. Recorded whether or not it is present:
// "the API does not exist here" is itself the answer to whether it can leak.
expect('webgpu_adapter');
(function () {
  try {
    if (!navigator.gpu || !navigator.gpu.requestAdapter) {
      done('webgpu_adapter', {available: false, note: 'navigator.gpu absent'});
      return;
    }
    navigator.gpu.requestAdapter().then(function (a) {
      if (!a) { done('webgpu_adapter', {available: false, note: 'no adapter'}); return; }
      var info = a.info || {};
      // Reported under unmasked_renderer so it lands in the same summary
      // column as the WebGL rows and a mismatch is visible side by side.
      done('webgpu_adapter', {
        available: true,
        unmasked_vendor: String(info.vendor === undefined ? '<none>' : info.vendor),
        unmasked_renderer: 'webgpu:' + String(info.vendor) + '/' +
                           String(info.architecture) + '/' + String(info.device) +
                           '/' + String(info.description)
      });
    }).catch(function (e) { done('webgpu_adapter', {error: 'requestAdapter failed: ' + e}); });
  } catch (e) { done('webgpu_adapter', {error: String(e)}); }
})();

// A worker created from INSIDE an about:blank child frame — the exact shape
// worker_wrap's docstring names as where creepjs caught the real GPU.
expect('worker_in_iframe');
try {
  var wf = document.createElement('iframe');
  wf.style.display = 'none';
  wf.src = 'about:blank';
  document.body.appendChild(wf);
  setTimeout(function () {
    try {
      var w = wf.contentWindow;
      var url = w.URL.createObjectURL(new w.Blob([workerSource()], {type: 'text/javascript'}));
      var worker = new w.Worker(url);
      worker.onmessage = function (m) {
        done('worker_in_iframe', (m.data && m.data.self) || {error: 'no reading'});
      };
      worker.onerror = function (e) {
        done('worker_in_iframe', {error: 'worker error: ' + (e && e.message)});
      };
      worker.postMessage({depth: 1});
    } catch (e) { done('worker_in_iframe', {error: 'iframe worker failed: ' + e}); }
  }, 400);
} catch (e) { done('worker_in_iframe', {error: 'iframe setup failed: ' + e}); }

// Anything still silent at the deadline is recorded as a timeout rather than
// dropped: "the realm did not answer" and "the realm was not asked" are
// different findings and must not collapse into one another.
setTimeout(function () {
  Object.keys(PENDING).forEach(function (k) { done(k, {error: 'timeout'}); });
  render();
}, """
        + str(REALM_TIMEOUT_MS)
        + r""");

render();
"""
    )


_PAGE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>PS-189 realm gpu probe</title>
<body><pre id="out">reading...</pre>
<script>
%%COLLECTOR%%
</script>
</body>
"""


def _serve_probe_page():
    """A loopback server for the realm probe, as a context manager.

    Serves the probe at ``/``, an empty document at ``/blank.html`` for the
    same-origin iframe arm, the worker body at ``/worker.js`` for the http(s)
    and module worker arms — those take DIFFERENT ``worker_wrap`` code paths
    from a blob worker (importScripts shim / module dynamic-import), and a
    probe that served no real URL would silently test neither — and the service
    worker at ``/sw.js``.

    ``/sw.js`` is served FROM THE ROOT SCOPE deliberately: a service worker's
    scope may not be broader than the path it was served from, so serving it
    from a subdirectory would silently narrow the registration. Reuses
    ``local_probe``'s handler shape rather than inventing a second web server.
    """
    import http.server
    import threading

    html = _PAGE_HTML.replace("%%COLLECTOR%%", _collector_js()).encode("utf-8")
    blank = b"<!doctype html><meta charset=utf-8><title>blank</title>"
    # The same body the blob workers run, so a difference between the arms is a
    # difference in the LAYER's reach and never in what was asked.
    worker_js = (
        "self.__PNA_SRC__ = " + json.dumps(_WORKER_BODY) + ";\n" + _WORKER_BODY
    ).encode("utf-8")
    sw_js = _SERVICE_WORKER_BODY.encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            if self.path.startswith("/worker.js"):
                body, ctype = worker_js, "text/javascript; charset=utf-8"
            elif self.path.startswith("/sw.js"):
                body, ctype = sw_js, "text/javascript; charset=utf-8"
            elif self.path.startswith("/blank"):
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


def read_cell(url: str, *, arm: str, seed: int, install_layer: bool) -> dict:
    """One cell: one arm, one seed, one layer state, every realm at once.

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
        # echoed from the request (the PS-103 discipline). This is what lets a
        # reader check --use-angle=swiftshader before attributing a SwiftShader
        # row to the product rather than to the harness (PS-14).
        captured["argv"] = list(args)
        return args

    chromium_tier._launch_args = _capturing_args
    record: dict = {
        "arm": arm,
        "seed": seed,
        "masking_layer": "on" if install_layer else "off",
    }
    try:
        session = chromium_tier.ChromiumSession(
            # Empty credential + allow_no_proxy is the loopback form (an
            # explicit NO_PROXY sentinel is a RETURN value, not an input — the
            # PS-161 trap, which emits a bogus --proxy-server and still reads
            # clean).
            "",
            seed=seed,
            declared_machine=arm,
            allow_unsandboxed=True,
            allow_no_proxy=True,
            install_layer=install_layer,
        )
        with session as live:
            page = live.new_page()
            page.goto(url, timeout=90000, wait_until="load")
            time.sleep(SETTLE_SECONDS)
            # Read through inner_text, the SAME path a real checker page is
            # read through — page.evaluate is blocked by CSP on real checker
            # pages, so nothing here may succeed through a route the live run
            # does not have.
            text = page.inner_text("body")
            record["sandbox_waived"] = getattr(session, "sandbox_waived", None)
            layer = getattr(session, "layer_report", None)
            if layer is not None:
                installed = getattr(layer, "installed", None)
                record["layer_installed"] = (
                    sorted(installed)
                    if isinstance(installed, (list, tuple, set))
                    else installed
                )
        try:
            record["reading"] = json.loads(text)
        except Exception:
            record["reading"] = None
            record["raw_text"] = text[:2000]
    except Exception as exc:  # noqa: BLE001 - a failed cell is a recorded cell
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["reading"] = None
    finally:
        chromium_tier._launch_args = original_args
    record["argv"] = captured.get("argv")
    return record


def _identity_of(realm_reading: object) -> str:
    """The unmasked renderer of one realm, or a marker naming why there is none."""
    if not isinstance(realm_reading, dict):
        return "<no reading>"
    if realm_reading.get("error"):
        return f"<error: {realm_reading['error']}>"
    return str(realm_reading.get("unmasked_renderer", "<absent>"))


def summarise(records: "list[dict]") -> str:
    """A per-cell table of DISTINCT identities across realms.

    The finding this script is for is "one profile, one launch, more than one
    identity", so the summary counts distinct values per cell rather than
    printing every realm — the full readings stay in the JSON.
    """
    lines = []
    for rec in records:
        head = f"{rec['arm']}/seed{rec['seed']}/layer-{rec['masking_layer']}"
        reading = rec.get("reading") or {}
        realms = reading.get("realms") if isinstance(reading, dict) else None
        if not isinstance(realms, dict) or not realms:
            lines.append(f"{head}: NO READING ({rec.get('error', 'no realms')})")
            continue
        by_identity: "dict[str, list[str]]" = {}
        for name, value in sorted(realms.items()):
            by_identity.setdefault(_identity_of(value), []).append(name)
        lines.append(f"{head}: {len(by_identity)} distinct identity/identities")
        for identity, names in sorted(by_identity.items()):
            lines.append(f"    {identity}")
            lines.append(f"        <- {', '.join(names)}")
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", required=True, help="output directory")
    parser.add_argument(
        "--arms", default=",".join(ARMS), help="comma-separated declared arms"
    )
    parser.add_argument(
        "--seeds", default=",".join(str(s) for s in SEEDS), help="comma-separated seeds"
    )
    parser.add_argument(
        "--layer",
        default="on",
        choices=("on", "off", "both"),
        help="masking layer state to read",
    )
    args = parser.parse_args(argv)

    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())
    states = (True, False) if args.layer == "both" else ((True,) if args.layer == "on" else (False,))

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: "list[dict]" = []
    with _serve_probe_page() as server:
        for arm in arms:
            for seed in seeds:
                for install_layer in states:
                    state = "on" if install_layer else "off"
                    print(f"[ps189] reading {arm}/seed{seed}/layer-{state} ...", flush=True)
                    rec = read_cell(
                        server.url, arm=arm, seed=seed, install_layer=install_layer
                    )
                    records.append(rec)
                    print(
                        f"[ps189]   -> {'ERROR: ' + rec['error'] if rec.get('error') else 'read'}",
                        flush=True,
                    )

    doc = {
        "schema_version": 1,
        "ticket": "PS-189",
        "observed_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "venue": "loopback (127.0.0.1) — no third party contacted, no exit in the picture",
        "records": records,
    }
    (out_dir / "realm-gpu.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8"
    )
    summary = summarise(records)
    (out_dir / "realm-gpu-summary.txt").write_text(summary + "\n", encoding="utf-8")
    print()
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
