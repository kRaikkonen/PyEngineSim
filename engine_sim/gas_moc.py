"""
gas_moc — 1D unsteady compressible flow in the exhaust by the METHOD OF
CHARACTERISTICS (Tier B / Phase B3, Leo's "路线二").

This is the real gas-dynamic exhaust that gas_truth's single lumped runner +
one-reflection wave-action term only approximated: each primary runner is a 1-D
duct in which pressure waves actually PROPAGATE and REFLECT, and N runners meet
at a shared collector, so the multi-cylinder pressure-wave INTERFERENCE (firing
order, runner length, 4-2-1 vs 4-1, cross- vs flat-plane collector beat) — the
"多缸支管压力波干涉叠加 = 声浪频谱本源" — falls out of the physics instead of being
reconstructed downstream.

Method (Benson/Blair non-dimensional homentropic MOC):
  * Non-dimensionalise by a reference sound speed a_ref: AA = a/a_ref, U = u/a_ref.
  * Riemann invariants along the two Mach characteristics
        lam_p = U + 2 AA/(g-1)   constant along C+  (dx/dt = u + a),
        lam_m = U - 2 AA/(g-1)   constant along C-  (dx/dt = u - a),
    recovered by  U = (lam_p+lam_m)/2,  AA = (lam_p-lam_m)(g-1)/4.
  * Mesh method: each step, trace each characteristic back to the previous time
    level and LINEARLY interpolate its invariant (first order, CFL-stable).
  * Boundaries: a prescribed-velocity VALVE end (driven by the cylinder blowdown)
    and a constant-pressure COLLECTOR end (a shared lumped node the N runners and
    the tailpipe all exchange with — the coupling that makes them interfere).

OFFLINE only (seconds/point) — its collector-pressure trace bakes the Tier-B
excitation LUT, exactly like gas_truth's.  Pure Python + numpy, no scipy.
"""

from __future__ import annotations

import math
import numpy as np

GAMMA = 1.33                       # hot exhaust gas
GM1 = GAMMA - 1.0
ATM = 101325.0
R_GAS = 287.0                      # J/kg K (air-ish)


class MOCDuct:
    """One 1-D duct solved by the mesh method of characteristics.

    State is held as the two Riemann invariants (lam_p, lam_m) at M mesh points.
    Length L (m), area A_duct (m^2), a_ref (m/s) reference sound speed.
    """

    def __init__(self, length, area, a_ref, M=24, p0=ATM, T0=900.0):
        self.L = float(length)
        self.area = float(area)
        self.a_ref = float(a_ref)
        self.M = int(M)
        self.dx = self.L / (self.M - 1)
        self.x = np.linspace(0.0, self.L, self.M)
        # initial quiescent gas at (p0, T0): u=0, a=sqrt(g R T0)
        a0 = math.sqrt(GAMMA * R_GAS * T0)
        AA0 = a0 / self.a_ref
        self.lam_p = np.full(self.M, 0.0 + 2.0 * AA0 / GM1)
        self.lam_m = np.full(self.M, 0.0 - 2.0 * AA0 / GM1)

    # --- state accessors (non-dimensional) ------------------------------------
    def U(self):
        return 0.5 * (self.lam_p + self.lam_m)

    def AA(self):
        return 0.25 * GM1 * (self.lam_p - self.lam_m)

    def u(self):
        return self.U() * self.a_ref

    def a(self):
        return self.AA() * self.a_ref

    def pressure(self, p_ref, a_ref_gas):
        """Static pressure from the isentropic relation p/p_ref = (a/a_ref_gas)^(2g/(g-1))
        referenced to the duct's own reference stagnation state (p_ref, a_ref_gas)."""
        return p_ref * (self.a() / a_ref_gas) ** (2.0 * GAMMA / GM1)

    def max_char_speed(self):
        U = self.U()
        AA = self.AA()
        return self.a_ref * float(np.max(np.abs(U) + AA))

    # --- one MOC time step ----------------------------------------------------
    def step(self, dt, bc_valve, bc_coll):
        """Advance dt.  bc_valve(lam_m_in) -> (lam_p, lam_m) at x=0 (valve end);
        bc_coll(lam_p_in) -> (lam_p, lam_m) at x=L (collector end).  Interior points
        by characteristic trace-back + linear interpolation."""
        a_ref = self.a_ref
        dx = self.dx
        x = self.x
        lp, lm = self.lam_p, self.lam_m
        U = 0.5 * (lp + lm)
        AA = 0.25 * GM1 * (lp - lm)
        sp = (U + AA) * a_ref                 # C+ speed (m/s), rightward
        sm = (U - AA) * a_ref                 # C- speed (m/s), leftward
        # foot of each characteristic at the previous time level
        xp_foot = x - sp * dt                  # C+ comes from the left
        xm_foot = x - sm * dt                  # C- comes from the right
        new_lp = np.interp(xp_foot, x, lp)     # invariant carried along C+
        new_lm = np.interp(xm_foot, x, lm)     # invariant carried along C-
        # --- boundaries (one invariant is interpolated from inside, the BC sets
        #     the other).  At x=0 the C- (new_lm[0]) came from inside; at x=L the
        #     C+ (new_lp[-1]) came from inside.
        lp0, lm0 = bc_valve(new_lm[0])
        lpL, lmL = bc_coll(new_lp[-1])
        new_lp[0], new_lm[0] = lp0, lm0
        new_lp[-1], new_lm[-1] = lpL, lmL
        self.lam_p, self.lam_m = new_lp, new_lm


