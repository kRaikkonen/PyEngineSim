"""Reference values for the Swift physics, over the whole 130-car fleet.

The renderer only reaches into the physics for nine dynamic quantities --
blowdown_pressure, exhaust_sound_speed, gas_torque, boost, rpm, omega,
throttle, ignition_on, hybrid_on -- plus static engine geometry.  So that is
all the Swift has to reproduce, and this pins it: every car, on a grid of rpm
and throttle, with the derived geometry alongside.

Every car, not a sample: a port that works on an inline-4 and quietly breaks
on a rotary or a radial is exactly the failure this is meant to catch.

    py tools/make_swift_physics_fixture.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402
from engine_sim.units import rpm_to_rads                          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "swift", "EngineSimCore", "Tests", "EngineSimCoreTests",
                   "Fixtures", "physics.json")

RPMS = (900.0, 2500.0, 4500.0, 7000.0)
THROTTLES = (0.0, 0.35, 1.0)


def sample(key):
    eng = presets.ALL[key]()
    sim = Simulator(eng)
    sim.ignition_on = True
    out = {
        "name": eng.name,
        "num_cylinders": eng.num_cylinders,
        "displacement_m3": eng.total_displacement,
        "firing_order": list(eng.firing_order),
        "exhaust_tone_hz": eng.exhaust_tone,
        "idle_rpm": eng.idle_rpm,
        "redline_rpm": eng.redline_rpm,
        "cycle_offsets_deg": [c.cycle_offset_deg for c in eng.cylinders],
        "bank_angles_deg": [c.bank_angle_deg for c in eng.cylinders],
        "clearance_volume_m3": eng.cylinders[0].clearance_volume,
        "piston_area_m2": eng.cylinders[0].piston_area,
        "points": [],
    }
    for rpm in RPMS:
        if rpm > eng.redline_rpm * 1.05:
            continue
        for thr in THROTTLES:
            sim.omega = rpm_to_rads(rpm)
            sim.throttle = thr
            # Settle the same state a running sim would carry.  The burn
            # multiplier matters most: blowdown_pressure() reads self._k_burn,
            # which is only ever solved inside step() -- capture it unsolved
            # and every car in this reference is frozen at the constructor's
            # placeholder 3.0, which is not what the sound is made of.
            for _ in range(200):
                sim._update_boost(1.0 / 200.0)
            sim._k_burn = sim._burn_k(sim.rpm,
                                      sim._manifold_pressure() / 101325.0)
            out["points"].append({
                "rpm": rpm,
                "throttle": thr,
                "boost_bar": sim.boost,
                "manifold_pressure_pa": sim._manifold_pressure(),
                "blowdown_pressure_pa": sim.blowdown_pressure(),
                "exhaust_sound_speed_ms": sim.exhaust_sound_speed(),
            })
    return out


def main():
    data = {}
    for key in sorted(presets.ALL):
        try:
            data[key] = sample(key)
        except Exception as exc:                     # a broken car is a finding
            data[key] = {"error": "%s: %s" % (type(exc).__name__, exc)}
            print("  !! %s: %s" % (key, exc))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    pts = sum(len(v.get("points", [])) for v in data.values())
    print("wrote %s (%.0f KB): %d cars, %d operating points"
          % (OUT, os.path.getsize(OUT) / 1024.0, len(data), pts))


if __name__ == "__main__":
    main()
