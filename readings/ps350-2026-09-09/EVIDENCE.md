# PS-350 — the Firefox `stealth` cell, established BY MEASUREMENT against a STOCK Firefox

**Date:** 2026-09-09  **Tree:** `0ce4354`  **Verdict:** the cell moves
`position_not_established` → **`not_applicable`**. **No spoof ships.**

## The question, and why only a control could answer it

`stealth_ext.py` re-adds two Chrome-only Navigator APIs that CreepJS counts
toward "like headless" — `navigator.connection.downlinkMax` and
`ServiceWorkerRegistration.prototype.index` — and Chromium installs it
unconditionally. Firefox reaches none of it: `spawn_browser` returns on the
Firefox arm before the extension list is assembled, and `downlinkMax` /
`ContentIndex` appear **zero times** across `invisible_launch.py`.

`tests/test_engine_masking_matrix.py` recorded the cell as
`position_not_established`, and named the gap in its own words:

> A plausible position is *"not applicable — Firefox exposes neither API, so a
> real Firefox is missing them too"* — but plausible is not recorded, and this
> file will not mint a position the tree does not hold.

⭐ **That sentence is a claim about TWO browsers, and the tree held only one.**
The 20 committed Firefox artifacts under `readings/` answer the first half
unanimously (persona's Firefox exposes neither API, both realms, four engine
builds) and **every one of them is persona's own engine**. They cannot answer
the second half at any sample size, and more of them would not help. Only a
**stock Firefox** can, and nothing in this project had ever read one.

## Environment — the shelf life

