//
//  WaveguideTests.swift
//
//  Includes a delay SHORTER than the block (D = 14), because that is the case
//  that exercises the segmented recursion -- and the one where a port that
//  quietly processes the whole block at once would still look right on a long
//  pipe and be wrong on a short one.
//

import XCTest
@testable import EngineSimCore

final class WaveguideTests: XCTestCase {
    struct Config: Decodable {
        let D: Int
        let g: Double
        let s: Double
        let lp_a: Double
        let y: [Double]
    }
    struct Fixture: Decodable {
        let x: [Double]
        let configs: [String: Config]
    }

    func testMatchesPython() throws {
        let url = Bundle.module.url(forResource: "waveguide", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "waveguide", withExtension: "json")
        guard let url else { XCTFail("waveguide.json missing"); return }
        let fx = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))
        for (tag, cfg) in fx.configs {
            let wg = ExhaustWaveguide(maxDelay: 2600)
            var got = [Double]()
            var i = 0
            while i < fx.x.count {
                got.append(contentsOf: wg.process(
                    Array(fx.x[i..<min(i + 256, fx.x.count)]),
                    D: cfg.D, g: cfg.g, s: cfg.s, lpA: cfg.lp_a))
                i += 256
            }
            var worst = 0.0
            for k in 0..<min(got.count, cfg.y.count) {
                worst = max(worst, abs(got[k] - cfg.y[k]))
            }
            print("  waveguide \(tag) (D=\(cfg.D)): worst \(worst)")
            XCTAssertLessThan(worst, 1e-11, "waveguide \(tag) diverges")
        }
    }
}
