"""Capture the BAY BUS -- intake, trumpets, spool, blow-off, gearbox.

The exhaust is only half of a car.  From the driver's seat the intake roar,
the individual-throttle howl, the blower or turbo, the dump valve and the
gearbox are most of what tells two engines apart, so they get the same
sample-for-sample treatment as the pipe.

The blow-off has to be provoked: it fires on a LIFT (the pedal dropping well
below where it lately was, while boost is still up), so the trajectory here
pulls hard and then snaps shut.  Without that the valve branch would never run
and the port would be "verified" with its most audible feature untested.

    py tools/export_induction_ref.py
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
                   "Fixtures", "induction.json")
RATE = 32000
FRAMES = 256

# (car, ssqv, flutter) -- one per induction hardware, plus the valve variants
CARS = [
    ("rs3", False, False),      # turbo 5, recirc valve
    ("rs3", True, False),       # ...the same car with an atmospheric SSQV
    ("rs3", False, True),       # ...and with no valve at all: surge flutter
    ("aven", False, False),     # NA V12 with individual throttle bodies
    ("8", False, False),        # roots blower
    ("veyron", False, False),   # quad turbo
    ("f2007", False, False),    # screamer + straight-cut box
    ("787b", False, False),     # rotary, ITB, straight-cut
    ("rx7", False, False),      # SEQUENTIAL twins: the hand-over surge
    ("deltas4", False, False),  # TWINCHARGE: blower crossfading into turbo
    ("evo7", False, False),     # twin-scroll
    ("merlin", False, False),   # centrifugal blower
    ("sf25", False, False),     # hybrid PU: MGU-K and MGU-H whines
]

# a hard pull then a snap shut, so the lift-off branch actually fires
TRAJ = [(0.20, 0.30), (0.45, 0.85), (0.62, 1.00), (0.75, 1.00),
        (0.80, 1.00), (0.82, 0.00), (0.82, 0.00), (0.80, 0.00),
        (0.78, 0.00), (0.76, 0.10), (0.74, 0.60), (0.72, 1.00)]


def key_of(k):
    if k[0] == "bw":
        return "bw|%d|%d|%s" % (k[1], k[2], k[3])
    return "pk|%d|%d|%d" % (k[1], k[2], k[3])


def capture(car, ssqv, flutter):
    sim = Simulator(presets.ALL[car]())
    sim.ignition_on = True
    syn = Synthesizer(sim, sample_rate=RATE, seed=1)
    syn.enabled = False
    syn.ssqv, syn.flutter = ssqv, flutter

    blocks = []
    for rev_frac, thr in TRAJ:
        rpm = sim.engine.idle_rpm + (sim.engine.redline_rpm
                                     - sim.engine.idle_rpm) * rev_frac
        sim.omega = rpm_to_rads(rpm)
        sim.throttle = thr
        for _ in range(40):
            sim._update_boost(1.0 / 200.0)
        # IN GEAR and moving: in neutral the whole gearbox model is dead code,
        # and half the spool branches never see load.  Walk up the box so the
        # selected-gear mesh changes pitch between blocks, which is the thing
        # that model exists to do.
        dt = sim.drivetrain
        dt.gear = 1 + (len(blocks) % max(dt.num_gears, 1))
        dt.clutch = 1.0
        dt.v = 8.0 + 4.0 * len(blocks)

        rng = syn._rng
        state = {
            "rpm": float(sim.rpm), "throttle": float(sim.throttle),
            "boost": float(sim.boost),
            "thr_ref": float(syn._thr_ref), "bov_env": float(syn._bov_env),
            "bov_pr0": float(getattr(syn, "_bov_pr0", 0.7)),
            "seq_prev": float(syn._seq_prev), "seq_surge": float(syn._seq_surge),
            "flutter_phase": float(syn._flutter_phase),
            "bov_prev": float(getattr(syn, "_bov_prev", 0.0)),
            "phases": {n: float(getattr(syn, "_%s_phase" % n))
                       for n in ("whine", "mesh", "seq", "seq2", "itb",
                                 "gearbox", "gwinput", "gwa", "gwb",
                                 "finaldrive")},
            "rng": {"state": [str(int(v)) for v in (rng.s0, rng.s1, rng.s2,
                                                    rng.s3)],
                    "spare": None if rng._spare is None else float(rng._spare)},
            "cache": {key_of(k): [list(map(float, v[0])), list(map(float, v[1]))]
                      for k, v in syn._fcache.items()},
            "drive": {
                "gear": int(sim.drivetrain.gear),
                "num_gears": int(sim.drivetrain.num_gears),
                "clutch": float(sim.drivetrain.clutch),
                "speed": float(sim.drivetrain.v),
                "wheel_radius": float(sim.drivetrain.wheel_radius),
                "final_drive": float(sim.drivetrain.final_drive),
                "gas_torque": float(sim.gas_torque),
            },
        }
        ind, gw = syn._induction_audio(FRAMES)
        state["ind"] = [float(v) for v in ind]
        state["gw"] = [float(v) for v in gw]
        state["bov_env_after"] = float(syn._bov_env)
        state["cache_after"] = {key_of(k): [list(map(float, v[0])),
                                            list(map(float, v[1]))]
                                for k, v in syn._fcache.items()}
        blocks.append(state)
    return {"blocks": blocks,
            "params": {k: float(v) for k, v in syn.params.items()
                       if isinstance(v, (int, float))},
            "ssqv": ssqv, "flutter": flutter}


def main():
    data = {}
    for car, ssqv, flutter in CARS:
        if car not in presets.ALL:
            print("  skip %s" % car)
            continue
        name = "%s%s" % (car, "-ssqv" if ssqv else ("-flutter" if flutter else ""))
        got = capture(car, ssqv, flutter)
        fired = sum(1 for b in got["blocks"] if b["bov_env_after"] > 1e-3)
        data[name] = got
        print("  %-14s %d blocks, valve active in %d" % (name, len(got["blocks"]),
                                                         fired))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
