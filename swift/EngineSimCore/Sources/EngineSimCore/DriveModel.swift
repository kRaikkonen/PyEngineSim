//
//  DriveModel.swift
//  Driving it yourself: a pedal, a gear, and the engine's real torque.
//
//  This is what the desktop app does and what the sliders cannot: you press
//  and the revs CLIMB -- fast in first, slowly in sixth, and they fall on a
//  lift because a closed throttle makes negative torque.  That behaviour is
//  the point.  A slider tells the engine what revs to be at; a pedal tells it
//  how much torque to make, and the revs are the consequence.
//
//  Where the numbers come from, stated plainly because the two halves are not
//  equally solid:
//
//    the ENGINE is real -- a baked gas-torque surface from the same white-box
//    BMEP model the desktop dyno uses, plus the engine's own friction
//    polynomial and its ECU torque and power caps.  Nothing here is invented.
//
//    the VEHICLE is a plain longitudinal model with GENERIC coefficients: one
//    drag area, one rolling coefficient, one driveline efficiency.  The
//    presets carry mass, wheel radius and the real gear ratios, but not a
//    drag figure, so these three are stand-ins.  They decide how fast the car
//    gets down the road; they do not touch what the engine sounds like at a
//    given rpm and throttle, which is the part that matters here.
//

import Foundation

/// One engine's torque surface, baked by tools/export_torque.py.
public struct TorqueTable: Decodable {
    public let rpm: [Double]
    public let throttle: [Double]
    public let gas: [[Double]]          // [throttle][rpm]
    public let friction: [Double]       // static, linear*w, quad*w^2
    public let torque_limit_nm: Double
    public let power_limit_kw: Double
    public let inertia: Double
    public let idle_rpm: Double
    public let redline_rpm: Double
    public let gear_ratios: [Double]
    public let final_drive: Double
    public let wheel_radius: Double
    public let vehicle_mass: Double

    public static func load(jsonData: Data) throws -> [String: TorqueTable] {
        try JSONDecoder().decode([String: TorqueTable].self, from: jsonData)
    }

    /// Bilinear over the grid, clamped at the edges.
    public func gasTorque(rpm r: Double, throttle t: Double) -> Double {
        func span(_ axis: [Double], _ v: Double) -> (Int, Int, Double) {
            if v <= axis[0] { return (0, 0, 0) }
            if v >= axis[axis.count - 1] {
                return (axis.count - 1, axis.count - 1, 0)
            }
            var i = 0
            while i + 1 < axis.count && axis[i + 1] < v { i += 1 }
            let f = (v - axis[i]) / (axis[i + 1] - axis[i])
            return (i, i + 1, f)
        }
        let (r0, r1, fr) = span(rpm, r)
        let (t0, t1, ft) = span(throttle, t)
        let a = gas[t0][r0] + (gas[t0][r1] - gas[t0][r0]) * fr
        let b = gas[t1][r0] + (gas[t1][r1] - gas[t1][r0]) * fr
        return a + (b - a) * ft
    }

    /// Crank torque actually available, friction and the ECU caps included.
    /// Goes NEGATIVE on a closed throttle, which is engine braking.
    public func netTorque(rpm r: Double, throttle t: Double) -> Double {
        let w = r * 2.0 * Double.pi / 60.0
        let fric = friction[0] + friction[1] * w + friction[2] * w * w
        var net = gasTorque(rpm: r, throttle: t) - fric
        if torque_limit_nm > 0 { net = min(net, torque_limit_nm) }
        if power_limit_kw > 0 { net = min(net, power_limit_kw * 1000.0 / max(w, 1.0)) }
        return net
    }
}

/// Drive it with a pedal instead of a slider.
///
/// Satisfies `TelemetrySource`, so it plugs into CarMode exactly where the
/// dongle or the sliders would -- the chain has no idea which one is talking
/// to it, and neither does the mixer.
public final class PedalSource: TelemetrySource {
    let table: TorqueTable

    // --- the vehicle side: generic, and labelled as such ------------------
    let dragArea = 0.62          // Cd*A, m^2 -- a saloon
    let rollingCoefficient = 0.013
    let drivelineEfficiency = 0.88
    let airDensity = 1.2
    static let g = 9.81

