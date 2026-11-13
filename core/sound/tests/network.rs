//! The pipes (stage S2), against what acoustics says of them.

mod common;

use common::*;
use es_sound::gas::GasModel;
use es_sound::{Control, Engine, Network, NetworkSpec, NodeSpec};

const RATE: f64 = 48_000.0;

fn c_of(t: f64) -> f64 {
    let g = GasModel { gamma_const: 0.0 };
    (g.gamma(t) * 287.0 * t).sqrt()
}

/// One step of a bare network with flow `q` pushed in at cylinder 0's node;
/// returns the pressure at that node.
fn step(net: &mut Network, q: f64) -> f64 {
    net.arrive();
    let v = net.port(0);
    net.scatter(&[q]);
    v.p + v.z * q
}

/// Magnitude of the Fourier component of `x` at `f` Hz.
fn tone(x: &[f64], f: f64) -> f64 {
    let w = 2.0 * core::f64::consts::PI * f / RATE;
    let (mut re, mut im) = (0.0, 0.0);
    for (i, v) in x.iter().enumerate() {
        re += v * (w * i as f64).cos();
        im -= v * (w * i as f64).sin();
    }
    (re * re + im * im).sqrt()
}

/// A valve node, one pipe of ambient air, an open end.
fn organ_pipe(length: f64, d: f64) -> NetworkSpec {
    let mut p = Plumber::new();
    let v = p.node(NodeSpec::Valve { cylinder: 0 });
    let n = p.pipe(v, length, circle(d), 0.0, 0.0);
    p.make(n, NodeSpec::Open { radius: 0.5 * d, noise: 0.0, distance: 0.0 });
    p.spec
}

#[test]
fn a_closed_open_pipe_sings_its_quarter_waves() {
    // closed at the valve, open at the far end: f = (2n - 1) c / (4 (L + 0.6133 a))
    let (length, d) = (0.80, 0.05);
    let mut net = Network::new(&organ_pipe(length, d), 1, RATE).unwrap();
    net.set_steepening(false); // the linear pipe is what the formula describes
    let mut h = vec![0.0; 1 << 15];
    for (i, v) in h.iter_mut().enumerate() {
        *v = step(&mut net, if i == 0 { 1.0 } else { 0.0 });
    }
    let c = c_of(293.0);
    for n in [1.0, 2.0, 3.0] {
        let want = (2.0 * n - 1.0) * c / (4.0 * (length + 0.6133 * 0.5 * d));
        // the peak of the response near there
        let mut best = (0.0, 0.0);
        let mut f = 0.9 * want;
        while f < 1.1 * want {
            let m = tone(&h, f);
            if m > best.1 {
                best = (f, m);
            }
            f += 0.1;
        }
        let err = best.0 / want - 1.0;
        println!("quarter wave {n}: {:.1} Hz, formula {want:.1} Hz ({:+.2} %)", best.0, 100.0 * err);
        assert!(err.abs() < 0.01, "resonance {n} at {} Hz, formula {want}", best.0);
    }
}

#[test]
fn what_goes_in_comes_out() {
    // a network with every kind of node; a burst of flow in, then nothing
    let mut p = Plumber::new();
    let v = p.node(NodeSpec::Valve { cylinder: 0 });
    let n = p.pipe(v, 0.3, circle(0.03), 1.0, 0.0);
    p.make(n, NodeSpec::Orifice { r: 2.0e4, k: 3.0e6, channel: 0, throat: 0.0, noise: 0.0 });
    let n = p.pipe(n, 0.5, circle(0.05), 0.8, 40.0);
    // a side branch, closed at its far end
    let _ = p.pipe(n, 0.2, circle(0.04), 0.5, 0.0);
    let n = p.pipe(n, 0.3, 0.02, 0.5, 10.0);
    let n = p.pipe(n, 0.6, circle(0.05), 0.4, 0.0);
    p.make(n, NodeSpec::Open { radius: 0.025, noise: 0.0, distance: 0.0 });
    let mut net = Network::new(&p.spec, 1, RATE).unwrap();
    // (exact to the last digit only in the linear pipe: a steepening wave
    //  keeps its mass over a cycle, not sample by sample)
    net.set_steepening(false);
    let gas = GasModel { gamma_const: 0.0 };
    net.set_gas(&gas, 900.0, &[1.0; 4], true);

    let (mut m_in, mut m_out) = (0.0, 0.0);
    for i in 0..4000 {
        let q = if i < 1500 { 0.08 * (1.0 + (i as f64 * 0.05).sin()) } else { 0.0 };
        step(&mut net, q);
        m_in += q / RATE;
        m_out += net.q_out / RATE;
        if i == 1499 {
            // the orifice is passing the steady part by now
            let held = net.stored_mass();
            println!("in {m_in:.6} kg, out {m_out:.6} kg, held {held:.6} kg");
            assert!(((m_out + held) / m_in - 1.0).abs() < 1.0e-9);
        }
    }
    for _ in 0..200_000 {
        step(&mut net, 0.0);
        m_out += net.q_out / RATE;
    }
    println!("after the burst: in {m_in:.6} kg, out {m_out:.6} kg");
    assert!((m_out / m_in - 1.0).abs() < 1.0e-6);
    assert!(net.stored_mass().abs() < 1.0e-9 * m_in);
}

