//
//  TurboHooks.swift
//  The engine side of the turbo machine: what the simulator hands turbo.build
//  and TurboSystem.step in the Python (Simulator._turbo_flow, _turbo_t03,
//  afr), on the same baked VE table the rest of the Swift physics uses.
//
//  The Python memoises the plate's fraction per 25 rpm / 1/400 pedal and
//  keeps whichever operating point it met first in a bucket; here it is
//  computed exactly each time (three 14-step bisections -- nothing, at the
//  machine's 10 ms), which is what the Python's reference runs do too
//  (tools/export_turbo_ref.py switches the memo off).
//

import Foundation

public final class EngineBreathing: TurboHooks {
    public static let pAtm = 101325.0
    /// bmep_model.charge_temp's ambient and its compressor
    static let tAmbCharge = 300.0
    static let gammaAir = 1.40
    static let etaComp = 0.70

    let engine: EnginePreset
    let ve: Table2D
    let idleArea: Double
    let vd: Double
    let diesel: Bool
    let intercoolerEff: Double
    let wotArea: Double
    let turbo: Bool

    /// Live state the turbine's heat follows (Simulator.coolant_c, ic_soak).
    public var coolantC = 88.0
    public var icSoak = 0.0

    public init(engine: EnginePreset, tables: EngineTables, spec: TurboSpec) {
        self.engine = engine
        ve = tables.veTable
        idleArea = tables.map_idle_area
        vd = spec.vd
        diesel = spec.diesel
        intercoolerEff = spec.intercooler_eff
        wotArea = spec.map_wot_area
        turbo = engine.induction == "turbo"
    }

    func solve(_ t: Double, _ rpm: Double, _ v: Double, wot: Double = 1.0) -> Double {
        MapModel.solveMapFraction(throttle: t, rpm: rpm, redline: engine.redlineRpm,
                                  ve: v, idleArea: idleArea, wotArea: wot)
    }

    /// Simulator._map_at: the manifold at (rpm, effective throttle t), the
    /// plate fed from P_ATM + boostPa; `fed` true: a compressor feeds it
    /// whatever it holds.
    public func mapAt(rpm: Double, t: Double, boostPa: Double, fed: Bool?) -> Double {
        let m0 = solve(t, rpm, MapModel.veNom)
        let veAct = ve.eval(rpm, m0)
        let w = min(max((rpm - engine.idleRpm * 1.5) / 2500.0, 0.0), 1.0)
        let v = MapModel.veNom + (veAct - MapModel.veNom) * w
        let frac = solve(t, rpm, v)
        if fed == true || (fed == nil && boostPa > 0.0) {
            let fracWot = solve(1.0, rpm, v)
            return (frac / max(fracWot, 0.25)) * (Self.pAtm + boostPa)
        }
        return frac * itbWotGain(rpm, v) * Self.pAtm
    }

    /// Simulator._itb_wot_gain (1 for every road intake).
    func itbWotGain(_ rpm: Double, _ v: Double) -> Double {
        if wotArea <= 1.0 { return 1.0 }
        let f1 = solve(1.0, rpm, v)
        let fw = solve(1.0, rpm, v, wot: wotArea)
        return fw / max(f1, 1e-3)
    }

    /// bmep_model.charge_temp: compressor heat, then the intercooler.
    public func chargeTemp(_ mapf: Double) -> Double {
        let pr = max(mapf, 1.0)
        let t2 = Self.tAmbCharge
            * (1.0 + (pow(pr, (Self.gammaAir - 1.0) / Self.gammaAir) - 1.0) / Self.etaComp)
        var eps = min(max(intercoolerEff, 0.0), 0.95)
        eps *= 1.0 - 0.22 * min(max(mapf - 1.0, 0.0), 1.6)
        eps *= 1.0 - 0.12 * min(max(icSoak, 0.0), 1.0)
        return t2 - eps * (t2 - Self.tAmbCharge)
    }

    /// Simulator._air_from_map: (kg/s, MAP, T_charge, VE).
    public func airFromMap(rpm: Double, mapPa: Double, pUp: Double)
        -> (Double, Double, Double, Double) {
        let mapf = mapPa / Self.pAtm
        let v = ve.eval(rpm, mapf)
        let pr = turbo ? max(max(pUp / Self.pAtm, mapf), 1.0) : max(mapf, 1.0)
        let tMan = chargeTemp(pr)
        let w = mapPa / (287.0 * tMan) * vd * max(rpm, 0.0) / 120.0 * max(v, 0.0)
        return (w, mapPa, tMan, v)
    }

    /// Simulator._wot_mapf: an NA plate's wide-open fraction, at the centre
    /// of this rpm's 25 rpm bucket.
    public func wotMapf(_ rpm: Double) -> Double {
        let b = Turbo.truncInt(max(rpm, 1.0)) / 25
        return max(mapAt(rpm: Double(b) * 25.0 + 12.5, t: 1.0, boostPa: 0.0, fed: nil)
                   / Self.pAtm, 0.05)
    }

    /// Simulator._charge_rel: the charge over a wide-open plate's.
    public func chargeRel(_ mapf: Double, _ rpm: Double) -> Double {
        min(max(mapf / wotMapf(rpm), 0.0), 1.0)
    }

    // ------------------------------------------------------------ TurboHooks
    public func engineFlow(rpm: Double, thr: Double, pP: Double) -> (Double, Double, Double) {
        let fr = mapAt(rpm: max(rpm, 1.0), t: thr, boostPa: 0.0, fed: true) / Self.pAtm
        let (w, mapPa, tMan, _) = airFromMap(rpm: rpm, mapPa: fr * pP, pUp: pP)
        return (w, mapPa, tMan)
    }

    public func afr(load: Double, rpm: Double) -> Double {
        if diesel { return 25.0 }
        let x = chargeRel(load, rpm)
        return 14.7 - 2.2 * min(max((x - 0.5) / 0.5, 0.0), 1.0)
    }

    /// Simulator.exhaust_gas_temp(rpm, load, pr): charge -> compression ->
    /// heat release (by the CHARGE, not the pedal) -> expansion -> walls.
    public func exhaustTemp(rpm: Double, load: Double, pr: Double) -> Double {
        let cr = min(max(engine.cylinders[0].compressionRatio, 6.0), 24.0)
        let l = min(max(chargeRel(load, rpm), 0.0), 1.0)
        let rpmFrac = min(rpm / max(engine.redlineRpm, 1.0), 1.0)
        let tCharge = chargeTemp(max(pr, 1.0))
        let g1 = EnginePhysics.gamma - 1.0
        let tComp = tCharge * pow(cr, g1)
        let tPeak = tComp + EnginePhysics.combDT * (0.333 + 0.667 * l)
        let tEvo = tPeak / pow(0.85 * cr, g1)
        let tc = coolantC + 273.15
        return min(max(tc + (tEvo - tc) * (0.60 + 0.40 * rpmFrac), 300.0), 1500.0)
    }

    /// Simulator._update_heat_soak: the intercooler core warms over ~20 s of
    /// boost and cools off it.
    public func updateHeatSoak(dt: Double, boost: Double, boostBar: Double) {
        if intercoolerEff <= 0.05 { icSoak = 0.0; return }
        let bf = min(max(boost, 0.0) / max(boostBar, 0.1), 1.0)
        let (target, tau) = bf > 0.15 ? (bf, 22.0) : (0.0, 12.0)
        icSoak += (target - icSoak) * min(dt / tau, 1.0)
        icSoak = min(max(icSoak, 0.0), 1.0)
    }
}
