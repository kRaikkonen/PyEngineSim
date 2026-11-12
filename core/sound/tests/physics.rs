//! The cylinder model against what is known exactly, and against itself.

mod common;

use common::*;
use es_sound::gas::{COMBUSTION_EFF, R_GAS};
use es_sound::{Control, Engine, EngineSpec, ValveCurve};

fn sealed(gamma: f64) -> EngineSpec {
    EngineSpec {
        exhaust: ValveCurve::closed(),
        intake: ValveCurve::closed(),
        heat_scale: 0.0,
        gamma_const: gamma,
        ..a3(1)
    }
}

/// A sealed, adiabatic cylinder of a constant-gamma gas: p V^gamma stays put.
#[test]
fn sealed_cylinder_follows_the_adiabat() {
    for &rpm in &[800.0, 3000.0, 8000.0] {
        let mut e = Engine::new(&sealed(1.4), 48_000.0).unwrap();
        e.force_control(&Control { rpm, ..Control::default() });
        let c = &e.cylinders()[0];
        let k0 = c.p * c.v.powf(1.4);
        let mut worst: f64 = 0.0;
        for _ in 0..samples_per(2.0, rpm, 48_000.0) {
            e.tick();
            let c = &e.cylinders()[0];
            worst = worst.max((c.p * c.v.powf(1.4) / k0 - 1.0).abs());
        }
        // second order in the crank step: 1 degree a sample at 8000 rpm
        assert!(worst < 2.0e-4, "{rpm} rpm: p V^g wandered by {worst:e}");
    }
}

/// A rigid vessel emptying through a choked orifice has a closed form:
/// m(t) = m0 (1 + (g-1)/2 t/tau)^(-2/(g-1)),  tau = m0 / q0.
fn blowdown_error(rate: f64) -> f64 {
    let g = 1.4;
    let area = 3.0e-4;
    let mut table = vec![area; 64];
    table[0] = 0.0;
    table[63] = 0.0;
    let spec = EngineSpec {
        exhaust: ValveCurve { open_deg: 0.0, span_deg: 700.0, area: table },
        intake: ValveCurve::closed(),
        heat_scale: 0.0,
        gamma_const: g,
        ..a3(1)
    };
    let mut e = Engine::new(&spec, rate).unwrap();
    e.force_control(&Control { rpm: 0.0, p_exhaust: P_ATM, ..Control::default() });
    e.set_crank(180.0);
    let (p0, t0) = (10.0 * P_ATM, 1000.0);
    e.cylinder_mut(0).set_state(p0, t0, 1.0);
    let m0 = e.cylinders()[0].m;
    let psi = g.sqrt() * (2.0 / (g + 1.0f64)).powf((g + 1.0) / (2.0 * (g - 1.0)));
    let q0 = area * psi * p0 / (R_GAS * t0).sqrt();
    let tau = m0 / q0;
    // choked while p > p_out / 0.528: until t = 1.34 tau.  Compare up to 1.2 tau.
    let n = (1.2 * tau * rate) as usize;
    let mut worst: f64 = 0.0;
    for i in 1..=n {
        let s = e.tick();
        let t = i as f64 / rate;
        let m = m0 * (1.0 + 0.5 * (g - 1.0) * t / tau).powf(-2.0 / (g - 1.0));
        worst = worst.max((e.cylinders()[0].m / m - 1.0).abs());
        assert!(s.q_exhaust > 0.0);
    }
    worst
}

#[test]
fn choked_blowdown_matches_the_closed_form() {
    let e48 = blowdown_error(48_000.0);
    let e192 = blowdown_error(192_000.0);
    assert!(e48 < 5.0e-3, "48 kHz: {e48:e}");
    assert!(e192 < 1.5e-3, "192 kHz: {e192:e}");
    // first order in the time step: four times the rate, about a quarter the error
    assert!(e192 < 0.4 * e48, "no convergence: {e48:e} -> {e192:e}");
}

/// u(T) of the gas whose gamma is 1.375 - 6.99e-5 T, from 250 K.
fn u_of(t: f64) -> f64 {
    let (a, b) = (0.375, 6.99e-5);
    R_GAS / b * ((a - b * 250.0) / (a - b * t)).ln()
}

fn h_of(t: f64) -> f64 {
    u_of(t) + R_GAS * t
}