#[test]
fn an_orifice_holds_back_its_pressure_drop() {
    // steady flow through dp = r q + k q^2: with the open end at ambient and
    // the pipes passing steady flow unchanged, the source sits at that drop
    let (r, k) = (3.0e4, 4.0e6);
    let mut p = Plumber::new();
    let v = p.node(NodeSpec::Valve { cylinder: 0 });
    let n = p.pipe(v, 0.4, circle(0.04), 0.5, 0.0);
    p.make(n, NodeSpec::Orifice { r, k, channel: 1, throat: 0.0, noise: 0.0 });
    let n = p.pipe(n, 0.8, circle(0.05), 0.5, 0.0);
    p.make(n, NodeSpec::Open { radius: 0.025, noise: 0.0, distance: 0.0 });
    let mut net = Network::new(&p.spec, 1, RATE).unwrap();
    let gas = GasModel { gamma_const: 0.0 };
    for (q, vanes) in [(0.02, 1.0), (0.10, 1.0), (0.10, 2.5), (-0.05, 1.0)] {
        net.set_gas(&gas, 800.0, &[1.0, vanes, 1.0, 1.0], true);
        let mut p_src = 0.0;
        for _ in 0..40_000 {
            p_src = step(&mut net, q);
        }
        let want = vanes * (r * q + k * q * q.abs());
        println!("q {q:+.2} kg/s, vanes x{vanes}: {p_src:.1} Pa, law {want:.1} Pa");
        assert!((p_src / want - 1.0).abs() < 1.0e-3);
        assert!((net.q_out / q - 1.0).abs() < 1.0e-6);
    }
}

/// The pulse that comes out of the far side of `chamber` (length, area)
/// set between two long pipes of area `s`, for a pulse sent in.
fn through_chamber(chamber: Option<(f64, f64)>, s: f64) -> Vec<f64> {
    let mut p = Plumber::new();
    let v = p.node(NodeSpec::Valve { cylinder: 0 });
    let mut n = p.pipe(v, 12.0, s, 0.0, 0.0);
    if let Some((len, area)) = chamber {
        n = p.pipe(n, len, area, 0.0, 0.0);
    }
    let tail = p.spec.pipes.len();
    let n = p.pipe(n, 12.0, s, 0.0, 0.0);
    p.make(n, NodeSpec::Open { radius: 0.02, noise: 0.0, distance: 0.0 });
    let mut net = Network::new(&p.spec, 1, RATE).unwrap();
    net.set_steepening(false);
    // before anything has come back from either far end
    let n_samples = (2.6 * 12.0 / c_of(293.0) * RATE) as usize;
    let mut out = Vec::with_capacity(n_samples);
    for i in 0..n_samples {
        // a short smooth pulse
        let x = i as f64 / 24.0;
        let q = if x < 1.0 { 0.5 - 0.5 * (2.0 * core::f64::consts::PI * x).cos() } else { 0.0 };
        step(&mut net, q);
        out.push(net.pipe_state(tail).2);
    }
    out
}

#[test]
fn an_expansion_chamber_has_its_textbook_transmission_loss() {
    // TL = 10 log10(1 + (m - 1/m)^2 sin^2(k L) / 4), m the area ratio
    let s = circle(0.05);
    let (len, m) = (0.30, 9.0);
    let plain = through_chamber(None, s);
    let with = through_chamber(Some((len, m * s)), s);
    let c = c_of(293.0);
    let mut worst: f64 = 0.0;
    for f in [150.0, 286.0, 400.0, 572.0, 700.0, 858.0, 1000.0, 1144.0] {
        let tl = 20.0 * (tone(&plain, f) / tone(&with, f)).log10();
        let kl = 2.0 * core::f64::consts::PI * f * len / c;
        let want = 10.0 * (1.0 + 0.25 * (m - 1.0 / m).powi(2) * kl.sin().powi(2)).log10();
        println!("{f:6.0} Hz: transmission loss {tl:5.2} dB, formula {want:5.2} dB");
        worst = worst.max((tl - want).abs());
    }
    assert!(worst < 0.5, "off the formula by {worst} dB");
}

