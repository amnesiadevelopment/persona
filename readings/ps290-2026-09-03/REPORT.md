# PS-290 — what actually moves between engine versions, and the rule for bumping

**Date:** 2026-09-03 · **Tree:** `origin/main` `7a8a0b6` · **Author:** worker seat

The owner's ask: *"нужно разобраться что в хромиуме что в фф что меняется с
переходом с одной версии на другую... надо замерять и делать вывод как нам
правильно обновлять движки."* — measure what moves between versions on **both**
engines, and conclude a **rule** for how to update them.

This document is that rule (§7) and the measurements it rests on (§2–§6). Where
a surface could not be measured, §8 says so with the reason rather than
estimating.

---

## 0. Summary — the four findings, in order of how much they change the answer

1. **The seven-day red gate is ONE release's movement, not six.** All three
   moved probes land at **firefox-21**. firefox-21, firefox-25 and firefox-26
   are byte-identical to each other on every probe. The gate's "20 → 26" framing
   made this look like six releases of accumulated drift; it is one, and the
   five releases after it moved **nothing**. (§2)
2. **The canvas movement is UPSTREAM's renderer, not our masking failing.** The
   ticket's lead holds, and it is now measured rather than argued from the
   vector list: on Firefox, canvas 2D has no persona spoof over it at all, so a
   changed digest is the engine's own rasteriser output. Attribution: **not
   ours**. (§3)
3. **The `window.innerSize` movement is the HARNESS, not anything a site
   reads about the identity.** It is the recorder's own window geometry, and
   the arithmetic identifies the mechanism exactly. Attribution: **harness**.
   (§4)
4. **The ticket's Gap 1 premise is WRONG in the direction that matters, and
   this is the finding I most want read.** Chromium is *not* unobserved.
   `engine-gpu-variance.yml` has measured the chromium engine **daily,
   unattended, on the unpinned build users actually receive**, for the last 8
   days — 15 seeds, all green — *without* opening a CDP port into anything the
   operator owns. The real chromium gap is narrower and different from the one
   the ticket describes. (§5)

Findings 1 and 4 both point the same way: **the instrument is in better shape
than the ticket believed, and the reason the gate looks broken is that it
reports a six-version comparison it never actually performed.**

---

## 1. What was measured, and how

The gate uploads both recordings as a CI artifact on every red run
(`Keep the recordings when the gate stops the bump`). Seven consecutive red runs
had therefore **already produced a per-release measurement series and left it on
the shelf** — nobody had to run six sequential provision-and-record rounds,
which the confirm review correctly costed as the expensive way to answer Gap 3.

All eight runs' artifacts are committed beside this report under
`artifacts/<run-id>/`, so every number below is re-derivable without CI access.

| run | date | before | after | app |
|---|---|---|---|---|
| 33098550615 | 2026-08-27 | firefox-20 | firefox-21 | 3.0.1 → 3.0.2 |
| 33198944706 | 2026-08-28 | firefox-20 | firefox-21 | 3.0.1 → 3.0.2 |
| 33252667879 | 2026-08-29 | firefox-20 | firefox-21 | 3.0.2 → 3.0.3 |
| 33309232380 | 2026-08-30 | firefox-20 | *(no after — see §8.3)* | — |
| 33395278014 | 2026-08-31 | firefox-20 | firefox-25 | 3.0.2 → 3.0.3 |
| 33502080628 | 2026-09-01 | firefox-20 | firefox-26 | 3.0.2 → 3.0.3 |
| 33621947004 | 2026-09-02 | firefox-20 | firefox-26 | 3.0.2 → 3.0.3 |
| 33746820013 | 2026-09-03 | firefox-20 | firefox-26 | 3.0.2 → 3.0.3 |

Comparisons below are **symmetric** — over the union of both sides' probe sets,
not one side's — which is what surfaced §6. An asymmetric walk silently drops a
probe present on only one side, and that is exactly the defect the gate's own
`plant_absent_probe` self-test exists to refuse.

---

## 2. Gap 3 answered: the drift is entirely at firefox-21

Every one of the seven comparable runs reports the **same three probes** and the
**same before/after values**:

