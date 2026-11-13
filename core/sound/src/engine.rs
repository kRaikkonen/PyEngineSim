//! The engine: a crankshaft turning at the speed it is told, and the
//! cylinders hanging on it.
//!
//! docs/REDESIGN_V2.md.  Stage S1: the cylinders and their valve flows.
//! Stage S2: the exhaust they discharge into, a waveguide network
//! (`attach_exhaust`); without one each exhaust port is a plenum at the
//! pressure the operating point names.

use std::sync::Arc;

use crate::control::{Control, Inbox};
use crate::cylinder::{burn_window, Cylinder, Port, StepEnv};
use crate::gas::{GasModel, P_ATM};
use crate::math;
use crate::network::{Network, NetworkSpec};
use crate::spec::{EngineSpec, Geom, SpecError, ValveCurve};

/// The operating point is read, and its ramps re-aimed, every so many
/// samples: 0.67 ms at 48 kHz.  A property of the engine's own sample count,
/// not of the block length the host renders in.
pub const CONTROL_BLOCK: usize = 32;

/// Sound pressure in Pa that maps to full scale when the operating point
/// names no reference level.
pub const FULL_SCALE_PA: f64 = 200.0;

/// The level the reference sound pressure plays at, rms of full scale.
pub const REFERENCE_LEVEL: f64 = 0.2;

/// The loudness curve never lifts a sound more than this far below the
/// reference (silence is not amplified into noise): -30 dB.
const LEVEL_FLOOR: f64 = 0.0316;

/// The operating point is approached, not jumped to: whoever publishes it
/// does so in steps (a simulator at 60 Hz, a car link slower still), and a
/// staircase of engine speed is a warble.  s.
const EASE_TIME: f64 = 0.008;

/// Time constant of the level the loudness curve reads, s.
const LEVEL_TIME: f64 = 0.25;

/// The loudness curve reads the level as an ear would, not as a microphone:
/// two high-pass poles here stand for the A weighting below 1 kHz (-28 dB
/// at 50 Hz, -17 at 100, -8 at 200, -2 at 500).  An idling four makes most
/// of its sound pressure at 30 Hz, where nobody hears it.
const EAR_HZ: f64 = 250.0;

/// Whatever the loudness curve asks, the sound as a microphone hears it is
/// not played above this rms of full scale: a low engine speed at full load
/// is mostly bass, quiet to the ear and huge to the loudspeaker.
const HEADROOM_RMS: f64 = 0.25;

/// ...nor may its peaks (held for a second) pass this: a steep-fronted
/// pulse train has peaks five times its rms, and the limiter is there to
/// protect, not to shape every cycle.
const HEADROOM_PEAK: f64 = 0.85;
const PEAK_HOLD: f64 = 1.0;

/// Nothing below this is played (two poles): no loudspeaker moves there,
/// and steady flow is not sound.
const RUMBLE_HZ: f64 = 18.0;

/// The ground.  Sources a third of a metre up, an ear at 1.2 m, a metre or
/// two away: the sound also arrives off the road, 1.3 ms later and a little
/// weaker (asphalt returns nearly all of it; the longer path and the angle
/// take the rest).  Without it the engine plays in a void.
const GROUND_DELAY: f64 = 0.0013;
const GROUND_GAIN: f64 = 0.6;
const GROUND_MAX: usize = 256;

/// The block.  The pressure in the cylinders shakes the structure, and the
/// structure radiates: "combustion noise".  Engine structures pass a band
/// of roughly 0.5-3 kHz and attenuate cylinder pressure by some 95 dB on
/// the way to a microphone at 1 m (the range of the classical structure
/// attenuation curves; a stiff racing block and a road car differ by
/// several dB either way -- one figure here, an estimate).  The listener is
/// taken 1.5 m from the block.
const BLOCK_LOW_HZ: f64 = 500.0;
const BLOCK_HIGH_HZ: f64 = 3000.0;
/// (A bare engine on a test bed is that loud -- louder than its exhaust.  In
/// a car the bay, the undertray and the body stand between it and someone
/// behind the car: a further 20 dB, again an estimate.)
const BLOCK_GAIN: f64 = 1.8e-6 / 1.5;

