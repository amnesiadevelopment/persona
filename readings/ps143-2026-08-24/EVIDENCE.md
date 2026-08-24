# PS-143 — `masking_detected` and `fingerprint_inconsistent` are NOT caused by persona's masking layer

**Both verdicts fire with the layer ON and with the layer OFF, through one proven exit, on two thick arms.**
That is pre-authorised outcome #2 of the four, for **both** verdicts. They do not split.

The "persona is detected" reading of these two verdicts is **retracted**. They belong to the packaged
engine, the host, or the verification tier — not to persona's masking layer.

---

## 1. The answer, per verdict

| verdict | layer ON (arm A) | layer OFF (arm B) | answer |
|---|---|---|---|
| `masking_detected` | `read` (adverse) | `read` (adverse) | **fires both ways → not our layer** |
| `fingerprint_inconsistent` | `read` (adverse) | `read` (adverse) | **fires both ways → not our layer** |

Every other adverse row in the matrix was `absent` or `unobtainable` on **both** arms — no adverse row
fired on one arm only:

| checker / item | ON | OFF |
|---|---|---|
| `pixelscan.net/masking_detected` | read | read |
| `pixelscan.net/fingerprint_inconsistent` | read | read |
| `pixelscan.net/proxy_detected` | absent | absent |
| `pixelscan.net/timezone_spoofed` | absent | absent |
| `pixelscan.net/automation_detected` | absent | absent |
| `bot.sannysoft.com/webdriver_present` | absent | absent |
| `bot.sannysoft.com/phantom_js` | absent | absent |
| `iphey.com/not_trustworthy` | absent | absent |
| `bot-detector.rebrowser.net/detected` | unobtainable | unobtainable |
| `deviceandbrowserinfo.com/bot_verdict_positive` | unobtainable | unobtainable |

`timezone_spoofed` remains cleared, consistent with PS-132/PS-137.

---

## 2. The gates this ticket was written to enforce — both PASS

The previous attempt (PS-137) failed on exactly these. They were checked **per checker**, not from the
aggregate `evidence: SUFFICIENT` floor, because PS-137 found three records that passed that floor with
pixelscan contributing zero rows.

**Gate 1 — both arms thick, and `pixelscan.net` contributes to both.**

| | arm A (layer ON) | arm B (layer OFF) |
|---|---|---|
| evidence verdict | `sufficient` | `sufficient` |
| fingerprint rows | **24 / 28** | **24 / 28** |
| `checkers_contributing` | `bot.sannysoft.com`, `creepjs`, `iphey.com`, **`pixelscan.net`** | `bot.sannysoft.com`, `creepjs`, `iphey.com`, **`pixelscan.net`** |
| pixelscan present | **yes** | **yes** |

No `TargetClosedError`. The `/dev/shm` death site PS-133 identified did not fire on either arm — see §5.

**Gate 2 — both arms share one exit, no rotation.**

Both arms observed **`46.205.201.136`** — Warsaw / PL / `AS12912 T-Mobile Polska S.A.` / `Europe/Warsaw`.
Identical on both. This is a comparison, not two unrelated readings.

(`tls.peet.ws/observed_ip` differs only in ephemeral source **port** — `:44988` vs `:45002` — same address.)

**Layer state verified on each arm, not assumed:**

| | `route` | `complete` | vectors installed |
|---|---|---|---|
| arm A | `extensions` | `true` | 10 — audio, canvas_ctx, device, gpu, locale, measuretext, native, stealth, voice, webgl |
| arm B | `none` | `false` | 0 |

---

## 3. The saturation guard — why "both ways" is a real result and not a dead comparison

A differential where both arms return identical verdicts is worthless if the arms did not actually differ.
That failure has a name on this project (`A/B VERIFICATION` / saturation) and it is the one that ships a
wrong ticket, because it produces evidence rather than silence. So it was tested rather than assumed.

**8 of 61 rows moved on the `fingerprint` sort.** Those are the rows that carry this argument: the
layer demonstrably changes what checkers see about the browser.

| row | layer ON | layer OFF |
|---|---|---|
| `creepjs/stealth_rating` | **20** | **0** |
| `creepjs/headless_rating` | **33** | **0** |
| `creepjs/like_headless_rating` | 38 | 44 |
| `pixelscan.net/webgl_renderer` | `ANGLE (NVIDIA … RTX 3070 …)` | `ANGLE (AMD … Radeon …)` |
| `pixelscan.net/webgl_vendor` | `Google Inc. (NVIDIA)` | `Google Inc. (AMD)` |
| `pixelscan.net/webgl_hash` | `036072f3…` | `185b5d0e…` |
| `iphey.com/trustworthy` | absent | read |
| `iphey.com/software_fine` | absent | read |

