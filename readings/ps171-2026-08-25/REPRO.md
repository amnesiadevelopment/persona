# PS-171 — Firefox tab stall: reproduction record

**Date:** 2026-08-25
**Ticket:** PS-171
**Deliverable:** a reproduction, characterised — *not* a fix.

---

## Verdict in one paragraph

**The stall REPRODUCES here, and it is NOT persona's.** With persona's masking
layer *entirely absent* — the packaged engine and nothing else — opening the
**third** tab blocks indefinitely and **never recovers**. It reproduces headless
and headful, and it reproduces on **`about:blank`**, so neither the per-tab spoof
path nor the weight of checker pages is required to produce it. At the stall the
browser is **idle-waiting, not exhausted**: every thread in `S`, ~1% CPU, 1.2 GB
RSS on a 16 GB host. That shape is a deadlock, not resource starvation.

**One thing this record does NOT settle**, and it is stated up front rather than
buried: every arm that reproduced the stall opened tabs through the **juggler
automation channel** (`ctx.new_page()`), which is *not* the gesture the owner
makes. See [Limitations](#what-this-record-does-not-settle). The stall is real
and layer-independent; whether the *browser* or the *automation channel* owns it
is not yet proven.

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
| `/dev/shm` | 1.0 GB (not exhausted at any point) | unknown |

**The gap is material and cuts both ways.** A headless, GPU-less container can
fail to show a defect a real Windows desktop does. It can *also* produce a stall
of its own that a real desktop would not. This record therefore leans on the
**control arm** — the same environment with and without persona — rather than on
the bare fact that something stalled.

---

## Method

Tabs are opened **one at a time**, each with a bounded deadline (`SIGALRM`), so a
stall can be *observed and then interrogated* instead of merely waited on. After
each tab the harness **pings tab 1** (`evaluate("1+1")`) — that is the
discriminator between "the new tab stalled" and "the browser stopped answering".

A watchdog samples every firefox process independently of the blocked call:
process state, **instantaneous** CPU, RSS, thread count, and per-thread states.

> **CPU is read as `utime+stime` deltas from `/proc`, never `ps pcpu`.**
> `pcpu` is an average over the process's *whole lifetime*, so a process that
> spun hard for ten seconds and then blocked forever keeps reporting a high
> number. Using it would have made **spinning** and **blocked** indistinguishable
> — the single distinction this ticket turns on. This was an instrument bug in
> the first draft of the harness, caught and fixed before any reading was taken.

No live checker page was loaded, so no exit was required. (Had one been loaded,
the proxied exit would have been mandatory with no direct-connection fallback.)

---

## Arms and results

| Arm | Layer | Display | Tab open path | Result |
|---|---|---|---|---|
| A | **ON** | headless | `new_page` | tab1 1.9s, tab2 0.9s, **tab3 hung >290s** |
| B | **OFF** (control) | headless | `new_page` | tab1 1.9s, tab2 **12.0s**, **tab3 hung >280s** |
| D | **OFF** | **headful** (Xvfb) | `new_page` | tab1 1.8s, tab2 0.9s, **tab3 stalled** |
| F | **OFF** | headless | `new_page` | **the characterisation — below** |
| E | OFF | headless | `window.open` | **null instrument — see Limitations** |

Four independent runs (A, B, D, F). **All four stalled at tab 3.** Tab 1 and tab
2 always opened in about a second, except arm B where tab 2 took 12.0s.

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
  40s; arms A and B sat blocked for **>280s**.

**And it is not exhaustion.** At the stall: **every thread in `S`** (interruptible
sleep — idle-waiting, not running), CPU decaying **35% → 1.4%**, RSS **1.2 GB of
16 GB**, `/dev/shm` untouched. A browser that had run out of something would be
thrashing or dying. This one is *waiting for something that never arrives* —
deadlock-shaped.

### How many tabs — hard limit or load-dependent?

**Tab 3, in 4 of 4 runs**, across headless *and* headful, with *and* without the
masking layer. In this environment it behaves as a **hard threshold at the third
tab**, not a load-dependent one — consistent with the owner's "третья вкладка".

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

### A process died in arm B

Arm B's watchdog caught a firefox process **disappearing** mid-stall:

```
t=109.0s  n=2 procs  threads=169  rss=769.2 MB
t=111.1s  n=1 proc   threads=95   rss=494.2 MB
```

No minidumps were found and `/dev/shm` was not exhausted, so the cause is
unrecorded. Flagged rather than explained — and it is exactly the case the
confirm-review comment warned about (PS-110: a run whose browser died can still
report like a clean one). The parent stayed up and kept not-answering.

---

## What this record does NOT settle

**The tab-opening gesture is not the owner's.** Arms A, B, D and F all call
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
   The control arm says the stall does not need it.
2. **The next reading is cheap and specific:** run `g_wchan.py` to a stall and
   read the wait channels; and drive the tab-opening from the **UI** (or via
   `--new-tab` to a running instance with a session bus present) to settle
   browser-vs-channel. Both are one run each.
3. **A reading on the owner's Windows machine remains worth taking** — this
   container has no GPU and no compositor, and his report is from hardware that
   has both.

---

## Files

| File | What it is |
|---|---|
| `ctrl_nolayer.txt` | Arm B — **control arm**, layer OFF, headless. Includes the process death. |
| `D.txt` | Arm D — layer OFF, **headful** under Xvfb. |
| `F_new_page.txt` | Arm F — **the characterisation**: bounded deadline, tab-1 ping, recovery watch. |
| `E1.txt` | Arm E — the **null instrument**, kept as the record of a failed measurement. |
| `e2.py` | Harness for arms E/F. |
| `g_wchan.py` | Arm G — wait-channel dump at the stall. **Written, never successfully run.** |

> **The `.txt` extensions are deliberate, not cosmetic.** `.gitignore:183` is
> `*.log`, so every one of these raw readings was silently untracked when first
> written — the exact accident that cost **PS-150 arm C** its evidence (its log
> was swallowed, leaving a committed JSON block that has never been reproduced).
> Caught here by running `git check-ignore` on the artifacts *before* committing
> rather than trusting `git status`. Do the same for the next reading.
