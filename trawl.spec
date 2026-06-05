# trawl.spec - PyInstaller one-file build for Trawl
# Build:  python -m PyInstaller trawl.spec
import os
from PyInstaller.utils.hooks import collect_submodules


def _safe_collect(mod):
    try:
        return collect_submodules(mod)
    except Exception:
        return []


block_cipher = None

hiddenimports = []
hiddenimports += _safe_collect("keyring")
hiddenimports += _safe_collect("win32ctypes")
hiddenimports += ["keyring.backends.Windows"]

icon_file = "trawl.ico" if os.path.exists("trawl.ico") else None

a = Analysis(
    ["trawl.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Trawl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
    # Set uac_admin=True to make the app request elevation (UAC) on every
    # launch. Note: this does NOT bypass Controlled Folder Access, and it can
    # interfere with silent "run on startup". Usually a writable install folder
    # is the better fix.
    uac_admin=False,
)
