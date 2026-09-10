# PS-388 — the survivor gate's DEGRADED arm, and what running it measured

**Ticket:** PS-388 · **Base:** `9dad467` · **Date:** 2026-09-10
**Charter:** PS-349's Recommendation 5, verbatim — *"run the same wedge through
`spawn_browser` rather than the arm-F harness, so the product's own teardown
path is the thing measured."*

---

## ⛔ READ THIS BOUND BEFORE READING ANY NUMBER BELOW

**NO CHROMIUM SESSION WAS LAUNCHED FOR ANY MEASUREMENT IN THIS DIRECTORY.**

This container cannot launch the Personium chromium engine at all. The engine
was downloaded successfully (`personium-152.0.7977.75`, 202,193,400 bytes) and
the launch reaches the engine, which exits:

```
FATAL:content/browser/zygote_host/zygote_host_impl_linux.cc:129] No usable
sandbox! … a Linux distro that has disabled unprivileged user namespaces with
AppArmor …
```

`kernel.apparmor_restrict_unprivileged_userns` is `1` here and this user has no
sudo, so `sudo sysctl -w …=0` — the fix the launch lane's workflow performs —
is not available. `unshare --user --map-root-user true` fails with EPERM.

Both survivor arms therefore report the **identical** honest refusal:

```
[CANNOT RUN] no-process-survives-a-closed-session      <- the SHIPPED sibling
[CANNOT RUN] no-process-survives-a-degraded-session    <- this slice's arm
  … the launched group N never held a SETTLED tree of at least 3 live
  processes (peak 1, last 0) within 90s …
```

That the sibling — untouched, shipped, and green on CI — reports the same thing
is the control: **this is the venue, not the new arm.** The chromium verdict
must come from the launch lane, which provisions the sysctl the sandbox needs.

**Everything below was measured on a REAL multi-process POSIX tree** — a shell
in its own session forking four `sleep` children — driven through the **real,
unsubstituted** product teardown. A `sleep` under a shell is not
`fpchrome.AppImage` above a zygote above renderers, and the shapes differ in
exactly the way PS-192 was about. What that venue can and cannot establish is
stated per finding.

---

## What was built

A second registry entry, `no-process-survives-a-degraded-session`, whose
sequence is its sibling's with ONE step inserted:

```
launch  ->  settle  ->  SIGSTOP the recorded group  ->  terminate()
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^
```

Everything else is section 8's, unchanged and un-tuned: the same
`_launch_and_grow` (so `_MIN_LIVE_TREE`/`_STABLE_SAMPLES` are **inherited**),
the same `_TEARDOWN_GRACE`, the same `_survivors_or_refuse`, the same
`_sweep_group`. **The healthy arm is byte-identical** and is this change's
control.

**A separate `Check`, not a second gesture inside the existing one**, because
`run_check` falsifies **per Check**: a folded-in gesture would ride on the
healthy arm's falsification — the one thing this slice adds would be the one
thing never shown capable of failing — and one verdict would cover two
surfaces, so a red could not say which teardown broke.

---

## FINDING 1 — the arm DISCRIMINATES (AC2, both arms, both results)

`readings/ps388-2026-09-10/probe.py` → `run.txt`. Both arms run the whole
registry entry through `run_check` (falsify first, then run), on the real tree.

| arm | `terminate` | verdict | evidence |
|---|---|---|---|
| **QUIET** | the product's own, intact | **`pass`** | peak 6, **6 members confirmed STOPPED**, survivors after teardown: **0** |
| **RED** | sabotaged to the pre-PS-192 shape | **`finding`** | peak 6, 6 confirmed STOPPED, **5 alive 5s after teardown returned** |

