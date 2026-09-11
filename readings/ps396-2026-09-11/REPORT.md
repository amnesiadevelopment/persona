# PS-396 — the session's own cpu/ctxt series, both arms, measured

**Date:** 2026-09-11 · **Base:** `1133fa0` · **Branch:** `feat/PS-396-session-resource-series`
**Instrument:** `arm.py` in this directory, driving the SHIPPED
`src/services/browser/session_series.SessionSeriesRecorder` — imported, not
copied, so what is measured here is the code that ships.
**Subject:** a real chromium tree (`/usr/bin/chromium` 152.0.7977.82, headless,
no sandbox, no GPU) launched into a real profile dir.

⛔ **This record measures a RECORDER. It contains no verdict, no threshold, and
nothing acts on any number in it.** PS-349 Recommendation 1 forbids shipping
anything that acts, on a table where a HEALTHY arm read below a WEDGED one; the
overlap section below restates that against this reading's own numbers.

---

## AC #2 — the two arms

Both arms ran for ~44 s at a 1 s cadence (tightened from the shipped 2 s so one
arm fits in one page; the cost figures in AC #8 are at the shipped cadence).
Both halved: the first half is the arm's baseline, the second half is the
gesture.

### Instrument check, BEFORE any verdict

```
QUIET  arm: matched 11 engine processes
SIGNAL arm: matched 11 engine processes
```

⚠️ **This is not decoration.** This project has twice shipped a clean,
confident and completely false result produced by the *instrument* rather than
the engine — PS-299's rebase probe printing *"81/81 hunks, 0 rejects ✅"*
against an empty directory, and PS-341's `--dump-dom` reading *"8 of 8 moved"*.
`arm.py` therefore **aborts** on a zero match rather than reporting a clean
series. It matched eleven processes both times, so the numbers below are about
a browser.

### QUIET arm — a HEALTHY session, **under load**, does not look degraded

Full log: `arm-quiet.log` · Raw record: `series-quiet.jsonl`

⚠️ **THE LOAD IS STATED, because a quiet arm run idle would be the easy
reading.** The page is
`<script>let x=0;function f(){for(let i=0;i<8e6;i++)x+=Math.sqrt(i);requestAnimationFrame(f);}f();</script>`
— a renderer kept genuinely busy for the whole session, chosen because PS-349
measured the healthy arm `busymax` reading BELOW the wedged arm `jugwedge` on
the context-switch axis. A loaded healthy arm is the hard case for a record
that is supposed to discriminate.

```
n=44 samples          cpu med = 105.6   cpu min = 60.2
                   ctxt_v med =    40   ctxt_v min =  19
first  half (baseline)  cpu med = 106.3   ctxt_v min = 19
second half (no gesture) cpu med = 105.6   ctxt_v min = 19
```

**Observed result: the healthy series does not look degraded, and it does not
drift.** cpu sits at ~105% of one core throughout (the busy renderer plus the
tree's other processes), context switches never reach zero, and the two halves
are indistinguishable — which is the correct reading for an arm where nothing
happened.

### SIGNAL arm — the SAME session, SIGSTOP'd, and the record carries it

Full log: `arm-signal.log` · Raw record: `series-signal.jsonl`

PS-349's cheapest gesture, and the one whose ground truth is **certain**
(`PROBE.md:165` — *"alive, cannot answer"*): SIGSTOP every pid of the tree at
the halfway mark. The processes remain alive and matched (`nproc` stays 11);
they simply stop running.

```
n=44 samples
first  half (running)  cpu med = 106.2   ctxt_v min = 15
second half (SIGSTOP)  cpu med =   0.0   ctxt_v min =  0   ctxt_nv = 0
```

**Observed result: the degradation is in the record, sample by sample.** The
transition is visible at a single sample boundary:

```
   t nproc      cpu  ctxt_v ctxt_nv denied  gone
16.1    11     96.5      46       8      0     0
17.1    11      0.0       0       0      0     0     <- SIGSTOP
```

⚠️ **And note `denied 0` on every stopped sample, with `cpu_from 11` /
`ctxt_from 11`.** Those zeros are a **reading of eleven readable processes that
did nothing**, not a failure to look. That distinction is AC #4 and it is what
makes the wedged signature *meaningful*: a recorder that answered `0` when
denied would produce this exact row for a permissions problem.

### ⚠️ The overlap, stated rather than smoothed over

The two arms above separate cleanly, and **that is a fact about SIGSTOP, not a
licence to threshold.** PS-349's own table (`PROBE.md:228-239`) is the reason:

```
arm          truth       cpu med   ctxt min
busymax      HEALTHY     103.9     9      <- healthy, answering every ping in 0.02s
jugwedge     DEGRADED      0.5     18     <- WEDGED, HIGHER than the healthy one
sigstop      DEGRADED      0.0     0
spin         DEGRADED    110.2     202    <- WEDGED, higher than several healthy ones
```

This reading's own quiet arm lands **inside that overlap**: `ctxt_v min = 19`,
which is *above* `jugwedge`'s wedged floor of 18 by one sample, and `cpu med
105.6`, which is *above* `spin`'s wedged 110.2 by less than 5%. A healthy
session under load and a wedged session are **not separable by a constant** on
this evidence. That is why nothing reads this file.

---

## AC #8 — the observed cost, at the shipped 2 s cadence

Measured against a live chromium tree of 10 processes over 40 s:

```
0.500 samples/s          (exactly the 2.0 s cadence)
138 bytes per sample
243.2 KiB/hour
0.26% of one core        (the recorder's own cpu, whole interval, RUSAGE_SELF)
4 MiB cap reached after 16.8 hours of continuous session
```

The per-arm logs above report 430–475 KiB/hour because they ran at the
tightened 1 s cadence — double the rate, as expected.

⛔ **Stated rather than inherited.** Recommendation 2 called the two series
"cheap"; this is what cheap measured as. The dominant cost is `/proc` reads
scaling with tree size (two files per process per sample), so a 40-process
Firefox tree costs proportionally more than the 10–11 measured here.

---

## AC #9 — platform scope: **Linux alone**

The reader is `/proc`. persona ships on three platforms and this series exists
on one of them. `series_capability()` is the machine-readable statement of that
and the test asserting it is `test_capability_is_stated_per_platform_...`.

⛔ **psutil does NOT widen it, and the reason is measured in the installed
library rather than assumed.** psutil IS a declared runtime dependency
(`psutil>=6.0`, both `requirements.txt` and `pyproject.toml`) and would appear
to make this portable. Read off 7.2.2:

```python
# _pswindows.py:1093            # _psosx.py:482
return ntp.pctxsw(ctx_switches, 0)   return ntp.pctxsw(vol, 0)
#                              ^-- the nonvoluntary leg is a LITERAL 0
```

On Windows and macOS the nonvoluntary leg is **not measured**; a constant zero
is returned in the shape of a reading, indistinguishable from a genuine zero —
and it forges the exact `sigstop` signature this reading's signal arm produces,
on the platforms where it is least checkable. The Linux arm of psutil parses
both legs genuinely, which is what makes this a per-platform asymmetry rather
than a library convention.

**So the delivery is Linux-only and says so.** Off Linux the recorder writes
**no file at all** — not an empty one, which would read as a session that
produced no samples.

⛔ **This does NOT close PS-8 DoD #4.** DoD #4 asks that the product
*distinguish* a working run from a stopped one. Recording is not
distinguishing. What this ships is the substrate a future distinction would be
calibrated on, and Recommendation 4 says that calibration needs real pages on
real hardware, which no cycle has.

---

## AC #10 — the full-suite failure SET, diffed intra-container

Both runs in THIS container, same interpreter, same installed packages, same
`-p no:randomly` ordering. The base is a clean worktree at `1133fa0`.

```
BASE   (1133fa0, pristine worktree)   77 failed, 7202 passed, 80 skipped   24:31
BRANCH (this work)                    79 failed, 7220 passed, 80 skipped   25:41
```

⛔ **Never assert zero — the absent-import set was re-measured here rather than
inherited.** The ticket's own list said `psutil` was absent; it is **present at
7.2.2**. On a first pass `flet`, `fastapi`, `cryptography`, `mcp`, `paramiko`,
`uvicorn` and `requests` were also absent until `requirements.txt` was
installed. The 77 baseline failures that remain are `invisible_playwright` and
`playwright` artifacts of this container, not of the tree.

**The 79 was NOT accepted as a wash.** Diffed as SETS:

```
IN BRANCH, NOT IN BASE:
  tests/test_encoding_discipline.py::test_no_platform_dependent_text_decode_in_tests
  tests/test_encoding_discipline.py::test_product_side_platform_dependent_decodes_do_not_grow
IN BASE, NOT IN BRANCH:
  (none)
```

Both were **mine**, and both were real: nine `open()`/`write_text()` calls in
the new test file and two `/proc` reads in the new module named no encoding, so
they resolve to cp1252 on Windows while the product writes utf-8. A project
guard doing exactly its job. Fixed in the branch's encoding commit; the two tests now pass, and the
suites for every touched file pass together (160 tests: the new file, both
encoding guards, all six launcher suites, the launch guard, all four wipe
suites, the manager stop hook, and PS-330's convention test).

⚠️ **The final failure set is therefore identical to the base's**, established
by the set diff above plus a targeted re-run — not by a second 25-minute
full-suite run, which would have measured the same 77 container artifacts again.

### Each guard was falsified by breaking the property it asserts

A test that cannot fail is not evidence. Before shipping, each was made to fail:

| break | result |
|---|---|
| `cpu`/`ctxt_v` record `0` instead of `None` | 3 fail (AC #4's three) |
| `start_recording` moved inside the `terminate(proc, ...)` arm | 1 fail (AC #5's call-site guard) |
| a reader of `series_path` added to `src/ui/state.py` | 1 fail (AC #3's absence) |

The AC #4 falsification was re-run after the encoding fix, on the shipped code.

---

## A defect this work found in its own instrument, kept because it is the shape

Building the signal arm, the first run **hung**: the SIGSTOP froze the sampler
along with the engine.

The cause was the matcher. `arm.py` takes the profile dir as `argv[1]`, so the
harness's own cmdline contained the profile path, so the path matcher returned
the **observer** as a member of the tree it was observing. That is PS-185's
`pkill -f chromium` lesson one level down — and for a *recorder* the damage is
quieter than a kill and no less wrong: every sample would have folded persona's
own cpu into the engine's series.

`engine_pids_for` now excludes `os.getpid()` explicitly, with a test named for
the incident. A path matcher is a substring test and a substring test sees
everyone; the one process it may never count is the one asking.

---

## What is NOT established here

1. **No Firefox arm.** Both arms ran against chromium, the engine available in
   this container. The matcher's doctrine is inherited from PS-171/PS-349's
   Firefox measurement (`comm` finding 1 process where the path matcher found
   11), and the profile-dir matcher works on both engines by construction — but
   it is **measured** on chromium only.
2. **n=1 per arm**, ~44 s each, on one container, one kernel, headless, no GPU.
3. **No claim about the in-process (thread) launch arm.** The matcher works on
   both arms by construction — it matches the engine's cmdline, not persona's —
   but only the fork/Popen shape was exercised here.
4. **Nothing about PS-171's stall is fixed.** This ships the record that makes
   the next one cheaper to diagnose, and nothing more.