/// What the engine's valves pass in one sample, all cylinders together.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Sample {
    /// out through the exhaust valves, kg/s
    pub q_exhaust: f64,
    /// in through the intake valves, kg/s
    pub q_intake: f64,
    /// out of the open ends of the exhaust, kg/s (no network: `q_exhaust`)
    pub q_tail: f64,
    /// what that radiates: sound pressure 1 m from the open ends, Pa
    pub p_far: f64,
}

pub struct Engine {
    geom: Geom,
    gas: GasModel,
    exhaust_curve: ValveCurve,
    intake_curve: ValveCurve,
    wall_temp: f64,
    heat_scale: f64,
    wall_refresh_deg: f64,
    firing_deg: Vec<f64>,
    /// (sin, cos) of each firing angle: one sine and cosine a sample serve all cylinders
    firing_sc: Vec<(f64, f64)>,
    cyl: Vec<Cylinder>,

    rate: f64,
    dt: f64,
    /// the engine's cycle angle, [0, 720)
    crank: f64,
    samples: u64,

    inbox: Arc<Inbox<Control>>,
    target: Control,
    pos: usize,
    // ramped inside a control block
    rpm: f64,
    d_rpm: f64,
    p_intake: f64,
    d_p_intake: f64,
    p_exhaust: f64,
    d_p_exhaust: f64,
    // constant inside a control block
    /// one exhaust port per cylinder
    exhaust_ports: Vec<Port>,
    intake_port: Port,
    exhaust_open: f64,
    intake_open: f64,
    speed_term: f64,
    burn_start: f64,
    burn_len: f64,

    exhaust_net: Option<Network>,
    q_valve: Vec<f64>,
    /// the intake side: its network, one port per cylinder, and what each
    /// cylinder pushed back into its port (minus what it drew)
    intake_net: Option<Network>,
    intake_ports: Vec<Port>,
    q_intake_back: Vec<f64>,
    block_hp: [f64; 2],
    block_lp: [f64; 2],
    block_k: (f64, f64),
    ground: [f64; GROUND_MAX],
    ground_at: usize,
    ground_lag: usize,
    q_exhaust_prev: f64,

    /// share of the way to the target covered in one control block
    ease: f64,
    orifice: [f64; crate::network::ORIFICE_CHANNELS],

    // the output stage
    level_ms: f64,
    raw_ms: f64,
    peak: f64,
    peak_k: f64,
    level_k: f64,
    ear_k: f64,
    ear: [f64; 2],
    rumble_k: f64,
    rumble: [f64; 2],
    gain: f64,
    d_gain: f64,
}

impl Engine {
    pub fn new(spec: &EngineSpec, sample_rate: f64) -> Result<Self, SpecError> {
        spec.validate()?;
        if !(sample_rate.is_finite() && (8_000.0..=768_000.0).contains(&sample_rate)) {
            return Err(SpecError(format!("sample rate {sample_rate} is not in 8k..768k")));
        }
        let geom = Geom::new(spec);
        let gas = GasModel { gamma_const: spec.gamma_const };
        let target = Control::default();
        let mut e = Engine {
            geom,
            gas,
            exhaust_curve: spec.exhaust.clone(),
            intake_curve: spec.intake.clone(),
            wall_temp: spec.wall_temp,
            heat_scale: spec.heat_scale,
            wall_refresh_deg: spec.wall_refresh_deg,
            firing_deg: spec.firing_deg.clone(),
            firing_sc: spec.firing_deg.iter().map(|a| math::sincos_deg(*a)).collect(),
            cyl: Vec::with_capacity(spec.firing_deg.len()),
            rate: sample_rate,
            dt: 1.0 / sample_rate,
            crank: 0.0,
            samples: 0,
            inbox: Arc::new(Inbox::new(target)),
            target,
            pos: 0,
            rpm: target.rpm,
            d_rpm: 0.0,
            p_intake: target.p_intake,
            d_p_intake: 0.0,
            p_exhaust: target.p_exhaust,
            d_p_exhaust: 0.0,
            exhaust_ports: vec![
                Port::new(&gas, target.p_exhaust, target.t_exhaust, 0.0);
                spec.firing_deg.len()
            ],
            intake_port: Port::new(&gas, target.p_intake, target.t_intake, 0.0),
            exhaust_open: spec.exhaust.open_deg,
            intake_open: spec.intake.open_deg,
            speed_term: 1.0,
            burn_start: 0.0,
            burn_len: 1.0,
            exhaust_net: None,
            q_valve: vec![0.0; spec.firing_deg.len()],
            intake_net: None,
            intake_ports: vec![
                Port::new(&gas, target.p_intake, target.t_intake, 0.0);
                spec.firing_deg.len()
            ],
            q_intake_back: vec![0.0; spec.firing_deg.len()],
            block_hp: [0.0; 2],
            block_lp: [0.0; 2],
            block_k: (
                1.0 - math::exp(-2.0 * core::f64::consts::PI * BLOCK_LOW_HZ / sample_rate),
                1.0 - math::exp(-2.0 * core::f64::consts::PI * BLOCK_HIGH_HZ / sample_rate),
            ),
            ground: [0.0; GROUND_MAX],
            ground_at: 0,
            ground_lag: ((GROUND_DELAY * sample_rate).round() as usize).clamp(1, GROUND_MAX - 1),
            q_exhaust_prev: 0.0,
            ease: 1.0 - math::exp(-(CONTROL_BLOCK as f64) / (EASE_TIME * sample_rate)),
            orifice: target.orifice,
            level_ms: 0.0,
            raw_ms: 0.0,
            peak: 0.0,
            peak_k: math::exp(-1.0 / (PEAK_HOLD * sample_rate)),
            level_k: 1.0 - math::exp(-1.0 / (LEVEL_TIME * sample_rate)),
            ear_k: 1.0 - math::exp(-2.0 * core::f64::consts::PI * EAR_HZ / sample_rate),
            ear: [0.0; 2],
            rumble_k: 1.0 - math::exp(-2.0 * core::f64::consts::PI * RUMBLE_HZ / sample_rate),
            rumble: [0.0; 2],
            gain: 1.0 / FULL_SCALE_PA,
            d_gain: 0.0,
        };
        e.reset();
        Ok(e)
    }

