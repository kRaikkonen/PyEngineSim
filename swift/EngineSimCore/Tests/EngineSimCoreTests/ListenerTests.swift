//
//  ListenerTests.swift
//
//  All three perspectives, because they run almost entirely different code:
//  only the cockpit has a partition, a structure path and a cabin boom; only
//  the chase has a ground reflection; only trackside has the fly-by.  Verify
//  one POV and two thirds of this stage is untested.
//
//  Both ends of the noise-reduction range are here too -- an F1 shell that
//  barely encloses the driver, and a truck cab that seals -- because the
//  partition is where "a race cockpit is violent and a luxury one is hushed"
//  either falls out of the physics or does not.
//

import XCTest
@testable import EngineSimCore

final class ListenerTests: XCTestCase {
    struct RngState: Decodable { let state: [String]; let spare: Double? }
    struct Block: Decodable {
        let rpm: Double, throttle: Double, speed: Double, dps: Double
        let crank: Double, comb_load: Double, inj_amt: Double, track_x: Double
        let sig: [Double], bay: [Double], bayi: [Double]
        let rng: RngState
        let cache: [String: [[Double]]]
        let cache_after: [String: [[Double]]]
        let taps: [String: [Double]]
    }
    struct Case: Decodable {
        let blocks: [Block]; let pov: String; let params: [String: Double]
    }

    func residualDB(_ got: [Double], _ want: [Double]) -> Double {
        guard got.count == want.count, !want.isEmpty else { return .infinity }
        var num = 0.0, den = 0.0
        for i in want.indices { let d = got[i] - want[i]; num += d * d; den += want[i] * want[i] }
        if den <= 0 { return num <= 0 ? -.infinity : .infinity }
        return 10.0 * log10(max(num / den, 1e-300))
    }

    func testListenerMatchesPython() throws {
        func fixture(_ name: String) throws -> Data {
            let url = Bundle.module.url(forResource: name, withExtension: "json",
                                        subdirectory: "Fixtures")
                ?? Bundle.module.url(forResource: name, withExtension: "json")
            guard let url else { throw XCTSkip("\(name).json missing") }
            return try Data(contentsOf: url)
        }
        let refs = try JSONDecoder().decode([String: Case].self,
                                            from: fixture("listener"))
        let fleet = try PresetLibrary.load(jsonData: try fixture("presets"))

        var worstEQ = -Double.infinity, worstRoom = -Double.infinity
        var whereEQ = "", whereRoom = ""
        var blocks = 0, povsSeen = Set<String>()

        for (name, c) in refs.sorted(by: { $0.key < $1.key }) {
            let carKey = String(name.split(separator: "-")[0])
            guard let eng = fleet[carKey] else { XCTFail("missing \(carKey)"); continue }
            let rate = 32000.0
            let cache = FilterCache(sampleRate: rate)
            let rng = PortableRNG(seed: 1)
            let layers = LayerStack()
            let stage = ListenerStage(engine: eng, sampleRate: rate,
                                      cache: cache, rng: rng, layers: layers)
            stage.pov = c.pov
            povsSeen.insert(c.pov)

            for b in c.blocks {
                cache.preload(b.cache); cache.preload(b.cache_after)
                rng.restore(state: b.rng.state.compactMap { UInt64($0) },
                            spare: b.rng.spare)
                stage.trackX = b.track_x

                var s = ListenerState()
                s.rpm = b.rpm; s.speed = b.speed
                s.degPerSample = b.dps; s.crank = b.crank
                s.combLoad = b.comb_load; s.injAmt = b.inj_amt

                let out = stage.process(b.sig, bay: b.bay, bayi: b.bayi,
                                        state: s, params: c.params)
                if let want = b.taps["EQ"], let mine = layers.lastValue(.eq) {
                    let db = residualDB(mine, want)
                    if db > worstEQ { worstEQ = db; whereEQ = name }
                }
                if let want = b.taps["cabin/room"] {
                    let db = residualDB(out, want)
                    if db > worstRoom { worstRoom = db; whereRoom = name }
                }
                blocks += 1
            }
        }

        print("  listener: \(refs.count) cases, \(blocks) blocks, "
              + "\(povsSeen.sorted().joined(separator: "/"))")
        print(String(format: "    EQ         worst %7.1f dB (%@)", worstEQ, whereEQ))
        print(String(format: "    cabin/room worst %7.1f dB (%@)", worstRoom, whereRoom))
        XCTAssertEqual(povsSeen.count, 3, "every perspective must be exercised")
        XCTAssertGreaterThan(blocks, 50)
        XCTAssertLessThan(worstEQ, -100.0, "EQ drifted (\(whereEQ))")
        XCTAssertLessThan(worstRoom, -100.0, "listener drifted (\(whereRoom))")
    }

    /// The geometry itself, spot-checked against the numbers the physics says.
    func testGeometryIsDerivedNotChosen() throws {
        let url = Bundle.module.url(forResource: "presets", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "presets", withExtension: "json")
        guard let url else { throw XCTSkip("presets.json missing") }
        let fleet = try PresetLibrary.load(jsonData: try Data(contentsOf: url))
        guard let rs3 = fleet["rs3"] else { throw XCTSkip("no rs3") }
        let stage = ListenerStage(engine: rs3, sampleRate: 32000,
                                  cache: FilterCache(sampleRate: 32000),
                                  rng: PortableRNG(seed: 1), layers: LayerStack())

        stage.pov = "cockpit"
        let cab = stage.geometry()
        // the cabin's lowest longitudinal mode, c / 2L with L = 2.4 m
        XCTAssertEqual(cab.boomF, 343.0 / 4.8, accuracy: 1e-9)
        // spreading between the two radiators, 1.5 m and 3.2 m away
        XCTAssertEqual(cab.gTail, 1.5 / 3.2, accuracy: 1e-12)
        // the mass law's TL = 20 dB corner for an 11 kg/m^2 firewall
        XCTAssertEqual(cab.bayFc, 2238.7 / 11.0, accuracy: 1e-9)
        XCTAssertNil(cab.ground, "a cockpit has no tarmac bounce")

        stage.pov = "chase"
        let chase = stage.geometry()
        XCTAssertNotNil(chase.ground, "the chase cam does")
        XCTAssertEqual(chase.boomF, 0.0)
        // 6 m back, the bay 4.5 m further: 1/r between them
        XCTAssertEqual(chase.gBay, 6.0 / 10.5, accuracy: 1e-12)

        stage.pov = "trackside"
        let track = stage.geometry()
        XCTAssertTrue(track.flyby)
        XCTAssertNil(track.tailFc, "no partition between a car and a field")
    }
}
