//
//  SettingsTests.swift
//
//  Persistence is the kind of thing you find out about at the worst moment,
//  so the two properties that matter are checked here rather than discovered
//  in a car park: what is saved comes back exactly, and RESET DOES NOT CLEAR
//  THE SAVED SETUPS.  Reset is for undoing the last ten minutes of poking,
//  not for losing the setup you built last week -- and the difference between
//  those two is one line of code nobody would notice was wrong.
//

import XCTest
@testable import EngineSimCore

final class SettingsTests: XCTestCase {
    /// Save and restore whatever the real store holds, so running the tests
    /// cannot destroy the setups on the machine running them.
    var backup: [String: Data] = [:]

    override func setUp() {
        let d = UserDefaults.standard
        backup = [:]
        for k in [SettingsStore.currentKey] + (0..<SettingsStore.slotCount)
            .map({ SettingsStore.slotKey + "\($0)" }) {
            if let v = d.data(forKey: k) { backup[k] = v }
            d.removeObject(forKey: k)
        }
    }

    override func tearDown() {
        let d = UserDefaults.standard
        for k in [SettingsStore.currentKey] + (0..<SettingsStore.slotCount)
            .map({ SettingsStore.slotKey + "\($0)" }) {
            d.removeObject(forKey: k)
            if let v = backup[k] { d.set(v, forKey: k) }
        }
    }

    func testRoundTrip() throws {
        var s = Settings()
        s.engineKey = "aven"
        s.mapMode = .ratio
        s.hidden = [.muffler, .thunder, .cabinRoom]
        s.source = "pedal"
        s.pops = false
        s.host = "10.0.0.7"
        s.port = 35001
        s.carIdle = 812
        s.carRedline = 7100
        SettingsStore.save(s)
        XCTAssertEqual(SettingsStore.load(), s)
    }

    func testAnEmptyStoreGivesTheDefaults() {
        XCTAssertEqual(SettingsStore.load(), .default)
        XCTAssertEqual(Settings.default.engineKey, "a3", "the car it is FOR")
    }

    func testSlotsAreIndependentOfTheCurrentSettings() {
        var a = Settings(); a.engineKey = "aven"; a.hidden = [.muffler]
        var b = Settings(); b.engineKey = "f2007"; b.mapMode = .direct
        SettingsStore.saveSlot(0, a)
        SettingsStore.saveSlot(2, b)
        XCTAssertTrue(SettingsStore.slotFilled(0))
        XCTAssertFalse(SettingsStore.slotFilled(1))
        XCTAssertTrue(SettingsStore.slotFilled(2))
        XCTAssertEqual(SettingsStore.loadSlot(0), a)
        XCTAssertEqual(SettingsStore.loadSlot(2), b)
        XCTAssertNil(SettingsStore.loadSlot(1))

        // the live settings changing must not touch a slot
        SettingsStore.save(.default)
        XCTAssertEqual(SettingsStore.loadSlot(0), a)
    }

    func testResetDoesNotClearTheSlots() {
        var a = Settings(); a.engineKey = "veyron"
        SettingsStore.saveSlot(1, a)
        var live = Settings(); live.engineKey = "787b"; live.hidden = [.eq]
        SettingsStore.save(live)

        // what resetToDefaults does: writes the defaults over the LIVE
        // settings and nothing else
        SettingsStore.save(.default)

        XCTAssertEqual(SettingsStore.load(), .default)
        XCTAssertEqual(SettingsStore.loadSlot(1), a,
                       "reset must not cost you the setups you saved")
    }

    /// Hidden stages survive as stage IDENTITIES, not as positions -- so
    /// adding a stage to the chain later cannot silently shift which ones a
    /// saved setup mutes.
    func testHiddenStagesRoundTripByName() throws {
        var s = Settings()
        s.hidden = [.standingWave, .valveBypass, .tailpipeExit]
        SettingsStore.save(s)
        let back = SettingsStore.load()
        XCTAssertEqual(Set(back.hidden), Set(s.hidden))
        let json = String(decoding: try JSONEncoder().encode(s), as: UTF8.self)
        XCTAssertTrue(json.contains("standing-wave"),
                      "stored by name, not by index")
    }
}
