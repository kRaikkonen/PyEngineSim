//! The pipes: a network of one-dimensional waveguides, compiled from a
//! topology.  Stage S2 of docs/REDESIGN_V2.md.
//!
//! A pipe carries two travelling pressure waves (Pa, about the pressure at
//! the open ends).  With `z = c / A` (Pa s/kg) the pressure and the mass flow
//! at any section are `p = down + up` and `q = (down - up) / z`.
//!
//! Pipes meet at nodes, and every node is one of four things:
//!
//! - Junction: any number of pipe ends at one pressure; one end alone is a
//!   closed end.
//! - Valve: a junction a cylinder discharges into -- to the valve it is the
//!   port `p = p_in + z q` the cylinder solves against.
//! - Orifice: two ends joined through a flow resistance
//!   `dp = r q + k q |q|` (a turbine, a brick, a flap).
//! - Open: one end radiating into the air (an unflanged pipe).
//!
//! Every node conserves mass exactly and the pipe losses leave steady flow
//! alone (their gain at 0 Hz is 1), so what the valves push in is what the
//! open ends let out.  Mufflers, resonators and cans are not elements of
//! their own: a chamber is a fat pipe, a side branch a pipe with one end
//! closed.
//!
//! Large waves steepen.  A wave of pressure p' moves its gas at
//! u = p' / (rho c) and travels at c + (gamma + 1)/2 u: the crests of an
//! exhaust pulse catch up with its troughs, the pulse leans forward into a
//! shock, and harmonics appear that the valve never made -- the hardness of
//! a loaded exhaust.  In a delay line this is exact for a wave travelling
//! one way (Earnshaw): what arrives now left earlier or later by
//! `delay x (gamma + 1)/(2 gamma) x p' / p`.
//!
//! Flow noise (s.6.4) is not a layer added afterwards.  Wherever the flow
//! loses pressure -- through the valve seat, through an orifice, out of the
//! tail -- that loss is turbulent and fluctuates by a few per cent of
//! itself, in a band set by the local speed over the local size.  So the
//! hiss exists only while gas is moving fast, rides on each pulse, grows as
//! the cube of the speed, and is filtered by whatever lies downstream of
//! where it was made.

use crate::gas::{GasModel, R_GAS};
use crate::math;
use crate::spec::SpecError;

/// How many `Control::orifice` channels there are.
pub const ORIFICE_CHANNELS: usize = 4;

/// A change the control block asks for is spread over this many samples.
const RAMP: u32 = crate::engine::CONTROL_BLOCK as u32;

