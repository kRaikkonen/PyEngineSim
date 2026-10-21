//
//  LoudnessTests.swift
//
//  REAL LOUDNESS: with it on, the auto-level's target follows rpm and load,
//  so the gap between an idle and a wide-open pull near the top opens up the
//  way a real car's does; with it off the classic constant level holds.
//

import XCTest
@testable import EngineSimCore

final class LoudnessTests: XCTestCase {
    func testTheCurve() {
        let red = 6500.0
        let idle = Synthesizer.loudnessDB(rpm: 800, redline: red, mapFraction: 0.3)
        let cruise = Synthesizer.loudnessDB(rpm: 2200, redline: red, mapFraction: 0.55)
        let wot = Synthesizer.loudnessDB(rpm: 4875, redline: red, mapFraction: 1.0)
        let boosted = Synthesizer.loudnessDB(rpm: 6400, redline: red, mapFraction: 2.0)
        XCTAssertEqual(idle, -24.0, accuracy: 1e-9, "held at the floor")
        XCTAssertLessThan(idle, cruise)
        XCTAssertLessThan(cruise, wot)
        XCTAssertEqual(wot, 0.0, accuracy: 0.01, "three quarters of the redline, wide open")
        XCTAssertEqual(boosted, 3.0, accuracy: 1e-9, "held at the ceiling")
        // ~9 dB per doubling of rpm at a fixed load
        let a = Synthesizer.loudnessDB(rpm: 1500, redline: red, mapFraction: 1.0)
        let b = Synthesizer.loudnessDB(rpm: 3000, redline: red, mapFraction: 1.0)
        XCTAssertEqual(b - a, 30.0 * log10(2.0), accuracy: 1e-9)
    }

    func library() throws -> EngineLibrary {
        func data(_ n: String) throws -> Data {
            let url = Bundle.module.url(forResource: n, withExtension: "json",
                                        subdirectory: "Fixtures")
                ?? Bundle.module.url(forResource: n, withExtension: "json")
            guard let url else { throw XCTSkip("\(n).json missing") }
            return try Data(contentsOf: url)
        }
        return try EngineLibrary(presets: data("presets"), tables: data("engine_tables"),
                                 voicing: data("engine_voicing"))
    }

    /// Output level (dB) settled at an operating point.
    func level(_ lib: EngineLibrary, _ key: String, real: Bool, rpmFrac: Double,
               throttle: Double) throws -> Double {
        guard let e = lib.engine(key), let t = lib.tables(key), let v = lib.voicing(key)
        else { throw XCTSkip("\(key) missing") }
        let sy = Synthesizer(engine: e, tables: t, voicing: v, sampleRate: 32000,
                             block: 256, seed: 1)
        sy.realLoudness = real
        let rpm = max(rpmFrac * e.redlineRpm, e.idleRpm)
        var acc = 0.0, n = 0
        for k in 0..<375 {                      // 3 s: let the auto-level settle
            sy.set(rpm: rpm, throttle: throttle, boost: 0)
            let out = sy.render(frames: 256)
            if k >= 250 { for x in out { acc += Double(x) * Double(x) }; n += out.count }
        }
        return 10.0 * log10(acc / Double(max(n, 1)) + 1e-20)
    }

    func testAPullSwellsOverTheIdle() throws {
        let lib = try library()
        for key in ["a3", "aven"] {
            let offGap = try level(lib, key, real: false, rpmFrac: 0.8, throttle: 1.0)
                - level(lib, key, real: false, rpmFrac: 0.0, throttle: 0.0)
            let onGap = try level(lib, key, real: true, rpmFrac: 0.8, throttle: 1.0)
                - level(lib, key, real: true, rpmFrac: 0.0, throttle: 0.0)
            print(String(format: "  %@: pull over idle %.1f dB classic, %.1f dB real", key, offGap, onGap))
            XCTAssertGreaterThan(onGap, offGap + 8.0, "\(key): the pull must swell over the idle")
        }
    }
}
