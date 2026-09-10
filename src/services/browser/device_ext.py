"""MAIN-world extension that gives each profile a believable, deterministic
screen geometry and mediaDevices set.

The engine spoofs deviceMemory/hardwareConcurrency but not the screen
(screen.width/height/availWidth/availHeight, colorDepth, devicePixelRatio) nor
navigator.mediaDevices.enumerateDevices(). On a VM the screen is the host's and
identical across every profile, and enumerateDevices() returns a bare,
camera-less set — both are detection signals (no per-profile entropy; "no
camera => server/VM"). This extension picks, deterministically from the
profile seed, a common real desktop resolution (with a realistic taskbar-inset
availHeight) and a plausible device list with stable per-profile deviceId /
groupId hashes.
"""

import json
import pathlib
from dataclasses import dataclass

from ...models.hardware_generation import normalize_generation, visible_entries
from .worker_wrap import (
    chromium_leaf_cloak_js,
    realm_bootstrap_js,
    realm_guard_js,
    realm_slot_js,
)


@dataclass(frozen=True)
class CoresMemoryEntry:
    """One plausible consumer-desktop (cores, GB-RAM) pair, tagged with the
    generation it was added in.

    ``since`` is a promise to the profiles already pinned to this entry, not
    bookkeeping: never renumber it on a shipped entry. New entries get the
    bumped ``CURRENT_HARDWARE_GENERATION``. See ``hardware_generation.py``.
    """

    cores: int
    memory_gb: int
    since: int = 0

    @property
    def pair(self) -> tuple[int, int]:
        return (self.cores, self.memory_gb)


# navigator.hardwareConcurrency / navigator.deviceMemory pairs. This pool is the
# SINGLE source of truth for both realms: the page-realm pick and the worker-realm
# twin inside applyHwPatch are both rendered from this list, so they cannot drift
# apart. They used to be two hand-maintained JS literals that had to be kept in
# sync by eye, and the worker copy silently shadowed the page copy.
#
# ADDING ONE: give it `since=<CURRENT_HARDWARE_GENERATION after you bump it>` and
# leave every entry below untouched. Order is free to stay readable — the
# generation filter is by tag, not by position.
CORES_MEMORY: list[CoresMemoryEntry] = [
    CoresMemoryEntry(4, 8),
    CoresMemoryEntry(8, 8),
    CoresMemoryEntry(8, 16),
    CoresMemoryEntry(12, 16),
    CoresMemoryEntry(16, 16),
    CoresMemoryEntry(6, 8),
]


def cores_memory_for_generation(generation: int) -> list[tuple[int, int]]:
    """The (cores, GB-RAM) pool a profile of ``generation`` picks from.

    The emitted JS divides by the length of THIS, never of the whole list —
    taking the whole list's length is the original defect.
    """
    return [e.pair for e in visible_entries(CORES_MEMORY, generation)]


#: The salt the emitted page script uses when it picks from the cores/RAM pool
#: (``pick(HCMEM, 0xc0de5)``). Named here so the Python resolver below and the
#: JS cannot drift to two different constants.
CORES_MEMORY_SALT = 0xC0DE5


def _h32(seed: int, salt: int) -> int:
    """The emitted script's ``h32``, in Python.

    A LINE-FOR-LINE port of the JS at the top of ``_CONTENT_SCRIPT``::

        var h = SEED ^ (x | 0);
        h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
        h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
        return (h ^ (h >>> 16)) >>> 0;

    ``Math.imul`` is 32-bit-truncating signed multiply and ``>>>`` is a LOGICAL
    shift, so every step masks to 32 bits — a Python ``*`` or ``>>`` without the
    mask silently diverges on large seeds, which is the whole reason this is
    written out rather than approximated.

    ⚠️ THIS IS A SECOND IMPLEMENTATION OF A RULE THAT ALREADY EXISTS IN JS, and
    that is a drift hazard by construction. It is justified only because the
    ENGINE needs the answer before any JS runs (see
    :func:`hardware_concurrency_for`). ``test_ps354_service_worker_cores.py``
    pins the two against each other by executing the REAL emitted script in
    node and comparing, over many seeds and every generation — so a change to
    either side fails rather than producing two quietly different profiles.
    """
    h = (seed ^ (salt & 0xFFFFFFFF)) & 0xFFFFFFFF
    h = ((h ^ (h >> 16)) * 0x85EBCA6B) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 0xC2B2AE35) & 0xFFFFFFFF
    return (h ^ (h >> 16)) & 0xFFFFFFFF


def cores_memory_pick(seed: int, generation: int) -> tuple[int, int]:
    """The (cores, GB-RAM) pair THIS profile resolves to — the page realm's own
    pick, computed in Python.

    ⛔ THE POOL IS GENERATION-FILTERED AND THE DIVISOR IS THE FILTERED LENGTH.
    Taking ``CORES_MEMORY`` whole, or its first entry, is the original defect
    this module's comments describe: it re-indexes existing profiles onto a
    different machine the moment anyone appends to the pool.
    """
    pool = cores_memory_for_generation(generation)
    return pool[_h32(int(seed) & 0xFFFFFFFF, CORES_MEMORY_SALT) % len(pool)]


