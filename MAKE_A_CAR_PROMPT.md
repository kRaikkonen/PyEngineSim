# Make any car for PyEngineSim — AI prompt template

You don't have to write code. Copy **everything inside the box below** into any
capable AI assistant (ChatGPT, Claude, Gemini, …), replace `<<CAR NAME>>` with the
car you want, and it will hand you a finished `.json` engine file.

Then:
1. Save the JSON it gives you as `configs/engines/<car>.json` (next to `run.py`).
2. Launch PyEngineSim and click **Load car…**, pick your file. Done.

If the engine idles badly or feels too weak/strong, tell the same AI "it idles
too low / makes too much power" and it will adjust the two or three numbers the
template tells it to.

---

## The prompt (copy from here ⬇)

````
You are generating an engine configuration file for **PyEngineSim**, a physics
engine-sound simulator. Output ONE valid JSON object and nothing else (no prose,
no markdown fences). The car is:

    <<CAR NAME>>

STEP 1 — Research the real engine. Find (use your best knowledge):
  - layout & cylinder count (I4 / I6 / flat-6 / 90° V8 / 60° V12 / Wankel rotary …)
  - bore × stroke (mm), connecting-rod length (mm; estimate ≈1.6× stroke if unknown)
  - compression ratio, redline rpm, idle rpm
  - peak power (hp) and roughly the rpm it makes it at
  - firing order (if known)
  - induction: naturally aspirated / turbo / supercharger (roots or centrifugal)
  - valvetrain: DOHC / SOHC / OHV-pushrod, and valves per cylinder (2/4/5)
  - gearbox: dual-clutch (DCT/PDK) / single-clutch automated / torque-converter
    automatic / manual; its gear ratios and final drive if you can

STEP 2 — Emit JSON with EXACTLY this shape. **Lengths are in METRES** (divide mm
by 1000). Angles are in degrees. Omit a field only if you want its default.

{
  "name": "string shown in the app",
  "flywheel_inertia": 0.12-0.45,        // light revvy 4cyl ~0.15, big V8 ~0.42
  "redline_rpm": number,
  "idle_rpm": number,
  "idle_air_base": 0.14,                // raise toward 0.20-0.23 if it stalls/idles low
  "closed_map_fraction": 0.10-0.24,     // idle air floor; higher = higher, lopier idle
  "ve_peak_frac": 0.55-0.75,            // rpm-fraction of peak torque (NA hi-rev ~0.72)
  "ve_width_frac": 0.50-0.78,           // torque-curve width (turbo broad ~0.75)
  "heat_release_k": see STEP 3,         // THE power knob
  "friction_static": 4-11,              // small engine ~5, big ~10
  "starter_torque": 90-220,
  "exhaust_tone": 45-135,               // Hz: deep V8 ~52, screamer ~100, rotary ~135
  "exhaust_primary_m": 0.42-0.80,       // SHORT=bright/hard, LONG=deep
  "exhaust_total_m": 1.6-2.6,
  "exhaust_radius_m": 0.022-0.031,
  "exhaust_channels": 1 or 2,           // 2 = separate banks (most V engines, boxers)
  "exhaust_openness": 0.5-0.96,         // 0.6 muffled .. 0.95 open race pipe
  "muffler_volume_m3": 0.001-0.005,     // smaller = louder/harder
  "induction": "na" | "turbo" | "roots" | "centrifugal",
  "boost_bar": 0.0,                     // peak boost over atmospheric (turbo/SC only)
  "blower_ratio": 9.0,                  // roots/centrifugal only: whine pitch per rev
  "turbo_lag": 0.3-1.3,                 // turbo only: spool time (big 80s turbo ~1.3)
  "turbo_spool_frac": 0.12,             // turbo only: rpm-frac before boost (laggy ~0.42)
  "turbo_spool_width": 0.5,             // turbo only: rpm-frac to reach full boost
  "anti_lag": false,                    // rally/Group-B bangs & crackle on overrun
  "header_unequal_deg": 0.0,            // 28 = the Subaru boxer "rumble" (unequal headers)
  "valvetrain": "dohc" | "sohc" | "ohv",
  "valves_per_cyl": 2 | 4 | 5,
  "is_rotary": false,                   // true for Wankel rotary (bright "brap")
  "has_gpf": false,                     // gasoline particulate filter (muffles a lot)
  "has_cat": true,                      // catalytic converter (mild muffle)
  "gearbox_type": "dct" | "single" | "at" | "manual",
  "gear_ratios": [list of gear ratios, 1st to top],
  "final_drive": number,
  "vehicle_mass": kg,
  "wheel_radius": 0.30-0.35,            // metres
  "clutch_capacity": 300-950,           // N·m (scale with torque)
  "cylinders": [ one object PER CYLINDER, see STEP 4 ]
}

