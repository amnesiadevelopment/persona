# PS-402 — the firefox survivor arm, measured. AC3 answered; AC1 refused with a reason.

**Base:** `068f12b` (branch `feature/PS-402-firefox-survivor-arm`)
**Venue:** this agent container — `sudo apt-get install xvfb`, `uv pip install invisible_playwright`,
`src.services.engine.firefox.download_engine("firefox-20")`. `psutil` 7.2.2.
**Engine:** `~/.cache/invisible-playwright/firefox-20_151.0_20260817150018/firefox` — the
PERSONIUM build named by `engine-baseline.txt`, fetched by the product's own downloader,
**not** stock playwright Firefox.
**Launches:** 8 real firefox sessions through `spawn_browser`, under `xvfb-run -a`, torn down
by the product's own `terminate()` from `..browser.process`.

⭐ Every count below is taken from the operating system's process table
(`process_group_survivors`, `psutil`, `os.getpgid`, `os.getsid`) — never from a teardown log
line. That distinction is PS-8 DoD #1's whole point.

⚠️ **Zombies are excluded from every survivor count.** A reaped-but-unwaited process is not a
leaked one, and counting it as one is the false-positive twin of the vacuous zero.

---

## THE HEADLINE

**PS-402's premise about the TREE is confirmed to the process name. Its premise about what a
firefox survivor arm would MEASURE is refuted — and the refutation is a sharper finding than
the ticket's own.**

> The persona firefox engine tree is **10–11 processes in its OWN SESSION**. The group the
> survivor gate records and counts holds **2** — the forked python leader and the playwright
> node driver. **The intersection is empty on every launch.** `_MIN_LIVE_TREE = 3` is therefore
> unsatisfiable, the falsification leaves zero survivors, and a firefox arm built as PS-402
> specifies would report **CANNOT_RUN (exit 2) on every run, forever**.

The firefox teardown itself is **correct** and is removed by two mechanisms, both isolated
below. What does not work is the **gate's instrument** on this engine.

---

## 1. THE STRUCTURE — `gecko ∩ recorded_group = ∅`, on all 8 launches

`readings/ps402-2026-09-10/tree_vs_group.py`, sampled at t≈5/10/20/30/45s. Stable across the
whole window; one representative reading, per-pid `pgid`/`sid` read from the kernel:

```
leader=11381   recorded pgid=11381

RECORDED GROUP (2): [11381, 11384]
  pid 11381  pgid 11381  sid 11381  python        the forked leader (mp fork ctx)
  pid 11384  pgid 11381  sid 11381  MainThread    playwright node driver

GECKO TREE (11): [11396, 11444, 11495, 11502, 11510, 11544, 11591, 11599, 11602, 11632, …]
  pid 11396  pgid 11396  sid 11396  firefox           -no-remote -wait-for-browser
  pid 11444  pgid 11396  sid 11396  Socket Process
  pid 11495  pgid 11396  sid 11396  forkserver
  pid 11502  pgid 11396  sid 11396  WebExtensions
  pid 11510  pgid 11396  sid 11396  RDD Process
  pid 11591  pgid 11396  sid 11396  Utility Process
  pid 11544  pgid 11396  sid 11396  Web Content
  pid 11599  pgid 11396  sid 11396  Web Content
  pid 11602  pgid 11396  sid 11396  Web Content
  pid 11632  pgid 11396  sid 11396  Web Content

⭐ is the gecko tree IN the recorded group?  False
```

**`firefox` calls `setsid`**, so the engine leader's `pgid == sid == its own pid` — a *different*
group from the one `record_group_by_construction(self, pid=self._proc.pid)` recorded at
`invisible_launch.py:5577`. Nothing is wrong with that recording: it is the group the FORKED
LEADER leads, and `_child`'s `start_own_session()` is what makes it one. The engine then leaves
it.

⭐ **This is precisely the tree PS-171 and PS-349 measured** — Socket Process, forkserver,
WebExtensions, RDD, Utility, 4× Web Content, matching
`readings/ps171-2026-08-25/REPRO.md:405-407`'s named children and PS-349's `nproc` 10/12
(re-counted: 300 samples at 10, 105 at 12). **The ticket's tree-size premise is correct.** What
is new is that those processes are not in the group a group-anchored counter looks at.

## 2. AC3 — NOT a slow curve. A **CEILING**.

`readings/ps402-2026-09-10/probe.py`, which samples exactly what `_launch_and_grow` samples
(`process_group_survivors` on `recorded_group(proc)`), at the same 0.25s interval, against the
same three constants:

```
[p402q] spawn_browser returned in 0.17s; handle=InvisibleProcess pid=8813 _fork=True
[p402q] recorded_group -> 8813
         0.18s  1
         0.43s  2
         …      2      (oscillating 2 ↔ 3 for the full window as the driver forks transients)
        90.04s  3
[p402q] peak=3  settled_at=None  stable_run=0/8
```

