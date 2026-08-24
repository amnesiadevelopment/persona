# PS-135 — the live `canvas.readback` reading, taken 2026-08-24

One session, two engines, five seeds, one counterfactual. Everything here is a
**measurement**. Where a question could not be answered, this document says so
rather than recording the absence as a pass.

The ticket's own bound was that *no browser was executed during research* — what
it established was an **asymmetry**, not an observed collision, and it said so.
That gap is what this document closes: both engines were executed here.

---

## 0. The environment, and the one caveat that bounds half of it

| | |
|---|---|
| repo root | `/workspace` (the ticket says `/workspace/persona`; it is wrong) |
| HEAD | `49a761d`, branch `feature/ps135-canvas-readback-probe` |
| firefox | packaged `invisible_playwright/firefox-20`, engine_build `firefox-20` |
| chromium | `~/.persona/engine/fpchrome.AppImage` (fingerprint-chromium) |
| display | none; every reading taken under `xvfb-run -a` |
| pytest | **available**, 9.1.1 — the ticket's "no pytest on this container" note is stale |

### ⚠️ The caveat that qualifies every CHROMIUM row below

`sandbox_available()` returns **False** on this host: the unprivileged user
namespace chromium's sandbox needs is refused (`unshare(CLONE_NEWUSER)` →
`EPERM`). `--no-sandbox` was **required** to get a chromium reading at all.

persona's own launch path **never** passes that flag, so by the project's own
rule (`chromium_tier.py:528`) a reading taken this way **is not the product's
default surface**. PS-128 carried the identical waiver. It is stated here rather
than buried in a JSON field because it bounds every chromium answer below.

The **firefox** arm carries no such waiver: it was recorded through the
project's own `baseline.record_snapshot()`, i.e. the shipping launch path.

### How each arm was read

* **firefox** — `baseline.record_snapshot(profile=..., fresh=True)`, the same
  in-process launch-and-read the committed baseline uses.
* **chromium** — the engine binary launched with the flags `process.py` passes,
  **including `--fingerprint=<seed>` (`process.py:561`)**, read over CDP with
  the shipped `runner.run_probes`.

Neither arm retypes the probe: both import the expression from `PROBES`, so a
change to what ships changes what was measured.

---

## 1. The headline — the two engines DISAGREE

AC2 requires the engines be reported separately, and this is why that matters:
a blended answer would be wrong about both.

| engine | two distinct seeds | verdict |
|---|---|---|
| **chromium** (delegated C++ patch) | `2838771797` vs `2455437942` | **DIFFERS** — the patch works |
| **firefox** (packaged engine) | `4242351214` vs `4242351214` | **COLLIDES** |

Neither is evidence about the other.

## 2. Chromium — five seeds, five digests

| seed | digest (window) | digest (worker) |
|---|---|---|
| 111 | `381336052` | `381336052` |
| 222 | `1832625859` | `1832625859` |
| 333 | `2076010582` | `2076010582` |
| 1337 | `2838771797` | `2838771797` |
| 4242 | `2455437942` | `2455437942` |

Five distinct values, so the vector is **not drawn from a small pool** — that is
what rules out `POOLED`, and two seeds alone could never have shown it.

### The counterfactual — what makes this evidence about the FLAG

Five distinct digests prove variation, not *cause*. So the flag was removed and
the run repeated:

| run | `--fingerprint=` | seed arg | digest |
|---|---|---|---|
| control a | **absent** | 1337 | `2616755061` |
| control b | **absent** | 4242 | `2616755061` |

With the flag gone, two different seeds read **the same value**. The entropy is
caused by `--fingerprint=`; it is genuinely seed-derived and not launch noise.

## 3. Firefox — three seeds, one digest

Seeds `111`, `1337` and `4242` **all** read `4242351214`, in **both** realms.

### The control that makes this a statement about canvas, not about the harness

A collision is the same shape as a broken probe, so the same snapshots were
asked whether *anything* varied. Five probes did:

