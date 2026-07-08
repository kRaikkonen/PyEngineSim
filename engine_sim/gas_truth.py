"""
gas_truth — a faithful Python port of AngeTheGreat's engine-sim gas dynamics.

This is the v0.3 WHITE-BOX TRUTH MODEL (see the whitebox-surrogate plan): the
CLOSED-LOOP physics the original C++ simulator runs in real time —

  * GasSystem: a real gas state (moles n, thermal energy E_k, volume V, bulk
    momentum, fuel/inert/O2 mole mix).  P = E/(dof/2 · V), T = E/(dof/2 · n R).
    Ported from include/gas_system.h + src/gas_system.cpp.
  * Compressible ORIFICE FLOW between systems with the exact isentropic
    subsonic/choked law, mole+energy+momentum advection and the
    pressure-equilibrium flow clamp (GasSystem::flowRate / flow).
  * COMBUSTION as a propagating cylindrical flame front: turbulent flame speed
    = f(turbulence/S_L)·S_L with the Metghalchi–Keck laminar burning velocity
    correlation, stoichiometric reaction 25 O2 + 2 C8H16 -> 16 CO2 + 18 H2O
    with the real mole-count change, burning efficiency attenuated by
    turbulence/dilution (src/combustion_chamber.cpp, src/fuel.cpp).
    Turbulence = 0.5 × mean piston speed (scripting/engine_node.h).
  * WALL HEAT LOSS (100 W/m^2 K toward the 90 C coolant), throttle plate with
    cosine flow attenuation + idle bleed circuit, intake plenum / runner /
    cylinder / exhaust-runner / collector as a chain of gas systems
    (src/intake.cpp, src/exhaust_system.cpp).

It runs OFFLINE (seconds per operating point is fine): its sweeps feed the
runtime LUT/MLP surrogates.  Nothing here is imported by the real-time path.

Simplifications vs the C++ (documented, not hidden):
  * ONE representative cylinder (even firing): plenum/collector volumes are
    divided by cylinder count; VE/IMEP match the full bank for even-fire
    engines.  No crank constraint solver — the piston follows our exact
    analytic crank-slider kinematics at a prescribed rpm.
  * Momentum is kept 1-D along each element's flow axis (the C++ always uses
    axis-aligned directions for these elements anyway).
  * Valve lift/flow curves are SYNTHESIZED from real geometry (valve diameter
    from bore, lift = 0.25-0.30 d_v, port flow saturating with the standard
    curtain-area law) because our presets don't carry per-engine flow-bench
    tables — same typed inputs, honest defaults.
"""

from __future__ import annotations

import math

R = 8.31446261815324
ATM = 101325.0
T_AMB = 298.15                    # 25 C
AIR_MOLAR_MASS = 0.02897          # kg/mol (units::AirMolecularMass)
COOLANT_T = 363.15                # 90 C wall/coolant temperature
WALL_H = 100.0                    # W/m^2 K convective wall loss (C++ constant)

# air as engine-sim mixes it: 25% O2 / 75% inert by mole
AIR_MIX = (0.0, 0.75, 0.25)       # (p_fuel, p_inert, p_o2)

# fuel: engine-sim default gasoline
FUEL_MOLAR_MASS = 0.100           # kg/mol
FUEL_ENERGY_DENSITY = 48.1e6      # J/kg
FUEL_MOLECULAR_AFR = 12.5         # mol O2 per mol fuel at stoich (25/2)
MAX_BURN_EFF = 0.8
LOW_EFF_ATTEN = 0.6
MAX_TURB_EFFECT = 2.0
MAX_DILUTION_EFFECT = 50.0


def turb_to_flame_ratio(x):
    """Default turbulence_to_flame_speed_ratio function (es/objects.mr):
    samples (0 -> 3.0) then 1.5x for x >= 5, linearly interpolated."""
    if x <= 0.0:
        return 3.0
    if x >= 5.0:
        return 1.5 * x
    t = x / 5.0
    return 3.0 * (1.0 - t) + 7.5 * t


def laminar_burning_velocity(molecular_afr, T, P):
    """Metghalchi–Keck gasoline correlation (src/fuel.cpp)."""
    er_m = 1.21
    B_m = 0.305                    # m/s
    B_er = -0.549                  # m/s
    er = molecular_afr / FUEL_MOLECULAR_AFR
    alpha = 2.4 - 0.271 * er ** 3.51
    beta = -0.357 + 0.14 * er ** 2.77
    S_L0 = B_m + B_er * (er - er_m) * (er - er_m)
    return S_L0 * (T / 298.0) ** alpha * (P / ATM) ** beta


