"""Bake the deep physics into per-engine lookup tables for the Swift build.

Two quantities sit between the throttle and the sound: volumetric efficiency
and the burn multiplier.  Both are computed in Python by a chain that is
genuinely deep -- ve_model's Mach index and Helmholtz ram tuning, bmep_model's
energy accounting, a per-engine burn calibration, torque and power limits.
Reimplementing all of that in Swift would be a large surface with a large
chance of drifting a few percent away from what was tuned by ear.

But both are functions of exactly (rpm, MAP fraction), and the Python already
evaluates VE through a 22x12 table.  So bake them: the NUMBERS still come from
the real physics, and the phone does a bilinear lookup.  That is the same
offline-physics -> runtime-surrogate architecture the project already uses,
applied one level further out.

What stays real code in Swift is the part that is short and exact -- the MAP
orifice/pump balance, adiabatic compression and expansion, the blowdown
closed form -- because those are a dozen lines each and reproduce exactly.

    py tools/export_engine_tables.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "engine_tables.json")

N_RPM = 48
N_MAP = 28


def r6(v):
    """Six significant figures: far finer than anything downstream can hear,
    and it nearly halves the file."""
    return float("%.6g" % float(v))


def tables_for(key):
    eng = presets.ALL[key]()
    sim = Simulator(eng)
    rpm_lo = max(eng.idle_rpm * 0.5, 200.0)
    rpm_hi = eng.redline_rpm * 1.08
    rpms = np.linspace(rpm_lo, rpm_hi, N_RPM)
    # MAP fraction spans deep vacuum to full boost
    map_hi = 1.0 + max(eng.boost_bar, 0.0)
    mapfs = np.linspace(0.05, max(map_hi, 1.0), N_MAP)

    kb = np.empty((N_RPM, N_MAP))
    for i, rpm in enumerate(rpms):
        sim.omega = rpm * 2.0 * np.pi / 60.0
        for j, mf in enumerate(mapfs):
            kb[i, j] = sim._burn_k(float(rpm), float(mf))

    # VE is exported VERBATIM -- the Python already evaluates it through a
    # table, so resampling it onto a different grid would interpolate an
    # interpolation and lose accuracy for nothing.
    lut = sim._ve_lut
    ve_axes = ([float(v) for v in lut.grids[0]], [float(v) for v in lut.grids[1]])
    # full precision: only 22x12 numbers per car, and rounding them
    # showed up as a 1e-4 wobble in the solved MAP
    ve_vals = [[float(v) for v in row] for row in lut.values]

    return {
        "rpm_axis": [float(v) for v in rpms],
        "map_axis": [float(v) for v in mapfs],
        "ve_rpm_axis": ve_axes[0],
        "ve_map_axis": ve_axes[1],
        "ve": ve_vals,
        "k_burn": [[r6(v) for v in row] for row in kb],
        "map_idle_area": float(sim._map_idle_area),
        # charge temperature is a 1-D function of the manifold ratio at zero
        # heat-soak; sampled on the same MAP axis
        "charge_temp": [r6(sim_charge_temp(sim, float(mf))) for mf in mapfs],
    }


# NO try/except around this import.  It was wrong once (charge_temp lives in
# bmep_model, not surrogate) and a silent fallback to the legacy formula hid it
# for two rounds -- the tables shipped a 60 K charge-temperature error that only
# showed up as 41 cents of pitch on a boosted car.  If the import breaks, this
# should stop, loudly.
from engine_sim.bmep_model import charge_temp                     # noqa: E402


def sim_charge_temp(sim, mapf):
    """Charge temp the way exhaust_gas_temp asks for it (cold intercooler)."""
    return charge_temp(sim.engine, mapf, 0.0)


def main():
    out = {}
    bad = []
    for key in sorted(presets.ALL):
        try:
            out[key] = tables_for(key)
        except Exception as exc:
            bad.append("%s: %s" % (key, exc))
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(out, fh)
    print("wrote %s (%.0f KB): %d cars, %dx%d grids"
          % (OUT, os.path.getsize(OUT) / 1024.0, len(out), N_RPM, N_MAP))
    for b in bad:
        print("  !! %s" % b)


if __name__ == "__main__":
    main()
