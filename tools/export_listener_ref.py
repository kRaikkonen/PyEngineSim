"""Capture the LISTENER run -- overrun darkening, EQ, and the perspective.

Two radiators and one listener.  Everything here is geometry: spreading,
path-difference delay, the composite panel (openings leak flat, sheet metal
obeys the mass law), the structure-borne path that bypasses the panel because
it is not airborne, the cabin's own standing wave, the tarmac bounce, and the
trackside fly-by whose ramping delay IS the Doppler.

All three perspectives are captured because they exercise entirely different
code: only the cockpit has a partition and a boom, only the chase has a ground
reflection, only trackside has the fly-by.  A single POV would leave two
thirds of this stage unverified.

    py tools/export_listener_ref.py
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
                   "Fixtures", "listener.json")
RATE = 32000
BLOCKS = 8

CASES = [
    ("rs3", "chase"), ("rs3", "cockpit"), ("rs3", "trackside"),
    ("f2007", "cockpit"),    # a RACE shell: stripped, so alpha and mass differ
    ("f2007", "trackside"),  # ...and the fly-by that only this POV runs
    ("aven", "chase"),
    ("actros", "cockpit"),   # a sealed cab: the other end of the NR range
    ("a3", "cockpit"),
]

TAPS = ("tailpipe exit", "EQ", "cabin/room")


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
    for bi in range(BLOCKS):
        rpm = sim.engine.idle_rpm + (sim.engine.redline_rpm
                                     - sim.engine.idle_rpm) * (bi / BLOCKS)
        sim.omega = rpm_to_rads(rpm)
        sim.throttle = 0.1 + 0.9 * (bi / BLOCKS)
        # moving, so the trackside fly-by actually sweeps past the mic
        sim.drivetrain.gear = 1 + (bi % max(sim.drivetrain.num_gears, 1))
        sim.drivetrain.v = 20.0 + 8.0 * bi
        for _ in range(40):
            sim._update_boost(1.0 / 200.0)

        syn._stage_full = {}
        cache_before = {key_of(k): [list(map(float, v[0])),
                                    list(map(float, v[1]))]
                        for k, v in syn._fcache.items()}
        track_x = float(getattr(syn, "_tk_x", -60.0))

        syn._render_block(frames)
        got = syn._stage_full
        if "cabin/room" not in got:
            return None
        sig0, bay0, bayi0, rng0, spare0 = syn._dbg_listen

        blocks.append({
            "rpm": float(sim.rpm), "throttle": float(sim.throttle),
            "speed": float(sim.drivetrain.v),
            "dps": float(math.degrees(sim.omega) / RATE * syn.time_scale),
            "crank": float(syn._audio_crank),
            "comb_load": float(syn._comb_load),
            "inj_amt": float(getattr(syn, "_inj_amt", 0.0)),
            "track_x": track_x,
            "sig": [float(v) for v in sig0],
            "bay": [float(v) for v in bay0],
            "bayi": [float(v) for v in bayi0],
            "rng": {"state": [str(v) for v in rng0],
                    "spare": None if spare0 is None else float(spare0)},
            "cache": cache_before,
            "cache_after": {key_of(k): [list(map(float, v[0])),
                                        list(map(float, v[1]))]
                            for k, v in syn._fcache.items()},
            "taps": {name: [float(v) for v in got[name][-1]]
                     for name in TAPS if name in got},
        })
    return {"blocks": blocks, "pov": pov,
            "params": {k: float(v) for k, v in syn.params.items()
                       if isinstance(v, (int, float))}}


def main():
    data = {}
    for car, pov in CASES:
        if car not in presets.ALL:
            print("  skip %s" % car)
            continue
        got = capture(car, pov)
        if got is None:
            print("  skip %s (no cabin tap)" % car)
            continue
        data["%s-%s" % (car, pov)] = got
        print("  %-18s %d blocks" % ("%s/%s" % (car, pov), len(got["blocks"])))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
