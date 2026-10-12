"""Reference audio of the white-box turbo layer for the Swift tests.

    py tools/export_turbo_audio_ref.py
      -> swift/EngineSimCore/Tests/EngineSimCoreTests/Fixtures/turbo_audio.json

Synthesizer._turbo_audio, block after block, on the machine (turbo.py) the
synthesizer steps once per block: a wide-open pull, a LIFT (the blow-off
valve -- or, with none, the surge flutter, which is the point of all this),
part throttle.  The machine is pre-spooled first so the lift has boost to
dump.  The AGC's gain and the combustion load are inputs (the layer holds the
fired gain through a lift); the gain rises after the lift as the AGC's does.

The generator and the filter cache are captured where the run starts; from
there NOTHING is restored, so the twin, every filter, the envelopes and the
gain hold have to stay on the Python by themselves for 1.2 s.  The engine
side's memo is off (see export_turbo_ref.py).
"""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim import presets                                    # noqa: E402
from engine_sim.audio import Synthesizer                          # noqa: E402
from engine_sim.simulator import Simulator                        # noqa: E402
from engine_sim.units import rpm_to_rads                          # noqa: E402
from export_turbo import spec_for                                 # noqa: E402
from export_turbo_ref import _NoMemo, mguh_power                  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "swift", "EngineSimCore", "Tests", "EngineSimCoreTests",
                   "Fixtures", "turbo_audio.json")
RATE = 32000
FRAMES = 256
NB = 150
PRE = 60                     # pre-spool frames at 10 ms
CARS = ["rs3", "2jzst", "f40", "a3", "deltas4"]


def key_of(k):
    if k[0] == "bw":
        return "bw|%d|%d|%s" % (k[1], k[2], k[3])
    return "pk|%d|%d|%d" % (k[1], k[2], k[3])


def drive(red, k):
    t = k * FRAMES / RATE
    if t < 0.45:                                  # the pull
        return (0.75 + 0.10 * t / 0.45) * red, 1.0, False
    if t < 1.05:                                  # the lift
        x = (t - 0.45) / 0.60
        return (0.85 - 0.30 * x) * red, 0.0, False
    return 0.55 * red, 0.3, False                 # part throttle


def gain_at(k):
    t = k * FRAMES / RATE
    return 1.0 + 0.8 * min(max((t - 0.55) / 0.4, 0.0), 1.0)


def capture(key):
    eng = presets.ALL[key]()
    sim = Simulator(eng)
    sim.ignition_on = True
    ts = sim.turbo
    assert ts is not None
    syn = Synthesizer(sim, sample_rate=RATE, seed=20261013)
    syn.enabled = False
    sim._frac_cache = _NoMemo()
    red = eng.redline_rpm
    pre = []
    for k in range(PRE):
        rpm, thr = 0.75 * red, 1.0
        mg = mguh_power(sim, eng, ts, thr)
        ts.step(0.01, rpm, thr, sim._turbo_flow, sim._turbo_t03, mguh_w=mg)
        pre.append([rpm, thr, mg])
    rng = syn._rng
    start = {
        "rng": {"state": [str(int(v)) for v in (rng.s0, rng.s1, rng.s2, rng.s3)],
                "spare": None if rng._spare is None else float(rng._spare)},
        "cache": {key_of(k): [list(map(float, v[0])), list(map(float, v[1]))]
                  for k, v in syn._fcache.items()},
    }
    blocks = []
    for k in range(NB):
        rpm, thr, cut = drive(red, k)
        sim.omega = rpm_to_rads(rpm)
        sim.throttle = thr
        rpm = float(sim.rpm)                      # what the inlet reads
        mg = mguh_power(sim, eng, ts, thr)
        ts.step(FRAMES / RATE, rpm, thr, sim._turbo_flow, sim._turbo_t03,
                fuel_cut=cut, mguh_w=mg)
        sim.boost = max(ts.boost, -0.9)
        map_pa = float(sim._manifold_pressure())
        g = gain_at(k)
        cl = 1.0 if thr > 0.1 else 0.0
        syn._gain = g
        syn._comb_load = cl
        out = syn._turbo_audio(FRAMES, rpm, syn.params["super_vol"])
        blocks.append({"in": [rpm, thr, 1 if cut else 0, mg, g, cl, map_pa],
                       "out": [float(v) for v in out],
                       "bov_env": float(syn._bov_env),
                       "busy": int(syn._tw.busy)})
    return {
        "spec": spec_for(sim),
        "coolant_c": sim.coolant_c,
        "ic_soak": sim.ic_soak,
        "pre": pre,
        "start": start,
        "params": {"turbo_vol": float(syn.params["turbo_vol"]),
                   "blade_vol": float(syn.params.get("blade_vol", 1.0)),
                   "super_vol": float(syn.params["super_vol"])},
        "flutter": bool(syn.flutter), "ssqv": bool(syn.ssqv),
        "pod_filter": bool(syn.pod_filter),
        "blocks": blocks,
    }


def main():
    data = {}
    for key in CARS:
        for attempt in range(3):          # CPython 3.14 here crashes now and then
            try:
                data[key] = capture(key)
                break
            except Exception as e:        # noqa: BLE001
                print("  retry", key, e)
        d = data[key]
        rms = [math.sqrt(sum(x * x for x in b["out"]) / FRAMES) for b in d["blocks"]]
        busy = sum(1 for b in d["blocks"] if b["busy"] > 0)
        print("  %-8s flutter=%s pod=%s  rms pull %.4f lift %.4f  twin busy %d blocks"
              % (key, d["flutter"], d["pod_filter"], max(rms[:56]), max(rms[56:131]), busy))
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(data, fh)
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
