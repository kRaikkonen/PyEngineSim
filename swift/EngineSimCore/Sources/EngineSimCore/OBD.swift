//
//  OBD.swift
//  Reading the real car: mode-01 PIDs off an ELM327, and what to do with them.
//
//  The parsing is the fiddly part and it is worth stating why it looks the way
//  it does.  A multi-PID answer carries ONE 0x41 header and then a RUN of
//  `<pid> <data...>` pairs -- `41 0C 1A F8 0D 2E 5A 80` -- not a fresh header
//  per PID.  So the parser finds the header once and then WALKS the chain,
//  resyncing on the next 0x41 only when a byte turns up that is not a PID we
//  asked for.  Searching for "41xx" instead would let a data byte that happens
//  to be 0x41 masquerade as a header, which is exactly the sort of bug that
//  shows up as a phantom 6000 rpm once every few minutes.
//

import Foundation

/// Mode-01 PID payload lengths, in bytes.
public let pidLength: [Int: Int] = [
    0x04: 1,   // calculated engine load
    0x05: 1,   // coolant temperature
    0x0B: 1,   // intake manifold absolute pressure
    0x0C: 2,   // engine rpm
    0x0D: 1,   // vehicle speed
    0x0F: 1,   // intake air temperature
    0x10: 2,   // mass air flow
    0x11: 1,   // throttle position
    0x1F: 2,   // run time since start
    0x33: 1,   // absolute barometric pressure
    0x42: 2,   // control module voltage
    0x45: 1,   // relative throttle position
    0x49: 1,   // accelerator pedal position D
    0x4A: 1,   // accelerator pedal position E
    0x5A: 1,   // relative accelerator pedal position
    0x00: 4, 0x20: 4, 0x40: 4,   // "PIDs supported" bitmasks
]

public enum OBDParse {
    /// Strip an ISO-TP frame's line noise down to the payload hex.
    ///
    /// A multi-frame reply is prefixed with a 3-digit total length; that
    /// header BOUNDS the real data, so the CAN padding after it (0x00 or 0xAA
    /// filling the last frame) can never be read as another PID.
    public static func cleanFrames(_ raw: String) -> String {
        var parts = [String]()
        var declared = 0
        // split on ANY whitespace plus the ELM327 prompt, by predicate
        // rather than a literal set -- see testFrameParsing
        for line in raw.uppercased().split(whereSeparator: {
            $0.isWhitespace || $0 == ">"
        }) {
            let s = String(line)
            guard !s.isEmpty, s.allSatisfy({ $0.isHexDigit }) else { continue }
            if s.count == 3 && parts.isEmpty {          // ISO-TP length header
                declared = Int(s, radix: 16) ?? 0
                continue
            }
            parts.append(s)
        }
        var hex = parts.joined()
        if declared > 0 && 2 * declared <= hex.count {
            hex = String(hex.prefix(2 * declared))
        }
        return hex
    }

    /// Pull the values out of a mode-01 reply.  See the file comment.
    public static func pids(_ hex: String,
                            lengths: [Int: Int] = pidLength) -> [Int: [Int]] {
        let h = Array(hex.utf8)
        func byte(_ i: Int) -> Int? {
            guard i + 2 <= h.count,
                  let v = Int(String(decoding: h[i..<(i + 2)], as: UTF8.self),
                              radix: 16) else { return nil }
            return v
        }
        var out = [Int: [Int]]()
        var i = 0
        while i + 4 <= h.count {
            guard byte(i) == 0x41 else { i += 2; continue }
            i += 2                                  // past the reply header
            while i + 2 <= h.count {
                guard let pid = byte(i), let ln = lengths[pid],
                      i + 2 + 2 * ln <= h.count else { break }   // padding: resync
                var vals = [Int]()
                for k in 0..<ln {
                    guard let v = byte(i + 2 + 2 * k) else { break }
                    vals.append(v)
                }
                out[pid] = vals
                i += 2 + 2 * ln
            }
        }
        return out
    }

