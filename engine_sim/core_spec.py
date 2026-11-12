"""What the v2 core is told about an engine (docs/REDESIGN_V2.md s.6.2, s.9).

The core (core/sound, Rust) solves each cylinder's discharge through its
valves live.  It knows nothing of cams or flow benches: it takes each valve's
EFFECTIVE FLOW AREA against crank angle as a table.  This module is the bake
side that makes those tables -- and the rest of the core's spec -- from a
preset.

Angles are crank degrees in a cylinder's own cycle with 0 at FIRING top dead
centre (expansion 0-180, exhaust 180-360, intake 360-540, compression
540-720).  gas_truth counts from intake TDC; that is this plus 360.

Head geometry.  The presets do not carry valve sizes or cam data, so these
are the same typed defaults gas_truth.TruthCylinder synthesises: valve
diameters from the bore, lift 0.27 d, duration by cam profile, the two-stage
lobe by rpm.  Where a preset one day carries the real numbers, they go in
here and nothing downstream changes.

The LIFT CURVE is not gas_truth's.  Its cos^4 bump is at half lift for only
0.36 of its duration and has a fullness of 0.375 -- a valve that barely opens
for the first 40 degrees.  Production cams measure 0.55-0.62 on both counts,
and that is what decides how fast a blowdown starts.  `LIFT_PRODUCTION`,
(1 - x^2)^1.5, has 0.61 and 0.59.  `LIFT_TRUTH` is kept to compare the two
models on equal terms (tools/core_check.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

AIR_MOLAR_MASS = 0.02897      # kg/mol, as gas_truth

LIFT_PRODUCTION = "production"
LIFT_TRUTH = "truth"


def lift_fraction(x, shape=LIFT_PRODUCTION):
    """Lift over full lift at x in [-1, 1] across the valve's open period."""
    if abs(x) >= 1.0:
        return 0.0
    if shape == LIFT_TRUTH:
        c = 0.5 * (1.0 + math.cos(math.pi * x))
        return c * c
    return (1.0 - x * x) ** 1.5


def area_fraction(u):
    """Flow area over its maximum at lift fraction u: the curtain grows with
    lift and saturates as the port takes over (the flow-bench shape)."""
    return min(1.0, 1.25 * u - 0.25 * u * u) if u > 0.0 else 0.0


@dataclass
class ValveCurve:
    open_deg: float           # [0, 720), firing TDC = 0
    span_deg: float
    area: list                # m^2, uniform from opening to closing, ends 0

    def area_after(self, d):
        """The core's own interpolation (Catmull-Rom), for references."""
        if d >= self.span_deg or d < 0.0:
            return 0.0
        a = self.area
        last = len(a) - 1
        x = d / self.span_deg * last
        i = min(int(x), last - 1)
        f = x - i
        p1, p2 = a[i], a[i + 1]
        p0 = p1 if i == 0 else a[i - 1]
        p3 = p2 if i + 2 > last else a[i + 2]
        v = p1 + 0.5 * f * ((p2 - p0) + f * ((2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3)
                                             + f * (3.0 * (p1 - p2) + p3 - p0)))
        return max(v, 0.0)

    def area_at(self, theta):
        return self.area_after((theta - self.open_deg) % 720.0)


@dataclass
class CoreSpec:
    bore: float
    stroke: float
    rod: float
    compression: float
    firing_deg: list
    exhaust: ValveCurve
    intake: ValveCurve
    wall_temp: float = 450.0
    heat_scale: float = 1.0
    gamma_const: float = 0.0
    wall_refresh_deg: float = 1.0
    notes: dict = field(default_factory=dict)

    @property
    def swept(self):
        return math.pi * 0.25 * self.bore ** 2 * self.stroke

    @property
    def clearance(self):
        return self.swept / (self.compression - 1.0)

    def volume(self, theta_deg):
        th = math.radians(theta_deg)
        r, l = 0.5 * self.stroke, self.rod
        s = math.sin(th)
        x = r * (1.0 - math.cos(th)) + l - math.sqrt(l * l - r * r * s * s)
        return self.clearance + math.pi * 0.25 * self.bore ** 2 * x

    def dvolume(self, theta_deg):
        """dV/dtheta, m^3 per RADIAN."""
        th = math.radians(theta_deg)
        r, l = 0.5 * self.stroke, self.rod
        s, c = math.sin(th), math.cos(th)
        return math.pi * 0.25 * self.bore ** 2 * r * s * (1.0 + r * c / math.sqrt(l * l - r * r * s * s))


