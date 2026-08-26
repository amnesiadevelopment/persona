# PS-193 — Byte census of the region CreepJS samples, on a real Firefox engine

**Taken:** 2026-08-26, 22:5x–23:0xZ
**Engine:** Firefox **151.0** (playwright build **v1532**), `Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0`
**Renderer:** `Mozilla` / `llvmpipe, or similar` (Mesa software GL under Xvfb — see §6 caveats)
**Exits:** six live arms, six different addresses, **no fallback on any of them** (PS-10)
**Repo:** `/workspace/persona`, branch `feature/193-firefox-webgl-byte-census`, off `main` @ `de0f199`

---

## 1. The answer, in the two numbers the ticket asked for

CreepJS's sampled region, measured **at CreepJS's own `readPixels` call**, on a real
Firefox engine, through the proxied exit:

| | |
|---|---|
| **total bytes in the region** | **2912** |
| **bytes passing the guard `v > 1 && v < 254`** | **32** (1.10%) |
| of which colour (non-alpha) bytes | 27 of 2184 |
| zeros | 2868 |
| bytes == 255 | 12 |
| bytes == 1 | 0 |

Reproduced **identically on four independent live arms through four different exits and
four different ASNs**, and on both the `webgl` and the `webgl2` context pass within each
arm (CreepJS reads twice per page — eight readings in total, all 2912/32).

| arm | file | exit | ASN |
|---|---|---|---|
| 1 | `live/census-arm1.json` | `46.205.195.157` | AS12912 T-Mobile Polska · Warsaw |
| 2 | `live/census-arm2.json` | `188.146.164.8` | AS12912 T-Mobile Polska · Warsaw |
| 3 | `live/census-arm3.json` | `79.191.52.210` | AS5617 Orange Polska · Warsaw |
| 4 | `live/census-arm4.json` | `87.206.189.140` | AS9141 P4 Sp. z o.o. · Ruda Śląska |

---

## 2. What the number says about the two candidates

The ticket named two surviving causes and asked which the census supports.

### ❌ Candidate 1 — "the sampled region is starved" — **REFUTED**

The leading hypothesis was that CreepJS's corner holds **~zero** bytes passing the
mid-range guard, making the perturbation a no-op *there specifically*. That is PS-182's
geometry **C**, and it is the only geometry that reproduced the collision.

**The region holds 32 guard-eligible bytes, not zero.** Three consequences, each of which
independently kills the starvation account:

1. **32 ≠ 0**, so the patch is not a no-op by construction. Geometry C is refuted as the
   live geometry.
2. **32 > 16**, and 16 is PS-182's geometry **B** — the *starved-but-working* case, which
   yielded **four distinct digests at four seeds**. The live region has **twice B's
   headroom**, so if B was sufficient this is comfortably sufficient.
3. `_BUDGET = 512` (`webgl_ext.py:76`) is far above 32, so the shipped selector spends its
   budget on **every one of the 32** eligible bytes — there is no decimation and no
   lottery at this size. Whatever else is wrong, it is not that too few bytes were chosen.

### ✅ Candidate 2 — "the delta never reaches the page realm" — **CONFIRMED**

With the census in hand, candidate 2 became directly testable rather than merely the
survivor, and it was tested rather than inferred. Two live arms with the **shipped**
`firefox_webgl_init_script(seed)` installed (the product's own text, not a
transcription — PS-11/PS-182), at two seeds, reducing CreepJS's received bytes with
FNV-1a at its own callsite:

| | seed 1337 | seed 4242 | |
|---|---|---|---|
| digest CreepJS actually received | `1379655975` | `1379655975` | **IDENTICAL — collides** |
| digest in the **page realm**, same run, same instant | `855826239` | `1729355265` | **differs — spoof ran** |

Files: `live/spoof-seed1337.json`, `live/spoof-seed4242.json`.

**An identical result across cells is the exact shape PS-14 says to distrust**, and it has
two incompatible explanations: *the spoof never executed* (instrument failure, no finding)
or *the spoof executed and its delta does not reach CreepJS's realm* (the finding). The
page-realm column is the **positive control** that separates them — it is taken in the
same run, after the same init scripts, and it **moves with the seed**. So the perturbation
demonstrably executed, and CreepJS still received the unperturbed bytes.

**The census supports candidate 2.**

---

## 3. The mechanism, isolated on loopback

Candidate 2 names a realm, so the follow-up needs no exit at all: "which realms does our
own perturbation reach" is a property of our code, not of the checker or the network.
`realm_probe.py` reproduces CreepJS's construction exactly and reads back in four realms
at two seeds (`loopback/realm-probe.json`):

| realm | unspoofed | seed 1337 | seed 4242 | verdict |
|---|---|---|---|---|
| `top_canvas` | 660023932 | 855826239 | 1729355265 | **REACHED** (seed-dependent) |
| `top_offscreen` | 660023932 | 855826239 | 1729355265 | **REACHED** |
| `phantom_canvas` | 660023932 | 660023932 | 660023932 | **NOT REACHED** |
| `phantom_offscreen` | 660023932 | 660023932 | 660023932 | **NOT REACHED** |

