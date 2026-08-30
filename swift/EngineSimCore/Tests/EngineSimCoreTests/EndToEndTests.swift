//
//  EndToEndTests.swift
//
//  Every stage has already been checked one at a time, with the filter cache
//  and the generator restored at its boundary.  That proves each stage.  It
//  does NOT prove the wiring -- restoring the state at every boundary hides
//  exactly the errors wiring produces: a bus fed from the wrong place, or two
//  stages drawing from the shared generator in the wrong order.
//
//  So this one restores nothing.  One seed, twelve blocks, and the final
//  output has to match sample for sample.
//

import XCTest
@testable import EngineSimCore

final class EndToEndTests: XCTestCase {
    struct Point: Decodable {
        let rpm: Double, throttle: Double, boost: Double, coolant_c: Double
        let last_level: Double, speed: Double
        let gear: Int, num_gears: Int, clutch: Double
        let wheel_radius: Double, final_drive: Double, gas_torque: Double
    }
    struct Exc: Decodable {
        let strength: Double, load: Double, choke: Double, dps: Double
        let c_runner: Double, valve: Double, cyl_scale: [Double]
        let wet: [Double], er: [Double], srcs: [[Double]]
        let d1: Int, d2: Int, d3: Int
        let g1: Double, g2: Double, g3: Double
        let lp_a: Double, lp_end: Double, res1: Double, res2: Double
        let pov_sig: [Double], tk_x: Double
        let bay: [Double], bayi: [Double]
        let bayi_intake: [Double], bayi_spool: [Double]
        let ind: [Double], gw: [Double]
    }
    struct Case: Decodable {
        let pov: String
        let states: [Point]
        let exc: [Exc]
        let out: [[Double]]
        let taps: [[String: [Double]]]
        let params: [String: Double]
        let volume: Double
    }

    func residualDB(_ got: [Float], _ want: [Double]) -> Double {
        guard got.count == want.count, !want.isEmpty else { return .infinity }
        var num = 0.0, den = 0.0
        for i in want.indices {
            let d = Double(got[i]) - want[i]
            num += d * d; den += want[i] * want[i]
        }
        if den <= 0 { return num <= 0 ? -.infinity : .infinity }
        return 10.0 * log10(max(num / den, 1e-300))
    }

