"""Turbocharger physics -- white-box (docs/ENGINESIM_PLAN.md s.10).

One charge-air system: one or more turbochargers (single / parallel twin /
quad / sequential twin) feeding ONE plenum -- the charge pipes and the
intercooler up to the throttle -- each unit a compressor and a turbine on one
shaft, the boost held by a wastegate, and a blow-off valve on the plenum
(recirculating, atmospheric, or none: then the compressor SURGES -- the
'stu-tu-tu').

  compressor  non-dimensional speed lines.  The work is Euler's, with slip
              (Wiesner) and backsweep; the head at the zero-slope point and at
              peak efficiency follow from it and an efficiency island; the line
              falls to choke at the inducer as Leufven & Eriksson's 'ellipse'
              (fitted to a large database of automotive maps).  Left of the
              zero-slope point: their unstable branch down to the zero-flow
              pressure ratio (a ~50 % dip, measured) and a turbine-like
              reverse branch -- what makes surge.
  duct/plenum Greitzer's lumped model: the compressor's mass flow has inertia
              (dW/dt = A_c/L_c (p_hat - p_plenum)), the plenum is a compliance;
              together a Helmholtz resonator that goes unstable on the positive
              slope.  Deep surge -- the flow reversing and recovering over and
              over -- falls out; nothing here sets its rate.
  turbine     a nozzle (its flow capacity sets the exhaust back-pressure, in
              closed form with the wastegate in parallel), efficiency by the
              blade-speed ratio.
  shaft       J dw/dt = (P_turbine - P_compressor - P_friction) / w, J from the
              wheels' sizes.

Sizes come from the engine: the compressor so the rated point sits at a well
matched flow coefficient, the turbine so full boost arrives at the car's
full-boost rpm, the inertia from both wheels (mass ~ D^3, radius^2 ~ D^2).
Every constant below is either physics or cited; the few estimates say so.
"""

from __future__ import annotations

import math

import numpy as np

GAMMA = 1.4
CP = 1005.0
R_AIR = 287.0
K_AIR = (GAMMA - 1.0) / GAMMA          # 0.2857
GAMMA_E = 1.33                         # hot exhaust
CP_E = 1150.0
K_EX = (GAMMA_E - 1.0) / GAMMA_E
P_ATM = 101325.0
T_AMB = 298.15

# --- the compressor stage (a typical automotive centrifugal compressor) -------
Z_MAIN, Z_SPLIT = 6, 6        # main + splitter blades: 6+6 is the common SI
                              #   stage (Dehner et al. 2021: 6+6, 39/49 mm)
BACKSWEEP = math.radians(40.0)  # exit backsweep (automotive: 30-50 deg)
B2_FRAC = 0.065               # exit blade height / exducer diameter
TRIM_D1 = 0.78                # inducer shroud / exducer diameter (GT2860RS
                              #   47.2/60.1 = 0.79, OSU 39.2/49.1 = 0.80,
                              #   GTX3582R 66/82 = 0.80; older wheels ~0.7)
HUB_D1 = 0.28                 # hub / inducer diameter
RHO2_RATIO = 1.30             # exit / inlet density over the impeller
PHI_ZSL = 0.045               # flow coefficient W/(rho01 U2 D2^2) at the
                              #   zero-slope (surge) point...
PHI_OPT = 0.085               # ...at peak efficiency (automotive maps: the
                              #   OSU stage's mid-flow 83 g/s at 140 krpm is
                              #   phi = 0.080)
PHI_MAX = 0.12                # ...at choke while the inducer is subsonic
M_CHOKE = 0.45                # inducer axial Mach at choke (relative-frame
                              #   choke at the shroud): caps the flow at speed
                              #   (a GT2860RS, 47/60 mm, then passes ~0.25
                              #   kg/s at 180 krpm; Garrett: ~35 lb/min)
ETA_PEAK = 0.78               # peak isentropic efficiency (modern maps 0.76-0.80)
ETA_SURGE_REL = 0.90          # efficiency at the surge point / peak
GAMMA_SURGE = 0.50            # zero-flow pressure-ratio dip: (Pi_zsl - Pi_c0) /
                              #   (Pi_zsl - 1) ~ 50 %, independent of speed
                              #   (Leufven & Eriksson 2013, surge rig)
C_REV = 0.07                  # reverse-flow effective area / eye area: the
                              #   spinning wheel is a tight restriction to
                              #   back-flow.  With the plenum it sets the deep
                              #   surge cycle: CALIBRATED so a 2 litre car's
                              #   4 L system cycles in ~75-80 ms (Leufven &
                              #   Eriksson measured 71 ms on an engine stand;
                              #   their fitted reverse branch passes 30-40 g/s
                              #   at 0.5-1 bar, the same order)
C_OPEN = 0.9                  # a stopped / choked wheel's open-area factor
C_DF = 4.0e-4                 # disk friction + windage: P = C_DF rho U2^3 D2^2
                              #   (Daily & Nece C_M ~ 3e-3, back face)

# --- the turbine stage ------------------------------------------------------
DT_OVER_D2 = 0.88             # turbine inducer / compressor exducer
                              #   (GT2860RS 53.9/60.1 = 0.90, GTX3582R 68/82)
ETA_T_MAX = 0.70              # peak turbine efficiency (incl. pulse losses)
BSR_OPT = 0.70                # blade-speed ratio U_t / c_s at peak (radial)
SEQ_BLEED = 0.30              # sequential pre-spool: the secondary's share of
                              #   the turbine area while its valve bleeds
SEQ_RELIEF = 0.3              # ...its dead-headed compressor's relief flow, per
                              #   surge flow
SEQ_ECV_T = 0.30              # ...and its exhaust valve's opening time (s)
TWIN_SCROLL_GAIN = 0.15       # divided housing: pulse energy kept to the
                              #   wheel at low speed (DSPORT A/B, 2.4 L:
                              #   full boost 400 rpm earlier, +21 % torque in
                              #   the transition, gone at the top)

