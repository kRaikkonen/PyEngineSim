"""Measure the PYTHON chain's realtime factor, no-scipy path.

No scipy on purpose: that is the path the phone runs, so this is the honest
thing to put next to the Swift number.  Same machine, same cars, same
operating point, same block size.
"""
import sys
import time

sys.path.insert(0, ".")

from engine_sim import presets                                    # noqa: E402
from engine_sim.audio import Synthesizer                          # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402
from engine_sim.units import rpm_to_rads                          # noqa: E402
from engine_sim import audio as A                                 # noqa: E402

RATE, BLOCK = 32000, 512
CARS = ["a3", "aven", "veyron", "f2007", "6", "917", "clkgtr", "speed12",
        "rs3", "8"]


def factor(key):
    sim = Simulator(presets.ALL[key]())
    sim.ignition_on = True
    syn = Synthesizer(sim, sample_rate=RATE, seed=1)
    syn.enabled = False
    syn.pov = "cockpit"
    eng = sim.engine
    sim.omega = rpm_to_rads(eng.redline_rpm * 0.9)
    sim.throttle = 1.0
    for _ in range(40):
        sim._update_boost(1.0 / 200.0)
    for _ in range(8):                       # warm the filter cache
        syn._render_block(BLOCK)
    n = 24
    t0 = time.perf_counter()
    for _ in range(n):
        syn._render_block(BLOCK)
    cpu = time.perf_counter() - t0
    return cpu / (n * BLOCK / float(RATE))


def main():
    print("python chain, no scipy (the phone's path), %d Hz / block %d"
          % (RATE, BLOCK))
    print("  native scipy present: %r" % A._NATIVE_SCIPY)
    worst, worst_car, total, n = 0.0, "", 0.0, 0
    for k in CARS:
        if k not in presets.ALL:
            continue
        f = factor(k)
        total += f
        n += 1
        if f > worst:
            worst, worst_car = f, k
        print("    %-10s %.3f  (%3.0f%% of one core)" % (k, f, f * 100))
    print("  worst %.3f (%s), mean %.3f" % (worst, worst_car, total / n))


main()
