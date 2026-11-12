//! The C ABI of the EngineSim v2 core (docs/REDESIGN_V2.md s.5.3).
//!
//! The header is `include/enginesim_core.h`; the two are kept in step by
//! `es_abi_version` and the layout checks in the Python binding's tests.
//!
//! Threads: `es_control` may be called from any thread; `es_render` (and
//! `es_run`) from one thread at a time -- the audio thread.  Everything else
//! is for the thread that owns the engine, while no render is running.

#![forbid(unsafe_op_in_unsafe_fn)]

use std::sync::Arc;

use es_sound::{Control, Engine, EngineSpec, Inbox, NetworkSpec, NodeSpec, PipeSpec, ValveCurve};

/// Bumped whenever a struct below or a function's meaning changes.
pub const ABI_VERSION: u32 = 4;

/// A valve's effective flow area against crank angle (see `ValveCurve`).
#[repr(C)]
pub struct EsValve {
    pub open_deg: f64,
    pub span_deg: f64,
    pub area: *const f64,
    pub n: i32,
}

/// An engine's facts (see `EngineSpec`).
#[repr(C)]
pub struct EsSpec {
    pub bore: f64,
    pub stroke: f64,
    pub rod: f64,
    pub compression: f64,
    pub firing_deg: *const f64,
    pub cylinders: i32,
    pub exhaust: EsValve,
    pub intake: EsValve,
    pub wall_temp: f64,
    pub heat_scale: f64,
    pub gamma_const: f64,
    pub wall_refresh_deg: f64,
}

/// One cylinder's state, for instruments and tests.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct EsCylinder {
    pub p: f64,
    pub t: f64,
    pub m: f64,
    pub m_burned: f64,
    pub v: f64,
    pub q_exhaust: f64,
    pub q_intake: f64,
    pub work: f64,
    pub burn: f64,
    pub wall: f64,
    pub m_exhaust: f64,
    pub m_intake: f64,
}

pub struct EsEngine {
    inbox: Arc<Inbox<Control>>,
    engine: Engine,
}

/// # Safety
/// `p` must point to `n` readable f64 (or n == 0).
unsafe fn slice<'a>(p: *const f64, n: i32) -> Option<&'a [f64]> {
    if n < 0 || (p.is_null() && n != 0) {
        return None;
    }
    if n == 0 {
        return Some(&[]);
    }
    // SAFETY: the caller's contract.
    Some(unsafe { core::slice::from_raw_parts(p, n as usize) })
}

/// # Safety
/// As `slice`, for the valve's table.
unsafe fn valve(v: &EsValve) -> Option<ValveCurve> {
    // SAFETY: the caller's contract.
    let a = unsafe { slice(v.area, v.n) }?;
    Some(ValveCurve { open_deg: v.open_deg, span_deg: v.span_deg, area: a.to_vec() })
}

#[no_mangle]
pub extern "C" fn es_abi_version() -> u32 {
    ABI_VERSION
}

/// sizeof(es_control_t) as this library was built -- a binding checks its own.
#[no_mangle]
pub extern "C" fn es_sizeof_control() -> u32 {
    core::mem::size_of::<Control>() as u32
}

/// Make an engine.  Returns null if the spec is refused (see `es_last_error`
/// is not provided: a refused spec is a bake bug; the Rust tests name it).
///
/// # Safety
/// `spec` must point to a valid `EsSpec` whose pointers are readable for
/// their counts.  The spec is copied; nothing is kept.
#[no_mangle]
pub unsafe extern "C" fn es_open(spec: *const EsSpec, sample_rate: f64) -> *mut EsEngine {
    if spec.is_null() {
        return core::ptr::null_mut();
    }
    // SAFETY: non-null, and valid by the caller's contract.
    let s = unsafe { &*spec };
    // SAFETY: the caller's contract on the pointers inside.
    let (firing, exhaust, intake) = unsafe {
        match (slice(s.firing_deg, s.cylinders), valve(&s.exhaust), valve(&s.intake)) {
            (Some(f), Some(e), Some(i)) => (f.to_vec(), e, i),
            _ => return core::ptr::null_mut(),
        }
    };
    let spec = EngineSpec {
        bore: s.bore,
        stroke: s.stroke,
        rod: s.rod,
        compression: s.compression,
        firing_deg: firing,
        exhaust,
        intake,
        wall_temp: s.wall_temp,
        heat_scale: s.heat_scale,
        gamma_const: s.gamma_const,
        wall_refresh_deg: s.wall_refresh_deg,
    };
    match Engine::new(&spec, sample_rate) {
        Ok(engine) => Box::into_raw(Box::new(EsEngine { inbox: engine.inbox(), engine })),
        Err(_) => core::ptr::null_mut(),
    }
}

