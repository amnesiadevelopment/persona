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
here.** The wedged arm's **teardown blocked**, and the session **orphaned its
entire 12-process engine tree**. The next arm's pre-flight check refused to
launch because of it. That is the `process_group.py` docstring's own failure
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
process into every aggregate — PS-171 recommendation 8, adopted. It fired for
real (see the leak finding).

**Window rules, stated rather than tuned.** 8 s is dropped after `READY` and
after `DEGRADED` (startup burns CPU in every arm; a threshold fitted across a
launch fires on every launch), and 10 s is dropped from each tail (teardown is a
burst in every arm).

> ⚠️ **ONE WINDOW BOUND IS NOT COSMETIC AND IS DISCLOSED RATHER THAN QUIETLY
> APPLIED.** The `jugwedge` arm's **teardown blocked**: after the recovery ping
> at `t=121.5` the subject printed nothing more and the observer kept sampling
> to **`t=1230`**. Those ~1100 s are *"wedged AND being torn down"*, which is not
> the state under test, and they contain **249 samples above 60% CPU** that are
> teardown work. Left in, they inflated the wedged arm's CPU to a median of
> 101.8% and made a CPU-based rule look **better than it is**. The wedged window
> therefore ends at `t=121.5` — the last moment the subject *confirmed* the
> session's state. `sweep.py:END_BOUND` carries this and says why.

---

## The arms

Nine arms, **and the four negative ones are the point**. A ticket that only
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

**Every "healthy" arm answered every ping.** `busy`, `busyheavy`, `busyheavy2`
and `busymax` each answered **12/12** pings in **0.01–0.03 s** while burning
CPU. Their health is not asserted, it is measured, on the same channel and in
the same runs.

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
`{S: 250}` sample after sample; `jugwedge` sits at `{S: 305}`. **`nonS max` is
3 on the healthy control and 0 on the wedged arm** — if anything the *wedged*
session looks calmer.

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

## ⭐ The leak — the most actionable finding, and not about signals

When the `jugwedge` arm ended, the next arm **refused to start**:

```
PREFLIGHT FAIL: engine already running
[27917, 27971, 28024, 28028, 28036, 28074, 28118, 28155, 28157, 28175, 28183, 28262]
```

**Twelve orphaned engine processes.** The wedged session's teardown blocked
(~1100 s of samples after the last ground-truth observation) and the tree
survived it. This is exactly what `process_group.py`'s docstring describes — the
accumulation that degrades a later launch into a contentless
`TargetClosedError`, *"the error PS-133 records being misattributed to
fingerprint seed 4242"* — **observed live**, and it is the mechanism behind this
ticket's own evidence-corruption argument.

⚠️ **Stated at the strength it carries.** One occurrence, in a container, on a
deliberately wedged session, through the arm-F harness rather than persona's
launcher — **so it is NOT a measurement of the product's teardown path** and
must not be quoted as one. Its value is that the **pre-flight check caught it in
one line**, and it is a candidate for a real ticket independent of everything
above.

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

**5. The leak is a separate, cheaper ticket** and does not depend on any of the
above.

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
| `jugwedge.txt` | Degraded with LOW cpu — PS-171's tab-3 stall reproduced. **Contains the blocked teardown; see `END_BOUND`.** |

Every `.txt` was `git check-ignore`'d **before** the run that produced it —
`.gitignore:183` is `*.log`, the trap that cost PS-171 its only layer-ON arm and
PS-150 its arm C. Every arm here has both its reading and the harness that
produced it committed.
