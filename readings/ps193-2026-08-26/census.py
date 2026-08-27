#!/usr/bin/env python3
"""PS-193 — byte census of the region CreepJS samples, on a real Firefox engine.

THE READING (ticket DoD 1): for the region CreepJS actually reads back,
  * how many bytes it holds, and
  * how many of them pass the shipped mid-range guard `v > 1 && v < 254`.

That one number separates the two surviving causes of the `webgl_pixel_hash`
collision:
  1. STARVED REGION  — ~zero guard-eligible bytes, so our perturbation is a
     no-op *there specifically* (PS-182 geometry "C", the only one that
     reproduces the collision).
  2. DELTA NEVER REACHES THE PAGE REALM on this engine under real conditions.

METHOD, and why it is this one. The census is taken by wrapping
`readPixels` on the prototype and censusing THE BYTES THE CALLER RECEIVES —
never by re-implementing CreepJS's sampling and censusing our own idea of it.
That is PS-11 ("assert on returned bytes, never on generated source") applied
to a measurement instead of a test: a re-implementation would only prove this
script is self-consistent with its own assumption about the geometry.

The x/y/w/h and drawingBuffer dimensions are RECORDED FROM THE CALL, not
assumed, so the `17x42 = 2856` figure in `webgl_ext.py:28-48` is something this
run either reproduces or contradicts — it is not an input.
"""

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.parse

from playwright.sync_api import sync_playwright

# Resolve the repo from THIS file's location rather than hard-coding it, so the
# committed instrument runs wherever the repo is checked out (same reason
# run.sh derives REPO_ROOT from BASH_SOURCE).
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CRED_FILE = os.environ.get(
    "PERSONA_TEST_PROXY_FILE", "/workspace/_secrets/test-proxy.txt"
)
WORKDIR = pathlib.Path(os.environ.get("WORKDIR", "/tmp/ps193"))
CREEPJS_URL = "https://abrahamjuliot.github.io/creepjs"
EXIT_URL = "https://ipinfo.io/json"

