//! One cylinder, solved live (docs/REDESIGN_V2.md s.6.2).
//!
//! The gas in the cylinder is one zone: a mass `m` at a temperature `t`, in
//! the volume the piston leaves it.  Each sample it
//!
//!   1. is compressed or expanded by the piston (adiabatic work),
//!   2. is heated by the burn (a Wiebe function of crank angle) and loses
//!      heat to the walls (Hohenberg's correlation),
//!   3. flows through whichever valves are open -- the compressible orifice
//!      of `gas`, choked while the pressure ratio across it is below the
//!      critical one -- taking its enthalpy with it.
//!
//! Nothing here is "a pulse".  A hard blowdown at full load, a soft
//! displacement push at light load, the back-flow of a long overlap at idle:
//! all are what these three steps give for the operating point they are fed.
//!
//! ## The valve step is implicit
//!
//! Near top dead centre the cylinder is a few tens of cc between two open
//! valves, and it equalises with its ports faster than one sample lasts.  An
//! explicit step rings there (and blows up on a racing head).  So step 3
//! solves for the cylinder pressure at the END of the sample:
//!
//! ```text
//!     F(p) = p - p* + (dt R / V) * sum over valves of  (gamma T)_upstream * q_valve(p)  =  0
//! ```
//!
//! with `p*` the pressure after steps 1-2 and `q_valve` the flow out of the
//! cylinder.  `F` rises monotonically with `p` and its root is bracketed by
//! `p*` and the port pressures, so a safeguarded Newton iteration always
//! finds it.  It starts from where last sample's ended, and usually needs one
//! or two evaluations.  The mass and temperature are then updated with those
//! flows, so mass is conserved whatever the iteration's tolerance.
//!
//! A port is not just a pressure: it is what the pipe behind it does when gas
//! is pushed into it.  For a pipe carrying waves that is `p = p_in + z q`,
//! `z = c / A` -- the port pressure rises with the very flow it receives.
//! That relation is solved together with the valve (the inner iteration), so
//! the exhaust network of stage S2 plugs in without touching this file.

use crate::gas::{
    orifice, GasModel, Nozzle, AFR_STOICH, COMBUSTION_EFF, FUEL_LHV, LINEAR_BELOW, R_GAS,
};
use crate::math;
use crate::spec::{Geom, ValveCurve};

/// Wiebe: x = 1 - exp(-A y^3); A = 6.908 leaves 0.1 % unburned at y = 1.
const WIEBE_A: f64 = 6.908;
/// y at 50 % burned, and the 10-90 % span, of that curve
pub const WIEBE_Y50: f64 = 0.464_66;
pub const WIEBE_SPAN_10_90: f64 = 0.445_35;

const MAX_OUTER: u32 = 24;
const MAX_INNER: u32 = 24;
/// The pressure iteration stops when its next step would be below this share
/// of the pressure -- and takes that step along a straight line.
const P_TOLERANCE: f64 = 1.0e-6;

/// What stands behind a valve.
#[derive(Clone, Copy, Debug)]
pub struct Port {
    /// the pressure there with no flow through the valve, Pa
    pub p_in: f64,
    /// how much that pressure rises per kg/s pushed in, Pa s/kg (0: a plenum)
    pub z: f64,
    /// temperature of the gas standing in the port, K (what flows back in)
    pub t: f64,
    gamma: f64,
    noz: Nozzle,
    /// 1 / sqrt(R t)
    inv_sqrt_rt: f64,
}

impl Port {
    pub fn new(gas: &GasModel, p_in: f64, t: f64, z: f64) -> Self {
        let gamma = gas.gamma(t);
        Port { p_in, z, t, gamma, noz: Nozzle::new(gamma), inv_sqrt_rt: 1.0 / (R_GAS * t).sqrt() }
    }

    /// The same port at another temperature (gamma follows).
    pub fn set_temperature(&mut self, gas: &GasModel, t: f64) {
        if t != self.t {
            *self = Port::new(gas, self.p_in, t, self.z);
        }
    }
}

