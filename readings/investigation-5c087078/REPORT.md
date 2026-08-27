# Fork or stay downstream: what running our own engine would actually cost

**Investigation:** `5c087078-8fab-4d50-bee4-a392403de69b`
**Measured:** 2026-08-27, all figures re-derived firsthand on that date
**Base:** `persona` @ `7441136`
**Extends:** PS-18 (knowledge article). Corrects it in two places, marked **CORRECTION**.

---

## Recommendation, in one paragraph

**Move our substrate from `adryfish/fingerprint-chromium` to `ungoogled-chromium`, carry our own fingerprint patch set on top of it, and start with Linux only.** The decisive measurement is not the security gap and not the build bill — it is that **the two costs separate far more cleanly than PS-18 could show, and the expensive one is already being paid by someone else who is actually awake.** At a one-major Chromium bump, our 16 fingerprint patches cost **3 conflicts and 6 failed hunks**; the 111 de-googling patches underneath them cost **27 conflicts and 112 failed hunks** — 18.7× more failed-hunk work for a body of work that is not our product. `ungoogled-chromium` maintains exactly those 111 patches, publicly, at a **median 5-day release gap**. `fingerprint-chromium` maintains the 16 we care about and has been **silent for 67 days**, has **never published 148's source at all**, and is not answering a July bug report saying the GPU spoof is broken in **the exact build we ship**. We are currently downstream of the dormant half of the stack and upstream of nothing.

This is a lean, not a menu. The argument against it is in §8 and it is a real one.

---

## 1. Two corrections to PS-18 before anything else

PS-18 told anyone acting on it to re-derive the release dates first. Doing so produced two findings that change the shape of the decision.

### CORRECTION 1 — 148's source does not exist. It is not "not yet released"; the tag is empty.

PS-18 says the newest obtainable source is `144` because "no version after 148 has been published". The mechanism is different and worse. There **is** a `148.0.7778.215` git tag. It points at the same commit as `main` — `3f61b0d` — and that entire tree is **four files**:

```
LICENSE   README-ZH.md   README.md   qqgroup.png
```

Every other tag carries a real source tree. Census of all 13 tags (`data/source-lag.json`):

| tag | files in tree | `chromium_version.txt` |
|---|---|---|
| 129 … 144 (12 tags) | 198 – 217 | present, matches tag |
| **148.0.7778.215** | **4** | **absent** |

So a reader who goes looking for 148's source finds a tag with the right name and nothing in it. Only **two branches** exist in the whole repository: `main` and `144.0.7559.132`.

### CORRECTION 2 — the "one month later" policy is not being honoured, and has not been for a year

PS-18 quotes the README's delayed-release policy ("patch files will be released when the next version is published (typically one month later)") as the governing constraint. The policy is stated; it is not being kept. Measured lag between a version's **binary** release and the date its **source** actually landed (n=12):

| | days |
|---|---|
| min | −4 (source landed *before* the binary) |
| median | 20 |
| max | **114** |

The trend is monotone and recent: `139` → 100 days, `142` → 66 days, `144` → **114 days**, `148` → **never, 67 days and counting**. "One version late" describes 2025. The current state is "one version late, plus an unbounded tail".

**Everything else in PS-18 that I re-derived held exactly** — the 127-patch split (35 core / 91 extra / 1 upstream-fix), the 16 fingerprint patches at 116,435 bytes, the release cadence table, and the zero service-worker coverage in `011-gpu-info.patch`. PS-18 is a sound floor.

---

## 2. Q1 — How far behind are we, in security rather than version numbers?

**The alarming reading is not supported. Say it plainly: there is no evidence of an actively exploited hole in the build we ship.**

- **Chromium stable today:** `152.0.7977.64` (Linux, 2026-08-25); `153.0.8010.12` (Windows/Mac, 2026-08-26).
- **We ship `148.0.7778.215`**, published 2026-06-21 — **67 days** old.
- **16 desktop Stable security posts** have shipped since. They fix **1,284 distinct CVEs**:

