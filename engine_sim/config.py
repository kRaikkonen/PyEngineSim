"""
Save / load engines and audio mixer settings as JSON config files.

This makes both the engine presets and the audio (EQ / mixer) presets fully
data-driven: you can dump the current engine or sound to a ``.json`` you can
edit, share or re-load — the role the original game's ``.mr`` scripts played.
"""

from __future__ import annotations

import json
import os
from dataclasses import fields

from .engine import Engine, Cylinder

# configs/ lives next to run.py (the project root).
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs")
ENGINE_DIR = os.path.join(CONFIG_DIR, "engines")
AUDIO_DIR = os.path.join(CONFIG_DIR, "audio")


def _ensure_dirs():
    for d in (ENGINE_DIR, AUDIO_DIR):
        os.makedirs(d, exist_ok=True)


def _init_fields(obj) -> dict:
    """Constructor (init=True) fields of a dataclass instance -> plain dict."""
    return {f.name: getattr(obj, f.name) for f in fields(obj) if f.init}


# ------------------------------------------------------------------ engines

def engine_to_dict(eng: Engine) -> dict:
    d = {f.name: getattr(eng, f.name)
         for f in fields(eng) if f.init and f.name != "cylinders"}
    d["cylinders"] = [_init_fields(c) for c in eng.cylinders]
    return d


def engine_from_dict(d: dict) -> Engine:
    cylinders = [Cylinder(**c) for c in d["cylinders"]]
    scalars = {k: v for k, v in d.items() if k != "cylinders"}
    return Engine(cylinders=cylinders, **scalars)


def save_engine(eng: Engine, path: str):
    _ensure_dirs()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(engine_to_dict(eng), f, indent=2, ensure_ascii=False)


def load_engine(path: str) -> Engine:
    with open(path, "r", encoding="utf-8") as f:
        return engine_from_dict(json.load(f))


def list_engine_configs() -> list:
    """All engine .json files in configs/engines/ -> [(label, path), ...]."""
    if not os.path.isdir(ENGINE_DIR):
        return []
    out = []
    for name in sorted(os.listdir(ENGINE_DIR)):
        if name.endswith(".json"):
            out.append((os.path.splitext(name)[0], os.path.join(ENGINE_DIR, name)))
    return out


# --------------------------------------------------------------- audio / EQ

def save_audio(params: dict, path: str, voice: int = 0, cabin: bool = False):
    _ensure_dirs()
    data = {"params": dict(params), "voice": voice, "cabin": cabin}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_audio(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_audio_configs() -> list:
    if not os.path.isdir(AUDIO_DIR):
        return []
    out = []
    for name in sorted(os.listdir(AUDIO_DIR)):
        if name.endswith(".json"):
            out.append((os.path.splitext(name)[0], os.path.join(AUDIO_DIR, name)))
    return out


def _safe_name(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name)
    return keep.strip().replace(" ", "_") or "preset"


def engine_path(name: str) -> str:
    return os.path.join(ENGINE_DIR, _safe_name(name) + ".json")


def audio_path(name: str) -> str:
    return os.path.join(AUDIO_DIR, _safe_name(name) + ".json")
