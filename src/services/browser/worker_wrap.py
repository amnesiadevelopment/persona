"""Shared JS that carries every MAIN-world spoof patch into every reachable realm.

A content script runs in the page realm (and, with all_frames, in each frame the
browser itself creates), but NOT into realms the page builds at runtime: a Web
Worker, a fresh about:blank/srcdoc iframe, a worker spawned from inside such an
iframe, or a nested iframe. A detector (CreepJS, Pixelscan) reads a fingerprint
from one of those pristine realms and sees the real, unspoofed values — a
page/child mismatch is a hard tell (creepjs read the real GPU from an
OffscreenCanvas in a worker created inside an about:blank iframe).

Every spoof extension calls ``realm_bootstrap_js("applyPatch")``. Each installs
a self-contained bootstrap that (a) runs its own leaf ``applyPatch`` in this
realm, (b) CHAINS a wrapper onto this realm's Worker/SharedWorker, and (c)
CHAINS its iframe accessors, so its leaf re-runs in every worker and child
frame, recursively.

NOTHING IS STORED ON THE GLOBAL OBJECT. Each module's leaf, its source text and
its per-realm dedup set live in the bootstrap's own closure. A page cannot name
them, cannot reach them by enumerating the global object, and — the point of
PS-48 — cannot recover the profile seed that the leaf source carries.

That closure-only rule is what forced the shared registry out. The previous
design kept the leaves in ``G.__pnaBoots`` and their source text in
``G.__pnaBootSrc`` because N extensions each had to reach child realms, and a
per-module *guard* on HTMLIFrameElement would let only the FIRST module win the
single iframe getter. The registry answered that, at the cost of publishing
every leaf's source — and with it the seed compiled inside it — under a
readable name. CHAINING answers the same question without any shared state: a
module wraps whatever wrapper is already installed rather than guarding on a
global, so all N compose. (Precedent in tree: the Firefox cloak chains
``Function.prototype.toString`` and holds its registry in a closure WeakMap —
``tests/test_ff_language_override.py`` pins the absence of a marker.)

Realm-crossing composes the same way, and this is the subtle part. A function
cannot be cloned into a Worker, so the leaf has to cross as TEXT. Each chain
link builds its own one-module fragment (its leaf's source plus this
bootstrap's own source, via ``__pnaInstall.toString()``) and prepends it to the
worker body, then hands the re-blobbed body to the link BELOW it, which
prepends its own fragment, and so on. The payload that finally reaches the
worker therefore carries every module's leaf even though no module ever saw
another's — and each fragment carries only its OWN source, once, so the payload
grows linearly with module count and worker depth rather than exponentially.

Lessons the old registry paid for, which still bind here:

* ``__pnaInstall`` is a NAMED function expression and re-binds itself by that
  name inside its own serialized body. A bare anonymous ``(fn)(self)`` left the
  installer unresolvable in the worker, so a NESTED worker's wrapper threw and
  ran completely unspoofed (a creepjs GPU tell).
* The fragment is built AT new-Worker time, not when the wrapper is installed.
  Modules register across separate content scripts; a payload snapshotted at
  install time would carry only the leaves present when the first module ran.
  (Under chaining this is structural — each link builds its own fragment — but
  the ordering requirement is unchanged.)
* The helpers live INSIDE ``__pnaInstall`` so that its ``toString()`` carries
  them. A helper defined beside it would be undefined in the worker realm.
* Every realm the page can reach must be covered, at every depth. A depth-2
  worker that silently receives nothing does not throw — it just reports the
  REAL GPU/hardwareConcurrency/audio/fonts while the page reports spoofed ones.
  ``tests/test_worker_wrap.py`` executes the generated bootstrap through three
  worker generations in isolated ``node:vm`` realms for exactly this reason.

⚠️ ONE REALM IS NOT COVERED, AND IT LEAKS: THE SERVICE WORKER (PS-189)
-----------------------------------------------------------------------
Read the bullet above as the INTENT it is, not as a completed inventory. The
chaining below wraps ``Worker`` and ``SharedWorker`` — both CONSTRUCTORS the
page calls — and a ``ServiceWorkerGlobalScope`` is reached by NEITHER, because
a service worker is never constructed by the page at all: it is REGISTERED with
the browser (``navigator.serviceWorker.register``) and started by the browser,
often for a later navigation, so there is no construction for a constructor
wrapper to intercept and no realm handle to chain onto. An MV3 content script
does not run there either.

MEASURED, not deduced — ``scripts/ps189_realm_gpu.py``, layer ON, one launch,
one instant, both seeds. Twelve realms read; ELEVEN reported the profile's
authored card and the service worker reported something else:

    linux/24601   11 realms: ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 ...)
                  service_worker: ANGLE (Google, ... SwiftShader ...)  <- THE HOST
    macos/24601   11 realms: ANGLE (Apple, ... Apple M1 ...)
                  service_worker: ANGLE (Apple, ... Apple M2 ...)      <- the ENGINE

That is Invariant #0 on linux (the GPU-less container's real software
rasteriser reaching a third-party page) and the PS-155/PS-161 two-author
contradiction on macos — ONE hole with two faces, depending only on whether the
engine happens to author that arm. Confirmed live: creepjs prints
``ServiceWorkerGlobalScope`` immediately above the ``gpu:`` row it leaked, and
it is the only worker-scope label on the page.

WINDOWS IS CLEAN HERE FOR A REASON THAT DOES NOT GENERALISE. On windows
``gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS`` stands our layer down entirely, so the
ENGINE authors every realm including this one and there is no second author to
disagree with. So a green windows reading is NOT evidence that this module's
realm coverage is complete, and windows must never be used as the control for
that question.

WHY THIS IS NOT FIXED HERE. Both techniques this module already relies on were
tried against the service worker and REFUSED by the browser (measured, same
script): registering a SW from a ``blob:`` URL — the re-blob trick the whole
worker path is built on — fails with *"The URL protocol of the script
('blob:...') is not supported"*, and a cross-origin script URL fails with a
``SecurityError``. ``ServiceWorkerContainer.prototype.register`` IS patchable
(writable and configurable), so a HOOK exists, but no delivery technique does:
there is nowhere to put our leaf. Suppressing registration outright would close
the leak and break every site that needs a service worker — a product decision,
not this module's to take. See PS-189 for the full record.

Worker scheme (proven by locale_ext): blob:/data: workers are re-blobbed under
the same scheme (the site's CSP already allows that scheme, so it stays allowed)
with the fragment prepended; http(s) workers get an importScripts shim. Module
workers get a module blob that dynamic-imports the original.

The Blob/URL/XHR constructors used to assemble a payload are captured from the
realm at install time and called through those captured references. They run at
document_start, before any page script, so what they capture is native. A page
that later overrides ``URL.createObjectURL`` or ``Blob`` would otherwise be
handed the assembled payload — the seed-bearing leaf source — as an argument,
which is the same disclosure PS-48 closes on the global object.
"""