def hardware_concurrency_for(seed: int, generation: int) -> int:
    """The value to pass the ENGINE as ``--fingerprint-hardware-concurrency``.

    WHY THE ENGINE NEEDS TO BE TOLD AT ALL (PS-354). ``applyHwPatch`` carries
    ``hardwareConcurrency`` into Web and Shared Workers, but a
    ``ServiceWorkerGlobalScope`` is reached by NEITHER of persona's identity
    authors: it is never CONSTRUCTED by the page, so there is no constructor for
    ``worker_wrap``'s chaining to intercept, and an MV3 content script does not
    run there. The realm therefore fell through to the engine's own seed
    fallback, or on arms the engine does not spoof, to the HOST. PS-189
    measured that directly — a linux service worker reported the host's
    SwiftShader while ELEVEN sibling realms in the same launch reported the
    profile's card.

    The engine flag authors the value before any of our code runs, so it covers
    every realm INCLUDING the service worker natively — no wrapper, no
    descriptor, no residue in a realm we otherwise never touch.

    ⛔ THE VALUE MUST EQUAL THE PAGE REALM'S PICK, BY CONSTRUCTION. Passing
    anything else — a constant, the pool's first entry, the host's real count —
    does not fix the defect: it REPLACES a page/worker mismatch with a
    page/engine mismatch, which is the same tell in a different place. Hence
    this reads the identical pool through the identical hash with the identical
    salt, rather than restating a number that happens to match today.
    """
    return cores_memory_pick(seed, generation)[0]


@dataclass(frozen=True)
class ScreenResolutionEntry:
    """One logical (CSS-px) screen resolution for the emitted ``ALL_RES`` pool,
    tagged with the generation it was added in.

    ``since`` is a promise to the profiles already pinned to this entry, not
    bookkeeping: never renumber it on a shipped entry. New entries get the
    bumped ``CURRENT_HARDWARE_GENERATION``. See ``hardware_generation.py``.

    This is deliberately NOT ``resolution.ResolutionEntry`` even though the two
    carry the same three fields — see ``SCREEN_RES_POOLS`` below for why the
    two pools are separate objects, and for the parity test that keeps the
    windows arm from drifting away from ``DESKTOP_RESOLUTIONS``.
    """

    width: int
    height: int
    since: int = 0

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)


# THE SCREEN-RESOLUTION POOLS THAT ACTUALLY SET ``screen.width`` (PS-264).
#
# These two lists used to be a JS array literal inside ``_CONTENT_SCRIPT``, and
# that made them the last seed-indexed pick site no census guard could iterate:
# an UNTAGGED append passed the whole suite while re-indexing 18 of 20 gen-0
# profiles onto a different monitor, on BOTH arms (measured at a1cc9d4 through
# the emitted device.js under node:vm — 90.0% / 90.0%). They are lifted here for
# exactly the reason PS-190 lifted the four GPU pools: a regex over the emitted
# script could only ever find the pools whose formatting it already anticipated,
# so the pool is turned into DATA and the script is rendered FROM it.
#
# WHICH POOL DRIVES WHAT. ``process.py`` passes
# ``resolution=parse_resolution(profile.resolution)``, which is ``None`` for an
# "auto" profile — so ``FORCED`` is null and it is THIS pool, not
# ``resolution.DESKTOP_RESOLUTIONS``, that the page ends up reading
# ``screen.width`` from. ``DESKTOP_RESOLUTIONS`` drives the real WINDOW extent
# (and an explicit "WIDTHxHEIGHT" profile), which is a different question.
#
# WHY A SEPARATE ``WIN_SCREEN_RESOLUTIONS`` RATHER THAN REGISTERING
# ``DESKTOP_RESOLUTIONS`` ITSELF (PS-264 asked for this choice to be stated):
# the two lists are byte-identical today, and they are still kept apart, for
# three reasons. (1) They answer different questions and are free to diverge:
# this one is a MONITOR pool for a spoofed ``screen``, that one is a WINDOW-size
# pool, and the macOS arm here already has no counterpart there at all. (2)
# Registering a pool from ``resolution.py`` in this module's registry would make
# an append to the window pool silently move every auto profile's spoofed
# screen — coupling two maintenance decisions that a maintainer makes for
# different reasons. (3) The census guard pins each registered pool's
# generation-0 contents independently, which a shared object cannot express.
# The cost of the choice is the drift hazard ``CORES_MEMORY``'s docstring above
# records having already been bitten by, so it is PAID FOR by an explicit
# parity test —
# ``test_the_windows_screen_pool_matches_desktop_resolutions_at_generation_zero``
# in tests/test_hardware_generation.py — which fails if either copy's
# GENERATION-0 baseline is edited without the other. It is pinned at generation
# 0 rather than over the whole list on purpose: a whole-list equality goes RED
# on the documented correct edit (a tagged append to the monitor pool alone,
# which a maintainer is entitled to make), and a guard that fires on the
# correct edit trains people to work around it. Two hand-maintained copies with
# a test between them; not two hand-maintained copies kept in sync by eye.
#
# ADDING ONE: give it `since=<CURRENT_HARDWARE_GENERATION after you bump it>`
# and leave every entry below untouched. Order is free to stay readable — the
# generation filter is by tag, not by position.
WIN_SCREEN_RESOLUTIONS: list[ScreenResolutionEntry] = [
    ScreenResolutionEntry(1366, 768),
    ScreenResolutionEntry(1440, 900),
    ScreenResolutionEntry(1536, 864),
    ScreenResolutionEntry(1600, 900),
    ScreenResolutionEntry(1920, 1080),
    ScreenResolutionEntry(1680, 1050),
    ScreenResolutionEntry(1920, 1200),
    ScreenResolutionEntry(2560, 1080),
    ScreenResolutionEntry(2560, 1440),
]

