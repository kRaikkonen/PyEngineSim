"""The v2 voice in the desktop studio: the simulator's operating point goes
into the compiled core (core/, Rust), and what comes back is the sound.

    simulator  --(rpm, manifold pressure, charge, mixture, exhaust gas,
                  fuel cut)-->  core: cylinders -> exhaust network -> open end

Nothing of the old sound chain (audio.py) runs while this voice is on: no
drawn pulse, no reverb, no AGC.  What the phone will play is this same
library given the same record (docs/REDESIGN_V2.md s.4, s.6).

What speaks: the exhaust, the intake mouth (where a car has an intake
written), and both once more off the ground.  NOT yet: the engine block and
its mechanical noise, a turbo whistle, the cabin.  The listener stands 1 m
behind the car, half a metre off its centre line.
"""
from __future__ import annotations

import os

import numpy as np

from . import core_exhaust, core_spec, corelib

P_ATM = corelib.P_ATM
RHO_REF = 1.184           # kg/m^3: what the breathing tables count a full charge in
_TURBO = ("turbo", "twin_scroll", "twincharge", "eturbo", "sequential")


def available():
    """Is the core built?"""
    return os.path.exists(corelib.library_path())


def operating_point(sim, net=None, intake=None):
    """The simulator's state as the core's record: a dict of
    corelib.control() arguments.

    Taken from the tables the torque comes from, so sound and torque carry
    the same charge: manifold pressure and volumetric efficiency (-> the air
    trapped per cylinder), the mixture, the exhaust gas temperature, the
    turbine's back-pressure (-> how far its vanes are shut), the cut mask.

    The burn itself (where its middle falls, how long it lasts, how much it
    varies from cycle to cycle) is not in the simulator; it follows the
    textbook trends with load and speed -- see `burn()`.
    """
    eng = sim.engine
    rpm = max(float(sim.rpm), 0.0)
    p_man = float(sim._manifold_pressure())
    mapf = p_man / P_ATM
    ve = float(sim._volumetric_efficiency(mapf))
    cyl = eng.cylinders[0]
    swept = cyl.piston_area * cyl.stroke
    charge = max(ve, 0.05) * mapf * RHO_REF * swept
    afr = float(sim.afr(min(mapf, 1.0)))
    load = float(sim._charge_rel(mapf))
    ca50, burn_deg, ccv = burn(eng, rpm, load)
    t_ex = float(sim.exhaust_gas_temp())

    n = len(eng.cylinders)
    cut = 0
    if (not sim.ignition_on) or getattr(sim, "_shift_cut", False):
        cut = (1 << n) - 1
    elif getattr(sim, "_fuel_cut", False):
        for i, c in enumerate(sim._cut_mask):
            if c:
                cut |= 1 << i

    orifice = [1.0, 1.0, 1.0, 1.0]
    if net is not None and net.turbine_k > 0.0 and rpm > 50.0:
        # the turbine's vanes (or its gate) sit where the turbo machine says
        # the turbine inlet pressure is: its orifice takes that drop at the
        # flow of this moment
        back = float(sim.exhaust_back_pressure(rpm, mapf))          # atm
        lines = max(sum(1 for nd in net.nodes
                        if nd.kind == core_exhaust.ORIFICE
                        and nd.index == core_exhaust.CH_TURBINE), 1)
        q = n * charge * (1.0 + 1.0 / afr) * rpm / 120.0 / lines
        dp = max(back - 1.0, 0.0) * P_ATM * 0.85     # the rest is cat and pipe
        want = dp / max(q * q, 1.0e-9) / net.turbine_k
        orifice[core_exhaust.CH_TURBINE] = min(max(want, 0.3), 30.0)
    if intake is not None and intake.throttle_k > 0.0 and rpm > 50.0:
        # the throttle plate stands where it holds the manifold at the
        # pressure the simulator has: that drop at the air flow of this moment
        q_air = n * charge * rpm / 120.0
        want = max(P_ATM - p_man, 0.0) / max(q_air * q_air, 1.0e-9) / intake.throttle_k
        orifice[core_exhaust.CH_THROTTLE] = min(max(want, 1.0), 1.0e6)
    if net is not None and net.flap_rpm > 0.0:
        # a shut bypass valve passes next to nothing (its loss x 3000)
        is_open = rpm >= net.flap_rpm or load >= net.flap_load
        orifice[core_exhaust.CH_FLAP] = 1.0 if is_open else 3000.0
    return dict(rpm=rpm, p_intake=p_man, t_intake=315.0, p_exhaust=P_ATM,
                t_exhaust=min(max(t_ex, 400.0), 1400.0), charge=charge,
                lam=afr / 14.7, ca50_deg=ca50, burn_deg=burn_deg,
                fuel_cut=cut, orifice=orifice, ccv=ccv)


