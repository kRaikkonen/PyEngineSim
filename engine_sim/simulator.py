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

try:                                    # white-box surrogate layer (VE tables);
    from .surrogate import Surrogate, LUT  # guarded so a stripped install still runs
    from .ve_model import build_ve_table
    from .bmep_model import torque_target, build_boost_table
    _HAVE_SURROGATE = True
except Exception:                       # pragma: no cover
    _HAVE_SURROGATE = False

try:                                    # white-box MAP model (orifice balance)
    from . import map_model
    _HAVE_MAP_MODEL = True
except Exception:                       # pragma: no cover
    _HAVE_MAP_MODEL = False

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
        self._dfco = False              # coasting indicator (no longer cuts fuel)
        self._blip = 0.0                # downshift rev-match throttle blip (0..1)
        # white-box MAP: idle throttle-plate bleed area + diesel flag (diesels
        # are UNTHROTTLED — load is set by fuel, so their MAP stays ~atmospheric)
        self._map_idle_area = (map_model.idle_area_for(engine)
                               if _HAVE_MAP_MODEL else 0.02)
        self._map_diesel = engine.cylinders[0].compression_ratio >= 14.5
        self.boost = 0.0                # forced-induction boost (bar gauge)
        # idle-governor air, 0..~0.35 — starts with a cold-start CHOKE head start
        # (extra air/fuel while the block is at ambient) so a cold engine catches
        # on the starter despite the thick-oil friction, just like a real ECU.
        self._idle_trim = engine.idle_air_base * 1.6
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
        # Per-cylinder blowdown pressure, captured as each cylinder's exhaust
        # valve actually opens — the audio reads THESE, so every cylinder's pulse
        # carries its own real thermodynamic state (a limiter-cut or misfiring
        # cylinder goes quiet by physics, not by a special case).
        self.last_blowdown = np.full(engine.num_cylinders, P_ATM)
        # SOFT rev limiter: cut a ROTATING subset of cylinders (the real-world
        # "brap-brap" stutter) instead of silencing the whole engine.
        self._cut_mask = np.zeros(engine.num_cylinders, dtype=bool)
        self._cycle_n = 0               # 720-deg cycle counter (rotates the mask)
        # --- thermal state (drives cold friction, fast idle and audio timbre) ---
        # Start WARMED THROUGH: a cold start means ~2 minutes of thick-oil drag +
        # fast idle before the car feels right — realistic, but a bad default for
        # a sim you open to PLAY.  The dynamics still live (temps climb under
        # load, cool off when parked); set these to ~20 for a true cold start.
        self.coolant_c = 88.0           # coolant temperature (deg C)
        self.oil_c = 85.0               # oil temperature (lags the coolant)

        # precompute per-cylinder cycle offset in radians of the *cycle*
        self._offset_deg = np.array([c.cycle_offset_deg for c in engine.cylinders])

        # --- white-box surrogate channels -----------------------------------
        # VE(rpm, MAP) is BAKED AT LOAD from the first-principles intake model
        # (Taylor Mach index + Helmholtz ram tuning + residual backflow) instead
        # of the old hand-drawn Gaussian; baking a 22x12 table costs ~1 ms.
        # Registered BEFORE the clutch-sizing sweep so that uses the same VE.
        self.surrogate = Surrogate() if _HAVE_SURROGATE else None
        self._ve_lut = None
        self._boost_lut = None          # steady-boost target (turbine balance)
        self._burn_C = None             # cycle-mean torque per unit (k-1)
        self._burn_T0 = None            # motoring (k=1) cycle-mean torque
        self._k_burn = 3.0              # live burn multiplier (solved per frame)
        if self.surrogate is not None:
            try:
                self._ve_lut = build_ve_table(engine)
                self.surrogate.register("ve", self._ve_lut)
            except Exception:
                self._ve_lut = None     # fall back to the legacy Gaussian
            try:
                # calibrate the pulse model so k can be SOLVED against the
                # physical BMEP target each frame (kills heat_release_k tuning)
                self._calibrate_burn()
            except Exception:
                self._burn_C = self._burn_T0 = None
            if engine.induction == "turbo" and engine.boost_bar > 0.0 \
                    and self._ve_lut is not None:
                try:
                    self._boost_lut = build_boost_table(engine, self._ve_lut)
                    self.surrogate.register("boost", self._boost_lut)
                except Exception:
                    self._boost_lut = None

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
            p_man = self._manifold_pressure()
            k = self._burn_k(rpm, p_man / P_ATM)   # physical burn at this point
            tot = 0.0
            for d in range(0, 720, 20):
                self.crank_angle = d * DEG
                tot += self._compute_torque(k=k, p_man=p_man)
            best = max(best, tot / 36.0)
            rpm += 200.0
        (self.throttle, self.boost, self.omega, self.crank_angle,
         self._fuel_cut, self._shift_cut) = saved
        self.cylinder_pressure[:] = P_ATM
        self.last_blowdown[:] = P_ATM
        return best

    def _calibrate_burn(self):
        """Measure the crank-resolved pulse model ONCE at load: cycle-mean
        torque is exactly linear in the burn multiplier, T(k) = T0 + (k-1)*C,
        so two sweeps (motoring k=1, reference k=5) over an rpm x MAP grid give
        C(rpm, map) and T0(rpm, map).  At runtime k is then SOLVED so the
        cycle-mean torque equals the physical BMEP target — the burn strength
        comes from energy accounting instead of a hand-tuned constant."""
        eng = self.engine
        saved = (self.throttle, self.boost, self.omega, self.crank_angle,
                 self._fuel_cut, self._shift_cut)
        self._fuel_cut = self._shift_cut = False
        rpm_knots = np.linspace(max(eng.idle_rpm * 0.6, 300.0),
                                eng.redline_rpm * 1.05, 5)
        map_hi = max(1.10, 1.0 + getattr(eng, "boost_bar", 0.0) + 0.15)
        map_knots = np.array([0.15, 0.55, 1.0, map_hi])
        K_REF = 5.0

        def cycle_mean(k, p_man):
            tot = 0.0
            for d in range(0, 720, 15):
                self.crank_angle = d * DEG
                tot += self._compute_torque(k=k, p_man=p_man)
            return tot / 48.0

        c_v = np.empty((len(rpm_knots), len(map_knots)))
        t0_v = np.empty_like(c_v)
        for i, r in enumerate(rpm_knots):
            self.omega = float(r) * TWO_PI / 60.0
            for j, m in enumerate(map_knots):
                p_man = float(m) * P_ATM
                t1 = cycle_mean(1.0, p_man)
                t5 = cycle_mean(K_REF, p_man)
                c_v[i, j] = max((t5 - t1) / (K_REF - 1.0), 1e-6)
                t0_v[i, j] = t1
        axes = [("rpm", rpm_knots), ("map", map_knots)]
        self._burn_C = LUT(axes, c_v)
        self._burn_T0 = LUT(axes, t0_v)
        (self.throttle, self.boost, self.omega, self.crank_angle,
         self._fuel_cut, self._shift_cut) = saved
        self.cylinder_pressure[:] = P_ATM
        self.last_blowdown[:] = P_ATM

    def _burn_k(self, rpm, mapf):
        """Burn multiplier solved so the pulse model's cycle-mean torque equals
        the physical BMEP target at this operating point.  Falls back to the
        legacy heat_release_k formula if calibration is unavailable."""
        ve = self._volumetric_efficiency(mapf)
        if self._burn_C is None or self._burn_T0 is None:
            return 1.0 + self.engine.heat_release_k * mapf * ve   # legacy
        t_tgt = torque_target(self.engine, rpm, mapf, ve)
        c = self._burn_C.eval2(rpm, mapf)
        t0 = self._burn_T0.eval2(rpm, mapf)
        return min(max(1.0 + (t_tgt - t0) / c, 1.0), 14.0)

    # ------------------------------------------------------------------ rpm
    @property
    def rpm(self) -> float:
        return rads_to_rpm(self.omega)

    # ----------------------------------------------------- pressure model
    def _effective_throttle(self) -> float:
        """Air demand actually reaching the engine: the larger of the driver's
        pedal, the idle governor's trim, and any downshift rev-match BLIP,
        clamped to 0..1.  The blip is what makes a downshift BARK: the ECU (or a
        heel-toe driver) cracks the throttle to fire the engine up to the target
        speed instead of dragging it up silently on the clutch."""
        return min(max(self.throttle, self._idle_trim, self._blip), 1.0)

    def _manifold_pressure(self) -> float:
        """Intake-manifold absolute pressure from effective throttle.

        Closed throttle -> strong vacuum; wide-open -> near atmospheric.  The
        closed-throttle floor DEEPENS with rpm: the shut throttle is a fixed
        orifice (near-constant flow) while the engine's displacement demand
        grows with speed, so vacuum pulls down roughly as idle_rpm/rpm — this
        is why a real overrun shows 25+ inHg and why an engine can't sustain
        2000 rpm against a closed plate.
        """
        eng = self.engine
        t = self._effective_throttle()
        boost_pa = self.boost * 1.0e5
        if not _HAVE_MAP_MODEL:                 # legacy fallback (stripped install)
            ratio = max(eng.idle_rpm / max(self.rpm, eng.idle_rpm), 0.25) ** 0.5
            idle_map = eng.closed_map_fraction * ratio * P_ATM
            return idle_map + t * (P_ATM - idle_map) + boost_pa
        # WHITE-BOX MAP: steady-state balance of throttle-orifice inflow against
        # cylinder pumping (see map_model).  No tuned exponent — the part-throttle
        # vacuum and its rpm-deepening fall out of the orifice physics.  A nominal
        # VE keeps the solve one-way (MAP -> VE -> torque, no circularity).
        # (A real diesel is unthrottled — fuel-metered — but our combustion tracks
        #  air, so we keep the throttle->MAP path for all engines: the diesel's
        #  pedal then meters charge exactly as the old model did, idle anchored by
        #  closed_map_fraction.  Modelling fuel-limited diesel load is a P3 job.)
        frac = map_model.solve_map_fraction(
            t, self.rpm, eng.redline_rpm, 0.85, self._map_idle_area)
        return frac * P_ATM + boost_pa

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
            # A turbo spools on EXHAUST MASS-FLOW, which is high only under LOAD.
            # Free-revving in neutral makes little boost (so the car doesn't just
            # surge to the limiter the instant you blip it); in gear it spools fully.
            load_gate = 1.0 if self.drivetrain.gear > 0 else 0.3
            if self._boost_lut is not None:
                # steady target from the offline turbine/compressor energy
                # balance (bmep_model.build_boost_table): onset shape follows
                # exhaust enthalpy flow, wastegate caps at boost_bar.  The
                # first-order lag below stays the live transient (turbo_lag).
                target = self._boost_lut.eval2(self.rpm, thr) * load_gate
            else:                                 # legacy linear spool ramp
                spool = (rf - eng.turbo_spool_frac) / max(eng.turbo_spool_width, 1e-3)
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
            # FAST IDLE while cold (the ECU holds extra air until warmed through)
            coldness = min(max((75.0 - self.coolant_c) / 55.0, 0.0), 1.0)
            err = eng.idle_rpm * (1.0 + 0.22 * coldness) - self.rpm
            self._idle_trim += err * eng.idle_gain * h
        else:
            # Foot on the throttle: relax the trim back toward its base level.
            self._idle_trim += (eng.idle_air_base - self._idle_trim) * 3.0 * h
        # Clamp to a sane idle-air range.  0.5 max: some small engines simply
        # need more idle air than the old 0.35 ceiling allowed to actually REACH
        # their target idle (the EA888 sat pinned at the stop, idling at ~580
        # against a 850 target); the integral flips sign at the target, so a
        # higher travel limit cannot overshoot.
        self._idle_trim = min(max(self._idle_trim, 0.0), 0.5)

    def _volumetric_efficiency(self, map_frac=None) -> float:
        """How well the cylinders breathe at the current rpm (0..~ve_max).

        Served from the baked white-box VE table (Taylor Mach-index roll-off +
        Helmholtz ram humps + residual backflow, see ve_model.py) — real
        geometry decides where the torque humps sit and how the top end dies.
        Falls back to the legacy hand-drawn Gaussian if no table exists.
        ``map_frac`` (p_man/p_atm) can be passed by callers that already know
        it, to save recomputing the manifold pressure.
        """
        lut = self._ve_lut
        if lut is not None:
            if map_frac is None:
                map_frac = self._manifold_pressure() / P_ATM
            return lut.eval2(self.rpm, map_frac)
        eng = self.engine
        peak = eng.ve_peak_frac * eng.redline_rpm
        width = eng.ve_width_frac * eng.redline_rpm
        bell = math.exp(-((self.rpm - peak) / width) ** 2)
        return eng.ve_floor + (eng.ve_max - eng.ve_floor) * bell

    def _cylinder_pressure(self, cyl, phi_deg, p_manifold, combusting, k):
        """Absolute gas pressure (Pa) for one cylinder at cycle angle phi (deg).

        `phi_deg` is taken modulo 720.  `combusting` is True when there is spark
        + air this cycle.  ``k`` is the burn pressure multiplier — SOLVED each
        frame so the cycle-mean torque hits the physical BMEP target (see
        bmep_model.torque_target and _calibrate_burn), replacing the old
        hand-tuned heat_release_k fudge.
        """
        phi = phi_deg % 720.0

        if phi < COMPRESSION:
            # Intake stroke: cylinder is open to the manifold.
            return p_manifold

        # Volume at the start of compression (BDC) and right now.
        v_bdc = cyl.clearance_volume + cyl.piston_area * cyl.stroke
        theta = (phi % 360.0) * DEG          # crank angle from TDC for kinematics
        v_now = cyl.volume(theta)

        if phi < EXHAUST:
            # Compression + power strokes share the MOTORING (no-burn) trace:
            # adiabatic from the trapped manifold charge, for any piston position.
            p_mot = p_manifold * (v_bdc / v_now) ** GAMMA
            if not combusting:
                return p_mot
            # WIEBE finite burn: the charge releases heat over a real crank-angle
            # window (spark BEFORE TDC, ~55 deg 10-90% burn) instead of an
            # instantaneous pressure jump at TDC.  xb ramps 0 -> 1, and the
            # pressure rides the motoring trace scaled by the released fraction:
            #     p = p_mot * (1 + (k - 1) * xb)
            # Once the burn completes (xb = 1) this is EXACTLY the old
            # instant-burn curve, so per-car torque calibration is preserved;
            # what changes is the (previously square) rise around TDC — peak
            # pressure now lands ~15 deg ATDC like a real trace, pressure climbs
            # slightly BEFORE TDC (ignition), and spark_advance_deg is a live,
            # physical parameter.
            eng = self.engine
            # Spark ADVANCE MAP: flame speed is roughly constant in TIME, so the
            # ECU advances the spark as rpm rises (more crank-degrees pass per
            # millisecond) and runs only ~10 deg BTDC at idle — a fixed advance
            # would waste idle torque on pre-TDC negative work.  Burn duration in
            # CRANK DEGREES stretches with rpm for the same reason.
            rf = min(self.rpm / 3600.0, 1.0)
            adv = eng.spark_advance_deg * (0.38 + 0.62 * rf)
            dur = eng.burn_duration_deg * (0.55 + 0.45 * rf)
            t = (phi - (COMBUSTION_TDC - adv)) / max(dur, 5.0)
            if t <= 0.0:
                xb = 0.0
            elif t >= 1.0:
                xb = 1.0
            else:
                xb = 1.0 - math.exp(-6.9 * t * t * t)   # Wiebe, a=6.9 m=2
            return min(p_mot * (1.0 + (k - 1.0) * xb), P_PEAK_CAP)

        # Exhaust stroke: blown down to ~atmospheric (slight back-pressure).
        return 1.05 * P_ATM

    # --------------------------------------------------------- torque sum
    def _compute_torque(self, k=None, p_man=None):
        """Net gas torque on the crank (N*m) at the current crank angle.

        Also refreshes ``self.cylinder_pressure`` telemetry.  ``k`` / ``p_man``
        override the burn multiplier and manifold pressure — used by the
        load-time burn calibration sweep; live stepping passes neither.
        """
        eng = self.engine
        p_manifold = self._manifold_pressure() if p_man is None else p_man
        crank_deg = math.degrees(self.crank_angle)
        # Per-cylinder combustion: the soft limiter cuts only the cylinders in
        # the rotating _cut_mask, so the rest keep firing (the "brap" stutter);
        # DFCO kills all of them on the overrun.
        base_burn = self.ignition_on and not self._shift_cut
        cutting = self._fuel_cut
        k_burn = self._k_burn if k is None else k

        total = 0.0
        for i, cyl in enumerate(eng.cylinders):
            phi = (crank_deg + self._offset_deg[i]) % 720.0
            burn_i = base_burn and not (cutting and self._cut_mask[i])
            p = self._cylinder_pressure(cyl, phi, p_manifold, burn_i, k_burn)
            self.cylinder_pressure[i] = p
            # capture the REAL blowdown pressure as this cylinder's exhaust valve
            # cracks open — the audio synth keys each pulse off this value
            if EXHAUST_OPEN <= phi < EXHAUST_OPEN + 16.0:
                self.last_blowdown[i] = p

            theta = (phi % 360.0) * DEG
            # Net force on the piston crown (gas above, ~atmosphere in the case).
            force = (p - P_ATM) * cyl.piston_area
            # Virtual work: torque = force * (dx/dtheta).
            total += force * cyl.d_displacement_d_theta(theta)
        # (the old per-car torque_scale trim is GONE: with the burn multiplier
        # solved against the physical BMEP target, boosted torque comes out
        # right from energy accounting — no per-car fudge to blend in.)
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
        # cold oil is thick — a VISCOUS effect, so it mostly scales the
        # omega-proportional shear term (up to +60%) and only nudges the static
        # rub: cranking stays easy (low omega = little shear), but a cold engine
        # feels sluggish at speed and drinks more fuel until it warms through.
        cold = min(max((80.0 - self.oil_c) / 60.0, 0.0), 1.0)
        mag = (eng.friction_static * (1.0 + 0.25 * cold)
               + eng.friction_linear * (1.0 + 0.6 * cold) * abs(w)
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

        # Gearshift rev-match detection (computed early so DFCO can see the
        # downshift BLIP).  UPSHIFT -> cut & let revs fall; DOWNSHIFT -> blip the
        # throttle and keep firing so the engine barks up to the rev-match speed
        # instead of being dragged up silently on the clutch (why an off-throttle
        # downshift used to be near-silent even at high rpm).
        mid_shift = self.drivetrain.mid_shift
        rev_matching = self.drivetrain.rev_matching
        shift_target = (self.drivetrain.shift_target_omega()
                        if (mid_shift or rev_matching) else 0.0)
        # a downshift on ANY gearbox (mid_shift covers single-clutch/manual too,
        # which never set is_shifting) whose match target is meaningfully higher
        # than the current revs -> blip up and keep firing (the bark).
        downshift_blip = mid_shift and shift_target > self.omega * 1.03
        if downshift_blip:
            gap = min((shift_target - self.omega) / max(self.omega, 1.0), 1.0)
            self._blip = min(max(0.35 + 0.9 * gap, 0.0), 1.0)
            self._shift_cut = False
        else:
            self._blip = 0.0
            self._shift_cut = self.drivetrain.is_shifting   # upshift cut (DCT/AT)

        # Coasting indicator (throttle shut, well above idle).  NOTE: this used
        # to trigger a hard DEcel Fuel Cut-Off that killed all combustion on the
        # overrun — but that made the exhaust note collapse the instant you
        # lifted, even at high rpm ("之前没有这个毛病").  The rpm-deepening
        # closed-throttle MAP now traps so little air that pumping + friction
        # brake the engine to idle on their own (verified: lift-off decel is the
        # same with or without a fuel cut), so we KEEP FIRING on the overrun and
        # let the note stay alive.  The flag is retained only as a coasting hint.
        self._dfco = (self._effective_throttle() < 0.04
                      and rpm > eng.idle_rpm * 2.0)

        # SOFT limiter: cut a rotating 3/4 of the cylinders (all of them if the
        # overshoot keeps growing) — the survivors keep firing, which is the real
        # limiter "brap-brap"; rotation spreads the heat like a real ECU.
        n = eng.num_cylinders
        if self._fuel_cut and n > 1:
            if rpm > eng.redline_rpm + 160.0:
                self._cut_mask[:] = True
            else:
                self._cut_mask[:] = False
                kcut = max(1, int(round(n * 0.75)))
                start = self._cycle_n % n
                for j in range(kcut):
                    self._cut_mask[(start + j) % n] = True
        else:
            self._cut_mask[:] = self._fuel_cut   # 1-cyl engines: plain cut

        # --- thermal state: waste heat warms the coolant, the radiator +
        # thermostat pull it back; oil chases the coolant with lag.  Drives cold
        # friction, fast idle and the exhaust's cold timbre.
        p_kw = max(self.gas_torque, 0.0) * max(self.omega, 0.0) / 1000.0
        running = self.omega > eng.idle_rpm * TWO_PI / 60.0 * 0.35
        q_in = (0.55 + min(p_kw, 27.0) / 22.0) if running else 0.0
        q_out = 0.010 * (self.coolant_c - 20.0) + 0.10 * max(self.coolant_c - 92.0, 0.0)
        self.coolant_c = min(max(self.coolant_c + (q_in - q_out) * dt, 15.0), 130.0)
        oil_tgt = self.coolant_c + 6.0 * min(p_kw / 150.0, 1.0) - 4.0
        self.oil_c += (oil_tgt - self.oil_c) * min(0.015 * dt, 1.0)   # tau ~ 1 min

        self._update_boost(dt)

        # Solve the burn multiplier ONCE per frame (it moves slowly): the
        # crank-resolved pulses then release exactly the heat the physical
        # BMEP target demands at this rpm/manifold point.
        self._k_burn = self._burn_k(rpm, self._manifold_pressure() / P_ATM)

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

        if self.crank_angle >= 2.0 * TWO_PI:          # completed a 720-deg cycle
            self._cycle_n += 1                        # (rotates the limiter mask)
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
        combusting = self.ignition_on and not self._fuel_cut

        v_bdc = cyl.clearance_volume + cyl.piston_area * cyl.stroke
        v_tdc = cyl.clearance_volume
        p_comp = p_man * (v_bdc / v_tdc) ** GAMMA
        # the live burn multiplier — solved against the physical BMEP target,
        # so pulse strength inherits the real energy accounting
        p_peak = p_comp * (self._k_burn if combusting else 1.0)
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
        ve = self._volumetric_efficiency(map_pa / P_ATM)
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
