"""
exhaust_tmm — white-box exhaust ACOUSTICS from the transmission-line / reflection
physics of the real pipe geometry.  Replaces the hand-tuned resonance-mix knobs
(res1, res2, wall, muffler) that Leo (rightly) called fudge: here they FALL OUT
of the geometry instead of being dialled in.

Physics (1-D duct acoustics):

  * A standing wave forms in a pipe only as strongly as its ends REFLECT.  At an
    area change A1 -> A2 the pressure-wave reflection coefficient is
        R = (A2 - A1) / (A2 + A1).
    So the primary runner's resonance strength (res1) is the reflection at the
    runner -> collector expansion, and the full-system resonance (res2) is the
    open tailpipe end's reflection surviving back through the muffler.

  * A muffler is an EXPANSION CHAMBER: area ratio m = A_chamber / A_pipe gives a
    transmission loss  TL = 10 log10(1 + ((m - 1/m)/2)^2 sin^2(kL)) — a comb of
    notches.  Its depth (the 'muffler' knob) is set by m; a big-bore straight-
    through (m~1) barely notches, a fat chambered box notches hard.

  * WALL / viscothermal + radiation loss scales with surface-to-volume, i.e.
    1/radius (a thin pipe loses more to its walls) and with pipe length.

Everything comes from the preset's real exhaust dimensions (primary length/bore,
collector radius, muffler volume/neck/type, tailpipe).  ONE reference scale per
quantity (like map_model's K_BALANCE) anchors a typical system; the PER-CAR
spread is pure geometry.
"""

from __future__ import annotations

import math


def _prim_area(eng):
    """Primary (header) tube cross-section — explicit bore if given, else the
    valve-derived default (same rule gas_truth uses)."""
    bore = eng.cylinders[0].bore
    d_ev = 0.83 * bore * (0.39 if getattr(eng, "valves_per_cyl", 4) >= 4 else 0.47)
    prim_bore = getattr(eng, "exhaust_primary_bore_m", 0.0)
    if prim_bore > 0.0:
        return math.pi * 0.25 * prim_bore * prim_bore
    return math.pi * 0.25 * (1.15 * d_ev) ** 2


def exhaust_acoustics(eng):
    """Return (res1, res2, wall, muffler) DERIVED from the exhaust geometry —
    the white-box replacement for the hand-tuned mixer knobs."""
    r_col = max(getattr(eng, "exhaust_radius_m", 0.03), 0.008)
    a_col = math.pi * r_col * r_col                       # collector / mid-pipe
    a_prim = _prim_area(eng)                              # header primary tube
    tip = getattr(eng, "tip_scale", 1.0)
    a_tail = math.pi * (r_col * tip) ** 2                 # tailpipe mouth

    # --- res1: primary-runner resonance = |R| at the runner->collector expansion.
    # A big step (small primary into a fat collector) reflects hard -> strong,
    # peaky runner resonance; a gentle step barely reflects.  Scaled by how much
    # of the field the OPEN end throws away (see res2's law below).
    open_frac0 = min(max(getattr(eng, "exhaust_openness", 0.7), 0.2), 1.0)
    r_step = abs(a_col - a_prim) / (a_col + a_prim)
    res1 = min(max(0.45 * r_step / 0.55 * (1.0 - 0.72 * open_frac0), 0.03),
               0.45)                                       # 0.55 = typical step

    # --- res2: full-system resonance = the open tailpipe end reflecting back
    # THROUGH the muffler.  Open end reflects strongly at low f; a reflective
    # (chambered) muffler sends more of it back than an absorptive one; an open
    # tailpipe (big tip) lets more escape (weaker return).
    reflective = getattr(eng, "muffler_type", "reflective") == "reflective"
    muff_return = 0.72 if reflective else 0.45           # fraction reflected back
    open_frac = min(max(getattr(eng, "exhaust_openness", 0.7), 0.2), 1.0)
    # a wider tailpipe radiates more away -> weaker standing wave (res2 down),
    # but a more OPEN system rings longer (res2 up): net from geometry.
    # RE-ANCHORED (Leo: "所有的管道都没质心，都没有管子的混响"): the old anchor
    # (aven res2 ~ 0.30) calibrated the pipe as a GARNISH under a dominant direct
    # bang — but in a real duct the transmitted spectrum rides a 6-12 dB modal
    # ripple: the standing-wave field is comparable to the through-wave at
    # resonance.  New anchor puts the reference (open absorptive V12) at
    # res2 ~ 0.75 so the PIPE IS THE MEDIUM; per-car spread is unchanged
    # geometry.  (One calibration constant, not per-car.)
    # THE TRANSMITTED-vs-REVERBERANT LAW (Leo's 0.05 slider experiment): an
    # OPEN system transmits nearly everything on the first pass — that's why
    # it's loud and raw (the chainsaw) — and its standing-wave field is only a
    # ripple; a CHAMBERED quiet system blocks the direct wave, so what exits
    # is mostly the reverberant field.  The old (0.6+0.6*open) factor had this
    # BACKWARDS (more open -> bigger field), which buried the Aventador's rasp
    # under a comb it physically doesn't have.  Now the field COLLAPSES with
    # openness; the direct share grows to match (see audio.py sig combine).
    # (collapse steepened again per Leo's 0.04-slider calibration: the open
    # cars want the field at near-garnish level; the direct wave is the voice)
    tail_ratio = a_tail / a_col
    # (openness collapse steepened to Leo's 0.07 slider point: open cars'
    # field is a whisper; boxed cars unchanged)
    res2 = min(max(1.00 * muff_return * (1.08 - 0.95 * open_frac)
                   / max(tail_ratio, 0.5), 0.04), 0.68)

    # --- wall: viscothermal + radiation loss ~ 1/radius (thin pipe = more wall
    # interaction, duller ring) scaled by the system length (more pipe = more
    # loss).  Small-bore long systems are woolier; big-bore short race pipes
    # keep the metallic edge.
    length = max(getattr(eng, "exhaust_total_m", 1.6), 0.3)
    wall = min(max(0.30 * (0.022 / r_col) * (length / 1.8) ** 0.5, 0.08), 0.9)

    # --- muffler: expansion-chamber notch depth ~ the area ratio m of the box to
    # the pipe (bigger box = deeper transmission-loss comb).  An absorptive
    # (straight-through packed) muffler makes almost no comb.
    v_muff = max(getattr(eng, "muffler_volume_m3", 0.003), 1e-5)
    l_box = max(getattr(eng, "muffler_neck_len_m", 0.08) * 4.0, 0.15)  # ~box length
    a_box = v_muff / l_box                               # effective chamber area
    m_ratio = max(a_box / a_col, 1.0)
    depth = math.log10(m_ratio + 1.0) / math.log10(6.0)  # 0..~1 over m 1..5
    muffler = min(max(depth * (1.0 if reflective else 0.35), 0.05), 1.0)

    return res1, res2, wall, muffler
