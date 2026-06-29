# PyInstaller spec -- builds a single windowed KarbonDashboard.exe
# Build with:  python -m PyInstaller build.spec --noconfirm
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Absolute paths so the build works from any working directory.
SRC = os.path.join(SPECPATH, "src")
sys.path.insert(0, SRC)  # let collect_submodules find our packages

hiddenimports = (
    collect_submodules("data")
    + collect_submodules("ui")
    + ["config"]
)

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Trim large, unused optional backends to keep the exe smaller.
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "PIL.ImageQt", "tornado", "pytest"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="KarbonDashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # windowed app, no console window
    disable_windowed_traceback=False,
)
