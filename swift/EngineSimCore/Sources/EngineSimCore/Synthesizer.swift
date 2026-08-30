//
//  Synthesizer.swift
//  The whole chain, assembled.
//
//  Every stage in this file has already been verified against the Python one
//  at a time, so what is left to get wrong is the WIRING: which bus feeds
//  which, and -- the thing that actually bites -- what order the stages draw
//  from the shared generator in.  Move one noise-using stage past another and
//  every draw after it shifts; the spectrum barely changes and nothing looks
//  broken.  That is why the order below is not free to be tidied.
//
//  Three buses run in parallel and only meet at the listener:
//
//    exhaust  the pipe: source -> waveguides -> muffler -> tip
//    bay      block radiation, timing gears, valvetrain, injectors
//    bayi     the intake tract mouth and the atmospheric dump -- a bright
//             OPENING, which is why it is kept apart from the bay's sheet
//             metal all the way to the ear
//

import Foundation

public final class Synthesizer {
    public let engine: EnginePreset
    public let sampleRate: Double
    public let block: Int

    let cache: FilterCache
    let rng: PortableRNG
    public let layers = LayerStack()

    public let physics: EnginePhysics
    let resonance: ResonanceModel
    let pulses: PulseTrain
    let bangFizz: BangFizz
    let source: SourceStage
    let blockStage: BlockStage
    let pipes: PipeStage
    let header: HeaderStage
    let muffler: MufflerStage
    public let induction: InductionStage
    let exit: ExitStage
    public let listener: ListenerStage
    let master: MasterStage
    let setup: VoicingSetup
    /// Armed by the app.  Disarmed it draws nothing from the shared generator,
    /// so turning it on cannot shift anything downstream.
    public let pops: OverrunPops

    /// Live controls.
    public var params: [String: Double]
    /// The voicing switches, with the Python's own defaults -- note that
        /// `vacuum` is OFF: the deep-vacuum overrun is the physical one, and
        /// the arcade lift-off bark is what ships.
    public var vx: [String: Bool] = ["series_wg": true, "sys_helm": true,
                                     "rumble": true, "asym": true,
                                     "engine_series": true, "rad_hp": true,
                                     "noise": true, "bipolar": true,
                                     "vacuum": false]
    public var pov = "chase" {
        didSet { listener.pov = pov }
    }
    public var timeScale = 1.0
    public var volume = 1.0 { didSet { master.volume = volume } }

    /// Read after each block.
    public var lastLevel: Double { master.lastLevel }

    /// The taps that are not switchable layers -- the raw excitation.  Only
    /// populated when `captureTaps` is on, and only the reference tests use it.
    public var captureTaps = false
    public private(set) var debugTaps = [String: [Double]]()
    /// Drive the chain from a GIVEN operating point instead of the baked
    /// surrogate tables.  Used by the end-to-end reference test to separate a
    /// wiring failure from the surrogate's known, bounded physics offset --
    /// and by anything that already HAS the real numbers, which is why it is
    /// public rather than a test hook.
    public struct Excitation {
        public var strength: Double, load: Double, choke: Double
        public var soundSpeed: Double, cylScale: [Double]
        public init(strength: Double, load: Double, choke: Double,
                    soundSpeed: Double, cylScale: [Double]) {
            self.strength = strength; self.load = load; self.choke = choke
            self.soundSpeed = soundSpeed; self.cylScale = cylScale
        }
    }
    public var forcedExcitation: Excitation?

    public private(set) var dbgStrength = 0.0, dbgLoad = 0.0, dbgChoke = 0.0
    public private(set) var dbgDps = 0.0, dbgCRunner = 0.0, dbgValve = 0.0
    public private(set) var dbgLastLevel = 0.0
    public private(set) var dbgPipeArgs = (d1: 0, d2: 0, d3: 0, g1: 0.0, g2: 0.0,
                                           g3: 0.0, lpA: 0.0, lpEnd: 0.0,
                                           res1: 0.0, res2: 0.0)
    public internal(set) var cylScale: [Double]

    var audioCrank = 0.0
    var cold = 1.0
    var lastLevelForBlowout = 0.0

