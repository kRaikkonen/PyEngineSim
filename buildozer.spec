# Active buildozer spec — run `buildozer android debug` from THIS directory
# (engine-sim-py/).  The phone port:
#   * audio.py streams through pygame's SDL2 mixer (no PortAudio/sounddevice)
#   * scipy is dropped; audio.py's _HAVE_SCIPY guards fall back to pure numpy
#   * app.py/audio.py detect Android (ANDROID_ARGUMENT) -> touch overlay on
# Entry point is main.py in this directory.

[app]
title = PyEngineSim
package.name = pyenginesim
package.domain = com.pyenginesim
source.dir = .
source.include_exts = py,png,jpg,ttf,json,otf
source.include_patterns = engine_sim/assets/*
version = 0.2.2
# scipy DROPPED: extremely painful to cross-compile with p4a, and audio.py
# already has a pure-numpy fallback for every scipy call (_HAVE_SCIPY guards).
# Use pygame-ce (Community Edition) for better Android GLES2 support
requirements = python3,numpy,pygame-ce
orientation = landscape
fullscreen = 1
# INTERNET = the optional Forza UDP telemetry listener; harmless if unused.
android.permissions = INTERNET
# Lock to arm64-v8a only for better performance and smaller APK
android.archs = arm64-v8a
android.api = 34
android.minapi = 24
android.allow_backup = True
# Enable OpenGL ES 2.0 hardware acceleration
android.enable_gles2 = True
# Disable GL debugging for better performance
android.debug_gl = False
# Pin p4a to the v2023.09.16 release: it builds CPython 3.10 (pygame-ce needs the
# top-level longintrepr.h that 3.12 removed) and its bundled SDL2 still calls
# ALooper_pollAll, which NDK r26+ removed.  master + r28c FAILS to compile.
p4a.branch = v2023.09.16
# Use a PRE-EXTRACTED NDK r25b (last NDK keeping ALooper_pollAll) so p4a never
# runs its stall-prone downloader — android_ndk25_prep.sh fetches/unzips it.
android.ndk_path = /root/.buildozer/android/platform/android-ndk-r25b

[buildozer]
log_level = 2
# allow running under root (WSL build box); buildozer otherwise refuses
warn_on_root = 0
