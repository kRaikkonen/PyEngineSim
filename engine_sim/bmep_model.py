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
T_AMB = 300.0           # ambient charge-inlet temperature (K)
GAMMA_AIR = 1.40        # cold-air ratio for the compression heat-of-compression
ETA_COMP = 0.70         # compressor isentropic efficiency (real wheel runs hotter)
GAMMA_CYC = 1.30        # matches the in-cylinder model
ETA_SHAPE = 0.82        # ONE global real-cycle factor: gross indicated work vs the
                        # ideal Otto cycle (finite burn + wall heat + blowdown +
                        # valve-timing losses, offset by WOT power-enrichment).  The
                        # real gross-indicated / ideal-Otto ratio is ~0.80-0.85 for a
                        # performance engine at WOT; calibrated ONCE so the acceptance
                        # reference (Aventador) makes its rated ~700 hp / ~690 Nm and
                        # the docstring-spec fleet centres on 1.0.  NEVER per-car.
KNOCK_DERATE = 0.24     # SI knock-limited retard + enrichment per unit of the
                        # cycle KNOCK INDEX (charge-temp x compression); anchored so
                        # a typical intercooled 1-bar turbo lands ~the old 8%/bar
P_ATM = 101325.0


def _is_diesel(eng):
    return eng.cylinders[0].compression_ratio >= 14.5


def charge_temp(eng, mapf, ic_soak=0.0):
    """WHITE-BOX charge-air temperature (K) entering the cylinder — the SINGLE source
    of truth shared by the torque model AND the exhaust-gas-temp / knock models so
    they always MATCH.  Compressor heat of compression (isentropic / eta_comp) then
    an intercooler whose effectiveness falls with mass flow (less residence) and with
    HEAT-SOAK (a warmed core cools less)."""
    PR = max(mapf, 1.0)                               # compressor pressure ratio
    t2 = T_AMB * (1.0 + (PR ** ((GAMMA_AIR - 1.0) / GAMMA_AIR) - 1.0) / ETA_COMP)
    eps = min(max(getattr(eng, "intercooler_eff", 0.7), 0.0), 0.95)
    eps *= 1.0 - 0.22 * min(max(mapf - 1.0, 0.0), 1.6)   # flow-dependent
    eps *= 1.0 - 0.12 * min(max(ic_soak, 0.0), 1.0)      # heat-soak
    return t2 - eps * (t2 - T_AMB)                    # NA (mapf<=1) stays ambient


def torque_target(eng, rpm, mapf, ve):
    """Physical cycle-mean crank torque (N*m) at manifold fraction ``mapf``
    (p_man/p_atm) with volumetric efficiency ``ve`` (from the P1 white-box
    table).  Friction is NOT included — the sim subtracts its own loss model."""
    cr = eng.cylinders[0].compression_ratio
    eta_cyc = 1.0 - cr ** (1.0 - GAMMA_CYC)
    diesel = _is_diesel(eng)
    q_per_air = LHV / (AFR_DIESEL if diesel else AFR_ST)
    # CHARGE-AIR TEMPERATURE (white-box hot-vs-cold): the compressor heats the
    # intake by the real heat of compression — isentropic to (P2/P1)^((g-1)/g),
    # divided by the compressor efficiency (a real ~70% wheel runs HOTTER) — then
    # an intercooler pulls a fraction (effectiveness) back out.  Density
    # rho = P/(R·T) is what makes torque, so a hot un-intercooled charge makes less
    # power than its boost pressure implies.  NA (mapf<=1) stays at ambient.
    # Computed FIRST because both the density AND the knock tendency depend on it.
    # (Shared charge_temp() — the same formula exhaust_gas_temp / knock read.)
    t_man = charge_temp(eng, mapf)
    rho = mapf * P_ATM / (R_AIR * t_man)
    # KNOCK (white-box, COUPLED to the real cycle — was boost alone): the end-gas
    # auto-ignites from the peak COMPRESSION STATE, so the knock index ~
    # (T_charge/T_amb)·(CR·MAP)^(g-1) — a HOT (un-intercooled) charge, a HIGH CR, or
    # high boost ALL raise it; a good intercooler or a low CR lower it.  So a high-CR
    # NA can knock with no boost, and an intercooled turbo knocks LESS than a hot-
    # charge one at the same boost.  Diesel is compression-ignition -> no derate.
    ki = (t_man / T_AMB) * (cr * min(mapf, cr) / 10.0) ** (GAMMA_CYC - 1.0)
    eta_k = 1.0 if diesel else 1.0 - KNOCK_DERATE * min(max(ki - 1.0, 0.0), 2.2)
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
        """Exhaust ENTHALPY flow that drives the turbine = mass flow x TEMPERATURE.
        mass ~ rpm x charge density (mapf) x VE; the exhaust-gas TEMP rises with
        load/boost (more fuel burned -> hotter exhaust) -> the turbine gets hotter
        AS it spools, a positive feedback that makes boost come on HARD once lit
        (then the wastegate caps it).  Couples the turbine hot-side to combustion."""
        mapf = 1.0 + boost
        ve = ve_lut.eval2(rpm, mapf) if ve_lut is not None else 0.9
        # exhaust-gas TEMPERATURE using the SAME combustion-load form as
        # Simulator.exhaust_gas_temp (0.42 + 0.58·load) so the turbine hot-side and
        # the note-pitch EGT are consistent; the CR/expansion factor is per-engine
        # constant and normalised out by kappa, so only the LOAD shape matters here.
        load = min(boost / max(b_max, 0.3), 1.0)
        egt = 0.42 + 0.58 * load
        return (rpm / redline) * mapf * ve * egt

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