| severity | count |
|---|---|
| Critical | **57** |
| High | 351 |
| Medium | 543 |
| Low | 333 |

- **Exploited in the wild: zero.** No post in that window contains an in-the-wild exploitation statement.
- **CISA KEV: zero.** 69 Chrome/Chromium entries exist all-time; **none** was added after 2026-06-21. The most recent (`CVE-2026-11645`, added 2026-06-09) **predates our build**, so it is fixed in what we run.

**Method note, because the numbers are large enough to distrust:** I did not count CVE identifiers and trust my own regex. Each post states its own total ("This update includes 433 security fixes"), and my extraction was checked against that stated total for **all 16 posts — 16/16 agreed exactly**. The two large figures (433, 370, 327) are milestone-promotion posts, which is where Chrome batches its accumulated fixes.

**What this means for the decision:** the security gap is real in volume and unremarkable in urgency. 57 unpatched Criticals in a browser pointed at hostile sites is not a comfortable position, but it does **not** reframe the fork as a security emergency. **Security is a reason to stay current. It is not, on this evidence, a reason to fork.** If a Chrome KEV entry lands that post-dates our build, this section flips (see §9).

---

## 3. Q2 — What does a Chromium major bump actually cost? (the number the decision turns on)

PS-18 said this could only be learned by attempting a rebase. I attempted one.

### Method, and why the control matters more than the result

I exported all 127 patches from tag `144` in `patches/series` order, extracted the **713 distinct files** they touch, fetched those files from `chromium.googlesource.com` at three tags, and applied the series sequentially, classifying each patch as CLEAN / OFFSET / FUZZ / CONFLICT / NO_TARGET.

**The first run of this harness was wrong, and the control is what caught it.** Applying the 144 patches against the 144 tree — which should be near-perfect — reported **26 conflicts**. Every one had `failed_hunks=0` and "can't find file to patch": my fetcher was running 24-way parallel and silently losing files to rate limiting. Serial retries returned HTTP 200 for the same paths. I rebuilt the fetcher with retries and 6-way parallelism and re-ran. **No number below comes from the first harness.**

### The result

| status | control @144 | **@145 (one major)** | **@152 (eight majors)** |
|---|---|---|---|
| CLEAN | 119 | 39 | 15 |
| OFFSET | 3 | 52 | 26 |
| FUZZ | 0 | 3 | 8 |
| **CONFLICT** | **2** | **30** | **75** |
| NO_TARGET | 3 | 3 | 3 |

The control's residual 2 CONFLICT + 3 NO_TARGET is the **instrument floor** (paths created by earlier patches or by ungoogled's generators, which upstream does not have). So roughly **28 of 30** conflicts at one major, and **73 of 75** at eight, are attributable to real upstream drift.

**A one-major bump is not a monthly afternoon and not a monthly week.** 30 patches need manual attention, but 52 more apply with nothing worse than line-offset drift, which is automatic. The honest characterisation of a single bump is **a focused day or two of patch work** — *before* compilation, which I could not measure (§10).

### The two costs, separated — this is the finding

The brief insisted these never be conflated. Separating them is what produced the recommendation:

| at **one** major bump (144→145) | patches | CONFLICT | failed hunks |
|---|---|---|---|
| **Our product** — the 16 fingerprint patches | 16 | **3** | **6** |
| **The distributor tax** — the other 111 | 111 | **27** | **112** |

| at **eight** majors (144→152) | patches | CONFLICT | failed hunks |
|---|---|---|---|
| **Our product** — 16 fingerprint | 16 | 9 | 17 |
| **The distributor tax** — 111 | 111 | 66 | 468 |

**Carrying the masking layer is cheap and stays cheap. Carrying the de-googling layer is 18.7× more failed-hunk work at one major and 27.5× at eight.** PS-18 guessed this; it is now measured.

Two patches worth naming individually:

