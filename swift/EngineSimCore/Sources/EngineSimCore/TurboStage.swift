//
//  TurboStage.swift
//  The turbocharger, heard from the machine -- Synthesizer._turbo_audio and
//  the surge path of _intake_radiate in engine_sim/audio.py (plan s.10, s.14,
//  s.17).
//
//  Per unit, at the shaft's real speed: the blade-pass tones (M_u2^2.5, the
//  measured pod / airbox anchors), the buzz-saw once the inducer tip goes
//  supersonic, the tip-clearance band at half the blade-pass (a small turbo's
//  whistle), the whoosh.  Through the charge-air TWIN at audio rate: the
//  surge -- the flow's own thump, the blow-back jet through the wheel, the
//  stalled wheel, the inlet duct's ring, rotating stall -- sent out through
//  the car's inlet (pod pipe or airbox, then the mouth); and the blow-off
//  jet.  THE FLUTTER IS THE WHISTLE: through a surge the whistle is lit by
//  every re-grip and cut by every reversal.
//
//  The layer keeps the gain the engine had while it fired (the AGC lifts the
//  engine on a lift; under the turbo's run-down that pumped a fading flutter
//  back to full).
//

import Foundation

/// numpy.random.RandomState(7): per unit, uniform(0.3, 1, 12) then
/// uniform(0, 6.283, 12) -- the buzz-saw's uneven blades.
let turboBuzz: [([Double], [Double])] = [
    ([0.35341580256177, 0.8459431545680802, 0.6068864620086254, 0.8064256244816588, 0.9845926583976219, 0.6769471092873036, 0.6507843245619565, 0.35043579335183306, 0.4879072860713098, 0.6499177505778919, 0.7754609972846582, 0.8626173252730627],
     [2.3934531395722667, 0.4142780676098018, 1.8104188004521236, 5.714976134662333, 1.3407001765426092, 2.840694852100503, 5.850767421706124, 0.15644184669883657, 3.773248848427082, 5.969663651098936, 1.4469929888887219, 3.4461621625599794]),
    ([0.9363898624207119, 0.3932186120314751, 0.6663888064716361, 0.8252869013714244, 0.7683092686187396, 0.6274270018214865, 0.44339436320845654, 0.6435361223637492, 0.5606692825695412, 0.6341808083961118, 0.556123270046415, 0.8865425960164823],
     [4.829412283462077, 1.9728285569271564, 3.5978049650019632, 1.7344161706617574, 2.8452121451890338, 2.2177630732286318, 4.1304408246452216, 2.3269158544138255, 2.8844811800918637, 4.5195134617243085, 2.594827662322223, 5.69505740015954]),
    ([0.42631613344187713, 0.818783211039285, 0.5956618305501982, 0.5985175008794597, 0.7440659080436871, 0.6660343407198417, 0.5904201849076103, 0.3009988163939307, 0.3645836420927264, 0.7965760756076489, 0.667041917735638, 0.7873123244618783],
     [6.003207473427384, 4.290747747040749, 0.33380756349819235, 1.9405214189992375, 3.7232724204527523, 1.4772615187986482, 6.062912790085486, 5.937737990090124, 5.330502734303199, 2.9676116686800316, 5.28699819970663, 0.8237681658719432]),
    ([0.5161135601084847, 0.624097475908092, 0.8192930404784267, 0.6400776600962277, 0.3958132831582156, 0.5404755707930509, 0.5270983187707101, 0.5102932330226277, 0.41585098032605117, 0.5904312408012764, 0.6136844602519063, 0.842430263069812],
     [5.003722773016753, 3.2821771742341275, 2.8941401507938687, 4.889516058500303, 5.574836484490733, 4.240514631073027, 5.029409864857347, 5.900436635499321, 0.2554404506906968, 5.5018454473587335, 1.7376457842104762, 2.9892283498825267]),
]

