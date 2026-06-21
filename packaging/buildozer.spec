# Android build config (buildozer / python-for-android).  See PACKAGING.md —
# Android needs a small PORTING pass first (this app uses sounddevice + scipy,
# neither of which builds cleanly on Android):
#
#   1. Audio backend: replace the `sounddevice` OutputStream in audio.py with a
#      pygame.mixer streamed buffer (pygame-ce works on Android) or `audiostream`.
#   2. DSP: scipy is painful on Android — either keep scipy in `requirements`
#      below (slow p4a build, may fail) OR drop it and use the pure-numpy biquad
#      path (implement an lfilter equivalent; the `_HAVE_SCIPY=False` fallback in
#      audio.py already guards every scipy call).
#
# Then, on a Linux box (or WSL/Docker):
#   pip install buildozer cython
#   buildozer -v android debug         # -> bin/*.apk
#   buildozer android deploy run       # install on a plugged-in phone

[app]
title = PyEngineSim
package.name = pyenginesim
package.domain = com.pyenginesim
source.dir = .
source.include_exts = py,png,jpg,ttf,json
version = 0.1
# scipy is optional — remove it if you took the numpy-only DSP path above.
requirements = python3,numpy,pygame,scipy
orientation = landscape
fullscreen = 1
android.archs = arm64-v8a, armeabi-v7a
android.api = 33
android.minapi = 24
# pygame on Android wants SDL2; p4a's pygame recipe pulls it in.
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
