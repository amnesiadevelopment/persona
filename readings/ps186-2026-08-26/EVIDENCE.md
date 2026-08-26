# PS-186 — the 7 arms PS-177 lost, re-run on the new credential

**Taken 2026-08-26, `origin/main` @ `6dffce6`, branch `feature/ps186-checker-sweep-new-credential`.**

PS-177 planned 8 configurations and obtained 1: the proxy credential stopped
authenticating after the first record and the remaining 7 were REFUSED rather
than read over a direct connection. **This sweep obtained all 8.** Nothing was
lost, so there is no "not obtained" list to explain — §"Not covered" below states
what remains unreadable *by construction*, which is a different thing.

Every number in this file and in the PS-16 patch was printed by a script from the
committed records. Nothing is hand-typed. Re-derive with:

```
./derive.py matrix              # inherited from PS-177, UNMODIFIED
./derive_ps186.py               # this ticket's cross-corpus claims
```

- `derived-output.txt` — output of the inherited instrument
- `derived-output.ps186.txt` — output of this ticket's instrument
- `take-sweep.sh` — the instrument that produced `matrix/` (copied verbatim from
  PS-177 so the sweep is reproducible from this directory alone)
- `sweep.log` — the run's own transcript

---

## The credential: live, Polish, and rotating harder than the ticket assumed

The gate ran before any browser launched, exactly as the ticket requires:

```
curl -x "$(head -1 /workspace/_secrets/test-proxy.txt | sed 's#^socks5://#socks5h://#')" https://api.ipify.org
-> 46.205.193.106
```

No `User was rejected by the SOCKS5 server (1 3)`. The PS-177 blocker is gone.

### ⚠️ CORRECTION — `AS12912 T-Mobile Polska` is NOT the pool's identity

The ticket states the new credential is "the **same ASN** already in the corpus,
and the same `/24` as the `ps135` record" and builds its confound analysis on
that. **Measured, the pool is materially wider.** Four probe calls before the
sweep returned four different ASNs, and the sweep's own 8 records carry five:

| ASN | records in this sweep | in the old corpus? |
|---|---|---|
| AS12912 T-Mobile Polska | 3 | yes (2) |
| AS39603 P4 Sp. z o.o. | 2 | yes (2) |
| AS9141 P4 Sp. z o.o. | 1 | yes (6) |
| AS5617 Orange Polska | 1 | yes (10+4) |
| **AS14593 Space Exploration Technologies (Starlink)** | **1** | **NO — new to the corpus** |

Probing (not in the records) also returned **AS29167 Netia SA**, likewise absent
from the corpus. `ip-api` reports every exit `mobile: false`.

**What this changes, and what it does not.** The ticket's *direction* is right —
the pools overlap heavily, four of five ASNs are already in the corpus, so
old-vs-new is not a category jump and a moved fingerprint row is still far more
likely to be the product than the pool. But the specific claim that the new
credential *is* one ASN adjacent to an existing record is false: T-Mobile was one
draw from a rotating set, not the pool's identity. A record hard-coding it would
be wrong for 5 of 8 rows here. **Every record carries its own `org`**, per the
ticket's instruction, and that instruction is now measured rather than prudential.

Starlink is the one genuinely new animal — a LEO satellite ISP is not a
residential DSL line and not a mobile carrier. It drew `chromium/linux/seed 5150`.
No fingerprint row on that record diverges from its `seed 24601` sibling in a way
attributable to the exit (see §"GPU identity"), but it is flagged here because a
later reader diffing that record against anything should know.

### Two smaller corrections

- **The `46.205.201.136` record is `ps143`, not `ps135`.** Both its arms
  (`arm-a-layer-on.json`, `arm-b-layer-off.json`) carry it. `ps135` carries no
  such address.
- **The ticket's own correction is confirmed.** The old pool was **mobile** —
  Orange / P4 (Play) / T-Mobile, counted from 24 corpus records — not datacenter.

### Stickiness mode

**ROTATING, on every record — no sticky-session token was requested or used.**
This is the correct mode per the ticket: this sweep contains **no A/B
differential**. Its comparisons are seed-vs-seed *independent confirmations*,
which the ticket says are *better* with rotation because a finding surviving a
different exit, ASN and day is not exit-driven. The headline finding below
survived four exits across three days, which is exactly that.

