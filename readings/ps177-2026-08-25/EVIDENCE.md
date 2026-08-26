# PS-177 — the baseline sweep, and the exit that died after the first record

**Date:** 2026-08-25 · **Ref under test:** `origin/main` @ `c04e15d` · **Branch:** `readings/PS-177-baseline-sweep`
**Instrument:** `take-sweep.sh` (committed beside the records) · **Re-derivation:** `derive.py` · **§2 verifier:** `probe-exit-recovery.py`
**Artifacts:** `reading.firefox.windows.seed5150.json` (61 rows) · `sweep.log` (the full run, refusals included) · `exit-recovery-probe.log` (20 recovery attempts) · `derived-output.txt` (this sweep, re-derived) · `derived-output.ps128-level2.txt` (the Level 2 comparison of §4b)
**Pinned by:** `tests/test_ps177_linkage_class.py` (19 tests — the Level 2 comparator) · `tests/test_ps177_evidence_provenance.py` (13 tests — §2's figures against the logs they cite)

> **Round 2 note.** `sweep.log` and `exit-recovery-probe.log` are the evidence for §2 and were
> **missing from the first submission** — `.gitignore:183` (`*.log`) excluded them silently, so the
> measurements establishing the exit failure as account-level were unverifiable. They are now
> force-added and tracked.
>
> **Round 3 correction.** Round 2 followed that with *"Everything in §2 can be checked against
> them."* **That sentence was false, and committing the logs is what made it checkable.** Three
> figures disagreed with the log they were attributed to (38 min → **24.8**; one-per-minute → **78s**;
> `21:49–22:29` → **22:01:07–22:25:55**), two timeline stamps came from an uncommitted run transcript,
> and three of §2's four measurements have no artifact at all. All of that is corrected below, and the
> blanket claim is **replaced by a per-row source column and a checkable command**:
>
> ```
> ./probe-exit-recovery.py --verify     # offline; no credential needed
> ```
>
> It re-derives every figure §2 quotes from the committed logs and **exits non-zero if the prose and
> the artifacts disagree** — including regression guards on the three withdrawn figures. A claim that
> can drift silently is what produced this defect; this one fails a command instead.

---

## 0. Headline — read this before any number below

The sweep was launched exactly as the ticket specified. **It obtained 1 of the 8 records it planned.**

| | |
|---|---|
| **Planned** | `--engine both --declared-machine windows,macos,linux --seed 5150,24601` → 8 configurations |
| **Obtained** | **1** — `firefox / windows / desktop / seed 5150` |
| **Refused** | **7** — every one with `SOCKS5AuthError: SOCKS5 authentication failed` |
| **Cause** | **The proxy credential stopped authenticating mid-run.** Not the product, not the harness. |
| **Level 2 (mutual unlinkability)** | **STILL UNMEASURED.** It needs two seeds on one arm; one seed was read. |

**The ticket's central deliverable was not achieved.** The two-seed axis is the load-bearing part of
PS-177 and it is exactly the part the exit failure took. §4 states this as *not covered, with the
reason*, per DoD #4 — it is **not** softened by the one record that did land.

**The one record that did land is itself partially degraded** (§3). Its `pixelscan` and `creepjs` rows
are `unobtainable` because the exit died *during* that record, not because Firefox cannot read them.
Treating that as a Firefox limitation would have written a fabricated product finding into PS-16, and
§3 shows the measurement that ruled it out.

---

## 1. What was run, and why those parameters

```
--engine both --declared-machine windows,macos,linux --seed 5150,24601
```

The instrument is committed as `take-sweep.sh`; re-running it regenerates the attempt.

**Seeds 5150 and 24601, and why not 9001.** The ticket requires two seeds that are not `9001` so the
result is independent of the existing corpus rather than half-anchored to it. Counted across
`readings/`, the corpus is `9001` (31 rows), `4242` (19), `1337` (19). **Neither 5150 nor 24601
appears anywhere under `readings/`** — verified by grep before the run. So a similarity found between
them could not be an artefact of a seed some earlier run had already exercised.

**8 records, not 12 — the Firefox collapse is real and the tool printed it itself:**

```
note: firefox: asked for 3 declared machines (windows, macos, linux) but this engine has no OS
parameter and presents windows regardless — collapsed to one run rather than writing near-identical
records claiming different machines
```

That is limitation 1 from the ticket, observed rather than assumed. The record carries
`declared_machine: "windows"` with **`declared_machine_honoured: false`**. The requested machine was
**not** echoed into the record — see §5.

**No axis was cut.** The ticket's fallback ("cut the declared-machine axis before the seed axis") was
not needed: one chromium configuration was timed at ~3.5 min, putting the full 8 at ~30 min. The
plan was affordable and both axes were kept. **What removed the axes was the exit, not a cost
decision** — and an exit failure does not respect the ticket's priority order, which is why the axis
that survived is the one that answers nothing.

---

## 2. The exit — proven, then dead, and the evidence it was not us

**PS-14 says check the instrument before attributing anything to the product, and that an identical
failure across every cell is the shape to distrust.** Seven identical failures across seven cells is
that shape exactly. It was chased down rather than reported.

Timeline (UTC). **Each row names its own source, because the two logs do not carry the same kind of
evidence and round 2 wrongly attributed all of it to both.** `sweep.log` carries **no timestamps at
all** (`grep -cE '[0-9]{2}:[0-9]{2}' sweep.log` → `0`); it establishes *order and outcome*, never
*time*. Only `exit-recovery-probe.log` and the record's own `observed_at` carry clock time.

| time | event | source |
|---|---|---|
| *(untimed)* | exit **proven**: `83.6.111.143` Warsaw/PL, AS5617 Orange Polska, `Europe/Warsaw` | `sweep.log` — the fact, **not** a time |
| **21:48:53** | record 1 written — the only one | `reading.firefox.windows.seed5150.json` → `observed_at: 2026-08-25T21:48:53Z` |
| *(untimed, after it)* | configurations 2–8: `SOCKS5AuthError: SOCKS5 authentication failed` on **both** providers (`ipinfo.io`, `ipwho.is`); **7 REFUSED** | `sweep.log` — order is the file's own |
| **22:01:07 – 22:25:55** | **20 recovery probes over 24.8 minutes, mean cadence 78s (min 78, max 79). Zero successes.** | `exit-recovery-probe.log` |

> **Withdrawn in round 3.** Round 2 gave this table the stamps `21:41` and `21:47:16` and the window
> `21:49–22:29`, and called the recovery run *"38 minutes, one per minute"*. **The first two came from
> the run transcript, which is not committed; the last three are contradicted by the log that is.**
> The corrected figures above are re-derived from `exit-recovery-probe.log` by
> `probe-exit-recovery.py --verify` (below), not typed. The document's round-2 title — *"the exit that
> died 96 seconds into it"* — rested on the `21:47:16`→`21:48:53` pair, and **half that pair has no
> committed source**, so the claim is withdrawn rather than restated: what is evidenced is that the
> exit died **after the first record and before the second**, which is what the argument actually needs.

**Four measurements establish this is the credential and not the product, the harness, or the network.
Their evidence is not uniform, and the split is stated rather than smoothed over:**

| # | measurement | committed evidence? |
|---|---|---|
| 1 | fails from outside the harness | ⚠️ **ad hoc — no artifact** |
| 2 | direct egress works throughout | ⚠️ **ad hoc — no artifact** |
| 3 | not sticky-session expiry (3 variants) | ⚠️ **ad hoc — no artifact** |
| 4 | not a transient blip | ✅ `exit-recovery-probe.log`, 20 rows |

1. ⚠️ **It fails from outside the harness.** A plain `requests` call through the same credential file
   failed identically. Nothing of persona's is in that path. **Taken ad hoc at the terminal; no
   artifact was captured and none is committed.** `probe-exit-recovery.py --variants` re-runs this
   probe, but it was written *after* the fact and did **not** produce this reading.
2. ⚠️ **Direct egress works throughout.** `https://ipwho.is/` answered `159.195.144.196` direct, at the
   same moments the proxy refused. The network is up; the credential is what stopped working.
   **Same status: ad hoc, no artifact.**
3. ⚠️ **It is not sticky-session expiry.** The credential is a Decodo sticky session
   (`gate.decodo.com:10000`, `…-country-pl-city-warsaw-session-…`). Three variants were probed:
   as-stored, a **freshly rotated session token**, and the **session segments stripped entirely**
   (plain rotating exit). **All three failed identically.** A session that had merely expired would
   have been fixed by the second or third. **Same status: ad hoc, no artifact.**
4. ✅ **It is not a transient blip.** **20 consecutive failures across 24.8 minutes at a mean 78s
   cadence**, every one `SOCKS5 authentication failed`, in `exit-recovery-probe.log`. This is the one
   of the four that is fully evidenced, and `sweep.log` independently corroborates all 7 refusals with
   the identical error.

**What the correction does and does not move.** Measurements 1–3 are now marked as what they are —
**unevidenced ad hoc probes** — so they should be read as the author's testimony, not as artifacts.
**The conclusion is unchanged and rests on 4 alone:** 20 consecutive identical auth failures across
24.8 minutes establishes *account-level, not transient* just as firmly as the longer window round 2
wrongly claimed would have — the overstatement was never load-bearing, which is precisely why it
survived two rounds unnoticed. Measurement 4 is the fully-evidenced one. Not blocking with
`retry_in_one_hour` remains correct.

**`checker_cli` did the right thing and it is worth recording.** It refused 7 configurations rather
than falling back to a direct connection, and wrote nothing for them:

```
REFUSED 7 of 8 configuration(s); 1 record(s) written. A partial matrix must not read as a whole one.
```

The alternative — the failure the tool's own docstring exists to prevent — would have been a
complete-looking reading of **the operator's real address** taken against a dozen fingerprinting
services. Direct egress demonstrably worked at that moment, so that outcome really was one silent
fallback away. The refusal cost this ticket its deliverable and was still correct.

---

## 3. The one record — and the confound in it that was ruled out, not assumed

Every number in this section was printed by `derive.py` from the committed record. None was typed
from a terminal scrollback.

```
cell                     : firefox / windows / desktop / seed 5150
engine build             : invisible_playwright/firefox-20
declared_machine_honoured: False
exit                     : 83.6.111.143 Warsaw/PL AS5617 Orange Polska Spolka Akcyjna Europe/Warsaw
masking layer            : route=init_scripts installed=audio,locale,webgl complete=True
counts                   : 25 read, 4 absent, 32 unobtainable (61 rows)
evidence verdict         : sufficient — 7/28 fingerprint rows (25.0%), from 2 checker(s)

ADVERSE VERDICTS THAT ACTUALLY FIRED (read AND adverse): 0
```

### 3a. Zero adverse verdicts fired — and why the raw count says 10

A naive scan of this record finds **10 rows carrying `adverse: true`** and would report ten findings
on the engine that had never been measured. **The true count is zero.** All ten sit in `absent` or
`unobtainable` states: they are the catalogue's **pattern definitions** ("if this matched it would be
bad"), not verdicts. Only a row that is `read` **and** `adverse` is a checker objecting.

`derive.py` enforces the distinction structurally and prints the two groups under different headings,
because this is precisely the error that turns a coverage hole into a fabricated finding — or a
fabricated exoneration. The four states it keeps apart:

| state | meaning |
|---|---|
| `read` + `adverse` | the checker **objected**. A finding. **0 of these.** |
| `read`, not adverse | asked, did not object. Evidence. |
| `absent` | the adverse pattern did not match. The page **was** read. |
| `unobtainable` | **nobody looked.** Never a pass. |

**What was asked and stayed silent** (this is what the reading actually rests on): `bot.sannysoft.com`
(webdriver_present, phantom_js — both absent), `iphey.com` (not_trustworthy absent; trustworthy,
hardware_fine, software_fine all true), `ipleak.net`, `tls.peet.ws`, `tls.browserleaks.com`,
`engine-exit`.

### 3b. ⚠️ The confound: pixelscan and creepjs are `unobtainable` here, and it is NOT a Firefox limit

`pixelscan.net` (12 rows) and `creepjs` (9 rows) are `unobtainable` in this record, every one with
`Error: Page.goto: NS_ERROR_CONNECTION_REFUSED`.

**The tempting conclusion — "Firefox cannot read pixelscan/creepjs through our exit" — is false, and
writing it into PS-16 would have been a fabricated product limitation.** Two facts refute it:

1. **Catalogue order.** The browser tier runs `engine-exit, deviceandbrowserinfo, bot.sannysoft,
   bot-detector.rebrowser, iphey, pixelscan, creepjs`. **pixelscan and creepjs are the last two.**
   The checkers that succeeded are the early ones; the ones that failed are the tail — the run walked
   off the end of a working exit mid-record.
2. **Prior Firefox readings read them both fine.** From the committed corpus
   (`readings/ps128-2026-08-23/`), on Firefox:

   | record | creepjs | pixelscan |
   |---|---|---|
   | `reading.firefox.windows.seed1337.json` | **9 read** | **8 read**, 4 absent |
   | `reading.firefox.windows.seed4242.json` | **9 read** | **7 read**, 5 absent |

   Both checkers are demonstrably readable on this engine. Their absence here is the exit, not the
   engine.

**So the one record this sweep obtained is itself partially degraded**, and its coverage holes are
recorded as exit-caused holes. `pixelscan` on Firefox stays `—` in PS-16, not a score, and not a
limitation.

### 3c. A structural note worth carrying: 15 of the 25 read rows are not about the profile

`ipleak.net`, `tls.peet.ws` and `tls.browserleaks.com` are **JSON-tier** checkers — fetched by curl
through the SOCKS proxy, not by the browser engine. Their `user_agent` rows read literally
`curl/8.14.1`. Those rows describe **the exit**, and they are identical whichever engine is nominally
under test. Only the browser tier (`engine-exit`, `bot.sannysoft`, `iphey` here) describes the
*profile*. This matters when reading `25 read` as though it were 25 facts about Firefox: it is not.

---

## 4. Level 2 (mutual unlinkability) — NOT answered by THIS SWEEP, but answered for ONE arm from the existing corpus

**This section was rewritten in round 2.** As submitted it said Level 2 was unanswered, full stop.
That was true of *this sweep* and remains true — but it was **not** true of the corpus, and the
difference matters enough that the reviewer's blocker on the comparator is what exposed it.

### 4a. This sweep: still UNANSWERABLE, unchanged

```
arm invisible_playwright / windows / desktop — seeds [5150]
  UNANSWERABLE from this sweep: only ONE seed was read on this arm.
  A single profile cannot answer whether TWO profiles are linkable, at any
  level of detail. NOT a pass — recorded as not covered.
```

The ticket's own framing is the reason this cannot be softened: *"Level 2 asks whether two profiles
can be tied to each other — which no single-profile reading can answer, at any level of detail,
ever."* One record is one profile.

### 4b. The corpus already held a two-seed arm, and nobody had diffed it

Fixing the comparator (§4c) meant finding real two-seed data to test it against. There is some, and
it was committed three days before this ticket was written:

| | seed 1337 | seed 4242 |
|---|---|---|
| record | `ps128-2026-08-23/run1-matrix/reading.firefox.windows.seed1337.json` | `…seed4242.json` |
| engine | `invisible_playwright/firefox-20` | identical |
| exit | `95.49.113.111` Warsaw/PL | **identical** |
| masking layer | `init_scripts`, audio+locale+webgl, complete | **identical** |
| observed | `2026-08-23T21:46:11Z` | `2026-08-23T21:42:42Z` |

Same arm, same exit, same masking layer, 3.5 minutes apart, differing only in seed. That is exactly
the comparison Level 2 asks for. Both records are schema v4, as is this sweep's — so they are
directly diffable and no conversion was involved.

**The result (`derived-output.ps128-level2.txt`, committed):**

```
arm invisible_playwright / windows / desktop — seeds [1337, 4242]
  15 fingerprint row(s) read on both sides: 9 entropy-bearing, 6 verdict/low-cardinality
  of the 9 entropy-bearing row(s): 8 DIFFER, 1 IDENTICAL
  ⚠ ROWS THAT TIE THE TWO PROFILES TOGETHER (identical high-entropy values):
      creepjs :: webgl_pixel_hash = 51df3565
  LEVEL 2 FAILS on this arm: a checker reading these rows can tie the two
  profiles to each other.
```

**Eight of nine entropy-bearing rows differ** across the two seeds — `canvas_data_hash`,
`webgl_image_hash`, `gpu_renderer`, `gpu_vendor` on creepjs, and canvas/webgl/renderer/vendor on
pixelscan. The masking is doing its job on those. **One does not:**
`creepjs :: webgl_pixel_hash` reads `51df3565` for both profiles.

**Why this is a finding and not a checker constant.** The same row across every chromium record in
`readings/` takes **three distinct values at three seeds** (`f801a1b3` @1337, `a96eedf0` @2024,
`b8dba17f` @9001). So the value is seed-derived on chromium and appears seed-INVARIANT on firefox —
which is the shape of a masking gap on the firefox leg, not of a value the checker always reports
the same.

**The honest bound on it: n = 2.** Two firefox records at two seeds is enough to *raise* this and not
enough to *prove* it is invariant across all seeds. A third firefox seed settles it, and that is the
single cheapest reading this project can now take (§8). Stated as a finding with its n, per PS-16's
rule that a theoretical figure presented as a measurement is worse than a blank cell.

**Out of scope to fix, per the ticket** (*"a defect it reveals is a new ticket; this one measures"*).
Not filed as a product issue from here — n=2 warrants confirmation first.

### 4c. The comparator itself — the reviewer's blocker, and what was wrong

The reviewer executed the comparison branch by hand and found it **had never run**: with one record
it always took the `len(seeds) < 2` early-`continue`. Executed, it reported two maximally-different
synthetic profiles as **linked**, because the only rows read on both sides in this sweep's record are
four boolean detector verdicts (`webdriver_advanced_passed`, `webdriver_missing_passed`,
`software_fine`, `trustworthy`) — all `True`.

**That inverts the question the ticket exists to answer.** Identical *high-entropy* values (a canvas
hash, a GPU renderer string) mean the two profiles are **linkable**. Identical *verdict* values mean
both profiles are **clean**. Reporting the second as the first would have written a fabricated
linkage finding into PS-16.

The fix classifies every row as `entropy` or `verdict` before any comparison
(`linkage_class()`), and:

* a **verdict** row can never tie two profiles at any value — it is excluded from the finding and
  printed separately, labelled as non-evidence, so nothing is hidden;
* an arm whose overlap is verdict-only reports **UNANSWERABLE (coverage, not a clean result)** — the
  branch that was already correct for a zero-overlap arm;
* the record's own `vector` tag outranks the value-shape heuristic, so a catalogue-tagged leak vector
  is entropy-bearing whatever its value looks like.

This is the same failure class recorded on PS-67: **an override must never be able to produce the
tool's loudest finding.** Here the least informative rows in the record were producing the strongest
claim.

**Pinned by `tests/test_ps177_linkage_class.py` (19 tests), against real records, both branches:**
the ps128 pair above is the answerable fixture, and this sweep's record reseeded is the
verdict-only fixture. Two tests exist purely to stop the dangerous direction — a verdict-only overlap
must never print `LEVEL 2 HOLDS`, and mutating every verdict row must not move the answerable arm's
counts.

> **A second bug found in the same comparator, and the correction to how it was reported.** It first
> keyed the diff on the row's `vector` field. Only **9 of 61 rows** carry a `vector`, while the
> record's own fingerprint axis is `sort == "fingerprint"` (**28 rows**). Keyed on `vector`, an
> unlinkability claim would have rested on a subset while appearing to consider all of it. Fixed to
> key on `sort`. **As submitted, this note claimed the fix moved the comparison from a third of the
> evidence to all 28 rows. It does not** — `readable()` also filters `state == "read"`, and only
> **4 of those 28** are read in this record (21 unobtainable, 3 absent). The fix moved the
> comparison from 9 rows to 4. The reviewer caught the overstatement; it is corrected here rather
> than quietly dropped.

---

## 5. Limitations recorded as required — not worked around

**1. Firefox cannot be told which OS to present (product issue #211).** Observed, not assumed: the
tool printed the collapse (§1) and the record carries `declared_machine_honoured: false`. **The
requested machine was not echoed into the record.** The `macos` and `linux` requests on the Firefox
leg produced *one* windows run, and the grid is reported as the 8 configurations that exist rather
than a square 12. Squaring it would have written a machine the engine never declared, and a later
comparison would have read the fabricated difference as a product coupling.

**2. Mobile is not selectable from this tier.** `--declared-machine` exists; there is no `device_type`
selector. Every record this tier can produce is a **desktop** record. `windows`+`mobile` — the arm
PS-161 round 4 actually repaired — **cannot be read here at all**, and nothing in this directory is
evidence about any mobile arm. `derive.py` hard-codes `desktop` in its cell label with that reasoning
attached, so a future reader cannot mistake the constant for a measurement.

**3. Eight checkers are unobtainable and expected to be.** Present in the record as `unobtainable`
with reasons, and **not** findings: `browserscan.net` (signed-params API), `amiunique.org` and
`coveryourtracks.eff.org` (click-gated), `whoer.net` (Cloudflare — out of scope by charter),
`fv.pro` (paywalled), `bot-detector.rebrowser.net` and `deviceandbrowserinfo.com`
(`NS_ERROR_CONNECTION_REFUSED` through our exit), `tools.scrapfly.io`
(`SOCKS5Error 0x02: connection not allowed by ruleset`).

**4. NEW — 7 of 8 configurations were never read, because the exit died.** Not covered, reason in §2.
Specifically never measured by this sweep:

| arm | status |
|---|---|
| `firefox / windows / seed 24601` | **not covered** — exit unavailable |
| `chromium / windows / seed 5150` and `seed 24601` | **not covered** — exit unavailable |
| `chromium / macos / seed 5150` and `seed 24601` | **not covered** — exit unavailable |
| `chromium / linux / seed 5150` and `seed 24601` | **not covered** — exit unavailable |

**No weaker reading is allowed to stand in for any of these.** In particular the existing
chromium/windows/seed9001 corpus (`ps143`, `ps150`, `ps161-live`, `ps170`) does **not** cover
`chromium / windows / seed 5150` — a different seed is a different profile, which is the entire
premise of the seed axis.

---

## 6. The instrument, disclosed in full (PS-14)

**Chromium — already installed, unchanged by me.**

```
~/.persona/engine/fpchrome.AppImage
version.txt : 148.0.7778.215
sha256      : a5fa5e6c05cb7fa3617ec2ca642ad3cc6e586ac5249cc29edb0a602d695685f0
```

**Firefox — was NOT installed in this container. I provisioned it.** Recording this because an
inherited "the engine is not available here" is evidence about *a container*, never about the ticket,
and the Firefox leg would otherwise have been written off as not-coverable when it was one command
away. Provisioned from the repo's own pin, not from a floating version:

```
pip install "invisible_playwright @ git+…@353df4faac4fb202cc4d836c46d981855ecf1bd9"   # pyproject.toml:102
  -> invisible_playwright 0.7.1, invisible_core 20.14.0
python -m invisible_playwright fetch
  -> ~/.cache/invisible-playwright/firefox-20_151.0_20260817150018/firefox
```

The fetched engine is `firefox-20`, which **matches the repo's `engine-baseline.txt`** (`firefox-20`).
The record's `engine` field reads `invisible_playwright/firefox-20`.

**⚠️ Side effect, disclosed: that install downgraded `playwright` 1.62.0 → 1.61.0**, because
`invisible_playwright` pins it. This is **not** confined to the Firefox leg — `chromium_tier` attaches
to persona's chromium over CDP via `playwright.async_api`, so the downgrade sits under both engines.
Evidence it was benign for the chromium leg: a post-downgrade chromium run read **both** tiers
normally (json 15 read, browser 24 read) before failing at an unrelated step (§7). No chromium record
survives this sweep to compare against `ps170`, so this is disclosed as an **open caveat on any future
comparison**, not as a cleared one. Anyone diffing a later chromium record against `ps170` should know
the playwright version moved underneath it.

**Waivers.** `--allow-unsandboxed-chromium` is taken and disclosed — persona's own launch path passes
`--no-sandbox` nowhere, so a reading taken with it is not the product's default surface. It is
required because this host forbids the unprivileged user namespace the sandbox needs, and it applied
equally to the `ps170` baseline. `--allow-small-dev-shm` is **not** taken: `/dev/shm` is 1.0 GiB,
above the tier's 256 MiB floor.

---

## 7. Reproducibility, and one harness gotcha that cost a full browser run

Re-run `./take-sweep.sh <output-directory>`. **Expected to differ:** the exit address (rotates by
design; a different Polish exit is exit-driven variance per PS-10) and every seed-derived fingerprint
row. **Expected to hold:** the Firefox collapse note, `declared_machine_honoured: false`, the eight
unobtainable checkers, and the plan of 8 configurations.

**Gotcha, recorded so the next person does not pay for it.** `-o` on a **single**-configuration run is
a **file path**; only a **multi**-configuration run treats it as a directory (`os.makedirs`). Passing a
directory to a single-config run crashes at `matrix.write` with `NotADirectoryError` — **after the
entire browser run has been spent**. That is how the timing run in §6 died. `take-sweep.sh` is
multi-config so it takes a directory correctly, and the distinction is commented there.

---

## 8. What the next run should do first

The exit is the blocker; nothing below is actionable until a working credential exists.

> **⚠️ Round 2 correction — the recommendation this section led with was wrong.**
> As submitted, item 1 was *"`firefox / windows / seed 24601`, one record, pairs with the record
> committed here and answers Level 2 for that arm."* **It would not have answered Level 2.** Pairing
> a second seed with *this sweep's* record can only diff the rows read in **both** — and the only
> fingerprint rows this record carries are four boolean detector verdicts. The entropy-bearing
> checkers on this arm (pixelscan, creepjs) are precisely the ones the exit killed, so the pair would
> have yielded no entropy row on both sides and the correct answer would have been UNANSWERABLE.
> Under the *old* comparator it would have printed a false linkage warning instead. Corrected below.

1. **A third Firefox seed on `windows`, through a healthy exit — e.g. `seed 24601`.** Not to pair
   with this sweep's record, but to pair with the **ps128 pair** (§4b), which already reads
   pixelscan and creepjs. This is the reading that settles the open question this ticket produced:
   whether `creepjs :: webgl_pixel_hash` is genuinely seed-invariant on Firefox (n=2 today) or
   whether 1337/4242 collided by chance. One ~3 min run, and it either confirms a real masking gap
   or retires it.
   **Verify the record actually carries the row before drawing any conclusion** — if the exit kills
   creepjs again, the run has answered nothing and must be re-taken, not written up.
2. **`chromium / windows / {5150, 24601}`.** Answers Level 2 on the arm the whole existing corpus
   sits in, and is directly comparable to `ps143`/`ps150`/`ps161-live`/`ps170`. Chromium already
   shows `webgl_pixel_hash` varying across three seeds, so this measures the arm that looks
   *healthy* on that vector — a useful control for item 1.
3. **`chromium / macos / {5150, 24601}`.** PS-16's worst counted cell (50% theoretical GPU collision)
   and never read by any checker.
4. **`chromium / linux / {5150, 24601}`.**
5. **Re-read pixelscan and creepjs on Firefox** through a healthy exit, to fill the holes §3b shows
   are exit-caused rather than engine-caused.
