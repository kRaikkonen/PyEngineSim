//
//  TripLogTests.swift
//
//  Phase 0 of docs/REDESIGN_V2.md: the trip file is the input the v2 link is
//  designed and regression-tested on, so what is written must be what was
//  said -- every line, in order, stamped on the one monotonic clock -- and the
//  fixes that ride along must hold: a shift is judged between READINGS (not
//  per control tick), a quiet link is not live, one wild rpm is not played.
//

import XCTest
@testable import EngineSimCore

final class TripLogTests: XCTestCase {
    func testLinesComeBackInOrder() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("trip-test-\(UUID().uuidString)")
        let log = TripLog()
        try log.open(directory: dir, name: "t.csv")
        log.line("M", 1.0, "engine=a3")
        log.line("O", 1.25, "1.1000,010C," + TripLog.field("410C0C76\r410C0C70\r\r>"))
        for i in 0..<2000 { log.line("I", 2.0 + Double(i) * 0.01, "0,0,0,0,0,-1,0,0,0") }
        log.line("E", 30.0, "a, b")              // a comma inside a field is the caller's
        XCTAssertGreaterThan(log.bytes, 40000)
        log.close()
        let text = try String(contentsOf: dir.appendingPathComponent("t.csv"), encoding: .utf8)
        let lines = text.split(separator: "\n").map(String.init)
        XCTAssertEqual(lines.first, TripLog.header)
        XCTAssertEqual(lines.count, 1 + 1 + 1 + 2000 + 1)
        XCTAssertEqual(lines[1], "M,1.0000,engine=a3")
        XCTAssertEqual(lines[2], "O,1.2500,1.1000,010C,410C0C76|410C0C70||>")
        XCTAssertEqual(lines[3], "I,2.0000,0,0,0,0,0,-1,0,0,0")
        XCTAssertTrue(lines[2002].hasPrefix("I,21.99"))
        // after close nothing more is written, and nothing crashes
        log.line("E", 31.0, "late")
        XCTAssertFalse(log.isOpen)
        try? FileManager.default.removeItem(at: dir)
    }

    func testTheClockOnlyGoesForward() {
        var last = Clock.now()
        for _ in 0..<1000 {
            let t = Clock.now()
            XCTAssertGreaterThanOrEqual(t, last)
            last = t
        }
    }

    /// Readings every 100 ms, the control tick every 20 ms.  A steady -600
    /// rpm/s decay with the pedal down is NOT a shift (per tick it read as
    /// -3000 and cut the throttle); a 1200 rpm drop between two readings is.
    func testAShiftIsJudgedBetweenReadings() {
        let sd = ShiftDetector()
        var rpm = 4000.0
        var cut = false
        for k in 0..<100 {                       // 2 s
            if k % 5 == 0 {
                rpm -= 60.0                      // -600 rpm/s, seen every 100 ms
                sd.sample(dt: 0.1, rpm: rpm, pedal: 0.6, speed: 20)
            }
            if sd.hold(dt: 0.02) { cut = true }
        }
        XCTAssertFalse(cut, "a slow decay under power is not an upshift")
        XCTAssertEqual(sd.shifts, 0)
        sd.sample(dt: 0.1, rpm: rpm - 1200.0, pedal: 0.6, speed: 20)   // -12000 rpm/s
        XCTAssertTrue(sd.hold(dt: 0.02))
        XCTAssertEqual(sd.shifts, 1)
        // the cut lasts its 0.12 s and ends
        var ticks = 0
        while sd.hold(dt: 0.02) { ticks += 1 }
        XCTAssertLessThanOrEqual(ticks, 7)
        // the same drop with the pedal up is a lift, not a shift
        sd.sample(dt: 0.1, rpm: rpm - 2400.0, pedal: 0.0, speed: 20)
        XCTAssertFalse(sd.hold(dt: 0.02))
        XCTAssertEqual(sd.shifts, 1)
    }

    /// A scripted adapter: answers rpm requests from a list.
    final class ScriptLink: OBDLink {
        var replies: [String]
        var i = 0
        init(_ r: [String]) { replies = r }
        func write(_ s: String) throws {}
        func readPrompt(_ timeout: TimeInterval) -> String {
            defer { i += 1 }
            return i < replies.count ? replies[i] : ""
        }
        func close() {}
    }

    func testOneWildReadingIsNotPlayed() {
        let t = OBDTelemetry(link: ScriptLink([]))
        func rpm(_ v: Double) -> [Int: [Int]] {
            let q = Int(v * 4)
            return [0x0C: [q >> 8, q & 0xFF]]
        }
        var now = 100.0
        for v in [2000.0, 2010.0, 2020.0] {
            t.ingest(rpm(v), now: now, sent: now - 0.08); now += 0.1
        }
        XCTAssertEqual(t.rawRPM, 2020)
        t.ingest(rpm(6100), now: now, sent: now - 0.08); now += 0.1     // a corrupted frame
        XCTAssertEqual(t.rawRPM, 2020, "one wild value is not played")
        XCTAssertEqual(t.maxRPM, 6500, "...and not learned from")
        t.ingest(rpm(2030), now: now, sent: now - 0.08); now += 0.1
        XCTAssertEqual(t.rawRPM, 2030)
        // a REAL jump (a downshift blip) is believed on its second reading
        t.ingest(rpm(5200), now: now, sent: now - 0.08); now += 0.1
        XCTAssertEqual(t.rawRPM, 2030)
        t.ingest(rpm(5250), now: now, sent: now - 0.08)
        XCTAssertEqual(t.rawRPM, 5250)
    }
}
