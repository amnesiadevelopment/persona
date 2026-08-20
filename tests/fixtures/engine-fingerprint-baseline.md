# Engine fingerprint baseline — what a pinned profile looks like on a given engine

Reference recording behind `src/services/verify/baseline.py` (ticket PS-24).
This file exists so `engine-fingerprint-baseline.firefox.json` has a checkable
provenance and the next person can reproduce it exactly instead of guessing.

**Artifact:** `tests/fixtures/engine-fingerprint-baseline.firefox.json`
**Engine build it was taken under:** `firefox-20` — also recorded inside the
artifact as `engine_build`, so "is this baseline still current?" is answerable
by reading the file and comparing it against `engine-baseline.txt` at the repo
root, without running anything.

## Why this exists

The engine moves on its own. `.github/workflows/engine-autoupdate.yml` runs
daily at 06:00 UTC, detects a newer patched-Firefox build, rewrites the pins and
`engine-baseline.txt`, commits to `main` and pushes a version tag — which
triggers `release.yml` to build and publish. Nobody approves that.

An engine change is the single event most likely to move what a site sees about
a profile, because it replaces the layer half the masking lives in. The release
workflow already refuses an engine *downgrade*; this artifact is the missing
complement — it makes it possible to refuse an engine that silently changed the
**identity**, which a version number cannot express.

## How to use it

```bash
# Does the current engine still produce the recorded identity?
xvfb-run -a python -m src.services.verify.baseline_cli check
```

Exit `0` means every probe was read on both sides and none of them moved.
Exit `1` means either a probe drifted (it is named, with expected vs observed,
in both realms) or a probe could not be read at all. Exit `2` means the check
could not run — most often no display.

## How the artifact was produced

Recorded by `python -m src.services.verify.baseline_cli record`, which launches
the pinned profile in-process, reads every probe through the live session's eval
hook, and tears the session down. It is a reading taken from a **real launched
browser**, never from generated source text.

The exact inputs are also embedded in the artifact itself under `provenance`:

| Input | Value | Why pinned |
|---|---|---|
| profile name | `persona-fingerprint-baseline` | `Profile.fingerprint_seed` is `crc32(name)` — **pinning the name is what pins the seed**, and with it the whole derived identity |
| fingerprint seed | `1042768975` | derived from the name above; recorded so a mismatch is visible |
| `os_type` | `windows` | explicit, so no host value can substitute |
| `device_type` | `desktop` | explicit, so no mobile preset is picked |
| `engine` | `firefox` | the family that auto-bumps |
| `resolution` | `1920x1080` | explicit, never `auto` — `auto` picks a preset from the seed, which is reproducible but leaves the geometry implicit |
| proxy | **none** | a proxy makes locale/timezone follow a geo lookup, and variance the network introduced would read exactly like variance the engine introduced. With no proxy the launcher pins `en-US` + `America/New_York` |
| bookmarks | **explicitly cleared** (`[]`, not `None`) | `None` means "use the store's defaults", which would make the reading depend on the operator's bookmark store |
| certificate | none | an mTLS session would add a terminator to the launch path |
| realms | `window` **and** `worker` | a spoof that lands on the page but not inside a Web Worker is the historically load-bearing leak, and it is invisible unless the worker realm is read |

The profile is constructed as a plain dataclass and is **never written to the
profile store**, so the baseline identity cannot be edited by a human out from
under the artifact.

## Verification performed when this landed

All four checks were run against a real browser on `firefox-20`, not simulated:

- **Stability first.** Three independent recordings — each wiping the profile's
  data directory and launching a fresh browser — produced **byte-identical**
  files, and the comparator called them identical. This is the property
  everything else rests on: if it did not hold, the baseline would be noise and
  anything built on it would be false confidence.
- **The gate can fail.** One spoofed value was perturbed in a copy of the
  baseline (`worker` / `navigator.hardwareConcurrency`, `12` → `8` — what a host
  core-count leak would look like). It was caught, named with both values, and
  exited non-zero.
- **A missing probe is reported, not skipped.** Deleting
  `worker/navigator.userAgent` from one side produced an explicit `added` entry
  and a non-zero exit, rather than passing quietly.
- **Both realms.** 78 readings: 45 in `window`, 33 in `worker`.

For the record, the recording also demonstrates the masking is real rather than
inherited: the host has 8 cores and reports Linux, while the recorded profile
reports 12 cores, `Win32`, and Firefox 151 on Windows.

## ⚠️ What this does NOT do

**This check does not fire on the automatic engine bump.** Both workflows run on
`ubuntu-24.04` with no display server, no engine installed and no `xvfb`, so
nothing in `.github/` invokes it. Wiring it in is a real slice of work — install
the engine, provide a display, keep it deterministic on a headless runner where
behaviour genuinely differs from an operator's desk — and it has not been done.

What exists today is a check an operator **can run**. That is the precondition
for automating it, not a substitute for it. **Until that slice lands, the daily
job still commits and tags on its own, and the bump ships unverified.** The fact
that the bump is fully automatic is the argument for prioritising the CI ticket.

Two things that must not be done to "solve" that gap: making the autobump job
skip the comparison silently, and weakening the comparison so it can run without
a browser. Comparing generated source text instead of a live reading is
precisely the defect the verification service was built to end.

**The `firefox-19` → `firefox-20` transition is unverified and will stay
unverified.** The baseline was first recorded on `firefox-20`, which was already
current by the time this landed; reconstructing the previous engine to retro-diff
that transition was explicitly not wanted. This machinery makes `20` → `21` and
everything after it checkable.

## When to re-record — and why you are allowed to

When a bump is genuinely accepted — the diff was reviewed, and the change is
understood and wanted — **re-record the baseline on the new engine and commit
that as a reviewable change**:

```bash
xvfb-run -a python -m src.services.verify.baseline_cli record
git add tests/fixtures/engine-fingerprint-baseline.firefox.json
# commit with WHY the diff was acceptable, not just "update baseline"
```

This is stated deliberately, because the opposite convention is worse: **a
baseline nobody may update becomes a permanently red check that everyone learns
to ignore.** A re-recording is a normal, reviewable commit — the diff in the
artifact *is* the record of what the engine changed, which is exactly what a
reviewer should be looking at.

`record` refuses to write a baseline that has any unreadable probe, because a
probe that errors here would compare equal against the same error later and be
reported as agreement.

## A trap worth knowing about

`diff_snapshots` compares entries verbatim, so two identically-**failed**
readings (`{"error": X}` on both sides) compare equal and the raw diff reports
them as agreement. A comparison could therefore go green off two non-readings.

The baseline check refuses that: it counts errors on both sides and fails when
either side has any, reporting `INCONCLUSIVE`. A pass from this command means
"every probe was read **and** nothing moved" — never "nothing could be read on
either side".

## Adding a second engine

Chromium can be added by the same mechanism (it is reachable over CDP from any
process, so it does not need the in-process launch the Firefox path requires).
It was deliberately left out of the first slice: Firefox is the engine that
auto-bumps, and covering both was not worth blocking this on.
