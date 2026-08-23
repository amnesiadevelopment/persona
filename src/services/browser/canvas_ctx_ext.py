"""MAIN-world extension that restores Safari's legacy ``webkit-3d`` canvas
context alias on iOS profiles.

Real iOS Safari accepts THREE WebGL context names on an HTMLCanvasElement:
``webgl``, ``webgl2`` and the WebKit-only legacy alias ``webkit-3d``. Chromium
accepts the first two and has never accepted the third, so an iOS profile — which
always launches on Chromium (``process.py`` forces it for any mobile OS) —
answers ``null`` to ``canvas.getContext('webkit-3d')`` while the iPhone it claims
to be answers with a working WebGL context.

That is a one-line, zero-ambiguity detection: a script calls it, gets ``null``,
and knows the iPhone UA is false. Unlike a numeric limit being slightly off, this
is a BINARY capability with no innocent explanation for the mismatch.

WHICH WEBGL VERSION (settled from WebKit source, not inferred)
--------------------------------------------------------------
``webkit-3d`` maps to a **WebGL1** context. WebCore/html/HTMLCanvasElement.cpp
resolves it in two adjacent functions::

    bool HTMLCanvasElement::isWebGLType(const String& type)
    {
        // Retain support for the legacy "webkit-3d" name.
        return type == "webgl"_s || type == "experimental-webgl"_s
            || type == "webgl2"_s
            || type == "webkit-3d"_s;
    }

    WebGLVersion HTMLCanvasElement::toWebGLVersion(const String& type)
    {
        ASSERT(isWebGLType(type));
        if (type == "webgl2"_s)
            return WebGLVersion::WebGL2;
        return WebGLVersion::WebGL1;
    }

``toWebGLVersion`` returns WebGL2 for exactly one string and WebGL1 for every
other name it accepts, so the alias is WebGL1. The reference capture's bold
``webgl2`` is browserleaks' "this is the one I rendered with" formatting and says
nothing about the alias.

So the alias delegates to ``getContext('webgl')`` and thereby inherits the WebGL1
identity gpu_ext already installs (Apple Inc. / Apple GPU, COMMON_IOS limits,
IOS_GL1_EXTS, the bare "WebGL 1.0" version strings). It sources NO values of its
own — nothing here can drift from gpu_ext, because there is nothing here to drift.

WHY THERE IS NO OffscreenCanvas ARM (deliberate, do not "complete" it)
----------------------------------------------------------------------
``OffscreenCanvas.getContext`` does NOT carry this alias on a real device, so
adding one would manufacture a fresh impossibility rather than close a gap. The
two getContext methods have different signatures: the page one takes a free-form
string, the offscreen one takes a **WebIDL enum** that has never included the
legacy name::

    // WebCore/html/HTMLCanvasElement.idl:54
    RenderingContext? getContext(DOMString contextId, any... arguments);

    // WebCore/html/OffscreenCanvas.idl:43-49, 71
    enum OffscreenRenderingContextType { "2d", "webgl", "webgl2",
                                         "bitmaprenderer", "webgpu" };
    OffscreenRenderingContext? getContext(OffscreenRenderingContextType, ...);

WebIDL enum conversion rejects ``webkit-3d`` with a TypeError before any WebKit
code runs. Chromium's offscreen IDL declares the same five members
(offscreen_canvas_module.idl:15), so it throws there too — meaning persona's iOS
profiles ALREADY match a real iPhone on that path, and installing the alias there
would make persona the only browser on earth accepting ``webkit-3d`` on an
OffscreenCanvas.

The realm requirement is still met, because its content is "a detector must not
find the alias present in one realm and absent in another" — which is satisfied
by matching the real device in EVERY realm, not by installing the alias in every
realm. Realms with an HTMLCanvasElement (page, iframes, about:blank/srcdoc
children, nested frames) get it via realm_bootstrap_js; a worker has no
HTMLCanvasElement to be inconsistent with, and its OffscreenCanvas rejects the
alias exactly as a real iPhone's does. The patch no-ops in a worker by finding no
HTMLCanvasElement, so this needs no OS-or-realm special-casing.

NOT BECOMING ITS OWN TELL
-------------------------
``getContext`` is one of the most-inspected functions in the browser, so the
replacement must be indistinguishable from the native one by the means detectors
actually use: it carries the ``__pnaName`` marker consumed by native_ext's
Function.prototype.toString patch (a per-function ``.toString`` override is
bypassed by the ``.call`` form detectors use), copies ``name`` and ``length``
from the original, and is installed with the original property descriptor's own
writable/enumerable/configurable flags so a descriptor read or a ``for...in``
sees what the platform shows. Every non-alias call forwards untouched —
arguments and ``this`` included — so ``2d``, ``bitmaprenderer``, ``webgpu``,
unknown names returning null, the options argument, and the engine's own
one-context-per-canvas caching all keep their native behaviour.
"""