import json
import textwrap
from typing import NamedTuple


class WorkerCloak(NamedTuple):
    """Every ENGINE-SPECIFIC seam in the bootstrap, in one object.

    The bootstrap installs wrappers of its own — ``Worker``, ``SharedWorker`` and
    the two ``HTMLIFrameElement`` accessors — quite apart from whatever the leaf
    patches. Those wrappers need the same cloak the leaf's do, and which cloak is
    correct depends on the engine, so it arrives here as a seam instead of being
    hard-coded. Four cloak fields, because the marker is applied in four
    syntactic positions and only one of them is a statement:

    * ``setup``   — statements spliced inside ``__pnaInstall``, once per realm.
    * ``apply``   — statements that cloak the ``Worker``/``SharedWorker`` wrapper.
    * ``frame_open`` / ``frame_close`` — wrapped AROUND the iframe accessor
      function expression, which is an argument position and cannot take one.

    Two further fields carry the engine's WORKER-BODY DELIVERY, which is not a
    cloak at all:

    * ``blob_setup``   — statements spliced inside ``__pnaInstall`` that prepare
      whatever the delivery needs (Firefox: retain the ``Blob`` behind each
      object URL).
    * ``blob_resolve`` — statements spliced at the TOP of the ``blob:``/``data:``
      branch of the Worker wrapper, which may ``return`` a constructed worker to
      pre-empt the shared sync-XHR path below.

    THEY LIVE IN THE SAME OBJECT DELIBERATELY, and that is a correctness
    property rather than tidiness. ``blob_setup`` defines ``__rb`` and cloaks its
    ``createObjectURL`` chain through ``__bcloak``, which only exists if ``setup``
    put it there — so a Firefox delivery paired with a Chromium cloak would
    install an UNCLOAKED wrapper, i.e. the exact class of tell PS-78 round 3 was
    rejected for. One object per engine makes that pairing unexpressible.

    Chromium's form is the ORIGINAL text and must stay byte-identical: it is the
    baseline every prior readback was taken against (the PS-78 boundary,
    "Chromium is unchanged"). Its ``setup``, frame pair and both delivery fields
    are therefore EMPTY STRINGS spliced at points chosen so that the empty case
    reproduces the old template exactly — no stray blank line, no moved
    indentation.
    """

    setup: str
    apply: str
    frame_open: str
    frame_close: str
    blob_setup: str = ""
    blob_resolve: str = ""


# Chromium: mark the wrapper for the single `Function.prototype.toString` patch
# native_ext.py installs from the extension side, which reads `__pnaName`.
CHROMIUM_WORKER_CLOAK = WorkerCloak(
    setup="",
    apply=(
        '        try { Object.defineProperty(W, "__pnaName", { value: Orig.name }); } catch (e) {}\n'
        '        try { Object.defineProperty(W, "name", { value: Orig.name }); } catch (e) {}'
    ),
    frame_open="",
    frame_close="",
)


