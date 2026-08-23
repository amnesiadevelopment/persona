"""MAIN-world extension that makes a profile present as a real mobile device.

The engine has no Android/iOS mode, so a mobile profile sets the user-agent and
window size at launch (process.py) and this extension fills the JS-visible
mobile signals the engine can't: touch support (maxTouchPoints, ontouchstart,
TouchEvent), navigator.platform, and the userAgentData / Client-Hints shape
(mobile:true + platform + model on Android; userAgentData undefined on iOS, as
real Safari has no UA-CH). Screen geometry and deviceMemory/hardwareConcurrency
for mobile are handled by the device extension and engine respectively.
"""

import json
import pathlib

from .engine_version import ChromiumVersion
from .worker_wrap import realm_bootstrap_js

_CONTENT_SCRIPT = r"""
(function () {
  // Patch one realm G. Mobile signals must reach a Web Worker and a fresh
  // about:blank/srcdoc iframe too: creepjs/pixelscan read navigator.platform /
  // hardwareConcurrency / maxTouchPoints / pointer-media out of a pristine
  // worker/iframe — where a desktop-backed engine reports 'Linux x86_64', a
  // desktop core count and pointer:fine while the page claims a phone → instant
  // "mobile emulation". Window-only bits (screen/matchMedia/visualViewport/
  // touch prototypes) are gated on G.screen/G.matchMedia/G.Window so the leaf is
  // worker-safe. All params live INSIDE the leaf so .toString() carries them.
  function applyMobilePatch(G) {
   try {
    if (!G || !G.navigator) return;
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
        if (__pnaReg["mobile"] === true) return;
        __pnaReg["mobile"] = true;
      }
    } catch (e) {}
    var IS_IOS   = __IS_IOS__;
    var MODEL    = "__MODEL__";
    var FULLVER  = "__FULLVER__";
    var TOUCH    = __TOUCH__;
    var CSS_W    = __CSS_W__;
    var CSS_H    = __CSS_H__;
    var DPR      = __DPR__;
    var MEM      = __MEM__;
    var HWC      = __HWC__;

    function def(obj, prop, val) {
      try {
        Object.defineProperty(obj, prop, {
          get: function () { return val; }, configurable: true, enumerable: true,
        });
      } catch (e) {}
    }

    var nav = G.navigator;

    // --- hardware (present on WorkerNavigator too) ---
    try { def(nav, 'deviceMemory', MEM); } catch (e) {}
    try { def(nav, 'hardwareConcurrency', HWC); } catch (e) {}
    try { def(nav, 'maxTouchPoints', TOUCH); } catch (e) {}

    // --- platform + vendor (WorkerNavigator has platform; vendor is Window-only
    //     but guarded harmlessly) ---
    // 'Linux armv81' ends in a DIGIT ONE, not a lowercase 'l'. Not a typo, and
    // not to be "corrected": it is byte-exact with upstream Chromium and is what
    // every real Android Chrome emits. The uname reading is the WRONG PATH —
    // NavigatorID::platform() (sysname + machine, whose arm endianness suffix is
    // a LETTER, hence the plausible-looking 'armv8l') is virtual and merely the
    // FALLBACK. NavigatorBase::platform() overrides it and on Android returns
    // GetReducedNavigatorPlatform()'s frozen "Linux armv81", consulting uname
    // only when ReduceUserAgentMinorVersion is off (Android WebView) — and that
    // feature is status:"stable" since M101. So shipping the kernel-plausible
    // value would make us the ONLY Android browser on the internet reporting it:
    // a unique, regex-detectable tell manufactured by a confident fix. Full
    // refutation on cancelled PS-98; pinned by tests/test_mobile.py.
    try { def(nav, 'platform', IS_IOS ? 'iPhone' : 'Linux armv81'); } catch (e) {}
    if (IS_IOS) {
      try { def(nav, 'vendor', 'Apple Computer, Inc.'); } catch (e) {}
    }

    // --- userAgentData / Client Hints (WorkerNavigator exposes it too) ---
    try {
      if (IS_IOS) {
        if ('userAgentData' in nav) def(nav, 'userAgentData', undefined);
      } else {
        // MAJOR and FULLVER both come from the engine that is actually
        // installed (engine_version.py) — never a constant. The two shapes
        // differ on purpose: brands carries a BARE MAJOR, while uaFullVersion
        // and fullVersionList carry the TRUE full version. 'Not.A/Brand' is a
        // real GREASE entry and is version-independent, so it stays literal.
        var brands = [
          { brand: 'Chromium', version: '__MAJOR__' },
          { brand: 'Google Chrome', version: '__MAJOR__' },
          { brand: 'Not.A/Brand', version: '24' },
        ];
        var high = {
          architecture: '', bitness: '', model: MODEL, mobile: true,
          platform: 'Android', platformVersion: '14.0.0',
          uaFullVersion: FULLVER,
          fullVersionList: brands.map(function (b) {
            return { brand: b.brand, version: FULLVER };
          }),
          brands: brands, wow64: false,
        };
        var uaData = {
          brands: brands, mobile: true, platform: 'Android',
          getHighEntropyValues: function (hints) {
            var out = {};
            (hints || []).forEach(function (h) { if (h in high) out[h] = high[h]; });
            out.brands = brands; out.mobile = true; out.platform = 'Android';
            return Promise.resolve(out);
          },
          toJSON: function () {
            return { brands: brands, mobile: true, platform: 'Android' };
          },
        };
        def(nav, 'userAgentData', uaData);
      }
    } catch (e) {}

    // --- devicePixelRatio (Window realms only) ---
    if (typeof G.devicePixelRatio !== 'undefined' || 'devicePixelRatio' in G) {
      try { def(G, 'devicePixelRatio', DPR); } catch (e) {}
    }

    // --- screen (Window realms only) ---
    if (G.screen) {
      try {
        def(G.screen, 'width', CSS_W);
        def(G.screen, 'height', CSS_H);
        def(G.screen, 'availWidth', CSS_W);
        def(G.screen, 'availHeight', CSS_H);
        def(G.screen, 'colorDepth', IS_IOS ? 32 : 24);
        def(G.screen, 'pixelDepth', IS_IOS ? 32 : 24);
        if (G.screen.orientation) {
          def(G.screen.orientation, 'type', 'portrait-primary');
          def(G.screen.orientation, 'angle', 0);
        }
      } catch (e) {}
    }

    // --- viewport (Window realms only) ---
    if ('innerWidth' in G) {
      try {
        def(G, 'innerWidth', CSS_W);
        def(G, 'innerHeight', CSS_H);
        def(G, 'outerWidth', CSS_W);
        def(G, 'outerHeight', CSS_H);
        if (G.visualViewport) {
          def(G.visualViewport, 'width', CSS_W);
          def(G.visualViewport, 'height', CSS_H);
          def(G.visualViewport, 'scale', 1);
        }
      } catch (e) {}
    }

    // --- touch prototypes + constructors (Window realms only) ---
    try {
      var touchTargets = [];
      if (G.Window) touchTargets.push(G.Window.prototype);
      if (G.Document) touchTargets.push(G.Document.prototype);
      if (G.HTMLElement) touchTargets.push(G.HTMLElement.prototype);
      touchTargets.forEach(function (proto) {
        try {
          if (!('ontouchstart' in proto)) {
            Object.defineProperty(proto, 'ontouchstart', {
              get: function () { return null; }, set: function () {},
              configurable: true, enumerable: true,
            });
          }
        } catch (e) {}
      });
      // Window realms ONLY: a real Android Chrome worker has no TouchEvent/Touch
      // on its global, so defining them in a WorkerGlobalScope (this leaf runs in
      // workers via the registry) was a net-new mobile tell — typeof TouchEvent
      // === 'function' in a worker (audit7 #6). Gate on G.Window like the touch
      // prototypes above.
      if (G.Window) {
        if (typeof G.TouchEvent === 'undefined') {
          try { G.TouchEvent = function TouchEvent() {}; } catch (e) {}
        }
        if (typeof G.Touch === 'undefined') {
          try { G.Touch = function Touch() {}; } catch (e) {}
        }
      }
    } catch (e) {}

    // --- pointer/hover media queries (Window realms with matchMedia only) ---
    if (G.matchMedia) {
      try {
        var realMM = G.matchMedia.bind(G);
        var MOBILE_MQ = {
          '(pointer: coarse)': true, '(pointer: fine)': false,
          '(any-pointer: coarse)': true, '(any-pointer: fine)': false,
          '(hover: none)': true, '(hover: hover)': false,
          '(any-hover: none)': true, '(any-hover: hover)': false,
        };
        function patchedMM(q) {
          var mql = realMM(q);
          var key = (q || '').replace(/\s+/g, ' ').trim().toLowerCase();
          if (key in MOBILE_MQ) {
            var want = MOBILE_MQ[key];
            try {
              Object.defineProperty(mql, 'matches', {
                get: function () { return want; }, configurable: true,
              });
            } catch (e) {}
          }
          return mql;
        }
        try {
          Object.defineProperty(patchedMM, 'name', { value: 'matchMedia' });
          Object.defineProperty(patchedMM, '__pnaName', { value: 'matchMedia' });
        } catch (e) {}
        G.matchMedia = patchedMM;
      } catch (e) {}
    }
   } catch (e) {}
  }
__MOBILE_REALM_BOOTSTRAP__
})();
"""

