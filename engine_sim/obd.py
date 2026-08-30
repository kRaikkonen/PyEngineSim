"""OBD-II live telemetry - play the synthesizer at your REAL car's engine speed.

This is the road-car sibling of :mod:`engine_sim.telemetry` (the Forza UDP
listener).  It exposes the SAME duck-typed fields (``rpm``, ``throttle``,
``speed``, ``gear``, ``boost_psi``, ``is_live()`` ...), so anything that can be
driven by Forza can be driven by the car you are sitting in.

Hardware: any ELM327-compatible dongle in the OBD-II port.

  * **WiFi** (``--host``): the dongle makes its own AP, usually
    ``192.168.0.10:35000``.  Zero dependencies - it is a plain TCP socket, so
    it works on desktop, on a Pi and on Android alike.  Recommended.
  * **Serial / Bluetooth-SPP / USB** (``--serial``): needs ``pyserial``; on
    Windows a paired BT dongle shows up as a COM port.

WHAT MAKES IT FEEL RIGHT
------------------------
Three things decide whether this is fun or nauseating, and all three are
latency:

1. **Pedal, not throttle plate.**  We prefer the *accelerator pedal position*
   PID (0x5A / 0x49) over the throttle-plate PID (0x11).  On a turbo car the
   pedal LEADS the crankshaft by 100-300 ms (that lag is the turbo spooling),
   so a pedal-driven synth starts shouting the instant your foot moves - which
   quietly pays back part of the audio-output latency.
2. **Multi-PID requests.**  ELM327 v1.4+ answers up to 6 PIDs in one message.
   We probe for it: if it works every channel arrives in ONE round trip
   (~25-40 Hz instead of ~8-12 Hz round-robin).  If it does not, we fall back
   to polling them one at a time.
3. **Extrapolation.**  Every sample is timestamped, so we know the round-trip
   time.  ``rpm`` is projected forward by (measured RTT + your configured
   output latency) along the smoothed drpm/dt.  On a sustained pull - which is
   when it matters - the prediction is good; at a shift it is wrong for ~100 ms,
   which is why the correction is clamped.

Nothing here fabricates data: every channel is a standard SAE J1979 mode-01
PID, the supported set is read from the car's own 0x00/0x20/0x40 bitmasks, and
the gearbox ratios are LEARNED from the car rather than hard-coded.
"""

from __future__ import annotations

import re
import socket
import threading
import time

# --------------------------------------------------------------------------
# SAE J1979 mode-01 PIDs we use.  pid -> number of data bytes.
# --------------------------------------------------------------------------
PID_LOAD = 0x04        # calculated engine load, A*100/255 %
PID_MAP = 0x0B         # intake manifold ABSOLUTE pressure, kPa
PID_RPM = 0x0C         # engine rpm, ((A*256)+B)/4
PID_SPEED = 0x0D       # vehicle speed, km/h
PID_TPS = 0x11         # throttle PLATE position, A*100/255 %
PID_BARO = 0x33        # barometric pressure, kPa
PID_PEDAL_D = 0x49     # accelerator pedal position D, A*100/255 %
PID_PEDAL_E = 0x4A     # accelerator pedal position E
PID_PEDAL_REL = 0x5A   # RELATIVE accelerator pedal position (0 at rest)

_PID_LEN = {PID_LOAD: 1, PID_MAP: 1, PID_RPM: 2, PID_SPEED: 1, PID_TPS: 1,
            PID_BARO: 1, PID_PEDAL_D: 1, PID_PEDAL_E: 1, PID_PEDAL_REL: 1}

# The "PIDs supported" queries answer with a 4-byte bitmask.  They are kept in
# a SEPARATE length table: 0x00 is also what CAN pads a short frame with, so
# letting the hot polling path treat 0x00 as a 4-byte PID would let padding
# masquerade as data.  Discovery opts in; polling never does.
_MASK_LEN = dict(_PID_LEN)
_MASK_LEN.update({0x00: 4, 0x20: 4, 0x40: 4})

# pedal sources in order of preference (best driver-demand signal first)
_PEDAL_ORDER = (PID_PEDAL_REL, PID_PEDAL_D, PID_PEDAL_E, PID_TPS, PID_LOAD)

_HEX = re.compile(r"^[0-9A-F]+$")
_JUNK = ("SEARCHING", "UNABLE", "NO DATA", "STOPPED", "ERROR", "BUS", "CAN ",
         "?", "OK", "ELM", "V1.", "V2.")


