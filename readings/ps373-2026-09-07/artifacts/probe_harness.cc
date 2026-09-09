// probe_harness.cc — run OUR canvas patch's own loop against a reconstruction of
// pixelscan's masking probe, and report how many reference pixels it modifies.
//
// THE QUESTION
// ------------
// PS-373 round 3 recorded feder's reverse-engineering of pixelscan's px294.js:
// the `canvasNoiseOn2d` probe fills 14 solid reference colours into a 70x5 canvas
// and checks they read back UNMODIFIED. Any substitution sets isCanvas=false,
// which gates the font probe, which is what produces "masking detected".
//
// That is a claim about OUR arithmetic, and arithmetic is the one thing an agent
// container CAN settle. This harness compiles our patch's verbatim loop
// (extracted_shuffle_body.inc) and runs it over the probe geometry.
//
// ⚠️ WHAT THIS DOES AND DOES NOT ESTABLISH.
//   IT DOES establish, by execution, how many pixels our shipped algorithm alters
//   on a given buffer — including whether the `< 2` clamp raises the budget on a
//   canvas whose (w*h)/128 is below 2.
//   IT DOES NOT establish that pixelscan's probe is 70x5, that 14 colours is the
//   right count, or that modifying it is what trips the badge. Those come from
//   feder's RE as recorded on the ticket, they are SECOND-HAND, and no engine,
//   display or GPU exists here to rank them. Per PS-363: source reading generates
//   candidates; only the owner's host ranks them. The geometry is therefore a
//   PARAMETER of this harness (--width/--height/--bands), not a constant, and the
//   sweep reports a whole range so the finding does not rest on one guessed size.

#include <algorithm>
#include <cstring>
#include <functional>
#include <string>
#include <unordered_set>
#include <vector>

#include "skia_shim.h"

