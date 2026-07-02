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
      replacement.toString = function () { return orig.toString(); };
    } catch (e) {}
    return replacement;
  }
  function def(obj, prop, val) {
    try {
      Object.defineProperty(obj, prop, {
        get: function () { return val; }, configurable: true, enumerable: true,
      });
    } catch (e) {}
  }

  // --- screen geometry ---
  // Pick from common real desktop resolutions, but never smaller than the
  // actual window — a screen smaller than its own window is an instant tell.
  // We choose the smallest plausible resolution that still contains the
  // window (+ taskbar), deterministically nudged by seed when several fit.
  var RES = [
    [1366, 768], [1440, 900], [1536, 864], [1600, 900], [1920, 1080],
    [1680, 1050], [1920, 1200], [2560, 1080], [2560, 1440],
  ];
  var TASKBAR = 40;  // typical Windows taskbar height
  // real window extent the screen must contain
  var needW = Math.max(window.outerWidth || 0, window.innerWidth || 0);
  var needH = (Math.max(window.outerHeight || 0, window.innerHeight || 0)) + TASKBAR;
  // A forced resolution (user picked a specific one) wins outright, as long as
  // it still contains the window; otherwise fall back to the seeded pick so the
  // screen is never smaller than its own window (an instant tell).
  var FORCED = __FORCED_RES__;
  var W, H;
  if (FORCED && FORCED[0] >= needW && FORCED[1] >= needH) {
    W = FORCED[0]; H = FORCED[1];
  } else {
    var fits = RES.filter(function (r) { return r[0] >= needW && r[1] >= needH; });
    if (!fits.length) {
      // window bigger than every preset — round the real screen up a little
      fits = [[Math.max(needW, 1920), Math.max(needH, 1080)]];
    }
    var r = pick(fits, 0x5c0fee);
    W = r[0]; H = r[1];
  }

  try {
    def(screen, 'width', W);
    def(screen, 'height', H);
    def(screen, 'availWidth', W);
    def(screen, 'availHeight', H - TASKBAR);
    def(screen, 'colorDepth', 24);
    def(screen, 'pixelDepth', 24);
    if (window.screen && window.screen.orientation) {
      def(window.screen.orientation, 'type', 'landscape-primary');
      def(window.screen.orientation, 'angle', 0);
    }
  } catch (e) {}

  // devicePixelRatio must agree with the spoofed screen. The host's real DPR
  // (1.5 on a 150%-scaled 4K monitor) leaks through untouched, and scanners
  // read the "physical" resolution as screen.width * devicePixelRatio — so a
  // spoofed 3840-wide screen at a real 1.5 DPR reports 5760x3240, a resolution
  // no real monitor has and an obvious tell. Pin DPR to 1 so the physical
  // resolution equals the spoofed screen (the overwhelmingly common desktop
  // case). matchMedia('(resolution: Ndppx)') is the other DPR probe, so answer
  // it consistently: only a 1dppx query matches.
  try {
    def(window, 'devicePixelRatio', 1);
    var mm = window.matchMedia;
    if (mm) {
      window.matchMedia = nativeWrap(mm, function (q) {
        var res = mm.call(window, q);
        if (/resolution|dppx|device-pixel-ratio|-webkit-device-pixel-ratio/i.test(q)) {
          var wantsOne = /(^|[^\d.])1(\.0+)?\s*dppx/i.test(q) ||
                         /device-pixel-ratio\s*:\s*1(\.0+)?\s*\)/i.test(q);
          try { def(res, 'matches', wantsOne); } catch (e) {}
        }
        return res;
      });
    }
  } catch (e) {}

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
    seed: int, base_dir: str, resolution: tuple[int, int] | None = None
) -> str:
    """Generate an unpacked extension that spoofs screen geometry and the
    mediaDevices list deterministically per profile seed. Returns its dir.

    ``resolution`` forces the spoofed screen to a specific (width, height);
    when None the screen is picked from common desktop sizes by the seed.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    forced = f"[{resolution[0]}, {resolution[1]}]" if resolution else "null"
    script = _CONTENT_SCRIPT.replace(
        "__SEED__", str(int(seed) & 0xFFFFFFFF)
    ).replace("__FORCED_RES__", forced)
    (ext_dir / "device.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
