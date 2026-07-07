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

from engine_sim import presets                      # noqa: E402
from engine_sim.ve_model import build_ve_table      # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*", help="preset keys (see engine_sim.presets)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true", help="validate only, no files")
    ap.add_argument("--outdir", default=os.path.join("docs", "tables"))
    args = ap.parse_args()

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
