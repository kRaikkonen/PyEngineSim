//
//  Filters.swift
//  The DSP primitives, reproduced from engine_sim/audio.py.
//
//  These are deliberately the FIRST thing ported.  Everything downstream --
//  the exhaust chain, the reverbs, the listener stage -- is built out of these
//  two shapes, so if they do not match the Python sample for sample, nothing
//  above them can.  Tests/FilterTests.swift holds them to reference vectors
//  captured from the implementation Leo actually tuned against.
//
//  Note the state convention: a filter OWNS its state, unlike the Python
//  where scipy's zi is threaded through the call.  Same arithmetic, less to
//  get wrong.
//

import Foundation

/// Direct-form-II transposed biquad -- the shape scipy's `lfilter` runs for a
/// second-order section, and therefore the shape the reference vectors expect.
public struct Biquad {
    public var b0: Double, b1: Double, b2: Double
    public var a1: Double, a2: Double
    private var s1: Double = 0, s2: Double = 0

    public init(b: [Double], a: [Double]) {
        let a0 = a.count > 0 ? a[0] : 1.0
        b0 = (b.count > 0 ? b[0] : 0) / a0
        b1 = (b.count > 1 ? b[1] : 0) / a0
        b2 = (b.count > 2 ? b[2] : 0) / a0
        a1 = (a.count > 1 ? a[1] : 0) / a0
        a2 = (a.count > 2 ? a[2] : 0) / a0
    }

    /// A pass-through, for a filter that will be retuned before first use.
    public static var identity: Biquad { Biquad(b: [1.0], a: [1.0]) }

    public mutating func reset() { s1 = 0; s2 = 0 }

    /// Retune WITHOUT clearing the state.  scipy's `lfilter(b, a, x, zi=zi)`
    /// carries zi across a coefficient change, and every sliding filter in the
    /// chain relies on that: rebuilding instead would zero the history and
    /// click on every redesign -- which happens continuously while the revs
    /// move the centre frequencies.
    public mutating func setCoefficients(b: [Double], a: [Double]) {
        let a0 = a.count > 0 ? a[0] : 1.0
        b0 = (b.count > 0 ? b[0] : 0) / a0
        b1 = (b.count > 1 ? b[1] : 0) / a0
        b2 = (b.count > 2 ? b[2] : 0) / a0
        a1 = (a.count > 1 ? a[1] : 0) / a0
        a2 = (a.count > 2 ? a[2] : 0) / a0
    }

    @inline(__always)
    public mutating func process(_ x: Double) -> Double {
        let y = b0 * x + s1
        s1 = b1 * x - a1 * y + s2
        s2 = b2 * x - a2 * y
        return y
    }

    /// In-place over a block: this is the hot path, so no allocation.
    public mutating func process(_ buf: inout [Double]) {
        for i in buf.indices { buf[i] = process(buf[i]) }
    }
}

/// First order, the same shape.  Kept separate because it is over half of all
/// filter calls in the chain (the waveguide alone runs ~47 per block) and the
/// biquad's extra state is pure waste there.
public struct OnePole {
    public var b0: Double, b1: Double, a1: Double
    private var s1: Double = 0

    public init(b: [Double], a: [Double]) {
        let a0 = a.count > 0 ? a[0] : 1.0
        b0 = (b.count > 0 ? b[0] : 0) / a0
        b1 = (b.count > 1 ? b[1] : 0) / a0
        a1 = (a.count > 1 ? a[1] : 0) / a0
    }

    /// A pass-through, for a filter that will be retuned before first use.
    public static var identity: OnePole { OnePole(b: [1.0], a: [1.0]) }

    public mutating func reset() { s1 = 0 }

    /// Retune, keeping the state -- see `Biquad.setCoefficients`.
    public mutating func setCoefficients(b: [Double], a: [Double]) {
        let a0 = a.count > 0 ? a[0] : 1.0
        b0 = (b.count > 0 ? b[0] : 0) / a0
        b1 = (b.count > 1 ? b[1] : 0) / a0
        a1 = (a.count > 1 ? a[1] : 0) / a0
    }

    @inline(__always)
    public mutating func process(_ x: Double) -> Double {
        let y = b0 * x + s1
        s1 = b1 * x - a1 * y
        return y
    }

    public mutating func process(_ buf: inout [Double]) {
        for i in buf.indices { buf[i] = process(buf[i]) }
    }
}

/// Filter DESIGN, matching `_np_butter` / `_peaking` in audio.py.
///
/// Design cost matters as much as run cost here: the centre frequencies slide
/// with rpm, so these are called continuously while the engine revs -- that is
/// exactly what made the Python crackle when the rpm was dragged.
public enum FilterDesign {

    /// Butterworth by bilinear transform.  Orders 1 and 2, low and high.
    public static func butter(order: Int, wn: Double,
                              btype: String = "low") -> (b: [Double], a: [Double]) {
        let w = min(max(wn, 1e-6), 0.999999)
        let k = tan(Double.pi * w / 2.0)
        if order <= 1 {
            let a = [1.0, (k - 1.0) / (k + 1.0)]
            let b = btype == "high"
                ? [1.0 / (k + 1.0), -1.0 / (k + 1.0)]
                : [k / (k + 1.0), k / (k + 1.0)]
            return (b, a)
        }
        let k2 = k * k
        let r2 = 2.0.squareRoot()
        let d = 1.0 + r2 * k + k2
        let a = [1.0, 2.0 * (k2 - 1.0) / d, (1.0 - r2 * k + k2) / d]
        let b = btype == "high"
            ? [1.0 / d, -2.0 / d, 1.0 / d]
            : [k2 / d, 2.0 * k2 / d, k2 / d]
        return (b, a)
    }

