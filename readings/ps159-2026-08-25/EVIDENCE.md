# PS-159 — de-confounding the stock-Chromium control arm

**Date:** 2026-08-25 · **Subject:** Debian stock `/usr/bin/chromium` 151.0.7922.169
**Checker:** `pixelscan.net/fingerprint-check` · **Artifact:** `stock-deconfound.json`

> ⚠️ **This record is not about persona.** Its whole subject is a browser carrying none of
> persona's code. Nothing here may be attributed to persona's product behaviour, in either
> direction. The script that produced it overrides `chromium_tier._engine_binary`'s refusal to
> launch a PATH chromium — deliberately, in a control-arm script, and never in the tier itself.

---

## 1. The question

PS-150's arm C found that a plain stock Chromium gets `masking_detected` **and**
`fingerprint_inconsistent` on the same exit persona's packaged engine does. That reads as
"the host does it too", and PS-150 **refused to draw it** — correctly — because the stock arm
carried three tells the packaged arm did not, each on its own sufficient to produce both
verdicts.

**Two runs reaching the same verdict by different routes is not a shared cause.**

So: **is `masking_detected` on this checker, in this environment, saying anything about us at
all?** The method is per-axis removal — removing all three at once tells you the destination
and not the road.

---

## 2. Preconditions

| constraint | status |
|---|---|
| proxied exit (mandatory, no fallback) | **PASS** — every arm proxied, every exit in **PL / `Europe/Warsaw`**. Arms 1–6: `2a02:a311:c5e7:8180:…`, `P4 Sp. z o.o.`, Bydgoszcz. `−timezone` re-run: `5.173.194.43`, Warsaw. |
| exit stable across arms | **PARTIAL — disclosed, not swept.** The first six arms shared one exit. The `−timezone` arm was later **re-run** (§7a) and the exit had rotated to a second Polish one. Sorted per PS-10 below. |
| credential channel | `file` (`/workspace/_secrets/test-proxy.txt`) |
| `--allow-unsandboxed-chromium` | **REQUIRED on every arm — the only waiver, disclosed** |
| GPU present | **NO** — `/dev/dri` absent |

**The waiver, stated plainly:** every arm ran with `--no-sandbox`, because this host forbids the
unprivileged user namespace. persona's own launch path passes that flag nowhere, so **no arm
here is the product's default surface.** It applies equally to every arm, so it does not bias
the differential — but it is inherited by every conclusion below.

---

## 3. The baseline reproduces — the premise survives

The ticket required this first, because PS-150's arm C was **never a re-run**: it was the
byte-exact block lifted from a run whose `.log` `.gitignore` had swallowed. It had never been
reproduced. If it did not reproduce, the rest of the ticket was moot.

**It reproduces: 12 of 12 rows byte-identical to the committed `arm-c-stock-vs-packaged.json`** —
including `webgl_hash b35a6e95…` and `canvas_hash 5f4a84ed…`.

Two things worth recording, neither of which the ticket asked for:

- The reproduction happened behind a **different exit** than PS-150's (`5.173.155.60`). Twelve
  identical rows across two different Polish exits is a stronger premise than the single reading
  the ticket was handed, and it means the fingerprint-sorted rows here are not exit artifacts.
- `webgl_hash` and `canvas_hash` are **stable across exits**, which is what a fingerprint-sorted
  row is supposed to be and is a small check on the instrument itself.

---

## 4. Per-axis results

Each axis was removed **alone**, and each removal carries in-band proof that it actually landed —
verified from the checker's own output, not from the request. An axis that silently failed to
apply would otherwise read as *"removing it changed nothing"*, which is the exact wrong
conclusion.

| row | baseline | −automation | −timezone | −both removable | −renderer | −all three |
|---|---|---|---|---|---|---|
| **`masking_detected`** | **true** | **true** | **true** | **true** | **true** | **true** |
| **`fingerprint_inconsistent`** | **true** | **true** | **true** | **true** | **true** | **true** |
| `automation_detected` | true | *null* | true | *null* | true | *null* |
| `timezone_spoofed` | true | true | *null* | *null* | true | *null* |
| `timezone_from_js` | `Africa/Abidjan` | `Africa/Abidjan` | `Europe/Warsaw` | `Europe/Warsaw` | `Africa/Abidjan` | `Europe/Warsaw` |
| `geo_country_city` | — | — | `Poland / Warsaw` ᵉ | `Poland / Bydgoszcz` | — | `Poland / Bydgoszcz` |
| `webgl_renderer` | SwiftShader | SwiftShader | SwiftShader | SwiftShader | **`-`** | **`-`** |
| `webgl_hash` | `b35a6e95…` | `b35a6e95…` | `b35a6e95…` | `b35a6e95…` | **null** | **null** |
| `canvas_hash` | `5f4a84ed…` | `5f4a84ed…` | `5f4a84ed…` | `5f4a84ed…` | `8789d2cd…` | `8789d2cd…` |

