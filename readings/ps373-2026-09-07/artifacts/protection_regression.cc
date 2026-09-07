// protection_regression.cc — the test that must FAIL if the guard is too wide.
//
// WHY THIS FILE EXISTS, AND WHY IT IS THE IMPORTANT ONE
// -----------------------------------------------------
// probe_harness.cc's canvases are painted in 14 colour bands. After adding the
// reference-render guard (kMaxRefColors = 16), EVERY one of them came back
// byte-exact — including the 256x256 and 280x60 cases that are meant to stand in
// for real fingerprinting surfaces. Read carelessly, that looks like a total win.
// It is not: those canvases are reference-like BY CONSTRUCTION (14 < 16), so the
// harness had lost the ability to tell "the guard works" from "the guard switched
// the noise off everywhere".
//
// PS-373 states the trap explicitly:
//     ⛔ "No masking detected" is NOT the goal. The only configuration that
//        currently achieves it has ZERO protection. Any recommendation must
//        preserve or improve what is masked.
//
// So this file is the negative control. It builds a REALISTIC fingerprinting
// canvas — the antialiased text-plus-gradient shape that fingerprint scripts
// actually draw — and requires that it STILL receives noise, and that the noise
// is still seed-dependent. If the guard ever widens enough to exempt this, the
// program exits non-zero and says so.
//
// ⚠️ MODELLED, NOT CAPTURED: there is no browser here, so the antialiased glyph
// coverage is synthesised (a blend ramp along each glyph edge). That is what
// antialiasing does, but it is a model. The claim it supports is deliberately
// coarse enough to survive the modelling: not "a real canvas has exactly K
// colours", but "a real canvas has HUNDREDS while a probe has tens, so the two
// populations are separated by more than an order of magnitude".

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <functional>
#include <set>
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

// A realistic fingerprinting canvas: the shape fingerprintjs/CreepJS actually
// draw — a colour gradient background, overlaid antialiased text glyphs, plus a
// blended shape. Antialiasing is what makes the distinct-colour count explode.
std::vector<uint32_t> RealisticFingerprintCanvas(int w, int h) {
  std::vector<uint32_t> buf((size_t)w * h);
  for (int y = 0; y < h; ++y) {
    for (int x = 0; x < w; ++x) {
      // Gradient background.
      double fx = (double)x / std::max(1, w - 1);
      double fy = (double)y / std::max(1, h - 1);
      double r = 30 + 200 * fx;
      double g = 60 + 150 * fy;
      double b = 200 - 120 * fx * fy;

      // Antialiased "glyph" strokes: several sinusoidal stems whose coverage
      // ramps smoothly, exactly as font rasterisation blends edge pixels.
      for (int stem = 0; stem < 6; ++stem) {
        double cx = (stem + 0.5) * w / 6.0 + 6.0 * std::sin(y * 0.15 + stem);
        double d = std::fabs(x - cx);
        double cov = std::max(0.0, 1.0 - d / 3.5);  // 0..1 coverage ramp
        if (cov > 0) {
          r = r * (1 - cov) + 250 * cov;
          g = g * (1 - cov) + 245 * cov;
          b = b * (1 - cov) + 235 * cov;
        }
      }
      // A blended ellipse, the "globalCompositeOperation" shape these scripts add.
      double ex = (x - w * 0.65) / (w * 0.22);
      double ey = (y - h * 0.5) / (h * 0.35);
      double e = ex * ex + ey * ey;
      if (e < 1.0) {
        double cov = 0.55 * (1.0 - std::sqrt(e));
        r = r * (1 - cov) + 20 * cov;
        g = g * (1 - cov) + 190 * cov;
        b = b * (1 - cov) + 120 * cov;
      }
      buf[(size_t)y * w + x] =
          Px(255, (unsigned)std::clamp(r, 0.0, 255.0),
             (unsigned)std::clamp(g, 0.0, 255.0), (unsigned)std::clamp(b, 0.0, 255.0));
    }
  }
  return buf;
}

