# Engine fingerprint baseline — what a pinned profile looks like on a given engine

Reference recording behind `src/services/verify/baseline.py` (ticket PS-24).
This file exists so `engine-fingerprint-baseline.firefox.json` has a checkable
provenance and the next person can reproduce it exactly instead of guessing.

**Artifact:** `tests/fixtures/engine-fingerprint-baseline.firefox.json`
**Engine build it was taken under:** `firefox-20` — also recorded inside the
artifact as `engine_build`, so "is this baseline still current?" is answerable
by reading the file and comparing it against `engine-baseline.txt` at the repo
root, without running anything.

## Why this exists

The engine moves on its own. `.github/workflows/engine-autoupdate.yml` runs
daily at 06:00 UTC, detects a newer patched-Firefox build, rewrites the pins and
`engine-baseline.txt`, commits to `main` and pushes a version tag — which
triggers `release.yml` to build and publish. Nobody approves that.

An engine change is the single event most likely to move what a site sees about
a profile, because it replaces the layer half the masking lives in. The release
workflow already refuses an engine *downgrade*; this artifact is the missing
complement — it makes it possible to refuse an engine that silently changed the
**identity**, which a version number cannot express.

## How to use it

```bash
# Does the current engine still produce the recorded identity?
xvfb-run -a python -m src.services.verify.baseline_cli check
```

Exit `0` means every probe was read on both sides and none of them moved.
Exit `1` means either a probe drifted (it is named, with expected vs observed,
in both realms) or a probe could not be read at all. Exit `2` means the check
could not run — no display, or **nothing usable to compare against**.

That last distinction is the load-bearing one for anything automated. Exit `1`
is the *drift* signal, so a baseline that cannot serve as a reference must never
produce it: nothing was compared, and a job that read non-zero as "the engine
changed the identity" would report a leak that never happened. Whenever the
check cannot run, the message says so in those words — it is not drift.

"Nothing usable" is a wider set than "unreadable", and the difference matters
because the extra cases do not look like malfunctions. Exit `2` covers all of:

- the artifact is **missing**, or the path is a directory;
- it is **corrupt** — malformed JSON, truncated, or not valid UTF-8;
- it **parses cleanly but is not a snapshot** — a JSON list, a bare `null`, or
  some other object in the tree (`--baseline site/package.json` is a plausible
  typo);
- it **is** a snapshot but carries **no probe readings at all**, which is what a
  refused or truncated recording leaves behind.

The last two are the ones worth stating explicitly. Reading bytes off disk is
not the same question as *being a baseline*, and a wrong-but-readable artifact
has nothing to compare against — so every observed probe would diff as `added`
and the tool would print a confident `DRIFT: 78 probe(s)` / `FAIL`. That is the
maximum-alarm output of this system, produced by a comparison that never
happened, and because it carries no traceback it looks like a real answer. It is
refused instead.

A missing artifact is most often a **path** problem rather than a lost file:
the default is repo-relative, so running the command from anywhere but the
repository root cannot find it. Pass `--baseline` with a full path, or `cd` to
the root.

## How the artifact was produced

Recorded by `python -m src.services.verify.baseline_cli record`, which launches
the pinned profile in-process, reads every probe through the live session's eval
hook, and tears the session down. It is a reading taken from a **real launched
browser**, never from generated source text.

The exact inputs are also embedded in the artifact itself under `provenance`:

| Input | Value | Why pinned |
|---|---|---|
| profile name | `persona-fingerprint-baseline` | `Profile.fingerprint_seed` is `crc32(name)` — **pinning the name is what pins the seed**, and with it the whole derived identity |
| fingerprint seed | `1042768975` | derived from the name above; recorded so a mismatch is visible |
| `os_type` | `windows` | explicit, so no host value can substitute |
| `device_type` | `desktop` | explicit, so no mobile preset is picked |
| `engine` | `firefox` | the family that auto-bumps |
| `resolution` | `1920x1080` | explicit, never `auto` — `auto` picks a preset from the seed, which is reproducible but leaves the geometry implicit |
| proxy | **none** | a proxy makes locale/timezone follow a geo lookup, and variance the network introduced would read exactly like variance the engine introduced. With no proxy the launcher pins `en-US` + `America/New_York` |
| bookmarks | **explicitly cleared** (`[]`, not `None`) | `None` means "use the store's defaults", which would make the reading depend on the operator's bookmark store |
| certificate | none | an mTLS session would add a terminator to the launch path |
| `window_size` | `1280x800` device px | **PS-304.** The recorder writes this into the profile's `xulstore.json` before the launch, so the main-window geometry is an *input* to the recording rather than whatever default the engine chose for itself. See below |
| realms | `window` **and** `worker` | a spoof that lands on the page but not inside a Web Worker is the historically load-bearing leak, and it is invisible unless the worker realm is read |

