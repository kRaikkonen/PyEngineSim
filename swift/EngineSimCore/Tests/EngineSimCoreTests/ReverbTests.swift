//
//  ReverbTests.swift
//
//  The reverb is used at six different points in the chain, so getting it
//  wrong would be wrong six times over.  Streamed in blocks with state
//  carried, which is the only way it is ever used.
//

import XCTest
@testable import EngineSimCore

final class ReverbTests: XCTestCase {
    struct Config: Decodable {
        let sr: Double
        let mix: Double
        let room: Double
        let feedback: Double
        let y: [Double]
    }

    struct Fixture: Decodable {
        let x: [Double]
        let configs: [String: Config]
    }

    func testMatchesPython() throws {
        let url = Bundle.module.url(forResource: "reverb", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "reverb", withExtension: "json")
        guard let url else { XCTFail("reverb.json missing"); return }
        let fx = try JSONDecoder().decode(Fixture.self,
                                          from: Data(contentsOf: url))
        for (tag, cfg) in fx.configs {
            var rev = Reverb(sampleRate: cfg.sr, mix: cfg.mix, room: cfg.room,
                             feedback: cfg.feedback, block: 256)
            var got = [Double]()
            var i = 0
            while i < fx.x.count {
                got.append(contentsOf:
                    rev.process(Array(fx.x[i..<min(i + 256, fx.x.count)])))
                i += 256
            }
            var worst = 0.0
            for k in 0..<min(got.count, cfg.y.count) {
                worst = max(worst, abs(got[k] - cfg.y[k]))
            }
            print("  reverb \(tag): worst \(worst)")
            XCTAssertLessThan(worst, 1e-12, "reverb \(tag) diverges")
        }
    }
}