import json
import pathlib

from .worker_wrap import realm_bootstrap_js, realm_guard_js

_CONTENT_SCRIPT = r"""
(function () {
  // Patch one realm G. OS lives INSIDE this function so applyCanvasCtxPatch
  // .toString() carries it into child realms (a var in the outer IIFE would be
  // undefined there) — the same constraint gpu_ext documents for SEED/OS.
  function applyCanvasCtxPatch(G) {
   try {
    var OS = "__OS__";
    // iOS ONLY. A windows/macos/android/linux profile is claiming to be
    // CHROMIUM, and real Chromium genuinely does not support webkit-3d —
    // adding it there would manufacture a brand-new impossibility (a
    // Chrome-on-Windows profile answering a Safari-only alias) that is worse
    // than the tell being fixed, and it would hit every profile instead of the
    // iOS ones. macOS included: persona's macOS profiles present Chrome-on-
    // macOS, not Safari. The alias is Safari's, not Apple's.
    if (OS !== "ios") return;
    if (!G) return;

    // A worker realm has no HTMLCanvasElement, so there is nothing to patch and
    // nothing to be inconsistent with — its OffscreenCanvas rejects the alias
    // just as a real iPhone's does. Bailing here is the correct behaviour, not
    // a gap in realm coverage.
    var CE = G.HTMLCanvasElement;
    var proto = CE && CE.prototype;
    var orig = proto && proto.getContext;
    if (typeof orig !== "function") return;
__REALM_GUARD__

    // WebKit's legacy name, and the modern name it resolves to. WebGL1 per
    // HTMLCanvasElement::toWebGLVersion — see the module docstring.
    var ALIAS = "webkit-3d";
    var TARGET = "webgl";

    var replacement = function () {
      // Delegate EVERYTHING to the original. The only change is the first
      // argument, and only when it converts to exactly the alias.
      //
      // TWO RULES GOVERN THIS FUNCTION, and both are about being invisible to a
      // probe that instruments what it hands us rather than one that passes a
      // plain string literal:
      //
      //   1. CONVERT THE contextId EXACTLY ONCE. WebIDL performs the DOMString
      //      conversion once, so a contextId carrying a counting `toString`
      //      must see exactly one call. Reading String(arguments[0]) to make
      //      the decision and then forwarding the ORIGINAL `arguments` makes
      //      the engine convert a second time — and that fires on the NON-alias
      //      path too, i.e. on getContext('2d') and every other canvas call on
      //      the profile. That would invert this module's whole cost argument:
      //      the missing alias only matters to a script that probes for it,
      //      whereas a double conversion is present on every call, on iOS, the
      //      one profile whose story we are protecting. So convert once and
      //      pass the resulting PRIMITIVE through — the engine re-converting a
      //      string primitive is idempotent and therefore invisible.
      //
      //   2. NEVER SWALLOW AND RETRY. The delegation stays OUTSIDE any
      //      try/catch. A catch around it turns an engine throw into a return
      //      of the unaliased call, which for 'webkit-3d' is null — the exact
      //      answer the UNPATCHED profile gives, handing a detector the pre-fix
      //      tell back, plus a new one (a context name that answers on a clean
      //      call but null on a throwing one is not a shape any engine
      //      produces). It also enters the engine twice, running any side
      //      effect of context creation a second time. Errors propagate here
      //      exactly as the engine's own would.
      if (arguments.length === 0) return orig.apply(this, arguments);

      // A Symbol is the one input where String() and WebIDL DISAGREE: String()
      // yields "Symbol(...)" while the DOMString conversion throws a TypeError.
      // Converting it ourselves would swap that TypeError for a null. Forward
      // untouched and let the engine throw as it would on a real device — a
      // Symbol can never convert to the alias, so nothing is missed.
      if (typeof arguments[0] === "symbol") return orig.apply(this, arguments);

      var args = [];
      for (var i = 0; i < arguments.length; i++) args.push(arguments[i]);
      // THE one conversion. If a hostile toString throws, it propagates — which
      // is what the engine's own conversion would do.
      var id = String(args[0]);
      args[0] = (id === ALIAS) ? TARGET : id;
      // The engine caches one context per canvas, so a canvas already holding a
      // 'webgl' context returns that SAME object here — which is what makes
      // getContext('webkit-3d') and getContext('webgl') agree on one canvas, in
      // both orders, without any bookkeeping of our own.
      return orig.apply(this, args);
    };

    try {
      // Match the platform's own function identity. `name` and `length` are
      // both read by detectors. Both are COPIED from the original rather than
      // hardcoded, so they stay correct if the engine's arity ever differs from
      // what we would have assumed.
      Object.defineProperty(replacement, "name", { value: orig.name,
        writable: false, enumerable: false, configurable: true });
      Object.defineProperty(replacement, "length", { value: orig.length,
        writable: false, enumerable: false, configurable: true });
      // Mark for native_ext's Function.prototype.toString patch so
      // Function.prototype.toString.call(canvas.getContext) reads native. A
      // plain replacement.toString override is bypassed by that .call form.
      Object.defineProperty(replacement, "__pnaName", { value: orig.name });
    } catch (e) {}

    try {
      // Reinstall with the ORIGINAL descriptor's flags. A method that became an
      // own enumerable property where the platform has a non-enumerable
      // prototype method is visible to a for...in or a descriptor read.
      var d = Object.getOwnPropertyDescriptor(proto, "getContext");
      if (d && !d.get && !d.set) {
        Object.defineProperty(proto, "getContext", {
          value: replacement,
          writable: d.writable,
          enumerable: d.enumerable,
          configurable: d.configurable,
        });
      } else {
        proto.getContext = replacement;
      }
    } catch (e) {
      try { proto.getContext = replacement; } catch (e2) {}
    }
   } catch (e) {}
  }

__REALM_BOOTSTRAP__
})();
"""

_MANIFEST = {
    "manifest_version": 3,
    "name": "persona-canvas-ctx",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["canvas_ctx.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_canvas_ctx_extension(os_type: str, base_dir: str) -> str:
    """Generate the unpacked extension that gives iOS profiles Safari's legacy
    ``webkit-3d`` canvas context alias.

    The OS is baked into the emitted script and checked at runtime, so the file
    is byte-identical in shape on every platform and only its behaviour differs
    — a non-iOS profile's copy returns before touching ``getContext`` at all.
    Returns its directory.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    ot = str(os_type).lower()
    os_norm = (
        "ios" if ot in ("ios", "iphone", "ipad", "ipados")
        else "macos" if ot in ("macos", "mac", "darwin")
        else "android" if ot in ("android",)
        else "windows"
    )
    script = (
        _CONTENT_SCRIPT
        .replace("__OS__", os_norm)
        .replace("__REALM_BOOTSTRAP__", realm_bootstrap_js("applyCanvasCtxPatch"))
        .replace("__REALM_GUARD__", realm_guard_js("canvas_ctx"))
    )
    (ext_dir / "canvas_ctx.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
