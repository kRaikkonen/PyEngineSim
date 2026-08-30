"""Capture a FULL render -- the whole chain, running free from block zero.

Every stage has already been checked one at a time with the filter cache and
the generator restored at its boundary.  That proves each stage; it does not
prove the WIRING, because restoring the state at every boundary hides exactly
the errors wiring produces: a bus fed from the wrong place, or two stages
drawing from the generator in the wrong order.

So this one restores nothing.  One seed at the start, then N blocks, and the
final output has to match sample for sample.

    py tools/export_endtoend_ref.py
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
                   "Fixtures", "endtoend.json")
RATE = 32000
FRAMES = 256
# long enough that the trackside case is not just silence: a mic 12 m off the
# line and 60 m down the road hears nothing for the first ~180 ms, so a short
# capture would "pass" on two all-zero buffers
BLOCKS = 30

CASES = [("rs3", "chase"), ("aven", "chase"), ("a3", "cockpit"),
         ("f2007", "trackside")]


def _cyl_scale(sim, syn, strength):
    """The per-cylinder amplitude the physics hands the pulse train."""
    P_ATM = 101325.0
    p_open = sim.blowdown_pressure() - 1.05 * P_ATM
    lb = getattr(sim, "last_blowdown", None)
    if lb is None:
        return [1.0] * sim.engine.num_cylinders
    lo = 0.06 if syn.vx.get("vacuum", True) else 0.30
    ref = max(p_open + 1.05 * P_ATM, 2.0 * P_ATM)
    return [min(max(v / ref, lo), 1.5) ** 0.8 for v in lb]


def capture(car, pov):
    sim = Simulator(presets.ALL[car]())
    sim.ignition_on = True
    syn = Synthesizer(sim, sample_rate=RATE, seed=1)
    syn.enabled = False
    syn.pov = pov

    syn.capture_stages = True
    out, states, taps, exc = [], [], [], []
    for bi in range(BLOCKS):
        rpm = sim.engine.idle_rpm + (sim.engine.redline_rpm
                                     - sim.engine.idle_rpm) * (bi / BLOCKS)
        sim.omega = rpm_to_rads(rpm)
        sim.throttle = 0.1 + 0.9 * (bi / BLOCKS)
        # moving, so the fly-by actually sweeps and the road noise runs
        sim.drivetrain.gear = 1 + (bi % max(sim.drivetrain.num_gears, 1))
        sim.drivetrain.v = 20.0 + 3.0 * bi
        for _ in range(40):
            sim._update_boost(1.0 / 200.0)
        # step() normally solves the burn multiplier every frame; this capture
        # forces the operating point instead of stepping, so solve it here.
        # Leaving it at its 3.0 initialiser would freeze the pulse strength at
        # a value the engine never actually runs at -- and the Swift, which
        # solves it, would look wrong for being right.
        sim._k_burn = sim._burn_k(sim.rpm, sim._manifold_pressure() / 101325.0)
        # the operating point EXACTLY as the synth will see it, so the Swift
        # starts each block from identical physics rather than re-deriving it
        dt = sim.drivetrain
        states.append({"speed": float(dt.v),
                       "gear": int(dt.gear), "num_gears": int(dt.num_gears),
                       "clutch": float(dt.clutch),
                       "wheel_radius": float(dt.wheel_radius),
                       "final_drive": float(dt.final_drive),
                       "gas_torque": float(sim.gas_torque),
                       "rpm": float(sim.rpm), "throttle": float(sim.throttle),
                       "boost": float(sim.boost),
                       "coolant_c": float(getattr(sim, "coolant_c", 88.0)),
                       # the muffler blow-out reads the PREVIOUS block's RMS,
                       # so it is the one value that couples blocks together
                       "last_level": float(syn.last_level)})
        syn._stage_full = {}
        out.append([float(v) for v in syn._render_block(FRAMES)])
        # every stage of THIS block, so a wiring error is reported where it
        # happens instead of as one number at the end
        taps.append({k: [float(x) for x in v[-1]]
                     for k, v in syn._stage_full.items()})
        bay_, bayi_ = syn._dbg_bay
        pov_sig, tk_x = syn._dbg_pov
        wet_, srcs_, er_ = syn._dbg_pipe
        st_, ld_, ch_, dp_, cr_, vl_ = syn._dbg_exc
        d1_, d2_, d3_, gg1, gg2, gg3, la_, fh_ = syn._dbg_res
        exc.append({"bay": [float(v) for v in bay_],
                    "bayi": [float(v) for v in bayi_],
                    "bayi_intake": [float(v) for v in syn._dbg_bayi1],
                    "bayi_spool": [float(v) for v in syn._dbg_bayi2],
                    "ind": [float(v) for v in syn._dbg_gw[0]],
                    "gw": [float(v) for v in syn._dbg_gw[1]],
                    "pov_sig": [float(v) for v in pov_sig], "tk_x": tk_x,
                    "d1": int(d1_), "d2": int(d2_), "d3": int(d3_),
                    "g1": float(gg1), "g2": float(gg2), "g3": float(gg3),
                    "lp_a": float(la_), "lp_end": float(syn._lp_a_end),
                    "res1": float(syn.params["res1"]),
                    "res2": float(syn.params["res2"]),
                    "wet": [float(v) for v in wet_],
                    "er": [float(v) for v in er_],
                    "srcs": [[float(v) for v in c_] for c_ in srcs_],
                    "strength": st_, "load": ld_, "choke": ch_, "dps": dp_,
                    "c_runner": cr_, "valve": vl_,
                    "cyl_scale": [float(v) for v in _cyl_scale(sim, syn, st_)]})
    return {"pov": pov, "states": states, "out": out, "taps": taps, "exc": exc,
            "params": {k: float(v) for k, v in syn.params.items()
                       if isinstance(v, (int, float))},
            "volume": float(syn.volume)}


def main():
    data = {}
    for car, pov in CASES:
        if car not in presets.ALL:
            print("  skip %s" % car)
            continue
        data["%s-%s" % (car, pov)] = capture(car, pov)
        print("  %-18s %d blocks" % ("%s/%s" % (car, pov), BLOCKS))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