/// The inputs of one sample, common to all cylinders.
#[derive(Clone, Copy, Debug)]
pub struct StepEnv<'a> {
    pub geom: &'a Geom,
    pub gas: &'a GasModel,
    pub exhaust_curve: &'a ValveCurve,
    pub intake_curve: &'a ValveCurve,
    /// where the valves open this block (cam phasing applied), [0, 720)
    pub exhaust_open: f64,
    pub intake_open: f64,
    pub exhaust: &'a Port,
    pub intake: &'a Port,
    /// s
    pub dt: f64,
    /// crank degrees this sample
    pub dtheta: f64,
    /// (mean piston speed + 1.4 m/s)^0.8, for the wall correlation
    pub speed_term: f64,
    pub wall_temp: f64,
    pub heat_scale: f64,
    pub wall_refresh_deg: f64,
    /// air per cycle from the tables, kg (0: breathe)
    pub charge: f64,
    pub lambda: f64,
    /// where a burn starting now begins, [0, 720), and how long it lasts
    pub burn_start: f64,
    pub burn_len: f64,
    /// cycle-to-cycle variation (`Control::ccv`)
    pub ccv: f64,
}

/// Standard deviation of the cylinders of one engine about their mean
/// charge (see `Cylinder::seed`).
pub const CYLINDER_SPREAD: f64 = 0.03;

/// (start, length) in crank degrees of the Wiebe burn whose 50 % point is at
/// `ca50_deg` after firing TDC and whose 10-90 % takes `burn_deg`.
pub fn burn_window(ca50_deg: f64, burn_deg: f64) -> (f64, f64) {
    let len = burn_deg / WIEBE_SPAN_10_90;
    (math::wrap720(ca50_deg - WIEBE_Y50 * len), len)
}

/// Running totals of one cylinder since they were last cleared -- the
/// energy book the tests audit, and the indicated work.
#[derive(Clone, Copy, Debug, Default)]
pub struct Ledger {
    /// work done ON the piston, J
    pub work: f64,
    /// heat released by the burn, J
    pub burn: f64,
    /// heat given to the walls, J
    pub wall: f64,
    /// mass out through the exhaust valve / in through the intake valve, kg
    pub m_exhaust: f64,
    pub m_intake: f64,
}

#[derive(Clone, Debug)]
pub struct Cylinder {
    /// gas mass, kg
    pub m: f64,
    /// gas temperature, K
    pub t: f64,
    /// of `m`, the burned part, kg
    pub m_burned: f64,
    /// volume, m^3
    pub v: f64,
    /// pressure, Pa
    pub p: f64,
    /// last sample's flows, kg/s: out through the exhaust valve, in through the intake valve
    pub q_exhaust: f64,
    pub q_intake: f64,

    // the burn
    armed: bool,
    burning: bool,
    cut: bool,
    q_total: f64,
    burn_start: f64,
    burn_len: f64,
    x_prev: f64,
    // this cycle's own burn: starts so many degrees late, lasts so many
    // times as long, releases this fraction of the heat
    rng: u64,
    shift: f64,
    stretch: f64,
    weak: f64,
    /// this cylinder against the others, for the life of the engine: its
    /// share of the charge, how late and how slowly it burns (`seed`)
    own_charge: f64,
    own_shift: f64,
    own_stretch: f64,

    // the walls
    h_wall: f64,
    h_since: f64,

    intake_area_prev: f64,
    /// how far the valves moved the pressure in the last two samples (the
    /// iteration's starting guess), and how many of them are valid
    dp_prev: [f64; 2],
    dp_valid: u8,

    pub ledger: Ledger,
    /// the most iterations any one sample has needed (health figures): of
    /// the cylinder-pressure solve, and of a valve-with-its-port solve
    pub worst_outer: u32,
    pub worst_inner: u32,
    /// samples with a valve open, and the pressure evaluations they took in all
    pub solves: u64,
    pub iterations: u64,
}

/// The cylinder's gas as the upstream side of a valve.  Its nozzle is made
/// only if some flow leaves the cylinder this sample (on the intake stroke
/// none does).
struct CylGas {
    gamma: f64,
    inv_sqrt_rt: f64,
    built: bool,
    noz: Nozzle,
}

impl CylGas {
    #[inline(always)]
    fn noz(&mut self) -> &Nozzle {
        if !self.built {
            self.noz = Nozzle::new(self.gamma);
            self.built = true;
        }
        &self.noz
    }
}

/// A valve between the cylinder (p, t) and a port: the flow OUT of the
/// cylinder and how it changes with the cylinder pressure.
struct Valve<'a> {
    cda: f64,
    port: &'a Port,
    /// cda / sqrt(R t_cyl): the cylinder gas as the upstream one
    k_out: f64,
    /// cda / sqrt(R t_port)
    k_in: f64,
}

impl<'a> Valve<'a> {
    #[inline(always)]
    fn new(cda: f64, port: &'a Port, inv_sqrt_rt_cyl: f64) -> Self {
        Valve { cda, port, k_out: cda * inv_sqrt_rt_cyl, k_in: cda * port.inv_sqrt_rt }
    }

