# PS-409 — does gating `measuretext_ext` on the engine version work, on both arms, with the full extension set?

**Taken:** 2026-09-11 ~03:45–03:58Z, in the worker container.
**Tree:** `41486a1` (`origin/main`) plus this branch's change, tree otherwise clean.
**Instrument:** `scripts/ps409_gate_reading.py` (committed beside this file).
**Engines under test — two, both real:**

| arm engine | provenance |
|---|---|
| **shipped** (unfixed) | `personium-152.0.7977.75-linux-x86_64.AppImage`, downloaded from the release. **sha256 `6ddb7bbe…0bb4c3` — byte-identical to `engine/releases/personium-152.0.7977.75.json`'s committed digest**, so this is the binary an operator actually has. |
| **fixed** | `ps218-patched-binary-152.0.7977.75-1` from `engine-trial-build.yml` run **34513928710**, head sha `828cc99`, packaged `2026-09-10T19:47:36Z` (its own `PROVENANCE.txt`). The same artefact PS-406 read. |

**Raw records:** `reading.txt`, `reading.json`, `falsification-inverted-gate.txt`,
`falsification-neutered.txt` beside this file.

---

## The answer in one paragraph

⭐ **All four acceptance items are satisfied, and every number was read off a
page in a launched browser rather than off argv or off a ticket.** The gate
installs the repair on the engine in the field (10 extensions, widths 15.83 /
56.47 / 298.55 against a DOM reference of 298.41 — ratio **1.0005**) and omits it
entirely on an engine carrying the PS-345 fix (9 extensions, identical widths
from the engine alone — ratio **1.0000**). ⛔ **But acceptance item 2's stated
evidence does not discriminate on Chromium, and §3 corrects that premise rather
than quietly passing it.**

---

## §1 — The four arms, and why two of them are controls

Every arm runs the **product's own `spawn_browser`**, unmodified. Nothing in
`src/` is patched and no gate function is stubbed: each arm writes a real
`ENGINE_DIR/version.txt`, puts a real AppImage at `ENGINE_DIR/fpchrome.AppImage`,
writes a real `engine-policy.json`, and launches in a **child process** so the
product's config module reads those records at import time. The only things that
move between arms are the two facts a real install carries.

| arm | engine | version.txt | threshold | gate says |
|---|---|---|---|---|
| **A** | shipped (unfixed) | `152.0.7977.75` | `152.0.7977.75.1` | install |
| **B** | fixed | `152.0.7977.75.1` | `152.0.7977.75.1` | **omit** |
| **C** | **fixed** | `152.0.7977.75` | `152.0.7977.75.1` | install |
| **D** | **shipped** | `152.0.7977.75` | `152.0.7977.75` | **omit** |

⭐ **A and B alone would not have established anything.** They differ in two
things at once — the binary and the gate's answer — so a difference between them
is attributable to either. **C holds the BINARY fixed and moves only the GATE; D
holds the gate's direction and moves only the binary.** Between them the
extension's presence is attributed to the gate's decision and to nothing else.

⛔ **D is also the falsification**: the broken engine with the repair omitted is
exactly what the STRICT failure would ship, and it reads the defect raw.

## §2 — The readings

```
                              extensions  ratio(canvas/DOM)   native width getter on the return value
A  shipped, gate ON     10 (+measuretext)        1.00049383   threw: TypeError      <- the repair FIRED
B  fixed,   gate OFF     9                       1.00000231   56.437630476408515
C  fixed,   gate ON     10 (+measuretext)        1.00000231   56.437630476408515
D  shipped, gate OFF     9                       0.00000231   0.00013047640851775392

DOM reference width (un-noised, via getBoundingClientRect): 298.4062
A  'W'=15.82812500  'Sheet1'=56.46537037  probe=298.55361111
B  'W'=15.82034907  'Sheet1'=56.43763048  probe=298.40693988
C  'W'=15.82034907  'Sheet1'=56.43763048  probe=298.40693988   (byte-identical to B)
D  'W'= 0.00003657  'Sheet1'= 0.00013048  probe=  0.00068988   <- ~1e-6, Sheets broken
```

⭐ **Arm D is the cost of getting this wrong in the strict direction, measured.**
A layout engine asked to place a label 0.0007 px wide lays every glyph at the
same place — which is exactly the Sheets failure `measuretext_ext`'s header
describes.

⭐ **Arms B and C are BYTE-IDENTICAL on all three widths.** That is the ticket's
central claim, measured: on a fixed engine the wrapper changes **nothing** a page
can read, so omitting it costs nothing. It also independently reproduces PR
#327's Arm N shape (14/14 identical) on Chromium rather than on Gecko.