The profile is constructed as a plain dataclass and is **never written to the
profile store**, so the baseline identity cannot be edited by a human out from
under the artifact.

### Why the window geometry is pinned (PS-304)

`window.innerSize` is not seed-derived and is not an identity vector — its own
note in `probes.py` says *"Window chrome geometry; differs with a resized
window, not a spoof change."* Nothing in the recording path used to fix it.
`_seed_window_size` (`invisible_launch.py`) looks like it would, but it **cannot
fire on Linux or macOS**: `_work_area()` returns `(0, 0)` off Windows and the
helper bails on `if not (aw and ah)`. The CI gate runs `ubuntu-24.04` under
`xvfb-run`, so the recorded geometry was simply the engine's own default.

firefox-21 changed that default (1152 → 1280 CSS px) and every later build kept
the new value, so `engine_gate compare` reported a moved probe on **every** bump
from firefox-20 onwards — permanently, for no security reason, and beside two
genuine `canvas.readback` findings. `engine_gate.py` states the cost in its own
words: *"a gate that is always red is a gate people learn to ignore, which is
worse than no gate."*

`baseline._pin_recording_window` now writes a fixed `main-window` size into
`DATA_DIR/<profile>/.invisible-profile/xulstore.json` — the engine's **inner**
profile dir, which is the path `spawn_browser` hands the child — between the
`fresh` wipe and the launch. `_seed_window_size` early-returns on an existing
`xulstore.json`, so the shipped seeding path defers to the pin byte-for-byte and
needed no change.

**Which recordings are pinned, and which are not.** `_pin_recording_window`
honours the same deferral it relies on: it writes only when there is no
`xulstore.json` already.

| Recording | Pinned? | Why |
|---|---|---|
| `fresh=True` — **the gate**, always (`engine_gate.py`) | **yes**, every time | the rmtree just removed the tree, so the file cannot exist |
| `fresh=False` — `baseline_cli --reuse-profile`, and the launch-backed behavioural checks | **no** (after the first) | `fresh=False` means *start from what the previous session left behind*, and that persisted state is what those checks are measuring |

That second row is load-bearing rather than a caveat. An earlier round pinned
unconditionally, which truncated a warm profile's own `xulstore.json` — losing
`PersonalToolbar`, `sidebar-box` and the user's `main-window` — and so wrote
over exactly the substrate `behaviour_checks.py`'s restart-continuity model
observes. `test_a_warm_recording_leaves_the_profiles_own_window_state_alone`
holds it, and `test_the_gate_path_is_still_pinned_despite_the_warm_deferral` is
the positive control that the deferral did not leak into the gate's path.

⛔ **This blinds nothing** — read with the scope above, which is what makes the
claim true rather than merely reassuring. The probe still reports what it reads
and still diffs; a genuine re-roll of window geometry still moves it, which is
exactly the property `behaviour_checks.py` protects when it refuses to solve the
same transient with an ignore list. What changed is that both sides of an
**engine comparison** are now sized by *us*, so a difference between them is
attributable to the engine.

`provenance.window_size` is therefore **measured, not restated**: it reports the
geometry read back off the profile's `xulstore.json` after the recording, so a
warm recording states what it actually ran with instead of claiming the pin. The
key is omitted entirely when nothing was recorded (the chromium arm never
launches a window) — a missing field means *not recorded* and never stands in
for a value nobody observed.

The value is capped by `BASELINE_RESOLUTION`: a CSS `innerWidth` larger than
`screen.width` is the #216 impossibility (no real un-maximized window is wider
than its own screen), and `test_the_pinned_window_cannot_exceed_the_baselines_spoofed_screen`
holds the invariant now that the pin pre-empts `_seed_window_size`'s own cap.
Note `1280x800` is the **outer window in device px**, not the content area: live
on the runner an 800px window records `innerHeight: 687`, chrome taking ~113px.
Anyone raising this constant should reason from that ratio rather than from 800.

### What moved in the PS-304 re-record, and why

Re-recording is a deliberate act (see "When to re-record" below), so the diff is
stated here rather than left to be reconstructed. **Three probes moved and none
of them is a masking change**; every canvas, WebGL and font reading is
byte-identical to the previous artifact.

