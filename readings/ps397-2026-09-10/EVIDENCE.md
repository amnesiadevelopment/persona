# PS-397 — does `navigator.languages` disagree between the bare engine and persona?

**Taken:** 2026-09-10, in the worker container.
**Tree:** `d08376d` (`origin/main` at the time of the reading, tree clean).
**Instrument:** `scripts/ps397_languages_reading.py` (committed), guarded by
`tests/test_ps397_languages_native.py`.
**Raw record:** `reading.json` beside this file — every arm, its full argv, its
per-realm reading and the wire headers it produced.

---

## The answer in one paragraph

⭐ **`navigator.languages` does NOT disagree. This slice is EMPTY, and that is a
real result.** persona has no `navigator.languages` override on the Chromium
track *at all* — not in the engine patches, not in the eleven masking
extensions — and the value the product presents is already the engine's own,
produced by upstream Chromium's `--lang`/`--accept-lang` handling. There is no
JS override to delete and no native read site to write, because the value was
never spoofed in JS in the first place. **Porting it would move a spoof that
does not exist, and the whole locale family already agrees at a non-host
locale.** PS-312, PS-327 and PS-330 are the precedents for a measurement slice
that correctly shipped nothing; this is a fourth.

---

## §0 — Which of the ticket's three shapes this is

The ticket named two precedents and asked which shape `languages` is. **It is
neither, and that is the finding.**

| shape | precedent | `languages`? |
|---|---|---|
| switch declared/externed/forwarded, **no read site** | the screen trio (`fingerprint-screen-width`/`-height`/`-device-scale-factor`, declared in `000`, consumed nowhere) | ❌ no — no switch is declared |
| read site exists but **hardcodes a constant** | `005`, `+  return 8;` for deviceMemory | ❌ no — no read site exists |
| ⭐ **no native surface exists, and none is needed** | — | ✅ **this** |

**Measured, not read off a summary:**

```
$ for p in engine/patches/fingerprint/*.patch; do
    echo "$(grep -ci 'lang\|locale\|accept' "$p") $p"; done
0 000-add-fingerprint-switches.patch
0 001-disable-runtime.enable.patch
0 002-user-agent-fingerprint.patch
0 003-audio-fingerprint.patch
0 005-hardware-concurrency-fingerprint.patch
0 006-font-fingerprint.patch
0 007-shadow-root.patch
0 009-webdriver.patch
0 010-headless.patch
0 011-gpu-info.patch
0 012-canvas-get-image-data.patch
0 013-canvas-toDataURL.patch
0 014-client-rects.patch
0 015-canvas-measure-text.patch
0 016-webgl-readPixels.patch
0 018-timezone.patch
```

**Zero hits across all sixteen patches.** No `kFingerprintLanguage`, no read
site, nothing to disable.

⭐ **That zero is what makes the question answerable in a container at all**, and
it is why this slice did not need the liaison's Windows host the way slices 1–3
did. The mechanism under test is *upstream Chromium's own* `--lang` /
`--accept-lang` handling — code that is byte-identical in stock chromium and in
persona's fork **because no patch in the series touches it**.

⚠️ **That is a BOUND, not a licence.** What a stock chromium establishes here is
the behaviour of the `--lang`/`--accept-lang` mechanism and of persona's own
extension layer loaded on top of it. **No row in `reading.json` may be read as a
statement about persona's engine patches**, and the record carries that sentence
on its own `bound` key so it travels with the data.

---

## §1 — The JS-override census

The ticket's standing rejection criterion is *"native value in, JS override
out, same PR"*. **On the Chromium track there is no JS override to remove.**

**`languages` is defined in exactly one place in the whole tree**, and it is the
**Firefox** arm:

```
src/services/browser/invisible_launch.py:694
    "def('languages', Object.freeze(LS.slice()));"
```

That is inside `_language_override_script`, which the **Firefox** launch path
installs. The Chromium extension builders — `device_ext`, `gpu_ext`,
`native_ext`, `locale_ext`, `mobile_ext`, `worker_wrap`, `stealth_ext`,
`voice_ext`, `webgl_ext`, `audio_ext`, `canvas_ctx_ext`, `measuretext_ext`,
`geo_ext` — define it **nowhere**.