    /// Flow out of the cylinder into a port held at `pp`:
    /// (q, dq/dp_cyl >= 0, dq/dp_port <= 0).
    #[inline(always)]
    fn through(&self, cyl: &mut CylGas, p: f64, pp: f64) -> (f64, f64, f64) {
        if p >= pp {
            let f = orifice(cyl.noz(), self.k_out, p, pp);
            (f.q, f.d_up, f.d_dn)
        } else {
            let f = orifice(&self.port.noz, self.k_in, pp, p);
            (-f.q, -f.d_dn, -f.d_up)
        }
    }

    /// The flow with the port's own reaction to it solved:
    /// (q, dq/dp_cyl, iterations).  `guess` is last sample's flow.
    #[inline(always)]
    fn flow(&self, cyl: &mut CylGas, p: f64, guess: f64) -> (f64, f64, u32) {
        if self.cda <= 0.0 {
            return (0.0, 0.0, 0);
        }
        let port = self.port;
        if port.z == 0.0 || p == port.p_in {
            let (q, dp, _) = self.through(cyl, p, port.p_in);
            return (q, dp, 0);
        }
        self.flow_into_pipe(cyl, p, guess)
    }

    /// The unknown is q in  g(q) = q - through(p, p_in + z q) = 0.  Around
    /// equal pressures the flow law is a straight line, and there the root is
    /// written down.  Outside it the law is one smooth branch on which g is
    /// convex (flow out) or concave (flow in): Newton from the far end of the
    /// bracket walks to the root without ever overshooting it.
    fn flow_into_pipe(&self, cyl: &mut CylGas, p: f64, guess: f64) -> (f64, f64, u32) {
        let port = self.port;
        let z = port.z;
        let forward = p > port.p_in;
        let kl = if forward {
            self.k_out * cyl.noz().lin_slope()
        } else {
            self.k_in * port.noz.lin_slope()
        };
        let q_lin = kl * (p - port.p_in) / (1.0 + kl * z);
        let pp = port.p_in + z * q_lin;
        let (up, dn) = if forward { (p, pp) } else { (pp, p) };
        if up - dn <= LINEAR_BELOW * up {
            return (q_lin, kl / (1.0 + kl * z), 0);
        }
        // the flow into a port that does not react is the far end; the edge
        // of the straight-line zone is the near one
        let (q0, _, _) = self.through(cyl, p, port.p_in);
        let (mut lo, mut hi, far) = if forward {
            let hi = q0.min((p * (1.0 - LINEAR_BELOW) - port.p_in) / z);
            (0.0, hi, hi)
        } else {
            let lo = q0.max((p / (1.0 - LINEAR_BELOW) - port.p_in) / z);
            (lo, 0.0, lo)
        };
        let mut q = if guess > lo && guess < hi { guess } else { far };
        let tol = 1.0e-10 * q0.abs() + 1.0e-18;
        let mut out = (q, 0.0, 0);
        let mut clamped = 0;
        for it in 1..=MAX_INNER {
            let (f, dp, dport) = self.through(cyl, p, port.p_in + z * q);
            let g = q - f;
            let dg = 1.0 - z * dport;
            out = (q, dp / dg, it);
            if g.abs() <= tol {
                break;
            }
            if g > 0.0 {
                hi = q;
            } else {
                lo = q;
            }
            let mut qn = q - g / dg;
            if !(qn >= lo && qn <= hi) {
                // a step from the near side lands beyond the far end: go to
                // that end (and bisect if that ever repeats)
                clamped += 1;
                qn = if clamped > 2 {
                    0.5 * (lo + hi)
                } else if qn > hi {
                    hi
                } else {
                    lo
                };
            }
            if qn == q {
                break;
            }
            q = qn;
        }
        out
    }
}

