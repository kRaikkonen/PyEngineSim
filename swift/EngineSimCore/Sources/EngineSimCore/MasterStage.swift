//
//  MasterStage.swift
//  Auto-level, distance, road noise, anti-harshness, limiter, output.
//
//  The level control is the part with a real idea in it.  A raw-RMS estimate
//  counts deep bass in full, but the ear barely does -- so every bit of low-end
//  body the chain gained made the control pull the AUDIBLE bands down: the more
//  weight, the more muffled.  Estimating on a ~300 Hz high-passed COPY (a cheap
//  A-weighting) lets bass ride on top instead of spending the gain budget.  The
//  signal itself is untouched; the limiter still guards the peaks.
//
//  The ceiling follows combustion for the same reason: on the overrun a real
//  car gets QUIETER, and a fixed ceiling would pump the residual noise floors
//  up to fill the hole -- which is exactly what a lift-off "white noise" swell
//  is.
//

import Foundation

public struct MasterState {
    public var rpm = 0.0
    public var speed = 0.0
    public var degPerSample = 0.0
    public var combLoad = 1.0
    public var camLump = 0.0          // cam overlap chop + balance-shaft buzz
    public var wobW = 0.0             // firing-rate wobble, rad/sample
    public var pov = "chase"
    public init() {}
}

public final class MasterStage {
    let eng: EnginePreset
    let sampleRate: Double
    let cache: FilterCache
    let rng: PortableRNG
    let layers: LayerStack

    public var agcEnabled = true
    public var volume = 1.0

    var agcHP = OnePole.identity, agcHPKey = ""
    var level = 0.0, gain = 1.0
    var spatial = Biquad.identity, spatialKey = ""
    var roadBP: Biquad
    var roadLP: Biquad
    var f1Env = 0.0, f1Gain = 1.0
    var wobPh = 0.0
    var aaCut: Double?
    var aa = Biquad.identity, aaKey = ""
    var lim = 0.0
    var limStarted = false

    /// RMS of the last block's output -- the loudness readout, and the SPL the
    /// muffler's blow-out reads on the NEXT block.
    public private(set) var lastLevel = 0.0

    public init(engine: EnginePreset, sampleRate sr: Double, cache: FilterCache,
                rng: PortableRNG, layers: LayerStack) {
        eng = engine
        sampleRate = sr
        self.cache = cache
        self.rng = rng
        self.layers = layers
        // road / tyre rumble: a low band that swells with road speed, so the
        // car sounds like it is moving down a street rather than strapped down
        let bp = rbjBandpass(f0: 130.0, q: 0.5, rate: sr)
        roadBP = Biquad(b: bp.b, a: bp.a)
        let lp = FilterDesign.butter(order: 2, wn: 520.0 / (sr / 2), btype: "low")
        roadLP = Biquad(b: lp.b, a: lp.a)
    }

