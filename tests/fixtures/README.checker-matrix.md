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
- **`host`** — constant on this machine, different on another. **The GPU rows
  are no longer in this sort** — see below.
- **`fingerprint`** — must **not** move when only the address moved. One that
  does is a coupling and deserves its own ticket. That is the entire return on
  accepting a rotating exit.
- **`harness`** — describes **the instrument, not the product**. The JSON tier
  is fetched by this repo's own Python client, so the TLS/JA4 shape those
  checkers report is Python's (the first run recorded `user_agent:
  curl/8.14.1`). Tagged `fingerprint`, a future Python or OpenSSL upgrade would
  read as *persona's fingerprint moving* — a false alarm of exactly the kind
  that makes a real one unbelievable. Reading persona's **real** TLS
  fingerprint means driving these endpoints from the engine; that is a
  different transport and a later slice.

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

## The GPU is two rows, and it is a product row (PS-69)

The committed record above tags its two renderer rows `host`, and an earlier
version of this file explained them away as *"the sandbox has no GPU"*. **That
exemption is withdrawn** — owner decision, 2026-08-22, recorded in knowledge
article PS-10 under *"a GPU-less environment is not an exemption"*:

> there will be no dev-VM and no GPU machine in the loop, and the engine is
> expected to present a plausible GPU wherever it runs, including on a host
> that has none.

So the GPU rows are `fingerprint`, and a red on one is a **masking finding**
filed against `undetectable-masking` with the reading attached — never written
off as the container's fault.

Asked whether the bar covers only the renderer *strings* or also the *pixels a
checker renders*, the owner answered **both** (*"планка требует идеала и там и
там"*). Those are different vectors with different fixes, so every GPU reading
now carries a `vector` key and they are recorded **apart**:

| `vector` | what it is | who fixes it |
|---|---|---|
| `gpu_claimed` | what the renderer **says it is** — the `WEBGL_debug_renderer_info` strings, vendor, reported capabilities. persona chooses these | the spoofer's declared values |
| `gpu_rendered` | what the checker's **own rendering produced** — canvas/WebGL hashes computed from pixels. persona does **not** choose these | the rendering layer, if at all |

The pairing is the point: a plausible claimed string beside a hash that came
out of a software rasteriser is the *"the string is right but the render gives
us away"* case, and **neither row alone can show it**. A single merged "GPU
red" cannot be acted on, which is why reporting *which* vector a red came from
is an obligation rather than a nicety.

Both prose checkers publish both vectors, and all of it was already on the
captured pages — only the claimed string was ever read:

- **pixelscan** — `webgl_vendor`, `webgl_renderer` (claimed); `webgl_hash`,
  `canvas_hash` (rendered)
- **creepjs** — `gpu_vendor`, `gpu_renderer` (claimed); `webgl_image_hash`,
  `webgl_pixel_hash`, `canvas_data_hash` (rendered)

**The committed record is NOT retagged, deliberately.** A reading carries its
`sort` (and now its `vector`) so a record stays interpretable after the
catalogue moves — a row taken when an item was tagged `host` must not silently
re-interpret as `fingerprint` because the tag was later corrected. So that file
still says `host`, correctly, as the dated observation it is. The *next* record
will carry the new tags, and the difference is the catalogue changing rather
than the product.

Two reader defects were found while splitting these rows, both by
over-matching the new patterns against the captured pages, and both **hidden by
luck of ordering** rather than by construction:

- a bare `gpu:` matches inside CreepJS's `webgpu: unsupported`, so on a page
  where the real GPU block does not populate the reader would record
  `unsupported` **as the GPU vendor** — a missing verdict recorded as a real
  value, rather than the loud `absent` it should be;
- a bare `data:` matches **twice** — Canvas's `data: 8d1ce292` and the Audio
  block's `data:2dcdf6c2` — so a reordered or partial page would record **the
  audio hash as the canvas rendering**, a `read` row carrying a corrupted
  value on a row whose entire purpose is being compared across runs.

Both are anchored now and both directions are pinned by tests.

## Comparing two records (PS-67)

    python -m src.services.verify.checker_cli compare before.json after.json

Reports **only what moved**, classified by the `sort` of the row that moved,
which is what the three-sort tagging above exists for. It reads two files,
needs no exit, and **gates nothing** — a difference opens a triage, and what
the triage finds is what blocks.

- A moved **`fingerprint`** row is the finding, and it is loudest when the
  **exit also moved** between the two records: that is the coupling a rotating
  exit was chosen to expose.
- A moved **`exit`** row is context. It rotates by design.
- A moved **`host`** row is a finding on the **same** machine and context
  across two machines — the header's `environment` decides which.
- A moved **`harness`** row is reported under its own heading, so this repo's
  own Python/OpenSSL shape can never be triaged as persona's fingerprint.
- A row that went `read` → `unobtainable` is **coverage lost, not drift**, and
  a row that appeared or vanished is a **catalogue** change, not drift. Both
  are exactly the cases this record's 24 unobtainable rows would otherwise
  turn into a screen of false red.

Exit codes follow the convention PS-61 settled: `1` a finding, `3` coverage was
lost, `2` **refused**, `0` nothing to triage (which means *no finding* — never
*no differences*; read the output).

It **refuses** rather than emitting a diff that reads as catastrophic drift
when the two records were never comparable: a different `seed` (the engine's
fingerprint is seed-derived — see above), a different **declared machine**, a
different engine build, a different schema version, or a missing
`seed`/`engine` header. The first three are overridable with
`--allow-different-seed` / `--allow-different-machine` / `--allow-cross-engine`;
a *missing* header is not, because an unrecorded fact gives an operator nothing
to weigh. Under either override the fingerprint rows report as seed-explained
or machine-explained **context**, so a flag can never manufacture the loudest
finding.

The **declared machine** guard is the PS-69 half of that same argument: the
declared machine is the spine of a presented identity (GPU strings, voices,
fonts, screen conventions, platform flags, UA and client hints), so two records
that declared different machines were never supposed to match on fingerprint
rows. The comparator handles the field's absence per side: both missing
compares fine (the committed record above has no such field), both present and
differing needs the override, and **present on only one side is refused** —
treating "no field" as "same machine" is exactly how a configuration change
would get reported as a coupling.

That per-side handling exists because of a seam PS-81 has since closed at the
source, and the seam is worth understanding because **it is not retroactive**.
PS-69 added `declared_machine` *without* bumping `schema_version`, so for a
window every record said `1` whether or not it carried the field, and the
schema guard could not separate the two generations at all. PS-81 recorded what
each generation's header actually contains (`matrix.HEADER_GENERATIONS`) and
bumped the writer to **`2`**, so a reading taken from now on is distinguishable
by its version alone.

What that does *not* do is fix records already written. The committed reading
above is genuinely generation `1` and still says so — nothing was re-tagged —
but any record written during the drift window claims `1` while carrying
generation `2`'s keys. `schema_ledger.generation_of` reads the generation from
the **keys**, not from the claimed version, so such a record can be identified
(`schema_ledger.mislabelled` reports exactly that disagreement) rather than
trusted. **The per-side absence handling therefore stays**, and so does the
machine guard: they are what covers the records the bump arrived too late for.

Do not confuse the two "machine" words: `environment` is the **host** the
reading was taken *on* (it governs `host`-sorted rows), while
`declared_machine` is what the profile **presented** (it governs
`fingerprint`-sorted rows). One laptop can declare Windows or macOS, so the
report words them apart deliberately.
