//! What an engine IS: the facts the sound model is built from.
//!
//! Angles are crank degrees in the cylinder's own four-stroke cycle, with
//! 0 at FIRING top dead centre: expansion 0-180, exhaust 180-360, intake
//! 360-540, compression 540-720.

use crate::math;

#[derive(Clone, Debug, PartialEq)]
pub struct SpecError(pub String);

impl core::fmt::Display for SpecError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(f, "engine spec: {}", self.0)
    }
}

impl std::error::Error for SpecError {}

fn bad<T>(msg: impl Into<String>) -> Result<T, SpecError> {
    Err(SpecError(msg.into()))
}

/// A valve's EFFECTIVE flow area (discharge coefficient included, all the
/// cylinder's valves of that kind together) against crank angle: a table
/// from its opening to its closing.  The bake makes it from the cam's lift
/// curve and the valve's size, or from a flow bench; the core does not care.
#[derive(Clone, Debug)]
pub struct ValveCurve {
    /// where it opens, [0, 720)
    pub open_deg: f64,
    /// how long it is open, (0, 720)
    pub span_deg: f64,
    /// m^2, uniformly spaced from the opening to the closing, both ends 0
    pub area: Vec<f64>,
}

impl ValveCurve {
    /// A valve that never opens.
    pub fn closed() -> Self {
        ValveCurve { open_deg: 0.0, span_deg: 1.0, area: vec![0.0, 0.0] }
    }

    pub fn validate(&self, what: &str) -> Result<(), SpecError> {
        if !(self.open_deg.is_finite() && (0.0..720.0).contains(&self.open_deg)) {
            return bad(format!("{what}: open_deg {} is not in [0, 720)", self.open_deg));
        }
        if !(self.span_deg > 0.0 && self.span_deg < 720.0) {
            return bad(format!("{what}: span_deg {} is not in (0, 720)", self.span_deg));
        }
        if self.area.len() < 2 {
            return bad(format!("{what}: the area table needs two points at least"));
        }
        if self.area.iter().any(|a| !(a.is_finite() && *a >= 0.0)) {
            return bad(format!("{what}: the area table holds a negative or non-finite value"));
        }
        if self.area[0] != 0.0 || self.area[self.area.len() - 1] != 0.0 {
            return bad(format!("{what}: the area table must start and end at 0"));
        }
        Ok(())
    }

    /// The area `d` degrees after the opening, d in [0, 720).  Catmull-Rom
    /// through the table: continuous in slope, so the flow it meters has no
    /// corner at every table point.
    #[inline]
    pub fn area_after(&self, d: f64) -> f64 {
        if d >= self.span_deg {
            return 0.0;
        }
        let a = &self.area[..];
        let last = a.len() - 1;
        let x = d / self.span_deg * last as f64;
        let i = (x as usize).min(last - 1);
        let f = x - i as f64;
        let p1 = a[i];
        let p2 = a[i + 1];
        let p0 = if i == 0 { p1 } else { a[i - 1] };
        let p3 = if i + 2 > last { p2 } else { a[i + 2] };
        let v = p1
            + 0.5
                * f
                * ((p2 - p0)
                    + f * ((2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3)
                        + f * (3.0 * (p1 - p2) + p3 - p0)));
        v.max(0.0)
    }

    pub fn max_area(&self) -> f64 {
        self.area.iter().cloned().fold(0.0, f64::max)
    }
}

/// One engine.
#[derive(Clone, Debug)]
pub struct EngineSpec {
    /// m
    pub bore: f64,
    /// m
    pub stroke: f64,
    /// connecting rod, centre to centre, m
    pub rod: f64,
    /// geometric compression ratio
    pub compression: f64,
    /// each cylinder's firing TDC in the engine's 720 degree cycle, [0, 720)
    pub firing_deg: Vec<f64>,
    pub exhaust: ValveCurve,
    pub intake: ValveCurve,
    /// mean temperature of the chamber walls, K
    pub wall_temp: f64,
    /// 1 = the Hohenberg correlation as published; 0 = adiabatic walls
    pub heat_scale: f64,
    /// 0 = gamma(T); > 0 = this constant (the analytic tests)
    pub gamma_const: f64,
    /// the wall heat-transfer coefficient is re-evaluated every so many
    /// crank degrees; 0 = every sample
    pub wall_refresh_deg: f64,
}

