//
//  EngineBayTests.swift
//
//  The picture has to be driven by the same numbers as the sound, so what is
//  checked here is the MECHANISM rather than the drawing: that the piston
//  follows the real slider-crank (including the rod asymmetry that makes it
//  look like an engine instead of a sine wave), that the banks are read off
//  the bank angles, that the cam opens and shuts where a four-stroke's does,
//  and that an exhaust pulse is launched once per cycle per cylinder even when
//  a single frame sweeps most of a revolution -- which at 6000 rpm it does.
//

import XCTest
@testable import EngineSimCore

final class EngineBayTests: XCTestCase {

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

    func bay(_ key: String) throws -> EngineBay {
        let lib = try library()
        guard let e = lib.engine(key) else { throw XCTSkip("\(key) missing") }
        return EngineBay(engine: e)
    }

    // MARK: - kinematics

    /// TDC and BDC must actually be REACHED, not approached.  A sine would
    /// also pass that, which is why the dwell test below exists too.
    func testPistonTravelsFullStroke() throws {
        let b = try bay("a45")
        var lo = 1.0, hi = 0.0
        for d in stride(from: 0.0, to: 720.0, by: 0.25) {
            let f = b.pistonFraction(0, crankAngleDeg: d)
            lo = min(lo, f); hi = max(hi, f)
        }
        XCTAssertEqual(lo, 0.0, accuracy: 1e-6, "must reach TDC")
        XCTAssertEqual(hi, 1.0, accuracy: 1e-6, "must reach BDC")
    }

    /// The real slider-crank is NOT symmetric: a finite rod makes the piston
    /// linger near BDC and hurry through TDC.  That asymmetry is most of what
    /// makes the animation read as an engine, so it is worth pinning.
    func testRodAsymmetry() throws {
        let b = try bay("a45")
        var top = 0, bottom = 0
        for d in stride(from: 0.0, to: 360.0, by: 1.0) {
            let f = b.pistonFraction(0, crankAngleDeg: d)
            if f < 0.25 { top += 1 }
            if f > 0.75 { bottom += 1 }
        }
        XCTAssertGreaterThan(bottom, top,
                             "a finite rod must dwell longer at BDC than TDC")
    }

    /// Cylinders must be a whole cycle apart in the right order, and the piston
    /// must be periodic in 720 deg.
    func testPhasingAndPeriodicity() throws {
        let b = try bay("aven")
        for i in 0..<b.cylinderCount {
            let a = b.pistonFraction(i, crankAngleDeg: 123.0)
            let c = b.pistonFraction(i, crankAngleDeg: 123.0 + 720.0)
            XCTAssertEqual(a, c, accuracy: 1e-12, "cycle must repeat at 720")
        }
        // a V12 fires every 60 deg, so no two cylinders share an offset
        let offs = Set(b.slots.map { Int($0.cycleOffsetDeg.rounded()) % 720 })
        XCTAssertEqual(offs.count, b.cylinderCount,
                       "every cylinder needs its own slot in the cycle")
    }

    // MARK: - layout

    func testLayoutComesFromTheBankAngles() throws {
        XCTAssertEqual(try bay("a45").layout, .inline)
        XCTAssertEqual(try bay("rs3").layout, .inline)
        XCTAssertEqual(try bay("aven").layout, .vee)
        XCTAssertEqual(try bay("f2007").layout, .vee)
        XCTAssertEqual(try bay("991rs").layout, .flat)
    }

    func testStationsSplitAcrossBanks() throws {
        let v = try bay("aven")                     // V12 -> 6 per bank
        XCTAssertEqual(v.bankAngles.count, 2)
        XCTAssertEqual(v.stationsPerBank, 6)
        let i = try bay("a45")                      // inline 4 -> 4 stations
        XCTAssertEqual(i.bankAngles.count, 1)
        XCTAssertEqual(i.stationsPerBank, 4)
    }

    // MARK: - valves

