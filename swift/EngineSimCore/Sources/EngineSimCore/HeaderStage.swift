//
//  HeaderStage.swift
//  From the pipe system to the header collector, then past the head.
//
//    burble   on a closed throttle with the revs still up, unburnt charge
//             lights off in the pipe.  It is multiplicative on |sig| rather
//             than added, because it is the EXISTING gas being disturbed --
//             which is why it disappears with the sound instead of hanging
//             around as a noise floor.
//    shear    high flow and choked flow both add odd-order distortion: the gas
//             is moving fast enough to be nonlinear.  sig + k*sig*|sig|.
//    head LP  the port and head are a duct with mass; a COLD engine is duller
//             still, so the cutoff opens up as it warms.
//
//  Overrun pops live in their own stage; they are off unless armed.
//

import Foundation

public final class HeaderStage {
    let sampleRate: Double
    let cache: FilterCache
    var headLP: Biquad
    var headKey: String = ""
    var burblePrev: Double = 0

    public init(sampleRate: Double, cache: FilterCache) {
        self.sampleRate = sampleRate
        self.cache = cache
        let ba = cache.butter(2, min(11000.0, sampleRate * 0.45), "low")
        headLP = Biquad(b: ba.b, a: ba.a)
        headKey = "\(ba.b)\(ba.a)"
    }

    /// - Parameters:
    ///   - flow: the synth's gas-flow estimate, cold: 0 warm .. 1 stone cold.
    /// - Returns: the signal at the header, and after the head duct.
    public func process(_ sig: [Double], throttle: Double, rpm: Double,
                        idleRpm: Double, flow: Double, choke: Double,
                        cold: Double, degPerSample: Double,
                        rng: PortableRNG) -> (header: [Double], head: [Double]) {
        var out = sig
        let n = out.count

        // --- burble: foot off, revs still up, gas still moving --------------
        let ov = max(0.0, 1.0 - min(max(throttle, 0.0), 1.0) * 6.0)
            * min(flow * 4.0, 1.0)
            * min(rpm / max(idleRpm * 1.5, 1.0), 1.0)
        if ov > 0.03 && degPerSample > 1e-12 {
            var nz = rng.standardNormal(n)
            // three passes of the same one-tap smooth, each seeded from the
            // SAME carried sample -- as the Python does it
            for _ in 0..<3 {
                var prev = burblePrev
                for i in 0..<n {
                    let cur = nz[i]
                    nz[i] = 0.5 * (cur + prev)
                    prev = cur
                }
            }
            burblePrev = n > 0 ? nz[n - 1] : burblePrev
            for i in 0..<n { out[i] += (0.30 * ov) * abs(out[i]) * nz[i] }
        }

        let header = out

        // --- shear: fast gas is nonlinear ----------------------------------
        let kst = 0.26 * flow + 0.34 * choke
        if kst > 0.01 {
            for i in 0..<n { out[i] += kst * out[i] * abs(out[i]) }
        }

        // --- the head duct, duller when cold -------------------------------
        let fc = cold > 0.02
            ? min(11000.0 * (1.0 - 0.30 * cold), sampleRate * 0.45)
            : min(11000.0, sampleRate * 0.45)
        let ba = cache.butter(2, fc, "low")
        let key = "\(ba.b)\(ba.a)"
        if key != headKey {
            headLP = Biquad(b: ba.b, a: ba.a)
            headKey = key
        }
        headLP.process(&out)
        return (header, out)
    }
}
