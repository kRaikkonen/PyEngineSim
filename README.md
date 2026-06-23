# PyEngineSim

**A real‑time engine *sound + mechanics* simulator, written in pure Python.**
By **Leo** · 🇬🇧 English (this file) · [🇨🇳 中文 README](README.zh.md)

> ⚠️ **Early Access** — PyEngineSim is under active development. Expect rough
> edges, changing features and the occasional bug. Feedback is very welcome.

![PyEngineSim — Ford GT 3.5 V6](docs/screenshot_fordgt.png)

PyEngineSim physically models a 4‑stroke engine — crank/rod/piston kinematics, a
thermodynamic combustion cycle, rigid‑body crankshaft dynamics — and turns the
exhaust pulses into **live engine audio**. It draws an animated engine bay
(pistons, valvetrain, turbos, manifolds), a full gauge cluster, and **130+
real‑world engine presets**, from an inline‑3 to a Bugatti **W16**, F1 V10s, a
Merlin **V12** aircraft engine and rotary Wankels.

It can also **follow a real Forza game over UDP** — drive in Forza Horizon /
Motorsport and PyEngineSim revs the matching engine in real time.

---

## 🙏 Credits

PyEngineSim is inspired by, and owes a huge debt to,
**[AngeTheGreat](https://github.com/ange-yaghi)** and his original C++
**[Engine Simulator](https://github.com/ange-yaghi/engine-sim)**. Watch his
videos and star the original — this Python edition exists only because that work
was so inspiring. PyEngineSim is an independent reimplementation and shares no
code with the original.

---

## ⬇️ Download & Run

| Platform | How |
|---|---|
| **Windows (fast)** | Unzip `PyEngineSim-onedir.zip`, run `PyEngineSim/PyEngineSim.exe` |
| **Windows (single file)** | `PyEngineSim-onefile.zip` → one `.exe` (slower first launch) |
| **Android (arm64)** | Sideload `pyenginesim-…-arm64-v8a-debug.apk` (touch UI, SDL2 audio) |
| **From source** | `pip install numpy scipy sounddevice pygame` → `python run.py` |

Startup loads the **Lamborghini Aventador V12** by default; pick any engine from
the **Demo cars ▾** menu.

---

## 🎮 Connect a real Forza game (Data Out → PyEngineSim)

Forza Horizon 4 / 5 and Forza Motorsport stream live telemetry over UDP.
PyEngineSim listens for it and revs the engine to match the game.

1. **In PyEngineSim:** click **`Forza`** in the toolbar. The button colour shows
   the link state — **🔴 red** = listening but no data yet, **🟢 green** = Forza
   packets are arriving. PyEngineSim listens on **UDP port `5300`**.
2. **In the Forza game:** open **Settings → HUD and Gameplay** (Horizon) /
   **Settings → Gameplay & HUD** (Motorsport) and set:
   - **Data Out:** `ON`
   - **Data Out IP Address:** depends on which Forza you own (see note below).
   - **Data Out IP Port:** `5300`
   - **(Horizon)** choose the **"Dash"** format.
3. Drive. The **Forza** button turns 🟢 green and the engine follows the game's
   rpm / throttle / boost.

> **⚠️ Which IP? It depends on the Forza edition (same‑PC case):**
> - **Steam edition** — a normal Win32 app, loopback works: use **`127.0.0.1`**.
> - **Microsoft Store / Xbox Game Pass edition** — a sandboxed **UWP** app, and
>   Windows **blocks UWP loopback**, so `127.0.0.1` never arrives. Use this PC's
>   **LAN IP** instead (e.g. `192.168.1.x`, find it with `ipconfig`).
>
> If the game and PyEngineSim are on **different PCs**, always use the LAN IP of
> the PC running PyEngineSim — regardless of edition.

> **Same‑PC performance:** if the game is heavy on your machine, switch
> PyEngineSim to **Forza Ultra** (below) so it uses almost no resources.

---

## ⚡ Performance modes (Low‑Q & Forza Ultra)

The bay rendering is heavy on a 16‑cylinder engine. Two toggles trade visuals for
CPU so the audio thread never starves (no crackling):

- **`Low Q` — Low‑Quality render.** Everything is drawn as **flat solid shapes
  with no shading**: cylinders, pipes, turbos, belts/gears, gauges and the wheel
  all go single‑colour, the combustion flash is off, and translucency is
  disabled. Roughly **halves a heavy frame** — visually simpler, identical data.
  Toggle it any time.
- **`Forza`.** Entering Forza telemetry mode **auto‑enables Low‑Q**, freezes the
  spinning parts (turbo, gears, prop — only the **pistons and dashboard** keep
  moving) and drops the scopes / ignition lamps. Leaving Forza restores whatever
  Low‑Q setting you had before.
- **`Forza Ultra` — display off.** The screen goes blank except its own button,
  the **Demo cars** menu and a **Mixer/EQ** toggle. The engine still runs and
  follows Forza over UDP, but the renderer draws essentially nothing (~0.9 ms/
  frame), so nearly the whole CPU is free for the game + audio. **Best mode while
  actually racing.**

---

## ⌨️ Controls

| Key | Action | | Key | Action |
|---|---|---|---|---|
| `↑ / ↓` | Throttle / brake | | `A` | Ignition on/off |
| `Shift` (hold) | Clutch | | `S` (hold) | Starter |
| `X` | Upshift | | `Z` | Downshift |
| `T` | Auto / Manual | | `C` | Mixer / EQ |
| `E` | Scope | | `M` | Mute |
| `V` | Firing voice | | `Esc` | Quit |

A **Touch** toggle (top‑right) brings up on‑screen pedals/paddles for
touchscreens; it's on by default on Android.

---

## 🔬 What it actually simulates

Each cylinder is integrated through its **four‑stroke cycle** every frame:
slider‑crank kinematics → adiabatic compression → spark heat release → expansion
→ exhaust blowdown; gas pressure becomes crank torque via virtual work, and all
cylinder torques + starter + friction + load are integrated as rigid‑body
crankshaft dynamics. A slipping clutch, gearbox, final drive and vehicle mass let
you launch, stall and drive. Every exhaust‑valve opening stamps a decaying pulse
into a real‑time audio stream — the *pulse train* is the engine note.

| Original (C++) | Here (Python) |
|---|---|
| `delta-studio` renderer | `pygame` window + drawing |
| 2D constraint solver | analytic crank‑slider + Euler integration |
| `.mr` engine scripts | `presets.py` engine builders |
| impulse‑response synth | exhaust‑pulse synthesizer (`audio.py`) |

---

## 🛠️ Build it yourself

- **Windows exe:** `pip install pyinstaller`, then
  `pyinstaller packaging/PyEngineSim.spec` (fast one‑folder) or
  `packaging/PyEngineSim-onefile.spec` (single file).
- **Android apk:** on Linux/WSL with `buildozer` (see `buildozer.spec`). pygame /
  SDL2 only compile with **python‑for‑android `v2023.09.16` (Python 3.10) +
  NDK r25b**; newer combos break on `longintrepr.h` / `ALooper`.

---

*PyEngineSim — by **Leo**. Early Access. With gratitude to **AngeTheGreat's**
Engine Simulator.*
