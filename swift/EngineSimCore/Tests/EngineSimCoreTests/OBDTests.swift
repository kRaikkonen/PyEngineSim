//
//  OBDTests.swift
//
//  The frame cases are the ones that actually break parsers, not a happy path:
//  a 0x41 appearing as a DATA byte (which a parser that searches for "41xx"
//  turns into a phantom PID), CAN padding after an ISO-TP length header, a
//  multi-PID reply with ONE header rather than one per PID, and the adapter's
//  "SEARCHING..." chatter.
//
//  The shift detector is given a lift-off with the SAME rpm collapse as an
//  upshift and must not fire on it -- that is the whole reason the pedal test
//  exists, so a test that only feeds it upshifts would prove nothing.
//

import XCTest
@testable import EngineSimCore

final class OBDTests: XCTestCase {
    struct Frame: Decodable {
        let name: String, raw: String, hex: String
        let mask: Bool
        let pids: [String: [Int]]
    }
    struct Mask: Decodable { let data: [Int], base: Int, out: [Int] }
    struct MapCase: Decodable { let mode: String, ratio: Double
                                let points: [[Double]] }
    struct Learn: Decodable {
        let seq: [[Double]], car_idle: Double, car_redline: Double
        let seen_max: Double
    }
    struct Ref: Decodable {
        let frames: [Frame], masks: [Mask], maps: [MapCase], learn: Learn
        let drive: [[Double]], gears: [Int]
        let shifts: [[Shift]], shift_count: Int
    }
    enum Shift: Decodable {
        case n(Double), b(Bool)
        init(from d: Decoder) throws {
            let c = try d.singleValueContainer()
            if let v = try? c.decode(Bool.self) { self = .b(v) }
            else { self = .n(try c.decode(Double.self)) }
        }
        var number: Double { if case .n(let v) = self { return v }; return 0 }
        var flag: Bool { if case .b(let v) = self { return v }; return false }
    }

    func load() throws -> Ref {
        let url = Bundle.module.url(forResource: "obd", withExtension: "json",
                                    subdirectory: "Fixtures")
            ?? Bundle.module.url(forResource: "obd", withExtension: "json")
        guard let url else { throw XCTSkip("obd.json missing") }
        return try JSONDecoder().decode(Ref.self, from: Data(contentsOf: url))
    }

    func testFrameParsing() throws {
        let ref = try load()
        for f in ref.frames {
            XCTAssertEqual(OBDParse.cleanFrames(f.raw), f.hex,
                           "cleaning: \(f.name)")
            let lengths = f.mask ? [0x00: 4, 0x20: 4, 0x40: 4] : pidLength
            let got = OBDParse.pids(f.hex, lengths: lengths)
            let want = Dictionary(uniqueKeysWithValues:
                f.pids.map { (Int($0.key)!, $0.value) })
            XCTAssertEqual(got, want, "parsing: \(f.name)")
        }
        print("  OBD frames: \(ref.frames.count) cases")
    }

    func testSupportedMasks() throws {
        for m in try load().masks {
            XCTAssertEqual(OBDParse.supported(m.data, base: m.base).sorted(),
                           m.out, "mask base \(m.base)")
        }
    }

    func testRpmMapModes() throws {
        for c in try load().maps {
            let mode = RpmMap.Mode(rawValue: c.mode)!
            let m = RpmMap(mode: mode, carIdle: 760, carRedline: 6500,
                           ratio: c.ratio)
            for p in c.points {
                XCTAssertEqual(m(p[0], engIdle: 900, engRedline: 8500), p[1],
                               accuracy: 1e-12, "\(c.mode) at \(p[0])")
            }
        }
    }

    func testRpmMapLearnsPastItsSeed() throws {
        let ref = try load()
        let m = RpmMap(mode: .stretch, carIdle: 800, carRedline: 6000)
        for s in ref.learn.seq { m.observe(rpm: s[0], pedal: s[1]) }
        // the seed said 6000 and the car went to 7100: the seed must lose,
        // or the top third of the rev range stays squashed forever
        XCTAssertEqual(m.carRedline, ref.learn.car_redline, accuracy: 1e-12)
        XCTAssertEqual(m.carIdle, ref.learn.car_idle, accuracy: 1e-12)
        XCTAssertEqual(m.seenMax, ref.learn.seen_max, accuracy: 1e-12)
        XCTAssertGreaterThan(m.carRedline, 6000.0, "the seed must be beaten")
    }

