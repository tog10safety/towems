# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
datas = [
    (str(root / "resources" / "logo.png"), "resources"),
    (str(root / "resources" / "logo.ico"), "resources"),
]
hiddenimports = []
for package in (
    "keyring.backends",
    "keyring.backends.Windows",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.sql.default_comparator",
):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

a = Analysis(
    [str(root / "run_towems.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "PySide6.QtWebEngineCore"],
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
    name="TOWEMS-Armory",
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
