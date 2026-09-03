# Rebasing the 16 fingerprint patches onto a new ungoogled tag

**Current target: ungoogled-chromium-portablelinux `152.0.7977.75-1` (Chromium
`152.0.7977.75`, v8 `3de6ffff`). All 16 patches apply: 81/81 hunks, 0 rejects,
`--fuzz=0`.**

Rebased from `144.0.7559.132-1` under PS-299, 2026-09-03.

---

## Do this first, every time

```bash
python3 scripts/ps299_rebase_probe.py                       # newest ungoogled tag
python3 scripts/ps299_rebase_probe.py --tag 152.0.7977.75-1 # a specific one
```

About a minute, no Chromium checkout, no compile. It reconstructs only the ~38
files our patches touch, applies ungoogled's own prerequisite patches on top,
then applies ours and reports per-patch rejects. Exit status is 0 only when all
16 apply with zero rejects **and zero fuzz**, so it works as a gate in the
watch-and-bump automation.

There are **three** exit statuses, and the third is the one to read carefully:

| exit | meaning |
|---|---|
| `0` | all 16 apply, zero rejects, zero fuzz |
| `1` | rejects and/or fuzz — a rebase is needed |
| `2` | **the measurement could not be made at all** — clone failed, a file could not be fetched, ungoogled's own prerequisites failed, wrong patch count |

`2` is **not** "the patches are fine". Nothing was measured. Do not let an
automation treat it as anything other than a hard stop.

**Green here is necessary, not sufficient.** A clean textual apply is not a
compile — see "What this does NOT establish" at the bottom.

### The gate is tested on its ability to FAIL

`tests/test_ps299_rebase_probe_gate.py` exists because the first version of this
probe **could not fail** on the most likely real breakage at a new tag: upstream
renaming or deleting a file we patch. GNU patch emits no `Hunk #N FAILED` for an
absent target — it says `can't find file to patch` and exits 1 — so a probe that
scraped only for `FAILED` scored it zero rejects. Run against an **empty
directory** it printed `81/81 hunks, 0 rejects, ✅` and exited 0.

If you change how rejects are counted, run that test file. Four of its nine
tests fail on the unfixed probe; the other five assert the fix does not break
the healthy shapes — in particular that a **create-file hunk against an absent
target is still not a reject**, which is trap #1 below seen from the other side.

---

## Two traps that cost real time. Both are encoded in the probe; read them anyway.

### 1. AN ABSENT PATH IS NOT A DELETED PATH

A plain "does this file exist upstream?" probe reports **10 of our 38 paths
missing** at a new tag, and would tell you 6 of 16 patches need rewriting. That
is wrong. Every one of them resolves. They split three ways:

| Group | Paths | Why it is absent |
|---|---|---|
| **Our patches create them** | `components/ungoogled/fingerprint_data.h`, `third_party/blink/renderer/modules/webgl/gpu_info.{cc,h}`, `.../gpu_fingerprint.{cc,h}` | Created by 002 and 011. Of course they are not upstream. |
| **ungoogled creates them** | `components/ungoogled/*` (`ungoogled_switches.{cc,h}`, `BUILD.gn`) | ungoogled's `add-components-ungoogled.patch` adds them, and 152 still ships it identically. |
| **Different repository** | `v8/src/inspector/v8-runtime-agent-impl.{cc,h}` | v8 lives in `v8/v8`, not `chromium/chromium`. |

### 2. v8 MUST BE READ FROM `DEPS`, NOT FROM `main`

001 is the only patch touching v8. Measuring it against v8 `main` HEAD reports a
reject the real build would never see. **The pin is readable** — the PS-299
ticket body recorded that it "could not be read out of `DEPS`", and that is
wrong:

```
'v8_revision': '3de6ffffbfdcf265e9f11a5c9d1cfb4d486d7550',
```

The probe does this automatically. If you measure v8 by hand, do it at the
pinned revision.

### 3. SECTION-FILTER UNGOOGLED'S PATCHES; DO NOT STUB THEIR TARGETS

