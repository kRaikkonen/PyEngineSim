//
//  InductionTests.swift
//
//  The trajectory in the fixture pulls hard and then snaps the throttle shut,
//  so the lift-off branch actually fires -- on the turbo cars the valve is
//  active in 7 of the 12 blocks.  Testing a blow-off model on a trace where it
//  never opens would prove nothing about the loudest thing it does.
//
//  All three valve hardwares are covered on the same car, because they differ
//  ONLY in orifice geometry: recirc, atmospheric SSQV, and no valve at all
//  (compressor surge).  If the geometry were wrong the three would collapse
//  toward each other and still look plausible one at a time.
//

import XCTest
@testable import EngineSimCore

final class InductionTests: XCTestCase {
    struct Drive: Decodable {
        let gear: Int, num_gears: Int, clutch: Double, speed: Double
        let wheel_radius: Double, final_drive: Double, gas_torque: Double
    }
    struct RngState: Decodable { let state: [String]; let spare: Double? }
    struct Block: Decodable {
        let rpm: Double, throttle: Double, boost: Double
        let thr_ref: Double, bov_env: Double, bov_pr0: Double
        let seq_prev: Double, seq_surge: Double, flutter_phase: Double
        let bov_prev: Double
        let phases: [String: Double]
        let rng: RngState
        let cache: [String: [[Double]]]
        let cache_after: [String: [[Double]]]
        let drive: Drive
        let ind: [Double]
        let gw: [Double]
        let bov_env_after: Double
    }
    struct Case: Decodable {
        let blocks: [Block]; let params: [String: Double]
        let ssqv: Bool; let flutter: Bool
    }

    func residualDB(_ got: [Double], _ want: [Double]) -> Double {
        guard got.count == want.count, !want.isEmpty else { return .infinity }
        var num = 0.0, den = 0.0
        for i in want.indices { let d = got[i] - want[i]; num += d * d; den += want[i] * want[i] }
        if den <= 0 { return num <= 0 ? -.infinity : .infinity }
        return 10.0 * log10(max(num / den, 1e-300))
    }

    func testBayBusMatchesPython() throws {
        func fixture(_ name: String) throws -> Data {
            let url = Bundle.module.url(forResource: name, withExtension: "json",
                                        subdirectory: "Fixtures")
                ?? Bundle.module.url(forResource: name, withExtension: "json")
            guard let url else { throw XCTSkip("\(name).json missing") }
            return try Data(contentsOf: url)
        }
        let refs = try JSONDecoder().decode([String: Case].self,
                                            from: fixture("induction"))
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))

        var worstInd = -Double.infinity, worstGw = -Double.infinity
        var worstIndCase = "", worstGwCase = ""
        var blocks = 0, valveBlocks = 0

        for (name, c) in refs.sorted(by: { $0.key < $1.key }) {
            let carKey = String(name.split(separator: "-")[0])
            guard let eng = fleet[carKey] else { XCTFail("missing \(carKey)"); continue }
            let rate = 32000.0
            let cache = FilterCache(sampleRate: rate)
            let rng = PortableRNG(seed: 1)
            let stage = InductionStage(engine: eng, sampleRate: rate,
                                       cache: cache, rng: rng)
            stage.ssqv = c.ssqv
            stage.flutter = c.flutter

            for b in c.blocks {
                cache.preload(b.cache); cache.preload(b.cache_after)
                rng.restore(state: b.rng.state.compactMap { UInt64($0) },
                            spare: b.rng.spare)
                // restore every oscillator phase and every valve latch, so the
                // block starts exactly where the Python's did
                for (k, v) in b.phases { stage.phases[k] = v }
                stage.thrRef = b.thr_ref
                stage.bovEnv = b.bov_env
                stage.bovPr0 = b.bov_pr0
                stage.seqPrev = b.seq_prev
                stage.seqSurge = b.seq_surge
                stage.flutterPhase = b.flutter_phase
                stage.bovPrev = b.bov_prev

                var s = InductionState()
                s.rpm = b.rpm; s.throttle = b.throttle; s.boost = b.boost
                s.degPerSample = 1.0
                s.drive.gear = b.drive.gear
                s.drive.numGears = b.drive.num_gears
                s.drive.clutch = b.drive.clutch
                s.drive.speed = b.drive.speed
                s.drive.wheelRadius = b.drive.wheel_radius
                s.drive.finalDrive = b.drive.final_drive
                s.drive.gasTorque = b.drive.gas_torque

                // The Python computes the gear mesh FIRST, inside the same
                // method; here it is a separate call.  Neither touches the RNG
                // or the cache, so the order cannot change either result.
                var gw = stage.gearboxAudio(frames: 256, state: s, params: c.params)
                var ind = stage.inductionAudio(frames: 256, state: s,
                                               params: c.params)
                stage.wallFilter(&ind, &gw, params: c.params)

                let dI = residualDB(ind, b.ind), dG = residualDB(gw, b.gw)
                if dI > worstInd { worstInd = dI; worstIndCase = name }
                if dG > worstGw { worstGw = dG; worstGwCase = name }
                XCTAssertEqual(stage.bovEnv, b.bov_env_after, accuracy: 1e-12,
                               "\(name) blow-off envelope")
                if b.bov_env_after > 1e-3 { valveBlocks += 1 }
                blocks += 1
            }
        }

        print("  bay bus: \(refs.count) cases, \(blocks) blocks, "
              + "\(valveBlocks) with the valve open")
        print(String(format: "    spool/blow-off worst %7.1f dB (%@)",
                     worstInd, worstIndCase))
        print(String(format: "    gearbox        worst %7.1f dB (%@)",
                     worstGw, worstGwCase))
        XCTAssertGreaterThan(valveBlocks, 20, "the blow-off never fired")
        XCTAssertLessThan(worstInd, -100.0, "spool drifted (\(worstIndCase))")
        XCTAssertLessThan(worstGw, -100.0, "gearbox drifted (\(worstGwCase))")
    }
}
