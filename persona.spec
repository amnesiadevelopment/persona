# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

home = os.path.expanduser("~")
flet_client = os.path.join(home, ".flet", "client", "flet-desktop-light-0.85.3")

datas = [
    ("src/assets", "src/assets"),
]
# bundle the downloaded flutter client so the app is self-contained
if os.path.isdir(flet_client):
    datas.append((flet_client, "flet_client"))

a = Analysis(
    ["src/main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=["flet", "flet_desktop", "uvicorn", "fastapi", "aiohttp", "cryptography"],
    hookspath=[],
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
