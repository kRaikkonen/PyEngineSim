"""Hold the v2 core's cylinder model to two independent yardsticks.

    python tools/core_check.py             # both, a few operating points
    python tools/core_check.py reference   # 1 only
    python tools/core_check.py truth       # 2 only

1. REFERENCE -- the same equations, written a second time.  The core steps the
   cylinder once a sample with an implicit valve step (core/sound/src/
   cylinder.rs).  Here the same physics is written as a plain differential
   equation and integrated by classical Runge-Kutta at 384 kHz, in Python,
   from the core's own state at one instant.  The two share no code.  If the
   core's equations are the ones it claims, its error against this shrinks in
   proportion to its time step: it is run at 48, 96 and 192 kHz to see that.

2. TRUTH -- another model of the same engine.  gas_truth.py is the offline
   port of engine-sim's gas dynamics: other equations (see `truth()` below
   for exactly how they differ), another combustion model, lumped runners.
   Given the same valves and the same burn, the two should tell the same
   story about a cycle -- not to the digit.  This prints where they stand.

Exit status is non-zero if a yardstick is missed.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine_sim import core_spec, corelib, gas_truth, presets  # noqa: E402

R = 287.0
P_ATM = 101325.0
EPS = 1.0e-3                 # gas.rs LINEAR_BELOW
AFR = 14.7
LHV = 44.0e6
ETA = 0.96
WIEBE_A = 6.908
WIEBE_Y50 = 0.46466
WIEBE_SPAN = 0.44535


# --------------------------------------------------------------------------
# 1. the reference: the model as an ODE
# --------------------------------------------------------------------------

def gamma(t, gc=0.0):
    return gc if gc > 0.0 else 1.375 - 6.99e-5 * min(max(t, 250.0), 2800.0)


def psi(r, g):
    """The nozzle law as written, with the core's straight line near r = 1."""
    def sub(r_):
        s = r_ ** (1.0 / g)
        return math.sqrt(max(2.0 * g / (g - 1.0) * s * (s - r_), 0.0))
    rc = (2.0 / (g + 1.0)) ** (g / (g - 1.0))
    if r <= rc:
        return sub(rc)
    if r < 1.0 - EPS:
        return sub(r)
    return sub(1.0 - EPS) * max(1.0 - r, 0.0) / EPS


def flow_out(cda, p, t, pp, tp, gc):
    """kg/s out of the cylinder (p, t) into a port (pp, tp)."""
    if cda <= 0.0:
        return 0.0
    if p >= pp:
        return cda * p / math.sqrt(R * t) * psi(pp / p, gamma(t, gc))
    return -cda * pp / math.sqrt(R * tp) * psi(p / pp, gamma(tp, gc))


