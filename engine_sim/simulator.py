"""
The real-time simulator: thermodynamics + crankshaft dynamics.

Each step we

  1. work out, for every cylinder, where it is in its 720 deg four-stroke cycle,
  2. compute the in-cylinder gas pressure from a simple but physical model
     (intake -> adiabatic compression -> heat release -> adiabatic expansion ->
     exhaust),
  3. turn that pressure into a torque on the crankshaft via the slider-crank
     torque arm (virtual work: T = F * d(displacement)/d(theta)),
  4. add starter / friction / external-load torques, and
  5. integrate the crankshaft's angular velocity (semi-implicit Euler) with
     adaptive sub-stepping so the cycle stays well resolved at high rpm.

The pressure model is open-loop (it does not feed combustion back into the gas
state cycle-to-cycle), which keeps it numerically rock solid while still giving
a realistic torque curve, idle, and throttle response.
"""

from __future__ import annotations

import math

import numpy as np

from .engine import Engine, P_ATM
from .drivetrain import Drivetrain
from .units import rads_to_rpm

GAMMA = 1.30          # ratio of specific heats for burned gas (~1.3)
# Physical peak-cylinder-pressure ceiling (Pa).  The open-loop Otto model lets a
# HIGH compression ratio multiply with boost into absurd peak pressures (a diesel
# at CR~20 + boost computes ~1300 bar, ~8x its real torque).  Real engines are
# limited by structure/combustion to a few hundred bar, so we clamp here.  Set
# well ABOVE petrol peaks (~100-180 bar even boosted) so only the broken
# high-CR/high-boost (diesel) cases are touched.  Used for TORQUE only — the
# audio's blowdown model is separate and stays as tuned.
P_PEAK_CAP = 3.0e7    # ~300 bar (well above petrol ~120-180 bar peaks; clamps the
                      # broken diesel/high-CR cases: ~8x real -> ~0.9x)
TWO_PI = 2.0 * math.pi
DEG = math.pi / 180.0

# Cycle landmarks, in degrees of the 720 deg cycle, measured so that phi = 0 is
# the intake-stroke TDC and phi = 360 is the (power) combustion TDC.
INTAKE = 0.0
COMPRESSION = 180.0
COMBUSTION_TDC = 360.0
POWER = 360.0
EXHAUST_OPEN = 520.0   # exhaust valve cracks open near end of power stroke
EXHAUST = 540.0