    public init(engine: EnginePreset, tables: EngineTables,
                voicing: VoicingSetup, sampleRate: Double = 32000,
                block: Int = 256, seed: UInt64 = 1) {
        self.engine = engine
        self.sampleRate = sampleRate
        self.block = block
        self.setup = voicing
        cache = FilterCache(sampleRate: sampleRate)
        rng = PortableRNG(seed: seed)
        physics = EnginePhysics(engine: engine, tables: tables)
        resonance = ResonanceModel(engine: engine, sampleRate: sampleRate,
                                   block: block)
        pulses = PulseTrain(setup: voicing, sampleRate: sampleRate)
        bangFizz = BangFizz(nchan: voicing.nchan,
                            nCylinders: engine.numCylinders,
                            sampleRate: sampleRate, cache: cache)
        source = SourceStage(sampleRate: sampleRate, nchan: voicing.nchan,
                             cache: cache, block: block)
        blockStage = BlockStage(setup: voicing, sampleRate: sampleRate,
                                cache: cache)
        pipes = PipeStage(nchan: voicing.nchan)
        header = HeaderStage(sampleRate: sampleRate, cache: cache,
                             layers: layers)
        muffler = MufflerStage(engine: engine, sampleRate: sampleRate,
                               cache: cache, rng: rng, layers: layers,
                               block: block)
        induction = InductionStage(engine: engine, sampleRate: sampleRate,
                                   cache: cache, rng: rng, block: block)
        exit = ExitStage(engine: engine, sampleRate: sampleRate, cache: cache,
                         rng: rng, layers: layers, nchan: voicing.nchan,
                         block: block)
        listener = ListenerStage(engine: engine, sampleRate: sampleRate,
                                 cache: cache, rng: rng, layers: layers,
                                 block: block)
        master = MasterStage(engine: engine, sampleRate: sampleRate,
                             cache: cache, rng: rng, layers: layers)
        pops = OverrunPops(sampleRate: sampleRate, cache: cache, rng: rng,
                           block: block)
        cylScale = [Double](repeating: 1.0, count: engine.numCylinders)
        params = voicing.params
        for (k, v) in Synthesizer.defaultParams where params[k] == nil {
            params[k] = v
        }
    }

    /// Everything the mixer exposes, with the values the Python ships.
    public static let defaultParams: [String: Double] = [
        "res1": 0.42, "res2": 0.75,      // per-car, overridden from geometry
        "pops": 0.6, "pop_muff": 0.4, "pops_reverb": 0.22,
        "master": 0.8, "intake": 0.5, "turbulence": 0.5, "src_reverb": 0.4,
        "dry": 1.0, "crack": 0.5, "body": 0.0, "drive": 0.0,
        "fire_weight": 0.5, "fire_grit": 0.5, "firing_pitch": 90.0,
        "whine": 1.0, "muffler": 1.0, "valve_open": 1.0,
        "wall_thickness": 0.5, "tail_rad": 0.35, "rad_near": 0.20,
        "shear": 0.10, "gear_grain": 1.0, "mech": 0.30,
        "eq_low": 0.0, "eq_mid": 0.0, "eq_high": 0.0, "presence": 0.0,
        "reverb": 0.2, "spatial_y": 0.5, "road_noise": 0.22,
        "super_vol": 0.5, "turbo_vol": 0.5, "gearbox_vol": 0.5,
        "gear_mesh": 0.10, "hybrid_vol": 0.5,
        "spool_reverb": 0.0, "gearbox_reverb": 0.0,
    ]

    // ---------------------------------------------------------------- state
    /// Set the operating point.  The physics derives everything else.
    public func set(rpm: Double, throttle: Double, boost: Double? = nil) {
        physics.rpm = rpm
        physics.throttle = throttle
        if let b = boost { physics.boost = b }
    }

    public var drive = DriveState()
    public var speed = 0.0

