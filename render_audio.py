"""
Render an engine to a WAV so you can listen without opening the app.

This auditions the car the way you actually hear one: an AUTOMATIC full-throttle
pull from rest, the gearbox launching and shifting up through every gear under
load (so the revs climb at a realistic rate and step down on each shift), then a
lift back to idle.  Writes engine_<key>.wav.

    py render_audio.py            # Ferrari 458
    py render_audio.py 3          # 1 single, 2 inline-4, 3 V8, 4 458
"""

import sys
import numpy as np

from engine_sim import Simulator, presets
from engine_sim.audio import Synthesizer, BLOCK

try:
    from scipy.io.wavfile import write as wav_write
except Exception:
    wav_write = None


def render(key="4", seconds=20.0):
    eng = presets.ALL[key]()
    sim = Simulator(eng)
    sim.ignition_on = True
    sim.drivetrain.auto = True            # automatic gearbox
    synth = Synthesizer(sim)
    sr = synth.sample_rate
    block_dt = BLOCK / sr

    chunks = []
    t = 0.0
    last_gear = None
    timeline = []
    while t < seconds:
        if t < 0.8:                        # crank
            sim.starter_engaged = True
            sim.throttle = 0.0
        elif t < 2.0:                      # settle to idle
            sim.starter_engaged = False
            sim.throttle = 0.0
        elif t < seconds - 2.0:            # full-throttle automatic pull
            sim.throttle = 1.0
        else:                              # lift back to idle
            sim.throttle = 0.0

        sim.drivetrain.auto_control(sim.rpm, sim.throttle, eng.redline_rpm, block_dt)
        sim.step(block_dt)
        chunks.append(synth._render_block(BLOCK))

        if sim.drivetrain.gear != last_gear:
            timeline.append((round(t, 1), sim.drivetrain.gear_name,
                             round(sim.rpm), round(sim.drivetrain.speed_kmh)))
            last_gear = sim.drivetrain.gear
        t += block_dt

    audio = np.concatenate(chunks)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = (audio / peak * 0.95 * 32767).astype(np.int16)

    out = f"engine_{key}_drive.wav"
    if wav_write is None:
        import wave
        with wave.open(out, "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(audio.tobytes())
    else:
        wav_write(out, sr, audio)

    print(f"wrote {out}  ({eng.name})  {len(audio)/sr:.1f}s @ {sr} Hz")
    print("  gear timeline (t, gear, rpm, km/h):")
    for row in timeline:
        print(f"    {row[0]:5.1f}s  gear {row[1]:>2}  {row[2]:5d} rpm  {row[3]:3d} km/h")


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "4"
    render(key)
