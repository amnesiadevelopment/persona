# PS-374 — is `Runtime.enable` still suppressed in the shipped engine, and what would catch it if it stopped?

**Measured:** 2026-09-09, Linux container, `--headless=new`.
**Verdict:** ⭐ **Patch 001 is present in the shipped binary and is acting.** The
guard that would have caught it going missing did not exist; it does now, and it
has been watched to fail three separate ways.

---

## 0. What was asked, and what was already known

The ticket asks three things: verify the patch on the SHIPPED engine over the
real CDP path, add the guard that does not exist, and falsify it.

Comment `9afca5c8` (persona-researcher-2, 2026-09-07) had already measured this
and located a root cause; comment `d5cc456e` added three findings on top. ⛔ **No
part of that work is re-derived here as if it were new.** What this reading adds
is: an independent reproduction of the measurement with a NEW instrument, the
GUARD the researcher explicitly did not write ("I am the researcher seat … the
fix and its behavioural test are worker work"), and the falsifications.

---

## 1. The measurement — reproduced independently, on the shipped artifact

```
gh release download personium-152.0.7977.75 --pattern '*linux-x86_64.AppImage'
sha256  6ddb7bbea0a2063b7a3618e6b5d4ebc96301cd80f8b0d6eae486af46a30bb4c3
        == engine/releases/personium-152.0.7977.75.json   ->  ✅ genuinely the shipped artifact
```

Extracted, launched with `--remote-debugging-port=0 --remote-allow-origins=*`
(the two switches `process.py:1438,1445` append for an `ai_control` profile),
driven over a raw stdlib WebSocket — no playwright, which is not a dependency of
this project.

| | site A `executionContextCreated` | site B console AFTER `Runtime.enable` |
|---|---|---|
| **SHIPPED** personium 152.0.7977.75 (V8 15.2.124.19) | 1 — **announced** | **SUPPRESSED** |
| **STOCK** chromium 152.0.7977.82 | 1 — announced | reported |

All six preconditions met on both arms. Artifacts:
`artifacts/shipped-personium.json`, `artifacts/stock-chromium.json`.

This agrees with `9afca5c8` on every cell, from a probe written without reading
its code.

### Why site B is the oracle and site A is not

Patch 001 converts exactly two `m_enabled` reads. It does **not** convert
`reportExecutionContextCreated`, which still reads the raw field. So:

* **Site A** (`executionContextCreated`) is the LEAK. ⛔ It is **not evidence
  about our patch** — the patch never covered this site, so a leak here is a
  SCOPE fact about patch 001, not a sign the patch went missing.
* **Site B** (`messageAdded` → console) IS routed through `enabled()`. It is the
  only one of the two that can tell *"the patch is in this binary"* from *"the
  patch was lost in a rebase"* — which is the question the ticket asks.

⭐ **The guard therefore keys on site B.** Keying it on site A would make it
permanently RED against a correctly-patched binary — an assertion no fix could
satisfy, which is the permanently-red gate this project already records as worse
than no gate at all. Site A is measured, recorded and reported in the headline;
it is not the pass/fail.

### The reading is by PAYLOAD, never by count

`Runtime.enable` REPLAYS stored console history on a different code path from
`messageAdded`, so a message logged BEFORE enable arrives on a patched binary
too — the shipped arm's `console_payloads` carries exactly the BEFORE marker and
not the AFTER one. Two distinctly-marked messages is what makes this an oracle;
`consoleAPICalled` cardinality is a real number and an uninterpretable one.
`test_before_marker_alone_does_not_condemn` pins that with a pair of readings
carrying the SAME event count and opposite verdicts.

### The probe refuses rather than reporting clean

Six preconditions, any failure yielding **INCONCLUSIVE** — a third exit code,
deliberately not folded into either verdict:

| | |
|---|---|
| P1 | the debugging endpoint answered |
| P2 | a page target exists |
| P3 | the WebSocket handshake returned 101 (and the accept key checked) |
| P4 | `Page.navigate` acked; **P4b** `1+1` → `2`, so a REAL context exists |
| **P5** | ⭐ an UNSOLICITED event actually arrived — the event channel is LIVE |
| P6 | `Runtime.enable` acked with no error object |

P5 is load-bearing: without it *"the realm is clean"* and *"the realm was never
reached"* are the same green, and on an absence assertion the second is the more
likely failure. `test_all_six_preconditions_are_gated_not_merely_recorded`
asserts every one of the seven can force INCONCLUSIVE on its own, so none can be
quietly demoted to decoration.

---

## 2. The guard gap — and a FALSE GREEN in the guard that did exist

The ticket's own probe reproduces:

```
git grep -rln "Runtime.enable" -- tests/ .github/   ->  0 hits
```

But `scripts/ps307_verify_patches_in_tree.sh` DOES verify all 16 patches are in
the tree about to be compiled — the ticket's grep could not see it. As
`9afca5c8` found, and as this reading re-derived by execution: **for patch 001 it
was looking for the wrong six lines.**

The extractor took the first `MAX_PER_FILE` (3) usable added lines per file **in
patch order**, and each of 001's hunks opens with a multi-line
`// persona fingerprint: …` block ahead of its ONE code line:

```
v8-runtime-agent-impl.cc   3 claims, all `// persona fingerprint: ...`
v8-runtime-agent-impl.h    3 claims, all `// persona fingerprint: ...`
claims pinning `return false`, `if (!enabled())`, `if (enabled())`:  0
```

`scripts/ps374_falsify_patch_evidence.sh` builds a genuinely-patched synthetic
tree, reverts exactly those three code lines to `m_enabled`, leaves every comment
in place — the shape a bad rebase or a careless revert produces — and runs the
verifier's own matcher (`grep -F` over a whitespace-stripped copy) over both:

```
⛔ FALSE GREEN DEMONSTRATED — 6 of 6 claims hold over a completely reverted patch 001
```

**Fleet-wide, 001 was the only offender** (measured across all 16: every other
patch pinned at least one code line).

### The fix: RANK, do not raise the cap

`scripts/ps307_patch_evidence.awk` now collects candidates into two buckets and
fills the budget from the CODE bucket before topping up from the COMMENT one.

Raising `MAX_PER_FILE` to 4 was the alternative and is strictly worse: it fixes
001 at *today's* comment lengths and says nothing about the next patch whose
prose runs one line longer. Ordering makes the property hold for any cap and any
patch.

It is a **preference, not an exclusion** — a hunk that genuinely adds only prose
must stay checkable, since the ps307 verifier treats an unverifiable patch as a
hard failure. And the **cap still binds**: this changes WHICH candidates are
taken, never HOW MANY, so the check's cost over a Chromium-sized tree is
unchanged.

| | before | after |
|---|---|---|
| patch 001 claims | 6 comment / **0 code** | 4 comment / **2 code** |
| comment claims, all 16 patches | 23 | 6 |
| patches with zero code claims | **1** (001) | **0** |
| `added`/`removed` claims per file | ≤ 3 | ≤ 3 (unchanged) |

After the fix the same script reports:

```
✅ GUARD IS SIGHTED — 2 claim(s) pin code, and the sabotaged tree fails 2 of them.
```

The existing `tests/test_ps307_tree_reuse.py` (39 tests, including its real
patched/unmodified tree fixtures) is green on the change.

---

## 3. ⛔ Falsification — three of them, all executed

A guard that has never been seen to fail is decoration, and this project has a
documented history of exactly that.

| # | sabotage | result |
|---|---|---|
| 1 | live arm pointed at **stock chromium** — a genuinely unpatched real binary, not a fixture | **RED**, exit 1, naming `messageAdded` and the AFTER marker |
| 2 | patch 001's three **code lines reverted**, comments left intact | **6 tests RED** |
| 3 | the **awk ranking fix reverted** | **6 tests RED** |

Each was restored and the suite re-run green afterwards.

Sabotage 1 is the strongest of the three: the falsification arm is a real binary
whose V8 genuinely lacks the patch, so it demonstrates the instrument can read
"unpatched" off a live CDP channel rather than only off a synthesised reading.

---

## 4. The exposed population — narrower than "every profile"

`automation_channel.opens_cdp_channel` requires **both** `ai_control` AND
`effective_engine(profile) == "chromium"`. Firefox opens no CDP port at any
`ai_control` value.

⭐ The **stored-vs-effective** distinction is the half that is easy to get
backwards: an android profile STORING `engine="firefox"` is reconciled to
chromium and DOES open a channel, so reading the stored field would report a
closed channel over a listening port. All four cells are asserted in
`test_cdp_is_opt_in_and_both_conditions_are_required`.

---

## 5. What was NOT done, and what this does not say

* ⛔ **The patch was not re-implemented, and rebrowser's driver-side approach was
  not ported.** Two mechanisms over one property drift, and the weaker becomes
  the one people trust.
* ⛔ **Site A was not fixed.** Routing `reportExecutionContextCreated` (and its
  siblings `:1215` / `:1225` / `:1181`) through `enabled()` is a code change to
  the patch — a FIX ticket. PS-374 is a verification ticket, and its own brief
  says so. The guard is deliberately two-sided so that fix has a target: the
  assertion "no `executionContextCreated` after `Runtime.enable`" is red today
  and **reachable** (measured on the same probe: enable NOT called → 0 events;
  enable called → 1), so it is a real target rather than a tautology.
* ⛔ **Nothing here touches the fingerprint axis (PS-373).** This is
  automation-detection only — "is this a bot", not "is this machine real". No
  canvas/WebGL claim is made.
* ⛔ **No checker verdict was consulted.** creepjs/iphey read many bot signals at
  once; a clean verdict would not prove this patch works and a bot tell would not
  prove this patch is the cause. Everything above is read off the CDP channel
  directly.

## Bounds

* **Linux AppImage only**, `--headless=new`. Headful and the Windows/macOS assets
  are unmeasured.
* The `:1215` / `:1225` / `:1181` siblings are named **from `9afca5c8`'s source
  reading only** — only the `executionContextCreated` site was measured here, and
  the V8 line numbers come from upstream tag 15.2.124.19 (matching the binary's
  reported version) and were **not** confirmed by disassembly.
* The live test arm is **skipped, never silently passed**, where no engine binary
  is provisioned (`PS374_ENGINE_BINARY`). An absent engine must not read as a
  clean bill of health — see `tests/KNOWN_SKIPS.md`. It is marked
  `requires_capability("browser_chromium")`, the capability `conftest.py` already
  names as the gap nothing provisions, so declaring that capability makes the
  skip a failure.
* The committed readings are **re-derived** through `verdict()` in the tests
  rather than trusted from their stored `verdict` block, so a change to the
  decision logic that contradicts the measurement fails loudly.

## Reproducing

```bash
gh release download personium-152.0.7977.75 --pattern '*linux-x86_64.AppImage'
chmod +x personium-*.AppImage && ./personium-*.AppImage --appimage-extract

python3 -m scripts.ps374_runtime_enable_probe \
    --binary squashfs-root/opt/ungoogled-chromium/chrome --label shipped \
    -o /tmp/shipped.json                       # exit 0 — patch present
python3 -m scripts.ps374_runtime_enable_probe \
    --binary /usr/bin/chromium --label stock \
    -o /tmp/stock.json                         # exit 1 — the falsification

scripts/ps374_falsify_patch_evidence.sh        # the static guard's own falsification
python3 -m pytest tests/test_ps374_runtime_enable_guard.py tests/test_ps307_tree_reuse.py
PS374_ENGINE_BINARY=squashfs-root/opt/ungoogled-chromium/chrome \
    python3 -m pytest tests/test_ps374_runtime_enable_guard.py -k live
```