namespace {

std::string g_seed = "0";

// ---------------------------------------------------------------------------
// The function under test. Signature mirrors Chromium's
// StaticBitmapImage::ShuffleSubchannelColorData(void*, const SkImageInfo&, int, int);
// the body between the markers is our patch's, verbatim.
// ---------------------------------------------------------------------------
struct ImageInfo {
  int w, h;
  SkColorType ct;
  size_t rowBytes;
  SkColorType colorType() const { return ct; }
};

void ShuffleSubchannelColorData(void* addr, const ImageInfo& info) {
  const int w = info.w;
  const int h = info.h;
  const size_t fRowBytes = info.rowBytes;
  const std::string seed_str = g_seed;
  const SkColorType colorType = info.colorType();

  // ---- BEGIN verbatim patch body -----------------------------------------
#include "extracted_shuffle_body.inc"
  // ---- END verbatim patch body -------------------------------------------
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Shim self-test: round-trip known values so a mis-ordered channel aborts the
// run rather than silently corrupting every number below it.
// ---------------------------------------------------------------------------
bool SelfTestShim() {
  bool ok = true;
  struct { unsigned a, r, g, b; } cases[] = {
      {255, 0xDE, 0xAD, 0xBE}, {128, 1, 2, 3}, {0, 255, 0, 255}, {255, 0, 0, 0},
  };
  for (const auto& c : cases) {
    uint32_t rgba = (c.a << SK_A32_SHIFT) | (c.r << SK_R32_SHIFT) |
                    (c.g << SK_G32_SHIFT) | (c.b << SK_B32_SHIFT);
    if (SkGetPackedR32(rgba) != c.r || SkGetPackedG32(rgba) != c.g ||
        SkGetPackedB32(rgba) != c.b || SkGetPackedA32(rgba) != c.a) {
      fprintf(stderr, "SHIM SELFTEST FAIL: RGBA8888 round-trip\n");
      ok = false;
    }
    uint32_t bgra = (c.a << SK_BGRA_A32_SHIFT) | (c.r << SK_BGRA_R32_SHIFT) |
                    (c.g << SK_BGRA_G32_SHIFT) | (c.b << SK_BGRA_B32_SHIFT);
    // The BGRA arm of the loop reads R via SkGetPackedB32 and B via SkGetPackedR32.
    if (SkGetPackedB32(bgra) != c.r || SkGetPackedR32(bgra) != c.b) {
      fprintf(stderr, "SHIM SELFTEST FAIL: BGRA8888 channel order\n");
      ok = false;
    }
  }
  // 565
  unsigned r5 = 21, g6 = 40, b5 = 9;
  uint16_t p565 = (uint16_t)(((r5 & SK_R16_MASK) << SK_R16_SHIFT) |
                             ((g6 & SK_G16_MASK) << SK_G16_SHIFT) |
                             ((b5 & SK_B16_MASK) << SK_B16_SHIFT));
  if (SkPacked16ToR32(p565) != r5 || SkPacked16ToG32(p565) != g6 ||
      SkPacked16ToB32(p565) != b5) {
    fprintf(stderr, "SHIM SELFTEST FAIL: RGB565 round-trip\n");
    ok = false;
  }
  // 4444
  unsigned a4 = 15, r4 = 3, g4 = 9, b4 = 12;
  uint16_t p4444 = (uint16_t)(((a4 & 0xF) << SK_A4444_SHIFT) | ((r4 & 0xF) << SK_R4444_SHIFT) |
                              ((g4 & 0xF) << SK_G4444_SHIFT) | ((b4 & 0xF) << SK_B4444_SHIFT));
  if (SkGetPackedA4444(p4444) != a4 || SkGetPackedR4444(p4444) != r4 ||
      SkGetPackedG4444(p4444) != g4 || SkGetPackedB4444(p4444) != b4) {
    fprintf(stderr, "SHIM SELFTEST FAIL: ARGB4444 round-trip\n");
    ok = false;
  }
  return ok;
}

// ---------------------------------------------------------------------------
// pixelscan's reference probe, as described in feder's RE: N solid colour bands
// filling a w x h canvas. Colours are mid-tone on purpose — the loop's
// isValidColor test only excludes pure black and pure white, so a reference
// palette of ordinary colours is entirely "valid" to it.
// ---------------------------------------------------------------------------
const uint32_t kReferenceColors[14] = {
    0xFFFF0000, 0xFF00FF00, 0xFF0000FF, 0xFFFFFF00, 0xFF00FFFF, 0xFFFF00FF,
    0xFF808080, 0xFFFF8000, 0xFF8000FF, 0xFF008080, 0xFF804000, 0xFF40C040,
    0xFFC04040, 0xFF4040C0,
};

std::vector<uint32_t> MakeBandedCanvas(int w, int h, int bands) {
  std::vector<uint32_t> buf((size_t)w * h);
  for (int y = 0; y < h; ++y) {
    for (int x = 0; x < w; ++x) {
      int band = (bands <= 0) ? 0 : std::min(bands - 1, (x * bands) / std::max(1, w));
      uint32_t c = kReferenceColors[band % 14];
      unsigned a = 255, r = (c >> 16) & 0xFF, g = (c >> 8) & 0xFF, b = c & 0xFF;
      buf[(size_t)y * w + x] = (a << SK_A32_SHIFT) | (r << SK_R32_SHIFT) |
                               (g << SK_G32_SHIFT) | (b << SK_B32_SHIFT);
    }
  }
  return buf;
}

// A uniform (single solid colour) canvas — the degenerate reference render.
std::vector<uint32_t> MakeUniformCanvas(int w, int h) {
  return MakeBandedCanvas(w, h, 1);
}

int CountDiff(const std::vector<uint32_t>& a, const std::vector<uint32_t>& b) {
  int n = 0;
  for (size_t i = 0; i < a.size() && i < b.size(); ++i)
    if (a[i] != b[i]) ++n;
  return n;
}

int RunOne(int w, int h, int bands, const char* seed, bool uniform) {
  g_seed = seed;
  std::vector<uint32_t> orig =
      uniform ? MakeUniformCanvas(w, h) : MakeBandedCanvas(w, h, bands);
  std::vector<uint32_t> work = orig;
  ImageInfo info{w, h, kRGBA_8888_SkColorType, (size_t)w * 4};
  ShuffleSubchannelColorData(work.data(), info);
  return CountDiff(orig, work);
}

}  // namespace

int main(int argc, char** argv) {
  if (!SelfTestShim()) {
    fprintf(stderr, "ABORT: shim self-test failed; no measurement is trustworthy.\n");
    return 2;
  }
  printf("shim self-test: PASS\n\n");

  printf("== A. pixelscan reference probe, as REed by feder (70x5, 14 bands) ==\n");
  printf("   budget arithmetic: (70*5)/128 = %d  -> after clamp = %d\n",
         (70 * 5) / 128, ((70 * 5) / 128) > 10 ? 10 : (((70 * 5) / 128) < 2 ? 2 : (70 * 5) / 128));
  for (const char* seed : {"0", "12345", "deadbeef"}) {
    int n = RunOne(70, 5, 14, seed, false);
    printf("   seed=%-9s modified reference pixels = %d   %s\n", seed, n,
           n == 0 ? "PASSES probe" : "FAILS probe (byte-exact readback broken)");
  }

  printf("\n== B. is it the geometry or the algorithm? sweep small canvases ==\n");
  printf("   %-12s %-8s %-8s %s\n", "size", "w*h/128", "budget", "modified");
  struct { int w, h; } sizes[] = {
      {16, 16}, {32, 32}, {70, 5}, {64, 64}, {100, 30}, {128, 128}, {280, 60}, {256, 256},
  };
  for (const auto& s : sizes) {
    int raw = (s.w * s.h) / 128;
    int budget = raw > 10 ? 10 : (raw < 2 ? 2 : raw);
    int n = RunOne(s.w, s.h, 14, "12345", false);
    char label[32];
    snprintf(label, sizeof(label), "%dx%d", s.w, s.h);
    printf("   %-12s %-8d %-8d %d\n", label, raw, budget, n);
  }

  printf("\n== C. uniform (single-colour) render — the degenerate reference ==\n");
  for (const auto& s : sizes) {
    int n = RunOne(s.w, s.h, 1, "12345", true);
    char label[32];
    snprintf(label, sizeof(label), "%dx%d", s.w, s.h);
    printf("   %-12s modified = %d  %s\n", label, n,
           n == 0 ? "(byte-exact)" : "(ALTERED)");
  }

  printf("\n== D. determinism: same seed twice, different seeds differ ==\n");
  int a1 = RunOne(280, 60, 14, "12345", false);
  int a2 = RunOne(280, 60, 14, "12345", false);
  int b1 = RunOne(280, 60, 14, "99999", false);
  printf("   280x60 seed=12345 run1=%d run2=%d (stable=%s), seed=99999 -> %d\n",
         a1, a2, a1 == a2 ? "yes" : "NO", b1);

  return 0;
}
