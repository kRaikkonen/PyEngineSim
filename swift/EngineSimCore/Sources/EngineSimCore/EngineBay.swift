//
//  EngineBay.swift
//  The engine as a MECHANISM, for looking at.
//
//  Everything here is derived from the same preset the sound is built from --
//  bore, stroke, rod length, the 720 deg cycle offsets, the bank angles.  So
//  the picture cannot drift from the noise: a cylinder lights on screen because
//  its firing offset came round, which is the same number that placed the pulse
//  in the audio, and a piston is where the slider-crank equation says it is
//  rather than where a sine wave would put it.
//
//  It holds NO drawing code and imports no UI framework.  That is deliberate:
//  the kinematics can then be tested against the Python on a machine with no
//  screen, which is the only way this stays honest.
//
//  On the strobe.  The crank is integrated in real time, so at 6000 rpm it
//  turns 100 times a second and a 60 Hz screen samples it about six revolutions
//  apart -- the pistons alias into a slow crawl or stand still, exactly like an
//  engine under a strobe lamp.  That is not a bug and the Python behaves the
//  same way; the honest fix is to slow TIME down, which is what timeScale is
//  for, rather than to lie about where the piston is.
//

import Foundation

// MARK: - layout

/// How the cylinders are arranged, worked out from the bank angles themselves
/// rather than from a name -- a preset never says "it is a V8", it says where
/// each cylinder points, and that is the more reliable thing to read.
public enum EngineLayout: String {
    case inline, vee, flat, w, radial, rotary, single

    public var label: String {
        switch self {
        case .inline: return "inline"
        case .vee: return "V"
        case .flat: return "flat"
        case .w: return "W"
        case .radial: return "radial"
        case .rotary: return "rotary"
        case .single: return "single"
        }
    }
}

/// Which quarter of the four-stroke cycle a cylinder is in.
public enum Stroke: String {
    case power, exhaust, intake, compression

    public var label: String { rawValue }
}

// MARK: - the bay

public struct EngineBay {
    /// One cylinder's fixed place in the picture.
    public struct Slot {
        public let index: Int
        /// Which bank, ordered left-to-right by angle.  An inline engine has
        /// one; a V or a flat has two; a W has four.
        public let bank: Int
        public let bankAngleDeg: Double
        /// Position along the crank, 0 at the front.  Cylinders are ordered by
        /// their index within the bank, which is how presets list them.
        public let station: Int
        public let cycleOffsetDeg: Double
        public let bore: Double
        public let stroke: Double
        public let rodLength: Double
    }

    public let engine: EnginePreset
    public let layout: EngineLayout
    public let slots: [Slot]
    /// Distinct bank angles, ascending.  One entry for an inline engine.
    public let bankAngles: [Double]
    /// The firing order as cylinder indices, earliest offset first.
    public let firingOrder: [Int]

    public var cylinderCount: Int { slots.count }
    /// Cylinders on the longest bank -- how many stations the drawing needs.
    public var stationsPerBank: Int {
        max(slots.map { $0.station }.max().map { $0 + 1 } ?? 1, 1)
    }

