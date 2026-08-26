// PS-182 — does the Firefox WebGL readback carry per-profile entropy?
//
// RUN:  node readings/ps182-2026-08-26/harness.js
// Needs: node only. No browser, no display, no network, no proxy, no credential.
//
// WHAT THIS IS. The ticket asks a question that had to be settled by measurement
// before any fix could be written: *on the Firefox path, what perturbs the
// readback — and does anything?* Two seeds landing on one value is consistent
// with BOTH "our delta is not delivered" AND "there is no delta on this path",
// and the committed corpus cannot tell those apart.
//
// This harness tells them apart WITHOUT a browser, by executing the SHIPPED
// script — `webgl_ext.firefox_webgl_init_script(seed)`, the exact text
// `invisible_launch.py:3345` installs — in a fresh JS realm and reading pixels
// back through the prototype it patched. It is not a re-implementation of the
// perturbation: a re-implementation would prove only that this file is
// self-consistent, which is the PS-11 failure class.
//
// WHY THE ANSWER IS TRUSTWORTHY: THE HARNESS IS VALIDATED AGAINST REAL OUTPUT.
// Geometry A below reproduces the digests a REAL firefox-20 engine recorded in
// `readings/ps135-2026-08-24/reading.firefox.seed*.json` **bit-identically**
// (2372980207 @111, 1471895271 @1337, 1444116715 @4242), and its unperturbed
// value matches the layer-off chromium counterfactual (2952899525). A harness
// that hits three independently-recorded engine digits on the nose is
// reproducing the product's arithmetic, not its own.
//
// ⚠️ THE INSTRUMENT DEFECT THIS FILE WAS BUILT AROUND — read before editing.
// The first version of this harness ran every seed in ONE node process, sharing
// one `Object`. The shipped per-realm idempotency guard
// (`worker_wrap.realm_guard_js`) stores its flag at `Object.__pnaRealm.webgl`,
// so seed 1337 marked the realm and EVERY LATER SEED RETURNED EARLY HAVING
// PATCHED NOTHING. That produced a clean, plausible, entirely FAKE collision —
// the product looked broken because the instrument was. Hence `vm.createContext`
// per seed: one fresh realm per profile, which is what a real page load is.
// PS-14's rule ("an identical result across every cell is the shape to
// distrust") caught this. Do not collapse the realms back into one.

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SEEDS = [111, 1337, 4242, 9001];

// FNV-1a — the exact reduction `probes.py webgl.readback` applies, so a digest
// here is comparable to a digest in the committed corpus.
function fnv1a(buf) {
  let h = 2166136261;
  for (let i = 0; i < buf.length; i++) h = Math.imul(h ^ buf[i], 16777619);
  return h >>> 0;
}

// How many bytes the shipped mid-range guard (`v > 1 && v < 254`) admits. This
// is the number the whole question turns on.
function eligible(buf) {
  let n = 0;
  for (let i = 0; i < buf.length; i++) if (buf[i] > 1 && buf[i] < 254) n++;
  return n;
}

// --- Geometry A: the loopback probe's OWN draw ------------------------------
// `probes.py webgl.readback`: 32x32, four scissored mid-range bands, RGBA
// UNSIGNED_BYTE, opaque alpha. This is the gate PS-90 added and the one the
// ticket directs us to build and verify against.
function probeBuffer() {
  const W = 32, H = 32;
  const bands = [[0.31,0.45,0.60],[0.55,0.35,0.69],[0.42,0.62,0.38],[0.66,0.51,0.28]];
  const px = new Uint8Array(W * H * 4);
  for (let y = 0; y < H; y++) {
    const b = bands[Math.floor(y / (H / 4))];
    for (let x = 0; x < W; x++) {
      const o = (y * W + x) * 4;
      px[o] = Math.round(b[0] * 255);
      px[o + 1] = Math.round(b[1] * 255);
      px[o + 2] = Math.round(b[2] * 255);
      px[o + 3] = 255;                       // opaque: NOT eligible (>= 254)
    }
  }
  return px;
}

