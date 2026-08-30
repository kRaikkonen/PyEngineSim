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
    /// What the audio session actually handed over, which is not always what
    /// was asked for -- worth showing rather than assuming.
    @Published public var audioInfo = ""
    /// Which chain stages are HIDDEN.  Kept here rather than in the synth
    /// because the synth is rebuilt on every engine change -- the switches
    /// belong to the listener, not to the car.
    @Published public var hidden = Set<Stage>()

    // --- the parts --------------------------------------------------------
    public private(set) var library: EngineLibrary?
    var car: CarMode?
    var audio: AudioOutput?
    var obd: OBDTelemetry?
    let manualSource = ManualSource()
    var timer: Timer?
    var lastTick = CFAbsoluteTimeGetCurrent()

    public var engineKeys: [String] { library?.keys ?? [] }
    /// The full name for a key, for the wheel -- 'Audi RS3 EA855 2.5 I5'
    /// tells you what you are choosing; 'rs3' does not.
    public func engineName(_ key: String) -> String {
        library?.name(key) ?? key
    }

    public init() {}

    // ------------------------------------------------------------- start-up
    public func boot() {
        do {
            // restore BEFORE anything is built: the engine choice decides
            // which synth gets constructed, so applying it afterwards would
            // mean building the wrong one first and throwing it away
            let saved = SettingsStore.load()
            engineKey = saved.engineKey
            mapMode = saved.mapMode
            hidden = Set(saved.hidden)
            manual = saved.manual
            host = saved.host
            port = saved.port
            let lib = try AppModel.loadLibrary()
            library = lib
            if lib.engine(engineKey) == nil { engineKey = "a3" }
            let out = try AudioOutput(renderRate: 32000, blockFrames: 512)
            audio = out
            let mode = try CarMode(
                engineKey: engineKey, library: lib, telemetry: manualSource,
                rpmMap: RpmMap(mode: mapMode, carIdle: saved.carIdle,
                               carRedline: saved.carRedline)
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
            audioInfo = String(format: "render %.0f Hz / %d -> out %.0f Hz, "
                               + "buffer %.1f ms", out.renderRate,
                               out.blockFrames, out.hardwareRate,
                               out.ioBufferSeconds * 1000)
            if let n = out.sessionNote { audioInfo += " (" + n + ")" }
            applyLayers()                    // restore the hidden stages
            if !manual { connect() }         // ...and the link, if it was live
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
        applyLayers()          // the new synth starts with every layer visible
        persist()
    }

    // ------------------------------------------------------------- layers
    public var stages: [Stage] { Stage.allCases }

    public func isVisible(_ s: Stage) -> Bool { !hidden.contains(s) }

    /// Hide or show one stage.  A hidden stage passes its input straight
    /// through, so what you hear is exactly what that stage contributes.
    public func toggle(_ s: Stage) {
        if hidden.contains(s) { hidden.remove(s) } else { hidden.insert(s) }
        applyLayers()
        persist()
    }

    /// Hear one stage on its own -- or bring everything back if it already is
    /// the only one.
    public func solo(_ s: Stage) {
        let others = Set(Stage.allCases).subtracting([s])
        hidden = hidden == others ? [] : others
        applyLayers()
        persist()
    }

    public func showAllLayers() {
        hidden = []
        applyLayers()
        persist()
    }

    // -------------------------------------------------------- persistence
    /// Everything the listener chose, as one value.
    public var settings: Settings {
        var s = Settings()
        s.engineKey = engineKey
        s.mapMode = mapMode
        s.hidden = hidden.sorted { $0.rawValue < $1.rawValue }
        s.manual = manual
        s.host = host
        s.port = port
        s.carIdle = car?.rpmMap.carIdle ?? 760
        s.carRedline = car?.rpmMap.carRedline ?? 6500
        return s
    }

    /// Written on every change, not on quit: an app the system kills -- which
    /// is what happens when the phone is unplugged and put down -- never gets
    /// to run a quit handler.
    func persist() { SettingsStore.save(settings) }

    public func apply(_ s: Settings) {
        if s.engineKey != engineKey { selectEngine(s.engineKey) }
        setMapMode(s.mapMode)
        hidden = Set(s.hidden)
        applyLayers()
        host = s.host
        port = s.port
        car?.rpmMap.carIdle = s.carIdle
        car?.rpmMap.carRedline = s.carRedline
        useManual(s.manual)
        persist()
    }

    /// Back to how it ships.  Deliberately does NOT clear the saved slots --
    /// reset is for undoing the last ten minutes of poking, not for losing
    /// the setup you built last week.
    public func resetToDefaults() { apply(.default) }

    public var slotCount: Int { SettingsStore.slotCount }
    public func slotFilled(_ i: Int) -> Bool { SettingsStore.slotFilled(i) }
    public func saveSlot(_ i: Int) {
        SettingsStore.saveSlot(i, settings)
        objectWillChange.send()
    }
    public func loadSlot(_ i: Int) {
        if let s = SettingsStore.loadSlot(i) { apply(s) }
    }

    func applyLayers() {
        guard let l = car?.synth?.layers else { return }
        for s in Stage.allCases { l.set(s, !hidden.contains(s)) }
    }

    public func setMapMode(_ m: RpmMap.Mode) {
        mapMode = m
        car?.rpmMap.mode = m
        persist()
    }

    public func setManualRPM(_ rpm: Double) { manualSource.rpm = rpm }
    public func setManualPedal(_ p: Double) { manualSource.throttle = p }

    /// Switch between the sliders and the real car.
    public func useManual(_ on: Bool) {
        manual = on
        persist()
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
