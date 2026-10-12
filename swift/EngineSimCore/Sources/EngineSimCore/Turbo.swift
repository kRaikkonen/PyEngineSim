//
//  Turbo.swift
//  The turbocharger as a machine, ported from engine_sim/turbo.py
//  (docs/ENGINESIM_PLAN.md s.10).
//
//  One charge-air system: one or more turbochargers (single / parallel twin /
//  quad / sequential twin) feeding ONE plenum -- the charge pipes and the
//  intercooler up to the throttle -- each a compressor and a turbine on one
//  shaft, the boost held by a wastegate, and a blow-off valve on the plenum
//  (recirculating, atmospheric, or none: then the compressor SURGES -- the
//  'stu-tu-tu').
//
//    compressor  non-dimensional speed lines: Euler work with slip (Wiesner)
//                and backsweep, Leufven & Eriksson's ellipse to choke, their
//                unstable branch to the zero-flow ratio, a reverse branch.
//    duct/plenum Greitzer's lumped model: the duct flow has inertia, the
//                plenum is a compliance -- deep surge falls out of it.
//    turbine     a nozzle, closed-form inlet pressure with the wastegate in
//                parallel; efficiency by the blade-speed ratio.
//    shaft       J dw/dt = (P_t - P_c - P_fr) / w.
//
//  The SIZING (turbo.build: the compressor match and the turbine bisection
//  over the steady shaft balance, closed-loop) runs once in the Python and
//  comes in as a TurboSpec (docs/engine_tables.json, "turbo").  Everything
//  that moves runs here, live.  No randomness anywhere in it.
//

import Foundation

public enum Turbo {
    public static let gamma = 1.4
    public static let cp = 1005.0
    public static let rAir = 287.0
    public static let kAir = (gamma - 1.0) / gamma
    public static let gammaE = 1.33
    public static let cpE = 1150.0
    public static let kEx = (gammaE - 1.0) / gammaE
    public static let pAtm = 101325.0
    public static let tAmb = 298.15

    // the compressor stage
    public static let zMain = 6, zSplit = 6
    static let backsweep = 40.0 * (Double.pi / 180.0)   // math.radians: x * (pi/180)
    static let b2Frac = 0.065
    static let trimD1 = 0.78
    static let hubD1 = 0.28
    static let rho2Ratio = 1.30
    static let phiZSL = 0.045
    static let phiOpt = 0.085
    static let phiMax = 0.12
    static let mChoke = 0.45
    static let etaPeak = 0.78
    static let etaSurgeRel = 0.90
    static let gammaSurge = 0.50
    static let cRev = 0.07
    static let cOpen = 0.9
    static let cDF = 4.0e-4
    static let cRC = 0.026

    // the turbine stage
    static let dtOverD2 = 0.88
    static let etaTMax = 0.70
    static let bsrOpt = 0.70
    static let seqBleed = 0.30
    static let seqRelief = 0.3
    static let seqEcvT = 0.30
    static let twinScrollGain = 0.15

    // the rotor
    static let rhoAl = 2700.0, rhoNi = 8000.0
    static let kJC = 0.0041
    static let kJT = 0.0069
    static let cFric = 1.2e-6

    // the charge-air system
    static let lcPerD2 = 5.0
    static let dpBovOpen = 0.25e5
    static let dpBovSpan = 0.30e5
    static let tauBovOpen = 0.010, tauBovClose = 0.040
    static let wgKP = 2.5, wgKI = 4.0
    static let wgKD = 0.25
    static let tauWG = 0.06
    static let wgOverspeed = 20.0
    public static let dtFast = 0.0005
    static let afrWOT = 12.5

    static func smoothMin(_ a: Double, _ b: Double, _ p: Double = 4.0) -> Double {
        pow(pow(a, -p) + pow(b, -p), -1.0 / p)
    }

    /// Compressible orifice mass flow (kg/s), p_up -> p_dn (sign follows).
    static func orifice(_ cda: Double, _ pUp: Double, _ tUp: Double,
                        _ pDn: Double) -> Double {
        if pUp < pDn { return -orifice(cda, pDn, tUp, pUp) }
        let r = pDn / max(pUp, 1.0)
        let psi: Double
        if r < 0.5283 {
            psi = 0.6847
        } else {
            psi = max(2.0 * gamma / (gamma - 1.0)
                      * (pow(r, 2.0 / gamma) - pow(r, (gamma + 1.0) / gamma)),
                      0.0).squareRoot()
        }
        return cda * pUp / (rAir * tUp).squareRoot() * psi
    }

