//
//  ResonanceTests.swift
//
//  Every car, at idle / mid / full, because this is where a car's individual
//  voice is decided -- delays from its pipe lengths, gains from its radiation
//  loss, valve opening from how hard it is being driven.  A port that got this
//  right on an inline-4 and wrong on a rotary would sound plausible and be
//  wrong about 131 cars.
//

import XCTest
@testable import EngineSimCore

final class ResonanceTests: XCTestCase {
    struct Point: Decodable {
        let rpm: Double, throttle: Double, c: Double
        let d1: Int, d2: Int, d3: Int
        let g1: Double, g2: Double, g3: Double
        let lp_a: Double, f_helm: Double, valve: Double, flow: Double
        let post_fc: Double, sysq: Double, rt60: Double
        let rv_d: [Int], rv_g: [Double], rv_lp: Double, lp_end: Double
        let wall_q: Double
    }

    func testMatchesPythonAcrossTheFleet() throws {
        let url = Bundle.module.url(forResource: "resonance", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "resonance", withExtension: "json")
        guard let url else { XCTFail("resonance.json missing"); return }
        let refs = try JSONDecoder().decode([String: [Point]].self,
                                            from: Data(contentsOf: url))
        let presetsURL = Bundle.module.url(forResource: "presets", withExtension: "json",
                                           subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "presets", withExtension: "json")
        let fleet = try PresetLibrary.load(jsonData: Data(contentsOf: presetsURL!))

        var cars = 0, points = 0
        var worstG = 0.0, worstLP = 0.0, worstHelm = 0.0
        for (key, pts) in refs {
            guard let eng = fleet[key] else { XCTFail("missing \(key)"); continue }
            for p in pts {
                let model = ResonanceModel(engine: eng, sampleRate: 32000)
                model.wallQ = p.wall_q
                model.cSmooth = p.c            // settled, as the reference is
                let r = model.update(rpm: p.rpm, throttle: p.throttle,
                                     soundSpeed: p.c)
                XCTAssertEqual(r.d1, p.d1, "\(key) D1"); XCTAssertEqual(r.d2, p.d2, "\(key) D2")
                XCTAssertEqual(r.d3, p.d3, "\(key) D3")
                XCTAssertEqual(r.rvD, p.rv_d, "\(key) resonator delays")
                worstG = max(worstG, max(abs(r.g1 - p.g1),
                                         max(abs(r.g2 - p.g2), abs(r.g3 - p.g3))))
                worstLP = max(worstLP, max(abs(r.lpA - p.lp_a), abs(r.lpAEnd - p.lp_end)))
                worstHelm = max(worstHelm, abs(r.fHelm - p.f_helm))
                XCTAssertEqual(r.valve, p.valve, accuracy: 1e-12, "\(key) valve")
                XCTAssertEqual(r.flow, p.flow, accuracy: 1e-12, "\(key) flow")
                XCTAssertEqual(r.postFc, p.post_fc, accuracy: 1e-9, "\(key) post fc")
                XCTAssertEqual(r.sysQ, p.sysq, accuracy: 1e-12, "\(key) system Q")
                XCTAssertEqual(r.rt60, p.rt60, accuracy: 1e-12, "\(key) rt60")
                XCTAssertEqual(r.rvLP, p.rv_lp, accuracy: 1e-15, "\(key) resonator LP")
                for (i, want) in p.rv_g.enumerated() {
                    XCTAssertEqual(r.rvG[i], want, accuracy: 1e-14, "\(key) rvG[\(i)]")
                }
                points += 1
            }
            cars += 1
        }
        print("  resonance: \(cars) cars, \(points) points; worst gain \(worstG), "
              + "worst pole \(worstLP), worst Helmholtz \(worstHelm) Hz")
        XCTAssertEqual(cars, 131)
        XCTAssertLessThan(worstG, 1e-12)
        XCTAssertLessThan(worstLP, 1e-12)
        XCTAssertLessThan(worstHelm, 1e-9)
    }
}