# macOS panel sizes (MacBook Air/Pro + Studio Display), in logical CSS px. This
# arm has NO Python twin anywhere else in the tree — before PS-264 it existed
# only inside the JS string, and every behavioural generation test drove the
# default ``os_type="windows"``, so nothing exercised it. Its divisor is 5,
# narrower than the windows arm's 9, which makes an untagged append here
# sharper rather than milder.
MAC_SCREEN_RESOLUTIONS: list[ScreenResolutionEntry] = [
    ScreenResolutionEntry(1440, 900),
    ScreenResolutionEntry(1512, 982),
    ScreenResolutionEntry(1680, 1050),
    ScreenResolutionEntry(1728, 1117),
    ScreenResolutionEntry(2560, 1440),
]


# THE POOL REGISTRY, AND WHY IT IS A REGISTRY RATHER THAN TWO NAMES.
#
# The guards in tests/test_hardware_generation.py iterate THIS mapping, so they
# cover the pool CLASS rather than the two arms someone happened to think of: a
# third arm (a linux screen pool, say) registered here is picked up by the
# existing tests with no edit to them at all, and FAILS until its shipped
# contents are pinned in the generation-0 census. That is the same property
# PS-190 established for ``gpu_ext.GPU_POOLS``, and it is only load-bearing
# because ``build_device_extension`` renders the emitted JS THROUGH this
# mapping: a pool that is not registered is not emitted.
SCREEN_RES_POOLS: dict[str, list[ScreenResolutionEntry]] = {
    "WIN_SCREEN_RESOLUTIONS": WIN_SCREEN_RESOLUTIONS,
    "MAC_SCREEN_RESOLUTIONS": MAC_SCREEN_RESOLUTIONS,
}


def screen_resolutions_for_generation(
    pool: list[ScreenResolutionEntry], generation: int
) -> list[ScreenResolutionEntry]:
    """The entries a profile of ``generation`` may be picked onto.

    The emitted JS divides by the length of THIS, never of the whole list —
    taking the whole list's length is the original defect.
    """
    return visible_entries(pool, generation)


def _render_screen_pool(pool: list[ScreenResolutionEntry]) -> str:
    """Render a screen-resolution pool as the JS array literal the template
    substitutes.

    Rendered UNFILTERED, three-element rows, with each entry's ``since``
    carried through — mirroring ``gpu_ext._render_pool`` and NOT the
    pre-filtered ``__HCMEM__`` render above. The emitted JS keeps its own
    ``RES = ALL_RES.filter(r[2] <= GEN)``, so pre-filtering here would change
    the emitted shape and drop the third element.
    """
    return json.dumps([[e.width, e.height, e.since] for e in pool])


