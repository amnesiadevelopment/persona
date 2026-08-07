import json
import pathlib

# Injected in the MAIN world at document_start. Wrapped in an IIFE so no injected
# name leaks as a page global (a page redeclaring the same const would throw and
# die — Sheets' calc worker did, see geo_ext #233).
#
# %LOCALE% is replaced with a JSON string literal at build time.
#
# Coverage: the page realm, Web/Shared Workers (content scripts don't inject
# there), AND same-realm child frames created as about:blank / srcdoc (content
# scripts don't inject there either — and creepjs/pixelscan read a "pristine"
# Intl/Date/Number out of exactly such a frame to catch a page-only patch as a
# lie, which is how the host locale "ru" / "доллар США" kept surfacing).
CONTENT_SCRIPT = r"""
(function () {
const LOCALE = %LOCALE%;

// Patch one realm G (a window or worker global). Idempotent per realm.
const _applyLocalePatch = function (LOCALE, G) {
  try {
    if (!G || G.__personaLocale) return;
    G.__personaLocale = true;
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
};

const SELF = (typeof self !== "undefined") ? self : this;
_applyLocalePatch(LOCALE, SELF);

// Carry the patch into Web/Shared Workers.
try {
  const PATCH = "(" + _applyLocalePatch.toString() + ")(" + JSON.stringify(LOCALE) +
                ", (typeof self!=='undefined'?self:this));";
  const wrapWorker = function (Orig) {
    if (typeof Orig !== "function") return Orig;
    const W = function (url, options) {
      try {
        // A worker the site built from its own blob:/data: URL runs under that
        // site's CSP (script-src). Re-wrapping it into OUR fresh blob: URL trips
        // a strict CSP and the worker silently never starts (pixelscan's scan
        // hung on exactly this). Only inject into plain http(s) worker scripts,
        // where an importScripts shim is CSP-safe; pass everything else through.
        const s = String(url);
        const isPlain = /^https?:/i.test(s);
        if (!isPlain || (options && options.type === "module")) {
          return Reflect.construct(Orig, [url, options], W);
        }
        const body = PATCH + "\ntry{importScripts(" + JSON.stringify(s) + ");}catch(e){}";
        const u = URL.createObjectURL(new Blob([body], { type: "application/javascript" }));
        return Reflect.construct(Orig, [u, options], W);
      } catch (e) { return Reflect.construct(Orig, [url, options], W); }
    };
    W.prototype = Orig.prototype;
    try { Object.defineProperty(W, "__pnaName", { value: Orig.name }); } catch (e) {}
    try { Object.defineProperty(W, "name", { value: Orig.name }); } catch (e) {}
    return W;
  };
  if (SELF.Worker) SELF.Worker = wrapWorker(SELF.Worker);
  if (SELF.SharedWorker) SELF.SharedWorker = wrapWorker(SELF.SharedWorker);
} catch (e) {}

// Patch same-realm child frames (about:blank / srcdoc) on access — content
// scripts don't inject there, so a scanner reading a fresh iframe's Intl would
// otherwise see the host locale. (creepjs additionally reads a pristine realm
// via a path JS can't intercept before its inline script runs — that residual
// is an fp-chromium ICU-default-locale gap, out of a content script's reach.)
try {
  if (typeof HTMLIFrameElement !== "undefined") {
    const proto = HTMLIFrameElement.prototype;
    ["contentWindow", "contentDocument"].forEach(function (prop) {
      const d = Object.getOwnPropertyDescriptor(proto, prop);
      if (!d || !d.get) return;
      Object.defineProperty(proto, prop, {
        configurable: true,
        enumerable: d.enumerable,
        get: function () {
          const r = d.get.call(this);
          try {
            const w = prop === "contentWindow" ? r : (r && r.defaultView);
            if (w) _applyLocalePatch(LOCALE, w);
          } catch (e) {}
          return r;
        },
      });
    });
  }
} catch (e) {}
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
    js = CONTENT_SCRIPT.replace("%LOCALE%", json.dumps(locale))
    (ext_dir / "locale.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
