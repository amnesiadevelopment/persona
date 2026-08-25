# PS-161 — the FIX half: one profile, one GPU identity

Implemented 2026-08-25 at the worker seat, after the owner's decision was
recorded on the ticket (planner comment `577e3c04`, 2026-08-25T08:47Z).

The investigation half of this ticket is `EVIDENCE.md` in this same directory
and is unchanged. This file records what was **built**, the two measurements
taken to size it, and the live verification.

---

## 0. The decision this implements, and the one place it departs from it

The owner chose the **per-arm split**:

| arm | owner's choice |
|---|---|
| linux | (a) — our `gpu_ext` keeps authorship |
| windows | (b) — defer to the engine |
| macos | (b) — defer, **subject to the seed requirement below** |

Two conditions were attached by the planner, both explicitly labelled scope
rather than substance:

1. **(b) ships with a check that watches the engine's variance** and goes red
   when it stops varying. Reuse the merged gate if it fits; do not build a
   second instrument. If it cannot be made automatic — stop and comment.
2. **macos needs the extra seeds before (b) lands there.** The evidence was
   2-of-3 seeds colliding on `Apple M2`. *"If macos turns out to vary weakly —
   a pool small enough that profiles collide often — that is a finding that
   changes macos to (a) on the same Level 2 grounds as linux, and it should be
   found now rather than after the code ships."*

**Condition 2 fired.** The seeds were run and macos is (a). That is the one
place this implementation differs from the table above, and it is not a fresh
decision taken at this seat: it is the execution of a conditional the
decision-maker wrote down in advance, on the measurement he asked for. §1 is
the evidence.

---

## 1. ⭐ macOS: the extra seeds were run, and they change the arm to (a)

30 seeds, masking layer OFF, engine `fingerprint-chromium/148.0.7778.215`,
reading `UNMASKED_RENDERER_WEBGL`. Records:
`readings/ps161-macos-seeds-2026-08-25/` (15 seeds) and
`readings/ps161-macos-seeds2-2026-08-25/` (15 more, disjoint).

**Two distinct values across all 30 seeds:**

| value | count | share |
|---|---|---|
| `ANGLE Metal Renderer: Apple M2` | 26 | 86.7% |
| `ANGLE Metal Renderer: Apple M4` | 4 | 13.3% |

The number that matters is not "it varies" but **how often two profiles
collide** — the pairwise collision probability:

| | collision | effective pool |
|---|---|---|
| engine, macos | **76.9%** | 1.30 |
| persona's own `MAC_GPUS` (2 entries) | 50.0% | 2.00 |

⇒ **Deferring to the engine on macos is measurably WORSE for unlinkability
than the pool it would replace.** Two macos profiles handed engine identities
share a card 77% of the time; under our own layer, 50%.

This is precisely the planner's stated condition, so macos stays under
`gpu_ext`'s authorship.

**The excuse that was explicitly ruled out in advance, checked rather than
assumed.** The instruction was *"do not treat Apple's genuinely small product
line as an excuse in advance… 'small pool' and 'colliding often enough to link
profiles' are different claims, and only the second one matters."* Apple ships
M1–M4 across Pro/Max/Ultra variants, so a two-value pool is **not** forced by
Apple's real lineup — and the skew (87/13, not 50/50) is a separate defect
again: even within two values the engine concentrates 87% of profiles onto one
card. The second claim is the one measured here, and it fails.

### Why a distinct COUNT would have got this wrong

A "does it vary?" check passes macos: it returned 2 distinct values, which is
more than 1. The skew is invisible to it. That is why the gate built in §3
scores the **collision probability**, not the distinct count — see the test
`test_collision_probability_is_sensitive_to_skew_not_just_distinctness`.

### The other two arms, same method, for comparison

15 seeds each (`readings/ps161-armsweep-2026-08-25/`):

| arm | distinct | collision | our pool | our collision | verdict |
|---|---|---|---|---|---|
| linux | 1 | 100.0% | 8 | 12.5% | (a) — forced, Level 2 |
| windows | 7 | **15.6%** | 5 | 20.0% | **(b)** — engine beats our pool |
| macos | 2 | **76.9%** | 2 | 50.0% | (a) — engine loses to our pool |

windows is the one arm where deferring is a genuine improvement, and it is the
only arm this change hands over.

---

## 2. ⭐ (b) had to be NARROWED: identity-only, not "drop the extension"

**This is the second measurement, and it changed the shape of the fix.**

The obvious reading of "defer to the engine on windows" is *stop installing
`gpu_ext` there*. That would have been a **host leak**, and the coherence run
(`readings/ps161-coherence-2026-08-25/`) is what caught it. `gpu_ext` authors
four things, not one. Layer OFF, per declared arm:

