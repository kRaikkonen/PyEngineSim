//
//  LayerStack.swift
//  The chain as a stack of layers you can switch off, one per stage.
//
//  Same idea as the eye column in an image editor, and the same rule: hiding a
//  layer passes its input straight through, so what you hear is exactly what
//  that stage contributes.  It is the only honest way to answer "is this stage
//  earning its place?" -- turning a parameter down changes the sound, but only
//  removing the stage tells you what it was doing.
//
//  A hidden stage still RUNS.  That costs a little CPU and it is deliberate:
//  every filter here is stateful, so a stage that stopped running would come
//  back with a dead history and click.  A toggle has to be silent to be useful.
//

import Foundation

/// Every switchable stage, in chain order.  The source taps and the final
/// output are absent: the first are the excitation itself and the last has
/// nothing after it, so neither has an input to fall back to.
public enum Stage: String, CaseIterable, Codable {
    case voiced, block, pipes, header
    case headPort = "head/port"
    case catalytic
    case standingWave = "standing-wave"
    case resonator, muffler
    case valveBypass = "valve bypass"
    case inductionGears = "induction+gears"
    case wallDeHonk = "wall de-honk"
    case metalRing = "metal ring"
    case megaphone, thunder, reflection, radiation
    case tailpipeExit = "tailpipe exit"
    case eq = "EQ"
    case cabinRoom = "cabin/room"
}

/// Which layers are visible, and the bypass that makes hiding one mean
/// something.  Shared by every stage in the chain.
public final class LayerStack {
    private var visible: Set<Stage> = Set(Stage.allCases)
    private var busPrev = [String: [Double]]()
    private var last = [Stage: [Double]]()

    public init() {}

    public func isVisible(_ s: Stage) -> Bool { visible.contains(s) }

    public func set(_ s: Stage, _ on: Bool) {
        if on { visible.insert(s) } else { visible.remove(s) }
    }

    public func toggle(_ s: Stage) { set(s, !isVisible(s)) }

    public var hiddenCount: Int { Stage.allCases.count - visible.count }

    public func showAll() { visible = Set(Stage.allCases) }

    /// Hide everything but one -- or show everything again if it is already
    /// the only one visible.
    public func solo(_ s: Stage) {
        if visible == [s] { showAll() } else { visible = [s] }
    }

    /// Call at each stage boundary with what the stage produced.  Returns that,
    /// or -- if the layer is hidden -- whatever the previous stage on the same
    /// bus produced, which is the stage's own input.
    public func tap(_ s: Stage, _ sig: [Double], bus: String = "exhaust") -> [Double] {
        guard !sig.isEmpty else { return sig }
        var out = sig
        if !visible.contains(s), let prev = busPrev[bus], prev.count == sig.count {
            out = prev
        }
        busPrev[bus] = out
        last[s] = out
        return out
    }

    /// What left a given stage on the last block -- the per-stage tap, used to
    /// compare one stage at a time against the Python.
    public func lastValue(_ s: Stage) -> [Double]? { last[s] }

    /// For an ADDITIVE layer (the bay bus), where hiding means contributing
    /// nothing rather than passing something through.
    public func gate(_ s: Stage, _ sig: [Double]) -> [Double] {
        visible.contains(s) ? sig : [Double](repeating: 0, count: sig.count)
    }
}