The red arm alone would only prove the check can *fire*. The quiet arm is what
proves it *discriminates* — this project has recorded the other shape twice
(PS-299's rebase probe reading "81/81 hunks, 0 rejects" against an **empty
directory**; PS-341's `--dump-dom` reading "8 of 8 moved"), and in both the
INSTRUMENT produced the result while the subject was never touched.

**The sabotage is the SHIPPED shape** — `os.kill` on the **held pid** — not the
proposal's `recorded_group → None`. Section 8's falsification says why in
capitals: on persona's Linux fork path the handle's own `kill()` **is** the
PS-192 fix, so a control built on it measures the fix and certifies nothing.

**LEAK AUDIT after every arm: 0 processes alive, 0 of them STOPPED.** The red
arm deliberately orphans a wedged tree; the audit is the proof the undo works,
rather than a code review of it.

---

## ⭐ FINDING 2 — A DEGRADED TEARDOWN COSTS THE FULL TIMEOUT (AC7)

**This is the slice's actual product finding, and it is in the TIMING exactly
where the ticket predicted a finding would be if there was one.**

The SAME tree, the SAME `terminate()`, differing only in the wedge:

```
healthy  (answering)   ->  terminate() returned in  0.00s
degraded (SIGSTOPped)  ->  terminate() returned in 10.00s
```

Ten seconds is **exactly** the `timeout` handed to `terminate`. The mechanism
is **isolated rather than inferred** — `wait_isolation.py` times each leg of
`terminate_process_group`'s escalation separately:

```
=== HEALTHY ===                      === WEDGED (SIGSTOP) ===
  killpg(SIGTERM):        0.00s        killpg(SIGTERM):        0.00s
  proc.wait(timeout=10):  0.00s        proc.wait(timeout=10): 10.00s   <-- HERE
  killpg(SIGKILL):        0.00s        killpg(SIGKILL):        0.00s
  proc.wait(timeout=5):   0.00s        proc.wait(timeout=5):   0.00s
  survivors: []                        survivors: []
```

**SIGTERM to a STOPPED process is QUEUED, not delivered** — the kernel holds it
until the process is continued — so the `wait()` between the SIGTERM and the
SIGKILL blocks for the whole timeout, every time.

### What this is, and three things it is NOT

- ✅ It is a **latency** fact about the teardown path: every teardown of a
  wedged session pays the full timeout on a thread the caller is holding.
  `launcher.stop_profile` passes `timeout=1` on most paths and `terminate`'s own
  default is 5, so the figure an operator sees depends on the call site — but
  the shape is the same everywhere: **a wedged session's teardown is bounded by
  the timeout rather than by the browser.**
- ❌ **NOT a leak.** The SIGKILL lands, the tree goes, survivors are zero. The
  outcome is correct.
- ❌ **NOT an Invariant #0 finding.** Nothing a page observes changes, no host
  fact escapes, no spoof is weakened. The session/orphan vocabulary invites a
  drift upward that is not warranted.
- ❌ **NOT absorbed by widening `_TEARDOWN_GRACE`.** AC7 forbids that by name.
  No threshold was touched; the arm **reports** the duration on every verdict,
  pass or finding.

**⚠️ Measured on a `sleep` tree, not on chromium.** The queued-SIGTERM mechanism
is a kernel fact and does not depend on the engine, but the wall-clock a
chromium wrapper produces is the launch lane's to report.

---

## FINDING 3 — the degradation is VERIFIED, and undone on every exit path (AC4)

**Verified**: `_stop_group_or_refuse` reads the wedge back from the process
table (`psutil.STATUS_STOPPED`) and raises `BehaviourCheckError` —
**CANNOT_RUN, exit 2, never a pass** — when it cannot confirm one. A `SIGSTOP`
that reached nothing raises nothing, and the resulting session tears down
exactly as section 8's does: a clean, confident, completely false green.

**Undone**: `_resume_group` (SIGCONT, group-anchored, via `signallable_group`)
runs in the `finally` of BOTH arms, **before** `_sweep_group`. The order is
load-bearing and is pinned by a test:

`_sweep_group` sends SIGKILL, which a stopped process **does** receive — so on
the paths that reach a working sweep, the resume is redundant. It is there for
the paths that do not: a `signallable_group` refusal (our own group, no
`killpg`), an EPERM swallowed by the sweep's own `contextlib.suppress`, or a
member reparented out of the group. **A leaked STOPPED process is worse than a
leaked running one:** it holds its RSS forever (PS-349 measured ~1.2 GB on a
12-process tree), it is invisible to anything sampling CPU, and no ordinary
teardown will touch it again.

The pids to stop come from `_survivors_or_refuse(pgid)` — the **recorded
group**, never a name match. PS-185 lost two cycles to a `pkill -f chromium`
that matched its own command line, and a wedge planted over a wider set than
the sweep can reach is precisely the escape this arm must not produce.

---

## What the arm does NOT observe, said rather than implied

- **The firefox `in_process` arm**, which is not merely unobserved but **known
  to differ**: `InvisibleProcess.terminate()` only sets a stop event, so *"a
  session that ignores it leaves a live browser thread behind while the registry
  entry is wiped"* (`baseline._teardown`'s own words). It is **unreachable from
  this check at any parameter value** — `spawn_browser`'s `in_process` is
  "honoured by the FIREFOX path only" and this check's profile is chromium by
  construction. **That is the next slice**, and its anchor is already written.
- **The `jugwedge` and `spin` gestures.** Both need `ctx.new_page()` / page
  evaluation, and the product publishes only `{"eval", "goto"}` — no `ctx`.
  Reaching them means editing the product to make a session easier to observe,
  which PS-349 declined and PS-1's charter forbids. `sigstop` needs no channel
  and its ground truth is *certain* (`PROBE.md:165` — "alive, cannot answer").
- **Chromium/Linux only**, inheriting the sibling's bound. DoD #1's *both
  engines, each shipped platform* is no closer than it was.
- **Nothing detects a degradation in production.** PS-349's Recommendation 1 is
  explicit: `busymax` — a healthy session answering every ping in 0.02s — went
  *below* the wedged arm's context-switch floor. This measures a **teardown**.

---

## On PS-349's reading, and its bound

Re-parsed from `readings/ps349-2026-09-09/jugwedge.txt` (810 rows) rather than
inherited: `nproc=12` from `t=121.5` through `t=216.9` (47 consecutive samples,
rss ~1209 MB), `nproc=0` at `t=218.9`. **A 12-process tree outlived its
session's last confirmed state by 95.4s.**

**The file's contamination bound travels with the finding.** That observer never
exited, so its tail folds in the next five arms' trees; `PROBE.md:565` states
*"only `t <= 121.5` is this arm."* The first foreign sample is `t=234.9` — **18
seconds after the reap at t=218.9** — so the observation sits entirely inside
the clean span. ⛔ Do not quote that file past `t=218.9`: an earlier draft read
those samples as teardown work when they are other sessions, which inflated the
wedged arm's CPU median to 101.8%.

**⚠️ AND THIS IS NOT A REPLICATION OF IT.** PS-349's arms ran **firefox**
`in_process=True` through PS-171's arm-F harness. This arm runs **chromium**
through `spawn_browser`. A result here must never be presented as confirming or
refuting the firefox observation. It is the product-path measurement PS-349 said
it could not make, on a different engine — and in this container it did not
reach the engine at all.

---

## The strongest counter-argument, and its honest answer

> *"SIGKILL cannot be ignored, so the group teardown passes this arm trivially —
> you are testing the kernel."*

The proposal's second answer to this leaned on the thread arm, which is
unreachable here; **that leg is withdrawn rather than left standing.** Two
remain, and the second is decisive:

**(a)** A check whose pass is predictable is still the check that would have
caught PS-349's tree, and its value is that it goes red when the escalation
regresses. A `terminate` that stopped escalating to SIGKILL — or stopped aiming
at the **group** — still passes section 8, because a healthy browser exits on
the SIGTERM alone. **This arm is the one that would not**, and the RED arm above
demonstrates exactly that.

**(c)** The prediction is precisely what had never been tested. PS-349 ran a
wedge and the teardown did **not** complete: no `child_exit`, a process spinning
at 100% CPU in state `R` 26 minutes later with no engine attached. The kernel
argument predicts that cannot happen; the one time anyone looked, it did.

And the prediction turned out to be **incomplete in a measurable way**: the
teardown does complete, and it costs the full timeout doing so (Finding 2). That
is a fact neither the kernel argument nor PS-349 could have supplied.

---

## Files

| file | what it is |
|---|---|
| `probe.py` | drives the whole registry entry (falsify + run) on a real tree, both arms, with a leak audit |
| `run.txt` | that probe's output, verbatim |
| `wait_isolation.py` | times each leg of `terminate_process_group`'s escalation, wedged vs healthy |
| `wait_isolation.txt` | that isolation's output, verbatim |

Both scripts substitute **only** `spawn_browser` (and, in the red arm,
`terminate`). `_launch_and_grow`, `_stop_group_or_refuse`, `_survivors_or_refuse`,
`_resume_group`, `_sweep_group` and — in the quiet arm — the product's
`terminate` are the real ones.