Ten rows differ in total. The other two are **corroboration, deliberately excluded from the headline**,
because neither is a fingerprint row and a guard that counts them overstates itself:

| row | sort | why it is not in the eight |
|---|---|---|
| `iphey.com/hardware_fine` | `host` | moved, and `compare` reports it under `host-moved` — real, but host-sorted, so it is not evidence about the browser's fingerprint |
| `tls.peet.ws/observed_ip` | `exit` | ephemeral source port only (`:44988`→`:45002`) — **same address**, and §2 already discounts it on exactly that ground; `compare` files it under `CONTEXT / exit-rotated: the exit rotates by design — not news` |

Counting the port row here would have put this record in contradiction with its own §2, which discounts
it. Eight is the number that carries the argument, and eight is still a decisive pass.

(One further row, `creepjs/canvas_data_hash`, is *not* a move at all: the value is `4c7ac378` on both
arms and only the surrounding page text changed. `compare` classifies it `reworded`. It is excluded.)

Independently corroborated on the loopback harness before the live run
(`checker_cli differential --engine chromium --axis layer`), which returned verdict `moved`:
`intl_locale` `de-DE`→`en-US`, `webgl_pixel_hash` `605e792a`→`c85ceb58`, `audio_digest` moved.

So the instrument was live, the layer reached the page, pixelscan read the page on both arms — **and the
two verdicts still did not move.** The comparison is sound.

---

## 4. Two secondary observations — recorded as leads, NOT as causes

**(a) The PS-128/PS-137 WebGL lead does not reproduce in its original form.**

That lead was an *absent* pixelscan renderer (`-`) beside a plausible creepjs string. Here pixelscan
published a plausible ANGLE string on **both** arms. The absent-renderer reading did not recur.

A *related but different* contradiction did appear, and only with the layer on:

| arm | pixelscan `webgl_renderer` | creepjs `gpu_renderer` | agree? |
|---|---|---|---|
| A (layer ON) | `ANGLE (NVIDIA, … RTX 3070 …)` | `ANGLE (AMD, … Radeon …)` | **no** |
| B (layer OFF) | `ANGLE (AMD, … Radeon …)` | `ANGLE (AMD, … Radeon …)` | yes |

Two checkers disagree about the GPU on the same page load, and the disagreement is introduced by the
layer. That is exactly the shape `fingerprint_inconsistent` names — **but it cannot be that verdict's
cause, because the verdict also fires on arm B where the two checkers agree.** Recorded as a lead for
the masking-invisibility theme (the layer publishing two different GPUs to two readers is worth its own
look on its own merits), explicitly *not* as an explanation of either verdict here.

**(b) `iphey.com` published no verdict at all with the layer on.**

All four iphey rows — including the *non-adverse* `trustworthy`, `hardware_fine`, `software_fine` —
read `absent` with reason *"the pattern did not match"* on arm A, while arm B read three of them.
`not_trustworthy` was absent on both, so **no adverse verdict is being hidden**, and iphey still
contributed rows to the evidence floor on both arms.

Absent-because-unmatched is not absent-because-clean. This is flagged rather than interpreted: it may be
a page that rendered differently under the layer, or a pattern-coverage gap. It does not affect the
two verdicts above, which come from pixelscan.

---

## 5. Conditions and waivers — one waiver, disclosed

The ticket's four-constraint list was reduced to two by the operator before this run (ticket comments
2026-08-24 12:53 and 13:13). Both were verified in **this** container rather than trusted:

| constraint | status |
|---|---|
| `/dev/shm` ≥ 256 MiB | **`df -h /dev/shm` → `1.0G`.** `--allow-small-dev-shm` **NOT** passed. `dev_shm_waived` does **not** appear on either record. |
| proven exit, no rotation between arms | **PASS** — `46.205.201.136` on both (§2) |
| `--allow-unsandboxed-chromium` | **REQUIRED and passed — the only waiver on this record** |
| credential | `/workspace/_secrets/test-proxy.txt`, 117 bytes, read from the **file**; `PERSONA_TEST_PROXY` deliberately not used |

**The one waiver, stated plainly:** both arms ran with `--no-sandbox`, because the host forbids the
unprivileged user namespace (`unshare(CLONE_NEWUSER)` → `EPERM`). persona's own launch path passes that
flag nowhere, so **neither arm is the product's default surface.** It applies equally to both arms, so it
does not bias the differential — but a reading taken under it is not a reading of the shipped product,
and the conclusion in §1 inherits that caveat. Both records carry the note.