| Moved | Cause |
|---|---|
| `window.innerSize` (window realm) | **This ticket.** inner 1152x808 / outer 1166x927 → inner 1280x687 / outer 1294x806, i.e. the pin — measured, not predicted |
| `stealth.apiPresence` → `Serial`, `navigator.serial` (both realms) | **The host, not the pin.** `function`/`object` → `undefined`/`undefined` |
| `app_version` | routine: 3.0.2 → 3.1.0 |

The WebSerial pair is the *already-documented* platform-gate movement — it is
named in `ENV_SENSITIVE_PROBES` for exactly this, recorded under PS-314 when the
flip ran the other way. It is attributed to the host rather than to the pin on
evidence: a recording taken on this host **before any code change**, on the same
`firefox-20` build, already differed from the committed artifact on precisely
these two keys and on nothing else. The container simply does not expose
WebSerial where the PS-314 host did.

The pin's own effect was isolated by diffing that pre-change recording against a
post-change one — same host, same engine build, same profile — and it is exactly
one probe: `window.innerSize`. Nothing else moved.

### The pin makes the recording reproducible — measured

Two independent fresh recordings after the pin (each wiping the profile dir and
launching a real browser under `xvfb-run`) produced the identical geometry —
inner 1280x687, outer 1294x806 — and `diff_snapshots` over the two full
snapshots reported **no differences** at all.

### ⚠️ The `outerHeight − innerHeight` offset SURVIVED the pin

PS-290 measured this offset at 119 against `_outer_size_override_script`'s own
constant of 91 and could not explain the extra 28px. The pin was predicted to
drive it to 0. **It did not, and that is reported rather than quietly dropped:**
the offset is still exactly 119 after pinning, on both fresh runs.

That is more informative than the predicted outcome, because moving the window
turned the question into a controlled experiment. The inner height changed
(808 → 687) and `outerHeight − 91` tracked it exactly (836 → 715), holding a
**constant 28px** above the probe-time inner height on both geometries. A frozen
number could not have tracked a change; a live recomputation could not have
stayed 28 off. So the getter is capturing a real inner height that is 28px
larger than the one the probe later reads — the window settles between the init
script and the probe read.

This also **settles the contested premise** in the ticket's own brief, live and
on the accessor's signature rather than on its value:

```
Object.getOwnPropertyDescriptor(window,'outerWidth').get.toString()
  -> "function outerWidth() {\n    [native code]\n}"
  -> .name === "get outerWidth"
live: innerWidth 1152, outerWidth 1166, screen.width 1920
```

`outerWidth` reports **inner + 14**, *not* the spoofed screen width of 1920.
Under the competing account (the spoof never ran on fx-20) the native accessor
would have reported 1920 and produced the `inner < outer == screen` tell the
override exists to remove. It reported 1166. **The spoof ran.** The
`[native code]` source text is the cloak's own output — `_native_cloak_js`
rewrites registered functions' `toString`, and the `.name` of `"get outerWidth"`
is what the cloak sets. So this is instrument staleness, not a masking defect,
and the memory recording the opposite reading (`9f50f0f4`) is wrong on this
point.

The staleness is a **separate, pre-existing defect** and is deliberately not
fixed here: a page that resizes its window reads a stale `outerWidth`. It is
untouched by the pin in either direction — 119 before, 119 after — so it is not
a regression this change introduces.

## Verification performed when this landed

All four checks were run against a real browser on `firefox-20`, not simulated:

- **Stability first.** Three independent recordings — each wiping the profile's
  data directory and launching a fresh browser — produced **byte-identical**
  files, and the comparator called them identical. This is the property
  everything else rests on: if it did not hold, the baseline would be noise and
  anything built on it would be false confidence.
- **The gate can fail.** One spoofed value was perturbed in a copy of the
  baseline (`worker` / `navigator.hardwareConcurrency`, `12` → `8` — what a host
  core-count leak would look like). It was caught, named with both values, and
  exited non-zero.
- **A missing probe is reported, not skipped.** Deleting
  `worker/navigator.userAgent` from one side produced an explicit `added` entry
  and a non-zero exit, rather than passing quietly.
- **Both realms.** 78 readings: 45 in `window`, 33 in `worker`.

For the record, the recording also demonstrates the masking is real rather than
inherited: the host has 8 cores and reports Linux, while the recorded profile
reports 12 cores, `Win32`, and Firefox 151 on Windows.

## Some readings depend on the HOST, not only on the engine

Pinning the profile pins everything the seed derives, but not what the GPU and
driver stack or the installed font metrics report. A handful of probes read
through host facilities and can therefore differ between two machines running
the **same** engine:

- `webgl.unmasked` — reports the renderer string of whatever GPU/driver is
  present (this recording carries an ANGLE/NVIDIA string from the machine below)
- `webgl.parameters`, `webgl.extensions`, `masking.webglGetParameter`
- `fonts.measureText` — host-specific text metrics

This list is embedded in the artifact itself under
`provenance.env_sensitive_probes`, so whoever is reading a red diff sees the
caveat in the same file as the values it applies to, without having to find this
note. It is recorded as probe **names only** — deliberately, because putting an
actual GPU or driver string in the artifact would make its bytes depend on the
machine that recorded it, which is the opposite of what a byte-stable reference
needs.

**What this means in practice:** a `check` run on different hardware from the
recording may report drift on these probes that is *host variance rather than
engine drift*. That is a real limitation of the current artifact, not a defect
in the comparison — the honest reading is that the check is at its strongest
when run on the machine the baseline was recorded on, and that a red run on
these specific probes is the first thing to attribute before treating it as an
engine change.

## ⚠️ What this does NOT do

**This check does not fire on the automatic engine bump.** Both workflows run on
`ubuntu-24.04` with no display server, no engine installed and no `xvfb`, so
nothing in `.github/` invokes it. Wiring it in is a real slice of work — install
the engine, provide a display, keep it deterministic on a headless runner where
behaviour genuinely differs from an operator's desk — and it has not been done.

What exists today is a check an operator **can run**. That is the precondition
for automating it, not a substitute for it. **Until that slice lands, the daily
job still commits and tags on its own, and the bump ships unverified.** The fact
that the bump is fully automatic is the argument for prioritising the CI ticket.

Two things that must not be done to "solve" that gap: making the autobump job
skip the comparison silently, and weakening the comparison so it can run without
a browser. Comparing generated source text instead of a live reading is
precisely the defect the verification service was built to end.

**The `firefox-19` → `firefox-20` transition is unverified and will stay
unverified.** The baseline was first recorded on `firefox-20`, which was already
current by the time this landed; reconstructing the previous engine to retro-diff
that transition was explicitly not wanted. This machinery makes `20` → `21` and
everything after it checkable.

## When to re-record — and why you are allowed to

When a bump is genuinely accepted — the diff was reviewed, and the change is
understood and wanted — **re-record the baseline on the new engine and commit
that as a reviewable change**:

```bash
xvfb-run -a python -m src.services.verify.baseline_cli record
git add tests/fixtures/engine-fingerprint-baseline.firefox.json
# commit with WHY the diff was acceptable, not just "update baseline"
```

This is stated deliberately, because the opposite convention is worse: **a
baseline nobody may update becomes a permanently red check that everyone learns
to ignore.** A re-recording is a normal, reviewable commit — the diff in the
artifact *is* the record of what the engine changed, which is exactly what a
reviewer should be looking at.

`record` refuses to write a baseline that has any unreadable probe, because a
probe that errors here would compare equal against the same error later and be
reported as agreement.

The refusal is checked **before** anything is written, and that ordering
matters: `--output` defaults to the committed artifact above, so validating
after the write would destroy the good reference and then print a message
saying it had been refused. Because re-recording is the *encouraged* workflow
after an accepted bump, that would fire exactly when the operator is doing the
right thing.

So on a failed reading the reference path is left **byte-identical**, and the
unusable recording is written beside it as `<output>.rejected` — kept, because
the errors are what you need to diagnose, but never at the path being blessed:

```
$ python -m src.services.verify.baseline_cli record
REFUSING to treat this as a good baseline: 1 probe(s) could not be read. ...
Wrote the unusable reading to tests/fixtures/engine-fingerprint-baseline.firefox.json.rejected
for inspection; tests/fixtures/engine-fingerprint-baseline.firefox.json is UNCHANGED.
$ echo $?
1
```

## A trap worth knowing about

`diff_snapshots` compares entries verbatim, so two identically-**failed**
readings (`{"error": X}` on both sides) compare equal and the raw diff reports
them as agreement. A comparison could therefore go green off two non-readings.

The baseline check refuses that: it counts errors on both sides and fails when
either side has any, reporting `INCONCLUSIVE`. A pass from this command means
"every probe was read **and** nothing moved" — never "nothing could be read on
either side".

## Adding a second engine

Chromium can be added by the same mechanism (it is reachable over CDP from any
process, so it does not need the in-process launch the Firefox path requires).
It was deliberately left out of the first slice: Firefox is the engine that
auto-bumps, and covering both was not worth blocking this on.