/// # Safety
/// `e` must come from `es_open` and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn es_close(e: *mut EsEngine) {
    if !e.is_null() {
        // SAFETY: made by Box::into_raw in es_open, closed once.
        drop(unsafe { Box::from_raw(e) });
    }
}

/// Publish an operating point.  Any thread; never waits.  Returns 1 if it
/// was published, 0 if another thread was publishing at that very moment.
///
/// # Safety
/// `e` from `es_open`, `c` a valid `es_control_t`.
#[no_mangle]
pub unsafe extern "C" fn es_control(e: *const EsEngine, c: *const Control) -> i32 {
    if e.is_null() || c.is_null() {
        return 0;
    }
    // SAFETY: only the `inbox` field is touched, through a shared reference;
    // the audio thread's `&mut` is to the `engine` field alone.
    let inbox = unsafe { &(*e).inbox };
    // SAFETY: valid by the caller's contract.
    inbox.publish(unsafe { *c }) as i32
}

/// Render `n` frames.  The audio thread.  No allocation, no lock, no system call.
///
/// # Safety
/// `e` from `es_open`; `left` and `right` writable for `n` floats.
#[no_mangle]
pub unsafe extern "C" fn es_render(e: *mut EsEngine, left: *mut f32, right: *mut f32, n: i32) {
    if e.is_null() || left.is_null() || right.is_null() || n <= 0 {
        return;
    }
    // SAFETY: the `engine` field only (see es_control); buffers by contract.
    unsafe {
        let engine = &mut (*e).engine;
        let l = core::slice::from_raw_parts_mut(left, n as usize);
        let r = core::slice::from_raw_parts_mut(right, n as usize);
        engine.render(l, r);
    }
}

/// Jump to an operating point with no ramp, and run `cycles` engine cycles
/// silently so the cylinders hold a running engine's gas.  Not real-time.
///
/// # Safety
/// `e` from `es_open`, `c` valid; no render running.
#[no_mangle]
pub unsafe extern "C" fn es_prime(e: *mut EsEngine, c: *const Control, cycles: f64) {
    if e.is_null() || c.is_null() {
        return;
    }
    // SAFETY: by contract.
    unsafe {
        let engine = &mut (*e).engine;
        engine.force_control(&*c);
        engine.prime(cycles);
    }
}

/// The instrument tap: advance `n` samples exactly as `es_render` does and
/// write what the valves passed (kg/s, all cylinders) and cylinder `cyl`'s
/// pressure (Pa), temperature (K) and mass (kg).  Any output may be null.
///
/// # Safety
/// `e` from `es_open`; each non-null output writable for `n` doubles.
#[no_mangle]
pub unsafe extern "C" fn es_run(
    e: *mut EsEngine,
    n: i32,
    cyl: i32,
    q_exhaust: *mut f64,
    q_intake: *mut f64,
    p: *mut f64,
    t: *mut f64,
    m: *mut f64,
) {
    if e.is_null() || n <= 0 {
        return;
    }
    // SAFETY: by contract.
    let engine = unsafe { &mut (*e).engine };
    let k = (cyl.max(0) as usize).min(engine.cylinders().len() - 1);
    for i in 0..n as usize {
        let s = engine.tick();
        let c = &engine.cylinders()[k];
        // SAFETY: each non-null output is writable for n doubles.
        unsafe {
            if !q_exhaust.is_null() {
                *q_exhaust.add(i) = s.q_exhaust;
            }
            if !q_intake.is_null() {
                *q_intake.add(i) = s.q_intake;
            }
            if !p.is_null() {
                *p.add(i) = c.p;
            }
            if !t.is_null() {
                *t.add(i) = c.t;
            }
            if !m.is_null() {
                *m.add(i) = c.m;
            }
        }
    }
}

/// One cylinder's state and running totals.
///
/// # Safety
/// `e` from `es_open`, `out` writable; no render running.
#[no_mangle]
pub unsafe extern "C" fn es_cylinder(e: *const EsEngine, cyl: i32, out: *mut EsCylinder) -> i32 {
    if e.is_null() || out.is_null() || cyl < 0 {
        return 0;
    }
    // SAFETY: by contract.
    let engine = unsafe { &(*e).engine };
    let Some(c) = engine.cylinders().get(cyl as usize) else {
        return 0;
    };
    let v = EsCylinder {
        p: c.p,
        t: c.t,
        m: c.m,
        m_burned: c.m_burned,
        v: c.v,
        q_exhaust: c.q_exhaust,
        q_intake: c.q_intake,
        work: c.ledger.work,
        burn: c.ledger.burn,
        wall: c.ledger.wall,
        m_exhaust: c.ledger.m_exhaust,
        m_intake: c.ledger.m_intake,
    };
    // SAFETY: by contract.
    unsafe { *out = v };
    1
}

