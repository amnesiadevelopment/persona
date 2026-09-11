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

⛔ **ROUND 1'S FIGURE IS WITHDRAWN, AND IT WAS WRONG IN A WAY WORTH KNOWING.**
Round 1 reported *"0.26% of one core, 138 bytes per sample, 243.2 KiB/hour, cap
at 16.8 h"* from an **ad-hoc measurement with no committed instrument**. When
the matcher changed under it (round 2), the figure could not be re-taken — only
re-asserted. A cost figure that cannot be reproduced is a claim, not a
measurement, so the instrument now exists: `arm-cost.py`, committed, with its
output at `arm-cost.log`.

Re-measured against a live chromium tree, with the shipped boundary-anchored
matcher:

```
/proc population              41 numeric entries
tree size (nproc)             11 processes
per-sample median           2.83 ms
cadence                      2.0 s
samples/second             0.500
bytes/sample                 103 B
bytes/hour                 181.1 KiB
cap reached after           22.6 h
cpu cost                    0.14 % of ONE core
```

`cpu cost` is per-sample **wall** time over the cadence, so it is an upper
bound: a sample blocked on a `/proc` read is counted as if it were spinning.

⚠️ **AND THE DOMINANT TERM IS NOT THE TREE — IT IS THE `/proc` WALK.** Round 1
said the cost scales with tree size ("two files per process per sample"), which
is what Recommendation 2's *"two `/proc` reads per sample"* implies. It is
wrong. `engine_pids_for` opens `cmdline` for **every numeric entry under
`/proc`**, so the cost is bounded by how many processes the **box** runs, not by
how many the browser runs. Measured directly (`arm-cost.py --scale` arm,
synthetic `/proc` roots, one matching entry each):

```
/proc entries   walk time   % of one core at the 2 s cadence
          100     2.98 ms        0.15 %
          400    12.01 ms        0.60 %
          800    22.86 ms        1.14 %
         1600    51.50 ms        2.57 %
```

Linear in the `/proc` population and flat in the tree size. On a busy
workstation (1600 processes is an ordinary developer machine) the sampler costs
**~2.6% of one core**, an order of magnitude above the figure measured in this
container's 41-process `/proc`.

⛔ **So a cost figure quoted WITHOUT its `/proc` population is not
reproducible**, and this is the correction that matters more than the number:
`0.14%` here and `2.57%` there are the same instrument measuring the same code.
Both are stated; neither is "the" cost.

⛔ **Stated rather than inherited.** Recommendation 2 called the two series
"cheap". On this container it is; the scaling arm is what says under which
conditions that stops being true.

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

### The set diff's own blind spot, checked rather than assumed

A **counting** test (a ratchet, a coverage floor, a lint total) that is red on
BOTH sides **cancels out of a set diff** while the number inside it moves — a
PR can add eleven violations and still truthfully report "failure sets
identical" (measured on PS-202, where exactly that hid eleven new encoding
violations). So the both-red set was examined rather than waved through:

```
comm -12 base.txt mine.txt              -> 77 tests, in 6 files
grep -lE "ratchet|must stay at zero|do not grow|no new "  -> none of them
normalise every numeric assertion message to N, diff the multiset
                                        -> the only difference is the
                                           encoding ratchet's own message,
                                           present in the branch run and
                                           absent after the fix
```

All 77 both-red tests are plain `invisible_playwright` / `playwright` import
failures carrying no counting assertion. And the closing check is stronger than
the diff: both encoding ratchets now **pass outright** — zero, not a lowered
number.

### Each guard was falsified by breaking the property it asserts

A test that cannot fail is not evidence. Before shipping, each was made to fail:

| break | result |
|---|---|
| `cpu`/`ctxt_v` record `0` instead of `None` | 3 fail (AC #4's three) |
| `start_recording` moved inside the `terminate(proc, ...)` arm | 1 fail (AC #5's call-site guard) |
| an importer of the recorder added to `src/ui/state.py` | 1 fail (AC #3's absence) |
| `.persona-session-series` removed from `_EXPORT_EXCLUDE_DIRS` | 2 fail (the export guard + AC #3's pin) |

The AC #4 falsification was re-run after the encoding fix, on the shipped code.

⚠️ **The set diff above was taken before the export exclusion landed.** That
change touches `transfer.py` and the new test file only; the suites for every
file it touches were re-run together and pass (195 tests: the new file, both
encoding guards, all four transfer/export/import suites, all six launcher
suites, the launch guard, the four wipe suites, and PS-330's convention test).

---

## The perimeter cuts both ways — a second consumer, caught by a memory

Putting the series inside the profile's data dir buys destruction for free.
But *"inside the profile data dir"* is a property OTHER code reads too, and
those consumers were written before this directory existed. PS-129 learned this
the expensive way: a new `.persona-tmp` would have added ~714 MB of engine
scratch to **every exported profile**, caught only by grepping for other
walkers of the same directory.

`export_to_zip` walks the whole profile data dir with exactly one pruning set,
`_EXPORT_EXCLUDE_DIRS`. The series is now in it, and here the argument is
**privacy rather than bulk**: the file is capped at 4 MiB, so size is not the
objection — it is a timestamped record of **when this operator's browser was
busy**, a behavioural trace of the person rather than a property of the
profile. An export is precisely *"a file the operator may share"*, which is the
PS-330 convention this record already answers by carrying no profile name;
shipping it inside an export would put the same class of fact back into the
same class of file by the back door.

⚠️ **Three entries, three different reasons** — `.persona-mtls` is SECRECY (a
cleartext client key), `.persona-tmp` is BULK, `.persona-session-series` is
PRIVACY. Stated in the code so a future reader who decides one argument is
obsolete concludes nothing about the other two.

Pinned by a test **with a control**: an ordinary `prefs.js` must appear in the
archive, so an export that shipped nothing at all cannot pass as a clean
exclusion. Falsified by removing the entry.

The same pass tightened AC #3's absence guard, which had flagged this exclusion
as a third mention of the module. It now separates the two axes that matter —
**importers** (parsed with `ast`, so naming a directory is not confused for
using the module) and **readers** (anything opening the file) — and pins the
excluder by name. ⭐ **An excluder is not a reader:** it keeps the record out of
a shared file and consumes nothing, which is the opposite of a step toward a
verdict.

---

## ⛔ ROUND 2 — the matcher was WRONG IN TWO DIRECTIONS AT ONCE, and the
## suggested fix was wrong in a third

The round-1 review rejected on the matcher alone, and it was right twice. Both
defects are recorded here with the measurement that settled them, because the
second one turns on evidence the review could not have had.

### Defect A — the prefix-sibling overcount (`work` absorbing `work2`)

`profile_dir in cmdline` is an **unanchored substring test**.
`validate_profile_name` permits digits and hyphens freely, so `work`/`work2` and
`client`/`client-2` are ordinary — and `/data/profiles/work` is a substring of
`/data/profiles/work2`. The **shorter** name absorbs the longer one's entire
tree, so the first profile an operator creates is the one whose record silently
accumulates its neighbours' cpu, recorded with `denied 0` — i.e. **as a
confident reading**, which is the class of forged value AC #4 exists to refuse.

This is a named, measured defect class in this repo, at two sites predating the
merge-base: `invisible_launch.py:5150-5156` (*"Matching the bare profile_dir is
a SUBSTRING match, so \"work\" also matches \"work2\"'s command line … the #150
wrong-kill class, between prefix-sibling personas"*) and `:5458-5460`.

### Defect B — the Firefox undercount, asserted by its own fixture

The round-1 docstring stated as established fact that the profile path *"appears
in the argv of every child of the tree"* for Firefox. The repo says the opposite
twice, both predating this branch:

- `invisible_launch.py:4409-4411` — *"The content procs don't carry the profile
  dir on their command line, so they're matched to this profile by descending
  from the profile's launcher parent."*
- `invisible_launch.py:4959-4961` — *"Firefox content/GPU children don't carry
  the profile dir on their command line, so the pid match only sees the
  parent."*

and PS-212's QA seat **measured** this exact key against a live 7-process
Firefox tree: the profile-dir key returned **1**.

Worse, the unit fixture **invented** a `Web Content` child carrying the profile
path — so the test asserted the docstring's premise instead of testing it, and
would have stayed green while a real Firefox tree went unmatched. That is an
instrument-produced green of the PS-299 / PS-341 family, in the guard that was
supposed to hold the matcher's doctrine.

### ⛔ Defect C — the SUGGESTED fix drops EVERY chromium child (measured)

The review proposed tokenising the cmdline on its NUL separators and requiring a
whole-token or path-prefix match. **It was not applied, because it is wrong**,
and the reason is a property no stub reproduction can show.

Measured against **two live chromium sessions** in prefix-sibling profile dirs,
all three matchers applied to the same `/proc`
(`arm-sibling.py`, `arm-sibling.log`):

```
matcher                            work   work2
bare `in` (what shipped)             20      11   <- the review's blocker, confirmed
token-split (the suggestion)          1       1   <- drops EVERY child
boundary-anchored (shipped now)      11      11   <- correct both ways
```

(`work`'s 20 is its own 11 plus the sibling's, minus the one renderer that moved
between the sequential scans; the scans are not atomic, so read the **shape**.)

The cause, from the same run:

```
argv shape of the chromium processes on this box:
   1 token(s):  16 processes   <- every child: zygote, gpu, utility, renderers
  11 token(s):   4 processes
  14 token(s):   2 processes   <- the two parents

/proc/<renderer>/cmdline  ->  1 token:
  ['/usr/lib/chromium/chromium --type=renderer --crashpad-handler-pid=976
    --user-data-dir=/tmp/ps396-sib/work ...']
```

**Chromium's children do not have a conventional argv.** The whole command line
arrives as ONE NUL-terminated entry with its spaces inside it, so
`tok.split("=", 1)[1]` never yields the bare dir and a whole-token comparison
sees the two parents alone.

That is **the PS-171 arm-H undercount arriving through the fix for the
overcount** — the exact failure the docstring cites arm H to forbid, and it
would have produced `nproc 1` with `denied 0`: a confident record of a
one-process tree. Trading an overcount for an undercount is not a fix.

### What shipped instead

A **boundary-anchored scan**: the profile path must be preceded by
`\0`/`=`/whitespace/quote and followed by one of those or `os.sep`. It is
**indifferent to how the engine packs its argv**, which is the property that
matters — this matcher must not need to know.

Five unit tests pin the shapes, including a **packed-blob case specifically so
nobody tidies it back into a `split`**, and the prefix-sibling case the review
asked for (plus the hyphenated `work-2` shape).

Blocker B is answered by **stating the scope, not by widening the matcher**: the
docstring now states coverage **per engine** (chromium MEASURED parent + every
child; firefox **NOT MEASURED**, expected to resolve the launcher parent alone,
with both `invisible_launch.py` sites quoted), the fixture is now the tree *as
those comments describe it* — content children with `-contentproc -isForBrowser`
and **no** profile path — asserting the honest `[700]`, and the test says in its
own name and docstring that it **pins a limitation, not a feature**, so what
retires it is a measurement rather than an edit.

⭐ **The honest alternative is named rather than hidden.** A two-stage match
(anchor on the parent by profile path, then descend by ppid — which is what
`_firefox_content_proc_count` already does via `_descendant_pids`) would widen
Firefox coverage. It is deliberately not done here: no Firefox engine exists in
this container, and this module must not be the site where an *unmeasured* claim
about Firefox is introduced. PS-212's general rule is the one this matcher
tripped on — *a matcher has TWO independent degrees of freedom, the SOURCE and
the KEY, and fixing only the source leaves the fault live* — and the key change
(engine dir → profile dir) was made for a real reason (two concurrent profiles
must not fold into one series), so reverting it would re-create cross-profile
mixing in a wider form. Both keys are individually wrong; the scope statement is
the honest answer available without a Firefox measurement.

### Two things the rework found on its own

1. **`SCHEMA` bumped 1 → 2.** A schema-1 file was written by the broken matcher,
   so its `nproc` and every series on it may carry a sibling's processes. Not a
   cosmetic version difference — it is how a reader of a file already on disk
   knows it is untrustworthy.
2. **The header line now carries `matcher` and `matcher_note`** (the review's
   non-blocking note 1, implemented). `nproc: 1` cannot distinguish *one
   process* from *one process because the matcher is blind to this engine's
   children*, so the file says which matcher produced its numbers and what that
   matcher is and is not measured on — in the file, which outlives the
   docstring.

### Round-2 falsification ledger

| break | result |
|---|---|
| revert to bare `in` | `test_a_prefix_sibling_profile_is_not_absorbed` → **1 fail** |
| apply the review's token-split candidate | **11 fail** |
| `SCHEMA` back to 1 | `test_the_record_says_what_its_matcher_matched_on` → **1 fail** |
| remove `matcher`/`matcher_note` from the header | **1 fail** |

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

1. **No Firefox arm, and the round-1 claim about it was WITHDRAWN rather than
   softened.** Both arms ran against chromium, the engine available in this
   container. Round 1 stated the matcher worked on both engines *"by
   construction"*; the repo's own record and PS-212's live 7-process
   measurement say it does not, and the docstring now states the narrow scope
   instead. What is asserted about Firefox is what this repo already recorded,
   pinned as a **limitation**.
2. **n=1 per arm**, ~44 s each, on one container, one kernel, headless, no GPU.
3. **No claim about the in-process (thread) launch arm.** The matcher does not
   match persona itself on either arm — on the fork arm the shim carries
   persona's cmdline, on the thread arm there is no separate process — but only
   the fork/Popen shape was exercised here.
4. **Nothing about PS-171's stall is fixed.** This ships the record that makes
   the next one cheaper to diagnose, and nothing more.
5. **The cost figure is container-specific.** `0.14%` of one core was measured
   in a 41-process `/proc`; the scaling arm shows `2.57%` at 1600 processes.
   Neither is "the" cost — see AC #8.