    /// Give the exhaust valves a network of pipes to discharge into.  With
    /// one, `Control::p_exhaust` is the pressure at its open ends (the air
    /// outside) and the pressure in the ports is the network's own.
    /// Allocates: not for the audio thread.
    pub fn attach_exhaust(&mut self, spec: &NetworkSpec) -> Result<(), SpecError> {
        let mut net = Network::new(spec, self.firing_deg.len(), self.rate)?;
        net.set_gas(&self.gas, self.target.t_exhaust, &self.target.orifice, true);
        self.exhaust_net = Some(net);
        Ok(())
    }

    /// Give the intake valves a network to draw from: runners, a plenum, a
    /// throttle (an orifice on a control channel), an airbox, a mouth open
    /// to the air.  With one, the pressure in the intake ports is the
    /// network's own, about `Control::p_exhaust` (the air outside), and
    /// `Control::p_intake` no longer feeds the valves.
    pub fn attach_intake(&mut self, spec: &NetworkSpec) -> Result<(), SpecError> {
        let mut net = Network::new(spec, self.firing_deg.len(), self.rate)?;
        net.set_gas(&self.gas, self.target.t_intake, &self.target.orifice, true);
        self.intake_net = Some(net);
        Ok(())
    }

    pub fn exhaust(&self) -> Option<&Network> {
        self.exhaust_net.as_ref()
    }

    /// Back to a stopped, cold engine at crank angle 0 (the operating point
    /// in force stays).
    pub fn reset(&mut self) {
        self.crank = 0.0;
        self.samples = 0;
        self.pos = 0;
        self.q_exhaust_prev = 0.0;
        self.level_ms = 0.0;
        self.raw_ms = 0.0;
        self.peak = 0.0;
        self.ear = [0.0; 2];
        self.rumble = [0.0; 2];
        if let Some(net) = &mut self.exhaust_net {
            net.clear();
        }
        if let Some(net) = &mut self.intake_net {
            net.clear();
        }
        self.ground = [0.0; GROUND_MAX];
        self.block_hp = [0.0; 2];
        self.block_lp = [0.0; 2];
        self.cyl.clear();
        for k in 0..self.firing_deg.len() {
            let th = math::wrap720_near(self.crank - self.firing_deg[k]);
            let a_in = self.intake_curve.area_after(math::wrap720_near(th - self.intake_open));
            let mut c = Cylinder::new(&self.geom, th, P_ATM, 400.0, a_in);
            c.seed(0x0123_4567_89AB_CDEF_u64.wrapping_mul(2 * k as u64 + 1));
            self.cyl.push(c);
        }
    }