    public func process(_ input: [Double], state s: MasterState,
                        params P: [String: Double]) -> [Float] {
        var sig = input
        let n = sig.count, sr = sampleRate
        let p = { (k: String, d: Double) -> Double in P[k] ?? d }
        guard n > 0 else { return [] }

        // --- auto-level ------------------------------------------------------
        if agcEnabled {
            var est = sig
            let ba = cache.butter(1, 300.0, "high")     // the loudness weighting
            let k = "\(ba.b)\(ba.a)"
            if agcHPKey != k { agcHP.setCoefficients(b: ba.b, a: ba.a); agcHPKey = k }
            agcHP.process(&est)
            var acc = 0.0
            for v in est { acc += v * v }
            let rms = (acc / Double(n)).squareRoot() + 1e-9
            level += (rms - level) * 0.04
            // on the overrun a real car gets quieter, so the ceiling falls with
            // combustion rather than letting the noise floors fill the hole
            let gmax = 2.2 + 3.8 * s.combLoad
            let want = min(0.22 / (level + 1e-6), gmax)
            var rate = want > gain ? 0.05 : 0.2        // rise SLOW: no pump-up
            if s.pov == "trackside" {
                // near-freeze: the fly-by's 1/r sweep IS the drama, and a
                // tracking control flattens a ~20 dB pass to about 9
                rate *= 0.06
            }
            gain += (want - gain) * rate
            for i in 0..<n { sig[i] *= gain }
        } else {
            for i in 0..<n { sig[i] *= 3.5 }
        }

        // --- distance: far away is darker and quieter ------------------------
        let d = 1.0 - p("spatial_y", 0.5)
        if d > 0.02 {
            let cut = min(max(14000.0 - 11500.0 * d, 600.0), sr * 0.45)
            let ba = cache.butter(2, cut)
            let k = "\(ba.b)\(ba.a)"
            if spatialKey != k { spatial.setCoefficients(b: ba.b, a: ba.a); spatialKey = k }
            spatial.process(&sig)
        }
        let dg = 1.0 / (1.0 + 1.7 * d)
        for i in 0..<n { sig[i] *= dg }

        // --- road and tyre rumble --------------------------------------------
        var rn = p("road_noise", 0.22)
        if s.pov == "cockpit" { rn = 0.0 }          // the cockpit hears no wind
        if rn > 1e-3 {
            let spd = min(s.speed / 32.0, 1.0)      // ~115 km/h is full
            if spd > 0.015 {
                var nz = rng.standardNormal(n)
                roadBP.process(&nz)
                var nz2 = rng.standardNormal(n)
                roadLP.process(&nz2)
                for i in 0..<n { sig[i] += rn * spd * (0.8 * nz[i] + 0.25 * nz2[i]) }
            }
        }

        // --- broadcast compression, screamers only ---------------------------
        // A real F1 feed rides heavy programme compression: the wall is DENSE
        // and the dynamic range small.  Trackside is exempt -- that ear hears
        // the raw fly-by sweep, which is the whole point of standing there.
        if s.degPerSample > 1e-12 && eng.redlineRpm >= 11000.0
            && s.pov != "trackside" {
            var acc = 0.0
            for v in sig { acc += v * v }
            let r = (acc / Double(n)).squareRoot()
            f1Env += (r - f1Env) * (r > f1Env ? 0.45 : 0.10)   // fast up, slow down
            let th = 0.22, ratio = 2.0
            let gt = f1Env <= th ? 1.0 : pow(th / f1Env, 1.0 - 1.0 / ratio)
            let gp = f1Gain
            f1Gain += (gt - gp) * 0.5
            for i in 0..<n {
                let t = n > 1 ? Double(i) / Double(n - 1) : 0.0
                sig[i] *= (gp + (f1Gain - gp) * t) * 1.15
            }
        }

        // --- cam-overlap chop and balance-shaft buzz -------------------------
        // a slow amplitude wobble at the firing rate: deep at idle for a big
        // cam, or for a four with no balance shaft
        let lump = s.camLump
        if lump > 1e-3 && s.wobW > 0.0 {
            var ph = wobPh
            for i in 0..<n {
                ph = wobPh + s.wobW * Double(i)
                sig[i] *= 1.0 - lump * (0.5 + 0.5 * sin(ph))
            }
            wobPh = (ph + s.wobW).truncatingRemainder(dividingBy: 2.0 * Double.pi)
        }

        // --- anti-harshness ---------------------------------------------------
        // At very high revs the sharp combustion edges fold past Nyquist into
        // breakup, so the cutoff drops with rpm -- slewed, so it never clicks.
        let rf = min(s.rpm / 13000.0, 1.0)
        var target = min(16500.0 - 7200.0 * rf, sr * 0.46)
        if eng.redlineRpm >= 11000.0 {
            // SCREAMERS ARE EXEMPT: an F1's identity lives at 8-16 kHz, and
            // cutting to ~9 kHz leaves only the periodic core -- an electric
            // drill.  Their harmonic stack is band-limited by construction, so
            // the fold-over this guards against barely applies.
            target = max(target, min(15500.0, sr * 0.46))
        }
        if aaCut == nil { aaCut = target }
        aaCut! += (target - aaCut!) * 0.08
        let ba = FilterDesign.butter(order: 2, wn: aaCut! / (sr / 2), btype: "low")
        let ka = "\(ba.b)\(ba.a)"
        if aaKey != ka { aa.setCoefficients(b: ba.b, a: ba.a); aaKey = ka }
        aa.process(&sig)

        // --- limiter, then the soft clip -------------------------------------
        // A slow peak-follower pulls sustained over-level back so the crests
        // stay in tanh's musical range instead of crushing into breakup.
        let g = volume * p("master", 0.8) * 1.5
        var pk = 1e-9
        for i in 0..<n { sig[i] *= g; pk = max(pk, abs(sig[i])) }
        lim = limStarted ? max(pk, lim * 0.992) : pk
        limStarted = true
        if lim > 1.0 {
            let inv = 1.0 / lim
            for i in 0..<n { sig[i] *= inv }
        }
        var out = [Float](repeating: 0, count: n)
        var acc = 0.0
        for i in 0..<n {
            let v = tanh(sig[i])
            out[i] = Float(v)
            acc += Double(Float(v)) * Double(Float(v))
        }
        lastLevel = (acc / Double(n)).squareRoot()
        return out
    }
}