def _clean_hex(raw: str) -> str:
    """Flatten an ELM327 reply into one hex string.

    Handles both the single-frame form (410C1AF8) and the ISO-TP multi-frame
    form the adapter prints for multi-PID answers::

        013              <- total length header (skipped)
        0:410C1AF80D00   <- line index prefix (stripped)
        1:5A3F00000000
    """
    parts, declared = [], 0
    for line in raw.replace("\r", "\n").split("\n"):
        s = line.strip().upper().replace(" ", "")
        if not s:
            continue
        if any(j in s for j in _JUNK):
            continue
        if len(s) > 2 and s[1] == ":":          # "0:" / "1:" frame index
            s = s[2:]
        if not _HEX.match(s):
            continue
        if len(s) == 3 and not parts:            # ISO-TP total-length header
            declared = int(s, 16)
            continue
        parts.append(s)
    hexs = "".join(parts)
    # The length header bounds the real data, so the CAN padding after it
    # (0x00 / 0xAA filling the last frame) can never be read as another PID.
    if declared and 2 * declared <= len(hexs):
        hexs = hexs[:2 * declared]
    return hexs


def _parse_pids(hexs: str, lengths: dict = None) -> dict:
    """Pull the values out of a mode-01 reply.

    A multi-PID answer carries ONE 0x41 header and then a run of
    ``<pid> <data...>`` pairs -- ``41 0C 1A F8 0D 2E 5A 80`` -- not a fresh
    header per PID.  So: find the header, then walk the chain, and resync on
    the next 0x41 if a byte turns up that is not a PID we asked for.  Walking
    (rather than searching for "41xx") also means a data byte that happens to
    be 0x41 cannot masquerade as a header.
    """
    lengths = _PID_LEN if lengths is None else lengths
    out, i, n = {}, 0, len(hexs)
    while i + 4 <= n:
        if hexs[i:i + 2] != "41":
            i += 2
            continue
        i += 2                                   # past the mode-01 reply header
        while i + 2 <= n:
            pid = int(hexs[i:i + 2], 16)
            ln = lengths.get(pid)
            if ln is None or i + 2 + 2 * ln > n:
                break                            # padding / unknown -> resync
            out[pid] = [int(hexs[i + 2 + 2 * k:i + 4 + 2 * k], 16)
                        for k in range(ln)]
            i += 2 + 2 * ln
    return out


def _parse_mask(data: list, base: int) -> set:
    """Decode a 4-byte "PIDs supported" bitmask (the 0x00/0x20/0x40 replies)."""
    got = set()
    for byte_i, byte in enumerate(data[:4]):
        for bit in range(8):
            if byte & (1 << (7 - bit)):
                got.add(base + byte_i * 8 + bit + 1)
    return got


