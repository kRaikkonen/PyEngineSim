# swiftEngineSim

PyEngineSim's engine, written a second time in Swift.  The Python is the
reference; this is the one that runs on the phone.

Not a rewrite for its own sake — the Python v1 shipped and worked at 24 kHz /
block 512, but it was at the edge of what a general-purpose runtime can do on
a phone, and the headroom was gone.  This has the headroom.

## What is here

```
EngineSimCore/          a SwiftPM package: the DSP, the physics, the car link
  Sources/EngineSimCore     24 chain stages + OBD + CarMode
  Sources/SwiftEngineSimUI  the app's own sources, as a library so
                            `swift build` type-checks them
  Tests/                    44 tests, all against the Python
swiftEngineSimApp/      the @main, which cannot live in a library
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

## Does it have the headroom

The whole reason this exists.  Measured on the Mac, 32 kHz, near the limiter
with the throttle buried -- the most expensive place the chain ever runs:

| | swift | python (no scipy, the phone's path) |
|---|---|---|
| A3 1.5 TFSI | 0.096 | 1.646 |
| Aventador V12 | 0.117 | 2.329 |
| Veyron W16 | 0.125 | 2.264 |
| F1 V8 | 0.107 | 2.078 |

Seconds of CPU per second of audio, so 1.0 is the edge.  **The Python cannot
keep up on a Mac** -- 1.6 to 2.3 times real time -- which is exactly why the
phone broke up.  Swift is 17-20x faster and sits at a tenth of one core.

Across all 131 cars at block 512: worst 0.102, median 0.073.  A phone core is
roughly a third of this Mac's, so the worst car should land near 30 % of one
core.  `swift test -c release --filter PerformanceTests` re-measures it, and
the Python side is `py tools/bench_python.py`.

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

## Putting it on the phone

The project is generated, not hand-built — `py tools/make_xcodeproj.py` writes
`swift/swiftEngineSimApp/swiftEngineSim.xcodeproj`, so it can be deleted and
rebuilt rather than being a file nobody dares touch.  It already holds the
local package reference, the three JSON fixtures (referenced IN PLACE under
`docs/`, so regenerating them updates the app), the signing team, and
`UIBackgroundModes = audio` — without which the sound stops at screen lock,
which in a car is most of the time.

So:

1. Open `swift/swiftEngineSimApp/swiftEngineSim.xcodeproj`.
2. Pick your iPhone at the top.
3. Run.

Verified from the command line: the simulator build **succeeds**, and the
device build gets all the way through compiling, linking and creating the
provisioning profile before failing at `CodeSign` with
`errSecInternalComponent`.  That is not a project problem — a non-GUI session
cannot get the signing key out of the keychain.  Pressing Run in Xcode is the
only step that has to happen there.

First install: the phone will say "Untrusted Developer" — Settings > General >
VPN & Device Management > your Apple ID > Trust.

Then, before anything else, watch the two numbers on screen: **cpu** should sit
near 30 % and **late** should stay at 0.  If either misbehaves, that is the
thing to report, not the sound.

## The car

`car mode` needs a **WiFi** ELM327, not Bluetooth: iOS will not talk Bluetooth
SPP to a non-MFi accessory, so a £10 Bluetooth dongle cannot work no matter
what the app does.  Join the dongle's WiFi, then press **the car** in the app.

One thing is still unverified and the whole in-car plan rests on it: whether a
non-CarPlay app's audio routes to the car speakers over **wired** CarPlay.  It
takes one minute to find out with YouTube and a cable, and it should be done
before any more effort goes into the car side.
