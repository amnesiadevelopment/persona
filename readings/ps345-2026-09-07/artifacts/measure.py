#!/usr/bin/env python3
"""PS-345 — read measureText through a real engine, headless, via CDP.

No CDP library assumed: we drive the browser with --dump-dom on a data: URL is
not enough (we need JS results), so we use --headless + --remote-debugging-port
and speak raw websocket... Simpler: use --headless=new with a page that writes
the result into document.title, and read it with --dump-dom? --dump-dom returns
the serialized DOM AFTER load, so writing results into the DOM works.
"""
import subprocess, sys, json, tempfile, os, re, html

STRINGS = ["A", "hello", "persona-PS345", "The quick brown fox jumps"]

PAGE = """
<html><body><div id=out>PENDING</div><script>
var c = document.createElement('canvas');
var ctx = c.getContext('2d');
ctx.font = '16px sans-serif';
var strs = %s;
var r = {};
for (var i=0;i<strs.length;i++) {
  var m = ctx.measureText(strs[i]);
  r[strs[i]] = {
    width: m.width,
    abbL: m.actualBoundingBoxLeft,
    abbR: m.actualBoundingBoxRight,
    fbbA: m.fontBoundingBoxAscent,
    fbbD: m.fontBoundingBoxDescent,
    abbA: m.actualBoundingBoxAscent,
    abbD: m.actualBoundingBoxDescent,
    emA: m.emHeightAscent,
    emD: m.emHeightDescent,
    alphabetic: m.alphabeticBaseline,
    hanging: m.hangingBaseline,
    ideographic: m.ideographicBaseline
  };
}
document.getElementById('out').textContent = JSON.stringify(r);
</script></body></html>
""" % json.dumps(STRINGS)


def run(binary, extra_args, label):
    with tempfile.TemporaryDirectory() as td:
        page = os.path.join(td, "p.html")
        open(page, "w").write(PAGE)
        # NO --user-data-dir: pointed at a fresh empty profile, stock
        # Chrome-for-Testing blocks on its first-run GCM registration and hangs
        # past any timeout, which reads as "stock produced no value" — a false
        # negative on the control arm. (PS-301 measured this.)
        cmd = [binary, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--no-first-run", "--virtual-time-budget=3000",
               "--dump-dom"] + extra_args + ["file://" + page]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        m = re.search(r'<div id="out">(.*?)</div>', p.stdout, re.S)
        if not m:
            return {"_error": "no out div", "_stderr": p.stderr[-2000:], "_stdout": p.stdout[:2000]}
        txt = html.unescape(m.group(1))
        if txt == "PENDING":
            return {"_error": "script did not run"}
        try:
            return json.loads(txt)
        except Exception as e:
            return {"_error": str(e), "_raw": txt[:500]}


if __name__ == "__main__":
    binary = sys.argv[1]
    label = sys.argv[2]
    extra = sys.argv[3:]
    out = run(binary, extra, label)
    print(json.dumps({"label": label, "binary": binary, "args": extra, "metrics": out}, indent=1))
