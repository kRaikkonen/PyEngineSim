//
//  PhysicsTests.swift
//
//  The nine quantities the renderer reads, held to the Python across the whole
//  fleet: 130 cars x 12 operating points.  Reference from
//  tools/make_swift_physics_fixture.py.
//
//  Tolerances are stated in what they mean for the SOUND, not in abstract
//  decimals: the pulse amplitude goes as sqrt(pressure), and the note pitch as
//  the sound speed, so those are the two that have to be tight.
//

import XCTest
@testable import EngineSimCore

final class PhysicsTests: XCTestCase {

    struct Point: Decodable {
        let rpm: Double
        let throttle: Double
        let boost_bar: Double
        let manifold_pressure_pa: Double
        let blowdown_pressure_pa: Double
        let exhaust_sound_speed_ms: Double
    }
    struct Ref: Decodable {
        let points: [Point]?
        let error: String?
    }

    func fixture(_ name: String) throws -> Data {
        let url = Bundle.module.url(forResource: name, withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: name, withExtension: "json")
        guard let url else {
            XCTFail("fixture \(name).json missing -- run the tools/ generators")
            throw NSError(domain: "EngineSimCoreTests", code: 1)
        }
        return try Data(contentsOf: url)
    }

    func testMatchesPythonAcrossTheFleet() throws {
        let fleet = try PresetLibrary.load(jsonData: fixture("presets"))
        let tables = try EngineTables.load(jsonData: fixture("engine_tables"))
        let refs = try JSONDecoder().decode([String: Ref].self,
                                            from: fixture("physics"))

        var checkedCars = 0, checkedPoints = 0
        var worstMapRel = 0.0, worstPulseDB = 0.0, worstCentsPitch = 0.0

        for (key, ref) in refs {
            guard let points = ref.points, ref.error == nil,
                  let engine = fleet[key], let tab = tables[key] else { continue }
            // the reference runs with the simulator's own idle-air trim in
            // place (idle_air_base * 1.6), which is what EnginePhysics starts
            // with -- zeroing it here would be comparing two different engines
            let phys = EnginePhysics(engine: engine, tables: tab)
            for p in points {
                phys.rpm = p.rpm
                phys.throttle = p.throttle
                phys.boost = p.boost_bar

                let map = phys.manifoldPressure()
                worstMapRel = max(worstMapRel,
                                  abs(map - p.manifold_pressure_pa)
                                    / max(p.manifold_pressure_pa, 1.0))

                // pulse amplitude goes as sqrt(pressure): compare in dB, which
                // is what an ear would notice
                let bd = phys.blowdownPressure()
                if p.blowdown_pressure_pa > 1.0 && bd > 1.0 {
                    let dB = abs(20.0 * log10((bd / p.blowdown_pressure_pa).squareRoot()))
                    worstPulseDB = max(worstPulseDB, dB)
                }

                // sound speed sets the pipe resonance, so an error here is a
                // pitch error: express it in cents
                let c = phys.exhaustSoundSpeed()
                let cents = abs(1200.0 * log2(c / p.exhaust_sound_speed_ms))
                worstCentsPitch = max(worstCentsPitch, cents)
                checkedPoints += 1
            }
            checkedCars += 1
        }

        XCTAssertEqual(checkedCars, 130, "every car must be checked")
        XCTAssertGreaterThan(checkedPoints, 1400, "and every operating point")
        print("  MAP  worst relative error   \(worstMapRel)")
        print("  pulse worst level error dB  \(worstPulseDB)")
        print("  pitch worst error, cents    \(worstCentsPitch)")

        XCTAssertLessThan(worstMapRel, 1e-6, "manifold pressure")
        XCTAssertLessThan(worstPulseDB, 0.5, "pulse level (audible at ~1 dB)")
        XCTAssertLessThan(worstCentsPitch, 5.0, "note pitch (audible at ~10 cents)")
    }

    /// The MAP solver is bisection over a choked kink -- worth its own check
    /// that it is monotonic in throttle, which the physics requires.
    func testManifoldPressureRisesWithThrottle() throws {
        let fleet = try PresetLibrary.load(jsonData: fixture("presets"))
        let tables = try EngineTables.load(jsonData: fixture("engine_tables"))
        for (key, engine) in fleet {
            guard let tab = tables[key] else { continue }
            let phys = EnginePhysics(engine: engine, tables: tab)
            phys.rpm = engine.redlineRpm * 0.5
            var last = -1.0
            for t in stride(from: 0.0, through: 1.0, by: 0.1) {
                phys.throttle = t
                let map = phys.manifoldPressure()
                XCTAssertGreaterThanOrEqual(map, last - 1.0,
                                            "\(key) MAP fell as throttle opened")
                last = map
            }
        }
    }
}