impl EngineSpec {
    pub fn validate(&self) -> Result<(), SpecError> {
        let pos = |v: f64, what: &str| -> Result<(), SpecError> {
            if v.is_finite() && v > 0.0 {
                Ok(())
            } else {
                bad(format!("{what} = {v} must be positive"))
            }
        };
        pos(self.bore, "bore")?;
        pos(self.stroke, "stroke")?;
        pos(self.rod, "rod")?;
        if self.rod <= 0.5 * self.stroke {
            return bad("the rod is shorter than the crank throw");
        }
        if !(self.compression > 1.5 && self.compression < 40.0) {
            return bad(format!("compression ratio {} is not in (1.5, 40)", self.compression));
        }
        if self.firing_deg.is_empty() || self.firing_deg.len() > 64 {
            return bad(format!("{} cylinders (1..64 supported)", self.firing_deg.len()));
        }
        if self.firing_deg.iter().any(|a| !(a.is_finite() && (0.0..720.0).contains(a))) {
            return bad("a firing angle is not in [0, 720)");
        }
        self.exhaust.validate("exhaust valve")?;
        self.intake.validate("intake valve")?;
        pos(self.wall_temp, "wall_temp")?;
        if !(self.heat_scale.is_finite() && self.heat_scale >= 0.0) {
            return bad("heat_scale must be >= 0");
        }
        if !(self.gamma_const == 0.0 || (self.gamma_const > 1.05 && self.gamma_const < 1.7)) {
            return bad("gamma_const must be 0 or in (1.05, 1.7)");
        }
        if !(self.wall_refresh_deg.is_finite() && self.wall_refresh_deg >= 0.0) {
            return bad("wall_refresh_deg must be >= 0");
        }
        Ok(())
    }
}

/// The slider-crank, derived once.
#[derive(Clone, Copy, Debug)]
pub struct Geom {
    pub bore: f64,
    pub stroke: f64,
    pub rod: f64,
    pub crank: f64,
    /// piston area, m^2
    pub a_bore: f64,
    /// swept volume, m^3
    pub swept: f64,
    /// clearance volume, m^3
    pub clearance: f64,
}

impl Geom {
    pub fn new(s: &EngineSpec) -> Self {
        let a_bore = core::f64::consts::FRAC_PI_4 * s.bore * s.bore;
        let swept = a_bore * s.stroke;
        Geom {
            bore: s.bore,
            stroke: s.stroke,
            rod: s.rod,
            crank: 0.5 * s.stroke,
            a_bore,
            swept,
            clearance: swept / (s.compression - 1.0),
        }
    }

    /// Cylinder volume at a crank angle (degrees from TDC), m^3.
    #[inline]
    pub fn volume(&self, theta_deg: f64) -> f64 {
        let (s, c) = math::sincos_deg(theta_deg);
        self.volume_at(s, c)
    }

    /// The same from the angle's sine and cosine.
    #[inline(always)]
    pub fn volume_at(&self, s: f64, c: f64) -> f64 {
        let r = self.crank;
        let l = self.rod;
        let x = r * (1.0 - c) + l - (l * l - r * r * s * s).sqrt();
        self.clearance + self.a_bore * x
    }

    /// The gas-side wall area at a volume: head, piston crown and the liner
    /// the piston has uncovered, m^2.
    #[inline(always)]
    pub fn wall_area(&self, v: f64) -> f64 {
        2.0 * self.a_bore + 4.0 * v / self.bore
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valve_curve_interpolates_through_its_points() {
        let c = ValveCurve { open_deg: 100.0, span_deg: 40.0, area: vec![0.0, 1.0, 3.0, 1.0, 0.0] };
        c.validate("t").unwrap();
        for (i, a) in c.area.iter().enumerate() {
            let d = i as f64 * 10.0;
            if d < c.span_deg {
                assert!((c.area_after(d) - a).abs() < 1e-12);
            }
        }
        assert_eq!(c.area_after(40.0), 0.0);
        assert_eq!(c.area_after(500.0), 0.0);
        assert!(c.area_after(39.999) < 1e-3);
        // never negative, never far above the table
        for i in 0..4000 {
            let v = c.area_after(i as f64 * 0.01);
            assert!((0.0..3.3).contains(&v));
        }
    }

    #[test]
    fn volume_ends() {
        let s = EngineSpec {
            bore: 0.0745,
            stroke: 0.0859,
            rod: 0.148,
            compression: 12.5,
            firing_deg: vec![0.0],
            exhaust: ValveCurve::closed(),
            intake: ValveCurve::closed(),
            wall_temp: 450.0,
            heat_scale: 1.0,
            gamma_const: 0.0,
            wall_refresh_deg: 1.0,
        };
        s.validate().unwrap();
        let g = Geom::new(&s);
        assert!((g.swept - 374.45e-6).abs() < 0.1e-6, "{}", g.swept);
        assert!((g.volume(0.0) - g.clearance).abs() < 1e-15);
        assert!((g.volume(180.0) - (g.clearance + g.swept)).abs() < 1e-12);
        assert!((g.volume(180.0) / g.volume(360.0) - 12.5).abs() < 1e-9);
    }

    #[test]
    fn bad_specs_are_refused() {
        let mut c = ValveCurve::closed();
        c.area = vec![0.0, 1.0];
        assert!(c.validate("x").is_err());
        c.area = vec![0.0, -1.0, 0.0];
        assert!(c.validate("x").is_err());
        c.area = vec![0.0, f64::NAN, 0.0];
        assert!(c.validate("x").is_err());
    }
}
