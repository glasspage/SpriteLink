# -*- mode: python ; coding: utf-8 -*-

import os


version_file = os.environ.get("SPRITELINK_VERSION_FILE")

a = Analysis(
    ["SpriteLink.pyw"],
    pathex=[],
    binaries=[],
    datas=[
        ("SL.ico", "."),
        ("sounds", "sounds"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SpriteLink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=["SL.ico"],
    version=version_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SpriteLink",
)