def realm_bootstrap_js(
    apply_fn_name: str, cloak: WorkerCloak = CHROMIUM_WORKER_CLOAK
) -> str:
    """Return JS that runs ``apply_fn_name`` (a leaf applyPatch(G)) in this realm
    and chains it into every realm this one can reach.

    ``cloak`` selects how the bootstrap's OWN wrappers are made to stringify as
    native; it defaults to the Chromium form, which is the original text. Pass
    ``firefox_worker_cloak()`` on that engine — see its docstring for why the
    default is actively wrong there rather than merely unnecessary.

    The caller must have defined ``apply_fn_name`` already. Nothing is written to
    the global object: the leaf, its source and the dedup set stay in closure.

    ONE ``blob:``/``data:`` BRANCH, ON BOTH ENGINES — with a Firefox-only
    delivery seam spliced at the top of it (``blob_resolve``), never a second
    branch. An earlier revision of PS-78 added a ``blob_via_import_scripts`` flag
    that gave Firefox a separate ``blob:`` branch using an ``importScripts``
    shim, on the belief that this engine refuses a SYNCHRONOUS XHR against a
    ``blob:`` URL (``NetworkError``). THE FLAG IS GONE, and its premise is only
    HALF right — which is worse than plainly wrong, because it is right on every
    origin a casual measurement reaches.

    IT IS NOT AN ENGINE FACT, IT IS A CSP FACT. The sync XHR is subject to the
    page's ``connect-src``, so the answer depends on the ORIGIN, not on the
    browser. Measured on firefox-20 / 151.0 through this project's own launch
    path::

        origin                    new XMLHttpRequest().open("GET", blobUrl, false)
        https://example.com/      -> status 200, body read back intact
        http://127.0.0.1          -> status 200
        data: / about:blank       -> status 200
        https://duckduckgo.com/   -> THROWS NetworkError

    DuckDuckGo sends ``default-src 'none'`` with no ``blob:`` in its
    ``connect-src``, so the fetch is refused by policy. An earlier round recorded
    the "status 200" half here as a settled general fact; it was measured only on
    origins that ship no CSP, and the conclusion did not survive an origin that
    does. THAT ORIGIN IS THE DEFAULT START PAGE — ``_ensure_firefox_policies()``
    pins DuckDuckGo — so the refusing case is where every Firefox profile already
    is the moment it opens, not an exotic corner.

    The in-tree corroboration is real but proves less than it appears to: the
    locale spoof at ``invisible_launch.py:504`` has shipped this identical
    sync-XHR ``blob:`` path in production all along, which establishes that the
    path WORKS, not that it works EVERYWHERE. It is subject to the same
    ``connect-src`` on the same origins.

    Hence ``blob_resolve``: on Firefox the retained ``Blob`` is composed directly
    and the XHR below is never reached for a url this realm minted. See
    :data:`_FIREFOX_BLOB_RETAIN_SETUP` for the measurement and for why neither
    the XHR nor the deleted shim clears both constraints alone.

    WHY THE SHIM WAS NOT MERELY REDUNDANT BUT HARMFUL. ``importScripts(blobUrl)``
    DEFERS the fetch of the original body out of the ``new Worker(...)`` call and
    into the worker's own startup, so the page's blob URL has to still be alive
    at that later moment. Revoking right after construction is the pattern MDN
    documents and bundlers emit::

        const w = new Worker(url);
        URL.revokeObjectURL(url);   // ← body has not been read yet

    Measured on the real launch path with the shim installed: the worker emits
    ZERO events — ``importScripts`` throws ``SecurityError`` against the revoked
    URL, the shim's own ``catch(e){}`` swallows it inside the worker realm, and
    the original body never runs. That is a FUNCTIONAL BREAK, strictly worse than
    the leak it was meant to close, and the outer ``try``/``catch`` around the
    construct cannot save it because the throw happens in another realm.

    The sync-XHR branch below has the property the shim lacked: it reads the body
    SYNCHRONOUSLY, inside the constructor, while the URL is still valid. So it
    survives both orderings. Verified on the real launch path, every realm
    reading its own marker from the inside — depth-1, depth-2 (a worker spawning
    a worker), a worker created inside a fresh ``about:blank`` iframe, and a
    ``data:`` worker — all spoofed, with and without a revoke.

    Keeping ONE branch is also what keeps Chromium byte-identical: there is no
    per-engine text to drift.
    """
    return (
        r"""
  var SELF = (typeof self !== "undefined") ? self : this;

  // The installer is a NAMED function expression: it must be able to re-bind
  // itself by name inside its own serialized body, because that body is what
  // crosses into a worker and has to keep covering the worker's OWN children.
  var __pnaInstall = function __pnaInstall(G, LEAF) {
    try {
      if (!G || typeof LEAF !== "function") return;

      // Realms this module has already covered. A closure WeakSet, NOT a marker
      // property on the realm: the iframe accessor below can fire many times for
      // the same child window and must not re-run the leaf each time. Declared
      // inside the installer so it ships with __pnaInstall.toString() and each
      // realm gets its own; a set in an enclosing scope would be undefined in a
      // worker, which is the failure mode this file exists to prevent.
      var SEEN = (typeof WeakSet === "function") ? new WeakSet() : null;
      var fresh = function (w) {
        try {
          if (!SEEN) return true;
          if (SEEN.has(w)) return false;
          SEEN.add(w);
          return true;
        } catch (e) { return true; }
      };

      // Capture the payload-assembly built-ins NOW, at document_start, before
      // any page script has run. Called through these references so a later
      // page override cannot be handed the assembled payload (which carries the
      // leaf source, and with it the seed).
      var _URL = G.URL, _cou = _URL && _URL.createObjectURL;
      var _Blob = G.Blob;
      var _XHR = G.XMLHttpRequest;
      var _Ref = G.Reflect || (typeof Reflect !== "undefined" ? Reflect : null);

      var mkurl = function (body) {
        return _cou.call(_URL, new _Blob([body], { type: "application/javascript" }));
      };

      // This module's one-module payload fragment, rebuilt per construction.
      // It carries the leaf's source and the installer's own source — and
      // nothing else. The next link in the chain prepends its own fragment to
      // whatever this produced, which is how N modules compose without ever
      // sharing state.
      var fragment = function () {
        return "(function(){try{var L=(" + LEAF.toString() + ");" +
          "var I=(" + __pnaInstall.toString() + ");" +
          "I((typeof self!=='undefined'?self:this),L);}catch(e){}})();";
      };

      // Run this module's leaf in this realm.
      try { LEAF(G); } catch (e) {}%(cloak_setup)s%(blob_setup)s

      // --- workers ---------------------------------------------------------
      // Chain onto whatever Worker is already installed. Delegating to Orig is
      // what makes composition work: Orig is the PREVIOUS module's wrapper, so
      // it re-blobs what this one produced and prepends its own fragment.
      var wrapWorker = function (Orig) {
        if (typeof Orig !== "function") return Orig;
        var W = function (url, options) {
          try {
            var BOOT = fragment();
            var s = String(url);
            // Resolve a RELATIVE worker URL (e.g. creepjs's './creep.js') to an
            // absolute one so it takes the http(s) importScripts path below. A
            // relative URL matched none of the scheme tests and fell through to
            // the native, UNSPOOFED construct — the real path creepjs uses to
            // read the engine-default GPU from a worker.
            if (!/^(https?:|blob:|data:)/i.test(s)) {
              try {
                var base = (G.location && G.location.href) || undefined;
                var rs = new URL(s, base).href;
                if (/^https?:/i.test(rs)) s = rs;
              } catch (er) {}
            }
            if (options && options.type === "module") {
              try {
                var abs = s;
                try { abs = new URL(s, (G.location && G.location.href) || undefined).href; } catch (e0) {}
                var mbody = BOOT + "\nimport(" + JSON.stringify(abs) + ").catch(function(e){});";
                return _Ref.construct(Orig, [mkurl(mbody), options], W);
              } catch (em) {
                return _Ref.construct(Orig, [url, options], W);
              }
            }
            if (/^https?:/i.test(s)) {
              var body = BOOT + "\ntry{importScripts(" + JSON.stringify(s) + ");}catch(e){}";
              return _Ref.construct(Orig, [mkurl(body), options], W);
            }
            if (/^blob:|^data:/i.test(s)) {%(blob_resolve)s
              try {
                var x = new _XHR();
                x.open("GET", s, false);
                x.send();
                if (x.status === 0 || (x.status >= 200 && x.status < 300)) {
                  return _Ref.construct(Orig, [mkurl(BOOT + "\n" + x.responseText), options], W);
                }
              } catch (e) {}
              return _Ref.construct(Orig, [url, options], W);
            }
            return _Ref.construct(Orig, [url, options], W);
          } catch (e) { return _Ref.construct(Orig, [url, options], W); }
        };
        W.prototype = Orig.prototype;
%(cloak_apply)s
        return W;
      };

      try {
        if (G.Worker) G.Worker = wrapWorker(G.Worker);
        if (G.SharedWorker) G.SharedWorker = wrapWorker(G.SharedWorker);
      } catch (e) {}

      // --- child frames ----------------------------------------------------
      // Same-realm about:blank / srcdoc children, recursively. CHAIN the
      // accessor rather than guarding on a marker: a per-module guard would let
      // only the first module win the single iframe getter, which is the
      // objection the shared registry used to answer. Re-running the whole
      // installer in the child covers that child's own workers and frames.
      try {
        var IF = G.HTMLIFrameElement;
        if (IF && IF.prototype) {
          ["contentWindow", "contentDocument"].forEach(function (prop) {
            var d0 = Object.getOwnPropertyDescriptor(IF.prototype, prop);
            if (!d0 || !d0.get) return;
            Object.defineProperty(IF.prototype, prop, {
              configurable: true, enumerable: d0.enumerable,
              get: %(cloak_frame_open)sfunction () {
                var r = d0.get.call(this);
                try {
                  var w = prop === "contentWindow" ? r : (r && r.defaultView);
                  if (w && fresh(w)) __pnaInstall(w, LEAF);
                } catch (e) {}
                return r;
              }%(cloak_frame_close)s,
            });
          });
        }
      } catch (e) {}
    } catch (e) {}
  };

  try { __pnaInstall(SELF, %(fn)s); } catch (e) {}
"""
        % {
            "fn": apply_fn_name,
            "cloak_setup": cloak.setup,
            "cloak_apply": cloak.apply,
            "cloak_frame_open": cloak.frame_open,
            "cloak_frame_close": cloak.frame_close,
            "blob_setup": cloak.blob_setup,
            "blob_resolve": cloak.blob_resolve,
        }
    )