/// What the stage reads each block besides the machine.
public struct TurboBlockInput {
    public var rpm = 0.0
    public var superVol = 0.6        // P["super_vol"] (the twincharge blower)
    public var turboVol = 0.40       // P["turbo_vol"]
    public var bladeVol = 1.0        // P["blade_vol"]
    /// the AGC's gain and the combustion load as the PREVIOUS block left them
    public var gain = 1.0
    public var combLoad = 1.0
    public var agcEnabled = true
    /// the manifold (Pa), for the inlet's Helmholtz damping
    public var mapPa = 101325.0
    public init() {}
}

public final class TurboStage {
    // the sources' levels (Synthesizer._TB_*)
    static let tone = 0.36, tonePod = 1.43, buzz = 0.050, tcn = 0.316
    static let flutterLit = 5.5, tcnQ = 20.0, whoosh = 0.030
    static let surgeK = 60.0, wHP = 3.0, cF = 0.3, mono = 0.25, st = 0.2, hissQ = 0.7
    static let swCF = 0.1, swSt = 0.1, swChord = 0.4, swLc = 0.1, swQ = 0.6
    static let rsF = 0.7, rsQ = 5.0, rs = 0.25, bov = 4.0, ductDB = 10.0, ductQ = 12.0
    // the inlet (module constants in audio.py)
    static let airboxVolX = 3.0, airboxDuctM = 0.40, airboxHfTlDB = 12.0, podDuctM = 0.25

    let eng: EnginePreset
    let sr: Double
    let cache: FilterCache
    let rng: PortableRNG
    let ve: Table2D
    public let ts: TurboSystem
    /// The audio-rate twin -- made, with the rest of the per-unit state, on
    /// the FIRST block (as the Python's lazily built _tw), after that block's
    /// machine step: its shaft speeds and flows are where the tones start.
    public private(set) var tw: Twin!

    /// The app's valve switches (they ARE the machine's valve: "none" /
    /// "atmo" / "recirc"), the inlet, and the easter egg.
    public var flutter: Bool
    public var ssqv: Bool
    public var podFilter: Bool
    public var oChord = false

    // per-unit state (_tw_*)
    var om = [Double](), w = [Double](), ph = [Double]()
    var pLast = Turbo.pAtm
    var env = 0.0
    var u2r = [Double](), wref = [Double]()
    var gate = [Double]()
    var tail = 0
    var srg = [Double]()
    var u2s = [Double?]()
    var sub0 = [Double?]()
    var filters = [String: Biquad]()
    var whinePhase = 0.0
    var bdimPhase = 0.0
    var tlGref: Double? = nil
    var inlWG: ExhaustWaveguide? = nil

    /// The valve's and the surge's real activity (Python's _bov_env).
    public private(set) var bovEnv = 0.0
    /// Any unit surging (its flow reversed within the last quarter second).
    public var surging: Bool { srg.contains { $0 > 0.0 } }

    public init(engine: EnginePreset, ve: Table2D, turbo ts: TurboSystem,
                sampleRate: Double, cache: FilterCache, rng: PortableRNG) {
        eng = engine
        sr = sampleRate
        self.cache = cache
        self.rng = rng
        self.ve = ve
        self.ts = ts
        flutter = ts.air.bovMode == "none"
        ssqv = ts.air.bovMode == "atmo"
        podFilter = engine.podIntake
    }

    func start() {
        tw = Twin(ts)
        let n = ts.units.count
        om = ts.units.map { $0.omega }
        w = ts.units.map { $0.wC }
        pLast = ts.air.pP
        ph = [Double](repeating: 0, count: n)
        // the surge's fixed flow unit: each wheel's zero-slope flow at its
        // RATED speed (build(): omega_max = 1.15 x rated)
        let omR = ts.omegaMax / 1.15
        u2r = []
        wref = []
        for u in ts.units {
            var u2 = 400.0, wr = 0.46 * ts.wAirRated / Double(n)
            if omR > 0.0 {
                u2 = omR * 0.5 * u.comp.d2
                wr = u.comp.line(u2: u2, p01: ts.p01, t01: Turbo.tAmb).wZ
            }
            u2r.append(max(u2, 1.0))
            wref.append(max(wr, 1e-3))
        }
        gate = [Double](repeating: 0, count: n)
        srg = [Double](repeating: 0, count: n)
        u2s = [Double?](repeating: nil, count: n)
        sub0 = [Double?](repeating: nil, count: n)
    }

