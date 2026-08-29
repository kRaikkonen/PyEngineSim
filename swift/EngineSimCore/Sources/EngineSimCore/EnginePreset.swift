//
//  EnginePreset.swift
//  The 130 cars, as data.
//
//  These are NOT transcribed from presets.py -- they are loaded from
//  docs/presets.json, which the Python writes through its own round-trippable
//  serializer.  3,408 lines of preset code stay in one place, and this second
//  implementation cannot drift from them by a typo.
//
//  Only the fields this implementation actually uses are declared; Codable
//  ignores the rest of the 95, and the JSON stays the full loss-free dump.
//

import Foundation

public struct Cylinder: Codable {
    public let bore: Double            // m
    public let stroke: Double          // m
    public let rodLength: Double       // m
    public let compressionRatio: Double
    public let cycleOffsetDeg: Double  // where in the 720 deg cycle it fires
    public let bankAngleDeg: Double

    // Derived exactly as engine.Cylinder.__post_init__ does.
    public var pistonArea: Double { Double.pi * (bore / 2.0) * (bore / 2.0) }
    public var displacement: Double { pistonArea * stroke }
    public var clearanceVolume: Double { displacement / (compressionRatio - 1.0) }

    enum CodingKeys: String, CodingKey {
        case bore, stroke
        case rodLength = "rod_length"
        case compressionRatio = "compression_ratio"
        case cycleOffsetDeg = "cycle_offset_deg"
        case bankAngleDeg = "bank_angle_deg"
    }
}

public struct EnginePreset: Codable {
    public let name: String
    public let cylinders: [Cylinder]

    // --- operating range -----------------------------------------------
    public let idleRpm: Double
    public let redlineRpm: Double
    public let idleThrottle: Double
    public let idleAirBase: Double
    public let closedMapFraction: Double
    public let flywheelInertia: Double

    // --- breathing / combustion (the white-box cycle) --------------------
    public let veMax: Double
    public let veFloor: Double
    public let vePeakFrac: Double
    public let veWidthFrac: Double
    public let heatReleaseK: Double
    public let sparkAdvanceDeg: Double

    // --- induction --------------------------------------------------------
    public let induction: String          // "na" | "turbo" | "roots" | ...
    public let boostBar: Double
    public let blowerRatio: Double
    public let antiLag: Bool
    public let electricTurbo: Bool
    public let hybridKw: Double

    // --- exhaust geometry (what the acoustics are built from) -------------
    public let exhaustChannels: Int
    public let exhaustOpenness: Double
    public let exhaustPrimaryM: Double
    public let exhaustRadiusM: Double
    public let exhaustTotalM: Double
    public let headerUnequalDeg: Double
    public let intakeRunnerM: Double
    public let mufflerNeckAreaM2: Double
    public let mufflerNeckLenM: Double
    public let mufflerVolumeM3: Double
    public let exhaustTone: Double

    // --- character --------------------------------------------------------
    public let isRotary: Bool
    public let valvesPerCyl: Int
    public let straightCut: Bool
    public let gearGrain: Double
    public let gearboxType: String
    public let cabinNrDb: Double

    enum CodingKeys: String, CodingKey {
        case name, cylinders, induction
        case idleRpm = "idle_rpm"
        case redlineRpm = "redline_rpm"
        case idleThrottle = "idle_throttle"
        case idleAirBase = "idle_air_base"
        case closedMapFraction = "closed_map_fraction"
        case flywheelInertia = "flywheel_inertia"
        case veMax = "ve_max"
        case veFloor = "ve_floor"
        case vePeakFrac = "ve_peak_frac"
        case veWidthFrac = "ve_width_frac"
        case heatReleaseK = "heat_release_k"
        case sparkAdvanceDeg = "spark_advance_deg"
        case boostBar = "boost_bar"
        case blowerRatio = "blower_ratio"
        case antiLag = "anti_lag"
        case electricTurbo = "electric_turbo"
        case hybridKw = "hybrid_kw"
        case exhaustChannels = "exhaust_channels"
        case exhaustOpenness = "exhaust_openness"
        case exhaustPrimaryM = "exhaust_primary_m"
        case exhaustRadiusM = "exhaust_radius_m"
        case exhaustTotalM = "exhaust_total_m"
        case headerUnequalDeg = "header_unequal_deg"
        case intakeRunnerM = "intake_runner_m"
        case mufflerNeckAreaM2 = "muffler_neck_area_m2"
        case mufflerNeckLenM = "muffler_neck_len_m"
        case mufflerVolumeM3 = "muffler_volume_m3"
        case exhaustTone = "exhaust_tone"
        case isRotary = "is_rotary"
        case valvesPerCyl = "valves_per_cyl"
        case straightCut = "straight_cut"
        case gearGrain = "gear_grain"
        case gearboxType = "gearbox_type"
        case cabinNrDb = "cabin_nr_db"
    }

    // --- derived, matching engine.Engine's properties --------------------
    public var numCylinders: Int { cylinders.count }
    public var totalDisplacement: Double {
        cylinders.reduce(0.0) { $0 + $1.displacement }
    }

    /// Cylinder numbers (1-based) in the order they fire -- derived from the
    /// cycle offsets, so it can never disagree with the physics.
    public var firingOrder: [Int] {
        cylinders.indices
            .sorted { cylinders[$0].cycleOffsetDeg < cylinders[$1].cycleOffsetDeg }
            .map { $0 + 1 }
    }
}

public enum PresetLibrary {
    /// Load the fleet from a presets.json produced by engine_sim.serialize.
    public static func load(from url: URL) throws -> [String: EnginePreset] {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode([String: EnginePreset].self, from: data)
    }

    public static func load(jsonData: Data) throws -> [String: EnginePreset] {
        try JSONDecoder().decode([String: EnginePreset].self, from: jsonData)
    }
}
