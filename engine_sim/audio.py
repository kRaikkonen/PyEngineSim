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

# On Android there is no PortAudio (so no sounddevice); play through pygame's
# SDL2 mixer instead.  Detect the phone via python-for-android's env markers.
ON_ANDROID = bool(os.environ.get("ANDROID_ARGUMENT")
                  or os.environ.get("ANDROID_APP_PATH")
                  or os.environ.get("ANDROID_PRIVATE"))

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

# fuel-rail pressure (bar) by injection type — the physical driver of the
# injector-close click (see the white-box injection tick in the synth).
_INJ_PRESSURE = {"port": 4.0, "dual": 130.0, "direct": 200.0,
                 "piezo": 350.0, "diesel": 2000.0}

# Exhaust-wall MATERIAL properties (Young's modulus GPa, density kg/m^3, damping
# loss factor) — VIRTUAL ANALOG of the pipe ringing.  The wall's structural
# resonance frequency ~ sqrt(E/rho) (material sound speed), how hard it is EXCITED
# by the gas pulses ~ 1/rho (a light wall vibrates more), and how LONG/sharp it
# rings ~ 1/loss (titanium sings, cast iron thuds).  Steel is the reference.
_MATERIAL = {
    "steel": (200.0, 7850.0, 0.0016), "mild_steel": (200.0, 7850.0, 0.0016),
    "stainless": (193.0, 8000.0, 0.0009), "304": (193.0, 8000.0, 0.0009),
    "321": (193.0, 8000.0, 0.0009), "321ss": (193.0, 8000.0, 0.0009),
    "321ti": (150.0, 6200.0, 0.0006), "ss": (193.0, 8000.0, 0.0009),
    "titanium": (116.0, 4500.0, 0.0004), "ti": (116.0, 4500.0, 0.0004),
    "inconel": (205.0, 8440.0, 0.0020),
    "aluminium": (69.0, 2700.0, 0.0002), "aluminum": (69.0, 2700.0, 0.0002),
    "iron": (110.0, 7200.0, 0.0120), "cast_iron": (110.0, 7200.0, 0.0120),
    "ceramic": (300.0, 3800.0, 0.0040), "ceramic_coated": (300.0, 3800.0, 0.0040),
    "cgi": (145.0, 7100.0, 0.0060), "compacted_graphite": (145.0, 7100.0, 0.0060),
    "magnesium": (45.0, 1800.0, 0.0010), "mag": (45.0, 1800.0, 0.0010),
}
_MAT_REF = (5.048, 7850.0, 0.0016)     # steel sqrt(E/rho), rho, loss (references)


def _material_acoustics(name):
    """(freq_factor, ring_gain, q_factor) for the wall resonance, DERIVED from
    the material's real E / density / damping — not hand-tuned."""
    E, rho, loss = _MATERIAL.get(name, _MATERIAL["steel"])
    c_ref, rho_ref, loss_ref = _MAT_REF
    mf = math.sqrt(E * 1e9 / rho) / (c_ref * 1000.0)      # wall sound-speed ratio
    gain = (rho_ref / rho) ** 0.30                        # lighter -> excited more
    q_fac = (loss_ref / loss) ** 0.30                     # low loss -> long sing
    return mf, gain, q_fac

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


class _BlockDelay:
    """Vectorised ring-buffer delay line.  The delay is a READ OFFSET (samples)
    that may change every block, so it can track the live (temperature-dependent)
    speed of sound — the delay 'breathes' with rpm/load instead of being frozen.
    Feed-forward read only (no recursion), so it is always stable and needs no
    Python sample loop."""

    def __init__(self, max_delay):
        self.buf = np.zeros(int(max_delay) + 4, dtype=np.float64)
        self.wp = 0

    def process(self, x, delay):
        n = len(x)
        N = len(self.buf)
        d = int(min(max(delay, 1), N - 2))
        wi = (self.wp + np.arange(n)) % N
        self.buf[wi] = x                              # write this block first
        ri = (self.wp + np.arange(n) - d) % N         # ...then read it delayed
        out = self.buf[ri]
        self.wp = (self.wp + n) % N
        return out


class CylinderVoicing:
    """Deterministic per-cylinder exhaust-voice variation derived PURELY from
    intake / exhaust GEOMETRY — no random numbers, no per-car tuning.  It gives
    each cylinder its own subtle character so the idle has real granular
    lumpiness (you hear the cylinders fire one by one) instead of one uniform
    electronic pulse.  At high rpm the fires overlap and the fixed per-cylinder
    offsets simply average into a thicker texture — so no rpm-dependent logic is
    needed: the same constants just *blend* once the firing gets dense.

    Three physical sources, all precomputed ONCE per engine:

      1. RUNNER HF DAMPING — a longer / thinner header runner loses more top end
         (wall friction + bends), so each cylinder gets a 1st-order low-pass whose
         cutoff is inversely proportional to its own runner length.  Equal-length
         headers => near-identical cutoffs => barely any difference; unequal / long
         headers => spread cutoffs => an obvious cylinder-to-cylinder colour shift.
         (Exactly the "equal headers small, unequal headers large" physical rule.)

      2. INTAKE DISTRIBUTION — a cylinder fed by a longer intake runner breathes a
         little less air, so its combustion (hence exhaust-pulse amplitude) is a
         few percent weaker.  A fixed +/-3% amplitude offset per cylinder.  Mapped
         through the FIRING ORDER (not the physical order) it lands as an irregular
         beat in time -> audible lumpiness rather than a smooth ramp.

      3. INTER-CYLINDER BACKPRESSURE — each exhaust pulse leaves residual pressure
         at the collector that loads the NEXT cylinder to fire on that collector.
         A short firing gap (closely-spaced fires) => more residual backpressure =>
         the next pulse is trimmed a touch and its rising edge softened.  Even-
         firing engines see a uniform gap (no effect); UNEVEN firing (cross-plane
         V8, unequal headers) gets a regular strong/weak beat for free.

    Cost: one 1st-order filter per cylinder per block (a few microseconds for a
    V12 at 48 kHz); everything else is just precomputed scalars.  One switch
    (params["cyl_voice"], 0 = off) scales the whole effect.
    """

    def __init__(self, runner_len, channel_of, offsets, sr,
                 intake_runner_m=0.30, bp_coupling=0.5):
        self.n = n = len(runner_len)
        self._scipy = _HAVE_SCIPY
        # ---- (1) runner HF damping: cutoff ~ mean_len / len -------------------
        Lmean = sum(runner_len) / max(n, 1)
        self._b, self._a, self._zi = [], [], []
        for L in runner_len:
            fc = 9000.0 * (Lmean / max(L, 1e-3))         # longer runner -> duller
            fc = min(max(fc, 3500.0), 13000.0)
            if _HAVE_SCIPY:
                b, a = butter(1, min(fc, sr * 0.45) / (sr / 2), btype="low")
                self._b.append(b); self._a.append(a); self._zi.append(np.zeros(1))
            else:
                self._b.append(None); self._a.append(None); self._zi.append(None)
        # ---- (2) intake distribution: +/-3% from intake runner length --------
        # We don't have an explicit per-cylinder intake length, so we reuse the
        # same along-the-rail ordering the exhaust runners imply (cylinders sorted
        # by runner length) and apply a fixed gradient: the longest intake runner
        # breathes ~3% less, the shortest ~3% more.  intake_runner_m scales nothing
        # by itself here (the gradient is normalised) but is kept as the physical
        # handle / JSON field and nudges the spread a touch for very long runners.
        spread3 = 0.03 * min(max(intake_runner_m / 0.30, 0.5), 1.6)
        order = sorted(range(n), key=lambda i: runner_len[i])
        intake_amp = [1.0] * n
        for rank, j in enumerate(order):
            frac = rank / max(n - 1, 1)                  # 0 short .. 1 long runner
            intake_amp[j] = 1.0 + spread3 * (1.0 - 2.0 * frac)
        # ---- (3) inter-cylinder backpressure ---------------------------------
        by_chan = {}
        for j in range(n):
            by_chan.setdefault(channel_of[j], []).append(j)
        bp_amp = [1.0] * n
        bp_edge = [1.0] * n
        for members in by_chan.values():
            seq = sorted(members, key=lambda i: offsets[i] % 720.0)
            m = len(seq)
            if m < 2:
                continue
            gaps = []
            for a_i in range(m):
                cur, prev = seq[a_i], seq[(a_i - 1) % m]
                gap = (offsets[cur] - offsets[prev]) % 720.0
                gaps.append(gap if gap > 1e-6 else 720.0)
            gmean = sum(gaps) / m
            for a_i in range(m):
                j = seq[a_i]
                gn = gaps[a_i] / gmean                   # <1 = closer than even-fire
                trim = bp_coupling * max(1.0 - gn, 0.0)  # only short gaps load up
                bp_amp[j] = 1.0 - 0.06 * trim            # trimmed amplitude
                bp_edge[j] = 1.0 + 0.5 * trim            # softer (blunter) edge
        # combined per-cylinder amplitude factor and edge (rise) factor
        self.amp = [intake_amp[j] * bp_amp[j] for j in range(n)]
        self.edge = bp_edge

    def damp(self, j, x):
        """Apply cylinder j's runner HF-damping low-pass (state carried)."""
        if not self._scipy:
            return x
        y, self._zi[j] = lfilter(self._b[j], self._a[j], x, zi=self._zi[j])
        return y