STEP 3 — Choose heat_release_k (the power knob). Pick the closest reference below
and nudge: higher k = more power; for the SAME power, smaller displacement needs
higher k. For TURBO engines, tune k so the **un-boosted** power roughly equals
the real nameplate hp — the boost then adds the extra punch in-game (so a turbo
uses a *higher* k than a same-size NA engine).

  NA   I4 1.6L 128hp ........ k 4.5      NA   flat6 4.0L 510hp ..... k 4.2
  NA   V6 3.0L 270hp ........ k 4.1      NA   V8 5.0L 415hp ........ k 4.0
  NA   V10 5.2L 610hp ....... k 3.7      NA   V12 6.5L 725hp ....... k 3.75
  NA   4-rotor 700hp ........ k 6.9      NA   V8 4.0L 414hp ........ k 4.0
  TURBO I4 2.0L 280hp ...... k 6.7      TURBO I6 3.0L 330hp ...... k 4.6
  TURBO I5 2.5L 400hp ...... k 5.7      TURBO V6 2.9L 450hp ...... k 5.2
  TURBO V8 4.0L 523hp ...... k 4.6      TURBO V12 6.0L 612hp ..... k 4.7
  ROOTS-SUPERCHARGED V8 6.2L 707hp ... k 2.5  (use induction "roots", boost ~0.8)

STEP 4 — Build the "cylinders" list (one object each). Each cylinder:

  {
    "bore": metres, "stroke": metres, "rod_length": metres,
    "compression_ratio": number,
    "cycle_offset_deg": see below,
    "bank_angle_deg": see below
  }

  cycle_offset_deg (this is what makes it fire evenly and in the right order):
    spacing = 720 / number_of_cylinders
    Number the firing order as cylinders c[0], c[1], c[2], … (1-based engine numbers).
    The k-th cylinder to fire gets offset = k * spacing  (k starting at 0).
    So if firing order is 1-3-4-2 on an I4 (spacing 180):
      cyl1 -> 0,  cyl3 -> 180,  cyl4 -> 360,  cyl2 -> 540
    and you list cylinders in numeric order 1,2,3,4 with offsets [0, 540, 180, 360].
    If you don't know the firing order, just use 0, spacing, 2*spacing, … in order.

  bank_angle_deg:
    inline / straight engine ........ 0 for every cylinder
    V engine of angle A ............. first half = -A/2, second half = +A/2
                                       (90° V8 -> -45/+45, 60° V12 -> -30/+30)
    flat / boxer .................... first half = -90, second half = +90
    rotary .......................... 0 for every "cylinder"

STEP 5 — Sanity rules before you output:
  - bore/stroke/rod_length are DECIMALS like 0.086, never 86.
  - cylinders array length == cylinder count; offsets are all different and span 0..720.
  - turbo/SC: set induction and boost_bar (>0); NA: induction "na", boost_bar 0.
  - rotary: is_rotary true, exhaust_tone high (120-135), bright/open pipe.
  - big American OHV V8: valvetrain "ohv", valves_per_cyl 2, exhaust_tone ~52-58.
  - Output ONLY the JSON object.
````

## Worked example (what a good answer looks like)

For **Honda S2000 (F20C, 2.0 L NA DOHC VTEC inline-4, 9000 rpm, ~240 hp,
firing 1-3-4-2, 6-speed manual)** the AI should produce something like:

```json
{
  "name": "Honda S2000 F20C 2.0 I4",
  "flywheel_inertia": 0.13,
  "redline_rpm": 9000,
  "idle_rpm": 850,
  "closed_map_fraction": 0.14,
  "ve_peak_frac": 0.72,
  "ve_width_frac": 0.58,
  "heat_release_k": 4.7,
  "friction_static": 5.0,
  "starter_torque": 120.0,
  "exhaust_tone": 98.0,
  "exhaust_primary_m": 0.5,
  "exhaust_total_m": 1.8,
  "exhaust_radius_m": 0.023,
  "exhaust_channels": 1,
  "exhaust_openness": 0.78,
  "muffler_volume_m3": 0.0020,
  "induction": "na",
  "valvetrain": "dohc",
  "valves_per_cyl": 4,
  "gearbox_type": "manual",
  "gear_ratios": [3.13, 2.05, 1.48, 1.16, 0.97, 0.81],
  "final_drive": 4.10,
  "vehicle_mass": 1290.0,
  "wheel_radius": 0.31,
  "clutch_capacity": 320.0,
  "cylinders": [
    {"bore": 0.087, "stroke": 0.084, "rod_length": 0.153, "compression_ratio": 11.0, "cycle_offset_deg": 0.0,   "bank_angle_deg": 0.0},
    {"bore": 0.087, "stroke": 0.084, "rod_length": 0.153, "compression_ratio": 11.0, "cycle_offset_deg": 540.0, "bank_angle_deg": 0.0},
    {"bore": 0.087, "stroke": 0.084, "rod_length": 0.153, "compression_ratio": 11.0, "cycle_offset_deg": 180.0, "bank_angle_deg": 0.0},
    {"bore": 0.087, "stroke": 0.084, "rod_length": 0.153, "compression_ratio": 11.0, "cycle_offset_deg": 360.0, "bank_angle_deg": 0.0}
  ]
}
```

That file drops straight into `configs/engines/` and loads with **Load car…**.

## Tuning hints to pass back to the AI if needed
- *Idles too low or stalls* → raise `idle_air_base` (0.17–0.23) and/or `closed_map_fraction`.
- *Idles too high* → lower `closed_map_fraction`.
- *Too much / too little power* → scale `heat_release_k` up or down by the same ratio.
- *Sounds too soft/deep* → shorten `exhaust_primary_m`, raise `exhaust_openness`,
  raise `exhaust_tone`, shrink `muffler_volume_m3`. Too harsh → the opposite.
- *Want the brutal shift kick* → `gearbox_type: "single"`. *Silky* → `"dct"`.
  *Slushy* → `"at"`.
