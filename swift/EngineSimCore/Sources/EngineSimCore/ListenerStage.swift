//
//  ListenerStage.swift
//  Two radiators, one listener -- overrun darkening, EQ, and the perspective.
//
//  The chain up to here produced a signal AT the tailpipe and another in the
//  engine bay.  Where you are standing decides what reaches you, and every
//  number of it is geometry:
//
//    spreading    g = r_near / r, the free-field 1/r
//    delay        (r - r_near) / c -- the bay and the tailpipe are metres apart
//    partition    a composite: a fraction `alpha` of the boundary is OPENINGS
//                 (footwell holes, shifter boot, underbody gap) that leak flat,
//                 and the rest is sheet metal obeying the mass law
//                 TL = 20 log10(f m) - 47 dB, a 1st-order low-pass whose corner
//                 is the TL = 20 dB point, fc = 2239 / m
//    structure    the DOMINANT in-cabin path in a real car: mounts, driveline
//                 and exhaust hangers shake the shell and the panels
//                 re-radiate inside.  It bypasses the airborne partition,
//                 because it is not airborne.
//    boom         the cabin's lowest longitudinal mode, c / 2L
//    ground       the chase cam's tarmac bounce -- but real asphalt is rough at
//                 centimetre scale, so only the low band reflects coherently
//    flyby        a trackside mic the car drives past: the propagation delay
//                 follows the live distance, and its per-sample ramp IS the
//                 Doppler bend.  No pitch shifter anywhere.
//
//  A race interior is stripped -- bare steel, big openings -- so a race cockpit
//  comes out bright and violent and a luxury one hushed, without either being
//  described anywhere.
//

import Foundation

/// Fractional delay whose length RAMPS across the block: a moving source's
/// propagation delay.  d(delay)/dt = radial velocity / c, so the ramp gives
/// exact Doppler -- the trackside sweep is geometry, not an effect.
struct FlybyDelay {
    private var buf: [Double]
    private var wp = 0
    private var prev = 1.0

    init(maxDelay: Int) { buf = [Double](repeating: 0, count: maxDelay + 4) }

    mutating func process(_ x: [Double], _ dNew: Double) -> [Double] {
        let n = x.count, N = buf.count
        for i in 0..<n { buf[(wp + i) % N] = x[i] }
        let d1 = min(max(dNew, 1.0), Double(N - 3))
        var out = [Double](repeating: 0, count: n)
        for i in 0..<n {
            // numpy linspace(prev, d1, n): the last sample lands exactly on d1
            let t = n > 1 ? Double(i) / Double(n - 1) : 0.0
            let d = prev + (d1 - prev) * t
            let idx = Double(wp + i) - d
            let i0 = Int(idx.rounded(.down))
            let fr = idx - Double(i0)
            let a = buf[((i0 % N) + N) % N], b = buf[(((i0 + 1) % N) + N) % N]
            out[i] = a * (1.0 - fr) + b * fr
        }
        prev = d1
        wp = (wp + n) % N
        return out
    }
}

/// The DSP constants for one perspective.  All derived; see the file comment.
public struct POVGeometry {
    public var gBay = 1.0, gTail = 1.0
    public var dBay = 0, dTail = 0
    public var bayAlpha = 0.2, bayFc = 355.0
    public var tailAlpha: Double? = nil
    public var tailFc: Double? = nil
    public var strct = 0.0, strctFc = 800.0
    public var chassis = 0.0, chassisFc = 90.0
    public var boomF = 0.0
    public var ground: (delay: Int, r: Double)? = nil
    public var flyby = false
}

public struct ListenerState {
    public var rpm = 0.0
    public var speed = 0.0            // m/s, for the flyby
    public var degPerSample = 0.0
    public var crank = 0.0
    public var combLoad = 0.0
    public var injAmt = 0.0
    public init() {}
}

public final class ListenerStage {
    let eng: EnginePreset
    let sampleRate: Double
    let cache: FilterCache
    let rng: PortableRNG
    let layers: LayerStack
    public var pov = "chase"

