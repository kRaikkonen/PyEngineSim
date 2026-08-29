"""Export the per-block resonance parameters for the whole fleet.

Every number that tunes the pipe: the three round-trip delays at the live gas
temperature, the feedback gains carrying the radiation loss at the open end,
the exhaust valve's opening, the cat and muffler resonators, the Helmholtz
notch.  This is where a car stops being "an engine" and becomes ITS engine, so
it is sampled on all of them at idle, mid and full.

    py tools/export_resonance_ref.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.audio import Synthesizer                          # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402
from engine_sim.units import rpm_to_rads                          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "swift", "EngineSimCore", "Tests", "EngineSimCoreTests",
                   "Fixtures", "resonance.json")
RATE = 32000
POINTS = ((900.0, 0.0), (3000.0, 0.5), (6000.0, 1.0))


def main():
    out = {}
    for key in sorted(presets.ALL):
        sim = Simulator(presets.ALL[key]())
        sim.ignition_on = True
        syn = Synthesizer(sim, sample_rate=RATE, seed=1)
        syn.enabled = False
        pts = []
        for rpm, thr in POINTS:
            if rpm > sim.engine.redline_rpm * 1.05:
                continue
            sim.omega = rpm_to_rads(rpm)
            sim.throttle = thr
            for _ in range(200):
                sim._update_boost(1.0 / 200.0)
            c = sim.exhaust_sound_speed()
            syn._c_sm = c        # a settled smoother, as the Swift side assumes
            d1, d2, d3, g1, g2, g3, lp_a, f_helm = syn._resonance_params()
            pts.append({
                "rpm": rpm, "throttle": thr, "c": float(c),
                "d1": int(d1), "d2": int(d2), "d3": int(d3),
                "g1": float(g1), "g2": float(g2), "g3": float(g3),
                "lp_a": float(lp_a), "f_helm": float(f_helm),
                "valve": float(syn._valve), "flow": float(syn._flow),
                "post_fc": float(syn._post_fc), "sysq": float(syn._sysq),
                "rt60": float(syn._rt60),
                "rv_d": [int(v) for v in syn._rvD],
                "rv_g": [float(v) for v in syn._rvG],
                "rv_lp": float(syn._rv_lp),
                "lp_end": float(syn._lp_a_end),
                "wall_q": float(getattr(syn, "_wall_q", 1.0)),
            })
        out[key] = pts
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(out, fh)
    print("wrote %s: %d cars, %d points"
          % (OUT, len(out), sum(len(v) for v in out.values())))


if __name__ == "__main__":
    main()