# --- the rotor ----------------------------------------------------------------
RHO_AL, RHO_NI = 2700.0, 8000.0   # cast aluminium compressor, Inconel turbine
K_J_C = 0.0041                # J_c = K_J_C rho_Al D2^5: a 60 mm wheel ~60 g
                              #   with a radius of gyration ~0.4 R (hub-heavy)
K_J_T = 0.0069                # J_t = K_J_T rho_Ni Dt^5: a 54 mm turbine and
                              #   shaft ~170 g, gyration ~0.35 R.  Together a
                              #   GT28-class rotor ~3.4e-5 kg m^2 (estimate)
C_FRIC = 1.2e-6               # bearing friction P = C_FRIC (D2/60 mm)^3 w^2:
                              #   ~300 W at 150 krpm for a GT28 (journal);
                              #   ball bearings half of that

# --- the charge-air system ----------------------------------------------------
L_C_PER_D2 = 5.0              # Greitzer duct length / exducer diameter: the
                              #   inlet pipe and the wheel passages, referred to
                              #   the eye area (~0.3 m for a 60 mm wheel)
V_P_PER_VD = 2.0              # plenum (charge pipes + intercooler) / engine
                              #   displacement (a 2 L car: hot pipe ~0.8 L, a
                              #   front-mount core ~1.6 L + tanks, cold pipe
                              #   ~1.5 L: ~4 L -- an estimate)
DP_BOV_OPEN = 0.25e5          # blow-off valve: opens when plenum - manifold
DP_BOV_SPAN = 0.30e5          #   exceeds the spring, fully open 0.3 bar later
TAU_BOV_OPEN, TAU_BOV_CLOSE = 0.010, 0.040   # diaphragm (s)
BOV_D_REF = 0.025             # throat of a 25 mm valve (OEM diverter / SSQV)
WG_KP, WG_KI = 2.5, 4.0       # wastegate PI on the boost error (per bar)
WG_KD = 0.25                  # ...and the rate term (per bar/s)
TAU_WG = 0.06                 # wastegate actuator (s)
WG_OVERSPEED = 20.0           # gate opening per fraction over the speed limit
                              #   (the limit: 1.15 x the rated shaft speed)
DT_FAST = 0.0005              # charge-air substep (s): the Helmholtz mode is
                              #   ~20-60 Hz, the surge transitions a few ms
AFR_WOT, AFR_PART, AFR_DIESEL = 12.5, 14.7, 22.0


def _smooth_min(a, b, p=4.0):
    return (a ** -p + b ** -p) ** (-1.0 / p)


def _orifice(cda, p_up, t_up, p_dn):
    """Compressible orifice mass flow (kg/s), p_up -> p_dn (sign follows)."""
    if p_up < p_dn:
        return -_orifice(cda, p_dn, t_up, p_up)
    r = p_dn / max(p_up, 1.0)
    if r < 0.5283:
        psi = 0.6847
    else:
        psi = math.sqrt(max(2.0 * GAMMA / (GAMMA - 1.0)
                            * (r ** (2.0 / GAMMA) - r ** ((GAMMA + 1.0) / GAMMA)),
                            0.0))
    return cda * p_up / math.sqrt(R_AIR * t_up) * psi


class Compressor:
    """A centrifugal compressor stage of exducer diameter ``d2`` (m)."""

    def __init__(self, d2, z_main=Z_MAIN, z_split=Z_SPLIT):
        self.d2 = d2
        self.d1s = TRIM_D1 * d2
        self.d1h = HUB_D1 * self.d1s
        self.a_eye = math.pi / 4.0 * self.d1s ** 2
        self.a_ann = math.pi / 4.0 * (self.d1s ** 2 - self.d1h ** 2)
        self.z_main, self.z_split = z_main, z_split
        z = z_main + z_split
        self.sigma = 1.0 - math.sqrt(math.cos(BACKSWEEP)) / z ** 0.7   # Wiesner
        self.kappa = math.tan(BACKSWEEP) / (math.pi * B2_FRAC) / RHO2_RATIO

    def psi_e(self, phi):
        """Euler work coefficient: slip and backsweep."""
        return self.sigma - self.kappa * phi

    def line(self, u2, p01, t01):
        """The speed line's anchors at tip speed ``u2``: a dict with the flow
        scale, the surge (zero-slope) point, the choke flow, the ellipse
        exponent, the zero-flow pressure ratio."""
        rho = p01 / (R_AIR * t01)
        a01 = math.sqrt(GAMMA * R_AIR * t01)
        u2 = max(u2, 1.0)
        scale = rho * u2 * self.d2 ** 2                  # W per unit phi
        w_ch = _smooth_min(PHI_MAX * scale, M_CHOKE * rho * a01 * self.a_ann)
        phi_ch = max(w_ch / scale, PHI_ZSL * 1.3)
        m_u = u2 / a01
        eta_pk = max(ETA_PEAK - 0.10 * max(m_u - 1.0, 0.0) ** 2, 0.45)
        psi_z = ETA_SURGE_REL * eta_pk * self.psi_e(PHI_ZSL)
        phi_o = min(PHI_OPT, PHI_ZSL + 0.8 * (phi_ch - PHI_ZSL))
        psi_o = eta_pk * self.psi_e(phi_o)
        x_o = (phi_o - PHI_ZSL) / (phi_ch - PHI_ZSL)
        ratio = min(max(psi_o / psi_z, 0.05), 0.98)
        c1 = min(max(math.log(1.0 - ratio) / math.log(max(x_o, 1e-3)), 1.5), 10.0)
        hs = u2 * u2 / (CP * t01)
        pi_z = (1.0 + psi_z * hs) ** (1.0 / K_AIR)
        pi_0 = pi_z - GAMMA_SURGE * (pi_z - 1.0)
        return dict(rho=rho, u2=u2, scale=scale, w_z=PHI_ZSL * scale,
                    w_ch=phi_ch * scale, phi_ch=phi_ch, psi_z=psi_z, c1=c1,
                    hs=hs, pi_z=pi_z, pi_0=pi_0, p01=p01, t01=t01)

    def pressure_ratio(self, w, ln):
        """Pi_hat(W) on the line ``ln`` (any sign of W)."""
        w_z, w_ch = ln["w_z"], ln["w_ch"]
        if w >= w_z:
            if w <= w_ch:
                x = (w - w_z) / (w_ch - w_z)
                psi = ln["psi_z"] * (1.0 - x ** ln["c1"])
                return (1.0 + psi * ln["hs"]) ** (1.0 / K_AIR)
            # past choke the wheel is only a restriction
            dw = w - w_ch
            return 1.0 - dw * dw / (2.0 * ln["rho"] * (C_OPEN * self.a_ann) ** 2
                                    * ln["p01"])
        if w >= 0.0:
            s = w / w_z
            return ln["pi_0"] + (ln["pi_z"] - ln["pi_0"]) * s * s * (3.0 - 2.0 * s)
        return ln["pi_0"] + w * w / (2.0 * ln["rho"] * (C_REV * self.a_eye) ** 2
                                     * ln["p01"])

    def power(self, w, ln):
        """Shaft power absorbed (W): the Euler work on the through-flow (half
        of it on a reversed flow the wheel churns), plus disk friction."""
        u2 = ln["u2"]
        pdf = C_DF * ln["rho"] * u2 ** 3 * self.d2 ** 2
        if w > 0.0:
            return w * u2 * u2 * max(self.psi_e(w / ln["scale"]), 0.05) + pdf
        return -w * u2 * u2 * 0.5 * self.sigma + pdf

    def table(self, u2, p01, t01, n=97):
        """Tabulate p01 * Pi_hat and the power over W at this speed, for the
        fast charge-air substeps (lerp instead of the formula)."""
        ln = self.line(u2, p01, t01)
        w_lo = -2.5 * ln["w_z"]
        w_hi = 1.6 * ln["w_ch"]
        ws = np.linspace(w_lo, w_hi, n)
        pr = np.array([self.pressure_ratio(float(x), ln) for x in ws])
        pw = np.array([self.power(float(x), ln) for x in ws])
        return _Table(w_lo, (w_hi - w_lo) / (n - 1), (p01 * pr).tolist(),
                      pw.tolist(), ln)