- **`011-gpu-info.patch` — the PS-189 patch — survives all eight majors at `OFFSET` only.** It has never needed manual rebasing across the entire span. Whatever we build on top of it inherits that stability.
- `005-hardware-concurrency-fingerprint.patch` is `CLEAN` at both tags.

Also measured: **31 of the 713 touched files were deleted or moved upstream** between 144 and 152 (`data/deleted-144-to-152.txt`). Those are the ones that need a human deciding what the patch now means, not just where it goes.

---

## 4. Q2b — The brief's prediction about Blink is refuted

The brief predicted breakage would concentrate in Blink's most-churned areas, and asked that it be tested rather than assumed. **It is false.**

Per-patch conflict rate at 152:

| | n | CONFLICT | rate |
|---|---|---|---|
| patches touching `third_party/blink/` | 21 | 13 | 61.9% |
| patches not touching Blink | 106 | 62 | 58.5% |

A 3.4-point difference on a 21-patch sample is not concentration. Attributing all **656 failed hunks** to the directory they landed in makes the picture unambiguous:

| area | failed hunks | share |
|---|---|---|
| `chrome/browser/` | 382 | **58.2%** |
| `components/` | 157 | 23.9% |
| `chrome/` (other) | 32 | 4.9% |
| **`third_party/blink/`** | **30** | **4.6%** |
| `content/` | 23 | 3.5% |
| everything else | 32 | 4.9% |

The worst individual files are `chrome/browser/BUILD.gn` (12), `components/signin/internal/identity_manager/primary_account_manager.cc` (12), `chrome/browser/signin/signin_promo_util.cc` (10), `components/safe_browsing/core/browser/safe_browsing_metrics_collector.cc` (10).

**The pain is in signin, safebrowsing, sync and policy — that is the de-googling surface, not the rendering engine.** This independently confirms §3's split from a second direction: the expensive work is ungoogled's, not ours, and it is expensive precisely because it fights Google integration code that Google keeps changing.

---

## 5. Q3 — Is upstream dormant, dead, or just slow?

**Dormant at best, and contributing upstream is not merely unlikely — it is structurally impossible in the current repository layout.**

- **The maintainer's last public word anywhere in the repository was 2026-06-21** — 67 days ago, the same day as the last release, when they answered seven issues in one burst and then stopped.
- **Four issues have been opened since. All four have zero maintainer replies.**
- **Issue #88, "Broken GPU spoofing in 148.0.7778.215"** (2026-07-25, still open, **zero comments**) reports that in the exact build we ship, `chrome://gpu` logs `eglCreateContext: Requested version is not supported` and Cloudflare Challenge pages enter an infinite refresh loop. The reporter's workarounds are `--disable-spoofing=gpu` / `--disable-gpu` / `--use-angle=swiftshader`. **A third party is independently reporting that the GPU masking in our shipping engine is broken, and upstream has not acknowledged it in a month.**
- The repository is not archived: 2,991 stars, 428 forks, 26 open issues, BSD-3-Clause.

### Contributing upstream is theatre, and here is the mechanism

**Only two pull requests have ever been opened. Both were closed unmerged.** PR #86 (2026-07-09) was an outside attempt to supply the missing 148 source; it was closed the same day.

The reason is structural, and I verified it directly with `git merge-base`:

```
merge-base(main, tag 144) -> none          parents of tag 144: (none — orphan commit)
merge-base(tag 142, tag 144) -> none       parents of tag 142: (none — orphan commit)
```

**Every source tag is an orphan commit sharing no history with any other.** The repository is not a development history; it is a series of unrelated source dumps. That is why issue #41 ("How to contribute patches") reports GitHub saying *"There isn't anything to compare. main and my-branch are entirely different"* — and why the only answer it ever received, 5 months later, was from another user, not the maintainer.

**PS-18's Option 4 (contribute upstream) should be struck.** There is no branch to target, no PR has ever been merged, and nobody is reading.

---

## 6. Q4 — The real build pipeline bill

