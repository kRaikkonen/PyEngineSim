//
//  SourceStage.swift
//  From the bang/fizz split to the voiced firing event.
//
//  This is where a pulse train becomes a combustion event.  Five things happen
//  and each is audible on its own:
//
//    port cavity reverb   the head and port are a small hard space, and the
//                         bang leaves through them before anything else
//    asymmetry            positive-going pressure is not the mirror of
//                         negative -- a real port pushes harder than it pulls
//    early reflections    the first three round trips of the primary, darker
//                         each time, which is what makes a pipe sound long
//    crack                the differentiated edge, band-limited: the tick you
//                         hear before the thump
//    body                 a chord of high-Q resonators RUNG by that edge, so
//                         it actually rings at pitch instead of being a
//                         band-passed thump
//
//  Then drive (soft saturation, opened up by grit and by choked flow) and a
//  low shelf for weight.
//

import Foundation

/// RBJ constant-peak-gain band-pass, as `_bandpass` in audio.py.
public func rbjBandpass(f0: Double, q: Double,
                        rate: Double) -> (b: [Double], a: [Double]) {
    let w0 = 2.0 * Double.pi * f0 / rate
    let alpha = sin(w0) / (2.0 * q)
    let cw = cos(w0)
    let a0 = 1.0 + alpha
    return ([alpha / a0, 0.0, -alpha / a0],
            [1.0, (-2.0 * cw) / a0, (1.0 - alpha) / a0])
}

/// The firing "voices" -- ratio/level pairs rung by the combustion snap.
/// Index 0 is the engine's own harmonic series, which is what a real engine
/// does; the rest are the musical voicings the Python offers on the V key.
public let fireChords: [[(ratio: Double, level: Double)]] = [
    [(1.0, 1.0), (2.0, 0.62), (3.0, 0.45), (4.0, 0.34), (5.0, 0.27), (6.0, 0.22)],
    [(0.5, 0.6), (1.0, 1.0), (1.26, 0.7), (1.5, 0.7), (2.0, 0.4)],
    [(0.5, 0.5), (1.0, 1.0), (1.0595, 0.8), (2.0, 0.45)],
    [(1.0, 1.0), (1.189, 0.65), (1.414, 0.62), (1.782, 0.5), (0.5, 0.5)],
    [(0.5, 0.55), (1.0, 1.0), (1.189, 0.7), (1.414, 0.6)],
    [(0.5, 0.5), (1.0, 1.0), (1.26, 0.6), (1.5, 0.62), (1.888, 0.45)],
]

/// Multi-tap read from one shared delay line, as `_TapDelay`.
struct TapDelay {
    private var buf: [Double]
    private var wp: Int = 0

    init(size: Int) { buf = [Double](repeating: 0, count: max(size, 8)) }

    mutating func process(_ x: [Double], taps: [Int]) -> [[Double]] {
        let n = x.count, N = buf.count
        for i in 0..<n { buf[(wp + i) % N] = x[i] }
        var outs = [[Double]]()
        for var d in taps {
            d = min(max(d, 1), N - n - 2)
            var o = [Double](repeating: 0, count: n)
            for i in 0..<n { o[i] = buf[((wp + i - d) % N + N) % N] }
            outs.append(o)
        }
        wp = (wp + n) % N
        return outs
    }
}

public final class SourceStage {
    let sampleRate: Double
    let nchan: Int
    let cache: FilterCache
    var srcVerb: [Reverb]
    var erDelay: TapDelay
    var crackLP: Biquad
    var crackHP: Biquad
    var chord: [Biquad]
    var chordKey: String = ""
    var fireLow = Biquad.identity
    var fireLowKey: String = ""

    public var useAsym = true
    public var useEngineSeries = true
    public var fireChordIndex = 0

    public init(sampleRate: Double, nchan: Int, cache: FilterCache,
                block: Int = 256) {
        self.sampleRate = sampleRate
        self.nchan = nchan
        self.cache = cache
        srcVerb = (0..<nchan).map { _ in
            Reverb(sampleRate: sampleRate, mix: 0.16, room: 0.4,
                   feedback: 0.55, block: block)
        }
        erDelay = TapDelay(size: Int(sampleRate * 0.45) + 8)
        let lp = FilterDesign.butter(order: 2,
                                     wn: min(7000.0, sampleRate * 0.46)
                                        / (sampleRate / 2), btype: "low")
        let hp = FilterDesign.butter(order: 2, wn: 700.0 / (sampleRate / 2),
                                     btype: "high")
        crackLP = Biquad(b: lp.b, a: lp.a)
        crackHP = Biquad(b: hp.b, a: hp.a)
        chord = []
    }

