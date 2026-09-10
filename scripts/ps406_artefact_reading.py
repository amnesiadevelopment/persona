#!/usr/bin/env python3
"""PS-406 acceptance 1+2, re-measured independently off the CI artefact.

Reads THREE numbers off the binary produced by run 34513928710, not off its
exit code:

  A. reference render, <=16 distinct colours, --fingerprint ON vs OFF
     -> PS-373 guard PRESENT iff the two are BYTE-IDENTICAL.
  B. realistic canvas, ~60 distinct colours, --fingerprint ON vs OFF
     -> protection INTACT iff the two DIFFER. (A guard that is too wide would
        make this identical too, silently disabling masking on real
        fingerprinting canvases. Without this arm, arm A passing is also
        consistent with a guard that broke everything.)
  C. ctx.measureText widths under --fingerprint
     -> PS-345 fix PRESENT iff widths are POSITIVE and plausible.

The OFF arm is the same binary with the flag removed: an in-binary control, so
any difference is attributable to the flag and to nothing else.

THE VERDICT DIRECTION, STATED EXPLICITLY
────────────────────────────────────────
This script exits **non-zero when the artefact does NOT carry both fixes.**

    exit 0  →  BOTH PRESENT: arm A byte-exact, arm B differing, arm C positive.
    exit 1  →  DEFECT: at least one arm failed. Which one is printed.

It is a guard, not a transcript. `ps301_measuretext_repro.py` exists because the
script before it had "162 lines, one conditional, zero non-zero exit paths" — a
confident-looking transcript that exited 0 whatever the numbers said. This file
does not repeat that.

WHY ARM B IS NOT OPTIONAL
─────────────────────────
Arm A alone is satisfied by a guard that is *too wide* — one that suppressed
canvas noise everywhere, which would silently disable masking on the real
fingerprinting canvases the product exists to protect. Arm A passing is
therefore consistent with the protection being destroyed. Arm B is what
separates "the guard is correctly narrow" from "the guard broke everything",
and a reading that omits it has not established what it appears to.

WHY TWO SEEDS
─────────────
`--fingerprint`'s noise factor is seed-derived, so a single seed cannot tell a
working flag from an INERT one: if the flag did nothing, arm A would be
byte-exact too, and for the wrong reason. Running a second seed and observing
that arm B's hash MOVES while arm A stays byte-exact is what proves the flag is
live. Seed 777 is the deliberate choice for the second: PS-345 drew it
specifically because its factor is POSITIVE, so on the broken engine it returns
spec-legal widths that are still collapsed by seven orders of magnitude — the
case a naive "is the width negative?" check passes. See `ps301_measuretext_repro.py`,
whose `constant and implausible` branch was written for exactly that seed.

⚠️ WHAT THIS DOES NOT ESTABLISH. It is a LINUX artefact, and it says nothing
about a Windows engine. It is also not a checker reading: no proxy, no exit and
no pixelscan are involved, so it cannot speak to the masking badge or to
PS-406's acceptance item 3.
"""
import asyncio, json, os, subprocess, sys, tempfile, shutil, time
import websockets

ENGINE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ps406/enginedir/fpchrome.AppImage"
SEED = int(os.environ.get("PS406_SEED", "24601"))

