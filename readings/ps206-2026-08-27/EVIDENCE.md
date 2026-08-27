# PS-206 — a second tab reporting "unable to connect": what was driven, and what it excluded

**Date:** 2026-08-27
**Platform driven:** Linux (this fleet). **The owner's platform, Windows, was NOT measured — see "Not reachable".**
**Engine:** `invisible_playwright` 0.7.3 + the packaged build `firefox-20_151.0_20260817150018`
⚠️ **See "A version-drift caveat on this instrument" at the bottom — the measurements below were taken on a stack that DRIFTED from `pyproject.toml`'s pin.**
**Instrument:** `scripts/ps206_second_tab.py`
**Proxy:** a real, authenticated, **rotating backconnect** SOCKS5 gateway (`gw.dataimpulse.com:824`)

---

## Verdict

**The reported symptom did not reproduce on Linux, and the leading mechanism was refuted by measurement rather than left open.**

This is a bounded "did not reproduce", which is what the ticket asked for: below is precisely what was driven, on which platform, with what proxy configuration, and which candidate mechanisms are now excluded *by measurement* rather than by argument.

**The symptom remains unexplained**, and the one place it could still live — Windows — is stated as unmeasured rather than quietly recorded as passing (PS-17).

---

## Why this needed a new instrument

Nothing in the tree ever opens a second tab. Every existing check drives the **first** page through the automation driver, so a defect appearing only in a tab the user opens is invisible to all of it however green it goes.

The harness therefore drives the **packaged engine** through a **real authenticated proxy**, using the product's **own** `_proxy_dict` and the shipped proxied-profile prefs.

---

## What was driven, and what happened

### 1. Baseline — tabs via `ctx.new_page()`, healthy proxy
`5/5` tabs loaded (first ~800 ms, later tabs ~220 ms), all via one exit IP.

### 2. Content-opened tabs — `window.open()` from the live page
`5/5` loaded. This matters because the product's own code (`invisible_launch.py` ~3481) notes that in *this* Firefox `new_page()` opens a whole new **window**; `window.open` is opener-linked and lands as a genuine **tab**, so it exercises the closer path to the user's Ctrl-T.

Each tab came out of a **distinct exit IP** (`178.42.28.106`, `89.66.77.40`, `109.243.148.253`, `46.205.197.43`) — proof these were genuinely **fresh proxy connections per tab**, not one pooled connection reused. That is what makes the negative result meaningful.

### 3. Transient proxy failure — the decisive leg
Hypothesis: Firefox marks a failed proxy bad for `network.proxy.retry_timeout` (~30 min), so an already-loaded page survives on keep-alive while a **new** tab, needing a new connection, reports "unable to connect". A rotating gateway whose exit dies mid-session supplies exactly that transient failure.

Driven through a controllable SOCKS5 shim in front of the real gateway:

| step | result |
|---|---|
| tab1, proxy healthy | OK |
| proxy hop breaks, tab opened during outage | **FAIL — `NS_ERROR_CONNECTION_REFUSED` in 23–33 ms** (the user's exact symptom text) |
| proxy healed, verified **out-of-band** before touching Firefox | reachable |
| **tabs opened after the heal** | **3/3 OK, on fresh connections** |
| original tab reloaded | OK |

**→ The proxy-blacklist / failover-retry hypothesis is REFUTED.** Firefox recovered the instant the proxy did. It did not carry a grudge.

Run with the fault as a well-formed SOCKS5 `0x01` refusal (a gateway that is up with a dead exit) and as an abrupt mid-handshake close (a dead exit). Same outcome.

### 4. Concurrency — could a live tab's sockets starve a new tab?
`47–48 / 48` concurrent held-open SOCKS connections established. **Gateway connection-limit excluded.**

---

## Three rules the harness had to enforce on itself

All three were learned by this harness producing a **wrong answer first**, and all three are now enforced in code. This is PS-14 in both directions: distrust an instrument reporting failure, *and* one reporting success.

**1. Pre-flight every probe host, in every leg.** An early run printed `*** REPRODUCED ***`. It was false. The probe host `api.my-ip.io` is simply **dead through this proxy** — `curl` gets 0/5 with Firefox nowhere in the picture. A dead probe host reads at the assertion site exactly like the product defect being hunted. The host was removed and a pre-flight added. (`openstreetmap.org` is dead through this proxy too — same TLS EOF under `curl` — which explains one other tab failure that was *not* a product finding.)

The pre-flight initially guarded only the fault-injection leg — the one where the false positive had happened. That fixed the instance rather than the class: a dead host in `baseline`, `content` or `concurrency` yields an equally confident and equally false `DEFECT SEEN`, and **two of the five probe hosts were already dead through this proxy**, so on a different machine and a different proxy that is a live risk rather than a theoretical one. **All four legs now pre-flight against the proxy they actually measure through** (`socks_reachable` takes the proxy's coordinates instead of assuming the local shim), and a leg with too few usable hosts aborts as **INVALID** rather than returning a product verdict. `concurrency` pre-flights its single host too, so `established 0/N` now means the gateway refused the concurrency and cannot mean that one host was dead.

**2. The injected fault must be proven to bite — and the heal proven to take.** The first fault-injection run had every tab riding **one pooled keep-alive connection to one host**, so the break never reached a tab: the tab opened "during the outage" loaded happily and the run went green **while measuring nothing**. Now each tab loads a **distinct host**, and a green "tab during outage" **aborts the run as INVALID** rather than reporting "did not reproduce".

The recovery is now checked in the same direction and for the same reason. The upstream is a **rotating backconnect gateway whose real exit can die on its own schedule**, entirely independently of the shim flag the harness clears — so "I set `fail = False`" is not evidence that the network came back. The out-of-band reachability check at step `[3]` was being *printed and discarded*; it is now **acted on**, and a proxy that did not verifiably recover aborts the run as **INVALID** before Firefox is touched. Without that branch, the decisive leg's tabs would fail because **the network was down** and the harness would report it as `*** REPRODUCED ***` — the exact false-positive class this instrument exists to refuse, one step further along than the one that already bit it.

**3. The control tab is a precondition, never a verdict.** The owner's report is *"the first page loads, the tab I open afterwards does not"* — so `tab1` loading is what makes every downstream tab **interpretable**. It is not evidence for or against the defect. Three legs folded it into the verdict anyway, and it broke in **both** directions:

- `leg_failover` computed `tab1_ok` and then gated the reproduction banner on `if tab1_ok and not all(after)`. When the control failed, the `and` short-circuited and control fell through to `return True` — so a **totally broken run** printed `tabs after heal: 0/3 OK` and `Not reproduced` three lines apart and exited `0`. That is the **false all-clear**, and it is the worse of the two directions: a false alarm gets investigated, a false all-clear closes the last open line of inquiry on this ticket.
- `leg_baseline` and `leg_content` scored `all(results)` with `tab1` inside `results`, so a run where the control failed and **every second tab loaded** reported `DEFECT SEEN` — the exact opposite of what was measured.

All three now score the control **separately**: a failing control returns `None` (**INVALID run**), and the verdict is computed only over the second-and-later tabs that the question is actually about.

This is **not theoretical, and pre-flight cannot close it**, because pre-flight measures a *different layer* than the legs assert on. `socks_reachable` proves the **SOCKS tunnel opens** (`s.connect((host, 443))`); the legs assert on a **full HTTPS page load**. A host that accepts the CONNECT and then fails TLS sits exactly in that gap — and this very document records one on this very proxy: `openstreetmap.org`, *same TLS EOF under `curl`*. Such a host **passes pre-flight and fails the tab**. Rule 1 narrows the gap; only rule 3 stops it from becoming a verdict.

A fourth defect of the same class was found while fixing these, by driving rather than reading: with `tab1` removed from `results`, a `--tabs 1` run would have scored `all([]) == True` — a **vacuous pass** reporting "no defect seen" having never opened the tab in question. `leg_baseline` now refuses a run with fewer than two tabs.

---

## An instrument limit that is not a product defect

Repeated runs eventually died with a node `EPIPE`, and one run stalled opening a third tab — which superficially resembles the PS-171 "stalls on the third tab" finding.

**It is this container's memory ceiling, not the product:**

- `memory.max` = 2048 MiB, `memory.peak` = 2048 MiB — the ceiling was reached exactly;
- `oom_kill 0`, but `memory.events: max` climbed into the thousands (hard allocation stalls);
- cgroup usage read **1995 MiB / 2048 MiB** immediately before a failing tab;
- leaked `Xvfb` processes from earlier runs were holding the memory; after killing them, usage fell to ~977 MiB;
- while at the ceiling, the container killed the **shell itself**, not just Firefox.

Five tabs succeed cleanly when the container starts with memory free. **This must not be reported as a third-tab product defect.**

---

## Not reachable from here — stated, not passed off as verified

**Windows is unmeasured, and the owner's symptom is a Windows symptom.**

`needs_fork_launch()` returns true **only on Linux**, so Windows takes the **thread** launch path and records pid `0`. Any reasoning that depends on the process tree does not transfer between the two platforms. There is no Windows host on this fleet.

Per PS-17, that is recorded as **not covered, with the reason** — never as a pass.

---

## What this leaves open, and who owns what

Excluded **by measurement** here: proxy blacklist / failover-retry; gateway connection limit; connection starvation; per-tab proxy connection failure on a healthy proxy.

Already excluded by the ticket: the peer-ownership guard (no certificate assigned ⇒ no `PeerGate` in this profile's Firefox path).

**Still `PS-217`'s, and untouched here:**
- `_proxy_dict`'s regex recognises **only** `socks5://`; any other scheme falls to `{"server": url}` and **silently drops credentials**, which an authenticated proxy would refuse.
- The shipped launch never pins `network.proxy.failover_direct`, while the verify harness (`src/services/verify/browser_tier.py:194`) does.

Neither was shown to cause this symptom. The `--pin-failover` flag exists on the harness so the second one can be measured directly if it is ever suspected again.

**The most valuable next step is a reading from the owner's own Windows machine**, which this harness is written to be run on unchanged.

---

## A version-drift caveat on this instrument — found by me, after the measurements

**Every measurement above was taken on an engine stack that had drifted from this repository's own pin.** I am recording it rather than quietly re-running, because the drift is exactly the class of instrument fault this project has been bitten by before, and a reader deserves to know which stack produced these numbers.

`pyproject.toml:102` pins `invisible_playwright` to an **exact commit** (`353df4f…`) with `invisible_core==20.14.0`, and the comment there says why: it is *"the commit the test suite is green against"*, pinned so a supply-chain push cannot land unverified.

I installed the package **bare** (`pip install invisible_playwright`), which resolved:

| | I measured on | `pyproject.toml` pins |
|---|---|---|
| `invisible_playwright` | 0.7.3 | commit `353df4f…` (= **0.7.1**) |
| `invisible_core` | 20.16.0 | **20.14.0** |
| `playwright` (driver) | 1.62.0 | **1.61.0** |

**Why this matters and is not pedantry:** the driver is the thing that speaks to the browser, and this fork exists *precisely because* stock Playwright cannot do SOCKS5 auth. Measuring proxy behaviour through a driver two versions off production is measuring a slightly different product. The project's own memory corpus already carries this rule twice, and I did not follow it.

**What I did about it:** installed the pinned stack (verified in place: `invisible_playwright 0.7.1`, `invisible_core 20.14.0`, `playwright 1.61.0`) and started a re-run of the baseline leg. It got:

```
tab1 first  [api.ipify.org] OK status=200 exit='109.243.64.65' 628ms
tab2 opened [ifconfig.me  ] OK status=200 exit='83.175.179.83' 654ms
```

— consistent with the drifted-stack result, then stalled at the **container's 2 GB ceiling** (the same instrument limit documented above; memory reached 1823 MiB and the runner was reclaimed). **So the pinned-stack re-run is INCOMPLETE: two tabs of five.**

### The honest status of the verdict

- **Unchanged in direction, and independently corroborated where it matters most.** The `NS_ERROR_CONNECTION_REFUSED` symptom, the fault-injection legs and the concurrency leg all exercise the **proxy hop**, and the two pinned-stack tabs behaved identically to the drifted ones.
- **But "5/5 tabs load on the pinned stack" is NOT something this reading has established.** Only 2/5 were re-measured before the ceiling.
- **The exclusions therefore carry a version caveat**, and anyone re-opening this should re-run `scripts/ps206_second_tab.py all` on the pinned stack — ideally on a host without a 2 GB cap — before treating them as settled.

**Install the pin, never bare:**

```
pip install "invisible_playwright @ git+https://github.com/feder-cr/invisible_playwright.git@353df4faac4fb202cc4d836c46d981855ecf1bd9" "invisible_core==20.14.0"
python3 -m invisible_playwright fetch     # NB: the verb is `fetch`, not `install`
```

This does not change the ticket's outcome — Windows remains the unmeasured platform and the symptom remains unexplained — but it does mean the Linux "did not reproduce" is bounded by **one more caveat than I originally claimed**, and saying so is worth more than a tidier record.