class _Table:
    __slots__ = ("w0", "dw", "p", "pw", "ln", "n")

    def __init__(self, w0, dw, p, pw, ln):
        self.w0, self.dw, self.p, self.pw, self.ln = w0, dw, p, pw, ln
        self.n = len(p)

    def eval(self, w):
        """(p_hat, dp_hat/dW, power) at W, linear between nodes."""
        f = (w - self.w0) / self.dw
        i = int(f)
        if i < 0:
            i = 0
        elif i > self.n - 2:
            i = self.n - 2
        t = f - i
        p0, p1 = self.p[i], self.p[i + 1]
        slope = (p1 - p0) / self.dw
        q0, q1 = self.pw[i], self.pw[i + 1]
        return p0 + slope * (w - self.w0 - i * self.dw), slope, q0 + (q1 - q0) * min(max(t, 0.0), 1.0)


class Turbine:
    """A radial turbine: flow capacity ``a_eff`` (m^2, the nozzle-equivalent
    area) and wheel diameter ``d_t``."""

    def __init__(self, d_t, a_eff):
        self.d_t = d_t
        self.a_eff = a_eff

    @staticmethod
    def inlet_pressure(w_ex, t03, p04, a_tot):
        """p03 so the turbine (and any open wastegate, both in a_tot) pass
        w_ex: W = A p03 / sqrt(R T03) sqrt(1 - (p04/p03)^2) inverts exactly,
        p03 = sqrt(p04^2 + (W sqrt(R T03) / A)^2).  (That flow form is the
        incompressible orifice at small ratios and saturates like a turbine's
        two nozzles in series at large ones.)"""
        q = w_ex * math.sqrt(R_AIR * t03) / max(a_tot, 1e-9)
        return math.sqrt(p04 * p04 + q * q)

    def power(self, w_t, t03, p03, p04, omega, pulse=1.0):
        """Shaft power (W) from w_t kg/s expanding p03 -> p04."""
        ex = 1.0 - (p04 / p03) ** K_EX
        if ex <= 1e-6 or w_t <= 0.0:
            return 0.0
        c_s = math.sqrt(2.0 * CP_E * t03 * ex)
        u_t = omega * self.d_t * 0.5
        r = (u_t / c_s) / BSR_OPT
        eta = ETA_T_MAX * max(2.0 * r - r * r, 0.0) * pulse
        return eta * w_t * CP_E * t03 * ex


class TurboUnit:
    """One turbocharger: compressor + turbine on a shaft, its own wastegate."""

    def __init__(self, d2, a_t, a_wg, ball_bearing=False, name="turbo"):
        self.name = name
        self.comp = Compressor(d2)
        self.turb = Turbine(DT_OVER_D2 * d2, a_t)
        self.a_wg = a_wg
        self.j = (K_J_C * RHO_AL * d2 ** 5
                  + K_J_T * RHO_NI * (DT_OVER_D2 * d2) ** 5)
        self.c_fric = C_FRIC * (d2 / 0.060) ** 3 * (0.5 if ball_bearing else 1.0)
        self.l_c = L_C_PER_D2 * d2
        self.omega = 2000.0               # rad/s (~19 krpm, a warm idle)
        self.w_c = 0.0                    # compressor mass flow (kg/s)
        self.x_wg = 0.0                   # wastegate opening 0..1
        self.wg_int = 0.0                 # controller integral
        self.p_t = 0.0                    # last turbine power (W)
        self.p_c = 0.0                    # last mean compressor power (W)
        self.w_t = 0.0                    # turbine mass flow
        self.w_wgf = 0.0                  # wastegate mass flow
        self.p03 = P_ATM                  # turbine inlet pressure
        self.table = None
        self.table_omega = 0.0            # shaft speed the table was drawn at
        self.active = True                # sequential: joined the plenum
        self.ecv = 1.0                    # sequential: exhaust control valve

    @property
    def rpm(self):
        return self.omega * 60.0 / (2.0 * math.pi)

    @property
    def u2(self):
        return self.omega * self.comp.d2 * 0.5


