"""Headless test for CAR MODE: the OBD-II link, the rpm map and the gear learner.

No hardware and no car required - ``tools.fake_elm327`` stands in for the
dongle AND the vehicle, speaking the real ELM327 protocol over a real socket,
so the client under test is exercised exactly as it will be on the road.

Run with:  py test_obd.py
"""

import time

from engine_sim import presets
from engine_sim.obd import (OBDTelemetry, RpmMap, ShiftDetector, _GearLearner,
                            _clean_hex, _parse_mask, _parse_pids, PID_MAP,
                            PID_RPM, PID_SPEED, PID_PEDAL_REL)
from tools.fake_elm327 import FakeELM327


def check(cond, what):
    if not cond:
        raise AssertionError(what)
    print("  ok   " + what)


# --------------------------------------------------------------------- parser
def test_parser():
    print("\nELM327 reply parsing")

    # single frame, spaces off
    got = _parse_pids(_clean_hex("410C1AF8\r\r"))
    check(got[PID_RPM] == [0x1A, 0xF8], "single-frame rpm parsed")
    check(((0x1A * 256) + 0xF8) / 4.0 == 1726.0, "rpm formula ((A*256)+B)/4")

    # spaces on (ATS1) - some clones ignore ATS0
    got = _parse_pids(_clean_hex("41 0C 1A F8"))
    check(got[PID_RPM] == [0x1A, 0xF8], "spaced reply parsed")

    # multi-frame ISO-TP, the long multi-PID answer
    raw = "013\r0:410C1AF80D2E\r1:5A80" + "0B" + "64\r"
    got = _parse_pids(_clean_hex(raw))
    check(got[PID_RPM] == [0x1A, 0xF8], "multi-frame rpm")
    check(got[PID_SPEED] == [0x2E], "multi-frame speed (46 km/h)")
    check(got[PID_PEDAL_REL] == [0x80], "multi-frame pedal")
    check(got[PID_MAP] == [0x64], "multi-frame MAP (100 kPa)")

    # adapter noise must never turn into data
    for junk in ("SEARCHING...", "NO DATA", "UNABLE TO CONNECT", "?", "STOPPED",
                 "BUS INIT: ...ERROR"):
        check(_parse_pids(_clean_hex(junk)) == {}, "junk ignored: " + junk)

    # a data byte of 0x41 must not be read as a new response header
    got = _parse_pids(_clean_hex("410C41F8"))
    check(got == {PID_RPM: [0x41, 0xF8]}, "0x41 inside data is not a header")

    # supported-PID bitmask, MSB of byte 0 = pid 0x01.  0x3E clears it; 0x3F in
    # byte 1 covers pids 0x0B-0x10, so MAP / rpm / speed are all in.
    sup = _parse_mask([0x3E, 0x3F, 0xA8, 0x13], 0x00)
    check(PID_RPM in sup and PID_SPEED in sup, "bitmask decodes rpm + speed")
    check(PID_MAP in sup, "bitmask decodes MAP")
    check(0x01 not in sup, "a cleared bitmask bit stays unsupported")


# --------------------------------------------------------------------- rpm map
def test_rpm_map():
    print("\nrpm mapping")
    eng = presets.audi_rs3_2024()
    idle, red = eng.idle_rpm, eng.redline_rpm

    m = RpmMap("stretch", car_idle=800.0, car_redline=6500.0, learn=False)
    check(abs(m(800.0, idle, red) - idle) < 1.0, "stretch: your idle -> its idle")
    check(abs(m(6500.0, idle, red) - red) < 1.0,
          "stretch: your redline -> its redline")
    check(m(3650.0, idle, red) > 3650.0,
          "stretch: mid-range lands higher on a higher-revving engine")

    d = RpmMap("direct", learn=False)
    check(abs(d(4000.0, idle, red) - 4000.0) < 1.0, "direct is 1:1")
    check(d(99000.0, idle, red) <= red * 1.02, "direct still clamps at redline")

    r = RpmMap("ratio", ratio=1.5, learn=False)
    check(abs(r(3000.0, idle, red) - 4500.0) < 1.0, "ratio multiplies")

    # learning: a car that revs past the seed widens the map instead of clipping
    L = RpmMap("stretch", car_idle=800.0, car_redline=6000.0, learn=True)
    L.observe(6800.0, 1.0)
    check(L.car_redline >= 6800.0, "learns a redline above the seed")
    for _ in range(400):
        L.observe(700.0, 0.0)
    check(L.car_idle < 800.0, "learns a lower idle from resting samples")