⛔ **This is not the surprise AC3 warned about.** A Gecko tree growing more slowly than a
chromium one would be cured by a larger `_TREE_GROW_TIMEOUT`. This is a **ceiling**: the tree
came up in ~5s and held 10–11 members for 45s while the counted group never exceeded 3.
`_MIN_LIVE_TREE = 3` cannot be satisfied by a population of 2, so `_launch_and_grow` raises
its "never held a SETTLED tree" refusal — correctly — and the arm is CANNOT_RUN on every run.

**A widened timeout cannot fix a ceiling, and lowering `_MIN_LIVE_TREE` is forbidden by the
ticket (out of scope) and would be wrong anyway** — 2 is exactly what several ways of not
launching look like, which is the guard's entire reason for existing.

## 3. AC2's RED ARM IS UNREACHABLE (n=2)

`readings/ps402-2026-09-10/falsifiability.py` runs
`_falsify_no_process_survives_a_closed_session`'s body **verbatim**: `os.kill(SIGTERM)` on the
HELD PID → `wait(10)` → `os.kill(SIGKILL)` → `wait(5)` → `sleep(_TEARDOWN_GRACE)` → count.

⚠️ `os.kill` on the held pid, **never** `proc.terminate()`. §5's rule — *"on persona's Linux
FORK path the handle's own `kill()` is group-aware (it IS the PS-192 fix)"* — was written about
this exact path and is inherited rather than re-derived.

```
f1  RECORDED GROUP 2, GECKO 10, gecko sid 16894
      group survivors 0    gecko survivors 0    gecko-session survivors 0
f2  RECORDED GROUP 2, GECKO 10, gecko sid 17469
      group survivors 0    gecko survivors 0    gecko-session survivors 0
```

`run_check` runs `falsify` FIRST, and a check that fails its self-test never reaches its verdict.
**So the arm would report CANNOT_RUN even if the settle precondition were somehow met.** AC1 and
AC2 cannot both be satisfied on this engine with this instrument.

## 4. ⭐ THE MECHANISM, ISOLATED — the group signal reaches the engine tree on NO path

`readings/ps402-2026-09-10/mechanism.py`. Two arms differing **only in the signal**. The Gecko
tree is SIGSTOPped in both, so the `-wait-for-browser` pipe cascade cannot act and the only
remaining variable is whether the in-child SIGTERM handler can run.

| arm | action | gecko survivors |
|---|---|---|
| `g1` | wedge, then `killpg(pgid, **SIGTERM**)` — handler CAN run | **0 of 10** |
| `h1` | wedge, then `killpg(pgid, **SIGKILL**)` — handler CANNOT run | ⛔ **10 of 10**, all STOPPED |
| `h2` | same, second reading | ⛔ **10 of 10**, all STOPPED |

`g1`'s own child log names the mechanism:

```
CHILD: LIFECYCLE close=stop-requested pid=20556
CHILD: LIFECYCLE teardown-kill pids=[20556] rescan=False
CHILD: BROWSER_CLOSED
```

**`_child` installs `signal.signal(SIGTERM, stop_gracefully)` (`invisible_launch.py:4148`), and
`stop_gracefully` runs `session.teardown()` — which kills the engine BY PID, from inside the
child.** SIGKILL cannot be handled, so under SIGKILL nothing removes the tree.

⚠️ **A SIGSTOPped process is not unkillable, and that is what makes the wedge a fair test rather
than a trick.** SIGKILL *does* reach a stopped process. The control is in
`readings/ps402-2026-09-10/isolation.py`: freezing the **node driver** — which IS in the
recorded group — and then `killpg(SIGKILL)` removed **10 of 10**. What the wedge removes is not
the tree's killability but the CASCADE, which is the only thing that was ever reaching it.

⚠️ **The two `[PARTIAL FREEZE]` refusals are reported rather than hidden.** The first two
`h`-arm attempts confirmed only 8 and 9 of 10 members STOPPED and **refused to publish a
result** — an unfrozen member can still react to the pipe, which is the mechanism under test.
The confirmation loop was then made to re-read the tree and re-signal newcomers for up to 5s
(the signal was never widened, only the confirmation retried), after which both arms confirmed
10 of 10 and ran.

## 5. THROUGH THE PRODUCT'S OWN `terminate()` — clean on every path the product takes

`readings/ps402-2026-09-10/product_teardown.py`:

| arm | wedge | `terminate()` returned | group survivors | gecko survivors |
|---|---|---|---|---|
| `q1` | no (control) | 1.62s | 0 | 0 of 10 |
| `w1` | 10/10 STOPPED | 5.43s | 0 | 0 of 10 |
| `w2` | 10/10 STOPPED | 6.05s | 0 | 0 of 10 |