class Synthesizer:
    """Streams physics-driven engine audio from a live :class:`Simulator`."""

    def __init__(self, simulator, sample_rate: int = None, device=None):
        self.sim = simulator
        self.volume = 1.0   # mute switch (M): 1.0 on, 0.0 muted
        self.enabled = _HAVE_SD or ON_ANDROID

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
        self._stroke_ref = cyls[0].stroke        # blowdown-pulse depth ~ stroke
        self._audio_crank = 0.0

        # VIRTUAL-ANALOG single-firing SHAPE — gas-dynamic blowdown SHARPNESS.
        # The single combustion's exhaust pulse is the discharge of the cylinder
        # gas 'capacitor' (volume V_evo at valve-open) through the exhaust-valve
        # 'orifice' (effective area A_ev) at the hot-gas sound speed c.  Its
        # emptying RATE  S = A_ev·c / V_evo  is the physics that sets whether the
        # blowdown is a PEAKY snap or a SOFT hump: a big-valve, small-bore,
        # high-CR jewel (an F1: S≈2×) empties in a flash -> sharp, bright, ripping;
        # a huge low-CR diesel cylinder (S≈0.5×) drains slowly -> soft and woofy.
        # Normalised to the validated reference (Aventador, S≈380) so THAT car's
        # pulse is byte-for-byte unchanged and every other car's peakiness now
        # falls out of real bore/stroke/valve/CR geometry — replacing the fixed
        # 0.30·tau blowdown fraction and the hand-set blow/displacement split.
        c0 = cyls[0]
        _vpc = getattr(simulator.engine, "valves_per_cyl", 4)
        _dev = 0.83 * c0.bore * (0.39 if _vpc >= 4 else 0.47)   # exhaust-valve dia
        _Aev = 0.5 * (math.pi * 0.25 * _dev * _dev)             # ~half-throat area
        _Vevo = c0.clearance_volume + 0.9 * c0.displacement     # cyl vol at EVO (~BDC)
        _S = _Aev * 550.0 / max(_Vevo, 1e-9)
        self._bd_sharp = min(max(_S / 380.0, 0.40), 2.30)       # 380 = reference

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
        # dry/wet REBALANCED (2026-07 "system body" pass): dry 1.6 / res 0.10+0.24
        # meant the raw combustion bang was ~5x the pipe's own voice — that IS the
        # sound of a bench dyno with the mic at the header.  A car on the street
        # is mostly its exhaust SYSTEM talking: bang down ~20%, pipe body up ~50%.
        self.params = {
            "dry": 1.44,         # combustion bang level (was cut to 1.28 — too far;
                                 #   engines lost their DRY combustion punch/fizz)
            "res1": 0.15,         # primary pipe resonance (runner)
            "res2": 0.30,         # secondary pipe resonance (full system) — a
                                  #   touch back UP: the radiation made it too dry
            "tail_rad": 0.26,     # tailpipe radiation mix: 0 = mic inside the duct,
                                  #   up = mic behind the car (dQ/dt far field).
                                  #   0.35 was TOO DRY — pulled back for body
            "crack": 0.12,        # attack snap (explosion punch)
            "attack_deg": 9.0,    # onset softness (deg): bigger = blunter attack
            "body": 1.60,         # thickness / low-end of each firing (浑厚)
            "drive": 0.40,        # saturation -> tight, solid 'power chord' grip
            "firing_pitch": 90.0,  # Hz, pitch of that firing body
            "pulse_tau": 22.0,    # blowdown decay (deg) -> firing timbre/brightness
            "turbulence": 0.34,   # gas-rush FIZZ gated by each firing (was 0.2 —
                                  #   too little; engines lost their fizzy grit)
            "src_reverb": 0.26,   # reverb on the explosion itself (pre-pipe) — a
                                  #   head/port cavity is TINY; 0.48 smeared the
                                  #   pulse transients into mush (wet complaint)
            "reverb": 0.18,      # spatial reverb mix — an exhaust mic is OUTDOORS
                                 #   in free field; 0.4 was a small room, far too wet
            "intake": 0.11,       # induction roar level (halved — was too windy)
            "eq_low": 0.0,        # dB
            "eq_mid": 0.0,        # dB
            "eq_high": 0.0,       # dB
            "presence": 0.0,      # dB — guitar-amp 'presence' (upper-mid bite ~3 kHz)
            "cyl_spread": 0.5,    # how much each cylinder's pitch/level differs
            "master": 0.6,        # master output volume
            "spatial_x": 0.5,     # stereo pan: 0 left .. 1 right
            "spatial_y": 0.6,     # distance: 0 far (dark/quiet) .. 1 near
            "super_vol": 0.6,     # mechanical supercharger (roots/centrifugal) whine
            "turbo_vol": 0.21,    # turbo spool whistle + BOV (0.45->0.30->0.21, -30%)
            "gearbox_vol": 0.375, # straight-cut gearbox whine (was 0.5 -> 75%)
            "wall_thickness": 0.3,  # pipe-wall thickness: higher = duller, less 'trumpet'
            "shear": 0.07,        # tail-pipe air-shear roar at the exit (mass-flow)
            "whine": 1.0,         # high-rpm standing-wave whine/scream amount
            "valve_open": 1.0,    # how far the active exhaust valve opens at revs
            "muffler": 1.0,       # muffler internal-reflection (comb) depth
            "cyl_voice": 1.0,     # per-cylinder voicing amount (0 = perfectly uniform)
            "road_noise": 0.10,   # tyre/road rumble that swells with road speed (subtle)
            "gear_grain": 1.0,    # gear-driven valvetrain whir mix (x eng.gear_grain)
            "mech": 0.16,         # valvetrain tick layer (cam/tappet clatter)
            "gear_mesh": 0.10,    # transmission mesh whine under load (helical, subtle)
            "spool_reverb": 0.15, # dedicated reverb on the induction/spool sounds
            "hybrid_vol": 0.5,    # electric-motor / e-turbo whine level (hybrids)
            "gearbox_reverb": 0.12,  # dedicated reverb on the straight-cut whine
            "fire_weight": 0.5,   # fire-tone pad X: thin/bright .. thick/fat body
            "fire_grit": 0.3,     # fire-tone pad Y: smooth .. coarse/raw saturation
            "pops": 0.6,          # overrun pop level (power-chord bangs on decel)
            "pop_muff": 0.4,      # how muffled the pops are (0 sharp .. 1 dull)
            "pops_reverb": 0.22,  # dedicated reverb on the overrun pops
        }

        # WHITE-BOX resonance mix from exhaust geometry (transmission-line
        # reflection physics, exhaust_tmm) — the per-car res1/res2/wall/muffler
        # now FALL OUT of the real pipe/collector/muffler dimensions instead of a
        # fixed hand-tuned default.  Set as the slider defaults so each car starts
        # at its physical value (the user can still trim).
        try:
            from .exhaust_tmm import exhaust_acoustics
            r1, r2, wl, mf = exhaust_acoustics(simulator.engine)
            self.params["res1"] = r1
            self.params["res2"] = r2
            self.params["wall_thickness"] = wl
            self.params["muffler"] = mf
        except Exception:
            pass

        # WHITE-BOX firing-VOICE MIX from the physical pulse content (step 2 of the
        # single-firing rewrite).  The three firing voices + fizz + grit are no
        # longer mixed at fixed GLOBAL gains hand-tuned once for all 130 cars; each
        # level now FALLS OUT of the same geometry that shapes the pulse, anchored
        # so the validated reference (Aventador) reproduces its known-good balance
        # and every other car varies by real physics:
        #   crack  (edge snap)   ~ blowdown SHARPNESS  -> peaky F1 cracks, diesel dull
        #   body   (low thunder) ~ displacement/cyl    -> big cylinder thunders, F1 thin
        #   turbulence (fizz)    ~ mean piston speed    (gas_truth's turb = 0.5·mps)
        #   drive  (combustion grit) ~ compression ratio -> violent burn tears more
        # (dry — the overall bang level — stays the anchor; these colour RELATIVE
        #  to it, and AGC normalises absolute loudness downstream.)
        try:
            _disp = max(c0.displacement, 1e-6)                       # m^3 / cylinder
            _cr = max(getattr(c0, "compression_ratio", 10.5) or 10.5, 5.0)
            _mps = 2.0 * c0.stroke * max(simulator.engine.redline_rpm, 1000.0) / 60.0
            self.params["crack"] = min(max(0.12 * self._bd_sharp ** 0.6, 0.05), 0.28)
            self.params["body"] = min(max(1.60 * (_disp / 5.415e-4) ** 0.30, 1.0), 2.6)
            self.params["turbulence"] = min(max(0.34 * (_mps / 21.65) ** 0.5, 0.18), 0.5)
            self.params["drive"] = min(max(0.40 * (_cr / 11.8) ** 0.5, 0.25), 0.6)
        except Exception:
            pass

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
        self.ssqv = False         # HKS SSQV atmospheric dump: loud sharp 'TSSSH'
        self.last_level = 0.0     # RMS of last rendered block (exhaust loudness meter)
        self.last_wave = np.zeros(64)   # decimated waveform for the HUD flow scope
        self.last_combustion = np.zeros(64)  # decimated REAL combustion voice (analyzer)
        # per-stage exhaust-path waveform taps for the refresh-style stage scopes.
        # Only captured while the UI overlay is open (scope_enabled) to keep the
        # audio callback cheap otherwise.
        self.scope_enabled = False
        self._stage_taps = {}     # tap name -> decimated np.float64 snapshot
        # Two selectable display orders over the SAME captured taps: the EXHAUST
        # GAS path (flow, header -> tailpipe exit) and the full LISTENER AUDIO
        # chain (... -> EQ -> cabin -> output).  The UI toggles between them.
        self._flow_stages = [
            "header", "head/port", "catalytic", "standing-wave", "resonator",
            "muffler", "valve bypass", "wall de-honk", "metal ring", "megaphone",
            "thunder", "reflection", "tailpipe exit",
        ]
        self._audio_stages = [
            "header", "head/port", "catalytic", "standing-wave", "muffler",
            "induction+gears", "metal ring", "thunder", "reflection", "block",
            "EQ", "cabin/room", "output",
        ]
        self._cold = 1.0          # cold-start factor (1 cold .. 0 warmed up)
        self._gear_phase = 0.0    # gear-mesh phase for the gear-grain whir
        self._whine_phase = 0.0   # blower / turbo whistle oscillator phase
        self._gearbox_phase = 0.0 # gearbox whine: selected (loaded) gear mesh
        self._gwinput_phase = 0.0 # gearbox whine: primary/input constant mesh
        self._gwa_phase = 0.0     # gearbox whine: a quieter unselected gear mesh
        self._gwb_phase = 0.0     # gearbox whine: another unselected gear mesh
        self._finaldrive_phase = 0.0  # final-drive / crown-wheel whine
        self._flutter_phase = 0.0 # compressor-surge flutter oscillator phase
        self._motor_phase = 0.0   # hybrid electric-motor whine oscillator phase
        self._ecomp_phase = 0.0   # e-compressor (e-turbo) whine oscillator phase
        self._seq_phase = 0.0     # sequential primary (small) turbo whistle phase
        self._seq2_phase = 0.0    # sequential secondary (big) turbo whistle phase
        self._seq_prev = 0.0      # last secondary-turbo presence (for the hand-over)
        self._seq_surge = 0.0     # decaying surge whoosh when the big turbo joins
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
        self._thr_ref = 0.0       # slowly-decaying recent-throttle peak (lift detect)
        self._bov_env = 0.0       # blow-off-valve 'pshhh' envelope
        self._bov_prev = 0.0      # stock-recirc dark-noise low-pass state
        self._lock = threading.Lock()
        self._stream = None
        self._pg_run = False          # pygame.mixer feeder thread (Android backend)
        self._pg_thread = None
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
        # Equal-length headers (race / high-revving exotics) keep every runner the
        # same length -> pulses stack tightly -> a clean, linear, high scream.
        # Unequal headers (muscle / road V8s) spread the lengths -> staggered
        # arrival -> the low-rpm 'boil' / rumble.  Derived from the car's own
        # nature so no preset needs hand-editing.
        # Header runner-length spread is GRADUATED, not just equal-vs-unequal: a
        # "6-into-1 equal-length" header is tight (low spread), a tuned 4-2-1 road
        # header is part-way, a cast log manifold is very uneven.  header_equality
        # (0 = log .. 1 = perfectly equal) sets it directly when given; otherwise
        # we auto-classify from the car's nature.
        he = getattr(eng, "header_equality", -1.0)
        if he >= 0.0:
            self._equal_headers = he >= 0.6
            spread = max(0.05, 0.95 * (1.0 - he))
        else:
            self._equal_headers = (eng.straight_cut or eng.gearbox_type == "single"
                                   or eng.redline_rpm >= 8400)
            spread = 0.10 if self._equal_headers else 0.95
        seen, posn = {}, []
        for c in eng.cylinders:
            ch = 0 if (self._nchan == 1 or c.bank_angle_deg < 0) else 1
            posn.append(seen.get(ch, 0)); seen[ch] = seen.get(ch, 0) + 1
        # store each runner's LENGTH (m); the per-block delay is L / c(live) so the
        # whole manifold interference pattern breathes with exhaust temperature.
        self._runner_len = []
        for j, c in enumerate(eng.cylinders):
            ch = self._channel_of[j]
            frac = posn[j] / max(seen[ch] - 1, 1)        # 0 (near) .. 1 (far)
            self._runner_len.append(prim * (1.0 - spread * 0.5 + spread * frac))
        maxd = int(max(self._runner_len) / 380.0 * sr) + BLOCK + 8
        self._runner_dl = [_BlockDelay(maxd) for _ in eng.cylinders]
        # (#3) muffler internal reflections: two short feed-forward taps (expansion
        # chamber + baffle path lengths) -> comb notches that give the box its
        # TIMBRE, not just attenuation.
        md = int(0.5 / 380.0 * sr) + BLOCK + 8
        self._muff_dl1, self._muff_dl2 = _BlockDelay(md), _BlockDelay(md)
        self._muff_len = (0.17, 0.31)
        self._absorb_zi = np.zeros(1)     # absorptive-muffler HF soak state
        self._flex_zi = np.zeros(2)       # corrugated flex-pipe buzz state
        self._fcache = {}                 # cached IIR designs (avoid per-block redesign)
        self._turbine_zi = np.zeros(2)    # boost-dependent turbine damping state
        self._scream_phase = 0.0          # F1 high-rpm harmonic-stack oscillator
        self._itb_phase = 0.0             # ITB induction-howl oscillator
        self._mesh_phase = 0.0            # transmission gear-mesh whine oscillator
        self._rad_prev = 0.0              # tailpipe-radiation derivative state
        self._burble_prev = 0.0          # overrun-burble low-pass state
        self._comb_load = 1.0            # positive-combustion load (0 on overrun)
        self._over_lp_zi = None          # overrun-darkening low-pass state
        self._over_prev = 0.0            # overrun-darkening (no-scipy) state
        self._wob_ph = 0.0                # cam-chop / balance-shaft wobble phase
        self._wob_w = 0.0
        self._inj_amt = self._cam_lump = self._balance_rough = 0.0
        if _HAVE_SCIPY:                   # injector-clatter band-pass (~5-9 kHz)
            self._inj_bp = butter(2, [5000.0 / (sr / 2),
                                      min(9000.0, sr * 0.45) / (sr / 2)], btype="band")
        else:
            self._inj_bp = None
        self._inj_zi = np.zeros(4)
        # (#4) full-system round-trip reflection: a weak low-passed echo at the
        # pipe's round-trip time -> low-frequency elasticity + a longer, rounder tail.
        self._tail_len = 2.0 * max(eng.exhaust_total_m, 0.5)
        self._tail_dl = _BlockDelay(int(self._tail_len / 380.0 * sr) + BLOCK + 8)
        # deterministic per-cylinder voicing (granular idle, no random/no per-car)
        # Exhaust merge topology: which cylinders share a SECONDARY collector.
        #   4-1   -> collector == channel (all runners merge at once: raw, top-end)
        #   4-2-1 -> each bank splits into TWO secondaries, pairing cylinders that
        #            fire ~360 deg apart so each secondary sees evenly-spaced pulses.
        #            The PAIRED cylinders share a pipe and load each other — this is
        #            exactly "which cylinders share a header runner", audibly.
        htype = getattr(eng, "header_type", "auto")
        if htype == "auto":
            htype = "4-1" if self._equal_headers else "4-2-1"
        self._header_type = htype
        self._collector_of = list(self._channel_of)
        if htype in ("4-2-1", "tri-y", "tri-Y"):
            by_ch, coll = {}, 0
            for j in range(eng.num_cylinders):
                by_ch.setdefault(self._channel_of[j], []).append(j)
            for ch in sorted(by_ch):
                members = sorted(by_ch[ch], key=lambda i: self._offsets[i] % 720.0)
                if len(members) >= 4:                  # pair only 4+ runners/bank
                    for rank, j in enumerate(members):
                        self._collector_of[j] = coll + (rank % 2)
                    coll += 2
                else:
                    for j in members:
                        self._collector_of[j] = coll
                    coll += 1
        # backpressure / shared-runner loading groups by the SECONDARY collector
        self._cyl_voice = CylinderVoicing(
            self._runner_len, self._collector_of, [float(o) for o in self._offsets], sr,
            intake_runner_m=getattr(eng, "intake_runner_m", 0.30),
            bp_coupling=getattr(eng, "backpressure_coupling", 0.5))
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
            # intake / induction path: the runner/velocity-stack rings at its
            # QUARTER-WAVE resonance f = c_air / (4 L) (white-box tube acoustics,
            # c_air = 343 m/s) — a short race stack honks high, a long torque
            # runner low.  So the real per-car intake length is what pitches the
            # induction note, straight from geometry (was a fixed 150 Hz).
            f_intake = min(max(343.0 / (4.0 * max(eng.intake_runner_m, 0.05)),
                               90.0), 900.0)
            self._intake_bp = _peaking(f_intake, 1.1, 7.0, sr)
            self._intake_lp = butter(2, min(2.2 * f_intake + 900.0, sr * 0.45)
                                     / (sr / 2), btype="low")
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
            # cat high-frequency damping ~ cell density: A(f) grows with f^2 (a
            # 2nd-order LP), and a denser honeycomb (more cells/in^2) pulls the
            # cutoff down, magnetic of how a packed 400-cpsi stock cat smothers the
            # top end while a 200-cpsi high-flow cat lets the whine through.
            cells = max(getattr(eng, "cat_cells_cpsi", 400), 50)
            cat_fc = min(max(5200.0 * math.sqrt(400.0 / cells), 2800.0), 9000.0)
            self._road_lp = butter(2, min(cat_fc, sr * 0.45) / (sr / 2), btype="low")
            self._road_sh = _peaking(3400.0, 0.7, -5.5, sr)
            self._road_lp_zi = np.zeros(2)
            self._road_sh_zi = np.zeros(2)
            self._gpf_lp_zi = np.zeros(1)      # gasoline particulate filter soak (1-pole)
            self._wgate_zi = np.zeros(2)       # external-wastegate screamer formant
            # tailpipe air-shear: high-pressure gas tearing into still air at the
            # exit — a broadband hiss/roar swelling with exhaust mass-flow, the
            # OUTERMOST layer you hear at the back of the car.
            self._shear_bp = _bandpass(2600.0, 0.6, sr)
            self._shear_hp = butter(2, 900.0 / (sr / 2), btype="high")
            self._shear_bp_zi = np.zeros(2)
            # F1 scream TEARING-noise band (~3 kHz, moderate Q): the shredding-air
            # texture that rides the tone so an F1 reads as a violent engine, not a
            # pure electronic buzz — band-limited so it tears, not hisses.
            self._f1tear_bp = _bandpass(3000.0, 0.9, sr)
            self._f1tear_zi = np.zeros(2)
            self._shear_hp_zi = np.zeros(2)
            # (Step 3) cylinder-head / exhaust-port cavity: a gentle low-pass that
            # 'rounds' the raw pulse so it reads as metal, not a digital click.
            self._head_lp = butter(2, min(11000.0, sr * 0.45) / (sr / 2), btype="low")
            self._head_lp_zi = np.zeros(2)
            # low-pass on the tail round-trip reflection (only lows reflect strongly)
            self._tail_lp = butter(2, 720.0 / (sr / 2), btype="low")
            self._tail_lp_zi = np.zeros(2)
            # road / tyre rumble: a low band (the car actually MOVING down a road,
            # not bolted to a dyno) — band-passed noise that swells with road speed.
            self._roadn = _bandpass(130.0, 0.5, sr)
            self._roadn_lp = butter(2, 520.0 / (sr / 2), btype="low")
            self._roadn_zi = np.zeros(2)
            self._roadn_lp_zi = np.zeros(2)
            # gear-grain: band-passed noise for the gear-driven valvetrain whir
            self._grain_bp = _bandpass(3200.0, 0.7, sr)
            self._grain_zi = np.zeros(2)
            # (Step 4) pipe-wall metal resonance formants — a thin, small-bore pipe
            # rings higher and sharper; a thick, big-bore pipe lower and tighter.
            # The MATERIAL shifts the ring: titanium (stiff & light) sings high and
            # clear, steel sits mid, cast iron is low & dead.  f_wall ~ sqrt(E/rho).
            r = max(eng.exhaust_radius_m, 0.012)
            # MATERIAL -> (formant-frequency multiplier, ring-gain scale).
            # f_wall ~ sqrt(E/rho): stiff & light alloys ring HIGH; cast iron sits
            # LOW & dead.  The ring scale is the metal "ping" strength — stainless
            # is a touch harder/brighter than mild steel, 321 SS brighter still,
            # inconel hard & harsh, and a CERAMIC COATING insulates + damps the
            # ping (smoother, less metallic).
            # VIRTUAL-ANALOG pipe material: frequency, ring gain and ring Q all
            # DERIVED from the wall material's real E / density / damping (see
            # _material_acoustics) — titanium sings (light + low-loss), cast iron
            # thuds (heavy + high-loss), steel is the reference.
            mf, ring, qf = _material_acoustics(getattr(eng, "wall_material", "steel"))
            self._wall_ring = ring
            self._wall_q = qf
            self._wall_f1 = min(max(2300.0 * (0.024 / r) * mf, 1300.0), 4200.0)
            self._wall_f2 = min(self._wall_f1 * 1.85, sr * 0.42)
            self._wallpk1_zi = np.zeros(2)
            self._wallpk2_zi = np.zeros(2)
            # MEGAPHONE / exit-horn: a diverging cone radiates efficiently only
            # ABOVE its cutoff f_horn = c/(2π·a_mouth) (a_mouth = flared-mouth
            # radius), around which it projects a POWERFUL mid 'bark' (a trumpet-
            # bell formant) while low frequencies escape poorly and the thin far-
            # field extreme-top rolls off.  That broad mid emphasis IS the 澎湃有力
            # midrange roar of an open F1/race exit — high AND massive, not a thin
            # whistle.  Frequency falls out of the flare + exit bore.
            mega = min(max(getattr(eng, "megaphone", 0.0), 0.0), 1.0)
            self._mega_amt = mega
            if mega > 0.02:
                a_mouth = r * (1.0 + 1.5 * mega)              # cone opens the mouth
                f_horn = min(max(343.0 / (2.0 * math.pi * a_mouth), 500.0), 3200.0)
                self._mega_f = f_horn * 1.3                   # bark just above cutoff
                self._mega_zi = np.zeros(2)
                self._mega_hi_zi = np.zeros(2)
            else:
                self._mega_f = 0.0

            # STRUCTURE-BORNE / BLOCK RADIATION: the combustion is sealed in the
            # block + head, so the listener hears it radiated THROUGH the casting —
            # CONTAINED by the wall mass (a mass-law low-pass; denser wall = more
            # contained) and rung at the casting's own structural resonances
            # (bending/panel modes ~ sqrt(E/rho)/bore — a bigger, heavier block
            # rings LOWER; a light alloy one HIGHER and longer, the metallic
            # 'clatter').  Adding this parallel path is what stops the note sounding
            # like combustion in the OPEN AIR.  All derived from block material +
            # bore; NOT sent down the exhaust pipe (it radiates off the metal).
            bmat = getattr(eng, "block_material", "aluminium")
            bE, brho, bloss = _MATERIAL.get(bmat, _MATERIAL["aluminium"])
            c_struct = math.sqrt(bE * 1e9 / brho)            # casting bar-wave speed
            bore = max(eng.cylinders[0].bore, 0.05)
            f_blk = 0.0249 * c_struct / bore                 # fundamental panel mode
            self._blk_f1 = min(max(f_blk, 600.0), 2400.0)
            self._blk_f2 = min(self._blk_f1 * 2.15, sr * 0.42)  # 2nd panel mode
            # ring Q from the real material damping (light alloy rings, cast iron thuds)
            self._blk_q = min(max((0.0016 / bloss) ** 0.30 * 1.7, 0.5), 4.5)
            # the LID (white-box): a finite panel radiates its excitation efficiently
            # only up to ~a few times its structural resonance; above that the
            # mechanical mobility rolls off and the combustion is trapped.  So the
            # muffle cutoff is TIED to the SAME sqrt(E/rho)/bore modes as the ring —
            # a heavy iron block resonates AND rolls off LOWER (darker, more sealed),
            # a light alloy higher (brighter) — not an independent hand-picked knob.
            self._blk_fc = min(max(2.4 * f_blk, 1500.0), sr * 0.44)
            self._blk_lp = butter(2, self._blk_fc / (sr / 2), btype="low")
            self._blk_lp_zi = np.zeros(2)
            self._blk1_zi = np.zeros(2)
            self._blk2_zi = np.zeros(2)
            # SEAL weight = how much of the raw open combustion is replaced by the
            # muffled-through-the-block version (the 焖煮 'lid').  Rises with the
            # casting mass (density): a heavy iron block smothers hard, a light
            # alloy one less (and rings brighter through the thinner walls).
            self._blk_seal = min(max(0.52 * (brho / 2700.0) ** 0.22, 0.36), 0.70)
            # (#2) HIGH-ORDER STANDING-WAVE WHINE: the odd harmonics of the pipe's
            # quarter-wave that fall in 3-7 kHz ARE the whine.  Their sharpness (Q)
            # scales with the pipe's length/diameter ratio — a long, thin, small-
            # bore system (LFA) gives a high-Q soprano scream; a short fat-bore one
            # gives a broad roar; a big lazy bore gives almost none.  Centre freqs
            # are recomputed each block from the live sound speed (they drift with
            # revs/heat).  We store the (odd) harmonic orders to hit ~3.5/5/6.5 kHz.
            L_tot = max(eng.exhaust_total_m, 0.5)
            d_pipe = 2.0 * r
            self._whine_ld = L_tot / d_pipe                  # length / diameter (-> Q)
            # Whine PROMINENCE is what an engine's scream really tracks: how high it
            # revs (the firing harmonics reach the whine band), how thin the bore is
            # (high Q), and how open the system is (un-muffled).  Driving it purely
            # off length/diameter mis-fired — it left every high-revving exotic flat
            # and gave a short-pipe F1 car ZERO whine.  Redline is the lead term.
            rev = min(max((eng.redline_rpm - 6500.0) / 3500.0, 0.0), 1.25)
            bore = min(max((0.028 - r) / 0.011, 0.0), 1.0)
            self._whine_amt = min(rev * (0.55 + 0.30 * bore
                                         + 0.25 * eng.exhaust_openness), 1.1)
            # HOT-V: turbos sit in the valley right off short, equal-length, merged
            # runners -> they swallow the header rasp / standing-wave whine and
            # deepen + smooth the note (the AMG/BMW twin-turbo woofle).
            self._hot_v = bool(getattr(eng, "hot_v", False))
            if self._hot_v:
                self._whine_amt *= 0.42
            fqw0 = 540.0 / (4.0 * L_tot)                     # quarter-wave, nominal c
            self._whine_orders = []
            for target in (3500.0, 5000.0, 6500.0):
                n = max(1, int(round(target / fqw0)))
                if n % 2 == 0:
                    n += 1                                   # odd harmonics only
                self._whine_orders.append(n)
            self._whine_zi = [np.zeros(2) for _ in self._whine_orders]
            # DISPLACEMENT THUNDER: a big cylinder shoves a big slug of gas, so it
            # has a deep low-end ROAR under the note — the thing a Ferrari V12 has
            # in the real world that pure scream lacks.  Low-shelf gain scales with
            # litres-per-cylinder (a 0.5 L+ cyl thunders, a 0.25 L screamer barely).
            cyl_l = (eng.total_displacement * 1000.0) / max(eng.num_cylinders, 1)
            g_thunder = min(max((cyl_l - 0.30) * 12.0, 0.0), 7.5)
            self._thunder = (_peaking(78.0, 0.5, g_thunder, sr)
                             if g_thunder > 0.1 else None)
            self._thunder_zi = np.zeros(2)
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
        # WIDENED so the real per-car exhaust openness (a hardware CHOICE: an
        # open race system vs a restrictive stock one) is clearly audible — a
        # restrictive pipe rings much less (damped, 'plastic'), an open one holds
        # a long metallic ring.  Was 0.84+0.15x (too narrow -> voices samey).
        # ...plus the WALL MATERIAL's damping: a low-loss pipe (titanium) absorbs
        # less of the gas wave into its walls, so the whole exhaust resonance
        # rings LONGER (the metallic 'sing'); cast iron soaks it up (dead).  This
        # is why the pipe material changes the note so much, not just a formant.
        qf = getattr(self, "_wall_q", 1.0)
        g = min(0.80 + 0.20 * eng.exhaust_openness + 0.035 * (qf - 1.0), 0.994)

        # VARIABLE EXHAUST VALVE: it opens with rpm + throttle.  Closed (idle /
        # light load) the gas takes the long muffled path -> dark, bassy, lumpy;
        # wide open (high rpm / hard throttle) it's a short straight pipe ->
        # bright, screaming.  This rpm-dependent brightness is the whole reason a
        # low idle sounds nothing like a redline pull.
        rpm_frac = min(self.sim.rpm / max(eng.redline_rpm, 1.0), 1.0)
        # mostly rpm-driven (throttle just nudges it open a bit)
        drive = min(rpm_frac + 0.30 * min(max(self.sim.throttle, 0.0), 1.0), 1.0)
        valve = min(max((drive - 0.28) / 0.45, 0.0), 1.0)
        # NONLINEAR opening curve (was linear): a real flap/gas-path brightens
        # slowly off idle then rushes open up top — and loudness perception is
        # log, so the linear map made idle and redline sound like the same
        # brightness with a volume knob.  ^1.4 keeps the low end darker longer
        # and steepens the top -> far more idle-vs-redline contrast.
        valve = valve ** 1.4
        self._valve = valve
        self._post_fc = 1600.0 + 9600.0 * valve     # muffled 1.6 kHz .. bright 11 kHz

        # MEAN EXHAUST FLOW (0..1) — drives the TURBULENT (v^2) nonlinearities:
        # pipe losses, wave steepening and backflow burble all scale with it.
        flow = rpm_frac * (0.30 + 0.70 * min(max(self.sim.throttle, 0.0), 1.0))
        self._flow = flow

        # in-loop treble damping, also scaled shut by the valve
        fc = (1200.0 + 8600.0 * eng.exhaust_openness) * (0.4 + 0.6 * valve)
        # TURBULENT wall/radiation loss grows with flow SQUARED (laminar at idle,
        # scrubbing at WOT): the resonator's Q and its top end fall away as flow
        # rises, so idle rings clean and hollow while a redline pull gets rough
        # and gritty instead of politely ringing — losses were previously static.
        fc *= 1.0 - 0.22 * flow * flow
        g = g * (1.0 - 0.09 * flow * flow)      # feedback gain: same v^2 loss
        # a rotary 'braps' brighter and raspier than a piston engine
        if eng.is_rotary:
            self._post_fc *= 1.35
            fc *= 1.4
        # VTEC / VVT high-lift cam CROSSOVER -> an audible step: above the crossover
        # rpm the aggressive cam piles on lift + overlap, so the note jumps brighter
        # and raspier ("VTEC kicks in").  variable_valve is display-only on the
        # Engine; we read the same field here to colour the sound.
        self._vtec = 0.0
        vl = getattr(eng, "valve_lift", "fixed")
        if vl != "fixed":
            # The audible 'kick' now rides the SAME crossover as the white-box VE
            # STEP (ve_model._cam_params): a two-stage lift SWITCH (VTEC/AVS/MIVEC)
            # steps hard AT vtec_rpm, a continuous phasing system (VANOS/VVT-i/
            # Valvetronic) just brightens gently — so the sound follows the physical
            # breathing change instead of a separate hand-set rpm.
            step = 1.0 if vl == "two-stage" else 0.22
            xf = (getattr(eng, "vtec_rpm", 0.0) / max(eng.redline_rpm, 1.0)) or 0.62
            self._vtec = min(max((rpm_frac - xf) / 0.06, 0.0), 1.0)
            self._post_fc *= 1.0 + 0.30 * step * self._vtec
            fc *= 1.0 + 0.26 * step * self._vtec
        # tail-pipe TIP mouth: a big bore brightens the exit, a small one darkens it
        # (tip_scale == 1.0 is neutral so existing presets are unchanged).
        self._post_fc *= 0.70 + 0.30 * min(max(getattr(eng, "tip_scale", 1.0), 0.3), 2.0)
        # --- extra detail models (all neutral at their defaults) -----------------
        # CAM profile: a big/race cam rasps up top and chops at idle (overlap);
        # a mild cam is calm.
        cam = getattr(eng, "cam_profile", "stock")
        self._post_fc *= 1.0 + {"mild": -0.06, "hot": 0.12, "race": 0.22}.get(cam, 0.0)
        self._cam_lump = ({"hot": 0.16, "race": 0.28}.get(cam, 0.0)
                          * max(1.0 - rpm_frac * 2.2, 0.0))   # lopey idle only
        # INTEGRATED (in-head) exhaust manifold: short, hot, buried -> tighter & a
        # touch more muffled than an external cast/tubular manifold.
        if getattr(eng, "integrated_manifold", False):
            self._post_fc *= 0.93
            fc *= 0.92
        # CONTINUOUS variable lift (Valvetronic / MultiAir): throttleless, smoother.
        if getattr(eng, "valve_lift", "fixed") == "continuous":
            self._post_fc *= 0.97
        # INJECTION clatter — WHITE-BOX from the fuel-rail PRESSURE: an injector
        # needle slamming shut against a high rail clicks hard and bright, a
        # low-pressure port injector is inaudible.  Click energy ~ P^0.3 (impulse
        # of the needle stopping).  Rail pressures (bar): port MPI ~4, D-4S dual
        # ~130 (the direct side), GDI direct ~200, piezo GDI ~350, diesel common-
        # rail ~2000.  Carb / mechanical race injection have no solenoid -> none.
        p_rail = _INJ_PRESSURE.get(getattr(eng, "injection", "port"), 0.0)
        amt = 0.075 * (p_rail / 200.0) ** 0.3 if p_rail > 0.0 else 0.0
        self._inj_amt = amt * max(1.0 - rpm_frac * 1.25, 0.22)
        # BALANCE-SHAFT roughness: an I3 / I4 / 90deg-V6 with NO balance shaft buzzes.
        self._balance_rough = 0.0
        if (eng.num_cylinders in (3, 4) and not eng.is_rotary
                and not getattr(eng, "balance_shaft", False)):
            self._balance_rough = 0.06 * max(1.0 - rpm_frac * 1.6, 0.15)
        # wobble rate for the cam chop / balance buzz ~ the firing frequency
        fire_hz = max(self.sim.rpm, 1.0) / 120.0 * eng.num_cylinders   # fires/sec / 2
        self._wob_w = 2.0 * math.pi * fire_hz / self.sample_rate
        # 2-valve heads breathe worse up top -> a touch darker than 4-valve
        if eng.valves_per_cyl <= 2:
            self._post_fc *= 0.82
        # exhaust after-treatment: a cat muffles a little, a GPF a lot
        if self.cat:
            self._post_fc *= 0.85
        if self.gpf:
            self._post_fc *= 0.6
            fc *= 0.75
        # OPEN-END RADIATION LOSS, frequency-aware: an unflanged pipe end
        # reflects LESS as ka grows (|R| ~ 1 - (ka)^2/2) — the wave increasingly
        # ESCAPES instead of re-ringing the comb.  Evaluated at the firing
        # fundamental (the spectrum's energy centroid): negligible for a road
        # car (ka ~ 0.03 at idle), decisive for an F1 firing at 1.5 kHz
        # (ka ~ 0.5) — which is exactly why a real F1 sounds DRY and ripping,
        # not like a ringing organ pipe.
        fire_hz = max(self.sim.rpm, 1.0) / 120.0 * eng.num_cylinders
        ka = 2.0 * math.pi * fire_hz * eng.exhaust_radius_m / c
        g *= max(1.0 - 0.5 * ka * ka, 0.55)

        lp_a = math.exp(-2 * math.pi * fc / sr)
        # Helmholtz muffler/chamber resonance
        A, V = eng.muffler_neck_area_m2, eng.muffler_volume_m3
        r_neck = math.sqrt(A / math.pi)
        l_h = eng.muffler_neck_len_m + 1.7 * r_neck
        f_helm = (c / (2 * math.pi)) * math.sqrt(A / (V * l_h))
        f_helm = min(max(f_helm, 40.0), 400.0)
        return D1, D2, g, lp_a, f_helm

    # ------------------------------------------------------- synthesis core
    def _tap(self, name: str, sig: np.ndarray) -> None:
        """Snapshot a decimated copy of one exhaust-stage waveform for the UI's
        per-stage refresh scopes.  No-op unless the scope overlay is open."""
        if not self.scope_enabled or sig is None or not len(sig):
            return
        step = max(1, len(sig) // 96)
        self._stage_taps[name] = sig[::step][:96].astype(np.float64).copy()

    def _pk(self, f0, Q, gain_db):
        """Cached peaking biquad — IIR design is expensive, so memoise by rounded
        params (the centres/gains slide slowly, so this hits the cache almost
        every block instead of redesigning ~17 filters per 256-frame block)."""
        key = ('pk', int(f0 / 8.0), int(Q * 10.0), int(round(gain_db * 4.0)))
        ba = self._fcache.get(key)
        if ba is None:
            if len(self._fcache) > 800:
                self._fcache.clear()
            ba = self._fcache[key] = _peaking(max(f0, 20.0), max(Q, 0.05),
                                              gain_db, self.sample_rate)
        return ba

    def _bw(self, order, fc, btype='low'):
        """Cached Butterworth design (see _pk)."""
        sr = self.sample_rate
        fc = min(max(fc, 20.0), sr * 0.49)
        key = ('bw', order, int(fc / 8.0), btype)
        ba = self._fcache.get(key)
        if ba is None:
            if len(self._fcache) > 800:
                self._fcache.clear()
            ba = self._fcache[key] = butter(order, fc / (sr / 2), btype=btype)
        return ba

    def _render_block(self, frames: int) -> np.ndarray:
        sim = self.sim
        omega = sim.omega
        # time_scale < 1 = slow motion (the whole engine note slows + drops)
        dps = math.degrees(omega) / self.sample_rate * self.time_scale
        # (Step 5) cold-start timbre now reads the REAL coolant temperature from
        # the physics' thermal model (was a private 8-second timer): the note
        # stays dark until the engine has actually warmed through, and re-cools
        # with the block when parked.  Falls back to the old timer if absent.
        dt_blk = frames / self.sample_rate
        cool = getattr(sim, "coolant_c", None)
        if cool is not None:
            self._cold = min(max((70.0 - cool) / 50.0, 0.0), 1.0)
        elif sim.ignition_on and sim.rpm > 300.0:
            self._cold = max(0.0, self._cold - dt_blk / 8.0)
        else:
            self._cold = min(1.0, self._cold + dt_blk / 40.0)

        D1, D2, g, lp_a, f_helm = self._resonance_params()
        s = -1.0    # inverting open-end reflection -> odd-harmonic quarter wave
        # live hot-gas sound speed (~470-670 m/s, climbs with rpm/load) -> the
        # runner-delay interference pattern shifts slightly as the engine heats.
        c_runner = max(sim.exhaust_sound_speed(), 300.0)

        # --- per-channel excitation, sampled from the physics ---------------
        chans = [np.zeros(frames, dtype=np.float64) for _ in range(self._nchan)]
        fizz_chans = [np.zeros(frames, dtype=np.float64) for _ in range(self._nchan)]
        choke = 0.0          # exhaust-valve choked-flow factor (0 subsonic .. 1 choked)
        if dps > 1e-12:
            idx = np.arange(frames)
            crank = self._audio_crank + dps * idx
            p_open = sim.blowdown_pressure() - 1.05 * P_ATM
            strength = math.copysign(math.sqrt(abs(p_open)), p_open) / math.sqrt(6 * P_ATM)
            # load 0..1 from the cylinder pressure at valve-open: drives how steep
            # and tall the blowdown edge is (high load = sharper edge = more scream).
            load = min(max(abs(strength) * 1.25, 0.08), 1.0)
            # CHOKED-FLOW orifice (standard IC-engine compressible discharge): once
            # the exhaust/cylinder pressure ratio drops below the critical ~0.54
            # (gamma~1.33 hot gas), the blowdown jet goes sonic and the flow
            # SATURATES — the pulse top is physically clipped and steepened, which
            # is where a hard-driven engine's high-order "tear/grit" harmonics come
            # from (vs. a clean louder pulse).  One scalar/ frame, no per-sample cost.
            p_cyl = p_open + 1.05 * P_ATM                  # absolute blowdown pressure
            pr = (1.05 * P_ATM) / max(p_cyl, 1.05 * P_ATM)   # back/cylinder ratio (0,1]
            choke = min(max((0.54 - pr) / 0.54, 0.0), 1.0)   # 0 subsonic .. 1 choked
            self._jit += (1.0 + 0.12 * (self._rng.random(len(self._jit)) - 0.5)
                          - self._jit) * 0.25

            # Cylinder spread ~3x stronger than before, and bigger still at low
            # rpm (valve shut), where the spaced pops make each cylinder's own
            # character clearly audible -> coarse, grainy low-rpm lumpiness.
            spread = self.params["cyl_spread"] * (1.0 + 1.4 * (1.0 - self._valve))
            # Blowdown decay from the cylinder's real STROKE (WHITE-BOX, not an
            # EQ): the exhaust-valve blowdown empties a gas column whose height is
            # the stroke, so the characteristic emptying time ~ stroke / c.  A
            # long-stroke engine (2JZ 86 mm, a diesel) therefore fires a longer,
            # DEEPER pulse; a short-stroke high-revver (RB26 74 mm, an F1) a sharp
            # bright one.  This is what makes displacement/bore-stroke audible —
            # the pulse SHAPE carries it, straight from geometry.
            base_tau = self.params["pulse_tau"] * (self._stroke_ref / 0.083)
            # deterministic per-cylinder voicing (geometry-derived, no random); the
            # switch scales the deviation from 1.0, so cv=0 is perfectly uniform.
            voice = self._cyl_voice
            cv = self.params.get("cyl_voice", 1.0)
            use_voice = voice is not None and cv > 1e-3
            for j, off in enumerate(self._offsets):
                # this cylinder's own decay (pitch) and loudness
                tau_j = base_tau * max(1.0 + 0.95 * spread * self._cyl_tau[j], 0.35)
                amp_j = self._jit[j] * max(1.0 + 0.55 * spread * self._cyl_amp[j], 0.1)
                edge_j = 1.0
                if use_voice:                                # geometry voicing
                    amp_j *= 1.0 + (voice.amp[j] - 1.0) * cv
                    edge_j = 1.0 + (voice.edge[j] - 1.0) * cv
                # PHYSICS-DRIVEN per-cylinder amplitude: each pulse is scaled by
                # the blowdown pressure the physics CAPTURED as this cylinder's
                # exhaust valve actually opened.  A soft-limiter-cut cylinder only
                # pumped air (no burn -> ~1/k the pressure), so it goes quiet by
                # thermodynamics — the limiter "brap" and any per-cylinder
                # imbalance fall out of the simulation instead of a special case.
                lb = getattr(sim, "last_blowdown", None)
                if lb is not None and j < len(lb):
                    rel = lb[j] / max(p_open + 1.05 * P_ATM, 2.0 * P_ATM)
                    amp_j *= min(max(rel, 0.06), 1.5) ** 0.8
                phi = np.mod(crank + off + self._header_offset[j], 720.0)
                d = phi - VALVE_OPEN
                inwin = (phi >= VALVE_OPEN) & (phi <= VALVE_CLOSE)
                dd = np.clip(d, 0.0, None)
                # Two-stage exhaust pulse instead of one flat blat:
                # (1) BLOWDOWN — the valve cracks and the still-high cylinder
                #   pressure bursts out as a HARD edge that rises in just a couple
                #   of audio SAMPLES (a TIME, not a fixed crank angle).  At high
                #   load the edge is sharper and taller, so the metallic HF scream
                #   grows straight from the SOURCE and tracks the throttle — never a
                #   global treble boost (which would just hiss).
                rise_deg = max((2.0 + 4.0 * (1.0 - load)) * dps * edge_j, 1e-4)
                hard = np.clip(d / rise_deg, 0.0, 1.0)        # linear hard edge
                # blowdown decay from the GAS-DYNAMIC emptying rate (was a fixed
                # 0.30·tau): a sharp-emptying cylinder (F1) rings a shorter, peakier
                # snap; a slow one (diesel) a longer, softer swell.  Anchored at
                # sharp=1 (the reference) so that car's decay is unchanged.
                tau_blow = max(0.30 * tau_j / self._bd_sharp ** 0.85, 2.5)
                blow = (0.7 + 1.0 * load) * hard * np.exp(-dd / tau_blow)
                # (2) DISPLACEMENT — the rising piston then pushes the rest out: a
                #   soft, broad, lower, later hump (the body / low end).
                soft = np.clip(d / self.params["attack_deg"], 0.0, 1.0)
                soft = 0.5 - 0.5 * np.cos(soft * math.pi)
                tau_disp = tau_j * 1.5
                disp = soft * (1.0 - np.exp(-dd / (0.5 * tau_j))) * np.exp(-dd / tau_disp)
                close = np.clip((VALVE_CLOSE - phi) / 18.0, 0.0, 1.0)
                # gas-dynamic blow/displacement SPLIT: a sharp-emptying cylinder puts
                # more of its energy into the choked blowdown snap and less into the
                # slow piston-push hump (peaky vs woofy).  Anchored at sharp=1 ->
                # exactly (blow + 0.7·disp), the reference's balance.
                pk = self._bd_sharp
                pulse = np.where(inwin,
                                 ((0.78 + 0.22 * pk) * blow
                                  + 0.7 * (1.22 - 0.22 * pk) * disp)
                                 * close * amp_j, 0.0)
                # per-cylinder runner HF damping (longer/thinner runner = duller)
                if use_voice:
                    pulse = voice.damp(j, pulse)
                # delay this cylinder's pulse down its own runner (length / live
                # sound speed) before it merges at the collector -> interference.
                d_samp = self._runner_len[j] / c_runner * self.sample_rate
                pulse = self._runner_dl[j].process(pulse, d_samp)
                chans[self._channel_of[j]] += pulse

            # Separate the clean 'bang' (tonal pulse) from the 'fizz' (gas-rush
            # noise gated by the pulse), so each gets its OWN mixer slider.
            for ci in range(self._nchan):
                e = chans[ci] * strength
                noise = self._rng.standard_normal(frames)
                chans[ci] = 0.55 * e                      # clean bang -> pipe + dry
                # fizz = gas-rush noise GATED by the pulse `e` (this is the GOOD,
                # per-firing fizz — restored via a higher `turbulence`).  The
                # UNGATED floor stays tiny (0.008): a constant hiss between pulses
                # is the dyno-cell tell (白噪音), the gated fizz is the car.
                fizz_chans[ci] = e * noise + 0.008 * noise
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
        # choked blowdown adds saturation/harmonics at the SOURCE — only at high
        # load (choke>0), so idle/cruise stay clean; this is the physical origin of
        # the coarse "tear" a turbo/NA engine gets when you bury the throttle.
        drive = P["drive"] + 1.6 * fg + 0.5 * choke
        if drive > 1e-3:
            bang = np.tanh(bang * (1.0 + 7.0 * drive))
        if _HAVE_SCIPY and fw > 0.02:                    # low-shelf 'weight'
            b, a = self._pk(110.0, 0.6, 10.0 * fw)
            bang, self._fire_low_zi = lfilter(b, a, bang, zi=self._fire_low_zi)

        # separated fizz (own slider)
        fizz = np.zeros(frames, dtype=np.float64)
        for ci in range(self._nchan):
            fizz += fizz_chans[ci]

        # --- STRUCTURE-BORNE ENCLOSURE ('焖煮' — the lid on the pot) -----------
        # The combustion is SEALED inside the block + head + piston, so you never
        # hear the raw open detonation.  The whole in-cylinder EVENT — the thump
        # AND its turbulent gas-rush fizz — is MUFFLED behind the wall mass (a
        # mass-law low-pass, the 'lid') and rung at the casting's structural
        # resonances; only the pipe resonance `wet` stays bright, because THAT
        # actually leaves through the open tailpipe.  Applied IN PLACE (not an
        # added echo) so the raw open top end is genuinely SMOTHERED: a heavier
        # block seals harder & darker, a light alloy less & brighter (the air-
        # cooled 'clatter').  This is what stops it sounding like combustion out
        # in the open air.
        combustion = bang + P["turbulence"] * (fizz * inv)
        if _HAVE_SCIPY and getattr(self, "_blk_seal", 0.0) > 0.0:
            st, self._blk_lp_zi = lfilter(self._blk_lp[0], self._blk_lp[1],
                                          combustion, zi=self._blk_lp_zi)  # the lid
            b1, a1 = self._pk(self._blk_f1, self._blk_q, 5.0)
            st, self._blk1_zi = lfilter(b1, a1, st, zi=self._blk1_zi)      # block ring
            b2, a2 = self._pk(self._blk_f2, self._blk_q * 0.8, 3.0)
            st, self._blk2_zi = lfilter(b2, a2, st, zi=self._blk2_zi)
            combustion = (1.0 - self._blk_seal) * combustion + self._blk_seal * st
        self._tap("block", combustion)        # sealed in-cylinder combustion event
        # keep a decimated copy of the REAL combustion voice for the analyzer's
        # 'firing pulses' scope — the actual non-linear waveform (tanh-saturated
        # bang, sharp blowdown edges, gated fizz) instead of an idealised hump.
        if frames:
            cstep = max(1, frames // 64)
            self.last_combustion = combustion[::cstep][:64].astype(np.float64).copy()
        sig = combustion + wet                # + the OPEN pipe resonance (tailpipe)

        # --- TURBULENT BACKFLOW (湍流回涌): on the overrun the mean flow
        # collapses and the REFLECTED wave dominates at the collector — shear
        # between the returning and residual gas makes pulse-synchronous chuffs.
        # Model: noise MULTIPLIED by the wet (reflected) envelope — a genuinely
        # nonlinear self-modulation, so the burble breathes with the pipe instead
        # of being a steady hiss.  Only computed off-throttle (zero cost on power).
        ov = (max(0.0, 1.0 - min(max(sim.throttle, 0.0), 1.0) * 6.0)   # foot off
              * min(getattr(self, "_flow", 0.0) * 4.0, 1.0)           # revs up
              * min(sim.rpm / max(sim.engine.idle_rpm * 1.5, 1.0), 1.0))  # not idling
        if ov > 0.03 and dps > 1e-12:
            nz_b = self._rng.standard_normal(frames)
            # DARK chuff, not bright hiss: real overrun backflow is low-frequency
            # air pumping.  A cheap 1-pole low-pass (running mean of the noise)
            # drops the harsh treble that read as "air noise covering the engine".
            nz_b = 0.5 * (nz_b + np.concatenate(([self._burble_prev], nz_b[:-1])))
            self._burble_prev = float(nz_b[-1])
            sig = sig + (0.22 * ov) * np.abs(wet) * nz_b
        # --- F1 / race-engine HIGH-RPM REGIME --------------------------------
        # Above ~600 fires/second the discrete blowdown pulses are only ~50
        # samples apart: they physically merge into a continuous tone, and the
        # per-pulse model degenerates — the reason an F1 car sounded nothing
        # like one.  Crossfade in a harmonic STACK at the firing frequency (the
        # scream IS its harmonic series at these speeds), keeping the pulse
        # model underneath for body.  Gated to genuine screamers (redline >=
        # 11 krpm) so ordinary engines are untouched.
        if dps > 1e-12 and sim.engine.redline_rpm >= 11000.0:
            fire_hz = sim.rpm * len(self._offsets) / 120.0
            if fire_hz > 500.0:
                m = min((fire_hz - 500.0) / 600.0, 1.0)
                # PHYSICS-SWEPT harmonic gains (the DDSP-without-NN move): each
                # harmonic's level = the blowdown source spectrum (~1/k) times
                # |H| of the ACTUAL exhaust waveguide at that frequency — the
                # closed form of the feedback comb we synthesize with:
                #     |H(w)| = 1 / sqrt(1 + 2 g_e cos(wD) + g_e^2),
                # g_e = g * |one-pole LP|.  The stack now rings exactly where
                # this car's pipe resonates instead of a hand-rolled rolloff.
                harms = []
                for hk in range(1, 11):
                    f = hk * fire_hz
                    if f >= self.sample_rate * 0.45:
                        break
                    w = 2.0 * math.pi * f / self.sample_rate
                    lp_mag = (1.0 - lp_a) / math.sqrt(
                        1.0 - 2.0 * lp_a * math.cos(w) + lp_a * lp_a)
                    g_e = min(g * lp_mag, 0.995)
                    comb = 1.0 / math.sqrt(1.0 + 2.0 * g_e * math.cos(w * D1)
                                           + g_e * g_e)
                    # A powerful brass-like scream = a STRONG fundamental anchoring
                    # a SMOOTH harmonic rolloff.  The 'broken trumpet' was a weak
                    # fundamental (6%!) with energy scattered into harsh high
                    # partials — so drive a dominant fundamental + exponential
                    # rolloff (0.66^k) and let the pipe comb only lightly COLOUR it
                    # (^0.35), instead of a sharp comb resonance hollowing out the
                    # core.  High AND massive (高亢澎湃有力), not a thin buzz.
                    a = comb ** 0.35 * 0.66 ** (hk - 1)
                    if hk == 1:
                        a *= 1.45              # firm the anchoring fundamental note
                    harms.append((hk, a))
                if harms:
                    srms = math.sqrt(sum(a * a for _, a in harms) * 0.5) or 1.0
                    harms = [(hk, a / srms) for hk, a in harms]   # unit-RMS stack
                    scream = self._whine(fire_hz, frames, harms,
                                         phase_attr="_scream_phase")
                    # grit: a real open-exhaust screamer is nonlinear — saturate HARD
                    # for the raspy, torn power (dense odd harmonics), not a pure sine.
                    scream = np.tanh(scream * (1.7 + 1.8 * load)) * 0.8
                    # ---- DE-BUZZ: a pure periodic sine stack IS an electric-drill /
                    # mosquito buzz.  A real F1 is a VIOLENT, TEXTURED, aperiodic
                    # scream, so ride the tone with:
                    #  (1) broadband TEARING noise (the shredding air at 1200-1500
                    #      fires/s), GATED by the tone envelope so it tears WITH the
                    #      firing instead of being a flat hiss;
                    envn = np.abs(scream)
                    envn = envn / (float(envn.max()) + 1e-9)
                    tn = self._rng.standard_normal(frames)
                    if _HAVE_SCIPY:
                        tn, self._f1tear_zi = lfilter(self._f1tear_bp[0],
                                                      self._f1tear_bp[1], tn,
                                                      zi=self._f1tear_zi)
                    else:
                        tn = np.diff(tn, prepend=tn[:1])       # bright-ish fallback
                    tear = tn * (0.35 + 0.65 * envn)           # gated by the tone
                    #  (2) a slow AMPLITUDE SHIMMER (cycle-to-cycle jitter) so it
                    #      breathes organically instead of buzzing electronically.
                    ctrl = self._rng.standard_normal(5)
                    jit = np.interp(np.linspace(0.0, 1.0, frames),
                                    np.linspace(0.0, 1.0, 5), ctrl)
                    scream = scream * (1.0 + 0.20 * m * jit) + (0.68 * m) * tear
                    # CROSSFADE the fused pulses onto the merged scream, but DUCK
                    # ONLY LIGHTLY — the combustion punch/body underneath IS the 有力
                    # a real F1 keeps.  (Was 0.62 = stripped it to a comical whistle.)
                    sig_rms = math.sqrt(float(np.mean(sig * sig)) + 1e-12)
                    duck = 0.32 * m
                    lvl = sig_rms * (1.10 + 0.50 * load) * m
                    sig = (1.0 - duck) * sig + lvl * scream
        # overrun pops/bangs are unburnt fuel igniting IN the exhaust, so they
        # enter HERE (at the header) and travel the whole pipe — cat, muffler,
        # wall, tail — instead of being bolted on at the tailpipe.  A stock car's
        # pops get muffled by the cat/box; an open race system keeps them sharp.
        sig = sig + self._overrun_pops(frames)

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

        self._tap("header", sig)              # exhaust gas at the header collector
        # --- FINITE-AMPLITUDE WAVE STEEPENING (nonlinear acoustics): a loud
        # pressure wave travels on its own compressed, hotter gas, so its crest
        # outruns its trough and the front STEEPENS as it goes down the pipe —
        # generating high harmonics in proportion to amplitude (the same physics
        # as a trumpet's brassy 'cuivre' snarl).  Quadratic self-distortion,
        # strength driven by mean flow + choked blowdown, so idle stays clean and
        # a hard pull turns raspy from the physics up.  One vector op.
        kst = 0.22 * getattr(self, "_flow", 0.0) + 0.30 * choke
        if kst > 0.01:
            sig = sig + kst * sig * np.abs(sig)
        # --- (3a) cylinder-head / exhaust-port cavity low-pass: round the raw
        # pulse so it reads as a metal port, not an electronic click.  A touch
        # duller while the engine is still cold.
        if _HAVE_SCIPY:
            if self._cold > 0.02:
                hc = min(11000.0 * (1.0 - 0.30 * self._cold), self.sample_rate * 0.45)
                bhd, ahd = self._bw(2, hc)
                sig, self._head_lp_zi = lfilter(bhd, ahd, sig, zi=self._head_lp_zi)
            else:
                sig, self._head_lp_zi = lfilter(self._head_lp[0], self._head_lp[1],
                                                sig, zi=self._head_lp_zi)
        self._tap("head/port", sig)

        # --- (3b) TURBINE damping: a turbo's hot-side wheel sits right in the
        # exhaust stream and smears the pressure pulses — so as BOOST climbs the
        # note gets muffled, woofy and "swallowed", losing the raw header edge.
        # This is the core reason a turbo car is NOT just "NA + a boost number".
        # Cutoff falls with boost; off-boost it's high (barely touches the sound).
        _eng = sim.engine
        if _eng.induction == "turbo" and _HAVE_SCIPY:
            bf = (min(sim.boost / max(_eng.boost_bar, 0.05), 1.0)
                  if _eng.boost_bar else 0.0)
            # TWO variables, not just boost: the turbine's LOADING (boost) smears and
            # muffles, while its SPEED (~exhaust mass-flow, tracked by rpm) keeps the
            # bright impeller whistle alive.  So high-rpm/low-boost stays sharp and
            # whistly, low-rpm/high-boost goes deep and dull — they no longer sound
            # identical.  Off-boost it barely touches the note at any rpm.
            rpm_frac = min(sim.rpm / max(_eng.redline_rpm, 1.0), 1.0)
            # eased (2026-07): the turbine muffled turbo cars so hard they lost
            # their combustion fizz/grit (r35 etc.).  Less boost pull-down + a
            # higher floor keeps the dry rasp while still darkening on boost.
            tcut = 9000.0 - 4800.0 * bf + 2200.0 * rpm_frac
            tcut = min(max(tcut, 3400.0), 11500.0)   # ~3.4-6.4k boosted .. 9-11k off
            b, a = self._bw(2, tcut)
            pre_turbine = sig                        # tap BEFORE the turbine wheel
            sig, self._turbine_zi = lfilter(b, a, sig, zi=self._turbine_zi)
            # WASTEGATE BYPASS: once boost reaches its target the wastegate cracks
            # open and part of the exhaust skips the turbine entirely — that gas
            # keeps its raw, bright pulse edge.  Crossfade a slice of the
            # pre-turbine signal back in as the gate opens, so FULL boost gains a
            # hard raspy layer instead of only sinking deeper into the muffle
            # (real cars get angrier at peak boost, not woollier).  Scalar mix per
            # block; zero extra filter state.
            wg = min(max((bf - 0.78) / 0.22, 0.0), 1.0)   # gate opens ~78% -> 100%
            m = 0.28 * wg
            if m > 1e-3:
                sig = (1.0 - m) * sig + m * pre_turbine
            # EXTERNAL WASTEGATE (screamer pipe): an atmospheric-vent gate SCREECHES
            # as it cracks — a hard, bright, saturated chatter (the rally/drift
            # 'BREE'), unlike the recirculated internal gate's quiet rasp.  Driven
            # by the same opening `wg`, band-passed to a bright dump formant and
            # tanh-clipped for the metallic edge.
            if getattr(_eng, "wastegate", "internal") == "external" and wg > 0.02:
                wn = self._rng.standard_normal(frames)
                bws, aws = self._pk(3200.0, 1.4, 9.0)
                wn, self._wgate_zi = lfilter(bws, aws, wn, zi=self._wgate_zi)
                sig = sig + (0.16 * wg) * np.tanh(wn * 3.0)
        self._tap("head/port", sig)

        # --- (4) catalytic converter: the ceramic honeycomb soaks up the raw
        # straight-pipe top end FIRST, upstream of the muffler — a stock car with
        # a cat can't sound like an open header no matter what the muffler does.
        if self.road_pipe and _HAVE_SCIPY:
            sig, self._road_lp_zi = lfilter(self._road_lp[0], self._road_lp[1],
                                            sig, zi=self._road_lp_zi)
            sig, self._road_sh_zi = lfilter(self._road_sh[0], self._road_sh[1],
                                            sig, zi=self._road_sh_zi)
        # --- (4a) GASOLINE PARTICULATE FILTER: a fine wall-flow ceramic packs the
        # stream far tighter than a cat — a strong BROADBAND absorptive soak that
        # dulls the rasp and 'chokes' the note (the muffled, restrained sound a
        # modern EU-emissions car has vs. its pre-GPF self).  Upstream of the muffler.
        if self.gpf and _HAVE_SCIPY:
            bg, ag = self._bw(1, min(4500.0, self.sample_rate * 0.45))
            sig, self._gpf_lp_zi = lfilter(bg, ag, sig, zi=self._gpf_lp_zi)
        self._tap("catalytic", sig)

        # --- (4b) main-pipe HIGH-ORDER STANDING-WAVE WHINE ------------------
        # The odd quarter-wave harmonics in 3-7 kHz, rung as resonant peaks whose
        # Q scales with the pipe's length/diameter (thin small bore = sharp soprano
        # scream, fat bore = broad roar / none).  Grows with the valve opening, so
        # the whine climbs in with revs.  Centre freqs follow the live sound speed.
        if _HAVE_SCIPY and self._whine_amt > 0.02:
            f_qw = c_runner / (4.0 * max(sim.engine.exhaust_total_m, 0.5))
            wamt = self._whine_amt * (0.25 + 0.75 * self._valve)
            Qbase = min(2.0 + 0.16 * self._whine_ld, 14.0)
            for k, n in enumerate(self._whine_orders):
                fc = f_qw * n
                if 2400.0 < fc < min(8000.0, self.sample_rate * 0.45):
                    Q = min(Qbase * math.sqrt(1.0 + 0.10 * n), 22.0)
                    gain = (5.5 - 1.3 * k) * wamt * P.get("whine", 1.0)
                    bw, aw = self._pk(fc, Q, gain)
                    sig, self._whine_zi[k] = lfilter(bw, aw, sig, zi=self._whine_zi[k])
        self._tap("standing-wave", sig)

        # --- (5+6) resonator + muffler: DC-block, de-drone notch, valve roll-off
        if _HAVE_SCIPY:
            sig, self._hp_zi = lfilter(self._hp[0], self._hp[1], sig, zi=self._hp_zi)
            # active-exhaust-valve BYPASS tap: this bright, un-muffled signal is
            # crossfaded back in as the flap cracks open with rpm (below) — the
            # straight-through path around the muffler.
            bypass = sig
            # (5) resonator: Helmholtz side-branch used as a NOTCH (Akrapovic-style:
            # remove the drone boom, do not add another resonance).  Depth WHITE-BOX
            # from the system: a quiet, closed ROAD exhaust runs a big de-drone
            # resonator (deep notch); an OPEN race system barely any.  Anchored so a
            # typical openness (~0.7) reproduces the known-good ~ -4 dB.
            _op = min(max(sim.engine.exhaust_openness, 0.2), 1.0)
            res_depth = -(2.0 + 7.0 * (1.0 - _op))       # -2 dB open .. -7.6 dB closed
            bH, aH = self._pk(f_helm, 1.4, res_depth)
            sig, self._helm_zi = lfilter(bH, aH, sig, zi=self._helm_zi)
            self._tap("resonator", sig)       # Helmholtz de-drone notch
            # (6) muffler: variable-valve expansion low-pass — muffled at idle,
            # wide open at redline.
            sr = self.sample_rate
            cutoff = min(self._post_fc, sr * 0.45)
            blp, alp = self._bw(2, cutoff)
            sig, self._lp_zi = lfilter(blp, alp, sig, zi=self._lp_zi)
            # ...and its expansion-chamber low-end body when the valve is shut.
            if self._valve < 0.75:
                bL, aL = self._pk(110.0, 0.6, (1.0 - self._valve) * 7.0)
                sig, self._lowboost_zi = lfilter(bL, aL, sig, zi=self._lowboost_zi)
            # (6b) muffler internal reflections: two short feed-forward comb taps
            # (expansion chamber + baffle paths) -> periodic notches = the muffler's
            # own colour, not just a low-pass.  Stronger in a packed/quiet box, light
            # on an open system.
            # Muffler construction: a REFLECTIVE (chambered/baffled) box rings the
            # comb notches and drones; an ABSORPTIVE (straight-through, fibre-packed)
            # one barely combs but soaks the high end broadband -> smooth & open.
            absorptive = getattr(sim.engine, "muffler_type", "reflective") == "absorptive"
            mcomb = (1.0 - sim.engine.exhaust_openness) * (0.3 if absorptive else 1.0)
            if mcomb > 0.05:
                d1 = self._muff_len[0] / c_runner * sr
                d2 = self._muff_len[1] / c_runner * sr
                mg = mcomb * P.get("muffler", 1.0)
                sig = (sig + 0.32 * mg * self._muff_dl1.process(sig, d1)
                       + 0.22 * mg * self._muff_dl2.process(sig, d2))
            if absorptive and _HAVE_SCIPY:    # packed-fibre broadband HF absorption
                bA, aA = self._bw(1, min(6800.0, sr * 0.45))
                sig, self._absorb_zi = lfilter(bA, aA, sig, zi=self._absorb_zi)
            if getattr(sim.engine, "flex_pipe", False) and _HAVE_SCIPY:
                # corrugated flex section -> a buzzy mid resonance (the 'braaa' rasp)
                bf, af = self._pk(1650.0, 2.2, 4.0)
                sig, self._flex_zi = lfilter(bf, af, sig, zi=self._flex_zi)
            self._tap("muffler", sig)         # expansion low-pass + comb baffles
            # active exhaust valve: above ~40% redline the bypass flap cracks open
            # and the bright straight-through tap is crossfaded back in — the note
            # gets louder and opens up at the top end, exactly like a valved system.
            vo = min(max((self._valve - 0.40) / 0.5, 0.0), 1.0) * 0.5 * P.get("valve_open", 1.0)
            vo = min(vo, 0.85)
            if vo > 1e-3:
                sig = (1.0 - vo) * sig + vo * bypass
            # SPL-controlled "blow-out": when the engine is LOUD (hard on it) the box
            # can't hold the pressure, so a touch more of the bright pre-muffler
            # signal bleeds through -> an active-valve-like dynamic ("内敛 -> 炸开")
            # WITHOUT a real second path.  Reuses last block's output RMS (free); the
            # (1-vo) guard avoids double-counting an already-open valve.
            spl = min(getattr(self, "last_level", 0.0) * 3.0, 1.0)
            bl = 0.16 * spl * (1.0 - vo)
            if bl > 1e-3:
                sig = (1.0 - bl) * sig + bl * bypass
        else:
            sig = np.diff(sig, prepend=sig[:1])
        self._tap("valve bypass", sig)        # active-valve straight-through mix

        # --- intake / induction roar (the OTHER half a real car you hear) ---
        # Broadband 'sucking' noise through the airbox resonance, swelling with
        # throttle and rpm.  A separate path from the exhaust, cool-air tuned.
        if _HAVE_SCIPY and dps > 1e-12:
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            intake_gain = P["intake"] * sim.throttle * (0.25 + 0.75 * rpm_frac)
            # BOOST mass-flow: a forced-induction engine pumps FAR more air through
            # the intake (mass flow ~ MAP·rpm), so the induction roar swells with the
            # compressor's boosted charge — the whoosh a turbo/blower car has that an
            # NA one doesn't.  Scales with boost gauge / rated boost.
            bb = getattr(sim.engine, "boost_bar", 0.0)
            if bb > 0.0:
                intake_gain *= 1.0 + 1.1 * min(max(sim.boost, 0.0) / bb, 1.0)
            if intake_gain > 1e-4:
                n = self._rng.standard_normal(frames)
                n, self._intake_bp_zi = lfilter(self._intake_bp[0], self._intake_bp[1],
                                                n, zi=self._intake_bp_zi)
                n, self._intake_lp_zi = lfilter(self._intake_lp[0], self._intake_lp[1],
                                                n, zi=self._intake_lp_zi)
                sig = sig + intake_gain * n

        # --- INDIVIDUAL THROTTLE BODIES: the raw induction HOWL --------------
        # With one trumpet per cylinder, each intake stroke sucks a sharp tuned
        # pulse straight past the driver: a hard, brassy, harmonically-rich howl
        # at the firing frequency that RISES viciously with revs and throttle —
        # the RB26 / S65 / F1 / 4A-GE signature.  A single-plenum intake (2JZ,
        # 4G63, most road cars) has none of it, only the muffled roar above; so
        # this is exactly what tells those otherwise-similar engines apart.
        if getattr(sim.engine, "individual_throttle", False) and dps > 1e-12:
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            fire_hz = sim.rpm * len(self._offsets) / 120.0
            thr = min(max(sim.throttle, 0.0), 1.0)
            howl_gain = 0.16 * (0.15 + 0.85 * thr) * rpm_frac ** 1.5
            if howl_gain > 1e-4 and 20.0 < fire_hz < self.sample_rate * 0.4:
                howl = self._whine(
                    fire_hz, frames,
                    [(1, 1.0), (2, 0.85), (3, 0.6), (4, 0.42), (5, 0.28),
                     (6, 0.18), (8, 0.10)],
                    phase_attr="_itb_phase")
                sig = sig + howl_gain * howl

        # --- forced induction (blower whine / turbo whistle / BOV) + gearbox -
        if dps > 1e-12:
            ind, gw = self._induction_audio(frames)
            if _HAVE_SCIPY and P["spool_reverb"] > 1e-3:
                self._ind_reverb.mix = P["spool_reverb"]
                ind = self._ind_reverb.process(ind)
            if _HAVE_SCIPY and P["gearbox_reverb"] > 1e-3:
                self._gear_reverb.mix = P["gearbox_reverb"]
                gw = self._gear_reverb.process(gw)
            # LIFT-OFF DUCK: when the BOV/flutter fires the driver has just LIFTED —
            # the combustion roar collapses, so the dump 'TSSSH' / surge 'stu-tu-tu'
            # is what you actually HEAR.  Duck the engine note by the BOV envelope so
            # the valve event is EXPOSED instead of buried under the note (and the
            # AGC can no longer normalise it away to the same loudness as the note).
            duck = min(0.80 * getattr(self, "_bov_env", 0.0), 0.80)
            sig = (1.0 - duck) * sig + ind + gw
        self._tap("induction+gears", sig)     # + intake roar, turbo, gearbox whine

        # --- (7) tail-pipe wall thickness: kill the 'small-trumpet' shriek
        # WITHOUT losing low end.  The brass honk lives in a ~1.8 kHz formant —
        # scoop THAT band and add a touch of low-shelf body (thicker, not thinner).
        wt = P["wall_thickness"]
        if _HAVE_SCIPY and wt > 1e-3:
            b, a = self._pk(1850.0, 1.1, -16.0 * wt)   # de-honk
            sig, self._wall_sig_zi = lfilter(b, a, sig, zi=self._wall_sig_zi)
            b2, a2 = self._pk(150.0, 0.7, 4.0 * wt)    # add body
            sig, self._wall_low_zi = lfilter(b2, a2, sig, zi=self._wall_low_zi)
        self._tap("wall de-honk", sig)        # tail-pipe wall thickness scoop
        # (Step 4) stainless-wall resonance formants: two narrow peaks give the
        # note its METAL ring.  A thicker wall (wt up) drops the peaks lower and
        # tightens them (fat & solid); a thin wall keeps them high & open (bright,
        # 'tinny').  Always on — it's the pipe material itself, not an effect.
        if _HAVE_SCIPY:
            f1 = self._wall_f1 * (1.0 - 0.18 * wt)
            f2 = self._wall_f2 * (1.0 - 0.22 * wt)
            ring = getattr(self, "_wall_ring", 1.0)      # material ping strength
            qf = getattr(self, "_wall_q", 1.0)           # material ring duration/Q
            # Q from the material damping: a low-loss wall (titanium) rings
            # sharper/longer; cast iron is broad and dead.
            bp1, ap1 = self._pk(f1, min(3.4 * qf, 12.0), (4.3 - 1.4 * wt) * ring)
            sig, self._wallpk1_zi = lfilter(bp1, ap1, sig, zi=self._wallpk1_zi)
            bp2, ap2 = self._pk(f2, min(4.2 * qf, 14.0), (3.2 - 1.6 * wt) * ring)
            sig, self._wallpk2_zi = lfilter(bp2, ap2, sig, zi=self._wallpk2_zi)
        self._tap("metal ring", sig)          # stainless wall-resonance formants
        # --- MEGAPHONE / exit-horn bark: the powerful mid formant a diverging
        # cone radiates (see the _mega setup).  A broad peak at the horn frequency
        # gives the massive 澎湃 midrange roar of an open race exit, and a gentle
        # trim of the extreme top (the horn's far field concentrates power in its
        # passband, not the thin >5 kHz hash) keeps it high AND full — the fix for
        # an F1 sounding like a thin 'broken trumpet'.
        if _HAVE_SCIPY and getattr(self, "_mega_f", 0.0) > 0.0:
            bM, aM = self._pk(self._mega_f, 0.8, 7.5 * self._mega_amt)
            sig, self._mega_zi = lfilter(bM, aM, sig, zi=self._mega_zi)
            bH, aH = self._pk(min(self._mega_f * 2.4, self.sample_rate * 0.44),
                              0.7, -3.5 * self._mega_amt)
            sig, self._mega_hi_zi = lfilter(bH, aH, sig, zi=self._mega_hi_zi)
        self._tap("megaphone", sig)           # exit-horn mid bark + top trim
        # displacement THUNDER: the deep low-end roar a big-cylinder engine carries
        # under the note (so a Ferrari V12 thunders, not just screams).
        if _HAVE_SCIPY and self._thunder is not None:
            sig, self._thunder_zi = lfilter(self._thunder[0], self._thunder[1],
                                            sig, zi=self._thunder_zi)
        self._tap("thunder", sig)             # deep displacement low-end roar
        # gear-grain: gear-driven valvetrain / timing-gear WHIR — a fine, dense
        # band-passed noise modulated by a gear-mesh tone, so it's a 'grind-like'
        # (but not actual grinding) grain riding ON the smooth note.  Rises with
        # rpm; per-engine amount = eng.gear_grain (Ferrari V12s etc.).
        gg = getattr(sim.engine, "gear_grain", 0.0) * P.get("gear_grain", 1.0)
        if _HAVE_SCIPY and gg > 1e-3 and dps > 1e-12:
            rf = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            f_mesh = max(sim.rpm / 60.0 * 8.5, 50.0)        # ~8.5x rev = a fine whir
            inc = 2.0 * math.pi * f_mesh / self.sample_rate
            ph = self._gear_phase + inc * np.arange(1, frames + 1)
            self._gear_phase = float(ph[-1] % (2.0 * math.pi))
            am = 0.55 + 0.45 * np.sin(ph)                   # gear-mesh modulation
            ngr = self._rng.standard_normal(frames)
            ngr, self._grain_zi = lfilter(self._grain_bp[0], self._grain_bp[1],
                                          ngr, zi=self._grain_zi)
            sig = sig + gg * (0.04 + 0.34 * rf) * ngr * am

        # --- (7b) full-system round-trip reflection: a weak, low-passed echo at
        # the pipe's round-trip time (2 x system length / sound speed) feeds a bit
        # of low end back in -> bouncy low frequencies and a longer, rounder tail,
        # instead of a dry abrupt cut-off.
        if _HAVE_SCIPY and dps > 1e-12:
            refl = self._tail_dl.process(sig, self._tail_len / c_runner * self.sample_rate)
            refl, self._tail_lp_zi = lfilter(self._tail_lp[0], self._tail_lp[1],
                                             refl, zi=self._tail_lp_zi)
            sig = sig + 0.16 * refl
        self._tap("reflection", sig)         # + gear-grain, round-trip echo

        # --- (8a) TAILPIPE RADIATION: what a microphone BEHIND the car hears is
        # NOT the in-duct pressure.  The pipe end radiates like a monopole whose
        # FAR-FIELD pressure follows the DERIVATIVE of the volume outflow
        # (p ~ dQ/dt, +6 dB/oct) while the NEAR field keeps the low-end body.
        # Blending the derivative in moves the virtual mic from inside the pipe
        # (where it effectively sat — the "mic at the engine" / wet complaint)
        # out to the exhaust exit: each pulse's edge sharpens into the discrete
        # dry puff you hear standing behind a real car (干/颗粒感).
        rad = min(max(P.get("tail_rad", 0.35), 0.0), 0.9)
        # A MOTORING engine (overrun / DFCO) has no sharp combustion blowdown to
        # radiate — only smooth air pumping — so the +6 dB/oct derivative has no
        # legitimate transient to sharpen and would just brighten the residual
        # hiss into a loud "air" wash that swamps the (now quiet) engine.  Fade
        # the radiation mix with combustion load so the overrun stays dark.
        # combustion load for radiation = POSITIVE blowdown only.  On the overrun
        # the cylinder is in deep VACUUM, so p_open is strongly NEGATIVE and the
        # abs() in `load` above reads it as high load (0.5) — a sign bug that kept
        # the HF radiation fully on and brightened the residual hiss into the
        # "air noise covering the engine".  Real combustion = pressure ABOVE
        # atmosphere, so gate on the positive part only.
        comb_load = min(max(strength * 1.25, 0.0), 1.0) if dps > 1e-12 else 0.0
        self._comb_load = comb_load           # reused by the overrun darkening
        rad *= comb_load       # no combustion (overrun) -> no sharp radiation
        if rad > 1e-3:
            ext = np.empty(frames + 1, dtype=np.float64)
            ext[0] = self._rad_prev
            ext[1:] = sig
            self._rad_prev = float(sig[-1])
            drv = np.diff(ext) * (self.sample_rate / (2.0 * math.pi * 500.0))
            sig = (1.0 - rad) * sig + rad * drv
        self._tap("radiation", sig)           # in-duct -> free-field derivative

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
        self._tap("tailpipe exit", sig)       # gas tearing out of the tip

        # --- OVERRUN DARKENING: a motoring engine (DFCO / no combustion) has no
        # sharp hot blowdown, so its exhaust note is physically DARK/muffled —
        # not the bright HF hash our residual synthesis leaves on high-boost,
        # high-rpm cars (the "wind-like white noise that covers everything" on
        # lift-off).  Fade a gentle 1-pole low-pass in as combustion load drops,
        # so the note darkens exactly when the fuel cuts.  Cheap and works with
        # or without scipy (manual 1-pole).
        over = 1.0 - self._comb_load
        if over > 0.05 and dps > 1e-12:
            fc_over = 5200.0 - 3400.0 * over          # 5.2 kHz on power -> 1.8 kHz overrun
            a_lp = math.exp(-2.0 * math.pi * fc_over / self.sample_rate)
            oma = 1.0 - a_lp
            if self._over_lp_zi is None:
                self._over_lp_zi = [0.0, 0.0]         # two cascaded 1-pole states
            if _HAVE_SCIPY:                           # vectorised 2-pole cascade
                y, z0 = lfilter([oma], [1.0, -a_lp], sig, zi=[self._over_lp_zi[0]])
                y, z1 = lfilter([oma], [1.0, -a_lp], y, zi=[self._over_lp_zi[1]])
                self._over_lp_zi = [float(z0[0]), float(z1[0])]
            else:                                     # manual 2-pole cascade (no scipy)
                y = np.empty_like(sig)
                p0, p1 = self._over_lp_zi
                for i in range(len(sig)):
                    p0 = oma * sig[i] + a_lp * p0
                    p1 = oma * p0 + a_lp * p1
                    y[i] = p1
                self._over_lp_zi = [float(p0), float(p1)]
            sig = (1.0 - over) * sig + over * y        # blend: full LP only on overrun

        # --- 3-band EQ (low / mid / high knobs) -----------------------------
        if _HAVE_SCIPY:
            if abs(P["eq_low"]) > 0.1:
                b, a = self._pk(120.0, 0.7, P["eq_low"])
                sig, self._eq_lo_zi = lfilter(b, a, sig, zi=self._eq_lo_zi)
            if abs(P["eq_mid"]) > 0.1:
                b, a = self._pk(850.0, 0.8, P["eq_mid"])
                sig, self._eq_mid_zi = lfilter(b, a, sig, zi=self._eq_mid_zi)
            if abs(P["eq_high"]) > 0.1:
                b, a = self._pk(4500.0, 0.7, P["eq_high"])
                sig, self._eq_hi_zi = lfilter(b, a, sig, zi=self._eq_hi_zi)
            if abs(P["presence"]) > 0.1:        # amp 'presence': broad upper-mid lift
                b, a = self._pk(3000.0, 0.6, P["presence"])
                sig, self._eq_pres_zi = lfilter(b, a, sig, zi=self._eq_pres_zi)
        self._tap("EQ", sig)

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
                            + (0.05 if self.road_pipe else 0.0))
        sig = self._reverb.process(sig)
        self._tap("cabin/room", sig)

        # --- auto-level (or fixed gain) + soft saturation + master volume ----
        if self.agc_enabled:
            rms = float(np.sqrt(np.mean(sig * sig))) + 1e-9
            self._level += (rms - self._level) * 0.04
            gain = min(0.22 / (self._level + 1e-6), 6.0)
            rate = 0.05 if gain > self._gain else 0.2    # rise SLOW (no decel pump-up)
            self._gain += (gain - self._gain) * rate
            sig *= self._gain
        else:
            sig *= 3.5
        # --- spatial distance: far away = darker + quieter (the pad's Y axis) -
        d = 1.0 - self.params["spatial_y"]      # 0 near .. 1 far
        if _HAVE_SCIPY and d > 0.02:
            sr = self.sample_rate
            cut = min(max(14000.0 - 11500.0 * d, 600.0), sr * 0.45)
            b, a = self._bw(2, cut)
            sig, self._spatial_zi = lfilter(b, a, sig, zi=self._spatial_zi)
        sig = sig * (1.0 / (1.0 + 1.7 * d))

        # --- road / tyre rumble: makes it sound like the car is MOVING down the
        # street, not strapped to a dyno.  Low band-passed noise swelling with road
        # speed (and a touch of throttle), sitting under the exhaust note.
        rn = P.get("road_noise", 0.22)
        if _HAVE_SCIPY and rn > 1e-3:
            spd = min(getattr(sim.drivetrain, "v", 0.0) / 32.0, 1.0)   # ~115 km/h full
            if spd > 0.015:
                nz = self._rng.standard_normal(frames)
                nz, self._roadn_zi = lfilter(self._roadn[0], self._roadn[1],
                                             nz, zi=self._roadn_zi)
                nz2 = self._rng.standard_normal(frames)
                nz2, self._roadn_lp_zi = lfilter(self._roadn_lp[0], self._roadn_lp[1],
                                                 nz2, zi=self._roadn_lp_zi)
                sig = sig + rn * spd * (1.6 * nz + 0.5 * nz2)

        # --- injection clatter: GDI 'sewing-machine' / piezo / diesel injector tick,
        # a band-passed HF texture loudest at idle and fading out with revs.
        ia = getattr(self, "_inj_amt", 0.0)
        if ia > 1e-3 and self._inj_bp is not None:
            nz, self._inj_zi = lfilter(self._inj_bp[0], self._inj_bp[1],
                                       self._rng.standard_normal(frames), zi=self._inj_zi)
            sig = sig + ia * nz
        # --- VALVETRAIN CLATTER: cam/tappet impact ticks, gated by the actual
        # valve events.  With even firing the events across all cylinders are
        # equally spaced every 720/(2n) deg, so ONE sawtooth of the live crank
        # phase gates a sharp noise ping — the classic sewing-machine tick that
        # dominates at idle and gets masked as combustion swells.  Radiates from
        # the cam covers DIRECTLY (post-exhaust-chain), like the injector tick.
        mech = P.get("mech", 0.30)
        if mech > 1e-3 and dps > 1e-12:
            spacing = 720.0 / max(2 * len(self._offsets), 1)
            ph_t = np.mod(self._audio_crank + dps * np.arange(frames), spacing) \
                / spacing
            tick_env = np.exp(-ph_t * 13.0)               # sharp hit, fast ring-down
            nzm = self._rng.standard_normal(frames)
            tick = np.diff(nzm, prepend=nzm[:1]) * tick_env   # HF 'click' spectrum
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            # scale DOWN with cylinder count: a V12's 180 events/s would fuse
            # into a continuous fizz at the 4-cyl level — real multi-bank
            # valvetrains blend into a much quieter hash
            n_sc = (4.0 / max(len(self._offsets), 1)) ** 0.5
            sig = sig + (mech * 0.050 * n_sc * (1.0 - 0.85 * rpm_frac)) * tick
        # --- cam-overlap idle CHOP + balance-shaft buzz: a slow amplitude wobble at
        # the firing rate (deep at idle for a big cam / an unbalanced no-shaft four).
        lump = getattr(self, "_cam_lump", 0.0) + getattr(self, "_balance_rough", 0.0)
        if lump > 1e-3 and self._wob_w > 0.0:
            ph = self._wob_ph + self._wob_w * np.arange(frames)
            self._wob_ph = float((ph[-1] + self._wob_w) % (2.0 * math.pi))
            sig = sig * (1.0 - lump * (0.5 + 0.5 * np.sin(ph)))

        # anti-harshness low-pass whose cutoff DROPS at very high rpm, where the
        # sharp combustion edges' harmonics fold past Nyquist into breakup (the
        # f2004 at 18k+ / on the overrun). Smoothly slewed so it never clicks.
        if _HAVE_SCIPY:
            rf = min(sim.rpm / 13000.0, 1.0)
            target = min(16500.0 - 7200.0 * rf, self.sample_rate * 0.46)
            self._aa_cut = getattr(self, "_aa_cut", target)
            self._aa_cut += (target - self._aa_cut) * 0.08
            b, a = butter(2, self._aa_cut / (self.sample_rate / 2), btype="low")
            if not hasattr(self, "_aa_zi"):
                self._aa_zi = np.zeros(2)
            sig, self._aa_zi = lfilter(b, a, sig, zi=self._aa_zi)
        # soft peak limiter BEFORE the tanh: a slow peak-follower pulls sustained
        # over-level back so high-rpm crests stay in tanh's musical range instead
        # of crushing into harsh 'clipping' breakup (F1 / high-revvers).
        x = sig * (self.volume * self.params["master"] * 1.5)
        pk = float(np.max(np.abs(x))) + 1e-9
        self._lim = max(pk, getattr(self, "_lim", pk) * 0.992)
        if self._lim > 1.0:
            x = x * (1.0 / self._lim)
        out = np.tanh(x).astype(np.float32)
        # exhaust loudness meter (RMS of the final output) for the HUD readout
        self.last_level = float(np.sqrt(np.mean(out * out))) if frames else 0.0
        # keep a decimated copy of the waveform for the HUD exhaust-flow scope
        if frames:
            step = max(1, frames // 64)
            self.last_wave = out[::step][:64].astype(np.float64).copy()
        self._tap("output", out)             # final post-master signal
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
        nyq = sr * 0.47
        for h, a in harmonics:
            if h * freq < nyq:                    # skip harmonics that would ALIAS
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

        # TRANSMISSION MESH WHINE (all cars, not just dog-boxes): the input
        # gear pair sings at tooth-mesh frequency (~21 teeth x shaft speed),
        # amplitude follows TRANSMITTED TORQUE — silent coasting, a fine rising
        # whine under load, exactly how a helical box behaves.
        gm = P.get("gear_mesh", 0.10)
        dt_ = getattr(sim, "drivetrain", None)
        if (gm > 1e-3 and dt_ is not None and dt_.gear > 0
                and dt_.clutch > 0.6 and rpm > 400.0):
            f_mesh = rpm / 60.0 * 21.0
            if f_mesh < sr * 0.42:
                load_t = min(abs(sim.gas_torque) / 600.0, 1.0)
                gw += (gm * 0.22 * load_t) * self._whine(
                    f_mesh, frames, [(1, 1.0), (2, 0.28)], phase_attr="_mesh_phase")
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
            # perfect fifth (root + 5th); the hidden 'o' mode adds a root bass
            # layer + a dominant-7th (V7) hung on top.
            voicing = _TURBO_V7 if self.o_chord else _PERFECT_FIFTH
            sub = getattr(eng, "induction_subtype", "")
            if bfrac > 0.02:
                if sub == "sequential":
                    # the SMALL turbo spools first (early, high-pitched); the BIG
                    # one hands over up top with an audible surge whoosh.
                    prim = min(bfrac / 0.5, 1.0)
                    sec = max(0.0, (bfrac - 0.45) / 0.55)
                    f1 = min(1600.0 + prim * 4200.0, sr * 0.45)
                    f2 = min(780.0 + sec * 3500.0, sr * 0.45)
                    out += (tv * prim * 0.22) * self._whine(
                        f1, frames, list(voicing), phase_attr="_seq_phase")
                    if sec > 1e-3:
                        out += (tv * sec * 0.30) * self._whine(
                            f2, frames, list(voicing), phase_attr="_seq2_phase")
                    if sec - self._seq_prev > 0.004:      # big turbo coming on-song
                        self._seq_surge = min(
                            1.0, self._seq_surge + (sec - self._seq_prev) * 8.0)
                    self._seq_prev = sec
                    if self._seq_surge > 1e-3:
                        n = np.arange(frames)
                        env = np.exp(-n / (sr * 0.18)) * self._seq_surge
                        out += (tv * 0.5) * self._rng.standard_normal(frames) * env
                        self._seq_surge *= math.exp(-frames / (sr * 0.25))
                    out += (tv * (prim + sec) * 0.16) * self._rng.standard_normal(frames)
                elif sub == "twin_scroll":
                    # divided housing keeps the exhaust pulses separated -> a
                    # tighter, cleaner, higher whistle with far less air hiss.
                    f = min(1150.0 + bfrac * 5300.0, sr * 0.45)
                    amp = tv * bfrac * 0.32
                    out += amp * self._whine(f, frames, list(voicing))
                    out += (amp * 0.18) * self._rng.standard_normal(frames)
                else:
                    f = 900.0 + bfrac * 5200.0            # whistle rises with boost
                    amp = tv * bfrac * 0.30
                    out += amp * self._whine(min(f, sr * 0.45), frames, list(voicing))
                    out += (amp * 0.5) * self._rng.standard_normal(frames)  # air
                if sub == "twincharge":
                    # compound: a positive-displacement blower whine sings LOW and
                    # crossfades into the turbo whistle as the revs climb.
                    ratio = eng.blower_ratio if eng.blower_ratio > 0 else 9.0
                    fb = (rpm / 60.0) * ratio
                    low = max(0.0, 1.0 - min(rpm / max(eng.redline_rpm, 1.0), 1.0) / 0.7)
                    if 20.0 < fb < sr * 0.45 and low > 0.01:
                        out += (sv * (0.3 + 0.5 * low) * 0.5) * self._whine(
                            fb, frames, [(1, 1.0), (2, 0.5), (3, 0.28)],
                            phase_attr="_whine_phase")
            # Throttle snaps shut while on boost -> the lift-off sound.  Which
            # one you hear depends on where the pressurised air goes:
            #   * an atmospheric dump valve vents it in one clean 'PSHHH';
            #   * with no (or a shut) valve the air backs up and pulses BACKWARD
            #     through the compressor wheel again and again -> compressor
            #     surge, the rapid 'stu-tu-tu-tu' flutter.
            # ROBUST LIFT detection: a real pedal is RAMPED (the app moves the
            # throttle ~0.04/frame, ~0.42 s for a full sweep), so a per-block delta
            # never snaps.  Track a slowly-decaying PEAK of recent throttle; a
            # blow-off fires when the pedal has dropped well BELOW where it recently
            # was, while still on boost — an edge-guard stops it re-firing every block.
            # NOTE the decay MUST be slower than the pedal ramp or the peak just
            # tracks the throttle DOWN and no gap ever opens: at 0.96/block (8 ms) the
            # peak fell ~4%/block, FASTER than the pedal, so a normal lift never
            # triggered (only the 'P' test, which sets the envelope directly).  0.995
            # holds the peak ~0.7 s, so a lift opens the >0.30 gap in ~150 ms while
            # boost is still up (verified across 30/60 FPS).
            self._thr_ref = max(sim.throttle, self._thr_ref * 0.995)
            if (self._thr_ref - sim.throttle) > 0.30 and sim.boost > 0.10 \
                    and self._bov_env < 0.5:
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
                # AUDIBLE base level (was tv*0.6-1.15 -> ~0.2 at tv=0.21, buried
                # under the engine so the toggles did nothing).  A real dump valve
                # is a LOUD lift-off event; give it its own prominence.
                bov = 0.42 + 1.0 * tv
                if self.ssqv:
                    # HKS SSQV atmospheric dump: LOUD, sharp, very BRIGHT metallic
                    # 'TSSSH' (HF-emphasised).
                    hf = np.diff(noise, prepend=noise[:1])
                    env = np.exp(-n / (sr * 0.13)) * self._bov_env
                    out += (bov * 1.25) * (0.35 * noise + 1.0 * hf) * env
                    self._bov_env *= math.exp(-frames / (sr * 0.15))
                elif self.flutter:
                    # JDM 爆改-turbo COMPRESSOR SURGE — the iconic 'stu-tu-tu-tu'
                    # machine-gun flutter of trapped boost pulsing back through the
                    # wheel (recirc / no-BOV / anti-lag).  A bigger turbo (more boost)
                    # flutters HARDER and a touch faster, and the burst SUSTAINS for
                    # ~0.5 s as the pressure bleeds down.
                    fl = 13.0 + 11.0 * bfrac             # ~13-24 Hz surge rate (classic)
                    ph = self._flutter_phase + 2.0 * math.pi * fl * n / sr
                    if frames:
                        self._flutter_phase = float(ph[-1] % (2.0 * math.pi))
                    # punchy 'tu' bursts: a hard gate (sin^2.5) rung with a little
                    # low-mid air body so each pulse chuffs instead of just hissing.
                    pulse = np.clip(np.sin(ph), 0.0, 1.0) ** 2.0
                    env = np.exp(-n / (sr * 0.40)) * self._bov_env
                    agg = 1.6 + 1.2 * bfrac              # bigger boost -> louder flutter
                    out += (bov * agg) * noise * pulse * env
                    self._bov_env *= math.exp(-frames / (sr * 0.42))  # sustains longer
                else:
                    # stock recirc: SOFT, DARK 'pshhh' (1-pole low-passed noise)
                    dark = 0.5 * (noise + np.concatenate(([self._bov_prev],
                                                          noise[:-1])))
                    self._bov_prev = float(noise[-1])
                    env = np.exp(-n / (sr * 0.09)) * self._bov_env
                    out += (bov * 0.55) * dark * env
                    self._bov_env *= math.exp(-frames / (sr * 0.12))
        self._prev_throttle = sim.throttle

        gv = P["gearbox_vol"]
        dt = sim.drivetrain
        # Straight-cut (spur) sequential / dog box: the gears are CONSTANT-MESH and
        # the input/layshaft pair runs at ENGINE speed and is always loaded, so the
        # dominant gearbox whine TRACKS ENGINE RPM — it rises through a gear and
        # DROPS on every upshift (the classic race-box 'weee-WHEE-weee'), with a
        # slightly different mesh pitch per selected gear.  A much quieter final-
        # drive / crown-wheel layer tracks ROAD SPEED underneath (the continuous
        # part).  (The old model used only the road-speed final drive — wrong: it
        # never dipped on a shift.)
        if self.straight_cut and gv > 1e-3 and dt.gear > 0 and rpm > 350.0:
            erps = rpm / 60.0
            ng = dt.num_gears

            def gear_mesh_hz(g):
                return erps * (11.0 + 1.5 * (g - 1))   # each gear: own tooth count

            # (1) primary / input constant mesh — same pitch in every gear, two
            # strong harmonics (a buzzy, tooth-impact tone, not a pure sine).
            f_in = erps * 8.5
            if 30.0 < f_in < sr * 0.45:
                gw += (gv * 0.22) * self._whine(
                    f_in, frames, [(1, 1.0), (2, 0.55), (3, 0.28)],
                    phase_attr="_gwinput_phase")
            # (2) the SELECTED (loaded) gear's output mesh — the loudest layer, a
            # distinct pitch per gear (so it steps when you shift).
            f_sel = gear_mesh_hz(dt.gear)
            if 30.0 < f_sel < sr * 0.45:
                gw += (gv * 0.40) * self._whine(
                    f_sel, frames, list(_AUG_TRIAD), phase_attr="_gearbox_phase")
            # (3) the OTHER constant-mesh gears keep spinning unloaded -> a quiet
            # shimmer chorus of extra pitches underneath (use the neighbours).
            for off, ph in ((-1, "_gwa_phase"), (2, "_gwb_phase")):
                g2 = dt.gear + off
                if 1 <= g2 <= ng:
                    f2 = gear_mesh_hz(g2)
                    if 30.0 < f2 < sr * 0.45:
                        gw += (gv * 0.07) * self._whine(
                            f2, frames, [(1, 1.0), (2, 0.3)], phase_attr=ph)
            # (4) final-drive / crown-wheel whine — tracks ROAD speed (continuous).
            if dt.v > 0.4:
                wheel_rps = dt.v / (2.0 * math.pi * max(dt.wheel_radius, 0.05))
                ff = wheel_rps * dt.final_drive * 9.0
                if 30.0 < ff < sr * 0.45:
                    gw += (gv * 0.13) * self._whine(
                        ff, frames, [(1, 1.0), (2, 0.4)], phase_attr="_finaldrive_phase")

        # --- hybrid power unit: MGU-K (motor) + MGU-H (e-turbo) electric whine ---
        hv = P["hybrid_vol"]
        mgu = getattr(eng, "mgu_whine", 0.0)    # F1 PU: prominent MGU whines
        if hv > 1e-3:
            # MGU-K (kinetic): a clean high motor whine that rises with rpm and
            # swells with deployment (throttle).  On an F1 PU it is LOUD.
            if eng.hybrid_kw > 0.0 and sim.hybrid_on and sim.throttle > 0.02:
                fm = (rpm / 60.0) * 14.0                  # geared-up motor whine
                if 60.0 < fm < sr * 0.45:
                    amp = hv * (0.18 + 0.30 * mgu) * min(sim.throttle, 1.0)
                    out += amp * self._whine(fm, frames, [(1, 1.0), (2, 0.28)],
                                             phase_attr="_motor_phase")
            # MGU-H (heat): the motor on the TURBO SHAFT spins it ~125,000 rpm, so
            # the modern-F1 PU has a piercing high electric WHISTLE and near-instant
            # boost (no lag).  On an F1 PU it keeps singing even slightly off the
            # throttle (the MGU-H holds the turbo spinning to recover heat energy).
            if eng.electric_turbo:
                present = bfrac
                if mgu > 1e-3:
                    present = max(bfrac, 0.28 * min(rpm / max(eng.redline_rpm, 1.0),
                                                    1.0))
                if present > 0.02:
                    # lower & capped so the MGU-H whistle is present but not
                    # ear-piercing; one gentle 2nd harmonic, very little air hiss.
                    fe = min((1600.0 + 2400.0 * bfrac) * (1.0 + 0.3 * mgu), 5200.0)
                    amp = hv * (0.20 + 0.38 * mgu) * present
                    out += amp * self._whine(min(fe, sr * 0.45), frames,
                                             [(1, 1.0), (2, 0.28)],
                                             phase_attr="_ecomp_phase")
                    out += (amp * 0.10) * self._rng.standard_normal(frames)  # air-rush

        # pipe-wall thickness: dull the brassy 'trumpet' edge of the whines
        wt = P["wall_thickness"]
        if _HAVE_SCIPY and wt > 1e-3:
            cut = min(max(7000.0 - 5600.0 * wt, 900.0), sr * 0.45)
            b, a = self._bw(2, cut)
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
        b, a = self._bw(2, cut)
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
        if ON_ANDROID or not _HAVE_SD:           # no PortAudio -> use pygame's mixer
            return self._start_pygame()
        attempts = []
        if self.prefer_exclusive and self._device is not None:
            try:
                excl = sd.WasapiSettings(exclusive=True)
                attempts.append(("exclusive", dict(
                    device=self._device, samplerate=self.sample_rate, channels=2,
                    blocksize=128, latency="low", extra_settings=excl)))
            except Exception:
                pass
        # Keep the tested 256-frame render block (bigger blocks broke the synth's
        # internal buffers), but ask for a generous ~60ms host buffer so a long
        # pure-Python draw can stall the GIL without underrunning the audio.
        # (Exclusive mode above stays tiny for the latency purists who opt in.)
        OB, OL = BLOCK, 0.06
        if self._device is not None:
            attempts.append(("shared", dict(
                device=self._device, samplerate=self.sample_rate, channels=2,
                blocksize=OB, latency=OL)))
            attempts.append(("shared-mono", dict(
                device=self._device, samplerate=self.sample_rate, channels=1,
                blocksize=OB, latency=OL)))
        attempts.append(("default", dict(
            device=None, samplerate=self.sample_rate, channels=2, blocksize=OB,
            latency=OL)))
        attempts.append(("default-mono", dict(
            device=None, samplerate=self.sample_rate, channels=1, blocksize=OB,
            latency=OL)))

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

    # --- Android / no-PortAudio backend: stream blocks through pygame.mixer -----
    def _start_pygame(self):
        """Play rendered blocks via pygame's SDL2 mixer (the Android path, and a
        desktop fallback when sounddevice/PortAudio is missing).  A daemon feeder
        thread keeps a Channel topped up with freshly rendered audio."""
        try:
            import pygame
        except Exception:
            print("[audio] disabled: pygame mixer unavailable")
            self.enabled = False
            return False
        sr = int(self.sample_rate or SAMPLE_RATE)
        # Larger device buffer on the pygame/Android backend: the SDL audio
        # callback then fires half as often, giving the (GIL-contended) feeder
        # thread more wall-clock slack to stay ahead at high rpm — fewer under-runs
        # / less crackle on weak SoCs.  Costs ~30 ms more latency, inaudible for an
        # engine sim.  Desktop uses the low-latency sounddevice path instead.
        pg_buf = 2048
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            pygame.mixer.init(frequency=sr, size=-16, channels=2, buffer=pg_buf)
        except Exception as exc:
            print("[audio] disabled: pygame.mixer.init failed (%s)" % exc)
            self.enabled = False
            return False
        self._rebuild_for_rate(sr)
        self.sample_rate = sr
        self._pg_chan = pygame.mixer.Channel(0)
        self._pg_cur = self._pg_prev = None      # keep queued Sounds alive vs GC
        self._pg_run = True
        self._pg_thread = threading.Thread(target=self._pygame_feed, daemon=True)
        self._pg_thread.start()
        self.mode = "pygame"
        self.latency_ms = round(2 * pg_buf / sr * 1000.0, 1)
        return True

    def _pygame_feed(self):
        import time
        import pygame
        CH = BLOCK                               # frames per queued chunk (tested size)
        while self._pg_run:
            try:
                if self._pg_chan.get_queue() is not None:   # already one ahead
                    time.sleep(0.004)
                    continue
                mono = self._render_block(CH)
                ang = self.params["spatial_x"] * (math.pi * 0.5)
                stereo = np.empty((CH, 2), dtype=np.int16)
                stereo[:, 0] = (np.clip(mono * math.cos(ang), -1.0, 1.0)
                                * 32767.0).astype(np.int16)
                stereo[:, 1] = (np.clip(mono * math.sin(ang), -1.0, 1.0)
                                * 32767.0).astype(np.int16)
                snd = pygame.sndarray.make_sound(stereo)
                if self._pg_chan.get_busy():
                    self._pg_chan.queue(snd)
                else:
                    self._pg_chan.play(snd)
                self._pg_prev, self._pg_cur = self._pg_cur, snd
            except Exception:
                time.sleep(0.01)
        try:
            self._pg_chan.stop()
        except Exception:
            pass

    def stop(self):
        self._pg_run = False
        if self._pg_thread is not None:
            try:
                self._pg_thread.join(timeout=0.3)
            except Exception:
                pass
            self._pg_thread = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
