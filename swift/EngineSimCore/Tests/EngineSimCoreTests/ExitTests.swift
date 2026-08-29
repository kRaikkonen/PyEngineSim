//
//  ExitTests.swift
//
//  Nine cases.  All three listener ranges on one car, because the radiation
//  stage blends a near-field piston term against a far-field derivative by
//  RANGE -- get that backwards and every POV still sounds like a car, just
//  the wrong one.  Both megaphone cars, since only two in the fleet have a
//  cone.  A rotary, a diesel truck with a long system, a big NA V12 where the
//  thunder and rumble run at full strength, and the A3 in the seat it will
//  actually be heard from.
//

import XCTest
@testable import EngineSimCore

final class ExitTests: XCTestCase {
    struct Res: Decodable {
        let rt60: Double, rv_lp: Double, sysq: Double, flow: Double
    }
    struct RngState: Decodable { let state: [String]; let spare: Double? }
    struct Block: Decodable {
        let rpm: Double, throttle: Double, c_runner: Double, dps: Double
        let crank: Double, comb_load: Double, rad_prev: Double
        let gear_phase: Double, u_abs: Double
        let input: [Double]
        let resonance: Res
        let rng: RngState
        let cache: [String: [[Double]]]
        let cache_after: [String: [[Double]]]
        let taps: [String: [Double]]
    }
    struct Case: Decodable {
        let blocks: [Block]; let pov: String; let nchan: Int
        let params: [String: Double]
    }

    func residualDB(_ got: [Double], _ want: [Double]) -> Double {
        guard got.count == want.count, !want.isEmpty else { return .infinity }
        var num = 0.0, den = 0.0
        for i in want.indices { let d = got[i] - want[i]; num += d * d; den += want[i] * want[i] }
        if den <= 0 { return num <= 0 ? -.infinity : .infinity }
        return 10.0 * log10(max(num / den, 1e-300))
    }

    func testExitRunMatchesPython() throws {
        func fixture(_ name: String) throws -> Data {
            let url = Bundle.module.url(forResource: name, withExtension: "json",
                                        subdirectory: "Fixtures")
                ?? Bundle.module.url(forResource: name, withExtension: "json")
            guard let url else { throw XCTSkip("\(name).json missing") }
            return try Data(contentsOf: url)
        }
        let refs = try JSONDecoder().decode([String: Case].self,
                                            from: fixture("exit"))
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))

        let checked = ["wall de-honk", "metal ring", "megaphone", "thunder",
                       "reflection", "radiation", "tailpipe exit"]
        let stageOf: [String: Stage] = [
            "wall de-honk": .wallDeHonk, "metal ring": .metalRing,
            "megaphone": .megaphone, "thunder": .thunder,
            "reflection": .reflection, "radiation": .radiation,
            "tailpipe exit": .tailpipeExit,
        ]
        var worst = [String: Double](), worstCase = [String: String]()
        var blocks = 0

        for (name, c) in refs.sorted(by: { $0.key < $1.key }) {
            let carKey = String(name.split(separator: "-")[0])
            guard let eng = fleet[carKey] else { XCTFail("missing \(carKey)"); continue }
            let rate = 32000.0
            let cache = FilterCache(sampleRate: rate)
            let rng = PortableRNG(seed: 1)
            let layers = LayerStack()
            let stage = ExitStage(engine: eng, sampleRate: rate, cache: cache,
                                  rng: rng, layers: layers, nchan: c.nchan)

            for b in c.blocks {
                // the signal AS IT ENTERED this run -- the induction section
                // sits between the valve-bypass tap and here
                let input = b.input
                cache.preload(b.cache); cache.preload(b.cache_after)
                rng.restore(state: b.rng.state.compactMap { UInt64($0) },
                            spare: b.rng.spare)
                stage.radPrev = b.rad_prev
                stage.gearPhase = b.gear_phase

                var r = Resonance()
                r.rt60 = b.resonance.rt60
                r.rvLP = b.resonance.rv_lp
                r.sysQ = b.resonance.sysq
                r.flow = b.resonance.flow

                var s = ExitState()
                s.rpm = b.rpm; s.throttle = b.throttle
                s.soundSpeed = b.c_runner
                s.degPerSample = b.dps
                s.crank = b.crank
                s.combLoad = b.comb_load
                s.pov = c.pov

                // the gear grain draws from the RNG between the thunder and
                // reflection taps, so it has to run in that order or every
                // draw after it is shifted
                let out = stage.process(input, r: r, state: s, params: c.params).out

                for nm in checked {
                    guard let want = b.taps[nm] else { continue }
                    // read the layer stack's record of what left that stage
                    let mine = layers.lastValue(stageOf[nm]!) ?? out
                    let db = residualDB(mine, want)
                    if db > (worst[nm] ?? -.infinity) { worst[nm] = db; worstCase[nm] = name }
                }
                if let want = b.taps["tailpipe exit"] {
                    XCTAssertEqual(out.count, want.count)
                }
                if b.u_abs > 0 {
                    XCTAssertEqual(stage.exitVelocity, b.u_abs, accuracy: 1e-9,
                                   "\(name) exit velocity")
                }
                blocks += 1
            }
        }

        print("  exit run: \(refs.count) cases, \(blocks) blocks")
        for nm in checked {
            print(String(format: "    %-14s worst %7.1f dB  (%@)",
                         (nm as NSString).utf8String!, worst[nm] ?? .infinity,
                         worstCase[nm] ?? "-"))
        }
        XCTAssertGreaterThan(blocks, 40)
        for nm in checked {
            XCTAssertLessThan(worst[nm] ?? .infinity, -100.0,
                              "\(nm) drifted (\(worstCase[nm] ?? "?"))")
        }
    }
}
