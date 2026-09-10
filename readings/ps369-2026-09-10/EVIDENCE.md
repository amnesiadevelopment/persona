# PS-369 — the Firefox `measuretext` cell, established BY MEASUREMENT against a STOCK Firefox

**Date:** 2026-09-10  **Tree:** `225ea15`  **Verdict:** the cell moves
`position_not_established` → **`not_applicable`**. **No spoof ships.**

⭐ **This was the LAST unclaimed cell of the cross-engine masking matrix.** With
it closed, `test_the_open_cells_are_the_deliverable_and_are_named`'s pinned set
is **empty** for the first time since PS-111 shipped the contract.

## The question, and why only a control could answer it

`measuretext_ext.py` **repairs** noise the Chromium fingerprint engine injects
into `Canvas::measureText` — a single multiplicative factor (~1e-6, its sign
set by the seed) applied to every returned metric, which breaks layout-heavy
apps that measure text. Chromium installs the builder unconditionally. Firefox
reaches none of it: `spawn_browser` returns on the Firefox arm before the
extension list is assembled, and `measureText` / `measuretext` / `TextMetrics` /
`actualBoundingBox` appear **zero times** across `invisible_launch.py`
(against firing controls in the same file: `_install_spoof` ×9,
`add_init_script` ×10).

`tests/test_engine_masking_matrix.py` recorded the cell as
`position_not_established`, and named the gap in its own words:

> No spoof, and NO RECORDED REASON. The Chromium builder repairs noise the
> FINGERPRINT ENGINE injects into Canvas::measureText, so a plausible position
> is *"not applicable — a different engine does not inject that noise"*.
> **Not recorded, so not claimed.**

⭐ **That sentence is a claim about TWO browsers, and the tree held only one.**
The 20 committed Firefox artifacts under `readings/` answer the first half
unanimously — persona's Firefox reports real, font-differentiated widths and
leaves `measureText` unwrapped — and **every one of them is persona's own
engine**. They cannot answer the second half at any sample size, and more of
them would not help. Only a **stock Firefox** can.

## Environment — the shelf life

