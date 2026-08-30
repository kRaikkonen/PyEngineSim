//
//  DriveModelTests.swift
//
//  A pedal is not a slider, and the difference is the whole point: a slider
//  says what revs to be at, a pedal says how much torque to make and the revs
//  are the consequence.  So what is checked here is that the CONSEQUENCE is
//  right -- first gear pulls harder than sixth, a lift makes the revs fall
//  rather than hold, an upshift drops them by the ratio step, and the limiter
//  actually limits.
//

import XCTest
@testable import EngineSimCore

final class DriveModelTests: XCTestCase {
    func table(_ key: String = "a3") throws -> TorqueTable {
        let url = Bundle.module.url(forResource: "engine_torque",
                                    withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "engine_torque",
                                 withExtension: "json")
        guard let url else { throw XCTSkip("engine_torque.json missing") }
        let all = try TorqueTable.load(jsonData: Data(contentsOf: url))
        guard let t = all[key] else { throw XCTSkip("no \(key)") }
        return t
    }

    func run(_ p: PedalSource, seconds: Double, throttle: Double,
             gear: Int? = nil) {
        if let g = gear { p.setGear(g) }
        p.throttle = throttle
        var t = 0.0
        while t < seconds { p.update(dt: 0.01); t += 0.01 }
    }

    func testAClosedThrottleMakesNegativeTorque() throws {
        let t = try table()
        // this is engine braking, and it is what a lift is MADE of -- a dyno
        // clamps it at zero because a dyno cannot show negative
        XCTAssertLessThan(t.netTorque(rpm: 4000, throttle: 0.0), 0)
        XCTAssertGreaterThan(t.netTorque(rpm: 4000, throttle: 1.0), 100)
    }

    func testNeutralRevsFreelyAndFallsBack() throws {
        let p = PedalSource(table: try table())
        p.setGear(0)
        run(p, seconds: 1.2, throttle: 1.0)
        XCTAssertGreaterThan(p.rpm, 5000, "a blip in neutral must SNAP")
        // 6 s, not 2: a 0.14 kg m^2 flywheel shedding ~11 Nm of friction
        // takes about four seconds to come down from five thousand, and
        // pretending otherwise would be testing a wish rather than the model
        run(p, seconds: 6.0, throttle: 0.0)
        print(String(format: "  free-rev decay settles at %.0f rpm", p.rpm))
        XCTAssertLessThan(p.rpm, 1200, "it must come back down...")
        XCTAssertGreaterThan(p.rpm, 600,
                             "...but the governor must CATCH it at idle, not "
                             + "let it coast to a stop")
    }

    func testFirstGearPullsHarderThanSixth() throws {
        let a = PedalSource(table: try table())
        run(a, seconds: 3.0, throttle: 1.0, gear: 1)
        let b = PedalSource(table: try table())
        run(b, seconds: 3.0, throttle: 1.0, gear: 6)
        print(String(format: "  3 s flat out: 1st %.1f km/h, 6th %.1f km/h",
                     a.speed * 3.6, b.speed * 3.6))
        XCTAssertGreaterThan(a.rpm, b.rpm * 2,
                             "the ratio is the whole reason gears exist")
    }

    func testAnUpshiftDropsTheRevs() throws {
        let p = PedalSource(table: try table())
        run(p, seconds: 5.0, throttle: 1.0, gear: 2)
        let before = p.rpm
        p.upshift()
        p.update(dt: 0.01)
        print(String(format: "  upshift 2->3: %.0f -> %.0f rpm", before, p.rpm))
        XCTAssertLessThan(p.rpm, before * 0.85, "revs must fall by the step")
        XCTAssertEqual(p.gear, 3)
    }

    func testTheLimiterLimits() throws {
        let t = try table()
        let p = PedalSource(table: t)
        run(p, seconds: 6.0, throttle: 1.0, gear: 1)
        XCTAssertLessThanOrEqual(p.rpm, t.redline_rpm * 1.02)
        XCTAssertTrue(p.limiting || p.rpm > t.redline_rpm * 0.9,
                      "flat out in first, it should reach the limiter")
    }

    /// A floored pedal has to SPOOL, not arrive: a turbo that reaches full
    /// boost instantly is a supercharger, and it was reaching none at all --
    /// the MAP the pedal reported never exceeded ambient, so the synth was
    /// told the car was NA no matter what it was driving.
    func testTheTurboSpools() throws {
        let p = PedalSource(table: try table("a3"))
        XCTAssertEqual(p.boost, 0, accuracy: 1e-9)
        run(p, seconds: 0.15, throttle: 1.0, gear: 2)
        let early = p.boost
        run(p, seconds: 5.0, throttle: 1.0)
        let settled = p.boost
        print(String(format: "  boost: %.2f bar early, %.2f settled",
                     early, settled))
        XCTAssertLessThan(early, settled * 0.8, "it must take TIME")
        XCTAssertGreaterThan(settled, 0.5, "...and then actually arrive")
        // and the synth reads boost off MAP, so MAP has to carry it
        XCTAssertGreaterThan(p.mapKPa, p.baroKPa * 1.2,
                             "MAP must exceed ambient on a boosted engine")

        // an NA engine must stay at zero, not pick up a phantom
        let na = PedalSource(table: try table("aven"))
        run(na, seconds: 3.0, throttle: 1.0, gear: 2)
        XCTAssertEqual(na.boost, 0, accuracy: 1e-12)
        XCTAssertLessThanOrEqual(na.mapKPa, na.baroKPa + 1e-9)
    }