    /// The mailbox of this engine's operating point: publish from any thread.
    pub fn inbox(&self) -> Arc<Inbox<Control>> {
        self.inbox.clone()
    }

    /// Publish an operating point (same as `inbox().publish`).
    pub fn set_control(&self, c: &Control) -> bool {
        self.inbox.publish(*c)
    }

    /// Jump straight to an operating point, no ramp (tests, priming).
    pub fn force_control(&mut self, c: &Control) {
        if let Some(c) = c.sanitized() {
            let _ = self.inbox.take();
            self.target = c;
            self.rpm = c.rpm;
            self.p_intake = c.p_intake;
            self.p_exhaust = c.p_exhaust;
            self.pos = 0;
            self.orifice = c.orifice;
            if let Some(net) = &mut self.exhaust_net {
                net.set_gas(&self.gas, c.t_exhaust, &c.orifice, true);
            }
            if let Some(net) = &mut self.intake_net {
                net.set_gas(&self.gas, c.t_intake, &c.orifice, true);
            }
            self.begin_block();
            self.d_rpm = 0.0;
            self.d_p_intake = 0.0;
            self.d_p_exhaust = 0.0;
            self.gain += self.d_gain * CONTROL_BLOCK as f64;
            self.d_gain = 0.0;
        }
    }

    pub fn sample_rate(&self) -> f64 {
        self.rate
    }

    pub fn cylinders(&self) -> &[Cylinder] {
        &self.cyl
    }

    pub fn cylinder_mut(&mut self, k: usize) -> &mut Cylinder {
        &mut self.cyl[k]
    }

    pub fn geom(&self) -> &Geom {
        &self.geom
    }

    pub fn crank_deg(&self) -> f64 {
        self.crank
    }

    pub fn rpm(&self) -> f64 {
        self.rpm
    }

    /// Samples rendered since the last reset.
    pub fn samples(&self) -> u64 {
        self.samples
    }

    /// Place the crank (tests; the cylinders' gas keeps its mass and
    /// temperature, in the volume of the new angle).
    pub fn set_crank(&mut self, deg: f64) {
        self.crank = math::wrap720(deg);
        for k in 0..self.cyl.len() {
            let th = math::wrap720_near(self.crank - self.firing_deg[k]);
            let c = &mut self.cyl[k];
            c.v = self.geom.volume(th);
            c.p = c.m * crate::gas::R_GAS * c.t / c.v;
        }
    }

    /// What the exhaust port presents to the valve: `z` in Pa s/kg is how far
    /// its pressure rises per kg/s pushed in (0: a plenum).  The seam the
    /// exhaust network of S2 attaches to.
    pub fn set_port_impedance(&mut self, exhaust_z: f64, intake_z: f64) {
        for p in &mut self.exhaust_ports {
            p.z = exhaust_z.max(0.0);
        }
        self.intake_port.z = intake_z.max(0.0);
    }

    #[inline]
    fn begin_block(&mut self) {
        if let Some(c) = self.inbox.take().and_then(|c| c.sanitized()) {
            self.target = c;
        }
        let n = CONTROL_BLOCK as f64;
        let t = &self.target;
        let k = self.ease / n;
        self.d_rpm = (t.rpm - self.rpm) * k;
        self.d_p_intake = (t.p_intake - self.p_intake) * k;
        self.d_p_exhaust = (t.p_exhaust - self.p_exhaust) * k;
        for (now, want) in self.orifice.iter_mut().zip(t.orifice.iter()) {
            *now += (want - *now) * self.ease;
        }
        for p in &mut self.exhaust_ports {
            p.set_temperature(&self.gas, t.t_exhaust);
        }
        self.intake_port.set_temperature(&self.gas, t.t_intake);
        for p in &mut self.intake_ports {
            p.set_temperature(&self.gas, t.t_intake);
        }
        if let Some(net) = &mut self.exhaust_net {
            net.set_gas(&self.gas, t.t_exhaust, &self.orifice, false);
        }
        if let Some(net) = &mut self.intake_net {
            net.set_gas(&self.gas, t.t_intake, &self.orifice, false);
        }
        // the loudness curve (s.6.6): played level = reference
        // x (physical level / reference)^dynamics
        let want = if t.level_ref > 0.0 {
            let rel = (self.level_ms.sqrt() / t.level_ref).max(LEVEL_FLOOR);
            let g = REFERENCE_LEVEL / t.level_ref * math::powf(rel, t.dynamics - 1.0);
            let raw = self.raw_ms.sqrt();
            let g = if g * raw > HEADROOM_RMS { HEADROOM_RMS / raw } else { g };
            t.volume * if g * self.peak > HEADROOM_PEAK { HEADROOM_PEAK / self.peak } else { g }
        } else {
            t.volume / FULL_SCALE_PA
        };
        self.d_gain = (want - self.gain) / n;
        self.exhaust_open = math::wrap720(self.exhaust_curve.open_deg + t.exhaust_phase_deg);
        self.intake_open = math::wrap720(self.intake_curve.open_deg + t.intake_phase_deg);
        let piston_speed = 2.0 * self.geom.stroke * t.rpm / 60.0;
        self.speed_term = math::powf(piston_speed + 1.4, 0.8);
        (self.burn_start, self.burn_len) = burn_window(t.ca50_deg, t.burn_deg);
    }