#[test]
fn the_a3_breathes_out_through_its_exhaust() {
    for (name, c) in [
        ("idle", Control { ccv: 0.0, ..cruise(800.0, 0.32 * P_ATM) }),
        ("cruise", cruise(2000.0, 0.55 * P_ATM)),
        ("full load", Control { p_exhaust: P_ATM, ..wot(5000.0, 1.9 * P_ATM) }),
    ] {
        let c = Control { p_exhaust: P_ATM, ..c };
        let mut e = running_a3(RATE, &c, 60.0);
        let n = samples_per(8.0, c.rpm, RATE);
        let (mut valves, mut tail, mut p2, mut peak) = (0.0, 0.0, 0.0, 0.0f64);
        let mut manifold = 0.0;
        for _ in 0..n {
            let s = e.tick();
            assert!(s.p_far.is_finite());
            valves += s.q_exhaust;
            tail += s.q_tail;
            p2 += s.p_far * s.p_far;
            peak = peak.max(s.p_far.abs());
            // pipe 4 is the collector, upstream of the turbine
            manifold += e.exhaust().unwrap().pipe_state(4).2;
        }
        let nf = n as f64;
        let rms = (p2 / nf).sqrt();
        let spl = 20.0 * (rms / 2.0e-5).log10();
        println!(
            "{name:9}: valves {:.2} g/s, tail {:.2} g/s, manifold {:+.0} mbar, \
             {spl:.1} dB SPL at 1 m (peak {peak:.2} Pa), worst solve {}",
            1.0e3 * valves / nf,
            1.0e3 * tail / nf,
            0.01 * manifold / nf,
            e.cylinders().iter().map(|c| c.worst_iterations()).max().unwrap(),
        );
        assert!((tail / valves - 1.0).abs() < 0.01, "{name}: mass is not conserved");
        assert!(e.cylinders().iter().all(|c| c.worst_iterations() < 24));
        assert!(spl > 40.0 && spl < 125.0, "{name}: {spl} dB SPL");
    }
}

#[test]
fn the_exhaust_does_not_care_about_block_length() {
    let c = Control { p_exhaust: P_ATM, ccv: 0.05, level_ref: 0.5, dynamics: 0.5, ..cruise(2200.0, 0.6 * P_ATM) };
    let render = |block: usize| {
        let mut e = running_a3_noisy(RATE, &c, 6.0);
        let inbox = e.inbox();
        let mut out = vec![0.0f32; 9000];
        let mut r = vec![0.0f32; 9000];
        let mut i = 0;
        while i < out.len() {
            if i >= 3000 && i < 3000 + block {
                inbox.publish(Control { rpm: 2900.0, t_exhaust: 950.0, orifice: [1.4, 1.0, 1.0, 1.0], ..c });
            }
            let j = (i + block).min(out.len());
            e.render(&mut out[i..j], &mut r[i..j]);
            i = j;
        }
        out
    };
    // the operating point is published at the same sample in both
    let a = render(1);
    let b = render(3000);
    assert!(a == b, "block length changed the sound");
    assert!(a.iter().any(|v| v.abs() > 1.0e-4));
}

#[test]
fn no_two_cycles_burn_alike() {
    // the work of single cycles scatters about as much as it was asked to
    for ccv in [0.0, 0.02, 0.10] {
        let c = Control { p_exhaust: P_ATM, ccv, ..cruise(1500.0, 0.5 * P_ATM) };
        let mut e = running_a3(RATE, &c, 30.0);
        let per = samples_per(1.0, c.rpm, RATE);
        let mut works = Vec::new();
        for _ in 0..200 {
            e.cylinder_mut(0).ledger = Default::default();
            for _ in 0..per {
                e.tick();
            }
            works.push(e.cylinders()[0].ledger.work);
        }
        let mean = works.iter().sum::<f64>() / works.len() as f64;
        let var = works.iter().map(|w| (w - mean) * (w - mean)).sum::<f64>() / works.len() as f64;
        let cov = var.sqrt() / mean;
        println!("asked {ccv:.2}: indicated work {mean:.1} J a cycle, scatter {:.3}", cov);
        if ccv == 0.0 {
            assert!(cov < 2.0e-3);
        } else {
            assert!(cov > 0.4 * ccv && cov < 2.5 * ccv, "asked {ccv}, got {cov}");
        }
    }
}