class ChargeAir:
    """The fast part: each compressor's duct (its mass flow has inertia) into
    one plenum (a compliance), drained by the throttle (a linear load line
    from the engine's pumping), a blow-off valve and an anti-lag bypass.

    The same class runs in the physics (0.5 ms steps) and, as a twin, in the
    synthesizer (finer, driven by the physics' shaft speeds) -- so the flutter
    one hears is the flutter the boost gauge shows."""

    def __init__(self, v_p, bov_cda, bov_mode="recirc"):
        self.v_p = v_p
        self.bov_cda = bov_cda
        self.bov_mode = bov_mode          # "recirc" | "atmo" | "none"
        self.p_p = P_ATM
        self.x_bov = 0.0
        self.w_bov = 0.0

    def substep(self, units, dt, k_thr, map_pa, t_p, p01, w_bypass=0.0):
        """Advance by dt (s): semi-implicit, so the stiff branches (the
        throttle, the reverse-flow restriction) stay stable."""
        c = GAMMA * R_AIR * t_p / self.v_p
        w_in = 0.0
        for u in units:
            if not u.active or u.table is None:
                continue
            ph, slope, _ = u.table.eval(u.w_c)
            k = u.comp.a_eye / u.l_c
            den = 1.0 - dt * k * slope
            if den < 0.25:                 # the unstable branch: explicit
                u.w_c += dt * k * (ph - self.p_p)
            else:
                u.w_c += dt * k * (ph - self.p_p) / den
            w_in += u.w_c
        # blow-off valve: the diaphragm sees plenum minus manifold
        if self.bov_mode != "none":
            tgt = min(max((self.p_p - map_pa - DP_BOV_OPEN) / DP_BOV_SPAN, 0.0), 1.0)
            tau = TAU_BOV_OPEN if tgt > self.x_bov else TAU_BOV_CLOSE
            self.x_bov += (tgt - self.x_bov) * min(dt / tau, 1.0)
            self.w_bov = (_orifice(self.bov_cda * self.x_bov, self.p_p, t_p, p01)
                          if self.x_bov > 1e-4 else 0.0)
        else:
            self.x_bov, self.w_bov = 0.0, 0.0
        # plenum: implicit in the throttle's (linear) draw
        self.p_p = ((self.p_p + dt * c * (w_in - self.w_bov - w_bypass))
                    / (1.0 + dt * c * k_thr))
        self.p_p = max(self.p_p, 0.3 * P_ATM)

    def run(self, units, dt, k_thr, map_pa, t_p, p01, w_bypass=0.0,
            h=DT_FAST, record=None):
        """dt in substeps of about h; returns each unit's mean compressor power
        over the interval.  ``record`` (a list) gets (w_c per unit, p_p, w_bov)
        after every substep."""
        n = max(int(math.ceil(dt / h)), 1)
        hs = dt / n
        acc = [0.0] * len(units)
        for _ in range(n):
            self.substep(units, hs, k_thr, map_pa, t_p, p01, w_bypass)
            for j, u in enumerate(units):
                if u.active and u.table is not None:
                    acc[j] += u.table.eval(u.w_c)[2]
            if record is not None:
                record.append(([u.w_c for u in units], self.p_p, self.w_bov))
        return [a / n for a in acc]


