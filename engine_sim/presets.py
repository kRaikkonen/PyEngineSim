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
        name="Porsche 911 Carrera (993) M64 3.8 flat-6",
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
        vehicle_mass=1370.0, wheel_radius=0.32, clutch_capacity=470.0,        gearbox_type="manual",
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
        name="Volkswagen Golf R EA888 2.0 I4",
        cylinders=cylinders,
        flywheel_inertia=0.24,
        redline_rpm=6800,
        idle_rpm=850,
        heat_release_k=1.66,             # full-boost power = nameplate (~310 hp)
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
        name="Ford Mustang GT Coyote 5.0 V8",
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
        vehicle_mass=1750.0, wheel_radius=0.34, clutch_capacity=640.0,        gearbox_type="manual",
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
        bank = -45.0 if i < 4 else 45.0          # 90-deg flat-plane V8
        cylinders.append(
            Cylinder(bore=mm(94), stroke=mm(81), rod_length=mm(149),
                     compression_ratio=12.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Ferrari 458 Italia F136 4.5 V8",
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
        wall_material="titanium", cat_cells_cpsi=200,
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
    offsets = _even_offsets(10, firing_order=[1, 6, 5, 10, 2, 7, 3, 8, 4, 9])                      # even 72-deg firing
    cylinders = []
    for i in range(10):
        bank = -36.0 if i < 5 else 36.0              # 72-deg V10
        cylinders.append(
            Cylinder(bore=mm(88), stroke=mm(79), rod_length=mm(130),
                     compression_ratio=12.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Lexus LFA 1LR-GUE 4.8 V10",
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
        exhaust_primary_m=0.60, exhaust_total_m=2.1, exhaust_radius_m=0.019,
        exhaust_channels=1, exhaust_openness=0.95, muffler_volume_m3=0.0015,
        wall_material="titanium", cat_cells_cpsi=200,   # thin titanium = soprano scream
        gear_ratios=[3.231, 2.188, 1.609, 1.233, 0.970, 0.795],
        final_drive=3.417,
        vehicle_mass=1480.0, wheel_radius=0.340, clutch_capacity=560.0,        gearbox_type="single",
    )


def lamborghini_murcielago() -> Engine:
    """Lamborghini Murcielago LP670-4 SuperVeloce — 6.5 L 60-deg V12.

    Real specs: 6496 cc, bore 88 x stroke 89 mm, 11.0:1 CR, 670 PS @ 8000 /
    660 Nm @ 6500, redline ~8000 rpm, ~100 kg lighter than the LP640.  Famous
    e-gear (single-clutch automated manual) and a far less restrictive, louder,
    rawer exhaust -> a harder, more open, more aggressive V12 howl.
    """
    offsets = _even_offsets(12, firing_order=[1, 7, 4, 10, 2, 8, 6, 12, 3, 9, 5, 11])                      # even 60-deg firing
    cylinders = []
    for i in range(12):
        bank = -30.0 if i < 6 else 30.0              # banks 1-6 / 7-12
        cylinders.append(
            Cylinder(bore=mm(88), stroke=mm(89), rod_length=mm(140),
                     compression_ratio=11.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Lamborghini Murcielago LP670-4 SV 6.5 V12",
        cylinders=cylinders,
        flywheel_inertia=0.30,           # lighter rotating mass (SV)
        redline_rpm=8100,
        idle_rpm=900,
        heat_release_k=3.5,              # 670 PS
        closed_map_fraction=0.09,
        ve_peak_frac=0.74,               # peak torque ~6500 rpm
        ve_width_frac=0.6,
        friction_static=10.0,
        starter_torque=200.0,
        exhaust_tone=0.0,                # derive pitch from physics (deep V12)
        exhaust_primary_m=0.68, exhaust_total_m=2.2, exhaust_radius_m=0.027,
        exhaust_channels=2, exhaust_openness=0.88, muffler_volume_m3=0.0026,
        gear_ratios=[2.94, 2.06, 1.52, 1.18, 0.94, 0.76],
        final_drive=3.45,
        vehicle_mass=1565.0, wheel_radius=0.345, clutch_capacity=720.0, gearbox_type="single",
    )


def lamborghini_diablo() -> Engine:
    """Lamborghini Diablo 6.0 VT — 6.0 L 60-deg V12 (the pre-Murcielago raw one).

    87.5 x 84 mm, ~10.7:1 CR, ~7100 rpm, ~550 hp.  Same V12 family as the
    Murcielago but a harder, rawer, brighter exhaust voice (less muffled, shorter
    primaries) — the point being that the SAME engine sounds different car-to-car
    because the exhaust system differs.  5-speed manual.
    """
    offsets = _even_offsets(12, firing_order=[1, 7, 4, 10, 2, 8, 6, 12, 3, 9, 5, 11])
    cylinders = []
    for i in range(12):
        bank = -30.0 if i < 6 else 30.0
        cylinders.append(
            Cylinder(bore=mm(87.5), stroke=mm(84), rod_length=mm(154),
                     compression_ratio=10.7, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Lamborghini Diablo VT 6.0 V12",
        cylinders=cylinders,
        flywheel_inertia=0.30, redline_rpm=7100, idle_rpm=900,
        heat_release_k=3.8, ve_peak_frac=0.7, ve_width_frac=0.6,
        closed_map_fraction=0.10, friction_static=10.0, starter_torque=200.0,
        # rawer / harder than the Murcielago via the EXHAUST SYSTEM (physics):
        # shorter, more open primaries + far less muffler.  Pitch auto-derives.
        exhaust_tone=56.0,               # rawer, deeper, analog '90s V12 growl
        exhaust_primary_m=0.58, exhaust_total_m=2.1, exhaust_radius_m=0.029,
        exhaust_channels=2, exhaust_openness=0.83, muffler_volume_m3=0.0024,
        gear_ratios=[2.31, 1.58, 1.24, 0.94, 0.76], final_drive=4.09,
        vehicle_mass=1625.0, wheel_radius=0.34, clutch_capacity=720.0,
        gearbox_type="manual",
    )


def ferrari_f2004_v10() -> Engine:
    """Ferrari F2004 — Tipo 053 3.0 L 90-deg V10 Formula 1 engine.

    The screaming 2004 F1 V10: ~3.0 L, ~96 x 41.4 mm (wildly oversquare to rev),
    ~13:1 CR, ~900 PS at ~18000 rpm, redline ~18500.  Almost no flywheel, so it
    spins up instantly.  Firing every 72 deg -> a 1500 Hz wail at full song.
    """
    offsets = _even_offsets(10, firing_order=[1, 6, 5, 10, 2, 7, 3, 8, 4, 9])                      # even 72-deg firing
    cylinders = []
    for i in range(10):
        bank = -45.0 if i < 5 else 45.0              # 90-deg V
        cylinders.append(
            Cylinder(bore=mm(96), stroke=mm(41.4), rod_length=mm(95),
                     compression_ratio=13.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank)
        )
    return Engine(
        name="Ferrari F2004 Tipo053 3.0 V10 F1",
        cylinders=cylinders,
        straight_cut=True,               # F1: sequential dog box
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
        wall_material="titanium",
        megaphone=0.7,                           # open upswept race exit -> mid bark
        has_cat=False,                           # open race exhaust, no cat/GPF
        # real F1 close-ratio 7-speed: 1st redlines ~140 km/h, 7th ~350 km/h
        gear_ratios=[3.04, 2.57, 2.20, 1.89, 1.64, 1.42, 1.24], final_drive=5.35,
        vehicle_mass=650.0, wheel_radius=0.33, clutch_capacity=400.0,        gearbox_type="single",
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
        name="Dodge Charger SRT Hellcat 6.2 SC V8",
        cylinders=cylinders,
        flywheel_inertia=0.45, redline_rpm=6200, idle_rpm=720,
        heat_release_k=1.73, ve_width_frac=0.75, closed_map_fraction=0.20,
        friction_static=7.0, starter_torque=180.0,
        exhaust_tone=52.0,
        exhaust_primary_m=0.75, exhaust_total_m=2.2, exhaust_radius_m=0.029,
        exhaust_channels=2, exhaust_openness=0.6, muffler_volume_m3=0.0035,
        induction="roots", boost_bar=0.8, blower_ratio=9.0,
        valvetrain="ohv", valves_per_cyl=2,      # pushrod 2-valve HEMI
        gear_ratios=[2.97, 2.07, 1.43, 1.00, 0.84, 0.57], final_drive=2.62,
        vehicle_mass=2020.0, wheel_radius=0.34, clutch_capacity=900.0,        gearbox_type="at",
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
        name="Toyota Supra 2JZ-GTE 3.0 I6",
        cylinders=cylinders,
        flywheel_inertia=0.26, redline_rpm=7000, idle_rpm=800,
        heat_release_k=1.7, ve_width_frac=0.75, closed_map_fraction=0.22,
        ve_floor=0.72,
        exhaust_tone=88.0,
        exhaust_primary_m=0.5, exhaust_total_m=2.0, exhaust_radius_m=0.026,
        exhaust_channels=1, exhaust_openness=0.62, muffler_volume_m3=0.003,
        induction="turbo", boost_bar=1.0, turbo_lag=0.55,
        gear_ratios=[3.83, 2.36, 1.69, 1.31, 1.00, 0.79], final_drive=3.13,
        vehicle_mass=1560.0, wheel_radius=0.32, clutch_capacity=520.0,        gearbox_type="manual",
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
        name="BMW M3 S58 3.0TT I6",
        cylinders=cylinders,
        flywheel_inertia=0.24, redline_rpm=7200, idle_rpm=750,
        heat_release_k=1.52, ve_width_frac=0.75, closed_map_fraction=0.16,
        exhaust_tone=92.0,
        exhaust_primary_m=0.48, exhaust_total_m=1.9, exhaust_radius_m=0.025,
        exhaust_channels=1, exhaust_openness=0.7, muffler_volume_m3=0.0028,
        induction="turbo", boost_bar=1.2, turbo_lag=0.32, anti_lag=False,
        has_gpf=True,                            # modern EU emissions
        gear_ratios=[4.11, 2.32, 1.54, 1.18, 0.94, 0.76, 0.63, 0.51],
        final_drive=3.15, vehicle_mass=1780.0, wheel_radius=0.33,
        clutch_capacity=700.0,        gearbox_type="at",
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
        name="Mazda RX-7 FD3S 13B-REW rotary",
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
        vehicle_mass=1310.0, wheel_radius=0.31, clutch_capacity=420.0,        gearbox_type="manual",
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
        name="Subaru Impreza 22B STi EJ22 flat-4",
        cylinders=cylinders,
        flywheel_inertia=0.20, redline_rpm=7000, idle_rpm=820,
        heat_release_k=4.4, ve_width_frac=0.72, closed_map_fraction=0.17,
        exhaust_tone=74.0,
        exhaust_primary_m=0.5, exhaust_total_m=1.9, exhaust_radius_m=0.025,
        exhaust_channels=2, exhaust_openness=0.6, muffler_volume_m3=0.003,
        header_unequal_deg=28.0,                 # the boxer-rumble delay
        induction="turbo", boost_bar=0.9, turbo_lag=0.5,
        gear_ratios=[3.45, 2.06, 1.45, 1.09, 0.82], final_drive=4.44,
        vehicle_mass=1270.0, wheel_radius=0.31, clutch_capacity=480.0,        gearbox_type="manual",
    )


def lamborghini_huracan_v10() -> Engine:
    """Lamborghini Huracan — 5.2 L naturally-aspirated 90-deg V10 (Audi/Lambo).

    84.5 x 92.8 mm, ~12.7:1 CR, ~8500 rpm, ~610 hp.  High-revving NA screamer
    with the raspy V10 voice.  DOHC 4-valve.
    """
    offsets = _even_offsets(10, firing_order=[1, 6, 5, 10, 2, 7, 3, 8, 4, 9])
    cylinders = []
    for i in range(10):
        bank = -45.0 if i < 5 else 45.0          # 90-deg V
        cylinders.append(
            Cylinder(bore=mm(84.5), stroke=mm(92.8), rod_length=mm(150),
                     compression_ratio=12.7, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Lamborghini Huracan LP610-4 5.2 V10",
        cylinders=cylinders,
        flywheel_inertia=0.21, redline_rpm=8500, idle_rpm=950,
        closed_map_fraction=0.15,
        heat_release_k=3.7, ve_peak_frac=0.7, ve_width_frac=0.62,
        friction_static=8.0, friction_quad=6.0e-5, starter_torque=180.0,
        exhaust_tone=112.0,
        exhaust_primary_m=0.6, exhaust_total_m=2.0, exhaust_radius_m=0.025,
        exhaust_channels=1, exhaust_openness=0.9, muffler_volume_m3=0.0018,
        gear_ratios=[3.13, 2.41, 1.81, 1.46, 1.19, 0.97, 0.84], final_drive=4.77,
        vehicle_mass=1422.0, wheel_radius=0.34, clutch_capacity=620.0,
        gearbox_type="dct",      # 7-speed dual-clutch — seamless
    )


def lamborghini_aventador_v12() -> Engine:
    """Lamborghini Aventador — 6.5 L naturally-aspirated 60-deg V12 (L539).

    95 x 76.4 mm, ~11.8:1 CR, ~8500 rpm, ~700 hp.  The big NA V12 howl — fuller
    and smoother than the V10, twelve even 60-deg pulses.  DOHC 4-valve.
    """
    # Official L539 firing order (cast on the engine plate): banks 1-6 / 7-12.
    offsets = _even_offsets(12, firing_order=[1, 12, 4, 9, 2, 11, 6, 7, 3, 10, 5, 8])
    cylinders = []
    for i in range(12):
        bank = -30.0 if i < 6 else 30.0          # 60-deg V
        cylinders.append(
            Cylinder(bore=mm(95), stroke=mm(76.4), rod_length=mm(148),
                     compression_ratio=11.8, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Lamborghini Aventador LP700-4 L539 6.5 V12",
        cylinders=cylinders,
        flywheel_inertia=0.30, redline_rpm=8500, idle_rpm=900,
        heat_release_k=3.75, ve_peak_frac=0.68, ve_width_frac=0.62,
        friction_static=10.0, starter_torque=200.0,
        # Hard, bright, raspy top-end — the Aventador's signature metallic howl.
        # Short primaries + open, near-unmuffled pipe set it apart from the
        # deeper Murcielago/Zonda and the smoother LaFerrari.
        exhaust_tone=86.0,               # raw, raspy 'chainsaw' L539 snarl (stock,
                                         #   NOT an open Gintani straight-pipe)
        exhaust_primary_m=0.50, exhaust_total_m=1.95, exhaust_radius_m=0.023,
        exhaust_channels=2, exhaust_openness=0.84, muffler_volume_m3=0.0016,
        wall_material="titanium", cat_cells_cpsi=350,
        header_unequal_deg=9.0, backpressure_coupling=0.8,   # buzzy 60-deg rasp
        gear_grain=0.3,                  # a touch of fine gear-driven whir
        gear_ratios=[2.93, 2.15, 1.66, 1.32, 1.06, 0.86, 0.72], final_drive=3.91,
        vehicle_mass=1575.0, wheel_radius=0.35, clutch_capacity=720.0,
        gearbox_type="single",   # ISR single-clutch — the brutal Aventador kick
    )


def ferrari_laferrari_v12() -> Engine:
    """Ferrari LaFerrari — 6.3 L naturally-aspirated 65-deg V12 (F140 FE).

    94 x 75.2 mm, ~13.5:1 CR, ~9000 rpm, ~800 hp (ICE only).  The ultimate NA
    Ferrari V12 scream — even higher-revving than the Lambos.  DOHC 4-valve.
    """
    offsets = _even_offsets(12, firing_order=[1, 12, 5, 8, 3, 10, 6, 7, 2, 11, 4, 9])
    cylinders = []
    for i in range(12):
        bank = -32.5 if i < 6 else 32.5          # 65-deg V
        cylinders.append(
            Cylinder(bore=mm(94), stroke=mm(75.2), rod_length=mm(147),
                     compression_ratio=13.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Ferrari LaFerrari F140FE 6.3 V12",
        cylinders=cylinders,
        flywheel_inertia=0.26, redline_rpm=9000, idle_rpm=950,
        closed_map_fraction=0.14,
        heat_release_k=3.7, ve_peak_frac=0.74, ve_width_frac=0.6,
        friction_static=10.0, starter_torque=200.0,
        exhaust_tone=70.0,
        exhaust_primary_m=0.62, exhaust_total_m=2.1, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.92, muffler_volume_m3=0.0021,
        wall_material="titanium",        # smooth, rising, metallic 'waaang' wail
        backpressure_coupling=0.7, gear_grain=0.5,   # smooth wail + fine gear whir
        gear_ratios=[3.08, 2.19, 1.63, 1.29, 1.03, 0.84, 0.69], final_drive=3.71,
        vehicle_mass=1585.0, wheel_radius=0.34, clutch_capacity=750.0,
        gearbox_type="dct",      # 7-speed dual-clutch — seamless
    )


def ferrari_f40_v8() -> Engine:
    """Ferrari F40 — 2.9 L twin-turbo 90-deg V8 (F120A), huge turbo lag.

    82 x 69.5 mm, low 7.8:1 CR for boost, ~7750 rpm, ~478 hp.  Twin IHI turbos
    that stay asleep below ~3500 rpm then slam in — the classic 80s lag.  We
    recreate that with a *late* spool threshold + a long lag time-constant.
    Flat-plane crank.  DOHC 4-valve.
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 3, 7, 4, 8, 2, 6])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0          # 90-deg V8, flat-plane
        cylinders.append(
            Cylinder(bore=mm(82), stroke=mm(69.5), rod_length=mm(124),
                     compression_ratio=7.8, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Ferrari F40 F120A 2.9TT V8",
        cylinders=cylinders,
        bov_flutter=True,                # 80s twin-turbo, no dump valve -> 'stututu'
        flywheel_inertia=0.16, redline_rpm=7750, idle_rpm=950,
        heat_release_k=2.1, ve_peak_frac=0.62, ve_width_frac=0.6,
        closed_map_fraction=0.17, idle_air_base=0.22,
        friction_static=7.0, starter_torque=160.0,
        exhaust_tone=98.0,
        exhaust_primary_m=0.55, exhaust_total_m=1.9, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.85, muffler_volume_m3=0.0026,
        induction="turbo", boost_bar=1.1,
        turbo_lag=1.3,            # long spool — the famous lag
        turbo_spool_frac=0.42,    # ~3250 rpm before any boost
        turbo_spool_width=0.32,   # then a sharp rush to full
        gear_ratios=[2.92, 2.10, 1.57, 1.25, 1.04], final_drive=3.42,
        vehicle_mass=1100.0, wheel_radius=0.33, clutch_capacity=520.0,
        gearbox_type="manual",   # gated 5-speed manual — jerky in auto mode
    )


def pagani_zonda_v12() -> Engine:
    """Pagani Zonda — 7.3 L naturally-aspirated 60-deg V12 (Mercedes-AMG M297).

    91.5 x 92.4 mm, ~10.5:1 CR, ~6700 rpm, ~650 hp / huge torque.  The big-bore
    AMG V12: lower-revving and torquier than the Ferrari/Lambo screamers — a
    deep, brutal voice.  SOHC 4-valve.
    """
    offsets = _even_offsets(12, firing_order=[1, 12, 5, 8, 3, 10, 6, 7, 2, 11, 4, 9])
    cylinders = []
    for i in range(12):
        bank = -30.0 if i < 6 else 30.0          # 60-deg V
        cylinders.append(
            Cylinder(bore=mm(91.5), stroke=mm(92.4), rod_length=mm(152),
                     compression_ratio=10.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Pagani Zonda Cinque M297 7.3 V12",
        cylinders=cylinders,
        flywheel_inertia=0.34, redline_rpm=6700, idle_rpm=720,
        idle_air_base=0.23,
        heat_release_k=4.1, ve_peak_frac=0.6, ve_width_frac=0.58,
        friction_static=11.0, starter_torque=220.0,
        valvetrain="sohc",
        exhaust_tone=50.0,
        exhaust_primary_m=0.80, exhaust_total_m=2.6, exhaust_radius_m=0.031,
        exhaust_channels=2, exhaust_openness=0.78, muffler_volume_m3=0.0048,
        wall_material="titanium", cat_cells_cpsi=200,
        gear_ratios=[3.00, 2.04, 1.52, 1.18, 0.95, 0.79], final_drive=3.36,
        vehicle_mass=1250.0, wheel_radius=0.34, clutch_capacity=820.0,
        gearbox_type="manual",   # Cima 6-speed manual — jerky in auto mode
    )


def toyota_ae86_4age() -> Engine:
    """Toyota AE86 — 4A-GE 1.6 L naturally-aspirated DOHC 16v inline-four.

    81 x 77 mm, ~10.3:1 CR, ~7600 rpm, ~128 hp.  Light, revvy, free-breathing
    little twin-cam — firing 1-3-4-2.  DOHC 4-valve, 5-speed manual.
    """
    offsets = _even_offsets(4, firing_order=[1, 3, 4, 2])
    cylinders = [
        Cylinder(bore=mm(81), stroke=mm(77), rod_length=mm(122),
                 compression_ratio=10.3, cycle_offset_deg=offsets[i])
        for i in range(4)
    ]
    return Engine(
        name="Toyota AE86 4A-GE 1.6 I4",
        cylinders=cylinders,
        flywheel_inertia=0.12, redline_rpm=7600, idle_rpm=850,
        heat_release_k=4.5, ve_peak_frac=0.66, closed_map_fraction=0.16,
        friction_static=4.0, starter_torque=110.0,
        exhaust_tone=100.0,              # thin, tinny little 1.6
        exhaust_primary_m=0.5, exhaust_total_m=1.7, exhaust_radius_m=0.018,
        exhaust_channels=1, exhaust_openness=0.66, muffler_volume_m3=0.0022,
        wall_material="aluminium",       # high, tinny sheet-metal ring (铁皮)
        intake_runner_m=0.42, backpressure_coupling=0.9,   # lumpy 'bubbling' texture
        gear_ratios=[3.59, 2.25, 1.49, 1.00, 0.85], final_drive=4.30,
        vehicle_mass=970.0, wheel_radius=0.30, clutch_capacity=300.0,
        gearbox_type="manual",
    )


def nissan_r34_rb26() -> Engine:
    """Nissan Skyline GT-R R34 — RB26DETT 2.6 L twin-turbo inline-six.

    86 x 73.7 mm, ~8.5:1 CR, ~8000 rpm, ~330 hp.  Firing 1-5-3-6-2-4, twin
    ceramic turbos (~1.0 bar) with a big blow-off whoosh.  Getrag 6-speed.
    """
    offsets = _even_offsets(6, firing_order=[1, 5, 3, 6, 2, 4])
    cylinders = [
        Cylinder(bore=mm(86), stroke=mm(73.7), rod_length=mm(139),
                 compression_ratio=8.5, cycle_offset_deg=offsets[i])
        for i in range(6)
    ]
    return Engine(
        name="Nissan Skyline GT-R R34 RB26DETT 2.6 I6",
        cylinders=cylinders,
        flywheel_inertia=0.24, redline_rpm=8000, idle_rpm=850,
        heat_release_k=1.42, ve_width_frac=0.74, closed_map_fraction=0.16,
        exhaust_tone=90.0,
        exhaust_primary_m=0.5, exhaust_total_m=2.0, exhaust_radius_m=0.026,
        exhaust_channels=1, exhaust_openness=0.66, muffler_volume_m3=0.0028,
        induction="turbo", boost_bar=0.95, turbo_lag=0.5,
        gear_ratios=[3.83, 2.36, 1.69, 1.31, 1.00, 0.79], final_drive=3.55,
        vehicle_mass=1560.0, wheel_radius=0.32, clutch_capacity=550.0,
        gearbox_type="manual",
    )


def nissan_r35_vr38() -> Engine:
    """Nissan GT-R R35 — VR38DETT 3.8 L twin-turbo 60-deg V6.

    95.5 x 88.4 mm, ~9.0:1 CR, ~7000 rpm, ~565 hp.  Twin turbos (~1.0 bar) and
    the GR6 6-speed dual-clutch — fast, brutal, seamless.
    """
    offsets = _even_offsets(6, firing_order=[1, 4, 2, 5, 3, 6])
    cylinders = []
    for i in range(6):
        bank = -30.0 if i < 3 else 30.0          # 60-deg V6
        cylinders.append(
            Cylinder(bore=mm(95.5), stroke=mm(88.4), rod_length=mm(151),
                     compression_ratio=9.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Nissan GT-R R35 VR38DETT 3.8 V6",
        cylinders=cylinders,
        flywheel_inertia=0.28, redline_rpm=7000, idle_rpm=800,
        heat_release_k=1.66, ve_width_frac=0.74, closed_map_fraction=0.16,
        exhaust_tone=80.0,
        exhaust_primary_m=0.55, exhaust_total_m=2.0, exhaust_radius_m=0.027,
        exhaust_channels=2, exhaust_openness=0.62, muffler_volume_m3=0.0032,
        induction="turbo", boost_bar=1.0, turbo_lag=0.4,
        gear_ratios=[4.06, 2.30, 1.60, 1.25, 1.00, 0.80], final_drive=3.70,
        vehicle_mass=1740.0, wheel_radius=0.34, clutch_capacity=720.0,
        gearbox_type="dct",
    )


def ferrari_f355_v8() -> Engine:
    """Ferrari F355 — F129 3.5 L flat-plane V8, FIVE valves per cylinder.

    85 x 77 mm, ~11.1:1 CR, ~8500 rpm, ~375 hp.  Firing 1-5-3-7-4-8-2-6, the
    famous 5-valve top-end howl.  F1 single-clutch automated manual.
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 3, 7, 4, 8, 2, 6])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0          # 90-deg flat-plane V8
        cylinders.append(
            Cylinder(bore=mm(85), stroke=mm(77), rod_length=mm(124),
                     compression_ratio=11.1, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Ferrari F355 Berlinetta F129 3.5 V8",
        cylinders=cylinders,
        flywheel_inertia=0.20, redline_rpm=8500, idle_rpm=950,
        heat_release_k=4.2, ve_peak_frac=0.72, closed_map_fraction=0.13,
        friction_static=7.0, starter_torque=160.0,
        valves_per_cyl=5,                        # 5-valve heads
        exhaust_tone=100.0,
        exhaust_primary_m=0.55, exhaust_total_m=1.95, exhaust_radius_m=0.025,
        exhaust_channels=1, exhaust_openness=0.9, muffler_volume_m3=0.0018,
        gear_ratios=[3.21, 2.10, 1.52, 1.16, 0.92, 0.77], final_drive=4.19,
        vehicle_mass=1350.0, wheel_radius=0.33, clutch_capacity=480.0,
        gearbox_type="single",                   # F1 single-clutch
    )


def mazda_787b_rotary() -> Engine:
    """Mazda 787B — R26B 2.6 L NA FOUR-rotor Wankel (Le Mans 1991).

    Four rotors fire four times per eccentric-shaft revolution (an 8-pulse
    four-stroke firing rate), with no valvetrain and peripheral ports — the
    screaming, metallic, ~9000 rpm Le Mans howl.  ~700 hp.  Modelled as an
    8-pulse even-fire with rotary brightness; sequential single-clutch.
    """
    offsets = _even_offsets(8)
    cylinders = [
        Cylinder(bore=mm(80), stroke=mm(80), rod_length=mm(120),
                 compression_ratio=10.0, cycle_offset_deg=offsets[i])
        for i in range(8)
    ]
    return Engine(
        name="Mazda 787B R26B 4-rotor",
        cylinders=cylinders,
        straight_cut=True, has_cat=False,   # Le Mans prototype: dog box, no cat
        flywheel_inertia=0.16, redline_rpm=9000, idle_rpm=1100,
        heat_release_k=6.9, ve_peak_frac=0.74, ve_width_frac=0.72,
        closed_map_fraction=0.14, friction_static=8.0, starter_torque=180.0,
        is_rotary=True,
        exhaust_tone=135.0,
        exhaust_primary_m=0.42, exhaust_total_m=1.6, exhaust_radius_m=0.023,
        exhaust_channels=1, exhaust_openness=0.95, muffler_volume_m3=0.0010,
        gear_ratios=[2.6, 1.9, 1.5, 1.2, 1.0], final_drive=4.10,
        vehicle_mass=830.0, wheel_radius=0.33, clutch_capacity=600.0,
        gearbox_type="single",
    )


def porsche_carrera_gt_v10() -> Engine:
    """Porsche Carrera GT — 5.7 L NA 68-deg V10 (race-derived).

    98 x 76 mm, ~12.0:1 CR, ~8400 rpm, ~612 hp.  A high-revving racing V10 in a
    road car — sharp, hard-edged wail.  6-speed manual.
    """
    offsets = _even_offsets(10, firing_order=[1, 6, 5, 10, 2, 7, 3, 8, 4, 9])
    cylinders = []
    for i in range(10):
        bank = -34.0 if i < 5 else 34.0          # 68-deg V10
        cylinders.append(
            Cylinder(bore=mm(98), stroke=mm(76), rod_length=mm(140),
                     compression_ratio=12.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Porsche Carrera GT 980/01 5.7 V10",
        cylinders=cylinders,
        flywheel_inertia=0.20, redline_rpm=8400, idle_rpm=950,
        heat_release_k=3.7, ve_peak_frac=0.72, closed_map_fraction=0.12,
        friction_static=9.0, starter_torque=190.0,
        exhaust_tone=104.0,
        exhaust_primary_m=0.55, exhaust_total_m=2.0, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.92, muffler_volume_m3=0.0018,
        wall_material="titanium", cat_cells_cpsi=200,
        gear_ratios=[3.15, 2.18, 1.61, 1.27, 1.03, 0.84], final_drive=3.55,
        vehicle_mass=1380.0, wheel_radius=0.34, clutch_capacity=600.0,
        gearbox_type="manual",
    )


def porsche_992_gt3() -> Engine:
    """Porsche 911 GT3 (992) — 4.0 L NA flat-six, ~9000 rpm.

    102 x 81.5 mm, ~13.3:1 CR, ~510 hp.  Firing 1-6-2-4-3-5.  The high-revving
    naturally-aspirated boxer howl — PDK dual-clutch.  DOHC 4-valve.
    """
    offsets = _even_offsets(6, firing_order=[1, 6, 2, 4, 3, 5])
    cylinders = []
    for i in range(6):
        bank = -90.0 if i < 3 else 90.0          # horizontally opposed
        cylinders.append(
            Cylinder(bore=mm(102), stroke=mm(81.5), rod_length=mm(128),
                     compression_ratio=13.3, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Porsche 911 GT3 (992) 9A1 4.0 flat-6",
        cylinders=cylinders,
        flywheel_inertia=0.17, redline_rpm=9000, idle_rpm=900,
        heat_release_k=4.2, ve_peak_frac=0.72, closed_map_fraction=0.12,
        friction_static=7.0, starter_torque=170.0,
        exhaust_tone=80.0,               # refined modern GT3 flat-six (vs DLS wail)
        exhaust_primary_m=0.5, exhaust_total_m=1.9, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.83, muffler_volume_m3=0.0020,
        wall_material="titanium", cat_cells_cpsi=200,
        header_unequal_deg=13.0, backpressure_coupling=0.85,  # flat-six 'boil' burble
        gear_ratios=[3.91, 2.29, 1.65, 1.30, 1.08, 0.88, 0.62], final_drive=3.97,
        vehicle_mass=1435.0, wheel_radius=0.34, clutch_capacity=560.0,
        gearbox_type="dct",                      # PDK
    )


def honda_nsx_na1() -> Engine:
    """Honda NSX (NA1) — C30A 3.0 L NA 90-deg V6, VTEC.

    90 x 78 mm, ~10.2:1 CR, ~8000 rpm, ~270 hp.  Smooth, high-revving VTEC V6 —
    firing 1-4-2-5-3-6.  5-speed manual.
    """
    offsets = _even_offsets(6, firing_order=[1, 4, 2, 5, 3, 6])
    cylinders = []
    for i in range(6):
        bank = -45.0 if i < 3 else 45.0          # 90-deg V6
        cylinders.append(
            Cylinder(bore=mm(90), stroke=mm(78), rod_length=mm(141),
                     compression_ratio=10.2, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Honda NSX NA1 C30A 3.0 V6",
        cylinders=cylinders,
        flywheel_inertia=0.18, redline_rpm=8000, idle_rpm=800,
        heat_release_k=4.1, ve_peak_frac=0.7, closed_map_fraction=0.14,
        friction_static=6.0, starter_torque=150.0,
        exhaust_tone=92.0,
        exhaust_primary_m=0.52, exhaust_total_m=1.95, exhaust_radius_m=0.025,
        exhaust_channels=2, exhaust_openness=0.82, muffler_volume_m3=0.0022,
        gear_ratios=[3.07, 1.96, 1.39, 1.03, 0.79], final_drive=4.06,
        vehicle_mass=1370.0, wheel_radius=0.33, clutch_capacity=420.0,
        gearbox_type="manual",
    )


def mitsubishi_evo7_4g63() -> Engine:
    """Mitsubishi Lancer Evo VII — 4G63T 2.0 L turbo inline-four.

    85 x 88 mm, ~8.8:1 CR, ~7000 rpm, ~280 hp.  Firing 1-3-4-2, single turbo
    (~1.0 bar) with a strong blow-off whoosh.  5-speed manual.
    """
    offsets = _even_offsets(4, firing_order=[1, 3, 4, 2])
    cylinders = [
        Cylinder(bore=mm(85), stroke=mm(88), rod_length=mm(150),
                 compression_ratio=8.8, cycle_offset_deg=offsets[i])
        for i in range(4)
    ]
    return Engine(
        name="Mitsubishi Lancer Evolution VI GSR 4G63T 2.0 I4",
        cylinders=cylinders,
        flywheel_inertia=0.18, redline_rpm=7000, idle_rpm=820,
        heat_release_k=2.6, ve_width_frac=0.72, closed_map_fraction=0.22,
        ve_floor=0.75,
        exhaust_tone=86.0,
        exhaust_primary_m=0.5, exhaust_total_m=1.85, exhaust_radius_m=0.025,
        exhaust_channels=1, exhaust_openness=0.62, muffler_volume_m3=0.003,
        induction="turbo", boost_bar=1.0, turbo_lag=0.55,
        gear_ratios=[2.92, 1.95, 1.41, 1.03, 0.76], final_drive=4.53,
        vehicle_mass=1400.0, wheel_radius=0.31, clutch_capacity=480.0,
        gearbox_type="manual",
    )


def chevrolet_c7_lt1() -> Engine:
    """Chevrolet Corvette C7 Stingray — LT1 6.2 L NA pushrod V8.

    103.25 x 92 mm, ~11.5:1 CR, ~6600 rpm, ~460 hp.  Cross-plane OHV 2-valve
    small-block — deep American V8 rumble.  Firing 1-8-7-2-6-5-4-3.  7-speed
    manual.
    """
    offsets = _even_offsets(8, firing_order=[1, 8, 7, 2, 6, 5, 4, 3])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0
        cylinders.append(
            Cylinder(bore=mm(103.25), stroke=mm(92), rod_length=mm(154),
                     compression_ratio=11.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Chevrolet Corvette C7 LT1 6.2 V8",
        cylinders=cylinders,
        flywheel_inertia=0.40, redline_rpm=6600, idle_rpm=750,
        heat_release_k=3.6, ve_peak_frac=0.58, closed_map_fraction=0.20,
        friction_static=6.0, starter_torque=180.0,
        valvetrain="ohv", valves_per_cyl=2,      # pushrod small-block
        exhaust_tone=52.0,
        exhaust_primary_m=0.74, exhaust_total_m=2.15, exhaust_radius_m=0.029,
        exhaust_channels=2, exhaust_openness=0.66, muffler_volume_m3=0.0034,
        gear_ratios=[2.97, 2.07, 1.43, 1.00, 0.71, 0.57, 0.48], final_drive=2.41,
        vehicle_mass=1500.0, wheel_radius=0.34, clutch_capacity=700.0,
        gearbox_type="manual",
    )


def chevrolet_camaro_z28_302() -> Engine:
    """1968 Chevrolet Camaro Z/28 — DZ 302 (4.9 L) NA small-block V8.

    101.6 x 76.2 mm (4.00 x 3.00 in), ~11.0:1 CR, high-revving ~7000 rpm.
    Carbureted, solid-lifter OHV 2-valve — lopey idle and a raw classic muscle
    bark.  Firing 1-8-4-3-6-5-7-2.  Muncie 4-speed manual.
    """
    offsets = _even_offsets(8, firing_order=[1, 8, 4, 3, 6, 5, 7, 2])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0
        cylinders.append(
            Cylinder(bore=mm(101.6), stroke=mm(76.2), rod_length=mm(145),
                     compression_ratio=11.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Chevrolet Camaro Z/28 302 V8 (1968)",
        cylinders=cylinders,
        flywheel_inertia=0.38, redline_rpm=7000, idle_rpm=800,
        heat_release_k=3.2, ve_peak_frac=0.62, closed_map_fraction=0.24,
        friction_static=6.0, starter_torque=170.0,
        valvetrain="ohv", valves_per_cyl=2, has_cat=False,
        exhaust_tone=58.0,
        exhaust_primary_m=0.7, exhaust_total_m=2.0, exhaust_radius_m=0.030,
        exhaust_channels=2, exhaust_openness=0.78, muffler_volume_m3=0.0026,
        gear_ratios=[2.52, 1.88, 1.46, 1.00], final_drive=3.73,
        vehicle_mass=1500.0, wheel_radius=0.33, clutch_capacity=620.0,
        gearbox_type="manual",
    )


def audi_sport_quattro_s1() -> Engine:
    """Audi Sport Quattro S1 — 2.1 L turbo inline-FIVE (Group B rally).

    79.5 x 86.4 mm, ~7.0:1 CR, ~8000 rpm, ~500 hp at huge boost (~1.6 bar).
    Firing 1-2-4-5-3 — the unmistakable five-cylinder warble.  Anti-lag bangs
    and crackles on the overrun (the flame-spitting rally car).  5-speed manual.
    """
    offsets = _even_offsets(5, firing_order=[1, 2, 4, 5, 3])
    cylinders = [
        Cylinder(bore=mm(79.5), stroke=mm(86.4), rod_length=mm(144),
                 compression_ratio=7.0, cycle_offset_deg=offsets[i])
        for i in range(5)
    ]
    return Engine(
        name="Audi Sport Quattro S1 EA827 2.1 I5",
        cylinders=cylinders,
        straight_cut=True, has_cat=False,   # Group B rally: dog box, no cat
        bov_flutter=True,                   # no dump valve -> 'stututu' surge
        flywheel_inertia=0.18, redline_rpm=8000, idle_rpm=950,
        heat_release_k=2.2, ve_width_frac=0.72, closed_map_fraction=0.24,
        ve_floor=0.72,
        friction_static=6.0, starter_torque=150.0,
        exhaust_tone=88.0,
        exhaust_primary_m=0.5, exhaust_total_m=1.9, exhaust_radius_m=0.026,
        exhaust_channels=1, exhaust_openness=0.7, muffler_volume_m3=0.0022,
        induction="turbo", boost_bar=1.6, turbo_lag=0.7, anti_lag=True,
        turbo_spool_frac=0.18, turbo_spool_width=0.4,
        gear_ratios=[3.60, 2.12, 1.46, 1.09, 0.86], final_drive=4.10,
        vehicle_mass=1200.0, wheel_radius=0.32, clutch_capacity=520.0,
        gearbox_type="manual",
    )


def audi_rs3_2024() -> Engine:
    """Audi RS3 (8Y, 2024) — EA855 2.5 L turbo inline-FIVE.

    82.5 x 92.8 mm, ~10.0:1 CR, ~7000 rpm, ~400 hp.  Firing 1-2-4-5-3, the
    modern five-cylinder warble, ~1.3 bar single turbo.  7-speed dual-clutch.
    """
    offsets = _even_offsets(5, firing_order=[1, 2, 4, 5, 3])
    cylinders = [
        Cylinder(bore=mm(82.5), stroke=mm(92.8), rod_length=mm(151),
                 compression_ratio=10.0, cycle_offset_deg=offsets[i])
        for i in range(5)
    ]
    return Engine(
        name="Audi RS3 EA855 2.5 I5",
        cylinders=cylinders,
        flywheel_inertia=0.20, redline_rpm=7000, idle_rpm=820,
        heat_release_k=1.7, ve_width_frac=0.74, closed_map_fraction=0.22,
        ve_floor=0.72,
        exhaust_tone=84.0,
        exhaust_primary_m=0.5, exhaust_total_m=1.95, exhaust_radius_m=0.026,
        exhaust_channels=1, exhaust_openness=0.66, muffler_volume_m3=0.0028,
        induction="turbo", boost_bar=1.3, turbo_lag=0.4,
        has_gpf=True,
        gear_ratios=[3.56, 2.53, 1.68, 1.22, 0.96, 0.79, 0.64], final_drive=4.27,
        vehicle_mass=1570.0, wheel_radius=0.33, clutch_capacity=600.0,
        gearbox_type="dct",
    )


def audi_rs5_ea839() -> Engine:
    """Audi RS5 — EA839 2.9 L twin-turbo 90-deg V6 (hot-V).

    84.5 x 86 mm, ~10.5:1 CR, ~7000 rpm, ~450 hp.  Turbos nestled in the vee,
    fast spool (~1.2 bar).  8-speed... modelled as quick dual-clutch.
    """
    offsets = _even_offsets(6, firing_order=[1, 4, 2, 5, 3, 6])
    cylinders = []
    for i in range(6):
        bank = -45.0 if i < 3 else 45.0          # 90-deg V6
        cylinders.append(
            Cylinder(bore=mm(84.5), stroke=mm(86), rod_length=mm(150),
                     compression_ratio=10.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Audi RS5 EA839 2.9TT V6",
        cylinders=cylinders,
        flywheel_inertia=0.26, redline_rpm=7000, idle_rpm=780,
        heat_release_k=1.34, ve_width_frac=0.74, closed_map_fraction=0.16,
        exhaust_tone=82.0,
        exhaust_primary_m=0.52, exhaust_total_m=2.0, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.6, muffler_volume_m3=0.0032,
        induction="turbo", boost_bar=1.2, turbo_lag=0.35,
        has_gpf=True,
        gear_ratios=[4.71, 3.14, 2.11, 1.67, 1.29, 1.00, 0.84, 0.67],
        final_drive=3.20, vehicle_mass=1730.0, wheel_radius=0.33,
        clutch_capacity=640.0, gearbox_type="at",
    )


def mercedes_sl65_m275() -> Engine:
    """Mercedes-Benz SL65 AMG — M275 6.0 L twin-turbo V12.

    82.6 x 93 mm, ~9.0:1 CR, ~6000 rpm, ~612 hp / huge torque (~1.0 bar).
    Effortless, deep, muffled twin-turbo V12 with a torque-converter automatic —
    soft, slushy shifts.
    """
    offsets = _even_offsets(12, firing_order=[1, 12, 5, 8, 3, 10, 6, 7, 2, 11, 4, 9])
    cylinders = []
    for i in range(12):
        bank = -30.0 if i < 6 else 30.0          # 60-deg V12
        cylinders.append(
            Cylinder(bore=mm(82.6), stroke=mm(93), rod_length=mm(150),
                     compression_ratio=9.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Mercedes-Benz SL65 AMG M275 6.0 V12",
        cylinders=cylinders,
        flywheel_inertia=0.40, redline_rpm=6000, idle_rpm=600,
        heat_release_k=1.31, ve_width_frac=0.78, closed_map_fraction=0.18,
        friction_static=11.0, starter_torque=220.0,
        exhaust_tone=48.0,
        exhaust_primary_m=0.8, exhaust_total_m=2.6, exhaust_radius_m=0.030,
        exhaust_channels=2, exhaust_openness=0.5, muffler_volume_m3=0.005,
        induction="turbo", boost_bar=1.0, turbo_lag=0.5,
        gear_ratios=[3.59, 2.19, 1.41, 1.00, 0.83], final_drive=2.65,
        vehicle_mass=2030.0, wheel_radius=0.34, clutch_capacity=950.0,
        gearbox_type="at",                       # torque-converter auto — slushy
    )


def mercedes_amg_gt_m178() -> Engine:
    """Mercedes-AMG GT — M178 4.0 L twin-turbo 'hot-V' V8.

    83 x 92 mm, ~10.5:1 CR, ~7000 rpm, ~523 hp.  Turbos inside the 90-deg vee,
    flat-plane crank — a hard, bark-y twin-turbo V8.  7-speed dual-clutch.
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 3, 7, 4, 8, 2, 6])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0          # 90-deg flat-plane (hot-V)
        cylinders.append(
            Cylinder(bore=mm(83), stroke=mm(92), rod_length=mm(150),
                     compression_ratio=10.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Mercedes-AMG GT M178 4.0TT V8",
        cylinders=cylinders,
        flywheel_inertia=0.28, redline_rpm=7000, idle_rpm=720,
        heat_release_k=1.23, ve_width_frac=0.74, closed_map_fraction=0.16,
        exhaust_tone=72.0,
        exhaust_primary_m=0.6, exhaust_total_m=2.05, exhaust_radius_m=0.027,
        exhaust_channels=2, exhaust_openness=0.68, muffler_volume_m3=0.003,
        induction="turbo", boost_bar=1.1, turbo_lag=0.4,
        gear_ratios=[3.40, 2.19, 1.63, 1.29, 1.03, 0.84, 0.69], final_drive=3.67,
        vehicle_mass=1630.0, wheel_radius=0.34, clutch_capacity=680.0,
        gearbox_type="dct",
    )


def singer_dls_williams_flat6() -> Engine:
    """Singer DLS — 4.0 L NA air-cooled flat-six (Williams-developed).

    ~107 x 74 mm, high CR, screams to ~9000 rpm, ~500 hp.  A reimagined 911
    engine with race-bred breathing — a hard, crystalline air-cooled howl.
    Firing 1-6-2-4-3-5.  6-speed manual.
    """
    offsets = _even_offsets(6, firing_order=[1, 6, 2, 4, 3, 5])
    cylinders = []
    for i in range(6):
        bank = -90.0 if i < 3 else 90.0          # horizontally opposed
        cylinders.append(
            Cylinder(bore=mm(107), stroke=mm(74), rod_length=mm(127),
                     compression_ratio=12.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Singer DLS Williams 4.0 flat-6",
        cylinders=cylinders,
        flywheel_inertia=0.16, redline_rpm=9000, idle_rpm=900,
        heat_release_k=4.3, ve_peak_frac=0.72, closed_map_fraction=0.12,
        friction_static=7.0, starter_torque=170.0,
        exhaust_tone=104.0,              # the screaming Williams-built 4.0 wail
        exhaust_primary_m=0.46, exhaust_total_m=1.8, exhaust_radius_m=0.0195,
        exhaust_channels=2, exhaust_openness=0.97, muffler_volume_m3=0.0012,
        wall_material="titanium", cat_cells_cpsi=200,
        gear_ratios=[3.50, 2.12, 1.58, 1.24, 1.00, 0.82], final_drive=3.44,
        vehicle_mass=1170.0, wheel_radius=0.33, clutch_capacity=520.0,
        gearbox_type="manual",
    )


def bmw_e92_m3_s65() -> Engine:
    """BMW E92 M3 — S65 4.0 L naturally-aspirated 90-deg V8.

    92 x 75.2 mm, ~12.0:1 CR, ~8300 rpm, ~414 hp.  Firing 1-5-4-8-6-3-7-2,
    flat-plane-ish high-revving V8 — a smooth, hard-edged top-end scream.  M-DCT
    7-speed.
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 4, 8, 6, 3, 7, 2])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0          # 90-deg V8
        cylinders.append(
            Cylinder(bore=mm(92), stroke=mm(75.2), rod_length=mm(141),
                     compression_ratio=12.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="BMW M3 E92 S65 4.0 V8",
        cylinders=cylinders,
        flywheel_inertia=0.24, redline_rpm=8300, idle_rpm=850,
        heat_release_k=4.0, ve_peak_frac=0.66, closed_map_fraction=0.14,
        friction_static=7.0, starter_torque=170.0,
        exhaust_tone=76.0,
        exhaust_primary_m=0.58, exhaust_total_m=2.0, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.8, muffler_volume_m3=0.0024,
        gear_ratios=[4.06, 2.40, 1.58, 1.19, 1.00, 0.87, 0.74], final_drive=3.85,
        vehicle_mass=1655.0, wheel_radius=0.33, clutch_capacity=600.0,
        gearbox_type="dct",
    )


def porsche_918_v8_hybrid() -> Engine:
    """Porsche 918 Spyder — 4.6 L NA flat-plane V8 + electric motors (hybrid).

    95 x 81 mm, ~11.0:1 CR, screams to ~9000 rpm, ~608 hp from the race-derived
    V8, plus ~210 kW of electric motors (front axle + crank) for ~890 hp combined
    and brutal instant low-end torque.  Firing 1-5-3-7-4-8-2-6, 7-speed PDK.
    Toggle the electric assist with the Hybrid button.
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 3, 7, 4, 8, 2, 6])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0          # 90-deg flat-plane V8
        cylinders.append(
            Cylinder(bore=mm(95), stroke=mm(81), rod_length=mm(140),
                     compression_ratio=11.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Porsche 918 Spyder 9A1 4.6 V8 hybrid",
        cylinders=cylinders,
        flywheel_inertia=0.22, redline_rpm=9000, idle_rpm=950,
        heat_release_k=4.8, ve_peak_frac=0.7, closed_map_fraction=0.13,
        friction_static=8.0, starter_torque=190.0,
        exhaust_tone=92.0,                       # top-exit race-car wail
        exhaust_primary_m=0.5, exhaust_total_m=1.9, exhaust_radius_m=0.025,
        exhaust_channels=2, exhaust_openness=0.92, muffler_volume_m3=0.0014,
        hybrid_kw=210.0, hybrid_base_rpm=2500.0,  # ~890 hp combined, instant low end
        gear_ratios=[3.91, 2.29, 1.58, 1.19, 0.97, 0.83, 0.70], final_drive=3.60,
        vehicle_mass=1675.0, wheel_radius=0.34, clutch_capacity=1500.0,  # hypercar PDK
        gearbox_type="dct",                      # PDK
    )


def ford_gt_2017_v6() -> Engine:
    """Ford GT (2017) — 3.5 L EcoBoost twin-turbo 60-deg V6.

    92.5 x 86.7 mm, ~9.5:1 CR, ~7000 rpm, ~647 hp.  Firing 1-4-2-5-3-6, twin
    turbos.  7-speed dual-clutch.
    """
    offsets = _even_offsets(6, firing_order=[1, 4, 2, 5, 3, 6])
    cylinders = []
    for i in range(6):
        bank = -30.0 if i < 3 else 30.0
        cylinders.append(
            Cylinder(bore=mm(92.5), stroke=mm(86.7), rod_length=mm(152),
                     compression_ratio=9.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Ford GT EcoBoost 3.5TT V6",
        cylinders=cylinders,
        flywheel_inertia=0.24, redline_rpm=7000, idle_rpm=820,
        heat_release_k=1.62, ve_width_frac=0.74, closed_map_fraction=0.18,
        ve_floor=0.7,
        exhaust_tone=84.0,
        exhaust_primary_m=0.52, exhaust_total_m=2.0, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.66, muffler_volume_m3=0.0028,
        induction="turbo", boost_bar=1.2, turbo_lag=0.4,
        gear_ratios=[3.66, 2.43, 1.69, 1.32, 1.00, 0.79, 0.64], final_drive=3.61,
        vehicle_mass=1565.0, wheel_radius=0.33, clutch_capacity=680.0,
        gearbox_type="dct",
    )


def bugatti_veyron_w16() -> Engine:
    """Bugatti Veyron — 8.0 L quad-turbo W16.

    86 x 86 mm x 16, ~9.0:1 CR, ~6500 rpm, ~1001 hp.  Two narrow-vee banks in a
    W; four turbos.  Modelled as a 16-pulse even-fire.  7-speed dual-clutch.
    """
    offsets = _even_offsets(16, firing_order=[1, 9, 5, 13, 3, 11, 7, 15, 2, 10, 6, 14, 4, 12, 8, 16])
    cylinders = []
    for i in range(16):
        # W16 = two VR8 units 90 deg apart (groups centred at +/-45 deg from
        # vertical); each VR splits into two cylinder columns 15 deg apart, so
        # the four banks sit at -52.5, -37.5, +37.5, +52.5 deg (3+ ... actually 4
        # cols of 4).  Columns alternate within each VR group.
        center = -45.0 if i < 8 else 45.0
        bank = center + (7.5 if (i % 2) else -7.5)
        cylinders.append(
            Cylinder(bore=mm(86), stroke=mm(86), rod_length=mm(140),
                     compression_ratio=9.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Bugatti Veyron Super Sport 8.0 W16",
        cylinders=cylinders,
        flywheel_inertia=0.55, redline_rpm=6500, idle_rpm=800,
        heat_release_k=1.47, ve_width_frac=0.78, closed_map_fraction=0.18,
        friction_static=14.0, starter_torque=240.0,
        exhaust_tone=64.0,
        exhaust_primary_m=0.6, exhaust_total_m=2.3, exhaust_radius_m=0.030,
        exhaust_channels=2, exhaust_openness=0.6, muffler_volume_m3=0.004,
        induction="turbo", boost_bar=1.0, turbo_lag=0.45,
        gear_ratios=[2.99, 2.05, 1.52, 1.18, 0.94, 0.76, 0.62], final_drive=2.81,
        vehicle_mass=1888.0, wheel_radius=0.34, clutch_capacity=1300.0,
        gearbox_type="dct", is_w=True,
    )


def bentley_continental_w12() -> Engine:
    """Bentley Continental GT — 6.0 L twin-turbo W12.

    84 x 90.2 mm x 12, ~10.5:1 CR, ~6000 rpm, ~626 hp / vast torque.  A smooth,
    deep, effortless twin-turbo W12.  8-speed dual-clutch.
    """
    offsets = _even_offsets(12, firing_order=[1, 12, 5, 8, 3, 10, 6, 7, 2, 11, 4, 9])
    cylinders = []
    for i in range(12):
        # W12 = two VR6 units 90 deg apart (groups centred at +/-45 deg); each VR
        # splits into two columns 15 deg apart -> four banks of 3 (3+3+3+3).
        center = -45.0 if i < 6 else 45.0
        bank = center + (7.5 if (i % 2) else -7.5)
        cylinders.append(
            Cylinder(bore=mm(84), stroke=mm(90.2), rod_length=mm(152),
                     compression_ratio=10.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Bentley Continental GT 6.0TT W12",
        cylinders=cylinders,
        flywheel_inertia=0.45, redline_rpm=6000, idle_rpm=620,
        heat_release_k=1.77, ve_width_frac=0.78, closed_map_fraction=0.18,
        friction_static=12.0, starter_torque=220.0,
        exhaust_tone=54.0,
        exhaust_primary_m=0.72, exhaust_total_m=2.4, exhaust_radius_m=0.029,
        exhaust_channels=2, exhaust_openness=0.55, muffler_volume_m3=0.0045,
        induction="turbo", boost_bar=0.7, turbo_lag=0.45,
        gear_ratios=[4.71, 3.14, 2.11, 1.67, 1.29, 1.00, 0.84, 0.67],
        final_drive=2.92, vehicle_mass=2244.0, wheel_radius=0.34,
        clutch_capacity=1100.0, gearbox_type="dct", is_w=True,
    )


def bmw_44_v8() -> Engine:
    """BMW 4.4 V8 (S63) — twin-turbo 90-deg 'hot-V' V8.

    89 x 88.3 mm, ~10.0:1 CR, ~7000 rpm, ~600 hp.  Cross-plane, firing
    1-5-4-8-6-3-7-2, turbos in the vee.  8-speed automatic (M Steptronic).
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 4, 8, 6, 3, 7, 2])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0
        cylinders.append(
            Cylinder(bore=mm(89), stroke=mm(88.3), rod_length=mm(150),
                     compression_ratio=10.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="BMW M5 S63 4.4TT V8",
        cylinders=cylinders,
        flywheel_inertia=0.30, redline_rpm=7000, idle_rpm=650,
        heat_release_k=1.35, ve_width_frac=0.74, closed_map_fraction=0.17,
        exhaust_tone=58.0,
        exhaust_primary_m=0.66, exhaust_total_m=2.1, exhaust_radius_m=0.028,
        exhaust_channels=2, exhaust_openness=0.62, muffler_volume_m3=0.0034,
        induction="turbo", boost_bar=1.1, turbo_lag=0.4,
        gear_ratios=[4.71, 3.14, 2.11, 1.67, 1.29, 1.00, 0.84, 0.67],
        final_drive=3.15, vehicle_mass=1930.0, wheel_radius=0.33,
        clutch_capacity=820.0, gearbox_type="at",
    )


def audi_42_v8() -> Engine:
    """Audi 4.2 FSI V8 — naturally-aspirated 90-deg V8 (RS4 / R8).

    84.5 x 92.8 mm, ~12.5:1 CR, ~8250 rpm, ~430 hp.  High-revving NA V8, firing
    1-5-4-8-6-3-7-2.  Gated 6-speed manual (the R8).
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 4, 8, 6, 3, 7, 2])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0
        cylinders.append(
            Cylinder(bore=mm(84.5), stroke=mm(92.8), rod_length=mm(154),
                     compression_ratio=12.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Audi R8 4.2 FSI V8",
        cylinders=cylinders,
        flywheel_inertia=0.26, redline_rpm=8250, idle_rpm=720,
        heat_release_k=3.82, ve_peak_frac=0.66, closed_map_fraction=0.15,
        friction_static=8.0, starter_torque=180.0,
        exhaust_tone=70.0,
        exhaust_primary_m=0.6, exhaust_total_m=2.05, exhaust_radius_m=0.027,
        exhaust_channels=2, exhaust_openness=0.84, muffler_volume_m3=0.0024,
        gear_ratios=[3.13, 2.05, 1.46, 1.09, 0.85, 0.68], final_drive=4.06,
        vehicle_mass=1560.0, wheel_radius=0.34, clutch_capacity=560.0,
        gearbox_type="manual",
    )


def mclaren_p1_v8_hybrid() -> Engine:
    """McLaren P1 — M838TQ 3.8 L twin-turbo V8 + electric (hybrid).

    93 x 69.9 mm, ~9.5:1 CR, ~8500 rpm, ~727 hp from the flat-plane V8 plus
    ~133 kW electric for ~903 hp combined.  Firing 1-5-3-7-4-8-2-6.  7-speed
    dual-clutch.  Toggle the e-motor with the Hybrid button.
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 3, 7, 4, 8, 2, 6])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0
        cylinders.append(
            Cylinder(bore=mm(93), stroke=mm(69.9), rod_length=mm(127),
                     compression_ratio=9.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="McLaren P1 M838TQ 3.8TT V8 hybrid",
        cylinders=cylinders,
        flywheel_inertia=0.22, redline_rpm=8500, idle_rpm=900,
        heat_release_k=1.4, ve_width_frac=0.72, closed_map_fraction=0.15,
        ve_floor=0.7,
        exhaust_tone=88.0,
        exhaust_primary_m=0.5, exhaust_total_m=1.9, exhaust_radius_m=0.025,
        exhaust_channels=2, exhaust_openness=0.84, muffler_volume_m3=0.0018,
        induction="turbo", boost_bar=1.2, turbo_lag=0.35,
        hybrid_kw=133.0, hybrid_base_rpm=2500.0,   # ~903 hp combined
        gear_ratios=[3.61, 2.37, 1.70, 1.30, 1.03, 0.84, 0.69], final_drive=3.31,
        vehicle_mass=1490.0, wheel_radius=0.34, clutch_capacity=1200.0,
        gearbox_type="dct",
    )


def bmw_b48_i4() -> Engine:
    """BMW B48 — 2.0 L turbo inline-four.

    82 x 94.6 mm, ~11.0:1 CR, ~6500 rpm, ~255 hp.  Firing 1-3-4-2, single
    twin-scroll turbo.  8-speed automatic.
    """
    offsets = _even_offsets(4, firing_order=[1, 3, 4, 2])
    cylinders = [
        Cylinder(bore=mm(82), stroke=mm(94.6), rod_length=mm(150),
                 compression_ratio=11.0, cycle_offset_deg=offsets[i])
        for i in range(4)
    ]
    return Engine(
        name="BMW 230i B48 2.0 I4",
        cylinders=cylinders,
        flywheel_inertia=0.18, redline_rpm=6500, idle_rpm=750,
        heat_release_k=1.95, ve_width_frac=0.72, closed_map_fraction=0.22,
        ve_floor=0.74,
        exhaust_tone=82.0,
        exhaust_primary_m=0.5, exhaust_total_m=1.9, exhaust_radius_m=0.025,
        exhaust_channels=1, exhaust_openness=0.6, muffler_volume_m3=0.003,
        induction="turbo", boost_bar=0.9, turbo_lag=0.4, has_gpf=True,
        gear_ratios=[5.0, 3.2, 2.14, 1.72, 1.31, 1.0, 0.82, 0.64],
        final_drive=3.15, vehicle_mass=1545.0, wheel_radius=0.32,
        clutch_capacity=440.0, gearbox_type="at",
    )


def mercedes_a45_amg_i4() -> Engine:
    """Mercedes-AMG A45 (M139) — 2.0 L turbo inline-four.

    83 x 92 mm, ~9.0:1 CR, ~6750 rpm, ~416 hp — the most powerful production
    2.0 turbo.  Firing 1-3-4-2, big single turbo.  8-speed dual-clutch.
    """
    offsets = _even_offsets(4, firing_order=[1, 3, 4, 2])
    cylinders = [
        Cylinder(bore=mm(83), stroke=mm(92), rod_length=mm(149),
                 compression_ratio=9.0, cycle_offset_deg=offsets[i])
        for i in range(4)
    ]
    return Engine(
        name="Mercedes-AMG A45 S M139 2.0 I4",
        cylinders=cylinders,
        flywheel_inertia=0.18, redline_rpm=6750, idle_rpm=820,
        heat_release_k=1.6, ve_width_frac=0.72, closed_map_fraction=0.22,
        ve_floor=0.72,
        exhaust_tone=86.0,
        exhaust_primary_m=0.48, exhaust_total_m=1.85, exhaust_radius_m=0.025,
        exhaust_channels=1, exhaust_openness=0.66, muffler_volume_m3=0.0026,
        induction="turbo", boost_bar=1.5, turbo_lag=0.4, has_gpf=True,
        gear_ratios=[5.5, 3.36, 2.27, 1.72, 1.31, 1.0, 0.82, 0.65],
        final_drive=3.06, vehicle_mass=1555.0, wheel_radius=0.32,
        clutch_capacity=520.0, gearbox_type="dct",
    )


def bmw_m3_gtr_p60() -> Engine:
    """BMW M3 GTR (E46) — P60B40 4.0 L naturally-aspirated flat-plane V8.

    94 x 72 mm, ~11.5:1 CR, ~8000 rpm, ~444 hp.  The homologation race V8 (NFS
    fame), firing 1-5-3-7-4-8-2-6 — a hard, flat-plane howl.  Sequential race
    'box (kicks like a single-clutch).
    """
    offsets = _even_offsets(8, firing_order=[1, 5, 3, 7, 4, 8, 2, 6])
    cylinders = []
    for i in range(8):
        bank = -45.0 if i < 4 else 45.0
        cylinders.append(
            Cylinder(bore=mm(94), stroke=mm(72), rod_length=mm(139),
                     compression_ratio=11.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="BMW M3 GTR E46 P60B40 4.0 V8",
        cylinders=cylinders,
        straight_cut=True, has_cat=False,   # homologation race V8: dog box, no cat
        flywheel_inertia=0.20, redline_rpm=8000, idle_rpm=950,
        heat_release_k=4.4, ve_peak_frac=0.66, closed_map_fraction=0.14,
        friction_static=8.0, starter_torque=180.0,
        exhaust_tone=78.0,
        exhaust_primary_m=0.55, exhaust_total_m=1.95, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.9, muffler_volume_m3=0.0016,
        gear_ratios=[3.23, 2.19, 1.65, 1.30, 1.05, 0.85], final_drive=3.62,
        vehicle_mass=1350.0, wheel_radius=0.33, clutch_capacity=600.0,
        gearbox_type="single",
    )


def mclaren_f1_v12() -> Engine:
    """McLaren F1 (1992) — BMW S70/2 6.1 L naturally-aspirated 60-deg V12.

    86 x 87 mm, ~11.0:1 CR, ~7500 rpm, ~627 hp.  The original analogue hypercar:
    a high-revving NA V12 with a famously hard, metallic top end.  Firing every
    60 deg.  6-speed manual.
    """
    offsets = _even_offsets(12, firing_order=[1, 7, 5, 11, 3, 9, 6, 12, 2, 8, 4, 10])
    cylinders = []
    for i in range(12):
        bank = -30.0 if i < 6 else 30.0          # 60-deg V12
        cylinders.append(
            Cylinder(bore=mm(86), stroke=mm(87), rod_length=mm(145),
                     compression_ratio=11.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="McLaren F1 BMW S70/2 6.1 V12",
        cylinders=cylinders,
        flywheel_inertia=0.24, redline_rpm=7500, idle_rpm=850,
        heat_release_k=4.15, ve_peak_frac=0.7, closed_map_fraction=0.13,
        friction_static=10.0, starter_torque=200.0,
        exhaust_tone=72.0,
        exhaust_primary_m=0.58, exhaust_total_m=2.05, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.9, muffler_volume_m3=0.0018,
        wall_material="titanium", cat_cells_cpsi=200,
        gear_ratios=[2.83, 1.96, 1.52, 1.20, 0.96, 0.74], final_drive=2.37,
        vehicle_mass=1140.0, wheel_radius=0.33, clutch_capacity=620.0,
        gearbox_type="manual",
    )


def peterbilt_389_diesel() -> Engine:
    """Peterbilt 389 — Cummins X15 15 L turbo-diesel inline-six big rig.

    137 x 169 mm x 6, ~17:1 CR, ~565 hp @ 1800, ~1850 lb-ft @ 1200, redline
    ~2100, idle ~600.  A huge, slow, lumpy diesel rumble with a big turbo.
    """
    offsets = _even_offsets(6, firing_order=[1, 5, 3, 6, 2, 4])
    cylinders = [
        Cylinder(bore=mm(137), stroke=mm(169), rod_length=mm(262),
                 compression_ratio=17.0, cycle_offset_deg=offsets[i])
        for i in range(6)
    ]
    return Engine(
        name="Peterbilt 389 Cummins X15 15L diesel I6",
        cylinders=cylinders,
        flywheel_inertia=2.4, redline_rpm=2100, idle_rpm=600,
        heat_release_k=4.8,              # hard diesel knock
        ve_peak_frac=0.5, ve_width_frac=0.7, closed_map_fraction=0.20,
        friction_static=34.0, starter_torque=1100.0, starter_speed_rpm=180.0,
        exhaust_tone=44.0,               # very deep diesel
        exhaust_primary_m=0.9, exhaust_total_m=3.2, exhaust_radius_m=0.046,
        exhaust_channels=1, exhaust_openness=0.62, muffler_volume_m3=0.02,
        induction="turbo", boost_bar=1.7, turbo_lag=0.9, turbo_spool_frac=0.08,
        valvetrain="ohv", valves_per_cyl=4, has_cat=False,
        backpressure_coupling=0.7,       # lumpy diesel beat
        gear_ratios=[12.8, 9.0, 6.5, 4.7, 3.4, 2.5, 1.8, 1.35, 1.0, 0.74],
        final_drive=3.55, vehicle_mass=15000.0, wheel_radius=0.50,
        clutch_capacity=3200.0, gearbox_type="manual",
    )


def ford_focus_ecoboost_i3() -> Engine:
    """Ford Focus 1.0 EcoBoost — 999 cc turbo inline-THREE.

    71.9 x 82 mm x 3, ~10:1 CR, ~125 hp, redline ~6500.  Even 240-deg firing
    1-2-3 — the thrummy, off-beat, characterful little three.
    """
    offsets = _even_offsets(3, firing_order=[1, 2, 3])
    cylinders = [
        Cylinder(bore=mm(71.9), stroke=mm(82.0), rod_length=mm(135),
                 compression_ratio=10.0, cycle_offset_deg=offsets[i])
        for i in range(3)
    ]
    return Engine(
        name="Ford Focus 1.0 EcoBoost I3",
        cylinders=cylinders,
        flywheel_inertia=0.13, redline_rpm=6500, idle_rpm=800,
        heat_release_k=3.4, ve_peak_frac=0.42, ve_width_frac=0.66,
        friction_static=4.0, starter_torque=120.0,
        exhaust_tone=92.0,
        exhaust_primary_m=0.45, exhaust_total_m=1.8, exhaust_radius_m=0.024,
        exhaust_channels=1, exhaust_openness=0.6, muffler_volume_m3=0.003,
        induction="turbo", boost_bar=0.95, turbo_lag=0.35, turbo_spool_frac=0.09,
        backpressure_coupling=0.9,       # the three-cylinder thrum / off-beat
        gear_ratios=[3.58, 1.93, 1.28, 0.95, 0.76, 0.62], final_drive=4.06,
        vehicle_mass=1300.0, wheel_radius=0.31, clutch_capacity=300.0,
        gearbox_type="manual",
    )


def spitfire_merlin_v12() -> Engine:
    """Supermarine Spitfire — Rolls-Royce Merlin 27 L supercharged 60-deg V12.

    137 x 152 mm x 12, ~6:1 CR, ~1030-1600 hp, ~3000 rpm.  Open exhaust stubs,
    a single/two-stage supercharger — the deep, throaty WWII fighter snarl.
    """
    offsets = _even_offsets(12, firing_order=[1, 8, 5, 10, 3, 7, 6, 11, 2, 9, 4, 12])
    cylinders = [
        Cylinder(bore=mm(137), stroke=mm(152), rod_length=mm(280),
                 compression_ratio=6.0, cycle_offset_deg=offsets[i],
                 bank_angle_deg=(-30.0 if i < 6 else 30.0))
        for i in range(12)
    ]
    return Engine(
        name="Supermarine Spitfire RR Merlin 27L V12",
        cylinders=cylinders,
        flywheel_inertia=3.2, redline_rpm=3000, idle_rpm=550,
        heat_release_k=3.6, ve_peak_frac=0.7, ve_width_frac=0.72,
        friction_static=22.0, starter_torque=700.0, starter_speed_rpm=140.0,
        exhaust_tone=52.0,
        exhaust_primary_m=0.32, exhaust_total_m=0.55, exhaust_radius_m=0.040,
        exhaust_channels=2, exhaust_openness=0.97, muffler_volume_m3=0.0008,
        induction="centrifugal", boost_bar=0.6, blower_ratio=8.5,
        valvetrain="sohc", valves_per_cyl=4, has_cat=False,
        backpressure_coupling=0.6, gear_grain=0.35,
        gear_ratios=[1.0], final_drive=0.42,   # single fixed prop reduction
        vehicle_mass=3000.0, wheel_radius=0.55, clutch_capacity=2500.0,
        gearbox_type="aircraft",
    )


def f4f_wildcat_radial() -> Engine:
    """Grumman F4F Wildcat — Pratt & Whitney R-1830 Twin Wasp 30 L 14-cylinder
    twin-row radial.  140 x 140 mm x 14, ~6.7:1 CR, ~1200 hp, ~2700 rpm.  The
    classic deep, throbbing WWII carrier-fighter radial drone."""
    offsets = _even_offsets(14, firing_order=[1, 10, 5, 14, 9, 4, 13, 8, 3, 12, 7, 2, 11, 6])
    cylinders = [
        Cylinder(bore=mm(140), stroke=mm(140), rod_length=mm(310),
                 compression_ratio=6.7, cycle_offset_deg=offsets[i],
                 bank_angle_deg=(i * 360.0 / 14.0))   # radial position (star)
        for i in range(14)
    ]
    return Engine(
        name="Grumman F4F Wildcat P&W R-1830 14-cyl radial",
        cylinders=cylinders,
        flywheel_inertia=3.5, redline_rpm=2700, idle_rpm=500,
        heat_release_k=3.5, ve_peak_frac=0.65, ve_width_frac=0.72,
        friction_static=24.0, starter_torque=600.0, starter_speed_rpm=130.0,
        exhaust_tone=50.0,
        exhaust_primary_m=0.3, exhaust_total_m=0.5, exhaust_radius_m=0.038,
        exhaust_channels=1, exhaust_openness=0.95, muffler_volume_m3=0.0008,
        induction="centrifugal", boost_bar=0.45, blower_ratio=7.5,
        valvetrain="ohv", valves_per_cyl=2, has_cat=False, is_radial=True,
        backpressure_coupling=0.55, gear_grain=0.3,
        gear_ratios=[1.0], final_drive=0.40,
        vehicle_mass=3600.0, wheel_radius=0.55, clutch_capacity=2500.0,
        gearbox_type="aircraft",
    )


def ferrari_f2007_v8() -> Engine:
    """Ferrari F2007 — Ferrari 056 2.4 L NA 90-deg V8 F1 engine (2007).

    98 x 39.75 mm x 8, ~13:1, ~750 hp @ 19000, rev limit 19000 rpm.  The
    screaming late-2000s F1 V8 — flat-plane, titanium, wide open, no muffler.
    """
    offsets = _even_offsets(8, firing_order=[1, 8, 3, 6, 4, 5, 2, 7])
    cylinders = [
        Cylinder(bore=mm(98), stroke=mm(39.75), rod_length=mm(102),
                 compression_ratio=13.0, cycle_offset_deg=offsets[i],
                 bank_angle_deg=(-45.0 if i < 4 else 45.0))
        for i in range(8)
    ]
    return Engine(
        name="Ferrari F2007 Tipo056 2.4 V8 F1",
        cylinders=cylinders,
        flywheel_inertia=0.05, redline_rpm=19000, idle_rpm=4000,
        heat_release_k=3.6, ve_peak_frac=0.85, ve_width_frac=0.55,
        friction_static=6.0, friction_quad=4.0e-5,
        starter_torque=140.0, starter_speed_rpm=3800.0,
        exhaust_tone=152.0,
        exhaust_primary_m=0.42, exhaust_total_m=0.65, exhaust_radius_m=0.018,
        exhaust_channels=2, exhaust_openness=0.99, muffler_volume_m3=0.0006,
        valvetrain="dohc", valves_per_cyl=4, has_cat=False, straight_cut=True,
        megaphone=0.72,                          # open upswept race exit -> mid bark
        wall_material="titanium", gear_grain=0.38, upshift_rpm=18500.0,
        # real F1 close-ratio 7-speed: 1st redlines ~140 km/h, 7th ~350 km/h
        # (was too tall -> 1st hit 200 km/h without reaching the limiter).
        gear_ratios=[3.08, 2.60, 2.23, 1.92, 1.66, 1.44, 1.25], final_drive=5.4,
        vehicle_mass=605.0, wheel_radius=0.33, clutch_capacity=600.0,
        gearbox_type="dct",              # F1 seamless shift (no flare)
    )


def ferrari_sf25_v6_hybrid() -> Engine:
    """Ferrari SF-25 — 1.6 L turbo-hybrid 90-deg V6 F1 power unit (2025).

    80 x 53 mm x 6, ~15000 rpm, ~830 hp ICE + ~160 hp ERS.  Single turbo +
    MGU-H (near-instant spool) and MGU-K — the muffled, strangled turbo-era note.
    """
    offsets = _even_offsets(6, firing_order=[1, 6, 3, 4, 2, 5])
    cylinders = [
        Cylinder(bore=mm(80), stroke=mm(53), rod_length=mm(102),
                 compression_ratio=13.0, cycle_offset_deg=offsets[i],
                 bank_angle_deg=(-45.0 if i < 3 else 45.0))
        for i in range(6)
    ]
    return Engine(
        name="Ferrari SF-25 1.6 V6 F1 hybrid",
        cylinders=cylinders,
        flywheel_inertia=0.11, redline_rpm=15000, idle_rpm=4000,
        heat_release_k=2.6, ve_peak_frac=0.8, ve_width_frac=0.6,
        friction_static=6.0, starter_torque=140.0, starter_speed_rpm=3800.0,
        exhaust_tone=118.0,
        exhaust_primary_m=0.4, exhaust_total_m=0.85, exhaust_radius_m=0.027,
        exhaust_channels=1, exhaust_openness=0.6, muffler_volume_m3=0.0018,
        induction="turbo", boost_bar=1.6, turbo_lag=0.2, turbo_spool_frac=0.06,
        electric_turbo=True,             # MGU-H -> near-instant spool
        hybrid_kw=120.0, hybrid_base_rpm=3000.0,
        mgu_whine=1.0, upshift_rpm=12000.0,   # loud MGU-H/K whistle; short-shifts
        valvetrain="dohc", valves_per_cyl=4, has_cat=False, straight_cut=True,
        gear_grain=0.3,
        # real F1 close-ratio 8-speed: 1st redlines ~130 km/h, 8th ~350 km/h
        gear_ratios=[2.88, 2.49, 2.16, 1.87, 1.62, 1.41, 1.22, 1.07], final_drive=5.0,
        vehicle_mass=800.0, wheel_radius=0.33, clutch_capacity=1500.0,
        gearbox_type="dct",              # F1 seamless shift (no flare)
    )


def mclaren_mp44_honda_v6() -> Engine:
    """McLaren MP4/4 — Honda RA168E 1.5 L twin-turbo 80-deg V6 F1 (1988).

    79 x 50.8 mm x 6, ~12500 rpm, ~650 hp race / 1000+ in qualifying boost.  The
    Senna/Prost turbo-era screamer with a big turbo whistle.  Manual H-box.
    """
    offsets = _even_offsets(6, firing_order=[1, 6, 3, 4, 2, 5])
    cylinders = [
        Cylinder(bore=mm(79), stroke=mm(50.8), rod_length=mm(100),
                 compression_ratio=9.0, cycle_offset_deg=offsets[i],
                 bank_angle_deg=(-40.0 if i < 3 else 40.0))
        for i in range(6)
    ]
    return Engine(
        name="McLaren MP4/4 Honda RA168E 1.5 V6 turbo",
        cylinders=cylinders,
        flywheel_inertia=0.11, redline_rpm=12500, idle_rpm=3500,
        heat_release_k=3.5, ve_peak_frac=0.8, ve_width_frac=0.6,
        friction_static=6.0, starter_torque=140.0, starter_speed_rpm=3300.0,
        exhaust_tone=130.0,
        exhaust_primary_m=0.4, exhaust_total_m=0.7, exhaust_radius_m=0.022,
        exhaust_channels=2, exhaust_openness=0.9, muffler_volume_m3=0.001,
        induction="turbo", boost_bar=2.6, turbo_lag=0.4, turbo_spool_frac=0.16,
        valvetrain="dohc", valves_per_cyl=4, has_cat=False, straight_cut=True,
        wall_material="titanium", gear_grain=0.3, upshift_rpm=11800.0,
        gear_ratios=[2.9, 2.2, 1.8, 1.5, 1.3, 1.1], final_drive=4.0,
        vehicle_mass=540.0, wheel_radius=0.33, clutch_capacity=550.0,
        gearbox_type="manual",
    )


def _inline4(name, bore_mm, stroke_mm, rod_mm, cr, **kw):
    """Helper: a generic inline-four (firing 1-3-4-2) with the given Engine kwargs."""
    offsets = _even_offsets(4, firing_order=[1, 3, 4, 2])
    cyls = [Cylinder(bore=mm(bore_mm), stroke=mm(stroke_mm), rod_length=mm(rod_mm),
                     compression_ratio=cr, cycle_offset_deg=offsets[i])
            for i in range(4)]
    return Engine(name=name, cylinders=cyls, **kw)


def _flat4(name, bore_mm, stroke_mm, rod_mm, cr, **kw):
    """Helper: a boxer flat-four (firing 1-3-2-4, unequal headers = rumble)."""
    offsets = _even_offsets(4, firing_order=[1, 3, 2, 4])
    cyls = [Cylinder(bore=mm(bore_mm), stroke=mm(stroke_mm), rod_length=mm(rod_mm),
                     compression_ratio=cr, cycle_offset_deg=offsets[i],
                     bank_angle_deg=(-90.0 if i < 2 else 90.0))
            for i in range(4)]
    return Engine(name=name, cylinders=cyls, **kw)


def _inline(name, n, bore_mm, stroke_mm, rod_mm, cr, firing, **kw):
    """Helper: a generic inline-N engine with the given firing order + Engine kwargs."""
    offsets = _even_offsets(n, firing_order=firing)
    cyls = [Cylinder(bore=mm(bore_mm), stroke=mm(stroke_mm), rod_length=mm(rod_mm),
                     compression_ratio=cr, cycle_offset_deg=offsets[i]) for i in range(n)]
    return Engine(name=name, cylinders=cyls, **kw)


def _vee(name, n, bore_mm, stroke_mm, rod_mm, cr, bank, firing, **kw):
    """Helper: a generic V-N engine (banks 1..n/2 left at -bank, rest right)."""
    offsets = _even_offsets(n, firing_order=firing)
    cyls = [Cylinder(bore=mm(bore_mm), stroke=mm(stroke_mm), rod_length=mm(rod_mm),
                     compression_ratio=cr, cycle_offset_deg=offsets[i],
                     bank_angle_deg=(-bank if i < n // 2 else bank)) for i in range(n)]
    return Engine(name=name, cylinders=cyls, **kw)


# firing orders reused below
_FO_V8_FLAT = [1, 8, 3, 6, 4, 5, 2, 7]      # flat-plane (clean L-R alternation)
_FO_V8_X = [1, 5, 4, 8, 6, 3, 7, 2]         # cross-plane (the burble)
_FO_V6 = [1, 6, 3, 4, 2, 5]
_FO_V10 = [1, 6, 5, 10, 2, 7, 3, 8, 4, 9]
_FO_V12 = [1, 12, 5, 8, 3, 10, 6, 7, 2, 11, 4, 9]
_FO_I6 = [1, 5, 3, 6, 2, 4]
_FO_FLAT6 = [1, 6, 2, 4, 3, 5]               # Porsche flat-six (alternates banks)


def mazda_savanna_rx7_fc() -> Engine:
    """Mazda Savanna RX-7 FC3S — 13B-T single-turbo two-rotor Wankel."""
    offsets = _even_offsets(4)
    cyls = [Cylinder(bore=mm(70), stroke=mm(80), rod_length=mm(120),
                     compression_ratio=8.5, cycle_offset_deg=offsets[i]) for i in range(4)]
    return Engine(
        name="Mazda Savanna RX-7 FC3S 13B-T", cylinders=cyls,
        flywheel_inertia=0.14, redline_rpm=7000, idle_rpm=850,
        heat_release_k=4.4, ve_peak_frac=0.7, closed_map_fraction=0.17,
        exhaust_tone=112.0, exhaust_primary_m=0.45, exhaust_total_m=1.8,
        exhaust_radius_m=0.025, exhaust_channels=1, exhaust_openness=0.78,
        muffler_volume_m3=0.0028, is_rotary=True,
        induction="turbo", boost_bar=0.7, turbo_lag=0.5,
        gear_ratios=[3.48, 2.02, 1.39, 1.00, 0.76], final_drive=4.10,
        vehicle_mass=1260.0, wheel_radius=0.30, clutch_capacity=380.0,
        gearbox_type="manual")


def abarth_500_esseesse() -> Engine:
    return _inline4(
        "Abarth 500 esseesse 1.4 T-Jet I4", 72.0, 84.0, 132.0, 9.8,
        flywheel_inertia=0.12, redline_rpm=6500, idle_rpm=850,
        heat_release_k=3.3, ve_peak_frac=0.45, closed_map_fraction=0.17,
        exhaust_tone=96.0, exhaust_primary_m=0.45, exhaust_total_m=1.7,
        exhaust_radius_m=0.022, exhaust_channels=1, exhaust_openness=0.66,
        muffler_volume_m3=0.0025, induction="turbo", boost_bar=0.9,
        turbo_lag=0.4, bov_flutter=True, backpressure_coupling=0.8,
        gear_ratios=[3.91, 2.24, 1.52, 1.16, 0.87], final_drive=3.35,
        vehicle_mass=1035.0, wheel_radius=0.30, clutch_capacity=240.0,
        gearbox_type="manual")


def honda_civic_type_r_ek9() -> Engine:
    return _inline4(
        "Honda Civic Type-R EK9 B16B", 81.0, 77.4, 134.0, 10.8,
        flywheel_inertia=0.11, redline_rpm=8400, idle_rpm=850,
        heat_release_k=3.6, ve_peak_frac=0.78, ve_width_frac=0.58,
        closed_map_fraction=0.14, exhaust_tone=104.0, exhaust_primary_m=0.5,
        exhaust_total_m=1.8, exhaust_radius_m=0.022, exhaust_channels=1,
        exhaust_openness=0.82, muffler_volume_m3=0.0022,
        gear_ratios=[3.23, 2.11, 1.52, 1.15, 0.92], final_drive=4.40,
        vehicle_mass=1070.0, wheel_radius=0.30, clutch_capacity=260.0,
        gearbox_type="manual")


def honda_civic_type_r_ep3() -> Engine:
    return _inline4(
        "Honda Civic Type-R EP3 K20A", 86.0, 86.0, 139.0, 11.5,
        flywheel_inertia=0.12, redline_rpm=8000, idle_rpm=820,
        heat_release_k=3.6, ve_peak_frac=0.76, ve_width_frac=0.6,
        closed_map_fraction=0.14, exhaust_tone=100.0, exhaust_primary_m=0.5,
        exhaust_total_m=1.85, exhaust_radius_m=0.023, exhaust_channels=1,
        exhaust_openness=0.8, muffler_volume_m3=0.0024,
        gear_ratios=[3.27, 2.13, 1.52, 1.21, 0.97], final_drive=4.76,
        vehicle_mass=1200.0, wheel_radius=0.31, clutch_capacity=280.0,
        gearbox_type="manual")


def honda_civic_type_r_fk8() -> Engine:
    return _inline4(
        "Honda Civic Type-R FK8 K20C1", 86.0, 85.9, 139.0, 9.8,
        flywheel_inertia=0.13, redline_rpm=7000, idle_rpm=800,
        heat_release_k=3.4, ve_peak_frac=0.5, ve_width_frac=0.66,
        closed_map_fraction=0.16, exhaust_tone=92.0, exhaust_primary_m=0.48,
        exhaust_total_m=1.9, exhaust_radius_m=0.026, exhaust_channels=1,
        exhaust_openness=0.66, muffler_volume_m3=0.0026, induction="turbo",
        boost_bar=1.1, turbo_lag=0.35, gear_ratios=[3.63, 2.12, 1.53, 1.13, 0.92, 0.74],
        final_drive=4.11, vehicle_mass=1380.0, wheel_radius=0.32,
        clutch_capacity=400.0, gearbox_type="manual")


def peugeot_205_t16() -> Engine:
    return _inline4(
        "Peugeot 205 Turbo 16 XU8T", 83.0, 82.0, 138.0, 7.5,
        flywheel_inertia=0.12, redline_rpm=7800, idle_rpm=950,
        heat_release_k=3.6, ve_peak_frac=0.6, ve_width_frac=0.55,
        closed_map_fraction=0.18, exhaust_tone=108.0, exhaust_primary_m=0.45,
        exhaust_total_m=1.6, exhaust_radius_m=0.026, exhaust_channels=1,
        exhaust_openness=0.86, muffler_volume_m3=0.0012, induction="turbo",
        boost_bar=1.6, turbo_lag=0.55, turbo_spool_frac=0.2, anti_lag=True,
        bov_flutter=True, has_cat=False, straight_cut=True,
        gear_ratios=[2.92, 1.92, 1.40, 1.07, 0.85], final_drive=4.6,
        vehicle_mass=1145.0, wheel_radius=0.31, clutch_capacity=420.0,
        gearbox_type="manual")


def ford_rs200_evo() -> Engine:
    return _inline4(
        "Ford RS200 Evolution BDT-E", 90.0, 77.0, 137.0, 7.2,
        flywheel_inertia=0.12, redline_rpm=8000, idle_rpm=1000,
        heat_release_k=3.7, ve_peak_frac=0.62, ve_width_frac=0.55,
        closed_map_fraction=0.18, exhaust_tone=110.0, exhaust_primary_m=0.42,
        exhaust_total_m=1.5, exhaust_radius_m=0.027, exhaust_channels=1,
        exhaust_openness=0.9, muffler_volume_m3=0.0009, induction="turbo",
        boost_bar=2.0, turbo_lag=0.5, turbo_spool_frac=0.18, anti_lag=True,
        bov_flutter=True, has_cat=False, straight_cut=True,
        gear_ratios=[2.5, 1.8, 1.35, 1.05, 0.85], final_drive=4.2,
        vehicle_mass=1180.0, wheel_radius=0.31, clutch_capacity=520.0,
        gearbox_type="manual")


def hoonigan_rs200_evo() -> Engine:
    e = ford_rs200_evo()
    e.name = "Hoonigan Ford RS200 Evolution BDT-E (anti-lag)"
    e.boost_bar = 2.6
    e.redline_rpm = 8200
    return e


def lancia_delta_s4() -> Engine:
    """Lancia Delta S4 — Group B 1.8 I4, TWINCHARGED: a Roots 'Volumex'
    supercharger fills the bottom end (no lag) and a big KKK turbo takes over up
    top, so you hear the blower whine low crossfading into turbo whistle.  ~480 hp
    rally, ~8000 rpm, low CR for the boost."""
    return _inline4(
        "Lancia Delta S4 Abarth 233 ATR 1.8 I4", 88.5, 71.5, 130.0, 7.0,
        flywheel_inertia=0.12, redline_rpm=8000, idle_rpm=1100,
        heat_release_k=3.6, ve_peak_frac=0.5, ve_width_frac=0.6,
        closed_map_fraction=0.18, exhaust_tone=112.0, exhaust_primary_m=0.42,
        exhaust_total_m=1.5, exhaust_radius_m=0.027, exhaust_channels=1,
        exhaust_openness=0.88, muffler_volume_m3=0.001,
        induction="turbo", induction_subtype="twincharge",
        boost_bar=2.0, blower_ratio=8.5, turbo_lag=0.5, turbo_spool_frac=0.16,
        anti_lag=True, bov_flutter=True, has_cat=False, straight_cut=True,
        gear_ratios=[2.6, 1.85, 1.4, 1.1, 0.88], final_drive=4.3,
        vehicle_mass=1090.0, wheel_radius=0.31, clutch_capacity=540.0,
        gearbox_type="manual")


def ford_escort_cosworth() -> Engine:
    return _inline4(
        "Ford Escort RS Cosworth YBT", 90.8, 77.0, 134.0, 8.0,
        flywheel_inertia=0.14, redline_rpm=6800, idle_rpm=850,
        heat_release_k=3.6, ve_peak_frac=0.55, ve_width_frac=0.6,
        closed_map_fraction=0.17, exhaust_tone=96.0, exhaust_primary_m=0.5,
        exhaust_total_m=1.8, exhaust_radius_m=0.026, exhaust_channels=1,
        exhaust_openness=0.7, muffler_volume_m3=0.0022, induction="turbo",
        boost_bar=1.1, turbo_lag=0.55, turbo_spool_frac=0.18, bov_flutter=True,
        gear_ratios=[3.61, 2.08, 1.36, 1.0, 0.83], final_drive=3.62,
        vehicle_mass=1275.0, wheel_radius=0.31, clutch_capacity=380.0,
        gearbox_type="manual")


def nissan_silvia_s15() -> Engine:
    return _inline4(
        "Nissan Silvia Spec-R S15 SR20DET", 86.0, 86.0, 136.3, 8.5,
        flywheel_inertia=0.13, redline_rpm=7500, idle_rpm=800,
        heat_release_k=3.5, ve_peak_frac=0.55, ve_width_frac=0.62,
        closed_map_fraction=0.16, exhaust_tone=94.0, exhaust_primary_m=0.5,
        exhaust_total_m=1.85, exhaust_radius_m=0.026, exhaust_channels=1,
        exhaust_openness=0.74, muffler_volume_m3=0.0024, induction="turbo",
        boost_bar=0.9, turbo_lag=0.5, bov_flutter=True,
        gear_ratios=[3.63, 2.18, 1.54, 1.18, 1.0, 0.79], final_drive=4.08,
        vehicle_mass=1240.0, wheel_radius=0.31, clutch_capacity=360.0,
        gearbox_type="manual")


def subaru_wrx_sti_gdb() -> Engine:
    return _flat4(
        "Subaru Impreza WRX STi GDB-C EJ207", 92.0, 75.0, 131.0, 8.0,
        flywheel_inertia=0.18, redline_rpm=8000, idle_rpm=820,
        heat_release_k=4.2, ve_width_frac=0.7, closed_map_fraction=0.17,
        exhaust_tone=72.0, exhaust_primary_m=0.5, exhaust_total_m=1.9,
        exhaust_radius_m=0.025, exhaust_channels=2, exhaust_openness=0.62,
        muffler_volume_m3=0.003, header_unequal_deg=28.0, induction="turbo",
        boost_bar=1.0, turbo_lag=0.45, bov_flutter=True,
        gear_ratios=[3.45, 2.06, 1.45, 1.09, 0.82, 0.65], final_drive=4.44,
        vehicle_mass=1330.0, wheel_radius=0.31, clutch_capacity=480.0,
        gearbox_type="manual")


def subaru_wrx_sti_gv() -> Engine:
    return _flat4(
        "Subaru WRX STi GV EJ257", 99.5, 79.0, 131.5, 8.2,
        flywheel_inertia=0.2, redline_rpm=6700, idle_rpm=800,
        heat_release_k=4.2, ve_width_frac=0.72, closed_map_fraction=0.17,
        exhaust_tone=70.0, exhaust_primary_m=0.55, exhaust_total_m=2.0,
        exhaust_radius_m=0.026, exhaust_channels=2, exhaust_openness=0.58,
        muffler_volume_m3=0.0035, header_unequal_deg=30.0, induction="turbo",
        boost_bar=0.95, turbo_lag=0.5, bov_flutter=True,
        gear_ratios=[3.64, 2.24, 1.59, 1.14, 0.89, 0.71], final_drive=3.90,
        vehicle_mass=1505.0, wheel_radius=0.32, clutch_capacity=520.0,
        gearbox_type="manual")


def subaru_wrx_sti_vt15r() -> Engine:
    return _flat4(
        "Subaru WRX STi VT15R EJ20 (rally)", 92.0, 75.0, 131.0, 8.0,
        flywheel_inertia=0.16, redline_rpm=7500, idle_rpm=950,
        heat_release_k=4.2, ve_width_frac=0.72, closed_map_fraction=0.18,
        exhaust_tone=72.0, exhaust_primary_m=0.5, exhaust_total_m=1.7,
        exhaust_radius_m=0.026, exhaust_channels=2, exhaust_openness=0.8,
        muffler_volume_m3=0.0015, header_unequal_deg=30.0, induction="turbo",
        boost_bar=1.1, turbo_lag=0.5, turbo_spool_frac=0.16, anti_lag=True,
        bov_flutter=True, has_cat=False, straight_cut=True,
        gear_ratios=[2.62, 1.85, 1.4, 1.1, 0.9], final_drive=4.44,
        vehicle_mass=1230.0, wheel_radius=0.32, clutch_capacity=520.0,
        gearbox_type="manual")


def aston_martin_db11_v12() -> Engine:
    """Aston DB11 AE31 5.2 twin-turbo V12 — smooth, deep, refined GT thunder."""
    return _vee("Aston Martin DB11 AE31 5.2TT V12", 12, 89.0, 69.7, 154.0, 9.3,
                30.0, _FO_V12, flywheel_inertia=0.4, redline_rpm=7000, idle_rpm=600,
                heat_release_k=2.4, ve_width_frac=0.78, closed_map_fraction=0.18,
                exhaust_tone=58.0, exhaust_primary_m=0.6, exhaust_total_m=2.4,
                exhaust_radius_m=0.029, exhaust_channels=2, exhaust_openness=0.74,
                muffler_volume_m3=0.004, induction="turbo", boost_bar=1.0,
                turbo_lag=0.4, gear_ratios=[4.71, 3.14, 2.11, 1.67, 1.29, 1.0, 0.84, 0.67],
                final_drive=2.7, vehicle_mass=1875.0, wheel_radius=0.34,
                clutch_capacity=900.0, gearbox_type="at")


def alfa_giulia_quadrifoglio() -> Engine:
    """Alfa Giulia QV 690T 2.9 twin-turbo V6 — hot, raspy, Ferrari-derived bite."""
    return _vee("Alfa Romeo Giulia QV 690T 2.9TT V6", 6, 86.5, 82.0, 145.0, 9.3,
                45.0, _FO_V6, flywheel_inertia=0.18, redline_rpm=7200, idle_rpm=800,
                heat_release_k=3.0, ve_width_frac=0.66, closed_map_fraction=0.16,
                exhaust_tone=104.0, exhaust_primary_m=0.5, exhaust_total_m=1.9,
                exhaust_radius_m=0.024, exhaust_channels=2, exhaust_openness=0.84,
                muffler_volume_m3=0.0018, induction="turbo", boost_bar=1.3,
                turbo_lag=0.3, wall_material="titanium", gear_grain=0.3,
                backpressure_coupling=0.7, gear_ratios=[4.71, 3.14, 2.11, 1.67, 1.29, 1.0, 0.84, 0.67],
                final_drive=3.09, vehicle_mass=1620.0, wheel_radius=0.33,
                clutch_capacity=650.0, gearbox_type="at")


def jaguar_ftype_r_v8() -> Engine:
    """Jaguar F-Type R AJ133 5.0 supercharged V8 — loud, crackly, bangs on lift."""
    return _vee("Jaguar F-Type R AJ133 5.0 SC V8", 8, 92.5, 93.0, 152.0, 9.5,
                45.0, _FO_V8_X, flywheel_inertia=0.3, redline_rpm=6800, idle_rpm=700,
                heat_release_k=3.4, ve_width_frac=0.7, closed_map_fraction=0.12,
                exhaust_tone=82.0, exhaust_primary_m=0.55, exhaust_total_m=2.0,
                exhaust_radius_m=0.028, exhaust_channels=2, exhaust_openness=0.88,
                muffler_volume_m3=0.0022, induction="roots", boost_bar=0.8,
                blower_ratio=9.0, anti_lag=True, gear_ratios=[4.71, 3.14, 2.11, 1.67, 1.29, 1.0, 0.84, 0.67],
                final_drive=3.31, vehicle_mass=1730.0, wheel_radius=0.34,
                clutch_capacity=750.0, gearbox_type="at")


def maserati_granturismo_s() -> Engine:
    """Maserati GranTurismo S F136 (M139) 4.7 — a CROSS-plane 90-deg V8.  Shares the
    F136 block with Ferrari but runs the road-car CROSS-plane crank (firing
    1-8-6-2-7-3-4-5, uneven 90/180-deg per-bank intervals), so it has the deep,
    burbling American-style V8 rumble and a smooth idle rather than the Ferrari
    flat-plane scream."""
    return _vee("Maserati GranTurismo S F136 4.7 V8", 8, 94.0, 84.5, 141.0, 11.3,
                45.0, [1, 8, 6, 2, 7, 3, 4, 5], flywheel_inertia=0.26,
                redline_rpm=7500, idle_rpm=850,
                heat_release_k=3.6, ve_peak_frac=0.7, ve_width_frac=0.62,
                closed_map_fraction=0.12, exhaust_tone=64.0, exhaust_primary_m=0.6,
                exhaust_total_m=2.1, exhaust_radius_m=0.026, exhaust_channels=2,
                exhaust_openness=0.84, muffler_volume_m3=0.0022, wall_material="steel",
                gear_grain=0.18, gear_ratios=[4.06, 2.4, 1.61, 1.16, 0.86, 0.69],
                final_drive=3.73, vehicle_mass=1880.0, wheel_radius=0.34,
                clutch_capacity=620.0, gearbox_type="single")


def jaguar_xj220_v6() -> Engine:
    """Jaguar XJ220 3.5 twin-turbo V6 — a raw, boosty, 90s supercar V6."""
    return _vee("Jaguar XJ220 JRV-6 3.5TT V6", 6, 94.0, 84.0, 152.0, 8.3,
                30.0, _FO_V6, flywheel_inertia=0.2, redline_rpm=7200, idle_rpm=850,
                heat_release_k=3.4, ve_width_frac=0.62, closed_map_fraction=0.17,
                exhaust_tone=98.0, exhaust_primary_m=0.5, exhaust_total_m=1.9,
                exhaust_radius_m=0.026, exhaust_channels=2, exhaust_openness=0.82,
                muffler_volume_m3=0.0015, induction="turbo", boost_bar=1.2,
                turbo_lag=0.5, turbo_spool_frac=0.18, bov_flutter=True,
                gear_ratios=[2.3, 1.61, 1.21, 0.94, 0.76], final_drive=3.31,
                vehicle_mass=1470.0, wheel_radius=0.34, clutch_capacity=620.0,
                gearbox_type="manual")


def donkervoort_d8_gto() -> Engine:
    """Donkervoort D8 GTO — Audi EA855 2.5 TFSI I5: the raw, light, 5-cyl warble."""
    return _inline("Donkervoort D8 GTO EA855 2.5TT I5", 5, 82.5, 92.8, 145.0, 10.0,
                   [1, 2, 4, 5, 3], flywheel_inertia=0.13, redline_rpm=7000,
                   idle_rpm=850, heat_release_k=3.4, ve_width_frac=0.66,
                   closed_map_fraction=0.16, exhaust_tone=96.0, exhaust_primary_m=0.5,
                   exhaust_total_m=1.7, exhaust_radius_m=0.025, exhaust_channels=1,
                   exhaust_openness=0.86, muffler_volume_m3=0.0012, induction="turbo",
                   boost_bar=1.1, turbo_lag=0.3, has_cat=False,
                   gear_ratios=[3.5, 2.16, 1.59, 1.26, 1.03], final_drive=3.64,
                   vehicle_mass=695.0, wheel_radius=0.31, clutch_capacity=420.0,
                   gearbox_type="manual")


def tvr_cerbera_speed12() -> Engine:
    """TVR Cerbera Speed 12 — AJP 7.7 V12 (two Speed Six I6s): brutal, raw, race."""
    return _vee("TVR Cerbera Speed 12 AJP 7.7 V12", 12, 93.0, 77.0, 148.0, 11.0,
                30.0, _FO_V12, flywheel_inertia=0.28, redline_rpm=7500, idle_rpm=900,
                heat_release_k=3.8, ve_peak_frac=0.72, ve_width_frac=0.6,
                closed_map_fraction=0.11, exhaust_tone=120.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.7, exhaust_radius_m=0.021, exhaust_channels=2,
                exhaust_openness=0.96, muffler_volume_m3=0.001, has_cat=False,
                straight_cut=True, wall_material="titanium", gear_grain=0.3,
                gear_ratios=[2.9, 2.1, 1.6, 1.3, 1.05, 0.85], final_drive=3.6,
                vehicle_mass=1100.0, wheel_radius=0.33, clutch_capacity=750.0,
                gearbox_type="manual")


def bmw_m3_e36() -> Engine:
    """BMW M3 E36 S50B30 3.0 I6 — the smooth, linear, high-revving straight-six."""
    return _inline("BMW M3 E36 S50B30 3.0 I6", 6, 86.0, 85.8, 135.0, 10.8, _FO_I6,
                   flywheel_inertia=0.22, redline_rpm=7200, idle_rpm=780,
                   heat_release_k=3.3, ve_peak_frac=0.62, ve_width_frac=0.62,
                   closed_map_fraction=0.14, exhaust_tone=86.0, exhaust_primary_m=0.55,
                   exhaust_total_m=2.0, exhaust_radius_m=0.026, exhaust_channels=1,
                   exhaust_openness=0.78, muffler_volume_m3=0.0026,
                   gear_ratios=[4.2, 2.49, 1.66, 1.24, 1.0], final_drive=3.23,
                   vehicle_mass=1440.0, wheel_radius=0.32, clutch_capacity=420.0,
                   gearbox_type="manual")


def bmw_330i_n53() -> Engine:
    """BMW 330i (E90) N53B30 3.0 I6 — direct-injection NA straight-six: a
    refined, creamy, muffled hum (no M-car rasp), double-VANOS, ~272 hp."""
    return _inline("BMW 330i (E90) N53B30 3.0 I6", 6, 85.0, 88.0, 140.0, 10.7, _FO_I6,
                   flywheel_inertia=0.24, redline_rpm=7000, idle_rpm=700,
                   heat_release_k=3.05, ve_peak_frac=0.55, ve_width_frac=0.6,
                   closed_map_fraction=0.15, exhaust_tone=80.0, exhaust_primary_m=0.5,
                   exhaust_total_m=2.15, exhaust_radius_m=0.025, exhaust_channels=1,
                   exhaust_openness=0.5, muffler_volume_m3=0.0042, has_cat=True,
                   variable_valve="double-VANOS",
                   gear_ratios=[4.17, 2.34, 1.52, 1.14, 0.87, 0.69], final_drive=3.46,
                   vehicle_mass=1525.0, wheel_radius=0.32, clutch_capacity=400.0,
                   gearbox_type="manual")


def bmw_m5_e60_v10() -> Engine:
    """BMW M5 E60 S85 5.0 V10 — the F1-derived, 8250-rpm screaming road V10."""
    return _vee("BMW M5 E60 S85 5.0 V10", 10, 92.0, 75.2, 139.0, 12.0, 45.0, _FO_V10,
                flywheel_inertia=0.2, redline_rpm=8250, idle_rpm=900,
                heat_release_k=3.6, ve_peak_frac=0.78, ve_width_frac=0.6,
                closed_map_fraction=0.12, exhaust_tone=120.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.95, exhaust_radius_m=0.021, exhaust_channels=2,
                exhaust_openness=0.9, muffler_volume_m3=0.0016, wall_material="titanium",
                gear_grain=0.3, gear_ratios=[4.06, 2.4, 1.58, 1.19, 1.0, 0.87, 0.74],
                final_drive=3.62, vehicle_mass=1830.0, wheel_radius=0.34,
                clutch_capacity=680.0, gearbox_type="single")


def mercedes_e63_amg_m157() -> Engine:
    """Mercedes E63 AMG M157 5.5 biturbo V8 — deep, muscular, muffled hot-V rumble."""
    return _vee("Mercedes-Benz E63 AMG M157 5.5TT V8", 8, 98.0, 90.5, 154.0, 10.0,
                45.0, _FO_V8_X, flywheel_inertia=0.32, redline_rpm=6500, idle_rpm=620,
                heat_release_k=2.8, ve_width_frac=0.74, closed_map_fraction=0.18,
                exhaust_tone=64.0, exhaust_primary_m=0.62, exhaust_total_m=2.3,
                exhaust_radius_m=0.030, exhaust_channels=2, exhaust_openness=0.62,
                muffler_volume_m3=0.004, induction="turbo", boost_bar=1.0,
                turbo_lag=0.4, gear_ratios=[4.38, 2.86, 1.92, 1.37, 1.0, 0.82, 0.73],
                final_drive=2.82, vehicle_mass=1880.0, wheel_radius=0.34,
                clutch_capacity=900.0, gearbox_type="at")


def mercedes_c63_black_m156() -> Engine:
    """Mercedes C63 AMG Black Series M156 6.2 NA V8 — thunderous cross-plane burble."""
    return _vee("Mercedes-Benz C63 AMG BS M156 6.2 V8", 8, 102.2, 94.6, 155.0, 11.3,
                45.0, _FO_V8_X, flywheel_inertia=0.3, redline_rpm=7200, idle_rpm=650,
                heat_release_k=3.4, ve_peak_frac=0.62, ve_width_frac=0.66,
                closed_map_fraction=0.12, exhaust_tone=66.0, exhaust_primary_m=0.6,
                exhaust_total_m=2.1, exhaust_radius_m=0.029, exhaust_channels=2,
                exhaust_openness=0.84, muffler_volume_m3=0.0018,
                gear_ratios=[4.38, 2.86, 1.92, 1.37, 1.0, 0.82, 0.73], final_drive=3.07,
                vehicle_mass=1720.0, wheel_radius=0.34, clutch_capacity=720.0,
                gearbox_type="at")


def cadillac_ct5v_blackwing() -> Engine:
    """Cadillac CT5-V Blackwing LT4 6.2 supercharged V8 — blown American thunder."""
    return _vee("Cadillac CT5-V Blackwing LT4 6.2 SC V8", 8, 103.25, 92.0, 154.0, 10.0,
                45.0, _FO_V8_X, flywheel_inertia=0.34, redline_rpm=6500, idle_rpm=620,
                heat_release_k=3.2, ve_width_frac=0.72, closed_map_fraction=0.16,
                exhaust_tone=62.0, exhaust_primary_m=0.6, exhaust_total_m=2.1,
                exhaust_radius_m=0.030, exhaust_channels=2, exhaust_openness=0.72,
                muffler_volume_m3=0.0028, induction="roots", boost_bar=0.7,
                blower_ratio=8.5, valvetrain="ohv", valves_per_cyl=2,
                gear_ratios=[2.97, 2.07, 1.43, 1.0, 0.71, 0.57], final_drive=3.27,
                vehicle_mass=1840.0, wheel_radius=0.34, clutch_capacity=850.0,
                gearbox_type="manual")


def porsche_carrera_rs_27() -> Engine:
    """Porsche 911 Carrera RS 2.7 — air-cooled flat-six: raw, mechanical thrash."""
    return _vee("Porsche 911 Carrera RS 2.7 air-cooled flat-6", 6, 90.0, 70.4, 127.0,
                8.5, 90.0, _FO_FLAT6, flywheel_inertia=0.16, redline_rpm=7300, idle_rpm=900,
                heat_release_k=3.6, ve_peak_frac=0.66, closed_map_fraction=0.14,
                exhaust_tone=98.0, exhaust_primary_m=0.5, exhaust_total_m=1.8,
                exhaust_radius_m=0.023, exhaust_channels=2, exhaust_openness=0.85,
                muffler_volume_m3=0.0016, header_unequal_deg=12.0,
                backpressure_coupling=0.75, gear_grain=0.2,
                gear_ratios=[3.18, 1.83, 1.26, 0.93, 0.72], final_drive=4.43,
                vehicle_mass=975.0, wheel_radius=0.31, clutch_capacity=320.0,
                gearbox_type="manual")


def porsche_930_turbo() -> Engine:
    """Porsche 911 Turbo 3.3 (930) — air-cooled single-turbo flat-six, big lag + whistle."""
    return _vee("Porsche 911 Turbo 3.3 (930) air-cooled flat-6", 6, 97.0, 74.4, 127.0,
                7.0, 90.0, _FO_FLAT6, flywheel_inertia=0.2, redline_rpm=7000, idle_rpm=900,
                heat_release_k=3.4, ve_width_frac=0.6, closed_map_fraction=0.16,
                exhaust_tone=86.0, exhaust_primary_m=0.5, exhaust_total_m=1.9,
                exhaust_radius_m=0.026, exhaust_channels=2, exhaust_openness=0.78,
                muffler_volume_m3=0.0024, header_unequal_deg=10.0, induction="turbo",
                boost_bar=0.8, turbo_lag=0.85, turbo_spool_frac=0.4, bov_flutter=True,
                gear_ratios=[3.17, 1.79, 1.26, 0.93], final_drive=4.22,
                vehicle_mass=1300.0, wheel_radius=0.32, clutch_capacity=520.0,
                gearbox_type="manual")


def porsche_993_gt2() -> Engine:
    """Porsche 911 GT2 (993) — air-cooled twin-turbo flat-six, aggressive widowmaker."""
    return _vee("Porsche 911 GT2 (993) air-cooled TT flat-6", 6, 100.0, 76.4, 127.0,
                8.0, 90.0, _FO_FLAT6, flywheel_inertia=0.18, redline_rpm=7000, idle_rpm=900,
                heat_release_k=3.5, ve_width_frac=0.62, closed_map_fraction=0.15,
                exhaust_tone=88.0, exhaust_primary_m=0.48, exhaust_total_m=1.8,
                exhaust_radius_m=0.025, exhaust_channels=2, exhaust_openness=0.86,
                muffler_volume_m3=0.0014, header_unequal_deg=10.0, induction="turbo",
                boost_bar=0.9, turbo_lag=0.5, bov_flutter=True, has_cat=False,
                gear_ratios=[3.15, 1.79, 1.26, 0.97, 0.79, 0.66], final_drive=3.44,
                vehicle_mass=1290.0, wheel_radius=0.32, clutch_capacity=560.0,
                gearbox_type="manual")


def porsche_996_gt1() -> Engine:
    """Porsche 911 GT1 Strassenversion (996) — water-cooled race twin-turbo flat-six."""
    return _vee("Porsche 911 GT1 Strassenversion (996) TT flat-6", 6, 95.0, 74.4, 127.0,
                8.5, 90.0, _FO_FLAT6, flywheel_inertia=0.16, redline_rpm=7400, idle_rpm=950,
                heat_release_k=3.5, ve_peak_frac=0.7, ve_width_frac=0.6,
                closed_map_fraction=0.13, exhaust_tone=96.0, exhaust_primary_m=0.45,
                exhaust_total_m=1.6, exhaust_radius_m=0.022, exhaust_channels=2,
                exhaust_openness=0.93, muffler_volume_m3=0.0008, induction="turbo",
                boost_bar=1.1, turbo_lag=0.4, has_cat=False, straight_cut=True,
                wall_material="titanium", gear_grain=0.3,
                gear_ratios=[2.74, 1.81, 1.35, 1.08, 0.89, 0.74], final_drive=3.44,
                vehicle_mass=1150.0, wheel_radius=0.33, clutch_capacity=620.0,
                gearbox_type="manual")   # Strassenversion: 6-speed H-pattern manual


def porsche_997_gt3_rs_40() -> Engine:
    """Porsche 911 GT3 RS 4.0 (997.2) — the Mezger 4.0 NA flat-six: a metallic scream."""
    return _vee("Porsche 911 GT3 RS 4.0 (997.2) Mezger flat-6", 6, 102.7, 80.4, 127.0,
                12.6, 90.0, _FO_FLAT6, flywheel_inertia=0.15, redline_rpm=8500, idle_rpm=950,
                heat_release_k=3.7, ve_peak_frac=0.76, ve_width_frac=0.6,
                closed_map_fraction=0.12, exhaust_tone=110.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.85, exhaust_radius_m=0.020, exhaust_channels=2,
                exhaust_openness=0.93, muffler_volume_m3=0.0012, header_unequal_deg=12.0,
                backpressure_coupling=0.8, gear_grain=0.3,
                gear_ratios=[3.15, 2.0, 1.48, 1.13, 0.92, 0.78], final_drive=3.89,
                vehicle_mass=1370.0, wheel_radius=0.33, clutch_capacity=560.0,
                gearbox_type="manual")   # 997.2 GT3 RS 4.0: manual-only (no PDK)


def porsche_991_gt3_rs() -> Engine:
    """Porsche 911 GT3 RS (991.1) — 4.0 NA flat-six (9A1), 8800-rpm howl."""
    return _vee("Porsche 911 GT3 RS 4.0 (991.1) 9A1 flat-6", 6, 102.0, 81.5, 127.0,
                12.9, 90.0, _FO_FLAT6, flywheel_inertia=0.15, redline_rpm=8800, idle_rpm=950,
                heat_release_k=3.6, ve_peak_frac=0.76, ve_width_frac=0.62,
                closed_map_fraction=0.12, exhaust_tone=106.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.85, exhaust_radius_m=0.022, exhaust_channels=2,
                exhaust_openness=0.9, muffler_volume_m3=0.0014, header_unequal_deg=13.0,
                backpressure_coupling=0.8, gear_grain=0.25,
                gear_ratios=[3.91, 2.32, 1.61, 1.28, 1.08, 0.88, 0.74], final_drive=3.97,
                vehicle_mass=1420.0, wheel_radius=0.33, clutch_capacity=560.0,
                gearbox_type="dct")


def porsche_991_gt2_rs() -> Engine:
    """Porsche 911 GT2 RS (991.2) — 3.8 twin-turbo flat-six, the 700-hp brute."""
    return _vee("Porsche 911 GT2 RS (991.2) 3.8TT flat-6", 6, 102.0, 77.5, 127.0,
                9.0, 90.0, _FO_FLAT6, flywheel_inertia=0.18, redline_rpm=7200, idle_rpm=900,
                heat_release_k=3.2, ve_width_frac=0.66, closed_map_fraction=0.15,
                exhaust_tone=82.0, exhaust_primary_m=0.5, exhaust_total_m=2.0,
                exhaust_radius_m=0.027, exhaust_channels=2, exhaust_openness=0.7,
                muffler_volume_m3=0.0018, header_unequal_deg=10.0, induction="turbo",
                boost_bar=1.55, turbo_lag=0.3, gear_ratios=[3.91, 2.32, 1.61, 1.28, 1.08, 0.88, 0.74],
                final_drive=3.97, vehicle_mass=1470.0, wheel_radius=0.33,
                clutch_capacity=700.0, gearbox_type="dct")


def porsche_917_lh() -> Engine:
    """Porsche 917 LH — air-cooled 4.9 flat-TWELVE (Type 912): the Le Mans wail."""
    return _vee("Porsche 917 LH Type 912 4.9 air-cooled flat-12", 12, 86.0, 70.4, 123.0,
                10.5, 90.0, _FO_V12, flywheel_inertia=0.22, redline_rpm=8400, idle_rpm=1100,
                heat_release_k=3.7, ve_peak_frac=0.74, ve_width_frac=0.6,
                closed_map_fraction=0.11, exhaust_tone=112.0, exhaust_primary_m=0.42,
                exhaust_total_m=1.3, exhaust_radius_m=0.020, exhaust_channels=2,
                exhaust_openness=0.97, muffler_volume_m3=0.0007, has_cat=False,
                straight_cut=True, gear_grain=0.32,
                gear_ratios=[2.3, 1.7, 1.35, 1.1, 0.9], final_drive=3.0,
                vehicle_mass=800.0, wheel_radius=0.33, clutch_capacity=700.0,
                gearbox_type="manual")


def ferrari_enzo_v12() -> Engine:
    """Ferrari Enzo F140B 6.0 NA 65-deg V12 — the bright, metallic F140 scream."""
    return _vee("Ferrari Enzo F140B 6.0 V12", 12, 92.0, 75.2, 147.0, 11.2, 32.5, _FO_V12,
                flywheel_inertia=0.26, redline_rpm=8200, idle_rpm=900,
                heat_release_k=3.6, ve_peak_frac=0.74, ve_width_frac=0.6,
                closed_map_fraction=0.13, exhaust_tone=96.0, exhaust_primary_m=0.6,
                exhaust_total_m=2.0, exhaust_radius_m=0.024, exhaust_channels=2,
                exhaust_openness=0.92, muffler_volume_m3=0.0018, wall_material="titanium",
                gear_grain=0.45, backpressure_coupling=0.6,
                gear_ratios=[3.15, 2.18, 1.67, 1.33, 1.07, 0.85], final_drive=4.1,
                vehicle_mass=1365.0, wheel_radius=0.34, clutch_capacity=620.0,
                gearbox_type="single")


def pagani_zonda_r() -> Engine:
    """Pagani Zonda R — Mercedes M120 6.0 V12 race: very open, raw, deafening."""
    return _vee("Pagani Zonda R Mercedes M120 6.0 V12", 12, 89.0, 80.2, 147.0, 11.0,
                30.0, _FO_V12, flywheel_inertia=0.24, redline_rpm=8000, idle_rpm=950,
                heat_release_k=3.7, ve_peak_frac=0.72, ve_width_frac=0.6,
                closed_map_fraction=0.11, exhaust_tone=104.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.5, exhaust_radius_m=0.022, exhaust_channels=2,
                exhaust_openness=0.96, muffler_volume_m3=0.0008, has_cat=False,
                straight_cut=True, wall_material="titanium", gear_grain=0.35,
                gear_ratios=[2.92, 2.04, 1.58, 1.28, 1.06, 0.88], final_drive=3.7,
                vehicle_mass=1070.0, wheel_radius=0.34, clutch_capacity=720.0,
                gearbox_type="single")


def koenigsegg_one1_v8() -> Engine:
    """Koenigsegg One:1 / Agera RS — 5.0 twin-turbo flat-plane V8, high-revving boost."""
    return _vee("Koenigsegg One:1 5.0TT V8", 8, 92.0, 95.25, 154.0, 9.5, 45.0, _FO_V8_FLAT,
                flywheel_inertia=0.24, redline_rpm=8250, idle_rpm=850,
                heat_release_k=3.4, ve_peak_frac=0.7, ve_width_frac=0.62,
                closed_map_fraction=0.13, exhaust_tone=98.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.8, exhaust_radius_m=0.024, exhaust_channels=2,
                exhaust_openness=0.86, muffler_volume_m3=0.0014, induction="turbo",
                boost_bar=1.5, turbo_lag=0.3, wall_material="titanium", gear_grain=0.25,
                gear_ratios=[2.85, 1.99, 1.5, 1.18, 0.96, 0.79, 0.66], final_drive=3.36,
                vehicle_mass=1360.0, wheel_radius=0.34, clutch_capacity=900.0,
                gearbox_type="dct")


def ariel_atom_v8() -> Engine:
    """Ariel Atom V8 — Hartley 3.0 NA flat-plane V8 to 10,500 rpm: the tiny screamer."""
    return _vee("Ariel Atom V8 Hartley 3.0 V8", 8, 90.0, 58.8, 110.0, 11.5, 45.0, _FO_V8_FLAT,
                flywheel_inertia=0.08, redline_rpm=10500, idle_rpm=1100,
                heat_release_k=3.6, ve_peak_frac=0.82, ve_width_frac=0.55,
                closed_map_fraction=0.11, exhaust_tone=150.0, exhaust_primary_m=0.42,
                exhaust_total_m=1.5, exhaust_radius_m=0.018, exhaust_channels=2,
                exhaust_openness=0.96, muffler_volume_m3=0.0007, has_cat=False,
                straight_cut=True, wall_material="titanium", gear_grain=0.3,
                gear_ratios=[2.7, 1.9, 1.45, 1.15, 0.95, 0.8], final_drive=3.6,
                vehicle_mass=550.0, wheel_radius=0.30, clutch_capacity=420.0,
                gearbox_type="single")


def ferrari_250_california() -> Engine:
    """Ferrari 250 California — Colombo 3.0 60-deg V12 — a mellow, warm, vintage wail."""
    return _vee("Ferrari 250 California Colombo 3.0 V12", 12, 73.0, 58.8, 130.0, 9.0,
                30.0, _FO_V12, flywheel_inertia=0.3, redline_rpm=7000, idle_rpm=750,
                heat_release_k=2.6, ve_width_frac=0.66, closed_map_fraction=0.16,
                exhaust_tone=80.0, exhaust_primary_m=0.55, exhaust_total_m=2.0,
                exhaust_radius_m=0.025, exhaust_channels=2, exhaust_openness=0.82,
                muffler_volume_m3=0.0022, gear_grain=0.2,
                gear_ratios=[3.14, 2.1, 1.55, 1.18, 0.9], final_drive=3.44,
                vehicle_mass=1100.0, wheel_radius=0.34, clutch_capacity=400.0,
                gearbox_type="manual")


def ferrari_488_gtb() -> Engine:
    """Ferrari 488 GTB — F154CB 3.9 twin-turbo flat-plane V8 — the modern turbo scream."""
    return _vee("Ferrari 488 GTB F154 3.9TT V8", 8, 86.5, 83.0, 152.0, 9.4, 45.0, _FO_V8_FLAT,
                flywheel_inertia=0.2, redline_rpm=8000, idle_rpm=850,
                heat_release_k=3.2, ve_peak_frac=0.7, ve_width_frac=0.62,
                closed_map_fraction=0.14, exhaust_tone=108.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.9, exhaust_radius_m=0.024, exhaust_channels=2,
                exhaust_openness=0.78, muffler_volume_m3=0.0016, induction="turbo",
                boost_bar=1.2, turbo_lag=0.25, wall_material="titanium", gear_grain=0.35,
                gear_ratios=[3.08, 2.19, 1.63, 1.29, 1.03, 0.84, 0.69], final_drive=3.7,
                vehicle_mass=1475.0, wheel_radius=0.34, clutch_capacity=700.0,
                gearbox_type="dct")


def ferrari_488_pista() -> Engine:
    """Ferrari 488 Pista — F154 twin-turbo V8, the track-sharpened, more-open 488."""
    return _vee("Ferrari 488 Pista F154 3.9TT V8", 8, 86.5, 83.0, 152.0, 9.45, 45.0, _FO_V8_FLAT,
                flywheel_inertia=0.18, redline_rpm=8000, idle_rpm=850,
                heat_release_k=3.3, ve_peak_frac=0.72, ve_width_frac=0.6,
                closed_map_fraction=0.13, exhaust_tone=112.0, exhaust_primary_m=0.48,
                exhaust_total_m=1.8, exhaust_radius_m=0.022, exhaust_channels=2,
                exhaust_openness=0.86, muffler_volume_m3=0.0012, induction="turbo",
                boost_bar=1.3, turbo_lag=0.22, wall_material="titanium", gear_grain=0.38,
                gear_ratios=[3.08, 2.19, 1.63, 1.29, 1.03, 0.84, 0.69], final_drive=3.7,
                vehicle_mass=1385.0, wheel_radius=0.34, clutch_capacity=720.0,
                gearbox_type="dct")


def ferrari_f50_gt() -> Engine:
    """Ferrari F50 GT — F130 4.7 NA V12 race — a raw, 10,000-rpm race scream."""
    return _vee("Ferrari F50 GT F130B 4.7 V12", 12, 85.0, 69.0, 140.0, 12.1, 32.5, _FO_V12,
                flywheel_inertia=0.18, redline_rpm=10000, idle_rpm=1000,
                heat_release_k=3.7, ve_peak_frac=0.78, ve_width_frac=0.58,
                closed_map_fraction=0.11, exhaust_tone=140.0, exhaust_primary_m=0.45,
                exhaust_total_m=1.5, exhaust_radius_m=0.019, exhaust_channels=2,
                exhaust_openness=0.97, muffler_volume_m3=0.0007, has_cat=False,
                straight_cut=True, wall_material="titanium", gear_grain=0.4,
                gear_ratios=[2.9, 2.05, 1.6, 1.3, 1.08, 0.9], final_drive=3.5,
                vehicle_mass=1000.0, wheel_radius=0.33, clutch_capacity=720.0,
                gearbox_type="single")


def ferrari_fxxk() -> Engine:
    """Ferrari FXX-K — F140 6.3 V12 hybrid track car — the F140 scream + ERS shove."""
    return _vee("Ferrari FXX-K F140 6.3 V12 hybrid", 12, 94.0, 75.2, 147.0, 13.5, 32.5, _FO_V12,
                flywheel_inertia=0.24, redline_rpm=9200, idle_rpm=950,
                heat_release_k=3.7, ve_peak_frac=0.76, ve_width_frac=0.6,
                closed_map_fraction=0.12, exhaust_tone=112.0, exhaust_primary_m=0.55,
                exhaust_total_m=1.7, exhaust_radius_m=0.021, exhaust_channels=2,
                exhaust_openness=0.94, muffler_volume_m3=0.0009, has_cat=False,
                straight_cut=True, wall_material="titanium", gear_grain=0.45,
                hybrid_kw=140.0, hybrid_base_rpm=3000.0,
                gear_ratios=[3.08, 2.19, 1.63, 1.29, 1.03, 0.84, 0.69], final_drive=3.7,
                vehicle_mass=1255.0, wheel_radius=0.34, clutch_capacity=760.0,
                gearbox_type="dct")


def mclaren_senna() -> Engine:
    """McLaren Senna — M840TR 4.0 twin-turbo flat-plane V8 — hard, clinical, brutal."""
    return _vee("McLaren Senna M840TR 4.0TT V8", 8, 93.0, 73.5, 150.0, 9.4, 45.0, _FO_V8_FLAT,
                flywheel_inertia=0.18, redline_rpm=8200, idle_rpm=850,
                heat_release_k=3.3, ve_peak_frac=0.72, ve_width_frac=0.6,
                closed_map_fraction=0.13, exhaust_tone=110.0, exhaust_primary_m=0.45,
                exhaust_total_m=1.6, exhaust_radius_m=0.022, exhaust_channels=2,
                exhaust_openness=0.84, muffler_volume_m3=0.0011, induction="turbo",
                boost_bar=1.35, turbo_lag=0.22, wall_material="titanium", gear_grain=0.3,
                gear_ratios=[3.0, 2.19, 1.69, 1.37, 1.14, 0.95, 0.79], final_drive=3.31,
                vehicle_mass=1300.0, wheel_radius=0.34, clutch_capacity=720.0,
                gearbox_type="dct")


def lamborghini_countach_qv() -> Engine:
    """Lamborghini Countach LP5000 QV — 5.2 60-deg V12 — raw, hard, analog 80s V12."""
    return _vee("Lamborghini Countach LP5000 QV 5.2 V12", 12, 85.5, 75.0, 142.0, 9.5,
                30.0, _FO_V12, flywheel_inertia=0.32, redline_rpm=7200, idle_rpm=900,
                heat_release_k=3.5, ve_width_frac=0.62, closed_map_fraction=0.12,
                exhaust_tone=70.0, exhaust_primary_m=0.56, exhaust_total_m=2.0,
                exhaust_radius_m=0.028, exhaust_channels=2, exhaust_openness=0.84,
                muffler_volume_m3=0.0016, backpressure_coupling=0.7, gear_grain=0.2,
                gear_ratios=[2.99, 2.04, 1.43, 1.0, 0.78], final_drive=4.09,
                vehicle_mass=1490.0, wheel_radius=0.34, clutch_capacity=560.0,
                gearbox_type="manual")


def aston_valkyrie_v12() -> Engine:
    """Aston Martin Valkyrie — Cosworth 6.5 NA V12 to 11,100 rpm — the ultimate scream."""
    return _vee("Aston Martin Valkyrie Cosworth 6.5 V12", 12, 89.0, 87.0, 150.0, 11.5,
                32.5, _FO_V12, flywheel_inertia=0.16, redline_rpm=11100, idle_rpm=1000,
                heat_release_k=3.7, ve_peak_frac=0.8, ve_width_frac=0.58,
                closed_map_fraction=0.11, exhaust_tone=150.0, exhaust_primary_m=0.42,
                exhaust_total_m=1.4, exhaust_radius_m=0.018, exhaust_channels=2,
                exhaust_openness=0.98, muffler_volume_m3=0.0006, has_cat=False,
                straight_cut=True, wall_material="titanium", gear_grain=0.45,
                gear_ratios=[2.9, 2.1, 1.65, 1.35, 1.12, 0.95, 0.8], final_drive=3.4,
                vehicle_mass=1030.0, wheel_radius=0.34, clutch_capacity=720.0,
                gearbox_type="single")


def aston_valhalla_v8() -> Engine:
    """Aston Martin Valhalla — AMG M178 4.0 twin-turbo flat-plane V8 hybrid."""
    return _vee("Aston Martin Valhalla M178 4.0TT V8 hybrid", 8, 83.0, 92.0, 150.0, 8.6,
                45.0, _FO_V8_FLAT, flywheel_inertia=0.2, redline_rpm=7200, idle_rpm=820,
                heat_release_k=3.0, ve_width_frac=0.66, closed_map_fraction=0.16,
                exhaust_tone=96.0, exhaust_primary_m=0.5, exhaust_total_m=1.9,
                exhaust_radius_m=0.026, exhaust_channels=2, exhaust_openness=0.72,
                muffler_volume_m3=0.0018, induction="turbo", boost_bar=1.3,
                turbo_lag=0.25, hybrid_kw=150.0, hybrid_base_rpm=2800.0, gear_grain=0.2,
                gear_ratios=[3.0, 2.19, 1.69, 1.37, 1.14, 0.95, 0.79], final_drive=3.31,
                vehicle_mass=1550.0, wheel_radius=0.34, clutch_capacity=720.0,
                gearbox_type="dct")


def aston_vulcan_v12() -> Engine:
    """Aston Martin Vulcan — 7.0 NA V12 track car — a deep, raw, race V12 roar."""
    return _vee("Aston Martin Vulcan 7.0 V12", 12, 100.0, 74.5, 150.0, 11.0, 30.0, _FO_V12,
                flywheel_inertia=0.26, redline_rpm=7750, idle_rpm=900,
                heat_release_k=3.6, ve_peak_frac=0.72, ve_width_frac=0.62,
                closed_map_fraction=0.12, exhaust_tone=86.0, exhaust_primary_m=0.55,
                exhaust_total_m=1.8, exhaust_radius_m=0.023, exhaust_channels=2,
                exhaust_openness=0.93, muffler_volume_m3=0.0012, has_cat=False,
                straight_cut=True, gear_grain=0.35,
                gear_ratios=[2.9, 2.05, 1.6, 1.3, 1.08, 0.9], final_drive=3.6,
                vehicle_mass=1350.0, wheel_radius=0.34, clutch_capacity=720.0,
                gearbox_type="single")


def mercedes_clk_gtr() -> Engine:
    """Mercedes-Benz CLK GTR — M120 6.0 60-deg V12 GT1 race — open, raw, race wail."""
    return _vee("Mercedes-Benz CLK GTR M120 6.0 V12", 12, 89.0, 80.2, 147.0, 10.5, 30.0,
                _FO_V12, flywheel_inertia=0.26, redline_rpm=7500, idle_rpm=950,
                heat_release_k=3.6, ve_peak_frac=0.72, ve_width_frac=0.6,
                closed_map_fraction=0.12, exhaust_tone=98.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.6, exhaust_radius_m=0.023, exhaust_channels=2,
                exhaust_openness=0.92, muffler_volume_m3=0.001, has_cat=False,
                straight_cut=True, wall_material="titanium", gear_grain=0.32,
                gear_ratios=[2.6, 1.9, 1.5, 1.25, 1.05, 0.88], final_drive=3.5,
                vehicle_mass=1440.0, wheel_radius=0.34, clutch_capacity=720.0,
                gearbox_type="single")


def ford_gt40_mk2() -> Engine:
    """Ford GT40 MK2 — 427 (7.0) NA OHV cross-plane V8 — the Le Mans American thunder."""
    return _vee("Ford GT40 MK2 427 7.0 V8", 8, 107.4, 96.0, 160.0, 10.5, 45.0, _FO_V8_X,
                flywheel_inertia=0.34, redline_rpm=6500, idle_rpm=750,
                heat_release_k=3.5, ve_width_frac=0.66, closed_map_fraction=0.12,
                exhaust_tone=60.0, exhaust_primary_m=0.55, exhaust_total_m=1.7,
                exhaust_radius_m=0.030, exhaust_channels=2, exhaust_openness=0.92,
                muffler_volume_m3=0.001, valvetrain="ohv", valves_per_cyl=2,
                has_cat=False, straight_cut=True,
                gear_ratios=[2.36, 1.62, 1.27, 1.0], final_drive=3.09,
                vehicle_mass=1180.0, wheel_radius=0.33, clutch_capacity=680.0,
                gearbox_type="manual")


def ford_gt350r_voodoo() -> Engine:
    """Ford Shelby GT350R — Voodoo 5.2 NA FLAT-PLANE V8 — a Mustang that screams European."""
    return _vee("Ford Shelby GT350R Voodoo 5.2 V8", 8, 94.0, 93.0, 150.0, 12.0, 45.0,
                _FO_V8_FLAT, flywheel_inertia=0.24, redline_rpm=8250, idle_rpm=750,
                heat_release_k=3.5, ve_peak_frac=0.72, ve_width_frac=0.6,
                closed_map_fraction=0.13, exhaust_tone=104.0, exhaust_primary_m=0.55,
                exhaust_total_m=2.0, exhaust_radius_m=0.025, exhaust_channels=2,
                exhaust_openness=0.86, muffler_volume_m3=0.0016, gear_grain=0.25,
                gear_ratios=[3.24, 2.19, 1.65, 1.29, 1.0, 0.83], final_drive=3.73,
                vehicle_mass=1650.0, wheel_radius=0.34, clutch_capacity=640.0,
                gearbox_type="manual")


def ford_gt500_predator() -> Engine:
    """Ford Shelby GT500 — Predator 5.2 supercharged cross-plane V8 — blown brute."""
    return _vee("Ford Shelby GT500 Predator 5.2 SC V8", 8, 94.0, 93.0, 150.0, 9.5, 45.0,
                _FO_V8_X, flywheel_inertia=0.3, redline_rpm=7500, idle_rpm=720,
                heat_release_k=3.2, ve_width_frac=0.7, closed_map_fraction=0.15,
                exhaust_tone=70.0, exhaust_primary_m=0.58, exhaust_total_m=2.1,
                exhaust_radius_m=0.029, exhaust_channels=2, exhaust_openness=0.78,
                muffler_volume_m3=0.0024, induction="roots", boost_bar=0.9,
                blower_ratio=9.0, gear_ratios=[3.24, 2.19, 1.65, 1.29, 1.0, 0.83, 0.66],
                final_drive=3.73, vehicle_mass=1800.0, wheel_radius=0.34,
                clutch_capacity=900.0, gearbox_type="dct")


def dodge_charger_rt() -> Engine:
    """Dodge Charger R/T — 5.7 HEMI cross-plane OHV V8 — lazy American muscle burble."""
    return _vee("Dodge Charger R/T 5.7 HEMI V8", 8, 99.5, 90.9, 155.0, 10.5, 45.0, _FO_V8_X,
                flywheel_inertia=0.34, redline_rpm=6000, idle_rpm=620,
                heat_release_k=3.4, ve_width_frac=0.68, closed_map_fraction=0.14,
                exhaust_tone=58.0, exhaust_primary_m=0.6, exhaust_total_m=2.2,
                exhaust_radius_m=0.030, exhaust_channels=2, exhaust_openness=0.7,
                muffler_volume_m3=0.003, valvetrain="ohv", valves_per_cyl=2,
                gear_ratios=[3.59, 2.19, 1.41, 1.0, 0.83, 0.69, 0.58, 0.48], final_drive=2.62,
                vehicle_mass=1950.0, wheel_radius=0.34, clutch_capacity=600.0,
                gearbox_type="at")


def dodge_challenger_rt() -> Engine:
    """Dodge Challenger R/T 392 — 6.4 HEMI cross-plane OHV V8 — bigger, harder muscle."""
    return _vee("Dodge Challenger R/T 392 6.4 HEMI V8", 8, 103.9, 94.6, 155.0, 10.9, 45.0,
                _FO_V8_X, flywheel_inertia=0.34, redline_rpm=6400, idle_rpm=620,
                heat_release_k=3.5, ve_width_frac=0.68, closed_map_fraction=0.13,
                exhaust_tone=56.0, exhaust_primary_m=0.62, exhaust_total_m=2.1,
                exhaust_radius_m=0.030, exhaust_channels=2, exhaust_openness=0.82,
                muffler_volume_m3=0.0024, valvetrain="ohv", valves_per_cyl=2,
                gear_ratios=[2.97, 2.07, 1.43, 1.0, 0.71, 0.57], final_drive=3.09,
                vehicle_mass=1880.0, wheel_radius=0.34, clutch_capacity=650.0,
                gearbox_type="manual")


def dodge_viper_gts() -> Engine:
    """Dodge SRT Viper GTS — 8.4 NA OHV V10 — a colossal, torquey, ten-cylinder rumble."""
    return _vee("Dodge SRT Viper GTS 8.4 V10", 10, 103.0, 100.6, 168.0, 10.4, 45.0, _FO_V10,
                flywheel_inertia=0.4, redline_rpm=6200, idle_rpm=600,
                heat_release_k=3.4, ve_width_frac=0.7, closed_map_fraction=0.13,
                exhaust_tone=54.0, exhaust_primary_m=0.62, exhaust_total_m=2.0,
                exhaust_radius_m=0.030, exhaust_channels=2, exhaust_openness=0.84,
                muffler_volume_m3=0.0022, valvetrain="ohv", valves_per_cyl=2,
                gear_ratios=[2.66, 1.78, 1.3, 1.0, 0.74, 0.5], final_drive=3.55,
                vehicle_mass=1520.0, wheel_radius=0.34, clutch_capacity=900.0,
                gearbox_type="manual")


def fd_viper_srt10() -> Engine:
    """Formula Drift Viper SRT10 — 8.4 V10, opened-up and screaming for drift."""
    return _vee("Formula Drift Viper SRT10 8.4 V10", 10, 103.0, 100.6, 168.0, 11.0, 45.0,
                _FO_V10, flywheel_inertia=0.3, redline_rpm=6800, idle_rpm=800,
                heat_release_k=3.6, ve_width_frac=0.66, closed_map_fraction=0.12,
                exhaust_tone=60.0, exhaust_primary_m=0.5, exhaust_total_m=1.6,
                exhaust_radius_m=0.026, exhaust_channels=2, exhaust_openness=0.96,
                muffler_volume_m3=0.0008, valvetrain="ohv", valves_per_cyl=2,
                has_cat=False, straight_cut=True, gear_grain=0.25,
                gear_ratios=[2.66, 1.78, 1.3, 1.0, 0.74], final_drive=4.1,
                vehicle_mass=1300.0, wheel_radius=0.34, clutch_capacity=900.0,
                gearbox_type="manual")


def ford_mustang_rtr() -> Engine:
    """Ford Mustang RTR — Coyote 5.0 cross-plane V8, cammed and opened up."""
    return _vee("Ford Mustang RTR Coyote 5.0 V8", 8, 93.0, 92.7, 151.0, 11.0, 45.0, _FO_V8_X,
                flywheel_inertia=0.26, redline_rpm=7500, idle_rpm=720,
                heat_release_k=3.5, ve_peak_frac=0.66, ve_width_frac=0.66,
                closed_map_fraction=0.13, exhaust_tone=64.0, exhaust_primary_m=0.56,
                exhaust_total_m=1.8, exhaust_radius_m=0.028, exhaust_channels=2,
                exhaust_openness=0.9, muffler_volume_m3=0.0014, has_cat=False,
                gear_ratios=[3.66, 2.43, 1.69, 1.32, 1.0, 0.65], final_drive=3.73,
                vehicle_mass=1650.0, wheel_radius=0.34, clutch_capacity=620.0,
                gearbox_type="manual")


def fd_nissan_370z() -> Engine:
    """Formula Drift Nissan 370Z — VQ37VHR 3.7 V6, high-strung drift screamer."""
    return _vee("Formula Drift Nissan 370Z VQ37 3.7 V6", 6, 95.5, 86.0, 147.0, 11.0, 30.0,
                _FO_V6, flywheel_inertia=0.15, redline_rpm=7800, idle_rpm=800,
                heat_release_k=3.5, ve_peak_frac=0.66, ve_width_frac=0.62,
                closed_map_fraction=0.13, exhaust_tone=100.0, exhaust_primary_m=0.5,
                exhaust_total_m=1.7, exhaust_radius_m=0.024, exhaust_channels=2,
                exhaust_openness=0.92, muffler_volume_m3=0.001, has_cat=False,
                straight_cut=True, gear_grain=0.2,
                gear_ratios=[3.79, 2.32, 1.62, 1.27, 1.0, 0.79], final_drive=3.69,
                vehicle_mass=1400.0, wheel_radius=0.33, clutch_capacity=480.0,
                gearbox_type="manual")


def chevy_ss_ls3() -> Engine:
    """Chevrolet SS — LS3 6.2 cross-plane OHV V8 — smooth, deep American sedan muscle."""
    return _vee("Chevrolet SS LS3 6.2 V8", 8, 103.25, 92.0, 154.0, 10.7, 45.0, _FO_V8_X,
                flywheel_inertia=0.32, redline_rpm=6600, idle_rpm=620,
                heat_release_k=3.3, ve_width_frac=0.68, closed_map_fraction=0.14,
                exhaust_tone=60.0, exhaust_primary_m=0.6, exhaust_total_m=2.1,
                exhaust_radius_m=0.029, exhaust_channels=2, exhaust_openness=0.78,
                muffler_volume_m3=0.0026, valvetrain="ohv", valves_per_cyl=2,
                gear_ratios=[2.97, 2.07, 1.43, 1.0, 0.71, 0.57], final_drive=3.27,
                vehicle_mass=1840.0, wheel_radius=0.34, clutch_capacity=700.0,
                gearbox_type="manual")


def nissan_r390_gt1() -> Engine:
    """Nissan R390 GT1 — VRH35 3.5 twin-turbo V8 Le Mans racer — aggressive race howl."""
    return _vee("Nissan R390 GT1 VRH35 3.5TT V8", 8, 85.0, 77.0, 145.0, 9.0, 45.0, _FO_V8_FLAT,
                flywheel_inertia=0.16, redline_rpm=8000, idle_rpm=1000,
                heat_release_k=3.5, ve_peak_frac=0.74, ve_width_frac=0.58,
                closed_map_fraction=0.12, exhaust_tone=110.0, exhaust_primary_m=0.45,
                exhaust_total_m=1.5, exhaust_radius_m=0.021, exhaust_channels=2,
                exhaust_openness=0.94, muffler_volume_m3=0.0008, induction="turbo",
                boost_bar=1.3, turbo_lag=0.3, has_cat=False, straight_cut=True,
                wall_material="titanium", gear_grain=0.3,
                gear_ratios=[2.6, 1.85, 1.45, 1.2, 1.0, 0.85], final_drive=3.3,
                vehicle_mass=1100.0, wheel_radius=0.33, clutch_capacity=680.0,
                gearbox_type="single")


def mercedes_w154() -> Engine:
    """Mercedes-Benz W154 — M163 3.0 supercharged 60-deg V12 (1938 GP) — vintage whine."""
    return _vee("Mercedes-Benz W154 M163 3.0 SC V12", 12, 67.0, 70.0, 130.0, 6.5, 30.0,
                _FO_V12, flywheel_inertia=0.26, redline_rpm=8000, idle_rpm=900,
                heat_release_k=3.6, ve_width_frac=0.6, closed_map_fraction=0.12,
                exhaust_tone=120.0, exhaust_primary_m=0.4, exhaust_total_m=1.3,
                exhaust_radius_m=0.020, exhaust_channels=2, exhaust_openness=0.96,
                muffler_volume_m3=0.0006, has_cat=False, straight_cut=True,
                induction="centrifugal", boost_bar=0.7, blower_ratio=9.0, gear_grain=0.3,
                gear_ratios=[2.6, 1.8, 1.4, 1.0, 0.8], final_drive=2.5,
                vehicle_mass=980.0, wheel_radius=0.34, clutch_capacity=600.0,
                gearbox_type="manual")


def ford_raptor_ecoboost() -> Engine:
    """Ford F-150 Raptor — 3.5 EcoBoost twin-turbo V6 — a boosty, deep-ish truck V6."""
    return _vee("Ford F-150 Raptor 3.5 EcoBoost TT V6", 6, 92.5, 86.7, 152.0, 10.0, 30.0,
                _FO_V6, flywheel_inertia=0.3, redline_rpm=6000, idle_rpm=650,
                heat_release_k=3.2, ve_width_frac=0.7, closed_map_fraction=0.17,
                exhaust_tone=76.0, exhaust_primary_m=0.6, exhaust_total_m=2.4,
                exhaust_radius_m=0.030, exhaust_channels=2, exhaust_openness=0.6,
                muffler_volume_m3=0.005, induction="turbo", boost_bar=1.0, turbo_lag=0.35,
                gear_ratios=[4.69, 3.31, 2.1, 1.52, 1.14, 0.86, 0.69, 0.64, 0.55, 0.43],
                final_drive=3.55, vehicle_mass=2540.0, wheel_radius=0.42,
                clutch_capacity=900.0, gearbox_type="at")


def ford_f450_powerstroke() -> Engine:
    """Ford F-450 Super Duty — Power Stroke 6.7 twin-turbo V8 DIESEL — clattery torque."""
    return _vee("Ford F-450 Power Stroke 6.7 TT V8 diesel", 8, 99.0, 108.0, 175.0, 16.2,
                45.0, _FO_V8_X, flywheel_inertia=1.4, redline_rpm=3500, idle_rpm=650,
                heat_release_k=4.7, ve_peak_frac=0.5, ve_width_frac=0.7,
                closed_map_fraction=0.2, friction_static=24.0, starter_torque=700.0,
                starter_speed_rpm=240.0, exhaust_tone=48.0, exhaust_primary_m=0.7,
                exhaust_total_m=2.8, exhaust_radius_m=0.040, exhaust_channels=2,
                exhaust_openness=0.6, muffler_volume_m3=0.012, valvetrain="ohv",
                valves_per_cyl=4, has_cat=False, induction="turbo", boost_bar=1.8,
                turbo_lag=0.7, backpressure_coupling=0.65,
                gear_ratios=[3.97, 2.32, 1.52, 1.15, 0.86, 0.69, 0.63, 0.45, 0.39, 0.32],
                final_drive=3.55, vehicle_mass=4500.0, wheel_radius=0.45,
                clutch_capacity=2200.0, gearbox_type="at")


def nissan_titan_warrior() -> Engine:
    """Nissan Titan Warrior — 5.0 Cummins twin-turbo V8 DIESEL — a deep oil-burner V8."""
    return _vee("Nissan Titan Warrior 5.0 Cummins TT V8 diesel", 8, 100.0, 99.0, 165.0, 16.5,
                45.0, _FO_V8_X, flywheel_inertia=1.1, redline_rpm=4000, idle_rpm=650,
                heat_release_k=4.6, ve_peak_frac=0.52, ve_width_frac=0.7,
                closed_map_fraction=0.2, friction_static=20.0, starter_torque=600.0,
                starter_speed_rpm=240.0, exhaust_tone=50.0, exhaust_primary_m=0.65,
                exhaust_total_m=2.6, exhaust_radius_m=0.036, exhaust_channels=2,
                exhaust_openness=0.62, muffler_volume_m3=0.01, valvetrain="ohv",
                valves_per_cyl=4, has_cat=False, induction="turbo", boost_bar=1.5,
                turbo_lag=0.7, backpressure_coupling=0.65,
                gear_ratios=[3.83, 2.36, 1.52, 1.15, 0.86, 0.69, 0.58], final_drive=3.69,
                vehicle_mass=3200.0, wheel_radius=0.43, clutch_capacity=1800.0,
                gearbox_type="at")


def mercedes_actros_race_truck() -> Engine:
    """Mercedes Tankpool Actros Racing Truck — OM471 12.8 turbo DIESEL I6 — race-truck roar."""
    return _inline("Mercedes-Benz Actros Tankpool OM471 12.8 diesel I6", 6, 132.0, 156.0,
                   255.0, 17.0, _FO_I6, flywheel_inertia=2.6, redline_rpm=2700, idle_rpm=560,
                   heat_release_k=4.8, ve_peak_frac=0.5, ve_width_frac=0.7,
                   closed_map_fraction=0.2, friction_static=30.0, starter_torque=1000.0,
                   starter_speed_rpm=200.0, exhaust_tone=46.0, exhaust_primary_m=0.85,
                   exhaust_total_m=2.6, exhaust_radius_m=0.044, exhaust_channels=1,
                   exhaust_openness=0.78, muffler_volume_m3=0.012, valvetrain="ohv",
                   valves_per_cyl=4, has_cat=False, induction="turbo", boost_bar=2.4,
                   turbo_lag=0.6, backpressure_coupling=0.7,
                   gear_ratios=[14.9, 11.6, 9.0, 7.0, 5.5, 4.3, 3.4, 2.6, 2.05, 1.6, 1.25, 1.0],
                   final_drive=2.6, vehicle_mass=5500.0, wheel_radius=0.52,
                   clutch_capacity=4000.0, gearbox_type="manual")


def volvo_iron_knight() -> Engine:
    """Volvo Iron Knight — D13 12.8 twin-turbo DIESEL I6 (record truck) — 2400 hp brute."""
    return _inline("Volvo Iron Knight D13 12.8 TT diesel I6", 6, 131.0, 158.0, 255.0, 17.0,
                   _FO_I6, flywheel_inertia=2.4, redline_rpm=2500, idle_rpm=560,
                   heat_release_k=5.0, ve_peak_frac=0.5, ve_width_frac=0.72,
                   closed_map_fraction=0.2, friction_static=30.0, starter_torque=1100.0,
                   starter_speed_rpm=200.0, exhaust_tone=44.0, exhaust_primary_m=0.85,
                   exhaust_total_m=2.4, exhaust_radius_m=0.046, exhaust_channels=1,
                   exhaust_openness=0.85, muffler_volume_m3=0.008, valvetrain="ohv",
                   valves_per_cyl=4, has_cat=False, induction="turbo", boost_bar=3.0,
                   turbo_lag=0.6, backpressure_coupling=0.7,
                   gear_ratios=[6.0, 4.3, 3.1, 2.3, 1.7, 1.25, 1.0, 0.78], final_drive=2.6,
                   vehicle_mass=4500.0, wheel_radius=0.52, clutch_capacity=5000.0,
                   gearbox_type="manual")


def hot_wheels_bone_shaker() -> Engine:
    """Hot Wheels Bone Shaker — a blown big-block V8 monster — pure cartoon thunder."""
    return _vee("Hot Wheels Bone Shaker blown 7.0 V8", 8, 110.0, 92.0, 160.0, 9.0, 45.0,
                _FO_V8_X, flywheel_inertia=0.4, redline_rpm=6500, idle_rpm=750,
                heat_release_k=3.7, ve_width_frac=0.66, closed_map_fraction=0.12,
                exhaust_tone=54.0, exhaust_primary_m=0.55, exhaust_total_m=1.4,
                exhaust_radius_m=0.032, exhaust_channels=2, exhaust_openness=0.97,
                muffler_volume_m3=0.0006, valvetrain="ohv", valves_per_cyl=2,
                has_cat=False, induction="roots", boost_bar=1.0, blower_ratio=10.0,
                gear_ratios=[2.48, 1.48, 1.0], final_drive=4.1, vehicle_mass=1500.0,
                wheel_radius=0.45, clutch_capacity=1000.0, gearbox_type="manual")


def toyota_t100_baja() -> Engine:
    """Toyota T100 Baja — a high-revving NA race V8 trophy truck — wide-open desert howl."""
    return _vee("Toyota T100 Baja race 5.0 V8", 8, 100.0, 80.0, 150.0, 11.5, 45.0, _FO_V8_FLAT,
                flywheel_inertia=0.22, redline_rpm=8200, idle_rpm=1000,
                heat_release_k=3.6, ve_peak_frac=0.74, ve_width_frac=0.58,
                closed_map_fraction=0.11, exhaust_tone=120.0, exhaust_primary_m=0.45,
                exhaust_total_m=1.4, exhaust_radius_m=0.022, exhaust_channels=2,
                exhaust_openness=0.97, muffler_volume_m3=0.0006, has_cat=False,
                straight_cut=True, wall_material="titanium", gear_grain=0.3,
                gear_ratios=[2.8, 1.9, 1.4, 1.1, 0.9], final_drive=4.3,
                vehicle_mass=1800.0, wheel_radius=0.46, clutch_capacity=720.0,
                gearbox_type="manual")


def hoonitruck_f150() -> Engine:
    """Hoonigan Hoonitruck (Ford F-150) — 3.5 EcoBoost twin-turbo V6, anti-lag monster."""
    return _vee("Hoonigan Hoonitruck 3.5 EcoBoost TT V6", 6, 92.5, 86.7, 152.0, 9.0, 30.0,
                _FO_V6, flywheel_inertia=0.2, redline_rpm=7000, idle_rpm=900,
                heat_release_k=3.5, ve_width_frac=0.66, closed_map_fraction=0.16,
                exhaust_tone=88.0, exhaust_primary_m=0.5, exhaust_total_m=1.7,
                exhaust_radius_m=0.027, exhaust_channels=2, exhaust_openness=0.9,
                muffler_volume_m3=0.0009, has_cat=False, induction="turbo", boost_bar=2.4,
                turbo_lag=0.4, turbo_spool_frac=0.14, anti_lag=True, bov_flutter=True,
                straight_cut=True, gear_ratios=[2.6, 1.85, 1.4, 1.1, 0.9],
                final_drive=3.73, vehicle_mass=1600.0, wheel_radius=0.36,
                clutch_capacity=900.0, gearbox_type="manual")


def funco_f9_buggy() -> Engine:
    """Funco F9 — an LS-based NA V8 off-road buggy — open, raucous, desert V8."""
    return _vee("Funco F9 LS 6.2 V8 buggy", 8, 103.25, 92.0, 154.0, 11.0, 45.0, _FO_V8_X,
                flywheel_inertia=0.28, redline_rpm=7000, idle_rpm=720,
                heat_release_k=3.5, ve_width_frac=0.66, closed_map_fraction=0.12,
                exhaust_tone=62.0, exhaust_primary_m=0.5, exhaust_total_m=1.5,
                exhaust_radius_m=0.028, exhaust_channels=2, exhaust_openness=0.95,
                muffler_volume_m3=0.0008, valvetrain="ohv", valves_per_cyl=2,
                has_cat=False, gear_ratios=[2.48, 1.48, 1.0], final_drive=4.86,
                vehicle_mass=1450.0, wheel_radius=0.46, clutch_capacity=720.0,
                gearbox_type="manual")


def rj_anderson_pro2() -> Engine:
    """RJ Anderson Pro 2 Truck — a 900-hp NA race V8 short-course truck — open and angry."""
    return _vee("RJ Anderson Pro 2 race 6.2 V8", 8, 103.0, 88.0, 152.0, 12.0, 45.0, _FO_V8_X,
                flywheel_inertia=0.24, redline_rpm=7600, idle_rpm=900,
                heat_release_k=3.6, ve_peak_frac=0.7, ve_width_frac=0.6,
                closed_map_fraction=0.11, exhaust_tone=68.0, exhaust_primary_m=0.45,
                exhaust_total_m=1.4, exhaust_radius_m=0.024, exhaust_channels=2,
                exhaust_openness=0.97, muffler_volume_m3=0.0006, valvetrain="ohv",
                valves_per_cyl=2, has_cat=False, straight_cut=True, gear_grain=0.2,
                gear_ratios=[2.6, 1.85, 1.4, 1.1, 0.9], final_drive=4.5,
                vehicle_mass=1450.0, wheel_radius=0.46, clutch_capacity=720.0,
                gearbox_type="manual")


def vw_bora_vr5() -> Engine:
    """VW Bora VR5 — AQN 2.3 narrow-angle (15-deg) VR5 — the offbeat, fluttery five."""
    return _vee("Volkswagen Bora VR5 AQN 2.3 VR5", 5, 81.0, 90.3, 164.0, 10.0, 7.5,
                [1, 2, 4, 5, 3], flywheel_inertia=0.18, redline_rpm=6500, idle_rpm=800,
                heat_release_k=3.3, ve_peak_frac=0.55, closed_map_fraction=0.15,
                exhaust_tone=78.0, exhaust_primary_m=0.5, exhaust_total_m=1.9,
                exhaust_radius_m=0.025, exhaust_channels=1, exhaust_openness=0.7,
                muffler_volume_m3=0.0024, header_unequal_deg=14.0,
                backpressure_coupling=0.78, gear_ratios=[3.30, 1.94, 1.31, 1.03, 0.84],
                final_drive=3.65, vehicle_mass=1320.0, wheel_radius=0.30,
                clutch_capacity=320.0, gearbox_type="manual")


def vw_golf_gti_vr6_mk3() -> Engine:
    """VW Golf VR6 (MK3) — AAA 2.8 narrow-angle (15-deg) VR6 — a warbly, compact six."""
    return _vee("Volkswagen Golf VR6 (MK3) AAA 2.8 VR6", 6, 81.0, 90.3, 164.0, 10.0,
                7.5, _FO_I6, flywheel_inertia=0.2, redline_rpm=6500, idle_rpm=780,
                heat_release_k=3.3, ve_peak_frac=0.55, closed_map_fraction=0.15,
                exhaust_tone=82.0, exhaust_primary_m=0.5, exhaust_total_m=1.9,
                exhaust_radius_m=0.026, exhaust_channels=1, exhaust_openness=0.72,
                muffler_volume_m3=0.0026, header_unequal_deg=12.0,
                backpressure_coupling=0.75, gear_ratios=[3.78, 2.12, 1.46, 1.03, 0.84],
                final_drive=3.39, vehicle_mass=1280.0, wheel_radius=0.31,
                clutch_capacity=360.0, gearbox_type="manual")


def vw_corrado_vr6() -> Engine:
    """VW Corrado VR6 — ABV 2.9 narrow-angle VR6 — the smooth, deep, warbling six."""
    return _vee("Volkswagen Corrado VR6 ABV 2.9 VR6", 6, 82.0, 90.3, 164.0, 10.0, 7.5,
                _FO_I6, flywheel_inertia=0.2, redline_rpm=6400, idle_rpm=780,
                heat_release_k=3.3, ve_peak_frac=0.55, closed_map_fraction=0.15,
                exhaust_tone=80.0, exhaust_primary_m=0.5, exhaust_total_m=1.95,
                exhaust_radius_m=0.026, exhaust_channels=1, exhaust_openness=0.74,
                muffler_volume_m3=0.0024, header_unequal_deg=12.0,
                backpressure_coupling=0.75, gear_ratios=[3.78, 2.12, 1.46, 1.03, 0.84],
                final_drive=3.68, vehicle_mass=1230.0, wheel_radius=0.30,
                clutch_capacity=340.0, gearbox_type="manual")


def bentley_supersports_w12() -> Engine:
    """Bentley Continental Supersports — 6.0 twin-turbo W12 — vast, smooth, muffled torque."""
    e = bentley_continental_w12()
    e.name = "Bentley Continental Supersports 6.0TT W12"
    e.boost_bar = 0.9
    e.redline_rpm = 6200
    e.heat_release_k = 1.95
    e.exhaust_openness = 0.6
    e.vehicle_mass = 2280.0
    return e


# ----------------------------------------------------------------- registry
# Ordered (key, label, factory).  Add a line here and the engine appears in the
# selector and on its number key — nothing else to wire up.
PRESETS = [
    ("ab500", "Abarth 500 esseesse", abarth_500_esseesse),
    ("giulia", "Giulia QV 690T V6", alfa_giulia_quadrifoglio),
    ("atomv8", "Ariel Atom V8 10500rpm", ariel_atom_v8),
    ("db11", "Aston DB11 V12", aston_martin_db11_v12),
    ("valhalla", "Aston Valhalla M178 V8", aston_valhalla_v8),
    ("valk", "Aston Valkyrie 11100rpm V12", aston_valkyrie_v12),
    ("vulcan", "Aston Vulcan 7.0 V12", aston_vulcan_v12),
    ("audiv8", "Audi 4.2 V8", audi_42_v8),
    ("rs3", "RS3 2.5 I5", audi_rs3_2024),
    ("rs5", "RS5 EA839 V6", audi_rs5_ea839),
    ("s1", "S1 Quattro I5", audi_sport_quattro_s1),
    ("conti", "Continental W12", bentley_continental_w12),
    ("bentss", "Continental Supersports W12", bentley_supersports_w12),
    ("b48", "BMW B48 I4", bmw_b48_i4),
    ("330i", "330i N53 I6", bmw_330i_n53),
    ("e36m3", "BMW M3 E36 S50 I6", bmw_m3_e36),
    ("e92m3", "E92 M3 V8", bmw_e92_m3_s65),
    ("m3gtr", "M3 GTR V8", bmw_m3_gtr_p60),
    ("0", "S58", bmw_s58),
    ("e60m5", "BMW M5 E60 S85 V10", bmw_m5_e60_v10),
    ("bmwv8", "BMW 4.4 V8", bmw_44_v8),
    ("veyron", "Veyron W16", bugatti_veyron_w16),
    ("ct5v", "CT5-V Blackwing LT4 V8", cadillac_ct5v_blackwing),
    ("z28", "'68 Camaro Z/28", chevrolet_camaro_z28_302),
    ("c7", "Corvette C7", chevrolet_c7_lt1),
    ("chevyss", "Chevrolet SS LS3 V8", chevy_ss_ls3),
    ("challenger", "Challenger R/T 392 HEMI", dodge_challenger_rt),
    ("charger", "Charger R/T 5.7 HEMI", dodge_charger_rt),
    ("8", "Hellcat", dodge_hellcat_v8),
    ("viper", "Viper GTS 8.4 V10", dodge_viper_gts),
    ("d8gto", "Donkervoort D8 GTO I5", donkervoort_d8_gto),
    ("250cal", "Ferrari 250 California V12", ferrari_250_california),
    ("4", "458", ferrari_458),
    ("488", "Ferrari 488 GTB F154 V8", ferrari_488_gtb),
    ("pista", "Ferrari 488 Pista V8", ferrari_488_pista),
    ("enzo", "Ferrari Enzo F140 V12", ferrari_enzo_v12),
    ("7", "F2004", ferrari_f2004_v10),
    ("f2007", "Ferrari F2007 V8 F1", ferrari_f2007_v8),
    ("f355", "F355 V8", ferrari_f355_v8),
    ("f40", "F40 twin-turbo V8", ferrari_f40_v8),
    ("f50gt", "Ferrari F50 GT F130 V12", ferrari_f50_gt),
    ("fxxk", "Ferrari FXX-K V12 hybrid", ferrari_fxxk),
    ("lafe", "LaFerrari V12", ferrari_laferrari_v12),
    ("sf25", "Ferrari SF-25 V6 F1", ferrari_sf25_v6_hybrid),
    ("escrs", "Escort RS Cosworth", ford_escort_cosworth),
    ("raptor", "F-150 Raptor EcoBoost V6", ford_raptor_ecoboost),
    ("f450", "F-450 Power Stroke diesel", ford_f450_powerstroke),
    ("focus3", "Focus 1.0 I3", ford_focus_ecoboost_i3),
    ("fordgt", "Ford GT V6", ford_gt_2017_v6),
    ("gt40", "Ford GT40 MK2 427 V8", ford_gt40_mk2),
    ("3", "Coyote", ford_coyote_v8),
    ("rtr", "Mustang RTR Coyote V8", ford_mustang_rtr),
    ("rs200", "Ford RS200 Evo", ford_rs200_evo),
    ("deltas4", "Lancia Delta S4 twincharge", lancia_delta_s4),
    ("gt350r", "Shelby GT350R Voodoo V8", ford_gt350r_voodoo),
    ("gt500", "Shelby GT500 Predator V8", ford_gt500_predator),
    ("fd370z", "FD 370Z VQ37 V6", fd_nissan_370z),
    ("fdviper", "FD Viper SRT10 V10", fd_viper_srt10),
    ("funco", "Funco F9 LS V8 buggy", funco_f9_buggy),
    ("wildcat", "F4F Wildcat radial", f4f_wildcat_radial),
    ("ek9", "Civic Type-R EK9 B16B", honda_civic_type_r_ek9),
    ("ep3", "Civic Type-R EP3 K20A", honda_civic_type_r_ep3),
    ("fk8", "Civic Type-R FK8 K20C1", honda_civic_type_r_fk8),
    ("nsx", "NSX NA1 V6", honda_nsx_na1),
    ("hoonrs", "Hoonigan RS200 Evo", hoonigan_rs200_evo),
    ("hoonitruck", "Hoonitruck EcoBoost V6", hoonitruck_f150),
    ("boneshaker", "Bone Shaker blown V8", hot_wheels_bone_shaker),
    ("ftype", "F-Type R 5.0 SC V8", jaguar_ftype_r_v8),
    ("xj220", "Jaguar XJ220 V6", jaguar_xj220_v6),
    ("one1", "Koenigsegg One:1 V8", koenigsegg_one1_v8),
    ("aven", "Aventador V12", lamborghini_aventador_v12),
    ("countach", "Countach LP5000 QV V12", lamborghini_countach_qv),
    ("diablo", "Diablo V12", lamborghini_diablo),
    ("hura", "Huracan V10", lamborghini_huracan_v10),
    ("6", "LP670 SV", lamborghini_murcielago),
    ("5", "LFA", lexus_lfa),
    ("gts", "GranTurismo S F136 V8", maserati_granturismo_s),
    ("787b", "787B 4-rotor", mazda_787b_rotary),
    ("rx7", "RX-7 rotary", mazda_rx7_rotary),
    ("rx7fc", "Savanna RX-7 FC 13B-T", mazda_savanna_rx7_fc),
    ("mf1", "McLaren F1 V12", mclaren_f1_v12),
    ("mp44", "McLaren MP4/4 V6 turbo", mclaren_mp44_honda_v6),
    ("p1", "McLaren P1", mclaren_p1_v8_hybrid),
    ("senna", "McLaren Senna M840 V8", mclaren_senna),
    ("a45", "A45 AMG I4", mercedes_a45_amg_i4),
    ("amggt", "AMG GT V8", mercedes_amg_gt_m178),
    ("actros", "Actros race truck diesel", mercedes_actros_race_truck),
    ("c63bs", "C63 AMG BS M156 V8", mercedes_c63_black_m156),
    ("clkgtr", "Mercedes CLK GTR V12", mercedes_clk_gtr),
    ("e63", "E63 AMG M157 V8", mercedes_e63_amg_m157),
    ("sl65", "SL65 AMG V12", mercedes_sl65_m275),
    ("w154", "Mercedes W154 SC V12", mercedes_w154),
    ("evo7", "Evo VII 4G63", mitsubishi_evo7_4g63),
    ("r35", "R35 VR38", nissan_r35_vr38),
    ("r390", "Nissan R390 GT1 V8", nissan_r390_gt1),
    ("s15", "Silvia Spec-R S15 SR20DET", nissan_silvia_s15),
    ("r34", "R34 RB26", nissan_r34_rb26),
    ("titan", "Titan Warrior Cummins V8", nissan_titan_warrior),
    ("zonda", "Zonda V12", pagani_zonda_v12),
    ("zondar", "Pagani Zonda R M120 V12", pagani_zonda_r),
    ("pete", "Peterbilt 389 diesel", peterbilt_389_diesel),
    ("p205", "Peugeot 205 T16", peugeot_205_t16),
    ("1", "911", porsche_911_h6),
    ("crs27", "Carrera RS 2.7 flat-6", porsche_carrera_rs_27),
    ("996gt1", "911 GT1 Strassen (996)", porsche_996_gt1),
    ("993gt2", "911 GT2 (993) TT", porsche_993_gt2),
    ("gt2rs", "991.2 GT2 RS 3.8TT", porsche_991_gt2_rs),
    ("gt3", "992 GT3 flat-6", porsche_992_gt3),
    ("991rs", "991.1 GT3 RS 4.0", porsche_991_gt3_rs),
    ("997rs4", "997.2 GT3 RS 4.0 Mezger", porsche_997_gt3_rs_40),
    ("930", "911 Turbo 3.3 (930)", porsche_930_turbo),
    ("917", "Porsche 917 LH flat-12", porsche_917_lh),
    ("918", "918 V8 hybrid", porsche_918_v8_hybrid),
    ("cgt", "Carrera GT V10", porsche_carrera_gt_v10),
    ("pro2", "RJ Anderson Pro 2 V8", rj_anderson_pro2),
    ("singer", "Singer DLS 4.0", singer_dls_williams_flat6),
    ("22b", "Subaru 22B", subaru_22b),
    ("gdb", "Impreza WRX STi GDB EJ207", subaru_wrx_sti_gdb),
    ("gv", "WRX STi GV EJ257", subaru_wrx_sti_gv),
    ("vt15r", "WRX STi VT15R rally", subaru_wrx_sti_vt15r),
    ("merlin", "Spitfire Merlin V12", spitfire_merlin_v12),
    ("ae86", "AE86 4A-GE", toyota_ae86_4age),
    ("9", "2JZ", toyota_2jz_supra),
    ("t100", "Toyota T100 Baja V8", toyota_t100_baja),
    ("speed12", "TVR Cerbera Speed 12", tvr_cerbera_speed12),
    ("2", "EA888", vw_ea888_i4),
    ("borav5", "Bora VR5", vw_bora_vr5),
    ("golfvr6", "Golf VR6 (MK3)", vw_golf_gti_vr6_mk3),
    ("corradovr6", "Corrado VR6", vw_corrado_vr6),
    ("ironknight", "Volvo Iron Knight diesel", volvo_iron_knight),
]

# --- display-only annotations -------------------------------------------------
# Variable-valve technology by registry key (only engines that truly have a
# branded system; blank = none shown).  VTEC / VANOS / Valvetronic / VVT-i ...
_VARIABLE_VALVE = {
    # Honda — VTEC (LIFT-switching, the audible "kick")
    "ek9": "VTEC", "ep3": "VTEC", "fk8": "VTEC", "nsx": "VTEC",
    # BMW — VANOS (phasing) / Valvetronic (continuous lift), both SMOOTH
    "e36m3": "VANOS", "e92m3": "double-VANOS", "m3gtr": "double-VANOS",
    "0": "Valvetronic", "e60m5": "double-VANOS", "b48": "Valvetronic",
    "330i": "double-VANOS", "bmwv8": "Valvetronic",          # S63
    # Toyota / Lexus — VVT-i (phasing)
    "9": "VVT-i", "5": "VVT-i",
    # Ferrari — VVT (phasing); F1 cars use pneumatic valves -> none
    "4": "F1-Trac VVT", "488": "VVT", "pista": "VVT", "lafe": "VVT",
    "enzo": "VVT", "fxxk": "VVT",
    # Audi / VW group — AVS (2-stage lift) / cam phasing
    "rs3": "AVS", "rs5": "AVS", "audiv8": "AVS", "d8gto": "AVS",
    "2": "VVT", "borav5": "VVT", "veyron": "VVT",            # EA888 / VR5 / W16
    "conti": "VVT", "bentss": "VVT",                          # Bentley W12
    # Porsche — VarioCam(+) (lift on the Plus systems)
    "gt3": "VarioCam", "1": "VarioCam", "991rs": "VarioCam",
    "gt2rs": "VarioCam", "997rs4": "VarioCam", "918": "VarioCam",
    # Nissan — CVTC(S) (phasing)
    "fd370z": "CVTCS", "s15": "VVT", "r35": "CVTCS",
    # Subaru — AVCS (phasing, intake)
    "gdb": "AVCS", "gv": "AVCS",
    # Ford — Ti-VCT (twin independent cam timing, phasing)
    "3": "Ti-VCT", "rtr": "Ti-VCT", "gt350r": "Ti-VCT", "gt500": "Ti-VCT",
    "fordgt": "Ti-VCT", "raptor": "Ti-VCT", "focus3": "Ti-VCT",
    "hoonitruck": "Ti-VCT",
    # GM — cam-phasing VVT (LS3 / older LS have NONE)
    "c7": "VVT", "ct5v": "VVT",                              # LT1 / LT4
    # Chrysler/HEMI — VVT (cam phasing); Viper V10 pushrod -> none
    "charger": "VVT", "challenger": "VVT", "8": "VVT",       # 5.7 / 6.4 / Hellcat
    # Mercedes-AMG — VVT (M139 = Camtronic 2-stage LIFT)
    "a45": "Camtronic", "amggt": "VVT", "c63bs": "VVT", "e63": "VVT",
    "valhalla": "VVT",                                       # AMG M178
    # Lamborghini — VVT (mid-2000s on; Countach/Diablo = none)
    "aven": "VVT", "hura": "VVT", "6": "VVT",
    # Maserati / Alfa (Ferrari-derived) — VVT
    "gts": "VVT", "giulia": "dual VVT",
    # Aston / Jaguar / McLaren / Koenigsegg — VVT
    "db11": "VVT", "vulcan": "VVT", "valk": "VVT", "ftype": "VVT",
    "p1": "VVT", "senna": "VVT", "one1": "VVT",
}
# Honda transverse engines famously spin the "wrong" way (CCW from the pulley).
_CCW_ROTATION = {"ek9", "ep3", "fk8", "nsx"}
# "hot vee" — exhaust + turbos inside the V valley (AMG M157/M177/M178, BMW
# S63, Ferrari F154, Audi EA839, McLaren M838T/M840 ...).
_HOT_V = {"e63", "amggt", "valhalla", "488", "pista", "rs5", "senna", "p1"}
# Turbo plumbing subtypes (display + audio).  Sequential = a small turbo spools
# first, a big one hands over up top (2JZ-GTE, RX-7 FD 13B-REW).  Twin-scroll =
# a single divided-housing turbo, tighter/cleaner whistle (BMW B48/N55, EA888).
_SEQUENTIAL_TT = {"9", "rx7"}
_TWIN_SCROLL = {"b48", "2", "evo7", "gv"}
# Parallel twin-turbo inline engines — two turbos, not one (S58, RB26DETT).
_INLINE_TWIN = {"0", "r34"}
# Single-plane "flat" crank V8 screamers; all other 90-deg V8s are cross-plane.
_FLAT_PLANE = {"4", "488", "918", "amggt", "atomv8", "e92m3", "f2007", "f355",
               "f40", "gt350r", "m3gtr", "one1", "p1", "pista", "senna",
               "valhalla"}

# Individual throttle bodies / velocity stacks / Weber-carb stacks -> the raw
# induction HOWL.  Explicit icons (incl. the turbo RB26 and the ITB BMW M V8s);
# a NA straight-cut screamer (>=8600 rpm) is auto-flagged in _annotate.  Carb
# race engines (Weber stacks) are ITB-like acoustically -> folded in there too.
_ITB = frozenset({"r34",        # RB26 twin-turbo, 6 ITBs
                  "f2004", "f2007", "mp44", "sf25",   # F1
                  "e92m3", "m3gtr",   # BMW S65 / P60 race V8, 8 ITBs
                  "cgt",        # Carrera GT 5.7 V10, 10 ITBs
                  "atomv8",     # Ariel Atom Hartley race V8
                  "ae86",       # 4A-GE 20-valve ITBs — Leo's own example
                  # --- V10 / V12 icons that genuinely run ITBs / velocity stacks
                  "5",          # Lexus LFA 1LR-GUE V10, 10 ITBs (the F1 howl)
                  "e60m5",      # BMW M5 S85 V10, 10 ITBs
                  "mf1",        # McLaren F1 BMW S70/2 V12, 12 ITBs
                  "917",        # Porsche 917 flat-12, slide throttles
                  "clkgtr",     # Mercedes CLK GTR M120 race V12
                  "speed12",    # TVR Speed 12 AJP V12
                  "countach",   # Lamborghini Countach, 6 Weber twin-chokes
                  "250cal",     # Ferrari 250 Colombo V12, triple Webers
                  "e36m3"})     # BMW M3 E36 Euro S50B30 I6, 6 ITBs
_NO_ITB = frozenset()          # exclusions from the auto straight-cut rule

# Real intake-runner lengths (m) for the V10/V12 fleet — physical MEASUREMENT
# data (short velocity-stacks on a screamer, a variable/plenum on a road exotic,
# a long torque runner on a pushrod, a big turbo plenum), NOT audio tuning.
# Drives the Helmholtz breathing (VE) and the per-cylinder induction spread, so
# these engines differentiate from geometry.  Only what Leo scoped: V10 + V12.
_INTAKE_RUNNER = {
    # V10
    "5": 0.10, "7": 0.08, "cgt": 0.12, "e60m5": 0.16, "hura": 0.24,
    "viper": 0.36, "fdviper": 0.34,
    # V12 — race / stacks (short)
    "mf1": 0.17, "clkgtr": 0.14, "917": 0.12, "valk": 0.13, "vulcan": 0.16,
    "speed12": 0.16, "zondar": 0.15, "f50gt": 0.13, "fxxk": 0.17, "w154": 0.20,
    # V12 — road exotic (variable / medium)
    "6": 0.24, "aven": 0.22, "enzo": 0.20, "lafe": 0.20, "diablo": 0.24,
    "countach": 0.22, "250cal": 0.22, "zonda": 0.26,
    # V12 — big turbo / GT (long plenum, torque)
    "sl65": 0.34, "bentss": 0.36, "conti": 0.36, "db11": 0.32,
    # aero
    "merlin": 0.50,
    # --- V8: race/high-rev NA run SHORT stacks; muscle V8s LONG torque runners;
    #     forced-induction a plenum.  (Bore-stroke/crank-plane already set.) ----
    "atomv8": 0.12, "f2007": 0.08, "e92m3": 0.15, "m3gtr": 0.14, "4": 0.16,
    "gt350r": 0.18, "gts": 0.20, "audiv8": 0.18, "f355": 0.22, "918": 0.16,
    "pro2": 0.20, "t100": 0.18,
    "3": 0.34, "rtr": 0.34, "c7": 0.32, "chevyss": 0.32, "challenger": 0.36,
    "charger": 0.36, "c63bs": 0.30, "gt40": 0.34, "z28": 0.32, "funco": 0.30,
    "488": 0.24, "pista": 0.24, "e63": 0.32, "bmwv8": 0.30, "senna": 0.24,
    "p1": 0.24, "valhalla": 0.26, "amggt": 0.30, "one1": 0.26, "f40": 0.28,
    "r390": 0.26, "8": 0.34, "gt500": 0.32, "ct5v": 0.32, "ftype": 0.32,
    "boneshaker": 0.34, "f450": 0.42, "titan": 0.42,
    # --- I6: RB26/S50 short ITB stacks, 2JZ/N53 plenum, big-diesel truck long --
    "r34": 0.15, "e36m3": 0.16, "9": 0.28, "330i": 0.30, "0": 0.26,
    "actros": 0.50, "ironknight": 0.52, "pete": 0.55,
    "ae86": 0.13,          # 4A-GE 20-valve velocity stacks (Leo's example)
}

# --- detail-model lookups (audio) -------------------------------------------
_CARB = frozenset({"z28", "250cal", "countach", "crs27", "930", "gt40", "w154",
                   "boneshaker", "917", "t100", "speed12", "diablo"})
_MECH_INJ = frozenset({"7", "f2007", "mp44", "cgt", "r390", "clkgtr", "zonda",
                       "zondar", "mf1", "f50gt", "f40"})  # mech / slide-throttle race
# dual (port + direct: Ford D-4S-style Coyote, Huracan iDS)
_DUAL_INJ = frozenset({"3", "rtr", "hura"})
# direct injection (GDI/DFI/FSI).  AUDITED against real fuel systems: the Lambo
# V12s (aven/6), the Enzo F140B, the AMG M156 (c63bs) and the Ford 5.2 Voodoo/
# Predator (gt350r/gt500) are all PORT, not direct -> removed; LaFerrari's F140FE
# IS direct -> moved in; the LFA (5) is port and the Huracan (hura) is dual.
_GDI = frozenset({"2", "a45", "b48", "0", "330i", "rs3", "rs5", "d8gto", "fk8",
                  "fordgt", "raptor", "focus3", "ct5v", "c7", "lafe",
                  "amggt", "e63", "giulia", "db11",
                  "ftype", "one1", "488", "pista", "fxxk",
                  "valhalla", "p1", "senna", "gt2rs", "918", "hoonitruck"})
_NO_BALANCE = frozenset({"22b", "gdb", "gv", "vt15r", "evo7", "ae86", "s15",
                         "escrs", "rs200", "hoonrs", "deltas4", "p205", "focus3"})
_INTEGRATED_MANIFOLD = frozenset({"2", "a45", "b48", "330i", "0", "fk8", "focus3",
                                  "giulia", "raptor", "hoonitruck", "fordgt"})
_RACE_CAM = frozenset({"7", "f2007", "mp44", "atomv8", "valk", "f50gt", "speed12",
                       "clkgtr", "zondar", "r390", "cgt", "996gt1"})
_HOT_CAM = frozenset({"4", "488", "pista", "f355", "nsx", "ek9", "ep3", "fk8",
                      "gt3", "991rs", "997rs4", "lafe", "enzo", "gt350r"})

# Forced-induction TORQUE trim (boost-blended, torque-path only — see
# Engine.torque_scale).  The open-loop Otto model + boost makes some turbo/SC
# cars produce ~2x their real peak torque; these multipliers pull each back to
# its catalogue figure (auto-calibrated vs real-world peak Nm, verified to land
# within ~1% at full boost).  Off-boost behaviour and the exhaust SOUND are
# unchanged.  NA cars never appear here (no boost to trim).
_TORQUE_SCALE = {
    "488": 0.48, "930": 0.46, "22b": 0.40, "gdb": 0.47, "giulia": 0.52,
    "gt500": 0.49, "9": 0.79, "f40": 0.80, "evo7": 0.66, "ct5v": 0.53,
    "e63": 0.41, "db11": 0.64, "pista": 0.49, "senna": 0.50, "one1": 0.50,
    "gt2rs": 0.49,
}


def _annotate(key, eng):
    """Stamp display-only spec metadata (variable-valve tech, rotation) onto eng."""
    if key in _TORQUE_SCALE:
        eng.torque_scale = _TORQUE_SCALE[key]
    if not eng.variable_valve and key in _VARIABLE_VALVE:
        eng.variable_valve = _VARIABLE_VALVE[key]
    if key in _CCW_ROTATION:
        eng.rotation = "CCW"
    if key in _HOT_V:
        eng.hot_v = True
    if not eng.induction_subtype and eng.induction == "turbo":
        if key in _SEQUENTIAL_TT:
            eng.induction_subtype = "sequential"
        elif key in _TWIN_SCROLL:
            eng.induction_subtype = "twin_scroll"
        elif key in _INLINE_TWIN:
            eng.induction_subtype = "twin"
    # --- exhaust hardware (audio) ------------------------------------------------
    # Spread the exhaust OPENNESS around the fleet mean so genuinely different
    # exhaust HARDWARE finally sounds different: a track/straight-cut car ends up
    # clearly open+loud, a stock/luxury car clearly restrictive+muffled.  This is
    # a monotonic stretch of the real per-car values (preserves each preset's
    # intent, just widens the contrast) — the within-class differentiator that
    # engine physics alone can't give (two 2.0T I4s are physically alike, but one
    # may wear a loud sports exhaust and the other a quiet stock box).
    # Individual throttle bodies (raw induction howl).  Flag the icons that
    # actually run ITBs / velocity stacks (RB26 even though it's turbo, the ITB
    # BMW M V8/V10, F1...), plus a NA race engine on straight-cut gears with a
    # screaming redline, which almost always does too.
    if not eng.individual_throttle:
        eng.individual_throttle = (
            key in _ITB
            or (key not in _NO_ITB and eng.induction in ("na", "")
                and eng.redline_rpm >= 8600 and eng.straight_cut))
    _op = eng.exhaust_openness
    if _op > 0.0:
        eng.exhaust_openness = min(max(0.66 + (_op - 0.66) * 1.5, 0.30), 0.98)
    # V6 / flat-6 bank character: two banks of three fire 240 deg apart, giving a
    # per-bank 'triple' beat — the V6 WARBLE / Porsche-boxer BURBLE that a
    # single-bank inline-6 (smooth even 120 deg) does not have.  In a mono mix the
    # two banks recombine to a smooth I6 note unless they're asymmetric, so give a
    # dual-exhaust V6/flat-6 a modest bank offset (physically the two collectors
    # differ) so the layout is AUDIBLE: I6 smooth vs V6/flat-6 warbly (why r34
    # and r35 used to sound alike).  I6s stay single-bank/smooth.
    if (eng.num_cylinders == 6 and eng.exhaust_channels >= 2
            and eng.header_unequal_deg == 0.0
            and len({round(c.bank_angle_deg, 0) for c in eng.cylinders}) >= 2):
        eng.header_unequal_deg = 14.0
    # Intake-runner length: explicit real measurement where we have it, else
    # DERIVED from intake tuning physics — short runners tune high-rpm power, long
    # ones low-rpm torque (Helmholtz); a turbo/SC feeds a longer plenum; ITB
    # velocity stacks are short.  So every engine gets a physical runner, not a
    # flat default, and the sound differentiates from it (VE + induction spread).
    if key in _INTAKE_RUNNER:
        eng.intake_runner_m = _INTAKE_RUNNER[key]
    elif abs(eng.intake_runner_m - 0.30) < 1e-6:      # untouched default -> derive
        run = 0.46 - 0.036 * (eng.redline_rpm / 1000.0)
        if eng.induction in ("turbo", "roots", "centrifugal"):
            run += 0.06                                # plenum
        if eng.individual_throttle:
            run -= 0.06                                # velocity stacks
        eng.intake_runner_m = min(max(run, 0.08), 0.52)
    # Performance cars run straight-through ABSORPTIVE mufflers (open, broadband,
    # smooth); stock road cars keep the chambered REFLECTIVE box (default).
    if eng.muffler_type == "reflective" and (eng.straight_cut
            or eng.exhaust_openness >= 0.82 or eng.redline_rpm >= 8000):
        eng.muffler_type = "absorptive"
    # A corrugated flex section buzzes — common on small modern turbo cars.
    if (not eng.flex_pipe and eng.induction == "turbo"
            and eng.total_displacement * 1000.0 <= 2.6):
        eng.flex_pipe = True

    # --- detail models (audio): injection / balance shaft / valve lift /
    #     integrated manifold / cam profile.  Heuristic auto-config; defaults are
    #     NEUTRAL so anything not matched here is left unchanged. ----------------
    nc = eng.num_cylinders
    diesel = eng.cylinders[0].compression_ratio >= 14.5
    if eng.injection == "port":                       # only fill the default
        if diesel:
            eng.injection = "diesel"
        elif key in _CARB:
            eng.injection = "carb"
        elif key in _MECH_INJ:
            eng.injection = "mech"
        elif key in _DUAL_INJ:
            eng.injection = "dual"
        elif key in _GDI:
            eng.injection = "direct"
        # else stays "port" (no injector tick)
    # balance shaft: road I3/I4/90deg-V6 have one (smooth); raw race/old ones don't
    if nc in (3, 4) and not eng.is_rotary:
        eng.balance_shaft = key not in _NO_BALANCE
    # valve LIFT mechanism, from the VVT tech
    vv = eng.variable_valve
    if any(k in vv for k in ("VTEC", "VVTL", "MIVEC", "AVS", "VarioCam", "Camtronic")):
        eng.valve_lift = "two-stage"
    elif "Valvetronic" in vv or "MultiAir" in vv:
        eng.valve_lift = "continuous"
    # integrated (in-head) exhaust manifold — modern turbo fours
    if key in _INTEGRATED_MANIFOLD:
        eng.integrated_manifold = True
    # cam profile — race screamers are lumpy + raspy, luxo/diesel are mild
    if eng.cam_profile == "stock":
        if eng.redline_rpm >= 9000 or key in _RACE_CAM:
            eng.cam_profile = "race"
        elif key in _HOT_CAM or (eng.induction == "na" and eng.redline_rpm >= 7800):
            eng.cam_profile = "hot"
        elif diesel or eng.redline_rpm <= 5600:
            eng.cam_profile = "mild"
    # crank plane (display) for V8s: the screamers (Ferrari/McLaren/AMG GT/S65/
    # Voodoo ...) run a single-plane FLAT crank; every other 90-deg V8 is the
    # two-plane CROSS crank that gives the burble.
    if not eng.crank_plane and eng.num_cylinders == 8 and not eng.is_rotary:
        banks = {round(c.bank_angle_deg, 1) for c in eng.cylinders}
        if len(banks) >= 2:
            eng.crank_plane = "flat" if key in _FLAT_PLANE else "cross"
    _apply_crank_plane(eng)
    return eng


def _apply_crank_plane(eng):
    """Make the V8 crank plane AUDIBLE (it was display-only).  A 90-deg V8's
    GLOBAL firing is even every 90 deg either way (so crank torque is unchanged),
    but the per-BANK exhaust timing differs:
      * FLAT-plane  — each bank fires evenly (180 deg apart), like two inline-4s:
        the smooth, high Ferrari/Voodoo SCREAM.
      * CROSS-plane — each bank fires UNEVENLY (90-180-270-180): the lumpy
        American-V8 'potato-potato' BURBLE, heard because the banks exhaust
        separately (exhaust_channels=2).
    We reassign the two banks' cycle offsets accordingly (same even global set,
    so torque is untouched); the per-bank waveguides then voice the difference.
    Without this every V8 sounded like a generic even-firing V8."""
    if eng.num_cylinders != 8 or eng.is_rotary or not eng.crank_plane:
        return
    a = [i for i, c in enumerate(eng.cylinders) if c.bank_angle_deg < 0]
    b = [i for i, c in enumerate(eng.cylinders) if c.bank_angle_deg >= 0]
    if len(a) != 4 or len(b) != 4:
        return                                     # not a standard 2-bank V8
    if eng.crank_plane == "cross":
        off_a, off_b = [0.0, 90.0, 270.0, 540.0], [180.0, 360.0, 450.0, 630.0]
        # In a mono mix the even GLOBAL firing would recombine the two banks
        # back to smooth, cancelling the crank-plane character.  A cross-plane
        # V8 with a dual (bank-separate) exhaust is physically ASYMMETRIC — the
        # two bank collectors reach the tail by different routes — so the uneven
        # bank pulse train survives to the ear as the potato-potato BURBLE.  Give
        # it a modest default bank offset (only if the preset hasn't set one).
        # Flat-plane V8s stay symmetric (header_unequal_deg = 0) -> smooth scream.
        if eng.exhaust_channels >= 2 and eng.header_unequal_deg == 0.0:
            eng.header_unequal_deg = 18.0
    else:                                          # flat
        off_a, off_b = [0.0, 180.0, 360.0, 540.0], [90.0, 270.0, 450.0, 630.0]
    for slot, i in enumerate(a):
        eng.cylinders[i].cycle_offset_deg = off_a[slot]
    for slot, i in enumerate(b):
        eng.cylinders[i].cycle_offset_deg = off_b[slot]


def _wrap(key, factory):
    def build():
        return _annotate(key, factory())
    return build


# Re-label every entry to its full rule-conforming engine name (车厂 车型 代号),
# wrap factories with the annotator, and sort the registry alphabetically.
_RAW_PRESETS = PRESETS
PRESETS = sorted(
    ((key, factory().name, _wrap(key, factory)) for key, _label, factory in _RAW_PRESETS),
    key=lambda t: t[1].lower(),
)

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
