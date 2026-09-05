//
//  BaySnapshotTests.swift
//  Render the engine bay to PNGs so it can actually be LOOKED at.
//
//  Every visual defect in this drawing so far -- combustion flashing on the
//  compression stroke, a V laid out sideways, pipes routed over the block, the
//  whole thing reading as a wiring diagram -- was invisible to whoever wrote it
//  and had to be reported by someone holding a phone.  That is a bad loop.  A
//  BayScene holds no view, no audio and no clock, so it can be handed a preset
//  and a crank angle and rendered straight to a file.
//
//  It is a "test" only because that is the cheapest way to get a build target
//  with the fixtures attached.  It asserts almost nothing; the point is the
//  images, which land in $BAY_SNAPSHOT_DIR (default /tmp/baysnaps) and are only
//  written when that variable is set, so a normal test run does no extra work.
//

#if canImport(AppKit)
import XCTest
import SwiftUI
import AppKit
@testable import EngineSimCore
@testable import SwiftEngineSimUI

final class BaySnapshotTests: XCTestCase {

    func library() throws -> EngineLibrary {
        func data(_ n: String) throws -> Data {
            let url = Bundle.module.url(forResource: n, withExtension: "json",
                                        subdirectory: "Fixtures")
                ?? Bundle.module.url(forResource: n, withExtension: "json")
            guard let url else { throw XCTSkip("\(n).json missing") }
            return try Data(contentsOf: url)
        }
        return try EngineLibrary(presets: data("presets"),
                                 tables: data("engine_tables"),
                                 voicing: data("engine_voicing"))
    }

    /// Spin the engine up to `crank` so the pulses are mid-flight, then draw.
    func scene(_ e: EnginePreset, crank: Double, rpm: Double,
               boost: Double) -> BayScene {
        let bay = EngineBay(engine: e)
        var pulses = ExhaustPulseField(bay: bay)
        // walk the crank there in small steps so pulses exist and have moved
        var c = 0.0
        let step = 4.0
        while c < crank {
            c += step
            pulses.update(bay: bay, crankAngleDeg: c, dt: step / (rpm * 6.0),
                          soundSpeed: 480, load: 0.9, rpm: rpm)
        }
        return BayScene(bay: bay, crankDeg: crank, pulses: pulses, rpm: rpm,
                        boostBar: boost, load: 0.9, warmth: 0.7)
    }

    // ImageRenderer is @MainActor, so the whole render has to run there.
    @MainActor
    func testWriteSnapshots() throws {
        let env = ProcessInfo.processInfo.environment
        guard let dir = env["BAY_SNAPSHOT_DIR"] else {
            throw XCTSkip("set BAY_SNAPSHOT_DIR to write bay snapshots")
        }
        try FileManager.default.createDirectory(atPath: dir,
                                                withIntermediateDirectories: true)
        let lib = try library()
        let size = CGSize(width: 390, height: 358)

        // one of each layout, plus the two that were reported wrong
        let cases: [(String, Double, Double, Double)] = [
            ("a45", 400, 4000, 1.1),      // inline-4 turbo, mid power stroke
            ("rs3", 400, 4000, 1.2),      // inline-5
            ("aven", 400, 6000, 0.0),     // V12 NA
            ("f2007", 400, 12000, 0.0),   // V8 flat-plane
            ("gt500", 400, 4000, 0.6),    // V8 cross-plane
            ("991rs", 400, 6000, 0.0),    // flat-6
            ("veyron", 400, 4000, 1.0),   // W16 quad-turbo
            ("rx7", 400, 5000, 0.8),      // 2-rotor
            ("787b", 400, 8000, 0.0),     // 4-rotor
        ]
        var wrote = 0
        for (key, crank, rpm, boost) in cases {
            guard let e = lib.engine(key) else { continue }
            let s = scene(e, crank: crank, rpm: rpm, boost: boost)
            let view = Canvas { ctx, sz in s.draw(ctx, size: sz) }
                .frame(width: size.width, height: size.height)
                .background(Color(white: 0.07))
            let renderer = ImageRenderer(content: view)
            renderer.scale = 2.0
            guard let img = renderer.nsImage,
                  let tiff = img.tiffRepresentation,
                  let rep = NSBitmapImageRep(data: tiff),
                  let png = rep.representation(using: .png, properties: [:])
            else {
                XCTFail("could not render \(key)")
                continue
            }
            let path = (dir as NSString).appendingPathComponent("\(key).png")
            try png.write(to: URL(fileURLWithPath: path))
            wrote += 1
        }
        print("wrote \(wrote) bay snapshots to \(dir)")
        XCTAssertGreaterThan(wrote, 0)
    }
}
#endif