    /// Python's int(): truncation toward zero, made total (a NaN or a huge
    /// value would trap in Swift where it would raise in Python).
    @inline(__always) static func truncInt(_ x: Double) -> Int {
        guard x.isFinite else { return 0 }
        return Int(min(max(x, -1.0e9), 1.0e9))
    }
}

/// A speed line's anchors (turbo.Compressor.line's dict).
public struct CompressorLine: Equatable {
    public var rho, u2, scale, wZ, wCh, phiCh, psiZ, c1, hs, piZ, pi0, p01, t01: Double
}

/// A centrifugal compressor stage of exducer diameter d2 (m).
public struct Compressor {
    public let d2, d1s, d1h, aEye, aAnn: Double
    public let zMain, zSplit: Int
    public let sigma, kappa: Double

    public init(d2: Double, zMain: Int = Turbo.zMain, zSplit: Int = Turbo.zSplit) {
        self.d2 = d2
        d1s = Turbo.trimD1 * d2
        d1h = Turbo.hubD1 * d1s
        aEye = Double.pi / 4.0 * (d1s * d1s)
        aAnn = Double.pi / 4.0 * (d1s * d1s - d1h * d1h)
        self.zMain = zMain
        self.zSplit = zSplit
        let z = Double(zMain + zSplit)
        sigma = 1.0 - cos(Turbo.backsweep).squareRoot() / pow(z, 0.7)   // Wiesner
        kappa = tan(Turbo.backsweep) / (Double.pi * Turbo.b2Frac) / Turbo.rho2Ratio
    }

    /// Euler work coefficient: slip and backsweep.
    public func psiE(_ phi: Double) -> Double { sigma - kappa * phi }

    public func line(u2 u2In: Double, p01: Double, t01: Double) -> CompressorLine {
        let rho = p01 / (Turbo.rAir * t01)
        let a01 = (Turbo.gamma * Turbo.rAir * t01).squareRoot()
        let u2 = max(u2In, 1.0)
        let scale = rho * u2 * (d2 * d2)
        let wCh = Turbo.smoothMin(Turbo.phiMax * scale, Turbo.mChoke * rho * a01 * aAnn)
        let phiCh = max(wCh / scale, Turbo.phiZSL * 1.3)
        let mU = u2 / a01
        let etaPk = max(Turbo.etaPeak - 0.10 * pow(max(mU - 1.0, 0.0), 2.0), 0.45)
        let psiZ = Turbo.etaSurgeRel * etaPk * psiE(Turbo.phiZSL)
        let phiO = min(Turbo.phiOpt, Turbo.phiZSL + 0.8 * (phiCh - Turbo.phiZSL))
        let psiO = etaPk * psiE(phiO)
        let xO = (phiO - Turbo.phiZSL) / (phiCh - Turbo.phiZSL)
        let ratio = min(max(psiO / psiZ, 0.05), 0.98)
        let c1 = min(max(log(1.0 - ratio) / log(max(xO, 1e-3)), 1.5), 10.0)
        let hs = u2 * u2 / (Turbo.cp * t01)
        let piZ = pow(1.0 + psiZ * hs, 1.0 / Turbo.kAir)
        let pi0 = piZ - Turbo.gammaSurge * (piZ - 1.0)
        return CompressorLine(rho: rho, u2: u2, scale: scale, wZ: Turbo.phiZSL * scale,
                              wCh: phiCh * scale, phiCh: phiCh, psiZ: psiZ, c1: c1,
                              hs: hs, piZ: piZ, pi0: pi0, p01: p01, t01: t01)
    }

    /// Pi_hat(W) on the line (any sign of W).
    public func pressureRatio(_ w: Double, _ ln: CompressorLine) -> Double {
        let wZ = ln.wZ, wCh = ln.wCh
        if w >= wZ {
            if w <= wCh {
                let x = (w - wZ) / (wCh - wZ)
                let psi = ln.psiZ * (1.0 - pow(x, ln.c1))
                return pow(1.0 + psi * ln.hs, 1.0 / Turbo.kAir)
            }
            // past choke the wheel is only a restriction
            let dw = w - wCh
            let a = Turbo.cOpen * aAnn
            return 1.0 - dw * dw / (2.0 * ln.rho * (a * a) * ln.p01)
        }
        if w >= 0.0 {
            let s = w / wZ
            return ln.pi0 + (ln.piZ - ln.pi0) * s * s * (3.0 - 2.0 * s)
        }
        let a = Turbo.cRev * aEye
        return ln.pi0 + w * w / (2.0 * ln.rho * (a * a) * ln.p01)
    }

