# PS-137 — re-reading Chromium against pixelscan on a tier that places its own exit

Taken 2026-08-24, one session, five runs. Everything here is a **measurement**.
Where a question could not be answered this document says so and says why,
rather than recording the absence as a pass.

---

## 0. The gate — verified before anything was read

PS-137 exists because a reading was once trusted about a configuration it did
not have. So the first thing checked was the **tree**, not the branch name:

| check | result |
|---|---|
| `origin/main` | `49a761d` — **is** PR #104's merge commit (`mergedAt 2026-08-24T04:06:44Z`) |
| `timezone` in `src/services/verify/chromium_tier.py` | **12 hits**, incl. `:339` `args.append(f"--timezone={timezone}")` |
| `observed.timezone` guard under `src/services/verify/` | present — `checker_cli.py:617` |

The middle two are load-bearing: they read the tree, so they cannot be fooled by
a stale status field.

**The discriminator, fixed before any run.** This container's own zone is
**UTC+0**; the exit's is **Europe/Warsaw**. PS-128 recorded `Africa/Abidjan`
(UTC+0) — the container's clock. A run reporting `Europe/Warsaw` is therefore
measuring something PS-128 could not.

### The environment, and the caveat that bounds every Chromium row

| fact | measurement | consequence |
|---|---|---|
| exit | `83.175.185.209` · Warsaw / PL · AS9141, via `socks5h://` | asserted non-empty before use; no direct-connect fallback |
| `/dev/shm` | **64 MiB** | PS-133's exact condition is **live here** — a crashed arm is never read as a clean one |
| user namespaces | `unshare(CLONE_NEWUSER)` → **EPERM** | `--allow-unsandboxed-chromium` **required** |
| FUSE | `/dev/fuse` absent | `--appimage-extract-and-run`; already in the tree, not a defect |

⚠️ **Every Chromium row below was taken with `--no-sandbox`.** By that flag's own
documentation such a reading **is not the product's default surface**. Stated
here because it bounds every Chromium answer in this document.

---

## 1. The three adverse verdicts — per row, on three thick records

Each record below cleared the evidence floor **and** had pixelscan actually
contributing (24/28 fingerprint rows, 4 checkers).

| row | PS-128 | seed 9001 | seed 1337 | seed 2024 | verdict |
|---|---|---|---|---|---|
| `timezone_from_js` | `Africa/Abidjan` ⚠ | `Europe/Warsaw` | `Europe/Warsaw` | `Europe/Warsaw` | ✅ **CLEARED** |
| `timezone_spoofed` | **True** ⚠ | absent | absent | absent | ✅ **CLEARED** |
| `masking_detected` | **True** ⚠ | **True** ⚠ | **True** ⚠ | **True** ⚠ | ❌ **SURVIVED** |
| `fingerprint_inconsistent` | **True** ⚠ | **True** ⚠ | **True** ⚠ | **True** ⚠ | ❌ **SURVIVED** |
| `geo_country_city` | absent | `Poland / Warsaw` | `Poland / Warsaw` | `Poland / Warsaw` | ✅ correct |
| `automation_detected` | absent *(good)* | absent | absent | absent | unchanged |
| `proxy_detected` | absent *(good)* | absent | absent | absent | unchanged |
| `fingerprint_consistent` | absent | absent | absent | absent | unchanged |
| `canvas_hash` | `f564d214…` | `acd0adf4…` | **`f564d214…`** | `2af3317f…` | per-seed |
| `webgl_renderer` | `-` ⚠ | `-` ⚠ | `-` ⚠ | `-` ⚠ | unchanged |
| `webgl_vendor` | `-` ⚠ | `-` ⚠ | `-` ⚠ | `-` ⚠ | unchanged |
| `webgl_hash` | absent | absent | absent | absent | unchanged |

**The answer to the ticket's question: the timezone hypothesis does NOT explain
the other two.** With the clock correct (`Europe/Warsaw`) and the geography
correct (`Poland / Warsaw`) on the same page load, pixelscan still returns
`masking_detected` and `fingerprint_inconsistent` — on three different seeds.

That is the more expensive of the two possible answers, and it is now on
evidence rather than assumption. The two verdicts are **not** retracted.

### The control that makes this comparable to PS-128

`canvas_hash` on **seed 1337** reads `f564d214e7f819f1ea5db5324aa5e5d0` —
**byte-identical to PS-128's value for the same seed** — while seeds 9001 and
2024 each produce a different hash. Same seed → same canvas; different seeds →
different canvases. So the profiles are genuinely distinct, the harness is
reproducing PS-128's identity for the seed they share, and the comparison is
like-for-like rather than a coincidence of two unrelated runs.

### Seeds used, and why these ones

