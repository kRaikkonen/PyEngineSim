//
//  PipeStage.swift
//  The exhaust system as three waveguides in SERIES, not three effects in a mix.
//
//  A real system is one continuous gas column: primary -> mid -> total.  Wiring
//  them in series is what makes the per-car voice ride THROUGH the pipe instead
//  of competing with it, and it is why the resonances interact rather than just
//  sum.
//
//  Two details that are easy to miss and both audible:
//
//    detune   banks are never identical -- a few percent between them kills the
//             organ-pipe/flanger tell.  A single-channel car has no bank to
//             detune against, so its total pipe is split into two slightly
//             different lengths instead.
//    direct   an OPEN system transmits the combustion straight out (the
//             chainsaw voice); a boxed one rings its field instead.  So the dry
//             share is 0.20 + 0.62 * openness rather than a fixed mix.
//

import Foundation

public final class PipeStage {
    let nchan: Int
    var primary: [ExhaustWaveguide]
    var mid: [ExhaustWaveguide]
    var total: [ExhaustWaveguide]
    var monoB: ExhaustWaveguide?

    public var useSeries = true

    public init(nchan: Int, maxDelay: Int = 2600) {
        self.nchan = nchan
        primary = (0..<nchan).map { _ in ExhaustWaveguide(maxDelay: maxDelay) }
        mid = (0..<nchan).map { _ in ExhaustWaveguide(maxDelay: maxDelay) }
        total = (0..<nchan).map { _ in ExhaustWaveguide(maxDelay: maxDelay) }
        if nchan == 1 { monoB = ExhaustWaveguide(maxDelay: 1200) }
    }

    /// - Returns: the wet (resonant) sum across channels, already averaged.
    public func process(srcs: [[Double]], combustion: [Double],
                        d1: Int, d2: Int, d3: Int,
                        g1: Double, g2: Double, g3: Double,
                        s: Double, lpA: Double, lpEnd: Double,
                        res1: Double, res2: Double) -> [Double] {
        let frames = combustion.count
        let inv = 1.0 / Double(nchan)
        let resMid = 0.40 * max(res1, res2)
        var wet = [Double](repeating: 0, count: frames)

        for ci in 0..<nchan {
            var exc = [Double](repeating: 0, count: frames)
            for i in 0..<frames {
                exc[i] = srcs[ci][i] + 0.7 * inv * combustion[i]
            }
            let det = nchan > 1
                ? 1.0 + 0.03 * (2.0 * Double(ci) - Double(nchan - 1))
                    / Double(max(nchan - 1, 1))
                : 0.98                       // mono pipe: split-detuned below
            let d2c = max(Int((Double(d2) * det).rounded()), 4)

            let prim = primary[ci].process(exc, D: d1, g: g1, s: s, lpA: lpA)
            var midOut: [Double]
            var tin: [Double]
            var tot: [Double]
            if useSeries {
                var midIn = [Double](repeating: 0, count: frames)
                for i in 0..<frames { midIn[i] = 0.35 * exc[i] + 0.5 * prim[i] }
                midOut = mid[ci].process(midIn, D: d3, g: g3, s: s, lpA: lpA)
                tin = [Double](repeating: 0, count: frames)
                for i in 0..<frames { tin[i] = 0.20 * exc[i] + 0.5 * midOut[i] }
                tot = total[ci].process(tin, D: d2c, g: g2, s: s, lpA: lpEnd)
            } else {
                midOut = mid[ci].process(exc, D: d3, g: g3, s: s, lpA: lpA)
                tin = exc
                tot = total[ci].process(exc, D: d2c, g: g2, s: s, lpA: lpEnd)
            }
            if nchan == 1, let mb = monoB {
                let b = mb.process(tin, D: max(Int((Double(d2) * 1.02).rounded()), 4),
                                   g: g2, s: s, lpA: lpEnd)
                for i in 0..<frames { tot[i] = 0.5 * (tot[i] + b[i]) }
            }
            for i in 0..<frames {
                wet[i] += res1 * prim[i] + resMid * midOut[i] + res2 * tot[i]
            }
        }
        for i in 0..<frames { wet[i] *= inv }
        return wet
    }

    /// An OPEN system transmits the combustion directly; a boxed one rings its
    /// field instead.  This is a LAW in the Python, not a mixer taste.
    public static func directShare(exhaustOpenness: Double) -> Double {
        0.20 + 0.62 * min(max(exhaustOpenness, 0.2), 1.0)
    }
}
