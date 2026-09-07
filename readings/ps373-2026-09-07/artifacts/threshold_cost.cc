// threshold_cost.cc — what does a distinct-colour guard COST?
//
// distinct_sweep.cc established two things by execution:
//   (1) our loop already returns a 1-colour render byte-exact, implicitly, because
//       a uniform buffer contains no `isEdge` pixel. Our implicit tolerance is
//       therefore exactly ONE distinct colour.
//   (2) feder's explicit guard tolerates SIXTEEN (`kMaxRefColors`), and pixelscan's
//       probe is reported to use FOURTEEN — which lands in the gap between the two.
//
// Raising our tolerance from 1 to 16 would close that gap. ⛔ But PS-373 carries an
// explicit trap: *"any recommendation must preserve or improve what is masked"* —
// a guard that also exempts genuine fingerprinting canvases trades real protection
// for a green badge, and must be refused.
//
// So this file measures the COST side, which is the half that decides whether the
// guard is admissible at all. It asks: how many distinct colours does a REAL
// fingerprinting canvas contain?
//
// THE MODEL, stated honestly. There is no browser here to render text with, so
// antialiased glyph coverage is MODELLED: a glyph edge blends foreground into
// background across N coverage steps, and each step is a distinct colour. That is
// what antialiasing does by definition, and it is why the count explodes — but it
// is a model, not a capture, and it is labelled as one everywhere it is used.
// The conclusion it supports is deliberately weak enough to survive that: not "the
// count is exactly K", but "the two populations are separated by orders of
// magnitude, so any threshold in the low tens distinguishes them".

#include <cstdio>
#include <set>
#include <string>
#include <vector>

int main() {
  printf("== distinct-colour counts: reference renders vs real fingerprint canvases ==\n\n");

  printf("-- population A: reference / probe renders (what a detector fills) --\n");
  struct { const char* what; int colours; } refs[] = {
      {"solid single fill", 1},
      {"two-tone test pattern", 2},
      {"pixelscan canvasNoiseOn2d probe (per feder RE)", 14},
      {"feder's kMaxRefColors ceiling", 16},
  };
  for (const auto& r : refs)
    printf("   %-48s %4d\n", r.what, r.colours);

  printf("\n-- population B: antialiased text canvas (MODELLED, not captured) --\n");
  printf("   model: a glyph edge blends fg->bg over `steps` coverage levels;\n");
  printf("   each level is a distinct RGB triple. Counting unique triples.\n\n");
  printf("   %-14s %-14s %s\n", "fg/bg pairs", "AA steps", "distinct colours");
  for (int pairs : {1, 2, 3}) {
    for (int steps : {8, 16, 32, 64, 256}) {
      std::set<unsigned> seen;
      for (int p = 0; p < pairs; ++p) {
        // Arbitrary but distinct foreground/background pairs.
        int fr = 20 + p * 60, fg = 200 - p * 40, fb = 120 + p * 30;
        int br = 240 - p * 50, bg = 30 + p * 70, bb = 200 - p * 60;
        for (int s = 0; s <= steps; ++s) {
          double t = (double)s / steps;
          unsigned r = (unsigned)(fr + (br - fr) * t);
          unsigned g = (unsigned)(fg + (bg - fg) * t);
          unsigned b = (unsigned)(fb + (bb - fb) * t);
          seen.insert((r << 16) | (g << 8) | b);
        }
      }
      printf("   %-14d %-14d %zu\n", pairs, steps, seen.size());
    }
  }

  printf("\n== the separation, and what threshold it admits ==\n");
  printf("   population A tops out at %d distinct colours (feder's own ceiling).\n", 16);
  printf("   population B starts, on the WEAKEST antialiasing modelled here\n");
  printf("   (1 colour pair, 8 coverage steps), at 9 and rises steeply.\n\n");
  printf("   ⚠️ THE POPULATIONS TOUCH at the weak end: 8-step AA on a single\n");
  printf("   colour pair yields 9, which is BELOW feder's 16. So a threshold of\n");
  printf("   16 is NOT free — it would exempt a hypothetical canvas drawn with a\n");
  printf("   single glyph in one colour at coarse AA. Whether such a canvas is a\n");
  printf("   real fingerprinting surface is a question for the host, not for me.\n");
  printf("   A conservative threshold (i.e. materially lower than 16) buys the\n");
  printf("   probe exemption with a strictly smaller exposed population.\n");
  return 0;
}