    /// One sample.
    #[inline]
    pub fn tick(&mut self) -> Sample {
        if self.pos == 0 {
            self.begin_block();
        }
        self.pos += 1;
        if self.pos == CONTROL_BLOCK {
            self.pos = 0;
        }
        self.rpm += self.d_rpm;
        self.p_intake += self.d_p_intake;
        self.p_exhaust += self.d_p_exhaust;
        self.gain += self.d_gain;
        self.intake_port.p_in = self.p_intake;
        match &mut self.exhaust_net {
            Some(net) => {
                net.arrive();
                let floor = 0.2 * self.p_exhaust;
                for (k, p) in self.exhaust_ports.iter_mut().enumerate() {
                    let v = net.port(k);
                    p.p_in = (self.p_exhaust + v.p).max(floor);
                    p.z = v.z;
                }
            }
            None => {
                for p in &mut self.exhaust_ports {
                    p.p_in = self.p_exhaust;
                }
            }
        }
        match &mut self.intake_net {
            Some(net) => {
                net.arrive();
                let floor = 0.05 * self.p_exhaust;
                for (k, p) in self.intake_ports.iter_mut().enumerate() {
                    let v = net.port(k);
                    p.p_in = (self.p_exhaust + v.p).max(floor);
                    p.z = v.z;
                }
            }
            None => {
                for p in &mut self.intake_ports {
                    p.p_in = self.p_intake;
                    p.z = self.intake_port.z;
                }
            }
        }

        let dtheta = 6.0 * self.rpm * self.dt;
        let mut crank = self.crank + dtheta;
        if crank >= 720.0 {
            crank -= 720.0;
        }
        self.crank = crank;
        self.samples += 1;

        let mut env = StepEnv {
            geom: &self.geom,
            gas: &self.gas,
            exhaust_curve: &self.exhaust_curve,
            intake_curve: &self.intake_curve,
            exhaust_open: self.exhaust_open,
            intake_open: self.intake_open,
            exhaust: &self.exhaust_ports[0],
            intake: &self.intake_port,
            dt: self.dt,
            dtheta,
            speed_term: self.speed_term,
            wall_temp: self.wall_temp,
            heat_scale: self.heat_scale,
            wall_refresh_deg: self.wall_refresh_deg,
            charge: self.target.charge,
            lambda: self.target.lambda,
            burn_start: self.burn_start,
            burn_len: self.burn_len,
            ccv: self.target.ccv,
        };
        let cut = self.target.fuel_cut;
        let mut out = Sample::default();
        let (s, c0) = math::sincos_deg(crank);
        for (k, c) in self.cyl.iter_mut().enumerate() {
            let th = math::wrap720_near(crank - self.firing_deg[k]);
            // sin and cos of (crank - firing angle)
            let (sf, cf) = self.firing_sc[k];
            let sc = (s * cf - c0 * sf, c0 * cf + s * sf);
            env.exhaust = &self.exhaust_ports[k];
            env.intake = &self.intake_ports[k];
            let (qe, qi) = c.step(&env, th, sc, (cut >> k) & 1 == 1);
            self.q_valve[k] = qe;
            self.q_intake_back[k] = -qi;
            out.q_exhaust += qe;
            out.q_intake += qi;
        }
        // what leaves the open ends is a monopole: p = (dq/dt) / (4 pi r)
        let dq = match &mut self.exhaust_net {
            Some(net) => {
                net.scatter(&self.q_valve);
                out.q_tail = net.q_out;
                net.dq_out
            }
            None => {
                out.q_tail = out.q_exhaust;
                (out.q_exhaust - self.q_exhaust_prev) * self.rate
            }
        };
        self.q_exhaust_prev = out.q_exhaust;
        // the intake mouth is a source too
        let dq = match &mut self.intake_net {
            Some(net) => {
                net.scatter(&self.q_intake_back);
                dq + net.dq_out
            }
            None => dq,
        };
        // the block: all the cylinder pressures, through the band the
        // structure passes
        let mut pc = 0.0;
        for c in &self.cyl {
            pc += c.p;
        }
        let (kh, kl) = self.block_k;
        self.block_hp[0] += kh * (pc - self.block_hp[0]);
        let h1 = pc - self.block_hp[0];
        self.block_hp[1] += kh * (h1 - self.block_hp[1]);
        let h2 = h1 - self.block_hp[1];
        self.block_lp[0] += kl * (h2 - self.block_lp[0]);
        self.block_lp[1] += kl * (self.block_lp[0] - self.block_lp[1]);
        let direct = dq * (1.0 / (4.0 * core::f64::consts::PI)) + BLOCK_GAIN * self.block_lp[1];
        // and all of it once more off the ground
        let at = self.ground_at;
        self.ground[at] = direct;
        let p = direct
            + GROUND_GAIN * self.ground[(at + GROUND_MAX - self.ground_lag) & (GROUND_MAX - 1)];
        self.ground_at = (at + 1) & (GROUND_MAX - 1);
        // what is played: without the rumble
        self.rumble[0] += self.rumble_k * (p - self.rumble[0]);
        let p1 = p - self.rumble[0];
        self.rumble[1] += self.rumble_k * (p1 - self.rumble[1]);
        out.p_far = p1 - self.rumble[1];
        // what the loudness curve reads: as an ear weights it
        self.ear[0] += self.ear_k * (out.p_far - self.ear[0]);
        let e1 = out.p_far - self.ear[0];
        self.ear[1] += self.ear_k * (e1 - self.ear[1]);
        let e = e1 - self.ear[1];
        self.level_ms += self.level_k * (e * e - self.level_ms);
        self.raw_ms += self.level_k * (out.p_far * out.p_far - self.raw_ms);
        self.peak = (self.peak * self.peak_k).max(out.p_far.abs());
        out
    }

