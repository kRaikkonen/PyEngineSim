//
//  PulseTests.swift
//
//  The raw pulse train, held to the Python sample for sample.  Everything
//  downstream only shapes this, so if it does not match, nothing above it can.
//
//  Both sides draw their jitter from the same PortableRNG, so this is an exact
//  comparison rather than "the spectra look close".
//

import XCTest
@testable import EngineSimCore

final class PulseTests: XCTestCase {
    struct Case: Decodable {
        let rpm: Double
        let seed: UInt64
        let sample_rate: Double
        let frames: Int
        let nblocks: Int
        let dps: Double
        let load: Double
        let sound_speed: Double
        let valve: Double
        let cyl_scale: [Double]
        let strength: Double
        let ncyl: Int
        let pulses: [Double]
        let bang: [Double]
        let fizz: [Double]
    }

    func fixture(_ name: String) throws -> Data {
        let url = Bundle.module.url(forResource: name, withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: name, withExtension: "json")
        guard let url else {
            XCTFail("fixture \(name).json missing"); throw NSError(domain: "t", code: 1)
        }
        return try Data(contentsOf: url)
    }

    func testPulseTrainMatchesPython() throws {
        let voicings = try VoicingSetup.load(jsonData: fixture("engine_voicing"))
        let cases = try JSONDecoder().decode([String: Case].self,
                                             from: fixture("pulses"))
        for (key, c) in cases {
            guard let setup = voicings[key] else {
                XCTFail("no voicing for \(key)"); continue
            }
            let train = PulseTrain(setup: setup, sampleRate: c.sample_rate)
            let rng = PortableRNG(seed: c.seed)
            var got = [Double]()
            for _ in 0..<c.nblocks {
                let chans = train.render(frames: c.frames, rpm: c.rpm,
                                         degPerSample: c.dps, load: c.load,
                                         soundSpeed: c.sound_speed,
                                         valve: c.valve,
                                         cylScale: c.cyl_scale, rng: rng)
                got.append(contentsOf: chans[0])
            }
            XCTAssertEqual(got.count, c.pulses.count, "\(key) length")

            var worst = 0.0, sumSq = 0.0, refSq = 0.0
            for i in 0..<min(got.count, c.pulses.count) {
                let d = got[i] - c.pulses[i]
                worst = max(worst, abs(d))
                sumSq += d * d
                refSq += c.pulses[i] * c.pulses[i]
            }
            let rel = (sumSq / max(refSq, 1e-30)).squareRoot()
            print("  \(key): worst sample \(worst), relative rms \(rel)")
            XCTAssertLessThan(worst, 1e-9, "\(key) pulse train diverges")
            XCTAssertLessThan(rel, 1e-9, "\(key) pulse train energy differs")
        }
    }

    /// The bang/fizz split, including the bipolar AC coupling that stops the
    /// train being a string of positive lumps.
    func testBangAndFizzMatchPython() throws {
        let voicings = try VoicingSetup.load(jsonData: fixture("engine_voicing"))
        let cases = try JSONDecoder().decode([String: Case].self,
                                             from: fixture("pulses"))
        for (key, c) in cases {
            guard let setup = voicings[key] else { continue }
            let train = PulseTrain(setup: setup, sampleRate: c.sample_rate)
            let cache = FilterCache(sampleRate: c.sample_rate)
            let bf = BangFizz(nchan: setup.nchan, nCylinders: c.ncyl,
                              sampleRate: c.sample_rate, cache: cache)
            let rng = PortableRNG(seed: c.seed)
            let chans = train.render(frames: c.frames, rpm: c.rpm,
                                     degPerSample: c.dps, load: c.load,
                                     soundSpeed: c.sound_speed, valve: c.valve,
                                     cylScale: c.cyl_scale, rng: rng)
            let out = bf.process(chans: chans, strength: c.strength, rpm: c.rpm,
                                 degPerSample: c.dps, rng: rng)
            for (name, got, want) in [("bang", out.bang[0], c.bang),
                                      ("fizz", out.fizz[0], c.fizz)] {
                var worst = 0.0, sumSq = 0.0, refSq = 0.0
                for i in 0..<min(got.count, want.count) {
                    let d = got[i] - want[i]
                    worst = max(worst, abs(d)); sumSq += d * d
                    refSq += want[i] * want[i]
                }
                let rel = (sumSq / max(refSq, 1e-30)).squareRoot()
                print("  \(key) \(name): worst \(worst), rel rms \(rel)")
                XCTAssertLessThan(rel, 1e-9, "\(key) \(name) diverges")
            }
        }
    }
}