## §3 — ⛔ THE READING CORRECTED THE TICKET. Acceptance 2's named evidence does not discriminate.

PS-409's acceptance item 2 names two facts as the evidence that no wrapper is present:

> `Function.prototype.toString` on `measureText` reads `[native code]`, and
> `getOwnPropertyNames` shows no added marker.

⛔ **Measured, BOTH ARE TRUE ON ALL FOUR ARMS** — including arm A, where the
repair is on the command line and demonstrably repairing:

```
every arm:  toString          = 'function measureText() { [native code] }'
every arm:  getOwnPropertyNames(measureText) = ['length', 'name']
every arm:  length=1   name='measureText'   hasPrototype=false
every arm:  Function.prototype.toString itself also reads [native code]
```

**The cause is in the tree and is not a defect: PS-368.** That ticket gave every
Chromium leaf its own closure-WeakMap toString cloak. `measuretext_ext` splices
`chromium_leaf_cloak_js` into its body, re-houses the wrapper in a method
shorthand (so it owns no `prototype`), pins `length` and `name` to the native
values, and registers the source-text name in a **WeakMap** rather than as an own
property — explicitly so `Object.getOwnPropertyNames` reads two names and not
three. The wrapper is doing precisely what PS-368 built it to do.

⭐ **So where did the ticket's premise come from? Its own evidence says so: PR
#327's Arm N, on FIREFOX.** That arm installed this repair on a stock Gecko and
watched `masking.measureText` stop reading `[native code]` — **and that is true
there**, because the Firefox path installs no such cloak. ⛔ **The premise was
carried from Gecko to Chromium, and Chromium is the target.** The ticket is right
that the wrapper buys nothing on a fixed engine; it is wrong about which surface
sees it.

### ⭐ What DOES discriminate — found by measuring, not assumed

The repair returns a **`Proxy`** over the native `TextMetrics` when it fires. A
native accessor invoked with a Proxy as its receiver throws `TypeError` (the
getter needs an internal slot the proxy lacks), so:

```js
Object.getOwnPropertyDescriptor(TextMetrics.prototype, 'width').get.call(m)
```

answers a number on B, C and D and **throws on A**. One line, no timing, no
statistics. ⚠️ **That makes the tell this ticket removes SHARPER than the
stringification it was written around** — which strengthens the case for the gate
rather than weakening it. Two weaker signals were also checked and do **not**
discriminate: the proxy forwards `getPrototypeOf` (so `instanceof TextMetrics`
holds on every arm), and `getOwnPropertyNames` on the metrics object reads `[]`
on every arm (`width` is a prototype accessor, not an own property).

### ⚠️ And one honest bound on that tell, stated because it weakens the ticket's claim

The Proxy is only reachable **when the repair FIRES**, which on an unfixed engine
is every call with text. On a **fixed** engine the `corrupt` guard refuses and
the wrapper returns the native object untouched — **so a wrapper left installed
on a fixed engine is invisible to this probe too.** The instruction to OMIT
rather than neuter still stands on its own terms (an installed extension is a
file on disk, a content script injected into every frame, and an entry in
surfaces this probe does not read), but ⛔ **the measured cost of leaving it is
"a wrapper no probe in this reading can see", not "an observable tell".** That is
a weaker claim than the ticket makes, and it is recorded as such rather than
glossed.

## §4 — ⭐ The guard has been SEEN to fail, twice, in two different ways

A check nobody has watched go red is not evidence. Both mutations were made in
the **product**, not in the instrument, and both are committed here.

**(a) `falsification-inverted-gate.txt` — the STRICT failure.** The gate inverted
(`if not measuretext_repair_required()`), which is the direction this ticket names
as far worse. The instrument refuses the arm outright:

```
RuntimeError: A-shipped-gated-on: expected 10 extensions, got 9:
  ['audio','canvas-ctx','device','gpu','locale','native','stealth','voice','webgl']
exit 1
```

⭐ It halts **at argv**, before reading a page — which is the right place: a
reading taken with a short extension set is a reading of something that is not
the product (PS-391), and the refusal names the missing count rather than
producing a plausible-looking table.

**(b) `falsification-neutered.txt` — the WRONG FIX the ticket forbids by name.**
`measuretext_ext`'s `corrupt` guard forced to `false`, so the wrapper installs and
returns early — *"omit, do not neuter"*. The instrument reads the page and goes
red on the three verdicts that matter:

```
FAIL  AC1/repair-PRESENT-and-FIRING-on-unfixed
FAIL  AC1/geometry-is-sheets-shaped
FAIL  CLAIM/omitting-on-unfixed-would-BREAK-SHEETS
exit 1
```

