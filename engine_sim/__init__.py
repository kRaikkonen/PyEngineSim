"""
engine_sim — A Python reimplementation of AngeTheGreat's Engine Simulator.

This package recreates the *core* of Engine Simulator (Community Edition) in pure
Python + numpy:

  * physically-grounded crank/rod/piston kinematics (the analytic crank-slider
    relations, equivalent to the original's 2D constraint solver),
  * a thermodynamic 4-stroke combustion model (adiabatic compression/expansion
    + heat release) that produces a real torque pulse per power stroke,
  * rigid-body crankshaft dynamics integrated in real time,
  * a real-time exhaust-pulse audio synthesizer (the engine sound), and
  * a pygame application with a live engine animation, gauges and keyboard
    controls that mirror the original game.

It is intentionally readable rather than maximally fast — the goal is a playable,
hackable engine simulator you can actually understand end to end.
"""

from .engine import Cylinder, Engine
from .simulator import Simulator
from . import presets

__all__ = ["Cylinder", "Engine", "Simulator", "presets"]
# Bumped automatically by .git/hooks/post-commit after each commit:
# 0.1 -> 0.1.1 -> 0.1.2 -> ...
__version__ = "0.3.1"
