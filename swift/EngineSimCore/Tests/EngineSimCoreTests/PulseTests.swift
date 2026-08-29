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
        let voiced: [Double]
        let choke: Double
        let d2: Int
        let exhaust_openness: Double
        let fire_chord: Int
        let params: [String: Double]
        let block: [Double]
        let pipes: [Double]
        let d1: Int
        let d3: Int
        let g1: Double
        let g2: Double
        let g3: Double
        let lp_a: Double
        let lp_end: Double
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

    /// The whole source path: port cavity, asymmetry, early reflections,
    /// crack, the rung chord, drive, weight.
    func testVoicedFiringEventMatchesPython() throws {
        let voicings = try VoicingSetup.load(jsonData: fixture("engine_voicing"))
        let cases = try JSONDecoder().decode([String: Case].self,
                                             from: fixture("pulses"))
        for (key, c) in cases {
            guard let setup = voicings[key] else { continue }
            let cache = FilterCache(sampleRate: c.sample_rate)
            let train = PulseTrain(setup: setup, sampleRate: c.sample_rate)
            let bf = BangFizz(nchan: setup.nchan, nCylinders: c.ncyl,
                              sampleRate: c.sample_rate, cache: cache)
            let src = SourceStage(sampleRate: c.sample_rate, nchan: setup.nchan,
                                  cache: cache)
            src.fireChordIndex = c.fire_chord
            let rng = PortableRNG(seed: c.seed)
            let chans = train.render(frames: c.frames, rpm: c.rpm,
                                     degPerSample: c.dps, load: c.load,
                                     soundSpeed: c.sound_speed, valve: c.valve,
                                     cylScale: c.cyl_scale, rng: rng)
            let bfOut = bf.process(chans: chans, strength: c.strength,
                                   rpm: c.rpm, degPerSample: c.dps, rng: rng)
            let out = src.process(bang: bfOut.bang, fizz: bfOut.fizz,
                                  rpm: c.rpm, nCylinders: c.ncyl,
                                  exhaustOpenness: c.exhaust_openness,
                                  choke: c.choke, d2: c.d2, params: c.params)
            var worst = 0.0, sumSq = 0.0, refSq = 0.0
            for i in 0..<min(out.voiced.count, c.voiced.count) {
                let d = out.voiced[i] - c.voiced[i]
                worst = max(worst, abs(d)); sumSq += d * d
                refSq += c.voiced[i] * c.voiced[i]
            }
            let rel = (sumSq / max(refSq, 1e-30)).squareRoot()
            print("  \(key) voiced: worst \(worst), rel rms \(rel)")
            XCTAssertLessThan(rel, 1e-9, "\(key) voiced firing event diverges")
        }
    }

    /// The structure-borne enclosure: combustion sealed behind the block, head
    /// and piston, and rung at the casting's resonances.
    func testBlockEnclosureMatchesPython() throws {
        let voicings = try VoicingSetup.load(jsonData: fixture("engine_voicing"))
        let cases = try JSONDecoder().decode([String: Case].self,
                                             from: fixture("pulses"))
        for (key, c) in cases {
            guard let setup = voicings[key] else { continue }
            let cache = FilterCache(sampleRate: c.sample_rate)
            let train = PulseTrain(setup: setup, sampleRate: c.sample_rate)
            let bf = BangFizz(nchan: setup.nchan, nCylinders: c.ncyl,
                              sampleRate: c.sample_rate, cache: cache)
            let src = SourceStage(sampleRate: c.sample_rate, nchan: setup.nchan,
                                  cache: cache)
            src.fireChordIndex = c.fire_chord
            let blk = BlockStage(setup: setup, sampleRate: c.sample_rate,
                                 cache: cache)
            let rng = PortableRNG(seed: c.seed)
            let chans = train.render(frames: c.frames, rpm: c.rpm,
                                     degPerSample: c.dps, load: c.load,
                                     soundSpeed: c.sound_speed, valve: c.valve,
                                     cylScale: c.cyl_scale, rng: rng)
            let bfOut = bf.process(chans: chans, strength: c.strength,
                                   rpm: c.rpm, degPerSample: c.dps, rng: rng)
            let sOut = src.process(bang: bfOut.bang, fizz: bfOut.fizz,
                                   rpm: c.rpm, nCylinders: c.ncyl,
                                   exhaustOpenness: c.exhaust_openness,
                                   choke: c.choke, d2: c.d2, params: c.params)
            // combustion = voiced bang + turbulence * mean fizz
            let inv = 1.0 / Double(setup.nchan)
            var fizzSum = [Double](repeating: 0, count: c.frames)
            for ch in bfOut.fizz {
                for i in 0..<c.frames { fizzSum[i] += ch[i] }
            }
            var combustion = [Double](repeating: 0, count: c.frames)
            let turb = c.params["turbulence"] ?? 0.5
            for i in 0..<c.frames {
                combustion[i] = sOut.voiced[i] + turb * (fizzSum[i] * inv)
            }
            let out = blk.process(combustion)
            var worst = 0.0, sumSq = 0.0, refSq = 0.0
            for i in 0..<min(out.sealed.count, c.block.count) {
                let d = out.sealed[i] - c.block[i]
                worst = max(worst, abs(d)); sumSq += d * d
                refSq += c.block[i] * c.block[i]
            }
            let rel = (sumSq / max(refSq, 1e-30)).squareRoot()
            print("  \(key) block: worst \(worst), rel rms \(rel)")
            XCTAssertLessThan(rel, 1e-9, "\(key) block enclosure diverges")
        }
    }

    /// The three waveguides in series plus the openness-scaled direct share.
    func testPipeSystemMatchesPython() throws {
        let voicings = try VoicingSetup.load(jsonData: fixture("engine_voicing"))
        let cases = try JSONDecoder().decode([String: Case].self,
                                             from: fixture("pulses"))
        for (key, c) in cases {
            guard let setup = voicings[key] else { continue }
            let cache = FilterCache(sampleRate: c.sample_rate)
            let train = PulseTrain(setup: setup, sampleRate: c.sample_rate)
            let bf = BangFizz(nchan: setup.nchan, nCylinders: c.ncyl,
                              sampleRate: c.sample_rate, cache: cache)
            let src = SourceStage(sampleRate: c.sample_rate, nchan: setup.nchan,
                                  cache: cache)
            src.fireChordIndex = c.fire_chord
            let blk = BlockStage(setup: setup, sampleRate: c.sample_rate,
                                 cache: cache)
            let pipes = PipeStage(nchan: setup.nchan)
            let rng = PortableRNG(seed: c.seed)

            let chans = train.render(frames: c.frames, rpm: c.rpm,
                                     degPerSample: c.dps, load: c.load,
                                     soundSpeed: c.sound_speed, valve: c.valve,
                                     cylScale: c.cyl_scale, rng: rng)
            let bfOut = bf.process(chans: chans, strength: c.strength,
                                   rpm: c.rpm, degPerSample: c.dps, rng: rng)
            let sOut = src.process(bang: bfOut.bang, fizz: bfOut.fizz,
                                   rpm: c.rpm, nCylinders: c.ncyl,
                                   exhaustOpenness: c.exhaust_openness,
                                   choke: c.choke, d2: c.d2, params: c.params)
            let inv = 1.0 / Double(setup.nchan)
            var fizzSum = [Double](repeating: 0, count: c.frames)
            for ch in bfOut.fizz { for i in 0..<c.frames { fizzSum[i] += ch[i] } }
            var combustion = [Double](repeating: 0, count: c.frames)
            let turb = c.params["turbulence"] ?? 0.5
            for i in 0..<c.frames {
                combustion[i] = sOut.voiced[i] + turb * (fizzSum[i] * inv)
            }
            let sealed = blk.process(combustion).sealed
            var wet = pipes.process(srcs: sOut.srcs, combustion: sealed,
                                    d1: c.d1, d2: c.d2, d3: c.d3,
                                    g1: c.g1, g2: c.g2, g3: c.g3, s: -1.0,
                                    lpA: c.lp_a, lpEnd: c.lp_end,
                                    res1: c.params["res1"] ?? 0.1,
                                    res2: c.params["res2"] ?? 0.1)
            let direct = PipeStage.directShare(exhaustOpenness: c.exhaust_openness)
            var sig = [Double](repeating: 0, count: c.frames)
            for i in 0..<c.frames {
                sig[i] = direct * sealed[i] + wet[i] + sOut.er[i]
            }
            wet = []
            var sumSq = 0.0, refSq = 0.0
            for i in 0..<min(sig.count, c.pipes.count) {
                let d = sig[i] - c.pipes[i]
                sumSq += d * d; refSq += c.pipes[i] * c.pipes[i]
            }
            let rel = (sumSq / max(refSq, 1e-30)).squareRoot()
            print("  \(key) pipes: rel rms \(rel)")
            XCTAssertLessThan(rel, 1e-9, "\(key) pipe system diverges")
        }
    }
}
