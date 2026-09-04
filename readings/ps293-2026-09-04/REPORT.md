# PS-293 — what actually stops an operator reaching a newer engine, per engine

**Date:** 2026-09-04 · **Tree:** `origin/main` `18e30eb` · **Author:** worker seat
**Method:** the shipped code paths *executed*, not read — one live Chromium tick end
to end (real fetch, real 189 MB download, real install, all four deferral branches),
and the Firefox gate probed by **launching three real engine builds** under the
shipped driver and under the current one.

The ticket asks one question: *for each engine, can an operator running a released
persona actually end up on a newer build today — and where exactly does that stop?*
It permits, explicitly, the answer "nothing stops it". The two engines answer
**differently**, and neither answers the way the pre-rewrite ticket assumed.

---

## 0. Summary — the finding in five lines

1. **Chromium: NOTHING STOPS IT. The requirement is already met.** An operator
   running a released persona reaches a newer Chromium build unattended, with no
   persona release, on the hourly tick. Measured end to end on a real build. Every
   gate in the path is either satisfied by an ordinary upstream release or is a
   *deferral* that resolves itself on a later tick. (§1)
2. **Firefox: a persona release IS genuinely required, and the ticket's candidate
   reason is the wrong one.** The gate is not the asset names and not a version
   comparison. It is the **seal**: `invisible_core` is cryptographically paired to
   exactly one build by **BuildID**, and it refuses any other — including a build
   whose assets are named identically. (§2)
3. **And the pin is REAL, not bookkeeping.** Swapping the seal to let firefox-21
   through does not produce a working browser: the driver fails at
   `Browser.enable` with a juggler protocol error. The contract genuinely moved.
   So "persona release required" is a correct refusal, not a conservative one. (§2.4)
4. **The Chromium un-versioned tree costs a DEFERRAL, not a release.** The single
   in-place tree is why Chromium needs an in-use oracle that Firefox does not — but
   it is fully handled: the install waits for profiles to close and retries from
   bytes already on disk. It is a latency cost, not a gate. (§3)
5. **⛔ macOS: taking a newer Firefox engine today would produce a Mac with NO
   ENGINE, and every existing guard would stay green.** Independently reproduced,
   and it is *worse* than the ticket states — the platform refusal is now visible
   in the **currently-published driver**, not merely at firefox-21. (§4)

**Bottom line for the owner's requirement** (*«движки должны обновляться и
подставлять без обновлений персоны это обязательно»*): **half of it already holds
and half of it cannot hold today.** Chromium meets it now. Firefox cannot meet it
without a change in an upstream package persona does not own — and the one route
that *looks* like it would (ship the seal alongside the engine) is blocked by
macOS, which the same upstream removed.

---

## 1. Chromium — traced end to end, live. Nothing stops it.

`_check_engines_periodic` (`src/ui/app.py:3490`) sleeps 3600s, then calls
`engine.fetch_latest()` → `_record_engine_check(tag)` → `_auto_update_engine()`
(`app.py:4510`) → `_update_engine_async(unattended=True)` (`app.py:4827`) →
`updater.download_engine(..., defer_if_in_use=True)` (`updater.py:832`).

I ran that chain against the **real upstream release**, into a throwaway
`PERSONA_ENGINE_DIR`. Transcript: `artifacts/live_chromium.log`.

### 1.1 Every stop point in the tick, and what each did

