"""Launch the invisible_playwright (patched Firefox 150) engine for a profile.

A Popen-compatible handle that runs the browser in a forked child (on Linux,
to dodge the flet-AppImage's embedded Python) or a plain subprocess elsewhere,
keeps the window open until the user closes it, and reports readiness on a pipe
— the same shape as the chromium launcher so spawn_browser can treat them alike.
"""

import json
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import threading
import time

from ...core import platform as _platform
from ...core.logging import get_logger
# The engine install/download subsystem lives in engine_install.py. Every name
# it owns is re-exported here so existing `invisible_launch.<name>` imports and
# call sites — including the stay-behind launcher code that calls them as bare
# globals — keep resolving unchanged.
from .engine_install import (  # noqa: F401
    _INSTALL_MARKER,
    _WHOLE_BUILD_BYTES,
    _binary_path_override,
    _build_is_whole,
    _download_invisible,
    _engine_in_use,
    _ensure_firefox_policies,
    _extract_as,
    _invisible_binary_path,
    _prune_old_engine_builds,
    _resumable_download,
    active_build,
    ensure_invisible_installed,
    install_engine_build,
    installed_builds,
    installed_version,
    is_invisible_installed,
    pinned_build,
    prune_superseded_builds,
    resume_engine_updates,
    revert_to_previous_build,
    rollback_target,
    # NOTE: the SETTER is re-exported, never `_in_use_provider` itself — a
    # `from ... import` binds a name by VALUE, so a re-exported variable would
    # be a stale copy that the setter's rebind in engine_install never reaches.
    # Pruning reads its own module global, so going through set_in_use_provider
    # is what makes the wiring visible to it.
    set_in_use_provider,
)
from .env_policy import scrub_current_process_environ
from .firefox_bookmarks import places_ready

logger = get_logger("browser.invisible")


# The FF engine speaks juggler, not CDP, so the MCP browser tools can't attach
# over a debugging port the way they do for chromium. A running FF session
# publishes an eval callable here (running JS on its live page through the
# thread-affine worker); MCP looks it up by profile name for firefox profiles.
_ff_eval_registry: "dict[str, object]" = {}


def register_ff_eval(name: str, fn) -> None:
    if name:
        _ff_eval_registry[name] = fn


def unregister_ff_eval(name: str) -> None:
    _ff_eval_registry.pop(name, None)


def get_ff_eval(name: str):
    return _ff_eval_registry.get(name)


def _proxy_dict(proxy_url: str):
    """Turn a 'socks5://user:pass@host:port' url into invisible_playwright's
    proxy dict. invisible_playwright does SOCKS5-with-auth natively, so no
    local bridge is needed (unlike Camoufox)."""
    if not proxy_url:
        return None
    m = re.match(r"socks5://(?:([^:]+):([^@]+)@)?(.+)", proxy_url)
    if not m:
        return {"server": proxy_url}
    user, pw, hostport = m.group(1), m.group(2), m.group(3)
    d = {"server": f"socks5://{hostport}"}
    if user:
        d["username"] = user
        d["password"] = pw
    return d


def _system_dpr() -> float:
    """The host display's scale factor (1.0 at 100%, 1.5 at 150%, 2.0 at 200%).

    Drives the Firefox render scale (layout.css.devPixelsPerPx) so a chosen
    resolution renders at the host's real scale — readable on a HiDPI display
    instead of physically tiny (#167) — and the initial-window-size seed. Read
    per-OS: GetDpiForSystem on Windows, the max NSScreen backingScaleFactor on
    macOS (2.0 on Retina), and the GDK/Wayland/Xft scale on Linux. Clamped to a
    sane desktop range so a weird reading can't produce an unusable window; a
    failed read falls back to 1.0."""
    if _platform.IS_WINDOWS:
        return _clamp_dpr(_windows_dpr())
    if _platform.IS_MACOS:
        return _clamp_dpr(_macos_dpr())
    if _platform.IS_LINUX:
        return _clamp_dpr(_linux_dpr())
    return 1.0


def _clamp_dpr(scale: float) -> float:
    """Keep a scale reading in a sane desktop range; 0/None → 1.0."""
    if not scale or scale <= 0:
        return 1.0
    return max(1.0, min(3.0, round(scale, 2)))


def _windows_dpr() -> float:
    try:
        import ctypes

        # Per-monitor DPI awareness so GetDpiForSystem returns the real scale.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return dpi / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def _macos_dpr() -> float:
    """The primary display's backing scale (2.0 on Retina). Prefers AppKit's
    NSScreen.backingScaleFactor via PyObjC when present; otherwise reads the
    active display mode's pixel-vs-point ratio through Quartz, which is in the
    stdlib-adjacent Core Graphics framework Python can reach with ctypes. Falls
    back to 2.0 — a Retina Mac tiny-rendering at 1.0 is the exact #167 failure,
    and modern Macs are overwhelmingly Retina, so 2.0 is the safer default than
    1.0 when the read fails."""
    try:
        from AppKit import NSScreen  # type: ignore

        screens = NSScreen.screens()
        if screens:
            return max(float(s.backingScaleFactor()) for s in screens)
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.util

        cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
        cg.CGDisplayCopyDisplayMode.argtypes = [ctypes.c_uint32]
        cg.CGDisplayModeGetPixelWidth.restype = ctypes.c_size_t
        cg.CGDisplayModeGetPixelWidth.argtypes = [ctypes.c_void_p]
        cg.CGDisplayModeGetWidth.restype = ctypes.c_size_t
        cg.CGDisplayModeGetWidth.argtypes = [ctypes.c_void_p]
        cg.CGDisplayModeRelease.argtypes = [ctypes.c_void_p]
        did = cg.CGMainDisplayID()
        mode = cg.CGDisplayCopyDisplayMode(did)
        if mode:
            try:
                px = cg.CGDisplayModeGetPixelWidth(mode)
                pt = cg.CGDisplayModeGetWidth(mode)
            finally:
                cg.CGDisplayModeRelease(mode)
            if pt:
                return px / pt
    except Exception:
        pass
    return 2.0  # Retina default — 1.0 would render tiny (#167)


def _linux_dpr() -> float:
    """The desktop's scale factor. GDK_SCALE / QT_SCALE_FACTOR are the explicit
    per-session overrides; otherwise GDK's monitor scale (Wayland's wl_output
    scale, or X's Xft.dpi/96) via a short introspection call. Only integer
    Wayland scales are exposed by wl_output, but GDK reports fractional Xft/
    logical scales too. Falls back to 1.0 (a 100% Linux desktop, the common
    case)."""
    for var in ("GDK_SCALE", "QT_SCALE_FACTOR"):
        val = os.environ.get(var)
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    try:
        import gi  # type: ignore

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # type: ignore

        display = Gdk.Display.get_default()
        if display is not None:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            if monitor is not None:
                sf = monitor.get_scale_factor()
                if sf:
                    return float(sf)
    except Exception:
        pass
    xft = os.environ.get("XFT_DPI")
    if xft:
        try:
            return float(xft) / 96.0
        except ValueError:
            pass
    return 1.0


def _context_overrides_for(w: int, h: int) -> dict:
    """Playwright context kwargs that decouple the spoofed screen (the
    fingerprint) from the physical window — the chromium model.

    The `screen` reports the CHOSEN resolution the user picked (the anti-detect
    value — this already works: pin -> zoom.stealth.screen.* -> JS screen.*).
    `no_viewport` stops Playwright from fixing the content size, so the page
    follows the NATIVE OS window: freely draggable/resizable, maximizes with no
    skew, exactly like a normal browser. Any fixed viewport couples the window
    to a chosen size — viewport = work_area opened the window across the whole
    4K monitor and rendered the page misscaled."""
    return {
        "screen": {"width": w, "height": h},
        "no_viewport": True,
    }


def _with_context_overrides(InvisiblePlaywright, overrides: dict):
    """A subclass whose context kwargs overlay `overrides` on the engine's own.

    The overlay rides on the launch's OWN class, never on InvisiblePlaywright
    itself: on the Windows/macOS thread path several launches share one
    process, so patching the engine class races — the last patch wins for any
    launch still inside __enter__, handing a profile the WRONG viewport/screen,
    and the patch would stay on for launches that chose no resolution at all.
    A per-launch subclass gives each launch its own overlay with nothing shared
    to race on or restore."""
    ov = dict(overrides)

    class _WithOverrides(InvisiblePlaywright):
        def _default_context_kwargs(self):
            kw = super()._default_context_kwargs()
            kw.update(ov)
            # The engine's own kwargs carry a fixed viewport and a
            # device_scale_factor; a viewport defeats no_viewport, and
            # Playwright rejects device_scale_factor with a null viewport.
            kw.pop("viewport", None)
            kw.pop("device_scale_factor", None)
            return kw

    return _WithOverrides


def _native_cloak_js() -> str:
    """JS prelude that makes every function an init script installs read as a
    native built-in. The Firefox counterpart of native_ext.py.

    native_ext.py is a Chromium MV3 extension (world: MAIN), loaded only from the
    Chromium launch path — Firefox launches through this module and loads no
    persona extension, so the cloak could not reach it by construction. Without it
    a page reads `Intl.DateTimeFormat.name === "Wrapped"` or stringifies any
    wrapper and sees injected source: a one-line, zero-false-positive masking
    tell.

    The native form is SPIDERMONKEY's, NOT native_ext.py's. That file (and
    locale_ext.py / geo_ext.py) emits V8's one-line `function x() { [native code]
    }`, which is correct there because all three are Chromium extensions. This is
    the first time the cloak crosses into Firefox, and the template does not
    survive the crossing — SpiderMonkey prints three lines with a four-space
    indent. Captured from a clean Firefox 151 with no init scripts at all:

        Array.prototype.map -> "function map() {\\n    [native code]\\n}"

    Emitting V8's form here would leave a detector one line better off than the
    `.name === "Wrapped"` tell this replaces, over a WIDER surface — every
    override, since every override is cloaked:

        Intl.DateTimeFormat.toString().replace("DateTimeFormat","map")
            !== Array.prototype.map.toString()

    A native ACCESSOR diverges a second way: its `.name` carries the `get `
    prefix but its SOURCE TEXT does not — `get language` stringifies as `function
    language() {...}`. So __cloak takes the stringified name separately from the
    pinned `.name`; the accessor call sites pass the bare property name.

    Two deliberate improvements on the Chromium marker:

    * the wrapper -> native-name mapping lives in a closure-scoped WeakMap, not in
      an own `__pnaName` property, so NO own property is added to any wrapper and
      the internal state is invisible to enumeration and to a symbol sweep;
    * the Function.prototype.toString patch CHAINS onto whatever is already
      installed instead of guarding on a global flag, so two independently
      injected scripts compose with no shared global name between them.

    The patch itself is cloaked as "toString" because a detector stringifies
    Function.prototype.toString to catch exactly this trick (native_ext.py:44-47).

    The text uses double quotes only and contains no newline and no backslash, so
    the SAME string is valid both here in the page realm and inlined inside the
    single-quoted worker-payload literal below — which is why the newline in the
    native form is built with String.fromCharCode(10) rather than written as an
    escape: a `\\n` would be consumed by the OUTER literal when the prelude is
    inlined, putting a raw newline inside a double-quoted string in the worker
    source and making it a SyntaxError. Parens and braces are balanced — see the
    balanced-count assertions in tests/test_ff_language_override.py."""
    return (
        # wrapper -> the name its SOURCE TEXT must carry (no `get ` prefix for an
        # accessor). WeakMap, so a wrapper that is dropped is collectable and the
        # page cannot enumerate the registry.
        "var __nm=new WeakMap();"
        # SpiderMonkey's shape: three lines, four-space indent. See the docstring
        # — this is deliberately NOT native_ext.py's V8 one-liner.
        "var __nl=String.fromCharCode(10);"
        "var __nat=function(n){"
        'return "function "+(n||"")+"() {"+__nl+"    [native code]"+__nl+"}";};'
        # Pin .name to the original's (non-enumerable + configurable, exactly the
        # descriptor a native function's own `name` carries) and record the name
        # to STRINGIFY as, which is `s` when it differs from `.name` (accessors).
        "var __cloak=function(f,n,s){try{__nm.set(f,s===undefined?n:s);"
        'Object.defineProperty(f,"name",{value:n,configurable:true});}'
        "catch(e){}return f;};"
        # Chain, don't flag-guard: __pts is whoever patched before us (native
        # toString on the first script to run in this realm).
        "var __pts=Function.prototype.toString;"
        'var __ts=function(){"use strict";'
        # `this` is the function being stringified. Strict mode so a primitive
        # `this` stays primitive and still reaches the original for its TypeError.
        'try{var n=__nm.get(this);if(typeof n==="string")return __nat(n);}catch(e){}'
        "return __pts.apply(this,arguments);};"
        '__cloak(__ts,"toString");'
        "try{Function.prototype.toString=__ts;}catch(e){}"
    )


def _outer_size_override_script() -> str:
    """JS that pins window.outerWidth/outerHeight to the real window (inner +
    chrome), not the spoofed screen.

    With the screen spoofed to a big resolution but the window physically small,
    Firefox reports outerWidth == the spoofed screen width — larger than
    innerWidth on a small window, an inner<outer==screen mismatch no real
    un-maximized window shows. Deriving outer from the live inner size keeps
    outer ≈ inner + chrome (both below screen), which is what a normal window
    looks like. Chrome offsets match the engine's own (_CHROME_W/_CHROME_H)."""
    return (
        "(() => {" + _native_cloak_js() +
        # The getter is what a page reaches through
        # Object.getOwnPropertyDescriptor(window,'outerWidth').get — cloak it as
        # the real accessor reads: .name "get outerWidth", source text
        # `function outerWidth() {...}` (SpiderMonkey drops the prefix there).
        "const def=(o,k,v)=>{try{Object.defineProperty(o,k,"
        "{get:__cloak(()=>v,'get '+k,k),configurable:true})}catch(e){}};"
        "def(window,'outerWidth', window.innerWidth + 14);"
        "def(window,'outerHeight', window.innerHeight + 91);"
        "})();"
    )


def _language_override_script(locale: str) -> str:
    """JS that pins navigator.language/languages to `locale`.

    firefox-17 applies the locale to the Accept-Language HEADER (through
    intl.accept_languages, built from the Playwright locale) but NOT to
    navigator.language — that reports the host OS locale instead (uk-UA on a
    Ukrainian Windows even behind a US proxy). Header en-US + JS uk-UA is an
    internal contradiction a scanner flags as masking. Pin the JS getters to the
    SAME locale the header already carries so the two agree. languages is
    [locale, base] (e.g. ["en-US","en"]) to mirror the q-valued header. Empty
    locale is a no-op — nothing to pin."""
    if not locale:
        return ""
    base = locale.split("-", 1)[0]
    langs = [locale] if base == locale else [locale, base]
    langs_js = json.dumps(langs)
    loc_js = json.dumps(locale)
    return (
        "(() => {" + _native_cloak_js() +
        "const L=" + loc_js + ",LS=" + langs_js + ";"
        "const def=(k,v)=>{try{Object.defineProperty(Navigator.prototype,k,"
        "{get:__cloak(()=>v,'get '+k,k),configurable:true})}catch(e){}};"
        "def('language', L);"
        "def('languages', Object.freeze(LS.slice()));"
        # firefox-17 also leaks the host locale through the Intl API and Date's
        # locale-aware formatting (pixelscan's "Internationalization API" reads
        # Intl.DateTimeFormat().resolvedOptions().locale; Date.toString renders the
        # timezone name in the host locale). navigator.language alone isn't enough —
        # force every Intl formatter and Date's default locale to L so a scanner
        # sees one consistent locale.
        "try{"
        # __om is the re-wrap guard that Wrapped.wrapped used to be. That was an
        # ENUMERABLE own property that handed the page the real constructor —
        # Object.keys(Intl.DateTimeFormat) was ["wrapped","supportedLocalesOf"]
        # (real: []) and one documented read recovered the host's true value. A
        # closure WeakMap keeps the guard and exposes nothing. Scope is this
        # script's own execution, which is the whole guard: add_init_script runs
        # once per document and each document is a fresh realm, so the repeat this
        # guards is forceLocale seeing a ctor it already wrapped.
        "const __om=new WeakMap();"
        "const forceLocale=(ctor)=>{if(!ctor)return;"
        "const Orig=__om.get(ctor)||ctor;"
        "const Wrapped=function(locales,options){"
        "const l=(locales===undefined||locales===null||"
        "(Array.isArray(locales)&&locales.length===0))?L:locales;"
        "return new.target?Reflect.construct(Orig,[l,options],new.target)"
        ":Orig(l,options);};"
        "Wrapped.prototype=Orig.prototype;"
        "__om.set(Wrapped,Orig);"
        "__cloak(Wrapped,Orig.name);"
        # defineProperty, not assignment: an assignment creates an ENUMERABLE own
        # property, which is what put "supportedLocalesOf" into Object.keys. The
        # descriptor below is the one the native method carries.
        "if(Orig.supportedLocalesOf){"
        "const slo=__cloak(Orig.supportedLocalesOf.bind(Orig),"
        "Orig.supportedLocalesOf.name);"
        "try{Object.defineProperty(Wrapped,'supportedLocalesOf',"
        "{value:slo,writable:true,configurable:true});}catch(e){}}"
        "const ro=Orig.prototype&&Orig.prototype.resolvedOptions;"
        "if(ro){Orig.prototype.resolvedOptions=__cloak(function(){"
        "const o=ro.call(this);o.locale=L;return o;},ro.name);}"
        "return Wrapped;};"
        "['DateTimeFormat','NumberFormat','RelativeTimeFormat','Collator',"
        "'PluralRules','ListFormat','DisplayNames','Segmenter'].forEach(k=>{"
        "if(Intl[k]){const w=forceLocale(Intl[k]);if(w)Intl[k]=w;}});"
        # Date locale-aware methods default to L when no locale is passed.
        "const patchDate=(name)=>{const orig=Date.prototype[name];if(!orig)return;"
        "Date.prototype[name]=__cloak(function(locales,options){"
        "return orig.call(this,locales===undefined?L:locales,options);},orig.name);};"
        "['toLocaleString','toLocaleDateString','toLocaleTimeString']"
        ".forEach(patchDate);"
        # Date.prototype.toString/toTimeString/toDateString render the timezone
        # name in the host OS locale ("(за східноєвропейським…)" on a Ukrainian
        # host) — pixelscan's "Time from JS" reads exactly this. They bypass Intl,
        # so rebuild the zone suffix with an en-US long-timezone formatter.
        "const OP=String.fromCharCode(40),CP=String.fromCharCode(41);"
        "const zoneName=(d)=>{try{const p=new Intl.DateTimeFormat(L,"
        "{timeZoneName:'long'}).formatToParts(d);"
        "const z=p.find(x=>x.type==='timeZoneName');return z?z.value:'';}"
        "catch(e){return '';}};"
        "const reZone=(s,d)=>{if(isNaN(d.getTime()))return s;"
        "const i=s.indexOf(' '+OP);const zn=zoneName(d);"
        "return i<0||!zn?s:s.slice(0,i)+' '+OP+zn+CP;};"
        "const oTS=Date.prototype.toString,oTTS=Date.prototype.toTimeString;"
        "Date.prototype.toString=__cloak(function(){"
        "return reZone(oTS.call(this),this);},oTS.name);"
        "Date.prototype.toTimeString=__cloak(function(){"
        "return reZone(oTTS.call(this),this);},oTTS.name);"
        # Number/BigInt.toLocaleString use the host ICU locale internally (not the
        # wrapped Intl.NumberFormat), so a currency name leaked in the host locale
        # — creepjs's lang/timezone check read "1 US dollar" (en-US) under a pl-PL
        # identity and flagged the mismatch. Default them to L too.
        "[Number,typeof BigInt!=='undefined'?BigInt:null].forEach(function(C){"
        "if(!C||!C.prototype||!C.prototype.toLocaleString)return;"
        "const o=C.prototype.toLocaleString;"
        "C.prototype.toLocaleString=__cloak(function(l,opt){"
        "return o.call(this,l===undefined?L:l,opt);},o.name);});"
        # Web Workers get a fresh Intl at the host locale (add_init_script only
        # runs in the page, not workers) — creepjs reads currency/list from a blob
        # worker and saw "1 US dollar"/en. Carry a compact locale patch into
        # blob:/data: workers by reading the original source, prepending the patch,
        # and re-blobbing under the same scheme (the site's CSP already allows
        # blob: workers). http(s) workers get an importScripts shim.
        "try{"
        # The worker is a SEPARATE realm with its own Function.prototype, so the
        # page-realm cloak above cannot reach it — the payload carries its own
        # inlined copy. _native_cloak_js() is double-quoted and newline-free by
        # construction, so it drops straight into this single-quoted literal.
        "const WP='(function(L){try{" + _native_cloak_js() +
        "var wrap=function(n){var C=Intl[n];if(!C)return;var W=function(a,o){"
        "return Reflect.construct(C,[a||L,o],W);};W.prototype=C.prototype;"
        "__cloak(W,C.name);"
        "if(C.supportedLocalesOf){var s=__cloak(C.supportedLocalesOf.bind(C),"
        "C.supportedLocalesOf.name);"
        "try{Object.defineProperty(W,\"supportedLocalesOf\","
        "{value:s,writable:true,configurable:true});}catch(e){}}"
        "Intl[n]=W;};"
        "[\"NumberFormat\",\"DateTimeFormat\",\"ListFormat\",\"RelativeTimeFormat\","
        "\"DisplayNames\",\"PluralRules\",\"Collator\"].forEach(wrap);"
        "[Number,typeof BigInt!==\"undefined\"?BigInt:null].forEach(function(C){"
        "if(!C||!C.prototype||!C.prototype.toLocaleString)return;"
        "var o=C.prototype.toLocaleString;C.prototype.toLocaleString=__cloak("
        "function(l,opt){"
        "return o.call(this,l===undefined?L:l,opt);},o.name);});"
        "}catch(e){}})('+JSON.stringify(L)+');';"
        "var wrapW=function(Orig){if(typeof Orig!=='function')return Orig;"
        "var W=function(url,opt){try{"
        "if(opt&&opt.type==='module')return Reflect.construct(Orig,[url,opt],W);"
        "var s=String(url);"
        "if(/^https?:/i.test(s)){var body=WP+'\\ntry{importScripts('+JSON.stringify(s)+');}catch(e){}';"
        "var u=URL.createObjectURL(new Blob([body],{type:'application/javascript'}));"
        "return Reflect.construct(Orig,[u,opt],W);}"
        "if(/^blob:|^data:/i.test(s)){try{var x=new XMLHttpRequest();x.open('GET',s,false);x.send();"
        "if(x.status===0||(x.status>=200&&x.status<300)){"
        "var u2=URL.createObjectURL(new Blob([WP+'\\n'+x.responseText],{type:'application/javascript'}));"
        "return Reflect.construct(Orig,[u2,opt],W);}}catch(e){}"
        "return Reflect.construct(Orig,[url,opt],W);}"
        "return Reflect.construct(Orig,[url,opt],W);"
        "}catch(e){return Reflect.construct(Orig,[url,opt],W);}};"
        "W.prototype=Orig.prototype;return __cloak(W,Orig.name);};"
        "if(self.Worker)self.Worker=wrapW(self.Worker);"
        "if(self.SharedWorker)self.SharedWorker=wrapW(self.SharedWorker);"
        "}catch(e){}"
        "}catch(e){}"
        "})();"
    )


