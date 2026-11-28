# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
datas = [
    (str(root / "dist" / "TOWEMS-Armory.exe"), "payload"),
    (str(root / "docs"), "docs"),
    (str(root / "README.md"), "."),
    (str(root / "resources" / "logo.png"), "resources"),
    (str(root / "resources" / "logo.ico"), "resources"),
]

a = Analysis(
    [str(root / "installer.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "shiboken6",
        "sqlalchemy",
        "reportlab",
        "openpyxl",
        "keyring",
        "requests",
        "pytest",
        "matplotlib",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TOWEMS-Armory-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "resources" / "logo.ico"),
)
