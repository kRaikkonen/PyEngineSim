"""
Forza Horizon / Forza Motorsport UDP telemetry receiver.

Forza's in-game "Data Out" feature broadcasts a binary telemetry packet over UDP
~60 times a second.  This listens for it on a port and exposes the live engine
rpm (plus max/idle rpm and throttle) so the synthesizer can be played at the
real car's rpm instead of the built-in physics.

Setup in-game: HUD / Gameplay settings -> Data Out -> On, IP = this machine's
address (127.0.0.1 if same PC), Port = 5300 (matching FORZA_PORT below).

The packet layout is the same "sled" header across every Forza title, so the
rpm fields are at fixed offsets regardless of game/version; the throttle and gear
live in the longer "dash" packet.  (Offsets confirmed against the Forza data
format docs — see PARSE below.)
"""

from __future__ import annotations

import socket
import struct
import threading
import time

FORZA_PORT = 5300

# Sled header (all little-endian) — byte-for-byte IDENTICAL in every Forza title
# (FM7, FM2023, FH4, FH5, FH6), so rpm parsing is version-independent:
#   s32 IsRaceOn @0, u32 TimestampMS @4, f32 EngineMaxRpm @8,
#   f32 EngineIdleRpm @12, f32 CurrentEngineRpm @16
_SLED = struct.Struct("<iIfff")

# Throttle (Accel u8) + Gear (u8) live in the longer "dash" packet.  Their offset
# shifts +12 only for the FH4/FH5 Horizon packets (323/324 bytes).  Verified
# against the official Forza Data Out docs + multiple open-source parsers.
#   311  FM7 dash           -> Accel@303 Gear@307
#   323/324 FH4/FH5 dash    -> +12 -> Accel@315 Gear@319
#   331  FM2023 dash        -> Accel@303 Gear@307 (extras appended after)
#   FH6 / unknown larger    -> rpm still exact; throttle derived from rpm
_KNOWN_DASH = {311, 323, 324, 331}
_BASE_ACCEL = 303
_BASE_GEAR = 307


class ForzaTelemetry:
    """Background UDP listener for Forza Data Out telemetry."""

    def __init__(self, port: int = FORZA_PORT):
        self.port = port
        self._sock = None
        self._thread = None
        self._running = False
        self.error = None

        # latest decoded values (plain attribute reads are atomic enough here)
        self.is_race_on = False
        self.rpm = 0.0
        self.max_rpm = 8000.0
        self.idle_rpm = 800.0
        self.throttle = 0.0
        self.throttle_valid = False   # False on FH6/unknown -> derive from rpm
        self.gear = 0
        self.packet_len = 0
        self._last_packet = 0.0

    # ------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("", self.port))
            self._sock.settimeout(0.4)
        except Exception as exc:
            self.error = str(exc)
            self._sock = None
            return False
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def is_live(self, timeout: float = 1.5) -> bool:
        return self._last_packet > 0.0 and (time.monotonic() - self._last_packet) < timeout

    # ----------------------------------------------------------------- loop
    def _loop(self):
        while self._running:
            try:
                data, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self._parse(data)

    def _parse(self, data: bytes):
        if len(data) < _SLED.size:
            return
        try:
            is_race, _ts, max_rpm, idle_rpm, cur_rpm = _SLED.unpack_from(data, 0)
        except struct.error:
            return
        # sanity: rpm fields should be plausible
        if not (0.0 <= cur_rpm < 30000.0 and 0.0 < max_rpm < 30000.0):
            return
        self.is_race_on = bool(is_race)
        self.max_rpm = max_rpm
        self.idle_rpm = idle_rpm
        self.rpm = cur_rpm
        n = len(data)
        self.packet_len = n
        # throttle + gear only from a KNOWN dash length (offset shifts +12 on FH4/5)
        if n in _KNOWN_DASH:
            shift = 12 if n in (323, 324) else 0
            try:
                self.throttle = data[_BASE_ACCEL + shift] / 255.0
                self.gear = data[_BASE_GEAR + shift]
                self.throttle_valid = True
            except IndexError:
                self.throttle_valid = False
        else:
            # FH6 / sled-only / future: rpm is exact, throttle derived from rpm
            self.throttle_valid = False
        self._last_packet = time.monotonic()
