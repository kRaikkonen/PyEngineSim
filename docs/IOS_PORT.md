# iOS port brief — car mode on an iPhone

Working document for whoever picks this up on the Mac. Everything marked
**verified** was actually run; everything marked **unverified** must be checked
before it is relied on.

---

## The goal

Run **car mode** (`engine_sim.carmode.CarMode`) on an iPhone: read the real
car's rpm from an OBD-II dongle, play a chosen engine's sound, route the audio
into the car over **wired CarPlay**. No engine-bay graphics — car mode draws
nothing by design.

Target car: Audi A3 8Y facelift. Default preset: `rs3` (EA855 2.5 I5 — the same
chassis's five-cylinder).

---

## Decision: Python port first, Swift rewrite deferred

A Swift rewrite is technically the better iOS app (it can render inside the
audio callback; Python cannot, because of the GIL and allocation in a real-time
thread). It was deliberately **not** chosen yet:

- The port surface is ~10,400 lines, of which `audio.py` is 3,528 lines carrying
  **82 commits of ear-tuning**. Rewriting that is a *reproduction* problem, not
  a writing problem — the value is the calibration, not the architecture.
- The latency budget is **not** dominated by audio output: OBD round trip is
  20–50 ms, the output buffer is 25 ms, and using the accelerator-pedal PID buys
  back 100–300 ms of turbo lag. Swift would save ~20 ms of a ~60–80 ms chain.

**Three on-device measurements decide whether a rewrite is ever needed:**

1. Does the audio hold without crackle, and at what buffer size?
2. Battery drain per hour with CarPlay connected.
3. Thermals.

If those are acceptable, the rewrite is pure cost. If not, we will know exactly
which one failed, with a working reference implementation on the device to A/B
against. Either way the Python build is the executable spec.

If a Swift port ever starts: **build the golden A/B harness first** (same rpm /
throttle / seed into both, compare waveform and spectrum), stage by stage —
firing pulse, exhaust chain, reverbs, listener. Presets are data: export to
JSON, do not rewrite 3,408 lines by hand.

---

## What is already in place

**verified — car mode has no display dependency.** `car.py`'s import graph:

    third-party modules car.py pulls in: ['numpy', 'scipy', 'sounddevice']
    pygame imported: False

So an iOS build needs **CPython + numpy + one audio sink** — not a display
stack. `scipy` is optional everywhere (`_HAVE_SCIPY` guards, same as Android).

**verified — the audio seam.** `Synthesizer.sink` takes any callable receiving a
C-contiguous `(256, 2) float32` block, already panned for the POV, and
`start()` prefers it over opening a device. It sits strictly *after* the render,
so no voicing changes. A blocking sink paces itself; a non-blocking one is paced
off the sample rate.

```python
synth = Synthesizer(sim, sample_rate=32000)
synth.sink = lambda block: ...      # (256, 2) float32
synth.start()
```

Covered by `test_headless.py::run_audio_sink` — 1.5 s rendered with no sound
device at all, holding shape, dtype, range and real-time pace (measured 32,080
frames/s against 32,000).

**verified — the app-facing object.** `CarMode` owns the sim, the rpm map, the
shift detector and the synth, and knows nothing about how it is driven:

```python
mode = CarMode(engine_key="rs3", telemetry=OBDTelemetry(host=..., port=...),
               rpm_map=RpmMap("stretch", car_idle=800, car_redline=6500),
               synth_factory=lambda sim: Synthesizer(sim, ...))
mode.start()
mode.update(dt)          # from a timer / run loop
mode.status()            # -> dict, everything a view needs
mode.set_engine("aven")  # a list tap; REBUILDS the synth (voicing tables are
                         # baked per engine — rebinding plays the old timbre)
mode.stop()
```

`car.py` is now only the terminal shell around this. An iOS app is a different
shell. Covered by 15 checks in `test_obd.py::test_carmode`.

---

## Hardware, and the iOS rules that force it

**Buy a WiFi ELM327.** Not because iOS demands WiFi, but because it is the
intersection of "works on iPhone" ∩ "works on the laptop" ∩ "already
implemented".

| dongle | iPhone | our code |
|---|---|---|
| WiFi | yes — plain TCP socket | **implemented, tested** |
| BLE | yes — CoreBluetooth | not implemented (`_BleLink` would be a drop-in: the transport only needs `write` / `read_prompt` / `close`) |
| Bluetooth classic SPP | **no** — iOS gives SPP only to MFi accessories | n/a |
| USB serial | **no** — no serial ports on iOS | implemented but unusable there |

**Wired CarPlay is required, not preferred:** wireless CarPlay uses the phone's
WiFi, and so does the dongle. One radio, one network — they conflict. Wired
CarPlay leaves WiFi free for the dongle and keeps the phone charging.

### Two things to verify in the actual car before building anything

1. **unverified — does the 8Y facelift actually connect CarPlay over USB?** Once
   a phone is paired for wireless CarPlay some Audis treat USB as charge-only.
2. **unverified — does a non-CarPlay app's audio route to the car speakers over
   the CarPlay link?** Expected yes (this is the "YouTube audio through CarPlay"
   behaviour), and the whole plan rests on it, because this app will *not* be a
   CarPlay app. Test with any media app in one minute.

If (2) fails the plan changes materially — fall back to an FM transmitter with
an analogue input, or a wired cabin speaker.

**CarPlay entitlement is not an option and is not needed.** Apple grants CarPlay
templates only for eight app categories (audio, comms, navigation, parking, EV
charging, fuelling, quick food, driving task); an engine-sound simulator is none
of them, and CarPlay entitlements do not work with a free personal provisioning
profile at all. We only need the audio *route*, which requires no entitlement —
just `UIBackgroundModes: audio` and an `AVAudioSession` in `.playback`.

---

## The Mac (verified 2026-08-29)

    ziyuliu@100.124.3.111   MacBookPro14,1 -- 2017 13" Intel i5-7360U
    2 cores / 8 GB RAM / 38 GB free       macOS 13.7.8 Ventura
    Xcode 15.2 + iOS 17.2 SDK (device and simulator)

Two consequences of the hardware:

- **Intel host, so the iOS Simulator is x86_64**, not arm64. Simulator wheels
  for x86_64 do exist (below), but that is the path numpy's own CI does not
  test -- prefer the real device when something looks wrong.
- Ventura caps Xcode at 15.2, which is fine: iOS 17.2 SDK matches the
  `IPHONEOS_DEPLOYMENT_TARGET=17.0` the iOS wheels are built against.

**No sudo was needed anywhere, and none should be.** `xcode-select` points at
CommandLineTools, but exporting `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`
overrides it without admin. Python came from `uv`, into userspace.

    export PATH="$HOME/.local/bin:$PATH"
    export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
    cd ~/pyenginesim-ios && . .venv/bin/activate    # py 3.13.15, briefcase 0.4.4
    cd src                                          # the repo clone

## numpy for iOS: solved, no compiling

**Prebuilt iOS wheels already exist** in the BeeWare channel Briefcase uses as
its secondary repository (`https://anaconda.org/beeware/repo`), so the
cibuildwheel step that was the schedule risk is simply not needed:

    numpy-2.5.0.post1-cp313-cp313-ios_17_0_arm64_iphoneos.whl          <- the phone
    numpy-2.5.0.post1-cp313-cp313-ios_13_0_x86_64_iphonesimulator.whl  <- this Mac's simulator
    ... also cp314 / cp315, and arm64 simulator slices

(PyPI itself still publishes no iOS wheels for numpy: iOS wheel *building* was
merged on 2026-07-17 for the 2.6.0 milestone, and 2.6.0 is not released. The
BeeWare channel is the source to use.)

## Verified running on the Mac

CPython 3.13.15 + numpy 2.5.2, **with sounddevice not installed at all** --
i.e. exactly the iOS situation:

    sounddevice available: False
    start=True mode=sink blocks=188 rate=32069 frames/s peak=0.54
    All headless checks passed.
    All OBD / car-mode checks passed.

That run found and fixed the one bug that would have stopped the port at its
first audio call: `Synthesizer.start()` gated the sink behind `enabled`
(`_HAVE_SD or ON_ANDROID`), so on any machine without PortAudio the synth
refused to start even with a sink supplied. Fixed in 51742a3; the test now
sets `enabled = False` before starting the sink, so the guarantee is "works
with no audio backend at all".

## Where v1 landed (2026-08-29)

**The Python build works on the phone at 24 kHz / block 512**, V12 included --
that is now the default in the app.  It does not work at 32 kHz / block 256:
a V12 there sits at 150 % of one A12 core.  The knobs are one tap away for a
phone with more room.

What that setting costs, measured (RS3, 3500 rpm, 80 % throttle):

    band share      30-300  0.3-1.2k  1.2-2.4k   2.4-5k    5-9k   9-12k
    32 kHz / 256     53.9%    17.9%     11.1%     8.1%     4.9%    2.1%
    24 kHz / 512     46.3%    20.9%     14.9%    10.1%     5.1%    1.1%

and above 12 kHz there is simply nothing left -- at 32 kHz that band carried
2-7 % of the energy, the top of the whine and the injector fizz.  The bigger
block also halves the per-block jitter refresh (125 -> 62.5 Hz).

So v1 is usable, and **v2 in Swift is what buys back the headroom to run
32 kHz / 256 on the heavy engines** rather than what rescues a broken build.
Both are kept: `swift/EngineSimCore` is the second implementation and the
Python is its specification (tools/golden.py).

## Remaining steps

1. **Briefcase iOS project.** Add a `[tool.briefcase]` config; app requires
   `numpy` only (no pygame). Build for the simulator first, confirm
   `import engine_sim` and a `CarMode` tick run on-device.
2. **The audio sink.** `rubicon-objc` -> `AVAudioEngine` / `AVAudioSourceNode`
   fed from `synth.sink`. `AVAudioSession` category `.playback` plus
   `UIBackgroundModes: audio` so it keeps playing with the screen off. Start
   with a large buffer, then tighten and measure.
3. **A minimal Toga view.** Engine picker + rpm/link readout, straight from
   `mode.status()`.
4. **Signing** (needs the GUI and an Apple ID in Xcode; free account = the app
   expires every 7 days) and **on-device run**.
5. **In the car.** WiFi-join the dongle AP, wired CarPlay, then the three
   measurements that decide the Swift question.

## Rules that still apply

- **Do not touch audio voicing.** The ear-approved baseline is git tag
  `sound-baseline-v0.9.46`; any audio change must be A/B-able against it. The
  sink was added specifically so a platform backend never has to.
- Test suites: `test_headless.py` (physics + the sink), `test_obd.py` (link, rpm
  map, gears, shifts, `CarMode`), both must stay green.
- `tools/fake_elm327.py` means none of the OBD work needs hardware or a car.