**It is the REALM, not `OffscreenCanvas`.** `top_offscreen` is an OffscreenCanvas and it is
reached; `phantom_canvas` is an ordinary canvas and it is not. Reading
`canvas_class: OffscreenCanvas` off the census record and concluding "we miss
OffscreenCanvas" would have been the wrong fix, aimed at the wrong axis.

### Why that realm is missed, in both codebases' own words

CreepJS creates its context in a **phantom iframe**, and takes the realm by **indexed
window access**:

```js
// creep.js — getPhantomIframe()
div.innerHTML = `<div style="${GHOST}"><iframe></iframe></div>`;
document.body.appendChild(frag);
const iframeWindow = self[numberOfIframes];      // ← INDEXED, not .contentWindow

// creep.js — the WebGL block
let win = window;
if (!LIKE_BRAVE && PHANTOM_DARKNESS) { win = PHANTOM_DARKNESS; }
canvas = new win.OffscreenCanvas(256, 256);      // ← constructed FROM that realm
```

Our realm chain hooks the **accessors**:

```js
// worker_wrap.py:387-403 — realm_bootstrap_js
var IF = G.HTMLIFrameElement;
["contentWindow", "contentDocument"].forEach(function (prop) { ... });
```

`self[N]` is a `WindowProxy` read off the window's indexed properties. **It never invokes
`HTMLIFrameElement.prototype.contentWindow`**, so the chain's only entry point into a child
frame is never triggered, and the leaf is never installed in that realm.

This also corrects a docstring: `firefox_webgl_init_script`'s own text says
`realm_bootstrap_js` "carries the leaf onward into workers and child frames, so the worker
realm is covered too" (`webgl_ext.py:288-290`). That is true for a frame reached through
the accessors and **false for one reached by index** — which is the one CreepJS uses.

**This is the PS-155/PS-161/PS-189 failure class on the same axis PS-189 found — the
REALM.** PS-189's service worker is unreachable because there is no constructor to
intercept; this frame is unreached because the interception point is an accessor the
consumer never touches. Same shape, different door.

---

## 4. Why the census had to be taken at CreepJS's own callsite

Four live arms recorded **zero** `readPixels` calls while CreepJS still published a pixel
hash. **None of those four is a finding — all four are bugs in my instrument**, and they
are recorded here rather than quietly fixed because PS-14 is explicit that the instrument
is checked first and an identical/absent result is the shape to distrust.

| # | bug | why it produced a silent zero |
|---|---|---|
| 1 | `add_init_script` on the **page**, and `set_content` to load the fixture | `set_content` writes into the existing document rather than navigating, so no init script ever ran. Hook silently absent. |
| 2 | records read from the **main frame only** | `add_init_script` runs per **frame**, each with its own `window.__ps193`. Records landing in a child frame were invisible. |
| 3 | phantom realm hooked via **`contentWindow` getter** | CreepJS takes it by indexed access — §3. The getter never fires. |
| 4 | prototype wrapping in the **top realm** | The `gl` object's prototype belongs to the *iframe* realm, so the top realm's patched prototype is not on its chain. |

The fix is to stop guessing which realm owns the object and instrument **CreepJS's own
line**: `creep.js` is fetched through the same proxied context, and one observer call is
appended after its `readPixels` returns.

**This stays PS-11-clean.** It censuses **the bytes CreepJS receives**, in the buffer
CreepJS allocated, at the moment it receives them. Nothing about the draw, the geometry or
the reduction is re-implemented — a re-implementation would only have proved the harness
self-consistent, which is the PS-11 failure class arriving inside the instrument built to
avoid it. The observer runs *after* the call returns and cannot change what was read.

**The instrument carries its own check.** `callsite_patch: {"seen": 1, "applied": true}` is
recorded on every arm: the literal must be found **exactly once**. Had it drifted, the
reading would be **not covered** rather than zero.

---

## 5. The geometry, measured rather than assumed

The `17 x 42 = 2856` figure in `webgl_ext.py:33-45` is a **prior loopback census**, so this
run recorded the call's parameters instead of assuming them. It **does not reproduce**, and
the difference is real:

```
w = 17.066666666666666      h = 42.666666666666664      → 2912 bytes
```

CreepJS computes `drawingBufferWidth / 15` and `drawingBufferHeight / 6` with **no
`Math.floor`** (§3), so the dimensions are **fractional**. `new Uint8Array(w * h * 4)`
allocates from the fractional product — **2912** bytes — while `readPixels` truncates to
integer dimensions and fills a 17×42 sub-region. So **2856 bytes are written and 56 are
left at zero**, and the buffer CreepJS hashes is 2912 long.

Both numbers are therefore correct about different things, and the honest total for "the
region CreepJS samples" is **2912** — that is the array whose contents become the hash.
The 56-byte tail is part of why the zero count is so high.

---

## 6. Caveats, stated in both directions

