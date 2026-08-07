"""Shared JS that carries a MAIN-world spoof patch into every reachable realm.

A content script runs in the page realm (and, with all_frames, in each frame the
browser itself creates), but NOT into realms the page builds at runtime: a Web
Worker, a fresh about:blank/srcdoc iframe, a worker spawned from inside such an
iframe, or a nested iframe. A detector (CreepJS, Pixelscan) reads a fingerprint
from one of those pristine realms and sees the real, unspoofed values — a
page/child mismatch is a hard tell (creepjs read the real GPU from an
OffscreenCanvas in a worker created inside an about:blank iframe).

``realm_bootstrap_js("applyPatch")`` emits a self-similar bootstrap: it applies
the leaf ``applyPatch(G)`` to a realm, then wraps that realm's Worker/SharedWorker
and iframe accessors so the SAME bootstrap re-runs in every worker and child
frame — recursively. Carrying the bootstrap (not just the leaf) is what closes
the worker-in-iframe and nested-iframe gaps: each new realm re-establishes the
full coverage, including its own children.

Worker scheme (proven by locale_ext): blob:/data: workers are re-blobbed under
the same scheme (the site's CSP already allows that scheme, so it stays allowed)
with the bootstrap prepended; http(s) workers get an importScripts shim. Module
workers are left untouched (can't prepend to an ES module without breaking it).
"""


def realm_bootstrap_js(apply_fn_name: str) -> str:
    """Return JS defining ``__pnaBoot(G)`` — apply ``apply_fn_name`` (a leaf
    applyPatch(G)) to realm G, then wrap G's Worker/SharedWorker and iframe
    getters so __pnaBoot re-runs in every worker and same-realm child frame,
    recursively — and invoke it for the current realm.

    The caller must have defined ``apply_fn_name`` already. This REPLACES the old
    separate worker_wrap_js + iframe_carry_js (each was single-layer: it carried
    only the leaf, so a worker or frame created inside a child realm stayed
    pristine).
    """
    return (
        r"""
  function __pnaBoot(G) {
    try {
      if (!G) return;
      try { %(fn)s(G); } catch (e) {}

      // Carry the whole bootstrap into this realm's Web/Shared Workers, so a
      // worker (or a worker inside a child frame) re-establishes full coverage.
      // __BOOT must carry BOTH the leaf applyPatch AND __pnaBoot — __pnaBoot's
      // source references %(fn)s by name, so shipping only __pnaBoot.toString()
      // would ReferenceError in the worker (the leaf isn't defined there).
      try {
        var __BOOT = "(function(){var " + (%(fn)s.name || "__pnaLeaf") + "=" +
          %(fn)s.toString() + ";(" + __pnaBoot.toString() +
          ")((typeof self!=='undefined'?self:this));})();";
        var __wrapWorker = function (Orig) {
          if (typeof Orig !== "function") return Orig;
          var W = function (url, options) {
            try {
              if (options && options.type === "module") {
                return Reflect.construct(Orig, [url, options], W);
              }
              var s = String(url);
              if (/^https?:/i.test(s)) {
                var body = __BOOT + "\ntry{importScripts(" + JSON.stringify(s) + ");}catch(e){}";
                var u = URL.createObjectURL(new Blob([body], { type: "application/javascript" }));
                return Reflect.construct(Orig, [u, options], W);
              }
              if (/^blob:|^data:/i.test(s)) {
                try {
                  var x = new XMLHttpRequest();
                  x.open("GET", s, false);
                  x.send();
                  if (x.status === 0 || (x.status >= 200 && x.status < 300)) {
                    var patched = __BOOT + "\n" + x.responseText;
                    var u2 = URL.createObjectURL(new Blob([patched], { type: "application/javascript" }));
                    return Reflect.construct(Orig, [u2, options], W);
                  }
                } catch (e) {}
                return Reflect.construct(Orig, [url, options], W);
              }
              return Reflect.construct(Orig, [url, options], W);
            } catch (e) { return Reflect.construct(Orig, [url, options], W); }
          };
          W.prototype = Orig.prototype;
          try { Object.defineProperty(W, "__pnaName", { value: Orig.name }); } catch (e) {}
          try { Object.defineProperty(W, "name", { value: Orig.name }); } catch (e) {}
          return W;
        };
        if (G.Worker) G.Worker = __wrapWorker(G.Worker);
        if (G.SharedWorker) G.SharedWorker = __wrapWorker(G.SharedWorker);
      } catch (e) {}

      // Carry the whole bootstrap into this realm's same-realm about:blank /
      // srcdoc child frames on access — recursively, so a nested iframe (child
      // of a child) is covered too. Each frame that HTMLIFrameElement belongs to
      // gets patched via its own prototype; guard prevents double-wrapping.
      try {
        var IF = G.HTMLIFrameElement;
        if (IF && IF.prototype && !IF.prototype.__pnaFramed) {
          IF.prototype.__pnaFramed = true;
          ["contentWindow", "contentDocument"].forEach(function (prop) {
            var d0 = Object.getOwnPropertyDescriptor(IF.prototype, prop);
            if (!d0 || !d0.get) return;
            Object.defineProperty(IF.prototype, prop, {
              configurable: true, enumerable: d0.enumerable,
              get: function () {
                var r = d0.get.call(this);
                try {
                  var w = prop === "contentWindow" ? r : (r && r.defaultView);
                  if (w && !w.__pnaBooted) { w.__pnaBooted = true; __pnaBoot(w); }
                } catch (e) {}
                return r;
              },
            });
          });
        }
      } catch (e) {}
    } catch (e) {}
  }
  var SELF = (typeof self !== "undefined") ? self : this;
  try { SELF.__pnaBooted = true; } catch (e) {}
  __pnaBoot(SELF);
"""
        % {"fn": apply_fn_name}
    )
