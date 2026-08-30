//
//  MufflerTests.swift
//
//  Twelve cars chosen so that no branch of the run goes unexercised: a turbo
//  with an internal gate and one with an external screamer pipe, a rotary, a
//  quad-turbo, a hot-V, a GPF car with a flex pipe, an absorptive box, a
//  two-valve blower V8, the highest-revving thing in the fleet, and a diesel
//  truck whose exhaust is long enough for air absorption to matter.
//
//  Each block restores the filter cache and the RNG to what the Python had at
//  that point, because both are history-dependent -- see ReferenceSupport.
//

import XCTest
@testable import EngineSimCore

final class MufflerTests: XCTestCase {
    struct Res: Decodable {
        let d1: Int, d2: Int, d3: Int
        let g1: Double, g2: Double, g3: Double
        let lp_a: Double, f_helm: Double, valve: Double, flow: Double
        let post_fc: Double, sysq: Double, rt60: Double
        let rv_d: [Int], rv_g: [Double], rv_lp: Double, lp_end: Double
    }
    struct RngState: Decodable { let state: [String]; let spare: Double? }
    struct Block: Decodable {
        let rpm: Double, throttle: Double, boost: Double, choke: Double
        let cold: Double, c_smooth: Double, c_runner: Double
        let last_level: Double
        let resonance: Res
        let rng: RngState
        let cache: [String: [[Double]]]
        let cache_after: [String: [[Double]]]
        let taps: [String: [Double]]
    }
    struct Car: Decodable { let blocks: [Block]; let params: [String: Double] }

    /// dB of the residual relative to the reference -- the honest measure when
    /// the numbers are audio and not just numbers.
    func residualDB(_ got: [Double], _ want: [Double]) -> Double {
        guard got.count == want.count, !want.isEmpty else { return .infinity }
        var num = 0.0, den = 0.0
        for i in want.indices { let d = got[i] - want[i]; num += d * d; den += want[i] * want[i] }
        if den <= 0 { return num <= 0 ? -.infinity : .infinity }
        return 10.0 * log10(max(num / den, 1e-300))
    }

    func testRunMatchesPython() throws {
        func fixture(_ name: String) throws -> Data {
            let url = Bundle.module.url(forResource: name, withExtension: "json",
                                        subdirectory: "Fixtures")
                ?? Bundle.module.url(forResource: name, withExtension: "json")
            guard let url else { throw XCTSkip("\(name).json missing") }
            return try Data(contentsOf: url)
        }
        let refs = try JSONDecoder().decode([String: Car].self,
                                            from: fixture("muffler"))
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))

        // "head/port" first, so a turbine or wastegate error is caught where it
        // happens instead of surfacing four stages later as a small residual
        let checked = ["head/port", "catalytic", "standing-wave", "resonator",
                       "muffler", "valve bypass"]
        var worst = [String: Double]()
        var worstCar = [String: String]()
        var blocksChecked = 0

        for (key, car) in refs.sorted(by: { $0.key < $1.key }) {
            guard let eng = fleet[key] else { XCTFail("missing preset \(key)"); continue }
            let rate = 32000.0
            let cache = FilterCache(sampleRate: rate)
            let rng = PortableRNG(seed: 1)
            let layers = LayerStack()
            let stage = MufflerStage(engine: eng, sampleRate: rate, cache: cache,
                                     rng: rng, layers: layers)

            for b in car.blocks {
                guard let input = b.taps["head/port-in"] else { continue }
                // put the two history-carrying things back where they were
                cache.preload(b.cache)
                cache.preload(b.cache_after)     // designs made earlier in THIS block
                rng.restore(state: b.rng.state.compactMap { UInt64($0) },
                            spare: b.rng.spare)

                var r = Resonance()
                let rr = b.resonance
                r.d1 = rr.d1; r.d2 = rr.d2; r.d3 = rr.d3
                r.g1 = rr.g1; r.g2 = rr.g2; r.g3 = rr.g3
                r.lpA = rr.lp_a; r.lpAEnd = rr.lp_end; r.fHelm = rr.f_helm
                r.valve = rr.valve; r.flow = rr.flow; r.postFc = rr.post_fc
                r.sysQ = rr.sysq; r.rt60 = rr.rt60
                r.rvD = rr.rv_d; r.rvG = rr.rv_g; r.rvLP = rr.rv_lp

                var st = PipeState()
                st.rpm = b.rpm; st.throttle = b.throttle; st.boost = b.boost
                st.choke = b.choke; st.cold = b.cold
                st.soundSpeed = b.c_runner       // the LIVE speed, not the smoothed
                st.lastLevel = b.last_level

            let stageOf: [String: Stage] = [
                    "head/port": .headPort, "catalytic": .catalytic,
                    "standing-wave": .standingWave, "resonator": .resonator,
                    "muffler": .muffler, "valve bypass": .valveBypass,
                ]
                _ = stage.process(input, r: r, state: st, params: car.params)
                for name in checked {
                    let refName = name == "head/port" ? "head/port-out" : name
                    guard let want = b.taps[refName],
                          let mine = layers.lastValue(stageOf[name]!)
                    else { continue }
                    let db = residualDB(mine, want)
                    if db > (worst[name] ?? -.infinity) {
                        worst[name] = db; worstCar[name] = key
                    }
                }
                blocksChecked += 1
            }
        }

        print("  muffler run: \(refs.count) cars, \(blocksChecked) blocks")
        for name in checked {
            let db = worst[name] ?? .infinity
            print(String(format: "    %-14@ worst residual %7.1f dB  (%@)",
                         name, db,
                         worstCar[name] ?? "-"))
        }
        XCTAssertGreaterThan(blocksChecked, 50)
        for name in checked {
            XCTAssertLessThan(worst[name] ?? .infinity, -100.0,
                              "\(name) drifted (worst car \(worstCar[name] ?? "?"))")
        }
    }
}