    public init(engine: EnginePreset) {
        self.engine = engine
        let cyls = engine.cylinders

        // Bank angles are bucketed at a tenth of a degree: presets carry exact
        // values, but floating point equality on an angle is a trap waiting to
        // split one bank into two.
        let key: (Double) -> Int = { Int(($0 * 10.0).rounded()) }
        var seen: [Int: Double] = [:]
        for c in cyls where seen[key(c.bankAngleDeg)] == nil {
            seen[key(c.bankAngleDeg)] = c.bankAngleDeg
        }
        let angles = seen.values.sorted()
        bankAngles = angles

        let spread = (angles.last ?? 0) - (angles.first ?? 0)
        if engine.isRotary {
            layout = .rotary
        } else if cyls.count == 1 {
            layout = .single
        } else if angles.count >= 4 {
            layout = .w
        } else if angles.count == 1 {
            // A radial has every cylinder on its own angle, so reaching here
            // with one angle and many cylinders means they are all upright.
            layout = .inline
        } else if spread >= 160.0 {
            layout = .flat            // boxer: the banks face each other
        } else {
            layout = .vee
        }

        // Station = how many cylinders on this bank came before it.  Presets
        // list cylinders front to back, so the natural order is the right one.
        var perBank = [Int: Int]()
        var built: [Slot] = []
        for (i, c) in cyls.enumerated() {
            let b = angles.firstIndex(where: { key($0) == key(c.bankAngleDeg) }) ?? 0
            let st = perBank[b] ?? 0
            perBank[b] = st + 1
            built.append(Slot(index: i, bank: b, bankAngleDeg: c.bankAngleDeg,
                              station: st, cycleOffsetDeg: c.cycleOffsetDeg,
                              bore: c.bore, stroke: c.stroke,
                              rodLength: c.rodLength))
        }
        slots = built
        firingOrder = cyls.indices.sorted {
            (cyls[$0].cycleOffsetDeg.truncatingRemainder(dividingBy: 720.0))
                < (cyls[$1].cycleOffsetDeg.truncatingRemainder(dividingBy: 720.0))
        }
    }

    // MARK: - kinematics

    /// Where this cylinder is in its own 720 deg cycle.
    ///
    /// Mirrors `Simulator.cycle_phase_deg`.  Zero is the firing TDC, so
    /// 0-180 is power, 180-360 exhaust, 360-540 intake, 540-720 compression --
    /// the same convention the audio places its pulses on.
    public func cyclePhaseDeg(_ i: Int, crankAngleDeg: Double) -> Double {
        let p = (crankAngleDeg + slots[i].cycleOffsetDeg)
            .truncatingRemainder(dividingBy: 720.0)
        return p < 0 ? p + 720.0 : p
    }

    /// Piston position, 0 at TDC and 1 at BDC.
    ///
    /// The real slider-crank, not a sine: with a short rod the piston spends
    /// visibly longer near BDC than near TDC, and that asymmetry is most of
    /// what makes the animation read as an engine rather than as pistons on
    /// springs.  Mirrors `Cylinder.piston_displacement`.
    public func pistonFraction(_ i: Int, crankAngleDeg: Double) -> Double {
        let s = slots[i]
        let phi = cyclePhaseDeg(i, crankAngleDeg: crankAngleDeg)
        let theta = phi.truncatingRemainder(dividingBy: 360.0) * .pi / 180.0
        let r = s.stroke / 2.0, l = s.rodLength
        let sn = sin(theta)
        let root = (l * l - (r * sn) * (r * sn)).squareRoot()
        return ((r + l) - (r * cos(theta) + root)) / s.stroke
    }

    /// The crank pin's angle for this cylinder, measured from its own TDC.
    /// What the crankshaft drawing needs.
    public func crankPinDeg(_ i: Int, crankAngleDeg: Double) -> Double {
        cyclePhaseDeg(i, crankAngleDeg: crankAngleDeg)
            .truncatingRemainder(dividingBy: 360.0)
    }

    public func stroke(_ i: Int, crankAngleDeg: Double) -> Stroke {
        switch cyclePhaseDeg(i, crankAngleDeg: crankAngleDeg) {
        case ..<180: return .power
        case ..<360: return .exhaust
        case ..<540: return .intake
        default: return .compression
        }
    }

    // MARK: - valve timing

    /// How long a valve stays open, in crank degrees, from the cam the preset
    /// names.  A race cam is not "the same cam, louder" -- it is open longer,
    /// which is where the overlap and the lopey idle come from.
    public func valveDurationDeg(rpm: Double) -> Double {
        var dur: Double
        switch engine.camProfile {
        case "mild": dur = 200.0
        case "hot": dur = 252.0
        case "race": dur = 284.0
        default: dur = 222.0            // "stock"
        }
        // VTEC and friends really do swap to a longer-duration lobe, and the
        // preset already says at what rpm, so the picture can show the step.
        if engine.valveLift == "two-stage", engine.vtecRpm > 1.0,
           rpm >= engine.vtecRpm {
            dur += 40.0
        } else if engine.valveLift == "continuous" {
            // continuously variable lift trims duration right down when it is
            // throttling on the valve rather than on a plate
            dur -= 26.0
        }
        return dur
    }

