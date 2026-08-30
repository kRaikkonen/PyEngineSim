"""Capture the MASTER run -- auto-level, distance, road noise, broadcast
compression, the idle chop, anti-harshness, limiter, output.

The level control is the interesting part and the easy one to get subtly
wrong: it estimates loudness on a high-passed COPY so bass rides on top
instead of spending the gain budget, its ceiling follows combustion so an
overrun does not pump the noise floors up, and it near-freezes trackside so
the fly-by's 1/r sweep survives.  All three behaviours need a trajectory that
actually varies, so the capture pulls, lifts, and moves.

    py tools/export_master_ref.py
"""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.audio import Synthesizer                          # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402
from engine_sim.units import rpm_to_rads                          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "swift", "EngineSimCore", "Tests", "EngineSimCoreTests",
                   "Fixtures", "master.json")
RATE = 32000

CASES = [
    ("rs3", "chase"),
    ("rs3", "cockpit"),        # no road noise at all in here
    ("f2007", "chase"),        # a screamer: broadcast compression runs
    ("f2007", "trackside"),    # ...and does NOT, and the level near-freezes
    ("aven", "chase"),
    ("a3", "cockpit"),         # a four with no balance shaft: the idle chop
    ("actros", "chase"),       # diesel: the anti-harshness at low revs
]

# pull hard, lift, then pull again -- the level control, the combustion
# ceiling and the compressor all need something to react to
TRAJ = [(0.15, 0.20), (0.35, 0.90), (0.55, 1.00), (0.72, 1.00),
        (0.85, 1.00), (0.88, 0.00), (0.84, 0.00), (0.80, 0.30),
        (0.86, 1.00), (0.95, 1.00)]


def key_of(k):
    if k[0] == "bw":
        return "bw|%d|%d|%s" % (k[1], k[2], k[3])
    return "pk|%d|%d|%d" % (k[1], k[2], k[3])


def capture(car, pov):
    sim = Simulator(presets.ALL[car]())
    sim.ignition_on = True
    syn = Synthesizer(sim, sample_rate=RATE, seed=1)
    syn.enabled = False
    syn.capture_stages = True
    syn.pov = pov
    frames = 256

    blocks = []
    for bi, (rev, thr) in enumerate(TRAJ):
        rpm = sim.engine.idle_rpm + (sim.engine.redline_rpm
                                     - sim.engine.idle_rpm) * rev
        sim.omega = rpm_to_rads(rpm)
        sim.throttle = thr
        sim.drivetrain.gear = 1 + (bi % max(sim.drivetrain.num_gears, 1))
        sim.drivetrain.v = 5.0 + 6.0 * bi          # moving, so road noise runs
        for _ in range(40):
            sim._update_boost(1.0 / 200.0)

        syn._stage_full = {}
        cache_before = {key_of(k): [list(map(float, v[0])),
                                    list(map(float, v[1]))]
                        for k, v in syn._fcache.items()}
        # the master's own carried state, as it stands BEFORE the block
        before = {
            "level": float(syn._level), "gain": float(syn._gain),
            "f1_env": float(getattr(syn, "_f1c_env", 0.0)),
            "f1_gain": float(getattr(syn, "_f1c_g", 1.0)),
            "wob_ph": float(syn._wob_ph),
            "aa_cut": (None if not hasattr(syn, "_aa_cut")
                       else float(syn._aa_cut)),
            "lim": (None if not hasattr(syn, "_lim") else float(syn._lim)),
        }

        out = syn._render_block(frames)
        sig0, rng0, spare0 = syn._dbg_master

        blocks.append({
            "rpm": float(sim.rpm), "speed": float(sim.drivetrain.v),
            "dps": float(math.degrees(sim.omega) / RATE * syn.time_scale),
            "comb_load": float(syn._comb_load),
            "cam_lump": float(getattr(syn, "_cam_lump", 0.0)
                              + getattr(syn, "_balance_rough", 0.0)),
            "wob_w": float(syn._wob_w),
            "state": before,
            "sig": [float(v) for v in sig0],
            "rng": {"state": [str(v) for v in rng0],
                    "spare": None if spare0 is None else float(spare0)},
            "cache": cache_before,
            "cache_after": {key_of(k): [list(map(float, v[0])),
                                        list(map(float, v[1]))]
                            for k, v in syn._fcache.items()},
            "out": [float(v) for v in out],
            "last_level": float(syn.last_level),
        })
    return {"blocks": blocks, "pov": pov,
            "volume": float(syn.volume),
            "agc": bool(syn.agc_enabled),
            "params": {k: float(v) for k, v in syn.params.items()
                       if isinstance(v, (int, float))}}


def main():
    data = {}
    for car, pov in CASES:
        if car not in presets.ALL:
            print("  skip %s" % car)
            continue
        got = capture(car, pov)
        data["%s-%s" % (car, pov)] = got
        print("  %-18s %d blocks" % ("%s/%s" % (car, pov), len(got["blocks"])))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