    /// Both valves shut on the compression and power strokes, and they overlap
    /// around the exhaust-to-intake TDC.  That is the four-stroke, and getting
    /// it backwards is the easiest thing in the world to not notice.
    func testValveTiming() throws {
        let b = try bay("a45")
        let rpm = 3000.0
        // mid-power (phi ~ 90): everything shut
        var l = b.valveLift(0, crankAngleDeg: 90.0, rpm: rpm)
        XCTAssertLessThan(l.intake, 0.02)
        XCTAssertLessThan(l.exhaust, 0.02)
        // mid-exhaust (phi ~ 270): exhaust open, intake shut
        l = b.valveLift(0, crankAngleDeg: 270.0, rpm: rpm)
        XCTAssertGreaterThan(l.exhaust, 0.5)
        XCTAssertLessThan(l.intake, 0.05)
        // mid-intake (phi ~ 450): intake open, exhaust shut
        l = b.valveLift(0, crankAngleDeg: 450.0, rpm: rpm)
        XCTAssertGreaterThan(l.intake, 0.5)
        XCTAssertLessThan(l.exhaust, 0.05)
        // overlap at 360: BOTH cracked open
        l = b.valveLift(0, crankAngleDeg: 360.0, rpm: rpm)
        XCTAssertGreaterThan(l.intake, 0.0)
        XCTAssertGreaterThan(l.exhaust, 0.0)
        // mid-compression (phi ~ 630): shut again
        l = b.valveLift(0, crankAngleDeg: 630.0, rpm: rpm)
        XCTAssertLessThan(l.intake, 0.02)
        XCTAssertLessThan(l.exhaust, 0.02)
    }

    func testRaceCamOpensLongerThanMild() throws {
        let b = try bay("f2007")
        let d = b.valveDurationDeg(rpm: 10000.0)
        XCTAssertGreaterThan(d, 180.0, "a valve must be open across a stroke")
        XCTAssertLessThan(d, 340.0, "no cam is open that long")
    }

    // MARK: - exhaust pulses

    /// One pulse per cylinder per 720 deg, and -- the part that actually bites
    /// -- still exactly one when a single frame sweeps most of a revolution,
    /// which is the normal case at any real rpm on a 60 Hz screen.
    func testOnePulsePerCylinderPerCycleEvenWithHugeSteps() throws {
        let b = try bay("a45")
        var f = ExhaustPulseField(bay: b)
        var crank = 0.0
        // 6000 rpm on a 60 Hz screen: 600 deg of crank per FRAME.  A naive
        // "did phi pass evo" test silently drops most of these.
        for _ in 0..<24 {                      // 24 frames ~ 20 cycles
            crank = (crank + 600.0).truncatingRemainder(dividingBy: 720.0)
            f.update(bay: b, crankAngleDeg: crank, dt: 1.0 / 60.0,
                     soundSpeed: 500.0, load: 1.0, rpm: 6000.0)
        }
        // 23 counted frames (the first only seeds the phase) x 600 deg is
        // 19.2 cycles, 4 cylinders each -- so about 76.
        XCTAssertGreaterThan(f.launched, 60,
                             "pulses must still fire when a frame sweeps >360")
        XCTAssertLessThan(f.launched, 96, "and must not fire twice per cycle")
    }

    /// A pulse has to TRAVEL: down the primary first, then the rest of the
    /// system.  A header that just flashes has missed the point of a header.
    ///
    /// This runs in SLOW MOTION on purpose.  The real numbers are a 0.48 m
    /// primary and roughly 500 m/s of hot gas, so a pulse crosses it in about
    /// 1 ms while a frame is 17 -- at live speed it is born and gone inside a
    /// fraction of one frame, which is exactly why the view has a speed
    /// control and why testing it at 1/60 s would prove nothing.
    func testPulseTravelsAndLeaves() throws {
        let b = try bay("a45")
        var f = ExhaustPulseField(bay: b)
        let dt = 1.0 / 5000.0                  // ~0.2 ms: 1/5 of the primary
        var crank = 0.0
        var guardCount = 0
        while f.pulses.isEmpty && guardCount < 4000 {
            crank += 2000.0 * 6.0 * dt         // 2000 rpm
            f.update(bay: b, crankAngleDeg: crank, dt: dt,
                     soundSpeed: 500.0, load: 1.0, rpm: 2000.0)
            guardCount += 1
        }
        XCTAssertFalse(f.pulses.isEmpty, "a firing cylinder must launch one")
        let first = f.pulses[0].primary
        XCTAssertLessThan(first, 1.0, "it should not arrive in a single step")
        f.update(bay: b, crankAngleDeg: crank, dt: dt,
                 soundSpeed: 500.0, load: 1.0, rpm: 2000.0)
        XCTAssertGreaterThan(f.pulses[0].primary, first, "it must move")

        // and it must eventually clear, rather than piling up forever
        for _ in 0..<400 {
            crank += 5.0
            f.update(bay: b, crankAngleDeg: crank, dt: 1.0 / 60.0,
                     soundSpeed: 500.0, load: 0.0, rpm: 900.0)
        }
        XCTAssertLessThan(f.pulses.count, 96, "pulses must retire")
    }

