// skia_shim.h — the MINIMUM of Skia that ShuffleSubchannelColorData's body touches.
//
// ⛔ THIS IS NOT VERBATIM AND MUST NOT BE READ AS SUCH. The loop body compiled
// beside it (extracted_shuffle_body.inc) IS verbatim from our patch; this file is
// a reconstruction of the Skia surface that body calls into, written because
// Chromium's tree is not present in an agent container.
//
// Correctness of the shim is not asserted, it is TESTED: probe_harness.cc runs
// SelfTestShim() before any measurement, which round-trips known ARGB values
// through pack/unpack for every colour type the loop handles and aborts on the
// first disagreement. A shim that mis-orders a channel would make every number
// downstream meaningless, so the harness refuses to measure until it round-trips.
//
// Byte order follows Skia's documented convention: kRGBA_8888 stores R at the
// lowest address, and on the little-endian hosts this project targets that makes
// SK_R32_SHIFT == 0. kBGRA_8888 swaps R and B.

#ifndef PS373_SKIA_SHIM_H_
#define PS373_SKIA_SHIM_H_

#include <cstdint>
#include <cstdlib>
#include <cstdio>

enum SkColorType {
  kUnknown_SkColorType = 0,
  kAlpha_8_SkColorType,
  kRGB_565_SkColorType,
  kARGB_4444_SkColorType,
  kRGBA_8888_SkColorType,
  kBGRA_8888_SkColorType,
  kGray_8_SkColorType,
};

// ---- 8888 shifts (little-endian) -------------------------------------------
#define SK_R32_SHIFT 0
#define SK_G32_SHIFT 8
#define SK_B32_SHIFT 16
#define SK_A32_SHIFT 24

#define SK_BGRA_B32_SHIFT 0
#define SK_BGRA_G32_SHIFT 8
#define SK_BGRA_R32_SHIFT 16
#define SK_BGRA_A32_SHIFT 24

static inline unsigned SkGetPackedR32(uint32_t p) { return (p >> SK_R32_SHIFT) & 0xFF; }
static inline unsigned SkGetPackedG32(uint32_t p) { return (p >> SK_G32_SHIFT) & 0xFF; }
static inline unsigned SkGetPackedB32(uint32_t p) { return (p >> SK_B32_SHIFT) & 0xFF; }
static inline unsigned SkGetPackedA32(uint32_t p) { return (p >> SK_A32_SHIFT) & 0xFF; }

// ---- 565 --------------------------------------------------------------------
#define SK_R16_SHIFT 11
#define SK_G16_SHIFT 5
#define SK_B16_SHIFT 0
#define SK_R16_MASK  0x1F
#define SK_G16_MASK  0x3F
#define SK_B16_MASK  0x1F

static inline unsigned SkPacked16ToR32(uint16_t p) { return (p >> SK_R16_SHIFT) & SK_R16_MASK; }
static inline unsigned SkPacked16ToG32(uint16_t p) { return (p >> SK_G16_SHIFT) & SK_G16_MASK; }
static inline unsigned SkPacked16ToB32(uint16_t p) { return (p >> SK_B16_SHIFT) & SK_B16_MASK; }

// ---- 4444 -------------------------------------------------------------------
#define SK_A4444_SHIFT 0
#define SK_R4444_SHIFT 12
#define SK_G4444_SHIFT 8
#define SK_B4444_SHIFT 4

static inline unsigned SkGetPackedA4444(uint16_t p) { return (p >> SK_A4444_SHIFT) & 0xF; }
static inline unsigned SkGetPackedR4444(uint16_t p) { return (p >> SK_R4444_SHIFT) & 0xF; }
static inline unsigned SkGetPackedG4444(uint16_t p) { return (p >> SK_G4444_SHIFT) & 0xF; }
static inline unsigned SkGetPackedB4444(uint16_t p) { return (p >> SK_B4444_SHIFT) & 0xF; }

// ---- Alpha_8 / Gray_8 helpers the loop calls --------------------------------
// NOTE: the loop calls SkColorGetR/G/B on a *uint8_t* in the kAlpha_8 arm. That
// is our patch's own oddity (an 8-bit alpha buffer has no RGB), faithfully
// preserved: SkColor is a uint32 and the promotion yields r=value, g=b=0.
typedef uint32_t SkColor;
static inline unsigned SkColorGetA(SkColor c) { return (c >> 24) & 0xFF; }
static inline unsigned SkColorGetR(SkColor c) { return (c >> 16) & 0xFF; }
static inline unsigned SkColorGetG(SkColor c) { return (c >>  8) & 0xFF; }
static inline unsigned SkColorGetB(SkColor c) { return (c >>  0) & 0xFF; }
static inline SkColor SkColorSetARGB(unsigned a, unsigned r, unsigned g, unsigned b) {
  return (a << 24) | (r << 16) | (g << 8) | b;
}

// ---- the addressing macro the loop uses -------------------------------------
#define writable_addr(type, addr, rowbytes, x, y) \
  ((type*)((char*)(addr) + (size_t)(y) * (size_t)(rowbytes) + (size_t)(x) * sizeof(type)))

#endif  // PS373_SKIA_SHIM_H_
