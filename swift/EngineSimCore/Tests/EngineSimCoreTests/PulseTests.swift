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
        let pulses: [Double]
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
}
