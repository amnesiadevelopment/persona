// clearrect_probe.cc — does our loop actually modify cleared pixels?
//
// PS-373 round 3's comparison table records, as a guard WE LACK:
//     "clearRect trap | feder: `if (*p == 0) continue;` (zero channel)
//                     | ours: ⛔ skips pure-black PIXELS, not zero CHANNELS"
//
// That is a true statement about the CODE. Whether it is a true statement about
// the BEHAVIOUR that matters is a different question, and it is the question
// CreepJS actually asks: it draws, calls clearRect, then re-reads, and sets
// lied=true if the cleared pixels do not read back as zero.
//
// A cleared pixel is (0,0,0,0). Our isValidColor excludes r==0 && g==0 && b==0.
// So the interesting case is not whether our test is per-channel — it is whether
// the pixels CreepJS re-reads survive. This file settles that by execution rather
// than by reading the predicate.

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

uint32_t Px(unsigned a, unsigned r, unsigned g, unsigned b) {
  return (a << SK_A32_SHIFT) | (r << SK_R32_SHIFT) | (g << SK_G32_SHIFT) |
         (b << SK_B32_SHIFT);
}

}  // namespace

int main() {
  const int w = 300, h = 150;

  // Draw a busy canvas, then "clearRect" the left half to (0,0,0,0).
  std::vector<uint32_t> buf((size_t)w * h);
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w; ++x)
      buf[(size_t)y * w + x] =
          Px(255, (unsigned)(x * 7 % 200 + 30), (unsigned)(y * 11 % 200 + 30),
             (unsigned)((x + y) * 13 % 200 + 30));
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w / 2; ++x) buf[(size_t)y * w + x] = Px(0, 0, 0, 0);

  std::vector<uint32_t> orig = buf;
  ImageInfo info{w, h, kRGBA_8888_SkColorType, (size_t)w * 4};
  ShuffleSubchannelColorData(buf.data(), info);

  int cleared_changed = 0, kept_changed = 0, cleared_nonzero_after = 0;
  for (int y = 0; y < h; ++y) {
    for (int x = 0; x < w; ++x) {
      size_t i = (size_t)y * w + x;
      bool in_cleared = x < w / 2;
      if (orig[i] != buf[i]) (in_cleared ? cleared_changed : kept_changed)++;
      if (in_cleared && buf[i] != 0) cleared_nonzero_after++;
    }
  }

  printf("== CreepJS clearRect trap, measured ==\n");
  printf("   canvas %dx%d, left half cleared to (0,0,0,0)\n\n", w, h);
  printf("   pixels modified inside the CLEARED region : %d\n", cleared_changed);
  printf("   pixels modified outside it                : %d\n", kept_changed);
  printf("   cleared pixels that read back NON-ZERO    : %d\n", cleared_nonzero_after);
  printf("\n   verdict: %s\n",
         cleared_nonzero_after == 0
             ? "cleared pixels read back as zero -> CreepJS lied=true is NOT tripped"
             : "CLEARED PIXELS ALTERED -> CreepJS would set lied=true");

  // The residual case the per-channel difference actually covers: a pixel with
  // ONE zero channel that is not pure black. feder skips it; we do not.
  printf("\n== the residual: pixels with a zero CHANNEL but not pure black ==\n");
  std::vector<uint32_t> buf2((size_t)w * h);
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w; ++x)
      buf2[(size_t)y * w + x] = Px(255, 0, (unsigned)(y * 11 % 200 + 30),
                                   (unsigned)((x + y) * 13 % 200 + 30));
  std::vector<uint32_t> o2 = buf2;
  ShuffleSubchannelColorData(buf2.data(), info);
  int r_channel_touched = 0, changed2 = 0;
  for (size_t i = 0; i < o2.size(); ++i) {
    if (o2[i] != buf2[i]) {
      changed2++;
      if (SkGetPackedR32(o2[i]) != SkGetPackedR32(buf2[i])) r_channel_touched++;
    }
  }
  printf("   canvas with R==0 everywhere: %d pixels modified, %d had R perturbed\n",
         changed2, r_channel_touched);
  printf("   -> a zero channel IS writable by our loop (feder's guard would not\n");
  printf("      write it). Whether any detector reads that specific case is a\n");
  printf("      host question; it is NOT the CreepJS clearRect trap, which is\n");
  printf("      measured above and which we already survive.\n");
  return 0;
}