    // --- state -------------------------------------------------------------
    public private(set) var rpm: Double
    public var rawRPM: Double { rpm }
    public var throttle: Double = 0
    public private(set) var speed: Double = 0        // m/s
    public private(set) var gear: Int = 1
    public var mapKPa: Double = 0
    public var baroKPa: Double = 101.3
    public var speedValid: Bool { true }
    public var hz: Double = 0
    public var status: String { "pedal" }
    public func isLive() -> Bool { true }

    /// True while the rev limiter is cutting -- the app shows it and the
    /// synth hears it as a closed throttle, which is what a limiter IS.
    public private(set) var limiting = false
    /// Set for a moment after a LIFT, so the exhaust can pop the way a real
    /// one does when unburnt charge lights off in a hot pipe.
    public private(set) var lifted = false
    var liftTimer = 0.0
    var prevThrottle = 0.0

    public var gearCount: Int { table.gear_ratios.count }

    public init(table: TorqueTable) {
        self.table = table
        rpm = table.idle_rpm
    }

    public func upshift() { if gear < gearCount { gear += 1 } }
    public func downshift() { if gear > 0 { gear -= 1 } }   // 0 = neutral
    public func setGear(_ g: Int) { gear = min(max(g, 0), gearCount) }

    /// The whole car, one step.
    public func update(dt rawDt: Double) {
        let dt = min(max(rawDt, 0.0), 0.05)
        guard dt > 0 else { return }

        // a LIFT is the transition, not the state: it is what lights the pipe
        if prevThrottle > 0.45 && throttle < 0.12 { liftTimer = 0.55 }
        prevThrottle = throttle
        liftTimer = max(liftTimer - dt, 0)
        lifted = liftTimer > 0

        // the rev limiter cuts fuel, and a fuel cut IS a closed throttle
        if rpm >= table.redline_rpm { limiting = true }
        if rpm < table.redline_rpm * 0.97 { limiting = false }
        let effective = limiting ? 0.0 : throttle

        // IDLE GOVERNOR.  Without one the engine coasts to a stop the moment
        // you lift, which is what an engine with no ECU actually does and what
        // no car has done since the eighties.  Proportional, opening the
        // bypass as the revs fall below idle -- the same job the real one has.
        let idleErr = (table.idle_rpm - rpm) / max(table.idle_rpm, 1.0)
        let idleAir = min(max(idleErr * 3.0, 0.0), 0.35)
        let demand = max(effective, limiting ? 0.0 : idleAir)

        let w = rpm * 2.0 * Double.pi / 60.0
        let torque = table.netTorque(rpm: rpm, throttle: demand)

        if gear == 0 {
            // NEUTRAL: free revving.  Nothing but the flywheel to accelerate,
            // which is why a blip in neutral snaps and a blip in gear does not.
            var newW = w + torque / max(table.inertia, 0.01) * dt
            newW = max(newW, table.idle_rpm * 0.75 * 2.0 * Double.pi / 60.0)
            rpm = min(newW * 60.0 / (2.0 * Double.pi), table.redline_rpm * 1.01)
            speed = max(speed - 0.6 * dt * max(speed, 0), 0)   // coasting
        } else {
            let ratio = table.gear_ratios[gear - 1] * table.final_drive
            let r = max(table.wheel_radius, 0.05)
            // Effective inertia at the wheels: the engine's own flywheel is
            // multiplied by the square of the ratio, which is why first gear
            // feels heavy at the crank and sixth feels light.
            let mEff = table.vehicle_mass
                + table.inertia * ratio * ratio / (r * r)
            let force = torque * ratio * drivelineEfficiency / r
            let drag = 0.5 * airDensity * dragArea * speed * abs(speed)
            let roll = rollingCoefficient * table.vehicle_mass * PedalSource.g
                * (speed > 0.1 ? 1.0 : speed * 10.0)
            speed = max(speed + (force - drag - roll) / mEff * dt, 0)
            // in gear the crank IS the wheels, through the ratio
            let geared = speed / r * ratio * 60.0 / (2.0 * Double.pi)
            // ...until the clutch would have to slip to keep it above idle
            rpm = max(geared, table.idle_rpm)
        }
        mapKPa = baroKPa * min(0.25 + 0.85 * demand, 1.0)
            * (rpm > 300 ? 1.0 : 0.0)
    }
}
