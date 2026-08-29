//
//  MufflerStage.swift
//  Everything between the exhaust port and the tailpipe: the hardware.
//
//  Wave steepening, the head/port cavity, the turbine and its wastegate, the
//  catalyst, the particulate filter, the standing-wave whine, the Helmholtz
//  de-drone notch, the muffler with all of its colour, and the active valve
//  that lets a car be quiet at 2000 rpm and obnoxious at 6000.
//
//  This is the longest single run in the chain and it is where two cars with
//  the same engine stop sounding alike -- a cat, a GPF and a packed box turn
//  an open header into a modern road car.
//

import Foundation

/// Feed-forward ring-buffer delay whose read offset may change every block,
/// so it tracks the live speed of sound.  `_BlockDelay` in audio.py.
struct BlockDelay {
    private var buf: [Double]
    private var wp = 0

    init(maxDelay: Int) { buf = [Double](repeating: 0, count: max(maxDelay, 1) + 4) }

    mutating func process(_ x: [Double], delay: Double) -> [Double] {
        let n = x.count, N = buf.count
        let d = Int(min(max(delay, 1.0), Double(N - 2)))
        for i in 0..<n { buf[(wp + i) % N] = x[i] }
        var out = [Double](repeating: 0, count: n)
        for i in 0..<n { out[i] = buf[((wp + i - d) % N + N) % N] }
        wp = (wp + n) % N
        return out
    }
}

/// What the muffler run needs to know about the engine's state this block.
public struct PipeState {
    public var rpm = 0.0
    public var throttle = 0.0
    public var boost = 0.0            // bar, gauge
    public var choke = 0.0
    public var cold = 0.0
    public var soundSpeed = 540.0     // c in the runner, live
    public var lastLevel = 0.0        // previous block's RMS, for the blow-out
    public init() {}
}

public final class MufflerStage {
    let eng: EnginePreset
    let sampleRate: Double
    let cache: FilterCache
    let rng: PortableRNG

    // fixed designs, built once
    var roadLP = Biquad.identity
    var roadShelf = Biquad.identity
    var hasRoadFilters = false
    var dcBlock: Biquad

    // state that persists across blocks
    // NOT optionals rebuilt on change: each of these keeps its filter state
    // for the life of the engine and is only ever RETUNED, because that is
    // what scipy's persistent zi does on the Python side.  Rebuilding would
    // zero the history and click on every redesign -- and the redesigns here
    // happen continuously, since the revs move the centre frequencies.
    var turbine = Biquad.identity
    var turbineKey = ""
    var gpfLP = OnePole.identity
    var gpfKey = ""
    var wgateFormant = Biquad.identity
    var wgateKey = ""
    var wgGate: ExhaustWaveguide?
    var whine: [Biquad]
    var whineKeys: [String]
    var helm = Biquad.identity
    var helmKey = ""
    var postLP = Biquad.identity
    var postKey = ""
    var lowBoost = Biquad.identity
    var lowBoostKey = ""
    var sysRes = Biquad.identity
    var sysResKey = ""
    var chamber = [Biquad.identity, Biquad.identity]
    var chamberKeys = ["", ""]
    var absorb1 = OnePole.identity
    var absorb2 = OnePole.identity
    var absorbBuilt = false
    var air = OnePole.identity
    var airKey = ""
    var flex = Biquad.identity
    var flexBuilt = false
    var muffDL1: BlockDelay
    var muffDL2: BlockDelay
    var xc: [ExhaustWaveguide]

    // per-engine constants
    let whineAmt: Double
    let whineLD: Double
    let whineOrders: [Int]
    let roadPipe: Bool
    let gpf: Bool
    let muffLen = (0.17, 0.31)

