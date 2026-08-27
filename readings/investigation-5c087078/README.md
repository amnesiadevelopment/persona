# Reproduction guide — investigation `5c087078`

Bundle for **"Fork or stay downstream: what running our own engine would actually cost"**.
Read [`REPORT.md`](REPORT.md) first; this file only explains how to re-run the measurements.

All figures were collected **2026-08-27**. Several sources are live and will drift —
in particular the Chromium stable version, the CVE gap, and upstream's silence counter.
That drift is the point of §9 of the report ("what would have to become true").

```
REPORT.md                     the report
README.md                     this file
scripts/                      the instruments
data/                         raw collected evidence + derived results
```

---

## The one thing to read before trusting any rebase number

**`data/results-ctrl-144b.tsv` is the control**: the 144 patch series applied against
Chromium 144, the tag the patches were authored against. It should be near-perfect,
and it is — 119 CLEAN / 3 OFFSET / 2 CONFLICT / 3 NO_TARGET.

**`data/results-ctrl-144-BROKEN-HARNESS.tsv` is the discarded first run**, kept on
purpose. It reports 26 CONFLICTs. Those were **not** real: the fetcher ran 24-way
parallel and silently lost files to googlesource rate limiting, so `patch` reported
"can't find file to patch" with `failed_hunks=0`. Serial retries returned HTTP 200 for
the same paths. Diffing the two control files shows the failure mode directly.

If you re-run this and your control does not land near 119 CLEAN, **your tree is
incomplete — fix that before reading the test results.**

---

## Re-running the rebase measurement (§3, §4 of the report)

Needs: `git`, `curl`, `patch`, `python3`, network access to
`chromium.googlesource.com` and `github.com`.

```bash
# 1. get the only patch set that exists (tag 144; note main and tag 148 hold no source)
mkdir -p /tmp/fpc && cd /tmp/fpc && git init -q .
git remote add origin https://github.com/adryfish/fingerprint-chromium
git fetch -q --depth 1 origin "+refs/tags/144.0.7559.132:refs/tags/t144"

# 2. export the 127 patches in series order
mkdir -p /tmp/rebase/patches && cd /tmp/rebase
git -C /tmp/fpc show t144:patches/series | grep -ve '^\s*$' -e '^#' > series.txt
while read -r p; do
  mkdir -p "patches/$(dirname "$p")"
  git -C /tmp/fpc show "t144:patches/$p" > "patches/$p"
done < series.txt

# 3. copy the instruments from this bundle
cp <bundle>/scripts/{fetch_tree2.sh,apply_series.sh} .
cp <bundle>/scripts/touched-files.txt .        # the 713 files the series touches
chmod +x fetch_tree2.sh apply_series.sh

# 4. fetch the minimal tree at each tag. RE-RUN UNTIL "failed=0" — it is resumable
#    and skips files already present. Rate limiting is expected; retries handle it.
./fetch_tree2.sh 144.0.7559.132 tree-144 touched-files.txt   # control
./fetch_tree2.sh 145.0.7632.159 tree-145 touched-files.txt   # one major bump
./fetch_tree2.sh 152.0.7977.64  tree-152 touched-files.txt   # eight majors

# 5. apply. ALWAYS run the control first and check it before the others.
cp -a tree-144 tree-144-work && ./apply_series.sh tree-144-work ctrl-144b
cp -a tree-145 tree-145-work && ./apply_series.sh tree-145-work test-145
cp -a tree-152 tree-152-work && ./apply_series.sh tree-152-work test-152
```

`touched-files.txt` is regenerable from the patches themselves by collecting every
`---`/`+++` path (excluding `/dev/null`); it is shipped so the file set is pinned.

### How to read the classification

| status | meaning | human work needed |
|---|---|---|
| `CLEAN` | applied, zero fuzz, zero offset | none |
| `OFFSET` | applied, hunks shifted | none — automatic |
| `FUZZ` | applied only with fuzz | eyeball it |
| `CONFLICT` | did not apply | **manual rebase** |
| `NO_TARGET` | every target file absent from the minimal tree | not evaluable here |

**`CONFLICT` counts textual failure only. Nothing here compiles anything**, so every
cost in the report is a lower bound (§10).

---

## Re-running the realm probe (§7B)

Fully offline, needs a local `/usr/bin/chromium`:

```bash
python3 scripts/realm_class_probe.py
```

Spawns a throwaway HTTP origin (service workers need a real origin — `file://` will
not do) and tries a `blob:` bootstrap in four realms. Expected result, written to
`realm-class-probe.json`:

- dedicated / module / shared workers → **accepted**
- `ServiceWorker` → **refused**, `TypeError: The URL protocol of the script ('blob:…') is not supported`

That asymmetry is the §7B finding: one realm, not a class.

---

## Data files

| file | what it is | report §|
|---|---|---|
| `gh-repo.json`, `releases-all.json`, `gh-tags.json`, `gh-branches.json`, `gh-commits.json` | upstream repo state, all 13 releases/tags | §1, §5 |
| `source-lag.json` | binary-publish vs source-commit lag per version | §1 |
| `cdash-stable-*.json` | Chromium stable per platform | §2 |
| `chrome-blog-posts.json` | 324 Chrome Releases posts (2026-03-10 → 2026-08-26) | §2 |
| `cve-gap.json` | 1,284 CVEs since our build, by severity | §2 |
| `kev.json`, `kev-chrome.json` | CISA KEV catalogue + Chrome subset | §2 |
| `results-ctrl-144b.tsv` | **the control** | §3 |
| `results-ctrl-144-BROKEN-HARNESS.tsv` | discarded first run, kept as evidence | §3 |
| `results-test-145.tsv`, `results-test-152.tsv` | one-major and eight-major runs | §3 |
| `rebase-classification.json` | all 127 patches × all three tags | §3 |
| `churn-analysis.json` | 656 failed hunks attributed by directory | §4 |
| `deleted-144-to-152.txt` | 31 touched files upstream deleted/moved | §3 |
| `per-patch-files.json` | which files each patch touches | §3, §4 |
| `issues-all.json`, `prs-all.json`, `gh-comments.json` | upstream liveness evidence | §5 |
| `ugc-{win,mac,lin}-build.yml` | ungoogled's own CI configs | §6 |
| `build-jobtimes.json`, `build-summary.json` | per-job build timings (**authoritative**) | §6 |
| `build-wallclock.json` | **superseded, retained deliberately** — holds the 9,676 h and negative artefacts described in §10 | §10 |
| `competitors.json` | comparable projects' health | §7A |
| `realm-class-probe.json` | realm probe output | §7B |

## Known limits

- The apply harness uses a **713-file subset**, not a full Chromium checkout, and never
  compiles. Lower bound, always.
- Tree completeness varies by tag (682 / 680 / 651 of 713); the rest are genuine 404s
  (paths created by earlier patches, or generated by ungoogled's tooling).
- `build-wallclock.json` must **not** be used for build durations — `updated_at` is
  bumped by re-runs and artifact expiry. Use `build-summary.json`.
- Signing costs in §6 are **unverified** and deliberately unquoted.