def _closed_end_valve(AA_ref_dummy):
    """Reflecting closed end at x=0: u=0 -> lam_p = lam_m + ... no: U=0 means
    lam_p = -lam_m? U=(lp+lm)/2=0 -> lp=-lm; and the incoming C- gives lm.
    So lp = -lm (full reflection, doubles pressure)."""
    def bc(lam_m_in):
        return (-lam_m_in, lam_m_in)
    return bc


def _closed_end_coll():
    """Reflecting closed end at x=L: U=0 -> lm = -lp; incoming C+ gives lp."""
    def bc(lam_p_in):
        return (lam_p_in, -lam_p_in)
    return bc


# --- physical boundary conditions -----------------------------------------------
# Valve end (x=0): PRESCRIBE the velocity U_valve (from the cylinder blowdown).
# With the incoming C- (lm) known: AA=(U-lm)(g-1)/2, lp = 2U - lm.  U=0 -> closed.
def valve_bc(U_valve):
    def bc(lam_m_in):
        return (2.0 * U_valve - lam_m_in, lam_m_in)
    return bc


# Constant-pressure end: the duct-end static a equals the reservoir a (=> pressure
# continuity), incoming characteristic sets the velocity.  Used at BOTH the
# collector end of every runner (reservoir = the shared collector node) and the
# open (atmospheric) end of the tailpipe.
def pressure_end_coll(AA_res):
    """At x=L (collector end): incoming C+ (lp) known; AA fixed by reservoir."""
    twoAA = 2.0 * AA_res / GM1
    def bc(lam_p_in):
        U = lam_p_in - twoAA
        return (lam_p_in, U - twoAA)          # lm = lp - 4AA/(g-1)
    return bc


def pressure_end_valve(AA_res):
    """At x=0 (a reservoir on the LEFT, e.g. tailpipe near-collector end): incoming
    C- (lm) known; AA fixed by reservoir."""
    twoAA = 2.0 * AA_res / GM1
    def bc(lam_m_in):
        U = lam_m_in + twoAA
        return (U + twoAA, lam_m_in)          # lp = lm + 4AA/(g-1)
    return bc


def _AA_of_p(p, p_ref, AA_ref):
    """Non-dim sound speed at static pressure p (isentropic from the ref state)."""
    return AA_ref * (max(p, 1.0) / p_ref) ** (GM1 / (2.0 * GAMMA))


