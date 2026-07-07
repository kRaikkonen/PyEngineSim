"""
Surrogate layer: physics numbers served from offline-baked models.

This is the runtime half of the "white-box truth -> fast surrogate" plan:
expensive first-principles physics is swept OFFLINE over the operating space,
and the runtime reads the result through one uniform interface —

    surrogate.evaluate(kind, rpm=..., map=...)

— transparently backed by either

  * :class:`LUT`  — an N-D lookup table with multilinear interpolation
    (pure numpy + bisect; the ONLY runtime cost is a handful of float ops).
    Low-dimensional (<= 3-D) channels stay LUTs so players can export and
    edit them as plain CSV/JSON.
  * :class:`MLP`  — a small dense network for 4-D+ channels, forward pass
    hand-rolled in numpy (a few thousand multiply-adds at CONTROL rate).
    Training happens offline on a PC; the device never needs torch.

Nothing here imports scipy or torch — phone-safe by construction.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right

import numpy as np


class LUT:
    """An N-D lookup table over rectangular axes with multilinear interpolation.

    ``axes``  — list of (name, 1-D ascending grid) pairs.
    ``values``— ndarray whose shape matches the grid lengths.

    Queries clamp to the table edges (no extrapolation surprises).  For the
    hot 2-D case there is a scalar fast path (:meth:`eval2`) built on plain
    Python floats + bisect — ~1 microsecond, safe to call per physics substep.
    """

    def __init__(self, axes, values):
        self.names = [str(n) for n, _g in axes]
        self.grids = [np.asarray(g, dtype=np.float64) for _n, g in axes]
        self.values = np.asarray(values, dtype=np.float64)
        if tuple(len(g) for g in self.grids) != self.values.shape:
            raise ValueError("grid lengths %r do not match values shape %r"
                             % (tuple(len(g) for g in self.grids),
                                self.values.shape))
        for g in self.grids:
            if len(g) < 2 or np.any(np.diff(g) <= 0):
                raise ValueError("each axis grid must be ascending, len >= 2")
        # plain-list mirrors for the scalar fast path (bisect on lists beats
        # numpy searchsorted for single queries)
        self._pygrids = [g.tolist() for g in self.grids]

    # ------------------------------------------------------------- queries
    def _axis_locate(self, ax, x):
        g = self._pygrids[ax]
        j = bisect_right(g, x) - 1
        if j < 0:
            return 0, 0.0
        if j >= len(g) - 1:
            return len(g) - 2, 1.0
        t = (x - g[j]) / (g[j + 1] - g[j])
        return j, t

    def eval2(self, x0, x1):
        """Scalar bilinear fast path for 2-D tables (the per-substep hot path)."""
        j0, t0 = self._axis_locate(0, x0)
        j1, t1 = self._axis_locate(1, x1)
        v = self.values
        a = v[j0, j1] * (1.0 - t1) + v[j0, j1 + 1] * t1
        b = v[j0 + 1, j1] * (1.0 - t1) + v[j0 + 1, j1 + 1] * t1
        return float(a * (1.0 - t0) + b * t0)

    def __call__(self, **inputs):
        """General N-D multilinear interpolation, inputs keyed by axis name."""
        n = len(self.grids)
        loc = []
        for ax in range(n):
            loc.append(self._axis_locate(ax, float(inputs[self.names[ax]])))
        acc = 0.0
        for corner in range(1 << n):
            w = 1.0
            idx = []
            for ax in range(n):
                j, t = loc[ax]
                hi = (corner >> ax) & 1
                w *= t if hi else (1.0 - t)
                idx.append(j + hi)
            if w > 0.0:
                acc += w * float(self.values[tuple(idx)])
        return acc

    # ------------------------------------------------------------ storage
    def save(self, path):
        meta = {"names": self.names}
        arrs = {"values": self.values,
                "meta": np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)}
        for i, g in enumerate(self.grids):
            arrs[f"grid{i}"] = g
        np.savez(path, **arrs)

    @classmethod
    def load(cls, path):
        z = np.load(path)
        meta = json.loads(bytes(z["meta"]).decode())
        axes = [(meta["names"][i], z[f"grid{i}"]) for i in range(len(meta["names"]))]
        return cls(axes, z["values"])

    def export_json(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"axes": {n: g.tolist() for n, g in zip(self.names, self.grids)},
                       "values": self.values.tolist()}, f, indent=1)

    def export_csv(self, path):
        """2-D only: rows = axis-0 grid, cols = axis-1 grid (spreadsheet-ready)."""
        if len(self.grids) != 2:
            raise ValueError("CSV export is for 2-D tables")
        with open(path, "w", encoding="utf-8") as f:
            f.write("%s\\%s," % (self.names[0], self.names[1])
                    + ",".join(f"{v:g}" for v in self.grids[1]) + "\n")
            for i, r in enumerate(self.grids[0]):
                f.write(f"{r:g}," + ",".join(f"{v:.4f}" for v in self.values[i])
                        + "\n")


class MLP:
    """A small dense network evaluated with plain numpy (device needs no torch).

    Layout in the .npz: W0,b0,W1,b1,... plus 'act' ("tanh"/"relu"), the input
    axis names, and min/max normalisation vectors for inputs and outputs.
    Sized for control-rate regression (~KBs of weights), NOT waveform synthesis.
    """

    def __init__(self, layers, act="tanh", in_names=(), in_lo=None, in_hi=None,
                 out_lo=0.0, out_hi=1.0):
        self.layers = [(np.asarray(W, np.float64), np.asarray(b, np.float64))
                       for W, b in layers]
        self.act = act
        self.in_names = list(in_names)
        self.in_lo = np.asarray(in_lo if in_lo is not None
                                else np.zeros(len(self.in_names)), np.float64)
        self.in_hi = np.asarray(in_hi if in_hi is not None
                                else np.ones(len(self.in_names)), np.float64)
        self.out_lo, self.out_hi = float(out_lo), float(out_hi)

    def __call__(self, **inputs):
        x = np.array([float(inputs[n]) for n in self.in_names], dtype=np.float64)
        span = np.maximum(self.in_hi - self.in_lo, 1e-12)
        x = (x - self.in_lo) / span * 2.0 - 1.0          # -> [-1, 1]
        for i, (W, b) in enumerate(self.layers):
            x = W @ x + b
            if i < len(self.layers) - 1:
                x = np.tanh(x) if self.act == "tanh" else np.maximum(x, 0.0)
        y = x[0] if x.size == 1 else x
        return self.out_lo + (self.out_hi - self.out_lo) * (float(y) + 1.0) * 0.5 \
            if x.size == 1 else y

    def save(self, path):
        arrs = {"act": np.frombuffer(self.act.encode(), dtype=np.uint8),
                "in_names": np.frombuffer(json.dumps(self.in_names).encode(),
                                          dtype=np.uint8),
                "in_lo": self.in_lo, "in_hi": self.in_hi,
                "out_range": np.array([self.out_lo, self.out_hi])}
        for i, (W, b) in enumerate(self.layers):
            arrs[f"W{i}"], arrs[f"b{i}"] = W, b
        np.savez(path, **arrs)

    @classmethod
    def load(cls, path):
        z = np.load(path)
        layers = []
        i = 0
        while f"W{i}" in z:
            layers.append((z[f"W{i}"], z[f"b{i}"]))
            i += 1
        return cls(layers, act=bytes(z["act"]).decode(),
                   in_names=json.loads(bytes(z["in_names"]).decode()),
                   in_lo=z["in_lo"], in_hi=z["in_hi"],
                   out_lo=float(z["out_range"][0]), out_hi=float(z["out_range"][1]))


class Surrogate:
    """Named registry of physics channels; the sim asks by kind, the backing
    model (LUT or MLP) is an implementation detail."""

    def __init__(self):
        self._channels = {}

    def register(self, kind, model):
        self._channels[kind] = model

    def has(self, kind):
        return kind in self._channels

    def get(self, kind):
        return self._channels.get(kind)

    def evaluate(self, kind, **inputs):
        return self._channels[kind](**inputs)


def bake(truth_fn, axes):
    """Offline sweep scaffold: evaluate ``truth_fn(**point)`` over the grid
    cartesian product and return a LUT.  ``axes`` = [(name, grid), ...]."""
    names = [n for n, _g in axes]
    grids = [np.asarray(g, dtype=np.float64) for _n, g in axes]
    shape = tuple(len(g) for g in grids)
    values = np.empty(shape, dtype=np.float64)
    it = np.ndindex(*shape)
    for idx in it:
        point = {names[k]: float(grids[k][idx[k]]) for k in range(len(names))}
        values[idx] = float(truth_fn(**point))
    return LUT(axes, values)