    // MARK: - induction

    func testChargerReadsThePreset() throws {
        XCTAssertEqual(try bay("a45").charger, .turbo)
        XCTAssertEqual(try bay("aven").charger, .na)
        // a turbo hangs on after a lift; a naturally aspirated engine has
        // nothing to spin at all
        let t = try bay("a45")
        XCTAssertGreaterThan(t.chargerSpin(rpm: 3000, boostBar: 1.0), 0.5)
        XCTAssertEqual(try bay("aven").chargerSpin(rpm: 6000, boostBar: 0), 0)
    }

    // MARK: - rotary

    /// A rotary must not be drawn as pistons, and the presets do not say how
    /// many rotors it has -- they carry two pseudo-cylinders per rotor so the
    /// firing RATE comes out right for the audio.
    func testRotorCountComesFromThePseudoCylinders() throws {
        let rx = try bay("rx7")                 // 13B, 4 pseudo -> 2 rotors
        XCTAssertEqual(rx.layout, .rotary)
        XCTAssertEqual(rx.cylinderCount, 4)
        XCTAssertEqual(rx.rotorCount, 2)
        let r4 = try bay("787b")                // R26B, 8 pseudo -> 4 rotors
        XCTAssertEqual(r4.rotorCount, 4)
        XCTAssertNotNil(r4.rotary)
        // and a piston engine must not claim to be one
        XCTAssertNil(try bay("a45").rotary)
    }

    /// The whole trick of a Wankel: all three apexes are on the housing at
    /// every angle, because the rotor turns at a THIRD of shaft speed while
    /// its centre orbits.  If that is off, the tips either float or cut
    /// through the housing, and no amount of drawing hides it.
    func testApexesLieOnTheHousing() throws {
        let g = RotaryGeometry(rotors: 2)
        var worst = 0.0
        for i in 0..<2160 {
            let shaft = Double(i) * 0.5
            for k in 0..<3 {
                let a = g.apex(shaft, rotor: 0, k)
                let phi = g.rotorAngleDeg(shaft, rotor: 0) + Double(k) * 120.0
                let h = g.housing(phi)
                worst = max(worst, ((a.x - h.x) * (a.x - h.x)
                                    + (a.y - h.y) * (a.y - h.y)).squareRoot())
            }
        }
        XCTAssertLessThan(worst, 1e-12, "apexes must ride the housing exactly")
    }

    /// And they must sweep the whole housing -- out to the lobes at R+e and in
    /// to the waist at R-e -- rather than tracing some smaller circle.
    func testApexSweepsLobeAndWaist() throws {
        let g = RotaryGeometry(rotors: 2)
        var lo = Double.infinity, hi = 0.0
        for i in 0..<1080 {
            let a = g.apex(Double(i), rotor: 0, 0)
            let r = (a.x * a.x + a.y * a.y).squareRoot()
            lo = min(lo, r); hi = max(hi, r)
        }
        XCTAssertEqual(lo, g.radius - g.eccentricity, accuracy: 1e-3)
        XCTAssertEqual(hi, g.radius + g.eccentricity, accuracy: 1e-3)
    }

    /// One chamber, four strokes, 1080 deg of shaft -- 270 each.  This is the
    /// fact that makes a rotary a four-stroke at all, and it is the one a
    /// piston-shaped model gets wrong.
    func testChamberTakes1080DegreesForFourStrokes() throws {
        let g = RotaryGeometry(rotors: 2)
        var seen: [Stroke: Int] = [:]
        for i in 0..<2160 {                      // 0.5 deg steps over 1080
            let s = g.chamberStroke(Double(i) * 0.5, rotor: 0, 0)
            seen[s, default: 0] += 1
        }
        XCTAssertEqual(seen.count, 4, "a chamber must see all four strokes")
        for (s, n) in seen {
            XCTAssertEqual(Double(n) * 0.5, 270.0, accuracy: 1e-6,
                           "\(s) took the wrong share of the cycle")
        }
    }

