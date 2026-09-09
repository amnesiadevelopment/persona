# PS-371 — Is the `invisible_core` pin actually fenced against a macOS-killing bump?

**Date:** 2026-09-09 · **Tree:** `main` @ `ff7b644` · **Seat:** worker

The brief asked for a measurement first and a guard only where a gap was
demonstrated, and explicitly admitted "nothing to add, and here is why" as a
delivered outcome. Three of the four candidates need nothing, and this report
says so with the evidence. The fourth was real and is closed.

---

## §0 — Summary

| # | Candidate | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Does `importorskip` leave the fence dark on a CI leg? | **NO, today** — the driver installs on all three | §1 |
| 1b | Would anyone be TOLD if it went dark? | **NO — this was the real gap. Closed.** | §4 |
| 2 | Does the fence run on a merge-gating shard? | **YES, on all three OSes** | §2 |
| 3 | Is the pin string itself asserted anywhere? | **No, and it correctly needs nothing** | §3 |

The existing fence is good and was **not** duplicated. Nothing was added that
re-implements it.

---

## §1 — Candidate 1: the driver is present on all three CI platforms

The fence opens with
`pytest.importorskip("invisible_core", reason="the engine driver is not installed in this environment")`,
and a skipped test is green. So: does it actually skip anywhere?

**Measured against run `34156128433`** — the last green `push` to `main`
(2026-09-07), which runs the full six-job matrix.

Every leg installs the pinned driver:

```
tests (ubuntu-24.04,  main): Successfully installed ... invisible_core-20.14.0 ...
tests (macos-latest,  main): Successfully installed ... invisible_core-20.14.0 ...
tests (windows-latest,main): Successfully installed ... invisible_core-20.14.0 ...
```

And no leg skips the fence. Counting the fence's own skip reason across all six
job logs:

```
fence-skip lines, ubuntu / macos / windows / ub-ui / mac-ui / win-ui:  0 0 0 0 0 0
```

⭐ **That zero is only meaningful because skip reasons ARE printed.** `addopts =
"-rsfE"` in `pyproject.toml` makes every run list its skips with reasons.
Positive control on the same logs, so the probe is known to discriminate:

```
SKIPPED-reason lines present:  ubuntu 50 · macos 109 · windows 227
```

416 skip lines were harvested and every one was re-classified through
`conftest.py`'s capability table. **Zero mention the engine packages.**

> **Verdict:** the fence is LIVE on all three platforms today. It is not dark
> anywhere, and candidate 1 as literally stated needs no code. But see §4 —
> asking this question surfaced the real defect one level down.

---

## §2 — Candidate 2: the fence gates a merge

- It carries **no** `ui_driver` marker, so it lands in the `main` shard
  (`-m "not ui_driver"`). Confirmed by collection: `8 tests collected` under
  that marker expression.
- The `tests` job runs on `[ubuntu-24.04, windows-latest, macos-latest]` for
  `pull_request` **and** `push` to `main`.
- Neither shard is optional, and `tests/test_ci_shard_partition.py` +
  `tests/test_ci_verification_gates.py` already forbid `continue-on-error`
  anywhere in the workflow.
- It also runs in `release.yml`'s `tests` job, which every `build-*` job
  declares in `needs:`.

> **Verdict:** it gates. Nothing to add.

---

## §3 — Candidate 3: the pin string is not separately asserted, and should not be

The brief suspected this may need nothing, and it is right. **Proved rather
than reasoned about** — the fence was run against four real driver versions:

| installed `invisible_core` | fence result |
|---|---|
| `20.14.0` (the pin) | **8 passed** |
| `20.15.0` | **8 passed** |
| `20.16.0` (first version with the macOS removal) | **1 failed**, 7 passed |
| `29.19.0` (what a bare `>=` resolves to TODAY) | **1 failed**, 7 passed |

The failure names the real refusal site rather than a version number:

```
AssertionError: persona's release workflow builds for 'darwin', but the pinned
invisible_core does not support it.
  invisible_core.make_virtual_display() raises on 'darwin' at LAUNCH:
  RuntimeError: invisible_playwright supporta Windows e Linux
  (macOS non e' piu' supportato; got 'darwin')
```