    func testWholeChainMatchesPython() throws {
        func fixture(_ name: String) throws -> Data {
            let url = Bundle.module.url(forResource: name, withExtension: "json",
                                        subdirectory: "Fixtures")
                ?? Bundle.module.url(forResource: name, withExtension: "json")
            guard let url else { throw XCTSkip("\(name).json missing") }
            return try Data(contentsOf: url)
        }
        let refs = try JSONDecoder().decode([String: Case].self,
                                            from: fixture("endtoend"))
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))
        let tables = try EngineTables.load(jsonData: try fixture("engine_tables"))
        let voicings = try VoicingSetup.load(jsonData: try fixture("engine_voicing"))

        for (name, c) in refs.sorted(by: { $0.key < $1.key }) {
            let carKey = String(name.split(separator: "-")[0])
            guard let eng = fleet[carKey], let tab = tables[carKey],
                  let voi = voicings[carKey] else {
                XCTFail("missing fixtures for \(carKey)"); continue
            }
            let syn = Synthesizer(engine: eng, tables: tab, voicing: voi,
                                  sampleRate: 32000, block: 256, seed: 1)
            syn.pov = c.pov
            syn.volume = c.volume
            syn.captureTaps = true
            for (k, v) in c.params { syn.params[k] = v }

            var worst = -Double.infinity, worstBlock = -1
            var firstBad: (stage: String, block: Int, db: Double)?
            for (bi, st) in c.states.enumerated() {
                syn.set(rpm: st.rpm, throttle: st.throttle, boost: st.boost)
                syn.physics.coolantC = st.coolant_c
                syn.speed = st.speed
                // the gearbox is part of the bay bus: leave the drivetrain in
                // neutral and a straight-cut car's whine simply is not there
                syn.drive.gear = st.gear
                syn.drive.numGears = st.num_gears
                syn.drive.clutch = st.clutch
                syn.drive.wheelRadius = st.wheel_radius
                syn.drive.finalDrive = st.final_drive
                syn.drive.gasTorque = st.gas_torque
                // Hand the chain the reference's own physics.  What is
                // being tested here is the WIRING; the surrogate tables'
                // distance from the Python is a separate claim with its own
                // measured bound in PhysicsTests, and letting it leak in here
                // would make a real wiring bug indistinguishable from it.
                let e = c.exc[bi]
                syn.forcedExcitation = .init(strength: e.strength, load: e.load,
                                             choke: e.choke,
                                             soundSpeed: e.c_runner,
                                             cylScale: e.cyl_scale)
                let out = syn.render(frames: 256)
                // deg/sample and the valve opening are NOT forced -- they
                // come from geometry the Swift derives itself, so they still
                // have to agree exactly
                XCTAssertEqual(syn.dbgDps, e.dps, accuracy: 1e-12,
                               "\(name) blk \(bi) deg/sample")
                XCTAssertEqual(syn.dbgValve, e.valve, accuracy: 1e-12,
                               "\(name) blk \(bi) valve")
                // every argument the pipe network takes, so a mismatch names
                // itself instead of surfacing as a wrong waveform
                let a = syn.dbgPipeArgs
                XCTAssertEqual(a.d1, e.d1, "\(name) blk \(bi) D1")
                XCTAssertEqual(a.d2, e.d2, "\(name) blk \(bi) D2")
                XCTAssertEqual(a.d3, e.d3, "\(name) blk \(bi) D3")
                XCTAssertEqual(a.g1, e.g1, accuracy: 1e-12, "\(name) blk \(bi) g1")
                XCTAssertEqual(a.g2, e.g2, accuracy: 1e-12, "\(name) blk \(bi) g2")
                XCTAssertEqual(a.g3, e.g3, accuracy: 1e-12, "\(name) blk \(bi) g3")
                XCTAssertEqual(a.lpA, e.lp_a, accuracy: 1e-12, "\(name) blk \(bi) lpA")
                XCTAssertEqual(a.lpEnd, e.lp_end, accuracy: 1e-12, "\(name) blk \(bi) lpEnd")
                XCTAssertEqual(a.res1, e.res1, accuracy: 1e-12, "\(name) blk \(bi) res1")
                XCTAssertEqual(a.res2, e.res2, accuracy: 1e-12, "\(name) blk \(bi) res2")
                // The one value that couples one block to the next.  Compared
                // at float32 precision, not double: numpy takes the mean of a
                // float32 array IN float32, and this is the RMS of the float32
                // output -- a tighter bound would be asserting that two
                // different accumulation widths agree.
                // (skipped on the one open case below -- it is the SAME
                // defect surfacing, not ten more)
                if name != "f2007-trackside" {
                    XCTAssertEqual(syn.dbgLastLevel, st.last_level,
                                   accuracy: 1e-7,
                                   "\(name) blk \(bi) previous-block RMS")
                }
                // walk the chain IN ORDER and report the first stage that
                // parts company -- a wiring error shows up once and then
                // contaminates everything after it
                if firstBad == nil {
                    // the raw excitation comes BEFORE any switchable layer, so
                    // check it first -- otherwise a bad pulse train is reported
                    // as a bad `voiced`
                    for nm in ["pulses", "bang", "fizz"] {
                        guard let want = c.taps[bi][nm],
                              let mine = syn.debugTaps[nm] else { continue }
                        let d = residualDB(mine.map { Float($0) }, want)
                        if d > -100.0 { firstBad = (nm, bi, d); break }
                    }
                }
                if firstBad == nil {
                    for st2 in Stage.allCases {
                        guard let want = c.taps[bi][st2.rawValue],
                              let mine = syn.layers.lastValue(st2) else { continue }
                        let d = residualDB(mine.map { Float($0) }, want)
                        if d > -100.0 {
                            firstBad = (st2.rawValue, bi, d)
                            break
                        }
                    }
                }
                let db = residualDB(out, c.out[bi])
                if db > worst { worst = db; worstBlock = bi }
            }
            if let f = firstBad {
                print(String(format: "  %-18@ FIRST BAD %@ at block %d, %.1f dB",
                             name,
                             f.stage, f.block, f.db))
            }
            print(String(format: "  %-18@ worst %7.1f dB (block %d)",
                         name, worst, worstBlock))
            // OUT OF SCOPE by decision (Leo, 2026-08-30): the trackside ear
            // is not a mode this ships in -- the car is for the CAR, and the
            // app is heard from the cockpit or the chase cam.  Recorded here
            // rather than deleted so nobody re-derives it as a mystery.
            //
            // What it is: f2007 on the trackside ear does not reproduce.  Traced as far as the GEARBOX WHINE on
            // the bay bus -- its pre-reverb value is 0.014854 where the Python
            // implies 0.016266, an 8.7 % shortfall present from block ZERO,
            // with every input to it (gear, speed, torque, wall-filter design,
            // oscillator phases) verified equal.  The stage passes at -316 dB
            // when its phases are restored, so it is something the free run
            // carries that the isolated test does not.
            //
            // The bound below is the MEASURED value, not a shrug: it cannot
            // regress, and it drops the moment this is fixed.
            let bound = name == "f2007-trackside" ? -13.0 : -100.0
            XCTAssertLessThan(worst, bound,
                              "\(name) drifted at block \(worstBlock)")
        }
    }
}
