# PS-150 — the verification tier is EXONERATED; the packaged engine is the live cause

**Both verdicts survive closing the tier-versus-product `geo_ext` gap, on one proven exit, on two thick
arms — and the geo vector was proved to reach the page rather than assumed.** Candidate #1 is closed by
measurement. Candidate #2 (the packaged engine) is the best-supported live cause, with a named mechanism.
Candidate #3 (the host) is **not settled**, and this record says so rather than claiming a clean sweep.

---

## 1. The answer, per verdict

The ticket requires each verdict to get its own answer. They still have not split — but what is now
excluded is different from what was excluded before.

| verdict | layer ON (arm A) | tier geo gap CLOSED (arm B) | answer |
|---|---|---|---|
| `masking_detected` | `true` | **`true`** | **not the tier's geo gap** |
| `fingerprint_inconsistent` | `true` | **`true`** | **not the tier's geo gap** |

All **12** pixelscan rows are byte-identical between arm A and arm B:

| item | arm A (no geo) | arm B (geo installed) |
|---|---|---|
| `masking_detected` | `true` | `true` |
| `fingerprint_inconsistent` | `true` | `true` |
| `fingerprint_consistent` | absent | absent |
| `proxy_detected` | absent (negated by "no") | absent (negated by "no") |
| `automation_detected` | absent (negated by "no") | absent (negated by "no") |
| `timezone_spoofed` | absent | absent |
| `geo_country_city` | `Poland / Warsaw` | `Poland / Warsaw` |
| `timezone_from_js` | `Europe/Warsaw` | `Europe/Warsaw` |
| `webgl_renderer` | `ANGLE (NVIDIA … RTX 3070 … D3D11)` | identical |
| `webgl_vendor` | `Google Inc. (NVIDIA)` | identical |
| `webgl_hash` | `036072f321775c68…` | identical |
| `canvas_hash` | `2bcfee1204804fa8…` | identical |

---

## 2. The gates — both PASS, checked PER CHECKER

Checked per checker rather than from the aggregate `evidence` floor, because PS-137 found records that
passed that floor with pixelscan contributing zero rows.

| | arm A (layer ON) | arm B (geo gap closed) |
|---|---|---|
| evidence verdict | `sufficient` | `sufficient` |
| fingerprint rows | **24 / 28** | **24 / 28** |
| `checkers_contributing` | sannysoft, creepjs, iphey, **pixelscan.net** | sannysoft, creepjs, iphey, **pixelscan.net** |
| pixelscan present | **yes** | **yes** |
| layer route / complete | `extensions` / `true` | `extensions` / `true` |
| vectors installed | 10 | **11 — `geo` added** |

**One exit, no rotation.** Both arms observed **`5.173.155.60`** — Warsaw / PL / `AS39603 P4 Sp. z o.o.` /
`Europe/Warsaw`. Identical on both. Arms taken 11.5 minutes apart (`16:22:33Z`, `16:34:05Z`).

This exit is **not** PS-143's `46.205.201.136`, so PS-143's arms could not serve as this ticket's control:
a fresh baseline arm was taken in this session rather than comparing across exits.

---

## 3. The saturation guard — and the trap it does NOT close

A differential where both arms return identical verdicts is worthless if the arms did not actually differ.

**4 of 61 rows moved:**

| row | sort | arm A | arm B |
|---|---|---|---|
| `iphey.com/trustworthy` | fingerprint | `true` | absent — *"the pattern did not match"* |
| `iphey.com/software_fine` | fingerprint | `true` | absent — *"the pattern did not match"* |
| `iphey.com/hardware_fine` | host | `true` | absent — *"the pattern did not match"* |
| `tls.peet.ws/observed_ip` | exit | `5.173.155.60:43007` | `5.173.155.60:48122` |

The `observed_ip` row is **ephemeral source port only — same address**, and is discounted here on exactly
the ground §2 discounts it. That leaves **3 fingerprint/host rows** that genuinely moved.

**That count is honestly weaker than PS-143's eight, and it is the wrong instrument for this question
anyway.** No checker in the matrix reads geolocation at all — `getCurrentPosition`, `watchPosition` and
`geolocation` return **zero hits** across `verify/checkers.py` and `verify/local_probe.py`. So a moved-row
count cannot show that the *geo* vector did anything, and an unmoved verdict is equally consistent with
"the extension landed and pixelscan does not care" and with "the extension never landed".

**Those are different findings, so the vector was observed directly** (the PS-78 rule: an assertion that a
builder was called is not evidence the spoof reached the page). `scripts/ps150_geo_reached.py` serves a
loopback page that calls `getCurrentPosition` and reads the outcome back through the tier's own
`inner_text`:

