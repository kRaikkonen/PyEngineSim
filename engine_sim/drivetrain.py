"""
A simple drivetrain: clutch, gearbox, final drive and a vehicle to push along.

This is what turns the engine from a bench dyno into something you *drive*.  The
clutch is a slipping torque coupler between the engine and the geared-up wheels;
the vehicle is a lumped mass fighting rolling resistance and aero drag.

Behaviour that falls out of it for free:
  * dump the clutch at idle in gear and the load stalls the engine (just like a
    real manual) — so you learn to feather it,
  * launch with the clutch slipping, then it bites and the car pulls,
  * each upshift drops the revs by the ratio step,
  * lift off and engine braking + drag slow the car down.
"""

from __future__ import annotations

import math

G = 9.81


class Drivetrain:
    def __init__(self, engine=None):
        # Gearbox / vehicle come from the engine preset when given, so each car
        # drives with its own real ratios.
        if engine is not None:
            self.ratios = list(engine.gear_ratios)
            self.final_drive = engine.final_drive
            self.wheel_radius = engine.wheel_radius
            self.mass = engine.vehicle_mass
            self.clutch_capacity = engine.clutch_capacity
            self.gearbox_type = engine.gearbox_type
        else:
            self.ratios = [3.45, 2.10, 1.42, 1.03, 0.82]
            self.final_drive = 3.9
            self.wheel_radius = 0.31
            self.mass = 1250.0
            self.clutch_capacity = 340.0
            self.gearbox_type = "dct"

        self.gear = 0                     # 0 = neutral, 1..len(ratios)
        self.clutch = 1.0                 # engagement, 0 (in) .. 1 (out/engaged)
        self.slip_scale = 4.0             # rad/s for the clutch to "bite"

        self.auto = False                 # automatic gearbox mode
        self._pending_gear = None         # gear to engage once the clutch lifts
        self._shifting = False            # a phased shift is in progress (auto OR manual)
        self._shift_phase = 0             # 0 idle | 1 declutch | 2 rev-match | 3 re-engage
        self._shift_elapsed = 0.0         # watchdog so a shift can never hang
        self._shift_lock = 0.0            # post-shift lockout (anti-hunting)
        self.brake = 0.0                  # 0..1 brake pedal
        self.max_brake_decel = 9.0        # m/s^2 at full brake

        self.c_roll = 0.014               # rolling resistance coefficient
        self.c_aero = 0.5 * 1.2 * 0.31 * 2.2   # 0.5*rho*Cd*A
        self.v = 0.0                      # vehicle speed, m/s

    # ------------------------------------------------------------- ratios
    @property
    def num_gears(self) -> int:
        return len(self.ratios)

    def total_ratio(self) -> float:
        if self.gear <= 0:
            return 0.0
        return self.ratios[self.gear - 1] * self.final_drive

    def shift_up(self):
        """Manual paddle up-shift — runs the same phased, gearbox-type-aware
        shift the automatic uses, so a DCT is seamless, a single-clutch kicks
        and an AT is slushy even when you drive it yourself."""
        if not self._shifting and self.gear < self.num_gears:
            self._begin_shift(self.gear + 1)

    def shift_down(self):
        if not self._shifting and self.gear > 0:
            self._begin_shift(self.gear - 1)

    def _apply_pending(self):
        """Engage the queued gear only once the clutch is essentially lifted, so
        the ratio never changes while it's still transmitting torque (that is
        what jolts a shift)."""
        if self._pending_gear is not None and self.clutch < 0.08:
            self.gear = self._pending_gear
            self._pending_gear = None

    @property
    def is_shifting(self) -> bool:
        """Whether the simulator should CUT fuel (engine makes no torque).

        Only a rev-matched 'box (DCT/AT) cuts fuel — and only while declutched
        in phases 1-2 — so the engine can be eased down to the matched speed for
        a seamless re-engage.  A single-clutch / manual box does NOT cut fuel:
        the engine keeps firing the whole time, flares against the open clutch,
        and the kick is a real power-ON driveline shock when the clutch slams
        shut — not a dead spot.  (Phase 3 re-engages with combustion live for
        every type.)"""
        if self._shifting and self._shift_phase < 3:
            return self.gearbox_type not in ("single", "manual")
        return False

    @property
    def rev_matching(self) -> bool:
        """Whether the simulator should actively rev-match the engine to the
        target gear.  A DCT/AT does (seamless); a single-clutch / manual 'box
        does NOT — the engine keeps its momentum and the clutch yanks it into
        line as it slams shut, which IS the Aventador-style shift kick."""
        if self._shifting and self._shift_phase < 3:
            return self.gearbox_type not in ("single", "manual")
        return False

    def _matched_rpm(self, gear: int) -> float:
        """Engine rpm the given gear would turn at the current road speed (with
        the clutch locked).  Used for shift decisions so a *slipping* launch
        can't fool the logic with a sky-high free-revving engine speed."""
        if gear < 1 or gear > self.num_gears:
            return 0.0
        ratio = self.ratios[gear - 1] * self.final_drive
        return (self.v / self.wheel_radius) * ratio * 9.5492966

    def shift_target_omega(self) -> float:
        """Engine speed (rad/s) that matches the wheels in the gear we are
        shifting into — the simulator rev-matches the engine to this so an
        upshift drops cleanly and a downshift blips up, with no limiter bounce."""
        g = self._pending_gear if self._pending_gear is not None else self.gear
        if g <= 0:
            return 0.0
        ratio = self.ratios[g - 1] * self.final_drive
        return (self.v / self.wheel_radius) * ratio

    def manual_clutch(self, clutch_held: bool, rpm: float, redline: float, dt: float):
        """Drive the clutch in manual mode.

        Tapping a shift runs the phased, gearbox-type-aware shift (paddle-shift,
        no need to hold anything) — so a DCT shifts seamlessly, a single-clutch
        kicks and an AT slurs, exactly like in auto.  Holding Shift still fully
        disengages the clutch for launches / stalls when you're NOT mid-shift.
        """
        if self.auto:
            return
        if self._shifting:
            self._run_shift(rpm, redline, dt)
            return
        target = 0.0 if clutch_held else 1.0
        rate = 9.0 if target < self.clutch else 2.2   # lift fast, engage smooth
        self._ease_clutch(target, dt, rate)

    # --------------------------------------------------------------- auto
    def _ease_clutch(self, target: float, dt: float, rate: float):
        """Move the clutch toward ``target`` at a first-order ``rate`` (1/s)."""
        self.clutch += (target - self.clutch) * min(rate * dt, 1.0)
        self.clutch = min(max(self.clutch, 0.0), 1.0)

    # Per-transmission shift personality:
    #   (declutch_rate, match_tol_frac, match_timeout, reengage_rate, lock)
    #   DCT  : snappy open, wait for a clean match, smooth feed-in  -> seamless
    #   single: snap open, only PARTLY match, then SLAM shut        -> the kick
    #   AT   : ease off the converter, full match, slow soft feed   -> slushy
    _SHIFT_PROFILES = {
        "dct":    (11.0, 0.018, 0.40, 3.4, 0.55),
        "single": (16.0, 0.075, 0.18, 30.0, 0.45),
        "manual": (16.0, 0.075, 0.18, 30.0, 0.45),
        "at":     (5.5,  0.030, 0.45, 1.9, 0.70),
    }

    def _begin_shift(self, new_gear: int):
        self._pending_gear = new_gear
        self._shifting = True
        self._shift_phase = 1
        self._shift_elapsed = 0.0

    def _run_shift(self, rpm, redline, dt):
        """Phased torque-cut shift (declutch -> swap -> rev-match -> re-engage),
        with the timing/feel taken from the gearbox type.

        DCT fully rev-matches before a smooth feed-in (no kick).  A single-clutch
        'box only partly matches then slams the clutch shut with the engine still
        a little off — that residual slip is the Aventador-style kick.  An AT
        eases the converter and feeds back in slowly for the slushy shift.
        """
        self._shift_elapsed += dt

        # --- TRUE DUAL-CLUTCH: a torque HANDOVER, not a torque cut -------------
        # A real DCT has two clutches; the target gear is pre-selected on the idle
        # one, so the shift is a fast clutch-to-clutch crossfade with (almost) no
        # interruption.  We swap the ratio instantly and let the FIRM engaging
        # clutch rev-match the engine itself — dragging it DOWN on an upshift and
        # UP on a downshift (the slip torque does it).  No passive rev-match wait,
        # which is what made off-throttle downshifts hang then slam in.
        if self.gearbox_type == "dct":
            if self._shift_phase == 1:
                self.gear = self._pending_gear     # pre-engaged 2nd clutch -> instant
                self._pending_gear = None
                self._shift_phase = 3
            if self._shift_elapsed < 0.06:
                self._ease_clutch(0.55, dt, 16.0)  # brief slip = the handover
            else:
                self._ease_clutch(1.0, dt, 6.5)    # firm close -> clutch rev-matches
            if self.clutch > 0.95 or self._shift_elapsed > 0.5:
                self._shifting = False
                self._shift_phase = 0
                self._shift_lock = 0.30
            return

        # --- TORQUE-CONVERTER AUTO: a fluid coupling never fully decouples, so an
        # AT shift is a SOFT, overlapping clutch-to-clutch handover smoothed by the
        # converter — slushy, no torque hole, no kick.  (It used to fully declutch
        # like a manual, which is what felt wrong.)  Some residual slip stays until
        # the converter re-locks at cruise.
        if self.gearbox_type == "at":
            if self._shift_phase == 1:
                self.gear = self._pending_gear
                self._pending_gear = None
                self._shift_phase = 3
            if self._shift_elapsed < 0.14:
                self._ease_clutch(0.42, dt, 7.0)   # converter slips through the swap
            else:
                self._ease_clutch(0.92, dt, 2.3)   # slow, soft, slushy feed-in
            if self.clutch > 0.90 or self._shift_elapsed > 0.75:
                self._shifting = False
                self._shift_phase = 0
                self._shift_lock = 0.55
            return

        # --- single-clutch / manual: a genuine declutch + re-engage ----------
        dc_rate, tol_frac, timeout, re_rate, lock = self._SHIFT_PROFILES.get(
            self.gearbox_type, self._SHIFT_PROFILES["single"])
        downshift = self._pending_gear is not None and self._pending_gear < self.gear
        if self._shift_phase == 1:                 # declutch, then swap gear
            self._ease_clutch(0.0, dt, dc_rate)
            self._apply_pending()                  # swaps only once clutch < 0.08
            if self._pending_gear is None:
                self._shift_phase = 2
        elif self._shift_phase == 2:               # hold clutch out, let revs match
            self._ease_clutch(0.0, dt, dc_rate)
            tgt_rpm = self.shift_target_omega() * 9.5492966   # rad/s -> rpm
            tol = max(110.0, tol_frac * redline)
            # A downshift needs the engine to rev UP to a higher target; off the
            # throttle it can't on its own, so don't wait for an impossible passive
            # match — move on and let the clutch blip drag it up on re-engage.
            matched = tgt_rpm < 60.0 or abs(rpm - tgt_rpm) < tol
            if (downshift and rpm < tgt_rpm) or matched or self._shift_elapsed > timeout:
                self._shift_phase = 3
        else:                                      # re-engage (feed-in or slam)
            self._ease_clutch(1.0, dt, re_rate)
            if self.clutch > 0.92 or self._shift_elapsed > 0.85:
                self._shifting = False
                self._shift_phase = 0
                self._shift_lock = lock            # settle before the next shift

    def auto_control(self, rpm, throttle, redline, dt):
        """Drive the clutch and gear selection like an automatic / DCT.

        Pulls away from rest by feathering the clutch, upshifts near the
        redline and downshifts when the revs fall.  Each shift runs the phased
        rev-matched sequence in :meth:`_run_shift` so the engine speed
        steps cleanly between gears with no lurch.
        """
        if not self.auto:
            return

        # --- mid-shift: hand off entirely to the phased state machine ---
        if self._shifting:
            self._run_shift(rpm, redline, dt)
            return

        if self._shift_lock > 0.0:
            self._shift_lock -= dt

        up = 0.93 * redline
        down = 0.50 * redline
        launching = self.v < 6.0          # still slipping the clutch off the line
        locked = self.clutch > 0.9

        if throttle < 0.04 and self.v < 0.5:
            # stopped, off throttle: decouple (idle, no stall, no creep)
            if self.gear > 1:
                self.gear = 1
            self._ease_clutch(0.0, dt, 7.0)
            return

        if self.gear == 0 and throttle > 0.04:
            self.gear = 1

        # Only start a shift once the clutch is LOCKED, the car is rolling, and
        # we are not in the brief post-shift lockout (which stops hunting).
        # Decisions use the *wheel-matched* rpm, not the raw engine rpm, so a
        # still-slipping launch can't trigger a bogus upshift.
        if locked and not launching and self._shift_lock <= 0.0:
            cur = self._matched_rpm(self.gear)
            if (cur > up and self.gear < self.num_gears
                    and self._matched_rpm(self.gear + 1) > down * 1.1):
                self._begin_shift(self.gear + 1)
                self._run_shift(rpm, redline, dt)
                return
            elif cur < down and self.gear > 1:
                self._begin_shift(self.gear - 1)
                self._run_shift(rpm, redline, dt)
                return

        if launching and throttle > 0.04:
            # Launch controller: feather the clutch to hold the engine near a
            # launch rpm, progressively locking as the car gathers speed.
            launch_rpm = 0.45 * redline
            err = (rpm - launch_rpm) / redline
            target = min(max(0.15 + 2.5 * err + self.v / 6.0, 0.0), 1.0)
        else:
            target = 1.0
        rate = 7.0 if target < self.clutch else 2.0   # disengage fast, engage smooth
        self._ease_clutch(target, dt, rate)

    # ------------------------------------------------------------- physics
    def _engine_side_clutch_torque(self, engine_omega: float) -> float:
        """Torque the clutch applies to the engine (negative = loads it down)."""
        ratio = self.total_ratio()
        if ratio == 0.0 or self.clutch <= 0.0:
            return 0.0
        wheel_omega = self.v / self.wheel_radius
        slip = engine_omega - wheel_omega * ratio
        return -self.clutch * self.clutch_capacity * math.tanh(slip / self.slip_scale)

    def clutch_torque_on_engine(self, engine_omega: float) -> float:
        return self._engine_side_clutch_torque(engine_omega)

    def step(self, engine_omega: float, h: float):
        """Advance the vehicle by ``h`` seconds given the engine speed."""
        ratio = self.total_ratio()
        # Torque reaching the wheels is the clutch torque multiplied by the ratio.
        engine_t = -self._engine_side_clutch_torque(engine_omega)  # wheel-driving
        wheel_force = (engine_t * ratio) / self.wheel_radius if ratio else 0.0

        # Road load + brakes always oppose motion.
        road = self.c_roll * self.mass * G + self.c_aero * self.v * self.v
        brake_force = self.brake * self.max_brake_decel * self.mass
        net = wheel_force - (road + brake_force if self.v > 0 else 0.0)

        self.v += net / self.mass * h
        if self.v < 0.0:
            self.v = 0.0

    # ------------------------------------------------------------- display
    @property
    def speed_kmh(self) -> float:
        return self.v * 3.6

    @property
    def gear_name(self) -> str:
        return "N" if self.gear == 0 else str(self.gear)