# Common real desktop resolutions (StatCounter-ish top set). Picking from a
# real-world distribution keeps each profile plausible while differing between
# profiles. availHeight subtracts a typical Windows taskbar (40px); availWidth
# stays full — matching how real Windows reports it.
#
# THE SCREEN GEOMETRY CROSSES REALMS THROUGH THE PER-REALM SLOT (PS-139), not
# through a global. It used to be published as a plain `top.__personaScreenWH`,
# which is an ENUMERABLE property of the global object: one `Object.keys(window)`
# in any realm at any depth handed the page the profile's resolved screen
# geometry. It now rides `Object.__pnaRealm` (worker_wrap.realm_slot_js), which
# is non-enumerable and already shipped for the idempotency guard — so this adds
# NO new global name and removes one.
#
# THIS NOTE IS PYTHON, DELIBERATELY, and must not migrate into the template
# below. The template's text IS the shipped artifact: a JS comment naming the
# retired channel would keep `__personaScreenWH` present in device.js, where a
# source-text assertion (tests/test_realm_guard.py sweeps for exactly this)
# would read it and go green on a build that no longer has the behaviour. Keep
# the history here, where it explains the code without shipping the string.
#
# WHAT ACTUALLY CROSSES, since the `top` hop is unchanged and so is the
# reachability — this is not a frame-isolation improvement:
#   * same-origin CHILD FRAME -> reads the top's slot. This is the channel the
#     value exists for: every realm must report ONE monitor, and realms that
#     disagree are a worse tell than the global this replaces.
#   * cross-origin child      -> cannot. It could not read the global either.
#   * WORKER                  -> cannot, and never did: WorkerGlobalScope has no
#     `top`. Nothing regresses here; the gap is real, unchanged, tracked apart.
_CONTENT_SCRIPT = r"""
(function () {
  // The OUTER IIFE's own cloak, for the readable `def()` below. Each of the
  // three leaves carries its OWN copy inside its body (the body is what
  // crosses realms as source text, so a map out here is undefined there).
  var G = (typeof self !== "undefined") ? self : this;
__IIFE_LEAF_CLOAK__
  var SEED = __SEED__;

  function h32(x) {
    var h = SEED ^ (x | 0);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    return (h ^ (h >>> 16)) >>> 0;
  }
  function pick(arr, salt) { return arr[h32(salt) % arr.length]; }

  // NOTE: this IIFE used to define a top-level `nativeWrap`. Its LAST callsite
  // was the `mediaDevices.enumerateDevices` install, which PS-320 moved into
  // `applyDevicesPatch` — and a leaf carries its OWN wrapper (`nw`) inside its
  // body, because the body is what crosses realms. So the readable copy became
  // ~10 lines of unreachable JS shipped into every realm, and it is deleted
  // rather than left. Each of the three leaves below declares its own minified
  // `nw`; there is no shared one, deliberately. This is not tidiness: a dead
  // readable copy is exactly what let a PS-314 falsification arm pass against
  // code that never runs, one function over in this same file.
  function def(obj, prop, val) {
    try {
      // A REAL ACCESSOR, pulled back out of an object literal — not a function
      // expression. An expression getter owns `prototype`/`arguments`/`caller`
      // and reads `.name === "getter"`; a native accessor owns exactly
      // ["length","name"] with `.name === "get <prop>"` (cf. Map#size). Both
      // are readable without calling anything, and `delete` cannot repair the
      // first (non-configurable), so the form must be right at creation.
      var getter = Object.getOwnPropertyDescriptor(
        { get m() { return val; } }, 'm').get;
      // Native-looking getters (a masking detector may stringify the accessor).
      // `name` is pinned as a PAIR with the marker: the marker is what the
      // toString cloak reads, and `name` is a SECOND, independent axis that
      // would otherwise leak the persona-internal identifier "getter".
      try {
        Object.defineProperty(getter, 'name', { value: 'get ' + prop });
      } catch (e) {}
      // ⛔ WeakMap, not an own `__pnaName` (PS-368): an own marker made every
      // accessor read a third name under `Object.getOwnPropertyNames`, which
      // is persona identification in one line and independent of the toString
      // cloak it existed to serve.
      //
      // ⚠️ THE `get ` PREFIX IS PART OF THE STRINGIFIED NAME ON V8 — measured
      // off `Object.getOwnPropertyDescriptor(Map.prototype,'size').get`, whose
      // source text reads `function get size() ...`. SpiderMonkey drops the
      // prefix (invisible_launch.py takes the source name separately for that
      // reason); emitting the wrong engine's form would be a sharper tell than
      // the marker this replaces.
      __pncMark(getter, 'get ' + prop);
      Object.defineProperty(obj, prop, {
        get: getter, configurable: true, enumerable: true,
      });
    } catch (e) {}
  }

  // --- screen geometry + devicePixelRatio + matchMedia ---
  // These ride the shared recursive registry (applyScreenPatch) so every nested
  // realm — page, iframe, grandchild iframe — reports the same spoofed monitor.
  // A one-level getter left a grandchild iframe reading the REAL 4K panel (the
  // 4090-class leak, for resolution). SEED/FORCED live INSIDE applyScreenPatch
  // so .toString() re-derives them in each realm. W/H are computed once in the
  // top window (where window.outerWidth is real) and stored on top so a child
  // realm reuses the exact same values instead of re-measuring its own extent.
  function applyScreenPatch(G) {
   try {
    if (!G || !G.screen) return;
__SCREEN_REALM_GUARD__
__SCREEN_REALM_SLOT__
__SCREEN_LEAF_CLOAK__
    var SEED = __SEED__;
    var FORCED = __FORCED_RES__;
    var OS = "__OS__";
    // The profile's frozen hardware generation — lives INSIDE the patch with
    // SEED/FORCED so .toString() carries it into every realm. See GEN's use
    // below: it is what keeps `fits.length` from moving under an existing
    // profile when a resolution is appended.
    var GEN = __GEN__;
    var IS_MAC = (OS === "macos");
    // macOS has a ~25px top menu bar and no bottom taskbar; Windows a 40px
    // bottom taskbar. Real Chrome reports 30-bit color + Retina DPR 2 on Mac,
    // 24-bit + DPR 1 on a typical Windows desktop. screen.* is in logical CSS
    // px (the engine already divides the panel by the render scale).
    var INSET = IS_MAC ? 25 : 40;
    var DEPTH = IS_MAC ? 30 : 24;
    var DPR = IS_MAC ? 2 : 1;
    function h(x){var v=SEED^(x|0);v=Math.imul(v^(v>>>16),0x85ebca6b);v=Math.imul(v^(v>>>13),0xc2b2ae35);return (v^(v>>>16))>>>0;}
    var def=function(o,k,val){try{var g=Object.getOwnPropertyDescriptor({get m(){return val;}},'m').get;try{Object.defineProperty(g,'name',{value:'get '+k});}catch(e){}__pncMark(g,'get '+k);Object.defineProperty(o,k,{get:g,configurable:true,enumerable:true});}catch(e){}};
    // The LEAF-LOCAL wrapper builder that serves `G.matchMedia`. PS-314 built
    // this as the minified twin of a readable top-level `nativeWrap`; that
    // readable copy is gone (its last callsite was the `enumerateDevices`
    // install this ticket moved into `applyDevicesPatch` — see the note beside
    // `pick`), so this is now the only copy in the seam, and it has to be: the
    // leaf body is what crosses realms, so a helper reached from the enclosing
    // IIFE would be undefined in a child. Re-housed per PS-314: shorthand
    // shell, arity copied from `orig` at runtime, marker pinned last. Fixing
    // the readable copy alone left
    // matchMedia reading ["__pnaName","arguments","caller","length","name",
    // "prototype"]; measured from a realm, not reasoned.
    var nw=function(orig,rep){var s;try{s=({m(){return rep.apply(this,arguments);}}).m;}catch(e){s=rep;}try{Object.defineProperty(s,'length',{value:orig.length});Object.defineProperty(s,'name',{value:orig.name});}catch(e){}return __pncMark(s,orig.name);};

    // Logical (CSS-px) resolutions from a real-world distribution for the OS.
    // Third element is the hardware GENERATION the entry was added in (absent =
    // 0, i.e. everything that shipped before generations existed). A profile
    // only sees entries at or below its own frozen generation, so appending one
    // here cannot change the divisor of the pick below for an existing profile.
    // See models/hardware_generation.py.
    //
    // BOTH ARMS ARE RENDERED FROM THE TAGGED PYTHON RECORDS in this module, so
    // a maintainer edits `MAC_SCREEN_RESOLUTIONS` / `WIN_SCREEN_RESOLUTIONS`
    // rather than a JS literal, and the generation guards can iterate them.
    var ALL_RES = IS_MAC ? __MAC_RES__ : __WIN_RES__;
    var RES = ALL_RES.filter(function (r) { return (r[2] || 0) <= GEN; });

    // Reuse the top window's already-computed W/H so every realm agrees. Only
    // the top realm has a real window extent to measure against. The value
    // rides the top realm's non-enumerable per-realm slot (see the Python note
    // above this template for why, and for what does and does not cross).
    // `create` is false: a READ must not mint an empty registry in the top.
    var top;
    try { top = G.top; } catch (e) { top = null; }
    var wh = null;
    try {
      if (top && top !== G) {
        var ts = __pnaSlot(top, false);
        if (ts && ts.screenWH) wh = ts.screenWH;
      }
    } catch (e) {}

    var W, H;
    if (wh) {
      W = wh.W; H = wh.H;
    } else if (FORCED) {
      // A user-picked resolution is honored outright (see #167: gating it on the
      // window extent leaked the render scale under --force-device-scale-factor).
      W = FORCED[0]; H = FORCED[1];
    } else {
      var needW = Math.max(G.outerWidth || 0, G.innerWidth || 0);
      var needH = (Math.max(G.outerHeight || 0, G.innerHeight || 0)) + INSET;
      var fits = RES.filter(function (r) { return r[0] >= needW && r[1] >= needH; });
      if (!fits.length) {
        fits = [[Math.max(needW, 1920), Math.max(needH, 1080)]];
      }
      var r = fits[h(0x5c0fee) % fits.length];
      W = r[0]; H = r[1];
    }
    // Publish this realm's resolved geometry for a child realm to reuse. Into
    // THIS realm's own slot (`create` true), never onto the global object.
    // First writer wins: a second independent invocation into one realm must
    // not re-roll W/H.
    try {
      var ss = __pnaSlot(G, true);
      if (ss && !ss.screenWH) ss.screenWH = { W: W, H: H };
    } catch (e) {}

    try {
      def(G.screen, 'width', W);
      def(G.screen, 'height', H);
      def(G.screen, 'availWidth', W);
      def(G.screen, 'availHeight', H - INSET);
      def(G.screen, 'colorDepth', DEPTH);
      def(G.screen, 'pixelDepth', DEPTH);
      if (G.screen && G.screen.orientation) {
        def(G.screen.orientation, 'type', 'landscape-primary');
        def(G.screen.orientation, 'angle', 0);
      }
    } catch (e) {}

    // devicePixelRatio must agree with the spoofed screen. The host's real DPR
    // leaking through makes scanners read screen.width * dpr = a resolution no
    // monitor has. Pin DPR (1 Windows / 2 Retina) and answer the matchMedia
    // dppx / device-width / device-height probes consistently.
    try {
      def(G, 'devicePixelRatio', DPR);
      var mm = G.matchMedia;
      if (mm) {
        var _mqDim = function (q, feature, target) {
          var re = new RegExp('(min-|max-)?' + feature + '\\s*:\\s*(\\d+(?:\\.\\d+)?)\\s*px', 'i');
          var m = q.match(re);
          if (!m) return null;
          var kind = (m[1] || '').toLowerCase(), n = parseFloat(m[2]);
          if (kind === 'min-') return target >= n;
          if (kind === 'max-') return target <= n;
          return target === n;
        };
        // Match a resolution/device-pixel-ratio query against our pinned DPR.
        var _dprRe = new RegExp('(^|[^\\d.])' + DPR + '(\\.0+)?\\s*dppx', 'i');
        var _dprRatioRe = new RegExp('device-pixel-ratio\\s*:\\s*' + DPR + '(\\.0+)?\\s*\\)', 'i');
        G.matchMedia = nw(mm, function (q) {
          var res = mm.call(G, q);
          if (/resolution|dppx|device-pixel-ratio|-webkit-device-pixel-ratio/i.test(q)) {
            var wantsDpr = _dprRe.test(q) || _dprRatioRe.test(q);
            try { def(res, 'matches', wantsDpr); } catch (e) {}
          }
          if (/device-width/i.test(q)) {
            var mw = _mqDim(q, 'device-width', W);
            if (mw !== null) { try { def(res, 'matches', mw); } catch (e) {} }
          }
          if (/device-height/i.test(q)) {
            var mh = _mqDim(q, 'device-height', H);
            if (mh !== null) { try { def(res, 'matches', mh); } catch (e) {} }
          }
          return res;
        });
      }
    } catch (e) {}
   } catch (e) {}
  }
__SCREEN_REALM_BOOTSTRAP__

  // --- mediaDevices.enumerateDevices ---
  // A believable consumer-desktop set: one mic + one default mic, one webcam,
  // one speaker + one default speaker. Labels stay '' (real browsers hide them
  // until getUserMedia permission). deviceId/groupId are stable per profile.
  //
  // ⭐ THIS RIDES THE REALM REGISTRY (PS-320). It used to be installed at the
  // content script's TOP LEVEL, which reaches only the realms chromium injects
  // into (`all_frames: True` covers frames the BROWSER creates). A realm the
  // PAGE builds at runtime — a Web Worker, a fresh about:blank/srcdoc iframe, a
  // worker spawned inside one — never received it, so such a realm reported the
  // ENGINE's device list while `screen`/`devicePixelRatio`/`hardwareConcurrency`
  // beside it were the profile's. Measured before the move: the page realm read
  // this spoofed list while a page-built child read the engine default, in a run
  // where the registry demonstrably DID transport its other two leaves. A
  // cross-realm mismatch is a stronger tell than a modified value, which is the
  // founding rationale of this registry.
  //
  // EVERYTHING THE LEAF NEEDS IS DECLARED INSIDE ITS BODY, because the body is
  // what crosses realms: `applyDevicesPatch.toString()` is re-evaluated in the
  // child, so anything referenced from the enclosing IIFE would be undefined
  // there. The three helpers this block used to close over were IIFE-scoped —
  // `h32` above, the since-deleted top-level `nativeWrap` (see the note beside
  // `pick`), and `hx`, which was declared here — and none of them survives the
  // trip. `applyHwPatch` below is the in-tree precedent and does exactly this
  // with its own SEED/`h`/`def`.
  //
  // THE GUARD SITS BELOW THIS LEAF'S REAL PRECONDITION, NOT AT THE TOP OF THE
  // BODY. `mediaDevices` is not universally present — it is absent from a
  // worker realm, and a page-built realm can be installed into BEFORE the page
  // has attached it — so `!G.navigator` is a weaker check that passes in realms
  // this leaf then bails out of. `realm_guard_js`'s own docstring states the
  // rule and names canvas_ctx/measuretext as the leaves that already obey it: a
  // realm where the leaf did NO work must not be recorded as covered, or the
  // later invocation that COULD have patched it returns early against an empty
  // realm and that realm reports the ENGINE's device list permanently. That is
  // this ticket's own defect reintroduced through the recovery path, and it is
  // silent — the structural suite asserts the guard's PRESENCE and UNIQUENESS,
  // never its POSITION. test_a_realm_installed_into_before_mediadevices_exists
  // is the value-read that gates it.
  function applyDevicesPatch(G) {
   try {
    if (!G || !G.navigator) return;
    var md = G.navigator.mediaDevices;
    if (!md || !md.enumerateDevices) return;
__DEVICES_REALM_GUARD__
__DEVICES_LEAF_CLOAK__
    var SEED = __SEED__;
    function h32(x) {
      var h = SEED ^ (x | 0);
      h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
      h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
      return (h ^ (h >>> 16)) >>> 0;
    }
    function hx(n, salt) {
      var s = '';
      for (var i = 0; i < n; i++) {
        s += (h32(salt + i * 2654435761) % 16).toString(16);
      }
      return s;
    }
    // The leaf's own copy of the wrapper, in the shape PS-314 established: a
    // real method shorthand (a function EXPRESSION owns prototype/arguments/
    // caller, which is a one-line tell), with `length`/`name` copied from the
    // ORIGINAL at runtime rather than pinned as literals, and the `__pnaName`
    // marker the native_ext toString cloak reads as an own property.
    var nw = function (orig, rep) {
      var s;
      try { s = ({ m() { return rep.apply(this, arguments); } }).m; }
      catch (e) { s = rep; }
      try {
        Object.defineProperty(s, 'length', { value: orig.length });
        Object.defineProperty(s, 'name', { value: orig.name });
      } catch (e) {}
      return __pncMark(s, orig.name);
    };
    var grpMic = hx(64, 0xa11), grpCam = hx(64, 0xb22), grpSpk = hx(64, 0xc33);
    var list = [
      { kind: 'audioinput',  gid: grpMic, did: 'default' },
      { kind: 'audioinput',  gid: grpMic, did: hx(64, 0x111) },
      { kind: 'videoinput',  gid: grpCam, did: hx(64, 0x222) },
      { kind: 'audiooutput', gid: grpSpk, did: 'default' },
      { kind: 'audiooutput', gid: grpSpk, did: hx(64, 0x333) },
    ];
    md.enumerateDevices = nw(
      md.enumerateDevices,
      function () {
        return Promise.resolve(list.map(function (d) {
          return {
            deviceId: d.did, groupId: d.gid, kind: d.kind, label: '',
            toJSON: function () {
              return { deviceId: d.did, groupId: d.gid, kind: d.kind, label: '' };
            },
          };
        }));
      }
    );
   } catch (e) {}
  }
__DEVICES_REALM_BOOTSTRAP__

  // --- navigator.hardwareConcurrency / deviceMemory ---
  // fingerprint-chromium leaves these at the host's real values on a desktop
  // profile (only the mobile presets set them), so a VM host leaked cores: 18 /
  // ram: 8 under a Windows identity — an obvious tell (creepjs "device"
  // rejected). Pin a plausible consumer-desktop (cores, GB-RAM) pair per seed.
  var HM = [4, 8];
  try {
    // The (cores, GB-RAM) pool ALREADY FILTERED to this profile's frozen
    // hardware generation, rendered from CORES_MEMORY in device_ext.py — the one
    // source of truth for both this realm and the worker twin in applyHwPatch.
    // It arrives pre-filtered rather than tagged-and-filtered-here so there is
    // no unfiltered array in scope whose .length could be taken by mistake:
    // dividing by the whole list's length is the original defect. Appending to
    // CORES_MEMORY therefore cannot change this divisor for an existing profile.
    var HCMEM = __HCMEM__;
    HM = pick(HCMEM, 0xc0de5);
    def(navigator, 'hardwareConcurrency', HM[0]);
    // deviceMemory is a coarse power-of-two-ish bucket the browser caps at 8.
    def(navigator, 'deviceMemory', Math.min(HM[1], 8));
  } catch (e) {}

  // screen geometry + devicePixelRatio ride applyScreenPatch on the shared
  // recursive registry (defined above), so they reach every nested realm
  // (page / iframe / grandchild iframe) — not a one-level getter here.

  // Carry hardwareConcurrency/deviceMemory into Web/Shared Workers, where
  // navigator.hardwareConcurrency otherwise reports the real host cores (a
  // worker/page mismatch is a tell — a VM host leaked 32 in a worker while the
  // page reported 12). SEED lives inside so applyHwPatch.toString() re-derives
  // the SAME pair in the worker realm.
  function applyHwPatch(G) {
   try {
    if (!G || !G.navigator) return;
__HW_REALM_GUARD__
__HW_LEAF_CLOAK__
    var SEED = __SEED__;
    function h(x){var v=SEED^(x|0);v=Math.imul(v^(v>>>16),0x85ebca6b);v=Math.imul(v^(v>>>13),0xc2b2ae35);return (v^(v>>>16))>>>0;}
    // Same pre-filtered pool as the page realm, rendered from the SAME
    // CORES_MEMORY list, so the worker cannot report a different machine than
    // the page (a page/worker mismatch is itself a tell). This runs AFTER the
    // top-level IIFE and re-defines both properties, so this is the divisor the
    // page actually ends up with — it must be generation-filtered too, and
    // fixing only the copy above would have changed nothing observable.
    var P=__HCMEM__; var m=P[h(0xc0de5)%P.length];
    var def=function(o,k,val){try{var g=Object.getOwnPropertyDescriptor({get m(){return val;}},'m').get;try{Object.defineProperty(g,'name',{value:'get '+k});}catch(e){}__pncMark(g,'get '+k);Object.defineProperty(o,k,{get:g,configurable:true,enumerable:true});}catch(e){}};
    def(G.navigator,'hardwareConcurrency',m[0]);
    def(G.navigator,'deviceMemory',Math.min(m[1],8));
   } catch (e) {}
  }
__HW_REALM_BOOTSTRAP__
})();
"""

