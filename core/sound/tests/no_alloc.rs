//! The render path does not allocate (s.5.2).  Its own test binary, because
//! it replaces the global allocator with one that counts.

mod common;

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicU64, Ordering};

use common::*;

struct Counting;

static ALLOCATIONS: AtomicU64 = AtomicU64::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        // SAFETY: forwarded unchanged.
        unsafe { System.alloc(l) }
    }

    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        // SAFETY: forwarded unchanged.
        unsafe { System.dealloc(p, l) }
    }

    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        // SAFETY: forwarded unchanged.
        unsafe { System.realloc(p, l, n) }
    }
}

#[global_allocator]
static A: Counting = Counting;

#[test]
fn rendering_and_publishing_never_allocate() {
    let mut e = running(&a3(4), 48_000.0, &cruise(1500.0, 0.4 * P_ATM), 3.0);
    let inbox = e.inbox();
    let points = [wot(4000.0, 1.8 * P_ATM), cruise(900.0, 0.3 * P_ATM), wot(6400.0, 1.5 * P_ATM)];
    let mut l = vec![0.0f32; 512];
    let mut r = vec![0.0f32; 512];

    let before = ALLOCATIONS.load(Ordering::Relaxed);
    for block in 0..1500 {
        if block % 50 == 0 {
            inbox.publish(points[(block / 50) % points.len()]);
        }
        let n = [64, 256, 512, 37][block % 4];
        e.render(&mut l[..n], &mut r[..n]);
    }
    let after = ALLOCATIONS.load(Ordering::Relaxed);
    assert_eq!(after - before, 0, "the render path allocated");
    assert!(l.iter().all(|v| v.is_finite()));
}