/// A pipe is never shorter than this many samples (the interpolator needs
/// the room); a shorter one is lengthened to it.
pub const MIN_DELAY: f64 = 2.0;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PipeSpec {
    /// m
    pub length: f64,
    /// acoustic cross-section, m^2
    pub area: f64,
    /// where its gas sits between the air outside (0) and the gas in the
    /// exhaust port (1)
    pub heat: f64,
    /// absorption beyond a bare wall, dB per metre at 1 kHz (packing, a
    /// brick, perforations); grows as the square root of frequency
    pub loss_db: f64,
    /// the nodes at its two ends
    pub a: u32,
    pub b: u32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum NodeSpec {
    Junction,
    /// the exhaust (or intake) valve of this cylinder
    Valve { cylinder: u32 },
    /// dp = r q + k q|q|; `channel` >= 0 multiplies both by
    /// `Control::orifice[channel]`.  The turbulent part fluctuates: `noise`
    /// is the rms of that as a fraction of k q|q|, `throat` (m^2) the
    /// passage whose speed and size set its band (0: silent).
    Orifice { r: f64, k: f64, channel: i32, throat: f64, noise: f64 },
    /// an open end of this radius, m; `noise` is the rms fluctuation of the
    /// dynamic head of the gas leaving it, as a fraction
    Open { radius: f64, noise: f64 },
}

#[derive(Clone, Debug, PartialEq)]
pub struct NetworkSpec {
    pub pipes: Vec<PipeSpec>,
    pub nodes: Vec<NodeSpec>,
    /// the air outside, K
    pub t_ambient: f64,
    /// rms fluctuation of the flow through a valve seat, as a fraction
    pub valve_noise: f64,
}

impl NetworkSpec {
    pub fn validate(&self, cylinders: usize) -> Result<(), SpecError> {
        let bad = |s: String| Err(SpecError(s));
        if self.pipes.is_empty() || self.pipes.len() > 4096 {
            return bad(format!("{} pipes", self.pipes.len()));
        }
        if !(self.t_ambient.is_finite() && (200.0..=400.0).contains(&self.t_ambient)) {
            return bad(format!("ambient temperature {}", self.t_ambient));
        }
        let mut ends = vec![0u32; self.nodes.len()];
        for (i, p) in self.pipes.iter().enumerate() {
            let ok = p.length.is_finite()
                && p.length > 0.0
                && p.length <= 50.0
                && p.area.is_finite()
                && p.area > 1.0e-7
                && p.area <= 10.0
                && (0.0..=1.0).contains(&p.heat)
                && (0.0..=2000.0).contains(&p.loss_db);
            if !ok {
                return bad(format!("pipe {i}: {p:?}"));
            }
            if p.a == p.b {
                return bad(format!("pipe {i} has both ends on node {}", p.a));
            }
            for n in [p.a, p.b] {
                match ends.get_mut(n as usize) {
                    Some(c) => *c += 1,
                    None => return bad(format!("pipe {i} names node {n}")),
                }
            }
        }
        let mut seen = vec![false; cylinders];
        for (i, n) in self.nodes.iter().enumerate() {
            let c = ends[i];
            let ok = match *n {
                NodeSpec::Junction => c >= 1,
                NodeSpec::Valve { cylinder } => {
                    let k = cylinder as usize;
                    if k >= cylinders || seen[k] {
                        return bad(format!("node {i}: cylinder {cylinder}"));
                    }
                    seen[k] = true;
                    c >= 1
                }
                NodeSpec::Orifice { r, k, channel, throat, noise } => {
                    c == 2
                        && r.is_finite()
                        && k.is_finite()
                        && r >= 0.0
                        && k >= 0.0
                        && (channel as i64) < ORIFICE_CHANNELS as i64
                        && (0.0..=10.0).contains(&throat)
                        && (0.0..=0.5).contains(&noise)
                }
                NodeSpec::Open { radius, noise } => {
                    c == 1 && radius.is_finite() && radius > 1.0e-4 && (0.0..=0.5).contains(&noise)
                }
            };
            if !ok {
                return bad(format!("node {i}: {n:?} with {c} pipe ends"));
            }
        }
        if seen.iter().any(|s| !s) {
            return bad("a cylinder has no valve node".into());
        }
        if !self.nodes.iter().any(|n| matches!(n, NodeSpec::Open { .. })) {
            return bad("no open end".into());
        }
        if !(0.0..=0.5).contains(&self.valve_noise) {
            return bad(format!("valve noise {}", self.valve_noise));
        }
        Ok(())
    }
}

/// One direction of a pipe: a delay with a fractional tap (cubic Lagrange:
/// gain 1 at 0 Hz and never above 1) and the wall loss.
struct Line {
    buf: Vec<f64>,
    mask: usize,
    w: usize,
    /// samples
    delay: f64,
    /// a change of delay is spread over a control block: per sample, and
    /// how many samples of it are left
    glide: f64,
    left: u32,
    /// age of the first of the four taps, and their weights
    first: usize,
    h: [f64; 4],
    /// the loss: one pole, gain 1 at 0 Hz
    b: f64,
    lp: f64,
    /// steepening: the fraction a pascal of wave shortens the delay by
    /// ((gamma + 1) / (2 gamma p), 0: a linear pipe), the steady part of
    /// what arrives (which is flow, not wave) and what arrived last sample
    k_nl: f64,
    dc: f64,
    last: f64,
    last2: f64,
    /// the delay the last sample was read at
    d_now: f64,
}

impl Line {
    fn new(max_delay: f64) -> Self {
        let n = ((max_delay.ceil() as usize) + 8).next_power_of_two();
        let mut l = Line {
            buf: vec![0.0; n],
            mask: n - 1,
            w: 0,
            delay: 0.0,
            glide: 0.0,
            left: 0,
            first: 1,
            h: [0.0, 1.0, 0.0, 0.0],
            b: 0.0,
            lp: 0.0,
            k_nl: 0.0,
            dc: 0.0,
            last: 0.0,
            last2: 0.0,
            d_now: MIN_DELAY,
        };
        l.set_delay(MIN_DELAY);
        l
    }

    /// Move to a delay of `d` samples: at once, or gliding there over the
    /// next `over` samples (a step every control block would be a tone at
    /// the block rate).
    fn aim_delay(&mut self, d: f64, over: u32) {
        let d = d.max(MIN_DELAY).min((self.buf.len() - 4) as f64);
        if over == 0 {
            self.left = 0;
            self.set_delay(d);
            self.d_now = d;
        } else {
            self.glide = (d - self.delay) / over as f64;
            self.left = if self.glide != 0.0 { over } else { 0 };
        }
    }

    fn set_delay(&mut self, d: f64) {
        if d == self.delay {
            return;
        }
        self.delay = d;
        (self.first, self.h) = taps(d);
    }

    /// What arrives at the far end this sample.  `k_mean`: per sample, for
    /// the slow mean that tells flow from wave.  (Slow -- a third of a
    /// second -- because a wave measured against a mean that follows it
    /// would not keep its mass: with a fixed reference, a steepening
    /// periodic wave carries exactly the mass it was sent with.)
    #[inline(always)]
    fn read(&mut self, k_mean: f64) -> f64 {
        if self.left > 0 {
            self.left -= 1;
            self.set_delay(self.delay + self.glide);
        }
        // How much sooner the wave now arriving got here than a small one
        // would have: judged by the two samples before it (their mean -- a
        // guess that alternates from sample to sample cancels in it).
        //
        // Where a front has steepened into a shock, "what left when" has
        // three answers and this guess would jump between them: noise.  So
        // the point the line is read at never moves more than `SLEW`
        // samples a sample -- a front compresses up to that rate and no
        // faster.  Shocks therefore form later than they should; the fix is
        // a proper shock (equal areas), not a higher rate.
        const SLEW: f64 = 0.7;
        let x = self.k_nl * (0.5 * (self.last + self.last2) - self.dc);
        let small = x.abs() * self.delay < 1.0e-4 && (self.d_now - self.delay).abs() < 1.0e-4;
        let (first, h) = if self.k_nl == 0.0 || small {
            self.d_now = self.delay;
            (self.first, self.h)
        } else {
            let want = self.delay * (1.0 - x.clamp(-0.4, 0.4));
            let d = want
                .clamp(self.d_now - SLEW, self.d_now + SLEW)
                .max(MIN_DELAY)
                .min((self.buf.len() - 4) as f64);
            self.d_now = d;
            taps(d)
        };
        // the sample written last (at w - 1) is one sample old
        let i = self.w.wrapping_sub(first);
        let b = &self.buf;
        let m = self.mask;
        let y = h[0] * b[i & m]
            + h[1] * b[i.wrapping_sub(1) & m]
            + h[2] * b[i.wrapping_sub(2) & m]
            + h[3] * b[i.wrapping_sub(3) & m];
        self.dc += k_mean * (y - self.dc);
        self.last2 = self.last;
        self.last = y;
        y
    }

    /// Send a wave in at the near end.
    #[inline(always)]
    fn write(&mut self, x: f64) {
        self.lp = x + self.b * (self.lp - x);
        self.buf[self.w & self.mask] = self.lp;
        self.w = self.w.wrapping_add(1);
    }

    fn clear(&mut self) {
        self.buf.iter_mut().for_each(|v| *v = 0.0);
        self.lp = 0.0;
        self.dc = 0.0;
        self.last = 0.0;
        self.last2 = 0.0;
        self.d_now = self.delay;
    }

    /// What has been sent in and has not yet come out, Pa samples: tap k
    /// has still to read the newest `first + k` samples, and the loss filter
    /// owes b / (1 - b) of its state.
    fn held(&self) -> f64 {
        let mut s = 0.0;
        let mut run = 0.0;
        let mut k = 0;
        for j in 1..=self.first + 3 {
            run += self.buf[self.w.wrapping_sub(j) & self.mask];
            if j >= self.first {
                s += self.h[k] * run;
                k += 1;
            }
        }
        s + self.b / (1.0 - self.b) * self.lp
    }
}

/// The four taps of a delay of `d` samples: the age of the first, and the
/// cubic Lagrange weights (taps aged whole-1 .. whole+2, the point between
/// the middle two).
#[inline(always)]
fn taps(d: f64) -> (usize, [f64; 4]) {
    let whole = d.floor();
    let x = 1.0 + (d - whole);
    (
        whole as usize - 1,
        [
            -(x - 1.0) * (x - 2.0) * (x - 3.0) / 6.0,
            x * (x - 2.0) * (x - 3.0) / 2.0,
            -x * (x - 1.0) * (x - 3.0) / 2.0,
            x * (x - 1.0) * (x - 2.0) / 6.0,
        ],
    )
}

struct Pipe {
    spec: PipeSpec,
    /// its gas now, K
    t: f64,
}

#[derive(Clone, Copy)]
enum Kind {
    Junction,
    Valve { cylinder: usize },
    Orifice { r: f64, k: f64, channel: i32 },
    Open { radius: f64, b0: f64, b1: f64, a1: f64, x1: f64, y1: f64, q_prev: f64 },
}

/// Turbulence at one place: white noise shaped to the band the local speed
/// and size set, of unit variance.
#[derive(Clone, Copy, Default)]
struct Hiss {
    /// rms, as a fraction of the loss it rides on (0: silent)
    amount: f64,
    /// the passage: its area (m^2) and the density of the gas in it (kg/m^3)
    area: f64,
    rho: f64,
    /// flow through it, smoothed over 20 ms: signed, and its magnitude
    q_mean: f64,
    q_abs: f64,
    // the band: a two-pole low-pass (above the band turbulence falls
    // steeply), a high-pass, the gain that keeps unit variance
    a_lp: f64,
    a_hp: f64,
    gain: f64,
    lp: f64,
    lp2: f64,
    hp: f64,
}

impl Hiss {
    /// Once a control block: the band follows the flow speed U over the
    /// size d of the passage -- from 0.03 U/d to 0.2 U/d (a Strouhal
    /// number of 0.2 is where a jet is loudest), never narrower than
    /// 40-150 Hz.  Above the band it falls at two poles: a rush, not a hiss.
    fn retune(&mut self, rate: f64) {
        if self.amount <= 0.0 {
            return;
        }
        let d = (self.area * (4.0 / core::f64::consts::PI)).sqrt();
        let u = self.q_abs / (self.rho * self.area);
        let hi = (0.2 * u / d + 150.0).min(0.4 * rate);
        let lo = (0.03 * u / d + 40.0).min(0.5 * hi);
        let a_lp = 1.0 - math::exp(-2.0 * core::f64::consts::PI * hi / rate);
        let a_hp = 1.0 - math::exp(-2.0 * core::f64::consts::PI * lo / rate);
        // the band follows the 20 ms mean of the flow, itself eased over
        // some thirty blocks
        self.a_lp += 0.03 * (a_lp - self.a_lp);
        self.a_hp += 0.03 * (a_hp - self.a_hp);
        // white noise of unit variance through the two poles (each 1 - r
        // z^-1 below a) keeps a^4 (1 + r^2) / (1 - r^2)^3 of it
        let r2 = (1.0 - self.a_lp) * (1.0 - self.a_lp);
        let a2 = self.a_lp * self.a_lp;
        let d = 1.0 - r2;
        self.gain = (d * d * d / (a2 * a2 * (1.0 + r2))).sqrt();
    }

    /// One sample: `white` has unit variance, `q` is the flow now.
    #[inline(always)]
    fn next(&mut self, white: f64, q: f64, k_mean: f64) -> f64 {
        self.q_mean += k_mean * (q - self.q_mean);
        self.q_abs += k_mean * (q.abs() - self.q_abs);
        self.lp += self.a_lp * (white - self.lp);
        self.lp2 += self.a_lp * (self.lp - self.lp2);
        self.hp += self.a_hp * (self.lp2 - self.hp);
        (self.lp2 - self.hp) * self.gain
    }
}

struct Node {
    kind: Kind,
    /// its pipe ends are `ends[first .. first + count]`
    first: usize,
    count: usize,
    /// 1 / (the sum of the admittances of its ends)
    z: f64,
    hiss: Hiss,
    /// an open end reflects less while gas streams out of it: (1 - M) / (1 + M)
    /// of the sound -- not of the steady part, `steady`, which stays whole
    /// (the static pressure at the end of a pipe is that of the air outside)
    mach_per_q: f64,
    steady: f64,
    /// an orifice's resistance now, and its change per sample while a
    /// control block re-aims it
    r_now: f64,
    k_now: f64,
    d_r: f64,
    d_k: f64,
    ramp: u32,
}

/// What a valve node shows its cylinder this sample.
#[derive(Clone, Copy, Debug, Default)]
pub struct PortView {
    /// pressure there with no flow from the valve, Pa about the outside
    pub p: f64,
    /// Pa s/kg
    pub z: f64,
}

pub struct Network {
    rate: f64,
    t_ambient: f64,
    pipes: Vec<Pipe>,
    /// two per pipe: [2i] carries a -> b, [2i + 1] carries b -> a
    lines: Vec<Line>,
    nodes: Vec<Node>,
    /// pipe ends by node; end `2i` is pipe i at its node a, `2i + 1` at b.
    /// The line LEAVING end e is `lines[e]`.
    ends: Vec<u32>,
    /// by end: the wave arriving there this sample, and its admittance A / c
    arriving: Vec<f64>,
    y: Vec<f64>,
    /// by cylinder: its valve node
    valve_node: Vec<usize>,
    port: Vec<PortView>,
    /// the port gas temperature every pipe has settled to (0: not settled)
    t_port: f64,
    rng: u64,
    /// per sample, for the 20 ms flow averages
    k_mean: f64,
    /// per sample, for the 0.3 s mean a wave is measured against
    k_slow: f64,
    /// do large waves steepen (they do; off only to audit the linear pipe)
    steepening: bool,
    /// mass flow out of all open ends this sample, kg/s, and its rate of
    /// change, kg/s^2 -- the strength of the monopole they are
    pub q_out: f64,
    pub dq_out: f64,
}

/// The speed of sound, m/s.
#[inline]
fn c_of(gas: &GasModel, t: f64) -> f64 {
    (gas.gamma(t) * R_GAS * t).sqrt()
}

impl Network {
    pub fn new(spec: &NetworkSpec, cylinders: usize, rate: f64) -> Result<Self, SpecError> {
        spec.validate(cylinders)?;
        let gas = GasModel { gamma_const: 0.0 };
        let c_cold = c_of(&gas, spec.t_ambient.min(250.0));
        let mut lines = Vec::with_capacity(2 * spec.pipes.len());
        for p in &spec.pipes {
            let d = p.length / c_cold * rate;
            lines.push(Line::new(d));
            lines.push(Line::new(d));
        }
        let mut nodes = Vec::with_capacity(spec.nodes.len());
        let mut ends = Vec::with_capacity(2 * spec.pipes.len());
        let mut valve_node = vec![0usize; cylinders];
        for (i, n) in spec.nodes.iter().enumerate() {
            let first = ends.len();
            for (j, p) in spec.pipes.iter().enumerate() {
                if p.a as usize == i {
                    ends.push(2 * j as u32);
                }
                if p.b as usize == i {
                    ends.push(2 * j as u32 + 1);
                }
            }
            let kind = match *n {
                NodeSpec::Junction => Kind::Junction,
                NodeSpec::Valve { cylinder } => {
                    valve_node[cylinder as usize] = i;
                    Kind::Valve { cylinder: cylinder as usize }
                }
                NodeSpec::Orifice { r, k, channel, .. } => Kind::Orifice { r, k, channel },
                NodeSpec::Open { radius, .. } => {
                    Kind::Open { radius, b0: -1.0, b1: 0.0, a1: 0.0, x1: 0.0, y1: 0.0, q_prev: 0.0 }
                }
            };
            let mut hiss = Hiss::default();
            match *n {
                NodeSpec::Valve { .. } => {
                    // a valve seat: sonic, millimetres -- white in the audio
                    // band, and the size plays no part
                    hiss.amount = spec.valve_noise;
                    hiss.area = 1.0e-6;
                }
                NodeSpec::Orifice { throat, noise, .. } if throat > 0.0 => {
                    hiss.amount = noise;
                    hiss.area = throat;
                }
                NodeSpec::Open { radius, noise } => {
                    hiss.amount = noise;
                    hiss.area = core::f64::consts::PI * radius * radius;
                }
                _ => {}
            }
            hiss.rho = 1.0;
            hiss.a_lp = 1.0;
            hiss.gain = 1.0;
            nodes.push(Node {
                kind,
                first,
                count: ends.len() - first,
                z: 0.0,
                hiss,
                mach_per_q: 0.0,
                steady: 0.0,
                r_now: 0.0,
                k_now: 0.0,
                d_r: 0.0,
                d_k: 0.0,
                ramp: 0,
            });
        }
        let mut net = Network {
            rate,
            t_ambient: spec.t_ambient,
            pipes: spec.pipes.iter().map(|p| Pipe { spec: *p, t: 0.0 }).collect(),
            lines,
            nodes,
            ends,
            arriving: vec![0.0; 2 * spec.pipes.len()],
            y: vec![0.0; 2 * spec.pipes.len()],
            valve_node,
            port: vec![PortView::default(); cylinders],
            t_port: 0.0,
            rng: 0x2545_F491_4F6C_DD1D,
            k_mean: 1.0 - math::exp(-1.0 / (0.020 * rate)),
            k_slow: 1.0 - math::exp(-1.0 / (0.3 * rate)),
            steepening: true,
            q_out: 0.0,
            dq_out: 0.0,
        };
        net.set_gas(&gas, 700.0, &[1.0; ORIFICE_CHANNELS], true);
        Ok(net)
    }

    /// Silence, and no flow anywhere.
    pub fn clear(&mut self) {
        for l in &mut self.lines {
            l.clear();
        }
        for n in &mut self.nodes {
            if let Kind::Open { x1, y1, q_prev, .. } = &mut n.kind {
                *x1 = 0.0;
                *y1 = 0.0;
                *q_prev = 0.0;
            }
            n.hiss.q_mean = 0.0;
            n.hiss.q_abs = 0.0;
            n.hiss.lp = 0.0;
            n.hiss.lp2 = 0.0;
            n.hiss.hp = 0.0;
            n.steady = 0.0;
        }
        self.rng = 0x2545_F491_4F6C_DD1D;
        self.q_out = 0.0;
        self.dq_out = 0.0;
    }

    pub fn pipes(&self) -> usize {
        self.pipes.len()
    }

    /// The wave arriving this sample at an end of pipe `i` (its downstream
    /// end if `downstream`), Pa.
    pub fn arriving(&self, i: usize, downstream: bool) -> f64 {
        self.arriving[2 * i + downstream as usize]
    }

    /// Large waves steepen unless this is turned off (the audits of the
    /// linear pipe: exact mass accounting, textbook transmission loss).
    pub fn set_steepening(&mut self, on: bool) {
        self.steepening = on;
        if !on {
            for l in &mut self.lines {
                l.k_nl = 0.0;
            }
        }
    }

    /// Pipe `i`: its gas temperature (K), its delay (samples) and the
    /// pressure at its upstream end (Pa about the outside) this sample.
    pub fn pipe_state(&self, i: usize) -> (f64, f64, f64) {
        // at end a: the wave arriving plus the one last sent
        let sent = self.lines[2 * i].buf[self.lines[2 * i].w.wrapping_sub(1) & self.lines[2 * i].mask];
        (self.pipes[i].t, self.lines[2 * i].delay, self.arriving[2 * i] + sent)
    }

    /// The gas in the pipes follows the gas in the port (`t_port`, K), and
    /// the controllable orifices follow their channels.  Called once a
    /// control block; a pipe warms or cools at most 0.05 K a call (75 K/s)
    /// unless `jump`, and its delay glides there.
    pub fn set_gas(
        &mut self,
        gas: &GasModel,
        t_port: f64,
        orifice: &[f64; ORIFICE_CHANNELS],
        jump: bool,
    ) {
        let over = if jump { 0 } else { RAMP };
        for n in &mut self.nodes {
            if let Kind::Orifice { r, k, channel } = n.kind {
                let m = if channel >= 0 { orifice[channel as usize] } else { 1.0 };
                if jump {
                    n.r_now = r * m;
                    n.k_now = k * m;
                    n.ramp = 0;
                } else {
                    n.d_r = (r * m - n.r_now) / RAMP as f64;
                    n.d_k = (k * m - n.k_now) / RAMP as f64;
                    n.ramp = if n.d_r != 0.0 || n.d_k != 0.0 { RAMP } else { 0 };
                }
            }
            n.hiss.retune(self.rate);
        }
        if self.steepening {
            // (gamma + 1) / (2 gamma p) of the gas in each pipe, at the
            // pressure it stands at now: the outside plus its two waves'
            // steady parts
            for (i, p) in self.pipes.iter().enumerate() {
                if p.t <= 0.0 {
                    continue;
                }
                let g = gas.gamma(p.t);
                let stands = 101_325.0 + self.lines[2 * i].dc + self.lines[2 * i + 1].dc;
                let k = (g + 1.0) / (2.0 * g * stands.max(30_000.0));
                self.lines[2 * i].k_nl = k;
                self.lines[2 * i + 1].k_nl = k;
            }
        }
        if t_port == self.t_port && !jump {
            return;
        }
        let mut settled = true;
        for (i, p) in self.pipes.iter_mut().enumerate() {
            let want = self.t_ambient + p.spec.heat * (t_port - self.t_ambient).max(0.0);
            let t = if jump { want } else { p.t + (want - p.t).clamp(-0.05, 0.05) };
            if t != want {
                settled = false;
            }
            if t == p.t {
                continue;
            }
            p.t = t;
            let c = c_of(gas, t);
            let y = p.spec.area / c;
            self.y[2 * i] = y;
            self.y[2 * i + 1] = y;
            // the loss of one pass: the wall (visco-thermal,
            // 2.98e-5 sqrt(f) / a nepers per metre in air at 300 K, growing
            // as T^0.35) plus what the spec adds
            let radius = (p.spec.area / core::f64::consts::PI).sqrt();
            let wall = 9.42e-4 / radius * math::powf(t / 300.0, 0.35);
            let kappa = (wall + p.spec.loss_db / 8.686) * p.spec.length;
            let b = loss_pole(kappa, self.rate);
            let d = p.spec.length / c * self.rate;
            for l in &mut self.lines[2 * i..2 * i + 2] {
                l.aim_delay(d, over);
                l.b = b;
            }
        }
        self.t_port = if settled { t_port } else { 0.0 };
        for n in &mut self.nodes {
            let e = &self.ends[n.first..n.first + n.count];
            let sum: f64 = e.iter().map(|&k| self.y[k as usize]).sum();
            n.z = 1.0 / sum;
            // the gas a hiss is made in: that of the first pipe at the node,
            // at the pressure outside
            let pipe = &self.pipes[(e[0] / 2) as usize];
            n.hiss.rho = 101_325.0 / (R_GAS * pipe.t);
            // Mach number of the gas in that pipe per kg/s through it
            n.mach_per_q = 1.0 / (n.hiss.rho * pipe.spec.area * c_of(gas, pipe.t));
            if let Kind::Open { radius, b0, b1, a1, .. } = &mut n.kind {
                // An unflanged end at low ka (Levine and Schwinger 1948): the
                // air outside is a mass 0.6133 a long beside a resistance
                // 1.504 rho c / A, which gives the reflection
                //   R(s) = (s tau (r - 1) - r) / (s tau (r + 1) + r)
                // seen from the hot gas in the pipe (r, tau in its units).
                let pipe = &self.pipes[(e[0] / 2) as usize];
                let c = c_of(gas, pipe.t);
                let th = pipe.t / self.t_ambient;
                let res = 1.504 * th.sqrt();
                let tau = 0.6133 * *radius * th / c;
                let s = 2.0 * self.rate * tau;
                let d0 = s * (res + 1.0) + res;
                *b0 = (s * (res - 1.0) - res) / d0;
                *b1 = (-s * (res - 1.0) - res) / d0;
                *a1 = (-s * (res + 1.0) + res) / d0;
            }
        }
    }

    /// Start of a sample: what has arrived at every pipe end, and what each
    /// valve node shows its cylinder.
    #[inline]
    pub fn arrive(&mut self) {
        for i in 0..self.pipes.len() {
            // line 2i left end 2i (node a): it arrives at end 2i + 1
            self.arriving[2 * i + 1] = self.lines[2 * i].read(self.k_slow);
            self.arriving[2 * i] = self.lines[2 * i + 1].read(self.k_slow);
        }
        for (k, &ni) in self.valve_node.iter().enumerate() {
            let n = &self.nodes[ni];
            let mut s = 0.0;
            for &e in &self.ends[n.first..n.first + n.count] {
                s += self.y[e as usize] * self.arriving[e as usize];
            }
            self.port[k] = PortView { p: 2.0 * s * n.z, z: n.z };
        }
    }

    #[inline(always)]
    pub fn port(&self, cylinder: usize) -> PortView {
        self.port[cylinder]
    }

    /// End of a sample: every node sends its waves back into the pipes.
    /// `q_valve[k]` is what cylinder k pushed into its port, kg/s.
    #[inline]
    pub fn scatter(&mut self, q_valve: &[f64]) {
        let mut q_out = 0.0;
        let mut dq_out = 0.0;
        let k_mean = self.k_mean;
        for n in self.nodes.iter_mut() {
            let ends = &self.ends[n.first..n.first + n.count];
            // this place's turbulence, if it has any
            let hiss = if n.hiss.amount > 0.0 {
                // xorshift64*, to a uniform number of unit variance
                let mut x = self.rng;
                x ^= x >> 12;
                x ^= x << 25;
                x ^= x >> 27;
                self.rng = x;
                let u = (x.wrapping_mul(0x2545_F491_4F6C_DD1D) >> 11) as f64
                    * (1.0 / 9007199254740992.0);
                (u - 0.5) * 3.464_101_615_137_754_6
            } else {
                0.0
            };
            match n.kind {
                Kind::Junction | Kind::Valve { .. } => {
                    let mut s = 0.0;
                    for &e in ends {
                        s += self.y[e as usize] * self.arriving[e as usize];
                    }
                    let q = match n.kind {
                        // the jet through the seat is turbulent: what it
                        // delivers to the port fluctuates about what the
                        // cylinder lost (no mass on average)
                        Kind::Valve { cylinder } => {
                            let q = q_valve[cylinder];
                            if n.hiss.amount > 0.0 {
                                q + n.hiss.amount * q.abs() * n.hiss.next(hiss, q, k_mean)
                            } else {
                                q
                            }
                        }
                        _ => 0.0,
                    };
                    let p = (2.0 * s + q) * n.z;
                    for &e in ends {
                        self.lines[e as usize].write(p - self.arriving[e as usize]);
                    }
                }
                Kind::Orifice { .. } => {
                    if n.ramp > 0 {
                        n.ramp -= 1;
                        n.r_now += n.d_r;
                        n.k_now += n.d_k;
                    }
                    let (r_now, k_now) = (n.r_now, n.k_now);
                    // flow q from end 1 to end 2:
                    //   p1 = 2 a1 - z1 q,  p2 = 2 a2 + z2 q,  p1 - p2 = r q + k q|q|
                    let (e1, e2) = (ends[0] as usize, ends[1] as usize);
                    let (a1, a2) = (self.arriving[e1], self.arriving[e2]);
                    let (z1, z2) = (1.0 / self.y[e1], 1.0 / self.y[e2]);
                    let e = 2.0 * (a1 - a2);
                    let zs = z1 + z2 + r_now;
                    // the turbulent loss, fluctuating
                    let k = if n.hiss.amount > 0.0 {
                        let f = n.hiss.amount * n.hiss.next(hiss, n.hiss.q_mean, 0.0);
                        k_now * (1.0 + f).max(0.0)
                    } else {
                        k_now
                    };
                    let q = 2.0 * e / (zs + (zs * zs + 4.0 * k * e.abs()).sqrt());
                    n.hiss.q_mean += k_mean * (q - n.hiss.q_mean);
                    n.hiss.q_abs += k_mean * (q.abs() - n.hiss.q_abs);
                    self.lines[e1].write(a1 - z1 * q);
                    self.lines[e2].write(a2 + z2 * q);
                }
                Kind::Open { .. } => {
                    if let Kind::Open { b0, b1, a1, x1, y1, q_prev, .. } = &mut n.kind {
                        let e = ends[0] as usize;
                        // the dynamic head of the gas leaving is lost there,
                        // and that loss fluctuates: a pressure source s in
                        // series with the air outside,
                        //   back = R (a - s/2) + s/2
                        let a_in = self.arriving[e];
                        let was = *q_prev;
                        let s = if n.hiss.amount > 0.0 && was > 0.0 {
                            let head = was * was / (2.0 * n.hiss.rho * n.hiss.area * n.hiss.area);
                            n.hiss.amount * head * n.hiss.next(hiss, was, k_mean)
                        } else {
                            n.hiss.q_mean += k_mean * (was - n.hiss.q_mean);
                            n.hiss.q_abs += k_mean * (was.abs() - n.hiss.q_abs);
                            0.0
                        };
                        let a = a_in - 0.5 * s;
                        let refl = *b0 * a + *b1 * *x1 - *a1 * *y1;
                        *x1 = a;
                        *y1 = refl;
                        n.steady += k_mean * (refl - n.steady);
                        // gas streaming out at Mach M (its 20 ms mean) takes
                        // sound with it: (1 - M) / (1 + M) comes back
                        let m = (n.hiss.q_mean.max(0.0) * n.mach_per_q).min(0.6);
                        let back = n.steady + (1.0 - m) / (1.0 + m) * (refl - n.steady) + 0.5 * s;
                        self.lines[e].write(back);
                        let q = (a_in - back) * self.y[e];
                        q_out += q;
                        dq_out += (q - *q_prev) * self.rate;
                        *q_prev = q;
                    }
                }
            }
        }
        self.q_out = q_out;
        self.dq_out = dq_out;
    }

    /// Mass of gas the waves hold above the resting state, kg (for the
    /// conservation tests).  A wave of pressure w fills the c dt of pipe it
    /// crosses in a sample with w A / c^2 per metre: w (A / c) dt.
    pub fn stored_mass(&self) -> f64 {
        let mut m = 0.0;
        for i in 0..self.pipes.len() {
            let per = self.y[2 * i] / self.rate;
            m += (self.lines[2 * i].held() + self.lines[2 * i + 1].held()) * per;
        }
        m
    }
}

/// The pole of a one-pole low-pass (gain 1 at 0 Hz) that loses `kappa`
/// nepers at 1 kHz, standing for a loss that grows as sqrt(f): matched at
/// 2 kHz.
fn loss_pole(kappa: f64, rate: f64) -> f64 {
    if kappa <= 0.0 {
        return 0.0;
    }
    let f = 2000.0_f64.min(0.2 * rate);
    let rho2 = math::exp(-2.0 * kappa * (f / 1000.0).sqrt());
    let (_, cw) = math::sincos_deg(360.0 * f / rate);
    let a = 1.0 - rho2;
    if a < 1.0e-12 {
        return 0.0;
    }
    let h = 1.0 - rho2 * cw;
    ((h - (h * h - a * a).max(0.0).sqrt()) / a).clamp(0.0, 0.9995)
}
