"""Capture the EXIT run -- de-honk, metal ring, megaphone, thunder,
reflection, radiation, tailpipe -- for the Swift port.

This is where in-duct pressure becomes something a microphone behind the car
would record, so it is the part most likely to be "close enough" and wrong.
The radiation stage in particular is a blend that follows the listener's
range, so every POV is captured, and the wall formants come from the pipe
MATERIAL, so a titanium and a cast-iron car are both here.

    py tools/export_exit_ref.py
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
                   "Fixtures", "exit.json")
RATE = 32000
BLOCKS = 6

# (car, pov) -- the radiation blend depends on range, so all three are here
CASES = [
    ("rs3", "chase"),
    ("rs3", "cockpit"),
    ("rs3", "trackside"),
    ("aven", "chase"),       # big NA V12: thunder and rumble at full strength
    ("f2007", "chase"),      # megaphone, and a jet whose roar IS its low end
    ("7", "trackside"),      # the other megaphone car
    ("787b", "chase"),       # rotary
    ("actros", "chase"),     # diesel: long system, big slugs
    ("a3", "cockpit"),       # the new car, in the seat it will be heard from
]

TAPS = ("valve bypass", "wall de-honk", "metal ring", "megaphone", "thunder",
        "reflection", "radiation", "tailpipe exit")


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
        sim.throttle = 0.15 + 0.85 * (bi / BLOCKS)
        for _ in range(40):
            sim._update_boost(1.0 / 200.0)

        syn._stage_full = {}
        # the rumble reads the crank AFTER the block advances it, so the value
        # to hand the Swift is the post-block one -- captured below.  The RNG
        # and the input signal come from the stash AT the stage boundary: the
        # induction section runs in between and draws from the same generator.
        rad_prev = float(getattr(syn, "_rad_prev", 0.0))
        gear_phase = float(syn._gear_phase)
        cache_before = {key_of(k): [list(map(float, v[0])),
                                    list(map(float, v[1]))]
                        for k, v in syn._fcache.items()}

        syn._render_block(frames)
        got = syn._stage_full
        if "tailpipe exit" not in got:
            return None

        exit_sig, exit_rng, exit_spare = syn._dbg_exit
        blocks.append({
            "rpm": float(sim.rpm), "throttle": float(sim.throttle),
            "c_runner": float(max(sim.exhaust_sound_speed(), 300.0)),
            "dps": float(math.degrees(sim.omega) / RATE * syn.time_scale),
            "crank": float(syn._audio_crank),   # post-advance, as the rumble saw
            "comb_load": float(syn._comb_load),
            "rad_prev": rad_prev,
            "gear_phase": gear_phase,
            "u_abs": float(getattr(syn, "_u_abs", 0.0)),
            "resonance": {
                "rt60": float(syn._rt60), "rv_lp": float(syn._rv_lp),
                "sysq": float(syn._sysq), "flow": float(syn._flow),
            },
            "input": [float(v) for v in exit_sig],
            "rng": {"state": [str(v) for v in exit_rng],
                    "spare": None if exit_spare is None else float(exit_spare)},
            "cache": cache_before,
            "cache_after": {key_of(k): [list(map(float, v[0])),
                                        list(map(float, v[1]))]
                            for k, v in syn._fcache.items()},
            "taps": {name: [float(v) for v in got[name][-1]]
                     for name in TAPS if name in got},
        })
    return {"blocks": blocks, "pov": pov,
            "nchan": int(syn._nchan),
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
            print("  skip %s (no tailpipe tap)" % car)
            continue
        data["%s-%s" % (car, pov)] = got
        print("  %-18s %d blocks" % ("%s/%s" % (car, pov), len(got["blocks"])))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
