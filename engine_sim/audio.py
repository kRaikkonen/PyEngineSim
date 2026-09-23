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

# --------------------------------------------------------------------------
# Filters.
#
# scipy does not exist on iOS or Android, and every `if _HAVE_SCIPY:` in this
# file used to be a branch with NO else -- so on a phone the filters were not
# replaced, they were SKIPPED.  A combustion snap with nothing low-passing it
# keeps its full-band content, and the chain screamed at Nyquist loudly enough
# to bury the engine (found on an iPhone, 2026-08-29).
#
# So these are real implementations, always defined so they can be checked
# against scipy wherever scipy exists (test_headless.run_filter_fallback).
# `_HAVE_SCIPY` therefore no longer asks "is scipy installed" but "do we have
# working filters" -- which is now everywhere, so every branch below is live
# on every platform.
# --------------------------------------------------------------------------
def _bilinear_biquad(wn, btype):
    """One Butterworth biquad by bilinear transform, matching scipy.butter."""
    k = math.tan(math.pi * min(max(wn, 1e-6), 0.999999) / 2.0)
    k2 = k * k
    r2 = math.sqrt(2.0)
    d = 1.0 + r2 * k + k2
    a = np.array([1.0, 2.0 * (k2 - 1.0) / d, (1.0 - r2 * k + k2) / d])
    if btype == "high":
        b = np.array([1.0, -2.0, 1.0]) / d
    else:
        b = np.array([k2, 2.0 * k2, k2]) / d
    return b, a


def _bilinear_onepole(wn, btype):
    k = math.tan(math.pi * min(max(wn, 1e-6), 0.999999) / 2.0)
    a = np.array([1.0, (k - 1.0) / (k + 1.0)])
    if btype == "high":
        b = np.array([1.0, -1.0]) / (k + 1.0)
    else:
        b = np.array([k, k]) / (k + 1.0)
    return b, a


def _np_bandpass(order, w1, w2):
    """TRUE digital Butterworth band-pass -- what scipy returns, by hand.

    Each analog low-pass prototype pole maps to a conjugate PAIR straddling
    the band (s = p*BW/2 +- sqrt((p*BW/2)^2 - w0^2)); `order` zeros go at DC
    and `order` at Nyquist; the bilinear transform is folded into the prewarp.
    Normalised to unity at the band centre, as scipy does.

    This used to be a low-pass * high-pass cascade, which is the same ORDER
    but not the same filter -- 10 dB down across the passband on the injector
    design.  Desktop Python gets scipy, so that error only ever reached the
    phone: it was running a filter the sound was never tuned on.
    """
    o1 = math.tan(math.pi * float(w1) / 2.0)      # prewarp, bilinear folded in
    o2 = math.tan(math.pi * float(w2) / 2.0)
    bw, o0sq = o2 - o1, o1 * o2
    b = np.array([1.0])
    a = np.array([1.0])
    for k in range(order):
        theta = math.pi * (2 * k + order + 1) / (2 * order)
        p = complex(math.cos(theta), math.sin(theta))   # unit-cutoff LP pole
        half = p * bw / 2.0
        root = (half * half - o0sq) ** 0.5
        for s in (half + root, half - root):
            if s.imag < 0.0:
                continue                          # one of each conjugate pair
            z = (1.0 + s) / (1.0 - s)             # bilinear
            a = np.convolve(a, [1.0, -2.0 * z.real, abs(z) ** 2])
            b = np.convolve(b, [1.0, 0.0, -1.0])  # a zero at DC and at Nyquist
    # unity at the band centre: the frequency whose prewarp is sqrt(o1*o2)
    w0 = 2.0 * math.atan(math.sqrt(o0sq)) / math.pi
    z0 = complex(math.cos(math.pi * w0), math.sin(math.pi * w0))
    num = sum(c * z0 ** -i for i, c in enumerate(b))
    den = sum(c * z0 ** -i for i, c in enumerate(a))
    return b * (1.0 / abs(num / den)), a


def _np_butter(order, wn, btype="low"):
    """Butterworth design by the bilinear transform."""
    if isinstance(wn, (list, tuple, np.ndarray)):
        return _np_bandpass(order, float(wn[0]), float(wn[1]))
    wn = float(wn)
    if order <= 1:
        return _bilinear_onepole(wn, btype)
    b, a = _bilinear_biquad(wn, btype)
    for _ in range(order - 2):
        bb, aa = _bilinear_biquad(wn, btype)
        b, a = np.convolve(b, bb), np.convolve(a, aa)
    return b, a


_IR_CACHE = {}


def _poles_of(a):
    """Roots of the denominator.

    np.roots builds a companion matrix and runs an eigenvalue solve -- 12.5 us
    to factor a quadratic.  Every filter here is order 1 or 2 (the one order-4
    is a cascade of two), so use the closed forms and keep np.roots as the
    general fallback.
    """
    n = len(a) - 1
    if n <= 0:
        return np.array([])
    if n == 1:
        return np.array([-a[1] / a[0]], dtype=np.complex128)
    if n == 2:
        a0, a1, a2 = float(a[0]), float(a[1]), float(a[2])
        disc = complex(a1 * a1 - 4.0 * a0 * a2, 0.0) ** 0.5
        return np.array([(-a1 + disc) / (2.0 * a0),
                         (-a1 - disc) / (2.0 * a0)], dtype=np.complex128)
    return np.roots(a).astype(np.complex128)


def _impulse_response(b, a, n):
    """Truncated impulse response, for filters whose poles decay fast.

    Plain Python floats: the arrays here are three elements long, so a numpy
    call per tap cost more than the arithmetic it performed (70 us for 48
    taps).
    """
    bl = [float(v) for v in b]
    al = [float(v) for v in a]
    nb, na = len(bl), len(al)
    h = np.empty(n)
    y1 = y2 = y3 = y4 = 0.0
    for i in range(n):
        acc = bl[i] if i < nb else 0.0          # x is a unit impulse at 0
        if na > 1:
            acc -= al[1] * y1
        if na > 2:
            acc -= al[2] * y2
        if na > 3:
            acc -= al[3] * y3
        if na > 4:
            acc -= al[4] * y4
        y4, y3, y2, y1 = y3, y2, y1, acc
        h[i] = acc
    return h


_POW_CACHE = {}


def _pole_powers(pole, n):
    """pole**[0..n-1] and its reciprocal, cached: identical every block."""
    key = (pole.real, pole.imag, n)
    got = _POW_CACHE.get(key)
    if got is None:
        if len(_POW_CACHE) > 256:
            _POW_CACHE.clear()
        # cumprod beats ** on a complex ramp (15.9 us vs 26.3 us) and is
        # exactly the same numbers
        pw = np.empty(n, dtype=np.complex128)
        pw[0] = 1.0
        if n > 1:
            np.cumprod(np.full(n - 1, pole, dtype=np.complex128), out=pw[1:])
        got = _POW_CACHE[key] = (pw, 1.0 / pw)
    return got


def _one_pole(v, pole, y_prev):
    """y[n] = pole*y[n-1] + v[n]: exact, vectorised, overflow-safe.

    y[m] = pole^m * (pole*y_prev + sum_(j<=m) v[j] * pole^-j).  That pole^-j
    term grows, so the block is cut into chunks short enough that it never
    exceeds ~1e12 -- far beyond audio precision, and no Python loop per sample.
    """
    n = len(v)
    if n == 0:
        return v, y_prev
    ap = abs(pole)
    if ap < 1e-9:                       # degenerate: pure feed-through
        out = v.astype(np.complex128, copy=True)
        out[0] += pole * y_prev
        return out, out[-1]
    if ap >= 0.999999:
        step = n
    else:
        step = max(1, min(n, int(27.6 / -math.log(ap)) + 1))
    if step >= n:                       # the common case: one shot, no loop
        pw, ipw = _pole_powers(pole, n)
        out = pw * (pole * y_prev + np.cumsum(v * ipw))
        return out, out[-1]
    out = np.empty(n, dtype=np.complex128)
    pw, ipw = _pole_powers(pole, step)
    i = 0
    while i < n:
        k = min(step, n - i)
        seg = pw[:k] * (pole * y_prev + np.cumsum(v[i:i + k] * ipw[:k]))
        out[i:i + k] = seg
        y_prev = seg[-1]
        i += k
    return out, y_prev


_ONEPOLE_POW = {}


def _one_pole_real(v, p, y_prev):
    """Real y[n] = p*y[n-1] + v[n].  Same closed form as _one_pole, without
    the complex arithmetic -- and these are usually 14-sample segments, so the
    overhead IS the cost."""
    n = v.shape[0]
    if n == 0:
        return v, y_prev
    ap = abs(p)
    if ap < 1e-12:
        out = v.copy()
        out[0] += p * y_prev
        return out, out[-1]
    step = n if ap >= 0.999999 else max(1, min(n, int(27.6 / -math.log(ap)) + 1))
    if step >= n:
        key = (p, n)
        got = _ONEPOLE_POW.get(key)
        if got is None:
            if len(_ONEPOLE_POW) > 512:
                _ONEPOLE_POW.clear()
            pw = np.empty(n)
            pw[0] = 1.0
            if n > 1:
                np.cumprod(np.full(n - 1, p), out=pw[1:])
            got = _ONEPOLE_POW[key] = (pw, 1.0 / pw)
        pw, ipw = got
        out = pw * (p * y_prev + np.cumsum(v * ipw))
        return out, out[-1]
    out = np.empty(n)
    i = 0
    while i < n:
        k = min(step, n - i)
        pw = np.empty(k)
        pw[0] = 1.0
        if k > 1:
            np.cumprod(np.full(k - 1, p), out=pw[1:])
        seg = pw * (p * y_prev + np.cumsum(v[i:i + k] / pw))
        out[i:i + k] = seg
        y_prev = seg[-1]
        i += k
    return out, y_prev