| realm/probe | firefox-20 | firefox-21 / 25 / 26 |
|---|---|---|
| `window/canvas.readback` | digest `4242351214` | digest `2735004646` |
| `worker/canvas.readback` | digest `4242351214` | digest `2735004646` |
| `window/window.innerSize` | inner 1152x808, outer 1166x927 | inner 1280x815, outer 1294x906 |

And directly comparing the *after* sides against each other:

```
firefox-21 vs firefox-25 → 0 probe differences
firefox-21 vs firefox-26 → 0 probe differences
```

**So the answer to "WHICH release moved a vector" is: firefox-21, and only
firefox-21.** Releases 22, 23, 24, 25 and 26 moved nothing observable to this
instrument. `bytes: 8192` and `mid: 6144` are unchanged on both sides of the
canvas move, which is the probe's own self-check saying the draw is still a
real mid-range draw and not a collapsed black/white surface — the digest moved
because the *rasterisation* moved, not because the probe broke.

### 2.1 A free bonus: host-independence is now MEASURED, not merely hoped

`baseline.py` states plainly that the committed reference's machine-independence
"was never shown". Three separate firefox-26 recordings, taken on **three
different GitHub runners on three different days**, are **byte-identical across
all 85 probes** — including all seven `ENV_SENSITIVE_PROBES`. Same for the three
firefox-21 recordings (modulo §6), and all seven firefox-20 *before* recordings.

This does **not** retire `ENV_SENSITIVE_PROBES`: `ubuntu-24.04` runners are a
homogeneous fleet, so this shows stability across *that* fleet, not across
arbitrary hardware. But it does mean the one-host-one-job constraint the gate
imposes on itself is currently stronger than the evidence requires, and the
env-sensitive caveat is not what is keeping the gate red.

---

## 3. Attribution: `canvas.readback` → UPSTREAM's build

The ticket offered this as a lead and demanded it be probed. It holds.

**The masking layer does not touch canvas 2D on Firefox.** Three independent
confirmations, not one:

1. `masking_layer.FIREFOX_VECTORS = (LOCALE, WEBGL, AUDIO)` — canvas absent.
2. The **product's own launch path**, which is what the baseline actually
   records through (`baseline._record_on_firefox` → `spawn_browser`), installs
   exactly four spoofs on the Firefox arm — `_install_spoof` is called for
   `outer-size`, `locale`, `webgl`, and `add_init_script` for audio. There is no
   canvas spoof to be found; `grep -n canvas src/services/browser/invisible_launch.py`
   returns **nothing**. This matters because `FIREFOX_VECTORS` is the *verify
   harness's* vector list, and the baseline does not launch through the harness
   — so quoting that constant alone would have been the right conclusion from
   the wrong file.
3. Prior art agrees: `readings/ps135-2026-08-24/EVIDENCE.md` established that
   three distinct seeds all read `4242351214` on firefox — a *total* collision —
   because "`--fingerprint=` is Chromium-only and the firefox arm returns at
   `process.py:353`, well before it."

So on Firefox the canvas readback **is the engine's own output with nothing of
ours over it**. A changed digest across an engine bump is upstream changing its
rasteriser.

**Attribution: UPSTREAM. Not a masking failure. Not a regression we caused.**

### 3.1 The corollary that is easy to miss, and is the actually-useful part

Because persona does not spoof this vector on Firefox, `canvas.readback` was
**already fully collided across every Firefox profile before the bump**
(PS-135). The digest moving from `4242351214` to `2735004646` moves *every*
Firefox profile to the same new value. It changes what a site sees, but it
**does not change the unlinkability posture by one bit** — the vector was
already worthless as a discriminator on this engine, and it still is.

That is the difference between "the engine changed a byte" and "the engine cost
us something", and it is precisely the distinction the rule in §7 is built on.

### 3.2 The honest limit

I did **not** get a Firefox engine build onto this host (`invisible_core` and
`invisible_playwright` are not installed here, and provisioning one is a CI-side
job). The attribution above rests on reading the launch path plus the committed
PS-135 evidence, not on a fresh two-build Firefox measurement of my own. What I
*did* measure locally is the neighbouring control: the same probe expression on
stock Chromium 151 reads `2616755061`, and moves to `3022900387` only under
`--force-device-scale-factor=2` — i.e. the digest tracks the **rasteriser and
its scale**, and is indifferent to `--lang` and to font hinting flags. That is
consistent with a renderer-authored value and inconsistent with a seed-authored
one, on a *different* engine, which is corroboration and not proof.