    var overLP: (Double, Double) = (0, 0)     // two cascaded one-pole states
    var overStarted = false
    var eqLo = Biquad.identity, eqLoKey = ""
    var eqMid = Biquad.identity, eqMidKey = ""
    var eqHi = Biquad.identity, eqHiKey = ""
    var eqPres = Biquad.identity, eqPresKey = ""
    var injBP: Biquad?
    var injBP2: Biquad?                       // order-4 band-pass = two biquads
    var povBuf = [String: [Double]]()
    var povLP = [String: OnePole]()
    var povLPKey = [String: String]()
    var stiff = OnePole.identity, stiffKey = ""
    var boom = Biquad.identity, boomKey = ""
    var flybyDL = FlybyDelay(maxDelay: 12000)
    var flybyLP = OnePole.identity, flybyLPKey = ""
    var trackX = -60.0
    var cabVerb: Reverb
    var roomVerb: Reverb
    var geoCache: (key: String, geo: POVGeometry)?

    public init(engine: EnginePreset, sampleRate sr: Double, cache: FilterCache,
                rng: PortableRNG, layers: LayerStack, block: Int = 256) {
        eng = engine
        sampleRate = sr
        self.cache = cache
        self.rng = rng
        self.layers = layers
        roomVerb = Reverb(sampleRate: sr, block: block)
        // a car interior is a ~2.4 m cavity with heavily absorbent trim, so its
        // comb paths scale down and it dies fast
        cabVerb = Reverb(sampleRate: sr, room: 0.22, feedback: 0.42, block: block)
        // the injector click: a band of noise, order 4 as two cascaded biquads
        let lo = 5000.0 / (sr / 2), hi = min(9000.0, sr * 0.45) / (sr / 2)
        let sec = FilterDesign.bandpassSections(order: 2, low: lo, high: hi)
        injBP = Biquad(b: sec[0].b, a: sec[0].a)
        injBP2 = Biquad(b: sec[1].b, a: sec[1].a)
    }

    // ------------------------------------------------------------- geometry
    public func geometry() -> POVGeometry {
        // a stripped interior: a straight-cut box or a near-open exhaust means
        // a race shell, with no deadening and big openings
        let race = eng.straightCut || eng.exhaustOpenness > 0.85
        let key = "\(pov)|\(race)"
        if let c = geoCache, c.key == key { return c.geo }
        let c = 343.0, sr = sampleRate
        func fcMass(_ m: Double) -> Double { 2238.7 / m }   // TL = 20 dB corner
        var g = POVGeometry()

        switch pov {
        case "cockpit":
            let rBay = 1.5, rTail = 3.2          // head->engine, head->tail exit
            // `alpha` is the measured WHOLE-BODY noise-reduction floor, not an
            // ideal panel's opening area: a real body underperforms the mass
            // law badly in the low-mid (panel resonances, glass coincidence,
            // pass-throughs, and the exhaust run right under the floor pan).
            let nr = eng.cabinNrDb != 0.0 ? eng.cabinNrDb : (race ? 2.0 : 5.0)
            let aFw = min(pow(10.0, -nr / 20.0), 0.9)
            let mFw = race ? 6.3 : 11.0                        // firewall
            let mRr = race ? 6.3 : 13.0, aRr = aFw * 0.95      // floor, bulkhead
            g.gBay = 1.0; g.gTail = rBay / rTail
            g.dBay = 0; g.dTail = Int((rTail - rBay) / c * sr)
            g.bayAlpha = aFw; g.bayFc = fcMass(mFw)
            g.tailAlpha = aRr; g.tailFc = fcMass(mRr)
            // summed over every mount point the structure-borne contribution
            // rivals the airborne one below ~500 Hz, the classic NVH result
            g.strct = race ? 0.55 : 0.40; g.strctFc = 2000.0
            // the hangers bolt the pipe to the floor pan: its low band is
            // re-radiated INSIDE, which is the thump you feel in your chest
            g.chassis = race ? 0.60 : 0.45; g.chassisFc = 90.0
            g.boomF = c / (2.0 * 2.4)                          // ~71 Hz
        case "trackside":
            g.gBay = race ? 0.7 : 0.45; g.gTail = 1.0
            g.bayAlpha = race ? 0.35 : 0.08; g.bayFc = fcMass(6.3)
            g.tailAlpha = nil; g.tailFc = nil
            g.strctFc = 800.0
            g.flyby = true
        default:                                  // chase cam behind the car
            let d = 6.0, hs = 0.3, hr = 1.2, car = 4.5
            let rTail = d, rBay = d + car
            let delta = (d * d + (hs + hr) * (hs + hr)).squareRoot()
                - (d * d + (hr - hs) * (hr - hs)).squareRoot()
            g.gBay = rTail / rBay; g.gTail = 1.0
            g.dBay = Int((rBay - rTail) / c * sr); g.dTail = 0
            // a race car has no sealed bay at all, and even a road car's is
            // open-bottomed -- modelling it as a sealed box deletes the intake
            // and valvetrain texture entirely
            g.bayAlpha = race ? 0.35 : 0.22; g.bayFc = fcMass(6.3)
            g.strctFc = 800.0
            // the whole shell is a large panel radiator: it re-radiates the
            // sub band omnidirectionally, which is the outdoor chest-feel
            g.chassis = 0.30; g.chassisFc = 70.0
            g.ground = (Int(delta / c * sr), 0.8)
        }
        geoCache = (key, g)
        return g
    }

