//
//  EQTests.swift
//  The EQ sliders have to move the SOUND, not just the UI.
//
//  The four peaking filters were in the chain all along with nothing able to
//  reach them, so the risk with adding a panel is shipping four controls that
//  look connected and are not.  These render real audio and measure the band
//  that was asked for.
//
//  The flat case matters just as much: at 0 dB every filter is skipped, so the
//  output must be BIT-IDENTICAL to the sound before the panel existed.  An EQ
//  that quietly colours everything at its neutral setting would undo months of
//  tuning without anyone noticing.
//

import XCTest
@testable import EngineSimCore

final class EQTests: XCTestCase {

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

    /// Render a couple of seconds at a fixed rpm with the given EQ.
    func render(_ eq: [String: Double]) throws -> [Double] {
        let lib = try library()
        guard let e = lib.engine("a45"), let t = lib.tables("a45"),
              let v = lib.voicing("a45") else { throw XCTSkip("a45 missing") }
        let sy = Synthesizer(engine: e, tables: t, voicing: v,
                             sampleRate: 32000, seed: 1)
        for (k, val) in eq { sy.params[k] = val }
        sy.physics.rpm = 3500
        sy.physics.throttle = 0.6
        sy.physics.ignitionOn = true
        var out: [Double] = []
        for _ in 0..<180 {                       // ~1.4 s
            out.append(contentsOf: sy.render(frames: 256).map { Double($0) })
        }
        return Array(out[(out.count / 3)...])    // let the filters settle
    }

    /// Energy in a band, by a plain Goertzel-ish sweep -- no FFT needed to say
    /// whether 120 Hz got louder.
    func bandEnergy(_ x: [Double], from f0: Double, to f1: Double) -> Double {
        let sr = 32000.0
        var total = 0.0
        var f = f0
        while f <= f1 {
            var re = 0.0, im = 0.0
            let w = 2.0 * Double.pi * f / sr
            for (i, v) in x.enumerated() {
                re += v * cos(w * Double(i))
                im += v * sin(w * Double(i))
            }
            total += (re * re + im * im) / Double(x.count * x.count)
            f *= 1.06
        }
        return total
    }

    /// Flat must be the untouched chain, sample for sample.
    func testFlatIsBitIdentical() throws {
        let a = try render([:])
        let b = try render(["ueq_low": 0, "ueq_mid": 0, "ueq_high": 0,
                            "ueq_presence": 0])
        XCTAssertEqual(a.count, b.count)
        for i in 0..<min(a.count, b.count) where a[i] != b[i] {
            XCTFail("EQ at 0 dB changed sample \(i): \(a[i]) vs \(b[i])")
            return
        }
    }

    /// Total energy, for normalising.
    func totalEnergy(_ x: [Double]) -> Double {
        x.reduce(0.0) { $0 + $1 * $1 } / Double(max(x.count, 1))
    }

    /// Each slider has to move ITS OWN band and leave the others alone.
    ///
    /// Measured ABSOLUTELY, which only works because the tone stack sits
    /// AFTER the limiter.  Ahead of it the limiter clawed a bass boost straight
    /// back and the band got quieter -- that is exactly why these controls are
    /// not the eq_* bells inside ListenerStage.  Normalising by total energy is
    /// no good either: the low band is most of the output, so boosting it moves
    /// the denominator as much as the numerator.
    func share(_ x: [Double], from f0: Double, to f1: Double) -> Double {
        bandEnergy(x, from: f0, to: f1) / max(totalEnergy(x), 1e-12)
    }

    /// What is actually happening to the signal, printed.
    func testDiagnose() throws {
        for (label, eq) in [("flat", [String: Double]()),
                            ("bass +10", ["ueq_low": 10.0]),
                            ("bass -10", ["ueq_low": -10.0]),
                            ("treble +10", ["ueq_high": 10.0])] {
            let x = try render(eq)
            let rms = totalEnergy(x).squareRoot()
            let lo = bandEnergy(x, from: 80, to: 180)
            let hi = bandEnergy(x, from: 3200, to: 6400)
            print(String(format: "EQDIAG %-11@ rms %.5f  low %.3e  high %.3e",
                         label as NSString, rms, lo, hi))
        }
    }

    /// A boost must not run into the rails: nothing limits after this stage,
    /// so it trims itself back.
    func testBoostDoesNotClip() throws {
        let hot = try render(["ueq_low": 12.0, "ueq_mid": 6.0])
        let peak = hot.map { abs($0) }.max() ?? 0
        XCTAssertLessThan(peak, 1.0, "tone stack clipped at full boost")
    }

    func testEachBandMovesItsOwnRange() throws {
        let flat = try render([:])
        let lowS = bandEnergy(flat, from: 80, to: 180)
        let midS = bandEnergy(flat, from: 600, to: 1200)
        let hiS = bandEnergy(flat, from: 3200, to: 6400)

        let bass = try render(["ueq_low": 10.0])
        XCTAssertGreaterThan(bandEnergy(bass, from: 80, to: 180), lowS * 2.0,
                             "bass slider did not lift 120 Hz")

        let mid = try render(["ueq_mid": 10.0])
        XCTAssertGreaterThan(bandEnergy(mid, from: 600, to: 1200), midS * 2.0,
                             "mid slider did not lift 850 Hz")

        let treble = try render(["ueq_high": 10.0])
        XCTAssertGreaterThan(bandEnergy(treble, from: 3200, to: 6400), hiS * 2.0,
                             "treble slider did not lift 4.5 kHz")

        // and a cut is a cut
        let cut = try render(["ueq_low": -10.0])
        XCTAssertLessThan(bandEnergy(cut, from: 80, to: 180), lowS * 0.5,
                          "a negative bass setting did not cut")
    }
}