A differential (a layer-off control) would have required the token. None was run,
so none was needed.

---

## 1. HEADLINE — the firefox `webgl_pixel_hash` bound: n=2 → **n=4**, settled

The ticket's priority #1. PS-16 records the Level-2 failure at a bound of n=2 and
says "a third firefox seed settles it". This delivers a **fourth**.

| engine | seed | hash | exit | ASN | day |
|---|---|---|---|---|---|
| firefox | 1337 | `51df3565` | `95.49.113.111` | Orange | 08-23 |
| firefox | 1337 | `51df3565` | `83.175.185.209` | AS9141 P4 | 08-24 |
| firefox | 4242 | `51df3565` | `95.49.113.111` | Orange | 08-23 |
| firefox | 4242 | `51df3565` | `83.175.185.209` | AS9141 P4 | 08-24 |
| **firefox** | **5150** | **`51df3565`** | `109.243.64.38` | AS39603 P4 | **08-26** |
| **firefox** | **24601** | **`51df3565`** | `109.243.69.197` | AS39603 P4 | **08-26** |

**6 readings, 4 distinct seeds, 4 exits, 3 days — ONE hash value.**

The chromium control, from the same script and the same corpus, moves the
opposite way:

**18 readings, 5 distinct seeds, 5 distinct hash values** — `f801a1b3` @1337,
`a96eedf0` @2024, `b8dba17f` @9001, **`c893e6c9` @5150**, **`6ba330fa` @24601`.
Every chromium seed reads its own value; every firefox seed reads the same one.

**So this is not a checker constant and not a collision.** The "those two
happened to collide" explanation is dead: two *previously unused* seeds, chosen
by PS-177 precisely because they appear nowhere in the corpus, both landed on the
identical value on a different day through different exits. `creepjs ::
webgl_pixel_hash` is **seed-invariant on the firefox leg and seed-derived on
chromium**, which is the shape of a masking gap on firefox, not a property of the
checker.

Two firefox profiles at different seeds carry an identical high-entropy row and
are therefore **linkable to each other**. Level 2 FAILS on firefox/windows/desktop
at **n=4**.

**Owned by PS-182 — measured and reported here, not fixed** (out of scope).

---

## 2. Priority #2 — pixelscan and creepjs on firefox are READ

The two checkers the owner judges Firefox by, and exactly the two PS-177 lost to
the dying exit (they sit last in browser-tier catalogue order).

| | PS-177 (firefox/win/5150) | **PS-186 (both firefox records)** |
|---|---|---|
| creepjs | **0 read** / 9 unobtainable | **9 read** / 0 unobtainable |
| pixelscan | **0 read** / 12 unobtainable | **7 read** / 5 absent / 0 unobtainable |
| fingerprint rows | 7/28 (25.0%) | **28/28 (100%)** |
| adverse verdicts fired | 0 | **0** |

**Zero adverse verdicts fired on either firefox record** — no row is both `read`
and `adverse`.

**And pixelscan does not merely stay silent on firefox — it affirms.** It reads
the positive-polarity row:

```
pixelscan.net :: fingerprint_consistent = True
  matched_text: "Your Browser Fingerprint is consistent"