---

## 4. Attribution: `window.innerSize` → the HARNESS

The probe's own inventory note already says it:
`"Window chrome geometry; differs with a resized window, not a spoof change."`
Its `variance` is `shared` — it is not a seed-derived vector at all.

The arithmetic identifies the mechanism precisely:

| | innerW | innerH | outerW | outerH | outerW−innerW | outerH−innerH |
|---|---|---|---|---|---|---|
| firefox-20 | 1152 | 808 | 1166 | 927 | **14** | **119** |
| firefox-26 | 1280 | 815 | 1294 | 906 | **14** | **91** |

`_outer_size_override_script()` pins `outerWidth = innerWidth + 14` and
`outerHeight = innerHeight + 91`. The width offset is 14 on both sides — the
override is live and working on both engines. The **height** offset is 119 on
firefox-20 and exactly **91** on firefox-26: on the newer engine the override's
own constant is what is observed, while on firefox-20 something added a further
28px. Either way this is *our own override* reading the *harness window*, not a
site-visible identity vector.

The width change (1152 → 1280) is the recorder's window being sized differently
under Xvfb, which `_seed_window_size` derives from `_work_area()` — and
`_work_area()` **returns `(0, 0)` on non-Windows**, so on the Linux CI runner
the seeding is skipped entirely and the window size is whatever the engine
chooses by default. A newer engine choosing a different default window size is
the entire content of this "drift".

**Attribution: HARNESS. Nothing a site reads about the identity moved.**

⚠️ This probe will keep reddening this gate on any engine bump that changes the
default window size, forever, for no security reason. §7.4 says what to do about
that — and deliberately does **not** propose deleting the probe, because on a
*continuity* question ("is this profile still itself after a restart?") window
geometry is legitimately interesting.

---

## 5. Gap 1 re-measured: Chromium IS observed — the ticket's premise is wrong

The ticket states Chromium engine changes "are unobserved by any gate", and the
confirm review verified the two *code* claims underneath it. Both of those code
claims are true. **The conclusion drawn from them is not**, because both point
at the same file (`baseline.record_snapshot`) and the chromium engine is watched
by a **different workflow entirely**.

`.github/workflows/engine-gpu-variance.yml` runs **daily at 06:40 UTC**,
unattended, and:

- downloads **whatever `/releases/latest` is serving** — deliberately unpinned,
  "the same bytes `updater.fetch_latest()` hands the operator's app";
- launches **15 chromium profiles** differing only by seed, masking layer OFF;
- verdicts the GPU identity variance, and **fails the job** on either a finding
  (exit 1) or a failure-to-look (exit 2).

Last 8 runs: **all green.** Run 33750895198 (2026-09-03) read
`upstream latest: 148.0.7778.215`, installed it, and got 15/15 seeds with
visibly varied GPUs (AMD Radeon / Intel Iris Xe / NVIDIA RTX 4060 / Intel Arc).

### 5.1 And it does this WITHOUT the trade PS-237 refused

This is the part that matters for the ticket's standing instruction not to
resolve the chromium gap by launching a scratch chromium with a debug port.

`verify/transport.py` attaches to an operator's already-running session and must
never open a port — that refusal is correct and is what PS-237 settled.
`chromium_tier.ChromiumSession` is a **different animal**: it launches a
**throwaway** engine into a `tempfile.mkdtemp()` user-data-dir with
`--remote-debugging-port=0` (ephemeral, unguessable, read back from
`DevToolsActivePort`), and `shutil.rmtree`s the whole thing at the end.
Its own docstring states the boundary: *"Nothing in the operator's profile store
is created, read or mutated."* It never sets or persists `ai_control`.

The Invariant #0 trade PS-237 refused is **"open an unauthenticated control
channel into a profile the operator owns."** A throwaway profile in a temp
directory that is deleted at the end of the run is not that profile. The
distinction is already load-bearing in this tree — it is what lets the
GPU-variance job exist at all — and it is why the chromium gap is **not** the
dead end the ticket assumed.

### 5.2 What the chromium gap ACTUALLY is

