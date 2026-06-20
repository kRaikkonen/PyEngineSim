"""
Built-in engine presets — the modular, selectable engine library.

These play the role of the original game's ``.mr`` engine-definition scripts:
each builder returns a fully configured :class:`Engine` (including its real
gearbox).  To add an engine, write a builder and append it to ``PRESETS`` — it
then shows up in the in-app selector and on a number key automatically.

Firing is made even by spacing each cylinder's cycle offset equally across the
720 deg four-stroke cycle; the *order* of the offsets sets the firing order.
"""

from __future__ import annotations

from .engine import Cylinder, Engine
from .units import mm


def _even_offsets(n: int, firing_order=None):
    """Evenly spaced cycle offsets (deg) for an n-cylinder four-stroke.

    If a firing order is given (1-based cylinder numbers), offsets are assigned
    in that sequence; otherwise cylinders fire in index order.
    """
    spacing = 720.0 / n
    offsets = [0.0] * n
    order = firing_order or list(range(1, n + 1))
    for slot, cyl_number in enumerate(order):
        offsets[cyl_number - 1] = slot * spacing
    return offsets


# --------------------------------------------------------------------- engines

def porsche_911_h6() -> Engine:
    """Porsche 911 3.8 air-cooled flat-six (993-era).

    Horizontally-opposed boxer six, firing order 1-6-2-4-3-5.  ~102 x 77.5 mm
    bore/stroke (3.8 L), ~11.5:1 CR, ~7000 rpm.  993 6-speed (G50/21).  The
    boxer's two banks give that distinctive Porsche flat-six warble.
    """
    offsets = _even_offsets(6, firing_order=[1, 6, 2, 4, 3, 5])
    cylinders = []
    for i in range(6):
        bank = -90.0 if i < 3 else 90.0          # horizontally opposed
        cylinders.append(
            Cylinder(bore=mm(102), stroke=mm(77.5), rod_length=mm(127),
                     compression_ratio=11.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Porsche 911 3.8 flat-six (993)",
        cylinders=cylinders,
        flywheel_inertia=0.16,
        redline_rpm=7000,
        idle_rpm=900,
        heat_release_k=3.6,
        ve_peak_frac=0.7,
        friction_static=6.0,
        closed_map_fraction=0.11,
        exhaust_tone=85.0,
        exhaust_primary_m=0.50, exhaust_total_m=1.7, exhaust_radius_m=0.024,
        exhaust_channels=2, exhaust_openness=0.78, muffler_volume_m3=0.002,
        gear_ratios=[3.82, 2.05, 1.41, 1.12, 0.92, 0.75],   # 993 G50 6-speed
        final_drive=3.44,
        vehicle_mass=1370.0, wheel_radius=0.32, clutch_capacity=470.0,
    )


def vw_ea888_i4() -> Engine:
    """VW/Audi EA888 2.0 TFSI turbo inline-four (as in the Golf R).

    Firing order 1-3-4-2, 82.5 x 92.8 mm (2.0 L), 9.6:1 CR, ~6800 rpm.  We don't
    model the turbo plumbing, but a flat, fat torque curve (wide VE + strong
    heat release) and the Golf R gearbox give it that torquey turbo-four feel.
    """
    offsets = _even_offsets(4, firing_order=[1, 3, 4, 2])
    cylinders = [
        Cylinder(bore=mm(82.5), stroke=mm(92.8), rod_length=mm(144),
                 compression_ratio=9.6, cycle_offset_deg=offsets[i])
        for i in range(4)
    ]
    return Engine(
        name="VW/Audi EA888 2.0 TFSI (Golf R)",
        cylinders=cylinders,
        flywheel_inertia=0.24,
        redline_rpm=6800,
        idle_rpm=850,
        heat_release_k=6.5,
        ve_peak_frac=0.45,               # turbo: torque peaks low and stays flat
        ve_width_frac=0.8,
        induction="turbo", boost_bar=1.1, turbo_lag=0.4,
        exhaust_tone=100.0,
        exhaust_primary_m=0.42, exhaust_total_m=1.9, exhaust_radius_m=0.022,
        exhaust_channels=1, exhaust_openness=0.58, muffler_volume_m3=0.0028,
        has_gpf=True,                            # modern EU emissions
        gear_ratios=[3.36, 2.09, 1.47, 1.10, 0.93, 0.76],   # Golf R 6-speed
        final_drive=4.43,
        vehicle_mass=1500.0, wheel_radius=0.31, clutch_capacity=420.0,
    )


def ford_coyote_v8() -> Engine:
    """Ford 'Coyote' 5.0 V8 (Mustang GT) — cross-plane muscle.

    Firing order 1-5-4-8-6-3-7-2, 92.2 x 92.7 mm (5.0 L), ~11:1 CR, ~7400 rpm.
    Cross-plane crank: banks 1-4 / 5-8 fire UNEVENLY, which (with the two
    separate exhaust channels) makes the classic American V8 burble/rumble.
    Mustang GT MT82 6-speed.
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 4, 8, 6, 3, 7, 2])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0          # banks 1-4 vs 5-8 (cross-plane)
        cylinders.append(
            Cylinder(bore=mm(92.2), stroke=mm(92.7), rod_length=mm(151),
                     compression_ratio=11.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Ford Coyote 5.0 V8 (Mustang GT)",
        cylinders=cylinders,
        flywheel_inertia=0.42,
        redline_rpm=7400,
        idle_rpm=800,
        heat_release_k=4.0,
        ve_peak_frac=0.6,
        friction_static=5.0,
        closed_map_fraction=0.22,
        starter_torque=170.0,
        exhaust_tone=55.0,               # deep muscle rumble
        exhaust_primary_m=0.72, exhaust_total_m=2.1, exhaust_radius_m=0.028,
        exhaust_channels=2, exhaust_openness=0.62, muffler_volume_m3=0.0035,
        gear_ratios=[3.66, 2.43, 1.69, 1.32, 1.00, 0.65],   # Mustang GT MT82
        final_drive=3.55,
        vehicle_mass=1750.0, wheel_radius=0.34, clutch_capacity=640.0,
    )


def ferrari_458() -> Engine:
    """Ferrari 458 Italia — F136 FB 4.5 L **flat-plane** V8.

    Real specs: 94 x 81 mm bore/stroke, 12.5:1 CR, redline 9000 rpm,
    570 PS @ 9000 / 540 Nm @ 6000, firing order 1-5-3-7-4-8-2-6, and the
    Getrag 7DCL750 7-speed dual-clutch gearbox (final drive 5.14).

    Being flat-plane, the crankpins sit at 180 deg and the banks fire in strict
    L-R-L-R alternation, so the exhaust pulses are perfectly even — that's the
    source of the high, flat 'scream' (versus a cross-plane V8's burble).
    """
    order = [1, 5, 3, 7, 4, 8, 2, 6]
    offsets = _even_offsets(8, firing_order=order)
    cylinders = []
    for i in range(8):
        bank = -45.0 if i % 2 == 0 else 45.0     # 90 deg V
        cylinders.append(
            Cylinder(bore=mm(94), stroke=mm(81), rod_length=mm(149),
                     compression_ratio=12.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Ferrari 458 Italia 4.5L flat-plane V8",
        cylinders=cylinders,
        flywheel_inertia=0.20,           # light flywheel — revs eagerly to 9k
        redline_rpm=9000,
        idle_rpm=950,
        heat_release_k=3.8,
        closed_map_fraction=0.10,        # deep idle vacuum so it idles ~1000
        ve_peak_frac=0.67,               # peak torque ~6000 rpm
        ve_width_frac=0.62,              # keeps breathing up to the 9000 redline
        friction_static=8.0,
        friction_quad=6.0e-5,
        starter_torque=170.0,
        exhaust_tone=120.0,              # high, raspy flat-plane voice
        # flat-plane fires evenly -> banks merge into one even channel; near-open
        # race exhaust -> bright, ringing (high openness)
        exhaust_primary_m=0.62, exhaust_total_m=2.2, exhaust_radius_m=0.025,
        exhaust_channels=1, exhaust_openness=0.92, muffler_volume_m3=0.0015,
        gear_ratios=[3.08, 2.18, 1.63, 1.29, 1.03, 0.84, 0.69],
        final_drive=5.14,
        vehicle_mass=1485.0,             # 458 curb weight ~1485 kg
        wheel_radius=0.345,
        clutch_capacity=640.0,
    )


def lexus_lfa() -> Engine:
    """Lexus LFA — 1LR-GUE 4.8 L 72-deg V10 (Yamaha-tuned titanium scream).

    Real specs: 4805 cc, redline 9000 rpm (fuel cut 9500), 560 PS @ 8700 /
    480 Nm @ 6800, titanium valvetrain & exhaust.  Bore 88 x stroke 79 mm,
    ~12:1 CR.  6-speed ASG (3.231/2.188/1.609/1.233/0.970/0.795, final 3.417).
    Even-firing V10 -> a smooth, screaming, very high-pitched metallic note.
    """
    offsets = _even_offsets(10)                      # even 72-deg firing
    cylinders = []
    for i in range(10):
        bank = -36.0 if i % 2 == 0 else 36.0         # 72-deg V
        cylinders.append(
            Cylinder(bore=mm(88), stroke=mm(79), rod_length=mm(130),
                     compression_ratio=12.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Lexus LFA 4.8L V10 (1LR-GUE)",
        cylinders=cylinders,
        flywheel_inertia=0.17,           # famously fast-revving (light flywheel)
        redline_rpm=9000,
        idle_rpm=950,
        heat_release_k=3.5,
        closed_map_fraction=0.11,
        ve_peak_frac=0.76,               # peak torque ~6800 rpm
        ve_width_frac=0.62,
        friction_static=8.0,
        friction_quad=6.0e-5,
        starter_torque=170.0,
        exhaust_tone=135.0,              # very high titanium voice
        exhaust_primary_m=0.60, exhaust_total_m=1.9, exhaust_radius_m=0.024,
        exhaust_channels=1, exhaust_openness=0.95, muffler_volume_m3=0.0015,
        gear_ratios=[3.231, 2.188, 1.609, 1.233, 0.970, 0.795],
        final_drive=3.417,
        vehicle_mass=1480.0, wheel_radius=0.340, clutch_capacity=560.0,
    )


def lamborghini_murcielago() -> Engine:
    """Lamborghini Murcielago LP640 — 6.5 L 60-deg V12 howl.

    Real specs: 6496 cc, bore 88 x stroke 89 mm, 11.0:1 CR, 640 PS @ 8000 /
    660 Nm @ 6000, redline ~8000 rpm.  6-speed (approx ratios).  Even-firing
    60-deg V12 -> a deep, complex, layered metallic howl.
    """
    offsets = _even_offsets(12)                      # even 60-deg firing
    cylinders = []
    for i in range(12):
        bank = -30.0 if i < 6 else 30.0              # banks 1-6 / 7-12
        cylinders.append(
            Cylinder(bore=mm(88), stroke=mm(89), rod_length=mm(140),
                     compression_ratio=11.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Lamborghini Murcielago LP640 6.5L V12",
        cylinders=cylinders,
        flywheel_inertia=0.32,
        redline_rpm=8000,
        idle_rpm=900,
        heat_release_k=3.4,
        closed_map_fraction=0.09,
        ve_peak_frac=0.72,               # peak torque ~6000 rpm
        ve_width_frac=0.6,
        friction_static=10.0,
        starter_torque=200.0,
        exhaust_tone=70.0,               # deep V12
        exhaust_primary_m=0.70, exhaust_total_m=2.3, exhaust_radius_m=0.028,
        exhaust_channels=2, exhaust_openness=0.80, muffler_volume_m3=0.0035,
        gear_ratios=[2.94, 2.06, 1.52, 1.18, 0.94, 0.76],
        final_drive=3.45,
        vehicle_mass=1665.0, wheel_radius=0.345, clutch_capacity=720.0,
    )


def ferrari_f2004_v10() -> Engine:
    """Ferrari F2004 — Tipo 053 3.0 L 90-deg V10 Formula 1 engine.

    The screaming 2004 F1 V10: ~3.0 L, ~96 x 41.4 mm (wildly oversquare to rev),
    ~13:1 CR, ~900 PS at ~18000 rpm, redline ~18500.  Almost no flywheel, so it
    spins up instantly.  Firing every 72 deg -> a 1500 Hz wail at full song.
    """
    offsets = _even_offsets(10)                      # even 72-deg firing
    cylinders = []
    for i in range(10):
        bank = -45.0 if i < 5 else 45.0              # 90-deg V
        cylinders.append(
            Cylinder(bore=mm(96), stroke=mm(41.4), rod_length=mm(95),
                     compression_ratio=13.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Ferrari F2004 3.0 V10 (F1)",
        cylinders=cylinders,
        flywheel_inertia=0.045,          # F1: revs almost instantly
        redline_rpm=18500,
        idle_rpm=3600,                   # F1 engines idle high
        heat_release_k=3.9,
        ve_peak_frac=0.82,               # peak torque high up (~15000)
        ve_width_frac=0.6,
        friction_static=9.0,
        friction_linear=0.010,
        friction_quad=7.0e-6,            # tiny: this engine lives at 18000 rpm
        starter_torque=120.0,
        exhaust_tone=185.0,              # very high F1 shriek
        exhaust_primary_m=0.40, exhaust_total_m=0.85, exhaust_radius_m=0.020,
        exhaust_channels=1, exhaust_openness=0.98, muffler_volume_m3=0.0008,
        has_cat=False,                           # open race exhaust, no cat/GPF
        gear_ratios=[2.50, 1.95, 1.60, 1.36, 1.18, 1.04, 0.92],  # close F1 7-speed
        final_drive=4.20,
        vehicle_mass=650.0, wheel_radius=0.33, clutch_capacity=400.0,
    )


def dodge_hellcat_v8() -> Engine:
    """Dodge Challenger SRT Hellcat — 6.2 L supercharged HEMI V8.

    Roots/twin-screw positive-displacement blower (~0.8 bar) — the iconic
    rpm-tracking supercharger WHINE.  92.... 103.9 x 90.9 mm, cross-plane,
    firing 1-8-4-3-6-5-7-2, ~6200 rpm, ~707 hp.
    """
    offsets = _even_offsets(8, firing_order=[1, 8, 4, 3, 6, 5, 7, 2])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0
        cylinders.append(
            Cylinder(bore=mm(103.9), stroke=mm(90.9), rod_length=mm(155),
                     compression_ratio=9.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Dodge Hellcat 6.2 supercharged V8",
        cylinders=cylinders,
        flywheel_inertia=0.45, redline_rpm=6200, idle_rpm=720,
        heat_release_k=2.5, ve_width_frac=0.75, closed_map_fraction=0.20,
        friction_static=7.0, starter_torque=180.0,
        exhaust_tone=52.0,
        exhaust_primary_m=0.75, exhaust_total_m=2.2, exhaust_radius_m=0.029,
        exhaust_channels=2, exhaust_openness=0.6, muffler_volume_m3=0.0035,
        induction="roots", boost_bar=0.8, blower_ratio=9.0,
        valvetrain="ohv", valves_per_cyl=2,      # pushrod 2-valve HEMI
        gear_ratios=[2.97, 2.07, 1.43, 1.00, 0.84, 0.57], final_drive=2.62,
        vehicle_mass=2020.0, wheel_radius=0.34, clutch_capacity=900.0,
    )


def toyota_2jz_supra() -> Engine:
    """Toyota Supra 2JZ-GTE — 3.0 L twin-turbo inline-six.

    86 x 86 mm, firing 1-5-3-6-2-4, ~8.5:1 CR, ~7000 rpm.  Turbo spool + a big
    blow-off-valve 'pshhh' on lift.  ~1.0 bar.
    """
    offsets = _even_offsets(6, firing_order=[1, 5, 3, 6, 2, 4])
    cylinders = [
        Cylinder(bore=mm(86), stroke=mm(86), rod_length=mm(142),
                 compression_ratio=8.5, cycle_offset_deg=offsets[i])
        for i in range(6)
    ]
    return Engine(
        name="Toyota Supra 2JZ-GTE 3.0 twin-turbo I6",
        cylinders=cylinders,
        flywheel_inertia=0.26, redline_rpm=7000, idle_rpm=800,
        heat_release_k=4.6, ve_width_frac=0.75, closed_map_fraction=0.17,
        exhaust_tone=88.0,
        exhaust_primary_m=0.5, exhaust_total_m=2.0, exhaust_radius_m=0.026,
        exhaust_channels=1, exhaust_openness=0.62, muffler_volume_m3=0.003,
        induction="turbo", boost_bar=1.0, turbo_lag=0.55,
        gear_ratios=[3.83, 2.36, 1.69, 1.31, 1.00, 0.79], final_drive=3.13,
        vehicle_mass=1560.0, wheel_radius=0.32, clutch_capacity=520.0,
    )


def bmw_s58() -> Engine:
    """BMW S58 — 3.0 L twin-turbo inline-six (M3/M4 Competition).

    84 x 90 mm, firing 1-5-3-6-2-4, ~9.3:1 CR, ~7200 rpm, ~510 hp.  Modern
    fast-spooling twin-turbo, ~1.2 bar.
    """
    offsets = _even_offsets(6, firing_order=[1, 5, 3, 6, 2, 4])
    cylinders = [
        Cylinder(bore=mm(84), stroke=mm(90), rod_length=mm(145),
                 compression_ratio=9.3, cycle_offset_deg=offsets[i])
        for i in range(6)
    ]
    return Engine(
        name="BMW S58 3.0 twin-turbo I6 (M3/M4)",
        cylinders=cylinders,
        flywheel_inertia=0.24, redline_rpm=7200, idle_rpm=750,
        heat_release_k=5.2, ve_width_frac=0.75, closed_map_fraction=0.16,
        exhaust_tone=92.0,
        exhaust_primary_m=0.48, exhaust_total_m=1.9, exhaust_radius_m=0.025,
        exhaust_channels=1, exhaust_openness=0.7, muffler_volume_m3=0.0028,
        induction="turbo", boost_bar=1.2, turbo_lag=0.32, anti_lag=False,
        has_gpf=True,                            # modern EU emissions
        gear_ratios=[4.11, 2.32, 1.54, 1.18, 0.94, 0.76, 0.63, 0.51],
        final_drive=3.15, vehicle_mass=1780.0, wheel_radius=0.33,
        clutch_capacity=700.0,
    )


def mazda_rx7_rotary() -> Engine:
    """Mazda RX-7 FD — 13B-REW 1.3 L twin-turbo TWO-ROTOR Wankel.

    A 2-rotor fires twice per eccentric-shaft revolution (like a 4-cylinder's
    firing rate), but brap's far brighter/raspier — no valvetrain, peripheral
    port overlap.  Modelled as a 4-pulse even-fire with is_rotary brightness.
    ~280 hp, revs to ~8000.
    """
    offsets = _even_offsets(4)
    cylinders = [
        Cylinder(bore=mm(70), stroke=mm(80), rod_length=mm(120),
                 compression_ratio=9.0, cycle_offset_deg=offsets[i])
        for i in range(4)
    ]
    return Engine(
        name="Mazda RX-7 FD 13B rotary (twin-turbo)",
        cylinders=cylinders,
        flywheel_inertia=0.13, redline_rpm=8000, idle_rpm=850,
        heat_release_k=4.6, ve_peak_frac=0.72, ve_width_frac=0.7,
        closed_map_fraction=0.16,
        exhaust_tone=120.0,
        exhaust_primary_m=0.45, exhaust_total_m=1.7, exhaust_radius_m=0.024,
        exhaust_channels=1, exhaust_openness=0.9, muffler_volume_m3=0.002,
        is_rotary=True,
        induction="turbo", boost_bar=0.8, turbo_lag=0.45,
        gear_ratios=[3.48, 2.02, 1.39, 1.00, 0.72], final_drive=4.10,
        vehicle_mass=1310.0, wheel_radius=0.31, clutch_capacity=420.0,
    )


def subaru_22b() -> Engine:
    """Subaru Impreza 22B STi — 2.2 L turbo flat-four (EJ22).

    The boxer's UNEQUAL-LENGTH headers delay one bank's pulses, so even firing
    arrives unevenly = the iconic Subaru rumble/burble.  86.9.... firing 1-3-2-4,
    ~8:1 CR, ~7000 rpm, ~280 hp, ~0.9 bar.
    """
    offsets = _even_offsets(4, firing_order=[1, 3, 2, 4])
    cylinders = []
    for i in range(4):
        bank = -90.0 if i < 2 else 90.0          # horizontally opposed
        cylinders.append(
            Cylinder(bore=mm(96.9), stroke=mm(75.0), rod_length=mm(131),
                     compression_ratio=8.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Subaru 22B STi 2.2 turbo flat-4",
        cylinders=cylinders,
        flywheel_inertia=0.20, redline_rpm=7000, idle_rpm=820,
        heat_release_k=4.4, ve_width_frac=0.72, closed_map_fraction=0.17,
        exhaust_tone=74.0,
        exhaust_primary_m=0.5, exhaust_total_m=1.9, exhaust_radius_m=0.025,
        exhaust_channels=2, exhaust_openness=0.6, muffler_volume_m3=0.003,
        header_unequal_deg=28.0,                 # the boxer-rumble delay
        induction="turbo", boost_bar=0.9, turbo_lag=0.5,
        gear_ratios=[3.45, 2.06, 1.45, 1.09, 0.82], final_drive=4.44,
        vehicle_mass=1270.0, wheel_radius=0.31, clutch_capacity=480.0,
    )


# ----------------------------------------------------------------- registry
# Ordered (key, label, factory).  Add a line here and the engine appears in the
# selector and on its number key — nothing else to wire up.
PRESETS = [
    ("1", "911",    porsche_911_h6),
    ("2", "EA888",  vw_ea888_i4),
    ("3", "Coyote", ford_coyote_v8),
    ("4", "458",    ferrari_458),
    ("5", "LFA",    lexus_lfa),
    ("6", "V12",    lamborghini_murcielago),
    ("7", "F2004",  ferrari_f2004_v10),
    ("8", "Hellcat", dodge_hellcat_v8),
    ("9", "2JZ",    toyota_2jz_supra),
    ("0", "S58",    bmw_s58),
    ("rx7", "RX-7 rotary", mazda_rx7_rotary),
    ("22b", "Subaru 22B", subaru_22b),
]

ALL = {key: factory for key, _label, factory in PRESETS}
LABELS = {key: label for key, label, _factory in PRESETS}


def _rebuild_maps():
    global ALL, LABELS
    ALL = {key: factory for key, _label, factory in PRESETS}
    LABELS = {key: label for key, label, _factory in PRESETS}


def register_user_engines():
    """Load every engine .json in configs/engines/ as an extra selectable
    preset (keys continue after the built-ins).  Safe to call repeatedly."""
    from . import config
    known = {label for _k, label, _f in PRESETS}
    for label, path in config.list_engine_configs():
        if label in known:
            continue
        key = str(len(PRESETS) + 1)
        PRESETS.append((key, label[:11], (lambda p=path: config.load_engine(p))))
        known.add(label)
    _rebuild_maps()


def default() -> Engine:
    return ford_coyote_v8()
