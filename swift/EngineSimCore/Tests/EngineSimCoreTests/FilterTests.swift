//
//  FilterTests.swift
//
//  The Swift filters are held to reference vectors captured from
//  engine_sim/audio.py -- the implementation that was actually tuned by ear.
//  Regenerate with:  py tools/make_swift_fixtures.py
//
//  This is the whole method for the port in miniature: reproduce, then PROVE
//  it, one piece at a time.  A filter that is merely "a low-pass" is not a
//  reproduction of the one the sound was built on.
//

import XCTest
@testable import EngineSimCore

final class FilterTests: XCTestCase {

    struct Fixture: Decodable {
        struct Case: Decodable {
            let kind: String
            let order: Int?
            let wn: Double?
            let btype: String?
            let f0: Double?
            let q: Double?
            let gain_db: Double?
            let rate: Double?
            let b: [Double]
            let a: [Double]
            let y: [Double]
        }
        let block: Int
        let x: [Double]
        let cases: [Case]
    }

    func loadFixture() throws -> Fixture {
        // .copy("Fixtures") puts the whole directory in the bundle, so the
        // resource lives one level down.  A missing fixture is a FAILURE, not
        // a skip: a test that silently passes because it found nothing to
        // check is worse than no test.
        let url = Bundle.module.url(forResource: "filters", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "filters", withExtension: "json")
        guard let url else {
            XCTFail("reference vectors missing -- run: py tools/make_swift_fixtures.py")
            throw NSError(domain: "EngineSimCoreTests", code: 1)
        }
        return try JSONDecoder().decode(Fixture.self,
                                        from: Data(contentsOf: url))
    }

    /// The DESIGN must match: same coefficients, not merely a similar curve.
    func testDesignsMatchPython() throws {
        let fx = try loadFixture()
        for c in fx.cases {
            let got: (b: [Double], a: [Double])
            switch c.kind {
            case "butter":
                got = FilterDesign.butter(order: c.order!, wn: c.wn!,
                                          btype: c.btype!)
            case "peaking":
                got = FilterDesign.peaking(f0: c.f0!, q: c.q!,
                                           gainDB: c.gain_db!, rate: c.rate!)
            default:
                continue
            }
            XCTAssertEqual(got.b.count, c.b.count, "\(c.kind) numerator length")
            for (i, want) in c.b.enumerated() {
                XCTAssertEqual(got.b[i], want, accuracy: 1e-12,
                               "\(c.kind) b[\(i)]")
            }
            for (i, want) in c.a.enumerated() {
                XCTAssertEqual(got.a[i], want, accuracy: 1e-12,
                               "\(c.kind) a[\(i)]")
            }
        }
    }

    /// And the RUN must match, streamed in blocks with state carried -- which
    /// is how the synth uses these.  A filter that is right on one block and
    /// drifts across the next is the classic way a port sounds "nearly right".
    func testStreamedOutputMatchesPython() throws {
        let fx = try loadFixture()
        for c in fx.cases {
            var out = [Double]()
            out.reserveCapacity(fx.x.count)
            if c.a.count <= 2 && c.b.count <= 2 {
                var f = OnePole(b: c.b, a: c.a)
                for v in fx.x { out.append(f.process(v)) }
            } else {
                var f = Biquad(b: c.b, a: c.a)
                for v in fx.x { out.append(f.process(v)) }
            }
            var worst = 0.0
            for i in 0..<min(out.count, c.y.count) {
                worst = max(worst, abs(out[i] - c.y[i]))
            }
            XCTAssertLessThan(worst, 1e-9,
                              "\(c.kind) output diverges by \(worst)")
        }
    }

    /// Cheap guard against a stability mistake: every design this chain uses
    /// must have its poles inside the unit circle.
    func testDesignsAreStable() throws {
        let fx = try loadFixture()
        for c in fx.cases where c.a.count == 3 {
            let a1 = c.a[1], a2 = c.a[2]
            // |poles| < 1 for a real biquad
            XCTAssertLessThan(abs(a2), 1.0, "pole radius, \(c.kind)")
            XCTAssertLessThan(abs(a1), 1.0 + a2, "stability triangle, \(c.kind)")
        }
    }
}
