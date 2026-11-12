//! The working gas, and the flow through a valve.
//!
//! One ideal gas with a temperature-dependent ratio of specific heats, and the
//! compressible orifice: the isentropic nozzle law, choked when the pressure
//! ratio across it falls below the critical one.

use crate::math;
use crate::nozzle_fit as fit;

/// Specific gas constant, J/(kg K).  Air; burned stoichiometric gas is within 1 %.
pub const R_GAS: f64 = 287.0;
pub const P_ATM: f64 = 101_325.0;
/// Stoichiometric air/fuel mass ratio of gasoline.
pub const AFR_STOICH: f64 = 14.7;
/// Lower heating value of gasoline, J/kg.
pub const FUEL_LHV: f64 = 44.0e6;
/// The share of the fuel's energy a healthy burn releases.
pub const COMBUSTION_EFF: f64 = 0.96;

/// The gas model: its ratio of specific heats.
#[derive(Clone, Copy, Debug)]
pub struct GasModel {
    /// > 0: a constant gamma (the analytic tests).  0: gamma(T).
    pub gamma_const: f64,
}

impl GasModel {
    /// gamma(T) = 1.375 - 6.99e-5 T, the linear law of Gatowski et al. (1984)
    /// for the gas in a spark-ignition cylinder: 1.35 for the cold charge,
    /// 1.27 at 1500 K, 1.20 for the burned gas at 2500 K.
    #[inline(always)]
    pub fn gamma(&self, t: f64) -> f64 {
        if self.gamma_const > 0.0 {
            self.gamma_const
        } else {
            1.375 - 6.99e-5 * t.clamp(250.0, 2800.0)
        }
    }

    /// cv(T) = R / (gamma(T) - 1), J/(kg K).
    #[inline(always)]
    pub fn cv(&self, t: f64) -> f64 {
        R_GAS / (self.gamma(t) - 1.0)
    }
}

/// Below this relative pressure difference the flow function is a straight
/// line through zero.  The isentropic law has an infinite slope there (flow
/// goes as the root of the difference); real flow at vanishing Reynolds number
/// does not.  100 Pa at atmospheric pressure.
pub const LINEAR_BELOW: f64 = 1.0e-3;

/// The nozzle flow function of a gas of one gamma.
///
/// Mass flow through an effective area `cda` from (p_up, t_up) to p_dn:
///
/// ```text
///     q = cda * p_up / sqrt(R t_up) * psi(p_dn / p_up)
///     psi(r) = sqrt( 2g/(g-1) * (r^(2/g) - r^((g+1)/g)) )     r > r_crit
///            = psi(r_crit)                                     r <= r_crit   (choked)
/// ```
///
/// This is the innermost function of the sound model, so it is not evaluated
/// with powers.  With x = 1 - r, psi^2 = 2 x Q(x, g) and Q is smooth: a
/// polynomial (`nozzle_fit`, generated and checked by tools/gen_nozzle_fit.py)
/// holds it to 3e-9 for g in 1.10 .. 1.45.  Making a `Nozzle` reduces that
/// polynomial to the one gamma; evaluating it is then ten multiply-adds and a
/// square root.  Outside that range of gamma the law is evaluated as written.
#[derive(Clone, Copy, Debug)]
pub struct Nozzle {
    pub gamma: f64,
    /// critical pressure ratio
    pub r_crit: f64,
    /// psi when choked
    pub psi_crit: f64,
    /// psi at 1 - LINEAR_BELOW
    psi_lin: f64,
    /// Q's coefficients in u = 2x/X_MAX - 1 at this gamma
    c: [f64; fit::NX + 1],
    /// gamma is outside the fit: the law as written
    exact: bool,
    inv_g: f64,
    /// 2g/(g-1)
    k: f64,
    /// (g+1)/g
    gp: f64,
}

impl Nozzle {
    /// A placeholder that passes nothing (to be overwritten before use).
    pub const NONE: Nozzle = Nozzle {
        gamma: 0.0,
        r_crit: 0.0,
        psi_crit: 0.0,
        psi_lin: 0.0,
        c: [0.0; fit::NX + 1],
        exact: false,
        inv_g: 0.0,
        k: 0.0,
        gp: 0.0,
    };