    /// The property that gives the mode its name, stated as Leo did: my car
    /// redlines at 6500 and the target at 9500, so at 6500 the note must be at
    /// 9500.  Both stretch and ratio do that; what separates them is the
    /// MIDDLE -- ratio goes through zero and keeps the proportion, stretch
    /// pins the idle ends together too.
    func testBothRedlineModesLineUpTheCeilings() {
        let carRedline = 6500.0, simIdle = 900.0, simRedline = 9500.0
        for mode in [RpmMap.Mode.ratio, .stretch] {
            let m = RpmMap(mode: mode, carIdle: 760, carRedline: carRedline)
            XCTAssertEqual(m(carRedline, engIdle: simIdle,
                             engRedline: simRedline), simRedline,
                           accuracy: 1.0, "\(mode) must reach the ceiling")
        }
        // ratio is proportional, so half the revs is half the note
        let r = RpmMap(mode: .ratio, carIdle: 760, carRedline: carRedline)
        let full = r(carRedline, engIdle: simIdle, engRedline: simRedline)
        let half = r(carRedline / 2, engIdle: simIdle, engRedline: simRedline)
        XCTAssertEqual(half, full / 2, accuracy: 1.0,
                       "ratio must go THROUGH ZERO, or it is just stretch")
        // stretch is not, because it pins the idle end
        let st = RpmMap(mode: .stretch, carIdle: 760, carRedline: carRedline)
        XCTAssertEqual(st(760, engIdle: simIdle, engRedline: simRedline),
                       simIdle, accuracy: 1.0, "stretch pins idle to idle")
        XCTAssertGreaterThan(r(760, engIdle: simIdle, engRedline: simRedline),
                             simIdle + 100, "...and ratio does not")
    }

    /// A value set by hand and an automatic value that overwrites it are a
    /// fight the automatic one always wins -- and it reads as a bug, not as a
    /// feature.  So a hand-set range must SURVIVE the car revving past it.
    func testAHandSetRangeIsNotOverwrittenByLearning() {
        let learning = RpmMap(mode: .stretch, carIdle: 800, carRedline: 6000,
                              learn: true)
        learning.observe(rpm: 7200, pedal: 1.0)
        XCTAssertEqual(learning.carRedline, 7200, accuracy: 1e-9,
                       "learning on: the car beats the seed")

        let byHand = RpmMap(mode: .stretch, carIdle: 800, carRedline: 6000,
                            learn: false)
        byHand.observe(rpm: 7200, pedal: 1.0)
        XCTAssertEqual(byHand.carRedline, 6000, accuracy: 1e-9,
                       "learning off: the number you set STAYS set")
        byHand.observe(rpm: 700, pedal: 0.0)
        XCTAssertEqual(byHand.carIdle, 800, accuracy: 1e-9,
                       "...and that goes for the idle end too")

        // and it can be handed back
        byHand.learn = true
        byHand.observe(rpm: 7200, pedal: 1.0)
        XCTAssertEqual(byHand.carRedline, 7200, accuracy: 1e-9)
    }

    /// The redline is what the mapping stretches TO, so changing it must
    /// actually move the note.
    func testTheRedlineChangesWhatIsPlayed() {
        let low = RpmMap(mode: .stretch, carIdle: 760, carRedline: 6500)
        let high = RpmMap(mode: .stretch, carIdle: 760, carRedline: 8000)
        // same real 5000 rpm, two different beliefs about where the car's
        // redline is: the one that thinks the car revs further plays LOWER,
        // because 5000 is a smaller fraction of its range
        let a = low(5000, engIdle: 900, engRedline: 8500)
        let b = high(5000, engIdle: 900, engRedline: 8500)
        XCTAssertGreaterThan(a, b + 500)
        // and both still land on the sim's own redline when the car is at its
        XCTAssertEqual(low(6500, engIdle: 900, engRedline: 8500), 8500,
                       accuracy: 1.0)
        XCTAssertEqual(high(8000, engIdle: 900, engRedline: 8500), 8500,
                       accuracy: 1.0)
    }

    func testGearLearner() throws {
        let ref = try load()
        let gl = GearLearner()
        var mismatches = 0
        for (i, d) in ref.drive.enumerated() {
            let g = gl.update(rpm: d[0], speedKmh: d[1])
            if g != ref.gears[i] { mismatches += 1 }
        }
        print("  gear learner: \(ref.drive.count) samples, "
              + "\(Set(ref.gears).count) distinct gears, \(mismatches) mismatches")
        XCTAssertEqual(mismatches, 0)
        XCTAssertGreaterThan(Set(ref.gears).count, 3, "the drive must SHIFT")
    }

    func testShiftDetectorIgnoresALiftOff() throws {
        let ref = try load()
        let sd = ShiftDetector()
        var fired = 0
        for (i, s) in ref.shifts.enumerated() {
            let got = sd.update(dt: 0.02, rpm: s[0].number, pedal: s[1].number,
                                speed: 20.0)
            XCTAssertEqual(got, s[2].flag, "sample \(i)")
            if got { fired += 1 }
        }
        XCTAssertEqual(sd.shifts, ref.shift_count)
        // two upshifts and one lift-off with an identical rpm collapse
        XCTAssertEqual(sd.shifts, 2, "the lift-off must NOT count as a shift")
        XCTAssertGreaterThan(fired, 0)
    }
}