class GasSystem:
    """Port of the C++ GasSystem (1-D momentum along the element axis)."""

    __slots__ = ("n", "E", "V", "mom", "mix", "dof", "hcr",
                 "choked_limit", "choked_rate", "width", "height")

    def __init__(self, P, V, T, mix=AIR_MIX, dof=5, width=0.1, height=0.1):
        self.dof = dof
        self.hcr = 1.0 + 2.0 / dof
        self.choked_limit = (2.0 / (self.hcr + 1.0)) ** (self.hcr / (self.hcr - 1.0))
        self.choked_rate = math.sqrt(self.hcr) * (2.0 / (self.hcr + 1.0)) ** (
            (self.hcr + 1.0) / (2.0 * (self.hcr - 1.0)))
        self.n = P * V / (R * T)
        self.V = V
        self.E = T * 0.5 * dof * self.n * R
        self.mom = 0.0
        self.mix = list(mix)
        self.width = width
        self.height = height

    # ------------------------------------------------------------- state
    def pressure(self):
        return self.E / (0.5 * self.dof * self.V) if self.V else 0.0

    def temperature(self):
        return self.E / (0.5 * self.dof * self.n * R) if self.n else 0.0

    def mass(self):
        return AIR_MOLAR_MASS * self.n

    def density(self):
        return self.mass() / self.V if self.V else 0.0

    def c(self):
        if self.n <= 0.0 or self.E <= 0.0:
            return 0.0
        return math.sqrt(self.pressure() * self.hcr / self.density())

    def E_per_mol(self):
        return self.E / self.n if self.n else 0.0

    def velocity(self):
        m = self.mass()
        return self.mom / m if m else 0.0

    def dyn_pressure(self, sign):
        """Dynamic (impact) pressure seen looking along +/-axis (C++
        dynamicPressure with the dof-specialised isentropic factor)."""
        if self.n <= 0.0 or self.E <= 0.0:
            return 0.0
        v = sign * self.velocity()
        if v <= 0.0:
            return 0.0
        c2 = self.pressure() * self.hcr / self.density()
        m2 = v * v / c2
        x = 1.0 + 0.5 * (self.hcr - 1.0) * m2
        x_d = x ** self.dof            # (x^dof) then sqrt == x^(dof/2)
        return self.pressure() * (math.sqrt(x_d) - 1.0)

    # ----------------------------------------------------------- changes
    def change_volume(self, dV):
        # C++ changeVolume reduces to W = -P dV (the cbrt dance cancels)
        self.E += -self.pressure() * dV
        self.V += dV

    def change_energy(self, dE):
        self.E += dE
        if self.E < 0.0:
            self.E = 0.0

    def lose_n(self, dn, e_per_mol):
        self.E -= e_per_mol * dn
        self.n -= dn
        if self.n < 0.0:
            self.n = 0.0

    def gain_n(self, dn, e_per_mol, mix):
        n0 = self.n
        n1 = n0 + dn
        self.E += dn * e_per_mol
        if n1 > 0.0:
            for i in range(3):
                self.mix[i] = (self.mix[i] * n0 + dn * mix[i]) / n1
        else:
            self.mix = [0.0, 0.0, 0.0]
        self.n = n1

    def react(self, n, mix):
        """Stoichiometric burn of the lit fraction: 25 O2 + 2 C8H16 ->
        16 CO2 + 18 H2O.  Returns moles of fuel burned."""
        l_fuel = mix[0] * n
        l_o2 = mix[2] * n
        s_fuel = self.mix[0] * self.n
        s_o2 = self.mix[2] * self.n
        s_inert = self.mix[1] * self.n
        ideal_fuel = (2.0 / 25.0) * l_o2
        ideal_o2 = 12.5 * l_fuel
        a_fuel = min(s_fuel, l_fuel, ideal_fuel)
        a_o2 = min(s_o2, l_o2, ideal_o2)
        reactants = a_fuel + a_o2
        products = (34.0 / 27.0) * reactants
        dn = products - reactants
        self.n += dn
        nf = s_fuel - a_fuel
        no = s_o2 - a_o2
        ni = s_inert + products
        nn = self.n
        if nn > 0.0:
            self.mix = [nf / nn, ni / nn, no / nn]
        else:
            self.mix = [0.0, 0.0, 0.0]
        return a_fuel

    # -------------------------------------------------------------- flow
    @staticmethod
    def flow_rate(k, P0, P1, T0, T1, hcr, choked_limit, choked_rate):
        """Exact C++ isentropic orifice molar flow (mol/s), signed + = 0->1."""
        if k == 0.0:
            return 0.0
        if P0 > P1:
            direction, T_up, p_up, p_dn = 1.0, T0, P0, P1
        else:
            direction, T_up, p_up, p_dn = -1.0, T1, P1, P0
        if p_up <= 0.0 or T_up <= 0.0:
            return 0.0
        ratio = p_dn / p_up
        if ratio <= choked_limit:
            fr = choked_rate / math.sqrt(R * T_up)
        else:
            s = ratio ** (1.0 / hcr)
            fr = (2.0 * hcr) / (hcr - 1.0) * s * (s - ratio)
            fr = math.sqrt(max(fr, 0.0) / (R * T_up))
        return direction * p_up * fr * k

    def equilibrium_max_flow(self, other):
        if self.pressure() > other.pressure():
            mf = ((other.V * self.E - self.V * other.E)
                  / (other.V * self.E_per_mol() + self.V * self.E_per_mol()))
            return max(0.0, min(mf, self.n))
        mf = ((other.V * self.E - self.V * other.E)
              / (other.V * other.E_per_mol() + self.V * other.E_per_mol()))
        return min(0.0, max(mf, -other.n))

    @staticmethod
    def flow(k, dt, sys0, sys1, sign0=1.0, A0=None, A1=None):
        """Port of GasSystem::flow(FlowParameters) with 1-D momentum.
        ``sign0`` is the flow axis direction of sys0 -> sys1 along each
        system's own axis.  Returns moles moved (+ = 0 -> 1)."""
        P0 = sys0.pressure() + sys0.dyn_pressure(sign0)
        P1 = sys1.pressure() + sys1.dyn_pressure(-sign0)
        if P0 > P1:
            src, snk, d = sys0, sys1, 1.0
            srcA = A0 if A0 is not None else src.width * src.height
            snkA = A1 if A1 is not None else snk.width * snk.height
        else:
            src, snk, d = sys1, sys0, -1.0
            srcA = A1 if A1 is not None else src.width * src.height
            snkA = A0 if A0 is not None else snk.width * snk.height
        flow = dt * GasSystem.flow_rate(
            k, max(P0, P1), min(P0, P1), src.temperature(), snk.temperature(),
            src.hcr, src.choked_limit, src.choked_rate)
        flow = min(max(flow, 0.0), 0.9 * src.n) if src.n > 0 else 0.0
        if flow <= 0.0:
            return 0.0
        frac = flow / src.n
        frac_vol = frac * src.V
        frac_mass = frac * src.mass()

        # stage 1: moles + thermal energy + bulk momentum advection
        bulk0 = (0.5 * src.mom * src.mom / src.mass() if src.mass() else 0.0) \
            + (0.5 * snk.mom * snk.mom / snk.mass() if snk.mass() else 0.0)
        e_mol = src.E_per_mol()
        snk.gain_n(flow, e_mol, src.mix)
        src.lose_n(flow, e_mol)
        dp = src.mom * frac
        src.mom -= dp
        snk.mom += dp
        bulk1 = (0.5 * src.mom * src.mom / src.mass() if src.mass() else 0.0) \
            + (0.5 * snk.mom * snk.mom / snk.mass() if snk.mass() else 0.0)
        snk.E -= (bulk1 - bulk0)

        # stage 2: jet momentum at the orifice (capped at each side's c)
        dirsign = d * sign0
        for sysx, A in ((snk, snkA), (src, srcA)):
            m = sysx.mass()
            if A and m > 0.0:
                v_j = min(max((frac_vol / A) / dt, 0.0), sysx.c())
                p_before = sysx.mom
                sysx.mom += dirsign * v_j * frac_mass
                # energy bookkeeping: bulk KE change comes out of thermal
                sysx.E -= 0.5 / m * (sysx.mom * sysx.mom - p_before * p_before)
        if src.E < 0.0:
            src.E = 0.0
        if snk.E < 0.0:
            snk.E = 0.0
        return flow * d

    def flow_env(self, k, dt, P_env, T_env, mix=AIR_MIX):
        """Flow to/from an infinite environment (C++ GasSystem::flow env)."""
        if self.pressure() > P_env:
            max_flow = -(P_env * (0.5 * self.dof * self.V) - self.E) / self.E_per_mol() \
                if self.n else 0.0
        else:
            e_env = 0.5 * T_env * R * self.dof
            max_flow = -(P_env * (0.5 * self.dof * self.V) - self.E) / e_env
        flow = dt * GasSystem.flow_rate(
            k, self.pressure(), P_env, self.temperature(), T_env,
            self.hcr, self.choked_limit, self.choked_rate)
        if abs(flow) > abs(max_flow):
            flow = max_flow
        if flow < 0.0:                      # inflow from environment
            self.gain_n(-flow, 0.5 * T_env * R * self.dof, mix)
        elif flow > 0.0 and self.n > 0.0:
            frac = flow / self.n
            self.lose_n(flow, self.E_per_mol())
            self.mom -= frac * self.mom
        return flow

    def dissipate_excess_velocity(self):
        v = self.velocity()
        c = self.c()
        if v * v <= c * c or v == 0.0:
            return
        k = c / abs(v)
        self.mom *= k
        self.E += 0.5 * self.mass() * (v * v - c * c)
        if self.E < 0.0:
            self.E = 0.0

    def update_velocity(self, dt, beta=1.0):
        """1-D port of updateVelocity: bulk momentum relaxes against its own
        dynamic-pressure imbalance on the element's end faces."""
        if self.n <= 0.0:
            return
        depth = self.V / (self.width * self.height)
        p_fwd = self.dyn_pressure(1.0) * (self.height * depth)
        p_bwd = self.dyn_pressure(-1.0) * (self.height * depth)
        m = self.mass()
        v0 = self.mom / m
        self.mom -= (p_fwd - p_bwd) * dt * beta
        v1 = self.mom / m
        self.E -= 0.5 * m * (v1 * v1 - v0 * v0)
        if self.E < 0.0:
            self.E = 0.0