# --- the BOOTSTRAP's own cloak, on Firefox ----------------------------------
#
# Distinct from `_FIREFOX_NATIVE_WRAP` below, and the distinction is the whole
# point of PS-78 round 3. That one cloaks the LEAF's wrappers (readPixels &c);
# this one cloaks the wrappers the BOOTSTRAP installs — `Worker`,
# `SharedWorker`, and the two `HTMLIFrameElement` accessors. They were left
# carrying `__pnaName` when this PR became the first thing to deliver
# `realm_bootstrap_js` to Firefox at all, where no extension exists to read that
# marker: a bare own property on every wrapper, plus a `toString()` returning
# raw patch source where every real engine returns `[native code]`. Two
# independent reads, either sufficient on its own.
#
# It cannot simply REUSE the leaf's cloak. The leaf's `__nm`/`nativeWrap` live
# inside `applyWebglPatch`'s body; the bootstrap is a sibling scope and a
# separate serialisation unit. More decisively, `setup` below is spliced INSIDE
# `__pnaInstall`, because `__pnaInstall.toString()` is what crosses into a
# worker — anything defined in an enclosing scope is undefined there, which is
# the exact failure this module's docstring records (a depth-2 worker silently
# reporting REAL values). So the bootstrap carries its own copy, per realm.
#
# It is installed AFTER `LEAF(G)` deliberately: `Function.prototype.toString` is
# CHAINED, not flag-guarded, so leaf-then-bootstrap composes with the leaf's
# cloak (and with the locale/outer-size cloaks already in the realm) instead of
# racing it for the single slot.
_FIREFOX_WORKER_CLOAK_SETUP = r"""

      // --- cloak for the wrappers THIS BOOTSTRAP installs (Firefox) --------
      // See the note beside _FIREFOX_WORKER_CLOAK_SETUP in worker_wrap.py.
      // Spliced inside __pnaInstall so it ships with __pnaInstall.toString()
      // and every realm — page, worker, nested worker, child frame — gets its
      // own. Mirrors invisible_launch._native_cloak_js: closure WeakMap (no own
      // property to enumerate), SpiderMonkey's three-line native shape (NOT
      // V8's one-liner, which is itself a tell on this engine), and a CHAINED
      // Function.prototype.toString.
      var __bnm = (typeof WeakMap === "function") ? new WeakMap() : null;
      var __bnl = String.fromCharCode(10);
      // G's Function, never the lexical one: the leaf reaches a child frame as
      // a PARENT-REALM function object, so a bare `Function.prototype` here
      // would re-patch the parent's and leave the child's pristine while the
      // wrappers ARE installed into the child.
      var __bF = G.Function;
      var __bpts = (__bF && __bF.prototype && __bF.prototype.toString)
                   || Function.prototype.toString;
      var __bts = function () {
        'use strict';
        try {
          var n = __bnm && __bnm.get(this);
          if (typeof n === "string") {
            return "function " + n + "() {" + __bnl + "    [native code]" + __bnl + "}";
          }
        } catch (e) {}
        return __bpts.apply(this, arguments);
      };
      // `f` gets `.name` = n; its SOURCE TEXT reports `s` (an accessor's name
      // carries a `get ` prefix that its source text does not).
      var __bcloak = function (f, n, s) {
        try {
          Object.defineProperty(f, "name", { value: n, configurable: true });
          if (__bnm) { __bnm.set(f, s === undefined ? n : s); }
        } catch (e) {}
        return f;
      };
      try {
        // Cloak the patch as "toString": a detector stringifies
        // Function.prototype.toString to catch exactly this trick.
        if (__bnm) { __bnm.set(__bts, "toString"); }
        Object.defineProperty(__bts, "name", { value: "toString", configurable: true });
        if (__bF && __bF.prototype) { __bF.prototype.toString = __bts; }
      } catch (e) {}"""


