# Driving persona's UI — what presses the button, and what it can reach

**Ticket:** PS-71. **Measured at** `1c765c7` on the Linux agent container, 2026-08-22.

This document answers one question: *can anything in this project press a
button, and if so, how far does it reach?* It is a **measurement, not a
promise**. Every claim below was produced by running the thing; where a surface
could not be driven, that is recorded as **not covered, with the reason**,
never approximated by a weaker check standing in for it.

---

## The answer

**Yes.** A real control in a real running persona can be pressed, and its
effect observed in the product's own state. The mechanism is committed as
`tests/ui_driver/` and demonstrated on one real path in `tests/test_ui_driven.py`.

The route that works is the one worth trying first, because it reuses what
already exists rather than inventing: flet serves the app over the web, which
puts a real browser in front of it, and this project already drives a real
browser with playwright.

```
ft.run(App._main, view=WEB_BROWSER)  ->  chromium  ->  semantics tree  ->  click
                                                                            |
                          observed through the SERVICE LAYER  <-------------+
```

### The part that is not obvious

Flutter web paints to **canvaskit** — a canvas. Point a browser at the served
app and the DOM is 32 elements with an empty `innerText`. No buttons, no
fields, nothing addressable. **A driver that stopped here would correctly
conclude the UI is undrivable, and would be wrong.**

What makes it drivable is Flutter's **semantics (accessibility) tree**, which
mirrors every widget into real DOM — `<flt-semantics role="button">` with real
text and real bounding boxes. It ships **dormant**, behind one element:

```html
<flt-semantics-placeholder role="button" aria-label="Enable accessibility">
```

That placeholder has a trap in it. It is positioned at **(-1, -1, 1, 1)** —
deliberately off-viewport, so a screen reader finds it and a user never does.
Playwright's `click()` therefore *refuses* it (`Element is outside of the
viewport`), and `force=True` does not help either. The activation path that
works is dispatching the pointer sequence onto the element directly:

```js
for (const t of ['pointerdown','pointerup','click'])
  el.dispatchEvent(new PointerEvent(t, {bubbles:true, cancelable:true, ...}));
el.click();
```

Measured effect: **32 → 39 DOM elements, 0 → 1 real `<input>`**, and every
button becomes addressable by the text a user reads.

### What it does not do

It adds **nothing to the shipped application** — no test ids, no debug
affordances, no hooks, no production branch. It serves the real `App._main`,
the same callable `App.run()` hands to flet, and finds controls by their
visible labels. That constraint is deliberate (it is out of scope for this
ticket to make the UI testable) and it is also the honest one: a control that
cannot be found by its visible label cannot be described by a user either.

---

## The demonstration

One narrow path with a known defect history — **creating a profile** — driven
through the actual controls:

`[ + new ]` → type into the real name field → `[ create ]`

The assertion does **not** read the screen. A repaint can flip text; only the
product's own persisted state proves the handler behind the control ran. The
test reads the profile back through `ProfileManager` against the same isolated
`PERSONA_HOME` the served app writes to.

```
service layer BEFORE: []
service layer AFTER : ['ps71-driven-profile']
```

### Shown failing

A UI driver observed only on a working screen has not been observed. This is
the class where a test that always passes is easiest to write and hardest to
notice, so the wiring was broken **on purpose** — `[ create ]` rendered
normally but disconnected from its handler — and the same path re-run:

| build | `[ create ]` present? | driven test |
|---|---|---|
| unmodified | yes | **green** |
| `on_click` removed | yes, still renders and depresses | **RED** |

```
AssertionError: pressing [ create ] did not create the profile.
Service layer holds [].
```

This is the defect a handler-level unit test **cannot** see: the handler it
calls directly is untouched and still perfectly correct. The break is carried
as `_UNWIRE_CREATE_BUTTON` in the test file, applied only inside the served
child, and verified surgical (it fires on `[ create ]` and leaves `[ save ]`
wired).

**Re-run against the real source, not just the served child.** The committed
break above monkeypatches `ft.Button` inside the served subprocess, which
proves the driver reacts but not that it would catch a defect in the shipped
file. So `on_click=on_submit` was deleted from
`src/ui/dialogs/profile.py:955` **itself** and the driven tests re-run:

```
AssertionError: the profile was not created at all. Service layer holds []
FAILED test_creating_a_profile_through_the_controls_persists_it
FAILED test_typing_into_the_multiline_field_reaches_the_saved_product
2 failed in 87.43s
```

Restored afterwards; `git status src/` clean. That is the real thing going red
on a real break in real production code.

**The text-field claim was falsified the same way.** A reach claim needs its
own negative control, or it is prose. `_FIELD_SELECTOR` was reverted to the
original buggy `"input"` and the two field tests re-run:

| driver | census sees | field tests |
|---|---|---|
| `"input, textarea"` | 3 fields (`INPUT`, `INPUT`, `TEXTAREA`) | **green** |
| `"input"` (the shipped bug) | 2 fields — Notes silently absent | **RED** |

```
AssertionError: no text field labelled 'optional'. Fields on screen:
["single-line label='e.g. Amazon US Shopper' value=''",
 "single-line label='shopping, us, amazon' value=''"]
