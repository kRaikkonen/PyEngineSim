//
//  CarMode.swift
//  Play a chosen engine at a real car's revs.
//
//  No UI, no I/O, no printing -- this is the control layer, and it is a
//  separate object from the app precisely so it can be driven by a test with
//  no dongle and no audio device.
//
//  The one design decision worth stating: swapping engines REBUILDS the
//  synthesizer rather than rebinding it.  A Synthesizer bakes its voicing
//  tables -- exhaust geometry, firing offsets, cavity reverbs -- from the
//  engine it was constructed with, so handing it a new preset would keep
//  playing the old car's timbre at the new car's revs.  Crank speed carries
//  across so the pitch does not jump on the way through.
//

import Foundation

/// Anything that can say what the car is doing.  A live ELM327 and a slider
/// both satisfy it, which is how the whole path is exercised on a desk.
public protocol TelemetrySource: AnyObject {
    var rpm: Double { get }          // projected forward; what you play
    var rawRPM: Double { get }       // un-projected; what shifts are found in
    var throttle: Double { get }
    var speed: Double { get }        // m/s
    var gear: Int { get }
    var mapKPa: Double { get }
    var baroKPa: Double { get }
    var speedValid: Bool { get }
    var hz: Double { get }
    var status: String { get }
    func isLive() -> Bool
}

extension OBDTelemetry: TelemetrySource {
    public func isLive() -> Bool { status == "live" && lastPacket > 0 }
}

/// A hand-driven source: the sliders, for setting the thing up without a car.
public final class ManualSource: TelemetrySource {
    public var rpm: Double = 800
    public var rawRPM: Double { rpm }
    public var throttle: Double = 0
    public var speed: Double = 0
    public var gear: Int = 0
    public var mapKPa: Double = 0
    public var baroKPa: Double = 101.3
    public var speedValid: Bool = true
    public var hz: Double = 0
    public var status: String = "manual"
    public init() {}
    public func isLive() -> Bool { true }
}

public struct CarStatus {
    public var live = false
    public var link = "waiting"
    public var carRPM = 0.0
    public var simRPM = 0.0
    public var pedal = 0.0
    public var gear = 0
    public var speedKmh = 0.0
    public var boostBar = 0.0
    public var hz = 0.0
    public var shifting = false
    public var engine = ""
}

public final class CarMode {
    /// How fast the simulated crank chases the mapped target.  Fast enough to
    /// feel connected, slow enough that a single bad OBD sample is a wobble
    /// rather than a click.
    static let rpmTrack = 14.0
    static let idleTrack = 3.0

    public private(set) var engineKey: String
    public private(set) var engine: EnginePreset
    public var telemetry: TelemetrySource?
    public var rpmMap: RpmMap
    public var shiftPop = true
    public private(set) var shifting = false
    public private(set) var rpmOut = 0.0
    public private(set) var running = false
    public private(set) var synth: Synthesizer?

    let shifter = ShiftDetector()
    let library: EngineLibrary
    /// Rebuilt on every engine change -- see the file comment.
    let makeSynth: (EnginePreset, EngineTables, VoicingSetup) -> Synthesizer

    var omega = 0.0       // rad/s
    var boost = 0.0