    /// Run `cycles` engine cycles silently at the operating point in force,
    /// so the gas in the cylinders is that of a running engine.  Not for the
    /// audio thread.
    pub fn prime(&mut self, cycles: f64) {
        // (a crank that is barely turning: no more than ten seconds of it)
        let rpm = self.target.rpm.max(1.0);
        let n = (cycles * 120.0 / rpm).min(10.0) * self.rate;
        let n = n.ceil() as u64;
        for _ in 0..n {
            self.tick();
        }
    }

    /// The level the loudness curve reads now: Pa rms at 1 m, weighted as
    /// an ear weights it (`EAR_HZ`).
    pub fn level(&self) -> f64 {
        self.level_ms.sqrt()
    }

    /// Render audio: the sound pressure 1 m from the open ends of the
    /// exhaust (with no network: from the bare exhaust valves), through the
    /// loudness curve and a limiter that only protects, on both channels.
    pub fn render(&mut self, left: &mut [f32], right: &mut [f32]) {
        let n = left.len().min(right.len());
        for i in 0..n {
            let s = self.tick();
            let v = limit(s.p_far * self.gain) as f32;
            left[i] = v;
            right[i] = v;
        }
    }
}

/// Straight up to 0.7 of full scale, then bending so that nothing passes 1.
#[inline(always)]
fn limit(x: f64) -> f64 {
    const KNEE: f64 = 0.7;
    let a = x.abs();
    if a <= KNEE {
        return x;
    }
    // KNEE + (1 - KNEE) tanh((a - KNEE) / (1 - KNEE))
    let u = (a - KNEE) * (1.0 / (1.0 - KNEE));
    let th = if u > 20.0 { 1.0 } else { 1.0 - 2.0 / (math::exp(2.0 * u) + 1.0) };
    (KNEE + (1.0 - KNEE) * th).copysign(x)
}
