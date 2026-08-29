//
//  PortableRNG.swift
//  xoshiro256** + Box-Muller, byte-identical to engine_sim.audio.PortableRNG.
//
//  numpy's PCG64 cannot be reproduced here, and with it in the loop this port
//  could only ever be compared to the Python statistically -- "the spectra look
//  close" is exactly the standard that lets a reproduction quietly drift.  With
//  the same generator on both sides the comparison is sample for sample.
//
//  The Python ships numpy's generator when unseeded; this one is what both use
//  when a seed is given, which is how the golden reference is rendered.
//

import Foundation

public final class PortableRNG {
    private var s0: UInt64, s1: UInt64, s2: UInt64, s3: UInt64
    private var spare: Double?

    public init(seed: UInt64) {
        // splitmix64 to spread one seed over the four words
        var x = seed
        var st = [UInt64]()
        for _ in 0..<4 {
            x = x &+ 0x9E37_79B9_7F4A_7C15
            var z = x
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            st.append(z ^ (z >> 31))
        }
        s0 = st[0]; s1 = st[1]; s2 = st[2]; s3 = st[3]
        spare = nil
    }

    @inline(__always)
    private func next() -> UInt64 {
        var r = s1 &* 5
        r = ((r << 7) | (r >> 57)) &* 9
        let t = s1 << 17
        s2 ^= s0
        s3 ^= s1
        s1 ^= s2
        s0 ^= s3
        s2 ^= t
        s3 = (s3 << 45) | (s3 >> 19)
        return r
    }

    /// [0, 1) from the top 53 bits -- the same construction as the Python.
    @inline(__always)
    public func uniform() -> Double {
        Double(next() >> 11) * (1.0 / 9007199254740992.0)
    }

    public func random(_ n: Int) -> [Double] {
        (0..<n).map { _ in uniform() }
    }

    /// Box-Muller, rejecting u1 == 0 so the log stays finite.  The spare is
    /// carried exactly as the Python does, so the sequences cannot diverge.
    public func normal() -> Double {
        if let v = spare { spare = nil; return v }
        var u1 = 0.0
        repeat { u1 = uniform() } while u1 <= 0.0
        let u2 = uniform()
        let r = (-2.0 * log(u1)).squareRoot()
        spare = r * sin(2.0 * Double.pi * u2)
        return r * cos(2.0 * Double.pi * u2)
    }

    public func standardNormal(_ n: Int) -> [Double] {
        (0..<n).map { _ in normal() }
    }
}
