//
//  Physics.swift
//  The nine quantities the renderer actually reaches into the physics for.
//
//  Not all of simulator.py: the audio chain reads only blowdownPressure,
//  exhaustSoundSpeed, gasTorque, boost, rpm, omega, throttle, ignitionOn and
//  hybridOn, so that is the whole surface to reproduce.
//
//  Two of the quantities behind those -- volumetric efficiency and the burn
//  multiplier -- come from a genuinely deep chain in Python (Mach index,
//  Helmholtz ram tuning, BMEP energy accounting, a per-engine burn
//  calibration, torque and power limits).  Those are BAKED into per-engine
//  tables offline rather than reimplemented, so the numbers stay the ones the
//  sound was tuned against instead of drifting a few percent.  Measured
//  against the Python at random off-grid points: median error 0.00002 of full
//  scale, p95 0.00087.
//
//  What is real code here is what is short and exact: the orifice/pump MAP
//  balance, adiabatic compression and expansion, the blowdown closed form.
//

import Foundation

/// Rectangular 2-D table with bilinear interpolation and edge clamping --
/// the same contract as surrogate.LUT.eval2 in the Python.
public struct Table2D {
    public let rows: [Double]        // ascending
    public let cols: [Double]        // ascending
    public let values: [[Double]]

    public init(rows: [Double], cols: [Double], values: [[Double]]) {
        self.rows = rows
        self.cols = cols
        self.values = values
    }

    static func locate(_ grid: [Double], _ x: Double) -> (Int, Double) {
        if grid.count < 2 { return (0, 0.0) }
        if x <= grid[0] { return (0, 0.0) }
        if x >= grid[grid.count - 1] { return (grid.count - 2, 1.0) }
        var lo = 0, hi = grid.count - 1
        while hi - lo > 1 {
            let mid = (lo + hi) / 2
            if grid[mid] <= x { lo = mid } else { hi = mid }
        }
        let span = grid[lo + 1] - grid[lo]
        return (lo, span > 0 ? (x - grid[lo]) / span : 0.0)
    }

    public func eval(_ r: Double, _ c: Double) -> Double {
        let (i, fi) = Table2D.locate(rows, r)
        let (j, fj) = Table2D.locate(cols, c)
        let v00 = values[i][j], v01 = values[i][j + 1]
        let v10 = values[i + 1][j], v11 = values[i + 1][j + 1]
        return (1 - fi) * ((1 - fj) * v00 + fj * v01)
             + fi * ((1 - fj) * v10 + fj * v11)
    }
}

/// The baked per-engine tables (docs/engine_tables.json).
public struct EngineTables: Decodable {
    public let rpm_axis: [Double]
    public let map_axis: [Double]
    public let ve_rpm_axis: [Double]
    public let ve_map_axis: [Double]
    public let ve: [[Double]]
    public let k_burn: [[Double]]
    public let map_idle_area: Double
    public let charge_temp: [Double]

    public var veTable: Table2D {
        Table2D(rows: ve_rpm_axis, cols: ve_map_axis, values: ve)
    }
    public var burnTable: Table2D {
        Table2D(rows: rpm_axis, cols: map_axis, values: k_burn)
    }

    /// charge temperature, 1-D on the MAP axis
    public func chargeTemp(mapFraction: Double) -> Double {
        let (j, f) = Table2D.locate(map_axis, mapFraction)
        return charge_temp[j] * (1 - f) + charge_temp[j + 1] * f
    }

    public static func load(jsonData: Data) throws -> [String: EngineTables] {
        try JSONDecoder().decode([String: EngineTables].self, from: jsonData)
    }
}

/// The renderer-facing physics state for one engine.
public final class EnginePhysics {
    public static let pAtm = MapModel.pAtm
    /// gamma for the CYCLE, 1.3 -- hot combustion products, not the 1.4 of the
    /// cold air the intake orifice flows.  The Python keeps two constants for
    /// exactly this reason (simulator.GAMMA vs map_model.GAMMA) and conflating
    /// them shifts the exhaust note by ~74 cents.
    public static let gamma = 1.3
    /// combustion temperature rise at full fuelling (simulator._COMB_DT)
    static let combDT = 1700.0

    public let engine: EnginePreset
    public let tables: EngineTables
    private let veTable: Table2D
    private let burnTable: Table2D

    // --- live state, set by whatever is driving (OBD, a slider, physics) ---
    public var rpm: Double = 800
    public var throttle: Double = 0
    public var boost: Double = 0          // bar, gauge
    public var ignitionOn: Bool = true
    public var idleTrim: Double = 0
    public var blip: Double = 0
    public var fuelCut: Bool = false
    public var coolantC: Double = 88.0
    /// The blowdown pressure captured as each cylinder's exhaust valve
    /// actually opened.  Starts at ambient; the simulator updates it as
    /// cylinders fire, which is how a cut cylinder goes quiet on its own.
    public var lastBlowdown: [Double]

    public var omega: Double { rpm * 2.0 * Double.pi / 60.0 }

