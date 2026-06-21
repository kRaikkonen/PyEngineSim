"""
Real-time engine audio — pure physics, no recordings.

The sound is generated the way the real machine makes it:

  1. EXCITATION (the 'vocal cords').  Each cylinder, when its exhaust valve
     opens, releases a blowdown pressure pulse whose strength comes from the gas
     model (:meth:`Simulator.blowdown_pressure`).  Summed at the cylinders' firing
     phases, with turbulence noise gated by the pulses, that is the raw exhaust
     waveform — real pulse shape, real load/rpm dynamics, rev-limit cuts and all.

  2. RESONANCE (the 'throat').  The pulses ring the exhaust pipe.  A pipe is an
     acoustic delay line: a pulse travels at the speed of sound, reflects at the
     open end, and re-circulates — a feedback comb filter.  We tune that comb's
     delay from the real pipe length and the HOT-gas speed of sound
     (:meth:`Simulator.exhaust_sound_speed`, ~470-670 m/s, *not* 343), so the
     resonant pitch is physical and slides up with load.  An open end inverts the
     pulse (negative feedback), giving the odd-harmonic quarter-wave 'hollow'
     exhaust character.  A Helmholtz biquad adds the muffler/chamber resonance.

This is more physics-driven than the original Engine Simulator, which convolves
the (also physics-derived) excitation with a *recorded* impulse-response .wav.
Here every resonance parameter is computed from geometry and gas properties.

Audio is optional (needs ``sounddevice``); ``scipy`` sharpens the filters.
"""

from __future__ import annotations

import math
import os
import threading

import numpy as np

try:
    import sounddevice as sd
    _HAVE_SD = True
except Exception:                       # pragma: no cover
    _HAVE_SD = False

try:
    from scipy.signal import lfilter, butter
    _HAVE_SCIPY = True
except Exception:                       # pragma: no cover
    _HAVE_SCIPY = False

from .engine import P_ATM

SAMPLE_RATE = 44100
BLOCK = 256

# The firing 'body' is voiced as a metal POWER CHORD — root + fifth, doubled
# across octaves (NO third, so the perfect intervals lock solid instead of
# floating).  Each entry is (pitch-ratio vs the firing pitch, level).  This
# multi-note stack is what gives the bangs a full, high-yet-solid wall instead
# of a thin single tone.  (ratio 1.5 = fifth, 0.5/2.0 = octave down/up.)
_POWER_CHORD = (
    (0.5, 0.70),    # octave below  (weight / chest)
    (1.0, 1.00),    # root
    (1.5, 0.85),    # fifth
    (2.0, 0.55),    # octave above
    (3.0, 0.38),    # fifth + octave (top sparkle, kept moderate)
)

# Selectable firing-body chord voicings (hidden number-key 1-6 hotkeys).  Each
# is (pitch-ratio vs firing pitch, level); max 5 voices to fit the filter state.
_FIRE_CHORDS = (
    _POWER_CHORD,                                                      # 1 power (default)
    ((0.5, 0.6), (1.0, 1.0), (1.26, 0.7), (1.5, 0.7), (2.0, 0.4)),    # 2 major power
    ((0.5, 0.5), (1.0, 1.0), (1.0595, 0.8), (2.0, 0.45)),             # 3 root + minor 2nd
    ((1.0, 1.0), (1.189, 0.65), (1.414, 0.62), (1.782, 0.5), (0.5, 0.5)),  # 4 m7b5
    ((0.5, 0.55), (1.0, 1.0), (1.189, 0.7), (1.414, 0.6)),            # 5 dim cluster
    ((0.5, 0.5), (1.0, 1.0), (1.26, 0.6), (1.5, 0.62), (1.888, 0.45)),  # 6 maj7
)

# Whine voicings (ratios vs the whine fundamental).
_PERFECT_FIFTH = ((1.0, 1.0), (1.5, 0.6))                 # turbo spool: root + 5th
_AUG_TRIAD = ((1.0, 1.0), (1.26, 0.5), (1.587, 0.45))     # gear whine: augmented triad
# Hidden 'o' easter egg: the turbo's perfect fifth gets a root-bass layer and a
# DOMINANT-7th (V7) hung on top; the blow-off then resolves as a B-diminished.
_TURBO_V7 = ((0.5, 0.55), (1.0, 1.0), (1.25, 0.5), (1.5, 0.62), (1.78, 0.42))
_BDIM_HZ = (246.94, 293.66, 349.23)                       # B - D - F  (Bdim triad)

# Exhaust-valve timing (deg of the 720 deg cycle) and blowdown decay.
VALVE_OPEN = 505.0
VALVE_CLOSE = 715.0
BLOWDOWN_TAU = 22.0


def _peaking(f0, Q, gain_db, sr):
    """RBJ peaking-EQ biquad -> (b, a)."""
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * f0 / sr
    alpha = math.sin(w0) / (2 * Q)
    cw = math.cos(w0)
    b = np.array([1 + alpha * A, -2 * cw, 1 - alpha * A])
    a = np.array([1 + alpha / A, -2 * cw, 1 - alpha / A])
    return b / a[0], a / a[0]


def _bandpass(f0, Q, sr):
    """RBJ constant-peak-gain band-pass biquad -> (b, a). Used to pull a tunable
    resonant 'body' tone out of the firing pulses."""
    w0 = 2 * math.pi * f0 / sr
    alpha = math.sin(w0) / (2 * Q)
    cw = math.cos(w0)
    b = np.array([alpha, 0.0, -alpha])
    a = np.array([1 + alpha, -2 * cw, 1 - alpha])
    return b / a[0], a / a[0]


def _select_output():
    """Pick the lowest-latency output device (prefers WASAPI). -> (idx, sr)."""
    if not _HAVE_SD:
        return None, SAMPLE_RATE
    for want in ("WASAPI", "WDM-KS", "DirectSound"):
        for ha in sd.query_hostapis():
            if want in ha["name"]:
                dev = ha["default_output_device"]
                if dev is not None and dev >= 0:
                    try:
                        return dev, int(sd.query_devices(dev)["default_samplerate"])
                    except Exception:
                        pass
    return None, SAMPLE_RATE


def list_output_devices():
    """Selectable output devices -> [(label, index_or_None)].  'Auto' first,
    then the WASAPI (and other) physical outputs, de-duplicated by name."""
    devices = [("Auto (best)", None)]
    if not _HAVE_SD:
        return devices
    seen = set()
    try:
        for want in ("WASAPI", "DirectSound", "MME"):
            for i, d in enumerate(sd.query_devices()):
                if d["max_output_channels"] <= 0:
                    continue
                ha = sd.query_hostapis(d["hostapi"])["name"]
                if want not in ha:
                    continue
                name = d["name"]
                if name in seen:
                    continue
                seen.add(name)
                short = name if len(name) <= 28 else name[:27] + "…"
                devices.append((f"{short}", i))
    except Exception:
        pass
    return devices


