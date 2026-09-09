# PS-349 — can a degraded session be told from a healthy one, from outside it?

**Date:** 2026-09-09
**Ticket:** PS-349
**Base:** `c3eba9e`
**Deliverable:** a characterisation and a recommendation. **No product code
changes. Nothing ships.**

---

## Verdict in one paragraph

**A degraded session CAN be distinguished from a healthy one from outside — but
not by any single signal, and the composite that separates these nine arms is
NOT a calibration and must not be shipped as one.** `session_registry`'s
existing pid+create-time probe answers **ALIVE on every degraded arm**, which
confirms the ticket's framing exactly: it is the wrong question, not a missing
one. Two `/proc` signals do carry information — **instantaneous CPU** and the
**per-second delta of `voluntary_ctxt_switches`** over the whole engine tree —
and a two-armed rule (`ctxt_v/s <= 20` **OR** `cpu >= 105%`, each sustained 10
samples ≈ 20 s) classifies all nine arms correctly. **But the margin is one
sample wide and it is an artifact of how hard I chose to load the healthy
control.** A healthy, fully responsive session driven harder reached **9
context switches/s — LOWER than the wedged session's floor of 18** — so the
populations *overlap*, and the rule survives only because the healthy
excursions were short. ⛔ **Do not ship a watchdog on this.** The honest
recommendation is the cheap, non-judgemental half: **record** these two series
per session and **report** them, so a stall arrives with evidence attached —
and leave the verdict to a human.

**One finding is not about signals at all and is the most actionable thing
here.** The wedged arm's **teardown never completed**: the session **orphaned
its entire 12-process engine tree**, which went on running for **95 s past the
session's last confirmed state** before something reaped it, and the
tearing-down process was still **spinning at 100% CPU ≈26 minutes later, with no
browser left attached**. That is the `process_group.py` docstring's own failure
mode — the one that degrades a later launch into a contentless
`TargetClosedError` — observed live, and it is reachable without solving the
health-signal question at all.

---

## Venue — established, and this was the first task

The ticket warned this needed a real launch and that the planner's container had
none. It was established here:

| | |
|---|---|
| host | Debian 13 container, kernel **6.8.0-138-generic**, 8 cores / 15 GB |
| `DISPLAY` | absent → **Xvfb** installed (`xvfb-run`, `2:21.1.16-1.3+deb13u3`) |
| engine | **`firefox-20_151.0_20260817150018`** — fetched via the product's own `ensure_invisible_installed()`. **The same build PS-171 measured.** |
| deps | `invisible_playwright @ 353df4fa` + `invisible_core==20.14.0` (the pyproject pins), `psutil`, `cryptography` |
| `/dev/shm` | 1.0 G, 0 used at every pre-flight |
| launch path | `spawn_browser(profile, in_process=True)` — the product's own path, verified end-to-end (eval hook up at 5.3 s, `1+1 -> 2`) |

⚠️ **Two environment facts that bound everything below.** (1) `/proc/<pid>/wchan`
is readable, but **`/proc/<pid>/syscall` and `/proc/<pid>/stack` are NOT**
(`Operation not permitted` / `Permission denied`) — so the sharpest possible
discriminator, *which syscall is each thread blocked in*, is unavailable at this
privilege level and was never measured. (2) This container has **no GPU and no
compositor**, the same gap PS-171 disclosed.

---

## Method — and what "from outside" is made to mean

Two processes, deliberately:

* **`subject.py`** launches a persona session through `spawn_browser` and holds
  it, then degrades it (or does not).
* **`observe.py`** launches the subject as a child and samples **`/proc` and
  nothing else**. It never touches a session object, a playwright handle or the
  eval hook.

That split is the whole design. `BrowserLauncher` holds a pid and a stdout pipe;
if a signal is visible to the observer, the launcher could read it too. If it is
not, no amount of product code makes it so.

