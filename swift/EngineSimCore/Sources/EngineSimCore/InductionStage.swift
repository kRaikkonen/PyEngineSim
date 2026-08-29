//
//  InductionStage.swift
//  The bay bus: everything you hear that is not the exhaust.
//
//  Intake roar through the airbox, the individual-throttle-body howl, blower
//  whine, turbo whistle, the blow-off or the surge flutter, and the gearbox.
//  A real car is not only its tailpipe -- from the driver's seat the intake and
//  the box are most of the character, which is why an RB26 and a 2JZ with the
//  same exhaust still sound nothing alike.
//
//  The blow-off is the part worth reading: it is a JET, not a noise burst with
//  a fade.  Plenum blowdown sets the time constant, isentropic vent velocity
//  sets the speed, Strouhal sets the pitch (so it GLIDES DOWN as the charge
//  bleeds), and Lighthill's u^8 sets the loudness.  The three hardwares differ
//  only in orifice geometry.
//

import Foundation

let perfectFifth: [(Double, Double)] = [(1.0, 1.0), (1.5, 0.6)]
let augTriad: [(Double, Double)] = [(1.0, 1.0), (1.26, 0.5), (1.587, 0.45)]
let turboV7: [(Double, Double)] = [(0.5, 0.55), (1.0, 1.0), (1.25, 0.5),
                                   (1.5, 0.62), (1.78, 0.42)]
let bdimHz: [Double] = [246.94, 293.66, 349.23]     // B - D - F

/// Drivetrain state the bay bus reads.
public struct DriveState {
    public var gear = 0
    public var numGears = 6
    public var clutch = 1.0
    public var speed = 0.0            // m/s
    public var wheelRadius = 0.32
    public var finalDrive = 3.5
    public var gasTorque = 0.0
    public init() {}
}

public struct InductionState {
    public var rpm = 0.0
    public var throttle = 0.0
    public var boost = 0.0
    public var degPerSample = 0.0
    public var hybridOn = true
    public var drive = DriveState()
    public init() {}
}

public final class InductionStage {
    let eng: EnginePreset
    let sampleRate: Double
    let cache: FilterCache
    let rng: PortableRNG
    let nCylinders: Int

    var intakeBP: Biquad
    var intakeLP: Biquad
    var bovJet = Biquad.identity
    var bovJetKey = ""
    var bovLo = Biquad.identity
    var bovLoKey = ""
    var wallOut = Biquad.identity
    var wallGw = Biquad.identity
    var wallKey = ""
    var indReverb: Reverb
    var gearReverb: Reverb

    // oscillator phases, one per voice, so nothing ever clicks on a retune
    var phases = [String: Double]()
    var thrRef = 0.0
    var bovEnv = 0.0
    var bovPr0 = 0.7
    var bdimPhase = 0.0
    var seqPrev = 0.0
    var seqSurge = 0.0
    var flutterPhase = 0.0
    var bovPrev = 0.0

    public var ssqv = false           // atmospheric dump valve
    public var flutter = false        // no valve: compressor surge
    public var oChord = false         // the hidden chord easter egg

    /// The blow-off envelope, read by the caller to duck the exhaust note.
    public var bovEnvelope: Double { bovEnv }

    public init(engine: EnginePreset, sampleRate sr: Double, cache: FilterCache,
                rng: PortableRNG, block: Int = 256) {
        eng = engine
        sampleRate = sr
        self.cache = cache
        self.rng = rng
        nCylinders = engine.numCylinders
        // the airbox note comes from the runner length, not a fixed frequency
        let fIntake = min(max(343.0 / (4.0 * max(engine.intakeRunnerM, 0.05)),
                              90.0), 900.0)
        let bp = FilterDesign.peaking(f0: fIntake, q: 1.1, gainDB: 7.0, rate: sr)
        intakeBP = Biquad(b: bp.b, a: bp.a)
        let lp = FilterDesign.butter(order: 2,
                                     wn: min(2.2 * fIntake + 900.0, sr * 0.45)
                                        / (sr / 2), btype: "low")
        intakeLP = Biquad(b: lp.b, a: lp.a)
        indReverb = Reverb(sampleRate: sr, mix: 0.0, room: 0.7,
                           feedback: 0.7, block: block)
        gearReverb = Reverb(sampleRate: sr, mix: 0.0, room: 0.6,
                            feedback: 0.66, block: block)
    }