size_t DistinctColors(const std::vector<uint32_t>& b) {
  return std::set<uint32_t>(b.begin(), b.end()).size();
}

int Diff(const std::vector<uint32_t>& a, const std::vector<uint32_t>& b) {
  int n = 0;
  for (size_t i = 0; i < a.size(); ++i)
    if (a[i] != b[i]) ++n;
  return n;
}

int NoiseOn(const std::vector<uint32_t>& orig, int w, int h, const char* seed) {
  g_seed = seed;
  std::vector<uint32_t> work = orig;
  ImageInfo info{w, h, kRGBA_8888_SkColorType, (size_t)w * 4};
  ShuffleSubchannelColorData(work.data(), info);
  return Diff(orig, work);
}

}  // namespace

int main() {
  int failures = 0;

  printf("== the two populations, counted ==\n");
  struct { int w, h; } sizes[] = {{220, 30}, {280, 60}, {300, 150}, {500, 200}};
  for (const auto& s : sizes) {
    auto c = RealisticFingerprintCanvas(s.w, s.h);
    printf("   realistic fp canvas %3dx%-3d : %6zu distinct colours\n", s.w, s.h,
           DistinctColors(c));
  }
  printf("   pixelscan reference probe   :     14 distinct colours (per feder RE)\n");
  printf("   guard ceiling kMaxRefColors :     16\n");

  printf("\n== REGRESSION: real fingerprinting canvases must STILL be noised ==\n");
  for (const auto& s : sizes) {
    auto c = RealisticFingerprintCanvas(s.w, s.h);
    int n = NoiseOn(c, s.w, s.h, "12345");
    bool ok = n > 0;
    if (!ok) failures++;
    printf("   %3dx%-3d modified = %-3d  %s\n", s.w, s.h, n,
           ok ? "OK (protection preserved)" : "*** FAIL: NOISE LOST ***");
  }

  printf("\n== REGRESSION: noise must still be seed-dependent (unlinkability) ==\n");
  {
    auto c = RealisticFingerprintCanvas(300, 150);
    std::vector<uint32_t> a = c, b = c, a2 = c;
    ImageInfo info{300, 150, kRGBA_8888_SkColorType, 300 * 4};
    g_seed = "seed-AAA"; ShuffleSubchannelColorData(a.data(), info);
    g_seed = "seed-BBB"; ShuffleSubchannelColorData(b.data(), info);
    g_seed = "seed-AAA"; ShuffleSubchannelColorData(a2.data(), info);
    bool differs = (a != b), stable = (a == a2);
    if (!differs) failures++;
    if (!stable) failures++;
    printf("   two different seeds differ : %s\n", differs ? "yes OK" : "*** FAIL ***");
    printf("   same seed reproduces       : %s\n", stable ? "yes OK" : "*** FAIL ***");
  }

  printf("\n== REGRESSION: reference-like renders must be byte-exact ==\n");
  {
    // 14 solid bands in a 70x5 canvas — the probe geometry.
    std::vector<uint32_t> p((size_t)70 * 5);
    for (int y = 0; y < 5; ++y)
      for (int x = 0; x < 70; ++x) {
        int band = std::min(13, (x * 14) / 70);
        p[(size_t)y * 70 + x] = Px(255, 40 + band * 13, 90 + band * 7, 150 - band * 5);
      }
    int n = NoiseOn(p, 70, 5, "12345");
    if (n != 0) failures++;
    printf("   70x5 / 14 colours modified = %-3d %s\n", n,
           n == 0 ? "OK (passes equality probe)" : "*** FAIL ***");
  }

  printf("\n%s\n", failures == 0
                       ? "ALL CHECKS PASSED: probe exempted AND protection preserved."
                       : "*** REGRESSIONS PRESENT — the guard is too wide ***");
  return failures == 0 ? 0 : 1;
}