| | |
|---|---|
| host | Linux 6.8.0-138-generic, x86_64, Debian-family container, 8 cores / 15 GB |
| display | Xvfb (arms A1/A2/R/M are headless; arm B is persona's **headful** launch) |
| **stock control** | upstream **Firefox 151.0**, `ftp.mozilla.org`, tarball `sha256 8ff8557a…`, binary `sha256 29a6161c…` |
| **subject engine** | persona `firefox-29` (`firefox-29_151.0_20260905222621`), binary `sha256 826f2488…` |
| `invisible_playwright` | 0.14.0 (`BINARY_VERSION` `firefox-29`, `FIREFOX_UPSTREAM_VERSION` `151.0`) |

⭐ **The version confound is REMOVED, not merely disclosed.** Playwright's own
`firefox` channel installs **153.0**; it was deliberately discarded. Upstream
**151.0** was pulled instead, because `invisible_playwright.FIREFOX_UPSTREAM_VERSION`
is `151.0` — so **both arms report `Mozilla Firefox 151.0`** and an A1-vs-A2
divergence cannot be a version difference. `test_the_reading_names_its_shelf_life`
asserts that equality, so re-taking this reading on mismatched versions goes red.

⚠️ **The HOST is named as well as the build**, per this ticket's Correction 1.
PS-314 recorded a row of *this very probe* (the WebSerial pair) moving between
two artifacts whose `engine_build` was **identical** — WebSerial is gated on the
host platform. A record naming only the build would attribute a host-gated row
to the engine, which is the same class of error that correction exists to fix.

## Method

`scripts/ps350_stealth_control.py`. Probe expressions are **imported verbatim**
from `src/services/verify/probes.py` by `Probe.id` and run through the product's
own `runner.window_expression` / `runner.worker_expression` — so this measures
what persona records, in the two realms persona records it in, and cannot drift
from the inventory. A retyped expression measures the typist.

**No product code was modified to take this reading.** Control injections are
probe-side and confined to the two control arms, as PS-312 did.

### Five arms, one host, one run

| arm | what | what it can attribute |
|---|---|---|
| **A1** | STOCK FF 151.0, bare binary, marionette, headless | the **CONTROL** — what an ordinary Firefox exposes. Attributable to Mozilla, **never** to persona. |
| **A2** | persona `firefox-29`, bare binary, marionette, headless | the matched **SUBJECT**. Same channel, gesture, page, host, minute — the only difference from A1 is **which binary is executed**. |
| **B** | `spawn_browser(in_process=True)`, headful under Xvfb | the **PRODUCT** — launcher, prefs, per-tab masking layer and all. |
| **R** | stock + `stealth_ext`'s own two shims injected | **reveal control** — are the probes live? |
| **M** | stock + a manufactured `navigator.connection` | the **second** reveal, because R only half fires (see below). |

Both bare arms run over **marionette** rather than juggler. PS-171's arm C
states why: stock Firefox has no juggler channel, so driving the two binaries
over different channels would confound **binary** with **channel**, and an
agreement from such a pair could be attributed to neither.

## The reading

**12/12 rows identical across A1, A2 and B — in BOTH realms.**

| row | realm | A1 STOCK | A2 persona engine | B persona product path |
|---|---|---|---|---|
| `stealth.connection` | window / worker | `null` | `null` | `null` |
| `stealth.contentIndex` | window / worker | `{"hasIndex": false}` | `{"hasIndex": false}` | `{"hasIndex": false}` |
| `apiPresence[navigator.connection]` | window / worker | `undefined` | `undefined` | `undefined` |
| `apiPresence[NetworkInformation]` | window / worker | `undefined` | `undefined` | `undefined` |
| `apiPresence[ServiceWorkerRegistration]` | window / worker | `function` | `function` | `function` |

These are **byte-identical to the 20 committed persona artifacts** on the same
rows. Full 41-key `apiPresence` diff, A1 vs A2: **two keys** — see the honesty
clause below.

### The controls that fired, per arm

`channel_arith == 2` · real loopback `origin` (never opaque) · `isSecureContext
== true` · UA is Gecko · **`layout_width` > 0** (the document *rendered*, not
merely parsed) · `Worker` available. The harness **refuses to emit** an arm
whose gates did not pass, because a browser that never started reports every row
as absent — which is byte-identical to a perfect match.

## ⭐⭐ The finding the ticket did not anticipate

The reveal control installed `stealth_ext`'s **own** two shims. It moved
`contentIndex` to `{"hasIndex": true}` — and left `stealth.connection` at
`null`.

Read carelessly that says *"the connection probe is inert"*, which would make
every `null` above worthless. **It is the opposite,** and it is the sharpest
thing this measurement found:

> `stealth_ext`'s downlinkMax shim is guarded `if (conn && !('downlinkMax' in
> conn))`. **Firefox exposes no `navigator.connection` for it to hang off, so
> the shim is a STRUCTURAL NO-OP on this engine** — it cannot fire, whatever it
> is asked to do.

Proved rather than argued, by a second mutation in the same direction: arm **M**
manufactures the whole object the shim expects, and the same probe then returns

```json
{"present": true, "hasDownlinkMax": true, "downlinkMax": "Infinity", "type": "ethernet"}
```

So the connection row **is** live, and the `null` is the engine's answer. Two
opposite mutations, which is what this project's own testing article asks of a
reveal control.

**This is what makes the cell `NOT_APPLICABLE` rather than merely "not
needed".** Installing `build_stealth_extension` on Firefox would run one shim
that *cannot fire* and one that *manufactures a Chrome-only API on a Gecko
browser* — an **impossible pair** and a detectable mechanism at once. That is
the trade PS-312 declined on `geo`, for the same reason.

## ⛔ Honesty clause — the one divergence, and what it is NOT

The full 41-key diff is **not** empty. Stock and both persona arms differ on
exactly one pair:

```
Serial:            function (stock)  vs  undefined (persona), window AND worker
navigator.serial:  object   (stock)  vs  undefined (persona), window AND worker
```

It is recorded here rather than omitted, because `ENV_SENSITIVE_PROBES` in
`src/services/verify/baseline.py` states that it excuses nothing — *"a future
movement in `stealth.apiPresence` still reds `baseline.check` exactly as it
would have before, and still has to be explained"* — and its scope clause says
the PS-314 entry covers this pair **under a platform-gate difference** and *"says
nothing about any other key of this probe"*.

**What I measured, and the limit of what it licenses.** On *this* host the pair
is **engine-side**, reproducibly: stock reads `function`, persona's engine reads
`undefined`, across five runs, and a Win32 platform override does **not** move
it. That is a different axis from PS-314's finding (a host gate explaining
movement *within* the corpus, where `engine_build` was identical). Both can be
true of different axes, and **this reading claims no more than its own axis.**

⛔ **It is explicitly NOT a second deliverable** — the ticket puts the `Serial`
pair out of scope, and `test_the_one_divergence_is_named_and_is_not_this_cells_rows`
pins both halves: the divergence is *exactly* that pair (a third key appearing
goes red as a new finding), and it touches **none** of the rows this cell is
about.

## ⚠️ Two traps hit during this work, recorded so a re-run does not repeat them

**1. The opaque-origin trap.** On `data:` and `about:blank` the origin is opaque
and service workers are unavailable, so `ServiceWorkerRegistration` reads
`undefined` on a browser that exposes it perfectly well — a clean, reproducible,
**false** divergence on a CreepJS-counted row. Every arm here reads a real
`http://127.0.0.1` document and asserts `isSecureContext`.

**2. The identity trap — this ticket's own, and it cost a false result.** A
first run produced an arm reporting `Serial: function` that disagreed with
**three fresh isolation runs of the same binary**. Nothing in the artifact could
adjudicate which binary had answered, and **`navigator.buildID` cannot**: the
engine *pins* it, and both binaries report `20181001000000` — the one identifier
a page could offer is exactly the one this engine spoofs. The harness now
records the answering process's install directory off `/proc` and **discards**
any arm that did not run what its label names.

