#!/usr/bin/env python3
"""PS-193 realm-reach probe — LOOPBACK, no exit needed.

The live census settled the geometry question. This probe settles the
MECHANISM behind candidate 2, and it needs no exit because it asks only
"which realms does the shipped perturbation reach?" — a property of our own
code, not of the checker or the network.

CreepJS builds a phantom iframe and creates its canvas in THAT realm:

    let win = window;
    if (!LIKE_BRAVE && PHANTOM_DARKNESS) { win = PHANTOM_DARKNESS; }
    canvas = new win.OffscreenCanvas(256, 256);

so the context object's prototype chain belongs to the IFRAME realm. This
probe reproduces exactly that construction and reads back in four realms at
two seeds, reducing each with FNV-1a. A realm the perturbation reaches gives
two different digests; a realm it misses gives one digest twice.
"""

import argparse
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

# Resolve the repo from THIS file's location rather than hard-coding it, so the
# committed probe runs wherever the repo is checked out (same reason
# realm-run.sh derives REPO_ROOT from BASH_SOURCE).
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from src.services.browser.webgl_ext import firefox_webgl_init_script  # noqa: E402

PROBE = r"""
() => {
  function fnv(px){ let h=0x811c9dc5;
    for(let i=0;i<px.length;i++){h^=px[i];h=Math.imul(h,0x01000193)>>>0;} return h>>>0; }
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
    return {fnv:fnv(px), len:px.length, eligible:e};
  }
  const out={};

  // 1-2: the TOP realm, both canvas kinds.
  try { const c=document.createElement('canvas'); c.width=256;c.height=256;
        const gl=c.getContext('webgl'); draw(gl); out.top_canvas=read(gl); }
  catch(e){ out.top_canvas={error:String(e)}; }
  try { const o=new OffscreenCanvas(256,256); const gl=o.getContext('webgl');
        draw(gl); out.top_offscreen=read(gl); }
  catch(e){ out.top_offscreen={error:String(e)}; }

  // 3-4: CreepJS's PHANTOM realm, built exactly as getPhantomIframe does —
  // innerHTML-created iframe, taken by INDEXED access, canvas constructed
  // from THAT realm's constructor (`new win.OffscreenCanvas`).
  try {
    const n=self.length;
    const frag=new DocumentFragment(); const div=document.createElement('div');
    div.innerHTML='<div style="display:none"><iframe></iframe></div>';
    frag.appendChild(div); document.body.appendChild(frag);
    const win=self[n];
    out.phantom_reached = !!win;
    if (win) {
      try { const o=new win.OffscreenCanvas(256,256); const gl=o.getContext('webgl');
            draw(gl); out.phantom_offscreen=read(gl); }
      catch(e){ out.phantom_offscreen={error:String(e)}; }
      try { const c=win.document.createElement('canvas'); c.width=256;c.height=256;
            const gl=c.getContext('webgl'); draw(gl); out.phantom_canvas=read(gl); }
      catch(e){ out.phantom_canvas={error:String(e)}; }
    }
  } catch(e){ out.phantom_error=String(e); }
  return out;
}
"""

HTML = "<!doctype html><html><body><p>ps193 realm probe</p></body></html>"


def run(seed):
    path = pathlib.Path("/tmp/ps193/realm.html")
    path.write_text(HTML)
    with sync_playwright() as p:
        b = p.firefox.launch(headless=False)
        ctx = b.new_context()
        if seed is not None:
            ctx.add_init_script(firefox_webgl_init_script(seed))
        pg = ctx.new_page()
        pg.goto("file://%s" % path, wait_until="load", timeout=60000)
        pg.wait_for_timeout(1500)
        out = pg.evaluate(PROBE)
        b.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default="/tmp/ps193/realm-probe.json",
        help="where to write the probe record (default: /tmp/ps193/realm-probe.json)",
    )
    args = ap.parse_args()

    res = {}
    for seed in (None, 1337, 4242):
        res["unspoofed" if seed is None else "seed_%d" % seed] = run(seed)
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(res, indent=2))

    realms = ["top_canvas", "top_offscreen", "phantom_canvas", "phantom_offscreen"]
    print("%-20s %-12s %-12s %-12s  verdict" % ("realm", "unspoofed", "seed1337", "seed4242"))
    for r in realms:
        vals = []
        for k in ("unspoofed", "seed_1337", "seed_4242"):
            cell = (res.get(k) or {}).get(r) or {}
            vals.append(cell.get("fnv"))
        moved = (vals[1] is not None and vals[2] is not None and vals[1] != vals[2])
        verdict = "REACHED (seed-dependent)" if moved else "NOT REACHED (identical)"
        print("%-20s %-12s %-12s %-12s  %s" % (r, vals[0], vals[1], vals[2], verdict))
    print("phantom_reached:", (res.get("seed_1337") or {}).get("phantom_reached"))


if __name__ == "__main__":
    main()
