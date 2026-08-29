//
//  ReferenceSupport.swift
//  The hooks that let one stage be compared against the Python IN ISOLATION.
//
//  Two things in this chain are history-dependent, so a stage's output is not a
//  pure function of its input:
//
//    the filter cache   its key rounds the cutoff into 8 Hz buckets but the
//                       design uses the exact frequency of whichever call
//                       missed first -- so what the earlier stages asked for
//                       changes what this stage gets back
//    the RNG            every earlier draw moves it, and several stages draw
//
//  Testing a stage on its own therefore means restoring both to what they were
//  at that point in the Python.  Reproducing the sound means reproducing the
//  history, so these are not test scaffolding around a mismatch -- they are how
//  the mismatch is made impossible to hide.
//

import Foundation

extension FilterCache {
    /// Load a snapshot of the Python's `_fcache`, keyed in this class's own
    /// string form.  Used to put the cache into the exact state it had when
    /// the reference block was rendered.
    public func preload(_ entries: [String: [[Double]]]) {
        for (key, ba) in entries where ba.count == 2 {
            insert(key: key, b: ba[0], a: ba[1])
        }
    }
}

extension PortableRNG {
    /// Restore the generator to a captured state (four words plus the carried
    /// Box-Muller spare), so a mid-block draw sequence can be reproduced.
    public func restore(state: [UInt64], spare: Double?) {
        guard state.count == 4 else { return }
        setState(state, spare: spare)
    }
}
