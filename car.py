"""PyEngineSim CAR MODE - headless in-car soundtrack, no graphics at all.

Plug an ELM327 dongle into the OBD-II port, run this on a phone / laptop / Pi,
and your car makes whatever noise you want it to.  Nothing is drawn: no pygame
window, no gauges, no analyzer, no mixer.  Just OBD in, engine note out, and one
line of text so you can see it working.

    py car.py                         # Audi A3 -> RS3 five-cylinder, WiFi dongle
    py car.py --engine aven           # ...or a Lamborghini V12
    py car.py --demo                  # no hardware: drive a simulated car
    py car.py --list-engines          # the 130-car roster
    py car.py --serial COM5           # Bluetooth-SPP / USB dongle instead

The rpm map is the point of car mode: your A3 stops at 6500, an Aventador does
not stop until 8500, so ``--map stretch`` (the default) puts your redline on its
redline.  ``--map direct`` is 1:1, which is what you want for an engine that
revs like yours.  Check it before you drive with ``--map-preview``.

WHY IT SOUNDS LATE, AND WHAT TO DO
    Bluetooth audio into the car stereo costs 150-300 ms and nothing here can
    fix that - use a WIRED speaker in the cabin (a small powered one in the
    footwell mixes with the real engine exactly the way a factory sound
    actuator does).  If you insist on Bluetooth, tell us how bad it is with
    ``--out-latency 0.2`` and the rpm feed will be predicted that far ahead: it
    tracks a sustained pull well and is wrong for ~100 ms at a shift.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

# Car mode must never open a display, and importing engine_sim.app would drag in
# pygame's window/font machinery.  We import ONLY the physics + audio + OBD.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from engine_sim import presets
from engine_sim.audio import Synthesizer, list_output_devices
from engine_sim.obd import (CAR_PROFILES, OBDTelemetry, RpmMap,
                            ShiftDetector)
from engine_sim.simulator import Simulator
from engine_sim.units import rpm_to_rads

LOOP_HZ = 100.0          # sim update rate; the synth integrates crank at audio rate


def _fmt_status(tm, rpm_out, eng_label, shift):
    link = "LIVE" if tm.is_live() else (tm.status or "waiting")
    boost = ""
    if tm.map_kpa:
        boost = " | boost %+.2f bar" % ((tm.map_kpa - tm.baro_kpa) * 0.01)
    return ("  %-4s | %5.0f -> %5.0f rpm | pedal %3.0f%% | g%d%s | %4.1f Hz "
            "| rtt %3.0f ms | %s%s" % (
                link, tm.raw_rpm, rpm_out, tm.throttle * 100.0, tm.gear, boost,
                tm.hz, tm.rtt * 1000.0, eng_label, "  *SHIFT*" if shift else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="PyEngineSim car mode - real car in, engine note out.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    g = ap.add_argument_group("engine")
    g.add_argument("--engine", default="rs3",
                   help="preset key to play (default: rs3, the 8Y RS3 five-pot)")
    g.add_argument("--list-engines", action="store_true",
                   help="print every preset key and exit")

    g = ap.add_argument_group("rpm mapping")
    g.add_argument("--map", choices=RpmMap.MODES, default="stretch",
                   help="how your rev range maps onto the engine's "
                        "(default: stretch = your redline is its redline)")
    g.add_argument("--car", choices=sorted(CAR_PROFILES), default="a3",
                   help="seed rev range for your own car (default: a3)")
    g.add_argument("--car-idle", type=float, default=None,
                   help="override the seed idle rpm")
    g.add_argument("--car-redline", type=float, default=None,
                   help="override the seed redline rpm")
    g.add_argument("--ratio", type=float, default=1.0,
                   help="multiplier for --map ratio")
    g.add_argument("--no-learn", action="store_true",
                   help="do not adapt the seed rev range to what the car does")
    g.add_argument("--map-preview", action="store_true",
                   help="print the rpm mapping table and exit")

    g = ap.add_argument_group("OBD link")
    g.add_argument("--host", default="192.168.0.10",
                   help="WiFi ELM327 address (default: 192.168.0.10)")
    g.add_argument("--port", type=int, default=35000, help="WiFi ELM327 port")
    g.add_argument("--serial", dest="serial_port", default=None,
                   help="serial / Bluetooth-SPP port instead of WiFi (COM5, "
                        "/dev/rfcomm0) - needs pyserial")
    g.add_argument("--baud", type=int, default=38400, help="serial baud rate")
    g.add_argument("--protocol", default="6",
                   help="ELM327 ATSP protocol; 6 = CAN 11-bit/500k (VAG MQB), "
                        "0 = auto-detect")
    g.add_argument("--poll-hz", type=float, default=50.0,
                   help="request rate ceiling (the adapter is the real limit)")
    g.add_argument("--demo", action="store_true",
                   help="no hardware: run a simulated car on localhost")

    g = ap.add_argument_group("audio")
    g.add_argument("--volume", type=float, default=1.0, help="0..1")
    g.add_argument("--pov", choices=("chase", "cockpit", "trackside"),
                   default="cockpit", help="listener perspective (default: cockpit)")
    g.add_argument("--rate", type=int, default=None, help="sample rate")
    g.add_argument("--device", default=None,
                   help="output device index or name substring")
    g.add_argument("--list-devices", action="store_true",
                   help="print audio output devices and exit")
    g.add_argument("--out-latency", type=float, default=0.0,
                   help="seconds of output lag to predict ahead of "
                        "(Bluetooth to a car stereo is roughly 0.2)")
    g.add_argument("--no-shift-pop", action="store_true",
                   help="do not cut the throttle when the gearbox shifts")
    g.add_argument("--no-audio", action="store_true",
                   help="run the data path only (link diagnostics, no sound)")
    g.add_argument("--quiet", action="store_true", help="no status line")

    args = ap.parse_args(argv)

    if args.list_engines:
        for key, label, _ in presets.PRESETS:
            print("  %-10s %s" % (key, label))
        return 0
    if args.list_devices:
        for line in list_output_devices():
            print(" ", line)
        return 0
    if args.engine not in presets.ALL:
        print("unknown engine %r (try --list-engines)" % args.engine)
        return 2

    # ---------------------------------------------------------- rpm map
    idle_seed, red_seed = CAR_PROFILES[args.car]
    rmap = RpmMap(mode=args.map,
                  car_idle=args.car_idle if args.car_idle else idle_seed,
                  car_redline=args.car_redline if args.car_redline else red_seed,
                  ratio=args.ratio, learn=not args.no_learn)

    sim = Simulator(presets.ALL[args.engine]())
    eng = sim.engine
    label = next((l for k, l, _ in presets.PRESETS if k == args.engine),
                 args.engine)

    if args.map_preview:
        print("  %s   idle %.0f, redline %.0f" % (label, eng.idle_rpm,
                                                  eng.redline_rpm))
        print("  your car (%s): idle %.0f, redline %.0f   [%s]"
              % (args.car, rmap.car_idle, rmap.car_redline, rmap.mode))
        print("      your rpm  ->  it plays")
        for car_rpm, sim_rpm in rmap.preview(eng.idle_rpm, eng.redline_rpm):
            print("        %6.0f  ->  %6.0f" % (car_rpm, sim_rpm))
        return 0

    # ------------------------------------------------------------ link
    host, port = args.host, args.port
    fake = None
    if args.demo:
        from tools.fake_elm327 import FakeELM327
        fake = FakeELM327()
        host, port = fake.start()
        print("  [demo] simulated car on %s:%d - no hardware in use" % (host, port))

    tm = OBDTelemetry(host=host, port=port, serial_port=args.serial_port,
                      baud=args.baud, protocol=args.protocol,
                      out_latency=args.out_latency, poll_hz=args.poll_hz)
    tm.start()

    # ----------------------------------------------------------- audio
    synth = None
    if not args.no_audio:
        synth = Synthesizer(sim, sample_rate=args.rate, device=args.device)
        synth.pov = args.pov
        synth.volume = max(0.0, min(args.volume, 1.0))
        synth.start()

    print("  PyEngineSim car mode - %s" % label)
    print("  map: %s   your car %.0f-%.0f rpm  ->  %.0f-%.0f rpm"
          % (rmap.mode, rmap.car_idle, rmap.car_redline, eng.idle_rpm,
             eng.redline_rpm))
    print("  link: %s   POV: %s   Ctrl-C to stop"
          % (args.serial_port or ("%s:%d" % (host, port)), args.pov))

    # ------------------------------------------------------------ loop
    dtr = sim.drivetrain
    sim.ignition_on = True
    period = 1.0 / LOOP_HZ
    last = time.monotonic()
    shifter = ShiftDetector()
    shifting = False
    next_status = 0.0
    show_status = not args.quiet
    tty = sys.stdout.isatty()
    try:
        while True:
            now = time.monotonic()
            dt = min(now - last, 0.1)
            last = now

            if tm.is_live():
                rmap.observe(tm.raw_rpm, tm.throttle)
                rpm_out = rmap(tm.rpm, eng.idle_rpm, eng.redline_rpm)

                shifting = (not args.no_shift_pop) and shifter.update(
                    dt, tm.raw_rpm, tm.throttle, tm.speed)
                # an ignition cut IS a closed throttle as far as the exhaust
                # is concerned - that is the bang
                sim.throttle = 0.0 if shifting else tm.throttle

                target = rpm_to_rads(rpm_out)
                sim.omega += (target - sim.omega) * min(22.0 * dt, 1.0)
                dtr.v = tm.speed if tm.speed_valid else 0.0
                dtr.gear = max(tm.gear, 0)
                if eng.induction != "na":
                    if tm.map_kpa:          # your REAL boost drives its compressor
                        sim.boost = max(0.0, (tm.map_kpa - tm.baro_kpa) * 0.01)
                    else:
                        sim._update_boost(dt)
            else:
                sim.throttle = 0.0
                idle = rpm_to_rads(eng.idle_rpm)
                sim.omega += (idle - sim.omega) * min(3.0 * dt, 1.0)
                rpm_out = sim.omega * 60.0 / (2.0 * math.pi)

            sim.crank_angle += sim.omega * dt

            if show_status and now >= next_status:
                next_status = now + (0.2 if tty else 1.0)
                line = _fmt_status(tm, rpm_out, label, shifting)
                sys.stdout.write(("\r" + line) if tty else (line + "\n"))
                sys.stdout.flush()

            slack = period - (time.monotonic() - now)
            if slack > 0:
                time.sleep(slack)
    except KeyboardInterrupt:
        pass
    finally:
        if show_status and tty:
            sys.stdout.write("\n")
        if synth is not None:
            synth.stop()
        tm.stop()
        if fake is not None:
            fake.stop()
        if shifter.shifts:
            print("  %d gearshifts heard" % shifter.shifts)
        if rmap.learn and rmap.seen_max > 0.0:
            print("  learned: your car idles at %.0f, revved to %.0f"
                  % (rmap.car_idle, rmap.seen_max))
            print("  bake it in next time:  --car-idle %.0f --car-redline %.0f"
                  % (rmap.car_idle, max(rmap.seen_max, rmap.car_redline)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