    public init(engine: EnginePreset, sampleRate sr: Double, cache: FilterCache,
                rng: PortableRNG, block: Int = 256) {
        self.eng = engine
        self.sampleRate = sr
        self.cache = cache
        self.rng = rng
        roadPipe = engine.hasCat
        gpf = engine.hasGpf

        let hp = FilterDesign.butter(order: 2, wn: 55.0 / (sr / 2), btype: "high")
        dcBlock = Biquad(b: hp.b, a: hp.a)

        if roadPipe {
            // cat absorption ~ cell density: A(f) grows with f^2, and a denser
            // honeycomb pulls the cutoff down
            let cells = max(engine.catCellsCpsi, 50.0)
            let catFc = min(max(5200.0 * (400.0 / cells).squareRoot(), 2800.0), 9000.0)
            let rl = FilterDesign.butter(order: 2, wn: min(catFc, sr * 0.45) / (sr / 2),
                                         btype: "low")
            roadLP.setCoefficients(b: rl.b, a: rl.a)
            let rs = FilterDesign.peaking(f0: 3400.0, q: 0.7, gainDB: -5.5, rate: sr)
            roadShelf.setCoefficients(b: rs.b, a: rs.a)
            hasRoadFilters = true
        }

        // standing-wave whine: odd quarter-wave harmonics landing in 3-7 kHz
        let lTot = max(engine.exhaustTotalM, 0.5)
        let r = engine.exhaustRadiusM
        whineLD = lTot / (2.0 * r)
        let rev = min(max((engine.redlineRpm - 5000.0) / 5000.0, 0.0), 1.25)
        let bore = min(max((0.028 - r) / 0.011, 0.0), 1.0)
        var amt = min(rev * (0.55 + 0.30 * bore + 0.25 * engine.exhaustOpenness), 0.72)
        if engine.hotV { amt *= 0.42 }      // turbos in the valley swallow the whine
        whineAmt = amt
        let fqw0 = 540.0 / (4.0 * lTot)
        whineOrders = [3500.0, 5000.0, 6500.0].map { target -> Int in
            var n = max(1, Int((target / fqw0).rounded()))
            if n % 2 == 0 { n += 1 }        // odd harmonics only
            return n
        }
        whine = whineOrders.map { _ in Biquad.identity }
        whineKeys = whineOrders.map { _ in "" }

        let md = Int(0.5 / 380.0 * sr) + block + 8
        muffDL1 = BlockDelay(maxDelay: md)
        muffDL2 = BlockDelay(maxDelay: md)
        xc = (0..<3).map { _ in ExhaustWaveguide(maxDelay: 2600) }
    }

