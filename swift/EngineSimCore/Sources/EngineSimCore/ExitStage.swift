//
//  ExitStage.swift
//  The tip: from the tailpipe wall to the air behind the car.
//
//    de-honk      scoop the ~1.8 kHz brass formant, add a little body -- the
//                 fix for a small pipe shrieking like a toy trumpet
//    metal ring   two narrow peaks ARE the pipe material: titanium sings
//                 (light, low-loss), cast iron thuds.  Derived from real E,
//                 density and loss factor, not chosen by ear.
//    megaphone    a diverging cone radiates only above c/(2*pi*a_mouth), and
//                 barks hard around it -- the massive midrange of a race exit
//    thunder      big cylinders shove big slugs of gas: a wide 60-250 Hz
//                 rumble modulated at the firing rate, not one bass note
//    reflection   the system's own round trip, fed back weakly
//    radiation    the crucial one -- what a microphone behind the car hears is
//                 NOT the in-duct pressure.  The open end radiates a near-field
//                 piston term and a far-field dQ/dt term, mixed by the
//                 listener's RANGE, and gated by combustion so an overrun stays
//                 dark instead of hissing.
//    tailpipe     the jet itself: shear roar (Lighthill), Karman vortices off
//                 the lip, and the edge tone of the shear layer on the lip
//

import Foundation

/// (frequency factor, ring gain, Q factor) from a material's real elastic
/// modulus, density and loss factor.  `_MATERIAL` / `_material_acoustics`.
public enum WallMaterial {
    static let table: [String: (E: Double, rho: Double, loss: Double)] = [
        "steel": (200.0, 7850.0, 0.0016), "mild_steel": (200.0, 7850.0, 0.0016),
        "stainless": (193.0, 8000.0, 0.0009), "304": (193.0, 8000.0, 0.0009),
        "321": (193.0, 8000.0, 0.0009), "321ss": (193.0, 8000.0, 0.0009),
        "321ti": (150.0, 6200.0, 0.0006), "ss": (193.0, 8000.0, 0.0009),
        "titanium": (116.0, 4500.0, 0.0004), "ti": (116.0, 4500.0, 0.0004),
        "inconel": (205.0, 8440.0, 0.0020),
        "aluminium": (69.0, 2700.0, 0.0002), "aluminum": (69.0, 2700.0, 0.0002),
        "iron": (110.0, 7200.0, 0.0120), "cast_iron": (110.0, 7200.0, 0.0120),
        "ceramic": (300.0, 3800.0, 0.0040),
        "ceramic_coated": (300.0, 3800.0, 0.0040),
        "cgi": (145.0, 7100.0, 0.0060),
        "compacted_graphite": (145.0, 7100.0, 0.0060),
        "magnesium": (45.0, 1800.0, 0.0010), "mag": (45.0, 1800.0, 0.0010),
    ]
    static let reference = (c: 5.048, rho: 7850.0, loss: 0.0016)

    public static func acoustics(_ name: String) -> (mf: Double, gain: Double,
                                                     q: Double) {
        let m = table[name] ?? table["steel"]!
        let mf = (m.E * 1e9 / m.rho).squareRoot() / (reference.c * 1000.0)
        return (mf,
                pow(reference.rho / m.rho, 0.30),      // lighter -> excited more
                pow(reference.loss / m.loss, 0.30))    // low loss -> long sing
    }
}

/// What the exit stage needs to know about this block.
public struct ExitState {
    public var rpm = 0.0
    public var throttle = 0.0
    public var soundSpeed = 540.0
    public var degPerSample = 0.0
    public var crank = 0.0            // audio-clock crank angle, degrees
    public var combLoad = 0.0         // POSITIVE blowdown only -- see below
    public var pov = "chase"          // cockpit | chase | trackside
    public init() {}
}

public final class ExitStage {
    let eng: EnginePreset
    let sampleRate: Double
    let cache: FilterCache
    let rng: PortableRNG
    let layers: LayerStack
    let nchan: Int

    // per-engine constants
    let wallF1: Double, wallF2: Double, wallRing: Double, wallQ: Double
    let megaF: Double, megaAmt: Double
    let thunderBA: (b: [Double], a: [Double])?
    let tailLen: Double

