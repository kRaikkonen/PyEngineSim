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
#if canImport(UIKit)
import UIKit
#endif

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
    /// A link that will not open, or drops, keeps the app on LIVE and is
    /// retried every few seconds instead of falling back to demo (Leo:
    /// "做一个防止连接不成功就跳过demo的设置").
    @Published public var stayLive = true
    /// The turbo machine's blow-off valve: "recirc" | "atmo" (SSQV) | "none"
    /// (it surges -- the flutter).  Back to the car's own on every car change,
    /// as the Python app's toggles are.
    @Published public var turboValve = "recirc"
    @Published public private(set) var hasTurboMachine = false
    @Published public var bladeVol = 1.0
    /// Predict the rpm from the pedal -- a mode, to compare in the car.
    @Published public var pedalPredict = false
    /// Move everyday revs up the target engine's range (0 = off).
    @Published public var lowRevLift = 0.0
    /// The volume follows rpm and load, as a real car's does.
    @Published public var realLoudness = false
    /// The BLE link's own log, newest last, for reading a failure in the car.
    @Published public var linkLog: [String] = []
    var retryTask: Task<Void, Never>?
    var lastLogPull = 0.0
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
    /// EQ, in dB.  Names as they are on a stereo, because that is what they do.
    @Published public var eqLow = 0.0
    @Published public var eqMid = 0.0
    @Published public var eqHigh = 0.0
    @Published public var eqPresence = 0.0
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
            stayLive = saved.stayLive
            bladeVol = saved.bladeVol
            pedalPredict = saved.pedalPredict
            lowRevLift = saved.lowRevLift
            realLoudness = saved.realLoudness
            carIdle = saved.carIdle
            carRedline = saved.carRedline
            learnRange = saved.learnRange
            showBay = saved.showBay
            bayTimeScale = saved.bayTimeScale
            eqLow = saved.eqLow; eqMid = saved.eqMid
            eqHigh = saved.eqHigh; eqPresence = saved.eqPresence
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
            ) { [weak self] engine, tables, voicing in
                let sy = Synthesizer(engine: engine, tables: tables, voicing: voicing,
                                     sampleRate: out.renderRate, block: out.blockFrames)
                // every synth starts with the car's own blow-off valve, and
                // the blade whistle where the listener left it
                sy.params["blade_vol"] = self?.bladeVol ?? 1.0
                sy.realLoudness = self?.realLoudness ?? false
                return sy
            }
            car = mode
            mode.rpmMap.lift = lowRevLift
            mode.start()
            turboValve = car?.synth?.turboValveDefault ?? "recirc"
            applyTurbo()
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
        if source == .live && obd == nil {
            linkState = retryTask != nil ? "retrying" : "connecting"
        }
        // a link that dropped while polling: reconnect (or demo, if asked)
        if source == .live, let o = obd, o.status == "link lost" {
            o.stop()
            obd = nil
            car.telemetry = nil
            linkFailed("the dongle dropped the link")
        }
        if now - lastLogPull > 0.5 {
            lastLogPull = now
            #if canImport(CoreBluetooth)
            if let b = ble {
                let l = b.log
                if l != linkLog { linkLog = l }
            }
            #endif
        }
    }

    // ---------------------------------------------------------------- edits
    public func selectEngine(_ key: String) {
        engineKey = key
        try? car?.setEngine(key)
        engineName = car?.engine.name ?? key
        applyLayers()          // the new synth starts with every layer visible
        applyEQ()              // ...and flat, so push the EQ back in
        if source == .demo {
            makePedal()                       // new car, new ratios and torque
            car?.telemetry = pedalSource
        }
        applyPops()                           // and a new synth needs re-arming
        applySustain()
        turboValve = car?.synth?.turboValveDefault ?? "recirc"
        applyTurbo()
        persist()
    }

    /// The turbo controls onto the (possibly new) synth.
    func applyTurbo() {
        guard let sy = car?.synth else { hasTurboMachine = false; return }
        hasTurboMachine = sy.turbo != nil
        sy.turboValve = turboValve
        sy.params["blade_vol"] = bladeVol
    }

    public func setPedalPredict(_ on: Bool) {
        pedalPredict = on
        obd?.predictFromPedal = on
        persist()
    }

    public func setRealLoudness(_ on: Bool) {
        realLoudness = on
        car?.synth?.realLoudness = on
        persist()
    }

    public func setLowRevLift(_ v: Double) {
        lowRevLift = min(max(v, 0.0), 1.0)
        car?.rpmMap.lift = lowRevLift
        objectWillChange.send()          // the mapping preview follows
        persist()
    }

    public func setTurboValve(_ v: String) {
        turboValve = v
        applyTurbo()
    }

    public func setBladeVol(_ v: Double) {
        bladeVol = v
        applyTurbo()
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
        s.stayLive = stayLive
        s.bladeVol = bladeVol
        s.pedalPredict = pedalPredict
        s.lowRevLift = lowRevLift
        s.realLoudness = realLoudness
        s.showBay = showBay
        s.bayTimeScale = bayTimeScale
        s.eqLow = eqLow; s.eqMid = eqMid
        s.eqHigh = eqHigh; s.eqPresence = eqPresence
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
        stayLive = s.stayLive
        bladeVol = s.bladeVol
        applyTurbo()
        pedalPredict = s.pedalPredict
        obd?.predictFromPedal = pedalPredict
        lowRevLift = s.lowRevLift
        car?.rpmMap.lift = lowRevLift
        realLoudness = s.realLoudness
        car?.synth?.realLoudness = realLoudness
        showBay = s.showBay
        bayTimeScale = s.bayTimeScale
        eqLow = s.eqLow; eqMid = s.eqMid
        eqHigh = s.eqHigh; eqPresence = s.eqPresence
        applyEQ()
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
        if s != .live {
            obd?.stop(); obd = nil
            retryTask?.cancel(); retryTask = nil
        }
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
        /// Named like a dongle or advertising a dongle's service.
        public var likely = false
    }

    /// Turn the engine-bay animation on or off.
    ///
    /// Off tears the view down, which is what makes off free -- the animator,
    /// the pulse field and the redraw clock all belong to the view.
    /// Push the EQ into the running chain.
    ///
    /// These are the SAME peaking filters the tuned sound already goes through,
    /// so at 0 dB the filter is skipped entirely and the output is bit-for-bit
    /// what it was before the panel existed.
    public func applyEQ() {
        guard let sy = car?.synth else { return }
        // ueq_*, not eq_*: the eq_* bells sit ahead of the limiter, which
        // claws a bass boost straight back.  These are the tone stack after it.
        sy.params["ueq_low"] = eqLow
        sy.params["ueq_mid"] = eqMid
        sy.params["ueq_high"] = eqHigh
        sy.params["ueq_presence"] = eqPresence
    }

    public func setEQ(_ band: String, _ dB: Double) {
        let v = min(max(dB, -12.0), 12.0)
        switch band {
        case "low": eqLow = v
        case "mid": eqMid = v
        case "high": eqHigh = v
        default: eqPresence = v
        }
        applyEQ()
        persist()
    }

    /// Back to the tuned sound.
    /// True when every band is at 0, i.e. the untouched tuned sound.
    public var eqIsFlat: Bool {
        abs(eqLow) < 0.05 && abs(eqMid) < 0.05
            && abs(eqHigh) < 0.05 && abs(eqPresence) < 0.05
    }

    public func resetEQ() {
        eqLow = 0; eqMid = 0; eqHigh = 0; eqPresence = 0
        applyEQ()
        persist()
    }

    public func setShowBay(_ on: Bool) {
        showBay = on
        persist()
    }

    public func setBayTimeScale(_ v: Double) {
        bayTimeScale = v
        persist()
    }

    public func setStayLive(_ on: Bool) {
        stayLive = on
        persist()
    }

    /// A link that would not open, or dropped.  Demo -- unless the listener
    /// asked to stay live: then the engine idles on LIVE and the link is tried
    /// again in three seconds, for as long as it takes.
    func linkFailed(_ why: String) {
        guard stayLive else {
            errorText = why
            linkState = "offline"
            setSource(.demo)
            return
        }
        errorText = why + " -- retrying"
        if source != .live {
            source = .live
            pedalSource = nil
            car?.telemetry = nil         // idles while it waits
            persist()
        }
        retryTask?.cancel()
        retryTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard let self, !Task.isCancelled, self.source == .live,
                  self.obd == nil else { return }
            self.connect()
        }
    }

    public func copyLinkLog() {
        #if canImport(UIKit)
        UIPasteboard.general.string = linkLog.joined(separator: "\n")
        #endif
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
        // returns at once: the wait for the radio (and, the first time, for
        // the permission prompt) happens off the main thread
        l.scan(seconds: 8.0, onUpdate: { [weak self] found in
            self?.bleFound = found.map {
                BLEDevice(id: $0.id, name: $0.name, rssi: $0.rssi,
                          likely: $0.likely)
            }
        }, onDone: { [weak self] failure in
            guard let self else { return }
            self.scanning = false
            if let failure {
                self.errorText = failure
            } else if self.bleFound.isEmpty {
                self.errorText = "No BLE device found.  Is the dongle plugged "
                    + "in with the ignition on?  If it shows up in Settings > "
                    + "Bluetooth it is a CLASSIC (SPP) adapter, and iOS cannot "
                    + "talk to those from any app -- it has to be a BLE one."
            }
        })
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
                    t.predictFromPedal = self.pedalPredict
                    self.obd = t
                    self.car?.telemetry = t
                    self.source = .live
                    self.retryTask = nil
                    self.errorText = nil
                }
                t.start()
            } catch {
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    self.linkFailed(error.localizedDescription)
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
                    t.predictFromPedal = self.pedalPredict
                    self.obd = t
                    self.car?.telemetry = t
                    self.source = .live
                    self.retryTask = nil
                    self.errorText = nil
                }
                t.start()
            } catch {
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    self.linkFailed(error.localizedDescription)
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