    /// Shaft power absorbed (W): Euler work on the through-flow (half of it on
    /// a reversed flow the wheel churns), disk friction, and the
    /// recirculation that takes over below the surge line.
    public func power(_ w: Double, _ ln: CompressorLine) -> Double {
        let u2 = ln.u2
        let u3 = pow(u2, 3.0)                     // Python's u2 ** 3
        let pdf = Turbo.cDF * ln.rho * u3 * (d2 * d2)
        let prc = Turbo.cRC * ln.rho * u3 * (d2 * d2)
        if w > 0.0 {
            let s = max(1.0 - w / max(ln.wZ, 1e-9), 0.0)
            return (w * u2 * u2 * max(psiE(w / ln.scale), 0.05) + pdf
                    + prc * s * s)
        }
        return -w * u2 * u2 * 0.5 * sigma + pdf + prc
    }

    /// p01 x Pi_hat and the power over W at this speed, tabulated (97 nodes,
    /// numpy.linspace's spacing) for the fast charge-air substeps.
    public func table(u2: Double, p01: Double, t01: Double, n: Int = 97) -> CompressorTable {
        let ln = line(u2: u2, p01: p01, t01: t01)
        let wLo = -2.5 * ln.wZ
        let wHi = 1.6 * ln.wCh
        let step = (wHi - wLo) / Double(n - 1)
        var p = [Double](repeating: 0, count: n)
        var pw = [Double](repeating: 0, count: n)
        for i in 0..<n {
            let x = i == n - 1 ? wHi : Double(i) * step + wLo
            p[i] = p01 * pressureRatio(x, ln)
            pw[i] = power(x, ln)
        }
        return CompressorTable(w0: wLo, dw: (wHi - wLo) / Double(n - 1), p: p, pw: pw,
                               ln: ln)
    }
}

public final class CompressorTable {
    public let w0, dw: Double
    public let p, pw: [Double]
    public let ln: CompressorLine
    public let n: Int

    init(w0: Double, dw: Double, p: [Double], pw: [Double], ln: CompressorLine) {
        self.w0 = w0; self.dw = dw; self.p = p; self.pw = pw; self.ln = ln
        n = p.count
    }

    /// (p_hat, dp_hat/dW, power) at W, linear between nodes.
    @inline(__always)
    public func eval(_ w: Double) -> (Double, Double, Double) {
        let f = (w - w0) / dw
        var i = Turbo.truncInt(f)
        if i < 0 { i = 0 } else if i > n - 2 { i = n - 2 }
        let t = f - Double(i)
        let p0 = p[i], p1 = p[i + 1]
        let slope = (p1 - p0) / dw
        let q0 = pw[i], q1 = pw[i + 1]
        return (p0 + slope * (w - w0 - Double(i) * dw), slope,
                q0 + (q1 - q0) * min(max(t, 0.0), 1.0))
    }
}

/// A radial turbine: flow capacity aEff (m^2, nozzle-equivalent), wheel dt.
public struct Turbine {
    public let dT: Double
    public var aEff: Double

    /// p03 so the turbine (and any open wastegate, both in aTot) pass wEx:
    /// p03 = sqrt(p04^2 + (W sqrt(R T03) / A)^2).
    public static func inletPressure(_ wEx: Double, _ t03: Double, _ p04: Double,
                                     _ aTot: Double) -> Double {
        let q = wEx * (Turbo.rAir * t03).squareRoot() / max(aTot, 1e-9)
        return (p04 * p04 + q * q).squareRoot()
    }

    /// Shaft power (W) from wT kg/s expanding p03 -> p04.
    public func power(_ wT: Double, _ t03: Double, _ p03: Double, _ p04: Double,
                      _ omega: Double, _ pulse: Double = 1.0) -> Double {
        let ex = 1.0 - pow(p04 / p03, Turbo.kEx)
        if ex <= 1e-6 || wT <= 0.0 { return 0.0 }
        let cS = (2.0 * Turbo.cpE * t03 * ex).squareRoot()
        let uT = omega * dT * 0.5
        let r = (uT / cS) / Turbo.bsrOpt
        let eta = Turbo.etaTMax * max(2.0 * r - r * r, 0.0) * pulse
        return eta * wT * Turbo.cpE * t03 * ex
    }
}

/// What ChargeAir steps: a compressor's duct (the physics' unit, or the
/// audio twin's copy of one).
public protocol DuctUnit: AnyObject {
    var comp: Compressor { get }
    var lC: Double { get }
    var table: CompressorTable? { get }
    var active: Bool { get }
    var wC: Double { get set }
}

