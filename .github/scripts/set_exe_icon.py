"""Embed a multi-resolution icon into a Windows PE executable's resources.

flet build (as of flet 0.85) leaves the default Flutter arrow icon in the
compiled Windows runner even though flutter_launcher_icons reports success, so
we stamp the real icon into the finished .exe with the Win32 resource-update
API. Standalone: only stdlib + Pillow, no external tools, CI-friendly.
"""
import struct
import sys
import ctypes
from ctypes import wintypes
from io import BytesIO

from PIL import Image

ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]
RT_ICON = 3
RT_GROUP_ICON = 14
LANG_NEUTRAL = 0


def _png_to_ico_images(src_png):
    """Return a list of (width, height, bmp_or_png_bytes) for each icon size.

    256px frames are stored as PNG (Vista+ compressed icon), smaller frames as
    32-bit BMP (DIB) which every Windows shell reads.
    """
    base = Image.open(src_png).convert("RGBA")
    frames = []
    for size in ICON_SIZES:
        img = base.resize((size, size), Image.LANCZOS)
        if size >= 256:
            buf = BytesIO()
            img.save(buf, format="PNG")
            frames.append((size, size, buf.getvalue()))
        else:
            frames.append((size, size, _rgba_to_dib(img)))
    return frames


def _rgba_to_dib(img):
    """Build a bottom-up BITMAPINFOHEADER DIB with doubled height (XOR+AND masks)."""
    w, h = img.size
    px = img.load()
    # BITMAPINFOHEADER: biHeight is doubled (color + mask) for icon DIBs.
    header = struct.pack(
        "<IiiHHIIiiII",
        40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0,
    )
    xor = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))
    # AND mask: 1bpp, row-padded to 32 bits; all zero = use alpha channel.
    and_row = (w + 31) // 32 * 4
    and_mask = bytes(and_row * h)
    return bytes(header) + bytes(xor) + and_mask


def _grpicondir(frames):
    """RT_GROUP_ICON directory referencing each RT_ICON by id (1..n)."""
    out = struct.pack("<HHH", 0, 1, len(frames))
    for idx, (w, h, data) in enumerate(frames, start=1):
        bw = 0 if w >= 256 else w
        bh = 0 if h >= 256 else h
        # bWidth,bHeight,bColorCount,bReserved,wPlanes,wBitCount,dwBytes,nID
        out += struct.pack("<BBBBHHIH", bw, bh, 0, 0, 1, 32, len(data), idx)
    return out


def set_icon(exe_path, src_png):
    frames = _png_to_ico_images(src_png)

    BeginUpdateResource = ctypes.windll.kernel32.BeginUpdateResourceW
    BeginUpdateResource.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    BeginUpdateResource.restype = wintypes.HANDLE
    UpdateResource = ctypes.windll.kernel32.UpdateResourceW
    UpdateResource.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.WORD, wintypes.LPVOID, wintypes.DWORD,
    ]
    UpdateResource.restype = wintypes.BOOL
    EndUpdateResource = ctypes.windll.kernel32.EndUpdateResourceW
    EndUpdateResource.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    EndUpdateResource.restype = wintypes.BOOL

    MAKEINTRESOURCE = lambda i: ctypes.cast(i, wintypes.LPCWSTR)

    h = BeginUpdateResource(exe_path, False)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())

    # Each RT_ICON gets id 1..n; the flutter runner uses group id 101 (#101).
    for idx, (_, _, data) in enumerate(frames, start=1):
        buf = ctypes.create_string_buffer(data, len(data))
        if not UpdateResource(h, MAKEINTRESOURCE(RT_ICON), MAKEINTRESOURCE(idx),
                              LANG_NEUTRAL, buf, len(data)):
            raise ctypes.WinError(ctypes.get_last_error())

    grp = _grpicondir(frames)
    gbuf = ctypes.create_string_buffer(grp, len(grp))
    if not UpdateResource(h, MAKEINTRESOURCE(RT_GROUP_ICON), MAKEINTRESOURCE(101),
                          LANG_NEUTRAL, gbuf, len(grp)):
        raise ctypes.WinError(ctypes.get_last_error())

    if not EndUpdateResource(h, False):
        raise ctypes.WinError(ctypes.get_last_error())


if __name__ == "__main__":
    set_icon(sys.argv[1], sys.argv[2])
    print("icon embedded:", sys.argv[1])
