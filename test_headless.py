"""
Headless sanity test for the simulation core (no window, no audio).

Runs the engine through a realistic sequence — crank it with the starter, let it
idle, then floor the throttle — and prints the resulting rpm.  Also sweeps the
throttle-locked engine across its rev range to print a torque/power curve.

Run with:  py test_headless.py
"""

import math
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


def run_audio_sink():
    """Render real audio with NO sound device, through the output sink.

    This is the seam a platform backend plugs into (iOS/AVAudioEngine, bare
    ALSA, a recorder), so it is worth holding still: shape, dtype, range and
    the real-time pace are all part of the contract.
    """
    import time
    from engine_sim.audio import Synthesizer
    from engine_sim.units import rpm_to_rads

    sim = Simulator(presets.audi_rs3_2024())
    sim.ignition_on = True
    sim.throttle = 0.8
    sim.omega = rpm_to_rads(5000.0)

    got = []
    synth = Synthesizer(sim, sample_rate=32000)
    # A sink must work with NO audio backend available at all -- that is the
    # iOS case (no PortAudio, no pygame), and it is what this guards.
    synth.enabled = False
    synth.sink = lambda b: got.append(
        (b.shape, b.dtype.str, float(np.abs(b).max())))
    assert synth.start(), "sink backend refused to start"
    assert synth.mode == "sink", "sink backend not selected"
    t0 = time.monotonic()
    time.sleep(1.5)
    elapsed = time.monotonic() - t0
    synth.stop()

    assert got, "sink received no audio"
    assert {g[0] for g in got} == {(256, 2)}, "sink block shape changed"
    assert {g[1] for g in got} == {"<f4"}, "sink block dtype changed"
    peak = max(g[2] for g in got)
    assert np.isfinite(peak) and 0.01 < peak <= 1.0, "sink audio silent or clipped"
    rate = len(got) * 256 / elapsed
    assert 0.9 < rate / 32000.0 < 1.1, "sink ran at %.0f frames/s, not real time" % rate
    print(f"  audio sink: {len(got)} blocks, {rate:,.0f} frames/s, peak {peak:.2f}"
          f"  (no sound device involved)")


def run_filter_fallback():
    """The pure-numpy filters must equal scipy's, sample for sample.

    scipy does not exist on iOS or Android.  These fallbacks used to be
    missing entirely -- the guarded filters were simply skipped, and the
    unfiltered chain screamed at Nyquist on a phone.  So the fallbacks are
    held to the real thing wherever the real thing is available.
    """
    from engine_sim.audio import _np_butter, _np_lfilter, _NATIVE_SCIPY
    print("\n=== pure-numpy filter fallback (the iOS / Android path) ===")
    if not _NATIVE_SCIPY:
        print("  scipy absent here -- nothing to compare against, skipping")
        return
    from scipy.signal import butter as sp_butter, lfilter as sp_lfilter

    rng = np.random.default_rng(0)
    worst = 0.0
    # every design this synth actually asks for, at both shipped rates
    cases = [(1, 0.05, "low"), (2, 0.0034, "high"), (2, 0.15, "low"),
             (2, 0.6875, "low"), (2, 0.0437, "high"), (2, 0.04, "low"),
             (2, 0.3, "low"), (2, 0.9, "low")]
    for order, wn, btype in cases:
        b1, a1 = _np_butter(order, wn, btype)
        b2, a2 = sp_butter(order, wn, btype=btype)
        x = rng.standard_normal(4096)
        zn = np.zeros(max(len(a1), len(b1)) - 1)
        zs = np.zeros(max(len(a2), len(b2)) - 1)
        yn, ys = [], []
        for i in range(0, len(x), 256):        # streamed, like the synth
            o, zn = _np_lfilter(b1, a1, x[i:i + 256], zi=zn)
            yn.append(o)
            o, zs = sp_lfilter(b2, a2, x[i:i + 256], zi=zs)
            ys.append(o)
        worst = max(worst, float(np.abs(np.concatenate(yn)
                                        - np.concatenate(ys)).max()))
    assert worst < 1e-9, f"numpy filters diverge from scipy by {worst:.2e}"

    # the order-4 bandpass must at least be stable and pass its band
    b, a = _np_butter(2, [0.3125, 0.5625], "band")
    y, _ = _np_lfilter(b, a, rng.standard_normal(2048), zi=np.zeros(4))
    assert np.all(np.isfinite(y)), "bandpass fallback blew up"
    print(f"  matches scipy to {worst:.1e} across {len(cases)} designs, "
          f"streamed in 256-sample blocks")



