# PS-218 — trial build of our own engine: the record

**Status of this document:** it records what was *prepared*, what was *measured
from existing evidence*, and — plainly — **what was not run and why**. The
ticket's own standard is that a report must never let *"it compiled"* and *"it
was never compiled"* collapse into each other. This document therefore leads
with the second.

---

## 1. Was anything compiled? **No. Not yet.**

**No Chromium build has been run, on either tree.** There is no binary, no
wall-clock figure and no peak-memory figure in this document, because the
compile happens on the owner's self-hosted runner (`persona-wsl-builder`) and is
triggered by a human pressing the button on `engine-trial-build`. An agent
pushes the branch and collects the artifact; the compile lives outside the agent
loop, deliberately — that is the ticket's design, not a shortfall in it.

Every "what good looks like" field that requires a compile is therefore
**UNMEASURED**, and is listed as such in §6 rather than estimated:

| deliverable | value |
|---|---|
| unmodified tree compiled? | **NOT YET RUN** |
| patched tree compiled? | **NOT YET RUN** |
| compile failures, attributed | **NOT YET RUN** |
| wall-clock per build | **NOT YET MEASURED** |
| peak memory per build | **NOT YET MEASURED** |
| GPU strings from a service worker in the built browser | **NOT REACHED** — see §5 |

What *is* delivered here is the instrument that produces all six, plus one
finding that came out of the existing evidence and **materially changes step 3**
(§4). Read §4 before running the build; it changes what the build is for.

---

## 2. What was built, and how to run it

### The versions, picked by whether source exists

The ticket warns: *"Pick the tag by whether its source exists, not by its
number."* **Verified rather than assumed:**

| tag | contents |
|---|---|
| `fingerprint-chromium` **`148.0.7778.215`** — the build we ship today | **4 files.** `LICENSE`, `README.md`, `README-ZH.md`, `qqgroup.png`. **No source tree at all.** |
| `fingerprint-chromium` **`144.0.7559.132`** | 22 entries — a real tree (`patches/`, `utils/`, `downloads.ini`, `flags.gn`, …) |

So **144.0.7559.132** is the newest fingerprint-chromium tag whose source
actually exists, and `ungoogled-chromium-portablelinux` **`144.0.7559.132-1`**
is its exact counterpart. Both are pinned as the workflow's default input.

> This is a real gap and worth stating plainly: **we ship 148 and the newest
> obtainable source is 144.** The trial build cannot be a build of what we ship,
> because the source of what we ship was never published.

### The pieces

| path | what it is |
|---|---|
| `.github/workflows/engine-trial-build.yml` | the build, `workflow_dispatch` only, on `[self-hosted, persona-build]` |
| `engine/patches/fingerprint/*.patch` | our 16, vendored from `144.0.7559.132` (116,435 bytes) |
| `scripts/ps218_record_env.sh` | what the environment sees, at three levels |
| `scripts/ps218_stage_patches.sh` | staging into ungoogled's own series |
| `scripts/ps218_build.sh` | drives prepare / compile as two timed phases |
| `scripts/ps218_manifest.sh` | the per-tree verdict |
| `scripts/ps218_attribute.sh` | errors → patches, with the control diff |
| `tests/test_ps218_trial_build_workflow.py` | pins the invariants that protect his machine |

### Running it

Actions → **engine-trial-build** → *Run workflow*. Inputs: `ungoogled_tag`
(default `144.0.7559.132-1`), `trees` (`both` or `unmodified`), `ninja_jobs`
(**leave empty** unless linking runs out of memory).

Two builds of ~4–5.5 h each are expected, sequentially, on his machine.
Recommended first run: **`trees: unmodified`** — establish the instrument before
spending a second block of hours on the patched tree.

---

## 3. How the design answers the ticket's two hard requirements

### "Patch application and compilation are separate results"

They are two workflow steps with two outcomes, two logs and two rows in the
manifest — not one verdict. Upstream's own CI entrypoint already splits exactly
here (`_prepare_only` stops after `gn gen`), so this drives that split rather
than inventing one.

The manifest reports **three** states, never two:

- `applied / compiled`
- `applied / DID NOT COMPILE` ← **the number this ticket exists to produce**
- `DID NOT APPLY / compile not reached` ← *not* the same as "the compile failed"

Binary presence is checked **on disk**, not inferred from an exit code, and when
the two disagree the manifest says *"trust the binary over the exit code"* and
marks the run unresolved rather than picking one.

### "Whether the unmodified tree had it too"

`ps218_attribute.sh` joins each compile error's file against the `+++ b/` headers
of our 16 patches, and diffs against the control's error set first. Errors in
both are reported **PRE-EXISTING — NOT ours**.

