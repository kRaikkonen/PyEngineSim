"""Bake the white-box turbo machine's SIZING for the Swift build (plan s.10).

turbo.build sizes every car's turbocharger from the engine: a 12-step
compressor match, a 30-step turbine bisection over the steady shaft balance,
and (the closed loop) the whole thing twice, around a VE rebuilt on the
turbine's own back-pressure.  That is seconds of work per car and needs the
deep VE/BMEP chain, so it stays here.  What it PRODUCES is a handful of
numbers -- each wheel's diameter, the turbine and wastegate areas, the
plenum, the valve, the controller's limits -- and the phone runs the machine
itself, live, from them (EngineSimCore/Turbo.swift).

    py tools/export_turbo.py        # adds "turbo" to docs/engine_tables.json

export_engine_tables.py calls spec_for() too, so a full re-export keeps it.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim import turbo as turbo_mod                         # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "docs")


def spec_for(sim):
    """The built machine of ``sim`` (None for a car without one), full
    precision: the dynamics are integrated from these, and a rounded wheel
    diameter moves the whole speed line."""
    ts = getattr(sim, "turbo", None)
    if ts is None:
        return None
    eng = sim.engine
    ball = bool(getattr(eng, "turbo_ball_bearing", False))
    units = []
    for u in ts.units:
        units.append({
            "d2": float(u.comp.d2),
            "a_t": float(u.turb.a_eff),
            "a_wg": float(u.a_wg),
            "ball": ball,
            # derived -- the Swift build recomputes them and its tests check
            "j": float(u.j),
            "c_fric": float(u.c_fric),
        })
    return {
        "units": units,
        "layout": ts.layout,
        "v_p": float(ts.air.v_p),
        "bov_cda": float(ts.air.bov_cda),
        "bov_mode": sim.bov_mode_default(),
        "boost_bar": float(ts.boost_bar),
        "twin_scroll": bool(ts.twin_scroll),
        "seq_rpm": [float(ts.seq_rpm[0]), float(ts.seq_rpm[1])],
        "diesel": bool(ts.diesel),
        "external_wg": bool(ts.external_wg),
        "anti_lag": bool(ts.anti_lag),
        "e_turbo": bool(ts.e_turbo),
        "sc_pr": float(ts.sc_pr),
        "sc_rpm": float(ts.sc_rpm),
        "rpm_red": float(ts.rpm_red),
        "omega_max": float(ts.omega_max),
        "dp_down_k": float(ts.dp_down_k),
        "rpm_full": float(ts.rpm_full),
        "pit_rated": float(ts.pit_rated),
        "w_air_rated": float(ts.w_air_rated),
        "size": float(ts.size),
        # what the engine-side hooks need that the preset JSON does not carry
        "mguh_kw": float(sim._mgu_h_kw),
        "intercooler_eff": float(getattr(eng, "intercooler_eff", 0.7)),
        "vd": float(sim._vd),
        "map_wot_area": float(sim._map_wot_area),
    }


def main():
    path = os.path.join(DOCS, "engine_tables.json")
    with open(path, encoding="utf-8") as f:
        tables = json.load(f)
    n = 0
    for key in sorted(tables):
        eng = presets.ALL[key]()
        if eng.induction != "turbo" or eng.boost_bar <= 0.0:
            tables[key].pop("turbo", None)
            continue
        spec = None
        for attempt in range(3):          # CPython 3.14 here crashes now and then
            try:
                spec = spec_for(Simulator(eng))
                break
            except Exception as e:        # noqa: BLE001
                print("  retry", key, e)
        if spec is None:
            tables[key].pop("turbo", None)
            print("NO MACHINE", key)
            continue
        tables[key]["turbo"] = spec
        n += 1
        print(key, spec["layout"], len(spec["units"]),
              "d2 %.1f mm" % (spec["units"][0]["d2"] * 1e3))
    with open(path, "w", encoding="ascii") as f:
        json.dump(tables, f)              # the format export_engine_tables writes
    print("%d turbo machines -> %s" % (n, path))


if __name__ == "__main__":
    _ = turbo_mod                         # the machine lives there
    main()