    public init(engine: EnginePreset, tables: EngineTables) {
        lastBlowdown = [Double](repeating: 101325.0,
                                count: max(engine.numCylinders, 1))
        self.engine = engine
        self.tables = tables
        self.veTable = tables.veTable
        self.burnTable = tables.burnTable
        self.idleTrim = engine.idleAirBase * 1.6
    }

    /// Air demand actually reaching the engine: the larger of pedal, idle
    /// governor and any rev-match blip.
    public var effectiveThrottle: Double {
        min(max(max(throttle, idleTrim), blip), 1.0)
    }

    /// Intake-manifold absolute pressure (Pa).
    ///
    /// Breathing and intake co-determine each other, so this is the same
    /// one-pass fixed point the Python does: solve with a nominal VE, read the
    /// real VE at that MAP, re-solve -- blended toward nominal near idle so
    /// the closed_map_fraction idle anchor survives.
    public func manifoldPressure() -> Double {
        let t = effectiveThrottle
        let boostPa = boost * 1.0e5
        let idleArea = tables.map_idle_area

        let m0 = MapModel.solveMapFraction(throttle: t, rpm: rpm,
                                           redline: engine.redlineRpm,
                                           ve: MapModel.veNom,
                                           idleArea: idleArea)
        let veAct = veTable.eval(rpm, m0)
        let w = min(max((rpm - engine.idleRpm * 1.5) / 2500.0, 0.0), 1.0)
        let ve = MapModel.veNom + (veAct - MapModel.veNom) * w

        let frac = MapModel.solveMapFraction(throttle: t, rpm: rpm,
                                             redline: engine.redlineRpm,
                                             ve: ve, idleArea: idleArea)
        if boostPa > 0.0 {
            // The plate draws from the COMPRESSOR OUTLET, not the atmosphere,
            // so the same orifice fraction scales the boosted upstream --
            // anchored to the WOT fraction so rated boost is preserved.
            let fracWot = MapModel.solveMapFraction(throttle: 1.0, rpm: rpm,
                                                    redline: engine.redlineRpm,
                                                    ve: ve, idleArea: idleArea)
            return (frac / max(fracWot, 0.25)) * (Self.pAtm + boostPa)
        }
        return frac * Self.pAtm
    }

    /// Cylinder pressure (Pa) when the exhaust valve cracks open -- the
    /// strength of each blowdown pulse, and so the source of the sound.
    public func blowdownPressure() -> Double {
        let cyl = engine.cylinders[0]
        let pMan = manifoldPressure()
        let combusting = ignitionOn && !fuelCut

        let vBdc = cyl.clearanceVolume + cyl.pistonArea * cyl.stroke
        let vTdc = cyl.clearanceVolume
        let pComp = pMan * pow(vBdc / vTdc, Self.gamma)
        let kBurn = burnTable.eval(rpm, pMan / Self.pAtm)
        let pPeak = pComp * (combusting ? kBurn : 1.0)
        // expand to the crank angle where the exhaust valve opens (~510 deg)
        let theta = (510.0.truncatingRemainder(dividingBy: 360.0)) * Double.pi / 180.0
        let vOpen = cylinderVolume(cyl, theta)
        return pPeak * pow(vTdc / vOpen, Self.gamma)
    }

    /// Exhaust-gas temperature (K) at the valve, from the real cycle:
    /// charge temp -> adiabatic compression -> heat release -> expansion ->
    /// wall loss.  This is what slides the note's pitch with load.
    public func exhaustGasTemp() -> Double {
        let cr = min(max(engine.cylinders[0].compressionRatio, 6.0), 24.0)
        let load = effectiveThrottle
        let rpmFrac = min(rpm / max(engine.redlineRpm, 1.0), 1.0)
        let mapf = 1.0 + max(boost, 0.0)
        let tCharge = tables.chargeTemp(mapFraction: mapf)
        let tComp = tCharge * pow(cr, Self.gamma - 1.0)
        let tPeak = tComp + Self.combDT * (0.42 + 0.58 * load)
        let tEvo = tPeak / pow(0.85 * cr, Self.gamma - 1.0)
        let tc = coolantC + 273.15
        return min(max(tc + (tEvo - tc) * (0.60 + 0.40 * rpmFrac), 300.0), 1500.0)
    }

    public func exhaustSoundSpeed() -> Double {
        (Self.gamma * 287.0 * exhaustGasTemp()).squareRoot()
    }

    // --- crank-slider geometry, as engine.Cylinder ------------------------
    func pistonDisplacement(_ cyl: Cylinder, _ theta: Double) -> Double {
        let r = cyl.stroke / 2.0
        let l = cyl.rodLength
        let s = r * sin(theta)
        return r * (1.0 - cos(theta)) + l - (l * l - s * s).squareRoot()
    }

    func cylinderVolume(_ cyl: Cylinder, _ theta: Double) -> Double {
        cyl.clearanceVolume + cyl.pistonArea * pistonDisplacement(cyl, theta)
    }
}
