"""A fake ELM327 + a fake car, so car mode can be developed and tested without
owning a dongle or sitting in a driveway.

It speaks the real thing: the AT command set, mode-01 PID requests, the
supported-PID bitmasks, single AND multi-PID replies, ISO-TP multi-frame
formatting for the long answers, and the ``>`` prompt.  ``car.py --demo`` starts
one on localhost and points the REAL client at it, so the whole path - socket,
prompt framing, hex parsing, PID discovery, rpm extrapolation, gear learning -
is exercised exactly as it will be in the car.

The simulated car idles, launches, pulls to the limiter, upshifts through a
seven-speed box and lifts off, on a loop.
"""

from __future__ import annotations

import socket
import threading
import time

# what our fake car supports (a plausible modern turbo petrol)
SUPPORTED = {0x04, 0x05, 0x0B, 0x0C, 0x0D, 0x0F, 0x11, 0x1C, 0x20,
             0x33, 0x40, 0x49, 0x4A, 0x5A}

_PID_LEN = {0x04: 1, 0x05: 1, 0x0B: 1, 0x0C: 2, 0x0D: 1, 0x0F: 1, 0x11: 1,
            0x1C: 1, 0x33: 1, 0x49: 1, 0x4A: 1, 0x5A: 1}

IDLE_RPM = 800.0
REDLINE = 6400.0
# rpm per km/h, 1st .. 7th.  Sized so 1st hits the limiter around 58 km/h and
# 7th is a long cruise gear - and so the whole pull stays under the 255 km/h
# ceiling of the single-byte OBD speed PID.
GEARS = (110.0, 66.0, 45.0, 33.0, 26.0, 21.0, 17.0)