PAGE_JS = r"""
(() => {
  const out = {};

  // --- A: reference render, small distinct-colour count (<=16) ---
  const rc = document.createElement('canvas'); rc.width = 64; rc.height = 64;
  const rx = rc.getContext('2d');
  const pal = ['#000000','#ffffff','#ff0000','#00ff00','#0000ff','#ffff00',
               '#00ffff','#ff00ff','#808080','#800000','#008000','#000080',
               '#808000','#008080'];  // 14 distinct colours
  for (let i = 0; i < pal.length; i++) {
    rx.fillStyle = pal[i];
    rx.fillRect((i % 7) * 9, Math.floor(i / 7) * 32, 9, 32);
  }
  const rd = rx.getImageData(0, 0, 64, 64).data;
  let rh = 2166136261 >>> 0;
  for (let i = 0; i < rd.length; i++) { rh ^= rd[i]; rh = Math.imul(rh, 16777619) >>> 0; }
  out.ref_hash = rh.toString(16);
  out.ref_colours = new Set(Array.from({length: rd.length/4},
      (_, p) => rd[p*4] + ',' + rd[p*4+1] + ',' + rd[p*4+2] + ',' + rd[p*4+3])).size;
  out.ref_bytes = Array.from(rd.slice(0, 4096));

  // --- B: realistic fingerprinting canvas, many distinct colours ---
  const fc = document.createElement('canvas'); fc.width = 240; fc.height = 60;
  const fx = fc.getContext('2d');
  const g = fx.createLinearGradient(0, 0, 240, 60);
  g.addColorStop(0, '#f60'); g.addColorStop(0.5, '#069'); g.addColorStop(1, '#0f9');
  fx.fillStyle = g; fx.fillRect(0, 0, 240, 60);
  fx.font = '16px Arial'; fx.fillStyle = 'rgba(102,204,0,0.7)';
  fx.fillText('Personium canvas fp \u2620 1.0', 4, 24);
  fx.strokeStyle = 'rgba(0,0,80,0.5)';
  fx.beginPath(); fx.arc(120, 30, 22, 0, Math.PI * 2); fx.stroke();
  const fd = fx.getImageData(0, 0, 240, 60).data;
  let fh = 2166136261 >>> 0;
  for (let i = 0; i < fd.length; i++) { fh ^= fd[i]; fh = Math.imul(fh, 16777619) >>> 0; }
  out.fp_hash = fh.toString(16);
  out.fp_colours = new Set(Array.from({length: fd.length/4},
      (_, p) => fd[p*4] + ',' + fd[p*4+1] + ',' + fd[p*4+2] + ',' + fd[p*4+3])).size;
  out.fp_bytes = Array.from(fd.slice(0, 4096));

  // --- C: measureText ---
  const mc = document.createElement('canvas');
  const mx = mc.getContext('2d');
  mx.font = '16px Arial';
  out.widths = {};
  for (const s of ['Personium measureText probe 12345', 'W', 'iiiii', 'The quick brown fox']) {
    out.widths[s] = mx.measureText(s).width;
  }
  return out;
})()
"""