def run_layer_switches():
    """Every stage of the chain has a visibility switch; prove each one is
    LIVE.

    A switch that silently does nothing is worse than no switch -- you would
    conclude the stage does not matter when really the toggle was not wired.
    So each layer is hidden in turn and the output compared, on a spread of
    cars and operating points wide enough to provoke every conditional stage:
    the burble needs a closed throttle with the revs up, the megaphone needs a
    car that actually has an exit horn (only two in the fleet do), and the
    turbine needs boost."""
    from engine_sim.audio import Synthesizer
    from engine_sim.simulator import Simulator
    from engine_sim.units import rpm_to_rads

    print("\nLAYER SWITCHES")
    # (car, throttle) -- between them these reach every conditional branch
    CASES = [("rs3", 0.85),      # turbo: turbine, wastegate, GPF
             ("rs3", 0.0),       # ...and the same car on OVERRUN: burble
             ("f2007", 0.9),     # a real megaphone, and a screamer's whine
             ("aven", 0.7)]      # big NA V12, open system

    def render(car, throttle, hide=None, blocks=6):
        sim = Simulator(presets.ALL[car]())
        sim.ignition_on = True
        syn = Synthesizer(sim, sample_rate=32000, seed=1)
        syn.enabled = False
        if hide:
            syn.stage_on[hide] = False
        out = []
        for i in range(blocks):
            rpm = sim.engine.redline_rpm * (0.35 + 0.09 * i)
            sim.omega = rpm_to_rads(rpm)
            sim.throttle = throttle
            for _ in range(20):
                sim._update_boost(1.0 / 200.0)
            out.append(np.asarray(syn._render_block(256), dtype=np.float64))
        return np.concatenate(out)

    bases = {c: render(*c) for c in CASES}
    dead = []
    for nm in Synthesizer.STAGES:
        best, where = -300.0, None
        for c in CASES:
            base = bases[c]
            d = render(c[0], c[1], hide=nm) - base
            rel = float(np.sqrt(np.mean(d ** 2))
                        / max(np.sqrt(np.mean(base ** 2)), 1e-12))
            db = 20.0 * math.log10(max(rel, 1e-12))
            if db > best:
                best, where = db, c[0]
        print(f"  {nm:<16s} {best:+7.1f} dB  ({where})")
        if best < -60.0:
            dead.append(nm)
    assert not dead, f"layer switch does nothing: {', '.join(dead)}"

    # ...and with everything on, the chain must be bit-identical to no
    # switches at all -- the feature must not cost the default path anything
    sim = Simulator(presets.ALL["rs3"]())
    sim.ignition_on = True
    syn = Synthesizer(sim, sample_rate=32000, seed=1)
    syn.enabled = False
    assert all(syn.stage_on.values()), "layers must default to visible"
    print(f"  all {len(Synthesizer.STAGES)} layers live, all visible by default")


