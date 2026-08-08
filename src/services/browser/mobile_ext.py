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
    if (!G || !G.navigator || G.__personaMobile) return;
    G.__personaMobile = true;
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
    try { def(nav, 'platform', IS_IOS ? 'iPhone' : 'Linux armv81'); } catch (e) {}
    if (IS_IOS) {
      try { def(nav, 'vendor', 'Apple Computer, Inc.'); } catch (e) {}
    }

    // --- userAgentData / Client Hints (WorkerNavigator exposes it too) ---
    try {
      if (IS_IOS) {
        if ('userAgentData' in nav) def(nav, 'userAgentData', undefined);
      } else {
        var brands = [
          { brand: 'Chromium', version: '148' },
          { brand: 'Google Chrome', version: '148' },
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
      if (typeof G.TouchEvent === 'undefined') {
        try { G.TouchEvent = function TouchEvent() {}; } catch (e) {}
      }
      if (typeof G.Touch === 'undefined') {
        try { G.Touch = function Touch() {}; } catch (e) {}
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
    ua_full_version: str,
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
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    script = (
        _CONTENT_SCRIPT
        .replace("__IS_IOS__", "true" if is_ios else "false")
        .replace("__MODEL__", model)
        .replace("__FULLVER__", ua_full_version or "148.0.0.0")
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
