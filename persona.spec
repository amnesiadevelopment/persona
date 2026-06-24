# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

# Pull in ALL of flet's package data (icons.json, controls metadata, etc.) and
# the official flet PyInstaller hook. Without this the binary builds but crashes
# at startup looking for flet/controls/material/icons.json.
flet_datas, flet_binaries, flet_hiddenimports = collect_all("flet")
flet_desktop_datas, flet_desktop_binaries, flet_desktop_hi = collect_all("flet_desktop")

# the bundled Flutter desktop client (downloaded into ~/.flet at build time)
home = os.path.expanduser("~")
flet_client = None
flet_root = os.path.join(home, ".flet", "client")
if os.path.isdir(flet_root):
    for name in os.listdir(flet_root):
        if name.startswith("flet-desktop"):
            flet_client = os.path.join(flet_root, name)
            break

datas = [("src/assets", "src/assets")]
datas += flet_datas + flet_desktop_datas
if flet_client:
    datas.append((flet_client, "flet_client"))

binaries = flet_binaries + flet_desktop_binaries

hiddenimports = list(set(
    flet_hiddenimports + flet_desktop_hi +
    ["flet", "flet_desktop", "uvicorn", "fastapi", "aiohttp", "cryptography"]
))

# the official flet hook (resolves data/runtime hooks correctly)
flet_hook_dir = None
try:
    import flet_cli, pathlib
    flet_hook_dir = str(pathlib.Path(flet_cli.__file__).parent / "__pyinstaller")
except Exception:
    pass

a = Analysis(
    ["src/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[flet_hook_dir] if flet_hook_dir else [],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="persona",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    onefile=True,
)