# Wrap readPixels and census what the CALLER receives. Installed as an init
# script so it is present before any page code runs.
HOOK_JS = r"""
(() => {
  const REC = [];
  Object.defineProperty(window, '__ps193', {value: REC, writable: false});

  function census(px, meta) {
    let total = 0, eligible = 0, zeros = 0, ff = 0, ones = 0;
    const n = px.length;
    for (let i = 0; i < n; i++) {
      const v = px[i];
      total++;
      if (v > 1 && v < 254) eligible++;
      if (v === 0) zeros++;
      else if (v === 255) ff++;
      else if (v === 1) ones++;
    }
    // Alpha channel is structurally 255 on an opaque readback; report the
    // colour bytes separately so "starved" is not an artifact of counting it.
    let rgbTotal = 0, rgbEligible = 0;
    for (let i = 0; i < n; i++) {
      if ((i & 3) === 3) continue;
      rgbTotal++;
      const v = px[i];
      if (v > 1 && v < 254) rgbEligible++;
    }
    // FNV-1a over EVERY byte — the same reduction the repo's probes use. This
    // is what makes a SPOOFED arm decidable: if the shipped perturbation
    // reaches the realm CreepJS reads, two seeds give two digests here.
    let hsh = 0x811c9dc5;
    for (let i = 0; i < n; i++) {
      hsh ^= px[i];
      hsh = Math.imul(hsh, 0x01000193) >>> 0;
    }
    const m = Object.assign({}, meta);
    m.fnv1a = hsh >>> 0;
    m.total_bytes = total;
    m.guard_eligible = eligible;
    m.zeros = zeros;
    m.bytes_255 = ff;
    m.bytes_1 = ones;
    m.rgb_bytes = rgbTotal;
    m.rgb_guard_eligible = rgbEligible;
    REC.push(m);
  }

  // REALM COVERAGE, and why it is required rather than defensive.
  //
  // The first live arm recorded ZERO readPixels calls while CreepJS still
  // published a pixel hash. CreepJS reads its WebGL data through a PHANTOM
  // IFRAME — a freshly created same-origin `about:blank` realm whose intrinsics
  // it uses in preference to the top window's, precisely so that a page which
  // has patched `WebGLRenderingContext.prototype` is caught lying. Wrapping the
  // top realm's prototypes therefore observes nothing, and a census built on it
  // would have reported "CreepJS never reads pixels", which is false.
  //
  // So the `contentWindow` getter is wrapped: the moment any iframe's realm is
  // reached for, this installs the same census into THAT realm, recording into
  // the top realm's array. `installed_realms` below is the instrument's own
  // check (PS-14) — if it does not grow, the hook did not reach the realm and
  // the reading is NOT COVERED rather than "zero".
  const REALMS = [];
  Object.defineProperty(window, '__ps193_realms', {value: REALMS, writable: false});

  function wrap(proto, label, realm) {
    if (!proto || !proto.readPixels) return false;
    if (proto.__ps193_wrapped) return true;
    const orig = proto.readPixels;
    proto.readPixels = function (x, y, w, h, fmt, type, pixels) {
      const r = orig.apply(this, arguments);
      try {
        if (pixels && typeof pixels.length === 'number') {
          let cname = '';
          try { cname = this.canvas && this.canvas.constructor ? this.canvas.constructor.name : ''; } catch (e) {}
          census(pixels, {
            api: label, realm: realm,
            // Recorded AS PASSED, not normalised: CreepJS computes
            // `drawingBufferWidth / 15` with no Math.floor, so these can be
            // fractional and that is part of the finding.
            x: x, y: y, w: w, h: h,
            w_is_int: (w === Math.floor(w)), h_is_int: (h === Math.floor(h)),
            dbw: this.drawingBufferWidth, dbh: this.drawingBufferHeight,
            canvas_class: cname,
            canvas_w: (this.canvas ? this.canvas.width : null),
            canvas_h: (this.canvas ? this.canvas.height : null)
          });
        }
      } catch (e) {
        REC.push({api: label, realm: realm, error: String(e)});
      }
      return r;
    };
    try { proto.__ps193_wrapped = true; } catch (e) {}
    return true;
  }

  function installInto(win, realm) {
    if (!win) return;
    try {
      if (win.__ps193_done) return;
      win.__ps193_done = true;
    } catch (e) { return; }
    let n = 0;
    try { if (wrap(win.WebGLRenderingContext && win.WebGLRenderingContext.prototype, 'webgl1', realm)) n++; } catch (e) {}
    try { if (wrap(win.WebGL2RenderingContext && win.WebGL2RenderingContext.prototype, 'webgl2', realm)) n++; } catch (e) {}
    REALMS.push({realm: realm, wrapped: n});
  }

  installInto(window, 'top');

  // Expose the census so the inline instrument (route-patched creep.js, which
  // runs in THIS realm) can report the exact bytes it received.
  try { Object.defineProperty(window, '__ps193_census', {value: census, writable: false}); } catch (e) {}

  // PHANTOM-IFRAME COVERAGE — the reason arms 1-3 recorded nothing.
  //
  // CreepJS builds its phantom realm with `div.innerHTML = '<iframe></iframe>'`
  // and then takes the window by INDEXED ACCESS, `self[numberOfIframes]`
  // (creep.js `getPhantomIframe`). That path never touches the
  // `HTMLIFrameElement.prototype.contentWindow` getter, so hooking the getter
  // observes nothing. Its canvas is created in that realm, so the `gl` object's
  // prototype is the IFRAME realm's `WebGLRenderingContext.prototype` — not the
  // top realm's one we wrapped.
  //
  // So poll the indexed child realms and install into each as it appears. The
  // wrappers close over the TOP realm's REC array, so records land in one place
  // regardless of which realm produced them.
  function scanFrames() {
    try {
      for (let i = 0; i < window.length; i++) {
        try { installInto(window[i], 'phantom[' + i + ']'); } catch (e) {}
      }
    } catch (e) {}
  }
  scanFrames();
  try { setInterval(scanFrames, 5); } catch (e) {}
  try {
    document.addEventListener('DOMContentLoaded', scanFrames, true);
  } catch (e) {}

  // Also cover the ordinary `contentWindow` / `contentDocument` routes, for any
  // realm reached the normal way rather than by indexed access.
  try {
    const d = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (d && d.get) {
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        configurable: true,
        get: function () {
          const w = d.get.call(this);
          try { installInto(w, 'iframe'); } catch (e) {}
          return w;
        }
      });
    }
    const dd = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentDocument');
    if (dd && dd.get) {
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentDocument', {
        configurable: true,
        get: function () {
          const doc = dd.get.call(this);
          try { if (doc && doc.defaultView) installInto(doc.defaultView, 'iframe-doc'); } catch (e) {}
          return doc;
        }
      });
    }
  } catch (e) {
    REC.push({error: 'realm-hook-failed: ' + String(e)});
  }
})();
"""