/// Clear every cylinder's running totals.
///
/// # Safety
/// `e` from `es_open`; no render running.
#[no_mangle]
pub unsafe extern "C" fn es_clear_ledgers(e: *mut EsEngine) {
    if e.is_null() {
        return;
    }
    // SAFETY: by contract.
    let engine = unsafe { &mut (*e).engine };
    for k in 0..engine.cylinders().len() {
        engine.cylinder_mut(k).ledger = Default::default();
    }
}

/// The crank angle in the engine's 720 degree cycle.
///
/// # Safety
/// `e` from `es_open`.
#[no_mangle]
pub unsafe extern "C" fn es_crank_deg(e: *const EsEngine) -> f64 {
    if e.is_null() {
        return 0.0;
    }
    // SAFETY: by contract.
    unsafe { (*e).engine.crank_deg() }
}

/// What the ports present to the valves (Pa s/kg; 0 = a plenum).  The seam
/// the exhaust network attaches to; exposed for the tests of it.
///
/// # Safety
/// `e` from `es_open`; no render running.
#[no_mangle]
pub unsafe extern "C" fn es_set_port_impedance(e: *mut EsEngine, exhaust_z: f64, intake_z: f64) {
    if e.is_null() {
        return;
    }
    // SAFETY: by contract.
    unsafe { (*e).engine.set_port_impedance(exhaust_z, intake_z) };
}

/// A pipe of an exhaust network (see `PipeSpec`).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsPipe {
    pub length: f64,
    pub area: f64,
    pub heat: f64,
    pub loss_db: f64,
    pub a: u32,
    pub b: u32,
}

/// A node of an exhaust network (see `NodeSpec`).  `kind`: 0 junction,
/// 1 valve (`index` = cylinder), 2 orifice (`index` = control channel, or
/// -1; `r`, `k`), 3 open end (`radius`).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsNode {
    pub kind: i32,
    pub index: i32,
    pub r: f64,
    pub k: f64,
    pub radius: f64,
    /// orifice: the passage whose speed sets the band of its flow noise, m^2
    pub throat: f64,
    /// orifice, open end: rms fluctuation of the turbulent loss, as a fraction
    pub noise: f64,
    /// open end: its distance from the listener, m (<= 0: 1 m)
    pub distance: f64,
}

/// Give the exhaust valves a network to discharge into.  Returns 1, or 0 if
/// the network is refused (the engine is then unchanged).
///
/// # Safety
/// `e` from `es_open`; the arrays readable for their counts; no render running.
#[no_mangle]
pub unsafe extern "C" fn es_attach_exhaust(
    e: *mut EsEngine,
    pipes: *const EsPipe,
    n_pipes: i32,
    nodes: *const EsNode,
    n_nodes: i32,
    t_ambient: f64,
    valve_noise: f64,
) -> i32 {
    if e.is_null() || pipes.is_null() || nodes.is_null() || n_pipes <= 0 || n_nodes <= 0 {
        return 0;
    }
    // SAFETY: by contract.
    let (pipes, nodes) = unsafe {
        (
            core::slice::from_raw_parts(pipes, n_pipes as usize),
            core::slice::from_raw_parts(nodes, n_nodes as usize),
        )
    };
    let mut spec = NetworkSpec { pipes: Vec::new(), nodes: Vec::new(), t_ambient, valve_noise };
    for p in pipes {
        spec.pipes.push(PipeSpec {
            length: p.length,
            area: p.area,
            heat: p.heat,
            loss_db: p.loss_db,
            a: p.a,
            b: p.b,
        });
    }
    for n in nodes {
        spec.nodes.push(match n.kind {
            0 => NodeSpec::Junction,
            1 if n.index >= 0 => NodeSpec::Valve { cylinder: n.index as u32 },
            2 => NodeSpec::Orifice {
                r: n.r,
                k: n.k,
                channel: n.index.max(-1),
                throat: n.throat,
                noise: n.noise,
            },
            3 => NodeSpec::Open { radius: n.radius, noise: n.noise, distance: n.distance },
            _ => return 0,
        });
    }
    // SAFETY: by contract.
    match unsafe { (*e).engine.attach_exhaust(&spec) } {
        Ok(()) => 1,
        Err(_) => 0,
    }
}

