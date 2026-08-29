"""Capture the muffler run -- head duct to tailpipe -- for the Swift port.

This is the longest single stretch in the chain and the one that turns an
engine into A CAR: the turbine and its wastegate, the cat, the particulate
filter, the standing-wave whine, the de-drone notch, the box with all of its
colour, and the active valve.

Two things here are HISTORY-dependent, so a block's output is not a pure
function of its input, and the reference has to carry both:

  the filter cache  its key rounds the cutoff into 8 Hz buckets but the design
                    uses the exact frequency of whichever call missed first
  the RNG           earlier stages draw from it, and the screamer pipe draws

So each block records the cache and the generator state as they were when the
Python reached this point, and the Swift restores them.  Anything less would be
comparing two different filters and calling the difference rounding.

    py tools/export_muffler_ref.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.audio import Synthesizer                          # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402
from engine_sim.units import rpm_to_rads                          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "swift", "EngineSimCore", "Tests", "EngineSimCoreTests",
                   "Fixtures", "muffler.json")
RATE = 32000
BLOCKS = 6

# One car for each thing the run does differently, so no branch goes untested.
CARS = [
    "rs3",          # turbo 5, internal gate, cat
    "aven",         # big NA V12, open system
    "8",            # 2-valve supercharged V8
    "787b",         # rotary
    "veyron",       # quad turbo
    "a45",          # GPF + flex pipe + turbo 4
    "hoonrs",       # EXTERNAL wastegate -- the screamer pipe, and it draws RNG
    "e63",          # hot-V twin turbo (whine suppressed)
    "1",            # absorptive box + two-stage lift
    "f2007",        # the highest-revving thing in the fleet
    "actros",       # diesel, long system -> heavy air absorption
    "991rs",        # NA flat six, two-stage lift
]

TAPS = ("header", "head/port", "catalytic", "standing-wave", "resonator",
        "muffler", "valve bypass")


def key_of(k):
    """The Python cache key as the Swift spells it."""
    if k[0] == "bw":
        return "bw|%d|%d|%s" % (k[1], k[2], k[3])
    return "pk|%d|%d|%d" % (k[1], k[2], k[3])


def capture(car):
    sim = Simulator(presets.ALL[car]())
    sim.ignition_on = True
    syn = Synthesizer(sim, sample_rate=RATE, seed=1)
    syn.enabled = False
    syn.capture_stages = True
    frames = 256

    blocks = []
    for bi in range(BLOCKS):
        # a rising pull, so the sliding filters actually slide
        rpm = sim.engine.idle_rpm + (sim.engine.redline_rpm
                                     - sim.engine.idle_rpm) * (bi / BLOCKS)
        sim.omega = rpm_to_rads(rpm)
        sim.throttle = 0.15 + 0.85 * (bi / BLOCKS)
        for _ in range(40):
            sim._update_boost(1.0 / 200.0)

        syn._stage_full = {}
        # snapshot the two history-carrying things BEFORE the block
        cache_before = {key_of(k): [list(map(float, v[0])),
                                    list(map(float, v[1]))]
                        for k, v in syn._fcache.items()}
        rng = syn._rng
        rng_before = [int(rng.s0), int(rng.s1), int(rng.s2), int(rng.s3)]
        spare_before = rng._spare
        level_before = syn.last_level      # the blow-out reads LAST block's RMS

        syn._render_block(frames)

        got = syn._stage_full
        if "valve bypass" not in got:
            return None
        # the cache the block ACTUALLY used is the union of what it started
        # with and what it designed on the way; the Swift only needs the
        # starting state, but a design the Python made BEFORE this stage in the
        # same block would be missing -- so take the state after the block and
        # let the Swift look up rather than design.  Same answer, no drift.
        cache_after = {key_of(k): [list(map(float, v[0])),
                                   list(map(float, v[1]))]
                       for k, v in syn._fcache.items()}

        D1, D2, D3, g1, g2, g3, lp_a, f_helm = syn._dbg_res
        blocks.append({
            "rpm": float(sim.rpm),
            "throttle": float(sim.throttle),
            "boost": float(sim.boost),
            "choke": float(getattr(syn, "_dbg_choke", 0.0)),
            "cold": float(syn._cold),
            # NOTE two different sound speeds: the delays use the SMOOTHED one
            # (the pipe cannot follow the gas), the rest of the run uses the
            # live value.  Conflating them would detune the whole box.
            "c_smooth": float(syn._c_sm),
            "c_runner": float(max(sim.exhaust_sound_speed(), 300.0)),
            "last_level": float(level_before),
            "resonance": {
                "d1": int(D1), "d2": int(D2), "d3": int(D3),
                "g1": float(g1), "g2": float(g2), "g3": float(g3),
                "lp_a": float(lp_a), "f_helm": float(f_helm),
                "valve": float(syn._valve), "flow": float(syn._flow),
                "post_fc": float(syn._post_fc), "sysq": float(syn._sysq),
                "rt60": float(syn._rt60),
                "rv_d": [int(v) for v in syn._rvD],
                "rv_g": [float(v) for v in syn._rvG],
                "rv_lp": float(syn._rv_lp),
                "lp_end": float(syn._lp_a_end),
            },
            "rng": {"state": [str(v) for v in rng_before],
                    "spare": None if spare_before is None
                             else float(spare_before)},
            "cache": cache_before,
            "cache_after": cache_after,
            # "head/port" is tapped TWICE per block -- once after the head
            # duct, once after the turbine and its wastegate.  The first is
            # this run's input; the second is what it must reproduce.
            "taps": dict(
                [(name, [float(v) for v in got[name][-1]])
                 for name in TAPS if name in got]
                + [("head/port-in", [float(v) for v in got["head/port"][0]]),
                   ("head/port-out", [float(v) for v in got["head/port"][-1]])]),
            "head_port_count": len(got.get("head/port", [])),
        })
    return {"blocks": blocks,
            "params": {k: float(v) for k, v in syn.params.items()
                       if isinstance(v, (int, float))}}


def main():
    data = {}
    for car in CARS:
        if car not in presets.ALL:
            print("  skip %s (not in presets)" % car)
            continue
        got = capture(car)
        if got is None:
            print("  skip %s (no valve-bypass tap)" % car)
            continue
        data[car] = got
        print("  %-12s %d blocks" % (car, len(got["blocks"])))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