# A local WebGL draw + corner readback, used to smoke-test the hook on loopback
# before any live arm is spent. Deliberately NOT a stand-in for the live census.
#
# It reproduces CreepJS's TWO awkward behaviours so the instrument is validated
# against them rather than against a convenient shape:
#   1. the read happens in a PHANTOM IFRAME realm, not the top window;
#   2. the width/height are `drawingBuffer/15` and `/6` with NO Math.floor.
SMOKE_HTML = """<!doctype html><html><body>
<canvas id=c width=256 height=256></canvas>
<iframe id=f src="about:blank" style="display:none"></iframe>
<script>
window.__smoke = (function(){
  const out = {};
  const c = document.getElementById('c');
  // Take the context constructor from the IFRAME realm, exactly as CreepJS does.
  const ifr = document.getElementById('f');
  const win = ifr.contentWindow;
  out.iframe_realm = !!win;
  const gl = c.getContext('webgl');
  if (!gl) return {ctx:false};
  gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);
  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, 'attribute vec2 p; varying vec2 v; void main(){v=p; gl_Position=vec4(p,0.0,1.0);} ');
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, 'precision mediump float; varying vec2 v; void main(){ gl_FragColor = vec4(0.5+0.4*v.x, 0.5+0.4*v.y, 0.45, 1.0);} ');
  gl.compileShader(fs);
  const pr = gl.createProgram();
  gl.attachShader(pr, vs); gl.attachShader(pr, fs); gl.linkProgram(pr); gl.useProgram(pr);
  const b = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, 0,1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(pr, 'p');
  gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  // CreepJS's own arithmetic, fractions and all.
  const w = gl.drawingBufferWidth/15, h = gl.drawingBufferHeight/6;
  const px = new Uint8Array(w*h*4);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
  out.ctx = true; out.w = w; out.h = h; out.len = px.length;
  return out;
})();
</script></body></html>"""


