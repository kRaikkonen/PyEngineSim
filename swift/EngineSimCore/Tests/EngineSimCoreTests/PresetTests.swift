//
//  PresetTests.swift
//
//  The whole 131-car fleet, not a sample: a port that works on an inline-4 and
//  quietly breaks on a rotary or a radial is exactly what this is for.
//  Reference values come from the Python via tools/make_swift_physics_fixture.py.
//

import XCTest
@testable import EngineSimCore

final class PresetTests: XCTestCase {

    struct Reference: Decodable {
        let name: String?
        let num_cylinders: Int?
        let displacement_m3: Double?
        let firing_order: [Int]?
        let idle_rpm: Double?
        let redline_rpm: Double?
        let cycle_offsets_deg: [Double]?
        let clearance_volume_m3: Double?
        let piston_area_m2: Double?
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

    func testLoadsEveryCar() throws {
        let fleet = try PresetLibrary.load(jsonData: fixture("presets"))
        XCTAssertEqual(fleet.count, 131, "the whole fleet must load")
        for (key, e) in fleet {
            XCTAssertFalse(e.name.isEmpty, "\(key) has no name")
            XCTAssertGreaterThan(e.numCylinders, 0, "\(key) has no cylinders")
            XCTAssertGreaterThan(e.redlineRpm, e.idleRpm, "\(key) rev range")
        }
    }

    /// Geometry is derived, not stored -- so it has to be derived the same way.
    func testDerivedGeometryMatchesPython() throws {
        let fleet = try PresetLibrary.load(jsonData: fixture("presets"))
        let refs = try JSONDecoder().decode([String: Reference].self,
                                            from: fixture("physics"))
        var checked = 0
        for (key, ref) in refs {
            if ref.error != nil { continue }
            guard let e = fleet[key] else {
                XCTFail("\(key) present in the reference but not in presets.json")
                continue
            }
            XCTAssertEqual(e.name, ref.name, "\(key) name")
            XCTAssertEqual(e.numCylinders, ref.num_cylinders, "\(key) cylinders")
            XCTAssertEqual(e.totalDisplacement, ref.displacement_m3!,
                           accuracy: 1e-12, "\(key) displacement")
            XCTAssertEqual(e.firingOrder, ref.firing_order!, "\(key) firing order")
            XCTAssertEqual(e.cylinders[0].clearanceVolume,
                           ref.clearance_volume_m3!, accuracy: 1e-15,
                           "\(key) clearance volume")
            XCTAssertEqual(e.cylinders[0].pistonArea, ref.piston_area_m2!,
                           accuracy: 1e-15, "\(key) piston area")
            XCTAssertEqual(e.cylinders.map { $0.cycleOffsetDeg },
                           ref.cycle_offsets_deg!, "\(key) cycle offsets")
            checked += 1
        }
        XCTAssertEqual(checked, 131, "every car must be checked")
    }
}
