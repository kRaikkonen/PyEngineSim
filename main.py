"""Android / buildozer entry point.

python-for-android launches ``main.py`` from the source root, so this is the
phone equivalent of ``run.py`` — it just starts the app with its default engine
(the Aventador).  On Android the UI defaults to the finger-control overlay and
audio streams through pygame's SDL2 mixer (see ON_ANDROID in app.py / audio.py).

Any startup crash is written to ``crash.log`` in the app's private dir (and to
stdout, which lands in logcat) — a silent instant-close is undebuggable on a
phone otherwise.
"""

import os
import sys
import traceback


def _log_crash(exc_text):
    print(exc_text, file=sys.stderr)          # -> adb logcat (python tag)
    for d in (os.environ.get("ANDROID_PRIVATE"), os.path.expanduser("~"), "."):
        if not d:
            continue
        try:
            with open(os.path.join(d, "crash.log"), "w", encoding="utf-8") as f:
                f.write(exc_text)
            break
        except Exception:
            continue


def main():
    try:
        from engine_sim.app import App
        App().run()
    except Exception:
        _log_crash(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