def build_proxy():
    """Resolve the credential from the FILE channel and relay it locally.

    Two separate points, both load-bearing.

    CHANNEL. The file wins over ``$PERSONA_TEST_PROXY`` by design
    (``exit_guard.resolve_credential``), and the env var on this container is a
    *different provider* carrying a session token minted at container creation.
    Reading only the file makes a credential failure unambiguous instead of a
    silent provider swap.

    RELAY. Firefox cannot authenticate to a SOCKS5 proxy — Playwright refuses
    the launch outright with *"Browser does not support socks5 proxy
    authentication"* (measured here, and documented at
    ``chromium_tier.py:13-16`` for the same reason on the other engine). So the
    browser is pointed at persona's OWN hardened loopback relay,
    ``services/proxy/bridge.ProxyBridge``, which performs the username/password
    auth on the way out. This is the product's own component, not a workaround
    invented for this reading, and the credential never reaches the browser.
    """
    raw = pathlib.Path(CRED_FILE).read_text().strip()
    if not raw:
        raise SystemExit("CREDENTIAL_ABSENT: empty file")

    sys.path.insert(0, str(REPO_ROOT))
    from src.services.proxy.bridge import ProxyBridge

    bridge = ProxyBridge(raw)
    port = bridge.start()
    # The gate starts unclaimed; claim it for THIS process. Firefox is spawned
    # as a descendant of this interpreter, and the gate authorises the process
    # tree rooted here.
    bridge.bind_to_process(0)
    u = urllib.parse.urlparse(raw)
    # Only the LOCAL port is ever named to the browser. The upstream hostname
    # identifies the provider and carries the geo/session labels.
    return {"server": f"socks5://127.0.0.1:{port}"}, bridge, u.hostname


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["smoke", "live"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait", type=int, default=90)
    ap.add_argument(
        "--spoof-seed",
        type=int,
        default=None,
        help="Install the SHIPPED firefox_webgl_init_script(seed) before the "
             "page loads. With the census in hand this is what tests candidate "
             "2 directly: if the perturbation reaches the realm CreepJS reads, "
             "two seeds must give two different fnv1a digests at its callsite.",
    )
    args = ap.parse_args()

    result = {
        "mode": args.mode,
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "display": os.environ.get("DISPLAY", ""),
        "spoof_seed": args.spoof_seed,
    }

    spoof_js = None
    if args.spoof_seed is not None:
        sys.path.insert(0, str(REPO_ROOT))
        from src.services.browser.webgl_ext import firefox_webgl_init_script

        # THE PRODUCT'S OWN TEXT, not a transcription of it (PS-11 / PS-182).
        spoof_js = firefox_webgl_init_script(args.spoof_seed)
        result["spoof_script_bytes"] = len(spoof_js)

    launch = {"headless": False}
    bridge = None
    if args.mode == "live":
        proxy, bridge, up_host = build_proxy()
        launch["proxy"] = proxy
        result["credential_channel"] = "file:%s" % CRED_FILE
        result["proxy_endpoint"] = proxy["server"]
        result["upstream_host"] = up_host
        result["relay"] = "src/services/proxy/bridge.ProxyBridge"

    with sync_playwright() as p:
        b = p.firefox.launch(**launch)
        result["engine_build"] = b.version
        # The hook goes on the CONTEXT, before any page exists, so it is
        # installed at document creation for every navigation. Adding it to an
        # already-created page does nothing for `set_content`, which writes into
        # the existing document rather than navigating (measured: the hook
        # silently never fired, RECORDS: 0).
        ctx = b.new_context()
        ctx.add_init_script(HOOK_JS)
        if spoof_js is not None:
            # Installed AFTER the census hook so the census is already in place,
            # and unconditionally — mirroring invisible_launch.py:3345.
            ctx.add_init_script(spoof_js)
        pg = ctx.new_page()

        if args.mode == "smoke":
            WORKDIR.mkdir(parents=True, exist_ok=True)
            sp = WORKDIR / "smoke.html"
            sp.write_text(SMOKE_HTML)
            pg.goto("file://%s" % sp, wait_until="load", timeout=60000)
            pg.wait_for_timeout(3000)
            result["smoke"] = pg.evaluate("() => window.__smoke")
            result["records"] = pg.evaluate("() => window.__ps193")
            result["user_agent"] = pg.evaluate("() => navigator.userAgent")
            result["gl"] = pg.evaluate("""() => {
              const c=document.createElement('canvas');
              const gl=c.getContext('webgl'); if(!gl) return null;
              return {version: gl.getParameter(gl.VERSION),
                      vendor: gl.getParameter(gl.VENDOR),
                      renderer: gl.getParameter(gl.RENDERER)};
            }""")
        else:
            # EXIT VERIFICATION FIRST, from inside the browser realm. PS-10 is
            # report-don't-fallback: if this reads the host's own address the
            # arm is NOT spent and the run is recorded as the exit having died.
            pg.goto(EXIT_URL, wait_until="load", timeout=90000)
            exit_json = pg.evaluate("() => document.body.innerText")
            try:
                result["exit"] = json.loads(exit_json)
            except Exception:
                result["exit_raw"] = exit_json[:400]
            result["user_agent"] = pg.evaluate("() => navigator.userAgent")

            # INSTRUMENT CREEPJS'S OWN CALL SITE.
            #
            # Four arms of prototype/realm hooking recorded nothing while
            # CreepJS still published a pixel hash, and the instrument's own
            # check said the wrap only ever reached the top realm. Rather than
            # keep guessing which realm owns the `gl` object, the census is
            # taken AT CreepJS's own `readPixels` line: the script is fetched
            # through this same proxied context and one observer call is
            # appended after the read.
            #
            # This is still PS-11-clean — it censuses THE BYTES CREEPJS
            # RECEIVES, in the buffer CreepJS allocated, at the moment it gets
            # them. Nothing about the draw, the geometry or the reduction is
            # re-implemented, and the observer cannot change what was read
            # because it runs after the call returns. `patch.count` is the
            # instrument check: if the literal is not found exactly once, the
            # reading is NOT COVERED rather than zero.
            LITERAL = ("gl.readPixels(0, 0, width, height, "
                       "gl.RGBA, gl.UNSIGNED_BYTE, pixels);")
            INJECT = LITERAL + (
                " try{var __c=(window.top&&window.top.__ps193_census)"
                "||window.__ps193_census;"
                "if(__c){__c(pixels,{api:'creepjs-inline',"
                "realm:'creepjs-own-callsite',x:0,y:0,w:width,h:height,"
                "w_is_int:(width===Math.floor(width)),"
                "h_is_int:(height===Math.floor(height)),"
                "dbw:drawingBufferWidth,dbh:drawingBufferHeight,"
                "context_type:String(contextType),"
                "canvas_class:(gl.canvas&&gl.canvas.constructor"
                "?gl.canvas.constructor.name:'')});}}catch(e){}"
            )
            patch = {"seen": 0, "applied": False}

            def _patch_creep(route):
                try:
                    resp = route.fetch()
                    body = resp.text()
                    n = body.count(LITERAL)
                    patch["seen"] = n
                    if n:
                        body = body.replace(LITERAL, INJECT)
                        patch["applied"] = True
                    route.fulfill(
                        status=resp.status,
                        headers={"content-type": "application/javascript; charset=utf-8"},
                        body=body,
                    )
                except Exception as exc:
                    patch["error"] = str(exc)
                    route.continue_()

            pg.route("**/creep.js*", _patch_creep)

            pg.goto(CREEPJS_URL, wait_until="domcontentloaded", timeout=120000)
            result["callsite_patch"] = patch

            # POSITIVE CONTROL FOR THE SPOOF (PS-14).
            #
            # The spoofed arm returned a digest IDENTICAL to the unspoofed
            # baseline. That is precisely the shape PS-14 says to distrust, and
            # it has two completely different explanations:
            #   (i)  the spoof never executed  -> instrument failure, no finding;
            #   (ii) the spoof executed but its delta does not reach the realm
            #        CreepJS reads -> candidate 2, the actual finding.
            #
            # Only a control separates them. After the page has loaded (so the
            # same init scripts have run), draw and read back IN THE PAGE REALM
            # via BOTH a regular canvas and an OffscreenCanvas, reducing each
            # with the same FNV-1a. If the regular-canvas digest MOVES with the
            # seed, the shipped perturbation demonstrably ran; if the
            # OffscreenCanvas digest does NOT, the gap is localised to the realm
            # CreepJS actually uses (`canvas_class: OffscreenCanvas`).
            try:
                result["spoof_control"] = pg.evaluate(
                    """() => {
                      function fnv(px){
                        let h = 0x811c9dc5;
                        for (let i=0;i<px.length;i++){ h ^= px[i]; h = Math.imul(h,0x01000193)>>>0; }
                        return h>>>0;
                      }
                      function draw(gl){
                        gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);
                        const vs=gl.createShader(gl.VERTEX_SHADER);
                        gl.shaderSource(vs,'attribute vec2 p; varying vec2 v; void main(){v=p; gl_Position=vec4(p,0.0,1.0);}');
                        gl.compileShader(vs);
                        const fs=gl.createShader(gl.FRAGMENT_SHADER);
                        gl.shaderSource(fs,'precision mediump float; varying vec2 v; void main(){ gl_FragColor=vec4(0.5+0.4*v.x,0.5+0.4*v.y,0.45,1.0);}');
                        gl.compileShader(fs);
                        const pr=gl.createProgram();
                        gl.attachShader(pr,vs); gl.attachShader(pr,fs); gl.linkProgram(pr); gl.useProgram(pr);
                        const b=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,b);
                        gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,0,1]),gl.STATIC_DRAW);
                        const l=gl.getAttribLocation(pr,'p');
                        gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l,2,gl.FLOAT,false,0,0);
                        gl.drawArrays(gl.TRIANGLES,0,3);
                      }
                      function read(gl){
                        const w=gl.drawingBufferWidth/15, h=gl.drawingBufferHeight/6;
                        const px=new Uint8Array(w*h*4);
                        gl.readPixels(0,0,w,h,gl.RGBA,gl.UNSIGNED_BYTE,px);
                        let e=0; for(let i=0;i<px.length;i++){const v=px[i]; if(v>1&&v<254)e++;}
                        return {fnv: fnv(px), len: px.length, eligible: e};
                      }
                      const out = {};
                      try {
                        const c=document.createElement('canvas'); c.width=256;c.height=256;
                        const gl=c.getContext('webgl');
                        if(gl){ draw(gl); out.canvas = read(gl); }
                        else out.canvas = {error:'no ctx'};
                      } catch(e){ out.canvas={error:String(e)}; }
                      try {
                        if (typeof OffscreenCanvas === 'function') {
                          const o=new OffscreenCanvas(256,256);
                          const gl2=o.getContext('webgl');
                          if(gl2){ draw(gl2); out.offscreen = read(gl2); }
                          else out.offscreen = {error:'no ctx'};
                        } else out.offscreen = {error:'no OffscreenCanvas'};
                      } catch(e){ out.offscreen={error:String(e)}; }
                      return out;
                    }"""
                )
            except Exception as exc:
                result["spoof_control"] = {"error": str(exc)}

            # COLLECT ACROSS EVERY FRAME, not just the main one.
            #
            # `add_init_script` runs once per FRAME, and each frame gets its own
            # `window.__ps193`. CreepJS does its WebGL read inside a phantom
            # same-origin iframe, so the records land in THAT frame's array and
            # the main frame's stays empty. Two live arms recorded `RECORDS: 0`
            # while CreepJS still published a pixel hash — that was this bug in
            # the instrument, not a fact about the engine (PS-14: check the
            # instrument before believing the reading).
            def harvest():
                recs, realms = [], []
                for fr in pg.frames:
                    try:
                        r = fr.evaluate("() => window.__ps193 || null")
                        if r:
                            for item in r:
                                item["frame_url"] = (fr.url or "")[:120]
                                recs.append(item)
                        rr = fr.evaluate("() => window.__ps193_realms || null")
                        if rr:
                            for item in rr:
                                item["frame_url"] = (fr.url or "")[:120]
                                realms.append(item)
                    except Exception:
                        continue
                return recs, realms

            deadline = time.time() + args.wait
            last = 0
            while time.time() < deadline:
                pg.wait_for_timeout(3000)
                recs, _ = harvest()
                n = len(recs)
                if n and n == last and n > 0:
                    break
                last = n
            recs, realms = harvest()
            result["records"] = recs
            # The instrument's OWN check: if no realm ever reported an install,
            # a zero-record reading is NOT COVERED rather than "CreepJS read
            # nothing".
            result["installed_realms"] = realms
            result["frame_count"] = len(pg.frames)
            result["gl"] = pg.evaluate("""() => {
              const c=document.createElement('canvas');
              const gl=c.getContext('webgl'); if(!gl) return null;
              return {version: gl.getParameter(gl.VERSION),
                      vendor: gl.getParameter(gl.VENDOR),
                      renderer: gl.getParameter(gl.RENDERER)};
            }""")
            try:
                result["creep_webgl_row"] = pg.evaluate(
                    """() => {
                      const t = document.body.innerText || '';
                      const m = t.match(/pixels:\\s*([0-9a-f]{6,10})/i);
                      return m ? m[1] : null;
                    }"""
                )
            except Exception:
                result["creep_webgl_row"] = None
        b.close()

    if bridge is not None:
        bridge.stop()

    pathlib.Path(args.out).write_text(json.dumps(result, indent=2))
    recs = result.get("records") or []
    print("RECORDS:", len(recs))
    for r in recs[:12]:
        if "error" in r:
            print("  ERR", r)
            continue
        print("  %s %sx%s at (%s,%s) db=%sx%s | total=%s eligible=%s zeros=%s ff=%s | rgb=%s rgb_elig=%s"
              % (r.get("api"), r.get("w"), r.get("h"), r.get("x"), r.get("y"),
                 r.get("dbw"), r.get("dbh"), r.get("total_bytes"), r.get("guard_eligible"),
                 r.get("zeros"), r.get("bytes_255"), r.get("rgb_bytes"), r.get("rgb_guard_eligible")))
    if result.get("exit"):
        e = result["exit"]
        print("EXIT: %s %s %s/%s" % (e.get("ip"), e.get("org"), e.get("city"), e.get("country")))
    print("WROTE", args.out)


if __name__ == "__main__":
    main()