**The renderer is not the packaged engine.** This engine is `llvmpipe` (Mesa software GL)
under Xvfb, not persona's packaged `firefox-20` with SwiftShader. Consequences, stated
plainly:

- **Our absolute hash is `a8ee71dc`, NOT the corpus's `51df3565`.** We reproduce the
  **shape** of the defect (one value, invariant across seeds, while the page realm varies),
  **not the value**. The value is renderer-dependent and this is a different renderer.
- **32 is this engine+renderer's figure for this scene.** A different rasteriser antialiases
  differently and would give a nearby but not identical count. What is robust is the
  **order of magnitude and the sign**: the region is sparse (1.1%) but **not empty**, which
  is the discriminating fact, and 32 clears PS-182's geometry-B threshold of 16 with margin.
- **The realm finding is renderer-INDEPENDENT.** §3 is about which realms a script is
  installed into. It does not depend on the rasteriser at all, and it is reproduced on
  loopback with no exit in the picture.

**The census arms ran unspoofed.** The census is a property of **the scene CreepJS draws**,
which is what DoD 1 asks for — the guard-eligible count is what our perturbation *has to
work with*, so measuring it with our own perturbation already applied would be circular.
The spoofed arms in §2 are separate and labelled.

**Two "spoof" arms in `live/census-arm3/4.json` are unspoofed.** `run.sh` initially did not
forward `$SPOOF_ARG` to the census command. Rather than discard them they are kept **as
census arms**, which is what they honestly are — they took the census through two further
exits (n=4) and established the unspoofed FNV baseline `1379655975` twice, which is exactly
the baseline §2's spoofed arms are compared against. The bug is recorded rather than tidied
away because the fix is what makes §2's arms meaningful.

**`declared_machine_honoured: false` on Firefox** is engine limitation #211 — expected, not
a finding.

---

## 7. Provisioning (the ticket's precondition #2, now closed)

The ticket recorded that this container cannot render Firefox WebGL, and that a run taken in
that state returns `null` — **byte-identical to the signature of a spoof that never loaded**,
i.e. indistinguishable from the defect under test. That was re-verified true at the start of
this session (`getContext('webgl')` → `null`, `webglcontextcreationerror`:
`Exhausted GL driver options (FEATURE_FAILURE_WEBGL_EXHAUSTED_DRIVERS)`), and then **closed**:

1. Firefox 151.0 provisioned via `playwright install firefox`.
2. Xvfb extracted from Debian `.deb`s into a prefix (no root available).
3. Xvfb hard-codes `/usr/bin/xkbcomp`; **`proot -b`** rebinds that path without root.
4. Mesa **llvmpipe** software GLX supplies the driver.

Smoke-tested on loopback before any live arm was spent, per the ticket's method constraint.
`run.sh` reports **`ENGINE_NOT_PROVISIONED`** and exits 90 if Xvfb never binds, so an engine
failure can never be mistaken for a census of zero.

**DoD 4 is not exercised: the census WAS obtained.** Neither failure mode applies — the
engine was provisioned, and the exit answered on every one of the six arms.

---

## 8. Re-deriving this reading

```bash
# provisioning is described in §7; it leaves an Xvfb-backed Firefox on :98
readings/ps193-2026-08-26/run.sh live  /tmp/out.json /tmp/out.log 150        # the census
readings/ps193-2026-08-26/run.sh live  /tmp/s1.json  /tmp/s1.log  150 1337   # spoofed arm
readings/ps193-2026-08-26/run.sh live  /tmp/s2.json  /tmp/s2.log  150 4242   # spoofed arm
readings/ps193-2026-08-26/realm-run.sh                                       # loopback mechanism
```

The credential is pinned to the **file** channel with `env -u PERSONA_TEST_PROXY` (the
env var on this container is a *different provider* carrying a session token minted at
container creation), and Firefox is pointed at persona's own
`services/proxy/bridge.ProxyBridge` — Playwright refuses a SOCKS5 launch with a credential
outright (*"Browser does not support socks5 proxy authentication"*), which is the same
limitation `chromium_tier.py:13-16` documents on the other engine. The credential never
reaches the browser.

---

## 9. What this does and does not license

**In scope of this ticket and delivered:** the census, the verdict against the two
candidates, and the mechanism.

**Explicitly NOT licensed by this reading — the ticket's out-of-scope list, re-affirmed by
the result.** Widening the shared mid-range guard was to become arguable *only if the census
said the region was starved*. **It does not.** 32 eligible bytes against `_BUDGET = 512` means
the selector already spends its budget on every eligible byte in the region — widening the
guard would move more Chromium bytes to chase a hypothesis this reading has **refuted**,
which is precisely the PS-97-shaped mistake PS-182 was created to correct. **Do not widen
the guard on the strength of this reading.**

The fix this reading points at is **delivery into the realm the checker reads** (§3), and it
is a fix with real cost and real blast radius — it is a change to the shared
`realm_bootstrap_js` chain, which every module rides. That is a design decision, not a
worker call, so this ticket **measures and hands off** rather than implementing it.