    /// A continuous tonal oscillator: a sum of harmonics at `freq`, with the
    /// phase carried per voice so a frequency change never clicks.
    func whine(_ freq: Double, _ frames: Int, _ harmonics: [(Double, Double)],
               phase key: String) -> [Double] {
        let sr = sampleRate
        let ph0 = phases[key] ?? 0.0
        let inc = 2.0 * Double.pi * freq / sr
        var out = [Double](repeating: 0, count: frames)
        let nyq = sr * 0.47
        for (h, a) in harmonics where h * freq < nyq {   // skip aliasing partials
            for i in 0..<frames { out[i] += a * sin(h * (ph0 + inc * Double(i))) }
        }
        phases[key] = (ph0 + inc * Double(frames))
            .truncatingRemainder(dividingBy: 2.0 * Double.pi)
        return out
    }

    /// - Returns: the bay signal to add, and the duck to apply to the exhaust.
    public func process(frames: Int, state s: InductionState,
                        params P: [String: Double]) -> (bay: [Double],
                                                        duck: Double) {
        var bay = [Double](repeating: 0, count: frames)
        let p = { (k: String, d: Double) -> Double in P[k] ?? d }
        let sr = sampleRate
        guard s.degPerSample > 1e-12 else { return (bay, 0.0) }
        let rpmFrac = min(s.rpm / max(eng.redlineRpm, 1.0), 1.0)

        // --- intake roar: the broadband suck through the airbox -------------
        var intakeGain = p("intake", 0.5) * s.throttle * (0.25 + 0.75 * rpmFrac)
        // a blown engine pumps far more air, so the roar swells with the charge
        if eng.boostBar > 0.0 {
            intakeGain *= 1.0 + 1.1 * min(max(s.boost, 0.0) / eng.boostBar, 1.0)
        }
        if intakeGain > 1e-4 {
            var nz = rng.standardNormal(frames)
            intakeBP.process(&nz)
            intakeLP.process(&nz)
            for i in 0..<frames { bay[i] += intakeGain * nz[i] }
        }

        // --- individual throttle bodies: the raw howl ------------------------
        // one trumpet per cylinder sucks a sharp tuned pulse straight past the
        // driver.  A single-plenum intake has none of it -- which is most of
        // what separates two otherwise similar engines.
        if eng.individualThrottle {
            let fireHz = s.rpm * Double(nCylinders) / 120.0
            let thr = min(max(s.throttle, 0.0), 1.0)
            // a screamer's soprano is ~40% intake: bare stacks, no filter, no
            // plenum damping -- so a bigger share and a flat-top harmonic set
            let scrm = eng.redlineRpm >= 11000.0
            let howlGain = (scrm ? 0.38 : 0.16) * (0.15 + 0.85 * thr)
                * pow(rpmFrac, 1.5)
            if howlGain > 1e-4 && fireHz > 20.0 && fireHz < sr * 0.4 {
                let hset: [(Double, Double)] = scrm
                    ? [(1, 1.0), (2, 0.9), (3, 0.8), (4, 0.68), (5, 0.55),
                       (6, 0.42), (8, 0.25), (10, 0.14)]
                    : [(1, 1.0), (2, 0.85), (3, 0.6), (4, 0.42), (5, 0.28),
                       (6, 0.18), (8, 0.10)]
                let howl = whine(fireHz, frames, hset, phase: "itb")
                for i in 0..<frames { bay[i] += howlGain * howl[i] }
            }
        }

        // --- forced induction and the gearbox -------------------------------
        var gw = gearboxAudio(frames: frames, state: s, params: P)
        var ind = inductionAudio(frames: frames, state: s, params: P)
        wallFilter(&ind, &gw, params: P)
        if p("spool_reverb", 0.0) > 1e-3 {
            indReverb.mix = p("spool_reverb", 0.0)
            ind = indReverb.process(ind)
        }
        if p("gearbox_reverb", 0.0) > 1e-3 {
            gearReverb.mix = p("gearbox_reverb", 0.0)
            gw = gearReverb.process(gw)
        }
        for i in 0..<frames { bay[i] += ind[i] + gw[i] }

        // the driver has just LIFTED, so the note collapses and the valve event
        // is what is actually heard -- duck the exhaust rather than bury it
        let duck = min(0.40 * bovEnv, 0.40)
        return (bay, duck)
    }