# ----------------------------------------------------------------- gear learner
def test_gear_learner():
    print("\ngear learning (no ratios given)")
    g = _GearLearner()
    seq = []
    for gear_ratio in (110.0, 66.0, 45.0):        # rpm per km/h
        for kmh in range(30, 60, 2):              # a steady pull in that gear
            seq.append(g.update(kmh * gear_ratio, float(kmh)))
    check(seq[-1] == 3, "third distinct ratio is recognised as 3rd gear")
    check(max(seq) == 3, "no phantom gears invented")

    # a shift sweep (ratio changing every sample) must not found new clusters
    before = len(g.centres)
    for kmh, rpm in ((50, 5500), (50, 4800), (50, 4100), (50, 3400)):
        g.update(float(rpm), float(kmh))
    check(len(g.centres) == before, "unstable ratios during a shift are ignored")


# --------------------------------------------------------------- shift detect
def test_shift_detector():
    print("\nupshift detection")
    dt = 0.03

    # a full-throttle upshift: rpm collapses ~1500 in 150 ms, foot still down
    d = ShiftDetector()
    cut = 0
    for rpm in (6400, 6400, 6400, 6100, 5600, 5100, 4900, 4900, 4900):
        if d.update(dt, float(rpm), 1.0, 25.0):
            cut += 1
    check(d.shifts == 1, "upshift detected exactly once")
    check(cut >= 3, "ignition cut held for ~%d ms" % (cut * dt * 1000))

    # lifting off drops rpm just as hard, and must NOT read as a shift
    d = ShiftDetector()
    for rpm in (6400, 6100, 5600, 5100, 4600, 4100):
        d.update(dt, float(rpm), 0.0, 25.0)
    check(d.shifts == 0, "a lift-off is not mistaken for a shift")

    # nor is coming to a stop, nor a downshift blip (rpm rising)
    d = ShiftDetector()
    for rpm in (3000, 2400, 1600, 900):
        d.update(dt, float(rpm), 0.5, 1.0)
    check(d.shifts == 0, "stopping is not a shift (speed gate)")
    d = ShiftDetector()
    for rpm in (3000, 4200, 5400, 6200):
        d.update(dt, float(rpm), 1.0, 25.0)
    check(d.shifts == 0, "a rev-matching blip is not a shift")


# ------------------------------------------------------------- serial transport
def test_serial_path():
    """The COM-port / Bluetooth-SPP transport, exercised with no hardware.

    pyserial's ``socket://`` URL handler gives a real Serial object whose bytes
    go to a TCP peer, so _SerialLink -- the same class a USB or paired-BT
    dongle uses -- is driven end to end against the fake adapter.
    """
    print("\nserial / Bluetooth-SPP transport")
    try:
        import serial                                   # noqa: F401
    except ImportError:
        print("  skip (pyserial not installed)")
        return
    srv = FakeELM327(latency=0.02)
    host, port = srv.start()
    tm = OBDTelemetry(serial_port="socket://%s:%d" % (host, port))
    tm.start()
    try:
        for _ in range(100):
            if tm.is_live():
                break
            time.sleep(0.1)
        check(tm.is_live(), "serial link came up")
        check(tm.pedal_src == PID_PEDAL_REL, "discovery works over serial too")
        check(tm.multi_pid, "multi-PID negotiated over serial")
        t0 = time.monotonic()
        seen = []
        while time.monotonic() - t0 < 6.0:
            seen.append(tm.raw_rpm)
            time.sleep(0.05)
        check(max(seen) - min(seen) > 500.0, "rpm moves over the serial link")
        check(tm.hz > 8.0, "serial sample rate %.1f Hz" % tm.hz)
    finally:
        tm.stop()
        srv.stop()


