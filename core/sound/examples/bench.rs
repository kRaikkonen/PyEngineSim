//! What the cylinders cost.
//!
//!     cargo run --release --example bench
//!
//! Renders ten seconds of audio at 48 kHz at several operating points and
//! prints the share of one core that takes (on this machine).

#[path = "../tests/common/mod.rs"]
mod common;

use std::time::Instant;

use common::*;
use es_sound::Control;

fn share(spec: &es_sound::EngineSpec, c: &Control, z: f64) -> (f64, u32, u32) {
    let rate = 48_000.0;
    let mut e = running(spec, rate, c, 4.0);
    e.set_port_impedance(z, 0.0);
    let mut l = vec![0.0f32; 512];
    let mut r = vec![0.0f32; 512];
    let seconds = 10.0;
    let blocks = (seconds * rate / 512.0) as usize;
    let t = Instant::now();
    for _ in 0..blocks {
        e.render(&mut l, &mut r);
    }
    let took = t.elapsed().as_secs_f64();
    let k = &e.cylinders()[0];
    (took / (blocks as f64 * 512.0 / rate), k.worst_outer, k.worst_inner)
}

fn main() {
    let z = 600.0 / (core::f64::consts::FRAC_PI_4 * 0.028 * 0.028);
    println!("{:<34} {:>10} {:>10}   worst iterations", "", "plenum", "pipe");
    let rows: Vec<(&str, es_sound::EngineSpec, Control)> = vec![
        ("A3 4 cyl, 800 rpm idle", a3(4), cruise(800.0, 0.32 * P_ATM)),
        ("A3 4 cyl, 2000 rpm cruise", a3(4), cruise(2000.0, 0.45 * P_ATM)),
        ("A3 4 cyl, 6000 rpm full load", a3(4), wot(6000.0, 1.8 * P_ATM)),
        ("racing 8 cyl, 18000 rpm", racing(8), wot(18_000.0, 0.98 * P_ATM)),
        ("racing 16 cyl, 9000 rpm", racing(16), wot(9000.0, 0.98 * P_ATM)),
    ];
    for (name, spec, c) in rows {
        let (a, o1, i1) = share(&spec, &c, 0.0);
        let (b, o2, i2) = share(&spec, &c, z);
        println!(
            "{:<34} {:>9.2}% {:>9.2}%   outer {} / {}, inner {} / {}",
            name,
            a * 100.0,
            b * 100.0,
            o1,
            o2,
            i1,
            i2
        );
    }
}