⭐ **And that was verified by BUILDING the layer, not by grepping the builders.**
A grep of source files can miss a value assembled at build time; this walks the
bytes that actually reach the browser:

```python
dirs, report = build_chromium_layer(d, 12345, os_type="windows", include_geo=True)
# 11 extension dirs; every .js and .json under them scanned
```

⚠️ **CORRECTION, added on review — that census covered 11 of the 13 extensions
`spawn_browser` builds, not all of them.** `build_chromium_layer` is a DESKTOP
checker tier and excludes `build_search_extension` and `build_mobile_extension`
by its own stated decision. The finding is unchanged (both were re-scanned and
neither defines `languages`), but the census as originally run did not cover
them, and the regression fence that borrowed the same harness inherited the same
hole — a worker-realm `G.navigator` override planted in `mobile_ext` was NOT
seen. `tests/test_ps397_languages_native.py` now scans the product's own set and
derives its completeness oracle BY AST from `spawn_browser`, so the coverage
claim is checked rather than asserted. See §6 bound 6.

Result — the **only** locale-family hits in the entire built layer:

| file | keyword | count |
|---|---|---|
| `.persona-voice-ext/voices.js` | `navigator.language` | 1 |
| `.persona-voice-ext/voices.js` | `'language'` | 1 |

Both are `voice_ext` *reading* `navigator.language` to pick a matching speech
voice, and an `Intl.DisplayNames({type:'language'})` call. **Neither defines
`languages`, and neither writes to any navigator property.**

Re-scanned on review across `search_ext` (manifest only, emits no `.js`) and
**both** arms of `mobile_ext` (Android and iOS emit different scripts): **zero
additional hits.** The census table above is unchanged by the widening.

`locale_ext` — the extension whose entire job is the locale — pins **Intl, Date
and Number only**, and says so in its own docstring: *"fingerprint-chromium
leaves the Intl default at the host locale regardless of `--lang`; this closes
that gap."* It deliberately does not touch `navigator.languages`, because
`--lang` already handles it.

---

## §2 — The reading

Seven arms, one binary (`Chromium 152.0.7977.82`, the same 152 line as the
published engine), one page, one run. Every arm's full argv is in
`reading.json`.

⛔ **The control is derived from the live process, never from a remembered flag
list.** `persona_locale_argv()` imports `services.browser.process`, reads the
`--lang`/`--accept-lang` expressions out of `spawn_browser`'s **own source**,
and **refuses** if they no longer match rather than substituting a literal. The
ticket's standing rule exists because a stale capture reintroduced a forced
device scale and an uncapped window and contaminated the liaison's first
reading; a silent fallback here would be the same failure.

| arm | flags | host `LANG` | page | worker | iframe | Accept-Language (wire) | Intl |
|---|---|---|---|---|---|---|---|
| `bare` | *(none)* | *(container)* | `["en-US","en"]` | `["en-US","en"]` | `["en-US","en"]` | `en-US,en;q=0.9` | `en-US` |
| `persona_flags` | `--lang=en-US --accept-lang=en-US,en` | *(container)* | `["en-US","en"]` | `["en-US","en"]` | `["en-US","en"]` | `en-US,en;q=0.9` | `en-US` |
| **`persona_full_layer`** | persona flags **+ all 11 extensions** | *(container)* | `["en-US","en"]` | `["en-US","en"]` | `["en-US","en"]` | `en-US,en;q=0.9` | `en-US` |
| `persona_full_layer_pl` | pl-PL flags **+ all 11 extensions** | *(container)* | `["pl-PL","pl"]` | `["pl-PL","pl"]` | `["pl-PL","pl"]` | `pl-PL,pl;q=0.9` | `pl-PL` |
| `bare_moved_host` | *(none)* | `de_DE.UTF-8` | `["en-US","en"]` | `["en-US","en"]` | `["en-US","en"]` | `en-US,en;q=0.9` | `en-US` |
| `persona_flags_pl_on_de_host` | `--lang=pl-PL --accept-lang=pl-PL,pl` | `de_DE.UTF-8` | `["pl-PL","pl"]` | `["pl-PL","pl"]` | `["pl-PL","pl"]` | `pl-PL,pl;q=0.9` | `en-US` |
| `reveal_control` | persona flags **+ a deliberate override** | *(container)* | `["zz-ZZ","zz"]` | `["en-US","en"]` | `["en-US","en"]` | `en-US,en;q=0.9` | `en-US` |

