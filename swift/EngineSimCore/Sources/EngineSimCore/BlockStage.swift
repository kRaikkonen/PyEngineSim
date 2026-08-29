//
//  BlockStage.swift
//  The lid on the pot: combustion sealed inside the block, head and piston.
//
//  You never hear a raw detonation from a running engine.  The event is
//  MUFFLED behind the wall mass -- a mass-law low-pass -- and rung at the
//  casting's own structural resonances.  Only what leaves through the tailpipe
//  stays bright, which is why the pipe resonance and the combustion sound like
//  two different things rather than one loud thing.
//
//  What the lid holds back does not vanish: it radiates off the block itself,
//  and that is the bay bus -- the sound of the engine as an object in front of
//  you, separate from the sound coming out of the pipe behind you.
//

import Foundation

public final class BlockStage {
    let seal: Double
    let cache: FilterCache
    var lid: Biquad
    var ring1: Biquad
    var ring2: Biquad
    var bayPrev: Double = 0

    public init(setup: VoicingSetup, sampleRate: Double, cache: FilterCache) {
        self.cache = cache
        seal = setup.blk_seal ?? 0.0
        let fc = setup.blk_fc ?? 800.0
        let f1 = setup.blk_f1 ?? 400.0
        let f2 = setup.blk_f2 ?? 900.0
        let q = setup.blk_q ?? 1.0
        let lp = FilterDesign.butter(order: 2, wn: fc / (sampleRate / 2),
                                     btype: "low")
        lid = Biquad(b: lp.b, a: lp.a)
        let r1 = cache.peaking(f1, q, 5.0)
        let r2 = cache.peaking(f2, q * 0.8, 3.0)
        ring1 = Biquad(b: r1.b, a: r1.a)
        ring2 = Biquad(b: r2.b, a: r2.a)
    }

    /// Returns the sealed combustion event and what the block radiates.
    public func process(_ combustion: [Double]) -> (sealed: [Double],
                                                    bay: [Double]) {
        let n = combustion.count
        guard seal > 0.0 else {
            // no enclosure: a two-tap mass-law crude, as the Python's fallback
            var bay = [Double](repeating: 0, count: n)
            var prev = bayPrev
            for i in 0..<n {
                bay[i] = 0.5 * (0.5 * (combustion[i] + prev))
                prev = combustion[i]
            }
            bayPrev = n > 0 ? combustion[n - 1] : bayPrev
            return (combustion, bay)
        }
        var st = combustion
        lid.process(&st)
        ring1.process(&st)
        ring2.process(&st)
        var sealed = [Double](repeating: 0, count: n)
        var bay = [Double](repeating: 0, count: n)
        for i in 0..<n {
            sealed[i] = (1.0 - seal) * combustion[i] + seal * st[i]
            bay[i] = seal * st[i]
        }
        return (sealed, bay)
    }
}