class Reference:
    """d(m, T, m_burned)/dt of one cylinder at a fixed operating point."""

    def __init__(self, spec, ctl, theta0):
        self.s = spec
        self.c = ctl
        self.theta0 = theta0
        self.omega = 6.0 * ctl.rpm                       # deg/s
        self.len = ctl.burn_deg / WIEBE_SPAN
        self.start = (ctl.ca50_deg - WIEBE_Y50 * self.len) % 720.0
        self.q_total = 0.0
        self.burning = False
        vp = 2.0 * spec.stroke * ctl.rpm / 60.0
        self.speed_term = (vp + 1.4) ** 0.8
        self.a_bore = math.pi * 0.25 * spec.bore ** 2

    def theta(self, t):
        return (self.theta0 + self.omega * t) % 720.0

    def flows(self, t, y):
        m, T, _ = y
        th = self.theta(t)
        p = m * R * T / self.s.volume(th)
        gc = self.s.gamma_const
        qe = flow_out(self.s.exhaust.area_at(th), p, T, self.c.p_exhaust, self.c.t_exhaust, gc)
        qi = flow_out(self.s.intake.area_at(th), p, T, self.c.p_intake, self.c.t_intake, gc)
        return qe, qi, p

    def rhs(self, t, y):
        m, T, mb = y
        s, c = self.s, self.c
        th = self.theta(t)
        V = s.volume(th)
        dVdt = s.dvolume(th) * math.radians(self.omega)
        gc = s.gamma_const
        g = gamma(T, gc)
        cv = R / (g - 1.0)
        qe, qi, p = self.flows(t, y)
        dq = 0.0
        if self.burning:
            yb = ((th - self.start) % 720.0) / self.len
            if yb < 1.0:
                dq += (self.q_total * 3.0 * WIEBE_A * yb * yb * math.exp(-WIEBE_A * yb ** 3)
                       / 0.999 / self.len * self.omega)
        if s.heat_scale > 0.0:
            h = 130.0 * V ** -0.06 * (p * 1e-5) ** 0.8 * T ** -0.4 * self.speed_term * s.heat_scale
            dq -= h * (2.0 * self.a_bore + 4.0 * V / s.bore) * (T - s.wall_temp)
        out = max(qe, 0.0) + max(qi, 0.0)
        e = -p * dVdt + dq - R * T * out
        for q, tin in ((qe, c.t_exhaust), (qi, c.t_intake)):
            if q < 0.0:
                cvm = R / (gamma(0.5 * (T + tin), gc) - 1.0)
                e += -q * (cvm * (tin - T) + R * tin)
        return (-(qe + qi), e / (m * cv), -(mb / m) * out + max(-qe, 0.0))

    def rk4(self, t, y, h):
        def add(a, b, k):
            return tuple(ai + k * bi for ai, bi in zip(a, b))
        k1 = self.rhs(t, y)
        k2 = self.rhs(t + 0.5 * h, add(y, k1, 0.5 * h))
        k3 = self.rhs(t + 0.5 * h, add(y, k2, 0.5 * h))
        k4 = self.rhs(t + h, add(y, k3, h))
        return tuple(yi + h / 6.0 * (a + 2.0 * b + 2.0 * c_ + d)
                     for yi, a, b, c_, d in zip(y, k1, k2, k3, k4))

    def run(self, y0, n, dt, sub):
        """n core samples of dt, each in `sub` Runge-Kutta steps.  The burn
        starts and ends exactly where it should, inside a step if need be.
        Returns p, T, m at each sample's end and the flows at its middle."""
        h = dt / sub
        y = tuple(y0)
        # times (from the start) of the burn's start and end in this cycle
        t_start = ((self.start - self.theta0) % 720.0) / self.omega
        t_end = t_start + self.len / self.omega
        events = [(t_start, "start"), (t_end, "end")]
        P = np.zeros(n)
        T = np.zeros(n)
        M = np.zeros(n)
        QE = np.zeros(n)
        QI = np.zeros(n)
        t = 0.0
        for i in range(n):
            for j in range(sub):
                if j == sub // 2:
                    QE[i], QI[i], _ = self.flows(t, y)
                t1 = (i * sub + j + 1) * h
                while events and events[0][0] <= t1:
                    te, what = events.pop(0)
                    if te > t:
                        y = self.rk4(t, y, te - t)
                        t = te
                    if what == "start":
                        fresh = max(y[0] - y[2], 0.0)
                        self.q_total = (fresh / (self.c.lambda_ * AFR) * LHV * ETA
                                        * min(self.c.lambda_, 1.0))
                        self.burning = True
                    else:
                        self.burning = False
                        y = (y[0], y[1], y[0])
                if t1 > t:
                    y = self.rk4(t, y, t1 - t)
                    t = t1
            M[i], T[i] = y[0], y[1]
            P[i] = y[0] * R * y[1] / self.s.volume(self.theta(t))
        return {"p": P, "t": T, "m": M, "q_exhaust": QE, "q_intake": QI}