def _np_lfilter(b, a, x, zi=None):
    """scipy-compatible enough for this file: returns (y, zf).

    `zi` is opaque -- we hand back our own state and only require the caller
    pass it straight back.  Zeros of any length mean "at rest", which is
    exactly how every call site initialises it.
    """
    # --- first order: over half of all calls, and the short ones.  Straight
    # real arithmetic, no cache lookups, no complex, no np.convolve.
    if len(a) == 2 and len(b) <= 2 and a[0] == 1.0:
        x = np.asarray(x, dtype=np.float64)
        p = -float(a[1])
        b0 = float(b[0])
        b1 = float(b[1]) if len(b) > 1 else 0.0
        x_prev = y_prev = 0.0
        if zi is not None and len(zi) >= 2:
            x_prev = float(np.real(zi[0]))
            y_prev = float(np.real(zi[1]))
        if b1:
            v = b0 * x
            v[1:] += b1 * x[:-1]
            v[0] += b1 * x_prev
        else:
            v = b0 * x
        y, y_end = _one_pole_real(v, p, y_prev)
        last_x = float(x[-1]) if x.shape[0] else x_prev
        return y, np.array([last_x, y_end])

    b = np.asarray(b, dtype=np.float64)
    a = np.asarray(a, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    if a[0] != 1.0:
        b, a = b / a[0], a / a[0]
    n_in = len(b) - 1
    key = (b.tobytes(), a.tobytes())
    cached = _IR_CACHE.get(key)
    if cached is None:
        poles = _poles_of(a)
        radius = float(np.max(np.abs(poles))) if len(poles) else 0.0
        # fast-decaying poles: a short FIR IS the filter, to ~1e-10, and one
        # convolve is much cheaper than a pole cascade
        ir = _impulse_response(b, a, 48) if (len(poles) and radius < 0.62) else None
        pair = None
        if ir is None and len(poles) == 2 and abs(poles[0].imag) > 1e-12 \
                and abs(poles[0] - np.conj(poles[1])) < 1e-12:
            # A = p / (p - conj(p)); y = 2*Re(A * onepole(v, p))
            pair = (poles[0], poles[0] / (poles[0] - poles[1]))
        if len(_IR_CACHE) > 400:
            _IR_CACHE.clear()
        cached = _IR_CACHE[key] = (poles, ir, pair)
    poles, ir, pair = cached

    if ir is not None:
        keep = len(ir) - 1
        hist = np.zeros(keep)
        if zi is not None and len(zi) >= keep:
            hist = np.real(np.asarray(zi)[:keep])
        buf = np.concatenate([hist[::-1], x])
        y = np.convolve(buf, ir)[keep:keep + len(x)]
        return y, buf[-keep:][::-1].astype(np.complex128)

    n_state = n_in + len(poles)
    st = np.zeros(n_state, dtype=np.complex128)
    if zi is not None and len(zi) == n_state:
        st = np.asarray(zi, dtype=np.complex128).copy()
    past = np.real(st[:n_in])
    pole_state = st[n_in:].copy()

    buf = np.concatenate([past[::-1], x]) if n_in else x
    v = np.convolve(buf, b)[n_in:n_in + len(x)].astype(np.complex128)
    if pair is not None:                    # conjugate pair: one pass, not two
        pole, amp = pair
        s1, pole_state[0] = _one_pole(v, pole, pole_state[0])
        y = 2.0 * np.real(amp * s1)
    else:
        for idx, pole in enumerate(poles):
            v, pole_state[idx] = _one_pole(v, pole, pole_state[idx])
        y = np.real(v)

    newpast = (buf[-n_in:][::-1] if n_in else np.zeros(0))
    return y, np.concatenate([newpast.astype(np.complex128), pole_state])


try:
    from scipy.signal import lfilter, butter
    _NATIVE_SCIPY = True
except Exception:                       # pragma: no cover -- the phone
    lfilter, butter = _np_lfilter, _np_butter
    _NATIVE_SCIPY = False


def _direct_lfilter(wrapper):
    """scipy's lfilter minus its wrapper, for IIR filters -- or None.

    Since scipy 1.15 lfilter goes through array-API dispatch first: ~5 us a
    call, more than the C routine needs for a 256-sample biquad, and a block
    makes 110-130 calls.  For len(a) > 1 the wrapper only does atleast_1d /
    asarray and calls _sigtools._linear_filter -- so calling that directly
    runs the same code on the same arrays and gives the same samples.  FIR
    calls (len(a) == 1: the wrapper convolves instead) still go through it.
    Proven identical here at import, or not used."""
    try:
        from scipy.signal import _sigtools
        lin = _sigtools._linear_filter
    except Exception:
        return None

    def fast(b, a, x, axis=-1, zi=None):
        a = np.atleast_1d(a)
        if a.ndim != 1 or a.shape[0] < 2:
            return wrapper(b, a, x, axis=axis, zi=zi)
        b = np.atleast_1d(b)
        if zi is None:
            return lin(b, a, np.asarray(x), axis)
        return lin(b, a, np.asarray(x), axis, np.asarray(zi))

    try:
        rng = np.random.default_rng(1)
        x = rng.standard_normal(300)
        b2, a2 = butter(2, 0.1)
        z2 = rng.standard_normal(2)
        for args, kw in (((b2, a2, x), dict(zi=z2)),
                         (([0.3], [1.0, -0.7], x), dict(zi=np.zeros(1))),
                         ((b2, a2, x), {})):
            r0, r1 = wrapper(*args, **kw), fast(*args, **kw)
            r0 = r0 if isinstance(r0, tuple) else (r0,)
            r1 = r1 if isinstance(r1, tuple) else (r1,)
            if len(r0) != len(r1) or any(
                    np.asarray(p).dtype != np.asarray(q).dtype
                    or not np.array_equal(p, q) for p, q in zip(r0, r1)):
                return None
    except Exception:
        return None
    return fast


if _NATIVE_SCIPY:
    lfilter = _direct_lfilter(lfilter) or lfilter

_HAVE_SCIPY = True

class PortableRNG:
    """xoshiro256** + Box-Muller: identical in Python and in Swift.

    numpy's Generator is faster and better, and it is what ships.  This exists
    so a reimplementation can be compared SAMPLE FOR SAMPLE rather than "the
    spectra look close", which is the standard that lets a port drift.

    Values are produced in strict sequence order, so a vectorised call here and
    a scalar loop there yield the same numbers.
    """

    __slots__ = ("s0", "s1", "s2", "s3", "_spare")

    def __init__(self, seed):
        # splitmix64 to spread a single seed over the four words
        x = int(seed) & 0xFFFFFFFFFFFFFFFF
        st = []
        for _ in range(4):
            x = (x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
            z = x
            z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
            z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
            st.append(z ^ (z >> 31))
        self.s0, self.s1, self.s2, self.s3 = st
        self._spare = None

    def _next(self):
        s0, s1, s2, s3 = self.s0, self.s1, self.s2, self.s3
        M = 0xFFFFFFFFFFFFFFFF
        r = (s1 * 5) & M
        r = (((r << 7) | (r >> 57)) & M) * 9 & M
        t = (s1 << 17) & M
        s2 ^= s0
        s3 ^= s1
        s1 ^= s2
        s0 ^= s3
        s2 ^= t
        s3 = ((s3 << 45) | (s3 >> 19)) & M
        self.s0, self.s1, self.s2, self.s3 = s0, s1, s2, s3
        return r

    def _uniform(self):
        """[0, 1) from the top 53 bits, the standard construction."""
        return (self._next() >> 11) * (1.0 / 9007199254740992.0)

    def random(self, n=None):
        if n is None:
            return self._uniform()
        return np.fromiter((self._uniform() for _ in range(int(n))),
                           dtype=np.float64, count=int(n))

    def _normal(self):
        if self._spare is not None:
            v, self._spare = self._spare, None
            return v
        # Box-Muller, rejecting u1 == 0 so the log is finite
        while True:
            u1 = self._uniform()
            if u1 > 0.0:
                break
        u2 = self._uniform()
        r = math.sqrt(-2.0 * math.log(u1))
        self._spare = r * math.sin(2.0 * math.pi * u2)
        return r * math.cos(2.0 * math.pi * u2)

    def standard_normal(self, n=None):
        if n is None:
            return self._normal()
        return np.fromiter((self._normal() for _ in range(int(n))),
                           dtype=np.float64, count=int(n))


from .engine import P_ATM
try:                                    # the turbocharger as a machine
    from . import turbo as turbo_mod
except Exception:                       # pragma: no cover
    turbo_mod = None

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
    # 1 (default): NOT a musical chord — the ENGINE SERIES.  A real exhaust
    # carries every integer harmonic of the firing rate with a ~1/k physical
    # decay; picking musical intervals (root+5th+octave) out of it is
    # instrument-synthesis thinking and is exactly what made the firing body
    # read as a PITCHED VOICE ("像人声/变调").  Chords 2-6 stay as easter eggs.
    ((1.0, 1.0), (2.0, 0.62), (3.0, 0.45), (4.0, 0.34), (5.0, 0.27),
     (6.0, 0.22)),
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


class _TapDelay:
    """Write-once, multi-tap read delay line — the EARLY-REFLECTION front of
    the in-pipe reverb (the first few discrete passes down the full run)."""

    def __init__(self, size):
        self.buf = np.zeros(int(size) + 4, dtype=np.float64)
        self.wp = 0

    def process(self, x, delays):
        n = len(x)
        N = len(self.buf)
        wi = (self.wp + np.arange(n)) % N
        self.buf[wi] = x
        outs = []
        for d in delays:
            d = int(min(max(d, 1), N - n - 2))
            ri = (self.wp + np.arange(n) - d) % N
            outs.append(self.buf[ri].copy())
        self.wp = (self.wp + n) % N
        return outs


class _FlybyDelay:
    """Fractional delay line whose delay RAMPS per sample — a moving source's
    propagation delay.  Changing path length IS the Doppler effect (physically
    exact: d(delay)/dt = radial velocity / c gives the pitch bend), so a car
    passing the trackside mic sweeps +30 %/-19 % at 290 km/h with zero explicit
    pitch-shifter (given the RETARDED delay -- see _TrackSide) — the classic F1 'neeeoowm'."""

    def __init__(self, max_delay):
        self.buf = np.zeros(int(max_delay) + 4, dtype=np.float64)
        self.wp = 0
        self.prev = 1.0

    def process(self, x, d_new):
        n = len(x)
        N = len(self.buf)
        wi = (self.wp + np.arange(n)) % N
        self.buf[wi] = x
        d_new = float(min(max(d_new, 1.0), N - 3))
        d = np.linspace(self.prev, d_new, n)        # smooth per-sample ramp
        self.prev = d_new
        idx = (self.wp + np.arange(n)) - d
        i0 = np.floor(idx).astype(np.int64)
        fr = idx - i0
        out = self.buf[i0 % N] * (1.0 - fr) + self.buf[(i0 + 1) % N] * fr
        self.wp = (self.wp + n) % N
        return out


# Flow-noise efficiency of the PHYSICAL voice (jet noise at the exit, the
# intake roar, the lip tones), the one constant it has: set so the F2004's
# tone-to-noise ratio under 2 kHz matches a real onboard recording at 17.5k
# rpm (12.4 / 9.4 dB real vs 18.0 / 9.1 dB here).  Lighthill's U^8 carries it
# to every other car and speed.
_PHYS_JET = 0.003
# Gear-mesh radiation of the physical voice (see _induction_audio): one
# constant, set so a loaded straight-cut box sits under the engine at full
# throttle as in a real F1 onboard recording (4-8 kHz ~2 % of the energy in a
# moving-car run, as in the recording; the old whine was levelled against the
# old voice's spike top end, ~30 dB hotter than a physical engine's).
_PHYS_GEAR = 0.03
# The physical voice's output level, in the synth's own units: without the
# classic voice's drive stages it leaves the chain ~40 dB under it (F2004 at
# full load: 0.002 against the classic's 0.2), below the reach of the AGC's
# gain ceiling.  A units constant, not voicing -- one gain on the whole mix,
# no ratio between any two sources moves.
_PHYS_MAKEUP = 100.0
# The airbox of the physical intake (see _induction's AIRBOX): its volume in
# engine displacements (race airboxes run 2-5x; over that range the firing
# orders reach the mouth 30-38 dB down either way) and its inlet duct's length.
_AIRBOX_VOL_X = 3.0
_AIRBOX_DUCT_M = 0.40


class _TrackSide:
    """Where the car is along the track, and which trackside post hears it.

    A fixed mic 12 m off the line, one every SPACING metres; the car drives
    past them.  The spacing is the distance it covers in ~10 s (never under
    200 m), so every pass has ~5 s of approach and ~5 s of getaway -- a real
    fly-by, not a quick 'neeow' every two seconds.  Posts are placed ahead of
    the car as it goes, from its speed at the time.

    Two positions matter and they are not the same:
      * EMISSION -- where the car is now.  Its radiation pattern is aimed at the
        post nearest to it (``advance`` returns the car's position relative to
        that post).
      * RECEPTION -- the sound arriving now left the car tau seconds ago, from
        further back.  tau solves c*tau = distance(X - v*tau, post), the
        RETARDED time, and its rate of change is exactly a moving source's
        Doppler, c/(c -/+ v).  The post hearing it is the one nearest to where
        it LEFT the car -- so at a post change both are equally far from the
        emission point and the delay is continuous.
    """

    C = 343.0
    T_CYCLE = 10.0                 # s per post: ~5 coming, ~5 going
    S_MIN, S_MAX = 200.0, 1000.0
    TAU_MAX = 2.5                  # s; the fly-by delay lines hold 3 s

    def __init__(self, x0=-60.0):
        self.X = x0                            # along the track (m)
        self.m_prev, self.m_cur, self.m_next = -self.S_MIN, 0.0, self.S_MIN

    def spacing(self, v):
        v = min(v, 0.8 * self.C)
        s = min(max(v * self.T_CYCLE, self.S_MIN), self.S_MAX)
        # the farthest delay is the approach from half a spacing out,
        # (S/2) / (c - v): keep it inside the delay line
        return min(s, 2.0 * self.TAU_MAX * (self.C - v))

    def advance(self, v, dt):
        """Move the car; return its position relative to its nearest post."""
        self.X += v * dt
        while self.X >= 0.5 * (self.m_cur + self.m_next):
            self.m_prev, self.m_cur = self.m_cur, self.m_next
            self.m_next = self.m_cur + self.spacing(v)
        return self.X - self.m_cur

    def retarded(self, v, h2, dx=0.0):
        """(tau, x_e) of the sound arriving NOW at the post that hears it:
        its delay in seconds, and where it LEFT the car along the track,
        relative to that post.  ``h2`` = the squared off-line distance
        (lateral^2 + height^2); ``dx`` = where on the car it comes from,
        along the direction of travel (+ ahead of mid-wheelbase).  Each point
        changes post as its own emission point crosses the midway, so every
        delay stays continuous."""
        v = min(v, 0.8 * self.C)
        a = self.C * self.C - v * v
        X = self.X + dx

        def tau(m):
            x = X - m
            return (-x * v + math.sqrt((x * v) ** 2 + a * (x * x + h2))) / a

        m = self.m_cur
        t = tau(m)
        if X - v * t < 0.5 * (self.m_prev + self.m_cur):
            m = self.m_prev          # it left while the previous post was nearer
            t = tau(m)
        elif X - v * t >= 0.5 * (self.m_cur + self.m_next):
            m = self.m_next          # a point ahead of the car, at crawl speed
            t = tau(m)
        return t, X - v * t - m


# --- the track itself -----------------------------------------------------------
# A trackside mic hears more than the car: the air in between (ISO 9613-1), the
# tarmac (the ground bounce), a barrier across the track, and the diffuse field
# of the whole place.  The place is a design choice, like the 12 m post; every
# effect of it is physics.
_TK_T_K, _TK_RH = 293.15, 60.0     # a 20 C, 60 % RH race day
_TK_WALL_M = 15.0                  # far-side barrier, this far beyond the line
_TK_WALL_R = 0.7                   # concrete, but only ~1 m of it faces the car
_TK_WALL_H = 1.0                   #   ...its height (m)
_TK_NEAR_M = 5.0                   # a facade behind the mic, this far behind it
_TK_NEAR_R = 0.6                   #   pressure reflection (openings, people)
_TK_NEAR_H = 4.0                   #   ...its height (m)
_TK_TYRE_DB = -12.0                # tyre/road noise at 270 km/h, re the auto-
_TK_WIND_DB = -16.0                # level's target; wind the same.  Together
                                   #   ~-22 dB of the whole at the pass, full
                                   #   load (Leo: very small)
_TK_RC_M = 100.0                   # direct = diffuse here, for a car in the
                                   #   middle of the scatterers (r << R_s)
_TK_JET_DB = 15.0                  # depth of the hot jet's zone of silence
                                   #   on its axis, high frequencies (heated
                                   #   jets: the axial region sits well under
                                   #   the off-axis lobe; checked against Leo's
                                   #   Aventador pass, s.8.7 of the plan)
_TK_RS_M = 50.0                    # the scatterers: grandstand, pit wall, marshal
                                   #   post, trees within ~50 m of the post
_TK_RT60 = 1.5                     # s: grandstands, pit buildings, tree lines


def _iso9613_db_per_m(f, T=_TK_T_K, rh=_TK_RH, pa=101.325):
    """ISO 9613-1 pure-tone atmospheric absorption in dB per metre.  Checked
    against ISO 9613-2 Table 2 (20 C / 70 %: 1 kHz 5.0, 4 kHz 22.9, 8 kHz 76.6
    dB/km; this gives 4.98, 23.1, 77.6 at the exact frequencies)."""
    pr, T0, T01 = 101.325, 293.15, 273.16
    C = -6.8346 * (T01 / T) ** 1.261 + 4.6151
    h = rh * (10.0 ** C) * pr / pa            # molar conc. of water vapour, %
    frO = pa / pr * (24.0 + 4.04e4 * h * (0.02 + h) / (0.391 + h))
    frN = pa / pr * (T / T0) ** -0.5 * (9.0 + 280.0 * h * math.exp(
        -4.170 * ((T / T0) ** (-1.0 / 3.0) - 1.0)))
    f2 = np.asarray(f, dtype=np.float64) ** 2
    return 8.686 * f2 * (
        1.84e-11 * (pr / pa) * (T / T0) ** 0.5
        + (T / T0) ** -2.5 * (
            0.01275 * math.exp(-2239.1 / T) / (frO + f2 / frO)
            + 0.1068 * math.exp(-3352.0 / T) / (frN + f2 / frN)))


class _AirFIR:
    """Air absorption over one path, as a linear-phase FIR re-designed every
    block from the ISO 9613-1 curve at that path's length.  The loss grows as
    ~f^2 per metre, which no fixed low-pass can follow: from 400 m it is -2 dB
    at 1 kHz and -36 dB at 8 kHz.  The old and new designs are cross-faded
    across the block, so a moving distance never zippers.  128 taps (within
    1 dB of the standard wherever it is above -40 dB, at 32-48 kHz): a
    constant 64-sample latency, the same on every path that has one."""

    M = 128

    def __init__(self, sr):
        f = np.fft.rfftfreq(self.M, 1.0 / sr)
        self._alpha = _iso9613_db_per_m(f)
        self._win = np.hanning(self.M + 1)[:self.M]     # periodic: peak at M/2
        self._hist = np.zeros(self.M - 1)
        self._h = None

    def _design(self, r):
        H = 10.0 ** (-self._alpha * r / 20.0)
        h = np.roll(np.fft.irfft(H, self.M), self.M // 2) * self._win
        return h / h.sum()                            # DC stays exactly 1

    def process(self, x, r):
        # distance buckets (0.5 m near, 2 % far): inside one the design is
        # the same to well under 0.1 dB, so it is neither re-built nor faded
        rq = round(r * 2.0) / 2.0 if r < 50.0 else round(math.log(r) * 50.0)
        xe = np.concatenate((self._hist, x))
        if self._h is not None and rq == getattr(self, "_rq", None):
            y = np.convolve(xe, self._h, mode="valid")
        else:
            h = self._design(r)
            y = np.convolve(xe, h, mode="valid")
            if self._h is not None:
                y0 = np.convolve(xe, self._h, mode="valid")
                y = y0 + np.linspace(0.0, 1.0, len(x)) * (y - y0)
            self._h = h
            self._rq = rq
        self._hist = xe[-(self.M - 1):]
        return y


class _MovingTaps:
    """One write, several read heads, each with its own RAMPING delay: the
    direct path, the ground image and the barrier image all read the SAME
    emitted sound at their own retarded time, so each carries its own Doppler.
    Each head is exactly a _FlybyDelay read."""

    def __init__(self, size, heads):
        self.buf = np.zeros(int(size) + 4, dtype=np.float64)
        self.wp = 0
        self.prev = [1.0] * heads

    def process(self, x, delays):
        n = len(x)
        N = len(self.buf)
        base = self.wp + np.arange(n)
        self.buf[base % N] = x
        # every head's ramp at once: numpy's linspace written out (start +
        # arange * step, the last sample exactly the end), so a head reads
        # the same samples it would alone
        d1 = np.array([float(min(max(d, 1.0), N - 3)) for d in delays])
        d0 = np.array(self.prev[:len(d1)], dtype=np.float64)
        if n > 1:
            D = np.arange(n, dtype=np.float64)[None, :] \
                * ((d1 - d0) / (n - 1))[:, None] + d0[:, None]
            D[:, -1] = d1
        else:
            D = d0[:, None] + np.zeros((1, n))
        self.prev[:len(d1)] = d1.tolist()
        idx = base[None, :] - D
        i0 = np.floor(idx).astype(np.int64)
        fr = idx - i0
        Y = self.buf[i0 % N] * (1.0 - fr) + self.buf[(i0 + 1) % N] * fr
        self.wp = (self.wp + n) % N
        return list(Y)


class _OutdoorField:
    """The track's own reverberation -- grandstand faces, pit buildings,
    barriers and tree lines tens of metres apart -- as a diffuse field.

    Eight delay lines of 47-179 ms, an energy-preserving Hadamard feedback,
    loop gains from RT60, and every pass round a loop loses the air's ISO
    9613-1 high end over that loop's own path length (a one-pole fitted at
    4 kHz), so the tail darkens as it decays, as outdoor tails do.  Every
    delay is longer than a block, so it runs a block at a time.  Scaled to a
    unit energy gain (within 1 dB for an engine-like pink spectrum): the
    caller sets its level."""

    D_MS = (47.0, 61.0, 73.0, 89.0, 103.0, 127.0, 151.0, 179.0)

    def __init__(self, sr, rt60=_TK_RT60):
        self.d = [max(int(ms * 1e-3 * sr), BLOCK + 1) for ms in self.D_MS]
        n = len(self.d)
        self.g = np.array([10.0 ** (-3.0 * d / (sr * rt60)) for d in self.d])
        self.lines = [np.zeros(d, dtype=np.float64) for d in self.d]
        H = np.array([[1.0]])
        while H.shape[0] < n:
            H = np.block([[H, H], [H, -H]])
        self.H = H / math.sqrt(n)
        self.c = np.array([1.0 if i % 2 == 0 else -1.0
                           for i in range(n)]) / math.sqrt(n)
        a4 = float(_iso9613_db_per_m(np.array([4000.0]))[0])
        w = 2.0 * math.pi * 4000.0 / sr
        self.p = []
        for d in self.d:
            L2 = (10.0 ** (-a4 * 343.0 * d / sr / 20.0)) ** 2
            if L2 >= 1.0 - 1e-12:
                self.p.append(0.0)
                continue
            # one-pole (1-p)/(1 - p z^-1) with |H(4 kHz)|^2 = L2
            B = 1.0 - L2 * math.cos(w)
            self.p.append((B - math.sqrt(max(B * B - (1.0 - L2) ** 2, 0.0)))
                          / (1.0 - L2))
        self.zi = [np.zeros(1) for _ in self.d]
        self.norm = math.sqrt(1.0 - float(np.mean(self.g)) ** 2)

    def process(self, x):
        n = len(x)
        Y = np.empty((len(self.d), n))
        for i, line in enumerate(self.lines):
            y = line[:n]
            if _HAVE_SCIPY and self.p[i] > 0.0:
                p = self.p[i]
                y, self.zi[i] = lfilter([1.0 - p], [1.0, -p], y, zi=self.zi[i])
            Y[i] = y
        out = self.c @ Y
        Z = self.H @ (self.g[:, None] * Y) + x[None, :]
        for i in range(len(self.d)):
            self.lines[i] = np.concatenate((self.lines[i][n:], Z[i]))
        return out * self.norm


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

    def __init__(self, simulator, sample_rate: int = None, device=None,
                 seed=None):
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
        # PER-CYLINDER FIRING LAMPS, lit by the firing offsets the audio is
        # actually using rather than by a timer that happens to look similar.
        # So a V12 shows its two banks alternating and a rotary shows three,
        # because that is what the engine's own offsets say.
        self.cylinder_light = np.zeros(ncyl, dtype=np.float64)

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
        self._gp_grid = None      # gas-solver pulse LUT, baked on first use

        # Fixed per-cylinder 'personality' (runner-length / build differences):
        # each cylinder fires with a slightly different pitch and loudness, which
        # is what gives a multi-cylinder exhaust its rich, layered waveform.
        # A fixed seed makes a render bit-exact reproducible, which is what
        # lets a reimplementation be compared against this one stage by
        # stage instead of by ear (see tools/golden.py).
        rngf = np.random.default_rng(20240517)
        self._cyl_tau = rngf.uniform(-1.0, 1.0, ncyl)   # decay -> pop pitch/brightness
        self._cyl_amp = rngf.uniform(-1.0, 1.0, ncyl)   # loudness

        # A seed means someone is comparing this render against another
        # implementation, so use the portable generator; unseeded, ship
        # numpy's.
        self._rng = (PortableRNG(seed) if seed is not None
                     else np.random.default_rng())
        # full-resolution stage capture for the golden reference; the UI's
        # scopes use the decimated path below
        self.capture_stages = False
        self._stage_full = {}
        self._jit = np.ones(ncyl)
        self._tjit = np.zeros(ncyl)   # per-cylinder firing-phase scatter (deg)
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
            "dry": 0.80,         # direct through-wave level (Leo-tuned by ear:
                                 #   0.62 too mushy, 0.92 a touch bangy — 0.80
                                 #   keeps the pipe field dominant with the bang
                                 #   present).  Live slider.
            "res1": 0.42,         # primary pipe resonance (runner) — fallback only,
            "res2": 0.75,         # normally overridden by exhaust_tmm geometry;
                                  #   re-anchored so the standing-wave field rivals
                                  #   the through-wave (real duct 6-12 dB ripple)
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
            "reverb": 0.21,      # spatial reverb mix — an exhaust mic is OUTDOORS
            "rad_near": 0.20,     # radiation near/far-field blend (slider):
                                  #   Leo's chase calibration = 0.20; the POV
                                  #   adds its range offset on top
                                 #   in free field; 0.4 was a small room, far too wet
            "intake": 0.11,       # induction roar level (halved — was too windy)
            "eq_low": 0.0,        # dB
            "eq_mid": 0.0,        # dB
            "eq_high": 0.0,       # dB
            "presence": 0.0,      # dB — guitar-amp 'presence' (upper-mid bite ~3 kHz)
            "cyl_spread": 0.5,    # how much each cylinder's pitch/level differs
            "master": 0.6,        # master output volume
            "spatial_x": 0.5,     # stereo pan: 0 left .. 1 right
            "spatial_y": 0.85,    # distance: 0 far (dark/quiet) .. 1 near.  0.6
                                  #   kept a permanent 9.4 kHz LP + -4.5 dB on
                                  #   EVERYTHING (a big hidden muffle); the POV
                                  #   stage now owns distance, so default near
            "super_vol": 0.6,     # mechanical supercharger (roots/centrifugal) whine
            "turbo_vol": 0.40,    # turbo spool whistle + BOV/flutter.  0.21 was
                                  #   calibrated pre-POV when induction injected
                                  #   into the full exhaust bus; through the bay
                                  #   bright path (1/r + leak) it needs ~2x —
                                  #   Leo: flutter buried, turbo too quiet
            "gearbox_vol": 0.375, # straight-cut gearbox whine (was 0.5 -> 75%)
            "wall_thickness": 0.3,  # pipe-wall thickness: higher = duller, less 'trumpet'
            "shear": 0.08,        # tail-pipe air-shear roar at the exit (mass-flow)
                                  #   — part of the un-pitched noise share a real
                                  #   engine carries (the fixed underlay)
            "whine": 1.0,         # high-rpm standing-wave whine/scream amount
            "valve_open": 1.0,    # how far the active exhaust valve opens at revs
            "muffler": 1.0,       # muffler internal-reflection (comb) depth
            "cyl_voice": 1.0,     # per-cylinder voicing amount (0 = perfectly uniform)
            "road_noise": 0.10,   # tyre/road rumble that swells with road speed (subtle)
            "gear_grain": 1.0,    # gear-driven valvetrain whir mix (x eng.gear_grain)
            "mech": 0.16,         # valvetrain tick layer (cam/tappet clatter)
            "gear_mesh": 0.10,    # transmission mesh whine under load (helical, subtle)
            "spool_reverb": 0.30, # induction-side reverb: the intercooler core
                                  #   + charge piping is a big chamber — the
                                  #   whine, BOV *and flutter* all ring it
                                  #   (x2 per Leo's reverb audit)
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
        self._phys_spread = 0.025 / 0.55     # EFI metering scatter (below)
        try:
            _disp = max(c0.displacement, 1e-6)                       # m^3 / cylinder
            _cr = max(getattr(c0, "compression_ratio", 10.5) or 10.5, 5.0)
            _mps = 2.0 * c0.stroke * max(simulator.engine.redline_rpm, 1000.0) / 60.0
            self.params["crack"] = min(max(0.17 * self._bd_sharp ** 0.6, 0.07), 0.40)
            self.params["body"] = min(max(1.60 * (_disp / 5.415e-4) ** 0.30, 1.0), 2.6)
            self.params["turbulence"] = min(max(0.34 * (_mps / 21.65) ** 0.5, 0.18), 0.5)
            self.params["drive"] = min(max(0.40 * (_cr / 11.8) ** 0.5, 0.25), 0.6)
            # CYLINDER SPREAD from the build, not taste: carb / mechanical
            # race injection meters each cylinder differently (±6-8 % scatter)
            # where modern EFI holds ±2-3 %; unequal-length headers add
            # acoustic per-runner differences on top.
            _eng = simulator.engine
            _inj = getattr(_eng, "injection", "port")
            self.params["cyl_spread"] = min(
                0.55 + (0.25 if _inj in ("carb", "mech") else 0.0)
                + (0.20 if getattr(_eng, "header_unequal_deg", 0.0) > 0.0
                   else 0.0), 1.0)
            # ...but that number is a PERSONALITY (+-41 % loudness, +-71 %
            # decay on an Aventador), 15-20x the metering scatter above.  What
            # the EXHAUST pulses really differ by is the metering: +-2.5 % EFI,
            # +-7 % carb / mechanical (amp = 1 + 0.55 * spread * U(-1, 1)).
            # The personality belongs to the block and intake (_struct_gain).
            self._phys_spread = (0.07 if _inj in ("carb", "mech")
                                 else 0.025) / 0.55
            # EXPLOSION (port/head cavity) REVERB from the port volume: a big
            # cylinder's exhaust port + header entry is a bigger chamber.
            _cyl_l = (_eng.total_displacement * 1000.0) / max(
                _eng.num_cylinders, 1)
            # x2 per Leo's ear: the port/head cavity carries far more
            # reverberant energy than first modelled
            self.params["src_reverb"] = min(0.40 + 0.24 * min(_cyl_l / 0.70,
                                                              1.2), 0.68)
            # INTAKE ROAR level from the induction hardware: an exposed race
            # airbox / ITB trumpet set breathes loud; a filtered plenum is shy.
            # exposed-airbox bump keyed to TRUE race builds (dog box), not
            # exhaust openness — an Aventador has an open pipe but a FILTERED
            # airbox (Leo's 0.11 calibration)
            self.params["intake"] = min(0.10
                                        + (0.08 if _eng.straight_cut else 0.0)
                                        + (0.06 if getattr(
                                            _eng, "individual_throttle", False)
                                           else 0.0), 0.24)
        except Exception:
            pass

        self._build_audio()

        # Listener PERSPECTIVE (white-box, racing-game style): "chase" = the chase
        # cam a few metres behind the car (tailpipe is the direct voice, the bay
        # arrives shadowed by the body + later), "cockpit" = the driver's seat
        # (bay through the firewall is the direct voice, exhaust from behind
        # through the rear partition + the cabin's standing-wave boom).  EVERY
        # gain / delay / cutoff derives from geometry + panel physics in
        # _pov_geo() — no hand-tuned listen coefficients.  The old `cabin` flag
        # is kept as a compatibility alias (property below).
        self.pov = "chase"
        self._pov_cache = None    # (key, dict) memo of the derived DSP constants
        self._pov_buf = {}        # named fixed delay lines (path-difference)
        self._pov_zi = {}         # named filter states for the partition LPs
        self._bay_prev = 0.0      # no-scipy fallback lid state
        self._boom_zi = np.zeros(2)   # cabin standing-wave resonator state
        # VOICING BISECT flags (F1-F7 in-app toggle one each, F8 flips all):
        # the recent voicing batch shipped several structural changes without
        # per-step ear validation; these gate each one LIVE so the ear can
        # bisect which serve the sound and which broke it.  All default ON.
        self.vx = dict(series_wg=True, sys_helm=True, rumble=True, asym=True,
                       engine_series=True, rad_hp=True, noise=True,
                       bipolar=True,   # F9: AC-couple the source pulses
                       vacuum=False,   # F10: deep-vacuum overrun — Leo prefers
                                       # the arcade lift-off bark as DEFAULT;
                                       # F10 turns the physical quiet ON
                       gas_pulse=False,  # the gas solver's own pulse SHAPE
                                         # (experiment; see _gas_pulse_at)
                       phys_voice=bool(getattr(simulator.engine,
                                               "phys_voice", False)),
                                         # F11: the PHYSICAL voice -- solver
                                         # pulse, no synthesizer layers; on by
                                         # default where the preset asks
                       cyl_split=False) # the exhaust merges the cylinders
                                        # (fuel-metering scatter only); the
                                        # block and intake carry each one's own
                                        # path.  PART OF THE PHYSICAL VOICE
                                        # (on with it): the classic voice's
                                        # narrow pulses need the per-cylinder
                                        # spread to soften their top end --
                                        # forced on alone it turned every car
                                        # shrill and let the fizz up
        self._bip_zi = {}         # per-channel AC-coupling filter states
        # LAYER VISIBILITY -- one switch per stage, like the eye column in an
        # image editor.  Hiding a layer passes its input straight through, so
        # you hear exactly what that stage contributes; it is the only honest
        # way to answer "is this stage earning its place?".
        self.stage_on = {nm: True for nm in self.STAGES}
        self._bus_prev = {}       # last output per bus, for the bypass above
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
        _ts = getattr(simulator, "turbo", None)
        if _ts is not None:       # the machine's own valve sets the defaults
            self.flutter = _ts.air.bov_mode == "none"
            self.ssqv = _ts.air.bov_mode == "atmo"
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
        self._pop_budget = 0      # bangs LEFT in this lift (the pipe empties)
        self._pop_on_gas = False  # for spotting the on-gas -> lift EDGE
        self.pops_fired = 0       # bangs actually let off, for a UI to read
        # KEEP THE NOTE UP ON A LIFT.  0 = physical (shut throttle, quiet
        # engine); 1 = the loudness paths are told the engine is still on
        # load.  Deliberately not physical, and off by default so the model
        # stays honest unless something asks for it.
        self.sustain_on_lift = 0.0
        self.o_chord = False      # hidden 'o' easter egg: turbo V7 + Bdim blow-off
        self._bdim_phase = 0.0    # Bdim blow-off oscillator phase
        self.fire_chord = 0       # firing-body chord voicing index (hidden keys 1-6)
        self._prev_throttle = 0.0 # for blow-off-valve detection
        self._thr_ref = 0.0       # slowly-decaying recent-throttle peak (lift detect)
        self._bov_env = 0.0       # blow-off-valve 'pshhh' envelope
        self._bov_prev = 0.0      # stock-recirc dark-noise low-pass state
        self._lock = threading.Lock()
        self._stream = None
        self._sink_run = False        # generic sink feeder thread (see _start_sink)
        self._sink_thread = None
        self._sink_blocks = 0
        # fraction of real time spent rendering: the number that says whether a
        # dropout is overload (load near 1) or mere jitter (load low, but the
        # sink still starved).  Read it, do not guess.
        self.load = 0.0
        self._pg_run = False          # pygame.mixer feeder thread (Android backend)
        self._pg_thread = None
        self.latency_ms = 0.0
        self.mode = "off"
        # Optional output SINK: any callable taking one (frames, 2) float32
        # array.  Set it before start() and the synth renders into it instead
        # of opening a sound device -- that is the seam an iOS AVAudioEngine
        # backend, a bare-ALSA Pi or a recorder plugs into (see _start_sink).
        self.sink = None
        self.prefer_exclusive = os.environ.get("ENGINE_SIM_EXCLUSIVE") == "1"
        # Host output buffer (seconds).  Generous by default because a
        # heavy pure-Python frame must never underrun the audio; car mode
        # has no frame to draw and asks for much less.
        self.host_latency = 0.06

    # --------------------------------------------------------- rate-dependent
    def _build_audio(self):
        eng = self.sim.engine
        sr = self.sample_rate
        # how many separate exhaust channels, and which channel each cylinder is on
        self._nchan = max(1, int(eng.exhaust_channels))
        if self._nchan == 1:
            self._channel_of = [0] * eng.num_cylinders
        elif getattr(eng, "exhaust_grouping", "bank") == "firing":
            # a CROSS-BANK manifold: alternate firings share a collector, so
            # each sees evenly spaced pulses from both banks (BMW S63)
            rank = sorted(range(eng.num_cylinders),
                          key=lambda i: eng.cylinders[i].cycle_offset_deg % 720.0)
            self._channel_of = [0] * eng.num_cylinders
            for slot, i in enumerate(rank):
                self._channel_of[i] = slot % self._nchan
        else:
            self._channel_of = [0 if c.bank_angle_deg < 0 else 1 for c in eng.cylinders]
        # Unequal-length headers: delay one bank's pulses a few crank-degrees so
        # the even firing arrives UNEVENLY -> the Subaru boxer rumble.
        hu = eng.header_unequal_deg
        self._header_offset = [hu if c.bank_angle_deg < 0 else 0.0
                               for c in eng.cylinders]
        # the physical voice takes a bank offset only where a preset states a
        # MEASURED one: header_unequal_deg is mostly a voicing choice of the
        # classic voice (the Aventador's 9-deg rasp, 14 deg on every dual-
        # exhaust V6 / flat-6, 18 on every cross-plane V8 -- bank character
        # kept audible in mono).  Real unequal-length headers reach it as a
        # LENGTH (header_unequal_m, below), a time delay.
        hp = getattr(eng, "header_unequal_phys_deg", -1.0)
        hp = 0.0 if hp < 0.0 else hp
        self._header_offset_phys = [hp if c.bank_angle_deg < 0 else 0.0
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
        for j in range(eng.num_cylinders):
            ch = self._channel_of[j]              # position along its collector
            posn.append(seen.get(ch, 0)); seen[ch] = seen.get(ch, 0) + 1
        # store each runner's LENGTH (m); the per-block delay is L / c(live) so the
        # whole manifold interference pattern breathes with exhaust temperature.
        self._runner_len = []
        for j, c in enumerate(eng.cylinders):
            ch = self._channel_of[j]
            frac = posn[j] / max(seen[ch] - 1, 1)        # 0 (near) .. 1 (far)
            self._runner_len.append(prim * (1.0 - spread * 0.5 + spread * frac))
        # the physical voice: real unequal-length headers add their length to
        # the long bank's runners (the delay is L / c, a time)
        du = max(getattr(eng, "header_unequal_m", 0.0), 0.0)
        self._runner_len_phys = [
            L_ + (du if eng.cylinders[j].bank_angle_deg < 0 else 0.0)
            for j, L_ in enumerate(self._runner_len)]
        maxd = int(max(self._runner_len_phys) / 380.0 * sr) + BLOCK + 8
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
        self._wg = [(ExhaustWaveguide(), ExhaustWaveguide(), ExhaustWaveguide())
                    for _ in range(self._nchan)]   # runner / mid-section / full
        self._reverb = Reverb(sr)
        # COCKPIT space: the car interior is a ~2.4 m cavity with heavily absorbent
        # trim -> comb path lengths scaled to the cabin (room ~ 0.22 of the default
        # hall) and a short RT (feedback from trim absorption).  Used instead of the
        # outdoor reverb when the listener perspective is "cockpit".
        self._cab_verb = Reverb(sr, room=0.22, feedback=0.42)
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
            self._chord_zi = [np.zeros(2) for _ in range(6)]
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
            # knee softened (fleet audit: (rl-6500)/3500 hard-ZEROED the whine
            # for all 26 cars with redlines <= 6500 — a cliff, not physics; a
            # 6000 rpm muscle V8 still carries a little pipe whistle)
            rev = min(max((eng.redline_rpm - 5000.0) / 5000.0, 0.0), 1.25)
            bore = min(max((0.028 - r) / 0.011, 0.0), 1.0)
            self._whine_amt = min(rev * (0.55 + 0.30 * bore
                                         + 0.25 * eng.exhaust_openness), 0.72)
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
            # knee softened (fleet audit: (cyl_l-0.30)*12 hard-ZEROED thunder
            # for 8 small-cylinder cars — same defect class as the F1 rumble)
            g_thunder = min(max((cyl_l - 0.16) * 8.5, 0.0), 7.5)
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
        # hot-gas speed of sound WITH THERMAL INERTIA: the instantaneous EGT
        # follows load within a block, but the PIPE's resonances are set by the
        # whole gas column + the metal's heat capacity — seconds, not
        # milliseconds.  Un-smoothed, a throttle blip jumped c 494 -> 603 m/s
        # in one block and every pipe resonance pitch-bent +22 % instantly
        # (Leo: "物理反应不对" — the rubber-band pipe).  tau ~ 3 s.
        c_now = sim.exhaust_sound_speed()
        if not hasattr(self, "_c_sm"):
            self._c_sm = c_now
        self._c_sm += (c_now - self._c_sm) * min(BLOCK / sr / 3.0, 1.0)
        c = self._c_sm
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
        # mostly rpm-driven — a real active flap follows rpm/back-pressure maps
        # and many stay OPEN on decel (the burble path).  0.30 throttle weight
        # slammed a whole brightness step shut on every lift (Leo's "是不是
        # 某个阀门关闭" — yes, this one, partially).
        drive = min(rpm_frac + 0.15 * min(max(self.sim.throttle, 0.0), 1.0), 1.0)
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
        lp_a = math.exp(-2 * math.pi * fc / sr)

        # ---- UNIFIED SYSTEM DAMPING (整体调音感): ONE resonant-character
        # number for the whole system, derived from the hardware — every
        # resonant stage (waveguides, standing-wave whine, wall formants,
        # chamber modes) reads its Q from THIS.  A straight-pipe car rings
        # sharp and long EVERYWHERE; a packed stock box is uniformly warm and
        # damped — one coherent personality instead of each module ringing to
        # its own taste.  The exhaust VALVE flips between two whole-system
        # characters (not just a brightness knob).
        absorptive = getattr(eng, "muffler_type", "reflective") == "absorptive"
        sysq = (0.35 + 0.45 * eng.exhaust_openness
                + 0.10 * min(max(qf - 1.0, -1.0), 2.0)   # wall material sing
                - (0.18 if absorptive else 0.0)          # packing soaks all Qs
                - 0.10 * min(l_total / 4.0, 1.0)         # long runs damp
                + 0.06 * min(rad / 0.035, 1.5))          # fat bore: less wall
        sysq = min(max(sysq + 0.20 * valve, 0.15), 1.0)
        self._sysq = sysq

        # ---- PER-MODE RADIATION-IMPEDANCE BACK-REACTION: an open end reflects
        # |R| ~ 1-(ka)^2/2, so each waveguide's OWN fundamental sees its OWN
        # end reflection — LF can't escape (strong reflection -> strong, long
        # ring), HF radiates away (weak reflection -> fast decay), and the TIP
        # DIAMETER shapes the whole system response, not just its brightness.
        a_tip = rad * max(getattr(eng, "tip_scale", 1.0), 0.5)
        l_mid = l_primary + 0.45 * (l_total - l_primary)  # collector->muffler
        D3 = round(2.0 * l_mid * sr / c)

        def _rend(fq):
            ka_m = 2.0 * math.pi * fq * a_tip / c
            return min(max(1.0 - 0.45 * ka_m * ka_m - 0.12 * ka_m, 0.45), 1.0)

        # firing-centroid escape term (why a real F1 is DRY, not an organ pipe)
        fire_hz = max(self.sim.rpm, 1.0) / 120.0 * eng.num_cylinders
        ka = 2.0 * math.pi * fire_hz * a_tip / c
        g *= max(1.0 - 0.5 * ka * ka, 0.55)

        # ---- JUNCTION REFLECTION (为什么真管子不是风琴管).  A waveguide only
        # rings as hard as its far end SENDS BACK, and only the last section
        # actually ends at the open air.  The primary ends at the COLLECTOR and
        # the mid section ends at the MUFFLER, and at an area step S1 -> S2 a
        # plane wave reflects |R| = (S2-S1)/(S2+S1) and TRANSMITS the rest on
        # down the system.  Scoring all three ends with the open-end loss is
        # what made every pipe reflect ~98% per pass: round-trip gains came out
        # 0.92-0.99 and the comb stood 21-37 dB above its own median, i.e. an
        # organ pipe.  That was the buzz.
        #
        # Collector area: a merge is sized between the two design limits --
        # equal to one primary if the pulses never overlap, n primaries if they
        # fully merge -- so the geometric mean sqrt(n) is the working figure,
        # and it matches real headers (1.75" 4-into-1 -> 2.5" collector is an
        # area ratio of 2.04 against sqrt(4) = 2.0).  On top of that every
        # system steps up in pipe size going aft, worth about 1.2 in area, and
        # that step is the only reflector when each cylinder has its own runner.
        n_per = eng.num_cylinders / max(int(eng.exhaust_channels), 1)
        ar_coll = 1.2 * math.sqrt(max(n_per, 1.0))
        r_coll = (ar_coll - 1.0) / (ar_coll + 1.0)

        # Muffler inlet: the box is a big expansion and therefore a genuinely
        # strong reflector -- that IS the mechanism a reflective muffler works
        # by, so this end stays ringy and keeps the body note.  A straight-
        # through absorptive box does not step the area at all; its perforated
        # tube couples to the packing instead, so most of that reflection is
        # simply not there.
        v_box_j = max(getattr(eng, "muffler_volume_m3", 0.003), 1e-5)
        l_box_j = max(eng.muffler_neck_len_m * 4.0, 0.15)
        s_pipe = math.pi * rad * rad
        ar_box = max(v_box_j / l_box_j / s_pipe, 1.0)
        if absorptive:
            ar_box = 1.0 + 0.25 * (ar_box - 1.0)
        r_box = (ar_box - 1.0) / (ar_box + 1.0)

        g1 = min(g * r_coll, 0.995)
        g3 = min(g * r_box, 0.995)
        # The full-system loop runs valve -> tip -> valve, so it has to get PAST
        # the collector and the box, once each way.  Crossing an area step both
        # ways leaves (1 - R^2) of the amplitude -- and that is not a detail: a
        # reflective muffler IS a device for stopping waves getting through, so
        # a stock box drops this loop to a third while a straight pipe barely
        # touches it.  Only the tip itself is scored with the open-end loss,
        # because only the tip is actually open.
        thru = (1.0 - r_coll * r_coll) * (1.0 - r_box * r_box)
        g2 = min(g * _rend(c / (4.0 * l_total)) * thru, 0.995)

        # ---- IN-PIPE REVERB NETWORK (管内混响) — the system's own tail, not
        # an effect.  RT60 derives from the hardware: a big stock box STORES
        # energy and releases it slowly (long, dark tail, 0.2-0.35 s); a
        # straight-through pipe radiates immediately (short, dry, 0.05-0.1 s);
        # packing shortens everything.  EVERY network feedback derives from it
        # via the comb relation g = 10^(-3D / (RT60*sr)) — one reverb
        # personality per car.
        v_box = max(getattr(eng, "muffler_volume_m3", 0.003), 1e-5)
        rt60 = (0.06 + 0.24 * (1.0 - eng.exhaust_openness)
                + 0.05 * min(v_box / 0.004, 1.0))
        if absorptive:
            rt60 *= 0.75
        self._rt60 = min(max(rt60, 0.05), 0.35)
        # extra REFLECTION POINTS: the catalyst brick mid-run + the box's
        # front/rear chambers.  Chambers are closed-ish -> NON-inverting
        # (half-wave) modal families that interleave with the quarter-wave
        # pipe series = the dense wall.
        l_cat = l_primary + 0.35 * (l_total - l_primary)
        l_box = max(eng.muffler_neck_len_m * 4.0, 0.15)
        self._rvD = (round(2.0 * l_cat * sr / c),
                     round(2.0 * l_box * 0.42 * sr / c),
                     round(2.0 * l_box * 0.58 * sr / c))
        self._rvG = tuple(min(10.0 ** (-3.0 * D / (self._rt60 * sr)), 0.985)
                          for D in self._rvD)
        # the HF reverb time is FAR shorter than LF (open end + fibre eat the
        # top): the in-loop pole sets RT_HF/RT_LF
        self._rv_lp = math.exp(-2.0 * math.pi
                               * (1400.0 + 3400.0 * eng.exhaust_openness) / sr)
        # tail-end FREQUENCY-DEPENDENT reflection for the full-system guide:
        # lows re-ring (|R| -> 1) while highs escape out the tip — an extra
        # in-loop pole at the tip's radiation corner, so the LF tail hums long
        # and the top stays clean.
        fc_end = 0.7 * c / (2.0 * math.pi * a_tip)
        self._lp_a_end = math.exp(-2.0 * math.pi * min(fc_end, fc) / sr)

        # Helmholtz muffler/chamber resonance
        A, V = eng.muffler_neck_area_m2, eng.muffler_volume_m3
        r_neck = math.sqrt(A / math.pi)
        l_h = eng.muffler_neck_len_m + 1.7 * r_neck
        f_helm = (c / (2 * math.pi)) * math.sqrt(A / (V * l_h))
        f_helm = min(max(f_helm, 40.0), 400.0)
        return D1, D2, D3, g1, g2, g3, lp_a, f_helm

    # ------------------------------------------------------- synthesis core
    #: The layer stack: every stage that can be switched off, in chain order.
    #: The source taps (pulses / bang / fizz) and the final `output` are not
    #: here -- the first three ARE the excitation and the last has nothing
    #: after it, so neither has an input to fall back to.
    # params each car derives from its own engine in __init__ (geometry, bore/
    # stroke, fuel system) -- not mixer settings; see App._make_synth
    PER_CAR_PARAMS = ("res1", "res2", "wall_thickness", "muffler", "crack",
                      "body", "turbulence", "drive", "cyl_spread",
                      "src_reverb", "intake")

    STAGES = ("voiced", "block", "pipes", "header", "head/port", "catalytic",
              "standing-wave", "resonator", "muffler", "valve bypass",
              "induction+gears", "wall de-honk", "metal ring", "megaphone",
              "thunder", "reflection", "radiation", "tailpipe exit", "EQ",
              "cabin/room")

    def _tap(self, name: str, sig, bus: str = "exhaust"):
        """Snapshot one stage boundary -- and honour that layer's switch.

        Each bus remembers what came out of its previous stage, so a hidden
        layer simply returns that: its contribution vanishes from the mix and
        everything downstream carries on as if it were not fitted.  Hiding
        `muffler` really is listening to the car without a muffler.

        The stage has already run by the time we get here, which is the point:
        its filters keep their history, so un-hiding never clicks."""
        if sig is None or not len(sig):
            return sig
        prev = self._bus_prev.get(bus)
        if (prev is not None and len(prev) == len(sig)
                and not self.stage_on.get(name, True)):
            sig = prev
        self._bus_prev[bus] = sig
        if self.capture_stages:
            self._stage_full.setdefault(name, []).append(
                np.asarray(sig, dtype=np.float64).copy())
        if self.scope_enabled:
            step = max(1, len(sig) // 96)
            self._stage_taps[name] = sig[::step][:96].astype(np.float64).copy()
        return sig

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

    # --------------------------------------- listener perspective (white-box)
    @property
    def cabin(self):
        """Compatibility alias: the old in-cabin toggle now IS the cockpit POV."""
        return self.pov == "cockpit"

    @cabin.setter
    def cabin(self, v):
        self.pov = "cockpit" if v else "chase"
        self._pov_cache = None

    def _pov_geo(self):
        """DSP constants for the current perspective — every number DERIVED:

          * spherical spreading      g = r_near / r          (1/r free field)
          * path-difference delay    dt = (r - r_near) / c   (c = 343 m/s)
          * panel transmission       composite partition: a fraction ``alpha`` of
            the boundary is OPENINGS (footwell holes, shifter boot, underbody gap
            — flat, un-filtered leak) and the rest is sheet metal obeying the
            MASS LAW  TL = 20*log10(f*m) - 47 dB (m = surface density kg/m^2),
            i.e. +6 dB/oct — a 1st-order low-pass whose corner is the TL = 20 dB
            point:  fc = 10^(67/20) / m = 2239/m Hz.
          * cabin boom               lowest longitudinal cavity mode f = c/(2*L),
            L ~ 2.4 m interior -> ~71 Hz standing wave.
          * ground reflection        chase cam: tarmac bounce arrives later by
            the geometric path difference -> a comb; delta =
            sqrt(d^2+(hs+hr)^2) - sqrt(d^2+(hr-hs)^2), |R|~0.8 asphalt.

        A RACE interior (straight-cut box or near-open exhaust) is stripped: no
        deadening (bare 0.8 mm steel, m ~ 6.3) and big openings (alpha up) — so
        a race cockpit is bright and violent, a luxury one hushed, from physics.
        """
        eng = self.sim.engine
        race = self.straight_cut or getattr(eng, "exhaust_openness", 0.6) > 0.85
        onboard = self.pov == "cockpit" and getattr(eng, "open_cockpit", False)
        key = (self.pov, race, onboard)
        if self._pov_cache is not None and self._pov_cache[0] == key:
            return self._pov_cache[1]
        c, sr = 343.0, self.sample_rate
        fc_mass = lambda m: 2238.7 / m           # mass-law TL=20 dB corner
        if onboard:
            # ONBOARD CAMERA on a single-seater's roll hoop -- open air, so no
            # partition, no cabin boom, no shell radiation, no cabin room.
            # From the geometry: the airbox inlet 0.35 m away (just below and
            # ahead), the engine 0.8 m (behind the driver's back), the exits
            # 1.4 m (1.2 m back, 0.5 m down).  Spherical spreading and path
            # delays relative to the nearest (the intake).  The exits point
            # back and ~30 deg up, the camera sits ahead of them: cos = -0.6 to
            # the pipe axis; the inlet faces forward, the camera is above and a
            # little behind it (near field): cos ~ -0.3.
            # (the gearbox and any turbo/blower housings sit at the back of
            # the engine, ~1.3 m -- NOT at the airbox mouth: only the mouth's
            # share of the intake bus is 0.35 m away)
            r_int, r_bay, r_tail, r_box = 0.35, 0.8, 1.4, 1.3
            geo = dict(
                g_bay=1.0, g_tail=r_bay / r_tail, g_int=r_bay / r_int,
                g_box=r_bay / r_box,
                d_int=0, d_bay=int((r_bay - r_int) / c * sr),
                d_tail=int((r_tail - r_int) / c * sr),
                bay_alpha=1.0, bay_fc=fc_mass(6.3),
                tail_alpha=None, tail_fc=None,
                struct=0.0, struct_fc=800.0, chassis=0.0, chassis_fc=90.0,
                boom_f=0.0, ground=None, onboard=True,
                cos_tail=-0.6, cos_int=-0.3)
        elif self.pov == "cockpit":
            r_bay, r_tail = 1.5, 3.2             # head->engine / head->tail exit
            # ``alpha`` = the measured WHOLE-BODY noise-reduction floor, not the
            # ideal-panel opening area: a real body underperforms the mass law
            # badly in the low-mid band (panel resonances, glass coincidence,
            # pass-throughs, and the exhaust run RIGHT UNDER the floor pan), so
            # the flat leak comes from measured vehicle-interior NR via
            # alpha = 10^(-NR/20).  NR is a PER-CAR physical attribute
            # (eng.cabin_nr_db): ~20+ dB a sealed luxury saloon, ~13 dB a thin-
            # shelled sports car (mid-engine: the bay is right behind your head),
            # ~6 dB a stripped/open race shell (an F1 cockpit is barely enclosed).
            # NR defaults are SPORTS-car numbers, not saloon NVH targets: this
            # roster is supercars/race cars whose makers pipe the sound IN on
            # purpose (sound symposers, induction ducts at the cowl, the bay
            # right behind a mid-engine driver's head) — ~8 dB effective.  A
            # stripped/open race shell barely attenuates at all (~3 dB).  A
            # sealed luxury saloon can still set eng.cabin_nr_db ~ 20+.
            # racing-game cockpit reference = the driver's HELMET EAR in a car
            # whose maker WANTS the engine heard (symposer ducts, mid-engine bay
            # at the bulkhead, race shells with no trim at all): effective
            # engine-band NR ~5 dB sports / ~2 dB race once every flanking path
            # (windows, vents, structure) is summed.
            nr = getattr(eng, "cabin_nr_db", 0.0) or (2.0 if race else 5.0)
            a_fw = min(10.0 ** (-nr / 20.0), 0.9)
            m_fw = 6.3 if race else 11.0                          # firewall panel
            m_rr, a_rr = (6.3 if race else 13.0), a_fw * 0.95     # floor + bulkhead
            # STRUCTURE-BORNE path — the DOMINANT in-cabin path in real cars:
            # engine mounts + driveline + exhaust hangers all shake the shell,
            # and the panels re-radiate INSIDE.  Summed over every mount point
            # the structure-borne contribution rivals the airborne one below
            # ~500 Hz (classic vehicle-NVH result): ~-9 dB net (x0.35) with
            # elastomer mounts, ~-6 dB (x0.50) solid race mounts; the shell
            # re-radiates up to ~1.2 kHz (2nd-order above the panel response).
            geo = dict(
                g_bay=1.0, g_tail=r_bay / r_tail,
                d_bay=0, d_tail=int((r_tail - r_bay) / c * sr),
                bay_alpha=a_fw, bay_fc=fc_mass(m_fw),
                tail_alpha=a_rr, tail_fc=fc_mass(m_rr),
                struct=(0.55 if race else 0.40), struct_fc=2000.0,
                # exhaust hangers bolt the pipe to the floor: the panels re-
                # radiate its LF INSIDE (chest thump), bypassing BOTH the
                # airborne partition and the stiffness HP — it's structure.
                chassis=(0.60 if race else 0.45), chassis_fc=90.0,
                boom_f=c / (2.0 * 2.4), ground=None)
        elif self.pov == "trackside":
            # fixed mic 12 m off the racing line; the CAR MOVES PAST it.  The
            # changing path length is applied downstream as a per-sample
            # fractional delay — true Doppler (+30 %/-19 % at 290 km/h), 1/r
            # level and distance air-absorption follow the live distance.
            geo = dict(
                g_bay=(0.7 if race else 0.45), g_tail=1.0,
                d_bay=0, d_tail=0,
                bay_alpha=(0.35 if race else 0.08), bay_fc=fc_mass(6.3),
                tail_alpha=None, tail_fc=None,
                struct=0.0, struct_fc=800.0, chassis=0.0,
                boom_f=0.0, ground=None, flyby=True)
        else:                                    # chase cam behind the car
            d, hs, hr, car = 6.0, 0.3, 1.2, 4.5  # cam 6 m back, tailpipe 0.3 m up
            r_tail, r_bay = d, d + car
            delta = (math.hypot(d, hs + hr) - math.hypot(d, hr - hs))
            geo = dict(
                g_bay=r_tail / r_bay, g_tail=1.0,
                d_bay=int((r_bay - r_tail) / c * sr), d_tail=0,
                # a RACE car has no sealed bay at all — vented engine cover,
                # exposed stacks/airbox (an open-wheeler's engine is naked);
                # and even a ROAD car's bay is open-bottomed (underbody, wheel
                # arches, cooling stack) — 0.08 modelled a sealed box and
                # deleted the intake/valvetrain texture (a hidden muffle).
                bay_alpha=(0.35 if race else 0.22), bay_fc=fc_mass(6.3),
                tail_alpha=None, tail_fc=None,
                struct=0.0, struct_fc=800.0,   # (mounts shake the cabin, not
                                               #  the street...)
                # ...but the whole BODY SHELL is a large panel radiator: it
                # re-radiates the engine/exhaust SUB band (<~70 Hz) into the
                # air omnidirectionally — the outdoor listener's chest-feel
                # (Leo: 次低频结构传导通路).
                chassis=0.30, chassis_fc=70.0,
                boom_f=0.0, ground=(int(delta / c * sr), 0.8))
        self._pov_cache = (key, geo)
        return geo

    def _pov_delay(self, x, key, d):
        """Fixed path-difference delay of ``d`` samples (named line)."""
        if d <= 0:
            return x
        buf = self._pov_buf.get(key)
        if buf is None or len(buf) != d:
            buf = np.zeros(d, dtype=np.float64)
        y = np.concatenate((buf, x))
        self._pov_buf[key] = y[-d:].copy()
        return y[:len(x)]

    def _pov_lp(self, x, key, fc):
        """1st-order mass-law low-pass (cached butter); pure-numpy fallback is a
        double 2-tap running mean (the codebase's standard no-scipy shape)."""
        if _HAVE_SCIPY:
            b, a = self._bw(1, fc)
            zi = self._pov_zi.get(key)
            if zi is None:
                zi = np.zeros(max(len(a), len(b)) - 1)
            y, self._pov_zi[key] = lfilter(b, a, x, zi=zi)
            return y
        p = self._pov_zi.get(key, 0.0)
        y = 0.5 * (x + np.concatenate(([p], x[:-1])))
        y = 0.5 * (y + np.concatenate(([p], y[:-1])))
        self._pov_zi[key] = float(x[-1]) if len(x) else p
        return y

    def _pov_partition(self, x, key, alpha, fc):
        """Composite panel: openings leak ``alpha`` flat + mass-law LP the rest."""
        return alpha * x + (1.0 - alpha) * self._pov_lp(x, key, fc)

    def _render_block(self, frames: int) -> np.ndarray:
        sim = self.sim
        omega = sim.omega
        # layers kept at the classic voice's scale meet the physical engine at
        # the classic's balance: x 1/_PHYS_MAKEUP (exactly 1.0 in the classic)
        self._cl = (1.0 / _PHYS_MAKEUP) if self.vx.get("phys_voice", False) \
            else 1.0
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

        D1, D2, D3, g1, g2, g3, lp_a, f_helm = self._resonance_params()
        # audit stash: the Swift port reproduces one block in isolation, and
        # these are the only values it cannot recompute from the sim state
        self._dbg_res = (D1, D2, D3, g1, g2, g3, lp_a, f_helm)
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
            # INSTANTANEOUS-SPEED FLUTTER: crank torsionals + combustion
            # feedback make a real crank's momentary speed wander ~0.1-0.3 %,
            # so its pulse train can NEVER phase-lock to any grid.  Ours
            # could: at 16,000 rpm a V10's firing period is EXACTLY 24.000
            # samples — every pulse identical -> drill-line spectrum (measured
            # fill 0.06 vs 0.39 just 1 % of rpm away; near-integer ratios
            # lock too).  A smooth AR(1) phase wander (grows with the same
            # speed-scatter law as `wall`) breaks every lock continuously.
            _rt = self._rng.standard_normal() * 1.5 * max(
                (min(sim.rpm, 18500.0) - 8500.0) / 9000.0, 0.0)
            self._rub = getattr(self, "_rub", 0.0)
            _rp = self._rub
            self._rub += (_rt - self._rub) * 0.45
            crank = self._audio_crank + dps * idx \
                + np.linspace(_rp, self._rub, frames)
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
            self._dbg_choke = choke                          # audit stash
            # audit stash: the excitation inputs, so a Swift mismatch says
            # WHICH input is wrong rather than just that the pulses differ
            if self.capture_stages:
                self._dbg_exc = (float(strength), float(load), float(choke),
                                 float(dps), float(c_runner),
                                 float(self._valve))
            # CYCLE-TO-CYCLE combustion variability — the physical line
            # broadener.  Scatter grows with rpm ceiling (burn time shrinks,
            # turbulence scatter rises): a screamer's harmonics smear into the
            # WALL of sound instead of clean synthesizer lines.  Amplitude
            # scatter (here) + firing-PHASE scatter (_tjit, degrees — phase
            # modulation broadens the HIGH harmonics hardest).
            # LIVE-rpm law (was keyed to the static REDLINE, so an F2004 got
            # the same dose at 10k as at 18.5k — measured: inter-harmonic fill
            # collapsed to 0.06 at 16k = the electric drill Leo heard).  The
            # burn occupies more crank angle and turbulence scatter grows with
            # SPEED: F50 @10k ~1.2 (its ear-proven level), F2004 @16k ~2.0,
            # @18.5k ~2.3, road cars below 8.5k untouched.
            wall = 1.0 + 1.2 * min(max((sim.rpm - 8500.0) / 9000.0, 0.0), 1.1)
            self._jit += (1.0 + (0.12 * wall)
                          * (self._rng.random(len(self._jit)) - 0.5)
                          - self._jit) * min(0.25 + 0.20 * (wall - 1.0), 0.42)
            # per-FIRING arrival scatter is what actually breaks the sample-
            # grid locks (a global phase wander shifts all pulses together and
            # changes nothing): at 16k each cylinder fires ~once per block, so
            # this block-rate refresh IS per-firing — but +-0.8 deg (~0.1
            # sample) couldn't crack a 24.000-sample lock.  Real spark/burn
            # scatter at extreme speed is degrees, not tenths.
            self._tjit += ((self._rng.random(len(self._jit)) - 0.5)
                           * (1.4 * wall) - self._tjit) * 0.45
            if self.vx.get("phys_voice", False):
                # PHYSICAL cycle-to-cycle scatter: a race engine at full load
                # varies a few percent firing to firing (COV of IMEP ~1-3 %),
                # and the exhaust pulse's TIMING does not vary at all -- the
                # exhaust valve opens on the cam, not on the burn.  (The "wall"
                # above -- +-13 % and +-1.5 deg at F1 revs -- was ear-set to
                # break up a drill sound whose real cause was the pulse.)
                self._jit = 1.0 + 0.06 * (self._rng.random(len(self._jit)) - 0.5)
                self._tjit[:] = 0.0

            # Cylinder spread ~3x stronger than before, and bigger still at low
            # rpm (valve shut), where the spaced pops make each cylinder's own
            # character clearly audible -> coarse, grainy low-rpm lumpiness.
            spread = (self._phys_spread if self._split_on()
                      else self.params["cyl_spread"]) \
                * (1.0 + 1.4 * (1.0 - self._valve))
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
            # THE GAS SOLVER'S PULSE (experiment switch): the exhaust valve's
            # own mass-flow burst at this rpm, from the closed-loop solver, at
            # the SAME energy as the parametric pulse -- so only the shape moves.
            phys = self.vx.get("phys_voice", False)
            gp = self._gas_pulse_at(sim.rpm, load) \
                if (self.vx.get("gas_pulse") or phys) else None
            if gp is not None:
                ref = np.arange(0.0, 720.0, 1.0)
                rd = ref - VALVE_OPEN
                rdd = np.clip(rd, 0.0, None)
                rin = (ref >= VALVE_OPEN) & (ref <= VALVE_CLOSE)
                r_rise = max((2.0 + 4.0 * (1.0 - load)) * dps, 1e-4)
                r_tb = max(0.30 * base_tau / self._bd_sharp ** 0.85, 2.5)
                r_blow = (0.7 + load) * np.clip(rd / r_rise, 0.0, 1.0) \
                    * np.exp(-rdd / r_tb)
                r_soft = np.clip(rd / self.params["attack_deg"], 0.0, 1.0)
                r_soft = 0.5 - 0.5 * np.cos(r_soft * math.pi)
                r_disp = r_soft * (1.0 - np.exp(-rdd / (0.5 * base_tau))) \
                    * np.exp(-rdd / (1.5 * base_tau))
                r_cl = np.clip((VALVE_CLOSE - ref) / 18.0, 0.0, 1.0)
                pk_ = self._bd_sharp
                r_par = np.where(rin, ((0.78 + 0.22 * pk_) * r_blow
                                       + 0.7 * (1.22 - 0.22 * pk_) * r_disp)
                                 * r_cl, 0.0)
                r_gas = np.interp(ref, self._gp_deg, gp, period=720.0)
                gp_scale = math.sqrt(float(np.mean(r_par * r_par))
                                     / max(float(np.mean(r_gas * r_gas)), 1e-18))
            inflow = np.zeros(frames) if (gp is not None and phys) else None
            # (the physical voice weights each cylinder's own pulse on its
            # structural path: see _struct_gain)
            blk_dev = np.zeros(frames) if inflow is not None else None
            w_s = self._struct_w() if inflow is not None else None
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
                    # DEEP-VACUUM effect (F10): ON = physical — a motored
                    # cylinder behind a shut throttle blows down at ~1/10 the
                    # fired pressure, so the overrun goes genuinely quiet
                    # (burble/pops carry the voice).  OFF = arcade overrun —
                    # floor the pulse so the engine keeps barking on lift.
                    lo = 0.06 if self.vx.get("vacuum", True) else 0.30
                    amp_j *= min(max(rel, lo), 1.5) ** 0.8
                phi = np.mod(crank + off + self._hdr_off()[j]
                             + self._tjit[j], 720.0)
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
                if gp is not None:
                    pulse = np.interp(phi, self._gp_deg, gp, period=720.0) \
                        * (gp_scale * amp_j)
                    if inflow is not None:
                        # its intake stroke, at its own phase (no header
                        # delay) -- and the same for every cylinder: the
                        # burn's scatter (amp_j) is not in the air it draws,
                        # and the trumpets share one airbox
                        inflow += np.interp(np.mod(crank + off, 720.0),
                                            self._gp_deg, self._gp_intake,
                                            period=720.0) * gp_scale
                # per-cylinder runner HF damping (longer/thinner runner = duller)
                if use_voice:
                    pulse = voice.damp(j, pulse)
                # delay this cylinder's pulse down its own runner (length / live
                # sound speed) before it merges at the collector -> interference.
                d_samp = (self._runner_len_phys[j] if phys
                          else self._runner_len[j]) / c_runner * self.sample_rate
                pulse = self._runner_dl[j].process(pulse, d_samp)
                chans[self._channel_of[j]] += pulse
                if blk_dev is not None:
                    blk_dev += (w_s[j] - 1.0) * pulse
            # the raw pulse train, before anything shapes it -- the first
            # thing a reimplementation has to get right, and the tap it is
            # held to (tools/export_pulses.py)
            self._tap("pulses", chans[0])
            self._phys_inflow = (inflow * strength) if (gp is not None and phys
                                                       and inflow is not None) \
                else None
            # what the block feels beyond the plain train: sum of (w - 1) x
            # each cylinder's pulse, at the scale the train reaches the bang
            self._phys_blk_dev = (blk_dev * (0.55 * strength)) \
                if blk_dev is not None else None

            # Separate the clean 'bang' (tonal pulse) from the 'fizz' (gas-rush
            # noise gated by the pulse), so each gets its OWN mixer slider.
            for ci in range(self._nchan):
                e = chans[ci] * strength
                noise = self._rng.standard_normal(frames)
                bang_c = 0.55 * e
                # BIPOLAR SOURCE (F9): blow+disp are non-negative ENVELOPES, so
                # the raw train is a string of positive lumps — a huge inherent
                # sub-firing pedestal (the hidden 闷/LF flood) and smooth sparse
                # harmonics (the synthesizer tell).  A real port's ACOUSTIC
                # pressure is AC: the compression spike is followed by a
                # rarefaction undershoot (slug inertia + overlap back-suction).
                # Adaptive AC-coupling at half the firing rate turns each lump
                # into that bipolar wave: pedestal gone, attack kept.
                if _HAVE_SCIPY and self.vx.get("bipolar", True) and dps > 1e-12:
                    # corner at QUARTER the firing rate, CAPPED AT 70 Hz: at
                    # revs the old 120 Hz cap shaved the audible low band (why
                    # F9-off sounded bassier — plus the pedestal feeds the
                    # downstream nonlinearities which re-generate audible LF).
                    # 70 Hz still kills the DC/infra pedestal at idle without
                    # eating the bass at speed.
                    # ...and the coupling FADES OUT as the pulses FUSE (Leo:
                    # F1 only sounds right with F9 off): past ~300 fires/s the
                    # overlapped train's pedestal IS the elevated mean pipe
                    # pressure that BIASES the real gas nonlinearities (tanh /
                    # shock steepening) into their violent operating point —
                    # strip it and the F1 goes thin.  Separated pulses (road
                    # cars) keep the full AC fix; the 55 Hz DC-block downstream
                    # stops the mean from ever radiating.
                    fire_n = sim.rpm * len(self._offsets) / 120.0
                    m_ac = min(max((900.0 - fire_n) / 600.0, 0.0), 1.0)
                    if m_ac > 1e-3:
                        f_hp = min(max(0.25 * fire_n, 18.0), 70.0)
                        bhp2, ahp2 = self._bw(1, f_hp, btype="high")
                        zi = self._bip_zi.get(ci)
                        if zi is None:
                            zi = np.zeros(1)
                        acp, self._bip_zi[ci] = lfilter(bhp2, ahp2, bang_c,
                                                        zi=zi)
                        bang_c = m_ac * acp + (1.0 - m_ac) * bang_c
                chans[ci] = bang_c                        # clean bang -> pipe + dry
                # fizz = gas-rush noise GATED by the pulse `e` (this is the GOOD,
                # per-firing fizz — restored via a higher `turbulence`).  The
                # UNGATED floor stays tiny (0.008): a constant hiss between pulses
                # is the dyno-cell tell (白噪音), the gated fizz is the car.
                # gated fizz + a REAL broadband floor: ~30 % of a live engine's
                # energy is un-pitched gas/turbulence noise that does NOT
                # transpose with rpm — the fixed underlay that separates an
                # internal-combustion machine from a synthesizer.  (0.008 was
                # a whisper; the flow-scaled shear/vortex stages add the rest.)
                nfl = 0.006 if self.vx.get("noise", True) else 0.005
                fizz_chans[ci] = e * noise + nfl * noise
            # bang and fizz, before any cavity sees them -- the second
            # tap a reimplementation is held to
            self._tap("bang", chans[0])
            self._tap("fizz", fizz_chans[0])
        self._update_lights(self._audio_crank, dps, frames)
        self._audio_crank = (self._audio_crank + dps * frames) % 720.0

        # --- mix the DRY combustion pulses with the WET pipe resonance -------
        # The pipe rings ON TOP of the bangs, it does not replace them: dry =
        # the explosion you hear at the valve, wet = the pipe colouring it,
        # crack = the sharp leading edge of each pulse (the combustion snap).
        P = self.params
        dry = np.zeros(frames, dtype=np.float64)
        wet = np.zeros(frames, dtype=np.float64)
        srcs = []                              # per-channel excitations (pass 2)
        for ci in range(self._nchan):
            # Reverb the explosion at the source, then that reverberant bang is
            # what both the dry mix and the PIPE (waveguide) downstream receive.
            self._src_verb[ci].mix = P["src_reverb"]
            # PASS 1 of the unified architecture: only the port-cavity reverb
            # here (the fire bang's EARLY reverb).  The waveguide system runs
            # in PASS 2 below, AFTER the bang is voiced — so the per-car voice
            # rides THROUGH the pipe instead of competing with it in a mix.
            # (The PHYSICAL voice skips it: a port is centimetres long, its
            # reflections are sub-millisecond -- not a room's 35 ms combs.)
            if self.vx.get("phys_voice", False):
                # ...nor an in-pipe turbulence hiss: turbulence INSIDE a duct
                # is a weak (dipole) source next to the jet at the exit, which
                # the tail-pipe stage carries.  Layered here it swamped the
                # pulsation ripple that is the actual sound.
                src = chans[ci]
            else:
                src = self._src_verb[ci].process(
                    chans[ci] + 0.25 * P["turbulence"] * fizz_chans[ci])
            srcs.append(src)
            dry += src
        inv = 1.0 / self._nchan
        dry *= inv
        # REAL exhaust pulses are grossly ASYMMETRIC: the compression crest
        # steepens as it travels (it rides hotter, faster gas — a forming shock,
        # a few ms to peak) while the rarefaction tail drags out long.  A
        # symmetric pulse has sparse, clean harmonics — the synthesizer tell
        # ("太对称、太干净").  One-sided quadratic = crest steepening + the
        # even-order richness of the real wave.  (0.45 overshot -> 0.25.)
        phys = self.vx.get("phys_voice", False)
        if self.vx.get("asym", True) and not phys:
            dry = dry + 0.30 * np.maximum(dry, 0.0) * np.tanh(np.abs(dry))

        # ================== IN-PIPE REVERB (管内混响) =======================
        # The sound bounces up and down the SYSTEM many times before it dies —
        # the tail is the pipe's own physical property, every number from
        # geometry (see _resonance_params: RT60, reflection points, tip pole).
        # (1) EARLY REFLECTIONS: the first three discrete passes down the full
        # run, each later one darker and weaker — what makes it read as a PIPE
        # WITH LENGTH instead of a point source.  Spacing = the full-system
        # round trip, so a long truck system echoes wide, a stubby race exit
        # tight.
        if not hasattr(self, "_er_dl"):
            self._er_dl = _TapDelay(int(self.sample_rate * 0.45) + 8)
            self._xc = (ExhaustWaveguide(2600), ExhaustWaveguide(2600),
                        ExhaustWaveguide(2600))
        t1, t2, t3 = self._er_dl.process(dry, (D2, 2 * D2, 3 * D2))
        d2 = 0.5 * (t2 + np.concatenate(([t2[0]], t2[:-1])))      # darker
        d3_ = 0.5 * (t3 + np.concatenate(([t3[0]], t3[:-1])))
        d3_ = 0.5 * (d3_ + np.concatenate(([d3_[0]], d3_[:-1])))  # darkest
        # discrete reflections also collapse with openness (a free tail end
        # barely reflects the passes back)
        er_sc = 1.0 - 0.6 * min(max(sim.engine.exhaust_openness, 0.2), 1.0)
        er_add = er_sc * (0.34 * t1 + 0.20 * d2 + 0.11 * d3_)  # -> wet, pass 2
        # The three firing voices must be TIMBRALLY distinct, or they all read as
        # one 'ignition' blob:
        #   dry   = the low broadband combustion THUMP (the punch)
        #   crack = a bright mechanical TICK (snap), high-passed off the thump
        #   body  = a pitched CHORD that RINGS (high-Q resonators), the musical note
        snap = np.diff(dry, prepend=dry[:1])             # raw broadband edge
        crack = snap
        if _HAVE_SCIPY and not phys:                      # bright tick, not a thump
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
        if _HAVE_SCIPY and P["body"] > 1e-3 and not phys:
            # LIVE physical pitch: the firing body rings at the actual firing
            # fundamental (rpm-tracking), NOT a fixed 90 Hz — that was leftover
            # synthesizer DNA (the resonators sat at 90*k Hz at any rev).  The
            # slider is now a RATIO trim around the physical pitch (90 = x1.0).
            fire_live = max(sim.rpm, 1.0) / 120.0 * len(self._offsets)
            root = min(max(fire_live * (P["firing_pitch"] / 90.0), 28.0), 600.0)
            nyq = self.sample_rate * 0.45
            chord = _FIRE_CHORDS[self.fire_chord % len(_FIRE_CHORDS)]
            if self.fire_chord == 0 and not self.vx.get("engine_series", True):
                chord = _POWER_CHORD          # classic musical default (F5 key)
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
        if phys:
            bang = P["dry"] * dry     # the solver's pulse, nothing layered on
        # choked blowdown adds saturation/harmonics at the SOURCE — only at high
        # load (choke>0), so idle/cruise stay clean; this is the physical origin of
        # the coarse "tear" a turbo/NA engine gets when you bury the throttle.
        drive = P["drive"] + 1.6 * fg + 0.5 * choke
        if drive > 1e-3 and not phys:
            bang = np.tanh(bang * (1.0 + 7.0 * drive))
        if _HAVE_SCIPY and fw > 0.02 and not phys:       # low-shelf 'weight'
            b, a = self._pk(110.0, 0.6, 10.0 * fw)
            bang, self._fire_low_zi = lfilter(b, a, bang, zi=self._fire_low_zi)
        # the voiced firing event -- pulse train turned into combustion
        bang = self._tap("voiced", bang, "src")

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
        if phys:
            combustion = bang
        # BAY bus: the second physical RADIATOR.  What the ENGINE-BAY emits is the
        # structure-borne block voice (the casting ringing behind the mass-law
        # lid) plus, further down, everything mounted in the bay: intake mouth,
        # ITB trumpets, compressor whine/BOV, gear-driven valvetrain, injectors,
        # cam covers.  It is kept OFF the exhaust chain (those components never
        # pass the muffler!) and rejoins at the listener-perspective stage with
        # its own geometry (delay + 1/r + body-panel transmission).
        bay = np.zeros(frames, dtype=np.float64)
        # intake-side sub-bus: compressor whine / BOV / intake roar / ITB howl
        # radiate from the intake tract MOUTH and the atmospheric dump — a
        # bright OPENING, not through sheet metal (the 355 Hz body-panel LP was
        # turning the turbo whistle to mud — Leo: "涡轮声搞坏了").
        bayi = np.zeros(frames, dtype=np.float64)
        if _HAVE_SCIPY and getattr(self, "_blk_seal", 0.0) > 0.0:
            st, self._blk_lp_zi = lfilter(self._blk_lp[0], self._blk_lp[1],
                                          combustion, zi=self._blk_lp_zi)  # the lid
            b1, a1 = self._pk(self._blk_f1, self._blk_q, 5.0)
            st, self._blk1_zi = lfilter(b1, a1, st, zi=self._blk1_zi)      # block ring
            b2, a2 = self._pk(self._blk_f2, self._blk_q * 0.8, 3.0)
            st, self._blk2_zi = lfilter(b2, a2, st, zi=self._blk2_zi)
            combustion = (1.0 - self._blk_seal) * combustion + self._blk_seal * st
            # what the block RADIATES carries each cylinder's own structural
            # path; what goes down the pipe (combustion, above) does not
            if self._split_on() and dps > 1e-12:
                dev = getattr(self, "_phys_blk_dev", None)
                if phys and dev is not None and len(dev) == frames:
                    # the lid and rings are linear: the weighted train's
                    # radiation = the plain one's + that of the deviation
                    if not hasattr(self, "_blkd_zi"):
                        self._blkd_zi = [np.zeros(max(len(self._blk_lp[0]),
                                                      len(self._blk_lp[1])) - 1),
                                         np.zeros(2), np.zeros(2)]
                    zd = self._blkd_zi
                    sd = (P["dry"] * inv) * dev
                    # ...and only above the block's first structural mode:
                    # below it the block moves as one rigid body, the same
                    # way whichever cylinder pushed it
                    if not hasattr(self, "_blkd_hp_zi"):
                        self._blkd_hp_zi = np.zeros(2)
                    bh, ah = self._bw(2, self._blk_f1, btype="high")
                    sd, self._blkd_hp_zi = lfilter(bh, ah, sd,
                                                   zi=self._blkd_hp_zi)
                    sd, zd[0] = lfilter(self._blk_lp[0], self._blk_lp[1], sd,
                                        zi=zd[0])
                    sd, zd[1] = lfilter(b1, a1, sd, zi=zd[1])
                    sd, zd[2] = lfilter(b2, a2, sd, zi=zd[2])
                    st = st + sd
                else:
                    st = st * self._struct_gain(crank, VALVE_OPEN)
            bay += self._blk_seal * st          # block radiation -> bay bus
        else:                                   # no-scipy lid: 2-tap mass-law crude
            bl = 0.5 * (combustion + np.concatenate(([self._bay_prev],
                                                     combustion[:-1])))
            self._bay_prev = float(combustion[-1]) if frames else self._bay_prev
            if self._split_on() and dps > 1e-12:
                dev = getattr(self, "_phys_blk_dev", None)
                if phys and dev is not None and len(dev) == frames:
                    bl = bl + (P["dry"] * inv) * dev
                else:
                    bl = bl * self._struct_gain(crank, VALVE_OPEN)
            bay += 0.5 * bl
        combustion = self._tap("block", combustion, "block")  # sealed in-cylinder event
        # keep a decimated copy of the REAL combustion voice for the analyzer's
        # 'firing pulses' scope — the actual non-linear waveform (tanh-saturated
        # bang, sharp blowdown edges, gated fizz) instead of an idealised hump.
        if frames:
            cstep = max(1, frames // 64)
            self.last_combustion = combustion[::cstep][:64].astype(np.float64).copy()
        # ============ PASS 2: THE VOICED BANG RINGS THE SYSTEM ==============
        # UNIFIED architecture (the whole-picture fix): the per-car VOICE (dry
        # punch + crack + body + drive — months of ear calibration) is not a
        # competitor mixed against the pipe field; it IS the excitation.  Each
        # channel's waveguide chain receives its own bank's pulses PLUS the
        # voiced combustion — so the fire bang's reverb IS the exhaust system,
        # the fleet identity rides THROUGH the pipe, and there is no A/B ratio
        # left to band-aid.
        res_mid = 0.40 * max(P["res1"], P["res2"])
        lp_end = getattr(self, "_lp_a_end", lp_a)
        for ci in range(self._nchan):
            exc = srcs[ci] + 0.7 * inv * combustion
            wg_primary, wg_total, wg_mid = self._wg[ci]
            # DE-REGULARIZED full-system comb: real pipes are never perfectly
            # periodic resonators (temperature gradient along the run, bends,
            # taper stagger the modes) — and each bank's path differs.  A few
            # percent detune between banks kills the organ-pipe/flanger tell.
            if self._nchan > 1:
                det = 1.0 + 0.03 * (2 * ci - (self._nchan - 1)) \
                    / max(self._nchan - 1, 1)
            else:
                det = 0.98                     # mono pipe: split-detuned below
            D2c = max(int(round(D2 * det)), 4)
            prim = wg_primary.process(exc, D1, g1, s, lp_a)
            if self.vx.get("series_wg", True):
                mid = wg_mid.process(0.35 * exc + 0.5 * prim, D3, g3, s, lp_a)
                tin = 0.20 * exc + 0.5 * mid
                total = wg_total.process(tin, D2c, g2, s, lp_end)
            else:                              # classic parallel wiring (F1 key)
                mid = wg_mid.process(exc, D3, g3, s, lp_a)
                tin = exc
                total = wg_total.process(exc, D2c, g2, s, lp_end)
            if self._nchan == 1:
                # SINGLE-PIPE cars (I4/I6 one collector — the 2JZ) had no bank
                # detune, so they alone kept a PERFECT periodic comb — the
                # organ-pipe tell Leo heard only on them.  A real single pipe
                # still staggers its modes (taper, bends, the temperature
                # gradient along the run): split the full-system comb into a
                # +-2 % detuned pair.
                if not hasattr(self, "_wg_monoB"):
                    self._wg_monoB = ExhaustWaveguide(1200)
                total = 0.5 * (total + self._wg_monoB.process(
                    tin, max(int(round(D2 * 1.02)), 4), g2, s, lp_end))
            wet += P["res1"] * prim + res_mid * mid + P["res2"] * total
        wet *= inv
        wet += er_add
        # DIRECT SHARE FOLLOWS THE HARDWARE (the fix for aven's lost chainsaw):
        # an open system transmits most of the wave on the FIRST pass — the raw
        # torn direct sound IS its voice; a chambered system blocks it and the
        # reverberant field carries the note instead.  A fixed 0.14 leak had
        # starved every open car of its rasp while over-ringing it.
        if self.capture_stages:
            self._dbg_pipe = (np.asarray(wet, dtype=np.float64).copy(),
                              [np.asarray(v, dtype=np.float64).copy()
                               for v in srcs],
                              np.asarray(er_add, dtype=np.float64).copy())
        op_d = min(max(sim.engine.exhaust_openness, 0.2), 1.0)
        direct = 0.20 + 0.62 * op_d
        sig = direct * combustion + wet
        # the pipe system in series, plus the openness-scaled direct share
        sig = self._tap("pipes", sig)
        # (the chamber reverb combs now live INLINE at their own stages —
        # catalyst can after the cat, box chambers after the muffler — so the
        # tails STACK along the physical chain instead of being one shared
        # network bolted to the end.  Leo: "reverb 一个一个叠加起来".)

        # --- TURBULENT BACKFLOW (湍流回涌): on the overrun the mean flow
        # collapses and the REFLECTED wave dominates at the collector — shear
        # between the returning and residual gas makes pulse-synchronous chuffs.
        # Model: noise MULTIPLIED by the wet (reflected) envelope — a genuinely
        # nonlinear self-modulation, so the burble breathes with the pipe instead
        # of being a steady hiss.  Only computed off-throttle (zero cost on power).
        ov = (max(0.0, 1.0 - min(max(sim.throttle, 0.0), 1.0) * 6.0)   # foot off
              * min(getattr(self, "_flow", 0.0) * 4.0, 1.0)           # revs up
              * min(sim.rpm / max(sim.engine.idle_rpm * 1.5, 1.0), 1.0))  # not idling
        if ov > 0.03 and dps > 1e-12 \
                and not self.vx.get("phys_voice", False):
            nz_b = self._rng.standard_normal(frames)
            # DARK chuff, not bright hiss: real overrun backflow is low-frequency
            # air pumping.  A cheap 1-pole low-pass (running mean of the noise)
            # drops the harsh treble that read as "air noise covering the engine".
            nz_b = 0.5 * (nz_b + np.concatenate(([self._burble_prev], nz_b[:-1])))
            nz_b = 0.5 * (nz_b + np.concatenate(([self._burble_prev], nz_b[:-1])))
            nz_b = 0.5 * (nz_b + np.concatenate(([self._burble_prev], nz_b[:-1])))
            self._burble_prev = float(nz_b[-1])
            # modulate by the ACTUAL in-duct wave |sig|, not |wet|: the wet
            # field collapsed on open cars under the transmitted-vs-reverberant
            # law, and the burble (tied to it) died with it — the main reason
            # lift-off went dead-silent on the raspy cars (长期问题).  The
            # overrun VOICE of a real car IS this burble + the pops; the
            # motored pulses are physically ~-26 dB and stay that way.
            sig = sig + (0.30 * ov) * np.abs(sig) * nz_b
        # --- F1 / race-engine HIGH-RPM REGIME --------------------------------
        # Above ~600 fires/second the discrete blowdown pulses are only ~50
        # samples apart: they physically merge into a continuous tone, and the
        # per-pulse model degenerates — the reason an F1 car sounded nothing
        # like one.  Crossfade in a harmonic STACK at the firing frequency (the
        # scream IS its harmonic series at these speeds), keeping the pulse
        # model underneath for body.  Gated to genuine screamers (redline >=
        # 11 krpm) so ordinary engines are untouched.
        # (The additive F1 'scream stack' that lived here 2026-07 is GONE — ear
        # verdict: the physical pulse path (bipolar AC source + series waveguides
        # + finite-amplitude steepening + megaphone horn + per-cylinder scatter)
        # beats the synthetic layer outright on every high-revving car.  The
        # screamers keep their open top end via the anti-harshness LP exemption.)
        # overrun pops/bangs are unburnt fuel igniting IN the exhaust, so they
        # enter HERE (at the header) and travel the whole pipe — cat, muffler,
        # wall, tail — instead of being bolted on at the tailpipe.  A stock car's
        # pops get muffled by the cat/box; an open race system keeps them sharp.
        sig = sig + self._cl * self._overrun_pops(frames)

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

        sig = self._tap("header", sig)        # exhaust gas at the header collector
        # --- FINITE-AMPLITUDE WAVE STEEPENING (nonlinear acoustics): a loud
        # pressure wave travels on its own compressed, hotter gas, so its crest
        # outruns its trough and the front STEEPENS as it goes down the pipe —
        # generating high harmonics in proportion to amplitude (the same physics
        # as a trumpet's brassy 'cuivre' snarl).  Quadratic self-distortion,
        # strength driven by mean flow + choked blowdown, so idle stays clean and
        # a hard pull turns raspy from the physics up.  One vector op.
        kst = 0.26 * getattr(self, "_flow", 0.0) + 0.34 * choke
        if kst > 0.01 and not self.vx.get("phys_voice", False):
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
        sig = self._tap("head/port", sig)

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
            _tsm = getattr(sim, "turbo", None)
            if _tsm is not None:
                # the machine's turbine: its LOADING is its pressure ratio --
                # the transmission loss grows with mass flow and pressure ratio
                # (Tiikoja, Abom & Boden) -- the same 0..1 scale the ear-tuned
                # range below was set on (1.0 = the rated turbine ratio)
                u0 = _tsm.units[0]
                pit = u0.p03 / max(_tsm.p04, 1.0)
                bf = min(max((pit - 1.0) / max(_tsm.pit_rated - 1.0, 0.05),
                             0.0), 1.0)
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
            if _tsm is not None:
                # the REAL bypass: the share of the exhaust the open gate
                # passes around the wheel keeps its raw pulse edge
                share = sum(u.w_wgf for u in _tsm.units) / max(_tsm.w_ex, 1e-6)
                wg = min(max(share / 0.35, 0.0), 1.0)
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
                # the screamer PIPE is a real ~0.4 m duct: the BREE rings it
                # (its own tiny reverb) instead of being a dry noise patch
                if not hasattr(self, "_wg_gate"):
                    self._wg_gate = ExhaustWaveguide(240)
                d_g = max(int(round(2.0 * 0.4 * self.sample_rate / c_runner)),
                          6)
                wn = wn + 0.55 * (self._wg_gate.process(wn, d_g, 0.82, -1.0,
                                                        self._rv_lp) - wn)
                sig = sig + (0.16 * wg * self._cl) * np.tanh(wn * 3.0)
        sig = self._tap("head/port", sig)

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
        # CATALYST CAN reverb: the brick sits in its own expansion casing —
        # a real small chamber, so it RINGS as well as absorbs (RT60-derived
        # feedback, in-loop pole keeps the HF tail short).
        rvD, rvG = getattr(self, "_rvD", None), getattr(self, "_rvG", None)
        # (the physical voice: the pipe's reflections are already in the
        # waveguides; this comb rang on every car, catalyst or not)
        if rvD is not None and not self.vx.get("phys_voice", False):
            sig = sig + 0.30 * (self._xc[0].process(sig, rvD[0], rvG[0], 1.0,
                                                    self._rv_lp) - sig)
        sig = self._tap("catalytic", sig)

        # --- (4b) main-pipe HIGH-ORDER STANDING-WAVE WHINE ------------------
        # The odd quarter-wave harmonics in 3-7 kHz, rung as resonant peaks whose
        # Q scales with the pipe's length/diameter (thin small bore = sharp soprano
        # scream, fat bore = broad roar / none).  Grows with the valve opening, so
        # the whine climbs in with revs.  Centre freqs follow the live sound speed.
        # (synthetic formants: peaking filters stacked on the pipe waveguides,
        # which already carry the real modes -- not in the physical voice)
        if _HAVE_SCIPY and self._whine_amt > 0.02 \
                and not self.vx.get("phys_voice", False):
            f_qw = c_runner / (4.0 * max(sim.engine.exhaust_total_m, 0.5))
            wamt = self._whine_amt * (0.25 + 0.75 * self._valve)
            # Q from the UNIFIED system damping + the header SIGNATURE:
            # equal-length headers stack their interference peaks razor-sharp
            # (the clean race whine); unequal headers smear them broad and
            # gruff — the architecture is audible, not just the muffler.
            _sq = getattr(self, "_sysq", 0.6)
            Qbase = min((2.0 + 0.16 * self._whine_ld) * (0.55 + 0.9 * _sq),
                        11.0)
            if getattr(sim.engine, "header_unequal_deg", 0.0) > 0.0:
                Qbase *= 0.65
                wamt *= 0.85
            else:
                Qbase *= 1.15
            for k, n in enumerate(self._whine_orders):
                fc = f_qw * n
                if 2400.0 < fc < min(8000.0, self.sample_rate * 0.45):
                    Q = min(Qbase * math.sqrt(1.0 + 0.10 * n), 14.0)
                    gain = (4.2 - 1.1 * k) * wamt * P.get("whine", 1.0)
                    bw, aw = self._pk(fc, Q, gain)
                    sig, self._whine_zi[k] = lfilter(bw, aw, sig, zi=self._whine_zi[k])
        sig = self._tap("standing-wave", sig)

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
            # NARROW like the real hardware: a side-branch resonator is a tuned
            # trap for ONE drone frequency; its bandwidth is its own damping,
            # Q ~ 5-10 for a packed automotive branch.  The old Q=1.4 smeared the
            # notch across ~50-250 Hz and GUTTED the engine's whole audible bass
            # body (traced: LF share 0.65 -> 0.14 at this stage — Leo's
            # "完全没有低频" was literally this one filter).
            bH, aH = self._pk(f_helm, 6.0, res_depth)
            sig, self._helm_zi = lfilter(bH, aH, sig, zi=self._helm_zi)
            sig = self._tap("resonator", sig)     # Helmholtz de-drone notch
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
            # SYSTEM HELMHOLTZ RESONANCE (the 浑厚 core, ALWAYS on): the
            # expansion box (volume V) breathing through the tailpipe (neck
            # A, L) is a Helmholtz resonator IN the transmission path — the
            # output PEAKS at f_sys = (c/2pi)*sqrt(A/(V*L)), the deep
            # 80-200 Hz 排管闷响 every real system carries.  Until now the
            # chain only ever CUT low end (de-drone notch, DC block, LP);
            # the box's own resonant GAIN was never applied — the muffler
            # attenuated but never RESONATED.  Tuned by the live hot-gas c,
            # strength from the box size; it's the hardware, not an effect.
            if self.vx.get("sys_helm", True):
                v_mf = max(getattr(sim.engine, "muffler_volume_m3", 0.003), 1e-5)
                a_tp = math.pi * (max(sim.engine.exhaust_radius_m, 0.012)
                                  * max(getattr(sim.engine, "tip_scale", 1.0),
                                        0.5)) ** 2
                l_nk = 0.45 + 0.61 * math.sqrt(a_tp / math.pi)  # + end corr
                f_sys = (c_runner / (2.0 * math.pi)) * math.sqrt(
                    a_tp / (v_mf * l_nk))
                f_sys = min(max(f_sys, 45.0), 240.0)
                # WIDE and MODEST, not a narrow peak: the neck's viscous losses
                # + any fibre packing damp the box resonance hard (Q ~ 1), and
                # an absorptive muffler damps it further.  A high-Q boost here
                # reads as a one-note drone (不悦耳) — the real thing is a
                # broad warm lift.
                g_sys = min(2.8 + 0.9 * math.log10(1.0 + v_mf / 0.002), 3.0)
                if getattr(sim.engine, "muffler_type",
                           "reflective") == "absorptive":
                    g_sys *= 0.6
                # DYNAMIC with rpm (Leo): the box is driven hardest when the
                # FIRING order sweeps through its resonance — the real cruise
                # 'drone' physics: the boom blooms as the revs pass f_sys and
                # relaxes away from it — and it scales with the mean flow
                # actually pumping the box (quiet at idle, full under load).
                fire_now = sim.rpm * len(self._offsets) / 120.0
                ovl = math.exp(-((fire_now - f_sys)
                                 / (0.6 * max(f_sys, 1.0))) ** 2)
                g_sys *= (0.45 + 0.55 * getattr(self, "_flow", 0.0)) \
                    * (0.7 + 0.9 * ovl)
                self._dbg_gsys, self._dbg_fsys = g_sys, f_sys   # audit stash
                bS, aS = self._pk(f_sys, 1.0, g_sys)
                if not hasattr(self, "_sysres_zi"):
                    self._sysres_zi = np.zeros(2)
                sig, self._sysres_zi = lfilter(bS, aS, sig, zi=self._sysres_zi)
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
            # DUAL-CHAMBER standing waves: the box is baffled front/rear; each
            # cavity rings its own quarter-wave through the transmission — two
            # extra modal groups between the Helmholtz boom and the comb
            # notches.  Lengths from the box geometry, Q from the UNIFIED
            # system damping (one resonant personality per car).
            l_box = max(getattr(sim.engine, "muffler_neck_len_m", 0.08) * 4.0,
                        0.15)
            _sq = getattr(self, "_sysq", 0.6)
            if not hasattr(self, "_cham_zi"):
                self._cham_zi = [np.zeros(2), np.zeros(2)]
            for kk, (cfr, cdb) in enumerate(((0.42, 2.6), (0.58, 2.0))):
                f_ch = c_runner / (4.0 * max(l_box * cfr, 0.05))
                # (peaking filters, applied to every car whether it has a box
                # or not -- not in the physical voice)
                if 60.0 < f_ch < 4500.0 \
                        and not self.vx.get("phys_voice", False):
                    bC, aC = self._pk(f_ch, 0.8 + 2.4 * _sq,
                                      cdb * (0.5 + 0.8 * _sq))
                    sig, self._cham_zi[kk] = lfilter(bC, aC, sig,
                                                     zi=self._cham_zi[kk])
            if absorptive and _HAVE_SCIPY:
                # PROGRESSIVE packed-fibre rolloff, not one pole: absorption
                # grows with frequency (deeper fibre interaction), so cascade
                # two gentle poles — a natural tail, no 'filter cutoff' edge.
                bA, aA = self._bw(1, min(8200.0, sr * 0.45))
                sig, self._absorb_zi = lfilter(bA, aA, sig, zi=self._absorb_zi)
                bA2, aA2 = self._bw(1, min(12500.0, sr * 0.45))
                if not hasattr(self, "_absorb2_zi"):
                    self._absorb2_zi = np.zeros(1)
                sig, self._absorb2_zi = lfilter(bA2, aA2, sig,
                                                zi=self._absorb2_zi)
            # AIR ABSORPTION over the pipe run: molecular losses rise with
            # frequency and LENGTH — a long truck system arrives duller than a
            # stubby side-exit, from geometry.
            if _HAVE_SCIPY:
                l_run = max(getattr(sim.engine, "exhaust_total_m", 1.6), 0.3)
                bAir, aAir = self._bw(1, min(16000.0 / (1.0 + 0.13 * l_run),
                                             sr * 0.45))
                if not hasattr(self, "_air_zi"):
                    self._air_zi = np.zeros(1)
                sig, self._air_zi = lfilter(bAir, aAir, sig, zi=self._air_zi)
            if getattr(sim.engine, "flex_pipe", False) and _HAVE_SCIPY:
                # corrugated flex section -> a buzzy mid resonance (the 'braaa' rasp)
                bf, af = self._pk(1650.0, 2.2, 4.0)
                sig, self._flex_zi = lfilter(bf, af, sig, zi=self._flex_zi)
            sig = self._tap("muffler", sig)       # expansion low-pass + comb baffles
            # active exhaust valve: above ~40% redline the bypass flap cracks open
            # and the bright straight-through tap is crossfaded back in — the note
            # gets louder and opens up at the top end, exactly like a valved system.
            vo = min(max((self._valve - 0.40) / 0.5, 0.0), 1.0) * 0.5 * P.get("valve_open", 1.0)
            vo = min(vo, 0.85)
            if self.vx.get("phys_voice", False):
                vo = 0.0      # a generic valve model, not this car's hardware
            if vo > 1e-3:
                sig = (1.0 - vo) * sig + vo * bypass
            # SPL-controlled "blow-out": when the engine is LOUD (hard on it) the box
            # can't hold the pressure, so a touch more of the bright pre-muffler
            # signal bleeds through -> an active-valve-like dynamic ("内敛 -> 炸开")
            # WITHOUT a real second path.  Reuses last block's output RMS (free); the
            # (1-vo) guard avoids double-counting an already-open valve.
            spl = min(getattr(self, "last_level", 0.0) * 3.0, 1.0)
            bl = 0.16 * spl * (1.0 - vo)
            if self.vx.get("phys_voice", False):
                bl = 0.0
            if bl > 1e-3:
                sig = (1.0 - bl) * sig + bl * bypass
        else:
            sig = np.diff(sig, prepend=sig[:1])
        # MUFFLER CHAMBER reverbs, inline: the box's front/rear cavities ring
        # their stored energy right where the box sits in the chain.
        if getattr(self, "_rvD", None) is not None \
                and not self.vx.get("phys_voice", False):   # (see the cat can)
            rvD, rvG = self._rvD, self._rvG
            sig = (sig
                   + 0.24 * (self._xc[1].process(sig, rvD[1], rvG[1], 1.0,
                                                 self._rv_lp) - sig)
                   + 0.18 * (self._xc[2].process(sig, rvD[2], rvG[2], 1.0,
                                                 self._rv_lp) - sig))
        sig = self._tap("valve bypass", sig)  # active-valve straight-through mix

        # --- intake / induction roar (the OTHER half a real car you hear) ---
        # Broadband 'sucking' noise through the airbox resonance, swelling with
        # throttle and rpm.  A separate path from the exhaust, cool-air tuned.
        if _HAVE_SCIPY and dps > 1e-12:
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            intake_gain = P["intake"] * sim.throttle * (0.25 + 0.75 * rpm_frac)
            if self.vx.get("phys_voice", False):
                intake_gain *= P.get("phys_jet", _PHYS_JET)   # same flow-noise physics
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
                bayi = bayi + intake_gain * n  # intake mouth: bright opening
                                              # (was mid-exhaust-chain: the roar
                                              # passed through the muffler!)

        # --- INDIVIDUAL THROTTLE BODIES: the raw induction HOWL --------------
        # With one trumpet per cylinder, each intake stroke sucks a sharp tuned
        # pulse straight past the driver: a hard, brassy, harmonically-rich howl
        # at the firing frequency that RISES viciously with revs and throttle —
        # the RB26 / S65 / F1 / 4A-GE signature.  A single-plenum intake (2JZ,
        # 4G63, most road cars) has none of it, only the muffled roar above; so
        # this is exactly what tells those otherwise-similar engines apart.
        # (the PHYSICAL voice has no sum-of-sines howl: it is not a model of
        # an intake, and at 0.38 it put 70 dB on the 5th/10th/15th orders --
        # the F1 "electric drill")
        if getattr(sim.engine, "individual_throttle", False) and dps > 1e-12 \
                and not self.vx.get("phys_voice", False):
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            fire_hz = sim.rpm * len(self._offsets) / 120.0
            thr = min(max(sim.throttle, 0.0), 1.0)
            # An F1-class engine's soprano is ~40 % INTAKE: bare velocity stacks
            # + an unfiltered airbox above the driver's head — no filter, no
            # plenum damping.  Bigger share + a FLAT-TOP harmonic set for
            # screamers (the trumpets are short quarter-wave horns, they carry
            # their harmonics almost undiminished).
            scrm = sim.engine.redline_rpm >= 11000.0
            howl_gain = (0.38 if scrm else 0.16) \
                * (0.15 + 0.85 * thr) * rpm_frac ** 1.5
            if howl_gain > 1e-4 and 20.0 < fire_hz < self.sample_rate * 0.4:
                hset = ([(1, 1.0), (2, 0.9), (3, 0.8), (4, 0.68), (5, 0.55),
                         (6, 0.42), (8, 0.25), (10, 0.14)] if scrm else
                        [(1, 1.0), (2, 0.85), (3, 0.6), (4, 0.42), (5, 0.28),
                         (6, 0.18), (8, 0.10)])
                howl = self._whine(fire_hz, frames, hset,
                                   phase_attr="_itb_phase")
                bayi = bayi + howl_gain * howl  # trumpets: bright opening

        # THE PHYSICAL INTAKE: the summed intake-valve flow into the airbox,
        # radiated from the mouth.  Per mole the hot exhaust carries T_exh/T_air
        # more volume, so the intake source sits that much under the exhaust
        # (same 0.55 source scale).  Radiation: the SAME piston model as the
        # exhaust's tip -- a volume-velocity source radiates as its derivative
        # while the mouth is small (ka < 1) and saturates above f_a = c/2pi a,
        # i.e. a first-order high-pass at f_a (~870 Hz for an F1 airbox mouth;
        # a pure derivative kept rising and put the 15th order 18 dB high).
        q_in = getattr(self, "_phys_inflow", None)
        if q_in is not None and self.vx.get("phys_voice", False) \
                and dps > 1e-12 and len(q_in) == frames:
            t_ratio = 300.0 / max(sim.exhaust_gas_temp(), 300.0)
            f_a = min(343.0 / (2.0 * math.pi
                               * max(self._intake_mouth_radius(), 0.01)),
                      self.sample_rate * 0.45)
            src_in = (0.55 * t_ratio) * q_in
            # THE AIRBOX: the trumpets draw from a plenum (volume V) fed
            # through the inlet duct (the mouth's area A, length L), so the
            # mouth's volume flow is theirs through a Helmholtz resonator --
            # a second-order low-pass at f_H = c/2pi sqrt(A / V L'), L' = L +
            # the end corrections (0.61a outside, 0.85a into the box).  Its
            # damping is the duct's mean-flow resistance rho U / A, so
            # Q = w_H L' / U.  An F1 box puts f_H near 100 Hz and the firing
            # orders 30+ dB down: the real onboard's spectrum is its exhaust's.
            eng_ = sim.engine
            a_m = max(self._intake_mouth_radius(), 0.01)
            A_m = math.pi * a_m * a_m
            L_eff = _AIRBOX_DUCT_M + (0.61 + 0.85) * a_m
            w_H = 343.0 * math.sqrt(
                A_m / (_AIRBOX_VOL_X * max(eng_.total_displacement, 1e-4)
                       * L_eff))
            map_f = sim._manifold_pressure() / P_ATM
            q_air = (eng_.total_displacement * max(sim.rpm, 1.0) / 120.0
                     * sim._volumetric_efficiency(map_f) * map_f)
            Q_H = min(max(w_H * L_eff / max(q_air / A_m, 0.5), 0.5), 20.0)
            w0 = min(w_H / self.sample_rate, 0.45 * math.pi)
            cw, al = math.cos(w0), math.sin(w0) / (2.0 * Q_H)
            b_h = np.array([0.5 * (1.0 - cw), 1.0 - cw, 0.5 * (1.0 - cw)])
            a_h = np.array([1.0 + al, -2.0 * cw, 1.0 - al])
            b_h, a_h = b_h / a_h[0], a_h / a_h[0]
            if _HAVE_SCIPY:
                if not hasattr(self, "_airbox_zi"):
                    self._airbox_zi = np.zeros(2)
                src_in, self._airbox_zi = lfilter(b_h, a_h, src_in,
                                                  zi=self._airbox_zi)
            else:                   # no scipy: the same biquad, direct form I
                x1, x2, y1, y2 = getattr(self, "_airbox_s", (0.0,) * 4)
                out_ = np.empty_like(src_in)
                for i_ in range(len(src_in)):
                    x0 = src_in[i_]
                    y0 = (b_h[0] * x0 + b_h[1] * x1 + b_h[2] * x2
                          - a_h[1] * y1 - a_h[2] * y2)
                    x2, x1, y2, y1 = x1, x0, y1, y0
                    out_[i_] = y0
                self._airbox_s = (x1, x2, y1, y2)
                src_in = out_
            if _HAVE_SCIPY:
                b_i, a_i = self._bw(1, f_a, btype="high")
                if not hasattr(self, "_q_in_zi"):
                    self._q_in_zi = np.zeros(1)
                rad_in, self._q_in_zi = lfilter(b_i, a_i, src_in,
                                                zi=self._q_in_zi)
            else:                   # no scipy: one-pole high-pass at f_a
                k_ = math.exp(-2.0 * math.pi * f_a / self.sample_rate)
                rad_in = np.empty_like(src_in)
                xp = getattr(self, "_q_in_xp", 0.0)
                yp = getattr(self, "_q_in_yp", 0.0)
                for i_ in range(len(src_in)):
                    yp = k_ * (yp + src_in[i_] - xp)
                    xp = src_in[i_]
                    rad_in[i_] = yp
                self._q_in_xp, self._q_in_yp = xp, yp
            bayi = bayi + rad_in
        # What has gone in so far leaves through the intake MOUTH: the roar and
        # the trumpet howl.  Everything added below -- spool, valve dump,
        # gearbox -- radiates from a housing instead, so only this part of the
        # bus is given the mouth's radiation pattern.  Each cylinder's intake
        # event reaches the mouth down its own runner, so the mouth carries the
        # per-cylinder spread (events at the start of each intake stroke).
        if self._split_on() and dps > 1e-12 \
                and getattr(self, "_phys_inflow", None) is None:
            bayi = bayi * self._struct_gain(crank, 0.0)
        bayi_mouth = bayi
        if self.capture_stages:
            self._dbg_bayi1 = np.asarray(bayi, dtype=np.float64).copy()
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
            # duck recalibrated for the POV era: 0.80 was sized when the BOV
            # injected into the full bus at unity — through the bay bright path
            # (~0.4x) it dug a -16 dB hole the flutter couldn't fill (Leo:
            # "flutter 搞坏了").  The engine now dips, the valve rides on top.
            duck = min(0.40 * getattr(self, "_bov_env", 0.0), 0.40)
            sig = (1.0 - duck) * sig          # exhaust collapses on the lift...
            if self.capture_stages:
                self._dbg_gw = (np.asarray(ind, dtype=np.float64).copy(),
                                np.asarray(gw, dtype=np.float64).copy())
            bayi = bayi + self._cl * ind + gw  # whine/BOV: intake tract + dump
                                              # vent to open air, not the pipe
        if not self.stage_on.get("induction+gears", True):
            bayi = np.zeros(frames, dtype=np.float64)   # layer hidden
            bayi_mouth = bayi
        self._tap("induction+gears", bay)     # bay bus: intake, turbo, gearbox

        # audit stash: the exit run is reproduced in isolation by the Swift
        # port, and the induction section above has just drawn from the shared
        # generator -- so its state HERE is the only one that reproduces it
        if self.capture_stages:
            _r = self._rng
            self._dbg_exit = (np.asarray(sig, dtype=np.float64).copy(),
                              (int(_r.s0), int(_r.s1), int(_r.s2), int(_r.s3)),
                              _r._spare)
        if self.capture_stages:
            self._dbg_bayi2 = np.asarray(bayi, dtype=np.float64).copy()
        # --- (7) tail-pipe wall thickness: kill the 'small-trumpet' shriek
        # WITHOUT losing low end.  The brass honk lives in a ~1.8 kHz formant —
        # scoop THAT band and add a touch of low-shelf body (thicker, not thinner).
        wt = P["wall_thickness"]
        phys = self.vx.get("phys_voice", False)
        if _HAVE_SCIPY and wt > 1e-3 and not phys:    # a voicing EQ
            b, a = self._pk(1850.0, 1.1, -16.0 * wt)   # de-honk
            sig, self._wall_sig_zi = lfilter(b, a, sig, zi=self._wall_sig_zi)
            b2, a2 = self._pk(150.0, 0.7, 4.0 * wt)    # add body
            sig, self._wall_low_zi = lfilter(b2, a2, sig, zi=self._wall_low_zi)
        sig = self._tap("wall de-honk", sig)  # tail-pipe wall thickness scoop
        # (Step 4) stainless-wall resonance formants: two narrow peaks give the
        # note its METAL ring.  A thicker wall (wt up) drops the peaks lower and
        # tightens them (fat & solid); a thin wall keeps them high & open (bright,
        # 'tinny').  Always on — it's the pipe material itself, not an effect.
        # (The physical voice leaves it out: wall vibration is a separate, weak
        # radiator -- it cannot put peaks INTO the orifice sound.)
        if _HAVE_SCIPY and not phys:
            f1 = self._wall_f1 * (1.0 - 0.18 * wt)
            f2 = self._wall_f2 * (1.0 - 0.22 * wt)
            ring = getattr(self, "_wall_ring", 1.0)      # material ping strength
            qf = getattr(self, "_wall_q", 1.0)           # material ring duration/Q
            # Q from the material damping: a low-loss wall (titanium) rings
            # sharper/longer; cast iron is broad and dead.
            _sq2 = 0.6 + 0.7 * getattr(self, "_sysq", 0.6)   # unified damping
            bp1, ap1 = self._pk(f1, min(3.4 * qf * _sq2, 12.0),
                                (4.3 - 1.4 * wt) * ring)
            sig, self._wallpk1_zi = lfilter(bp1, ap1, sig, zi=self._wallpk1_zi)
            bp2, ap2 = self._pk(f2, min(4.2 * qf * _sq2, 14.0),
                                (3.2 - 1.6 * wt) * ring)
            sig, self._wallpk2_zi = lfilter(bp2, ap2, sig, zi=self._wallpk2_zi)
        sig = self._tap("metal ring", sig)    # stainless wall-resonance formants
        # --- MEGAPHONE / exit-horn bark: the powerful mid formant a diverging
        # cone radiates (see the _mega setup).  A broad peak at the horn frequency
        # gives the massive 澎湃 midrange roar of an open race exit, and a gentle
        # trim of the extreme top (the horn's far field concentrates power in its
        # passband, not the thin >5 kHz hash) keeps it high AND full — the fix for
        # an F1 sounding like a thin 'broken trumpet'.
        # (the physical voice: a diverging cone RADIATES, it does not ring like a
        # closed column -- its physics is the bigger exit the radiation stage
        # already takes from the tip size; no formant, no horn comb)
        if _HAVE_SCIPY and getattr(self, "_mega_f", 0.0) > 0.0 and not phys:
            bM, aM = self._pk(self._mega_f, 0.8, 7.5 * self._mega_amt)
            sig, self._mega_zi = lfilter(bM, aM, sig, zi=self._mega_zi)
            bH, aH = self._pk(min(self._mega_f * 2.4, self.sample_rate * 0.44),
                              0.7, -3.5 * self._mega_amt)
            sig, self._mega_hi_zi = lfilter(bH, aH, sig, zi=self._mega_hi_zi)
        if getattr(self, "_mega_f", 0.0) > 0.0 and not phys:
            # HORN BODY: the diverging cone is a real air column (~0.6 m) — it
            # rings a short bright tail of its own on top of the formant.
            if not hasattr(self, "_wg_horn"):
                self._wg_horn = ExhaustWaveguide(400)
            d_h = max(int(round(2.0 * 0.6 * self.sample_rate / c_runner)), 8)
            g_h = min(10.0 ** (-3.0 * d_h /
                               (max(getattr(self, "_rt60", 0.1), 0.05)
                                * self.sample_rate)), 0.90)
            sig = sig + 0.22 * (self._wg_horn.process(sig, d_h, g_h, 1.0,
                                                      self._rv_lp) - sig)
        sig = self._tap("megaphone", sig)     # exit-horn mid bark + top trim
        # displacement THUNDER: the deep low-end roar a big-cylinder engine carries
        # under the note (so a Ferrari V12 thunders, not just screams).
        phys = self.vx.get("phys_voice", False)
        if _HAVE_SCIPY and self._thunder is not None and not phys:
            sig, self._thunder_zi = lfilter(self._thunder[0], self._thunder[1],
                                            sig, zi=self._thunder_zi)
        # BROADBAND PULSATING LOW BAND (the 澎湃): a real system's low end is
        # not one 78 Hz resonance ("低频是点不是面") — every blowdown shoves a
        # turbulent SLUG of gas, a WIDE 60-250 Hz rumble amplitude-modulated at
        # the firing rate.  A point peak hums one note; the firing-gated band
        # is the 轰隆隆 / air-push.  Gain from cylinder litres (big slugs
        # thunder), level rides load via mean flow; the modulation envelope is
        # locked to the live crank phase.
        if _HAVE_SCIPY and dps > 1e-12 and self.vx.get("rumble", True) \
                and not phys:
            cyl_l2 = (sim.engine.total_displacement * 1000.0) \
                / max(len(self._offsets), 1)
            # LOUDER x2 (Leo): with the unipolar pedestal gone (F9) this band IS
            # the low end, and the loudness-weighted AGC means raising it no
            # longer ducks the rest — bass is a pure additive here.
            g_rmb = min(max((cyl_l2 - 0.22) * 1.05, 0.0), 0.72)
            if sim.engine.redline_rpm >= 11000.0:
                # extreme fire rates merge the slugs into a continuous jet
                # ROAR whose LF tracks TOTAL mass flow, not litres-per-cyl —
                # the 0.3 L F1 cylinders had zeroed their rumble share
                # (Leo: the F1 needs MORE bass, not less)
                g_rmb = max(g_rmb, 0.26)
            self._dbg_grmb = g_rmb            # fleet-audit stash
            if g_rmb > 0.01:
                spac2 = 720.0 / max(len(self._offsets), 1)     # firing spacing
                ph2 = np.mod(self._audio_crank + dps * np.arange(frames),
                             spac2) / spac2
                envp = np.exp(-ph2 * 3.0)                      # per-firing slug
                brm, arm = _bandpass(130.0, 0.6, self.sample_rate)
                if not hasattr(self, "_rumble_zi"):
                    self._rumble_zi = np.zeros(2)
                rmb, self._rumble_zi = lfilter(
                    brm, arm, self._rng.standard_normal(frames),
                    zi=self._rumble_zi)
                sig = sig + g_rmb * (0.35 + 0.65 * envp) \
                    * (0.30 + 0.70 * getattr(self, "_flow", 0.0)) * rmb
        sig = self._tap("thunder", sig)       # deep displacement low-end roar
        # gear-grain: gear-driven valvetrain / timing-gear WHIR — a fine, dense
        # band-passed noise modulated by a gear-mesh tone, so it's a 'grind-like'
        # (but not actual grinding) grain riding ON the smooth note.  Rises with
        # rpm; per-engine amount = eng.gear_grain (Ferrari V12s etc.).
        gg = getattr(sim.engine, "gear_grain", 0.0) * P.get("gear_grain", 1.0)
        if _HAVE_SCIPY and gg > 1e-3 and dps > 1e-12 \
                and not self.vx.get("phys_voice", False):
            rf = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            f_mesh = max(sim.rpm / 60.0 * 8.5, 50.0)        # ~8.5x rev = a fine whir
            inc = 2.0 * math.pi * f_mesh / self.sample_rate
            ph = self._gear_phase + inc * np.arange(1, frames + 1)
            self._gear_phase = float(ph[-1] % (2.0 * math.pi))
            am = 0.55 + 0.45 * np.sin(ph)                   # gear-mesh modulation
            ngr = self._rng.standard_normal(frames)
            ngr, self._grain_zi = lfilter(self._grain_bp[0], self._grain_bp[1],
                                          ngr, zi=self._grain_zi)
            bay = bay + gg * (0.04 + 0.34 * rf) * ngr * am   # timing gears: BAY

        # --- (7b) full-system round-trip reflection: a weak, low-passed echo at
        # the pipe's round-trip time (2 x system length / sound speed) feeds a bit
        # of low end back in -> bouncy low frequencies and a longer, rounder tail,
        # instead of a dry abrupt cut-off.
        if _HAVE_SCIPY and dps > 1e-12:
            refl = self._tail_dl.process(sig, self._tail_len / c_runner * self.sample_rate)
            refl, self._tail_lp_zi = lfilter(self._tail_lp[0], self._tail_lp[1],
                                             refl, zi=self._tail_lp_zi)
            sig = sig + 0.16 * refl
        sig = self._tap("reflection", sig)    # + gear-grain, round-trip echo

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
        comb_load_true = (min(max(strength * 1.25, 0.0), 1.0)
                          if dps > 1e-12 else 0.0)
        # what the LOUDNESS paths are told, which may be a lie: sustain_on_lift
        # holds the perceived load up when the throttle shuts, so lifting does
        # not drop the engine to a whisper.  At the default 0.0 this is exactly
        # comb_load_true and nothing changes.
        k = min(max(self.sustain_on_lift, 0.0), 1.0)
        comb_load = comb_load_true + (1.0 - comb_load_true) * k
        self._comb_load = comb_load           # reused by the overrun darkening
        rad *= comb_load       # no combustion (overrun) -> no sharp radiation
        if rad > 1e-3:
            # PISTON-RADIATOR shape, not a pure derivative: the open end radiates
            # with efficiency ~ (ka)^2 below its corner and ~flat above, i.e. a
            # 1st-order HIGH-PASS at f_a = c/(2*pi*a_tip).  A pure d/dt was
            # +6 dB/oct FOREVER — it starved the radiated share of low end and
            # over-brightened the top.  A big tip lowers f_a: the fat pipe IS a
            # low-frequency horn (口径越大低频辐射越强), from geometry.
            a_tip = max(sim.engine.exhaust_radius_m, 0.012) \
                * max(getattr(sim.engine, "tip_scale", 1.0), 0.5)
            f_a = min(343.0 / (2.0 * math.pi * a_tip), self.sample_rate * 0.4)
            # the far-field dQ/dt term (crisp puff edges) exists at ANY range
            ext = np.empty(frames + 1, dtype=np.float64)
            ext[0] = self._rad_prev
            ext[1:] = sig
            self._rad_prev = float(sig[-1])
            drv_far = np.diff(ext) * (self.sample_rate / (2.0 * math.pi * 500.0))
            if _HAVE_SCIPY and self.vx.get("rad_hp", True):
                # SUPERPOSED radiation (Leo's blend verdict, and the physics
                # agrees): an open end radiates BOTH a near-field piston term
                # (the HP shape — keeps the body) and the far-field dQ/dt term.
                # Near field dies as 1/r^2 vs 1/r, so the mix follows the
                # LISTENER'S RANGE: cockpit hears mostly piston, trackside
                # mostly derivative, chase in between.  (Binary HP-vs-diff was
                # the modelling shortcut; the blend is the real field.)
                bR, aR = self._bw(1, f_a, btype="high")
                if not hasattr(self, "_radhp_zi"):
                    self._radhp_zi = np.zeros(1)
                hp_near, self._radhp_zi = lfilter(bR, aR, sig,
                                                  zi=self._radhp_zi)
                w_near = min(max(P.get("rad_near", 0.20)
                                 + {"cockpit": 0.25, "chase": 0.0,
                                    "trackside": -0.12}.get(self.pov, 0.0),
                                 0.0), 0.90)
                drv = w_near * hp_near + (1.0 - w_near) * drv_far
            else:                              # classic pure derivative (F6 off)
                drv = drv_far
            sig = (1.0 - rad) * sig + rad * drv
        sig = self._tap("radiation", sig)     # in-duct -> free-field radiation

        # --- (8) tail-pipe air-shear: the gas tearing out of the tip into still
        # air — a broadband roar/hiss swelling with exhaust mass-flow (rpm x load).
        # This is the outermost 'whoosh' you hear standing behind the car.
        if _HAVE_SCIPY and dps > 1e-12:
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            flow = rpm_frac * (0.35 + 0.65 * sim.throttle)
            # ABSOLUTE exit velocity (why only the F1 lacked 澎湃: every LF
            # body mechanism — rumble/boom/thunder — lives below 250 Hz where
            # a 1.4 kHz-firing engine has nothing; a real F1's wall is its JET
            # ROAR).  u = expanded volume flow / total tip area, choked-capped;
            # Lighthill power ~ U^8, tempered here to amplitude ~ (u/u_ref)^2
            # with saturation.  A 300 m/s F1/race exit roars a broadband WALL;
            # a 120 m/s cruiser stays polite — from geometry, same formula.
            q_ex = sim.engine.total_displacement * sim.rpm / 120.0 * 3.0
            a_tips = self._nchan * math.pi * (
                max(sim.engine.exhaust_radius_m, 0.012)
                * max(getattr(sim.engine, "tip_scale", 1.0), 0.5)) ** 2
            u_abs = min(q_ex / max(a_tips, 1e-4)
                        * (0.35 + 0.65 * min(max(sim.throttle, 0.0), 1.0)),
                        320.0)
            jet_amp = min(max((u_abs / 150.0) ** 2, 0.5), 6.0)
            self._u_abs = u_abs               # Mach input for the flyby crackle
            if self.vx.get("phys_voice", False):
                # LIGHTHILL: jet noise POWER ~ rho U^8 D^2 / c^5, so its
                # amplitude goes as U^4 -- a 320 m/s race exit is 34 dB above
                # a 120 m/s road car, not the capped (u/150)^2 above.  One
                # efficiency constant, P["phys_jet"], set against a real
                # recording's tone-to-noise ratio.
                jet_amp = P.get("phys_jet", _PHYS_JET) * (u_abs / 150.0) ** 4
            shear_gain = P.get("shear", 0.10) * flow * jet_amp \
                * (0.30 + 0.70 * getattr(self, "_comb_load", 1.0))
            if not self.vx.get("noise", True):
                shear_gain *= 0.7             # classic quieter underlay (F7)
            if shear_gain > 1e-4:
                ns_ = self._rng.standard_normal(frames)
                ns_, self._shear_bp_zi = lfilter(self._shear_bp[0], self._shear_bp[1],
                                                 ns_, zi=self._shear_bp_zi)
                ns_, self._shear_hp_zi = lfilter(self._shear_hp[0], self._shear_hp[1],
                                                 ns_, zi=self._shear_hp_zi)
                if self.road_pipe:                 # a cat car's tip is breathier
                    shear_gain *= 0.7
                sig = sig + shear_gain * ns_
            # --- KARMAN VORTEX STREET + EDGE TONE (flow-acoustic sources): the
            # moving gas itself sings.  Vortices shed off the tip lip at the
            # Strouhal rate f = 0.2*U/d_tip — a narrowband, hollow flutter that
            # RISES with flow (dipole source, power ~ U^6 -> amplitude ~ flow^3);
            # and the shear layer grazing the lip edge locks into an EDGE TONE at
            # f = 0.2*U/t_lip (lip wall ~5 mm) — the thin high 'ripping' whistle
            # of a hard pull.  U from the mean exhaust flow (~90 m/s at WOT
            # redline), d from the car's real tip radius.  Both die at idle.
            u_ex = 90.0 * flow
            d_tip = 2.0 * max(getattr(sim.engine, "exhaust_radius_m", 0.03), 0.012) \
                * max(getattr(sim.engine, "tip_scale", 1.0), 0.5)
            a_fl = flow ** 3
            if a_fl > 0.003:
                sr_ = self.sample_rate
                f_v = min(max(0.2 * u_ex / d_tip, 60.0), 900.0)
                bv, av = _bandpass(f_v, 6.0, sr_)
                vex, self._vortex_zi = lfilter(
                    bv, av, self._rng.standard_normal(frames),
                    zi=getattr(self, "_vortex_zi", np.zeros(2)))
                f_e = min(0.2 * u_ex / 0.005, sr_ * 0.42)
                be, ae = _bandpass(f_e, 8.0, sr_)
                edg, self._edge_zi = lfilter(
                    be, ae, self._rng.standard_normal(frames),
                    zi=getattr(self, "_edge_zi", np.zeros(2)))
                if self.vx.get("phys_voice", False):
                    a_fl *= P.get("phys_jet", _PHYS_JET) * (u_abs / 150.0) ** 2
                sig = sig + a_fl * (0.40 * vex + 0.12 * edg)
        sig = self._tap("tailpipe exit", sig)  # gas tearing out of the tip

        # audit stash: the listener run is reproduced in isolation by the Swift
        # port, and it needs all THREE buses -- the tailpipe, the bay, and the
        # intake sub-bus -- plus the generator as it stands here
        if self.capture_stages:
            _r = self._rng
            self._dbg_listen = (
                np.asarray(sig, dtype=np.float64).copy(),
                np.asarray(bay, dtype=np.float64).copy(),
                np.asarray(bayi, dtype=np.float64).copy(),
                (int(_r.s0), int(_r.s1), int(_r.s2), int(_r.s3)), _r._spare)
            self._dbg_mouth = np.asarray(bayi_mouth, dtype=np.float64).copy()
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
                # keep the DF2T state (a*y), NOT y: that is what scipy's zi is,
                # and this pole MOVES every block (its cutoff follows the
                # combustion load), so storing y would apply the new pole to the
                # old sample and drift away from the scipy path every block.
                y = np.empty_like(sig)
                s0, s1 = self._over_lp_zi
                for i in range(len(sig)):
                    y0 = oma * sig[i] + s0
                    s0 = a_lp * y0
                    y1 = oma * y0 + s1
                    s1 = a_lp * y1
                    y[i] = y1
                self._over_lp_zi = [float(s0), float(s1)]
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
        sig = self._tap("EQ", sig)

        # --- bay-mounted mechanical sources.  These used to be added AFTER the
        # room reverb — bone-dry, glued to the ear (the literal "engine in my
        # face").  They radiate from the cam covers / rail IN the bay, so they
        # join the BAY bus and get the same perspective as everything else. ----
        mech = P.get("mech", 0.30)
        if mech > 1e-3 and dps > 1e-12:
            spacing = 720.0 / max(2 * len(self._offsets), 1)
            ph_t = np.mod(self._audio_crank + dps * np.arange(frames), spacing) \
                / spacing
            tick_env = np.exp(-ph_t * 13.0)               # sharp hit, fast ring-down
            nzm = self._rng.standard_normal(frames)
            tick = np.diff(nzm, prepend=nzm[:1]) * tick_env   # HF 'click' spectrum
            rpm_frac = min(sim.rpm / max(sim.engine.redline_rpm, 1.0), 1.0)
            n_sc = (4.0 / max(len(self._offsets), 1)) ** 0.5
            bay = bay + (mech * 0.050 * n_sc * (1.0 - 0.85 * rpm_frac)
                         * self._cl) * tick
        ia = getattr(self, "_inj_amt", 0.0)
        if ia > 1e-3 and self._inj_bp is not None:
            nz, self._inj_zi = lfilter(self._inj_bp[0], self._inj_bp[1],
                                       self._rng.standard_normal(frames),
                                       zi=self._inj_zi)
            bay = bay + (ia * self._cl) * nz

        # ================ LISTENER PERSPECTIVE (white-box) ===================
        # Two radiators, one listener.  TAIL = the tailpipe exit (everything
        # that came down the exhaust chain above); BAY = the engine bay
        # (block radiation, intake mouth/ITB, turbo whine/BOV, timing gears,
        # valvetrain, injectors).  Per _pov_geo(): spherical spreading 1/r,
        # path-difference delay, composite panel transmission (openings leak +
        # mass-law LP), the cabin's c/2L standing wave, and the chase cam's
        # tarmac-bounce comb — all from geometry, no listen fudges.
        if self.capture_stages:
            self._dbg_bay = (np.asarray(bay, dtype=np.float64).copy(),
                             np.asarray(bayi, dtype=np.float64).copy())
        geo = self._pov_geo()
        tail = sig
        if geo["d_tail"]:
            tail = self._pov_delay(tail, "tail_d", geo["d_tail"])
        tail_pre = tail                       # pre-partition (for structure paths)
        if geo["tail_fc"] is not None:
            tail = self._pov_partition(tail, "tail_p",
                                       geo["tail_alpha"], geo["tail_fc"])
        bay_p = self._pov_delay(bay, "bay_d", geo["d_bay"]) if geo["d_bay"] else bay
        bay_air = self._pov_partition(bay_p, "bay_p",
                                      geo["bay_alpha"], geo["bay_fc"])
        if geo.get("flyby"):
            # TRACKSIDE: move the car first.  Both openings' radiation patterns
            # and the fly-by further down all read this one position.
            v = abs(float(getattr(sim.drivetrain, "v", 0.0)))
            if getattr(self, "_track", None) is None:
                self._track = _TrackSide()
            x = self._track.advance(v, frames / self.sample_rate)
            self._tk_x = x                # relative to the post it is nearest
            # the intake mouth faces FORWARD: its roar and howl beam at a mic
            # the car is still driving towards.  Kept as the CHANGE the pattern
            # makes, so the unbeamed (omnidirectional) mix survives for the
            # track's diffuse field, which hears the car from every side.
            dbi = self._tk_intake(bayi_mouth,
                                  x + self._tk_sources()["intake"][0]) \
                - bayi_mouth
        # intake-side BRIGHT path: the tract mouth / atmospheric dump is an
        # OPENING — high leak, gentle 2.4 kHz shading (arch/ducting), never the
        # body-panel mass law that was muddying the compressor whistle.
        d_int = geo.get("d_int", geo["d_bay"])
        bayi_p = self._pov_delay(bayi, "bayi_d", d_int) if d_int else bayi
        a_hi = min(geo["bay_alpha"] * 2.2 + 0.15, 0.80)
        g_int = geo.get("g_int", geo["g_bay"]) / geo["g_bay"]
        if geo.get("onboard"):
            a_hi = 1.0                    # the inlet is IN the camera's air
            # fixed geometry, fixed patterns: the intake seen from above and
            # behind its inlet (the change it makes, as on trackside)
            dbi = self._tk_beam(bayi_mouth, geo["cos_int"],
                                self._intake_mouth_radius(), "ob_int") \
                - bayi_mouth
        if geo.get("onboard"):
            # open air (a_hi = 1 passes it flat): the mouth at the airbox,
            # the housings (spool, dump, gearbox) at the back of the engine
            bay_air = bay_air + g_int * bayi_mouth \
                + (geo["g_box"] / geo["g_bay"]) * (bayi - bayi_mouth)
        elif geo.get("flyby"):
            # the mouth and the housings radiate from different places on the
            # car: the same partition, kept apart (it is linear)
            tk_block = bay_air
            tk_mouth = g_int * self._pov_partition(bayi_mouth, "tk_pm",
                                                   a_hi, 2400.0)
            tk_house = g_int * self._pov_partition(bayi - bayi_mouth, "tk_ph",
                                                   a_hi, 2400.0)
            bay_air = bay_air + tk_mouth + tk_house
        else:
            bay_air = bay_air + g_int * self._pov_partition(bayi_p, "bayi_p",
                                                            a_hi, 2400.0)
        if geo["struct"] > 0.0:
            # structure-borne mount path: shell re-radiation of the engine's
            # low-mid band inside the cabin (2nd-order above the panel response)
            stq = self._pov_lp(self._pov_lp(bay_p, "st1", geo["struct_fc"]),
                               "st2", geo["struct_fc"])
            bay_air = bay_air + geo["struct"] * stq
        if geo.get("flyby"):
            # Directivity belongs to the two OPENINGS alone.  The block, the
            # housings and the panels are big, roughly omnidirectional
            # radiators; the pipe end beams BACKWARDS, the intake mouth (above)
            # forwards.  The diffuse field is fed from every direction at once,
            # so it gets the unbeamed mix.
            omni = geo["g_tail"] * tail + geo["g_bay"] * bay_air
            pos = self._tk_sources()
            tail = self._tk_directivity(tail, self._tk_x + pos["exh"][0])
            dbi_p = self._pov_partition(dbi, "bayi_dp", a_hi, 2400.0)
            bay_air = bay_air + dbi_p
            # the three engine radiators, each propagated from its own place
            self._tk_src = (geo["g_tail"] * tail,
                            geo["g_bay"] * (tk_mouth + dbi_p),
                            geo["g_bay"] * (tk_block + tk_house))
        if geo.get("onboard"):
            # the pipes from in front of their exits: their top end beams away
            a_tip = sim.engine.exhaust_radius_m \
                * max(getattr(sim.engine, "tip_scale", 1.0), 0.5)
            tail = self._tk_beam(tail, geo["cos_tail"], a_tip, "ob_tail")
            dbi_d = self._pov_delay(dbi, "bayi_dd", d_int) if d_int else dbi
            bay_air = bay_air + g_int * self._pov_partition(
                dbi_d, "bayi_dp", a_hi, 2400.0)
        sig = geo["g_tail"] * tail + geo["g_bay"] * bay_air
        if geo["ground"]:
            dg, rg = geo["ground"]
            if dg > 0:
                # tarmac bounce — but REAL asphalt is rough at cm scale: the
                # highs scatter diffusely and only the LOW band reflects
                # coherently.  A full-band 0.8 copy carved -14 dB comb notches
                # straight through the presence band (a major hidden muffle);
                # low-passed + softer, it's a gentle outdoor LF ripple instead.
                gref = self._pov_lp(self._pov_delay(sig, "gnd", dg),
                                    "gnd_lp", 1800.0)
                sig = sig + 0.45 * gref
        if geo["boom_f"] > 0.0 and _HAVE_SCIPY:
            # STIFFNESS region: below the first panel resonance (~90 Hz) the
            # partition is stiffness-controlled and TL RISES as f falls — deep
            # LF does NOT flood the cabin.  Without this the sub-bass passed at
            # 0 dB, drowned the AGC and left the cockpit quiet AND muffled.
            bhp, ahp = self._bw(1, 90.0, btype="high")
            if not hasattr(self, "_stiff_zi"):
                self._stiff_zi = np.zeros(1)
            sig, self._stiff_zi = lfilter(bhp, ahp, sig, zi=self._stiff_zi)
            # ...and the cabin standing-wave boom re-peaks what DOES get in
            bbm, abm = self._pk(geo["boom_f"], 2.2, 5.0)
            sig, self._boom_zi = lfilter(bbm, abm, sig, zi=self._boom_zi)
        if geo.get("chassis", 0.0) > 0.0:
            # CHASSIS-BORNE exhaust LF: the hangers shake the floor pan and the
            # panels re-radiate the pipe's low band INSIDE the cabin — the chest
            # thump ("胸口能感觉到的震动").  A STRUCTURE path: it legitimately
            # bypasses both the airborne partition and the stiffness HP (that HP
            # models AIRBORNE transmission only).
            chas = self._pov_lp(tail_pre, "chassis",
                                geo.get("chassis_fc", 100.0))
            sig = sig + geo["chassis"] * chas
        if self.capture_stages:
            self._dbg_pov = (np.asarray(sig, dtype=np.float64).copy(),
                             float(getattr(self, "_tk_x", -60.0)))
        if geo.get("flyby"):
            # TRACKSIDE FLY-BY: the car drives past fixed mics 12 m off the
            # line (_TrackSide places the posts; the car was moved above).
            # Every path's propagation delay follows the live geometry -- its
            # per-sample ramp IS the Doppler bend; level 1/r and air
            # absorption ride the same distance.
            #
            # TWO paths now, not one.  The tarmac is a mirror: the mic hears the
            # tailpipe directly AND its image under the road, a little later.
            # The difference between them is tiny (a few cm) and it CHANGES as
            # the car closes, so the comb it carves SWEEPS -- down through the
            # mids to a first notch near 1.6 kHz at the pass, and back up as the
            # car leaves.  That moving notch is the "whoosh" on a TV pass.
            v = abs(float(getattr(sim.drivetrain, "v", 0.0)))
            L, hm = 12.0, 1.2                     # mic: 12 m off, 1.2 m up
            sr = self.sample_rate
            # RETARDED time: the sound arriving now left the car when it was
            # further back, and c*tau is the distance from THERE -- the moving
            # SOURCE's Doppler, c/(c -/+ v), falls out of the delay's slope.
            # EVERY RADIATOR FROM ITS OWN PLACE: the exhaust at the tail, the
            # intake where the engine breathes, the block and housings with
            # the engine (_tk_sources) -- each its own delay line (its own
            # Doppler; the front ones pass the mic first).  They sit within
            # 4 m of each other, so the air and the ground bounce's coherence,
            # which differ by nothing measurable between them, are applied
            # once to the sum.
            if getattr(self, "_tk_paths", None) is None:  # 3 s: a pass fits
                self._tk_paths = {nm: _MovingTaps(int(3.0 * sr), 2)
                                  for nm in ("exh", "intake", "body")}
                self._tk_air1 = _AirFIR(sr)
                self._tk_gzi = np.zeros(1)
                self._tk_img = _MovingTaps(int(3.0 * sr), 4)
                self._tk_img_zi = [[np.zeros(1), np.zeros(1)]
                                   for _ in range(4)]
                self._tk_omni_dl = _FlybyDelay(int(3.0 * sr))
                self._tk_field = _OutdoorField(sr)
            # CONVECTIVE AMPLIFICATION: a moving monopole is louder ahead of
            # itself than behind, (1 - M_r)^-2 with M_r its Mach number towards
            # the mic when the sound left (+4.7 dB coming, -3.7 dB going at
            # 290 km/h).  The Doppler above is the same motion bending pitch.
            M = min(v, 0.8 * 343.0) / 343.0
            pos = self._tk_sources()
            _m = getattr(self, "_tk_mute", ())
            d_sum = np.zeros(frames)
            g_sum = np.zeros(frames)
            for nm, s_ in zip(("exh", "intake", "body"), self._tk_src):
                if nm in _m:
                    continue                  # analysis: this source silenced
                dx, z = pos[nm]
                t_d, xe_d = self._track.retarded(v, L * L + (hm - z) ** 2, dx)
                t_g, _ = self._track.retarded(v, L * L + (hm + z) ** 2, dx)
                r_d, r_g = 343.0 * t_d, 343.0 * t_g
                dd, dg = self._tk_paths[nm].process(s_, (t_d * sr, t_g * sr))
                cv = (1.0 + M * xe_d / r_d) ** -2
                d_sum = d_sum + dd * (L / r_d * cv)
                g_sum = g_sum + dg * (0.9 * L / r_g * cv)  # asphalt |R| 0.9
            # the car as a whole, from mid-car: the ground bounce's coherence,
            # the air, the walls and the place's field
            tau1, xe1 = self._track.retarded(v, L * L + (hm - 0.5) ** 2)
            r1 = 343.0 * tau1
            cv1 = (1.0 + M * xe1 / r1) ** -2
            if _HAVE_SCIPY:
                # The bounce is only COHERENT up to a point.  At grazing
                # incidence asphalt is smooth by the Rayleigh criterion right
                # up through the audio band, but air turbulence and the fact
                # that a car is not a point source decorrelate the two paths
                # more the farther they run -- so the comb is sharp as it
                # passes and washes out at range.  (12th-octave steps: the
                # design caches.)
                r_g1 = math.sqrt(r1 * r1 + 4.0 * hm * 0.5)
                fcoh = min(max(9000.0 * L / r_g1, 1500.0), sr * 0.45)
                fcoh = 2.0 ** (round(12.0 * math.log2(fcoh)) / 12.0)
                bG, aG = self._bw(1, fcoh)
                g_sum, self._tk_gzi = lfilter(bG, aG, g_sum, zi=self._tk_gzi)
            # the air takes its ISO 9613-1 share over the path's length
            near = self._tk_air1.process(d_sum + g_sum, r1)
            # BOTH WALLS: the barrier across the track and the facade behind
            # the mic, and the bounces between them (_tk_walls)
            wall = self._tk_walls(sig, v, M, L, hm, frames)
            # THE PLACE: the grandstand, pit wall and trees round the post
            # scatter the car back at the mic as a diffuse field.  Outdoors that
            # field is NOT a room's constant: a far car lights the scatterers
            # with 1/r too, so the field falls with distance -- only slower than
            # the direct sound while the car is among the scatterers:
            #     field / direct-at-the-post = (L/r_c) * R_s / (r + R_s)
            # -> 20 dB under the direct as it passes, only ~4-6 dB under it far
            # out: far cars go washier, yet their lines stay sharp, as they do
            # in real recordings.  The reverb slider scales it (0.2 = as
            # specified).
            src_then = self._tk_omni_dl.process(omni, tau1 * sr)
            g_field = (L / _TK_RC_M) * _TK_RS_M / (r1 + _TK_RS_M) * cv1
            field = self._tk_field.process(src_then * g_field)
            sig = near + (0.0 if "wall" in _m else wall) \
                + (0.0 if "field" in _m else field) * (P["reverb"] / 0.2)
            # the tyres' geometry, for the tyre and wind noise after the level
            self._tk_tire_geo = (v, [self._track.retarded(
                v, L * L + (hm - 0.05) ** 2, dxa) for dxa in (1.35, -1.35)])
            # the auto-level below measures THIS -- the car as it was when the
            # arriving sound left it -- not the mic, so it levels rpm and car
            # like any other view and leaves 1/r, the air and the track alone
            self._tk_level_src = src_then

        # --- the SPACE, per perspective: the open air behind the car (chase)
        # vs the small absorbent cabin cavity (cockpit).  ONE shared space —
        # the per-component reverbs above are source-local (port/spool), this
        # is where the LISTENER is.
        if self._pov_geo().get("onboard"):
            pass                          # onboard camera: open air, moving car
        elif self.pov == "cockpit":
            # heavily-absorbent trimmed cavity: little reverberant energy (a wet
            # short room was part of the '闷' feel)
            self._cab_verb.mix = 0.6 * P["reverb"]
            sig = self._cab_verb.process(sig)
        elif self.pov == "trackside":
            pass                          # the track's own field is in already
        else:
            self._reverb.mix = P["reverb"] + (0.05 if self.road_pipe else 0.0)
            sig = self._reverb.process(sig)
        sig = self._tap("cabin/room", sig)

        # audit stash: the master run is reproduced in isolation by the Swift
        if self.capture_stages:
            _r = self._rng
            self._dbg_master = (np.asarray(sig, dtype=np.float64).copy(),
                                (int(_r.s0), int(_r.s1), int(_r.s2),
                                 int(_r.s3)), _r._spare)
        # --- auto-level (or fixed gain) + soft saturation + master volume ----
        phys = self.vx.get("phys_voice", False)
        if phys:
            sig = sig * _PHYS_MAKEUP
        if self.agc_enabled:
            # LOUDNESS-weighted level estimate: the ear barely counts deep LF
            # (equal-loudness contours), but a raw-RMS AGC counts it in full —
            # so every bit of real low-end body added lately made the AGC pull
            # the AUDIBLE bands down: the more 浑厚, the more 闷.  Estimate the
            # level on a ~300 Hz-high-passed copy (a cheap A-weighting LF roll)
            # so bass rides ON TOP instead of stealing the gain budget.  The
            # signal itself is untouched; peaks stay guarded by the limiter.
            # TRACKSIDE levels the CAR, not the mic: the fly-by's distance
            # sweep (1/r, the air, the track) IS the sound, and any AGC that
            # listens to the mic undoes it -- a "near-freeze" still pumped a
            # six-second getaway back up to full level.
            lvl_src = sig
            ref = getattr(self, "_tk_level_src", None)
            if self.pov == "trackside" and ref is not None \
                    and len(ref) == len(sig):
                lvl_src = ref * _PHYS_MAKEUP if phys else ref
            if _HAVE_SCIPY:
                bwg, awg = self._bw(1, 300.0, btype="high")
                if not hasattr(self, "_agc_hp_zi"):
                    self._agc_hp_zi = np.zeros(1)
                est, self._agc_hp_zi = lfilter(bwg, awg, lvl_src,
                                               zi=self._agc_hp_zi)
            else:
                est = np.diff(lvl_src, prepend=lvl_src[:1]) * 8.0  # crude HF
            rms = float(np.sqrt(np.mean(est * est))) + 1e-9
            # the physical voice keeps its own lift: its level follows the
            # engine while it FIRES and holds when it stops (as trackside levels
            # the car, not the mic) -- otherwise the AGC fills the physical
            # overrun drop back in.  sustain_on_lift still holds it up.
            self._level += (rms - self._level) * 0.04 * (
                min(max(getattr(self, "_comb_load", 1.0), 0.0), 1.0)
                if phys else 1.0)
            # gain ceiling FOLLOWS COMBUSTION: on the overrun a real car gets
            # QUIETER — the old fixed x6 ceiling let the AGC pump the residual
            # noise floors (fizz/ticks/injector band) up to fill the hole,
            # which was Leo's lift-off "white noise" amplifier.
            gmax = 2.2 + 3.8 * getattr(self, "_comb_load", 1.0)
            gain = min(0.22 / (self._level + 1e-6), gmax)
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
        if self.pov == "cockpit":
            rn = 0.0                     # Leo: the cockpit hears NO wind, any form
        if _HAVE_SCIPY and rn > 1e-3:
            spd = min(getattr(sim.drivetrain, "v", 0.0) / 32.0, 1.0)   # ~115 km/h full
            if spd > 0.015:
                nz = self._rng.standard_normal(frames)
                nz, self._roadn_zi = lfilter(self._roadn[0], self._roadn[1],
                                             nz, zi=self._roadn_zi)
                nz2 = self._rng.standard_normal(frames)
                nz2, self._roadn_lp_zi = lfilter(self._roadn_lp[0], self._roadn_lp[1],
                                                 nz2, zi=self._roadn_lp_zi)
                # wind/road wash halved (Leo: cabin/room 风噪太大)
                road = rn * spd * (0.8 * nz + 0.25 * nz2)
                if self.pov == "trackside" \
                        and getattr(self, "_tk_tire_geo", None) is not None:
                    # trackside hears the TYRES and the WIND, from the car
                    road = self._tk_rolling(frames)
                    if "tires" in getattr(self, "_tk_mute", ()):
                        road = road * 0.0
                sig = sig + road

        # --- F1 BROADCAST COMPRESSION: a real F1 feed (and every racing game)
        # rides heavy programme compression — the wall is DENSE, the dynamic
        # range small.  Soft block compressor, screamers only: fast attack,
        # slow release, 3:1 above threshold, makeup gain.
        if dps > 1e-12 and sim.engine.redline_rpm >= 11000.0 \
                and self.pov != "trackside":
            # (broadcast compression is the TV/onboard character — the
            # TRACKSIDE ear hears the raw 20 dB fly-by sweep uncompressed)
            if not hasattr(self, "_f1c_env"):
                self._f1c_env, self._f1c_g = 0.0, 1.0
            _r = float(np.sqrt(np.mean(sig * sig)))
            self._f1c_env += (_r - self._f1c_env) \
                * (0.45 if _r > self._f1c_env else 0.10)
            _th, _ratio = 0.22, 2.0          # gentled (F50 control: the 3:1
                                             # pump was part of the fake feel)
            _gt = 1.0 if self._f1c_env <= _th \
                else (_th / self._f1c_env) ** (1.0 - 1.0 / _ratio)
            _gp = self._f1c_g
            self._f1c_g += (_gt - _gp) * 0.5
            sig = sig * np.linspace(_gp, self._f1c_g, frames) * 1.15

        # (injector + valvetrain clatter now radiate from the BAY bus above —
        # they used to be bolted on here, post-reverb and bone-dry.)
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
            # SCREAMERS ARE EXEMPT: an F1's identity lives at 8-16 kHz — cutting
            # the mix to ~9.3 kHz at speed left only the 1-5 kHz periodic core,
            # i.e. the electric drill.  Their harmonic stack is additive (band-
            # limited by construction), so the fold-over this LP guards against
            # barely applies; keep the top end open.
            if sim.engine.redline_rpm >= 11000.0:
                target = max(target, min(15500.0, self.sample_rate * 0.46))
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

    # the turbocharger's sound sources: levels relative to the turbo_vol mix
    # knob, set so a WOT pull and a lift sit where the ear-approved old layer
    # sat (the pitch, timing and spectrum are now the machine's)
    _TB_TONE = 0.160              # blade-pass tone per M_u2^2.5 (fan law: power
                                  #   ~ U^5)
    _TB_BUZZ = 0.050              # buzz-saw orders per (M_rel - 1)
    _TB_TCN = 0.030               # tip-clearance narrowband
    _TB_WHOOSH = 0.030            # whoosh band per M_u2^3
    _TB_THUMP = 0.50              # surge volume pulse (dW/dt at the inlet)
    _TB_STALL = 0.17              # stalled wheel's broadband
    _TB_BOV = 4.0                 # blow-off jet per (u/c)^4

    def _turbo_audio(self, frames, rpm, sv):
        """The turbocharger, heard from the machine (turbo.py).

        Tones at the shaft's real speed per unit; whoosh; the charge-air twin's
        surge and blow-off.  Sets self._bov_env (the engine's lift duck) from
        the valve's and the surge's real activity."""
        sim, eng, sr = self.sim, self.sim.engine, self.sample_rate
        ts = sim.turbo
        tv = self.params["turbo_vol"]
        out = np.zeros(frames, dtype=np.float64)
        if frames <= 0:
            return out
        # the app's Flutter / SSQV switches ARE the valve now
        ts.air.bov_mode = ("none" if self.flutter
                           else ("atmo" if self.ssqv else "recirc"))
        tw = getattr(self, "_tw", None)
        if tw is None or tw.ts is not ts:
            tw = self._tw = turbo_mod.Twin(ts)
            self._tw_om = [u.omega for u in ts.units]
            self._tw_w = [u.w_c for u in ts.units]
            self._tw_p = ts.air.p_p
            self._tw_ph = [0.0 for _ in ts.units]
            self._tw_zi = {}
            self._tw_env = 0.0
            rs = np.random.RandomState(7)
            self._tw_buzz = [(rs.uniform(0.3, 1.0, 12), rs.uniform(0, 6.283, 12))
                             for _ in ts.units]
        # the hidden BOV test key sets the envelope: stage a lift in the twin
        if getattr(self, "_bov_env", 0.0) >= 0.999 and self._tw_env < 0.5:
            tw.air.p_p = P_ATM + eng.boost_bar * 1.0e5
        n_sub = max(frames // 8, 1)
        rec = tw.run(frames / sr, n_sub)
        xs = np.concatenate(([-1.0], (np.arange(n_sub) + 1.0) * (frames / n_sub) - 1.0))
        xi = np.arange(frames, dtype=np.float64)
        p_arr = np.interp(xi, xs, np.concatenate(([self._tw_p], [r[1] for r in rec])))
        wb = np.array([r[2] for r in rec])
        self._tw_p = float(rec[-1][1])
        a01 = math.sqrt(1.4 * 287.0 * 298.15)
        nyq = 0.45 * sr
        noise = None
        env_t = 0.0
        busy = tw.busy > 0
        for j, u in enumerate(ts.units):
            if u.table is None:
                continue
            ln = u.table.ln
            wj = np.interp(xi, xs, np.concatenate(([self._tw_w[j]],
                                                   [r[0][j] for r in rec])))
            self._tw_w[j] = float(wj[-1])
            om0, om1 = self._tw_om[j], u.omega
            self._tw_om[j] = om1
            om = om0 + (om1 - om0) * (xi + 1.0) / frames
            ph = self._tw_ph[j] + np.cumsum(om) / sr
            self._tw_ph[j] = float(ph[-1] % (2.0 * math.pi))
            c = u.comp
            u2 = om * (0.5 * c.d2)
            m_u = u2 / a01
            rho = ln["rho"]
            phi = wj / np.maximum(rho * u2 * c.d2 * c.d2, 1e-6)
            phi_z = turbo_mod.PHI_ZSL
            # the blades hold their pressure field only with forward flow:
            # the tones chop with every surge reversal
            fwd = np.clip(phi / phi_z, 0.0, 1.0) ** 2
            f_s = float(om1) / (2.0 * math.pi)
            z = c.z_main

            def fade(f):                 # no partial past the Nyquist guard
                return min(max((nyq - f) / (0.05 * sr), 0.0), 1.0)
            a = tv * self._TB_TONE * m_u ** 2.5 * fwd
            tone = (fade(z * f_s) * np.sin(z * ph)
                    + 0.35 * fade(2 * z * f_s) * np.sin(2 * z * ph)
                    + 0.5 * fade(f_s) * np.sin(ph)
                    + 0.2 * fade(2 * f_s) * np.sin(2 * ph))
            if self.o_chord:             # easter egg: the V7 on the 1st order
                for hm, ha in _TURBO_V7:
                    tone = tone + 0.6 * ha * fade(hm * 4 * f_s) * np.sin(hm * 4 * ph)
            out += a * tone
            # buzz-saw: the inducer tip's relative Mach past 1 -> shocks
            # locked to the rotor, every shaft order, uneven blade to blade
            c_ax = wj / (rho * c.a_ann)
            m_rel = np.sqrt(c_ax * c_ax + (om * 0.5 * c.d1s) ** 2) / a01
            over = np.clip(m_rel - 1.0, 0.0, 0.5)
            if float(over.max()) > 1e-3:
                amps, phs = self._tw_buzz[j]
                bz = np.zeros(frames)
                for k in range(12):
                    if (k + 1) * f_s < nyq:
                        bz += amps[k] * np.sin((k + 1) * ph + phs[k])
                out += tv * self._TB_BUZZ * over * fwd * bz
            if noise is None:
                noise = self._rng.standard_normal(frames)
            # tip-clearance noise (subsonic): a narrow band near half the BPF
            sub_ = np.clip((1.05 - m_rel) / 0.15, 0.0, 1.0)
            f_t = min(0.5 * z * f_s, nyq)
            if f_t > 200.0:
                bT, aT = _bandpass(2.0 ** (round(24.0 * math.log2(f_t)) / 24.0), 4.0, sr)
                key = ("tcn", j)
                nb, self._tw_zi[key] = lfilter(bT, aT, noise,
                                               zi=self._tw_zi.get(key, np.zeros(2)))
                out += tv * self._TB_TCN * m_u ** 2 * fwd * sub_ * nb
            # whoosh: broadband from the inlet duct's (1,0) cut-on (the duct
            # ~1.2 x the inducer) to ~0.8 x BPF, worst at low-to-mid flow
            f_lo = 1.8412 * a01 / (math.pi * 1.2 * c.d1s)
            f_hi = min(0.8 * z * f_s, nyq)
            if f_hi > 1.2 * f_lo:
                fc = math.sqrt(f_lo * f_hi)
                q = fc / (f_hi - f_lo)
                bW, aW = _bandpass(2.0 ** (round(12.0 * math.log2(fc)) / 12.0),
                                   max(q, 0.5), sr)
                key = ("wh", j)
                nw, self._tw_zi[key] = lfilter(bW, aW, noise,
                                               zi=self._tw_zi.get(key, np.zeros(2)))
                g = np.exp(-((phi - 0.060) / 0.025) ** 2) * (phi > 0.0)
                out += tv * self._TB_WHOOSH * m_u ** 3 * g * nw
            # SURGE: the reversals themselves
            if busy:
                dw = np.diff(wj, prepend=wj[0]) * sr          # kg/s^2
                w_ref = max(ln["w_z"], 1e-3)
                thump = dw * (0.005 / w_ref)                  # ~1 over a 5 ms flip
                bL, aL = self._bw(2, 700.0)                   # the air box
                key = ("th", j)
                thump, self._tw_zi[key] = lfilter(bL, aL, thump,
                                                  zi=self._tw_zi.get(key, np.zeros(2)))
                out += tv * self._TB_THUMP * np.clip(thump, -3.0, 3.0)
                stall = np.clip((phi_z - phi) / phi_z, 0.0, 1.5)
                if float(stall.max()) > 1e-3:
                    fs2 = min(max(2.0 * f_s, 300.0), nyq)
                    bS, aS = _bandpass(2.0 ** (round(12.0 * math.log2(fs2)) / 12.0),
                                       1.2, sr)
                    key = ("st", j)
                    ns, self._tw_zi[key] = lfilter(bS, aS, noise,
                                                   zi=self._tw_zi.get(key, np.zeros(2)))
                    out += tv * self._TB_STALL * m_u ** 2 * stall * ns
                    env_t = max(env_t, float(stall.mean()))
        # BLOW-OFF: the jet through the valve throat at the plenum's pressure
        wbm = float(wb.mean()) if len(wb) else 0.0
        if wbm > 1e-4:
            pr = np.maximum(p_arr / ts.p01, 1.0)
            u = np.minimum(np.sqrt(np.maximum(5.0 * (1.0 - pr ** (-2.0 / 7.0)), 0.0)), 1.0)
            w_ref = max(ts.w_air_rated, 0.02) * 0.5
            openf = min(wbm / w_ref, 1.0) ** 0.5
            d_v = math.sqrt(ts.air.bov_cda / 0.6 / (math.pi / 4.0))
            f_pk = min(max(0.2 * float(u.mean()) * 343.0 / d_v, 120.0), nyq)
            if noise is None:
                noise = self._rng.standard_normal(frames)
            bj, aj = _bandpass(2.0 ** (round(12.0 * math.log2(f_pk)) / 12.0), 1.1, sr)
            jet, self._tw_zi["bov"] = lfilter(bj, aj, noise,
                                              zi=self._tw_zi.get("bov", np.zeros(2)))
            amp = tv * self._TB_BOV * u ** 4 * openf
            if ts.air.bov_mode == "atmo":
                out += 1.25 * amp * jet
            else:                        # back into the intake: the pipe run
                bR, aR = self._bw(1, 1500.0)
                jet, self._tw_zi["bovr"] = lfilter(bR, aR, jet,
                                                   zi=self._tw_zi.get("bovr", np.zeros(1)))
                out += 0.8 * amp * jet
            env_t = max(env_t, openf * float(u.mean()))
            if self.o_chord:             # easter egg: the blow-off as B-dim
                n = np.arange(frames)
                chord = np.zeros(frames)
                inc = 2.0 * math.pi / sr
                bp0 = getattr(self, "_bdim_phase", 0.0)
                for fz in _BDIM_HZ:
                    chord += np.sin(bp0 * (fz / _BDIM_HZ[0]) + inc * fz * n)
                self._bdim_phase = bp0 + inc * _BDIM_HZ[0] * frames
                out += (tv * 0.7) * openf * chord
        # twincharge: the series blower sings low, handing over to the turbo
        if getattr(eng, "induction_subtype", "") == "twincharge":
            ratio = eng.blower_ratio if eng.blower_ratio > 0 else 9.0
            fb = (rpm / 60.0) * ratio
            low = max(0.0, 1.0 - min(rpm / max(eng.redline_rpm, 1.0), 1.0) / 0.7)
            if 20.0 < fb < sr * 0.45 and low > 0.01:
                out += (sv * (0.3 + 0.5 * low) * 0.5) * self._whine(
                    fb, frames, [(1, 1.0), (2, 0.5), (3, 0.28)],
                    phase_attr="_whine_phase")
        # the lift duck follows the valve's and the surge's real activity
        self._tw_env += (min(env_t * 1.6, 1.0) - self._tw_env) * min(frames / (sr * 0.05), 1.0)
        self._bov_env = self._tw_env
        return out

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
        if self.vx.get("phys_voice", False):
            gm *= P.get("phys_gear", _PHYS_GEAR)   # same mesh-force physics
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

        if tv > 1e-3 and eng.induction == "turbo" \
                and getattr(sim, "turbo", None) is not None:
            out += self._turbo_audio(frames, rpm, sv)
        elif tv > 1e-3 and eng.induction == "turbo":
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
                # capture the TRAPPED pressure: the vent JET's velocity, pitch and
                # loudness all derive from it (white-box jet noise below)
                self._bov_pr0 = max(float(sim.boost), 0.1)
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
                # ---- WHITE-BOX VENT JET (the fix for the 'artificial' pshhh):
                # a real dump is a JET through an orifice, not white noise with a
                # volume fade.  Everything below derives from the trapped charge:
                #   * plenum blowdown   (PR-1) decays with tau = V/(A*Cd*c) — the
                #     charge piping (~6 L) emptying through the valve throat;
                #   * jet velocity      isentropic vent  u/c = sqrt(5*(1-PR^(-2/7)))
                #     (gamma=1.4), choked-capped at 1;
                #   * jet PITCH         Strouhal peak f = St*u/D (St~0.2): starts
                #     bright and GLIDES DOWN as the pressure bleeds — the real
                #     'psheeew' signature a static spectrum never has;
                #   * jet LOUDNESS      Lighthill's U^8 acoustic power -> amplitude
                #     ~ u^4: the sound dies with the velocity, not on a timer.
                # The three hardwares differ only in ORIFICE GEOMETRY:
                #   SSQV atmospheric: small throat (D~20 mm, A~3 cm^2) straight to
                #     free air -> loud, bright, fast (tau ~ 0.10 s);
                #   stock recirc: bigger valve (A~4 cm^2) but vents INTO the intake
                #     plumbing -> the pipe run low-passes it dark and soft;
                #   no-valve SURGE: backflow squeezes through the compressor wheel
                #     (small effective area -> tau ~ 0.4 s) at the big inlet
                #     (D~50 mm -> LOW Strouhal pitch: dark chuffs), gated by the
                #     deep-surge cycle (~13-24 Hz, faster with more boost).
                n = np.arange(frames)
                noise = self._rng.standard_normal(frames)
                bov = 0.42 + 1.0 * tv
                V, CD, C0 = 0.006, 0.6, 343.0            # charge piping m^3, Cd, c
                if self.flutter and not self.ssqv:
                    A, D = 0.8e-4, 0.050                 # backflow via wheel; inlet
                else:
                    A, D = (3.0e-4, 0.020) if self.ssqv else (4.0e-4, 0.025)
                tau = V / (A * CD * C0)                  # plenum blowdown constant
                pr = 1.0 + getattr(self, "_bov_pr0", 0.7) * self._bov_env
                u = min(math.sqrt(max(5.0 * (1.0 - pr ** (-2.0 / 7.0)), 0.0)), 1.0)
                f_pk = min(max(0.2 * (u * C0) / D, 120.0), sr * 0.42)
                if _HAVE_SCIPY:
                    bj, aj = _bandpass(f_pk, 1.1, sr)    # the jet's Strouhal band
                    jet, self._bovjet_zi = lfilter(bj, aj, noise,
                                                   zi=getattr(self, "_bovjet_zi",
                                                              np.zeros(2)))
                elif D < 0.03:                           # no-scipy: bright-ish tilt
                    jet = np.diff(noise, prepend=noise[:1]) * 0.7 + 0.3 * noise
                else:                                    # ...or dark 2-tap mean
                    jet = 0.5 * (noise + np.concatenate(([self._bov_prev],
                                                         noise[:-1])))
                    self._bov_prev = float(noise[-1])
                amp = bov * (u ** 4) * 3.2               # Lighthill U^8 (quadrupole)
                if self.ssqv:
                    out += (amp * 1.25) * jet
                elif self.flutter:
                    # SURGE is not a steady jet: each cycle is a bulk FLOW
                    # REVERSAL — a MONOPOLE volume pulse at the big inlet mouth
                    # (velocity there = throat flow spread over the inlet area,
                    # u_mouth = u*A/(pi/4*D^2) -> Strouhal puts its energy LOW:
                    # the meaty 'tu' thump), radiating ~ u^2 (monopole), plus
                    # the bright throat hiss (quadrupole u^4) on top.  Both
                    # gated by the deep-surge cycle.
                    fl = 13.0 + 11.0 * bfrac             # deep-surge cycle rate
                    ph = self._flutter_phase + 2.0 * math.pi * fl * n / sr
                    if frames:
                        self._flutter_phase = float(ph[-1] % (2.0 * math.pi))
                    pulse = np.clip(np.sin(ph), 0.0, 1.0) ** 2.0
                    u_mouth = u * C0 * A / (0.785 * D * D)   # m/s at the inlet
                    f_lo = min(max(0.2 * u_mouth / D, 45.0), 400.0)
                    if _HAVE_SCIPY:
                        bl_, al_ = _bandpass(f_lo, 0.8, sr)
                        thump, self._bovlo_zi = lfilter(
                            bl_, al_, noise, zi=getattr(self, "_bovlo_zi",
                                                        np.zeros(2)))
                    else:
                        thump = 0.5 * (noise + np.concatenate(([self._bov_prev],
                                                               noise[:-1])))
                        thump = 0.5 * (thump + np.concatenate(([self._bov_prev],
                                                               thump[:-1])))
                        self._bov_prev = float(noise[-1])
                    mono = bov * (u * u) * 3.9           # monopole ~ u^2: the body
                    out += ((mono * (1.6 + 0.9 * bfrac)) * thump
                            + (amp * 1.0) * jet) * pulse
                else:                                    # recirc: darkened by the
                    drk = 0.5 * (jet + np.concatenate(([self._bov_prev],
                                                       jet[:-1])))   # intake pipe run
                    self._bov_prev = float(jet[-1]) if frames else self._bov_prev
                    out += (amp * 0.8) * drk
                self._bov_env *= math.exp(-frames / (sr * tau))
        self._prev_throttle = sim.throttle

        gv = P["gearbox_vol"]
        dt = sim.drivetrain
        if self.vx.get("phys_voice", False):
            # MESH WHINE is the teeth's dynamic mesh force -- transmission error
            # times mesh stiffness -- so it scales with the TORQUE they carry:
            # loud under load and on hard engine braking, gone when unloaded.
            # One radiation constant, P["phys_gear"] (_PHYS_GEAR), set against
            # a real F1 onboard, where a loaded dog box sits under the engine.
            gv *= P.get("phys_gear", _PHYS_GEAR) \
                * min(abs(sim.gas_torque) / 600.0, 1.0)
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

    def _update_lights(self, crank_before, dps, frames):
        """Did each cylinder's firing angle pass during this block?

        Read off the SAME offsets that place the pulses, so the lamps and the
        sound can never disagree.
        """
        decay = math.exp(-frames / self.sample_rate / 0.045)
        self.cylinder_light *= decay
        if dps <= 1e-12:
            return
        swept = dps * frames
        if swept >= 720.0:                       # faster than one whole cycle
            self.cylinder_light[:] = 1.0
            return
        a = crank_before % 720.0
        for i, off in enumerate(self._offsets):
            if i >= len(self.cylinder_light):
                break
            d = (off % 720.0) - a                # measured from the block start
            if d < 0:
                d += 720.0
            if d <= swept:
                self.cylinder_light[i] = 1.0

    _GP_GRID = (0.12, 0.25, 0.40, 0.55, 0.70, 0.85, 1.0)
    _GP_LOAD = (0.1, 0.4, 0.7, 1.0)
    _GP_N = 360

    def _gas_pulse_at(self, rpm, load=1.0):
        """The exhaust valve's mass-flow burst over one cycle at this rpm, from
        the closed-loop gas solver (gas_truth), interpolated across a baked rpm
        grid.  Baked on first use (~0.8 s, offline physics); None if the solver
        cannot run for this engine."""
        if self._gp_grid is None:
            try:
                from .gas_truth import exhaust_pulse_with_amplitude
                eng = self.sim.engine
                self._gp_deg = np.arange(self._GP_N) * (720.0 / self._GP_N)
                lut, ilut = [], []
                for rf in self._GP_GRID:
                    shape, dp, pm, ishape = exhaust_pulse_with_amplitude(
                        eng, rf, N=self._GP_N, intake=True)
                    shape = np.asarray(shape, dtype=np.float64)
                    ilut.append(np.asarray(ishape, dtype=np.float64))
                    r_ = max(rf * eng.redline_rpm, eng.idle_rpm)
                    row = []
                    for ld in self._GP_LOAD:
                        c_ = self.sim.exhaust_sound_speed(rpm=r_, load=ld)
                        row.append(self._steepen(shape, ld * dp, pm, r_, c_)
                                   if self.vx.get("steepen", True) else shape)
                    lut.append(row)
                self._gp_lut = np.asarray(lut, dtype=np.float64)
                self._gp_ilut = np.asarray(ilut, dtype=np.float64)
                self._gp_grid = np.asarray(self._GP_GRID, dtype=np.float64)
            except Exception:
                self._gp_grid = False
        if self._gp_grid is False:
            return None

        def _w(grid, v):
            if v <= grid[0]:
                return 0, 0, 0.0
            if v >= grid[-1]:
                return len(grid) - 1, len(grid) - 1, 0.0
            i = int(np.searchsorted(grid, v))
            return i - 1, i, (v - grid[i - 1]) / (grid[i] - grid[i - 1])

        i0, i1, t = _w(self._gp_grid, rpm / max(self.sim.engine.redline_rpm, 1.0))
        a0, a1, u = _w(self._GP_LOAD, min(max(load, 0.0), 1.0))
        # the intake flow at this rpm (no steepening: a smooth suction hump)
        self._gp_intake = (1 - t) * self._gp_ilut[i0] + t * self._gp_ilut[i1]
        L_ = self._gp_lut
        return ((1 - t) * ((1 - u) * L_[i0, a0] + u * L_[i0, a1])
                + t * ((1 - u) * L_[i1, a0] + u * L_[i1, a1]))

    def _steepen(self, shape, dp, p_mean, rpm, c):
        """One blowdown pulse, steepened down its own pipe (see above).

        The pulse's acoustic velocity from its pressure by the simple-wave
        relation u = 2c/(g-1) * ((p/p0)^((g-1)/2g) - 1); each point arrives
        earlier by L*beta*u/c^2.  In the reversed crank angle s = -phi that is
        Burgers' equation w_x + w w_s = 0 with w = beta*u*6*rpm/c^2 (deg per
        metre), solved at x = L by Hopf-Lax: w = (s - y*)/L, y* minimising
        W0(y) + (s - y)^2 / 2L.  Hot exhaust gamma 1.33."""
        g_ = 1.33
        beta = 0.5 * (g_ + 1.0)
        eng = self.sim.engine
        L = eng.exhaust_primary_m + 0.5 * max(eng.exhaust_total_m
                                              - eng.exhaust_primary_m, 0.0)
        pr = 1.0 + max(dp, 0.0) / max(p_mean, 1e3)
        u_pk = 2.0 * c / (g_ - 1.0) * (pr ** ((g_ - 1.0) / (2.0 * g_)) - 1.0)
        pos = np.clip(shape, 0.0, None)
        if u_pk < 1e-3 or pos.max() <= 0.0:
            return shape
        dphi = 720.0 / len(shape)
        s = -self._gp_deg[::-1]                       # ascending s = -phi
        w0 = (beta * u_pk * 6.0 * rpm / (c * c)) * pos[::-1]
        W0 = np.concatenate(([0.0], np.cumsum(0.5 * (w0[1:] + w0[:-1]) * dphi)))
        G = W0[None, :] + (s[:, None] - s[None, :]) ** 2 / (2.0 * L)
        w = (s - s[np.argmin(G, axis=1)]) / L
        out = shape.copy()
        # carry the steepened positive burst; keep any negative (reverse-flow)
        # part as it was -- it is small and not what steepens
        neg = np.clip(shape, None, 0.0)
        out = neg + (w * (c * c) / (beta * 6.0 * rpm * u_pk))[::-1]
        return out

    def _split_on(self):
        """The physical cylinder split is part of the physical voice (F11)."""
        return bool(self.vx.get("cyl_split", False)
                    or self.vx.get("phys_voice", False))

    def _hdr_off(self):
        """Per-cylinder bank offsets: measured geometry in the physical
        voice, the preset's (possibly voiced) value in the classic one."""
        return (self._header_offset_phys if self.vx.get("phys_voice", False)
                else self._header_offset)

    def _struct_w(self):
        """Each cylinder's structural-path weight (see _struct_gain), by
        cylinder, power-normalised (mean w^2 = 1)."""
        s = self.params["cyl_spread"]
        w = np.maximum(1.0 + 0.55 * s * self._cyl_amp, 0.1)
        return w / math.sqrt(float(np.mean(w * w)))

    def _struct_gain(self, crank, ref_deg):
        """Each cylinder reaches the listener through its OWN path.

        The in-cylinder event shakes the block at that cylinder's own place --
        end or middle, near bank or far -- and the transfer from there to the
        radiating surfaces differs by a few dB from cylinder to cylinder (the
        classic NVH transfer-path spread), even when every cylinder burns
        alike; the intake's runners do the same for the mouth.  So each
        cylinder's weight is held through its slot -- from its event at
        ``ref_deg`` in its own cycle to the next cylinder's -- with a
        12-degree cross-fade.  The paths only SHARE OUT the radiator's power
        among the cylinders, they cannot add any, so the weights are power-
        normalised (mean w^2 = 1) -- and a structure's path spread does not
        change with the exhaust valve.  Their spread is the "cylinder spread"
        slider, the old per-cylinder personality, which is what it physically
        is."""
        w = self._struct_w()
        ev = np.mod(ref_deg - np.asarray(self._offsets, dtype=np.float64)
                    - np.asarray(self._hdr_off(), dtype=np.float64), 720.0)
        order = np.argsort(ev, kind="stable")
        ev, w = ev[order], w[order]
        a = np.mod(crank, 720.0)
        k = np.searchsorted(ev, a, side="right") - 1   # -1 wraps to the last
        since = np.mod(a - ev[k], 720.0)
        return w[k - 1] + (w[k] - w[k - 1]) * np.minimum(since / 12.0, 1.0)

    def _tk_beam(self, sig, cos_t, a_mouth, key, jet=None):
        """How an OPENING radiates depends on which way it points.

        A pipe end or an intake mouth is omnidirectional while it is small
        against the wavelength and starts to BEAM once its circumference
        catches up -- ka ~ 1, a = the mouth radius, so f = c / (2*pi*a).
        Below that the sound spills out everywhere; above it, it goes where the
        opening points.

        So the signal is split at ka = 1 and the upper band given a monopole +
        dipole pattern, D = (1 - b) + b * (1 + cos t) / 2, with t the angle
        between the opening's axis and the mic, normalised so its power
        averaged over the sphere is unity -- directivity moves energy around, it
        does not make any.  With b = 0.85 that is +4 dB straight down the axis,
        -0.7 dB side-on and -12 dB from behind.  The split is complementary
        (low + high is the input), so a pattern of 1 gives the input back.

        ``key`` names the opening: each keeps its own filter and its own last
        gain, which the next block ramps from.

        ``jet`` = (t_z, depth dB): the zone of silence the opening's own hot
        jet refracts round its axis (see _tk_jet).  The pattern is
        re-normalised with it, so the zone's energy reappears off-axis.
        """
        sr = self.sample_rate
        fc = min(343.0 / (2.0 * math.pi * max(a_mouth, 0.005)), sr * 0.45)
        b = 0.85
        A, B = 1.0 - 0.5 * b, 0.5 * b
        norm = 1.0 / math.sqrt(A * A + B * B / 3.0)
        d_new = norm * (A + B * cos_t)
        if jet is not None and jet[0] > 1e-3 and jet[1] > 0.0:
            tz, dep = jet
            w_lobe = math.radians(12.0)

            def zone(th):
                return 10.0 ** (-dep * np.clip(1.0 - th / tz, 0.0, 1.0) ** 2
                                / 20.0)

            def lobe(th):
                return np.exp(-((th - tz) / w_lobe) ** 2)
            # the lobe takes back exactly the power the zone lost: solve
            # <(pipe * zone * (1 + G lobe))^2> = <pipe^2> over the sphere
            gkey = (round(tz, 2), round(dep, 1))
            G = getattr(self, "_tk_lobe_g", {}).get(gkey)
            if G is None:
                th_s = np.linspace(0.0, math.pi, 181)
                wgt = np.sin(th_s) / np.sum(np.sin(th_s))
                pz = (A + B * np.cos(th_s)) * zone(th_s)
                lb = lobe(th_s)
                p0 = float(np.sum((A + B * np.cos(th_s)) ** 2 * wgt))
                qa = float(np.sum((pz * lb) ** 2 * wgt))
                qb = 2.0 * float(np.sum(pz * pz * lb * wgt))
                qc = float(np.sum(pz * pz * wgt)) - p0
                G = (-qb + math.sqrt(max(qb * qb - 4.0 * qa * qc, 0.0))) \
                    / (2.0 * max(qa, 1e-12))
                if not hasattr(self, "_tk_lobe_g"):
                    self._tk_lobe_g = {}
                self._tk_lobe_g[gkey] = G
            th = math.acos(min(max(cos_t, -1.0), 1.0))
            d_new = norm * (A + B * cos_t) * float(zone(th)) \
                * (1.0 + G * float(lobe(th)))
        if not hasattr(self, "_tk_gain"):
            self._tk_gain, self._tk_zi = {}, {}
        d_old = self._tk_gain.get(key, d_new)
        self._tk_gain[key] = d_new
        if not _HAVE_SCIPY:
            return sig
        bL, aL = self._bw(1, fc)
        zi = self._tk_zi.get(key)
        if zi is None:
            zi = np.zeros(1)
        low, self._tk_zi[key] = lfilter(bL, aL, sig, zi=zi)
        high = sig - low
        # ramped across the block: the angle swings fast at the pass, and a
        # step per block would zipper
        g = np.linspace(d_old, d_new, len(sig))
        return low + g * high

    def _tk_directivity(self, tail, x):
        """The tailpipe beams BACKWARDS.

        Its tips in the library put ka = 1 at 1.9-3.0 kHz: exactly the rasp
        band.  Which is the TV pass: coming towards you the pipe points away
        and the car sounds dull; once it is past you are looking up the pipe
        and the rasp arrives all at once.
        """
        eng = self.sim.engine
        L, hm = 12.0, 1.2
        race = self.straight_cut or eng.exhaust_openness > 0.85
        hs = 0.55 if race else 0.33
        a_tip = self._exhaust_outlet_radius()
        r1 = math.sqrt(x * x + L * L + (hm - hs) ** 2)
        # pipe axis points BACKWARD (-x); the mic is at (0, L, hm) from a car at
        # (x, 0, hs), so cos(theta) = x / r1: positive once the car is past.
        # ...and the sound leaves through the pipe's hot JET, which refracts
        # its top end out of a cone round that axis (_tk_jet)
        n_ = getattr(self, "_tk_jet_n", 0)
        if n_ % 8 == 0 or getattr(self, "_tk_jet_v", None) is None:
            self._tk_jet_v = self._tk_jet()         # slow: load, temperature
        self._tk_jet_n = n_ + 1
        return self._tk_beam(tail, x / r1, a_tip, "tail", jet=self._tk_jet_v)

    def _exhaust_outlet_radius(self):
        """The outlet the exhaust jet leaves through: the preset tip, or --
        if that could not pass the engine's flow -- the radius that passes
        the peak flow at ~120 m/s (road outlets: ~100-150 m/s), at the tip
        temperature flat out.  Constant per car (cached)."""
        eng = self.sim.engine
        a_tip = eng.exhaust_radius_m * max(getattr(eng, "tip_scale", 1.0), 0.5)
        cached = getattr(self, "_outlet_a", None)
        if cached is not None:
            return cached
        try:
            t_valve = max(self.sim.exhaust_gas_temp(rpm=eng.redline_rpm,
                                                    load=1.0), _TK_T_K)
        except Exception:
            t_valve = 1100.0
        rho_in = P_ATM * (1.0 + max(eng.boost_bar, 0.0)) / (287.0 * _TK_T_K)
        mdot = (rho_in * eng.total_displacement * eng.redline_rpm / 120.0
                * eng.ve_max * (1.0 + 1.0 / 14.7)
                / max(eng.exhaust_channels, 1))
        q = mdot / (P_ATM / (287.0 * 0.9 * t_valve))   # the tip ~0.9 of the valve
        self._outlet_a = max(a_tip, math.sqrt(q / (math.pi * 120.0)))
        return self._outlet_a

    def _tk_jet(self):
        """(t_z, depth dB) of the exhaust jet's zone of relative silence.

        Snell at the jet's edge: cos t_z = c_air / c_jet = sqrt(T_air / T_tip).
        T_tip: the cycle's exhaust temperature at the valve, cooled along the
        pipes -- the gas gives heat through the wall (U ~ 50 W/m^2 K: forced
        convection inside, the car's airflow outside) over the pipes' and the
        box's surface, T_tip = T_air + (T_valve - T_air) exp(-U A / mdot cp).
        Flat out ~1100 K (t_z ~ 60 deg); at idle the trickle cools to near
        ambient and the zone closes.  Its depth follows the jet's speed (a
        slow plume mixes before it can bend anything), full from ~60 m/s."""
        sim = self.sim
        eng = sim.engine
        try:
            map_f = sim._manifold_pressure() / P_ATM
            ve = sim._volumetric_efficiency(map_f)
        except Exception:
            return None
        t_air = _TK_T_K
        t_valve = max(sim.exhaust_gas_temp(), t_air)
        rho = P_ATM * map_f / (287.0 * t_air)             # the charge drawn in
        mdot = (rho * eng.total_displacement * max(sim.rpm, 0.0) / 120.0 * ve
                * (1.0 + 1.0 / 14.7) / max(eng.exhaust_channels, 1))
        if mdot < 1e-4:
            return None
        r = max(eng.exhaust_radius_m, 0.01)
        area = (2.0 * math.pi * r * max(eng.exhaust_total_m, 0.3)
                + 6.0 * max(eng.muffler_volume_m3, 0.0) ** (2.0 / 3.0))
        t_tip = t_air + (t_valve - t_air) * math.exp(-50.0 * area
                                                     / (mdot * 1150.0))
        tz = math.acos(min(math.sqrt(t_air / t_tip), 1.0))
        a_tip = self._exhaust_outlet_radius()
        u_jet = mdot / (P_ATM / (287.0 * t_tip) * math.pi * a_tip * a_tip)
        return tz, _TK_JET_DB * min(u_jet / 60.0, 1.0)

    def _intake_mouth_radius(self):
        """The intake mouth, sized to pass peak airflow at ~35 m/s:
        a = sqrt(Q / (pi * 35)), Q = displacement * redline / 120 * VE *
        (1 + boost)."""
        eng = self.sim.engine
        q = (eng.total_displacement * eng.redline_rpm / 120.0 * eng.ve_max
             * (1.0 + max(eng.boost_bar, 0.0)))
        return math.sqrt(q / (math.pi * 35.0))

    def _tk_sources(self):
        """Where each radiator sits on the car, (dx, height) in metres, dx
        along the direction of travel from mid-wheelbase.  The exhaust leaves
        at the tail; the intake breathes where the engine does -- the grille
        (front-engined), behind the cabin (mid) or the rear deck (the 911s);
        the block and its housings sit with the engine.  A single-seater:
        airbox over the driver's head, exits at the back of the engine
        cover."""
        eng = self.sim.engine
        race = self.straight_cut or eng.exhaust_openness > 0.85
        hs = 0.55 if race else 0.33              # pipe exit height
        if getattr(eng, "open_cockpit", False):
            return dict(exh=(-1.6, 0.75), intake=(-0.3, 1.0), body=(-0.9, 0.45))
        lay = getattr(eng, "engine_layout", "front")
        if lay == "mid":
            return dict(exh=(-2.2, hs), intake=(-0.4, 1.0), body=(-0.9, 0.5))
        if lay == "rear":
            return dict(exh=(-2.3, hs), intake=(-1.9, 0.9), body=(-1.7, 0.5))
        return dict(exh=(-2.2, hs), intake=(1.9, 0.6), body=(1.3, 0.5))

    def _tk_walls(self, sig, v, M, L, hm, frames):
        """The track's two walls: the barrier across it and the facade behind
        the mic, as image sources in the two planes -- each wall once and each
        pair of bounces between them.  One delay buffer, four read heads, each
        at its own retarded time (its own Doppler).

        A wall reflects only what it is big enough to: coherently where it
        covers the first Fresnel zone, f > c d1 d2 / ((d1 + d2) h^2) (d1, d2
        the lateral legs to and from it, h its height) -- a first-order
        high-pass there.  And the long legs lose their top end to the air (a
        one-pole where ISO 9613-1 takes 3 dB over the path)."""
        sr = self.sample_rate
        z = 0.5
        a_, b_ = -_TK_WALL_M, L + _TK_NEAR_M          # the two planes (lateral)
        # (image lateral position, reflections as (wall, leg-to, leg-from))
        imgs = (
            (2.0 * a_, (("far", -a_, L - a_),)),
            (2.0 * b_, (("near", b_, b_ - L),)),
            (2.0 * b_ - 2.0 * a_, (("far", -a_, b_ - a_), ("near", b_ - a_, b_ - L))),
            (2.0 * a_ - 2.0 * b_, (("near", b_, b_ - a_), ("far", b_ - a_, L - a_))),
        )
        taus, gains, fcs, fas = [], [], [], []
        for y_img, refl in imgs:
            ly = abs(y_img - L)
            t_, xe_ = self._track.retarded(v, ly * ly + (hm - z) ** 2)
            r_ = 343.0 * t_
            g = L / r_ * (1.0 + M * xe_ / r_) ** -2
            fc = 20.0
            for wall, d1, d2 in refl:
                R, h = ((_TK_WALL_R, _TK_WALL_H) if wall == "far"
                        else (_TK_NEAR_R, _TK_NEAR_H))
                g *= R
                fc = max(fc, 343.0 * d1 * d2 / ((d1 + d2) * h * h))
            taus.append(t_ * sr)
            gains.append(g)
            fcs.append(fc)
            fas.append(self._air_f3db(r_))
        heads = self._tk_img.process(sig, taus)
        out = np.zeros(frames)
        for k in range(4):
            y_ = heads[k] * gains[k]
            if _HAVE_SCIPY:
                q = lambda f: 2.0 ** (round(12.0 * math.log2(f)) / 12.0)
                bH, aH = self._bw(1, q(min(fcs[k], sr * 0.4)), btype="high")
                y_, self._tk_img_zi[k][0] = lfilter(bH, aH, y_,
                                                    zi=self._tk_img_zi[k][0])
                bL, aL = self._bw(1, q(min(fas[k], sr * 0.45)))
                y_, self._tk_img_zi[k][1] = lfilter(bL, aL, y_,
                                                    zi=self._tk_img_zi[k][1])
            out = out + y_
        return out

    def _air_f3db(self, r):
        """Where ISO 9613-1 takes 3 dB over a path of r metres (cached)."""
        key = int(r / 5.0)
        if not hasattr(self, "_f3db"):
            self._f3db = {}
        f = self._f3db.get(key)
        if f is None:
            lo, hi = 200.0, 24000.0
            for _ in range(24):
                mid = math.sqrt(lo * hi)
                if _iso9613_db_per_m(mid) * max(key * 5.0 + 2.5, 1.0) > 3.0:
                    hi = mid
                else:
                    lo = mid
            f = self._f3db[key] = lo
        return f

    def _tk_rolling(self, frames):
        """Tyre/road and wind noise, from the car (Leo: very small).

        Tyres: a band round the 1 kHz octave (tread impact and air pumping),
        ~32 dB per decade of speed (tyre/road noise grows as the 3rd-4th power
        of speed).  Wind: a mid band (A-pillars, mirrors, wheel arches), a
        dipole -- the 6th power, 60 dB per decade -- so it overtakes the tyres
        only at the very top.  At 270 km/h they sit _TK_TYRE_DB and
        _TK_WIND_DB under the engine at full load (the auto-level's target);
        under 150 km/h they are gone.  They leave from the axles: each with
        its own retarded time, 1/r, convective amplification, and the air."""
        sr = self.sample_rate
        v, ((tf, xf), (tr, xr)) = self._tk_tire_geo
        if v < 5.0:
            return np.zeros(frames)
        if getattr(self, "_tk_roll", None) is None:
            self._tk_roll = dict(taps=_MovingTaps(int(3.0 * sr), 2),
                                 air=_AirFIR(sr), zt=np.zeros(2),
                                 zw1=np.zeros(1), zw2=np.zeros(1))
        st = self._tk_roll
        u = v / 75.0
        a_t = 0.22 * 10.0 ** (_TK_TYRE_DB / 20.0) * u ** 1.6
        a_w = 0.22 * 10.0 ** (_TK_WIND_DB / 20.0) * u ** 3.0
        n1 = self._rng.standard_normal(frames)
        n2 = self._rng.standard_normal(frames)
        if _HAVE_SCIPY:
            f0 = min(1000.0 * u ** 0.25, sr * 0.4)      # the peak drifts up
            bT, aT = _bandpass(2.0 ** (round(12.0 * math.log2(f0)) / 12.0),
                               0.8, sr)
            tyre, st["zt"] = lfilter(bT, aT, n1, zi=st["zt"])
            bW1, aW1 = self._bw(1, 150.0, btype="high")
            wind, st["zw1"] = lfilter(bW1, aW1, n2, zi=st["zw1"])
            bW2, aW2 = self._bw(1, 2500.0)
            wind, st["zw2"] = lfilter(bW2, aW2, wind, zi=st["zw2"])
            # unit rms for the band-passes (Q 0.8: ~0.55; 150-2500 Hz: ~0.37)
            src = a_t * tyre / 0.55 + a_w * wind / 0.37
        else:
            src = (a_t + a_w) * n1 * 0.5
        M = min(v, 0.8 * 343.0) / 343.0
        df, dr = st["taps"].process(src, (tf * sr, tr * sr))
        rf, rr = 343.0 * tf, 343.0 * tr
        out = 0.5 * (df * (12.0 / rf) * (1.0 + M * xf / rf) ** -2
                     + dr * (12.0 / rr) * (1.0 + M * xr / rr) ** -2)
        return st["air"].process(out, 0.5 * (rf + rr))

    def _tk_intake(self, mouth, x):
        """The intake mouth beams too -- the OTHER way.

        An inlet faces into the airstream to catch ram pressure: an F1 airbox
        over the driver's head, a road car's snorkel behind the grille.  Same
        opening, same pattern, pointed FORWARD.

        Its size is not a free number.  An inlet is sized to pass the engine's
        peak airflow at a modest velocity, ~35 m/s: faster costs pressure drop,
        which grows as v^2, and makes the inlet itself whistle.  So

            Q = displacement * redline / 120 * VE * (1 + boost)    [m^3/s]
            a = sqrt(Q / (pi * 35))

        which lands between ~4 cm (a 1.5 litre road turbo) and ~7 cm (a 5.2
        litre V8) and puts ka = 1 at 0.7-1.4 kHz.  Bigger mouths than the
        tailpipes, so the intake beams over a WIDER band than the exhaust does
        -- and in the opposite direction.  That swap is the pass: the howl
        comes at you, the rasp goes away from you.
        """
        eng = self.sim.engine
        L, hm = 12.0, 1.2
        race = self.straight_cut or eng.exhaust_openness > 0.85
        hi = 0.90 if race else 0.60          # roll-hoop airbox / grille snorkel
        a_in = self._intake_mouth_radius()
        r = math.sqrt(x * x + L * L + (hm - hi) ** 2)
        # the mouth faces FORWARD (+x): cos(theta) = -x / r, positive while the
        # car is still coming
        return self._tk_beam(mouth, -x / r, a_in, "intake")

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
        # THE LIFT is the event, not the coasting: crossing from on-gas to shut
        # is what fills the budget, and it only refills by going back on the
        # gas.  The pipe holds a finite amount of unburnt charge -- once it has
        # burnt off there is nothing left to light until you fill it again.
        # Without this it crackles all the way down to idle, which is a
        # fireworks display rather than a car.  How many depends on how loaded
        # the pipe was, so a lift after a hard pull gives four and a lift after
        # trundling gives two.
        now_on_gas = sim.throttle > 0.5
        if self._pop_on_gas and not now_on_gas:
            # floor(x+0.5), NOT round(): Swift rounds halves away from zero and
            # Python rounds them to even, so round(0.5) is 0 here and 1 there.
            self._pop_budget = 2 + int(math.floor(self._was_on_gas * 2.0 + 0.5))
        self._pop_on_gas = now_on_gas
        overrun = (sim.ignition_on and sim.throttle < 0.06
                   and rpm > eng.idle_rpm * 1.5)
        # trigger a new pop once the previous one is mostly done (allows crackle)
        if overrun and self._pop_budget > 0 and self._pop_age > self._pop_len * 0.45:
            rf = min(rpm / max(eng.redline_rpm, 1.0), 1.0)
            aggr = 2.4 if eng.anti_lag else 1.0
            rate = lvl * aggr * (0.06 + 0.55 * rf) * (0.3 + 0.7 * self._was_on_gas)
            if self._rng.random() < rate:
                big = self._rng.random() < (0.3 if eng.anti_lag else 0.14)
                self._pop_age = 0
                self._pop_len = int(sr * (0.16 if big else 0.085))
                self._pop_f0 = (95.0 if big else 150.0) * (0.85 + 0.4 * self._rng.random())
                self._pop_amp = (1.0 if big else 0.6) * (0.6 + 0.7 * self._rng.random())
                self._pop_budget -= 1
                self.pops_fired += 1
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
        # A sink IS the output, so it must be checked before `enabled` -- that
        # flag only asks whether one of the BUILT-IN backends could work, and
        # on iOS neither can: there is no PortAudio and no pygame.  Gating the
        # sink on it made the synth refuse to start on any machine without
        # sounddevice installed, which is exactly the platform the sink exists
        # for.  (Found on the first macOS run of the port.)
        if self.sink is not None:                # caller supplies the output
            return self._start_sink()
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
        # CAR MODE draws NOTHING, so nothing can stall the GIL -- it lowers
        # host_latency to buy that 60 ms back, where every ms is a ms your foot
        # is ahead of the sound.
        OB, OL = BLOCK, self.host_latency
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

    # --- Generic sink backend: hand blocks to whatever the platform provides ---
    def _start_sink(self):
        """Render into ``self.sink`` instead of opening a sound device.

        ``sink(block)`` is called with a C-contiguous ``(frames, 2) float32``
        array, already panned for the current POV, and may keep no reference to
        it after returning (the buffer is reused).  Return value is ignored.

        This is the platform seam.  An iOS build drives AVAudioEngine through
        rubicon-objc from here; a Pi can write straight to ALSA; a test can
        count blocks on a machine with no audio hardware at all.  Nothing about
        the voicing changes -- the sink sits strictly after the render.

        A sink that BLOCKS until it has consumed the block sets the pace by
        itself.  One that returns immediately is paced here off the sample
        rate, so a non-blocking sink cannot spin the CPU or race ahead.
        """
        sr = int(self.sample_rate or SAMPLE_RATE)
        self._rebuild_for_rate(sr)
        self.sample_rate = sr
        self._sink_run = True
        self._sink_blocks = 0
        self._sink_thread = threading.Thread(target=self._sink_feed, daemon=True)
        self._sink_thread.start()
        self.mode = "sink"
        self.latency_ms = round(BLOCK / sr * 1000.0, 1)
        return True

    def _sink_feed(self):
        import time
        CH = BLOCK
        period = CH / float(self.sample_rate)
        buf = np.empty((CH, 2), dtype=np.float32)
        deadline = time.monotonic()
        while self._sink_run:
            t_start = time.monotonic()
            try:
                mono = self._render_block(CH)
                ang = self.params["spatial_x"] * (math.pi * 0.5)
                buf[:, 0] = mono * math.cos(ang)
                buf[:, 1] = mono * math.sin(ang)
                np.clip(buf, -1.0, 1.0, out=buf)
                spent = time.monotonic() - t_start   # render only, before the sink
                self.load += (spent / period - self.load) * 0.05
                self.sink(buf)
                self._sink_blocks += 1
            except Exception:
                time.sleep(0.01)
                continue
            deadline += period
            slack = deadline - time.monotonic()
            if slack > 0:
                time.sleep(slack)
            elif slack < -0.5:                   # a slow sink: stop accumulating
                deadline = time.monotonic()

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
        self._sink_run = False
        if getattr(self, "_sink_thread", None) is not None:
            try:
                self._sink_thread.join(timeout=0.3)
            except Exception:
                pass
            self._sink_thread = None
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
