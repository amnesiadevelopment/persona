# PS-171 — Firefox tab stall: reproduction record

**Date:** 2026-08-25
**Ticket:** PS-171
**Deliverable:** a reproduction, characterised — *not* a fix.

---

## Verdict in one paragraph

**The stall REPRODUCES here, and persona's masking layer is NOT required to
produce it.** With the layer *entirely absent* — the packaged engine and nothing
else — opening the **third** tab blocks indefinitely and **never recovers**. It
reproduces headless (arm B) and headful (arm D), it reproduces *with* the layer
installed (arm A2), and it reproduces on **`about:blank`**, so neither the
per-tab spoof path nor the weight of checker pages is required.

At the stall the browser looks **idle-waiting, not exhausted** — but that
reading belongs to **one arm, and it is named rather than generalised.** In
**arm F**, the characterisation arm, every thread is in `S` (interruptible
sleep) at the stall and CPU decays **35.1% → 1.4%** across the following 40s
recovery watch, at **1.2 GB RSS on a 16 GB host**. That shape is a deadlock, not
resource starvation. **Arm A2 does not show the same picture at its own stall**
(54.4% CPU, `R=2` threads still running) and is not evidence for this claim; see
[the instrument warning](#method) before reading either number.

**The precise claim, stated at the strength the evidence carries it.** The
layer-off arms show the stall does not *need* persona's masking layer — that is
the load-bearing finding and it defeats the ticket's first lead on its own.
"Unchanged with and without the layer" is the *weaker* thing this record can say
about *shape*, not about *timing*: tab 3 blocked in every arm either way, but tab
2 took 0.9s (D), 12.0s (B) and 17.1s (A2), so the run-to-run spread across four
arms is wider than any layer-on/layer-off difference within it. **Do not read
these four arms as a timing comparison** — they are four independent runs, not a
matched pair, and N=4 cannot separate a layer cost from that spread.

**It is also not plain Firefox's defect, and not the engine patches' alone.**
[Arm C](#arm-c--the-stock-firefox-control) — the stock control the ticket asked
for first — ran **upstream Firefox 151.0 with nothing of persona's in the
process**, in this same headless GPU-less container, and it opened **five tabs
without stalling** (tab 3 in 0.03s). Persona's *patched* engine, run bare over
the same channel, did the same. So the container is not producing this on its
own, and neither the masking layer nor the engine patches is required. **The
suspect list is the launcher and the juggler channel** — and arm C changed the
channel and the binary together, so it **narrows to those two without picking
between them.**

**One thing this record does NOT settle**, and it is stated up front rather than
buried: every arm that reproduced the stall opened tabs through the **juggler
automation channel** (`ctx.new_page()`), which is *not* the gesture the owner
makes. See [Limitations](#what-this-record-does-not-settle). The stall is real
and does not require persona's masking layer; whether the *browser* or the
*automation channel* owns it is not yet proven.

---

## Environment — and the gap to the owner's machine

| | This record | Owner's report |
|---|---|---|
| OS | Debian 13 (trixie) container, kernel 6.8 | Windows, real desktop |
| persona | this tree, `fix/PS-171-firefox-tab-stall` | v3.0.0 install |
| Engine | `firefox-20_151.0_20260817150018` (Firefox 151.0) | as shipped by v3.0.0 |
| GPU | **none** — `/dev/dri` absent | real GPU |
| Compositor | **none** — no `DISPLAY`; Xvfb where headful was needed | real compositor |
| CPU / RAM | 8 cores / 16 GB | unknown |
| `/dev/shm` | 1.0 GB (size only — occupancy during the arms was **never sampled**) | unknown |

**The gap is material and cuts both ways.** A headless, GPU-less container can
fail to show a defect a real Windows desktop does. It can *also* produce a stall
of its own that a real desktop would not. This record therefore leans on
**control arms** rather than on the bare fact that something stalled.

**Be precise about what those controls hold constant, because an earlier draft of
this record was not.** Arms A2/B/D/E/F compare the same environment **with and
without persona's *masking layer*** — `PS171_LAYER=0` disables
`install_firefox_layer` and nothing else. **Persona's patched engine and its
launcher are CONSTANT across all five**, which are therefore *not* controlled by
that axis: a stall originating in the engine patches or in the launcher would
reproduce in every one of them and be attributed away. Saying "with and without
persona" of those arms **overstated them**, and that wording is withdrawn.

**The control that does move that axis is [arm C](#arm-c--the-stock-firefox-control),
and it has now been run** — plain upstream Firefox with nothing of persona's in
the process at all.

---

## Method

Tabs are opened **one at a time**, each with a bounded deadline (`SIGALRM`), so a
stall can be *observed and then interrogated* instead of merely waited on. After
each tab the harness **pings tab 1** (`evaluate("1+1")`) — that is the
discriminator between "the new tab stalled" and "the browser stopped answering".

A watchdog samples every firefox process independently of the blocked call:
process state, CPU, RSS, thread count, and per-thread states. **Which CPU
reading you get depends on the arm** — see the warning immediately below.

> **⚠️ TWO INSTRUMENTS WERE USED, AND THE EARLIER ONE IS WRONG. Check which
> schema a reading carries before you read its CPU column.**
>
> `ps pcpu` is an average over the process's *whole lifetime*, so a process that
> spun hard for ten seconds and then blocked forever keeps reporting a high
> number. It makes **spinning** and **blocked** indistinguishable — the single
> distinction this ticket turns on. The corrected sampler reads `utime+stime`
> deltas from `/proc/<pid>/stat`, which is **instantaneous**.
>
> **An earlier draft of this record claimed the bug was "caught and fixed before
> any reading was taken". That was false, and the committed data says so.** The
> fix landed *between* arms B/D and arms A2/E/F. Arms **B and D were taken on the
> uncorrected `ps pcpu` instrument** and their readings are captioned in-file to
> say so. The two schemas are distinguishable on sight:
>
> | Schema | CPU column | **Processes counted** | Arms | Harness |
> |---|---|---|---|---|
> | `cpu_total` / `threads_total` / `stats` / `phase` | **lifetime average** | **PARENT ONLY** — `ps comm`, matched on `"firefox" in comm` | B, D | `abd_harness.py` |
> | `cpu` / `rss_mb` / `thr` | **instantaneous** | **WHOLE PROCESS TREE** — `/proc/<pid>/cmdline`, matched on the engine path | A2, E, F | `a2.py`, `e2.py` |
>
> **⚠️ THE TWO INSTRUMENTS DIFFER IN MORE THAN THEIR CPU COLUMN — THEY DO NOT
> COUNT THE SAME PROCESSES.** `comm` is the kernel's `TASK_COMM_LEN` field,
> capped at **15 characters**, and Firefox's children are named `Web Content`,
> `Socket Process`, `RDD Process`, `WebExtensions`, `Utility Process`,
> `forkserver` — **none of which contains the string "firefox"**. So the `ps
> comm` matcher sees the parent and nothing else.
>
> **This is measured, not inferred — see [arm H](#arm-h--the-two-instruments-do-not-count-the-same-processes).**
> Both matchers were applied to one real engine at the same instant: `ps comm`
> found **1** process (the parent) at both tab 1 and tab 2, while `/proc cmdline`
> found **6** and then **11**.
>
> **Consequence: `n`, `threads_total` and `rss_mb_total` are NOT comparable
> across the two groups of arms.** Arms **B and F are the same configuration**
> (layer OFF, headless, `new_page`, `about:blank`), yet at the stall arm B reports
> `n=2` (`ctrl_nolayer.txt` @ `"t": 109.0`, the last sample before the drop) while
> arm F reports `n=12` (`F_new_page.txt` @ `"t": 53.1`) — that gap is the
> instrument, not the browser. The **1.2 GB figure in the headline is arm F's**
> and is a whole-tree number.
>
> These two numbers are stated as **the size of the instrument gap**, which is the
> one thing they can jointly support. They are **not** offered as a comparison of
> the two arms' process counts: the matchers do not count the same set, so no such
> comparison is available at any pair of values.
>
> **Arm B's ~750 MB is NOT a parent-only undercount of that quantity, and an
> earlier draft of this record said it was. That is withdrawn.** At `t=109.0`,
> `rss_mb_total = 769.2` is a **sum over two processes that are not both this
> run's browser** — see the contamination note below. It is not a reading of arm
> F's quantity at any scale, so the two numbers are not two views of one thing
> and must not be compared.
>
> ### Arm B ran on a box that already had a firefox process on it
>
> **`ctrl_nolayer.txt` @ `"t": 0.0` — the first watchdog sample, taken BEFORE
> `LAUNCH` returned at `t=1.86s`:**
>
> ```
> WD {"t": 0.0, "n": 1, "cpu_total": 6.4, "rss_mb_total": 489.6, "threads_total": 85, ...}
> ```
>
> A 490 MB, 85-thread firefox process the harness had not started. This is not an
> ordering artifact: `abd_harness.py` starts the watchdog thread *before* it
> constructs the engine, so `t=0.0` genuinely precedes this run's browser.
> **Arm D is the control on this and is clean** (`D.txt`, `t=0.0` → `"n": 0`), so
> this is arm B specifically, not a property of the instrument.
>
> **Where it lands: the survivor of the `2 → 1` drop is 0.5 MB and 2 threads from
> the pre-launch process, and the fit is over the survivor alone.** All 90 fitted
> samples (`t=125.2→304.3`) are `n=1`. Survivor at `t=304.3`: `rss 490.1 MB,
> 87 thr`; pre-launch process at `t=0.0`: `rss 489.6 MB, 85 thr`. The process that
> *vanished* at `t=111.1` accounted for `~275 MB / 74 threads`.
>
> **What the fit does and does not establish.** Over those 90 samples `cpu_total`
> decays 58.1 → 24.2 monotonically and fits
>
> ```
> cpu = 7433 / (t + 2.3)        max relative error 0.34%
> ```
>
> A hyperbola that tight *is* `cumulative_cpu_seconds / elapsed`, and **that is
> the whole of what it proves: the `cpu_total` column is a lifetime average.**
> It is a clean demonstration of the *instrument* defect and needs no claim about
> which process produced it.
>
> **The claim it was previously used for — "the process burned zero additional
> CPU while wedged, so arm B corroborates idle-waiting" — is WITHDRAWN.** Flat
> cumulative CPU over the tail is what an *untouched* process trivially shows;
> it corroborates nothing about a wedged browser. **The idle-wait reading rests
> on arm F alone**, as stated below.
>
> **One correction to the reasoning that produced this withdrawal, because it was
> re-derived here rather than taken on trust.** The age offset `k = +2.3s` is a
> sharp minimum (3–5× better than any value in `(-1.86, 0)`, the band this run's
> browser could occupy), which is what first placed the fitted process before the
> harness clock. But `k` identifies a process only under "cumulative CPU exactly
> constant", and that holds **only in the tail**: the `t=0.0` sample lies off the
> fitted curve by a factor of ~500, and under `k=+2.3` the fitted process must
> have gained **~74 s of CPU between `t=0` and `t=125`** — about 58% of a core
> sustained. So the survivor is **not** an idle bystander either, and 490 MB /
> 85 threads is not a 2.3 s-old process. **Identity is not settled by the fit in
> either direction, and it cannot be settled from this tree at all** — see the
> PID gap below. The withdrawal stands regardless of which process it was: the
> fit cannot bear the idle-wait claim under any of the readings.
>
> **The direct reading comes from arm F, and from arm F alone.** It is worth
> being exact about which sample, because an earlier draft of this record
> attributed arm F's quietest sample to arm A2 and that is false under every
> reading of it:
>
> | Arm | at the stall | at the end of the 40s recovery watch |
> |---|---|---|
> | **F** (`F_new_page.txt` @ `"t": 53.1` and @ `"t": 101.2`) | `cpu 35.1`, `thr {"S": 275}` | **`cpu 1.4`, `thr {"S": 267}`** ← the idle-wait reading |
> | **A2** (`A2.txt` @ `"t": 72.9` and @ `"t": 120.9`) | `cpu 54.4`, `thr {"R": 2, "S": 277}` | `n=0` — engine gone, no live sample |
>
> **Arm A2 cannot supply a 1.4% figure at all**: by `t=120.9` it had no
> processes left to sample. And at its *own* stall A2 shows **54.4%** with **two
> threads in `R`**, so "every thread in `S`, ~1% CPU" is **not** true of A2 and
> must not be stated across the arms. The idle-wait characterisation rests on
> **arm F**, exactly as the no-recovery characterisation does
> ([below](#process-loss-in-arm-a2--and-why-arm-bs-apparent-death-is-not-comparable)).
>
> Read `ctrl_nolayer.txt` @ `"t": 304.3`'s `"cpu_total": 24.2` with that in mind:
> it is an averaging artifact, **not** a browser burning a quarter of a core
> while wedged.

No live checker page was loaded, so no exit was required. (Had one been loaded,
the proxied exit would have been mandatory with no direct-connection fallback.)

---

## Arms and results

| Arm | Layer | Display | Tab open path | Instrument | Result |
|---|---|---|---|---|---|
| ~~A~~ | ~~ON~~ | ~~headless~~ | ~~`new_page`~~ | lifetime avg | **READING LOST — struck, see below** |
| **A2** | **ON** | headless | `new_page` | **instantaneous**, whole tree | tab1 ok, tab2 17.1s, **tab3 BLOCKED>40s**, no recovery |
| B | **OFF** (control) | headless | `new_page` | lifetime avg, **parent only** | tab1 1.9s, tab2 **12.0s**, **tab3 hung >280s** |
| D | **OFF** | **headful** (Xvfb) | `new_page` | lifetime avg, **parent only** | tab1 1.8s, tab2 0.9s, **tab3 stalled** |
| F | **OFF** | headless | `new_page` | **instantaneous**, whole tree | **the characterisation — below** |
| E | OFF | headless | `window.open` | instantaneous, whole tree | **null instrument — see Limitations** |
| **C1** | **n/a — stock Firefox** | headless | **`WebDriver:NewWindow`** (marionette) | instantaneous, whole tree | **ALL 5 TABS OPENED — no stall** |
| **C2** | **n/a — bare patched binary** | headless | **`WebDriver:NewWindow`** (marionette) | instantaneous, whole tree | **ALL 5 TABS OPENED — no stall** |
| **H** | OFF | headless | `new_page` (2 tabs only) | **both, side by side** | **instrument comparison — not a stall arm** |

**Arm A's original reading was lost and its numbers are struck from this record.**
It was written to `A.log`, and `.gitignore:183` is `*.log`, so it was never
committed — the *exact* trap this record's closing paragraph claims to have
beaten. That check was evidently run on the artifacts that survived rather than
on arm A. The previously-cited figures (`tab1 1.9s, tab2 0.9s, tab3 hung >290s`)
are **not reproducible from anything in this tree** and must not be quoted; with
no committed reading behind them a transcription error cannot even be ruled out.

**Arm A2 replaces it, and it is a genuine layer-ON arm** — the log records
`LAYER INSTALLED installed=['audio', 'locale', 'webgl'] failed={}`, so the spoofs
demonstrably registered rather than silently no-op'ing (the `context_for` defect
that once made a "layer-ON" arm install nothing). It was re-run on the
**corrected instrument**, so the layer-ON side of the comparison now sits on the
same sampler as the arm it is compared against, and it was written **directly
into this directory as `.txt`** with `git check-ignore` run *before* the run
rather than after.

**Four independent runs (A2, B, D, F). All four stalled at tab 3** — two with the
masking layer absent, one with it installed, one headful. Tab 1 and tab 2 always
opened, though not always quickly (arm B's tab 2 took 12.0s, arm A2's 17.1s).

### Arm F — what "lag" actually is

```
tab1 open                    | 6 procs, threads: R=2 S=139 D=1
--- tab2 open ok=True 1.04s  | pages=2, 11 procs, cpu 120.9%, rss 1079.9 MB
--- tab3 open ok=False 40.0s BLOCKED>40s
    ping-tab1 ok=False 8.0s BLOCKED>8s   pages=2
                             | 12 procs, cpu 35.1%, rss 1261.3 MB, threads: S=275
*** STALL at tab3: watching 40s for recovery
RECOVERY after 40s: ping ok=False 8.0s BLOCKED>8s
                             | 12 procs, cpu 1.4%, rss 1221.8 MB, threads: S=267
```

Against the four modes the ticket asks to distinguish:

- **UI stops repainting, page keeps working** — ✗ not this.
- **Page stops loading, browser responds** — ✗ **ruled out.** The ping of *tab 1*
  — an already-open, already-loaded tab — **also blocked**. It is not confined to
  the new tab.
- **Whole process stops responding** — ✓ **this one**, as observed through the
  automation channel.
- **Recovers after N seconds** — ✗ **no recovery.** Still blocked after a further
  40s; arm A2 likewise still blocked after a further 40s, and arm B sat blocked
  for **>280s**.

**And it is not exhaustion.** At the stall, **in arm F**: **every thread in `S`**
(interruptible sleep — idle-waiting, not running), CPU decaying **35.1% → 1.4%**
across the recovery watch, RSS **1.2 GB of 16 GB**. A browser that had run out of
something would be thrashing or dying. This one is *waiting for something that
never arrives* — deadlock-shaped. (**`/dev/shm` occupancy was never sampled
during any arm** — see [below](#process-loss-in-arm-a2--and-why-arm-bs-apparent-death-is-not-comparable);
it is not part of this reading.)

### How many tabs — hard limit or load-dependent?

**Tab 3, in 4 of 4 runs** (A2, B, D, F), across headless *and* headful, with
*and* without the masking layer. In this environment it behaves as a **hard
threshold at the third tab**, not a load-dependent one — consistent with the
owner's "третья вкладка".

Two honest qualifications. (1) His "sometimes the second" was **not** reproduced
as a hang, but arm B's tab 2 taking **12.0s** against ~1s elsewhere is a
plausible match for what a user would call the second tab lagging. (2) N=4 is
enough to say "consistently the third here", not enough to exclude a rare
second-tab hang.

### Content is not required

Every arm used **`about:blank`**. No WebGL, no audio, no canvas, no font probing.
This **disproves the "checker pages are heavy, three at once exhausts something"**
lead as a *necessary* condition: the stall arrives with three empty documents.

### The control arm — the finding that matters most

Arm B and arm D ran with `install_layer=False`: **the packaged engine, with none
of persona's masking layer**. The stall is **unchanged**. Persona's per-tab spoof
path — `add_init_script` plus the replay into already-open tabs
(`invisible_launch.py`, `masking_layer.install_firefox_layer`) — is **not
required to produce this**. The ticket's first lead ("the masking extensions run
per tab… tab three would be where it shows") is **not supported**.

**Arm D carries most of this weight, and more of it than earlier drafts credited
it with.** Arm B's evidentiary value is reduced by the pre-launch foreign process
disclosed above, so of the two layer-off arms **only arm D is *both* layer-off
*and* verifiably uncontaminated at `t=0.0`** (`D.txt` → `"n": 0` before launch).
Arm D's single matched process also **stayed alive across the whole stall**
(`n=1` continuously from `t=4.1` to the final sample at `t=98.8`), so it carries
no unexplained process loss of its own.

**Note what the contamination does and does not touch.** It bears on arm B's
*process-count and CPU aggregates*. It does **not** touch the layer-off finding,
which rests on the **stall still occurring** with the layer absent — observed in
arm D and arm F through `new_page` blocking and the tab-1 ping blocking on the
automation channel, neither of which is a process count. The load-bearing finding
is unaffected.

### Arm C — the stock-Firefox control

**This is the control the ticket asked for first, and earlier rounds of this
record did not run it.** Every other arm launches through persona's
`InvisiblePlaywright` launcher; `PS171_LAYER=0` removes the masking layer and
nothing else, so **persona's patched engine and its launcher were constant across
all of them**. A stall living in the engine patches or in the launcher would have
reproduced in every arm and been attributed away. Arm C moves that axis.

**Two runs, one file** (`c_stock.py` → `C_stock.txt`), both driven over
**marionette** so the channel, gesture, instrument and profile flags are held
constant and only the *binary* changes:

| Run | Binary | Persona involvement | Result |
|---|---|---|---|
| **C1** | upstream Firefox 151.0 from `ftp.mozilla.org` | **none at all** | **`ALL_5_TABS_OPENED`** |
| **C2** | persona's `firefox-20_151.0_20260817150018`, run **bare** | patched engine only — no launcher, no layer, no juggler | **`ALL_5_TABS_OPENED`** |

**Both binaries report `Mozilla Firefox 151.0`**, so C1-vs-C2 is not a version
comparison — it isolates persona's **engine patches**.

**Neither stalled.** Tab 3 — the tab the owner reports, and the tab every juggler
arm blocks on — opened in **0.03s** (C1) and **0.04s** (C2). Tab 1 kept answering
`1+1` after every tab in both runs, and a 40s recovery watch on each caught no
late-arriving stall.

**What this licenses.** Plain upstream Firefox **does not stall on the third tab
in this container** — the same headless, GPU-less, compositor-less container that
produces the stall through persona's launcher. So the stall is **not a bare-Firefox
defect persona merely inherits**, and it is **not the environment on its own**.
And since C2 is persona's *patched* binary opening five tabs just as cleanly, **the
engine patches alone do not produce it either.** Had C1 stalled, this record's
conclusion would have changed outright; it did not.

> **⚠️ WHAT THIS DOES *NOT* LICENSE — arm C narrows the stall, it does not
> attribute it.** C2 differs from the juggler arms in **more than one variable at
> once**: the launcher, the automation channel (marionette vs juggler) **and** the
> profile prefs (`MOZ_DISABLE_CONTENT_SANDBOX=1` and the `user.js` listed in the
> caption) all differ. A quiet C2 therefore narrows the stall to
> **launcher-or-juggler and cannot pick between them.**
>
> In particular this is **not** "stock is fine, so persona's build is at fault."
> Neither C arm uses juggler, so a quiet arm C is **equally consistent** with the
> stall needing the juggler channel to be expressed at all — in which case arm C
> simply cannot reach it. That reading is not excluded by anything here.

### Arm H — the two instruments do not count the same processes

The round-2 code review observed from the *source* that the two harnesses might
not be counting the same processes. **Arm H measures it** rather than leaving it
as an inference: one real engine, **both matchers applied at the same instant**,
each printing what it actually matched (`h_matchers.py` → `H_matchers.txt`).

```
--- tab 1 open
    ps comm       n=1   pids=[3791]
    /proc cmdline n=6   pids=[3791, 3835, 3893, 3899, 3907, 3941]
    SEEN ONLY BY /proc cmdline: Socket Process, forkserver, WebExtensions,
                                RDD Process, Web Content
--- tab 2 open
    ps comm       n=1   pids=[3791]
    /proc cmdline n=11  pids=[3791, 3835, 3893, 3899, 3907, 3941, 3990,
                              4023, 4028, 4042, 4044]
    SEEN ONLY BY /proc cmdline: + Utility Process, 4x Web Content
RESULT {"ps_comm_n": {"tab1": 1, "tab2": 1},
        "proc_cmdline_n": {"tab1": 6, "tab2": 11},
        "matchers_agree": false}
```

**The `ps comm` matcher matched the parent and nothing else — not one child, at
either sample.** The reason is mechanical: `comm` is the kernel's
`TASK_COMM_LEN` field, capped at **15 characters**, and every child is named
`Web Content` / `Socket Process` / `RDD Process` / `WebExtensions` /
`Utility Process` / `forkserver`. **None contains the substring "firefox"**, so
`"firefox" in comm` cannot match any of them. (The 15-char cap is itself
verifiable: `prctl(PR_SET_NAME, "IsolatedWebContentProcess")` reads back from
`ps comm` as `IsolatedWebCont`.)

**What this invalidates.** Arms **B/D** report a **parent-only** count; arms
**A2/E/F** report the **whole tree**. So across those two groups, `n`,
`threads_total` and `rss_mb_total` are **not comparable at all**. No table in
this record may set one group's counts against the other's.

> **A previous draft of this record put arm B's and arm F's process counts side
> by side in a table, under the heading that — being the same configuration —
> "their process counts ought to agree. They do not". That table is DELETED, not
> corrected.** Its two columns are produced by the two matchers this section has
> just shown count different things, so the table set incommensurable quantities
> against each other and invited exactly the cross-arm reading the paragraph
> above forbids. **A comparison between two instruments that do not measure the
> same thing does not become correct when its digits are right** — and a round
> that merely fixed the digits left the defect in place.
>
> (The digits were wrong as well, and inverted, having been **transcribed from a
> review comment rather than re-derived from the file** — the defect class this
> record has spent several rounds closing. Recorded here because the correction
> history is part of the evidence, but the table itself does not come back.)

For completeness, and as a statement about **arm B alone** rather than a
comparison across instruments: arm B's `n` has exactly **two** transitions in the
whole file. It is `1` at `ctrl_nolayer.txt` @ `"t": 0.0`, `2` from @ `"t": 2.0`,
and `1` again from @ `"t": 111.1` through to the final sample @ `"t": 304.3`.
**The single early `n=1` sample is the one at `"t": 0.0`, which predates this
run's browser** (see the contamination note above) — it is not a tab-1 reading
and must not be used as one.

That is the instrument, not the browser. **The stall observations themselves are
unaffected** — they rest on `new_page` blocking and the tab-1 ping blocking, both
measured through the automation channel, not on any process count.

### Process loss in arm A2 — and why arm B's apparent "death" is not comparable

**Arm A2 lost its entire engine.** At the stall it had 12 processes; by the end
of the 40s recovery watch there were **none left**:

```
t=72.9s   n=12 procs  cpu 54.4%  rss 1337.3 MB  thr R=2 S=277   <- stalled
t=120.9s  n=0  procs  cpu 0.0    rss 0          thr {}          <- all gone
```

A2's matcher walks `/proc` for the engine path, so it counts the **whole tree**.
`12 → 0` is therefore a real and complete engine loss, and it means the ping that
"still blocked after a further 40s" was, by the end of that window, blocking
against a browser that **no longer existed**. That does not change the stall
observation at `t=72.9` — tab 3 blocked >40s and the tab-1 ping blocked >8s while
all 12 processes were alive and idle in `S` — but the *recovery* line for A2 must
be read as "never recovered, and then the engine died", not as "a live browser
still wedged at `t=120.9`". **Arm F, which kept its processes throughout (12 at
both the stall and after the recovery watch), is the arm that carries the clean
no-recovery reading.**

**Arm B's watchdog also shows a drop, and an earlier draft of this record paired
the two as one pattern. That pairing was wrong and is withdrawn.** Arm B was
taken on the `ps comm` matcher, which — as [arm H](#arm-h--the-two-instruments-do-not-count-the-same-processes)
measures — **matches the parent only and never a single child**. So arm B's

```
t=109.0s  n=2 procs  threads=169  rss=769.2 MB
t=111.1s  n=1 proc   threads=95   rss=494.2 MB
```

is a `2 → 1` drop **within a matched subset that never contained the content
processes at all**. An unknown number of engine processes may have been alive and
simply unmatched throughout. It is **not** the same observation as A2's `12 → 0`,
and the two cannot be counted as instances of one thing.

**But the drop must not be read as benign either, and an earlier draft left it
implying that.** The withdrawal above rests on the *denominator* — arm B's `n`
counts a subset that excludes every content process — and that argument is sound.
It does **not** establish which process died. Two readings survive it, and they
point opposite ways:

- the process that vanished at `t=111.1` (`~275 MB / 74 threads`) is a
  **bystander**, and the survivor is this run's browser; or
- the vanished process is **this run's own parent**, and the survivor is the
  pre-launch process disclosed above — in which case arm B's drop is a *closer*
  relative of A2's engine loss, not a more distant one.

The RSS/thread arithmetic favours the second (the `t=304.3` survivor sits within
**0.5 MB and 2 threads** of the pre-launch process at `t=0.0`), but that is a
resemblance between two coarse aggregates, not an identification.

**It cannot be settled from this tree, and the reason is a one-line harness
defect.** `abd_harness.py` @ `"pid": int(f[0])` parses a pid for every matched
process, and the `return {` block of `sample_procs()` immediately below
**discards it** — only the aggregates `n`, `cpu_total`, `rss_mb_total`,
`threads_total` and `stats` are emitted. **No PID appears in any arm-B sample**,
so process identity across the drop is unrecoverable. This is recorded as an
open question, not resolved in either direction.

**`D.txt` shows directly why `n` is not a death signal under this matcher.** It
carries an undisclosed drop of exactly the same shape —

```
t=2.0s  n=2   (startup)
t=4.1s  n=1   (still in new_page1, before tab 1 had finished opening)
```

— during startup, *before the first tab was even open*. Nobody would call that a
death. It is the same `2 → 1` shape the record previously read as one in arm B.

**What survives:** arm A2 lost its engine during the recovery watch, and that is
recorded and unexplained. **What does not survive:** any claim that processes
died in *two of four* arms. The data cannot support it as stated, because the two
numbers have different denominators. Whether A2's death is part of the defect or
an artifact of this container is **not settled here** — it is flagged for the
next session, and it is exactly the case the confirm-review comment warned about
(PS-110: a run whose browser died can still report like a clean one).

**Two checks that would bear on it were not recorded at the time, and are marked
as what they are rather than dressed up.** No harness in this directory looks for
minidumps or samples `/dev/shm`, so for the arms as run these are **unverified**:

- **`/dev/shm`** — a reading taken *now*, on the same container, shows
  `1.0G total, 0 used`. That is the size and the idle state; it is **not**
  evidence about occupancy *during* the arms, which nothing recorded.
  `/dev/shm` exhaustion is a known false-attribution source on this project
  (PS-14), so it is worth an explicit sample in the next run rather than an
  assumption in this one.
- **Minidumps** — a `find / -name '*.dmp'` run now returns nothing, but the
  engine's crash reporter path was never checked at the time and the container
  has been reused since. Treat "no minidump" as **not looked for properly**,
  not as "no crash".

---

## What this record does NOT settle

**The tab-opening gesture is not the owner's.** Arms A2, B, D and F all call
`ctx.new_page()`, a **juggler protocol command**. The owner opens a tab in the
browser UI. So a defect that lives in the automation channel would reproduce in
every arm here and affect no real user.

Arm E was written to close exactly that gap by opening tabs with `window.open()`
from inside a live page — the browser's own code path. **It failed as an
instrument and is reported, not quietly dropped:** it returned "ok" in under
0.15s for three consecutive calls while `ctx.pages` **stayed at 1**. No tab was
ever created (popup blocking, most likely), so it measured nothing. **It does not
exonerate the automation channel**, and it must not be read as if it did.

This also means the ping evidence has an alternative reading that the data cannot
yet exclude: a blocked `new_page` may be **head-of-line blocking the protocol
connection**, so the tab-1 ping would block whether or not the browser itself is
wedged. "The browser stops responding" is therefore precise as *"the browser
stops answering over the automation channel, and does not recover"* — the
stronger claim about the browser's own main thread needs the next reading.

**[Arm C](#arm-c--the-stock-firefox-control) does not close this gap either, and
must not be read as if it did.** It was run over **marionette**, not juggler, so
it changes the channel at the same time as it changes the binary. Its quiet
result narrows the stall to **launcher-or-juggler without picking between them**,
and it leaves the reading above — that the stall may need the juggler channel to
be expressed at all — **fully open**. What arm C *does* settle is narrower and
worth stating plainly: the stall is **not** plain Firefox's, and **not** the
engine patches' alone.

An arm to read `/proc/<tid>/wchan` at the stall (a futex pile-up would name a
lock; a pipe/socket wait would name an unanswered IPC peer) was written and could
not be run — the harness process was repeatedly reaped by the environment before
Python started. It is committed here (`g_wchan.py`) so the next run gets it for
free.

Also untested, deliberately: **Chromium** (out of scope) and **checker verdicts**
(out of scope — Firefox's results are excellent and this is about stalling).

---

## What follows from this

1. **Do not fix persona's per-tab spoof path on the strength of this ticket.**
   The layer-off arms say the stall does not need it.
2. **Do not fix the engine patches on the strength of this ticket either.**
   [Arm C](#arm-c--the-stock-firefox-control) ran persona's patched binary bare,
   over marionette, and it opened five tabs without stalling.
3. **The suspect list is now the launcher and the juggler channel — and the next
   reading has to separate them, because arm C could not.** Run the juggler
   gesture against a **stock** engine, or the marionette gesture *through*
   persona's launcher: either one moves exactly one variable and turns arm C's
   "launcher-or-juggler" into an attribution.
4. **The next reading is cheap and specific:** run `g_wchan.py` to a stall and
   read the wait channels; and drive the tab-opening from the **UI** (or via
   `--new-tab` to a running instance with a session bus present) to settle
   browser-vs-channel. Both are one run each.
5. **A reading on the owner's Windows machine remains worth taking** — this
   container has no GPU and no compositor, and his report is from hardware that
   has both.
6. **Sample `/dev/shm` and the crash-reporter directory in the next run.**
   Neither was recorded during these arms, so arm A2's engine loss has no
   attributable cause in this tree. Both are one line in the watchdog.
7. **Re-run any arm you intend to compare on the SAME matcher.** Arms B/D
   (parent-only) and A2/E/F (whole tree) cannot be compared on `n`,
   `threads_total` or `rss_mb_total`. `h_matchers.py` shows the check that
   catches this in under ten seconds.
8. **Record PIDs, and assert a clean box before launching.** Two one-line fixes
   to `abd_harness.py`, both learned the hard way in arm B:
   - **Keep the `pid` field it already parses.** The `"pid": int(f[0])` line
     reads a pid for every matched process and the `return {` block of
     `sample_procs()` immediately below throws it away, which is
     the sole reason arm B's `2 → 1` drop cannot be attributed to a process.
   - **Take one pre-flight sample and assert it matched nothing**, before
     constructing the engine. Arm B began with a 490 MB firefox process already
     running that the harness had not started; arm D shows `n=0` at `t=0.0`, so
     a clean box is the norm and a foreign process is worth failing loudly on
     rather than silently folding into every aggregate for the rest of the run.

---

## Files

| File | What it is |
|---|---|
| `A2.txt` | **Arm A2 — the layer-ON arm**, headless, corrected instrument. Replaces the lost arm A. Includes the engine dying during the recovery watch. |
| `ctrl_nolayer.txt` | Arm B — layer OFF, headless. **Captioned three times: lifetime-average CPU column, a PARENT-ONLY process count, and a PRE-LAUNCH FOREIGN PROCESS.** Its `2 → 1` drop is **not comparable** to A2's `12 → 0` and is **unattributable** — no PID was recorded. Reduced evidentiary value; **arm D is the cleaner control.** |
| `D.txt` | Arm D — layer OFF, **headful** under Xvfb. **The cleanest control arm**: the only arm that is *both* layer-off *and* verifiably uncontaminated at `t=0.0` (`n=0`). **Captioned: lifetime-average CPU, parent-only count** — carries the startup `2 → 1` drop that shows `n` is not a death signal here. |
| `F_new_page.txt` | Arm F — **the characterisation**: bounded deadline, tab-1 ping, recovery watch. Corrected instrument. |
| `E1.txt` | Arm E — the **null instrument**, kept as the record of a failed measurement. |
| `H_matchers.txt` | **Arm H — the instrument comparison.** Both matchers applied to one live engine at the same instant. Settles that `ps comm` counts the **parent only**. |
| `C_stock.txt` | **Arm C — the stock-Firefox control.** Two runs in one file: **C1** upstream Firefox 151.0 (nothing of persona's) and **C2** persona's patched engine run bare. **Neither stalled.** Captioned with what the arm licenses and — importantly — what its marionette channel means it **cannot** attribute. |
| `abd_harness.py` | Harness for arms **A (lost), B and D**. `ps pcpu` (**uncorrected** CPU) **and** `ps comm` (**parent-only** process matcher). |
| `a2.py` | Harness for arm **A2**. Corrected `/proc`-delta instrument, whole-tree matcher, layer ON. |
| `e2.py` | Harness for arms E/F. Corrected `/proc`-delta instrument, whole-tree matcher. |
| `h_matchers.py` | Harness for arm **H**. Runs *both* matchers side by side and prints what each matched. |
| `c_stock.py` | Harness for arm **C**. Drives a bare Firefox binary over **marionette** (no persona launcher), corrected `/proc`-delta instrument, whole-tree matcher keyed on the binary's own directory so each run counts only its own tree. |
| `g_wchan.py` | Arm G — wait-channel dump at the stall. **Written, never successfully run.** |

Every arm in this record now has both its **reading** and the **harness that
produced it** committed, so the control arm is reproducible by the next person
rather than only its conclusion being readable.

> **The `.txt` extensions are deliberate, not cosmetic — and the first time
> round this discipline FAILED.** `.gitignore:183` is `*.log`, so every raw
> reading here was silently untracked when first written. That is the same
> accident that cost **PS-150 arm C** its evidence (its log was swallowed,
> leaving a committed JSON block that has never been reproduced).
>
> **An earlier draft of this record claimed the trap had been beaten. It had
> not.** `git check-ignore` was run on the artifacts that survived rather than on
> all of them, so `A.log` — the *only* layer-ON arm — went uncommitted while the
> record cited its numbers. The check has to be run on **every** artifact, and it
> has to be run **before** the run that produces it, not after: a lost reading
> costs the whole run. For arm A2 the destination was `check-ignore`'d first and
> the harness wrote straight into this directory as `.txt`. Do that.
