//
//  LayerEffectTests.swift
//
//  Every one of the twenty switches must be LIVE.  A switch that silently
//  does nothing is worse than no switch, because it reads as coverage: you
//  press it, hear no change, and conclude the stage does not matter -- when
//  really the toggle was never wired.
//
//  Reaching them all needs a spread of cars and operating points, because
//  several stages are conditional: the burble only fires on a closed throttle
//  with the revs up, only two cars in the fleet have a megaphone, the turbine
//  needs boost, and the gearbox needs a gear.
//

import XCTest
@testable import EngineSimCore

final class LayerEffectTests: XCTestCase {
    func fixture(_ n: String) throws -> Data {
        let url = Bundle.module.url(forResource: n, withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: n, withExtension: "json")
        guard let url else { throw XCTSkip("\(n).json missing") }
        return try Data(contentsOf: url)
    }

    func testEverySwitchChangesTheSound() throws {
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))
        let tables = try EngineTables.load(jsonData: try fixture("engine_tables"))
        let voicings = try VoicingSetup.load(jsonData: try fixture("engine_voicing"))

        // (car, throttle, pov) -- between them these reach every branch
        let cases: [(String, Double, String)] = [
            ("rs3", 0.85, "chase"),      // turbo: turbine, wastegate, GPF
            ("rs3", 0.0, "chase"),       // ...and OVERRUN: the burble
            ("f2007", 0.9, "cockpit"),   // a real megaphone, a screamer's whine
            ("aven", 0.7, "chase"),      // big NA V12: thunder and rumble
        ]

        func render(_ key: String, _ throttle: Double, _ pov: String,
                    hide: Stage?) -> [Float] {
            guard let e = fleet[key], let t = tables[key],
                  let v = voicings[key] else { return [] }
            let syn = Synthesizer(engine: e, tables: t, voicing: v,
                                  sampleRate: 32000, block: 256, seed: 1)
            syn.pov = pov
            if let h = hide { syn.layers.set(h, false) }
            syn.drive.gear = 3
            syn.drive.speed = 30
            syn.speed = 30
            var out = [Float]()
            for i in 0..<8 {
                syn.set(rpm: e.redlineRpm * (0.35 + 0.07 * Double(i)),
                        throttle: throttle, boost: e.boostBar * throttle)
                out += syn.render(frames: 256)
            }
            return out
        }

        func differenceDB(_ a: [Float], _ b: [Float]) -> Double {
            guard a.count == b.count, !a.isEmpty else { return -300 }
            var num = 0.0, den = 0.0
            for i in a.indices {
                let d = Double(a[i]) - Double(b[i])
                num += d * d; den += Double(b[i]) * Double(b[i])
            }
            if den <= 0 { return -300 }
            return 10.0 * log10(max(num / den, 1e-30))
        }

        var bases = [String: [Float]]()
        for c in cases { bases["\(c.0)|\(c.1)|\(c.2)"] = render(c.0, c.1, c.2, hide: nil) }

        var dead = [String]()
        print("  chain switches, best of \(cases.count) operating points:")
        for s in Stage.allCases {
            var best = -300.0, where_ = ""
            for c in cases {
                let base = bases["\(c.0)|\(c.1)|\(c.2)"]!
                let d = differenceDB(render(c.0, c.1, c.2, hide: s), base)
                if d > best { best = d; where_ = c.0 }
            }
            print(String(format: "    %-16@ %+7.1f dB  (%@)", s.rawValue,
                         best, where_))
            if best < -60.0 { dead.append(s.rawValue) }
        }
        XCTAssertTrue(dead.isEmpty,
                      "these switches do NOTHING: \(dead.joined(separator: ", "))")
    }

    func testSoloLeavesExactlyOneVisible() {
        let st = LayerStack()
        st.solo(.muffler)
        XCTAssertEqual(Stage.allCases.filter { st.isVisible($0) }, [.muffler])
        st.solo(.muffler)                    // pressing it again brings it back
        XCTAssertEqual(st.hiddenCount, 0)
    }
}