PS-18 asserted "the cost is becoming a Chromium distributor" without pricing it. Priced, from `ungoogled-chromium`'s own public CI, using **per-job** timings:

| platform | jobs chained | median compute per build | median wall-clock | build success rate |
|---|---|---|---|---|
| Windows | 17 | **43.5 h** (min 39.5, max 63.3) | 43.5 h | 33/36 = **92%** |
| Linux (portable/AppImage) | 25 | **32.5 h** (min 30.6, max 34.1) | 19.4 h | 6/6 sampled |
| macOS (arm64 + x86_64) | 43 | **23.9 h** (min 22.4, max 26.8) | 15.4 h | 41/58 = **71%** |

**≈100 compute-hours per release across three platforms.**

Three structural facts fall out of those configs:

1. **No single hosted runner can build Chromium.** Every job in every repo caps at ~5.2 h against GitHub's 6-hour limit. That is *why* Windows chains 17 jobs and Linux 25 — the build is checkpointed to cache and resumed. This is the single most important pipeline fact: hosted CI is usable, but only via a resume-across-jobs harness that must itself be maintained.
2. **macOS already needed paid infrastructure.** `ungoogled-chromium-macos` runs on `depot-macos-latest`, a commercial runner service, not GitHub's free tier — and it is the **least reliable** leg at 71%, currently failing (2026-08-25, 2026-08-26).
3. `fingerprint-chromium`'s own `.cirrus.yml` (`cpu: 8, memory: 32G`) is **code-checking and config validation only** — it never builds Chromium. Their binaries are built somewhere unpublished.

**Storage/bandwidth, measured:** the official source tarball is **1.20 GiB** compressed at 144 and **1.65 GiB** at 152 — a 37% growth in eight majors. Unpacked build trees and `out/` directories are multiples of that; this container has 426 GB free, so disk is not the binding constraint.