The earlier dry run stubbed `bromite_flag_entries.h` empty (it does not exist
upstream; ungoogled's build generates it) and bought an artificial reject **in
ungoogled's patches, not ours**. Filtering each prerequisite down to the
file-sections touching our files avoids the entire class: 7 prerequisites
apply, 108 skip as irrelevant, 0 fail.

---

## Why fuzz=0 is the bar even though the build allows fuzz

ungoogled applies with `patch -p1 --ignore-whitespace` and no `--fuzz`, so GNU
patch's **default fuzz of 2 is live in the real build**. The probe defaults to
`--fuzz=0` anyway.

A hunk that only lands with fuzz is a hunk whose context has *already* drifted.
It passes today and rejects at the next tag. At 152, three patches (003, 007,
013) were in exactly that state — they reported "0 rejects" under default fuzz
and failed at `--fuzz=0`. They were re-anchored. **If the probe reports fuzz,
fix it then, not next quarter.**

---

## What actually rejected at 144 → 152, and how each was resolved

13 hunks rejected. (The pre-work dry run predicted 17; it over-counted because
it measured 001 against v8 `main` and stubbed a file into ungoogled's own patch
set. 011-gpu-info was predicted to reject and did not.)

**Nine of the thirteen were the same trivial thing: an include-block hunk.**
Upstream reorders and adds `#include` lines constantly, so a hunk quoting six
neighbouring includes as context is the single most fragile shape in this patch
set. All nine were re-anchored onto **one** adjacent include each.

| Patch | Rejects | Cause | Resolution |
|---|---|---|---|
| 001-disable-runtime.enable | 1 | Upstream added re-entrancy guards *inside* `addBindings`, whose whole body the patch commented out | **Re-thought.** Now a one-line `if (!enabled()) return;` — `enabled()` is already hard-coded `false` in the header, which is the actual mechanism. No commented-out body to drift, and no unreachable code for `-Wunreachable-code`. |
| 002-user-agent-fingerprint | 5 | `GetUserAgentInternal()` **lost its `user_agent_reduction` parameter**; `GetUnifiedPlatform()`'s buildflag block was reshaped; two include hunks | Signature change re-expressed against the new zero-arg form. `GetUnifiedPlatform` override re-anchored on the **function signature line** rather than the buildflag maze below it. |
| 006-font-fingerprint | 1 | include block (`base/byte_size.h`, `base/numerics/safe_conversions.h` added upstream) | Three separate one-line include anchors |
| 012-canvas-get-image-data | 2 | `base::RandInt` → **`base::RandIntInclusive`** rename, inside a block the patch deletes wholesale; one include | Context updated to the new name. Note the cause: this is *upstream* churn. The ticket predicted a three-way ordering conflict with ungoogled's own patch — that is **not** what it was. |
| 014-client-rects | 1 | include block in `element.cc` | Two one-line include anchors |
| 016-webgl-readPixels | 2 | include block in `webgl_rendering_context_base.cc` | Two one-line include anchors |
| 018-timezone | 1 | `base/command_line.h` **was added upstream**, so our hunk tried to add a duplicate | Dropped our duplicate add; anchored the remaining include on one line |
| 003, 007, 013 | 0 (fuzz) | Passed only via default fuzz=2 | Re-anchored to fuzz-0 clean |

### The re-anchoring rule this produced

**Anchor on the smallest stable thing, not on what is nearby.**

- An include? Anchor on **one** neighbouring include, never a block of six.
- A function body edit? Anchor on the **signature line**, not on the code inside
  it, which upstream reshapes freely.
- Disabling something? Route it through an existing predicate you already
  control (001's `enabled()`) instead of commenting out a body that will drift.

Patch size went **from 116 KB to 112 KB** while covering the same behaviour;
the difference is context that was never load-bearing.

---

## Verifying a rebase actually preserved behaviour

A patch that *applies* is not a patch that still *does* anything — a re-anchored
hunk can land in the wrong place, or an inserted call can be silently dropped.
Two checks, both cheap:

1. **Symbol-level diff.** Every fingerprint identifier the old patch set
   *inserted* must still be inserted by the new one (`switches::kFingerprint*`,
   `GetUserAgentFingerprintBrandInfo`, `UpdateUserAgentMetadataFingerprint`,
   `ShuffleSubchannelColorData`, …). Compare inserted-line identifier multisets
   between the two patch sets, not their text.
2. **Created files byte-identical.** The five files our patches create
   (`fingerprint_data.h`, `gpu_info.{cc,h}`, `gpu_fingerprint.{cc,h}`) must come
   out byte-for-byte the same unless you deliberately changed them.

Both passed for 144 → 152.

---

## What this does NOT establish

**The probe measures TEXT. It does not compile anything.** Between `152.0.7977.75`
and `144.0.7559.132`, `element.cc` grew +42 KB, `element.h` +10 KB and
`webgl_rendering_context_base.cc` +11 KB. A signature can change underneath a
hunk that still applies perfectly.

Static checks done beyond the apply, none of which replace a compile:

- cross-TU symbols (`GetUserAgentFingerprintBrandInfo`,
  `UpdateUserAgentMetadataFingerprint`) are both declared and defined, and every
  call site includes the declaring header;
- the four `user_agent_utils.cc` functions our patch calls exist with the arity
  we pass — this is what caught 002's dropped parameter;
- all 12 `switches::k*` referenced on inserted lines are defined by
  000-add-fingerprint-switches;
- every include our patches add resolves.

**The deliverable is a compile.** Dispatch `engine-trial-build.yml`
(`workflow_dispatch` only, runner label `persona-build`) and read
`scripts/ps218_attribute.sh`'s attribution rather than raw ninja output.

⚠️ **The first run on a NEW ungoogled tag must be `trees=both`.** `trees=patched`
borrows an unmodified control from a previous run, and that borrow is *verified*
(same tag, same host, actually compiled) — so a control from a different tag is
correctly refused. Only later iterations **on the same tag** can borrow, using
the first run's id.

---

## Platform neutrality is a hard constraint

Owner ruling, 2026-09-03: these are the **same patches on every OS**; Linux and
Windows build synchronously from the same set; macOS is deferred.

**A hunk that only applies on Linux is a defect.** Do not resolve a reject with
a platform-conditional. If a reject genuinely cannot be resolved
platform-neutrally, say so on the ticket — that is the owner's decision, not a
judgement call inside a rebase.
