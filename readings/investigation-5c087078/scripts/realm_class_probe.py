#!/usr/bin/env python3
"""
Question B: is the ServiceWorker realm ONE hole, or is a CLASS of realm
structurally unreachable from an extension/injection layer?

PS-189 recorded three REFUSALS for the service-worker realm:
  - blob: scriptURL           -> TypeError (protocol not supported)
  - cross-origin scriptURL    -> SecurityError
  - register() is patchable   -> but no delivery technique exists

Those are facts about ServiceWorker. They do not, on their own, say whether
other realms share the property. This script tests the DISCRIMINATOR directly:
for each worker-ish realm, can it be bootstrapped from a blob: URL (the
technique the whole injection path depends on)?

If blob: works for dedicated/shared/module workers and ONLY ServiceWorker
refuses it, the answer is "one realm, for a specific spec reason", not "a class".

Run:  python3 realm_class_probe.py
Needs: /usr/bin/chromium (headless), a local HTTP origin (spawned here).
"""
import json, os, subprocess, sys, tempfile, threading, http.server, socketserver, functools

PAGE = """<!doctype html><meta charset=utf-8><title>realm probe</title>
<script>
const out = {};
function fin(k, v) { out[k] = v; render(); }
function render() { document.title = 'DONE:' + JSON.stringify(out); }

// 1. dedicated worker from a blob:
try {
  const b = new Blob(["self.postMessage({ok:true, scope:'DedicatedWorkerGlobalScope'})"],
                     {type:'text/javascript'});
  const w = new Worker(URL.createObjectURL(b));
  w.onmessage = m => fin('worker_blob', {accepted:true, data:m.data});
  w.onerror  = e => fin('worker_blob', {accepted:false, refusal:String(e.message||e)});
} catch (e) { fin('worker_blob', {accepted:false, refusal:String(e)}); }

// 2. module worker from a blob:
try {
  const b = new Blob(["self.postMessage({ok:true, type:'module'})"], {type:'text/javascript'});
  const w = new Worker(URL.createObjectURL(b), {type:'module'});
  w.onmessage = m => fin('worker_module_blob', {accepted:true, data:m.data});
  w.onerror  = e => fin('worker_module_blob', {accepted:false, refusal:String(e.message||e)});
} catch (e) { fin('worker_module_blob', {accepted:false, refusal:String(e)}); }

// 3. shared worker from a blob:
try {
  const b = new Blob(["onconnect=e=>{e.ports[0].postMessage({ok:true, scope:'SharedWorkerGlobalScope'})}"],
                     {type:'text/javascript'});
  const s = new SharedWorker(URL.createObjectURL(b));
  s.port.onmessage = m => fin('shared_worker_blob', {accepted:true, data:m.data});
  s.onerror = e => fin('shared_worker_blob', {accepted:false, refusal:String(e.message||e)});
  s.port.start();
} catch (e) { fin('shared_worker_blob', {accepted:false, refusal:String(e)}); }

// 4. service worker from a blob:  <-- the one PS-189 recorded as refused
try {
  const b = new Blob(["self.addEventListener('install',()=>{})"], {type:'text/javascript'});
  navigator.serviceWorker.register(URL.createObjectURL(b))
    .then(() => fin('service_worker_blob', {accepted:true}))
    .catch(e => fin('service_worker_blob', {accepted:false, refusal:String(e)}));
} catch (e) { fin('service_worker_blob', {accepted:false, refusal:String(e)}); }

// 5. service worker from a SAME-ORIGIN url (control: does registration work at all?)
try {
  navigator.serviceWorker.register('/sw.js')
    .then(() => fin('service_worker_sameorigin', {accepted:true}))
    .catch(e => fin('service_worker_sameorigin', {accepted:false, refusal:String(e)}));
} catch (e) { fin('service_worker_sameorigin', {accepted:false, refusal:String(e)}); }

// 6. iframe srcdoc (a non-worker realm, for contrast)
try {
  const f = document.createElement('iframe');
  f.srcdoc = "<script>parent.postMessage({ok:true, realm:'srcdoc'},'*')<\\/script>";
  window.addEventListener('message', m => {
    if (m.data && m.data.realm === 'srcdoc') fin('iframe_srcdoc', {accepted:true, data:m.data});
  });
  // NOTE: this script runs before <body> is parsed, so document.body is null here.
  // An earlier run of this harness used document.body.appendChild and recorded a
  // bogus "TypeError: Cannot read properties of null (reading 'appendChild')" as
  // though it were a browser refusal. It was an instrument bug, not a result.
  (document.body || document.documentElement).appendChild(f);
} catch (e) { fin('iframe_srcdoc', {accepted:false, refusal:String(e)}); }
setTimeout(render, 4000);
</script><body></body>
"""

SW_JS = "self.addEventListener('install', () => {});\n"


def main():
    d = tempfile.mkdtemp(prefix="realmprobe-")
    open(os.path.join(d, "index.html"), "w").write(PAGE)
    open(os.path.join(d, "sw.js"), "w").write(SW_JS)

    Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=d)
    class Q(socketserver.TCPServer):
        allow_reuse_address = True
        def handle_error(self, *a): pass
    httpd = Q(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/index.html"

    prof = tempfile.mkdtemp(prefix="realmprofile-")
    cmd = ["/usr/bin/chromium", "--headless=new", "--no-sandbox", "--disable-gpu",
           f"--user-data-dir={prof}", "--virtual-time-budget=8000",
           "--dump-dom", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    dom = r.stdout
    # title carries the JSON
    import re
    m = re.search(r"DONE:(\{.*?\})</title>", dom, re.S)
    if not m:
        print("could not read probe result; stderr tail:", r.stderr[-500:], file=sys.stderr)
        print("DOM head:", dom[:400], file=sys.stderr)
        return 1
    res = json.loads(m.group(1))
    print(json.dumps(res, indent=1))
    open("realm-class-probe.json", "w").write(json.dumps(res, indent=1))

    print("\n=== VERDICT ===")
    blobs = {k: v for k, v in res.items() if k.endswith("_blob")}
    ok = [k for k, v in blobs.items() if v.get("accepted")]
    no = [k for k, v in blobs.items() if not v.get("accepted")]
    print("blob: bootstrap ACCEPTED by:", ok)
    print("blob: bootstrap REFUSED  by:", no)
    if no == ["service_worker_blob"] and len(ok) >= 2:
        print("=> ONE REALM, not a class: every other worker realm accepts a blob: bootstrap.")
    elif len(no) > 1:
        print("=> A CLASS of realm refuses the blob: bootstrap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
