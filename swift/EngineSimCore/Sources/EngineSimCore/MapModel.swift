//
//  MapModel.swift
//  Manifold pressure from a steady-state mass balance, ported from
//  engine_sim/map_model.py.
//
//  MAP is where the air the throttle plate lets IN equals the air the
//  cylinders pump OUT:
//
//      in   ~  A_eff(throttle) * psi(MAP/P_atm)     isentropic orifice
//      out  ~  rpm * VE * (MAP/P_atm)               cylinders as a pump
//
//  There is no tuned exponent anywhere in it, which is the point: closed
//  throttle gives deep vacuum, and that vacuum DEEPENS with rpm, because the
//  pump outruns a fixed orifice.  The one calibration is a global
//  orifice/pump ratio sized from the engine's own airflow demand.
//

import Foundation

public enum MapModel {
    public static let pAtm = 101325.0
    public static let gamma = 1.4

    /// Choked pressure ratio, 0.528 for air.
    public static let crit = pow(2.0 / (gamma + 1.0), gamma / (gamma - 1.0))
    static let psiChoke = gamma.squareRoot()
        * pow(2.0 / (gamma + 1.0), (gamma + 1.0) / (2.0 * (gamma - 1.0)))

    /// The single global orifice/pump ratio.
    public static let kBalance = 0.55
    /// Nominal VE used to keep the MAP solve one-way (MAP -> VE -> torque).
    public static let veNom = 0.85

    /// Isentropic orifice flow function at pressure ratio down/up.
    public static func psi(_ pr: Double) -> Double {
        if pr <= crit { return psiChoke }         // choked: flow stops rising
        if pr >= 1.0 { return 0.0 }               // no pressure drop, no flow
        let a = pow(pr, 2.0 / gamma)
        let b = pow(pr, (gamma + 1.0) / gamma)
        return max((2.0 * gamma) / (gamma - 1.0) * (a - b), 0.0).squareRoot()
    }

    /// Effective plate open-area fraction.  A butterfly's projected opening
    /// goes as 1 - cos(angle); the idle bleed sets the floor.
    public static func throttleArea(_ throttle: Double,
                                    idleArea: Double) -> Double {
        let t = min(max(throttle, 0.0), 1.0)
        let plate = 1.0 - cos(0.5 * Double.pi * t)
        return idleArea + (1.0 - idleArea) * plate
    }

    /// Solve MAP/P_atm where inflow meets pumping.  Bisection, because the
    /// choked kink makes anything smoother unreliable -- and 14 halvings is
    /// ~6e-5, far finer than anything downstream can hear.
    public static func solveMapFraction(throttle: Double, rpm: Double,
                                        redline: Double, ve: Double,
                                        idleArea: Double) -> Double {
        let aEff = throttleArea(throttle, idleArea: idleArea)
        let pump = kBalance * max(rpm, 1.0) / max(redline, 1.0) * max(ve, 0.05)

        func imbalance(_ pr: Double) -> Double {
            aEff * psi(pr) - pump * pr            // > 0: inflow wins, pr rises
        }

        var lo = 0.02, hi = 1.0
        if imbalance(hi) >= 0.0 { return 1.0 }    // plate can supply full MAP
        for _ in 0..<14 {
            let mid = 0.5 * (lo + hi)
            if imbalance(mid) > 0.0 { lo = mid } else { hi = mid }
        }
        return 0.5 * (lo + hi)
    }

    /// Idle bleed area, anchored so the model reproduces the preset's own
    /// closed_map_fraction at idle: at a shut pedal the effective area IS the
    /// idle area, so the balance inverts in closed form.  Nothing tuned, and
    /// the known-good idle vacuum survives exactly.
    public static func idleArea(for engine: EnginePreset) -> Double {
        let cmf = min(max(engine.closedMapFraction, 0.05), 0.6)
        let pump = kBalance * (engine.idleRpm / max(engine.redlineRpm, 1.0)) * veNom
        let area = pump * cmf / max(psi(cmf), 1e-6)
        return min(max(area, 0.002), 0.15)
    }
}
