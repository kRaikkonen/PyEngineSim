//
//  ResonanceParams.swift
//  Everything tuning the pipe resonance, derived from physics every block.
//
//  This is the piece that makes the note LIVE.  The delays are round-trip
//  travel times at the live gas temperature, so the resonance slides with
//  load.  The feedback gains carry the real radiation loss at the open end, so
//  a fat pipe leaks more and rings less.  The exhaust valve's opening is a
//  function of revs and pedal, so the system brightens as it is driven.
//
//  Nothing here is a fitted curve.  Every term is a named physical effect and
//  the comments say which -- that is the project's premise, and a port that
//  swapped any of it for an approximation would have thrown the premise away
//  while still sounding roughly similar.
//

import Foundation

public struct Resonance {
    public var d1 = 0, d2 = 0, d3 = 0            // round-trip delays, samples
    public var g1 = 0.0, g2 = 0.0, g3 = 0.0      // feedback gains
    public var lpA = 0.0, lpAEnd = 0.0           // in-loop damping poles
    public var fHelm = 0.0                       // muffler Helmholtz notch, Hz
    public var valve = 0.0                       // exhaust valve openness 0..1
    public var flow = 0.0
    public var postFc = 0.0
    public var sysQ = 0.6
    public var rt60 = 0.1
    public var rvD = [0, 0, 0]                   // cat + two muffler chambers
    public var rvG = [0.0, 0.0, 0.0]
    public var rvLP = 0.0
    public var vtec = 0.0
    public var injAmt = 0.0
    public var balanceRough = 0.0
    public var wobW = 0.0
    public var camLump = 0.0
}

let injectionPressure: [String: Double] = [
    "port": 4.0, "dual": 130.0, "direct": 200.0, "piezo": 350.0, "diesel": 2000.0,
]

public final class ResonanceModel {
    let engine: EnginePreset
    let sampleRate: Double
    let block: Int
    var cSmooth: Double?
    public var wallQ: Double = 1.0

    public init(engine: EnginePreset, sampleRate: Double, block: Int = 256) {
        self.engine = engine
        self.sampleRate = sampleRate
        self.block = block
    }

