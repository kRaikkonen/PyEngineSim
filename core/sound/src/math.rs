//! Every transcendental the core uses, in one place -- and written here.
//!
//! These are plain IEEE-754 double arithmetic: a range reduction and a
//! polynomial, no table, no call into the platform's maths library.  Rust
//! never fuses a multiply and an add on its own, so the same inputs give the
//! same bits on a phone, a Mac and this desk (s.5.1) -- a golden made on one
//! is exact on all.  They are also several times faster than what the C
//! runtime of some toolchains provides, which is what the render path needs.
//!
//! Accuracy: a few units in the last place over the ranges the core uses
//! (tested against the platform's own below).  Not correctly rounded, and not
//! meant for arguments near the overflow limits.

/// ln 2 in two parts: the high one has its low 32 bits clear, so `k * LN2_HI`
/// is exact for the k that occur.
const LN2_HI: f64 = 6.931_471_803_691_238_164_90e-1;
const LN2_LO: f64 = 1.908_214_929_270_587_700_02e-10;
const INV_LN2: f64 = core::f64::consts::LOG2_E;

/// 2^k for k in [-1022, 1023].
#[inline(always)]
fn pow2(k: i64) -> f64 {
    f64::from_bits(((k + 1023) as u64) << 52)
}

/// e^x.
#[inline]
pub fn exp(x: f64) -> f64 {
    if !(x > -745.2) {
        return if x.is_nan() { x } else { 0.0 };
    }
    if x > 709.78 {
        return f64::INFINITY;
    }
    // x = k ln2 + r,  |r| <= ln2 / 2
    let k = (x * INV_LN2 + if x >= 0.0 { 0.5 } else { -0.5 }) as i64;
    let kf = k as f64;
    let r = (x - kf * LN2_HI) - kf * LN2_LO;
    // e^r by its Taylor series to r^13: the next term is below 5e-18
    let p = 1.0
        + r * (1.0
            + r * (1.0 / 2.0
                + r * (1.0 / 6.0
                    + r * (1.0 / 24.0
                        + r * (1.0 / 120.0
                            + r * (1.0 / 720.0
                                + r * (1.0 / 5040.0
                                    + r * (1.0 / 40320.0
                                        + r * (1.0 / 362_880.0
                                            + r * (1.0 / 3_628_800.0
                                                + r * (1.0 / 39_916_800.0
                                                    + r * (1.0 / 479_001_600.0
                                                        + r * (1.0 / 6_227_020_800.0)))))))))))));
    if (-1000..=1000).contains(&k) {
        p * pow2(k)
    } else {
        // near the ends of the range: in two steps, each a normal number
        let h = k / 2;
        p * pow2(h) * pow2(k - h)
    }
}

/// The natural logarithm.
#[inline]
pub fn ln(x: f64) -> f64 {
    if !(x > 0.0) {
        return if x == 0.0 { f64::NEG_INFINITY } else { f64::NAN };
    }
    if x == f64::INFINITY {
        return x;
    }
    let mut x = x;
    let mut e: i64 = 0;
    if x < f64::MIN_POSITIVE {
        // subnormal: bring it up first
        x *= 18_014_398_509_481_984.0; // 2^54
        e = -54;
    }
    let bits = x.to_bits();
    e += ((bits >> 52) & 0x7ff) as i64 - 1023;
    // the mantissa, in [1, 2) -> (sqrt 1/2, sqrt 2]
    let mut m = f64::from_bits((bits & 0x000f_ffff_ffff_ffff) | 0x3ff0_0000_0000_0000);
    if m > core::f64::consts::SQRT_2 {
        m *= 0.5;
        e += 1;
    }
    // ln m = 2 atanh(s), s = (m - 1) / (m + 1), |s| <= 0.1716;
    // atanh(s) = s + s^3/3 + ... + s^21/21: the next term is below 1e-18 of it
    let s = (m - 1.0) / (m + 1.0);
    let z = s * s;
    let p = 1.0 / 3.0
        + z * (1.0 / 5.0
            + z * (1.0 / 7.0
                + z * (1.0 / 9.0
                    + z * (1.0 / 11.0
                        + z * (1.0 / 13.0
                            + z * (1.0 / 15.0
                                + z * (1.0 / 17.0 + z * (1.0 / 19.0 + z * (1.0 / 21.0)))))))));
    let ln_m = 2.0 * s + 2.0 * s * z * p;
    let ef = e as f64;
    ef * LN2_HI + (ln_m + ef * LN2_LO)
}

/// x^y for x > 0.
#[inline]
pub fn powf(x: f64, y: f64) -> f64 {
    exp(y * ln(x))
}

