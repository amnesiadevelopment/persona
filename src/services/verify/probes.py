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

# A same-origin CHILD BROWSING CONTEXT — a nested realm with its own set of
# intrinsics, reached from the top realm by INDEXED access (``self[N]``).
#
# Deliberately NOT named "iframe". The name is data, and this one is load
# bearing twice over: the test suite already uses the literal ``"iframe"`` as
# its example of an UNKNOWN realm (it asserts that both ``run_probes`` and the
# CLI reject it), so registering that spelling would silently invert two tests
# from guarding a rejection into failing. "child_frame" also says the thing
# that actually matters here — the realm is the child CONTEXT, not the element
# that hosts it, and those are reached by different paths.
CHILD_FRAME = "child_frame"

ALL_REALMS = (WINDOW, WORKER, CHILD_FRAME)

BOTH = (WINDOW, WORKER)
WINDOW_ONLY = (WINDOW,)
CHILD_FRAME_ONLY = (CHILD_FRAME,)

# Every realm the harness can enter — which is ALL_REALMS, by definition.
# Reserved for probes whose vector is meaningful in all of them (see
# ``realm.frameIdentity``). ALIASED rather than restated: written out as its
# own tuple it is a second source of truth, and a new realm landing in one
# tuple and not the other would empty the four ``set(p.realms) ==
# set(ALL_REALMS)`` selectors in tests/test_verify_snapshot.py and break them
# four files away from the edit that caused it. Aliasing makes that drift
# impossible by construction instead of asking the next author to remember.
EVERY_REALM = ALL_REALMS

# --- how a vector is expected to behave ACROSS TWO PROFILES -----------------
#
# Orthogonal to everything else in this file. The rest of the inventory serves
# the continuity question ("is this ONE profile still itself?"), where every
# probe is equally interesting. This axis serves the unlinkability question
# ("are these TWO profiles the same identity?"), where they are emphatically
# not: two profiles agreeing on `navigator.platform` is the operator's own
# choice, while two profiles agreeing on a seed-derived digest is a defect.
#
# The classification lives HERE, as data on the record, rather than as a list
# inside the comparator, because ``probes.py:8`` requires that adding a vector
# means adding a record to PROBES and nothing else. A comparator holding its
# own id list would silently keep answering after the inventory moved.

# Seed-derived and high-entropy: two DISTINCT profiles must not produce the
# same reading. Agreement here is a linkable identity — the Level 2 finding.
INDEPENDENT = "independent"

# Seed-derived but drawn from a SMALL fixed pool. Varies across profiles by
# design, yet two profiles colliding is ordinary pigeonhole, not a leak — with
# a six-entry pool, agreement is expected roughly one time in six. Reporting a
# collision here would be a false finding, so this axis never does.
POOLED = "pooled"

# Not seed-derived at all: operator-chosen configuration (os_type, resolution,
# engine, locale), a real-hardware constant, or an observation of persona's
# masking MECHANISM rather than of the identity it produces. Two profiles are
# SUPPOSED to agree; demanding otherwise would flag correct behaviour.
SHARED = "shared"

VARIANCE_KINDS = (INDEPENDENT, POOLED, SHARED)


@dataclass(frozen=True)
class Probe:
    """One observation.

    ``expr`` is evaluated as a JS expression in each of ``realms``. It must be
    side-effect free and must return a JSON-serialisable value or a Promise of
    one. ``note`` is operator-facing only; it never reaches a snapshot.

    ``variance`` says how this vector is expected to behave across two DIFFERENT
    profiles — see the constants above. Like ``note`` it is operator-facing
    metadata that never reaches a snapshot: it describes how to READ a reading,
    it is not itself a reading, and a snapshot recorded before this field
    existed must stay loadable and comparable.

    It defaults to :data:`SHARED`, which is the side that reports nothing. A
    probe added to the inventory tomorrow is therefore never treated as
    must-differ until somebody deliberately classifies it — an unclassified
    vector produces silence rather than a false leak report.
    """

    id: str
    realms: tuple[str, ...]
    expr: str
    note: str = ""
    variance: str = SHARED

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
        if self.variance not in VARIANCE_KINDS:
            raise ValueError(
                f"probe {self.id!r} declares unknown variance {self.variance!r}; "
                f"valid kinds are {list(VARIANCE_KINDS)}"
            )


# --- shared JS snippets -----------------------------------------------------

