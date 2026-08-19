# iOS WebGL reference — what a real iPhone reports

Reference values behind the `ios` arm of `src/services/browser/gpu_ext.py`
(ticket PS-12). This file exists so the constants in that module have a
checkable provenance and the next person does not re-derive them from scratch.

**Read the provenance column before trusting any value.** Every entry below is
labelled either **[device]** (observed on physical hardware) or **[source]**
(reproduced from published WebKit/ANGLE source). Source derivation is the
stronger evidence for values that are compile-time constants: a capture samples
only the devices that happen to exist, whereas source proves what the value
*can* be on any device. It is the weaker evidence for anything queried at
runtime — `MAX_SAMPLES` below is exactly that case, and the two disagree.

## Provenance of this document

- **Device capture:** iPhone on **iOS 18.7** Safari, captured on
  browserleaks.com/webgl.
  UA: `Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6 Mobile/15E148 Safari/604.1`
- **Source derivation:** WebKit trunk and its bundled ANGLE
  (`Source/WebCore/html/canvas/*`, `Source/ThirdParty/ANGLE/src/libANGLE/renderer/metal/DisplayMtl.mm`).
- ⚠️ **The six capture screenshots referenced by PS-12 (`ios-webgl-1.jpg` …
  `-6.jpg`, staged in `/workspace/_inbox/`) were NOT available in the worker
  environment when this file was written and are therefore NOT committed.** The
  transcribed values below were carried over from the ticket description, which
  recorded them from that capture. Anything marked **[device]** rests on that
  transcription, not on an image in this repository. If the screenshots resurface,
  commit them beside this file and drop this caveat.

Note the version skew that is deliberate: the capture is iOS 18.7, while
`IOS_PRESETS` in `device_presets.py` claim **iOS 17.5**. The values below are
valid for both (see "Version floors"), but a preset claiming an older iOS would
invalidate the extension set — see the note in `device_presets.py`.

## Vendor / renderer — ONE pair, not a pool

| Parameter | Enum | Value | Provenance |
|---|---|---|---|
| `VENDOR` | 0x1F00 / 7936 | `WebKit` | [source] + [device] |
| `RENDERER` | 0x1F01 / 7937 | `WebKit WebGL` | [source] + [device] |
| `UNMASKED_VENDOR_WEBGL` | 0x9245 / 37445 | `Apple Inc.` | [source] + [device] |
| `UNMASKED_RENDERER_WEBGL` | 0x9246 / 37446 | `Apple GPU` | [source] + [device] |

These are **compile-time string literals in WebKit, not values queried from the
GPU**. `WebGLRenderingContextBase.cpp:2127-2135` returns them before any
hardware is consulted, with no platform, model, chip or version branching in the
code path. They were made constant deliberately in commit `0a982a43f4`
(2018-11-07, bug 191393, *"[iOS] WebGL leaks exact GPU type"*), replacing a
prior passthrough that leaked the real chip. Apple never exposes the A-series
part.

**Consequence — this is the counter-intuitive one.** Every iPhone on earth
reports this exact pair, so it carries **zero entropy**. Presenting it on every
iOS profile is presenting the truth; *diversifying* it across profiles would
itself be the anomaly, because any other value is impossible on real hardware.
iOS profiles are differentiated on other vectors, never this one. `gpu_ext.py`
therefore holds `IOS_GPU` as a single constant and deliberately does **not**
route it through `pick()` — the seed must not reach this value.

**Gating:** the unmasked pair is returned **only** when the page has called
`getExtension('WEBGL_debug_renderer_info')`. Queried without enabling the
extension, real WebKit raises `INVALID_ENUM` and returns `null`.

## Numeric limits (WebGL1 / `COMMON_IOS`)

From ANGLE's Metal backend, `DisplayMtl.mm` → `ensureCapsInitialized()`. iOS
WebGL has run on ANGLE-over-Metal since iOS 15. WebKit itself does **no**
clamping (`WebGLRenderingContextBase.cpp` passes `getParameter` straight
through). 30 of the 37 values are literal compile-time constants or arithmetic
over them.

| Parameter | Enum | Value | Provenance |
|---|---|---|---|
| `MAX_TEXTURE_SIZE` | 3379 | 16384 | [source] + [device] |
| `MAX_CUBE_MAP_TEXTURE_SIZE` | 34076 | 16384 | [source] |
| `MAX_RENDERBUFFER_SIZE` | 34024 | 16384 | [source] |
| `MAX_VIEWPORT_DIMS` | 3386 | `[16384, 16384]` | [source] + [device] |
| `MAX_VERTEX_ATTRIBS` | 34921 | 16 | [source] |
| `MAX_VERTEX_UNIFORM_VECTORS` | 36347 | 1024 | [source] |
| `MAX_FRAGMENT_UNIFORM_VECTORS` | 36349 | 1024 | [source] |
| `MAX_VARYING_VECTORS` | 36348 | 31 | [source] |
| `MAX_VERTEX_TEXTURE_IMAGE_UNITS` | 35660 | 16 | [source] |
| `MAX_TEXTURE_IMAGE_UNITS` | 34930 | 16 | [source] |
| `MAX_COMBINED_TEXTURE_IMAGE_UNITS` | 35661 | 32 | [source] |
| `ALIASED_POINT_SIZE_RANGE` | 33901 | `[1, 511]` | [source] |
| `ALIASED_LINE_WIDTH_RANGE` | 33902 | `[1, 1]` | [source] |