impl Cylinder {
    /// A cylinder at rest: gas at pressure `p` and temperature `t`, all of
    /// it inert, at the crank angle `theta`.
    pub fn new(geom: &Geom, theta: f64, p: f64, t: f64, intake_area: f64) -> Self {
        let v = geom.volume(theta);
        let m = p * v / (R_GAS * t);
        Cylinder {
            m,
            t,
            m_burned: m,
            v,
            p,
            q_exhaust: 0.0,
            q_intake: 0.0,
            armed: false,
            burning: false,
            cut: false,
            q_total: 0.0,
            burn_start: 0.0,
            burn_len: 1.0,
            x_prev: 0.0,
            rng: 0x9E37_79B9_7F4A_7C15,
            shift: 0.0,
            stretch: 1.0,
            weak: 1.0,
            own_charge: 1.0,
            own_shift: 0.0,
            own_stretch: 1.0,
            h_wall: 0.0,
            h_since: f64::INFINITY,
            intake_area_prev: intake_area,
            dp_prev: [0.0; 2],
            dp_valid: 0,
            ledger: Ledger::default(),
            worst_outer: 0,
            worst_inner: 0,
            solves: 0,
            iterations: 0,
        }
    }

    /// Put the gas in a given state (tests, and the analytic checks).
    pub fn set_state(&mut self, p: f64, t: f64, burned_fraction: f64) {
        self.p = p;
        self.t = t;
        self.m = p * self.v / (R_GAS * t);
        self.m_burned = self.m * burned_fraction.clamp(0.0, 1.0);
        self.dp_valid = 0;
    }