def head(eng, rpm=None):
    """Valve sizes, lifts and timing of a preset -- gas_truth's rules.
    Returns a dict; centres are in THIS module's angle convention."""
    bore = eng.cylinders[0].bore
    valves = getattr(eng, "valves_per_cyl", 4)
    n_int = max(1, valves // 2)
    n_exh = max(1, valves - n_int)
    d_iv = bore * (0.39 if n_int >= 2 else 0.47)
    d_ev = 0.83 * d_iv
    cam = getattr(eng, "cam_profile", "stock")
    dur_i = {"mild": 236.0, "stock": 250.0, "hot": 266.0, "race": 284.0}.get(cam, 250.0)
    lift_scale = 1.0
    if getattr(eng, "valve_lift", "fixed") == "two-stage" and rpm is not None:
        xr = getattr(eng, "vtec_rpm", 0.0) or 0.62 * eng.redline_rpm
        hi = rpm >= xr
        dur_i = 286.0 if hi else 230.0
        lift_scale = 1.15 if hi else 0.82
    dur_e = dur_i + 4.0
    return {
        "n_int": n_int, "n_exh": n_exh, "d_iv": d_iv, "d_ev": d_ev,
        "dur_i": dur_i, "dur_e": dur_e,
        "lift_i": 0.27 * d_iv * lift_scale, "lift_e": 0.27 * d_ev * lift_scale,
        "cd_i": 0.60, "cd_e": 0.62,
        # intake opens 15 deg before the overlap TDC (360); exhaust closes 10 after
        "centre_i": 360.0 + 0.5 * dur_i - 15.0,
        "centre_e": 360.0 - 0.5 * dur_e + 10.0,
    }


def valve_curve(centre, dur, cda_max, shape=LIFT_PRODUCTION, per_deg=2):
    """The effective-area table of a valve (pair): `per_deg` points a degree."""
    n = int(round(dur * per_deg)) + 1
    area = []
    for i in range(n):
        x = -1.0 + 2.0 * i / (n - 1)
        area.append(cda_max * area_fraction(lift_fraction(x, shape)))
    area[0] = 0.0
    area[-1] = 0.0
    return ValveCurve(open_deg=(centre - 0.5 * dur) % 720.0, span_deg=dur, area=area)


def spec_from_engine(eng, rpm=None, lift=LIFT_PRODUCTION, cylinders=None, truth=None):
    """The core's spec of a preset.

    ``truth``: a gas_truth.TruthCylinder of the same engine -- then the valves'
    flow areas are taken from ITS flow constants (k * sqrt(M) is the effective
    area of the same orifice law), so the two models meter the same valve.
    """
    h = head(eng, rpm)
    c0 = eng.cylinders[0]
    if truth is not None:
        cda_i = truth.k_int_max * math.sqrt(AIR_MOLAR_MASS)
        cda_e = truth.k_exh_max * math.sqrt(AIR_MOLAR_MASS)
    else:
        cda_i = h["n_int"] * math.pi * h["d_iv"] * h["lift_i"] * h["cd_i"]
        cda_e = h["n_exh"] * math.pi * h["d_ev"] * h["lift_e"] * h["cd_e"]
    firing = [c.cycle_offset_deg % 720.0 for c in eng.cylinders]
    if cylinders is not None:
        firing = firing[:cylinders]
    return CoreSpec(
        bore=c0.bore, stroke=c0.stroke, rod=c0.rod_length,
        compression=c0.compression_ratio, firing_deg=firing,
        exhaust=valve_curve(h["centre_e"], h["dur_e"], cda_e, lift),
        intake=valve_curve(h["centre_i"], h["dur_i"], cda_i, lift),
        notes={"head": h, "lift": lift, "cda_i": cda_i, "cda_e": cda_e},
    )
