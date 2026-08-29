//
//  Waveguide.swift
//  One exhaust pipe as a lossy feedback comb -- a digital waveguide.
//
//      y[n] = x[n] + s*g*LP(y[n-D])
//
//  D is the round-trip travel time, so the comb resonates exactly where the
//  pipe's standing waves are; s = -1 is the inverting reflection at the open
//  end, which is why a pipe gives ODD harmonics and sounds like a pipe rather
//  than a filter sweep.  The one-pole in the loop is the pipe's treble loss --
//  each round trip comes back darker, as it does in metal.
//
//  D changes every block as the gas temperature changes, which is what makes
//  the note slide with load rather than sit at a fixed pitch.
//
//  The block is walked in segments of at most D samples, so every delayed
//  sample a segment needs is already written before that segment is computed.
//  That is what lets the Python vectorise it and what keeps this cheap.
//

import Foundation

public final class ExhaustWaveguide {
    let maxD: Int
    private var hist: [Double]
    private var lp = OnePole(b: [1.0], a: [1.0])
    private var lpA: Double = -1.0

    public init(maxDelay: Int = 2600) {
        maxD = max(maxDelay, 8)
        hist = [Double](repeating: 0, count: maxD)
    }

    /// - Parameters:
    ///   - g: round-trip gain, s: reflection sign, lpA: loop damping pole.
    public func process(_ x: [Double], D: Int, g: Double, s: Double,
                        lpA a: Double) -> [Double] {
        let n = x.count
        let d = min(max(D, 4), maxD)
        var ext = [Double](repeating: 0, count: maxD + n)
        for i in 0..<maxD { ext[i] = hist[i] }
        let base = maxD
        let sg = s * g
        let useLP = a > 0.0
        if useLP && a != lpA {
            // the loop filter's STATE is carried across designs, as in the
            // Python -- resetting it would click every time D moves
            lp.setCoefficients(b: [1.0 - a], a: [1.0, -a])
            lpA = a
        }

        var p = 0
        while p < n {
            let seg = min(d, n - p)
            let d0 = base + p - d
            var delayed = [Double](repeating: 0, count: seg)
            for i in 0..<seg { delayed[i] = ext[d0 + i] }
            if useLP { lp.process(&delayed) }
            for i in 0..<seg { ext[base + p + i] = x[p + i] + sg * delayed[i] }
            p += seg
        }
        for i in 0..<maxD { hist[i] = ext[ext.count - maxD + i] }
        return Array(ext[base...])
    }
}
