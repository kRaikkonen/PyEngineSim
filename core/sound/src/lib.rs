//! The real-time sound core of EngineSim v2 (docs/REDESIGN_V2.md s.5-6).
//!
//! The sound is not drawn: every cylinder's discharge through its valves is
//! solved live, in SI units, and what leaves the valve is a mass flow in kg/s.
//!
//! Rules of this crate (s.5.2):
//!   * nothing on the render path allocates, locks or calls the system;
//!   * every time constant is in seconds and every angle in crank degrees, so
//!     the output does not depend on the block length it is rendered in;
//!   * the same inputs give the same output, bit for bit;
//!   * every transcendental goes through `math`, the one seam where a
//!     platform-independent implementation is swapped in.

#![forbid(unsafe_op_in_unsafe_fn)]

pub mod control;
pub mod cylinder;
pub mod engine;
pub mod gas;
pub mod math;
pub mod nozzle_fit;
pub mod spec;

pub use control::{Control, Inbox};
pub use cylinder::Cylinder;
pub use engine::{Engine, Sample, CONTROL_BLOCK};
pub use spec::{EngineSpec, SpecError, ValveCurve};
