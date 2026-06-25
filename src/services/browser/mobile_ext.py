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

_CONTENT_SCRIPT = r"""
(function () {
  var PLATFORM = "__PLATFORM__";   // "Android" | "iPhone"
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

  // --- device pixel ratio + hardware (engine reports desktop values when
  //     backed by a linux/macos platform; force the real device's numbers) ---
  try { def(window, 'devicePixelRatio', DPR); } catch (e) {}
  try { def(navigator, 'deviceMemory', MEM); } catch (e) {}
  try { def(navigator, 'hardwareConcurrency', HWC); } catch (e) {}

  // --- screen (mobile reports CSS-pixel screen size; physical = css*dpr) ---
  try {
    def(screen, 'width', CSS_W);
    def(screen, 'height', CSS_H);
    def(screen, 'availWidth', CSS_W);
    def(screen, 'availHeight', CSS_H);
    def(screen, 'colorDepth', 24);
    def(screen, 'pixelDepth', 24);
    if (screen.orientation) {
      def(screen.orientation, 'type', 'portrait-primary');
      def(screen.orientation, 'angle', 0);
    }
  } catch (e) {}

  // --- viewport: on a phone the window fills the screen width; the host WM may
  //     not honour --window-size, so pin inner/outer to the device viewport.
  //     A window wider than its own screen is an instant mobile-emulation tell.
  try {
    var BAR = 0;  // URL bar already excluded from CSS_H in the preset height
    def(window, 'innerWidth', CSS_W);
    def(window, 'innerHeight', CSS_H - BAR);
    def(window, 'outerWidth', CSS_W);
    def(window, 'outerHeight', CSS_H);
    if (window.visualViewport) {
      def(window.visualViewport, 'width', CSS_W);
      def(window.visualViewport, 'height', CSS_H - BAR);
      def(window.visualViewport, 'scale', 1);
    }
  } catch (e) {}

  // --- touch ---
  // Real mobile exposes ontouchstart on the PROTOTYPE (Window/Document/Element),
  // not just the instance, and on document.documentElement. Detectors test
  // 'ontouchstart' in document.documentElement and the prototype chain, so set
  // it there — an instance-only ontouchstart still reads as no-touch.
  try {
    def(navigator, 'maxTouchPoints', TOUCH);
    var touchTargets = [];
    if (window.Window) touchTargets.push(Window.prototype);
    if (window.Document) touchTargets.push(Document.prototype);
    if (window.HTMLElement) touchTargets.push(HTMLElement.prototype);
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
    // Touch / TouchEvent constructors exist on real mobile Chrome.
    if (typeof window.TouchEvent === 'undefined') {
      try { window.TouchEvent = function TouchEvent() {}; } catch (e) {}
    }
    if (typeof window.Touch === 'undefined') {
      try { window.Touch = function Touch() {}; } catch (e) {}
    }
  } catch (e) {}

  // --- pointer/hover media queries: a phone is pointer:coarse + hover:none,
  //     never pointer:fine. The engine (backed by a desktop platform with a
  //     mouse) answers the opposite, which detectors compare against
  //     maxTouchPoints — a mismatch (touch points but pointer:fine) is the
  //     classic mobile-emulation tell. Rewrite the pointer/hover answers.
  try {
    var realMM = window.matchMedia.bind(window);
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
      patchedMM.toString = function () {
        return 'function matchMedia() { [native code] }';
      };
    } catch (e) {}
    window.matchMedia = patchedMM;
  } catch (e) {}

  // --- platform + vendor ---
  try { def(navigator, 'platform', IS_IOS ? 'iPhone' : 'Linux armv81'); } catch (e) {}
  // Real iOS Safari reports vendor "Apple Computer, Inc."; the Chromium engine
  // says "Google Inc.", which contradicts an iPhone UA. Android Chrome keeps
  // "Google Inc." (correct), so only override on iOS.
  if (IS_IOS) {
    try { def(navigator, 'vendor', 'Apple Computer, Inc.'); } catch (e) {}
  }

  // --- userAgentData / Client Hints ---
  try {
    if (IS_IOS) {
      // Real iOS Safari exposes NO userAgentData. Remove it if the engine added one.
      if ('userAgentData' in navigator) {
        def(navigator, 'userAgentData', undefined);
      }
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
          (hints || []).forEach(function (h) {
            if (h in high) out[h] = high[h];
          });
          out.brands = brands; out.mobile = true; out.platform = 'Android';
          return Promise.resolve(out);
        },
        toJSON: function () {
          return { brands: brands, mobile: true, platform: 'Android' };
        },
      };
      def(navigator, 'userAgentData', uaData);
    }
  } catch (e) {}
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
        .replace("__PLATFORM__", platform)
        .replace("__IS_IOS__", "true" if is_ios else "false")
        .replace("__MODEL__", model)
        .replace("__FULLVER__", ua_full_version or "148.0.0.0")
        .replace("__TOUCH__", str(int(touch_points)))
        .replace("__CSS_W__", str(int(css_width)))
        .replace("__CSS_H__", str(int(css_height)))
        .replace("__DPR__", repr(float(dpr)))
        .replace("__MEM__", str(int(device_memory)))
        .replace("__HWC__", str(int(hardware_concurrency)))
    )
    (ext_dir / "mobile.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
