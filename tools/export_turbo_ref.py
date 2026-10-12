"""Reference runs of the white-box turbo machine for the Swift tests.

    py tools/export_turbo_ref.py
      -> swift/EngineSimCore/Tests/EngineSimCoreTests/Fixtures/turbo.json

For each car: its built machine (export_turbo.spec_for) and a 5 s drive --
idle, a wide-open pull through the sequential hand-over, a LIFT (the blow-off
valve, or surge), part throttle, the rev limiter's fuel cut -- stepped at
10 ms as the synthesizer steps it, the state after every frame.  Then the
audio-rate twin through the lift, and the car-mode observer (follow).

The engine side (air flow, turbine-inlet temperature, mixture) is the
simulator's own, with ONE change: its per-25-rpm memo of the plate's
fraction is switched off for the run, so the Python is an exact function of
its inputs -- the memo keeps whichever rpm in a bucket it saw first, which
the phone could only reproduce by replaying the Python's whole call history.
Swift computes the same functions live (TurboHooks) and must land on them.
"""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim import turbo as T                                 # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402
from export_turbo import spec_for                                 # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "swift", "EngineSimCore", "Tests", "EngineSimCoreTests",
                   "Fixtures", "turbo.json")

CARS = ["a3", "rs3", "2jzst", "veyron", "rx7", "9", "deltas4", "sf25", "s1",
        "2", "f450", "f40"]
DT = 0.01
N = 500
TWIN_CARS = ["rs3", "2jzst", "f40"]
TWIN_FROM, TWIN_TO = 255, 330            # the lift (frames)
TWIN_SUB = 32


class _NoMemo(dict):
    """A memo that never keeps anything: the hooks become exact functions."""

    def __setitem__(self, k, v):
        pass


def drive(red, k):
    """(rpm, pedal, fuel_cut) at frame k."""
    t = k * DT
    if t < 0.4:
        return 0.30 * red, 0.0, False
    if t < 2.6:                                   # the pull
        x = (t - 0.4) / 2.2
        return (0.30 + 0.62 * x) * red, 1.0, False
    if t < 3.8:                                   # the lift
        x = (t - 2.6) / 1.2
        return (0.92 - 0.30 * x) * red, 0.0, False
    if t < 4.6:                                   # part throttle
        return 0.62 * red, 0.4, False
    # on the limiter: the cut every third frame
    return 1.0 * red, 1.0, (k % 3 == 0)


def mguh_power(sim, eng, ts, thr):
    """_update_boost's MGU-H (its store assumed charged)."""
    e_spun = eng.electric_turbo or eng.mgu_h
    if not e_spun:
        return 0.0
    p_max = 1000.0 * (sim._mgu_h_kw or 0.4 * max(eng.hybrid_kw, 50.0))
    tgt = T.P_ATM + eng.boost_bar * 1.0e5 * thr
    err = (tgt - ts.air.p_p) / 0.3e5
    return p_max * min(max(err, -0.5), 1.0)


def state(ts):
    s = [ts.boost, ts.air.p_p, ts.air.x_bov, ts.air.w_bov, ts.t03, ts.w_ex,
         ts.k_thr, ts.map_pa, ts.t_p, ts.p04, ts.w_bypass]
    for u in ts.units:
        s += [u.omega, u.w_c, u.x_wg, u.wg_int, u.p03, u.p_t, u.p_c,
              u.ecv, 1.0 if u.active else 0.0]
    return s


def run_car(key):
    eng = presets.ALL[key]()
    sim = Simulator(eng)
    ts = sim.turbo
    assert ts is not None, key
    spec = spec_for(sim)
    sim._frac_cache = _NoMemo()
    red = eng.redline_rpm
    frames, out = [], []
    twin_rec = []
    tw = None
    for k in range(N):
        rpm, thr, cut = drive(red, k)
        mg = mguh_power(sim, eng, ts, thr)
        ts.step(DT, rpm, thr, sim._turbo_flow, sim._turbo_t03, fuel_cut=cut,
                mguh_w=mg)
        frames.append([rpm, thr, 1 if cut else 0, mg])
        out.append(state(ts))
        if key in TWIN_CARS and TWIN_FROM <= k < TWIN_TO:
            if tw is None:
                tw = T.Twin(ts)
            rec = tw.run(DT, TWIN_SUB)
            twin_rec.append({"busy": tw.busy,
                             "w_c": [[r[0][j] for r in rec]
                                     for j in range(len(ts.units))],
                             "p_p": [r[1] for r in rec],
                             "w_bov": [r[2] for r in rec]})
    # the observer: car mode's real boost, a ramp up and back
    fol, fol_out = [], []
    for k in range(120):
        rpm = (0.35 + 0.5 * k / 120.0) * red
        b = ts.boost_bar * math.sin(math.pi * k / 120.0) - 0.2
        thr = 1.0 if b > 0.0 else 0.2
        ts.follow(b, rpm, thr, DT, sim._turbo_flow)
        fol.append([rpm, thr, b])
        fol_out.append([u.omega for u in ts.units] + [ts.air.p_p, ts.k_thr,
                                                       ts.map_pa])
    # the engine-side functions, sampled where the drive never goes
    hooks = []
    for rf in (0.2, 0.45, 0.7, 0.95):
        for thr in (0.0, 0.3, 1.0):
            for pr in (0.8, 1.0, 1.6):
                rpm = rf * red
                p_p = pr * T.P_ATM
                w, m, tm = sim._turbo_flow(rpm, thr, p_p)
                load = min(m / T.P_ATM, 1.0)
                hooks.append([rpm, thr, p_p, w, m, tm,
                              sim._turbo_t03(rpm, load, pr),
                              sim.afr(load, rpm)])
    return {
        "spec": spec,
        "coolant_c": sim.coolant_c,
        "ic_soak": sim.ic_soak,
        "dt": DT,
        "frames": frames,
        "state": out,
        "twin_sub": TWIN_SUB,
        "twin_from": TWIN_FROM if key in TWIN_CARS else -1,
        "twin": twin_rec,
        "follow": fol,
        "follow_state": fol_out,
        "hooks": hooks,
    }


def main():
    res = {}
    for key in CARS:
        for attempt in range(3):          # CPython 3.14 here crashes now and then
            try:
                res[key] = run_car(key)
                break
            except Exception as e:        # noqa: BLE001
                print("  retry", key, e)
        r = res[key]
        bmax = max(s[0] for s in r["state"])
        bmin = min(s[0] for s in r["state"])
        print("%-8s boost %.2f..%.2f bar  shaft %.0f krpm" % (
            key, bmin, bmax,
            max(s[11] for s in r["state"]) * 60 / (2 * math.pi) / 1e3))
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(res, fh)
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