| probe | seed 1337 | seed 4242 |
|---|---|---|
| `audio.digest` | `sum 35.749981` | `sum 35.749964` |
| `webgl.readback` | differs | differs |
| `webgl.unmasked` | `ANGLE (AMD, Radeon R9 200…)` | `ANGLE (Intel…)` |
| `webgl.parameters` | differs | differs |
| `navigator.hardwareConcurrency` | differs | differs |
| **`canvas.readback`** | **`4242351214`** | **`4242351214`** |

The masking layer was **live and correctly seeded**. Canvas 2D simply is not
spoofed on firefox — `--fingerprint=` is Chromium-only and the firefox arm
returns at `process.py:353`, well before it. This is the third case of the
pattern PS-73 (audio) and PS-78 (WebGL readback) already established.

## 4. AC4 — a profile still recognises itself

| engine | seed | first | re-read | |
|---|---|---|---|---|
| chromium | 1337 | `2838771797` | `2838771797` | fresh profile dir |
| firefox | 1337 | `4242351214` | `4242351214` | fresh session |

Bit-identical. This is the half that divergence alone cannot establish: a random
vector satisfies "two profiles differ" perfectly while making one profile
unrecognisable to itself, which is a different leak, not a fix. No rounding was
needed — the digest is an integer over integer bytes.

## 5. The self-check counters

`bytes 8192`, `mid 6144` on **every** reading, both engines, both realms.
`mid` is exactly 3/4 of the surface: the RGB channels sit mid-range and alpha is
pinned at 255 and correctly skipped. A draw that had gone black or white would
collapse `mid` toward the alpha-only floor and read a *working* spoof as a dead
one — this is the counter that tells those two apart.

## 6. Cross-engine — the reading tracks the RENDERER

Same seed `1337`: `4242351214` on firefox, `2838771797` on chromium. This is the
observation behind the `ENV_SENSITIVE_PROBES` decision (Delta 3).

---

## 7. What this establishes, and what it does not

**Established.** Canvas 2D is now *observed* rather than assumed. The
`webgl_ext.py:6` claim that toDataURL is patched in C++ is, on the readback
path, **consistent with what chromium does** — the flag demonstrably drives the
vector. On firefox the vector is **unspoofed and colliding**, measured.

**NOT established.**

1. **No cross-machine reading.** Only one host was available, so
   "differs across machines" was not observed. The `ENV_SENSITIVE_PROBES`
   decision rests on the argument from what the draw does plus the weaker
   cross-*engine* fact above — stated as an argument, not dressed as a
   measurement.
2. **The chromium rows are not the product's default surface** (§0 waiver).
3. **This ships no protection.** It turns an inherited assumption into an
   observation. The firefox collision is a **finding for PS-2** per the ticket's
   own seam; picking a perturbation before measuring would be guessing.
4. **`getImageData` only.** `toDataURL` was not separately read; the C++ claim
   names that entry point, and this probe reads the raw-bytes one.

## 8. The consequence of the classification, stated up front

`canvas.readback` is `INDEPENDENT`, so `compare_profiles` now compares it. On
**firefox** two profiles agree, so the two-profile unlinkability check will
report **COLLIDING** on `window` and `worker` and go to **FINDING** on that
engine.

That finding is **true** — the digests really are identical and really are
linkable. It is a report, not the fabrication the `SHARED` default guards
against. `SHARED` was rejected because it is a positive claim that a vector is
"not seed-derived at all", which the chromium counterfactual shows to be false,
and because `SHARED` probes are skipped entirely — the collision would have been
recorded once and then never reported again.

Precedent: `audio.digest` (PS-73) and `webgl.readback` (PS-78) were both
classified `INDEPENDENT` while firefox still collided on them. In both cases the
collision was reported, then fixed — `audio.digest` now varies per seed, as §3
shows.

## 9. Files

```
readings/ps135-2026-08-24/
  reading.firefox.seed{111,1337,4242}.json      full snapshots (83 readings each)
  reading.firefox.seed1337.rerun.json           AC4
  reading.chromium.seed{111,222,333,1337,4242}.json
  reading.chromium.seed1337.rerun.json          AC4
  counterfactual.chromium.no-fingerprint-flag.seedarg{1337,4242}.json
```

Every chromium record carries its own `sandbox_waiver` field, so a row read in
isolation still states the bound in §0.
