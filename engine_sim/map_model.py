"""
White-box manifold-pressure (MAP) model — replaces the hand-tuned closed-throttle
formula (`closed_map_fraction * (idle_rpm/rpm)^p`) that Leo rightly flagged as a
fudge.

MAP is set by a STEADY-STATE MASS BALANCE across the intake manifold: the air the
throttle plate lets IN must equal the air the cylinders pump OUT.

  * IN  — compressible flow through the throttle orifice (upstream = atmosphere,
    downstream = MAP).  Isentropic subsonic/choked law, the SAME physics
    gas_truth uses, in closed form:
        n_in  ∝  A_eff(throttle) · Ψ(MAP/P_atm)
    where Ψ falls to 0 as MAP → P_atm (no pressure drop, no flow) and saturates
    at the choked value below the critical ratio 0.528.

  * OUT — the cylinders acting as a pump: each intake stroke draws a cylinder
    volume of charge at manifold density, so
        n_out ∝  rpm · VE · (MAP/P_atm)

Balancing IN = OUT and solving for MAP gives, with NO tuned exponent:
  - wide-open throttle → MAP just below atmospheric (only the pumping loss),
  - closed throttle    → deep vacuum,
  - higher rpm at fixed throttle → DEEPER vacuum (the pump outruns the fixed
    orifice) — the real "lift-off vacuum grows with revs" behaviour that the old
    formula faked with an exponent.

The only calibration is ONE global orifice/pump ratio, sized from the engine's
own airflow demand (∝ displacement · redline), so it is displacement-independent
and never per-car.  Boost is added on top by the caller (compressor raises the
plenum above atmosphere); this model is the NA breathing balance.
"""

from __future__ import annotations

import math

P_ATM = 101325.0
GAMMA = 1.4
CRIT = (2.0 / (GAMMA + 1.0)) ** (GAMMA / (GAMMA - 1.0))   # 0.528 choke ratio
# choked value of the flow function Ψ (its maximum)
_PSI_CHOKE = math.sqrt(GAMMA) * (2.0 / (GAMMA + 1.0)) ** (
    (GAMMA + 1.0) / (2.0 * (GAMMA - 1.0)))

# ONE global orifice/pump ratio, calibrated so a wide-open throttle sits at
# ~0.9-0.95 atm across the rev range and a shut throttle idles in real vacuum.
K_BALANCE = 0.55


def _psi(pr):
    """Isentropic orifice flow function at pressure ratio pr = P_down/P_up."""
    if pr <= CRIT:
        return _PSI_CHOKE                       # choked: constant max flow
    if pr >= 1.0:
        return 0.0                              # no pressure drop, no flow
    # subsonic: sqrt( (2γ/(γ-1)) (pr^(2/γ) - pr^((γ+1)/γ)) )
    a = pr ** (2.0 / GAMMA)
    b = pr ** ((GAMMA + 1.0) / GAMMA)
    return math.sqrt(max((2.0 * GAMMA) / (GAMMA - 1.0) * (a - b), 0.0))


def throttle_area(throttle, idle_area, wot_area=1.0):
    """Effective throttle-plate open-area fraction (0..1 of the fleet tract).
    A butterfly plate's projected opening ≈ 1 - cos(angle); the idle bleed
    sets the floor; wide open it is the tract's ``wot_area`` (1 = the fleet's
    lumped road intake, > 1 a race ITB tract -- wot_area_for)."""
    t = min(max(throttle, 0.0), 1.0)
    plate = 1.0 - math.cos(0.5 * math.pi * t)   # 0 shut → 1 wide open, convex
    return idle_area + (wot_area - idle_area) * plate


# K_BALANCE in metres: the balance a·Ψ(pr) = K·(rpm/redline)·VE·pr is the
# isentropic orifice  m = Cd·A·p0/sqrt(R·T0)·Ψ  against the pump
# m = (pr·p0/(R·T0))·VE·Vd·rpm/120, so a = 1 is an effective open area
#   Cd·A_nom = Vd·redline / (120·sqrt(R·T0)·K_BALANCE)
# (a 2.0 L 7000 rpm car: 7.3 cm^2, a 30 mm hole -- filter, snorkel and plate
# together, sized so the fleet sits ~0.90 atm at redline).
_R_AIR, _T_AMB = 287.05, 293.15
_CD_BELL = 0.97        # radiused bellmouth discharge coefficient (ISO 5167 nozzle 0.96-0.99)


def wot_area_for(eng):
    """Wide-open tract area in fleet units.  A race ITB tract (F1): each
    cylinder's trumpet opens straight into a ram airbox, so the open area is
    n·π/4·d², far above the demand -- the manifold stays at the airbox's
    pressure and only the valves (in VE) restrict.  Else 1 (fleet)."""
    d = getattr(eng, "throttle_bore_mm", 0.0) or 0.0
    if d <= 0.0:
        return 1.0
    vd = eng.total_displacement
    a_nom = vd * eng.redline_rpm / (120.0 * math.sqrt(_R_AIR * _T_AMB) * K_BALANCE)
    a_t = eng.num_cylinders * 0.25 * math.pi * (d * 1.0e-3) ** 2 * _CD_BELL
    return max(a_t / max(a_nom, 1e-9), 1.0)


def solve_map_fraction(throttle, rpm, redline, ve, idle_area, warm=0.5,
                       wot_area=1.0):
    """Solve MAP/P_atm from the throttle-orifice ↔ cylinder-pump balance.

    n_in(pr) = A_eff · Ψ(pr)            (falls as pr → 1)
    n_out(pr) = k_pump · (rpm/redline) · VE · pr   (rises with pr)
    balance where they cross; bisection is robust across the choked kink.
    """
    a_eff = throttle_area(throttle, idle_area, wot_area)
    # pump demand grows with rpm and breathing; K_BALANCE fixes the operating
    # point.  rpm normalised by redline keeps the ratio displacement-free.
    pump = K_BALANCE * max(rpm, 1.0) / max(redline, 1.0) * max(ve, 0.05)

    def imbalance(pr):
        return a_eff * _psi(pr) - pump * pr     # >0: inflow wins → pr rises

    lo, hi = 0.02, 1.0
    # inflow always wins at very low pr (Ψ maxed, pr→0), pump wins at pr=1
    if imbalance(hi) >= 0.0:
        return 1.0                              # throttle can supply full MAP
    for _ in range(14):                         # bisection → ~6e-5 precision
        mid = 0.5 * (lo + hi)
        if imbalance(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


VE_NOM = 0.85          # nominal VE used in the (one-way) MAP balance


def idle_area_for(eng):
    """Throttle-plate bleed-area fraction at the idle stop, ANCHORED so the model
    reproduces the preset's existing ``closed_map_fraction`` at idle.

    At a shut pedal the effective area IS the idle area, so the balance
    ``idle_area·Ψ(cmf) = pump·cmf`` inverts in closed form — no tuning, and the
    known-good idle vacuum is preserved exactly while the part-throttle / rpm
    behaviour becomes white-box."""
    cmf = min(max(getattr(eng, "closed_map_fraction", 0.25), 0.05), 0.6)
    pump = K_BALANCE * (eng.idle_rpm / max(eng.redline_rpm, 1.0)) * VE_NOM
    area = pump * cmf / max(_psi(cmf), 1e-6)
    return min(max(area, 0.002), 0.15)