⭐ **Note which ones stayed green: both of acceptance 2's named facts, and the
no-op control.** A neutered wrapper satisfies `[native code]` and
`['length','name']` perfectly — so (b) is a second, independent demonstration of
§3's finding: had this reading checked only the criterion as written, the
forbidden fix would have passed.

## §5 — Acceptance, item by item

| item | state |
|---|---|
| 1. On an engine WITHOUT the fix, the repair is installed and functions; Sheets-shaped geometry works | ⭐ **SATISFIED** — arm A: installed, Proxy tell reachable, ratio 1.0005 |
| 2. On an engine WITH the fix, no wrapper at all | ⭐ **SATISFIED** — arm B: no `measuretext` extension, no Proxy, `[native code]`, `['length','name']`. ⚠️ **with §3's correction to what that evidence can show** |
| 3. Both arms MEASURED; version read from what is actually installed | ⭐ **SATISFIED** — each arm writes a real `version.txt` and a real AppImage; the product is unmodified and runs in a child process |
| 4. Verified with the full extension set loaded | ⭐ **SATISFIED in substance** — see the bound below |

### ⚠️ On item 4's arithmetic, stated rather than glossed

The criterion says "the FULL twelve-extension set". **This reading carries TEN on
the install arms and NINE on the omit arms**, and the number was read off the
product rather than counted from source (the instrument's first revision asserted
eleven and the product answered ten — which is why it is a refusal and not a
comment). The three absences are the product's own documented conditions for a
**direct desktop Linux** profile, not gaps in the reading:

* `search` — `not _platform.IS_LINUX`, and `masking_layer` excludes it from the
  masking layer in its own words ("a settings override, not masking");
* `mobile` — a desktop profile takes the `device` branch instead (branches of one
  `if`, never two gates);
* `geo` — `proxy`, and this container has no exit (PS-406 established the owner's
  exits are not reachable from here).

⭐ **What PS-391 established is that a ONE-extension reading proves nothing,
because these leaves compose** — and the composition that actually bears on this
question is `native_ext`, which patches `Function.prototype.toString` for the
whole layer and is present on every arm. §3's finding is a direct consequence of
reading the full set: in isolation, the cloak's interaction would have been
invisible.

## §6 — ⚠️ What this reading does NOT establish

* ⛔ **LINUX only.** No Windows engine carries the fix and no Windows compile
  venue exists (PS-406, PS-390). Both fixes are platform-neutral, so this settles
  *"does the gate discriminate"* — not *"do Windows users have it"*.
* ⛔ **No checker, no proxy, no pixelscan.** This touches neither the masking
  badge nor Invariant #0, and makes no claim in either direction about them.
* ⚠️ **HEADLESS.** persona's own launch is headed and this container has no Xvfb,
  so `--headless=new`, `--no-sandbox` and `--disable-dev-shm-usage` are appended
  to the product's argv. All three are flags persona never passes; all three are
  stated on every arm's record. What they could plausibly move is the rendering
  path — the facts read here are a function's source text, an own-property list,
  and a receiver check, which are properties of the JS realm the content script
  installs into. The product's own software-rendering flags
  (`--use-gl=angle --use-angle=swiftshader`) are left exactly as they are;
  `--disable-gpu` is deliberately NOT added, because adding it made the engine
  never publish `DevToolsActivePort` at all.
* ⚠️ **The threshold used here is a TEST threshold.** `152.0.7977.75.1` is the tag
  PS-406 proposes, not one that has been published. The committed default in
  `engine/policy.py` is empty, so **on every engine in the field today the
  product's behaviour is byte-identical to before this change** — the gate is
  correct and inert until a fixed engine is released. That is the sequencing
  dependency the confirm review named, and it is why the threshold is a
  configurable constant with the release obligation written above it rather than
  a value guessed in advance.

---

## §7 — ⛔ ROUND 2: the escape hatch this reading could not reach, and why no arm here can

**Nothing in §1–§6 is re-measured or withdrawn.** The gate, the arms and the
instrument are unchanged and the code review re-ran the seven mutation claims and
reproduced each one. What follows is a defect in the *policy lookup beside* the
gate — ⚠️ **structurally invisible to this reading**, and recorded here so the
next reader does not conclude that four measured arms covered it.

### The defect

`engine/policy.measuretext_fix_min_version()` resolved **every** unusable
operator override — an absent key, a corrupt file, `true`, a bare int, an
explicitly-emptied string — to the committed default. That is the pattern
`known_bad_versions` uses, and it is safe **there** because that function's local
entries "only ever ADD": an unusable override can leave a build *blocked* and
nothing worse. ⛔ **This value's committed default REMOVES a repair**, so the same
fallback is the STRICT direction — the one this ticket's own bounds name as far
worse — the moment the constant is non-empty.