class TurboSystem:
    """All of an engine's turbochargers and their charge-air system.

    ``engine_flow(rpm, thr, p_p) -> (w_air, map_pa, t_man)`` and
    ``exhaust_temp(rpm, load) -> T03`` come from the simulator, so the load
    line and the turbine's heat are the engine's own."""

    def __init__(self, units, layout, v_p, bov_cda, bov_mode, boost_bar,
                 twin_scroll=False, seq_rpm=(0.0, 0.0), diesel=False,
                 external_wg=False, anti_lag=False, e_turbo=False,
                 sc_pr=0.0, sc_rpm=0.0):
        self.units = units
        self.layout = layout              # "single" | "twin" | "quad" | "sequential"
        self.air = ChargeAir(v_p, bov_cda, bov_mode)
        self.boost_bar = boost_bar
        self.twin_scroll = twin_scroll
        self.seq_rpm = seq_rpm            # (pre-spool starts, secondary joins)
        self.diesel = diesel
        self.external_wg = external_wg
        self.anti_lag = anti_lag
        self.e_turbo = e_turbo
        self.sc_pr, self.sc_rpm = sc_pr, sc_rpm   # series blower (twincharge)
        self.k_thr = 0.0
        self.map_pa = P_ATM
        self.t_p = T_AMB
        self.p01 = P_ATM
        self.t03 = 600.0
        self.p04 = P_ATM
        self.w_air = 0.0
        self.w_ex = 0.0
        self.dp_down_k = 0.0              # post-turbine pressure drop / W^2
        self.w_bypass = 0.0               # anti-lag air around the throttle
        self.stepped = 0                  # frames the physics has advanced
        self.p_mguh = 0.0                 # MGU-H motor power into unit 0

    # ------------------------------------------------------------- helpers
    @property
    def boost(self):
        """Plenum pressure, bar gauge (what a boost gauge reads)."""
        return (self.air.p_p - P_ATM) / 1.0e5

    def _p01(self, rpm):
        """Compressor inlet: ambient, or a series blower's outlet (the Delta
        S4's Volumex feeding the turbo at low rpm, bypassed above sc_rpm)."""
        if self.sc_pr > 0.0 and self.sc_rpm > 0.0:
            f = min(max(1.0 - rpm / self.sc_rpm, 0.0), 1.0)
            return P_ATM * (1.0 + self.sc_pr * f)
        return P_ATM

    def _shares(self, rpm, thr, dt=0.0):
        """Exhaust flow share and join state of each unit."""
        n = len(self.units)
        if self.layout == "sequential" and n == 2:
            r1, r2 = self.seq_rpm
            u2 = self.units[1]
            if thr < 0.5 or rpm < r1:
                u2.ecv = 0.0
            elif rpm < r2 and u2.ecv < SEQ_BLEED:
                # the pre-boost control valve bleeds a little exhaust into the
                # secondary to pre-spin it (its compressor still recirculating)
                u2.ecv = SEQ_BLEED * (rpm - r1) / max(r2 - r1, 1.0)
            elif rpm >= r2:
                # the exhaust control valve opens as the secondary comes up to
                # speed (the ECU watches it: open too early and the primary
                # loses its exhaust before the secondary can blow), through
                # its actuator
                u1 = self.units[0]
                ready = min(max((u2.omega / max(u1.omega, 1.0) - 0.5) / 0.4, 0.0), 1.0)
                tgt = SEQ_BLEED + (1.0 - SEQ_BLEED) * ready
                step = dt / SEQ_ECV_T
                u2.ecv = min(max(u2.ecv + min(max(tgt - u2.ecv, -step), step),
                                 0.0), 1.0)
                if tgt >= 0.999 and u2.ecv > 0.995:
                    u2.ecv = 1.0
            if u2.active:
                u2.active = rpm > r2 * 0.95 and thr >= 0.5
            elif rpm >= r2 and thr >= 0.5:
                # its intake valve is a check valve: it opens once the secondary
                # can push against the plenum
                ln = u2.comp.line(u2.u2, self.p01, T_AMB)
                if self.p01 * u2.comp.pressure_ratio(SEQ_RELIEF * ln["w_z"], ln)                         >= self.air.p_p:
                    u2.active = True
                    u2.w_c = SEQ_RELIEF * ln["w_z"]
            self.units[0].ecv, self.units[0].active = 1.0, True
            return [1.0, u2.ecv]
        for u in self.units:
            u.ecv, u.active = 1.0, True
        return [1.0] * n

    def _exhaust(self, rpm, thr, dt, fuel_cut):
        """Turbine inlet pressures, flows and powers for this frame."""
        n = len(self.units)
        shares = self._shares(rpm, thr, dt)
        # the gas: the engine's air plus fuel; on a fuel cut it is cool air
        afr = AFR_DIESEL if self.diesel else (AFR_WOT if thr > 0.8 else AFR_PART)
        w_ex = self.w_air * (1.0 + (0.0 if fuel_cut else 1.0 / afr))
        t03 = self.t03
        if self.anti_lag and thr < 0.2 and not fuel_cut:
            # anti-lag: air around the throttle, fuel burnt in the manifold --
            # the turbine keeps its heat and its flow off the throttle
            w_ex = max(w_ex, self.w_bypass * (1.0 + 1.0 / AFR_WOT))
            t03 = max(t03, 1250.0)
        self.w_ex = w_ex
        self.p04 = P_ATM + self.dp_down_k * w_ex * w_ex
        pulse = 1.0
        if self.twin_scroll:
            pulse = 1.0 + TWIN_SCROLL_GAIN * max(1.0 - rpm / max(self.rpm_red, 1.0), 0.0)
        if self.layout == "sequential" and n == 2:
            # one manifold feeds both turbines; the secondary behind its valve
            a_tot = 0.0
            for u, s in zip(self.units, shares):
                a_tot += s * u.turb.a_eff + u.x_wg * u.a_wg * (1.0 if s > 0.5 else 0.0)
            p03 = Turbine.inlet_pressure(w_ex, t03, self.p04, a_tot)
            for u, s in zip(self.units, shares):
                a_u = s * u.turb.a_eff
                u.w_t = w_ex * a_u / a_tot
                u.w_wgf = w_ex * (u.x_wg * u.a_wg * (1.0 if s > 0.5 else 0.0)) / a_tot
                u.p03 = p03
                u.p_t = u.turb.power(u.w_t, t03, p03, self.p04, u.omega, pulse)
            return
        per = w_ex / n                    # parallel: each bank its own turbine
        for u in self.units:
            a_tot = u.turb.a_eff + u.x_wg * u.a_wg
            u.p03 = Turbine.inlet_pressure(per, t03, self.p04, a_tot)
            u.w_t = per * u.turb.a_eff / a_tot
            u.w_wgf = per - u.w_t
            u.p_t = u.turb.power(u.w_t, t03, u.p03, self.p04, u.omega, pulse)

    def _wastegate(self, thr, dt):
        """Boost control: the ECU asks for boost_bar x pedal; a PI on the error,
        integrating only near the target (no wind-up while spooling), with a
        rate term that cracks the gate as the boost rushes in -- how a modern
        controller avoids the old overshoot -- drives each gate through its
        actuator lag."""
        target = P_ATM + self.boost_bar * 1.0e5 * min(max(thr, 0.0), 1.0)
        e = (self.air.p_p - target) / 1.0e5
        rate = (self.air.p_p - getattr(self, "_p_prev", self.air.p_p)) / 1.0e5 / max(dt, 1e-4)
        self._p_prev = self.air.p_p
        for u in self.units:
            if e > -0.15:
                u.wg_int = min(max(u.wg_int + WG_KI * e * dt, 0.0), 1.0)
            cmd = WG_KP * e + u.wg_int + WG_KD * max(rate, 0.0)
            # overspeed protection: a choking compressor stops loading its
            # turbine, the boost falls, the gate would shut -- and the shaft
            # runs away.  The ECU watches the speed and opens the gate.
            over = u.omega / max(self.omega_max, 1.0) - 1.0
            if over > 0.0:
                cmd = max(cmd, WG_OVERSPEED * over)
            cmd = min(max(cmd, 0.0), 1.0)
            u.x_wg += (cmd - u.x_wg) * min(dt / TAU_WG, 1.0)

    # ------------------------------------------------------------ the frame
    def step(self, dt, rpm, thr, engine_flow, exhaust_temp, fuel_cut=False,
             mguh_w=0.0):
        """Advance the whole system by dt (s)."""
        self.stepped += 1
        n_chunks = max(int(math.ceil(dt / 0.02)), 1)     # shaft: <= 20 ms
        h = dt / n_chunks
        for _ in range(n_chunks):
            self._frame(h, rpm, thr, engine_flow, exhaust_temp, fuel_cut, mguh_w)

    def _frame(self, dt, rpm, thr, engine_flow, exhaust_temp, fuel_cut, mguh_w):
        p_p = self.air.p_p
        w_air, map_pa, t_man = engine_flow(rpm, thr, p_p)
        self.w_air, self.map_pa, self.t_p = w_air, map_pa, t_man
        self.k_thr = w_air / max(p_p, 1.0)
        self.p01 = self._p01(rpm)
        self.t03 = (exhaust_temp(rpm, max(thr, 0.05)) if not fuel_cut
                    else max(t_man, T_AMB) + 150.0)
        if self.anti_lag:
            w_wot, _, _ = engine_flow(rpm, 1.0, P_ATM + self.boost_bar * 1.0e5)
            self.w_bypass = (0.35 * w_wot if (thr < 0.2 and rpm > 0.35 * self.rpm_red
                                              and not fuel_cut) else 0.0)
        else:
            self.w_bypass = 0.0
        self._exhaust(rpm, thr, dt, fuel_cut)
        self._wastegate(thr, dt)
        for u in self.units:
            if not u.active:
                u.table = None
            elif (u.table is None or abs(u.omega - u.table_omega) > 0.005 * u.table_omega
                  or u.table.ln["p01"] != self.p01):
                # the speed line only needs redrawing when the shaft has moved
                u.table = u.comp.table(u.u2, self.p01, T_AMB)
                u.table_omega = u.omega
        pcs = self.air.run(self.units, dt, self.k_thr, map_pa, t_man, self.p01,
                           self.w_bypass)
        for j, u in enumerate(self.units):
            if u.active:
                u.p_c = pcs[j]
            else:
                # sequential secondary behind its closed intake valve: dead-
                # headed, a relief valve passing a little -- it spins up light
                ln = u.comp.line(u.u2, self.p01, T_AMB)
                u.w_c = SEQ_RELIEF * ln["w_z"]
                u.p_c = u.comp.power(u.w_c, ln)
            p_fr = u.c_fric * u.omega * u.omega
            p_net = u.p_t - u.p_c - p_fr + (mguh_w if u is self.units[0] else 0.0)
            u.omega = max(u.omega + dt * p_net / (u.j * max(u.omega, 500.0)), 50.0)

    # -------------------------------------------------------- steady state
    def steady(self, rpm, thr, engine_flow, exhaust_temp, wg_closed=True,
               w_iters=40):
        """Steady WOT/part-throttle operating point with the gates shut (or as
        controlled): returns (boost_bar_gauge, shaft rad/s of unit 0).

        For a shaft speed w: the plenum pressure where the compressor's speed
        line meets the engine's draw; then the turbine's power at the exhaust
        that draw makes.  The shaft balance P_t = P_c + P_fr picks w."""
        n_act = len(self.units) if self.layout != "sequential" else (
            2 if rpm >= self.seq_rpm[1] and thr >= 0.5 else 1)
        units = self.units[:n_act]
        u0 = units[0]
        p01 = self._p01(rpm)
        afr = AFR_DIESEL if self.diesel else (AFR_WOT if thr > 0.8 else AFR_PART)

        def plenum_at(omega):
            ln = u0.comp.line(omega * u0.comp.d2 * 0.5, p01, T_AMB)
            lo, hi = 0.5 * P_ATM, 6.0 * P_ATM
            w = 0.0
            for _ in range(w_iters):
                mid = 0.5 * (lo + hi)
                w_air, _, _ = engine_flow(rpm, thr, mid)
                w = w_air / n_act
                if p01 * u0.comp.pressure_ratio(w, ln) > mid:
                    lo = mid
                else:
                    hi = mid
            return 0.5 * (lo + hi), w, ln

        def surplus(omega):
            p_p, w, ln = plenum_at(omega)
            w_ex = w * n_act * (1.0 + 1.0 / afr)
            t03 = exhaust_temp(rpm, max(thr, 0.05))
            p04 = P_ATM + self.dp_down_k * w_ex * w_ex
            per = w_ex / n_act
            p03 = Turbine.inlet_pressure(per, t03, p04, u0.turb.a_eff)
            pulse = 1.0 + (TWIN_SCROLL_GAIN * max(1.0 - rpm / max(self.rpm_red, 1.0), 0.0)
                           if self.twin_scroll else 0.0)
            p_t = u0.turb.power(per, t03, p03, p04, omega, pulse)
            p_c = u0.comp.power(w, ln) + u0.c_fric * omega * omega
            return p_t - p_c, p_p

        lo, hi = 300.0, 1.5 * self.omega_max
        s_hi, p_hi = surplus(hi)
        if s_hi > 0.0:                    # it would overspeed: the gate holds it
            b = (p_hi - P_ATM) / 1.0e5
            if not wg_closed:
                b = min(b, self.boost_bar * min(max(thr, 0.0), 1.0))
            return b, hi
        for _ in range(40):
            mid = math.sqrt(lo * hi)
            s, _ = surplus(mid)
            if s > 0.0:
                lo = mid
            else:
                hi = mid
        omega = math.sqrt(lo * hi)
        _, p_p = surplus(omega)
        b = (p_p - P_ATM) / 1.0e5
        if not wg_closed:
            b = min(b, self.boost_bar * min(max(thr, 0.0), 1.0))
        return b, omega

    # ------------------------------------------------------------- observer
    def follow(self, boost_bar_gauge, rpm, thr, dt, engine_flow):
        """Car / telemetry mode: the REAL boost is known; keep the plenum on it
        and let each shaft relax to the speed its compressor needs there."""
        self.stepped += 1
        p_p = P_ATM + max(boost_bar_gauge, -0.7) * 1.0e5
        self.air.p_p = p_p
        w_air, map_pa, t_man = engine_flow(rpm, thr, p_p)
        self.w_air, self.map_pa, self.t_p = w_air, map_pa, t_man
        self.k_thr = w_air / max(p_p, 1.0)
        n_act = sum(1 for u in self.units if u.active) or 1
        for u in self.units:
            if not u.active:
                continue
            w = w_air / n_act
            u.w_c = w
            lo, hi = 300.0, 1.5 * self.omega_max
            for _ in range(30):
                mid = 0.5 * (lo + hi)
                ln = u.comp.line(mid * u.comp.d2 * 0.5, P_ATM, T_AMB)
                if P_ATM * u.comp.pressure_ratio(max(w, ln["w_z"]), ln) < p_p:
                    lo = mid
                else:
                    hi = mid
            u.omega += (0.5 * (lo + hi) - u.omega) * min(dt / 0.15, 1.0)
            u.table = u.comp.table(u.u2, P_ATM, T_AMB)
            u.table_omega = u.omega

    # -------------------------------------------------------------- reading
    def compressor_outlet_temp(self):
        """T2 of unit 0 at its current operating point (K)."""
        u = self.units[0]
        pr = max(self.air.p_p / self.p01, 1.0)
        ln = u.table.ln if u.table is not None else None
        eta = 0.7
        if ln is not None and u.w_c > 0.0:
            psi_e = u.comp.psi_e(u.w_c / ln["scale"])
            psi = CP * T_AMB * (pr ** K_AIR - 1.0) / max(ln["u2"] ** 2, 1.0)
            eta = min(max(psi / max(psi_e, 1e-3), 0.3), 0.85)
        return T_AMB * (1.0 + (pr ** K_AIR - 1.0) / eta)

    def phi(self, u):
        """Flow coefficient of unit u (for the synthesizer's whoosh)."""
        if u.table is None:
            return 0.0
        return u.w_c / u.table.ln["scale"]