**The instrument is PS-171's corrected one, re-used deliberately.** CPU is an
**instantaneous** `utime+stime` delta from `/proc/<pid>/stat`, never `ps pcpu`
(a lifetime average, which makes *spinning* and *blocked* indistinguishable —
the one distinction this ticket turns on). Processes are matched on the **engine
path in `/proc/<pid>/cmdline`**, never `ps comm` (PS-171 arm H measured that
matcher finding 1 process where this one finds 11). Sampling cadence 2 s.

**Pre-flight asserts a clean box** and *aborts* rather than folding a foreign
process into every aggregate — PS-171 recommendation 8, adopted.

> ⛔ **AND IT DOES NOT COVER THE CASE THAT ACTUALLY BIT THIS RUN. Stated here
> because it is a real limitation of the instrument, found while reworking this
> record, and the next reader should not have to discover it.** The check reads
> the process set **once, before launch** (`observe.py`, the `pre = engine_procs()`
> block guarding the `ABORT` emit). It cannot see a **later arm's engine tree
> appearing under a still-running observer** — and here it did not, because that
> is exactly what happened. The `jugwedge` observer never exited: its subject
> never returned, so the `while child.poll() is None` loop went on sampling
> `/proc` for **another ~24 minutes**, straight through the next five arms.
> Those arms' engine trees are **matched by the same engine-path matcher** and
> folded into `jugwedge.txt`'s tail as if they were its own. Every one of the
> nine `.txt` files records `clean_box: true`, and none contains an `ABORT` —
> the guard fired at no point in this session.
>
> This is **PS-171 arm B's foreign-process contamination arriving through a door
> the adopted recommendation does not close**, in the record that adopts it. The
> honest scope of the guard is *"no foreign process at launch"*, not *"no
> foreign process in this file"*. A per-sample version would compare each
> sample's pid set against the one established at `READY` and mark, not drop,
> the divergence — **not implemented here, and named as the gap it is.**
>
> ⚠️ **It costs this record's findings nothing, and that is a claim with a
> reason rather than a reassurance.** Every signal window closes at or before
> `t=121.5` (`SETTLE`/`TAIL_DROP`/`END_BOUND`, applied in `sweep.py`'s
> `windows()`), and the first contaminating sample is at **`t=234.9`**. The
> nine-arm table, the sweep and the composite are computed entirely inside the
> clean span. What it *does* invalidate is the **prose about the tail**, which
> is corrected at [finding (b)](#b-the-teardown-did-not-hang--it-spun-and-it-was-still-spinning-25-minutes-later).

**Window rules, stated rather than tuned.** 8 s is dropped after `READY` and
after `DEGRADED` (startup burns CPU in every arm; a threshold fitted across a
launch fires on every launch), and 10 s is dropped from each tail (teardown is a
burst in every arm).

> ⚠️ **ONE WINDOW BOUND IS NOT COSMETIC AND IS DISCLOSED RATHER THAN QUIETLY
> APPLIED.** The `jugwedge` arm's teardown **never completed**: after the
> recovery ping at `t=121.5` the subject printed nothing more and the observer
> kept sampling to **`t=1605.2`**. The wedged window therefore ends at
> `t=121.5` — the last moment the subject *confirmed* the session's state.
>
> ⭐ **The reason the excluded span must go is STRONGER than this record first
> said, and the first version had it wrong.** It was described as *"wedged AND
> being torn down"*, its high-CPU samples as *"teardown work"*. They are not.
> After the orphaned tree was reaped at `t=218.9` that span holds **five live
> 10-process engine trees** — the `unreachable`, `busy`, `busyheavy`,
> `busyheavy2` and `busymax` arms, running under a **different** observer and
> folded into this file by the stale one (see the instrument note above). Their
> episode CPU medians of **5.4 / 72.9 / 103.9 / 104.0 / 104.0** reproduce those
> arms' own medians of **5.9 / 72.8 / 103.9 / 104.0 / 103.9**. So the excluded
> samples are not this arm's teardown at all — **they are other sessions**, and
> excluding them is not a judgement call about teardown but a correctness
> requirement. **The bound is unchanged at `t=121.5`; only its justification
> was wrong.** Left in, they inflated the wedged arm's CPU to a median of 101.8%
> and made a CPU-based rule look **better than it is**. `sweep.py:END_BOUND`
> carries this and says why. (What the teardown was actually doing is
> [finding (b)](#b-the-teardown-did-not-hang--it-spun-and-it-was-still-spinning-25-minutes-later),
> and it is not what it first looked like.)

---

## The arms

Nine arms, **and the six negative ones are the point**. A ticket that only
degrades sessions measures sensitivity and never false positives — and the trap
this ticket names is precisely a check that fires when it should not, or
reassures when it cannot see.

| arm | ground truth | what it is |
|---|---|---|
| `healthy` | **healthy** | launch, hold a page, ping every 10 s. Idle. |
| `busy` | **healthy** | one chunked, yielding compute loop on the page |
| `busyheavy` | **healthy** | six concurrent loops |
| `busyheavy2` | **healthy** | repeat of `busyheavy` — reproducibility |
| `busymax` | **healthy** | sixteen loops |
| `unreachable` | **healthy** | the **eval hook is unregistered** — the browser is fine, only the *observer's channel* is gone |
| `sigstop` | DEGRADED | `SIGSTOP` to the whole engine tree. Ground truth certain: alive, cannot answer. |
| `spin` | DEGRADED | `while(true){}` on the page — alive, main thread pinned |
| `jugwedge` | DEGRADED | PS-171's `ctx.new_page()` gesture — tab 3 blocks |

**The five arms that were PINGED answered every ping.** `healthy`, `busy`,
`busyheavy`, `busyheavy2` and `busymax` each answered **12/12** pings in
**0.01–0.03 s** — the four `busy*` ones while burning CPU. For those five,
health is not asserted, it is measured, on the same channel and in the same
runs.

⛔ **The sixth negative arm, `unreachable`, was NOT pinged and its health is
STRUCTURAL, not measured.** Its committed result is `"pings": []` — zero, by
construction, because the arm's whole point is that `subject.py` unregisters the
eval hook, so there is no channel left to ping through. Its health rests on
*what was done to it* (the browser was left untouched; only the observer's
channel was torn out) and on its first eval succeeding at 0.14 s before that
happened. **That is a weaker warrant than the other five carry, and this record
will not launder it into the same sentence.** This ticket's own trap is *"a
health check that reports healthy because it cannot see is worse than none"*,
and the one arm nothing could see is exactly where that sentence has to be said
out loud rather than smoothed over.

⚠️ **`unreachable` is a NEGATIVE arm and the reason it exists is worth stating.**
It is the false-positive trap for any probe that reads *through* the automation
channel: the browser is perfectly healthy and such a probe calls it dead. Its
`/proc` reading is indistinguishable from `healthy` — which is the correct
answer, and the one a channel-based check gets wrong.

### `jugwedge` — PS-171 reproduces, and it is used only as a generator

```
tab2 open ok=True 0.74s
  ping-tab1 ok=True 0.03s
tab3 open ok=False 40.0s BLOCKED>40s
  ping-tab1 ok=False 8.0s BLOCKED>8s
RECOVERY after 60s: ping ok=False 8.0s BLOCKED>8s
```

**Tab 3, blocked, tab-1 ping also blocked, no recovery** — PS-171's arm F
reading, reproduced on this container on the same engine build. It is used here
as a **degraded-session generator**, nothing more.

> ⛔ **THIS ARM IS NOT EVIDENCE ON THE LAUNCHER-VS-JUGGLER QUESTION, AND MUST NOT
> BE READ AS ANY.** Its generator is **PS-171's arm-F harness**
> (`InvisiblePlaywright` + `context_for`), **not persona's launcher**. The
> launcher's published eval hook exposes `eval` and `goto` only — it does not
> expose `ctx`, so the `ctx.new_page()` gesture that reproduces is not reachable
> through it, and PS-171 arm E already measured `window.open` to be a **null
> instrument**. Reaching the ctx would have meant editing the product to make a
> session easier to observe, which this ticket forbids. **It costs this ticket's
> question nothing** — the observer reads `/proc`, so it sees the same engine
> tree, the same binary and the same channel whoever called `new_page`.
> **See [what this record declines](#what-this-record-declines-and-why).**

---

## The readings

All windows bounded as described. `ctxt` is **`voluntary_ctxt_switches` per
second, summed over the engine tree**; `cpu` is instantaneous, whole tree;
`nonS` counts threads *not* in `S`.

| arm | truth | n | cpu med | cpu max | **ctxt min** | ctxt med | **alive** | nonS max |
|---|---|---|---|---|---|---|---|---|
| `healthy` | healthy | 50 | 6.0 | 134.3 | 127 | 192 | **True** | 3 |
| `unreachable` | healthy | 41 | 5.4 | 46.5 | 140 | 197 | **True** | 2 |
| `busy` | healthy | 51 | 72.6 | 82.1 | 208 | 266 | **True** | 2 |
| `busyheavy` | healthy | 52 | 103.7 | 114.0 | **20** | 37 | **True** | 2 |
| `busyheavy2` | healthy | 51 | 103.9 | 114.1 | **17** | 42 | **True** | 4 |
| `busymax` | healthy | 50 | 103.9 | 111.2 | **9** | 31 | **True** | 3 |
| `sigstop` | DEGRADED | 51 | 0.0 | 0.0 | 0 | 0 | **True** | **253** |
| `spin` | DEGRADED | 56 | 110.2 | 124.0 | 202 | 250 | **True** | 3 |
| `jugwedge` | DEGRADED | 30 | 0.5 | 7.9 | **18** | 19 | **True** | 0 |

### 1. The existing probe answers YES on every degraded arm

`alive` is **True in all nine**, degraded included. This is `session_registry`'s
pid+create-time question, and the table is the direct confirmation that it
cannot see this class of failure. The ticket said so; this measures it.

### 2. Thread states are NOT the deadlock signal they look like

PS-171 characterised its stall as *"every thread in `S`… deadlock-shaped"*. That
is true — **and a healthy idle session looks identical.** `healthy` sits at
`{S: 213}` sample after sample (its live samples range `S` 150–225, mode 213);
`jugwedge` sits at `{S: 305}`. **`nonS max` is 3 on the healthy control and 0 on
the wedged arm** — if anything the *wedged* session looks calmer.

> ⚠️ An earlier revision of this paragraph quoted `{S: 250}` for the healthy
> control. **That value does not occur anywhere in `healthy.txt`.** It occurs in
> `jugwedge.txt`'s contaminated tail, which is where it was most likely read
> from — the same stale-observer defect the instrument note above records,
> reaching one number in the prose. The finding is untouched: both arms still
> sit at ~100% `S`, and `nonS max` is still 3 healthy / 0 wedged.

⚠️ **This is a caution about reading PS-171's shape as a detector, not a
correction to PS-171.** That record used the shape *comparatively*, to argue the
stall is a deadlock rather than exhaustion, and that reading stands. What this
adds is that the same shape has **no discriminating power** against an idle
healthy session, so it cannot be turned into a check.

`sigstop` is the one exception (`T: 253`), and it is exceptional for an
uninteresting reason: `T` is what `SIGSTOP` *means*. **No naturally-occurring
degradation produces it**, so it validates the instrument and generalises to
nothing.

### 3. CPU alone: fails in both directions

`spin` sustains ~110% and `jugwedge` sits near **0.5%** — two degraded arms at
opposite extremes, so no single-sided CPU test can catch both. And `busy` /
`busyheavy` / `busymax` are **healthy** at 72%, 104% and 104%. CPU says how much
work is happening, never whether the session can answer.

### 4. Context switches: real information, overlapping populations

This is the one signal that separates *idle-healthy* from *wedged*, which CPU
cannot: both are ~0% CPU, but an idle-but-live browser still has timers, IPC and
compositor traffic (127–208 switches/s) while the wedged tree drops to **18–19**.

**And it does not hold under load.** The healthy `busy*` arms fall to **20, 17
and 9** — `busymax`, a healthy session answering every ping in 0.02 s, went
**below the wedged arm's floor of 18**. The distributions **overlap**.

### 5. The composite — what it does and what it does not

`ctxt_v/s <= 20` **OR** `cpu >= 105%`, each sustained 10 samples (≈20 s):

```
healthy      healthy   low=. cpu=. -> healthy   ok
unreachable  healthy   low=. cpu=. -> healthy   ok
busy         healthy   low=. cpu=. -> healthy   ok
busyheavy    healthy   low=. cpu=. -> healthy   ok
busyheavy2   healthy   low=. cpu=. -> healthy   ok
busymax      healthy   low=. cpu=. -> healthy   ok
sigstop      DEGRADED  low=Y cpu=. -> DEGRADED  ok
spin         DEGRADED  low=. cpu=Y -> DEGRADED  ok
jugwedge     DEGRADED  low=Y cpu=. -> DEGRADED  ok
=> fp=0 fn=0
```

**Nine for nine — and it is not a calibration.** Both arms are one sample wide:

* `busyheavy` held `cpu >= 105%` for **3** consecutive samples, `busymax` for
  **2**, `spin` for **56**. The rule needs 10. **Margin: 7 samples, on a load I
  chose.** At `cpu >= 110%` the rule **misses `spin` outright**.
* `busymax` held `ctxt <= 20` for **3** consecutive samples against the rule's
  10 — and its *floor* is below the wedged arm's.

**The separation is a duration artifact, not a magnitude one.** The healthy arms
*visit* both degraded bands and do not *stay* there. Nothing measured here says
where the healthy ceiling is: three loops, six, sixteen — the numbers moved each
time, and a real user's page is not bounded by my `for` loop. **Nine arms on one
container, one engine build, one OS, no GPU, no compositor, is a hypothesis.**

---

## ⭐ The teardown findings — the most actionable part, and not about signals

Two distinct things went wrong when the wedged arm ended, and they are stated
separately because they have different strengths.

### (a) Twelve orphaned engine processes — measured, one occurrence

**The wedged session's engine tree outlived the session's last confirmed state
by 95 seconds.** `jugwedge.txt` carries this directly: the recovery ping fails
at `t=121.5` and the subject never speaks again, yet the observer goes on
reading a **live 12-process tree at 1210 MB** — unchanged, at ~0.5% CPU —
through `t=216.9`. At `t=218.9` it is `nproc: 0`. **Something reaped it; this
record does not know what**, and the observer that would have said so is the
same one whose staleness is disclosed above.

```
t=  200.6 nproc=12 rss=1210.1 cpu=0.0
   …                                     (47 consecutive samples, t=123.5–216.9)
t=  216.9 nproc=12 rss=1209.3 cpu=0.5
t=  218.9 nproc= 0 rss=   0.0 cpu=None   <- reaped
```

This is what `process_group.py`'s docstring describes — the accumulation that
degrades a later launch into a contentless `TargetClosedError`, *"the error
PS-133 records being misattributed to fingerprint seed 4242"* — **observed
live**, and it is the mechanism behind this ticket's own evidence-corruption
argument.

> ⚠️ **THE CITATION FOR THIS FINDING WAS WRONG AND IS CORRECTED HERE.** An
> earlier revision quoted a pre-flight abort —
> `PREFLIGHT FAIL: engine already running [27917, … 28262]`, twelve pids — as
> the evidence, and said *"the next arm refused to start"*. **No committed
> reading contains that abort, and the arm that actually ran next started
> clean.** Every one of the nine `.txt` files records `clean_box: true` with
> `engine_pids_before_launch: []`, and none contains the `{"meta": "ABORT"}`
> that `observe.py` emits on a dirty box. `unreachable` (subject pid 28655) ran
> next and its ten engine pids intersect the twelve claimed orphans **not at
> all**. Only the **first six** of the twelve (`27917, 27971, 28024, 28028,
> 28036, 28074`) are real and committed — they are `jugwedge`'s own
> `ENGINE_PIDS` at `READY`. That console line was written to
> `/tmp/ps349/<arm>.console`, which was **never committed and no longer
> exists**; it is therefore not evidence anyone can re-read, and this record's
> own closing claim that *"every arm here has both its reading and the harness
> that produced it committed"* does not extend to it. The finding above is
> re-grounded on `jugwedge.txt`, which **is** committed — and the tree it shows
> is the better citation anyway.
>
> ⛔ **What this costs the finding, stated rather than glossed:** the orphaned
> tree is now *"it outlived its session by 95 s and was then reaped"*, which is
> weaker than *"it blocked the next launch"*. The claim that the pre-flight
> guard caught it **is withdrawn** — the guard fired at no point in this
> session.

⚠️ **Stated at the strength it carries.** One occurrence, in a container, on a
deliberately wedged session, through the arm-F harness rather than persona's
launcher — **so it is NOT a measurement of the product's teardown path** and
must not be quoted as one. It is a candidate for a real ticket independent of
everything above.

### (b) The teardown did not hang — it SPUN, and it was still spinning ≈26 minutes later

The first reading of this record said the wedged arm's teardown "blocked". That
was **wrong in a way worth correcting rather than smoothing over**, because the
two states have different causes and different fixes. A live probe taken while
writing this record (`post_state.py`, output below) found:

```
surviving ENGINE processes: 0
pid 27900: state=R age=1544s threads=1 cpu=100.0%   <- subject.py
pid 27899: state=S age=1546s threads=2 cpu=0.0%     <- observe.py
```

The engine tree was **gone**; the single remaining Python thread was **burning a
full core**, in state `R`, **1544 s (≈25.7 min, quoted as ≈26 min) after
launch** and ~1400 s after its last ground-truth output. It was not waiting on
anything.

⛔ **The sentence that used to follow this was wrong, and it is the one clause
the first correction of this paragraph missed.** It read: *"That is why the
observer went on emitting samples to `t=1497` — it was faithfully sampling a
tree that no longer existed (`nproc: 0`), which is also why the tail of
`jugwedge.txt` is all-null rather than merely quiet."* **The tail is not
all-null.** After `t=121.5` it holds **737 samples, of which 355 carry
`nproc > 0`** — 300 of them a full live 10-process tree at a ~1037 MB median
RSS. The engine-tree presence flips **11 times** across six episodes: the wedged
arm's own orphaned tree to `t=216.9`, then **five more that are the next five
arms** (see the `END_BOUND` note). The observer was not sampling nothing; it was
sampling **other people's sessions**.

**The true explanation is simpler and is the same defect.** The observer kept
emitting because its `while child.poll() is None` loop never ended — the
subject process it launched **never exited**, which is precisely what
`post_state.py` catches it doing above. `jugwedge.txt` is the one file of the
nine with **no `{"meta": "child_exit"}` record**; the other eight all have one.
That absence is the machine-checkable form of this finding, and it is committed.

⚠️ **The spinning-teardown reading stands on `post_state.py` alone and does not
need the tail at all.** What the correction removes is a false explanation of
the tail, not the observation of a spinning process.

⚠️ **Attribution is NOT established and must not be assumed.** The spinning
process is the **arm-F harness** (`InvisiblePlaywright.__exit__` on a wedged
session), **not persona's launcher**, so this says nothing about the product's
own teardown until someone runs the same wedge through `spawn_browser`. What it
does establish is the shape: a torn-down wedged session can leave a **live,
CPU-burning process with no browser attached** — which is the same
already-degraded-machine condition (a) describes, arriving by a different route.

⭐ **And note what observed it: nothing did — and the corrected record makes
that point sharper than the original did.** The first revision credited the
*next arm's pre-flight refusal* with catching the orphaned tree. **There was no
such refusal.** Both failures went entirely unobserved at the time: the orphaned
tree was reaped by something unidentified with nothing recording it, the
spinning teardown ran for ~24 more minutes unnoticed, and both were found only
because a person went looking afterwards — one of them only during a *rework* of
this record. That is the ticket's thesis reproduced at one level up, and then
once more inside the instrument built to test it: the observation gap is not
only in-session, it is also in-teardown, and it reaches the harness too.

---

## Recommendation

**1. Ship no watchdog, and ship no health verdict. ⛔ Above all, ship nothing
that acts.** The composite is a hypothesis with a one-sample margin whose
healthy side I set by choosing a load. A killer acting on it would terminate a
user's live session — with account sessions open — on a signal that a busy page
reproduces.

**2. What is worth shipping is RECORDING, not JUDGING.** The two series
(`cpu`, `ctxt_v/s`, whole tree, instantaneous, engine-path matcher) are cheap:
two `/proc` reads per sample at a 2 s cadence, no session handle, no automation
channel, nothing that could wedge on the thing it observes. The launcher already
runs a per-session thread. Recording them into the session's own log would mean
the *next* PS-171 arrives with its own before-and-after attached instead of
being reconstructed by a person — which is the actual gap this ticket names.
**Recording is not detection**, and it should be described as what it is.

**3. Do not build a check on thread states.** They read the same on healthy-idle
and wedged. That is finding 2 and it is the one that would most likely have been
assumed the other way.

**4. Any future verdict must be calibrated against REAL pages, not synthetic
load.** The question that decides it is not *"can a wedge be seen"* — it can —
but *"how low does a genuinely busy, genuinely healthy session go, and for how
long"*. `busymax` says: lower than a wedged one. Until that is measured on real
browsing on real hardware, any threshold is a guess with a number on it.

**5. The two teardown findings are a separate, cheaper ticket** and do not
depend on any of the above — neither the orphaned tree nor the spinning
teardown needs the health-signal question answered first. Both want the same
next step: run the same wedge through `spawn_browser` rather than the arm-F
harness, so the product's own teardown path is the thing measured.

---

## What this record declines, and why

**The launcher-vs-juggler separation PS-171 asks for (its recommendation 3) is
NOT taken here.** The ticket offers it as an optional second slice; this record
declines it, and says so plainly rather than leaving it ambiguous:

* it is a **different question** — *who owns the stall* — from the one this
  ticket asks, which is *whether any observer outside a session can tell it is
  degraded*, and mixing them would have produced a weaker answer to both;
* the sharp instance (marionette **through** persona's launcher) needs a driver
  the product does not expose, and **the ticket forbids editing the masking or
  launch path to make a session easier to observe**;
* **nothing here bears on it either way.** `jugwedge` moved the launcher *and*
  the channel together, exactly as PS-171's arm C did. It narrows nothing, and
  it is not offered as evidence.

PS-171's recommendation 3 therefore **stands entirely open**, unchanged by this
reading.

**Also not established here:**

* **`wchan` did not discriminate.** It is read and committed
  (`futex_wait_queue` dominates every arm — at each window's median sample,
  181/222 threads healthy and 258/311 wedged, i.e. **82% and 83%**), but the
  histogram is a proportion of a *thread pool*, not of *what is stuck*, and the
  two proportions are indistinguishable. The reading that would name a lock or
  an unanswered IPC peer needs `/proc/<tid>/syscall` or `/stack`, **and both are
  permission-denied here**. PS-171's arm G is still unrun in the sense that
  matters.
* **Frequency remains unmeasured**, exactly as the ticket says. One historical
  incident; nothing here changes that, and the priority should not move.
* **No Invariant #0 claim.** Nothing measured here concerns a host fact escaping
  a session.
* **Chromium was not measured.** Firefox only.
* **`/dev/shm` was sampled at every pre-flight** (1.0 G, 0 used) but **not
  during the arms** — the same gap PS-171 disclosed, now disclosed again rather
  than fixed.

---

## An instrument caveat, disclosed rather than buried

The subject's bounded-call helper reported `ok=True` at **exactly the deadline**
(`20.0 s`) for the `sigstop` and `spin` post-degrade evals — an eval that was in
fact blocked. The `SIGALRM` fired while the call was inside a C extension and
the exception was delivered late, so the wrapper recorded a *success at the
deadline*. **No conclusion here rests on those two values**: `sigstop`'s ground
truth is the signal itself, and `spin`'s is the pinned CPU. But a reading of
`ok=True` at precisely the deadline is a **blocked call**, and anyone reusing
`guarded()` should treat "success at exactly N s" as a stall. The ping loops in
the healthy arms are unaffected — they returned in 0.01–0.03 s.

---

## Files

| File | What it is |
|---|---|
| `observe.py` | The observer. `/proc` only — never a session handle. Whole-tree matcher, instantaneous CPU, pre-flight clean-box assertion. |
| `subject.py` | The subject. Launches through `spawn_browser` and holds/degrades the session. All nine arms. |
| `run_arm.sh` | Arm runner. **`setsid`-detached deliberately** — this environment reaps a foreground shell call that runs for minutes, which is why PS-171's arm G was never taken. |
| `analyse.py` | Per-arm distributions, split at each arm's own `DEGRADED` mark. |
| `sweep.py` | The threshold sweep and the composite. Carries `END_BOUND` and the ground-truth table. |
| `healthy.txt` | Control — idle, 12/12 pings at 0.01 s. |
| `busy.txt`, `busyheavy.txt`, `busyheavy2.txt`, `busymax.txt` | **Healthy under load.** The false-positive arms; `busymax` is the one that breaks the ctxt floor. |
| `unreachable.txt` | **Healthy**, eval hook torn out — the channel-probe false-positive trap. |
| `sigstop.txt` | Degraded, ground truth certain. Instrument validation. |
| `spin.txt` | Degraded with HIGH cpu. |
| `jugwedge.txt` | Degraded with LOW cpu — PS-171's tab-3 stall reproduced. ⚠️ **Only `t <= 121.5` is this arm.** Its observer never exited (the one file of nine with no `child_exit`), so the tail folds in the NEXT FIVE ARMS' engine trees; see `END_BOUND` and the instrument note. |
| `post_state.py` | The live post-run probe behind finding (b). Excludes SELF and probe processes — the self-match trap bit three times this session. |

Every `.txt` was `git check-ignore`'d **before** the run that produced it —
`.gitignore:183` is `*.log`, the trap that cost PS-171 its only layer-ON arm and
PS-150 its arm C. Every arm here has both its reading and the harness that
produced it committed.

> ⚠️ **One exception, and it is stated because an earlier revision leaned on
> it.** `run_arm.sh` also writes a per-arm console file to `/tmp/ps349/`, which
> was **never committed and no longer exists on disk**. Finding (a) originally
> cited a line from one of those; that citation is withdrawn above and
> re-grounded on `jugwedge.txt`. **Nothing in this record now rests on an
> uncommitted artifact** — but the sentence above should be read as *"every
> reading quoted here is committed"*, not as *"the run produced no other
> output"*, because it did.