**`bare_vs_persona_disagree: false`. `full_layer_vs_bare_disagree: false`.**

The descriptor reading, on every persona arm:

```
own_descriptor:       null                                     (no own property on the instance)
proto_getter_source:  "function get languages() { [native code] }"
```

**That is what a stock browser looks like.** There is no accessor to detect,
because persona installed none.

---

## §3 — The controls, and why the null means anything

⛔ **A null result is exactly what a dead instrument produces**, so each of the
three gates below was made to fire before the null was accepted.

### 1. The channel gate — `1+1`

Every arm proves its eval channel with `1+1 === 2` **before a single locale row
is read**, and the harness **refuses to emit** an arm whose gate did not pass. A
browser that never started answers "absent" for every row, which is
byte-identical to a perfect match.

### 2. ⭐ The dead-knob gate — `knob_proved_live: true`

*"They agree"* is worthless if the flag does nothing: **a dead knob produces a
confident false negative that looks exactly like a pass.** Proved live by
`persona_flags_pl_on_de_host`:

```
--lang=pl-PL --accept-lang=pl-PL,pl   →   page   ["pl-PL","pl"]
                                          worker ["pl-PL","pl"]
                                          iframe ["pl-PL","pl"]
                                          wire   pl-PL,pl;q=0.9
```

**persona's own flag expression moved every realm AND the wire.** So the
agreement in §2 is an agreement between two live mechanisms, not two dead ones.

⚠️ **One leg of this control did NOT fire, and it is an instrument limit rather
than a finding.** `bare_moved_host` was meant to show the bare arm *tracking*
the host locale; it did not move. **`locale -a` in this container lists only
`C`, `C.utf8` and `POSIX`** — `de_DE.UTF-8` does not exist here to be tracked,
so chromium fell back. That is a fact about the container, and reporting it as a
failed control would be reporting an instrument limit as a result. The proof
above does not depend on it: `control_flags_override_host` alone establishes the
mechanism is live.

### 3. ⭐ The reveal gate — `probe_proved_live: true`

**A descriptor probe that reports "clean" unconditionally is byte-identical to a
browser that genuinely is clean**, so every "no override" row in §2 means
nothing until the probe is shown to *see* one. `reveal_control` installs an
override deliberately — the Firefox arm's own shape, the only `languages`
override that exists anywhere in this tree — and the probe reads it:

```
languages:            ["zz-ZZ","zz"]
proto_getter_source:  "function () { return Object.freeze(['zz-ZZ', 'zz']); }"
```

No `[native code]`. **The probe can distinguish a spoofed getter from a native
one**, which is what licenses reading `null` / `[native code]` on the persona
arms as a real absence.

### ⭐ And the reveal arm reproduced the ticket's own worker trap, unasked

`reveal_reproduces_worker_split: true`:

```
page:   ["zz-ZZ","zz"]     <- the Navigator.prototype override
worker: ["en-US","en"]     <- WorkerNavigator, untouched
```