/// One turbocharger: compressor + turbine on a shaft, its own wastegate.
public final class TurboUnit: DuctUnit {
    public let comp: Compressor
    public var turb: Turbine
    public var aWG: Double
    public let j: Double
    public let cFric: Double
    public let lC: Double
    public var omega = 2000.0          // rad/s (~19 krpm, a warm idle)
    public var wC = 0.0                // compressor mass flow (kg/s)
    public var xWG = 0.0               // wastegate opening 0..1
    public var wgInt = 0.0             // controller integral
    public var pT = 0.0                // last turbine power (W)
    public var pC = 0.0                // last mean compressor power (W)
    public var wT = 0.0                // turbine mass flow
    public var wWGF = 0.0              // wastegate mass flow
    public var p03 = Turbo.pAtm        // turbine inlet pressure
    public var table: CompressorTable? = nil
    public var tableOmega = 0.0        // shaft speed the table was drawn at
    public var active = true           // sequential: joined the plenum
    public var ecv = 1.0               // sequential: exhaust control valve

    public init(d2: Double, aT: Double, aWG: Double, ballBearing: Bool) {
        comp = Compressor(d2: d2)
        turb = Turbine(dT: Turbo.dtOverD2 * d2, aEff: aT)
        self.aWG = aWG
        j = Turbo.kJC * Turbo.rhoAl * pow(d2, 5.0)
            + Turbo.kJT * Turbo.rhoNi * pow(Turbo.dtOverD2 * d2, 5.0)
        cFric = Turbo.cFric * pow(d2 / 0.060, 3.0) * (ballBearing ? 0.5 : 1.0)
        lC = Turbo.lcPerD2 * d2
    }

    public var rpm: Double { omega * 60.0 / (2.0 * Double.pi) }
    public var u2: Double { omega * comp.d2 * 0.5 }
}

/// A recorder for ChargeAir.run: (w_c per unit, p_p, w_bov) after every
/// substep, into preallocated storage (it runs on the audio thread).
public final class ChargeAirRecord {
    public private(set) var count = 0
    public private(set) var wC: [[Double]]
    public private(set) var pP: [Double]
    public private(set) var wBov: [Double]

    public init(units: Int, capacity: Int) {
        wC = [[Double]](repeating: [Double](repeating: 0, count: capacity), count: units)
        pP = [Double](repeating: 0, count: capacity)
        wBov = [Double](repeating: 0, count: capacity)
    }

    func reset(units: Int, capacity: Int) {
        if wC.count != units || pP.count < capacity {
            let cap = max(capacity, pP.count)
            wC = [[Double]](repeating: [Double](repeating: 0, count: cap), count: units)
            pP = [Double](repeating: 0, count: cap)
            wBov = [Double](repeating: 0, count: cap)
        }
        count = 0
    }

    func append(_ units: [DuctUnit], _ pp: Double, _ wb: Double) {
        guard count < pP.count else { return }
        for j in 0..<units.count { wC[j][count] = units[j].wC }
        pP[count] = pp
        wBov[count] = wb
        count += 1
    }

    /// The same record repeated n times (the twin's quiet path).
    func fill(_ units: [DuctUnit], _ pp: Double, _ wb: Double, _ n: Int) {
        for _ in 0..<n { append(units, pp, wb) }
    }
}

/// The fast part: each compressor's duct into one plenum, drained by the
/// throttle (a linear load line), a blow-off valve and an anti-lag bypass.
/// The same class runs in the physics (0.5 ms) and, as a twin, at audio
/// rate -- so the flutter one hears is the flutter the boost gauge shows.
public final class ChargeAir {
    public var vP: Double
    public var bovCda: Double
    public var bovMode: String          // "recirc" | "atmo" | "none"
    public var pP = Turbo.pAtm
    public var xBov = 0.0
    public var wBov = 0.0

    public init(vP: Double, bovCda: Double, bovMode: String = "recirc") {
        self.vP = vP
        self.bovCda = bovCda
        self.bovMode = bovMode
    }

