"""
Headless sanity test for the simulation core (no window, no audio).

Runs the engine through a realistic sequence — crank it with the starter, let it
idle, then floor the throttle — and prints the resulting rpm.  Also sweeps the
throttle-locked engine across its rev range to print a torque/power curve.

Run with:  py test_headless.py
"""

import numpy as np

from engine_sim import Simulator, presets
from engine_sim.units import nm_to_lbft, nm_to_hp_at


def run_startup(eng):
    sim = Simulator(eng)
    sim.ignition_on = True
    sim.throttle = 0.0

    dt = 1.0 / 60.0
    print(f"\n=== {eng.name}: start-up sequence ===")

    # 1) Crank with the starter for 1.0 s
    sim.starter_engaged = True
    for _ in range(60):
        sim.step(dt)
    print(f"after 1.0s cranking:        {sim.rpm:7.0f} rpm")

    # 2) Release starter, let it idle for 2 s
    sim.starter_engaged = False
    for _ in range(120):
        sim.step(dt)
    idle = sim.rpm
    print(f"idle (2s after start):      {idle:7.0f} rpm")

    # 3) Floor it for 1.5 s
    sim.throttle = 1.0
    peak = 0.0
    for _ in range(90):
        sim.step(dt)
        peak = max(peak, sim.rpm)
    print(f"wide-open throttle (1.5s):  {sim.rpm:7.0f} rpm  (peak {peak:.0f})")

    # 4) Lift off for 1.5 s — should fall back toward idle
    sim.throttle = 0.0
    for _ in range(90):
        sim.step(dt)
    print(f"after lift-off (1.5s):      {sim.rpm:7.0f} rpm")

    assert 400 < idle < 2500, f"idle rpm {idle:.0f} out of sane range"
    assert peak > idle * 2, "throttle did not rev the engine up"
    print("  [ok] cranks, idles, revs and settles")


def run_torque_curve(eng):
    """Hold rpm fixed (open throttle) and average torque over whole cycles."""
    print(f"\n=== {eng.name}: WOT torque curve ===")
    print("  rpm    torque(Nm)  torque(lb-ft)   power(hp)")
    best_tq = (0, 0.0)
    best_hp = (0, 0.0)
    for rpm in range(1000, int(eng.redline_rpm) + 1, 1000):
        sim = Simulator(eng)
        sim.ignition_on = True
        sim.throttle = 1.0
        sim.omega = rpm * 2 * np.pi / 60.0
        sim.starter_engaged = False

        # Spin through several full cycles, averaging gas torque only.
        samples = []
        dt = 1.0 / 20000.0
        for _ in range(4000):
            # Freeze the speed so we measure torque at this rpm.
            w = sim.omega
            sim.step(dt)
            sim.omega = w
            samples.append(sim.gas_torque - sim.friction_torque)
        tq = float(np.mean(samples))
        hp = nm_to_hp_at(tq, rpm)
        if tq > best_tq[1]:
            best_tq = (rpm, tq)
        if hp > best_hp[1]:
            best_hp = (rpm, hp)
        print(f"  {rpm:5d}   {tq:8.1f}     {nm_to_lbft(tq):8.1f}      {hp:8.1f}")
    print(f"  peak torque ~{best_tq[1]:.0f} Nm @ {best_tq[0]} rpm,"
          f"  peak power ~{best_hp[1]:.0f} hp @ {best_hp[0]} rpm")


if __name__ == "__main__":
    for factory in (presets.porsche_911_h6, presets.vw_ea888_i4, presets.ford_coyote_v8):
        eng = factory()
        run_startup(eng)
        run_torque_curve(eng)
    print("\nAll headless checks passed.")