The last row is the whole answer to candidate 3. Relaxing `==20.14.0` to
`>=20.14.0` resolves `29.19.0`, and the fence goes **red** — so a separate
assertion that the file still reads `==` would be a **second guard over one
property**, which the brief forbids and which is exactly how the weaker guard
becomes the one people trust.

> **Verdict:** the fence already covers it. **Nothing added.**

⭐ This also re-confirms the fence is falsifiable without touching it: the guard
that holds macOS is proved capable of failing on real driver builds.

---

## §4 — THE REAL GAP: the fence could go dark *silently*

§1 answers "is it dark **today**?" — no. The question one level down is: **if
it ever went dark, would anyone be told?** This project already owns a
mechanism for exactly that question (PS-58's `PERSONA_REQUIRED_CAPABILITIES`:
an environment that DECLARES it supplies something turns a skip of that thing
into a FAILURE). The answer was **no**, for two independent reasons.

### 4a — The fence's skip reason classified as *nothing*

`pytest.importorskip(mod, reason=...)` **replaces** importorskip's own wording,
and `conftest.py` classifies skips **by** that wording. Measured:

```
'the engine driver is not installed in this environment'   -> classified as []
"could not import 'invisible_core': No module named ..."   -> classified as ['engine']
```

⚠️ **Both spellings sit in the SAME FILE**, and the important one was the
unclassified one: the fence uses the custom-reason form, while
`test_the_probe_can_actually_fail` beside it uses the bare form. A guard written
*more helpfully* had thereby become *invisible*.

### 4b — CI never declared `engine` anyway

`ci.yml` declared `browser` (and `ui_driver` on the ubuntu ui-driver shard).
`expand_capabilities(["browser"])` is `["browser", "browser_firefox"]` — the
engine packages are **not** in that umbrella. So even the correctly-classified
sibling was unpoliced.

**Both halves were needed.** Either alone enforces nothing, and each is pinned
by its own test.

### What was changed

1. `conftest.py` — the `engine` capability gains the reason stem
   `"the engine driver is not installed"` (the environment-independent stem,
   deliberately not the fence's full sentence, so the next guard that appends
   its own detail still classifies).
2. `.github/workflows/ci.yml` — both shards declare `engine`, **and so does the
   non-ubuntu fallback literal**. ⚠️ That fallback is a second copy: the
   `matrix.shard.capabilities` value never reaches macOS or Windows, so a
   capability added only to the matrix would be declared on **one** of three
   platforms — silently absent from the very OS this fence exists to protect.

### Falsification of the new guard (the brief requires this)

Each half was reverted independently and the guards were observed **red**:

| mutant | result |
|---|---|
| pattern removed from `conftest.py` | `2 failed` (`..._fails_where_the_engine_is_declared`, `..._is_the_stem_not_one_guards_exact_sentence`) |
| `engine` removed from matrix shards only | `1 failed` (`test_ci_declares_the_engine_capability_on_every_platform`) |
| `engine` removed from fallback literal only | `1 failed` (same test) |
| **fallback changed to `browser` with the old value left in a COMMENT above it** | **`1 failed`** (same test, reporting `declares 'browser'`) — see §4b |
| restored | all pass |

End-to-end, driver absent + CI's real declaration — the fence now **fails
loudly** instead of skipping green:

```
FAILED engine: 4 test(s) skipped in an environment that declares the
  invisible_playwright / invisible_core engine packages:
    tests/test_engine_driver_platform_support.py::test_pinned_driver_supports_every_os_we_ship[darwin]
    ...
4 failed, 4 passed
```

### Blast radius — measured, not reasoned about

| control | expected | observed |
|---|---|---|
| driver present + `browser,engine` | green | **8 passed**, `ok engine` |
| driver at `20.16.0` + `browser,engine` | red for **macOS**, not for absence | **1 failed** (`[darwin]`), `ok engine` |
| no declaration, no driver (contributor laptop) | green | **4 passed, 4 skipped** |
| all 416 real CI skip lines re-classified | 0 new failures | **0** |

The last row is the important one: this polices a state that **does not
currently occur**, which is what a guard is for. It cannot turn today's CI red.

---

## §4b — A defect the review found in THIS guard, and the one-line fix

