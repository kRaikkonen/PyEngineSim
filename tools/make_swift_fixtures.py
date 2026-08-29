"""Generate reference vectors so the Swift port can prove itself offline.

The golden reference (tools/golden.py) grades a whole render.  These are the
other end of the same idea: the smallest possible units -- one filter design,
one filtered block -- with coefficients and expected output captured from the
Python that Leo actually tuned against.  A Swift test that loads these passes
or fails without a Mac, a phone, or an ear in the room.

    py tools/make_swift_fixtures.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_sim.audio import _np_butter, _np_lfilter, _peaking   # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "swift", "EngineSimCore", "Tests", "EngineSimCoreTests",
                   "Fixtures", "filters.json")

# every design audio.py actually asks for, at both shipped rates
BUTTER_CASES = [
    (1, 0.05, "low"), (2, 0.0034, "high"), (2, 0.15, "low"),
    (2, 0.6875, "low"), (2, 0.0437, "high"), (2, 0.04, "low"),
    (2, 0.3, "low"), (2, 0.9, "low"),
]
PEAKING_CASES = [
    (3000.0, 11.0, 12.0), (5000.0, 14.0, 9.0), (1200.0, 8.0, 6.0),
    (8000.0, 11.0, 15.0), (400.0, 3.0, -8.0), (6000.0, 20.0, 18.0),
]
BLOCK = 256
RATE = 32000


def main():
    rng = np.random.default_rng(20260829)
    x = rng.standard_normal(BLOCK * 4)
    cases = []

    for order, wn, btype in BUTTER_CASES:
        b, a = _np_butter(order, wn, btype)
        zi = np.zeros(max(len(a), len(b)) - 1)
        y = []
        for i in range(0, len(x), BLOCK):        # streamed, state carried
            blk, zi = _np_lfilter(b, a, x[i:i + BLOCK], zi=zi)
            y.append(blk)
        cases.append({
            "kind": "butter", "order": order, "wn": wn, "btype": btype,
            "b": [float(v) for v in b], "a": [float(v) for v in a],
            "y": [float(v) for v in np.concatenate(y)],
        })

    for f0, q, gain in PEAKING_CASES:
        b, a = _peaking(f0, q, gain, RATE)
        zi = np.zeros(2)
        y = []
        for i in range(0, len(x), BLOCK):
            blk, zi = _np_lfilter(b, a, x[i:i + BLOCK], zi=zi)
            y.append(blk)
        cases.append({
            "kind": "peaking", "f0": f0, "q": q, "gain_db": gain,
            "rate": RATE,
            "b": [float(v) for v in b], "a": [float(v) for v in a],
            "y": [float(v) for v in np.concatenate(y)],
        })

    doc = {
        "note": ("Reference vectors from engine_sim.audio, the implementation "
                 "these were tuned against. Streamed in %d-sample blocks with "
                 "filter state carried, which is how the synth runs them."
                 % BLOCK),
        "block": BLOCK,
        "x": [float(v) for v in x],
        "cases": cases,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="ascii") as fh:
        json.dump(doc, fh)
    print("wrote %s (%.0f KB, %d cases)"
          % (OUT, os.path.getsize(OUT) / 1024.0, len(cases)))


if __name__ == "__main__":
    main()
