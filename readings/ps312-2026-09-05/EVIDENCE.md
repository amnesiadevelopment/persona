# PS-312 — the Firefox geolocation cell, established BY MEASUREMENT

**Date:** 2026-09-05  **Base:** `d658c81`  **Verdict:** the cell moves
`position_not_established` → `not_covered_recorded`. **No spoof ships.**

## The question

`spawn_browser`'s Firefox arm forwards `locale` and `timezone` to the child and
does **not** forward `lat`/`lon`, though `proxy.lat`/`proxy.lon` are in scope
fifty lines above the cfg dict. The Chromium arm installs `build_geo_extension`
for *every* proxied profile, and `geo_ext.py` states the premise in the tree's
own words:

> Rather than let getCurrentPosition fall through to the REAL host coords (a
> "spoofed location" tell — country=DE but coords in the operator's real city,
> audit7 #5), deny permission.

`tests/test_engine_masking_matrix.py` recorded the Firefox cell as
`position_not_established` — *no spoof, and NO RECORDED REASON* — because
nothing in the tree said whether that premise was true, false, or already
handled on this engine. **Nothing was assumed here. It was measured.**

## Environment

| | |
|---|---|
| engine | `firefox-20 · FF 151.0` (`~/.cache/invisible-playwright/firefox-20_151.0_20260817150018/firefox`) |
| `invisible_core` | **20.14.0** (⚠️ the proposal researched 27.17.0 — re-verified on this host) |
| `invisible_playwright` | 0.7.1 |
| display | Xvfb (persona launches **headful**: `"headless": False`) |
| exit | a real SOCKS5 CONNECT relay on loopback; geography written by `ProxyStore.mark_checked(DE, Europe/Berlin, 52.52, 13.405)` |

The engine's two shipped geo prefs were re-read from the wheel **on this host**
and are unchanged from the proposal's reading: `geo.enabled: True`,
`geo.provider.network.url: ""`, and `permissions.default.geo` **not set**.
`greprefs.js` ships the real Google provider URL, so the engine's `""` is an
override that is doing work, not a default.

`_profile_prefs` sets **zero** geo keys — persona's product code contributes
nothing to this posture; it is entirely the engine's.

## Method

`spawn_browser(profile, in_process=True)` → `get_ff_eval(name)["eval"]`, the
route `verify/baseline.py` uses. `in_process=True` is required: the eval hook
is published per-process and Linux launches fork.

Every assertion is on **what the success/error callback receives**.
`getCurrentPosition` is callback-based, so the probe resolves on whichever
callback fires and times out explicitly — a hang is a *recorded outcome*, not
an absent one. Pref overlays are **probe-side** (monkeypatched over
`_profile_prefs`); the product was never modified to take a reading.

## The readings

`four-legs-controls.json` — four separate launches, one profile each.

| leg | proxy | permission | `geo.provider.network.url` | outcome |
|---|---|---|---|---|
| **posctl** | proxied | granted | **patched → local endpoint** | ✅ `position 11.111111, 22.222222` @ 18 ms |
| **granted-stock** | proxied | granted | engine's `""` | `error code=2` @ 12 ms |
| **direct** | none | prompt (shipped) | engine's `""` | `timeout` @ 40001 ms — no callback |
| **direct-granted** | none | granted | engine's `""` | `error code=2` @ 13 ms |

Supporting: `ab-provider-restored.json` (the same A/B on the main probe —
position `41.4041, 2.1741` @ 25 ms, provider logged 2 hits);
`loopback-origin.json` (the reading reproduces on `http://127.0.0.1`, which
`isSecureContext: true` confirms is a secure context — this is what let the
shipped test drop its network dependency).

## What the readings establish

**1. No coordinates reach the page — so there is no leak.** Under the shipped
`prompt` default neither callback fires; under `granted` the page gets
`POSITION_UNAVAILABLE`. Invariant #0 is intact. **No leak is claimed and none
was found.**

**2. The null is a real null, not a null instrument.** The positive control
returns a **sentinel** position (`11.111111, 22.222222` — deliberately neither
the exit's coordinates nor any host value) through *the same channel, page and
JS*. This is the PS-150 rule satisfied by measurement rather than by assertion.

**3. The cause is named and isolated.** `posctl` and `granted-stock` differ in
**exactly one pref**. Patched → position in 18 ms; the engine's `""` →
refusal in 12 ms. ~12 ms is far too fast for a network round trip: the refusal
is **local**, caused by `geo.provider.network.url: ""`.

**4. The refusal generalises off this container.** A `POSITION_UNAVAILABLE`
could have meant "no geoclue in this container" — an artifact that would *not*
hold on an operator's desktop. The A/B rules that out: with a provider
reachable, this same container returns a position immediately.

**5. AC5 — a DIRECT profile is not treated differently.** `direct-granted`
(code 2 @ 13 ms) is indistinguishable from `granted-stock` (code 2 @ 12 ms). No
Firefox geolocation behaviour is conditional on the proxy, which matches
Chromium's `if proxy:` gate in outcome. The direct legs also confirm the
surrounding coherence (`America/New_York` + `en-US`, the forced-US pairing).

## Why NO SPOOF SHIPS

`geo_ext.py`'s premise — that `getCurrentPosition` would fall through to real
host coordinates — **does not hold on this engine**. The concern is already
answered, by a different mechanism than Chromium's extension.

Adding a spoof would install a JS override in front of an engine already
refusing locally: it would replace a native refusal that reads clean
(`Function.prototype.toString` still renders `[native code]`) with an override
a detector can see, to close a hole that is not open. Under Invariant #0 that
is a **net loss**.

**AC3 held:** neither settled engine decision is reversed. `geo.enabled` stays
`True` (an absent `Navigator.prototype.geolocation` is itself a tell);
`permissions.default.geo` stays unset (a `denied` where stock says `prompt` was
measured as the only divergence across 21 permission names). A test pins that
the product ships neither.

## Falsification (AC7), run and recorded

**A — the recorded reason.** Reword the sentence in `process.py`'s Firefox arm
and `test_recorded_reasons_still_in_tree` goes RED, naming the file. So the
cell cannot outlive the reason it cites.

**B — the behaviour, on the value the page received.** Patch the *product* to
set `geo.provider.network.url` at a reachable endpoint — the exact regression
the recorded decision rests on not happening — and three live tests go RED:

```
FAILED test_a_granted_page_receives_NO_COORDINATES
FAILED test_the_shipped_default_never_answers_a_page
FAILED test_a_DIRECT_profile_is_not_treated_differently

AssertionError: a DIRECT (proxy-less) profile received
  {'outcome': 'position', 'lat': 11.111111, 'lon': 22.222222,
   'accuracy': 33, 'elapsed_ms': 22}
```

The failure is on **the payload the page's callback received**, never on a
source-text assertion. The tree was restored and `git diff` confirmed empty.

## Honest bounds

1. **The exit is a real SOCKS5 tunnel on loopback, not a foreign exit.** That
   bounds the *network* half — but the network half is precisely what
   `geo.provider.network.url: ""` closes, and the positive control proves the
   channel works when a provider *is* reachable.
2. **This rests on the ENGINE, not on persona's code.** It is true while the
   engine behaves this way. The `process.py` comment says so, and says to
   re-measure on an engine bump. `test_the_recorded_geo_absence_is_still_an_absence`
   and the live suite are the standing guards.
3. **The live suite SKIPS — never silently passes — without an engine, a
   display or `invisible_playwright`.** An absent engine must not read as a
   clean bill of health.
4. **Frequency is still unmeasured.** How often a site calls
   `getCurrentPosition` on a persona profile is not known; this establishes the
   posture, not the exposure.