| # | Gate | Code | Live result |
|---|---|---|---|
| 1 | `self._engine_busy` | `app.py:4551` | False — proceeds |
| 2 | `engine.is_installed()` | `app.py:4551` | upgrade-only guard; a cold machine goes to `_download_engine_fresh` instead |
| 3 | `engine.pinned_build()` | `app.py:2679` | `""` — no operator revert in force |
| 4 | `is_newer(latest, current)` | `app.py:2681` | True |
| 5 | `_engine_unverifiable_tag` | `app.py:2683` | not set — upstream publishes a digest for **every** matched asset (verified: all 5 assets carry `sha256:`) |
| 6 | **`engine_policy.is_installable`** | `policy.py:262` | **`ok`** — `KNOWN_BAD_VERSIONS` ships empty, `max_tested_major()` → `inf` |
| 7 | `_engine_tree_in_use()` | `app.py:4557` | cheap early exit only; a TOCTOU by construction, so not the guard |
| 8 | `httpdl.digest_missing(digest)` | `updater.py:897` | digest present — no `EngineUnverifiable` |
| 9 | download + sha256 verify | `updater.py:934` | **188,811,768 B fetched and verified, 1s** |
| 10 | **`_engine_in_use()` under `_install_lock`** | `updater.py:938` | the binding guard — see §1.2 |
| 11 | `_install_linux` / `_install_windows` / `_install_macos` | `updater.py:583+` | atomic replace — **succeeded** |

Live, on tag `148.0.7778.215`:

```
fetch_latest_checked -> tag=148.0.7778.215 verdict=ok msg=''
policy.check(...)    -> ('ok', '')  is_installable=True
cold download_engine -> True (1s)
is_installed         -> True   current_version='148.0.7778.215'
```

Then, with `version.txt` forced back to a lower version to make the tick see a
genuine upgrade:

```
forced current_version -> '100.0.0.0'; is_newer(148.0.7778.215) = True
idle unattended download_engine -> True   (asset already on disk)
FINAL current_version='148.0.7778.215' is_installed=True
```

**No persona release was involved at any point.** The Chromium half of the owner's
requirement is met today.

### 1.2 The in-use guard: all four branches exercised

The one gate that can stop an unattended tick is the in-use oracle, re-asked under
`_install_lock` immediately before the replacement. All four states, live:

```
oracle -> True      InstallDeferred("a profile is running — install deferred to a later check")
oracle unwired      "no in-use oracle wired — deferring…"   → InstallDeferred   (fails CLOSED)
oracle raises       "in-use check failed (RuntimeError('boom')) — deferring…"   → InstallDeferred
oracle -> False     install lands
```

And critically, after a deferral:

```
after deferral: version.txt still '100.0.0.0', is_installed=True
idle retry: download_engine -> True (asset already on disk, no re-download)
```

**A deferral is not a stop.** The verified asset stays on disk, `is_installed()`
stays True (the marker/sentinel writes are deliberately *after* the deferral check,
`updater.py:938-951`), and the next hourly tick installs it without spending the
bytes again. The docstring's claim that "the hourly tick is both the discovery of a
new build AND the retry that eventually installs one" is **verified behaviour**, not
an aspiration.

### 1.3 Where a Chromium tick *could* legitimately stop

For completeness, three states where it does not reach an install — none of which is
a defect, and none of which needs a persona release to clear:

* **The operator reverted** (`pinned_build()`): a deliberate standing "not this
  build". Cleared by the operator resuming updates.
* **The operator set `max_tested_major`** in their own `engine-policy.json`. persona
  ships no ceiling; `ABOVE_CEILING` is unreachable unless someone asked for it, and
  its message correctly names *their* file rather than telling them to update
  persona (`policy.py:245`).
* **Upstream publishes no digest** for the matched asset → `EngineUnverifiable`,
  keyed by tag so a later build supersedes it. Not observed on any current asset.

### 1.4 One real Chromium limitation, which is upstream's and not a gate

Upstream `fingerprint-chromium`'s newest release is `148.0.7778.215`, published
**2026-06-21** — 75 days stale as of this measurement. So the *mechanism* works and
there is simply nothing newer to take. That is consistent with the stalled-cadence
finding already recorded in PS-18, and it is not something persona's update path can
or should fix.

---

## 2. Firefox — a persona release IS required, and the reason is the SEAL

The ticket asks whether the limit is the driver pin, and whether a release is
required "for a build above that pin, or only for one whose ASSET NAMES changed".

**Neither. It is the BuildID seal, and it fires even when the asset names are
byte-identical.**

### 2.1 The asset names have NOT changed — so the ticket's candidate reason is out

Enumerated live from the release API:

| release | published | assets |
|---|---|---|
| firefox-20 *(our pin)* | 2026-08-17 | linux-arm64, linux-x86_64, **macos-arm64, macos-x86_64**, win-x86_64 |
| firefox-21 … firefox-26 | 2026-08-27 → 08-31 | linux-arm64, linux-x86_64, win-x86_64 |

Every non-mac asset name is **identical across all of them**
(`firefox-151.0-stealth-linux-x86_64.tar.gz`), because they all carry the same
upstream Firefox version, 151.0. So on Linux and Windows an "asset names changed"
theory predicts no refusal at all — and the refusal happens anyway.

What that means for `fetch_latest_full`'s `capped_by` path: it is reached by the
**build-number** comparison (`num <= pkg_num`), never by the asset test. Measured on
every OS persona ships, against the shipped pin:

```
linux/x86_64   fetch_latest_full -> ('firefox-20', True, 'firefox-26')
win32/AMD64    fetch_latest_full -> ('firefox-20', True, 'firefox-26')
darwin/arm64   fetch_latest_full -> ('firefox-20', True, 'firefox-26')
```

The operator is correctly told, on every platform, that firefox-26 exists and needs
a newer persona (`app.py:3358` / `app.py:3392`). The reporting is honest. The
question is whether the refusal itself is necessary.

### 2.2 It is not one gate but THREE, and only the third is load-bearing

Persona has two of its own, and the driver has one:

1. **`firefox.fetch_latest_full`** caps the *offer* at `build_number(BINARY_VERSION)`
   (`firefox.py:171`). Persona's own code. Removable.
2. **`engine_install.installed_builds`** discards any cached build above the pin
   (`engine_install.py:145`, `if num > pinned_num: continue`). Persona's own code.
   Removable.
3. **`invisible_core`'s seal** — `verify_engine` / `ensure_binary`. **Not persona's
   code, and not removable from persona.**

I measured that the first two are genuinely only persona's opinion by driving the
*real* installer past them. `install_engine_build("firefox-21")` — the exact call
`firefox.download_engine` makes — **succeeds**:

```
install_engine_build('firefox-21') -> True
cache dirs: ['firefox-21']
installed_builds() -> []            ← persona's own cap discards what it just installed
active_build()     -> firefox-20
is_invisible_installed -> False
```

So persona **downloads, verifies against `checksums.txt`, and extracts** a build
above the pin without complaint. The bytes are on disk and whole. Persona then
refuses to *see* them. That first pair of gates is therefore not the real limit.

### 2.3 The real gate: identity by BuildID, not by version

`invisible_core` ships a `seal.json` pairing it to exactly one build, per-leg, by
`application.ini` BuildID. Probing the two real trees against the shipped seal:

```
active seal: firefox-20, upstream 151.0

firefox-20  Version 151.0  BuildID 20260817150018  juggler loose 4/4  -> ACCEPTED
firefox-21  Version 151.0  BuildID 20260826204710  juggler loose 4/4  -> REFUSED
    application.ini BuildID '20260826204710' != one of 20260817145957, 20260817150018
    platform.ini    BuildID '20260826204710' != one of 20260817145957, 20260817150018
```

Note what is *not* the reason. Both trees report **the same upstream version
(151.0)**, both carry the juggler patch at the **full 4/4 marker score**, and both
ship the **same asset name**. The two builds are as alike as two builds can be, and
the seal still refuses — because the seal's claim is about *this exact CI build*,
not about a version range.

This is enforced on **every** route that turns a path into a running Firefox
(`invisible_playwright/_engine.py:37-39`), so it is not bypassable via
`binary_path=` — which is exactly the route persona's `_binary_path_override()`
uses. Confirmed live:

```
resolve_executable(firefox-21 tree) -> REFUSED: application.ini BuildID … != …
```

And the by-name route refuses just as firmly:

```
ensure_binary("firefox-21") -> SealMismatch: this invisible-core is sealed to
  firefox-20 …; it cannot install 'firefox-21'.
```

### 2.4 ⭐ The pin is a REAL protocol contract — the refusal is correct, not conservative

