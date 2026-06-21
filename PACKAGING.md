# Packaging PyEngineSim

The app is a pygame + numpy/scipy + sounddevice Python program (`run.py` →
`engine_sim/app.py`). Here is the honest state of each target.

## Touch controls
Already built in: the **Touch** toolbar button (auto-enables when the screen is
first touched) shows on-screen GAS/BRAKE pedals, UP/DOWN shift paddles and
START/IGN/AUTO buttons, with full multi-touch. So the app is finger-playable on
any touchscreen (Windows tablet, touch laptop, and any mobile build).

## Desktop — Windows / macOS / Linux  ✅ easy
PyInstaller bundles Python + all libs into one app. **Build on the target OS**
(PyInstaller does not cross-compile):

```bash
pip install pyinstaller
pyinstaller packaging/PyEngineSim.spec
```

- **Windows** → `dist/PyEngineSim.exe` (double-click, no Python needed).
- **macOS** → `dist/PyEngineSim.app` (run on a Mac; to ship it outside your own
  machine you must codesign + notarize with an Apple Developer ID).
- **Linux** → `dist/PyEngineSim` binary.

Drop an `.ico` (Windows) / `.icns` (macOS) path into the spec's `icon=` for a
custom icon. (The existing `PyEngineSim_portable.zip` is already a no-install PC
distribution — PyInstaller just makes it a single clean executable.)

## Android  ⚠️ feasible, but needs a small porting pass
Two libraries don't run on Android as-is:

1. **sounddevice** (PortAudio) — not on Android. Swap the `OutputStream`
   callback in `audio.py` for a **pygame.mixer** streamed buffer (pygame-ce runs
   on Android) or `audiostream`.
2. **scipy** — builds painfully (or not at all) under python-for-android. Either
   keep it in `requirements` (slow, may fail) or **drop scipy** and add a
   pure-numpy biquad `lfilter` — every scipy call in `audio.py` is already
   guarded by `_HAVE_SCIPY`, so the hooks exist.

Then build on Linux / WSL / Docker:

```bash
pip install buildozer cython
buildozer -v android debug        # -> bin/PyEngineSim-*.apk
buildozer android deploy run      # install + launch on a USB phone
```

Config is in `packaging/buildozer.spec`. Estimate: ~1 day for the audio-backend
+ numpy-DSP swap, then the apk builds.

## iOS  ❌ not practical with this stack
There is no supported path to run pygame + real-time Python audio on iOS, and
the App Store requires a Mac, Xcode and a paid Apple Developer account ($99/yr).
**Recommended instead for true cross-platform mobile: a web / PWA port** — the
UI to HTML5 Canvas and the DSP to a WebAudio `AudioWorklet`. That runs in iOS
Safari and Android Chrome and installs to the home screen as an app, with no
store needed. It is a separate reimplementation (the physics/DSP math ports
directly; the realtime layer is rewritten in JS/WASM).

## Summary
| Target | Status | How |
|---|---|---|
| Touchscreen | ✅ done | Touch toolbar button |
| Windows .exe | ✅ easy | `pyinstaller` on Windows |
| macOS .app | ✅ easy | `pyinstaller` on a Mac (+ sign/notarize to share) |
| Linux | ✅ easy | `pyinstaller` on Linux |
| Android .apk | ⚠️ ~1 day port | swap audio backend + numpy DSP, then `buildozer` |
| iOS | ❌ rewrite | do a web/PWA port instead |