Chromium coverage is real but **narrow**: the daily job reads exactly **one
vector** (the WebGL GPU identity pair) on exactly **one arm** (`windows`). The
other 84 probes in the inventory are not read on chromium by any scheduled job.

So the honest statement is not "Chromium is unobserved". It is:

> **Chromium's engine updates are observed on one vector, daily, on the build
> users actually receive. The remaining ~84 vectors are unobserved on chromium
> — not for an isolation reason, but because no job reads them.**

That is a materially more actionable finding than "cannot be measured without
weakening isolation", and it is a *smaller* piece of work than the ticket
budgeted for. §7.5 states the rule; §8.1 records what remains uncovered.

### 5.3 One asymmetry worth stating plainly

The two engines are watched on **opposite axes**, and neither covers the other's:

| | Firefox | Chromium |
|---|---|---|
| watched by | `engine-autoupdate.yml` (the bump gate) | `engine-gpu-variance.yml` |
| pinned? | **yes** — `engine-baseline.txt` | **no** — deliberately unpinned |
| axis | continuity (85 probes, 1 profile) | unlinkability (1 vector, 15 seeds) |
| fires on | our bump | upstream publishing |

A Firefox build that broke *unlinkability* would pass the bump gate (one
profile cannot collide with itself). A Chromium build that broke *continuity*
would pass the variance job (it never compares against a reference). Each engine
is blind on exactly the axis the other is watched on.

---

## 6. An instrument defect found while measuring: inventory drift is invisible

Comparing the firefox-21 recordings against each other **symmetrically** turned
up something the gate did not report:

```
run 33098550615 (2026-08-27):  83 probes  (window 48, worker 35)
run 33252667879 (2026-08-29):  85 probes  (window 49, worker 36)
                                → realm.frameIdentity present in the later, absent in the earlier
```

`realm.frameIdentity` was added to the inventory by `bdc96f2` (PS-232,
2026-08-28) — **between** those two runs. The probe *set* changed underneath the
gate.

The gate did not miss this, and to be precise about why: within a single run
both recordings are taken from the same tree, so the two sides always agree on
the inventory and there is nothing to report. `diff_snapshots` walks the union
and would have reported an `added`/`removed` entry correctly. **The blind spot
is across runs, not within one** — nothing tells an operator comparing today's
red run to last week's that they are reading a different instrument.

This matters directly for the rule: "the same three probes moved again today" is
only reassuring if the probe set is the same. §7.6 handles it.

---

## 7. THE RULE — when an engine bump may be taken, and when it must be refused

The deliverable. Each clause names the evidence it rests on.

### 7.1 A bump is never taken on a green gate alone; it is taken on an ATTRIBUTED diff

Exit 0 means "nothing moved" and may be taken unattended — that is today's
behaviour and it is correct. A **non-empty** diff is never auto-accepted, and is
never auto-refused-forever either. It is **attributed**, per moved probe, to one
of three causes:

| attribution | meaning | disposition |
|---|---|---|
| **ours** | a persona spoof stopped landing, or changed | **REFUSE.** This is a masking regression; the bump is not the fix. |
| **upstream** | the engine changed its own output on a vector we do not author | **May be accepted** — see 7.2 for the test. |
| **harness** | the recorder's own environment (window geometry, display, host) | **Accept and fix the instrument** — see 7.4. |

The attribution question is mechanical, not a judgement call: **does persona
install a spoof over this vector on this engine?** For Firefox, the answer is
the four spoofs `_install_spoof`/`add_init_script` place in
`invisible_launch.py` (outer-size, locale, webgl, audio) — and nothing else.
Everything outside that set is upstream's or the harness's by construction.

### 7.2 An UPSTREAM movement is accepted only when it does not cost a level of the bar

A vector we do not author moving is not automatically harmless. The test is
whether it degrades the three mandatory levels (PS-1):

- **Level 1 (no hardware leaks)** — does the new value expose the real host?
  For `canvas.readback` on Firefox: no. The value is renderer-derived and
  identical across every profile, so it discloses the *engine build*, not the
  machine. (`webgl.unmasked` — which *would* be a host disclosure — is
  unchanged across the whole 20→26 span.)
- **Level 2 (mutual unlinkability)** — does it make two profiles more alike?
  For `canvas.readback` on Firefox: **it cannot.** They were already 100%
  identical on this vector (§3.1). A vector that is already fully collided
  cannot be made more collided.