**Three sharp discriminators against the desktop block** that `ios` used to be
served, all wrong before PS-12: viewport `[16384,16384]` not `[32767,32767]`;
point size `[1,511]` not `[1,1024]`; vertex uniform vectors `1024` not `4096`.

**`MAX_VARYING_VECTORS = 31` is an iOS-vs-macOS discriminator** that was not
previously modelled: macOS Metal subtracts `[[position]]` and reports **30**,
iOS reports **31**. Copying the desktop value leaves a subtler impossible pair.

**These values are not independent — do not parameterise them.**
`MAX_VIEWPORT_DIMS` is definitionally `[MAX_TEXTURE_SIZE, MAX_TEXTURE_SIZE]`;
cube-map and renderbuffer sizes equal it too; `MAX_TEXTURE_LOD_BIAS =
log2(MAX_TEXTURE_SIZE)+1`; combined uniform components equal
`4096 + 16384*16/4`. Changing one without the others is self-inconsistent and
detectable.

**Model variance: none.** The only model-dependent gates in the whole function
are `supportsAppleGPUFamily(3)`, `supportsEitherGPUFamily(2,1)` and
`supportsAppleGPUFamily(1)`. `MTLGPUFamilyApple3` corresponds to A9, and iOS 15
— the first release with this backend — already requires A9, so every iPhone
that can run this code path takes the identical branch. The alternate branches
(8192 texture, 60 components, 4 draw buffers) are reachable only on the
Simulator and pre-A9 hardware.

## Version strings — no Chromium suffix

| Context | `VERSION` (7938) | `SHADING_LANGUAGE_VERSION` (35724) |
|---|---|---|
| WebGL2 | `WebGL 2.0` | `WebGL GLSL ES 3.00` |
| WebGL1 | `WebGL 1.0` | `WebGL GLSL ES 1.00` |

The WebGL2 pair is **[device]**. The WebGL1 pair is **[source], medium
confidence** — the device capture was a WebGL2 context.

Before PS-12 the code emitted `"WebGL 2.0 (OpenGL ES 3.0 Chromium)"`. That
parenthetical is a Chromium *build* artifact announcing Chromium on a device
where Chromium cannot exist — a direct contradiction of the iPhone UA.

## Extension sets — different per context, and order matters

`getSupportedExtensions()` in WebKit is a **hardcoded ordered sequence of
`APPEND_IF_SUPPORTED` macros**, not a dynamic enumeration:
`WebGLRenderingContext.cpp:221-262` (WebGL1) and
`WebGL2RenderingContext.cpp:2694-2732` (WebGL2). The return order is therefore
deterministic and identical on every device — entries drop out, they never
reorder.

**The order is nearly alphabetical but provably not sorted.** The device capture
confirms the model with **25/25 exact agreement** over the span it covers,
including the non-alphabetical placement of
`WEBKIT_WEBGL_compressed_texture_pvrtc` immediately after
`WEBGL_compressed_texture_pvrtc`. In the WebGL1 list, `EXT_sRGB` sits after
`EXT_texture_mirror_clamp_to_edge` — a lowercase-`s` sorting artifact in the
source; deliberate, not a transcription error.

A fingerprinter that sorts before comparing sees nothing; one that hashes the
raw array catches a wrong order instantly. **Emit source order verbatim — the
lists in `gpu_ext.py` must not be "tidied".** The tests compare them as ordered
sequences for this reason.

The authoritative lists live in `gpu_ext.py` as `IOS_GL2_EXTS` (36 entries) and
`IOS_GL1_EXTS` (39 entries), and are asserted element-for-element in
`tests/test_gpu_ext.py`. They are not duplicated here to avoid a second copy
drifting from the first.

### Deliberately excluded

- `WEBGL_draw_instanced_base_vertex_base_instance` and
  `WEBGL_multi_draw_instanced_base_vertex_base_instance` — guarded by
  `&& enableDraftExtensions` in source and confirmed **absent** on the device.
  Draft extensions are off in shipping Safari.
