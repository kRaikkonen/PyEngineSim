//
//  TurboAudioTests.swift
//
//  The turbo layer against Synthesizer._turbo_audio
//  (tools/export_turbo_audio_ref.py -> Fixtures/turbo_audio.json): a pull, a
//  LIFT and part throttle on five cars -- a recirculating valve (RS3, the A3's
//  EA211), no valve on a pod (the 2JZ single: the flutter), no valve through
//  an airbox on a twin (F40), and a twincharge on a pod (Delta S4: its
//  blower's whine, anti-lag, the flutter).
//
//  The generator and the filter cache are restored ONCE, where the run starts;
//  from there the machine, its audio-rate twin, every filter, the envelopes
//  and the fired-gain hold run on their own for 150 blocks, so anything that
//  drifts -- a phase, a state carried wrong across a redesign, a surge cycle
//  that resolves a sample late -- accumulates and shows.
//

import XCTest
@testable import EngineSimCore

final class TurboAudioTests: XCTestCase {
    struct Start: Decodable {
        struct Rng: Decodable { let state: [String]; let spare: Double? }
        let rng: Rng
        let cache: [String: [[Double]]]
    }
    struct Block: Decodable {
        let `in`: [Double]
        let out: [Double]
        let bov_env: Double
        let busy: Int
    }
    struct Case: Decodable {
        let spec: TurboSpec
        let coolant_c: Double
        let ic_soak: Double
        let pre: [[Double]]
        let start: Start
        let params: [String: Double]
        let flutter: Bool
        let ssqv: Bool
        let pod_filter: Bool
        let blocks: [Block]
    }

    func fixture() throws -> [String: Case] {
        let url = Bundle.module.url(forResource: "turbo_audio", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "turbo_audio", withExtension: "json")
        guard let url else { throw XCTSkip("turbo_audio.json missing") }
        return try JSONDecoder().decode([String: Case].self, from: Data(contentsOf: url))
    }

    func shipped(_ name: String) throws -> Data {
        let docs = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("docs")
        let url = docs.appendingPathComponent("\(name).json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw XCTSkip("\(url.path) is not here (package checked out alone)")
        }
        return try Data(contentsOf: url)
    }

    func residualDB(_ got: [Double], _ want: [Double]) -> Double {
        var num = 0.0, den = 0.0
        for (g, w) in zip(got, want) { num += (g - w) * (g - w); den += w * w }
        if den == 0 { return num == 0 ? -300 : 0 }
        return 10.0 * log10(max(num / den, 1e-300))
    }

    /// The app's path: a Synthesizer on the SHIPPED data builds the machine,
    /// steps it every block, and a lift with no valve makes it surge.
    func testTheSynthRunsTheMachine() throws {
        let lib = try EngineLibrary(presets: try shipped("presets"),
                                    tables: try shipped("engine_tables"),
                                    voicing: try shipped("engine_voicing"))
        for key in ["a3", "2jzst", "veyron"] {
            guard let eng = lib.engine(key), let tab = lib.tables(key),
                  let voi = lib.voicing(key) else { XCTFail("\(key)"); continue }
            let sy = Synthesizer(engine: eng, tables: tab, voicing: voi,
                                 sampleRate: 44100, block: 512, seed: 1)
            XCTAssertNotNil(sy.turbo, "\(key): no machine")
            guard let ts = sy.turbo else { continue }
            XCTAssertEqual(sy.turboValve, sy.turboValveDefault)
            let blocks = Int(44100.0 / 512.0 * 3.0)
            var peak = 0.0, surged = false
            let t0 = Date()
            for k in 0..<(2 * blocks) {
                let pull = k < blocks
                let x = Double(k % blocks) / Double(blocks)
                sy.set(rpm: (pull ? 0.35 + 0.55 * x : 0.9 - 0.3 * x) * eng.redlineRpm,
                       throttle: pull ? 1.0 : 0.0)
                let out = sy.render(frames: 512)
                for v in out { XCTAssertTrue(v.isFinite); if !v.isFinite { break } }
                if pull { peak = max(peak, sy.physics.boost) }
                if !pull && ts.units.contains(where: { $0.wC < 0 }) { surged = true }
            }
            let rt = Date().timeIntervalSince(t0) / 6.0
            print(String(format: "  synth %-7@ peak boost %.2f bar (spec %.2f), surged %@, %.2f x real time",
                         key, peak, eng.boostBar, surged ? "yes" : "no", rt))
            XCTAssertGreaterThan(peak, 0.5 * eng.boostBar, "\(key): never spooled")
            XCTAssertEqual(surged, ts.air.bovMode == "none", "\(key): surge iff no valve")
            #if !DEBUG      // speed means nothing unoptimised (PerformanceTests)
            XCTAssertLessThan(rt, 0.25, "\(key): too slow for the phone")
            #endif
        }
    }