    /// Advance by dt (s): semi-implicit, so the stiff branches stay stable.
    public func substep(_ units: [DuctUnit], _ dt: Double, _ kThr: Double,
                        _ mapPa: Double, _ tP: Double, _ p01: Double,
                        _ wBypass: Double = 0.0) {
        let c = Turbo.gamma * Turbo.rAir * tP / vP
        var wIn = 0.0
        for u in units {
            guard u.active, let tab = u.table else { continue }
            let (ph, slope, _) = tab.eval(u.wC)
            let k = u.comp.aEye / u.lC
            let den = 1.0 - dt * k * slope
            if den < 0.25 {                        // the unstable branch: explicit
                u.wC += dt * k * (ph - pP)
            } else {
                u.wC += dt * k * (ph - pP) / den
            }
            wIn += u.wC
        }
        // blow-off valve: the diaphragm sees plenum minus manifold
        if bovMode != "none" {
            let tgt = min(max((pP - mapPa - Turbo.dpBovOpen) / Turbo.dpBovSpan, 0.0), 1.0)
            let tau = tgt > xBov ? Turbo.tauBovOpen : Turbo.tauBovClose
            xBov += (tgt - xBov) * min(dt / tau, 1.0)
            wBov = xBov > 1e-4 ? Turbo.orifice(bovCda * xBov, pP, tP, p01) : 0.0
        } else {
            xBov = 0.0
            wBov = 0.0
        }
        // plenum: implicit in the throttle's (linear) draw
        pP = (pP + dt * c * (wIn - wBov - wBypass)) / (1.0 + dt * c * kThr)
        pP = max(pP, 0.3 * Turbo.pAtm)
    }

    /// dt in substeps of about h; returns each unit's mean compressor power.
    @discardableResult
    public func run(_ units: [DuctUnit], _ dt: Double, _ kThr: Double, _ mapPa: Double,
                    _ tP: Double, _ p01: Double, _ wBypass: Double = 0.0,
                    h: Double = Turbo.dtFast, record: ChargeAirRecord? = nil) -> [Double] {
        let n = max(Turbo.truncInt((dt / h).rounded(.up)), 1)
        let hs = dt / Double(n)
        var acc = [Double](repeating: 0, count: units.count)
        for _ in 0..<n {
            substep(units, hs, kThr, mapPa, tP, p01, wBypass)
            for j in 0..<units.count {
                let u = units[j]
                if u.active, let tab = u.table { acc[j] += tab.eval(u.wC).2 }
            }
            record?.append(units, pP, wBov)
        }
        return acc.map { $0 / Double(n) }
    }
}

/// The engine side the machine is driven by -- the simulator's own breathing
/// (TurboHooks.swift has the live implementation).
public protocol TurboHooks: AnyObject {
    /// (air kg/s, MAP Pa, charge temperature K) drawn from a plenum at pP.
    func engineFlow(rpm: Double, thr: Double, pP: Double) -> (Double, Double, Double)
    /// Turbine-inlet temperature (K) at a manifold fraction `load` and a
    /// compressor pressure ratio `pr`.
    func exhaustTemp(rpm: Double, load: Double, pr: Double) -> Double
    /// The ECU's mixture at manifold fraction `load`.
    func afr(load: Double, rpm: Double) -> Double
}

/// The baked sizing (export_turbo.py).
public struct TurboSpec: Decodable, Equatable {
    public struct Unit: Decodable, Equatable {
        public let d2, a_t, a_wg: Double
        public let ball: Bool
        public let j, c_fric: Double
    }
    public let units: [Unit]
    public let layout: String
    public let v_p, bov_cda: Double
    public let bov_mode: String
    public let boost_bar: Double
    public let twin_scroll: Bool
    public let seq_rpm: [Double]
    public let diesel, external_wg, anti_lag, e_turbo: Bool
    public let sc_pr, sc_rpm: Double
    public let rpm_red, omega_max, dp_down_k, rpm_full, pit_rated, w_air_rated, size: Double
    public let mguh_kw, intercooler_eff, vd, map_wot_area: Double
}

/// All of an engine's turbochargers and their charge-air system.
public final class TurboSystem {
    public let spec: TurboSpec
    public var units: [TurboUnit]
    public let layout: String
    public let air: ChargeAir
    public let boostBar: Double
    public let twinScroll: Bool
    public let seqRpm: (Double, Double)
    public let diesel, externalWG, antiLag, eTurbo: Bool
    public let scPr, scRpm: Double
    public let rpmRed, omegaMax, dpDownK, rpmFull, pitRated, wAirRated, size: Double
    public var kThr = 0.0
    public var mapPa = Turbo.pAtm
    public var tP = Turbo.tAmb
    public var p01 = Turbo.pAtm
    public var t03 = 600.0
    public var p04 = Turbo.pAtm
    public var wAir = 0.0
    public var wEx = 0.0
    public var wBypass = 0.0
    public var stepped = 0
    var pPrev: Double? = nil
    public let hooks: TurboHooks