    public init(engineKey: String, library: EngineLibrary,
                telemetry: TelemetrySource? = nil,
                rpmMap: RpmMap = RpmMap(mode: .direct),
                makeSynth: @escaping (EnginePreset, EngineTables, VoicingSetup)
                    -> Synthesizer) throws {
        guard let e = library.engine(engineKey) else {
            throw NSError(domain: "CarMode", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "unknown engine preset: \(engineKey)"])
        }
        self.engineKey = engineKey
        self.engine = e
        self.library = library
        self.telemetry = telemetry
        self.rpmMap = rpmMap
        self.makeSynth = makeSynth
        omega = e.idleRpm * 2.0 * Double.pi / 60.0
    }

    // ------------------------------------------------------------ lifecycle
    public func start() {
        if synth == nil, let t = library.tables(engineKey),
           let v = library.voicing(engineKey) {
            synth = makeSynth(engine, t, v)
        }
        running = true
    }

    public func stop() {
        running = false
        synth = nil
    }

    /// Swap the engine, keeping the link and the rpm map.
    public func setEngine(_ key: String) throws {
        guard let e = library.engine(key), let t = library.tables(key),
              let v = library.voicing(key) else {
            throw NSError(domain: "CarMode", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "unknown engine preset: \(key)"])
        }
        engineKey = key
        engine = e
        synth = nil
        if running { synth = makeSynth(e, t, v) }   // rebuilt, never rebound
    }

    // ----------------------------------------------------------------- tick
    /// Advance one control step.  Call it as often as you like -- the synth
    /// integrates the crank at audio rate itself, so this only has to keep the
    /// targets fresh.
    @discardableResult
    public func update(dt rawDt: Double) -> Double {
        let dt = min(max(rawDt, 0.0), 0.1)
        let idleOmega = engine.idleRpm * 2.0 * Double.pi / 60.0

        if let tm = telemetry, tm.isLive() {
            rpmMap.observe(rpm: tm.rawRPM, pedal: tm.throttle)
            rpmOut = rpmMap(tm.rpm, engIdle: engine.idleRpm,
                            engRedline: engine.redlineRpm)

            shifting = shiftPop && shifter.update(dt: dt, rpm: tm.rawRPM,
                                                  pedal: tm.throttle,
                                                  speed: tm.speed)
            // an ignition cut IS a closed throttle as far as the exhaust is
            // concerned -- that is the bang
            let throttle = shifting ? 0.0 : tm.throttle

            let target = rpmOut * 2.0 * Double.pi / 60.0
            omega += (target - omega) * min(CarMode.rpmTrack * dt, 1.0)

            if engine.induction != "na" && tm.mapKPa > 0 {
                // YOUR real boost drives the simulated compressor
                boost = max(0.0, (tm.mapKPa - tm.baroKPa) * 0.01)
            }
            if let s = synth {
                s.set(rpm: rpmOut, throttle: throttle, boost: boost)
                s.speed = tm.speedValid ? tm.speed : 0.0
                s.drive.gear = max(tm.gear, 0)
                s.drive.speed = s.speed
            }
        } else {
            shifting = false
            omega += (idleOmega - omega) * min(CarMode.idleTrack * dt, 1.0)
            rpmOut = omega * 60.0 / (2.0 * Double.pi)
            synth?.set(rpm: rpmOut, throttle: 0.0, boost: 0.0)
        }
        return rpmOut
    }

    // --------------------------------------------------------------- status
    public func status() -> CarStatus {
        var s = CarStatus()
        let tm = telemetry
        s.live = tm?.isLive() ?? false
        s.link = s.live ? "LIVE" : (tm?.status.isEmpty == false
                                    ? tm!.status : "waiting")
        s.carRPM = tm?.rawRPM ?? 0
        s.simRPM = rpmOut
        s.pedal = tm?.throttle ?? 0
        s.gear = tm?.gear ?? 0
        s.speedKmh = (tm?.speed ?? 0) * 3.6
        if let t = tm, t.mapKPa > 0 { s.boostBar = (t.mapKPa - t.baroKPa) * 0.01 }
        s.hz = tm?.hz ?? 0
        s.shifting = shifting
        s.engine = engine.name
        return s
    }
}

/// The three fixture files, loaded once and looked up by preset key.
public final class EngineLibrary {
    let engines: [String: EnginePreset]
    let tablesByKey: [String: EngineTables]
    let voicings: [String: VoicingSetup]
    let torques: [String: TorqueTable]

    public init(presets: Data, tables: Data, voicing: Data,
                torque: Data? = nil) throws {
        engines = try PresetLibrary.load(jsonData: presets)
        tablesByKey = try EngineTables.load(jsonData: tables)
        voicings = try VoicingSetup.load(jsonData: voicing)
        torques = try torque.map { try TorqueTable.load(jsonData: $0) } ?? [:]
    }

    public func torque(_ k: String) -> TorqueTable? { torques[k] }

    public var keys: [String] { engines.keys.sorted() }
    public func engine(_ k: String) -> EnginePreset? { engines[k] }
    public func tables(_ k: String) -> EngineTables? { tablesByKey[k] }
    public func voicing(_ k: String) -> VoicingSetup? { voicings[k] }
    public func name(_ k: String) -> String { engines[k]?.name ?? k }
}
