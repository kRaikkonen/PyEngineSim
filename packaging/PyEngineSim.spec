# PyInstaller spec — build a standalone desktop app (Windows .exe / macOS .app /
# Linux binary) from run.py.  Build on the TARGET OS (PyInstaller does NOT
# cross-compile): on Windows you get the .exe, on a Mac you get the .app.
#
#   pip install pyinstaller
#   pyinstaller packaging/PyEngineSim.spec
#
# Output lands in dist/.  See PACKAGING.md for full instructions.

import os
import sys
from PyInstaller.utils.hooks import collect_all

# run.py lives one level up from this spec (in the project root).
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [], [], []
# Bundle the embedded UI fonts (BankGothic-style face) so the app looks right
# even on machines where the font isn't installed.
datas += [(os.path.join(ROOT, "engine_sim", "assets"), "engine_sim/assets")]
# Pull in everything these packages need (DLLs, data, submodules) so the bundle
# is self-contained — scipy/numpy/sounddevice especially carry native libs.
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
    # tkinter is imported lazily inside a function (file dialogs) — name it
    # explicitly so PyInstaller's static scan still bundles it.
    hiddenimports=hiddenimports + ["scipy.signal", "tkinter",
                                   "tkinter.filedialog"],
    hookspath=[],
    runtime_hooks=[],
    # NOTE: tkinter MUST stay bundled — the Load/Save car & EQ buttons use
    # tkinter.filedialog for the native file picker.  Excluding it kills them.
    excludes=["matplotlib", "PyQt5", "PySide2", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="PyEngineSim",
    debug=False,
    strip=False,
    upx=True,
    console=False,            # no terminal window
    icon=None,                # drop an .ico (win) / .icns (mac) path here
)

# On macOS, wrap the binary in a proper .app bundle.
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