    /// TRUE digital Butterworth band-pass, as a list of BIQUADS.
    ///
    /// Each analog low-pass prototype pole maps to a conjugate pair straddling
    /// the band, s = p*BW/2 +- sqrt((p*BW/2)^2 - w0^2); one zero goes at DC and
    /// one at Nyquist per section; the bilinear transform is folded into the
    /// prewarp; and the whole thing is normalised to unity at the band centre.
    ///
    /// This was a low-pass * high-pass cascade on both sides -- the same ORDER
    /// but not the same filter, 10 dB down across the passband on the injector
    /// design.  Desktop Python called scipy and got the real thing, so the
    /// error only ever reached the phone.
    public static func bandpassSections(order: Int, low w1: Double,
                                        high w2: Double)
        -> [(b: [Double], a: [Double])] {
        let o1 = tan(Double.pi * w1 / 2.0)        // prewarp, bilinear folded in
        let o2 = tan(Double.pi * w2 / 2.0)
        let bw = o2 - o1, o0sq = o1 * o2
        var sections = [(b: [Double], a: [Double])]()
        for k in 0..<order {
            let theta = Double.pi * Double(2 * k + order + 1) / Double(2 * order)
            let p = Complex(cos(theta), sin(theta))       // unit-cutoff LP pole
            let half = p * (bw / 2.0)
            let root = (half * half - Complex(o0sq, 0)).sqrt()
            for s in [half + root, half - root] where s.im >= 0 {
                let z = Complex(1.0 + s.re, s.im) / Complex(1.0 - s.re, -s.im)
                sections.append((b: [1.0, 0.0, -1.0],
                                 a: [1.0, -2.0 * z.re, z.re * z.re + z.im * z.im]))
            }
        }
        // unity at the band centre: the frequency whose prewarp is sqrt(o1*o2)
        let w0 = 2.0 * atan(o0sq.squareRoot()) / Double.pi
        let z0 = Complex(cos(Double.pi * w0), sin(Double.pi * w0))
        var h = Complex(1, 0)
        for s in sections {
            h = h * (evaluate(s.b, at: z0) / evaluate(s.a, at: z0))
        }
        let g = 1.0 / h.magnitude
        if !sections.isEmpty {
            sections[0].b = sections[0].b.map { $0 * g }
        }
        return sections
    }

    /// The same filter flattened to one (b, a), for comparison and tests.
    public static func bandpass(order: Int, low: Double,
                                high: Double) -> (b: [Double], a: [Double]) {
        var b = [1.0], a = [1.0]
        for s in bandpassSections(order: order, low: low, high: high) {
            b = convolve(b, s.b); a = convolve(a, s.a)
        }
        return (b, a)
    }

    /// Evaluate a polynomial in z^-1 at a point on the unit circle.
    private static func evaluate(_ c: [Double], at z: Complex) -> Complex {
        var acc = Complex(0, 0)
        var zi = Complex(1, 0)
        let zinv = Complex(1, 0) / z
        for coeff in c { acc = acc + zi * coeff; zi = zi * zinv }
        return acc
    }

    /// RBJ peaking EQ, as `_peaking` in audio.py -- including the fact that it
    /// clamps NOTHING (the call site does that) and returns the coefficients
    /// already divided by a0.  Both matter: the reference vectors were captured
    /// from that exact function, so a "safer" version here would simply be a
    /// different filter.
    public static func peaking(f0: Double, q: Double, gainDB: Double,
                               rate: Double) -> (b: [Double], a: [Double]) {
        let A = pow(10.0, gainDB / 40.0)
        let w0 = 2.0 * Double.pi * f0 / rate
        let alpha = sin(w0) / (2.0 * q)
        let cw = cos(w0)
        let a0 = 1.0 + alpha / A
        let b = [(1.0 + alpha * A) / a0, (-2.0 * cw) / a0, (1.0 - alpha * A) / a0]
        let a = [1.0, (-2.0 * cw) / a0, (1.0 - alpha / A) / a0]
        return (b, a)
    }

    static func convolve(_ x: [Double], _ y: [Double]) -> [Double] {
        var out = [Double](repeating: 0, count: x.count + y.count - 1)
        for (i, xv) in x.enumerated() {
            for (j, yv) in y.enumerated() { out[i + j] += xv * yv }
        }
        return out
    }
}

/// Just enough complex arithmetic for the band-pass pole mapping.  Swift has
/// no complex type in the standard library and pulling in Numerics for four
/// operators would be a dependency for nothing.
struct Complex {
    var re: Double, im: Double
    init(_ r: Double, _ i: Double) { re = r; im = i }

    var magnitude: Double { (re * re + im * im).squareRoot() }

    static func + (a: Complex, b: Complex) -> Complex {
        Complex(a.re + b.re, a.im + b.im)
    }
    static func - (a: Complex, b: Complex) -> Complex {
        Complex(a.re - b.re, a.im - b.im)
    }
    static func * (a: Complex, b: Complex) -> Complex {
        Complex(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re)
    }
    static func * (a: Complex, s: Double) -> Complex {
        Complex(a.re * s, a.im * s)
    }
    static func / (a: Complex, b: Complex) -> Complex {
        let d = b.re * b.re + b.im * b.im
        return Complex((a.re * b.re + a.im * b.im) / d,
                       (a.im * b.re - a.re * b.im) / d)
    }

    /// The principal square root.
    func sqrt() -> Complex {
        let m = magnitude.squareRoot()
        let th = atan2(im, re) / 2.0
        return Complex(m * cos(th), m * sin(th))
    }
}