# --------------------------------------------------------------------------
# transports
# --------------------------------------------------------------------------
class _TcpLink:
    """WiFi ELM327 - a raw TCP socket, no third-party dependency."""

    kind = "wifi"

    def __init__(self, host: str, port: int, timeout: float = 3.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def write(self, data: bytes):
        self.sock.sendall(data)

    def read_prompt(self, timeout: float) -> str:
        end = time.monotonic() + timeout
        while b">" not in self.buf:
            left = end - time.monotonic()
            if left <= 0:
                break
            try:
                self.sock.settimeout(max(0.02, left))
                chunk = self.sock.recv(1024)
            except socket.timeout:
                break
            if not chunk:
                raise OSError("adapter closed the connection")
            self.buf += chunk
        if b">" in self.buf:
            head, _, self.buf = self.buf.partition(b">")
            return head.decode("ascii", "ignore")
        out, self.buf = self.buf.decode("ascii", "ignore"), b""
        return out

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class _SerialLink:
    """Bluetooth-SPP / USB ELM327 through a COM / tty port (needs pyserial)."""

    kind = "serial"

    def __init__(self, port: str, baud: int = 38400, timeout: float = 3.0):
        import serial                       # optional dependency, imported late
        if "://" in port:
            # pyserial URL handlers: rfcomm and friends on Linux, plus
            # socket:// and loop:// which let the whole serial path be tested
            # against the fake adapter with no hardware attached.
            self.ser = serial.serial_for_url(port, baudrate=baud, timeout=0.05)
        else:
            self.ser = serial.Serial(port, baud, timeout=0.05)
        self.timeout = timeout
        self.buf = b""

    def write(self, data: bytes):
        self.ser.write(data)

    def read_prompt(self, timeout: float) -> str:
        end = time.monotonic() + timeout
        while b">" not in self.buf:
            if time.monotonic() > end:
                break
            # read(256) would sit out the WHOLE port timeout waiting for 256
            # bytes that never come - a reply is ~20.  Block for one byte, then
            # take whatever else already arrived: the reply is served the
            # moment it lands instead of on the next timeout tick.
            chunk = self.ser.read(1)
            if chunk:
                extra = getattr(self.ser, "in_waiting", 0)
                if extra:
                    chunk += self.ser.read(extra)
                self.buf += chunk
        if b">" in self.buf:
            head, _, self.buf = self.buf.partition(b">")
            return head.decode("ascii", "ignore")
        out, self.buf = self.buf.decode("ascii", "ignore"), b""
        return out

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def list_serial_ports() -> list:
    """[(device, description)] for every serial port - which COM is the dongle."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return [(p.device, p.description or "") for p in list_ports.comports()]


# --------------------------------------------------------------------------
# rpm mapping: your car's rev range -> the simulated engine's rev range
# --------------------------------------------------------------------------
class RpmMap:
    """Map real crankshaft rpm onto the simulated engine's rev range.

    ``direct``   1:1.  Right when the two engines rev alike (an A3 wearing the
                 RS3's five-cylinder: 6500 vs 7000, near enough).
    ``stretch``  affine, idle->idle and redline->redline.  This is what you want
                 for an engine that revs somewhere else entirely: flooring a
                 6500 rpm road car then makes the V12 sing at its own 8500
                 instead of stopping two thirds of the way up.
    ``ratio``    REDLINE-PROPORTIONAL: sim = car * (sim_redline / car_redline),
                 so your 6500 redline lands exactly on the target's 9500 and
                 everything below scales with it.  Unlike ``stretch`` it goes
                 through zero rather than pinning the idle ends together, so
                 doubling your revs doubles the note -- the proportion is kept
                 and only the SCALE changes.  ``ratio`` is a trim on top, for
                 when you want it a little higher or lower than exact.

    The car end of ``stretch`` is SEEDED from a profile and then LEARNED: the
    highest rpm ever seen (and the resting rpm) beat the seed, so a wrong guess
    self-corrects within one drive instead of squashing the top end forever.
    """

    MODES = ("direct", "stretch", "ratio")

    def __init__(self, mode="stretch", car_idle=800.0, car_redline=6500.0,
                 ratio=1.0, learn=True):
        if mode not in self.MODES:
            raise ValueError("rpm map mode must be one of %s" % (self.MODES,))
        self.mode = mode
        self.car_idle = float(car_idle)
        self.car_redline = float(car_redline)
        self.ratio = float(ratio)
        self.learn = bool(learn)
        self.seen_idle = None
        self.seen_max = 0.0

    def observe(self, rpm: float, pedal: float):
        """Fold a live sample into the learned car rev range."""
        if not self.learn or rpm < 300.0:
            return
        if rpm > self.seen_max:
            self.seen_max = rpm
            if rpm > self.car_redline:          # the seed was too low
                self.car_redline = rpm
        if pedal < 0.03 and rpm < 1400.0:
            self.seen_idle = rpm if self.seen_idle is None else (
                self.seen_idle + (rpm - self.seen_idle) * 0.02)
            if self.seen_idle < self.car_idle:
                self.car_idle = self.seen_idle

    def __call__(self, rpm: float, eng_idle: float, eng_redline: float) -> float:
        if self.mode == "direct":
            out = rpm
        elif self.mode == "ratio":
            # scale by the RATIO OF THE REDLINES, so the two rev ceilings line
            # up and everything below keeps its proportion
            out = rpm * (max(eng_redline, 1.0)
                         / max(self.car_redline, 1.0)) * self.ratio
        else:
            span = max(self.car_redline - self.car_idle, 500.0)
            frac = (rpm - self.car_idle) / span
            out = eng_idle + frac * max(eng_redline - eng_idle, 500.0)
        # never below a plausible idle, never past the rev limiter
        return min(max(out, eng_idle * 0.55), eng_redline * 1.02)

    def preview(self, eng_idle: float, eng_redline: float, steps: int = 8):
        """[(car_rpm, sim_rpm)] across the range - a pre-drive sanity check."""
        lo, hi = self.car_idle, self.car_redline
        out = []
        for i in range(steps):
            car = lo + (hi - lo) * i / (steps - 1.0)
            out.append((car, self(car, eng_idle, eng_redline)))
        return out


# --------------------------------------------------------------------------
# gearbox ratio learner
# --------------------------------------------------------------------------
class _GearLearner:
    """Work out which gear the car is in without being told its ratios.

    rpm/kmh is constant within a gear, so the ratios show up as plateaus.  We
    cluster them online (a value within 8 % joins a cluster and eases it); the
    gear number is the cluster's rank, highest ratio = 1st.  Self-calibrating,
    so it stays honest about a gearbox whose numbers we were never given.  It
    only feeds the straight-cut gear whine, so being wrong for the first minute
    of a drive costs nothing.
    """

    def __init__(self, max_gears=8, tol=0.08):
        self.centres = []
        self.max_gears = max_gears
        self.tol = tol
        self.gear = 0
        self._prev_r = None

    def update(self, rpm: float, speed_kmh: float) -> int:
        if speed_kmh < 8.0 or rpm < 700.0:
            self._prev_r = None
            self.gear = 1 if speed_kmh > 1.0 else 0
            return self.gear
        r = rpm / speed_kmh
        # Only learn from a STABLE ratio.  A shift, a slipping clutch or a
        # launch sweeps rpm/kmh across everything in between; letting those
        # samples found clusters invents gears that do not exist.
        prev, self._prev_r = self._prev_r, r
        if prev is None or abs(r - prev) / r > 0.02:
            return self.gear
        best, bi = None, -1
        for i, c in enumerate(self.centres):
            d = abs(c - r) / c
            if d < self.tol and (best is None or d < best):
                best, bi = d, i
        if bi < 0:
            if len(self.centres) < self.max_gears:
                self.centres.append(r)
        else:
            self.centres[bi] += (r - self.centres[bi]) * 0.05
        self.centres.sort(reverse=True)          # highest rpm/kmh = 1st gear
        self.gear = 1 + min(range(len(self.centres)),
                            key=lambda i: abs(self.centres[i] - r))
        return self.gear


# --------------------------------------------------------------------------
# upshift detector
# --------------------------------------------------------------------------
class ShiftDetector:
    """Spot a gearshift so the exhaust can bang the way the real one does.

    A dual-clutch upshift cuts ignition for around a tenth of a second, and
    that torque interruption is what makes the bang.  No car broadcasts "I am
    shifting", but it is unmistakable in the data anyway: rpm collapsing FAST
    while the pedal stays down and the car is not slowing down.  Lifting off
    also drops rpm, so the pedal test is what separates the two - and it is why
    a downshift blip (rpm RISING) never triggers it.

    ``update`` returns True while the cut is in progress; feed that to the
    synth as a closed throttle.
    """

    def __init__(self, drop_rate=-3000.0, min_pedal=0.35, min_speed=5.0,
                 cut=0.12):
        self.drop_rate = drop_rate      # rpm/s: steeper than any natural decay
        self.min_pedal = min_pedal      # still asking for power -> not a lift
        self.min_speed = min_speed      # m/s: rules out coming to a stop
        self.cut = cut                  # seconds of ignition cut to imitate
        self.t = 0.0
        self.shifts = 0
        self._prev = None

    def update(self, dt: float, rpm: float, pedal: float, speed: float) -> bool:
        prev, self._prev = self._prev, rpm
        if self.t > 0.0:
            self.t -= dt
            return self.t > 0.0
        if prev is None or dt <= 0.0:
            return False
        if ((rpm - prev) / dt < self.drop_rate and pedal > self.min_pedal
                and speed > self.min_speed):
            self.t = self.cut
            self.shifts += 1
            return True
        return False


# --------------------------------------------------------------------------
# the telemetry source
# --------------------------------------------------------------------------
class OBDTelemetry:
    """Background ELM327 poller exposing Forza-compatible telemetry fields."""

    def __init__(self, host="192.168.0.10", port=35000, serial_port=None,
                 baud=38400, protocol="6", out_latency=0.0, poll_hz=50.0):
        self.host, self.port = host, port
        self.serial_port, self.baud = serial_port, baud
        self.protocol = protocol
        self.out_latency = float(out_latency)   # seconds of audio output lag
        self.poll_dt = 1.0 / max(poll_hz, 1.0)

        self._link = None
        self._thread = None
        self._running = False
        self.error = None
        self.status = "idle"
        self.adapter_id = ""            # ATI  - what the dongle calls itself
        self.protocol_name = ""         # ATDP - what it actually negotiated

        # --- Forza-compatible surface (see engine_sim/telemetry.py) ---
        self.is_race_on = True
        self.rpm = 0.0                 # EXTRAPOLATED rpm - what you play
        self.max_rpm = 6500.0          # learned from the car
        self.idle_rpm = 800.0
        self.throttle = 0.0
        self.throttle_valid = True
        self.gear = 0
        self.speed = 0.0               # m/s
        self.speed_valid = False
        self.boost_psi = 0.0
        self.brake = 0.0
        self.clutch = 0.0
        self.power = 0.0
        self.torque = 0.0
        self.dash_valid = False
        self.accel_x = 0.0
        self.accel_z = 0.0
        self.packet_len = 0

        # --- OBD extras ---
        self.raw_rpm = 0.0             # un-extrapolated, for shift detection
        self.pedal_src = None
        self.supported = set()
        self.multi_pid = False
        self.rtt = 0.0                 # measured request round-trip, seconds
        self.hz = 0.0                  # achieved sample rate
        self.baro_kpa = 101.3
        self.map_kpa = 0.0
        self._poll_pids = [PID_RPM]
        self._rr = 0
        self._drpm = 0.0
        self._last_rpm_t = 0.0
        self._last_packet = 0.0
        self._gears = _GearLearner()

    # ---------------------------------------------------------- lifecycle
    def start(self) -> bool:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        self._close()

    def is_live(self, timeout: float = 1.5) -> bool:
        return self._last_packet > 0.0 and (
            time.monotonic() - self._last_packet) < timeout

    # ------------------------------------------------------------- link
    def _close(self):
        if self._link is not None:
            self._link.close()
            self._link = None

    def _connect(self) -> bool:
        self._close()
        try:
            if self.serial_port:
                self._link = _SerialLink(self.serial_port, self.baud)
            else:
                self._link = _TcpLink(self.host, self.port)
        except Exception as exc:
            self.error = str(exc)
            self.status = "no adapter"
            return False
        self.error = None
        return self._init_adapter()

    def _cmd(self, text: str, timeout: float = 1.0) -> str:
        self._link.write((text + "\r").encode("ascii"))
        return self._link.read_prompt(timeout)

    def _init_adapter(self) -> bool:
        """ATZ + the usual quiet-mode setup, then discover what the car offers."""
        try:
            self.status = "init"
            # An adapter greets a new connection with its banner and a prompt.
            # Drain it first: otherwise every reply is read one command late -
            # discovery parses the wrong answers and every sample carries an
            # extra round trip of lag.
            self._link.read_prompt(0.4)
            self._cmd("ATZ", 4.0)              # reset (slow: the chip reboots)
            for c in ("ATE0",       # echo off - otherwise every reply is doubled
                      "ATL0",       # no linefeeds
                      "ATS0",       # no spaces: shorter frames, less parsing
                      "ATH0",       # no headers
                      "ATAT1"):     # adaptive timing: back off only when needed
                self._cmd(c, 1.5)
            # ISO 15765-4 CAN 11-bit / 500 kbaud is what every MQB-platform VAG
            # car speaks; protocol "0" lets the adapter hunt for it instead
            # (costs a slow first request).
            self._cmd("ATSP" + str(self.protocol), 2.0)
            self.adapter_id = " ".join(self._cmd("ATI", 1.5).split())
            self.protocol_name = " ".join(self._cmd("ATDP", 2.0).split())
            self._discover()
            self.status = "live"
            return True
        except Exception as exc:
            self.error = str(exc)
            self.status = "init failed"
            return False

    def _discover(self):
        """Read the car's own supported-PID bitmasks, then pick our channels."""
        sup = set()
        for base, pid in ((0x00, "0100"), (0x20, "0120"), (0x40, "0140")):
            got = _parse_pids(_clean_hex(self._cmd(pid, 2.0)), _MASK_LEN)
            data = got.get(base)
            if not data:
                break
            sup |= _parse_mask(data, base)
            if (base + 0x20) not in sup:       # no continuation bit -> stop
                break
        self.supported = sup
        if not sup:                            # dongle refused the bitmask query
            self.supported = {PID_RPM, PID_SPEED, PID_TPS, PID_MAP, PID_LOAD}

        self.pedal_src = next((p for p in _PEDAL_ORDER if p in self.supported),
                              None)
        self.speed_valid = PID_SPEED in self.supported

        if PID_BARO in self.supported:         # ambient reference for boost
            got = _parse_pids(_clean_hex(self._cmd("01%02X" % PID_BARO, 1.5)))
            if PID_BARO in got:
                self.baro_kpa = float(got[PID_BARO][0])

        # channel list, de-duplicated (pedal_src may BE the throttle PID)
        wanted = [PID_RPM, self.pedal_src, PID_SPEED, PID_MAP]
        seen, ordered = set(), []
        for p in wanted:
            if p and p in self.supported and p not in seen:
                seen.add(p)
                ordered.append(p)
        self._poll_pids = ordered or [PID_RPM]

        # probe the multi-PID request (ELM327 >= 1.4): all channels, one trip
        req = "01" + "".join("%02X" % p for p in self._poll_pids)
        got = _parse_pids(_clean_hex(self._cmd(req, 2.0)))
        self.multi_pid = bool(got) and all(p in got for p in self._poll_pids)
        self._rr = 0                            # round-robin cursor (fallback)

    # -------------------------------------------------------------- loop
    def _loop(self):
        backoff = 0.5
        while self._running:
            if self._link is None:
                if not self._connect():
                    time.sleep(backoff)
                    backoff = min(backoff * 1.6, 5.0)
                    continue
                backoff = 0.5
            try:
                self._poll_once()
            except Exception as exc:
                self.error = str(exc)
                self.status = "link lost"
                self._close()
                time.sleep(0.3)

    def _poll_once(self):
        t0 = time.monotonic()
        if self.multi_pid:
            req = "01" + "".join("%02X" % p for p in self._poll_pids)
        else:
            req = "01%02X" % self._poll_pids[self._rr % len(self._poll_pids)]
            self._rr += 1
        got = _parse_pids(_clean_hex(self._cmd(req, 1.0)))
        t1 = time.monotonic()
        if not got:
            return
        self.rtt += ((t1 - t0) - self.rtt) * 0.2
        if self._last_packet:
            dt = max(t1 - self._last_packet, 1e-3)
            self.hz += (1.0 / dt - self.hz) * 0.2
        self._ingest(got, t1)
        self._last_packet = t1
        left = self.poll_dt - (time.monotonic() - t0)
        if left > 0:
            time.sleep(left)

    def _ingest(self, got: dict, now: float):
        if PID_RPM in got:
            a, b = got[PID_RPM]
            rpm = ((a * 256) + b) / 4.0
            dt = now - self._last_rpm_t if self._last_rpm_t else 0.0
            if 0.0 < dt < 0.5:
                self._drpm += ((rpm - self.raw_rpm) / dt - self._drpm) * 0.35
            self._last_rpm_t = now
            self.raw_rpm = rpm
            # project forward over the whole pipeline lag (poll RTT + output)
            lead = min(self.rtt + self.out_latency, 0.35)
            self.rpm = max(rpm + max(min(self._drpm * lead, 800.0), -800.0), 0.0)
            if rpm > self.max_rpm:
                self.max_rpm = rpm

        if self.pedal_src is not None and self.pedal_src in got:
            self.throttle = min(max(got[self.pedal_src][0] / 255.0, 0.0), 1.0)

        if PID_SPEED in got:
            kmh = float(got[PID_SPEED][0])
            self.speed = kmh / 3.6
            self.gear = self._gears.update(self.raw_rpm, kmh)

        if PID_MAP in got:
            self.map_kpa = float(got[PID_MAP][0])
            self.boost_psi = (self.map_kpa - self.baro_kpa) * 0.1450377


# --------------------------------------------------------------------------
# car profiles - SEEDS for the rpm map (RpmMap.observe then learns the truth)
# --------------------------------------------------------------------------
CAR_PROFILES = {
    #  key          idle   redline
    "a3": (800.0, 6500.0),          # Audi A3 8Y / 8Y facelift, TFSI petrol
    "generic": (800.0, 6500.0),
    "diesel": (750.0, 4800.0),
    "highrev": (900.0, 8500.0),
}
