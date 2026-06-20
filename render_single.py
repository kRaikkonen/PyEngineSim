"""
Render truly ISOLATED single combustion explosions to audition the base firing
sound: a sharp pop, then the 1-3-5 chord + pipe ring out into silence, repeated.

    py render_single.py
"""

import numpy as np

from engine_sim import Simulator, presets
from engine_sim.audio import Synthesizer, BLOCK
from engine_sim.units import rpm_to_rads
from scipy.io.wavfile import write as wav_write


def render():
    sim = Simulator(presets.vw_ea888_i4())   # 4-cyl: one firing per 180 deg
    sim.ignition_on = True
    sim.throttle = 1.0
    syn = Synthesizer(sim)
    syn.agc_enabled = False                  # fixed gain -> no noise pumping in gaps
    # isolate the base explosion + its chord (trim pipe/reverb/intake)
    syn.params.update({"res1": 0.06, "res2": 0.05, "reverb": 0.12, "intake": 0.0})

    out = []
    sim.omega = rpm_to_rads(1800)
    for _ in range(8):                       # warm up the filters
        syn._render_block(BLOCK)

    for _ in range(6):                       # six isolated explosions
        # --- one sharp pop: advance the crank ~200 deg (one firing) fast ---
        sim.omega = rpm_to_rads(2200)
        for _ in range(3):
            out.append(syn._render_block(BLOCK))
        # --- silence: no new firing, let the chord + pipe ring decay -------
        sim.omega = 0.0
        for _ in range(60):
            out.append(syn._render_block(BLOCK))

    audio = np.concatenate(out)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = (audio / peak * 0.95 * 32767).astype(np.int16)
    wav_write("single_explosion.wav", syn.sample_rate, audio)
    print(f"wrote single_explosion.wav  {len(audio)/syn.sample_rate:.1f}s  "
          f"(6 isolated bangs, chord root {syn.params['firing_pitch']:.0f} Hz "
          f"= {syn.params['firing_pitch']:.0f}/{syn.params['firing_pitch']*1.25:.0f}/"
          f"{syn.params['firing_pitch']*1.5:.0f} Hz)")


if __name__ == "__main__":
    render()