ᵉ **Exit-driven, not fingerprint-driven.** The `−timezone` arm was re-run (§7a) behind a second
Polish exit, so this cell reads its city rather than the first exit's. Sorted per PS-10's
three-sort rule: `geo_country_city` is an **exit-sorted** row and is *expected* to track the exit.
Every **fingerprint-sorted** row in this column — `webgl_hash`, `canvas_hash`, `webgl_renderer` —
came back **byte-identical across the two exits**, as did both target verdicts. The column is
therefore comparable with the rest of the table on every row the argument uses.

### Axis 1 — automation: **removed, verdicts unmoved**

`--disable-blink-features=AutomationControlled`. Proof it landed: `automation_detected` went
**true → absent** on the checker's own page. Both target verdicts unchanged.

*Bound:* this suppresses the automation *tell*; it cannot remove the CDP channel, which is the
tier's only way to read a page at all. So the honest claim is "what can be suppressed from the
command line was suppressed", not "automation was eliminated".

### Axis 2 — timezone: **removed, verdicts unmoved**

**Stock ignores `--timezone`** — that flag is a fingerprint-chromium patch, and its absence is
precisely *why* the baseline reported `Africa/Abidjan` (ICU's canonical UTC+0) behind a Polish
exit while the harness believed it had pinned the zone. Passing that flag and calling the axis
moved would have been a null instrument. The axis therefore rides the **`TZ` environment
variable**, which stock does honour.

Proof it landed, three independent rows: `timezone_from_js` **`Africa/Abidjan` → `Europe/Warsaw`**,
`timezone_spoofed` **true → absent**, and `geo_country_city` newly reading a **Polish city matching
the exit** (`Poland / Warsaw` on the re-run behind `5.173.194.43`; `Poland / Bydgoszcz` on the
first run behind the Bydgoszcz exit — the row tracks the exit, which is exactly what an
exit-sorted row should do). The geography contradiction is gone. Both target verdicts unchanged.

*Strengthened by the re-run:* this axis is now the one arm measured **twice, behind two different
Polish exits** (§7a), and both target verdicts came back `true` on both. Its proof-of-landing rows
reproduced identically; only the exit-sorted city moved.

### Axis 3 — renderer: **COULD NOT BE MOVED. This is the ticket's third outcome.**

This host has **no GPU** (`/dev/dri` absent), so there is no hardware renderer to switch to.
Stripping the harness's forced `--use-gl=angle --use-angle=swiftshader
--enable-unsafe-swiftshader` did **not** yield a real adapter — it removed WebGL **entirely**:
`webgl_vendor` and `webgl_renderer` both became `-`, `webgl_hash` became null, and `canvas_hash`
changed.

**That is not removing a confound; it is swapping one anomaly for a louder one.** A browser with
no WebGL at all is *more* anomalous to a fingerprinting checker, not less. So the software
rasteriser is the one axis that **could not be de-confounded on this host**, and no reading here
tests it as a cause.

**Consequence for the `−all three` arm: it settles nothing**, because its third axis was never
actually removed. It is recorded for completeness and is explicitly **not** the answer. The
strongest *honest* arm is `−both removable`, which removes the two axes that genuinely can be
removed and leaves the rasteriser exactly as the baseline had it.

---

## 5. What the evidence supports

Against the ticket's three options:

> **✅ Outcome #3 — "cannot be de-confounded", with the axis named and a partial result reported.**

The named axis is **the software rasteriser**, and it is immovable here for a structural reason:
this host has no GPU, and per PS-10 the owner has ruled out a GPU machine in the loop entirely
(*"не должен появиться дев вм"*). So this is not a gap a re-run closes.

**The partial result is strong and worth stating on its own:**

- On **both axes that could be moved**, each verified as landed, **both target verdicts were
  completely immovable** — `true` in all six arms, never once moving.
- A stock browser that is **not announcing automation**, whose **clock and geography agree with
  its exit**, is *still* `masking_detected` + `fingerprint_inconsistent` — with **no persona code
  anywhere near it**.

**What may NOT be concluded, and why each is withheld:**

- ❌ **Not outcome #1** ("stock goes green once de-confounded"). Stock never went green on any
  arm. Nothing here says the checker is responding to real tells of ours.
- ❌ **Not outcome #2** ("this checker flags any browser in this environment"). It is the closer
  reading, but it is **not earned**: SwiftShader was never removed, so it remains a live and
  untested candidate cause. No browser with a real GPU has ever been read on this host. Claiming
  outcome #2 would attribute the verdicts to "the environment" on evidence that cannot separate
  "the environment" from "the software rasteriser" — the same one-reading over-reach this project
  has retracted four times in two days.
- ❌ **Nothing about persona.** The subject is a browser that is not persona.

**For the owner's release gate.** The stated gate is a fully green Chromium profile on pixelscan.
On this evidence that gate is **unreachable in this environment for at least one reason that is
not about persona's masking** — a stock browser cannot pass it here either, and two of the three
candidate explanations for that are now eliminated. That is a fact the gate's owner should have
before more work is aimed at it. It stops short of *"the gate needs revisiting"* only because the
third candidate survives.

**On splitting the two verdicts.** They did **not** separate: `masking_detected` and
`fingerprint_inconsistent` held identical values in all six arms. Per the ticket's boundary, a
separation would have been worth its own ticket; there is nothing to file.

---

## 6. What would settle it

One question remains, and it is a single measurement: **read a browser with a real WebGL renderer
on this checker.** If a browser that is otherwise coherent *and* has a genuine GPU still reads
`masking_detected`, outcome #2 is earned and the gate needs revisiting. If it goes green, the
rasteriser is the cause and the gate is reachable only on hardware this loop does not have.

Per PS-10 that cannot be answered on this host, and the owner has closed the dev-VM route. So it
is recorded as the open question rather than filed as a task with no path to completion.

---

## 7. Reproducing

```bash
.venv/bin/python -m scripts.ps159_deconfound_stock \
  --arms baseline,automation,timezone,removable_only,renderer,all_three \
  -o readings/ps159-2026-08-25/
```

Arms are written incrementally and merged, so a run that dies part-way keeps the arms it
completed. Each arm is a live ~60-second-settle page read; the full set takes roughly ten minutes.

---

## 7a. The `−timezone` arm was re-run — disclosed, with the reason

**This record is a merge across two runs, and it says so rather than reading as one campaign.**

The `−timezone` arm in the first campaign was produced *before* a fix to where the script captures
`TZ`. The capture point sits inside `_patched_args` (`scripts/ps159_deconfound_stock.py:137-142`)
precisely so it reads the value **at launch**, before the `finally` block restores the ambient one.
The earlier version captured after restoration, so that arm recorded `tz_env: null` — a value the
committed script **cannot** produce for an arm that removes this axis, since `TZ` is set before the
session is constructed. The merge logic correctly preserved prior arms, and in doing so carried a
row from a **different instrument version** forward.

That is a defect on its own terms — `tz_env` is the field documenting the *presented surface* for
axis 2 — and it made the committed record **not reproducible from the committed script**. So the
arm was re-run with the committed script:

```bash
.venv/bin/python -m scripts.ps159_deconfound_stock --arms timezone \
  -o readings/ps159-2026-08-25/
```

**What the re-run changed, in full:**

| field | before | after | sort |
|---|---|---|---|
| `tz_env` | `null` *(impossible)* | **`Europe/Warsaw`** | instrument fix |
| `geo_country_city` | `Poland / Bydgoszcz` | `Poland / Warsaw` | **exit-driven** (exit rotated) |
| *everything else* | — | **byte-identical** | — |

**Both target verdicts came back `true`, and every proof-of-landing row reproduced.** The other
five arms were untouched by the merge and verified byte-identical afterwards. The exit had rotated
to a second Polish exit between runs — recorded in the artifact's `prior_exits`, kept rather than
overwritten — which is why the city moved and why §4's table carries the `ᵉ` footnote.

**Nothing in §5's conclusion depends on this.** Axis 2's proof-of-landing rests on the checker's
own page rows, not on `tz_env`, and all three reproduced. The re-run makes the record honest and
reproducible; it did not move the answer.

**Provenance note.** `arm-c-stock-vs-packaged.json` — the artifact this ticket rests on — was
itself the file whose absence failed PS-150's first audit, having been written to a `.log` that
`.gitignore`'s `*.log` rule silently swallowed. Both files committed here are `.json` and were
checked against `git check-ignore` before commit.
