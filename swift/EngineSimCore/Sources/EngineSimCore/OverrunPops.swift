//
//  OverrunPops.swift
//  The bangs on a lift.
//
//  Modelled as little combustion events rather than as a sample: each pop is a
//  sharp transient, a power chord (root, fifth, octave and a sub) whose pitch
//  GLIDES DOWN -- that downward glide is the 'dewp', and a fixed pitch sounds
//  like a firework instead of a car -- and a low thump for body.
//
//  Two things make it behave like the real thing:
//
//    it enters at the HEADER, not at the tailpipe.  So the pops travel the
//    whole pipe: a stock car's get muffled by the cat and the box, an open
//    race system keeps them sharp.  Bolting them on at the exit would make
//    every car pop identically, which is the giveaway.
//
//    it needs the pipe to have been LOADED.  Unburnt charge only lights off
//    if there was fuel going through in the first place, so being on the gas
//    charges a reservoir and coasting drains it -- lift after a hard pull and
//    it crackles, lift after trundling and it barely does.
//
//  Off unless armed: with `enabled` false nothing here draws from the shared
//  generator at all, which is what keeps the reference renders bit-exact.
//

import Foundation

public final class OverrunPops {
    let sampleRate: Double
    let cache: FilterCache
    let rng: PortableRNG
    var lp = Biquad.identity
    var lpKey = ""
    var reverb: Reverb

    /// Armed.  Not on by default -- a car that pops on every lift is a car
    /// nobody wants after ten minutes.
    public var enabled = false

    var wasOnGas = 0.0
    /// How many bangs are LEFT in this lift.  The pipe holds a finite amount
    /// of unburnt charge: once it has burnt off there is nothing to light
    /// until you fill it again.  Without this it crackles all the way down to
    /// idle, which is a fireworks display rather than a car.
    var budget = 0
    var onGas = false
    /// How many have actually gone off.  Counting them from the waveform is
    /// unreliable -- consecutive bangs overlap and read as one -- so the
    /// stage says so itself.
    public private(set) var fired = 0
    var age = 1 << 30
    var length = 0
    var f0 = 120.0
    var amp = 0.0

    public init(sampleRate: Double, cache: FilterCache, rng: PortableRNG,
                block: Int = 256) {
        self.sampleRate = sampleRate
        self.cache = cache
        self.rng = rng
        reverb = Reverb(sampleRate: sampleRate, mix: 0.0, block: block)
    }

    /// One block of pops, to be added at the header.
    public func render(frames: Int, rpm: Double, throttle: Double,
                       idleRpm: Double, redlineRpm: Double, antiLag: Bool,
                       ignitionOn: Bool,
                       params P: [String: Double]) -> [Double] {
        let lvl = P["pops"] ?? 0.6
        // NOTHING happens here when disarmed -- in particular no draw from the
        // shared generator, so arming it cannot shift anything downstream
        guard enabled, lvl > 1e-3 else { return [] }
        let sr = sampleRate

        // being on the gas loads the pipe with fuel that lights off on a lift
        if throttle > 0.5 { wasOnGas = min(1.0, wasOnGas + 0.05) }
        else { wasOnGas *= 0.996 }

        // THE LIFT is the event, not the coasting: crossing from on-gas to
        // shut is what fills the budget, and it only refills by going back on
        // the gas.  How many depends on how loaded the pipe was, so a lift
        // after a hard pull gives four and a lift after trundling gives two.
        let nowOnGas = throttle > 0.5
        if onGas && !nowOnGas {
            budget = 2 + Int((wasOnGas * 2.0).rounded())
        }
        onGas = nowOnGas

        let overrun = ignitionOn && throttle < 0.06 && rpm > idleRpm * 1.5
        // a new pop once the last is mostly done, which is what lets it
        // crackle rather than bang once
        if overrun && budget > 0 && Double(age) > Double(length) * 0.45 {
            let rf = min(rpm / max(redlineRpm, 1.0), 1.0)
            let aggr = antiLag ? 2.4 : 1.0
            let rate = lvl * aggr * (0.06 + 0.55 * rf) * (0.3 + 0.7 * wasOnGas)
            if rng.uniform() < rate {
                let big = rng.uniform() < (antiLag ? 0.3 : 0.14)
                age = 0
                length = Int(sr * (big ? 0.16 : 0.085))
                f0 = (big ? 95.0 : 150.0) * (0.85 + 0.4 * rng.uniform())
                amp = (big ? 1.0 : 0.6) * (0.6 + 0.7 * rng.uniform())
                budget -= 1
                fired += 1
            }
        }

        var out = [Double](repeating: 0, count: frames)
        if age < length {
            let L = Double(length)
            let decay = sr * (L > sr * 0.1 ? 0.06 : 0.03)
            let crack = rng.standardNormal(frames)
            for i in 0..<frames {
                let t = Double(age + i)
                guard t < L else { continue }
                let env = exp(-t / decay)
                // phase is the INTEGRAL of a falling frequency, which is what
                // makes it slide instead of step
                let ph = 2.0 * Double.pi / sr * (f0 * t - f0 * 0.5 * t * t / (2.0 * L))
                let chord = sin(ph) + 0.7 * sin(1.5 * ph) + 0.45 * sin(2.0 * ph)
                    + 0.4 * sin(0.5 * ph)
                let thump = sin(2.0 * Double.pi * 72.0 * t / sr)
                    * exp(-t / (sr * 0.035))
                out[i] = amp * (0.7 * chord * env
                                + 0.6 * crack[i] * exp(-t / (sr * 0.004))
                                + 0.5 * thump)
            }
        }
        age += frames

        // muffle: the cutoff falls as pop_muff rises
        let cut = min(max(9000.0 - 7600.0 * (P["pop_muff"] ?? 0.4), 700.0),
                      sr * 0.45)
        let ba = cache.butter(2, cut)
        let key = "\(ba.b)\(ba.a)"
        if lpKey != key { lp.setCoefficients(b: ba.b, a: ba.a); lpKey = key }
        lp.process(&out)
        let g = lvl * 1.4
        for i in 0..<frames { out[i] *= g }

        let rev = P["pops_reverb"] ?? 0.22
        if rev > 1e-3 {
            reverb.mix = rev
            out = reverb.process(out)
        }
        return out
    }
}
