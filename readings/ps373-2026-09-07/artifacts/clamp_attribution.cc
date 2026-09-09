// clamp_attribution.cc — is the `< 2` clamp actually the culprit round 3 named?
//
// PS-373 round 3's headline mechanism is a specific, quotable claim:
//
//     "On pixelscan's 70x5 probe: (70*5)/128 = 2. The `< 2` branch CLAMPS UP to 2.
//      ⭐⭐ The clamp that exists to guarantee a minimum amount of noise is
//      precisely what guarantees we modify the detector's reference probe."
//
// The FINDING (we modify the probe) is confirmed by probe_harness.cc. This file
// tests the ATTRIBUTION, which is a separate claim and the one a fix would be
// built on. It runs the loop twice on identical input: once with the shipped
// clamp, once with the clamp removed. If the clamp is the culprit, removing it
// must change the outcome at 70x5.
//
// This matters because a fix aimed at the wrong mechanism ships, looks principled,
// and does nothing — and the ticket's whole A/B 6 is built on the attribution.

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

// Budget arithmetic, isolated from the loop so both variants share one loop.
int BudgetShipped(int w, int h) {
  auto max_pixels = (w * h) / 128;
  if (max_pixels > 10) max_pixels = 10;
  else if (max_pixels < 2) max_pixels = 2;   // the clause under suspicion
  return max_pixels;
}
int BudgetNoClampUp(int w, int h) {
  auto max_pixels = (w * h) / 128;
  if (max_pixels > 10) max_pixels = 10;
  return max_pixels;                          // may legitimately be 0 or 1
}

// The loop, with the budget injected rather than computed, so the ONLY difference
// between the two arms is the budget. Body is otherwise our patch's shape.
int RunWithBudget(int w, int h, int budget, int bands) {
  std::vector<uint32_t> orig((size_t)w * h), work;
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w; ++x) {
      int band = (bands <= 1) ? 0 : std::min(bands - 1, (x * bands) / std::max(1, w));
      unsigned r = 40 + (unsigned)((band * 37) % 180);
      unsigned g = 40 + (unsigned)((band * 91) % 180);
      unsigned b = 40 + (unsigned)((band * 53) % 180);
      orig[(size_t)y * w + x] = (255u << SK_A32_SHIFT) | (r << SK_R32_SHIFT) |
                                (g << SK_G32_SHIFT) | (b << SK_B32_SHIFT);
    }
  work = orig;

  const size_t fRowBytes = (size_t)w * 4;
  void* addr = work.data();
  const std::string seed_str = g_seed;
  int max_pixels = budget;

  int modified_pixels = 0;
  std::unordered_set<int> processed_rows;
  std::unordered_set<int> processed_cols;
  for (int y = 0; y < h - 1 && modified_pixels < max_pixels; y++) {
    if (processed_rows.count(y)) continue;
    for (int x = 0; x < w - 1 && modified_pixels < max_pixels; x++) {
      if (processed_cols.count(x)) continue;
      auto* current = writable_addr(uint32_t, addr, fRowBytes, x, y);
      auto* right = writable_addr(uint32_t, addr, fRowBytes, x + 1, y);
      auto* bottom = writable_addr(uint32_t, addr, fRowBytes, x, y + 1);
      auto r = SkGetPackedR32(*current), g = SkGetPackedG32(*current),
           b = SkGetPackedB32(*current);
      bool isValidColor = !((r == 0 && g == 0 && b == 0) || (r == 255 && g == 255 && b == 255));
      bool isEdge = isValidColor && ((*current != *right) || (*current != *bottom));
      if (isEdge && isValidColor) {
        std::string k = seed_str + "_x" + std::to_string(x) + "_y" + std::to_string(y);
        uint8_t sr = std::hash<std::string>{}(k + "_r") & 1;
        uint8_t sg = std::hash<std::string>{}(k + "_g") & 1;
        uint8_t sb = std::hash<std::string>{}(k + "_b") & 1;
        auto a = SkGetPackedA32(*current);
        r = (r & ~0x1) | sr; g = (g & ~0x1) | sg; b = (b & ~0x1) | sb;
        *current = (a << SK_A32_SHIFT) | (r << SK_R32_SHIFT) |
                   (g << SK_G32_SHIFT) | (b << SK_B32_SHIFT);
        modified_pixels++;
        processed_rows.insert(y);
        processed_cols.insert(x);
      }
    }
  }
  int d = 0;
  for (size_t i = 0; i < orig.size(); ++i) if (orig[i] != work[i]) ++d;
  return d;
}

}  // namespace

int main() {
  printf("== Does removing the `< 2` clamp change the probe outcome? ==\n\n");
  printf("   %-10s %-8s %-10s %-10s %-9s %-9s %s\n", "size", "area", "shipped", "no-clamp",
         "mod(ship)", "mod(no)", "clamp fires?");
  struct { int w, h; const char* what; } cases[] = {
      {70, 5, "pixelscan probe"}, {16, 16, "area 256"}, {15, 15, "area 225"},
      {8, 8, "area 64"}, {100, 30, "small fp"}, {300, 150, "default"},
  };
  for (const auto& c : cases) {
    int bs = BudgetShipped(c.w, c.h), bn = BudgetNoClampUp(c.w, c.h);
    int ms = RunWithBudget(c.w, c.h, bs, 14);
    int mn = RunWithBudget(c.w, c.h, bn, 14);
    char label[32];
    snprintf(label, sizeof(label), "%dx%d", c.w, c.h);
    printf("   %-10s %-8d %-10d %-10d %-9d %-9d %-5s (%s)\n", label, c.w * c.h, bs, bn,
           ms, mn, bs != bn ? "YES" : "no", c.what);
  }

  printf("\n== verdict ==\n");
  int bs = BudgetShipped(70, 5), bn = BudgetNoClampUp(70, 5);
  printf("   At 70x5: (70*5)/128 = %d. The clause is `else if (max_pixels < 2)`,\n", (70 * 5) / 128);
  printf("   and 2 < 2 is FALSE, so the clamp DOES NOT FIRE. Shipped budget %d,\n", bs);
  printf("   budget with the clamp deleted %d — IDENTICAL.\n\n", bn);
  printf("   ⭐ So the finding stands (we modify the probe and fail it) but the\n");
  printf("   ATTRIBUTION does not: the `< 2` clamp is innocent on this geometry.\n");
  printf("   Deleting it would change nothing at 70x5. The real cause is that the\n");
  printf("   budget is a FLOOR-DIVIDED AREA with no notion of whether the render is\n");
  printf("   a reference pattern — a 350-pixel banded canvas earns a budget of 2 on\n");
  printf("   its own, without help from any clamp.\n");
  return 0;
}
