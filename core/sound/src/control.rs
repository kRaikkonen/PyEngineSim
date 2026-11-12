//! The operating point, and how it crosses from the thread that knows it to
//! the thread that renders.
//!
//! The engine's state is a function of the operating point it is given
//! (s.5.2): whoever drives the core -- the free-running simulator, the car
//! link, a test, the bake -- hands over the same record, and the same code
//! turns it into sound.

use core::cell::UnsafeCell;
use core::sync::atomic::{AtomicBool, AtomicU8, Ordering};

use crate::gas::P_ATM;
use crate::network::ORIFICE_CHANNELS;

/// One operating point.  Plain numbers, SI units, crank degrees.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Control {
    /// crankshaft speed, rev/min
    pub rpm: f64,
    /// pressure in the intake port (the manifold), Pa
    pub p_intake: f64,
    /// temperature of the charge in the intake port, K
    pub t_intake: f64,
    /// pressure the exhaust port discharges into (before a turbine: its inlet), Pa
    pub p_exhaust: f64,
    /// temperature of the gas standing in the exhaust port, K
    pub t_exhaust: f64,
    /// air trapped per cylinder per cycle, kg -- from the baked breathing
    /// tables, so the sound carries the same charge the torque does.
    /// <= 0: let the cylinder breathe through its own valves.
    pub charge: f64,
    /// relative air/fuel ratio (1 = stoichiometric)
    pub lambda: f64,
    /// crank angle of 50 % burned, degrees after firing TDC
    pub ca50_deg: f64,
    /// 10-90 % burn duration, crank degrees
    pub burn_deg: f64,
    /// cam phasing: degrees the intake / exhaust events are moved LATER
    pub intake_phase_deg: f64,
    pub exhaust_phase_deg: f64,
    /// multipliers of the flow resistance of the network orifices that name
    /// a channel (a turbine with moving vanes, an exhaust flap): 1 = as built
    pub orifice: [f64; ORIFICE_CHANNELS],
    /// cycle-to-cycle variation of the burn, about the coefficient of
    /// variation of the indicated work: 0.01-0.03 at full load, 0.05-0.15
    /// idling.  0: every cycle the same.
    pub ccv: f64,
    /// sound pressure (Pa rms at 1 m) that plays at the reference level,
    /// 0.2 rms of full scale.  <= 0: a fixed 200 Pa = full scale.
    pub level_ref: f64,
    /// how much of the real dynamic range is played: 1 = all of it, 0 = a
    /// constant level (s.6.6)
    pub dynamics: f64,
    /// the listener's volume knob, linear
    pub volume: f64,
    /// bit k set: cylinder k gets no fuel (overrun, limiter, deactivation)
    pub fuel_cut: u64,
}

impl Default for Control {
    fn default() -> Self {
        Control {
            rpm: 800.0,
            p_intake: 0.35 * P_ATM,
            t_intake: 310.0,
            p_exhaust: P_ATM,
            t_exhaust: 700.0,
            charge: 0.0,
            lambda: 1.0,
            ca50_deg: 10.0,
            burn_deg: 25.0,
            intake_phase_deg: 0.0,
            exhaust_phase_deg: 0.0,
            orifice: [1.0; ORIFICE_CHANNELS],
            ccv: 0.0,
            level_ref: 0.0,
            dynamics: 1.0,
            volume: 1.0,
            fuel_cut: 0,
        }
    }
}

impl Control {
    /// The record made safe to run on, or None if it holds a non-finite
    /// number (then the previous one stays in force).
    pub fn sanitized(&self) -> Option<Control> {
        let all = [
            self.rpm,
            self.p_intake,
            self.t_intake,
            self.p_exhaust,
            self.t_exhaust,
            self.charge,
            self.lambda,
            self.ca50_deg,
            self.burn_deg,
            self.intake_phase_deg,
            self.exhaust_phase_deg,
            self.ccv,
            self.level_ref,
            self.dynamics,
            self.volume,
        ];
        if all.iter().chain(self.orifice.iter()).any(|v| !v.is_finite()) {
            return None;
        }
        let mut orifice = self.orifice;
        for m in &mut orifice {
            *m = m.clamp(1.0e-3, 1.0e6);
        }
        Some(Control {
            rpm: self.rpm.clamp(0.0, 30_000.0),
            p_intake: self.p_intake.clamp(2.0e3, 1.0e6),
            t_intake: self.t_intake.clamp(200.0, 600.0),
            p_exhaust: self.p_exhaust.clamp(2.0e4, 1.0e6),
            t_exhaust: self.t_exhaust.clamp(250.0, 1500.0),
            charge: self.charge.max(0.0),
            lambda: self.lambda.clamp(0.5, 3.0),
            ca50_deg: self.ca50_deg.clamp(-20.0, 60.0),
            burn_deg: self.burn_deg.clamp(8.0, 120.0),
            intake_phase_deg: self.intake_phase_deg.clamp(-90.0, 90.0),
            exhaust_phase_deg: self.exhaust_phase_deg.clamp(-90.0, 90.0),
            orifice,
            ccv: self.ccv.clamp(0.0, 0.3),
            level_ref: self.level_ref.max(0.0),
            dynamics: self.dynamics.clamp(0.0, 1.0),
            volume: self.volume.clamp(0.0, 8.0),
            fuel_cut: self.fuel_cut,
        })
    }
}