/// A running engine in a steady state: what goes in comes out, and the
/// energy book closes -- burn = work + wall heat + enthalpy carried off.
#[test]
fn steady_cycle_conserves_mass_and_energy() {
    for c in [wot(2500.0, 1.0 * P_ATM), wot(2000.0, 1.8 * P_ATM), cruise(1500.0, 0.4 * P_ATM)] {
        let rate = 48_000.0;
        let mut e = running(&a3(1), rate, &c, 10.0);
        e.cylinder_mut(0).ledger = Default::default();
        let (mut h_out, mut h_in) = (0.0, 0.0);
        let dt = 1.0 / rate;
        let n = samples_per(4.0, c.rpm, rate);
        assert_eq!(n as f64, 4.0 * 120.0 / c.rpm * rate, "pick an rpm with whole cycles");
        let u0 = {
            let k = &e.cylinders()[0];
            k.m * u_of(k.t)
        };
        for _ in 0..n {
            e.tick();
            let k = &e.cylinders()[0];
            if k.q_exhaust >= 0.0 {
                h_out += k.q_exhaust * dt * h_of(k.t);
            } else {
                h_in -= k.q_exhaust * dt * h_of(c.t_exhaust);
            }
            if k.q_intake >= 0.0 {
                h_in += k.q_intake * dt * h_of(c.t_intake);
            } else {
                h_out -= k.q_intake * dt * h_of(k.t);
            }
        }
        let k = &e.cylinders()[0];
        let l = k.ledger;
        let du = k.m * u_of(k.t) - u0;
        assert!(l.m_intake > 0.0 && l.m_exhaust > 0.0);
        let dm = (l.m_intake - l.m_exhaust) / l.m_intake;
        assert!(dm.abs() < 1.0e-6, "mass: in {} out {} ({dm:e})", l.m_intake, l.m_exhaust);
        let residual = l.burn - l.work - l.wall + h_in - h_out - du;
        assert!(
            residual.abs() < 0.01 * l.burn,
            "energy at {} rpm {:.2} bar: burn {:.1} work {:.1} wall {:.1} h_out-h_in {:.1} residual {:.2} J",
            c.rpm,
            c.p_intake / 1e5,
            l.burn,
            l.work,
            l.wall,
            h_out - h_in,
            residual
        );
    }
}

/// The indicated efficiency and mean effective pressure of the cycle the
/// model runs are those of a spark-ignition engine.
#[test]
fn the_cycle_is_an_engine() {
    let c = wot(2500.0, 1.0 * P_ATM);
    let rate = 48_000.0;
    let mut e = running(&a3(1), rate, &c, 10.0);
    e.cylinder_mut(0).ledger = Default::default();
    for _ in 0..samples_per(4.0, c.rpm, rate) {
        e.tick();
    }
    let l = e.cylinders()[0].ledger;
    let fuel_energy = l.burn / COMBUSTION_EFF;
    let eff = l.work / fuel_energy;
    let imep = l.work / 4.0 / e.geom().swept;
    let wall = l.wall / fuel_energy;
    let ve = l.m_intake / 4.0 / (c.p_intake / (R_GAS * c.t_intake) * e.geom().swept);
    println!("eff {eff:.3}  imep {:.2} bar  wall {wall:.3}  ve {ve:.3}", imep / 1e5);
    assert!((0.30..0.45).contains(&eff), "indicated efficiency {eff}");
    assert!((8.0e5..15.0e5).contains(&imep), "imep {imep}");
    assert!((0.08..0.35).contains(&wall), "wall heat share {wall}");
    assert!((0.70..1.10).contains(&ve), "volumetric efficiency {ve}");
}

/// A cylinder without fuel is an air spring: it gives back almost all the
/// work it takes, and its exhaust valve still passes the charge (the soft
/// pulse of the overrun).
#[test]
fn a_cut_cylinder_motors() {
    let c = Control { fuel_cut: 1, ..cruise(3000.0, 0.3 * P_ATM) };
    let rate = 48_000.0;
    let mut e = running(&a3(1), rate, &c, 8.0);
    e.cylinder_mut(0).ledger = Default::default();
    for _ in 0..samples_per(4.0, c.rpm, rate) {
        e.tick();
    }
    let l = e.cylinders()[0].ledger;
    assert_eq!(l.burn, 0.0);
    assert!(e.cylinders()[0].was_cut());
    assert!(l.work < 0.0, "a motored cylinder absorbs work, got {}", l.work);
    assert!(l.m_exhaust > 0.0);
}

fn render_in(chunk: usize, total: usize) -> Vec<f32> {
    let mut e = running(&a3(4), 48_000.0, &cruise(1200.0, 0.4 * P_ATM), 3.0);
    let points = [
        wot(1200.0, 1.3 * P_ATM),
        wot(3500.0, 1.8 * P_ATM),
        Control { fuel_cut: 0b1111, ..cruise(3400.0, 0.25 * P_ATM) },
        cruise(1500.0, 0.45 * P_ATM),
    ];
    let every = 6000; // not a multiple of the control block, on purpose
    let mut out_l = vec![0.0f32; total];
    let mut out_r = vec![0.0f32; total];
    let mut pos = 0;
    while pos < total {
        if pos % every == 0 {
            assert!(e.set_control(&points[(pos / every) % points.len()]));
        }
        let to_change = every - pos % every;
        let n = chunk.min(to_change).min(total - pos);
        e.render(&mut out_l[pos..pos + n], &mut out_r[pos..pos + n]);
        pos += n;
    }
    assert_eq!(out_l, out_r);
    out_l
}

