"""Bake each engine's gas-torque surface for the Swift driving model.

The Swift port reads a table rather than reimplementing torque_target -- the
BMEP model is charge temperature, knock coupling, compression ratio and a
diesel branch, and a second hand-written copy of that would drift from this
one the first time either changed.  Same reasoning as the VE and burn tables.

GAS torque only: before friction and before the ECU caps.  Friction is a
three-term polynomial the Swift evaluates itself (it needs it continuous and
signed, for engine braking), and the caps are two scalars.  Baking the NET
torque would throw away the negative half, which is exactly the half a lift
is made of.

    py tools/export_torque.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "engine_torque.json")
N_RPM, N_THR = 22, 9


def main():
    data = {}
    for key in sorted(presets.ALL):
        eng = presets.ALL[key]()
        sim = Simulator(eng)
        lo, hi = 300.0, eng.redline_rpm * 1.08
        rpms = [lo + (hi - lo) * i / (N_RPM - 1.0) for i in range(N_RPM)]
        thrs = [i / (N_THR - 1.0) for i in range(N_THR)]
        grid = []
        for t in thrs:
            tq, _ = sim.dyno_curve(rpms, throttle=t, raw_gas=True)
            grid.append([float(v) for v in tq])
        data[key] = {
            "rpm": rpms,
            "throttle": thrs,
            "gas": grid,                       # [throttle][rpm]
            "friction": [eng.friction_static, eng.friction_linear,
                         eng.friction_quad],
            "torque_limit_nm": float(eng.torque_limit_nm),
            "power_limit_kw": float(eng.power_limit_kw),
            "inertia": float(eng.flywheel_inertia),
            "idle_rpm": float(eng.idle_rpm),
            "redline_rpm": float(eng.redline_rpm),
            "gear_ratios": [float(g) for g in eng.gear_ratios],
            "final_drive": float(eng.final_drive),
            "wheel_radius": float(eng.wheel_radius),
            "vehicle_mass": float(eng.vehicle_mass),
        }
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB): %d cars, %dx%d grid"
          % (OUT, os.path.getsize(OUT) / 1024.0, len(data), N_RPM, N_THR))


if __name__ == "__main__":
    main()