    // ------------------------------------------------------------- the block
    /// - Parameters:
    ///   - sig: the tailpipe signal, bay: the bay bus, bayi: the intake sub-bus
    ///     (a bright OPENING, never the body-panel mass law -- that was turning
    ///     the compressor whistle to mud).
    public func process(_ input: [Double], bay bayIn: [Double],
                        bayi bayiIn: [Double], state s: ListenerState,
                        params P: [String: Double]) -> [Double] {
        var sig = input
        var bay = bayIn
        let n = sig.count, sr = sampleRate
        let p = { (k: String, d: Double) -> Double in P[k] ?? d }
        let live = s.degPerSample > 1e-12

        // --- overrun darkening ----------------------------------------------
        // A motoring engine has no sharp hot blowdown, so its note is
        // physically dark -- not the bright hash a residual synthesis leaves.
        // Fade a low-pass in exactly as the fuel cuts.
        let over = 1.0 - s.combLoad
        if over > 0.05 && live {
            let fcOver = 5200.0 - 3400.0 * over    // 5.2 kHz on power -> 1.8 overrun
            let aLP = exp(-2.0 * Double.pi * fcOver / sr)
            let oma = 1.0 - aLP
            // Keep the DF2T state (a*y), NOT y.  This pole MOVES every block
            // -- its cutoff follows the combustion load -- so storing y would
            // apply the new pole to the old sample and drift.
            var s0 = overLP.0, s1 = overLP.1
            for i in 0..<n {
                let x = sig[i]
                let y0 = oma * x + s0;  s0 = aLP * y0
                let y1 = oma * y0 + s1; s1 = aLP * y1
                sig[i] = (1.0 - over) * x + over * y1
            }
            overLP = (s0, s1)
        }

        // --- the three EQ knobs plus presence --------------------------------
        for (gain, f0, q, filt, key) in [
            (p("eq_low", 0.0), 120.0, 0.7, 0, "lo"),
            (p("eq_mid", 0.0), 850.0, 0.8, 1, "mid"),
            (p("eq_high", 0.0), 4500.0, 0.7, 2, "hi"),
            (p("presence", 0.0), 3000.0, 0.6, 3, "pres"),
        ] as [(Double, Double, Double, Int, String)] {
            guard abs(gain) > 0.1 else { continue }
            let ba = cache.peaking(f0, q, gain)
            let k = "\(ba.b)\(ba.a)"
            switch filt {
            case 0: if eqLoKey != k { eqLo.setCoefficients(b: ba.b, a: ba.a); eqLoKey = k }
                    eqLo.process(&sig)
            case 1: if eqMidKey != k { eqMid.setCoefficients(b: ba.b, a: ba.a); eqMidKey = k }
                    eqMid.process(&sig)
            case 2: if eqHiKey != k { eqHi.setCoefficients(b: ba.b, a: ba.a); eqHiKey = k }
                    eqHi.process(&sig)
            default: if eqPresKey != k { eqPres.setCoefficients(b: ba.b, a: ba.a); eqPresKey = k }
                     eqPres.process(&sig)
            }
            _ = key
        }
        sig = layers.tap(.eq, sig)

        // --- bay-mounted mechanical sources ----------------------------------
        // These belong to the BAY, not the tailpipe: they radiate from the cam
        // covers and the rail, so they get the same perspective as everything
        // else instead of sitting bone-dry against the ear.
        let mech = p("mech", 0.30)
        if mech > 1e-3 && live {
            let spacing = 720.0 / Double(max(2 * eng.numCylinders, 1))
            var nzm = rng.standardNormal(n)
            let rpmFrac = min(s.rpm / max(eng.redlineRpm, 1.0), 1.0)
            let nSc = pow(4.0 / Double(max(eng.numCylinders, 1)), 0.5)
            let amp = mech * 0.050 * nSc * (1.0 - 0.85 * rpmFrac)
            var prev = nzm.first ?? 0
            for i in 0..<n {
                let cur = nzm[i]
                let phT = (s.crank + s.degPerSample * Double(i))
                    .truncatingRemainder(dividingBy: spacing) / spacing
                // a sharp hit with a fast ring-down, differentiated for the
                // click spectrum
                nzm[i] = (cur - prev) * exp(-phT * 13.0)
                prev = cur
                bay[i] += amp * nzm[i]
            }
        }
        if s.injAmt > 1e-3, injBP != nil {
            var nz = rng.standardNormal(n)
            injBP!.process(&nz); injBP2!.process(&nz)
            for i in 0..<n { bay[i] += s.injAmt * nz[i] }
        }

        // --- the listener ----------------------------------------------------
        let geo = geometry()
        var tail = sig
        if geo.dTail > 0 { tail = povDelay(tail, "tail_d", geo.dTail) }
        let tailPre = tail                    // pre-partition, for structure
        if let fc = geo.tailFc, let a = geo.tailAlpha {
            tail = povPartition(tail, "tail_p", a, fc)
        }
        let bayP = geo.dBay > 0 ? povDelay(bay, "bay_d", geo.dBay) : bay
        var bayAir = povPartition(bayP, "bay_p", geo.bayAlpha, geo.bayFc)
        // the intake tract mouth and the atmospheric dump are OPENINGS: a high
        // leak with gentle shading from the arch and ducting, never the body
        // panel's mass law
        let bayiP = geo.dBay > 0 ? povDelay(bayiIn, "bayi_d", geo.dBay) : bayiIn
        let aHi = min(geo.bayAlpha * 2.2 + 0.15, 0.80)
        let bayiAir = povPartition(bayiP, "bayi_p", aHi, 2400.0)
        for i in 0..<n { bayAir[i] += bayiAir[i] }
        if geo.strct > 0.0 {
            // the shell re-radiating the engine's low-mid band inside the
            // cabin -- 2nd order, above the panel response
            let st = povLowPass(povLowPass(bayP, "st1", geo.strctFc),
                                "st2", geo.strctFc)
            for i in 0..<n { bayAir[i] += geo.strct * st[i] }
        }
        for i in 0..<n { sig[i] = geo.gTail * tail[i] + geo.gBay * bayAir[i] }

        if let (dg, _) = geo.ground, dg > 0 {
            // Real asphalt is rough at centimetre scale: the highs scatter
            // diffusely and only the low band comes back coherently.  A
            // full-band copy carves deep comb notches through the presence
            // band, which reads as a hidden muffle rather than as an echo.
            let gref = povLowPass(povDelay(sig, "gnd", dg), "gnd_lp", 1800.0)
            for i in 0..<n { sig[i] += 0.45 * gref[i] }
        }
        if geo.boomF > 0.0 {
            // Below the first panel resonance the partition is
            // STIFFNESS-controlled and transmission loss RISES as frequency
            // falls: deep bass does not flood a cabin.  Without this the sub
            // passes at 0 dB, drowns the level control, and leaves the cockpit
            // quiet AND muffled at the same time.
            let ba = cache.butter(1, 90.0, "high")
            let k = "\(ba.b)\(ba.a)"
            if stiffKey != k { stiff.setCoefficients(b: ba.b, a: ba.a); stiffKey = k }
            stiff.process(&sig)
            let bb = cache.peaking(geo.boomF, 2.2, 5.0)
            let kb = "\(bb.b)\(bb.a)"
            if boomKey != kb { boom.setCoefficients(b: bb.b, a: bb.a); boomKey = kb }
            boom.process(&sig)             // ...and what does get in, booms
        }
        if geo.chassis > 0.0 {
            // The hangers shake the floor pan and the panels re-radiate the
            // pipe's low band inside.  A STRUCTURE path: it legitimately
            // bypasses both the airborne partition and the stiffness
            // high-pass, which model airborne transmission only.
            let chas = povLowPass(tailPre, "chassis", geo.chassisFc)
            for i in 0..<n { sig[i] += geo.chassis * chas[i] }
        }
        if geo.flyby {
            // the car drives past a fixed mic: posts every 200 m, 12 m off the
            // line.  Level, air absorption and Doppler all ride one geometry.
            let v = abs(s.speed)
            var x = trackX + v * (Double(n) / sr)
            if x > 100.0 { x -= 200.0 }
            trackX = x
            let dist = (x * x + 144.0).squareRoot()
            sig = flybyDL.process(sig, dist / 343.0 * sr)
            let g = 12.0 / dist
            for i in 0..<n { sig[i] *= g }
            let fca = min(800.0 + 16000.0 / (1.0 + dist / 30.0), sr * 0.45)
            let ba = cache.butter(1, fca)
            let k = "\(ba.b)\(ba.a)"
            if flybyLPKey != k { flybyLP.setCoefficients(b: ba.b, a: ba.a); flybyLPKey = k }
            flybyLP.process(&sig)
        }

        // --- the space the LISTENER is in ------------------------------------
        // One shared room: the per-component reverbs upstream are source-local
        // (the port, the spool); this is where the ears are.
        if pov == "cockpit" {
            cabVerb.mix = 0.6 * p("reverb", 0.2)
            sig = cabVerb.process(sig)
        } else {
            roomVerb.mix = p("reverb", 0.2) + (eng.hasCat ? 0.05 : 0.0)
            sig = roomVerb.process(sig)
        }
        sig = layers.tap(.cabinRoom, sig)
        return sig
    }

