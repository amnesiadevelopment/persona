# Engine release provenance records

For every Personium engine we have **published**, a record here says which
ungoogled base, which Chromium version and which patch set produced it — and a
reader can check a published binary against that record without trusting
anything but the bytes.

One file per release, named for its tag: `personium-<version>.json`.

```
python3 scripts/ps343_verify_release_provenance.py --download   # fetch + check
python3 scripts/ps343_verify_release_provenance.py --assets DIR # check local files
python3 scripts/ps343_verify_release_provenance.py --lint-only  # record only
```

## The quantifier in that first sentence, and what evaluates it

*"For every Personium engine we have **published**"* is a claim about the
PUBLISHED set, and the verifier above cannot evaluate it: `load_records()` globs
this directory and `main()` iterates that list, so the RECORD SET is its
denominator. A published release with no record is not red there — it is
**absent from the question**.

`.github/scripts/ps343_release_audit.py` is the half that asks it. It reconciles
`updater.engine_versions_newest_first()` — the same enumerator the operator's
app uses, so there is no second place that knows the tag prefix — against
`personium-*.json` in this directory, and reports a published release with no
record as a **named, red row**. It runs daily from
`.github/workflows/engine-release-provenance-audit.yml`.

| exit | meaning |
|---|---|
| `0` | every published release has a record here |
| `1` | a published release has **no** record — named |
| `2` | the published set **could not be established** (network, rate limit, an empty answer). NOT "every release is recorded" |

⚠️ Two questions, deliberately kept apart. The audit asks whether a record
**exists**; the verifier asks whether it **agrees with the bytes**. A green
audit says nothing about a record's contents, and a green verifier says nothing
about a release nobody wrote a record for.

---

## Why these live in git and not in a CI artifact

`scripts/ps218_manifest.sh` already emits a good per-run build manifest, and it
is not a substitute for this. Two reasons:

1. **It describes a trial CI run, not the shipped release.** None of the three
   published `personium-152.0.7977.75` assets was produced by any workflow in
   this repository — `RELEASING.md` says so plainly.
2. **It lives in a CI artifact with 30-day retention.** *A provenance record
   that expires is not a provenance record.* These files outlive every runner
   because they are in the tree.

---

## The record's one real idea: every field carries how it was established

The value of a provenance record is not that it is detailed — it is that it can
be **checked**, and that where it cannot be checked it **says so**. So every
field is `{value, confidence}`, and `confidence` is a closed vocabulary:

| confidence | means | checkable? |
|---|---|---|
| `derived_from_artifact` | re-derived from the shipped bytes | **yes — and the verifier does** |
| `from_repository` | read out of this repo at `recorded.repo_commit` | a true statement *about the repository*; **not** an attestation about the build |
| `unknown` | not established, recorded as a gap | n/a — and a field declared `unknown` **may not carry a value** |

That last rule is the honesty mechanism, and the verifier enforces it as a
**RED**, not a warning. Filling in a plausible-looking `build.produced_by` is
exactly the failure this record exists to prevent — a manifest assembled from
assumptions is worse than one with a stated gap, because it reads as evidence.

`from_repository` is the one to read carefully. It means *"the tree says this"*,
not *"this built the binary"*. Where nothing in the repo built the asset, that
distinction is the whole difference between a record and a guess.

### `execution` is deliberately not a confidence

Each asset also carries `execution: {executed, note}` — **with no `confidence`
key**. Whether we ran the binary is a fact about the *recording session*, not
about the artifact's provenance, and folding it into the confidence vocabulary
produced a genuine contradiction the linter caught while this format was being
written (`executed: false` asserted under `confidence: unknown`). Two questions,
two fields.

---

## Why a digest is only half the record

**A digest attests to identity, not to content.** `sha256` says "these are the
bytes that were published" and says nothing at all about what is inside them.
This project has already been bitten by exactly that: three seats verified a
Chromium binary by hash and none of them ever executed it.

So every asset carries a second, content-level derivation alongside its digest,
read out of the artifact's own structure:

| asset | derived from |
|---|---|
| Linux `.AppImage` | `X-AppImage-Version` in the extracted `.desktop` (the **packaging** tag, `…-1`), plus our switches in `opt/*/chrome` |
| Windows `.zip` | the `Chrome-bin/<version>/` directory **and** `assemblyIdentity/@version` in `<version>.manifest` — two independent statements, both required |
| macOS `.dmg` | `CFBundleShortVersionString` of the `org.chromium.Chromium` bundle, read out of the UDIF/APFS image with stdlib only (no `hdiutil`, no `7z`) |

Every asset also states which of the eleven switches
`000-add-fingerprint-switches.patch` introduces are present in the shipped
machine code. That is the load-bearing link between the patch set in this repo
and the binaries on the release — and it is a **content** claim, so it survives
a rename and a re-upload in a way a digest does not.

Digest and derivation are reported as **separate rows**. Neither stands in for
the other.

---

## The verifier's three exit statuses

| exit | meaning |
|---|---|
| `0` | every derivable check agreed with the record |
| `1` | a check went **RED** — the artifact and the record disagree |
| `2` | the measurement **could not be made** (asset absent, host cannot read this format, record unreadable) |

`2` is **not** "the record is fine" — nothing was measured. Same vocabulary
`scripts/ps299_rebase_probe.py` uses, for the same reason.

---

## What the first record found

`personium-152.0.7977.75` was recorded retroactively, and recording it surfaced
two things that were not previously written down anywhere:

* **The macOS asset is named `.75` and contains `.64`.** `7977.64` is the only
  `7977.x` string anywhere in the 457 MB decompressed image; `7977.75` does not
  occur in it at all. The engine updater compares *tags*, so a macOS machine on
  this asset believes it is on `.75`. Measured, not reported — the ticket flagged
  it as a rumour and the verifier turned it into a number.
* **No build attestation exists for any of the three.** The patch-set link is
  established at the level of *content* (all eleven switches in all three
  binaries) and **not** as a build attestation. A reader may conclude these
  binaries carry our patch layer. A reader may **not** conclude they were built
  from this exact tree.

Both are in the record's `discrepancies[]`, with their consequence stated.

---

## Adding a record for a new release

1. Cut the release as `RELEASING.md` describes.
2. Copy the existing record, update `tag` / `published_at` / `assets[]`
   (name, `size_bytes`, `sha256` from the release API's `digest` field).
3. Fill `derived` from what you actually re-derived. **Never guess a value**;
   set `unknown` where you tried and could not. Note that an asset with **no**
   `derived` block at all is `UNMEASURED` (exit `2`), not a pass — its digest
   is checked and its *content* is not, which is precisely the trap above. So
   "leave it out" is honest about one field and not about a whole asset.
4. Run the verifier against the real assets. Exit `0` is the bar.
5. If you find a mismatch, record it in `discrepancies[]` rather than editing
   the derived value to match — the mismatch *is* the finding.

---

## What the verifier checks, and what it deliberately does not

`derived_from_artifact` is a promise that the verifier checks the field, so
every field wearing that label has a code path behind it:

| claim | checked against |
|---|---|
| `assets[].sha256`, `size_bytes` | the file on disk |
| `assets[].derived.*` | the per-format deriver's output |
| `base.chromium_version` | the Windows `manifest_version` and `version_dir`, and the Linux `appimage_version` with its packaging revision dropped |
| `base.ungoogled_tag` | the Linux `appimage_version` — the only asset carrying the packaging tag |
| `patch_set.switches_introduced` | every declared switch must be found in the shipped machine code of **at least one** asset |

Two of those need their reasoning stated, because both were holes:

* **The macOS `bundle_short_version` is deliberately NOT a witness for
  `base.chromium_version`.** It disagrees with the tag — that *is* the record's
  headline finding — so admitting it would make the record go RED on the very
  discrepancy it exists to preserve, and the pressure would be to "correct" the
  finding away. The exclusion is in the code as `BASE_WITNESSES_EXCLUDED`, with
  its reason, rather than left to be inferred from which fields happen to agree.
* **`patch_set.switches_introduced` is the derivers' own search list**, so an
  invented switch appears on *neither* side of the per-asset comparison and
  cannot be falsified by that comparison. What the verifier checks instead is
  the claim the label actually implies: a switch declared and found in **no**
  shipped asset is RED.

A `base` field claiming `derived_from_artifact` for which the verifier defines
no witness is `UNMEASURED`, never silently green.