| vector | does the ENGINE author it? |
|---|---|
| identity pair (`UNMASKED_VENDOR`/`RENDERER`) | **yes** — seed-derived, per arm |
| getParameter limits | **yes** — windows 32767×32767, linux 8192×8192, macos 16384×16384 |
| **getSupportedExtensions** | **NO** |
| masked `VENDOR`/`RENDERER` | no |

The extension set, layer off, is **36 entries and byte-identical on all three
declared arms**. An arm-invariant value is not a spoof — it is the **host's**
own set reaching the page. And it advertises:

* `WEBGL_compressed_texture_s3tc` / `_srgb`, `EXT_texture_compression_bptc`,
  `_rgtc` — the Direct3D BC families, **and**
* `WEBGL_compressed_texture_astc`, `_etc`, `_etc1` — the **mobile GLES**
  families,

**simultaneously**, on a profile claiming `ANGLE (AMD, AMD Radeon(TM) Graphics
(0x00001638) Direct3D11 …)`. That "supports everything" set is the software
rasteriser's signature. This module already classifies the mirror of it (the
desktop set on an Android profile) as a **hard renderer↔extension
impossibility** — audit7 #3, documented at `DESKTOP_EXTS`.

⇒ Dropping `gpu_ext` on windows would have traded a *contradiction* for a
*leak*, against Invariant #0. The limits would have survived by luck (the
engine's windows values happen to equal `COMMON_DESKTOP` exactly), but the
extension set would not.

**So the gate is deliberately narrow: it covers the IDENTITY PAIR only.** Every
other vector stays persona's on every arm. The principle the ticket actually
needs is **one author per VECTOR**, not one author per module — and that is
what makes the contradiction structurally impossible rather than merely fixed
once. Pinned by
`test_the_split_is_identity_only_every_other_vector_stays_ours`.

---

## 3. What was built

### `browser/gpu_ext.py` — the authorship gate

`ENGINE_AUTHORED_IDENTITY_ARMS = frozenset({"windows"})`, consulted through
`engine_authors_identity(os_norm)`, baked into the emitted script as
`ENGINE_AUTHORS_IDENTITY`. When true the extension does not answer
`UNMASKED_VENDOR_WEBGL`/`UNMASKED_RENDERER_WEBGL` and does not synthesise the
`WEBGL_debug_renderer_info` handle (a synthesised handle whose constants then
fall through is a state no real browser is in).

**Fail-safe direction:** an arm not in the set keeps *our* authorship, so an
unrecognised or unmeasured platform gets persona's spoof rather than whatever
the host reports.

`android`/`ios` are absent deliberately and not merely for want of a
measurement: the engine has no android or ios platform at all (`process.py`
backs them with the nearest desktop platform it *does* spoof), so an
engine-authored identity there would be a desktop card on a phone UA — the
impossibility the `ANDROID_GPUS` arm exists to prevent.

### The header rationale — corrected, and arm-qualified (the ticket's DoD #2)

The module header asserted, unqualified, that without this extension the engine
reads as a generic `Google Inc. (Google)` / SwiftShader pair. Measured:

* **linux — HOLDS**, every seed.
* **windows — STALE.** 7 distinct real IHV cards over 15 seeds.
* **macos — STALE.** A real Apple Metal renderer.

It was never wrong so much as **arm-specific and stated as though it described
the engine**. It now carries the measurement and names the arm, because it is
the sentence that would otherwise justify re-adding our identity override on an
arm that does not need it.

### `verify/engine_gpu_variance.py` — the condition-1 gate

Deferring makes one of *our* invariants depend on a *third party's*
implementation detail. The engine autobumps daily; if a future build narrows its
pool, every windows profile silently shares a card and Level 2 breaks **with
nothing going red**.

**Why this is a new lane rather than a reuse of the merged gate.** The
instruction was to reuse `matrix_consistency` if it fits. It does not, for a
structural reason worth stating: that module asks whether **one record agrees
with itself**. A shared card is not a self-contradiction — every record is
individually perfectly consistent, and the entire population is linked. The
defect is a property of a **set of profiles**, and nothing in the subsystem
holds a set. That is the same shape of gap `matrix_consistency`'s own header
describes, one axis over. No second instrument was built for anything the
merged gate *can* answer: §4's verification is the merged gate, unmodified.

* **Metric:** pairwise collision probability (Simpson index), *not* a distinct
  count — §1 is the proof that a distinct count passes the macos case.
* **Bar:** the collision probability of *the arm's own fallback pool*, read out
  of `gpu_ext`'s source rather than duplicated, so editing a pool moves the bar
  automatically and the two cannot drift.
* **Sample floor:** fewer than `MIN_SEEDS` readable seeds is `INCONCLUSIVE`,
  never a pass — a cheap two-seed run must not be able to certify the property.
* **Verdicts:** `OK` / `TOO_NARROW` / `CONSTANT` / `INCONCLUSIVE`, with
  `INCONCLUSIVE` exiting `CANNOT_RUN` rather than `PASS`.