**A page-realm override leaves the worker reading the engine's value** — the
exact page/worker disagreement the ticket cites as this port's recurring trap
(slice 2's `G.navigator` deviceMemory). It is reproduced here **on this engine**
rather than cited from another slice, and it is the strongest argument against
porting: **an override is what CREATES that split, and persona currently has
none.**

---

## §4 — Coherence: the whole locale surface, read where a leak is visible

⛔ **The ticket's central warning:** a `languages` that disagrees with
`navigator.language`, the `Accept-Language` header, or the Intl/locale family is
a **new tell, worse than the one removed**.

⚠️ **It cannot be checked at `en-US` on this host, and checking it there would
be the PS-124 failure repeated.** This container's locale *is* en-US, so at
en-US every surface reads en-US **whether it is being pinned or merely leaking
the host** — the two are indistinguishable and the check agrees by construction.
So the coherence arm runs at **`pl-PL`**, a locale the host is not, which gives
each surface something to disagree about.

`persona_full_layer_pl` — persona's real flags **and** its real eleven-extension
layer, at `pl-PL`, every surface judged against the **declared locale** rather
than against each other:

| surface | value | matches declared `pl-PL`? |
|---|---|---|
| `navigator.languages` (page) | `["pl-PL","pl"]` | ✅ |
| `navigator.languages` (**worker**) | `["pl-PL","pl"]` | ✅ |
| `navigator.languages` (**iframe**) | `["pl-PL","pl"]` | ✅ |
| `navigator.language` | `"pl-PL"` | ✅ |
| `Accept-Language` **on the wire** | `pl-PL,pl;q=0.9` | ✅ |
| `Intl.DateTimeFormat().resolvedOptions().locale` | `"pl-PL"` | ✅ |
| own descriptor on `navigator` | `null` | ✅ (native) |
| prototype getter | `function get languages() { [native code] }` | ✅ (native) |

**Every surface of the locale family agrees, in every realm, at a locale that is
not the host's.**

⚠️ **The header is read OFF THE WIRE, not off the kwarg.** PS-124 cost three
rounds because every seat read `kwargs["locale"]` — the string persona hands
*to* the engine — and split it themselves, so the two channels agreed by
construction while the browser contradicted itself. Here a local capture server
records the `Accept-Language` the browser **actually sent**, on five separate
requests (document, `img`, `script`, worker script, favicon), all downstream of
whatever the engine does to the flag.

### ⛔ Two things this section does NOT claim

1. **The timezone leg is NOT asserted coherent.** `Intl...timeZone` reads `UTC`
   on every arm because this container is UTC and **no `--timezone` flag was
   passed** — that flag is persona's, and this harness deliberately does not
   present a surface the product did not. So the tz row is *unmeasured here*,
   not *verified*.
2. ⚠️ **PS-358's known incoherence is untouched and NOT contradicted.** An
   INLINE socks5 proxy launches with a US timezone and `en-US` even when the
   exit is in Poland, because only named/store proxies resolve exit geography.
   That is a different axis, it is explicitly out of scope, and **nothing here
   asserts the locale family is coherent under an inline proxy.** What is shown
   is narrower and stated as such: *given a declared locale, every locale
   surface presents that locale, in every realm.* Whether the declared locale is
   the RIGHT one for the exit is PS-358's question, not this one.

---

## §5 — What this slice ships, and why it is not a patch

⛔ **Do not port a value that is already correct.** The ticket's own instruction,
and it applies:

- **Native value in?** It already is. `--lang`/`--accept-lang` are passed at
  launch by `process.py:1261-1262`, and upstream Chromium — untouched by all
  sixteen patches — produces `navigator.languages` from them, in the page, the
  worker and child frames alike.
- **JS override out?** There is none to remove on this engine. The only
  `languages` override in the tree is the Firefox arm's, which is a different
  engine, is out of this port's scope, and is **load-bearing there** (PS-124
  measured firefox reporting the *host* locale without it).
- **Would porting help?** No — it would **create** the disagreement it claims to
  remove. §3's reveal arm shows that installing a page-realm override is
  precisely what splits the page from the worker.

**Ten minutes of the liaison's host and a rebuild would be spent to change
nothing observable.** That is the cost the ticket asks to avoid.

---

## §6 — Honest bounds

1. **Stock chromium is the instrument, not persona's engine.** Legitimate for
   *this* question only, on the §0 grounds. `persona-152` is not installed in
   this container (`~/.persona/engine/` holds `builds.json` and the firefox-20
   build only), and no row here speaks about the patched engine's behaviour on
   any other surface.