    // ------------------------------------------------------------------ spool
    func inductionAudio(frames: Int, state s: InductionState,
                                params P: [String: Double]) -> [Double] {
        var out = [Double](repeating: 0, count: frames)
        let p = { (k: String, d: Double) -> Double in P[k] ?? d }
        let sr = sampleRate, rpm = s.rpm
        let sv = p("super_vol", 0.5), tv = p("turbo_vol", 0.5)
        let bfrac = eng.boostBar != 0.0
            ? min(s.boost / max(eng.boostBar, 0.05), 1.0) : 0.0

        // mechanical blower: the rotor pair sings at shaft speed x drive ratio
        if sv > 1e-3 && (eng.induction == "roots" || eng.induction == "centrifugal")
            && bfrac > 0.01 {
            var ratio = eng.blowerRatio > 0 ? eng.blowerRatio : 9.0
            var harm: [(Double, Double)]
            if eng.induction == "centrifugal" {
                ratio *= 2.5                                  // higher-pitched
                harm = [(1, 1.0), (2, 0.25)]
            } else {
                harm = [(1, 1.0), (2, 0.5), (3, 0.28)]        // rich roots whine
            }
            let f = (rpm / 60.0) * ratio
            if f > 20.0 && f < sr * 0.45 {
                let w = whine(f, frames, harm, phase: "whine")
                for i in 0..<frames { out[i] += (sv * bfrac * 0.5) * w[i] }
            }
        }

        if tv > 1e-3 && eng.induction == "turbo" {
            let voicing = oChord ? turboV7 : perfectFifth
            let sub = eng.inductionSubtype
            if bfrac > 0.02 {
                if sub == "sequential" {
                    // the small turbo spools first; the big one hands over up
                    // top with an audible surge whoosh
                    let prim = min(bfrac / 0.5, 1.0)
                    let sec = max(0.0, (bfrac - 0.45) / 0.55)
                    let f1 = min(1600.0 + prim * 4200.0, sr * 0.45)
                    let f2 = min(780.0 + sec * 3500.0, sr * 0.45)
                    let w1 = whine(f1, frames, voicing, phase: "seq")
                    for i in 0..<frames { out[i] += (tv * prim * 0.22) * w1[i] }
                    if sec > 1e-3 {
                        let w2 = whine(f2, frames, voicing, phase: "seq2")
                        for i in 0..<frames { out[i] += (tv * sec * 0.30) * w2[i] }
                    }
                    if sec - seqPrev > 0.004 {         // big turbo coming on-song
                        seqSurge = min(1.0, seqSurge + (sec - seqPrev) * 8.0)
                    }
                    seqPrev = sec
                    if seqSurge > 1e-3 {
                        let nz = rng.standardNormal(frames)
                        for i in 0..<frames {
                            out[i] += (tv * 0.5) * nz[i]
                                * exp(-Double(i) / (sr * 0.18)) * seqSurge
                        }
                        seqSurge *= exp(-Double(frames) / (sr * 0.25))
                    }
                    let nz = rng.standardNormal(frames)
                    for i in 0..<frames { out[i] += (tv * (prim + sec) * 0.16) * nz[i] }
                } else if sub == "twin_scroll" {
                    // a divided housing keeps the pulses apart: tighter, cleaner
                    // whistle with far less air hiss
                    let f = min(1150.0 + bfrac * 5300.0, sr * 0.45)
                    let amp = tv * bfrac * 0.32
                    let w = whine(f, frames, voicing, phase: "whine")
                    let nz = rng.standardNormal(frames)
                    for i in 0..<frames { out[i] += amp * w[i] + (amp * 0.18) * nz[i] }
                } else {
                    let f = 900.0 + bfrac * 5200.0     // whistle rises with boost
                    let amp = tv * bfrac * 0.30
                    let w = whine(min(f, sr * 0.45), frames, voicing, phase: "whine")
                    let nz = rng.standardNormal(frames)
                    for i in 0..<frames { out[i] += amp * w[i] + (amp * 0.5) * nz[i] }
                }
                if sub == "twincharge" {
                    // compound: the blower sings low and crossfades into the
                    // turbo whistle as the revs climb
                    let ratio = eng.blowerRatio > 0 ? eng.blowerRatio : 9.0
                    let fb = (rpm / 60.0) * ratio
                    let low = max(0.0, 1.0 - min(rpm / max(eng.redlineRpm, 1.0),
                                                 1.0) / 0.7)
                    if fb > 20.0 && fb < sr * 0.45 && low > 0.01 {
                        let w = whine(fb, frames, [(1, 1.0), (2, 0.5), (3, 0.28)],
                                      phase: "whine")
                        for i in 0..<frames {
                            out[i] += (sv * (0.3 + 0.5 * low) * 0.5) * w[i]
                        }
                    }
                }
            }
            liftOff(&out, frames: frames, state: s, tv: tv, bfrac: bfrac)
        }

        // --- hybrid power unit: MGU-K and MGU-H ----------------------------
        let hv = p("hybrid_vol", 0.5)
        let mgu = eng.mguWhine
        if hv > 1e-3 {
            // MGU-K (kinetic): a clean motor whine rising with revs and
            // swelling with deployment.  On an F1 PU it is LOUD.
            if eng.hybridKw > 0.0 && s.hybridOn && s.throttle > 0.02 {
                let fm = (rpm / 60.0) * 14.0            // geared-up motor whine
                if fm > 60.0 && fm < sr * 0.45 {
                    let amp = hv * (0.18 + 0.30 * mgu) * min(s.throttle, 1.0)
                    let w = whine(fm, frames, [(1, 1.0), (2, 0.28)], phase: "motor")
                    for i in 0..<frames { out[i] += amp * w[i] }
                }
            }
            // MGU-H (heat): the motor on the TURBO SHAFT spins it ~125,000 rpm,
            // so a modern F1 PU has a piercing electric whistle and no lag --
            // and it keeps singing slightly off the throttle, because the
            // MGU-H holds the turbo spinning to recover heat energy.
            if eng.electricTurbo {
                var present = bfrac
                if mgu > 1e-3 {
                    present = max(bfrac, 0.28 * min(rpm / max(eng.redlineRpm, 1.0),
                                                    1.0))
                }
                if present > 0.02 {
                    let fe = min((1600.0 + 2400.0 * bfrac) * (1.0 + 0.3 * mgu),
                                 5200.0)
                    let amp = hv * (0.20 + 0.38 * mgu) * present
                    let w = whine(min(fe, sr * 0.45), frames,
                                  [(1, 1.0), (2, 0.28)], phase: "ecomp")
                    let nz = rng.standardNormal(frames)
                    for i in 0..<frames { out[i] += amp * w[i] + (amp * 0.10) * nz[i] }
                }
            }
        }
        return out
    }