⭐ **The shipped teardown is correct, wedged or not**, because `terminate_process_group` sends
**SIGTERM to the group first** — which is exactly the signal the in-child handler needs. The
leak in §4 is reachable only by a SIGKILL-first teardown, **which the product does not do
anywhere**. This is therefore **not a shipped defect**, and no fix ships here.

`readings/ps402-2026-09-10/isolation.py` also drove the DoD #3 leak path — `proc.wait()` on the
leader BEFORE the teardown, the shape that blinds a live re-resolution — and it too left
**0 of 10**.

---

## WHAT SHIPPED, AND WHY NOT THE ARM

**No arm.** A `Check` reporting CANNOT_RUN (exit 2) on every run would put a name in
`SELECTED_CHECKS`/`EXPECTED_CHECKS` that can never certify anything and would red the lane
permanently. That is worse than the recorded gap. This is ticket bound #5 —
*"the scope note's premise is refuted; its CONCLUSION might still be defensible"* — arriving
with a measurement behind it, and both researcher comments anticipated it in terms
(*"a legitimate close"*, *"the five scope notes should still be corrected"*).

⛔ **`DOCUMENTED_OMISSIONS` stays `{}`** (AC6). No check exists to omit, because none is added,
so **both lane constants are byte-unchanged**. That is the strictest available state, not a
carve-out — and re-growing the mapping in the commit after PS-383 emptied it is the specific
move the ticket forbids.

⛔ **`_survivor_profile`, `_launch_and_grow`, `_sweep_group`, every threshold and the whole
chromium arm are byte-unchanged** (AC4). §4b's trap was never reached: the helper is not reused
because no firefox arm is added, so no false `CANNOT_RUN` can be manufactured from its
chromium-only socket guard.

**All five scope notes corrected** (AC7), plus the PASS-message disclaimer re-derived, plus the
real gap recorded in `UNCOVERED_SURFACES` with the measured reason **and the cure**: a firefox
survivor gate must be anchored on the engine's **own session** (`os.getsid` of the engine
leader), which is a different instrument, not a second profile. That is the follow-up slice this
reading specifies.

⭐ **AND IT EXPLAINS THE STAND-IN GAP MORE SHARPLY THAN THE TICKET COULD.**
`test_the_firefox_fork_path_leaves_no_survivor_once_its_leader_is_reaped`
(`tests/test_browser_process_group.py:651`) is a real, group-anchored, OS-level survivor
assertion and it **stays** (AC9). Its three `sys.executable` sleepers are spawned by the
stand-in `_child` **without `setsid`**, so they ARE in the recorded group — the one structural
property a real Gecko tree does not share. So the two now cover:

* **the unit test** — that the fork path's *own* group is recorded and reaped, on a tree that
  stays in that group. Fast, no engine, no DISPLAY. **Not a duplicate of anything.**
* **this reading** — that a real engine tree *leaves* that group, which is why no
  group-anchored gate can observe it, and what would.

---

## BOUNDS

1. ⛔ **NOT an Invariant #0 leak and none is claimed.** Nothing a page observes changes; no host
   fact escapes; no spoof is weakened. The session/survivor/orphan vocabulary invites a drift
   upward that is not warranted here.
2. ⛔ **NOT "firefox teardown is broken."** It is correct on every path the product takes, by two
   mechanisms, both isolated above. **The gate cannot SEE it.**
3. ⛔ **NOT "nothing tests firefox teardown."** See AC9 above — the mechanism is guarded by a real
   assertion; the gap is the stand-in, and §4 says exactly which property the stand-in does not
   reproduce.
4. **This ships no fix and closes no leak.** It corrects five false sentences and records a
   measured gap that was previously recorded as a vacuous one.
5. **n=8 launches, one host, one container, Linux fork path only.** The group/session split
   reproduces on all 8; the SIGKILL leak at n=2; the falsification's zero at n=2. Two other
   platforms and the thread arm (`in_process=True`) stay out of scope, as the ticket requires.
6. **The spoof-failure lines** in each run's output (`outer-size spoof FAILED … cannot switch to
   a different thread`) are a property of driving `spawn_browser` from a probe rather than from
   the launcher, and are unrelated to teardown. They are reported rather than filtered, but no
   claim rests on them.

## FILES

| file | what it measures |
|---|---|
| `probe.py` | the settle curve against the gate's own three constants (§2) |
| `tree_vs_group.py` | group membership vs. the real tree, per-pid `pgid`/`sid` (§1) |
| `decisive.py` | is the tree in the group; does SIGKILL-on-leader reach it |
| `falsifiability.py` | the pre-PS-192 sabotage verbatim, n=2 (§3) |
| `isolation.py` | the cascade control (freeze the node driver); the DoD #3 leak path |
| `mechanism.py` | ⭐ SIGTERM vs SIGKILL on a wedged tree — the isolation (§4) |
| `product_teardown.py` | the shipped `terminate()`, wedged and quiet (§5) |