class _Proxy:
    """A twin's view of one unit: the physics' compressor and table, its own
    duct flow."""
    __slots__ = ("comp", "l_c", "table", "active", "w_c")

    def __init__(self, u):
        self.comp, self.l_c = u.comp, u.l_c
        self.table, self.active, self.w_c = u.table, u.active, u.w_c


class Twin:
    """The charge-air system again, at audio rate, for the synthesizer.

    The physics steps the plenum and the ducts at 0.5 ms inside each frame; the
    sound of surge lives in the few milliseconds when the flow reverses, and the
    audio runs on its own clock.  So the synthesizer runs this twin: the same
    equations, driven by the physics' shaft speeds, load line and valve set-up,
    stepped finer on its own clock.  While nothing is happening -- forward flow
    well right of surge, the valve shut, the two agreeing -- it just copies the
    physics and costs nothing."""

    def __init__(self, ts):
        self.ts = ts
        self.air = ChargeAir(ts.air.v_p, ts.air.bov_cda, ts.air.bov_mode)
        self.units = [_Proxy(u) for u in ts.units]
        self.sync()
        self.busy = 0

    def sync(self):
        ts = self.ts
        self.air.p_p, self.air.x_bov, self.air.w_bov = (ts.air.p_p, ts.air.x_bov,
                                                        ts.air.w_bov)
        for p, u in zip(self.units, ts.units):
            p.table, p.active, p.w_c = u.table, u.active, u.w_c

    def quiet(self):
        """Nothing to resolve: every active unit clear of surge, the valve shut,
        the twin on the physics."""
        ts = self.ts
        if ts.air.x_bov > 0.005 or self.air.x_bov > 0.005:
            return False
        if abs(self.air.p_p - ts.air.p_p) > 0.03e5:
            return False
        for p, u in zip(self.units, ts.units):
            if not u.active or u.table is None:
                continue
            if u.w_c < 1.6 * u.table.ln["w_z"] or p.w_c < 1.6 * u.table.ln["w_z"]:
                return False
        return True

    def run(self, dt, n_sub):
        """Advance dt in n_sub steps; returns the per-step record
        [(w_c per unit, p_p, w_bov), ...] (length n_sub)."""
        ts = self.ts
        if len(self.units) != len(ts.units):
            self.units = [_Proxy(u) for u in ts.units]
        self.air.bov_mode = ts.air.bov_mode
        self.air.bov_cda = ts.air.bov_cda
        if self.quiet():
            self.sync()
            self.busy = max(self.busy - 1, 0)
            rec = ([p.w_c for p in self.units], self.air.p_p, self.air.w_bov)
            return [rec] * n_sub
        self.busy = 8
        for p, u in zip(self.units, ts.units):
            p.table, p.active = u.table, u.active
            if not u.active:
                p.w_c = u.w_c
        rec = []
        self.air.run(self.units, dt, ts.k_thr, ts.map_pa, ts.t_p, ts.p01,
                     ts.w_bypass, h=dt / max(n_sub, 1), record=rec)
        return rec


