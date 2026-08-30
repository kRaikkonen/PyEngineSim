//
//  PerformanceTests.swift
//
//  The whole reason this implementation exists.  The Python v1 shipped and
//  worked, but it sat right on the edge of what a general-purpose runtime can
//  do on a phone -- a V12 pushed it past 150 % of real time and it broke up.
//  So the number that matters is not "is it fast", it is "how much room is
//  left when the hardest car in the fleet is playing".
//
//  Measured as a REALTIME FACTOR: seconds of CPU per second of audio.  0.10
//  means a tenth of one core.  A phone core is roughly a third the speed of
//  this Mac's, so the useful reading is the factor multiplied by ~3 -- which
//  is why the assertion below is far tighter than "under 1.0".
//
//  Every car in the fleet is measured, not a favourite: the failure mode being
//  guarded against is one specific engine (a W16, a V12, an 18,000 rpm V8)
//  being the one that does not fit.
//

import XCTest
@testable import EngineSimCore

final class PerformanceTests: XCTestCase {
    /// Skip unless optimised.  An unoptimised Swift build of this chain is
    /// roughly twenty times slower, so in debug these tests take ten minutes
    /// and measure the compiler rather than the code.  Run them with
    /// `swift test -c release --filter PerformanceTests`.
    override func setUpWithError() throws {
        #if DEBUG
        throw XCTSkip("performance is only meaningful in release "
                      + "(swift test -c release)")
        #endif
    }

    func fixture(_ n: String) throws -> Data {
        let url = Bundle.module.url(forResource: n, withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: n, withExtension: "json")
        guard let url else { throw XCTSkip("\(n).json missing") }
        return try Data(contentsOf: url)
    }

    /// Seconds of CPU per second of audio, at a hard operating point.
    func realtimeFactor(_ syn: Synthesizer, rate: Double, block: Int,
                        blocks: Int) -> Double {
        for _ in 0..<8 { _ = syn.render(frames: block) }     // warm the caches
        let t0 = CFAbsoluteTimeGetCurrent()
        for _ in 0..<blocks { _ = syn.render(frames: block) }
        let cpu = CFAbsoluteTimeGetCurrent() - t0
        return cpu / (Double(blocks * block) / rate)
    }

    func testWholeFleetFitsInRealTime() throws {
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))
        let tables = try EngineTables.load(jsonData: try fixture("engine_tables"))
        let voicings = try VoicingSetup.load(jsonData: try fixture("engine_voicing"))
        let rate = 32000.0, block = 512

        var worst = 0.0, worstCar = ""
        var total = 0.0, n = 0
        var results = [(String, Double)]()

        for key in fleet.keys.sorted() {
            guard let e = fleet[key], let t = tables[key],
                  let v = voicings[key] else { continue }
            let syn = Synthesizer(engine: e, tables: t, voicing: v,
                                  sampleRate: rate, block: block)
            // near the limiter with the throttle buried: the most expensive
            // place the chain ever runs, because every conditional stage is on
            syn.set(rpm: e.redlineRpm * 0.92, throttle: 1.0, boost: e.boostBar)
            syn.drive.gear = 3
            syn.drive.speed = 40
            syn.speed = 40
            let f = realtimeFactor(syn, rate: rate, block: block, blocks: 24)
            results.append((key, f))
            total += f; n += 1
            if f > worst { worst = f; worstCar = key }
        }

        results.sort { $0.1 > $1.1 }
        print("  realtime factor at \(Int(rate)) Hz / block \(block), \(n) cars")
        print("    worst 5:")
        for (k, f) in results.prefix(5) {
            print("      \(k) \(String(format: "%.3f", f))  "
                  + "(\(String(format: "%.0f", f * 100))% of one core)")
        }
        print(String(format: "    median %.3f, mean %.3f",
                     results[results.count / 2].1, total / Double(n)))
        print(String(format: "    a phone core is ~3x slower: worst becomes ~%.0f%%",
                     worst * 300))

        XCTAssertEqual(n, 131, "every car must be measured")
        // 0.25 here leaves the worst car at roughly three quarters of one
        // phone core -- tight enough to catch a regression, loose enough not
        // to fail on a busy Mac
        XCTAssertLessThan(worst, 0.25,
                          "\(worstCar) is too expensive: \(worst) of real time")
    }

    /// Head to head with the Python, same machine, same cars, same block.
    ///
    /// Block 256 because that is the largest the PYTHON can do: its reverb
    /// sizes its comb delays from a module constant BLOCK = 256 and the
    /// vectorised form breaks above it.  Measured against the no-scipy path,
    /// because that is what the phone runs.
    func testAgainstThePython() throws {
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))
        let tables = try EngineTables.load(jsonData: try fixture("engine_tables"))
        let voicings = try VoicingSetup.load(jsonData: try fixture("engine_voicing"))
        // measured on this Mac with tools/bench_python.py, numpy 2.0.2, no scipy
        let python: [String: Double] = [
            "a3": 1.646, "aven": 2.329, "veyron": 2.264, "f2007": 2.078,
            "6": 2.238, "917": 2.304, "clkgtr": 2.261, "speed12": 2.257,
            "rs3": 1.696, "8": 1.820,
        ]
        var worstSpeedup = Double.infinity
        print("  swift vs python, 32000 Hz / block 256, same machine")
        for key in python.keys.sorted() {
            guard let e = fleet[key], let t = tables[key],
                  let v = voicings[key] else { continue }
            let syn = Synthesizer(engine: e, tables: t, voicing: v,
                                  sampleRate: 32000, block: 256)
            syn.pov = "cockpit"
            syn.set(rpm: e.redlineRpm * 0.9, throttle: 1.0, boost: e.boostBar)
            let f = realtimeFactor(syn, rate: 32000, block: 256, blocks: 40)
            let speedup = python[key]! / f
            worstSpeedup = min(worstSpeedup, speedup)
            print("    \(key): swift \(String(format: "%.3f", f)) vs python "
                  + "\(String(format: "%.3f", python[key]!)) -- "
                  + "\(String(format: "%.0f", speedup))x")
        }
        // The Python cannot keep up on a MAC (1.6-2.3x real time), which is
        // why the phone broke up.  This has to be a different order of
        // magnitude, not a percentage better.
        XCTAssertGreaterThan(worstSpeedup, 8.0)
    }

    /// The same thing at the settings the app actually ships.
    func testTheShippingConfigurationHasHeadroom() throws {
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))
        let tables = try EngineTables.load(jsonData: try fixture("engine_tables"))
        let voicings = try VoicingSetup.load(jsonData: try fixture("engine_voicing"))
        // the A3 is what will actually be playing in the car, and the aven is
        // the one that broke the Python
        for key in ["a3", "aven", "veyron", "f2007"] {
            guard let e = fleet[key], let t = tables[key],
                  let v = voicings[key] else { continue }
            let syn = Synthesizer(engine: e, tables: t, voicing: v,
                                  sampleRate: 32000, block: 512)
            syn.pov = "cockpit"
            syn.set(rpm: e.redlineRpm * 0.9, throttle: 1.0, boost: e.boostBar)
            let f = realtimeFactor(syn, rate: 32000, block: 512, blocks: 40)
            print(String(format: "    %-8@ cockpit, near the limiter: %.3f", key, f))
            XCTAssertLessThan(f, 0.25, "\(key) has no headroom")
        }
    }
}
