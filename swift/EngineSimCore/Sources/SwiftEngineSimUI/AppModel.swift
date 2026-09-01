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
    /// Where the revs come from.  The pedal INVENTS the car, so it is an
    /// offline mode by definition -- it cannot be the source while a real one
    /// is talking, and picking the car drops it.
    /// The sliders are gone from the UI: demo does everything they did and
    /// more, and two hand-driven modes is one too many to explain.
    public enum Source: String { case demo, live }
    @Published public var source = Source.demo
    @Published public var popsOn = true
    /// Keep the note up on a lift.  Not physical, on by choice -- the
    /// interesting part of a lift is the overrun and the bangs, and having
    /// everything duck underneath them buries what you lifted to hear.
    @Published public var sustainOnLift = 0.85
    /// The pedal's own controls, published so the sliders track them.
    @Published public var pedalThrottle = 0.0
    @Published public var pedalBrake = 0.0
    /// Set while the rev limiter is cutting, so the screen can show it.
    @Published public var limiting = false
    public var manual: Bool { source != .live }
    @Published public var mapMode = RpmMap.Mode.stretch
    @Published public var host = "192.168.0.10"
    @Published public var port = 35000
    @Published public var errorText: String?
    /// wifi | ble.  A BLE dongle and a WiFi one are different radios, not two
    /// settings of the same thing.
    @Published public var linkKind = "ble"
    @Published public var bleFound: [BLEDevice] = []
    @Published public var scanning = false
    public var bleDeviceName = ""
    var bleDeviceID = ""
    /// What the audio session actually handed over, which is not always what
    /// was asked for -- worth showing rather than assuming.
    @Published public var audioInfo = ""
    /// Which chain stages are HIDDEN.  Kept here rather than in the synth
    /// because the synth is rebuilt on every engine change -- the switches
    /// belong to the listener, not to the car.
    @Published public var hidden = Set<Stage>()
    /// The engine-bay animation.  Off by default, and off costs nothing: the
    /// view owns every moving part, so when this is false SwiftUI never builds
    /// it and no crank is integrated and no pulse is stepped.  Nothing was
    /// added to `tick()` for it either.
    @Published public var showBay = false
    /// 1 = real time; below that the strobe unwinds.
    @Published public var bayTimeScale = 1.0
    /// The REAL car's rev range that the mapping stretches from.  Published so
    /// the readout moves as the map learns, not only when it is set by hand.
    @Published public var carIdle = 760.0
    @Published public var carRedline = 6500.0
    @Published public var learnRange = true
    /// The highest the car has actually been seen to rev this session.
    public var seenMax: Double { car?.rpmMap.seenMax ?? 0 }
    /// Per-cylinder firing lamps, straight off the audio crank.
    public var cylinderLight: [Double] { car?.synth?.cylinderLight ?? [] }
    /// The preset the bay is drawn FROM -- same object the sound is built from.
    public var enginePreset: EnginePreset? { car?.engine }
    /// Hot-gas sound speed, so a pulse crawls down the drawn pipe at the rate
    /// the acoustics say it does (a cold engine's really are slower).
    public var exhaustSoundSpeed: Double {
        car?.synth?.physics.exhaustSoundSpeed() ?? 340.0
    }
    /// How close to the limiter, 0..1 -- what the shift lights read.
    public var revFraction: Double {
        let r = car?.engine.redlineRpm ?? 1
        return min(max(simRPM / max(r, 1), 0), 1.05)
    }

    // --- the parts --------------------------------------------------------
    public private(set) var library: EngineLibrary?
    var car: CarMode?
    var audio: AudioOutput?
    var obd: OBDTelemetry?
    #if canImport(CoreBluetooth)
    var ble: BLELink?
    #endif
    let manualSource = ManualSource()
    public private(set) var pedalSource: PedalSource?
    var timer: Timer?
    var lastTick = CFAbsoluteTimeGetCurrent()

    public var engineKeys: [String] { library?.keys ?? [] }
    public var sourceIcon: String {
        switch source {
        case .demo: return "gamecontroller"
        case .live: return "antenna.radiowaves.left.and.right"
        }
    }
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
            // "pedal" and "sliders" are what these were called before
            source = Source(rawValue: saved.source)
                ?? (saved.source == "pedal" ? .demo : .demo)
            popsOn = saved.pops
            sustainOnLift = saved.sustainOnLift
            host = saved.host
            port = saved.port
            linkKind = saved.linkKind
            bleDeviceID = saved.bleDeviceID
            bleDeviceName = saved.bleDeviceName
            carIdle = saved.carIdle
            carRedline = saved.carRedline
            learnRange = saved.learnRange
            showBay = saved.showBay
            bayTimeScale = saved.bayTimeScale
            let lib = try AppModel.loadLibrary()
            library = lib
            if lib.engine(engineKey) == nil { engineKey = "a3" }
            let out = try AudioOutput(renderRate: 32000, blockFrames: 512)
            audio = out
            let mode = try CarMode(
                engineKey: engineKey, library: lib, telemetry: manualSource,
                rpmMap: RpmMap(mode: mapMode, carIdle: saved.carIdle,
                               carRedline: saved.carRedline,
                               learn: saved.learnRange)
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
            applySustain()
            setSource(source)                // rebuild the pedal, or reconnect
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
                                 voicing: data("engine_voicing"),
                                 torque: try? data("engine_torque"))
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
        // the pedal car is stepped BEFORE the chain reads it, so a press and
        // the note that answers it belong to the same frame
        pedalSource?.update(dt: dt)
        limiting = pedalSource?.limiting ?? false
        car.update(dt: dt)
        let s = car.status()
        carRPM = s.carRPM; simRPM = s.simRPM; pedal = s.pedal
        gear = s.gear; speedKmh = s.speedKmh; boostBar = s.boostBar
        linkState = s.link; pollHz = s.hz; shifting = s.shifting
        engineName = s.engine
        if let a = audio { renderLoad = a.renderLoad; underruns = a.underruns }
        syncRangeFromMap()
    }

    // ---------------------------------------------------------------- edits
    public func selectEngine(_ key: String) {
        engineKey = key
        try? car?.setEngine(key)
        engineName = car?.engine.name ?? key
        applyLayers()          // the new synth starts with every layer visible
        if source == .demo {
            makePedal()                       // new car, new ratios and torque
            car?.telemetry = pedalSource
        }
        applyPops()                           // and a new synth needs re-arming
        applySustain()
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

    // ------------------------------------------------------ the real car
    // No text field: a keyboard is not a control you can use while driving.
    // Steps of 100 rpm, which is finer than anyone knows their own redline.
    public func nudgeIdle(_ delta: Double) {
        carIdle = min(max(carIdle + delta, 400), 2000)
        stopLearningBecauseItWasSetByHand()
    }

    public func nudgeRedline(_ delta: Double) {
        carRedline = min(max(carRedline + delta, carIdle + 800), 12000)
        stopLearningBecauseItWasSetByHand()
    }

    /// Take the highest the car has actually revved as the redline.  Better
    /// than a number off a spec sheet: the fuel cut is what matters, and it
    /// is what you just hit.
    public func useSeenRedline() {
        guard seenMax > carIdle + 800 else { return }
        carRedline = seenMax
        stopLearningBecauseItWasSetByHand()
    }

    /// Hand back to the car.  Clears nothing -- it just starts letting the
    /// highest rpm seen win again.
    public func relearnRange() {
        learnRange = true
        pushRange()
        persist()
    }

    /// A value you set by hand and an automatic value that overwrites it are
    /// a fight the automatic one always wins, and it looks like a bug.  So
    /// touching either number turns the learning off.
    private func stopLearningBecauseItWasSetByHand() {
        learnRange = false
        pushRange()
        persist()
    }

    func pushRange() {
        guard let m = car?.rpmMap else { return }
        m.carIdle = carIdle
        m.carRedline = carRedline
        m.learn = learnRange
    }

    /// Pull the learned values back out, so the readout tracks the car.
    func syncRangeFromMap() {
        guard learnRange, let m = car?.rpmMap else { return }
        if m.carIdle != carIdle { carIdle = m.carIdle }
        if m.carRedline != carRedline { carRedline = m.carRedline }
    }

    // -------------------------------------------------------- persistence
    /// Everything the listener chose, as one value.
    public var settings: Settings {
        var s = Settings()
        s.engineKey = engineKey
        s.mapMode = mapMode
        s.hidden = hidden.sorted { $0.rawValue < $1.rawValue }
        s.source = source.rawValue
        s.pops = popsOn
        s.sustainOnLift = sustainOnLift
        s.host = host
        s.port = port
        s.linkKind = linkKind
        s.bleDeviceID = bleDeviceID
        s.bleDeviceName = bleDeviceName
        s.showBay = showBay
        s.bayTimeScale = bayTimeScale
        s.carIdle = carIdle
        s.carRedline = carRedline
        s.learnRange = learnRange
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
        linkKind = s.linkKind
        bleDeviceID = s.bleDeviceID
        bleDeviceName = s.bleDeviceName
        showBay = s.showBay
        bayTimeScale = s.bayTimeScale
        carIdle = s.carIdle
        carRedline = s.carRedline
        learnRange = s.learnRange
        pushRange()
        popsOn = s.pops
        sustainOnLift = s.sustainOnLift
        applySustain()
        // an unknown name means a setup saved before the modes changed; demo
        // is the safe landing, since it needs nothing to be plugged in
        setSource(Source(rawValue: s.source) ?? .demo)
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

    public func setSource(_ s: Source) {
        source = s
        if s != .live { obd?.stop(); obd = nil }
        switch s {
        case .demo:
            makePedal()
            car?.telemetry = pedalSource
            linkState = "demo"
            // demo drives the engine itself, so there is no second car to
            // stretch from: anything but direct would distort a number this
            // app invented in the first place
            setMapMode(.direct)
        case .live:
            pedalSource = nil
            connect()
        }
        applyPops()
        applySustain()
        persist()
    }

    /// Rebuilt per engine: the torque surface, the ratios, the mass and the
    /// flywheel all belong to the car you are driving, not to the app.
    func makePedal() {
        guard let t = library?.torque(engineKey) else { pedalSource = nil; return }
        let p = PedalSource(table: t)
        pedalSource = p
    }

    public func setPops(_ on: Bool) {
        popsOn = on
        applyPops()
        persist()
    }

    func applyPops() { car?.synth?.pops.enabled = popsOn }

    func applySustain() { car?.synth?.sustainOnLift = sustainOnLift }

    public func setSustainOnLift(_ v: Double) {
        sustainOnLift = v
        applySustain()
        persist()
    }

    // ------------------------------------------------------------- driving
    public func setPedal(_ v: Double) {
        pedalThrottle = v
        pedalSource?.throttle = v
    }

    public func setBrake(_ v: Double) {
        pedalBrake = v
        pedalSource?.brake = v
    }
    public func upshift() { pedalSource?.upshift() }
    public func downshift() { pedalSource?.downshift() }
    public var pedalGear: Int { pedalSource?.gear ?? 0 }
    public var pedalGearCount: Int { pedalSource?.gearCount ?? 0 }

    /// A device the scan found, without the UI needing CoreBluetooth types.
    public struct BLEDevice: Identifiable, Equatable {
        public let id: UUID
        public let name: String
        public let rssi: Int
    }

    /// Turn the engine-bay animation on or off.
    ///
    /// Off tears the view down, which is what makes off free -- the animator,
    /// the pulse field and the redraw clock all belong to the view.
    public func setShowBay(_ on: Bool) {
        showBay = on
        persist()
    }

    public func setBayTimeScale(_ v: Double) {
        bayTimeScale = v
        persist()
    }

    public func setLinkKind(_ k: String) {
        linkKind = k
        persist()
    }

    /// Look for BLE dongles.  Deliberately unfiltered -- these things are
    /// inconsistent about what they advertise, and a filtered scan that finds
    /// nothing looks exactly like no dongle being present.
    public func scanForDongles() {
        #if canImport(CoreBluetooth)
        errorText = nil
        bleFound = []
        scanning = true
        let l = ble ?? BLELink()
        ble = l
        l.scan(seconds: 6.0) { [weak self] found in
            self?.bleFound = found.map {
                BLEDevice(id: $0.id, name: $0.name, rssi: $0.rssi)
            }
        }
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 6_200_000_000)
            self?.scanning = false
            if self?.bleFound.isEmpty == true {
                self?.errorText = "No BLE dongle found.  If yours shows up in "
                    + "Settings > Bluetooth it is a CLASSIC (SPP) adapter, and "
                    + "iOS cannot talk to those from any app -- it has to be a "
                    + "BLE one."
            }
        }
        #else
        errorText = "no Bluetooth on this platform"
        #endif
    }

    public func connectBLE(_ device: BLEDevice) {
        #if canImport(CoreBluetooth)
        bleDeviceID = device.id.uuidString
        bleDeviceName = device.name
        linkKind = "ble"
        persist()
        connect()
        #endif
    }

    public func connect() {
        if linkKind == "ble" { connectOverBLE(); return }
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
                    self.source = .live
                }
                t.start()
            } catch {
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    self.errorText = error.localizedDescription
                    self.linkState = "offline"
                    // a link that will not open falls back to the sliders
                    // rather than leaving the app with no source at all
                    // a link that will not open falls back to demo rather
                    // than leaving the app with no source at all
                    self.setSource(.demo)
                }
            }
        }
        #else
        errorText = "no network transport on this platform"
        #endif
    }

    func connectOverBLE() {
        #if canImport(CoreBluetooth)
        errorText = nil
        linkState = "connecting"
        guard let id = UUID(uuidString: bleDeviceID) else {
            linkState = "offline"
            errorText = "pick a dongle first"
            scanForDongles()
            return
        }
        let l = ble ?? BLELink()
        ble = l
        Task.detached {
            do {
                try l.connect(to: id)
                let t = OBDTelemetry(link: l)
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    t.outLatency = Double(self.audio?.blockFrames ?? 512)
                        / (self.audio?.renderRate ?? 32000) * 3.0
                    self.obd = t
                    self.car?.telemetry = t
                    self.source = .live
                }
                t.start()
            } catch {
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    self.errorText = error.localizedDescription
                    self.linkState = "offline"
                    self.setSource(.demo)
                }
            }
        }
        #endif
    }

    /// What the mapping will do, for a look before driving off.
    public func mapPreview() -> [(car: Double, sim: Double)] {
        guard let car else { return [] }
        return car.rpmMap.preview(engIdle: car.engine.idleRpm,
                                  engRedline: car.engine.redlineRpm)
    }
}