def _work_area() -> tuple[int, int]:
    """The usable desktop size in PHYSICAL pixels (excludes the taskbar).
    SPI_GETWORKAREA returns physical pixels on a DPI-aware process; divide by
    _system_dpr() for CSS px. Non-Windows / failure returns (0, 0) so callers
    skip work-area-based sizing."""
    if not _platform.IS_WINDOWS:
        return (0, 0)
    try:
        import ctypes
        from ctypes import wintypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        SPI_GETWORKAREA = 0x0030
        r = RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(r), 0
        ):
            return (0, 0)
        return (r.right - r.left, r.bottom - r.top)
    except Exception:
        return (0, 0)


def _seed_window_size(
    profile_dir: str,
    screen: "tuple[int, int] | None" = None,
    dpr: float = 1.0,
) -> None:
    """Seed the profile's INITIAL Firefox window size to half the work area
    so a fresh profile opens a normal mid-size window — the same feel as
    chromium's default. xulstore.json's main-window size restores in DEVICE
    pixels (live-proven: a window measured 1920x1068 physical at a 1.5 OS
    scale persisted and restored as "1920"/"1068"), so the seed is physical
    px, NOT divided by the OS scale. Only when xulstore.json is absent:
    Firefox persists the user's own window size there, and a manual resize
    must survive relaunches.

    When a resolution was chosen the window is CAPPED to that spoofed screen:
    the CSS innerWidth is the physical window width divided by `dpr`, so a
    window whose inner CSS size exceeds the spoofed screen is impossible for a
    real browser (a window can't be wider than its screen) — a scanner flags it
    and it skews layout on non-responsive pages (#216). screen is in CSS px, the
    window is device px, so the physical cap is screen*dpr minus a little chrome
    to leave inner ≤ screen."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "xulstore.json")
    if os.path.exists(path):
        return
    aw, ah = _work_area()
    if not (aw and ah):
        return
    w, h = aw // 2, ah // 2
    if screen:
        sw, sh = screen
        # screen.* is CSS px; the window is device px. Physical inner ≤
        # screen*dpr keeps CSS inner (physical/dpr) ≤ screen. Trim a little for
        # the window frame so the content area stays under the screen.
        scale = dpr if dpr and dpr > 0 else 1.0
        if sw:
            w = min(w, max(320, int(sw * scale) - 16))
        if sh:
            h = min(h, max(240, int(sh * scale) - 96))
    # Centre the window on the work area. Firefox otherwise opens each new window
    # at its default top-left origin and cascades it ~16px down-right on every
    # launch, so it drifts across the desktop instead of opening centred (Mac
    # live-found: FF at the corner, "дурацкого размера"). Seeding screenX/screenY
    # pins it centred from the first launch.
    x = max(0, (aw - w) // 2)
    y = max(0, (ah - h) // 2)
    try:
        os.makedirs(profile_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "chrome://browser/content/browser.xhtml": {
                        "main-window": {
                            "width": str(w),
                            "height": str(h),
                            "screenX": str(x),
                            "screenY": str(y),
                            "sizemode": "normal",
                        }
                    }
                },
                f,
            )
    except OSError:
        pass


# Firefox's xul.css hides a collapsed toolbar with `[collapsed]{visibility:
# collapse}` (no !important). visibility:visible alone doesn't reverse it under
# -moz-box-collapse:legacy — the box stays zero-sized — so display:flex is needed
# to re-establish the layout box (modern chrome is flex-based post XUL-layout).
_BOOKMARKS_TOOLBAR_CSS = (
    "#PersonalToolbar { visibility: visible !important; display: flex !important; }\n"
)


def _show_bookmarks_toolbar(profile_dir: str) -> None:
    """Make the bookmarks toolbar visible on the profile's FIRST window.

    browser.toolbars.bookmarks.visibility="always" alone does NOT show the
    toolbar on a fresh profile's first paint. Firefox's setToolbarVisibility
    (browser.js) early-returns when `toolbar.hasAttribute("collapsed") !=
    isVisible`: a virgin profile's PersonalToolbar has NO collapsed attribute, so
    with "always" (isVisible=true) the guard reads `false != true` → returns
    before showing the bar. It only appears from the SECOND launch, once Firefox
    persisted a collapsed attribute for the guard to act on. Seeding the
    attribute either way in xulstore.json doesn't fix it reliably on the first
    paint (live-proven both false and true fail there).

    A userChrome.css rule bypasses that JS guard entirely — it's applied at paint
    from the profile's own stylesheet, so the toolbar is visible on the very
    first window. It needs toolkit.legacyUserProfileCustomizations.stylesheets
    enabled — and that pref must already be in the profile's prefs.js when Firefox
    builds its chrome, not injected late through the engine's extra_prefs (which
    lands after the first paint, so the sheet loaded only from the SECOND launch).
    We used to rely on the headless places-init to warm that up; now that the
    fast places-template path skips the headless run, write the pref straight into
    prefs.js here so the sheet loads on the FIRST paint. Written into
    chrome/userChrome.css; a zoom sheet a user added is scrubbed separately
    (_scrub_chrome_zoom_css) and runs before this, so it can't clobber the rule.
    (#242, root = the setToolbarVisibility guard against an absent collapsed
    attribute.)"""
    if not profile_dir:
        return
    # Enable custom stylesheet loading in prefs.js up front (not via the engine's
    # late extra_prefs) so userChrome.css is read on the very first paint.
    _upsert_prefs_js(profile_dir, {
        "toolkit.legacyUserProfileCustomizations.stylesheets": True,
        "browser.toolbars.bookmarks.visibility": "always",
    })
    chrome_dir = os.path.join(profile_dir, "chrome")
    path = os.path.join(chrome_dir, "userChrome.css")
    try:
        existing = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                existing = f.read()
        if "#PersonalToolbar" in existing:
            return
        os.makedirs(chrome_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(existing + _BOOKMARKS_TOOLBAR_CSS)
    except OSError:
        pass


def _scrub_chrome_zoom_css(profile_dir: str) -> None:
    """Remove a userChrome.css that zooms the browser's own UI.

    Profiles may carry a chrome/userChrome.css with a zoom rule on
    #navigator-toolbox/#mainPopupSet (an attempt to enlarge the tiny dpr=1
    chrome on a HiDPI host). CSS zoom participates in layout, so the zoomed
    toolbox moves the content area's geometry out from under the compositor:
    pages render shifted/overlapping with a paint artifact down the left edge,
    worse at Retina 2.0 (#206). Being a SIBLING of the content <browser> in
    browser.xhtml does NOT decouple them — siblings share the window's layout
    flow. A sheet with no zoom rule (a user's own customization) can't skew
    content and is left alone."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "chrome", "userChrome.css")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            css = f.read()
        if "zoom" in css:
            os.remove(path)
    except OSError:
        pass


def _screen_metrics() -> str:
    """A compact string of the host display metrics for debugging HiDPI sizing:
    physical pixel size, virtual (scaled) size, and the work area. Windows only;
    empty elsewhere."""
    if not _platform.IS_WINDOWS:
        return "n/a"
    try:
        import ctypes

        u = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        SM_CXSCREEN, SM_CYSCREEN = 0, 1
        vx = u.GetSystemMetrics(SM_CXSCREEN)
        vy = u.GetSystemMetrics(SM_CYSCREEN)
        # physical resolution via EnumDisplaySettings (current mode)
        return f"virt={vx}x{vy}"
    except Exception:
        return "err"


from .window_entry import app_id_for as _remoting_name


_SEARCH_URLS = {
    "duckduckgo": "https://duckduckgo.com/?q=",
    "google": "https://www.google.com/search?q=",
    "brave": "https://search.brave.com/search?q=",
}

# Remote Settings / Normandy / Pocket / telemetry all do a network fetch during
# Firefox startup. Over Tor those fetches are slow, and two profiles starting at
# once make one of them hang the full launch timeout on the changeset poll. The
# data: URL makes Remote Settings' shouldSkipRemoteActivity short-circuit BEFORE
# any request (a valid URL, so no invalid-URL hang — an empty string breaks URL
# parsing and hangs instead). The rest kill the remaining startup requests so a
# launch never blocks on the network. NEVER blank a *.server pref to disable a
# feature — use its enabled flag; a blank server URL is an invalid URL and hangs.
_NO_STARTUP_FETCH = {
    "services.settings.server": "data:,#remote-settings-dummy/v1",
    "services.settings.poll_interval": 0,
    "services.settings.load_dumps": True,
    "extensions.pocket.enabled": False,
    "browser.newtabpage.activity-stream.feeds.section.topstories": False,
    "browser.newtabpage.activity-stream.feeds.system.topstories": False,
    "browser.newtabpage.activity-stream.showSponsored": False,
    "browser.newtabpage.activity-stream.showSponsoredTopSites": False,
    "app.normandy.enabled": False,
    "app.normandy.first_run": False,
    "app.shield.optoutstudies.enabled": False,
    "messaging-system.rsexperimentloader.enabled": False,
    "browser.discovery.enabled": False,
    "extensions.blocklist.enabled": False,
    "datareporting.policy.dataSubmissionEnabled": False,
    "datareporting.healthreport.uploadEnabled": False,
    "toolkit.telemetry.unified": False,
    "toolkit.telemetry.archive.enabled": False,
    "browser.search.update": False,
    "browser.region.update.enabled": False,
    "network.captive-portal-service.enabled": False,
    "network.connectivity-service.enabled": False,
    "extensions.getAddons.cache.enabled": False,
    "browser.safebrowsing.downloads.remote.enabled": False,
}


def _wal_settled(places_db: str) -> bool:
    """Whether the places `-wal` has stopped growing — Places has finished its
    initial writes (the bookmark roots among them) and quiesced.

    This is a plain stat of the -wal file, so it needs NO sqlite connection and
    NO lock. The stealth-Firefox can hold places.sqlite under an exclusive lock
    for its whole run — reliably on macOS, INTERMITTENTLY on Windows/Linux
    (proven live in v2.5.1: fresh FF profiles hung 75s-3min there too) — so a
    separate-connection readiness read (places_ready) gets locked out and can't
    tell when the roots have landed. The -wal size settling is the lock-free
    signal that the engine has done its initial Places work and is safe to
    close, after which the roots become readable to the post-close places_ready
    check.

    Requires the -wal to have grown to a real size AND held it across two polls:
    a rootless first-write -wal is only a few pages, and a page-cache checkpoint
    can momentarily shrink it, so a minimum-size floor plus the stability check
    avoids declaring 'settled' before Places has actually written the roots."""
    wal = places_db + "-wal"
    try:
        size = os.path.getsize(wal)
    except OSError:
        return False
    # A -wal that hasn't grown past a few pages hasn't seen the roots yet. The
    # roots + their origins/places rows push it well past this floor.
    if size < _WAL_ROOTS_FLOOR:
        return False
    prev = _wal_settled._sizes.get(places_db)
    _wal_settled._sizes[places_db] = size
    # Settled once we observe the same non-trivial size on two consecutive polls.
    return prev is not None and prev == size


# ~16 KiB: four 4KiB pages. A fresh Places -wal with only its schema is smaller;
# writing the bookmark roots (moz_bookmarks + moz_places + moz_origins rows and
# their indexes) pushes it past this. Tuned to sit above the empty-DB baseline
# and below the roots-written size on all three OS.
_WAL_ROOTS_FLOOR = 16 * 1024
_wal_settled._sizes = {}


def _roots_ready(places_db: str) -> bool:
    """Readiness gate for the headless places init, robust on every OS.

    True if EITHER the concurrent reader can see the toolbar root (places_ready
    — the precise, fast signal, available whenever the writer isn't holding an
    exclusive lock) OR the -wal has settled (the lock-free fallback for when the
    reader is locked out). Preferring places_ready keeps the common case instant;
    the -wal fallback stops a locked-out read from burning the whole 90s timeout
    (#207) on ANY platform. The post-close places_ready is still the correctness
    check — a false-early -wal settle only costs one re-init, never a bad seed."""
    return places_ready(places_db) or _wal_settled(places_db)


# Prefs that make Firefox bake the dark theme and load userChrome.css during the
# Linux visible-offscreen warmup, so the first visible launch is already correct.
_WARMUP_CHROME_PREFS = {
    "toolkit.legacyUserProfileCustomizations.stylesheets": True,
    "browser.toolbars.bookmarks.visibility": "always",
    "ui.systemUsesDarkTheme": 1,
    "extensions.activeThemeID": "firefox-compact-dark@mozilla.org",
    "browser.theme.content-theme": 0,
    "browser.theme.toolbar-theme": 0,
}

# Far enough offscreen that the warmup window is invisible on any real display.
_OFFSCREEN_XY = -32000


def _seed_offscreen_window(profile_dir: str) -> None:
    """Seed xulstore.json so the warmup window opens far offscreen (invisible).

    The init's window state — this geometry included — is wiped before the
    visible launch, so the real window still opens at a normal position."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "xulstore.json")
    key = "chrome://browser/content/browser.xhtml"
    try:
        data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        win = data.setdefault(key, {})
        mw = win.setdefault("main-window", {})
        mw["screenX"] = str(_OFFSCREEN_XY)
        mw["screenY"] = str(_OFFSCREEN_XY)
        mw["sizemode"] = "normal"
        os.makedirs(profile_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except (OSError, ValueError):
        pass


def _seed_places_from_template(profile_dir: str) -> bool:
    """Copy the bundled places.sqlite template into a fresh profile so its
    bookmark roots exist WITHOUT launching Firefox headless first.

    Firefox rejects a hand-authored places.sqlite, but it ACCEPTS one that a
    matching-build Firefox created (right schema user_version, the fixed roots
    root/menu/toolbar/unfiled/mobile/tags) — live-proven: the patched FF150 kept
    the template and didn't rebuild it. The template is committed under
    src/assets and is checkpointed to journal_mode=DELETE, so it carries NO
    -wal/-shm sidecars (a stale sidecar is exactly what makes Firefox treat the
    db as dirty and rebuild it). Copy it in only when the profile has no
    places.sqlite yet, and clear any sidecars first. Returns True if the file is
    in place after the copy (the caller re-checks places_ready and falls back to
    the headless init if the template didn't take — e.g. after an engine update
    bumps the schema)."""
    if not profile_dir:
        return False
    try:
        from ...core.assets import asset_path
    except Exception:
        return False
    tmpl = asset_path("places_template.sqlite")
    dst = os.path.join(profile_dir, "places.sqlite")
    try:
        if not os.path.isfile(tmpl):
            return False
        if os.path.exists(dst):
            return False
        os.makedirs(profile_dir, exist_ok=True)
        # Remove any leftover sidecars so Firefox doesn't read a stale WAL over
        # the fresh template and rebuild.
        for sc in (dst + "-wal", dst + "-shm"):
            try:
                os.remove(sc)
            except OSError:
                pass
        import shutil

        shutil.copyfile(tmpl, dst)
        return os.path.isfile(dst)
    except OSError:
        return False


def _init_places_db(
    profile_dir: str,
    seed: int,
    timeout: float = 90.0,
    close_grace: float = 15.0,
    enter_timeout: float = 8.0,
    stop_event=None,
    log=None,
    proxy: dict | None = None,
    proxy_declared: bool = False,
) -> bool:
    """Launch the engine once headless so it creates a valid places.sqlite.

    Firefox rejects a hand-built places database ("files in use"), so the only
    way to get a database we can seed is to let the engine create one. `seed`
    is the profile's stable fingerprint seed, the same one the real launch
    uses. Waits for Places to write the bookmark roots (the toolbar row the
    seeder needs) — the file alone lands on disk long before the roots, and
    seeding a rootless database silently inserts nothing. Playwright's sync
    API is thread-affine, so each attempt (enter → wait for roots → polite
    exit) runs on one worker thread. The enter itself is bounded to
    `enter_timeout` and retried: on Windows the FIRST headless __enter__ RACES —
    usually ~2.5s, but ~1 in 5 wedges the cold engine start; a 25s bound made
    that wedge cost the full 25s before the fast retry, the whole 28s first
    launch (#219). At 8s the normal enter still fits and a wedge costs ~8s + a
    ~2.5s retry (~10s worst case) instead of 28s. A wedged persistent-context
    launch (the #137 family — live: the playwright driver crashed mid-init) used
    to eat the whole `timeout` as dead air with the user staring at a card that
    "does nothing". `timeout` still bounds the whole init, the polite close gets
    only `close_grace` on top (Firefox's shutdown blockers hang it for ~90s
    at times; the settle below kills the leftovers anyway), and `stop_event`
    cancels between polls so a STOP doesn't have to wait the init out.
    Returns True once the roots exist.

    `proxy` is the profile's assigned proxy, already resolved by the SAME
    `_proxy_dict` the visible launch uses. This is a REAL Firefox on the
    profile's own directory and its own fingerprint seed, so it must reach the
    network exactly the way the visible launch does. The run is engineered to
    be quiet (`_NO_STARTUP_FETCH`, plus the concrete timezone/locale that
    short-circuit the engine's geo lookups), so on a good day it costs nothing
    — but if it emits anything at all (OCSP/CRL, an engine-internal probe,
    whatever a future engine bump adds), that traffic would otherwise leave on
    the operator's real address correlated with this profile's identity.

    `proxy_declared` says the profile HAS a proxy assigned, independently of
    whether a usable dict could be built for it — see the fail-closed guard
    below.

    DELIBERATE ASYMMETRY with the visible launch's pref set: `_profile_prefs`
    adds five `media.peerconnection.ice.*` guards for a proxied profile, and
    this warm-up does NOT. That is a considered omission, not an oversight —
    the warm-up loads no page and never constructs an RTCPeerConnection, so
    there is nothing for those prefs to constrain, and the extra_prefs here are
    deliberately the minimal quiet-startup set. The DNS half arrives anyway:
    invisible_core's `configure_proxy` writes
    `network.proxy.socks_remote_dns = True` itself for any SOCKS server. If
    this run ever grows a real page load, revisit and take the full set.

    The caller must install `_install_geo_shortcircuit()` BEFORE invoking this
    with a proxy — see the call site in `_launch_and_watch`. The engine's
    `__enter__` resolves session geo before it forks Firefox, and that lookup
    goes over the proxy regardless of the concrete timezone, which collides
    with the `enter_timeout` wedge detector below (#207/#208).
    """
    # Fail CLOSED: a profile that declares a proxy but handed us no usable dict
    # must not get an engine at all. The caller treats a failed seed as
    # BOOKMARK_SEED_FAILED and opens without bookmarks (see the call site in
    # _launch_and_watch), so the cost is a lost bookmark seed — the correct
    # price. A direct Firefox against a proxied profile's identity is not.
    #
    # UNREACHABLE TODAY BY CONSTRUCTION — do not read this as a live path.
    # _spawn_invisible gates on _require_proxy_resolved (process.py) in the
    # PARENT and raises before cfg is built, and cfg carries only `proxy_url`,
    # which _proxy_dict maps to None only when falsy — indistinguishable in the
    # child from "no proxy assigned". Kept as defense in depth because the
    # failure it prevents is precisely the one this code exists to avoid.
    if proxy_declared and not proxy:
        if log:
            log(
                "PLACES_INIT refusing to start: this profile has a proxy "
                "assigned but no usable proxy could be resolved — a direct "
                "warm-up would expose the real address"
            )
        return False
    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception:
        return False
    import threading

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    # The engine hides this run by passing cloak_windows via user.js, but the
    # cloak gate acts on the value already in prefs.js when the window is
    # created — on a fresh profile there is none, so the init's Firefox window
    # popped up VISIBLY (at the sampled profile's own scale, resizing as
    # Playwright applied its viewport — the "misscaled window skewing before
    # my eyes" of #131). Put the cloak in prefs.js BEFORE the first start;
    # _scrub_headless_cloak_prefs drops it again before the visible launch.
    if not _platform.IS_LINUX:
        _upsert_prefs_js(profile_dir, {
            "zoom.stealth.cloak_windows": True,
            "widget.windows.window_occlusion_tracking.enabled": False,
        })

    # On Windows/macOS the init runs headless — the patched binary DWM-cloaks its
    # window, and a cloaked chrome window still writes the profile's chrome state
    # (the active-theme startup cache, the toolbar's persisted layout), so the
    # first VISIBLE launch already shows the dark theme and the bookmarks toolbar.
    # Linux has no cloak; a headless run has NO chrome window, so it never writes
    # that state and the first visible launch opens light with a collapsed
    # toolbar, only correcting itself on the second launch (#242). Run the Linux
    # init as a VISIBLE window placed far offscreen instead: it writes the chrome
    # state like the cloaked Windows run, but the user never sees it. The
    # throwaway offscreen geometry is wiped with the rest of the init's window
    # state below, so the real launch still opens centered/normal.
    warmup_visible = _platform.IS_LINUX
    if warmup_visible:
        # Place the warmup window far offscreen so it's invisible, and write the
        # chrome customizations it must bake in: the bookmarks-toolbar rule
        # (userChrome.css) and the dark-theme prefs. Firefox writes its
        # active-theme startup cache and toolbar layout from THIS run, so the
        # first visible launch already reflects them.
        _seed_offscreen_window(profile_dir)
        _show_bookmarks_toolbar(profile_dir)

    places = os.path.join(profile_dir, "places.sqlite")
    deadline = time.monotonic() + timeout

    # The stealth-Firefox can hold places.sqlite under an EXCLUSIVE lock for its
    # whole run (reliably on macOS, intermittently on Win/Linux — proven live in
    # v2.5.1), locking out the separate-connection places_ready read so the init
    # burned the full ~90s timeout waiting on a read that can't succeed, then
    # closed too late/hard and the seed failed (#207/#208). _roots_ready gates on
    # places_ready OR the lock-free -wal settle on EVERY platform: instant when
    # the reader is free, still bounded when it's locked out. The post-close
    # places_ready stays the correctness check.
    roots_ready = _roots_ready
    _wal_settled._sizes.pop(places, None)

    _t0 = time.monotonic()

    def phase(msg):
        if log:
            log(f"PLACES_INIT {msg} t={time.monotonic() - _t0:.1f}s")

    # Bound before the loop so the post-loop return can read it even if every
    # attempt wedged before creating its own (the loop var would be unbound).
    roots_confirmed = threading.Event()

    for _attempt in range(3):
        if stopped() or time.monotonic() >= deadline:
            break
        phase(f"attempt={_attempt} enter-begin")
        entered = threading.Event()
        ready = threading.Event()
        done = threading.Event()
        # roots_confirmed (bound above the loop) is set ONLY when roots_ready
        # actually returned True while Firefox was live — distinct from `ready`,
        # which also fires in the finally on any exit. The post-teardown
        # places_ready() re-check can read False when the roots live in an
        # un-checkpointed -wal the teardown left behind (the db IS seedable —
        # sync_places_bookmarks succeeds — but a fresh connection sees no toolbar
        # root), so trusting the live confirmation avoids a false
        # BOOKMARK_SEED_FAILED (#242).

        def init(entered=entered, ready=ready, done=done,
                 roots_confirmed=roots_confirmed) -> None:
            try:
                with InvisiblePlaywright(
                    seed=seed,
                    headless=not warmup_visible,
                    profile_dir=profile_dir,
                    # The SAME binary the visible launch uses — the profile
                    # this init creates must match the build that opens it.
                    binary_path=_binary_path_override(),
                    # The engine's __enter__ resolves geo BEFORE Firefox
                    # starts: with no timezone it discovers the egress IP
                    # (three HTTPS echo endpoints, 10s timeout each) and
                    # refreshes the geoip mmdb — over Tor that's 30-60s per
                    # attempt, blowing enter_timeout and looping the init
                    # into a 3-4 minute first launch (#207) whose seed never
                    # lands (#208). Any concrete IANA zone short-circuits all
                    # of it before the first request; this run is throwaway
                    # (its session state is wiped below), so the zone itself
                    # doesn't matter.
                    timezone="UTC",
                    # The newer engine ALSO discovers the LOCALE from the egress
                    # IP at __enter__ (a separate geo lookup: "could not resolve
                    # the session locale with no proxy"). With no direct DNS
                    # (Whonix routes only Tor) that resolve of api.ipify.org hangs
                    # its 15-20s budget and BLOCKS the enter — every headless
                    # places-init attempt then enter-wedges, the seed never lands,
                    # and a fresh Firefox profile opens with no dark theme and no
                    # bookmarks until the 2nd launch (#242). A concrete locale
                    # short-circuits that lookup too; this run is throwaway and
                    # the visible launch sets the real locale.
                    locale="en-US",
                    # Skip Firefox's startup remote-settings sync — over Tor
                    # that fetch stalls this throwaway run for minutes. The
                    # fast-shutdown stage spares the polite close below from
                    # Firefox's async shutdown blockers (~60-90s at times,
                    # burning the whole close_grace plus the settle's kill):
                    # the roots are committed before the close is requested,
                    # and the visible launch runs this same profile with the
                    # same pref.
                    extra_prefs={
                        **_NO_STARTUP_FETCH,
                        "toolkit.shutdown.fastShutdownStage": 3,
                        # On the Linux visible-offscreen warmup, bake the dark
                        # theme and the userChrome.css load into the chrome state
                        # this run persists, so the first visible launch is dark
                        # with the bookmarks toolbar shown (#242).
                        **(_WARMUP_CHROME_PREFS if warmup_visible else {}),
                    },
                    # Route this run through the profile's assigned proxy, so a
                    # real Firefox on the profile's own identity never reaches
                    # the network on the operator's real address. Spread rather
                    # than passed as proxy=None, so a DIRECT profile's engine
                    # call is byte-identical to what it was before this existed
                    # and the engine's own default is left untouched.
                    **({"proxy": proxy} if proxy else {}),
                ):
                    entered.set()
                    while (
                        time.monotonic() < deadline
                        and not stopped()
                        and not roots_ready(places)
                    ):
                        time.sleep(0.5)
                    if roots_ready(places):
                        roots_confirmed.set()
                    ready.set()
            except Exception:
                pass
            finally:
                ready.set()
                done.set()

        t = threading.Thread(target=init, daemon=True)
        t.start()
        # Two bounds on the enter, so a wedge costs seconds — not the whole
        # `timeout`. `enter_timeout` is how long we wait for a FIREFOX PROCESS to
        # appear (a cold start reads the ~230MB engine off disk, legitimately
        # ~19s on Windows, past the 8s a truly-wedged driver needs to fork). Once
        # the process is up we keep waiting for __enter__ to COMPLETE — but only
        # up to `enter_complete_cap`, not the full `timeout`: a headless enter
        # that forks Firefox and then never returns (juggler never attaches — the
        # 90s wedge Mars hit) would otherwise burn the entire timeout on ONE
        # attempt with no retry. Capping it lets the fast kill+retry (~2.5s)
        # actually run, so worst case is cap + a retry instead of 90s.
        enter_complete_cap = 22.0
        proc_deadline = min(deadline, time.monotonic() + enter_timeout)
        complete_deadline = min(deadline, time.monotonic() + enter_complete_cap)
        while not entered.wait(0.2):
            if done.is_set() or stopped() or time.monotonic() >= deadline:
                break
            if time.monotonic() > complete_deadline:
                # Firefox came up but __enter__ never completed within the cap —
                # a wedged juggler attach. Treat as wedged: kill + retry below.
                break
            if time.monotonic() > proc_deadline:
                # Grace elapsed with no `entered`. If a Firefox process exists for
                # this profile the enter is progressing (cold disk start) — keep
                # waiting (up to complete_deadline above). If none appeared, the
                # driver genuinely wedged: bail out.
                if _profile_firefox_pids(profile_dir):
                    continue
                break
        if not entered.is_set():
            # Wedged (or failed) before the engine came up: kill this
            # profile's Firefox so the blocked enter unwinds, settle, retry.
            phase(f"attempt={_attempt} enter-wedged")
            _kill_profile_firefox(profile_dir)
            _wait_profile_released(profile_dir)
            for fname in ("lock", ".parentlock"):
                try:
                    os.remove(os.path.join(profile_dir, fname))
                except OSError:
                    pass
            continue
        phase(f"attempt={_attempt} entered")
        # Entered: the worker leaves its roots-wait on ready/stop/deadline.
        while not ready.wait(0.5):
            if stopped() or time.monotonic() > deadline:
                break
        phase(f"attempt={_attempt} roots-ready")
        # The roots are committed to places.sqlite the moment ready fires, and
        # this throwaway run's session is wiped below — so there's nothing to
        # gain by waiting out its polite close. On Windows that polite close ran
        # the full close_grace (Firefox's async shutdown blockers survive
        # fastShutdownStage here), and the release-wait added more on top — ~35s
        # of pure teardown dead-air on top of every FIRST launch of a bookmarked
        # profile (#219). Kill the headless engine immediately once the roots
        # exist; _wait_profile_released below confirms it's gone before the
        # visible launch. A brief nudge for the polite exit that's usually enough.
        done.wait(1.0)
        if not done.is_set():
            _kill_profile_firefox(profile_dir)
        phase(f"attempt={_attempt} killed")
        break
    # The engine's __exit__ is a polite Playwright teardown the multi-process
    # Firefox routinely survives; a REAL launch over a still-dying instance
    # can't come up cleanly. Don't proceed until this profile's Firefox is
    # confirmed gone. The headless engine was just killed above, so give the
    # polite exit almost no grace before force-killing survivors — the roots are
    # already on disk (#219: the default 5s grace + slow Windows shutdown was
    # dead-air on every first launch).
    _wait_profile_released(profile_dir, grace=0.5)
    phase("released")
    # Clear the lock the headless run leaves so the real launch isn't blocked.
    for fname in ("lock", ".parentlock"):
        try:
            os.remove(os.path.join(profile_dir, fname))
        except OSError:
            pass
    # Drop this run's saved session. The real launch restores the previous
    # session (sessionstore.resume_session_once) — restoring the throwaway headless
    # window replaces the initial window juggler attaches to (half-destroyed
    # webProgress, endless SimpleChannel churn) and the launch wedges before
    # BROWSER_STARTED. Live-proven: process settling alone did NOT unwedge it,
    # wiping the session did.
    import shutil

    for name in (
        "sessionstore.jsonlz4",
        "sessionCheckpoints.json",
        "sessionstore-backups",
        # The init run also persists ITS window size into xulstore.json,
        # which would both defeat _seed_window_size (it only seeds when the
        # file is absent) and hand the visible window the throwaway run's
        # geometry. The init's window state is throwaway by definition.
        "xulstore.json",
    ):
        path = os.path.join(profile_dir, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass
    # Trust the LIVE confirmation over a post-teardown re-check: the roots were
    # verified present while Firefox ran; a False from places_ready() now only
    # means the -wal isn't checkpointed yet, not that seeding will fail (#242).
    return roots_confirmed.is_set() or places_ready(places)


def _upsert_prefs_js(profile_dir: str, prefs: dict) -> None:
    """Write prefs straight into the profile's prefs.js (replacing any existing
    lines for the same prefs), creating the file if needed.

    Firefox's own startup applies user.js over prefs.js, but the patched
    binary's window gates (the DWM cloak) and the first paint act on the value
    already in prefs.js when the window is created — a user.js override lands
    too late for them (live-proven both ways: the first headless init's window
    stayed VISIBLE despite cloak=true in user.js, and a visible launch right
    after the init stayed cloaked despite false in user.js). Prefs that must
    be in force for the profile's FIRST window go through here."""
    if not profile_dir or not prefs:
        return
    path = os.path.join(profile_dir, "prefs.js")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        lines = []

    def fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        return json.dumps(str(v))

    kept = [ln for ln in lines if not any(f'"{k}"' in ln for k in prefs)]
    kept += [f'user_pref("{k}", {fmt(v)});\n' for k, v in prefs.items()]
    try:
        os.makedirs(profile_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except OSError:
        pass


def _scrub_prefs_js(profile_dir: str, keys) -> None:
    """Remove the given prefs from the profile's prefs.js entirely.

    A sampled profile carries its source's scale in prefs.js. If a later launch
    picks Auto (no resolution), the resolution branch that writes
    layout.css.devPixelsPerPx never runs, so the STALE value stays in prefs.js
    and the first window opens at the sampled dpr, not the host's — a scale skew
    the user never chose. Delete the leftover so Firefox falls back to its own
    host-derived scale."""
    if not profile_dir or not keys:
        return
    path = os.path.join(profile_dir, "prefs.js")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    kept = [ln for ln in lines if not any(f'"{k}"' in ln for k in keys)]
    if len(kept) == len(lines):
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except OSError:
        pass


def _profile_last_engine_dir(profile_dir: str) -> str:
    """The engine directory Firefox last ran this profile from, read from the
    profile's own compatibility.ini (LastPlatformDir). Empty when Firefox has
    never opened the profile yet (no compatibility.ini). This is Firefox's own
    record of which build last touched the profile — an exact, native signal."""
    path = os.path.join(profile_dir, "compatibility.ini")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("LastPlatformDir="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _firefox_build_of(path: str) -> str:
    """The firefox-NN build token in an engine dir path. compatibility.ini's
    LastPlatformDir names the SHORT build dir (…/firefox-19), while
    cache_dir_for_version returns the FULL name (…/firefox-19_151.0_2026…); both
    carry the same firefox-NN, so compare on that, not the raw path."""
    base = os.path.basename(os.path.normpath(path)) if path else ""
    m = re.match(r"(firefox-\d+)", base)
    return m.group(1) if m else base


def _build_number_of(path_or_tag: str) -> int:
    """The NN in a firefox-NN dir name or tag, or -1.

    Delegates the tag grammar to engine.firefox.build_number rather than
    re-implementing it: two parsers for one tag format drift, and that format
    has already grown once (the package now appends the upstream version and a
    timestamp, firefox-18_151.0_20260724001829 — #234). This function's own job
    is the PATH part, which build_number does not do: _firefox_build_of strips
    a full filesystem path down to the bare tag first."""
    from ..engine.firefox import build_number

    return build_number(_firefox_build_of(path_or_tag) or "")


def _clear_downgrade_guard(profile_dir: str) -> bool:
    """Remove the profile's compatibility.ini so an OLDER engine will open it.

    THIS IS THE REVERSE DIRECTION OF THE PREFS PROBLEM, AND IT IS A DIFFERENT
    MECHANISM. Dropping prefs.js (below) fixes the crash a stale prefs file
    causes. It does NOT address Firefox's own *downgrade protection*, which is
    a deliberate refusal rather than a crash: Firefox records the version that
    last used a profile in compatibility.ini (LastVersion), and when it starts
    on a profile last touched by a NEWER version it refuses to open it and puts
    up the "you've launched an older version of Firefox" modal offering to
    create a fresh profile. persona's launcher drives Firefox through juggler
    with no one to click that modal, so the launch would simply never come up —
    the profile would be unopenable, which is exactly the trap that makes
    "moving binaries alone" an incomplete revert.

    compatibility.ini is DERIVED, not user data: Firefox rewrites it on every
    start from the build that is running. Removing it makes the version check
    find no prior version to compare against, so the guard does not fire and
    Firefox regenerates the file for the older build. Every piece of USER data
    (cookies, logins, history, bookmarks, cert9/key4) lives in the sqlite/db
    files and is untouched — the same reasoning that makes dropping prefs.js a
    lossless migration.

    Applied ONLY when the engine build actually goes DOWN. Forward moves are
    left exactly as they were: they are the path that is live-proven today, and
    the downgrade guard does not fire going forward, so clearing it there would
    be a behaviour change bought for nothing. Returns True when removed."""
    path = os.path.join(profile_dir, "compatibility.ini")
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        # No file, nothing to guard against — the ordinary case for a profile
        # Firefox has never opened. Not a failure.
        return False
    except OSError:
        # The file IS there and could not be removed. This is NOT the benign
        # case above and must not read as one: the downgrade guard survives,
        # so the launch about to happen meets the "older version of Firefox"
        # modal with nobody to click it and hangs instead of coming up. Say so
        # loudly — matching what _reset_prefs_on_engine_build_change does on
        # its own remove failure — because the symptom is otherwise a silent
        # launch that never paints.
        logger.exception("could not clear downgrade guard: %s", path)
        return False


def _reset_prefs_on_engine_build_change(profile_dir: str, engine_dir: str) -> bool:
    """Drop the profile's Firefox-written prefs.js when the engine BUILD changed.

    A profile's prefs.js is written by the Firefox build that last ran it. The
    stealth engine's prefs.js is NOT forward-compatible across builds: a profile
    seeded on firefox-18 makes firefox-19 SIGSEGV on startup (live-proven — the
    launch dies as TargetClosedError before the window paints). Firefox
    regenerates prefs.js from its defaults + the profile's user.js on the next
    start, and every piece of USER data (cookies, logins, history, bookmarks,
    cert9/key4) lives in the sqlite/db files, which are untouched — so removing
    the derived prefs.js is a safe, lossless migration.

    The check uses Firefox's OWN compatibility.ini (LastPlatformDir): it exists
    only after Firefox has run the profile, and names the build it ran from. When
    that differs from the build about to launch, prefs.js is stale — remove it.
    A profile Firefox has never opened (no compatibility.ini) has only persona's
    freshly-seeded prefs.js, which is safe, so this is a no-op there.
    Returns True when prefs.js was removed."""
    if not profile_dir or not engine_dir:
        return False
    last_dir = _profile_last_engine_dir(profile_dir)
    if not last_dir:
        return False  # Firefox never opened this profile — nothing stale yet
    if _firefox_build_of(last_dir) == _firefox_build_of(engine_dir):
        return False  # same firefox-NN build as last time
    prefs = os.path.join(profile_dir, "prefs.js")
    if not os.path.exists(prefs):
        return False
    try:
        os.remove(prefs)
        return True
    except OSError:
        logger.exception("Could not reset prefs.js on engine build change")
        return False


def _migrate_profile_for_engine_build(profile_dir: str, engine_dir: str) -> list:
    """Make `profile_dir` openable by the build at `engine_dir`, whichever
    DIRECTION the build moved. Returns the emit() lines describing what it did.

    THE ORDER HERE IS LOAD-BEARING, which is the whole reason this is one
    function rather than two call sites. Both migrations key off the profile's
    compatibility.ini and one of them DELETES it:

      1. the DIRECTION is read FIRST, while the file is still on disk;
      2. the prefs.js reset runs next — it READS compatibility.ini to decide
         whether prefs.js is stale;
      3. the downgrade guard is cleared LAST, because it REMOVES the file.

    Clearing first would make step 2 read "Firefox never opened this profile"
    and leave the incompatible prefs.js in place — trading Firefox's refusal
    for the SIGSEGV that refusal was standing in front of. That is a strictly
    worse outcome than not reverting at all, so the sequence is asserted by
    test rather than left to reading order.
    """
    out = []
    if not profile_dir or not engine_dir:
        return out
    # (1) Direction, taken before anything below mutates the profile.
    was = _build_number_of(_profile_last_engine_dir(profile_dir))
    now = _build_number_of(engine_dir)
    going_back = was >= 0 and now >= 0 and now < was

    # (2) A prefs.js written by a DIFFERENT build is not loadable by this one.
    if _reset_prefs_on_engine_build_change(profile_dir, engine_dir):
        out.append("ENGINE_BUILD_CHANGED: reset prefs for the new Firefox build")
        # Resetting prefs.js drops the dark-theme + bookmarks-toolbar chrome prefs
        # that a first-launch warmup would have baked in. On a profile that
        # already has its bookmarks seeded the warmup is skipped, so the first
        # visible launch after a reset opened LIGHT (live-proven on the boevaya
        # Linux). Re-write the warmup chrome prefs and re-activate the dark theme
        # (which also invalidates the addon startup cache) so the chrome comes
        # back dark. (Firefox rebuilds its toolbar-theme startup cache lazily, so
        # the tab strip goes dark immediately and the toolbar follows on the next
        # launch — the same first-launch behaviour a brand-new profile has; #242.)
        _upsert_prefs_js(profile_dir, _WARMUP_CHROME_PREFS)
        _activate_dark_theme(profile_dir)

    # (3) GOING BACK ONLY. Firefox refuses to open a profile last used by a
    # NEWER version (the "you've launched an older version of Firefox" modal),
    # and the launcher drives Firefox over juggler with nobody to click it — so
    # the launch would simply never come up and the profile reads as broken.
    if going_back and _clear_downgrade_guard(profile_dir):
        out.append(
            "ENGINE_BUILD_REVERTED: cleared the downgrade guard for the older build"
        )
        # The addon startup cache was written by the NEWER build; a stale one is
        # what makes the chrome open wrong on the first launch after a reset
        # (#242). Drop it so the older build rebuilds it from extensions.json.
        _invalidate_addon_startup_cache(profile_dir)
    return out


def _engine_lib_dir():
    """Directory of the active engine's NSS shared libraries (the Firefox
    executable's parent). certutil links against them, so the loader is pointed
    here instead of requiring a system NSS install. None when unresolved."""
    p = _invisible_binary_path()
    if not p:
        return None
    return os.path.dirname(str(p))


def _certutil_path():
    """Bundled certutil for this OS (src/assets/nss/<os>/), else one on PATH."""
    from ...core.assets import asset_path

    exe = "certutil.exe" if _platform.IS_WINDOWS else "certutil"
    osdir = "win" if _platform.IS_WINDOWS else ("mac" if _platform.IS_MACOS else "linux")
    bundled = asset_path("nss", osdir, exe)
    if os.path.isfile(bundled):
        return bundled
    from shutil import which

    return which(exe)


def _import_mtls_ca(profile_dir: str, ca_path) -> bool:
    """Import the mTLS terminator's CA into the profile's cert9.db as a trusted
    TLS CA, so Firefox accepts the terminator's leaf. Only the terminator's own
    CA is trusted, and only in this profile — no OS trust store is touched.
    Soft-fails (returns False, launch proceeds) if certutil isn't available."""
    if not ca_path or not os.path.isfile(str(ca_path)):
        return False
    # macOS ships no bundled certutil and none is on PATH; Firefox on macOS also
    # consolidates NSS into libnss3.dylib. Use the in-process ctypes NSS path
    # (services.cert.nssdb) against the engine's own libnss3 instead of a binary.
    if _platform.IS_MACOS:
        from ..cert import nssdb

        engine_dir = _engine_lib_dir()
        if not engine_dir:
            logger.error("engine lib dir unresolved; launching without mTLS CA trust")
            return False
        return nssdb.trust_ca(str(profile_dir), str(ca_path), engine_dir)
    tool = _certutil_path()
    if not tool:
        logger.error("certutil unavailable; launching without mTLS CA trust")
        return False
    env = dict(os.environ)
    if _platform.IS_WINDOWS:
        # The bundled Windows certutil ships its own NSS DLLs alongside it — put
        # that directory on PATH so it loads them (not the engine's, which may be
        # a different NSS version).
        env["PATH"] = os.path.dirname(tool) + os.pathsep + env.get("PATH", "")
    else:
        # The Linux/macOS certutil is tiny and links against the engine's NSS
        # shared libraries, so point the loader at the engine directory.
        lib = _engine_lib_dir()
        if lib:
            env["LD_LIBRARY_PATH"] = lib
            env["DYLD_LIBRARY_PATH"] = lib
    db = f"sql:{profile_dir}"
    # Writing trust needs the token authenticated. The engine creates the
    # profile's cert9.db with an EMPTY password, and a from-scratch profile has
    # no db at all — both cases fail -A with SEC_ERROR_IO unless we (a) create an
    # empty-password db when none exists and (b) authenticate -A with an empty
    # password file. An empty password file covers the engine-created db too.
    import tempfile

    # WHERE this file lands, and WHAT it is called, are both load-bearing.
    #
    # dir=: without it mkstemp resolves through TMPDIR/tmp — outside
    # PERSONA_HOME, outside the profile, and therefore outside everything
    # delete_profile, the trash and wipe_all_profiles can reach. The `finally`
    # below only covers the subprocess block, so a crash or SIGKILL between
    # here and there strands a product-identifying `persona-*` artifact in the
    # host's shared temp dir indefinitely, one per crashed import. ca_path is
    # already verified to exist (guard at the top of this function) and lives in
    # the cert session's <profile>/.persona-mtls, so its dirname names that
    # directory without reconstructing the path and without needing a makedirs
    # — sweep_key_material must never bring .persona-mtls into existence.
    #
    # prefix: sweep_key_material filters by NAME, not by directory
    # (terminator.py: `name.startswith("persona-mtls-")`). A `persona-nsspw-`
    # file placed here would be inside the perimeter but never swept — moved,
    # yet permanent. The `persona-mtls-` stem is what makes crash residue
    # genuinely reachable by the sweep at the start of the next session.
    pwfd, pwfile = tempfile.mkstemp(
        prefix="persona-mtls-nsspw-", dir=os.path.dirname(str(ca_path))
    )
    os.close(pwfd)  # empty file = empty password
    try:
        if not os.path.isfile(os.path.join(profile_dir, "cert9.db")):
            subprocess.run(
                [tool, "-N", "-d", db, "-f", pwfile],
                env=env, capture_output=True, text=True, timeout=30,
            )
        r = subprocess.run(
            [tool, "-A", "-n", "persona-mtls-terminator", "-t", "CT,C,C",
             "-i", str(ca_path), "-d", db, "-f", pwfile],
            env=env, capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        logger.exception("certutil failed to run: %s", e)
        return False
    finally:
        try:
            os.remove(pwfile)
        except OSError:
            pass
    if r.returncode != 0:
        logger.error("certutil import failed rc=%s: %s", r.returncode, r.stderr.strip())
        return False
    return True


def _scrub_headless_cloak_prefs(profile_dir: str) -> None:
    """Drop the engine's headless window-hiding prefs from the profile's
    prefs.js before a visible launch.

    The headless places init runs the engine once in this same profile; on
    Windows/macOS the engine hides that run by pref (zoom.stealth.cloak_windows
    — the patched binary DWM-cloaks its own windows), and Firefox persists the
    pref into prefs.js. The cloak gate acts on the value already in prefs.js
    when the window is created — the visible launch's own pref override lands
    too late to uncloak it (live-proven) — so the stale entries must be gone
    from prefs.js before Firefox starts. Only the two headless-hiding prefs
    are touched."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "prefs.js")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    stale = ("zoom.stealth.cloak_windows",
             "widget.windows.window_occlusion_tracking.enabled")
    kept = [ln for ln in lines if not any(k in ln for k in stale)]
    if len(kept) == len(lines):
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except OSError:
        pass


_DARK_THEME_ID = "firefox-compact-dark@mozilla.org"


def _activate_dark_theme(profile_dir: str) -> None:
    """Make Firefox's chrome (titlebar + tab strip) dark by activating the
    built-in dark theme in the profile's extensions.json.

    extensions.json is the AddonManager's source of truth for WHICH theme is
    active; on this patched Firefox, extensions.activeThemeID in prefs alone
    does not switch the active theme (live-proven: the pref is set yet
    default-theme stays active and the tab strip is light — #152). The browser
    chrome follows the ACTIVE theme add-on, so flip the built-in dark theme on
    and the others off (live-proven: the tab strip and toolbar go dark).

    The file is created by the headless bookmarks/places init that runs before
    the visible launch; when it isn't present yet (a profile with no bookmarks
    on its first launch) this is a no-op — the theme then applies from the next
    launch, once Firefox has written extensions.json."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "extensions.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    addons = data.get("addons")
    if not isinstance(addons, list):
        return
    changed = False
    for a in addons:
        if not isinstance(a, dict) or a.get("type") != "theme":
            continue
        want_active = a.get("id") == _DARK_THEME_ID
        if a.get("active") != want_active or a.get("userDisabled") != (
            not want_active
        ):
            a["active"] = want_active
            a["userDisabled"] = not want_active
            changed = True
    if changed:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass
    # The headless places init wrote addonStartup.json.lz4 with the theme that
    # was active THEN (default/light), and Firefox applies the theme from that
    # startup cache — not from a freshly-written extensions.json — on the first
    # visible launch. So the dark flip above lands in extensions.json but the
    # window still opens light, and dark only shows from the SECOND launch once
    # Firefox has rebuilt the cache (#242, live-proven: deleting this file made
    # dark + the bookmarks toolbar appear on the first launch). Drop the stale
    # cache so Firefox rebuilds it from the current extensions.json on the next
    # start. Unconditional (not gated on `changed`): the cache can be stale even
    # when extensions.json already reads dark (a prior launch flipped it after
    # the cache was written).
    _invalidate_addon_startup_cache(profile_dir)


def _invalidate_addon_startup_cache(profile_dir: str) -> None:
    """Delete Firefox's AddonManager startup cache so the next start rebuilds it
    from the profile's current extensions.json (see _activate_dark_theme)."""
    if not profile_dir:
        return
    try:
        os.remove(os.path.join(profile_dir, "addonStartup.json.lz4"))
    except OSError:
        pass


def _profile_released(profile_dir: str) -> bool:
    """Whether no Firefox of THIS profile is running. Prefers the WMI pid scan
    (a confident empty set); on no-verdict falls back to the pgrep/single-pid
    probe — treating its None as released, since blocking every launch on a
    broken scan is worse than a best-effort relaunch."""
    pids = _profile_firefox_pids(profile_dir)
    if pids is not None:
        return not pids
    return _firefox_pid(profile_dir) is None


def _wait_profile_released(
    profile_dir: str, grace: float = 5.0, timeout: float = 15.0
) -> bool:
    """Block until this profile's Firefox has fully released the profile.

    The polite teardown gets `grace` seconds to exit on its own; survivors are
    then force-killed and polled until confirmed gone (or `timeout` passes).
    Returns True when the profile is confirmed released."""
    deadline = time.monotonic() + grace
    while True:
        if _profile_released(profile_dir):
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    _kill_profile_firefox(profile_dir)
    deadline = time.monotonic() + timeout
    while True:
        if _profile_released(profile_dir):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


_PERSONA_BOOKMARKS_MARKER = ".persona-bookmarks.json"


def _read_persona_bookmark_urls(profile_dir: str) -> "set[str]":
    """The urls persona placed on the toolbar last launch, from the profile's
    marker file. Missing/unreadable → empty (a fresh or pre-#171 profile)."""
    path = os.path.join(profile_dir, _PERSONA_BOOKMARKS_MARKER)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(u) for u in data}
    except (OSError, ValueError):
        pass
    return set()


def _write_persona_bookmark_urls(profile_dir: str, urls) -> None:
    """Record the urls persona just placed so the next launch reconciles only
    persona's own footprint (see sync_places_bookmarks)."""
    path = os.path.join(profile_dir, _PERSONA_BOOKMARKS_MARKER)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(urls), f)
    except OSError:
        pass


def _seed_firefox_bookmarks(
    profile_dir: str, bookmarks: list, seed: int, stop_event=None,
    attempts: int = 2, log=None, proxy: dict | None = None,
    proxy_declared: bool = False,
) -> bool:
    """Reconcile persona's OWN Firefox toolbar bookmarks via places.sqlite —
    runs before every launch, so edits in the profile editor take effect on the
    next launch: removed bookmarks disappear, nothing ever duplicates (#144),
    an empty set clears persona's bookmarks (#147). Bookmarks the user added
    inside Firefox are preserved across relaunches — persona reconciles only the
    set it placed, tracked in a per-profile marker (#171).

    The engine must have created places.sqlite first (Firefox rejects a
    hand-built one); if it hasn't, do a one-time headless init to create it.
    An empty set on a profile with no places.sqlite yet has nothing to clear —
    no engine init just to write nothing (and no marker to update: persona has
    placed nothing).

    Returns True once the toolbar matches the configured set (or there was
    nothing to apply). Right after the init, places.sqlite can still be held
    EXCLUSIVE by the dying headless Firefox, failing the reconcile with
    "database is locked" — so the ready→reconcile pipeline gets `attempts`
    tries (a retry re-checks readiness first, so a roots-exist-but-was-locked
    miss heals without another engine run). The engine init itself runs at
    most ONCE per seed: a failed init has already exhausted its own budget of
    bounded enter retries, and re-running it from scratch is what stretched a
    fresh profile's first launch to minutes (#207). A single silent skip here
    is what opened the first visible window of a bookmarked profile on an
    unseeded database (#202); False means the seed is NOT in place, so the
    launch can report it — and the next launch, finding the db still rootless,
    inits again."""
    if not profile_dir:
        return True

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    places = os.path.join(profile_dir, "places.sqlite")
    init_ran = False
    for _attempt in range(attempts):
        if stopped():
            return False
        try:
            from ...models.bookmark import Bookmark
            from .firefox_bookmarks import places_ready, sync_places_bookmarks

            if not places_ready(places):
                if not bookmarks:
                    return True
                if init_ran:
                    continue
                init_ran = True
                # FAST PATH: drop in a pre-built places.sqlite template (the
                # bookmark roots already written) so the seed reconciles into it
                # WITHOUT a headless Firefox pre-launch. That pre-launch was the
                # whole reason a fresh bookmarked profile took 30-90s to open —
                # Firefox run twice, and on Windows its second launch on the same
                # profile dir wedges the juggler attach for the full timeout
                # (Mars measured 90s). The template is generated from THIS engine
                # build (matching schema user_version), so Firefox accepts it and
                # doesn't rebuild it (live-proven). If it doesn't take (an engine
                # update changed the schema), fall back to the headless init.
                if _seed_places_from_template(profile_dir):
                    if not places_ready(places):
                        # Template didn't satisfy the readiness check — remove it
                        # and fall back so a schema drift can't leave a bad db.
                        try:
                            os.remove(places)
                        except OSError:
                            pass
                if not places_ready(places):
                    if not _init_places_db(
                        profile_dir, seed, stop_event=stop_event, log=log,
                        # The warm-up is a real Firefox on this profile's own
                        # identity, so it goes over this profile's proxy.
                        proxy=proxy, proxy_declared=proxy_declared,
                    ):
                        continue

            marks = [
                Bookmark(b.get("name", ""), b.get("url", "")) for b in bookmarks
            ]
            prev = _read_persona_bookmark_urls(profile_dir)
            if sync_places_bookmarks(places, marks, prev_persona_urls=prev):
                _write_persona_bookmark_urls(
                    profile_dir, {bm.url for bm in marks}
                )
                return True
        except Exception:
            pass
    return False


def _has_saved_session(profile_dir: str) -> bool:
    """Whether the profile holds a session Firefox will restore at startup —
    the clean-shutdown store or a crash-recovery backup."""
    if not profile_dir:
        return False
    return os.path.exists(
        os.path.join(profile_dir, "sessionstore.jsonlz4")
    ) or os.path.exists(
        os.path.join(profile_dir, "sessionstore-backups", "recovery.jsonlz4")
    )


def _profile_prefs(cfg: dict) -> dict:
    """Firefox prefs overlaid (LAST) on invisible_playwright's generated profile.

    Brings back the behaviour persona's users expect on top of invisible's
    stealth profile: a dark UI, the chosen start page, restored tabs, a visible
    bookmarks toolbar — and disables every startup network fetch so a launch
    never blocks on Tor (the multi-profile launch hang).
    """
    prefs = dict(_NO_STARTUP_FETCH)
    prefs.update(
        {
            # The engine implements headless on Windows/macOS by making the
            # patched binary DWM-cloak its own windows
            # (zoom.stealth.cloak_windows). The one-time headless places init
            # runs in this SAME profile and Firefox persists that pref into
            # prefs.js, so a visible launch would inherit the cloak and open
            # every window invisible (#142). Force it off — user.js overlays
            # prefs.js at startup. Occlusion tracking goes back to its default
            # for the same reason (the headless run disables it).
            "zoom.stealth.cloak_windows": False,
            "widget.windows.window_occlusion_tracking.enabled": True,
            # Force the dark UI regardless of the seed. invisible derives the
            # theme from the fingerprint seed, so without this a profile's theme
            # is random; persona's users expect dark. systemUsesDarkTheme alone
            # only darkens page content and the in-content UI — the titlebar and
            # tab strip stayed light (a light strip across the top, #152). The
            # browser CHROME follows the active theme, so switch it to Firefox's
            # built-in dark theme and pin both chrome surfaces to dark (0=dark).
            "ui.systemUsesDarkTheme": 1,
            "extensions.activeThemeID": "firefox-compact-dark@mozilla.org",
            "browser.theme.content-theme": 0,
            "browser.theme.toolbar-theme": 0,
            "layout.css.prefers-color-scheme.content-override": 0,
            # Restore the previous session's tabs/windows across launches. The
            # persistent profile_dir holds sessionstore. Restore is enabled via
            # resume_session_once (re-armed through user.js on every launch, so
            # the "once" never expires) and NOT browser.startup.page=3: the
            # startup-page choice must stay 0 so nsIBrowserHandler.defaultArgs
            # stays "about:blank" — SessionStore only lets the restored session
            # OWN the startup window (overwriteTabs) when the cmdline URL
            # equals defaultArgs, and Playwright hardcodes an "about:blank"
            # cmdline URL on every persistent launch (swallowed into a string
            # arg by the trailing -new-window flag, see _child). With page=3
            # defaultArgs became the homepage instead, so every relaunch KEPT
            # an extra blank tab next to the restored ones (#148). Write the
            # store often so a tab opened seconds before close is still in the
            # restored session.
            "browser.startup.page": 0,
            "browser.sessionstore.resume_session_once": True,
            "browser.sessionstore.resume_from_crash": True,
            "browser.sessionstore.interval": 1500,
            # Always show the bookmarks toolbar so the shipped test bookmarks
            # are visible (default only shows it on the new-tab page). This pref
            # alone doesn't reveal it on a FRESH profile's first paint (the
            # setToolbarVisibility guard early-returns against an absent collapsed
            # attribute, #242) — _show_bookmarks_toolbar writes a userChrome.css
            # rule for the first launch, which needs the next pref to load.
            "browser.toolbars.bookmarks.visibility": "always",
            # Load the profile's chrome/userChrome.css (off by default since
            # FF69). Required for the bookmarks-toolbar first-paint rule above.
            "toolkit.legacyUserProfileCustomizations.stylesheets": True,
            # Close the window immediately when the user hits the X — no
            # "close N tabs?" confirmation. The confirmation would leave the
            # window (and the profile's "running" state) up until dismissed.
            "browser.tabs.warnOnClose": False,
            "browser.warnOnQuit": False,
            "browser.sessionstore.warnOnQuit": False,
            # Quit promptly when the user closes the last window. Under
            # Playwright's persistent context + juggler pipe, closing the
            # window does NOT quit Firefox on its own: it runs the full async
            # shutdown, whose blockers keep the process alive for ~60-90s
            # (live-measured). The Linux close-watch waits for that process to
            # die, so the profile card stayed "running" for a minute after an
            # X-close (#149). fastShutdownStage=3 _exit()s right after the
            # essential shutdown notifications — the process dies ~2s after the
            # window closes (live-measured), so pid-death detection fires
            # promptly. The restored session survives: sessionstore's periodic
            # write (interval below) has the tabs on disk before shutdown
            # (live-verified: has_saved_session True after a fast-shutdown
            # X-close), so #148's restore is intact.
            "toolkit.shutdown.fastShutdownStage": 3,
            # Render emoji as color glyphs, not tofu boxes (#170). The Linux
            # engine build defaults this list to "Noto Color Emoji, Twemoji
            # Mozilla" — neither resolves on the bundle-only font list (Noto
            # isn't bundled; Twemoji Mozilla is hidden by StealthSkipFamily),
            # so without this pref emoji fall through to .notdef tofu. The
            # engine bundles the genuine COLRv1 Segoe UI Emoji (fonts/
            # seguiemj1_60.ttf, mapped in bundle-fonts.list), so this value —
            # the stock Windows Firefox default — renders the Fluent color set
            # and matches the engine's always-Windows identity. (Flat Twemoji
            # glyphs mean the running build shipped the older engine that
            # bundles only TwemojiMozilla.ttf; a current engine draws Fluent.)
            "font.name-list.emoji": "Segoe UI Emoji, Twemoji Mozilla",
        }
    )
    # WebRTC IP-leak guard for a PROXIED profile. The engine's srflx override is
    # driven by an egress-IP lookup that _install_geo_shortcircuit nulls whenever
    # persona passes a concrete timezone (nearly always) — so for a NON-Tor SOCKS5
    # proxy FF would otherwise gather real host/srflx ICE candidates that don't
    # match the exit IP (an RTCPeerConnection tell). The Chromium path already
    # forbids non-proxied UDP; give FF the equivalent: force ICE to relay-only and
    # hide host candidates, and route TURN/STUN + DNS through the proxy. On a
    # direct profile WebRTC stays normal (the real IP IS the identity there).
    if cfg.get("proxy_url"):
        prefs.update(
            {
                "media.peerconnection.ice.relay_only": True,
                "media.peerconnection.ice.no_host": True,
                "media.peerconnection.ice.default_address_only": True,
                "media.peerconnection.ice.proxy_only_if_behind_proxy": True,
                "media.peerconnection.use_document_iceservers": False,
                "network.proxy.socks_remote_dns": True,
            }
        )
    # The chosen search engine feeds the Home button and the start page a
    # first launch navigates to (see _child — with startup.page=0 Firefox
    # itself never loads the homepage at startup). (Firefox 150 has no
    # per-profile way to set the URL-bar default engine without a network search
    # config, so the address bar keeps its built-in default; the start page is
    # what the user sees open.)
    engine = cfg.get("search_engine", "duckduckgo")
    start = _SEARCH_URLS.get(engine, _SEARCH_URLS["duckduckgo"]).split("?", 1)[0]
    prefs["browser.startup.homepage"] = start
    return prefs


# One launch pipeline per profile at a time (thread path). A relaunch clicked
# while the previous _child of the SAME profile was still winding down (its
# headless places init, or the kill/settle of an abandoned attempt) ran
# concurrently with it — and the predecessor's _kill_profile_firefox shot down
# the successor's just-launching Firefox: the intermittent "clicked open,
# nothing happened" relaunch roulette. Fork children are separate processes
# and never contend on these in-process locks.
_launch_locks: dict = {}
_launch_locks_guard = threading.Lock()


def _profile_launch_lock(profile_dir: str) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(profile_dir or ""))
    with _launch_locks_guard:
        lock = _launch_locks.get(key)
        if lock is None:
            lock = _launch_locks[key] = threading.Lock()
        return lock


class _WorkerSession:
    """A launched engine whose ctx lives on its own worker thread.

    Playwright's sync API is thread-affine: every call on the context — and
    the teardown — must run on the thread that created it. The Windows/macOS
    path can't use the calling thread for that, because a proxied launch of
    the patched Firefox wedges INSIDE launch_persistent_context and never
    returns (live: ~half of fresh proxied launches hang on a half-destroyed
    initial-window attach), and killing the browser does NOT make the blocked
    sync call raise — the driver keeps waiting. So the enter runs on a
    dedicated worker that the caller can bound and ABANDON on overrun, then
    retry on a fresh worker. `run_on_worker` marshals a callable back onto the
    worker (for add_init_script / teardown); the abandoned worker's leaked
    greenlet dies on its own once its Firefox is gone."""

    def __init__(self, inv, ctx, worker, requests, results):
        self.inv = inv
        self.ctx = ctx
        self._worker = worker
        self._requests = requests
        self._results = results

    def run_on_worker(self, fn, timeout=None):
        """Run `fn()` on the worker thread and return its result. Thread-affine
        ctx calls must go through here. `timeout` bounds the wait for the
        result; on overrun returns None and leaves the request in flight (the
        worker is abandoned by the caller). A None wait is unbounded."""
        self._requests.put(fn)
        try:
            return self._results.get(timeout=timeout)
        except Exception:
            return None

    def teardown(self):
        """Politely tear the engine down on its own worker, then join it.

        The polite __exit__ closes the persistent context and stops Playwright;
        over a proxy against a wedged Firefox that close can itself hang, and an
        UNBOUNDED wait for it would hold the caller (and the per-profile launch
        lock #150 wraps around the whole session) forever — the next launch of
        the profile then waits on the lock and times out (#154 mechanism a). So
        the teardown is bounded: on overrun the worker is abandoned (a daemon
        thread whose greenlet dies once its Firefox is force-killed by the
        caller right after this) rather than blocking the lock release."""
        if not self._worker.is_alive():
            return  # a dead worker never answers run_on_worker — blocking forever
        self.run_on_worker(lambda: self.inv.__exit__(None, None, None), timeout=15)
        self._requests.put(None)  # release the worker loop
        self._worker.join(10)


def _enter_on_worker(InvisiblePlaywright, kwargs, profile_dir, attempts, per_try,
                     stop_event=None, err_out=None):
    """Enter on a dedicated worker thread, bounding each attempt to `per_try`
    seconds; on overrun kill this profile's Firefox and retry on a FRESH
    worker (the wedged one is abandoned — see _WorkerSession). Returns a
    _WorkerSession on success, or None if every attempt overran or STOP
    cancelled the launch.

    When an attempt's enter RAISES (not overruns), the reason is stored in
    err_out['err'] if err_out is a dict, so the caller can surface the real error
    instead of a misleading "timed out"."""
    import queue
    import threading

    for attempt in range(attempts):
        if attempt and stop_event is not None and stop_event.is_set():
            break  # the user cancelled the launch — don't retry
        entered = threading.Event()
        holder = {}
        requests: "queue.Queue" = queue.Queue()
        results: "queue.Queue" = queue.Queue()

        def worker(entered=entered, holder=holder,
                   requests=requests, results=results):
            try:
                inv = InvisiblePlaywright(**kwargs)
                ctx = inv.__enter__()
            except BaseException as exc:  # noqa: BLE001 — the bound/retry decides
                # Record the real reason so the caller can report it instead of a
                # blanket "launch timed out": an enter that RAISES (e.g. a
                # driver/engine version mismatch) is not a timeout, and masking it
                # sent us chasing a phantom cause (#405).
                holder["err"] = f"{type(exc).__name__}: {exc}"
                entered.set()
                return
            holder["inv"] = inv
            holder["ctx"] = ctx
            entered.set()
            # Service thread-affine ctx calls until told to stop (None).
            while True:
                fn = requests.get()
                if fn is None:
                    return
                try:
                    results.put(fn())
                except Exception:
                    results.put(None)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Bound the enter, also breaking early on STOP.
        deadline = time.monotonic() + per_try
        while not entered.wait(0.2):
            stopped = stop_event is not None and stop_event.is_set()
            if stopped or time.monotonic() > deadline:
                break
        if "ctx" in holder:
            return _WorkerSession(
                holder["inv"], holder["ctx"], t, requests, results
            )
        # Remember an enter-error reason (if the attempt raised rather than
        # overran) so the caller reports it instead of "timed out".
        if isinstance(err_out, dict) and "err" in holder:
            err_out["err"] = holder["err"]
        # Overrun (or a stop, or an enter error). Kill this profile's Firefox
        # so the abandoned worker's blocked call eventually unwinds and its
        # Firefox tree is gone, then settle before a fresh attempt so we never
        # relaunch over a still-dying instance (#132's half-destroyed-window
        # wedge).
        _kill_profile_firefox(profile_dir)
        _wait_profile_released(profile_dir)
        for fname in ("lock", ".parentlock"):
            try:
                os.remove(os.path.join(profile_dir, fname))
            except OSError:
                pass
    return None


def _enter_with_timeout(InvisiblePlaywright, kwargs, profile_dir, attempts,
                        per_try, err_out=None):
    """Enter an InvisiblePlaywright context on the FORK path (Linux), bounding
    each attempt to `per_try` seconds and retrying. Returns (inv, ctx) on
    success, or (None, None) if every attempt timed out.

    __enter__ is a blocking call, so it runs in a thread; when the attempt
    overruns we kill the launching Firefox so the blocked call raises and the
    thread unwinds, then try again with a clean profile lock. The Windows/macOS
    thread path uses _enter_on_worker instead (see _WorkerSession). An attempt
    that RAISES (not overruns) records the reason in err_out['err'] so the caller
    can report it instead of a blanket timeout (#405)."""
    import threading

    for _ in range(attempts):
        holder = {}

        def attempt():
            try:
                inv = InvisiblePlaywright(**kwargs)
                holder["inv"] = inv
                holder["ctx"] = inv.__enter__()
            except BaseException as e:  # noqa: BLE001 — record, retry decides
                holder["err"] = e

        t = threading.Thread(target=attempt, daemon=True)
        t.start()
        t.join(per_try)
        if "ctx" in holder:
            return holder["inv"], holder["ctx"]
        if isinstance(err_out, dict) and holder.get("err") is not None:
            e = holder["err"]
            err_out["err"] = f"{type(e).__name__}: {e}"
        # Timed out or failed — kill the launching Firefox so the thread unwinds,
        # clear the stale lock, and retry.
        pid = _firefox_pid(profile_dir)
        if pid:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        t.join(5)
        for fname in ("lock", ".parentlock"):
            try:
                os.remove(os.path.join(profile_dir, fname))
            except OSError:
                pass
        try:
            inv = holder.get("inv")
            if inv is not None:
                inv.__exit__(None, None, None)
        except Exception:
            pass
    return None, None


def _install_geo_shortcircuit() -> None:
    """Skip the engine's egress-IP lookup when the timezone is already
    concrete. invisible runs that lookup over the proxy before every launch to
    drive a WebRTC srflx override, but persona always passes a concrete zone,
    on Tor WebRTC carries no real candidates anyway, and the lookup adds ~15s
    of round-trips (and one more network step for a proxied launch to wedge
    on). "auto"/empty still falls through to the engine's own resolver."""
    try:
        from invisible_playwright import _geo as _ipgeo
        from invisible_playwright import launcher as _iplauncher

        def _geo_no_egress(timezone, proxy, _orig=_ipgeo.prepare_session_geo):
            tz = (timezone or "").strip()
            if tz and tz.lower() != "auto":
                return _ipgeo.SessionGeo(tz, None)
            return _orig(timezone, proxy)

        _iplauncher.prepare_session_geo = _geo_no_egress
    except Exception:
        pass


def _resolve_seed(cfg: dict) -> int:
    """The profile's fingerprint seed. persona passes the profile's stable
    crc32 seed in cfg — hash(str) is salted per-process, so deriving the seed
    here gave a DIFFERENT fingerprint every app restart. The hash fallback only
    covers a cfg that predates the seed field."""
    seed = cfg.get("seed")
    if seed is not None:
        return int(seed)
    return abs(hash(cfg.get("profile_name", ""))) % (2**31)


def _child(cfg: dict, write_fd: int, stop_event=None) -> None:
    """Open a single visible Firefox window via invisible_playwright and keep it
    alive until the user closes the window or the parent asks to stop.

    Runs as a forked PROCESS on Linux and as a THREAD on Windows/macOS (where
    re-exec via sys.executable can't work: sys.executable is the flet launcher,
    not a python interpreter). When `stop_event` is given we're in a thread:
    don't install a SIGTERM handler (only valid on the main thread) and never
    os._exit (that would kill the whole app) — return instead and honour the
    event for STOP.

    Readiness and closure are reported on the pipe (BROWSER_STARTED /
    BROWSER_CLOSED) so the launcher can treat this like the chromium Popen.
    """
    in_thread = stop_event is not None

    # The browser executes untrusted remote code, so it inherits none of the
    # operator's identity — above all SSH_AUTH_SOCK, which is a live handle onto
    # their ssh-agent rather than a passive label. Done in this child's own
    # environment: forks have separate memory, so this doesn't race with other
    # profiles' children (the same reasoning as MOZ_APP_REMOTINGNAME below).
    #
    # FORK PATH ONLY, and the guard is the whole point. When `stop_event` is set
    # we are a THREAD of the manager process (Windows/macOS, where re-exec can't
    # work) and there is no separate environment to scrub: mutating os.environ
    # there would strip persona's OWN environment and every other concurrently
    # open profile's. That platform gap is a recorded absence, not a guarantee
    # that silently doesn't hold. The chromium launcher, which passes an env=
    # copy to Popen, is scrubbed on all platforms.
    if not in_thread and _platform.IS_LINUX:
        scrub_current_process_environ()

    out = os.fdopen(write_fd, "w", buffering=1)

    def emit(msg: str) -> None:
        try:
            out.write(msg + "\n")
            out.flush()
        except Exception:
            pass

    def _finish() -> None:
        """End the child: a forked process must os._exit so it doesn't return
        into the parent's code; a thread must close the pipe's write end so the
        parent's reader sees EOF (readline would otherwise block until the fd
        happens to be garbage-collected), then just return."""
        try:
            out.flush()
        except Exception:
            pass
        if not in_thread:
            os._exit(0)
        try:
            out.close()
        except Exception:
            pass

    profile_dir = cfg.get("profile_dir", "")
    _launch_and_watch(cfg, profile_dir, emit, _finish, stop_event, in_thread)


def _launch_and_watch(cfg, profile_dir, emit, _finish, stop_event, in_thread):
    """Run one profile's whole browser session: seed the profile, launch the
    engine, report readiness, watch for the close, tear down.

    Launches of the SAME profile are serialized only through the SETUP+ENTER
    phase (see _profile_launch_lock): that's where #150's race lives — a
    relaunch clicked while the previous launch was still in its headless
    bookmark init or the kill/settle of an abandoned enter attempt ran
    concurrently, and the predecessor's cleanup shot down the successor's
    just-launching Firefox. Once the browser is up (BROWSER_STARTED) the lock
    is RELEASED, so the long close-watch never blocks a legitimate relaunch —
    holding it through the whole session made a relaunch wait on the previous
    session's (slow) close and time out (#154). The acquire is cancellable so a
    STOP while a predecessor winds down can't block this thread forever (#141)."""
    import threading
    import time

    launch_lock = _profile_launch_lock(profile_dir)
    while not launch_lock.acquire(timeout=0.5):
        if stop_event is not None and stop_event.is_set():
            emit("LAUNCH_CANCELLED")
            emit("BROWSER_CLOSED")
            _finish()
            return
    _lock_released = False

    def _release_lock():
        nonlocal _lock_released
        if not _lock_released:
            _lock_released = True
            try:
                launch_lock.release()
            except RuntimeError:
                pass

    # A killed Firefox leaves lock/.parentlock in the profile; a stale lock makes
    # the next launch think the profile is already running. persona only spawns
    # this child when it knows the profile isn't running, so any lock here is
    # stale — clear it before launching.
    for fname in ("lock", ".parentlock"):
        try:
            os.remove(os.path.join(profile_dir, fname))
        except OSError:
            pass

    # The engine build may have moved since this profile last opened (auto-update
    # or a persona release that bumps the pinned build). The stealth engine's
    # prefs.js is not forward-compatible: a firefox-18-seeded profile SIGSEGVs
    # firefox-19 on startup. Drop the stale prefs.js (Firefox regenerates it; all
    # user data is in the sqlite/db files) before any seeding writes to it. The
    # engine dir is the profile's LastPlatformDir — see the compatibility.ini check.
    _engine_dir = ""
    try:
        from invisible_playwright.download import cache_dir_for_version

        _build = active_build()
        if _build:
            _engine_dir = str(cache_dir_for_version(_build))
    except Exception:
        _engine_dir = ""
    for _line in _migrate_profile_for_engine_build(profile_dir, _engine_dir):
        emit(_line)

    # Deterministic per-profile seed so the same profile keeps a stable
    # fingerprint across launches AND app restarts.
    seed = _resolve_seed(cfg)

    # Seed the profile's bookmarks into places.sqlite before the engine opens
    # the visible window, so the first real window already shows them. The
    # first time a profile with bookmarks is opened this does a one-time
    # HEADLESS engine init (a real Firefox start, tens of seconds) — it must
    # run here in the child, never on the thread constructing the handle,
    # which on a UI launch is the Flet session thread and would freeze the app.
    # A seed that didn't land after its bounded retries is reported, not
    # swallowed (#202) — but never blocks the launch itself.
    # MUST be installed BEFORE the bookmark warm-up below, not just before the
    # visible launch. The warm-up hands the engine a proxy, and the engine's
    # __enter__ calls prepare_session_geo(timezone, proxy) BEFORE it forks
    # Firefox. The egress-IP discovery in there is gated on the proxy being set
    # and sits ABOVE the concrete-IANA early return, so timezone="UTC" prevents
    # the RAISE but NOT the round-trips: ~12.5s on a cold/dead circuit. The
    # warm-up's own wedge detector waits `enter_timeout` (8.0s) for a Firefox
    # PROCESS to appear and, finding none (the geo lookup runs before the fork),
    # treats a perfectly healthy enter as a wedged driver — kill + retry, three
    # times, ending in BOOKMARK_SEED_FAILED for every proxied bookmarked
    # profile. That is #207/#208 coming back through a different door.
    #
    # Unconditional (rather than the old `if cfg.get("timezone")`) because the
    # warm-up hardcodes timezone="UTC" regardless of what cfg carries, so it
    # needs the shortcircuit even for a cfg with no timezone of its own. Safe:
    # the patch only short-circuits a CONCRETE zone and falls through to the
    # engine's real resolver for "auto"/empty. Idempotent — the closure binds
    # the pristine core function as a default arg, so re-installing can't
    # recurse.
    _install_geo_shortcircuit()

    # ONE construction of the proxy dict for this launch, built here because
    # the bookmark warm-up below needs it too — it starts a REAL Firefox on this
    # profile's own directory and fingerprint seed, so it must reach the network
    # over the same proxy the visible launch does, never on the operator's real
    # address. The visible launch reuses this exact value further down.
    proxy = _proxy_dict(cfg.get("proxy_url", ""))
    if not _seed_firefox_bookmarks(
        profile_dir, cfg.get("bookmarks", []), seed, stop_event, log=emit,
        proxy=proxy, proxy_declared=bool(cfg.get("proxy_url")),
    ) and cfg.get("bookmarks"):
        emit("BOOKMARK_SEED_FAILED: opening without the profile's bookmarks")
    # The headless init above persists the engine's window-hiding pref into
    # prefs.js; left there, THIS launch's window comes up DWM-cloaked —
    # running but invisible (#142). Must run after the seeding, before the
    # launch.
    _scrub_headless_cloak_prefs(profile_dir)
    # A zoom rule in the profile's userChrome.css skews the page render (#206,
    # see _scrub_chrome_zoom_css). Removed before the launch so the first
    # window paints clean.
    _scrub_chrome_zoom_css(profile_dir)
    # Switch the profile's chrome to the built-in dark theme. The headless init
    # above created extensions.json (the AddonManager's active-theme source of
    # truth); flip it to dark so the titlebar/tab strip aren't light (#152).
    # No-op when no init ran (no extensions.json yet).
    _activate_dark_theme(profile_dir)
    # Decided BEFORE Firefox starts (a running session rewrites the store):
    # with a saved session Firefox restores the user's tabs into the window;
    # without one the launch navigates the lone blank tab to the start page.
    restoring = _has_saved_session(profile_dir)
    # Pin DuckDuckGo as the default search engine for every Firefox profile.
    _ensure_firefox_policies()

    # When an mTLS certificate is assigned, trust the terminator's CA in this
    # profile's cert9.db so Firefox accepts the terminator's leaf for the admin
    # host. Only this profile, only the terminator's own CA.
    _ca = cfg.get("mtls_ca_path")
    if _ca:
        if _import_mtls_ca(profile_dir, _ca):
            emit("MTLS_CA_TRUSTED")
        elif _certutil_path() is None and not _platform.IS_MACOS:
            # Firefox certificate trust needs certutil, bundled for Linux and
            # Windows. macOS is coming in a follow-up. Chromium certificates work
            # on every OS, so say so plainly instead of failing with a cryptic
            # TLS error on the admin site.
            emit(
                "MTLS_UNSUPPORTED: Firefox certificates aren't available on this "
                "OS yet (use the Chromium engine for this profile)"
            )
        else:
            emit("MTLS_CA_IMPORT_FAILED: opening without certificate trust")

    # A DBus-valid, per-profile-unique remoting name so multiple profiles open
    # at once (see _remoting_name). It doubles as the Wayland app_id for the
    # taskbar icon. Set in this child's own environment — forks have separate
    # memory, so this doesn't race with other profiles' children.
    name = cfg.get("profile_name", "")
    if name and _platform.IS_LINUX:
        os.environ["MOZ_APP_REMOTINGNAME"] = _remoting_name(name)

    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception as e:
        _release_lock()
        emit(f"LAUNCH_FAILED: invisible_playwright import error: {e}")
        emit("BROWSER_CLOSED")
        _finish()  # close the pipe so the monitor's reader unblocks (no fd leak)
        return

    # NOTE: the geo shortcircuit that used to be installed here (conditionally,
    # on `cfg.get("timezone")`) now runs unconditionally further up, before the
    # bookmark warm-up — the warm-up needs it too, and needs it regardless of
    # cfg's timezone. See the comment there. Installing it again here would be
    # harmless (it is idempotent) but says something false: that the visible
    # launch is the first thing that needs it.

    # `proxy` was built once above (the bookmark warm-up needed the same value);
    # reuse it here rather than constructing a second dict from the same url.
    kwargs = {"seed": seed, "headless": False, "extra_prefs": _profile_prefs(cfg)}
    # When a downloaded build newer than the package's pinned one is active,
    # point the engine at it — its own ensure_binary() only knows the pinned
    # build. None → the engine resolves as usual.
    _binary = _binary_path_override()
    if _binary:
        kwargs["binary_path"] = _binary
    # Decouple the spoofed screen (the chosen resolution the fingerprint
    # reports) from the physical window (native, user-sized — the chromium
    # model). Overlaid on the context kwargs below, AFTER the engine's own, so
    # a 4K pick reports 4K in JS while the window behaves like a normal
    # browser. None when the profile uses Auto (engine's own screen/sizing).
    _res_overrides = None
    res = cfg.get("resolution")
    if res:
        w, h = int(res[0]), int(res[1])
        _res_overrides = _context_overrides_for(w, h)
        # Render at the HOST's display scale (layout.css.devPixelsPerPx = host
        # dpr) so the browser chrome AND page content are drawn at a readable
        # size on a HiDPI display, scaled together by the engine — no content
        # zoom, so nothing overflows its layout boxes. On this engine that one
        # pref moves the render scale, window.devicePixelRatio AND the CSS
        # resolution media queries TOGETHER (live-proven on the engine: at 1.5,
        # devicePixelRatio=1.5, matchMedia('(resolution:1.5dppx)') and
        # '(resolution:144dpi)' true, '(resolution:1dppx)' false). screen.* stays
        # the chosen resolution in CSS px, so a scanner reads an honest scaled
        # HiDPI monitor — screen 2560 at dpr 1.5, physical 2560*1.5 — a coherent
        # device, NOT the #187 "4K tell" (which was screen*dpr with an unrelated
        # chosen res). Only zoom.stealth.screen.width/height are read by xul.dll
        # for JS screen.*; screen.dpr in the pin is not read for devicePixelRatio,
        # so it's kept equal to the render dpr purely for coherence.
        dpr = _system_dpr()
        # With no_viewport Firefox owns the window size; seed a fresh profile's
        # first window so it doesn't open at Firefox's own default. Cap it to the
        # spoofed screen so the CSS innerWidth/Height (physical / dpr) never
        # exceed screen.* — a window wider than its own screen is impossible for a
        # real browser (#216).
        _seed_window_size(profile_dir, screen=(w, h), dpr=dpr)
        # Firefox has NO --width/--height CLI flags (those are chromium's); a
        # patched-Firefox launch that received them treated the leftover as a URL
        # and opened a bogus "0.0.9.51" page. Size comes ONLY from the profile's
        # xulstore.json (seeded above), never from extra_args.
        emit(f"HIDPI_DEBUG chosen={w}x{h} window=capped dpr={dpr}")
        kwargs["pin"] = {
            "screen.width": w,
            "screen.height": h,
            "screen.avail_width": w,
            "screen.avail_height": h - 40,
            "screen.dpr": dpr,
        }
        kwargs["extra_prefs"]["layout.css.devPixelsPerPx"] = str(dpr)
        # The FIRST paint uses the value already in prefs.js — the user.js
        # override only kicks in a beat later, and the headless places init
        # leaves the SAMPLED profile's dpr there. A mismatch between the prefs.js
        # value at first paint and the user.js value applied just after flips the
        # window scale mid-open (the initial skew). Carry the same value in
        # prefs.js before Firefox starts so there's no flip.
        _upsert_prefs_js(
            profile_dir, {"layout.css.devPixelsPerPx": str(dpr)}
        )
    else:
        # Auto (no chosen resolution): the resolution branch above never runs, so
        # a sampled profile's leftover devPixelsPerPx would still be in prefs.js
        # and open the first window at the SAMPLED scale, not the host's. Scrub it
        # so Firefox uses its own host-derived scale.
        _scrub_prefs_js(profile_dir, ("layout.css.devPixelsPerPx",))
    if proxy:
        kwargs["proxy"] = proxy
        # Firefox owns its own proxy sockets (playwright's native proxy), so the
        # bridge's SO_KEEPALIVE can't cover them. Turn on Firefox's TCP keepalive
        # so a silent half-open proxy circuit (Tor wedges: socket open, no bytes,
        # no EOF) is detected and dropped instead of leaving a long-lived stream
        # — a Sheets collab websocket — hung on "Working" (#184). idle_time is in
        # seconds; the default is off for content sockets over a proxy.
        kwargs["extra_prefs"]["network.tcp.keepalive.enabled"] = True
        kwargs["extra_prefs"]["network.tcp.keepalive.idle_time"] = 30
        kwargs["extra_prefs"]["network.tcp.keepalive.retry_interval"] = 10
        kwargs["extra_prefs"]["network.tcp.keepalive.probe_count"] = 4
    # A profile with no explicit locale must still present a consistent one, or
    # firefox-17 leaves the header/JS/Intl at the host OS locale (uk-UA on a
    # Ukrainian host) — a masking tell. Default an unset locale to en-US so the
    # Accept-Language header, navigator.language, and Intl all agree.
    locale = cfg.get("locale") or "en-US"
    timezone = cfg.get("timezone", "")
    kwargs["locale"] = locale
    if timezone:
        kwargs["timezone"] = timezone
    extra_args: list = []
    if name and _platform.IS_LINUX:
        # --name sets the X11 instance (the WM_CLASS labwc matches for the icon);
        # MOZ_APP_REMOTINGNAME (set above) is the Wayland app_id. Keep both so
        # the taskbar icon matches the .desktop StartupWMClass on either backend.
        extra_args.append(f"--name={_remoting_name(name)}")
    if profile_dir:
        # MUST stay the LAST argument. Playwright hardcodes an "about:blank"
        # positional URL after our args on every persistent-context launch;
        # a positional URL reaches the first window as an nsIArray, which can
        # never equal nsIBrowserHandler.defaultArgs — so SessionStore keeps
        # the cmdline tab and APPENDS the restored ones: an extra blank tab
        # on every relaunch (#148). A trailing -new-window consumes the
        # "about:blank" as its own parameter instead: same single window, but
        # opened through the string path where the URL equals defaultArgs
        # (startup.page=0), so a restored session fully overwrites it.
        extra_args.append("-new-window")
    if extra_args:
        kwargs["extra_args"] = extra_args
    if profile_dir:
        kwargs["profile_dir"] = profile_dir

    # Show the bookmarks toolbar on the FIRST window when the profile has
    # bookmarks to show. After _seed_window_size (so a seeded main-window size is
    # preserved) and before the engine starts, since Firefox reads the toolbar's
    # collapsed state from xulstore.json as it builds the window (#242).
    if cfg.get("bookmarks"):
        _show_bookmarks_toolbar(profile_dir)

    # Decouple window size from the spoofed screen. The engine builds both the
    # context `viewport` (window) and `screen` (fingerprint) from one p.screen
    # value; overlay our own so the window stays native while the screen
    # reports the chosen resolution. The overlay is a per-launch subclass (see
    # _with_context_overrides — patching the engine class races on the thread
    # path), and only when a resolution was chosen (Auto keeps the engine's own
    # sizing).
    if _res_overrides is not None:
        InvisiblePlaywright = _with_context_overrides(
            InvisiblePlaywright, _res_overrides
        )

    # Launch with a bounded timeout and retries. A launch can stall inside
    # launch_persistent_context: over Tor on Firefox's startup remote-settings
    # fetch, and — the #137 wedge — on a proxied FRESH profile whose initial
    # window is torn down mid-attach ("half-destroyed webProgress"). Cap each
    # attempt; on overrun kill this profile's Firefox and retry. On the thread
    # path the enter runs on an abandonable worker thread (killing the browser
    # does NOT make the blocked sync call raise there); the fork path kills the
    # launching Firefox so the blocked __enter__ raises and the thread unwinds.
    #
    # The per-attempt bound MUST clear the slowest legitimate launch, or it
    # kills a launch that was about to succeed and every retry repeats the
    # kill — the #154 regression: a proxied launch (a cold proxy circuit, and a
    # relaunch that restores a session referencing proxied tabs) routinely
    # takes far longer than the 25s that a fast local launch needs, so all
    # attempts overran and the launch reported LAUNCH_FAILED. The engine itself
    # bounds launch_persistent_context at 180s (a true wedge raises there), so a
    # proxied launch gets a per-attempt budget just past that — a genuinely
    # wedged launch still fails and retries, but a merely-slow one completes.
    per_try = 190 if proxy else 45
    attempts = 2 if proxy else 3
    session = None
    inv = ctx = None
    enter_err: dict = {}
    if in_thread:
        session = _enter_on_worker(
            InvisiblePlaywright, kwargs, profile_dir,
            attempts=attempts, per_try=per_try, stop_event=stop_event,
            err_out=enter_err,
        )
        if session is not None:
            inv, ctx = session.inv, session.ctx
    else:
        inv, ctx = _enter_with_timeout(
            InvisiblePlaywright, kwargs, profile_dir,
            attempts=attempts, per_try=per_try, err_out=enter_err,
        )
    if ctx is None:
        _release_lock()
        if stop_event is not None and stop_event.is_set():
            emit("LAUNCH_CANCELLED")
        elif enter_err.get("err"):
            # The enter RAISED — report the real reason (e.g. an engine/driver
            # version mismatch), not a blanket timeout (#405).
            emit(f"LAUNCH_FAILED: {enter_err['err']}")
        else:
            emit("LAUNCH_FAILED: launch timed out")
        emit("BROWSER_CLOSED")
        _finish()
        return

    # Thread-affine ctx calls must run on the worker that created ctx (the
    # thread path) — route them through the session; the fork path uses ctx
    # directly on this thread.
    def on_ctx(fn):
        if session is not None:
            return session.run_on_worker(fn)
        try:
            return fn()
        except Exception:
            return None

    # NO TITLE PREFIX (PS-30). This used to prefix every tab/window title with
    # the profile name so the taskbar button identified the persona. That wrote
    # the operator's own profile label into document.title in every page, where
    # any script lifted it with one regex — and since the label is the crc32
    # PREIMAGE of `Profile.fingerprint_seed`, reading it recovered the whole
    # presented machine. Invariant #0 does not weigh that against taskbar
    # legibility. Profile identity is carried host-side instead, by the app_id
    # (MOZ_APP_REMOTINGNAME above / --class on Chromium) matched against the
    # .desktop StartupWMClass — a surface no page can read.
    #
    # Deliberately no replacement tag: a page that clears its title must read
    # back empty, so ANY title mutation (even an opaque per-profile token) keeps
    # the observer-rewrites-my-title tell that announced the masking regardless
    # of what the label said.

    # Keep outerWidth/outerHeight tied to the real window (inner + chrome) so a
    # small window on a big spoofed screen doesn't leak the screen size through
    # outerWidth (an inner<outer==screen mismatch a scanner can flag). Only when
    # a resolution was chosen — Auto opens the window at the engine's own screen,
    # where outer already agrees.
    if _res_overrides is not None:
        on_ctx(lambda: ctx.add_init_script(_outer_size_override_script()))

    # firefox-17 leaks the host OS locale through navigator.language even when the
    # Accept-Language header is the intended locale (the header follows
    # intl.accept_languages, navigator.language does not on this build). Pin the JS
    # getters to the same locale the header carries so a scanner sees one
    # consistent language, not a header/JS mismatch (masking). Live-proven: without
    # this a US-proxy profile on a Ukrainian host reported navigator.language=uk-UA
    # while the header was en-US.
    # Mirror the effective locale (en-US when the profile leaves it unset) so the
    # override always runs — an unset-locale profile otherwise kept the host Intl.
    _lang = cfg.get("locale") or "en-US"
    on_ctx(lambda: ctx.add_init_script(_language_override_script(_lang)))

    # The persistent context already opened ONE window: with a saved session
    # Firefox restored the user's tabs into it (the trailing -new-window arg
    # plus startup.page=0 let the restore fully own the window — no extra
    # blank tab, #148); on a first launch it holds a single about:blank tab,
    # navigated to the start page below. Don't open a second page — new_page()
    # opens a whole new WINDOW in this Firefox, which is the "two windows, one
    # flashes and dies" bug. The single window is enough; the user drives it
    # from there.

    # The window is on screen the moment __enter__ returns, so report ready now.
    emit("BROWSER_STARTED")

    # firefox-19's restored/cmdline first tab can come up WITHOUT a
    # browsingContext: driving it raises "can't access property 'loadURI',
    # browsingContext is undefined" (live-proven on the fx-19 build — the
    # default ctx.pages[0] is unusable while a fresh ctx.new_page() works
    # fully). _live_page returns the first tab that actually has a context,
    # opening one if the existing tabs are all context-less, so navigation and
    # eval never hit the dead default tab.
    def _live_page():
        pages = list(ctx.pages)
        for pg in pages:
            try:
                # A page with a browsingContext answers a trivial eval; the dead
                # default tab raises here without navigating anything.
                pg.evaluate("0")
                return pg
            except Exception:
                continue
        return ctx.new_page()

    # Publish an eval hook so the MCP browser tools can drive this FF session
    # (FF has no CDP). Runs JS on the live page through on_ctx, which marshals
    # onto the worker that owns the thread-affine ctx. Bounded so a wedged page
    # can't hang the MCP request forever.
    def _ff_eval(expr: str):
        def _do():
            page = _live_page()
            if page is None:
                return None
            return page.evaluate(expr)
        return on_ctx(_do)

    def _ff_goto(url: str):
        def _do():
            page = _live_page()
            page.goto(url, wait_until="commit", timeout=30000)
            return {"url": page.url}
        return on_ctx(_do)

    if name:
        register_ff_eval(name, {"eval": _ff_eval, "goto": _ff_goto})

    # The browser is up: the #150 launch race is over. Release the per-profile
    # lock so a later relaunch of this profile isn't blocked by this session's
    # (potentially slow) close-watch and teardown below (#154). The end-of-
    # session kill targets only THIS session's tracked pids (never a fresh
    # profile-dir rescan), so it can't shoot down a relaunch's Firefox.
    _release_lock()

    # Windows opens the engine's window BEHIND persona (the launch runs in
    # persona's own process, which holds the foreground), so the user only
    # sees the stop button and no browser. Chromium raises its own window;
    # match that. Off this thread so the close-watch starts immediately.
    if _platform.IS_WINDOWS:
        threading.Thread(
            target=_raise_profile_window, args=(profile_dir,), daemon=True
        ).start()

    # First-ever launch of the profile (nothing to restore): the window came
    # up on the swallowed cmdline's lone about:blank tab — show the chosen
    # start page instead. Never on a restore launch: pages[0] is then a
    # RESTORED tab and navigating it would clobber the user's session.
    # wait_until="commit" + a cap so a stalled proxy can't hold this thread
    # (the close-watch below) for long; on timeout the tab just stays blank.
    if not restoring:
        _start = kwargs["extra_prefs"].get("browser.startup.homepage")
        if _start:
            def _open_start_page():
                # _live_page skips a context-less fx-19 default tab (which would
                # raise browsingContext-undefined) and navigates a usable one.
                _live_page().goto(_start, wait_until="commit", timeout=15000)
            on_ctx(_open_start_page)

    # Set by stop_gracefully when a STOP tears the browser down; the
    # platform close-watches below poll it between liveness checks.
    closed = threading.Event()

    def stop_gracefully() -> None:
        """On STOP (parent terminate) close the context so Firefox removes its
        own lock before exit; a hard kill would leave a stale lock and block the
        next launch. The teardown is thread-affine, so it runs on the worker
        that created ctx (thread path) or directly (fork path)."""
        try:
            if session is not None:
                session.teardown()
            elif inv is not None:
                inv.__exit__(None, None, None)
        except Exception:
            pass
        closed.set()

    # A forked process is told to STOP with SIGTERM (only settable on the main
    # thread). In the Windows/macOS thread path there's no signal; the parent
    # sets stop_event, which we poll in the wait loop below.
    if not in_thread:
        import signal

        signal.signal(signal.SIGTERM, lambda *a: stop_gracefully())

    # Closure watch. The Windows/macOS thread path watches the profile's
    # visible window and processes (see _thread_close_watch); the Linux fork
    # path watches the Firefox pid (see _fork_close_watch). Neither can use
    # ctx.pages: the ctx lives on the launch worker thread, so the sync API
    # never delivers close events to a poll made from this thread.
    # Diagnostic lifecycle log (#169): a Firefox profile sometimes died with no
    # trace in the Activity Log. Emit WHY the close-watch decided closed, and
    # every teardown/kill with its reason, so a silent death is traceable. These
    # lines reach the activity log via the launcher's log_callback.
    tracked_pids = None
    if in_thread:
        tracked_pids = _thread_close_watch(
            profile_dir, closed, stop_event, stop_gracefully, log=emit
        )
    else:
        tracked_pids = _fork_close_watch(profile_dir, closed, log=emit)

    # Tear down so Firefox actually exits and releases its lock, then report.
    # (On a STOP the close-watch already called stop_gracefully, which tears
    # the session down; a second teardown is a harmless no-op.)
    try:
        if session is not None:
            session.teardown()
        elif inv is not None:
            inv.__exit__(None, None, None)
    except Exception:
        pass
    # __exit__ is a polite Playwright teardown; a persistent-context
    # multi-process Firefox routinely survives it (parent + GPU/content/socket
    # children stayed alive after an X-close), holding the profile lock and
    # piling up across launches. Kill whatever still belongs to THIS session.
    # Kill ONLY the tracked pids (+ their process trees), never a fresh
    # profile-dir rescan: the launch lock was released at BROWSER_STARTED, so a
    # relaunch of this profile may already be starting, and a fresh rescan would
    # match — and kill — the relaunch's Firefox (the #150 race, re-armed by the
    # early lock release). The tracked parent's tree covers its own children.
    # When the watch never resolved a pid (a dead launch, tracked_pids falsy),
    # fall back to a rescan — there's no live session to protect a relaunch from.
    unregister_ff_eval(cfg.get("profile_name", ""))
    emit(f"LIFECYCLE teardown-kill pids={sorted(tracked_pids or ())} "
         f"rescan={not tracked_pids}")
    _kill_profile_firefox(
        profile_dir, tracked_pids, rescan=not tracked_pids
    )
    emit("BROWSER_CLOSED")
    _finish()
    return


def _ps_single_quote(s: str) -> str:
    """Quote a string as a PowerShell single-quoted literal.

    A Windows path is full of backslashes; json.dumps (used before) escaped them
    to '\\\\', which no longer matches the real single-backslash CommandLine, so
    the WMI -like filter returned nothing and the close-watch never saw the
    profile's processes (the "profile stuck running after X" bug). A PowerShell
    single-quoted string treats backslashes literally — only a single quote needs
    doubling."""
    return "'" + s.replace("'", "''") + "'"


def _thread_close_watch(profile_dir, closed, stop_event, stop_gracefully,
                        no_process_timeout=60.0, interval=1.0, log=None):
    """Wait until the user closed THIS profile's Firefox or STOP is requested —
    the Windows/macOS thread-path close signal. A persistent context keeps
    ctx.pages at 1 and never fires a close event, and the multi-process
    Firefox does NOT exit when the window is X-closed — GPU/content/socket
    firefox.exe children (and the connected parent) keep running, so waiting
    for a pid to die never fires. The close is decided by the profile's
    VISIBLE top-level window disappearing after it was seen (EnumWindows over
    the tracked pids); every tracked pid dying stays a close signal too (a
    crash, or macOS where there's no window enumeration).

    The profile's pids are resolved once and then polled with cheap ctypes
    checks — re-running the WMI CommandLine scan every tick for every open
    profile is real CPU/battery burn. A failed query (None) carries no
    verdict: it must NOT count as "process/window gone", or a transient
    failure would tear down a live browser — only a confident dead poll, or a
    confident no-window after the window was actually seen, decides the
    close. When no process is confidently seen within `no_process_timeout`,
    the watch falls back to the engine's visible windows (the pid resolve can
    be broken while the session lives, #203): a window in sight keeps the
    watch alive and its sustained disappearance closes; only no-pid AND
    no-window-ever is a dead launch, given up so a half-failed launch can't
    wedge the profile "running" forever.

    A close needs `no_window_streak` CONSECUTIVE confident no-window polls, not
    one: navigating to a heavy page (a scanner site over a proxy) can make a
    single EnumWindows tick miss the profile's window for a beat while the
    window is busy — reacting to that one poll tore the whole session down
    mid-navigation ("окно закрывается" на pixelscan/iphey, #159). A real user
    close keeps the window gone; a nav transient recovers on the next tick and
    resets the streak.

    Returns the tracked pid set (None if never seen) — captured while the
    browser was still alive, so the caller can force-kill the survivors after
    the polite teardown (which the multi-process Firefox routinely outlives)."""
    pids = None
    window_seen = False
    gone_streak = 0
    no_window_streak = 2
    engine_window_seen = False
    engine_window_gone = 0
    # macOS has no window enumeration (_pids_have_visible_window → None) and the
    # X-closed parent lingers, so neither window-gone nor pid-death fires — the
    # close went undetected (#168 on Mac). The content/tab procs DO die on close;
    # track a debounced drop to 0 as the close signal where the window has no
    # verdict.
    content_seen = False
    content_gone = 0
    deadline = time.monotonic() + no_process_timeout
    def say(msg):
        if log:
            log(msg)

    while not closed.wait(interval):
        if stop_event is not None and stop_event.is_set():
            say(f"LIFECYCLE close=stop-requested pids={sorted(pids or ())}")
            stop_gracefully()
            return pids
        if pids is not None:
            if not any(_pid_alive(p) for p in pids):
                say(f"LIFECYCLE close=all-pids-exit pids={sorted(pids)}")
                return pids  # every tracked Firefox process exited
            visible = _pids_have_visible_window(pids)
            if visible:
                window_seen = True
                gone_streak = 0
            elif visible is False and window_seen:
                gone_streak += 1
                if gone_streak >= no_window_streak:
                    say(f"LIFECYCLE close=window-gone pids={sorted(pids)} "
                        f"streak={gone_streak}")
                    return pids  # the window the user saw is gone → they closed it
            # None (no verdict) leaves the streak unchanged — neither a sighting
            # nor a confident miss.
            if visible is None:
                # No window enumeration (macOS): fall back to the content/tab
                # process count, which drops to 0 when the user closes the window
                # even though the parent lingers on shutdown blockers. Debounced
                # (a transient 0 between navigations doesn't close, like the
                # window-gone #159 streak).
                n = _firefox_content_proc_count(profile_dir, parent=next(iter(pids)))
                if n:
                    content_seen = True
                    content_gone = 0
                elif n == 0 and content_seen:
                    content_gone += 1
                    if content_gone >= no_window_streak:
                        say(f"LIFECYCLE close=content-procs-gone pids={sorted(pids)} "
                            f"streak={content_gone}")
                        return pids
            continue
        found = _profile_firefox_pids(profile_dir)
        if found:
            pids = found
            say(f"LIFECYCLE watch-pids pids={sorted(pids)}")
            continue
        if found is None:
            # No verdict from the WMI scan; the single-pid helper is a second
            # chance (and the only resolver on macOS, which has no WMI).
            p = _firefox_pid(profile_dir)
            if p is not None:
                pids = {p}
                say(f"LIFECYCLE watch-pids pids={sorted(pids)}")
                continue
        if time.monotonic() <= deadline:
            continue
        # Deadline passed with no pid ever resolved. That alone is NOT a dead
        # launch: BROWSER_STARTED already fired, and the pid resolve can be
        # broken while the user works in the window — returning here tore a
        # healthy session down mid-use (#203). Fall back to watching the
        # engine's own visible windows: close on a sustained window-gone
        # (the #159 debounce), give up only when no window was ever in sight.
        visible = _any_firefox_window_visible()
        if visible:
            if not engine_window_seen:
                say("LIFECYCLE watch-window (pid unresolved, watching windows)")
            engine_window_seen = True
            engine_window_gone = 0
        elif visible is False and engine_window_seen:
            engine_window_gone += 1
            if engine_window_gone >= no_window_streak:
                say(f"LIFECYCLE close=window-gone pids=[] "
                    f"streak={engine_window_gone} (pid never resolved)")
                return pids
        elif not engine_window_seen:
            # Never confidently saw a process OR a window → a dead launch;
            # give up so it can't wedge the profile "running" forever.
            say("LIFECYCLE close=no-process-timeout (launch never resolved a pid)")
            return pids
        # visible is None with a window seen before: no verdict — neither a
        # sighting nor a confident miss, same as the profile-scoped poll.
    say(f"LIFECYCLE close=closed-event pids={sorted(pids or ())}")
    return pids


def _firefox_content_proc_count(profile_dir: str, parent: int = None):
    """How many Firefox content/tab processes (`-contentproc -isForBrowser`)
    belong to THIS profile's browser (Linux/macOS), or None when the scan
    can't run.

    A persistent-context Firefox renders each open tab in an `-isForBrowser`
    content process. Those exist iff a browser window is open: closing the
    window kills every one of them within ~1s (live-measured #168: 6 → 0),
    while the launcher parent lingers on shutdown blockers. The content procs
    don't carry the profile dir on their command line, so they're matched to
    this profile by descending from the profile's launcher parent.

    `parent` is the launcher pid the close-watch already resolved once; passing
    it avoids re-running _firefox_pid (a pgrep name+cmdline match) every poll.
    That re-resolve is a #168 intermittency source: mid-shutdown pgrep can miss
    the parent for a beat and return None (no verdict), so the window-gone
    signal is lost on that poll and, depending on timing, the whole close. The
    tracked pid is stable until the parent dies (checked separately), so the
    tree walk stays anchored."""
    if _platform.IS_WINDOWS:
        return None
    if parent is None:
        parent = _firefox_pid(profile_dir)
    if parent is None:
        return None
    tree = _descendant_pids(parent)
    if tree is None:
        return None  # pgrep unavailable — no verdict
    count = 0
    for p in tree:
        cmd = _proc_cmdline(p)
        if cmd is not None and b"-isForBrowser" in cmd:
            count += 1
    return count


def _descendant_pids(root: int):
    """Every descendant pid of `root` via a pgrep -P tree walk (a single
    pgrep -P misses grandchildren, e.g. content procs under a forkserver
    child), or None when pgrep can't run — no verdict."""
    tree = set()
    frontier = [root]
    while frontier:
        nxt = []
        for p in frontier:
            try:
                out = subprocess.check_output(["pgrep", "-P", str(p)], text=True)
            except subprocess.CalledProcessError:
                continue  # no children of p
            except Exception:
                return None
            for x in out.split():
                if x.isdigit() and int(x) not in tree:
                    tree.add(int(x))
                    nxt.append(int(x))
        frontier = nxt
    return tree


def _proc_cmdline(pid: int):
    """The command line of `pid` as bytes, or None when unreadable (the process
    exited). Reads /proc/<pid>/cmdline on Linux; macOS has no /proc, so it reads
    the command through `ps` there (without which every content-proc match failed
    and the macOS X-close went undetected, #168)."""
    if _platform.IS_LINUX:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f.read().replace(b"\0", b" ")
        except OSError:
            return None
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="], text=True
        )
    except Exception:
        return None
    out = out.strip()
    return out.encode("utf-8", "replace") if out else None


def _forked_firefox_alive():
    """Whether this fork child still has a live Firefox among its own
    descendants (Linux fork path), or None when the scan can't run.

    The fork child launched exactly one browser, so any firefox in its
    process tree is THIS profile's — a liveness verdict that doesn't depend
    on the profile-dir cmdline match, which can be broken while the session
    lives (#203). The engine binary path always carries "firefox"
    (.../firefox-NN/firefox)."""
    if _platform.IS_WINDOWS:
        return None
    tree = _descendant_pids(os.getpid())
    if tree is None:
        return None
    for p in tree:
        cmd = _proc_cmdline(p)
        if cmd is not None and b"firefox" in cmd:
            return True
    return False


def _fork_close_watch(profile_dir, closed, no_process_timeout=60.0, interval=1.0,
                      log=None):
    """Wait until this profile's Firefox window closed or STOP is requested —
    the Linux fork-path close signal.

    ctx.pages cannot be the signal: the context is created on
    _enter_with_timeout's launch thread, and the sync API's dispatcher
    greenlet only pumps events during calls made on THAT thread — polled from
    the child's own thread, ctx.pages is a frozen snapshot that never drops
    to 0, so an X-close was never detected and the profile stayed "running"
    with no close in the log (#143).

    Parent-pid death alone is NOT a reliable close signal: under the Playwright
    juggler pipe an X-close does NOT quit Firefox — it runs the full async
    shutdown whose blockers hold the PARENT process alive ~60-90s (live-measured
    #168: after the window closed the parent lingered 65s, so the card stayed
    "running" a full minute). toolkit.shutdown.fastShutdownStage=3 _exits the
    parent ~2s after close on SOME hosts but NOT reliably (live-measured: the
    same profile X-closed 8 times died in 0.4s some runs and lingered tens of
    seconds others — the #168 "раз через раз" intermittency). So the parent's
    death time is NOT a dependable signal; the WINDOW closing is. Every one of
    the profile's `-isForBrowser` content/tab processes exits within ~1s of the
    window closing regardless of how long the parent lingers (live-measured
    #168: 6 → 0 in one poll while the parent hung on for another 65s). The close
    is decided by that content-proc count dropping to zero after it was seen —
    the Linux window-gone signal — and the caller then force-kills the lingering
    parent's tree, so detection never waits on the variable shutdown.

    The tracked parent pid is resolved ONCE and reused for the content-proc tree
    walk (see _firefox_content_proc_count(parent=...)): re-resolving it per poll
    let a transient pgrep miss return None and drop the window-gone signal for a
    beat — part of the intermittency. A close needs `gone_streak_needed`
    consecutive zero-content polls so a single pgrep hiccup can't tear a LIVE
    session down (#169: never kill a working profile). Returns the tracked pid
    set (None if never seen) so the caller can force-kill the lingering parent
    (+tree) after the polite teardown."""
    def say(msg):
        if log:
            log(msg)

    pid = None
    content_seen = False
    gone_streak = 0
    gone_streak_needed = 2
    tree_seen = False
    tree_gone = 0
    deadline = time.monotonic() + no_process_timeout
    while not closed.wait(interval):
        if pid is not None:
            if not _pid_alive(pid):
                say(f"LIFECYCLE close=parent-pid-exit pid={pid}")
                return {pid}  # parent exited (crash / fast shutdown fired)
            content = _firefox_content_proc_count(profile_dir, parent=pid)
            if content:
                content_seen = True
                gone_streak = 0
            elif content == 0 and content_seen:
                # The window's content processes are gone after we saw them:
                # the user closed the window. The parent may still be winding
                # down its shutdown blockers — don't wait it out; the caller
                # force-kills the tracked parent's tree right after this returns.
                gone_streak += 1
                if gone_streak >= gone_streak_needed:
                    say(f"LIFECYCLE close=window-gone pid={pid} "
                        f"streak={gone_streak}")
                    return {pid}
            # content is None (pgrep couldn't run) carries no verdict — leave
            # the streak untouched, same as the thread path's no-window
            # no-verdict.
            continue
        pid = _firefox_pid(profile_dir)
        if pid is not None:
            say(f"LIFECYCLE watch-pid pid={pid}")
            continue
        if time.monotonic() <= deadline:
            continue
        # Deadline passed with no pid resolved — NOT a dead launch by itself:
        # the profile-dir cmdline match can be broken while the session lives
        # (#203). The fork child launched exactly one browser, so its own
        # process tree is a profile-scoped liveness verdict: firefox among
        # the descendants keeps the watch alive; its sustained disappearance
        # closes; only no-pid AND no-firefox-ever gives up.
        alive = _forked_firefox_alive()
        if alive:
            if not tree_seen:
                say("LIFECYCLE watch-tree (pid unresolved, watching process tree)")
            tree_seen = True
            tree_gone = 0
        elif alive is False and tree_seen:
            tree_gone += 1
            if tree_gone >= gone_streak_needed:
                say(f"LIFECYCLE close=window-gone pid=None "
                    f"streak={tree_gone} (pid never resolved)")
                return None
        elif not tree_seen:
            say("LIFECYCLE close=no-process-timeout (launch never resolved a pid)")
            return None
    say(f"LIFECYCLE close=stop-requested pid={pid}")
    return {pid} if pid is not None else None


def _kill_profile_firefox(profile_dir, known_pids=None, rescan=True) -> None:
    """Force-kill the firefox.exe processes of THIS profile.

    `known_pids` are the pids the close-watch resolved while the browser was
    alive; each is killed together with its process tree, so a parent's
    GPU/content/socket children go too without needing a fresh scan. When
    `rescan` is True the set is also unioned with a fresh profile-dir resolve
    (children spawn/exit over a window's lifetime). The teardown after a live
    session passes rescan=False so a rescan can't match — and kill — a
    concurrently-relaunching Firefox of the same profile (the launch lock is
    released at BROWSER_STARTED). Only pids matched to this profile_dir are ever
    killed; other profiles' Firefox is untouchable."""
    pids = set(known_pids or ())
    if rescan:
        fresh = _profile_firefox_pids(profile_dir)
        if fresh:
            pids |= fresh
    for pid in pids:
        if _pid_alive(pid):
            _force_kill_pid(pid)


def _kill_process_tree_ctypes(pid: int) -> bool:
    """Terminate `pid` and every descendant via pure ctypes (Toolhelp children
    map + TerminateProcess). Returns True when the root process is confirmed
    gone. No subprocess — the packaged flet app couldn't spawn taskkill, which
    is how X-closed Firefox trees piled up as zombies."""
    if not _platform.IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        entries = _win_process_entries()
        if entries is None:
            return False
        children: dict = {}
        for p, pp, _name in entries:
            children.setdefault(pp, []).append(p)
        order = [int(pid)]
        i = 0
        while i < len(order):
            order.extend(children.get(order[i], ()))
            i += 1

        PROCESS_TERMINATE = 0x0001
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        for p in order:
            h = k32.OpenProcess(PROCESS_TERMINATE, False, p)
            if h:
                try:
                    k32.TerminateProcess(h, 1)
                finally:
                    k32.CloseHandle(h)
        return not _pid_alive(pid)
    except Exception:
        return False


def _force_kill_pid(pid: int) -> None:
    """Kill `pid` and, on Windows, its whole process tree. Firefox content/GPU
    children don't carry the profile dir on their command line, so the pid
    match only sees the parent — a single TerminateProcess would orphan the
    children (the 30 leftover firefox.exe after an X-close). Pure ctypes
    first; taskkill (by absolute path) is the fallback."""
    if _platform.IS_WINDOWS:
        if _kill_process_tree_ctypes(pid):
            return
        try:
            subprocess.run(
                [_system32_tool("taskkill.exe"), "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **_platform.no_window_kwargs(),
            )
            return
        except Exception:
            pass
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _system32_tool(*rel: str) -> str:
    """Absolute path of a System32 tool, falling back to the bare name when
    the expected file is missing. The packaged flet app failed to spawn
    PATH-searched tools (powershell/taskkill) while absolute-path spawns kept
    working — the same reason playwright invokes its node.exe by full path."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    p = os.path.join(root, "System32", *rel)
    return p if os.path.exists(p) else rel[-1].rsplit(".", 1)[0]


def _win_process_entries():
    """(pid, parent_pid, exe_name) of every running process (Windows) via a
    pure-ctypes Toolhelp snapshot, or None when the snapshot failed."""
    if not _platform.IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        TH32CS_SNAPPROCESS = 0x2

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return None
        entries = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                entries.append(
                    (
                        int(entry.th32ProcessID),
                        int(entry.th32ParentProcessID),
                        entry.szExeFile,
                    )
                )
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return entries
    except Exception:
        return None


def _win_process_command_line(pid: int):
    """The command line of `pid`, read straight from its PEB
    (NtQueryInformationProcess + ReadProcessMemory), or None when unreadable.
    Same-user processes — all of persona's Firefoxes — are readable. No
    subprocess is spawned, so this works identically in the dev venv and the
    packaged flet app."""
    if not _platform.IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, int(pid)
        )
        if not h:
            return None
        try:

            class PBI(ctypes.Structure):
                _fields_ = [
                    ("ExitStatus", ctypes.c_void_p),
                    ("PebBaseAddress", ctypes.c_void_p),
                    ("AffinityMask", ctypes.c_void_p),
                    ("BasePriority", ctypes.c_void_p),
                    ("UniqueProcessId", ctypes.c_void_p),
                    ("InheritedFromUniqueProcessId", ctypes.c_void_p),
                ]

            pbi = PBI()
            ret_len = ctypes.c_ulong(0)
            if ctypes.windll.ntdll.NtQueryInformationProcess(
                h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len)
            ):
                return None  # non-zero NTSTATUS = failure
            if not pbi.PebBaseAddress:
                return None

            def read(addr, size):
                buf = ctypes.create_string_buffer(size)
                got = ctypes.c_size_t(0)
                if (
                    not k32.ReadProcessMemory(
                        h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)
                    )
                    or got.value != size
                ):
                    return None
                return buf.raw

            ptr = ctypes.sizeof(ctypes.c_void_p)
            # PEB.ProcessParameters: 0x20 on x64, 0x10 on x86.
            raw = read(pbi.PebBaseAddress + (0x20 if ptr == 8 else 0x10), ptr)
            if raw is None:
                return None
            params = int.from_bytes(raw, "little")
            if not params:
                return None
            # RTL_USER_PROCESS_PARAMETERS.CommandLine (a UNICODE_STRING:
            # USHORT Length, USHORT MaximumLength, pad, PWSTR Buffer):
            # 0x70 on x64, 0x40 on x86.
            raw = read(params + (0x70 if ptr == 8 else 0x40), 2 * ptr)
            if raw is None:
                return None
            length = int.from_bytes(raw[0:2], "little")
            buf_ptr = int.from_bytes(raw[ptr : 2 * ptr], "little")
            if not buf_ptr or not length:
                return None
            data = read(buf_ptr, length)
            if data is None:
                return None
            return data.decode("utf-16-le", errors="replace")
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _win_firefox_command_lines():
    """[(pid, command_line_or_None)] for every running firefox.exe (Windows),
    or None when the process snapshot itself failed. A per-process None means
    that one process couldn't be read — no verdict for it."""
    entries = _win_process_entries()
    if entries is None:
        return None
    return [
        (pid, _win_process_command_line(pid))
        for pid, _ppid, name in entries
        if name.lower() == "firefox.exe"
    ]


def _win_path_needle(path: str) -> str:
    """A Windows path as it appears on a command line, for substring matching:
    lowercased with forward slashes folded to backslashes. profile_dir can
    carry a forward slash (expanduser keeps the '/' of "~/.persona"), while
    the engine normalizes it through pathlib before it reaches firefox.exe's
    command line — a raw substring match therefore never hit, the watch
    resolved NO pid for a live session, and the no-process give-up tore the
    working browser down (#203)."""
    return path.replace("/", "\\").lower()


# FF is launched with -profile <profile_dir>/.invisible-profile. Matching the
# bare profile_dir is a SUBSTRING match, so "work" also matches "work2"'s command
# line (\work is inside \work2) — resolving/killing pids for "work" tears down
# "work2"'s LIVE browser (the #150 wrong-kill class, between prefix-sibling
# personas). Anchor the match to the exact profile arg by appending the
# .invisible-profile subdir, which is unique per profile.
_INVISIBLE_SUBDIR = ".invisible-profile"


def _ff_profile_arg(profile_dir: str) -> str:
    """The exact -profile value FF is launched with (unique per profile).

    Idempotent: callers already pass the .invisible-profile path (it's what
    cfg['profile_dir'] holds), but a caller that passes the bare base dir gets it
    anchored too — either way the match is on the full, prefix-unambiguous arg.

    Separator-agnostic: a Windows profile_dir can carry BOTH separators
    (expanduser keeps the caller's forward slash — C:\\Users\\u/.persona\\...),
    and this runs on Linux too. Test the last path component by splitting on
    either separator rather than through os.path, whose split depends on the
    host os.sep (on Linux os.path.basename never splits a backslash path, so the
    idempotency check would miss an already-anchored Windows path and double the
    suffix)."""
    p = profile_dir or ""
    last = p.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if last == _INVISIBLE_SUBDIR:
        return p
    sep = "\\" if ("\\" in p and "/" not in p.rstrip("/")) else "/"
    return p.rstrip("/\\") + sep + _INVISIBLE_SUBDIR


def _profile_firefox_pids(profile_dir: str):
    """PIDs of firefox.exe processes belonging to THIS profile (Windows),
    matched by profile_dir in the command line. Returns None when no resolver
    could produce a verdict — a transient failure is "no verdict", while an
    empty set means the scan SUCCEEDED and the profile truly has no process;
    the close-watch must tell the two apart or it either misses a close
    forever or tears down a live browser.

    The primary resolver is a pure-ctypes scan (Toolhelp + a PEB read): the
    packaged flet app silently failed to spawn powershell.exe, so the WMI
    query below never returned pids and every X-close was only "detected" by
    the close-watch's 60s give-up (persona's own live logs: every Firefox
    close landed at start+60..66s while STOPs resolved in 1s). PowerShell —
    by absolute path now — stays as the fallback verdict for processes the
    ctypes scan couldn't read.

    Counting ALL firefox.exe windows is the bug behind "profile stuck running":
    with several profiles (or a stray/zombie Firefox) open, closing one still
    leaves other firefox.exe windows, so an all-windows count never reaches zero
    and the close is never detected. Scoping to this profile's own processes
    makes the close-watch reliable regardless of what else is running."""
    if not profile_dir or not _platform.IS_WINDOWS:
        return None
    entries = _win_firefox_command_lines()
    if entries is not None:
        # Anchor to the exact -profile arg so a prefix-sibling (work2) isn't
        # matched by work's needle.
        needle = _win_path_needle(_ff_profile_arg(profile_dir))
        matched = {
            pid for pid, cl in entries if cl and needle in _win_path_needle(cl)
        }
        if matched or all(cl is not None for _pid, cl in entries):
            return matched
    try:
        pat = _ps_single_quote(
            "*" + _ff_profile_arg(profile_dir).replace("/", "\\") + "*"
        )
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='firefox.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like {pat} }} | "
            "Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.check_output(
            [_system32_tool("WindowsPowerShell", "v1.0", "powershell.exe"),
             "-NoProfile", "-Command", ps],
            text=True, **_platform.no_window_kwargs(),
        )
        return {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:
        return None


def _visible_windows():
    """(hwnd, pid) of every visible top-level window (Windows), or None when
    the enumeration can't run or failed — no-verdict must stay distinct from a
    confident empty, same as _profile_firefox_pids. Pure ctypes so the
    close-watch can poll every tick without spawning a subprocess."""
    if not _platform.IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        windows = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def on_window(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                windows.append((int(hwnd), int(owner.value)))
            return True

        if not user32.EnumWindows(on_window, 0):
            return None
        return windows
    except Exception:
        return None


def _visible_window_pids():
    """PIDs that own a visible top-level window (Windows), or None on a failed
    enumeration (no verdict)."""
    windows = _visible_windows()
    if windows is None:
        return None
    return {pid for _hwnd, pid in windows}


def _window_cloaked(hwnd: int) -> bool:
    """Whether the window is DWM-cloaked (invisible to the user while still
    passing IsWindowVisible) — the engine's own headless mechanism, and also
    the state of windows on another virtual desktop. On any failure report
    not-cloaked so the raise still tries the window."""
    try:
        import ctypes

        cloaked = ctypes.c_int(0)
        DWMWA_CLOAKED = 14
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4
        ):
            return False
        return bool(cloaked.value)
    except Exception:
        return False


def _bring_window_to_foreground(hwnd: int) -> None:
    """Raise + focus a top-level window. SetForegroundWindow is permitted here
    because this runs in persona's own process right after the user clicked
    launch — the foreground process may reassign the foreground. When Windows
    refuses anyway (another app grabbed the foreground meanwhile), attach to
    the foreground window's input thread, which lifts the restriction. Every
    step is best-effort: a failed raise leaves the window in the background,
    never hidden."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        SW_SHOW, SW_RESTORE = 5, 9
        user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
        user32.BringWindowToTop(hwnd)
        if user32.SetForegroundWindow(hwnd):
            return
        k32 = ctypes.windll.kernel32
        fg = user32.GetForegroundWindow()
        if not fg or fg == hwnd:
            return
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        our_thread = k32.GetCurrentThreadId()
        if fg_thread and fg_thread != our_thread:
            if user32.AttachThreadInput(our_thread, fg_thread, True):
                try:
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(our_thread, fg_thread, False)
    except Exception:
        pass


def _raise_profile_window(profile_dir: str, timeout: float = 15.0,
                          interval: float = 0.5) -> bool:
    """Bring THIS profile's Firefox window to the foreground (Windows only).

    The engine's window is created while persona holds the foreground, so
    Windows opens it in the BACKGROUND — the profile looks launched-but-
    invisible. Polls briefly: the window can lag BROWSER_STARTED by a beat,
    and the pid resolve is a WMI query (resolved once, then reused). Returns
    True once a window owned by the profile's Firefox was raised."""
    if not _platform.IS_WINDOWS:
        return False
    deadline = time.monotonic() + timeout
    pids = None
    while time.monotonic() < deadline:
        if not pids:
            pids = _profile_firefox_pids(profile_dir)
        if pids:
            for hwnd, pid in _visible_windows() or ():
                # A DWM-cloaked window is invisible to the user even though
                # IsWindowVisible says otherwise; raising it does nothing —
                # keep polling for a really-visible window.
                if pid in pids and not _window_cloaked(hwnd):
                    _bring_window_to_foreground(hwnd)
                    return True
        time.sleep(interval)
    return False


def _pids_have_visible_window(pids):
    """Whether any of this profile's Firefox pids still owns a visible
    top-level window. True/False are confident verdicts; None means the
    enumeration couldn't tell (non-Windows or a transient failure) and the
    close-watch must not act on it. Firefox's hidden helper windows don't
    pass IsWindowVisible, so "no visible window" is exactly the state after
    the user X-closed the browser while the processes live on."""
    visible = _visible_window_pids()
    if visible is None:
        return None
    return bool(visible & set(pids))


def _any_firefox_window_visible():
    """Whether ANY engine-launched Firefox owns a visible top-level window
    (Windows). True/False are confident verdicts; None means no verdict
    (non-Windows, or a failed scan/enumeration).

    The no-process-timeout guard: when the profile-scoped pid resolve is
    broken, profile scoping is by definition unavailable — this is the widest
    honest check that a session the user can see is still alive, so the watch
    never declares a dead launch under an open window (#203). Only juggler
    parents count: the user's own Firefox is not an engine session, and
    counting it would keep a genuinely dead launch "running"."""
    entries = _win_firefox_command_lines()
    if entries is None:
        return None
    parents = {pid for pid, cl in entries if cl and "-juggler-pipe" in cl}
    if not parents:
        return False
    visible = _visible_window_pids()
    if visible is None:
        return None
    return bool(visible & parents)


def _firefox_pid(profile_dir: str):
    """The pid of a Firefox process owning this profile, or None.

    invisible launches `firefox -no-remote ... -profile <profile_dir> ...`; match
    that command line so we watch the right process even with several profiles
    open. profile_dir is unique per profile, so the match is unambiguous. Uses
    pgrep on Linux/macOS and a WMI CommandLine query on Windows (pgrep doesn't
    exist there)."""
    if not profile_dir:
        return None
    if _platform.IS_WINDOWS:
        try:
            # Query the command line of every firefox.exe and match the profile
            # dir. CIM/WMI exposes CommandLine; PowerShell keeps this dependency
            # free of extra packages. The real command line holds the pathlib-
            # normalized (backslash) dir — fold separators like _win_path_needle.
            pat = _ps_single_quote(
                "*" + _ff_profile_arg(profile_dir).replace("/", "\\") + "*"
            )
            ps = (
                "Get-CimInstance Win32_Process -Filter "
                "\"Name='firefox.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like {pat} }} | "
                "Select-Object -First 1 -ExpandProperty ProcessId"
            )
            out = subprocess.check_output(
                [_system32_tool("WindowsPowerShell", "v1.0", "powershell.exe"),
                 "-NoProfile", "-Command", ps],
                text=True, **_platform.no_window_kwargs(),
            )
            out = out.strip()
            return int(out) if out else None
        except Exception:
            return None
    try:
        # `--` stops pgrep parsing the pattern (which starts with "-profile") as
        # options. Anchor on the exact -profile arg (…/.invisible-profile) so a
        # prefix-sibling profile (work2) isn't matched by work's pattern.
        out = subprocess.check_output(
            ["pgrep", "-f", "--", re.escape(_ff_profile_arg(profile_dir))],
            text=True,
        )
    except Exception:
        return None
    for line in out.split():
        try:
            return int(line)
        except ValueError:
            continue
    return None


def _pid_alive(pid: int) -> bool:
    """Whether `pid` is still running. The Windows check is pure ctypes
    (OpenProcess + WaitForSingleObject) so the close-watch can poll every tick
    without spawning a subprocess per poll."""
    if pid is None:
        return False
    if _platform.IS_WINDOWS:
        try:
            import ctypes

            SYNCHRONIZE = 0x00100000
            WAIT_TIMEOUT = 0x00000102
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
            if not handle:
                return False  # an exited pid can't be opened
            try:
                return k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _child_main() -> None:
    """Entry for the non-fork (Win/Mac) subprocess path: read cfg from env and
    run _child writing to stdout (fd 1)."""
    cfg = json.loads(os.environ.get("PERSONA_INVISIBLE_CFG", "{}"))
    _child(cfg, 1)


class InvisibleProcess:
    """Popen-compatible handle around the invisible_playwright child.

    ``in_process`` forces the THREAD path even on Linux (where the default is
    fork). The only caller that needs it is an observer that must reach the
    session's eval hook: :func:`register_ff_eval` writes to an in-memory,
    per-process dict, so a FORKED session publishes its hook in the child and
    the parent can never see it. Running the session in a thread of the calling
    process is what makes "launch this profile, then read what it exposes" a
    single-process operation. It changes nothing else about the launch.
    """

    def __init__(self, cfg: dict, *, in_process: bool = False) -> None:
        # No blocking work here: this runs on the caller's thread (the Flet
        # session thread on a UI launch). Bookmark seeding — which can do a
        # one-time headless engine init taking tens of seconds — happens in
        # _child, off this thread, before the visible window opens.
        self._fork = _platform.needs_fork_launch() and not in_process
        if self._fork:
            ctx = mp.get_context("fork")
            r, w = os.pipe()
            self._proc = ctx.Process(target=_child, args=(cfg, w), daemon=False)
            self._proc.start()
            os.close(w)
            self.stdout = os.fdopen(r)
            self.pid = self._proc.pid
        else:
            # Windows/macOS: sys.executable is the flet launcher, not a python
            # interpreter, so re-exec (`sys.executable -c ...`) just opens a
            # second GUI. Run _child in a THREAD in this process instead; it
            # talks to us over an os.pipe exactly like the forked child does,
            # and a stop_event stands in for the SIGTERM the fork path uses.
            import threading

            r, w = os.pipe()
            self._stop_event = threading.Event()
            wf = w

            def _run(_cfg=cfg, _wf=wf, _ev=self._stop_event):
                try:
                    _child(_cfg, _wf, stop_event=_ev)
                except Exception:
                    try:
                        os.write(_wf, b"BROWSER_CLOSED\n")
                    except Exception:
                        pass
                    try:
                        os.close(_wf)
                    except Exception:
                        pass

            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
            self.stdout = os.fdopen(r)
            self.pid = 0
        self.returncode = None

    def poll(self):
        if self._fork:
            if self._proc.is_alive():
                return None
            self.returncode = self._proc.exitcode
            return self.returncode
        if self._thread.is_alive():
            return None
        self.returncode = 0
        return 0

    def wait(self, timeout=None):
        if self._fork:
            self._proc.join(timeout)
            self.returncode = self._proc.exitcode
            return self.returncode
        self._thread.join(timeout)
        self.returncode = 0 if not self._thread.is_alive() else None
        return self.returncode

    def terminate(self):
        if self._fork:
            if self._proc.is_alive():
                self._proc.terminate()
        else:
            self._stop_event.set()

    def kill(self):
        if self._fork:
            if self._proc.is_alive():
                self._proc.kill()
        else:
            self._stop_event.set()


def spawn(cfg: dict, *, in_process: bool = False) -> InvisibleProcess:
    return InvisibleProcess(cfg, in_process=in_process)