    pub fn new(gamma: f64) -> Self {
        let g = gamma;
        let mut n = Nozzle {
            gamma: g,
            r_crit: 0.0,
            psi_crit: 0.0,
            psi_lin: 0.0,
            c: [0.0; fit::NX + 1],
            exact: !(fit::G_LO..=fit::G_HI).contains(&g),
            inv_g: 1.0 / g,
            k: 2.0 * g / (g - 1.0),
            gp: (g + 1.0) / g,
        };
        if n.exact {
            n.r_crit = math::powf(2.0 / (g + 1.0), g / (g - 1.0));
        } else {
            let v = (g - fit::G_MID) * (1.0 / fit::G_HALF);
            // every coefficient of u^i is itself a polynomial in v
            let mut c = [0.0; fit::NX + 1];
            for j in (0..=fit::NG).rev() {
                for i in 0..=fit::NX {
                    c[i] = c[i] * v + fit::Q[i][j];
                }
            }
            n.c = c;
            let mut r = 0.0;
            for j in (0..=fit::NR).rev() {
                r = r * v + fit::R_CRIT[j];
            }
            n.r_crit = r;
        }
        n.psi_crit = n.subsonic(n.r_crit).0;
        n.psi_lin = n.subsonic(1.0 - LINEAR_BELOW).0;
        n
    }

    /// (psi, d psi / d r) of the subsonic law.
    #[inline(always)]
    fn subsonic(&self, r: f64) -> (f64, f64) {
        if self.exact {
            return self.subsonic_exact(r);
        }
        let x = 1.0 - r;
        let u = x * (2.0 / fit::X_MAX) - 1.0;
        // Q and dQ/du together
        let mut q = self.c[fit::NX];
        let mut dq = 0.0;
        for i in (0..fit::NX).rev() {
            dq = dq * u + q;
            q = q * u + self.c[i];
        }
        let psi = (2.0 * x * q).max(0.0).sqrt();
        // d(psi^2)/dx = 2 Q + 2 x dQ/dx
        let slope = q + x * dq * (2.0 / fit::X_MAX);
        (psi, if psi > 0.0 { (-slope / psi).min(0.0) } else { 0.0 })
    }

    /// The same from the law as written.
    #[inline]
    pub fn subsonic_exact(&self, r: f64) -> (f64, f64) {
        let s = math::powf(r, self.inv_g);
        let a = self.k * s * (s - r);
        let psi = a.max(0.0).sqrt();
        // d/dr [ k (s^2 - s r) ],  ds/dr = s / (g r)
        let da = self.k * s * (2.0 * s * self.inv_g / r - self.gp);
        (psi, if psi > 0.0 { (0.5 * da / psi).min(0.0) } else { 0.0 })
    }

    /// Slope of the straight-line zone: there q = k * lin_slope * (p_up - p_dn).
    #[inline(always)]
    pub fn lin_slope(&self) -> f64 {
        self.psi_lin / LINEAR_BELOW
    }

    /// (psi, d psi / d r) at the pressure ratio r = p_dn / p_up, 0 < r <= 1.
    #[inline(always)]
    pub fn psi(&self, r: f64) -> (f64, f64) {
        if r <= self.r_crit {
            (self.psi_crit, 0.0)
        } else if r < 1.0 - LINEAR_BELOW {
            self.subsonic(r)
        } else {
            let slope = -self.psi_lin / LINEAR_BELOW;
            (slope * (r - 1.0).min(0.0), slope)
        }
    }
}

/// A flow and its sensitivities.
#[derive(Clone, Copy, Debug, Default)]
pub struct Flow {
    /// kg/s, from the upstream side to the downstream one
    pub q: f64,
    /// d q / d p_up  (>= 0)
    pub d_up: f64,
    /// d q / d p_dn  (<= 0)
    pub d_dn: f64,
}