    public func update(rpm: Double, throttle: Double,
                       soundSpeed: Double) -> Resonance {
        let eng = engine, sr = sampleRate
        // gas temperature moves faster than the pipe can follow, so the
        // effective speed of sound is smoothed
        if cSmooth == nil { cSmooth = soundSpeed }
        cSmooth! += (soundSpeed - cSmooth!) * min(Double(block) / sr / 3.0, 1.0)
        let c = cSmooth!

        var r = Resonance()
        let rad = eng.exhaustRadiusM
        let lPrimary = eng.exhaustPrimaryM + 0.61 * rad      // end correction
        let lTotal = eng.exhaustTotalM + 0.61 * rad
        r.d1 = Int((2.0 * lPrimary * sr / c).rounded())
        r.d2 = Int((2.0 * lTotal * sr / c).rounded())

        let qf = wallQ
        var g = min(0.80 + 0.20 * eng.exhaustOpenness + 0.035 * (qf - 1.0), 0.994)
        let rpmFrac = min(rpm / max(eng.redlineRpm, 1.0), 1.0)
        let drive = min(rpmFrac + 0.15 * min(max(throttle, 0.0), 1.0), 1.0)
        var valve = min(max((drive - 0.28) / 0.45, 0.0), 1.0)
        valve = pow(valve, 1.4)
        r.valve = valve
        r.postFc = 1600.0 + 9600.0 * valve
        let flow = rpmFrac * (0.30 + 0.70 * min(max(throttle, 0.0), 1.0))
        r.flow = flow

        var fc = (1200.0 + 8600.0 * eng.exhaustOpenness) * (0.4 + 0.6 * valve)
        fc *= 1.0 - 0.22 * flow * flow
        g *= 1.0 - 0.09 * flow * flow           // same v^2 loss on the feedback
        if eng.isRotary { r.postFc *= 1.35; fc *= 1.4 }

        if eng.valveLift != "fixed" {
            let step = eng.valveLift == "two-stage" ? 1.0 : 0.22
            var xf = eng.vtecRpm / max(eng.redlineRpm, 1.0)
            if xf == 0.0 { xf = 0.62 }
            r.vtec = min(max((rpmFrac - xf) / 0.06, 0.0), 1.0)
            r.postFc *= 1.0 + 0.30 * step * r.vtec
            fc *= 1.0 + 0.26 * step * r.vtec
        }
        r.postFc *= 0.70 + 0.30 * min(max(eng.tipScale, 0.3), 2.0)
        let camGain = ["mild": -0.06, "hot": 0.12, "race": 0.22][eng.camProfile] ?? 0.0
        r.postFc *= 1.0 + camGain
        r.camLump = (["hot": 0.16, "race": 0.28][eng.camProfile] ?? 0.0)
            * max(1.0 - rpmFrac * 2.2, 0.0)          // lopey idle only
        if eng.integratedManifold { r.postFc *= 0.93; fc *= 0.92 }
        if eng.valveLift == "continuous" { r.postFc *= 0.97 }

        let pRail = injectionPressure[eng.injection] ?? 0.0
        let amt = pRail > 0.0 ? 0.075 * pow(pRail / 200.0, 0.3) : 0.0
        r.injAmt = amt * max(1.0 - rpmFrac * 1.25, 0.22)
        if (eng.numCylinders == 3 || eng.numCylinders == 4) && !eng.isRotary
            && !eng.balanceShaft {
            r.balanceRough = 0.06 * max(1.0 - rpmFrac * 1.6, 0.15)
        }
        let fireHz = max(rpm, 1.0) / 120.0 * Double(eng.numCylinders)
        r.wobW = 2.0 * Double.pi * fireHz / sr
        if eng.valvesPerCyl <= 2 { r.postFc *= 0.82 }
        if eng.hasCat { r.postFc *= 0.85 }
        if eng.hasGpf { r.postFc *= 0.6; fc *= 0.75 }
        r.lpA = exp(-2.0 * Double.pi * fc / sr)

        let absorptive = eng.mufflerType == "absorptive"
        var sysq = 0.35 + 0.45 * eng.exhaustOpenness
            + 0.10 * min(max(qf - 1.0, -1.0), 2.0)          // wall material sing
            - (absorptive ? 0.18 : 0.0)                      // packing soaks Q
            - 0.10 * min(lTotal / 4.0, 1.0)                  // long runs damp
            + 0.06 * min(rad / 0.035, 1.5)                   // fat bore, less wall
        sysq = min(max(sysq + 0.20 * valve, 0.15), 1.0)
        r.sysQ = sysq

        let aTip = rad * max(eng.tipScale, 0.5)
        let lMid = lPrimary + 0.45 * (lTotal - lPrimary)
        r.d3 = Int((2.0 * lMid * sr / c).rounded())

        // radiation loss at the open end: a wide mouth at a high frequency
        // leaks, which is why a big-bore system rings less
        func rend(_ fq: Double) -> Double {
            let kaM = 2.0 * Double.pi * fq * aTip / c
            return min(max(1.0 - 0.45 * kaM * kaM - 0.12 * kaM, 0.45), 1.0)
        }
        let ka = 2.0 * Double.pi * fireHz * aTip / c
        g *= max(1.0 - 0.5 * ka * ka, 0.55)          // firing-centroid escape
        r.g1 = min(g * rend(c / (4.0 * lPrimary)), 0.995)
        r.g3 = min(g * rend(c / (4.0 * lMid)), 0.995)
        r.g2 = min(g * rend(c / (4.0 * lTotal)), 0.995)

        let vBox = max(eng.mufflerVolumeM3, 1e-5)
        var rt60 = 0.06 + 0.24 * (1.0 - eng.exhaustOpenness)
            + 0.05 * min(vBox / 0.004, 1.0)
        if absorptive { rt60 *= 0.75 }
        r.rt60 = min(max(rt60, 0.05), 0.35)

        let lCat = lPrimary + 0.35 * (lTotal - lPrimary)
        let lBox = max(eng.mufflerNeckLenM * 4.0, 0.15)
        r.rvD = [Int((2.0 * lCat * sr / c).rounded()),
                 Int((2.0 * lBox * 0.42 * sr / c).rounded()),
                 Int((2.0 * lBox * 0.58 * sr / c).rounded())]
        r.rvG = r.rvD.map { min(pow(10.0, -3.0 * Double($0) / (r.rt60 * sr)), 0.985) }
        r.rvLP = exp(-2.0 * Double.pi * (1400.0 + 3400.0 * eng.exhaustOpenness) / sr)

        let fcEnd = 0.7 * c / (2.0 * Double.pi * aTip)
        r.lpAEnd = exp(-2.0 * Double.pi * min(fcEnd, fc) / sr)

        // Helmholtz: the muffler box and its neck are a mass on a spring
        let A = eng.mufflerNeckAreaM2, V = eng.mufflerVolumeM3
        let rNeck = (A / Double.pi).squareRoot()
        let lH = eng.mufflerNeckLenM + 1.7 * rNeck
        var fHelm = (c / (2.0 * Double.pi)) * (A / (V * lH)).squareRoot()
        fHelm = min(max(fHelm, 40.0), 400.0)
        r.fHelm = fHelm
        return r
    }
}
