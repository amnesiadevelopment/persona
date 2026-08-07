"""Shared JS that carries a MAIN-world spoof patch into Web/Shared Workers.

Content scripts run only in the page realm; a Web Worker gets a pristine
WebGL/canvas/audio/Intl surface, so a detector (CreepJS, Pixelscan) that reads a
fingerprint from an OffscreenCanvas or Intl inside a worker sees the real,
unspoofed values — a page/worker mismatch is a hard tell. Each spoof extension
defines an ``applyPatch(G)`` that patches one realm, then calls
``worker_wrap_js("applyPatch")`` to wrap Worker/SharedWorker so the same patch
runs in every worker it spawns.

Scheme (proven by locale_ext): blob:/data: workers are re-blobbed under the same
scheme (the site's CSP already allows that scheme, so it stays allowed) with the
patch prepended; http(s) workers get an importScripts shim. Module workers are
left untouched (can't prepend to an ES module without breaking it).
"""


def worker_wrap_js(apply_fn_name: str) -> str:
    """Return JS that wraps self.Worker/SharedWorker so ``apply_fn_name`` (an
    in-scope function taking the worker global) also runs inside spawned workers.
    The caller must have defined ``apply_fn_name`` and a ``SELF`` var already.
    """
    return (
        r"""
  try {
    var __PATCH = "(" + %(fn)s.toString() + ")((typeof self!=='undefined'?self:this));";
    var __wrapWorker = function (Orig) {
      if (typeof Orig !== "function") return Orig;
      var W = function (url, options) {
        try {
          if (options && options.type === "module") {
            return Reflect.construct(Orig, [url, options], W);
          }
          var s = String(url);
          if (/^https?:/i.test(s)) {
            var body = __PATCH + "\ntry{importScripts(" + JSON.stringify(s) + ");}catch(e){}";
            var u = URL.createObjectURL(new Blob([body], { type: "application/javascript" }));
            return Reflect.construct(Orig, [u, options], W);
          }
          if (/^blob:|^data:/i.test(s)) {
            try {
              var x = new XMLHttpRequest();
              x.open("GET", s, false);
              x.send();
              if (x.status === 0 || (x.status >= 200 && x.status < 300)) {
                var patched = __PATCH + "\n" + x.responseText;
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
    if (SELF.Worker) SELF.Worker = __wrapWorker(SELF.Worker);
    if (SELF.SharedWorker) SELF.SharedWorker = __wrapWorker(SELF.SharedWorker);
  } catch (e) {}
"""
        % {"fn": apply_fn_name}
    )


def iframe_carry_js(apply_fn_name: str) -> str:
    """Return JS that carries ``apply_fn_name`` (an applyPatch(G) taking a realm
    global) into same-realm about:blank / srcdoc child frames on access.

    A content script doesn't inject into a freshly-created about:blank/srcdoc
    frame, so a detector reading a fingerprint from a pristine iframe realm gets
    the real, unspoofed values — a page/iframe mismatch is a hard tell. On a VM
    (SwiftShader) the real and spoofed values coincide so it hides; on real
    hardware the gap surfaces (creepjs read the real GPU from an iframe).
    """
    return (
        r"""
  try {
    if (typeof HTMLIFrameElement !== "undefined") {
      var __ifp = HTMLIFrameElement.prototype;
      ["contentWindow", "contentDocument"].forEach(function (prop) {
        var d0 = Object.getOwnPropertyDescriptor(__ifp, prop);
        if (!d0 || !d0.get) return;
        Object.defineProperty(__ifp, prop, {
          configurable: true, enumerable: d0.enumerable,
          get: function () {
            var r = d0.get.call(this);
            try {
              var w = prop === "contentWindow" ? r : (r && r.defaultView);
              if (w) %(fn)s(w);
            } catch (e) {}
            return r;
          },
        });
      });
    }
  } catch (e) {}
"""
        % {"fn": apply_fn_name}
    )