def against_reference(spec, ctl, rate, label):
    """Core at `rate` against the reference over one cycle.  Both start from
    the core's state in mid-compression (valves shut, nothing burning)."""
    spec.wall_refresh_deg = 0.0
    core = corelib.Core(spec, rate)
    core.prime(ctl, 10.0)
    # stop in the compression stroke, after the intake valve has shut
    ivc = (spec.intake.open_deg + spec.intake.span_deg) % 720.0
    while not (ivc + 8.0 < core.crank_deg < ivc + 12.0):
        core.run(1)
    st = core.cylinder(0)
    theta0 = core.crank_deg
    n = int(round(120.0 / ctl.rpm * rate))
    got = core.run(n)
    core.close()
    ref = Reference(spec, ctl, theta0)
    want = ref.run((st["m"], st["t"], st["m_burned"]), n, 1.0 / rate, int(round(384000.0 / rate)))
    ep = float(np.max(np.abs(got["p"] / want["p"] - 1.0)))
    peak = float(np.max(np.abs(want["q_exhaust"])))
    eq = float(np.max(np.abs(got["q_exhaust"] - want["q_exhaust"])) / peak)
    rms = float(np.sqrt(np.mean((got["q_exhaust"] - want["q_exhaust"]) ** 2)) / peak)
    em = abs(got["q_exhaust"].sum() / want["q_exhaust"].sum() - 1.0)
    print("  %-26s %7.0f Hz   p %.2e   flow max %.2e  rms %.2e   mass %.2e"
          % (label, rate, ep, eq, rms, em))
    return ep, eq, rms, em


def reference():
    print("1. the core against the same equations integrated by Runge-Kutta at 384 kHz")
    print("   (worst relative error over one cycle: cylinder pressure; exhaust-valve flow")
    print("    over its peak, worst and rms; exhausted mass)")
    eng = presets.audi_a3_35tfsi_ea211evo()
    points = [
        ("2000 rpm, 1.8 bar", corelib.control(2000.0, 1.8 * P_ATM, 320.0, 1.5e5, 1000.0)),
        ("1500 rpm, 0.35 bar", corelib.control(1500.0, 0.35 * P_ATM, 310.0, 1.03e5, 800.0)),
        ("6000 rpm, 1.6 bar", corelib.control(6000.0, 1.6 * P_ATM, 320.0, 1.5e5, 1100.0)),
    ]
    ok = True
    for label, ctl in points:
        rows = []
        for rate in (48000.0, 96000.0, 192000.0):
            spec = core_spec.spec_from_engine(eng, cylinders=1)
            rows.append(against_reference(spec, ctl, rate, label))
        # first order in the step: four times the rate, at most 0.4 of the error
        for k, name, at48, at192 in ((0, "pressure", 2.5e-2, 8.0e-3), (2, "flow rms", 1.0e-2, 3.0e-3)):
            a, b = rows[0][k], rows[2][k]
            if not (a < at48 and b < at192 and b < 0.4 * a):
                ok = False
                print("     MISSED: %s error %.2e at 48 kHz, %.2e at 192 kHz" % (name, a, b))
        if rows[0][3] > 2.0e-3:
            ok = False
            print("     MISSED: exhausted mass off by %.2e" % rows[0][3])
    print("   -> %s\n" % ("converges on the reference, first order in the step" if ok else "FAILED"))
    return ok


# --------------------------------------------------------------------------
# 2. gas_truth
# --------------------------------------------------------------------------

def truth_cycle(eng, rpm, p_intake, t_intake, p_back, dphi=0.5):
    """One settled cycle of gas_truth's cylinder, instrumented: flows (kg/s),
    cylinder pressure and temperature, and the burn's heat, against this
    module's crank angle (firing TDC = 0)."""
    cyl = gas_truth.TruthCylinder(eng, rpm=rpm, p_intake=p_intake, t_intake=t_intake,
                                  p_exh_back=P_ATM)
    cyl.k_throttle *= gas_truth.PLATE_OPEN
    heat = []
    burn = cyl.burn

    def recording_burn(dt):
        q = burn(dt)
        heat.append(q)
        return q
    cyl.burn = recording_burn

    def one(rec):
        rows = []
        phi = 0.0
        while phi < 720.0 - 1e-9:
            del heat[:]
            fi, fe = cyl.step(phi + dphi, dphi, rpm, 1.0)
            if rec:
                rows.append((phi + dphi, fi, fe, cyl.gas.pressure(), cyl.gas.temperature(),
                             cyl.gas.mass(), sum(heat), cyl.exhaust_runner.pressure()))
            phi += dphi
        return rows
    for _ in range(gas_truth.START_CYCLES):
        one(False)
    cyl.p_exh_back = p_back
    for _ in range(10):
        one(False)
    rows = np.array(one(True))
    theta = (rows[:, 0] - 360.0) % 720.0
    order = np.argsort(theta)
    rows, theta = rows[order], theta[order]
    per_deg_to_s = 6.0 * rpm / dphi * gas_truth.AIR_MOLAR_MASS      # mol per step -> kg/s
    return {"theta": theta, "q_intake": rows[:, 1] * per_deg_to_s,
            "q_exhaust": rows[:, 2] * per_deg_to_s, "p": rows[:, 3], "t": rows[:, 4],
            "m": rows[:, 5], "heat": rows[:, 6], "p_runner": rows[:, 7], "cyl": cyl,
            "dphi": dphi}


