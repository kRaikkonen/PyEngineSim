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
        self._idle_trim = engine.idle_air_base  # idle-governor air, 0..~0.3

        # --- telemetry (updated every step) ---
        self.gas_torque = 0.0
        self.friction_torque = 0.0
        self.cylinder_pressure = np.full(engine.num_cylinders, P_ATM)

        # precompute per-cylinder cycle offset in radians of the *cycle*
        self._offset_deg = np.array([c.cycle_offset_deg for c in engine.cylinders])

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
        return idle_map + t * (P_ATM - idle_map)

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
                # pressure spike.
                k = 1.0 + self.engine.heat_release_k * (p_manifold / P_ATM) * ve
                p_peak = p_compressed * k
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
        shift_target = self.drivetrain.shift_target_omega() if shifting else 0.0

        # Sub-step so the crank never advances more than ~3 deg per integration
        # step; this keeps the sharp combustion torque pulse well resolved.
        max_step = 3.0 * DEG
        speed = max(abs(self.omega), 1.0)
        n = int(dt * speed / max_step) + 1
        n = min(n, 4000)
        h = dt / n

        for _ in range(n):
            self._update_idle_governor(h)
            self.gas_torque = self._compute_torque()
            self.friction_torque = self._loss_torque()
            starter = self._starter_torque()
            clutch = self.drivetrain.clutch_torque_on_engine(self.omega)

            net = (self.gas_torque + starter - self.friction_torque
                   - self.external_load + clutch)
            domega = net / eng.flywheel_inertia * h

            new_omega = self.omega + domega
            # Don't let friction/load push a stopped engine backwards.
            if (not self.starter_engaged and self.gas_torque + clutch <= 0.0
                    and new_omega < 0.0):
                new_omega = 0.0
            self.omega = new_omega

            # Rev-match toward the target gear speed while shifting.
            if shifting and shift_target > 5.0:
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
        return {
            "map_kpa": map_pa / 1000.0,
            "vacuum_inhg": (P_ATM - map_pa) / 3386.39,
            "ve_pct": ve * 100.0,
            "afr": afr,
            "lambda": lam,
            "o2_pct": max(0.0, (lam - 1.0)) * 21.0,     # lean -> leftover O2
            "scfm": scfm,
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
