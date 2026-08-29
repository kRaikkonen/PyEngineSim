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

    public mutating func reset() { s1 = 0; s2 = 0 }

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

    public mutating func reset() { s1 = 0 }

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

    /// Band-pass as a high-pass * low-pass cascade -- the same choice the
    /// Python makes, and documented there for the same reason: not literally a
    /// Butterworth band-pass, but the same order and, over the one wide band
    /// this chain asks for, the same job.
    public static func bandpass(order: Int, low: Double,
                                high: Double) -> (b: [Double], a: [Double]) {
        let lo = butter(order: order, wn: high, btype: "low")
        let hi = butter(order: order, wn: low, btype: "high")
        return (convolve(lo.b, hi.b), convolve(lo.a, hi.a))
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