class MultiCylExhaust:
    """N primary runners + a tailpipe, all coupled at a shared COLLECTOR node,
    solved by MOC.  The runners are driven at their valve ends by the (phase-
    shifted) cylinder blowdown; the shared collector is what makes them interfere.
    Records the collector pressure over one cycle -> the Tier-B excitation source
    with REAL multi-cylinder wave interference.
    """

    def __init__(self, ncyl, offsets_deg, prim_len, prim_area, tail_len, tail_area,
                 col_vol, rpm, blowdown_shape, a_ref, T_exh=950.0, M=20):
        self.ncyl = ncyl
        self.offsets = np.asarray(offsets_deg, dtype=np.float64) % 720.0
        self.rpm = rpm
        self.a_ref = a_ref
        self.blowdown = np.asarray(blowdown_shape, dtype=np.float64)  # len Nb, over 720
        self.nb = len(self.blowdown)
        self.prim_area = prim_area
        self.tail_area = tail_area
        self.col_vol = col_vol
        self.p_col = ATM
        self.a_gas = math.sqrt(GAMMA * R_GAS * T_exh)      # reference stagnation a
        self.AA_atm = _AA_of_p(ATM, ATM, self.a_gas / a_ref)
        # a runner per cylinder + one tailpipe
        self.runners = [MOCDuct(prim_len, prim_area, a_ref, M=M, T0=T_exh)
                        for _ in range(ncyl)]
        self.tail = MOCDuct(tail_len, tail_area, a_ref, M=M, T0=T_exh)
        # peak blowdown velocity ~ high-subsonic choked jet
        self.u_peak = 0.55 * self.a_gas

    def _valve_U(self, theta, j):
        """Non-dim valve velocity for runner j at crank theta (deg)."""
        ph = (theta - self.offsets[j]) % 720.0
        idx = ph / 720.0 * self.nb
        i0 = int(idx) % self.nb
        i1 = (i0 + 1) % self.nb
        frac = idx - int(idx)
        s = self.blowdown[i0] + (self.blowdown[i1] - self.blowdown[i0]) * frac
        return max(s, 0.0) * self.u_peak / self.a_ref

    def run(self, cycles=4, rec_cycle=True):
        omega = self.rpm * 2.0 * math.pi / 60.0
        deg_per_s = self.rpm * 360.0 / 60.0
        # CFL dt from the fastest characteristic across all ducts
        vmax = max([d.max_char_speed() for d in self.runners] + [self.tail.max_char_speed()])
        dt = 0.4 * self.runners[0].dx / max(vmax, 1.0)
        steps_per_cycle = int(720.0 / (deg_per_s * dt))
        rec = []
        theta = 0.0
        AA_col = _AA_of_p(self.p_col, ATM, self.AA_atm)
        for c in range(cycles):
            cyc_rec = []
            for _ in range(steps_per_cycle):
                AA_col = _AA_of_p(self.p_col, ATM, self.AA_atm)
                Vdot_net = 0.0
                # advance each runner: valve BC (blowdown) + collector-pressure BC
                for j, d in enumerate(self.runners):
                    Uv = self._valve_U(theta, j)
                    d.step(dt, valve_bc(Uv), pressure_end_coll(AA_col))
                    # runner outflow into the collector = U_end * area (into node)
                    Vdot_net += d.U()[-1] * self.a_ref * self.prim_area
                # tailpipe: near end = collector reservoir, far end = atmosphere
                self.tail.step(dt, pressure_end_valve(AA_col),
                               pressure_end_coll(self.AA_atm))
                Vdot_net -= self.tail.U()[0] * self.a_ref * self.tail_area  # into tail
                # lumped collector: dp = (g p / V) * net volume inflow * dt
                self.p_col += (GAMMA * self.p_col / self.col_vol) * Vdot_net * dt
                self.p_col = min(max(self.p_col, 0.3 * ATM), 6.0 * ATM)
                if rec_cycle and c == cycles - 1:
                    cyc_rec.append(self.p_col - ATM)
                theta = (theta + deg_per_s * dt) % 720.0
            if rec_cycle and c == cycles - 1:
                rec = cyc_rec
        return np.asarray(rec)


def moc_collector_pulse(eng, rf, N=128, cycles=4):
    """Tier B / B3: bake the REAL multi-cylinder collector-pressure pulse from the
    MOC exhaust for one operating point, resampled to N points (peak-normalised).
    The blowdown that drives each runner is the validated gas_truth flow pulse;
    the MOC adds the runner wave propagation + shared-collector interference.

    Returns a list of N floats (collector p - ATM over 720 deg), or None on failure.
    """
    try:
        from .gas_truth import exhaust_pressure_pulse
        blow = np.asarray(exhaust_pressure_pulse(eng, rf, N=180))
    except Exception:
        return None
    ncyl = eng.num_cylinders
    offsets = [c.cycle_offset_deg for c in eng.cylinders]
    rpm = max(rf * eng.redline_rpm, eng.idle_rpm)
    # geometry from the preset's real exhaust dimensions
    bore = eng.cylinders[0].bore
    d_ev = 0.83 * bore * (0.39 if getattr(eng, "valves_per_cyl", 4) >= 4 else 0.47)
    prim_bore = getattr(eng, "exhaust_primary_bore_m", 0.0) or (1.15 * d_ev)
    prim_area = math.pi * 0.25 * prim_bore * prim_bore
    prim_len = max(getattr(eng, "exhaust_primary_m", 0.4), 0.15)
    r_col = max(getattr(eng, "exhaust_radius_m", 0.03), 0.012)
    tail_area = math.pi * r_col * r_col
    tail_len = max(getattr(eng, "exhaust_total_m", 1.6) - prim_len, 0.3)
    col_vol = max(getattr(eng, "muffler_volume_m3", 0.003) * 0.3, 3e-4)
    T_exh = 850.0 + 350.0 * min(rf, 1.0)                 # hotter up top
    a_gas = math.sqrt(GAMMA * R_GAS * T_exh)
    ex = MultiCylExhaust(ncyl, offsets, prim_len, prim_area, tail_len, tail_area,
                         col_vol, rpm, blow, a_gas, T_exh=T_exh)
    p = ex.run(cycles=cycles)
    if len(p) == 0 or not np.all(np.isfinite(p)):
        return None
    # resample to N, peak-normalise
    n = len(p)
    out = np.interp(np.linspace(0, n - 1, N), np.arange(n), p)
    peak = float(np.max(np.abs(out))) or 1.0
    return list(out / peak)