- `EXT_disjoint_timer_query` (GL1) and `EXT_disjoint_timer_query_webgl2` (GL2) —
  gated on the `webGLTimerQueriesEnabled` setting, whose shipping default could
  not be established; they fall in the region the device screenshot cut off.
  Excluded because timer queries are a classic timing-attack surface Safari is
  conservative about. **This is the weakest single decision in the set** — see
  "Known uncertainties".

### s3tc is REAL on Apple silicon

An older comment in `gpu_ext.py` asserted *"Apple/Metal also has no s3tc"* and
dropped the s3tc family on that basis. **That was false and the device disproves
it**; PS-12 corrected the comment in the same commit as the code.

The chain: `WebGLCompressedTextureS3TC::supported()` is a pure capability query
with no transcode path; it resolves into ANGLE's Metal backend, where every BC
format maps to a real `MTLPixelFormat`; and
`DisplayMtl::supportsBCTextureCompression()` returns
`[mMetalDevice supportsBCTextureCompression]` behind an **iOS 16.4**
availability annotation, `false` below that. Apple silicon natively supports
BC/DXT while retaining the mobile PVRTC/ETC/ASTC lineage — hence **all five
compression families coexisting**, which is the counter-intuitive fact this
reference exists to preserve.

**All-or-nothing constraint:** BC1–BC7 share that single boolean, so
`WEBGL_compressed_texture_s3tc`, `WEBGL_compressed_texture_s3tc_srgb`,
`EXT_texture_compression_bptc` and `EXT_texture_compression_rgtc` are **all
present or all absent together**. Emitting s3tc without bptc/rgtc is an
internally inconsistent set — a natural mistake and a detection signal.

## Version floors

| Requirement | Floor |
|---|---|
| s3tc / bptc / rgtc (BC compression) | iOS ≥ 16.4 |
| `EXT_clip_control`, `WEBGL_polygon_mode`, `EXT_conservative_depth`, `EXT_render_snorm`, `EXT_depth_clamp`, `WEBGL_render_shared_exponent`, `WEBGL_stencil_texturing` (2023-08-08 WebKit batch) | iOS ≥ 17 |
| `ALIASED_POINT_SIZE_RANGE = [1,511]` (was `[1,64]`) | iOS ≥ 15.0 |

`IOS_PRESETS` claim iOS 17.5, which clears all three. **If a preset is ever
added claiming an older iOS, this extension set becomes wrong for it** — the
matching note lives in `device_presets.py` beside the presets.

## Known uncertainties — carried forward honestly

1. **`MAX_SAMPLES` is not spoofed, deliberately.** It is the one genuinely
   runtime-queried value in the set (`mFormatTable.getMaxSamples()` over
   `{2,4,8}`). Source predicts **8**; the physical device reports **4**. The
   discrepancy is unexplained and there is no second capture. Leaving it
   unspoofed is honest; pinning an unexplained constant would be a guess wearing
   a citation.
2. **`EXT_disjoint_timer_query` / `_webgl2`** — excluded (reasoning above), but
   the shipping default is genuinely unknown. This is a ±1 on both lists.
3. **Emission direction.** The device rendering shows the list reversed relative
   to source order. Whether browserleaks reverses it for display or the browser
   emits it that way could not be settled remotely; it is a one-line check
   against a real device. The lists are in **source order**. If a later capture
   shows otherwise, the *order* — not the membership — is what changes.
4. **The WebGL1 list is source-derived and unverified against hardware** (the
   capture was a WebGL2 context). Membership and order come from WebKit source
   and are solid; runtime presence of individual entries is inference.
5. **Trunk versus the iOS 18.7 branch.** The source read was WebKit trunk,
   validated against the device for the span the capture covers. The head of the
   WebGL2 list — the `EXT_*` region — is exactly where the screenshot was cut
   off and carries unquantified drift risk.

## Known gaps — not spoofed today, on any platform

These `getParameter` calls still fall through to the host's real renderer. The
reference supplies the iOS values, but closing the gap touches all platforms and
belongs in its own ticket.

- **The WebGL2-only parameter block:** `MAX_3D_TEXTURE_SIZE`,
  `MAX_ARRAY_TEXTURE_LAYERS`, uniform-buffer limits, transform-feedback limits,
  texel offsets, `MAX_TEXTURE_LOD_BIAS`, anisotropy.
- **Context attributes:** `stencil=false`, `antialias=true`,
  `premultipliedAlpha=true`, `depth=24`, `stencil bits=0`.
- **The `webkit-3d` context alias.** Real iOS reports supported contexts
  `webgl2, webgl, webkit-3d`; the third is a WebKit-only legacy alias Chromium
  does not implement, so `canvas.getContext('webkit-3d')` returns a context on a
  real iPhone and `null` for us. This cannot be fixed by patching
  `getParameter` — it needs an alias in `getContext` — and is a genuine residual
  tell.