    /// Decode a 4-byte "PIDs supported" bitmask (the 0x00/0x20/0x40 replies).
    public static func supported(_ data: [Int], base: Int) -> Set<Int> {
        var got = Set<Int>()
        for (byteIndex, byte) in data.prefix(4).enumerated() {
            for bit in 0..<8 where byte & (1 << (7 - bit)) != 0 {
                got.insert(base + byteIndex * 8 + bit + 1)
            }
        }
        return got
    }
}

// ---------------------------------------------------------------- rpm map
/// Map the real crankshaft onto the simulated engine's rev range.
public final class RpmMap {
    public enum Mode: String, CaseIterable {
        /// 1:1.  Right when the two engines rev alike -- an A3 wearing the
        /// RS3's five-cylinder is 6500 against 7000, near enough.
        case direct
        /// Affine, idle to idle and redline to redline.  What you want for an
        /// engine that revs somewhere else entirely: flooring a 6500 rpm road
        /// car then makes a V12 sing at ITS own 8500 instead of stopping two
        /// thirds of the way up.
        case stretch
        /// REDLINE-PROPORTIONAL: sim = car * (sim redline / car redline), so
        /// your 6500 lands exactly on the target's 9500 and everything below
        /// scales with it.  Unlike `stretch` it goes through zero rather than
        /// pinning the idle ends together, so doubling your revs doubles the
        /// note -- the proportion is kept and only the SCALE changes.
        case ratio
    }

    public var mode: Mode
    public var carIdle: Double
    public var carRedline: Double
    /// A trim on top of the redline ratio, for when you want it a little
    /// higher or lower than exact.  1.0 is exact.
    public var ratio: Double
    public var learn: Bool
    public private(set) var seenIdle: Double?
    public private(set) var seenMax = 0.0

    public init(mode: Mode = .stretch, carIdle: Double = 800,
                carRedline: Double = 6500, ratio: Double = 1.0,
                learn: Bool = true) {
        self.mode = mode
        self.carIdle = carIdle
        self.carRedline = carRedline
        self.ratio = ratio
        self.learn = learn
    }

    /// Fold a live sample into the learned car rev range.
    ///
    /// The seed is a guess; the highest rpm ever seen beats it.  So a wrong
    /// guess self-corrects within one drive instead of squashing the top end
    /// forever -- which matters, because the seed comes from a profile table
    /// and the car in front of it might not be the car in the table.
    public func observe(rpm: Double, pedal: Double) {
        guard learn, rpm >= 300.0 else { return }
        if rpm > seenMax {
            seenMax = rpm
            if rpm > carRedline { carRedline = rpm }       // the seed was low
        }
        if pedal < 0.03 && rpm < 1400.0 {
            seenIdle = seenIdle.map { $0 + (rpm - $0) * 0.02 } ?? rpm
            if let s = seenIdle, s < carIdle { carIdle = s }
        }
    }

    public func callAsFunction(_ rpm: Double, engIdle: Double,
                               engRedline: Double) -> Double {
        var out: Double
        switch mode {
        case .direct: out = rpm
        case .ratio:
            // the ratio of the REDLINES, so the two ceilings line up
            out = rpm * (max(engRedline, 1.0) / max(carRedline, 1.0)) * ratio
        case .stretch:
            let span = max(carRedline - carIdle, 500.0)
            let frac = (rpm - carIdle) / span
            out = engIdle + frac * max(engRedline - engIdle, 500.0)
        }
        // never below a plausible idle, never past the limiter
        return min(max(out, engIdle * 0.55), engRedline * 1.02)
    }

    /// A pre-drive sanity check: what the mapping does across the range.
    public func preview(engIdle: Double, engRedline: Double,
                        steps: Int = 8) -> [(car: Double, sim: Double)] {
        (0..<steps).map { i in
            let car = carIdle + (carRedline - carIdle) * Double(i)
                / Double(steps - 1)
            return (car, callAsFunction(car, engIdle: engIdle,
                                        engRedline: engRedline))
        }
    }
}