Two claims were false in that state, both of them *promises to an operator*:

* the docstring's "a threshold that is absent, malformed or unreadable must
  leave the repair **INSTALLED**";
* `process.py`'s launch log, which told an operator whose canvas geometry broke
  to **clear** `measuretext_fix_min_version` to restore the repair. ⛔ Clearing a
  key is byte-indistinguishable from never having written one, so that landed on
  the committed default that was withholding it. **The one recovery action the
  product names was a no-op.** The only value that would have worked is an
  absurdly high threshold, which nothing names and nobody would guess.

### ⚠️ WHY NO ARM OF THIS READING COULD SEE IT — the bound worth carrying forward

**Arms A–D each set the threshold EXPLICITLY through a well-formed policy file**
(`scripts/ps409_gate_reading.py` writes `{"measuretext_fix_min_version": <tag>}`
per arm), so **not one of them ever takes the fallback branch**. And the
committed constant is `""`, which makes the fallback's *value* identical to the
fail-open answer — so the defect is inert today and becomes live **only on the
release-day edit this same change mandates.** A reading whose every arm supplies
the input under test cannot measure what happens when the input is absent; that
is not a flaw in the instrument's execution but in its coverage, and it is the
reason the fix is fenced by tests rather than by a fifth arm.

### The fix, and what fences it

`_local_policy_entry()` keeps **ABSENT** (no file, or a file with no such key)
distinguishable from **UNREADABLE** and from a present-but-unusable value. Only
ABSENT reaches the committed default; everything else states *no threshold*,
which installs the repair. So an explicitly-present `""` is a real gesture, and
the log line now names **that** gesture rather than deleting the key.

⭐ **Measured, as a mutation battery on the product** — each mutation reverted
after reading, `__pycache__` cleared between runs (a stale `.pyc` produced a
phantom failure during the code review):

| mutation | result |
|---|---|
| the round-1 `.get()` lookup restored (the defect itself) | **12 red**, incl. the behavioural log test |
| `MEASURETEXT_FIX_MIN_VERSION = "152.0.7977.75.1"` — **the release-day edit, verbatim** | ⭐ **exactly 1 red**, the release tripwire, by design (round 1: **7 red**, six of which were correct-about-intent assertions whose shortest path to green was to delete them) |
| log line reverted to "clear the key" (mechanism left fixed) | 1 red — the behavioural test performs the printed instruction |
| `_ABSENT` collapsed into the malformed arm (silence read as a gesture) | 3 red |
| `_UNREADABLE` folded into `_ABSENT` (a corrupt file read as silence) | 3 red |
| `data.get(key) or _ABSENT` (an explicit `null`/`""` collapsing to absent) | 5 red |
| type check coerced to `str(raw)` | 2 red — incl. the bare-int row, exactly as reported in round 1 |
| the other two readers switched to the narrowed lookup ("consistency" refactor) | 1 red — the divergence is asserted, not left to a comment |

The behavioural fence is
`test_the_omission_log_names_a_remediation_THAT_ACTUALLY_WORKS`
(`tests/test_engine_masking_matrix.py`): it **parses the sentence the product
actually logged**, performs the gesture that sentence names, relaunches, and
asserts the repair came back — with the committed default stubbed to a released
tag, because with it empty both arms pass for free. ⚠️ **That stub is the whole
point of the test**: it is the only way to ask this question before release day.

### ⚠️ And the release tripwire is now singular, deliberately

`test_an_unset_threshold_is_not_read_as_every_engine_being_fixed` still pins the
constant and still goes red on release day — that is its job. The six
malformed-override rows no longer do: they asserted the *fallback value* (`== ""`)
and now assert the *behaviour they are named for* against a **non-empty**
committed default, which is the state that makes the question non-vacuous. ⛔ Under
the old oracle those rows passed while the code did the opposite of what they
claimed, and release day would have turned them red with the shortest path to
green being to edit six correct assertions — silently removing the hatch.

### Non-blocking, also corrected

`process.py`'s function-local `..engine` import carried a comment asserting a
cycle through `browser/__init__` and that it matched "every other browser→engine
reference in this package". ⛔ **Both halves are false** — verified by hoisting it
to module scope: the package imports fine (`engine/policy` imports only
`core.config` and `engine_naming`; `browser/__init__` exports only
`BrowserLauncher`), and it was the *only* function-local `..engine` import in the
file. The import is now at module scope and the false constraint is gone rather
than restated. ⚠️ The identical sentence still sits on
`installed_chromium_version` (`engine_version.py`), where it predates this change
and refers to `..engine.updater`; it is left alone as out of scope rather than
edited on an unmeasured assumption.