def burn_of(tr):
    """(total J, CA50, 10-90 %) of a heat-release trace over theta (firing TDC = 0)."""
    th = np.where(tr["theta"] > 360.0, tr["theta"] - 720.0, tr["theta"])
    order = np.argsort(th)
    th, q = th[order], tr["heat"][order]
    total = q.sum()
    if total <= 0.0:
        return 0.0, 0.0, 0.0
    cum = np.cumsum(q) / total
    at = lambda f: float(np.interp(f, cum, th))
    return float(total), at(0.5), at(0.9) - at(0.1)


def event_figures(theta, q, p, t, evo, span, rpm):
    """What one exhaust event looks like: state at opening, peak, where, how much."""
    d = (theta - evo) % 720.0
    inside = d < span
    order = np.argsort(d[inside])
    dd, qq = d[inside][order], q[inside][order]
    i0 = int(np.argmin(np.abs(d)))
    step = 720.0 / len(theta)
    mass = qq.sum() * step / (6.0 * rpm)
    early = qq[dd < 0.25 * span].sum() / qq.sum()
    return {"p_evo": p[i0], "t_evo": t[i0], "peak": qq.max(), "peak_at": float(dd[int(np.argmax(qq))]),
            "mass": mass, "early": early}


def truth():
    print("2. the core against gas_truth (engine-sim's gas model), same valves, same burn")
    print("   They are NOT the same equations.  gas_truth:")
    print("     - takes the gas's internal energy out with what leaves, not its enthalpy:")
    print("       the gas left in the cylinder does not cool as it empties;")
    print("     - keeps gamma at 1.4 (the core: 1.35 cold to 1.2 burned);")
    print("     - loses heat to the walls at a constant 100 W/m2K (the core: Hohenberg);")
    print("     - discharges into a lumped runner whose pressure rises with the pulse")
    print("       (the core, at this stage: a port held at the back-pressure);")
    print("     - rams the intake charge with the runner gas's impact pressure, which it")
    print("       lets reach 1.58 x static: its volumetric efficiency comes out at 1.3-1.5.")
    print("       (The core breathing through the same valves: the 'own VE' column.  The")
    print("       runtime never used gas_truth's raw charge either -- ve_max is re-solved.)")
    print("   So the core is given gas_truth's trapped charge and its burn, and the two")
    print("   EXHAUST EVENTS are compared.")
    eng = presets.audi_a3_35tfsi_ea211evo()
    ok = True
    hdr = "   %-20s %-9s %8s %8s %9s %9s %8s %7s %7s" % (
        "", "", "p_EVO", "T_EVO", "peak", "peak at", "mass", "early", "own VE")
    print(hdr)
    print("   %-20s %-9s %8s %8s %9s %9s %8s %7s %7s" % ("", "", "bar", "K", "g/s", "deg", "mg", "", ""))
    for rpm, p_in, p_back in ((2000.0, 1.0 * P_ATM, 1.05e5), (2000.0, 1.8 * P_ATM, 1.5e5),
                              (4500.0, 1.6 * P_ATM, 1.5e5), (1500.0, 0.40 * P_ATM, 1.03e5)):
        t_in = 320.0
        tr = truth_cycle(eng, rpm, p_in, t_in, p_back)
        q_total, ca50, dur = burn_of(tr)
        spec = core_spec.spec_from_engine(eng, cylinders=1, lift=core_spec.LIFT_TRUTH,
                                          truth=tr["cyl"])
        evo, span = spec.exhaust.open_deg, spec.exhaust.span_deg
        ft = event_figures(tr["theta"], tr["q_exhaust"], tr["p"], tr["t"], evo, span, rpm)
        # what gas_truth inducts per cycle, and what a cylinder-full at the
        # manifold's density would be
        charge = float(tr["q_intake"].sum() * tr["dphi"] / (6.0 * rpm))
        full = p_in / (R * t_in) * spec.swept
        ft["ve"] = charge / full

        # the core at the same point: the truth's burn phasing, the port at the
        # truth's mean runner pressure, and the same heat (through lambda)
        p_port = float(tr["p_runner"].mean())
        t_port = float(tr["t"][(tr["theta"] > 200) & (tr["theta"] < 340)].mean())
        # (its own breathing first, for the VE column)
        core = corelib.Core(spec, 48000.0)
        core.prime(corelib.control(rpm, p_in, t_in, p_port, min(t_port, 1500.0),
                                   ca50_deg=ca50, burn_deg=max(dur, 8.0)), 10.0)
        core.clear_ledgers()
        core.run(int(round(4 * 120.0 / rpm * 48000.0)))
        own_ve = core.cylinder(0)["m_intake"] / 4.0 / full
        core.close()
        lam = 1.0
        fc = None
        for _ in range(4):
            ctl = corelib.control(rpm, p_in, t_in, p_port, min(t_port, 1500.0), charge=charge,
                                  lam=lam, ca50_deg=ca50, burn_deg=max(dur, 8.0))
            core = corelib.Core(spec, 192000.0)
            core.prime(ctl, 10.0)
            while not (evo - 2.0 < core.crank_deg < evo - 1.5):
                core.run(1)
            core.clear_ledgers()
            n = int(round(120.0 / rpm * 192000.0))
            th = np.zeros(n)
            got = {k: np.zeros(n) for k in ("q_exhaust", "p", "t")}
            for i in range(0, n, 64):
                # (in pieces, to read the crank angle along the way)
                a = core.crank_deg
                part = core.run(min(64, n - i))
                m = len(part["p"])
                th[i:i + m] = (a + 6.0 * rpm / 192000.0 * (np.arange(m) + 1)) % 720.0
                for k in got:
                    got[k][i:i + m] = part[k]
            burned = core.cylinder(0)["burn"]
            core.close()
            fc = event_figures(th, got["q_exhaust"], got["p"], got["t"], evo, span, rpm)
            fc["ve"] = own_ve
            fc["heat"] = burned / q_total
            if abs(burned / q_total - 1.0) < 0.01:
                break
            lam = max(lam * burned / q_total, 1.0)

        name = "%.0f rpm, %.2f bar" % (rpm, p_in / 1e5)
        for who, f in (("gas_truth", ft), ("core", fc)):
            print("   %-20s %-9s %8.2f %8.0f %9.1f %9.1f %8.1f %7.2f %7.2f" % (
                name if who == "gas_truth" else "", who, f["p_evo"] / 1e5, f["t_evo"],
                f["peak"] * 1e3, f["peak_at"], f["mass"] * 1e6, f["early"], f["ve"]))
        if abs(fc["heat"] - 1.0) > 0.02:
            print("     (the core's burn released %.2f of gas_truth's heat)" % fc["heat"])
        # the same story: the charge within 15 %, the opening pressure within
        # 35 %, the blowdown's peak within a factor of 1.6
        miss = []
        if abs(fc["mass"] / ft["mass"] - 1.0) > 0.15:
            miss.append("mass")
        if abs(fc["p_evo"] / ft["p_evo"] - 1.0) > 0.35:
            miss.append("p_EVO")
        if not (1.0 / 1.6 < fc["peak"] / ft["peak"] < 1.6):
            miss.append("peak")
        if miss:
            ok = False
            print("     MISSED: " + ", ".join(miss))
    print("   -> %s\n" % ("the two models tell the same story" if ok else "FAILED"))
    return ok


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    ok = True
    if what in ("all", "reference"):
        ok = reference() and ok
    if what in ("all", "truth"):
        ok = truth() and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