// ------------------------------------------------------------ gear learner
/// Work out which gear the car is in without being told its ratios.
///
/// rpm/kmh is constant within a gear, so the ratios show up as plateaus.  They
/// are clustered online and the gear number is the cluster's rank, highest
/// ratio first.  Only STABLE samples are learned from: a shift, a slipping
/// clutch or a launch sweeps rpm/kmh across everything in between, and letting
/// those found clusters invents gears the car does not have.
///
/// It feeds the straight-cut gear whine and nothing else, so being wrong for
/// the first minute of a drive costs nothing.
public final class GearLearner {
    public private(set) var centres = [Double]()
    public private(set) var gear = 0
    let maxGears: Int
    let tol: Double
    var prevRatio: Double?

    public init(maxGears: Int = 8, tol: Double = 0.08) {
        self.maxGears = maxGears
        self.tol = tol
    }

    @discardableResult
    public func update(rpm: Double, speedKmh: Double) -> Int {
        guard speedKmh >= 8.0, rpm >= 700.0 else {
            prevRatio = nil
            gear = speedKmh > 1.0 ? 1 : 0
            return gear
        }
        let r = rpm / speedKmh
        let prev = prevRatio
        prevRatio = r
        guard let p = prev, abs(r - p) / r <= 0.02 else { return gear }

        var best: Double?
        var bi = -1
        for (i, c) in centres.enumerated() {
            let d = abs(c - r) / c
            if d < tol && (best == nil || d < best!) { best = d; bi = i }
        }
        if bi < 0 {
            if centres.count < maxGears { centres.append(r) }
        } else {
            centres[bi] += (r - centres[bi]) * 0.05
        }
        centres.sort(by: >)                    // highest rpm/kmh is 1st gear
        if !centres.isEmpty {
            var bestI = 0
            for i in centres.indices
            where abs(centres[i] - r) < abs(centres[bestI] - r) { bestI = i }
            gear = 1 + bestI
        }
        return gear
    }
}

// ---------------------------------------------------------- shift detector
/// Spot a gearshift so the exhaust can bang the way the real one does.
///
/// A dual-clutch upshift cuts ignition for about a tenth of a second, and that
/// torque interruption IS the bang.  No car broadcasts "I am shifting", but it
/// is unmistakable in the data: rpm collapsing fast while the pedal stays down
/// and the car is not slowing.  Lifting off also drops rpm -- the pedal test is
/// what separates the two, and it is why a downshift blip (rpm RISING) can
/// never trigger it.
public final class ShiftDetector {
    let dropRate: Double      // rpm/s, steeper than any natural decay
    let minPedal: Double      // still asking for power, so not a lift
    let minSpeed: Double      // m/s, rules out coming to a stop
    let cut: Double           // seconds of ignition cut to imitate
    public private(set) var timer = 0.0
    public private(set) var shifts = 0
    var prev: Double?

    public init(dropRate: Double = -3000, minPedal: Double = 0.35,
                minSpeed: Double = 5.0, cut: Double = 0.12) {
        self.dropRate = dropRate
        self.minPedal = minPedal
        self.minSpeed = minSpeed
        self.cut = cut
    }

    /// True while the cut is in progress -- feed it to the synth as a closed
    /// throttle.
    @discardableResult
    public func update(dt: Double, rpm: Double, pedal: Double,
                       speed: Double) -> Bool {
        let p = prev
        prev = rpm
        if timer > 0.0 {
            timer -= dt
            return timer > 0.0
        }
        guard let prevRpm = p, dt > 0.0 else { return false }
        if (rpm - prevRpm) / dt < dropRate && pedal > minPedal
            && speed > minSpeed {
            timer = cut
            shifts += 1
            return true
        }
        return false
    }
}