class ExhaustWaveguide:
    """A lossy feedback-comb model of one exhaust pipe (digital waveguide).

    ``y[n] = x[n] + s*g*LP(y[n-D])`` — a delay line of D samples fed back with
    gain g through a one-pole low-pass (the pipe's treble loss).  D is the
    round-trip travel time, so the comb resonates at the pipe's standing-wave
    frequencies; s=-1 models the inverting open-end reflection (odd harmonics).

    The block is processed in segments of length <=D so the recursion stays
    vectorised (each segment's delayed samples are already known) — no per-sample
    Python loop, and D may change every block as the gas temperature changes.
    """

    def __init__(self, max_delay=1200):
        self.maxD = max_delay
        self._hist = np.zeros(max_delay, dtype=np.float64)
        self._lp_zi = np.zeros(1)

    def process(self, x, D, g, s, lp_a):
        N = len(x)
        D = int(min(max(D, 4), self.maxD))
        ext = np.empty(self.maxD + N, dtype=np.float64)
        ext[:self.maxD] = self._hist
        base = self.maxD
        sg = s * g
        use_lp = _HAVE_SCIPY and lp_a > 0.0
        if use_lp:
            b = [1.0 - lp_a]
            a = [1.0, -lp_a]
        p = 0
        while p < N:
            seg = min(D, N - p)
            d0 = base + p - D
            delayed = ext[d0:d0 + seg]
            if use_lp:
                delayed, self._lp_zi = lfilter(b, a, delayed, zi=self._lp_zi)
            ext[base + p:base + p + seg] = x[p:p + seg] + sg * delayed
            p += seg
        self._hist = ext[-self.maxD:].copy()
        return ext[base:].copy()


class _Comb:
    """Schroeder feedback comb (delay >= block, so fully vectorised)."""
    def __init__(self, D, g):
        self.D, self.g = D, g
        self._y = np.zeros(D, dtype=np.float64)

    def process(self, x):
        N = len(x)
        delayed = self._y[:N]
        out = x + self.g * delayed
        self._y = np.concatenate([self._y[N:], out])
        return out


class _Allpass:
    """Schroeder all-pass diffuser (delay >= block)."""
    def __init__(self, D, g):
        self.D, self.g = D, g
        self._x = np.zeros(D, dtype=np.float64)
        self._y = np.zeros(D, dtype=np.float64)

    def process(self, x):
        N = len(x)
        dx, dy = self._x[:N], self._y[:N]
        out = -self.g * x + dx + self.g * dy
        self._x = np.concatenate([self._x[N:], x])
        self._y = np.concatenate([self._y[N:], out])
        return out


class Reverb:
    """A small Schroeder reverb (parallel combs + series all-passes) for the
    sense of the car being in a space rather than an anechoic void."""
    def __init__(self, sr, mix=0.16, room=1.0, feedback=0.78):
        sc = sr / 44100.0 * room                 # room < 1 = smaller/shorter space
        self.mix = mix
        self._combs = [_Comb(max(int(d * sc), BLOCK + 1), feedback)
                       for d in (1557, 1617, 1491, 1422)]
        self._aps = [_Allpass(max(int(d * sc), BLOCK + 1), 0.5)
                     for d in (556, 441)]

    def process(self, x):
        acc = np.zeros(len(x), dtype=np.float64)
        for c in self._combs:
            acc += c.process(x)
        acc /= len(self._combs)
        for ap in self._aps:
            acc = ap.process(acc)
        return (1.0 - self.mix) * x + self.mix * acc


