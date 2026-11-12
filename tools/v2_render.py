"""Render a car through the v2 core to a WAV -- and the old chain beside it.

    python tools/v2_render.py a3                 # the drive below, both voices
    python tools/v2_render.py aven --seconds 16
    python tools/v2_render.py a3 --dynamics 1.0  # the real dynamic range

The drive: start, idle, light throttle (the everyday range), a full-throttle
pull through the gears, a lift.  Writes v2_<key>.wav and classic_<key>.wav
into --out (default: the current directory) and prints what was played.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine_sim import Simulator, core_voice, presets          # noqa: E402
from engine_sim.audio import BLOCK, Synthesizer                # noqa: E402


def drive(t, seconds):
    """(starter, throttle) at time t."""
    if t < 0.8:
        return True, 0.0
    if t < 3.0:
        return False, 0.0                       # idle
    if t < 7.0:
        return False, 0.22                      # pulling away gently
    if t < 9.0:
        return False, 0.10                      # cruising
    if t < seconds - 2.5:
        return False, 1.0                       # everything
    return False, 0.0                           # lift


def render(key, seconds, voice, dynamics):
    eng = presets.ALL[key]()
    sim = Simulator(eng)
    sim.ignition_on = True
    sim.drivetrain.auto = True
    synth = Synthesizer(sim)
    rate = synth.sample_rate
    if voice == "v2":
        synth.v2 = core_voice.CoreVoice(sim, key, rate)
        synth.v2.dynamics = dynamics
        synth.v2_on = True
    dt = BLOCK / rate
    chunks, log = [], []
    t, spent = 0.0, 0.0
    while t < seconds:
        sim.starter_engaged, sim.throttle = drive(t, seconds)
        sim.drivetrain.auto_control(sim.rpm, sim.throttle, eng.redline_rpm, dt)
        sim.step(dt)
        t0 = time.perf_counter()
        chunks.append(np.array(synth._render_block(BLOCK), dtype=np.float64))
        spent += time.perf_counter() - t0
        if int(t / 0.5) != int((t - dt) / 0.5):
            x = chunks[-1]
            log.append((t, sim.rpm, sim.throttle, sim._manifold_pressure() / 101325.0,
                        sim.drivetrain.gear_name,
                        synth.v2.core.level if voice == "v2" else 0.0))
        t += dt
    return np.concatenate(chunks), rate, log, spent / seconds


def save(path, x, rate):
    y = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(y.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", default="a3")
    ap.add_argument("--seconds", type=float, default=22.0)
    ap.add_argument("--dynamics", type=float, default=0.5)
    ap.add_argument("--out", default=".")
    ap.add_argument("--no-classic", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for voice in (["v2"] if a.no_classic else ["v2", "classic"]):
        x, rate, log, load = render(a.key, a.seconds, voice, a.dynamics)
        path = os.path.join(a.out, "%s_%s.wav" % (voice, a.key))
        save(path, x, rate)
        rms = math.sqrt(float(np.mean(x * x)))
        print("%s: %s  %.1f s at %d Hz, peak %.2f, rms %.3f, render load %.0f %% of real time"
              % (voice, path, a.seconds, rate, float(np.abs(x).max()), rms, 100.0 * load))
        if voice == "v2":
            for t, rpm, thr, mapf, gear, lvl in log[::2]:
                spl = 20.0 * math.log10(max(lvl, 1e-9) / 2.0e-5)
                print("   %5.1f s  %5.0f rpm  throttle %.2f  MAP %.2f bar  gear %-2s  %5.1f dB SPL at 1 m"
                      % (t, rpm, thr, mapf, gear, spl))


if __name__ == "__main__":
    main()
