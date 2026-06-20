"""
Engine / cylinder definitions and derived geometry.

This is the data model: an :class:`Engine` is a flywheel inertia plus a list of
:class:`Cylinder` objects.  Each cylinder owns its bore/stroke/rod geometry and a
*cycle offset* — the crank angle (within the 720 deg four-stroke cycle) at which
its own power stroke begins relative to the global crankshaft angle.  Evenly
spacing those offsets is what makes an engine "even-firing".

All physical quantities are SI (metres, kilograms, pascals, radians).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

P_ATM = 101325.0  # Pa, ambient pressure


@dataclass
class Cylinder:
    """A single combustion cylinder and its slider-crank geometry."""

    bore: float            # m, cylinder diameter
    stroke: float          # m, full piston travel (= 2 * crank radius)
    rod_length: float      # m, connecting-rod length (centre to centre)
    compression_ratio: float
    cycle_offset_deg: float = 0.0  # where this cylinder sits in the 720 deg cycle
    bank_angle_deg: float = 0.0    # cylinder axis tilt from vertical (V engines)

    # --- derived geometry (filled in __post_init__) -------------------------
    crank_radius: float = field(init=False)
    piston_area: float = field(init=False)
    displacement: float = field(init=False)     # swept volume, m^3
    clearance_volume: float = field(init=False)  # volume at TDC, m^3

    def __post_init__(self) -> None:
        self.crank_radius = self.stroke / 2.0
        self.piston_area = math.pi * (self.bore / 2.0) ** 2
        self.displacement = self.piston_area * self.stroke
        # V_bdc / V_tdc = CR  ->  V_tdc = V_disp / (CR - 1)
        self.clearance_volume = self.displacement / (self.compression_ratio - 1.0)

    # --- kinematics ---------------------------------------------------------
    # theta is measured from this cylinder's TDC, in radians.

    def piston_displacement(self, theta):
        """Distance of the piston below TDC (m). 0 at TDC, `stroke` at BDC.

        Uses scalar ``math`` (not numpy) — this is called thousands of times per
        frame in the integrator, and numpy scalar calls are far slower.
        """
        r, l = self.crank_radius, self.rod_length
        s = math.sin(theta)
        root = math.sqrt(l * l - (r * s) ** 2)
        return (r + l) - (r * math.cos(theta) + root)

    def d_displacement_d_theta(self, theta):
        """d(piston_displacement)/d(theta) — the crank-slider torque arm."""
        r, l = self.crank_radius, self.rod_length
        s = math.sin(theta)
        root = math.sqrt(l * l - (r * s) ** 2)
        return r * s * (1.0 + (r * math.cos(theta)) / root)

    def volume(self, theta):
        """Instantaneous cylinder volume (m^3) at crank angle theta from TDC."""
        return self.clearance_volume + self.piston_area * self.piston_displacement(theta)


@dataclass
class Engine:
    """A complete engine: a flywheel plus an ordered list of cylinders."""

    name: str
    cylinders: list
    flywheel_inertia: float = 0.35   # kg*m^2, lumped rotating inertia
    redline_rpm: float = 7500.0
    idle_rpm: float = 850.0          # target the idle governor holds, foot off
    idle_throttle: float = 0.0       # minimum air floor (governor supplies more)
    idle_air_base: float = 0.14      # governor starting trim (cranking air)
    idle_gain: float = 8.0e-4        # idle-governor integral gain
    closed_map_fraction: float = 0.16  # manifold pressure (xP_atm) at closed throttle

    # volumetric efficiency (gives the torque curve its mid-range hump) --------
    ve_max: float = 1.0              # peak volumetric efficiency
    ve_floor: float = 0.55           # VE far from the peak rpm
    ve_peak_frac: float = 0.5        # rpm of peak VE, as a fraction of redline
    ve_width_frac: float = 0.5       # width of the VE curve, fraction of redline

    # combustion / loss tuning ------------------------------------------------
    heat_release_k: float = 3.0      # peak combustion pressure multiplier scale
    friction_static: float = 5.0     # N*m constant drag
    friction_linear: float = 0.012   # N*m per (rad/s)
    friction_quad: float = 9.0e-5    # N*m per (rad/s)^2 (windage/pumping at revs)
    engine_brake_k: float = 0.16     # closed-throttle vacuum braking, N*m per (rad/s above idle)
    starter_torque: float = 90.0     # N*m the starter motor can apply
    starter_speed_rpm: float = 280.0 # starter spins up to about this rpm
    exhaust_tone: float = 80.0       # Hz, resonant note of the exhaust 'pop'

    # drivetrain / vehicle (so each preset carries its real gearbox) ----------
    gear_ratios: list = field(default_factory=lambda: [3.45, 2.10, 1.42, 1.03, 0.82])
    final_drive: float = 3.90
    vehicle_mass: float = 1250.0     # kg
    wheel_radius: float = 0.31       # m
    clutch_capacity: float = 340.0   # N*m the clutch can transmit

    # exhaust acoustics -> physically-tuned pipe resonance (audio) -------------
    # The exhaust note's pitch is set by the pipe length and the HOT-gas speed
    # of sound, not by ear.  See audio.ExhaustWaveguide.
    exhaust_primary_m: float = 0.50  # header primary runner length (m) -> high resonance
    exhaust_total_m: float = 1.6     # full system length (m) -> low resonance
    exhaust_radius_m: float = 0.022  # pipe inner radius (open-end correction/loss)
    exhaust_channels: int = 1        # 1 = merged; 2 = separate banks (cross-plane V8)
    exhaust_openness: float = 0.85   # 0 packed muffler .. 1 open header (sets g & loop fc)
    muffler_volume_m3: float = 0.003 # Helmholtz chamber volume (m^3)
    muffler_neck_area_m2: float = 0.0020
    muffler_neck_len_m: float = 0.08

    # forced induction -------------------------------------------------------
    # "na" naturally aspirated | "roots" positive-displacement supercharger
    # (Hellcat whine) | "centrifugal" supercharger | "turbo" turbocharger
    induction: str = "na"
    boost_bar: float = 0.0           # peak boost above atmospheric (bar)
    blower_ratio: float = 0.0        # whine pitch per engine-rev (SC types)
    turbo_lag: float = 0.6           # spool time constant (s) for turbo
    anti_lag: bool = False           # bangs/crackle + whoosh on overrun

    # head / valvetrain / engine type ----------------------------------------
    # Unequal-length exhaust headers delay one bank's pulses, creating the
    # classic Subaru boxer rumble (even firing, uneven *sound*).
    header_unequal_deg: float = 0.0  # extra crank-deg delay on one bank
    valvetrain: str = "dohc"         # dohc | sohc | ohv -> breathing + tick
    valves_per_cyl: int = 4          # 4 = breathes high, 2 = low-end / muted
    is_rotary: bool = False          # Wankel rotary (no pistons; bright 'brap')
    has_gpf: bool = False            # came with a gasoline particulate filter
    has_cat: bool = True             # came with a catalytic converter

    @property
    def total_displacement(self) -> float:
        return sum(c.displacement for c in self.cylinders)

    @property
    def num_cylinders(self) -> int:
        return len(self.cylinders)