    // ------------------------------------------------------------- helpers
    func povDelay(_ x: [Double], _ key: String, _ d: Int) -> [Double] {
        guard d > 0 else { return x }
        var buf = povBuf[key]
        if buf == nil || buf!.count != d { buf = [Double](repeating: 0, count: d) }
        let y = buf! + x
        povBuf[key] = Array(y.suffix(d))
        return Array(y.prefix(x.count))
    }

    func povLowPass(_ x: [Double], _ key: String, _ fc: Double) -> [Double] {
        let ba = cache.butter(1, fc)
        let k = "\(ba.b)\(ba.a)"
        if povLP[key] == nil {
            povLP[key] = OnePole(b: ba.b, a: ba.a); povLPKey[key] = k
        } else if povLPKey[key] != k {
            povLP[key]!.setCoefficients(b: ba.b, a: ba.a); povLPKey[key] = k
        }
        var y = x
        povLP[key]!.process(&y)
        return y
    }

    /// Openings leak `alpha` flat; the rest is sheet metal on the mass law.
    func povPartition(_ x: [Double], _ key: String, _ alpha: Double,
                      _ fc: Double) -> [Double] {
        let lp = povLowPass(x, key, fc)
        var out = [Double](repeating: 0, count: x.count)
        for i in x.indices { out[i] = alpha * x[i] + (1.0 - alpha) * lp[i] }
        return out
    }
}