// --- Geometry B/C: CreepJS's corner -----------------------------------------
// `webgl_ext.py:30-45` records the census measured on a real engine: CreepJS
// reads a `drawingBufferWidth/15 x drawingBufferHeight/6` corner, which off a
// 256x256 canvas is 17x42 = 2856 bytes, ~98.9% cleared zeros, of which only ~16
// pass the mid-range guard. `nEligible` is the only variable: it is what
// separates "starved but working" from "nothing to work on".
function creepBuffer(nEligible) {
  const px = new Uint8Array(17 * 42 * 4);
  for (let i = 3; i < px.length; i += 4) px[i] = 255;   // cleared, opaque alpha
  for (let k = 0; k < nEligible; k++) px[k * 4] = 128;  // antialiased edge bytes
  return px;
}

// Execute the SHIPPED script in a FRESH realm, then read pixels through it.
// `which`: 1 = WebGL1 only, 2 = WebGL2 only (the Firefox worker shape
// `probes.py` measured), 3 = both.
function runShipped(seed, base, which) {
  const src = fs.readFileSync(
    path.join(__dirname, 'scripts', `ff_${seed}.js`), 'utf8');

  const sb = {};
  vm.createContext(sb);            // fresh intrinsics => fresh realm guard

  // The fake GL context is built INSIDE the sandbox realm on purpose: the
  // shipped `perturbBytes` gates on `buf instanceof Uint8Array`, and a typed
  // array from another realm fails that test. Building it outside would make
  // the patch appear to do nothing — another way to fake a collision.
  vm.runInContext(`
    globalThis.__base = null;
    function C1() {}
    C1.prototype.readPixels = function (x,y,w,h,f,t,px) { px.set(globalThis.__base); };
    function C2() {}
    C2.prototype.readPixels = function (x,y,w,h,f,t,px) { px.set(globalThis.__base); };
    if (${which === 1 || which === 3}) globalThis.WebGLRenderingContext = C1;
    if (${which === 2 || which === 3}) globalThis.WebGL2RenderingContext = C2;
    globalThis.self = globalThis;
    globalThis.window = globalThis;
  `, sb);

  vm.runInContext(
    `globalThis.__base = new Uint8Array(${JSON.stringify(Array.from(base))});`, sb);

  vm.runInContext(src, sb);        // install the spoof, exactly as a page would

  const out = vm.runInContext(`
    (function () {
      var r = {};
      if (globalThis.WebGLRenderingContext) {
        var d1 = new Uint8Array(globalThis.__base.length);
        new globalThis.WebGLRenderingContext().readPixels(0,0,0,0,0,0,d1);
        r.gl1 = Array.from(d1);
      }
      if (globalThis.WebGL2RenderingContext) {
        var d2 = new Uint8Array(globalThis.__base.length);
        new globalThis.WebGL2RenderingContext().readPixels(0,0,0,0,0,0,d2);
        r.gl2 = Array.from(d2);
      }
      return r;
    })();
  `, sb);

  const res = {};
  for (const k of ['gl1', 'gl2']) {
    if (out[k]) {
      const got = Uint8Array.from(out[k]);
      let moved = 0;
      for (let i = 0; i < base.length; i++) if (got[i] !== base[i]) moved++;
      res[k] = { digest: fnv1a(got), moved };
    }
  }
  return res;
}