This is the measurement that decides whether the Firefox gate is worth removing, and
it is the one I most want read.

`invisible_core` offers a documented escape hatch: `INVISIBLE_SEAL_FILE` points the
package at another build's seal, and upstream publishes `seal.json` as a release
asset for every build. So persona *could*, in principle, fetch `seal.json` beside
the engine and update both together — no persona release, requirement met.

**I tried it. It does not work.** With the firefox-21 seal in force, the seal check
passes and the launch then fails one layer deeper:

```
seal override in effect: firefox-21 (build 20260826204710)
  firefox-20 -> REFUSED   (correctly — the seal now names 21)
  firefox-21 -> ACCEPTED

launch firefox-21 under the SHIPPED driver (invisible_playwright @ 353df4fa):
  Error: BrowserType.launch: Protocol error (Browser.enable):
    Browser.enable no longer applies preferences. They are written into the
    profile before startup, so a browser started this way already has them.
    Remove userPrefs from the request.
```

The juggler contract genuinely changed between firefox-20 and firefox-21. The seal
is not bureaucratic version-stamping — it is standing in front of a real
incompatibility, and it fails *loudly at launch* rather than silently misbehaving.

For the control, the same probe on the builds each driver is actually paired with:

| driver | engine | result |
|---|---|---|
| shipped (`353df4fa` + core 20.14.0) | firefox-20 | **DROVE OK** — `Firefox/151.0` |
| shipped | firefox-21 (seal overridden) | **protocol error at `Browser.enable`** |
| current upstream (`03f695d8` + core 26.17.0) | firefox-26 | **DROVE OK** — `Firefox/151.0` |

So the full loop is proven in both directions: a newer engine needs a newer driver,
and with the newer driver it works. **"A persona release is required" is the
correct, measured answer** — the release is what ships the matching driver.

### 2.5 Why the two packages cannot be moved separately

The pin is enforced at import, so a core-only bump is not even a runtime failure —
it is an `ImportError` on the operator's machine:

```
ImportError: invisible-core version mismatch.
  invisible-playwright 0.7.1 requires invisible-core==20.14.0
  installed            invisible-core 26.17.0
```