async def read_via_cdp(ws_url):
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
        tid = None
        while tid is None:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                for t in m["result"]["targetInfos"]:
                    if t["type"] == "page":
                        tid = t["targetId"]
        await ws.send(json.dumps({"id": 2, "method": "Target.attachToTarget",
                                  "params": {"targetId": tid, "flatten": True}}))
        sess = None
        while sess is None:
            m = json.loads(await ws.recv())
            if m.get("id") == 2:
                sess = m["result"]["sessionId"]
        await ws.send(json.dumps({"id": 3, "sessionId": sess, "method": "Page.enable"}))
        await ws.send(json.dumps({"id": 4, "sessionId": sess, "method": "Page.navigate",
                                  "params": {"url": "data:text/html,<html><body>ps406</body></html>"}}))
        await asyncio.sleep(1.5)
        await ws.send(json.dumps({"id": 5, "sessionId": sess, "method": "Runtime.evaluate",
                                  "params": {"expression": PAGE_JS, "returnByValue": True,
                                             "awaitPromise": True}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 5:
                r = m["result"]["result"]
                if "value" not in r:
                    raise RuntimeError(f"eval failed: {json.dumps(m)[:800]}")
                return r["value"]


def run_arm(fingerprint: bool):
    udd = tempfile.mkdtemp(prefix="ps406-")
    args = [ENGINE, "--appimage-extract-and-run", "--no-sandbox", "--headless=new",
            "--disable-dev-shm-usage", "--disable-gpu",
            f"--user-data-dir={udd}", "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check", "--no-proxy-server",
            "about:blank"]
    if fingerprint:
        args[6:6] = [f"--fingerprint={SEED}", "--fingerprint-platform=windows",
                     "--fingerprint-brand=Chrome"]
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file = os.path.join(udd, "DevToolsActivePort")
        for _ in range(300):
            if os.path.exists(port_file):
                txt = open(port_file).read().split("\n")
                if len(txt) >= 2 and txt[0].strip():
                    path = txt[1].strip()
                    if not path.startswith("/"):
                        path = "/" + path
                    ws_url = f"ws://127.0.0.1:{txt[0].strip()}{path}"
                    time.sleep(0.5)
                    return asyncio.run(read_via_cdp(ws_url))
            time.sleep(0.2)
        raise RuntimeError("engine never published DevToolsActivePort")
    finally:
        p.terminate()
        try:
            p.wait(timeout=15)
        except Exception:
            p.kill()
        shutil.rmtree(udd, ignore_errors=True)


def main():
    print(f"engine: {ENGINE}")
    print(subprocess.run([ENGINE, "--appimage-extract-and-run", "--version"],
                         capture_output=True, text=True).stdout.strip())
    on = run_arm(True)
    off = run_arm(False)

    def bytediff(a, b):
        return sum(1 for x, y in zip(a, b) if x != y)

    ref_diff = bytediff(on["ref_bytes"], off["ref_bytes"])
    fp_diff = bytediff(on["fp_bytes"], off["fp_bytes"])

    guard_ok = ref_diff == 0 and on["ref_hash"] == off["ref_hash"]
    protection_ok = fp_diff > 0 and on["fp_hash"] != off["fp_hash"]

    print("\n=== A. REFERENCE RENDER (PS-373 guard) ===")
    print(f"distinct colours: ON={on['ref_colours']}  OFF={off['ref_colours']}")
    print(f"hash  ON={on['ref_hash']}  OFF={off['ref_hash']}")
    print(f"modified bytes (first 4096): {ref_diff}")
    print("VERDICT: guard PRESENT" if guard_ok else "VERDICT: guard ABSENT/BROKEN")

    print("\n=== B. REALISTIC CANVAS (protection still intact) ===")
    print(f"distinct colours: ON={on['fp_colours']}  OFF={off['fp_colours']}")
    print(f"hash  ON={on['fp_hash']}  OFF={off['fp_hash']}")
    print(f"modified bytes (first 4096): {fp_diff}")
    print("VERDICT: protection INTACT" if protection_ok
          else "VERDICT: GUARD TOO WIDE - masking disabled on real canvases")

    print("\n=== C. measureText (PS-345 fix) ===")
    widths_ok = True
    for s, w in on["widths"].items():
        o = off["widths"][s]
        ratio = (w / o) if o else float("nan")
        # A width must be POSITIVE *and* plausible. The sign alone is not the
        # test: PS-345's seed 777 returns spec-legal positive widths that are
        # still collapsed by seven orders of magnitude, and a sign-only check
        # passes it. The ratio against the same binary's flag-OFF arm is what
        # catches that, because a healthy noise factor is centred on 1.
        bad = w <= 0 or not (0.9 < ratio < 1.1)
        widths_ok = widths_ok and not bad
        print(f"  {s[:34]!r:38} ON={w:>18.6f}  OFF={o:>10.4f}  ratio={ratio:.6f}"
              + ("   <-- IMPLAUSIBLE" if bad else ""))
    print("VERDICT: fix PRESENT (positive and plausible)" if widths_ok
          else "VERDICT: DEFECT - width non-positive or implausible")

    json.dump({"on": {k: v for k, v in on.items() if not k.endswith("_bytes")},
               "off": {k: v for k, v in off.items() if not k.endswith("_bytes")},
               "ref_modified_bytes": ref_diff, "fp_modified_bytes": fp_diff,
               "guard_present": guard_ok, "protection_intact": protection_ok,
               "widths_plausible": widths_ok,
               "seed": SEED, "engine": ENGINE},
              open(f"/tmp/ps406/reading-{SEED}.json", "w"), indent=2)
    print(f"\nwrote /tmp/ps406/reading-{SEED}.json")

    failed = [n for n, ok in (("A/guard", guard_ok),
                              ("B/protection", protection_ok),
                              ("C/measureText", widths_ok)) if not ok]
    if failed:
        print(f"\nOVERALL: DEFECT — failing arms: {', '.join(failed)}")
        return 1
    print("\nOVERALL: the artefact carries BOTH fixes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