/// The orifice.  `k` is `cda / sqrt(R t_up)`; requires p_up >= p_dn > 0.
#[inline(always)]
pub fn orifice(noz: &Nozzle, k: f64, p_up: f64, p_dn: f64) -> Flow {
    let r = p_dn / p_up;
    let (psi, dpsi) = noz.psi(r);
    Flow { q: k * p_up * psi, d_up: k * (psi - r * dpsi), d_dn: k * dpsi }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn choked_values_of_air() {
        let n = Nozzle::new(1.4);
        assert!((n.r_crit - 0.528_281_787).abs() < 1e-9, "{}", n.r_crit);
        assert!((n.psi_crit - 0.684_731_5).abs() < 1e-7, "{}", n.psi_crit);
        // and outside the fit, from the law as written (helium-like)
        let n = Nozzle::new(1.667);
        assert!(n.exact);
        assert!((n.r_crit - 0.487_1).abs() < 1e-4, "{}", n.r_crit);
        assert!((n.psi_crit - 0.726_2).abs() < 1e-4, "{}", n.psi_crit);
    }

    /// The polynomial against the law it stands for, over every gamma and
    /// pressure ratio it is used at.
    #[test]
    fn fit_matches_the_law() {
        let mut worst_psi: f64 = 0.0;
        let mut worst_slope: f64 = 0.0;
        for gi in 0..=70 {
            let g = fit::G_LO + (fit::G_HI - fit::G_LO) * gi as f64 / 70.0;
            let n = Nozzle::new(g);
            assert!(!n.exact);
            let r_exact = (2.0 / (g + 1.0f64)).powf(g / (g - 1.0));
            assert!((n.r_crit / r_exact - 1.0).abs() < 1e-11, "r_crit at {g}");
            let psi_c = g.sqrt() * (2.0 / (g + 1.0f64)).powf((g + 1.0) / (2.0 * (g - 1.0)));
            assert!((n.psi_crit / psi_c - 1.0).abs() < 5e-9, "psi_crit at {g}: {} vs {psi_c}", n.psi_crit);
            for ri in 0..2000 {
                let r = n.r_crit + (1.0 - LINEAR_BELOW - n.r_crit) * (ri as f64 + 0.5) / 2000.0;
                let (a, da) = n.subsonic(r);
                let (b, db) = n.subsonic_exact(r);
                worst_psi = worst_psi.max((a / b - 1.0).abs());
                // the slope vanishes at the critical ratio: compare absolutely there
                worst_slope = worst_slope.max((da - db).abs() / (db.abs() + 1.0e-2));
            }
        }
        println!("fit: psi within {worst_psi:.1e}, slope within {worst_slope:.1e}");
        assert!(worst_psi < 5.0e-9, "{worst_psi:e}");
        assert!(worst_slope < 2.0e-6, "{worst_slope:e}");
    }

    #[test]
    fn flow_function_is_continuous_and_monotone() {
        for &g in &[1.18, 1.27, 1.35, 1.40, 1.60] {
            let n = Nozzle::new(g);
            // continuous at the choking point and at the linear join
            let (a, _) = n.psi(n.r_crit);
            let (b, _) = n.psi(n.r_crit + 1e-12);
            assert!((a - b).abs() < 1e-9);
            let j = 1.0 - LINEAR_BELOW;
            assert!((n.psi(j - 1e-13).0 - n.psi(j).0).abs() < 1e-9);
            assert_eq!(n.psi(1.0).0, 0.0);
            // never rising with the pressure ratio (to the fit's own accuracy)
            let mut last = f64::INFINITY;
            for i in 1..=2000 {
                let r = i as f64 / 2000.0;
                let (p, d) = n.psi(r);
                assert!(p <= last + 1e-8 && d <= 0.0, "g {g} r {r}: {p} after {last}");
                last = p;
            }
        }
    }

    #[test]
    fn sensitivities_match_finite_differences() {
        for g in [1.3, 1.6] {
            let n = Nozzle::new(g);
            let k = 3.0e-4 / (R_GAS * 900.0_f64).sqrt();
            for &(pu, pd) in &[(5.0e5, 1.0e5), (1.6e5, 1.0e5), (1.02e5, 1.0e5), (1.00005e5, 1.0e5)] {
                let f = orifice(&n, k, pu, pd);
                let h = 1.0e-2;
                let up = (orifice(&n, k, pu + h, pd).q - orifice(&n, k, pu - h, pd).q) / (2.0 * h);
                let dn = (orifice(&n, k, pu, pd + h).q - orifice(&n, k, pu, pd - h).q) / (2.0 * h);
                let tol = 1e-5 * (f.d_up.abs() + f.d_dn.abs()) + 1e-15;
                assert!((f.d_up - up).abs() < tol, "d_up {} vs {} at {pu}", f.d_up, up);
                assert!((f.d_dn - dn).abs() < tol, "d_dn {} vs {} at {pu}", f.d_dn, dn);
                assert!(f.d_up >= 0.0 && f.d_dn <= 0.0);
            }
        }
    }
}
