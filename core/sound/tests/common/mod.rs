//! Engines for the tests.  The head geometry follows the rules of the Python
//! bake (engine_sim/core_spec.py): valve sizes from the bore, lift 0.27 d,
//! curtain-area flow that saturates near full lift.

#![allow(dead_code)]

use es_sound::{Control, Engine, EngineSpec, ValveCurve};

pub const P_ATM: f64 = 101_325.0;

/// Lift over full lift, x in [-1, 1] across the valve's open period:
/// (1 - x^2)^1.5.  Its fullness (mean lift over peak) is 0.59 and it is at
/// half lift for 0.61 of its duration -- what production cams measure.
pub fn lift(x: f64) -> f64 {
    (1.0 - x * x).max(0.0).powf(1.5)
}

/// Effective-area table of a valve pair: `n_valves` valves of diameter `d`,
/// discharge coefficient `cd`, open for `dur` degrees centred on `centre`.
pub fn cam(centre: f64, dur: f64, d: f64, n_valves: f64, cd: f64) -> ValveCurve {
    let max_lift = 0.27 * d;
    let a_max = n_valves * core::f64::consts::PI * d * max_lift * cd;
    let n = (2.0 * dur) as usize + 1;
    let mut area = Vec::with_capacity(n);
    for i in 0..n {
        let x = -1.0 + 2.0 * i as f64 / (n - 1) as f64;
        let u = lift(x);
        area.push(a_max * (1.25 * u - 0.25 * u * u).min(1.0));
    }
    area[0] = 0.0;
    area[n - 1] = 0.0;
    ValveCurve { open_deg: (centre - 0.5 * dur).rem_euclid(720.0), span_deg: dur, area }
}

/// A four-valve head on a bore, with an intake cam of `dur_i` degrees.
pub fn head(bore: f64, dur_i: f64) -> (ValveCurve, ValveCurve) {
    let d_iv = 0.39 * bore;
    let d_ev = 0.83 * d_iv;
    let dur_e = dur_i + 4.0;
    let intake = cam(360.0 + 0.5 * dur_i - 15.0, dur_i, d_iv, 2.0, 0.60);
    let exhaust = cam(360.0 - 0.5 * dur_e + 10.0, dur_e, d_ev, 2.0, 0.62);
    (exhaust, intake)
}

/// Audi A3 35 TFSI, EA211 evo 1.5: 74.5 x 85.9 mm, 12.5:1, 1-3-4-2.
pub fn a3(cylinders: usize) -> EngineSpec {
    let (exhaust, intake) = head(0.0745, 250.0);
    let firing = [0.0, 540.0, 180.0, 360.0];
    EngineSpec {
        bore: 0.0745,
        stroke: 0.0859,
        rod: 0.148,
        compression: 12.5,
        firing_deg: firing[..cylinders].to_vec(),
        exhaust,
        intake,
        wall_temp: 450.0,
        heat_scale: 1.0,
        gamma_const: 0.0,
        wall_refresh_deg: 1.0,
    }
}

/// A racing head: 98 x 39.7 mm (a 2.4 litre V8's cylinder), 13:1, a long cam
/// with a great deal of overlap, on a clearance volume of 25 cc.
pub fn racing(cylinders: usize) -> EngineSpec {
    let (exhaust, intake) = head(0.098, 284.0);
    EngineSpec {
        bore: 0.098,
        stroke: 0.0397,
        rod: 0.102,
        compression: 13.0,
        firing_deg: (0..cylinders).map(|k| k as f64 * 720.0 / cylinders as f64).collect(),
        exhaust,
        intake,
        wall_temp: 500.0,
        heat_scale: 1.0,
        gamma_const: 0.0,
        wall_refresh_deg: 1.0,
    }
}

/// A full-load operating point of the A3 at `rpm` (boosted to `p_intake`).
pub fn wot(rpm: f64, p_intake: f64) -> Control {
    Control {
        rpm,
        p_intake,
        t_intake: 320.0,
        p_exhaust: P_ATM + 0.6 * (p_intake - P_ATM).max(0.0) + 5.0e3,
        t_exhaust: 1000.0,
        charge: 0.0,
        lambda: 1.0,
        ca50_deg: 10.0,
        burn_deg: 25.0,
        intake_phase_deg: 0.0,
        exhaust_phase_deg: 0.0,
        fuel_cut: 0,
    }
}

/// A light-load (cruise) operating point.
pub fn cruise(rpm: f64, p_intake: f64) -> Control {
    Control { p_exhaust: P_ATM + 2.0e3, t_exhaust: 800.0, ..wot(rpm, p_intake) }
}

/// A running engine at an operating point.
pub fn running(spec: &EngineSpec, rate: f64, c: &Control, cycles: f64) -> Engine {
    let mut e = Engine::new(spec, rate).expect("spec");
    e.force_control(c);
    e.prime(cycles);
    e
}

/// Samples in `cycles` engine cycles.
pub fn samples_per(cycles: f64, rpm: f64, rate: f64) -> usize {
    (cycles * 120.0 / rpm * rate).round() as usize
}