_MANIFEST = {
    "manifest_version": 3,
    "name": "persona-mobile",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["mobile.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_mobile_extension(
    base_dir: str,
    *,
    is_ios: bool,
    platform: str,
    model: str,
    chromium_version: ChromiumVersion | None,
    css_width: int,
    css_height: int,
    dpr: float,
    device_memory: int,
    hardware_concurrency: int,
    touch_points: int = 5,
) -> str:
    """Generate an unpacked extension that adds the JS-visible mobile signals
    (screen, touch, platform, Client Hints) for a mobile profile. Returns its
    directory.

    ``chromium_version`` is the version of the ENGINE THAT IS INSTALLED, read
    by ``engine_version.installed_chromium_version()``. It fills the Client
    Hints brand major, the ``uaFullVersion`` and the ``fullVersionList`` —
    there is deliberately no default here, because a default is what let the
    advertised version drift from the engine's real one in the first place.

    It may be ``None`` ONLY for iOS, where nothing consumes it: real iOS Safari
    ships no UA-CH, so an iOS profile advertises no Chromium version at all and
    the whole userAgentData object is removed rather than populated. An Android
    build with ``None`` is a programming error and raises, rather than quietly
    emitting a placeholder version into a live profile.
    """
    if not is_ios and chromium_version is None:
        raise ValueError(
            "an Android mobile extension needs the installed engine's Chromium "
            "version; refusing to build one that advertises a placeholder"
        )
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    # On iOS these two never reach a page (the userAgentData branch is dropped
    # entirely), so the placeholders are substituted with empty strings purely
    # to leave no unreplaced __TOKEN__ in the emitted script.
    major = chromium_version.major if chromium_version else ""
    full = chromium_version.full if chromium_version else ""
    script = (
        _CONTENT_SCRIPT
        .replace("__IS_IOS__", "true" if is_ios else "false")
        .replace("__MODEL__", model)
        .replace("__MAJOR__", major)
        .replace("__FULLVER__", full)
        .replace("__TOUCH__", str(int(touch_points)))
        .replace("__CSS_W__", str(int(css_width)))
        .replace("__CSS_H__", str(int(css_height)))
        .replace("__DPR__", repr(float(dpr)))
        .replace("__MEM__", str(int(device_memory)))
        .replace("__HWC__", str(int(hardware_concurrency)))
        .replace("__MOBILE_REALM_BOOTSTRAP__", realm_bootstrap_js("applyMobilePatch"))
    )
    (ext_dir / "mobile.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