const DIRTY: u8 = 4;

/// A mailbox holding the latest value: any thread publishes, the audio
/// thread takes.  Neither side ever waits for the other, and a value is never
/// seen half-written -- three slots, one always free for the writer.
pub struct Inbox<T: Copy> {
    slots: [UnsafeCell<T>; 3],
    /// the slot last published (low bits) and whether the reader has it yet
    back: AtomicU8,
    /// the writer's slot
    w: AtomicU8,
    /// the reader's slot
    r: AtomicU8,
    /// one writer at a time
    writing: AtomicBool,
}

// SAFETY: a slot is touched only by the side that owns its index.  The
// indices change hands by an atomic swap of `back`, so no slot is ever owned
// by both sides; `writing` keeps the writer side to one thread at a time, and
// the reader side is the single audio thread (`take` is not re-entrant: it is
// called from `Engine::tick`, which needs `&mut Engine`).
unsafe impl<T: Copy + Send> Sync for Inbox<T> {}
unsafe impl<T: Copy + Send> Send for Inbox<T> {}

impl<T: Copy> Inbox<T> {
    pub fn new(v: T) -> Self {
        Inbox {
            slots: [UnsafeCell::new(v), UnsafeCell::new(v), UnsafeCell::new(v)],
            back: AtomicU8::new(1),
            w: AtomicU8::new(0),
            r: AtomicU8::new(2),
            writing: AtomicBool::new(false),
        }
    }

    /// Publish a value.  Returns false (and publishes nothing) if another
    /// thread is publishing at this very moment -- its value wins.
    pub fn publish(&self, v: T) -> bool {
        if self.writing.swap(true, Ordering::Acquire) {
            return false;
        }
        let w = self.w.load(Ordering::Relaxed);
        // SAFETY: slot `w` belongs to the writer side, and `writing` makes
        // this thread the only writer.
        unsafe { *self.slots[w as usize].get() = v };
        let old = self.back.swap(w | DIRTY, Ordering::AcqRel);
        self.w.store(old & 3, Ordering::Relaxed);
        self.writing.store(false, Ordering::Release);
        true
    }

    /// The latest value, if one was published since the last call.
    /// To be called from one thread only (the audio thread).
    pub fn take(&self) -> Option<T> {
        if self.back.load(Ordering::Acquire) & DIRTY == 0 {
            return None;
        }
        let r = self.r.load(Ordering::Relaxed);
        let old = self.back.swap(r, Ordering::AcqRel);
        let mine = old & 3;
        self.r.store(mine, Ordering::Relaxed);
        // SAFETY: slot `mine` was handed to the reader side by the swap.
        Some(unsafe { *self.slots[mine as usize].get() })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn latest_value_wins() {
        let b = Inbox::new(0u64);
        assert_eq!(b.take(), None);
        assert!(b.publish(1));
        assert!(b.publish(2));
        assert!(b.publish(3));
        assert_eq!(b.take(), Some(3));
        assert_eq!(b.take(), None);
        assert!(b.publish(4));
        assert_eq!(b.take(), Some(4));
    }

    #[test]
    fn never_torn_across_threads() {
        // every field of a published record is the same number; a torn read
        // would show two different ones
        #[derive(Clone, Copy)]
        struct Rec([u64; 16]);
        let b = Arc::new(Inbox::new(Rec([0; 16])));
        let w = {
            let b = b.clone();
            std::thread::spawn(move || {
                for i in 1..=200_000u64 {
                    b.publish(Rec([i; 16]));
                }
            })
        };
        // the last value published is always there to be taken, so this ends
        let mut seen = 0u64;
        while seen < 200_000 {
            if let Some(Rec(a)) = b.take() {
                assert!(a.iter().all(|x| *x == a[0]), "torn read: {a:?}");
                assert!(a[0] > seen, "went backwards: {} after {}", a[0], seen);
                seen = a[0];
            }
        }
        w.join().unwrap();
        assert_eq!(b.take().map(|r| r.0[0]), None);
    }

    #[test]
    fn non_finite_controls_are_refused() {
        let mut c = Control::default();
        assert!(c.sanitized().is_some());
        c.rpm = f64::NAN;
        assert!(c.sanitized().is_none());
        c.rpm = -5.0;
        assert_eq!(c.sanitized().unwrap().rpm, 0.0);
    }
}