    // ------------------------------------------------------------- helpers
    /// lfilter(b, a, x, zi=self._tw_zi[key]) -- the state kept per key across
    /// redesigns, zeros the first time.
    func filt(_ key: String, _ ba: (b: [Double], a: [Double]), _ x: [Double],
              initState: Double? = nil) -> [Double] {
        var f: Biquad
        if let have = filters[key] {
            f = have
            f.setCoefficients(b: ba.b, a: ba.a)
        } else {
            f = Biquad(b: ba.b, a: ba.a)
            if let s = initState { f.setState(s) }
        }
        var y = x
        f.process(&y)
        filters[key] = f
        return y
    }

    /// _tw_band: white noise through a band-pass at f0 (1/24-octave steps),
    /// scaled to unit rms.
    func band(_ noise: [Double], _ f0In: Double, _ q: Double, _ key: String) -> [Double] {
        let f0 = pow(2.0, (24.0 * log2(f0In)).rounded(.toNearestOrEven) / 24.0)
        var y = filt(key, rbjBandpass(f0: f0, q: q, rate: sr), noise)
        let s = (0.5 * sr / min(0.5 * Double.pi * f0 / q, 0.5 * sr)).squareRoot()
        for i in y.indices { y[i] *= s }
        return y
    }

    /// np.interp at 0..frames-1 over the twin's substep clock.
    static func interp(_ xs: [Double], _ fp: [Double], _ frames: Int) -> [Double] {
        var out = [Double](repeating: 0, count: frames)
        var j = 0
        let last = xs.count - 1
        for i in 0..<frames {
            let x = Double(i)
            while j < last && xs[j + 1] <= x { j += 1 }
            if j >= last || xs[j] == x {
                out[i] = fp[j]
            } else {
                let slope = (fp[j + 1] - fp[j]) / (xs[j + 1] - xs[j])
                out[i] = slope * (x - xs[j]) + fp[j]
            }
        }
        return out
    }

    /// The intake mouth, sized to pass peak airflow at ~35 m/s.
    var mouthRadius: Double {
        let q = ts.spec.vd * eng.redlineRpm / 120.0 * eng.veMax * (1.0 + max(eng.boostBar, 0.0))
        return (q / (Double.pi * 35.0)).squareRoot()
    }

    func whine(_ freq: Double, _ frames: Int, _ harmonics: [(Double, Double)]) -> [Double] {
        let ph0 = whinePhase
        let inc = 2.0 * Double.pi * freq / sr
        var out = [Double](repeating: 0, count: frames)
        let nyq = sr * 0.47
        for (h, a) in harmonics where h * freq < nyq {
            for i in 0..<frames { out[i] += a * sin(h * (ph0 + inc * Double(i))) }
        }
        whinePhase = (ph0 + inc * Double(frames)).truncatingRemainder(dividingBy: 2.0 * Double.pi)
        return out
    }