/// The host's block length is not an input: 64, 256, 512, 1024 or 37 frames
/// at a time give the same samples, bit for bit -- and so does a second run.
#[test]
fn block_length_does_not_change_the_output() {
    let total = 48_000;
    let reference = render_in(512, total);
    assert!(reference.iter().all(|v| v.is_finite()));
    assert!(reference.iter().any(|v| v.abs() > 1.0e-4), "silence");
    for chunk in [512, 64, 256, 1024, 37, 1] {
        let other = render_in(chunk, total);
        let first = reference.iter().zip(&other).position(|(a, b)| a.to_bits() != b.to_bits());
        assert_eq!(first, None, "chunk {chunk} differs from 512 at sample {first:?}");
    }
}

/// How many times the slope flips sign: a smooth flow turns a few times a
/// cycle, a ringing one at every sample.
fn turns(x: &[f64]) -> usize {
    x.windows(3)
        .filter(|w| {
            let (a, b) = (w[1] - w[0], w[2] - w[1]);
            a != 0.0 && b != 0.0 && (a > 0.0) != (b > 0.0)
        })
        .count()
}

/// A racing head near top dead centre is a 25 cc volume between two open
/// valves of 10 cm^2: it equalises in a fraction of a sample.  The step must
/// not ring there, at any speed, and the answer must not depend on the rate.
#[test]
fn racing_head_overlap_is_stable() {
    let spec = racing(1);
    for c in [
        Control { ca50_deg: 12.0, burn_deg: 30.0, ..cruise(4000.0, 0.3 * P_ATM) },
        Control { ca50_deg: 10.0, burn_deg: 35.0, ..wot(18_000.0, 0.98 * P_ATM) },
        Control { fuel_cut: 1, ..cruise(12_000.0, 0.15 * P_ATM) },
    ] {
        let mut per_cycle = Vec::new();
        for rate in [48_000.0, 192_000.0] {
            let mut e = running(&spec, rate, &c, 12.0);
            e.cylinder_mut(0).ledger = Default::default();
            let n = samples_per(4.0, c.rpm, rate);
            let mut q = Vec::with_capacity(n);
            let mut gross = 0.0;
            for _ in 0..n {
                let s = e.tick();
                gross += s.q_exhaust.abs() / rate;
                let k = &e.cylinders()[0];
                assert!(k.p.is_finite() && k.t.is_finite() && k.m.is_finite());
                assert!((3.0e3..3.0e7).contains(&k.p), "{} rpm: p = {}", c.rpm, k.p);
                assert!((150.0..4000.0).contains(&k.t), "{} rpm: T = {}", c.rpm, k.t);
                q.push(s.q_exhaust);
            }
            let per = turns(&q) as f64 / 4.0;
            let it = e.cylinders()[0].worst_iterations();
            println!("{} rpm at {rate}: {per} turns a cycle, worst iterations {it}", c.rpm);
            assert!(per < 16.0, "{} rpm at {rate}: the flow rings ({per} turns a cycle)", c.rpm);
            assert!(it < 12, "{} rpm: {it} iterations", c.rpm);
            per_cycle.push(gross / 4.0);
        }
        // (on a closed throttle gas goes both ways through the overlap and the
        // net is a small difference: it is the gross flow that is compared)
        let d = (per_cycle[0] / per_cycle[1] - 1.0).abs();
        assert!(d < 0.03, "{} rpm: gross mass per cycle {:?} differs by {d}", c.rpm, per_cycle);
    }
}

