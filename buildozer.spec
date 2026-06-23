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
version = 0.1
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
# Use a PRE-EXTRACTED NDK so p4a never runs its (no-timeout, stall-prone)
# downloader — we fetch + unzip it ourselves with wget retries.
android.ndk_path = /root/.buildozer/android/platform/android-ndk-r28c

[buildozer]
log_level = 2
# allow running under root (WSL build box); buildozer otherwise refuses
warn_on_root = 0
