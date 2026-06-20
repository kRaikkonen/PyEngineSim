# PyEngineSim

A from-scratch **Python** combustion-engine simulator — physically-modelled
crank/piston dynamics, a thermodynamic four-stroke combustion cycle, a drivetrain
you can drive, and a fully **physics-driven, real-time audio synthesizer** with a
live mixing console. Inspired by AngeTheGreat's
[Engine Simulator](https://github.com/ange-yaghi/engine-sim).

```
py run.py              # default engine
py run.py --engine 7   # 1=911 H6  2=EA888 I4  3=Coyote V8  4=458
                       # 5=LFA V10  6=Murcielago V12  7=F2004 F1 V10
```

## Controls

| Key | Action |
| :---: | :--- |
| `A` | Toggle ignition |
| `S` | Hold for the starter |
| `↑` / `↓` | Throttle / **brake** |
| `Z` / `X` | Shift down / up (paddle — tap, no clutch needed) |
| `Shift` | Hold to ride the clutch (launches / stalls) |
| `T` | Auto / manual gearbox |
| `1`–`7` | Load engine (or click a chip) |
| `V` | Cycle firing-pulse timbre (voice) |
| `I` | Cabin (interior) sound on/off |
| `C` | Open the audio mixer (drag the sliders) |
| `F2` | Save current engine + audio to a `.json` config |
| `F3` | Cycle through saved audio (EQ) presets |
| `F4` / `F5` | Output device / sample rate (44.1 ↔ 48 kHz) |
| `M` · `Esc` | Mute · quit |

**Engines** (each with real bore/stroke, redline, firing order, gearbox and
exhaust geometry): Porsche 911 3.8 flat-six, VW/Audi EA888 2.0T, Ford Coyote 5.0
V8, Ferrari 458 flat-plane V8, Lexus LFA 4.8 V10, Lamborghini Murciélago 6.5 V12,
Ferrari F2004 3.0 F1 V10 (18 500 rpm).

**Start it:** hold **S** to crank, ignition on (**A**), release **S** when it
catches. **Drive it:** tap **X** to upshift (paddle-shift), or **T** for auto and
just hold **↑**.

## Audio mixer & configs

Press **C** for the live console: fire/bang level + power-chord drive, body,
firing pitch, attack softness, fizz, the two physical pipe resonances, intake
roar, explosion + room reverb, per-cylinder spread and a 3-band EQ — all real
time. **F2** saves the current engine and sound as editable `.json` files under
`configs/`; saved engines reappear as selectable presets, saved sounds cycle with
**F3**.

## What it actually simulates

This is not a video player — it's a real, if simplified, engine simulation. Each
cylinder is integrated through its **four-stroke cycle** every frame:

1. **Slider-crank kinematics** — exact analytic piston position / velocity from
   bore, stroke and rod length (`engine.py`). This is the readable equivalent of
   the original's 2D rigid-body constraint solver.
2. **Thermodynamics** — intake at manifold pressure → adiabatic compression →
   heat release (spark + air) → adiabatic expansion → exhaust blowdown
   (`simulator.py`). Gas pressure becomes crank torque via virtual work,
   `T = (P − P_atm)·A · d(stroke)/dθ`.
3. **Crankshaft dynamics** — all cylinder torques plus starter, friction,
   windage and external load are summed and integrated (semi-implicit Euler with
   adaptive sub-stepping so the combustion pulse stays sharp at high rpm).
4. **Idle governor & rev limiter** — an idle-air controller holds a stable idle
   with the throttle shut, and fuel is cut above the redline. Both are real
   behaviours you can watch on the indicators.
5. **Volumetric efficiency** — a breathing curve that gives the torque its proper
   **mid-range hump** and fall-off toward idle and redline.
6. **Drivetrain** — a slipping clutch, 5-speed gearbox + final drive and a
   vehicle mass fighting rolling and aero drag (`drivetrain.py`). Dump the clutch
   in gear and the load stalls the engine; feather it and you launch the car.
7. **Audio** — every exhaust-valve opening stamps a decaying "blat" pulse into a
   real-time `sounddevice` stream (`audio.py`). The pulse *train* is the engine
   note: its rate rises with rpm, its punch rises with cylinder pressure, so it
   idles lumpy and snarls under load — the same idea as the original's impulse
   convolution, made cheap enough for pure Python.

Run `py test_headless.py` to see the start-up sequence and a dyno-style
torque/power curve printed for each engine, with no window or audio.

## How this maps to the original C++ project

| Original (C++) | Here (Python) |
| :--- | :--- |
| `delta-studio` (custom DX engine) | `pygame` window + drawing |
| `simple-2d-constraint-solver` | analytic crank-slider + Euler integration |
| `piranha` `.mr` engine scripts | `presets.py` (plain Python engine builders) |
| impulse-response convolution synth | exhaust-pulse synthesizer (`audio.py`) |
| `gas_system` / `combustion_chamber` | `simulator.py` thermodynamic model |

### Honest limitations

Python can't match the C++ original's fidelity in real time, so the model is
deliberately tractable: the gas cycle is open-loop (it doesn't track residual gas
or cross-port flow between cylinders), there's no cam/valve-timing or
forced-induction modelling, and the audio is a pulse synthesizer rather than true
convolution. The torque-curve *shapes* and driving feel are realistic; the
absolute numbers are in the right ballpark but not dyno-accurate.

## Build your own engine

Engines are modular: write a builder in `presets.py` that returns an `Engine`
made of `Cylinder`s, then add one line to the `PRESETS` list — it shows up in the
on-screen selector and on its number key automatically, nothing else to wire up.
Bore, stroke, rod length, compression ratio, cylinder count, firing order,
inertia, redline, the breathing/friction curves **and the full gearbox** (gear
ratios, final drive, vehicle mass, wheel radius, clutch capacity) are all
parameters on `Engine`. See `ferrari_458()` for a fully-specced real example.

## Audio latency

On Windows the synth opens the output stream on **WASAPI in exclusive mode**
(~5 ms) when available, falling back to WASAPI shared low-latency, then the
system default. This matters: PortAudio's default MME backend buffers 90–180 ms,
enough to make the engine audibly lag the throttle. The chosen mode and measured
latency are printed/queryable via `Synthesizer.mode` / `.latency_ms`.

## Requirements

```
pip install -r requirements.txt   # numpy, pygame-ce, sounddevice
```

`sounddevice` is optional — if it's missing or no audio device opens, everything
else still runs, silently.