/// A port that is a pipe pushes back on the flow it receives (p = p_in + z q),
/// and the valve and the port are solved together.  While the valve is choked
/// the flow cannot know: the blowdown's peak is the same.  Afterwards the
/// piston has to push against the pipe: the same gas leaves, for more work.
#[test]
fn a_pipe_behind_the_valve_costs_work_not_flow() {
    let c = wot(3000.0, 1.6 * P_ATM);
    let rate = 48_000.0;
    let mut peaks = Vec::new();
    let mut masses = Vec::new();
    let mut works = Vec::new();
    // z = c / A: 600 m/s over a 28 mm port
    for z in [0.0, 600.0 / (core::f64::consts::FRAC_PI_4 * 0.028 * 0.028)] {
        let mut e = Engine::new(&a3(1), rate).unwrap();
        e.set_port_impedance(z, 0.0);
        e.force_control(&c);
        e.prime(10.0);
        e.cylinder_mut(0).ledger = Default::default();
        let mut peak: f64 = 0.0;
        for _ in 0..samples_per(4.0, c.rpm, rate) {
            peak = peak.max(e.tick().q_exhaust);
        }
        let k = &e.cylinders()[0];
        println!("z {z:.3e}: worst outer {} inner {}", k.worst_outer, k.worst_inner);
        assert!(k.worst_iterations() < 12);
        peaks.push(peak);
        masses.push(k.ledger.m_exhaust);
        works.push(k.ledger.work);
    }
    println!("peak {peaks:?} kg/s, mass {masses:?} kg, work {works:?} J");
    assert!((peaks[1] / peaks[0] - 1.0).abs() < 0.03, "{peaks:?}");
    assert!((masses[1] / masses[0] - 1.0).abs() < 0.05, "{masses:?}");
    assert!(works[1] < 0.995 * works[0], "{works:?}");
}

/// One exhaust event of cylinder 0: its flow from where the valve opens.
fn exhaust_event(spec: &EngineSpec, c: &Control, rate: f64) -> Vec<f64> {
    let mut e = running(spec, rate, c, 10.0);
    // run to just before this cylinder's exhaust valve opens
    let open = spec.exhaust.open_deg;
    while {
        let th = e.crank_deg();
        !(th > open - 3.0 && th < open - 1.0)
    } {
        e.tick();
    }
    let n = samples_per(1.0, c.rpm, rate) * (spec.exhaust.span_deg as usize + 6) / 720;
    (0..n).map(|_| e.tick().q_exhaust).collect()
}

/// Seconds the flow spends above half its peak.
fn width_at_half(q: &[f64], rate: f64) -> f64 {
    let peak = q.iter().cloned().fold(0.0, f64::max);
    q.iter().filter(|v| **v > 0.5 * peak).count() as f64 / rate
}

/// The claim the redesign rests on (s.6.1): the event is a process in TIME,
/// and load changes its kind, not only its size.
#[test]
fn the_event_is_shaped_by_load_and_lives_in_time() {
    let spec = a3(1);
    let rate = 192_000.0;

    // full load: a blowdown -- the cylinder opens at ten bar into a port at
    // one and a half, and most of the gas has left by bottom dead centre.
    let full = exhaust_event(&spec, &wot(2000.0, 1.8 * P_ATM), rate);
    // light load: it opens barely above the port's pressure; what leaves is
    // what the piston pushes out, over the whole stroke.
    let light = exhaust_event(&spec, &cruise(2000.0, 0.30 * P_ATM), rate);

    // share of the event's mass that leaves in the first quarter of the
    // valve's open period (to about bottom dead centre)
    let early = |q: &[f64]| q[..q.len() / 4].iter().sum::<f64>() / q.iter().sum::<f64>();
    let (ef, el) = (early(&full), early(&light));
    let (wf, wl) = (width_at_half(&full, rate), width_at_half(&light, rate));
    let peak = |q: &[f64]| q.iter().cloned().fold(0.0, f64::max);
    println!(
        "2000 rpm  full: early {ef:.2} width {:.2} ms peak {:.1} g/s   light: early {el:.2} width {:.2} ms peak {:.1} g/s",
        wf * 1e3,
        peak(&full) * 1e3,
        wl * 1e3,
        peak(&light) * 1e3
    );
    assert!(ef > 0.5, "full load: only {ef} of the gas in the first quarter");
    assert!(el < 0.2, "light load: {el} of the gas in the first quarter");
    // not the same shape scaled: the light-load event is several times longer
    assert!(wl > 2.0 * wf, "widths {wl} and {wf}");
    assert!(peak(&full) > 5.0 * peak(&light));

    // in time: from 1500 to 6000 rpm the crank turns four times faster, a
    // pulse drawn in crank degrees gets four times shorter; the blowdown does not.
    let slow = width_at_half(&exhaust_event(&spec, &wot(1500.0, 1.8 * P_ATM), rate), rate);
    let fast = width_at_half(&exhaust_event(&spec, &wot(6000.0, 1.8 * P_ATM), rate), rate);
    println!("blowdown width: {:.2} ms at 1500, {:.2} ms at 6000 (ratio {:.2})", slow * 1e3, fast * 1e3, slow / fast);
    assert!(slow / fast < 3.0 && slow / fast > 1.0, "{slow} / {fast}");
    assert!(slow > 1.0e-3 && slow < 12.0e-3, "{slow}");
}
