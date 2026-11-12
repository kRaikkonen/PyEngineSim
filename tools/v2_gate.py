"""v2 against a reference, by numbers, before anyone is asked to listen.

    python tools/v2_gate.py 6                 # v2 vs the classic chain
    python tools/v2_gate.py 6 --points 0.5:1.0,0.9:1.0

At each operating point (fraction of the redline : throttle) both voices play
two seconds at a held speed; printed are the engine orders (dB relative to
the strongest) and the band shares (dB of the total), and the difference.
The tolerances of ENGINESIM_PLAN s.5.3 are +-3 dB on the main orders and
+-6 points on the band shares; this prints how far off v2 is.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine_sim import Simulator, core_voice, presets          # noqa: E402
from engine_sim.audio import BLOCK, Synthesizer                # noqa: E402
from engine_sim.units import rpm_to_rads                       # noqa: E402

EDGES = [20, 100, 200, 400, 800, 1600, 3200, 6400, 20000]
ORDERS = [0.5, 1, 1.5, 2, 2.5, 3, 4, 4.5, 5, 6, 7.5, 9, 12, 18, 24]


def play(key, voice, frac, thr, seconds=2.0):
    eng = presets.ALL[key]()
    sim = Simulator(eng)
    sim.ignition_on = True
    synth = Synthesizer(sim)
    rate = synth.sample_rate
    rpm = frac * eng.redline_rpm
    sim.omega = rpm_to_rads(rpm)
    sim.throttle = thr
    for _ in range(40):
        sim._update_boost(0.1)
    if voice == "v2":
        synth.v2 = core_voice.CoreVoice(sim, key, rate)
        synth.v2_on = True
    out = []
    for i in range(int((seconds + 1.0) * rate / BLOCK)):
        sim._map_memo = None
        out.append(np.array(synth._render_block(BLOCK), dtype=np.float64))
    x = np.concatenate(out)[int(rate):]
    return x, rate, rpm


def measure(x, rate, rpm):
    n = len(x)
    X = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
    f = np.fft.rfftfreq(n, 1.0 / rate)
    tot = X[f > 20].sum() + 1e-30
    bands = [10 * math.log10(X[(f >= a) & (f < b)].sum() / tot + 1e-12)
             for a, b in zip(EDGES, EDGES[1:])]
    f0 = rpm / 60.0
    orders = [10 * math.log10(X[(f > (k - 0.12) * f0) & (f < (k + 0.12) * f0)].sum() / tot + 1e-12)
              for k in ORDERS]
    cen = float((f[f > 20] * X[f > 20]).sum() / tot)
    return bands, orders, cen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--points", default="0.3:0.25,0.5:1.0,0.75:1.0,0.92:1.0")
    a = ap.parse_args()
    worst_b = worst_o = 0.0
    for pt in a.points.split(","):
        frac, thr = (float(v) for v in pt.split(":"))
        res = {}
        for voice in ("classic", "v2"):
            x, rate, rpm = play(a.key, voice, frac, thr)
            res[voice] = measure(x, rate, rpm)
        print("\n%s  %.0f rpm, throttle %.2f" % (a.key, rpm, thr))
        print("  bands to  " + " ".join("%5d" % e for e in EDGES[1:]) + "   centroid")
        for v in ("classic", "v2"):
            print("  %-8s  " % v + " ".join("%5.0f" % b for b in res[v][0]) + "   %5.0f Hz" % res[v][2])
        print("  orders    " + " ".join("%5s" % ("%g" % k) for k in ORDERS))
        for v in ("classic", "v2"):
            print("  %-8s  " % v + " ".join("%5.0f" % o for o in res[v][1]))
        # bands that matter: those within 20 dB of the total in the reference
        db = [abs(b2 - b1) for b1, b2 in zip(res["classic"][0], res["v2"][0]) if b1 > -20]
        do = [abs(o2 - o1) for o1, o2 in zip(res["classic"][1], res["v2"][1]) if o1 > -15]
        print("  off by: bands %.0f dB worst, main orders %.0f dB worst" % (max(db), max(do or [0])))
        worst_b, worst_o = max(worst_b, max(db)), max(worst_o, max(do or [0]))
    print("\n%s: worst band %.0f dB, worst main order %.0f dB" % (a.key, worst_b, worst_o))


if __name__ == "__main__":
    main()
