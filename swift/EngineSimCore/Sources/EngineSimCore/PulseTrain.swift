//
//  PulseTrain.swift
//  The source of the engine note: one blowdown pulse per exhaust valve
//  opening, stamped at each cylinder's firing phase.
//
//  Everything downstream -- the pipe, the cat, the muffler, the reverbs, the
//  listener -- only ever shapes THIS.  So it is ported first, and held to the
//  Python sample for sample.
//
//  Two shapes are summed per firing, which is what makes the note sound like
//  combustion rather than a click track:
//
//    blowdown  a hard edge (the valve cracking against several bar) decaying
//              fast -- the crack.
//    displacement  a soft cosine onset, the piston pushing the rest of the
//              charge out -- the body.
//
//  Per-cylinder scatter (decay, level, edge) comes from the constants exported
//  with the engine; the cycle-to-cycle jitter is fresh every block and uses the
//  PortableRNG so both implementations draw the same numbers.
//

import Foundation

public let valveOpen = 505.0
public let valveClose = 715.0

/// Everything about one engine's pulse train that never changes while it runs.
public struct VoicingSetup: Decodable {
    public let offsets: [Double]         // firing phase per cylinder, deg
    public let cyl_tau: [Double]         // per-cylinder decay scatter, -1..1
    public let cyl_amp: [Double]         // per-cylinder level scatter, -1..1
    public let header_offset: [Double]   // bank phase offset, deg
    public let runner_len: [Double]      // m, sets the per-cylinder delay
    public let channel_of: [Int]         // which exhaust channel each feeds
    public let nchan: Int
    public let bd_sharp: Double
    public let stroke_ref: Double
    public let params: [String: Double]
    public let voice_amp: [Double]?
    public let voice_edge: [Double]?
    public let damp_b: [[Double]]?
    public let damp_a: [[Double]]?

    public static func load(jsonData: Data) throws -> [String: VoicingSetup] {
        try JSONDecoder().decode([String: VoicingSetup].self, from: jsonData)
    }
}

/// Fractional delay line: the runner length each cylinder's pulse travels.
struct RunnerDelay {
    private var buf: [Double]
    private var wp: Int = 0

    init(maxDelay: Int) {
        buf = [Double](repeating: 0, count: max(maxDelay, 4) + 4)
    }

    mutating func process(_ x: [Double], delay: Double) -> [Double] {
        let n = x.count
        let N = buf.count
        let d = min(max(delay, 0.0), Double(N - 3))
        var out = [Double](repeating: 0, count: n)
        for i in 0..<n {
            buf[(wp + i) % N] = x[i]
        }
        for i in 0..<n {
            let idx = Double(wp + i) - d
            let i0 = Int(floor(idx))
            let fr = idx - Double(i0)
            let a = buf[((i0 % N) + N) % N]
            let b = buf[(((i0 + 1) % N) + N) % N]
            out[i] = a * (1.0 - fr) + b * fr
        }
        wp = (wp + n) % N
        return out
    }
}

public final class PulseTrain {
    let setup: VoicingSetup
    let sampleRate: Double

    // per-block state, carried exactly as the Python carries it
    var jit: [Double]
    var tjit: [Double]
    var runners: [RunnerDelay]
    var damp: [OnePole]
    var audioCrank: Double = 0.0
    var rub: Double = 0.0

    public init(setup: VoicingSetup, sampleRate: Double) {
        self.setup = setup
        self.sampleRate = sampleRate
        let n = setup.offsets.count
        jit = [Double](repeating: 1.0, count: n)
        tjit = [Double](repeating: 0.0, count: n)
        let maxDelay = Int((setup.runner_len.max() ?? 1.0) / 300.0 * sampleRate) + 512
        runners = (0..<n).map { _ in RunnerDelay(maxDelay: maxDelay) }
        if let b = setup.damp_b, let a = setup.damp_a {
            damp = (0..<n).map { OnePole(b: b[$0], a: a[$0]) }
        } else {
            damp = []
        }
    }

