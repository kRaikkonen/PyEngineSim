//! The engine: a crankshaft turning at the speed it is told, and the
//! cylinders hanging on it.
//!
//! Stage S1 of docs/REDESIGN_V2.md: the cylinders and their valve flows are
//! here; the pipes they discharge into (S2) are not yet -- each port is a
//! plenum at the pressure the operating point names.

use std::sync::Arc;

use crate::control::{Control, Inbox};
use crate::cylinder::{burn_window, Cylinder, Port, StepEnv};
use crate::gas::{GasModel, P_ATM};
use crate::math;
use crate::spec::{EngineSpec, Geom, SpecError, ValveCurve};

/// The operating point is read, and its ramps re-aimed, every so many
/// samples: 0.67 ms at 48 kHz.  A property of the engine's own sample count,
/// not of the block length the host renders in.
pub const CONTROL_BLOCK: usize = 32;

/// PROVISIONAL (until the exhaust network of S2): sound pressure in Pa that
/// maps to full scale, for the bare-port monopole `render` writes.
pub const FULL_SCALE_PA: f64 = 200.0;

/// What the engine's valves pass in one sample, all cylinders together.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Sample {
    /// out through the exhaust valves, kg/s
    pub q_exhaust: f64,
    /// in through the intake valves, kg/s
    pub q_intake: f64,
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
    exhaust_port: Port,
    intake_port: Port,
    exhaust_open: f64,
    intake_open: f64,
    speed_term: f64,
    burn_start: f64,
    burn_len: f64,

    q_exhaust_prev: f64,
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
            exhaust_port: Port::new(&gas, target.p_exhaust, target.t_exhaust, 0.0),
            intake_port: Port::new(&gas, target.p_intake, target.t_intake, 0.0),
            exhaust_open: spec.exhaust.open_deg,
            intake_open: spec.intake.open_deg,
            speed_term: 1.0,
            burn_start: 0.0,
            burn_len: 1.0,
            q_exhaust_prev: 0.0,
        };
        e.reset();
        Ok(e)
    }

    /// Back to a stopped, cold engine at crank angle 0 (the operating point
    /// in force stays).
    pub fn reset(&mut self) {
        self.crank = 0.0;
        self.samples = 0;
        self.pos = 0;
        self.q_exhaust_prev = 0.0;
        self.cyl.clear();
        for k in 0..self.firing_deg.len() {
            let th = math::wrap720_near(self.crank - self.firing_deg[k]);
            let a_in = self.intake_curve.area_after(math::wrap720_near(th - self.intake_open));
            self.cyl.push(Cylinder::new(&self.geom, th, P_ATM, 400.0, a_in));
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
            self.begin_block();
            self.d_rpm = 0.0;
            self.d_p_intake = 0.0;
            self.d_p_exhaust = 0.0;
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
        self.exhaust_port.z = exhaust_z.max(0.0);
        self.intake_port.z = intake_z.max(0.0);
    }

    #[inline]
    fn begin_block(&mut self) {
        if let Some(c) = self.inbox.take().and_then(|c| c.sanitized()) {
            self.target = c;
        }
        let n = CONTROL_BLOCK as f64;
        let t = &self.target;
        self.d_rpm = (t.rpm - self.rpm) / n;
        self.d_p_intake = (t.p_intake - self.p_intake) / n;
        self.d_p_exhaust = (t.p_exhaust - self.p_exhaust) / n;
        self.exhaust_port.set_temperature(&self.gas, t.t_exhaust);
        self.intake_port.set_temperature(&self.gas, t.t_intake);
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
        self.exhaust_port.p_in = self.p_exhaust;
        self.intake_port.p_in = self.p_intake;

        let dtheta = 6.0 * self.rpm * self.dt;
        let mut crank = self.crank + dtheta;
        if crank >= 720.0 {
            crank -= 720.0;
        }
        self.crank = crank;
        self.samples += 1;

        let env = StepEnv {
            geom: &self.geom,
            gas: &self.gas,
            exhaust_curve: &self.exhaust_curve,
            intake_curve: &self.intake_curve,
            exhaust_open: self.exhaust_open,
            intake_open: self.intake_open,
            exhaust: &self.exhaust_port,
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
        };
        let cut = self.target.fuel_cut;
        let mut out = Sample::default();
        let (s, c0) = math::sincos_deg(crank);
        for (k, c) in self.cyl.iter_mut().enumerate() {
            let th = math::wrap720_near(crank - self.firing_deg[k]);
            // sin and cos of (crank - firing angle)
            let (sf, cf) = self.firing_sc[k];
            let sc = (s * cf - c0 * sf, c0 * cf + s * sf);
            let (qe, qi) = c.step(&env, th, sc, (cut >> k) & 1 == 1);
            out.q_exhaust += qe;
            out.q_intake += qi;
        }
        out
    }

    /// Run `cycles` engine cycles silently at the operating point in force,
    /// so the gas in the cylinders is that of a running engine.  Not for the
    /// audio thread.
    pub fn prime(&mut self, cycles: f64) {
        let rpm = self.target.rpm.max(1.0);
        let n = (cycles * 120.0 / rpm * self.rate).ceil() as u64;
        for _ in 0..n {
            self.tick();
        }
        self.q_exhaust_prev = self.cyl.iter().map(|c| c.q_exhaust).sum();
    }

    /// Render audio.  PROVISIONAL until the exhaust network exists: the
    /// exhaust valves' flow radiated as a bare monopole at 1 m,
    /// p = (dq/dt) / (4 pi r), on both channels.
    pub fn render(&mut self, left: &mut [f32], right: &mut [f32]) {
        let n = left.len().min(right.len());
        let k = self.rate / (4.0 * core::f64::consts::PI) / FULL_SCALE_PA;
        for i in 0..n {
            let s = self.tick();
            let v = ((s.q_exhaust - self.q_exhaust_prev) * k) as f32;
            self.q_exhaust_prev = s.q_exhaust;
            left[i] = v;
            right[i] = v;
        }
    }
}
