"""Declare UTF-8 as the active code page in a Windows PE executable's manifest.

The flet-built runner embeds Python whose locale encoding is the process ANSI
code page (cp1251 on a Russian install). PYTHONUTF8 set at runtime can't fix an
already-started interpreter, and the flet launcher gives us no hook to set env
vars before Py_Initialize on a fresh launch. The supported per-application
switch is the activeCodePage manifest element (Win10 1903+): with it GetACP()
returns 65001, Python's locale encoding becomes UTF-8, and every open()/pipe
without an explicit encoding — including flet's console.log stdout redirect —
speaks UTF-8, so em-dashes, ellipses and Cyrillic profile names survive.

Merges the element into the exe's existing manifest (keeping DPI awareness
etc.) with the Win32 resource-update API. Stdlib only, CI-friendly.
"""
import ctypes
import sys
from ctypes import wintypes

RT_MANIFEST = 24
MANIFEST_ID = 1
LOAD_LIBRARY_AS_DATAFILE = 0x2

ACTIVE_CODE_PAGE = (
    '<activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/'
    'WindowsSettings">UTF-8</activeCodePage>'
)
SETTINGS_BLOCK = (
    '<application xmlns="urn:schemas-microsoft-com:asm.v3">'
    f"<windowsSettings>{ACTIVE_CODE_PAGE}</windowsSettings></application>"
)

MAKEINTRESOURCE = lambda i: ctypes.cast(i, wintypes.LPCWSTR)  # noqa: E731

# Resource type/name reach the callback as MAKEINTRESOURCE integers; declaring
# them LPCWSTR would make ctypes dereference pointer value 24 and crash.
_ENUM_LANG_CB = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p,
    ctypes.c_void_p, wintypes.WORD, wintypes.LPARAM,
)


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LoadLibraryExW.restype = wintypes.HMODULE
    k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    k32.FreeLibrary.argtypes = [wintypes.HMODULE]
    k32.EnumResourceLanguagesW.restype = wintypes.BOOL
    k32.EnumResourceLanguagesW.argtypes = [
        wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR,
        _ENUM_LANG_CB, wintypes.LPARAM,
    ]
    k32.FindResourceW.restype = wintypes.HANDLE
    k32.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR]
    k32.SizeofResource.restype = wintypes.DWORD
    k32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    k32.LoadResource.restype = wintypes.HANDLE
    k32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    k32.LockResource.restype = wintypes.LPVOID
    k32.LockResource.argtypes = [wintypes.HANDLE]
    k32.BeginUpdateResourceW.restype = wintypes.HANDLE
    k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    k32.UpdateResourceW.restype = wintypes.BOOL
    k32.UpdateResourceW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.WORD, wintypes.LPVOID, wintypes.DWORD,
    ]
    k32.EndUpdateResourceW.restype = wintypes.BOOL
    k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    return k32


def read_manifest(exe_path):
    """Return (manifest_bytes, language_id) of the exe's RT_MANIFEST #1."""
    k32 = _kernel32()
    module = k32.LoadLibraryExW(exe_path, None, LOAD_LIBRARY_AS_DATAFILE)
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        lang = wintypes.WORD(0)

        @_ENUM_LANG_CB
        def on_lang(_h, _type, _name, lang_id, _param):
            lang.value = lang_id
            return False  # first language is enough

        k32.EnumResourceLanguagesW(
            module, MAKEINTRESOURCE(RT_MANIFEST), MAKEINTRESOURCE(MANIFEST_ID),
            on_lang, 0,
        )

        res = k32.FindResourceW(
            module, MAKEINTRESOURCE(MANIFEST_ID), MAKEINTRESOURCE(RT_MANIFEST)
        )
        if not res:
            raise ctypes.WinError(ctypes.get_last_error())
        size = k32.SizeofResource(module, res)
        data = k32.LoadResource(module, res)
        ptr = k32.LockResource(data)
        return ctypes.string_at(ptr, size), lang.value
    finally:
        k32.FreeLibrary(module)


def merged_manifest(manifest: str) -> str:
    if "activeCodePage" in manifest:
        return manifest
    if "</windowsSettings>" in manifest:
        return manifest.replace(
            "</windowsSettings>", ACTIVE_CODE_PAGE + "</windowsSettings>", 1
        )
    if "</assembly>" in manifest:
        return manifest.replace("</assembly>", SETTINGS_BLOCK + "</assembly>", 1)
    raise RuntimeError("manifest has no </windowsSettings> or </assembly>")


def write_manifest(exe_path, manifest_bytes, lang):
    k32 = _kernel32()
    h = k32.BeginUpdateResourceW(exe_path, False)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    buf = ctypes.create_string_buffer(manifest_bytes, len(manifest_bytes))
    if not k32.UpdateResourceW(
        h, MAKEINTRESOURCE(RT_MANIFEST), MAKEINTRESOURCE(MANIFEST_ID),
        lang, buf, len(manifest_bytes),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if not k32.EndUpdateResourceW(h, False):
        raise ctypes.WinError(ctypes.get_last_error())


def set_utf8_code_page(exe_path):
    raw, lang = read_manifest(exe_path)
    manifest = raw.decode("utf-8")
    merged = merged_manifest(manifest)
    if merged == manifest:
        print("activeCodePage already declared:", exe_path)
        return
    write_manifest(exe_path, merged.encode("utf-8"), lang)
    check, _ = read_manifest(exe_path)
    if b"activeCodePage" not in check:
        raise RuntimeError("manifest update did not stick")
    print("activeCodePage=UTF-8 declared:", exe_path)


if __name__ == "__main__":
    set_utf8_code_page(sys.argv[1])