class Synthesizer:
    """Streams physics-driven engine audio from a live :class:`Simulator`."""

    def __init__(self, simulator, sample_rate: int = None, device=None):
        self.sim = simulator
        self.volume = 1.0   # mute switch (M): 1.0 on, 0.0 muted
        self.enabled = _HAVE_SD

        if device is not None:
            self._device = device
            try:
                native_sr = int(sd.query_devices(device)["default_samplerate"])
            except Exception:
                native_sr = SAMPLE_RATE
        else:
            self._device, native_sr = _select_output()
        self.sample_rate = sample_rate or native_sr

        cyls = simulator.engine.cylinders
        ncyl = len(cyls)
        self._offsets = np.array([c.cycle_offset_deg for c in cyls], dtype=np.float64)
        self._audio_crank = 0.0

        # Fixed per-cylinder 'personality' (runner-length / build differences):
        # each cylinder fires with a slightly different pitch and loudness, which
        # is what gives a multi-cylinder exhaust its rich, layered waveform.
        rngf = np.random.default_rng(20240517)
        self._cyl_tau = rngf.uniform(-1.0, 1.0, ncyl)   # decay -> pop pitch/brightness
        self._cyl_amp = rngf.uniform(-1.0, 1.0, ncyl)   # loudness

        self._rng = np.random.default_rng()
        self._jit = np.ones(ncyl)
        self._level = 0.05
        self._gain = 1.0
        self.agc_enabled = True   # off (fixed gain) for isolated-pop auditioning

        # Live, user-adjustable mix (the in-app audio console drives these).
        # Akrapovic-style: keep the firing 'bang' + low body strong, keep pipe
        # resonance modest (too much = the 'plastic tube' ring), de-drone.
        self.params = {
            "dry": 1.6,          # combustion bang level
            "res1": 0.10,         # primary pipe resonance (runner)
            "res2": 0.24,         # secondary pipe resonance (full system)
            "crack": 0.12,        # attack snap (explosion punch)
            "attack_deg": 9.0,    # onset softness (deg): bigger = blunter attack
            "body": 1.60,         # thickness / low-end of each firing (浑厚)
            "drive": 0.40,        # saturation -> tight, solid 'power chord' grip
            "firing_pitch": 90.0,  # Hz, pitch of that firing body
            "pulse_tau": 22.0,    # blowdown decay (deg) -> firing timbre/brightness
            "turbulence": 0.2,    # gas-rush noise on each firing
            "src_reverb": 0.48,   # reverb on the explosion itself (pre-pipe)
            "reverb": 0.4,       # spatial reverb mix (post, room)
            "intake": 0.22,       # induction roar level (was a bit windy)
            "eq_low": 0.0,        # dB
            "eq_mid": 0.0,        # dB
            "eq_high": 0.0,       # dB
            "presence": 0.0,      # dB — guitar-amp 'presence' (upper-mid bite ~3 kHz)
            "cyl_spread": 0.5,    # how much each cylinder's pitch/level differs
            "master": 0.6,        # master output volume
            "spatial_x": 0.5,     # stereo pan: 0 left .. 1 right
            "spatial_y": 0.6,     # distance: 0 far (dark/quiet) .. 1 near
            "super_vol": 0.6,     # mechanical supercharger (roots/centrifugal) whine
            "turbo_vol": 0.45,    # turbo spool whistle + BOV (was 0.6 -> 75%)
            "gearbox_vol": 0.375, # straight-cut gearbox whine (was 0.5 -> 75%)
            "wall_thickness": 0.3,  # pipe-wall thickness: higher = duller, less 'trumpet'
            "shear": 0.10,        # tail-pipe air-shear roar at the exit (mass-flow)
            "spool_reverb": 0.15, # dedicated reverb on the induction/spool sounds
            "hybrid_vol": 0.5,    # electric-motor / e-turbo whine level (hybrids)
            "gearbox_reverb": 0.12,  # dedicated reverb on the straight-cut whine
            "fire_weight": 0.5,   # fire-tone pad X: thin/bright .. thick/fat body
            "fire_grit": 0.3,     # fire-tone pad Y: smooth .. coarse/raw saturation
            "pops": 0.6,          # overrun pop level (power-chord bangs on decel)
            "pop_muff": 0.4,      # how muffled the pops are (0 sharp .. 1 dull)
            "pops_reverb": 0.22,  # dedicated reverb on the overrun pops
        }

        self._build_audio()

        self.cabin = False        # interior (in-cabin) muffling effect
        # straight-cut gearbox whine — on by default for cars that actually have
        # a straight-cut (dog) box (race cars), off otherwise.
        self.straight_cut = simulator.engine.straight_cut
        self.gpf = simulator.engine.has_gpf   # particulate filter (muffles a lot)
        self.cat = simulator.engine.has_cat   # catalytic converter (mild muffle)
        # bent stainless road exhaust + cat: absorbs the harsh fire/bang highs so
        # it doesn't sound like a raw straight pipe.  On by default for road cars
        # (those with a cat), off for open-exhaust race cars.
        self.road_pipe = simulator.engine.has_cat
        # lift-off sound: False = clean BOV 'pshhh', True = compressor-surge
        # 'stututu' — defaults from the engine (some cars have no dump valve).
        self.flutter = simulator.engine.bov_flutter
        self.last_level = 0.0     # RMS of last rendered block (exhaust loudness meter)
        self.last_wave = np.zeros(64)   # decimated waveform for the HUD flow scope
        self._cold = 1.0          # cold-start factor (1 cold .. 0 warmed up)
        self._whine_phase = 0.0   # blower / turbo whistle oscillator phase
        self._gearbox_phase = 0.0 # gearbox whine oscillator phase
        self._flutter_phase = 0.0 # compressor-surge flutter oscillator phase
        self._motor_phase = 0.0   # hybrid electric-motor whine oscillator phase
        self._ecomp_phase = 0.0   # e-compressor (e-turbo) whine oscillator phase
        self._was_on_gas = 0.0    # recent on-throttle memory (fuels the crackle)
        self.pops_on = False      # overrun pops on/off (default off)
        self.time_scale = 1.0     # 1.0 normal .. <1 slow motion
        self._pop_age = 10 ** 9   # samples since the current pop started
        self._pop_len = 1         # length of the current pop (samples)
        self._pop_f0 = 180.0      # pop base pitch (glides down)
        self._pop_amp = 0.0       # pop strength
        self.o_chord = False      # hidden 'o' easter egg: turbo V7 + Bdim blow-off
        self._bdim_phase = 0.0    # Bdim blow-off oscillator phase
        self.fire_chord = 0       # firing-body chord voicing index (hidden keys 1-6)
        self._prev_throttle = 0.0 # for blow-off-valve detection
        self._bov_env = 0.0       # blow-off-valve 'pshhh' envelope
        self._lock = threading.Lock()
        self._stream = None
        self.latency_ms = 0.0
        self.mode = "off"
        self.prefer_exclusive = os.environ.get("ENGINE_SIM_EXCLUSIVE") == "1"

    # --------------------------------------------------------- rate-dependent
    def _build_audio(self):
        eng = self.sim.engine
        sr = self.sample_rate
        # how many separate exhaust channels, and which channel each cylinder is on
        self._nchan = max(1, int(eng.exhaust_channels))
        if self._nchan == 1:
            self._channel_of = [0] * eng.num_cylinders
        else:
            self._channel_of = [0 if c.bank_angle_deg < 0 else 1 for c in eng.cylinders]
        # Unequal-length headers: delay one bank's pulses a few crank-degrees so
        # the even firing arrives UNEVENLY -> the Subaru boxer rumble.
        hu = eng.header_unequal_deg
        self._header_offset = [hu if c.bank_angle_deg < 0 else 0.0
                               for c in eng.cylinders]
        # --- (Step 2) per-cylinder header-runner DELAY LINES --------------------
        # Each cylinder sits a different distance from the collector, so its pulse
        # reaches the merge point at a slightly different time.  Firing order x
        # runner-length spread = the comb interference that makes an inline-4, a
        # cross-plane V8 and a flat-plane V10 sound fundamentally different (not
        # just the same pulse at another pitch).  Runner length is derived from the
        # header primary length with a per-cylinder gradient along each bank, so it
        # works for every preset without hand-tuning 47 of them.
        prim = max(eng.exhaust_primary_m, 0.15)
        c_gas = 500.0                                   # hot-gas speed (m/s), nominal
        seen, posn = {}, []
        for c in eng.cylinders:
            ch = 0 if (self._nchan == 1 or c.bank_angle_deg < 0) else 1
            posn.append(seen.get(ch, 0)); seen[ch] = seen.get(ch, 0) + 1
        self._runner_samp = []
        for j, c in enumerate(eng.cylinders):
            ch = self._channel_of[j]
            frac = posn[j] / max(seen[ch] - 1, 1)        # 0 (near) .. 1 (far)
            length = prim * (0.55 + 0.9 * frac)          # spread of runner lengths
            self._runner_samp.append(int(round(length / c_gas * sr)))
        self._runner_hist = [np.zeros(max(d, 0)) for d in self._runner_samp]
        # TWO waveguides per channel: a short primary runner (high resonance) and
        # the full system length (low resonance) -> several pipe resonances at
        # different frequencies, like a real exhaust.
        self._wg = [(ExhaustWaveguide(), ExhaustWaveguide()) for _ in range(self._nchan)]
        self._reverb = Reverb(sr)
        # A dedicated reverb for the forced-induction sounds (spool whistle /
        # blower whine / BOV) so the user can wash them with their own space.
        self._ind_reverb = Reverb(sr, room=0.7, feedback=0.7)
        # ...and a separate one just for the straight-cut gearbox whine.
        self._gear_reverb = Reverb(sr, room=0.6, feedback=0.66)
        # ...and a big roomy one for the overrun pops/bangs (they echo off walls).
        self._pops_reverb = Reverb(sr, room=0.85, feedback=0.76)
        self._pop_lp_zi = np.zeros(2)
        # A short reverb on the explosion ITSELF, before the pipe — so the
        # waveguide resonates an already-reverberant bang (chamber/port acoustics).
        self._src_verb = [Reverb(sr, room=0.4, feedback=0.55)
                          for _ in range(self._nchan)]

        # fixed post filters (state carried across blocks)
        if _HAVE_SCIPY:
            self._hp = butter(2, 55.0 / (sr / 2), btype="high")
            self._hp_zi = np.zeros(max(len(self._hp[0]), len(self._hp[1])) - 1)
            # post low-pass is recomputed every block from the exhaust valve
            self._lp_zi = np.zeros(2)
            self._lowboost_zi = np.zeros(2)     # low-end boost when valve is shut
            self._spatial_zi = np.zeros(2)      # spatial distance darkening
            self._helm_zi = np.zeros(2)
            # intake / induction path: cool-air airbox resonance + roll-off
            self._intake_bp = _peaking(150.0, 1.1, 7.0, sr)
            self._intake_lp = butter(2, 1300.0 / (sr / 2), btype="low")
            self._intake_bp_zi = np.zeros(2)
            self._intake_lp_zi = np.zeros(max(len(self._intake_lp[0]),
                                               len(self._intake_lp[1])) - 1)
            # firing 'body' = a power/colour chord rung as high-Q resonators.
            # Sized to the widest voicing (5) so every hidden chord fits.
            self._chord_zi = [np.zeros(2) for _ in range(5)]
            self._eq_lo_zi = np.zeros(2)
            self._eq_mid_zi = np.zeros(2)
            self._eq_hi_zi = np.zeros(2)
            self._eq_pres_zi = np.zeros(2)
            # cabin effect: muffle the highs (hearing it from inside the car)
            self._cabin_lp = butter(2, 2400.0 / (sr / 2), btype="low")
            self._cabin_zi = np.zeros(max(len(self._cabin_lp[0]),
                                          len(self._cabin_lp[1])) - 1)
            # band-limit the combustion 'crack': raw np.diff has harsh energy all
            # the way to Nyquist (a piercing digital click).  Rolling it off above
            # ~7 kHz keeps the bright, solid attack snap without the thin shriek.
            self._crack_lp = butter(2, min(7000.0, sr * 0.46) / (sr / 2), btype="low")
            self._crack_lp_zi = np.zeros(2)
            # ...and high-pass it so the 'crack' is a bright mechanical TICK that
            # sits clearly apart from the low combustion thump (dry) — otherwise
            # dry / crack / body all blur into one undifferentiated 'ignition'.
            self._crack_hp = butter(2, 700.0 / (sr / 2), btype="high")
            self._crack_hp_zi = np.zeros(2)
            # pipe-wall-thickness low-passes (dull the brassy 'trumpet' edge)
            self._wall_out_zi = np.zeros(2)   # turbo / supercharger whine
            self._wall_gw_zi = np.zeros(2)    # gearbox whine
            self._wall_sig_zi = np.zeros(2)   # the main exhaust note (de-honk)
            self._wall_low_zi = np.zeros(2)   # ...and its low-shelf body boost
            self._fire_low_zi = np.zeros(2)   # fire-tone pad 'weight' low shelf
            # bent-pipe road exhaust: a low-pass + an upper-mid scoop that the
            # bends & cat impose, absorbing the raw straight-pipe high frequencies
            self._road_lp = butter(2, min(5200.0, sr * 0.45) / (sr / 2), btype="low")
            self._road_sh = _peaking(3400.0, 0.7, -5.5, sr)
            self._road_lp_zi = np.zeros(2)
            self._road_sh_zi = np.zeros(2)
            # tailpipe air-shear: high-pressure gas tearing into still air at the
            # exit — a broadband hiss/roar swelling with exhaust mass-flow, the
            # OUTERMOST layer you hear at the back of the car.
            self._shear_bp = _bandpass(2600.0, 0.6, sr)
            self._shear_hp = butter(2, 900.0 / (sr / 2), btype="high")
            self._shear_bp_zi = np.zeros(2)
            self._shear_hp_zi = np.zeros(2)
            # (Step 3) cylinder-head / exhaust-port cavity: a gentle low-pass that
            # 'rounds' the raw pulse so it reads as metal, not a digital click.
            self._head_lp = butter(2, min(7200.0, sr * 0.45) / (sr / 2), btype="low")
            self._head_lp_zi = np.zeros(2)
            # (Step 4) pipe-wall metal resonance formants — a thin, small-bore pipe
            # rings higher and sharper; a thick, big-bore pipe lower and tighter.
            r = max(eng.exhaust_radius_m, 0.012)
            self._wall_f1 = min(max(2300.0 * (0.024 / r), 1400.0), 3600.0)
            self._wall_f2 = min(self._wall_f1 * 1.85, sr * 0.42)
            self._wallpk1_zi = np.zeros(2)
            self._wallpk2_zi = np.zeros(2)
        self._audio_crank = 0.0

    def _rebuild_for_rate(self, sr: int):
        if sr == self.sample_rate and getattr(self, "_wg", None):
            return
        self.sample_rate = sr
        self._build_audio()

    # ------------------------------------------------- physical resonance setup
    def _resonance_params(self):
        """Everything tuning the pipe resonance, DERIVED FROM PHYSICS."""
        sim, eng, sr = self.sim, self.sim.engine, self.sample_rate
        c = sim.exhaust_sound_speed()                  # hot-gas speed of sound
        # Round-trip travel = 2L (down and back); the inverting open-end (s=-1)
        # then makes the comb a quarter-wave resonator at odd multiples of
        # fs/(2D) = c/(4*L_eff) -- exactly the open-closed pipe fundamental.
        rad = eng.exhaust_radius_m
        l_primary = eng.exhaust_primary_m + 0.61 * rad
        l_total = eng.exhaust_total_m + 0.61 * rad
        D1 = round(2.0 * l_primary * sr / c)           # high resonance (runner)
        D2 = round(2.0 * l_total * sr / c)             # low resonance (full system)
        # feedback gain from radiation + wall loss (more open -> rings longer,
        # sharper/higher-Q teeth = metallic, not a damped 'plastic' tube)
        g = min(0.84 + 0.15 * eng.exhaust_openness, 0.992)

        # VARIABLE EXHAUST VALVE: it opens with rpm + throttle.  Closed (idle /
        # light load) the gas takes the long muffled path -> dark, bassy, lumpy;
        # wide open (high rpm / hard throttle) it's a short straight pipe ->
        # bright, screaming.  This rpm-dependent brightness is the whole reason a
        # low idle sounds nothing like a redline pull.
        rpm_frac = min(self.sim.rpm / max(eng.redline_rpm, 1.0), 1.0)
        # mostly rpm-driven (throttle just nudges it open a bit)
        drive = min(rpm_frac + 0.30 * min(max(self.sim.throttle, 0.0), 1.0), 1.0)
        valve = min(max((drive - 0.28) / 0.45, 0.0), 1.0)
        self._valve = valve
        self._post_fc = 1600.0 + 9600.0 * valve     # muffled 1.6 kHz .. bright 11 kHz

        # in-loop treble damping, also scaled shut by the valve
        fc = (2000.0 + 7000.0 * eng.exhaust_openness) * (0.4 + 0.6 * valve)
        # a rotary 'braps' brighter and raspier than a piston engine
        if eng.is_rotary:
            self._post_fc *= 1.35
            fc *= 1.4
        # 2-valve heads breathe worse up top -> a touch darker than 4-valve
        if eng.valves_per_cyl <= 2:
            self._post_fc *= 0.82
        # exhaust after-treatment: a cat muffles a little, a GPF a lot
        if self.cat:
            self._post_fc *= 0.85
        if self.gpf:
            self._post_fc *= 0.6
            fc *= 0.75
        lp_a = math.exp(-2 * math.pi * fc / sr)
        # Helmholtz muffler/chamber resonance
        A, V = eng.muffler_neck_area_m2, eng.muffler_volume_m3
        r_neck = math.sqrt(A / math.pi)
        l_h = eng.muffler_neck_len_m + 1.7 * r_neck
        f_helm = (c / (2 * math.pi)) * math.sqrt(A / (V * l_h))
        f_helm = min(max(f_helm, 40.0), 400.0)
        return D1, D2, g, lp_a, f_helm

    # ------------------------------------------------------- synthesis core
    def _render_block(self, frames: int) -> np.ndarray:
        sim = self.sim
        omega = sim.omega
        # time_scale < 1 = slow motion (the whole engine note slows + drops)
        dps = math.degrees(omega) / self.sample_rate * self.time_scale
        # (Step 5) cold-start: the engine warms over ~8 s of running (note brightens
        # as it warms); it slowly re-cools when shut off.  Drives the head-LP above.
        dt_blk = frames / self.sample_rate
        if sim.ignition_on and sim.rpm > 300.0:
            self._cold = max(0.0, self._cold - dt_blk / 8.0)
        else:
            self._cold = min(1.0, self._cold + dt_blk / 40.0)

        D1, D2, g, lp_a, f_helm = self._resonance_params()
        s = -1.0    # inverting open-end reflection -> odd-harmonic quarter wave

        # --- per-channel excitation, sampled from the physics ---------------
        chans = [np.zeros(frames, dtype=np.float64) for _ in range(self._nchan)]
        fizz_chans = [np.zeros(frames, dtype=np.float64) for _ in range(self._nchan)]
        if dps > 1e-12:
            idx = np.arange(frames)
            crank = self._audio_crank + dps * idx
            p_open = sim.blowdown_pressure() - 1.05 * P_ATM
            strength = math.copysign(math.sqrt(abs(p_open)), p_open) / math.sqrt(6 * P_ATM)
            self._jit += (1.0 + 0.12 * (self._rng.random(len(self._jit)) - 0.5)
                          - self._jit) * 0.25

            # Cylinder spread ~3x stronger than before, and bigger still at low
            # rpm (valve shut), where the spaced pops make each cylinder's own
            # character clearly audible -> coarse, grainy low-rpm lumpiness.
            spread = self.params["cyl_spread"] * (1.0 + 1.4 * (1.0 - self._valve))
            base_tau = self.params["pulse_tau"]
            for j, off in enumerate(self._offsets):
                # this cylinder's own decay (pitch) and loudness
                tau_j = base_tau * max(1.0 + 0.95 * spread * self._cyl_tau[j], 0.35)
                amp_j = self._jit[j] * max(1.0 + 0.55 * spread * self._cyl_amp[j], 0.1)
                phi = np.mod(crank + off + self._header_offset[j], 720.0)
                d = phi - VALVE_OPEN
                inwin = (phi >= VALVE_OPEN) & (phi <= VALVE_CLOSE)
                ramp = np.clip(d / self.params["attack_deg"], 0.0, 1.0)
                ramp = 0.5 - 0.5 * np.cos(ramp * math.pi)   # blunt (soft) attack
                dd = np.clip(d, 0.0, None)
                # Two-stage exhaust pulse (real valve behaviour) instead of one
                # flat blat: (1) BLOWDOWN — the valve cracks open and the still-high
                # cylinder pressure dumps in a steep, short, HF-rich burst (the
                # sharp leading edge); (2) DISPLACEMENT — the rising piston then
                # pushes the rest of the gas out as a broader, lower, later hump
                # (the body / low end).  Decays are in CRANK DEGREES, so the whole
                # event is fixed-angle and naturally sounds sharper/brighter as rpm
                # climbs and fatter when it drops — the load/rev character is real.
                tau_blow = max(0.34 * tau_j, 5.0)
                blow = ramp * np.exp(-dd / tau_blow)
                tau_disp = tau_j * 1.5
                disp = ramp * (1.0 - np.exp(-dd / (0.5 * tau_j))) * np.exp(-dd / tau_disp)
                close = np.clip((VALVE_CLOSE - phi) / 18.0, 0.0, 1.0)
                pulse = np.where(inwin, (blow + 0.7 * disp) * close * amp_j, 0.0)
                # delay this cylinder's pulse down its own runner before it merges
                # at the collector (carries the tail across blocks) -> interference.
                d_samp = self._runner_samp[j]
                if d_samp > 0:
                    comb = np.concatenate([self._runner_hist[j], pulse])
                    pulse = comb[:frames]
                    self._runner_hist[j] = comb[frames:]
                chans[self._channel_of[j]] += pulse

            # Separate the clean 'bang' (tonal pulse) from the 'fizz' (gas-rush
            # noise gated by the pulse), so each gets its OWN mixer slider.
            for ci in range(self._nchan):
                e = chans[ci] * strength
                noise = self._rng.standard_normal(frames)
                chans[ci] = 0.55 * e                      # clean bang -> pipe + dry
                fizz_chans[ci] = e * noise + 0.03 * noise  # fizz, scaled later
        self._audio_crank = (self._audio_crank + dps * frames) % 720.0

        # --- mix the DRY combustion pulses with the WET pipe resonance -------
        # The pipe rings ON TOP of the bangs, it does not replace them: dry =
        # the explosion you hear at the valve, wet = the pipe colouring it,
        # crack = the sharp leading edge of each pulse (the combustion snap).
        P = self.params
        dry = np.zeros(frames, dtype=np.float64)
        wet = np.zeros(frames, dtype=np.float64)
        for ci in range(self._nchan):
            # Reverb the explosion at the source, then that reverberant bang is
            # what both the dry mix and the PIPE (waveguide) downstream receive.
            self._src_verb[ci].mix = P["src_reverb"]
            src = self._src_verb[ci].process(chans[ci])
            dry += src
            wg_primary, wg_total = self._wg[ci]
            # two pipe resonances at different frequencies (runner + full system)
            wet += (P["res1"] * wg_primary.process(src, D1, g, s, lp_a)
                    + P["res2"] * wg_total.process(src, D2, g * 0.96, s, lp_a))
        inv = 1.0 / self._nchan
        dry *= inv
        wet *= inv
        # The three firing voices must be TIMBRALLY distinct, or they all read as
        # one 'ignition' blob:
        #   dry   = the low broadband combustion THUMP (the punch)
        #   crack = a bright mechanical TICK (snap), high-passed off the thump
        #   body  = a pitched CHORD that RINGS (high-Q resonators), the musical note
        snap = np.diff(dry, prepend=dry[:1])             # raw broadband edge
        crack = snap
        if _HAVE_SCIPY:                                   # bright tick, not a thump
            crack, self._crack_lp_zi = lfilter(
                self._crack_lp[0], self._crack_lp[1], crack, zi=self._crack_lp_zi)
            crack, self._crack_hp_zi = lfilter(
                self._crack_hp[0], self._crack_hp[1], crack, zi=self._crack_hp_zi)

        # --- firing 'body' = a chord RUNG OUT by the combustion snap ----------
        # Each chord voice is a high-Q resonator EXCITED by the sharp snap, so it
        # actually rings at its pitch (a real, audible chord) instead of being a
        # weak band-pass of the thump.  Now switching voicings (keys 1-6) clearly
        # changes the timbre, and the chord is its own voice — not 'more ignition'.
        body = np.zeros(frames, dtype=np.float64)
        if _HAVE_SCIPY and P["body"] > 1e-3:
            root = min(max(P["firing_pitch"], 28.0), 600.0)
            nyq = self.sample_rate * 0.45
            chord = _FIRE_CHORDS[self.fire_chord % len(_FIRE_CHORDS)]
            for k, (ratio, lvl) in enumerate(chord):
                f = min(root * ratio, nyq)
                bb, ab = _bandpass(f, 11.0, self.sample_rate)    # resonant -> rings
                tone, self._chord_zi[k] = lfilter(bb, ab, snap, zi=self._chord_zi[k])
                body += lvl * tone
            body *= 1.7                          # the resonators ring quietly; lift

        # Assemble the bang (pulse + snap + power chord) and SATURATE it for the
        # tight, solid 'metal power chord' grip (the fix for the floaty feel).
        # The 2-D fire-tone pad morphs the STYLE: weight (X) = fatter body + low
        # shelf, grit (Y) = more saturation and attack snap.
        fw, fg = P["fire_weight"], P["fire_grit"]
        bang = (P["dry"] * dry + P["crack"] * (1.0 + 1.3 * fg) * crack
                + (1.6 * P["body"]) * (1.0 + 1.4 * fw) * body)
        drive = P["drive"] + 1.6 * fg
        if drive > 1e-3:
            bang = np.tanh(bang * (1.0 + 7.0 * drive))
        if _HAVE_SCIPY and fw > 0.02:                    # low-shelf 'weight'
            b, a = _peaking(110.0, 0.6, 10.0 * fw, self.sample_rate)
            bang, self._fire_low_zi = lfilter(b, a, bang, zi=self._fire_low_zi)

        # separated fizz (own slider) + clean wet pipe resonance on top
        fizz = np.zeros(frames, dtype=np.float64)
        for ci in range(self._nchan):
            fizz += fizz_chans[ci]
        sig = bang + wet + P["turbulence"] * (fizz * inv)

        # ================== EXHAUST PATH, IN PHYSICAL ORDER ==================
        # Head -> tail, the way the gas actually travels (so the chain matches a
        # real car):
        #   1. combustion bang + exhaust-valve impact   -> dry + crack (above)
        #   2. head / port cavity reverb                -> src_verb     (above)
        #   3. header (primary runner) resonance + wall -> wet + wall    (above/below)
        #   4. CATALYTIC CONVERTER  (honeycomb absorbs the highs) -- BEFORE muffler
        #   5. resonator  (Helmholtz NOTCH, kills the boom drone)
        #   6. main muffler  (expansion low-pass + low-end body)
        #   7. tail-pipe wall thickness (de-honk, see wall_thickness below)
        #   8. tail-pipe air-shear at the exit (broadband roar into open air)
        #   9. room / environment reverb  (the space — added last, below)

        # --- (3a) cylinder-head / exhaust-port cavity low-pass: round the raw
        # pulse so it reads as a metal port, not an electronic click.  A touch
        # duller while the engine is still cold.
        if _HAVE_SCIPY:
            if self._cold > 0.02:
                hc = min(7200.0 * (1.0 - 0.22 * self._cold), self.sample_rate * 0.45)
                bhd, ahd = butter(2, hc / (self.sample_rate / 2), btype="low")
                sig, self._head_lp_zi = lfilter(bhd, ahd, sig, zi=self._head_lp_zi)
            else:
                sig, self._head_lp_zi = lfilter(self._head_lp[0], self._head_lp[1],
                                                sig, zi=self._head_lp_zi)

        # --- (4) catalytic converter: the ceramic honeycomb soaks up the raw
        # straight-pipe top end FIRST, upstream of the muffler — a stock car with
        # a cat can't sound like an open header no matter what the muffler does.
        if self.road_pipe and _HAVE_SCIPY:
            sig, self._road_lp_zi = lfilter(self._road_lp[0], self._road_lp[1],
                                            sig, zi=self._road_lp_zi)
            sig, self._road_sh_zi = lfilter(self._road_sh[0], self._road_sh[1],
                                            sig, zi=self._road_sh_zi)

        # --- (5+6) resonator + muffler: DC-block, de-drone notch, valve roll-off
        if _HAVE_SCIPY:
            sig, self._hp_zi = lfilter(self._hp[0], self._hp[1], sig, zi=self._hp_zi)
            # (5) resonator: Helmholtz used as a NOTCH (Akrapovic-style: remove the
            # drone boom, do not add yet another resonance).
            bH, aH = _peaking(f_helm, 1.2, -4.0, self.sample_rate)
            sig, self._helm_zi = lfilter(bH, aH, sig, zi=self._helm_zi)
            # (6) muffler: variable-valve expansion low-pass — muffled at idle,
            # wide open at redline.
            sr = self.sample_rate
            cutoff = min(self._post_fc, sr * 0.45)
            blp, alp = butter(2, cutoff / (sr / 2), btype="low")
            sig, self._lp_zi = lfilter(blp, alp, sig, zi=self._lp_zi)
            # ...and its expansion-chamber low-end body when the valve is shut.
            if self._valve < 0.75:
                bL, aL = _peaking(110.0, 0.6, (1.0 - self._valve) * 7.0, sr)
                sig, self._lowboost_zi = lfilter(bL, aL, sig, zi=self._lowboost_zi)
        else:
            sig = np.diff(sig, prepend=sig[:1])

        # --- intake / induction roar (the OTHER half a real car you hear) ---
        # Broadband 'sucking' noise through the airbox resonance, swelling with
        # throttle and rpm.  A separate path from the exhaust, cool-air tuned.
        if _HAVE_SCIPY and dps > 1e-12:
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            intake_gain = P["intake"] * sim.throttle * (0.25 + 0.75 * rpm_frac)
            if intake_gain > 1e-4:
                n = self._rng.standard_normal(frames)
                n, self._intake_bp_zi = lfilter(self._intake_bp[0], self._intake_bp[1],
                                                n, zi=self._intake_bp_zi)
                n, self._intake_lp_zi = lfilter(self._intake_lp[0], self._intake_lp[1],
                                                n, zi=self._intake_lp_zi)
                sig = sig + intake_gain * n

        # --- forced induction (blower whine / turbo whistle / BOV) + gearbox -
        if dps > 1e-12:
            ind, gw = self._induction_audio(frames)
            if _HAVE_SCIPY and P["spool_reverb"] > 1e-3:
                self._ind_reverb.mix = P["spool_reverb"]
                ind = self._ind_reverb.process(ind)
            if _HAVE_SCIPY and P["gearbox_reverb"] > 1e-3:
                self._gear_reverb.mix = P["gearbox_reverb"]
                gw = self._gear_reverb.process(gw)
            sig = sig + ind + gw
            sig = sig + self._overrun_pops(frames)   # decel pops & bangs

        # --- (7) tail-pipe wall thickness: kill the 'small-trumpet' shriek
        # WITHOUT losing low end.  The brass honk lives in a ~1.8 kHz formant —
        # scoop THAT band and add a touch of low-shelf body (thicker, not thinner).
        wt = P["wall_thickness"]
        if _HAVE_SCIPY and wt > 1e-3:
            b, a = _peaking(1850.0, 1.1, -16.0 * wt, self.sample_rate)   # de-honk
            sig, self._wall_sig_zi = lfilter(b, a, sig, zi=self._wall_sig_zi)
            b2, a2 = _peaking(150.0, 0.7, 4.0 * wt, self.sample_rate)    # add body
            sig, self._wall_low_zi = lfilter(b2, a2, sig, zi=self._wall_low_zi)
        # (Step 4) stainless-wall resonance formants: two narrow peaks give the
        # note its METAL ring.  A thicker wall (wt up) drops the peaks lower and
        # tightens them (fat & solid); a thin wall keeps them high & open (bright,
        # 'tinny').  Always on — it's the pipe material itself, not an effect.
        if _HAVE_SCIPY:
            f1 = self._wall_f1 * (1.0 - 0.18 * wt)
            f2 = self._wall_f2 * (1.0 - 0.22 * wt)
            bp1, ap1 = _peaking(f1, 3.4, 3.0 - 1.2 * wt, self.sample_rate)
            sig, self._wallpk1_zi = lfilter(bp1, ap1, sig, zi=self._wallpk1_zi)
            bp2, ap2 = _peaking(f2, 4.2, 2.2 - 1.4 * wt, self.sample_rate)
            sig, self._wallpk2_zi = lfilter(bp2, ap2, sig, zi=self._wallpk2_zi)

        # --- (8) tail-pipe air-shear: the gas tearing out of the tip into still
        # air — a broadband roar/hiss swelling with exhaust mass-flow (rpm x load).
        # This is the outermost 'whoosh' you hear standing behind the car.
        if _HAVE_SCIPY and dps > 1e-12:
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            flow = rpm_frac * (0.35 + 0.65 * sim.throttle)
            shear_gain = P.get("shear", 0.10) * flow
            if shear_gain > 1e-4:
                ns_ = self._rng.standard_normal(frames)
                ns_, self._shear_bp_zi = lfilter(self._shear_bp[0], self._shear_bp[1],
                                                 ns_, zi=self._shear_bp_zi)
                ns_, self._shear_hp_zi = lfilter(self._shear_hp[0], self._shear_hp[1],
                                                 ns_, zi=self._shear_hp_zi)
                if self.road_pipe:                 # a cat car's tip is breathier
                    shear_gain *= 0.7
                sig = sig + shear_gain * ns_

        # --- 3-band EQ (low / mid / high knobs) -----------------------------
        if _HAVE_SCIPY:
            if abs(P["eq_low"]) > 0.1:
                b, a = _peaking(120.0, 0.7, P["eq_low"], self.sample_rate)
                sig, self._eq_lo_zi = lfilter(b, a, sig, zi=self._eq_lo_zi)
            if abs(P["eq_mid"]) > 0.1:
                b, a = _peaking(850.0, 0.8, P["eq_mid"], self.sample_rate)
                sig, self._eq_mid_zi = lfilter(b, a, sig, zi=self._eq_mid_zi)
            if abs(P["eq_high"]) > 0.1:
                b, a = _peaking(4500.0, 0.7, P["eq_high"], self.sample_rate)
                sig, self._eq_hi_zi = lfilter(b, a, sig, zi=self._eq_hi_zi)
            if abs(P["presence"]) > 0.1:        # amp 'presence': broad upper-mid lift
                b, a = _peaking(3000.0, 0.6, P["presence"], self.sample_rate)
                sig, self._eq_pres_zi = lfilter(b, a, sig, zi=self._eq_pres_zi)

        # --- cabin effect: muffle the highs, as if heard from inside the car -
        if self.cabin and _HAVE_SCIPY:
            sig, self._cabin_zi = lfilter(self._cabin_lp[0], self._cabin_lp[1],
                                          sig, zi=self._cabin_zi)
            sig *= 1.4   # make up the level lost to the low-pass

        # --- spatial / room reverb — the LAST stage, the space the tailpipe
        # exhausts into (after the bent pipe + cat, exactly like real life).  A
        # bent-stainless road car is heard bouncing off tarmac & bodywork, so it
        # carries MORE room than a dry open-header race car; the cabin adds more.
        self._reverb.mix = (P["reverb"] + (0.10 if self.cabin else 0.0)
                            + (0.12 if self.road_pipe else 0.0))
        sig = self._reverb.process(sig)

        # --- auto-level (or fixed gain) + soft saturation + master volume ----
        if self.agc_enabled:
            rms = float(np.sqrt(np.mean(sig * sig))) + 1e-9
            self._level += (rms - self._level) * 0.04
            gain = min(0.22 / (self._level + 1e-6), 6.0)
            self._gain += (gain - self._gain) * 0.15
            sig *= self._gain
        else:
            sig *= 3.5
        # --- spatial distance: far away = darker + quieter (the pad's Y axis) -
        d = 1.0 - self.params["spatial_y"]      # 0 near .. 1 far
        if _HAVE_SCIPY and d > 0.02:
            sr = self.sample_rate
            cut = min(max(14000.0 - 11500.0 * d, 600.0), sr * 0.45)
            b, a = butter(2, cut / (sr / 2), btype="low")
            sig, self._spatial_zi = lfilter(b, a, sig, zi=self._spatial_zi)
        sig = sig * (1.0 / (1.0 + 1.7 * d))

        out = np.tanh(sig * (self.volume * self.params["master"] * 1.5)).astype(np.float32)
        # exhaust loudness meter (RMS of the final output) for the HUD readout
        self.last_level = float(np.sqrt(np.mean(out * out))) if frames else 0.0
        # keep a decimated copy of the waveform for the HUD exhaust-flow scope
        if frames:
            step = max(1, frames // 64)
            self.last_wave = out[::step][:64].astype(np.float64).copy()
        return out

    # ------------------------------------------------------------ callback
    # --------------------------------------------------- forced induction
    def _whine(self, freq, frames, harmonics, phase_attr="_whine_phase"):
        """A continuous tonal oscillator (sum of harmonics) at ``freq`` Hz."""
        sr = self.sample_rate
        ph0 = getattr(self, phase_attr)
        inc = 2.0 * math.pi * freq / sr
        ph = ph0 + inc * np.arange(frames)
        sig = np.zeros(frames, dtype=np.float64)
        for h, a in harmonics:
            sig += a * np.sin(h * ph)
        setattr(self, phase_attr, (ph0 + inc * frames) % (2.0 * math.pi))
        return sig

    def _induction_audio(self, frames):
        """Supercharger whine / turbo whistle + BOV, and straight-cut gearbox
        whine — the forced-induction and transmission character on top of the
        engine note."""
        sim, eng, sr = self.sim, self.sim.engine, self.sample_rate
        P = self.params
        rpm = sim.rpm
        out = np.zeros(frames, dtype=np.float64)   # induction (spool/whine/BOV)
        gw = np.zeros(frames, dtype=np.float64)    # straight-cut gearbox whine
        sv = P["super_vol"]      # mechanical supercharger whine
        tv = P["turbo_vol"]      # turbo spool whistle + BOV
        bfrac = min(sim.boost / max(eng.boost_bar, 0.05), 1.0) if eng.boost_bar else 0.0

        if sv > 1e-3 and eng.induction in ("roots", "centrifugal") and bfrac > 0.01:
            ratio = (eng.blower_ratio if eng.blower_ratio > 0 else 9.0)
            if eng.induction == "centrifugal":
                ratio *= 2.5                                  # higher-pitched
                harm = [(1, 1.0), (2, 0.25)]
            else:
                harm = [(1, 1.0), (2, 0.5), (3, 0.28)]        # rich roots whine
            f = (rpm / 60.0) * ratio
            if 20.0 < f < sr * 0.45:
                out += (sv * bfrac * 0.5) * self._whine(f, frames, harm)

        if tv > 1e-3 and eng.induction == "turbo":
            if bfrac > 0.02:
                f = 900.0 + bfrac * 5200.0                    # whistle rises with boost
                amp = tv * bfrac * 0.30
                # perfect fifth (root + 5th); the hidden 'o' mode adds a root
                # bass layer + a dominant-7th (V7) hung on top.
                voicing = _TURBO_V7 if self.o_chord else _PERFECT_FIFTH
                out += amp * self._whine(min(f, sr * 0.45), frames, list(voicing))
                out += (amp * 0.5) * self._rng.standard_normal(frames)  # air
            # Throttle snaps shut while on boost -> the lift-off sound.  Which
            # one you hear depends on where the pressurised air goes:
            #   * an atmospheric dump valve vents it in one clean 'PSHHH';
            #   * with no (or a shut) valve the air backs up and pulses BACKWARD
            #     through the compressor wheel again and again -> compressor
            #     surge, the rapid 'stu-tu-tu-tu' flutter.
            if (self._prev_throttle - sim.throttle) > 0.25 and sim.boost > 0.15:
                self._bov_env = 1.0
                self._bdim_phase = 0.0
            if self._bov_env > 1e-3 and self.o_chord:
                # easter egg: the blow-off resolves as a B-diminished chord
                n = np.arange(frames)
                env = np.exp(-n / (sr * 0.18)) * self._bov_env
                chord = np.zeros(frames, dtype=np.float64)
                inc = 2.0 * math.pi / sr
                for fz in _BDIM_HZ:
                    chord += np.sin(self._bdim_phase * (fz / _BDIM_HZ[0]) + inc * fz * n)
                self._bdim_phase = (self._bdim_phase + inc * _BDIM_HZ[0] * frames)
                out += (tv * 0.7) * chord * env
                self._bov_env *= math.exp(-frames / (sr * 0.2))
            elif self._bov_env > 1e-3:
                n = np.arange(frames)
                noise = self._rng.standard_normal(frames)
                if self.flutter:
                    fl = 18.0 + 12.0 * bfrac             # surge rate rises with boost
                    ph = self._flutter_phase + 2.0 * math.pi * fl * n / sr
                    if frames:
                        self._flutter_phase = float(ph[-1] % (2.0 * math.pi))
                    pulse = np.clip(np.sin(ph), 0.0, 1.0) ** 3  # spiky 'tu' bursts
                    env = np.exp(-n / (sr * 0.20)) * self._bov_env
                    out += (tv * 1.2) * noise * pulse * env
                    self._bov_env *= math.exp(-frames / (sr * 0.24))
                else:
                    env = np.exp(-n / (sr * 0.09)) * self._bov_env
                    out += (tv * 0.9) * noise * env      # clean 'pshhh'
                    self._bov_env *= math.exp(-frames / (sr * 0.13))
        self._prev_throttle = sim.throttle

        gv = P["gearbox_vol"]
        dt = sim.drivetrain
        # Straight-cut gear whine tracks ROAD SPEED (the output-shaft / final-drive
        # mesh), NOT engine rpm.  So the pitch sweeps continuously low->high as the
        # car accelerates and does NOT dip on every upshift the way an rpm-tracked
        # tone wrongly would — that single rising whine is the straight-cut sound.
        if self.straight_cut and gv > 1e-3 and dt.v > 0.4 and dt.gear > 0:
            wheel_rps = dt.v / (2.0 * math.pi * max(dt.wheel_radius, 0.05))
            f = wheel_rps * dt.final_drive * 9.0             # final-drive mesh ~ speed
            if 30.0 < f < sr * 0.45:
                # augmented-triad voicing (root + maj3 + aug5) for the gear whine
                gw += (gv * 0.4) * self._whine(
                    f, frames, list(_AUG_TRIAD), phase_attr="_gearbox_phase")

        # --- hybrid: electric-motor whine + e-turbo e-compressor whine ----------
        hv = P["hybrid_vol"]
        if hv > 1e-3:
            # electric drive motor: a clean high whine that rises with rpm and
            # swells with the motor's assist (throttle), present on hybrids.
            if eng.hybrid_kw > 0.0 and sim.hybrid_on and sim.throttle > 0.02:
                fm = (rpm / 60.0) * 14.0                  # geared-up motor whine
                if 60.0 < fm < sr * 0.45:
                    amp = hv * 0.18 * min(sim.throttle, 1.0)
                    out += amp * self._whine(fm, frames, [(1, 1.0), (2, 0.3)],
                                             phase_attr="_motor_phase")
            # e-turbo / e-compressor: a steady electric whine whenever it makes
            # boost (no spool lag, so it's there the moment you ask for it).
            if eng.electric_turbo and bfrac > 0.02:
                fe = 2200.0 + bfrac * 2600.0
                out += (hv * 0.22 * bfrac) * self._whine(
                    min(fe, sr * 0.45), frames, [(1, 1.0)], phase_attr="_ecomp_phase")

        # pipe-wall thickness: dull the brassy 'trumpet' edge of the whines
        wt = P["wall_thickness"]
        if _HAVE_SCIPY and wt > 1e-3:
            cut = min(max(7000.0 - 5600.0 * wt, 900.0), sr * 0.45)
            b, a = butter(2, cut / (sr / 2), btype="low")
            out, self._wall_out_zi = lfilter(b, a, out, zi=self._wall_out_zi)
            gw, self._wall_gw_zi = lfilter(b, a, gw, zi=self._wall_gw_zi)
        return out, gw

    def _overrun_pops(self, frames):
        """Overrun exhaust pops/bangs ('放炮') — modelled like little combustion
        events: each pop is a sharp transient + a PILE-DRIVING power chord (root
        + fifth + octave) whose pitch glides DOWNWARD (the dewp/blat), with a low
        thump for body.  Muffled and reverbed.  Off unless self.pops_on."""
        P = self.params
        lvl = P["pops"]
        if not self.pops_on or lvl < 1e-3:
            return 0.0
        sim, eng, sr = self.sim, self.sim.engine, self.sample_rate
        rpm = sim.rpm
        # being on the gas loads the pipe with fuel that lights off on lift
        if sim.throttle > 0.5:
            self._was_on_gas = min(1.0, self._was_on_gas + 0.05)
        else:
            self._was_on_gas *= 0.996
        overrun = (sim.ignition_on and sim.throttle < 0.06
                   and rpm > eng.idle_rpm * 1.5)
        # trigger a new pop once the previous one is mostly done (allows crackle)
        if overrun and self._pop_age > self._pop_len * 0.45:
            rf = min(rpm / max(eng.redline_rpm, 1.0), 1.0)
            aggr = 2.4 if eng.anti_lag else 1.0
            rate = lvl * aggr * (0.06 + 0.55 * rf) * (0.3 + 0.7 * self._was_on_gas)
            if self._rng.random() < rate:
                big = self._rng.random() < (0.3 if eng.anti_lag else 0.14)
                self._pop_age = 0
                self._pop_len = int(sr * (0.16 if big else 0.085))
                self._pop_f0 = (95.0 if big else 150.0) * (0.85 + 0.4 * self._rng.random())
                self._pop_amp = (1.0 if big else 0.6) * (0.6 + 0.7 * self._rng.random())
        out = np.zeros(frames, dtype=np.float64)
        if self._pop_age < self._pop_len:
            n = np.arange(frames)
            t = self._pop_age + n                    # samples since this pop began
            mask = (t < self._pop_len).astype(np.float64)
            L = float(self._pop_len)
            env = np.exp(-t / (sr * (0.06 if self._pop_len > sr*0.1 else 0.03))) * mask
            # power chord with a DOWNWARD pitch glide: phase = 2*pi*integral(f)
            f0, k = self._pop_f0, 0.5                 # glide down to 0.5*f0
            ph = 2 * math.pi / sr * (f0 * t - f0 * k * t * t / (2 * L))
            chord = (np.sin(ph) + 0.7 * np.sin(1.5 * ph) + 0.45 * np.sin(2.0 * ph)
                     + 0.4 * np.sin(0.5 * ph))        # root+fifth+octave+sub
            thump = np.sin(2 * math.pi * 72.0 * t / sr) * np.exp(-t / (sr * 0.035)) * mask
            crack = self._rng.standard_normal(frames) * np.exp(-t / (sr * 0.004)) * mask
            out = self._pop_amp * (0.7 * chord * env + 0.6 * crack + 0.5 * thump)
            self._pop_age += frames
        else:
            self._pop_age += frames
        if not _HAVE_SCIPY:
            return (lvl * 1.4) * out
        # muffle (a low-pass whose cutoff drops as pop_muff rises)
        cut = min(max(9000.0 - 7600.0 * P["pop_muff"], 700.0), sr * 0.45)
        b, a = butter(2, cut / (sr / 2), btype="low")
        out, self._pop_lp_zi = lfilter(b, a, out, zi=self._pop_lp_zi)
        out = (lvl * 1.4) * out
        if P["pops_reverb"] > 1e-3:                   # roomy echo
            self._pops_reverb.mix = P["pops_reverb"]
            out = self._pops_reverb.process(out)
        return out

    def _callback(self, outdata, frames, time_info, status):
        mono = self._render_block(frames)
        nch = outdata.shape[1]
        if nch >= 2:
            # equal-power stereo pan from the spatial pad's X axis
            ang = self.params["spatial_x"] * (math.pi * 0.5)
            outdata[:, 0] = mono * math.cos(ang)
            outdata[:, 1] = mono * math.sin(ang)
            if nch > 2:
                outdata[:, 2:] = 0.0
        else:
            outdata[:, 0] = mono

    # ----------------------------------------------------------- lifecycle
    def start(self):
        if not self.enabled:
            return False
        attempts = []
        if self.prefer_exclusive and self._device is not None:
            try:
                excl = sd.WasapiSettings(exclusive=True)
                attempts.append(("exclusive", dict(
                    device=self._device, samplerate=self.sample_rate, channels=2,
                    blocksize=128, latency="low", extra_settings=excl)))
            except Exception:
                pass
        if self._device is not None:
            attempts.append(("shared", dict(
                device=self._device, samplerate=self.sample_rate, channels=2,
                blocksize=BLOCK, latency="low")))
            attempts.append(("shared-mono", dict(
                device=self._device, samplerate=self.sample_rate, channels=1,
                blocksize=BLOCK, latency="low")))
        attempts.append(("default", dict(
            device=None, samplerate=SAMPLE_RATE, channels=2, blocksize=BLOCK,
            latency="low")))
        attempts.append(("default-mono", dict(
            device=None, samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK,
            latency="low")))

        for mode, cfg in attempts:
            try:
                self._rebuild_for_rate(cfg["samplerate"])
                self._stream = sd.OutputStream(
                    dtype="float32", callback=self._callback, **cfg)
                self._stream.start()
                self.mode = mode
                self.latency_ms = round(self._stream.latency * 1000, 1)
                return True
            except Exception:
                self._stream = None
                continue
        print("[audio] disabled: no usable output device")
        self.enabled = False
        return False

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