- **Level 3 (checker matrix)** — unchanged by this bump; no checker reading was
  re-taken here (§8.2).

**Verdict for the current bump (firefox-20 → firefox-26): the three moved probes
cost none of the three levels, and the bump is SAFE TO TAKE.** One is the
harness (§4) and two are a single upstream rasteriser change on a vector persona
does not author and which was already fully collided (§3).

⚠️ **This verdict is a recommendation to a human, not an action I took.** The
committed reference has **not** been re-recorded — the ticket forbids it and the
gate's own message reserves it as a deliberate human act. Accepting this bump
means a person running `baseline_cli record` and committing the artifact in a
reviewable diff.

### 7.3 Bump ONE release at a time; never let the pin fall behind

The strongest operational lesson here. Because the pin sat at firefox-20 while
upstream reached firefox-26, the gate reported a **six-version** comparison and
could not say which release was responsible — and the true answer (§2) is that
**five of those six releases moved nothing at all**.

A six-version diff is not six times as informative as a one-version diff; it is
strictly *less* informative, because it destroys attribution. And it compounds:
every day the pin stays behind, the eventual diff spans more releases and gets
harder to attribute, which makes it likelier to be deferred again.

**Rule: when the gate goes red, the pin is bumped to the FIRST refused release
and the movement is attributed there.** Accepting firefox-21 (with the reference
re-recorded) would have let the next six days' runs each compare a single
release step — and given §2, all five would have been green.

### 7.4 A harness-attributed movement is fixed in the INSTRUMENT, never accepted as identity drift

`window.innerSize` will red this gate on every engine bump that changes the
default window size, permanently, for no security reason — and a gate that is
routinely red for a known-benign reason is the failure mode both
`engine_gate.py` and `engine-gpu-variance.yml` explicitly write themselves
against ("a gate that is always red is a gate people learn to ignore").

Two candidate remedies, and I am deliberately **not** implementing either here —
both change what the gate *decides*, which is beyond a measurement-and-reporting
ticket, and the choice between them is an architectural call that is not a
worker's to make:

- **(a)** Pin the recorder's window size explicitly for the gate's recording, so
  the geometry is an input rather than an observation. Preferred: it keeps the
  probe honest on the continuity axis while removing it as a source of engine
  noise.
- **(b)** Declare the probe environment-sensitive, alongside
  `ENV_SENSITIVE_PROBES`. Cheaper, but weaker — it annotates the noise rather
  than removing it, and `ENV_SENSITIVE_PROBES` currently means *host*-dependent,
  which is a different claim from *harness-window*-dependent.

**Filed as a follow-up, not done here.** (§9)

### 7.5 The chromium rule: the axis that is watched is the axis that is covered

Chromium updates arrive **unattended and hourly** on the operator's machine
(`_check_engines_periodic` → `updater` → `/releases/latest`, with
`KNOWN_BAD_VERSIONS = frozenset()` and no ceiling). There is no pin and no
pre-install gate, and building one is circular — you cannot seed-vary a build
you have not installed.

So the chromium rule is **detection-and-blocklist**, not gate-and-refuse:

1. The daily variance job is the detector. A red run means naming the bad tag in
   `policy.KNOWN_BAD_VERSIONS`, which every install already passes through and
   which refuses a build **by name without waiting for a persona release**.
2. The exposure window is **up to ~24h** (hourly install poll vs daily
   detection). This is a known, accepted, *stated* gap — not a new finding.
3. **Coverage is one vector on one arm.** Any claim that "chromium is covered"
   must be read as "chromium's GPU identity variance is covered". Continuity —
   the question the Firefox gate answers — is **not** watched on chromium at
   all (§5.3).

### 7.6 Every recorded verdict must carry the instrument's own version

§6 showed the probe set changing between two runs with nothing announcing it.
Two red runs that "moved the same three probes" are only comparable if they
measured the same probes.

**Rule: a gate verdict states the size of the inventory it ran over.** Cheap,
purely additive to the report, and it makes cross-run comparison honest. This
one *is* implemented in this PR (§9) — it is reporting, squarely in scope.

---

## 8. Uncovered surfaces — stated with reasons, not estimated

### 8.1 Chromium continuity (the other ~84 probes)