**Run coordinates** (identical across arms except the layer):

- engine `fingerprint-chromium/148.0.7778.215`, seed `9001`, declared machine `windows` (honoured: `true`)
- arm A observed `2026-08-24T13:48:25Z`; arm B observed `2026-08-24T13:53:48Z` — five minutes apart, one exit
- environment `linux-x86_64 (agent sandbox)`

---

## 6. Environment findings from this run (for whoever runs the next one)

The container was **not** provisioned to take this reading. None of this changed a verdict; all of it cost
time, and a later run should not rediscover it:

1. **Repo was not checked out and no Python deps were installed.** Built `.venv`, installed `requirements.txt`.
2. **`invisible_playwright` and `invisible_core` are in `pyproject.toml` only, not `requirements.txt`** —
   installing `requirements.txt` alone leaves the engine unimportable.
3. **`Xvfb` was missing** (`chromium_tier.py:138` requires it). Installed via apt.
4. **persona's Chromium engine was not installed** at `~/.persona/engine/fpchrome.AppImage`. The tier
   refuses to substitute a chromium found on `PATH` — correctly, since that would not be the product.
   Provisioned through the sanctioned path, `updater.ensure_engine` (digest-verified), → `148.0.7778.215`.
   **This was caught by the loopback smoke test *before* a live matrix read was spent** — the smoke test
   is cheap, needs no exit, and is worth running first every time.
5. **The exit was transiently dead at the start of the session, and the diagnosis is worth keeping.**
   `prove_exit()` refused with `0x05 Connection refused` from both providers. That is a **SOCKS reply
   code, not a network failure**: DNS resolved, TCP to `gate.decodo.com:10000` was open, and **SOCKS5
   auth succeeded (status 0)** — the credential was valid throughout. `CONNECT` returned `REP 5` for
   every destination, 5/5 attempts over 40s. The account was healthy and Polish the whole time
   (a session-less auth returned `79.184.250.135` PL/Warsaw). Only the **shipped sticky session token**
   was refusing. It rolled over on its own (`sessionduration-30`) and the run proceeded.
   **Diagnostic worth reusing:** auth-succeeds-but-CONNECT-refuses ⇒ the sticky token, not the
   credential and not the network. Rotation is the operator's from the host; nothing was self-rotated.

**Disclosure:** while diagnosing the above I ran a `touch` writability probe against
`/workspace/_secrets/test-proxy.txt`, which changed its **mtime** to `2026-08-24 13:35:53`.
The **contents are unmodified** — 117 bytes, `sha256` prefix `8744012686223d21`. Recorded because a
credential file's timestamp is evidence someone may later read.

---

## 7. What this does and does not license

**Does:** retract the reading of `masking_detected` and `fingerprint_inconsistent` as statements about
persona's masking layer. Turning the layer off does not clear either one.

**Does not:** license a masking defect on these two verdicts — the control says the layer is not their
cause. It also does not license calling them a *product* defect from this record alone: the verification
tier is still not the product launch path. PS-137's divergence stands unchanged — `process.py:547` builds
`geo_ext` whenever a profile has a proxy, and the verify tier excludes it deliberately
(`verify/masking_layer.py:413`) — and this run inherits it, plus the `--no-sandbox` waiver in §5.

The open question these two verdicts now belong to is **which** of the packaged engine, the host, or the
tier produces them, on a surface where they can be attributed. That is the next question, and it is not
this ticket's.

## Artifacts

- `arm-a-layer-on.json` — layer ON, `route=extensions`, 10 vectors
- `arm-b-layer-off.json` — layer OFF, `route=none`, control arm
- Reproduce the comparison — **run this from the repository root**, with the paths as written:

  ```bash
  python -m src.services.verify.checker_cli compare \
    readings/ps143-2026-08-24/arm-a-layer-on.json \
    readings/ps143-2026-08-24/arm-b-layer-off.json
  ```

  The `-m` form only resolves at the repo root, so a `cd` into this directory fails with
  `ModuleNotFoundError: No module named 'src'`. Running it at the root with *bare* filenames fails the
  other way, with `REFUSED: no checker-matrix record to read at 'arm-a-layer-on.json'` — read that as a
  statement about the path, **not** as a finding about these records. The command exits non-zero when
  the differential has findings, which for this pair it does; that is success, not failure.