**It is verified able to go red, three distinct ways** (constant identity;
a pool narrower than ours; too few seeds), and green on the *real measured
windows readings* rather than on a hand-built ideal.

One bug was caught by its own boundary test: an arm sitting *exactly* at its bar
produced `5 × 0.2² = 0.20000000000000004 > 0.2` and flipped to `TOO_NARROW` on a
rounding artefact — a spurious red in production. Fixed with `BAR_TOLERANCE`.

### ⚠️ Where this gate runs — stated plainly, including where it does not

The live reading needs the product's own engine. Measured at this commit: CI
provisions `browser_firefox` only and names `browser_chromium` as a real
capability **nothing declares** (`ci.yml`), and `engine-autoupdate.yml`'s gate
is firefox-only for its own recorded reason.

So: the **judgement** (`classify`) is a pure function, gated in CI on every run
including its red cases. The **reading** (`measure`) is automated but runs only
where the engine exists. Wiring it into the chromium engine's own bump is the
remaining step and is named in the module docstring rather than quietly assumed.

This is disclosed because the condition said to stop and comment if the check
could not be made automatic. It *is* automatic; what is not yet automatic is the
chromium engine's provisioning, which is a pre-existing, separately-recorded gap
(`ci.yml` calls closing it "a separate slice, and it is not one line").

---

## 4. Live verification — the ticket's DoD #3 and #4

**Through the proxied exit, proven before anything was read.**

```
exit proven: 83.5.154.59  Warsaw/PL  AS5617 Orange Polska Spolka Akcyjna  Europe/Warsaw
engine: chromium (fingerprint-chromium/148.0.7778.215)
declared machine: windows   seed: 9001   masking layer: ON (the product's surface)
record: readings/ps161-live-2026-08-25/arm-fix-windows-seed9001.json
evidence: SUFFICIENT (24/28 fingerprint rows, 4 checkers)
```

The baseline exit was `5.173.155.60` (Warsaw PL). This run exited
`83.5.154.59`, also Warsaw PL — **a different Warsaw exit is exit-driven
variance per PS-10, not a failed baseline**, as the ticket's method constraints
state. The address is recorded here because the constraint requires it.

`windows/seed9001` was chosen deliberately: it is the exact condition of the
`ps143`/`ps150` corpus, so this is like-for-like with the records that
demonstrated the defect.

### The gate's verdict, and the proof it could have failed

Verified with the **merged same-vector consistency check**, unmodified — the
instrument the ticket names as its verifier.

| record | condition | exit |
|---|---|---|
| `ps150/arm-a-baseline-layer-on.json` (pre-fix) | windows/9001 | **1 — CONTRADICTION** |
| `ps143/arm-a-layer-on.json` (pre-fix) | windows/9001 | **1 — CONTRADICTION** |
| `ps161-live/arm-fix-windows-seed9001.json` (**post-fix**) | windows/9001 | **0 — CONSISTENT** |

Post-fix, all four `gpu_claimed` rows name **one** identity:

```
creepjs        gpu_renderer     ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 …)
creepjs        gpu_vendor       Google Inc. (AMD)
pixelscan.net  webgl_renderer   ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 …)
pixelscan.net  webgl_vendor     Google Inc. (AMD)
```

```
CONSISTENT — 1 vector(s):
  gpu_claimed   all 4 row(s) name the same hardware (amd)
```

Pre-fix, the same vector in the same condition read:

```
gpu_claimed   2 different hardware identities in one record:
              amd (creepjs/gpu_renderer, creepjs/gpu_vendor);
              nvidia (pixelscan.net/webgl_renderer, pixelscan.net/webgl_vendor)
```

**The check is proven able to fail on this exact question, at this exact
condition, and it now passes.** That is what makes this coverage rather than a
check that could not have failed. `creepjs` and `pixelscan` no longer disagree,
because there is no longer a second author for them to disagree about.

---

## 5. Scope discipline

Held to the ticket's out-of-scope list. Not touched, not folded in, not
commented on beyond this line: the `masking_detected` verdict; whether this
clears pixelscan's `fingerprint_inconsistent` (this work *answers* that question
and may not *assume* it — the post-fix record is on disk for whoever asks it);
widening the checker matrix; re-testing whether the masking layer causes the
verdicts.

**One finding is recorded and deliberately NOT acted on here.** macos is weakly
unlinkable under *both* authors — 50% collision from our own two-entry
`MAC_GPUS`, 77% from the engine's. Keeping our layer is the better of the two
and is what this ticket ships, but 50% is not a good number in absolute terms.
Widening `MAC_GPUS` is a different change with its own evidence requirements
(every entry needs provenance, per this module's standing rule), and folding it
in here would be scope creep on a ticket that already carries a reversed arm.
It is named for a follow-up rather than silently absorbed.
