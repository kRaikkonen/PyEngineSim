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
        else:
            self.ratios = [3.45, 2.10, 1.42, 1.03, 0.82]
            self.final_drive = 3.9
            self.wheel_radius = 0.31
            self.mass = 1250.0
            self.clutch_capacity = 340.0

        self.gear = 0                     # 0 = neutral, 1..len(ratios)
        self.clutch = 1.0                 # engagement, 0 (in) .. 1 (out/engaged)
        self.slip_scale = 4.0             # rad/s for the clutch to "bite"

        self.auto = False                 # automatic gearbox mode
        self._shift_timer = 0.0           # brief clutch lift during an auto shift
        self._blip_timer = 0.0            # paddle-shift clutch blip (manual)
        self._pending_gear = None         # gear to engage once the clutch lifts
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
        if self.gear < self.num_gears:
            self._pending_gear = self.gear + 1
            self._blip_timer = 0.22     # paddle-shift: auto-blip the clutch

    def shift_down(self):
        if self.gear > 0:
            self._pending_gear = self.gear - 1
            self._blip_timer = 0.22

    def _apply_pending(self):
        """Engage the queued gear only once the clutch is lifted, so the ratio
        never changes while it's coupled (that is what jolts a shift)."""
        if self._pending_gear is not None and self.clutch < 0.3:
            self.gear = self._pending_gear
            self._pending_gear = None

    @property
    def is_shifting(self) -> bool:
        return self._blip_timer > 0.0 or self._shift_timer > 0.0

    def shift_target_omega(self) -> float:
        """Engine speed (rad/s) that matches the wheels in the gear we are
        shifting into — the simulator rev-matches the engine to this so an
        upshift drops cleanly and a downshift blips up, with no limiter bounce."""
        g = self._pending_gear if self._pending_gear is not None else self.gear
        if g <= 0:
            return 0.0
        ratio = self.ratios[g - 1] * self.final_drive
        return (self.v / self.wheel_radius) * ratio

    def manual_clutch(self, clutch_held: bool, dt: float):
        """Drive the clutch in manual mode.

        You do NOT have to hold the clutch to change gear — tapping a shift
        auto-blips it (paddle-shift), so a one-key tap is enough.  Holding Shift
        still fully disengages it for launches / stalls.
        """
        if self.auto:
            return
        if self._blip_timer > 0.0:
            self._blip_timer -= dt
            target = 0.0
        elif clutch_held:
            target = 0.0
        else:
            target = 1.0
        rate = 9.0 if target < self.clutch else 2.2   # lift fast, engage smooth
        self.clutch += (target - self.clutch) * min(rate * dt, 1.0)
        self.clutch = min(max(self.clutch, 0.0), 1.0)
        self._apply_pending()

    # --------------------------------------------------------------- auto
    def auto_control(self, rpm, throttle, redline, dt):
        """Drive the clutch and gear selection like an automatic / DCT.

        Pulls away from rest by feathering the clutch, upshifts near the
        redline and downshifts when the revs fall, lifting the clutch briefly
        for each shift so the engine speed steps cleanly between gears.
        """
        if not self.auto:
            return
        up = 0.93 * redline
        down = 0.55 * redline
        launching = self.v < 6.0          # still slipping the clutch off the line
        locked = self.clutch > 0.9

        if self._shift_timer > 0.0:
            # mid-shift: clutch out briefly so the revs step between gears
            self._shift_timer -= dt
            target = 0.0
        elif throttle < 0.04 and self.v < 0.5:
            # stopped, off throttle: decouple (idle, no stall, no creep)
            if self.gear > 1:
                self.gear = 1
            target = 0.0
        else:
            if self.gear == 0 and throttle > 0.04:
                self.gear = 1
            # Only change gear once the clutch is LOCKED and the car is rolling,
            # so a slipping launch (engine free-revving) can't blast up the box.
            if locked and not launching and self._pending_gear is None:
                if rpm > up and self.gear < self.num_gears:
                    self._pending_gear = self.gear + 1
                    self._shift_timer = 0.18
                elif rpm < down and self.gear > 1:
                    self._pending_gear = self.gear - 1
                    self._shift_timer = 0.16

            if launching and throttle > 0.04:
                # Launch controller: feather the clutch to hold the engine near
                # a launch rpm, progressively locking as the car gathers speed.
                launch_rpm = 0.45 * redline
                err = (rpm - launch_rpm) / redline
                target = min(max(0.15 + 2.5 * err + self.v / 6.0, 0.0), 1.0)
            else:
                target = 1.0

        rate = 7.0 if target < self.clutch else 2.0   # disengage fast, engage smooth
        self.clutch += (target - self.clutch) * min(rate * dt, 1.0)
        self.clutch = min(max(self.clutch, 0.0), 1.0)
        self._apply_pending()

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
