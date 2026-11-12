//! One exhaust event, in numbers.
//!
//!     cargo run --release --example event
//!
//! For the A3's cylinder at a handful of operating points: the state when
//! the exhaust valve opens, and the shape of what leaves -- the table of
//! docs/REDESIGN_V2.md s.6.1, from the model itself.

#[path = "../tests/common/mod.rs"]
mod common;

use common::*;
use es_sound::{Control, Engine};

struct Event {
    p_open: f64,
    t_open: f64,
    peak: f64,
    rise_ms: f64,
    width_ms: f64,
    early: f64,
    back: f64,
    mass: f64,
}

fn event(c: &Control, rate: f64) -> Event {
    let spec = a3(1);
    let mut e: Engine = running(&spec, rate, c, 10.0);
    let open = spec.exhaust.open_deg;
    while {
        let th = e.crank_deg();
        !(th > open - 3.0 && th < open - 1.0)
    } {
        e.tick();
    }
    let (p_open, t_open) = (e.cylinders()[0].p, e.cylinders()[0].t);
    let n = samples_per(1.0, c.rpm, rate) * (spec.exhaust.span_deg as usize + 6) / 720;
    let q: Vec<f64> = (0..n).map(|_| e.tick().q_exhaust).collect();
    let peak = q.iter().cloned().fold(0.0, f64::max);
    let i_peak = q.iter().position(|v| *v == peak).unwrap();
    let i10 = q.iter().position(|v| *v > 0.1 * peak).unwrap();
    let i90 = q.iter().position(|v| *v > 0.9 * peak).unwrap();
    let _ = i_peak;
    let total: f64 = q.iter().sum();
    Event {
        p_open,
        t_open,
        peak,
        rise_ms: (i90 - i10) as f64 / rate * 1e3,
        width_ms: q.iter().filter(|v| **v > 0.5 * peak).count() as f64 / rate * 1e3,
        early: q[..n / 4].iter().sum::<f64>() / total,
        back: q.iter().cloned().fold(0.0, f64::min),
        mass: total / rate,
    }
}

fn main() {
    let rate = 192_000.0;
    println!("A3 1.5 (one cylinder): the exhaust event");
    println!(
        "{:>6} {:>8} | {:>7} {:>6} | {:>8} {:>7} {:>8} {:>6} {:>8} {:>8}",
        "rpm", "MAP bar", "p_EVO", "T_EVO", "peak g/s", "rise ms", "width ms", "early", "back g/s", "mass mg"
    );
    for &rpm in &[900.0, 1500.0, 2500.0, 4000.0, 6000.0] {
        for &(map, full) in &[(0.30, false), (0.50, false), (1.0, true), (1.8, true)] {
            let c = if full { wot(rpm, map * P_ATM) } else { cruise(rpm, map * P_ATM) };
            let ev = event(&c, rate);
            println!(
                "{:>6.0} {:>8.2} | {:>7.2} {:>6.0} | {:>8.1} {:>7.2} {:>8.2} {:>6.2} {:>8.2} {:>8.1}",
                rpm,
                map,
                ev.p_open / 1e5,
                ev.t_open,
                ev.peak * 1e3,
                ev.rise_ms,
                ev.width_ms,
                ev.early,
                ev.back * 1e3,
                ev.mass * 1e6
            );
        }
    }
}