    /// The pedal has to be indistinguishable from any other source, or the
    /// chain would need to know which one is talking to it.
    func testItIsJustATelemetrySource() throws {
        let p: TelemetrySource = PedalSource(table: try table())
        XCTAssertTrue(p.isLive())
        XCTAssertEqual(p.rpm, p.rawRPM)
        XCTAssertTrue(p.speedValid)
    }

    // ------------------------------------------------------------- the pops
    func testPopsAreSilentUntilArmed() {
        let cache = FilterCache(sampleRate: 32000)
        let rng = PortableRNG(seed: 1)
        let pops = OverrunPops(sampleRate: 32000, cache: cache, rng: rng)
        // disarmed it must not even DRAW: arming it would otherwise shift
        // every random number downstream and change the whole engine note
        let before = rng.standardNormal(4)
        let out = pops.render(frames: 256, rpm: 4000, throttle: 0.0,
                              idleRpm: 800, redlineRpm: 6500, antiLag: false,
                              ignitionOn: true, params: [:])
        XCTAssertTrue(out.isEmpty)
        let rng2 = PortableRNG(seed: 1)
        XCTAssertEqual(before, rng2.standardNormal(4),
                       "a disarmed stage must not touch the generator")
    }

    /// Count the distinct bangs in one lift: a block that is loud after a
    /// quiet one is a new pop.
    func popCount(onGasSeconds: Double, coastSeconds: Double,
                  lifts: Int = 1) -> Int {
        let pops = OverrunPops(sampleRate: 32000,
                               cache: FilterCache(sampleRate: 32000),
                               rng: PortableRNG(seed: 7))
        pops.enabled = true
        let P = ["pops": 1.0, "pop_muff": 0.4, "pops_reverb": 0.0]
        var t = 0.0
        for lift in 0..<lifts {
            t = 0
            while t < onGasSeconds {
                _ = pops.render(frames: 256, rpm: 5000, throttle: 1.0,
                                idleRpm: 800, redlineRpm: 6500, antiLag: false,
                                ignitionOn: true, params: P)
                t += 256.0 / 32000.0
            }
            if lift == lifts - 1 { break }
            t = 0
            while t < coastSeconds {
                _ = pops.render(frames: 256, rpm: 5000, throttle: 0.0,
                                idleRpm: 800, redlineRpm: 6500, antiLag: false,
                                ignitionOn: true, params: P)
                t += 256.0 / 32000.0
            }
        }
        t = 0
        while t < coastSeconds {
            _ = pops.render(frames: 256, rpm: 5000, throttle: 0.0,
                            idleRpm: 800, redlineRpm: 6500,
                            antiLag: false, ignitionOn: true, params: P)
            t += 256.0 / 32000.0
        }
        return pops.fired
    }

    func testPopsNeedTheLiftAndTheLoading() {
        func bangs(onGasSeconds: Double) -> Double {
            let cache = FilterCache(sampleRate: 32000)
            let pops = OverrunPops(sampleRate: 32000, cache: cache,
                                   rng: PortableRNG(seed: 7))
            pops.enabled = true
            let P = ["pops": 1.0, "pop_muff": 0.4, "pops_reverb": 0.0]
            // load the pipe by being on the gas
            var t = 0.0
            while t < onGasSeconds {
                _ = pops.render(frames: 256, rpm: 5000, throttle: 1.0,
                                idleRpm: 800, redlineRpm: 6500, antiLag: false,
                                ignitionOn: true, params: P)
                t += 256.0 / 32000.0
            }
            // then lift
            var peak = 0.0
            for _ in 0..<160 {
                for v in pops.render(frames: 256, rpm: 5000, throttle: 0.0,
                                     idleRpm: 800, redlineRpm: 6500,
                                     antiLag: false, ignitionOn: true,
                                     params: P) {
                    peak = max(peak, abs(v))
                }
            }
            return peak
        }
        let loaded = bangs(onGasSeconds: 4.0)
        print(String(format: "  lift after a pull: peak %.3f", loaded))
        XCTAssertGreaterThan(loaded, 0.02, "a lift after a pull must CRACKLE")

        // ...and then STOP.  A finite budget per lift is the difference
        // between a car and a fireworks display; without it this crackles all
        // the way down to idle.
        let counted = popCount(onGasSeconds: 4.0, coastSeconds: 6.0)
        print("  bangs in one lift: \(counted)")
        XCTAssertGreaterThanOrEqual(counted, 3, "a lift should crackle...")
        XCTAssertLessThanOrEqual(counted, 4, "...three or four, not a stream")

        // and a second lift refills it -- but only after going back on the gas
        let twice = popCount(onGasSeconds: 4.0, coastSeconds: 6.0,
                             lifts: 2)
        print("  bangs over two lifts: \(twice)")
        XCTAssertGreaterThan(twice, counted, "going back on the gas refills it")

        // and on the gas it must be silent -- pops are a lift-off event
        let cache = FilterCache(sampleRate: 32000)
        let pops = OverrunPops(sampleRate: 32000, cache: cache,
                               rng: PortableRNG(seed: 7))
        pops.enabled = true
        var peak = 0.0
        for _ in 0..<200 {
            for v in pops.render(frames: 256, rpm: 5000, throttle: 1.0,
                                 idleRpm: 800, redlineRpm: 6500,
                                 antiLag: false, ignitionOn: true,
                                 params: ["pops": 1.0]) {
                peak = max(peak, abs(v))
            }
        }
        XCTAssertEqual(peak, 0.0, accuracy: 1e-12,
                       "it must not pop while you are ON the throttle")
    }
}
