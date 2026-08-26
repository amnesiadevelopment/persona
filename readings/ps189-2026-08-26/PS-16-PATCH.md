# PS-16 patch — the linux and macos GPU cells, after PS-189

**Ticket:** PS-189 · **Date:** 2026-08-26 · **Basis:** `origin/main` @ `880fd14`
(rebased from `fa0a5e9`; PS-186 landed in between and its records are now on
`main`. Re-checked after the rebase: PS-16 is at v18, the
"🔴 PS-186 — THAT VERIFICATION WAS WINDOWS-ONLY" section this patch appends to
is present, and every figure below still holds.)
**Evidence:** `readings/ps189-2026-08-26/EVIDENCE.md` (this directory)

PS-16's maintenance rule requires the reading's own ticket to update the
article. **PS-16 is a knowledge-base article, not a file in this repo**, and the
worker seat has no tool that edits one — so this patch is written beside the
records in the form PS-186 established, and the PR flags it for the seat that
can apply it. Applying it is a copy-paste, not a re-derivation: every figure
below was produced by the committed instruments in this directory.

> **Re-derive, never edit-to-match** — the rule's own words. Every number here
> came out of `realm-gpu.json` / `realm-gpu-layer-off.json` via `derive.py` and
> the consistency gate, not out of recollection.

---

## 1. Update the re-derivation date

```
**Last re-derived: 2026-08-26, against `origin/main` @ `880fd14`.**
```

---

## 2. §"🔴 PS-186 — THAT VERIFICATION WAS WINDOWS-ONLY" — append the cause

The table there is **correct and stays**. PS-189 does not revise a single value
in it; it explains *why* those values are what they are. Append:

> ### ✅ PS-189 — THE CAUSE IS ONE DEFECT, AND IT IS A REALM RATHER THAN AN ARM
>
> The macos and linux failures above are **one defect, not two**, settled by
> measurement rather than by argument. The **`ServiceWorkerGlobalScope` is
> authored by NEITHER identity author**, so it falls through to whoever is
> left — the **ENGINE** on an arm the engine spoofs (macos), the **HOST** on an
> arm it does not (linux).
>
> Twelve realms read in **one launch, at one instant**, layer ON, both seeds
> (`readings/ps189-2026-08-26/realm-gpu.json`). Eleven carry the profile's
> authored card; exactly one does not:
>
> | arm / seed | the 11 page-reachable realms | **`service_worker`** |
> |---|---|---|
> | linux / 24601 | `ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)` | **`ANGLE (Google, ... SwiftShader ...)`** |
> | linux / 5150 | `ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2), OpenGL 4.6)` | **the same SwiftShader string** |
> | macos / 24601 | `ANGLE (Apple, ... Apple M1 ...)` | **`... Apple M2 ...`** |
> | macos / 5150 | `ANGLE (Apple, ... Apple M2 Pro ...)` | **`... Apple M4 ...`** |
> | windows / both | engine's AMD `0x1638` / RTX 3060 `0x2560` | *agrees* |
>
> The eleven: `page`, `page_webgl2`, `iframe_same_origin`,
> `iframe_about_blank`, `iframe_srcdoc`, `worker`, `worker_nested`,
> `worker_in_iframe`, `worker_http`, `worker_module`, `shared_worker`.
>
> **These reproduce PS-186's live checker values exactly** — which is what ties
> the loopback mechanism to the live defect instead of to a local artefact.
>
> **Attribution is by CONTROL, not by inspection.** With the layer OFF the
> engine produces exactly the macos service-worker values (`M2` @24601, `M4`
> @5150) in **all twelve** realms — identifying the second author as the engine.
>
> **Mechanism:** `worker_wrap` chains `Worker` and `SharedWorker`
> (`worker_wrap.py:376-377`) — both **constructors the page calls**. A service
> worker is **registered** with the browser and **started by the browser**, so
> there is no construction to intercept, and an MV3 content script does not run
> there. This is the **PS-155/PS-161 failure class on a third axis**: not
> our-fold-vs-engine, not os_type-vs-engine_platform, but the **realm**.
>
> ⚠️ **WINDOWS IS NOT THE CONTROL FOR REALM COVERAGE.** It is clean *because*
> `ENGINE_AUTHORED_IDENTITY_ARMS` stands our layer down there, leaving the
> engine as the single author of every realm. Its green reading is evidence
> about **authorship count**, never about whether our layer reaches a service
> worker — and reading it as the latter is exactly what let this survive PS-161.
>
> **NOT FIXED — and the impossibility is measured, not asserted.** Both
> techniques this codebase relies on were tried and **refused by the browser**:
> a `blob:` SW registration fails (*"URL protocol ... not supported"*) and a
> cross-origin script URL fails with `SecurityError`.
> `ServiceWorkerContainer.prototype.register` **is** patchable
> (`writable`/`configurable`), so a hook exists and **no delivery technique
> does**. The two candidate remedies are product decisions with measured costs:
> deferring macos to the engine trades a contradiction for a **76.9%**
> collision (Level 2), and it **does not help linux at all** — with the layer
> off the engine reports SwiftShader in *every* linux realm, so deferring would
> spread the leak from one realm to twelve. Suppressing SW registration closes
> it and breaks every site that needs one. **Escalated to the owner on PS-189.**

