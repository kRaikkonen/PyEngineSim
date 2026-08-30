//
//  CarModeTests.swift
//
//  The control layer, driven with no dongle and no audio device -- which is
//  the point of it being a separate object.  What is checked here is the
//  behaviour that only shows up over TIME: that the crank chases the mapped
//  target rather than jumping to it, that swapping engines rebuilds the synth
//  instead of rebinding it, and that losing the link falls back to idle rather
//  than freezing at whatever rpm it last saw.
//

import XCTest
@testable import EngineSimCore

final class CarModeTests: XCTestCase {
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

    func make(_ key: String = "a3", source: TelemetrySource? = nil,
              map: RpmMap = RpmMap(mode: .direct)) throws -> CarMode {
        let lib = try library()
        return try CarMode(engineKey: key, library: lib, telemetry: source,
                           rpmMap: map) { e, t, v in
            Synthesizer(engine: e, tables: t, voicing: v, sampleRate: 32000,
                        block: 256)
        }
    }

    func testTracksTheCarWithoutJumping() throws {
        let src = ManualSource()
        src.rpm = 800
        let car = try make(source: src)
        car.start()
        for _ in 0..<50 { car.update(dt: 0.02) }     // settle at idle
        let settled = car.rpmOut

        src.rpm = 5000
        src.throttle = 1.0
        let firstStep = { () -> Double in
            car.update(dt: 0.02)
            return car.rpmOut
        }()
        // rpmOut is the TARGET (it follows the map immediately); what must not
        // jump is the crank the synth actually plays
        XCTAssertEqual(firstStep, 5000, accuracy: 1.0)
        let omegaRPM = car.omega * 60.0 / (2.0 * Double.pi)
        XCTAssertLessThan(omegaRPM, 3000.0, "the crank must CHASE, not teleport")
        XCTAssertGreaterThan(omegaRPM, settled, "...but it must move")

        for _ in 0..<200 { car.update(dt: 0.02) }
        XCTAssertEqual(car.omega * 60.0 / (2.0 * Double.pi), 5000, accuracy: 60.0)
    }

    func testFallsBackToIdleWhenTheLinkDies() throws {
        let src = ManualSource()
        src.rpm = 6000; src.throttle = 1.0
        let car = try make(source: src)
        car.start()
        for _ in 0..<300 { car.update(dt: 0.02) }
        XCTAssertGreaterThan(car.rpmOut, 5000)

        car.telemetry = nil                       // the dongle drops out
        for _ in 0..<400 { car.update(dt: 0.02) }
        // it must SETTLE somewhere sane, not hold the last revs it saw
        XCTAssertEqual(car.rpmOut, car.engine.idleRpm, accuracy: 60.0)
    }

    func testSwappingEnginesRebuildsTheSynth() throws {
        let car = try make("a3")
        car.start()
        let first = car.synth
        XCTAssertNotNil(first)
        try car.setEngine("aven")
        XCTAssertEqual(car.engineKey, "aven")
        XCTAssertFalse(car.synth === first,
                       "the synth bakes its voicing from the engine; rebinding "
                       + "would play the old car's timbre at the new car's revs")
        XCTAssertEqual(car.synth?.engine.numCylinders, 12)
    }

    func testStretchMapUsesTheWholeRevRange() throws {
        // an A3 revving to 6500 wearing a V12 that revs to 8500: flooring it
        // must reach the V12's OWN top end, not stop two thirds up
        let src = ManualSource()
        let map = RpmMap(mode: .stretch, carIdle: 760, carRedline: 6500)
        let car = try make("aven", source: src, map: map)
        car.start()
        src.rpm = 6500; src.throttle = 1.0
        for _ in 0..<400 { car.update(dt: 0.02) }
        XCTAssertGreaterThan(car.rpmOut, car.engine.redlineRpm * 0.95,
                             "stretch must reach the top of the SIM engine")

        src.rpm = 760; src.throttle = 0.0
        for _ in 0..<400 { car.update(dt: 0.02) }
        XCTAssertEqual(car.rpmOut, car.engine.idleRpm, accuracy: 80.0)
    }

    func testAShiftCutsTheThrottle() throws {
        let src = ManualSource()
        src.speed = 25; src.throttle = 0.9
        let car = try make(source: src)
        car.start()
        src.rpm = 6400
        car.update(dt: 0.02)
        src.rpm = 4200                            // the collapse of an upshift
        car.update(dt: 0.02)
        XCTAssertTrue(car.shifting, "an upshift must be seen")

        // ...and a LIFT with the same collapse must not be
        let car2 = try make(source: src)
        car2.start()
        src.throttle = 0.0
        src.rpm = 6400; car2.update(dt: 0.02)
        src.rpm = 4200; car2.update(dt: 0.02)
        XCTAssertFalse(car2.shifting, "a lift-off is not a shift")
    }

    func testRendersAudioEndToEnd() throws {
        let src = ManualSource()
        src.rpm = 3000; src.throttle = 0.6
        let car = try make(source: src)
        car.start()
        for _ in 0..<20 { car.update(dt: 0.02) }
        guard let synth = car.synth else { return XCTFail("no synth") }
        var peak: Float = 0
        for _ in 0..<20 {
            for v in synth.render(frames: 256) { peak = max(peak, abs(v)) }
        }
        print("  car mode: peak \(peak) after 20 blocks")
        XCTAssertGreaterThan(peak, 0.01, "the whole path must make SOUND")
        XCTAssertLessThanOrEqual(peak, 1.0, "and stay inside the rails")
    }
}