# --- the Firefox WORKER-BODY DELIVERY seam ----------------------------------
#
# NOT A CLOAK. This is how the boot payload gets INTO a blob: worker at all, and
# it exists because the shared sync-XHR path does not survive a real CSP.
#
# THE DEFECT, measured on the real engine through this project's own launch path
# (xvfb + spawn_browser(in_process=True) + get_ff_eval, seeds pinned directly via
# `fingerprint_seed_value` so the digest tracks the SEED and not the name):
#
#     origin                    sync XHR on blob:   page (1000 vs 2000)   WORKER
#     https://duckduckgo.com/   THREW NetworkError  differ                COLLIDE
#     https://example.com/      status 200          differ                differ
#
# The Worker wrapper reads the blob body with a SYNCHRONOUS XHR, and that XHR is
# subject to the page's `connect-src`. DuckDuckGo sends `default-src 'none'` with
# no `blob:` in its connect-src, so the XHR throws, the wrapper's own catch falls
# back to constructing the ORIGINAL Worker, and the result is the worst available
# shape: a worker that RUNS NORMALLY AND CARRIES NO SPOOF. Two profiles then read
# the SAME readPixels digest in the worker realm — the exact linkability
# `webgl_ext.py` exists to prevent, in exactly the realm its docstring names as
# the one detectors read.
#
# AND THAT ORIGIN IS THE DEFAULT START PAGE. `_ensure_firefox_policies()` pins
# DuckDuckGo, so this is not an exotic origin reached by browsing somewhere
# unusual — it is where every Firefox profile already is the moment it opens.
#
# WHY THIS ROUTE AND NOT THE OTHER TWO. Three deliveries were measured against
# the two constraints that have each already rejected a round of PS-78, on
# duckduckgo.com:
#
#     route                      reaches the worker under CSP   survives revoke
#     sync XHR (shared path)     NO  (NetworkError)             yes
#     importScripts shim         yes                            NO (SecurityError)
#     new Blob([BOOT, body])     YES                            YES
#
# NEITHER OF THE FIRST TWO CLEARS BOTH, which is why this is not a matter of
# taste: the `importScripts` shim was deleted in round 2 precisely because a
# worker whose blob URL is revoked right after construction (the pattern MDN
# documents and bundlers emit) never ran at all. Composing the Blob OBJECT needs
# no fetch, no XHR and no importScripts, so no CSP directive governs it; and a
# `Blob` outlives `revokeObjectURL`, which tears down only the URL mapping. One
# change retires both rejections instead of trading one for the other.
#
# HOW THE BLOB IS OBTAINED. `URL.createObjectURL` is CHAINED so each minted url
# is remembered against the `Blob` it came from, and `revokeObjectURL` is chained
# to forget it. THE RETENTION THIS ADDS IS ZERO, which is the point of chaining
# the revoke as well: an un-revoked object URL already pins its blob alive in the
# engine for the document's lifetime, so the map's lifetime is the engine's own
# and a page that revokes properly frees the entry immediately. A plain Map, not
# a WeakMap: the key is a STRING, which a WeakMap cannot hold.
#
# IT DEGRADES TO THE OLD BEHAVIOUR, NEVER BELOW IT. A `blob:` url the map has
# never seen (minted before this script ran, or in another realm) simply falls
# through to the sync-XHR path below — which is what it would have done anyway.
# `data:` is untouched by this seam and keeps the XHR path, correctly: there is
# no object URL to retain, and a data: URL carries its own body inline.
#
# BOTH NEW WRAPPERS ARE CLOAKED, through the same `__bcloak` the rest of this
# object installs. An uncloaked `URL.createObjectURL` stringifying as raw patch
# source is the identical class of tell that got round 3 rejected, and it would
# be a fresh one introduced by the fix for the previous one.
_FIREFOX_BLOB_RETAIN_SETUP = r"""

      // --- retain the Blob behind each object URL (Firefox delivery) -------
      // See the note beside _FIREFOX_BLOB_RETAIN_SETUP in worker_wrap.py. The
      // worker wrapper composes `new Blob([BOOT, retained])` instead of
      // re-fetching the url, because a restrictive `connect-src` (the DEFAULT
      // start page ships one) refuses the fetch and leaves the worker unspoofed.
      //
      // Spliced inside __pnaInstall so it ships with __pnaInstall.toString() and
      // every realm gets its OWN map — a map in an enclosing scope would be
      // undefined in a worker, the exact failure this module's docstring records.
      var __rb = (typeof Map === "function") ? new Map() : null;
      try {
        if (__rb && _URL && typeof _cou === "function"
            && typeof _URL.revokeObjectURL === "function") {
          var __orv = _URL.revokeObjectURL;
          var __wcou = function createObjectURL(obj) {
            var u = _cou.apply(this, arguments);
            // Only Blob bodies are useful to compose with; a MediaSource has no
            // body to prepend to and must fall through to the old path.
            try { if (obj instanceof _Blob) { __rb.set(String(u), obj); } } catch (e) {}
            return u;
          };
          var __wrv = function revokeObjectURL(u) {
            // Forget FIRST, so the map can never outlive the engine's own
            // mapping even if the underlying revoke throws.
            try { __rb["delete"](String(u)); } catch (e) {}
            return __orv.apply(this, arguments);
          };
          __bcloak(__wcou, "createObjectURL");
          __bcloak(__wrv, "revokeObjectURL");
          _URL.createObjectURL = __wcou;
          _URL.revokeObjectURL = __wrv;
        }
      } catch (e) {}"""

