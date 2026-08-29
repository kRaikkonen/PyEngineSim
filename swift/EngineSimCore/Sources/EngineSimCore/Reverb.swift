//
//  Reverb.swift
//  Schroeder reverb -- parallel combs into series all-passes.
//
//  Used all over the chain, not as an effect at the end: the port cavity, the
//  cat, the muffler chambers, the megaphone horn, the wastegate screamer pipe
//  and the induction spool each get their own, because each of them IS a
//  cavity and a real one rings.
//
//  Every delay is at least one block long, so a whole block can be processed
//  without any per-sample feedback -- which is why the Python could vectorise
//  it, and why this is cheap here too.
//

import Foundation

/// Schroeder feedback comb.  Delay >= block, so the whole block is read before
/// any of it is written back.
struct Comb {
    let g: Double
    private var y: [Double]

    init(D: Int, g: Double) {
        self.g = g
        y = [Double](repeating: 0, count: max(D, 1))
    }

    mutating func process(_ x: [Double]) -> [Double] {
        let n = x.count
        var out = [Double](repeating: 0, count: n)
        for i in 0..<n { out[i] = x[i] + g * y[i] }
        y.removeFirst(n)
        y.append(contentsOf: out)
        return out
    }
}

/// Schroeder all-pass diffuser.
struct Allpass {
    let g: Double
    private var xs: [Double]
    private var ys: [Double]

    init(D: Int, g: Double) {
        self.g = g
        xs = [Double](repeating: 0, count: max(D, 1))
        ys = [Double](repeating: 0, count: max(D, 1))
    }

    mutating func process(_ x: [Double]) -> [Double] {
        let n = x.count
        var out = [Double](repeating: 0, count: n)
        for i in 0..<n { out[i] = -g * x[i] + xs[i] + g * ys[i] }
        xs.removeFirst(n); xs.append(contentsOf: x)
        ys.removeFirst(n); ys.append(contentsOf: out)
        return out
    }
}

public struct Reverb {
    public var mix: Double
    private var combs: [Comb]
    private var aps: [Allpass]

    /// `room` < 1 is a smaller, shorter space.  The delay primes are the
    /// classic Schroeder set, scaled from 44.1 kHz to whatever rate we run at
    /// and floored at one block so the block-at-a-time form stays valid.
    public init(sampleRate: Double, mix: Double = 0.16, room: Double = 1.0,
                feedback: Double = 0.78, block: Int = 256) {
        self.mix = mix
        let sc = sampleRate / 44100.0 * room
        combs = [1557, 1617, 1491, 1422].map {
            Comb(D: max(Int(Double($0) * sc), block + 1), g: feedback)
        }
        aps = [556, 441].map {
            Allpass(D: max(Int(Double($0) * sc), block + 1), g: 0.5)
        }
    }

    public mutating func process(_ x: [Double]) -> [Double] {
        let n = x.count
        var acc = [Double](repeating: 0, count: n)
        for i in combs.indices {
            let c = combs[i].process(x)
            for k in 0..<n { acc[k] += c[k] }
        }
        let inv = 1.0 / Double(combs.count)
        for k in 0..<n { acc[k] *= inv }
        for i in aps.indices { acc = aps[i].process(acc) }
        var out = [Double](repeating: 0, count: n)
        for k in 0..<n { out[k] = (1.0 - mix) * x[k] + mix * acc[k] }
        return out
    }
}
