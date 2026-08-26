(function(){
(function () {
  // Patch one realm G (window or a WorkerGlobalScope). Detectors read a WebGL
  // pixel hash from an OffscreenCanvas inside a worker to catch a page-only
  // spoof, so the readback noise must run in every realm — carried into workers
  // below. SEED/BUDGET live INSIDE so applyWebglPatch.toString() carries them
  // into the worker realm (a var in the outer IIFE would be undefined there).
  function applyWebglPatch(G) {
   try {
    if (!G) return;
    var __pnaReg = null;
    try {
      var __pnaO = G.Object;
      if (__pnaO) {
        __pnaReg = __pnaO.__pnaRealm;
        if (!__pnaReg) {
          __pnaReg = {};
          __pnaO.defineProperty(__pnaO, '__pnaRealm',
                                { value: __pnaReg, configurable: true });
        }
      }
    } catch (e) { __pnaReg = null; }
    try {
      if (__pnaReg) {
        if (__pnaReg["webgl"] === true) return;
        __pnaReg["webgl"] = true;
      }
    } catch (e) {}
    var SEED = 4242;
    var BUDGET = 512;

  function bit(i) {
    var h = SEED ^ (i + 0x9e3779b1);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    h = (h ^ (h >>> 16)) >>> 0;
    return (h & 1) ? 1 : -1;
  }

  var __nm = (typeof WeakMap === 'function') ? new WeakMap() : null;
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
  }

  function perturbBytes(buf) {
    // Only byte-typed pixel data (the RGBA UNSIGNED_BYTE readback path).
    if (!(buf instanceof Uint8Array) && !(buf instanceof Uint8ClampedArray)) {
      return;
    }
    // Spend the budget over the bytes that CARRY CONTENT, never over byte
    // offsets. A fixed byte stride is a function of the buffer's row geometry,
    // and the caller chooses that geometry — CreepJS's 17x42 corner has a
    // 68-byte row, so the old `i += 17` hit four columns of every row forever
    // and never once landed on a byte this guard admits. Selecting by ORDINAL
    // AMONG ELIGIBLE BYTES makes the choice depend on the IMAGE instead, which
    // no row width can alias away, and gives a sparse readback a guaranteed
    // floor rather than a ~1-hit lottery.
    //
    // ONE pass, and the budget is a HARD bound rather than an arithmetic aim.
    // The obvious way to write this — count the eligible bytes, then divide to
    // get a stride — needs a second full pass over the buffer AND overshoots:
    // `floor(eligible / BUDGET)` is 1 for every `eligible` in [BUDGET, 2*BUDGET),
    // so at 1023 eligible bytes it moves all 1023 while claiming a cap of 512.
    // Both faults are removed by never computing a stride at all.
    //
    // Instead the eligible offsets stream into a reservoir capped at BUDGET.
    // When it fills, HALF the entries are dropped and the spacing doubles —
    // which is exactly a stride of 2, then 4, then 8, discovered as the content
    // arrives instead of guessed in advance. The invariant is that `sel` always
    // holds the eligible ordinals congruent to `phase` modulo `step`, in order,
    // so decimating to every second entry IS the next power-of-two stride.
    //
    // WHICH half survives is seed-derived, so two seeds differ by WHICH bytes
    // move as well as by which direction they move. On a buffer with no more
    // than BUDGET eligible bytes `step` never leaves 1 and EVERY eligible byte
    // moves — the property that rescues the starved CreepJS readback, where all
    // 16 usable bytes of 2856 must move or the vector delivers nothing.
    //
    // The guard is the original one: skip fully transparent/black and fully
    // opaque/white edges so we don't make obviously-wrong pixels; nudge
    // mid-range bytes only. It is applied in ONE place now, so the two-pass
    // hazard of the passes disagreeing cannot arise.
    var sel = [];
    var count = 0;
    var step = 1;
    var phase = 0;
    var lvl = 0;
    var ord = 0;
    var i;
    for (i = 0; i < buf.length; i++) {
      var v = buf[i];
      if (v <= 1 || v >= 254) continue;
      if (ord % step === phase) {
        if (count === BUDGET) {
          var takeOdd = (SEED >>> (lvl & 31)) & 1;
          var w = 0;
          for (var j = takeOdd ? 1 : 0; j < count; j += 2) { sel[w++] = sel[j]; }
          count = w;
          if (takeOdd) phase += step;
          step *= 2;
          lvl++;
        }
        // Re-tested because the decimation above may have just moved this
        // ordinal off the lattice.
        if (ord % step === phase) { sel[count++] = i; }
      }
      ord++;
    }

    // Touch only the selected offsets — at most BUDGET of them, so this pass is
    // bounded by the budget and not by the buffer.
    for (var k = 0; k < count; k++) {
      var at = sel[k];
      // Keyed by byte offset, not by ordinal: the offset is stable under a
      // change of content, so the same pixel keeps the same direction.
      buf[at] = buf[at] + bit(at);
    }
  }

  function patch(proto) {
    if (!proto || !proto.readPixels) return;
    var orig = proto.readPixels;
    proto.readPixels = nativeWrap(orig, function (x, y, w, h, fmt, type, pixels) {
      var r = orig.apply(this, arguments);
      try { perturbBytes(pixels); } catch (e) {}
      return r;
    });
  }

  try { if (G.WebGLRenderingContext) patch(G.WebGLRenderingContext.prototype); } catch (e) {}
  try { if (G.WebGL2RenderingContext) patch(G.WebGL2RenderingContext.prototype); } catch (e) {}
   } catch (e) {}
  }


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
      } catch (e) {}

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
      } catch (e) {}

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
              } catch (e) {}
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
        __bcloak(W, Orig.name);
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
              get: __bcloak(function () {
                var r = d0.get.call(this);
                try {
                  var w = prop === "contentWindow" ? r : (r && r.defaultView);
                  if (w && fresh(w)) __pnaInstall(w, LEAF);
                } catch (e) {}
                return r;
              }, "get " + prop, prop),
            });
          });
        }
      } catch (e) {}
    } catch (e) {}
  };

  try { __pnaInstall(SELF, applyWebglPatch); } catch (e) {}

})();
})();