**Signing — cited, NOT verified.** macOS distribution requires Apple Developer Program membership plus notarisation, and Windows requires an OV or EV code-signing certificate with annual renewal. **I could not verify the current prices firsthand** (Apple's comparison page did not yield a figure to the fetch), so I am not quoting numbers I did not read. This is a genuine gap in the bill — see §10.

---

## 7. Two things nobody asked for

### A. What comparable products chose

| project | stars | last push | release cadence | strategy |
|---|---|---|---|---|
| `ungoogled-chromium` | 27,529 | 2026-08-21 | **median 5 days** | full distributor |
| `camoufox` | 11,463 | 2026-08-26 | median 33 days | **Firefox fork, engine-level** |
| `undetected-chromedriver` | 12,808 | **2025-07-05** | — | runtime patch |
| `nodriver` | 4,700 | 2026-05-13 | — | wrapper / CDP |
| `patchright-python` | 1,482 | 2026-08-19 | median 53 days | runtime patch |
| `fingerprint-chromium` | 2,991 | **2026-06-21** | 113-day last gap | our current upstream |

Two things are visible. **The runtime-patch strategy decays** — the most-starred example in the category, `undetected-chromedriver`, has not been pushed in 13 months. And **the one project that took the engine-level route, `camoufox`, is the healthiest antidetect project on the list** — more stars than our upstream by 4×, pushed yesterday, shipping monthly. It also demonstrates the cost honestly: it forked Firefox, which is a materially smaller build than Chromium, and it is still a months-of-work undertaking.

**`ungoogled-chromium`'s 5-day median release gap against `fingerprint-chromium`'s 113-day last gap is the single sharpest number in this report.** It is the whole recommendation in one comparison.

### B. Is the extension layer a dead end for a whole class of realm?

**No. It is one realm, for a specific spec reason — not a class.** I tested this rather than inferring it (`scripts/realm_class_probe.py`, run against local headless Chromium):

| realm | `blob:` bootstrap |
|---|---|
| dedicated `Worker` | **accepted** |
| module `Worker` | **accepted** |
| `SharedWorker` | **accepted** |
| **`ServiceWorker`** | **refused** — `TypeError: The URL protocol of the script ('blob:…') is not supported` |

The refusal reproduces exactly what PS-189 recorded. But it is **specific to service workers**, whose spec requires a same-origin, network-fetchable script URL so the registration can outlive the page. Every other worker realm takes the blob bootstrap our injection path depends on.

**This weakens the "engine work is forced" argument and should be reported as such.** It is one realm, not a structural collapse of the extension layer. It does not, however, make the service-worker realm reachable — PS-189's three refusals still stand, and no fourth technique presented itself here.

### Sizing PS-201's Option 3 — it is patch-sized, and the reason is specific

PS-18 suspected this; the mechanism confirms it. `011-gpu-info.patch` is 445 lines across 7 files, and it hooks **exactly two switch cases** in `WebGLRenderingContextBase::getParameter` — `kUnmaskedRendererWebgl` and `kUnmaskedVendorWebgl`. There are **zero** references to service workers, `OffscreenCanvas`, or `WorkerGlobalScope` in **any** of the 16 fingerprint patches (verified by grep over each).

The decisive detail is the hook's **input**:

```cpp
const base::CommandLine* command_line = base::CommandLine::ForCurrentProcess();
```

**The spoofed identity is derived from process-global command-line state, not from per-realm context.** So extending coverage to another realm requires **no new plumbing to carry identity across a realm boundary** — the value is already reachable from anywhere in the process. It needs an additional call site where the service-worker path reads GPU strings. `016-webgl-readPixels.patch` uses the identical process-global pattern.

**That makes Option 3 a patch of the same shape and size as one that already exists and has survived eight Chromium majors at `OFFSET`.** It is the cheapest of PS-201's three options in engineering terms — its cost was never the patch, it was owning a build.

---

## 8. The strongest argument against this recommendation

**Stated as well as I can make it, because it is not weak.**

**The bus factor is one, and the cadence is unforgiving.** A Chromium major lands roughly monthly. Our measurement says each one costs ~30 patch conflicts and ~100 compute-hours across three platforms — *before* a single compile error. If the one person who owns this is unavailable for a month, we miss a major; miss two and we are exactly where `fingerprint-chromium` is now, except the dormant upstream is us and there is nobody downstream of us to notice. **We would have converted a dependency risk into a key-person risk, and key-person risk is the one we cannot escalate to anybody.**

Three supporting points:

1. **My numbers are a floor, and the part I could not measure is historically the expensive part.** Textual application is not compilation. A patch that applies at `OFFSET` can still break the build when a function it calls changed signature. Real cost is strictly greater than 30 conflicts/major — possibly much greater, and I cannot bound by how much.
2. **The security case does not support urgency.** Zero in-the-wild exploitation and zero new KEV entries (§2) mean the honest reading is that we are *stale*, not *breached*. Taking on a build pipeline for a stale-but-unexploited browser is a large cost against a risk that has not materialised.
3. **macOS is a real hole.** The reference implementation runs at 71% success on a *paid* runner and is failing right now. Signing costs are unverified (§10). Our worst-supported platform under the current arrangement would remain our worst-supported platform, at higher cost.

**The honest counter-position:** stay downstream, take the `--disable-spoofing=gpu` workaround from issue #88 if the Cloudflare loop bites, and accept a known Invariant #0 violation on `chromium/linux` — which is what PS-201 asks the owner to decide anyway. That position is defensible on this evidence. What it costs is that **we can never fix a leak we find**, because the only actor who could is not answering, and no PR has ever been merged.

---

## 9. Decision point — what would have to become true to flip this

| if this becomes true | effect |
|---|---|
| **Upstream publishes 149 *with* source, and answers #88** | **Recommendation weakens substantially.** A responsive upstream shipping current source restores the cheap option. Note that shipping 149 *without* source, or with an empty tag like 148's, changes nothing. |
| A Chrome CVE dated **after 2026-06-21** enters CISA KEV, or a post reports in-the-wild exploitation | §2 inverts. Security becomes the driver, urgency goes up sharply, and staying on a 67-day-old build stops being defensible regardless of build cost. |
| We attempt one Linux build and it exceeds ~2 days of human time to green | Stop. The floor in §3 was optimistic and the distributor cost is real for us as well. |
| `ungoogled-chromium`'s cadence lapses toward `fingerprint-chromium`'s | The core premise dies — we would be adopting a second dormant upstream. **Re-check the 5-day median before committing.** |
| Owner rules that macOS/Windows parity is required from day one | The phased Linux-first plan does not apply; the bill is ~100 compute-hours per release plus unverified signing costs from the start. |

**Concrete next step, sized:** reproduce **one Linux build** of `ungoogled-chromium` + our 16 patches at Chromium 152. Linux is the cheapest leg (32.5 h compute), the most reliable (6/6 sampled), the arm where Invariant #0 is currently open, and the arm where PS-201's Option 3 would be validated. That single experiment converts every "lower bound" in §3 and §10 into a real number, and it is reversible — nothing is committed by trying it.

---

## 10. What I could not establish, and why

Modelled on PS-18 §7, because it is the right model.

- **Compilation cost. The largest gap.** Everything in §3 is *textual patch application against a 713-file subset*, never a compile. A patch applying cleanly is necessary, not sufficient. **Every rebase figure here is a lower bound**, and the unmeasured remainder is where rebase work historically goes.
- **Tree completeness varies slightly by tag** — 682/713 files at 144, 680 at 145, 651 at 152 (the rest are genuine upstream 404s: paths created by earlier patches, or generated by ungoogled's tooling). The control quantifies the residual effect at 2 CONFLICT + 3 NO_TARGET, but a handful of the @152 conflicts may be fetch artefacts rather than drift. The direction of the result is far larger than that error.
- **Signing costs.** Not verified firsthand and therefore not quoted. Needs: current Apple Developer Program fee, notarisation workflow, Windows OV vs EV certificate pricing and renewal. This is a real line item missing from §6.
- **Whether anyone else builds from these tags.** 428 forks exist; I did not establish whether any produces binaries. A healthy third-party build community would materially change §5's "dormant" reading.
- **I did not open an issue upstream.** The brief suggested asking when 148's source lands, and noted the ask is itself the measurement. I judged it wrong to post to a third-party repository as an autonomous agent. The existing record answers it anyway: #41 (contribution blocked, 5-month-old unanswered question), #86 (148 supplied by an outsider, closed), #88 (our shipping build's GPU spoof reported broken, ignored for a month). **If the owner wants the direct test, posting it is a one-line action and I would defer to them on making it.**
- **The 76.9% / 58.7% macOS collision discrepancy flagged in PS-201** is untouched here — out of scope, and it belongs to whoever acts on that decision.
- **Windows/macOS behaviour of our own layer** was not measured; this investigation is about the engine, and PS-16 owns the matrix.

### One instrument failure worth recording

Two figures in my raw output were artefacts, caught and discarded rather than reported: computing build wall-clock as `updated_at − run_started_at` produced a **9,676-hour** macOS build (403 days) and a **negative** one, because re-runs and artifact expiry bump `updated_at` long after a build ends. All §6 numbers come from **per-job** `started_at`/`completed_at` with a sanity filter. `data/build-wallclock.json` retains the bad values beside `data/build-summary.json` so the correction is auditable.

---

## Reproduction

Everything in this report re-derives from `scripts/` and `data/`. See `README.md` for the commands. The two most load-bearing artefacts:

- `data/results-ctrl-144b.tsv` — **the control.** Read this before trusting `results-test-145.tsv` or `results-test-152.tsv`. `data/results-ctrl-144-BROKEN-HARNESS.tsv` is the discarded first run, kept deliberately so the failure mode is inspectable.
- `data/realm-class-probe.json` — the §7B result, reproducible offline with `python3 scripts/realm_class_probe.py`.