    /// One block of the raw per-channel pulse train.
    ///
    /// `strength`, `load` and `soundSpeed` come from the physics; `rng` is the
    /// shared PortableRNG, drawn from in the same order as the Python so the
    /// jitter sequences agree.
    /// `cylScale` is the per-cylinder amplitude factor the physics carries
    /// from each cylinder's LAST blowdown -- a cylinder that just fired weakly
    /// sounds weaker.  Passed in rather than recomputed: it belongs to the
    /// physics, not to the pulse shape.
    public func render(frames: Int, rpm: Double, degPerSample: Double,
                       load: Double, soundSpeed: Double, valve: Double,
                       cylScale: [Double], rng: PortableRNG) -> [[Double]] {
        let n = setup.offsets.count
        var chans = (0..<setup.nchan).map { _ in
            [Double](repeating: 0, count: frames)
        }
        guard degPerSample > 1e-12 else { return chans }

        // crank torsional flutter: a slow global phase wander that grows with
        // revs -- spectrum-invariant on its own, but it de-regularises the
        // train once the per-firing scatter rides on top
        let rt = rng.normal() * 1.5 * max((min(rpm, 18500.0) - 8500.0) / 9000.0, 0.0)
        let rp = rub
        rub += (rt - rub) * 0.45

        let wall = 1.0 + 1.2 * min(max((rpm - 8500.0) / 9000.0, 0.0), 1.1)
        for i in 0..<n {
            jit[i] += (1.0 + (0.12 * wall) * (rng.uniform() - 0.5) - jit[i])
                * min(0.25 + 0.20 * (wall - 1.0), 0.42)
        }
        for i in 0..<n {
            tjit[i] += ((rng.uniform() - 0.5) * (1.4 * wall) - tjit[i]) * 0.45
        }

        let spread = (setup.params["cyl_spread"] ?? 0.5) * (1.0 + 1.4 * (1.0 - valve))
        let baseTau = (setup.params["pulse_tau"] ?? 22.0) * (setup.stroke_ref / 0.083)
        let attackDeg = setup.params["attack_deg"] ?? 9.0
        let cv = setup.params["cyl_voice"] ?? 1.0
        let useVoice = setup.voice_amp != nil && cv > 1e-3
        let pk = setup.bd_sharp

        for j in 0..<n {
            let tauJ = baseTau * max(1.0 + 0.95 * spread * setup.cyl_tau[j], 0.35)
            var ampJ = jit[j] * max(1.0 + 0.55 * spread * setup.cyl_amp[j], 0.1)
            var edgeJ = 1.0
            if useVoice {
                ampJ *= 1.0 + (setup.voice_amp![j] - 1.0) * cv
                edgeJ = 1.0 + (setup.voice_edge![j] - 1.0) * cv
            }
            if j < cylScale.count { ampJ *= cylScale[j] }

            let riseDeg = max((2.0 + 4.0 * (1.0 - load)) * degPerSample * edgeJ, 1e-4)
            let tauBlow = max(0.30 * tauJ / pow(pk, 0.85), 2.5)
            let tauDisp = tauJ * 1.5
            let phase0 = audioCrank + setup.offsets[j] + setup.header_offset[j]
                + tjit[j]

            var pulse = [Double](repeating: 0, count: frames)
            for i in 0..<frames {
                var phi = (phase0 + degPerSample * Double(i)
                           + (rp + (rub - rp) * Double(i) / Double(max(frames - 1, 1))))
                    .truncatingRemainder(dividingBy: 720.0)
                if phi < 0 { phi += 720.0 }
                guard phi >= valveOpen && phi <= valveClose else { continue }
                let d = phi - valveOpen
                let dd = max(d, 0.0)
                let hard = min(max(d / riseDeg, 0.0), 1.0)
                let blow = (0.7 + 1.0 * load) * hard * exp(-dd / tauBlow)
                var soft = min(max(d / attackDeg, 0.0), 1.0)
                soft = 0.5 - 0.5 * cos(soft * Double.pi)
                let disp = soft * (1.0 - exp(-dd / (0.5 * tauJ))) * exp(-dd / tauDisp)
                let close = min(max((valveClose - phi) / 18.0, 0.0), 1.0)
                pulse[i] = ((0.78 + 0.22 * pk) * blow
                            + 0.7 * (1.22 - 0.22 * pk) * disp) * close * ampJ
            }
            if useVoice && j < damp.count {
                damp[j].process(&pulse)
            }
            // the Python's _BlockDelay is INTEGER, floored, and never less
            // than one sample -- matching it matters more than being smoother
            let dSamp = Double(max(Int(setup.runner_len[j] / soundSpeed
                                       * sampleRate), 1))
            let delayed = runners[j].process(pulse, delay: dSamp)
            let ch = setup.channel_of[j]
            for i in 0..<frames { chans[ch][i] += delayed[i] }
        }

        audioCrank = (audioCrank + degPerSample * Double(frames))
            .truncatingRemainder(dividingBy: 720.0)
        return chans
    }
}
