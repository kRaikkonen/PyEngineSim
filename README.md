# PyEngineSim

**A real-time engine *sound + mechanics* simulator, written in pure Python.**
By **Leo** · `v1.0` · 🇬🇧 English (this file) · [🇨🇳 中文 README](README.zh.md)

![PyEngineSim — Ford GT 3.5 V6](docs/screenshot_fordgt.png)

PyEngineSim models a four-stroke engine from first principles — crank/rod/piston
kinematics, a finite-burn thermodynamic cycle, volumetric-efficiency breathing,
turbo/supercharger energy balance, cycle temperature and rigid-body crankshaft
dynamics — and turns the exhaust pulses into **live engine audio**. It draws an
animated engine bay (pistons, valvetrain, turbos, manifolds), a full gauge
cluster, a physics **analyzer**, and ships **130+ real-world engine presets** —
from an inline-3 to a Bugatti **W16**, F1 **V10s**, a Merlin **V12** aircraft
engine, rotary **Wankels**, big-rig **diesels** and **hybrids**.

It can also **follow a real Forza game over UDP** — drive in Forza Horizon /
Motorsport and PyEngineSim revs the matching engine in real time.

---

## ✨ What makes it different: it's **white-box**

Nothing here is a hand-drawn curve faked to look right. Every stage of the engine
is reconstructed from physics, and the numbers **fall out** of that physics:

- **Breathing** — volumetric efficiency comes from the Taylor Mach index (top-end
  choke), Engelman/Helmholtz intake ram tuning (the mid-range torque hump) and
  residual-gas backflow, off the engine's real bore/stroke/runner geometry — not a
  Gaussian bell.
- **Combustion** — a finite-burn **Wiebe** heat release with a real spark-advance
  map; peak cylinder pressure lands a few degrees after TDC like a real trace.
- **Torque** — mean BMEP from air-limited energy accounting (`IMEP = η_otto ·
  η_shape · (LHV/AFR) · ρ · VE`), knock-derated from the real charge temperature.
- **Forced induction** — turbo boost from a turbine/compressor **energy balance**
  with real spool lag (`τ ~ J_turbo / exhaust_power`); roots vs centrifugal
  superchargers from positive-displacement vs tip-speed² physics; charge-air
  heating + intercooler + heat-soak.
- **Exhaust note** — a real blowdown pulse train through white-box exhaust
  acoustics (megaphone, cat/GPF, resonator, muffler) with structure-borne block
  radiation.
- **ERS / hybrid** — MGU-K deploy/regen, MGU-H harvest, a battery state-of-charge.

Because it's all physics, the **displayed dyno is what the engine actually makes**,
and the whole fleet is **calibrated to real spec**: the 130 cars produce their
rated power and torque (fleet median vs published spec = **1.00**). Where an
engine is electronically **torque/power-limited** (an AMG/Aston TT held to
~1000 N·m), that's modelled as a real ECU envelope, not a fudge.

Every gauge in the **Analyzer** (`E`) — firing pulses, exhaust flow, valve lift,
cylinder pressure, spark advance, the torque/HP dyno — is drawn straight from the
running physics.

---

## ⬇️ Download & run

| Platform | How |
|---|---|
| **Windows (fast)** | Unzip `PyEngineSim-onedir.zip`, run `PyEngineSim/PyEngineSim.exe` |
| **Windows (single file)** | `PyEngineSim-onefile.zip` → one `.exe` (slower first launch) |
| **Android (arm64)** | Sideload `pyenginesim-1.0-arm64-v8a-debug.apk` — touch UI, SDL2 audio, no scipy needed |
| **From source** | `pip install numpy scipy sounddevice pygame` → `python run.py` |

Startup loads the **Lamborghini Aventador V12** by default; pick any engine from
the **Demo cars ▾** menu. On desktop `scipy` sharpens a few audio filters but is
optional — PyEngineSim has a pure-numpy fallback for every one (that's the Android
path).

---

## 🚗 The car roster (130+)

Naturally aspirated screamers (Aventador V12, LFA V10, 458, F1 **V10/V8**),
turbo icons (F40, 2JZ, R35, 911 Turbo/GT2), superchargers (Hellcat, GT500,
F-Type), rotaries (787B 4-rotor, RX-7), big diesels (Cummins, Actros, Iron
Knight), hybrids & F1 power units (918, P1, FXX-K, SF-25), and oddballs like a
**Merlin V12** Spitfire and a Wildcat radial. Each carries its real geometry,
firing order, cam profile, induction and exhaust hardware — so they sound and
behave like themselves, not like a generic V8.

