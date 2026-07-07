"""
White-box mean-torque (BMEP) and steady-boost targets — the plan's Phase 2.

Replaces the last three torque fudges (hand-tuned ``heat_release_k``, per-car
``torque_scale``, the ``P_PEAK_CAP`` band-aid) with energy accounting:

TORQUE.  The heat released per cycle is AIR-LIMITED: every kg of trapped air
can burn fuel worth LHV/AFR_stoich ~ 2.99 MJ (running rich adds no heat, the
excess fuel leaves unburned).  So

    IMEP_gross = eta_otto(CR) * ETA_SHAPE * (LHV/AFR_st) * rho_man * VE

with eta_otto = 1 - CR^(1-gamma) the ideal-cycle efficiency and ETA_SHAPE one
GLOBAL constant (~0.8) covering finite burn / wall heat / blowdown loss — one
universal shape factor instead of 130 per-car fudges.  Pumping loss
PMEP = p_atm - p_man on the throttled intake loop.  Sanity: this lands the
Aventador at ~13 bar BMEP and the 488 at ~24 bar with the SAME constants —
the physics closes without per-car tuning.

The runtime keeps the crank-resolved Wiebe pulse machinery (audio, limiter,
ripple all live there); this module only decides HOW MUCH heat each cycle
releases, by solving the burn multiplier k so that the pulse model's
cycle-mean torque equals the physical target (see Simulator._calibrate_burn).

BOOST.  Steady boost comes from the turbine/compressor energy balance: boost
rises with exhaust enthalpy flow (~ rpm * charge density * VE) until the
wastegate cap.  The balance is solved by fixed-point iteration and anchored
through the preset's existing full-spool point, so each car's turbo SIZING is
preserved while the onset SHAPE becomes physical (small engines with big
turbos knee late, big engines spool off idle).  Transient lag stays a live
first-order ODE in the simulator reading this table as its target.
"""

from __future__ import annotations

import numpy as np

from .surrogate import LUT

LHV = 44.0e6            # gasoline lower heating value, J/kg
AFR_ST = 14.7           # stoichiometric AFR (air-limited heat: LHV/AFR_st per kg air)
AFR_DIESEL = 25.0       # diesel runs lean at rated load (lambda ~ 1.7)
R_AIR = 287.0
GAMMA_CYC = 1.30        # matches the in-cylinder model
ETA_SHAPE = 0.68        # ONE global real-cycle factor: finite burn + wall heat +
                        # blowdown + valve-timing losses vs the ideal Otto cycle.
                        # Calibrated ONCE against the 12-car REF set (mean -> 1.0);
                        # never per-car.
KNOCK_DERATE = 0.08     # boosted SI runs knock-limited retard + enrichment:
                        # efficiency falls ~8%/bar of design boost (one global law)
P_ATM = 101325.0


def _is_diesel(eng):
    return eng.cylinders[0].compression_ratio >= 14.5


def torque_target(eng, rpm, mapf, ve):
    """Physical cycle-mean crank torque (N*m) at manifold fraction ``mapf``
    (p_man/p_atm) with volumetric efficiency ``ve`` (from the P1 white-box
    table).  Friction is NOT included — the sim subtracts its own loss model."""
    cr = eng.cylinders[0].compression_ratio
    eta_cyc = 1.0 - cr ** (1.0 - GAMMA_CYC)
    diesel = _is_diesel(eng)
    q_per_air = LHV / (AFR_DIESEL if diesel else AFR_ST)
    # boosted SI is knock-limited: spark retard + enrichment shave efficiency
    eta_k = 1.0 if diesel else 1.0 - KNOCK_DERATE * min(
        getattr(eng, "boost_bar", 0.0), 2.5)
    t_man = 300.0 + 30.0 * max(mapf - 1.0, 0.0)      # intercooled charge heats a bit
    rho = mapf * P_ATM / (R_AIR * t_man)
    imep = eta_cyc * ETA_SHAPE * eta_k * q_per_air * rho * max(ve, 0.0)
    pmep = max(P_ATM - mapf * P_ATM, 0.0)            # throttled intake pumping loop
    vd = eng.total_displacement
    # NO floor: at closed throttle the pumping loop DOMINATES and the net is
    # negative — that IS gas-exchange engine braking; clamping it to zero made
    # revs hang on the overrun (the k-solver would keep the engine net-positive).
    return (imep - pmep) * vd / (4.0 * np.pi)


def build_boost_table(eng, ve_lut, n_rpm=20, n_thr=7):
    """Steady-state boost target table boost[rpm][throttle] (bar gauge) from the
    turbine/compressor energy balance, anchored at the preset's full-spool
    point so per-car turbo sizing survives while the shape becomes physical."""
    redline = eng.redline_rpm
    b_max = eng.boost_bar

    def flow(rpm, boost):
        """Exhaust enthalpy-flow proxy: rpm x charge density x VE."""
        mapf = 1.0 + boost
        ve = ve_lut.eval2(rpm, mapf) if ve_lut is not None else 0.9
        return (rpm / redline) * mapf * ve

    # anchor: at the preset's historical full-boost speed (WOT) the balance
    # must just reach the wastegate cap
    rpm_full = min(max(eng.turbo_spool_frac + eng.turbo_spool_width, 0.2), 0.95) \
        * redline
    kappa = b_max / max(flow(rpm_full, b_max), 1e-9)

    rpm_grid = np.linspace(300.0, redline + 600.0, n_rpm)
    thr_grid = np.linspace(0.0, 1.0, n_thr)
    values = np.empty((n_rpm, n_thr), dtype=np.float64)
    for i, r in enumerate(rpm_grid):
        for j, t in enumerate(thr_grid):
            b = 0.0
            for _ in range(8):                        # fixed point converges fast
                b = min(b_max, kappa * t * flow(float(r), b))
            values[i, j] = b
    return LUT([("rpm", rpm_grid), ("thr", thr_grid)], values)
