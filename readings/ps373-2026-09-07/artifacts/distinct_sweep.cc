// distinct_sweep.cc — the follow-up experiment section C forced.
//
// Section C of probe_harness showed a UNIFORM canvas already comes back
// byte-exact from our loop. That was not predicted by PS-373 round 3, whose
// table records our "uniform-render skip" as *none*. The reason is structural:
// our loop only touches a pixel where `isEdge` holds (`*current != *right ||
// *current != *bottom`), and a single-colour buffer has no such pixel anywhere.
//
// So we DO have a uniform-render guard — implicitly, as a side effect of edge
// detection. The question this file answers is HOW WIDE that implicit guard is,
// because feder's explicit guard is parameterised (`kMaxRefColors = 16`: a render
// of at most 16 distinct colours is returned byte-exact) and pixelscan's probe is
// reported to use FOURTEEN.
//
// If our implicit guard covers only 1 colour while feder's covers 16, then the
// gap between the two implementations is not "he has a guard and we don't" but a
// specific, quantified threshold — and 14 < 16 would explain why the same probe
// passes his engine and fails ours.
//
// ⚠️ Same standing limit as the main harness: the 14-colour figure is second-hand
// from feder's RE as recorded on the ticket. This file measures OUR side of the
// comparison exactly; it does not verify his probe description.

#include <algorithm>
#include <cstdio>
#include <functional>
#include <string>
#include <unordered_set>
#include <vector>

#include "skia_shim.h"

namespace {

std::string g_seed = "12345";

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
#include "extracted_shuffle_body.inc"
      }
    }
  }
}

// A canvas painted in `n` vertical bands of distinct mid-tone colours.
std::vector<uint32_t> Banded(int w, int h, int n) {
  std::vector<uint32_t> buf((size_t)w * h);
  for (int y = 0; y < h; ++y) {
    for (int x = 0; x < w; ++x) {
      int band = (n <= 1) ? 0 : std::min(n - 1, (x * n) / std::max(1, w));
      // Spread hues across the band index; keep away from pure black/white so the
      // loop's own isValidColor test never excludes them for the wrong reason.
      unsigned r = 40 + (unsigned)((band * 37) % 180);
      unsigned g = 40 + (unsigned)((band * 91) % 180);
      unsigned b = 40 + (unsigned)((band * 53) % 180);
      buf[(size_t)y * w + x] = (255u << SK_A32_SHIFT) | (r << SK_R32_SHIFT) |
                               (g << SK_G32_SHIFT) | (b << SK_B32_SHIFT);
    }
  }
  return buf;
}

int Modified(int w, int h, int n) {
  std::vector<uint32_t> orig = Banded(w, h, n);
  std::vector<uint32_t> work = orig;
  ImageInfo info{w, h, kRGBA_8888_SkColorType, (size_t)w * 4};
  ShuffleSubchannelColorData(work.data(), info);
  int d = 0;
  for (size_t i = 0; i < orig.size(); ++i)
    if (orig[i] != work[i]) ++d;
  return d;
}

}  // namespace

int main() {
  printf("== How many distinct colours does OUR implicit guard tolerate? ==\n");
  printf("(a byte-exact row means the render survives a reference-equality probe)\n\n");
  printf("   %-9s %-14s %-14s %s\n", "colours", "70x5 probe", "300x150", "verdict@70x5");
  for (int n : {1, 2, 3, 4, 8, 14, 16, 17, 24, 32}) {
    int a = Modified(70, 5, n);
    int b = Modified(300, 150, n);
    printf("   %-9d %-14d %-14d %s\n", n, a, b,
           a == 0 ? "byte-exact" : "ALTERED -> isCanvas=false");
  }

  printf("\n== Would a size floor of 64*64*4 bytes cost real protection? ==\n");
  printf("(the floor feder applies: skip buffers under %d bytes)\n", 64 * 64 * 4);
  printf("   %-12s %-10s %-10s %s\n", "size", "bytes", "modified", "under floor?");
  struct { int w, h; const char* what; } cases[] = {
      {70, 5, "pixelscan probe"},
      {16, 16, "icon"},
      {32, 32, "favicon"},
      {64, 64, "exactly at floor"},
      {100, 30, "small fp canvas"},
      {128, 128, "medium"},
      {220, 30, "classic fp text canvas"},
      {280, 60, "classic fp text canvas"},
      {300, 150, "canvas default size"},
      {500, 200, "large fp canvas"},
  };
  for (const auto& c : cases) {
    size_t bytes = (size_t)c.w * c.h * 4;
    int n = Modified(c.w, c.h, 14);
    printf("   %-12s %-10zu %-10d %-6s  (%s)\n",
           (std::string(std::to_string(c.w) + "x" + std::to_string(c.h))).c_str(),
           bytes, n, bytes < 64u * 64u * 4u ? "YES" : "no", c.what);
  }
  return 0;
}