    /// Intake and exhaust lift, 0..1, for this cylinder right now.
    ///
    /// Both events are centred the textbook way -- the exhaust opens before BDC
    /// of the power stroke and shuts after TDC, the intake opens before that
    /// same TDC -- so the two overlap around 360 deg exactly as a real head
    /// does.  The lobe itself is a raised cosine, which has the zero velocity
    /// at both ends that a real cam must have.
    public func valveLift(_ i: Int, crankAngleDeg: Double,
                          rpm: Double) -> (intake: Double, exhaust: Double) {
        let phi = cyclePhaseDeg(i, crankAngleDeg: crankAngleDeg)
        let dur = valveDurationDeg(rpm: rpm)
        let half = (dur - 180.0) / 2.0          // spill either side of a stroke

        func lobe(open: Double, close: Double) -> Double {
            var t = phi - open
            if t < 0 { t += 720.0 }
            let span = close - open
            guard span > 0, t <= span else { return 0 }
            return 0.5 * (1.0 - cos(2.0 * .pi * t / span))
        }
        // exhaust stroke is 180..360, intake is 360..540
        let ex = lobe(open: 180.0 - half, close: 360.0 + half)
        let inn = lobe(open: 360.0 - half, close: 540.0 + half)
        return (inn, ex)
    }

    // MARK: - induction

    /// `na` rather than `none`: a case called `none` on a non-Optional enum
    /// shadows `Optional.none` at every use site and turns `x == .none` into a
    /// coin toss for the reader, if not for the compiler.
    public enum Charger: String {
        case na, turbo, twinTurbo, roots, centrifugal, electric

        public var label: String { self == .na ? "NA" : rawValue }
    }

    public var charger: Charger {
        if engine.electricTurbo { return .electric }
        switch engine.induction {
        case "turbo":
            return engine.inductionSubtype.contains("twin") ? .twinTurbo : .turbo
        case "roots", "screw": return .roots
        case "centrifugal": return .centrifugal
        default: return .na
        }
    }

    /// Shaft speed as a fraction of flat out, for the spinning wheel.
    ///
    /// A turbo is driven by the exhaust, so it tracks BOOST and hangs on after
    /// a lift; a belt-driven blower is geared to the crank and can only ever
    /// follow rpm.  Two different mechanisms, so two different needles.
    public func chargerSpin(rpm: Double, boostBar: Double) -> Double {
        let rf = min(max(rpm / max(engine.redlineRpm, 1.0), 0.0), 1.2)
        switch charger {
        case .na: return 0
        case .roots, .centrifugal:
            return rf                                   // belted to the crank
        case .electric:
            return min(max(boostBar / max(engine.boostBar, 0.1), 0.0), 1.0)
        case .turbo, .twinTurbo:
            let b = min(max(boostBar / max(engine.boostBar, 0.1), 0.0), 1.0)
            return min(0.25 * rf + 0.85 * b, 1.2)       // idles on exhaust flow
        }
    }
}

// MARK: - exhaust pulses

/// The blowdown pulses, as things that TRAVEL.
///
/// A header animation that just flashes the pipe when a cylinder fires misses
/// the point of a header: the pulse takes real time to reach the collector, and
/// on a 4-into-1 the pulses from different cylinders arrive there staggered by
/// the firing interval.  So each one is tracked as a position along its primary
/// and then along the rest of the system, at the gas's own speed of sound.
public struct ExhaustPulseField {
    public struct Pulse {
        public let cylinder: Int
        public let bank: Int
        /// 0 at the port, 1 at the collector.
        public var primary: Double
        /// 0 at the collector, 1 at the tailpipe.  Negative until it merges.
        public var tail: Double
        public var strength: Double
    }