`invisible_playwright` and `invisible_core` therefore move as **one unit**, and that
unit is a pinned dependency in `pyproject.toml` — i.e. a persona build. There is no
in-product path to it, and `engine_autobump.py` already models this correctly
(`scripts/engine_autobump.py:5-8`: *"update the engine" == "bump the driver pin +
rebuild persona"*), running daily at 06:00 UTC with a fingerprint gate.

That autobump is, in fact, live and ready right now:

```
plan() -> needed: True
   reason: engine firefox-26 available (was firefox-20)
   current_core: 20.14.0  latest_core: 26.17.0  new_baseline: firefox-26
```

**Which is exactly the danger in §4.** Read that before acting on it.

---

## 3. The Chromium un-versioned tree — what it actually costs

The ticket names this the genuine Chromium obstacle. It is real, and it is the
reason the two engines' update paths differ in shape — but **it costs a deferral,
not a release, and it is already paid.**

**The asymmetry.** Firefox lands each build in its own versioned cache dir
(`cache_dir_for_seal` → `firefox-20_151.0_20260817150018`), so a running profile
keeps executing from an untouched tree; an install is additive. Chromium keeps **one
un-versioned tree** (`ENGINE_DIR`, `updater.py:29`) and every install path replaces
entries of it **in place** (`_promote_staging`, `updater.py:643`). POSIX does not
refuse that `os.replace` — only Windows does, by accident of its sharing rules — so
on Linux/macOS an ill-timed install is *silent corruption of a live session*, not a
loud failure.

**What that costs, itemised, all of it already implemented:**

| cost | where | paid how |
|---|---|---|
| An in-use oracle must be injected from the UI layer | `updater.set_in_use_provider`, wired at `app.py:3294` | done |
| The guard must be re-asked under the install lock (TOCTOU) | `updater.py:938` | done |
| It must fail **closed** (unwired/raising ⇒ defer) | `updater.py:117` | done, both branches measured (§1.2) |
| A deferral must not look like a failure | `InstallDeferred`, distinct from `False` | done |
| A deferral must not re-download ~190 MB per retry | verified-asset reuse, `updater.py:917` | done, measured |
| A failed promotion must not leave a half-tree | `BACKUP_NAME` rename + restore, `updater.py:643` | done |
| A crashed install must not read as ready | `.engine-installing` sentinel vetoes the marker | done |

**So the honest cost statement is: the un-versioned tree costs Chromium updates a
LATENCY, bounded by how long the operator keeps a profile open, plus the seven
mechanisms above — all of which already exist and all of which I exercised.** It
does not cost an operator the ability to reach a newer build, and it does not
require a persona release. Giving Chromium versioned trees would remove the
deferral, at the price of a second ~190–600 MB tree on disk; the ticket asked what
it costs, not whether to do it, and on this evidence **the case for doing it is
weak** — the deferral resolves itself and is invisible to the operator apart from
one log line.

---

## 4. ⛔ The macOS hazard — reproduced independently, and it is worse than stated

The ticket's standing constraint is *"do not ship a path that lets a macOS operator
update into a state with no engine."* Measuring is safe, so I measured — and I
confirm PS-288's finding from the opposite direction, plus one detail that matters
for **this** ticket specifically.

**The asset cliff is real and exact.** From the release enumeration in §2.1:
firefox-20 is the **last** release carrying `macos-arm64` / `macos-x86_64`;
firefox-21 onward ship three legs only. Confirmed in the seals themselves:

```
core 20.14.0 (ours) -> seal firefox-20, 5 assets, 2 macOS legs
core 20.16.0        -> seal firefox-20, 5 assets, 2 macOS legs
core 21.16.0        -> seal firefox-21, 3 assets, 0 macOS legs
core 26.17.0        -> seal firefox-26, 3 assets, 0 macOS legs
```

**And the refusal is by PLATFORM CHECK, which is the harder failure.** On the
current driver the platform gate is a declared tuple, above any asset lookup:

```
GAMBE_SUPPORTATE       = (('linux','arm64'), ('linux','x86_64'), ('win32','x86_64'))
PIATTAFORME_SUPPORTATE = ('linux', 'win32')

ARCHIVE_NAME('darwin','arm64') -> NotImplementedError:
    seal firefox-26 has no asset for platform=darwin arch=arm64
```

Our shipped core 20.14.0 has **no such tuple at all** and still resolves
`firefox-151.0-stealth-macos-arm64.tar.gz`. That is precisely why macOS works today
and breaks on the next bump.

**The detail specific to PS-293.** `engine_autobump.py` is armed *right now* and its
plan is `firefox-20 → firefox-26` (§2.5). That is a scheduled daily job. If it
lands, the macOS bundle ships a driver that refuses macOS at both the download and
the launch boundary — and **every existing guard stays green**, because
`release.yml`'s three downgrade guards compare `firefox-NN` (26 ≥ 20 passes) and the
fingerprint gate is a Linux measurement.

PS-288 has since landed `tests/test_engine_driver_platform_support.py`, which is the
guard that catches exactly this, and it is on `main` at `18e30eb`. **That test is
the only thing standing between the autobump and a broken Mac release.** Nothing in
this ticket should weaken it, and I have changed no code.

**Conclusion for this ticket's scope:** every route that would make Firefox engine
updates reach an operator without a persona release — removing persona's two caps
(§2.2), or shipping the seal alongside the engine (§2.4) — moves the product toward
firefox-21+, which is the state with no macOS engine. **Measuring was safe; the
change is not, and it stays blocked behind the macOS asset work.** This is not a
nominal dependency.

---

## 5. The answer to the ticket's question, per engine

> *For each engine, can an operator running a released persona actually end up on a
> newer build today — and where exactly does that stop?*

**Chromium — YES, nothing stops it. The requirement is already met.**
Traced live, end to end, on the real upstream release. Every gate in the path is
satisfied by an ordinary release: no ceiling ships, the known-bad list is empty,
upstream publishes a digest for every asset. The only thing that can pause a tick is
the in-use deferral, which is self-resolving, does not re-download, and is retried
hourly. There is no persona-release requirement anywhere in the Chromium path. The
sole practical limitation is that upstream has published nothing since 2026-06-21.

**Firefox — NO, and a persona release is genuinely required.**
Not because of asset names (identical across firefox-20…26 on every non-mac leg),
and not merely because of a version comparison. Because `invisible_core` is sealed
by **BuildID** to one exact build and refuses every other on every launch route —
and, measured, that refusal is standing in front of a **real juggler protocol
change**: firefox-21 under the shipped driver dies at `Browser.enable`, while
firefox-26 under the matching driver drives fine. The driver and core are pinned to
each other at import time, so they move as one unit, and that unit is a persona
build. `engine_autobump.py` already implements exactly this and runs daily.

**What would have to change for Firefox to meet the requirement** (stated for the
ticket that may follow, not proposed here — implementation is out of scope):

1. Persona's own two caps (`firefox.py:171`, `engine_install.py:145`) would have to
   go. Necessary, nowhere near sufficient — measured in §2.2: removing them yields a
   downloaded, extracted, whole build that then **refuses to launch**.
2. The seal would have to travel with the engine (`INVISIBLE_SEAL_FILE`, fetched
   from the release like `checksums.txt`). Measured in §2.4: this clears the seal
   check and **still does not produce a working browser** — the protocol contract is
   the real barrier.
3. Therefore the driver itself must move, which is a `pyproject.toml` pin, which is
   a persona release. **There is no in-product route around this**, and the honest
   answer to the owner's requirement is that Firefox cannot meet it without a change
   inside `invisible_playwright`/`invisible_core` — packages persona does not own.
4. ⛔ And all of it is **blocked on macOS** (§4): every step above moves toward
   firefox-21+, which has no macOS engine and refuses darwin by platform check.

---

## 6. What was NOT established, and why

Stated rather than estimated, per the project's standing rule.

* **Windows and macOS install paths were not exercised.** All live runs were on
  Linux x86_64 in-container. `_install_windows` (zip → staging → promote) and
  `_install_macos` (dmg mount → copy → detach) were read, not run. The gates I
  measured (policy, digest, in-use, lock) are all **above** the per-OS branch at
  `updater.py:966`, so the finding for §1 holds on all three; the promotion
  mechanics themselves are Linux-measured only.
* **No claim about whether firefox-21+ actually leaks differently.** The gate-red
  fingerprint movement at firefox-21 is PS-290's measurement and I did not re-derive
  it. This report is about *reachability*, not about whether a newer build is
  desirable.
* **The `Browser.enable` protocol error was not root-caused past its message.** It
  is sufficient for this question — it proves the pin guards a real incompatibility —
  but I did not determine which juggler commit introduced it or whether a narrower
  driver change would suffice.
* **`_download_engine_fresh` (cold-start) was measured only in its success path.**
  Its refusal handling is deliberately separate from `_auto_update_engine`
  (`app.py:4534`) and I did not exercise its failure branches.
* **The upstream stall was not investigated.** Chromium's newest release being 75
  days old is reported as an observation; whether the cadence resumes is PS-18's
  territory.

---

## 7. Reproducing this

Artifacts are committed beside this report.

* `artifacts/live_chromium.log` — the full live Chromium tick transcript (§1).
* `artifacts/live_chromium.py` — the script that produced it. Downloads ~190 MB into
  a throwaway `PERSONA_HOME`; touches no real engine dir.
* `artifacts/firefox_gates.py` — the Firefox seal/driver probe (§2.3, §2.4). Needs
  the two engine trees; it prints the URLs it wants.
* `artifacts/ff_install.log` — `install_engine_build("firefox-21")` succeeding while
  `installed_builds()` discards it (§2.2).

The seal probes need `xvfb` for the launch legs (`sudo apt install xvfb`); the
non-launch legs (identity, `verify_engine`, `ensure_binary`) need no display.
