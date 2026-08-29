//
//  RNGTests.swift
//
//  The two implementations must produce the SAME numbers in the same order --
//  including the Box-Muller spare, which is the easy thing to get subtly wrong
//  and the reason the calls below are interleaved rather than batched.
//

import XCTest
@testable import EngineSimCore

final class RNGTests: XCTestCase {
    struct Case: Decodable {
        let u1: [Double]
        let n1: [Double]
        let u2: [Double]
        let n2: [Double]
    }

    func testMatchesPythonBitForBit() throws {
        let url = Bundle.module.url(forResource: "rng", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "rng", withExtension: "json")
        guard let url else {
            XCTFail("rng.json missing"); return
        }
        let cases = try JSONDecoder().decode([String: Case].self,
                                             from: Data(contentsOf: url))
        for (seedStr, c) in cases {
            let rng = PortableRNG(seed: UInt64(seedStr)!)
            for (i, want) in c.u1.enumerated() {
                XCTAssertEqual(rng.uniform(), want, accuracy: 0,
                               "seed \(seedStr) u1[\(i)]")
            }
            for (i, want) in c.n1.enumerated() {
                XCTAssertEqual(rng.normal(), want, accuracy: 1e-15,
                               "seed \(seedStr) n1[\(i)]")
            }
            for (i, want) in c.u2.enumerated() {
                XCTAssertEqual(rng.uniform(), want, accuracy: 0,
                               "seed \(seedStr) u2[\(i)]")
            }
            for (i, want) in c.n2.enumerated() {
                XCTAssertEqual(rng.normal(), want, accuracy: 1e-15,
                               "seed \(seedStr) n2[\(i)]")
            }
        }
    }

    func testDistribution() {
        let n = PortableRNG(seed: 1).standardNormal(200_000)
        let mean = n.reduce(0, +) / Double(n.count)
        let varc = n.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(n.count)
        XCTAssertEqual(mean, 0.0, accuracy: 0.02)
        XCTAssertEqual(varc.squareRoot(), 1.0, accuracy: 0.02)
    }
}