PS-128 recorded Chromium **seed 4242 crashing its renderer 3 runs out of 3**
before the page completed. `/dev/shm` is 64 MiB in this container, which is
PS-133's cause, so that crash is expected here too. Seed 4242 was therefore
**avoided by design** rather than retried: **9001**, **1337** and **2024** were
read, all three rendered, and no crashed arm is reported as a clean one.

---

## 2. The seed accounting — PS-128's Firefox split did NOT reproduce

The ticket required this not be quietly dropped because the Chromium answer came
out interesting. Both Firefox arms, read this session (24/28 rows, 4 checkers each):

| row | ff / seed 4242 | ff / seed 1337 | PS-128 ff / seed 1337 |
|---|---|---|---|
| `masking_detected` | absent *(good)* | **absent** *(good)* | **True** ⚠ |
| `fingerprint_inconsistent` | absent *(good)* | **absent** *(good)* | **True** ⚠ |
| `fingerprint_consistent` | **True** ✅ | **True** ✅ | — |
| `timezone_from_js` | `Europe/Warsaw` | `Europe/Warsaw` | `Europe/Warsaw` |
| `geo_country_city` | `Poland / Warsaw` | `Poland / Warsaw` | `Poland / Warsaw` |
| `canvas_hash` | `e74bab01…` | `38ee3a51…` | — |
| `webgl_renderer` | `ANGLE (Intel, …)` | `ANGLE (AMD, Radeon …)` | `ANGLE (AMD, Radeon …)` |

**The unexplained 4242-clean / 1337-adverse split is gone: both seeds now read
fully clean.** PS-128's own note — that the split could not be the timezone,
since both Firefox arms carried the same tier behaviour — is consistent with
this: the variable was never the clock.

### A candidate cause, offered as a lead and NOT as a proven one

| event | timestamp |
|---|---|
| PS-128 firefox/seed4242 reading | `2026-08-23T21:42:42Z` |
| PS-128 firefox/seed1337 reading | `2026-08-23T21:46:11Z` |
| **PS-131 merged** (`dc879d7`) | **`2026-08-24T00:49:36Z`** |
| this session's firefox readings | `2026-08-24T09:44–09:51Z` |

PS-131 is *"cloak the bootstrap's own wrappers on the Firefox window realm"* —
a **real masking leak** in which stringifying `Worker` in the Firefox window
realm returned **2,109 characters of raw patch source** where every real engine
returns `[native code]`. It merged **after** PS-128's Firefox readings and
**before** these.

That is a coherent explanation for a `masking_detected` on Firefox disappearing.
It is **not proven here**: proving it needs PS-128's tree re-read under today's
harness, which this ticket does not scope. Recorded as a lead with its timeline
so the next person does not re-derive it.

**Why the split was seed-dependent at all remains unexplained.** If PS-131 is
the cause, a leak in a shared bootstrap ought to have shown on both seeds. It
did not. That question is **still open** and is stated rather than closed.

---

## 3. ⚠️ The causal question is OPEN — the control arm failed twice

The strongest available test of a surviving `masking_detected` is the
differential: read Chromium **without** persona's masking layer. If the verdict
fires without the layer, it is not the layer's doing. **Both attempts are
unusable, and neither is being read as a result.**

| attempt | outcome |
|---|---|
| run 4 | crashed at the **write** step — `IsADirectoryError`. The browser tier had already been read (26 rows) and was then discarded. |
| run 5 (paired control + treatment, back to back) | **both arms thin**: 7/28 fingerprint rows, 2 checkers, **pixelscan 0/12 rows read** |

A differential in which the checker under test contributed **nothing to either
arm** answers nothing.

Independently disqualifying: the exit **rotated** between the thick runs and the
differential — `83.175.185.209` (AS9141, P4) → `83.24.251.158` (AS5617, Orange
Polska). The CLI states that both arms of a comparison must be taken through the
**same** exit and that an arm which rotated is not a comparison. Even a thick
result there would have needed re-taking.

**So: `masking_detected` and `fingerprint_inconsistent` survive a corrected
timezone — but whether persona's masking layer causes them is NOT established.**

### The harness sharp edge that made this worth checking twice

`evidence: SUFFICIENT` is an **aggregate** floor over all checkers. Three records
this session passed it while pixelscan contributed **zero** rows
(`TargetClosedError: Target page, context or browser has been closed`), the
floor being met by `bot.sannysoft.com` and `iphey.com` instead.

A record can be "sufficient" and **completely silent on the one checker a ticket
is about**. Every such record was discarded rather than read: an
absent-because-unobtainable row is not an absent-because-clean row. The first
seed-1337 arm of run 1 is exactly this shape and is **not** used anywhere above.

---

## 4. The comparison the ticket mandates — tier vs product, on the masking axis

Before reporting any surviving verdict as a product defect: *"does a real launch
do something here that this tier does not?"* Two findings in one day were exactly
that shape. **Three divergences found, and they are not cosmetic.**

### D1 — the product loads a masking extension the tier never builds