# Spliced at the TOP of the `blob:`/`data:` branch, so it pre-empts the sync XHR
# for a blob: url whose Blob we still hold and falls through for everything else.
# `_Blob` is the captured native constructor, so a page that later overrode
# `Blob` is neither consulted nor handed the seed-bearing payload (PS-48).
_FIREFOX_BLOB_RESOLVE = r"""
              // Compose the retained Blob rather than re-fetching the url: no
              // fetch means no `connect-src` to refuse it, and a Blob outlives
              // revokeObjectURL. Falls through to the XHR path below when this
              // realm never minted the url. See _FIREFOX_BLOB_RETAIN_SETUP.
              try {
                var rb = __rb && __rb.get(s);
                if (rb) {
                  var nb = new _Blob([BOOT + "\n", rb], { type: "application/javascript" });
                  return _Ref.construct(Orig, [_cou.call(_URL, nb), options], W);
                }
              } catch (e) {}"""


def firefox_worker_cloak() -> WorkerCloak:
    """The ``WorkerCloak`` a FIREFOX caller passes to :func:`realm_bootstrap_js`.

    Cloaks the bootstrap's own ``Worker``/``SharedWorker`` wrappers and its two
    ``HTMLIFrameElement`` accessors through a closure WeakMap, adding NO own
    property — the standard already set in tree by the locale spoof's Firefox
    worker wrapper (``invisible_launch.py:511``, ``return __cloak(W,Orig.name)``,
    whose marker-absence ``tests/test_ff_language_override.py:166`` pins).

    The Chromium default is not merely unnecessary on this engine, it is a tell:
    ``__pnaName`` exists on no browser, and nothing on Firefox reads it.

    ALSO carries this engine's WORKER-BODY DELIVERY (``blob_setup`` /
    ``blob_resolve``), which is not a cloak: the shared sync-XHR path is refused
    by a restrictive ``connect-src`` and leaves the worker realm UNSPOOFED on the
    default start page. See :data:`_FIREFOX_BLOB_RETAIN_SETUP` for the
    measurement and for why neither of the two previously-tried routes clears
    both constraints. The two travel in one object because ``blob_setup`` calls
    ``__bcloak``, which ``setup`` defines — pairing this delivery with Chromium's
    cloak would install an uncloaked wrapper.
    """
    return WorkerCloak(
        setup=_FIREFOX_WORKER_CLOAK_SETUP,
        apply="        __bcloak(W, Orig.name);",
        # An accessor's `.name` is "get contentWindow" while its source text
        # stringifies as "function contentWindow() { [native code] }" — hence the
        # two names. Wrapped AROUND the function expression because that is an
        # argument position and cannot take a statement.
        frame_open="__bcloak(",
        frame_close=', "get " + prop, prop)',
        blob_setup=_FIREFOX_BLOB_RETAIN_SETUP,
        blob_resolve=_FIREFOX_BLOB_RESOLVE,
    )


# --- the Firefox cloak seam, shared by every init-script spoof --------------
#
# On CHROMIUM every spoof is an MV3 extension and native_ext.py installs one
# Function.prototype.toString patch that reads each wrapper's `__pnaName` marker
# property. FIREFOX loads no persona extension at all (invisible_launch.py is
# the whole launch path), so on that engine `__pnaName` has nobody to read it:
# the marker is not a cloak there, it is a bare own property on every wrapper —
# a tell rather than a hiding place. Each Firefox init-script spoof therefore
# carries the cloak itself, and this is the one copy of it.
#
# Three deliberate differences from the Chromium form, each mirroring the cloak
# already in invisible_launch._native_cloak_js:
#
#   * SPIDERMONKEY's native shape (three lines, four-space indent), NOT V8's
#     one-liner. Emitting V8's form on Firefox is itself a masking tell — one
#     `Array.prototype.map.toString()` comparison away.
#   * a closure WeakMap, so NO own property is added to any wrapper and the
#     registry cannot be enumerated or swept for symbols.
#   * `Function.prototype.toString` is CHAINED, not flag-guarded, so this
#     composes with the locale/outer-size cloaks already installed in the realm
#     instead of racing them for the single slot.
#
# It must be spliced INSIDE the leaf `applyPatch`: the leaf crosses into a
# worker as SOURCE TEXT (``realm_bootstrap_js`` serialises it with
# ``LEAF.toString()``), so anything defined in an enclosing scope is undefined
# in the worker realm — the exact failure this module's docstring records, where
# a depth-2 worker silently reported REAL values while the page reported
# spoofed ones.
_FIREFOX_NATIVE_WRAP = r"""  var __nm = (typeof WeakMap === 'function') ? new WeakMap() : null;
  var __nl = String.fromCharCode(10);
  // THE REALM MUST COME FROM G, NEVER FROM THE LEXICAL SCOPE. The leaf reaches
  // a child frame as a PARENT-REALM FUNCTION OBJECT — the chained
  // `contentWindow` accessor above calls `__pnaInstall(childWindow, LEAF)` with
  // the leaf itself, not with source text. So a bare `Function.prototype` here
  // resolves to the PARENT's: it re-patches an already-patched toString (a
  // no-op) and leaves the child realm's own pristine, while the wrappers ARE
  // installed into the child. Read from inside that child — where a detector
  // runs — the wrapper then stringifies as raw patch source. Measured in two
  // isolated realms, not reasoned.
  //
  // `G.Function.prototype` is Chromium's shape (native_ext.py:32,48) and the
  // reason its comment names "a fresh about:blank iframe … has its own
  // Function.prototype". The WORKER realm never had this problem — the leaf
  // crosses there as SOURCE TEXT and is re-evaluated in the worker's own realm,
  // so both forms resolve correctly there; this is the frame case only.
  //
  // FAIL SOFT, NEVER `return`: this block is spliced INSIDE the leaf, so
  // bailing out here would skip the spoof's own overrides too — trading the
  // unlinkability fix for the masking one. A realm without a usable
  // Function.prototype loses the cloak and keeps the perturbation.
  var __F = G.Function;
  var __pts = (__F && __F.prototype && __F.prototype.toString)
              || Function.prototype.toString;
  var __ts = function () {
    'use strict';
    // `this` is the function being stringified. Strict mode so a primitive
    // `this` stays primitive and still reaches the original for its TypeError.
    try {
      var n = __nm && __nm.get(this);
      if (typeof n === 'string') {
        return 'function ' + n + '() {' + __nl + '    [native code]' + __nl + '}';
      }
    } catch (e) {}
    return __pts.apply(this, arguments);
  };
  try {
    // Cloak the patch as "toString": a detector stringifies
    // Function.prototype.toString to catch exactly this trick.
    if (__nm) { __nm.set(__ts, 'toString'); }
    Object.defineProperty(__ts, 'name', { value: 'toString', configurable: true });
    // G's prototype, not the lexical one — see the note above.
    if (__F && __F.prototype) { __F.prototype.toString = __ts; }
  } catch (e) {}

  function nativeWrap(orig, replacement) {
    try {
      // configurable: true is the descriptor a native function's own `name`
      // carries; the Chromium form omits it because its marker property, not
      // `name`, is what its extension-side cloak reads.
      Object.defineProperty(replacement, 'name',
                            { value: orig.name, configurable: true });
      if (__nm) { __nm.set(replacement, orig.name); }
    } catch (e) {}
    return replacement;
  }"""