def build(eng, engine_flow, exhaust_temp, bov_mode="recirc"):
    """Size and build the TurboSystem for engine ``eng``.

    Compressor: the rated point (0.88 x redline, full boost) sits at phi = 0.08,
    just right of peak efficiency -- a well matched stage -- which fixes the tip
    speed from the pressure ratio and then the diameter from the flow.
    Turbine: its flow capacity is solved so that, gates shut, the WOT shaft
    balance reaches full boost exactly at the car's full-boost rpm.
    Everything else follows from those two diameters."""
    sub = getattr(eng, "induction_subtype", "")
    layout = (getattr(eng, "turbo_layout", "")
              or {"sequential": "sequential", "twin": "twin"}.get(sub, "single"))
    n = {"single": 1, "twin": 2, "sequential": 2, "quad": 4}.get(layout, 1)
    red = eng.redline_rpm
    b = max(eng.boost_bar, 0.05)
    diesel = eng.cylinders[0].compression_ratio >= 14.5
    rpm_d = 0.88 * red
    p_d = P_ATM + b * 1.0e5
    w_air_d, _, _ = engine_flow(rpm_d, 1.0, p_d)
    # a sequential pair and a parallel set share the rated flow between them
    w_d = w_air_d / n
    pr_d = p_d / P_ATM + 0.05                      # + the intercooler's drop
    # a road turbo is matched small, for response: the rated point sits 80 %
    # of the way from surge to choke along its speed line (the IS38, the K03,
    # a GT28 on a 300 hp 2 litre all run out of flow at the top)
    d2, u2_d = 0.06, 400.0
    for _ in range(12):
        c = Compressor(d2)
        ln = c.line(u2_d, P_ATM, T_AMB)
        w_des = ln["w_z"] + 0.8 * (ln["w_ch"] - ln["w_z"])
        pr_line = c.pressure_ratio(w_des, ln)
        # the tip speed that makes pr_d there (head ~ U2^2 at fixed phi)
        u2_d *= math.sqrt((pr_d ** K_AIR - 1.0) / max(pr_line ** K_AIR - 1.0, 1e-4))
        u2_d = min(u2_d, 600.0)                    # billet / titanium limit
        d2 *= math.sqrt(w_d / max(w_des, 1e-6))
        d2 = min(max(d2, 0.030), 0.140)
    omega_max = 1.15 * 2.0 * u2_d / d2
    v_p = V_P_PER_VD * eng.total_displacement
    bov_cda = 0.6 * math.pi / 4.0 * (BOV_D_REF * math.sqrt(max(w_air_d, 0.02) / 0.20)) ** 2
    ext_wg = getattr(eng, "wastegate", "internal") == "external"
    ball = getattr(eng, "turbo_ball_bearing", False)
    units = [TurboUnit(d2, 1e-3, 1e-3, ball, name="turbo%d" % (i + 1))
             for i in range(n)]
    seq = (0.0, 0.0)
    if layout == "sequential":
        seq = (getattr(eng, "seq_rpm_on", 0.0) or 0.50 * red,
               getattr(eng, "seq_rpm_full", 0.0) or 0.57 * red)
    sc_pr = sc_rpm = 0.0
    if sub == "twincharge":
        sc_pr, sc_rpm = 0.6 * b, 0.60 * red
    ts = TurboSystem(units, layout, v_p, bov_cda, bov_mode, b,
                     twin_scroll=(sub == "twin_scroll"), seq_rpm=seq,
                     diesel=diesel, external_wg=ext_wg,
                     anti_lag=bool(getattr(eng, "anti_lag", False)),
                     e_turbo=bool(getattr(eng, "electric_turbo", False)
                                  or getattr(eng, "mgu_h", False)),
                     sc_pr=sc_pr, sc_rpm=sc_rpm)
    ts.rpm_red = red
    ts.omega_max = omega_max
    # post-turbine system (cat, muffler): ~0.25 bar at the rated exhaust flow
    w_ex_d = w_air_d * (1.0 + 1.0 / (AFR_DIESEL if diesel else AFR_WOT))
    ts.dp_down_k = 0.25e5 / max(w_ex_d, 1e-3) ** 2
    # the turbine: full boost at the car's full-boost rpm, gates shut
    rpm_full = min(max((eng.turbo_spool_frac + eng.turbo_spool_width) * red,
                       0.25 * red), 0.90 * red)
    if getattr(eng, "turbo_full_rpm", 0.0):
        rpm_full = min(max(eng.turbo_full_rpm, 0.2 * red), 0.9 * red)
    ts.rpm_full = rpm_full
    lo, hi = 1e-5, 0.05
    for _ in range(30):
        a = math.sqrt(lo * hi)
        for u in units:
            u.turb.a_eff = a
        bst, _ = ts.steady(rpm_full, 1.0, engine_flow, exhaust_temp, w_iters=24)
        if bst > b:
            lo = a
        else:
            hi = a
    a_t = math.sqrt(lo * hi)
    # the rated turbine pressure ratio (full boost arriving, gates shut) and
    # the rated air flow: the synthesizer's scales for the turbine's loading
    # and the blow-off valve's flow
    afr_d = AFR_DIESEL if diesel else AFR_WOT
    w_f, _, _ = engine_flow(rpm_full, 1.0, p_d)
    w_exf = w_f * (1.0 + 1.0 / afr_d)
    p04f = P_ATM + ts.dp_down_k * w_exf * w_exf
    p03f = Turbine.inlet_pressure(w_exf / n, exhaust_temp(rpm_full, 1.0), p04f, a_t)
    ts.pit_rated = max(p03f / p04f, 1.1)
    ts.w_air_rated = w_air_d
    # a BIGGER or SMALLER turbo than the stock match (turbo_size): every length
    # x s -- flow capacities x s^2, the rotor's inertia x s^5.  Full boost then
    # moves (later for a big one) by physics, not by a setting.
    s = min(max(float(getattr(eng, "turbo_size", 1.0) or 1.0), 0.5), 2.0)
    if abs(s - 1.0) > 1e-6:
        units = [TurboUnit(d2 * s, 1e-3, 1e-3, ball, name="turbo%d" % (i + 1))
                 for i in range(n)]
        ts.units = units
        ts.omega_max = omega_max / s
        a_t *= s * s
    a_wg = (1.2 if ext_wg else 0.7) * a_t
    for i, u in enumerate(units):
        # bank-to-bank tolerance of the turbine housings (+-0.5 %): the twins
        # never quite run at one speed
        tol = 1.0 + (0.005 if i % 2 else -0.005) * (1 if n > 1 else 0)
        u.turb.a_eff = a_t * tol
        u.a_wg = a_wg
    ts.size = s
    return ts
