# PS-288 — macOS loses its Firefox engine: what actually breaks, and what a mac asset would and would not buy

**Date:** 2026-09-03 · **Tree:** `origin/main` `1932a6d` · **Author:** worker seat
**Method:** upstream release/commit enumeration via the GitHub API, and the four
published `invisible_core` wheels read and *executed* under a patched
`sys.platform == "darwin"`.

The ticket asked four questions in a stated order and named the fourth decisive.
It is decisive, and **it comes back the opposite way from the one the ticket's
plan was built on**. Everything below §1 exists to say what follows from that.

---

## 0. Summary — the finding in four lines

1. **`invisible_core` refuses macOS by PLATFORM CHECK, not by asset absence.**
   From `invisible_core==20.16.0` onward, `ensure_binary()` refuses on
   `sys.platform == "darwin"` **before it ever looks at an asset**, and
   `make_virtual_display()` — on the *launch* path, downstream of any download —
   raises on darwin too. A correctly-named macOS asset we produce ourselves is
   **not accepted**: it is never reached. (§1)
2. **So the shape of the fix changes completely, exactly as the ticket
   anticipated.** "Build the macOS assets ourselves" is *necessary but nowhere
   near sufficient*. There are two refusals to clear, in two different packages,
   and only one of them is about a file existing. (§1.4, §5)
3. **The break is ALSO one release earlier and one axis wider than the ticket
   states.** The ticket says the danger begins at `firefox-21`. The *asset*
   cliff does. The *platform refusal* shipped in `invisible_core 20.16.0`, which
   is still sealed to **`firefox-20`** — our own pinned tag. Mac dies on a
   **core-only bump that never moves `engine-baseline.txt` at all**, and
   `scripts/engine_autobump.py` is structurally blind to it because it compares
   **majors only**. (§4)
4. **A macOS build is genuinely obtainable** — the source config, the mac-specific
   patches and the exact prior CI job all still exist and are recoverable from a
   single revertible commit; cost is ~68–140 min per leg on GitHub-hosted mac
   runners. That half of the ticket's optimism holds. (§2, §3)

Bottom line for the owner's constraint (*macOS is required, and a negative
finding is a report rather than a licence to drop the platform*): **macOS is
recoverable, and nothing here says otherwise.** But it cannot be recovered by
producing an asset alone, and the deadline is earlier than believed.

---

## 1. Question 4 (decisive) — platform check, not asset absence

The ticket: *"Does `invisible_core` refuse on Mac by PLATFORM CHECK or by ASSET
ABSENCE — i.e. would a correctly-named asset we produce even be accepted by the
installed driver? … If it hard-refuses on `sys.platform == 'darwin'` regardless
of assets, producing an asset does not help and the shape of the fix changes
completely."*

**It hard-refuses on the platform.** Measured, not read.

### 1.1 The refusal in `ensure_binary()`

`invisible_core/download.py` (26.17.0, lines 393–412):

```python
plat = sys.platform
if plat not in PIATTAFORME_SUPPORTATE:
    if plat == "darwin":
        raise NotImplementedError(
            "macOS non e' piu' una piattaforma supportata: da firefox-21 in poi non "
            "vengono piu' pubblicati binari per Mac, e questo pacchetto non ne scarica.\n…")
    raise NotImplementedError(…)
asset = seal.asset_for(plat, platform.machine())   # ← never reached on darwin
```

The refusal is **above** `seal.asset_for(...)`. The declaration it reads is a
single tuple in `seal.py:51`:

```python
GAMBE_SUPPORTATE = (("linux", "arm64"), ("linux", "x86_64"), ("win32", "x86_64"))
PIATTAFORME_SUPPORTATE = tuple(dict.fromkeys(p for p, _ in GAMBE_SUPPORTATE))
```

Executed under a patched platform (core 26.17.0):

```
ensure_binary() with sys.platform='darwin'
  -> NotImplementedError: macOS non e' piu' una piattaforma supportata…
```

An asset sitting on a release, correctly named, byte-perfect, changes nothing:
the function returns before asset resolution.

### 1.2 A second refusal, further downstream, at LAUNCH

Even if the download gate were cleared, `invisible_core/_headless.py:166-181`
refuses again — and this one is on the *launch* path, reached through
`invisible_playwright.launcher._resolve_headless()` and `async_api` alike:

```python
if sys.platform.startswith("linux"): return _LinuxVirtualDisplay()
if sys.platform == "win32":          return None
raise RuntimeError("invisible_playwright supporta Windows e Linux "
                   "(macOS non e' piu' supportato; got 'darwin')")
```

Measured the same way:

```
make_virtual_display() with sys.platform='darwin'
  -> RuntimeError: invisible_playwright supporta Windows e Linux (macOS non e' piu' supportato…)
```

