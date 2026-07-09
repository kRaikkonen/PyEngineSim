"""
White-box volumetric-efficiency model (the plan's Phase-1 "临时真理").

Replaces the old hand-drawn Gaussian VE bell with numbers DERIVED from the
engine's real geometry, using three textbook mechanisms:

1. TAYLOR MACH INDEX (Taylor & Livengood):  VE collapses once the mean flow
   velocity through the intake valves approaches sonic — Z = Ap*Sp / (Av*c).
   Real engines fall off a cliff past Z ~ 0.6.  This is what actually limits
   a 2-valve pushrod up top and lets a 4-valve race head scream.

2. ENGELMAN HELMHOLTZ RAM TUNING:  the intake runner + cylinder form a
   Helmholtz resonator, f_h = (c/2pi)*sqrt(A/(L*V_eff)); charge rams in near
   the tuned speed (N ~ 60*f_h/2.1) giving the mid-range torque HUMP, with a
   weaker second bump one octave down — the classic two-hump torque curve.

3. RESIDUAL-GAS BACKFLOW:  at part throttle the exhaust back-pressure exceeds
   the manifold pressure, so burned gas is pushed back into the intake at
   overlap; the trapped-fresh-charge fraction falls as x_r ~ (p_e/p_m)^(1/g)/CR.

A cam-profile term shifts the character (hot/race cams: late intake closing
= better top-end breathing, backflow penalty at low rpm — the lopey idle's
thermodynamic half).

The BAKE normalises the surface so its best point equals the preset's old
``ve_max`` — per-car peak-torque calibration is preserved; what changes is the
SHAPE (where the humps sit and how it rolls off), which now comes from
geometry instead of taste.

Everything is closed-form scalar math (no numpy needed per point); baking a
whole table takes ~1 ms, so tables are built at engine load — no assets to
ship until the Phase-3 solver swap makes offline baking worthwhile.
"""

from __future__ import annotations

import math

import numpy as np

from .surrogate import LUT

C_INTAKE = 340.0        # speed of sound in warm intake air (~292 K), m/s
GAMMA_EX = 1.30         # burned-gas gamma for the residual backflow term

# --- global breathing-shape constants (calibrated ONCE against the acceptance
# reference, never per-car) --------------------------------------------------
# TUNE_DIV: Engelman primary tuned engine speed N = 60*f_h / TUNE_DIV.  Sets WHERE
#   the mid-range ram torque hump sits; lower -> the hump moves UP the rev range.
# ROLL_KNEE / ROLL_EXP: Taylor Mach-index roll-off  1/(1+(z/(ROLL_KNEE*cam_knee))^ROLL_EXP).
#   A higher knee / lower exponent lets a good head keep BREATHING toward redline
#   (peak power near redline) instead of the VE dying in the mid-range.
TUNE_DIV = 1.65
ROLL_KNEE = 0.75
ROLL_EXP = 2.9