/// Give the intake valves a network to draw from (as `es_attach_exhaust`).
///
/// # Safety
/// As `es_attach_exhaust`.
#[no_mangle]
pub unsafe extern "C" fn es_attach_intake(
    e: *mut EsEngine,
    pipes: *const EsPipe,
    n_pipes: i32,
    nodes: *const EsNode,
    n_nodes: i32,
    t_ambient: f64,
    valve_noise: f64,
) -> i32 {
    // SAFETY: by contract.
    let Some(spec) = (unsafe { network_spec(pipes, n_pipes, nodes, n_nodes, t_ambient, valve_noise) })
    else {
        return 0;
    };
    if e.is_null() {
        return 0;
    }
    // SAFETY: by contract.
    match unsafe { (*e).engine.attach_intake(&spec) } {
        Ok(()) => 1,
        Err(_) => 0,
    }
}

/// # Safety
/// The arrays readable for their counts.
unsafe fn network_spec(
    pipes: *const EsPipe,
    n_pipes: i32,
    nodes: *const EsNode,
    n_nodes: i32,
    t_ambient: f64,
    valve_noise: f64,
) -> Option<NetworkSpec> {
    if pipes.is_null() || nodes.is_null() || n_pipes <= 0 || n_nodes <= 0 {
        return None;
    }
    // SAFETY: by contract.
    let (pipes, nodes) = unsafe {
        (
            core::slice::from_raw_parts(pipes, n_pipes as usize),
            core::slice::from_raw_parts(nodes, n_nodes as usize),
        )
    };
    let mut spec = NetworkSpec { pipes: Vec::new(), nodes: Vec::new(), t_ambient, valve_noise };
    for p in pipes {
        spec.pipes.push(PipeSpec {
            length: p.length,
            area: p.area,
            heat: p.heat,
            loss_db: p.loss_db,
            a: p.a,
            b: p.b,
        });
    }
    for n in nodes {
        spec.nodes.push(match n.kind {
            0 => NodeSpec::Junction,
            1 if n.index >= 0 => NodeSpec::Valve { cylinder: n.index as u32 },
            2 => NodeSpec::Orifice {
                r: n.r,
                k: n.k,
                channel: n.index.max(-1),
                throat: n.throat,
                noise: n.noise,
            },
            3 => NodeSpec::Open { radius: n.radius, noise: n.noise, distance: n.distance },
            _ => return None,
        });
    }
    Some(spec)
}

/// The instrument tap on the exhaust: advance `n` samples exactly as
/// `es_render` does and write the flow through the exhaust valves and out
/// of the open ends (kg/s), the sound pressure 1 m from the open ends (Pa)
/// and the pressure at the upstream end of pipe `pipe` (Pa about the
/// outside).  Any output may be null.
///
/// # Safety
/// `e` from `es_open`; each non-null output writable for `n` doubles.
#[no_mangle]
pub unsafe extern "C" fn es_listen(
    e: *mut EsEngine,
    n: i32,
    pipe: i32,
    q_exhaust: *mut f64,
    q_tail: *mut f64,
    p_far: *mut f64,
    p_pipe: *mut f64,
) {
    if e.is_null() || n <= 0 {
        return;
    }
    // SAFETY: by contract.
    let engine = unsafe { &mut (*e).engine };
    for i in 0..n as usize {
        let s = engine.tick();
        // SAFETY: each non-null output is writable for n doubles.
        unsafe {
            if !q_exhaust.is_null() {
                *q_exhaust.add(i) = s.q_exhaust;
            }
            if !q_tail.is_null() {
                *q_tail.add(i) = s.q_tail;
            }
            if !p_far.is_null() {
                *p_far.add(i) = s.p_far;
            }
            if !p_pipe.is_null() {
                *p_pipe.add(i) = match engine.exhaust() {
                    Some(net) if pipe >= 0 && (pipe as usize) < net.pipes() => {
                        net.pipe_state(pipe as usize).2
                    }
                    _ => 0.0,
                };
            }
        }
    }
}

/// The level the loudness curve reads now: Pa rms, 1 m from the open ends.
///
/// # Safety
/// `e` from `es_open`.
#[no_mangle]
pub unsafe extern "C" fn es_level(e: *const EsEngine) -> f64 {
    if e.is_null() {
        return 0.0;
    }
    // SAFETY: by contract.
    unsafe { (*e).engine.level() }
}
