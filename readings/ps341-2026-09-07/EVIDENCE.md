# PS-341 — the Chromium engine-continuity cell, established BY MEASUREMENT

**Date:** 2026-09-07  **Base:** `db6d528`  **Verdict:** **no profile migration
is owed on the Chromium arm**, and the position is now recorded in the tree.
One genuine continuity finding is recorded alongside it: the **engine-authored
WebGL identity pair moves across a build change**.

## The question

`services/browser/process.py`'s Chromium arm has **zero build-awareness** — it
computes `profile_dir` and hands it to the engine as `--user-data-dir` without
ever asking which build wrote it. The Firefox arm runs a four-part migration on
*every* launch (`invisible_launch._migrate_profile_for_engine_build`), and its
own docstring says why: *"a profile seeded on firefox-18 makes firefox-19
SIGSEGV on startup (live-proven — the launch dies as TargetClosedError before
the window paints)"*.

The asymmetry was a fact. Whether it was a **defect** was not.

> ⛔ The parity question is **not** "why is Chromium missing Firefox's guard".
> It is **"does Chromium exhibit the behaviour that guard defends against?"**
> Only the second is a defect. **Nothing was assumed here. It was measured.**

## Environment

| | |
|---|---|
| build N | `personium-152.0.7977.75`, sha256 `6ddb7bbe…0bb4c3` (persona's own release, installed by `ensure_engine`) |
| build N−1 | `148.0.7778.215`, sha256 `a5fa5e6c…685f0` — a **real** older fingerprint-chromium (adryfish upstream, which persona's engine is built from), not a stand-in |
| gesture | `updater.revert_to_previous_build()` — what `ui/app.py:_on_engine_rollback` calls |
| venue | **headful** under Xvfb 1920x1080x24, read over CDP (`--remote-debugging-port=0`) |
| product path | `services/browser/process.spawn_browser` — the real launcher, unmodified |
| profile | `windows` / chromium / `search_engine=brave` (deliberately **not** the default, so "it kept the choice" is unambiguous) |

## Two disclosed substitutions, neither touching the code under test

1. **Upstream publishes exactly one `personium-` tag today**, so there is no
   second *published* build for a revert to reach. Only the module's
   `RELEASE_BY_TAG_API` **constant** is redirected to a loopback release
   document serving the real 148 AppImage. Every line of
   `revert_to_previous_build` runs verbatim — `rollback_target()`, the
   `_engine_in_use()` guard, `fetch_release_full`, `download_engine` **against
   the digest recorded on disk**, `record_installed_build`, `write_version`,
   `_set_pin`.
2. **`--no-sandbox`** is injected probe-side (`process.py` unmodified) because
   this container sets `kernel.apparmor_restrict_unprivileged_userns=1` and is
   not root, so the zygote aborts before any window paints — on *both* builds
   and on stock `/usr/bin/chromium` too. **Applied identically to both legs**,
   so it cannot author a *difference*, and a difference is the entire subject.

## The positive control — three axes, because one is not enough

"The profile survived" is ambiguous between *Chromium tolerated the downgrade*
and *my probe never changed the build*, and the second reads as the first.

| axis | before | after | moved |
|---|---|---|---|
| version **record** (`current_version()`) | `152.0.7977.75` | `personium-148.0.7778.215` | ✅ |
| binary **bytes** (sha256) | `6ddb7bbe…` | `a5fa5e6c…` | ✅ |
| **running page**'s `navigator.userAgent` major | `152` | `148` | ✅ |

The third is the one that matters most: the record and the bytes can both move
while the process a page actually talks to does not.

## The readings

### Q1 — does it open? **Yes.**

Both legs opened. No refusal, no crash, **no SIGSEGV** — the Firefox analogue
does not occur. Going *forward* again (148 → 152, same gesture, second
independent exercise of the mechanism) opens too.

### Q2 — does Chromium's own downgrade handling fire? **No.**

`Last Version` is **written and silently overwritten**: `152.0.7977.75` →
(downgrade) → `148.0.7778.215` → (forward again) → `152.0.7977.75`. It is a
record of what ran, **not a gate**. `Default/` is neither renamed nor recreated;
**no** backup/reset directory appears; no "profile is from a newer version"
behaviour of any kind.

This **confirms on the packaged engine** what a prior probe (2026-08-26) had
only established on the *system* `/usr/bin/chromium`, which is precisely the
re-probe that prior reading said it owed.

### Q3 — is derived state lost? **No.** *(read from the RUNNING browser)*

`profile_seed.seed_profile_prefs` is **once-only** — it returns early the moment
`Default/Preferences` exists — so had a downgrade reset that file, persona would
**never** restore the operator's theme/search choice. Nothing removes it.

| | on disk before | on disk after | **live on the older build** |
|---|---|---|---|
| Classic theme | `{"id":"","system_theme":0}` | unchanged | — |
| dark mode (`color_scheme2`) | `2` | unchanged | — |
| default search engine | `Brave` | unchanged | **`Brave (Default)`** on `chrome://settings/searchEngines` |
| bookmarks | 1758 B | unchanged | present in `chrome://bookmarks` |
| cookies | 20480 B | unchanged | sentinel cookie **served** |

The live column is the point: *a file that survives but is ignored is the same
outcome for the operator as one that was deleted.*

### Q4 — does the fingerprint move? **Only one vector, and not a migratable one.**

**Identical across the build change:** `screen`, `devicePixelRatio`,
`platform`, `hardwareConcurrency`, `deviceMemory`, `maxTouchPoints`, timezone,
`languages`, `colorDepth`, and the full **canvas `toDataURL()`**.

**Moved:** the WebGL vendor/renderer pair.

```
152 →  Google Inc. (NVIDIA) | ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002487) …)
148 →  Google Inc. (Intel)  | ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x00009A49) …)
```

Generalised across **8 seeds**, headful under CDP, both arms read through the
**same** venue: **8/8 moved, 0 unreadable**, and the two builds' observed card
pools **do not intersect at all** — 148 answers Intel integrated parts, 152
answers NVIDIA RTX parts. The engine's whole GPU table was replaced between
builds.

**Why it moved, and why no migration could fix it.**
`gpu_ext.engine_authors_identity_for_engine_platform("windows")` is `True`:
on the windows/macos arms the **engine** authors this pair and persona's own GPU
layer deliberately stands down. The observed `0x00009A49` is not in persona's
`WIN_GPUS` pool at all (which carries Iris Xe as `0x0000A7A1`), which
independently confirms the author. The value comes from a table compiled into
the **engine binary**, so rewriting the profile directory cannot change it.

It is **recorded, not fixed**: the fix — if one is wanted — is a decision about
*who authors that pair on those arms*, which is `gpu_ext.py`'s question, not the
launch path's. Filed as a separate finding rather than silently widened into
this ticket.

## ⚠️ Two false positives, caught by controls

Both looked like headline findings. Both were the instrument.

**1. "The downgrade lost the operator's cookie jar."** The first run read the
sentinel cookie as absent on the older build. Chromium's cookie store is flushed
by a task that **outlives process exit** — polling the SQLite file after a clean
terminate, the row was **absent at +2s/+5s/+10s and present at +20s** — so a
probe that restarted too soon read an empty jar. The control **reproduced the
same "loss" with no build change at all**, on both builds. The harness now
settles 45s.

**2. "The older build refuses to open the profile."** A later run had leg N−1
die at startup. Leaked browser process trees from earlier control runs had
exhausted the container's **2048-PID cgroup budget** (868 in use); the launch
failed with `pthread_create: Resource temporarily unavailable` and exit **133**
— indistinguishable from an engine refusing a profile. The harness now reaps
process **groups** (`killpg`), not the AppImage wrapper pid.

**3. A third, in the seed sweep.** An earlier draft read both builds with
`--headless=new --dump-dom` and reported a clean, confident **8/8 moved** — while
the old build's arm returned *no reading at all* for every seed. "No reading"
compared against a real string is unequal, so every row scored MOVED. Re-run
through the headful CDP venue with unreadable legs **excluded** rather than
counted, the 8/8 held up — but the first one was the instrument, not the engine.

> This is the project's own rule doing its job: *a difference observed across a
> change means nothing until the same difference is shown NOT to appear within
> one build.*

## Conclusion

**Outcome (b): a recorded no-migration-needed position, with its evidence.**

Recorded beside the Chromium arm in `src/services/browser/process.py`, where
premise 1's zero-hit grep would land (that grep returns **0 hits on `origin/main`
and 2 here**), and re-read live by
`tests/test_ps341_engine_continuity_live.py`.

**No Chromium migration function was added**, deliberately — it would be a fix
for a state this engine does not enter.

## Reproducing

```bash
export PERSONA_HOME=/tmp/ps341/home DISPLAY=:99   # a real Xvfb
python3 -c "import sys;sys.path.insert(0,'.');\
from src.services.engine import updater; print(updater.ensure_engine(timeout=900))"
PS341_PREDECESSOR_APPIMAGE=/path/to/fp148.AppImage python3 scripts/ps341_run.py
PS341_NEW_BINARY=… PS341_OLD_BINARY=… python3 scripts/ps341_gpu_seeds.py
python3 scripts/ps341_controls.py        # PS341_CONTROL=gpu|cookie|forward
python3 scripts/ps341_cookie_matrix.py
```

## Files

| file | what it is |
|---|---|
| `reading.json` | the main two-leg measurement (the run these numbers come from) |
| `gpu_seeds.json` | the 8-seed WebGL sweep across both builds |
| `control-gpu-stable-within-build-148.json` | GPU pair stable across two launches of ONE build |
| `control-cookie-restart-152.json` | the cookie false positive, reproduced with no build change |
| `control-forward-again.json` | 148 → 152 through the same gesture |