    /// Advance one sample to the crank angle `theta` (this cylinder's own,
    /// [0, 720), the END of the sample), whose sine and cosine are `sc`.
    /// `cut`: no fuel for a cycle that starts its burn now.  Returns (flow
    /// out through the exhaust valve, flow in through the intake valve), kg/s.
    #[inline]
    pub fn step(&mut self, e: &StepEnv, theta: f64, sc: (f64, f64), cut: bool) -> (f64, f64) {
        let gas = e.gas;
        let geom = e.geom;

        // ---- 1. the piston: adiabatic, second order in the volume step
        let v0 = self.v;
        let v1 = geom.volume_at(sc.0, sc.1);
        let gamma = gas.gamma(self.t);
        let cv = R_GAS / (gamma - 1.0);
        let x = (gamma - 1.0) * (v1 - v0) / (v1 + v0);
        let t_was = self.t;
        let mut t = t_was * (1.0 - x) / (1.0 + x);
        // the work that took: m cv (t_was - t), on the piston
        self.ledger.work += self.m * cv * (t_was - t);

        // ---- 2a. the burn
        let mut dq = 0.0;
        if self.armed && math::wrap720(theta - e.burn_start - self.shift) < 180.0 {
            self.armed = false;
            self.cut = cut;
            if !cut {
                self.burning = true;
                self.burn_start = math::wrap720(e.burn_start + self.shift);
                self.burn_len = e.burn_len * self.stretch;
                self.x_prev = 0.0;
                let fresh = (self.m - self.m_burned).max(0.0);
                let fuel = fresh / (e.lambda * AFR_STOICH);
                // rich: the oxygen, not the fuel, limits what burns
                self.q_total = fuel * FUEL_LHV * COMBUSTION_EFF * e.lambda.min(1.0) * self.weak;
            }
        }
        if self.burning {
            let y = math::wrap720_near(theta - self.burn_start) / self.burn_len;
            let xb = if y >= 1.0 {
                self.burning = false;
                self.m_burned = self.m;
                1.0
            } else {
                ((1.0 - math::exp(-WIEBE_A * y * y * y)) * (1.0 / (1.0 - 0.001_000_0))).min(1.0)
            };
            dq = self.q_total * (xb - self.x_prev);
            self.x_prev = xb;
            self.ledger.burn += dq;
        }

        // ---- 2b. the walls (Hohenberg 1979: h = 130 V^-0.06 p[bar]^0.8 T^-0.4 (vp + 1.4)^0.8)
        if e.heat_scale > 0.0 {
            self.h_since += e.dtheta;
            if self.h_since >= e.wall_refresh_deg {
                self.h_since = 0.0;
                let ln_h =
                    -0.06 * math::ln(v1) + 0.8 * math::ln(self.p * 1.0e-5) - 0.4 * math::ln(t);
                self.h_wall = 130.0 * math::exp(ln_h) * e.speed_term * e.heat_scale;
            }
            let lost = self.h_wall * geom.wall_area(v1) * (t - e.wall_temp) * e.dt;
            dq -= lost;
            self.ledger.wall += lost;
        }
        t += dq / (self.m * cv);
        if t < 120.0 {
            t = 120.0;
        }
        let p_star = self.m * R_GAS * t / v1;

        // ---- 3. the valves, at the middle of the sample
        let mid = theta - 0.5 * e.dtheta;
        let mid = if mid < 0.0 { mid + 720.0 } else { mid };
        let a_ex = e.exhaust_curve.area_after(math::wrap720_near(mid - e.exhaust_open));
        let a_in = e.intake_curve.area_after(math::wrap720_near(mid - e.intake_open));

        let mut q_ex = 0.0; // out of the cylinder, into the exhaust port
        let mut q_in = 0.0; // out of the cylinder, into the intake port
        let mut p = p_star;
        if a_ex > 0.0 || a_in > 0.0 {
            let gamma_c = gas.gamma(t);
            let inv = 1.0 / (R_GAS * t).sqrt();
            let mut cyl =
                CylGas { gamma: gamma_c, inv_sqrt_rt: inv, built: false, noz: Nozzle::NONE };
            let ex = Valve::new(a_ex, e.exhaust, cyl.inv_sqrt_rt);
            let inl = Valve::new(a_in, e.intake, cyl.inv_sqrt_rt);
            let c = e.dt * R_GAS / v1;
            let w_cyl = gamma_c * t;
            let w_ex = e.exhaust.gamma * e.exhaust.t;
            let w_in = e.intake.gamma * e.intake.t;

            let mut lo = p_star;
            let mut hi = p_star;
            if a_ex > 0.0 {
                lo = lo.min(e.exhaust.p_in);
                hi = hi.max(e.exhaust.p_in);
            }
            if a_in > 0.0 {
                lo = lo.min(e.intake.p_in);
                hi = hi.max(e.intake.p_in);
            }
            // start where the last samples point to
            let guess = match self.dp_valid {
                0 => 0.0,
                1 => self.dp_prev[0],
                _ => 2.0 * self.dp_prev[0] - self.dp_prev[1],
            };
            p = (p_star + guess).clamp(lo, hi);
            let tol = P_TOLERANCE * p_star;
            let mut g_ex = self.q_exhaust;
            let mut g_in = -self.q_intake;
            let mut it = 0;
            loop {
                it += 1;
                let (qe, dqe, ie) = ex.flow(&mut cyl, p, g_ex);
                let (qi, dqi, ii) = inl.flow(&mut cyl, p, g_in);
                self.worst_inner = self.worst_inner.max(ie.max(ii));
                let we = if qe >= 0.0 { w_cyl } else { w_ex };
                let wi = if qi >= 0.0 { w_cyl } else { w_in };
                let f = p - p_star + c * (we * qe + wi * qi);
                let df = 1.0 + c * (we * dqe + wi * dqi);
                let step = f / df;
                if step.abs() <= tol {
                    // near enough for a straight line to finish it
                    let pn = (p - step).clamp(lo, hi);
                    q_ex = qe + dqe * (pn - p);
                    q_in = qi + dqi * (pn - p);
                    p = pn;
                    break;
                }
                q_ex = qe;
                q_in = qi;
                g_ex = qe;
                g_in = qi;
                if it >= MAX_OUTER {
                    break;
                }
                if f > 0.0 {
                    hi = p;
                } else {
                    lo = p;
                }
                let mut pn = p - step;
                if !(pn >= lo && pn <= hi) {
                    pn = 0.5 * (lo + hi);
                }
                if pn == p {
                    break;
                }
                p = pn;
            }
            self.worst_outer = self.worst_outer.max(it);
            self.solves += 1;
            self.iterations += it as u64;
            self.dp_prev = [p - p_star, self.dp_prev[0]];
            self.dp_valid = (self.dp_valid + 1).min(2);

            // out: the gas that leaves takes its enthalpy; what stays expands
            // isentropically (T ~ m^(gamma-1), second order in the mass step)
            let m0 = self.m;
            let mut out = (q_ex.max(0.0) + q_in.max(0.0)) * e.dt;
            if out > 0.9 * m0 {
                // cannot happen with a physical port pressure; never divide by nothing
                let s = 0.9 * m0 / out;
                q_ex *= if q_ex > 0.0 { s } else { 1.0 };
                q_in *= if q_in > 0.0 { s } else { 1.0 };
                out = 0.9 * m0;
            }
            let mut m = m0;
            if out > 0.0 {
                let m1 = m0 - out;
                let y = (gamma_c - 1.0) * out / (m0 + m1);
                t *= (1.0 - y) / (1.0 + y);
                self.m_burned *= m1 / m0;
                m = m1;
            }
            // in: mixing at constant volume, the incoming gas bringing its enthalpy
            if q_ex < 0.0 {
                let dm = -q_ex * e.dt;
                t = mix(gas, m, t, dm, e.exhaust.t);
                m += dm;
                self.m_burned += dm;
            }
            if q_in < 0.0 {
                let dm = -q_in * e.dt;
                t = mix(gas, m, t, dm, e.intake.t);
                m += dm;
            }
            self.m = m;
            p = m * R_GAS * t / v1;
            self.ledger.m_exhaust += q_ex * e.dt;
            self.ledger.m_intake -= q_in * e.dt;
        } else {
            self.dp_valid = 0;
        }

        // ---- the intake valve has just shut: the charge is trapped
        if self.intake_area_prev > 0.0 && a_in <= 0.0 {
            if e.charge > 0.0 {
                // the breathing tables' charge, not this model's own
                self.m = self.m_burned + e.charge * self.own_charge;
                p = self.m * R_GAS * t / v1;
            }
            self.armed = true;
            self.vary(e.ccv);
        }
        self.intake_area_prev = a_in;

        self.t = t;
        self.v = v1;
        self.p = p;
        self.q_exhaust = q_ex;
        self.q_intake = -q_in;
        (q_ex, -q_in)
    }

