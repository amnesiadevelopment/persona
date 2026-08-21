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
  fingerprint means driving these endpoints from the engine; that is a
  different transport and a later slice.

## Provenance of the committed record, including what is wrong with it

Recorded honestly rather than re-run until it looked good:

1. **A first run was taken and then discarded as a baseline**, because reading
   it found three defects *in the reader*: a `phantom\s*js` pattern that
   matched the page's own probe label `phantomJS` and so recorded a clean
   browser as a **PhantomJS detection**; a GPU capture that stopped at the `)`
   inside `Intel(R)` and recorded a truncated renderer; and the harness/
   fingerprint mislabelling above. All three are now pinned by tests.

2. **The committed run used the corrected reader**, but the mobile exit
   **degraded during it** — `pixelscan`, `iphey` and `creepjs` failed with
   `NS_ERROR_UNKNOWN_HOST` (DNS at the exit), and the exit stopped answering
   entirely immediately afterwards (`curl (97)` on every host, three attempts
   spaced 20s apart). So this record carries **28 unobtainable rows**, and its
   browser tier is much thinner than the matrix can actually produce.

   That is left exactly as it is. Rotation and outage are the operator's, from
   the host: the standing rule is *report and stop*, never retry around a dead
   exit, and never fall back to a direct connection. A run that recorded fewer
   verdicts is a legitimate outcome; a run that quietly recorded them from the
   wrong address would not be.

   **Consequence for whoever compares next:** most browser-tier rows here are
   `unobtainable`, not `absent`. Do not read them as clean, and do not treat
   the first later run that actually reads them as a regression.

`checker-pages/*.txt` are the rendered page texts captured through the exit on
2026-08-21, kept so the suite can prove each pattern reads a **real** page the
way the catalogue claims — which is how three of the four original pattern
defects were caught.
