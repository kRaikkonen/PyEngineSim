"""Android / buildozer entry point.

python-for-android launches ``main.py`` from the source root, so this is the
phone equivalent of ``run.py`` — it just starts the app with its default engine
(the Aventador).  On Android the UI defaults to the finger-control overlay and
audio streams through pygame's SDL2 mixer (see ON_ANDROID in app.py / audio.py).
"""

from engine_sim.app import App


def main():
    App().run()


if __name__ == "__main__":
    main()