`build_chromium_layer` (`masking_layer.py:397+`) builds **ten** vectors:
`native, locale, voice, stealth, measuretext, audio, device, webgl, gpu,
canvas_ctx` — confirmed in the record header (`route=extensions, complete=true`).

`spawn_browser` (`process.py:411+`) appends those **plus**:

| extension | product condition | in tier? |
|---|---|---|
| **`geo`** | **whenever the profile has a proxy** (`process.py:526`) | ❌ **never** |
| `search` | non-Linux only | ❌ (settings override, not masking) |
| `mobile` | mobile profiles only | ❌ (not a checker run) |

The tier's docstring states the `geo` exclusion openly — *"needs proxy
coordinates this harness does not carry"* — so it is a known, documented gap
rather than a bug. **But every reading in this document is of a proxied
profile**, which is precisely the condition under which the product **does**
load `geo_ext`. The product's own comment says that extension exists so
`getCurrentPosition` cannot fall through to the **real host coordinates** while
locale and timezone already say the exit country (audit7 #5).

So the tier presents a browser whose geolocation surface behaves **differently
from the product's**, in exactly the direction a coherence checker looks at —
and `masking_detected` / `fingerprint_inconsistent` are coherence verdicts. This
is the same *shape* as the timezone defect PS-132 fixed: one masking axis the
tier does not carry.

### D2 — the tier opens an unauthenticated CDP port unconditionally

| | |
|---|---|
| tier | `--remote-debugging-port=0` + `--remote-allow-origins=*`, **unconditional** (`chromium_tier.py:353`) |
| product | the same two flags, **only** under `if getattr(profile, "ai_control", False)` (`process.py:695`) |

An ordinary persona profile opens **no** debug port; every tier reading opens one.
Pixelscan's `automation_detected` read **absent** on all three thick records and
sannysoft's `webdriver_advanced_passed` / `webdriver_missing_passed` both read
`True`, so this did **not** produce a visible automation tell — but it is a real
difference between what was measured and what the product ships.

### D3 — `--disable-extensions-except`

Passed by the tier (`chromium_tier.py:402`), passed by the product **nowhere**.
This one makes the tier's extensions *more* reliably loaded, so it does not
explain an adverse verdict; recorded for completeness.

### What this means for the two surviving verdicts

They survived a corrected **timezone**. They have **not** been tested against a
tier that matches the product on **geolocation**. Given that this project has
now had three live findings turn out to be the instrument — PS-119 (locale),
PS-132 (timezone), PS-133 (`/dev/shm`) — and that D1 is the same shape as
PS-132, **the honest state is: the verdicts survive, and the instrument is still
not the product.**

Reported, not fixed. Deciding the fix before knowing which row survived is
guessing, and closing `geo_ext` in the tier is PS-132's kind of work, not this
ticket's.

---

## 5. Summary

| question | answer |
|---|---|
| Does the tier now place its exit? | **Yes** — `Europe/Warsaw` on every run, verified from each run's own record |
| `timezone_spoofed` | ✅ **CLEARED** — retract |
| `timezone_from_js` = `Africa/Abidjan` | ✅ **CLEARED** — now `Europe/Warsaw`, retract |
| `masking_detected` | ❌ **SURVIVED** on 3 seeds |
| `fingerprint_inconsistent` | ❌ **SURVIVED** on 3 seeds |
| Is the survival caused by persona's masking? | ⚠️ **UNKNOWN** — control arm failed twice |
| Does a Chromium profile read fully clean on pixelscan? | **No.** The owner's stated gate is **not** met |
| PS-128's Firefox seed split | **Did not reproduce** — both seeds clean. Lead: PS-131 merged in between. Seed-dependence still unexplained |
| Is the tier now equivalent to the product? | **No** — three divergences, `geo_ext` the material one |

### What the next ticket should do, in order

1. **Close D1** — carry proxy coordinates into the tier so `geo_ext` is built,
   mirroring what PS-132 did for the timezone. Until that lands, a surviving
   coherence verdict cannot be attributed to the product.
2. **Then re-run the differential** — control and treatment, thick, same exit,
   pixelscan contributing in both. That is the measurement that says whether
   persona's layer causes `masking_detected` at all.
3. Only then scope a fix.

### Records

```
run1-chromium/           seed 9001 (thick), seed 1337 (THIN — discarded, pixelscan 0/12)
run2-chromium/           seed 1337 (thick), seed 2024 (thick)
run3-firefox-seedcheck/  seed 4242 (thick), seed 1337 (thick)
run4-chromium-control/   control arm — UNUSABLE (pixelscan 0/12, exit rotated)
run5-differential/       paired control+treatment — UNUSABLE (both arms thin, pixelscan 0/12)
```

The unusable runs are kept deliberately. A failed control arm that is deleted
looks like a control arm that was never attempted.