def firefox_native_wrap_js() -> str:
    """The ``nativeWrap`` seam a FIREFOX init-script spoof splices into its leaf.

    Returns the Firefox form: a chained ``Function.prototype.toString`` backed by
    a closure WeakMap, emitting SpiderMonkey's native shape. Splice it where the
    Chromium build puts its own ``nativeWrap`` — everything else in a spoof's
    patch body is shared between the two engines, and this is the only part that
    differs.
    """
    return _FIREFOX_NATIVE_WRAP


# ---------------------------------------------------------------------------
# Per-realm idempotency guard (PS-93)
# ---------------------------------------------------------------------------
# Replaces the 11 `G.__personaX = true` markers that used to sit ENUMERABLE on
# the global object, where `Object.getOwnPropertyNames(self)` — the sweep every
# fingerprinter runs, and the one `realm.bootMarkers` encodes — reads them.
#
# THE CONSTRAINT SET, which is narrower than it looks. Dedup must be:
#
#   (a) per-realm    — one realm's state must not answer for another's, or a
#                      child frame silently never gets the leaf;
#   (b) shared across INDEPENDENT `__pnaInstall` invocations into the SAME
#       realm — `all_frames:true` means a same-origin child runs the content
#       script ITSELF *and* is installed into by the parent's `contentWindow`
#       accessor, and Firefox re-evaluates whole leaves into open tabs
#       (invisible_launch.py `_apply_audio_to_open_tabs`). Those two invocations
#       share no closure AND hold DIFFERENT `LEAF` function objects, so neither
#       a closure `WeakSet` nor a marker on the leaf can carry state between
#       them;
#   (c) carried into a worker AS TEXT — `fragment()` above serialises only
#       `LEAF.toString()` + `__pnaInstall.toString()`, so anything defined in an
#       enclosing scope is `undefined` in a worker and the leaf would silently
#       never apply there.
#
# The closure `SEEN` WeakSet above satisfies (c) only. It is per-INVOCATION (it
# is declared inside `__pnaInstall`'s body), so it cannot satisfy (b) and is NOT
# a substitute for this guard — it stays where it is, guarding the iframe
# accessor, which is the one place its per-invocation lifetime is correct.
#
# (b) is what forces the state to hang off an object resolved FROM `G`, and (c)
# forces the whole thing to be inline text. So the registry lives on the realm's
# own `Object` constructor, NON-ENUMERABLY, keyed per module:
#
#   * `G.Object` is per-realm, is present in every realm including a DOM-less
#     worker, and is resolved identically by two independent invocations —
#     which is exactly the trio (a)/(b)/(c) asks for;
#   * one slot holds all 11 module keys, so this REPLACES eleven names with one.
#
# HONEST BOUND, stated rather than implied: this is not invisibility. It moves
# the flag off the global sweep, but a detector that walks
# `Object.getOwnPropertyNames(Object)` still finds `__pnaRealm`. That is the
# same shape — and the same `__pna` family — as the `__pnaName` marker already
# carried on every Chromium wrapper, which this ticket scopes out on exactly
# that ground. Symbol keying was NOT chosen instead: this file's own standard
# (see the Firefox cloak note) is that the registry must not be "enumerated OR
# SWEPT FOR SYMBOLS", so a symbol would satisfy the probe's regex while
# breaching the in-tree rule it encodes.
#
# FAIL OPEN, never closed. If `defineProperty` is refused the leaf RE-RUNS
# rather than bailing — matching `fresh()`, which returns true when `WeakSet` is
# unavailable. A re-run costs a double-applied spoof; a false bail costs an
# UNMASKED realm, which is strictly worse.
def realm_guard_js(module_key: str, indent: int = 4) -> str:
    """Inline JS that returns early if ``module_key`` already ran in this realm.

    THE ONLY SOURCE OF THIS TEXT. Every leaf that needs the guard carries a
    ``*_REALM_GUARD__`` placeholder in its template and fills it from HERE in its
    builder's ``.replace()`` chain, exactly as ``realm_bootstrap_js`` is already
    consumed in those same files. Do NOT paste the emitted body into a leaf: it
    was pasted into all twelve sites once, and the copies were coupled to this
    function by nothing — editing the helper silently changed nothing that
    shipped, and editing one copy silently diverged it from eleven others. The
    structural suite in tests/test_realm_guard.py asserts every generated script
    contains ``realm_guard_js(<its key>)``, so a dropped placeholder or an
    unwired builder goes red per-site rather than becoming a leaf with no
    idempotency at all.

    Splice INSIDE the leaf (it emits a bare ``return``), AFTER the leaf's own
    preconditions and before it patches anything. That ordering matters where a
    leaf legitimately bails in some realms (canvas_ctx and measuretext bail
    without a DOM): a realm where the leaf did no work must NOT be recorded as
    covered, or a later invocation that COULD have patched it returns early
    against an empty realm. Those two therefore keep their placeholder at the
    point where the leaf is known to be doing work, not at the top of the body.

    Emitted as text with no free variables other than ``G``, so it survives the
    trip into a worker realm. ``indent`` is the leaf body's own indentation —
    voice_ext.py indents its body by 2, every other leaf by 4 — and a wrong
    value here is a real mismatch the structural suite catches, not cosmetic.
    """
    key = json.dumps(module_key)
    body = (
        "var __pnaReg = null;\n"
        "try {\n"
        "  var __pnaO = G.Object;\n"
        "  if (__pnaO) {\n"
        "    __pnaReg = __pnaO.__pnaRealm;\n"
        "    if (!__pnaReg) {\n"
        "      __pnaReg = {};\n"
        "      __pnaO.defineProperty(__pnaO, '__pnaRealm',\n"
        "                            { value: __pnaReg, configurable: true });\n"
        "    }\n"
        "  }\n"
        "} catch (e) { __pnaReg = null; }\n"
        "try {\n"
        "  if (__pnaReg) {\n"
        "    if (__pnaReg[" + key + "] === true) return;\n"
        "    __pnaReg[" + key + "] = true;\n"
        "  }\n"
        "} catch (e) {}"
    )
    return textwrap.indent(body, " " * indent)


