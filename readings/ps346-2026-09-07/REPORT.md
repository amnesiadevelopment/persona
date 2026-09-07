# PS-346 — is anything we ship signed?

**Date:** 2026-09-07 · **Tree:** `feature/ps346-signing-state` off `main` `ea6fadc`
**Author:** worker seat · **Instrument:** `scripts/ps346_signing_state.py`
**Assets read:** the **PUBLISHED** bytes of all **six** release assets, downloaded
from GitHub. Every sha256 below was recomputed locally and **matches the digest
the GitHub API reports** — these are the bytes a user gets.

> **⛔ THIS TICKET MEASURED. IT DID NOT SIGN, AND IT COULD NOT HAVE.**
> No credential was handled, requested, generated or echoed. §4 states the
> options and their prices and **deliberately does not choose between them** —
> every one of them is an owner decision. See [The stop](#the-stop).

---

## The answer, in one line

**Nothing this project produces carries any identity at all — on any of the
three platforms — and the macOS build additionally could not be notarized today
even if a certificate arrived tomorrow.**

That last clause is the finding that was not in the brief. See §3.

---

## 1. What was measured

| asset | kind | sha256 (recomputed = API digest) |
|---|---|---|
| `persona-windows-setup.exe` | app, Inno Setup 6.7.0 | `44382b50…9641` |
| `persona-macos.dmg` | app, UDIF/APFS | `393ba48a…5669` |
| `persona-x86_64.AppImage` | app | `3fb59913…2c5b` |
| `personium-…-windows-x86_64.zip` | engine | `997c7275…5259` |
| `personium-…-macos-arm64.dmg` | engine, UDIF/APFS | `591572b3…70bb` |
| `personium-…-linux-x86_64.AppImage` | engine | `6ddb7bbe…bb4c3` |

**Host:** `Linux 6.8.0-138-generic x86_64`, Debian 13 container.
**`codesign`, `xcrun`, `spctl`, `signtool`, `osslsigncode`, `hdiutil`: all absent.**
Apple's tooling has no Linux port, so every check below is a **direct read of the
on-disk container format** rather than a shell-out. Formats read: PE
`IMAGE_DIRECTORY_ENTRY_SECURITY`; Mach-O `LC_CODE_SIGNATURE` → `SuperBlob`; UDIF
`koly` trailer + resource fork; ELF `.sha256_sig`/`.sig_key` sections. The macOS
leg decompresses the UDZO image and walks the APFS volume (`libfsapfs-python`).

### The instrument is known to be able to say "signed"

A report that everything is unsigned is worthless if the tool cannot report the
opposite. **The positive control fired, and it is not synthetic** — three
genuinely signed items were found *inside our own assets*:

```
persona-macos.dmg   …/playwright/driver/node
    Developer ID Application: Node.js Foundation (HX7739G8FX)
    ← chained to Developer ID Certification Authority ← Apple Root CA
personium-…-windows-x86_64.zip   d3dcompiler_47.dll, dxil.dll
    CN=Microsoft Corporation ← Microsoft Windows Code Signing PCA 2024
```

**Read the identities.** Every one belongs to a **third party whose binary we
redistribute**. Not one artifact *we* produce carries an identity. The contrast
is the measurement: the same parser, on the same pass, over the same files,
prints a real certificate chain for Node.js and Microsoft and prints nothing for
us.

---

## 2. Per-platform state, as measured

### Windows — `UNSIGNED`, no certificate table

```
persona-windows-setup.exe
    IMAGE_DIRECTORY_ENTRY_SECURITY  VirtualAddress=0x0  Size=0
    signify → AuthenticodeVerificationResult.NOT_SIGNED
              "No signature was found."
```

`Size=0` means the file carries **no certificate table**. That is exactly what
Windows sees. Confirmed independently by `signify`, which parses the same
structure and reports `NOT_SIGNED` verbatim.

The engine zip: **10 of 12** PE files unsigned, including every one that matters
— `chrome.exe`, `chrome_proxy.exe`, `chrome.dll` (324 MB), `chrome_elf.dll`,
`notification_helper.exe`. The only two signed are Microsoft's own
redistributables, which arrived signed and were never ours.

### macOS — `ADHOC`, which is **not** signed

This is the leg where the obvious check gives the wrong answer, so state it
precisely:

```
persona.app/Contents/_CodeSignature/CodeResources     ← EXISTS
persona.app/Contents/MacOS/persona                    ← HAS LC_CODE_SIGNATURE
```

Both present. **Both irrelevant.** Inside the `SuperBlob`:

```
slot 0x0      CodeDirectory     CodeDirectory version=0x20400 flags=0x2
                                adhoc=True   identifier='dev.persona.persona'
                                teamID=None
slot 0x10000  CMS blob wrapper  len=8  →  CMS payload = 0 BYTES
```

`flags=0x2` is `CS_ADHOC`. The CMS wrapper exists but is **empty**: 8 bytes of
blob header and no payload. An ad-hoc signature is a self-referential hash tree
with **no certificate, no chain and no identity** — it is what the linker emits
by default on Apple silicon. Gatekeeper treats it as unsigned. `teamID` is
`None` because there is no team.

The `CodeResources` file agrees: every one of its `requirement` strings is a
bare `cdhash H"…"` with **no `anchor apple generic`** clause — i.e. "this
binary hashes to this value", never "this binary came from anyone".

Across the whole app bundle, **113 Mach-O files / 225 arch slices**:

| state | slices |
|---|---|
| `ADHOC` (no identity) | **196** |
| `UNSIGNED` (no `LC_CODE_SIGNATURE` at all) | **28** |
| `SIGNED_CMS` (real identity) | **1** — the vendored Node.js, above |

The 28 fully-unsigned ones are the compiled Python extensions
(`aiohttp/_http_parser.cpython-312-darwin.so` and siblings).

The engine's `Chromium.app`: **11 of 11 slices `ADHOC`**, and it has **no
`_CodeSignature` directory at all** — the app bundle has at least the shape of a
signature; the engine bundle does not.

**Neither `.dmg` is signed at the image level either.** In both, the `koly`
trailer sits **0 bytes** after the end of the XML plist (a signed image carries a
CMS blob in that gap) and the resource fork holds only `blkx` and `plst` — **no
`cSig` resource**.

**No stapled notarization ticket exists anywhere in either image.**

### Linux — `UNSIGNED`, sections reserved and empty

```
persona-x86_64.AppImage           .sha256_sig  1024 bytes  ALL ZERO
                                  .sig_key     8192 bytes  ALL ZERO
personium-…-linux-x86_64.AppImage .sha256_sig  1024 bytes  ALL ZERO
                                  .sig_key     8192 bytes  ALL ZERO
```

appimagetool reserves these sections in every AppImage; ours are empty. Note
the engine AppImage's `.upd_info` is **non-empty** and reads
`gh-releases-zsync|ungoogled-software|ungoogled-chromium-portablelinux|…` —
inherited from upstream's build, and a separate matter from signing.

**Linux is the honest exception on user impact:** there is no OS-level trust
prompt for an AppImage. `chmod +x` and run. The absence of a signature here is
real but costs the user nothing today.

---

## 3. ⭐ The finding that was not in the brief

**The macOS build would be REJECTED BY NOTARIZATION TODAY even with a valid
Developer ID certificate in hand.** Two independent blockers, both read out of
the shipped binary:

```
CodeDirectory flags = 0x2
    adhoc              : True
    runtime (0x10000)  : False     ← HARDENED RUNTIME IS OFF
```

Bundle-wide this reads **`hardened-runtime slices: 1/225`** — and that single
exception is the vendored **Node.js** binary, which arrived that way. **Not one
slice we build has it.** (The instrument reports this as a count and names the
sole exception, precisely so it cannot be misread as "the bundle is hardened".)

```xml
<key>com.apple.security.get-task-allow</key>
<true/>                            ← DEBUG ENTITLEMENT, SHIPPED
```

1. **Hardened runtime is off.** Apple's notary service refuses any submission
   whose executables are not signed with `--options runtime`. This is not a
   warning; it is a hard rejection.
2. **`com.apple.security.get-task-allow` is `true`.** This entitlement lets any
   process attach a debugger to ours. It is a *development* entitlement, it is
   an explicit notarization-rejection condition, and it is **in the shipped
   3.1.1 build** (full plist in
   `artifacts/persona_entitlements.plist`).

**Why this matters for costing the work.** The naive plan is "buy a
certificate, add a `codesign` step, done." That plan is wrong. Buying the
certificate does not get you a notarized build — the `flet build macos` output
must first be re-signed with the hardened runtime and with that entitlement
removed, and `get-task-allow` removal has to be checked against the app actually
still working (this app runs a bundled CPython and a bundled Chromium; JIT and
subprocess behaviour under the hardened runtime is exactly where such builds
break). **The engineering is a real cost item, not a footnote to the purchase.**

---

## 4. What each platform requires — options and prices, not a choice

> **Costs below are the published list prices of the identity programmes and are
> stated so the decision has a number attached. Every row is an owner decision.
> This ticket takes none of them.**

### macOS

| | |
|---|---|
| identity needed | **Apple Developer ID Application** certificate |
| how obtained | Apple Developer Program membership |
| cost | **USD 99 / year**, recurring |
| org requirement | an org membership needs a **D-U-N-S number** and legal-entity verification (weeks of lead time); an individual membership is faster but puts a **person's legal name** on every artifact users inspect |
| process | `codesign --options runtime --timestamp` every Mach-O → `notarytool submit --wait` → `stapler staple` the `.dmg` |
| engineering beyond the purchase | **hardened runtime must be enabled and `get-task-allow` removed** (§3), then the bundled CPython + Chromium re-validated under it |
| CI implication | signing must run on the `macos-latest` runner; the certificate `.p12` and an App Store Connect API key must exist as repository secrets |

### Windows

| | |
|---|---|
| identity needed | **Authenticode code-signing certificate** |
| OV (organisation-validated) | ~**USD 215–400 / year**; requires a verifiable legal entity |
| EV (extended-validation) | ~**USD 280–690 / year**; deeper validation, faster driver-signing eligibility |
| hardware requirement | since **1 June 2023** the CA/B Forum requires **all** code-signing keys — OV *and* EV — to be generated and held on **FIPS 140-2 Level 2 / Common Criteria EAL 4+** hardware. Confirmed independently by DigiCert, GlobalSign and Entrust advisories |
| ⚠️ consequence for CI | **a hardware token cannot be plugged into a GitHub-hosted runner.** Automated signing therefore requires either a cloud signing service (Azure Trusted Signing / DigiCert KeyLocker / SSL.com eSigner — an additional subscription) or a self-hosted runner with the token attached. **This constrains the release pipeline, not just the budget** |
| process | `signtool sign /fd sha256 /tr <timestamp-url> /td sha256` on `persona-windows-setup.exe`, and ideally on the engine's `chrome.exe`/`chrome.dll` before zipping |
| individual certificates | available, but put a **person's legal name** on the publisher line |

> #### ⚠️ Do not buy EV expecting the SmartScreen prompt to disappear
>
> The widely-repeated advice is *"EV grants immediate SmartScreen reputation,
> OV has to earn it."* **That has not been true since 2024**, when Microsoft
> removed the instant-reputation behaviour from its Trusted Root Program
> requirements. Two independent sources confirm it (ToDesktop, 2026-05-10;
> My-SSL, 2026-07-16), and the practical reports only became widespread in
> 2026 because the change took time to bite.
>
> **What this means for the decision:** signing a Windows binary *at all* —
> OV or EV — does **not** remove the prompt for a new publisher. Reputation
> accrues per **file hash** and per publisher identity, so **every new release
> starts fresh** and a freshly-signed installer can still trip SmartScreen.
> Signing is what makes reputation *accruable*; it is not an off switch.
>
> Since the one row that used to justify EV's premium now reads the same in
> both columns, **OV is the defensible default** unless driver signing is
> needed — which for this project it is not. ⚠️ These are 2026 readings of a
> policy Microsoft has changed twice; **confirm with the CA at purchase time
> rather than trusting this paragraph.**

### Linux

| | |
|---|---|
| identity needed | none for execution — **no OS trust prompt exists for an AppImage** |
| optional | AppImage's own `.sha256_sig` / `.sig_key` sections take a **GPG** signature (`appimagetool --sign`). Cost: **zero** — a self-generated key |
| ⚠️ honest caveat | this is **self-asserted**: it proves the same key signed successive releases, not that any authority vouches for the key. Users have no automatic path to verify it. It is a genuine improvement over the current all-zero sections, and it is **not** equivalent to what the other two platforms are buying |

### The recurring total

| scope | recurring |
|---|---|
| macOS only | ~USD 99 / yr |
| Windows OV only | ~USD 215–400 / yr **+ token or cloud-signing subscription** |
| Windows EV only | ~USD 280–690 / yr **+ token or cloud-signing subscription** |
| **both, OV on Windows** | **~USD 315–500 / yr** + signing-service subscription + the macOS re-signing engineering (§3) |
| Linux GPG | 0 |

⚠️ **The sticker price is not the cost.** Three items sit outside it and each
is load-bearing: the **hardware token or cloud-signing subscription** (§Windows
— mandatory since 2023, and it dictates *where CI can run*), the **macOS
re-signing engineering** (§3 — hardened runtime + entitlement removal +
re-validating a bundled interpreter and browser under it), and the fact that
**neither purchase removes the Windows prompt on day one** (§Windows warning).

### What the money actually buys

Stating this plainly, because the naive expectation is "pay, prompt goes away":

- **macOS: yes, essentially.** A Developer ID signature + successful
  notarization + a stapled ticket clears Gatekeeper for a normal download. This
  is the platform where the purchase maps most directly onto the outcome —
  **provided §3's engineering is done first.**
- **Windows: no, not immediately.** It converts "unknown publisher, forever"
  into "known publisher, accruing reputation" — a real and necessary change,
  but the prompt can still appear on new releases. Anyone approving this spend
  should expect that, or the purchase will look like it failed.
- **Linux: nothing is being bought.** GPG signing is free and self-asserted.

---

## 5. What was NOT measured, named rather than inferred

The ticket's falsification clause requires that anything reported about OS
behaviour come from **running it**. Accordingly:

### 5a. The two classes of claim in this report

**Everything in §1–§3 was MEASURED** — read out of the published bytes on this
host, reproducible with the committed script. Nothing there is cited.

**Everything in §4 is CITED, not measured.** Prices and platform policies
cannot be measured from an artifact; they were read from vendor and CA sources
on 2026-09-07 and are labelled as such. The Apple fee is from Apple's own
`developer.apple.com/programs/whats-included/`; the FIPS hardware mandate from
DigiCert, GlobalSign and Entrust advisories (three independent CAs agreeing);
the SmartScreen correction from two independent 2026 sources. **They are
citations with dates, not observations**, and a reader should re-confirm any
figure at purchase time. ⚠️ One of them was **wrong in this report's first
draft** — the EV/SmartScreen claim — and was corrected only because it was
checked rather than recalled. Treat the rest with the same suspicion.

### 5b. Unmeasured platform behaviour

- ⛔ **Gatekeeper's actual on-screen verdict is UNMEASURED.** No macOS host is
  reachable from this fleet. What *is* measured is the input Gatekeeper reads —
  no CMS, no team, no stapled ticket — and that input is unambiguous. But the
  dialog text, and whether it is the "cannot be opened because Apple cannot
  check it for malicious software" refusal or a softer variant on a given
  macOS version, **was not observed and is not claimed here.**
- ⛔ **SmartScreen's actual on-screen verdict is UNMEASURED.** No Windows host
  is reachable. Same shape: the input (no certificate table at all) is measured
  and unambiguous; the rendered prompt is not.
- ⛔ **The quarantine bit is UNMEASURED.** `com.apple.quarantine` is set by the
  *downloading* application, not by us, so it cannot be read from the artifact.
- ✅ **Linux was measured end to end** — no prompt exists to observe, and the
  empty signature sections were read directly.

**Nothing in §2 depends on the unmeasured legs.** "This artifact carries no
certificate" is a fact about the bytes and is fully established. "Therefore the
user sees dialog X" is the inference, and it is flagged as such rather than
dressed up as an observation.

---

## 6. The stop

Per the ticket's own pre-authorisation:

> *If it needs a purchase or an identity decision, **STOP and report** — that is
> a delivered outcome, not a failure.*

**It needs both, on both platforms.** macOS needs a USD 99/yr Apple Developer
Program membership under a chosen legal identity; Windows needs a purchased
certificate **and** a decision about key custody hardware that constrains where
CI can run. No credential exists in this repository, and an agent must not
acquire, hold or place one.

**Wiring was therefore not attempted.** The one piece of the work that is *not*
gated on a purchase — enabling the hardened runtime and dropping
`get-task-allow` — is real, is prerequisite to notarization, and is **left
unstarted deliberately**: it changes the runtime sandbox of a bundled
interpreter and a bundled browser, so it is its own ticket with its own
verification, not a rider on a measurement ticket.

## 7. ⛔ What must not be done with this finding

- **Do not tell users to right-click → Open, or to run
  `xattr -d com.apple.quarantine`.** That is training users to defeat the exact
  protection this report is about. It is not a remedy and it is not a
  workaround; it is the problem, relocated onto the user.
- **Do not read the existing sha256 verification as covering this.** The
  appimagetool pin (`release.yml`) and the fail-closed engine digest
  (`sha256_ok()`) are both good and both answer *"did the bytes change?"*. This
  report answers *"who produced them?"*. Neither substitutes for the other, and
  the project's hash integrity is **unaffected** by everything above.
- **Do not escalate this ticket's priority.** No host fact escapes, no masking
  is weakened, this is not an Invariant #0 leak. It is a distribution-trust and
  user-experience cost. `medium` is correct.

---

## 8. Incidental, found while measuring

`RELEASING.md`'s asset table names the macOS engine asset
`personium-<version>-macos-x86_64.dmg`. The **published** asset is
`personium-152.0.7977.75-macos-**arm64**.dmg`, and
`src/services/engine/updater.py:665` correctly matches `-macos-arm64.dmg`. **The
code is right and the document is stale.** Not fixed here — it is unrelated to
signing and belongs in its own change rather than buried in this one.

---

## Reproducing

```bash
pip install libfsapfs-python signify pefile     # not in requirements.txt
python3 scripts/ps346_signing_state.py --download
```

Exit code is `0` whatever the signing state: this is an **instrument, not a
gate**. An asset it cannot read is reported `UNREADABLE` and is **never**
silently counted as unsigned.

Artifacts: `artifacts/signing_state_run.txt` (full run),
`artifacts/signing_state.json` (machine-readable),
`artifacts/persona_entitlements.plist` (the shipped entitlements, §3).