Two honesty properties worth naming, because both are places this could have
quietly lied:

- **Attribution is by file, so it has a blind spot**, and it is reported rather
  than hidden. The classic signature-change break — our patch calls a function
  whose declaration moved — lands in **UNATTRIBUTED**, prominently, because in a
  tree differing from a *working* control by exactly 16 patches that is a real
  finding, not noise. This is precisely the failure mode the ticket predicts
  ("a patch that lands with nothing worse than line-offset drift can still break
  the build").
- **The control log lives in a different job**, so when it is absent every error
  is marked `CONTROL UNKNOWN` instead of being asserted as ours. Claiming an
  error is ours without having checked the control is the exact error this
  ticket exists to correct.

### The instrument check is enforced by the job graph

`patched` declares `needs: unmodified`. The ordering is a property of the graph,
not of anyone remembering it. If the control fails, the workflow says so in
those words: *the finding is about the build environment and says nothing about
our patches.*

### The `-j` reduction cannot go unrecorded

It is a workflow **input**, not a hidden default. When set it is written into
the timing file *next to the number it distorts*, into the manifest, and into a
warning that the wall-clock is no longer comparable to the 130-core-hours ÷
core-count prediction. The prediction and the actual are both computed into the
timing record so the comparison is in the artifact, not left as arithmetic.

Memory is **sampled every 5 s** for the whole phase, not read once at the end —
peak memory during *linking* is the figure that matters and a single reading
taken after the process exits would miss exactly it.

### Protecting the owner's machine

`workflow_dispatch` only; no `push`, no `pull_request`, no `schedule`.
`concurrency` queues rather than cancels — a build three hours in must not be
discarded because someone pressed the button twice. `permissions: contents:
read`, matching the posture `ci.yml` and `release.yml` already document, because
these jobs execute untrusted third-party code. The label is **`persona-build`**
(the corrected one; `persona-build-linux` carries no runner and would queue
forever *with no error*). `tests/test_ps218_trial_build_workflow.py` pins all of
this — 6 tests, passing.

### depot_tools is not installed, and does not need to be

The default route retrieves and unpacks the release tarball via
`utils/downloads.py`. The `-c`/clone route — the one that needs depot_tools — is
deliberately never passed. The whole toolchain comes from the Docker image.

### Our 16 go in through ungoogled's own mechanism

`shared.sh`'s `apply_patches()` already calls:

```
utils/patches.py apply <src> <ungoogled/patches> <root/patches>
```

It accepts **multiple patch directories by design**. Our 16 are appended to the
portablelinux series in numeric order (`000` first — it declares the switches
every later patch reads). Our layer is an *addition* to the existing pipeline,
not a fork of the tooling, which is how `fingerprint-chromium` composes them
today. Staging asserts the count is exactly 16 and fails loudly otherwise: a
build of some other number measures nothing.

---

## 4. ⚠️ Step 3's premise does not survive contact with the existing evidence

**This is the most important finding in this document, it was not expected, and
it should be read before the build is run.**

The ticket describes the fix as: the service-worker realm is unauthored, so add
*"an additional call site where the service-worker path reads GPU strings"*.
That description rests on the service-worker realm being **unreached by the C++
hook**. I checked that against PS-189's raw readings rather than inheriting it,
and **the layer-OFF control contradicts it.**

Layer-off means our JS masking layer is not installed, so what each realm
reports is *the engine's own C++ behaviour, unaided*. From
`readings/ps189-2026-08-26/realm-gpu-layer-off.json`:

| arm / seed | `page` (layer OFF) | `service_worker` (layer OFF) |
|---|---|---|
| **macos** / 24601 | `ANGLE (Apple, … Apple M2, …)` | `ANGLE (Apple, … Apple M2, …)` |
| **macos** / 5150 | `ANGLE (Apple, … Apple M4, …)` | `ANGLE (Apple, … Apple M4, …)` |
| **linux** / 24601 | `… SwiftShader …` | `… SwiftShader …` |
| **linux** / 5150 | `… SwiftShader …` | `… SwiftShader …` |

Two things follow, and they point somewhere different from the ticket's framing:

**(a) On macOS the C++ hook ALREADY fires inside the ServiceWorker realm.**
With our layer off, the service worker returns the *engine's spoofed* `Apple
M2`/`M4` — not the host's real GPU. A hook that produces a spoofed value in that
realm is a hook that *already reaches it*. So `getParameter` in a service worker
is served by the very code path `011-gpu-info.patch` hooks, and **there is no
missing call site to add.** The premise that the realm is unreachable by the
existing hook is, on the evidence, false.

**(b) On Linux the engine's GPU spoof produces nothing in ANY realm — including
the page.** Layer-off linux is `SwiftShader` *everywhere*, not just in the
service worker. And the captured `argv` in that same record proves the flags
were supplied:

```
--fingerprint=24601 --fingerprint-platform=linux --fingerprint-brand=Chrome
--use-gl=angle --use-angle=swiftshader
```

So this is not a realm-coverage defect on Linux. **The engine's GPU spoofing is
simply not working on the Linux arm at all.** Our JS layer masks the 11 realms
it can reach, which hides that; the ServiceWorker realm is the one realm JS
cannot reach, so it does not *cause* the leak — **it exposes it.**

This is consistent with something the ticket already records as an external
fact: *fingerprint-chromium carries an open, unanswered report that GPU spoofing
is broken in exactly the build we ship (`148.0.7778.215`)*. Finding (b) looks
like that same defect, observed from our side.

### What this changes

- **Adding a service-worker call site would fix nothing on Linux.** The hook is
  not missing; on this arm it returns nothing to any realm. A patch of that
  shape would be a change that measures clean on macOS (already working) and
  makes no difference on Linux (the arm where Invariant #0 is open).
- **The real question is why `GetGLRendererStringForFingerprint()` yields no
  value on the Linux path**, and whether that is fixed, still broken, or
  different in **144** — the version this build compiles. That is a question a
  built binary can answer directly and nothing else can.
- **The trial build is the right instrument for it**, which is a point in favour
  of running it, not against.

**I have deliberately not written a speculative patch.** Step 3 is gated on a
working binary, the binary does not exist yet, and the diagnosis the patch would
have been based on does not survive the evidence. Writing a fix now would mean
guessing at a cause I have just shown is misidentified — and the ticket is
explicit that a change made to look right without being verified from the page
is not evidence. The first measurement to take from the built binary is §5.

*Caveat, stated rather than buried:* these readings come from the shipped **148**
binary in a GPU-less container using `--use-angle=swiftshader`. That is our own
flag and the host's real renderer under it, per PS-14's discipline — it does not
weaken (a), which is a macOS/Linux *contrast* under identical harness
conditions, but the **144** build is what settles whether (b) is version-specific.

---

## 5. The first measurement to take once a binary exists

Before any patch is written, run this on the built **144** binary — it costs one
launch and it decides what step 3 should even be:

1. Launch with `--fingerprint=24601 --fingerprint-platform=linux`, **no JS layer**.
2. Read `UNMASKED_VENDOR_WEBGL` / `UNMASKED_RENDERER_WEBGL` from the **page** and
   from **inside a service worker**.

`scripts/ps189_realm_gpu.py` already reads all twelve realms including the
service worker and records the `argv` alongside; point it at the new binary. The
outcomes are cleanly distinguishable:

| page (layer off) | service worker | reading |
|---|---|---|
| spoofed | spoofed | Linux spoof works in 144 — the 148 defect is version-specific, and **the leak may already be closed** |
| spoofed | real GPU | *this* is the unauthored-realm defect the ticket describes — and only then is a new call site the right fix |
| real GPU | real GPU | the Linux spoof is broken in 144 too — an **engine-wide** defect, not a realm one, and step 3 is a different piece of work |

The middle row is the only one where the ticket's stated remedy is the correct
one. The evidence in §4 predicts the first or third.

Per the ticket, the leak must be shown closed **from the page, not from the
patch** — a test asserting the patch applied is not evidence.

---

## 6. Territory checklist, answered honestly

| the ticket asks for | answer |
|---|---|
| ungoogled tag and Chromium version | `144.0.7559.132-1` / Chromium `144.0.7559.132`; verified to carry real source |
| whether the unmodified tree compiled | **NOT YET RUN** — needs the owner to trigger the workflow |
| whether the patched tree compiled | **NOT YET RUN** |
| compile failures attributed to a patch | **NOT YET RUN** — the attribution instrument is built and tested |
| failures the unmodified tree also had | **NOT YET RUN** — the control diff is built |
| wall-clock and peak memory per build | **NOT YET MEASURED** — instrumented (5 s sampling; prediction vs actual computed into the record) |
| GPU strings a service worker reports | **NOT REACHED.** Gated on a binary, per the ticket. §4 explains why the step's premise needs re-deriving first, and §5 is the measurement that settles it |

**Nothing was migrated and nothing shipped.** No release, no change to what users
install, no switch of substrate. Nothing was tuned to make a build go green; no
patch was dropped. The upstream defect (GPU spoofing broken in `148.0.7778.215`)
is **recorded, not fixed** — it is not this ticket's to resolve. Windows and
macOS were not touched.