function measure(label, base, which, note) {
  const unperturbed = fnv1a(base);
  const elig = eligible(base);
  const perSeed = {};
  const distinct = { gl1: new Set(), gl2: new Set() };

  for (const s of SEEDS) {
    const r = runShipped(s, base, which);
    perSeed[s] = r;
    for (const k of ['gl1', 'gl2']) if (r[k]) distinct[k].add(r[k].digest);
  }

  console.log(`\n=== ${label}`);
  if (note) console.log(`    ${note}`);
  console.log(`    bytes=${base.length}  guard_eligible=${elig}  unperturbed=${unperturbed}`);
  for (const s of SEEDS) {
    const parts = [];
    for (const k of ['gl1', 'gl2']) {
      if (perSeed[s][k]) {
        parts.push(`${k} digest=${String(perSeed[s][k].digest).padEnd(11)} moved=${String(perSeed[s][k].moved).padEnd(4)}` +
                   (perSeed[s][k].digest === unperturbed ? ' <UNPERTURBED>' : ''));
      }
    }
    console.log(`    seed ${String(s).padEnd(5)} ${parts.join('  ')}`);
  }
  const summary = {};
  for (const k of ['gl1', 'gl2']) {
    if (distinct[k].size) {
      summary[k] = distinct[k].size;
      const verdict = distinct[k].size === 1
        ? `1  <<< COLLISION (all ${SEEDS.length} seeds identical)`
        : `${distinct[k].size} of ${SEEDS.length}`;
      console.log(`    -> ${k} distinct digests: ${verdict}`);
    }
  }
  return { label, note, bytes: base.length, guard_eligible: elig,
           unperturbed, per_seed: perSeed, distinct: summary };
}

// --- Validation against the committed corpus --------------------------------
// The claim "this harness reproduces the product" is itself checked, not
// asserted. These three digests were recorded by a REAL firefox-20 engine in
// readings/ps135-2026-08-24/, under xvfb, months of code-changes ago.
const CORPUS_PS135_FIREFOX = { 111: 2372980207, 1337: 1471895271, 4242: 1444116715 };

const results = [];
results.push(measure(
  'A: the loopback probe draw (probes.py `webgl.readback`), 32x32 mid-range bands',
  probeBuffer(), 1,
  'The gate PS-90 added. Content-rich: 3072 of 4096 bytes pass the guard.'));

results.push(measure(
  'B: CreepJS corner 17x42 — 16 eligible of 2856 (the census webgl_ext.py:30-45 measured)',
  creepBuffer(16), 1,
  'Starved but NOT empty. This is the shape PS-97 fixed the Chromium side for.'));

results.push(measure(
  'C: CreepJS corner 17x42 — ZERO guard-eligible bytes',
  creepBuffer(0), 1,
  'The counterfactual: a fully cleared render, nothing for the guard to admit.'));

results.push(measure(
  'D: WebGL2-ONLY realm (the Firefox worker shape probes.py measured)',
  probeBuffer(), 2,
  "probes.py declares webgl.readback WINDOW_ONLY because in a FF worker only " +
  "'webgl2' yields a context. If the patch missed WebGL2 that would be an " +
  'in-scope Firefox delivery gap.'));

// --- Report the validation --------------------------------------------------
console.log('\n=== VALIDATION against readings/ps135-2026-08-24 (real firefox-20 engine)');
let allMatch = true;
for (const s of Object.keys(CORPUS_PS135_FIREFOX)) {
  const got = results[0].per_seed[s].gl1.digest;
  const want = CORPUS_PS135_FIREFOX[s];
  const ok = got === want;
  if (!ok) allMatch = false;
  console.log(`    seed ${String(s).padEnd(5)} harness=${String(got).padEnd(11)} ` +
              `engine=${String(want).padEnd(11)} ${ok ? 'MATCH' : '*** MISMATCH ***'}`);
}
console.log(`    -> ${allMatch
  ? 'ALL MATCH. The harness reproduces real engine output bit-identically.'
  : 'MISMATCH — do not trust the measurements above until this is explained.'}`);

fs.writeFileSync(path.join(__dirname, 'result.json'),
  JSON.stringify({ seeds: SEEDS, results,
                   corpus_validation: { source: 'readings/ps135-2026-08-24',
                                        expected: CORPUS_PS135_FIREFOX,
                                        all_match: allMatch } }, null, 2));
console.log('\nwrote result.json');
