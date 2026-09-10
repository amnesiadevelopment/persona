# PS-406 — does the CI-built engine artefact carry BOTH fixes? And can the proxied gate be run?

**Taken:** 2026-09-10 ~21:00–21:10Z, in the worker container.
**Tree:** `0020020` (`origin/main` at the time of the reading, tree clean).
**Instrument:** `scripts/ps406_artefact_reading.py` (committed beside this file).
**Artefact under test:** `ps218-patched-binary-152.0.7977.75-1`, 366,668,822 bytes,
from **`engine-trial-build.yml` run 34513928710**, head sha `828cc99`, conclusion
`success`, packaged `2026-09-10T19:47:36Z` (its own `PROVENANCE.txt`).
**Raw records:** `reading-seed24601.json`, `reading-seed777.json`, `exit-probe.txt`,
`falsification-arm-b.log` beside this file.

---

## The answer in one paragraph

⭐ **Acceptance items 1 and 2 are SATISFIED, and this reading establishes them
independently rather than inheriting them.** The artefact was downloaded, the
AppImage was executed in this container, and three numbers were read off it over
CDP. ⛔ **Acceptance item 3 is NOT satisfied and could not be attempted**, and
this reading sharpens *why*: the residential exit is not slow, not flickering and
not exhausted — it **rejects SOCKS5 authentication**, and it does so
indistinguishably from a credential that is outright fabricated.

---

## §1 — What was measured, and why each arm exists

Every arm is a differential **within one binary**: the same artefact launched
with `--fingerprint` and then without it. That is deliberate — a control
built from a different tree would confound the patch with the build, and the
flag-OFF arm of the *same* binary attributes any difference to the flag and to
nothing else.

| arm | question | pass condition |
|---|---|---|
| **A** reference render, 15 distinct colours | is the **PS-373 guard** present? | ON and OFF **byte-identical** |
| **B** realistic canvas, ~1900 colours | is the guard **correctly narrow**? | ON and OFF **differ** |
| **C** `measureText` | is the **PS-345 fix** present? | widths **positive AND plausible** |

⭐ **Arm B is not a courtesy and it is the arm most likely to be dropped.** A
guard that was *too wide* — one that suppressed canvas noise everywhere — would
make arm A byte-exact for the wrong reason, while silently disabling masking on
the real fingerprinting canvases the product exists to protect. **Arm A passing
is, on its own, also consistent with the protection being destroyed.** Arm B is
what separates those two worlds.

## §2 — The readings

```
seed 24601                              seed 777
A  ref  ON=b0becdc5 OFF=b0becdc5        A  ref  ON=b0becdc5 OFF=b0becdc5
   modified bytes: 0        GUARD PRESENT   modified bytes: 0        GUARD PRESENT
B  fp   ON=9aa1331d OFF=77b3d0a8        B  fp   ON=62388145 OFF=77b3d0a8
   modified bytes: 13    PROTECTION OK      modified bytes: 15    PROTECTION OK
C  "Personium measureText probe 12345"  C  same probe
     ON = 298.406078  ratio 0.999999         ON = 298.406552  ratio 1.000001
   "W"  ON = 15.820303                     "W"  ON = 15.820328
```

**Against the shipped 3.1.1 engine the ticket reports** — same probe string —
`-0.0006113856500905102`, and `"W"` at `-0.0000346`. ⭐ **The artefact returns
298.41 and 15.82. The impossible value is gone.**

## §3 — ⭐ Why TWO seeds, and why seed 777 specifically

**One seed cannot tell a working flag from an INERT one.** If `--fingerprint`
did nothing at all, arm A would be byte-exact too — for entirely the wrong
reason. The second seed is what closes that:

⭐ **Arm B's hash MOVED with the seed (`9aa1331d` → `62388145`) while arm A
stayed byte-exact at `b0becdc5`.** That is only possible if the flag is live and
the guard is keyed on the render, not on the flag being ignored.

**Seed 777 is not an arbitrary second seed.** PS-345 drew it deliberately because
its noise factor is **positive**: on the broken engine it returns *spec-legal*
widths that are still collapsed by seven orders of magnitude. ⛔ **It is the seed
that defeats a naive "is the width negative?" check** — which is why arm C tests
the **ratio against the same binary's flag-OFF arm** (a healthy factor is centred
on 1) and not the sign. The stored worker memory on PS-345 states the invariant:
*"what condemns is the factor's centre, and the sign is incidental."*