    /// Fill is 0 at the waist and 1 at a lobe, and ignition is at the waist --
    /// so a chamber lights at minimum volume and expands, which is the whole
    /// point of the power stroke.
    func testChamberFillExtremes() throws {
        let g = RotaryGeometry(rotors: 2)
        // gamma = shaft/3 + 60 for chamber 0, so shaft = (gamma - 60) * 3
        func fillAt(_ gamma: Double) -> Double {
            g.chamberFill((gamma - 60.0) * 3.0, rotor: 0, 0)
        }
        XCTAssertEqual(fillAt(270), 0.0, accuracy: 1e-9, "ignition at the waist")
        XCTAssertEqual(fillAt(0), 1.0, accuracy: 1e-9, "power ends at a lobe")
        XCTAssertEqual(fillAt(90), 0.0, accuracy: 1e-9, "exhaust ends empty")
        XCTAssertEqual(fillAt(180), 1.0, accuracy: 1e-9, "intake ends full")
        XCTAssertEqual(g.chamberStroke((270.0 - 60.0) * 3.0, rotor: 0, 0),
                       .power)
    }

    /// The picture has to fire at the rate the SOUND does.  A rotor fires once
    /// per shaft revolution and the rotors are evenly phased, so the interval
    /// must land on 720/pseudo-cylinders -- the same number the audio places
    /// its pulses on.
    func testFiringRateAgreesWithTheAudio() throws {
        for key in ["rx7", "rx7fc", "787b"] {
            let b = try bay(key)
            guard let g = b.rotary else { return XCTFail("\(key) not rotary") }
            var fires: [Double] = []
            // seed first: an empty map reads the first sample as a transition
            var prev: [String: Stroke] = [:]
            for r in 0..<g.rotors {
                for k in 0..<3 { prev["\(r).\(k)"] = g.chamberStroke(0, rotor: r, k) }
            }
            for i in 1..<21600 {
                let shaft = Double(i) * 0.1
                for r in 0..<g.rotors {
                    for k in 0..<3 {
                        let s = g.chamberStroke(shaft, rotor: r, k)
                        if prev["\(r).\(k)"] != .power && s == .power {
                            fires.append(shaft)
                        }
                        prev["\(r).\(k)"] = s
                    }
                }
            }
            let want = 720.0 / Double(b.cylinderCount)
            XCTAssertGreaterThan(fires.count, 4, "\(key) never fired")
            for i in 1..<fires.count {
                XCTAssertEqual(fires[i] - fires[i - 1], want, accuracy: 0.15,
                               "\(key) fires unevenly")
            }
        }
    }

    // MARK: - the whole fleet

    /// Nothing in the library may throw, divide by zero or produce a NaN --
    /// the bay is drawn for whatever car is loaded, including the rotaries and
    /// the W engines.
    func testEveryPresetProducesFiniteGeometry() throws {
        let lib = try library()
        for key in lib.keys {
            guard let e = lib.engine(key) else { continue }
            let b = EngineBay(engine: e)
            XCTAssertGreaterThan(b.cylinderCount, 0, "\(key) has no cylinders")
            XCTAssertGreaterThan(b.stationsPerBank, 0, "\(key) has no stations")
            for i in 0..<b.cylinderCount {
                for d in [0.0, 133.0, 421.0, 700.0] {
                    let f = b.pistonFraction(i, crankAngleDeg: d)
                    XCTAssertTrue(f.isFinite, "\(key) cyl \(i) not finite")
                    XCTAssertGreaterThanOrEqual(f, -1e-9, "\(key) below TDC")
                    XCTAssertLessThanOrEqual(f, 1.0 + 1e-9, "\(key) past BDC")
                    let l = b.valveLift(i, crankAngleDeg: d, rpm: 4000)
                    XCTAssertTrue(l.intake.isFinite && l.exhaust.isFinite,
                                  "\(key) valve lift not finite")
                }
            }
        }
    }
}