    public init(spec: TurboSpec, hooks: TurboHooks) {
        self.spec = spec
        self.hooks = hooks
        units = spec.units.map {
            TurboUnit(d2: $0.d2, aT: $0.a_t, aWG: $0.a_wg, ballBearing: $0.ball)
        }
        layout = spec.layout
        air = ChargeAir(vP: spec.v_p, bovCda: spec.bov_cda, bovMode: spec.bov_mode)
        boostBar = spec.boost_bar
        twinScroll = spec.twin_scroll
        seqRpm = (spec.seq_rpm.first ?? 0, spec.seq_rpm.count > 1 ? spec.seq_rpm[1] : 0)
        diesel = spec.diesel
        externalWG = spec.external_wg
        antiLag = spec.anti_lag
        eTurbo = spec.e_turbo
        scPr = spec.sc_pr
        scRpm = spec.sc_rpm
        rpmRed = spec.rpm_red
        omegaMax = spec.omega_max
        dpDownK = spec.dp_down_k
        rpmFull = spec.rpm_full
        pitRated = spec.pit_rated
        wAirRated = spec.w_air_rated
        size = spec.size
    }

    /// Plenum pressure, bar gauge (what a boost gauge reads).
    public var boost: Double { (air.pP - Turbo.pAtm) / 1.0e5 }

    /// Compressor inlet: ambient, or a series blower's outlet (twincharge).
    func inletPressure(_ rpm: Double) -> Double {
        if scPr > 0.0 && scRpm > 0.0 {
            let f = min(max(1.0 - rpm / scRpm, 0.0), 1.0)
            return Turbo.pAtm * (1.0 + scPr * f)
        }
        return Turbo.pAtm
    }

    /// Exhaust flow share and join state of each unit.
    func shares(_ rpm: Double, _ thr: Double, _ dt: Double = 0.0) -> [Double] {
        let n = units.count
        if layout == "sequential" && n == 2 {
            let (r1, r2) = seqRpm
            let u2 = units[1]
            if thr < 0.5 || rpm < r1 {
                u2.ecv = 0.0
            } else if rpm < r2 && u2.ecv < Turbo.seqBleed {
                u2.ecv = Turbo.seqBleed * (rpm - r1) / max(r2 - r1, 1.0)
            } else if rpm >= r2 {
                let u1 = units[0]
                let ready = min(max((u2.omega / max(u1.omega, 1.0) - 0.5) / 0.4, 0.0), 1.0)
                let tgt = Turbo.seqBleed + (1.0 - Turbo.seqBleed) * ready
                let step = dt / Turbo.seqEcvT
                u2.ecv = min(max(u2.ecv + min(max(tgt - u2.ecv, -step), step), 0.0), 1.0)
                if tgt >= 0.999 && u2.ecv > 0.995 { u2.ecv = 1.0 }
            }
            if u2.active {
                u2.active = rpm > r2 * 0.95 && thr >= 0.5
            } else if rpm >= r2 && thr >= 0.5 {
                // its intake valve is a check valve: it opens once the
                // secondary can push against the plenum
                let ln = u2.comp.line(u2: u2.u2, p01: p01, t01: Turbo.tAmb)
                if p01 * u2.comp.pressureRatio(Turbo.seqRelief * ln.wZ, ln) >= air.pP {
                    u2.active = true
                    u2.wC = Turbo.seqRelief * ln.wZ
                }
            }
            units[0].ecv = 1.0
            units[0].active = true
            return [1.0, u2.ecv]
        }
        for u in units { u.ecv = 1.0; u.active = true }
        return [Double](repeating: 1.0, count: n)
    }

    /// Turbine inlet pressures, flows and powers for this frame.
    func exhaust(_ rpm: Double, _ thr: Double, _ dt: Double, _ fuelCut: Bool) {
        let n = units.count
        let sh = shares(rpm, thr, dt)
        let afr = hooks.afr(load: min(mapPa / Turbo.pAtm, 1.0), rpm: rpm)
        var wEx = wAir * (1.0 + (fuelCut ? 0.0 : 1.0 / afr))
        var t03 = self.t03
        if antiLag && thr < 0.2 && !fuelCut {
            wEx = max(wEx, wBypass * (1.0 + 1.0 / Turbo.afrWOT))
            t03 = max(t03, 1250.0)
        }
        self.wEx = wEx
        p04 = Turbo.pAtm + dpDownK * wEx * wEx
        var pulse = 1.0
        if twinScroll {
            pulse = 1.0 + Turbo.twinScrollGain * max(1.0 - rpm / max(rpmRed, 1.0), 0.0)
        }
        if layout == "sequential" && n == 2 {
            var aTot = 0.0
            for (u, s) in zip(units, sh) {
                aTot += s * u.turb.aEff + u.xWG * u.aWG * (s > 0.5 ? 1.0 : 0.0)
            }
            let p03 = Turbine.inletPressure(wEx, t03, p04, aTot)
            for (u, s) in zip(units, sh) {
                let aU = s * u.turb.aEff
                u.wT = wEx * aU / aTot
                u.wWGF = wEx * (u.xWG * u.aWG * (s > 0.5 ? 1.0 : 0.0)) / aTot
                u.p03 = p03
                u.pT = u.turb.power(u.wT, t03, p03, p04, u.omega, pulse)
            }
            return
        }
        let per = wEx / Double(n)
        for u in units {
            let aTot = u.turb.aEff + u.xWG * u.aWG
            u.p03 = Turbine.inletPressure(per, t03, p04, aTot)
            u.wT = per * u.turb.aEff / aTot
            u.wWGF = per - u.wT
            u.pT = u.turb.power(u.wT, t03, u.p03, p04, u.omega, pulse)
        }
    }