# ------------------------------------------------------- degraded adapter
def test_cheap_clone():
    """A bad dongle must still work, just worse -- and say so.

    Round-robin polling and the throttle-plate fallback are the paths a cheap
    clone forces us down, so they get tested rather than assumed.
    """
    print("\ncheap clone (few PIDs, no multi-PID)")
    srv = FakeELM327(latency=0.01, allow_multi_pid=False,
                     supported={0x0C, 0x0D, 0x11})
    host, port = srv.start()
    tm = OBDTelemetry(host=host, port=port)
    tm.start()
    try:
        for _ in range(100):
            if tm.is_live():
                break
            time.sleep(0.1)
        check(tm.is_live(), "still comes up on a stripped-down adapter")
        check(not tm.multi_pid, "multi-PID refusal detected, not assumed")
        check(tm.pedal_src == 0x11,
              "falls back to the throttle plate when no pedal PID exists")
        check(0x5A not in tm.supported, "does not claim PIDs the car lacks")
        seen = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 8.0:
            seen.append(tm.raw_rpm)
            time.sleep(0.05)
        check(max(seen) - min(seen) > 500.0,
              "rpm still tracks through round-robin polling")
    finally:
        tm.stop()
        srv.stop()


# ------------------------------------------------------------------ end to end
def test_link():
    print("\nend-to-end link against the fake ELM327")
    srv = FakeELM327(latency=0.02)
    host, port = srv.start()
    tm = OBDTelemetry(host=host, port=port)
    tm.start()
    try:
        for _ in range(100):
            if tm.is_live():
                break
            time.sleep(0.1)
        check(tm.is_live(), "link came up and is delivering samples")
        check(tm.pedal_src == PID_PEDAL_REL,
              "picked the RELATIVE pedal PID (0x5A) as driver demand")
        check(tm.multi_pid, "multi-PID request negotiated (one trip per sample)")
        check(PID_RPM in tm.supported and PID_MAP in tm.supported,
              "supported-PID bitmask read from the car")

        rpms, gears, pedals = [], [], []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 16.0:
            rpms.append(tm.raw_rpm)
            gears.append(tm.gear)
            pedals.append(tm.throttle)
            time.sleep(0.05)

        check(tm.hz > 8.0, "sample rate %.1f Hz is usable" % tm.hz)
        check(0.0 < tm.rtt < 0.5, "round trip measured (%.0f ms)" % (tm.rtt * 1000))
        check(min(rpms) < 1000.0, "saw idle")
        check(max(rpms) > 5000.0, "saw a pull to the top of the range")
        check(max(pedals) > 0.9 and min(pedals) < 0.1, "pedal swings full range")
        check(max(gears) >= 3, "climbed at least to 3rd (%d)" % max(gears))
        check(tm.baro_kpa > 50.0, "barometric reference read for boost")
        check(tm.map_kpa > 0.0, "manifold pressure live")

        # Extrapolation must LEAD the raw signal while rpm is climbing.  Wait
        # for the next pull rather than sampling a fixed window - the drive
        # cycle also idles and coasts, and a lift-off must NOT read as a lead.
        lead = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30.0 and len(lead) < 20:
            if tm._drpm > 2000.0:
                lead.append(tm.rpm - tm.raw_rpm)
            time.sleep(0.02)
        check(len(lead) >= 20, "caught a rising-rpm pull to measure")
        check(sum(lead) / len(lead) > 0.0,
              "rpm is predicted ahead while revs are climbing (+%.0f rpm)"
              % (sum(lead) / len(lead)))
    finally:
        tm.stop()
        srv.stop()


if __name__ == "__main__":
    test_parser()
    test_rpm_map()
    test_gear_learner()
    test_shift_detector()
    test_link()
    test_serial_path()
    test_cheap_clone()
    print("\nAll OBD / car-mode checks passed.")
