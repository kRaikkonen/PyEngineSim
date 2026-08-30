//
//  MasterTests.swift
//
//  The last stage, and the one that decides how loud everything is.  The
//  trajectory pulls hard, lifts off and pulls again, because all three of the
//  level control's behaviours only show up when something changes: the ceiling
//  follows combustion, the rise is deliberately slower than the fall, and
//  trackside is near-frozen so the fly-by keeps its 20 dB sweep.
//
//  The output is float32, and it is compared as float32: the last thing this
//  chain does is narrow the width, and a comparison in double would hide a
//  conversion that differed.
//

import XCTest
@testable import EngineSimCore

final class MasterTests: XCTestCase {
    struct Carried: Decodable {
        let level: Double, gain: Double
        let f1_env: Double, f1_gain: Double, wob_ph: Double
        let aa_cut: Double?, lim: Double?
    }
    struct RngState: Decodable { let state: [String]; let spare: Double? }
    struct Block: Decodable {
        let rpm: Double, speed: Double, dps: Double, comb_load: Double
        let cam_lump: Double, wob_w: Double
        let state: Carried
        let sig: [Double]
        let rng: RngState
        let cache: [String: [[Double]]]
        let cache_after: [String: [[Double]]]
        let out: [Double]
        let last_level: Double
    }
    struct Case: Decodable {
        let blocks: [Block]; let pov: String
        let volume: Double; let agc: Bool
        let params: [String: Double]
    }

    func residualDB(_ got: [Float], _ want: [Double]) -> Double {
        guard got.count == want.count, !want.isEmpty else { return .infinity }
        var num = 0.0, den = 0.0
        for i in want.indices {
            let d = Double(got[i]) - want[i]
            num += d * d; den += want[i] * want[i]
        }
        if den <= 0 { return num <= 0 ? -.infinity : .infinity }
        return 10.0 * log10(max(num / den, 1e-300))
    }

    func testMasterMatchesPython() throws {
        func fixture(_ name: String) throws -> Data {
            let url = Bundle.module.url(forResource: name, withExtension: "json",
                                        subdirectory: "Fixtures")
                ?? Bundle.module.url(forResource: name, withExtension: "json")
            guard let url else { throw XCTSkip("\(name).json missing") }
            return try Data(contentsOf: url)
        }
        let refs = try JSONDecoder().decode([String: Case].self,
                                            from: fixture("master"))
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))

        var worst = -Double.infinity, worstCase = ""
        var worstLevel = 0.0
        var blocks = 0

        for (name, c) in refs.sorted(by: { $0.key < $1.key }) {
            let carKey = String(name.split(separator: "-")[0])
            guard let eng = fleet[carKey] else { XCTFail("missing \(carKey)"); continue }
            let rate = 32000.0
            let cache = FilterCache(sampleRate: rate)
            let rng = PortableRNG(seed: 1)
            let stage = MasterStage(engine: eng, sampleRate: rate, cache: cache,
                                    rng: rng, layers: LayerStack())
            stage.agcEnabled = c.agc
            stage.volume = c.volume

            for b in c.blocks {
                cache.preload(b.cache); cache.preload(b.cache_after)
                rng.restore(state: b.rng.state.compactMap { UInt64($0) },
                            spare: b.rng.spare)
                stage.level = b.state.level
                stage.gain = b.state.gain
                stage.f1Env = b.state.f1_env
                stage.f1Gain = b.state.f1_gain
                stage.wobPh = b.state.wob_ph
                stage.aaCut = b.state.aa_cut
                if let l = b.state.lim { stage.lim = l; stage.limStarted = true }
                else { stage.limStarted = false }

                var s = MasterState()
                s.rpm = b.rpm; s.speed = b.speed
                s.degPerSample = b.dps; s.combLoad = b.comb_load
                s.camLump = b.cam_lump; s.wobW = b.wob_w
                s.pov = c.pov

                let out = stage.process(b.sig, state: s, params: c.params)
                let db = residualDB(out, b.out)
                if db > worst { worst = db; worstCase = name }
                worstLevel = max(worstLevel, abs(stage.lastLevel - b.last_level))
                blocks += 1
            }
        }

        print("  master: \(refs.count) cases, \(blocks) blocks")
        print(String(format: "    output worst %7.1f dB (%@), level meter %.3e",
                     worst, worstCase, worstLevel))
        XCTAssertGreaterThan(blocks, 60)
        XCTAssertLessThan(worst, -100.0, "master drifted (\(worstCase))")
        XCTAssertLessThan(worstLevel, 1e-7, "the loudness meter drifted")
    }
}