**Uncovered. Not for an isolation reason.** §5.1 establishes that a throwaway
chromium *can* be launched and read within Invariant #0 — `chromium_tier` does
it daily. What is missing is a job that records the 85-probe inventory on
chromium either side of an engine change, and a chromium counterpart to
`engine-baseline.txt` to compare against.

The genuine obstacle is different from the one the ticket assumed, and should be
recorded as such: chromium has **no pin to bump**, so there is no "before" and
"after" *event* to hang a gate on. A chromium continuity check would have to
compare against a **committed reference** — which is exactly the design
`engine_gate.py` rejected for the Firefox gate ("permanently red for reasons
having nothing to do with the engine"), and it would run into the seven
`ENV_SENSITIVE_PROBES` head-on.

§2.1's byte-identical result across three runners is genuinely encouraging for
that design's feasibility on a homogeneous CI fleet. It is **not** sufficient
evidence to build on, and I am not proposing the job here.

### 8.2 Gap 2 — the measurement is still one profile deep

**Not closed.** The gate records one pinned profile (`persona-fingerprint-baseline`:
windows / desktop / firefox / 1920x1080 / no proxy). A vector that moves on a
Windows-declaring profile and holds on a Linux-declaring one is still invisible.

Two things narrow this more than the ticket assumed, and one that does not:

- On Firefox, `os_type` is **not** a free variable: `coherent_engine` routes
  every non-Windows-desktop profile to **chromium**. There is no such thing as a
  Linux-declaring Firefox profile in this product, so the specific example in
  the ticket's Gap 2 cannot occur on the gated engine.
- The declared-machine axis therefore belongs to the **chromium** side, where it
  is already partially exercised (`ENGINE_AUTHORED_IDENTITY_ARMS`, today
  `windows` — one arm).
- What *is* genuinely uncovered is the **seed** axis on Firefox: one seed is
  recorded, so a movement that depends on the seed would be missed. Note that
  for the vectors persona actually authors on Firefox (locale, webgl, audio) the
  seed axis is covered elsewhere by `pool_depth`, and for canvas it is moot
  (§3.1). Recording the gate at 2–3 seeds is the cheapest real widening
  available, and is filed as a follow-up rather than done here.

### 8.3 Run 33309232380 (2026-08-30) has no *after* recording

Its `before` is byte-identical to every other firefox-20 recording, so nothing
is lost from the series. The run failed before the second recording was taken —
whatever the cause, it exited via a path that produced no `after`, which the
gate correctly treats as a refusal rather than a pass. Not investigated further;
out of scope, and it does not affect any conclusion here.

### 8.4 No Firefox engine was launched on this host

Stated in §3.2. `invisible_core` / `invisible_playwright` are absent from this
container. Every Firefox reading in this report comes from the committed CI
artifacts; the only browser I launched myself was stock Chromium 151, and that
only as the cross-renderer control described in §3.2 — it is **not** persona's
engine and answers nothing about the product on its own.

---

## 9. What changed in the tree with this report

Deliberately minimal, and confined to **measurement and reporting** — the
ticket's scope fence. Nothing here changes a spoof, and nothing re-records the
committed reference.

1. **`engine_gate.gate()` now states the inventory size behind its verdict**
   (§7.6) — one line on every outcome, pass or drift: how many probes were
   compared, across how many realms. Makes two runs comparable and makes §6's
   defect visible at a glance instead of requiring an artifact download. Run
   against the archived recordings it reads:

   ```
   inventory: 85 probe(s) across 2 realm(s) on both sides          # today's red run
   inventory: 83 probe(s) ... before, 85 across 2 after — THE TWO
              SIDES DISAGREE ON THE INVENTORY ...                  # the §6 pair
   ```

   Replaying the archived pair through the gate reproduces CI's verdict exactly
   (same three probes, exit 1), which is what makes this report's numbers
   checkable rather than merely quoted.
2. **This report and the eight runs' artifacts**, committed under
   `readings/ps290-2026-09-03/`, so the series is re-derivable without CI access.

Follow-ups filed rather than done, each with its reason:

- `window.innerSize` harness noise (§7.4) — changes what the gate *decides*.
- Chromium continuity job (§8.1) — needs a reference-design decision.
- Multi-seed gate recording (§8.2) — widens the instrument; costs runner time.