In core `20.14.0` (what we ship today) that same line reads
`if sys.platform in ("win32", "darwin"): return None` — macOS was a *supported,
self-cloaking* platform. The regression is a deliberate narrowing, in two
places, on two different code paths.

### 1.3 The name is gone too

`constants.ARCHIVE_NAME()` no longer *constructs* a darwin name for a local seal
(26.17.0, `constants.py:101-104` — the `if pk == "darwin"` branch present in
20.14.0/20.15.0 was replaced by a comment explaining its removal), and
`_post_extract_darwin()` — the ad-hoc-signature / quarantine-strip step that made
a downloaded `.app` launchable — was **deleted outright** from `download.py`
between 20.15.0 and 20.16.0.

This matters for a reason easy to miss: persona's own `_expected_asset()`
(`src/services/engine/firefox.py:77-85`) calls exactly this function. On a
release-sealed core it still returns a mac name by reading it out of a
*historical* seal (measured: core 20.16.0 sealed to firefox-20 still answers
`firefox-151.0-stealth-macos-arm64.tar.gz`), so **persona's update-offer layer
keeps believing macOS works after the driver has stopped supporting it.** The
refusal surfaces later, at install/launch, not at the offer.

### 1.4 What this changes

| The ticket's plan assumed | Measured reality |
|---|---|
| Missing input is a *file* | Missing input is a *declaration* (`GAMBE_SUPPORTATE`) plus two refusals |
| Produce the asset → install path accepts it | Asset is never reached; refusal precedes lookup |
| One place to fix (upstream CI) | Three: upstream CI, `invisible_core`'s platform declaration, `_headless`'s launch gate |
| Downstream is passive | Downstream **actively rejects** darwin, twice |

Producing a macOS asset is a **prerequisite** of a fix, not the fix.

---

## 2. Question 1 — the upstream tree still carries macOS

Yes, fully. Mac support was removed from **CI and the seal**, *not* from the
source.

Verified on `feder-cr/firefox_antidetect_patch` (public, ~5.08 GB, `pushed_at`
2026-08-31):

- `widget/cocoa/` present on `stealth/151` with **129 entries** — the whole
  Cocoa widget backend.
- The **antidetect patch itself is present and mac-specific**:
  `widget/cocoa/nsCocoaWindow.mm` carries `STEALTHFOX_CLOAK_HOOK v1` at lines
  5265 and 5368, reading `zoom.stealth.cloak_windows` — the macOS analogue of the
  Windows `DWMWA_CLOAK` path (`widget/windows/nsWindow.cpp`, 6 hits). This is
  precisely the hand-written work that "we would have to write ourselves" if it
  were absent. It is not absent.
- `browser/installer/Makefile.in`, `gfx/thebes/`, `toolkit/library/` all present.
- The removal commit's own message states the intent explicitly: *"Cosa NON e'
  toccato, di proposito: … il codice mac interno di Firefox (cocoa, CoreText)"*
  — the internal mac code was deliberately left in place.

So the ticket's framing — *"what is missing is a macOS BUILD of code that already
exists"* — is **accurate**.

---

## 3. Questions 2 & 3 — the prior job is recoverable, and the cost is measured

### 3.1 The removal is one revertible commit

Branch `mac/rimozione-macos` holds commit **`eed9abeffe9b`** (2026-08-26),
*"macOS esce dalla pipeline di rilascio: tre gambe, non piu' cinque"*:

```
modified .github/workflows/release.yml   +512/-623
modified scripts/make_seal.py            +7/-3
modified scripts/test_make_seal.py       +354/-354
```

`release.yml` at `eed9abeffe9b^` (623 lines) carries the complete pre-removal mac
configuration verbatim:

| leg | runner | target | asset |
|---|---|---|---|
| `macos-arm64` | `macos-26` | `aarch64-apple-darwin` | `firefox-151.0-stealth-macos-arm64.tar.gz` |
| `macos-x86_64` | `macos-26-intel` | `x86_64-apple-darwin` | `firefox-151.0-stealth-macos-x86_64.tar.gz` |

plus the four steps the removal deleted: *Select Xcode 26.4.1 + export SDK path*,
the `--with-macos-sdk=$SDK_PATH` mozconfig append, the *Package + validate
(macOS)* step (`mach package` → juggler gate on the `.app`'s `omni.ja` →
`codesign --force --deep --sign -` → `--version` gate → `plutil -lint` on
`Info.plist`), and *Install pyobjc Quartz*. Both gate-matrix entries are there
too.

The commit also records something useful: `make_seal.py` dropped darwin from
`EXPECTED` but **deliberately kept it in `ENTRY_REL` and `NAME_RE`** — *"Si smette
di pretendere il mac, non di saperlo leggere."* The seal machinery can still
*read* mac assets. That is a real, if partial, foothold.

