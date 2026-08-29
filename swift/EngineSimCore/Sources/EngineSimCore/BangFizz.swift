//
//  BangFizz.swift
//  Splitting the raw train into the tonal BANG and the turbulent FIZZ.
//
//  The bang is what rings the pipe; the fizz is gas rush, gated by the pulses
//  so it only exists while gas is actually moving.  Keeping them apart is why
//  the note sounds like combustion in a pipe rather than a tone plus hiss.
//
//  The bipolar stage is the one that stops the train being a string of
//  positive lumps: a real port's ACOUSTIC pressure is AC -- the compression
//  spike is followed by a rarefaction undershoot.  It fades out as firings
//  fuse into a continuous roar (above ~900 firings/s the pedestal is real,
//  because the mean pipe pressure genuinely biases the nonlinearities).
//

import Foundation

/// Mirrors audio.py's `_bw` cache, INCLUDING its quirk: the key rounds the
/// cutoff into 8 Hz buckets, but the design uses the exact frequency of
/// whichever call missed the cache first.  The filter therefore depends on
/// call history -- so reproducing the sound means reproducing the cache.
public final class FilterCache {
    private var store: [String: (b: [Double], a: [Double])] = [:]
    public let sampleRate: Double

    public init(sampleRate: Double) { self.sampleRate = sampleRate }

    public func butter(_ order: Int, _ fc: Double,
                       _ btype: String = "low") -> (b: [Double], a: [Double]) {
        let f = min(max(fc, 20.0), sampleRate * 0.49)
        let key = "bw|\(order)|\(Int(f / 8.0))|\(btype)"
        if let hit = store[key] { return hit }
        if store.count > 800 { store.removeAll(keepingCapacity: true) }
        let ba = FilterDesign.butter(order: order, wn: f / (sampleRate / 2),
                                     btype: btype)
        store[key] = ba
        return ba
    }

    public func peaking(_ f0: Double, _ q: Double,
                        _ gainDB: Double) -> (b: [Double], a: [Double]) {
        let key = "pk|\(Int(f0 / 8.0))|\(Int(q * 10.0))|\(Int((gainDB * 4.0).rounded()))"
        if let hit = store[key] { return hit }
        if store.count > 800 { store.removeAll(keepingCapacity: true) }
        let ba = FilterDesign.peaking(f0: max(f0, 20.0), q: max(q, 0.05),
                                      gainDB: gainDB, rate: sampleRate)
        store[key] = ba
        return ba
    }
}

public final class BangFizz {
    let nchan: Int
    let nCylinders: Int
    let sampleRate: Double
    let cache: FilterCache
    var bipolar: [Biquad?]          // one-pole high-pass per channel
    var bipolarKey: [String]
    public var useBipolar = true
    public var useNoise = true

    public init(nchan: Int, nCylinders: Int, sampleRate: Double,
                cache: FilterCache) {
        self.nchan = nchan
        self.nCylinders = nCylinders
        self.sampleRate = sampleRate
        self.cache = cache
        bipolar = [Biquad?](repeating: nil, count: nchan)
        bipolarKey = [String](repeating: "", count: nchan)
    }

    /// Returns (bang, fizz) per channel.  `rng` is drawn in the Python's
    /// order: one block of normals per channel, before the bipolar filter.
    public func process(chans: [[Double]], strength: Double, rpm: Double,
                        degPerSample: Double,
                        rng: PortableRNG) -> (bang: [[Double]], fizz: [[Double]]) {
        var bang = [[Double]](), fizz = [[Double]]()
        let frames = chans.first?.count ?? 0
        for ci in 0..<nchan {
            var e = [Double](repeating: 0, count: frames)
            for i in 0..<frames { e[i] = chans[ci][i] * strength }
            let noise = rng.standardNormal(frames)

            var bangC = [Double](repeating: 0, count: frames)
            for i in 0..<frames { bangC[i] = 0.55 * e[i] }

            if useBipolar && degPerSample > 1e-12 {
                let fireN = rpm * Double(nCylinders) / 120.0
                let mAC = min(max((900.0 - fireN) / 600.0, 0.0), 1.0)
                if mAC > 1e-3 {
                    let fHp = min(max(0.25 * fireN, 18.0), 70.0)
                    let ba = cache.butter(1, fHp, "high")
                    let key = "\(ba.b)\(ba.a)"
                    if bipolarKey[ci] != key {
                        // a new design means a new filter; the Python keeps the
                        // STATE across designs, so carry it rather than reset
                        bipolar[ci] = Biquad(b: ba.b, a: ba.a)
                        bipolarKey[ci] = key
                    }
                    var acp = bangC
                    bipolar[ci]!.process(&acp)
                    for i in 0..<frames {
                        bangC[i] = mAC * acp[i] + (1.0 - mAC) * bangC[i]
                    }
                }
            }
            let nfl = useNoise ? 0.006 : 0.005
            var f = [Double](repeating: 0, count: frames)
            for i in 0..<frames { f[i] = e[i] * noise[i] + nfl * noise[i] }
            bang.append(bangC)
            fizz.append(f)
        }
        return (bang, fizz)
    }
}
