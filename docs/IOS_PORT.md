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

## Build steps, riskiest first

1. **numpy for iOS.** iOS wheel building was merged into numpy on 2026-07-17
   ([PR #28759](https://github.com/numpy/numpy/pull/28759)), milestone **2.6.0**
   — which at the time of writing is **not yet released** (latest stable 2.5.2).
   So either build from main or wait:

   ```
   CIBW_PLATFORM=ios CIBW_ENVIRONMENT="IPHONEOS_DEPLOYMENT_TARGET=17.0" \
       cibuildwheel --arch arm64_iphoneos
   ```

   Needs Xcode with the iOS SDK, cibuildwheel >= 4.1.0, ninja. Do this first —
   it is the one step that can sink the schedule. Check whether BeeWare publish a
   prebuilt iOS wheel before compiling one.

2. **Briefcase skeleton.** A minimal iOS app; add the `engine_sim` package and
   the numpy wheel. `pygame` is *not* needed. Confirm `import engine_sim` and a
   `CarMode` tick run on the simulator before touching audio.

3. **The audio sink.** `rubicon-objc` -> `AVAudioEngine` /
   `AVAudioSourceNode`, fed from `synth.sink`. Set `AVAudioSession` category
   `.playback` and `UIBackgroundModes: audio` so it keeps playing with the
   screen off. Start with a large buffer, then tighten and measure.

4. **A minimal Toga view.** Engine picker + rpm/link readout, both straight from
   `mode.status()`. iOS needs *a* view; it does not need the engine bay.

5. **In the car.** WiFi-join the dongle's AP, USB CarPlay, `--probe` equivalent
   first, then listen.

---

## Rules that still apply

- **Do not touch audio voicing.** The ear-approved baseline is git tag
  `sound-baseline-v0.9.46`; any audio change must be A/B-able against it. The
  sink was added specifically so a platform backend never has to.
- Test suites: `test_headless.py` (physics + the sink), `test_obd.py` (link, rpm
  map, gears, shifts, `CarMode`), both must stay green.
- `tools/fake_elm327.py` means none of the OBD work needs hardware or a car.