## §4 — ⭐ The guard has been SEEN to fail

A check nobody has watched go red is not evidence. `falsification-arm-b.log`
records this instrument deliberately broken — the ON arm's flag suppressed, so
both arms are identical — and it correctly reports:

```
VERDICT: GUARD TOO WIDE - masking disabled on real canvases
OVERALL: DEFECT — failing arms: B/protection      exit 1
```

**Exit 1 on a defective artefact, exit 0 on a sound one.** The script is a guard,
not a transcript.

---

## §5 — ⛔ Acceptance item 3: the exit REJECTS AUTHENTICATION. This corrects the record.

The standing diagnosis on this ticket is that the exit is **INTERMITTENT** —
*"alive for seconds, dead for minutes"*. ⛔ **That is not what this container
measures, and the difference changes what the owner should check.**

```
gw.dataimpulse.com:824  TCP CONNECT           OK      <- the gateway is UP
SOCKS5 handshake, real credential             User was rejected by the SOCKS5 server (1 2)
21 consecutive attempts over ~4 minutes       every one rejected, none timed out
```

⭐⭐ **THE FALSIFICATION THAT MAKES THIS A DIAGNOSIS RATHER THAN A COMPLAINT.**
The same handshake was run with a deliberately **wrong password**, and with a
**username that does not exist**:

```
real credential          ->  User was rejected by the SOCKS5 server (1 2)
wrong password           ->  User was rejected by the SOCKS5 server (1 2)
nonexistent username     ->  User was rejected by the SOCKS5 server (1 2)
```

⛔ **The real credential is byte-indistinguishable from a fabricated one.** The
project's own canonical prover agrees, naming the layer explicitly:

```
ExitNotProven: 2 provider(s) tried
  https://ipinfo.io/json : SOCKS5AuthError: SOCKS5 authentication failed
  https://ipwho.is/      : SOCKS5AuthError: SOCKS5 authentication failed
```

### ⭐ What this rules IN and OUT — the reason it is worth the owner's minute

| hypothesis | verdict from this reading |
|---|---|
| exit is flickering / intermittent | ⛔ **ruled OUT** — 21/21 rejected, none timed out, over 4 min |
| far end reset the connection | ⛔ **ruled OUT** — TCP connects fine; the refusal is at SOCKS5 auth |
| harness fault | ⛔ **ruled OUT** — `curl` and the project's own prover agree |
| ⭐ **credential dead: plan exhausted, expired, or rotated** | ⭐ **the only survivor** |

⚠️ **A rejection is not a timeout, and the two have different fixes.** An
intermittent exit is waited out; ⭐ **a rejected credential is never waited out —
it needs the owner to look at the DataImpulse account.** The earlier
"intermittent" reading was true when taken (an exit was live at ~19:5xZ); what
this adds is that the failure has since become a **stable auth rejection**, which
is a different fault with a different remedy.

⛔ **This is the owner's account, not our infrastructure.** The ticket already
flags one exit as *"a 1GB residential plan and may simply be EXHAUSTED"* — ⭐ **an
exhausted plan is exactly what a stable auth rejection looks like from here.**

## §6 — ⛔ What was NOT done, and why no substitute was accepted

- ⛔ **No unproxied pixelscan reading was taken and called a verdict.** The ticket's
  own bound governs: *"A run that does not settle is NOT a verdict."* An
  unproxied profile cannot even produce the consistency tile, so it cannot clear
  it. A green badge taken here would have been the most misleading artefact
  available.
- ⛔ **No claim about Windows.** This is a **Linux** artefact. Both fixes are
  platform-neutral, so this settles *"does the series produce the behaviour"* —
  ⛔ it does **not** settle *"do Windows users have it"*, which needs a **build**,
  and there is still no Windows compile lane.
- ⛔ **No Invariant #0 claim in either direction.** Nothing here says a leak is
  open or closed. This reading touches no checker.
- ⚠️ `fp_colours` differ slightly between ON and OFF (1911/1910 vs 1904). That is
  the noise doing its job on a gradient, not an anomaly.

## §7 — Standing correction carried forward from the ticket

⚠️ **Font/`measureText` readings taken earlier today against the shipped engine
were taken against a BROKEN measurement** (`-0.0006`, an impossible value). ⛔ **Do
not delete them — mark them as taken on a binary now known to return negative
widths**, and re-take against this artefact, whose numbers are now on record here.