    // persistent filters and lines
    var deHonk = Biquad.identity, deHonkKey = ""
    var wallLow = Biquad.identity, wallLowKey = ""
    var wallPk1 = Biquad.identity, wallPk1Key = ""
    var wallPk2 = Biquad.identity, wallPk2Key = ""
    var mega = Biquad.identity, megaKey = ""
    var megaHi = Biquad.identity, megaHiKey = ""
    var wgHorn: ExhaustWaveguide?
    var thunder = Biquad.identity
    var rumbleBP = Biquad.identity, rumbleKey = ""
    var tailDL: BlockDelay
    var tailLP: Biquad
    var shearBP: Biquad
    var shearHP: Biquad
    var radHP = OnePole.identity, radHPKey = ""
    var vortex = Biquad.identity, vortexKey = ""
    var edge = Biquad.identity, edgeKey = ""
    var radPrev = 0.0
    var grainBP: Biquad
    var gearPhase = 0.0

    /// Absolute jet velocity at the tip, m/s -- the flyby crackle reads it.
    public private(set) var exitVelocity = 0.0

    public init(engine: EnginePreset, sampleRate sr: Double, cache: FilterCache,
                rng: PortableRNG, layers: LayerStack, nchan: Int,
                block: Int = 256) {
        eng = engine
        sampleRate = sr
        self.cache = cache
        self.rng = rng
        self.layers = layers
        self.nchan = nchan
        let r = engine.exhaustRadiusM

        // the wall formants ARE the material
        let mat = WallMaterial.acoustics(engine.wallMaterial)
        wallRing = mat.gain
        wallQ = mat.q
        wallF1 = min(max(2300.0 * (0.024 / r) * mat.mf, 1300.0), 4200.0)
        wallF2 = min(wallF1 * 1.85, sr * 0.42)

        // a diverging cone radiates only above its cutoff, and barks around it
        let m = min(max(engine.megaphone, 0.0), 1.0)
        megaAmt = m
        if m > 0.02 {
            let aMouth = r * (1.0 + 1.5 * m)             // the cone opens the mouth
            let fHorn = min(max(343.0 / (2.0 * Double.pi * aMouth), 500.0), 3200.0)
            megaF = fHorn * 1.3                          // bark just above cutoff
        } else {
            megaF = 0.0
        }

        // litres per cylinder: a 0.5 L cylinder thunders, a 0.25 L screamer barely
        let cylL = (engine.totalDisplacement * 1000.0)
            / Double(max(engine.numCylinders, 1))
        let gThunder = min(max((cylL - 0.16) * 8.5, 0.0), 7.5)
        thunderBA = gThunder > 0.1
            ? FilterDesign.peaking(f0: 78.0, q: 0.5, gainDB: gThunder, rate: sr)
            : nil
        if let t = thunderBA { thunder = Biquad(b: t.b, a: t.a) }

        tailLen = 2.0 * max(engine.exhaustTotalM, 0.5)
        tailDL = BlockDelay(maxDelay: Int(tailLen / 380.0 * sr) + block + 8)
        let tl = FilterDesign.butter(order: 2, wn: 720.0 / (sr / 2), btype: "low")
        tailLP = Biquad(b: tl.b, a: tl.a)
        let sb = rbjBandpass(f0: 2600.0, q: 0.6, rate: sr)
        shearBP = Biquad(b: sb.b, a: sb.a)
        let sh = FilterDesign.butter(order: 2, wn: 900.0 / (sr / 2), btype: "high")
        shearHP = Biquad(b: sh.b, a: sh.a)
        let gb = rbjBandpass(f0: 3200.0, q: 0.7, rate: sr)
        grainBP = Biquad(b: gb.b, a: gb.a)
    }