# A realm-agnostic drawing surface. In a window realm this is a DETACHED canvas
# element (never appended, so no page ever sees it); in a worker it is an
# OffscreenCanvas. Written as an expression so it can be inlined anywhere.
_CANVAS = (
    "((typeof document!=='undefined'&&document.createElement)"
    "?document.createElement('canvas')"
    ":(typeof OffscreenCanvas!=='undefined'?new OffscreenCanvas(300,150):null))"
)


# THE canvas-2D readback: the draw AND its reduction, as ONE shared fragment.
#
# SHARED BECAUSE TWO SURFACES READ IT. This expression is the `canvas.readback`
# probe below, and it is ALSO what the loopback probe page evaluates
# (`local_probe.PROBE_JS`, PS-174). Those two instruments answer different
# questions — the inventory asks "what does this profile read?", the loopback
# page asks "did a change in the layer REACH the page?" — but they must agree on
# what the NUMBER means, so they evaluate the same source rather than two
# hand-copied drafts.
#
# A COPY WOULD BE THE WRONG SHAPE, and specifically it would fail silently. Two
# surfaces reading "canvas" by even slightly different draws (a different font
# string, a band boundary off by one, `toDataURL` instead of `getImageData`)
# produce digests that are individually valid and MUTUALLY INCOMPARABLE — and
# nothing would report that, because each surface's own numbers stay
# self-consistent. The committed readings under `readings/ps135-2026-08-24/`
# are digests of THIS draw; a second draft would quietly stop being the thing
# they measured.
#
# Kept as a bare expression (no realm assumptions, no globals) so it can be
# inlined into a window, an OffscreenCanvas worker, or a page's inline script
# without adaptation. Returns `{digest, bytes, mid}` or null — see the inline
# notes for why each of those is what it is.
CANVAS_READBACK_EXPR = (
    "(function(){"
    "var c=" + _CANVAS + ";"
    "if(!c)return null;"
    "var ctx=null;try{ctx=c.getContext('2d');}catch(e){ctx=null;}"
    "if(!ctx)return null;"
    # A FIXED surface size. `_CANVAS` hands back a 300x150 element in a
    # window (there the HTML default) and a 300x150 OffscreenCanvas in a
    # worker (there a LITERAL, `new OffscreenCanvas(300,150)` at
    # probes.py:112 — not a spec default, so nothing guarantees it stays).
    # Either way the size is the shared surface's, not this probe's, so it
    # is pinned here: a future change to `_CANVAS` must not silently change
    # what this vector reads. Note this WRITES to the surface, which the
    # module docstring's "probes READ" rule would otherwise forbid — it is
    # safe only because `_CANVAS` mints a FRESH element per call, so the
    # mutation cannot outlive this expression or be seen by another probe.
    "var W=64,H=32;"
    "try{c.width=W;c.height=H;}catch(e){return null;}"
    # MID-RANGE fills, for the same reason webgl.readback uses them: a spoof
    # that nudges a byte within a guard band leaves pure black and pure
    # white untouched, so a black-or-white draw would read as a total
    # masking failure while the masking worked perfectly. Four bands rather
    # than one flat fill so the surface carries spatial structure for the
    # position-sensitive digest below. Written as integer rgb() rather than
    # float channels — there is no float->byte conversion to land on a tie.
    "var bands=['rgb(79,115,154)','rgb(140,90,163)',"
    "'rgb(107,160,92)','rgb(168,129,74)'];"
    "for(var b=0;b<bands.length;b++){"
    "ctx.fillStyle=bands[b];ctx.fillRect(0,b*(H/4),W,H/4);}"
    # Text and a curve: the two things a canvas-2D fingerprint actually
    # reads. Band fills alone are flat colour that any renderer reproduces
    # exactly, so a fills-only draw would be stable everywhere and observe
    # nothing — it is glyph rasterisation and antialiasing that carry the
    # host signal, which is the same reason `fonts.measureText` is already
    # ENV-sensitive. Deliberately drawn OVER the bands so the antialiased
    # edges blend against a mid-range background rather than against black.
    "try{ctx.font='14px sans-serif';ctx.fillStyle='rgb(60,90,140)';"
    "ctx.fillText('Persona mMwWgjpq\\u00c9\\u4e2d',2,20);"
    "ctx.strokeStyle='rgb(150,110,80)';ctx.lineWidth=2;"
    "ctx.beginPath();ctx.arc(50,16,9,0,Math.PI*1.5);ctx.stroke();}"
    "catch(e){}"
    # getImageData, NOT toDataURL. Both are canvas-2D readbacks, but
    # toDataURL routes through a PNG encoder, so its output mixes the pixels
    # with a compressor's own choices; getImageData hands back the raw RGBA
    # bytes the renderer produced. Wrapped because a tainted canvas throws
    # SecurityError — this surface is never tainted (nothing external is
    # drawn into it), so the guard is for a future edit, not for today.
    "var d=null;"
    "try{d=ctx.getImageData(0,0,W,H).data;}catch(e){return null;}"
    "if(!d)return null;"
    # FNV-1a over EVERY byte — the same reduction webgl.readback uses, and
    # for the same two reasons. Not a sample: a spoof's touched offsets are
    # not on a fixed comb, and a narrow sample reads a false "no variance".
    # Not a sum: small per-byte deltas make a sum a random walk in which two
    # seeds collide by ARITHMETIC rather than by identity, which would make
    # a vector look POOLED whatever its declaration says. An integer digest,
    # so there is no float formatting for a snapshot comparison to trip on.
    "var h=2166136261,mid=0;"
    "for(var i=0;i<d.length;i++){var v=d[i];"
    "if(v>1&&v<254)mid++;"
    "h=Math.imul(h^v,16777619);}"
    # `mid` is the self-check that keeps a green from being empty: how many
    # bytes were even ELIGIBLE to be nudged. If a future edit makes the draw
    # black or white, `mid` collapses toward the alpha-only floor and the
    # digest stops moving — this says WHICH of the two happened rather than
    # leaving "identical digests" to be read as a masking failure.
    "return {digest:h>>>0,bytes:d.length,mid:mid};"
    "})()"
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

_JS_WEBGL_READBACK = (
    _JS_WITH_GL + "(function(gl){"
    # A deterministic draw with NO shaders: scissored clears only. Shader
    # compilation is the flakiest thing to depend on under a software
    # renderer, and nothing here needs it — the vector is the readback,
    # not the geometry.
    "var W=32,H=32;"
    "try{gl.disable(gl.DEPTH_TEST);}catch(e){}"
    "gl.enable(gl.SCISSOR_TEST);"
    # MID-RANGE colours, deliberately. webgl_ext's perturbBytes nudges a
    # byte only `if (v > 1 && v < 254)` (webgl_ext.py:66), so a black or
    # white surface is returned UNTOUCHED and this probe would read as a
    # total spoof failure while the spoof is working perfectly. Four bands
    # rather than one flat fill so the surface carries spatial structure
    # for the position-sensitive digest below.
    #
    # Every channel is chosen to land OFF a .5 byte boundary once scaled by
    # 255. A float->byte conversion that lands exactly on .5 (0.30 -> 76.5,
    # 0.70 -> 178.5, 0.50 -> 127.5) is a tie, and tie-breaking is not
    # guaranteed to be the same rule on every renderer — so a boundary
    # channel makes the digest look more renderer-portable than it is, and
    # each band here covers 1024 bytes. It does not threaten AC3/AC4 (both
    # sides of a comparison move together), but it costs nothing to remove
    # the ambiguity, so none of these twelve is a tie.
    "var bands=[[0.31,0.45,0.60],[0.55,0.35,0.69],"
    "[0.42,0.62,0.38],[0.66,0.51,0.28]];"
    "for(var b=0;b<bands.length;b++){"
    "gl.scissor(0,b*(H/4),W,H/4);"
    "gl.clearColor(bands[b][0],bands[b][1],bands[b][2],1);"
    "gl.clear(gl.COLOR_BUFFER_BIT);}"
    "gl.disable(gl.SCISSOR_TEST);"
    # Uint8Array / RGBA UNSIGNED_BYTE: perturbBytes returns early on any
    # other destination type (webgl_ext.py:60-63), so a float readback
    # would observe no seed variance at all.
    "var px=new Uint8Array(W*H*4);"
    "gl.readPixels(0,0,W,H,gl.RGBA,gl.UNSIGNED_BYTE,px);"
    # FNV-1a over EVERY byte, not a sum and not a sample.
    #   * Not a sample: `perturbBytes` moves at most `_BUDGET` (512) bytes
    #     (webgl_ext.py:75, :121), and it picks WHICH ones by ordinal among
    #     the bytes that pass the mid-range guard — so the touched offsets
    #     are spread thinly and unpredictably across the whole array rather
    #     than sitting on a fixed comb. A narrow sample reads a false "no
    #     variance". Measured on THIS probe's own 32x32 draw: 3072 of 4096
    #     bytes are eligible and 384 of them move.
    #
    #     This used to read "_STRIDE is 17, so only every 17th byte is
    #     touched at all; 4096 bytes span ~241 touched indices". PS-97
    #     deleted that stride, precisely BECAUSE selecting by BYTE OFFSET is
    #     aliased away by a row width the CALLER chooses: CreepJS's 17x42
    #     corner has a 68-byte row = exactly 4 x 17, so the comb visited four
    #     columns forever and moved ZERO eligible bytes, and two profiles
    #     published one `pixels:` hash. The reason a narrow sample lies is
    #     unchanged; only the reason the touched set is sparse is.
    #   * Not a sum: the deltas are +/-1 each, so a sum is a random walk
    #     over a ~+/-40 range — small enough that two seeds collide by
    #     ARITHMETIC rather than by identity. That is the pigeonhole
    #     property which makes a vector POOLED, and it would have made
    #     this probe INDEPENDENT by declaration and POOLED in behaviour.
    #     A position-sensitive hash makes a collision a real collision.
    "var h=2166136261,mid=0;"
    "for(var i=0;i<px.length;i++){var v=px[i];"
    "if(v>1&&v<254)mid++;"
    "h=Math.imul(h^v,16777619);}"
    # `mid` is the self-check that keeps a green from being empty: it is
    # how many bytes were even ELIGIBLE for perturbation. If a future
    # change makes the draw black or white, `mid` collapses toward the
    # alpha-only floor and the digest stops varying — this says which of
    # the two happened, rather than leaving "identical digests" to be read
    # as a spoof failure. An integer, so no float formatting to defeat.
    "return {digest:h>>>0,bytes:px.length,mid:mid};"
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
    Probe(
        "navigator.hardwareConcurrency",
        BOTH,
        "navigator.hardwareConcurrency",
        # device_ext.py:218 picks the (cores, GB) pair from a SIX-entry pool.
        # Seed-derived, so it does vary — but two profiles colliding is one
        # chance in six, which is pigeonhole, not a linkable identity.
        variance=POOLED,
    ),
    Probe(
        "navigator.deviceMemory",
        BOTH,
        "(navigator.deviceMemory===undefined?null:navigator.deviceMemory)",
        # Same six-entry pool, then capped at 8 (device_ext.py:222), which
        # collapses it further: agreement is close to expected.
        variance=POOLED,
    ),
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
        # `resolution` is an OPERATOR-SET field (profile.py:15-16). An explicit
        # "WIDTHxHEIGHT" is honoured verbatim for every profile that names it
        # (resolution.py:45-52), so two profiles agreeing here is very often
        # the operator's own choice; only "auto" consults the seed, and then
        # from a small preset list.
        variance=POOLED,
    ),
    Probe(
        "screen.devicePixelRatio",
        WINDOW_ONLY,
        "Math.round(devicePixelRatio*1e6)/1e6",
        # Effectively a two-value vector in practice (1 on Windows, 2 on the
        # macOS Retina preset, device_ext.py:277-279) and selected by os_type,
        # which is operator-set.
        variance=POOLED,
    ),
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
        # NOT must-differ, and this is the trap worth naming. gpu_ext.py's
        # `var POOL =` gate picks the vendor/renderer pair from a per-OS pool,
        # so a collision is ordinary pigeonhole — but iOS does not even do
        # that: its `var IOS_GPU =` constant pins ONE pair for every iOS
        # profile on earth, because "a seed-varied one would itself be the
        # tell" (the `build_gpu_extension` docstring).
        # Two iOS profiles MUST agree here. Demanding difference would flag
        # persona's most deliberately correct behaviour as a leak.
        variance=POOLED,
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
    Probe(
        "webgl.readback",
        # WINDOW_ONLY, and this is a correctness constraint rather than a
        # preference. MEASURED in a worker on this engine: OffscreenCanvas
        # exists, but `getContext('webgl')` returns null and only 'webgl2'
        # yields a context — so _JS_WITH_GL, which asks for 'webgl' then
        # 'experimental-webgl', returns null there. A null is RECORDED as
        # {"value": null}, and diff._unread keys on the PRESENCE of "value",
        # so two profiles both reading null compare EQUAL and are reported
        # COLLIDING. On a SHARED probe that is harmless (never compared); on
        # an INDEPENDENT one it manufactures a false leak report on every
        # pair. Declaring the realm we can actually read keeps the must-differ
        # axis free of readings that are unobtainable by construction.
        WINDOW_ONLY,
        _JS_WEBGL_READBACK,
        # THE SECOND must-differ vector. webgl_ext.py adds a deterministic
        # per-(seed, byte-offset) +/-1 delta to a bounded, content-selected set
        # of bytes in a byte-typed readPixels result (webgl_ext.py:5-7,
        # :121-194) precisely BECAUSE the
        # GPU-less VM renders through SwiftShader, where the real pixels
        # collide across profiles and link them. Continuous and seed-derived,
        # not drawn from a pool, so two DISTINCT seeds agreeing here is not
        # pigeonhole — it is linkability.
        #
        # OBSERVED, not argued (this is why the classification is safe to
        # make): under ANGLE/SwiftShader with the extension loaded, seeds
        # 111/222/333 produced three distinct digests, each distinct from the
        # unspoofed baseline, and seed 111 reproduced bit-identically on a
        # second fresh profile.
        variance=INDEPENDENT,
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
        # The context's nominal rate, requested as 44100 by this very probe.
        # A constant, not an identity: every profile must report it.
        variance=SHARED,
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
        # THE must-differ vector, and on this inventory the only one. audio_ext
        # adds a per-(seed, index) delta to the float readback (audio_ext.py:10,
        # :124-133) with a ~1e-5 relative magnitude, and this probe reduces 500
        # perturbed samples to a 6dp sum — continuous, not drawn from a pool, so
        # two DISTINCT seeds colliding is not pigeonhole. Two profiles agreeing
        # on this digest are linkable on it.
        variance=INDEPENDENT,
    ),
    # --- canvas 2D ----------------------------------------------------------
    Probe(
        "canvas.readback",
        # BOTH — declared on an INDEPENDENT record (see `variance` below), so
        # the cost of a worker realm that cannot read is paid on the
        # cross-profile axis and is stated here rather than left to be found.
        #
        # WHAT A WORKER-REALM null COSTS. Not a false COLLIDING: that failure
        # mode is already handled generally by `diff._unread_for_unlinkability`
        # (diff.py:295), which — unlike `_unread` — maps a {"value": null} to
        # UNREAD on the must-differ axis, so two profiles that both failed to
        # read are INCONCLUSIVE rather than reported linkable. That guard is
        # pre-existing and is what makes BOTH admissible here at all; it is NOT
        # the `WINDOW_ONLY` declaration on webgl.readback, which predates it and
        # answers a different question (a context that is absent BY
        # CONSTRUCTION in that realm on that engine — measured, not feared).
        #
        # What it does cost is the whole verdict. `compare_profiles` walks the
        # INVENTORY, so this probe contributes a worker row on every run; one
        # inconclusive entry sends `_run_two_profile_unlinkability` to
        # CANNOT_RUN (behaviour_checks.py:216-220), discarding a genuine and
        # complete pass on `audio.digest` and `webgl.readback` alongside it.
        # Measured, not argued: two profiles differing correctly on every window
        # vector, with only the worker canvas row null, yield
        #     entries: [('worker', 'canvas.readback', 'inconclusive')]
        #     verdict: CANNOT_RUN
        # and this is pinned by a test (see
        # test_a_worker_realm_null_on_this_probe_degrades_the_unlinkability_gate)
        # so the claim is enforced rather than asserted. There are five paths
        # that return null below (no canvas, no 2D context, a throwing `width`
        # assignment, a throwing getImageData, empty data), so this is a
        # reachable state and not a theoretical one.
        #
        # WHY BOTH IS STILL RIGHT. The worker reading was OBTAINED on both
        # engines actually measured — firefox-20 and chromium recorded a real
        # digest in the worker realm on every seed (readings/ps135-2026-08-24/),
        # so the degradation above is a contingency, not the expected path. And
        # whether the delegated C++ canvas patch reaches a worker at all is
        # precisely the question this probe exists to answer: declaring
        # WINDOW_ONLY to dodge a null that was not observed would silence the
        # measurement to protect a verdict, which is the inversion this
        # subsystem is built to refuse. If a host is later found where the
        # worker realm genuinely cannot give a 2D context, the honest response
        # is the same one webgl.readback got — narrow the realm ON THE MEASURED
        # ENGINE — not to pre-emptively stop looking.
        #
        # NOTE FOR THE NEXT READER CHOOSING REALMS: `_unread_for_unlinkability`
        # closes at diff.py:332-338 with the observation that no INDEPENDENT
        # probe has a legitimate null reading. This probe is the first
        # INDEPENDENT record that contemplates one, and that note now says so.
        BOTH,
        CANVAS_READBACK_EXPR,
        note=(
            "Deterministic 2D draw (four mid-range bands, text, a stroked arc) "
            "on the shared detached surface, reduced by FNV-1a over the raw "
            "getImageData bytes. Reads the vector persona delegates to "
            "fingerprint-chromium's C++ patch and never itself spoofs."
        ),
        # INDEPENDENT — and this is a READING, not a reflex. Every number below
        # was measured live on PS-135 and is committed under
        # readings/ps135-2026-08-24/. The two engines DISAGREE, so they are
        # stated separately: neither is evidence about the other.
        #
        # CHROMIUM (the delegated C++ patch, driven by --fingerprint=,
        # process.py:561) — five seeds, five DISTINCT digests:
        #     111 -> 381336052    222 -> 1832625859   333 -> 2076010582
        #     1337 -> 2838771797  4242 -> 2455437942
        #   Not a small pool, so not POOLED. Stable: seed 1337 re-read on a
        #   fresh profile dir returned 2838771797 bit-identically, so the
        #   variation is per-PROFILE and not per-LAUNCH noise (a random vector
        #   satisfies "two profiles differ" while making one profile
        #   unrecognisable to itself, which is a different leak, not a fix).
        #   COUNTERFACTUAL, which is what makes this evidence about the FLAG
        #   rather than a correlation: with --fingerprint= REMOVED, seed args
        #   1337 and 4242 both read 2616755061 — the same value. The entropy
        #   is caused by the flag.
        #
        # FIREFOX (the packaged engine, firefox-20) — three seeds, ONE digest:
        #   111, 1337 and 4242 all read 4242351214, in BOTH realms. That is an
        #   observed COLLISION, and the control is what makes it a statement
        #   about canvas rather than about the harness: in the very same
        #   snapshots `audio.digest` DOES move per seed (35.749981 vs
        #   35.749964), along with webgl.readback, webgl.unmasked,
        #   webgl.parameters and navigator.hardwareConcurrency. The masking
        #   layer was live and correctly seeded; canvas 2D simply is not
        #   spoofed there, because the --fingerprint= flag is Chromium-only and
        #   the Firefox arm returns at process.py:353 well before it.
        #
        # WHY INDEPENDENT AND NOT SHARED, given that half the matrix collides.
        # SHARED is not the neutral choice here — it is a positive claim that a
        # vector is "not seed-derived at all", which the Chromium counterfactual
        # shows to be FALSE. It would also be self-erasing: SHARED probes are
        # skipped by `compare_profiles` entirely, so the Firefox collision this
        # ticket exists to expose would be recorded once and then never
        # reported again by the machinery. The classification says how the
        # vector MUST behave across two profiles; it is not a summary of how
        # well it currently behaves on the weakest engine.
        #
        # THE CONSEQUENCE, STATED RATHER THAN DISCOVERED: this makes two
        # Firefox profiles report COLLIDING on window and worker, which turns
        # the two-profile unlinkability check to FINDING on that engine. That
        # finding is TRUE — the digests really are identical and really are
        # linkable — so it is a report, not the fabrication the SHARED default
        # guards against. Fixing it is out of scope here (per the ticket, a
        # canvas spoof is a finding for PS-2); this probe's job is to stop the
        # fact being invisible. Precedent: `audio.digest` (PS-73) and
        # `webgl.readback` (PS-78) were both classified INDEPENDENT while
        # Firefox still collided on them, and in both cases the collision was
        # reported and then fixed — audio.digest now varies per seed above.
        variance=INDEPENDENT,
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
        # What a DETECTOR can find of persona's realm machinery — which must be
        # nothing. This probe used to read the registry by name
        # (__pnaBoots/__pnaBootSrc/__pnaBooted/__pnaBootInstalled) and report
        # its size, which made it a presence check on globals that PS-48
        # deleted: the leaf source text stored in __pnaBootSrc carried the
        # profile seed compiled inside each leaf, so one property read gave a
        # page both positive tool identification and the identity itself.
        #
        # So it now asserts ABSENCE instead, and reports the LEAKED NAMES rather
        # than a count — a non-empty `markers` here is the regression. The
        # standard is the in-tree one: tests/test_ff_language_override.py pins
        # `"__pnaName" not in cloak`, and tests/native_mask_probe.py checks the
        # observable property rather than the presence of a marker.
        "(function(){"
        "var g=self,markers=[];"
        "try{Object.getOwnPropertyNames(g).forEach(function(k){"
        "  if(/^__pna|^__persona/.test(k))markers.push(k);});}catch(e){}"
        "return {markers:markers,markerCount:markers.length};})()",
    ),
    Probe(
        "realm.seedRecoverable",
        BOTH,
        # The other half of PS-48, and the one that matters most: can a script
        # standing in this realm recover the profile seed at all? The seed is
        # compiled INSIDE each masking leaf on purpose (gpu_ext.py, webgl_ext.py
        # both say so in place) so that stringifying the leaf carries it across a
        # realm boundary — which is exactly why a readable reference to a leaf,
        # or to its source, published the identity.
        #
        # Reports the NAMES of any own property of the global object that is, or
        # transitively stringifies to, something holding a long digit run. It
        # cannot know the profile's actual seed (this file is profile-agnostic),
        # so it reports candidates and their shape; an empty list is the healthy
        # reading, and any entry is worth a human look.
        "(function(){"
        "var g=self,hits=[];"
        "try{Object.getOwnPropertyNames(g).forEach(function(k){"
        "  var v;try{v=g[k];}catch(e){return;}"
        "  try{"
        "    if(typeof v==='function'&&/\\d{5,}/.test(Function.prototype.toString.call(v)))"
        "      {hits.push(k);return;}"
        "    if(Array.isArray(v)){"
        "      for(var i=0;i<v.length&&i<64;i++){"
        "        var e0=v[i];"
        "        var s=(typeof e0==='function')?Function.prototype.toString.call(e0)"
        "              :(typeof e0==='string'?e0:'');"
        "        if(/\\d{5,}/.test(s)){hits.push(k);return;}}}"
        "  }catch(e){}});}catch(e){}"
        "return {candidates:hits,candidateCount:hits.length};})()",
    ),
    Probe(
        "realm.kind",
        BOTH,
        "(function(){"
        "return {hasDocument:(typeof document!=='undefined'),"
        "hasWindow:(typeof window!=='undefined'),"
        "ctor:(self.constructor&&self.constructor.name)||null};})()",
    ),
    Probe(
        "realm.frameIdentity",
        EVERY_REALM,
        # WHERE this realm sits in the frame tree — the one vector the top realm
        # and a child realm are REQUIRED to disagree on.
        #
        # That requirement is the whole reason this record exists, and it is a
        # deliberate answer to the trap the rest of this inventory walks into:
        # every other probe here is seed-derived and deterministic, so a child
        # realm reporting the same value as the window realm is the EXPECTED
        # reading whether or not the harness ever entered the frame. Agreement
        # proves nothing. Frame identity cannot agree — a realm that is its own
        # top and a realm that is not are distinguishable by construction — so a
        # divergence on this record is positive evidence that a distinct realm
        # was actually entered, and its absence is a defect rather than a shrug.
        #
        # Every read is individually guarded because the three realms genuinely
        # differ in what they expose: a Worker has no `top` and no `parent` at
        # all, so it reports nulls rather than inventing a depth. Determinism
        # holds in each realm (probes.py's hard requirement): the readings are
        # a fixed shape per realm, and the walk is bounded at 64 so a cyclic or
        # hostile parent chain cannot hang the run.
        "(function(){"
        "var hasTop=false,hasParent=false,selfIsTop=null,depth=null;"
        "try{hasTop=(typeof self.top!=='undefined'&&self.top!==null);}catch(e){}"
        "try{hasParent=(typeof self.parent!=='undefined'&&self.parent!==null"
        "&&self.parent!==self);}catch(e){}"
        "if(hasTop){try{selfIsTop=(self===self.top);}catch(e){selfIsTop=null;}}"
        "if(hasTop){try{var n=0,w=self;while(n<64){var p=null;"
        "try{p=w.parent;}catch(e){break;}"
        "if(!p||p===w){break;}n++;w=p;}depth=n;}catch(e){depth=null;}}"
        "return {hasTop:hasTop,hasParent:hasParent,"
        "selfIsTop:selfIsTop,frameDepth:depth};})()",
        note=(
            "Realm position in the frame tree. Window and child_frame MUST "
            "disagree here; that disagreement is what proves a child realm was "
            "entered rather than assumed."
        ),
    ),
    Probe(
        # PS-232. THE SAME VECTOR, DECLARED IN THE CHILD REALM — as a NEW
        # record rather than as a realm added to the one above, and that shape
        # is the whole point of this record rather than an incidental choice.
        #
        # `compare_profiles` - the Level 2 unlinkability gate - builds its work
        # list from `must_differ_probes() x probe.realms`, so it can only ask
        # about a realm some INDEPENDENT record declares. Before this record
        # existed that list held four pairs and NONE was in a child realm: the
        # realm was structurally outside the unlinkability question. PS-193
        # MEASURED a real Level 2 failure in exactly this realm on exactly this
        # vector - CreepJS built a phantom iframe, took it by INDEXED access
        # (`self[N]`), which never invokes the `contentWindow` accessor our
        # chain hooked, and Firefox's `creepjs :: webgl_pixel_hash` received the
        # unperturbed buffer, bit-identical to the unspoofed baseline, across
        # 4 seeds / 4 exits / 3 days. A collision was measured here, the realm
        # was added to the harness for it, and the gate deciding "are these two
        # profiles distinguishable?" still could not look at it.
        #
        # WHY A SEPARATE RECORD AND NOT `webgl.readback` GAINING A REALM.
        # PS-210 chose "the new realm arrives as a NEW record" deliberately, so
        # that no existing vector silently starts being evaluated somewhere it
        # was never validated, and it installed a guard to enforce that. The
        # two records share EXPRESSION but not IDENTITY: `_JS_WEBGL_READBACK`
        # is one source of truth, so the charter's one-record-per-vector
        # objection to a duplicated expression does not apply - there is no
        # second copy to drift. What is duplicated is the DECLARATION, which is
        # exactly the thing that has to be per-realm for the realm to be
        # validated on its own evidence.
        #
        # CHILD_FRAME_ONLY, not (WINDOW, CHILD_FRAME). Declaring the window
        # realm here would add this id to `probes_for_realm("window")`, which
        # the committed baseline records exactly - tripping
        # test_the_committed_baseline_records_exactly_the_live_probe_inventory
        # for a reading the window realm already has under the id above. The
        # window reading is not missing; it belongs to the other record.
        #
        # NOT WORKER, for the reason the record above gives at length: in a
        # worker `getContext('webgl')` returns null, a null is recorded as
        # {"value": null}, and two profiles both reading null compare EQUAL and
        # are reported COLLIDING. A child frame has a DOCUMENT, so `_CANVAS`
        # takes its `document.createElement('canvas')` branch rather than the
        # OffscreenCanvas branch that reads null in a worker. That is the
        # mechanical reason this vector is expressible here and not there, and
        # it was MEASURED before this record was declared, not assumed: in a
        # genuinely entered child realm (chromium, indexed reach, the
        # `contentWindow` accessor instrumented and counted at zero) this
        # expression returns a real reading, byte-identical across two
        # consecutive records - the determinism this module demands.
        "webgl.readback.childFrame",
        CHILD_FRAME_ONLY,
        _JS_WEBGL_READBACK,
        note=(
            "The webgl readback vector, read in a same-origin CHILD realm "
            "reached by indexed access. Shares its expression with "
            "webgl.readback; separate record so the child realm is validated "
            "on its own evidence rather than inheriting the window realm's."
        ),
        variance=INDEPENDENT,
    ),
)