2. **The de_DE host-tracking leg could not run** — only `C`/`POSIX` locales are
   generated here. Recorded in §3 rather than dropped.
3. **No proxy is in the picture.** Every arm runs against `127.0.0.1` with no
   exit, so nothing here speaks to exit-derived locale selection — which is
   PS-358's axis and is left alone deliberately.
4. **The timezone flag was not presented**, so the tz row is unmeasured (§4).
5. **Headless.** `--headless=new` was used; persona's own launch is headed. The
   locale mechanism has no headless-conditional path in upstream Chromium, and
   the same waiver is applied **identically to every arm**, so it cannot author
   a *difference* between them — and a difference is the entire subject.
6. ⚠️ **Every arm ran a DESKTOP profile, so no row here speaks to a mobile
   profile's layer.** A mobile profile is a genuinely different extension set —
   `process.py`'s `spawn_browser` builds `mobile_ext` **in place of**
   `device_ext` for one — and none of the seven arms launched with it. The §1
   census *was* widened on review to scan both arms of `mobile_ext`'s built
   bytes (zero hits), so the **JS-override half** of the finding now covers the
   mobile layer; the **live-reading half** does not. If `navigator.languages`
   is ever questioned on a mobile profile, §2 and §4 must be re-taken there —
   the extension census alone does not answer it. Recorded because the
   regression fence originally inherited this exact blind spot silently, and a
   bound that is not written down is one the next reader has to rediscover.
7. **`search_ext` is scanned but emits no `.js`** — it is a manifest-only
   settings override. It is in the scanned set anyway, because "it cannot carry
   an override" is precisely the reasoning that was wrong about `mobile_ext`.

---

## §7 — The exact expected reading, for FALSIFICATION on the host

⛔ **Required deliverable on every port slice: state the reading BEFORE pushing,
so the liaison can try to FALSIFY it rather than confirm it.**

**Prediction.** On the owner's Windows host, launching persona's **real**
Chromium product (`persona-152`, headed, full layer) for a profile whose
declared locale is `L` (e.g. `en-US`), and reading in **all three realms**:

| surface | predicted value |
|---|---|
| `navigator.languages` — page | `[L, base(L)]` — e.g. `["en-US","en"]` |
| `navigator.languages` — **Web Worker** | **identical to the page** |
| `navigator.languages` — about:blank iframe | **identical to the page** |
| `navigator.language` | `L` |
| `Accept-Language` on the wire | `L,base(L);q=0.9` |
| `Object.getOwnPropertyDescriptor(navigator,'languages')` | **`undefined`** |
| `Function.prototype.toString` on the prototype getter | **contains `[native code]`** |

**How to falsify it — any ONE of these refutes the slice and reopens it:**

1. **The descriptor is not `undefined`** on `navigator`, or the prototype getter
   stringifies to anything other than `[native code]`. → persona *is* overriding
   it somewhere I did not find, and the slice is a real port after all.
2. **The worker disagrees with the page.** → the trap fired on the product, and
   something realm-specific is in play.
3. **`navigator.languages` disagrees with the `Accept-Language` header** read
   off the wire (not off the kwarg). → the coherence claim is wrong.
4. **`languages` does not track the declared locale** — launch two profiles with
   different declared locales and get the same list, or the host's list. → the
   flag is not doing the work I attribute to it on Windows.

⭐ **The sharpest single check, if there is time for only one** — it needs no
comparison run and takes one line in the console:

```js
[Object.getOwnPropertyDescriptor(navigator, 'languages'),
 Function.prototype.toString.call(
   Object.getOwnPropertyDescriptor(Navigator.prototype, 'languages').get)]
```

**Predicted: `[undefined, "function get languages() { [native code] }"]`.**
Anything else refutes the slice.

⚠️ **And the negative prediction, which is the one that matters for the badge:**
this slice changes **no bytes on any launch path**, so the pixelscan verdict
**must not move**. If a verdict *does* move after merging this PR, something
other than this PR moved it. PS-363 already established there are at least two
independent contributors to the `inconsistent` + `masking detected` badge and
attributed one of them to the **engine**.
