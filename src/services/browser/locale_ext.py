import json
import pathlib

from .worker_wrap import (
    chromium_leaf_cloak_js,
    realm_bootstrap_js,
    realm_guard_js,
)

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
__LOCALE_REALM_GUARD__
    var LOCALE = %LOCALE%;
    // Make our wrapped built-ins read as native in THIS realm (page or worker):
    // a masking detector (creepjs) calls Function.prototype.toString on Intl in a
    // Web Worker and, seeing our wrapper source, marks the Timezone/Intl
    // component "rejected". Every other leaf now installs the same cloak, and
    // load order between them is not guaranteed — so this one applies its own
    // and CHAINS, exactly as they do.
    //
    // ⛔ THIS BLOCK USED TO BE A SECOND, HAND-ROLLED COPY of native_ext's
    // marker-reading `patched`, pinning `__pnaName` on itself and reading
    // `this.__pnaName` off every wrapper. It is now the SHARED emitter
    // (`chromium_leaf_cloak_js`), which is the one source of that text — the
    // twelve `realm_guard` copies are the in-tree lesson about what a pasted
    // twin costs. Two behaviours it keeps and one it drops:
    //
    //   * CHAIN, don't flag-guard. The two scripts used to coordinate through
    //     `G.__pnaToStringPatched` so at most one wrapped a realm — an
    //     enumerable global under persona's own prefix that `Object.keys(window)`
    //     found in one line, in every realm. Delegating instead composes with
    //     no shared name at all, in either load order.
    //   * The patch reads as native ITSELF (`__pncMark(__pncTs, "toString")`).
    //   * DROPPED: the own-property marker. See the note beside
    //     `_CHROMIUM_LEAF_CLOAK` in worker_wrap.py — an own `__pnaName` made
    //     every wrapper read a third name under `Object.getOwnPropertyNames`,
    //     which is persona identification in one line.
__LOCALE_LEAF_CLOAK__
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
      // Read as native under Function.prototype.toString (this leaf's own cloak,
      // spliced above), so a masking detector doesn't see the wrapper source.
      // ⛔ WeakMap, not an own `__pnaName` (PS-368).
      __pncMark(W, name);
      try { Object.defineProperty(W, "name", { value: name }); } catch (e) {}
      if (Ctor.supportedLocalesOf) W.supportedLocalesOf = Ctor.supportedLocalesOf.bind(Ctor);
      if (Ctor.prototype && Ctor.prototype.resolvedOptions) {
        Ctor.prototype.resolvedOptions = _resolved(Ctor.prototype.resolvedOptions);
      }
      Intl[name] = W;
    };
    ["DateTimeFormat", "NumberFormat", "RelativeTimeFormat", "DisplayNames",
     "ListFormat", "PluralRules", "Collator", "Segmenter"].forEach(_wrap);

    // ⚠️ `_mark` is for METHODS ONLY — never for the Intl constructors above.
    //
    // `_wrap`'s W legitimately owns `prototype`: a native Intl constructor does
    // too (Intl.DateTimeFormat owns ["length","name","prototype",
    // "supportedLocalesOf"]), and a method shorthand is NOT CONSTRUCTIBLE, so
    // re-housing W would make `new Intl.DateTimeFormat()` throw. Measured, not
    // assumed. That is why the shape fix stops at the constructor boundary.
    //
    // A wrapped Date METHOD is the opposite case: native Date#toLocaleString
    // owns exactly ["length","name"], so an expression wrapper leaks
    // `prototype`/`arguments`/`caller` here with nothing to justify them.
    const _mark = function (fn, name, orig) {
      let shell;
      try {
        shell = ({ m() { return fn.apply(this, arguments); } }).m;
        // Copy arity from the ORIGINAL where we have one, so a wrapped method
        // keeps the platform's own reading rather than the wrapper's.
        if (orig) Object.defineProperty(shell, "length", { value: orig.length });
      } catch (e) { shell = fn; }
      try { Object.defineProperty(shell, "name", { value: name }); } catch (e) {}
      // ⛔ WeakMap, not an own `__pnaName` (PS-368): a native Date method owns
      // exactly ["length","name"], so a third name here was readable in one
      // line and identified persona specifically.
      return __pncMark(shell, name);
    };
    if (Dp) {
      ["toLocaleString", "toLocaleDateString", "toLocaleTimeString"].forEach(function (n) {
        const orig = Dp[n];
        if (orig) Dp[n] = _mark(function (l, o) { return orig.call(this, l || LOCALE, o); }, n, orig);
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
        }, name, orig);
      });
    }
    // Number/BigInt.toLocaleString use the host locale internally (not the JS
    // Intl.NumberFormat we wrapped) — a currency NAME leaked "доллар США".
    [G.Number, G.BigInt].forEach(function (C) {
      if (!C || !C.prototype || !C.prototype.toLocaleString) return;
      const orig = C.prototype.toLocaleString;
      C.prototype.toLocaleString = _mark(function (l, o) { return orig.call(this, l || LOCALE, o); }, "toLocaleString", orig);
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
        "__LOCALE_LEAF_CLOAK__", chromium_leaf_cloak_js(4)
    ).replace(
        "__LOCALE_REALM_BOOTSTRAP__", realm_bootstrap_js("applyLocalePatch")
    ).replace(
        "__LOCALE_REALM_GUARD__", realm_guard_js("locale")
    )
    (ext_dir / "locale.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
