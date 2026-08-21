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
