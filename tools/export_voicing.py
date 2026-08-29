"""Export the per-engine VOICING setup -- everything the pulse train needs.

All of this is computed once, at Synthesizer construction, and never changes
while the engine runs: firing offsets, per-cylinder decay/level scatter, the
header bank offsets, runner lengths, the exhaust-channel map, the blowdown
sharpness, and the CylinderVoicing amplitudes, edges and runner-damping filter
coefficients.

Some of it is drawn from a FIXED-SEED numpy generator (audio.py seeds one with
20240517 so the per-cylinder character is deterministic per car, not random per
launch).  Reproducing numpy's generator in Swift to regenerate constants would
be absurd -- so the constants are exported instead.  The per-block jitter,
which really is fresh every block, uses the PortableRNG both sides implement.

    py tools/export_voicing.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.audio import Synthesizer                          # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "engine_voicing.json")
RATE = 32000


def voicing_for(key):
    sim = Simulator(presets.ALL[key]())
    syn = Synthesizer(sim, sample_rate=RATE, seed=1)
    v = syn._cyl_voice
    out = {
        "offsets": [float(x) for x in syn._offsets],
        "cyl_tau": [float(x) for x in syn._cyl_tau],
        "cyl_amp": [float(x) for x in syn._cyl_amp],
        "header_offset": [float(x) for x in syn._header_offset],
        "runner_len": [float(x) for x in syn._runner_len],
        "channel_of": [int(x) for x in syn._channel_of],
        "nchan": int(syn._nchan),
        "bd_sharp": float(syn._bd_sharp),
        "stroke_ref": float(syn._stroke_ref),
        "params": {k: float(syn.params[k])
                   for k in ("pulse_tau", "attack_deg", "cyl_spread",
                             "cyl_voice")},
    }
    if v is not None:
        out["voice_amp"] = [float(x) for x in v.amp]
        out["voice_edge"] = [float(x) for x in v.edge]
        # the runner HF-damping one-pole per cylinder
        out["damp_b"] = [[float(x) for x in b] for b in v._b]
        out["damp_a"] = [[float(x) for x in a] for a in v._a]
    return out


def main():
    data = {}
    for key in sorted(presets.ALL):
        data[key] = voicing_for(key)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB): %d cars"
          % (OUT, os.path.getsize(OUT) / 1024.0, len(data)))


if __name__ == "__main__":
    main()