⚠️ Its first version compared **realpaths** and refused *every* stock arm,
because upstream's `firefox` execs `firefox-bin` from the same install — a true
fact about Mozilla's launcher and a false alarm about identity. The grain is now
the install **directory**, which is the axis that actually separates the arms.

## Reproducibility

Five full runs of the harness. The three taken after the identity gate was
corrected are **byte-identical** in both `rows` and `apiPresence_full_diff`.

## Falsification battery — run, not argued

**Eight RED**, and the two GREEN rows exist only because someone ran them:

```
_install_spoof("stealth", ...) registered            -> RED   (registry oracle)
a shipped builder emits "downlinkMax"                -> RED   (emitted source)
the reading re-measured to a different answer        -> RED   (anti-rot)
readings/ps350-2026-09-09/reading.json deleted       -> RED   (cell rests on nothing)
tests/test_ps350_ff_stealth_live.py deleted          -> RED   (constraint unasserted)
the manufacture control stops moving its probe       -> RED   (inert instrument)
a THIRD apiPresence key diverges                     -> RED   (new finding, unexcused)
the reading marked opaque-origin / insecure          -> RED   (void venue)

a COMMENT at the launch site naming both tokens      -> GREEN (no false +)
a DOCSTRING in the builder naming both tokens        -> GREEN (no false +)
```

The two green rows are why this guard uses **two** oracles and not the device
cell's three: `_launch_site_code` keeps string literals, and this cell's recorded
reason must discuss `downlinkMax` and `ContentIndex` **by name** to say why they
are inapplicable. The emitted-source and registry oracles cannot be tripped by
prose about the vector; that third one could be.

## Honest bounds

1. ⛔ **The stock arm is a CONTROL and is not the product.** Nothing it reports
   may be attributed to persona's behaviour **in either direction** — the
   `ps159` rule, which that record states in its own words.
2. **One host, Linux x86_64, headless (arm B headful), one run repeated five
   times.** The `Serial` pair proves this probe *has* host-gated rows, which is
   precisely why the recorded position names the host platform.
3. ⛔ **NOT an Invariant #0 claim.** No host fact escapes and no leak was found;
   this is cross-engine equivalence / masking invisibility. Priority stays
   `medium`.
4. **Arm B is one profile.** The two rows are not seed-derived on either engine,
   so seed variance is not the axis here — but this reading does not establish
   cross-profile behaviour and does not claim to.
5. **Frequency is unmeasured.** No operator report and no checker reading cites
   these rows on Firefox. PS-16 records that every Firefox checker reading fired
   zero adverse verdicts — consistent with this answer, and not evidence for it.
6. **CI cannot re-take this reading.** No CI job downloads an upstream Firefox,
   so the live suite **skips** there. Its offline half re-reads this artifact on
   every run with no browser at all, which is what keeps the cell honest between
   measurements.

## How to re-take this reading

```bash
pip3 install --user invisible-playwright          # then:
python3 -m invisible_playwright fetch             # ⚠️ the verb is `fetch`, not `install`
apt-get install -y xvfb                           # arm B is headful; A1/A2/R/M are not

curl -L -o ff.tar.xz \
  https://ftp.mozilla.org/pub/firefox/releases/151.0/linux-x86_64/en-US/firefox-151.0.tar.xz
tar -xf ff.tar.xz -C /tmp/stock                   # match invisible_playwright.FIREFOX_UPSTREAM_VERSION

python3 scripts/ps350_stealth_control.py \
  --stock /tmp/stock/firefox/firefox \
  --out readings/psNNN-YYYY-MM-DD
```

Then run the guards:

```bash
pytest tests/test_engine_masking_matrix.py -q                       # offline, no browser
PS350_STOCK_FIREFOX=/tmp/stock/firefox/firefox \
  pytest tests/test_ps350_ff_stealth_live.py -q                     # live, both halves
```

⚠️ **Pull the stock version that matches `FIREFOX_UPSTREAM_VERSION`**, not
whatever `playwright install firefox` provides — that channel ships a different
version and reintroduces the confound this reading removed.

## Suite state at this commit

`pytest tests/test_engine_masking_matrix.py -q` → **44 passed** (41 at
`0ce4354`, plus this ticket's three). Full-suite regression check: the branch and
the merge-base were run in parallel worktrees and their failure sets compared —
**226 at base, 224 on this branch, and the new-on-branch set is EMPTY**. The two
that stopped failing are `test_ps330_ff_mediadevices_live`'s permission and
label legs, which fail at base for want of a provisioned engine and pass here
because this work provisioned one; nothing in this branch touches that file.