    public private(set) var pulses: [Pulse] = []
    /// Set when a pulse reaches the tip, for a puff at the tailpipe.
    public private(set) var exitFlash = 0.0
    /// How many have been launched, ever.
    ///
    /// Counting them from `pulses` does not work: a pulse can be born and
    /// retired inside one step -- the whole system is only about 4 ms long and
    /// a frame is 17 ms -- so the list length says nothing about how many
    /// there were.  The field reports it instead, the way the pop stage does.
    public private(set) var launched = 0

    private var lastPhase: [Double]
    private let primaryM: Double
    private let totalM: Double

    public init(bay: EngineBay) {
        lastPhase = [Double](repeating: -1, count: bay.cylinderCount)
        primaryM = max(bay.engine.exhaustPrimaryM, 0.05)
        totalM = max(bay.engine.exhaustTotalM, primaryM + 0.1)
    }

    /// Advance by `dt`, launching a pulse for any cylinder whose exhaust valve
    /// just cracked open.
    ///
    /// `soundSpeed` is the hot-gas figure the acoustics use, so the pulse moves
    /// down the pipe at the rate the sound model says it does -- a cold engine's
    /// pulses really are slower.
    public mutating func update(bay: EngineBay, crankAngleDeg: Double,
                                dt: Double, soundSpeed: Double,
                                load: Double, rpm: Double) {
        exitFlash *= exp(-dt / 0.05)

        // BLOWDOWN is the event: the exhaust valve cracking near the end of the
        // power stroke, not the firing TDC.  That is where the bang leaves the
        // cylinder, so that is where the pulse starts.
        let half = (bay.valveDurationDeg(rpm: rpm) - 180.0) / 2.0
        let evo = 180.0 - half
        for i in 0..<bay.cylinderCount {
            let phi = bay.cyclePhaseDeg(i, crankAngleDeg: crankAngleDeg)
            let prev = lastPhase[i]
            lastPhase[i] = phi
            guard prev >= 0 else { continue }
            // Did EVO fall inside the arc we just swept?  Both are measured
            // FORWARD from `prev` and wrapped into 0..<720, which is the only
            // form of this test that survives the wrap without a special case
            // -- and at high rpm a single step can be most of a cycle, so the
            // wrap is the normal case rather than the rare one.
            let swept = (phi - prev).truncatingRemainder(dividingBy: 720.0)
            let arc = swept < 0 ? swept + 720.0 : swept
            let toEvo = (evo - prev).truncatingRemainder(dividingBy: 720.0)
            let ahead = toEvo < 0 ? toEvo + 720.0 : toEvo
            if arc > 0, ahead <= arc, bay.slots.indices.contains(i) {
                pulses.append(Pulse(cylinder: i, bank: bay.slots[i].bank,
                                    primary: 0, tail: -1,
                                    strength: min(max(load, 0.12), 1.0)))
                launched += 1
            }
        }

        let vPrimary = soundSpeed / primaryM               // fractions per second
        let vTail = soundSpeed / max(totalM - primaryM, 0.1)
        for k in pulses.indices {
            if pulses[k].primary < 1.0 {
                pulses[k].primary = min(pulses[k].primary + vPrimary * dt, 1.0)
                if pulses[k].primary >= 1.0 { pulses[k].tail = 0 }
            } else if pulses[k].tail >= 0 {
                pulses[k].tail += vTail * dt
            }
            // it fades as it goes: the pipe is lossy and the far end radiates
            pulses[k].strength *= exp(-dt * 2.2)
        }
        for p in pulses where p.tail >= 1.0 {
            exitFlash = max(exitFlash, p.strength)
        }
        pulses.removeAll { $0.tail >= 1.0 || $0.strength < 0.02 }
        // a hard ceiling so a stall or a huge dt cannot pile them up forever
        if pulses.count > 96 { pulses.removeFirst(pulses.count - 96) }
    }
}
