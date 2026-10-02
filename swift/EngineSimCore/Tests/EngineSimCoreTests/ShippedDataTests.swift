//
//  ShippedDataTests.swift
//
//  The four JSON files the APP bundles (docs/presets.json, engine_tables.json,
//  engine_voicing.json, engine_torque.json -- make_xcodeproj.py references
//  them in place) are re-exported from the Python whenever its cars change.
//  Every other test here runs on frozen copies in Fixtures/, so a fresh export
//  that this decoder cannot read, a car missing from one of the four, or a
//  setting that drives the chain to NaN would reach the phone untested.  This
//  reads the shipping files themselves and runs every car through the chain.
//

import XCTest
@testable import EngineSimCore

final class ShippedDataTests: XCTestCase {

    /// swift/EngineSimCore/Tests/EngineSimCoreTests/ -> the repo's docs/
    func shipped(_ name: String) throws -> Data {
        let docs = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("docs")
        let url = docs.appendingPathComponent("\(name).json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw XCTSkip("\(url.path) is not here (package checked out alone)")
        }
        return try Data(contentsOf: url)
    }

    func testEveryShippedCarLoadsAndRenders() throws {
        let lib = try EngineLibrary(presets: try shipped("presets"),
                                    tables: try shipped("engine_tables"),
                                    voicing: try shipped("engine_voicing"),
                                    torque: try shipped("engine_torque"))
        let fleet = try PresetLibrary.load(jsonData: try shipped("presets"))
        XCTAssertGreaterThanOrEqual(lib.keys.count, 134, "the whole fleet")
        var rendered = 0
        for key in lib.keys {
            guard let eng = fleet[key] else { XCTFail("\(key): no preset"); continue }
            guard let tab = lib.tables(key) else { XCTFail("\(key): no tables"); continue }
            guard let voi = lib.voicing(key) else { XCTFail("\(key): no voicing"); continue }
            guard let tq = lib.torque(key) else { XCTFail("\(key): no torque table"); continue }
            XCTAssertGreaterThan(tq.drag_cda ?? 0, 0.1, "\(key): drag area")
            let syn = Synthesizer(engine: eng, tables: tab, voicing: voi,
                                  sampleRate: 32000, block: 256, seed: 1)
            // a quarter of a second at 70 % of the redline, wide open
            syn.set(rpm: 0.7 * eng.redlineRpm, throttle: 1.0, boost: 0.0)
            var peak: Float = 0
            for _ in 0..<32 {
                let out = syn.render(frames: 256)
                for x in out {
                    XCTAssertTrue(x.isFinite, "\(key): non-finite sample")
                    if !x.isFinite { break }
                    peak = max(peak, abs(x))
                }
            }
            XCTAssertGreaterThan(peak, 0, "\(key): silent")
            rendered += 1
        }
        XCTAssertEqual(rendered, lib.keys.count, "every car rendered")
    }
}