# ---------------------------------------------------------------------------
# Per-realm VALUE slot (PS-139)
# ---------------------------------------------------------------------------
# The second consumer of the same `Object.__pnaRealm` object the guard above
# creates. It replaces the last two `__persona*` names that sat ENUMERABLE on
# the global object — and unlike the eleven booleans PS-93 removed, these two
# handed the page live session values rather than mere tool presence:
#
#   * `__personaScreenWH` -> the profile's resolved screen geometry {W, H};
#   * `__personaMtFactor` -> the measureText noise factor, i.e. the divisor
#     that INVERTS the text-metrics spoof.
#
# NO NEW GLOBAL NAME IS ADDED. The slot already ships (see realm_guard_js), so
# this is two keys inside one existing object; `getOwnPropertyNames(G)` gains
# nothing and loses two.
#
# WHY A HELPER AND NOT `__pnaReg`. The guard already resolves this realm's own
# slot into a local `__pnaReg`, and the own-realm write could have reused it.
# It does not, for two reasons: the value channels must also read ANOTHER
# realm's slot (the iframe->top crossing below), which `__pnaReg` cannot do; and
# reusing a variable the guard happens to declare would couple two leaves to the
# guard's internal spelling with nothing asserting the coupling.
#
# THE CROSSING THIS SUPPORTS, precisely. `__pnaRealm` is built per-realm off
# each realm's OWN `G.Object`, so a child reaches the top's copy the same way it
# reached `top.__personaScreenWH` before: through the `top` hop, same-origin
# only. That means:
#
#   * iframe -> top   WORKS, and is the channel both values exist for. Every
#                     realm must agree on one screen geometry and one noise
#                     factor; divergence between realms is a worse tell than
#                     the global this replaces.
#   * cross-origin    UNCHANGED, not improved — such a child could not read
#                     `top.__personaScreenWH` either. This is not a frame
#                     isolation fix and must not be read as one.
#   * worker -> top    STILL IMPOSSIBLE. `WorkerGlobalScope` has no `top`, so a
#                     worker never read either value and does not now. Nothing
#                     regresses; nothing improves. Carrying a RUNTIME-LEARNED
#                     value into a worker needs a mechanism nobody has
#                     specified — the leaf crosses as SOURCE TEXT built at
#                     new-Worker time (`fragment()` above), which by
#                     construction cannot carry a value learned later. That is
#                     tracked separately and this slice does not block on it.
#
# FAIL OPEN, exactly like the guard: every path returns null rather than
# throwing, and each caller falls back to computing the value locally. A null
# slot costs a realm re-deriving its own geometry (what happens today when
# `top` is cross-origin); a throw would cost an UNMASKED realm.
#
# HONEST BOUND, inherited not closed: a detector walking
# `getOwnPropertyNames(Object)` still finds `__pnaRealm`. PS-93 states that
# bound in place and this slice does not claim to close it.
def realm_slot_js(indent: int = 4) -> str:
    """Inline JS defining ``__pnaSlot(R, create)`` — the per-realm value slot.

    THE ONLY SOURCE OF THIS TEXT, on the same terms as ``realm_guard_js``: a
    leaf carries a ``*_REALM_SLOT__`` placeholder and fills it from HERE in its
    builder's ``.replace()`` chain. Do not paste the emitted body into a leaf.

    ``__pnaSlot(R, create)`` returns realm ``R``'s ``Object.__pnaRealm``, or
    null when it cannot be reached (no ``Object``, a cross-origin ``R``, a
    refused ``defineProperty``). ``create`` is for the realm you OWN; pass false
    when reading another realm's slot, so a read never mints an empty registry
    in a realm that has not booted.

    Keys inside the slot are a flat namespace shared with the guard's module
    keys, so a value key must not collide with one: the guard's are module names
    (``audio``, ``screen``, ``hw``, ``measuretext``, …) and the value keys are
    ``screenWH`` / ``mtFactor``. A collision would make a leaf's guard read a
    value object as its ``=== true`` boot flag — which fails OPEN (the leaf
    re-applies) rather than silently skipping, but is still a bug.

    Emitted as text with no free variables at all, so it survives the trip into
    a worker realm inside ``LEAF.toString()``. ``indent`` is the leaf body's own
    indentation, as with the guard.
    """
    body = (
        "function __pnaSlot(R, create) {\n"
        "  try {\n"
        "    var O = R && R.Object;\n"
        "    if (!O) return null;\n"
        "    var s = O.__pnaRealm;\n"
        "    if (!s && create) {\n"
        "      s = {};\n"
        "      O.defineProperty(O, '__pnaRealm', { value: s, configurable: true });\n"
        "      s = O.__pnaRealm || s;\n"
        "    }\n"
        "    return s || null;\n"
        "  } catch (e) { return null; }\n"
        "}"
    )
    return textwrap.indent(body, " " * indent)