def _mask_for(base: int) -> list:
    """Build the 4-byte supported-PID bitmask for the 0x00/0x20/0x40 groups."""
    out = [0, 0, 0, 0]
    for pid in SUPPORTED:
        if base < pid <= base + 0x20:
            idx = pid - base - 1
            out[idx // 8] |= 1 << (7 - (idx % 8))
    return out


class _FakeCar:
    """A drive cycle: idle, launch, pull to the limiter, upshift, lift, repeat."""

    def __init__(self):
        self.t0 = time.monotonic()
        self.rpm = IDLE_RPM
        self.pedal = 0.0
        self.gear = 1
        self.speed = 0.0
        self.map_kpa = 35.0
        self._last = self.t0

    def step(self):
        now = time.monotonic()
        dt = min(now - self._last, 0.2)
        self._last = now
        phase = (now - self.t0) % 26.0

        if phase < 3.0:                      # idle
            self.pedal, self.gear, self.speed = 0.0, 1, 0.0
        elif phase < 20.0:                   # pull through the gears
            self.pedal = 1.0
            self.speed += 8.0 * dt * (7.0 / (self.gear + 2.0))   # km/h
        else:                                # lift off and coast down
            self.pedal = 0.0
            self.speed = max(self.speed - 40.0 * dt, 0.0)

        if self.gear < len(GEARS) and self.speed * GEARS[self.gear - 1] >= REDLINE:
            self.gear += 1                   # upshift at the limiter
        if self.speed < 2.0:
            self.gear = 1

        # In gear the crank is bolted to the wheels: rpm is EXACTLY speed x
        # ratio, no lag.  Only the launch (slipping clutch) and idle are eased,
        # which is what makes the rpm/kmh plateaus a gearbox actually shows.
        geared = self.speed * GEARS[self.gear - 1]
        if geared < IDLE_RPM or self.speed < 2.0:
            self.rpm += (max(geared, IDLE_RPM) - self.rpm) * min(6.0 * dt, 1.0)
        else:
            self.rpm = min(geared, REDLINE)
        self.map_kpa = 30.0 + 170.0 * self.pedal * min(self.rpm / 3000.0, 1.0)

    def value(self, pid: int):
        self.step()
        if pid == 0x0C:
            v = int(self.rpm * 4.0)
            return [(v >> 8) & 0xFF, v & 0xFF]
        if pid == 0x0D:
            return [min(int(self.speed), 255)]
        if pid == 0x0B:
            return [min(int(self.map_kpa), 255)]
        if pid == 0x33:
            return [101]
        if pid in (0x11, 0x49, 0x4A, 0x5A):
            return [int(self.pedal * 255.0)]
        if pid == 0x04:
            return [int(self.pedal * 200.0)]
        if pid == 0x05:
            return [90 + 40]                 # coolant, A-40
        if pid == 0x0F:
            return [30 + 40]                 # intake air temp
        if pid == 0x1C:
            return [6]
        if pid == 0x20:
            return _mask_for(0x20)
        if pid == 0x40:
            return _mask_for(0x40)
        return None


class FakeELM327:
    """TCP server that answers like a WiFi ELM327 clone."""

    def __init__(self, host="127.0.0.1", port=0, latency=0.03):
        self.host, self.port = host, port
        self.latency = latency               # per-request round trip to imitate
        self.sock = None
        self.thread = None
        self.running = False

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(1)
        self.host, self.port = self.sock.getsockname()
        self.running = True
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()
        return self.host, self.port

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    # ---------------------------------------------------------- protocol
    def _serve(self, conn):
        car = _FakeCar()
        echo = False
        spaces = True
        buf = b""
        conn.sendall(b"ELM327 v1.5\r\r>")
        while self.running:
            try:
                chunk = conn.recv(256)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\r" in buf:
                line, _, buf = buf.partition(b"\r")
                cmd = line.decode("ascii", "ignore").strip().upper().replace(" ", "")
                if not cmd:
                    continue
                time.sleep(self.latency)
                out = ""
                if echo:
                    out += cmd + "\r"
                if cmd.startswith("AT"):
                    if cmd.startswith("ATE"):
                        echo = cmd.endswith("1")
                    elif cmd.startswith("ATS"):
                        spaces = cmd.endswith("1")
                    out += ("ELM327 v1.5\r" if cmd == "ATZ" else "OK\r")
                elif cmd.startswith("01"):
                    out += self._mode01(cmd[2:], car, spaces)
                else:
                    out += "?\r"
                try:
                    conn.sendall((out + "\r>").encode("ascii"))
                except OSError:
                    return
        try:
            conn.close()
        except Exception:
            pass

    def _mode01(self, arg: str, car: _FakeCar, spaces: bool) -> str:
        pids = [int(arg[i:i + 2], 16) for i in range(0, len(arg) - 1, 2)]
        if not pids:
            return "?\r"
        payload = [0x41]
        for pid in pids:
            if pid == 0x00:
                data = _mask_for(0x00)
            elif pid in SUPPORTED:
                data = car.value(pid)
            else:
                continue
            if data is None:
                continue
            payload.append(pid)
            payload.extend(data)
        if len(payload) == 1:
            return "NO DATA\r"

        if len(payload) <= 7:                       # single CAN frame
            body = "".join("%02X" % b for b in payload)
            if spaces:
                body = " ".join("%02X" % b for b in payload)
            return body + "\r"

        # ISO-TP multi-frame, the way a real adapter prints a long answer:
        #   <total length>
        #   0: <first 7 bytes>
        #   1: <next 7 bytes> ...
        lines = ["%03X" % len(payload)]
        for i in range(0, len(payload), 7):
            frame = payload[i:i + 7]
            sep = " " if spaces else ""
            lines.append("%d:%s%s" % (i // 7, sep,
                                      sep.join("%02X" % b for b in frame)))
        return "\r".join(lines) + "\r"


if __name__ == "__main__":
    srv = FakeELM327(port=35000)
    host, port = srv.start()
    print("fake ELM327 listening on %s:%d - Ctrl-C to stop" % (host, port))
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        srv.stop()