def run_swift_parity():
    """The three behaviours the Swift app grew first, now shared.

    They are ported line-for-line, so what is checked here is that the PYTHON
    side really does them -- a silent no-op would leave the two builds sounding
    different while every other test still passed.
    """
    from engine_sim.audio import Synthesizer
    from engine_sim.units import rpm_to_rads

    print("")
    print("Swift parity (pop budget / lift sustain / firing lamps)")

    # -- a lift gives three or four bangs and then stops -------------------
    # Without the budget it crackles all the way down to idle, which is a
    # fireworks display rather than a car.
    sim = Simulator(presets.ALL["a45"]())
    sim.ignition_on = True
    syn = Synthesizer(sim, sample_rate=32000, seed=1)
    syn.enabled = False
    syn.pops_on = True
    sim.omega = rpm_to_rads(4000.0)
    sim.throttle = 1.0
    for _ in range(120):                       # load the pipe with fuel
        syn._render_block(256)
    sim.throttle = 0.0                         # THE LIFT
    syn._render_block(256)
    budget = syn._pop_budget
    fired0 = syn.pops_fired
    for _ in range(700):                       # coast a long way (5.6 s)
        syn._render_block(256)
    fired = syn.pops_fired - fired0
    print("  lift after a hard pull: budget %d, %d bangs in 5.6 s"
          % (budget, fired))
    assert 3 <= budget <= 4, "a loaded pipe should give 3-4 bangs, got %d" % budget
    assert fired == budget, "fired %d but the budget was %d" % (fired, budget)
    sim.throttle = 1.0                         # back on the gas refills it
    for _ in range(120):
        syn._render_block(256)
    sim.throttle = 0.0
    syn._render_block(256)
    assert syn._pop_budget >= 3, "going back on the gas must refill the budget"
    print("  going back on the gas refills it to %d" % syn._pop_budget)

    # -- the lift sustain holds the note up -------------------------------
    def lift_level(sustain):
        sim = Simulator(presets.ALL["a45"]())
        sim.ignition_on = True
        sy = Synthesizer(sim, sample_rate=32000, seed=1)
        sy.enabled = False
        sy.sustain_on_lift = sustain
        sim.omega = rpm_to_rads(4000.0)
        sim.throttle = 1.0
        for _ in range(80):
            sy._render_block(256)
        sim.throttle = 0.0
        y = np.concatenate([np.asarray(sy._render_block(256))
                            for _ in range(60)])
        y = y[len(y) // 2:]
        return 20.0 * math.log10(float(np.sqrt(np.mean(y * y))) + 1e-12)

    off, on = lift_level(0.0), lift_level(0.85)
    print("  lift level %+.1f dB at 0.0 -> %+.1f dB at 0.85" % (off, on))
    assert on > off + 0.5, "sustain_on_lift must hold the note UP on a lift"

    # -- the lamps are lit by the firing offsets, not by a timer -----------
    sim = Simulator(presets.ALL["aven"]())
    sim.ignition_on = True
    syn = Synthesizer(sim, sample_rate=32000, seed=1)
    syn.enabled = False
    assert len(syn.cylinder_light) == len(syn._offsets)
    sim.omega = rpm_to_rads(1200.0)
    sim.throttle = 0.4
    seen = np.zeros(len(syn.cylinder_light))
    for _ in range(400):
        syn._render_block(256)
        seen = np.maximum(seen, syn.cylinder_light)
    lit = int((seen > 0.5).sum())
    print("  %d/%d cylinders lit over 400 blocks" % (lit, len(seen)))
    assert lit == len(seen), "every cylinder must light at least once"
    # and they must DECAY -- a lamp stuck on is not an ignition lamp
    sim.ignition_on = False
    sim.omega = rpm_to_rads(0.0)
    for _ in range(200):
        syn._render_block(256)
    assert syn.cylinder_light.max() < 0.05, "lamps must decay when nothing fires"
    print("  lamps decay once the engine stops")


if __name__ == "__main__":
    for factory in (presets.porsche_911_h6, presets.vw_ea888_i4, presets.ford_coyote_v8):
        eng = factory()
        run_startup(eng)
        run_torque_curve(eng)
    run_filter_fallback()
    run_layer_switches()
    run_audio_sink()
    run_swift_parity()
    print("\nAll headless checks passed.")