    // ---------------------------------------------------------------- render
    public func render(frames: Int) -> [Float] {
        let sr = sampleRate
        let rpm = physics.rpm
        let dps = physics.omega * 180.0 / Double.pi / sr * timeScale

        // the note stays dark until the engine has actually warmed through
        cold = min(max((70.0 - physics.coolantC) / 50.0, 0.0), 1.0)

        let cRaw = physics.exhaustSoundSpeed()
        let cRunner = forcedExcitation?.soundSpeed
            ?? max(cRaw, 300.0)
        let r = resonance.update(rpm: rpm, throttle: physics.throttle,
                                 soundSpeed: forcedExcitation.map { _ in cRunner }
                                    ?? cRaw)

        // --- excitation -------------------------------------------------
        let pOpen = physics.blowdownPressure() - 1.05 * pAtm
        let strength = forcedExcitation?.strength ?? ((pOpen < 0 ? -1.0 : 1.0)
            * abs(pOpen).squareRoot() / (6.0 * pAtm).squareRoot())
        let load = forcedExcitation?.load
            ?? min(max(abs(strength) * 1.25, 0.08), 1.0)
        // choked flow: past the critical pressure ratio the blowdown jet goes
        // sonic and the pulse top is physically clipped -- where a hard-driven
        // engine's tear and grit come from
        let pCyl = pOpen + 1.05 * pAtm
        let pr = (1.05 * pAtm) / max(pCyl, 1.05 * pAtm)
        let choke = forcedExcitation?.choke
            ?? min(max((0.54 - pr) / 0.54, 0.0), 1.0)
        // POSITIVE blowdown only: on the overrun the cylinder is in vacuum, and
        // taking the magnitude there reads as high load and brightens the hiss
        let combLoad = dps > 1e-12 ? min(max(strength * 1.25, 0.0), 1.0) : 0.0

        // each pulse is scaled by the pressure the physics captured as THAT
        // cylinder's valve opened -- so a cylinder the limiter cut goes quiet
        // by thermodynamics, not by a special case
        if let f = forcedExcitation {
            for j in 0..<min(cylScale.count, f.cylScale.count) {
                cylScale[j] = f.cylScale[j]
            }
        } else {
            let deepVacuum = vx["vacuum"] ?? false
            let lo = deepVacuum ? 0.06 : 0.30
            let ref = max(pCyl, 2.0 * pAtm)
            for j in 0..<cylScale.count {
                let k = min(j, physics.lastBlowdown.count - 1)
                cylScale[j] = pow(min(max(physics.lastBlowdown[k] / ref, lo),
                                      1.5), 0.8)
            }
        }
        let chans = pulses.render(frames: frames, rpm: rpm, degPerSample: dps,
                                  load: load, soundSpeed: cRunner,
                                  valve: r.valve, cylScale: cylScale, rng: rng)
        dbgStrength = strength; dbgLoad = load; dbgChoke = choke
        dbgDps = dps; dbgCRunner = cRunner; dbgValve = r.valve
        if captureTaps { debugTaps["pulses"] = chans[0] }
        let bf = bangFizz.process(chans: chans, strength: strength, rpm: rpm,
                                  degPerSample: dps, rng: rng)
        if captureTaps {
            debugTaps["bang"] = bf.bang[0]
            debugTaps["fizz"] = bf.fizz[0]
        }
        let src = source.process(bang: bf.bang, fizz: bf.fizz, rpm: rpm,
                                 nCylinders: engine.numCylinders,
                                 exhaustOpenness: engine.exhaustOpenness,
                                 choke: choke, d2: r.d2, params: params)
        let voiced = layers.tap(.voiced, src.voiced, bus: "src")

        // --- the block: combustion sealed behind the casting ---------------
        // The in-cylinder EVENT is the voiced thump AND its turbulent gas-rush
        // fizz -- both get muffled behind the wall mass together.  The fizz the
        // source stage already used went into the port cavity; this is the
        // whole of it, at the mixer's own weight.
        let inv = 1.0 / Double(setup.nchan)
        let turb = params["turbulence"] ?? 0.5
        var combIn = voiced
        for ci in 0..<setup.nchan {
            for i in 0..<frames { combIn[i] += turb * (bf.fizz[ci][i] * inv) }
        }
        let sealed = blockStage.process(combIn)
        let combustion = layers.tap(.block, sealed.sealed, bus: "block")
        var bay = sealed.bay
        var bayi = [Double](repeating: 0, count: frames)

        // --- the pipe network ----------------------------------------------
        // res1/res2 are NOT mixer taste: they fall out of the real pipe,
        // collector and muffler dimensions (exhaust_tmm), so each car starts at
        // its own physical resonance mix.
        dbgPipeArgs = (r.d1, r.d2, r.d3, r.g1, r.g2, r.g3, r.lpA, r.lpAEnd,
                       params["res1"] ?? 0.42, params["res2"] ?? 0.75)
        var sig = pipes.process(srcs: src.srcs, combustion: combustion,
                                d1: r.d1, d2: r.d2, d3: r.d3,
                                g1: r.g1, g2: r.g2, g3: r.g3,
                                s: -1.0, lpA: r.lpA, lpEnd: r.lpAEnd,
                                res1: params["res1"] ?? 0.42,
                                res2: params["res2"] ?? 0.75)
        for i in 0..<frames { sig[i] += src.er[i] }
        if captureTaps {
            debugTaps["wet"] = sig          // wet, er already folded in
            debugTaps["er"] = src.er
            for (ci, v) in src.srcs.enumerated() { debugTaps["src\(ci)"] = v }
        }
        // An OPEN system transmits most of the wave on the FIRST pass -- the
        // raw torn direct sound IS its voice; a chambered one blocks it and the
        // reverberant field carries the note instead.
        let direct = PipeStage.directShare(exhaustOpenness: engine.exhaustOpenness)
        for i in 0..<frames { sig[i] += direct * combustion[i] }
        sig = layers.tap(.pipes, sig)

        let hd = header.process(sig, throttle: physics.throttle, rpm: rpm,
                                idleRpm: engine.idleRpm, flow: r.flow,
                                choke: choke, cold: cold, degPerSample: dps,
                                rng: rng)
        sig = hd.head          // the header layer is tapped inside the stage

        // The bangs enter HERE, at the header, so they travel the whole pipe:
        // a stock car's get muffled by the cat and the box, an open race
        // system keeps them sharp.  Bolted on at the tailpipe every car would
        // pop identically, which is the giveaway.
        let bang = pops.render(frames: frames, rpm: rpm,
                               throttle: physics.throttle,
                               idleRpm: engine.idleRpm,
                               redlineRpm: engine.redlineRpm,
                               antiLag: engine.antiLag,
                               ignitionOn: physics.ignitionOn, params: params)
        if !bang.isEmpty {
            for i in 0..<frames { sig[i] += bang[i] }
        }

        // --- port to tailpipe -----------------------------------------------
        var ps = PipeState()
        ps.rpm = rpm; ps.throttle = physics.throttle; ps.boost = physics.boost
        ps.choke = choke; ps.cold = cold; ps.soundSpeed = cRunner
        ps.lastLevel = lastLevelForBlowout
        dbgLastLevel = lastLevelForBlowout
        sig = muffler.process(sig, r: r, state: ps, params: params, vx: vx)

        // --- the bay bus: intake, trumpets, spool, gearbox -------------------
        var ist = InductionState()
        ist.rpm = rpm; ist.throttle = physics.throttle; ist.boost = physics.boost
        ist.degPerSample = dps; ist.drive = drive
        let ind = induction.process(frames: frames, state: ist, params: params)
        // hidden as a layer means "not fitted", not "passed through": the bay is
        // ADDED to the mix, it is not a filter in series
        let bayAdd = layers.gate(.inductionGears, ind.bay)
        for i in 0..<frames { bayi[i] += bayAdd[i] }
        // the driver has just LIFTED, so the note collapses and the valve event
        // is what is actually heard
        if ind.duck > 1e-6 {
            let k = 1.0 - ind.duck
            for i in 0..<frames { sig[i] *= k }
        }

        // --- the tip ----------------------------------------------------------
        var es = ExitState()
        es.rpm = rpm; es.throttle = physics.throttle
        es.soundSpeed = cRunner; es.degPerSample = dps
        es.combLoad = combLoad; es.pov = pov
        // ONE crank, owned by the pulse train.  Keeping a second copy here
        // that advances by the same rule is an invitation to drift: they only
        // stay equal for as long as nobody adds a branch to one of them.
        audioCrank = pulses.audioCrank
        es.crank = audioCrank
        let ex = exit.process(sig, r: r, state: es, params: params, vx: vx)
        sig = ex.out
        for i in 0..<frames { bay[i] += ex.bay[i] }

        // --- the listener ------------------------------------------------------
        var ls = ListenerState()
        ls.rpm = rpm; ls.speed = speed; ls.degPerSample = dps
        ls.crank = audioCrank; ls.combLoad = combLoad; ls.injAmt = r.injAmt
        if captureTaps { debugTaps["bay"] = bay; debugTaps["bayi"] = bayi }
        sig = listener.process(sig, bay: bay, bayi: bayi, state: ls,
                               params: params)

        // --- master -------------------------------------------------------------
        var ms = MasterState()
        ms.rpm = rpm; ms.speed = speed; ms.degPerSample = dps
        ms.combLoad = combLoad
        ms.camLump = r.camLump + r.balanceRough
        ms.wobW = r.wobW; ms.pov = pov
        let out = master.process(sig, state: ms, params: params)
        lastLevelForBlowout = master.lastLevel
        _ = combustion; _ = voiced           // taps already recorded them
        return out
    }

}

let pAtm = 101325.0