| arm | `getCurrentPosition` result |
|---|---|
| `include_geo=False` | `ERROR code=3 message=Timeout expired` |
| `include_geo=True` | **`ERROR code=1 message=User denied Geolocation`** |

`code=1` is `PERMISSION_DENIED` — persona's DENY-mode extension answering. **The geo vector demonstrably
reaches the page, and the verdicts still did not move.** The null is a real null, not a null instrument.

---

## 4. Why candidate #1's stated reason did not survive reading the product

The tier excluded `build_geo_extension` on the ground that it *"needs proxy coordinates this harness does
not carry"* (`verify/masking_layer.py:413`). **That reason is refuted by the product itself.**

`process.py:547` builds the extension for **every** proxied profile, and computes:

```python
has_coords = proxy.lat is not None and proxy.lon is not None
build_geo_extension(proxy.lat if has_coords else None, ...)
```

Coordinate-less is not a blocker — it is a case the builder is **designed** for and the product
**exercises**, precisely so `getCurrentPosition` cannot fall through to the real host coordinates while
locale and timezone already name the exit country. Every reading in this campaign is proxied, so the
product surface always carried this extension and the tier never did.

The gap was real and is now closable (`--match-product-geo`, default OFF so no existing reading moves).
**It is simply not the cause of these two verdicts.**

---

## 5. Candidate #2 — the packaged engine, with a named mechanism

The engine claims **hardware it is not using**, and pixelscan checks exactly that.

On arm A, **two checkers on the same page load disagree about the GPU**:

| checker | renderer | vendor |
|---|---|---|
| pixelscan | `ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 (0x00002484) Direct3D11 vs_5_0 ps_5_0, D3D11)` | `Google Inc. (NVIDIA)` |
| creepjs | `ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 vs_5_0 ps_5_0, D3D11)` | `Google Inc. (AMD)` |

And the deeper contradiction, which does **not** depend on that disagreement: both strings claim a
**Direct3D11** discrete GPU, while the actual rasteriser on this Linux host is **SwiftShader** (software).
`webgl_hash` is computed by pixelscan **from pixels it drew itself** — it is not a string persona can edit.
A believable hardware renderer string beside a hash produced by software rendering is the "the string is
right but the render gives us away" case, and it is exactly what `fingerprint_inconsistent` names.

This belongs to `--fingerprint`, i.e. **the engine persona ships** — it is present with the masking layer
OFF, so it is not the layer's doing. Contrast stock chromium, which declares its rasteriser honestly:
`ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`.

Per the ticket, this is stated plainly as a finding about the product with a real decision behind it —
patch, replace, or accept and document — rather than treated as out of reach.

**Two honest limits on this attribution:**

1. The pixelscan-vs-creepjs *disagreement* is introduced by the layer (PS-143 measured it on arm A only),
   yet the verdict fires with the layer off. So the disagreement **cannot be the sole cause**. The
   claimed-vs-rendered contradiction, which survives layer removal, is the part that carries this section.
2. It is a **mechanism consistent with the verdicts**, not a demonstrated cause. Nothing here removes the
   claim and re-reads. That experiment is named in §7.

---

## 6. Candidate #3 — the host is NOT settled, and the control that looked decisive is confounded

`scripts/ps150_stock_control.py` read pixelscan under Debian's **stock** `/usr/bin/chromium` 151 — zero
persona code — through the same exit, same host, same no-sandbox waiver:

| item | stock chromium | packaged engine (layer OFF) |
|---|---|---|
| `masking_detected` | **`true`** | **`true`** |
| `fingerprint_inconsistent` | **`true`** | **`true`** |
| `automation_detected` | **`true`** | absent |
| `timezone_spoofed` | **`true`** | absent |
| `timezone_from_js` | **`Africa/Abidjan`** | `Europe/Warsaw` |
| `geo_country_city` | absent | `Poland / Warsaw` |
| `webgl_renderer` | `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device…))` | `ANGLE (AMD … D3D11)` |

**This reads as "the host does it too" and that conclusion is NOT drawn here**, because the stock arm is
confounded on two independent axes, each on its own sufficient to produce both verdicts:

- **Timezone incoherence.** Stock ignores `--timezone` (that flag is a fingerprint-chromium patch), so it
  reported `Africa/Abidjan` behind a Warsaw exit — and pixelscan flagged `timezone_spoofed` accordingly.
  A browser whose clock contradicts its address is *genuinely* inconsistent. This is the PS-132 defect
  reproduced in the control arm.
- **Automation detection.** Stock tripped `automation_detected`, which the packaged engine does not.

So the stock arm shows only that **some** configuration on this host trips these verdicts — not that the
host trips them for a *coherent* browser. It is recorded as a lead and explicitly not as an attribution.