    /// Give this cylinder its own random sequence.
    pub fn seed(&mut self, s: u64) {
        self.rng = (s ^ 0x9E37_79B9_7F4A_7C15).max(1);
        // No two cylinders of an engine are alike: runner and port castings,
        // injector flow, where the cylinder sits in the plenum and the water
        // jacket.  Measured engines spread 2-5 % in the work of their
        // cylinders.  Here: 3 % in the charge, 1.5 degrees in the burn, 5 %
        // in its speed (standard deviations), fixed per cylinder.  It is
        // what gives an even-firing engine its orders below the firing one.
        self.own_charge = 1.0 + CYLINDER_SPREAD * self.bell().clamp(-2.0, 2.0);
        self.own_shift = 50.0 * CYLINDER_SPREAD * self.bell().clamp(-2.0, 2.0);
        self.own_stretch = math::exp(1.7 * CYLINDER_SPREAD * self.bell().clamp(-2.0, 2.0));
    }

    /// A number from a bell curve of unit variance (four uniforms summed).
    fn bell(&mut self) -> f64 {
        let mut s = 0.0;
        for _ in 0..4 {
            // xorshift64*
            let mut x = self.rng;
            x ^= x >> 12;
            x ^= x << 25;
            x ^= x >> 27;
            self.rng = x;
            let u = (x.wrapping_mul(0x2545_F491_4F6C_DD1D) >> 11) as f64 * (1.0 / 9007199254740992.0);
            s += u;
        }
        (s - 2.0) * 1.732_050_807_568_877_2
    }

    /// The burn of the cycle now trapped: no two alike (s.6.3).  The early
    /// flame kernel is what varies -- it sets when the burn gets going
    /// (`shift`) and how fast it then runs (`stretch`), and a slow burn is
    /// also a less complete one.  With `ccv` about the coefficient of
    /// variation of the indicated work: at 0.02 the 50 % point wanders 1.3
    /// degrees and the duration 5 %; at 0.10, 4.5 degrees and 25 %.
    fn vary(&mut self, ccv: f64) {
        if ccv <= 0.0 {
            self.shift = self.own_shift;
            self.stretch = self.own_stretch;
            self.weak = 1.0;
            return;
        }
        let n1 = self.bell();
        let n2 = self.bell();
        self.stretch = self.own_stretch * math::exp(2.5 * ccv * n1).clamp(0.6, 2.5);
        self.shift = self.own_shift + ((0.5 + 40.0 * ccv) * (0.6 * n2 + 0.4 * n1)).clamp(-15.0, 25.0);
        self.weak = (1.0 - 1.2 * ccv * n1.max(0.0)).max(0.5);
    }

    /// The most iterations any one sample has needed, of either solve.
    pub fn worst_iterations(&self) -> u32 {
        self.worst_outer.max(self.worst_inner)
    }

    /// Did the cycle now burning (or last burned) get fuel?
    pub fn was_cut(&self) -> bool {
        self.cut
    }
}

/// Temperature after `dm` at `t_in` joins `m` at `t` in a fixed volume:
/// (m + dm) u(T') = m u(T) + dm (u(t_in) + R t_in).
#[inline(always)]
fn mix(gas: &GasModel, m: f64, t: f64, dm: f64, t_in: f64) -> f64 {
    let cv = gas.cv(t);
    let cv_mid = gas.cv(0.5 * (t + t_in));
    t + dm * (cv_mid * (t_in - t) + R_GAS * t_in) / ((m + dm) * cv)
}
