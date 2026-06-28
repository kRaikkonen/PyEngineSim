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
    # Torque-only trim for FORCED-INDUCTION cars whose open-loop boost torque runs
    # high vs the real car.  1.0 = no change.  Applied to crank torque ONLY (never
    # the audio), and BLENDED BY BOOST: full effect at peak boost, none off-boost
    # (so idle / light-throttle / NA behaviour is untouched).  e.g. 0.47 ~= halve
    # the on-boost torque of a car that simulates ~2x real.
    torque_scale: float = 1.0
    friction_static: float = 5.0     # N*m constant drag
    friction_linear: float = 0.012   # N*m per (rad/s)
    friction_quad: float = 9.0e-5    # N*m per (rad/s)^2 (windage/pumping at revs)
    engine_brake_k: float = 0.16     # closed-throttle vacuum braking, N*m per (rad/s above idle)
    starter_torque: float = 90.0     # N*m the starter motor can apply
    starter_speed_rpm: float = 280.0 # starter spins up to about this rpm
    exhaust_tone: float = 0.0        # Hz pop pitch; 0 = derive from cylinder size

    # drivetrain / vehicle (so each preset carries its real gearbox) ----------
    gear_ratios: list = field(default_factory=lambda: [3.45, 2.10, 1.42, 1.03, 0.82])
    final_drive: float = 3.90
    vehicle_mass: float = 1250.0     # kg
    wheel_radius: float = 0.31       # m
    clutch_capacity: float = 340.0   # N*m the clutch can transmit
    # transmission type sets the *shift feel*:
    #   "dct"    dual-clutch  -> fast, seamless, rev-matched (no kick)
    #   "single" single-clutch automated manual (Aventador ISR, F1 'box) ->
    #            one clutch must open & slam shut, hard torque interruption + KICK
    #   "at"     torque-converter automatic -> soft, slushy, overlapping, no kick
    #   "manual" H-pattern (drives like single-clutch in auto mode)
    gearbox_type: str = "dct"

    # exhaust acoustics -> physically-tuned pipe resonance (audio) -------------
    # The exhaust note's pitch is set by the pipe length and the HOT-gas speed
    # of sound, not by ear.  See audio.ExhaustWaveguide.
    exhaust_primary_m: float = 0.50  # header primary runner length (m) -> high resonance
    exhaust_total_m: float = 1.6     # full system length (m) -> low resonance
    exhaust_radius_m: float = 0.022  # pipe inner radius (open-end correction/loss)
    exhaust_channels: int = 1        # 1 = merged; 2 = separate banks (cross-plane V8)
    hot_v: bool = False              # "hot vee": exhaust + turbos INSIDE the V valley
                                     #   -> short, equal-length, centrally-merged
                                     #   pulses straight into the turbo -> a deeper,
                                     #   more uniform, turbo-muffled note (AMG/BMW
                                     #   M-TT, Ferrari F154 ...).  audio: merge banks,
                                     #   damp the header rasp / standing-wave whine.
    exhaust_openness: float = 0.85   # 0 packed muffler .. 1 open header (sets g & loop fc)
    muffler_volume_m3: float = 0.003 # Helmholtz chamber volume (m^3)
    muffler_neck_area_m2: float = 0.0020
    muffler_neck_len_m: float = 0.08
    # muffler construction: "reflective" = chambered/baffled (comb notches, drone),
    # "absorptive" = straight-through packed (broadband HF soak, smooth, less comb)
    muffler_type: str = "reflective"
    # tail-pipe TIP mouth size relative to the pipe: >1 big bore (brighter, more
    # open), <1 small bore (darker + a whistle, more restrictive)
    tip_scale: float = 1.0
    flex_pipe: bool = False           # corrugated flex section -> a buzzy mid resonance

    # forced induction -------------------------------------------------------
    # "na" naturally aspirated | "roots" positive-displacement supercharger
    # (Hellcat whine) | "centrifugal" supercharger | "turbo" turbocharger
    induction: str = "na"
    induction_subtype: str = ""      # turbo plumbing flavour (display + audio):
                                     #   ""            single turbo
                                     #   "twin_scroll" divided-housing single turbo ->
                                     #                 tighter, cleaner whistle, less lag
                                     #   "sequential"  small turbo spools first, big one
                                     #                 hands over up top -> a mid surge
                                     #   "twincharge"  supercharger + turbo compound ->
                                     #                 blower whine low, turbo whistle top
    boost_bar: float = 0.0           # peak boost above atmospheric (bar)
    blower_ratio: float = 0.0        # whine pitch per engine-rev (SC types)
    turbo_lag: float = 0.6           # spool time constant (s) for turbo
    turbo_spool_frac: float = 0.12   # rpm frac where boost starts (F40: high = laggy)
    turbo_spool_width: float = 0.5   # rpm frac over which boost ramps to full
    anti_lag: bool = False           # bangs/crackle + whoosh on overrun
    bov_flutter: bool = False        # lift-off = compressor surge 'stututu' (no/closed
                                     #   dump valve) instead of a clean 'pshhh' BOV
    electric_turbo: bool = False     # e-turbo / e-compressor: near-instant spool, no lag

    # hybrid electric drive ----------------------------------------------------
    # An electric motor that adds torque (instant, low-end-strong) on top of the
    # combustion engine, with constant torque below hybrid_base_rpm and constant
    # power above it.  Set hybrid_kw > 0 to make the car a hybrid (e.g. 918).
    hybrid_kw: float = 0.0           # electric motor peak power (kW)
    hybrid_base_rpm: float = 2200.0  # rpm below which the motor gives constant torque
    mgu_whine: float = 0.0           # F1-style power unit: prominent MGU-H (turbo)
                                     #   + MGU-K electric whine.  0 = ordinary hybrid
    upshift_rpm: float = 0.0         # auto upshift point (0 = 0.93*redline); F1 cars
                                     #   short-shift well below the redline

    # head / valvetrain / engine type ----------------------------------------
    # Unequal-length exhaust headers delay one bank's pulses, creating the
    # classic Subaru boxer rumble (even firing, uneven *sound*).
    header_unequal_deg: float = 0.0  # extra crank-deg delay on one bank
    # Exhaust merge topology — which cylinders share a (secondary) collector:
    #   "auto" -> 4-1 for equal-length race headers, 4-2-1 otherwise
    #   "4-1"  -> every runner on a bank merges at one collector (raw, top-end)
    #   "4-2-1"/"tri-y" -> runners pair up first (paired cylinders share a pipe),
    #                      then merge -> a secondary resonance, smoother mid-range
    #   "log"  -> a shared cast log manifold (cylinders strongly coupled, muffled)
    header_type: str = "auto"
    # Runner-length equality, GRADUATED: 0.0 = very uneven (cast log) .. 1.0 =
    # perfectly equal (e.g. a tuned 6-into-1 equal-length header).  -1 = auto
    # (binary classify from redline / straight-cut).  A "61 等长 / partial-equal"
    # header is a middle value (~0.7-0.9), not just the equal/unequal extremes.
    header_equality: float = -1.0
    valvetrain: str = "dohc"         # dohc | sohc | ohv -> breathing + tick
    valves_per_cyl: int = 4          # 4 = breathes high, 2 = low-end / muted
    variable_valve: str = ""         # "VTEC"/"VANOS"/"VVT-i"/... "" = none (display only)
    # --- extra detail models (audio); all default NEUTRAL (no change) -----------
    # Fuel injection: "port" (MPI, soft), "direct" (GDI injector tick/clatter),
    # "dual" (port+direct), "piezo" (sharp high-pressure click), "carb" (none),
    # "mech" (old mechanical race injection, none), "diesel" (common-rail clatter).
    injection: str = "port"
    balance_shaft: bool = False      # cancels the secondary shake of an I4 / 90deg-V6
    # Valve LIFT mechanism: "fixed", "two-stage" (VTEC/AVS-style switch -> a step),
    # "continuous" (Valvetronic/MultiAir -> throttleless, extra-smooth).  Derived
    # from variable_valve in _annotate.
    valve_lift: str = "fixed"
    integrated_manifold: bool = False  # exhaust manifold cast into the head (modern
    #                                    turbos): short, hot, tighter & more muffled
    # Cam profile: "mild" (smooth idle), "stock", "hot" (lopey idle + rasp),
    # "race" (very lumpy idle, big overlap, strong top-end).
    cam_profile: str = "stock"
    rotation: str = "CW"             # crank rotation viewed from front: CW | CCW (display)
    crank_plane: str = ""            # crankpin phase (display): "flat" (single-plane,
                                     #   pins at 0/180 -> the high flat-plane scream)
                                     #   | "cross" (two-plane, pins at 0/90/180/270 ->
                                     #   the burbling cross-plane rumble) | "" none
    is_rotary: bool = False          # Wankel rotary (no pistons; bright 'brap')
    is_w: bool = False               # W engine (two narrow-vee banks) -> draw 4 rows
    is_radial: bool = False          # aircraft radial -> draw cylinders in a star
    has_gpf: bool = False            # came with a gasoline particulate filter
    has_cat: bool = True             # came with a catalytic converter
    straight_cut: bool = False       # straight-cut (dog-box) gearbox -> whine on by default
    wall_material: str = "steel"     # exhaust pipe material: titanium / steel /
                                     #   aluminium / iron -> wall-resonance pitch
    cat_cells_cpsi: int = 400        # catalytic cell density (cells/in^2): higher =
                                     #   denser honeycomb = more high-frequency damping
    intake_runner_m: float = 0.30    # intake runner length (m) -> per-cylinder
                                     #   breathing (+/-3%) variation in the voicing model
    backpressure_coupling: float = 0.5  # 0..1: how strongly each exhaust pulse loads the
                                     #   next cylinder to fire (cyl-to-cyl strong/weak beat)
    gear_grain: float = 0.0          # gear-driven valvetrain/timing-gear whir: a fine,
                                     #   dense 'grind-like' grain (Ferrari V12 etc.), 0=off

    def __post_init__(self) -> None:
        # Physically derive the exhaust 'pop' resonance pitch from the MEAN
        # CYLINDER SIZE instead of hand-tuning it: a small cylinder empties a
        # short, sharp pulse (high pitch); a big lazy cylinder a long, low one.
        # The per-car *character* still comes from the (physical) pipe geometry —
        # length / radius / openness / muffler — so same-engine cars (Diablo vs
        # Murcielago) still differ through their exhaust SYSTEMS, not a fudge.
        # Set exhaust_tone explicitly (> 0) only to force a value.
        if self.exhaust_tone <= 0.0:
            cyl_litres = (self.total_displacement * 1000.0) / max(self.num_cylinders, 1)
            tone = 36.0 / max(cyl_litres, 0.12) ** 0.92
            if self.is_rotary:
                tone *= 1.7              # no valves, peripheral ports -> bright brap
            self.exhaust_tone = float(min(max(tone, 44.0), 185.0))

    @property
    def total_displacement(self) -> float:
        return sum(c.displacement for c in self.cylinders)

    @property
    def firing_order(self) -> list:
        """Cylinder numbers (1-based) in the order they fire — derived from the
        cycle offsets, so it always matches the physics."""
        order = sorted(range(self.num_cylinders),
                       key=lambda i: self.cylinders[i].cycle_offset_deg)
        return [i + 1 for i in order]

    @property
    def num_cylinders(self) -> int:
        return len(self.cylinders)