```

That is the first *automated, committed, diffable* evidence for the surface the
owner previously judged by eyesight alone. His `~100` / `~75` figures are still
**not** carried into PS-16 — they remain unreproducible — but the cells they
described are no longer blank, and what fills them was measured here.

---

## 3. ⚠️ NEW FINDING — the two-GPU defect class is ALIVE on macos and linux

PS-16 records the PS-170 GPU-identity fix as "**fixed and verified**": one profile
used to hand two different graphics cards to two checkers on the same run. That
verification was only ever possible on **chromium/windows**, the one arm with any
checker coverage. macos and linux were `—` in every column.

**These are the first readings that could check the other arms, and both fail.**

| arm | creepjs `gpu_renderer` | pixelscan `webgl_renderer` | |
|---|---|---|---|
| chromium/windows/5150 | NVIDIA RTX 3060 Laptop `0x2560` | NVIDIA RTX 3060 Laptop `0x2560` | **AGREE** |
| chromium/windows/24601 | AMD Radeon `0x1638` | AMD Radeon `0x1638` | **AGREE** |
| firefox/windows/5150 | NVIDIA GTX 980 | NVIDIA GTX 980 | **AGREE** |
| firefox/windows/24601 | NVIDIA GTX 980 | NVIDIA GTX 980 | **AGREE** |
| **chromium/macos/5150** | Apple **M4** | Apple **M2 Pro** | **⚠ DISAGREE** |
| **chromium/macos/24601** | Apple **M2** | Apple **M1** | **⚠ DISAGREE** |
| **chromium/linux/5150** | **SwiftShader** (Google, Vulkan 1.3.0, Subzero) | Intel **Iris Xe** | **⚠ DISAGREE** |
| **chromium/linux/24601** | **SwiftShader** (Google, Vulkan 1.3.0, Subzero) | Intel **UHD 630** | **⚠ DISAGREE** |

**The windows rows are the internal control**: the same instrument, same run,
same comparison produces AGREE there. So DISAGREE elsewhere is not a measurement
artifact.

**The two authors are identifiable in the source.** pixelscan's values are
*verbatim entries of our own pools* — `ANGLE (Intel, Mesa Intel(R) UHD Graphics
630 (CFL GT2), OpenGL 4.6)` and the Iris Xe string are `LINUX_GPUS[4]` and
`LINUX_GPUS[5]`; `Apple M1` and `Apple M2 Pro` are exactly the two `MAC_GPUS`
entries. creepjs's values are what `gpu_ext.py`'s own header measured the
**engine** producing (macos: "only TWO values across 30 seeds, Apple M2 87%,
Apple M4 13%" — precisely the M2 and M4 seen here).

So on macos and linux, **our masking layer authors one card and the engine
authors another, and different checkers see different ones.** That is the exact
defect class PS-170 closed on windows — where `ENGINE_AUTHORED_IDENTITY_ARMS =
frozenset({"windows"})` makes both authors the same one.

**Linux is the more serious half**: creepjs is reading `SwiftShader`, the
container's *real* software rasteriser. That is not a plausible consumer GPU and
it is the host's true renderer leaking past the mask on the arm where PS-16
notes the engine returns "one identical SwiftShader string on every seed" — the
100%-collision case `LINUX_GPUS` exists to prevent.

**Not fixed here — out of scope** (the ticket assigns fixes to PS-182/PS-183 and
says measure and report). Flagged because PS-16 currently records this class as
verified-closed, which is true only of the arm that had coverage.

**This is the two-engine rule paying out exactly as PS-16 predicts**: filling a
cell for one arm made the sibling's blank legible.

---

## 4. ⚠️ CORRECTION to PS-16 — 2 of the 8 "structurally unobtainable" checkers ARE readable

PS-16 lists 8 checkers "no automated run can obtain", three of them because they
"refuse our SOCKS exit". That list was written against the **old pool**. Re-counted
against these records:

| checker | PS-16 says | this sweep | |
|---|---|---|---|
| browserscan.net | signed params 401 | 8 unobtainable | holds |
| amiunique.org | click-gated | 8 unobtainable | holds |
| coveryourtracks.eff.org | click-gated | 8 unobtainable | holds |
| whoer.net | Cloudflare (out of scope by charter) | 8 unobtainable | holds |
| fv.pro | paywalled | 8 unobtainable | holds |
| tools.scrapfly.io | refuses our SOCKS exit | 16 unobtainable | holds |
| **bot-detector.rebrowser.net** | **refuses our SOCKS exit** | **14 read, 2 absent** | **CONTRADICTED** |
| **deviceandbrowserinfo.com** | **refuses our SOCKS exit** | **5 read, 11 absent** | **CONTRADICTED** |

The two that were failing with `ERR_SOCKS_CONNECTION_FAILED` now answer. The most
economical explanation is the pool change — they refused the *old* exits, not our
harness — which is itself a small measured consequence of the mobile→residential
move. **The structurally-unobtainable list is 6, not 8.**

This matters beyond bookkeeping: both newly-readable checkers **fire adverse
verdicts**, so PS-16's "only two adverse markers fire anywhere in the whole
matrix" is no longer true.

### What they say

```
deviceandbrowserinfo.com :: bot_verdict_positive = True   (5 of 6 chromium records)
bot-detector.rebrowser.net :: detected = "navigatorWebdriver"  (all 6 chromium records)
```

**Neither fires on either firefox record.** rebrowser's row is `absent` on
firefox — the page was read and the adverse pattern did not match.

**On `navigatorWebdriver`, read the cross-check before concluding.**
`sannysoft :: webdriver_present` is **absent** on all 8 records, and both
`webdriver_advanced_passed` and `webdriver_missing_passed` read `True` on all 8.
So two checkers disagree about the same property on the same runs. This is
reported as **an open discrepancy, not as a confirmed webdriver leak** — settling
it needs a layer-off control on this credential (the ticket's own prescribed
method), which is a differential and would need the sticky token. Not run here.

---

## 5. pixelscan verdicts — all five rows, and a coverage hole worth more than the verdicts

The ticket requires `proxy_detected` recorded explicitly on every record **even
when absent**, as the cheapest witness to whether the pool change is visible.

**`pixelscan :: proxy_detected` is `absent` on all 8 records.** It was also absent
throughout the old corpus. So on this evidence the mobile→residential pool change
is **not visible to pixelscan as a proxy** — the one witness the ticket asked for,
answered.

`automation_detected` and `timezone_spoofed`: `absent` on all 8.

`fingerprint_inconsistent` / `masking_detected`: fired on **3 of 8** records
(chromium windows ×2, chromium macos/24601). PS-16 says these fire in *every* arm
we own, including layer-off and stock-chromium controls — so 5 records not firing
them looks like a contradiction.

**It is not. It is a coverage hole, and the distinction is invisible if you read
only the adverse row.** pixelscan states the same fact in two opposite-polarity
rows. Reading whether *either* rendered separates "pixelscan cleared us" from
"pixelscan never said anything":

| record | verdict block |
|---|---|
| chromium/windows/5150 | RENDERED → INCONSISTENT (fired) |
| chromium/windows/24601 | RENDERED → INCONSISTENT (fired) |
| chromium/macos/24601 | RENDERED → INCONSISTENT (fired) |
| firefox/windows/5150 | RENDERED → **CONSISTENT** |
| firefox/windows/24601 | RENDERED → **CONSISTENT** |
| **chromium/macos/5150** | **⚠ NO VERDICT RENDERED** |
| **chromium/linux/5150** | **⚠ NO VERDICT RENDERED** |
| **chromium/linux/24601** | **⚠ NO VERDICT RENDERED** |

Those three records have **no pixelscan verdict at all**. Scoring them as clean
would be a false green of precisely the kind PS-11 documents — the row is absent
because nothing rendered, not because we passed. They are recorded as **not
covered for the pixelscan verdict**, and the linux cell in PS-16 says so rather
than carrying a score.

---

## 6. Level 2 across the other arms

From the inherited instrument (`derived-output.txt`), per arm, comparing
fingerprint rows read on **both** seeds:

| arm | entropy rows both sides | differ | identical | verdict |
|---|---|---|---|---|
| firefox / windows | 9 | 4 | **5** | **FAILS** |
| chromium / windows | 10 | 9 | 1 | FAILS (see caveat) |
| chromium / macos | 10 | 7 | 3 | **FAILS** |
| chromium / linux | 10 | 6 | 4 | **FAILS** |

The identical rows on firefox are the pixel hash plus the GPU renderer/vendor
strings on both checkers. On macos and linux they are the GPU vendor strings —
`Google Inc. (Apple)` and `Google Inc. (Intel)` / `Google Inc. (Google)` — which
are constant across seeds because the *pool* is one vendor deep in those slots.

### ⚠️ Caveat on the inherited classifier — read before quoting its row list

`derive.py` classifies `bot-detector.rebrowser.net :: detected =
"navigatorWebdriver"` as **entropy-bearing**, so it appears in its "rows that tie
the two profiles together" list on all three chromium arms. **That
classification is wrong**, in the direction its own docstring warns about.

The value is a **detector's reason token** — the name of the signal that tripped —
not an attribute of the profile. It reaches the entropy class only by being a
non-numeric string of ≥8 characters with no `vector` tag. Two profiles both
reading `navigatorWebdriver` are *both detected for the same reason*; they are
not thereby linkable, any more than two profiles both reading "webdriver check:
passed" are.

**No verdict above changes**: every arm it appears on fails on other, genuinely
entropy-bearing rows. But `chromium/windows` reaches FAILS on that row **alone**,
so that one arm's verdict rests entirely on a misclassification and should be
read as **UNANSWERABLE from this sweep** rather than as a failure. The row is a
real fired verdict (§4) — the caveat is about what it can *link*, not whether it
fired.

The inherited script is committed **unmodified** and this correction is stated
here rather than patched into it, so PS-177's output remains reproducible.

---

## Not covered, and why

Nothing was lost to the exit. These are unreadable **by construction**:

- **firefox / macos and firefox / linux — the configuration does not exist.**
  `InvisiblePlaywright` has no OS parameter (product issue #211); firefox presents
  Windows whatever is requested. The sweep printed the collapse and wrote
  `declared_machine_honoured: false`. *The engine cannot do this* — not an exit
  failure.
- **Any mobile arm.** This tier exposes no `device_type` selector, so every record
  it can produce is desktop. `windows`+`mobile` — the arm PS-161 round 4 repaired
  — remains unreadable here. Explicitly out of scope in the ticket.
- **6 checkers** (§4) — structurally unobtainable, and expected absent per the
  ticket. Not a finding.
- **A layer-off control on this credential.** Would settle both the
  `navigatorWebdriver` discrepancy (§4) and whether the pool causes any pixelscan
  movement. It is a **differential**, so it needs the sticky-session token; the
  token was not requested because no differential was in this ticket's scope.
  **This is the single highest-value follow-up.**
- **The renderer axis.** This container has no GPU. PS-16's standing note applies
  unchanged: a red pixelscan here is not evidence against our masking, and a green
  one cannot be produced from this seat.

## Environment

- `origin/main` @ `6dffce6`; engine `148.0.7778.215`, sha256
  `a5fa5e6c05cb7fa3617ec2ca642ad3cc6e586ac5249cc29edb0a602d695685f0` — **byte-identical
  to the engine PS-170's reviewer provisioned independently**.
- `--allow-unsandboxed-chromium` is a **disclosed waiver**, as in PS-170/PS-177:
  this host forbids the unprivileged user namespace chromium's sandbox needs.
  persona's own launch path passes it nowhere, so these are not the product's
  default surface. `--allow-small-dev-shm` was **not** taken (`/dev/shm` is 1.0 GiB,
  above the 256 MiB floor).
- The container arrived unprovisioned. Notably the engine directory held a
  **stale sentinel** — `.engine-complete` reading `ok` and a `builds.json` naming
  `148.0.7778.215`, but **no AppImage** and single-character digests (`"a"`, `"n"`).
  `is_installed()` correctly refused it (it requires a non-empty binary *and* the
  marker) and re-downloaded through the digest-verified path. The fail-closed gate
  worked; a weaker check would have launched nothing and blamed the product.
- A **loopback differential** was run before any live arm and returned `moved`
  (`intl_locale` de-DE→en-US, `webgl_pixel_hash`, `audio_digest`), confirming the
  masking layer reaches the page. That is why §3's linux finding is read as
  engine-vs-layer disagreement rather than as the layer being absent.
- The harness reported, on every record: **the credential file and
  `PERSONA_TEST_PROXY` hold different credentials.** It used the file and said so.
  Consistent with the standing rule never to read the env var.
- `proxy bridge: REFUSED a local connection from peer port … it does not belong to
  browser process …` appears on 2 records. That is the bridge's peer-ownership
  guard refusing a connection it cannot attribute to the browser — **not** a
  credential failure; both records wrote normally at 45 read immediately after.
