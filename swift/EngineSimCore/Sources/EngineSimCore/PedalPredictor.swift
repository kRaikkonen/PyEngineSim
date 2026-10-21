//
//  PedalPredictor.swift
//  An EARLY rpm from the pedal (Leo, 2026-10-21: "根据踏板来").
//
//  The pedal moves first.  Throttle plate, manifold fill, the turbo and the
//  drivetrain's inertia put 100-300 ms between a stab and the rpm the OBD link
//  can see rising.  Every reading crosses the same slow link, but the pedal's
//  arrives while the rpm's still says "steady".  So the rpm projection gets
//  one more term: the change in engine EFFORT since the last rpm sample -- the
//  pedal through the engine's lag -- times how hard this gear turns effort
//  into rpm/s.  Only the CHANGE: the measured rate stays the baseline, so a
//  wrong gain errs by a fraction of a fraction instead of steering the note.
//
//  The gain starts from physics (the gear learner's rpm per km/h fixes how a
//  wheel torque becomes rpm/s) and is then learned per gear from the car
//  itself, rising and falling apart -- engine braking is not drive torque
//  reversed.
//

import Foundation

public final class PedalPredictor {
    /// The engine's torque answer to the pedal (s): plate, manifold, turbo.
    public static let tauEngine = 0.20

    var pedal = 0.0
    var effortAt = 0.0
    var tAt: TimeInterval = 0
    /// The effort the MEASURED rpm rate reflects.  That rate is a smoothed
    /// finite difference -- each new one the average over an interval
    /// (its midpoint's effort), folded in by `smoothing` -- so it lags the
    /// effort by the same filter; the pedal's term is taken against this,
    /// which makes up that lag as well as the link's.
    public private(set) var effortRef = 0.0
    var refSet = false
    struct Fit { var sxy = 0.0, sxx = 0.0 }
    var fits = [String: Fit]()

    public init() {}

    /// Effort (0..1): the pedal through the engine's lag, at time t -- the
    /// last reading held from then on.
    public func effort(at t: TimeInterval) -> Double {
        guard tAt > 0 else { return pedal }
        let dt = max(t - tAt, 0.0)
        return pedal + (effortAt - pedal) * exp(-dt / Self.tauEngine)
    }

    /// A pedal reading the car took at time t.
    public func pedalSample(_ p: Double, at t: TimeInterval) {
        effortAt = tAt > 0 ? effort(at: t) : min(max(p, 0.0), 1.0)
        tAt = t
        pedal = min(max(p, 0.0), 1.0)
    }

    /// rpm/s per unit of effort, from physics.  In gear, rpm = n km/h, so
    /// d rpm/dt = 3.6 n a and a = T (G / r) / m with G / r = 3.6 n (2 pi / 60):
    /// d rpm/dt = T (3.6 n)^2 (2 pi / 60) / m -- a 200 N m pull on a 1500 kg
    /// car.  Stood still or in neutral, the flywheel alone (~4000 rpm/s).
    public static func prior(rpmPerKmh n: Double) -> Double {
        guard n > 1.0 else { return 4000.0 }
        let k = 3.6 * n
        return min(max(200.0 * k * k * (2.0 * Double.pi / 60.0) / 1500.0, 60.0), 6000.0)
    }

    func key(_ gear: Int, _ rising: Bool) -> String { "\(gear)" + (rising ? "+" : "-") }

    /// The gain for this gear: learned once there is enough of it, kept
    /// within a factor of three of the physics either way.
    public func gain(gear: Int, rpmPerKmh: Double, rising: Bool) -> Double {
        let p = Self.prior(rpmPerKmh: rpmPerKmh) * (rising ? 1.0 : 0.5)
        guard let f = fits[key(gear, rising)], f.sxx > 0.05 else { return p }
        return min(max(f.sxy / f.sxx, 0.3 * p), 3.0 * p)
    }

    /// Learn from one rpm sample interval: the measured acceleration changed
    /// by dRate (rpm/s) while the effort changed by dEffort.
    public func learn(gear: Int, dRate: Double, dEffort: Double) {
        guard abs(dEffort) > 0.03, dRate.isFinite else { return }
        let k = key(gear, dEffort > 0)
        var f = fits[k] ?? Fit()
        f.sxy = f.sxy * 0.97 + dRate * dEffort
        f.sxx = f.sxx * 0.97 + dEffort * dEffort
        fits[k] = f
    }

    /// An rpm sample taken at `taken` (the previous at `previous`): fold the
    /// interval's effort into the reference the way the caller folds the
    /// interval's rate into its smoothed rate.  Returns the reference's change
    /// (what the measured rate's change is to be learned against).
    @discardableResult
    public func rpmSample(taken: TimeInterval, previous: TimeInterval?,
                          smoothing: Double) -> Double {
        guard let p = previous, refSet, taken > p else {
            effortRef = effort(at: taken)
            refSet = true
            return 0.0
        }
        let before = effortRef
        effortRef += (effort(at: 0.5 * (p + taken)) - effortRef) * smoothing
        return effortRef - before
    }

    /// The rpm the pedal adds between the last rpm sample (ts) and the time
    /// the sound plays (tT): the gain times the effort over that span
    /// (Simpson) above the effort the measured rate already carries.  The
    /// gains are passed in -- the caller reads them on another thread than
    /// the one learning them, so it keeps them as plain numbers.
    public func extraRPM(from ts: TimeInterval, to tT: TimeInterval,
                         gainRising: Double, gainFalling: Double) -> Double {
        let h = tT - ts
        guard h > 0, refSet else { return 0.0 }
        let e0 = effort(at: ts), em = effort(at: ts + 0.5 * h), eT = effort(at: tT)
        let avg = (e0 + 4.0 * em + eT) / 6.0 - effortRef
        return (avg >= 0 ? gainRising : gainFalling) * avg * h
    }
}
