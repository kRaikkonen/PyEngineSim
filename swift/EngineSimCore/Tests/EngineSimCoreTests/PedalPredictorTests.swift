//
//  PedalPredictorTests.swift
//
//  The pedal model on its own (lag, physics prior, learning) and against a
//  simulated car: a driver stabs the throttle, the rpm and pedal arrive over
//  a slow link (sampled every 70 ms, 60 ms round trip), and the rpm the sound
//  plays is reconstructed with and without the pedal's head start.  The low-
//  rev lift of the rpm map is checked here too: same mode, same ends.
//

import XCTest
@testable import EngineSimCore

final class PedalPredictorTests: XCTestCase {
    func testEffortFollowsThePedalThroughTheEngineLag() {
        let p = PedalPredictor()
        p.pedalSample(0.0, at: 10.0)
        p.pedalSample(1.0, at: 10.0 + 1e-9)
        let tau = PedalPredictor.tauEngine
        XCTAssertEqual(p.effort(at: 10.0 + tau), 1.0 - exp(-1.0), accuracy: 1e-6)
        XCTAssertEqual(p.effort(at: 20.0), 1.0, accuracy: 1e-9)
    }

    func testThePriorFollowsTheGear() {
        // first gear (~150 rpm per km/h) pulls the revs up far faster than
        // fifth (~30); stood still the flywheel alone
        let first = PedalPredictor.prior(rpmPerKmh: 150)
        let fifth = PedalPredictor.prior(rpmPerKmh: 30)
        XCTAssertGreaterThan(first, 10 * fifth)
        XCTAssertEqual(PedalPredictor.prior(rpmPerKmh: 0), 4000)
        XCTAssertGreaterThan(fifth, 60)
    }

    func testItLearnsTheCarsOwnGain() {
        let p = PedalPredictor()
        let n = 100.0                        // ~2nd gear
        let prior = PedalPredictor.prior(rpmPerKmh: n)
        let truth = 1.6 * prior
        for k in 0..<200 {
            let de = (k % 2 == 0 ? 1.0 : -1.0) * 0.1
            p.learn(gear: 2, dRate: (de > 0 ? truth : 0.5 * truth) * de, dEffort: de)
        }
        XCTAssertEqual(p.gain(gear: 2, rpmPerKmh: n, rising: true), truth,
                       accuracy: 0.02 * truth)
        XCTAssertEqual(p.gain(gear: 2, rpmPerKmh: n, rising: false), 0.5 * truth,
                       accuracy: 0.02 * truth)
        // a gear it never saw keeps the physics
        XCTAssertEqual(p.gain(gear: 5, rpmPerKmh: 30, rising: true),
                       PedalPredictor.prior(rpmPerKmh: 30))
    }

    /// A simulated stab in 2nd gear: the real rpm accelerates at
    /// K x (effort - hold), the effort being the pedal through the engine's
    /// lag.  The link delivers rpm and pedal every 70 ms, each 30 ms old (half
    /// a 60 ms round trip), measured rate smoothed as OBDTelemetry does.  The
    /// reconstruction aims 120 ms past each arrival (audio + glide).
    func testThePedalCutsTheLagOfAStab() {
        let n = 100.0
        let K = PedalPredictor.prior(rpmPerKmh: n)
        let hold = 0.15
        let dtSim = 0.001
        var t = 0.0, rpm = 1800.0, eff = hold, pedal = hold
        var truth = [(Double, Double)]()        // (t, rpm)
        var pedals = [(Double, Double)]()
        while t < 3.0 {
            pedal = t < 1.0 ? hold : 0.8         // the stab at 1 s
            eff += (pedal - eff) * dtSim / PedalPredictor.tauEngine
            rpm += K * (eff - hold) * dtSim
            truth.append((t, rpm)); pedals.append((t, pedal))
            t += dtSim
        }
        func at(_ arr: [(Double, Double)], _ x: Double) -> Double {
            arr[min(max(Int((x / dtSim).rounded()), 0), arr.count - 1)].1
        }
        func run(predict: Bool) -> Double {
            let pp = PedalPredictor()
            var raw = 0.0, dR = 0.0, lastT = 0.0, tS = 0.0, prevTaken: Double? = nil
            var err2 = 0.0, nErr = 0
            var arrive = 0.0
            while arrive < 2.6 {
                arrive += 0.070
                let taken = arrive - 0.030
                pp.pedalSample(at(pedals, taken), at: taken)
                let r = at(truth, taken)
                if lastT > 0 { dR += ((r - raw) / (arrive - lastT) - dR) * 0.35 }
                pp.rpmSample(taken: taken, previous: prevTaken, smoothing: 0.35)
                prevTaken = taken; tS = taken
                raw = r; lastT = arrive
                // between arrivals, every 10 ms: the rpm the sound plays
                var now = arrive
                while now < arrive + 0.070 {
                    let ahead = (now - arrive) + 0.030 + 0.120
                    var d = max(min(dR * ahead, 800.0), -800.0)
                    if predict {
                        d += pp.extraRPM(from: tS, to: tS + ahead,
                                         gainRising: K, gainFalling: 0.5 * K)
                    }
                    let played = raw + d
                    let real = at(truth, now + 0.120)
                    if now > 0.95 && now < 1.6 {  // the stab and its rise
                        err2 += (played - real) * (played - real); nErr += 1
                    }
                    now += 0.010
                }
            }
            return (err2 / Double(max(nErr, 1))).squareRoot()
        }
        let without = run(predict: false), with = run(predict: true)
        print(String(format: "  stab: rpm error %.0f without the pedal, %.0f with", without, with))
        XCTAssertLessThan(with, 0.6 * without, "the pedal must take a real bite out of the lag")
    }

    func testTheLowRevLiftKeepsTheEnds() {
        let m = RpmMap(mode: .stretch, carIdle: 760, carRedline: 6500, learn: false)
        let flat = m(2500, engIdle: 1000, engRedline: 9000)
        m.lift = 1.0
        let lifted = m(2500, engIdle: 1000, engRedline: 9000)
        XCTAssertGreaterThan(lifted, 6000, "everyday revs move up the range")
        XCTAssertGreaterThan(lifted, flat + 2000)
        XCTAssertEqual(m(760, engIdle: 1000, engRedline: 9000), 1000, accuracy: 1)
        XCTAssertEqual(m(6500, engIdle: 1000, engRedline: 9000), 9000, accuracy: 1)
        // monotonic: more revs never play fewer
        var prev = 0.0
        for r in stride(from: 800.0, through: 6400.0, by: 100.0) {
            let v = m(r, engIdle: 1000, engRedline: 9000)
            XCTAssertGreaterThan(v, prev)
            prev = v
        }
        m.lift = 0
        XCTAssertEqual(m(2500, engIdle: 1000, engRedline: 9000), flat)
    }
}