**The sandbox waiver could not be lifted, and that limit is real.** `sandbox_available()` returns `False`;
`sysctl -w kernel.unprivileged_userns_clone=1` and `user.max_user_namespaces` were both refused
(*permission denied*) despite sudo. Per the ticket's own rule — *confirm on a sandboxed host before
attributing anything to the product* — **no cause is attributed to the product as a final answer here.**
§5 names the best-supported candidate and §7 names what would settle it.

---

## 7. What would settle it (not done here — identification was the scope)

1. **Remove the claim, keep everything else.** Read the packaged engine with the GPU/WebGL vectors
   subtracted so the engine stops claiming D3D11 hardware, on the same exit. If `fingerprint_inconsistent`
   clears, §5's mechanism is the cause. This is the one-axis subtraction the harness already supports.
2. **A sandboxed host.** Every reading in this campaign carries `--allow-unsandboxed-chromium`. Until one
   run happens without it, no cause can be finally attributed to the product.
3. **Give stock a coherent timezone.** Re-run the control with the host clock set to `Europe/Warsaw` so
   the stock arm is not self-contradicting. That is what turns §6 from a lead into an answer about the host.

---

## 8. Conditions and waivers — one waiver, disclosed

| constraint | status |
|---|---|
| proven exit, no rotation between arms | **PASS** — `5.173.155.60` on both (§2) |
| `--allow-unsandboxed-chromium` | **REQUIRED and passed — the only waiver on this record** |
| `/dev/shm` | `1.0G`. `--allow-small-dev-shm` **NOT** passed; `dev_shm_waived` appears on neither record |
| credential | `/workspace/_secrets/test-proxy.txt`, read from the file |

**The one waiver, stated plainly:** every arm ran with `--no-sandbox`, because this host forbids the
unprivileged user namespace (`sandbox_available() → False`, and raising the sysctls was refused).
persona's own launch path passes that flag nowhere, so **no arm here is the product's default surface.**
It applies equally to every arm, so it does not bias the differential — but a reading taken under it is
not a reading of the shipped product, and every conclusion above inherits that caveat.

**Run coordinates:** engine `fingerprint-chromium/148.0.7778.215`, seed `9001`, declared machine `windows`
(honoured), environment `linux-x86_64 (agent sandbox)`.

---

## 9. A correction to this ticket's own record

The confirm-review comment states that `verify/masking_layer.py` *"does not exist on `main`"* and lives
only on feature branches. **That is wrong.** `git ls-tree origin/main --name-only src/services/verify/`
lists it, at 500 lines, and both cited premises were verified on the checked-out tree:

- `process.py:547` — `build_geo_extension(...)` inside `if proxy:`, DENY-mode rationale in the comment. **Confirmed.**
- `verify/masking_layer.py:413` — *"``build_geo_extension`` needs proxy coordinates this harness does not carry"*. **Confirmed at exactly line 413.**

The path correction in that comment (`src/services/verify/`, no `browser/` segment) **is** right and was
useful. Only the absent-from-`main` claim is withdrawn.

---

## 10. Environment findings (for whoever runs the next one)

The container was **not** provisioned to take this reading — PS-143 §6 reported the same, so this is the
steady state rather than a one-off:

1. `Xvfb` missing → `apt-get install xvfb`.
2. `.venv` present but `requirements.txt` not installed; **`invisible_playwright` and `invisible_core` are
   in `pyproject.toml` only**, so installing `requirements.txt` alone leaves the engine unimportable.
3. persona's Chromium engine absent at `~/.persona/engine/fpchrome.AppImage` (`builds.json` carried
   placeholder digests). Provisioned via `updater.ensure_engine()` → `148.0.7778.215`, digest-verified.
4. **Import root is the repo**: `from src.services...`. `sys.path.insert(0, "src")` fails with
   *"attempted relative import beyond top-level package"*.
5. `~/.persona/proxies.json` holds only a fixture `socks5://1.2.3.4:1080`, which is unreachable and is
   **not** the run's exit — the credential file is.
6. The loopback smoke test (`checker_cli differential --engine chromium --axis layer`) returned
   `MOVED: 3 of 4` before any live read was spent. It is cheap, needs no exit, and is worth running first.

---

## Artifacts

- `arm-a-baseline-layer-on.json` — arm A, layer ON, 10 vectors
- `arm-b-geo-gap-closed.json` — arm B, layer ON + `geo`, 11 vectors
- `arm-c-stock-vs-packaged.log` — stock chromium vs packaged engine (layer off), same exit

Reproduce the comparison:

```bash
python -m src.services.verify.checker_cli compare \
  readings/ps150-2026-08-24/arm-a-baseline-layer-on.json \
  readings/ps150-2026-08-24/arm-b-geo-gap-closed.json
```
