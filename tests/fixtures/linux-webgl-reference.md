# Linux WebGL reference — what a real Linux Chrome reports

Reference values behind the `linux` arm of `src/services/browser/gpu_ext.py`
(ticket PS-36). This file exists so the constants in that module have a
checkable provenance and the next person does not re-derive them from scratch.

**Read the provenance column before trusting any value.** Every entry below is
labelled either **[upstream]** (a renderer string pasted verbatim into a public
Mesa issue report by a real user on real hardware) or **[source]** (derived from
published Mesa / ANGLE / Chromium source). Neither is a capture taken by this
project on its own hardware — see "What is NOT established" at the bottom.

## Why the raw driver string is not the value to bake in

This is the trap, and it is the whole reason this file is long. The string
`glxinfo` prints on Linux is **not** the string a web page reads. Two ANGLE
transforms sit in between, and both were confirmed from source:

### 1. Truncation — the kernel and DRM version never reach the page

Mesa's `radeonsi` composes its renderer string at runtime from five terms,
the last being the host kernel release from `uname()`
([`si_get.c:106-110`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/gallium/drivers/radeonsi/si_get.c)):

```c
snprintf(sscreen->renderer_string, sizeof(sscreen->renderer_string),
         "%s (radeonsi, %s%s%s, DRM %i.%i%s)", first_name, second_name,
         sscreen->has_gfx_compute ? ", " : "",
         sscreen->has_gfx_compute ? compiler_name : "",
         sscreen->info.drm_major, sscreen->info.drm_minor, kernel_version);
```

