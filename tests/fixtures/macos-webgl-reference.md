# macOS WebGL reference — what a real Mac reports

Reference values behind the `macos` arm of `src/services/browser/gpu_ext.py`
(ticket PS-183). This file exists so the constants in that module have a
checkable provenance and the next person does not re-derive them from scratch.

**Read the provenance column before trusting any value.** Every entry below is
labelled either **[upstream]** (a renderer string pasted verbatim into a public
issue report by a real user on real hardware) or **[source]** (derived from
published ANGLE / Chromium source). Neither is a capture taken by this project
on its own hardware — see "What is NOT established" at the bottom.

## Why this pool was widened

`MAC_GPUS` shipped with **two** entries (Apple M1, Apple M2 Pro). Selection is
uniform (see "Selection is uniform" below), so two macOS profiles collided on
the GPU identity **50.0%** of the time — a shared cross-profile identifier, which
is what Level 2 of the project bar (mutual unlinkability) forbids. That was the
worst cell in PS-16's Table 2 by a wide margin (linux 12.5%, windows 15.6%,
android 25.0%).

**Deferring to the engine is not available here and must not be re-proposed.**
It was measured across 30 seeds (`readings/ps161-macos-seeds-2026-08-25/`,
`…-seeds2-…`) and the engine produced only two values — Apple M2 at 87%, Apple
M4 at 13%, a **76.9%** collision, *worse* than the two-entry pool it would have
replaced. `ENGINE_AUTHORED_IDENTITY_ARMS = frozenset({"windows"})` is that
decision in code. The only lever left is widening the pool, which is this file.

## The string shape, proven from ANGLE source

A macOS Chrome runs WebGL over ANGLE's **Metal** backend. The page-visible
`UNMASKED_RENDERER_WEBGL` is composed by ANGLE from three elements, and every
one of them was read out of source rather than pattern-matched from captures:

### 1. The renderer element — `"ANGLE Metal Renderer: " + MTLDevice.name`

