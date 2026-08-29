"""Golden reference for a reimplementation of the audio chain.

A Swift (or C, or anything) port of `audio.py` is not a writing problem, it is
a REPRODUCTION problem: the value in that file is 82 commits of one person's
ear, and a rewrite that merely "sounds like an engine" has thrown all of it
away.  Re-tuning by ear a second time is not a plan.

So before any port: freeze what the Python renders, exactly, and make the port
provable against it -- stage by stage, not just at the output.  The synth is
bit-exact reproducible under a fixed seed, and it already taps 19 named points
along the chain (block -> header -> head/port -> catalytic -> ... -> EQ ->
cabin/room -> output).  That turns "does it sound right" into "does stage 7
match", which is a question with an answer.

    py tools/golden.py make                 # write the reference
    py tools/golden.py check                # re-render and prove it is stable
    py tools/golden.py compare other.npz    # grade a candidate

Grading is per stage, per band, in dB -- because that is what an ear reacts
to.  A port is done when every stage is inside tolerance; where it is not, the
first failing stage tells you exactly which one to fix.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.audio import Synthesizer                          # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402
from engine_sim.units import rpm_to_rads                          # noqa: E402

REF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "golden_reference.npz")
SEED = 20260829
RATE = 32000
BLOCK = 256
BANDS = ((30, 150), (150, 300), (300, 600), (600, 1200), (1200, 2400),
         (2400, 5000), (5000, 9000), (9000, 16000))

# Each case is a rev/throttle trajectory, not a single point: the interesting
# behaviour (spool, the whine coming in, overrun) only happens when things move.
CASES = (
    # key,    pov,        seconds, rpm from -> to, throttle from -> to
    ("rs3", "chase", 2.0, (900, 6800), (0.15, 1.0)),      # turbo I5, pull
    ("aven", "chase", 2.0, (1000, 8200), (0.2, 1.0)),     # NA V12, pull
    ("8", "chase", 2.0, (800, 5800), (0.1, 1.0)),         # supercharged V8
    ("rs3", "cockpit", 1.5, (3000, 3000), (1.0, 0.0)),    # steady, then lift
    ("aven", "trackside", 1.5, (7000, 3000), (1.0, 0.0)),  # overrun
    ("787b", "chase", 1.5, (3000, 8500), (0.5, 1.0)),     # rotary
)


def render(key, pov, seconds, rpm_range, thr_range, rate=RATE, block=BLOCK):
    """Deterministic render: no wall clock, no audio device, fixed seed.

    Time advances by exactly one block per block, so the same call always
    produces the same samples -- that is the whole point.
    """
    sim = Simulator(presets.ALL[key]())
    sim.ignition_on = True
    synth = Synthesizer(sim, sample_rate=rate, seed=SEED)
    synth.enabled = False          # never touch a device
    synth.pov = pov
    synth.capture_stages = True

    n_blocks = int(seconds * rate / block)
    dt = block / float(rate)
    r0, r1 = rpm_range
    t0, t1 = thr_range
    out = []
    for i in range(n_blocks):
        u = i / max(n_blocks - 1, 1)
        sim.omega = rpm_to_rads(r0 + (r1 - r0) * u)
        sim.throttle = t0 + (t1 - t0) * u
        if sim.engine.induction != "na":
            sim._update_boost(dt)
        out.append(np.asarray(synth._render_block(block), dtype=np.float64))
        sim.crank_angle += sim.omega * dt
    stages = {k: np.concatenate(v) for k, v in synth._stage_full.items()}
    stages["OUTPUT"] = np.concatenate(out)
    return stages


def case_name(case):
    key, pov, secs, rpm, thr = case
    return "%s|%s|%.0f-%.0f|%.2f-%.2f" % (key, pov, rpm[0], rpm[1],
                                          thr[0], thr[1])


def band_db(x, rate=RATE):
    """Energy per band in dB -- the shape an ear actually reacts to."""
    if len(x) < 1024:
        return np.full(len(BANDS), -120.0)
    n = 1 << int(np.log2(len(x)))
    X = np.abs(np.fft.rfft(x[:n] * np.hanning(n))) ** 2
    f = np.fft.rfftfreq(n, 1.0 / rate)
    return np.array([10.0 * np.log10(max(X[(f >= lo) & (f < hi)].sum(), 1e-20))
                     for lo, hi in BANDS])


def make(path=REF_PATH):
    data = {}
    for case in CASES:
        stages = render(*case)
        for stage, sig in stages.items():
            data["%s@@%s" % (case_name(case), stage)] = sig.astype(np.float32)
        print("  %-34s %2d stages, %6d samples"
              % (case_name(case), len(stages), len(stages["OUTPUT"])))
    np.savez_compressed(path, **data)
    print("\nwrote %s (%.1f MB)" % (path, os.path.getsize(path) / 1e6))


def _load(path):
    z = np.load(path)
    out = {}
    for k in z.files:
        case, stage = k.split("@@")
        out.setdefault(case, {})[stage] = z[k]
    return out


def compare(path_a, path_b, tol_db=0.5):
    """Grade b against a, per stage, per band.  Returns True if within tol."""
    A, B = _load(path_a), _load(path_b)
    ok = True
    for case in sorted(A):
        if case not in B:
            print("  %-34s MISSING" % case)
            ok = False
            continue
        print("\n  %s" % case)
        for stage in sorted(A[case]):
            a = A[case][stage].astype(np.float64)
            b = B[case].get(stage)
            if b is None:
                print("      %-18s MISSING" % stage)
                ok = False
                continue
            b = b.astype(np.float64)
            n = min(len(a), len(b))
            da = band_db(a[:n]) - band_db(b[:n])
            worst = float(np.max(np.abs(da)))
            # correlation says "same signal"; band dB says "same balance"
            if n > 16 and a[:n].std() > 1e-9 and b[:n].std() > 1e-9:
                corr = float(np.corrcoef(a[:n], b[:n])[0, 1])
            else:
                corr = float("nan")
            flag = "" if worst <= tol_db else "   <-- OUT"
            if worst > tol_db:
                ok = False
            print("      %-18s worst band %+6.2f dB   corr %+.4f%s"
                  % (stage, worst, corr, flag))
    return ok


def check(path=REF_PATH):
    """Re-render and compare against the stored reference: proves the render
    really is reproducible, and catches an accidental voicing change."""
    tmp = path + ".check.npz"
    make(tmp)
    ok = compare(path, tmp, tol_db=0.01)
    os.remove(tmp)
    print("\n%s" % ("REPRODUCIBLE - the reference still describes this build"
                    if ok else "DIVERGED - this build no longer matches the reference"))
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "make"
    if cmd == "make":
        make()
    elif cmd == "check":
        sys.exit(0 if check() else 1)
    elif cmd == "compare":
        sys.exit(0 if compare(REF_PATH, sys.argv[2]) else 1)
    else:
        print(__doc__)