    /// The whole run, port to tailpipe.  Returns the signal and the named taps.
    public func process(_ input: [Double], r: Resonance, state s: PipeState,
                        params P: [String: Double],
                        vx: [String: Bool] = [:]) -> (out: [Double],
                                                      taps: [String: [Double]]) {
        var sig = input
        let n = sig.count, sr = sampleRate
        let c = s.soundSpeed
        var taps = [String: [Double]]()
        let p = { (k: String, d: Double) -> Double in P[k] ?? d }
        let on = { (k: String) -> Bool in vx[k] ?? true }

        // Wave steepening and the head duct happen in HeaderStage, which is
        // where this signal comes from -- they are not repeated here.

        // --- turbine: the hot-side wheel smears the pulses ------------------
        // this is why a turbo car is not "NA plus a boost number"
        if eng.induction == "turbo" {
            let bf = eng.boostBar != 0.0
                ? min(s.boost / max(eng.boostBar, 0.05), 1.0) : 0.0
            let rpmFrac = min(s.rpm / max(eng.redlineRpm, 1.0), 1.0)
            // loading (boost) muffles, speed (rpm) keeps the impeller bright, so
            // high-rpm/low-boost and low-rpm/high-boost no longer sound alike
            var tcut = 9000.0 - 4800.0 * bf + 2200.0 * rpmFrac
            tcut = min(max(tcut, 3400.0), 11500.0)
            let ba = cache.butter(2, tcut)
            let key = "\(ba.b)\(ba.a)"
            if turbineKey != key {
                turbine.setCoefficients(b: ba.b, a: ba.a); turbineKey = key
            }
            let preTurbine = sig            // tapped BEFORE the wheel
            turbine.process(&sig)
            // wastegate bypass: gas that skips the turbine keeps its raw edge,
            // so peak boost gets angrier instead of only woollier
            let wg = min(max((bf - 0.78) / 0.22, 0.0), 1.0)
            let m = 0.28 * wg
            if m > 1e-3 {
                for i in 0..<n { sig[i] = (1.0 - m) * sig[i] + m * preTurbine[i] }
            }
            // external (screamer-pipe) gate: the atmospheric vent SCREECHES
            if eng.wastegate == "external" && wg > 0.02 {
                var wn = rng.standardNormal(n)
                let ba2 = cache.peaking(3200.0, 1.4, 9.0)
                let key2 = "\(ba2.b)\(ba2.a)"
                if wgateKey != key2 {
                    wgateFormant.setCoefficients(b: ba2.b, a: ba2.a)
                    wgateKey = key2
                }
                wgateFormant.process(&wn)
                // the screamer pipe is a real ~0.4 m duct, so the BREE rings it
                if wgGate == nil { wgGate = ExhaustWaveguide(maxDelay: 240) }
                let dG = max(Int((2.0 * 0.4 * sr / c).rounded()), 6)
                let rung = wgGate!.process(wn, D: dG, g: 0.82, s: -1.0, lpA: r.rvLP)
                for i in 0..<n { wn[i] += 0.55 * (rung[i] - wn[i]) }
                for i in 0..<n { sig[i] += (0.16 * wg) * tanh(wn[i] * 3.0) }
            }
        }
        taps["head/port"] = sig

        // --- catalyst: the ceramic soaks the raw top end FIRST --------------
        // a stock car with a cat cannot sound like an open header, whatever the
        // muffler does
        if roadPipe && hasRoadFilters { roadLP.process(&sig); roadShelf.process(&sig) }
        // a wall-flow GPF packs the stream far tighter: broadband soak
        if gpf {
            let ba = cache.butter(1, min(4500.0, sr * 0.45))
            let key = "\(ba.b)\(ba.a)"
            if gpfKey != key { gpfLP.setCoefficients(b: ba.b, a: ba.a); gpfKey = key }
            gpfLP.process(&sig)
        }
        // the brick sits in its own expansion casing, so it RINGS as well
        let cat = xc[0].process(sig, D: r.rvD[0], g: r.rvG[0], s: 1.0, lpA: r.rvLP)
        for i in 0..<n { sig[i] += 0.30 * (cat[i] - sig[i]) }
        taps["catalytic"] = sig

        // --- high-order standing-wave whine ---------------------------------
        // the pipe's odd quarter-wave harmonics in 3-7 kHz, sharp on a long thin
        // bore (soprano scream), broad on a fat one (roar)
        if whineAmt > 0.02 {
            let fQw = c / (4.0 * max(eng.exhaustTotalM, 0.5))
            var wamt = whineAmt * (0.25 + 0.75 * r.valve)
            // Q from the unified system damping AND the header signature:
            // equal-length headers stack razor-sharp peaks, unequal smear them
            var qBase = min((2.0 + 0.16 * whineLD) * (0.55 + 0.9 * r.sysQ), 11.0)
            if eng.headerUnequalDeg > 0.0 { qBase *= 0.65; wamt *= 0.85 }
            else { qBase *= 1.15 }
            for (k, order) in whineOrders.enumerated() {
                let fc = fQw * Double(order)
                if fc > 2400.0 && fc < min(8000.0, sr * 0.45) {
                    let q = min(qBase * (1.0 + 0.10 * Double(order)).squareRoot(), 14.0)
                    let gain = (4.2 - 1.1 * Double(k)) * wamt * p("whine", 1.0)
                    let ba = cache.peaking(fc, q, gain)
                    let key = "\(ba.b)\(ba.a)"
                    if whineKeys[k] != key {
                        whine[k].setCoefficients(b: ba.b, a: ba.a)
                        whineKeys[k] = key
                    }
                    whine[k].process(&sig)
                }
            }
        }
        taps["standing-wave"] = sig

        // --- resonator: DC block, then the Helmholtz de-drone NOTCH ---------
        dcBlock.process(&sig)
        let bypass = sig                 // the bright straight-through path
        // a quiet closed road system runs a big de-drone resonator; an open race
        // system barely any.  Narrow, like the real side branch -- a wide notch
        // here guts the engine's whole bass body.
        let op = min(max(eng.exhaustOpenness, 0.2), 1.0)
        let resDepth = -(2.0 + 7.0 * (1.0 - op))
        let bh = cache.peaking(r.fHelm, 6.0, resDepth)
        let hkey = "\(bh.b)\(bh.a)"
        if helmKey != hkey { helm.setCoefficients(b: bh.b, a: bh.a); helmKey = hkey }
        helm.process(&sig)
        taps["resonator"] = sig

        // --- muffler --------------------------------------------------------
        // variable-valve expansion low-pass: muffled at idle, open at redline
        let cutoff = min(r.postFc, sr * 0.45)
        let bl = cache.butter(2, cutoff)
        let lkey = "\(bl.b)\(bl.a)"
        if postKey != lkey { postLP.setCoefficients(b: bl.b, a: bl.a); postKey = lkey }
        postLP.process(&sig)
        // ...and its expansion-chamber low-end body while the valve is shut
        if r.valve < 0.75 {
            let ba = cache.peaking(110.0, 0.6, (1.0 - r.valve) * 7.0)
            let key = "\(ba.b)\(ba.a)"
            if lowBoostKey != key {
                lowBoost.setCoefficients(b: ba.b, a: ba.a); lowBoostKey = key
            }
            lowBoost.process(&sig)
        }
        // SYSTEM HELMHOLTZ: the box breathing through the tailpipe is a
        // resonator IN the path, and its GAIN is the deep 80-200 Hz thud every
        // real system carries.  Wide and modest -- neck losses damp it hard.
        if on("sys_helm") {
            let vMf = max(eng.mufflerVolumeM3, 1e-5)
            let rTip = max(eng.exhaustRadiusM, 0.012) * max(eng.tipScale, 0.5)
            let aTp = Double.pi * rTip * rTip
            let lNk = 0.45 + 0.61 * (aTp / Double.pi).squareRoot()
            var fSys = (c / (2.0 * Double.pi)) * (aTp / (vMf * lNk)).squareRoot()
            fSys = min(max(fSys, 45.0), 240.0)
            var gSys = min(2.8 + 0.9 * log10(1.0 + vMf / 0.002), 3.0)
            if eng.mufflerType == "absorptive" { gSys *= 0.6 }
            // driven hardest when the firing order sweeps through it: the real
            // cruise-drone physics, scaled by the flow actually pumping the box
            let fireNow = s.rpm * Double(eng.numCylinders) / 120.0
            let z = (fireNow - fSys) / (0.6 * max(fSys, 1.0))
            let ovl = exp(-z * z)
            gSys *= (0.45 + 0.55 * r.flow) * (0.7 + 0.9 * ovl)
            let ba = cache.peaking(fSys, 1.0, gSys)
            let key = "\(ba.b)\(ba.a)"
            if sysResKey != key {
                sysRes.setCoefficients(b: ba.b, a: ba.a); sysResKey = key
            }
            sysRes.process(&sig)
        }
        // internal reflections: two feed-forward comb taps (chamber + baffle),
        // periodic notches = the box's own colour, not just attenuation.  A
        // reflective box combs and drones; an absorptive one barely combs.
        let absorptive = eng.mufflerType == "absorptive"
        let mcomb = (1.0 - eng.exhaustOpenness) * (absorptive ? 0.3 : 1.0)
        if mcomb > 0.05 {
            let d1 = muffLen.0 / c * sr, d2 = muffLen.1 / c * sr
            let mg = mcomb * p("muffler", 1.0)
            let t1 = muffDL1.process(sig, delay: d1)
            let t2 = muffDL2.process(sig, delay: d2)
            for i in 0..<n { sig[i] += 0.32 * mg * t1[i] + 0.22 * mg * t2[i] }
        }
        // dual-chamber standing waves: the box is baffled front/rear and each
        // cavity rings its own quarter-wave
        let lBox = max(eng.mufflerNeckLenM * 4.0, 0.15)
        for (kk, ch) in [(0.42, 2.6), (0.58, 2.0)].enumerated() {
            let fCh = c / (4.0 * max(lBox * ch.0, 0.05))
            if fCh > 60.0 && fCh < 4500.0 {
                let ba = cache.peaking(fCh, 0.8 + 2.4 * r.sysQ,
                                       ch.1 * (0.5 + 0.8 * r.sysQ))
                let key = "\(ba.b)\(ba.a)"
                if chamberKeys[kk] != key {
                    chamber[kk].setCoefficients(b: ba.b, a: ba.a)
                    chamberKeys[kk] = key
                }
                chamber[kk].process(&sig)
            }
        }
        if absorptive {
            // absorption grows with frequency, so two gentle poles, not one
            // hard cutoff -- a natural tail
            if !absorbBuilt {
                let a1 = cache.butter(1, min(8200.0, sr * 0.45))
                absorb1.setCoefficients(b: a1.b, a: a1.a)
                let a2 = cache.butter(1, min(12500.0, sr * 0.45))
                absorb2.setCoefficients(b: a2.b, a: a2.a)
                absorbBuilt = true
            }
            absorb1.process(&sig); absorb2.process(&sig)
        }
        // air absorption over the run: molecular loss rises with frequency and
        // LENGTH, so a long truck system arrives duller than a stubby side exit
        let lRun = max(eng.exhaustTotalM, 0.3)
        let ba = cache.butter(1, min(16000.0 / (1.0 + 0.13 * lRun), sr * 0.45))
        let akey = "\(ba.b)\(ba.a)"
        if airKey != akey { air.setCoefficients(b: ba.b, a: ba.a); airKey = akey }
        air.process(&sig)
        if eng.flexPipe {
            if !flexBuilt {
                let f = cache.peaking(1650.0, 2.2, 4.0)
                flex.setCoefficients(b: f.b, a: f.a)
                flexBuilt = true
            }
            flex.process(&sig)          // corrugated section: the 'braaa' rasp
        }
        taps["muffler"] = sig

        // --- active exhaust valve -------------------------------------------
        // above ~40% redline the flap cracks and the bright bypass crossfades in
        var vo = min(max((r.valve - 0.40) / 0.5, 0.0), 1.0) * 0.5 * p("valve_open", 1.0)
        vo = min(vo, 0.85)
        if vo > 1e-3 {
            for i in 0..<n { sig[i] = (1.0 - vo) * sig[i] + vo * bypass[i] }
        }
        // SPL blow-out: when it is loud the box cannot hold the pressure and a
        // little more of the bright pre-muffler path bleeds through
        let spl = min(s.lastLevel * 3.0, 1.0)
        let blm = 0.16 * spl * (1.0 - vo)
        if blm > 1e-3 {
            for i in 0..<n { sig[i] = (1.0 - blm) * sig[i] + blm * bypass[i] }
        }
        // the box's front/rear cavities ring their stored energy, inline
        let c1 = xc[1].process(sig, D: r.rvD[1], g: r.rvG[1], s: 1.0, lpA: r.rvLP)
        let c2 = xc[2].process(sig, D: r.rvD[2], g: r.rvG[2], s: 1.0, lpA: r.rvLP)
        for i in 0..<n {
            sig[i] += 0.24 * (c1[i] - sig[i]) + 0.18 * (c2[i] - sig[i])
        }
        taps["valve bypass"] = sig
        return (sig, taps)
    }
}
