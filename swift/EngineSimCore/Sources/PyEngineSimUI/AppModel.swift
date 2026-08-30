//
//  AppModel.swift
//  What the screen is looking at.
//
//  The model owns the CarMode, the audio output and the OBD link, and it is
//  the only thing that touches all three.  The view reads published values and
//  nothing else -- in particular the view never touches the render thread, and
//  the render thread never touches the view.
//

import Foundation
import Combine
import EngineSimCore

@MainActor
public final class AppModel: ObservableObject {
    // --- what the screen shows -------------------------------------------
    @Published public var engineName = ""
    @Published public var engineKey = "a3"
    @Published public var carRPM = 0.0
    @Published public var simRPM = 0.0
    @Published public var pedal = 0.0
    @Published public var gear = 0
    @Published public var speedKmh = 0.0
    @Published public var boostBar = 0.0
    @Published public var linkState = "offline"
    @Published public var pollHz = 0.0
    @Published public var renderLoad = 0.0
    @Published public var underruns = 0
    @Published public var shifting = false
    @Published public var manual = true          // sliders, or the real car
    @Published public var mapMode = RpmMap.Mode.stretch
    @Published public var host = "192.168.0.10"
    @Published public var port = 35000
    @Published public var errorText: String?

    // --- the parts --------------------------------------------------------
    public private(set) var library: EngineLibrary?
    var car: CarMode?
    var audio: AudioOutput?
    var obd: OBDTelemetry?
    let manualSource = ManualSource()
    var timer: Timer?
    var lastTick = CFAbsoluteTimeGetCurrent()

    public var engineKeys: [String] { library?.keys ?? [] }

    public init() {}

    // ------------------------------------------------------------- start-up
    public func boot() {
        do {
            let lib = try AppModel.loadLibrary()
            library = lib
            let out = try AudioOutput(renderRate: 32000, blockFrames: 512)
            audio = out
            let mode = try CarMode(
                engineKey: engineKey, library: lib, telemetry: manualSource,
                rpmMap: RpmMap(mode: mapMode, carIdle: 760, carRedline: 6500)
            ) { engine, tables, voicing in
                Synthesizer(engine: engine, tables: tables, voicing: voicing,
                            sampleRate: out.renderRate, block: out.blockFrames)
            }
            car = mode
            mode.start()
            // The synth's own idea of how much lag is downstream: the OBD
            // extrapolation uses it to project the crank forward, so getting
            // it from the actual buffer rather than a guess is the difference
            // between in time and a tenth of a second late.
            out.render = { [weak mode] n in
                mode?.synth?.render(frames: n) ?? [Float](repeating: 0, count: n)
            }
            try out.start()
            engineName = mode.engine.name
            startTicking()
        } catch {
            errorText = error.localizedDescription
        }
    }

    static func loadLibrary() throws -> EngineLibrary {
        func data(_ name: String) throws -> Data {
            guard let u = Bundle.main.url(forResource: name, withExtension: "json")
                ?? Bundle.main.url(forResource: name, withExtension: "json",
                                   subdirectory: "Fixtures") else {
                throw NSError(domain: "AppModel", code: 2, userInfo: [
                    NSLocalizedDescriptionKey: "\(name).json is not in the bundle"])
            }
            return try Data(contentsOf: u)
        }
        return try EngineLibrary(presets: data("presets"),
                                 tables: data("engine_tables"),
                                 voicing: data("engine_voicing"))
    }

    func startTicking() {
        timer?.invalidate()
        // 50 Hz is plenty: the synth integrates the crank at audio rate, so
        // this only keeps the targets fresh.
        timer = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) {
            [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.tick() }
        }
    }

    func tick() {
        let now = CFAbsoluteTimeGetCurrent()
        let dt = min(now - lastTick, 0.1)
        lastTick = now
        guard let car else { return }
        car.update(dt: dt)
        let s = car.status()
        carRPM = s.carRPM; simRPM = s.simRPM; pedal = s.pedal
        gear = s.gear; speedKmh = s.speedKmh; boostBar = s.boostBar
        linkState = s.link; pollHz = s.hz; shifting = s.shifting
        engineName = s.engine
        if let a = audio { renderLoad = a.renderLoad; underruns = a.underruns }
    }

    // ---------------------------------------------------------------- edits
    public func selectEngine(_ key: String) {
        engineKey = key
        try? car?.setEngine(key)
        engineName = car?.engine.name ?? key
    }

    public func setMapMode(_ m: RpmMap.Mode) {
        mapMode = m
        car?.rpmMap.mode = m
    }

    public func setManualRPM(_ rpm: Double) { manualSource.rpm = rpm }
    public func setManualPedal(_ p: Double) { manualSource.throttle = p }

    /// Switch between the sliders and the real car.
    public func useManual(_ on: Bool) {
        manual = on
        if on {
            obd?.stop(); obd = nil
            car?.telemetry = manualSource
            linkState = "manual"
        } else {
            connect()
        }
    }

    public func connect() {
        #if canImport(Network)
        errorText = nil
        linkState = "connecting"
        let h = host, p = UInt16(port)
        // The connect itself blocks, so it must not run on the main actor --
        // but everything it touches afterwards is main-actor state, so the
        // results are handed back rather than written from the task.
        Task.detached {
            do {
                let link = try TCPLink(host: h, port: p)
                let t = OBDTelemetry(link: link)
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    // tell the extrapolator how much audio lag is downstream,
                    // so the crank is projected over the REAL pipeline, not a
                    // guess at it
                    t.outLatency = Double(self.audio?.blockFrames ?? 512)
                        / (self.audio?.renderRate ?? 32000) * 3.0
                    self.obd = t
                    self.car?.telemetry = t
                    self.manual = false
                }
                t.start()
            } catch {
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    self.errorText = error.localizedDescription
                    self.linkState = "offline"
                    self.manual = true
                    self.car?.telemetry = self.manualSource
                }
            }
        }
        #else
        errorText = "no network transport on this platform"
        #endif
    }

    /// What the mapping will do, for a look before driving off.
    public func mapPreview() -> [(car: Double, sim: Double)] {
        guard let car else { return [] }
        return car.rpmMap.preview(engIdle: car.engine.idleRpm,
                                  engRedline: car.engine.redlineRpm)
    }
}