Two constraints on any revival, from the file's own comments:

- FF151 needs **macOS SDK ≥ 26.4**, first present on the `macos-26` images
  (Xcode 26.4.1) — `macos-15` will not do.
- The x86_64 leg must be **native** (`macos-26-intel`), not Rosetta: *"this
  product SPOOFS hardware, so a fingerprint measured under translation is not the
  one an Intel user receives."* That reasoning applies to us identically.

### 3.2 Cost — measured from their own runs, not estimated

Job durations from the last three release runs that built mac
(`actions/runs/<id>/jobs`):

| run (tag) | `build-macos-arm64` | `build-macos-x86_64` | mac gate |
|---|---|---|---|
| firefox-20 (`32040758970`) | 1 h 07 m | **2 h 17 m** | ~1 m |
| firefox-19 (`31447557862`) | 1 h 17 m | 2 h 20 m | ~1 m |
| firefox-18 (`30055500531`) | 1 h 09 m | 1 h 12 m | ~2 m |

So **~68–140 min per mac leg**, both legs in parallel with the linux/win legs
(whole run ≈ 2 h 20 m wall-clock), on **GitHub-hosted** `macos-26` /
`macos-26-intel` runners — no self-hosted Mac hardware required. The workflow
sets `timeout-minutes: 350`. The drive gate is ~1–2 min.

This is the *cheapest* answer the four questions could have returned. Cost is not
what blocks this.

---

## 4. The break is earlier and wider than the ticket states

The ticket's premise is that `engine-baseline.txt` pinning `firefox-20` is what
holds the line, and that the danger starts when a bump takes us to `firefox-21`.
**The asset cliff is at 21. The platform refusal is at `invisible_core 20.16.0`,
which is still sealed to `firefox-20`.**

Release-asset census (GitHub API, 2026-09-03) — confirms the ticket exactly:

| tag | assets | mac assets | published |
|---|---|---|---|
| firefox-14 … firefox-20 | 7–8 | **2** | ≤ 2026-08-17 |
| firefox-21 … firefox-26 | 6 | **0** | ≥ 2026-08-27 |

But the *package* census tells a different story:

| `invisible_core` | uploaded | sealed tag | seal has mac assets | `ensure_binary` on darwin | `make_virtual_display` on darwin |
|---|---|---|---|---|---|
| 20.14.0 *(ours)* | 2026-08-17 | firefox-20 | yes | works | returns `None` (supported) |
| 20.15.0 | 2026-08-18 | firefox-20 | yes | works | returns `None` (supported) |
| **20.16.0** | **2026-08-26** | **firefox-20** | **yes** | **REFUSES** | **RAISES** |
| 21.16.0 | 2026-08-27 | firefox-21 | no | REFUSES | RAISES |
| 26.17.0 | 2026-08-31 | firefox-26 | no | REFUSES | RAISES |

**`20.16.0` is the row that matters.** It is sealed to `firefox-20`, its seal
*still contains both macOS assets*, and it still refuses macOS. Measured:

```
core 20.16.0, seal firefox-20, assets include macos-arm64 + macos-x86_64
  ARCHIVE_NAME('darwin','arm64') -> 'firefox-151.0-stealth-macos-arm64.tar.gz'   # a name!
  ensure_binary() on darwin      -> NotImplementedError: macOS non e' piu' supportata
```

The asset is *named in the seal we are pinned to* and the package refuses it
anyway. That is the clearest possible demonstration that the gate is the platform
declaration and not the file.

### 4.1 Our autobump cannot see this

`scripts/engine_autobump.py` compares **majors only**:

```python
new_major = core_major(latest_core)          # '20.16.0' -> 20
if new_major <= cur_major:                   # 20 <= 20  -> no bump
    return BumpPlan(False, f"already on core major {cur_major} …")
```

`core_major("20.16.0") == core_major("20.14.0") == 20`, so the autobump reports
"nothing to do" — correct by its own rule, and it means the 20.14 → 20.16 move
is not something it will make. Good. But it also means **`engine-baseline.txt` is
not the guard people think it is on this axis**: the release-time downgrade
guards in `release.yml` (three copies, lines ~172, ~397, ~925) compare
`BINARY_VERSION` (`firefox-NN`) against the baseline. `firefox-20` vs
`firefox-20` passes. A core-only bump to 20.16.0 — by a manual pin edit, a
`requirements` refresh, a lockfile change, anything not going through
`engine_autobump.plan()` — ships a macOS build whose engine refuses to download
**and** refuses to launch, with **every existing guard green**.

The ticket says the fingerprint gate refusing the bump "is not a mac guard and
must not be relied on as one". That is right, and it understates it: the
*baseline pin itself* is not a mac guard either.

