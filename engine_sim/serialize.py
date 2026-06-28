"""
Export / import every car preset as plain data, so players can open the engine
parameters in a spreadsheet (Excel, Google Sheets, ...) or any text editor,
tweak them, and feed them back in.

Two artefacts are produced (run ``py -m engine_sim.serialize``):

  * ``docs/presets.json`` — the COMPLETE, round-trippable dump: every Engine
    field plus the full per-cylinder geometry.  ``engine_from_dict`` rebuilds an
    exact Engine from one entry, so an edited JSON loads straight back.
  * ``docs/presets.csv`` — a FLAT, one-row-per-car table of the scalar fields
    (the cylinders are summarised as count + bore/stroke/rod/CR of cylinder 1,
    plus the bank-angle / firing-offset lists), for quick editing in a
    spreadsheet.  The JSON is the authoritative, loss-free copy.

Nothing in the running sim imports this module — it is a tool, so it never adds
load-time cost.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import os

from .engine import Cylinder, Engine
from . import presets

# the six real (init) cylinder parameters — the rest of a Cylinder is derived
_CYL_FIELDS = ["bore", "stroke", "rod_length", "compression_ratio",
               "cycle_offset_deg", "bank_angle_deg"]


def engine_to_dict(eng: Engine) -> dict:
    """A JSON-safe, round-trippable dict of one Engine (all fields + cylinders).

    A read-only ``_derived`` block (displacement, firing order, ...) is included
    for reference; it is ignored on import."""
    d = {}
    for f in dataclasses.fields(eng):
        if f.name == "cylinders":
            continue
        d[f.name] = getattr(eng, f.name)
    d["cylinders"] = [{k: getattr(c, k) for k in _CYL_FIELDS} for c in eng.cylinders]
    d["_derived"] = {
        "num_cylinders": eng.num_cylinders,
        "total_displacement_L": round(eng.total_displacement * 1000.0, 3),
        "firing_order": eng.firing_order,
        "exhaust_tone_hz": round(eng.exhaust_tone, 1),
    }
    return d


def engine_from_dict(d: dict) -> Engine:
    """Rebuild an Engine from a dict produced by :func:`engine_to_dict` (so an
    edited entry in presets.json loads straight back).  Unknown keys (e.g. the
    ``_derived`` block) and any future fields not present are ignored/defaulted."""
    names = {f.name for f in dataclasses.fields(Engine)}
    cyls = [Cylinder(**{k: c[k] for k in _CYL_FIELDS if k in c})
            for c in d.get("cylinders", [])]
    kw = {k: v for k, v in d.items() if k in names and k != "cylinders"}
    return Engine(cylinders=cyls, **kw)


def all_presets_dict() -> dict:
    out = {}
    for key in sorted(presets.ALL):
        try:
            out[key] = engine_to_dict(presets.ALL[key]())
        except Exception as e:               # never let one bad preset break the dump
            out[key] = {"error": f"{type(e).__name__}: {e}"}
    return out


def dump_json(path: str) -> int:
    data = all_presets_dict()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return len(data)


def dump_csv(path: str) -> int:
    """Flat one-row-per-car table for spreadsheets.  Scalar Engine fields plus a
    cylinder summary; list fields (gear_ratios, bank angles, firing offsets) are
    written as compact ``|``-separated strings."""
    rows = []
    scalar_names = [f.name for f in dataclasses.fields(Engine)
                    if f.name != "cylinders"]
    for key in sorted(presets.ALL):
        try:
            eng = presets.ALL[key]()
        except Exception:
            continue
        c0 = eng.cylinders[0]
        row = {"key": key}
        for n in scalar_names:
            v = getattr(eng, n)
            row[n] = "|".join(map(str, v)) if isinstance(v, list) else v
        row.update({
            "num_cylinders": eng.num_cylinders,
            "displacement_L": round(eng.total_displacement * 1000.0, 3),
            "bore_m": c0.bore, "stroke_m": c0.stroke, "rod_length_m": c0.rod_length,
            "compression_ratio": c0.compression_ratio,
            "bank_angles_deg": "|".join(f"{c.bank_angle_deg:g}" for c in eng.cylinders),
            "firing_offsets_deg": "|".join(f"{c.cycle_offset_deg:g}" for c in eng.cylinders),
        })
        rows.append(row)
    cols = (["key"] + scalar_names + ["num_cylinders", "displacement_L", "bore_m",
            "stroke_m", "rod_length_m", "compression_ratio", "bank_angles_deg",
            "firing_offsets_deg"])
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _docs_dir() -> str:
    # engine_sim/serialize.py -> repo root -> docs/
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(root, "docs")
    os.makedirs(d, exist_ok=True)
    return d


def main() -> None:
    d = _docs_dir()
    jpath = os.path.join(d, "presets.json")
    cpath = os.path.join(d, "presets.csv")
    n = dump_json(jpath)
    m = dump_csv(cpath)
    # sanity: every entry must round-trip back to an Engine
    data = json.load(open(jpath, encoding="utf-8"))
    ok = 0
    for k, v in data.items():
        if "error" in v:
            continue
        try:
            engine_from_dict(v)
            ok += 1
        except Exception as e:
            print(f"  round-trip FAIL {k}: {e}")
    print(f"wrote {jpath} ({n} cars)")
    print(f"wrote {cpath} ({m} cars)")
    print(f"round-trip OK: {ok}/{n}")


if __name__ == "__main__":
    main()