    /// Pipe-wall thickness dulls the brassy trumpet edge of every whine.  Both
    /// the induction and the gearbox leave through it, each with its own state.
    func wallFilter(_ ind: inout [Double], _ gw: inout [Double],
                    params P: [String: Double]) {
        let wt = P["wall_thickness"] ?? 0.5
        guard wt > 1e-3 else { return }
        let cut = min(max(7000.0 - 5600.0 * wt, 900.0), sampleRate * 0.45)
        let ba = cache.butter(2, cut)
        let key = "\(ba.b)\(ba.a)"
        if wallKey != key {
            wallOut.setCoefficients(b: ba.b, a: ba.a)
            wallGw.setCoefficients(b: ba.b, a: ba.a)
            wallKey = key
        }
        wallOut.process(&ind)
        wallGw.process(&gw)
    }

    // ------------------------------------------------------------- blow-off
    func liftOff(_ out: inout [Double], frames: Int,
                         state s: InductionState, tv: Double, bfrac: Double) {
        let sr = sampleRate
        // A real pedal is RAMPED, so a per-block delta never snaps.  Track a
        // slowly-decaying PEAK of recent throttle instead: the valve fires when
        // the pedal has dropped well below where it lately was, while boost is
        // still up.  The decay MUST be slower than the pedal ramp or the peak
        // just follows the pedal down and no gap ever opens.
        thrRef = max(s.throttle, thrRef * 0.995)
        if (thrRef - s.throttle) > 0.30 && s.boost > 0.10 && bovEnv < 0.5 {
            bovEnv = 1.0
            bovPr0 = max(s.boost, 0.1)       // the trapped charge drives everything
            bdimPhase = 0.0
        }
        guard bovEnv > 1e-3 else { return }

        if oChord {
            // easter egg: the blow-off resolves as a B-diminished chord
            let inc = 2.0 * Double.pi / sr
            for i in 0..<frames {
                var chord = 0.0
                for fz in bdimHz {
                    chord += sin(bdimPhase * (fz / bdimHz[0]) + inc * fz * Double(i))
                }
                out[i] += (tv * 0.7) * chord * exp(-Double(i) / (sr * 0.18)) * bovEnv
            }
            bdimPhase += inc * bdimHz[0] * Double(frames)
            bovEnv *= exp(-Double(frames) / (sr * 0.2))
            return
        }

        // --- the white-box vent jet ----------------------------------------
        let noise = rng.standardNormal(frames)
        let bov = 0.42 + 1.0 * tv
        let V = 0.006, CD = 0.6, C0 = 343.0          // charge piping m^3, Cd, c
        var A = 4.0e-4, D = 0.025                    // stock recirc valve
        if flutter && !ssqv { A = 0.8e-4; D = 0.050 }   // backflow past the wheel
        else if ssqv { A = 3.0e-4; D = 0.020 }          // small throat to free air
        let tau = V / (A * CD * C0)                  // plenum blowdown constant
        let pr = 1.0 + bovPr0 * bovEnv
        let u = min((max(5.0 * (1.0 - pow(pr, -2.0 / 7.0)), 0.0)).squareRoot(), 1.0)
        let fPk = min(max(0.2 * (u * C0) / D, 120.0), sr * 0.42)

        var jet = noise
        let bj = rbjBandpass(f0: fPk, q: 1.1, rate: sr)   // the Strouhal band
        let jkey = "\(bj.b)\(bj.a)"
        if bovJetKey != jkey { bovJet.setCoefficients(b: bj.b, a: bj.a); bovJetKey = jkey }
        bovJet.process(&jet)
        let amp = bov * pow(u, 4.0) * 3.2            // Lighthill u^8 (quadrupole)

        if ssqv {
            for i in 0..<frames { out[i] += (amp * 1.25) * jet[i] }
        } else if flutter {
            // surge is not a steady jet: each cycle is a bulk FLOW REVERSAL, a
            // monopole volume pulse at the big inlet mouth (radiating ~u^2 --
            // the meaty 'tu' thump) with the bright throat hiss on top, both
            // gated by the deep-surge cycle.
            let fl = 13.0 + 11.0 * bfrac
            var thump = noise
            let uMouth = u * C0 * A / (0.785 * D * D)      // m/s at the inlet
            let fLo = min(max(0.2 * uMouth / D, 45.0), 400.0)
            let bl = rbjBandpass(f0: fLo, q: 0.8, rate: sr)
            let lkey = "\(bl.b)\(bl.a)"
            if bovLoKey != lkey { bovLo.setCoefficients(b: bl.b, a: bl.a); bovLoKey = lkey }
            bovLo.process(&thump)
            let mono = bov * (u * u) * 3.9
            var lastPh = flutterPhase
            for i in 0..<frames {
                let ph = flutterPhase + 2.0 * Double.pi * fl * Double(i) / sr
                lastPh = ph
                let pulse = pow(min(max(sin(ph), 0.0), 1.0), 2.0)
                out[i] += ((mono * (1.6 + 0.9 * bfrac)) * thump[i]
                           + (amp * 1.0) * jet[i]) * pulse
            }
            if frames > 0 {
                flutterPhase = lastPh.truncatingRemainder(dividingBy: 2.0 * Double.pi)
            }
        } else {
            // recirc: vented into the intake plumbing, so the pipe run darkens it
            var prev = bovPrev
            for i in 0..<frames {
                let cur = jet[i]
                out[i] += (amp * 0.8) * (0.5 * (cur + prev))
                prev = cur
            }
            if frames > 0 { bovPrev = jet[frames - 1] }
        }
        bovEnv *= exp(-Double(frames) / (sr * tau))
    }