def _geometry(eng):
    """Effective intake-flow geometry estimated from real engine dimensions."""
    cyl = eng.cylinders[0]
    bore, stroke = cyl.bore, cyl.stroke
    a_piston = math.pi * 0.25 * bore * bore
    n_int = max(1, getattr(eng, "valves_per_cyl", 4) // 2)   # intake valves
    # typical intake-valve head diameter vs bore (2-valve heads run bigger valves)
    d_v = bore * (0.39 if n_int >= 2 else 0.47)
    # effective flow area: valve curtain averaged over the lift event x Cd
    a_valve = n_int * math.pi * 0.25 * d_v * d_v * 0.62
    # runner: physical length + open-end correction, area a little over valve
    length = max(getattr(eng, "intake_runner_m", 0.30), 0.05) + 0.35 * d_v
    a_runner = math.pi * 0.25 * (1.10 * d_v) ** 2
    v_eff = cyl.displacement * 0.5 + cyl.clearance_volume
    f_h = (C_INTAKE / (2.0 * math.pi)) * math.sqrt(a_runner / (length * v_eff))
    n_tuned = 60.0 * f_h / TUNE_DIV     # Engelman: primary tuned engine speed
    return a_piston, a_valve, stroke, n_tuned, cyl.compression_ratio


# cam profile -> (top-end knee stretch, low-rpm overlap penalty, penalty fade rpm)
_CAM = {"mild": (0.97, 0.00, 1.0), "stock": (1.00, 0.00, 1.0),
        "hot": (1.08, 0.10, 3200.0), "race": (1.16, 0.18, 3900.0)}

# VTEC/AVS two-stage lobes: a LOW-rpm economy lobe (breathes well low, rolls off
# HARD up top so keeping it past the crossover would strangle the engine) and a
# HIGH-rpm power lobe (screams up top, would lope down low).  The step between them
# at the switch IS the VTEC kick.
_VTEC_LO = (0.78, 0.00, 1.0)
_VTEC_HI = (1.20, 0.16, 4200.0)


def _cam_params(eng, rpm):
    """Cam (knee, low-rpm penalty, penalty-fade rpm) — RPM-VARIABLE for variable
    valve timing, so the breathing/VE reflects the real mechanism:

      * two-stage (VTEC/AVS): SWITCH from the low lobe to the high lobe at the
        crossover rpm -> a genuine VE/torque STEP (the 'VTEC kick'), the engine
        taking whichever lobe breathes better at each speed.
      * continuous (VANOS/VVT-i/Valvetronic): phase the cam to keep the high-rpm
        breathing WITHOUT the low-rpm overlap lope -> a broad, flexible curve.
      * fixed: the static cam_profile as before.
    """
    lift = getattr(eng, "valve_lift", "fixed")
    if lift == "two-stage":
        xr = getattr(eng, "vtec_rpm", 0.0) or 0.62 * eng.redline_rpm
        w = 1.0 / (1.0 + math.exp(-(rpm - xr) / max(0.013 * eng.redline_rpm, 45.0)))
        return tuple(lo + (hi - lo) * w for lo, hi in zip(_VTEC_LO, _VTEC_HI))
    if lift == "continuous":
        knee, _, _ = _CAM.get(getattr(eng, "cam_profile", "stock"), _CAM["stock"])
        return (max(knee, _CAM["hot"][0]), 0.0, 1.0)   # top-end breathing, no lope
    return _CAM.get(getattr(eng, "cam_profile", "stock"), _CAM["stock"])


def ve_truth(eng, rpm, mapf):
    """Volumetric efficiency (unnormalised) at ``rpm`` and manifold-pressure
    fraction ``mapf`` (p_man / p_atm; > 1 under boost)."""
    a_p, a_v, stroke, n_tuned, cr = _geometry(eng)
    rpm = max(rpm, 1.0)
    cam_knee, cam_pen, cam_fade = _cam_params(eng, rpm)

    # INDIVIDUAL THROTTLE BODIES: one throttle per cylinder on a short dedicated
    # runner removes the shared-plenum throttling/robbing loss, so the top-end
    # breathing knee extends — an ITB engine keeps filling up top where a single-
    # plenum one is already choking.
    if getattr(eng, "individual_throttle", False):
        cam_knee *= 1.05

    # 1) Taylor Mach-index roll-off (top-end breathing limit)
    sp = 2.0 * stroke * rpm / 60.0                    # mean piston speed
    z = (a_p * sp) / (a_v * C_INTAKE)
    roll = 1.0 / (1.0 + (z / (ROLL_KNEE * cam_knee)) ** ROLL_EXP)

    # 2) Helmholtz ram bumps (primary + the octave-down echo)
    ram = min(max(mapf, 0.25), 1.2) ** 0.5            # ram needs mass flow
    b1 = math.exp(-((rpm - n_tuned) / (0.30 * n_tuned)) ** 2)
    b2 = math.exp(-((rpm - 0.5 * n_tuned) / (0.25 * n_tuned)) ** 2)
    bump = 1.0 + ram * (0.13 * b1 + 0.06 * b2)

    # 3) residual-gas backflow: burned gas is trapped when the exhaust back-pressure
    #    exceeds the manifold pressure.  A TURBINE dams the exhaust, so a turbo's
    #    exhaust-manifold back-pressure RISES with boost (the turbine needs a
    #    pressure ratio ~ tracking the compressor to spin) -> more trapped residual,
    #    worse scavenging at overlap, and the real VE penalty a turbo pays vs an NA
    #    or belt-supercharged engine at the SAME manifold pressure.
    p_exh = 1.08
    if getattr(eng, "induction", "na") == "turbo":
        bp = min(max(getattr(eng, "backpressure_coupling", 0.55), 0.0), 1.2)
        # turbine back-pressure grows with exhaust MASS FLOW (~ rpm x charge): so it
        # bites hardest OFF-boost / high-rpm-low-load (the trapped-residual, laggy-
        # breathing penalty), while at WOT boost the manifold pressure overcomes it
        # and scavenging stays good — exactly the real turbo behaviour.
        rf = min(rpm / max(eng.redline_rpm, 1.0), 1.0)
        p_exh = 1.08 + bp * rf * max(mapf, 0.30)
    pr = p_exh / max(mapf, 0.15)                      # exhaust/manifold pressure
    x_r = pr ** (1.0 / GAMMA_EX) / cr
    x_r0 = 1.08 ** (1.0 / GAMMA_EX) / cr              # residual at NA full throttle
    res = 1.0 - 1.4 * max(x_r - x_r0, 0.0)

    # cam overlap penalty at low rpm (the hot-cam idle lope, thermo side)
    pen = 1.0 - cam_pen * max(0.0, 1.0 - rpm / cam_fade)

    return max(roll * bump * res * pen, 0.10)


def build_ve_table(eng, n_rpm=22, n_map=12):
    """Bake VE(rpm, map) for one engine into a LUT, peak-anchored to the
    preset's old ``ve_max`` so per-car torque calibration is preserved."""
    top = eng.redline_rpm + 600.0
    rpm_grid = np.linspace(300.0, top, n_rpm)
    map_hi = max(1.05, 1.0 + getattr(eng, "boost_bar", 0.0) + 0.15)
    map_grid = np.linspace(0.12, map_hi, n_map)

    values = np.empty((n_rpm, n_map), dtype=np.float64)
    for i, r in enumerate(rpm_grid):
        for j, m in enumerate(map_grid):
            values[i, j] = ve_truth(eng, float(r), float(m))

    # normalise: best naturally-aspirated point (map <= ~1) -> old ve_max
    na_cols = map_grid <= 1.051
    peak = float(values[:, na_cols].max()) if na_cols.any() else float(values.max())
    values *= eng.ve_max / max(peak, 1e-6)
    np.clip(values, 0.08, 1.40, out=values)
    return LUT([("rpm", rpm_grid), ("map", map_grid)], values)