| | |
|---|---|
| host | Linux 6.8.0-138-generic, x86_64, glibc 2.41, Debian-family container, 8 cores / 15 GB |
| display | Xvfb `:90` (A1/A2/R/N are headless; arm B is persona's **headful** launch) |
| **installed font families** | **3** — `DejaVu Sans`, `DejaVu Sans Mono`, `DejaVu Serif` |
| **stock control** | upstream **Firefox 151.0**, `ftp.mozilla.org`, tarball `sha256 8ff8557a…`, binary `sha256 29a6161c…` |
| **subject engine** | persona `firefox-20` (`firefox-20_151.0_20260817150018`), binary `sha256 db519632…` |
| `invisible_playwright` | `BINARY_VERSION` `firefox-20`, `FIREFOX_UPSTREAM_VERSION` `151.0` |
| taken at | `2026-09-10T04:31:51Z`, one run, one host |

⭐ **The version confound is REMOVED, not merely disclosed.** Playwright's own
`firefox` channel installs **153.0**; it was deliberately discarded. Upstream
**151.0** was pulled instead, because `invisible_playwright.FIREFOX_UPSTREAM_VERSION`
is `151.0` — so **both arms report `Mozilla Firefox 151.0`** and an A1-vs-A2
divergence cannot be a version difference. `test_the_reading_names_its_shelf_life`
asserts that equality, so re-taking this reading on mismatched versions goes red.

⚠️ **THE HOST FONT STACK IS NAMED AS WELL AS THE BUILD, and on this probe that
is not a formality.** `fonts.measureText` is in `ENV_SENSITIVE_PROBES`
(`src/services/verify/baseline.py`) precisely because everything that moves a
text width between two machines — which fonts are installed, which rasteriser,
which hinting — moves it. This host has **3 font families**, so its absolute
magnitudes are a statement about *fontconfig* and must never be quoted off it.
The rows this cell rests on are the **host-invariant** ones (see below).

⚠️ And note what `ENV_SENSITIVE_PROBES` does **not** do, in that file's own
words: *"It is DOCUMENTATION, not a filter. `compare()` diffs every probe and
consults this tuple NOWHERE."* Listing excuses no movement — it tells a reader
which lines to distrust across hosts.

## ⛔ The de-confounding statement

**What differs between the arms besides the product:**

| axis | A1 (stock) | A2 (persona engine) | B (product path) |
|---|---|---|---|
| binary | upstream tarball | invisible-playwright cache | invisible-playwright cache |
| Firefox version | 151.0 | 151.0 | 151.0 |
| host, fonts, page, run, minute | **identical** | **identical** | **identical** |
| channel | marionette | marionette | juggler (product's own eval hook) |
| headless | yes | yes | **no** — headful under Xvfb |
| launcher | bare `-profile` | bare `-profile` | `spawn_browser` → `_spawn_invisible` |

So **A1 vs A2 varies exactly ONE thing — which binary is executed** — and any
divergence between them is attributable to persona's engine patches and to
nothing else. **B varies everything the product does** and answers the separate
question "does anything on the shipping path wrap `measureText` after all".
A1/A2 both run over **marionette** rather than juggler because stock Firefox has
no juggler channel: driving the two binaries over different channels would
confound **binary** with **channel**, and an agreement from such a pair could be
attributed to neither (PS-171 arm C's construction).

⛔ **THE STOCK ARM IS A CONTROL AND IT IS NOT THE PRODUCT.** Nothing arm A1 or
the two mutated stock arms report may be attributed to persona's behaviour **in
either direction** — the `readings/ps159-2026-08-25` rule. It is launched
directly by `scripts/ps369_measuretext_control.py`, deliberately **never**
through the product's engine resolver, on the discipline
`scripts/ps150_stock_control.py`, `scripts/ps301_engine_launch.py` and
`scripts/ps350_stealth_control.py` carry. **No product code was modified to take
this reading**; the two control injections are probe-side and confined to arms
R and N, as PS-312 and PS-350 did.

## Method

`scripts/ps369_measuretext_control.py`. Probe expressions are **imported
verbatim** from `src/services/verify/probes.py` by `Probe.id` and run through
the product's own `runner.window_expression` / `runner.worker_expression` — so
this measures what persona records, in the two realms persona records it in, and
cannot drift from the inventory. A retyped expression measures the typist. The
14-font list is likewise **parsed out of the probe** rather than retyped, and
the harness refuses to run if it is not 14 long.

### Five arms, one host, one run

| arm | what | what it can attribute |
|---|---|---|
| **A1** | STOCK FF 151.0, bare binary, marionette, headless | the **CONTROL** — what an ordinary Firefox reports. Attributable to Mozilla, **never** to persona. |
| **A2** | persona `firefox-20`, bare binary, marionette, headless | the matched **SUBJECT**. Same channel, gesture, page, host, minute — the only difference from A1 is **which binary is executed**. |
| **B** | `spawn_browser(in_process=True)`, headful under Xvfb | the **PRODUCT** — launcher, prefs, per-tab masking layer and all. |
| **R** | stock + a **multiplicative noise wrapper** (the DEFECT) | **reveal control** — are the probes live? |
| **N** | stock + `measuretext_ext`'s **own repair** (the FIX) | ⭐ the cell's central claim, **measured** rather than argued. |

**Every arm passed every gate** before a single row was read: `1+1 == 2` · real
loopback `origin` (never opaque) · `isSecureContext == true` · UA is Gecko ·
**layout box > 0** (the document *rendered*, not merely parsed) · **a canvas 2d
context exists** · `Worker` available. The harness **refuses to emit** an arm
whose gates did not pass, because a browser that never started reports every row
as absent — which is byte-identical to a perfect match. An **identity gate**
also discards any arm whose answering process (read off `/proc`) did not run out
of the install its label names; `navigator.buildID` cannot do that job here,
since the engine pins it to `20181001000000`.

## The reading

### ✅ Row 1 — the wrapper shape. Identical on all three reading arms, both realms.

| arm | `masking.measureText` (window) | (worker) |
|---|---|---|
| **A1 STOCK** | `function measureText() {\n    [native code]\n}` | `absent:undefined` |
| **A2 persona engine** | **identical** | **identical** |
| **B persona product path** | **identical** | **identical** |

⭐ **BOTH HALVES OF THE REALM SPLIT REPRODUCE ON STOCK.** The 20 committed
persona artifacts report `[native code]` in the window realm and
`absent:undefined` in the worker realm, unanimously — and a **stock** Firefox
does exactly the same. So the worker row is a property of **Firefox** (the probe
finds no `CanvasRenderingContext2D` in a worker realm to inspect), **not** a gap
in persona's corpus. The two realms are stated separately here for that reason:
the window row says *nothing is wrapped*, the worker row says *there is nothing
there to inspect*, and a record collapsing them would claim more than the tree
holds.

### ✅ Row 2 — the noise signature. `|v| < 1` on **0 of 14** fonts, every reading arm, both realms.

| arm | window | worker |
|---|---|---|
| A1 STOCK | **0/14** | **0/14** |
| A2 persona engine | **0/14** | **0/14** |
| B persona product path | **0/14** | **0/14** |
| *R (defect installed)* | *14/14* | *0/14 — see the scope note* |

**This is the host-invariant row, and it is why this reading survives a 3-font
host.** The Chromium defect collapses all 14 metrics to `|v| < 1` (`0.001` /
`-0.0` / `-0.001`, moving with the seed — re-derived from the committed
artifacts this session: 6 of 6 seeded Chromium window rows read 14/14). Real
16px text widths are two orders of magnitude above that on **any** font stack —
even a fully-fallback host reports ~200 — so this count separates *noised* from
*unnoised* **without depending on which fonts are installed**. An
absolute-width comparison cannot do that.

⇒ **Neither a stock Firefox nor persona's Firefox injects the noise
`measuretext_ext` exists to repair, and neither wraps `measureText`.**

### ⭐⭐ Row 3 — the claim the ticket ARGUED, now MEASURED: "one tell traded for two"

Arm **N** installs `measuretext_ext`'s own repair on a stock Firefox — a browser
with nothing to repair — and reads both halves:

| | A1 (untouched stock) | N (repair installed) | |
|---|---|---|---|
| `fonts.measureText`, window | 14 widths | **identical, 14/14** | the arithmetic is a **STRUCTURAL NO-OP** |
| `fonts.measureText`, worker | 14 widths | **identical, 14/14** | |
| `masking.measureText`, window | `function measureText() {\n    [native code]\n}` | `m() { return inner.apply(this, arguments); }` | the **WRAPPER IS OBSERVABLE** |

The repair's own guard is why: `var corrupt = hasText && !(Math.abs(m.width) >= 1);
if (!corrupt) return m;` — real widths are ~200, so the guard returns the native
metrics untouched and the divisor never runs. **Zero benefit, and a new tell.**

⭐ **THAT IS THE SAME SHAPE PS-350 MEASURED ON `stealth_ext`'s downlinkMax
shim** — a Chromium builder that is not merely *unnecessary* on this engine but
*structurally inert* on it — and it is what makes this cell `NOT_APPLICABLE`
rather than `NOT_COVERED_RECORDED`. The constraint is not "we chose not to";
it is **"the engine cannot reach the configuration this vector addresses"**: the
vector repairs an injected noise factor, and this engine injects none, so the
repair's own guard refuses to fire.

⚠️ **THE BOUND ON ARM N, stated rather than left for a reader to find.** N
carries `patch()`'s guard, its divisor and its `length`/`name`/`__pnaName`
cloak, reduced exactly as PS-350 reduced `stealth_ext`'s two `defineProperty`
calls. It does **not** carry the realm registry, the worker bootstrap, or
`_native_cloak_js`, which the Firefox route inlines as a PRELUDE into every init
script it installs — so a product route would answer
`Function.prototype.toString` differently. ⛔ **That is not the same as "the tell
goes away."** This project's own knowledge article PS-22 records that toString is
**one read out of at least four**, and that `getOwnPropertyNames` / `.length` /
`.name` are *cheaper* for a detector to run. The reduction bounds arm N's claim
about the *shape of the string*; it does not soften the finding that a JS
function stands where a native one stood.

### The controls that fired

**R (the DEFECT):** installing a ×1e-6 wrapper moved **both** readable rows —
widths collapsed to 14/14 below one (`0` on every font), and
`masking.measureText` stopped reading `[native code]`. So a reading of "no
noise, not wrapped" on the unmutated arms is a **live instrument's answer**, not
a probe that reports a constant.

⚠️ **R IS WINDOW-SCOPED AND CANNOT COVER THE WORKER**, and its worker row is
therefore *unmoved by construction* rather than a probe that failed to see a
defect. R patches `CanvasRenderingContext2D.prototype` in the **page** realm over
marionette; the worker harness builds a fresh `Worker` from a Blob with its own
globals, which a page-realm prototype patch never reaches.

**So the worker leg has its OWN liveness control, and it is inter-arm
variation:** the three reading arms returned **3 distinct worker vectors** on one
host in one run (A1: 3 distinct widths; A2: 7; B: 8). A probe reporting a
constant could not do that. Recorded as `realm_liveness` in the artifact.

## ⚠️ THE MAGNITUDES DIVERGE — and it is the host-font confound, NOT an engine finding

`fonts.measureText` is **0/14 identical** between A1 and A2 in both realms. ⛔
**Do not read that as a divergence finding.** This host has three font families
and `fonts.measureText` is env-sensitive by its own registry entry. The tell is
in the **shape**, not the numbers:

```
A1 stock firefox   :  3 distinct widths over 14 fonts  -> collapsed onto this host's fallbacks
A2 persona engine  :  7 distinct widths over 14 fonts  -> font-DIFFERENTIATED
B  persona product :  8 distinct widths over 14 fonts  -> font-DIFFERENTIATED
```

### ⭐ The cross-host ladder — and read its DIRECTION before quoting it

The 20 committed recordings were taken on **other hosts** (all `os_type: windows`
desktop profiles, full font stacks). Comparing each live arm against the
committed baseline artifact, key by key:

```
A1 stock firefox   vs committed corpus :   0/14 window    0/14 worker
A2 persona engine  vs committed corpus :   9/14 window   12/14 worker
B  persona product vs committed corpus :  14/14 window   14/14 worker
```

⛔ **A low score here is not a defect and a high score is not "the arms agree."**
The corpus is another host's, so a browser that simply reports what *this*
host's fontconfig resolves must score low. A **high** score means the arm
reproduced widths for fonts this host **does not have installed** — i.e. it
carried its own font set rather than the host's. That is the only claim a
cross-host magnitude comparison can support.

⇒ **persona's shipping path reproduces its committed 14-font vector EXACTLY on a
host with three fonts installed; a stock Firefox reproduces none of it.** The
divergence runs in the direction of persona **masking** the host font stack
rather than leaking it — the product working — and it is a *stronger* result for
this cell than a bare A1/A2 match would have been.

⚠️ **NOT settled, and flagged rather than filed:** whether that host-invariance
is uniformly good. It defeats host-fingerprinting, and it also means two
persona-Firefox profiles on different machines share an identical 14-font width
vector — the same *shape* as the `creepjs :: webgl_pixel_hash` cross-profile
linkage concern already recorded on this project (PS-177). **That is a different
cell and outside this slice.**

## ⇒ The position, and what it rests on

**`firefox:measuretext` → `NOT_APPLICABLE`.**

1. A **stock** Firefox neither wraps `measureText` nor injects the noise the
   Chromium builder repairs — the counterfactual no volume of persona-only
   recordings could reach.
2. persona's Firefox agrees with it on both host-invariant rows, in both realms,
   bare **and** through the shipping path.
3. The Chromium vector's repair is **structurally inert** on this engine
   (measured, arm N): its `corrupt` guard cannot fire where widths are real.
4. Installing it anyway would **add an observable JS wrapper where 20 of 20
   committed window readings — and a stock browser — read `[native code]`**.
   One tell traded for two, with the trade's *benefit* measured at zero.

## Shelf life — what to re-run, and when

Read on **persona `firefox-20` / Firefox 151.0 / Linux x86_64 / 3 font families
/ headless + headful-under-Xvfb**, `2026-09-10`, tree `225ea15`.

**Re-run `scripts/ps369_measuretext_control.py --stock <upstream firefox>` if:**

* a future Gecko starts injecting canvas-metric noise (row 2 would move), or
* a future Gecko ships `measureText` non-native (row 1 would move), or
* persona's engine gains a canvas-noise patch of its own, or
* the Chromium arm's repair is rewritten such that its `corrupt` guard could
  fire on real widths (row 3's no-op would stop being one).

`tests/test_ps369_ff_measuretext_live.py` asserts the outcome live where a stock
control is provisioned, and re-reads this artifact with no browser at all
everywhere else.

## ⛔ Honest bounds

1. ⛔ **THE STOCK ARM IS A CONTROL**, attributable to persona's behaviour in
   **neither direction**.
2. **n=1 host, Linux x86_64, 3 font families.** ⚠️ **The absolute magnitudes in
   this record are worthless off this host** — quote the *shape* (0/14 noise
   signature, the wrapper strings, 3-vs-7-vs-8 distinct, the 0/9/14 corpus
   ladder), never the numbers.
3. **All 20 committed recordings are `os_type: windows` desktop profiles**, and
   arm B declares `windows` too (coherence refuses every other os_type for
   Firefox). A reading on another declared machine is not established here.
4. **Arm N is a REDUCTION** of `measuretext_ext`'s repair, and its omissions are
   named above. It establishes that the arithmetic no-ops and that a JS function
   replaces a native one; it does not measure what the full product route's
   cloak would answer on each of PS-22's four axes.
5. ⛔ **NOT an Invariant #0 claim, and no leak is claimed.** No host fact is
   known to escape. This is masking *invisibility* / cross-engine equivalence.
6. **Frequency unmeasured.** No operator report and no checker reading cites
   measureText on Firefox.
7. **Arm A2 is persona's engine driven BARE**, not the product path — that is
   what makes it the channel-matched twin of A1. Arm B is the product path, and
   it is the arm that speaks for what persona ships.

## Artifacts

* `reading.json` — all five arms, every gate, both realms, all 14 fonts, plus
  the four derived comparison surfaces (`noise_signature`, `wrapper_shape`,
  `corpus_agreement`, `realm_liveness`), each computed from the recorded arms
  rather than measured a second time.
* `scripts/ps369_measuretext_control.py` — the instrument.
* `tests/test_ps369_ff_measuretext_live.py` — the live half (skips without a
  stock control) and the offline half (re-reads this artifact on every CI run).