---

## 3. Table 2 — the two cells this ticket owns

The GPU-identity column for these arms currently reads `—` ("never measured").
That is **no longer true**, and a blank here is now the false-green the article
exists to prevent — the arms *have* been read and they *fail*.

| engine / OS / type | GPU identity | basis |
|---|---|---|
| chromium / macos / desktop | **🔴 FAILS — 2 identities in one run** (page realm `M1`/`M2 Pro`, service worker `M2`/`M4`) | measured, PS-186 live + PS-189 realm sweep |
| chromium / linux / desktop | **🔴 FAILS — HOST LEAK** (service worker reports the container's real SwiftShader) | measured, PS-186 live + PS-189 realm sweep |

Keep `chromium / windows / desktop` at **OK**, and keep the standing caveat that
it is OK on the one arm structurally incapable of showing this bug.

**The GPU *unlinkability* column is untouched by this ticket** — those figures
are PS-185's and belong to PS-183. Authorship and pool width are different
questions; do not fold them.

---

## 4. Table 1 — leave the `—`s alone, with one footnote

Rows 2 and 3 (`chromium / macos`, `chromium / linux`) stay `—` in the
per-checker columns: PS-189 took **no** new per-checker scores, and PS-186's
sweep is the reading that fills those. Add a footnote to the GPU discussion
only, so a reader does not infer from a blank that nothing is known:

> The macos and linux GPU rows are **read and failing** (see Table 2 and the
> PS-189 section) even though the per-checker score cells remain blank. **A
> blank score is not a clean score, and here it sits beside a known defect.**

---

## 5. §"What to measure first" — item 3 is partly discharged

Item 3 ("The macOS arm") says *"Both halves of the problem sit in one cell."*
The **authorship** half is now measured on macos and linux. The **unlinkability**
half is not, and stays with PS-183. Suggested edit:

> 3. **The macOS arm.** ~~Both halves of the problem sit in one cell.~~ The
>    **authorship** half is now measured (PS-186 live, PS-189 mechanism): the
>    service-worker realm is unauthored on macos and linux. The
>    **unlinkability** half — macOS at 53.1% against a two-entry pool — is
>    still the worst measured cell and is owned by PS-183.

---

## 6. The two-engine rule — the firefox leg, stated

PS-16's rule requires naming both engines or saying why one is out of scope.

**A `firefox / linux` arm does not exist**, and that is a product fact rather
than an untried cell. `InvisiblePlaywright` has **no OS/platform parameter**
(issue **#211**): Firefox presents **Windows regardless**, which is why PS-186's
firefox records carry `declared_machine_honoured: false` while the chromium
records carry `true`. So the firefox leg of this defect is **not measurable on
linux or macos**, and its two `windows` records are **clean** under the same
gate (exit 0).

**The consequence, which is a genuine blank rather than a pass:** the firefox
service-worker realm has **not been characterised on any non-windows declared
machine**, because no such arm can be produced today. On windows it agrees —
but so does chromium's, for the same reason, so that agreement discriminates
nothing.

---

## 7. One stale citation to fix while you are in there

PS-16 cites `ENGINE_AUTHORED_IDENTITY_ARMS` at **`gpu_ext.py:795`**. It was
already at `:810` when PS-189 opened (the confirm-review comment noted the
15-line drift), and this branch's header additions move it to **`:832`**.

**Prefer the symbol to the line** — `grep -n ENGINE_AUTHORED_IDENTITY_ARMS` —
since this is the second time the same citation has drifted.