That looks like it makes a free-standing pool impossible — a baked string would
embed somebody's kernel version. **It does not, because ANGLE cuts it off.**
`SanitizeRendererString` ([`DisplayGL.cpp:36-52`](https://chromium.googlesource.com/angle/angle/+/refs/heads/main/src/libANGLE/renderer/gl/DisplayGL.cpp))
truncates at the first `", DRM "` and re-closes the paren:

```cpp
size_t pos = rendererString.find(", DRM ");
if (pos != std::string::npos) { rendererString.resize(pos); rendererString.push_back(')'); return rendererString; }
```

It is called from `DisplayGL::getRendererDescription` (`:178-187`) under the
feature `sanitizeAMDGPURendererString`, whose condition is
`IsLinux() && hasAMD` ([`renderergl_utils.cpp:2552`](https://chromium.googlesource.com/angle/angle/+/refs/heads/main/src/libANGLE/renderer/gl/renderergl_utils.cpp)) —
enabled by default, no flag required. Chromium added it precisely because the
kernel and DRM version were a fingerprinting leak (crbug.com/1181193).

**Intel never had the problem at all.** `iris_get_name`
([`iris_screen.c:122-130`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/gallium/drivers/iris/iris_screen.c))
is `snprintf(buf, sizeof(buf), "Mesa %s", devinfo->name)` and nothing else;
`grep -cE "uname|utsname|drm_major" iris_screen.c` returns **0**. The five-term
coupling is an AMD/`radeonsi` property, not a Linux property.

### 2. Comma erasure — why the surviving terms are space-separated

`Context::initRendererString` ([`Context.cpp:3684-3701`](https://chromium.googlesource.com/angle/angle/+/refs/heads/main/src/libANGLE/Context.cpp))
builds `"ANGLE (" + vendor + ", " + renderer + ", " + version + ")"` and
**erases every comma from each element first**:

```cpp
rendererString.erase(std::remove(rendererString.begin(), rendererString.end(), ','), rendererString.end());
```

This is the non-obvious one. `AMD Radeon RX 6800 (radeonsi, navi21, ACO)`
becomes `AMD Radeon RX 6800 (radeonsi navi21 ACO)` — space-separated inside the
parens. Do not "correct" it back to commas.

The vendor element is the driver's `GL_VENDOR` literal — `"AMD"`
(`si_get.c:13-16`) or `"Intel"` (`iris_screen.c:80-84`) — and
`UNMASKED_VENDOR_WEBGL` is EGL's `"Google Inc." + " (" + GL_VENDOR + ")"`
([`Display.cpp:2478-2486`](https://chromium.googlesource.com/angle/angle/+/refs/heads/main/src/libANGLE/Display.cpp)),
the same convention `WIN_GPUS` already uses.

## The version element: `OpenGL 4.6`, established not guessed

This was the one element the ticket left explicitly open, so it is recorded in
full.

Two candidates were possible: `OpenGL ES 3.2` (ANGLE-over-GLES) or `OpenGL 4.6`
(ANGLE-over-desktop-GL). `kDefaultANGLEVulkan` is `FEATURE_DISABLED_BY_DEFAULT`
(`ui/gl/gl_switches.cc:299-301`), which rules out the Vulkan backend but does
not by itself decide between the remaining two.

**It is settled empirically by a real capture.** Mesa issue
[#6144](https://gitlab.freedesktop.org/mesa/mesa/-/issues/6144) opens with a
`chrome://gpu` reading from a real Linux Chrome on Intel:

```
GL_RENDERER  ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6 (Core Profile) Mesa 22.1.0-develgit-)
```

That is ANGLE composing over **desktop GL**, not GLES. — **[upstream]**

The `(Core Profile) Mesa 22.1.0-develgit-` tail is then dropped in a WebGL
context: `Context.cpp:3697` passes `getBackendVersionString(!isWebGL())`, so
`includeFullVersion` is `false`, and `SanitizeVersionString`
(`DisplayGL.cpp:56-83`) reduces the string to its version number alone.

Be precise about *how*, because the tempting one-line gloss — "it keeps the
token up to the first space" — is wrong: read literally it yields `OpenGL`, not
`OpenGL 4.6`. The function never slices the input from position 0. It
*re-emits* a literal prefix (`"OpenGL "`, plus `"ES "` when `isES`) and then
appends the token that follows that prefix in the input:

```
openGLESPos = find("OpenGL ")            // 0 here; 0 if absent
openGLESPos += len("OpenGL ") (+ len("ES ") if isES)   // -> 7
result = "OpenGL " + versionString.substr(7, find(" ", 7) - 7)
```

So the "first space" it stops at is the one *after* the version number, not the
one after the word `OpenGL`. Traced against the captured string: `find(" ", 7)`
is `10`, `substr(7, 3)` is `4.6`, giving exactly `OpenGL 4.6`. The mesa#6144
`chrome://gpu` capture confirms the same value empirically. — **[source]**

**No Mesa build version is page-visible, and none is baked into any value
below.** A string carrying one would be impossible in a real browser.

## Vendor / renderer pool

Each row is one harvested tuple. The raw column is what the user pasted; the
page-visible column is that string after transforms 1 and 2 above.

**Keep each tuple intact — do not recombine terms across rows.** The ASIC
codename and the compiler term (`ACO` vs `LLVM <ver>`) are coherent with the
marketing name: `navi21` belongs to RX 6800 and to nothing else. Mixing terms
from two rows produces a well-formed string that has never existed on any real
machine — a *novel* tell, which is strictly worse than the Direct3D-on-Linux bug
this arm fixes.

### AMD — `radeonsi`, `UNMASKED_VENDOR_WEBGL` = `Google Inc. (AMD)`

| Raw (`glxinfo` / user paste) | Page-visible renderer | Provenance |
|---|---|---|
| `AMD Radeon RX 6800 (radeonsi, navi21, ACO, DRM 3.64, 6.17.7-ba28.fc43.x86_64)` | `ANGLE (AMD, AMD Radeon RX 6800 (radeonsi navi21 ACO), OpenGL 4.6)` | [upstream] [mesa#15156](https://gitlab.freedesktop.org/mesa/mesa/-/issues/15156) |
| `AMD Radeon RX 7900 XTX (radeonsi, navi31, ACO, DRM 3.64, 6.18.13-arch1-1)` | `ANGLE (AMD, AMD Radeon RX 7900 XTX (radeonsi navi31 ACO), OpenGL 4.6)` | [upstream] [mesa#14957](https://gitlab.freedesktop.org/mesa/mesa/-/issues/14957) |
| `AMD Radeon RX 7600 (radeonsi, navi33, ACO, DRM 3.61, 6.12.74-1-lts)` | `ANGLE (AMD, AMD Radeon RX 7600 (radeonsi navi33 ACO), OpenGL 4.6)` | [upstream] [mesa#15022](https://gitlab.freedesktop.org/mesa/mesa/-/issues/15022) |
| `AMD Radeon RX 6600 (radeonsi, navi23, LLVM 18.1.6, DRM 3.57, 6.10.3)` | `ANGLE (AMD, AMD Radeon RX 6600 (radeonsi navi23 LLVM 18.1.6), OpenGL 4.6)` | [upstream] [mesa#11663](https://gitlab.freedesktop.org/mesa/mesa/-/issues/11663) |

The RX 6600 row is the one that keeps `LLVM 18.1.6` rather than `ACO`; that is
how it was reported, and it is kept as-is rather than normalised to match its
neighbours.

### Intel — `iris`, `UNMASKED_VENDOR_WEBGL` = `Google Inc. (Intel)`

No truncation applies (no DRM/kernel term exists in the Intel string), so the
raw renderer and the page-visible renderer differ only by ANGLE's wrapper.

| Raw (`glxinfo` / user paste) | Page-visible renderer | Provenance |
|---|---|---|
| `Mesa Intel(R) UHD Graphics 630 (CFL GT2)` | `ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)` | [upstream] [mesa#6144](https://gitlab.freedesktop.org/mesa/mesa/-/issues/6144) — captured *already wrapped*, straight from `chrome://gpu` |
| `Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2)` | `ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2), OpenGL 4.6)` | [upstream] [mesa#15385](https://gitlab.freedesktop.org/mesa/mesa/-/issues/15385) |
| `Mesa Intel(R) HD Graphics 530 (SKL GT2)` | `ANGLE (Intel, Mesa Intel(R) HD Graphics 530 (SKL GT2), OpenGL 4.6)` | [upstream] [mesa#4421](https://gitlab.freedesktop.org/mesa/mesa/-/issues/4421) |
| `Mesa Intel(R) UHD Graphics 770 (ADL-S GT1)` | `ANGLE (Intel, Mesa Intel(R) UHD Graphics 770 (ADL-S GT1), OpenGL 4.6)` | [upstream] [mesa#15683](https://gitlab.freedesktop.org/mesa/mesa/-/issues/15683) |

The `mesa#6144` row is the strongest single piece of evidence in this file: it
is the only one observed *through Chrome's own ANGLE stack* rather than through
`glxinfo`, so it validates the wrapper format and the version element at the
same time as the renderer value.

## NVIDIA proprietary — deliberately excluded

Real NVIDIA strings were recovered upstream (e.g.
`NVIDIA GeForce RTX 4060/PCIe/SSE2`, [mesa#15217](https://gitlab.freedesktop.org/mesa/mesa/-/issues/15217)),
and the blob does **not** go through Mesa, so it has a different string shape
again. Its `GL_VENDOR` literal appears to be `NVIDIA Corporation` rather than
`NVIDIA`, but that could not be confirmed from source because the driver is
closed. Since `UNMASKED_VENDOR_WEBGL` is built from that literal, guessing it
would fabricate the exact kind of value this file exists to prevent. Two vendors
is a sufficient pool; a third is not worth an unconfirmed constant.

## Limits and extension sets — verified, deliberately unchanged

`linux` resolves to the existing desktop defaults, and both were checked rather
than assumed:

- `gpu_ext.py` `COMMON = ... : COMMON_DESKTOP` → linux gets **`COMMON_DESKTOP`**
- `gpu_ext.py` `STABLE_EXTS = ... : DESKTOP_EXTS` → linux gets **`DESKTOP_EXTS`**

The open question was whether `DESKTOP_EXTS`' `s3tc` / `s3tc_srgb` / `bptc` /
`rgtc` (the BC/DXT family) are safe to advertise on Mesa. Checked against Mesa's
own feature matrix (`docs/features.txt`): — **[source]**

- `GL_EXT_texture_compression_rgtc` — *DONE (all drivers that support `GL_EXT_texture_snorm`)*
- `GL_ARB_texture_compression_bptc` — *DONE (all drivers that support `EXT_texture_sRGB` and `OES_texture_half_float`)*
- S3TC has been unconditional since Mesa 17.3 (`docs/relnotes/17.3.0.rst`), the
  patent-expiry release.

Both `radeonsi` and `iris` are well past those gates, so the desktop defaults
are correct for Linux as-is. **Neither block was forked.** Inventing a "Linux
variant" of the desktop limits would have manufactured unsourced values for no
benefit.

`MAX_VIEWPORT_DIMS` stays `[32767, 32767]` — the desktop ANGLE value, coherent
with a desktop renderer string.

## What is NOT established

Stated plainly so the next person does not over-trust this file:

1. **No capture was taken by this project on real Linux GPU hardware.** The
   build container has no GPU (`/dev/dri` absent; local Chromium falls back to
   SwiftShader, which is the exact tell `gpu_ext` exists to hide). Every value
   here is upstream-harvested or source-derived.
2. **The `getParameter` limits below the vendor/renderer pair were not measured
   on Linux.** They are inherited from the existing desktop block, which is
   argued correct above but not confirmed against a Linux capture.
3. **Only `mesa#6144` was observed through Chrome's ANGLE stack.** The other
   seven rows are `glxinfo`-shaped and had transforms 1 and 2 applied by
   derivation. The derivation is executed from source, not eyeballed — but it is
   a derivation.

If a real Linux Chrome on real hardware becomes reachable, confirm the eight
page-visible strings against it and replace the `[upstream]` labels with
`[device]`.