_MANIFEST = {
    "manifest_version": 3,
    "name": "persona-device",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["device.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_device_extension(
    seed: int,
    base_dir: str,
    generation: int,
    resolution: tuple[int, int] | None = None,
    os_type: str = "windows",
) -> str:
    """Generate an unpacked extension that spoofs screen geometry and the
    mediaDevices list deterministically per profile seed. Returns its dir.

    ``resolution`` forces the spoofed screen to a specific (width, height);
    when None the screen is picked from common desktop sizes by the seed.
    ``os_type`` selects the screen preset: a macOS profile gets a Retina preset
    (DPR 2, 30-bit color, menu-bar geometry, Mac resolutions) so it stays
    consistent with its Apple/Metal GPU; everything else gets the Windows preset.

    ``generation`` is REQUIRED and deliberately has no default: every default
    would be a silent guess about which pool a profile belongs to, and guessing
    high is exactly the re-roll it exists to prevent.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    gen = normalize_generation(generation)
    # Render the cores/RAM pool ALREADY FILTERED to this profile's generation, so
    # the emitted JS has no unfiltered array in scope to divide by. Both realms
    # (page + worker twin) substitute this same value.
    hcmem = json.dumps(
        [list(pair) for pair in cores_memory_for_generation(gen)]
    )
    forced = f"[{resolution[0]}, {resolution[1]}]" if resolution else "null"
    os_norm = (
        "macos"
        if str(os_type).lower() in ("macos", "mac", "darwin", "ios")
        else "windows"
    )
    script = _CONTENT_SCRIPT.replace(
        "__SEED__", str(int(seed) & 0xFFFFFFFF)
    ).replace("__GEN__", str(gen)).replace(
        "__HCMEM__", hcmem
    ).replace(
        # The two screen-resolution pools are RENDERED from the tagged Python
        # records above, so a maintainer edits a `ScreenResolutionEntry` list
        # rather than a JS literal and the generation guards can iterate them.
        # Rendered unfiltered, `since` carried through: the emitted
        # `ALL_RES.filter(r[2] <= GEN)` does the per-profile filtering.
        #
        # RENDERED THROUGH `SCREEN_RES_POOLS`, NOT THROUGH THE TWO NAMES
        # DIRECTLY. That makes the registry load-bearing rather than
        # decorative: a pool that is not registered is not emitted, so
        # "registered" and "shipped" cannot drift apart, and the class-covering
        # guards in tests/test_hardware_generation.py iterate the same mapping
        # the product actually renders from.
        "__MAC_RES__",
        _render_screen_pool(SCREEN_RES_POOLS["MAC_SCREEN_RESOLUTIONS"]),
    ).replace(
        "__WIN_RES__",
        _render_screen_pool(SCREEN_RES_POOLS["WIN_SCREEN_RESOLUTIONS"]),
    ).replace(
        "__FORCED_RES__", forced
    ).replace(
        "__OS__", os_norm
    ).replace(
        "__SCREEN_REALM_BOOTSTRAP__", realm_bootstrap_js("applyScreenPatch")
    ).replace(
        "__HW_REALM_BOOTSTRAP__", realm_bootstrap_js("applyHwPatch")  # noqa: E501
    ).replace(
        # PS-320. A THIRD leaf, with its OWN guard key. Deliberately not folded
        # into `applyHwPatch`: the guard is per-key, so sharing "hw" would mean
        # a realm where applyHwPatch had already run SKIPS the devices install
        # entirely — and silently, because a skipped guard is indistinguishable
        # from a completed one. That is the same argument GUARD_SITES' own
        # comment makes for why device.js already carries two leaves.
        "__DEVICES_REALM_BOOTSTRAP__", realm_bootstrap_js("applyDevicesPatch")
    ).replace(
        "__SCREEN_LEAF_CLOAK__", chromium_leaf_cloak_js(4)
    ).replace(
        "__DEVICES_LEAF_CLOAK__", chromium_leaf_cloak_js(4)
    ).replace(
        "__HW_LEAF_CLOAK__", chromium_leaf_cloak_js(4)
    ).replace(
        "__IIFE_LEAF_CLOAK__", chromium_leaf_cloak_js(2)
    ).replace(
        "__SCREEN_REALM_GUARD__", realm_guard_js("screen")
    ).replace(
        "__HW_REALM_GUARD__", realm_guard_js("hw")
    ).replace(
        "__DEVICES_REALM_GUARD__", realm_guard_js("devices")
    ).replace(
        "__SCREEN_REALM_SLOT__", realm_slot_js()
    )
    (ext_dir / "device.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