    /// Boost control: a PI on the error with a rate term, overspeed guard,
    /// through each gate's actuator lag.
    func wastegate(_ thr: Double, _ dt: Double) {
        let target = Turbo.pAtm + boostBar * 1.0e5 * min(max(thr, 0.0), 1.0)
        let e = (air.pP - target) / 1.0e5
        let rate = (air.pP - (pPrev ?? air.pP)) / 1.0e5 / max(dt, 1e-4)
        pPrev = air.pP
        for u in units {
            if e > -0.15 {
                u.wgInt = min(max(u.wgInt + Turbo.wgKI * e * dt, 0.0), 1.0)
            }
            var cmd = Turbo.wgKP * e + u.wgInt + Turbo.wgKD * max(rate, 0.0)
            let over = u.omega / max(omegaMax, 1.0) - 1.0
            if over > 0.0 { cmd = max(cmd, Turbo.wgOverspeed * over) }
            cmd = min(max(cmd, 0.0), 1.0)
            u.xWG += (cmd - u.xWG) * min(dt / Turbo.tauWG, 1.0)
        }
    }

    /// Advance the whole system by dt (s).
    public func step(dt: Double, rpm: Double, thr: Double, fuelCut: Bool = false,
                     mguhW: Double = 0.0) {
        stepped += 1
        let nChunks = max(Turbo.truncInt((dt / 0.02).rounded(.up)), 1)   // shaft: <= 20 ms
        let h = dt / Double(nChunks)
        for _ in 0..<nChunks {
            frame(h, rpm, thr, fuelCut, mguhW)
        }
    }

    func frame(_ dt: Double, _ rpm: Double, _ thr: Double, _ fuelCut: Bool,
               _ mguhW: Double) {
        let pP = air.pP
        let (wAir, mapPa, tMan) = hooks.engineFlow(rpm: rpm, thr: thr, pP: pP)
        self.wAir = wAir
        self.mapPa = mapPa
        tP = tMan
        kThr = wAir / max(pP, 1.0)
        p01 = inletPressure(rpm)
        t03 = fuelCut ? max(tMan, Turbo.tAmb) + 150.0
            : hooks.exhaustTemp(rpm: rpm, load: min(mapPa / Turbo.pAtm, 1.0),
                                pr: pP / Turbo.pAtm)
        if antiLag {
            let (wWot, _, _) = hooks.engineFlow(rpm: rpm, thr: 1.0,
                                                pP: Turbo.pAtm + boostBar * 1.0e5)
            wBypass = (thr < 0.2 && rpm > 0.35 * rpmRed && !fuelCut) ? 0.35 * wWot : 0.0
        } else {
            wBypass = 0.0
        }
        exhaust(rpm, thr, dt, fuelCut)
        wastegate(thr, dt)
        for u in units {
            if !u.active {
                u.table = nil
            } else if u.table == nil || abs(u.omega - u.tableOmega) > 0.005 * u.tableOmega
                        || u.table!.ln.p01 != p01 {
                // the speed line only needs redrawing when the shaft has moved
                u.table = u.comp.table(u2: u.u2, p01: p01, t01: Turbo.tAmb)
                u.tableOmega = u.omega
            }
        }
        let pcs = air.run(units, dt, kThr, mapPa, tMan, p01, wBypass)
        for (j, u) in units.enumerated() {
            if u.active {
                u.pC = pcs[j]
            } else {
                // sequential secondary behind its closed intake valve: dead-
                // headed, a relief valve passing a little -- it spins up light
                let ln = u.comp.line(u2: u.u2, p01: p01, t01: Turbo.tAmb)
                u.wC = Turbo.seqRelief * ln.wZ
                u.pC = u.comp.power(u.wC, ln)
            }
            let pFr = u.cFric * u.omega * u.omega
            let pNet = u.pT - u.pC - pFr + (j == 0 ? mguhW : 0.0)
            u.omega = max(u.omega + dt * pNet / (u.j * max(u.omega, 500.0)), 50.0)
        }
    }