    /// - Returns: the tailpipe signal, and the gear-grain contribution to the
    ///   BAY bus (those gears never pass the muffler, so they leave separately).
    @discardableResult
    public func process(_ input: [Double], r: Resonance, state s: ExitState,
                        params P: [String: Double],
                        vx: [String: Bool] = [:]) -> (out: [Double],
                                                      bay: [Double]) {
        var sig = input
        let n = sig.count, sr = sampleRate, c = s.soundSpeed
        let p = { (k: String, d: Double) -> Double in P[k] ?? d }
        let on = { (k: String) -> Bool in vx[k] ?? true }
        let live = s.degPerSample > 1e-12

        // --- de-honk: kill the small-trumpet shriek without losing low end ---
        let wt = p("wall_thickness", 0.5)
        if wt > 1e-3 {
            let ba = cache.peaking(1850.0, 1.1, -16.0 * wt)
            retune(&deHonk, &deHonkKey, ba)
            deHonk.process(&sig)
            let b2 = cache.peaking(150.0, 0.7, 4.0 * wt)     // put body back
            retune(&wallLow, &wallLowKey, b2)
            wallLow.process(&sig)
        }
        sig = layers.tap(.wallDeHonk, sig)

        // --- metal ring: the pipe material itself, always on -----------------
        let f1 = wallF1 * (1.0 - 0.18 * wt), f2 = wallF2 * (1.0 - 0.22 * wt)
        // Q from the material damping and the unified system damping: a
        // low-loss wall rings sharper and longer, cast iron is broad and dead
        let sq2 = 0.6 + 0.7 * r.sysQ
        let p1 = cache.peaking(f1, min(3.4 * wallQ * sq2, 12.0),
                               (4.3 - 1.4 * wt) * wallRing)
        retune(&wallPk1, &wallPk1Key, p1)
        wallPk1.process(&sig)
        let p2 = cache.peaking(f2, min(4.2 * wallQ * sq2, 14.0),
                               (3.2 - 1.6 * wt) * wallRing)
        retune(&wallPk2, &wallPk2Key, p2)
        wallPk2.process(&sig)
        sig = layers.tap(.metalRing, sig)

        // --- megaphone -------------------------------------------------------
        if megaF > 0.0 {
            let bm = cache.peaking(megaF, 0.8, 7.5 * megaAmt)
            retune(&mega, &megaKey, bm)
            mega.process(&sig)
            // the horn's far field concentrates power in its passband, not the
            // thin top hash -- trimming it keeps the note high AND full
            let bh = cache.peaking(min(megaF * 2.4, sr * 0.44), 0.7, -3.5 * megaAmt)
            retune(&megaHi, &megaHiKey, bh)
            megaHi.process(&sig)
            // the cone is a real ~0.6 m air column: it rings its own short tail
            if wgHorn == nil { wgHorn = ExhaustWaveguide(maxDelay: 400) }
            let dH = max(Int((2.0 * 0.6 * sr / c).rounded()), 8)
            let gH = min(pow(10.0, -3.0 * Double(dH) / (max(r.rt60, 0.05) * sr)), 0.90)
            let rung = wgHorn!.process(sig, D: dH, g: gH, s: 1.0, lpA: r.rvLP)
            for i in 0..<n { sig[i] += 0.22 * (rung[i] - sig[i]) }
        }
        sig = layers.tap(.megaphone, sig)

        // --- thunder ---------------------------------------------------------
        if thunderBA != nil { thunder.process(&sig) }
        // A real system's low end is not one resonance: every blowdown shoves a
        // turbulent SLUG of gas, so it is a WIDE 60-250 Hz rumble amplitude-
        // modulated at the firing rate.  A point peak hums one note; the
        // firing-gated band is the air-push you feel.
        if live && on("rumble") {
            let cylL2 = (eng.totalDisplacement * 1000.0)
                / Double(max(eng.numCylinders, 1))
            var gRmb = min(max((cylL2 - 0.22) * 1.05, 0.0), 0.72)
            if eng.redlineRpm >= 11000.0 {
                // extreme fire rates merge the slugs into a continuous jet roar
                // whose low end tracks TOTAL mass flow, not litres-per-cylinder
                gRmb = max(gRmb, 0.26)
            }
            if gRmb > 0.01 {
                let spac2 = 720.0 / Double(max(eng.numCylinders, 1))
                var noise = rng.standardNormal(n)
                let ba = rbjBandpass(f0: 130.0, q: 0.6, rate: sr)
                let key = "\(ba.b)\(ba.a)"
                if rumbleKey != key {
                    rumbleBP.setCoefficients(b: ba.b, a: ba.a); rumbleKey = key
                }
                rumbleBP.process(&noise)
                let lvl = 0.30 + 0.70 * r.flow
                for i in 0..<n {
                    let ph = (s.crank + s.degPerSample * Double(i))
                        .truncatingRemainder(dividingBy: spac2) / spac2
                    let envp = exp(-ph * 3.0)             // one slug per firing
                    sig[i] += gRmb * (0.35 + 0.65 * envp) * lvl * noise[i]
                }
            }
        }
        sig = layers.tap(.thunder, sig)

        // The gear grain runs HERE, between the thunder and reflection taps,
        // because it draws from the shared generator: moving it would shift
        // every draw after it and quietly change the shear and vortex noise.
        let bay = gearGrain(frames: n, state: s, params: P)

        // --- the system's own round trip, fed back weakly --------------------
        if live {
            var refl = tailDL.process(sig, delay: tailLen / c * sr)
            tailLP.process(&refl)                 // only lows reflect strongly
            for i in 0..<n { sig[i] += 0.16 * refl[i] }
        }
        sig = layers.tap(.reflection, sig)

        // --- radiation: in-duct pressure -> what a mic behind the car hears --
        var rad = min(max(p("tail_rad", 0.35), 0.0), 0.9)
        // A MOTORING engine has no sharp blowdown to radiate, only smooth air
        // pumping, so the derivative would just brighten the residual hiss into
        // a wash.  Gate on POSITIVE blowdown only: on the overrun the cylinder
        // is in deep vacuum, and taking |pressure| there reads as high load.
        rad *= s.combLoad
        if rad > 1e-3 {
            // A piston radiator, not a pure derivative: the open end radiates
            // with efficiency ~(ka)^2 below its corner and flat above, i.e. a
            // 1st-order high-pass at c/(2*pi*a).  A pure d/dt would be
            // +6 dB/oct forever and starve the radiated low end.  A big tip
            // lowers the corner: the fat pipe IS a low-frequency horn.
            let aTip = max(eng.exhaustRadiusM, 0.012) * max(eng.tipScale, 0.5)
            let fA = min(343.0 / (2.0 * Double.pi * aTip), sr * 0.4)
            var drvFar = [Double](repeating: 0, count: n)
            let scale = sr / (2.0 * Double.pi * 500.0)
            var prev = radPrev
            for i in 0..<n { drvFar[i] = (sig[i] - prev) * scale; prev = sig[i] }
            if n > 0 { radPrev = sig[n - 1] }

            var drv = drvFar
            if on("rad_hp") {
                // SUPERPOSED: an open end radiates both a near-field piston
                // term (keeps the body) and the far-field dQ/dt term.  Near
                // field dies as 1/r^2 against 1/r, so the mix follows the
                // listener's RANGE -- cockpit mostly piston, trackside mostly
                // derivative.  A binary choice was the shortcut; this is the
                // actual field.
                var hpNear = sig
                let ba = cache.butter(1, fA, "high")
                let key = "\(ba.b)\(ba.a)"
                if radHPKey != key {
                    radHP.setCoefficients(b: ba.b, a: ba.a); radHPKey = key
                }
                radHP.process(&hpNear)
                let bias = ["cockpit": 0.25, "chase": 0.0,
                            "trackside": -0.12][s.pov] ?? 0.0
                let wNear = min(max(p("rad_near", 0.20) + bias, 0.0), 0.90)
                for i in 0..<n { drv[i] = wNear * hpNear[i] + (1.0 - wNear) * drvFar[i] }
            }
            for i in 0..<n { sig[i] = (1.0 - rad) * sig[i] + rad * drv[i] }
        }
        sig = layers.tap(.radiation, sig)

        // --- the jet: shear roar, vortices, edge tone ------------------------
        if live {
            let rpmFrac = min(s.rpm / max(eng.redlineRpm, 1.0), 1.0)
            let flow = rpmFrac * (0.35 + 0.65 * s.throttle)
            // ABSOLUTE exit velocity, which is why only an F1 lacked weight:
            // every low-frequency body mechanism lives below 250 Hz, where an
            // engine firing at 1.4 kHz has nothing.  A real F1's wall of sound
            // is its JET.  Same formula gives a 120 m/s cruiser a polite one.
            let qEx = eng.totalDisplacement * s.rpm / 120.0 * 3.0
            let rTip = max(eng.exhaustRadiusM, 0.012) * max(eng.tipScale, 0.5)
            let aTips = Double(nchan) * Double.pi * rTip * rTip
            let uAbs = min(qEx / max(aTips, 1e-4)
                           * (0.35 + 0.65 * min(max(s.throttle, 0.0), 1.0)), 320.0)
            exitVelocity = uAbs
            let jetAmp = min(max(pow(uAbs / 150.0, 2.0), 0.5), 6.0)
            var shearGain = p("shear", 0.10) * flow * jetAmp
                * (0.30 + 0.70 * s.combLoad)
            if !on("noise") { shearGain *= 0.7 }
            if shearGain > 1e-4 {
                var ns = rng.standardNormal(n)
                shearBP.process(&ns)
                shearHP.process(&ns)
                if eng.hasCat { shearGain *= 0.7 }    // a cat car's tip is breathier
                for i in 0..<n { sig[i] += shearGain * ns[i] }
            }
            // Karman vortices shed off the lip at the Strouhal rate f = 0.2 U/d
            // (a dipole, power ~U^6 -> amplitude ~flow^3), and the shear layer
            // grazing the lip edge locks into an EDGE TONE at 0.2 U/t_lip --
            // the thin ripping whistle of a hard pull.  Both die at idle.
            let uEx = 90.0 * flow
            let dTip = 2.0 * rTip
            let aFl = flow * flow * flow
            if aFl > 0.003 {
                let fV = min(max(0.2 * uEx / dTip, 60.0), 900.0)
                var vex = rng.standardNormal(n)
                let bv = rbjBandpass(f0: fV, q: 6.0, rate: sr)
                let kv = "\(bv.b)\(bv.a)"
                if vortexKey != kv { vortex.setCoefficients(b: bv.b, a: bv.a); vortexKey = kv }
                vortex.process(&vex)
                let fE = min(0.2 * uEx / 0.005, sr * 0.42)
                var edg = rng.standardNormal(n)
                let be = rbjBandpass(f0: fE, q: 8.0, rate: sr)
                let ke = "\(be.b)\(be.a)"
                if edgeKey != ke { edge.setCoefficients(b: be.b, a: be.a); edgeKey = ke }
                edge.process(&edg)
                for i in 0..<n { sig[i] += aFl * (0.40 * vex[i] + 0.12 * edg[i]) }
            }
        }
        sig = layers.tap(.tailpipeExit, sig)
        return (sig, bay)
    }