def burn(eng, rpm, load):
    """(50 % point in degrees after TDC, 10-90 % duration in degrees,
    cycle-to-cycle variation) at a speed and a relative load 0..1.

    Trends (Heywood ch. 9): best timing puts the 50 % point 8 degrees after
    TDC; a boosted engine at low speed and full charge is knock-limited and
    burns later; a thin charge burns slower (more residual, less
    turbulence) and scatters more -- 1-2 % at full load, 6-10 % idling.
    """
    turbo = getattr(eng, "induction", "na") in _TURBO
    knock = max(load - 0.7, 0.0) / 0.3 * max(1.0 - rpm / (0.6 * eng.redline_rpm), 0.0)
    ca50 = 8.0 + (10.0 if turbo else 4.0) * knock
    burn_deg = 20.0 + 12.0 * (1.0 - load) + 4.0 * min(rpm / eng.redline_rpm, 1.2)
    ccv = 0.012 + 0.09 * (1.0 - load) ** 2
    return ca50, burn_deg, ccv


class CoreVoice:
    """One car playing through the core."""

    def __init__(self, sim, key, rate):
        self.sim = sim
        self.key = key
        self.rate = float(rate)
        eng = sim.engine
        self.spec = core_spec.spec_from_engine(eng)
        self.net = core_exhaust.exhaust_for(key, eng)
        self.core = corelib.Core(self.spec, self.rate)
        self.core.attach_exhaust(self.net)
        self.intake = core_exhaust.intake_for(key, eng)
        if self.intake is not None:
            self.core.attach_intake(self.intake)
        # how much of the real dynamic range is played (s.6.6): 1 = idle is
        # 30-40 dB below full load, as outside the car; 0 = constant level
        self.dynamics = 0.5
        self.volume = 1.0
        self.level_ref = self._reference_level()
        self._left = np.zeros(4096, dtype=np.float32)
        self._right = np.zeros(4096, dtype=np.float32)
        # run in at idle (a stopped crank would take for ever to turn six cycles)
        c = self._control()
        c.rpm = max(c.rpm, float(eng.idle_rpm))
        self.core.prime(c, 6.0)

    def _reference_level(self):
        """Pa rms at 1 m (ear-weighted, as the core meters it) at the rated
        point -- full charge, 85 % of the redline: the level that plays at
        the reference loudness."""
        eng = self.sim.engine
        rpm = 0.85 * eng.redline_rpm
        boost = max(getattr(eng, "boost_bar", 0.0), 0.0) \
            if getattr(eng, "induction", "na") != "na" else 0.0
        c = corelib.control(rpm, (1.0 + boost) * P_ATM, t_exhaust=1150.0,
                            lam=0.88, ca50_deg=10.0, burn_deg=24.0, ccv=0.015)
        # long enough for the level meter (0.25 s) to settle
        self.core.prime(c, 1.5 * rpm / 120.0)
        return float(max(self.core.level, 1.0e-3))

    def _control(self):
        kw = operating_point(self.sim, self.net, self.intake)
        return corelib.control(level_ref=self.level_ref, dynamics=self.dynamics,
                               volume=self.volume, **kw)

    def block(self, frames):
        """Render ``frames`` samples at the simulator's state now (the audio
        thread calls this; the core releases the GIL while it works)."""
        if frames > len(self._left):
            self._left = np.zeros(frames, dtype=np.float32)
            self._right = np.zeros(frames, dtype=np.float32)
        self.core.control(self._control())
        self.core.render_into(self._left, self._right, frames)
        return self._left[:frames].astype(np.float64)

    def close(self):
        self.core.close()
