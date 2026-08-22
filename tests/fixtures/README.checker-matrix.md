# Checker-matrix readings

`checker-matrix-reading.sandbox.json` is the **first reading of the Level 3
checker matrix ever taken by anything automatic** on this project (PS-59).
Produced by:

    python3 -m src.services.verify.checker_cli read -o <path>

Read it with `git log -p` on this file: that is the whole point of committing
it here rather than to a separate store — "when did this change, and what
shipped alongside it" is a question only the repository can answer.

## What the record is, and is not

It is a **dated observation of third parties**, taken through one exit, on one
machine. It is deliberately **not** byte-stable across runs (unlike
`engine-fingerprint-baseline.firefox.json`): it carries the observed exit and a
timestamp, because a reading is uninterpretable without them. Two runs are
*supposed* to differ on exit-driven rows.

Every catalogued item appears in every record, in one of three states:

| state | meaning |
|---|---|
| `read` | the checker answered and the item was extracted |
| `absent` | the checker answered and did NOT say this. For an adverse item (`proxy_detected`) this is the **good** news |
| `unobtainable` | the checker was not read at all — **nothing** may be inferred, including "no news is good news" |

`absent` and `unobtainable` are distinct on purpose. Folding them together
would make either a clean page look unread, or an unread page look clean.

## Reading it against the three sorts

Every row carries `sort`, and comparing two records without it is meaningless
because the exit rotates by design:

- **`exit`** — expected to move between runs. Not news.
- **`host`** — constant on this machine, different on another.
- **`fingerprint`** — must **not** move when only the address moved. One that
  does is a coupling and deserves its own ticket. That is the entire return on
  accepting a rotating exit.
- **`harness`** — describes **the instrument, not the product**. The JSON tier
  is fetched by this repo's own Python client, so the TLS/JA4 shape those
  checkers report is Python's (the first run recorded `user_agent:
  curl/8.14.1`). Tagged `fingerprint`, a future Python or OpenSSL upgrade would
  read as *persona's fingerprint moving* — a false alarm of exactly the kind
  that makes a real one unbelievable. Reading persona's **real** TLS
  fingerprint means driving these endpoints from the engine — **now built, see
  below.**

## The engine-driven TLS tier (PS-62)

The gap the `harness` sort above was invented to *name* is now **closed as a
capability**. The same three endpoints are also catalogued under `…@engine`
ids (`tls.peet.ws@engine`, `tls.browserleaks.com@engine`,
`tools.scrapfly.io@engine`) and asked by **persona's engine**, over the same
launch and the same proven exit as the prose checkers.

**The harness rows stayed.** They are not junk: they pin what this repo's own
fetcher looks like on the wire, which is exactly what PS-46's egress work needs
when it asks whether persona's unattended requests are distinguishable. The
engine rows sit *beside* them — distinct checker ids, because the record is
keyed by `(checker, item)` and two readings of one endpoint would otherwise
collide.

### A row must prove its own origin

This is the part worth reading before trusting any `fingerprint`-sorted TLS row.

**The tag is earned from the checker's answer, never from the transport.** The
catalogue declares these items `fingerprint` because that is what they are
*worth*; `engine_tls.retag` refuses to record them that way until the response
shows a browser engine asked. The witness is the `user_agent` the checker echoes
back — the same field that exposed the original mistake — and it is catalogued
**first** on every one of these checkers so a row set meets the evidence before
the claims.

| the witness says | the row is recorded as |
|---|---|
| a browser engine | `fingerprint` — it describes persona |
| a scripting client | `harness` — it describes the instrument, value kept |
| unrecognised or absent | `unobtainable` — value dropped, nothing may be read |

"The engine made this request, therefore these rows are the engine's" is
precisely the reasoning that produced the original defect — the JSON tier was
*known* to be fetched by `socks_fetch` and its rows were tagged `fingerprint`
anyway. So the transport does not vouch for itself. A mis-wired client, a proxy
answering from elsewhere, or a refactor that swaps the engine underneath all
land as `harness` or `unobtainable` rather than as a false claim about persona.

**`exit`-sorted rows are never demoted.** An observed address is a property of
the exit and is the same fact whichever client asked; demoting it would discard
a true reading to punish an unrelated uncertainty.

**This is not a security control.** A hostile client can send any User-Agent it
likes. The failure being guarded against is *our own instrument being mistaken
for our own product* — two clients that are both ours, neither lying.

### What has NOT been measured, and must not be read as clean

**No live engine-TLS reading exists yet.** The capability is built and proven
against recorded payloads; the *reading* is outstanding, for the reason the
standing rules require be recorded rather than worked around:

> The exit was **alive** at the start of the PS-62 session (Warsaw / PL /
> `AS12912 T-Mobile Polska`, rotated onto an `ftth.dynamic` host) and **died
> mid-session**. The engine's own geo preflight was refused (`0x05`), and
> `curl -x socks5h://…` then returned `(97) cannot complete SOCKS5 connection`
> on *every* host, while TCP to the SOCKS endpoint still connected and direct
> egress still answered 200 — i.e. the exit refusing sessions, not a sandbox
> fault. Rotation is the operator's, from the host: **report and stop**, no
> retry loop, and no fallback to a direct connection. A TLS reading taken over
> the sandbox's own address would be both wrong and an Invariant #0 exposure,
> and it would look like data.

