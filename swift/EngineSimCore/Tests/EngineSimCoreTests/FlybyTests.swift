//
//  FlybyTests.swift
//
//  The moving-source delay line, on its own.  It had reached the end-to-end
//  test UNVERIFIED: the listener fixture put the car 61 m from the mic and ran
//  eight blocks, so the wavefront never arrived and both sides were compared
//  as silence.  A test that passes on two empty buffers is worse than no test,
//  because it reads as coverage.
//
//  The main case here therefore runs long enough for the sound to actually
//  arrive, close, and recede -- 7,007 non-zero samples, not zero.
//

import XCTest
@testable import EngineSimCore

final class FlybyTests: XCTestCase {
    struct Case: Decodable {
        let name: String
        let delays: [Double]
        let input: [[Double]]
        let out: [[Double]]
    }

    func testMatchesPython() throws {
        let url = Bundle.module.url(forResource: "flyby", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "flyby", withExtension: "json")
        guard let url else { throw XCTSkip("flyby.json missing") }
        let cases = try JSONDecoder().decode([Case].self,
                                             from: Data(contentsOf: url))

        var worst = 0.0, worstCase = ""
        var nonZero = 0
        for c in cases {
            var dl = FlybyDelay(maxDelay: 12000)
            for (bi, d) in c.delays.enumerated() {
                let got = dl.process(c.input[bi], d)
                XCTAssertEqual(got.count, c.out[bi].count)
                for i in got.indices {
                    let e = abs(got[i] - c.out[bi][i])
                    if e > worst { worst = e; worstCase = "\(c.name) blk \(bi)" }
                    if c.out[bi][i] != 0 { nonZero += 1 }
                }
            }
        }
        print("  fly-by: \(cases.count) cases, \(nonZero) non-zero samples, "
              + "worst \(worst) (\(worstCase))")
        // if this ever reads zero the fixture has gone silent again and the
        // test below is measuring nothing
        XCTAssertGreaterThan(nonZero, 5000, "the fixture must contain SOUND")
        XCTAssertLessThan(worst, 1e-12)
    }
}
