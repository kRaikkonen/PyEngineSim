"""
Offline sweep / bake tool for the white-box surrogate tables.

    py tools/sweep.py aven r34            # bake + export named presets
    py tools/sweep.py --all               # all 130
    py tools/sweep.py --all --check       # bake-and-validate only, no files

Writes per-car VE tables to docs/tables/ as .npz (runtime-loadable), .csv
(spreadsheet-editable) and .json.  This is the same baking path the Simulator
runs at engine load — exporting just makes the physics INSPECTABLE/EDITABLE.
When the Phase-3 real gas-dynamics solver replaces ve_model as truth, this
tool becomes the (slow, one-off) offline baking entry point.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                  # noqa: E402

from engine_sim import presets                      # noqa: E402
from engine_sim.ve_model import build_ve_table      # noqa: E402
from engine_sim.surrogate import LUT                # noqa: E402


def bake_truth_tables(eng, n_rpm=9, n_thr=5, dphi=2.5):
    """Sweep the CLOSED-LOOP gas-dynamics truth model (gas_truth.py — the
    AngeTheGreat physics port) and bake torque/VE/blowdown LUTs.  This is the
    v0.3 pipeline: offline white-box truth -> runtime surrogate tables."""
    from engine_sim.gas_truth import measure_operating_point
    rpm_grid = np.linspace(max(eng.idle_rpm * 0.8, 600.0),
                           eng.redline_rpm * 1.02, n_rpm)
    thr_grid = np.linspace(0.1, 1.0, n_thr)
    tq = np.empty((n_rpm, n_thr))
    ve = np.empty_like(tq)
    bd = np.empty_like(tq)
    for i, r in enumerate(rpm_grid):
        for j, t in enumerate(thr_grid):
            m = measure_operating_point(eng, float(r), float(t),
                                        dphi=dphi, max_cycles=8)
            tq[i, j] = m["torque"]
            ve[i, j] = m["ve"]
            bd[i, j] = m["blowdown_p"]
    axes = [("rpm", rpm_grid), ("thr", thr_grid)]
    return {"torque": LUT(axes, tq), "ve": LUT(axes, ve),
            "blowdown": LUT(axes, bd)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*", help="preset keys (see engine_sim.presets)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true", help="validate only, no files")
    ap.add_argument("--truth", action="store_true",
                    help="bake from the CLOSED-LOOP gas-dynamics truth model "
                         "(slow, offline) instead of the analytic VE model")
    ap.add_argument("--outdir", default=os.path.join("docs", "tables"))
    args = ap.parse_args()

    if args.truth:
        keys = sorted(presets.ALL) if args.all else args.keys
        if not keys:
            ap.error("give preset keys or --all")
        if not args.check:
            os.makedirs(args.outdir, exist_ok=True)
        import time
        for key in keys:
            eng = presets.ALL[key]()
            t0 = time.perf_counter()
            tabs = bake_truth_tables(eng)
            tq = tabs["torque"].values
            i, j = divmod(int(tq.argmax()), tq.shape[1])
            print(f"{key:10s} truth: peak torque {tq.max():5.0f} Nm "
                  f"@ {tabs['torque'].grids[0][i]:5.0f} rpm  "
                  f"idle-ish VE {tabs['ve'].eval2(eng.idle_rpm, 0.2):.2f}  "
                  f"[{time.perf_counter()-t0:.1f} s]")
            if not args.check:
                for name, lut in tabs.items():
                    base = os.path.join(args.outdir, f"{key}_truth_{name}")
                    lut.save(base + ".npz")
                    lut.export_csv(base + ".csv")
        return 0

    keys = sorted(presets.ALL) if args.all else args.keys
    if not keys:
        ap.error("give preset keys or --all")
    if not args.check:
        os.makedirs(args.outdir, exist_ok=True)

    bad = 0
    for key in keys:
        try:
            eng = presets.ALL[key]()
            lut = build_ve_table(eng)
            v = lut.values
            rpm_g, map_g = lut.grids
            # sanity: bounded, peak anchored near ve_max, idle VE workable
            peak = float(v.max())
            idle_ve = lut.eval2(eng.idle_rpm, 0.30)
            ok = (0.05 < float(v.min()) and peak < 1.45
                  and abs(peak - eng.ve_max) < 0.15 * eng.ve_max + 1e-6
                  and 0.30 < idle_ve < 1.0)
            ir = rpm_g[int(v[:, -1].argmax())]
            print(f"{'OK ' if ok else 'BAD'} {key:10s} peak={peak:.2f} "
                  f"(ve_max {eng.ve_max:.2f})  idleVE={idle_ve:.2f}  "
                  f"hump@{ir:5.0f}rpm  range[{v.min():.2f},{v.max():.2f}]")
            if not ok:
                bad += 1
            if not args.check:
                base = os.path.join(args.outdir, f"{key}_ve")
                lut.save(base + ".npz")
                lut.export_csv(base + ".csv")
                lut.export_json(base + ".json")
        except Exception as e:
            bad += 1
            print(f"ERR {key}: {type(e).__name__}: {e}")
    print(f"\n{len(keys)} presets, {bad} problems")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
