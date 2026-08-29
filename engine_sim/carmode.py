"""Car mode as a reusable object, not a script.

``car.py`` is a terminal program: parse argv, loop, print a status line.  An
iPhone build cannot be that -- an iOS app is a view with a run loop, and it
needs to CREATE this thing, tick it, read it and stop it.  So the whole of car
mode lives here, and the CLI is a thin shell around it:

    car = CarMode(engine_key="rs3", telemetry=OBDTelemetry(...), rpm_map=...)
    car.start()
    ...  car.update(dt)  ...        # from a timer, a loop, whatever drives you
    car.stop()

Nothing in here knows how it is being driven, prints anything, or touches a
display.  That is the whole point: the same object serves the terminal, an iOS
app and the tests.
"""

from __future__ import annotations

import math

from . import presets
from .obd import RpmMap, ShiftDetector
from .simulator import Simulator
from .units import rpm_to_rads

# How hard the sim's crank is pulled toward the mapped rpm.  Fast enough to
# track a real engine, slow enough that one bad OBD sample cannot snap the
# pitch (the poll is 30-50 Hz and every sample is a fresh measurement).
_RPM_TRACK = 22.0
# With no data we sit at the engine's own idle rather than in silence, eased
# gently so a link dropout fades instead of stepping.
_IDLE_TRACK = 3.0