    func testTheTurboLayerMatchesThePython() throws {
        let cases = try fixture()
        let presets = try PresetLibrary.load(jsonData: try shipped("presets"))
        let tables = try EngineTables.load(jsonData: try shipped("engine_tables"))
        let sr = 32000.0, frames = 256
        for (key, c) in cases.sorted(by: { $0.key < $1.key }) {
            guard let eng = presets[key], let tab = tables[key] else {
                XCTFail("\(key): not shipped"); continue
            }
            let hooks = EngineBreathing(engine: eng, tables: tab, spec: c.spec)
            hooks.coolantC = c.coolant_c
            hooks.icSoak = c.ic_soak
            let ts = TurboSystem(spec: c.spec, hooks: hooks)
            for p in c.pre { ts.step(dt: 0.01, rpm: p[0], thr: p[1], mguhW: p[2]) }
            let cache = FilterCache(sampleRate: sr)
            cache.preload(c.start.cache)
            let rng = PortableRNG(seed: 1)
            rng.restore(state: c.start.rng.state.compactMap { UInt64($0) },
                        spare: c.start.rng.spare)
            let st = TurboStage(engine: eng, ve: tab.veTable, turbo: ts, sampleRate: sr,
                                cache: cache, rng: rng)
            XCTAssertEqual(st.flutter, c.flutter, "\(key): the valve default")
            XCTAssertEqual(st.ssqv, c.ssqv, "\(key): the valve default")
            XCTAssertEqual(st.podFilter, c.pod_filter, "\(key): the inlet")
            var got = [Double](), want = [Double]()
            var worstBlock = -300.0, at = 0, envErr = 0.0
            for (k, b) in c.blocks.enumerated() {
                let i = b.in
                ts.step(dt: Double(frames) / sr, rpm: i[0], thr: i[1], fuelCut: i[2] > 0.5,
                        mguhW: i[3])
                var inp = TurboBlockInput()
                inp.rpm = i[0]
                inp.gain = i[4]
                inp.combLoad = i[5]
                inp.mapPa = i[6]
                inp.turboVol = c.params["turbo_vol"] ?? 0.4
                inp.bladeVol = c.params["blade_vol"] ?? 1.0
                inp.superVol = c.params["super_vol"] ?? 0.6
                let out = st.process(frames: frames, input: inp)
                XCTAssertEqual(st.tw.busy, b.busy, "\(key) block \(k): twin busy")
                let r = residualDB(out, b.out)
                if r > worstBlock { worstBlock = r; at = k }
                envErr = max(envErr, abs(st.bovEnv - b.bov_env))
                got += out
                want += b.out
            }
            let whole = residualDB(got, want)
            print(String(format: "  turbo audio %-8@ whole %.1f dB, worst block %.1f dB (#%d), env %.1e",
                         key, whole, worstBlock, at, envErr))
            XCTAssertLessThan(whole, -100.0, "\(key): the turbo layer drifts from the Python")
            XCTAssertLessThan(worstBlock, -90.0, "\(key): block \(at) is off")
            // (the Delta S4's anti-lag machine drifts ~3e-7 from the Python's)
            XCTAssertLessThan(envErr, 1e-8, "\(key): the lift envelope")
        }
    }
}
