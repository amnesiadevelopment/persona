"""The fingerprint vector inventory, as DATA.

Every observable persona spoofs is one :class:`Probe` record here: an id, the
realms it is meaningful in, and a JavaScript **expression** that returns a
JSON-serialisable value (or a Promise of one — both engines await a returned
Promise, see ``runner``).

Adding a vector MUST mean adding a record to :data:`PROBES` and nothing else.
No probe may install, define, or delete a property on a shared object: probes
READ. A detached ``<canvas>`` that is never appended, and an ``OffscreenCanvas``,
are not shared state and are fine.

Determinism is a hard requirement — two records of the same live profile must
produce byte-identical snapshots — so anything continuous is rounded here, at
the source, rather than left for the differ to squint at. A probe that cannot
be made stable does not belong in this file at all: an unstable probe is worse
than no probe, because it makes a real difference unreadable.
"""

from __future__ import annotations

from dataclasses import dataclass

WINDOW = "window"
WORKER = "worker"
ALL_REALMS = (WINDOW, WORKER)

BOTH = (WINDOW, WORKER)
WINDOW_ONLY = (WINDOW,)


@dataclass(frozen=True)
class Probe:
    """One observation.

    ``expr`` is evaluated as a JS expression in each of ``realms``. It must be
    side-effect free and must return a JSON-serialisable value or a Promise of
    one. ``note`` is operator-facing only; it never reaches a snapshot.
    """

    id: str
    realms: tuple[str, ...]
    expr: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("probe id must not be empty")
        if not self.realms:
            # Enforced here rather than in a test so an inventory edit can't
            # silently add a probe that is never evaluated anywhere.
            raise ValueError(f"probe {self.id!r} declares no realm")
        unknown = set(self.realms) - set(ALL_REALMS)
        if unknown:
            raise ValueError(f"probe {self.id!r} declares unknown realm(s): {sorted(unknown)}")


# --- shared JS snippets -----------------------------------------------------

# A realm-agnostic drawing surface. In a window realm this is a DETACHED canvas
# element (never appended, so no page ever sees it); in a worker it is an
# OffscreenCanvas. Written as an expression so it can be inlined anywhere.
_CANVAS = (
    "((typeof document!=='undefined'&&document.createElement)"
    "?document.createElement('canvas')"
    ":(typeof OffscreenCanvas!=='undefined'?new OffscreenCanvas(300,150):null))"
)

# Normalise a WebGL getParameter return: typed arrays become plain arrays,
# floats are rounded, everything else passes through. Kept as a function
# expression so each probe can inline it without a shared global.
_JS_NORM = (
    "var norm=function(v){"
    "if(v==null)return v;"
    "if(ArrayBuffer.isView(v))return Array.prototype.slice.call(v)"
    ".map(function(x){return Math.round(x*1e6)/1e6;});"
    "if(typeof v==='number')return Math.round(v*1e6)/1e6;"
    "if(typeof v==='boolean'||typeof v==='string')return v;"
    "return String(v);};"
)

# Acquire a WebGL context on a throwaway surface, hand it to ``body``, then
# release the context. Contexts are a scarce per-process resource; a probe run
# that leaked one per probe would eventually evict the page's own context.
_JS_WITH_GL = (
    "(function(body){"
    "var c=" + _CANVAS + ";"
    "if(!c)return null;"
    "var gl=null;"
    "try{gl=c.getContext('webgl')||c.getContext('experimental-webgl');}catch(e){gl=null;}"
    "if(!gl)return null;"
    "try{return body(gl);}"
    "finally{try{var lc=gl.getExtension('WEBGL_lose_context');if(lc)lc.loseContext();}catch(e){}}"
    "})"
)

# ``toString`` of a function, or a marker when the function isn't there. Used by
# the masking-tell probes: the point is to observe what a PAGE would read off a
# spoofed function, not to grep our own source for the masking helper.
_JS_FNSRC = (
    "(function(f){"
    "if(typeof f!=='function')return 'absent:'+(typeof f);"
    "try{return Function.prototype.toString.call(f);}catch(e){return 'throws:'+e;}"
    "})"
)


def _typeof(path: str) -> str:
    """``typeof`` of a possibly-absent global path, as a probe expression."""
    return f"(function(){{try{{return typeof {path};}}catch(e){{return 'throws';}}}})()"


# --- the inventory ----------------------------------------------------------