    /// Car / telemetry mode: the REAL boost is known; keep the plenum on it
    /// and let each shaft relax to the speed its compressor needs there.
    public func follow(boostBarGauge: Double, rpm: Double, thr: Double, dt: Double) {
        stepped += 1
        let pP = Turbo.pAtm + max(boostBarGauge, -0.7) * 1.0e5
        air.pP = pP
        let (wAir, mapPa, tMan) = hooks.engineFlow(rpm: rpm, thr: thr, pP: pP)
        self.wAir = wAir
        self.mapPa = mapPa
        tP = tMan
        kThr = wAir / max(pP, 1.0)
        var nAct = units.reduce(0) { $0 + ($1.active ? 1 : 0) }
        if nAct == 0 { nAct = 1 }
        for u in units where u.active {
            let w = wAir / Double(nAct)
            u.wC = w
            var lo = 300.0, hi = 1.5 * omegaMax
            for _ in 0..<30 {
                let mid = 0.5 * (lo + hi)
                let ln = u.comp.line(u2: mid * u.comp.d2 * 0.5, p01: Turbo.pAtm,
                                     t01: Turbo.tAmb)
                if Turbo.pAtm * u.comp.pressureRatio(max(w, ln.wZ), ln) < pP {
                    lo = mid
                } else {
                    hi = mid
                }
            }
            u.omega += (0.5 * (lo + hi) - u.omega) * min(dt / 0.15, 1.0)
            u.table = u.comp.table(u2: u.u2, p01: Turbo.pAtm, t01: Turbo.tAmb)
            u.tableOmega = u.omega
        }
    }
}

/// A twin's view of one unit: the physics' compressor and table, its own
/// duct flow.
public final class TwinUnit: DuctUnit {
    public let comp: Compressor
    public let lC: Double
    public var table: CompressorTable?
    public var active: Bool
    public var wC: Double

    init(_ u: TurboUnit) {
        comp = u.comp
        lC = u.lC
        table = u.table
        active = u.active
        wC = u.wC
    }
}

/// The charge-air system again, at audio rate, for the synthesizer: the same
/// equations driven by the physics' shaft speeds, load line and valve set-up,
/// stepped finer on the audio clock.  While nothing is happening it just
/// copies the physics and costs nothing.
public final class Twin {
    public let ts: TurboSystem
    public let air: ChargeAir
    public private(set) var units: [TwinUnit]
    public var busy = 0
    public let record: ChargeAirRecord

    public init(_ ts: TurboSystem) {
        self.ts = ts
        air = ChargeAir(vP: ts.air.vP, bovCda: ts.air.bovCda, bovMode: ts.air.bovMode)
        units = ts.units.map { TwinUnit($0) }
        record = ChargeAirRecord(units: ts.units.count, capacity: 66)
        sync()
    }

    public func sync() {
        air.pP = ts.air.pP
        air.xBov = ts.air.xBov
        air.wBov = ts.air.wBov
        for (p, u) in zip(units, ts.units) {
            p.table = u.table
            p.active = u.active
            p.wC = u.wC
        }
    }

    /// Nothing to resolve: every active unit clear of surge, the valve shut,
    /// the twin on the physics.
    public func quiet() -> Bool {
        if ts.air.xBov > 0.005 || air.xBov > 0.005 { return false }
        if abs(air.pP - ts.air.pP) > 0.03e5 { return false }
        for (p, u) in zip(units, ts.units) {
            guard u.active, let tab = u.table else { continue }
            if u.wC < 1.6 * tab.ln.wZ || p.wC < 1.6 * tab.ln.wZ { return false }
        }
        return true
    }

    /// Advance dt in nSub steps; the per-step record (length nSub) is left in
    /// `record`.
    public func run(dt: Double, nSub: Int) {
        if units.count != ts.units.count { units = ts.units.map { TwinUnit($0) } }
        air.bovMode = ts.air.bovMode
        air.bovCda = ts.air.bovCda
        // ceil(dt / (dt/n)) can land on n + 1 -- as it would in the Python
        record.reset(units: units.count, capacity: max(nSub, 1) + 2)
        if quiet() {
            sync()
            busy = max(busy - 1, 0)
            record.fill(units, air.pP, air.wBov, nSub)
            return
        }
        busy = 8
        for (p, u) in zip(units, ts.units) {
            p.table = u.table
            p.active = u.active
            if !u.active { p.wC = u.wC }
        }
        air.run(units, dt, ts.kThr, ts.mapPa, ts.tP, ts.p01, ts.wBypass,
                h: dt / Double(max(nSub, 1)), record: record)
    }
}