So every `…@engine` row in a record taken before that exit is restored is
`unobtainable` **with that reason**. Do not read them as clean, and do not
treat the first later run that actually reads them as a regression — the same
caution PS-59 recorded for its 24 unobtainable browser rows.

### Firefox only — stated, not implied

persona ships a patched **Firefox** *and* a **fingerprint-chromium**, and they
will not present the same handshake. The engine tier is driven by
`invisible_playwright` (Firefox, `firefox-20` as installed here);
fingerprint-chromium is **not installed in the agent sandbox** and was **not
read**. The record's `engine` header names the build for this reason: a matrix
that read one engine and labelled the rows "persona" would repeat PS-62's own
defect one level up — a true reading published under a name that claims both.

Chromium additionally cannot authenticate to a SOCKS5 proxy on
`--proxy-server`, so reading it needs the local relay
(`src/services/proxy/bridge.py`, hardened by PS-25) that the Firefox path does
not — it does SOCKS5-with-auth natively. That is the shape of the follow-up,
not a defect in this one.

### Out of scope here, on purpose

If the engine's JA4 turns out to be distinctive or a poor match for what it
claims to be, **that is a real finding and it belongs to the masking direction
as its own ticket**, with the evidence attached. This tier reads and records;
it does not tune the handshake.

The comparison keys are **not spelled alike on every endpoint**, and assuming
they are is what this ticket's first attempt got wrong:

- **`ja4` is the key on every checker** — it is normalised (upstream sorts the
  extension list) so it does not move with permutation.
- **`tls.browserleaks.com` also publishes a genuinely distinct `ja3n_hash`**,
  and that one is read as a second key.
- **`tls.peet.ws` publishes no normalised JA3 at all.** Its `ja3` is the RAW
  string and its `ja3_hash` the MD5 of it (`pagpeter/TrackMe`
  `pkg/types/structs.go:16-23`; the extension list is joined in wire order at
  `pkg/tls/fingerprint_tls.go:88-99` and never sorted — contrast the
  `sort.Strings` it *does* apply to peetprint at :171 and to ja4 at
  `pkg/tls/ja4.go:73`). So peet's second key is **`peetprint_hash`**, and every
  `ja3` spelling on that checker is skipped. `tools.scrapfly.io` mirrors peet's
  schema and is treated the same way.

Raw JA3 is read nowhere, under **any** of its spellings, and a test pins the
rule rather than a list of spellings (`reads_raw_ja3` in
`tests/test_verify_checkers.py`, imported by the engine tier's guard so the two
cannot drift): it varies with TLS extension permutation, so it moves without
anything meaningful changing and would manufacture drift in the one record
built to detect drift.

## The header, and the two keys a comparison cannot work without

Besides the observed `exit`, the header carries:

- **`seed`** — the engine fingerprint seed. The engine's fingerprint is
  *seed-derived*, so without this a comparison cannot tell **a real coupling**
  from **a different seed**, which is the whole analysis this record exists to
  enable. Measured: the renderer moved `NVIDIA GTX 980` → `Intel HD Graphics
  400` between two runs here purely because the seed differed. `0` means the
  engine's own default was used.
- **`skipped_tiers`** — any tier the operator asked not to read. A skipped
  tier's rows are still **present** as `unobtainable` with `tier skipped` as
  the reason, so the matrix never silently narrows; this key is the
  header-level statement of the same fact, so a later diff can tell *"the tier
  was skipped"* from *"those checkers were dropped"* from *"that schema had no
  such tier"*.