---

## 🎮 Connect a real Forza game (Data Out → PyEngineSim)

Forza Horizon 4/5 and Forza Motorsport stream live telemetry over UDP;
PyEngineSim listens and revs the engine to match the game.

1. **In PyEngineSim:** click **`Forza`** in the toolbar. 🔴 red = listening, no
   data yet; 🟢 green = packets arriving. It listens on **UDP port `5300`**.
2. **In the Forza game:** **Settings → HUD and Gameplay** (Horizon) /
   **Gameplay & HUD** (Motorsport): **Data Out: `ON`**, **Port: `5300`**, and
   (Horizon) the **"Dash"** format.
3. Drive — the engine follows the game's rpm / throttle / boost.

> **Which IP?** Same-PC **Steam** edition → `127.0.0.1`. Same-PC **Microsoft
> Store / Game Pass** (sandboxed UWP, loopback blocked) → this PC's **LAN IP**
> (`ipconfig`). Different PCs → the LAN IP of the PC running PyEngineSim.

---

## ⚡ Performance modes

The bay rendering is the heavy part (a W16 has a lot to draw); the physics + audio
are cheap (audio is ~1 ms per 8 ms block). Two toggles trade visuals for CPU so
the audio never crackles:

- **`Low Q`** — everything drawn as flat solid shapes, no shading/flash/
  translucency. Roughly halves a heavy frame; identical data.
- **`Forza Ultra`** — display off except its own button, the **Demo cars** menu
  and a **Mixer/EQ** toggle. The engine still runs and follows Forza (~0.9 ms/
  frame), leaving almost the whole CPU for the game + audio. **Best while racing.**

---

## ⌨️ Controls

| Key | Action | | Key | Action |
|---|---|---|---|---|
| `↑ / ↓` | Throttle / brake | | `A` | Ignition on/off |
| `Shift` (hold) | Clutch | | `S` (hold) | Starter |
| `X` | Upshift | | `Z` | Downshift |
| `T` | Auto / Manual | | `C` | Mixer / EQ |
| `E` | Analyzer scopes | | `M` | Mute |
| `V` | Firing voice | | `Esc` | Quit |
| `B` | POV: chase cam / cockpit | | | |

A **Touch** toggle (top-right) brings up on-screen pedals/paddles; on by default
on Android.

---

## 🔬 Under the hood

Each cylinder is integrated through its four-stroke cycle every frame: slider-crank
kinematics → adiabatic compression → Wiebe spark heat release → expansion →
exhaust blowdown. Gas pressure becomes crank torque via virtual work; all cylinder
torques + starter + friction + load integrate as rigid-body crankshaft dynamics. A
slipping clutch, gearbox, final drive and vehicle mass let you launch, stall and
drive. Every exhaust-valve opening stamps a decaying pulse into the audio stream —
the **pulse train is the engine note**.

The runtime uses fast closed-form white-box models (`ve_model`, `map_model`,
`bmep_model`) baked into small lookup tables at load; an offline first-principles
gas model (`gas_truth`, `gas_moc`) is the ground truth they are made consistent
with. Everything is pure Python + NumPy, no per-car fudge maps.

| Original (C++) | Here (Python) |
|---|---|
| `delta-studio` renderer | `pygame` window + drawing |
| 2D constraint solver | analytic crank-slider + Euler integration |
| `.mr` engine scripts | `presets.py` engine builders |
| impulse-response synth | white-box exhaust-pulse synthesizer (`audio.py`) |

---

## 🛠️ Build it yourself

- **Windows exe** — `pip install pyinstaller`, then `pyinstaller
  packaging/PyEngineSim.spec` (one-folder) or `packaging/PyEngineSim-onefile.spec`.
- **Android apk** — on Linux/WSL with `buildozer android debug` (see
  `buildozer.spec`). pygame/SDL2 only compile with **python-for-android
  `v2023.09.16` (Python 3.10) + NDK r25b**; newer combos break on
  `longintrepr.h` / `ALooper`. scipy is dropped on Android (pure-numpy fallback).

---

## 🙏 Credits

PyEngineSim is inspired by, and owes a huge debt to,
**[AngeTheGreat](https://github.com/ange-yaghi)** and his original C++
**[Engine Simulator](https://github.com/ange-yaghi/engine-sim)** — watch his
videos and star the original. PyEngineSim is an **independent reimplementation**
and shares no code with it.

---

*PyEngineSim `v1.0` — by **Leo**. A pure-Python, fully white-box engine simulator.
With gratitude to **AngeTheGreat's** Engine Simulator.*
