"""Generate branded Inno Setup wizard images from the app icon.

Produces two BMPs the modern-style wizard uses:
  - wizard-large.bmp (164x314): the left welcome/finish panel — dark persona
    background with the logo and wordmark and an accent bar.
  - wizard-small.bmp (55x58): the small header logo shown on inner pages.

Kept deliberately simple (no external fonts) so it runs in CI with only Pillow.
"""
import sys

from PIL import Image, ImageDraw, ImageFont

# persona's app theme: pure black with a lime accent (src/ui/theme/colors.py).
# The banner must match WizardImageBackColor=$00000000 so there's no seam
# between the banner panel and the wizard's dark chrome.
BG_TOP = (0, 0, 0)         # #000000 — persona bg
BG_BOTTOM = (10, 10, 10)   # #0A0A0A — a hair lighter for a subtle gradient
ACCENT = (168, 255, 63)    # #A8FF3F — lime accent
TEXT = (255, 255, 255)     # #FFFFFF
SUBTLE = (188, 188, 188)   # #BCBCBC


def _vertical_gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", size, top)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _paste_logo(canvas, icon_path, box_w, y, target):
    logo = Image.open(icon_path).convert("RGBA")
    logo.thumbnail((target, target), Image.LANCZOS)
    x = (box_w - logo.width) // 2
    canvas.paste(logo, (x, y), logo)
    return logo.height


def make_large(icon_path, out_path):
    w, h = 164, 314
    img = _vertical_gradient((w, h), BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(img)
    # logo near the top third
    logo_h = _paste_logo(img, icon_path, w, 70, 84)
    # wordmark under the logo, centered. Use a larger default font so the name
    # reads as a title, not fine print (Pillow >=10 supports load_default(size)).
    try:
        text_y = 70 + logo_h + 18
        try:
            title_font = ImageFont.load_default(22)
            sub_font = ImageFont.load_default(11)
        except Exception:
            title_font = sub_font = None
        tw = draw.textlength("persona", font=title_font)
        draw.text(((w - tw) / 2, text_y), "persona", fill=TEXT, font=title_font)
        sub = "anti-detect browser"
        sw = draw.textlength(sub, font=sub_font)
        draw.text(((w - sw) / 2, text_y + 30), sub, fill=SUBTLE, font=sub_font)
    except Exception:
        pass
    # accent bar at the bottom
    draw.rectangle([(0, h - 4), (w, h)], fill=ACCENT)
    img.convert("RGB").save(out_path, "BMP")


def make_small(icon_path, out_path):
    w, h = 55, 58
    img = _vertical_gradient((w, h), BG_TOP, BG_BOTTOM).convert("RGBA")
    _paste_logo(img, icon_path, w, 6, 46)
    img.convert("RGB").save(out_path, "BMP")


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: make_wizard_images.py <icon.png> <large.bmp> <small.bmp>")
        return 2
    icon, large, small = sys.argv[1], sys.argv[2], sys.argv[3]
    make_large(icon, large)
    make_small(icon, small)
    print(f"wrote {large} and {small}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