    /// _intake_radiate(src, "surge"): the car's inlet between the compressor
    /// and the air -- the pod's pipe, or the airbox (a Helmholtz low-pass with
    /// its high-frequency path past the box's cross-mode) -- then the mouth's
    /// piston high-pass.
    func intakeRadiate(_ src: [Double], rpm: Double, mapPa: Double) -> [Double] {
        let aM = max(mouthRadius, 0.01)
        let AM = Double.pi * aM * aM
        let vd = max(ts.spec.vd, 1e-4)
        let mapF = mapPa / Turbo.pAtm
        let qAir = vd * max(rpm, 1.0) / 120.0 * ve.eval(rpm, mapF) * mapF
        let u = max(qAir / AM, 0.5)
        var x = src
        if podFilter {
            if inlWG == nil { inlWG = ExhaustWaveguide(maxDelay: Int(0.02 * sr) + 8) }
            let lPipe = eng.podPipeM > 0 ? eng.podPipeM : Self.podDuctM
            let L = lPipe + 0.61 * aM
            let lp = exp(-2.0 * Double.pi * 0.765 * 343.0 / (2.0 * Double.pi * aM) / sr)
            let d = max(Int((2.0 * L / 343.0 * sr - lp / (1.0 - lp)).rounded(.toNearestOrEven)), 4)
            x = inlWG!.process(x, D: d, g: 0.88, s: -1.0, lpA: lp)
        } else {
            let lEff = Self.airboxDuctM + (0.61 + 0.85) * aM
            let wH = 343.0 * (AM / (Self.airboxVolX * vd * lEff)).squareRoot()
            let qH = min(max(wH * lEff / u, 0.5), 20.0)
            // biquad_lp
            let w0 = min(wH / sr, 0.45 * Double.pi)
            let cw = cos(w0), al = sin(w0) / (2.0 * qH)
            let a0 = 1.0 + al
            let b = [0.5 * (1.0 - cw) / a0, (1.0 - cw) / a0, 0.5 * (1.0 - cw) / a0]
            let a = [a0 / a0, -2.0 * cw / a0, (1.0 - al) / a0]
            var xBox = filt("surge_box", (b, a), x)
            let fX = 343.0 / (2.0 * pow(Self.airboxVolX * vd, 1.0 / 3.0))
            let xHf = filt("surge_boxhf", cache.butter(2, fX, "high"), x)
            let k = pow(10.0, -Self.airboxHfTlDB / 20.0)
            for i in xBox.indices { xBox[i] = xBox[i] + k * xHf[i] }
            x = xBox
        }
        let fA = min(343.0 / (2.0 * Double.pi * aM), sr * 0.45)
        return filt("surge_rad", cache.butter(1, fA, "high"), x)
    }

