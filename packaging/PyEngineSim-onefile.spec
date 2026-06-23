# PyInstaller spec — ONEFILE build (a single self-contained PyEngineSim.exe).
# Convenient to hand around, but slower to launch: every run unpacks the whole
# ~94 MB bundle to a temp dir first.  For fast startup use PyEngineSim.spec
# (onedir).  Build:  pyinstaller packaging/PyEngineSim-onefile.spec

import os
import sys
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [], [], []
datas += [(os.path.join(ROOT, "engine_sim", "assets"), "engine_sim/assets")]
for pkg in ("numpy", "scipy", "sounddevice", "pygame", "soundfile"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["scipy.signal", "tkinter",
                                   "tkinter.filedialog"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "PyQt5", "PySide2", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# ONEFILE: binaries + datas are packed INTO the exe (no COLLECT step).
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="PyEngineSim",
    debug=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="PyEngineSim.app",
        icon=None,
        bundle_identifier="com.pyenginesim.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "Audio output only.",
        },
    )
