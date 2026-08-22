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


def realm_bootstrap_js(apply_fn_name: str) -> str:
    """Return JS that runs ``apply_fn_name`` (a leaf applyPatch(G)) in this realm
    and chains it into every realm this one can reach.

    The caller must have defined ``apply_fn_name`` already. Nothing is written to
    the global object: the leaf, its source and the dedup set stay in closure.

    ONE ``blob:``/``data:`` BRANCH, ON BOTH ENGINES — and it must stay that way.
    An earlier revision of PS-78 added a ``blob_via_import_scripts`` flag that
    gave Firefox a separate ``blob:`` branch using an ``importScripts`` shim,
    on the belief that this engine refuses a SYNCHRONOUS XHR against a ``blob:``
    URL (``NetworkError``). THAT PREMISE IS FALSE, and the flag is gone. Measured
    on firefox-20 / 151.0 through this project's own launch path, on a real
    ``https://`` origin::

        new XMLHttpRequest().open("GET", blobUrl, false).send()
            -> status 200, body read back intact

    The same reading came back from a plain playwright launch of both engines
    and from ``data:``/``about:blank``/``http`` origins. It is corroborated in
    the tree: the locale spoof at ``invisible_launch.py:504`` has shipped this
    identical sync-XHR ``blob:`` path in production all along, with live CreepJS
    evidence behind it.

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
      try { LEAF(G); } catch (e) {}

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
            if (/^blob:|^data:/i.test(s)) {
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
        try { Object.defineProperty(W, "__pnaName", { value: Orig.name }); } catch (e) {}
        try { Object.defineProperty(W, "name", { value: Orig.name }); } catch (e) {}
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
              get: function () {
                var r = d0.get.call(this);
                try {
                  var w = prop === "contentWindow" ? r : (r && r.defaultView);
                  if (w && fresh(w)) __pnaInstall(w, LEAF);
                } catch (e) {}
                return r;
              },
            });
          });
        }
      } catch (e) {}
    } catch (e) {}
  };

  try { __pnaInstall(SELF, %(fn)s); } catch (e) {}
"""
        % {"fn": apply_fn_name}
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
