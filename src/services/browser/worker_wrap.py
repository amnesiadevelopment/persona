"""Shared JS that carries every MAIN-world spoof patch into every reachable realm.

A content script runs in the page realm (and, with all_frames, in each frame the
browser itself creates), but NOT into realms the page builds at runtime: a Web
Worker, a fresh about:blank/srcdoc iframe, a worker spawned from inside such an
iframe, or a nested iframe. A detector (CreepJS, Pixelscan) reads a fingerprint
from one of those pristine realms and sees the real, unspoofed values — a
page/child mismatch is a hard tell (creepjs read the real GPU from an
OffscreenCanvas in a worker created inside an about:blank iframe).

Every spoof extension calls ``realm_bootstrap_js("applyPatch")``. Each REGISTERS
its leaf ``applyPatch`` into a per-realm registry (``G.__pnaBoots``) and — ONCE
per realm — installs a single self-similar bootstrap that (a) runs every
registered leaf, (b) wraps that realm's Worker/SharedWorker, and (c) wraps its
iframe accessors, so ALL leaves re-run in every worker and child frame,
recursively. A shared registry (not a per-module guard on HTMLIFrameElement) is
what lets N extensions each reach child realms: a per-module guard would let only
the first module win the single iframe getter.

Worker scheme (proven by locale_ext): blob:/data: workers are re-blobbed under
the same scheme (the site's CSP already allows that scheme, so it stays allowed)
with the bootstrap prepended; http(s) workers get an importScripts shim. Module
workers are left untouched (can't prepend to an ES module without breaking it).
"""


def realm_bootstrap_js(apply_fn_name: str) -> str:
    """Return JS that registers ``apply_fn_name`` (a leaf applyPatch(G)) into the
    per-realm registry and, once per realm, installs the shared recursive
    bootstrap, then runs it for the current realm.

    The caller must have defined ``apply_fn_name`` already.
    """
    return (
        r"""
  var SELF = (typeof self !== "undefined") ? self : this;
  try {
    // Register this module's leaf into the shared per-realm registry. The leaf
    // source is stored as text too, so the worker payload can ship every leaf.
    if (!SELF.__pnaBoots) { SELF.__pnaBoots = []; SELF.__pnaBootSrc = []; }
    SELF.__pnaBoots.push(%(fn)s);
    try { SELF.__pnaBootSrc.push("(" + %(fn)s.toString() + ")"); } catch (e) {}
  } catch (e) {}

  if (!SELF.__pnaBootInstalled) {
    SELF.__pnaBootInstalled = true;

    var __pnaBoot = function (G) {
      try {
        if (!G) return;
        // Apply every registered leaf to realm G.
        try {
          var L = G.__pnaBoots || (typeof self !== "undefined" ? self : this).__pnaBoots || [];
          for (var i = 0; i < L.length; i++) { try { L[i](G); } catch (e) {} }
        } catch (e) {}

        // Carry the whole registry into this realm's Web/Shared Workers. The
        // payload defines __pnaBoots from the stored leaf sources, then re-runs
        // the same bootstrap so the worker (and anything it spawns) is covered.
        try {
          var SRC = ((typeof self !== "undefined" ? self : this).__pnaBootSrc || []).join(",");
          var __BOOT = "(function(){try{self.__pnaBoots=[" + SRC + "];}catch(e){}" +
            "(" + __pnaBoot.toString() + ")((typeof self!=='undefined'?self:this));})();";
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

        // Carry the whole registry into this realm's same-realm about:blank /
        // srcdoc child frames on access — recursively (nested frames too). The
        // child inherits __pnaBoots by reference and re-runs the full bootstrap.
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
                    if (w && !w.__pnaBooted) {
                      w.__pnaBooted = true;
                      try { w.__pnaBoots = G.__pnaBoots; w.__pnaBootSrc = G.__pnaBootSrc; } catch (e) {}
                      __pnaBoot(w);
                    }
                  } catch (e) {}
                  return r;
                },
              });
            });
          }
        } catch (e) {}
      } catch (e) {}
    };

    try { SELF.__pnaBoot = __pnaBoot; } catch (e) {}
    SELF.__pnaBooted = true;
    __pnaBoot(SELF);
  } else {
    // Bootstrap already installed in this realm by an earlier module; just apply
    // this newly-registered leaf to the current realm (the others already ran).
    try { %(fn)s(SELF); } catch (e) {}
  }
"""
        % {"fn": apply_fn_name}
    )