[`DisplayMtl::getRendererDescription`](https://chromium.googlesource.com/angle/angle/+/refs/heads/main/src/libANGLE/renderer/metal/DisplayMtl.mm)
(`DisplayMtl.mm:188-202`):

```cpp
std::string DisplayMtl::getRendererDescription()
{
    std::string desc = "ANGLE Metal Renderer";
    if (mMetalDevice) { desc += ": "; desc += mMetalDevice.get().name.UTF8String; }
    return desc;
}
```

So the variable half is **`MTLDevice.name`** — a string Apple's Metal driver
reports, not something ANGLE composes. **That is precisely why every device name
in the table below has to come from a real machine**: it cannot be derived from
source, only observed. An invented or extrapolated chip name is a *positive*
tell on a single profile, which is strictly worse than the collision this pool
widening exists to reduce.

### 2. The vendor element — the literal `Apple`

[`GetVendorString`](https://chromium.googlesource.com/angle/angle/+/refs/heads/main/src/libANGLE/renderer/driver_utils.cpp)
(`driver_utils.cpp:225-234`) maps `VENDOR_ID_APPLE` to the literal `"Apple"`.
`DisplayMtl::getVendorString` (`:204-207`) returns exactly that.

### 3. The version element — the literal `Unspecified Version`

`DisplayMtl::getVersionString` (`:209-216`) returns the compile-time literal
`"Unspecified Version"` whenever `includeFullVersion` is false, with the comment
*"For WebGL contexts it's inappropriate to include any additional version
information, but Chrome requires something to be present here."* WebGL always
takes that branch (`Context.cpp:3697` passes `getBackendVersionString(!isWebGL())`),
so **no macOS version ever reaches the page** and none is baked in below.

`Context::initRendererString` then composes `"ANGLE (" + vendor + ", " +
renderer + ", " + version + ")"`, giving the shape every row below follows:

```
ANGLE (Apple, ANGLE Metal Renderer: <MTLDevice.name>, Unspecified Version)
```

The `UNMASKED_VENDOR_WEBGL` half is EGL's `"Google Inc. (<vendor>)"` convention
(`Display.cpp:2478-2486`) — `Google Inc. (Apple)` — the same convention
`WIN_GPUS` and `LINUX_GPUS` already use.

## Vendor / renderer — the pool

All eleven rows are **[upstream]**: each `MTLDevice.name` below was found inside
the body of a public issue report, embedded in the full renderer string in
exactly the shape above. `captures` counts the distinct issue URLs the string was
found in; the URLs column lists up to three of them.

`UNMASKED_VENDOR_WEBGL` is `Google Inc. (Apple)` for **every** row — Apple is the
only Metal vendor — so it is stated once here rather than repeated per row.

| `MTLDevice.name` | since | captures | Provenance (distinct public issue reports) |
|---|---|---|---|
| `Apple M1` | 0 | 4 | [bevy#18257](https://github.com/bevyengine/bevy/issues/18257), [ruffle#23851](https://github.com/ruffle-rs/ruffle/issues/23851), [ruffle#23990](https://github.com/ruffle-rs/ruffle/issues/23990) |
| `Apple M1 Pro` | 1 | 5 | [ruffle#13043](https://github.com/ruffle-rs/ruffle/issues/13043), [ruffle#14168](https://github.com/ruffle-rs/ruffle/issues/14168), [Construct-bugs#8682](https://github.com/Scirra/Construct-bugs/issues/8682) |
| `Apple M1 Max` | 1 | 4 | [gpu.js#859](https://github.com/gpujs/gpu.js/issues/859), [ruffle#19742](https://github.com/ruffle-rs/ruffle/issues/19742), [million#1080](https://github.com/aidenybai/million/issues/1080) |
| `Apple M2` | 1 | 6 | [CloakBrowser#236](https://github.com/CloakHQ/CloakBrowser/issues/236), [Horizon#852](https://github.com/Fchat-Horizon/Horizon/issues/852), [ruffle#20028](https://github.com/ruffle-rs/ruffle/issues/20028) |
| `Apple M2 Pro` | 0 | 4 | [million#1127](https://github.com/aidenybai/million/issues/1127), [ruffle#15648](https://github.com/ruffle-rs/ruffle/issues/15648), [ruffle#17749](https://github.com/ruffle-rs/ruffle/issues/17749) |
| `Apple M2 Max` | 1 | 1 | [ruffle#20682](https://github.com/ruffle-rs/ruffle/issues/20682) — ⚠️ single capture, see caveat below |
| `Apple M3` | 1 | 3 | [ruffle#21431](https://github.com/ruffle-rs/ruffle/issues/21431), [ruffle#21475](https://github.com/ruffle-rs/ruffle/issues/21475), [Angels-Bandits#30](https://github.com/ajasmbat/Angels-Bandits/pull/30) |
| `Apple M3 Pro` | 1 | 2 | [chroma-key-video#1](https://github.com/kaltura/chroma-key-video/issues/1), [table-saw-project#19](https://github.com/sharkky9/table-saw-project/pull/19) |
| `Apple M3 Max` | 1 | 2 | [donutbrowser#531](https://github.com/zhom/donutbrowser/issues/531), [react-os-shell#94](https://github.com/victorymau/react-os-shell/pull/94) |
| `Apple M4` | 1 | 2 | [ruffle#22068](https://github.com/ruffle-rs/ruffle/issues/22068), [ruffle#24364](https://github.com/ruffle-rs/ruffle/issues/24364) |
| `Apple M4 Pro` | 1 | 2 | [Construct-bugs#9202](https://github.com/Scirra/Construct-bugs/issues/9202), [crucible#1137](https://github.com/foundryvtt/crucible/issues/1137) |

The two `since: 0` rows are the entries that shipped before this widening. They
are listed with provenance they never previously carried: **both were
independently re-confirmed** by this harvest (M1 in 4 reports, M2 Pro in 4), so
the original pool is corroborated rather than merely inherited.

### Why these names and not others

Every name above was **observed**, never extrapolated. Apple's published silicon
line also includes `M1 Ultra`, `M2 Ultra`, `M3 Ultra` and `M4 Max`, and those are
real products — but no verbatim capture of them turned up in this harvest, so
they are **deliberately absent**. Adding a plausible-but-unobserved name is
exactly the failure mode this file exists to prevent: the pool would gain an
entry that no real machine reports, which is a novel tell on every profile that
draws it. Eleven observed entries already clear the target band, so there is no
reason to reach for a twelfth on inference.

⚠️ **Do not recombine terms across rows**, the same rule `LINUX_GPUS` carries.
`MTLDevice.name` is a single opaque string from the driver; "Apple M3 Ultra" is
not constructible by taking "Apple M3" and the "Ultra" from another row.

### ⚠️ Caveat on `Apple M2 Max` — one capture, not several

`Apple M2 Max` rests on a **single** public report, where every other row has at
least two. It is retained because the string is verbatim, well-formed, and names
a chip Apple certainly shipped — but it is the weakest row in the table and is
flagged so that a future reader does not mistake it for the same strength of
evidence as `Apple M2` (six reports). If a second capture is ever seen, record it
here. If this row is ever suspected, dropping it costs little: the pool would go
to ten entries and 10.0% collision, still inside the target band.

## Selection is uniform — checked, not assumed

PS-183 asked for this explicitly, because a pool of *n* selected non-uniformly
does not give a 1/*n* collision, and PS-16's figures had been *counted* rather
than *measured*. The selection path in the emitted `gpu.js` is:

```js
function pick(arr, salt) { var pool = visible(arr); return pool[h32(salt) % pool.length]; }
var GPU = (OS === "ios") ? IOS_GPU : pick(POOL, 0x67900);
```

`h32` avalanche-mixes `SEED ^ salt` (two `Math.imul` rounds with xor-shifts), and
the modulo is taken over the **generation-filtered** pool. The salt is a
constant, but the per-profile entropy enters through `SEED`, so selection is not
degenerate — it is uniform-by-construction.

**Measured rather than argued** (by executing the emitted `gpu.js` under node,
the `_GPU_READ` harness pattern in `tests/test_hardware_generation.py`), 64
seeds, generation 0, on the two-entry pool as it shipped:

```
Apple M1      33  51.6%
Apple M2 Pro  31  48.4%
collision (Simpson) = 50.0%
```

51.6/48.4 over 64 seeds is uniform to within sampling noise, so **PS-16's
"theoretical" basis for the macOS cell is sound** and the 1/*n* arithmetic is
the right model for this pool. That answers PS-183's DoD item 4: selection is
*not* skewed, and the counted figure needed no correction.

## The generation split — why the win does not reach existing profiles

`pick()` divides by the length of the profile's **own generation's** visible
pool. Appending an entry **without** a `since` tag therefore changes the divisor
for profiles that already exist and re-indexes a large share of them onto a
different graphics card — a site holding a live session cookie sees the machine
change under it, which is the linkage event `models/hardware_generation.py`
exists to prevent (measured on this pool at 66-75% moved).

So the nine new rows are tagged `since: 1` and `CURRENT_HARDWARE_GENERATION` was
bumped 0 → 1, per the three-step procedure in that module. The consequence is
deliberate and must be stated rather than glossed:

| generation | visible pool | collision | who |
|---|---|---|---|
| 0 | 2 entries | **50.0%** | every profile that already existed — **unchanged** |
| 1 | 11 entries | **9.1%** | every profile created from now on |

**Existing macOS profiles keep the 50.0% collision.** Closing that gap means
re-rolling or tombstoning already-issued identities, which is the owner-level
trade PS-54 explicitly scoped out and PS-183 explicitly declined to settle in
passing. It is named here so the number is not mistaken for a fleet-wide win.

## What is NOT established

- **No value here was captured by this project on its own Mac.** Every row is a
  string harvested from a public issue report written by somebody else. That is
  the same standard `linux-webgl-reference.md` holds itself to, and it is weaker
  than a first-party capture. A real macOS machine would settle it.
- **The harvest is not exhaustive.** It searched public issue bodies for the
  exact renderer shape; it does not enumerate every chip Apple ships, and the
  absence of a name below is not evidence that no Mac reports it.
- **Capture counts are a floor, not a population estimate.** They say how many
  distinct reports this harvest happened to find, and carry no information about
  the real-world market share of each chip. In particular the pool is drawn
  **uniformly**, which does *not* match the real installed base — that is a
  deliberate unlinkability trade (an even draw minimises collision), not a claim
  about how common an M4 Pro is.
- **macOS version is absent by design**, not overlooked: ANGLE returns the
  `Unspecified Version` literal for WebGL contexts, so there is no version term
  to get wrong.