class Simulator:
    """Holds the live state of one engine and advances it through time."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.drivetrain = Drivetrain(engine)

        # --- live state ---
        self.crank_angle = 0.0          # rad, accumulates without wrapping
        self.omega = 0.0                # rad/s, crankshaft angular velocity
        self.ignition_on = True
        self.starter_engaged = False
        self.throttle = 0.0             # 0..1 driver demand
        self.external_load = 0.0        # N*m resisting torque (e.g. a dyno)
        self._fuel_cut = False          # rev-limiter state (with hysteresis)
        self._shift_cut = False         # ignition cut during a gearshift
        self.boost = 0.0                # forced-induction boost (bar gauge)
        self._idle_trim = engine.idle_air_base  # idle-governor air, 0..~0.3
        self.hybrid_on = True           # electric-motor assist enabled (hybrids)
        # Max physics sub-steps per frame.  The crank-angle integration sub-steps
        # for torque-pulse resolution; the flywheel smooths pulses at high rpm so a
        # coarser cap barely moves the rpm TRAJECTORY (and the audio runs its own
        # crank, independent of this).  Normal mode keeps the fine 80; Low-Q (phones)
        # lowers it to slash the high-rpm physics cost.  Set by the app per mode.
        self.substep_cap = 80

        # --- telemetry (updated every step) ---
        self.gas_torque = 0.0
        self.friction_torque = 0.0
        self.motor_torque = 0.0         # electric-motor assist torque (hybrids)
        self.cylinder_pressure = np.full(engine.num_cylinders, P_ATM)

        # precompute per-cylinder cycle offset in radians of the *cycle*
        self._offset_deg = np.array([c.cycle_offset_deg for c in engine.cylinders])

        # Size the clutch so it can actually HOLD this engine.  A real clutch is
        # rated ABOVE peak torque; if the (boosted) engine makes more torque than
        # the clutch can pass, the coupling saturates and the light flywheel just
        # free-revs to the limiter no matter the road speed — the "stab the
        # throttle and it instantly rushes to redline, even after an upshift" bug.
        # We sweep WOT+full-boost torque once at load and lift the capacity to
        # cover it (keeping the preset value if it's already higher).
        try:
            peak = self._peak_wot_torque()
            self.drivetrain.clutch_capacity = max(
                self.drivetrain.clutch_capacity, peak * 1.25)
        except Exception:
            pass

    def _peak_wot_torque(self) -> float:
        """Approximate peak MEAN crank torque at wide-open throttle and full
        boost, by sweeping rpm and averaging the gas torque over a 720 deg cycle.
        Restores all live state before returning (pure measurement)."""
        eng = self.engine
        saved = (self.throttle, self.boost, self.omega, self.crank_angle,
                 self._fuel_cut, self._shift_cut)
        self.throttle, self.boost = 1.0, eng.boost_bar
        self._fuel_cut = self._shift_cut = False
        best = 0.0
        rpm = max(eng.idle_rpm, 1000.0)
        while rpm <= eng.redline_rpm:
            self.omega = rpm * TWO_PI / 60.0
            tot = 0.0
            for d in range(0, 720, 20):
                self.crank_angle = d * DEG
                tot += self._compute_torque()
            best = max(best, tot / 36.0)
            rpm += 200.0
        (self.throttle, self.boost, self.omega, self.crank_angle,
         self._fuel_cut, self._shift_cut) = saved
        self.cylinder_pressure[:] = P_ATM
        return best

    # ------------------------------------------------------------------ rpm
    @property
    def rpm(self) -> float:
        return rads_to_rpm(self.omega)

    # ----------------------------------------------------- pressure model
    def _effective_throttle(self) -> float:
        """Air demand actually reaching the engine: the larger of the driver's
        pedal and the idle governor's trim, clamped to 0..1."""
        return min(max(self.throttle, self._idle_trim), 1.0)

    def _manifold_pressure(self) -> float:
        """Intake-manifold absolute pressure from effective throttle.

        Closed throttle -> strong vacuum; wide-open -> near atmospheric.
        """
        t = self._effective_throttle()
        idle_map = self.engine.closed_map_fraction * P_ATM
        return idle_map + t * (P_ATM - idle_map) + self.boost * 1.0e5

    def _update_boost(self, dt: float):
        """Advance the forced-induction boost (bar) for the current engine."""
        eng = self.engine
        ind = eng.induction
        if ind == "na" or eng.boost_bar <= 0.0:
            self.boost = 0.0
            return
        thr = self._effective_throttle()
        rf = min(self.rpm / max(eng.redline_rpm, 1.0), 1.0)
        if ind == "roots":                       # positive-displacement: ~instant
            target = eng.boost_bar * thr * min(max(rf / 0.25, 0.25), 1.0)
            rate = min(dt * 18.0, 1.0)
        elif ind == "centrifugal":               # rises with rpm^2, ~instant
            target = eng.boost_bar * thr * (rf * rf)
            rate = min(dt * 14.0, 1.0)
        else:                                     # turbo: spools with lag
            if eng.electric_turbo:
                # e-turbo / e-compressor: an electric motor spins the compressor,
                # so boost is available almost instantly from low rpm, no lag.
                target = eng.boost_bar * thr
                tau = 0.05
                rate = min(dt / tau, 1.0)
                self.boost += (target - self.boost) * rate
                return
            spool = (rf - eng.turbo_spool_frac) / max(eng.turbo_spool_width, 1e-3)
            # A turbo spools on EXHAUST MASS-FLOW, which is high only under LOAD.
            # Free-revving in neutral makes little boost (so the car doesn't just
            # surge to the limiter the instant you blip it); in gear it spools fully.
            load_gate = 1.0 if self.drivetrain.gear > 0 else 0.3
            target = eng.boost_bar * thr * min(max(spool, 0.0), 1.0) * load_gate
            tau = eng.turbo_lag if target > self.boost else 0.18
            rate = min(dt / max(tau, 0.02), 1.0)
        self.boost += (target - self.boost) * rate

    def _electric_motor_torque(self) -> float:
        """Electric-motor assist torque (N*m) for a hybrid.

        Constant torque below ``hybrid_base_rpm`` (the flat low-end shove an EV
        gives), then constant power above it.  Throttle-scaled so it adds with
        the driver's demand.  Returns 0 for a non-hybrid or when switched off."""
        eng = self.engine
        if eng.hybrid_kw <= 0.0 or not self.hybrid_on:
            return 0.0
        if self.rpm >= eng.redline_rpm or self._shift_cut:
            return 0.0                          # no drive past the limiter / mid-shift
        thr = min(max(self.throttle, 0.0), 1.0)
        if thr <= 0.0:
            return 0.0
        p_w = eng.hybrid_kw * 1000.0
        base = max(eng.hybrid_base_rpm, 1.0) * TWO_PI / 60.0
        peak_tq = p_w / base
        if self.omega <= base:
            tq = peak_tq
        else:
            tq = p_w / max(self.omega, 1.0)         # constant power region
        return tq * thr

    def _update_idle_governor(self, h: float):
        """Integral controller that trims idle air to hold ``idle_rpm`` when the
        driver's foot is off the throttle (just like a real idle-air valve)."""
        eng = self.engine
        if self.throttle < 0.05 and self.ignition_on and not self._fuel_cut:
            err = eng.idle_rpm - self.rpm
            self._idle_trim += err * eng.idle_gain * h
        else:
            # Foot on the throttle: relax the trim back toward its base level.
            self._idle_trim += (eng.idle_air_base - self._idle_trim) * 3.0 * h
        # Clamp to a sane idle-air range.
        self._idle_trim = min(max(self._idle_trim, 0.0), 0.35)

    def _volumetric_efficiency(self) -> float:
        """How well the cylinders breathe at the current rpm (0..ve_max).

        A bell curve peaking in the mid-range; this is what gives a real engine
        its torque hump and the fall-off toward idle and redline.
        """
        eng = self.engine
        peak = eng.ve_peak_frac * eng.redline_rpm
        width = eng.ve_width_frac * eng.redline_rpm
        bell = math.exp(-((self.rpm - peak) / width) ** 2)
        return eng.ve_floor + (eng.ve_max - eng.ve_floor) * bell

    def _cylinder_pressure(self, cyl, phi_deg, p_manifold, combusting, ve):
        """Absolute gas pressure (Pa) for one cylinder at cycle angle phi (deg).

        `phi_deg` is taken modulo 720.  `combusting` is True when there is spark
        + air this cycle (i.e. the charge actually burns).
        """
        phi = phi_deg % 720.0

        if phi < COMPRESSION:
            # Intake stroke: cylinder is open to the manifold.
            return p_manifold

        # Volume at the start of compression (BDC) and right now.
        v_bdc = cyl.clearance_volume + cyl.piston_area * cyl.stroke
        theta = (phi % 360.0) * DEG          # crank angle from TDC for kinematics
        v_now = cyl.volume(theta)

        if phi < POWER:
            # Compression stroke: adiabatic from the trapped manifold charge.
            return p_manifold * (v_bdc / v_now) ** GAMMA

        if phi < EXHAUST:
            # Power stroke: start from the compressed (and possibly burned) state
            # at TDC, then expand adiabatically.
            p_compressed = p_manifold * (v_bdc / cyl.clearance_volume) ** GAMMA
            if combusting:
                # Heat release scales with trapped charge mass (~ manifold
                # pressure x how well the engine is breathing): a near-empty or
                # badly-breathing cylinder barely burns, a full one makes a big
                # pressure spike.  The charge-mass factor is CLAMPED at the NA
                # wide-open value (1.0): boost already raises p_compressed
                # linearly, so letting it ALSO grow this multiplier made p_peak
                # scale with manifold pressure SQUARED — wildly overpowering
                # forced-induction (and especially high-CR diesel) engines.
                # Capping it keeps the physical LINEAR torque gain with boost and
                # leaves every NA engine (ratio <= 1 at WOT) untouched.
                k = 1.0 + self.engine.heat_release_k * (p_manifold / P_ATM) * ve
                p_peak = min(p_compressed * k, P_PEAK_CAP)   # physical pressure ceiling
            else:
                p_peak = p_compressed
            return p_peak * (cyl.clearance_volume / v_now) ** GAMMA

        # Exhaust stroke: blown down to ~atmospheric (slight back-pressure).
        return 1.05 * P_ATM

    # --------------------------------------------------------- torque sum
    def _compute_torque(self):
        """Net gas torque on the crank (N*m) at the current crank angle.

        Also refreshes ``self.cylinder_pressure`` telemetry.
        """
        eng = self.engine
        p_manifold = self._manifold_pressure()
        crank_deg = math.degrees(self.crank_angle)
        combusting = self.ignition_on and not self._fuel_cut and not self._shift_cut
        ve = self._volumetric_efficiency()

        total = 0.0
        for i, cyl in enumerate(eng.cylinders):
            phi = (crank_deg + self._offset_deg[i]) % 720.0
            p = self._cylinder_pressure(cyl, phi, p_manifold, combusting, ve)
            self.cylinder_pressure[i] = p

            theta = (phi % 360.0) * DEG
            # Net force on the piston crown (gas above, ~atmosphere in the case).
            force = (p - P_ATM) * cyl.piston_area
            # Virtual work: torque = force * (dx/dtheta).
            total += force * cyl.d_displacement_d_theta(theta)
        # Per-car forced-induction torque trim, BLENDED BY BOOST so it only bites
        # on boost (off-boost/NA torque is untouched).  Audio is unaffected.
        ts = eng.torque_scale
        if ts != 1.0 and eng.boost_bar > 0.0:
            bf = min(self.boost / eng.boost_bar, 1.0)
            total *= 1.0 + (ts - 1.0) * bf
        return total

    # ------------------------------------------------------------- losses
    def _loss_torque(self):
        """Friction + windage + closed-throttle engine braking, opposing motion.

        Engine braking is the big one for *feel*: lift off the throttle and the
        manifold vacuum pumps against the pistons, so the revs fall back to idle
        instead of hanging.  It scales with how shut the throttle is.
        """
        eng = self.engine
        w = self.omega
        closed = 1.0 - self._effective_throttle()
        # Engine braking only bites above idle, so it pulls hanging revs down
        # without fighting the idle governor.
        w_idle = eng.idle_rpm * TWO_PI / 60.0
        over_idle = max(abs(w) - w_idle, 0.0)
        mag = (eng.friction_static
               + eng.friction_linear * abs(w)
               + eng.friction_quad * w * w
               + eng.engine_brake_k * closed * over_idle)
        return math.copysign(mag, w) if w != 0.0 else 0.0

    def _starter_torque(self):
        eng = self.engine
        if not self.starter_engaged:
            return 0.0
        target = eng.starter_speed_rpm * TWO_PI / 60.0
        if self.omega >= target:
            return 0.0
        # Ease off as we approach the starter's free speed.
        return eng.starter_torque * (1.0 - self.omega / target)

    # --------------------------------------------------------------- step
    def step(self, dt: float):
        """Advance the simulation by ``dt`` seconds (real time)."""
        eng = self.engine
        # Rev limiter: cut fuel above the redline, restore once it drops back.
        rpm = self.rpm
        if rpm > eng.redline_rpm:
            self._fuel_cut = True
        elif rpm < eng.redline_rpm - 300.0:
            self._fuel_cut = False

        # During a gearshift, cut combustion and rev-match the engine to the
        # target gear (so an upshift falls instead of bouncing the limiter).
        shifting = self.drivetrain.is_shifting
        self._shift_cut = shifting
        rev_matching = self.drivetrain.rev_matching
        shift_target = self.drivetrain.shift_target_omega() if rev_matching else 0.0

        self._update_boost(dt)

        # Sub-step so the crank never advances more than ~3 deg per integration
        # step; this keeps the sharp combustion torque pulse well resolved.  At high
        # rpm the flywheel smooths the pulses, so we let the step grow coarser there
        # and HARD-CAP the count: ~300 pure-Python sub-steps/frame at the redline was
        # holding the GIL long enough (3-5 ms) to starve the audio callback ->
        # 'tearing' at high revs.  ~80 keeps the rpm dynamics accurate and the step
        # well under the audio block budget.
        speed = max(abs(self.omega), 1.0)
        max_step = (3.0 + 11.0 * min(speed / 1500.0, 1.0)) * DEG
        n = int(dt * speed / max_step) + 1
        n = min(n, self.substep_cap)
        h = dt / n

        for _ in range(n):
            self._update_idle_governor(h)
            self.gas_torque = self._compute_torque()
            self.friction_torque = self._loss_torque()
            self.motor_torque = self._electric_motor_torque()
            starter = self._starter_torque()
            clutch = self.drivetrain.clutch_torque_on_engine(self.omega)

            net = (self.gas_torque + self.motor_torque + starter
                   - self.friction_torque - self.external_load + clutch)
            domega = net / eng.flywheel_inertia * h

            new_omega = self.omega + domega
            # Don't let friction/load push a stopped engine backwards.
            if (not self.starter_engaged and self.gas_torque + clutch <= 0.0
                    and new_omega < 0.0):
                new_omega = 0.0
            self.omega = new_omega

            # Rev-match toward the target gear speed while shifting (DCT/AT).
            if rev_matching and shift_target > 5.0:
                self.omega += (shift_target - self.omega) * min(9.0 * h, 1.0)

            self.crank_angle += self.omega * h
            self.drivetrain.step(self.omega, h)

        self.crank_angle %= (2.0 * TWO_PI)  # keep within one 720 deg cycle

    # ------------------------------------------------------------ audio
    def blowdown_pressure(self) -> float:
        """Cylinder pressure (Pa) at the instant the exhaust valve cracks open.

        This is the strength of each exhaust 'blowdown' pulse — the real source
        of engine sound.  It rises with load (manifold pressure x breathing) and
        collapses on a rev-limit fuel cut, so the audio that samples it inherits
        all of that dynamics straight from the physics.
        """
        cyl = self.engine.cylinders[0]
        p_man = self._manifold_pressure()
        ve = self._volumetric_efficiency()
        combusting = self.ignition_on and not self._fuel_cut

        v_bdc = cyl.clearance_volume + cyl.piston_area * cyl.stroke
        v_tdc = cyl.clearance_volume
        p_comp = p_man * (v_bdc / v_tdc) ** GAMMA
        if combusting:
            k = 1.0 + self.engine.heat_release_k * (p_man / P_ATM) * ve
            p_peak = p_comp * k
        else:
            p_peak = p_comp
        # Expand to the crank angle where the exhaust valve opens (~510 deg).
        theta = math.radians(510.0 % 360.0)
        v_open = cyl.volume(theta)
        return p_peak * (v_tdc / v_open) ** GAMMA

    def exhaust_sound_speed(self) -> float:
        """Speed of sound in the exhaust gas (m/s) at the current operating point.

        c = sqrt(gamma * R * T).  The exhaust gas is hot (idle ~650 K up to
        ~1200 K at full load), so c is ~470-670 m/s — far above the 343 m/s of
        cold air, and it *rises with load and rpm*.  That is why a real exhaust
        note slides up in pitch as you load the engine, on top of the firing
        rate climbing.  The audio pipe resonance is tuned from this.
        """
        load = self._effective_throttle()
        rpm_frac = min(self.rpm / max(self.engine.redline_rpm, 1.0), 1.0)
        temp_k = 650.0 + 350.0 * load + 200.0 * rpm_frac
        return math.sqrt(GAMMA * 287.0 * temp_k)

    def forced_induction_rpm(self) -> float:
        """Estimated compressor/turbine SHAFT speed (rpm) for the boost gauge.

        Turbos ride exhaust energy, so their shaft speed tracks BOOST (which
        already spools with lag here) — idling at a freewheel and screaming to
        ~180k at full boost.  Belt-driven blowers instead spin in fixed ratio
        with the crank: a Roots pack at ~2.6x, a centrifugal at a big step-up."""
        eng = self.engine
        ind = eng.induction
        if ind == "na" or eng.boost_bar <= 0.0:
            return 0.0
        rf = min(self.rpm / max(eng.redline_rpm, 1.0), 1.0)
        if ind == "roots":
            return self.rpm * 2.6                       # lobe pack, belt-driven
        if ind == "centrifugal":
            return 60000.0 * rf                         # impeller step-up pulley
        # turbo / e-turbo: tie to boost so the needle lags & spools realistically
        frac = min(max(self.boost / max(eng.boost_bar, 0.05), 0.0), 1.1)
        running = min(1.0, self.rpm / max(eng.idle_rpm * 0.5, 1.0))
        return 185000.0 * (0.10 + 0.90 * math.sqrt(frac)) * running

    def telemetry(self) -> dict:
        """Live physical readouts (the gauges the original game shows)."""
        map_pa = self._manifold_pressure()
        ve = self._volumetric_efficiency()
        # Air-fuel ratio: ~stoich light, enriching toward ~12.5 at full load.
        afr = 14.7 - 2.2 * min(max(self.throttle, 0.0), 1.0)
        lam = afr / 14.7
        # Volumetric airflow -> standard cubic feet per minute (4-stroke: one
        # intake stroke every two revolutions).
        flow_m3s = (self.engine.total_displacement * (self.omega / (2 * math.pi) / 2.0)
                    * ve * (map_pa / P_ATM))
        scfm = max(flow_m3s, 0.0) * 2118.88

        # Exhaust gas composition.  When fuel is flowing and burning, the mix is
        # stoich-to-rich: almost no leftover O2 and high CO2 (peaking at stoich).
        # When fuel is CUT — rev limiter, gearshift, or closed-throttle overrun —
        # the engine just pumps air, so O2 jumps to nearly atmospheric (~20.9%)
        # and CO2 collapses.  That overrun O2 spike is why a real wideband reads
        # lean on a trailing throttle; the old model could never show it.
        combusting = self.ignition_on and not self._fuel_cut and not self._shift_cut
        overrun = (combusting and self.throttle < 0.04
                   and self.rpm > self.engine.idle_rpm * 1.4)
        if (not combusting) or overrun:
            o2_pct, co2_pct = 20.9, 0.4
        else:
            rich = max(0.0, 1.0 - lam)              # 0 at stoich, grows rich
            lean = max(0.0, lam - 1.0)
            o2_pct = 0.6 + lean * 21.0              # residual + any lean excess
            co2_pct = max(6.0, 14.8 - 10.0 * rich - 9.0 * lean)
        return {
            "map_kpa": map_pa / 1000.0,
            "vacuum_inhg": (P_ATM - map_pa) / 3386.39,
            "ve_pct": ve * 100.0,
            "afr": afr,
            "lambda": lam,
            "o2_pct": o2_pct,
            "co2_pct": co2_pct,
            "scfm": scfm,
            "fi_rpm": self.forced_induction_rpm(),
        }

    # ------------------------------------------------------------ helpers
    def cycle_phase_deg(self, cyl_index: int) -> float:
        """Current 720 deg cycle angle for a cylinder (for drawing / audio)."""
        crank_deg = math.degrees(self.crank_angle)
        return (crank_deg + self._offset_deg[cyl_index]) % 720.0

    def piston_fraction(self, cyl_index: int) -> float:
        """Piston position as 0 (TDC) .. 1 (BDC) for animation."""
        cyl = self.engine.cylinders[cyl_index]
        phi = self.cycle_phase_deg(cyl_index)
        theta = (phi % 360.0) * DEG
        return cyl.piston_displacement(theta) / cyl.stroke