    // ------------------------------------------------------------- the block
    public func process(frames: Int, input I: TurboBlockInput) -> [Double] {
        var out = [Double](repeating: 0, count: frames)
        guard frames > 0 else { return out }
        let tv = I.turboVol
        let rpm = I.rpm
        // the app's Flutter / SSQV switches ARE the valve
        ts.air.bovMode = flutter ? "none" : (ssqv ? "atmo" : "recirc")
        if tw == nil { start() }
        let nSub = max(frames / 8, 1)
        tw.run(dt: Double(frames) / sr, nSub: nSub)
        let rec = tw.record
        let nr = rec.count
        var xs = [Double](repeating: -1.0, count: nr + 1)
        let step = Double(frames) / Double(nSub)
        for k in 0..<nr { xs[k + 1] = (Double(k) + 1.0) * step - 1.0 }
        var fpP = [pLast]
        fpP.append(contentsOf: rec.pP[0..<nr])
        let pArr = Self.interp(xs, fpP, frames)
        let wb = Array(rec.wBov[0..<nr])
        pLast = rec.pP[nr - 1]
        let a01 = (1.4 * 287.0 * 298.15).squareRoot()
        let nyq = 0.45 * sr
        var noise: [Double]? = nil
        var envT = 0.0
        let busy = tw.busy > 0
        var surge = [Double](repeating: 0, count: frames)
        let fr = Double(frames)
        func ramp(_ i: Int) -> Double { (Double(i) + 1.0) / fr }

        for (j, u) in ts.units.enumerated() {
            guard let tab = u.table else { continue }
            let ln = tab.ln
            var fpW = [w[j]]
            fpW.append(contentsOf: rec.wC[j][0..<nr])
            let wj = Self.interp(xs, fpW, frames)
            w[j] = wj[frames - 1]
            let om0 = om[j], om1 = u.omega
            om[j] = om1
            var omA = [Double](repeating: 0, count: frames)
            for i in 0..<frames { omA[i] = om0 + (om1 - om0) * (Double(i) + 1.0) / fr }
            var phA = [Double](repeating: 0, count: frames)
            var cum = 0.0
            for i in 0..<frames { cum += omA[i]; phA[i] = ph[j] + cum / sr }
            ph[j] = phA[frames - 1].truncatingRemainder(dividingBy: 2.0 * Double.pi)
            let c = u.comp
            var u2 = [Double](repeating: 0, count: frames)
            var mU = [Double](repeating: 0, count: frames)
            var phi = [Double](repeating: 0, count: frames)
            let rho = ln.rho
            for i in 0..<frames {
                u2[i] = omA[i] * (0.5 * c.d2)
                mU[i] = u2[i] / a01
                phi[i] = wj[i] / max(rho * u2[i] * c.d2 * c.d2, 1e-6)
            }
            let phiZ = Turbo.phiZSL
            var fwd = [Double](repeating: 0, count: frames)
            for i in 0..<frames {
                let x = min(max(phi[i] / phiZ, 0.0), 1.0)
                fwd[i] = x * x
            }
            let fS = om1 / (2.0 * Double.pi)
            let z = Double(c.zMain)
            // a SURGE is on while the flow reverses (and 0.25 s after)
            let s0 = srg[j]
            let wMin = wj.min() ?? 0.0
            let s1 = wMin < 0.0 ? 1.0 : max(s0 - fr / (0.25 * sr), 0.0)
            srg[j] = s1
            var sg = [Double](repeating: 0, count: frames)
            for i in 0..<frames { sg[i] = s0 + (s1 - s0) * (Double(i) + 1.0) / fr }
            // THE FLUTTER IS THE WHISTLE, lit by each re-grip ~U2^2
            if s0 <= 0.0 { u2s[j] = u2[0] }
            let u2ref = max(u2s[j] ?? u2[0], 1.0)
            var lit = [Double](repeating: 0, count: frames)
            var gWh = [Double](repeating: 0, count: frames)
            for i in 0..<frames {
                let r = u2[i] / u2ref
                let kRg = r * r
                lit[i] = 1.0 + (Self.flutterLit - 1.0) * sg[i] * min(kRg, 1.0)
                let g = min(max(1.0 + phi[i] / (0.5 * phiZ), 0.0), 1.0)
                gWh[i] = g * g
            }
            func fade(_ f: Double) -> Double { min(max((nyq - f) / (0.05 * sr), 0.0), 1.0) }
            let gBp = podFilter ? Self.tonePod / Self.tone : 1.0
            let bv = I.bladeVol
            let f1 = fade(z * fS), f2 = fade(2.0 * z * fS), f3 = fade(fS), f4 = fade(2.0 * fS)
            let gb = gBp * bv
            for i in 0..<frames {
                let a = tv * Self.tone * pow(mU[i], 2.5) * gWh[i]
                let p = phA[i]
                var tone = gb * (f1 * sin(z * p) + 0.35 * f2 * sin(2.0 * z * p))
                    + 0.5 * f3 * sin(p) + 0.2 * f4 * sin(2.0 * p)
                if oChord {
                    for (hm, ha) in turboV7 {
                        tone = tone + 0.6 * ha * fade(hm * 4.0 * fS) * sin(hm * 4.0 * p)
                    }
                }
                out[i] += (a * lit[i]) * tone
            }
            // buzz-saw: the inducer tip's relative Mach past 1
            var mRel = [Double](repeating: 0, count: frames)
            var over = [Double](repeating: 0, count: frames)
            var overMax = 0.0
            for i in 0..<frames {
                let cAx = wj[i] / (rho * c.aAnn)
                let t = omA[i] * 0.5 * c.d1s
                mRel[i] = (cAx * cAx + t * t).squareRoot() / a01
                over[i] = min(max(mRel[i] - 1.0, 0.0), 0.5)
                overMax = max(overMax, over[i])
            }
            if overMax > 1e-3 {
                let (amps, phs) = turboBuzz[j % turboBuzz.count]
                var bz = [Double](repeating: 0, count: frames)
                for k in 0..<12 where Double(k + 1) * fS < nyq {
                    for i in 0..<frames { bz[i] += amps[k] * sin(Double(k + 1) * phA[i] + phs[k]) }
                }
                for i in 0..<frames { out[i] += tv * Self.buzz * over[i] * fwd[i] * bz[i] }
            }
            if noise == nil { noise = rng.standardNormal(frames) }
            let nz = noise!
            // tip-clearance noise: a narrow band at ~half the blade-pass
            var sub = [Double](repeating: 0, count: frames)
            for i in 0..<frames { sub[i] = min(max((1.05 - mRel[i]) / 0.15, 0.0), 1.0) }
            if s1 <= 0.0 && s0 <= 0.0 { sub0[j] = sub[frames - 1] }
            let subHold = sub0[j] ?? sub[0]
            for i in 0..<frames { sub[i] = (1.0 - sg[i]) * sub[i] + sg[i] * subHold }
            let fT = min(0.5 * z * fS, nyq)
            if fT > 200.0 {
                let nb = band(nz, fT, Self.tcnQ, "tcn\(j)")
                let anchor = podFilter ? Self.tonePod : Self.tone
                for i in 0..<frames {
                    let aBp = anchor * pow(mU[i], 2.5) * (1.0 / 2.0.squareRoot())
                    out[i] += (tv * Self.tcn * I.bladeVol * aBp) * gWh[i] * sub[i] * lit[i] * nb[i]
                }
            }
            // whoosh: the inlet duct's cut-on to ~0.8 x BPF
            let fLo = 1.8412 * a01 / (Double.pi * 1.2 * c.d1s)
            let fHi = min(0.8 * z * fS, nyq)
            if fHi > 1.2 * fLo {
                let fc = (fLo * fHi).squareRoot()
                let q = fc / (fHi - fLo)
                let fq = pow(2.0, (12.0 * log2(fc)).rounded(.toNearestOrEven) / 12.0)
                let nw = filt("wh\(j)", rbjBandpass(f0: fq, q: max(q, 0.5), rate: sr), nz)
                for i in 0..<frames {
                    let d = (phi[i] - 0.060) / 0.025
                    let g = exp(-(d * d)) * (phi[i] > 0.0 ? 1.0 : 0.0)
                    out[i] += tv * Self.whoosh * pow(mU[i], 3.0) * g * nw[i]
                }
            }
            // SURGE, in one unit: the flow over the rated zero-slope flow
            let wR = wref[j]
            let bwL = cache.butter(1, Self.wHP)
            let wlp = filt("wlp\(j)", bwL, wj, initState: (bwL.b[1] - bwL.a[1]) * wj[0])
            let g0 = gate[j], g1 = min(Double(tw.busy) / 8.0, 1.0)
            gate[j] = g1
            if busy || g0 > 0.0 {
                // (1) the flow itself, a monopole at the mouth
                for i in 0..<frames {
                    let gt = g0 + (g1 - g0) * (Double(i) + 1.0) / fr
                    surge[i] += tv * Self.surgeK * Self.mono * gt * (wj[i] - wlp[i]) / wR
                }
                var rev = [Double](repeating: 0, count: frames)
                var revMax = 0.0
                for i in 0..<frames { rev[i] = max(-wj[i], 0.0); revMax = max(revMax, rev[i]) }
                if revMax > 1e-5 {
                    // (2) the blow-back jet through the spinning wheel (Curle)
                    let aRev = Turbo.cRev * c.aEye
                    let tp = max(ts.tP, 250.0)
                    var rhoP = [Double](repeating: 0, count: frames)
                    var uRev = [Double](repeating: 0, count: frames)
                    for i in 0..<frames {
                        rhoP[i] = pArr[i] / (Turbo.rAir * tp)
                        uRev[i] = rev[i] / (rhoP[i] * aRev)
                    }
                    let dH = (4.0 * aRev / Double.pi).squareRoot()
                    let wA = 343.0 / max(mouthRadius, 0.01)
                    let paUnit = wR * wA / (4.0 * Double.pi)
                    let k = Self.cF * Self.st * aRev / (4.0 * 343.0 * dH) / paUnit
                    var num = 0.0, den = 0.0
                    for i in 0..<frames { num += uRev[i] * rev[i]; den += rev[i] }
                    let uM = num / max(den, 1e-12)
                    let fPk = min(max(Self.st * uM / dH, 200.0), nyq)
                    let nh = band(nz, fPk, Self.hissQ, "hiss\(j)")
                    for i in 0..<frames {
                        let amp = k * rhoP[i] * pow(uRev[i], 3.0)
                        surge[i] += tv * Self.surgeK * amp * nh[i]
                    }
                    // (3) the stalled wheel: a dipole on the tip speed
                    var uT = [Double](repeating: 0, count: frames)
                    var uTsum = 0.0
                    for i in 0..<frames { uT[i] = omA[i] * (0.5 * c.d1s); uTsum += uT[i] }
                    let span = 0.5 * (c.d1s - c.d1h)
                    let kw = Self.swCF * Self.swSt * span
                        * (Double(c.zMain) * Self.swLc).squareRoot() / (4.0 * 343.0) / paUnit
                    var ampW = [Double](repeating: 0, count: frames)
                    let wz = max(ln.wZ, 1e-4)
                    for i in 0..<frames {
                        let depth = min(rev[i] / wz, 1.0)
                        ampW[i] = kw * rhoP[i] * pow(uT[i], 3.0) * depth
                    }
                    let fSw = min(max(Self.swSt * (uTsum / fr) / (Self.swChord * c.d1s), 200.0), nyq)
                    var nw = band(nz, fSw, Self.swQ, "sw\(j)")
                    // ...leaving through the duct: below the (1,0) cut-on only
                    // the plane wave, 1/z of the blades' power
                    let f10 = 1.8412 * a01 / (Double.pi * 1.2 * c.d1s)
                    let kPw = 1.0 - 1.0 / Double(c.zMain).squareRoot()
                    let lo = filt("swlp\(j)", cache.butter(1, min(f10, nyq)), nw)
                    for i in 0..<frames { nw[i] = nw[i] - kPw * lo[i] }
                    for i in 0..<frames { surge[i] += tv * Self.surgeK * ampW[i] * nw[i] }
                    // (3b) the inlet duct's own (1,0) ring, if there is a duct
                    let dDuct = 1.2 * c.d1s
                    var gDuct = 1.0
                    if podFilter {
                        let lPipe = eng.podPipeM > 0 ? eng.podPipeM : Self.podDuctM
                        gDuct = min(max((lPipe - dDuct) / dDuct, 0.0), 1.0)
                    }
                    if f10 < nyq && gDuct > 0.0 {
                        let r = f10 / fSw - fSw / f10
                        let h2 = 1.0 / (1.0 + pow(Self.swQ, 2.0) * pow(r, 2.0))
                        let re = 1.0 - 0.5 * kPw, im = 0.5 * kPw
                        let s2 = pow(hypot(re, im), 2.0)
                        let gR = (pow(10.0, Self.ductDB / 10.0) * h2 * s2
                                  * (f10 / Self.ductQ) / (fSw / Self.swQ)).squareRoot()
                        let nrb = band(nz, f10, Self.ductQ, "duct\(j)")
                        for i in 0..<frames {
                            surge[i] += tv * Self.surgeK * gDuct * gR * ampW[i] * nrb[i]
                        }
                    }
                }
                // (4) rotating stall: forward stall only (0 < phi < phi_z)
                var inSt = [Double](repeating: 0, count: frames)
                for i in 0..<frames { inSt[i] = (phi[i] > 0.0 && phi[i] < phiZ) ? 1.0 : 0.0 }
                var trans = filt("tr\(j)", cache.butter(1, 20.0), inSt)
                var tMax = 0.0
                for i in 0..<frames { trans[i] = min(max(trans[i], 0.0), 1.0); tMax = max(tMax, trans[i]) }
                let fRs = Self.rsF * fS
                if tMax > 1e-3 && 100.0 < fRs && fRs < nyq {
                    let ns = band(nz, fRs, Self.rsQ, "st\(j)")
                    for i in 0..<frames {
                        surge[i] += tv * Self.surgeK * Self.rs
                            * pow(u2[i] / u2r[j], 3.0) * trans[i] * ns[i]
                    }
                }
                if busy {
                    var s = 0.0
                    for i in 0..<frames { s += min(max((phiZ - phi[i]) / phiZ, 0.0), 1.5) }
                    envT = max(envT, s / fr)
                }
            }
        }
        var surgeMax = 0.0
        for x in surge { surgeMax = max(surgeMax, abs(x)) }
        if busy || surgeMax > 0.0 {
            tail = 8
            let y = intakeRadiate(surge, rpm: rpm, mapPa: I.mapPa)
            for i in 0..<frames { out[i] += y[i] }
        } else if tail > 0 {
            // the pipe and the box ring out before the chain rests
            tail -= 1
            let y = intakeRadiate(surge, rpm: rpm, mapPa: I.mapPa)
            for i in 0..<frames { out[i] += y[i] }
        }
        // BLOW-OFF: the jet through the valve throat at the plenum's pressure
        var wbm = 0.0
        if !wb.isEmpty { for x in wb { wbm += x }; wbm /= Double(wb.count) }
        if wbm > 1e-4 {
            var u = [Double](repeating: 0, count: frames)
            var uSum = 0.0
            for i in 0..<frames {
                let pr = max(pArr[i] / ts.p01, 1.0)
                u[i] = min(max(5.0 * (1.0 - pow(pr, -2.0 / 7.0)), 0.0).squareRoot(), 1.0)
                uSum += u[i]
            }
            let uMean = uSum / fr
            let wRef = max(ts.wAirRated, 0.02) * 0.5
            let openf = pow(min(wbm / wRef, 1.0), 0.5)
            let dV = (ts.air.bovCda / 0.6 / (Double.pi / 4.0)).squareRoot()
            let fPk = min(max(0.2 * uMean * 343.0 / dV, 120.0), nyq)
            if noise == nil { noise = rng.standardNormal(frames) }
            let fq = pow(2.0, (12.0 * log2(fPk)).rounded(.toNearestOrEven) / 12.0)
            var jet = filt("bov", rbjBandpass(f0: fq, q: 1.1, rate: sr), noise!)
            if ts.air.bovMode == "atmo" {
                for i in 0..<frames { out[i] += 1.25 * (tv * Self.bov * pow(u[i], 4.0) * openf) * jet[i] }
            } else {                       // back into the intake: the pipe run
                jet = filt("bovr", cache.butter(1, 1500.0), jet)
                for i in 0..<frames { out[i] += 0.8 * (tv * Self.bov * pow(u[i], 4.0) * openf) * jet[i] }
            }
            envT = max(envT, openf * uMean)
            if oChord {                    // easter egg: the blow-off as B-dim
                let inc = 2.0 * Double.pi / sr
                let bp0 = bdimPhase
                for i in 0..<frames {
                    var ch = 0.0
                    for fz in bdimHz { ch += sin(bp0 * (fz / bdimHz[0]) + inc * fz * Double(i)) }
                    out[i] += (tv * 0.7) * openf * ch
                }
                bdimPhase = bp0 + inc * bdimHz[0] * fr
            }
        }
        // twincharge: the series blower sings low, handing over to the turbo
        if eng.inductionSubtype == "twincharge" {
            let ratio = eng.blowerRatio > 0 ? eng.blowerRatio : 9.0
            let fb = (rpm / 60.0) * ratio
            let low = max(0.0, 1.0 - min(rpm / max(eng.redlineRpm, 1.0), 1.0) / 0.7)
            if 20.0 < fb && fb < sr * 0.45 && low > 0.01 {
                let wh = whine(fb, frames, [(1, 1.0), (2, 0.5), (3, 0.28)])
                let k = I.superVol * (0.3 + 0.5 * low) * 0.5
                for i in 0..<frames { out[i] += k * wh[i] }
            }
        }
        // the lift envelope follows the valve's and the surge's real activity
        env += (min(envT * 1.6, 1.0) - env) * min(fr / (sr * 0.05), 1.0)
        bovEnv = env
        // the layer keeps the gain the engine fired at, letting go over ~3 s
        let g = I.agcEnabled ? I.gain : 1.0
        var gRef = tlGref ?? g
        if I.combLoad > 0.5 || gRef > g {
            gRef = g
        } else if !surging {
            gRef += (g - gRef) * min(fr / (sr * 3.0), 1.0)
        }
        tlGref = gRef
        if g > 1e-9 && gRef < g {
            let k = gRef / g
            for i in 0..<frames { out[i] *= k }
        }
        return out
    }
}