class CarMode:
    """Plays a chosen engine at a real car's rpm.  No UI, no I/O, no printing.

    Parameters
    ----------
    engine_key : preset key from ``presets.ALL``.
    telemetry  : anything with the Forza/OBD duck type -- ``rpm``, ``raw_rpm``,
                 ``throttle``, ``speed``, ``gear``, ``map_kpa``, ``baro_kpa``,
                 ``speed_valid`` and ``is_live()``.  Started and stopped by the
                 caller, not by us: on a phone the link outlives the screen.
    rpm_map    : an :class:`~engine_sim.obd.RpmMap`; defaults to a 1:1 map.
    synth_factory : ``f(sim) -> Synthesizer``, called on start and again on
                 every engine change.  It is a FACTORY rather than an instance
                 because a Synthesizer bakes its voicing tables from the engine
                 it was built with -- swapping cars means rebuilding it, the
                 same thing the GUI does.  ``None`` runs the data path with no
                 audio at all, which is how the link is diagnosed and how this
                 is tested.
    shift_pop  : cut the throttle when the gearbox shifts (the exhaust bang).
    """

    def __init__(self, engine_key="rs3", telemetry=None, rpm_map=None,
                 synth_factory=None, shift_pop=True):
        if engine_key not in presets.ALL:
            raise ValueError("unknown engine preset: %r" % (engine_key,))
        self.engine_key = engine_key
        self.sim = Simulator(presets.ALL[engine_key]())
        self.sim.ignition_on = True
        self.telemetry = telemetry
        self.rpm_map = rpm_map if rpm_map is not None else RpmMap("direct")
        self.synth_factory = synth_factory
        self.synth = None
        self.running = False
        self.shifter = ShiftDetector()
        self.shift_pop = bool(shift_pop)

        # live readouts, for whatever is displaying them (a status line, a
        # Toga label, a test)
        self.rpm_out = 0.0
        self.shifting = False
        self.label = next((l for k, l, _ in presets.PRESETS if k == engine_key),
                          engine_key)

    # ------------------------------------------------------------- lifecycle
    def start(self):
        if self.synth_factory is not None and self.synth is None:
            self.synth = self.synth_factory(self.sim)
            self.synth.start()
        self.running = True

    def stop(self):
        self.running = False
        if self.synth is not None:
            self.synth.stop()
            self.synth = None

    def set_engine(self, engine_key: str):
        """Swap the engine being played, keeping the link and the rpm map.

        The synthesizer is REBUILT, not rebound: it bakes its voicing tables
        (exhaust geometry, firing offsets, cavity reverbs) from the engine it
        was constructed with, so handing it a new simulator would keep playing
        the old car's timbre at the new car's rpm.  Crank speed carries across
        so the pitch does not jump on the way.
        """
        if engine_key not in presets.ALL:
            raise ValueError("unknown engine preset: %r" % (engine_key,))
        omega = self.sim.omega
        self.engine_key = engine_key
        self.sim = Simulator(presets.ALL[engine_key]())
        self.sim.ignition_on = True
        self.sim.omega = omega
        self.label = next((l for k, l, _ in presets.PRESETS if k == engine_key),
                          engine_key)
        if self.synth is not None:
            self.synth.stop()
            self.synth = None
        if self.running and self.synth_factory is not None:
            self.synth = self.synth_factory(self.sim)
            self.synth.start()

    # ------------------------------------------------------------------ tick
    def update(self, dt: float):
        """Advance one control step.  Call this as often as you like (100 Hz
        in the CLI); the synth integrates the crank at audio rate itself, so
        this only has to keep the targets fresh."""
        dt = min(max(dt, 0.0), 0.1)
        sim, eng = self.sim, self.sim.engine
        tm = self.telemetry

        if tm is not None and tm.is_live():
            self.rpm_map.observe(tm.raw_rpm, tm.throttle)
            self.rpm_out = self.rpm_map(tm.rpm, eng.idle_rpm, eng.redline_rpm)

            self.shifting = self.shift_pop and self.shifter.update(
                dt, tm.raw_rpm, tm.throttle, tm.speed)
            # an ignition cut IS a closed throttle as far as the exhaust is
            # concerned -- that is the bang
            sim.throttle = 0.0 if self.shifting else tm.throttle

            sim.omega += (rpm_to_rads(self.rpm_out) - sim.omega) * min(
                _RPM_TRACK * dt, 1.0)

            dtr = sim.drivetrain
            dtr.v = tm.speed if getattr(tm, "speed_valid", False) else 0.0
            dtr.gear = max(getattr(tm, "gear", 0), 0)
            if eng.induction != "na":
                map_kpa = getattr(tm, "map_kpa", 0.0)
                if map_kpa:      # your REAL boost drives the simulated compressor
                    sim.boost = max(0.0, (map_kpa - tm.baro_kpa) * 0.01)
                else:
                    sim._update_boost(dt)
        else:
            self.shifting = False
            sim.throttle = 0.0
            sim.omega += (rpm_to_rads(eng.idle_rpm) - sim.omega) * min(
                _IDLE_TRACK * dt, 1.0)
            self.rpm_out = sim.omega * 60.0 / (2.0 * math.pi)

        sim.crank_angle += sim.omega * dt
        return self.rpm_out

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        """Everything a display might want, already resolved."""
        tm = self.telemetry
        live = bool(tm is not None and tm.is_live())
        boost_bar = 0.0
        if tm is not None and getattr(tm, "map_kpa", 0.0):
            boost_bar = (tm.map_kpa - tm.baro_kpa) * 0.01
        return {
            "live": live,
            "link": ("LIVE" if live
                     else (getattr(tm, "status", "") or "waiting")),
            "car_rpm": getattr(tm, "raw_rpm", 0.0) if tm else 0.0,
            "sim_rpm": self.rpm_out,
            "pedal": getattr(tm, "throttle", 0.0) if tm else 0.0,
            "gear": getattr(tm, "gear", 0) if tm else 0,
            "speed_kmh": (getattr(tm, "speed", 0.0) * 3.6) if tm else 0.0,
            "boost_bar": boost_bar,
            "hz": getattr(tm, "hz", 0.0) if tm else 0.0,
            "rtt_ms": (getattr(tm, "rtt", 0.0) * 1000.0) if tm else 0.0,
            "shifting": self.shifting,
            "shifts": self.shifter.shifts,
            "engine": self.label,
        }