⚠️ **Round 1's fallback assertion could be defeated by a comment, and reported
green.** Found by the reviewer on `c45b215`; reproduced here before fixing.

The assertion scanned the **raw** `ci_text`, and `re.search` takes the FIRST
match anywhere in the file. The directive it looks for sits directly beneath a
6-line comment explaining that this value is a second copy which must be kept
in step — so the most likely way the drift actually happens is a maintainer
editing the directive and leaving the old value behind in a comment:

```yaml
          # was: PERSONA_REQUIRED_CAPABILITIES: ${{ ... || 'browser,engine' }}
          PERSONA_REQUIRED_CAPABILITIES: ${{ matrix.os == 'ubuntu-24.04' && matrix.shard.capabilities || 'browser' }}
```

Against raw text the assertion read the **comment's** `browser,engine`, passed,
and reported fine — while macOS and Windows were handed `browser` and the fence
went unpoliced on exactly the OS this guard exists to protect. Measured on the
mutant before the fix: **`1 passed`**.

⭐ **This is the ticket's own defect class, one level up** — a guard that
declines to fire and reads as green. It is also the *same miss this file has
already recorded making once*:
`test_ci_states_the_measured_floor_for_every_platform` documents an earlier
version asserting `"2446" in ci_text`, which the surrounding narrative also
contained, and which mutation-testing did not catch.

**The fix is the helper the repo already wrote for this.** `_effective_lines`
strips YAML comments — *"scan what the runner would execute, not the prose
about it"* — and every other `ci.yml` content assertion in this file already
routes through it. This one was the exception; it no longer is.

Verified in **both** directions, because a guard that fires on everything is as
useless as one that fires on nothing:

| direction | expected | observed |
|---|---|---|
| commented mutant (above) | **red** — caught | **`1 failed`**, reporting `declares 'browser'` |
| shipped tree, unmodified | green — no false red | **`1 passed`** |

The `shard.capabilities` half of the same test needed no change: it reads
`ci_yaml`, so comments cannot reach it.

---

## §5 — Traps, honoured

- ⛔ **No second guard over the macOS property.** The fence is untouched; not
  one line of `tests/test_engine_driver_platform_support.py` was edited. What
  was added is one level *away* — a guard that the fence is *running*, which is
  a different property from the one the fence asserts.
- ⛔ **`build-macos` not touched.** No workflow job was removed.
- ⛔ **The pin was not lowered.** `pyproject.toml` is unmodified.
- ✅ **The new guard is proved capable of failing** (four mutants above,
  including the comment-defeat mutant the review found — §4b).

## §5b — A near-miss caught in this session, worth recording

The first draft of the new sandbox tests named the **real** package,
`pytest.importorskip("invisible_core", reason=...)`. That passed in this
container — where the driver is absent — and **failed under a driver-installed
interpreter**, which is what CI actually is: the import would succeed, no skip
would occur, and the `assert False` guard fired.

⚠️ **This is the ticket's own defect class pointed at the ticket's own work.**
A test about how a skip is CLASSIFIED had quietly become a test of what happened
to be installed on the machine running it. It was caught only by re-running the
new tests under a second interpreter with `invisible_core==20.14.0` present,
rather than trusting the one environment at hand.

Fixed by making the module unimportable in **every** environment — the
custom-reason case names a module that cannot exist (the reason string is what
the classifier reads, so the package name is irrelevant to the claim), and the
bare case imports a non-existent **submodule** of the real package, so
`importorskip` still writes `could not import 'invisible_core...` whether or
not the real package is installed.

Both spellings, both mutants, both environments:

| | with driver | without driver |
|---|---|---|
| fixed tests | **5 passed** | **5 passed** |
| pattern removed (mutant) | **2 failed** | **2 failed** |

## §6 — What this does NOT claim

- It does not claim the driver **will** install on every future runner. It
  claims that if it stops, CI says so instead of going quietly green.
- The `test_the_shards_do_not_overlap` / `..._cover_every_test_in_the_suite`
  failures seen while running locally are **pre-existing environment artifacts**
  of this container (87 collection errors from absent optional deps). Verified
  by `git stash` — byte-identical failure on the unmodified tree.