    /// One block.  `params` carries the mixer values the Python reads out of
    /// self.params, so the two stay in step without duplicating defaults here.
    public func process(bang: [[Double]], fizz: [[Double]], rpm: Double,
                        nCylinders: Int, exhaustOpenness: Double,
                        choke: Double, d2: Int,
                        params: [String: Double]) -> (dry: [Double],
                                                      voiced: [Double],
                                                      er: [Double],
                                                      srcs: [[Double]]) {
        let frames = bang.first?.count ?? 0
        let p = { (k: String, d: Double) -> Double in params[k] ?? d }

        // --- port/head cavity, then sum the channels ----------------------
        var srcs = [[Double]]()
        var dry = [Double](repeating: 0, count: frames)
        for ci in 0..<nchan {
            srcVerb[ci].mix = p("src_reverb", 0.4)
            var input = [Double](repeating: 0, count: frames)
            for i in 0..<frames {
                input[i] = bang[ci][i] + 0.25 * p("turbulence", 0.5) * fizz[ci][i]
            }
            let s = srcVerb[ci].process(input)
            srcs.append(s)
            for i in 0..<frames { dry[i] += s[i] }
        }
        let inv = 1.0 / Double(nchan)
        for i in 0..<frames { dry[i] *= inv }

        // a real port pushes harder than it pulls
        if useAsym {
            for i in 0..<frames {
                dry[i] += 0.30 * max(dry[i], 0.0) * tanh(abs(dry[i]))
            }
        }

        // --- the first three round trips, darker each time ----------------
        let taps = erDelay.process(dry, taps: [d2, 2 * d2, 3 * d2])
        let t1 = taps[0]
        var d2s = smooth(taps[1])
        let d3s = smooth(smooth(taps[2]))
        let erSc = 1.0 - 0.6 * min(max(exhaustOpenness, 0.2), 1.0)
        var er = [Double](repeating: 0, count: frames)
        for i in 0..<frames {
            er[i] = erSc * (0.34 * t1[i] + 0.20 * d2s[i] + 0.11 * d3s[i])
        }
        d2s = []                        // silence "never mutated" warning

        // --- crack: the differentiated edge, band-limited ------------------
        var snap = [Double](repeating: 0, count: frames)
        var prev = dry.first ?? 0
        for i in 0..<frames { snap[i] = dry[i] - prev; prev = dry[i] }
        var crack = snap
        crackLP.process(&crack)
        crackHP.process(&crack)

        // --- body: a chord of resonators RUNG by that edge -----------------
        var body = [Double](repeating: 0, count: frames)
        let pBody = p("body", 0.0)
        if pBody > 1e-3 {
            let fireLive = max(rpm, 1.0) / 120.0 * Double(nCylinders)
            let root = min(max(fireLive * (p("firing_pitch", 90.0) / 90.0),
                               28.0), 600.0)
            let nyq = sampleRate * 0.45
            let voices = fireChords[fireChordIndex % fireChords.count]
            // RETUNED, never rebuilt: the root tracks the firing frequency, so
            // it moves EVERY block, and rebuilding would zero each resonator's
            // history 125 times a second.  The Python redesigns every block too
            // but carries zi, and these are high-Q -- their whole job is to
            // keep ringing.
            if chord.count != voices.count {
                chord = voices.map { _ in Biquad.identity }
            }
            for (k, v) in voices.enumerated() {
                let ba = rbjBandpass(f0: min(root * v.ratio, nyq), q: 11.0,
                                     rate: sampleRate)
                chord[k].setCoefficients(b: ba.b, a: ba.a)
            }
            chordKey = "\(root)|\(fireChordIndex)"
            for (k, v) in voices.enumerated() {
                var tone = snap
                chord[k].process(&tone)
                for i in 0..<frames { body[i] += v.level * tone[i] }
            }

            for i in 0..<frames { body[i] *= 1.7 }
        }

        // --- mix, drive, weight -------------------------------------------
        let fw = p("fire_weight", 0.5), fg = p("fire_grit", 0.5)
        var voiced = [Double](repeating: 0, count: frames)
        for i in 0..<frames {
            voiced[i] = p("dry", 1.0) * dry[i]
                + p("crack", 0.5) * (1.0 + 1.3 * fg) * crack[i]
                + (1.6 * pBody) * (1.0 + 1.4 * fw) * body[i]
        }
        let drive = p("drive", 0.0) + 1.6 * fg + 0.5 * choke
        if drive > 1e-3 {
            for i in 0..<frames { voiced[i] = tanh(voiced[i] * (1.0 + 7.0 * drive)) }
        }
        if fw > 0.02 {
            let ba = cache.peaking(110.0, 0.6, 10.0 * fw)
            let key = "\(ba.b)\(ba.a)"
            if fireLowKey != key {
                fireLow.setCoefficients(b: ba.b, a: ba.a)
                fireLowKey = key
            }
            fireLow.process(&voiced)
        }
        return (dry, voiced, er, srcs)
    }

    /// The Python's one-tap box smooth, prepending the first sample.
    private func smooth(_ x: [Double]) -> [Double] {
        guard !x.isEmpty else { return x }
        var out = [Double](repeating: 0, count: x.count)
        out[0] = x[0]
        for i in 1..<x.count { out[i] = 0.5 * (x[i] + x[i - 1]) }
        return out
    }
}