    // -------------------------------------------------------------- gearbox
    func gearboxAudio(frames: Int, state s: InductionState,
                              params P: [String: Double]) -> [Double] {
        var gw = [Double](repeating: 0, count: frames)
        let p = { (k: String, d: Double) -> Double in P[k] ?? d }
        let sr = sampleRate, rpm = s.rpm, dt = s.drive

        // mesh whine on EVERY car, not just dog boxes: the input pair sings at
        // tooth-mesh frequency and its amplitude follows transmitted torque --
        // silent coasting, a fine rising whine under load
        let gm = p("gear_mesh", 0.10)
        if gm > 1e-3 && dt.gear > 0 && dt.clutch > 0.6 && rpm > 400.0 {
            let fMesh = rpm / 60.0 * 21.0
            if fMesh < sr * 0.42 {
                let loadT = min(abs(dt.gasTorque) / 600.0, 1.0)
                let w = whine(fMesh, frames, [(1, 1.0), (2, 0.28)], phase: "mesh")
                for i in 0..<frames { gw[i] += (gm * 0.22 * loadT) * w[i] }
            }
        }

        // Straight-cut dog box: the gears are constant-mesh and the input pair
        // runs at ENGINE speed, so the whine rises through a gear and DROPS on
        // every upshift -- the race-box 'weee-WHEE-weee'.  A road-speed final
        // drive sits underneath as the continuous part.
        let gv = p("gearbox_vol", 0.5)
        guard eng.straightCut && gv > 1e-3 && dt.gear > 0 && rpm > 350.0 else {
            return gw
        }
        let erps = rpm / 60.0
        func gearMeshHz(_ g: Int) -> Double {
            erps * (11.0 + 1.5 * Double(g - 1))      // each gear, its own count
        }
        // (1) the input constant mesh: same pitch in every gear, buzzy
        let fIn = erps * 8.5
        if fIn > 30.0 && fIn < sr * 0.45 {
            let w = whine(fIn, frames, [(1, 1.0), (2, 0.55), (3, 0.28)],
                          phase: "gwinput")
            for i in 0..<frames { gw[i] += (gv * 0.22) * w[i] }
        }
        // (2) the selected, loaded gear: the loudest layer, and it STEPS
        let fSel = gearMeshHz(dt.gear)
        if fSel > 30.0 && fSel < sr * 0.45 {
            let w = whine(fSel, frames, augTriad, phase: "gearbox")
            for i in 0..<frames { gw[i] += (gv * 0.40) * w[i] }
        }
        // (3) the others keep spinning unloaded: a quiet shimmer underneath
        for (off, ph) in [(-1, "gwa"), (2, "gwb")] {
            let g2 = dt.gear + off
            if g2 >= 1 && g2 <= dt.numGears {
                let f2 = gearMeshHz(g2)
                if f2 > 30.0 && f2 < sr * 0.45 {
                    let w = whine(f2, frames, [(1, 1.0), (2, 0.3)], phase: ph)
                    for i in 0..<frames { gw[i] += (gv * 0.07) * w[i] }
                }
            }
        }
        // (4) the crown wheel, tracking ROAD speed
        if dt.speed > 0.4 {
            let wheelRps = dt.speed / (2.0 * Double.pi * max(dt.wheelRadius, 0.05))
            let ff = wheelRps * dt.finalDrive * 9.0
            if ff > 30.0 && ff < sr * 0.45 {
                let w = whine(ff, frames, [(1, 1.0), (2, 0.4)], phase: "finaldrive")
                for i in 0..<frames { gw[i] += (gv * 0.13) * w[i] }
            }
        }
        return gw
    }
}