def probe_ids() -> tuple[str, ...]:
    return tuple(p.id for p in PROBES)


def probes_for_realm(realm: str) -> tuple[Probe, ...]:
    """Every probe that declares ``realm``, in inventory order."""
    return tuple(p for p in PROBES if realm in p.realms)


def probes_with_variance(kind: str) -> tuple[Probe, ...]:
    """Every probe classified ``kind``, in inventory order."""
    if kind not in VARIANCE_KINDS:
        raise ValueError(f"unknown variance {kind!r}; valid kinds are {list(VARIANCE_KINDS)}")
    return tuple(p for p in PROBES if p.variance == kind)


def must_differ_probes() -> tuple[Probe, ...]:
    """The probe RECORDS two distinct profiles must not agree on.

    Returns records rather than ids because the comparator needs each probe's
    declared ``realms`` as well as its id: it walks the inventory's realms for
    a target instead of intersecting whatever realms two files happen to carry,
    so a vector MISSING from a snapshot is still compared (and reported
    inconclusive) rather than silently skipped. Driving that off ``realms``
    keeps a window-only vector from being reported absent in the worker realm,
    where the inventory never asked for it — the same rule ``diff_realms``
    states for realm parity.
    """
    return probes_with_variance(INDEPENDENT)


def must_differ_ids() -> frozenset[str]:
    """Ids of the vectors two DISTINCT profiles must not agree on.

    Derived from the inventory rather than listed anywhere, so classifying a
    probe is done by editing its record — ``probes.py:8`` — and the
    cross-profile comparator cannot drift out of step with the inventory.
    """
    return frozenset(p.id for p in must_differ_probes())


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
    "probes_with_variance",
    "must_differ_ids",
    "must_differ_probes",
    "INDEPENDENT",
    "POOLED",
    "SHARED",
    "VARIANCE_KINDS",
]