### 4.2 What today's build actually does on a Mac

We ship `invisible_core==20.14.0` (`pyproject.toml:111`) and
`invisible_playwright` at `353df4fa` (= release 0.7.1, 2026-08-17). Both predate
the refusal. **macOS works today.** The exposure is entirely forward-looking —
but the first step forward is smaller than one release.

Upstream driver releases and the cores they pin, for reference:

| `invisible_playwright` | pins | mac |
|---|---|---|
| 0.7.1 *(our pin)* | 20.14.0 | ✅ |
| v0.7.2 | 20.15.0 | ✅ |
| **v0.7.3** | **20.16.0** | ❌ **first refusing release** |
| v0.7.4 | 21.16.0 | ❌ |
| v0.8.0 … v0.10.0 | 23 / 24 / 25 / 26.17.0 | ❌ |

Upstream is at v0.10.0 / core 26.17.0. We are seven driver releases and six
engine releases behind, and **the very next one breaks the Mac.**

---

## 5. What a fix would actually require

Stated as a finding, not proposed as work — this ticket is an investigation and
the decision is not a worker's to make.

A macOS operator on an engine ≥ 21 needs **all** of:

1. **A macOS engine asset for the target `firefox-NN`.** Obtainable (§2, §3):
   revive the two legs from `eed9abeffe9b^` on `macos-26` / `macos-26-intel`,
   ~68–140 min each. Requires either upstream to accept it, or us to build from
   the public tree and host the asset somewhere the driver will fetch it.
2. **`invisible_core` to stop refusing darwin.** Requires `GAMBE_SUPPORTATE` to
   readmit the two darwin legs, `ARCHIVE_NAME`'s darwin branch back, and
   `_post_extract_darwin()` restored (without it a downloaded `.app` is not
   launchable — quarantine + exec bit). This is a change **inside a third-party
   package we pin by version**.
3. **`_headless.make_virtual_display()` to stop raising on darwin.** One line
   (`("win32", "darwin")`), reachable at launch through both the sync and async
   entry points. Note the cocoa cloak patch this depends on is *still in the
   upstream source* (§2) — so this is re-enabling a path whose engine-side half
   was never removed.
4. **A guard so this cannot recur silently.** Nothing in persona today asserts
   "the driver we pin supports the platforms we ship". §4 is the proof that the
   existing baseline guard does not cover it.

Item 2 is the one with no cheap answer, and it is the item the ticket's plan did
not contain. Three routes exist, in descending order of how much they depend on
someone else:

- **(a) Upstream reversal.** The removal is one revertible commit and the owner's
  stated reason was a decision, not a technical obstacle. Cheapest if it lands;
  entirely outside our control.
- **(b) A pinned fork of `invisible_core` + `invisible_playwright`.** We already
  pin `invisible_playwright` by git sha (`pyproject.toml:102`), so the mechanism
  exists. Cost is ongoing: we would carry a four-point patch across every future
  engine bump, on a package whose author is actively removing this platform.
- **(c) A local seal + `binary_path=` bypass on darwin.** `ensure_binary()`'s
  refusal is only on the *download* path; persona already passes
  `binary_path=_binary_path_override()` (`invisible_launch.py:1493`), and a local
  seal is a documented, supported state. But §1.2's launch-time
  `make_virtual_display()` raise is **not** bypassable this way, so (c) is
  incomplete on its own and still needs item 3.

**None of these is "produce the asset and we're done."** That is the finding.

---

## 6. What was NOT established, and why

Stated rather than estimated, per the ticket's own standard.

- **Whether upstream would accept a mac-restoring PR.** Not asked — that is a
  human conversation, not a measurement.
- **Whether a mac build from the current `stealth/151` tree actually passes the
  drive gate.** Not run: it needs a macOS runner this container does not have,
  and ~2 h. The prior runs passing (§3.2) is evidence, not proof, that today's
  tree still builds — commits have landed since.
- **Whether the cocoa cloak still works on FF151.** The patch is present (§2);
  it has not been exercised since 2026-08-26, and upstream's own
  `verify-cloak.yml` header says so explicitly: *"le gambe mac sono uscite, quindi
  il cloak cocoa NON viene piu' esercitato da nessuna parte."* Untested code, not
  known-broken code.
- **The exact per-leg cost on a `stealth/151` HEAD build.** The §3.2 numbers are
  from firefox-18/19/20 builds of the same branch family; a tree that has moved
  can build slower.

---

## 7. Scope

Per the ticket: the Chromium engine, the Personium self-build, the canvas
readback movement blocking the current bump, and Windows are all out of scope and
were not investigated. Nothing here proposes dropping macOS — the owner's
2026-09-03 instruction is that a negative finding is a report, and §2/§3 say the
platform is recoverable regardless.
