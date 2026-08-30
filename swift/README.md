# PyEngineSim, second implementation (Swift)

The same engine, written twice.  The Python is the reference; this is the one
that runs on the phone.

Not a rewrite for its own sake — the Python v1 shipped and worked at 24 kHz /
block 512, but it was at the edge of what a general-purpose runtime can do on
a phone, and the headroom was gone.  This has the headroom.

## What is here

```
EngineSimCore/          a SwiftPM package: the DSP, the physics, the car link
  Sources/EngineSimCore     24 chain stages + OBD + CarMode
  Sources/PyEngineSimUI     the app's own sources, as a library so
                            `swift build` type-checks them
  Tests/                    44 tests, all against the Python
PyEngineSimApp/         the @main, which cannot live in a library
```

## Proving it

Every stage is checked against the Python it reproduces, not against
"sounds about right".  Run it with:

```bash
cd swift/EngineSimCore && swift test
```

Two kinds of check, and both are needed:

**Per stage**, with the filter cache and the random generator restored at that
stage's boundary.  That proves the stage.

**End to end**, restoring nothing — one seed, thirty blocks, free-running.
That proves the WIRING, and it is a different question: restoring state at
every boundary hides exactly the errors wiring produces.  It caught seven,
including two inputs that were being passed as 1.0 and are not, and a chord
resonator that was being rebuilt 125 times a second.

Current: three of four cases reproduce at float32 precision (-153 to -156 dB).
The fourth, f2007 on the trackside ear, does not — traced to the gearbox whine
on the bay bus, and deliberately out of scope, because trackside is not a mode
this ships in.  The test asserts the measured bound so it cannot regress.

## Regenerating the fixtures

The fixtures are the Python's own output.  If the Python changes, they must be
rebuilt or the tests are checking against a previous version of the truth:

```bash
py -m engine_sim.serialize          # presets.json / presets.csv
py tools/export_voicing.py          # per-engine voicing + solved geometry
py tools/export_engine_tables.py    # VE and burn surrogate tables
py tools/export_resonance_ref.py
py tools/export_muffler_ref.py
py tools/export_induction_ref.py
py tools/export_exit_ref.py
py tools/export_listener_ref.py
py tools/export_master_ref.py
py tools/export_endtoend_ref.py
cp docs/{presets,engine_voicing,engine_tables}.json \
   swift/EngineSimCore/Tests/EngineSimCoreTests/Fixtures/
```

## What still needs Xcode (and therefore Leo)

Everything above builds and tests from the command line.  The app target does
not, because it needs a signing identity, and signing needs the GUI —
`codesign` over SSH fails with `errSecInternalComponent`.

In Xcode:

1. **File > New > Project > iOS > App.**  Product name `PyEngineSim`,
   interface SwiftUI, language Swift.  Save it over `swift/PyEngineSimApp/`.
2. **Delete** the `ContentView.swift` and `<name>App.swift` Xcode generates.
3. **Add** `swift/PyEngineSimApp/PyEngineSim/App.swift` and, from
   `swift/EngineSimCore/Sources/PyEngineSimUI/`, the three UI files.
4. **File > Add Package Dependencies > Add Local**, pick
   `swift/EngineSimCore`, and add the `EngineSimCore` library to the target.
5. **Add the three JSON files** to the target's *Copy Bundle Resources*:
   `docs/presets.json`, `docs/engine_voicing.json`, `docs/engine_tables.json`.
   The app looks for them by name at the bundle root.
6. **Signing & Capabilities:** pick your personal team, and add the
   **Background Modes** capability with *Audio, AirPlay, and Picture in
   Picture* ticked — without it the sound stops when the screen locks, which
   is most of the time in a car.
7. Build to the phone.

## The car

`car mode` needs a **WiFi** ELM327, not Bluetooth: iOS will not talk Bluetooth
SPP to a non-MFi accessory, so a £10 Bluetooth dongle cannot work no matter
what the app does.  Join the dongle's WiFi, then press **the car** in the app.

One thing is still unverified and the whole in-car plan rests on it: whether a
non-CarPlay app's audio routes to the car speakers over **wired** CarPlay.  It
takes one minute to find out with YouTube and a cable, and it should be done
before any more effort goes into the car side.
