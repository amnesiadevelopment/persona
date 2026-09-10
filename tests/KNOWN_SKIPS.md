# Known skips — the measured position

A test that fails is a message. A test that silently declines to run is the
*absence* of a message, indefinitely, and it looks exactly like success.

This file is the baseline that makes the next unexpected skip legible. Without
it, the run's new skip-reporting just produces a list nobody can rank: you
cannot tell "that one always skips, it needs Windows" from "that one used to
run here and stopped".

**This is not a number to drive down.** A test that correctly skips because a
contributor does not have Node is working as designed. Reducing the count by
making a skip silent again is the exact defect this record exists to prevent.

## How to read a run

Skip reasons print on every run — no flag, nothing to know:

```
$ python -m pytest -q
...
SKIPPED [2] tests/test_ff_language_override.py:512: playwright not installed
```

To make a skip *consequential* on a machine provisioned to run the thing, see
[Declaring capabilities](#declaring-capabilities) below.

## Measured position

Measured on the CI-equivalent Linux container at `96e574b` with a clean tree:
**43 failed, 2377 passed, 17 skipped**.

> **This table is a reading of one environment, not the universal position.**
> It was measured on a container where `pip install .` had *not* been run. A
> machine that has installed the project sees a materially different set — an
> independent run during review, on a container carrying `playwright 1.61.0`
> and `invisible_core`, reported **0 failed, 14 skipped**, and there the
> `browser` skips come from the *launch* guard rather than the import guard.
> Read a difference from this table as "a different environment" first, and
> only then as a regression; what makes a skip legible is the reason it gives,
> which prints on every run, not this snapshot's arithmetic.

> The 43 failures are this container missing `invisible_core`, `aiohttp` and
> `PIL` — they are the same before and after this change and are not caused by
> it. A machine that has run `pip install .` does not see them.

There are **21 skip guards across 12 test files** in `tests/` (9
`importorskip`, 12 `pytest.skip(`). Only the 16 sites below fired here — the
rest ran, which is itself worth knowing.

### Structural — a dependency this project does not require of a contributor

These are expected to skip on an ordinary developer machine, and expected to
skip in the current CI. Nothing is wrong when they do.

| Where | Reason given | Capability |
|---|---|---|
| `test_ff_language_override.py:512` (×2) | `playwright not installed` | `browser_firefox` |
| `test_ff_language_override.py:527` | `playwright not installed` | `browser_firefox` |
| `test_invisible_launch.py:718` | `could not import 'invisible_playwright'` | `engine` |
| `test_invisible_launch.py:2051` | `could not import 'invisible_core'` | `engine` |
| `test_invisible_launch.py:5203` | `could not import 'invisible_playwright'` | `engine` |
| `test_app_egress.py:1467` (×6) | `the engine driver is not installed in this environment (CI installs it; …)` | `engine` |
| `test_verify_engine_selection.py:68` | `the engine driver is not installed here, so persona's firefox engine cannot be inspected` | `engine` |
| `test_assets.py:40`, `:81` | `could not import 'PIL.Image'` | — (dev extra) |

> **The last two rows carry a wording constraint, not just a location.** Both
> pass `reason=` to `importorskip`, which REPLACES the wording this table's
> `Capability` column is matched from — so the reason must contain the stem
> `the engine driver is not installed` or it classifies as **nothing**, and a
> runner that declares `engine` prints *"ok engine: no test declined to run"*
> beside the skips. That has now happened twice (PS-371's macOS fence; PS-353
> wrote *"the **pinned** engine driver…"* here and six egress tests — three of
> them security-relevant routing assertions — went quiet). It is swept for by
> `tests/test_skip_visibility.py::TestACustomImportorskipReasonIsStillPoliced::test_every_engine_guard_in_this_repo_writes_a_reason_that_classifies`,
> so a third occurrence fails rather than passing silently.

The three `test_ff_language_override.py` skips are the ones that matter most.
They are the real-Firefox probes — the suite's strongest evidence, checked
against the only oracle that counts (SpiderMonkey itself), including the sweep
asserting that no function in the realm betrays itself by shape. **So far as
can be established they have never run in CI**, and the suite reported green
the whole time.

### Platform-bound — this OS cannot host the test

Correct everywhere except the named platform. Not a provisioning gap on Linux.

| Where | Reason given |
|---|---|
| `test_app_update.py:819` | live relaunch-bat run needs Windows + the .NET csc compiler |
| `test_invisible_launch.py:971` | exercises the real Windows PowerShell/WMI pid query path |
| `test_invisible_launch.py:1564` | exercises the real EnumWindows/ctypes enumeration path |
| `test_invisible_launch.py:3867` | exercises the real Toolhelp/PEB process scan |
| `test_update_verify.py:217`, `:238` | exercises the real Windows `apply_and_restart` `os._exit` path |
| `test_main_utf8_fs.py:76` | this platform's C locale still yields a UTF-8 filesystem encoding |
| `test_ps374_runtime_enable_guard.py:592`, `:735` | `POSIX shell script` — skips on **Windows only**; runs on ubuntu + macOS |
| `test_ps374_runtime_enable_guard.py:679` | `the BSD-sed shim is a POSIX shebang script; PATHEXT ignores it` — **Windows only** |

> ⚠️ The two `test_ps374_runtime_enable_guard.py` rows above — covering **three**
> skip sites (`:592`, `:735`, `:679`) — are the **inverse** of
> every other row here: they skip on Windows and run on Linux/macOS, so a Linux
> contributor never sees them skip. They model **BSD `sed`**, which macOS
> runners have and Windows runners never will — an extensionless `#!/bin/sh`
> shim is not even a PATHEXT candidate on Windows, so the shim would silently
> not engage. That was caught by its own positive control going red in CI
> rather than by review (PS-374), and skipping is the honest outcome: a
> `sed.bat` wrapper would test the wrapper. The platform-independent half —
> `test_no_shell_script_uses_the_gnu_only_in_place_sed_form` — still runs on
> all three.

### Environment-bound — a machine that is simply missing something

| Where | Reason given |
|---|---|
| `test_apply_restart.py:131`, `:136` | no real AppImage available |
| `test_ps341_engine_continuity_live.py` (×22) | `PS-341 evidence not present at readings/ps341-2026-09-07/…` |
| `test_browser_process_global_guards.py::test_the_pre_fix_fixture_is_the_real_historical_blob` | `the pre-fix revision 360c488… is not in this checkout (shallow clone or exported tree)` |

#### The PS-360 provenance guard, and the skip it deliberately REPLACED

⚠️ **Read this row as the INVERSE of the one it supersedes.** PS-360's gate
(`tests/test_browser_process_global_guards.py`) carries its strongest
falsification: run the guard-gate against the source *as it shipped before the
fix* and confirm it names the real historical defect, rather than one the test
planted itself. That test originally reached the pre-fix source with
`git show <sha>` — and **CI's `actions/checkout` is a shallow clone**, so on
every leg on every platform it skipped, permanently. The one test whose whole
claim is *"this gate would have caught the bug it was written for"* was the one
test CI never ran, and it looked exactly like success. That is this file's own
opening argument, arriving in the wild.

So the evidence is **committed**:
`tests/fixtures/ps360_pre_fix_launch_child.py.txt` holds `_child` and
`_launch_and_watch` verbatim at `360c488`, as a `.txt` so no collector, linter
or import machine touches it. **The falsification now runs everywhere,
including CI.**

What skips instead is the much narrower **provenance** check above: a committed
fixture *can* be doctored, and a falsification run against doctored evidence
proves nothing — so the fixture is re-extracted from git and compared byte for
byte whenever git can reach the revision. That is every full clone, including
every developer machine; it is not a shallow CI checkout. **A skip here means
"the fixture's authenticity was not re-verified on this run", never "the
falsification did not run".**

#### The PS-341 guard, and why it is shaped that way

`tests/test_ps341_engine_continuity_live.py` re-reads a **committed reading**
(`readings/ps341-2026-09-07/`) rather than re-running its own two-build
download on every suite run — a real revert costs two ~190 MB engine
transfers, a display, and several minutes.

So its skip guard is `_load()`, which skips when the evidence file is absent
instead of failing. **That is the honest outcome for a checkout without the
reading, and it is exactly the shape this file exists to keep legible**: the
evidence ships in the repo, so on an ordinary checkout these do NOT skip —
they run and are cheap. A skip here means the reading was *removed*, which is
a change of state worth noticing, not a routine absence.

Two tests deliberately sit OUTSIDE that guard and run everywhere, because
neither needs the engine or the reading — they police the recorded position
itself:

* `test_the_position_is_recorded_beside_the_chromium_arm` — reads
  `process.py`, and goes red if PS-341's recorded no-migration-needed position
  is deleted (the premise-1 grep would then find nothing again);
* `test_persona_stands_down_on_the_arm_where_the_pair_moved` — reads
  `gpu_ext.py`, and goes red if persona starts authoring the WebGL pair on the
  windows arm, which would invalidate the recorded finding's explanation.

A **third** joined them: `test_the_recorded_position_does_not_overclaim_a_live
_theme_reading`, which also reads only `process.py`. It pins the split between
the readings taken **live** (search engine, bookmarks, cookies) and those taken
**on disk only** (theme, dark mode). It sits outside the guard for the same
reason as the other two — the failure it guards against was never in the
reading, it was in the *sentence about* the reading, so hiding the evidence must
not take the guard with it.

⚠️ **A third skip guard was added, and it is a DIFFERENT file from the two
above:** `control-dark-mode.json`, the follow-up control for the one unexplained
number in the reading (`page.dark`). Two tests read the evidence guard and one
reads this one, so a checkout missing only the control skips exactly one test:

| test | skip reason |
|---|---|
| `test_the_dark_control_actually_falsifies_the_obvious_explanation` | `PS-341 dark-mode control not present at readings/ps341-2026-09-07/control-dark-mode.json` |

Unlike the main reading, this control needs **no engine and no display** —
stock `chromium`, headless, four fresh profiles — so it is cheap to regenerate:
`python3 scripts/ps341_dark_control.py --out readings/ps341-2026-09-07/control-dark-mode.json`.
It is committed anyway, for the same reason the reading is: a control that is
only *describable* is not a control.

That split is the point: hiding the reading must not silently take the
position's own guards with it. Verified by removing the directory — **3 passed,
23 skipped**, never a green 26.

⚠️ **`23` there is the file's TOTAL skip count, and the `(×22)` on line 90 is
the count for the ONE reason written beside it** — the two numbers differ by
exactly the dark-mode control test, which skips for the *other* reason and is
already accounted for in its own table above. They are not the same figure and
neither is a typo for the other.

Every number in the two paragraphs above is **measured, not asserted** — each
was re-taken by actually removing the file and running the module, on the tree
these words ship in (a docs-only change over `4ca4c97`, so nothing in this
commit can have moved them; both figures were re-taken *after* the edit
anyway). If you change the test count, re-take them the same way rather than
adjusting them by arithmetic: the totals move for reasons the diff does not
show (a `parametrize` list changing length moves the skip count by ten without
adding a test).

**The measurement itself needs `browser_chromium`**, the capability nothing
provisions today (see below). Re-running it needs a real fingerprint-chromium,
a second published engine build to revert to, a display, and — measured on this
container — a PID budget the harness does not exhaust: leaked browser trees
took 868 of 2048 PIDs and made the *next* launch die with
`pthread_create: Resource temporarily unavailable`, which reads exactly like
the engine refusing a profile. `scripts/ps341_run.py` reaps process **groups**
for that reason.

### Opt-in — deliberately not run unless asked for

Neither a provisioning gap nor a platform bound: the machine is fully capable
and the test is expensive enough that running it on every PR would not pay.
Distinguished from every other section here because there is **nothing to
provision** — the remedy is an environment variable, not an install.

| Where | Reason given | How to run it |
|---|---|---|
| `test_unclean_exit_survivors.py` (real-wrap pid reuse) | `exhausts the whole pid space (~8 min, measured): set PERSONA_PID_WRAP_TEST=1 to run the real-wrap pid-reuse test` | `PERSONA_PID_WRAP_TEST=1 python -m pytest tests/test_unclean_exit_survivors.py` |

⚠️ **Read a skip here as UNMEASURED, never as measured-and-fine.** The property
it checks — that the create-time discriminator survives the OS genuinely
handing a recorded pid back to a different process — *is* covered on every run
by the fast discriminator test in the same file. What only the opt-in test
covers is whether our idea of pid reuse matches the kernel's. It has been run
to completion on this container and **passed**: `pid_max` 4,194,304, the
allocator wrapped at t=+487s, a fork landed on the exact recorded pid at
t=+490s, the product's probe answered `GONE`, and the test reported
`1 passed in 515.75s`.

Deliberately carries **no capability**, so declaring
`PERSONA_REQUIRED_CAPABILITIES=browser` does not turn this skip into a failure.
A capability declares "this machine is provisioned for X and a skip is
therefore a fault"; that is the wrong shape for a test whose skip is a
deliberate cost decision on a machine that could run it perfectly well.

### Guards that did NOT fire here

Worth recording, because a *future* skip from one of these is a change of
state rather than the status quo:

* the **node** probes (`native_mask_probe.py:141`, the `realms` fixture in
  `test_worker_wrap.py`, `test_ff_language_override.py:333`,
  `test_gpu_ext.py:529`/`:974`, `test_title_ext.py:251`,
  `test_canvas_ctx_ext.py:155`/`:355`) — `node` is on PATH here, so all of
  these ran;
* `test_geo_disproven_refusal.py:575` (`flet`) — installed here;
* `test_cert_terminator.py:580` (running as root) and
  `test_peer_auth.py:49` (`SO_PEERCRED`) — neither condition held.

## PyYAML — the guards that had no name until PS-389

⚠️ **This section records a skip class that does NOT fire today, anywhere.**
It is here because that is precisely the state this file exists to make
legible: "that one always skips" and "that one used to run here and stopped"
are different sentences, and the second is unreadable without the first
written down first.

### What it covers

Every **workflow-shape** test in this repo parses its subject with PyYAML.
Measured at `9dad467` with a `sys.meta_path` blocker raising a genuine
`ModuleNotFoundError` — ⚠️ **not** an `ImportError` stub, which takes a
different path and produces *errors* rather than skips — over the 16 test files
that guard on yaml:

| PyYAML | declaration in force | outcome |
|---|---|---|
| present | — | **660 passed, 0 skipped** |
| absent | *nothing declared* | **89 silently skipped** |
| absent | `browser,engine` — ci.yml's OWN declaration, verbatim | **89 silently skipped** — *byte-identical to declaring nothing* |
| absent | `browser,engine,yaml` — ci.yml's declaration **after** PS-389 | **89 FAILED / errored**, each naming PyYAML and how to supply it |

⚠️ **The 89 counts SKIPS only.** A further **222** tests are lost at
*collection* to the 8 module-level guards — see the section on that hole
below, which the capability alone could not reach.

That last row is the defect PS-389 closed. The full declaration bought nothing,
because the capability table had no name for PyYAML.

Weighted per file (`pytest -rs` collapses same-reason skips into
`SKIPPED [N] file:line`, so these figures multiply by `[N]`):

| file | dark tests |
|---|---|
| `test_ci_verification_gates.py` | **37** |
| `test_ps336_launch_behaviour_venue.py` | 13 |
| `test_ps372_firefox_major_watch.py` | 11 |
| `test_ps370_published_engine_gate.py` | 9 |
| `test_ps375_engine_continuity_gate.py` | 9 |
| `test_ps315_behaviour_gate.py` | 6 |
| `test_protocol_conformance_gate.py` | 4 |
| **TOTAL** | **89** |

⭐ **Read the top row as recursive, because that is the whole argument.** The
37 are not ordinary tests. They include, by name,
`test_ci_declares_the_browser_capability_rather_than_inferring_it` and
`test_ci_declares_the_engine_capability_on_every_platform` — the guards
asserting that the OTHER capability declarations in this file's table exist.
Without PyYAML the mechanism stops policing its own shape, and reports green
while doing it.

`tests/test_ps370_published_engine_gate.py::test_the_selftest_installs_what_this_suite_needs_to_not_skip`
was the sharpest case: it takes a fixture that `importorskip`s yaml, so it
skipped in **precisely the condition it was written to catch**.

### Three guard shapes, and the wording is what the table matches on

| shape | wording produced | count |
|---|---|---|
| `pytest.importorskip("yaml")` | `could not import 'yaml': No module named 'yaml'` | 78 |
| `pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")` | that reason, verbatim | 8 module-level guards |
| a fixture-level bare `pytest.skip("PyYAML is needed to parse the workflow")` | the same | 11 (`test_ps372_firefox_major_watch.py:706`) |

⚠️ **The first two strings share no substring**, so one pattern cannot reach
both — `conftest.py`'s `yaml` entry carries **two** stems for that reason. An
entry written against `importorskip`'s default wording alone would have left 11
tests dark **and reported success**, which is this file's own subject matter
re-created inside the fix for it.

The third shape is deliberately not an `importorskip`: at module level that
raises during *collection* and skips the entire file, which
`test_ps372_firefox_major_watch.py` documents having measured (`1 skipped` for
a whole file, green). It needs no pattern of its own — the table matches on the
reason **text**, not on the call that wrote it.

### Two shapes that are already loud, and are NOT in the 89

Worth recording so a future reader does not go looking for them:

* `test_engine_autoupdate_workflow.py` and `test_release_fingerprint_baseline.py`
  use a bare module-level `import yaml`, which is a **collection error**, not a
  skip. Loud already.
* `test_ps342_chromium_watch.py` imports yaml *inside* two tests, which
  **fails** them. Loud already.

### ⭐ The hole the capability alone could not reach — 222 more tests

⚠️ **Found by running the fix rather than by reading it, and it would have made
PS-389 a half-fix that reported success.**

`conftest.py`'s declaration was wired to `pytest_runtest_makereport`, which
sees a skip that happened while **running** a test — a guard in the body, or in
a fixture it takes. A **module-level** `importorskip` never gets that far: it
raises during **collection**, the whole file is dropped, and no test item is
ever created for that hook to be called with.

Eight of the yaml guards are module-level. With the capability declared and
PyYAML absent, they reported this, verbatim:

```
ok yaml: no test declined to run
SKIPPED [1] tests/test_ps306_toolchain_retry.py:54: PyYAML is needed to parse the workflow
SKIPPED [1] tests/test_ci_shard_partition.py:47: PyYAML is needed to parse the workflow
```

**222 tests vanished** (18 + 34 + 27 + 19 + 38 + 40 + 7 + 39) **and the summary
printed a green `ok` line about the exact capability that had just failed.**
That is strictly worse than having no capability: a reader is reassured rather
than merely uninformed.

`conftest.py` now carries a `pytest_collectreport` hook as a **second entry
point** — not a second opinion. It reads the same table every other path reads,
so it is **capability-blind**: any module-level guard, for any capability, in
any file written from now on, is covered without anyone remembering to wire it.
The engine and browser guards are all fixture-level today, so this changes
nothing for them — which is the point. A file that moves its guard to module
level tomorrow does not thereby escape the declaration.

It names the **file**, not test ids: there are none to name, they were never
created. Inventing per-test ids for items that do not exist would be a
fabricated precision. Pinned by
`tests/test_skip_visibility.py::TestAModuleLevelGuardIsPolicedToo`, in both
directions — a module skip that classifies as **nothing** (a platform-bound
guard, say) stays an honest skip on a fully-declared run.

### The position

**A `yaml` capability now exists and `ci.yml` declares it, on both copies of
the declaration** (the shard matrix and the `||` fallback macOS and Windows
actually receive). PyYAML is declared in `requirements-dev.txt`, which the
tests job installs before pytest runs.

⛔ **Those two are one change and must stay together.** Declaring the
capability alone converts 89 silent skips into 89 red ones on any runner that
fails to supply PyYAML — honest, but a gate failing for want of provisioning
rather than for want of correctness, which is the mistake `browser_chromium` is
deliberately left out of the umbrella to avoid. Declaring the dependency alone
re-creates the original defect one level up: a declared dep that fails to
install still skips *silently*, with a requirements file as its new hiding
place. Both are pinned by tests
(`tests/test_skip_visibility.py::TestThePyYAMLGuardsAreReachableByADeclaration`,
`tests/test_ci_verification_gates.py::test_ci_declares_the_yaml_capability_on_every_platform`).

**Blast radius: nil, measured.** With PyYAML present the 16 guarding files
report 660 passed and **zero** skips, so this declaration converts nothing that
currently happens. It polices a state that does not currently occur — which is
what a guard is for.

### What is deliberately unchanged

* `release.yml` runs the full suite and declares **no** capabilities at all.
  It is untouched: this mechanism polices what is declared, and that job
  declares nothing.
* `chromium-upstream-watch.yml:134`, `firefox-major-watch.yml:131`,
  `published-engine-verdict.yml:208` and `engine-continuity.yml:332` install
  PyYAML **by name** at their own pip line and run a single named test file
  with no declaration. Those hand-rolled installs stay: two of them are
  *asserted by name* by the very tests they enable
  (`test_ps342_chromium_watch.py`, `test_ps372_firefox_major_watch.py`), and
  they are the control this change was measured against — not duplication to
  be tidied away. Three of five workflows learning this by hand, each after
  being bitten, is the frequency argument for naming the class, not a
  refutation of it.
* `tests/test_skip_visibility.py`'s AST sweep is **still scoped to the engine**.
  Widening it to yaml is a separate judgement nobody has made; adding a
  capability *in order to* green that sweep is named there as the reverse
  defect, and this is not that. Its scope note was updated in the same commit,
  because the example it used to give — "the `PyYAML` guards name no capability
  because none is declared for them" — is now false.

## Which guard fires in CI — measured, not reasoned

The browser probes are guarded **twice**, and it matters which one fires,
because it decides what a future CI slice has to provision:

```python
sync_playwright = pytest.importorskip(              # the import guard
    "playwright.sync_api", reason="playwright not installed").sync_playwright
...
except Exception as exc:
    pytest.skip(f"firefox not runnable here: {exc}")   # the launch guard
```

`pyproject.toml` pins the engine as `invisible_playwright` from a git ref, and
that package is a playwright fork — so whether it satisfies
`import playwright.sync_api` was an open question. Measured at the pinned
commit `353df4f`:

* it ships **no** `playwright` package of its own (only `src/invisible_playwright`);
* it **hard-depends on upstream** `playwright>=1.55,<=1.61.0`.

Confirmed by installing that range in a clean venv: `import playwright.sync_api`
succeeds, and `p.firefox.launch()` then fails with
`Executable doesn't exist at .../firefox-1532/firefox/firefox`.

**Therefore: wherever `pip install .` has run — which includes the release
pipeline — the import guard PASSES and the LAUNCH guard is the one that fires.**
The pip package is not the missing piece; the **browser binary** is. A CI slice
that provisions only the Python package will not make these probes run, and
will still report green.

(The `playwright not installed` reason in the table above is this bare
container, where `pip install .` has not run. It is not what CI sees.)

## Declaring capabilities

A skip is honest on a laptop and dishonest on a machine provisioned to run the
thing. An environment says what it supports:

```bash
PERSONA_REQUIRED_CAPABILITIES=browser python -m pytest      # or --require-capability browser
```

In that environment, a skip of the browser probes becomes a **failure** naming
what was missing and how to provision it. Declaring nothing changes nothing:
an ordinary developer run still skips and still passes.

Capabilities: `browser`, `browser_firefox`, `browser_chromium`, `node`,
`engine`, `ui_driver` (see `conftest.py`). Multiple are comma- or
space-separated. A name that is not one of these is a hard error, not a silent
no-op — a typo that quietly disabled the guard would be the original defect
wearing a new hat.

### `browser` is an umbrella, and what it leaves out is the point

`browser` used to mean, verbatim, "a real Firefox the playwright API can
launch" — one name for one engine, with no way to say anything about the other.
It is now an UMBRELLA over `browser_firefox`, so it declares exactly what it
declared before: `PERSONA_REQUIRED_CAPABILITIES=browser` and every existing
`@pytest.mark.requires_capability("browser")` police the Firefox probes and
nothing new.

What the split buys is that the table can now SAY what is missing.
**`browser_chromium` is deliberately not under the umbrella.** Chromium is not
the secondary engine — `src/services/profile/coherence.py:78` reads
`DEFAULT_ENGINE = "chromium"`, and it is the engine every impossible
os_type/engine pair is reconciled toward (`:99`, `:162`). The engine the
product defaults to is the one no gate ever launches. What rides on it:
`src/services/browser/process.py` returns early for firefox (`:353-356`) before
all 13 `extensions.append` calls — audio, WebGL, GPU, device, voice, locale,
mobile, native-cloak, stealth, canvas-ctx, measuretext, search, geo. None is
exercised by any gate on any platform.

Every declared run now PRINTS that gap next to the green line it qualifies, so
"ok browser" can never again be read as "both engines are covered". Folding
chromium into the umbrella instead would turn every declaring job red for want
of PROVISIONING rather than for want of correctness — a gate that fails for the
wrong reason teaches its reader to ignore it.

**This ships no new coverage, and the distinction is deliberate.** It converts
a silent gap into a declared one, which is genuinely less than "chromium is now
tested". Provisioning that engine is a separate, larger question: it is NOT
`python -m playwright install chromium` (the product launches
fingerprint-chromium, not playwright's build) — the binary arrives via the
`download_engine` route `.github/workflows/engine-autoupdate.yml:104-115` uses
against a pinned baseline tag, so which build, which pin and which runner are
all open. `browser_chromium` is opt-in and declaring it today would be a
promise no machine can keep.

**Nothing infers support from the presence of the thing being checked.** A
guard that reasoned "playwright imported, therefore this machine should run
browser tests" would conclude "not supported here" on exactly the machine where
support broke — the one case that has to be loud. The only input is the
operator's declaration; `tests/test_skip_visibility.py` enforces that
structurally.

## Not yet wired

**Firefox is now provisioned in CI** — `.github/workflows/ci.yml:152` runs
`python -m playwright install firefox` and `:364` declares
`PERSONA_REQUIRED_CAPABILITIES: browser`, so the provisioning cannot silently
rot back to skipping. (The paragraph that used to sit here called that a
follow-up living entirely in `.github/workflows/`; it has since landed.)

**The chromium engine is not, and that is the open one.** See the umbrella
section above: `browser_chromium` exists as a NAMED capability precisely so the
gap is attributable rather than invisible, but nothing provisions it and the
umbrella deliberately does not cover it. Closing it is a separate slice —
which build, which pin, which runner — and until then declaring
`browser_chromium` is a promise no machine can keep.

When those probes first run for real, `tells` may come back non-empty. That is
a genuine finding and belongs to the masking direction as its own ticket —
it is not a reason to adjust the assertion.
