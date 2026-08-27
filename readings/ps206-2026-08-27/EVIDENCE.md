# PS-206 — a second tab reporting "unable to connect": what was driven, and what it excluded

**Date:** 2026-08-27
**Platform driven:** Linux (this fleet). **The owner's platform, Windows, was NOT measured — see "Not reachable".**
**Engine:** `invisible_playwright` 0.7.3 + the packaged build `firefox-20_151.0_20260817150018`
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

## Two rules the harness had to enforce on itself

Both were learned by this harness producing a **wrong answer first**, and both are now enforced in code. This is PS-14 in both directions: distrust an instrument reporting failure, *and* one reporting success.

**1. Pre-flight every probe host.** An early run printed `*** REPRODUCED ***`. It was false. The probe host `api.my-ip.io` is simply **dead through this proxy** — `curl` gets 0/5 with Firefox nowhere in the picture. A dead probe host reads at the assertion site exactly like the product defect being hunted. The host was removed and a pre-flight added. (`openstreetmap.org` is dead through this proxy too — same TLS EOF under `curl` — which explains one other tab failure that was *not* a product finding.)

**2. The injected fault must be proven to bite.** The first fault-injection run had every tab riding **one pooled keep-alive connection to one host**, so the break never reached a tab: the tab opened "during the outage" loaded happily and the run went green **while measuring nothing**. Now each tab loads a **distinct host**, and a green "tab during outage" **aborts the run as INVALID** rather than reporting "did not reproduce".

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
- The shipped launch never pins `network.proxy.failover_direct`, while the verify harness (`verify/browser_tier.py:194`) does.

Neither was shown to cause this symptom. The `--pin-failover` flag exists on the harness so the second one can be measured directly if it is ever suspected again.

**The most valuable next step is a reading from the owner's own Windows machine**, which this harness is written to be run on unchanged.
