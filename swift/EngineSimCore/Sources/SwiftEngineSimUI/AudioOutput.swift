//
//  AudioOutput.swift
//  Getting the chain out of the phone.
//
//  The rule that shapes everything here: NOTHING expensive runs in a real-time
//  audio callback.  The v1 Python app broke up on the phone because a
//  general-purpose runtime was being asked to meet a hard deadline; doing the
//  same thing in Swift would be the same mistake in a faster language.
//
//  So the synth renders on its OWN thread into scheduled buffers, and the
//  audio engine pulls from a queue that is already full.  If a block is late
//  the queue drains and you get silence rather than a click, and `underruns`
//  says so out loud instead of leaving it to be argued about.
//
//  The RENDER RATE is decoupled from the hardware rate on purpose.  iOS locks
//  the session to 48 kHz whatever you request -- `setPreferredSampleRate` is a
//  request, not an instruction -- and rendering the whole chain at 48 kHz
//  costs 50 % more than at 32 kHz for nothing audible.  So the chain runs at
//  its own rate and is resampled, phase-continuously, on the way out.
//

import AVFoundation
import EngineSimCore

public final class AudioOutput {
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    let format: AVAudioFormat

    /// How many buffers to keep in flight.  Deep enough to ride out a slow
    /// block, shallow enough that the note is not late.
    let queueDepth = 6
    let semaphore: DispatchSemaphore

    public private(set) var underruns = 0
    public private(set) var renderLoad = 0.0     // fraction of real time used
    public let renderRate: Double
    public let hardwareRate: Double
    public let blockFrames: Int

    var thread: Thread?
    var running = false
    var resamplePos = 0.0
    var resampleTail: Float = 0

    /// `render` is called on the render thread and must fill `count` frames.
    public var render: ((Int) -> [Float])?

    public init(renderRate: Double = 32000, blockFrames: Int = 512) throws {
        self.renderRate = renderRate
        self.blockFrames = blockFrames
        semaphore = DispatchSemaphore(value: queueDepth)

        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        // .playback so it keeps going with the screen off and routes to the
        // car; the mode is what CarPlay and Bluetooth hand to the head unit.
        try session.setCategory(.playback, mode: .default,
                                options: [.allowBluetoothA2DP, .allowAirPlay])
        try session.setPreferredIOBufferDuration(Double(blockFrames) / renderRate)
        try session.setActive(true)
        hardwareRate = session.sampleRate      // what it ACTUALLY gave us
        #else
        hardwareRate = engine.outputNode.outputFormat(forBus: 0).sampleRate
        #endif

        guard let f = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                    sampleRate: hardwareRate, channels: 1,
                                    interleaved: false) else {
            throw NSError(domain: "AudioOutput", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "could not make an output format"])
        }
        format = f
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    public func start() throws {
        guard !running else { return }
        try engine.start()
        player.play()
        running = true
        let t = Thread { [weak self] in self?.renderLoop() }
        t.name = "enginesim.render"
        t.qualityOfService = .userInteractive
        thread = t
        t.start()
    }

    public func stop() {
        running = false
        thread = nil
        player.stop()
        engine.stop()
    }

    // ------------------------------------------------------------ the loop
    private func renderLoop() {
        while running {
            // block until the queue has room: this is the backpressure, and it
            // is what keeps the render thread from running away
            semaphore.wait()
            guard running, let render else { break }

            let t0 = CFAbsoluteTimeGetCurrent()
            let mono = render(blockFrames)
            let out = resample(mono)
            let elapsed = CFAbsoluteTimeGetCurrent() - t0
            let realTime = Double(blockFrames) / renderRate
            renderLoad += (elapsed / realTime - renderLoad) * 0.05
            if elapsed > realTime { underruns += 1 }

            guard let buf = AVAudioPCMBuffer(pcmFormat: format,
                                             frameCapacity: AVAudioFrameCount(out.count))
            else { semaphore.signal(); continue }
            buf.frameLength = AVAudioFrameCount(out.count)
            out.withUnsafeBufferPointer { src in
                buf.floatChannelData![0].update(from: src.baseAddress!,
                                                count: out.count)
            }
            player.scheduleBuffer(buf) { [weak self] in self?.semaphore.signal() }
        }
    }

    /// Linear resample from the render rate to the hardware rate, carrying the
    /// fractional position and the last sample across blocks so the seam is
    /// continuous.  Dropping either of those gives a click at every block
    /// boundary -- 62 times a second, which reads as a buzz, not as clicks.
    private func resample(_ x: [Float]) -> [Float] {
        guard hardwareRate != renderRate, !x.isEmpty else { return x }
        let step = renderRate / hardwareRate
        var out = [Float]()
        out.reserveCapacity(Int(Double(x.count) / step) + 2)
        var p = resamplePos
        while p < Double(x.count) {
            let i = Int(p)
            let f = Float(p - Double(i))
            let a = i == 0 ? resampleTail : x[i - 1]
            let b = x[i]
            out.append(a + (b - a) * f)
            p += step
        }
        resamplePos = p - Double(x.count)
        resampleTail = x[x.count - 1]
        return out
    }
}
