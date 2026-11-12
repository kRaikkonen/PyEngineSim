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
        ..Control::default()
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

// ---------------------------------------------------------------- exhausts

use es_sound::{NetworkSpec, NodeSpec, PipeSpec};

pub fn circle(d: f64) -> f64 {
    0.25 * core::f64::consts::PI * d * d
}

/// Builds a network pipe by pipe.
pub struct Plumber {
    pub spec: NetworkSpec,
}

impl Plumber {
    pub fn new() -> Self {
        Plumber { spec: NetworkSpec { pipes: Vec::new(), nodes: Vec::new(), t_ambient: 293.0, valve_noise: 0.0 } }
    }

    pub fn node(&mut self, n: NodeSpec) -> u32 {
        self.spec.nodes.push(n);
        self.spec.nodes.len() as u32 - 1
    }

    /// A pipe from node `a` to a new junction, which is returned.
    pub fn pipe(&mut self, a: u32, length: f64, area: f64, heat: f64, loss_db: f64) -> u32 {
        let b = self.node(NodeSpec::Junction);
        self.spec.pipes.push(PipeSpec { length, area, heat, loss_db, a, b });
        b
    }

    /// A pipe between two nodes that exist.
    pub fn join(&mut self, a: u32, b: u32, length: f64, area: f64, heat: f64, loss_db: f64) {
        self.spec.pipes.push(PipeSpec { length, area, heat, loss_db, a, b });
    }

    /// Turn a junction into something else.
    pub fn make(&mut self, node: u32, n: NodeSpec) {
        self.spec.nodes[node as usize] = n;
    }
}

impl Plumber {
    /// Density (kg/m^3) of the gas in a pipe at the rated point (port gas 1100 K).
    pub fn density(&self, heat: f64) -> f64 {
        101_325.0 / (287.0 * (self.spec.t_ambient + heat * (1100.0 - self.spec.t_ambient)))
    }

    /// Node `x` becomes a turbulent loss of `k_loss` dynamic heads of the
    /// flow through `area`.
    pub fn loss(&mut self, x: u32, k_loss: f64, area: f64, heat: f64, noise: f64) {
        let k = k_loss / (2.0 * self.density(heat) * area * area);
        self.make(x, NodeSpec::Orifice { r: 0.0, k, channel: -1, throat: area, noise });
    }

    /// An expansion chamber after node `x`: Borda-Carnot going in, half a
    /// contraction head going out.
    #[allow(clippy::too_many_arguments)]
    pub fn chamber(
        &mut self,
        x: u32,
        a_in: f64,
        length: f64,
        area: f64,
        heat: f64,
        loss_db: f64,
        a_out: f64,
        noise: f64,
    ) -> u32 {
        self.loss(x, (1.0 - a_in / area).powi(2), a_in, heat, noise);
        let x = self.pipe(x, length, area, heat, loss_db);
        self.loss(x, 0.5 * (1.0 - a_out / area), a_out, heat, noise);
        x
    }
}

/// The exhaust of the A3 35 TFSI (EA211 evo 1.5, MQB evo), turbine to tail
/// -- engine_sim/core_exhaust.py::a3_exhaust, pipe for pipe.  Dimensions
/// are typical of the layout, NOT measured from the car: four short runners
/// cast into the head, a variable-vane turbine (orifice channel 0), a
/// close-coupled catalyst and particulate filter, a front pipe, an
/// absorption centre silencer, a lined rear silencer and a down-turned
/// tailpipe.  `noise` scales the flow noise (1: as the studio plays it).
pub fn a3_exhaust_with(noise: f64) -> NetworkSpec {
    let mut p = Plumber::new();
    p.spec.valve_noise = 0.006 * noise;
    let collector = p.node(NodeSpec::Junction);
    for (k, len) in [0.19, 0.13, 0.13, 0.19].into_iter().enumerate() {
        let v = p.node(NodeSpec::Valve { cylinder: k as u32 });
        p.join(v, collector, len, circle(0.032), 1.0, 0.0);
    }
    let n = p.pipe(collector, 0.06, circle(0.040), 0.97, 0.0);
    // turbine: 1.1 bar across it at 0.155 kg/s; below 3 % of that flow its
    // resistance is the linear one of a leak, not the square law of a nozzle
    p.make(
        n,
        NodeSpec::Orifice {
            r: 0.03 * 4.6e6 * 0.155,
            k: 4.6e6,
            channel: 0,
            throat: circle(0.030),
            noise: 0.006 * noise,
        },
    );
    let n = p.pipe(n, 0.10, circle(0.050), 0.85, 0.0);
    // catalyst brick (400 cpsi, 75 % open), 4 kPa at rated flow
    let n = p.pipe(n, 0.10, 0.75 * circle(0.118), 0.82, 40.0);
    p.make(n, NodeSpec::Orifice { r: 2.6e4, k: 0.0, channel: -1, throat: 0.0, noise: 0.0 });
    // particulate filter, 7 kPa
    let n = p.pipe(n, 0.12, 0.60 * circle(0.118), 0.78, 80.0);
    p.make(n, NodeSpec::Orifice { r: 4.5e4, k: 0.0, channel: -1, throat: 0.0, noise: 0.0 });
    let a = circle(0.055);
    let n = p.pipe(n, 0.90, a, 0.62, 0.0);
    // centre silencer: a perforated pipe through packing
    let n = p.pipe(n, 0.40, a, 0.52, 25.0);
    let n = p.pipe(n, 1.10, a, 0.45, 0.0);
    // rear silencer: 10 l across the car, wool-lined
    let n = p.chamber(n, a, 0.45, 0.022, 0.34, 15.0, circle(0.050), 0.008 * noise);
    let n = p.pipe(n, 0.45, circle(0.050), 0.30, 0.0);
    p.make(n, NodeSpec::Open { radius: 0.025, noise: 0.008 * noise });
    p.spec
}

/// The A3 exhaust with no flow noise (the tests that count mass and orders).
pub fn a3_exhaust() -> NetworkSpec {
    a3_exhaust_with(0.0)
}

/// A running A3 with its exhaust and its flow noise.
pub fn running_a3_noisy(rate: f64, c: &Control, cycles: f64) -> Engine {
    let mut e = Engine::new(&a3(4), rate).expect("spec");
    e.attach_exhaust(&a3_exhaust_with(1.0)).expect("exhaust");
    e.force_control(c);
    e.prime(cycles);
    e
}

/// A running A3 with its exhaust.
pub fn running_a3(rate: f64, c: &Control, cycles: f64) -> Engine {
    let mut e = Engine::new(&a3(4), rate).expect("spec");
    e.attach_exhaust(&a3_exhaust()).expect("exhaust");
    e.force_control(c);
    e.prime(cycles);
    for k in 0..e.cylinders().len() {
        e.cylinder_mut(k).ledger = Default::default();
    }
    e
}