#[test]
fn the_hiss_rides_on_the_flow() {
    // flow noise is made where gas moves fast: next to none idling, and at
    // full load it grows much faster than the flow does
    let band = |c: &Control, noise: f64| {
        let mut e = Engine::new(&a3(4), RATE).unwrap();
        e.attach_exhaust(&a3_exhaust_with(noise)).unwrap();
        e.force_control(c);
        e.prime(60.0);
        let n = 1 << 15;
        let x: Vec<f64> = (0..n).map(|_| e.tick().p_far).collect();
        // sound pressure between the orders, around 1.5-3 kHz
        let mut s = 0.0;
        for f in [1510.0, 1790.0, 2130.0, 2470.0, 2890.0] {
            s += tone(&x, f).powi(2);
        }
        s.sqrt()
    };
    let idle = Control { p_exhaust: P_ATM, ..cruise(800.0, 0.32 * P_ATM) };
    let full = Control { p_exhaust: P_ATM, ..wot(5000.0, 1.9 * P_ATM) };
    let (i0, i1) = (band(&idle, 0.0), band(&idle, 1.0));
    let (f0, f1) = (band(&full, 0.0), band(&full, 1.0));
    println!("1.5-3 kHz, quiet -> with flow noise: idle {i0:.3e} -> {i1:.3e}, full load {f0:.3e} -> {f1:.3e}");
    // (the block radiates in this band too, so the hiss adds to something)
    assert!(f1 > 1.2 * f0, "at full load the hiss is heard up there");
    // 50 times the flow: far more than 50 times the hiss
    let hiss_idle = (i1 * i1 - i0 * i0).abs().sqrt();
    let hiss_full = (f1 * f1 - f0 * f0).sqrt();
    assert!(hiss_full > 30.0 * hiss_idle, "idle {hiss_idle}, full load {hiss_full}");
}

#[test]
fn a_loud_wave_leans_forward() {
    // Fubini: a sine of amplitude p sent down a pipe has grown harmonics
    //   B_n = 2 J_n(n s) / (n s),   s = x / x_shock,
    //   x_shock = c / (k p w),      k = (gamma + 1) / (2 gamma p0)
    // by the time it has gone x.  At s = 0.5: B2/B1 = 0.237, B3/B1 = 0.084.
    let gas = GasModel { gamma_const: 0.0 };
    let g = gas.gamma(293.0);
    let c = c_of(293.0);
    let (f, amp) = (500.0, 2000.0);
    let k = (g + 1.0) / (2.0 * g * P_ATM);
    let x_shock = c / (k * amp * 2.0 * core::f64::consts::PI * f);
    let len = 0.5 * x_shock;

    let harmonics = |steepening: bool| {
        let mut p = Plumber::new();
        let v = p.node(NodeSpec::Valve { cylinder: 0 });
        // wide, so its walls take next to nothing
        let n = p.pipe(v, len, circle(0.2), 0.0, 0.0);
        let n = p.pipe(n, 40.0, circle(0.2), 0.0, 0.0);
        p.make(n, NodeSpec::Open { radius: 0.1, noise: 0.0, distance: 0.0 });
        let mut net = Network::new(&p.spec, 1, RATE).unwrap();
        net.set_steepening(steepening);
        net.set_gas(&gas, 293.0, &[1.0; 4], true);
        let z = {
            net.arrive();
            let z = net.port(0).z;
            net.scatter(&[0.0]);
            z
        };
        let per = (RATE / f) as usize; // 96 samples
        let skip = (len / c * RATE) as usize + 20 * per;
        let n_fft = 40 * per;
        let mut x = Vec::with_capacity(n_fft);
        for i in 0..skip + n_fft {
            let w = 2.0 * core::f64::consts::PI * f * i as f64 / RATE;
            // (eased in over ten periods)
            let ease = (i as f64 / (10 * per) as f64).min(1.0);
            step(&mut net, ease * amp / z * w.sin());
            if i >= skip {
                // the wave as it reaches the end of the first pipe
                x.push(net.arriving(0, true));
            }
        }
        [tone(&x, f), tone(&x, 2.0 * f), tone(&x, 3.0 * f)].map(|v| 2.0 * v / n_fft as f64)
    };
    let lin = harmonics(false);
    let non = harmonics(true);
    println!("shock distance {x_shock:.2} m, pipe {len:.2} m");
    println!("linear:     {:.1} Pa, 2nd {:.4}, 3rd {:.4} of it", lin[0], lin[1] / lin[0], lin[2] / lin[0]);
    println!("steepening: {:.1} Pa, 2nd {:.4}, 3rd {:.4} of it (Fubini: 0.237, 0.084)", non[0], non[1] / non[0], non[2] / non[0]);
    assert!(lin[1] / lin[0] < 1.0e-3);
    assert!((non[1] / non[0] / 0.237 - 1.0).abs() < 0.08);
    assert!((non[2] / non[0] / 0.084 - 1.0).abs() < 0.15);
}