/// (sin, cos) of an angle in DEGREES.  The reduction to an eighth of a turn
/// is done in degrees, where it is exact.
#[inline]
pub fn sincos_deg(deg: f64) -> (f64, f64) {
    // quarter turns: n the nearest, f the rest in [-0.5, 0.5]
    let q = deg * (1.0 / 90.0);
    let n = (q + if q >= 0.0 { 0.5 } else { -0.5 }) as i64;
    let f = q - n as f64;
    let x = f * core::f64::consts::FRAC_PI_2; // [-pi/4, pi/4]
    let z = x * x;
    // Taylor to x^17 and x^18: the next terms are below 1e-19
    let s = x
        * (1.0
            + z * (-1.0 / 6.0
                + z * (1.0 / 120.0
                    + z * (-1.0 / 5040.0
                        + z * (1.0 / 362_880.0
                            + z * (-1.0 / 39_916_800.0
                                + z * (1.0 / 6_227_020_800.0
                                    + z * (-1.0 / 1_307_674_368_000.0
                                        + z * (1.0 / 355_687_428_096_000.0)))))))));
    let c = 1.0
        + z * (-1.0 / 2.0
            + z * (1.0 / 24.0
                + z * (-1.0 / 720.0
                    + z * (1.0 / 40320.0
                        + z * (-1.0 / 3_628_800.0
                            + z * (1.0 / 479_001_600.0
                                + z * (-1.0 / 87_178_291_200.0
                                    + z * (1.0 / 20_922_789_888_000.0
                                        + z * (-1.0 / 6_402_373_705_728_000.0)))))))));
    match n & 3 {
        0 => (s, c),
        1 => (c, -s),
        2 => (-s, -c),
        _ => (-c, s),
    }
}

/// An angle in degrees brought into [0, 720), for any finite input.
#[inline]
pub fn wrap720(x: f64) -> f64 {
    let d = x % 720.0;
    if d < 0.0 {
        d + 720.0
    } else {
        d
    }
}

/// The same for an input already within one turn of the range, (-720, 1440):
/// the difference of two wrapped angles.  No division.
#[inline(always)]
pub fn wrap720_near(x: f64) -> f64 {
    debug_assert!(x > -720.0 && x < 1440.0, "wrap720_near({x})");
    if x < 0.0 {
        x + 720.0
    } else if x >= 720.0 {
        x - 720.0
    } else {
        x
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fixed, well-spread sequence in [0, 1).
    fn spread(i: u32) -> f64 {
        (i as f64 * 0.618_033_988_749_894_9).fract()
    }

    fn close(a: f64, b: f64, ulps: f64) -> bool {
        if a == b {
            return true;
        }
        (a - b).abs() <= ulps * f64::EPSILON * a.abs().max(b.abs()).max(f64::MIN_POSITIVE)
    }

    #[test]
    fn exp_matches_the_platform() {
        for i in 0..200_000 {
            let x = -60.0 + 120.0 * spread(i);
            assert!(close(exp(x), x.exp(), 4.0), "exp({x}) = {:e} vs {:e}", exp(x), x.exp());
        }
        assert_eq!(exp(0.0), 1.0);
        assert_eq!(exp(-1.0e4), 0.0);
        assert_eq!(exp(1.0e4), f64::INFINITY);
        assert!(exp(f64::NAN).is_nan());
        assert!(close(exp(-744.0), (-744.0f64).exp(), 1.0e13)); // subnormal: a few of ITS last places
        assert!(close(exp(709.0), 709.0f64.exp(), 4.0));
    }

    #[test]
    fn ln_matches_the_platform() {
        for i in 0..200_000 {
            // across 60 decades, and densely around 1
            let a = 10.0f64.powf(-30.0 + 60.0 * spread(i));
            let b = 0.5 + 1.5 * spread(i);
            for x in [a, b, 1.0 + (b - 1.25) * 1e-6] {
                let (mine, theirs) = (ln(x), x.ln());
                assert!(
                    close(mine, theirs, 4.0) || (mine - theirs).abs() < 4.0 * f64::EPSILON,
                    "ln({x}) = {mine:e} vs {theirs:e}"
                );
            }
        }
        assert_eq!(ln(1.0), 0.0);
        assert_eq!(ln(0.0), f64::NEG_INFINITY);
        assert!(ln(-1.0).is_nan());
        assert!(close(ln(5.0e-324), (5.0e-324f64).ln(), 4.0));
    }

    #[test]
    fn powf_matches_the_platform() {
        for i in 0..200_000 {
            let x = 0.02 + 30.0 * spread(i);
            let y = -3.0 + 6.0 * spread(i.wrapping_mul(7919));
            assert!(close(powf(x, y), x.powf(y), 64.0), "{x}^{y}: {:e} vs {:e}", powf(x, y), x.powf(y));
        }
        // the pressure ratios of the nozzle law
        for i in 1..=10_000 {
            let r = i as f64 / 10_000.0;
            assert!(close(powf(r, 1.0 / 1.3), r.powf(1.0 / 1.3), 16.0));
        }
    }

    #[test]
    fn sincos_matches_the_platform() {
        for i in 0..400_000 {
            let deg = -1440.0 + 2880.0 * spread(i);
            let (s, c) = sincos_deg(deg);
            let rad = deg.to_radians();
            assert!((s - rad.sin()).abs() < 3.0e-15, "sin {deg}: {s} vs {}", rad.sin());
            assert!((c - rad.cos()).abs() < 3.0e-15, "cos {deg}: {c} vs {}", rad.cos());
            assert!((s * s + c * c - 1.0).abs() < 1.0e-15);
        }
        assert_eq!(sincos_deg(0.0), (0.0, 1.0));
        assert_eq!(sincos_deg(90.0), (1.0, 0.0));
        assert_eq!(sincos_deg(180.0), (0.0, -1.0));
        assert_eq!(sincos_deg(270.0), (-1.0, 0.0));
        assert_eq!(sincos_deg(720.0), (0.0, 1.0));
    }
}