PROBES: tuple[Probe, ...] = (
    # --- navigator ----------------------------------------------------------
    Probe("navigator.userAgent", BOTH, "navigator.userAgent"),
    Probe("navigator.platform", BOTH, "navigator.platform"),
    Probe("navigator.hardwareConcurrency", BOTH, "navigator.hardwareConcurrency"),
    Probe("navigator.deviceMemory", BOTH, "(navigator.deviceMemory===undefined?null:navigator.deviceMemory)"),
    Probe("navigator.languages", BOTH, "Array.prototype.slice.call(navigator.languages||[])"),
    Probe("navigator.language", BOTH, "navigator.language"),
    Probe(
        "navigator.maxTouchPoints",
        BOTH,
        "(navigator.maxTouchPoints===undefined?null:navigator.maxTouchPoints)",
        note="WorkerNavigator has no maxTouchPoints; null there is correct, not a failure.",
    ),
    Probe("navigator.vendor", BOTH, "(navigator.vendor===undefined?null:navigator.vendor)"),
    Probe(
        "navigator.userAgentData",
        BOTH,
        # An iOS profile has NO userAgentData — real Safari has none — so the
        # honest reading there is null. Recorded as a value, never an error.
        "(function(){"
        "var d=navigator.userAgentData;"
        "if(!d)return null;"
        "return d.getHighEntropyValues(['architecture','bitness','model',"
        "'platformVersion','uaFullVersion','fullVersionList','wow64'])"
        ".then(function(h){"
        "var out={mobile:!!d.mobile,platform:d.platform,"
        "brands:(d.brands||[]).map(function(b){return b.brand+'|'+b.version;}).sort()};"
        "Object.keys(h).sort().forEach(function(k){"
        "var v=h[k];"
        "out[k]=Array.isArray(v)?v.map(function(b){"
        "return (b&&b.brand!==undefined)?(b.brand+'|'+b.version):String(b);}).sort():v;});"
        "return out;});"
        "})()",
    ),
    Probe("navigator.webdriver", BOTH, "(navigator.webdriver===undefined?null:navigator.webdriver)"),
    # --- screen geometry (window only: a worker has no screen) --------------
    Probe(
        "screen.geometry",
        WINDOW_ONLY,
        "({width:screen.width,height:screen.height,"
        "availWidth:screen.availWidth,availHeight:screen.availHeight,"
        "colorDepth:screen.colorDepth,pixelDepth:screen.pixelDepth})",
    ),
    Probe("screen.devicePixelRatio", WINDOW_ONLY, "Math.round(devicePixelRatio*1e6)/1e6"),
    Probe(
        "screen.orientation.type",
        WINDOW_ONLY,
        "((screen.orientation&&screen.orientation.type)||null)",
    ),
    Probe(
        "window.innerSize",
        WINDOW_ONLY,
        "({innerWidth:innerWidth,innerHeight:innerHeight,"
        "outerWidth:outerWidth,outerHeight:outerHeight})",
        note="Window chrome geometry; differs with a resized window, not a spoof change.",
    ),
    # --- locale -------------------------------------------------------------
    Probe(
        "intl.timeZone",
        BOTH,
        "Intl.DateTimeFormat().resolvedOptions().timeZone",
    ),
    Probe(
        "intl.resolvedOptions",
        BOTH,
        "(function(){var o=Intl.DateTimeFormat().resolvedOptions();"
        "return {locale:o.locale,calendar:o.calendar,numberingSystem:o.numberingSystem};})()",
    ),
    Probe(
        "intl.toLocaleString",
        BOTH,
        # FIXED instant — never `new Date()`, which would make every snapshot
        # differ from every other snapshot.
        "new Date(Date.UTC(2020,0,2,3,4,5)).toLocaleString()",
    ),
    Probe(
        "intl.dateToLocaleParts",
        BOTH,
        "({date:new Date(Date.UTC(2020,0,2,3,4,5)).toLocaleDateString(),"
        "time:new Date(Date.UTC(2020,0,2,3,4,5)).toLocaleTimeString(),"
        "iso:new Date(Date.UTC(2020,0,2,3,4,5)).toString()})",
    ),
    Probe("intl.numberToLocaleString", BOTH, "(1234567.891).toLocaleString()"),
    Probe(
        "intl.timezoneOffset",
        BOTH,
        "new Date(Date.UTC(2020,0,2,3,4,5)).getTimezoneOffset()",
    ),
    # --- WebGL --------------------------------------------------------------
    Probe(
        "webgl.unmasked",
        BOTH,
        _JS_WITH_GL + "(function(gl){"
        "var d=null;try{d=gl.getExtension('WEBGL_debug_renderer_info');}catch(e){}"
        "if(!d)return null;"
        "return {vendor:gl.getParameter(d.UNMASKED_VENDOR_WEBGL),"
        "renderer:gl.getParameter(d.UNMASKED_RENDERER_WEBGL)};"
        "})",
    ),
    Probe(
        "webgl.extensions",
        BOTH,
        _JS_WITH_GL + "(function(gl){"
        "var e=gl.getSupportedExtensions();"
        "return e?Array.prototype.slice.call(e).sort():null;})",
    ),
    Probe(
        "webgl.parameters",
        BOTH,
        _JS_WITH_GL + "(function(gl){" + _JS_NORM +
        "var names={'0x1F00':0x1F00,'0x1F01':0x1F01,'0x1F02':0x1F02,"
        "'0x8B8C':0x8B8C,'0x0D33':0x0D33,'0x0D3A':0x0D3A,'0x84E8':0x84E8,"
        "'0x8869':0x8869,'0x8872':0x8872,'0x8DFB':0x8DFB,'0x8DFC':0x8DFC,"
        "'0x846E':0x846E,'0x846D':0x846D,'0x0D50':0x0D50,'0x8B4D':0x8B4D};"
        "var out={};"
        "Object.keys(names).sort().forEach(function(k){"
        "try{out[k]=norm(gl.getParameter(names[k]));}catch(e){out[k]='throws';}});"
        "return out;})",
        note="Fixed pname list, hex-keyed so the snapshot is readable without a GL header.",
    ),
    # --- audio (window only: OfflineAudioContext is not exposed to workers) --
    Probe(
        "audio.sampleRate",
        WINDOW_ONLY,
        "(function(){"
        "var C=self.OfflineAudioContext||self.webkitOfflineAudioContext;"
        "if(!C)return null;"
        "var c=new C(1,1024,44100);"
        "return Math.round(c.sampleRate*1e6)/1e6;})()",
    ),
    Probe(
        "audio.digest",
        WINDOW_ONLY,
        # The canonical oscillator -> compressor graph, rendered offline and
        # reduced to a rounded scalar. persona's audio spoof adds a per-(seed,
        # index) perturbation that is deterministic across loads and sessions,
        # so this digest is stable for a profile and distinct between profiles.
        "(function(){"
        "var C=self.OfflineAudioContext||self.webkitOfflineAudioContext;"
        "if(!C)return null;"
        "var ctx=new C(1,44100,44100);"
        "var osc=ctx.createOscillator();"
        "osc.type='triangle';osc.frequency.value=10000;"
        "var comp=ctx.createDynamicsCompressor();"
        "comp.threshold.value=-50;comp.knee.value=40;comp.ratio.value=12;"
        "comp.attack.value=0;comp.release.value=0.25;"
        "osc.connect(comp);comp.connect(ctx.destination);osc.start(0);"
        "return ctx.startRendering().then(function(buf){"
        "var d=buf.getChannelData(0),s=0;"
        "for(var i=4500;i<5000;i++){s+=Math.abs(d[i]);}"
        "return {sum:Math.round(s*1e6)/1e6,length:buf.length,"
        "rate:Math.round(buf.sampleRate*1e6)/1e6};});"
        "})()",
    ),
    # --- fonts --------------------------------------------------------------
    Probe(
        "fonts.measureText",
        BOTH,
        "(function(){"
        "var c=" + _CANVAS + ";"
        "if(!c)return null;"
        "var ctx=null;try{ctx=c.getContext('2d');}catch(e){ctx=null;}"
        "if(!ctx)return null;"
        "var S='mmMwWLliI0Oo\\u00c9\\u4e2d\\u0627 gjpqy';"
        "var FONTS=['16px monospace','16px sans-serif','16px serif',"
        "'16px Arial','16px Courier New','16px Times New Roman','16px Georgia',"
        "'16px Verdana','16px Tahoma','16px Helvetica','16px Segoe UI',"
        "'16px Roboto','16px Noto Sans','16px DejaVu Sans'];"
        "var out={};"
        "FONTS.forEach(function(f){"
        "try{ctx.font=f;out[f]=Math.round(ctx.measureText(S).width*1e3)/1e3;}"
        "catch(e){out[f]='throws';}});"
        "return out;})()",
        note="Fixed string over a fixed font list, rounded to 3dp.",
    ),
    # --- devices ------------------------------------------------------------
    Probe(
        "devices.kindCounts",
        WINDOW_ONLY,
        # Kind counts only — labels are user-identifying and are deliberately
        # NOT recorded into a file the operator may share.
        "(function(){"
        "if(!navigator.mediaDevices||!navigator.mediaDevices.enumerateDevices)return null;"
        "return navigator.mediaDevices.enumerateDevices().then(function(ds){"
        "var out={};ds.forEach(function(d){out[d.kind]=(out[d.kind]||0)+1;});"
        "var sorted={};Object.keys(out).sort().forEach(function(k){sorted[k]=out[k];});"
        "return sorted;});})()",
    ),
    # --- voices -------------------------------------------------------------
    Probe(
        "voices.list",
        WINDOW_ONLY,
        # getVoices() is asynchronously populated on some builds; poll (never
        # mutate — no listener is attached) up to ~1s so two records of the
        # same session agree.
        "(function(){"
        "var s=(typeof speechSynthesis!=='undefined')?speechSynthesis:null;"
        "if(!s)return null;"
        "var grab=function(){return (s.getVoices()||[]).map(function(v){"
        "return v.name+'|'+v.lang+'|'+(v.default?'1':'0')+'|'+(v.localService?'1':'0');"
        "}).sort();};"
        "return new Promise(function(res){var n=0;var tick=function(){"
        "var v=grab();"
        "if(v.length||n++>=10)return res(v);"
        "setTimeout(tick,100);};tick();});})()",
    ),
    # --- headless / stealth tells (stealth_ext.py) --------------------------
    Probe(
        "stealth.connection",
        BOTH,
        "(function(){var c=navigator.connection;"
        "if(!c)return null;"
        "return {present:true,hasDownlinkMax:('downlinkMax' in c),"
        "downlinkMax:(c.downlinkMax===undefined?null:"
        "(c.downlinkMax===Infinity?'Infinity':c.downlinkMax)),"
        "type:(c.type===undefined?null:c.type)};})()",
    ),
    Probe(
        "stealth.contentIndex",
        BOTH,
        "(function(){"
        "var S=self.ServiceWorkerRegistration;"
        "if(!S||!S.prototype)return null;"
        "return {hasIndex:('index' in S.prototype)};})()",
    ),
    Probe(
        "stealth.apiPresence",
        BOTH,
        # A map of typeof for the desktop APIs stealth_ext fills in AND the
        # mobile-only APIs it deliberately leaves absent. An UNEXPECTED
        # PRESENCE is as much a finding as an absence, so both are recorded.
        "(function(){"
        "var names=['ServiceWorkerRegistration','ContactsManager','Bluetooth',"
        "'BatteryManager','NetworkInformation','Notification','PaymentRequest',"
        "'SharedWorker','Worker','OffscreenCanvas','SpeechSynthesis','USB',"
        "'Serial','HID','MediaDevices','WakeLock','IdleDetector','Scheduler'];"
        "var out={};names.sort().forEach(function(n){"
        "try{out[n]=typeof self[n];}catch(e){out[n]='throws';}});"
        "var navNames=['bluetooth','usb','serial','hid','credentials','permissions',"
        "'presentation','scheduling','wakeLock','xr','virtualKeyboard','ink',"
        "'getBattery','getGamepads','share','clipboard','storage','connection',"
        "'mediaDevices','mediaSession','serviceWorker','userAgentData','webkitPersistentStorage'];"
        "navNames.sort().forEach(function(n){"
        "try{out['navigator.'+n]=typeof navigator[n];}catch(e){out['navigator.'+n]='throws';}});"
        "return out;})()",
    ),
    # --- mobile tells (mobile_ext.py) ---------------------------------------
    Probe("mobile.ontouchstart", WINDOW_ONLY, _typeof("self.ontouchstart")),
    Probe("mobile.TouchEvent", BOTH, _typeof("self.TouchEvent")),
    Probe("mobile.Touch", BOTH, _typeof("self.Touch")),
    Probe(
        "mobile.pointerMedia",
        WINDOW_ONLY,
        "(function(){"
        "if(typeof matchMedia!=='function')return null;"
        "var qs=['(pointer: fine)','(pointer: coarse)','(any-pointer: fine)',"
        "'(any-pointer: coarse)','(hover: hover)','(hover: none)',"
        "'(prefers-color-scheme: dark)','(prefers-reduced-motion: reduce)'];"
        "var out={};qs.sort().forEach(function(q){"
        "try{out[q]=matchMedia(q).matches;}catch(e){out[q]='throws';}});"
        "return out;})()",
    ),
    # --- masking tells, observed rather than grepped ------------------------
    Probe(
        "masking.functionToString",
        BOTH,
        "({name:Function.prototype.toString.name,"
        "length:Function.prototype.toString.length,"
        "src:" + _JS_FNSRC + "(Function.prototype.toString)})",
    ),
    Probe(
        "masking.webglGetParameter",
        BOTH,
        "(function(){var P=self.WebGLRenderingContext&&self.WebGLRenderingContext.prototype;"
        "return " + _JS_FNSRC + "(P&&P.getParameter);})()",
    ),
    Probe(
        "masking.measureText",
        BOTH,
        "(function(){var P=self.CanvasRenderingContext2D&&self.CanvasRenderingContext2D.prototype;"
        "return " + _JS_FNSRC + "(P&&P.measureText);})()",
    ),
    Probe(
        "masking.getChannelData",
        BOTH,
        "(function(){var P=self.AudioBuffer&&self.AudioBuffer.prototype;"
        "return " + _JS_FNSRC + "(P&&P.getChannelData);})()",
    ),
    Probe(
        "masking.getVoices",
        WINDOW_ONLY,
        "(function(){var s=(typeof speechSynthesis!=='undefined')?speechSynthesis:null;"
        "return " + _JS_FNSRC + "(s&&s.getVoices);})()",
    ),
    Probe(
        "masking.getCurrentPosition",
        WINDOW_ONLY,
        "(function(){var g=navigator.geolocation;"
        "return " + _JS_FNSRC + "(g&&g.getCurrentPosition);})()",
    ),
    Probe(
        "masking.hardwareConcurrencyGetter",
        BOTH,
        "(function(){"
        "var d=Object.getOwnPropertyDescriptor(navigator,'hardwareConcurrency');"
        "if(!d){var P=Object.getPrototypeOf(navigator);"
        "d=P&&Object.getOwnPropertyDescriptor(P,'hardwareConcurrency');}"
        "if(!d)return 'absent:descriptor';"
        "return {own:!!Object.getOwnPropertyDescriptor(navigator,'hardwareConcurrency'),"
        "src:" + _JS_FNSRC + "(d.get)};})()",
    ),
    Probe(
        "masking.workerConstructor",
        BOTH,
        "(function(){return " + _JS_FNSRC + "(self.Worker);})()",
    ),
    # --- realm bootstrap markers -------------------------------------------
    Probe(
        "realm.bootMarkers",
        BOTH,
        # Whether persona's shared realm registry reached this realm at all. A
        # window that carries leaves and a worker that carries none is exactly
        # the defect class worker_wrap.py exists to prevent.
        "(function(){"
        "var g=self;"
        "return {hasBoots:Array.isArray(g.__pnaBoots),"
        "bootCount:(Array.isArray(g.__pnaBoots)?g.__pnaBoots.length:null),"
        "srcCount:(Array.isArray(g.__pnaBootSrc)?g.__pnaBootSrc.length:null),"
        "booted:!!g.__pnaBooted,installed:!!g.__pnaBootInstalled};})()",
    ),
    Probe(
        "realm.kind",
        BOTH,
        "(function(){"
        "return {hasDocument:(typeof document!=='undefined'),"
        "hasWindow:(typeof window!=='undefined'),"
        "ctor:(self.constructor&&self.constructor.name)||null};})()",
    ),
)


def probe_ids() -> tuple[str, ...]:
    return tuple(p.id for p in PROBES)


def probes_for_realm(realm: str) -> tuple[Probe, ...]:
    """Every probe that declares ``realm``, in inventory order."""
    return tuple(p for p in PROBES if realm in p.realms)


def _check_unique() -> None:
    seen: set[str] = set()
    for p in PROBES:
        if p.id in seen:
            raise ValueError(f"duplicate probe id in inventory: {p.id!r}")
        seen.add(p.id)


_check_unique()

__all__ = [
    "ALL_REALMS",
    "BOTH",
    "PROBES",
    "Probe",
    "WINDOW",
    "WINDOW_ONLY",
    "WORKER",
    "probe_ids",
    "probes_for_realm",
]
