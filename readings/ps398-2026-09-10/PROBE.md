# PS-398 — is the announce→publish window REACHABLE, and does the bounded wait close it?

**Date:** 2026-09-10
**Ticket:** PS-398
**Base:** `294179c` (== `origin/main` at execution time)
**Deliverable:** evidence for the change in `src/services/verify/baseline.py`.

---

## ⛔ WHAT THIS IS NOT

**It is not a measurement of PS-380's "~1 run in 3".** That figure is the
*product's* flake rate on a real Firefox launch, and it could **not** be
reproduced in this container: there is no `invisible_playwright`, no engine
binary under `~/.cache/invisible-playwright`, no `~/.persona/engine` and no
`xvfb-run`. **No browser was launched to produce anything below**, and no
number here may be quoted as the lane's real rate.

Reporting an unreproduced figure as if it had been measured is the exact shape
this project has shipped twice (PS-299's "81/81 hunks ✅" against an empty
directory, PS-341's "8 of 8 moved"), so the honest position is stated first.

## What it IS

The **mechanism**, not the product. The probes drive the **real** `emit` writer
construction (`os.fdopen(..., buffering=1)`, `write()` + `flush()`) and the
**real** `register_ff_eval`/`get_ff_eval` registry from
`src/services/browser/invisible_launch.py`, in the ordering `_launch_and_watch`
uses — announce `BROWSER_STARTED`, define three closures, then register — with a
reader on another thread doing exactly what `_record_on_firefox` does: return on
the announcement, then look up the hook.

The question is narrow and answerable without an engine: **can a reader that
arrives on the announcement observe an empty registry?** That is the whole
defect; everything else about the launch is irrelevant to it.

## Results

| arm | trials | single-read misses | rate |
|---|---|---|---|
| **idle** interpreter (`probe_idle.py`) | 300 | **0** | 0.0% |
| **loaded** — 4 CPU-burning threads, 5 µs switch interval (`probe_single_read.py`) | 400 | **104** | **26.0%** |
| **loaded + the bounded wait** (`probe_bounded_wait.py`) | 400 | **0** | 0.0% |

Cost of the wait on the loaded arm: **2.76 s over 400 trials — 6.89 ms/trial**,
against a 5 s budget it never came close to spending.

⚠️ **The loaded arm's rate MOVES between runs** — a second execution of the same
file gave **123/400 (30.8%)**, and the wait arm gave 0/400 again at 6.20
ms/trial. The single-read rate is a property of this container's scheduling on
the day, not a constant; the **0** on the wait arm is the reading that is
stable, and it is the one the change rests on.

## Reading

1. **The window is real and it is reachable.** 104 of 400 readers arriving on
   the announcement found no hook, against code that had already emitted it and
   was three closure definitions from registering. The ordering argument in the
   ticket is not theoretical.
2. **It is invisible on an idle box.** 0 of 300 on the same code. That is why
   this is a *flake* rather than a bug anyone can reproduce on demand — and why
   an idle container answering "0 misses" is not evidence of absence. **A CI
   runner is the loaded arm, not the idle one.**
3. **The bounded wait closes it, and costs milliseconds.** 0 of 400 under the
   same contention that produced 104 misses without it, at ~7 ms per trial.
4. ⚠️ **26% IS NOT THE PRODUCT'S RATE.** The contention here is synthetic and
   was tuned until the window opened; the loading is the *instrument*, not a
   model of CI. Read it as "the window is reachable and the wait closes it",
   never as "the lane fails 26% of the time". The similarity to PS-380's ~1-in-3
   is a coincidence of two unrelated instruments and must not be presented as
   corroboration.

## Reproducing

```
.venv/bin/python readings/ps398-2026-09-10/probe_idle.py
.venv/bin/python readings/ps398-2026-09-10/probe_single_read.py
.venv/bin/python readings/ps398-2026-09-10/probe_bounded_wait.py
```

`probe_bounded_wait.py` imports `baseline._await_ff_eval_hook`, so it only runs
against the fix; the other two run against any tree.
