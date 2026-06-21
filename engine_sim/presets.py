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
        name="VW/Audi EA888 2.0 TFSI (Golf R)",
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
        name="Lamborghini Murcielago LP670-4 SV 6.5L V12",
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
        name="Lamborghini Diablo 6.0 V12",
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
        name="Ferrari F2004 3.0 V10 (F1)",
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
        has_cat=False,                           # open race exhaust, no cat/GPF
        gear_ratios=[2.50, 1.95, 1.60, 1.36, 1.18, 1.04, 0.92],  # close F1 7-speed
        final_drive=4.20,
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
        name="Dodge Hellcat 6.2 supercharged V8",
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
        name="Toyota Supra 2JZ-GTE 3.0 twin-turbo I6",
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
        name="BMW S58 3.0 twin-turbo I6 (M3/M4)",
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
        name="Lamborghini Huracan 5.2 V10",
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
        name="Lamborghini Aventador 6.5 V12",
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
        name="Ferrari LaFerrari 6.3 V12",
        cylinders=cylinders,
        flywheel_inertia=0.26, redline_rpm=9000, idle_rpm=950,
        closed_map_fraction=0.14,
        heat_release_k=3.7, ve_peak_frac=0.74, ve_width_frac=0.6,
        friction_static=10.0, starter_torque=200.0,
        exhaust_tone=70.0,
        exhaust_primary_m=0.62, exhaust_total_m=2.1, exhaust_radius_m=0.026,
        exhaust_channels=2, exhaust_openness=0.92, muffler_volume_m3=0.0021,
        wall_material="titanium",        # smooth, rising, metallic 'waaang' wail
        backpressure_coupling=0.7,       # fine gear-like grain ON the smooth wail
                                         #   (no header offset -> grain, not 'cough')
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
        name="Ferrari F40 2.9 twin-turbo V8",
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
        name="Pagani Zonda 7.3 V12",
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
        name="Nissan R34 GT-R RB26DETT 2.6 twin-turbo I6",
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
        name="Nissan R35 GT-R VR38DETT 3.8 twin-turbo V6",
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
        name="Ferrari F355 3.5 V8 (5-valve)",
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
        name="Mazda 787B R26B 4-rotor (Le Mans)",
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
        name="Porsche Carrera GT 5.7 V10",
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
        name="Porsche 992 GT3 4.0 flat-six",
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
        name="Honda NSX NA1 3.0 V6 VTEC",
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
        name="Mitsubishi Evo VII 4G63T 2.0 turbo I4",
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
        name="1968 Camaro Z/28 302 V8",
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
        name="Audi Sport Quattro S1 2.1 turbo I5",
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
        name="Audi RS3 2.5 turbo I5 (EA855)",
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
        name="Audi RS5 2.9 twin-turbo V6 (EA839)",
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
        name="Mercedes SL65 AMG 6.0 twin-turbo V12",
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
        name="Mercedes-AMG GT 4.0 twin-turbo V8 (M178)",
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
        name="Singer DLS 4.0 flat-six (Williams)",
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
        name="BMW E92 M3 S65 4.0 V8",
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
        name="Porsche 918 Spyder 4.6 V8 hybrid",
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
        name="Ford GT 3.5 EcoBoost twin-turbo V6",
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
        bank = -45.0 if i < 8 else 45.0
        cylinders.append(
            Cylinder(bore=mm(86), stroke=mm(86), rod_length=mm(140),
                     compression_ratio=9.0, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Bugatti Veyron 8.0 quad-turbo W16",
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
        bank = -36.0 if i < 6 else 36.0
        cylinders.append(
            Cylinder(bore=mm(84), stroke=mm(90.2), rod_length=mm(152),
                     compression_ratio=10.5, cycle_offset_deg=offsets[i],
                     bank_angle_deg=bank))
    return Engine(
        name="Bentley Continental 6.0 twin-turbo W12",
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
        name="BMW 4.4 twin-turbo V8 (S63)",
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
        name="Audi 4.2 FSI V8 (R8)",
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
        name="McLaren P1 3.8 twin-turbo V8 hybrid",
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
        name="BMW B48 2.0 turbo I4",
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
        name="Mercedes-AMG A45 2.0 turbo I4 (M139)",
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
        name="McLaren F1 6.1 V12 (BMW S70/2)",
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


# ----------------------------------------------------------------- registry
# Ordered (key, label, factory).  Add a line here and the engine appears in the
# selector and on its number key — nothing else to wire up.
PRESETS = [
    ("1", "911",    porsche_911_h6),
    ("2", "EA888",  vw_ea888_i4),
    ("3", "Coyote", ford_coyote_v8),
    ("4", "458",    ferrari_458),
    ("5", "LFA",    lexus_lfa),
    ("6", "LP670 SV", lamborghini_murcielago),
    ("7", "F2004",  ferrari_f2004_v10),
    ("8", "Hellcat", dodge_hellcat_v8),
    ("9", "2JZ",    toyota_2jz_supra),
    ("0", "S58",    bmw_s58),
    ("rx7", "RX-7 rotary", mazda_rx7_rotary),
    ("22b", "Subaru 22B", subaru_22b),
    ("hura", "Huracan V10", lamborghini_huracan_v10),
    ("aven", "Aventador V12", lamborghini_aventador_v12),
    ("lafe", "LaFerrari V12", ferrari_laferrari_v12),
    ("f40", "F40 twin-turbo V8", ferrari_f40_v8),
    ("zonda", "Zonda V12", pagani_zonda_v12),
    ("ae86", "AE86 4A-GE", toyota_ae86_4age),
    ("r34", "R34 RB26", nissan_r34_rb26),
    ("r35", "R35 VR38", nissan_r35_vr38),
    ("f355", "F355 V8", ferrari_f355_v8),
    ("787b", "787B 4-rotor", mazda_787b_rotary),
    ("cgt", "Carrera GT V10", porsche_carrera_gt_v10),
    ("gt3", "992 GT3 flat-6", porsche_992_gt3),
    ("nsx", "NSX NA1 V6", honda_nsx_na1),
    ("evo7", "Evo VII 4G63", mitsubishi_evo7_4g63),
    ("c7", "Corvette C7", chevrolet_c7_lt1),
    ("z28", "'68 Camaro Z/28", chevrolet_camaro_z28_302),
    ("s1", "S1 Quattro I5", audi_sport_quattro_s1),
    ("rs3", "RS3 2.5 I5", audi_rs3_2024),
    ("rs5", "RS5 EA839 V6", audi_rs5_ea839),
    ("sl65", "SL65 AMG V12", mercedes_sl65_m275),
    ("amggt", "AMG GT V8", mercedes_amg_gt_m178),
    ("singer", "Singer DLS 4.0", singer_dls_williams_flat6),
    ("e92m3", "E92 M3 V8", bmw_e92_m3_s65),
    ("918", "918 V8 hybrid", porsche_918_v8_hybrid),
    ("fordgt", "Ford GT V6", ford_gt_2017_v6),
    ("veyron", "Veyron W16", bugatti_veyron_w16),
    ("conti", "Continental W12", bentley_continental_w12),
    ("bmwv8", "BMW 4.4 V8", bmw_44_v8),
    ("audiv8", "Audi 4.2 V8", audi_42_v8),
    ("p1", "McLaren P1", mclaren_p1_v8_hybrid),
    ("b48", "BMW B48 I4", bmw_b48_i4),
    ("a45", "A45 AMG I4", mercedes_a45_amg_i4),
    ("m3gtr", "M3 GTR V8", bmw_m3_gtr_p60),
    ("mf1", "McLaren F1 V12", mclaren_f1_v12),
    ("diablo", "Diablo V12", lamborghini_diablo),
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