```

This matters more than it looks. The first version of this document called
text fields reachable while the driver could not reach the multiline one, and
**nothing failed** — the map was wrong and every test was green. These two
tests are what make that specific silence impossible now.

---

## The reach map

Measured by walking the real UI and recording what the semantics tree exposes.

### Reachable

| Surface | Evidence |
|---|---|
| Sidebar navigation | `profiles`, `network`, `bookmarks`, `tags`, `certificates`, `connect`, `trash` — all press and switch the page |
| Top-bar actions | `[ + new ]`, `[ import ]`, `[ export ]`, `[ wipe all ]` |
| Per-page primary actions | `[ + add proxy ]`, `[ + add bookmark ]`, `[ + add certificate ]`, `[ + add host ]`, `[ empty trash ]`, `[ assign to selected ]`, `[ make pool from selected ]`, `[ edit ]` |
| Modal dialogs | `Create New Profile` opens with `[ create ]`, `[ cancel ]`, `[ + proxy ]`, `[ bulk ]` addressable |
| Text fields — single-line | Real `<input>`; typing round-trips (`'ps71-driven-profile'` reached the saved profile) |
| Text fields — multiline | Real `<textarea>`; typing round-trips (`'driven-notes-through-the-textarea'` reached the saved profile's `notes`). Reachable **only because the driver queries `input, textarea`** — see the correction note below |
| Onboarding | `Skip` / `Next` |
| Modal scoping | Correct — while onboarding is up, *only* `Skip`/`Next` are addressable. The driver cannot reach behind a modal, which matches what a user can do |

> **Correction note — this row was wrong in the first version of this
> document, and the way it was wrong is worth keeping.** It read *"Text
> fields | Real `<input>`; typing round-trips"*, while `FletDriver.type_into`
> queried `page.locator("input")` only. Flutter web backs a **multiline**
> field with a `<textarea>`, not an `<input>`, so the Notes field in the very
> dialog this document demonstrates was **silently unreachable** while the map
> called text fields reachable. Measured in that dialog:
>
> ```
> locator('input')           -> 2      # name, tags
> locator('textarea')        -> 1      # notes   <-- invisible to type_into
> locator('input, textarea') -> 3
> ```
>
> Two things follow, and the second is why this note exists rather than a
> silent edit. The fix is one line (`_FIELD_SELECTOR = "input, textarea"`).
> But a *prose* claim about reach is exactly what failed here — so the claim
> is now **asserted by a test**: `test_every_visible_text_field_is_reachable_including_the_multiline_one`
> pins the census by TAG (`INPUT`, `INPUT`, `TEXTAREA`), and
> `test_typing_into_the_multiline_field_reaches_the_saved_product` drives the
> `<textarea>` and reads the value back out of `ProfileManager`. If a future
> flet backs a field with something new, those fail and this row gets
> corrected instead of quietly drifting out of true.

### NOT reachable — recorded with the reason

> Per the standing directive these are recorded as **not covered, with the
> reason**. None is approximated by a weaker check.

**1. `ft.Dropdown` options — the most consequential limit.**
The create-profile dialog carries seven dropdowns (the source declares proxy,
certificate, OS, engine, resolution, search and pool). Two of them open and
reveal a single placeholder option — `(direct)` and `(none)`, i.e. the
"nothing selected" entries, not a real choice between values. **The other five
reveal nothing**, and
this is not a viewport problem — re-measured in a 1500px-tall window with every
control fully on screen, the result was identical. Both routes were tried:

- mouse press at the control's own centre — no options enter the tree
- focus + `ArrowDown` + `Enter` (the standard accessibility path) — no change

The decisive evidence is the service layer, not the tree: after driving the OS
dropdown by keyboard and creating the profile, `os_type` was still `windows`,
the untouched default. `build_os_dropdown` declares five real options
(`windows/macos/linux/android/ios`), so this is a control with a genuine choice
behind it that **neither input route can operate**. Flutter renders the open
menu in an overlay that does not surface as semantics nodes here.

*Consequence:* "every option in creating a profile" — which the directive names
explicitly — is **not reachable by this mechanism today**. See
[Recommendation](#recommendation).

*Second consequence — two text fields inherit this limit.* `custom_w` and
`custom_h` (`src/ui/dialogs/profile.py:299,305`) carry no visibility flag of
their own; they sit inside `custom_row` (`:311`), and it is that enclosing Row
that is declared `visible=res_value == "custom"` (`:313`) and re-toggled in
`on_res_change` (`:318`). So they render only once the **resolution** dropdown
is set to `custom` — and that dropdown is one of the five that cannot be
operated. Measured: the dialog exposes **3** text fields at rest, and after
pressing the resolution control by its displayed value and driving it by
keyboard it still exposes 3; no `custom` node ever enters the tree. So these
two fields are not a gap in the text-field mechanism — typing works fine on
every field that renders — they are **unreachable because the only control
that reveals them is unreachable**. Recorded under the dropdown limit rather
than as a separate text-field limit, because that is where the actual block
is and that is what a fix would have to solve.

**2. Native file dialogs.** `[ import ]` and `[ export ]` press cleanly and
then nothing happens in the tree — measured: 22 controls before, 22 after, zero
new nodes. They are `ft.FilePicker`, an OS-level dialog rendered entirely
outside the Flutter tree. A browser cannot see it and neither can this driver.
The same applies to the cookie `[ import file ]` / `[ export file ]` controls.
**Structurally unreachable, not a gap to close.**

**3. Icon-only controls.** Every page carries buttons with a real box and **no
text** — the Activity Log expander at `(154,776,40,40)`, pagination arrows at
`(744,936)` and `(917,936)`. They are addressable *by coordinate* but not by
label, so a test naming them would be pinned to pixel positions. Recorded as
reachable-but-fragile rather than covered.

**4. Desktop window chrome.** Web mode has no native window, so
`page.window.*` behaviour — sizing, centring, the hidden-window-on-start
sequence, close/minimise — is **not exercised by this route at all**. Anything
that depends on being a desktop window needs a different mechanism.

**5. What web mode changes.** The app under test runs in web mode, not as the
shipped desktop binary. Everything above is evidence about *the UI's wiring*,
which is shared, and is **not** evidence about desktop-specific behaviour.

---

## Cost

Measured, whole committed suite: **5 tests, 200s** — three runs, 200.29s,
200.69s and 200.40s, the last against the committed tree rather than a dirty
working copy. Per test the spread is **31–44s**, averaging **~40s**:

| test | call |
|---|---|
| `test_typing_into_the_multiline_field_reaches_the_saved_product` | 44.3s |
| `test_driven_test_fails_when_the_button_is_unwired` | 42.6s |
| `test_creating_a_profile_through_the_controls_persists_it` | 42.6s |
| `test_every_visible_text_field_is_reachable_including_the_multiline_one` | 39.1s |
| `test_real_controls_are_addressable_in_the_shipped_ui` | 31.4s |

The cost is **dominated by fixed startup, not by the interaction**: the cheapest
test bounds boot-plus-settle (a ~25s app-settle plus browser startup) at ≤31.4s,
and the whole spread above that floor is only ~13s. So a test's price is
essentially the boot, and driving *more* controls within one test is close to
free — which is the shape that matters when sizing downstream work. Each test
boots a real persona against a fresh isolated home, so they are independent and
parallelisable, but they are not free — this is a marked, opt-in tier, not
something to add to the default run.

> **This figure was wrong once, in the direction that matters.** It read
> "3 tests, 117s / 45–60s per test" — accurate when written at `283993d`, and
> left untouched when this branch added two tests, so it described a suite that
> was no longer the committed one. The total understated the real 200.29s by
> 42% (equivalently, the real figure is 71% above the stated 117s), and the
> per-test band was wrong in the *opposite* direction — 45–60s excludes the
> entire measured spread of 31.4–44.3s. Same failure mode as the
> reach-map row corrected above: green, measured-looking, and false. Restated
> here against the committed 5-test suite from a fresh run. If you add a test,
> re-run and update this section — a stale number here is what the next ticket
> sizes against.

Guarded honestly: the tests are marked `ui_driver` and skip with a reason
naming exactly what is missing (`flet not installed`, `chromium not runnable
here`). The capability is registered in `conftest.py`, so on a machine
*declaring* `PERSONA_REQUIRED_CAPABILITIES=ui_driver` a skip becomes a
**failure** rather than a silent green.

> **Provisioning note.** `flet` is declared in `requirements.txt` but was **not
> present** in the dev container; the playwright browser cache carries firefox
> only, so the driver uses the system chromium at `/usr/bin/chromium`
> explicitly. Both halves genuinely have to be provisioned and neither can be
> inferred from the other — which is why they are one capability with two
> guards.

---

## Recommendation

**The route works. Downstream coverage work is viable, with one carve-out.**

Order worth taking, cheapest evidence first:

1. **Button-and-field paths** — profile create/edit, proxy add, bookmark add,
   certificate add, trash empty. **Their buttons and text fields are reachable
   today** — single-line and multiline both, driven and asserted through the
   service layer. Each costs roughly one test at ~40s (see [Cost](#cost) — and
   note the price is almost all fixed boot, so covering several controls inside
   one test costs little more than covering one). Read that as a claim about
   *buttons and text fields only*: any of these dialogs that also carries
   a dropdown is **partially** coverable, because the dropdown options are not
   drivable (item 3). Creating a profile is exactly such a case — the name,
   tags and notes fields are reachable, the OS/engine/resolution choices are
   not. Scope a "cover the create-profile dialog" ticket against that split,
   not against the dialog as a whole.

   *(This item previously read "these are fully reachable today". It was
   inaccurate — multiline fields were unreachable at the time it was written,
   and the dropdown carve-out in item 3 already contradicted the word "fully".
   It is corrected here because this sentence is what the next ticket sizes
   against.)*
2. **Navigation and modal scoping** — cheap, and the modal-scoping behaviour is
   already proven correct.
3. **Dropdown options — do not promise these until the limit is solved.** This
   is the one place where a coverage plan could quietly overstate itself. The
   options are a genuine part of "creating a profile" and they are *not*
   drivable now. Three honest ways forward, in preference order:
   - drive the desktop window directly instead of web mode (see below);
   - investigate whether a newer flet/Flutter emits menu semantics, and pin
     the finding to a version rather than to a hope;
   - accept the limit and cover option-handling through the service layer
     (PS-70's half), recording the *control* as not covered with this reason.
     **Note this is a service-layer check standing beside a UI gap, not a UI
     check** — it must be labelled as such, not counted as button coverage.
4. **Never promise** native file dialogs or window chrome through this route.
   Those need a different mechanism entirely (an OS-level driver), which is a
   separate decision with its own cost.

**Not attempted here, and worth knowing before the next ticket commits:** a
desktop-window driver (Xvfb is installed and a flet desktop window will start
under it). That would address both the dropdown limit and window chrome, but it
means driving Flutter pixels without a DOM — a materially harder problem than
this one, and it should be scoped as its own investigation rather than assumed
to work.

### What the harness looks like

```python
with serve_app(REPO_ROOT) as app, FletDriver(app.url) as drv:
    drv.press("+ new")                      # a real control, by its label
    drv.type_into(0, "my-profile")           # a real <input>
    drv.press("create")
assert "my-profile" in profiles_in(app.home)  # the PRODUCT, not the screen
```

Three primitives — `press`, `type_into`, `controls` — plus `serve_app` for
isolation. `press` raises with a dump of every control on screen when a label
is missing, so a test cannot silently pass on a blank screen.