    /// Gear-driven valvetrain / timing-gear WHIR -- a fine dense band of noise
    /// modulated by a mesh tone, so it is a grain riding ON the note rather
    /// than a texture of its own.  It radiates from the BAY, not the tailpipe,
    /// which is why it is returned separately instead of added to `sig`: those
    /// gears never pass the muffler.
    public func gearGrain(frames: Int, state s: ExitState,
                          params P: [String: Double]) -> [Double] {
        var out = [Double](repeating: 0, count: frames)
        let gg = eng.gearGrain * (P["gear_grain"] ?? 1.0)
        guard gg > 1e-3 && s.degPerSample > 1e-12 else { return out }
        let sr = sampleRate
        let rf = min(s.rpm / max(eng.redlineRpm, 1.0), 1.0)
        let fMesh = max(s.rpm / 60.0 * 8.5, 50.0)      // ~8.5x rev: a fine whir
        let inc = 2.0 * Double.pi * fMesh / sr
        var ngr = rng.standardNormal(frames)
        grainBP.process(&ngr)
        let amp = gg * (0.04 + 0.34 * rf)
        for i in 0..<frames {
            let ph = gearPhase + inc * Double(i + 1)
            out[i] = amp * ngr[i] * (0.55 + 0.45 * sin(ph))
        }
        if frames > 0 {
            gearPhase = (gearPhase + inc * Double(frames))
                .truncatingRemainder(dividingBy: 2.0 * Double.pi)
        }
        return out
    }

    @inline(__always)
    private func retune(_ f: inout Biquad, _ key: inout String,
                        _ ba: (b: [Double], a: [Double])) {
        let k = "\(ba.b)\(ba.a)"
        if key != k { f.setCoefficients(b: ba.b, a: ba.a); key = k }
    }
}