## Each tier proves its own exit

`exit_guard` proves the exit for the **Python fetcher** — it opens a
`socks_fetch` socket and reads ipinfo through it. The browser tier is a
**different process on a different socket**, so that proof does not transfer to
it. The `engine-exit` checker is the browser tier's own proof: before any
checker page is loaded, the **engine** observes its own egress and the country
is checked. An engine whose proxy silently failed would otherwise render every
page, parse every verdict and land every row as `read` — a complete-looking
reading of the operator's real address taken against every checker in the
matrix.

It is recorded as rows (`sort: exit`) rather than merely asserted, so *"which
address did the browser tier actually use?"* is answerable from the file
instead of by hand. Firefox's `network.proxy.failover_direct` is pinned off for
the same reason: with it on, a dead SOCKS proxy is answered by retrying
**directly**.

## Provenance of the committed record, including what is wrong with it

Recorded honestly rather than re-run until it looked good:

1. **A first run was taken and then discarded as a baseline**, because reading
   it found three defects *in the reader*: a `phantom\s*js` pattern that
   matched the page's own probe label `phantomJS` and so recorded a clean
   browser as a **PhantomJS detection**; a GPU capture that stopped at the `)`
   inside `Intel(R)` and recorded a truncated renderer; and the harness/
   fingerprint mislabelling above. All three are now pinned by tests.

2. **A second run found two more defects in the reader**, and neither fixture
   could have caught them, because both fixtures were captured on the one exit
   and the one page state that hides them:

   - `geo_poland` matched `poland\s*/\s*warsaw` — a **city hardcoded into an
     exit-sorted item**. The exit rotates within Poland *by design*, so when it
     moved to Ursynów/Kraków a perfectly clean Polish page read `absent`,
     which looks exactly like the checker having stopped reporting Poland. It
     now matches the country and captures the whole `Poland / <city>` string.
   - CreepJS's three rating items are `capture` items — matching means *the
     rating was published*, not *the rating is bad*. Tagged adverse, the clean
     measured page (`0% headless`, `0% stealth`, `6% like headless` — the best
     readings CreepJS gives) recorded **three adverse matches**. The polarity
     of a captured number lives in the number.

   Both are pinned, including a test that demonstrates the naive city pattern
   still missing the rotated exit.

3. **The committed run used the twice-corrected reader**, and its exit
   (`84.40.220.51`, AS12887 Netia SA, Ursynów) was proven **on both legs** —
   the Python fetcher's and the engine's own, which agreed on the address.
   The mobile exit then **degraded during the run**: `pixelscan` and `creepjs`
   failed with `NS_ERROR_UNKNOWN_HOST` — DNS *at the exit*, which is
   `socks_remote_dns` working as intended rather than a local resolver leak.
   So this record carries **24 unobtainable rows** and its browser tier is
   thinner than the matrix can actually produce.

   A further attempt was made and **refused before recording anything**
   (`Host unreachable`, exit code 2, nothing written) — the guard doing exactly
   its job, live. No third attempt was made.

   That is left exactly as it is. Rotation and outage are the operator's, from
   the host: the standing rule is *report and stop*, never retry around a dead
   exit, and never fall back to a direct connection. A run that recorded fewer
   verdicts is a legitimate outcome; a run that quietly recorded them from the
   wrong address would not be.

   **Consequence for whoever compares next:** many browser-tier rows here are
   `unobtainable`, not `absent`. Do not read them as clean, and do not treat
   the first later run that actually reads them as a regression.

   **One `absent` here also needs care.** `iphey.com` rendered — so its rows
   are `absent` rather than `unobtainable` — but **both** polarity items
   (`trustworthy` *and* `not_trustworthy`) are absent, which is a pair that
   cannot both be true of a settled page. Read together they mean *the verdict
   block never rendered*, not *the checker declined to call us trustworthy*.
   A single-row read of that checker would get the opposite impression, which
   is precisely why both polarities are catalogued as separate rows.

`checker-pages/*.txt` are the rendered page texts captured through the exit on
2026-08-21, kept so the suite can prove each pattern reads a **real** page the
way the catalogue claims — which is how three of the four original pattern
defects were caught.