def k_flow_from_scfm(flow_scfm, pressure_drop=28.0 * 249.088889):
    """Port of GasSystem::k_28inH2O — flow constant from a flow-bench figure
    (SCFM at 28 inH2O by default)."""
    scfm_to_m3s = 0.0004719474432
    target = flow_scfm * scfm_to_m3s * (ATM / (R * T_AMB))  # mol/s at std density
    hcr = 1.4
    p0 = ATM
    pT = ATM - pressure_drop
    ratio = pT / p0
    choked_limit = (2.0 / (hcr + 1.0)) ** (hcr / (hcr - 1.0))
    if ratio <= choked_limit:
        fr = math.sqrt(hcr) * (2.0 / (hcr + 1.0)) ** ((hcr + 1.0) / (2.0 * (hcr - 1.0)))
    else:
        fr = (2.0 * hcr) / (hcr - 1.0) * (1.0 - ratio ** ((hcr - 1.0) / hcr))
        fr = math.sqrt(fr) * ratio ** (1.0 / hcr)
    fr *= p0 / math.sqrt(R * T_AMB)
    return target / fr


class TruthCylinder:
    """One representative cylinder + its runner gas systems + flame model —
    the CombustionChamber port, driven by prescribed crank kinematics."""

    def __init__(self, eng, spark_advance_fn=None, boost_bar=0.0):
        cyl = eng.cylinders[0]
        self.bore = cyl.bore
        self.stroke = cyl.stroke
        self.rod = cyl.rod_length
        self.cr = cyl.compression_ratio
        self.Vd = cyl.displacement
        self.Vc = cyl.clearance_volume
        self.A_bore = math.pi * 0.25 * self.bore * self.bore
        self.ncyl = eng.num_cylinders

        # --- synthesized head geometry (typed white-box defaults) -----------
        n_int = max(1, getattr(eng, "valves_per_cyl", 4) // 2)
        d_iv = self.bore * (0.39 if n_int >= 2 else 0.47)
        d_ev = 0.83 * d_iv
        n_exh = max(1, getattr(eng, "valves_per_cyl", 4) - n_int)
        self.max_lift_i = 0.27 * d_iv
        self.max_lift_e = 0.27 * d_ev
        # peak port flow from curtain area at max lift with Cd ~ 0.6, converted
        # to a flow-bench SCFM figure and then to a flow constant like the C++
        v_bench = math.sqrt(2.0 * 28.0 * 249.088889 / 1.2)   # bench dP velocity
        q_i = n_int * math.pi * d_iv * self.max_lift_i * 0.60 * v_bench
        q_e = n_exh * math.pi * d_ev * self.max_lift_e * 0.62 * v_bench
        self.k_int_max = k_flow_from_scfm(q_i / 0.0004719474432)
        self.k_exh_max = k_flow_from_scfm(q_e / 0.0004719474432)

        # cam: duration by profile, lift = smooth lobe (raised-cosine^2)
        cam = getattr(eng, "cam_profile", "stock")
        dur_i = {"mild": 236.0, "stock": 250.0, "hot": 266.0, "race": 284.0}.get(cam, 250.0)
        dur_e = dur_i + 4.0
        self.dur_i, self.dur_e = dur_i, dur_e
        # centerlines in THIS project's cycle convention (phi=0 is INTAKE TDC,
        # 180 BDC, 360 combustion TDC, 540 exhaust BDC):
        #   intake opens ~15 deg BTDC(0)  -> centre ~ dur/2 - 15
        #   exhaust closes ~10 deg ATDC(720) -> centre ~ 720 - dur/2 + 10
        self.int_center = dur_i * 0.5 - 15.0
        self.exh_center = 720.0 - dur_e * 0.5 + 10.0

        # runner + plenum + collector gas systems (per-cylinder share)
        run_len = max(getattr(eng, "intake_runner_m", 0.30), 0.05)
        a_run_i = math.pi * 0.25 * (1.1 * d_iv) ** 2
        # HEADER PRIMARY BORE: a real tuning choice, not the valve size.  A
        # small-bore primary keeps exhaust-gas VELOCITY high -> strong inertial
        # scavenging + low-end torque; a big bore drops backpressure for the top
        # end.  Use the preset's explicit bore if given, else a sensible
        # valve-derived default.  The bore drives THREE things a real header
        # bore changes: runner volume, gas velocity (via cross-section), and the
        # primary-out flow restriction below.
        a_valve_default = math.pi * 0.25 * (1.15 * d_ev) ** 2
        prim_bore = getattr(eng, "exhaust_primary_bore_m", 0.0)
        a_run_e = (math.pi * 0.25 * prim_bore * prim_bore) if prim_bore > 0.0 \
            else a_valve_default
        self._a_prim_ref = a_valve_default        # reference for flow scaling
        prim_len = max(getattr(eng, "exhaust_primary_m", 0.5), 0.1)
        self.intake_runner = GasSystem(ATM, a_run_i * run_len, T_AMB, AIR_MIX,
                                       width=run_len, height=math.sqrt(a_run_i))
        self.exhaust_runner = GasSystem(ATM, a_run_e * prim_len, T_AMB, AIR_MIX,
                                        width=prim_len, height=math.sqrt(a_run_e))
        self.a_run_i, self.a_run_e = a_run_i, a_run_e
        self.prim_len = prim_len
        # --- 1-D WAVE-ACTION primary (the header-TUNING physics) --------------
        # The lumped runner above captures inertial scavenging but has no finite
        # wave-propagation delay, so it can't produce the RESONANT torque peak a
        # tuned header is famous for.  Model the primary as a travelling-wave
        # element: the blowdown launches an overpressure wave that travels to the
        # collector, reflects there as a RAREFACTION (area expansion, R<0) and
        # returns to the valve after 2L/c.  When that rarefaction arrives during
        # the exhaust/overlap window it drops the back-pressure the cylinder
        # exhausts against -> extra scavenging -> a peak at the TUNED rpm (which
        # scales with 1/L, exactly like a real header).  Same delay-line maths as
        # the audio waveguide.
        self._wave_hist = [0.0] * 4096                      # launched-wave ring
        self._wave_i = 0
        # collector/primary area-expansion reflection coefficient (acoustic):
        # R = (A_prim - A_col)/(A_prim + A_col) < 0  -> returns a rarefaction
        a_col_each = math.pi * (getattr(eng, "exhaust_radius_m", 0.03) * 1.6) ** 2
        self._wave_R = (a_run_e - a_col_each) / (a_run_e + a_col_each)
        self._wave_atten = 0.40        # round-trip friction/entropy loss in the tube
        plenum_v = max(eng.total_displacement * 1.2, 0.5e-3) / self.ncyl
        self.plenum = GasSystem(ATM, plenum_v, T_AMB, AIR_MIX,
                                width=0.2, height=math.sqrt(plenum_v / 0.2))
        a_col = math.pi * (getattr(eng, "exhaust_radius_m", 0.03) * 1.6) ** 2
        col_len = max(getattr(eng, "exhaust_total_m", 1.6), 0.5)
        self.collector = GasSystem(ATM, a_col * col_len / self.ncyl, T_AMB, AIR_MIX,
                                   width=col_len, height=math.sqrt(a_col / self.ncyl))
        self.a_col = a_col
        # throttle body / outlet flow constants scaled to engine airflow demand
        peak_scfm = eng.total_displacement * (eng.redline_rpm / 2.0) / 60.0 \
            / 0.0004719474432 * 1.4
        self.k_throttle = k_flow_from_scfm(peak_scfm)
        self.k_runner_feed = k_flow_from_scfm(peak_scfm / self.ncyl * 2.2)
        # the primary-out restriction scales with the primary bore area (a wider
        # header primary passes more into the collector -> less top-end backpressure)
        self.k_primary_out = k_flow_from_scfm(
            peak_scfm / self.ncyl * 2.4 * (a_run_e / self._a_prim_ref))
        self.k_outlet = k_flow_from_scfm(
            peak_scfm * (0.6 + 0.8 * getattr(eng, "exhaust_openness", 0.85)) / self.ncyl)

        # --- FORCED INDUCTION boundary conditions --------------------------
        # The compressor raises the throttle-body feed pressure to (1 + boost)
        # atm and heats the charge by isentropic compression; an intercooler
        # removes most of that heat.  The turbine, extracting the energy to
        # drive it, raises the EXHAUST manifold back-pressure — the pumping
        # penalty that stops boost being free.  For an OFFLINE truth sweep the
        # boost level is an input (the runtime picks it from the P2 turbine-
        # balance table); here it sets the intake/exhaust env pressures.
        self.boost_bar = max(boost_bar, 0.0)
        pr = 1.0 + self.boost_bar / 1.01325            # compressor pressure ratio
        self.p_intake = (1.0 + self.boost_bar) * ATM
        # isentropic compressor outlet temp, then intercooler recovers ~75%
        t_comp = T_AMB * pr ** ((1.4 - 1.0) / 1.4)
        eta_ic = 0.75 if self.boost_bar > 0.0 else 0.0
        self.t_intake = T_AMB + (t_comp - T_AMB) * (1.0 - eta_ic)
        # turbine back-pressure: a modern turbo runs exhaust manifold pressure a
        # bit ABOVE boost (P3/P2 ~ 1.0-1.4).  Scale gently with boost.
        self.p_exh_back = (1.0 + 1.15 * self.boost_bar) * ATM if self.boost_bar > 0 \
            else ATM

        # cylinder gas system
        self.gas = GasSystem(ATM, self.Vc + 0.5 * self.Vd, T_AMB, AIR_MIX,
                             width=math.sqrt(self.A_bore),
                             height=(self.Vc + 0.5 * self.Vd) / self.A_bore)
        # MBT-ish spark schedule: the cylindrical flame front needs a roughly
        # CONSTANT burn angle (turbulence ~ piston speed makes S_T ~ rpm), so
        # advance grows with rpm — same reason the original .mr timing curves
        # reach 50-60 deg at speed.
        self.spark_advance_fn = spark_advance_fn or (
            lambda rpm: min(14.0 + 0.006 * rpm, 56.0))

        # flame state
        self.lit = False
        self.flame = None
        self.piston_speed_acc = []
        self.fired_pressure = 20.0 * ATM

    # ------------------------------------------------------------ kinematics
    def piston_pos(self, theta):
        r, l = self.stroke * 0.5, self.rod
        s = math.sin(theta)
        return (r + l) - (r * math.cos(theta) + math.sqrt(l * l - (r * s) ** 2))

    def volume(self, phi_deg):
        return self.Vc + self.A_bore * self.piston_pos(math.radians(phi_deg % 360.0))

    def lift(self, phi_deg, center, dur, max_lift):
        d = (phi_deg - center) % 720.0
        if d > 360.0:
            d -= 720.0
        x = d / (0.5 * dur)
        if abs(x) >= 1.0:
            return 0.0
        c = 0.5 * (1.0 + math.cos(math.pi * x))
        return max_lift * c * c

    def valve_k(self, lift, max_lift, k_max):
        """Port flow constant vs lift: linear curtain growth saturating near
        max lift (the standard flow-bench curve shape)."""
        if lift <= 0.0:
            return 0.0
        x = lift / max_lift
        return k_max * min(1.0, 1.25 * x - 0.25 * x * x)

    # ------------------------------------------------------------ combustion
    def ignite(self, rpm):
        if self.lit or self.gas.mix[0] <= 0.0:
            return
        afr = self.gas.mix[2] / self.gas.mix[0]
        er = afr / FUEL_MOLECULAR_AFR
        if er < 0.5 or er > 1.9:
            return
        ideal_inert = self.gas.mix[2] / 0.7
        dilution = (self.gas.mix[1] / ideal_inert) - 1.0 if ideal_inert > 0 else 0.0
        mean_ps = 2.0 * self.stroke * rpm / 60.0
        turbulence = 0.5 * mean_ps
        mixing = 1.0 - (min(max(turbulence / MAX_TURB_EFFECT, 0.0), 1.0)
                        * min(max(1.0 - dilution / MAX_DILUTION_EFFECT, 0.0), 1.0))
        rand_s = LOW_EFF_ATTEN * (0.5 + 0.5 * 0.5)   # deterministic mid randomness
        eff = (mixing * rand_s + (1.0 - mixing)) * MAX_BURN_EFF
        S_L = laminar_burning_velocity(afr, self.gas.temperature(),
                                       self.gas.pressure())
        S_T = turb_to_flame_ratio(turbulence / max(S_L, 1e-6)) * S_L
        self.flame = {
            "last_V": self.gas.V, "tx": 0.0, "ty": 0.0,
            "eff": eff, "speed": S_T, "mix": list(self.gas.mix),
        }
        self.lit = True

    def burn(self, dt):
        if not self.lit:
            return 0.0
        f = self.flame
        V = self.gas.V
        total_x = self.bore * 0.5
        total_y = V / self.A_bore
        expansion = V / f["last_V"]
        lx, ly = f["tx"], f["ty"] * expansion
        f["tx"] = min(lx + dt * f["speed"], total_x)
        f["ty"] = min(ly + dt * f["speed"], total_y)
        released = 0.0
        if lx < f["tx"] or ly < f["ty"]:
            burned_V = f["tx"] * f["tx"] * math.pi * f["ty"]
            prev_V = lx * lx * math.pi * ly
            lit_V = burned_V - prev_V
            n = (lit_V / V) * self.gas.n
            fuel_burned = self.gas.react(n * f["eff"], f["mix"])
            released = fuel_burned * FUEL_MOLAR_MASS * FUEL_ENERGY_DENSITY
            self.gas.change_energy(released)
        else:
            self.lit = False
        f["last_V"] = V
        return released

    # ------------------------------------------------------------- one step
    def step(self, phi_deg, dphi_deg, rpm, throttle):
        omega = rpm * 2.0 * math.pi / 60.0
        dt = math.radians(dphi_deg) / omega

        # piston motion -> volume change (does compression/expansion work)
        V_new = self.volume(phi_deg)
        self.gas.change_volume(V_new - self.gas.V)
        self.gas.width = math.sqrt(self.A_bore)
        self.gas.height = max(V_new / self.A_bore, 1e-4)

        # wall heat loss toward the 90 C coolant (C++ constant law)
        height = V_new / self.A_bore
        area = height * math.pi * self.bore + 2.0 * self.A_bore
        self.gas.change_energy((COOLANT_T - self.gas.temperature())
                               * area * WALL_H * dt)

        # spark
        adv = self.spark_advance_fn(rpm)
        d_spark = (phi_deg - (360.0 - adv)) % 720.0
        if 0.0 <= d_spark < dphi_deg * 1.5:
            self.ignite(rpm)
        self.burn(dt)

        # --- gas exchange chain (plenum <-> runner <-> cyl <-> primary <-> collector)
        # butterfly plate: cos gives the projected opening; the 4th power
        # matches a real plate's effective-flow-area curve (a plate at 65 deg
        # passes a few % of full flow, not 40%) — the C++ gets the same effect
        # from its nonlinear pedal->plate linkage in the .mr files.
        thr_plate = (1.0 - throttle) * 0.97
        k_thr = (math.cos(thr_plate * math.pi * 0.5) ** 4) \
            * self.k_throttle / self.ncyl
        # throttle-body feed is at the COMPRESSOR outlet pressure/temperature
        # under boost (self.p_intake/self.t_intake == ambient when NA)
        self.plenum.flow_env(k_thr, dt, self.p_intake, self.t_intake,
                             self._charge_mix(throttle))
        self.plenum.flow_env(self.k_throttle * 0.004 / self.ncyl, dt,
                             self.p_intake, self.t_intake,
                             self._charge_mix(1.0))          # idle bleed
        GasSystem.flow(self.k_runner_feed, dt, self.plenum, self.intake_runner,
                       A0=self.plenum.width * self.plenum.height, A1=self.a_run_i)
        self.intake_runner.dissipate_excess_velocity()

        l_i = self.lift(phi_deg, self.int_center, self.dur_i, self.max_lift_i)
        k_iv = self.valve_k(l_i, self.max_lift_i, self.k_int_max)
        intake_flow = GasSystem.flow(k_iv, dt, self.intake_runner, self.gas,
                                     A0=self.a_run_i, A1=self.A_bore)
        self.intake_runner.dissipate_excess_velocity()
        self.gas.dissipate_excess_velocity()
        if abs(intake_flow) > 1e-12 and self.lit:
            self.lit = False                                  # C++: intake kills flame

        # --- WAVE-ACTION (header tuning): the rarefaction that reflected off the
        # collector 2L/c ago modulates the exhaust-valve SCAVENGING as it arrives
        # at the port.  Implemented as a BOUNDED flow modulation (not an energy
        # injection — that positive-feedback-loops and blows the gas state up):
        # a returned rarefaction (returned<0) raises the pressure drop across the
        # open valve -> more scavenging; a returned compression wave chokes it.
        # The 2L/c delay makes this help only near the TUNED rpm (peak ~ 1/L, the
        # real header behaviour).
        l_e = self.lift(phi_deg, self.exh_center, self.dur_e, self.max_lift_e)
        c_exh = self.exhaust_runner.c()
        if c_exh > 1.0:
            delay = int(round((2.0 * self.prim_len / c_exh) / max(dt, 1e-9)))
            delay = min(max(delay, 1), len(self._wave_hist) - 1)
            past = self._wave_hist[(self._wave_i - delay) % len(self._wave_hist)]
            returned = self._wave_R * self._wave_atten * past    # R<0 -> rarefaction
            # the wave only couples to the cylinder while the exhaust valve is
            # OPEN (that is when tuning acts); perturbing the runner with the
            # valve shut only destabilises it.  Gate on valve lift, apply the
            # returned wave as a pressure perturbation (dE = dP·V·dof/2) CLAMPED
            # to 12% of the runner energy, and clamp the resulting pressure to a
            # physical band so a resonant build-up can never blow the state up.
            gate = min(l_e / (0.15 * self.max_lift_e), 1.0)
            if gate > 0.0:
                dE = (returned * self.exhaust_runner.V
                      * self.exhaust_runner.dof * 0.5) * gate
                cap = 0.12 * self.exhaust_runner.E
                self.exhaust_runner.change_energy(min(max(dE, -cap), cap))
                p_now = self.exhaust_runner.pressure()
                lo, hi = 0.25 * ATM, 6.0 * ATM
                if p_now < lo or p_now > hi:
                    tgt = min(max(p_now, lo), hi)
                    self.exhaust_runner.change_energy(
                        (tgt - p_now) * self.exhaust_runner.V
                        * self.exhaust_runner.dof * 0.5)
            self._wave_hist[self._wave_i] = self.exhaust_runner.pressure() - ATM
            self._wave_i = (self._wave_i + 1) % len(self._wave_hist)

        k_ev = self.valve_k(l_e, self.max_lift_e, self.k_exh_max)
        exhaust_flow = GasSystem.flow(k_ev, dt, self.gas, self.exhaust_runner,
                                      A0=self.A_bore, A1=self.a_run_e)
        self.gas.dissipate_excess_velocity()
        self.exhaust_runner.dissipate_excess_velocity()

        GasSystem.flow(self.k_primary_out, dt, self.exhaust_runner, self.collector,
                       A0=self.a_run_e, A1=self.a_col / self.ncyl)
        self.exhaust_runner.dissipate_excess_velocity()

        # collector discharges against TURBINE back-pressure (== ambient when NA)
        self.collector.flow_env(self.k_outlet, dt, self.p_exh_back, T_AMB)
        self.collector.dissipate_excess_velocity()

        self.intake_runner.update_velocity(dt, 0.5)
        self.gas.update_velocity(dt, 0.5)
        self.exhaust_runner.update_velocity(dt, 0.25)
        self.collector.update_velocity(dt, 0.25)

        return intake_flow, exhaust_flow

    def _charge_mix(self, throttle):
        """Premixed charge entering the plenum (C++ Intake::process): air with
        fuel at the target AFR (ideal_afr = 0.8 * molecularAfr * 4 = 40 by mole
        of air)."""
        ideal_afr = 0.8 * FUEL_MOLECULAR_AFR * 4.0
        p_air = ideal_afr / (1.0 + ideal_afr)
        return ((1.0 - p_air), p_air * 0.75, p_air * 0.25)


def measure_operating_point(eng, rpm, throttle, spark_advance_fn=None,
                            dphi=2.0, max_cycles=12, tol=0.01, min_warmup=3,
                            boost_bar=None):
    """Run the truth model at one operating point until the cycle converges.
    Returns dict(ve, imep, torque, p_peak, T_peak, map_frac, blowdown_p,
    exhaust_wave) — the quantities the surrogate tables are baked from.

    The chamber starts cold/empty at ambient, so the first few cycles are a
    startup transient in which the flame may not yet catch (VE low, motoring
    torque).  Convergence is therefore only accepted AFTER ``min_warmup``
    cycles AND once the cycle is actually firing — otherwise the flat
    warmup phase (two near-identical motoring cycles) would false-trigger the
    tolerance check and return a non-firing state (the high-rpm ``-9 Nm``
    artifact)."""
    # boost defaults to the engine's rated boost gated by a SPOOL ramp (a turbo
    # makes no boost at idle and reaches full boost up top) so the truth bake
    # isn't full-boost-at-1500rpm.  An explicit schedule can bake a 3-D table.
    if boost_bar is None:
        b_max = getattr(eng, "boost_bar", 0.0)
        if b_max > 0.0 and throttle > 0.15:
            rf = rpm / max(eng.redline_rpm, 1.0)
            spool = (rf - getattr(eng, "turbo_spool_frac", 0.12)) \
                / max(getattr(eng, "turbo_spool_width", 0.5), 1e-3)
            boost_bar = b_max * min(max(spool, 0.0), 1.0) * min(throttle / 0.6, 1.0)
        else:
            boost_bar = 0.0
    cyl = TruthCylinder(eng, spark_advance_fn, boost_bar=boost_bar)
    rho_ref = ATM / (R * T_AMB)                # ambient molar density (VE ref)
    n_ideal = rho_ref * cyl.Vd                 # ideal trapped charge, mol
    last_imep = None
    result = None
    for cycle in range(max_cycles):
        work = 0.0
        intake_n = 0.0
        p_peak = 0.0
        T_peak = 0.0
        blowdown_p = 0.0
        fired = False
        wave = []
        phi = 0.0
        while phi < 720.0:
            V0 = cyl.gas.V
            p_mid = cyl.gas.pressure()
            was_lit = cyl.lit
            fi, fe = cyl.step(phi + dphi, dphi, rpm, throttle)
            if cyl.lit or was_lit:
                fired = True
            work += p_mid * (cyl.gas.V - V0)
            intake_n += fi          # NET flow: low-rpm pushback subtracts (real
            #                         long-cam VE loss), overlap reverse too
            p = cyl.gas.pressure()
            T = cyl.gas.temperature()
            if p > p_peak:
                p_peak = p
            if T > T_peak:
                T_peak = T
            e_open = (phi - (cyl.exh_center - cyl.dur_e * 0.5)) % 720.0
            if e_open < dphi * 1.5:
                blowdown_p = p
            wave.append(fe)
            phi += dphi
        imep = work / cyl.Vd
        ve = intake_n / n_ideal
        result = {
            "ve": ve, "imep": imep,
            "torque": work * cyl.ncyl / (4.0 * math.pi),
            "p_peak": p_peak, "T_peak": T_peak,
            "map_frac": cyl.plenum.pressure() / ATM,
            "blowdown_p": blowdown_p,
            "exhaust_wave": wave,
            "cycles": cycle + 1, "fired": fired,
        }
        converged = (last_imep is not None
                     and abs(imep - last_imep) < tol * max(abs(imep), 1e3))
        # accept only a settled, FIRING cycle (unless the engine genuinely
        # cannot run here — then let max_cycles end it on the honest no-fire state)
        if cycle + 1 >= min_warmup and converged and (fired or throttle < 0.05):
            break
        last_imep = imep
    return result
