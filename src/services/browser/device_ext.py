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
from .worker_wrap import realm_bootstrap_js, realm_guard_js


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


# Common real desktop resolutions (StatCounter-ish top set). Picking from a
# real-world distribution keeps each profile plausible while differing between
# profiles. availHeight subtracts a typical Windows taskbar (40px); availWidth
# stays full — matching how real Windows reports it.
_CONTENT_SCRIPT = r"""
(function () {
  var SEED = __SEED__;

  function h32(x) {
    var h = SEED ^ (x | 0);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    return (h ^ (h >>> 16)) >>> 0;
  }
  function pick(arr, salt) { return arr[h32(salt) % arr.length]; }

  function nativeWrap(orig, replacement) {
    try {
      Object.defineProperty(replacement, 'name', { value: orig.name });
      // Mark for the native_ext Function.prototype.toString patch so a detector
      // calling Function.prototype.toString.call(replacement) reads native. A
      // plain replacement.toString override is bypassed by that .call form.
      Object.defineProperty(replacement, '__pnaName', { value: orig.name });
    } catch (e) {}
    return replacement;
  }
  function def(obj, prop, val) {
    try {
      var getter = function () { return val; };
      // Native-looking getters (a masking detector may stringify the accessor).
      try { Object.defineProperty(getter, '__pnaName', { value: 'get ' + prop }); } catch (e) {}
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
    var def=function(o,k,val){try{var g=function(){return val;};try{Object.defineProperty(g,'__pnaName',{value:'get '+k});}catch(e){}Object.defineProperty(o,k,{get:g,configurable:true,enumerable:true});}catch(e){}};
    var nw=function(orig,rep){try{Object.defineProperty(rep,'name',{value:orig.name});Object.defineProperty(rep,'__pnaName',{value:orig.name});}catch(e){}return rep;};

    // Logical (CSS-px) resolutions from a real-world distribution for the OS.
    // Third element is the hardware GENERATION the entry was added in (absent =
    // 0, i.e. everything that shipped before generations existed). A profile
    // only sees entries at or below its own frozen generation, so appending one
    // here cannot change the divisor of the pick below for an existing profile.
    // See models/hardware_generation.py. Append with `, N` and never renumber a
    // shipped row.
    var ALL_RES = IS_MAC ? [
      [1440, 900, 0], [1512, 982, 0], [1680, 1050, 0], [1728, 1117, 0], [2560, 1440, 0],
    ] : [
      [1366, 768, 0], [1440, 900, 0], [1536, 864, 0], [1600, 900, 0], [1920, 1080, 0],
      [1680, 1050, 0], [1920, 1200, 0], [2560, 1080, 0], [2560, 1440, 0],
    ];
    var RES = ALL_RES.filter(function (r) { return (r[2] || 0) <= GEN; });

    // Reuse the top window's already-computed W/H so every realm agrees. Only
    // the top realm has a real window extent to measure against.
    var top;
    try { top = G.top; } catch (e) { top = null; }
    var wh = null;
    try { if (top && top !== G && top.__personaScreenWH) wh = top.__personaScreenWH; } catch (e) {}

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
    try { if (!G.__personaScreenWH) G.__personaScreenWH = { W: W, H: H }; } catch (e) {}

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
  function hx(n, salt) {
    var s = '';
    for (var i = 0; i < n; i++) {
      s += (h32(salt + i * 2654435761) % 16).toString(16);
    }
    return s;
  }
  try {
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
      var grpMic = hx(64, 0xa11), grpCam = hx(64, 0xb22), grpSpk = hx(64, 0xc33);
      var list = [
        { kind: 'audioinput',  gid: grpMic, did: 'default' },
        { kind: 'audioinput',  gid: grpMic, did: hx(64, 0x111) },
        { kind: 'videoinput',  gid: grpCam, did: hx(64, 0x222) },
        { kind: 'audiooutput', gid: grpSpk, did: 'default' },
        { kind: 'audiooutput', gid: grpSpk, did: hx(64, 0x333) },
      ];
      var orig = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
      navigator.mediaDevices.enumerateDevices = nativeWrap(
        navigator.mediaDevices.enumerateDevices,
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
    }
  } catch (e) {}

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
    var SEED = __SEED__;
    function h(x){var v=SEED^(x|0);v=Math.imul(v^(v>>>16),0x85ebca6b);v=Math.imul(v^(v>>>13),0xc2b2ae35);return (v^(v>>>16))>>>0;}
    // Same pre-filtered pool as the page realm, rendered from the SAME
    // CORES_MEMORY list, so the worker cannot report a different machine than
    // the page (a page/worker mismatch is itself a tell). This runs AFTER the
    // top-level IIFE and re-defines both properties, so this is the divisor the
    // page actually ends up with — it must be generation-filtered too, and
    // fixing only the copy above would have changed nothing observable.
    var P=__HCMEM__; var m=P[h(0xc0de5)%P.length];
    var def=function(o,k,val){try{var g=function(){return val;};try{Object.defineProperty(g,'__pnaName',{value:'get '+k});}catch(e){}Object.defineProperty(o,k,{get:g,configurable:true,enumerable:true});}catch(e){}};
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
        "__FORCED_RES__", forced
    ).replace(
        "__OS__", os_norm
    ).replace(
        "__SCREEN_REALM_BOOTSTRAP__", realm_bootstrap_js("applyScreenPatch")
    ).replace(
        "__HW_REALM_BOOTSTRAP__", realm_bootstrap_js("applyHwPatch")  # noqa: E501
    ).replace(
        "__SCREEN_REALM_GUARD__", realm_guard_js("screen")
    ).replace(
        "__HW_REALM_GUARD__", realm_guard_js("hw")
    )
    (ext_dir / "device.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
