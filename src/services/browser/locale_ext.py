import json
import pathlib

from .worker_wrap import realm_bootstrap_js

# Injected in the MAIN world at document_start. Wrapped in an IIFE so no injected
# name leaks as a page global (a page redeclaring the same const would throw and
# die — Sheets' calc worker did, see geo_ext #233).
#
# %LOCALE% is replaced with a JSON string literal at build time.
#
# Coverage rides the shared recursive registry (realm_bootstrap_js): the page
# realm, Web/Shared Workers, AND same-realm child frames (about:blank / srcdoc)
# — recursively, so a NESTED iframe (grandchild) is covered too. creepjs/
# pixelscan read a "pristine" Intl/Date/Number out of exactly such a frame to
# catch a page-only patch as a lie, which is how the host locale "ru" /
# "доллар США" kept surfacing.
CONTENT_SCRIPT = r"""
(function () {
// Patch one realm G (a window or worker global). Idempotent per realm. LOCALE
// lives INSIDE so applyLocalePatch.toString() carries it into every realm the
// shared registry re-runs it in (a var in the outer IIFE would be undefined
// there).
function applyLocalePatch(G) {
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
        if (__pnaReg["locale"] === true) return;
        __pnaReg["locale"] = true;
      }
    } catch (e) {}
    var LOCALE = %LOCALE%;
    // Make our wrapped built-ins read as native in THIS realm (page or worker):
    // a masking detector (creepjs) calls Function.prototype.toString on Intl in a
    // Web Worker and, seeing our wrapper source, marks the Timezone/Intl
    // component "rejected". native_ext also patches this per realm via the shared
    // registry, but load order between the two leaves isn't guaranteed — so
    // re-apply the same __pnaName-aware toString here.
    //
    // CHAIN onto whatever is installed; do NOT guard on a shared global. The two
    // scripts used to coordinate through `G.__pnaToStringPatched` so that at most
    // one wrapped a realm — an enumerable global under persona's own prefix,
    // which `Object.keys(window)` found in one line in every realm. Delegating to
    // `_ots` (the engine's toString, or native_ext's patch) makes the two compose
    // with no shared name, and keeps the property the flag protected: whichever
    // patch ends up outermost answers a `__pnaName` hit itself and never reaches
    // the one below, so a marked wrapper renders the native form EXACTLY once, in
    // either load order. See native_ext.py's applyNativePatch and
    // worker_wrap.py:28-32 for the same idiom.
    try {
      const FP = G.Function && G.Function.prototype;
      if (FP) {
        const _ots = FP.toString;
        const _pts = function () {
          try { const n = this && this.__pnaName;
            if (typeof n === "string") return "function " + n + "() { [native code] }";
          } catch (e) {}
          return _ots.apply(this, arguments);
        };
        try { Object.defineProperty(_pts, "__pnaName", { value: "toString" }); } catch (e) {}
        try { Object.defineProperty(_pts, "name", { value: "toString" }); } catch (e) {}
        FP.toString = _pts;
      }
    } catch (e) {}
    const Intl = G.Intl, Dp = G.Date && G.Date.prototype;
    if (!Intl) return;
    const _resolved = function (orig) {
      return function () { const r = orig.apply(this, arguments); r.locale = LOCALE; return r; };
    };
    const DTF = Intl.DateTimeFormat;
    const _wrap = function (name) {
      const Ctor = Intl[name];
      if (!Ctor) return;
      const W = function (locales, options) {
        return Reflect.construct(Ctor, [locales || LOCALE, options], W);
      };
      W.prototype = Ctor.prototype;
      // Read as native under Function.prototype.toString (native_ext patch), so
      // a masking detector doesn't see the wrapper source.
      try { Object.defineProperty(W, "__pnaName", { value: name }); } catch (e) {}
      try { Object.defineProperty(W, "name", { value: name }); } catch (e) {}
      if (Ctor.supportedLocalesOf) W.supportedLocalesOf = Ctor.supportedLocalesOf.bind(Ctor);
      if (Ctor.prototype && Ctor.prototype.resolvedOptions) {
        Ctor.prototype.resolvedOptions = _resolved(Ctor.prototype.resolvedOptions);
      }
      Intl[name] = W;
    };
    ["DateTimeFormat", "NumberFormat", "RelativeTimeFormat", "DisplayNames",
     "ListFormat", "PluralRules", "Collator", "Segmenter"].forEach(_wrap);

    const _mark = function (fn, name) {
      try { Object.defineProperty(fn, "__pnaName", { value: name }); } catch (e) {}
      try { Object.defineProperty(fn, "name", { value: name }); } catch (e) {}
      return fn;
    };
    if (Dp) {
      ["toLocaleString", "toLocaleDateString", "toLocaleTimeString"].forEach(function (n) {
        const orig = Dp[n];
        if (orig) Dp[n] = _mark(function (l, o) { return orig.call(this, l || LOCALE, o); }, n);
      });
      // Date.toString / toTimeString render the tz NAME in the host locale; the
      // Intl overrides don't touch it. Re-render the suffix in LOCALE.
      const _tzName = function (d) {
        try {
          const parts = new DTF(LOCALE, { timeZoneName: "long" }).formatToParts(d);
          const p = parts.find(function (x) { return x.type === "timeZoneName"; });
          return p ? p.value : null;
        } catch (e) { return null; }
      };
      ["toString", "toTimeString"].forEach(function (name) {
        const orig = Dp[name];
        if (!orig) return;
        Dp[name] = _mark(function () {
          let s = orig.call(this);
          const tz = _tzName(this);
          if (tz && /\([^)]*\)\s*$/.test(s)) s = s.replace(/\([^)]*\)\s*$/, "(" + tz + ")");
          return s;
        }, name);
      });
    }
    // Number/BigInt.toLocaleString use the host locale internally (not the JS
    // Intl.NumberFormat we wrapped) — a currency NAME leaked "доллар США".
    [G.Number, G.BigInt].forEach(function (C) {
      if (!C || !C.prototype || !C.prototype.toLocaleString) return;
      const orig = C.prototype.toLocaleString;
      C.prototype.toLocaleString = _mark(function (l, o) { return orig.call(this, l || LOCALE, o); }, "toLocaleString");
    });
  } catch (e) {}
}
__LOCALE_REALM_BOOTSTRAP__
})();
"""

MANIFEST = {
    "manifest_version": 3,
    "name": "persona-locale",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["locale.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_locale_extension(locale: str, base_dir: str) -> str:
    """Generate an unpacked extension that pins Intl/Date/Number locale to
    `locale` in the page, Web Workers, and same-realm about:blank/srcdoc child
    frames — so date/number/display formatting matches navigator.language and the
    proxy region everywhere a scanner (creepjs/pixelscan) can read it.
    fingerprint-chromium leaves the Intl default at the host locale regardless of
    --lang; this closes that gap."""
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    js = CONTENT_SCRIPT.replace("%LOCALE%", json.dumps(locale)).replace(
        "__LOCALE_REALM_BOOTSTRAP__", realm_bootstrap_js("applyLocalePatch")
    )
    (ext_dir / "locale.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
